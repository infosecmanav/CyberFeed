"""Gemini model discovery and an explicit, optional minimal generation test."""
import argparse
import json
import os
from pathlib import Path
import re

import requests

from .ai import AIError, _http_error, _request_bytes

CATALOG_URL = 'https://generativelanguage.googleapis.com/v1beta/models'
MODEL_ID = re.compile(r'models/(gemini-[A-Za-z0-9._-]{1,180})')


def available_models(api_key):
    """Return advertised generateContent model IDs, without claiming quota/access."""
    if not api_key:
        raise AIError('Configure the repository secret AI_API_KEY before checking Gemini models.')
    models = set()
    token = ''
    seen_tokens = set()
    for _ in range(10):
        params = {'pageSize': 100}
        if token:
            params['pageToken'] = token
        try:
            with requests.get(CATALOG_URL, headers={'x-goog-api-key': api_key}, params=params,
                              timeout=(10, 30), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise AIError(f'Gemini model listing failed (HTTP {response.status_code}). Check provider status, API-key restrictions, and Gemini API access.')
                raw = bytearray()
                for chunk in response.iter_content(65536):
                    raw.extend(chunk)
                    if len(raw) > 1_000_000:
                        raise AIError('Gemini model listing exceeded the 1 MB page limit.')
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get('models'), list):
                raise ValueError()
            for model in data['models']:
                if not isinstance(model, dict):
                    raise ValueError()
                name = model.get('name', '')
                methods = model.get('supportedGenerationMethods', [])
                if not isinstance(name, str) or not isinstance(methods, list):
                    raise ValueError()
                match = MODEL_ID.fullmatch(name)
                if match and 'generateContent' in methods:
                    if api_key in name:
                        raise AIError('Gemini returned an unsafe model identifier; nothing will be displayed.')
                    models.add(match.group(1))
            token = data.get('nextPageToken', '')
            if not isinstance(token, str) or len(token) > 4096 or token in seen_tokens:
                raise ValueError()
            if not token:
                return sorted(models)
            seen_tokens.add(token)
        except requests.RequestException:
            raise AIError('Gemini model listing connection failed; check network access and TLS configuration.') from None
        except (ValueError, TypeError):
            raise AIError('Gemini returned an invalid model listing. Nothing will be displayed.') from None
    raise AIError('Gemini model listing exceeded the ten-page limit; check the provider console.')


def _model_id(api_key, model):
    if not api_key:
        raise AIError('Configure the repository secret AI_API_KEY before checking Gemini access.')
    model = model.removeprefix('models/')
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,200}', model) or api_key in model:
        raise AIError('Configure AI_MODEL with an exact model ID before checking Gemini access.')
    return model


def check_model(api_key, model):
    """Fetch this exact model's metadata; successful generation is a separate test."""
    model = _model_id(api_key, model)
    try:
        with requests.get(f'{CATALOG_URL}/{model}', headers={'x-goog-api-key': api_key},
                          timeout=(10, 30), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                error = _http_error(response.status_code, 1, response)
                if response.status_code == 404:
                    raise AIError(f'Gemini model preflight failed: model not found: {model}. {error}')
                raise error
            raw = bytearray()
            for chunk in response.iter_content(65536):
                raw.extend(chunk)
                if len(raw) > 1_000_000:
                    raise AIError('Gemini model metadata exceeded the 1 MB limit.')
        data = json.loads(raw)
        if data['name'] != 'models/' + model or not isinstance(data['supportedGenerationMethods'], list):
            raise ValueError()
        if 'generateContent' not in data['supportedGenerationMethods']:
            raise AIError(f'Gemini model preflight failed: {model} does not advertise generateContent.')
    except requests.RequestException:
        raise AIError('Gemini model preflight connection failed; check network access and TLS configuration.') from None
    except (ValueError, TypeError, KeyError):
        raise AIError('Gemini returned invalid model metadata. Raw response text is hidden.') from None


def test_generation(api_key, model):
    """Make one small JSON generation request, only when explicitly requested."""
    model = _model_id(api_key, model)
    url = f'{CATALOG_URL}/{model}:generateContent'
    body = {
        'systemInstruction': {'parts': [{'text': 'Return only valid JSON.'}]},
        'contents': [{'role': 'user', 'parts': [{'text': 'Return a JSON object containing exactly one property: "ok": true.'}]}],
        'generationConfig': {'responseMimeType': 'application/json', 'maxOutputTokens': 2048},
    }
    try:
        raw = _request_bytes(url, {'x-goog-api-key': api_key}, body, max_attempts=1)
        data = json.loads(raw)
        candidate = data['candidates'][0]
        if candidate.get('finishReason') != 'STOP':
            raise AIError('Gemini accepted the generation request but did not finish the small JSON test successfully. Check model restrictions and output limits.')
        text = ''.join(part.get('text', '') for part in candidate['content']['parts'] if not part.get('thought'))
        if json.loads(text) != {'ok': True}:
            raise ValueError()
    except requests.RequestException:
        raise AIError('Gemini generation test connection failed; check network access and TLS configuration. No retry was attempted.') from None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise AIError('Gemini accepted the generation request but returned an invalid JSON test result. Raw response text is hidden.') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-generation', action='store_true', help='Make one minimal JSON generation request; consumes API quota.')
    args = parser.parse_args(argv)
    key = (os.getenv('AI_API_KEY') or '').strip()
    configured = (os.getenv('AI_MODEL') or '').strip().removeprefix('models/')
    try:
        if args.test_generation:
            test_generation(key, configured)
            lines = [
                'Gemini minimal JSON generation test passed.',
                'The selected model accepted one small generation request and returned the expected JSON.',
                'This does not verify the full report, its token budget, or Discord delivery.',
            ]
        else:
            models = available_models(key)
            if not models:
                raise AIError('No Gemini models advertising generateContent were returned for this key.')
            lines = ['Gemini models advertising generateContent:', *[f'- {model}' for model in models]]
            if configured in models:
                lines.append('The configured AI_MODEL is listed. Generation can still fail because of overload, quota, or account restrictions.')
            else:
                lines.append('The configured AI_MODEL is not listed. Set the repository variable AI_MODEL to one of the exact IDs above.')
            lines.append('Choose a text model with JSON support; image/audio variants are not suitable for this report workflow.')
            lines.append('This check lists models only; it does not verify free-tier eligibility or successful report generation.')
    except AIError as error:
        raise SystemExit(str(error)) from None
    text = '\n'.join(lines) + '\n'
    print(text, end='')
    summary = os.getenv('GITHUB_STEP_SUMMARY')
    if summary:
        with Path(summary).open('a', encoding='utf-8') as output:
            output.write(text)


if __name__ == '__main__':
    main()
