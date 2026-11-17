from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class OneBotReplyImage:
    source: str
    total_count: int


def _component_type(component: Any) -> str:
    if isinstance(component, dict):
        return str(component.get("type", "")).lower()
    return component.__class__.__name__.lower()


def _reply_id(components: list[Any]) -> str:
    for component in components:
        if _component_type(component) != "reply":
            continue
        if isinstance(component, dict):
            data = component.get("data", {})
            value = component.get("id", "")
            if isinstance(data, dict):
                value = data.get("id", value)
        else:
            value = getattr(component, "id", "")
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _extract_image_sources(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("data"), dict):
        payload = payload["data"]
    message = payload.get("message", [])
    if not isinstance(message, list):
        return []
    sources: list[str] = []
    for segment in message:
        if (
            not isinstance(segment, dict)
            or str(segment.get("type", "")).lower() != "image"
        ):
            continue
        data = segment.get("data", {})
        if not isinstance(data, dict):
            data = {}
        source = (
            data.get("url")
            or data.get("file")
            or segment.get("url")
            or segment.get("file")
        )
        text = str(source or "").strip()
        if text and text not in sources:
            sources.append(text)
    return sources


async def fetch_onebot_reply_image(
    event: Any, components: list[Any]
) -> OneBotReplyImage | None:
    message_id = _reply_id(components)
    if not message_id:
        return None
    bot = getattr(event, "bot", None)
    call_action = getattr(bot, "call_action", None)
    if not callable(call_action):
        return None
    try:
        numeric_id: int | str = int(message_id)
    except ValueError:
        numeric_id = message_id
    try:
        payload = await call_action("get_msg", message_id=numeric_id)
    except Exception:  # noqa: BLE001 - optional adapter-specific fallback
        return None
    sources = _extract_image_sources(payload)
    if not sources:
        return None
    return OneBotReplyImage(sources[0], len(sources))
