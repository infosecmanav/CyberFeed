"""Toronto delivery slots and explicit report windows, including DST changes."""
from datetime import datetime, time, timedelta, timezone
import os
from zoneinfo import ZoneInfo

TORONTO = ZoneInfo('America/Toronto')
DAILY_SCHEDULE = '50 6-13 * * *'
WEEKLY_SCHEDULE = '30 6-13 * * 5'
SCHEDULES = {DAILY_SCHEDULE: ('daily', time(8, 50)),
             WEEKLY_SCHEDULE: ('weekly', time(8, 30))}


def gate(event, expression, period='daily', now=None):
    if event == 'workflow_dispatch':
        return True, period
    if event != 'schedule' or expression not in SCHEDULES:
        return False, period
    local = (now or datetime.now(timezone.utc)).astimezone(TORONTO)
    selected, clock = SCHEDULES[expression]
    # GitHub's scheduled-event service can be delayed by hours. Several early
    # probes are configured in the workflow; only a probe that reaches the
    # local delivery time is eligible. A workflow cache key prevents duplicates.
    enabled = local.time().replace(tzinfo=None) >= clock
    if selected == 'weekly':
        enabled = enabled and local.weekday() == 4
    return enabled, selected


def should_run(event, expression, now=None):
    return gate(event, expression, now=now)[0]


def report_window(period, now=None):
    local = (now or datetime.now(timezone.utc)).astimezone(TORONTO)
    if period == 'weekly':
        # Last completed Friday-Thursday calendar week; Friday midnight is exclusive.
        friday = local.date() - timedelta(days=(local.weekday()-4) % 7)
        end = datetime.combine(friday, time(), tzinfo=TORONTO)
        start = datetime.combine(friday-timedelta(days=7), time(), tzinfo=TORONTO)
    elif period == 'daily':
        end = datetime.combine(local.date(), time(8, 50), tzinfo=TORONTO)
        if local < end:
            end = datetime.combine(local.date()-timedelta(days=1), time(8, 50), tzinfo=TORONTO)
        start = datetime.combine(end.date()-timedelta(days=1), time(8, 50), tzinfo=TORONTO)
    else:
        raise ValueError('Choose daily or weekly')
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def delivery_key(period, now=None):
    """Stable cache key for one successful delivery per report window."""
    _, end = report_window(period, now)
    return f'cyber-feed-delivered-{period}-{end.astimezone(TORONTO).date().isoformat()}'


if __name__ == '__main__':
    now = datetime.now(timezone.utc)
    enabled, period = gate(os.getenv('GITHUB_EVENT_NAME'), os.getenv('SCHEDULE_EXPRESSION'), os.getenv('REPORT_PERIOD', 'daily'), now)
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
        output.write('enabled=' + str(enabled).lower() + '\nperiod=' + period +
                     '\ndelivery_key=' + delivery_key(period, now) + '\n')
