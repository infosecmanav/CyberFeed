import json, os
from concurrent.futures import ThreadPoolExecutor
from .details import enrich
from pathlib import Path
from datetime import datetime, timezone, timedelta
from .ingest import load_sources, fetch_feeds, fetch_cisa_kev, fixture_stories
from .verify import cluster_stories, score_story
ROOT = Path(__file__).resolve().parent.parent

def weekly_digest(stories):
    """Group attributed excerpts without inventing cross-story conclusions."""
    groups = {}
    for story in stories:
        words = (story["title"] + " " + story["summary"]).lower()
        topic = "Other security updates"
        for label, terms in (
            ("Ransomware and extortion", ("ransomware", "extortion")),
            ("Vendor hardening releases", ("security hardening",)),
            ("Vulnerabilities and patches", ("cve-", "vulnerabilit", "patch", "security update")),
            ("Intrusions and credential theft", ("intrusion", "credential", "phishing", "malware", "campaign")),
        ):
            if any(term in words for term in terms):
                topic = label
                break
        groups.setdefault(topic, []).append({
            "title": story["title"], "source": story["source"],
            "url": story["url"], "excerpt": story["summary"],
        })
    return [{"topic": topic, "updates": updates} for topic, updates in groups.items()]

def validate_claims(claims, evidence):
    accepted = []
    for claim in claims[:3]:
        text = claim.get("text", "")
        quote = claim.get("quote", "")
        eid = claim.get("evidence_id")
        if isinstance(text, str) and text and text == quote and eid in evidence and quote in evidence[eid]:
            accepted.append({"text": text, "evidence_id": eid, "quote": quote, "scope": "Source reports"})
    return accepted if sum(len(c["text"].split()) for c in accepted) <= 60 else []

def extract(story, key, model):
    import requests
    instructions = (ROOT / "SKILL.md").read_text()
    evidence = {"feed": story.summary}
    response = requests.post("https://api.openai.com/v1/responses", headers={"Authorization": "Bearer " + key}, json={"model": model, "store": False, "instructions": instructions, "input": json.dumps({"title": story.title, "evidence": evidence}), "max_output_tokens": 700}, timeout=45)
    response.raise_for_status()
    output = "".join(c.get("text", "") for o in response.json().get("output", []) for c in o.get("content", []) if c.get("type") == "output_text")
    claims = json.loads(output).get("claims", [])
    return validate_claims(claims, evidence)

def select_stories(stories, limit=10, per_source=3):
    """Round-robin ranked publishers so a release burst cannot crowd others out."""
    pools = {}
    def priority(story):
        routine = any(term in story.title.lower() for term in ('security hardening release', 'advance notification'))
        return (story.kev, not routine, story.score)
    for story in sorted(stories, key=priority, reverse=True):
        pools.setdefault(story.source, []).append(story)
    selected = []
    for offset in range(per_source):
        for pool in pools.values():
            if offset < len(pool):
                selected.append(pool[offset])
                if len(selected) == limit:
                    return selected
    return selected


def build(period="daily", fixtures=False, key=None, model=None):
    if period not in ("daily", "weekly"):
        raise ValueError("Choose daily or weekly")
    # Loading is mandatory even when deterministic extraction is used.
    (ROOT / "SKILL.md").read_text()
    errors = []
    if fixtures:
        stories, kev = fixture_stories(), {}
    else:
        stories, errors = fetch_feeds(load_sources(ROOT / "sources.yaml"))
        try: kev = fetch_cisa_kev()
        except Exception: kev = {}; errors.append({"source": "CISA KEV", "error": "Catalog unavailable; membership unknown"})
    now = datetime.now(timezone.utc)
    if not fixtures:
        candidates = sorted((s for s in stories if s.source == 'Fortinet PSIRT' and now-s.published <= timedelta(days=30)), key=lambda s: s.published, reverse=True)[:3]
        with ThreadPoolExecutor(max_workers=3) as pool:
            fresh_details = list(pool.map(enrich, candidates))
        for story, detail in zip(candidates, fresh_details):
            story.details = detail
            story.cves = sorted(set(story.cves + detail.get('cves', [])))
            if detail.get('error'):
                errors.append({'source': story.source, 'error': detail['error'], 'url': story.url})
    window = timedelta(hours=36) if period == "daily" else timedelta(days=7)
    def effective_date(story):
        return datetime.fromisoformat(story.details['updated_at']) if story.details.get('updated_at') else story.published
    stories = [s for s in cluster_stories(stories) if timedelta(0) <= now-effective_date(s) <= window]
    for s in stories: score_story(s, kev)
    stories = select_stories(stories)
    with ThreadPoolExecutor(max_workers=4) as pool:
        details = list(pool.map(lambda s: enrich(s, fixtures), stories))
    result = []
    for s, detail in zip(stories, details):
        # Safe deterministic default: publish a bounded, attributed source excerpt.
        words = s.summary.split()
        excerpt = " ".join(words[:60])
        claims = validate_claims([{"text": excerpt, "quote": excerpt, "evidence_id": "feed"}], {"feed": s.summary}) if excerpt else []
        if key:
            try: claims = extract(s, key, model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
            except Exception: errors.append({"source": s.source, "error": "Optional model unavailable; using source excerpt"})
        item = s.to_dict()
        for field in ("confidence", "score", "score_parts", "status"): item.pop(field, None)
        item.update(summary=" ".join(c["text"] for c in claims), claims=claims, kev_available=not any(e["source"]=="CISA KEV" for e in errors))
        item['details'] = {k: v for k, v in detail.items() if k != 'source_text'}
        if detail.get('summary') and not key:
            item['summary'] = detail['summary']
            item['claims'] = [{'text': detail['summary'], 'quote': detail['summary'],
                               'evidence_id': detail['status'] if detail['status'] in ('article', 'csaf', 'cvrf') else 'feed',
                               'scope': 'Source reports', 'url': detail['url']}]
        if detail.get('status') == 'unavailable' and not any(e.get('url') == s.url and e['source'] == s.source for e in errors):
            errors.append({'source': s.source, 'error': detail['error'], 'url': s.url})
        item['cves'] = sorted(set(s.cves + detail.get('cves', [])))
        item['known_exploited_cves'] = [cve for cve in item['cves'] if cve in kev]
        item['kev'] = bool(item['known_exploited_cves'])
        result.append(item)
    return {"period": period, "generated": now.isoformat(), "demo": fixtures, "stories": result, "errors": errors,
            "window_start": (now-window).isoformat(), "window_end": now.isoformat(),
            "selection": "Up to 10 updates; at most 3 per publisher, selected in ranked publisher rounds",
            "weekly_digest": weekly_digest(result) if period == "weekly" else []}
