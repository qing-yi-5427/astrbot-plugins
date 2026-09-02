from __future__ import annotations

from .cache import ResultCache
from .http_client import SearchEngineError
from .models import Confidence, EngineOutcome, ImagePayload, SearchHit, SearchReport


class SearchOrchestrator:
    def __init__(self, saucenao, tracemoe, cache: ResultCache, *, max_results: int):
        self.saucenao = saucenao
        self.tracemoe = tracemoe
        self.cache = cache
        self.max_results = max_results
        self.last_quota: dict[str, dict[str, str]] = {}

    async def _call(self, engine, image: ImagePayload) -> EngineOutcome:
        try:
            outcome = await engine.search(image)
        except SearchEngineError as exc:
            return EngineOutcome(engine.name, warning=f"{engine.name}：{exc.message}")
        except Exception:  # noqa: BLE001 - isolate unexpected third-party failures
            return EngineOutcome(
                engine.name, warning=f"{engine.name}：引擎返回异常，已跳过"
            )
        if outcome.quota:
            self.last_quota[outcome.engine] = dict(outcome.quota)
        return outcome

    @staticmethod
    def _should_trace(sauce: EngineOutcome) -> bool:
        if not sauce.hits:
            return True
        best = sauce.hits[0]
        return not (
            best.confidence is Confidence.HIGH
            and best.kind != "anime"
            and bool(best.work_url)
        )

    @staticmethod
    def _merge(outcomes: list[EngineOutcome], limit: int) -> list[SearchHit]:
        engine_priority = {"SauceNAO": 0, "trace.moe": 1}
        confidence_priority = {
            Confidence.HIGH: 0,
            Confidence.POSSIBLE: 1,
            Confidence.LOW: 2,
        }
        hits = [hit for outcome in outcomes for hit in outcome.hits]
        hits.sort(
            key=lambda hit: (
                confidence_priority[hit.confidence],
                engine_priority.get(hit.engine, 9),
                -(hit.similarity or 0),
            )
        )
        unique: list[SearchHit] = []
        seen: set[str] = set()
        for hit in hits:
            key = hit.dedupe_key()
            if key in seen:
                continue
            seen.add(key)
            unique.append(hit)
            if len(unique) >= limit:
                break
        return unique

    async def search(self, image: ImagePayload, route: str = "auto") -> SearchReport:
        route = route if route in {"auto", "saucenao", "tracemoe"} else "auto"
        cache_key = f"v4:{route}:{image.sha256}"
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return cached

        outcomes: list[EngineOutcome] = []
        if route == "saucenao":
            outcomes.append(await self._call(self.saucenao, image))
        elif route == "tracemoe":
            outcomes.append(await self._call(self.tracemoe, image))
        else:
            sauce = await self._call(self.saucenao, image)
            outcomes.append(sauce)
            if self._should_trace(sauce):
                outcomes.append(await self._call(self.tracemoe, image))

        report = SearchReport(
            hits=self._merge(outcomes, self.max_results),
            engines_used=[outcome.engine for outcome in outcomes],
            warnings=[outcome.warning for outcome in outcomes if outcome.warning],
        )
        # Transient API/configuration failures must not poison the result cache.
        if not report.warnings:
            await self.cache.set(cache_key, report)
        return report
