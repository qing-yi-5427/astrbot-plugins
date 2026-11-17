"""OpenAI-compatible Images API client; independent of AstrBot."""

import asyncio
import base64
import binascii
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import aiohttp


class ImageGenError(Exception):
    """A safe explanation and structured facts, never a raw upstream error."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "image_generation_error",
        stage: str = "request",
        http_status: int | None = None,
        request_outcome: str = "unknown",
    ):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.http_status = http_status
        self.request_outcome = request_outcome

    def details(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "stage": self.stage,
            "http_status": self.http_status,
            "request_outcome": self.request_outcome,
            "automatic_retry": False,
        }


def config_error(message: str) -> ImageGenError:
    return ImageGenError(
        message, code="invalid_config", stage="prepare", request_outcome="not_started"
    )


def http_url(value: str, label: str, *, stage: str = "prepare") -> str:
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        )
        parsed.port  # Validate malformed ports too.
    except ValueError:
        valid = False
    if not valid:
        message = f"{label} 必须是有效的 HTTP(S) 地址，且不能包含用户名或密码。"
        if stage == "prepare":
            raise config_error(message)
        raise ImageGenError(message, code="invalid_image_url", stage=stage)
    return value


@dataclass(frozen=True)
class ImageSettings:
    endpoint: str
    api_key: str = field(repr=False)
    model: str
    size: str
    quality: str
    output_format: str
    background: str
    n: int
    timeout: int
    proxy: str = field(repr=False)
    max_image_bytes: int

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "ImageSettings":
        def string(key: str, default: str = "") -> str:
            value = config.get(key, default)
            if not isinstance(value, str):
                raise config_error(f"配置 {key} 必须是字符串。")
            return value.strip()

        def integer(key: str, default: int, lower: int, upper: int) -> int:
            value = config.get(key, default)
            if type(value) is not int or not lower <= value <= upper:
                raise config_error(f"配置 {key} 必须是 {lower}–{upper} 之间的整数。")
            return value

        base = http_url(string("base_url", "https://api.openai.com/v1"), "API 地址")
        parsed = urlsplit(base)
        if parsed.query or parsed.fragment:
            raise config_error("API 地址不能包含查询参数或片段。")
        base = base.rstrip("/")
        path = string("images_path", "/images/generations")
        if not path.startswith("/") or "?" in path or "#" in path:
            raise config_error("images_path 必须是以 / 开头的接口路径。")
        endpoint = base if base.endswith(path) else base + path
        model = string("model", "gpt-image-2")
        if not model:
            raise config_error("请先配置生图模型名。")
        size = string("size", "auto")
        if size != "auto" and not re.fullmatch(r"[1-9]\d{1,4}x[1-9]\d{1,4}", size):
            raise config_error("图片尺寸应为 auto 或 宽x高，例如 1024x1024。")
        quality = string("quality", "auto")
        if quality not in {"auto", "low", "medium", "high", "xhigh", "max"}:
            raise config_error("图片质量配置无效。")
        output_format = string("output_format", "png").lower()
        if output_format not in {"png", "jpeg", "webp", "gif"}:
            raise config_error("图片格式只能为 png、jpeg、webp 或 gif。")
        background = string("background", "auto")
        if background not in {"auto", "opaque", "transparent"}:
            raise config_error("图片背景配置无效。")
        if background == "transparent" and output_format == "jpeg":
            raise config_error("透明背景需要 png、webp 或 gif 格式，并且服务端支持。")
        proxy = string("proxy")
        if proxy:
            http_url(proxy, "代理地址")
        api_key = string("api_key") or os.environ.get("OPENAI_API_KEY", "").strip()
        return cls(
            endpoint=endpoint,
            api_key=api_key,
            model=model,
            size=size,
            quality=quality,
            output_format=output_format,
            background=background,
            n=integer("n", 1, 1, 10),
            timeout=integer("timeout", 300, 1, 1800),
            proxy=proxy,
            max_image_bytes=integer("max_image_mb", 20, 1, 100) * 1024 * 1024,
        )

    def payload(self, prompt: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "prompt": prompt,
            "n": self.n,
            "size": self.size,
            "quality": self.quality,
            "output_format": self.output_format,
            "background": self.background,
        }


async def read_limited(
    response: aiohttp.ClientResponse, limit: int, *, stage: str = "response"
) -> bytes:
    data = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        if len(data) + len(chunk) > limit:
            raise ImageGenError(
                "返回的数据过大，请减小图片尺寸、数量或调整大小限制。",
                code="response_too_large",
                stage=stage,
            )
        data.extend(chunk)
    return bytes(data)


def validate_image(data: bytes, limit: int) -> bytes:
    if not data or len(data) > limit:
        raise ImageGenError(
            "图片为空或超出 max_image_mb 限制。", code="invalid_image", stage="response"
        )
    is_image = (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
        or data.startswith((b"GIF87a", b"GIF89a"))
    )
    if not is_image:
        raise ImageGenError(
            "接口返回的内容不是 PNG、JPEG、WebP 或 GIF 图片。",
            code="invalid_image",
            stage="response",
        )
    return data


class ImagesClient:
    async def generate(self, prompt: str, settings: ImageSettings) -> list[bytes]:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ImageGenError(
                "请提供图片描述，例如：/i 一只趴在窗边的橘猫。",
                code="invalid_prompt",
                stage="prepare",
                request_outcome="not_started",
            )
        if len(prompt) > 32000:
            raise ImageGenError(
                "图片描述过长，请控制在 32000 字以内。",
                code="invalid_prompt",
                stage="prepare",
                request_outcome="not_started",
            )
        headers = {"Content-Type": "application/json"}
        if settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key}"
        stage = "request"
        try:
            # One overall deadline covers generation and URL downloads. No automatic
            # retries: a timeout can happen after the provider has billed the request.
            async with asyncio.timeout(settings.timeout):
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=settings.timeout),
                    trust_env=True,
                ) as session:
                    async with session.post(
                        settings.endpoint,
                        headers=headers,
                        json=settings.payload(prompt.strip()),
                        proxy=settings.proxy or None,
                        allow_redirects=False,
                    ) as response:
                        status = response.status
                        if not 200 <= status < 300:
                            # Bounded diagnostics: inspect only known code/type values.
                            # Never copy free-form upstream messages into model context.
                            try:
                                async with asyncio.timeout(2):
                                    error_body = await read_limited(response, 64 * 1024)
                                error_payload = json.loads(error_body)
                            except (
                                ImageGenError,
                                ValueError,
                                UnicodeError,
                                TimeoutError,
                            ):
                                error_payload = None
                            self._check_status(status, error_payload)
                        stage = "response"
                        limit = (
                            settings.n * ((settings.max_image_bytes + 2) // 3 * 4)
                            + 1024 * 1024
                        )
                        body = await read_limited(response, limit)
                    try:
                        payload = json.loads(body)
                    except (ValueError, UnicodeError) as exc:
                        raise ImageGenError(
                            "生图接口没有返回 JSON，请检查 API 地址和接口路径。",
                            code="invalid_response",
                            stage="response",
                        ) from exc
                    if not isinstance(payload, dict):
                        raise ImageGenError(
                            "生图接口的响应格式无效。",
                            code="invalid_response",
                            stage="response",
                        )
                    if payload.get("error"):
                        self._check_status(status, payload)
                    items = payload.get("data")
                    if not isinstance(items, list) or not items:
                        raise ImageGenError(
                            "接口没有返回图片，请确认服务支持 Images API。",
                            code="no_image_returned",
                            stage="response",
                        )
                    if len(items) > settings.n:
                        raise ImageGenError(
                            "接口返回的图片数量超过请求数量。",
                            code="invalid_response",
                            stage="response",
                        )
                    images = []
                    for item in items:
                        if not isinstance(item, dict):
                            raise ImageGenError(
                                "图片数据格式无效。",
                                code="invalid_response",
                                stage="response",
                            )
                        encoded = item.get("b64_json")
                        url = item.get("url")
                        if isinstance(encoded, str) and encoded:
                            try:
                                data = base64.b64decode(encoded, validate=True)
                            except (binascii.Error, ValueError) as exc:
                                raise ImageGenError(
                                    "接口返回的图片 Base64 无效。",
                                    code="invalid_image",
                                    stage="response",
                                ) from exc
                        elif isinstance(url, str) and url:
                            stage = "download"
                            http_url(url, "返回的图片地址", stage=stage)
                            # Authentication is scoped to POST, never forwarded to CDN.
                            async with session.get(
                                url, proxy=settings.proxy or None
                            ) as response:
                                if response.status != 200:
                                    raise ImageGenError(
                                        f"图片下载失败（HTTP {response.status}）。",
                                        code="image_download_failed",
                                        stage="download",
                                        http_status=response.status,
                                    )
                                data = await read_limited(
                                    response, settings.max_image_bytes, stage="download"
                                )
                        else:
                            raise ImageGenError(
                                "图片数据缺少 b64_json 或 url。",
                                code="invalid_response",
                                stage="response",
                            )
                        stage = "response"
                        images.append(validate_image(data, settings.max_image_bytes))
                    return images
        except TimeoutError as exc:
            raise ImageGenError(
                "生图或下载超时，无法确认服务端是否已完成生成或计费；请勿自动重试。",
                code="timeout",
                stage=stage,
            ) from exc
        except aiohttp.ClientError as exc:
            raise ImageGenError(
                "连接生图服务或下载图片时发生网络错误，请检查网络和代理；服务端执行状态不明。",
                code="network_error",
                stage=stage,
            ) from exc

    @staticmethod
    def _check_status(status: int, payload: Any = None) -> None:
        error = payload.get("error") if isinstance(payload, dict) else None
        if 200 <= status < 300 and not error:
            return
        categories = {
            "content_rejected": "远程生图服务明确拒绝了内容（安全审核/内容限制）；本次未返回图片。",
            "authentication_failed": "鉴权失败，请配置正确的 api_key。",
            "permission_denied": "服务拒绝访问，请检查账户或模型权限；不能仅据此认定是内容审核。",
            "quota_exceeded": "生图服务额度或余额不足，请检查账户配额。",
            "rate_limited": "生图服务触发限流，请稍后由用户决定是否重试。",
            "model_not_found": "生图模型不存在或不可访问，请检查模型名和账户权限。",
            "invalid_request": (
                "请求被服务拒绝，可能是参数或内容限制；没有明确审核错误码，无法确定具体原因。"
            ),
            "endpoint_not_found": "接口或模型不存在，请检查 base_url、images_path 和 model。",
            "rate_or_quota_limited": "请求受限或余额不足，请检查账户配额和服务状态。",
            "remote_error": "远程生图服务返回错误，未提供可确认的具体原因；请检查服务端日志。",
        }
        # Only these exact, known identifiers affect classification. Unknown codes,
        # URLs, messages, headers and arbitrary provider text never leave the client.
        known_codes = {
            "content_policy_violation": "content_rejected",
            "moderation_blocked": "content_rejected",
            "safety_system": "content_rejected",
            "content_filter": "content_rejected",
            "invalid_api_key": "authentication_failed",
            "authentication_error": "authentication_failed",
            "permission_denied": "permission_denied",
            "permission_error": "permission_denied",
            "insufficient_quota": "quota_exceeded",
            "quota_exceeded": "quota_exceeded",
            "rate_limit_exceeded": "rate_limited",
            "rate_limit_error": "rate_limited",
            "model_not_found": "model_not_found",
        }
        category = None
        if isinstance(error, dict):
            for key in ("code", "type"):
                identifier = error.get(key)
                if isinstance(identifier, str) and identifier in known_codes:
                    category = known_codes[identifier]
                    break
        if category is None:
            category = {
                400: "invalid_request",
                401: "authentication_failed",
                403: "permission_denied",
                404: "endpoint_not_found",
                429: "rate_or_quota_limited",
            }.get(status, "remote_error")
        outcome = "unknown" if category == "remote_error" else "rejected"
        raise ImageGenError(
            f"{categories[category]}（HTTP {status}）",
            code=category,
            stage="request",
            http_status=status,
            request_outcome=outcome,
        )
