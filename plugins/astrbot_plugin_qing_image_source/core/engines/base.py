from __future__ import annotations

from typing import Protocol

from ..models import EngineOutcome, ImagePayload


class SearchEngine(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    async def search(self, image: ImagePayload) -> EngineOutcome: ...
