from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from .models import SearchReport


def _expiry(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class ResultCache:
    def __init__(self, path: Path, *, enabled: bool, ttl_seconds: int):
        self.path = path
        self.enabled = enabled
        self.ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    async def get(self, key: str) -> SearchReport | None:
        if not self.enabled:
            return None
        async with self._lock:
            data = await asyncio.to_thread(self._read)
            item = data.get(key)
            if not isinstance(item, dict):
                return None
            if _expiry(item.get("expires_at")) <= time.time():
                data.pop(key, None)
                await asyncio.to_thread(self._write, data)
                return None
            report_data = item.get("report", {})
            if not isinstance(report_data, dict):
                return None
            report = SearchReport.from_dict(report_data)
            report.cache_hit = True
            return report

    async def set(self, key: str, report: SearchReport) -> None:
        if not self.enabled:
            return
        async with self._lock:
            data = await asyncio.to_thread(self._read)
            now = time.time()
            data = {
                cache_key: value
                for cache_key, value in data.items()
                if isinstance(value, dict) and _expiry(value.get("expires_at")) > now
            }
            data[key] = {
                "expires_at": now + self.ttl_seconds,
                "report": report.to_dict(),
            }
            await asyncio.to_thread(self._write, data)

    async def clear(self) -> int:
        async with self._lock:
            data = await asyncio.to_thread(self._read)
            count = len(data)
            if self.path.exists():
                await asyncio.to_thread(self.path.unlink)
            return count

    async def stats(self) -> dict[str, int | bool]:
        if not self.enabled:
            return {"enabled": False, "entries": 0}
        async with self._lock:
            data = await asyncio.to_thread(self._read)
            now = time.time()
            entries = sum(
                1
                for value in data.values()
                if isinstance(value, dict) and _expiry(value.get("expires_at")) > now
            )
            return {"enabled": True, "entries": entries}
