"""Bounded service capability probes; never send reply text or credentials."""

import asyncio
import json
import time
from urllib.parse import urlsplit, urlunsplit

import aiohttp

MAX_HEALTH_BYTES = 16384
CACHE_SECONDS = 60


def normalize_endpoint(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("Invalid service endpoint")
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or any(c.isspace() or ord(c) < 32 for c in value)
    ):
        raise ValueError("Invalid service endpoint")
    # Validate the port without displaying the private endpoint in errors.
    try:
        _ = parts.port
    except ValueError as exc:
        raise ValueError("Invalid service endpoint") from exc
    path = parts.path.rstrip("/")
    path = path.removesuffix("/generate")
    if not path.endswith("/text2img"):
        path += "/text2img"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


async def probe_health(endpoint, protocol_key="style_protocol"):
    timeout = aiohttp.ClientTimeout(total=4, connect=2, sock_read=2)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout, trust_env=False) as session,
            session.get(endpoint + "/health", allow_redirects=False) as response,
        ):
            if response.status != 200:
                return False, "服务健康检查失败或发生重定向，未启用主题注入。"
            if (
                response.content_length is not None
                and response.content_length > MAX_HEALTH_BYTES
            ):
                return False, "服务健康响应过大，未启用主题注入。"
            data = bytearray()
            async for chunk in response.content.iter_chunked(4096):
                data.extend(chunk)
                if len(data) > MAX_HEALTH_BYTES:
                    return False, "服务健康响应过大，未启用主题注入。"
        payload = json.loads(data)
        protocol = payload.get(protocol_key) if isinstance(payload, dict) else None
        if type(protocol) is not int or protocol != 1:
            return (
                False,
                f"服务未声明 {protocol_key}: 1，旧服务不支持主题切换，未启用主题注入。",
            )
        return (
            True,
            f"服务已确认 {protocol_key}: 1；实际效果仍需符合 LLM/模板/文转图开关条件。",
        )
    except (aiohttp.ClientError, TimeoutError, ValueError, UnicodeError):
        return (
            False,
            f"无法验证服务 {protocol_key} 能力，请检查管理员配置或升级服务；未启用主题注入。",
        )


class ServiceCapabilities:
    def __init__(self, strategy_getter=None, fallback_url="", probe=probe_health):
        self.strategy_getter = strategy_getter
        self.fallback_url = fallback_url
        if fallback_url:
            normalize_endpoint(fallback_url)
        self.probe = probe
        self.cache = {}
        self.lock = asyncio.Lock()

    def endpoints(self, strategy=None, endpoint=None):
        if strategy is None and self.strategy_getter is not None:
            strategy = self.strategy_getter()
        # These are the actual random/fallback destinations used by AstrBot.
        # BASE_RENDER_URL is NOT used while the endpoints list is nonempty.
        # AstrBot 4.29.4 adds an explicit per-call endpoint that supersedes the pool.
        candidates = [endpoint] if endpoint else getattr(strategy, "endpoints", None)
        if not candidates:
            base = getattr(strategy, "BASE_RENDER_URL", None)
            candidates = (
                [base] if base else ([self.fallback_url] if self.fallback_url else [])
            )
        if not isinstance(candidates, (list, tuple)) or not 0 < len(candidates) <= 16:
            raise ValueError("No usable service endpoints")
        return tuple(dict.fromkeys(normalize_endpoint(value) for value in candidates))

    async def check(self, strategy=None, endpoint=None):
        try:
            endpoints = self.endpoints(strategy, endpoint)
        except (ValueError, TypeError, AttributeError):
            return (
                False,
                "未找到有效文转图服务地址，未启用主题注入；请检查 AstrBot 网络文转图配置。",
            )
        async with self.lock:
            cached = self.cache.get(endpoints)
            if cached is not None and time.monotonic() - cached[0] < CACHE_SECONDS:
                return cached[1]
            results = await asyncio.gather(
                *(self.probe(endpoint) for endpoint in endpoints)
            )
            # Never regard an empty candidate list as supported. Every actual
            # fallback destination must be safe before sending custom metadata.
            result = next((item for item in results if not item[0]), results[0])
            if len(self.cache) >= 8:
                self.cache.clear()
            self.cache[endpoints] = (time.monotonic(), result)
            return result

    async def supports(self, strategy, endpoint=None):
        return (await self.check(strategy, endpoint))[0]

    async def describe(self, endpoint=None):
        return (await self.check(endpoint=endpoint))[1]
