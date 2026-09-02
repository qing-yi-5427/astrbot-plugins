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


def _first_text(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value if item)
        if value not in (None, ""):
            return str(value).strip()
    return ""


class SauceNaoEngine:
    name = "SauceNAO"
    endpoint = "https://saucenao.com/search.php"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        api_key: str,
        high_threshold: float,
        possible_threshold: float,
        hide: int,
        proxy: str = "",
    ):
        self.session = session
        self.api_key = api_key
        self.high_threshold = high_threshold
        self.possible_threshold = possible_threshold
        self.hide = hide
        self.proxy = proxy

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _confidence(self, similarity: float) -> Confidence:
        if similarity >= self.high_threshold:
            return Confidence.HIGH
        if similarity >= self.possible_threshold:
            return Confidence.POSSIBLE
        return Confidence.LOW

    def parse_response(self, payload: dict[str, Any]) -> EngineOutcome:
        header = payload.get("header", {})
        if not isinstance(header, dict):
            raise SearchEngineError(self.name, "返回数据缺少 header")
        try:
            status = int(header.get("status", 0) or 0)
        except (TypeError, ValueError):
            status = -1
        if status < 0:
            message = str(header.get("message") or "API 返回错误")
            raise SearchEngineError(self.name, message[:160])

        quota = {
            "short_remaining": str(header.get("short_remaining", "?")),
            "long_remaining": str(header.get("long_remaining", "?")),
        }
        hits: list[SearchHit] = []
        for item in payload.get("results", []):
            if not isinstance(item, dict):
                continue
            result_header = item.get("header", {})
            data = item.get("data", {})
            if not isinstance(result_header, dict) or not isinstance(data, dict):
                continue
            try:
                similarity = float(result_header.get("similarity", 0))
            except (TypeError, ValueError):
                similarity = 0.0
            confidence = self._confidence(similarity)
            if confidence is Confidence.LOW:
                continue

            urls = data.get("ext_urls", [])
            if not isinstance(urls, list):
                urls = []
            source_url = next((_safe_url(url) for url in urls if _safe_url(url)), "")
            pixiv_id = _first_text(data, "pixiv_id")
            if not source_url and pixiv_id.isdigit():
                source_url = f"https://www.pixiv.net/artworks/{pixiv_id}"

            index_name = str(result_header.get("index_name", ""))
            kind = (
                "anime"
                if "anime" in index_name.casefold() or data.get("est_time")
                else "illustration"
            )
            hidden = str(result_header.get("hidden", "0")).casefold()
            adult = hidden not in {"", "0", "false", "none"}
            hits.append(
                SearchHit(
                    engine=self.name,
                    kind=kind,
                    title=_first_text(data, "title", "eng_name", "jp_name", "source")
                    or index_name
                    or "未知作品",
                    creator=_first_text(data, "member_name", "creator", "author_name"),
                    source_url=source_url,
                    thumbnail_url=_safe_url(result_header.get("thumbnail")),
                    similarity=similarity,
                    confidence=confidence,
                    episode=_first_text(data, "part", "episode"),
                    adult=adult,
                    uncertain=confidence is not Confidence.HIGH,
                    raw_id=_first_text(data, "pixiv_id", "danbooru_id", "md_id"),
                )
            )
        hits.sort(key=lambda hit: hit.similarity or 0, reverse=True)
        return EngineOutcome(self.name, hits, quota)

    async def search(self, image: ImagePayload) -> EngineOutcome:
        if not self.available:
            return EngineOutcome(self.name, warning="未配置 SauceNAO API Key，已跳过")

        def form_factory() -> aiohttp.FormData:
            form = aiohttp.FormData()
            form.add_field("output_type", "2")
            form.add_field("api_key", self.api_key)
            form.add_field("db", "999")
            form.add_field("numres", "6")
            form.add_field("hide", str(self.hide))
            form.add_field(
                "file",
                image.content,
                filename=image.filename,
                content_type=image.mime_type,
            )
            return form

        payload = await post_json_with_retry(
            self.session,
            engine=self.name,
            url=self.endpoint,
            form_factory=form_factory,
            proxy=self.proxy,
        )
        return self.parse_response(payload)
