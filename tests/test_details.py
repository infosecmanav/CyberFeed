from datetime import datetime, timezone, timedelta
import hashlib
import json
from unittest.mock import Mock
import pytest
from cyberbrief.details import parse_article, retrieve, parse_msrc, enrich
from cyberbrief.csaf import parse_document, verified_document
from cyberbrief.engine import select_stories, build
from cyberbrief.models import Story


def test_cisco_detail_sections_preserve_conditions_and_remediation():
    html = '''<html><body><nav>Ignore navigation</nav><div id="advisorycontentbody">
    <div id="summaryfield">A network flaw could allow code execution. User interaction is required.</div>
    <div id="vulnerableproducts">Switch version 4.1 when NX-API is enabled.</div>
    <div id="fixedsoftfield">Upgrade to 4.2. <table><tr><td>4.1</td><td>4.2</td></tr></table></div>
    <div id="workaroundsfield">Disable the feature temporarily.</div>
    <div id="exploitfield">No known malicious use has been reported.</div></div></body></html>'''
    d = parse_article(html, 'https://sec.cloudapps.cisco.com/advisory')
    assert 'User interaction is required' in d['summary']
    assert 'when NX-API is enabled' in d['fields']['affected']['text']
    assert 'Upgrade to 4.2' in d['fields']['remediation']['text']
    for field in d['fields'].values():
        assert field['text'] == field['quote'] and field['quote'] in d['source_text']
    assert 'Ignore navigation' not in d['source_text']


def test_article_missing_fields_are_not_guessed_and_script_is_not_evidence():
    html = '<article><h1>Security update</h1><p>' + 'Researchers report a campaign. ' * 8 + '</p><script>All releases are safe.</script></article>'
    d = parse_article(html, 'https://therecord.media/update')
    assert d['fields'] == {}
    assert 'All releases are safe' not in d['source_text']
    with pytest.raises(ValueError):
        parse_article('<html><body>Access denied</body></html>', 'https://therecord.media/update')


@pytest.mark.parametrize('url', ['http://therecord.media/a', 'https://127.0.0.1/a', 'https://therecord.media.evil.example/a', 'https://name:secret@therecord.media/a', 'https://therecord.media:444/a'])
def test_retrieval_rejects_untrusted_urls_before_network(url, monkeypatch):
    called = Mock()
    monkeypatch.setattr('cyberbrief.details.requests.get', called)
    with pytest.raises(ValueError): retrieve(url)
    called.assert_not_called()


def csaf_document():
    return {'document': {'title': 'Vendor controller advisory', 'tracking': {'current_release_date': '2026-10-08T06:00:00Z'}, 'notes': [{'category': 'summary', 'text': 'A flaw allows remote code execution.'}, {'category': 'general', 'text': 'No known public exploitation has been reported.'}]}, 'product_tree': {'branches': [{'product': {'name': 'Controller < 2.1', 'product_id': 'p1'}}]}, 'vulnerabilities': [{'cve': 'CVE-2026-12345', 'product_status': {'known_affected': ['p1']}, 'remediations': [{'category': 'vendor_fix', 'details': 'Upgrade to 2.1.'}, {'category': 'workaround', 'details': 'Restrict network access.'}]}]}


def test_csaf_product_versions_and_fixes_are_resolved():
    s = parse_document(csaf_document(), 'https://raw.githubusercontent.com/cisagov/CSAF/a.json')
    assert s.source == 'CISA' and s.cves == ['CVE-2026-12345']
    assert s.details['fields']['affected']['text'] == 'Controller < 2.1'
    assert s.details['fields']['remediation']['text'] == 'Upgrade to 2.1.'
    assert 'No known public exploitation' in s.details['fields']['exploitation']['text']


def test_csaf_checksum_mismatch_fails_closed(monkeypatch):
    raw = json.dumps(csaf_document()).encode()
    document = Mock(content=raw)
    checksum = Mock(text='0' * 128 + '  a.json')
    monkeypatch.setattr('cyberbrief.csaf.requests.get', Mock(side_effect=[document, checksum]))
    with pytest.raises(ValueError, match='SHA-512'):
        verified_document('https://raw.githubusercontent.com/cisagov/CSAF/a.json')
    checksum.text = hashlib.sha512(raw).hexdigest() + '  a.json'
    monkeypatch.setattr('cyberbrief.csaf.requests.get', Mock(side_effect=[document, checksum]))
    assert verified_document('https://raw.githubusercontent.com/cisagov/CSAF/a.json').source == 'CISA'


