from __future__ import annotations

import html as html_lib
import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from curl_cffi import CurlMime
from curl_cffi.requests import AsyncSession

from ..http_client import SearchEngineError
from ..models import Confidence, EngineOutcome, ImagePayload, SearchHit


class Ascii2DEngine:
    name = "Ascii2D"
    base_url = "https://ascii2d.net"

    def __init__(
        self,
        *,
        enabled: bool,
        session_id: str = "",
        cf_clearance: str = "",
        proxy: str = "",
        user_agent: str = "",
        timeout_seconds: int = 25,
    ):
        self.enabled = enabled
        self.session_id = session_id
        self.cf_clearance = cf_clearance
        self.proxy = proxy
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds

    @property
    def available(self) -> bool:
        return self.enabled

    def _cookies(self) -> dict[str, str]:
        cookies: dict[str, str] = {}
        if self.session_id:
            cookies["_session_id"] = self.session_id
        if self.cf_clearance:
            cookies["cf_clearance"] = self.cf_clearance
        return cookies

    @staticmethod
    def _extract_file_token(page: str) -> str:
        form = re.search(
            r"<form\b[^>]*action=['\"]/search/file['\"][^>]*>(.*?)</form>",
            page,
            re.IGNORECASE | re.DOTALL,
        )
        if form is None:
            return ""
        token = re.search(
            r"<input\b[^>]*name=['\"]authenticity_token['\"][^>]*"
            r"value=['\"]([^'\"]+)['\"]",
            form.group(1),
            re.IGNORECASE,
        )
        if token is None:
            token = re.search(
                r"<input\b[^>]*value=['\"]([^'\"]+)['\"][^>]*"
                r"name=['\"]authenticity_token['\"]",
                form.group(1),
                re.IGNORECASE,
            )
        return html_lib.unescape(token.group(1)) if token else ""

    @staticmethod
    def _plain_text(fragment: str) -> str:
        text = re.sub(r"<[^>]+>", " ", fragment)
        return " ".join(html_lib.unescape(text).split())

    @classmethod
    def _external_url(cls, value: str) -> str:
        candidate = html_lib.unescape(value).strip()
        absolute = urljoin(f"{cls.base_url}/", candidate)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        if parsed.netloc.casefold() != "ascii2d.net":
            return absolute
        query = parse_qs(parsed.query)
        for key in ("url", "uri", "target"):
            for item in query.get(key, []):
                decoded = unquote(item)
                target = urlparse(decoded)
                if target.scheme in {"http", "https"} and target.netloc:
                    return decoded
        return ""

    @classmethod
    def parse_result_page(cls, page: str) -> list[SearchHit]:
        blocks = re.findall(
            r"<div\b[^>]*class=['\"][^'\"]*\bitem-box\b[^'\"]*['\"][^>]*>"
            r"(.*?)<div\b[^>]*class=['\"][^'\"]*\bclearfix\b[^'\"]*['\"]"
            r"[^>]*>\s*</div>",
            page,
            re.IGNORECASE | re.DOTALL,
        )
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for block in blocks:
            anchors = re.findall(
                r"<a\b[^>]*href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>",
                block,
                re.IGNORECASE | re.DOTALL,
            )
            work_url = ""
            link_title = ""
            for href, label in anchors:
                work_url = cls._external_url(href)
                if work_url:
                    link_title = cls._plain_text(label)
                    break
            if not work_url or work_url.casefold() in seen:
                continue
            seen.add(work_url.casefold())

            heading = re.search(
                r"<h6\b[^>]*>(.*?)</h6>",
                block,
                re.IGNORECASE | re.DOTALL,
            )
            title = cls._plain_text(heading.group(1)) if heading else link_title
            image = re.search(
                r"<img\b[^>]*(?:src|data-src)=['\"]([^'\"]+)['\"]",
                block,
                re.IGNORECASE,
            )
            thumbnail = urljoin(f"{cls.base_url}/", image.group(1)) if image else ""
            hits.append(
                SearchHit(
                    engine=cls.name,
                    kind="illustration",
                    title=title or "Ascii2D 搜索结果",
                    work_url=work_url,
                    thumbnail_url=thumbnail,
                    confidence=Confidence.POSSIBLE,
                    uncertain=True,
                )
            )
        return hits

    async def _fetch_page(self, session: AsyncSession, url: str) -> str:
        response = await session.get(url, proxy=self.proxy or None)
        if response.status_code == 403:
            raise SearchEngineError(
                self.name,
                "访问被 Cloudflare 拒绝（HTTP 403），请更新 Ascii2D Cookie",
            )
        if response.status_code >= 400:
            raise SearchEngineError(
                self.name, f"请求失败（HTTP {response.status_code}）"
            )
        return response.text

    async def search(self, image: ImagePayload) -> EngineOutcome:
        if not self.available:
            return EngineOutcome(self.name)

        try:
            async with AsyncSession(
                impersonate="chrome",
                timeout=self.timeout_seconds,
                cookies=self._cookies(),
                headers={"User-Agent": self.user_agent} if self.user_agent else None,
            ) as session:
                home = await self._fetch_page(session, f"{self.base_url}/")
                token = self._extract_file_token(home)
                if not token:
                    raise SearchEngineError(
                        self.name, "无法取得上传令牌，请更新 Ascii2D Cookie"
                    )

                form = CurlMime()
                form.addpart("utf8", data="✓".encode())
                form.addpart("authenticity_token", data=token.encode())
                form.addpart(
                    "file",
                    filename=image.filename,
                    content_type=image.mime_type,
                    data=image.content,
                )
                try:
                    response = await session.post(
                        f"{self.base_url}/search/file",
                        multipart=form,
                        allow_redirects=True,
                        proxy=self.proxy or None,
                        headers={
                            "Origin": self.base_url,
                            "Referer": f"{self.base_url}/",
                        },
                    )
                finally:
                    form.close()

                if response.status_code == 403:
                    raise SearchEngineError(
                        self.name,
                        "访问被 Cloudflare 拒绝（HTTP 403），请更新 Ascii2D Cookie",
                    )
                if response.status_code >= 400:
                    raise SearchEngineError(
                        self.name, f"请求失败（HTTP {response.status_code}）"
                    )
                result_url = str(response.url)
                if (
                    "/search/color/" not in result_url
                    and "/search/bovw/" not in result_url
                ):
                    raise SearchEngineError(self.name, "服务未返回有效结果页")

                color_url = result_url.replace("/search/bovw/", "/search/color/")
                bovw_url = result_url.replace("/search/color/", "/search/bovw/")
                color_page = (
                    response.text
                    if result_url == color_url
                    else await self._fetch_page(session, color_url)
                )
                bovw_page = (
                    response.text
                    if result_url == bovw_url
                    else await self._fetch_page(session, bovw_url)
                )

            combined = self.parse_result_page(bovw_page) + self.parse_result_page(
                color_page
            )
            unique: list[SearchHit] = []
            seen: set[str] = set()
            for hit in combined:
                key = hit.dedupe_key()
                if key in seen:
                    continue
                seen.add(key)
                unique.append(hit)
            return EngineOutcome(self.name, unique)
        except SearchEngineError:
            raise
        except Exception as exc:
            raise SearchEngineError(self.name, "网络连接失败") from exc
