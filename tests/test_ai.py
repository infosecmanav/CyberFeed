import copy
import json
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
import pytest
import requests
from pypdf import PdfReader
from cyberbrief.ai import AIConfig, AIError, request_editorial, generate, preflight
from cyberbrief.editorial import validate
from cyberbrief.reports import sections, pdf_report

ROOT=Path(__file__).resolve().parent.parent


def sample():
    payload=json.loads((ROOT/'docs/editorial-preview/evidence.json').read_text())
    output=json.loads((ROOT/'docs/editorial-preview/summary.json').read_text())
    return payload,output


class Response:
    def __init__(self,data,status=200,headers=None):
        self.raw=json.dumps(data).encode();self.status_code=status
        self.headers=headers or {};self.closed=False
    def __enter__(self):return self
    def __exit__(self,*args):self.closed=True
    def iter_content(self,size):yield self.raw


@pytest.fixture
def configured(monkeypatch):
    for name in ('AI_API_KEY','AI_PROVIDER','AI_MODEL','AI_BASE_URL','AI_JSON_MODE','AI_MAX_OUTPUT_TOKENS','AI_GEMINI_TRANSPORT','AI_REPAIR_ATTEMPTS','AI_BATCH_SIZE'):
        monkeypatch.delenv(name,raising=False)
    monkeypatch.setenv('AI_API_KEY','private-test-key')
    monkeypatch.setenv('AI_PROVIDER','groq')
    monkeypatch.setenv('AI_MODEL','account-model')
    monkeypatch.setenv('AI_BATCH_SIZE','10')
    return monkeypatch


def test_key_provider_model_are_required(configured):
    for missing in ('AI_API_KEY','AI_PROVIDER','AI_MODEL'):
        with configured.context() as m:
            m.delenv(missing)
            with pytest.raises(AIError,match='Configure'):AIConfig.from_env()
    assert 'private-test-key' not in repr(AIConfig.from_env())


@pytest.mark.parametrize('provider,base',[('groq','https://api.groq.com/openai/v1'),('openrouter','https://openrouter.ai/api/v1'),('openai','https://api.openai.com/v1'),('openai-compatible','https://model.example/v1')])
def test_openai_compatible_aliases(configured,provider,base):
    configured.setenv('AI_PROVIDER',provider)
    if provider=='openai-compatible':configured.setenv('AI_BASE_URL',base)
    c=AIConfig.from_env();assert c.base_url==base


@pytest.mark.parametrize('base',['http://model.example/v1','https://name:key@model.example/v1','https://model.example/v1?key=secret','https://model.example:bad/v1',''])
def test_invalid_compatible_endpoint_is_rejected(configured,base):
    configured.setenv('AI_PROVIDER','openai-compatible');configured.setenv('AI_BASE_URL',base)
    with pytest.raises(AIError):AIConfig.from_env()


def test_compatible_api_uses_single_json_request_and_consolidated_skill(configured):
    p,o=sample();config=AIConfig.from_env()
    def post(url,**kwargs):
        assert url==config.base_url+'/chat/completions'
        assert kwargs['headers']['Authorization']=='Bearer private-test-key'
        assert not kwargs['allow_redirects']
        body=kwargs['json'];assert body['response_format']=={'type':'json_object'}
        assert 'EVERY update' in body['messages'][0]['content']
        assert 'private-test-key' not in body['messages'][1]['content']
        return Response({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(o)}}]})
    mocked=Mock(side_effect=post)
    configured.setattr('cyberbrief.ai.requests.post',mocked)
    assert request_editorial(p,config)==o and mocked.call_count==1


def test_gemini_native_headers_schema_and_json_response(configured):
    p,o=sample();configured.setenv('AI_PROVIDER','gemini');configured.setenv('AI_MODEL','models/account-model');c=AIConfig.from_env()
    def post(url,**kwargs):
        assert url=='https://generativelanguage.googleapis.com/v1beta/models/account-model:generateContent'
        assert kwargs['headers']=={'x-goog-api-key':'private-test-key'}
        assert kwargs['json']['generationConfig']['responseMimeType']=='application/json'
        return Response({'candidates':[{'finishReason':'STOP','content':{'parts':[{'thought':True,'text':'internal'},{'text':json.dumps(o)}]}}]})
    configured.setattr('cyberbrief.ai.requests.post',post)
    assert request_editorial(p,c)==o


