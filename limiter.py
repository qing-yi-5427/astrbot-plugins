"""Pure, offline image pruning; no database, file or network access."""

from dataclasses import dataclass
import math
from typing import Any, Callable

MB = 1_000_000
DEFAULT_LIMIT_MB = 10.0
REMOVED_IMAGE_TEXT = "[图片已按上下文图片大小限制移除，当前无法查看该图片。]"


def configured_limit_bytes(value: Any) -> int:
    """Invalid limits fall back to 10 MB; small positive limits remain valid."""
    try:
        if isinstance(value, bool):
            raise ValueError("Boolean is not a size")
        size = float(value)
        if not math.isfinite(size) or size <= 0:
            raise ValueError("Size must be finite and positive")
        return max(1, int(size * MB))
    except (ValueError, TypeError, OverflowError):
        return int(DEFAULT_LIMIT_MB * MB)


def field(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def image_data_bytes(part: Any) -> int | None:
    """Count embedded image strings as sent, including Base64 overhead.

    Remote URLs count only their URL bytes; fetching them is deliberately avoided.
    Audio, text, tool arguments and non-image attachments are never counted.
    """
    kind = field(part, "type")
    if kind in ("image_url", "input_image"):
        value = field(part, "image_url")
        if not isinstance(value, str):
            value = field(value, "url")
    elif kind == "image":
        source = field(part, "source")
        value = field(source, "data") if field(source, "type") == "base64" else None
    else:
        return None
    if not isinstance(value, str):
        return None
    return len(value) if value.isascii() else len(value.encode("utf-8"))


@dataclass(frozen=True)
class TrimResult:
    before_bytes: int
    after_bytes: int
    before_count: int
    removed_count: int


def trim_context_images(
    messages: list,
    max_bytes: int,
    typed_text_factory: Callable[[str], Any] | None = None,
) -> TrimResult:
    """Replace oldest image parts in-place, preserving messages and other parts.

    Both ProviderRequest dictionaries and assembled AstrBot Message models work.
    The typed factory is required when pruning model-based messages.
    """
    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    candidates = []
    total = 0
    for message in messages:
        content = field(message, "content")
        if not isinstance(content, list):
            continue
        for index, part in enumerate(content):
            size = image_data_bytes(part)
            if size is not None:
                typed = not isinstance(message, dict) or not isinstance(part, dict)
                candidates.append((content, index, part, size, typed))
                total += size
    remaining = total
    removed = 0
    for content, index, part, size, typed in candidates:
        if remaining <= max_bytes:
            break
        if typed:
            if typed_text_factory is None:
                raise TypeError("Model content requires typed_text_factory")
            replacement = typed_text_factory(REMOVED_IMAGE_TEXT)
        else:
            replacement = {
                "type": "input_text" if field(part, "type") == "input_image" else "text",
                "text": REMOVED_IMAGE_TEXT,
            }
        content[index] = replacement
        remaining -= size
        removed += 1
    return TrimResult(total, remaining, len(candidates), removed)
