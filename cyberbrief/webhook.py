"""Deliver an existing report through a secret Discord channel webhook."""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse
import requests


def deliver(directory, format, webhook_url):
    if format not in ('pdf', 'text'):
        raise ValueError('Choose pdf or text')
    parts = urlparse(webhook_url)
    if parts.scheme != 'https' or parts.hostname != 'discord.com' or parts.port not in (None, 443) or parts.username or parts.password or not parts.path.startswith('/api/webhooks/') or len(parts.path.split('/')) != 5:
        raise ValueError('Configure a valid HTTPS discord.com webhook URL')
    directory = Path(directory)
    data = json.loads((directory / 'briefing.json').read_text(encoding='utf-8'))
    if data.get('demo'):
        raise ValueError('Fictional fixture reports cannot be posted by this command')
    if data.get('ai',{}).get('status') != 'validated' or not data.get('editorial'):
        raise ValueError('Only validated AI reports can be delivered. Generate the report with AI enabled first.')
    from .editorial import validate
    validate(data['editorial']['evidence'],data['editorial']['report'])
    extension = 'pdf' if format == 'pdf' else 'txt'
    report = directory / f'briefing.{extension}'
    content = f"Cyber Feed — {data['period']} cyber briefing: {len(data['stories'])} updates."
    if data['errors']:
        content += f" {len(data['errors'])} coverage warnings; see the report."
    filename = f"cyber-feed-{data['period']}-{data['generated'][:10]}.{extension}"
    # Never log request exceptions: their URLs contain webhook credentials.
    try:
        with report.open('rb') as stream:
            response = requests.post(webhook_url, params={'wait': 'true'},
                data={'payload_json': json.dumps({'content': content, 'allowed_mentions': {'parse': []}})},
                files={'files[0]': (filename, stream, 'application/pdf' if format == 'pdf' else 'text/plain')},
                timeout=(10, 60), allow_redirects=False)
    except requests.RequestException:
        raise RuntimeError('Discord webhook request failed. Check network access; delivery may be uncertain. No automatic retry was attempted.') from None
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f'Discord webhook delivery failed (HTTP {response.status_code}). Check webhook validity, permissions, or rate limits.')
    return filename


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='site')
    parser.add_argument('--format', choices=['pdf', 'text'], default='pdf')
    args = parser.parse_args()
    url = os.getenv('DISCORD_WEBHOOK_URL')
    if not url:
        raise SystemExit('Configure DISCORD_WEBHOOK_URL in GitHub Actions secrets.')
    try:
        filename = deliver(args.out, args.format, url)
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(str(exc)) from None
    print('Delivered ' + filename + ' to Discord.')


if __name__ == '__main__':
    main()