@pytest.mark.parametrize('status',[302,400,401,403,404,408,429,501])
def test_api_http_failures_are_sanitized_and_not_retried(configured,status):
    post=Mock(return_value=Response({'secret':'private-test-key'},status));configured.setattr('cyberbrief.ai.requests.post',post)
    with pytest.raises(AIError,match=str(status)) as e:request_editorial(sample()[0],AIConfig.from_env())
    assert 'private-test-key' not in str(e.value) and post.call_count==1


def test_provider_error_codes_are_useful_without_printing_error_bodies(configured):
    body={'error':{'status':'NOT_FOUND','message':'private-test-key https://example.test/?key=private-test-key',
                   'details':[{'reason':'MODEL_NOT_FOUND','metadata':{'key':'private-test-key'}},
                              {'reason':'private-test-key'}]}}
    post=Mock(return_value=Response(body,404));configured.setattr('cyberbrief.ai.requests.post',post)
    with pytest.raises(AIError) as error:request_editorial(sample()[0],AIConfig.from_env())
    message=str(error.value)
    assert 'NOT_FOUND' in message and 'MODEL_NOT_FOUND' in message
    assert 'private-test-key' not in message and 'https://' not in message
    assert post.call_count==1


def test_groq_json_generation_failure_is_identified_without_exposing_draft(configured):
    body = {'error': {'code': 'json_validate_failed', 'type': 'invalid_request_error',
                      'message': 'private-test-key and private provider details',
                      'failed_generation': 'untrusted draft with private-test-key'}}
    post = Mock(return_value=Response(body, 400))
    configured.setattr('cyberbrief.ai.requests.post', post)
    with pytest.raises(AIError) as error:
        request_editorial(sample()[0], AIConfig.from_env())
    message = str(error.value)
    assert 'JSON_VALIDATE_FAILED' in message
    assert 'invalid_request_error' in message
    assert 'model failed to produce JSON' in message
    assert 'private' not in message and 'untrusted draft' not in message
    assert 'check model access' not in message and post.call_count == 1


def test_rejected_request_parameter_is_identified(configured):
    post = Mock(return_value=Response({'error': {'code': 'unsupported_parameter',
                                                'param': 'response_format',
                                                'type': 'invalid_request_error'}}, 400))
    configured.setattr('cyberbrief.ai.requests.post', post)
    with pytest.raises(AIError) as error:
        request_editorial(sample()[0], AIConfig.from_env())
    assert 'UNSUPPORTED_PARAMETER' in str(error.value)
    assert 'Rejected parameter: response_format' in str(error.value)
    assert post.call_count == 1


@pytest.mark.parametrize('error', [
    {'code': 'private-test-key', 'type': 'private-test-key', 'param': 'private-test-key'},
    {'code': ['private-test-key'], 'type': {'key': 'private-test-key'}, 'param': ['private-test-key']},
])
def test_unknown_or_malformed_error_metadata_is_not_echoed(configured, error):
    configured.setattr('cyberbrief.ai.requests.post', Mock(return_value=Response({'error': error}, 400)))
    with pytest.raises(AIError) as failure:
        request_editorial(sample()[0], AIConfig.from_env())
    assert 'HTTP 400' in str(failure.value)
    assert 'private-test-key' not in str(failure.value)
    assert 'Provider error type:' not in str(failure.value)
    assert 'Rejected parameter:' not in str(failure.value)


@pytest.mark.parametrize('error',[
    {'status':'private-test-key','message':'private-test-key','details':[{'reason':'private-test-key'}]},
    {'status':{'secret':'private-test-key'}},
    {'status':'NOT_FOUND','message':'private-test-key'+'x'*17000},
])
def test_arbitrary_or_oversized_provider_diagnostics_are_not_printed(configured,error):
    configured.setattr('cyberbrief.ai.requests.post',Mock(return_value=Response({'error':error},404)))
    with pytest.raises(AIError) as failure:request_editorial(sample()[0],AIConfig.from_env())
    assert 'private-test-key' not in str(failure.value)
    assert 'Provider code(s)' not in str(failure.value)


