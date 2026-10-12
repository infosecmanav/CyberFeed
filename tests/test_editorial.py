import copy
from io import BytesIO
import json
from pathlib import Path
import pytest
from pypdf import PdfReader
from cyberbrief.editorial import pack,validate,render
from cyberbrief.engine import build


def sample():
    root=Path(__file__).resolve().parent.parent/'docs'/'editorial-preview'
    return json.loads((root/'evidence.json').read_text()),json.loads((root/'summary.json').read_text())


def test_compact_input_bounded_and_fictional_status_preserved():
    data=build(fixtures=True)
    data['stories'][0]['summary']='source words '*1000
    p=pack(data)
    assert p['demo'] and len(p['stories'])==len(data['stories'])
    evidence=p['stories'][0]['evidence'][0]
    assert len(evidence['text'].split())<=60 and evidence['truncated']
    assert 'api_key' not in json.dumps(p)


def test_unmatched_evidence_and_oversized_output_fail():
    p,s=sample();validate(p,s)
    bad=copy.deepcopy(s);bad['stories'][0]['bullets'][0]['quote']='Invented quotation'
    with pytest.raises(ValueError,match='quotation'):validate(p,bad)
    bad=copy.deepcopy(s);bad['stories'][0]['bullets'][0]['text']='words '*200
    with pytest.raises(ValueError,match='120-word'):validate(p,bad)
    bad=copy.deepcopy(s);bad['stories'].reverse()
    with pytest.raises(ValueError,match='IDs'):validate(p,bad)


@pytest.mark.parametrize('theme',['dark','light'])
def test_preview_pdf_preserves_summary_links_and_missing_fields(theme):
    p,s=sample();pdf=render(p,s,theme);reader=PdfReader(BytesIO(pdf))
    text='\n'.join(page.extract_text() for page in reader.pages)
    assert 'DESIGN PREVIEW' in text and 'Not supplied: versions' in text
    assert 'FortiMail' in text and 'Lantronix' in text and 'Citrix' in text
    assert any(page.get('/Annots') for page in reader.pages)
    assert len(reader.pages)==10
    for page in reader.pages:
        page_text=page.extract_text()
        assert page_text.count('a. AI summary')==1
        assert page_text.count('b. Detailed AI-powered report')==1
        assert page_text.count('c. References')==1
    assert len(p['stories']) == 10
    assert text.count('a. AI summary') == 10
    assert text.count('b. Detailed AI-powered report') == 10
    assert text.count('c. References') == 10
    cursor = 0
    for _ in p['stories']:
        a = text.index('a. AI summary',cursor)
        b = text.index('b. Detailed AI-powered report',a)
        c = text.index('c. References',b)
        assert a < b < c
        cursor = c + len('c. References')
    assert 'Source-backed diagram' not in text
    assert '[1.1]' in text and 'https://' in text


def test_detailed_citations_and_length_are_checked():
    p,s=sample()
    bad=copy.deepcopy(s);bad['stories'][0]['details'][0]['quote']='Invented detail evidence'
    with pytest.raises(ValueError,match='quotation'):validate(p,bad)
    bad=copy.deepcopy(s);bad['stories'][0]['details'][0]['text']='word '*230
    with pytest.raises(ValueError,match='220 words'):validate(p,bad)


def test_diagram_is_optional_and_requires_supported_nodes_and_edges():
    p,s=sample();evidence={'id':'s1.sequence','text':'Step A leads to Step B.','truncated':False}
    p['stories'][0]['evidence'].append(evidence)
    s['stories'][0]['diagram']={'nodes':[{'id':n,'text':label,'evidence_id':'s1.sequence','quote':evidence['text']} for n,label in [('n1','Step A'),('n2','Step B')]],'edges':[{'from':'n1','to':'n2','evidence_id':'s1.sequence','quote':evidence['text']}]}
    validate(p,s)
    reader=PdfReader(BytesIO(render(p,s)))
    text='\n'.join(page.extract_text() for page in reader.pages)
    assert text.count('Source-backed diagram')==1
    assert 'Step A' in text and 'Step B' in text
    bad=copy.deepcopy(s);bad['stories'][0]['diagram']['edges'][0]['to']='invented'
    with pytest.raises(ValueError,match='invalid nodes'):validate(p,bad)
    bad=copy.deepcopy(s);bad['stories'][0]['diagram']['edges'][0]['quote']='Invented transition'
    with pytest.raises(ValueError,match='matching evidence'):validate(p,bad)
