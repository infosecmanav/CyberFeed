import copy
from io import BytesIO
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from pypdf import PdfReader

from cyberbrief import ai
from cyberbrief.ai_limits import GroqRateState, token_error_metrics
from cyberbrief.reports import pdf_report


def sample():
    root = Path(__file__).resolve().parent.parent / 'docs/editorial-preview'
    return json.loads((root / 'evidence.json').read_text()), json.loads((root / 'summary.json').read_text())


def source(payload):
    return {'period': payload['period'], 'generated': payload['generated'], 'errors': [],
            'stories': [{} for _ in payload['stories']], 'demo': False}


def test_groq_free_tier_defaults_and_explicit_overrides(monkeypatch):
    for name in ('AI_BASE_URL', 'AI_MAX_OUTPUT_TOKENS', 'AI_BATCH_SIZE', 'AI_REPAIR_ATTEMPTS'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('AI_API_KEY', 'private-test-key')
    monkeypatch.setenv('AI_PROVIDER', 'groq')
    monkeypatch.setenv('AI_MODEL', 'openai/gpt-oss-120b')
    config = ai.AIConfig.from_env()
    assert config.batch_size == 1 and config.max_output_tokens == 4096
    monkeypatch.setenv('AI_BATCH_SIZE', '3'); monkeypatch.setenv('AI_MAX_OUTPUT_TOKENS', '6000')
    config = ai.AIConfig.from_env()
    assert config.batch_size == 3 and config.max_output_tokens == 6000
    monkeypatch.delenv('AI_BATCH_SIZE'); monkeypatch.delenv('AI_MAX_OUTPUT_TOKENS')
    monkeypatch.setenv('AI_PROVIDER', 'openai')
    config = ai.AIConfig.from_env()
    assert config.batch_size == 10 and config.max_output_tokens == 16384


@pytest.mark.parametrize('batch_size', ['0', '11', 'invalid'])
def test_invalid_batch_size_fails_before_requests(monkeypatch, batch_size):
    monkeypatch.setenv('AI_API_KEY', 'private-test-key'); monkeypatch.setenv('AI_PROVIDER', 'groq')
    monkeypatch.setenv('AI_MODEL', 'model'); monkeypatch.setenv('AI_BATCH_SIZE', batch_size)
    monkeypatch.delenv('AI_BASE_URL', raising=False)
    with pytest.raises(ai.AIError, match='AI_BATCH_SIZE'): ai.AIConfig.from_env()


@pytest.mark.parametrize('batch_size', [1, 2, 3, 10])
def test_batches_preserve_all_evidence_order_and_complete_pdf(monkeypatch, batch_size):
    payload, output = sample(); batches = []
    by_id = {story['id']: story for story in output['stories']}
    def request(batch, config):
        batches.append(copy.deepcopy(batch))
        return {'stories': [by_id[story['id']] for story in batch['stories']]}
    monkeypatch.setattr(ai, 'pack', lambda *a, **k: payload)
    monkeypatch.setattr(ai, 'request_editorial', request)
    config = ai.AIConfig('groq', 'model', 'private-test-key', 'https://api.groq.com/openai/v1', batch_size=batch_size)
    result = ai.generate(source(payload), config)
    assert [story for batch in batches for story in batch['stories']] == payload['stories']
    assert all(len(batch['stories']) <= batch_size for batch in batches)
    assert result['editorial']['report'] == output
    assert result['editorial']['evidence'] == payload
    text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(pdf_report(result))).pages)
    assert text.count('References') == 10 and 'Cyber Feed' in text


def test_413_splits_without_truncation_or_reordering(monkeypatch):
    payload, output = sample(); by_id = {story['id']: story for story in output['stories']}
    calls = []; accepted = []
    def request(batch, config):
        calls.append(len(batch['stories']))
        if len(batch['stories']) > 2: raise ai.AIHTTPError(413, 'too large')
        accepted.extend(copy.deepcopy(batch['stories']))
        return {'stories': [by_id[story['id']] for story in batch['stories']]}
    monkeypatch.setattr(ai, 'pack', lambda *a, **k: payload); monkeypatch.setattr(ai, 'request_editorial', request)
    result = ai.generate(source(payload), ai.AIConfig('groq', 'model', 'private-test-key', 'https://api.groq.com/openai/v1'))
    assert calls[0] == 10 and len(calls) <= 2 * len(payload['stories']) - 1
    assert accepted == payload['stories'] and result['editorial']['report'] == output


