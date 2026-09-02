from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Confidence(str, Enum):
    HIGH = "high"
    POSSIBLE = "possible"
    LOW = "low"


@dataclass(slots=True)
class ImagePayload:
    content: bytes
    filename: str
    mime_type: str
    sha256: str


@dataclass(slots=True)
class SearchHit:
    engine: str
    kind: str
    title: str
    source_url: str = ""
    creator: str = ""
    thumbnail_url: str = ""
    similarity: float | None = None
    confidence: Confidence = Confidence.LOW
    episode: str = ""
    from_seconds: float | None = None
    to_seconds: float | None = None
    adult: bool = False
    uncertain: bool = False
    raw_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = self.confidence.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchHit:
        values = dict(data)
        values["confidence"] = Confidence(values.get("confidence", "low"))
        return cls(**values)

    def dedupe_key(self) -> str:
        if self.source_url:
            return self.source_url.casefold()
        return f"{self.engine.casefold()}|{self.title.casefold()}|{self.episode.casefold()}"


@dataclass(slots=True)
class EngineOutcome:
    engine: str
    hits: list[SearchHit] = field(default_factory=list)
    quota: dict[str, str] = field(default_factory=dict)
    warning: str = ""


@dataclass(slots=True)
class SearchReport:
    hits: list[SearchHit] = field(default_factory=list)
    engines_used: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cache_hit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": [hit.to_dict() for hit in self.hits],
            "engines_used": list(self.engines_used),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchReport:
        return cls(
            hits=[SearchHit.from_dict(item) for item in data.get("hits", [])],
            engines_used=[str(item) for item in data.get("engines_used", [])],
            warnings=[str(item) for item in data.get("warnings", [])],
        )
