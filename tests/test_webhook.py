import json
from unittest.mock import Mock
import pytest
import requests
from cyberbrief.webhook import deliver

URL = 'https://discord.com/api/webhooks/123/test-secret'


def report(tmp_path):
    from pathlib import Path
    root=Path(__file__).resolve().parent.parent/'docs'/'editorial-preview'
    payload=json.loads((root/'evidence.json').read_text());output=json.loads((root/'summary.json').read_text())
    data={'demo':False,'period':'daily','generated':'2026-10-12T00:00:00Z','stories':[{}],'errors':[], 'ai':{'status':'validated'}, 'editorial':{'evidence':payload,'report':output}}
    (tmp_path/'briefing.json').write_text(json.dumps(data))
    (tmp_path/'briefing.pdf').write_bytes(b'%PDF-test')
    (tmp_path/'briefing.txt').write_text('Test report')
    return tmp_path


@pytest.mark.parametrize('format,extension', [('pdf','pdf'), ('text','txt')])
def test_webhook_upload_and_mentions_disabled(tmp_path, monkeypatch, format, extension):
    def post(url, **kwargs):
        assert url == URL and not kwargs['allow_redirects']
        assert kwargs['params'] == {'wait':'true'}
        assert json.loads(kwargs['data']['payload_json'])['allowed_mentions'] == {'parse':[]}
        file = kwargs['files']['files[0]']
        assert file[0].endswith(extension) and file[1].read()
        return Mock(status_code=200)
    monkeypatch.setattr('cyberbrief.webhook.requests.post',post)
    assert deliver(report(tmp_path),format,URL).endswith(extension)


def test_delivery_failure_does_not_leak_secret_or_retry(tmp_path, monkeypatch):
    post = Mock(side_effect=requests.ConnectionError(URL))
    monkeypatch.setattr('cyberbrief.webhook.requests.post', post)
    with pytest.raises(RuntimeError) as exc: deliver(report(tmp_path),'pdf',URL)
    assert 'test-secret' not in str(exc.value) and post.call_count == 1


def test_delivery_discloses_source_fallback_count(tmp_path, monkeypatch):
    directory = report(tmp_path)
    data = json.loads((directory/'briefing.json').read_text())
    data['ai']['source_fallbacks'] = [{'story_id': 's1', 'reason': 'validation_failure'}]
    (directory/'briefing.json').write_text(json.dumps(data))

    def post(url, **kwargs):
        content = json.loads(kwargs['data']['payload_json'])['content']
        assert '1 update(s) used validated source fallback' in content
        assert 'AI generation did not produce validated drafts' in content
        return Mock(status_code=200)

    monkeypatch.setattr('cyberbrief.webhook.requests.post', post)
    deliver(directory, 'pdf', URL)


@pytest.mark.parametrize('status',[302,401,404,429,500])
def test_webhook_failed_http_status_is_failure(tmp_path,monkeypatch,status):
    monkeypatch.setattr('cyberbrief.webhook.requests.post',Mock(return_value=Mock(status_code=status)))
    with pytest.raises(RuntimeError,match=str(status)):deliver(report(tmp_path),'pdf',URL)


def test_demo_or_untrusted_destination_cannot_be_sent(tmp_path,monkeypatch):
    post = Mock();monkeypatch.setattr('cyberbrief.webhook.requests.post',post)
    directory = report(tmp_path)
    with pytest.raises(ValueError):deliver(directory,'pdf','https://example.com/api/webhooks/123/token')
    data=json.loads((directory/'briefing.json').read_text());data['demo']=True;(directory/'briefing.json').write_text(json.dumps(data))
    with pytest.raises(ValueError,match='Fictional'):deliver(directory,'pdf',URL)
    post.assert_not_called()


@pytest.mark.parametrize('date,expected', [
    ('2026-07-01T12:50:00+00:00', True),
    ('2026-07-01T18:50:00+00:00', True),
    ('2026-12-01T12:50:00+00:00', False),
    ('2026-12-01T13:50:00+00:00', True),
    ('2026-03-08T12:50:00+00:00', True),
    ('2026-11-01T13:50:00+00:00', True),
])
def test_schedule_summer_winter_and_transition_days(date,expected):
    from datetime import datetime
    from cyberbrief.schedule import should_run
    now=datetime.fromisoformat(date)
    assert should_run('schedule','50 12-18 * * *',now) is expected
    assert should_run('workflow_dispatch','',now)


def test_schedule_does_not_skip_delayed_matching_slot():
    from datetime import datetime
    from cyberbrief.schedule import should_run
    assert should_run('schedule','50 12-18 * * *',datetime.fromisoformat('2026-07-01T13:10:00+00:00'))
    assert not should_run('push','50 12-18 * * *')