def test_single_update_413_stops_with_no_partial_report(monkeypatch):
    payload, output = sample(); by_id = {story['id']: story for story in output['stories']}
    def request(batch, config):
        if batch['stories'][0]['id'] == 's3': raise ai.AIHTTPError(413, 'Reported token limit: 8000; requested: 9000.')
        return {'stories': [by_id[story['id']] for story in batch['stories']]}
    monkeypatch.setattr(ai, 'pack', lambda *a, **k: payload)
    call = Mock(side_effect=request); monkeypatch.setattr(ai, 'request_editorial', call)
    config = ai.AIConfig('groq', 'model', 'private-test-key', 'https://api.groq.com/openai/v1', batch_size=1)
    with pytest.raises(ai.AIHTTPError, match='Single update s3') as error:
        ai.generate(source(payload), config)
    assert call.call_count == 3 and '8000' in str(error.value)
    assert 'no evidence was discarded' in str(error.value)


def test_validation_diagnostics_identify_batch_story_ids(monkeypatch):
    payload, output = sample(); by_id = {story['id']: story for story in output['stories']}
    def request(batch, config):
        result = {'stories': [copy.deepcopy(by_id[story['id']]) for story in batch['stories']]}
        if batch['stories'][0]['id'] == 's4': result['stories'][0]['details'] = []
        return result
    monkeypatch.setattr(ai, 'pack', lambda *a, **k: payload); monkeypatch.setattr(ai, 'request_editorial', request)
    config = ai.AIConfig('groq', 'model', 'private-test-key', 'https://api.groq.com/openai/v1', repair_attempts=0, batch_size=1)
    with pytest.raises(ai.AIValidationError) as error: ai.generate(source(payload), config)
    assert error.value.issue['story_ids'] == ['s4'] and error.value.issue['code'] == 'detail_count'
    assert 'checks for s4 at stories[0]' in str(error.value)


def test_rate_headers_pace_next_request_without_printing_body(monkeypatch, caplog):
    state = GroqRateState(); sleep = Mock()
    monkeypatch.setattr('cyberbrief.ai_limits.time.monotonic', lambda: 100)
    monkeypatch.setattr('cyberbrief.ai_limits.time.sleep', sleep)
    state.observe({'x-ratelimit-remaining-tokens': '20', 'x-ratelimit-reset-tokens': '12.5s'})
    state.wait({'messages': [{'content': 'private-test-key'}], 'max_tokens': 4096})
    sleep.assert_called_once_with(14)
    assert 'private-test-key' not in caplog.text
    state.wait({'max_tokens': 4096})
    assert sleep.call_count == 1


@pytest.mark.parametrize('headers', [{}, {'x-ratelimit-remaining-tokens': 'invalid', 'x-ratelimit-reset-tokens': '1s'},
                                    {'x-ratelimit-remaining-tokens': '0', 'x-ratelimit-reset-tokens': '2h'},
                                    {'x-ratelimit-remaining-tokens': '100000', 'x-ratelimit-reset-tokens': '1m'}])
def test_absent_invalid_or_sufficient_rate_capacity_does_not_sleep(monkeypatch, headers):
    sleep = Mock(); monkeypatch.setattr('cyberbrief.ai_limits.time.sleep', sleep)
    state = GroqRateState(); state.observe(headers); state.wait({'max_tokens': 4096})
    sleep.assert_not_called()


def test_token_metrics_are_numeric_only_and_error_does_not_leak_organization_or_key():
    message = 'Request too large for model in organization private-org on tokens per minute (TPM): Limit 8,000, Requested 16384. private-test-key'
    assert token_error_metrics(message) == {'limit': 8000, 'requested': 16384, 'scope': 'tokens per minute'}
    class Response:
        def iter_content(self, size):
            yield json.dumps({'error': {'code': 'request_too_large', 'message': message}}).encode()
    error = ai._http_error(413, 1, Response())
    text = str(error)
    assert 'REQUEST_TOO_LARGE' in text and '8000' in text and '16384' in text
    assert 'private-org' not in text and 'private-test-key' not in text


def test_rate_state_is_applied_to_actual_groq_http_calls(monkeypatch):
    payload, output = sample()
    class Response:
        status_code = 200
        headers = {'x-ratelimit-remaining-tokens': '0', 'x-ratelimit-reset-tokens': '1s'}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size):
            yield json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(output)}}]}).encode()
    monkeypatch.setattr(ai.requests, 'post', Mock(return_value=Response()))
    sleep = Mock(); monkeypatch.setattr('cyberbrief.ai_limits.time.sleep', sleep)
    monkeypatch.setattr('cyberbrief.ai_limits.time.monotonic', lambda: 100)
    config = ai.AIConfig('groq', 'model', 'private-test-key', 'https://api.groq.com/openai/v1')
    ai.request_editorial(payload, config); ai.request_editorial(payload, config)
    sleep.assert_called_once_with(2)
