import copy
from io import BytesIO
import json
from pathlib import Path
from unittest.mock import Mock

from pypdf import PdfReader
import pytest

from cyberbrief import ai
from cyberbrief.editorial import (EditorialValidationError, citation_payload,
                                 evidence_passages, validate)
from cyberbrief.reports import pdf_report, sections


def sample():
    root = Path(__file__).resolve().parent.parent / 'docs/editorial-preview'
    payload = json.loads((root / 'evidence.json').read_text())
    output = json.loads((root / 'summary.json').read_text())
    return payload, output


def passage_report(payload, output):
    """Express the historical report's existing exact citations as passage IDs."""
    result = copy.deepcopy(output)
    for source, story in zip(payload['stories'], result['stories']):
        evidence = {entry['id']: entry['text'] for entry in source['evidence']}
        for item in story['bullets'] + story['details']:
            text = evidence[item['evidence_id']]
            quote = item.pop('quote')
            start = text.index(quote)
            end = start + len(quote)
            cursor, selected = 0, []
            for ident, passage in evidence_passages(text).items():
                if cursor < end and cursor + len(passage) > start:
                    selected.append(ident)
                cursor += len(passage)
            item['quote_ids'] = selected
    return result


@pytest.mark.parametrize('text', [
    'First sentence.\n\nSecond sentence — “Unicode” and CVE-2026-12345.\r\n',
    'A version 10.2.3 and a fix. ' * 200,
    'x' * 1801,
    ' ' * 1300 + 'last word',
    '',
])
def test_passage_input_preserves_every_source_character(text):
    assert ''.join(evidence_passages(text).values()) == text


def test_wire_input_retains_all_evidence_once_without_mutating_original():
    payload, _ = sample()
    original = copy.deepcopy(payload)
    packet = citation_payload(payload)
    for source, sent in zip(payload['stories'], packet['stories']):
        for entry, wire_entry in zip(source['evidence'], sent['evidence']):
            assert 'text' not in wire_entry
            assert ''.join(wire_entry['passages'].values()) == entry['text']
            assert wire_entry['id'] == entry['id']
            assert wire_entry['truncated'] == entry['truncated']
        assert sent['references'] == source['references']
    assert payload == original


def test_passage_citations_produce_exact_quotes_and_render_all_ten_updates():
    payload, output = sample()
    draft = passage_report(payload, output)
    original = copy.deepcopy(draft)
    resolved = validate(payload, draft)
    assert draft == original  # Validation does not mutate the untrusted draft.
    assert validate(payload, resolved) == resolved
    for source, story in zip(payload['stories'], resolved['stories']):
        evidence = {entry['id']: entry['text'] for entry in source['evidence']}
        for item in story['bullets'] + story['details']:
            assert item['quote'] in evidence[item['evidence_id']]
    report = {'period': payload['period'], 'generated': payload['generated'], 'errors': [],
              'stories': [{} for _ in payload['stories']],
              'editorial': {'evidence': payload, 'report': resolved}}
    pdf = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(pdf_report(report))).pages)
    text = '\n'.join(sections(report))
    for content in (pdf, text):
        assert content.count('a. Summary') == 10
        assert content.count('b. Detailed Reports (powered by AI)') == 10
        assert content.count('c. References') == 10


@pytest.mark.parametrize('selection,code', [
    (['p99999'], 'unknown_passage_id'),
    ([], 'unknown_passage_id'),
    ('p1', 'unknown_passage_id'),
    ([{}], 'unknown_passage_id'),
    (['p1', 'p1'], 'noncontiguous_passages'),
    (['p2', 'p1'], 'noncontiguous_passages'),
    (['p1', 'p3'], 'noncontiguous_passages'),
])
def test_invalid_citations_cannot_be_resolved(selection, code):
    payload, output = sample()
    draft = passage_report(payload, output)
    entry = payload['stories'][0]['evidence'][0]
    entry['text'] = 'A supported source sentence. ' * 100  # At least three passages.
    item = draft['stories'][0]['bullets'][0]
    item.update(evidence_id=entry['id'], quote_ids=selection)
    with pytest.raises(EditorialValidationError) as error:
        validate(payload, draft)
    assert error.value.issue['code'] == code


