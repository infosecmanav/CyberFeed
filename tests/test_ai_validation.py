import copy
from io import BytesIO
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from pypdf import PdfReader

from cyberbrief import ai, cli
from cyberbrief.editorial import EditorialValidationError, citation_payload, validate
from cyberbrief.reports import pdf_report


def sample():
    root = Path(__file__).resolve().parent.parent / 'docs/editorial-preview'
    payload = json.loads((root / 'evidence.json').read_text())
    valid = json.loads((root / 'summary.json').read_text())
    rejected = copy.deepcopy(valid)
    rejected['stories'][0]['bullets'][0]['quote'] = 'unsupported quotation'
    return payload, valid, rejected


class Response:
    def __init__(self, output, status=200):
        self.status_code = status
        self.headers = {}
        self.raw = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(output)}}]}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield self.raw


def source_report(payload):
    return {'period': payload['period'], 'generated': payload['generated'],
            'stories': [{} for _ in payload['stories']], 'errors': [], 'demo': False}


def test_validation_failure_identifies_field_without_echoing_output():
    payload, _, rejected = sample()
    rejected['stories'][0]['bullets'][0]['quote'] = 'private-test-key'
    with pytest.raises(EditorialValidationError) as error:
        validate(payload, rejected)
    assert error.value.issue['code'] == 'quotation_mismatch'
    assert error.value.issue['path'] == 'stories[0].bullets[0].quote'
    assert 'private-test-key' not in str(error.value)


@pytest.mark.parametrize('location,code', [('title', 'title_length'), ('details', 'detail_count'),
                                         ('bullet', 'invalid_object'), ('evidence_id', 'unknown_evidence_id'),
                                         ('section', 'detail_section'), ('stories', 'invalid_object')])
def test_malformed_schema_has_safe_specific_failure(location, code):
    payload, output, _ = sample()
    if location == 'title': output['stories'][0]['title'] = 'x' * 141
    elif location == 'details': output['stories'][0]['details'] = []
    elif location == 'bullet': output['stories'][0]['bullets'][0] = 'private-test-key'
    elif location == 'evidence_id': output['stories'][0]['bullets'][0]['evidence_id'] = []
    elif location == 'section': output['stories'][0]['details'][0]['section'] = 'invalid'
    else: output['stories'][0] = None
    with pytest.raises(EditorialValidationError) as error: validate(payload, output)
    assert error.value.issue['code'] == code
    assert 'private-test-key' not in str(error.value)


def test_groq_correction_produces_validated_pdf_without_weakening_evidence(monkeypatch, caplog):
    payload, valid, rejected = sample()
    monkeypatch.setattr(ai, 'pack', lambda *a, **k: payload)
    post = Mock(side_effect=[Response(rejected), Response(valid)])
    monkeypatch.setattr(ai.requests, 'post', post)
    config = ai.AIConfig('groq', 'openai/gpt-oss-120b', 'private-test-key', 'https://api.groq.com/openai/v1')
    result = ai.generate(source_report(payload), config)
    assert post.call_count == 2 and result['ai']['status'] == 'validated'
    correction = post.call_args.kwargs['json']['messages']
    packet = json.loads(correction[1]['content'])
    assert packet['original_evidence'] == citation_payload(payload)
    assert packet['rejected_report'] == rejected
    assert packet['validation_error']['path'] == 'stories[0].bullets[0].quote'
    assert 'Specific correction request' in correction[0]['content']
    assert 'private-test-key' not in json.dumps(correction)
    assert 'unsupported quotation' not in caplog.text
    text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(pdf_report(result))).pages)
    assert text.count('References') == 10 and 'Cyber Feed' in text
    assert 'unsupported quotation' not in text


@pytest.mark.parametrize('repairs', [0, 1])
def test_invalid_output_never_passes_and_corrections_are_bounded(monkeypatch, repairs):
    payload, _, rejected = sample()
    monkeypatch.setattr(ai, 'pack', lambda *a, **k: payload)
    post = Mock(return_value=Response(rejected)); monkeypatch.setattr(ai.requests, 'post', post)
    config = ai.AIConfig('groq', 'model', 'private-test-key', 'https://api.groq.com/openai/v1', repair_attempts=repairs)
    with pytest.raises(ai.AIValidationError) as error: ai.generate(source_report(payload), config)
    assert post.call_count == repairs + 1
    assert error.value.issue['code'] == 'quotation_mismatch'
    assert 'stories[0].bullets[0].quote' in str(error.value)


