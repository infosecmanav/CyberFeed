from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

@dataclass
class Story:
    title: str
    url: str
    source: str
    tier: int
    published: datetime
    summary: str = ""
    lens: str = "Global"
    kind: str = "reporting"
    cves: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    score_parts: dict[str, float] = field(default_factory=dict)
    confidence: str = "Low"
    status: str = "Developing"
    kev: bool = False

    def to_dict(self):
        return {
            "title": self.title, "url": self.url, "source": self.source,
            "tier": self.tier, "published": self.published.astimezone(timezone.utc).isoformat(),
            "summary": self.summary, "lens": self.lens, "kind": self.kind,
            "cves": self.cves, "evidence": self.evidence, "score": round(self.score, 1),
            "score_parts": self.score_parts, "confidence": self.confidence,
            "status": self.status, "kev": self.kev,
        }