def test_gemini_base_url_is_fixed_even_when_variable_is_present(configured):
    configured.setenv('AI_PROVIDER','gemini')
    configured.setenv('AI_BASE_URL','https://unrelated.example/custom')
    assert AIConfig.from_env().base_url=='https://generativelanguage.googleapis.com/v1beta'


def test_preflight_reports_effective_config_and_checks_gemini_metadata(configured,capsys):
    configured.setenv('AI_PROVIDER','gemini')
    configured.setenv('AI_MODEL','models/gemini-example')
    configured.setenv('AI_BASE_URL','https://unrelated.example/custom')
    check=Mock();configured.setattr('cyberbrief.gemini_models.check_model',check)
    config=AIConfig.from_env()
    preflight(config)
    check.assert_called_once_with('private-test-key','gemini-example')
    text=capsys.readouterr().out
    assert '"provider": "gemini"' in text and '"model": "gemini-example"' in text
    assert 'https://generativelanguage.googleapis.com/v1beta' in text
    assert 'unrelated.example' not in text and 'private-test-key' not in text
    assert 'does not verify generation access' in text


def test_preflight_failure_stops_and_keys_are_redacted(configured,capsys):
    check=Mock(side_effect=AIError('model not found'))
    configured.setattr('cyberbrief.gemini_models.check_model',check)
    config=AIConfig('gemini','private-test-key','private-test-key','https://generativelanguage.googleapis.com/v1beta')
    with pytest.raises(AIError,match='model not found'):preflight(config)
    text=capsys.readouterr().out
    assert 'private-test-key' not in text and '[redacted]' in text
    assert 'metadata check passed' not in text


def test_other_providers_do_not_use_gemini_model_preflight(configured,capsys):
    check=Mock();configured.setattr('cyberbrief.gemini_models.check_model',check)
    preflight(AIConfig.from_env())
    check.assert_not_called()
    assert '"provider": "groq"' in capsys.readouterr().out


@pytest.mark.parametrize('status',[200,404])
def test_actual_workflow_preflight_executes_metadata_check(configured,status):
    import yaml
    workflow=yaml.load((ROOT/'.github/workflows/briefing.yml').read_text(),Loader=yaml.BaseLoader)
    step=next(step for step in workflow['jobs']['deliver']['steps'] if step.get('name')=='Check AI and Discord configuration')
    script=step['run'].splitlines()
    assert script[0]=="python - <<'PY'" and script[-1]=='PY'
    configured.setenv('AI_PROVIDER','gemini')
    configured.setenv('AI_GEMINI_TRANSPORT','native')
    configured.setenv('DISCORD_WEBHOOK_URL','configured-test-webhook')
    body={'name':'models/account-model','supportedGenerationMethods':['generateContent']} if status==200 else {'error':{'status':'NOT_FOUND'}}
    get=Mock(return_value=Response(body,status));post=Mock()
    configured.setattr('cyberbrief.ai.requests.get',get)
    configured.setattr('cyberbrief.ai.requests.post',post)
    code=compile('\n'.join(script[1:-1]),'<workflow-preflight>','exec')
    if status==404:
        with pytest.raises(SystemExit,match='model not found: account-model'):exec(code,{})
    else:
        exec(code,{})
    assert get.call_args.args==('https://generativelanguage.googleapis.com/v1beta/models/account-model',)
    assert get.call_count==1
    post.assert_not_called()


@pytest.mark.parametrize('provider',['gemini','groq'])
@pytest.mark.parametrize('status',[500,502,503,504])
def test_transient_failure_retries_same_request_then_succeeds(configured,provider,status,caplog):
    p,o=sample();configured.setenv('AI_PROVIDER',provider)
    if provider=='gemini':
        success={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(o)}]}}]}
    else:
        success={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(o)}}]}
    responses=[Response({'error':'private-test-key'},status),Response({},status),Response(success)]
    post=Mock(side_effect=responses);waits=[]
    def sleep(delay):
        assert responses[len(waits)].closed
        waits.append(delay)
    configured.setattr('cyberbrief.ai.requests.post',post)
    configured.setattr('cyberbrief.ai.time.sleep',sleep)
    assert request_editorial(p,AIConfig.from_env())==o
    assert waits==[10,20] and post.call_count==3
    assert post.call_args_list[0]==post.call_args_list[1]==post.call_args_list[2]
    assert 'private-test-key' not in caplog.text
    assert f'HTTP {status}' in caplog.text and 'attempt 3/3' in caplog.text


