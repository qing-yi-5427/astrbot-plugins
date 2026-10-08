from __future__ import annotations

import asyncio
import time


class Cooldown:
    def __init__(self, seconds: int):
        self.seconds = seconds
        self._last: dict[tuple[str, str], float] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: tuple[str, str]) -> float:
        if self.seconds <= 0:
            return 0.0
        async with self._lock:
            now = time.monotonic()
            previous = self._last.get(key, 0.0)
            remaining = self.seconds - (now - previous)
            if remaining > 0:
                return remaining
            self._last[key] = now
            if len(self._last) > 4096:
                cutoff = now - max(self.seconds * 2, 60)
                self._last = {
                    item_key: timestamp
                    for item_key, timestamp in self._last.items()
                    if timestamp >= cutoff
                }
            return 0.0
