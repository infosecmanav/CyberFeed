"""Required, provider-neutral editorial generation with evidence validation."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse
import requests
from .editorial import pack, validate

ROOT = Path(__file__).resolve().parent.parent
BASES = {'groq': 'https://api.groq.com/openai/v1', 'openrouter': 'https://openrouter.ai/api/v1', 'openai': 'https://api.openai.com/v1'}


class AIError(RuntimeError):
    """A safe-to-display failure: never contains keys or provider response bodies."""


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
    # Do not follow redirects with credentials, print raw bodies, or retry paid/
    # quota-consuming calls automatically. TLS verification stays enabled.
    try:
        with requests.post(url, headers=headers, json=body, timeout=(10, 180), allow_redirects=False, stream=True) as response:
            if not 200 <= response.status_code < 300:
                raise AIError(f'AI request failed (HTTP {response.status_code}); check model access, key, quota, and API settings.')
            raw = bytearray()
            for chunk in response.iter_content(65536):
                raw.extend(chunk)
                if len(raw) > 2_000_000: raise AIError('AI response exceeds the 2 MB limit.')
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
        raise AIError('AI service connection failed; check network access and TLS configuration. No automatic retry was attempted.') from None
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
