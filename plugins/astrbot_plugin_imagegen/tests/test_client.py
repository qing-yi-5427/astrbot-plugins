import asyncio
import base64
from dataclasses import replace

import pytest
from aiohttp import web

from astrbot_plugin_imagegen.image_client import ImageGenError, ImagesClient, ImageSettings

# A valid 1x1 PNG fixture.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1S0AAAAASUVORK5CYII="
)
ENCODED = base64.b64encode(PNG).decode()


async def test_base64_generation_payload_and_full_endpoint(server, config):
    calls = []

    async def handler(request):
        calls.append((request.path, request.headers.get("Authorization"), await request.json()))
        return web.json_response({"data": [{"b64_json": ENCODED}]})

    base = await server(handler)
    config.update(base_url=base + "/v1", api_key="test-secret", model="custom-gpt-image")
    client = ImagesClient()
    settings = ImageSettings.from_config(config)
    assert await client.generate("a cat in a sunny room", settings) == [PNG]
    config["base_url"] = base + "/v1/images/generations"
    assert await client.generate("猫", ImageSettings.from_config(config)) == [PNG]
    assert len(calls) == 2
    path, auth, body = calls[0]
    assert path == "/v1/images/generations"
    assert auth == "Bearer test-secret"
    assert body["model"] == "custom-gpt-image"
    assert body["prompt"] == "a cat in a sunny room"
    assert body["n"] == 1
    assert "response_format" not in body
    assert calls[1][0] == path
    assert "test-secret" not in repr(settings)


async def test_download_from_separate_cdn_without_api_credentials(server, config):
    auth = []

    async def cdn(request):
        auth.append(request.headers.get("Authorization"))
        if request.path == "/redirect":
            raise web.HTTPFound("/cat.png")
        return web.Response(body=PNG, content_type="image/png")

    cdn_url = await server(cdn)

    async def api(request):
        assert request.headers["Authorization"] == "Bearer api-only"
        return web.json_response({"data": [{"url": cdn_url + "/redirect"}]})

    base = await server(api)
    config.update(base_url=base, api_key="api-only")
    assert await ImagesClient().generate("cat", ImageSettings.from_config(config)) == [PNG]
    assert auth == [None, None]


async def test_multiple_images_and_custom_path(server, config):
    async def api(request):
        assert request.path == "/api/image/create"
        assert (await request.json())["n"] == 2
        return web.json_response({"data": [{"b64_json": ENCODED}, {"b64_json": ENCODED}]})

    config.update(base_url=await server(api) + "/api", images_path="/image/create", n=2)
    assert await ImagesClient().generate("cat", ImageSettings.from_config(config)) == [PNG, PNG]


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 429, 500])
async def test_http_errors_are_safe_and_not_retried(server, config, status):
    requests = []

    async def api(request):
        requests.append(request)
        return web.Response(status=status, text="sensitive upstream body: secret-key")

    config["base_url"] = await server(api)
    with pytest.raises(ImageGenError, match=f"HTTP {status}") as error:
        await ImagesClient().generate("cat", ImageSettings.from_config(config))
    assert "secret-key" not in str(error.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"data": []},
        {"data": [{}]},
        {"data": [None]},
        {"data": [{"b64_json": "!!!"}]},
        {"data": [{"b64_json": base64.b64encode(b"not an image").decode()}]},
        {"data": [{"url": "file:///etc/passwd"}]},
        {"error": {"message": "secret upstream content"}},
        {"data": [{"b64_json": ENCODED}] * 2},
    ],
)
async def test_malformed_response(server, config, payload):
    async def api(request):
        return web.json_response(payload)

    config["base_url"] = await server(api)
    with pytest.raises(ImageGenError) as error:
        await ImagesClient().generate("cat", ImageSettings.from_config(config))
    assert "secret upstream content" not in str(error.value)


async def test_non_json_response(server, config):
    async def api(request):
        return web.Response(text="<html>proxy login page</html>")

    config["base_url"] = await server(api)
    with pytest.raises(ImageGenError, match="JSON"):
        await ImagesClient().generate("cat", ImageSettings.from_config(config))


async def test_timeout_covers_download_and_does_not_retry(server, config):
    started = []

    async def api(request):
        if request.path == "/slow.png":
            await asyncio.sleep(0.6)
            return web.Response(body=PNG)
        started.append(request)
        await asyncio.sleep(0.6)
        return web.json_response({"data": [{"url": base + "/slow.png"}]})

    base = await server(api)
    config.update(base_url=base, timeout=1)
    with pytest.raises(ImageGenError, match="超时"):
        await ImagesClient().generate("cat", ImageSettings.from_config(config))
    assert len(started) == 1


@pytest.mark.parametrize("kind", ["base64", "url", "response"])
async def test_size_limits(server, config, kind):
    async def api(request):
        if request.path == "/large.png":
            return web.Response(body=PNG * 2)
        if kind == "response":
            return web.Response(body=b" " * (1024 * 1024 + 200))
        if kind == "url":
            return web.json_response({"data": [{"url": base + "/large.png"}]})
        return web.json_response({"data": [{"b64_json": base64.b64encode(PNG * 2).decode()}]})

    base = await server(api)
    config["base_url"] = base
    settings = replace(ImageSettings.from_config(config), max_image_bytes=len(PNG))
    with pytest.raises(ImageGenError, match="过大|超出"):
        await ImagesClient().generate("cat", settings)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("base_url", "ftp://example.com"),
        ("base_url", "http://user:pass@host/v1"),
        ("base_url", "http://host/v1?key=secret"),
        ("base_url", "http://host:bad/v1"),
        ("images_path", "images/generations"),
        ("model", ""),
        ("size", "large"),
        ("quality", "ultra"),
        ("n", 0),
        ("n", 11),
        ("n", True),
        ("timeout", 0),
        ("proxy", "socks5://host"),
        ("output_format", "gif"),
        ("max_image_mb", 101),
    ],
)
def test_config_validation(config, key, value):
    config[key] = value
    with pytest.raises(ImageGenError):
        ImageSettings.from_config(config)


def test_transparent_jpeg_rejected(config):
    config.update(background="transparent", output_format="jpeg")
    with pytest.raises(ImageGenError, match="透明背景"):
        ImageSettings.from_config(config)


def test_env_key_and_explicit_override(config, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    assert ImageSettings.from_config(config).api_key == "env-key"
    config["api_key"] = "explicit-key"
    assert ImageSettings.from_config(config).api_key == "explicit-key"


async def test_no_auth_header_for_local_no_auth_api(server, config, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def api(request):
        assert "Authorization" not in request.headers
        return web.json_response({"data": [{"b64_json": ENCODED}]})

    config["base_url"] = await server(api)
    assert await ImagesClient().generate("cat", ImageSettings.from_config(config)) == [PNG]


@pytest.mark.parametrize("prompt", ["", " ", None, "猫" * 32001])
async def test_invalid_prompt_no_request(config, prompt):
    with pytest.raises(ImageGenError):
        await ImagesClient().generate(prompt, ImageSettings.from_config(config))
