"""Bounded, validated local images and asynchronous fixed-origin QQ avatars."""

import asyncio
import base64
import io
import os
import re
import stat
import warnings
from pathlib import Path

import aiohttp
from PIL import Image

MAX_AVATAR_BYTES = 512000
MAX_IMAGE_PIXELS = 1024 * 1024
MAX_IMAGE_DIMENSION = 1024
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def validate_image(data: bytes) -> str:
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_AVATAR_BYTES:
        raise ValueError("头像必须为不超过 512000 字节的图片")
    if not (
        data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"))
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    ):
        raise ValueError("仅支持 PNG、JPEG、WebP 头像")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError("Unsupported avatar format")
                if (
                    image.width > MAX_IMAGE_DIMENSION
                    or image.height > MAX_IMAGE_DIMENSION
                    or image.width * image.height > MAX_IMAGE_PIXELS
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise ValueError("Avatar dimensions or frame count exceeded")
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
    except Exception as exc:
        raise ValueError("头像图片无效、损坏、包含动画或尺寸过大") from exc
    return base64.b64encode(data).decode("ascii")


def validate_base64(value: str) -> str:
    if not isinstance(value, str) or len(value) > ((MAX_AVATAR_BYTES + 2) // 3) * 4:
        raise ValueError("Invalid stored avatar")
    if not value:
        return ""
    try:
        data = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError("Invalid stored avatar") from exc
    return validate_image(data)


def read_local_avatar(path: str) -> str:
    # Check the extension before opening any file; never follow symlink files.
    source = Path(path).expanduser()
    if source.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError("本地头像必须为 PNG、JPEG、WebP 图片路径")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(source, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or not 0 < info.st_size <= MAX_AVATAR_BYTES
            ):
                raise ValueError("本地头像不是有效的限长普通文件")
            data = stream.read(MAX_AVATAR_BYTES + 1)
    except OSError as exc:
        raise ValueError("本地头像无法读取") from exc
    return validate_image(data)


def qq_avatar_url(qq: str) -> str:
    if not isinstance(qq, str) or re.fullmatch(r"[1-9][0-9]{4,11}", qq) is None:
        raise ValueError("QQ 号须为 5 至 12 位非零开头的数字，不接受 URL")
    return f"https://q1.qlogo.cn/g?b=qq&nk={qq}&s=640"


async def download_qq_avatar(qq: str) -> str:
    url = qq_avatar_url(qq)
    timeout = aiohttp.ClientTimeout(total=10, connect=4, sock_read=4)
    try:
        # Do not trust proxy environment variables or follow redirects elsewhere.
        async with (
            aiohttp.ClientSession(timeout=timeout, trust_env=False) as session,
            session.get(url, allow_redirects=False) as response,
        ):
            if response.status != 200:
                raise ValueError("QQ 头像请求失败或发生重定向")
            if response.content_type not in {"image/png", "image/jpeg", "image/webp"}:
                raise ValueError("QQ 头像响应不是支持的图片")
            if (
                response.content_length is not None
                and response.content_length > MAX_AVATAR_BYTES
            ):
                raise ValueError("QQ 头像超过大小限制")
            data = bytearray()
            async for chunk in response.content.iter_chunked(16384):
                data.extend(chunk)
                if len(data) > MAX_AVATAR_BYTES:
                    raise ValueError("QQ 头像超过大小限制")
        return await asyncio.to_thread(validate_image, bytes(data))
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise ValueError("QQ 头像下载失败或超时") from exc
