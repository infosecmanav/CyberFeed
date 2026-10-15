"""Required, provider-neutral editorial generation with evidence validation."""
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
import json
import logging
import os
from pathlib import Path
import re
import time
from urllib.parse import urlparse
import requests
from .editorial import pack, validate

ROOT = Path(__file__).resolve().parent.parent
BASES = {'groq': 'https://api.groq.com/openai/v1', 'openrouter': 'https://openrouter.ai/api/v1', 'openai': 'https://api.openai.com/v1'}
LOGGER = logging.getLogger(__name__)
TRANSIENT_STATUSES = frozenset({500, 502, 503, 504})
MAX_ATTEMPTS = 3
MAX_RETRY_DELAY = 60
PROVIDER_STATUS_CODES = frozenset({'CANCELLED', 'UNKNOWN', 'INVALID_ARGUMENT', 'DEADLINE_EXCEEDED', 'NOT_FOUND', 'ALREADY_EXISTS', 'PERMISSION_DENIED', 'RESOURCE_EXHAUSTED', 'FAILED_PRECONDITION', 'ABORTED', 'OUT_OF_RANGE', 'UNIMPLEMENTED', 'INTERNAL', 'UNAVAILABLE', 'DATA_LOSS', 'UNAUTHENTICATED'})
PROVIDER_REASON_CODES = frozenset({'API_KEY_INVALID', 'API_KEY_EXPIRED', 'API_KEY_SERVICE_BLOCKED', 'API_KEY_HTTP_REFERRER_BLOCKED', 'API_KEY_IP_ADDRESS_BLOCKED', 'SERVICE_DISABLED', 'BILLING_DISABLED', 'CONSUMER_INVALID', 'ACCESS_TOKEN_SCOPE_INSUFFICIENT', 'RATE_LIMIT_EXCEEDED', 'RESOURCE_QUOTA_EXCEEDED', 'MODEL_NOT_FOUND', 'LOCATION_POLICY_VIOLATED'})


class AIError(RuntimeError):
    """A safe-to-display failure: never contains keys or provider response bodies."""