def test_citing_an_entry_does_not_support_versions_in_its_unselected_passages():
    payload, output = sample()
    draft = passage_report(payload, output)
    entry = payload['stories'][0]['evidence'][0]
    entry['text'] = 'An unrelated source sentence. ' * 40 + 'Affected version 99.88.77.'
    item = draft['stories'][0]['bullets'][0]
    item.update(text='Affected version 99.88.77.', evidence_id=entry['id'], quote_ids=['p1'])
    with pytest.raises(EditorialValidationError) as error:
        validate(payload, draft)
    assert error.value.issue['code'] == 'unsupported_fact_token'


def test_conflicting_or_cross_story_citations_still_fail():
    payload, output = sample()
    draft = passage_report(payload, output)
    item = draft['stories'][0]['bullets'][0]
    item['quote'] = 'Invented quotation'
    with pytest.raises(EditorialValidationError, match='Quotation must match'):
        validate(payload, draft)
    del item['quote']
    item['evidence_id'] = payload['stories'][1]['evidence'][0]['id']
    with pytest.raises(EditorialValidationError) as error:
        validate(payload, draft)
    assert error.value.issue['code'] == 'unknown_evidence_id'


def test_optional_diagram_nodes_and_edges_resolve_source_passages():
    payload, output = sample()
    payload['stories'][0]['evidence'].append({'id': 's1.sequence', 'text': 'Step A leads to Step B.'})
    output['stories'][0]['diagram'] = {
        'nodes': [{'id': node, 'text': text, 'evidence_id': 's1.sequence', 'quote_ids': ['p1']}
                  for node, text in [('n1', 'Step A'), ('n2', 'Step B')]],
        'edges': [{'from': 'n1', 'to': 'n2', 'evidence_id': 's1.sequence', 'quote_ids': ['p1']}]}
    result = validate(payload, output)
    for item in result['stories'][0]['diagram']['nodes'] + result['stories'][0]['diagram']['edges']:
        assert item['quote'] == 'Step A leads to Step B.'


def test_groq_correction_uses_passage_ids_and_finishes_all_batches(monkeypatch):
    payload, original = sample()
    draft = passage_report(payload, original)
    by_id = {story['id']: story for story in draft['stories']}
    monkeypatch.setattr(ai, 'pack', lambda *a, **k: payload)

    class Response:
        status_code = 200
        headers = {}
        def __init__(self, output):
            self.raw = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(output)}}]}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def iter_content(self, size): yield self.raw

    def respond(url, **kwargs):
        packet = json.loads(kwargs['json']['messages'][1]['content'])
        correcting = 'original_evidence' in packet
        evidence = packet['original_evidence'] if correcting else packet
        ident = evidence['stories'][0]['id']
        assert 'passages' in evidence['stories'][0]['evidence'][0]
        story = copy.deepcopy(by_id[ident])
        if ident in ('s1', 's7') and not correcting:
            story['bullets'][0]['text'] += ' CVE-2099-9999999'
        return Response({'stories': [story]})

    post = Mock(side_effect=respond)
    monkeypatch.setattr(ai.requests, 'post', post)
    report = {'period': payload['period'], 'generated': payload['generated'], 'errors': [],
              'stories': [{} for _ in payload['stories']]}
    config = ai.AIConfig('groq', 'openai/gpt-oss-120b', 'private-test-key',
                         'https://api.groq.com/openai/v1', batch_size=1)
    result = ai.generate(report, config)
    assert post.call_count == 12  # Ten updates and one correction each for s1/s7.
    assert [story['id'] for story in result['editorial']['report']['stories']] == list(by_id)
    assert result['ai']['status'] == 'validated'
    assert pdf_report(result).startswith(b'%PDF-')
