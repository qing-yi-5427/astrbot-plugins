import asyncio
import base64
import io
import threading

import aiohttp
import pytest
from astrbot_plugin_t2i_style import avatars
from conftest import FakeResponse, fake_session, image_bytes
from PIL import Image


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_real_raster_roundtrip(fmt):
    data = image_bytes(fmt)
    encoded = avatars.validate_image(data)
    assert base64.b64decode(encoded) == data
    assert avatars.validate_base64(encoded) == encoded


@pytest.mark.parametrize(
    "qq",
    [
        "https://evil/",
        "1234",
        "012345",
        "１２３４５",
        "12345&x=evil",
        "1234567890123",
        "-12345",
        "12345\n",
        None,
    ],
)
def test_qq_only_ascii_digits(qq):
    with pytest.raises(ValueError):
        avatars.qq_avatar_url(qq)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"<svg/>",
        b"GIF89a",
        b"\x89PNG\r\n\x1a\ncorrupt",
        b"\xff\xd8\xffjunk",
        b"x" * 512001,
    ],
)
def test_reject_invalid_avatar_data(data):
    with pytest.raises(ValueError):
        avatars.validate_image(data)


@pytest.mark.parametrize("size", [(1025, 2), (2, 1025)])
def test_reject_large_dimensions(size):
    with pytest.raises(ValueError):
        avatars.validate_image(image_bytes(size=size))


def test_reject_animation():
    buffer = io.BytesIO()
    first, second = (
        Image.new("RGB", (16, 16), "red"),
        Image.new("RGB", (16, 16), "blue"),
    )
    first.save(
        buffer, "PNG", save_all=True, append_images=[second], duration=100, loop=0
    )
    with pytest.raises(ValueError):
        avatars.validate_image(buffer.getvalue())


def test_read_local_image_and_reject_symlink_bad_suffix(tmp_path, monkeypatch):
    image = tmp_path / "avatar.png"
    data = image_bytes()
    image.write_bytes(data)
    assert base64.b64decode(avatars.read_local_avatar(str(image))) == data
    link = tmp_path / "link.png"
    link.symlink_to(image)
    with pytest.raises(ValueError):
        avatars.read_local_avatar(str(link))
    credential = tmp_path / "credentials.txt"
    credential.write_text("must not be opened")

    def fail_open(*args, **kwargs):
        raise AssertionError("Credentials must not be opened")

    monkeypatch.setattr(avatars.os, "open", fail_open)
    with pytest.raises(ValueError):
        avatars.read_local_avatar(str(credential))


@pytest.mark.asyncio
async def test_download_fixed_origin_timeout_redirect_and_validation(monkeypatch):
    data = image_bytes()
    calls = fake_session(
        monkeypatch, avatars, FakeResponse(data, content_length=len(data))
    )
    value = await avatars.download_qq_avatar("12345678")
    assert base64.b64decode(value) == data
    assert calls[0][1]["trust_env"] is False
    assert calls[0][1]["timeout"].total == 10
    assert calls[1] == (
        "https://q1.qlogo.cn/g?b=qq&nk=12345678&s=640",
        {"allow_redirects": False},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(status=302),
        FakeResponse(status=404),
        FakeResponse(b"<svg/>", content_type="image/svg+xml"),
        FakeResponse(b"x", content_length=512001),
        FakeResponse(b"x" * 512001),
        FakeResponse(b"bad png"),
        TimeoutError(),
        aiohttp.ClientConnectionError(),
    ],
)
async def test_bad_http_avatars_rejected(monkeypatch, response):
    fake_session(monkeypatch, avatars, response)
    with pytest.raises(ValueError):
        await avatars.download_qq_avatar("123456")


@pytest.mark.asyncio
async def test_decoder_does_not_block_event_loop(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def delayed(data):
        entered.set()
        assert release.wait(3)
        return "encoded"

    monkeypatch.setattr(avatars, "validate_image", delayed)
    fake_session(monkeypatch, avatars, FakeResponse(image_bytes()))
    task = asyncio.create_task(avatars.download_qq_avatar("123456"))
    assert await asyncio.to_thread(entered.wait, 3)
    # This coroutine can still run while the decoder waits on another thread.
    release.set()
    assert await task == "encoded"


@pytest.mark.asyncio
async def test_invalid_qq_does_not_open_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Invalid QQ must not open a network session")

    monkeypatch.setattr(avatars.aiohttp, "ClientSession", fail)
    with pytest.raises(ValueError):
        await avatars.download_qq_avatar("https://127.0.0.1/private")
