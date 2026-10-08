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
    """An error safe to display in a conversation."""


def http_url(value: str, label: str) -> str:
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
        raise ImageGenError(f"{label} 必须是有效的 HTTP(S) 地址，且不能包含用户名或密码。")
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
                raise ImageGenError(f"配置 {key} 必须是字符串。")
            return value.strip()

        def integer(key: str, default: int, lower: int, upper: int) -> int:
            value = config.get(key, default)
            if type(value) is not int or not lower <= value <= upper:
                raise ImageGenError(f"配置 {key} 必须是 {lower}–{upper} 之间的整数。")
            return value

        base = http_url(string("base_url", "https://api.openai.com/v1"), "API 地址")
        parsed = urlsplit(base)
        if parsed.query or parsed.fragment:
            raise ImageGenError("API 地址不能包含查询参数或片段。")
        base = base.rstrip("/")
        path = string("images_path", "/images/generations")
        if not path.startswith("/") or "?" in path or "#" in path:
            raise ImageGenError("images_path 必须是以 / 开头的接口路径。")
        endpoint = base if base.endswith(path) else base + path
        model = string("model", "gpt-image-2")
        if not model:
            raise ImageGenError("请先配置生图模型名。")
        size = string("size", "auto")
        if size != "auto" and not re.fullmatch(r"[1-9]\d{1,4}x[1-9]\d{1,4}", size):
            raise ImageGenError("图片尺寸应为 auto 或 宽x高，例如 1024x1024。")
        quality = string("quality", "auto")
        if quality not in {"auto", "low", "medium", "high", "xhigh", "max"}:
            raise ImageGenError("图片质量配置无效。")
        output_format = string("output_format", "png")
        if output_format not in {"png", "jpeg", "webp"}:
            raise ImageGenError("图片格式只能为 png、jpeg 或 webp。")
        background = string("background", "auto")
        if background not in {"auto", "opaque", "transparent"}:
            raise ImageGenError("图片背景配置无效。")
        if background == "transparent" and output_format == "jpeg":
            raise ImageGenError("透明背景需要 png 或 webp 格式。")
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


async def read_limited(response: aiohttp.ClientResponse, limit: int) -> bytes:
    data = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        if len(data) + len(chunk) > limit:
            raise ImageGenError("返回的数据过大，请减小图片尺寸、数量或调整大小限制。")
        data.extend(chunk)
    return bytes(data)


def validate_image(data: bytes, limit: int) -> bytes:
    if not data or len(data) > limit:
        raise ImageGenError("图片为空或超出 max_image_mb 限制。")
    is_image = (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    )
    if not is_image:
        raise ImageGenError("接口返回的内容不是 PNG、JPEG 或 WebP 图片。")
    return data


class ImagesClient:
    async def generate(self, prompt: str, settings: ImageSettings) -> list[bytes]:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ImageGenError("请提供图片描述，例如：/i 一只趴在窗边的橘猫。")
        if len(prompt) > 32000:
            raise ImageGenError("图片描述过长，请控制在 32000 字以内。")
        headers = {"Content-Type": "application/json"}
        if settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key}"
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
                        self._check_status(response.status)
                        limit = settings.n * ((settings.max_image_bytes + 2) // 3 * 4) + 1024 * 1024
                        body = await read_limited(response, limit)
                    try:
                        payload = json.loads(body)
                    except (ValueError, UnicodeError) as exc:
                        raise ImageGenError(
                            "生图接口没有返回 JSON，请检查 API 地址和接口路径。"
                        ) from exc
                    if not isinstance(payload, dict):
                        raise ImageGenError("生图接口的响应格式无效。")
                    if payload.get("error"):
                        raise ImageGenError("生图服务返回错误，请检查模型、参数和服务端日志。")
                    items = payload.get("data")
                    if not isinstance(items, list) or not items:
                        raise ImageGenError("接口没有返回图片，请确认服务支持 Images API。")
                    if len(items) > settings.n:
                        raise ImageGenError("接口返回的图片数量超过请求数量。")
                    images = []
                    for item in items:
                        if not isinstance(item, dict):
                            raise ImageGenError("图片数据格式无效。")
                        encoded = item.get("b64_json")
                        url = item.get("url")
                        if isinstance(encoded, str) and encoded:
                            try:
                                data = base64.b64decode(encoded, validate=True)
                            except (binascii.Error, ValueError) as exc:
                                raise ImageGenError("接口返回的图片 Base64 无效。") from exc
                        elif isinstance(url, str) and url:
                            http_url(url, "返回的图片地址")
                            # Authentication is scoped to POST, never forwarded to CDN.
                            async with session.get(url, proxy=settings.proxy or None) as response:
                                if response.status != 200:
                                    raise ImageGenError(f"图片下载失败（HTTP {response.status}）。")
                                data = await read_limited(response, settings.max_image_bytes)
                        else:
                            raise ImageGenError("图片数据缺少 b64_json 或 url。")
                        images.append(validate_image(data, settings.max_image_bytes))
                    return images
        except TimeoutError as exc:
            raise ImageGenError(
                "生图请求超时，请稍后重试或增大 timeout；服务端可能已完成生成。"
            ) from exc
        except aiohttp.ClientError as exc:
            raise ImageGenError("无法连接生图服务，请检查 API 地址、网络和代理设置。") from exc

    @staticmethod
    def _check_status(status: int) -> None:
        if 200 <= status < 300:
            return
        messages = {
            400: "请求参数被拒绝，请检查模型、尺寸、质量和提示词。",
            401: "鉴权失败，请配置正确的 api_key。",
            403: "无权使用此模型，请检查账户权限或模型访问资格。",
            404: "接口或模型不存在，请检查 base_url、images_path 和 model。",
            429: "请求受限或余额不足，请检查配额并稍后再试。",
        }
        message = messages.get(status, "生图服务暂不可用，请检查服务状态。")
        raise ImageGenError(f"{message}（HTTP {status}）")
