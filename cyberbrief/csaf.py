"""CISA's official machine-readable advisory repository, checksum verified."""
import csv
import hashlib
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import requests
from .models import Story
from .details import excerpt, normalize

BASE = 'https://raw.githubusercontent.com/cisagov/CSAF/develop/csaf_files'


def parse_document(data, url):
    doc = data['document']
    date = datetime.fromisoformat(doc['tracking']['current_release_date'].replace('Z', '+00:00'))
    if date.tzinfo is None:
        raise ValueError('CSAF release date has no timezone')
    notes = doc.get('notes', [])
    vulnerabilities = data.get('vulnerabilities', [])
    summary = ' '.join(n['text'] for n in notes if n.get('category') == 'summary')
    products = {}
    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get('product'), dict):
                p = value['product']; products[p['product_id']] = p['name']
            for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(data.get('product_tree', {}))
    affected, fixes, workarounds, exploitation = [], [], [], []
    for vulnerability in vulnerabilities:
        for pid in vulnerability.get('product_status', {}).get('known_affected', []):
            if pid in products: affected.append(products[pid])
        for remediation in vulnerability.get('remediations', []):
            target = workarounds if remediation.get('category') in ('workaround', 'mitigation') else fixes
            if remediation.get('details'): target.append(remediation['details'])
        for note in vulnerability.get('notes', []):
            if re.search(r'exploit', note.get('text', ''), re.I): exploitation.append(note['text'])
    for note in notes:
        if re.search(r'known public exploitation|actively exploited|malicious use', note.get('text', ''), re.I):
            exploitation.append(note['text'])
    fields = {}
    for name, values in [('affected', affected), ('remediation', fixes), ('workarounds', workarounds), ('exploitation', exploitation)]:
        if values:
            quote, truncated = excerpt(normalize(' | '.join(dict.fromkeys(values))))
            fields[name] = {'text': quote, 'quote': quote, 'url': url, 'truncated': truncated}
    detail = {'status': 'csaf', 'url': url, 'fields': fields, 'summary': excerpt(normalize(summary), 160)[0]}
    return Story(doc['title'], url, 'CISA', 0, date, normalize(summary), kind='primary',
                 cves=sorted({v['cve'] for v in vulnerabilities if v.get('cve')}),
                 evidence=[{'source': 'CISA official CSAF', 'url': url, 'tier': 0}], details=detail)


def verified_document(url, timeout=12):
    response = requests.get(url, timeout=timeout); response.raise_for_status()
    checksum = requests.get(url + '.sha512', timeout=timeout); checksum.raise_for_status()
    fields = checksum.text.split()
    if not fields:
        raise ValueError('CISA CSAF checksum file is empty')
    expected = fields[0]
    if not re.fullmatch('[0-9a-fA-F]{128}', expected) or hashlib.sha512(response.content).hexdigest() != expected.lower():
        raise ValueError('CISA CSAF SHA-512 verification failed')
    return parse_document(json.loads(response.content), url)


def fetch_cisa_csaf(timeout=12, since=None):
    candidates, errors = set(), []
    now = datetime.now(timezone.utc)
    for scope in ('IT', 'OT', 'VA'):
        try:
            root = f'{BASE}/{scope}/white'
            response = requests.get(root + '/changes.csv', timeout=timeout); response.raise_for_status()
            rows = list(csv.reader(io.StringIO(response.text)))
            if not rows:
                raise ValueError('CISA CSAF change index is empty')
            parsed = []
            for row in rows:
                if len(row) != 2 or not re.fullmatch(r'\d{4}/[a-z0-9-]+\.json', row[0]):
                    continue
                date = datetime.fromisoformat(row[1].replace('Z', '+00:00'))
                if date.tzinfo is not None and date <= now:
                    parsed.append((date, row[0]))
            if not parsed:
                raise ValueError('No valid dated CSAF index records')
            parsed.sort(reverse=True)
            # All recent updates (bounded) plus two latest records for readiness.
            chosen = [p for p in parsed if p[0] >= (since or now-timedelta(days=8))] or parsed[:2]
            for _, path in chosen[:30]: candidates.add(root + '/' + path)
        except (requests.RequestException, ValueError) as exc:
            errors.append({'source': f'CISA CSAF {scope}', 'error': str(exc)[:250]})
    def fetch(url):
        try: return verified_document(url, timeout), None
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            return None, {'source': 'CISA CSAF', 'error': str(exc)[:250]}
    stories = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for story, error in pool.map(fetch, sorted(candidates)):
            if error: errors.append(error)
            elif story: stories.append(story)
    return stories, errors
