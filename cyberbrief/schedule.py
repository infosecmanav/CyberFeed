"""Toronto delivery slots and explicit report windows, including DST changes."""
from datetime import datetime, time, timedelta, timezone
import os
from zoneinfo import ZoneInfo

TORONTO = ZoneInfo('America/Toronto')
SLOTS = {'50 12 * * *': ('daily', time(8, 50)), '50 13 * * *': ('daily', time(8, 50)),
         '30 12 * * 5': ('weekly', time(8, 30)), '30 13 * * 5': ('weekly', time(8, 30))}


def gate(event, expression, period='daily', now=None):
    if event == 'workflow_dispatch':
        return True, period
    if event != 'schedule' or expression not in SLOTS:
        return False, period
    local = (now or datetime.now(timezone.utc)).astimezone(TORONTO)
    selected, clock = SLOTS[expression]
    target = datetime.combine(local.date(), clock, tzinfo=TORONTO).astimezone(timezone.utc)
    return int(expression.split()[1]) == target.hour, selected


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


if __name__ == '__main__':
    enabled, period = gate(os.getenv('GITHUB_EVENT_NAME'), os.getenv('SCHEDULE_EXPRESSION'), os.getenv('REPORT_PERIOD', 'daily'))
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
        output.write('enabled=' + str(enabled).lower() + '\nperiod=' + period + '\n')
