from __future__ import annotations

from typing import Any


def extract_message_id(event: Any) -> int | None:
    message_obj = getattr(event, "message_obj", None)
    raw = getattr(message_obj, "raw_message", None)
    candidates: list[Any] = []
    if isinstance(raw, dict):
        candidates.append(raw.get("message_id"))
    elif raw is not None:
        candidates.append(getattr(raw, "message_id", None))
    candidates.append(getattr(message_obj, "message_id", None))
    for value in candidates:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


async def add_reaction(event: Any, *, emoji_id: int, emoji_type: int) -> bool:
    if not getattr(event, "get_group_id", lambda: "")():
        return False
    bot = getattr(event, "bot", None)
    method = getattr(bot, "set_msg_emoji_like", None)
    message_id = extract_message_id(event)
    if not callable(method) or message_id is None:
        return False
    try:
        await method(
            message_id=message_id,
            emoji_id=emoji_id,
            emoji_type=emoji_type,
            set=True,
        )
        return True
    except Exception:  # noqa: BLE001 - platform adapters expose heterogeneous errors
        return False
