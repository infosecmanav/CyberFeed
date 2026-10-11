import json, os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from .ingest import load_sources, fetch_feeds, fetch_cisa_kev, fixture_stories
from .verify import cluster_stories, score_story
ROOT = Path(__file__).resolve().parent.parent

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

def build(period="daily", fixtures=False, key=None, model=None):
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
    window = timedelta(hours=36) if period == "daily" else timedelta(days=7)
    stories = [s for s in cluster_stories(stories) if timedelta(0) <= now-s.published <= window]
    for s in stories: score_story(s, kev)
    stories = sorted(stories, key=lambda s: s.score, reverse=True)[:10]
    result = []
    for s in stories:
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
        result.append(item)
    return {"period": period, "generated": now.isoformat(), "demo": fixtures, "stories": result, "errors": errors}
