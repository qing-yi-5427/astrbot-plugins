from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .config import TriggerSettings


@dataclass(frozen=True, slots=True)
class TriggerMatch:
    matched: bool
    route: str = "auto"
    keyword: str = ""


def _component_type(component: Any) -> str:
    if isinstance(component, dict):
        return str(component.get("type", "")).lower()
    return component.__class__.__name__.lower()


def extract_plain_text(components: Iterable[Any]) -> str:
    parts: list[str] = []
    for component in components:
        if _component_type(component) not in {"plain", "text"}:
            continue
        if isinstance(component, dict):
            text = component.get("text", "")
            if not text and isinstance(component.get("data"), dict):
                text = component["data"].get("text", "")
        else:
            text = getattr(component, "text", "")
        if text:
            parts.append(str(text))
    return " ".join(parts).strip()


def has_bot_at(components: Iterable[Any], self_id: str) -> bool:
    expected = str(self_id)
    for component in components:
        if _component_type(component) != "at":
            continue
        if isinstance(component, dict):
            data = component.get("data", {})
            target = component.get("qq", component.get("id", ""))
            if isinstance(data, dict):
                target = data.get("qq", data.get("id", target))
        else:
            target = getattr(component, "qq", getattr(component, "id", ""))
        if str(target) == expected:
            return True
    return False


class TriggerMatcher:
    def __init__(self, settings: TriggerSettings):
        self.settings = settings

    def match(
        self,
        components: list[Any],
        *,
        is_group: bool,
        self_id: str,
    ) -> TriggerMatch:
        if (
            is_group
            and self.settings.require_at_in_group
            and not has_bot_at(components, self_id)
        ):
            return TriggerMatch(False)

        text = extract_plain_text(components)
        if not text:
            return TriggerMatch(False)
        haystack = text if self.settings.case_sensitive else text.casefold()

        groups = (
            ("saucenao", self.settings.saucenao_keywords),
            ("tracemoe", self.settings.tracemoe_keywords),
            ("auto", self.settings.auto_keywords),
        )
        for route, keywords in groups:
            for keyword in keywords:
                needle = keyword if self.settings.case_sensitive else keyword.casefold()
                if self.settings.match_mode == "exact":
                    matched = haystack.strip(" \t\r\n,，。.!！?？:：") == needle
                else:
                    matched = needle in haystack
                if matched:
                    return TriggerMatch(True, route, keyword)
        return TriggerMatch(False)
