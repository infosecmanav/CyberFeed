"""Bounded retrieval and attributed extraction; remote text is never instructions."""
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup

ARTICLE_HOSTS = frozenset({
    'www.cisa.gov', 'cyber.gc.ca', 'www.cyber.gc.ca', 'msrc.microsoft.com',
    'api.msrc.microsoft.com', 'blog.talosintelligence.com', 'sec.cloudapps.cisco.com',
    'tools.cisco.com', 'www.fortiguard.com', 'filestore.fortinet.com', 'fortiguard.fortinet.com', 'cloud.google.com',
    'therecord.media', 'www.bleepingcomputer.com', 'thehackernews.com',
    'raw.githubusercontent.com',
})
FIELDS = ('affected', 'remediation', 'workarounds', 'exploitation')
CISCO_IDS = {'affected': 'vulnerableproducts', 'remediation': 'fixedsoftfield',
             'workarounds': 'workaroundsfield', 'exploitation': 'exploitfield'}
LABELS = {
    'affected': r'affected|vulnerable products|impacted products',
    'remediation': r'fixed software|remediation|recommendations|mitigations|solution|suggested actions',
    'workarounds': r'workarounds?',
    'exploitation': r'exploitation|exploits? and public|threat activity',
}


def normalize(value):
    return ' '.join(value.split())


def excerpt(text, limit=300):
    # Keep quotations contiguous and mark truncation separately.
    words = text.split()
    return ' '.join(words[:limit]), len(words) > limit


def retrieve(url, timeout=12):
    """Only known publishers, including redirect targets; no arbitrary URLs."""
    for _ in range(4):
        parts = urlparse(url)
        if parts.scheme != 'https' or parts.hostname not in ARTICLE_HOSTS or parts.username or parts.password or parts.port not in (None, 443):
            raise ValueError('Article URL is outside supported HTTPS publisher hosts')
        with requests.get(url, timeout=timeout, allow_redirects=False, stream=True,
                          headers={'User-Agent': 'CyberBrief/0.2 (+public security research)'}) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers['Location'])
                continue
            response.raise_for_status()
            raw = bytearray()
            for chunk in response.iter_content(65536):
                raw.extend(chunk)
                if len(raw) > 3_000_000:
                    raise ValueError('Article exceeds 3 MB retrieval limit')
            if 'html' not in response.headers.get('Content-Type', '').lower():
                raise ValueError('Article did not return HTML')
            return bytes(raw), url
    raise ValueError('Too many article redirects')


def heading_text(main, pattern):
    heading = next((h for h in main.find_all(re.compile('^h[2-6]$')) if re.search(pattern, h.get_text(' ', strip=True), re.I)), None)
    if not heading:
        heading = next((h for h in main.find_all(['strong', 'b']) if len(h.get_text()) < 80 and re.search(pattern, h.get_text(' ', strip=True), re.I)), None)
        if heading and heading.parent.name == 'p':
            heading = heading.parent
    if not heading:
        return ''
    pieces = []
    for sibling in heading.next_siblings:
        if getattr(sibling, 'name', '') and re.match('^h[1-6]$', sibling.name):
            break
        if getattr(sibling, 'get_text', None):
            label = sibling.find(['strong', 'b']) if getattr(sibling, 'name', None) else None
            if label and re.match(r'^(indicators|acknowledg|timeline|references)', label.get_text(' ', strip=True), re.I):
                break
            pieces.append(sibling.get_text(' ', strip=True))
    return normalize(' '.join(pieces))