@pytest.mark.parametrize('status',[500,502,503,504])
def test_transient_failures_stop_after_three_attempts(configured,status,caplog):
    post=Mock(side_effect=[Response({'error':'private-test-key'},status) for _ in range(3)])
    sleep=Mock();configured.setattr('cyberbrief.ai.requests.post',post)
    configured.setattr('cyberbrief.ai.time.sleep',sleep)
    with pytest.raises(AIError,match=f'HTTP {status}.*3 attempt') as error:
        request_editorial(sample()[0],AIConfig.from_env())
    assert post.call_count==3 and [call.args[0] for call in sleep.call_args_list]==[10,20]
    assert 'private-test-key' not in str(error.value)+caplog.text


@pytest.mark.parametrize('retry_after,delay',[('30',30),('0',10),('-1',10),('invalid',10),('60',60),('Thu, 01 Jan 1970 00:17:20 GMT',40)])
def test_retry_after_respected_within_bounded_wait(configured,retry_after,delay):
    _,o=sample()
    post=Mock(side_effect=[Response({},503,{'Retry-After':retry_after}),Response({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(o)}}]})])
    sleep=Mock();configured.setattr('cyberbrief.ai.requests.post',post)
    configured.setattr('cyberbrief.ai.time.sleep',sleep)
    configured.setattr('cyberbrief.ai.time.time',lambda:1000)
    assert request_editorial(sample()[0],AIConfig.from_env())==o
    sleep.assert_called_once_with(delay)


@pytest.mark.parametrize('retry_after',['120','Thu, 01 Jan 1970 00:20:00 GMT'])
def test_long_retry_after_stops_without_retrying_early(configured,retry_after):
    post=Mock(return_value=Response({},503,{'Retry-After':retry_after}));sleep=Mock()
    configured.setattr('cyberbrief.ai.requests.post',post)
    configured.setattr('cyberbrief.ai.time.sleep',sleep)
    configured.setattr('cyberbrief.ai.time.time',lambda:1000)
    with pytest.raises(AIError,match='longer than 60 seconds'):
        request_editorial(sample()[0],AIConfig.from_env())
    assert post.call_count==1
    sleep.assert_not_called()


@pytest.mark.parametrize('failure',[Response({},401),Response({},429),Response({'invalid':'response'}),requests.ReadTimeout('private-test-key')])
def test_retry_stops_on_permanent_or_ambiguous_failure(configured,failure):
    post=Mock(side_effect=[Response({},503),failure]);sleep=Mock()
    configured.setattr('cyberbrief.ai.requests.post',post)
    configured.setattr('cyberbrief.ai.time.sleep',sleep)
    with pytest.raises(AIError) as error:request_editorial(sample()[0],AIConfig.from_env())
    assert post.call_count==2 and 'private-test-key' not in str(error.value)
    sleep.assert_called_once_with(10)


def test_connection_failure_never_exposes_request_key(configured):
    post=Mock(side_effect=requests.ConnectionError('private-test-key'))
    configured.setattr('cyberbrief.ai.requests.post',post)
    with pytest.raises(AIError) as e:request_editorial(sample()[0],AIConfig.from_env())
    assert 'private-test-key' not in str(e.value) and post.call_count==1


def test_incomplete_or_non_json_response_is_rejected(configured):
    for finish,text in [('length','{}'),('stop','not JSON')]:
        configured.setattr('cyberbrief.ai.requests.post',Mock(return_value=Response({'choices':[{'finish_reason':finish,'message':{'content':text}}]})))
        with pytest.raises(AIError):request_editorial(sample()[0],AIConfig.from_env())


