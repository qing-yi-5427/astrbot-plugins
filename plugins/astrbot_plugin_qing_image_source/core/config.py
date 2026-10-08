from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


def _section(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key, {})
    return value if isinstance(value, Mapping) else {}


def _strings(value: Any, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    result = [str(item).strip() for item in value if str(item).strip()]
    return result or list(default)


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, parsed))


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, parsed))


@dataclass(slots=True)
class TriggerSettings:
    require_at_in_group: bool = False
    enable_onebot_reply_fallback: bool = True
    match_mode: str = "contains"
    case_sensitive: bool = False
    auto_keywords: list[str] = field(
        default_factory=lambda: ["搜图", "找出处", "查来源", "link"]
    )
    saucenao_keywords: list[str] = field(default_factory=lambda: ["sauce", "saucenao"])
    tracemoe_keywords: list[str] = field(
        default_factory=lambda: ["trace", "tracemoe", "搜番"]
    )


@dataclass(slots=True)
class SearchSettings:
    enable_saucenao: bool = True
    saucenao_api_key: str = ""
    enable_ascii2d: bool = True
    ascii2d_session_id: str = ""
    ascii2d_cf_clearance: str = ""
    enable_tracemoe: bool = True
    tracemoe_api_key: str = ""
    sauce_high: float = 85.0
    sauce_possible: float = 70.0
    trace_high: float = 90.0
    trace_possible: float = 87.0
    max_results: int = 3
    timeout_seconds: int = 25
    max_concurrency: int = 2
    cooldown_seconds: int = 5


@dataclass(slots=True)
class ReactionSettings:
    enabled: bool = True
    emoji_id: int = 128076
    emoji_type: int = 2


@dataclass(slots=True)
class CacheSettings:
    enabled: bool = True
    ttl_hours: int = 12


@dataclass(slots=True)
class NetworkSettings:
    proxy_url: str = ""
    saucenao_proxy_url: str = ""
    ascii2d_proxy_url: str = ""
    ascii2d_user_agent: str = ""
    tracemoe_proxy_url: str = ""


@dataclass(slots=True)
class SafetySettings:
    max_image_mb: int = 10
    max_pixels: int = 40_000_000
    saucenao_hide: int = 2
    allow_adult_links_in_private: bool = False


@dataclass(slots=True)
class OutputSettings:
    force_text_message: bool = True


@dataclass(slots=True)
class PluginSettings:
    trigger: TriggerSettings = field(default_factory=TriggerSettings)
    search: SearchSettings = field(default_factory=SearchSettings)
    reaction: ReactionSettings = field(default_factory=ReactionSettings)
    cache: CacheSettings = field(default_factory=CacheSettings)
    network: NetworkSettings = field(default_factory=NetworkSettings)
    safety: SafetySettings = field(default_factory=SafetySettings)
    output: OutputSettings = field(default_factory=OutputSettings)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> PluginSettings:
        data = data or {}
        trigger = _section(data, "trigger")
        search = _section(data, "search")
        reaction = _section(data, "reaction")
        cache = _section(data, "cache")
        network = _section(data, "network")
        safety = _section(data, "safety")
        output = _section(data, "output")

        match_mode = str(trigger.get("match_mode", "contains")).lower()
        if match_mode not in {"contains", "exact"}:
            match_mode = "contains"

        return cls(
            trigger=TriggerSettings(
                require_at_in_group=bool(trigger.get("require_at_in_group", False)),
                enable_onebot_reply_fallback=bool(
                    trigger.get("enable_onebot_reply_fallback", True)
                ),
                match_mode=match_mode,
                case_sensitive=bool(trigger.get("case_sensitive", False)),
                auto_keywords=_strings(
                    trigger.get("auto_keywords"),
                    ["搜图", "找出处", "查来源", "link"],
                ),
                saucenao_keywords=_strings(
                    trigger.get("saucenao_keywords"), ["sauce", "saucenao"]
                ),
                tracemoe_keywords=_strings(
                    trigger.get("tracemoe_keywords"),
                    ["trace", "tracemoe", "搜番"],
                ),
            ),
            search=SearchSettings(
                enable_saucenao=bool(search.get("enable_saucenao", True)),
                saucenao_api_key=str(search.get("saucenao_api_key", "")).strip(),
                enable_ascii2d=bool(search.get("enable_ascii2d", True)),
                ascii2d_session_id=str(search.get("ascii2d_session_id", "")).strip(),
                ascii2d_cf_clearance=str(
                    search.get("ascii2d_cf_clearance", "")
                ).strip(),
                enable_tracemoe=bool(search.get("enable_tracemoe", True)),
                tracemoe_api_key=str(search.get("tracemoe_api_key", "")).strip(),
                sauce_high=_bounded_float(search.get("sauce_high"), 85.0, 0, 100),
                sauce_possible=_bounded_float(
                    search.get("sauce_possible"), 70.0, 0, 100
                ),
                trace_high=_bounded_float(search.get("trace_high"), 90.0, 0, 100),
                trace_possible=_bounded_float(
                    search.get("trace_possible"), 87.0, 0, 100
                ),
                max_results=_bounded_int(search.get("max_results"), 3, 1, 10),
                timeout_seconds=_bounded_int(search.get("timeout_seconds"), 25, 5, 120),
                max_concurrency=_bounded_int(search.get("max_concurrency"), 2, 1, 20),
                cooldown_seconds=_bounded_int(
                    search.get("cooldown_seconds"), 5, 0, 3600
                ),
            ),
            reaction=ReactionSettings(
                enabled=bool(reaction.get("enabled", True)),
                emoji_id=_bounded_int(
                    reaction.get("emoji_id"), 128076, 1, 2_147_483_647
                ),
                emoji_type=_bounded_int(reaction.get("emoji_type"), 2, 1, 2),
            ),
            cache=CacheSettings(
                enabled=bool(cache.get("enabled", True)),
                ttl_hours=_bounded_int(cache.get("ttl_hours"), 12, 1, 720),
            ),
            network=NetworkSettings(
                proxy_url=str(network.get("proxy_url", "")).strip(),
                saucenao_proxy_url=str(network.get("saucenao_proxy_url", "")).strip(),
                ascii2d_proxy_url=str(network.get("ascii2d_proxy_url", "")).strip(),
                ascii2d_user_agent=str(network.get("ascii2d_user_agent", "")).strip(),
                tracemoe_proxy_url=str(network.get("tracemoe_proxy_url", "")).strip(),
            ),
            safety=SafetySettings(
                max_image_mb=_bounded_int(safety.get("max_image_mb"), 10, 1, 50),
                max_pixels=_bounded_int(
                    safety.get("max_pixels"), 40_000_000, 1_000_000, 100_000_000
                ),
                saucenao_hide=_bounded_int(safety.get("saucenao_hide"), 2, 0, 3),
                allow_adult_links_in_private=bool(
                    safety.get("allow_adult_links_in_private", False)
                ),
            ),
            output=OutputSettings(
                force_text_message=bool(output.get("force_text_message", True)),
            ),
        )
