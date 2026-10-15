import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
import yaml

from cyberbrief.ai import AIError
from cyberbrief import gemini_models


class Response:
    def __init__(self, data, status=200):
        self.raw = json.dumps(data).encode()
        self.status_code = status
        self.headers = {}
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, size):
        yield self.raw


def model(name, methods=None):
    return {'name': name, 'supportedGenerationMethods': methods if methods is not None else ['generateContent']}


def test_catalog_pagination_capability_filter_and_safe_request(monkeypatch):
    pages = [
        Response({'models': [model('models/gemini-b'), model('models/embedding-example', ['embedContent']),
                              model('models/gemini-ignored', ['embedContent']), model('models/gemini-a\n::error::injection')],
                  'nextPageToken': 'next-page'}),
        Response({'models': [model('models/gemini-a'), model('models/gemini-b')]}),
    ]
    get = Mock(side_effect=pages)
    post = Mock()
    monkeypatch.setattr(gemini_models.requests, 'get', get)
    monkeypatch.setattr(gemini_models.requests, 'post', post)
    assert gemini_models.available_models('private-test-key') == ['gemini-a', 'gemini-b']
    first, second = get.call_args_list
    assert first.args == (gemini_models.CATALOG_URL,)
    assert first.kwargs['headers'] == {'x-goog-api-key': 'private-test-key'}
    assert first.kwargs['params'] == {'pageSize': 100}
    assert second.kwargs['params'] == {'pageSize': 100, 'pageToken': 'next-page'}
    assert first.kwargs['allow_redirects'] is False
    assert first.kwargs['timeout'] == (10, 30)
    assert all(page.closed for page in pages)
    post.assert_not_called()


@pytest.mark.parametrize('status', [302, 401, 403, 404, 429, 503])
def test_catalog_http_errors_do_not_expose_bodies_or_credentials(monkeypatch, status):
    get = Mock(return_value=Response({'error': 'private-test-key'}, status))
    monkeypatch.setattr(gemini_models.requests, 'get', get)
    with pytest.raises(AIError, match=f'HTTP {status}') as error:
        gemini_models.available_models('private-test-key')
    assert 'private-test-key' not in str(error.value)
    assert get.call_count == 1


def test_connection_error_is_sanitized(monkeypatch):
    get = Mock(side_effect=requests.ConnectionError('private-test-key'))
    monkeypatch.setattr(gemini_models.requests, 'get', get)
    with pytest.raises(AIError, match='connection failed') as error:
        gemini_models.available_models('private-test-key')
    assert 'private-test-key' not in str(error.value)


@pytest.mark.parametrize('data', [[], {}, {'models': None}, {'models': ['invalid']},
                                  {'models': [model(123)]}, {'models': [model('models/gemini-a', 'generateContent')]},
                                  {'models': [], 'nextPageToken': 123}])
def test_invalid_catalog_is_rejected(monkeypatch, data):
    monkeypatch.setattr(gemini_models.requests, 'get', Mock(return_value=Response(data)))
    with pytest.raises(AIError, match='invalid model listing'):
        gemini_models.available_models('private-test-key')


def test_response_size_and_pagination_are_bounded(monkeypatch):
    get = Mock(return_value=Response({'models': [], 'extra': 'x' * 1_000_000}))
    monkeypatch.setattr(gemini_models.requests, 'get', get)
    with pytest.raises(AIError, match='1 MB'):
        gemini_models.available_models('private-test-key')
    get = Mock(return_value=Response({'models': [], 'nextPageToken': 'repeated'}))
    monkeypatch.setattr(gemini_models.requests, 'get', get)
    with pytest.raises(AIError, match='invalid model listing'):
        gemini_models.available_models('private-test-key')
    assert get.call_count == 2
    get = Mock(side_effect=[Response({'models': [], 'nextPageToken': str(i)}) for i in range(10)])
    monkeypatch.setattr(gemini_models.requests, 'get', get)
    with pytest.raises(AIError, match='ten-page limit'):
        gemini_models.available_models('private-test-key')
    assert get.call_count == 10


def test_key_cannot_be_echoed_as_a_model_id(monkeypatch):
    monkeypatch.setattr(gemini_models.requests, 'get', Mock(return_value=Response({'models': [model('models/gemini-private-test-key')]})))
    with pytest.raises(AIError, match='unsafe model identifier'):
        gemini_models.available_models('private-test-key')


@pytest.mark.parametrize('configured,expected', [('models/gemini-example', 'AI_MODEL is listed'),
                                               ('gemini-unverified', 'AI_MODEL is not listed')])
