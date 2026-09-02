from pathlib import Path

import pytest

from core.cache import ResultCache
from core.http_client import SearchEngineError
from core.models import Confidence, EngineOutcome, ImagePayload, SearchHit
from core.orchestrator import SearchOrchestrator


class FakeEngine:
    def __init__(self, name, outcome):
        self.name = name
        self.outcome = outcome
        self.calls = 0

    async def search(self, image):
        self.calls += 1
        return self.outcome


class RaisingEngine:
    def __init__(self, name):
        self.name = name
        self.calls = 0

    async def search(self, image):
        self.calls += 1
        raise SearchEngineError(self.name, "请求失败（HTTP 403）")


def image():
    return ImagePayload(b"image", "a.jpg", "image/jpeg", "abc")


@pytest.mark.asyncio
async def test_high_confidence_illustration_stops_before_trace(tmp_path: Path):
    sauce = FakeEngine(
        "SauceNAO",
        EngineOutcome(
            "SauceNAO",
            [
                SearchHit(
                    engine="SauceNAO",
                    kind="illustration",
                    title="A",
                    work_url="https://example/a",
                    similarity=95,
                    confidence=Confidence.HIGH,
                )
            ],
        ),
    )
    trace = FakeEngine("trace.moe", EngineOutcome("trace.moe"))
    cache = ResultCache(tmp_path / "cache.json", enabled=True, ttl_seconds=60)
    orchestrator = SearchOrchestrator(sauce, trace, cache, max_results=3)
    report = await orchestrator.search(image())
    assert len(report.hits) == 1
    assert sauce.calls == 1
    assert trace.calls == 0

    cached = await orchestrator.search(image())
    assert cached.cache_hit
    assert sauce.calls == 1


@pytest.mark.asyncio
async def test_anime_sauce_result_also_calls_trace(tmp_path: Path):
    sauce = FakeEngine(
        "SauceNAO",
        EngineOutcome(
            "SauceNAO",
            [
                SearchHit(
                    engine="SauceNAO",
                    kind="anime",
                    title="A",
                    work_url="https://example/a",
                    similarity=96,
                    confidence=Confidence.HIGH,
                )
            ],
        ),
    )
    trace = FakeEngine("trace.moe", EngineOutcome("trace.moe"))
    cache = ResultCache(tmp_path / "cache.json", enabled=False, ttl_seconds=60)
    orchestrator = SearchOrchestrator(sauce, trace, cache, max_results=3)
    await orchestrator.search(image())
    assert trace.calls == 1


@pytest.mark.asyncio
async def test_engine_errors_are_named_and_not_cached(tmp_path: Path):
    sauce = RaisingEngine("SauceNAO")
    trace = FakeEngine("trace.moe", EngineOutcome("trace.moe"))
    cache = ResultCache(tmp_path / "cache.json", enabled=True, ttl_seconds=60)
    orchestrator = SearchOrchestrator(sauce, trace, cache, max_results=3)

    first = await orchestrator.search(image(), route="saucenao")
    second = await orchestrator.search(image(), route="saucenao")

    assert first.warnings == ["SauceNAO：请求失败（HTTP 403）"]
    assert not second.cache_hit
    assert sauce.calls == 2
