from datetime import datetime, timezone, timedelta
from cyberbrief.models import Story
from cyberbrief.verify import cluster_stories, score_story, verify_story
from cyberbrief.ingest import fixture_stories


def test_parse_rss_and_atom_publication_dates():
    import pytest
    from cyberbrief.ingest import parse_date
    expected = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    for value in ("Sun, 11 Oct 2026 12:00:00 GMT", "2026-10-11T12:00:00Z", "2026-10-11T08:00:00-04:00"):
        assert parse_date(value) == expected
    with pytest.raises(ValueError):
        parse_date("invalid")


def story(title, source, url, cves=None, tier=2, hours=1):
    return Story(title,url,source,tier,datetime.now(timezone.utc)-timedelta(hours=hours),"test summary",cves=cves or [],evidence=[{"source":source,"url":url,"tier":tier}])


def test_cluster_same_cve_and_keep_evidence():
    a=story("CVE-2026-12345 exploited", "Outlet A", "https://a.example/x", ["CVE-2026-12345"])
    b=story("Vendor patch CVE-2026-12345", "Vendor", "https://b.example/y", ["CVE-2026-12345"], tier=0)
    result=cluster_stories([a,b])
    assert len(result)==1
    assert len(result[0].evidence)==2


def test_kev_signal_scores_higher():
    s=story("Advisory CVE-2026-12345", "Vendor", "https://vendor.example/advisory", ["CVE-2026-12345"], tier=0)
    score_story(s,{"CVE-2026-12345":{"cveID":"CVE-2026-12345"}})
    assert s.kev is True
    assert s.score_parts["cisa_kev"]==20
    assert s.confidence!="High"


def test_missing_evidence_is_flagged():
    s=story("A story", "Source", "https://source.example/story")
    s.evidence=[]
    assert "No evidence record" in verify_story(s)


def test_fixture_stories_are_explicitly_fictional():
    assert all("Fictional test story" in s.summary for s in fixture_stories())


def test_claim_gate_rejects_invented_or_rewritten_text():
    from cyberbrief.engine import validate_claims
    evidence={"feed":"The vendor reports a patch."}
    assert validate_claims([{"text":"Attackers exploited it.","quote":"Attackers exploited it.","evidence_id":"feed"}],evidence)==[]
    assert validate_claims([{"text":"A patch is available.","quote":"The vendor reports a patch.","evidence_id":"feed"}],evidence)==[]

def test_primary_summary_wins():
    a=story("report", "News", "https://news.example/a", ["CVE-2026-12345"])
    b=story("advisory", "Vendor", "https://vendor.example/a", ["CVE-2026-12345"],tier=0)
    a.summary="Much longer secondary text with extra allegations"
    b.summary="Primary statement"
    merged=cluster_stories([a,b])[0]
    assert merged.source=="Vendor" and merged.summary=="Primary statement"

def test_shared_engine_fixture_output_has_no_confidence():
    from cyberbrief.engine import build
    d=build(fixtures=True)
    assert d["demo"] and d["stories"]
    assert all("confidence" not in s and "score" not in s for s in d["stories"])


def test_weekly_digest_preserves_attribution_and_excerpts():
    from cyberbrief.engine import build
    daily = build(fixtures=True)
    weekly = build(period="weekly", fixtures=True)
    assert daily["weekly_digest"] == []
    assert weekly["weekly_digest"]
    updates = [u for group in weekly["weekly_digest"] for u in group["updates"]]
    assert len(updates) == len(weekly["stories"])
    for update in updates:
        original = next(s for s in weekly["stories"] if s["url"] == update["url"])
        assert update["excerpt"] == original["summary"]
        assert update["source"] == original["source"]


def test_kev_uses_official_mirror_when_cisa_site_fails(monkeypatch):
    import io, json
    from urllib.error import HTTPError
    from cyberbrief.ingest import fetch_cisa_kev
    calls = []
    def response(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 403, "Denied", {}, None)
        return io.BytesIO(json.dumps({"vulnerabilities": [{"cveID": "CVE-2026-12345"}]}).encode())
    monkeypatch.setattr("urllib.request.urlopen", response)
    assert "CVE-2026-12345" in fetch_cisa_kev()
    assert calls[1] == "https://raw.githubusercontent.com/cisagov/kev-data/main/known_exploited_vulnerabilities.json"


def test_kev_does_not_accept_an_empty_catalog(monkeypatch):
    import io, pytest
    from cyberbrief.ingest import fetch_cisa_kev
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: io.BytesIO(b'{"vulnerabilities": []}'))
    with pytest.raises(RuntimeError, match="Both official"):
        fetch_cisa_kev()