def test_generation_validates_before_rendering_and_never_falls_back(configured):
    p,o=sample();report={'period':p['period'],'generated':p['generated'],'stories':[{} for _ in p['stories']],'errors':[],'demo':False}
    configured.setattr('cyberbrief.ai.pack',lambda *a,**k:p)
    configured.setattr('cyberbrief.ai.request_editorial',lambda *a:o)
    d=generate(report);assert d['ai']['status']=='validated'
    assert 'private-test-key' not in json.dumps(d)
    text='\n'.join(sections(d));assert text.count('a. Summary')==10 and text.count('c. References')==10
    reader=PdfReader(BytesIO(pdf_report(d)));text='\n'.join(page.extract_text() for page in reader.pages)
    assert 'Cyber Feed' in text and 'DESIGN PREVIEW' not in text
    bad=copy.deepcopy(o);bad['stories'][0]['bullets'][0]['quote']='invented quote'
    configured.setattr('cyberbrief.ai.request_editorial',lambda *a:bad)
    configured.setattr('cyberbrief.ai.repair_editorial',lambda *a:bad)
    with pytest.raises(AIError,match='evidence checks'):generate(report)


def test_empty_window_requires_config_but_avoids_model_call(configured):
    call=Mock();configured.setattr('cyberbrief.ai.request_editorial',call)
    d=generate({'period':'daily','generated':'2026-10-12T00:00:00+00:00','stories':[],'errors':[]})
    assert d['editorial']['report']=={'stories':[]} and d['ai']['status']=='validated'
    call.assert_not_called()


def test_cli_requires_ai_by_default_and_exports_validated_reports(configured,tmp_path):
    from cyberbrief import cli
    p,o=sample()
    source={'period':'weekly','generated':p['generated'],'stories':[{} for _ in p['stories']],'errors':[],'demo':False}
    configured.setattr(cli,'build',lambda *a,**k:source)
    configured.setattr('cyberbrief.ai.pack',lambda *a,**k:p)
    configured.setattr('cyberbrief.ai.request_editorial',lambda *a:o)
    configured.setattr('sys.argv',['cli','build','--format','all','--out',str(tmp_path)])
    cli.main()
    assert json.loads((tmp_path/'briefing.json').read_text())['ai']['status']=='validated'
    assert 'Detailed Reports (powered by AI)' in (tmp_path/'briefing.txt').read_text()
    assert (tmp_path/'briefing.pdf').read_bytes().startswith(b'%PDF-')
    assert 'ai' not in json.loads((tmp_path/'source-report.json').read_text())


def test_cli_missing_key_fails_before_source_collection(configured):
    from cyberbrief import cli
    configured.delenv('AI_API_KEY');build=Mock();configured.setattr(cli,'build',build)
    configured.setattr('sys.argv',['cli','build'])
    with pytest.raises(SystemExit,match='AI_API_KEY'):cli.main()
    build.assert_not_called()


def test_ai_failure_prevents_artifact_export(configured,tmp_path):
    from cyberbrief import cli
    configured.setattr(cli,'build',lambda *a,**k:{'stories':[],'errors':[]})
    configured.setattr('cyberbrief.ai.generate',Mock(side_effect=AIError('Invalid AI output')))
    configured.setattr('sys.argv',['cli','build','--format','all','--out',str(tmp_path)])
    with pytest.raises(SystemExit,match='Invalid AI'):cli.main()
    assert not (tmp_path/'briefing.pdf').exists()
    assert (tmp_path/'source-report.json').exists()


def test_unsupported_version_and_cve_are_rejected():
    p,o=sample()
    for text in ('Upgrade to 99.999.888.', 'Attackers exploited CVE-2099-99999.'):
        bad=copy.deepcopy(o);bad['stories'][0]['details'][0]['text']=text
        with pytest.raises(ValueError,match='CVEs or versions'):validate(p,bad)


def test_raw_model_json_fence_can_be_parsed_without_relaxing_evidence(configured):
    p,o=sample()
    configured.setenv('AI_JSON_MODE','false')
    def post(url,**kwargs):
        assert 'response_format' not in kwargs['json']
        return Response({'choices':[{'finish_reason':'stop','message':{'content':'```json\n'+json.dumps(o)+'\n```'}}]})
    configured.setattr('cyberbrief.ai.requests.post',post)
    assert request_editorial(p,AIConfig.from_env())==o


@pytest.mark.parametrize('transport',['auto','native','openai-compatible'])
def test_gemini_transport_configuration(configured,transport):
    configured.setenv('AI_PROVIDER','gemini')
    configured.setenv('AI_GEMINI_TRANSPORT',transport)
    assert AIConfig.from_env().gemini_transport==transport
    configured.setenv('AI_GEMINI_TRANSPORT','invalid')
    with pytest.raises(AIError,match='AI_GEMINI_TRANSPORT'):AIConfig.from_env()


