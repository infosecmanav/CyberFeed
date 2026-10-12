import copy
import json
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
import pytest
import requests
from pypdf import PdfReader
from cyberbrief.ai import AIConfig, AIError, request_editorial, generate
from cyberbrief.editorial import validate
from cyberbrief.reports import sections, pdf_report

ROOT=Path(__file__).resolve().parent.parent


def sample():
    payload=json.loads((ROOT/'docs/editorial-preview/evidence.json').read_text())
    output=json.loads((ROOT/'docs/editorial-preview/summary.json').read_text())
    return payload,output


class Response:
    def __init__(self,data,status=200):self.raw=json.dumps(data).encode();self.status_code=status
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def iter_content(self,size):yield self.raw


@pytest.fixture
def configured(monkeypatch):
    for name in ('AI_API_KEY','AI_PROVIDER','AI_MODEL','AI_BASE_URL','AI_JSON_MODE','AI_MAX_OUTPUT_TOKENS'):
        monkeypatch.delenv(name,raising=False)
    monkeypatch.setenv('AI_API_KEY','private-test-key')
    monkeypatch.setenv('AI_PROVIDER','groq')
    monkeypatch.setenv('AI_MODEL','account-model')
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


@pytest.mark.parametrize('status',[302,401,403,429,500])
def test_api_http_failures_are_sanitized_and_not_retried(configured,status):
    post=Mock(return_value=Response({'secret':'private-test-key'},status));configured.setattr('cyberbrief.ai.requests.post',post)
    with pytest.raises(AIError,match=str(status)) as e:request_editorial(sample()[0],AIConfig.from_env())
    assert 'private-test-key' not in str(e.value) and post.call_count==1


def test_connection_failure_never_exposes_request_key(configured):
    configured.setattr('cyberbrief.ai.requests.post',Mock(side_effect=requests.ConnectionError('private-test-key')))
    with pytest.raises(AIError) as e:request_editorial(sample()[0],AIConfig.from_env())
    assert 'private-test-key' not in str(e.value)


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
    text='\n'.join(sections(d));assert text.count('a. AI summary')==10 and text.count('c. References')==10
    reader=PdfReader(BytesIO(pdf_report(d)));text='\n'.join(page.extract_text() for page in reader.pages)
    assert 'Cyber Feed' in text and 'DESIGN PREVIEW' not in text
    bad=copy.deepcopy(o);bad['stories'][0]['bullets'][0]['quote']='invented quote'
    configured.setattr('cyberbrief.ai.request_editorial',lambda *a:bad)
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
    assert 'Detailed AI-powered report' in (tmp_path/'briefing.txt').read_text()
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
