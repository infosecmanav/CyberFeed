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


@pytest.mark.parametrize('date,expected', [('2026-07-01T12:49:00+00:00',False),
                                           ('2026-07-01T12:50:00+00:00',True),
                                           ('2026-12-01T12:49:00+00:00',False),
                                           ('2026-12-01T12:50:00+00:00',True),
                                           ('2026-03-08T12:50:00+00:00',True),
                                           ('2026-11-01T12:50:00+00:00',True)])
def test_schedule_uses_fixed_utc_time(date,expected):
    from datetime import datetime
    from cyberbrief.schedule import DAILY_SCHEDULE,should_run
    now=datetime.fromisoformat(date)
    assert should_run('schedule',DAILY_SCHEDULE,now) is expected
    assert should_run('workflow_dispatch','',now)


def test_schedule_does_not_skip_delayed_matching_slot():
    from datetime import datetime
    from cyberbrief.schedule import DAILY_SCHEDULE,should_run
    assert should_run('schedule',DAILY_SCHEDULE,datetime.fromisoformat('2026-07-01T13:10:00+00:00'))
    assert not should_run('schedule',DAILY_SCHEDULE,datetime.fromisoformat('2026-07-01T11:10:00+00:00'))
    assert not should_run('push',DAILY_SCHEDULE)


@pytest.mark.parametrize('date,expected', [('2026-07-03T12:30:00+00:00',True),
                                           ('2026-07-03T12:29:00+00:00',False),
                                           ('2026-12-04T12:30:00+00:00',True),
                                           ('2026-12-04T12:29:00+00:00',False),
                                           ('2026-07-04T12:30:00+00:00',False)])
def test_weekly_uses_fixed_utc_time(date,expected):
    from datetime import datetime
    from cyberbrief.schedule import WEEKLY_SCHEDULE,gate
    enabled,period=gate('schedule',WEEKLY_SCHEDULE,now=datetime.fromisoformat(date))
    assert enabled==expected and period=='weekly'


def test_delivery_keys_follow_completed_utc_windows():
    from datetime import datetime
    from cyberbrief.schedule import delivery_key
    before=datetime.fromisoformat('2026-11-01T12:49:00+00:00')
    after=datetime.fromisoformat('2026-11-01T12:50:00+00:00')
    assert delivery_key('daily',before).endswith('2026-10-31')
    assert delivery_key('daily',after).endswith('2026-11-01')
    assert delivery_key('weekly',after).endswith('2026-10-30')


def test_workflow_has_early_fallbacks_and_success_only_delivery_lock():
    from pathlib import Path
    workflow=(Path(__file__).resolve().parent.parent/'.github/workflows/briefing.yml').read_text()
    assert "cron: '50 6-13 * * *'" in workflow
    assert "cron: '30 6-13 * * 5'" in workflow
    assert 'actions/cache/restore@v4' in workflow and 'lookup-only: true' in workflow
    assert workflow.index('Send report to Discord') < workflow.index('Record successful scheduled delivery')
    assert 'actions/cache/save@v4' in workflow


def test_friday_thursday_week_and_daily_windows_are_fixed_utc():
    from datetime import datetime, timedelta
    from cyberbrief.schedule import report_window
    start,end=report_window('weekly',datetime.fromisoformat('2026-10-16T12:30:00+00:00'))
    assert start.isoformat()=='2026-10-09T00:00:00+00:00'
    assert end.isoformat()=='2026-10-16T00:00:00+00:00'
    start,end=report_window('weekly',datetime.fromisoformat('2026-11-06T12:30:00+00:00'))
    assert end-start==timedelta(days=7)
    start,end=report_window('daily',datetime.fromisoformat('2026-11-01T12:50:00+00:00'))
    assert end-start==timedelta(hours=24)
    assert end.hour==12 and end.minute==50


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
