from __future__ import annotations

import asyncio
import hashlib
import io
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .models import ImagePayload


class ImageResolutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ImageSelection:
    component: Any
    origin: str
    total_count: int


def _component_type(component: Any) -> str:
    if isinstance(component, dict):
        return str(component.get("type", "")).lower()
    return component.__class__.__name__.lower()


def _reply_chain(component: Any) -> list[Any]:
    if isinstance(component, dict):
        chain = component.get("chain")
        data = component.get("data", {})
        if chain is None and isinstance(data, dict):
            chain = data.get("chain")
    else:
        chain = getattr(component, "chain", None)
    return list(chain) if isinstance(chain, (list, tuple)) else []


def _images(components: Iterable[Any]) -> list[Any]:
    return [item for item in components if _component_type(item) == "image"]


def select_image_component(components: list[Any]) -> ImageSelection | None:
    quoted: list[Any] = []
    for component in components:
        if _component_type(component) == "reply":
            quoted.extend(_images(_reply_chain(component)))
    current = _images(components)
    total = len(quoted) + len(current)
    if quoted:
        return ImageSelection(quoted[0], "reply", total)
    if current:
        return ImageSelection(current[0], "current", total)
    return None


def _validate_and_read(path: Path, max_bytes: int, max_pixels: int) -> ImagePayload:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ImageResolutionError("无法读取图片文件") from exc
    if size <= 0:
        raise ImageResolutionError("图片文件为空")
    if size > max_bytes:
        raise ImageResolutionError(f"图片超过 {max_bytes // 1024 // 1024} MiB 限制")

    try:
        content = path.read_bytes()
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            image_format = (image.format or "").upper()
            animated = bool(getattr(image, "is_animated", False))
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise ImageResolutionError("图片像素尺寸过大")
            if image_format not in {"JPEG", "PNG", "WEBP", "GIF"}:
                raise ImageResolutionError("仅支持 JPEG、PNG、WebP 和 GIF 图片")
            if animated:
                image.seek(0)
                frame = image.convert("RGB")
                output = io.BytesIO()
                frame.save(output, format="JPEG", quality=95)
                content = output.getvalue()
                image_format = "JPEG"
            else:
                image.verify()
    except ImageResolutionError:
        raise
    except (OSError, UnidentifiedImageError) as exc:
        raise ImageResolutionError("图片格式无效或文件已损坏") from exc

    mime = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
        "GIF": "image/gif",
    }[image_format]
    suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}[
        image_format
    ]
    return ImagePayload(
        content=content,
        filename=f"search{suffix}",
        mime_type=mime,
        sha256=hashlib.sha256(content).hexdigest(),
    )


async def materialize_image(
    component: Any, *, max_bytes: int, max_pixels: int
) -> ImagePayload:
    converter = getattr(component, "convert_to_file_path", None)
    if not callable(converter):
        raise ImageResolutionError("当前平台没有提供可读取的图片文件")
    try:
        value = converter()
        path_value = await value if hasattr(value, "__await__") else value
    except Exception as exc:
        raise ImageResolutionError("图片下载或转换失败") from exc
    path_text = str(path_value or "").strip()
    path_text = path_text.removeprefix("file://")
    if not path_text or path_text.startswith(("http://", "https://")):
        raise ImageResolutionError("当前平台未能将图片安全地转换为本地文件")
    return await asyncio.to_thread(
        _validate_and_read, Path(path_text), max_bytes, max_pixels
    )