@pytest.mark.parametrize('date,expected', [
    ('2026-07-03T12:30:00+00:00', True),
    ('2026-07-03T18:30:00+00:00', True),
    ('2026-12-04T13:30:00+00:00', True),
    ('2026-12-04T12:30:00+00:00', False),
    ('2026-12-05T13:30:00+00:00', False),
])
def test_weekly_dst_gate(date,expected):
    from datetime import datetime
    from cyberbrief.schedule import gate
    enabled,period=gate('schedule','30 12-18 * * 5',now=datetime.fromisoformat(date))
    assert enabled==expected and period=='weekly'


def test_delivery_keys_are_stable_for_completed_windows():
    from datetime import datetime
    from cyberbrief.schedule import delivery_key
    assert delivery_key('daily', datetime.fromisoformat('2026-10-31T12:00:00+00:00')) == 'cyber-feed-delivered-daily-2026-10-30'
    assert delivery_key('daily', datetime.fromisoformat('2026-10-31T13:00:00+00:00')) == 'cyber-feed-delivered-daily-2026-10-31'
    assert delivery_key('weekly', datetime.fromisoformat('2026-10-31T13:00:00+00:00')) == 'cyber-feed-delivered-weekly-2026-10-30'


def test_workflow_has_backup_slots_and_success_deduplication():
    from pathlib import Path
    import yaml
    workflow = yaml.load((Path(__file__).resolve().parent.parent / '.github/workflows/briefing.yml').read_text(), Loader=yaml.BaseLoader)
    expressions = {item['cron'] for item in workflow['on']['schedule']}
    assert expressions == {'50 12-18 * * *', '30 12-18 * * 5'}
    assert workflow['permissions']['actions'] == 'write'
    schedule_steps = workflow['jobs']['schedule']['steps']
    assert any(step.get('uses') == 'actions/cache/restore@v4' and step.get('with', {}).get('lookup-only') == 'true'
               for step in schedule_steps)
    deliver = workflow['jobs']['deliver']
    assert 'already_delivered' in deliver['if']
    assert any(step.get('uses') == 'actions/cache/save@v4' for step in deliver['steps'])


def test_friday_thursday_week_and_dst_boundary():
    from datetime import datetime, timedelta
    from cyberbrief.schedule import report_window,TORONTO
    start,end=report_window('weekly',datetime.fromisoformat('2026-10-16T12:30:00+00:00'))
    assert start.astimezone(TORONTO).isoformat()=='2026-10-09T00:00:00-04:00'
    assert end.astimezone(TORONTO).isoformat()=='2026-10-16T00:00:00-04:00'
    start,end=report_window('weekly',datetime.fromisoformat('2026-11-06T13:30:00+00:00'))
    assert end-start==timedelta(days=7,hours=1)
    start,end=report_window('daily',datetime.fromisoformat('2026-11-01T13:50:00+00:00'))
    assert end-start==timedelta(hours=25)
    assert end.astimezone(TORONTO).hour==8 and end.astimezone(TORONTO).minute==50


def test_weekly_window_includes_friday_and_excludes_next_friday(monkeypatch):
    from datetime import datetime,timezone,timedelta
    from cyberbrief import engine
    from cyberbrief.models import Story
    start=datetime(2026,10,9,4,tzinfo=timezone.utc);end=start+timedelta(days=7)
    stories=[Story(name,'https://example.invalid/'+name,'Vendor',0,date,'Source excerpt') for name,date in [('before',start-timedelta(seconds=1)),('first',start),('last',end-timedelta(seconds=1)),('next',end)]]
    monkeypatch.setattr(engine,'fetch_feeds',lambda sources,**kwargs:(stories,[]))
    monkeypatch.setattr(engine,'fetch_cisa_kev',lambda:{})
    monkeypatch.setattr(engine,'enrich',lambda s,*a:{'status':'article','url':s.url,'fields':{},'summary':s.summary})
    data=engine.build('weekly',window_start=start,window_end=end)
    assert {s['title'] for s in data['stories']}=={'first','last'}


def test_non_ai_or_invalid_editorial_report_is_not_delivered(tmp_path,monkeypatch):
    directory=report(tmp_path);post=Mock();monkeypatch.setattr('cyberbrief.webhook.requests.post',post)
    data=json.loads((directory/'briefing.json').read_text());data.pop('ai');(directory/'briefing.json').write_text(json.dumps(data))
    with pytest.raises(ValueError,match='validated AI'):deliver(directory,'pdf',URL)
    post.assert_not_called()
