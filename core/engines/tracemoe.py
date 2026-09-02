from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import aiohttp

from ..http_client import SearchEngineError, post_json_with_retry
from ..models import Confidence, EngineOutcome, ImagePayload, SearchHit


def _safe_url(value: Any) -> str:
    text = str(value or "").strip().replace("\n", "").replace("\r", "")
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    return text if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _title(anilist: dict[str, Any]) -> str:
    title = anilist.get("title", {})
    if not isinstance(title, dict):
        return "未知动画"
    for key in ("native", "english", "romaji"):
        if title.get(key):
            return str(title[key]).strip()
    return "未知动画"


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TraceMoeEngine:
    name = "trace.moe"
    endpoint = "https://api.trace.moe/search?anilistInfo"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        api_key: str,
        high_threshold: float,
        possible_threshold: float,
        enabled: bool = True,
        proxy: str = "",
    ):
        self.session = session
        self.api_key = api_key
        self.high_threshold = high_threshold
        self.possible_threshold = possible_threshold
        self.enabled = enabled
        self.proxy = proxy

    @property
    def available(self) -> bool:
        return self.enabled

    def _confidence(self, similarity: float) -> Confidence:
        if similarity >= self.high_threshold:
            return Confidence.HIGH
        if similarity >= self.possible_threshold:
            return Confidence.POSSIBLE
        return Confidence.LOW

    def parse_response(self, payload: dict[str, Any]) -> EngineOutcome:
        if payload.get("error"):
            raise SearchEngineError(self.name, str(payload["error"])[:160])
        hits: list[SearchHit] = []
        for item in payload.get("result", []):
            if not isinstance(item, dict):
                continue
            try:
                similarity = float(item.get("similarity", 0)) * 100
            except (TypeError, ValueError):
                similarity = 0.0
            confidence = self._confidence(similarity)
            if confidence is Confidence.LOW:
                continue
            anilist = item.get("anilist", {})
            if not isinstance(anilist, dict):
                anilist = {}
            raw_id = str(anilist.get("id", item.get("anilist", "")) or "")
            source_url = (
                f"https://anilist.co/anime/{raw_id}" if raw_id.isdigit() else ""
            )
            hits.append(
                SearchHit(
                    engine=self.name,
                    kind="anime",
                    title=_title(anilist),
                    source_url=source_url,
                    thumbnail_url=_safe_url(item.get("image")),
                    similarity=similarity,
                    confidence=confidence,
                    episode=str(item.get("episode") or ""),
                    from_seconds=_optional_float(item.get("from")),
                    to_seconds=_optional_float(item.get("to")),
                    adult=bool(anilist.get("isAdult", False)),
                    uncertain=confidence is not Confidence.HIGH,
                    raw_id=raw_id,
                )
            )
        hits.sort(key=lambda hit: hit.similarity or 0, reverse=True)
        quota = {}
        if payload.get("frameCount") is not None:
            quota["frame_count"] = str(payload["frameCount"])
        return EngineOutcome(self.name, hits, quota)

    async def search(self, image: ImagePayload) -> EngineOutcome:
        if not self.available:
            return EngineOutcome(self.name, warning="trace.moe 已在配置中关闭")

        def form_factory() -> aiohttp.FormData:
            form = aiohttp.FormData()
            form.add_field(
                "image",
                image.content,
                filename=image.filename,
                content_type=image.mime_type,
            )
            return form

        headers = {"x-trace-key": self.api_key} if self.api_key else None
        payload = await post_json_with_retry(
            self.session,
            engine=self.name,
            url=self.endpoint,
            form_factory=form_factory,
            headers=headers,
            proxy=self.proxy,
        )
        return self.parse_response(payload)
