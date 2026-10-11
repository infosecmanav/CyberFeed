from datetime import datetime, timezone, timedelta
from cyberbrief.models import Story
from cyberbrief.verify import cluster_stories, score_story, verify_story
from cyberbrief.ingest import fixture_stories


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
