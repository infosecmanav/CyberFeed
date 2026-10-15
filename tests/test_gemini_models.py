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
    gemini_models.main()
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
        gemini_models.main()
    get.assert_not_called()
    monkeypatch.setenv('AI_API_KEY', 'private-test-key')
    get.return_value = Response({'models': []})
    with pytest.raises(SystemExit, match='No Gemini models'):
        gemini_models.main()
    assert capsys.readouterr().out == ''


def test_diagnostic_workflow_is_manual_and_uses_saved_secret():
    path = Path(__file__).resolve().parent.parent / '.github/workflows/check-gemini.yml'
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'workflow_dispatch'}
    assert workflow['permissions'] == {'contents': 'read'}
    step = workflow['jobs']['models']['steps'][-1]
    assert step['run'] == 'python -m cyberbrief.gemini_models'
    assert step['env']['AI_API_KEY'] == '${{ secrets.AI_API_KEY }}'
    assert 'DISCORD_WEBHOOK_URL' not in path.read_text()
