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
    assert len(reader.pages)<=3