def test_cli_reports_model_presence_and_writes_safe_summary(monkeypatch, tmp_path, capsys, configured, expected):
    monkeypatch.setenv('AI_API_KEY', 'private-test-key')
    monkeypatch.setenv('AI_MODEL', configured)
    summary = tmp_path / 'summary.md'
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary))
    monkeypatch.setattr(gemini_models.requests, 'get', Mock(return_value=Response({'models': [model('models/gemini-example')]})))
    gemini_models.main([])
    output = capsys.readouterr().out
    assert expected in output and '- gemini-example' in output
    assert 'private-test-key' not in output
    assert summary.read_text() == output
    assert 'does not verify free-tier eligibility' in output


def test_cli_requires_key_and_rejects_empty_catalog(monkeypatch, capsys):
    monkeypatch.delenv('AI_API_KEY', raising=False)
    get = Mock()
    monkeypatch.setattr(gemini_models.requests, 'get', get)
    with pytest.raises(SystemExit, match='secret AI_API_KEY'):
        gemini_models.main([])
    get.assert_not_called()
    monkeypatch.setenv('AI_API_KEY', 'private-test-key')
    get.return_value = Response({'models': []})
    with pytest.raises(SystemExit, match='No Gemini models'):
        gemini_models.main([])
    assert capsys.readouterr().out == ''


def test_diagnostic_workflow_is_manual_and_uses_saved_secret():
    path = Path(__file__).resolve().parent.parent / '.github/workflows/check-gemini.yml'
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'workflow_dispatch'}
    assert workflow['permissions'] == {'contents': 'read'}
    steps = workflow['jobs']['models']['steps']
    step = next(step for step in steps if step.get('name') == 'List Gemini generation models')
    assert step['run'] == 'python -m cyberbrief.gemini_models'
    assert step['env']['AI_API_KEY'] == '${{ secrets.AI_API_KEY }}'
    assert 'DISCORD_WEBHOOK_URL' not in path.read_text()
    assert workflow['on']['workflow_dispatch']['inputs']['test_generation']['default'] == 'false'
    probe = steps[-1]
    assert probe['if'] == 'inputs.test_generation == true'
    assert probe['run'] == 'python -m cyberbrief.gemini_models --test-generation'


