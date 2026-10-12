import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from cyberbrief.engine import build
from cyberbrief.reports import text_messages, pdf_report
from cyberbrief.preferences import get_format, set_format


def test_preferences_persist_and_are_per_user(tmp_path, monkeypatch):
    monkeypatch.setenv('CYBERBRIEF_STATE_DIR', str(tmp_path))
    assert get_format(1) == 'text'
    set_format(1, 'pdf')
    assert get_format(1) == 'pdf'
    assert get_format(2) == 'text'
    set_format(1, 'text')
    assert get_format(1) == 'text'
    with pytest.raises(ValueError):
        set_format(1, 'html')
    assert get_format(1) == 'text'


def test_text_covers_stories_warnings_and_discord_limits():
    data = build(period='weekly', fixtures=True)
    data['stories'][0]['summary'] = '@everyone ' + 'long excerpt ' * 600
    data['errors'] = [{'source': 'CISA', 'error': 'Access denied'}]
    messages = text_messages(data)
    assert all(0 < len(m) <= 1900 for m in messages)
    text = '\n'.join(messages)
    assert '@everyone' not in text
    assert 'Coverage warnings' in text and 'Access denied' in text
    assert 'Weekly overview' in text and 'DEMO' in text
    for story in data['stories']:
        assert story['url'] in text


def test_pdf_is_readable_paginated_and_contains_source_links():
    from pypdf import PdfReader
    data = build(period='weekly', fixtures=True)
    data['stories'] *= 15
    data['stories'][0]['title'] = 'Update <b> & Canadian security — café'
    data['errors'] = [{'source': 'CISA', 'error': 'Access denied'}]
    pdf = pdf_report(data)
    assert pdf.startswith(b'%PDF-')
    reader = PdfReader(BytesIO(pdf))
    assert len(reader.pages) > 1
    text = '\n'.join(p.extract_text() for p in reader.pages)
    assert 'Update <b> & Canadian security' in text
    assert 'café' in text and 'Access denied' in text and 'DEMO' in text
    assert data['stories'][0]['url'] in text
    assert any(p.get('/Annots') for p in reader.pages)


def test_empty_reports_explain_coverage_failure():
    data = {'period': 'daily', 'generated': '2026-10-11', 'stories': [], 'errors': [{'source': 'Feed', 'error': 'Unavailable'}]}
    text = '\n'.join(text_messages(data))
    assert 'No updates' in text and 'Unavailable' in text
    assert pdf_report(data).startswith(b'%PDF-')


@pytest.mark.parametrize('saved,override,expected', [('pdf', None, 'pdf'), ('pdf', 'text', 'text'), ('text', 'pdf', 'pdf')])
def test_bot_delivery_uses_preference_or_override(monkeypatch, saved, override, expected):
    from cyberbrief import discord_bot as module
    data = build(fixtures=True)
    monkeypatch.setattr(module.AIConfig, 'from_env', lambda: object())
    monkeypatch.setattr(module, 'generate', lambda report, config: report)
    monkeypatch.setattr(module, 'get_format', lambda user: saved)
    monkeypatch.setattr(module, 'build', lambda *args: data)
    monkeypatch.setattr(module, 'build_lock', asyncio.Lock())
    interaction = SimpleNamespace(user=SimpleNamespace(id=42), response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
    asyncio.run(module.briefing.callback(interaction, 'daily', override))
    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    calls = interaction.followup.send.await_args_list
    assert calls and all(c.kwargs['ephemeral'] for c in calls)
    if expected == 'pdf':
        assert len(calls) == 1
        assert calls[0].kwargs['file'].filename.endswith('.pdf')
        assert calls[0].kwargs['file'].fp.read().startswith(b'%PDF-')
    else:
        assert all('file' not in c.kwargs for c in calls)
        assert 'Fictional test story' in '\n'.join(c.args[0] for c in calls)


def test_bot_reports_failure_without_exposing_exception(monkeypatch):
    from cyberbrief import discord_bot as module
    monkeypatch.setattr(module.AIConfig, 'from_env', lambda: object())
    monkeypatch.setattr(module, 'get_format', lambda user: 'text')
    monkeypatch.setattr(module, 'build_lock', asyncio.Lock())
    def fail(*args):
        raise RuntimeError('sensitive value')
    monkeypatch.setattr(module, 'build', fail)
    interaction = SimpleNamespace(user=SimpleNamespace(id=1), response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
    asyncio.run(module.briefing.callback(interaction))
    message = interaction.followup.send.await_args.args[0]
    assert 'unavailable' in message and 'sensitive' not in message
