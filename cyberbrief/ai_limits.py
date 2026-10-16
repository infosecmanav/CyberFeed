"""Safe provider-limit diagnostics and conservative Groq request pacing."""
import json
import logging
import math
import re
import time

LOGGER = logging.getLogger(__name__)


def token_error_metrics(message):
    """Extract bounded numeric limits, excluding identifiers and free-form text."""
    if not isinstance(message, str) or len(message) > 16384 or not re.search(r'token|\bTPM\b', message, re.I):
        return {}
    result = {}
    for name, pattern in [('limit', r'\bLimit\s*:?\s*([0-9][0-9,]{0,10})\b'),
                          ('requested', r'\bRequested\s*:?\s*([0-9][0-9,]{0,10})\b')]:
        match = re.search(pattern, message, re.I)
        if match:
            value = int(match.group(1).replace(',', ''))
            if 0 <= value <= 10_000_000:
                result[name] = value
    if re.search(r'tokens per minute|\bTPM\b', message, re.I):
        result['scope'] = 'tokens per minute'
    elif re.search(r'tokens per day|\bTPD\b', message, re.I):
        result['scope'] = 'tokens per day'
    return result


def _reset_seconds(value):
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    parts = list(re.finditer(r'([0-9]+(?:\.[0-9]+)?)(ms|m|s)', value))
    if not parts or ''.join(part.group(0) for part in parts) != value:
        return None
    seconds = sum(float(part.group(1)) * {'ms': .001, 's': 1, 'm': 60}[part.group(2)] for part in parts)
    return seconds if 0 <= seconds <= 65 else None


class GroqRateState:
    """Pace later requests from actual token headers; never infer account quotas."""

    def __init__(self):
        self.remaining = None
        self.reset_at = 0

    def observe(self, headers):
        remaining = headers.get('x-ratelimit-remaining-tokens')
        reset = _reset_seconds(headers.get('x-ratelimit-reset-tokens'))
        try:
            remaining = int(remaining)
        except (TypeError, ValueError):
            remaining = None
        self.remaining = max(0, remaining) if remaining is not None and reset is not None else None
        self.reset_at = time.monotonic() + reset if reset is not None else 0

    def wait(self, body):
        # UTF-8 bytes / 2 is a conservative estimate, not a model tokenizer.
        estimated = len(json.dumps(body, ensure_ascii=False).encode('utf-8')) // 2
        estimated += body.get('max_tokens', body.get('max_completion_tokens', 0))
        delay = self.reset_at - time.monotonic()
        if self.remaining is not None and self.remaining < estimated and delay > 0:
            seconds = min(65, math.ceil(delay) + 1)
            LOGGER.warning('Waiting %s seconds for Groq token capacity before the next request.', seconds)
            time.sleep(seconds)
        self.remaining = None
        self.reset_at = 0
