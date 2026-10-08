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
    ascii2d = FakeEngine("Ascii2D", EngineOutcome("Ascii2D"))
    cache = ResultCache(tmp_path / "cache.json", enabled=True, ttl_seconds=60)
    orchestrator = SearchOrchestrator(
        sauce,
        trace,
        cache,
        max_results=3,
        ascii2d=ascii2d,
    )
    report = await orchestrator.search(image())
    assert len(report.hits) == 1
    assert sauce.calls == 1
    assert trace.calls == 0
    assert ascii2d.calls == 0

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


@pytest.mark.asyncio
async def test_ascii2d_runs_only_after_other_engines_lack_high_confidence_link(
    tmp_path: Path,
):
    sauce = FakeEngine("SauceNAO", EngineOutcome("SauceNAO"))
    trace = FakeEngine("trace.moe", EngineOutcome("trace.moe"))
    ascii2d = FakeEngine(
        "Ascii2D",
        EngineOutcome(
            "Ascii2D",
            [
                SearchHit(
                    engine="Ascii2D",
                    kind="illustration",
                    title="Fallback",
                    work_url="https://example/fallback",
                    confidence=Confidence.POSSIBLE,
                )
            ],
        ),
    )
    cache = ResultCache(tmp_path / "cache.json", enabled=False, ttl_seconds=60)
    orchestrator = SearchOrchestrator(
        sauce,
        trace,
        cache,
        max_results=3,
        ascii2d=ascii2d,
    )
    report = await orchestrator.search(image())
    assert ascii2d.calls == 1
    assert report.hits[0].engine == "Ascii2D"


@pytest.mark.asyncio
async def test_trace_high_confidence_result_skips_ascii2d(tmp_path: Path):
    sauce = FakeEngine("SauceNAO", EngineOutcome("SauceNAO"))
    trace = FakeEngine(
        "trace.moe",
        EngineOutcome(
            "trace.moe",
            [
                SearchHit(
                    engine="trace.moe",
                    kind="anime",
                    title="Anime",
                    work_url="https://anilist.co/anime/1",
                    confidence=Confidence.HIGH,
                )
            ],
        ),
    )
    ascii2d = FakeEngine("Ascii2D", EngineOutcome("Ascii2D"))
    cache = ResultCache(tmp_path / "cache.json", enabled=False, ttl_seconds=60)
    orchestrator = SearchOrchestrator(
        sauce,
        trace,
        cache,
        max_results=3,
        ascii2d=ascii2d,
    )
    await orchestrator.search(image())
    assert ascii2d.calls == 0
