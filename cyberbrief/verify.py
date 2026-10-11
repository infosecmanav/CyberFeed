from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse


def cluster_stories(stories):
    """Merge same-CVE stories first, then normalized-title duplicates."""
    buckets = {}
    for story in sorted(stories, key=lambda s: (s.tier, -s.published.timestamp())):
        title_key = " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in story.title).split())
        key = "cve:" + ",".join(story.cves) if story.cves else "title:" + title_key[:140]
        if key not in buckets:
            buckets[key] = story
        else:
            lead = buckets[key]
            known = {(e.get("source"), e.get("url")) for e in lead.evidence}
            for evidence in story.evidence:
                if (evidence.get("source"), evidence.get("url")) not in known:
                    lead.evidence.append(evidence)
            if story.url != lead.url and story.url not in [e.get("url") for e in lead.evidence]:
                lead.evidence.append({"source": story.source, "url": story.url, "tier": story.tier})
            lead.cves = sorted(set(lead.cves + story.cves))
    return list(buckets.values())


def score_story(story, kev=None, now=None):
    now = now or datetime.now(timezone.utc)
    kev = kev or {}
    age_hours = max(0, (now - story.published).total_seconds() / 3600)
    recency = max(0, 20 * (1 - age_hours / (24 * 14)))
    tier_score = {0: 25, 1: 18, 2: 9}.get(story.tier, 5)
    independent_sources = len({e.get("source") for e in story.evidence if e.get("source")})
    corroboration = min(15, max(0, independent_sources - 1) * 7.5)
    cve_score = min(10, 4 * len(story.cves))
    kev_score = 20 if any(cve in kev for cve in story.cves) else 0
    words = (story.title + " " + story.summary).lower()
    operational_terms = ["actively exploited", "exploited in the wild", "zero-day", "zero day", "rce", "remote code execution", "ransomware", "data breach", "credential theft", "authentication bypass", "patch", "kev"]
    relevance = min(10, sum(2 for term in operational_terms if term in words))
    story.kev = any(cve in kev for cve in story.cves)
    story.score_parts = {"recency": round(recency, 1), "source_tier": tier_score, "independent_corroboration": corroboration, "cve_presence": cve_score, "cisa_kev": kev_score, "operational_relevance": relevance}
    story.score = sum(story.score_parts.values())
    if story.tier == 0 and independent_sources >= 1:
        story.confidence = "Medium"  # primary source, but article-level claims are not fully parsed
    else:
        story.confidence = "Low" if independent_sources < 2 else "Medium"
    if story.kev and any(e.get("tier") == 0 for e in story.evidence):
        story.confidence = "Medium"  # KEV verifies only catalog membership
    story.status = "Source reported"
    return story


def verify_story(story, kev=None):
    issues = []
    if not story.url.startswith(("https://", "http://")):
        issues.append("Missing valid evidence URL")
    if not story.evidence:
        issues.append("No evidence record")
    for cve in story.cves:
        if not cve.startswith("CVE-"):
            issues.append("Malformed CVE identifier")
    # KEV membership is verified when present; absence does not mean a CVE is safe or unexploited.
    if kev is not None:
        story.kev = any(cve in kev for cve in story.cves)
    return issues
