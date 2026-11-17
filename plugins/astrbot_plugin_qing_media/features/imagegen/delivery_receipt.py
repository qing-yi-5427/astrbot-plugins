"""Bounded, read-only reconciliation of ambiguous OneBot image receipts."""

import asyncio
import hashlib
import math
import re
import time

CHECK_TIMEOUT = 8
HISTORY_COUNT = 30
_FILE_HASH = re.compile(r"([0-9a-f]{32})\.(?:png|jpe?g|webp|gif|bmp|image)", re.I)
_CQ_IMAGE = re.compile(r"\[CQ:image,([^\[\]]+)\]")


def receipt_is_ambiguous(exc: Exception) -> bool:
    # Do not stringify adapter exceptions: they may contain credentials/base64.
    if isinstance(exc, TimeoutError):
        return True
    result = getattr(exc, "result", None)
    if not isinstance(result, dict):
        return False
    return any(
        isinstance(result.get(key), str)
        and result[key].strip().lower() == "waitforselfecho timeout"
        for key in ("message", "wording")
    )


def image_fingerprint(data: bytes) -> str:
    # QQ identifies original image bytes with MD5; not a security/authentication hash.
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def _segments(message):
    if isinstance(message, list):
        return message
    if isinstance(message, str):
        segments = []
        for match in _CQ_IMAGE.finditer(message):
            fields = {}
            for field in match[1].split(","):
                key, sep, value = field.partition("=")
                if sep:
                    for encoded, decoded in (
                        ("&#44;", ","),
                        ("&#91;", "["),
                        ("&#93;", "]"),
                        ("&amp;", "&"),
                    ):
                        value = value.replace(encoded, decoded)
                    fields[key] = value
            segments.append({"type": "image", "data": fields})
        return segments
    return []


def _same_image(segment, fingerprint: str, size: int) -> bool:
    if not isinstance(segment, dict) or segment.get("type") != "image":
        return False
    data = segment.get("data")
    if not isinstance(data, dict):
        return False
    # LLBot's original image filename is MD5.ext. Never download images or
    # interpret arbitrary URL fragments as fingerprints.
    file = data.get("file")
    match = _FILE_HASH.fullmatch(file) if isinstance(file, str) else None
    if not match or match[1].lower() != fingerprint:
        return False
    try:
        return int(data["file_size"]) == size
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def matching_receipt(
    history,
    *,
    group: str,
    self_id: str,
    fingerprint: str,
    size: int,
    started: float,
    ended: float,
    used_ids: set[str],
) -> str | None:
    if not isinstance(history, dict) or not isinstance(history.get("messages"), list):
        return None
    matches = set()
    for message in history["messages"]:
        if not isinstance(message, dict):
            continue
        if (
            message.get("message_type") != "group"
            or str(message.get("group_id")) != group
            or str(message.get("user_id")) != self_id
        ):
            continue
        try:
            timestamp = float(message["time"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        # QQ timestamps have whole-second precision. Old identical pictures,
        # future records and non-finite timestamps must not confirm this attempt.
        if (
            not math.isfinite(timestamp)
            or not int(started) <= timestamp <= int(ended) + 2
        ):
            continue
        msg_id = message.get("message_id")
        if isinstance(msg_id, bool) or not isinstance(msg_id, (str, int)):
            continue
        identity = str(msg_id)
        if not identity or identity in used_ids:
            continue
        if any(
            _same_image(seg, fingerprint, size)
            for seg in _segments(message.get("message"))
        ):
            matches.add(identity)
    # Multiple plausible deliveries are ambiguous, never guess the latest one.
    return next(iter(matches)) if len(matches) == 1 else None


async def confirm_group_delivery(
    event, data: bytes, started: float, used_ids: set[str]
) -> bool:
    """Reuse the existing OneBot connection; no WebUI credentials or send calls."""
    try:
        get_group = getattr(event, "get_group_id", None)
        get_self = getattr(event, "get_self_id", None)
        action = getattr(getattr(event, "bot", None), "call_action", None)
        if not callable(get_group) or not callable(get_self) or not callable(action):
            return False
        group, self_id = str(get_group()), str(get_self())
        if not group.isdecimal() or not self_id.isdecimal():
            return False
        fingerprint = image_fingerprint(data)
        async with asyncio.timeout(CHECK_TIMEOUT):
            for attempt in range(2):
                history = await action(
                    "get_group_msg_history",
                    group_id=int(group),
                    count=HISTORY_COUNT,
                    self_id=int(self_id),
                )
                identity = matching_receipt(
                    history,
                    group=group,
                    self_id=self_id,
                    fingerprint=fingerprint,
                    size=len(data),
                    started=started,
                    ended=time.time(),
                    used_ids=used_ids,
                )
                if identity is not None:
                    used_ids.add(identity)
                    return True
                if attempt == 0:
                    await asyncio.sleep(0.5)
    except Exception:
        # Unavailable history is not proof of failed delivery. Cancellation is
        # intentionally not swallowed (CancelledError inherits BaseException).
        return False
    return False