def test_msrc_preserves_product_build_and_exploitation_context():
    data = {'ProductTree': {'Branch': [{'Items': [{'ProductID': 'p1', 'Value': 'Windows Server 2025'}]}]}}
    v = {'Notes': [{'Type': 2, 'Value': '<p>Opening a crafted file can execute code.</p>'}], 'ProductStatuses': [{'Type': 3, 'ProductID': ['p1']}], 'Remediations': [{'Type': 2, 'Description': {'Value': 'KB123'}, 'FixedBuild': '10.0.123', 'URL': 'https://vendor.example/fix', 'ProductID': ['p1']}], 'Threats': [{'Type': 1, 'Description': {'Value': 'Exploitation Detected:No'}}]}
    d = parse_msrc(data, v, 'https://api.msrc.microsoft.com/cvrf')
    assert d['fields']['affected']['text'] == 'Windows Server 2025'
    assert '10.0.123' in d['fields']['remediation']['text']
    assert 'Windows Server 2025' in d['fields']['remediation']['text']
    assert 'Detected:No' in d['fields']['exploitation']['text']
    assert d['summary'] == 'Opening a crafted file can execute code.'


def test_ranked_source_rounds_prevent_vendor_burst_domination():
    stories = []
    for source, count, score in [('Cisco', 25, 90), ('CISA', 4, 60), ('Research', 4, 40), ('Reporting', 4, 20)]:
        for n in range(count):
            s = Story(f'{source} {n}', 'https://example.invalid', source, 0, datetime.now(timezone.utc));s.score = score-n;stories.append(s)
    selected = select_stories(stories)
    assert len(selected) == 10
    assert {s.source for s in selected[:4]} == {'Cisco', 'CISA', 'Research', 'Reporting'}
    assert max(sum(s.source == source for s in selected) for source in {s.source for s in selected}) <= 3


def test_fixture_details_do_not_fetch_and_invalid_period_fails(monkeypatch):
    get = Mock()
    monkeypatch.setattr('cyberbrief.details.requests.get', get)
    d = build(fixtures=True)
    assert all(s['details']['status'] == 'fixture' for s in d['stories'])
    get.assert_not_called()
    with pytest.raises(ValueError): build(period='monthly', fixtures=True)


def test_generic_advisory_uses_details_not_audience_boilerplate():
    html = '''<main><h1>Alert</h1><h2>Audience</h2><p>This alert is intended for IT professionals and managers.</p>
    <h2>Details</h2><p>A flaw affects appliance 3.1 and is actively exploited.</p>
    <h2>Suggested actions</h2><p>Upgrade to appliance 3.2. Preserve forensic evidence before rebooting.</p>
    <h2>References</h2><p>Unrelated reference.</p></main>'''
    d = parse_article(html, 'https://cyber.gc.ca/en/alert')
    assert d['summary'] == 'A flaw affects appliance 3.1 and is actively exploited.'
    assert d['fields']['remediation']['text'] == 'Upgrade to appliance 3.2. Preserve forensic evidence before rebooting.'
    assert 'Unrelated' not in d['fields']['remediation']['text']
    assert d['fields']['affected']['text'].endswith('actively exploited.')


def test_redirect_cannot_escape_supported_publishers(monkeypatch):
    response = Mock(status_code=302, headers={'Location': 'https://127.0.0.1/internal'})
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    get = Mock(return_value=response)
    monkeypatch.setattr('cyberbrief.details.requests.get', get)
    with pytest.raises(ValueError): retrieve('https://therecord.media/redirect')
    assert get.call_count == 1


def test_msrc_lookup_follows_cve_release_not_feed_revision_month(monkeypatch):
    from cyberbrief.details import msrc_details
    doc = {'Vulnerability': [{'CVE': 'CVE-2026-12345', 'Notes': [{'Type': 2, 'Value': 'Original description.'}]}]}
    lookup = Mock(side_effect=[{'value': [{'ID': '2026-Sep'}]}, doc])
    monkeypatch.setattr('cyberbrief.details.json_request', lookup)
    story = Story('October revision', 'https://msrc.microsoft.com/update', 'Microsoft MSRC', 0, datetime.now(timezone.utc), cves=['CVE-2026-12345'])
    result = msrc_details(story)
    assert result['status'] == 'cvrf' and result['summary'] == 'Original description.'
    assert lookup.call_args_list[1].args[0].endswith('2026-Sep')


