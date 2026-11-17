import base64
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from astrbot_plugin_qing_media.features.imagegen.image_client import (
    ImageGenError,
    ImagesClient,
    ImageSettings,
)

from .conftest import FakeEvent
from .test_client import GIF, GIF87, PNG


@pytest.mark.parametrize("image", [GIF, GIF87])
@pytest.mark.parametrize("transport", ["base64", "url"])
@pytest.mark.parametrize("output_format", ["png", "gif"])
async def test_gif_response_preserves_all_bytes_and_scopes_auth(
    server, config, image, transport, output_format
):
    calls = []

    async def cdn(request):
        assert "Authorization" not in request.headers
        return web.Response(body=image, content_type="image/gif")

    cdn_url = await server(cdn)

    async def api(request):
        calls.append(await request.json())
        assert request.headers["Authorization"] == "Bearer api-only"
        result = (
            {"url": cdn_url + "/animation.gif"}
            if transport == "url"
            else {"b64_json": base64.b64encode(image).decode()}
        )
        return web.json_response({"data": [result]})

    config.update(
        base_url=await server(api), api_key="api-only", output_format=output_format
    )
    assert await ImagesClient().generate(
        "cat waving", ImageSettings.from_config(config)
    ) == [image]
    assert len(calls) == 1 and calls[0]["output_format"] == output_format


async def test_gif_size_limit_is_still_enforced(server, config):
    async def api(request):
        return web.json_response(
            {"data": [{"b64_json": base64.b64encode(GIF).decode()}]}
        )

    config.update(base_url=await server(api), output_format="gif")
    settings = replace(ImageSettings.from_config(config), max_image_bytes=len(GIF) - 1)
    with pytest.raises(ImageGenError, match="超出"):
        await ImagesClient().generate("cat waving", settings)


@pytest.mark.parametrize("command", ["/i", "/imagegen", "/生图", "!i"])
async def test_gif_command_selects_format_without_changing_global_config(
    plugin_module, config, command
):
    config["show_progress"] = False
    original = dict(config)
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(return_value=[GIF])
    event = FakeEvent(command + " --gif 一只猫挥手\n循环播放")
    await plugin.image_command(event)
    prompt, settings = plugin.client.generate.await_args.args
    assert prompt == "一只猫挥手\n循环播放" and settings.output_format == "gif"
    assert event.sent[0].chain[0].image == GIF
    assert event.stopped and not event.call_llm and config == original


async def test_gif_tool_override_and_format_changes_cannot_double_generate(
    plugin_module, config
):
    config["show_progress"] = False
    original = dict(config)
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(return_value=[GIF])
    event = FakeEvent("/aa 生成一个猫咪挥手的GIF动图")
    first = await plugin.generate_image(
        event, "完整描述：猫咪挥手，循环播放", output_format="gif"
    )
    assert await plugin.generate_image(event, "完整描述：猫咪挥手，循环播放") == first
    assert (
        await plugin.generate_image(event, "完整描述：猫咪挥手，循环播放", "png")
        == first
    )
    assert json.loads(first)["formats"] == ["gif"]
    assert json.loads(first)["status"] == "success" and len(event.sent) == 1
    plugin.client.generate.assert_awaited_once()
    assert plugin.client.generate.await_args.args[1].output_format == "gif"
    assert event.sent[0].chain[0].image == GIF and config == original


@pytest.mark.parametrize("output_format", ["bmp", None, 123, ["gif"]])
async def test_invalid_tool_format_does_not_request_api(
    plugin_module, config, output_format
):
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock()
    result = json.loads(await plugin.generate_image(FakeEvent(), "cat", output_format))
    assert result["error"]["code"] == "invalid_config"
    assert result["error"]["request_outcome"] == "not_started"
    plugin.client.generate.assert_not_awaited()


async def test_actual_return_format_is_reported_and_normal_chat_keeps_default(
    plugin_module, config
):
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(return_value=[PNG])
    result = json.loads(await plugin.generate_image(FakeEvent(), "cat", "gif"))
    assert result["formats"] == ["png"]
    await plugin.generate_image(FakeEvent(session="other"), "ordinary cat")
    assert plugin.client.generate.await_args.args[1].output_format == "png"
    assert config["output_format"] == "png"


async def test_gif_remote_rejection_is_not_retried_with_other_formats(
    plugin_module, config
):
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(side_effect=ImageGenError("接口拒绝该格式。"))
    event = FakeEvent()
    result = await plugin.generate_image(event, "cat", "gif")
    assert await plugin.generate_image(event, "new prompt", "png") == result
    plugin.client.generate.assert_awaited_once()


def test_gif_format_is_available_in_ui_and_normalized(config):
    config.update(output_format=" GIF ", background="transparent")
    assert ImageSettings.from_config(config).output_format == "gif"