def parse_article(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    for element in soup.select('script, style, nav, footer, header, form, noscript, .cookie-banner'):
        element.decompose()
    main = (soup.find(id='advisorycontentbody') or soup.find('article') or soup.find('main') or soup.body or soup)
    text = normalize(main.get_text(' ', strip=True))
    if len(text) < 80 or (not soup.find('article') and not soup.find(id='advisorycontentbody') and re.search(r'enable javascript|access denied|checking your browser', text[:700], re.I)):
        raise ValueError('Source did not provide a readable article')
    result = {}
    for field in FIELDS:
        node = main.find(id=CISCO_IDS[field])
        content = normalize(node.get_text(' ', strip=True)) if node else ''
        if not content and field in ('affected', 'remediation'):
            table = next((t for t in main.find_all('table') if {'version', 'affected', 'solution'}.issubset({th.get_text(' ', strip=True).lower() for th in t.find_all('th')})), None)
            if table:
                content = normalize(table.get_text(' ', strip=True))
        if not content:
            content = heading_text(main, LABELS[field])
        if not content:
            terms = {
                'affected': r'affects?|affected|vulnerable (?:version|release|product)|versions? (?:before|prior)',
                'remediation': r'upgrade to|update to|install (?:the )?patch',
                'workarounds': r'workaround|temporary mitigation',
                'exploitation': r'actively exploited|active exploitation|no known (?:public )?exploitation|not aware of.*malicious|exploited in the wild',
            }
            content = next((normalize(p.get_text(' ', strip=True)) for p in main.find_all('p') if re.search(terms[field], p.get_text(' ', strip=True), re.I)), '')
        if content:
            quote, truncated = excerpt(content)
            result[field] = {'text': quote, 'quote': quote, 'url': url, 'truncated': truncated}
    summary_node = main.find(id='summaryfield')
    summary = normalize(summary_node.get_text(' ', strip=True)) if summary_node else heading_text(main, r'^(details|summary|overview|description|executive summary)$')
    if not summary:
        paragraphs = [normalize(p.get_text(' ', strip=True)) for p in main.find_all('p')]
        summary = next((p for p in paragraphs if len(p) >= 100), '')
    detail = {'status': 'article', 'url': url, 'fields': result, 'summary': excerpt(summary, 160)[0], 'source_text': text,
              'cves': sorted({cve.upper() for cve in re.findall(r'\bCVE-\d{4}-\d{4,7}\b', text, re.I)})}
    if urlparse(url).hostname == 'www.fortiguard.com':
        for row in main.find_all('tr'):
            cells = row.find_all('td')
            if len(cells) == 2 and cells[0].get_text(strip=True) == 'Updated Date':
                updated = datetime.strptime(cells[1].get_text(' ', strip=True), '%b %d, %Y').replace(tzinfo=timezone.utc)
                detail['updated_at'] = updated.isoformat()
                detail['updated_precision'] = 'day'
    return detail


def enrich(story, fixtures=False):
    if story.details:
        return story.details
    if fixtures:
        return {'status': 'fixture', 'url': story.url, 'fields': {}, 'summary': story.summary}
    try:
        if story.source == 'Microsoft MSRC':
            return msrc_details(story)
        url = story.url
        if story.source == 'Fortinet PSIRT':
            advisory = re.search(r'/psirt/(?:cvrf/)?(FG-IR-\d{2}-\d+)$', urlparse(url).path)
            if advisory:
                url = 'https://www.fortiguard.com/psirt/' + advisory.group(1)
        html, url = retrieve(url)
        return parse_article(html, url)
    except (requests.RequestException, ValueError, KeyError) as exc:
        # No invented details or source substitution on failure.
        return {'status': 'unavailable', 'url': story.url, 'fields': {}, 'summary': story.summary,
                'error': f'Full advisory unavailable ({type(exc).__name__}); feed excerpt only'}


def json_request(url):
    with requests.get(url, timeout=12, headers={'Accept': 'application/json'}, stream=True, allow_redirects=False) as response:
        response.raise_for_status()
        raw = bytearray()
        for chunk in response.iter_content(65536):
            raw.extend(chunk)
            if len(raw) > 25_000_000:
                raise ValueError('Structured advisory exceeds 25 MB limit')
        import json
        return json.loads(raw)


def msrc_details(story):
    # Use the public CVRF API: the update-guide webpage needs JavaScript.
    cve = next((c for c in story.cves if re.fullmatch(r'CVE-\d{4}-\d{4,7}', c)), None)
    if not cve:
        raise ValueError('No CVE identifier for MSRC advisory lookup')
    index = json_request(f"https://api.msrc.microsoft.com/cvrf/v3.0/updates('{cve}')")
    docs = index.get('value', [])
    for update in docs[:4]:
        release = update.get('ID', '')
        if not re.fullmatch(r'\d{4}-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', release):
            continue
        url = f'https://api.msrc.microsoft.com/cvrf/v3.0/cvrf/{release}'
        data = json_request(url)
        vulnerability = next((v for v in data.get('Vulnerability', []) if v.get('CVE') == cve), None)
        if vulnerability:
            return parse_msrc(data, vulnerability, url, story.summary)
    raise ValueError('MSRC CVRF lookup did not contain the requested CVE')


def parse_msrc(data, vulnerability, url, feed_summary=''):
    def plain(value):
        if isinstance(value, dict): value = value.get('Value', '')
        return normalize(BeautifulSoup(str(value or ''), 'html.parser').get_text(' ', strip=True))
    products = {}
    def visit(value):
        if isinstance(value, dict):
            if 'ProductID' in value and 'Value' in value:
                products[value['ProductID']] = plain(value['Value'])
            for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(data.get('ProductTree', {}))
    affected = [products[pid] for status in vulnerability.get('ProductStatuses', []) if status.get('Type') == 3 for pid in status.get('ProductID', []) if pid in products]
    fixes, workarounds = [], []
    for remediation in vulnerability.get('Remediations', []):
        parts = [plain(remediation.get('Description', ''))]
        if remediation.get('FixedBuild'): parts.append('Fixed build: ' + remediation['FixedBuild'])
        if remediation.get('URL'): parts.append(remediation['URL'])
        names = [products[pid] for pid in remediation.get('ProductID', []) if pid in products]
        if names: parts.append('Products: ' + ', '.join(names))
        content = ' | '.join(p for p in parts if p)
        if remediation.get('Type') == 2: fixes.append(content)
        elif remediation.get('Type') in (0, 1): workarounds.append(content)
    exploitation = [plain(t.get('Description', '')) for t in vulnerability.get('Threats', []) if t.get('Type') == 1]
    fields = {}
    for name, values in [('affected', affected), ('remediation', fixes), ('workarounds', workarounds), ('exploitation', exploitation)]:
        values = [v for v in values if v]
        if values:
            quote, truncated = excerpt(' | '.join(dict.fromkeys(values)), 300)
            fields[name] = {'text': quote, 'quote': quote, 'url': url, 'truncated': truncated}
    summaries = [plain(n.get('Value', '')) for n in vulnerability.get('Notes', []) if n.get('Type') == 2]
    summary = next((s for s in summaries if s), feed_summary)
    return {'status': 'cvrf', 'url': url, 'fields': fields, 'summary': excerpt(summary, 160)[0]}