def test_full_detail_failure_is_reported_not_silently_ignored(monkeypatch):
    from cyberbrief import engine
    story = Story('Current alert', 'https://therecord.media/alert', 'Publisher', 1, datetime.now(timezone.utc), 'Source excerpt')
    monkeypatch.setattr(engine, 'fetch_feeds', lambda sources: ([story], []))
    monkeypatch.setattr(engine, 'fetch_cisa_kev', lambda: {})
    monkeypatch.setattr(engine, 'enrich', lambda s, fixtures: {'status': 'unavailable', 'url': s.url, 'fields': {}, 'summary': s.summary, 'error': 'Article failed'})
    data = engine.build()
    assert data['errors'][0]['error'] == 'Article failed'
    assert data['stories'][0]['summary'] == 'Source excerpt'


def test_substantive_vendor_advisory_beats_routine_release_burst():
    now = datetime.now(timezone.utc)
    routine = Story('Cisco Security Hardening Release', 'https://example.invalid/routine', 'Cisco', 0, now);routine.score = 100
    substantive = Story('Cisco NX-API Remote Code Execution Vulnerability', 'https://example.invalid/advisory', 'Cisco', 0, now);substantive.score = 60
    assert select_stories([routine, substantive], limit=1)[0] is substantive
    routine.kev = True
    assert select_stories([routine, substantive], limit=1)[0] is routine


def test_cli_exports_all_formats_and_strict_failure_has_nonzero_status(tmp_path, monkeypatch):
    from cyberbrief import cli
    data = build(fixtures=True)
    monkeypatch.setattr(cli, 'build', lambda *a: data)
    monkeypatch.setattr('sys.argv', ['cli', 'build', '--source-only', '--format', 'all', '--strict', '--out', str(tmp_path)])
    cli.main()
    assert (tmp_path / 'briefing.pdf').read_bytes().startswith(b'%PDF-')
    assert json.loads((tmp_path / 'briefing.json').read_text())['demo']
    assert 'Fictional test story' in (tmp_path / 'briefing.txt').read_text()
    data['errors'] = [{'source': 'Feed', 'error': 'Failure'}]
    with pytest.raises(SystemExit) as exc: cli.main()
    assert exc.value.code == 2


def test_fortinet_version_table_workaround_and_revision_date():
    html = '''<main><h2>Summary</h2><p>A flaw allows an unauthenticated attacker to write arbitrary files. This has been reported to be exploited in the wild.</p>
    <table><thead><tr><th>Version</th><th>Affected</th><th>Solution</th></tr></thead><tbody><tr><td>FortiMail 8.0</td><td>8.0.0 through 8.0.1</td><td>Upgrade to 8.0.2</td></tr></tbody></table>
    <p><strong>Workaround:</strong></p><p>Disable the IBE feature.</p><p><strong>Indicators of Compromise:</strong></p><p>IP addresses omitted.</p>
    <h2>Timeline</h2><p>October update.</p><table><tr><td>Updated Date</td><td>Oct 7, 2026</td></tr></table></main>'''
    d = parse_article(html, 'https://www.fortiguard.com/psirt/FG-IR-26-175')
    assert '8.0.0 through 8.0.1' in d['fields']['affected']['text']
    assert 'Upgrade to 8.0.2' in d['fields']['remediation']['text']
    assert d['fields']['workarounds']['text'] == 'Disable the IBE feature.'
    assert d['updated_at'].startswith('2026-10-07') and d['updated_precision'] == 'day'


def test_fortinet_revision_includes_older_publication_in_weekly_window(monkeypatch):
    from cyberbrief import engine
    now = datetime.now(timezone.utc)
    story = Story('Revised vendor advisory', 'https://www.fortiguard.com/psirt/FG-IR-26-175', 'Fortinet PSIRT', 0, now-timedelta(days=12), 'Old feed excerpt')
    detail = {'status': 'article', 'url': story.url, 'fields': {}, 'summary': 'Updated advisory excerpt', 'updated_at': (now-timedelta(days=2)).isoformat(), 'updated_precision': 'day', 'cves': ['CVE-2026-12345']}
    monkeypatch.setattr(engine, 'fetch_feeds', lambda sources: ([story], []))
    monkeypatch.setattr(engine, 'fetch_cisa_kev', lambda: {})
    monkeypatch.setattr(engine, 'enrich', lambda *a: detail)
    data = engine.build(period='weekly')
    assert not data['errors'] and len(data['stories']) == 1
    assert data['stories'][0]['published'] == story.published.isoformat()
    assert 'CVE-2026-12345' in data['stories'][0]['cves']
    assert data['stories'][0]['details']['updated_at'] == detail['updated_at']
