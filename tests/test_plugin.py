import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from astrbot_plugin_imagegen.image_client import ImageGenError

from .conftest import FakeEvent
from .test_client import ENCODED, PNG


@pytest.mark.parametrize("command", ["/i", "/imagegen", "/生图", "!i"])
async def test_command_sends_image_and_keeps_full_prompt(plugin_module, config, server, command):
    prompts = []

    async def api(request):
        prompts.append((await request.json())["prompt"])
        return web.json_response({"data": [{"b64_json": ENCODED}]})

    config["base_url"] = await server(api)
    plugin = plugin_module.ImageGenPlugin(None, config)
    event = FakeEvent(command + " 一只 cat sitting on a windowsill\n水彩 风格")
    await plugin.image_command(event)
    assert prompts == ["一只 cat sitting on a windowsill\n水彩 风格"]
    assert event.sent[0].text.startswith("正在生成")
    assert event.sent[1].chain[0].image == PNG
    assert event.stopped and event.call_llm is False


async def test_empty_command_help_without_api(plugin_module, config):
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock()
    event = FakeEvent("/i")
    await plugin.image_command(event)
    assert "/i" in event.sent[0].text
    plugin.client.generate.assert_not_awaited()
    assert event.stopped


async def test_llm_tool_schema_delivery_and_same_event_dedup(plugin_module, config, server):
    requests = []

    async def api(request):
        requests.append(await request.json())
        return web.json_response({"data": [{"b64_json": ENCODED}]})

    config["base_url"] = await server(api)
    plugin = plugin_module.ImageGenPlugin(None, config)
    assert plugin.generate_image.tool_args == {"prompt": "string"}
    assert plugin.generate_image.tool_name == "astrbot_generate_image"
    event = FakeEvent("/aa 生成一张猫图")
    response = await plugin.generate_image(event, "一只猫，插画")
    again = await plugin.generate_image(event, "一只猫，插画")
    assert again == response
    assert json.loads(response)["status"] == "success"
    assert json.loads(response)["sent"] == 1
    assert len(requests) == 1
    assert requests[0]["prompt"] == "一只猫，插画"
    assert requests[0]["prompt"] != event.get_message_str()
    assert event.sent[-1].chain[0].image == PNG
    assert not event.stopped and event.call_llm is True
    # A new user message can explicitly request the same prompt again.
    await plugin.generate_image(FakeEvent(), "一只猫，插画")
    assert len(requests) == 2


async def test_failure_is_sent_and_cached(plugin_module, config):
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(side_effect=ImageGenError("鉴权失败（HTTP 401）"))
    event = FakeEvent()
    first = await plugin.generate_image(event, "cat")
    assert await plugin.generate_image(event, "cat") == first
    plugin.client.generate.assert_awaited_once()
    assert json.loads(first)["status"] == "error"
    assert json.loads(first)["sent"] == 0
    assert "鉴权失败" in event.sent[-1].text
    assert not plugin._busy_sessions


async def test_partial_send_has_correct_counts(plugin_module, config, caplog):
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(return_value=[PNG, PNG])
    event = FakeEvent(fail_after=1)
    result = json.loads(await plugin.generate_image(event, "two cats"))
    assert result["status"] == "partial"
    assert result["generated"] == 2
    assert result["sent"] == 1
    assert "credentials" not in caplog.text
    assert not event.stopped


async def test_broken_adapter_still_returns_truthful_tool_result(plugin_module, config):
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(return_value=[PNG])
    event = FakeEvent()
    event.send = AsyncMock(side_effect=RuntimeError("disconnected"))
    result = json.loads(await plugin.generate_image(event, "cat"))
    assert result["status"] == "error"
    assert result["generated"] == 1
    assert result["sent"] == 0
    assert not plugin._busy_sessions


