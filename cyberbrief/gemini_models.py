"""Read-only Gemini model discovery; never generates content or posts reports."""
import json
import os
from pathlib import Path
import re

import requests

from .ai import AIError

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


def main():
    key = (os.getenv('AI_API_KEY') or '').strip()
    try:
        models = available_models(key)
        if not models:
            raise AIError('No Gemini models advertising generateContent were returned for this key.')
    except AIError as error:
        raise SystemExit(str(error)) from None
    configured = (os.getenv('AI_MODEL') or '').strip().removeprefix('models/')
    lines = ['Gemini models advertising generateContent:', *[f'- {model}' for model in models]]
    if configured in models:
        lines.append('The configured AI_MODEL is listed. Generation can still fail because of overload, quota, or account restrictions.')
    else:
        lines.append('The configured AI_MODEL is not listed. Set the repository variable AI_MODEL to one of the exact IDs above.')
    lines.append('Choose a text model with JSON support; image/audio variants are not suitable for this report workflow.')
    lines.append('This check lists models only; it does not verify free-tier eligibility or successful report generation.')
    text = '\n'.join(lines) + '\n'
    print(text, end='')
    summary = os.getenv('GITHUB_STEP_SUMMARY')
    if summary:
        with Path(summary).open('a', encoding='utf-8') as output:
            output.write(text)


if __name__ == '__main__':
    main()