@pytest.mark.parametrize('status',[404,500,502,503,504])
def test_gemini_auto_fallback_stays_on_google_with_same_model_and_key(configured,status,caplog):
    from urllib.parse import urlparse
    p,o=sample();configured.setenv('AI_PROVIDER','gemini')
    native_attempts=1 if status==404 else 3
    responses=[Response({'error':{'message':'private-test-key'}},status) for _ in range(native_attempts)]
    responses.append(Response({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(o)}}]}))
    post=Mock(side_effect=responses);sleep=Mock()
    configured.setattr('cyberbrief.ai.requests.post',post)
    configured.setattr('cyberbrief.ai.time.sleep',sleep)
    assert request_editorial(p,AIConfig.from_env())==o
    assert post.call_count==native_attempts+1
    for call in post.call_args_list:
        assert urlparse(call.args[0]).hostname=='generativelanguage.googleapis.com'
        assert call.kwargs['allow_redirects'] is False
    native=post.call_args_list[0];compatible=post.call_args_list[-1]
    assert native.args[0].endswith('/v1beta/models/account-model:generateContent')
    assert native.kwargs['headers']=={'x-goog-api-key':'private-test-key'}
    assert compatible.args[0].endswith('/v1beta/openai/chat/completions')
    assert compatible.kwargs['headers']=={'Authorization':'Bearer private-test-key'}
    assert compatible.kwargs['json']['model']=='account-model'
    assert compatible.kwargs['json']['messages'][1]['content']==native.kwargs['json']['contents'][0]['parts'][0]['text']
    assert compatible.kwargs['json']['response_format']=={'type':'json_object'}
    assert 'private-test-key' not in caplog.text
    assert sleep.call_count==max(0,native_attempts-1)


@pytest.mark.parametrize('status',[400,401,403,429])
def test_gemini_auto_does_not_fallback_on_config_auth_or_quota_errors(configured,status):
    configured.setenv('AI_PROVIDER','gemini')
    post=Mock(return_value=Response({},status));configured.setattr('cyberbrief.ai.requests.post',post)
    with pytest.raises(AIError,match=f'HTTP {status}'):request_editorial(sample()[0],AIConfig.from_env())
    assert post.call_count==1


def test_gemini_auto_stops_after_both_google_interfaces_fail(configured):
    configured.setenv('AI_PROVIDER','gemini')
    post=Mock(side_effect=[Response({},404),Response({'error':{'message':'private-test-key'}},503)])
    configured.setattr('cyberbrief.ai.requests.post',post)
    with pytest.raises(AIError,match='both Google interfaces') as error:
        request_editorial(sample()[0],AIConfig.from_env())
    assert 'HTTP 404' in str(error.value) and 'HTTP 503' in str(error.value)
    assert 'private-test-key' not in str(error.value) and post.call_count==2


@pytest.mark.parametrize('retry_after',['30','120'])
def test_gemini_fallback_honors_final_native_retry_after(configured,retry_after):
    configured.setenv('AI_PROVIDER','gemini');_,o=sample()
    responses=[Response({},503),Response({},503),Response({},503,{'Retry-After':retry_after})]
    responses.append(Response({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(o)}}]}))
    post=Mock(side_effect=responses);sleep=Mock()
    configured.setattr('cyberbrief.ai.requests.post',post);configured.setattr('cyberbrief.ai.time.sleep',sleep)
    if retry_after=='120':
        with pytest.raises(AIError,match='longer than 60 seconds'):request_editorial(sample()[0],AIConfig.from_env())
        assert post.call_count==3
        assert [call.args[0] for call in sleep.call_args_list]==[10,20]
    else:
        assert request_editorial(sample()[0],AIConfig.from_env())==o
        assert post.call_count==4
        assert [call.args[0] for call in sleep.call_args_list]==[10,20,40]


