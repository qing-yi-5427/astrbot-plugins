from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import aiohttp


class SearchEngineError(RuntimeError):
    def __init__(self, engine: str, message: str):
        super().__init__(message)
        self.engine = engine
        self.message = message


async def post_json_with_retry(
    session: aiohttp.ClientSession,
    *,
    engine: str,
    url: str,
    form_factory: Callable[[], aiohttp.FormData],
    headers: dict[str, str] | None = None,
    proxy: str = "",
    retries: int = 2,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            async with session.post(
                url,
                data=form_factory(),
                headers=headers,
                proxy=proxy or None,
            ) as response:
                if response.status in {429, 503}:
                    if attempt >= retries:
                        raise SearchEngineError(
                            engine, f"服务暂时繁忙（HTTP {response.status}）"
                        )
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        delay = min(5.0, max(0.25, float(retry_after)))
                    except ValueError:
                        delay = min(5.0, float(2**attempt))
                    await asyncio.sleep(delay)
                    continue
                if response.status >= 400:
                    raise SearchEngineError(
                        engine, f"请求失败（HTTP {response.status}）"
                    )
                payload = await response.json(content_type=None)
                if not isinstance(payload, dict):
                    raise SearchEngineError(engine, "服务返回了无法识别的数据")
                return payload
        except SearchEngineError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_error = exc
            if attempt < retries:
                await asyncio.sleep(min(5.0, float(2**attempt)))
                continue
    raise SearchEngineError(engine, "网络连接失败") from last_error
