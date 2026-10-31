"""Toronto delivery slots and explicit report windows, including DST changes."""
from datetime import datetime, time, timedelta, timezone
import os
from zoneinfo import ZoneInfo

TORONTO = ZoneInfo('America/Toronto')
SLOTS = {'50 12-18 * * *': ('daily', time(8, 50)),
         '30 12-18 * * 5': ('weekly', time(8, 30))}
RETRY_WINDOW = timedelta(hours=6)


def gate(event, expression, period='daily', now=None):
    if event == 'workflow_dispatch':
        return True, period
    if event != 'schedule' or expression not in SLOTS:
        return False, period
    local = (now or datetime.now(timezone.utc)).astimezone(TORONTO)
    selected, clock = SLOTS[expression]
    if selected == 'weekly' and local.weekday() != 4:
        return False, selected
    target = datetime.combine(local.date(), clock, tzinfo=TORONTO)
    return target <= local <= target + RETRY_WINDOW, selected


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
    """Stable key for one completed report window, used to prevent duplicates."""
    _, end = report_window(period, now)
    date = end.astimezone(TORONTO).date().isoformat()
    return f'cyber-feed-delivered-{period}-{date}'


if __name__ == '__main__':
    enabled, period = gate(os.getenv('GITHUB_EVENT_NAME'), os.getenv('SCHEDULE_EXPRESSION'), os.getenv('REPORT_PERIOD', 'daily'))
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
        output.write('enabled=' + str(enabled).lower() + '\nperiod=' + period + '\n')
        output.write('delivery_key=' + delivery_key(period) + '\n')