async def test_busy_and_cancellation_release_slots(plugin_module, config):
    config["max_concurrent"] = 1
    plugin = plugin_module.ImageGenPlugin(None, config)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def generate(*args):
        entered.set()
        await release.wait()
        return [PNG]

    plugin.client.generate = generate
    task = asyncio.create_task(plugin.generate_image(FakeEvent(session="a"), "cat"))
    await entered.wait()
    for session in ["a", "b"]:
        result = json.loads(await plugin.generate_image(FakeEvent(session=session), "cat"))
        assert result["status"] == "error"
        assert "正在执行" in result["message"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not plugin._busy_sessions
    assert not plugin._slots.locked()
    release.set()
    result = json.loads(await plugin.generate_image(FakeEvent(session="b"), "cat"))
    assert result["sent"] == 1


@pytest.mark.parametrize(
    ("available", "active", "enabled", "expected"),
    [
        (True, True, True, True),
        (False, True, True, False),
        (True, False, True, False),
        (True, True, False, False),
    ],
)
async def test_tool_hint_respects_persona_and_disabled_tools(
    plugin_module,
    config,
    available,
    active,
    enabled,
    expected,
):
    config["inject_tool_hint"] = enabled
    plugin = plugin_module.ImageGenPlugin(None, config)
    tool = SimpleNamespace(active=active) if available else None
    req = SimpleNamespace(
        system_prompt="original persona",
        func_tool=SimpleNamespace(
            get_tool=lambda name: tool,
        ),
    )
    await plugin.guide_image_requests(FakeEvent("/aa 画只猫"), req)
    await plugin.guide_image_requests(FakeEvent("/aa 画只猫"), req)
    assert req.system_prompt.startswith("original persona")
    assert (plugin_module.TOOL_HINT in req.system_prompt) is expected
    assert req.system_prompt.count(plugin_module.TOOL_HINT) == int(expected)


async def test_no_toolset_leaves_persona_alone(plugin_module, config):
    plugin = plugin_module.ImageGenPlugin(None, config)
    req = SimpleNamespace(system_prompt="original", func_tool=None)
    await plugin.guide_image_requests(FakeEvent(), req)
    assert req.system_prompt == "original"


async def test_legacy_tool_manager_hint(plugin_module, config):
    plugin = plugin_module.ImageGenPlugin(None, config)
    req = SimpleNamespace(
        system_prompt="original",
        func_tool=SimpleNamespace(get_func=lambda name: SimpleNamespace(active=True)),
    )
    await plugin.guide_image_requests(FakeEvent("/aa 画只猫"), req)
    assert plugin_module.TOOL_HINT in req.system_prompt


async def test_session_limit_with_global_capacity(plugin_module, config):
    config["max_concurrent"] = 2
    plugin = plugin_module.ImageGenPlugin(None, config)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def generate(*args):
        entered.set()
        await release.wait()
        return [PNG]

    plugin.client.generate = generate
    task = asyncio.create_task(plugin.generate_image(FakeEvent(session="a"), "cat"))
    try:
        await entered.wait()
        assert not plugin._slots.locked()
        result = json.loads(await plugin.generate_image(FakeEvent(session="a"), "another cat"))
        assert "正在执行" in result["message"]
        release.set()
        assert json.loads(await plugin.generate_image(FakeEvent(session="b"), "cat"))["sent"] == 1
        assert json.loads(await task)["sent"] == 1
    finally:
        release.set()
        await task


async def test_silent_progress_and_config_updates(plugin_module, config):
    config["show_progress"] = False
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(return_value=[PNG])
    await plugin.generate_image(FakeEvent(), "cat")
    config["model"] = "another-gpt-image-model"
    event = FakeEvent()
    await plugin.generate_image(event, "cat")
    assert plugin.client.generate.await_args.args[1].model == "another-gpt-image-model"
    assert len(event.sent) == 1 and hasattr(event.sent[0], "chain")


class RequestToolSet:
    def __init__(self, tools):
        self.tools = tools

    def get_tool(self, name):
        return next((tool for tool in self.tools if tool.name == name), None)


@pytest.mark.parametrize(
    "message", ["/aa 介绍一下你记忆里的小糖豆", "/aa 你还记得我吗", "/aa 你好"]
)
async def test_plain_chat_restores_original_tools_without_mutating_shared_set(
    plugin_module, config, message
):
    plugin = plugin_module.ImageGenPlugin(None, config)
    memory = SimpleNamespace(name="recall_long_term_memory", active=True)
    image = SimpleNamespace(name=plugin_module.TOOL_NAME, active=True)
    web = SimpleNamespace(name="web_search_tavily", active=True)
    registered = RequestToolSet([memory, image, web])
    memories = [SimpleNamespace(type="text", text="recalled data", _no_save=True)]
    history = [
        {"role": "assistant", "tool_calls": [{"function": {"name": plugin_module.TOOL_NAME}}]}
    ]
    req = SimpleNamespace(
        prompt=message,
        system_prompt="original persona",
        func_tool=registered,
        contexts=history,
        extra_user_content_parts=memories,
    )
    event = FakeEvent(message)
    await plugin.guide_image_requests(event, req)
    assert req.func_tool is not registered
    assert req.func_tool.tools == [memory, web]
    assert registered.tools == [memory, image, web]
    assert req.func_tool.get_tool("recall_long_term_memory") is memory
    assert req.system_prompt == "original persona"
    assert req.prompt == message
    assert req.contexts is history
    assert req.extra_user_content_parts is memories
    assert not event.get_extra("imagegen_tool_available")


@pytest.mark.parametrize("message", ["/aa 生成一张猫图", "/aa 根据记忆画小糖豆", "/aa 再画一张"])
async def test_image_chat_keeps_tool_and_memory_available(plugin_module, config, message):
    plugin = plugin_module.ImageGenPlugin(None, config)
    memory = SimpleNamespace(name="recall_long_term_memory", active=True)
    image = SimpleNamespace(name=plugin_module.TOOL_NAME, active=True)
    registered = RequestToolSet([memory, image])
    req = SimpleNamespace(
        system_prompt="persona",
        func_tool=registered,
        contexts=[
            {"role": "assistant", "tool_calls": [{"function": {"name": plugin_module.TOOL_NAME}}]}
        ],
    )
    await plugin.guide_image_requests(FakeEvent(message), req)
    assert req.func_tool is registered
    assert req.func_tool.tools == [memory, image]
    assert registered.get_tool(plugin_module.TOOL_NAME) is image


async def test_non_instruction_image_topic_is_also_isolated(plugin_module, config):
    plugin = plugin_module.ImageGenPlugin(None, config)
    toolset = RequestToolSet([SimpleNamespace(name=plugin_module.TOOL_NAME, active=True)])
    req = SimpleNamespace(system_prompt="persona", func_tool=toolset, contexts=[])
    await plugin.guide_image_requests(FakeEvent("/aa 这张图片好看吗"), req)
    assert req.func_tool is not toolset
    assert not req.func_tool.get_tool(plugin_module.TOOL_NAME)
    assert toolset.get_tool(plugin_module.TOOL_NAME)