@pytest.mark.parametrize('kind',['invalid-json','incomplete','network','long-retry-after'])
def test_gemini_auto_never_fallbacks_after_uncertain_or_invalid_generation(configured,kind):
    configured.setenv('AI_PROVIDER','gemini')
    if kind=='network':
        post=Mock(side_effect=requests.ConnectionError('private-test-key'))
    elif kind=='long-retry-after':
        post=Mock(return_value=Response({},503,{'Retry-After':'120'}))
    else:
        finish='STOP' if kind=='invalid-json' else 'MAX_TOKENS'
        post=Mock(return_value=Response({'candidates':[{'finishReason':finish,'content':{'parts':[{'text':'not JSON'}]}}]}))
    configured.setattr('cyberbrief.ai.requests.post',post)
    with pytest.raises(AIError):request_editorial(sample()[0],AIConfig.from_env())
    assert post.call_count==1


def test_explicit_google_compatible_transport_ignores_custom_host_and_never_calls_native(configured):
    _,o=sample();configured.setenv('AI_PROVIDER','gemini')
    configured.setenv('AI_GEMINI_TRANSPORT','openai-compatible')
    configured.setenv('AI_BASE_URL','https://unrelated.example/v1')
    post=Mock(return_value=Response({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(o)}}]}))
    configured.setattr('cyberbrief.ai.requests.post',post)
    assert request_editorial(sample()[0],AIConfig.from_env())==o
    assert post.call_count==1
    assert post.call_args.args==('https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',)


def test_native_only_mode_never_uses_compatible_fallback(configured):
    configured.setenv('AI_PROVIDER','gemini');configured.setenv('AI_GEMINI_TRANSPORT','native')
    post=Mock(return_value=Response({},404));configured.setattr('cyberbrief.ai.requests.post',post)
    with pytest.raises(AIError,match='HTTP 404'):request_editorial(sample()[0],AIConfig.from_env())
    assert post.call_count==1


def test_openai_uses_its_key_host_and_completion_token_parameter(configured):
    _,o=sample();configured.setenv('AI_PROVIDER','openai')
    configured.setenv('AI_GEMINI_TRANSPORT','invalid-but-irrelevant')
    configured.setenv('AI_BASE_URL','https://generativelanguage.googleapis.com/v1beta')
    post=Mock(return_value=Response({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(o)}}]}))
    configured.setattr('cyberbrief.ai.requests.post',post)
    assert request_editorial(sample()[0],AIConfig.from_env())==o
    assert post.call_args.args==('https://api.openai.com/v1/chat/completions',)
    assert post.call_args.kwargs['json']['max_completion_tokens']==16384
    assert 'max_tokens' not in post.call_args.kwargs['json']


@pytest.mark.parametrize('transport',['auto','native','openai-compatible'])
def test_preflight_uses_transport_policy_for_native_metadata_404(configured,transport,caplog):
    from cyberbrief.ai import AIHTTPError
    configured.setenv('AI_PROVIDER','gemini');configured.setenv('AI_GEMINI_TRANSPORT',transport)
    check=Mock(side_effect=AIHTTPError(404,'metadata not found'))
    configured.setattr('cyberbrief.gemini_models.check_model',check)
    if transport=='native':
        with pytest.raises(AIError,match='metadata not found'):preflight(AIConfig.from_env())
    else:
        preflight(AIConfig.from_env())
    assert check.call_count==(0 if transport=='openai-compatible' else 1)
    if transport=='auto':assert 'metadata returned HTTP 404' in caplog.text


@pytest.mark.parametrize('transport',['native','openai-compatible','auto'])
def test_gemini_transport_generates_validated_detailed_pdf(configured,transport):
    p,o=sample();configured.setenv('AI_PROVIDER','gemini');configured.setenv('AI_GEMINI_TRANSPORT',transport)
    report={'period':p['period'],'generated':p['generated'],'stories':[{} for _ in p['stories']],'errors':[],'demo':False}
    configured.setattr('cyberbrief.ai.pack',lambda *a,**k:p)
    native={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(o)}]}}]}
    compatible={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(o)}}]}
    responses=[Response(native)] if transport=='native' else [Response(compatible)]
    if transport=='auto':responses.insert(0,Response({},404))
    configured.setattr('cyberbrief.ai.requests.post',Mock(side_effect=responses))
    result=generate(report)
    assert result['ai']['provider']=='gemini' and result['ai']['status']=='validated'
    text='\n'.join(page.extract_text() for page in PdfReader(BytesIO(pdf_report(result))).pages)
    assert 'Cyber Feed' in text and text.count('References')==10
    assert 'private-test-key' not in text