def _retry_delay(response, attempt):
    """Honor Retry-After without waiting indefinitely or retrying too early."""
    backoff = 10 * 2 ** (attempt - 1)
    value = response.headers.get('Retry-After', '').strip()
    if not value or len(value) > 128:
        return backoff
    try:
        requested = int(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return backoff
            requested = date.timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return backoff
    if requested > MAX_RETRY_DELAY:
        return None
    return max(backoff, requested)


def _provider_codes(response):
    """Extract only known symbolic codes, never arbitrary provider error text."""
    try:
        raw = bytearray()
        for chunk in response.iter_content(4096):
            raw.extend(chunk)
            if len(raw) > 16384:
                return ''
        error = json.loads(raw).get('error', {})
        code = error.get('status')
        codes = [code] if code in PROVIDER_STATUS_CODES else []
        details = error.get('details', [])
        if isinstance(details, list):
            for detail in details:
                if isinstance(detail, dict):
                    reason = detail.get('reason')
                    if reason in PROVIDER_REASON_CODES and reason not in codes:
                        codes.append(reason)
        return ' Provider code(s): ' + ', '.join(codes) + '.' if codes else ''
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return ''


def _http_error(status, attempts, response=None):
    prefix = f'AI request failed (HTTP {status}) after {attempts} attempt(s); '
    if response is not None:
        codes = _provider_codes(response)
        if codes:
            prefix += codes.strip() + ' '
    if status in TRANSIENT_STATUSES:
        return AIError(prefix + 'the provider is temporarily unavailable. Retry later or select another available model.')
    if status == 404:
        return AIError(prefix + 'check AI_PROVIDER, AI_MODEL, and any custom AI_BASE_URL; the requested model or endpoint was not found.')
    if status in (401, 403):
        return AIError(prefix + 'check AI_API_KEY and the account\'s access to the selected model.')
    if status == 429:
        return AIError(prefix + 'check the provider\'s rate limits and available quota. Rate-limit responses are not automatically retried.')
    return AIError(prefix + 'check model access and API settings.')


def _request_bytes(url, headers, body, *, max_attempts=MAX_ATTEMPTS):
    # Retry only explicit transient HTTP failures. Network/read failures may
    # follow a completed generation, so repeating them could consume more quota.
    for attempt in range(1, max_attempts + 1):
        with requests.post(url, headers=headers, json=body, timeout=(10, 180), allow_redirects=False, stream=True) as response:
            status = response.status_code
            if 200 <= status < 300:
                raw = bytearray()
                for chunk in response.iter_content(65536):
                    raw.extend(chunk)
                    if len(raw) > 2_000_000:
                        raise AIError('AI response exceeds the 2 MB limit.')
                return raw
            if status not in TRANSIENT_STATUSES or attempt == max_attempts:
                raise _http_error(status, attempt, response)
            delay = _retry_delay(response, attempt)
            if delay is None:
                raise AIError(f'AI request failed (HTTP {status}); the provider requested a retry delay longer than 60 seconds. Retry later.')
        # Close the response before waiting. Never log URLs, keys, or raw bodies.
        LOGGER.warning('AI provider returned HTTP %s; retrying in %.0f seconds (attempt %s/%s).', status, delay, attempt + 1, max_attempts)
        time.sleep(delay)


@dataclass(frozen=True)
class AIConfig:
    provider: str
    model: str
    api_key: str = field(repr=False)
    base_url: str = ''
    max_output_tokens: int = 16384
    json_mode: bool = True

    @classmethod
    def from_env(cls):
        provider = (os.getenv('AI_PROVIDER') or '').strip().lower()
        key = (os.getenv('AI_API_KEY') or '').strip()
        model = (os.getenv('AI_MODEL') or '').strip()
        if not provider or not key or not model:
            raise AIError('Configure AI_API_KEY, AI_PROVIDER, and AI_MODEL before generating reports.')
        if provider not in ('gemini', 'openai-compatible', *BASES):
            raise AIError('AI_PROVIDER must be gemini, groq, openrouter, openai, or openai-compatible.')
        if len(model) > 200 or any(c.isspace() for c in model):
            raise AIError('AI_MODEL must be a nonempty provider model identifier without whitespace.')
        base = (os.getenv('AI_BASE_URL') or BASES.get(provider, '')).rstrip('/')
        if provider == 'gemini':
            model = model.removeprefix('models/')
            if not re.fullmatch(r'[A-Za-z0-9._-]+', model):
                raise AIError('Invalid Gemini model identifier.')
            base = 'https://generativelanguage.googleapis.com/v1beta'
        else:
            try:
                parts = urlparse(base)
                port = parts.port
            except ValueError:
                raise AIError('Invalid AI_BASE_URL.') from None
            if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or port not in (None, 443):
                raise AIError('AI_BASE_URL must be an HTTPS API base URL without credentials, query, or fragment.')
        try:
            limit = int(os.getenv('AI_MAX_OUTPUT_TOKENS') or '16384')
            if not 256 <= limit <= 65536: raise ValueError()
        except ValueError:
            raise AIError('AI_MAX_OUTPUT_TOKENS must be between 256 and 65536.') from None
        mode = (os.getenv('AI_JSON_MODE') or 'true').lower()
        if mode not in ('true', 'false'):
            raise AIError('AI_JSON_MODE must be true or false.')
        return cls(provider, model, key, base, limit, mode == 'true')


def preflight(config):
    """Log effective non-secret settings and check native Gemini model metadata."""
    settings = {'provider': config.provider, 'model': config.model, 'base_url': config.base_url}
    safe = {name: value.replace(config.api_key, '[redacted]') if config.api_key else value
            for name, value in settings.items()}
    print('AI configuration: ' + json.dumps(safe), flush=True)
    if config.provider == 'gemini':
        from .gemini_models import check_model
        check_model(config.api_key, config.model)
        print('Gemini model metadata check passed. This does not verify generation access or quota.', flush=True)


def request_editorial(payload, config):
    instructions = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
    evidence = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    if len(evidence.encode('utf-8')) > 1_000_000:
        raise AIError('Evidence exceeds the 1 MB request limit; reduce the input scope.')
    if config.provider == 'gemini':
        url = f'{config.base_url}/models/{config.model}:generateContent'
        headers = {'x-goog-api-key': config.api_key}
        generation = {'maxOutputTokens': config.max_output_tokens}
        if config.json_mode: generation['responseMimeType'] = 'application/json'
        body = {'systemInstruction': {'parts': [{'text': instructions}]}, 'contents': [{'role': 'user', 'parts': [{'text': evidence}]}], 'generationConfig': generation}
    else:
        url = config.base_url + '/chat/completions'
        headers = {'Authorization': 'Bearer ' + config.api_key}
        body = {'model': config.model, 'messages': [{'role': 'system', 'content': instructions}, {'role': 'user', 'content': evidence}], 'max_tokens': config.max_output_tokens}
        if config.json_mode: body['response_format'] = {'type': 'json_object'}
    # Do not follow redirects with credentials or print raw bodies.
    # TLS verification stays enabled; transient HTTP retries are bounded.
    try:
        raw = _request_bytes(url, headers, body)
        result = json.loads(raw)
        if config.provider == 'gemini':
            candidate = result['candidates'][0]
            if candidate.get('finishReason') != 'STOP':
                raise AIError('AI response did not finish successfully; check output-token budget and provider restrictions.')
            text = ''.join(p.get('text', '') for p in candidate['content']['parts'] if not p.get('thought'))
        else:
            choice = result['choices'][0]
            if choice.get('finish_reason') != 'stop':
                raise AIError('AI response did not finish successfully; check output-token budget and provider restrictions.')
            text = choice['message']['content']
        if not isinstance(text, str) or not text.strip(): raise AIError('AI returned no report text.')
        match = re.fullmatch(r'\s*```(?:json)?\s*([\s\S]*?)\s*```\s*', text)
        return json.loads(match.group(1) if match else text)
    except requests.RequestException:
        raise AIError('AI service connection failed; check network access and TLS configuration. Network failures are not automatically retried.') from None
    except (KeyError, IndexError, ValueError, TypeError):
        raise AIError('AI returned an unsupported response or invalid JSON. Nothing will be delivered.') from None


def generate(report, config=None):
    config = config or AIConfig.from_env()
    if report.get('demo'):
        raise AIError('Live AI generation is not enabled for fictional fixture reports.')
    payload = pack(report, detail_level='full')
    # Empty windows need no model call, but configuration is still required.
    output = request_editorial(payload, config) if payload['stories'] else {'stories': []}
    try:
        validate(payload, output)
    except (ValueError, TypeError, AttributeError, KeyError):
        raise AIError('AI output failed report structure or evidence checks. Nothing will be delivered.') from None
    data = dict(report)
    data['editorial'] = {'evidence': payload, 'report': output}
    data['ai'] = {'provider': config.provider, 'model': config.model, 'status': 'validated'}
    return data
