"""Fixed UTC delivery slots and explicit UTC report windows."""
from datetime import datetime, time, timedelta, timezone
import os

DAILY_SCHEDULE = '50 6-13 * * *'
WEEKLY_SCHEDULE = '30 6-13 * * 5'
SCHEDULES = {DAILY_SCHEDULE: ('daily', time(12, 50)),
             WEEKLY_SCHEDULE: ('weekly', time(12, 30))}


def gate(event, expression, period='daily', now=None):
    if event == 'workflow_dispatch':
        return True, period
    if event != 'schedule' or expression not in SCHEDULES:
        return False, period
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    selected, clock = SCHEDULES[expression]
    # GitHub's scheduled-event service can be delayed by hours. Several early
    # probes are configured in the workflow; only a probe that reaches the UTC
    # delivery time is eligible. A workflow cache key prevents duplicates.
    enabled = current.time().replace(tzinfo=None) >= clock
    if selected == 'weekly':
        enabled = enabled and current.weekday() == 4
    return enabled, selected


def should_run(event, expression, now=None):
    return gate(event, expression, now=now)[0]


def report_window(period, now=None):
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if period == 'weekly':
        # Last completed Friday-Thursday UTC week; Friday midnight is exclusive.
        friday = current.date() - timedelta(days=(current.weekday()-4) % 7)
        end = datetime.combine(friday, time(), tzinfo=timezone.utc)
        start = datetime.combine(friday-timedelta(days=7), time(), tzinfo=timezone.utc)
    elif period == 'daily':
        end = datetime.combine(current.date(), time(12, 50), tzinfo=timezone.utc)
        if current < end:
            end = datetime.combine(current.date()-timedelta(days=1), time(12, 50), tzinfo=timezone.utc)
        start = end-timedelta(days=1)
    else:
        raise ValueError('Choose daily or weekly')
    return start, end


def delivery_key(period, now=None):
    """Stable cache key for one successful delivery per report window."""
    _, end = report_window(period, now)
    return f'cyber-feed-delivered-{period}-{end.date().isoformat()}'


if __name__ == '__main__':
    now = datetime.now(timezone.utc)
    enabled, period = gate(os.getenv('GITHUB_EVENT_NAME'), os.getenv('SCHEDULE_EXPRESSION'), os.getenv('REPORT_PERIOD', 'daily'), now)
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
        output.write('enabled=' + str(enabled).lower() + '\nperiod=' + period +
                     '\ndelivery_key=' + delivery_key(period, now) + '\n')
