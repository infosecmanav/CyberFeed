import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import json
import urllib.request
import xml.etree.ElementTree as ET
from .models import Story

CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.I)


def load_sources(path="sources.yaml"):
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt to read sources.yaml") from exc
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("sources", [])


def parse_date(value):
    if value:
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            pass
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            pass
    raise ValueError("Missing or invalid publication date")


def fetch_feeds(sources, timeout=15):
    stories = []
    errors = []
    for src in sources:
        try:
            request = urllib.request.Request(src["url"], headers={"User-Agent": "CyberBrief/0.1 (+public security research)"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
            root = ET.fromstring(raw)
            if root.tag.split("}")[-1] not in ("rss", "feed", "RDF"):
                raise ValueError("Source returned XML that is not an RSS or Atom feed")
            entries = root.findall(".//item")
            # Atom feeds use a namespace; local-name handling keeps this dependency-free.
            if not entries:
                entries = [el for el in root.iter() if el.tag.split("}")[-1] == "entry"]
            for entry in entries:
                values = {}
                for child in list(entry):
                    key = child.tag.split("}")[-1]
                    if key == "link":
                        values[key] = child.attrib.get("href", "") or (child.text or "")
                    elif key in ("title", "description", "summary", "content", "pubDate", "published", "updated"):
                        values[key] = " ".join("".join(child.itertext()).split())
                title = (values.get("title") or "Untitled security update").strip()
                link = values.get("link", "")
                if not link.startswith(("https://", "http://")):
                    continue
                summary = re.sub(r"<[^>]+>", " ", values.get("summary", values.get("description", values.get("content", ""))))
                summary = " ".join(summary.split())[:1800]
                try:
                    published = parse_date(values.get("published") or values.get("pubDate") or values.get("updated"))
                except ValueError:
                    continue
                if published > datetime.now(timezone.utc):
                    continue
                stories.append(Story(title=title, url=link, source=src["name"], tier=int(src.get("tier", 2)),
                    published=published, summary=summary, lens=src.get("lens", "Global"), kind=src.get("kind", "reporting"),
                    cves=sorted(set(x.upper() for x in CVE_RE.findall(title + " " + summary))),
                    evidence=[{"source": src["name"], "url": link, "tier": int(src.get("tier", 2))}]))
        except Exception as exc:
            errors.append({"source": src.get("name", src.get("url", "unknown")), "error": str(exc)[:300]})
    return stories, errors


def fetch_cisa_kev(timeout=20):
    # Both locations are published by CISA; the GitHub mirror works when
    # cisa.gov's edge service rejects this cloud instance.
    urls = (
        "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
        "https://raw.githubusercontent.com/cisagov/kev-data/main/known_exploited_vulnerabilities.json",
    )
    last_error = None
    for url in urls:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "CyberBrief/0.1"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            items = data["vulnerabilities"]
            if not isinstance(items, list) or not items:
                raise ValueError("CISA KEV catalog has no vulnerability records")
            catalog = {item["cveID"].upper(): item for item in items if item.get("cveID")}
            if not catalog:
                raise ValueError("CISA KEV catalog has no CVE identifiers")
            return catalog
        except (OSError, ValueError, KeyError, TypeError) as exc:
            last_error = exc
    raise RuntimeError("Both official CISA KEV catalog locations are unavailable") from last_error


def fixture_stories():
    now = datetime.now(timezone.utc)
    return [
        Story("Vendor advisory: critical remote code execution fix", "https://security.example.invalid/advisory", "Fixture Vendor PSIRT", 0, now-timedelta(hours=4), "Fictional test story; not a real incident.", "Enterprise", "primary", ["CVE-2099-12345"], [{"source":"Fixture Vendor PSIRT","url":"https://security.example.invalid/advisory","tier":0}]),
        Story("Researchers publish analysis of credential theft campaign", "https://research.example.invalid/report", "Fixture Research Team", 1, now-timedelta(days=1), "Fictional test story; not a real incident.", "Global", "research", [], [{"source":"Fixture Research Team","url":"https://research.example.invalid/report","tier":1}]),
        Story("Security industry weekly roundup", "https://news.example.invalid/roundup", "Fixture News", 2, now-timedelta(days=2), "Fictional test story; not a real incident.", "Global", "reporting", [], [{"source":"Fixture News","url":"https://news.example.invalid/roundup","tier":2}]),
    ]