def test_correction_quota_failure_stops_without_more_requests(monkeypatch):
    payload, _, rejected = sample(); monkeypatch.setattr(ai, 'pack', lambda *a, **k: payload)
    post = Mock(side_effect=[Response(rejected), Response({}, 429)])
    monkeypatch.setattr(ai.requests, 'post', post)
    config = ai.AIConfig('groq', 'model', 'private-test-key', 'https://api.groq.com/openai/v1')
    with pytest.raises(ai.AIValidationError) as error: ai.generate(source_report(payload), config)
    assert post.call_count == 2
    assert 'HTTP 429' in error.value.issue['correction_error']
    assert 'private-test-key' not in str(error.value)


def test_http_failure_does_not_trigger_content_correction(monkeypatch):
    payload, _, _ = sample(); monkeypatch.setattr(ai, 'pack', lambda *a, **k: payload)
    post = Mock(return_value=Response({}, 429)); correction = Mock()
    monkeypatch.setattr(ai.requests, 'post', post); monkeypatch.setattr(ai, 'repair_editorial', correction)
    with pytest.raises(ai.AIHTTPError):
        ai.generate(source_report(payload), ai.AIConfig('groq', 'model', 'private-test-key', 'https://api.groq.com/openai/v1'))
    assert post.call_count == 1
    correction.assert_not_called()


def test_cli_keeps_safe_diagnostics_and_never_creates_rejected_pdf(monkeypatch, tmp_path):
    payload, valid, rejected = sample()
    for name, value in {'AI_API_KEY': 'private-test-key', 'AI_PROVIDER': 'groq', 'AI_MODEL': 'model', 'AI_REPAIR_ATTEMPTS': '1', 'AI_BATCH_SIZE': '10'}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('AI_BASE_URL', raising=False)
    monkeypatch.setattr(cli, 'build', lambda *a, **k: source_report(payload))
    monkeypatch.setattr(ai, 'pack', lambda *a, **k: payload)
    post = Mock(return_value=Response(rejected))
    monkeypatch.setattr(ai.requests, 'post', post)
    monkeypatch.setattr('sys.argv', ['cli', 'build', '--format', 'all', '--out', str(tmp_path)])
    with pytest.raises(SystemExit, match='quotation_mismatch'): cli.main()
    issue = json.loads((tmp_path / 'validation-error.json').read_text())
    assert issue['path'] == 'stories[0].bullets[0].quote'
    assert 'private-test-key' not in json.dumps(issue)
    assert (tmp_path / 'source-report.json').exists()
    assert not (tmp_path / 'briefing.pdf').exists() and not (tmp_path / 'briefing.json').exists()
    post.return_value = Response(valid)
    cli.main()
    assert not (tmp_path / 'validation-error.json').exists()
    assert (tmp_path / 'briefing.pdf').read_bytes().startswith(b'%PDF-')


@pytest.mark.parametrize('repairs', ['-1', '2', 'invalid'])
def test_repair_configuration_is_bounded(monkeypatch, repairs):
    for name, value in {'AI_API_KEY': 'private-test-key', 'AI_PROVIDER': 'groq', 'AI_MODEL': 'model', 'AI_REPAIR_ATTEMPTS': repairs}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('AI_BASE_URL', raising=False)
    with pytest.raises(ai.AIError, match='AI_REPAIR_ATTEMPTS'): ai.AIConfig.from_env()


def test_oversized_correction_stops_before_api_call(monkeypatch):
    call = Mock(); monkeypatch.setattr(ai, 'request_json', call)
    config = ai.AIConfig('groq', 'model', 'private-test-key', 'https://api.groq.com/openai/v1')
    with pytest.raises(ai.AIError, match='1 MB'):
        ai.repair_editorial({'stories': [{'evidence': [{'id': 's1.summary', 'text': 'x' * 1_000_000}]}]}, {}, {'code': 'invalid_structure'}, config)
    call.assert_not_called()