def test_generation_probe_makes_one_small_json_call(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('AI_API_KEY', 'private-test-key')
    monkeypatch.setenv('AI_MODEL', 'models/gemini-example')
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(tmp_path / 'summary.md'))
    post = Mock(return_value=Response({'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'thought': True, 'text': 'hidden'}, {'text': '{"ok":true}'}]}}]}))
    get = Mock()
    monkeypatch.setattr(gemini_models.requests, 'post', post)
    monkeypatch.setattr(gemini_models.requests, 'get', get)
    gemini_models.main(['--test-generation'])
    assert post.call_count == 1
    call = post.call_args
    assert call.args == (gemini_models.CATALOG_URL + '/gemini-example:generateContent',)
    assert call.kwargs['headers'] == {'x-goog-api-key': 'private-test-key'}
    assert call.kwargs['json']['generationConfig'] == {'maxOutputTokens': 2048, 'responseMimeType': 'application/json'}
    assert not call.kwargs['allow_redirects']
    assert len(json.dumps(call.kwargs['json'])) < 1000
    get.assert_not_called()
    output = capsys.readouterr().out
    assert 'minimal JSON generation test passed' in output
    assert 'private-test-key' not in output and 'hidden' not in output


def test_generation_probe_uses_same_google_compatibility_fallback_as_reports(monkeypatch):
    monkeypatch.setenv('AI_GEMINI_TRANSPORT','auto')
    post=Mock(side_effect=[Response({},404),Response({'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}]})])
    monkeypatch.setattr(gemini_models.requests,'post',post)
    gemini_models.test_generation('private-test-key','gemini-example')
    assert post.call_count==2
    assert post.call_args.args==('https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',)


@pytest.mark.parametrize('status,code', [(404, 'NOT_FOUND'), (503, 'UNAVAILABLE'), (429, 'RESOURCE_EXHAUSTED')])
def test_generation_probe_failure_reports_only_allowlisted_codes(monkeypatch, status, code):
    monkeypatch.setenv('AI_GEMINI_TRANSPORT', 'native')
    post = Mock(return_value=Response({'error': {'status': code, 'message': 'private-test-key', 'details': [{'reason': 'MODEL_NOT_FOUND', 'metadata': {'key': 'private-test-key'}}]}}, status))
    monkeypatch.setattr(gemini_models.requests, 'post', post)
    sleep = Mock()
    monkeypatch.setattr('cyberbrief.ai.time.sleep', sleep)
    with pytest.raises(AIError, match=f'HTTP {status}') as error:
        gemini_models.test_generation('private-test-key', 'gemini-example')
    assert code in str(error.value) and 'MODEL_NOT_FOUND' in str(error.value)
    assert 'private-test-key' not in str(error.value)
    assert post.call_count == 1
    sleep.assert_not_called()


@pytest.mark.parametrize('key,model_id', [('', 'gemini-example'), ('key', ''), ('key', 'gemini-example?key=private'), ('key', '../other')])
def test_probe_configuration_rejected_before_request(monkeypatch, key, model_id):
    post = Mock()
    monkeypatch.setattr(gemini_models.requests, 'post', post)
    with pytest.raises(AIError):
        gemini_models.test_generation(key, model_id)
    post.assert_not_called()


@pytest.mark.parametrize('result', [
    {'candidates': [{'finishReason': 'MAX_TOKENS', 'content': {'parts': [{'text': 'private-test-key'}]}}]},
    {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': 'private-test-key'}]}}]},
    {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': '{"ok":false}'}]}}]},
])
def test_probe_rejects_incomplete_or_invalid_output_without_exposure(monkeypatch, result):
    post = Mock(return_value=Response(result))
    monkeypatch.setattr(gemini_models.requests, 'post', post)
    with pytest.raises(AIError) as error:
        gemini_models.test_generation('private-test-key', 'gemini-example')
    assert 'private-test-key' not in str(error.value) and post.call_count == 1


def test_exact_model_preflight_normalizes_prefix_and_does_not_generate(monkeypatch):
    get = Mock(return_value=Response(model('models/gemini-example')))
    post = Mock()
    monkeypatch.setattr(gemini_models.requests, 'get', get)
    monkeypatch.setattr(gemini_models.requests, 'post', post)
    gemini_models.check_model('private-test-key', 'models/gemini-example')
    assert get.call_count == 1
    assert get.call_args.args == (gemini_models.CATALOG_URL + '/gemini-example',)
    assert get.call_args.kwargs['headers'] == {'x-goog-api-key': 'private-test-key'}
    assert get.call_args.kwargs['allow_redirects'] is False
    post.assert_not_called()


def test_metadata_404_fails_with_model_id_and_safe_codes(monkeypatch):
    get = Mock(return_value=Response({'error': {'status': 'NOT_FOUND', 'message': 'private-test-key'}}, 404))
    monkeypatch.setattr(gemini_models.requests, 'get', get)
    with pytest.raises(AIError, match='model not found: gemini-example') as error:
        gemini_models.check_model('private-test-key', 'gemini-example')
    assert 'HTTP 404' in str(error.value) and 'NOT_FOUND' in str(error.value)
    assert 'private-test-key' not in str(error.value)
    assert get.call_count == 1


@pytest.mark.parametrize('data', [{}, [], {'name': 'models/other', 'supportedGenerationMethods': ['generateContent']},
                                  {'name': 'models/gemini-example', 'supportedGenerationMethods': 'generateContent'}])
def test_invalid_metadata_does_not_pass_preflight(monkeypatch, data):
    monkeypatch.setattr(gemini_models.requests, 'get', Mock(return_value=Response(data)))
    with pytest.raises(AIError, match='invalid model metadata'):
        gemini_models.check_model('private-test-key', 'gemini-example')


def test_metadata_embedding_only_model_does_not_pass_preflight(monkeypatch):
    monkeypatch.setattr(gemini_models.requests, 'get', Mock(return_value=Response(model('models/gemini-example', ['embedContent']))))
    with pytest.raises(AIError, match='does not advertise generateContent'):
        gemini_models.check_model('private-test-key', 'gemini-example')


def test_metadata_connection_and_size_failures_are_safe(monkeypatch):
    monkeypatch.setattr(gemini_models.requests, 'get', Mock(side_effect=requests.ConnectionError('private-test-key')))
    with pytest.raises(AIError, match='preflight connection failed') as error:
        gemini_models.check_model('private-test-key', 'gemini-example')
    assert 'private-test-key' not in str(error.value)
    monkeypatch.setattr(gemini_models.requests, 'get', Mock(return_value=Response({'extra': 'x' * 1_000_000})))
    with pytest.raises(AIError, match='1 MB limit'):
        gemini_models.check_model('private-test-key', 'gemini-example')
