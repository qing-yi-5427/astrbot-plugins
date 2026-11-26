"""Native AstrBot integration with fake providers, sends, storage and private roots."""

import copy
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.api.provider import ProviderRequest
from astrbot.core.star.session_plugin_manager import SessionPluginManager
from astrbot.core.star.star import star_registry
from astrbot.core.star.star_handler import EventType, star_handlers_registry
from astrbot.core.agent.context.manager import ContextManager
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
    AiocqhttpAdapter,
)
from astrbot.core.utils.t2i.network_strategy import NetworkRenderStrategy
from astrbot.core.pipeline.result_decorate.stage import ResultDecorateStage

from astrbot_plugin_qing_chat import main as chat
from astrbot_plugin_qing_media import main as media
from astrbot_plugin_qing_chat.runtime import (
    FeatureService,
    SuiteRuntime,
    hide_image_tool,
    current_event,
)


def defaults(schema):
    return {
        k: defaults(v["items"])
        if v["type"] == "object"
        else copy.deepcopy(v.get("default"))
        for k, v in schema.items()
    }


def config_for(module):
    return defaults(
        json.loads((Path(module.__file__).parent / "_conf_schema.json").read_text())
    )


class Context:
    def __init__(self):
        self.platform_manager = SimpleNamespace(platform_insts=[])
        self.cfg = {"plugin_set": ["*"]}
        self.metadata = {}

    def get_config(self, *args, **kwargs):
        return self.cfg

    def get_registered_star(self, name):
        return self.metadata.get(name)


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    from astrbot.core import sp
    from astrbot.api.star import StarTools

    monkeypatch.setattr(sp, "get_async", AsyncMock(return_value=None))
    monkeypatch.setattr(sp, "put_async", AsyncMock())
    monkeypatch.setattr(
        SessionPluginManager,
        "is_plugin_enabled_for_session",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(StarTools, "get_data_dir", lambda name: tmp_path / name)


def event(umo="offline:GroupMessage:123", selected=None):
    return SimpleNamespace(unified_msg_origin=umo, plugins_name=selected)


def patch_points():
    return (
        ContextManager.process,
        ToolLoopAgentRunner.step,
        ToolLoopAgentRunner._iter_llm_responses,
        AiocqhttpAdapter._convert_handle_message_event,
        NetworkRenderStrategy.render_custom_template,
        ResultDecorateStage.process,
    )


def test_only_two_suites_are_registered_and_one_image_tool():
    names = [v.name for v in star_registry if "qing_" in (v.name or "")]
    assert sorted(names) == ["astrbot_plugin_qing_chat", "astrbot_plugin_qing_media"]
    assert not any(".features." in (v.module_path or "") for v in star_registry)
    methods = star_handlers_registry.get_handlers_by_event_type(
        EventType.OnLLMRequestEvent, only_activated=False
    )
    priority = {v.handler_name: v.extras_configs.get("priority", 0) for v in methods}
    assert (
        priority["conversation_rotate_idle_group"]
        > priority["memory_compat_ensure_current_memory_version"]
    )
    assert (
        priority["context_images_limit_history"]
        > priority["reaction_react_to_llm_request"]
    )
    begin = star_handlers_registry.get_handlers_by_event_type(
        EventType.OnAgentBeginEvent, only_activated=False
    )
    p = {v.handler_name: v.extras_configs.get("priority", 0) for v in begin}
    assert (
        p["imagegen_ensure_image_tool_call"]
        > p["context_images_limit_assembled_context"]
        > p["conversation_start_chat_budget"]
    )


def test_real_image_tool_name_schema_and_single_registration():
    from astrbot.core.provider.register import llm_tools

    matched = [t for t in llm_tools.func_list if t.name == "astrbot_generate_image"]
    assert len(matched) == 1
    schema = matched[0].parameters
    assert set(schema["properties"]) == {"prompt", "output_format"}
    import inspect

    signature = inspect.signature(media.Main.imagegen_generate_image)
    assert signature.parameters["prompt"].default is inspect.Parameter.empty
    assert signature.parameters["output_format"].default == ""
    assert schema["properties"]["prompt"]["type"] == "string"


def test_command_facade_keeps_admin_permissions_and_greedy_parser():
    from astrbot.core.star.filter.command import CommandFilter
    from astrbot.core.star.filter.permission import PermissionTypeFilter
    from astrbot_plugin_qing_media.features.render.tests.test_astrbot import (
        real_event,
        stage_config,
    )

    methods = star_handlers_registry.get_handlers_by_event_type(
        EventType.AdapterMessageEvent, only_activated=False
    )
    hook = next(
        h
        for h in methods
        if h.handler_module_path == media.__name__ and h.handler_name == "render_t2i"
    )
    command = next(f for f in hook.event_filters if isinstance(f, CommandFilter))
    permission = next(
        f for f in hook.event_filters if isinstance(f, PermissionTypeFilter)
    )
    e = real_event("t2i name 测试 名称")
    assert command.filter(e, stage_config(""))
    assert e.get_extra("parsed_params") == {"command": "name 测试 名称"}
    assert permission.filter(e, {})
    assert not permission.filter(real_event("t2i use genshin", admin=False), {})


@pytest.mark.parametrize("module,sections", [(chat, 7), (media, 3)])
def test_visual_settings_schema_has_sections_and_module_switches(module, sections):
    schema = json.loads(
        (Path(module.__file__).parent / "_conf_schema.json").read_text()
    )
    assert len(schema) == sections
    for feature in schema.values():
        assert feature["description"]
        assert feature["type"] == "object"
        assert feature["items"]["enabled"]["type"] == "bool"


@pytest.mark.asyncio
async def test_native_lifecycle_all_nine_features_and_idempotent_reload():
    before = patch_points()
    context = Context()
    a, b = chat.Main(context, config_for(chat)), media.Main(context, config_for(media))
    try:
        for _ in range(2):
            await a.initialize()
            await b.initialize()
            assert len(a.modules.services) == 7, a.modules.errors
            assert len(b.modules.services) == 3, b.modules.errors
            assert not a.modules.errors and not b.modules.errors
            installed = patch_points()
            await a.initialize()
            await b.initialize()
            assert patch_points() == installed
            await b.terminate()
            await a.terminate()
            assert patch_points() == before
    finally:
        await b.terminate()
        await a.terminate()


@pytest.mark.asyncio
@pytest.mark.parametrize("section", list(chat.LEGACY))
async def test_module_off_does_not_initialize(section):
    cfg = config_for(chat)
    cfg[section]["enabled"] = False
    a = chat.Main(Context(), cfg)
    try:
        await a.initialize()
        assert section not in a.modules.services
        assert not await a.modules.allowed(section, event())
    finally:
        await a.terminate()


@pytest.mark.asyncio
async def test_legacy_conflict_is_dormant_without_installing_wrappers():
    c = Context()
    c.metadata[chat.LEGACY["mention"]] = SimpleNamespace(activated=True)
    a = chat.Main(c, config_for(chat))
    before = patch_points()
    await a.initialize()
    assert a.modules.blocked and not a.modules.services
    assert patch_points() == before
    c.metadata.clear()
    try:
        await a.initialize()
        assert len(a.modules.services) == 7
    finally:
        await a.terminate()


@pytest.mark.asyncio
async def test_individual_failure_closes_partial_service_and_keeps_other_features():
    c = Context()
    owner = SimpleNamespace(
        context=c,
        config={"bad": {}, "good": {}},
        logger=logging.getLogger("suite-test"),
    )

    class Bad(FeatureService):
        async def initialize(self):
            raise ValueError("secret text must not escape")

        async def terminate(self):
            self.closed = True

    class Good(FeatureService):
        pass

    runtime = SuiteRuntime(
        owner,
        "test",
        {"bad": "oldbad", "good": "oldgood"},
        {"bad": "author/oldbad", "good": "author/oldgood"},
        {"bad": (Bad, True), "good": (Good, True)},
    )
    await runtime.initialize()
    assert list(runtime.services) == ["good"]
    assert runtime.errors == {"bad": "ValueError"}
    await runtime.terminate()


@pytest.mark.asyncio
async def test_native_new_and_legacy_session_switches_both_apply(monkeypatch):
    a = chat.Main(Context(), config_for(chat))
    try:
        await a.initialize()
        method = AsyncMock(
            side_effect=lambda umo, name: name != chat.LEGACY["reaction"]
        )
        monkeypatch.setattr(
            SessionPluginManager, "is_plugin_enabled_for_session", method
        )
        assert not await a.modules.allowed("reaction", event())
        assert await a.modules.allowed("mention", event())
        method.side_effect = lambda umo, name: name != "astrbot_plugin_qing_chat"
        assert not await a.modules.allowed("mention", event())
    finally:
        await a.terminate()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection,mention,reaction",
    [
        (["*"], True, True),
        ([], False, False),
        (["astrbot_plugin_qing_chat"], True, True),
        (["astrbot_plugin_qing_chat", "astrbot_plugin_mention_llm"], True, False),
        (["astrbot_plugin_mention_llm"], False, False),
    ],
)
async def test_explicit_profile_selection_retains_old_feature_mask(
    selection, mention, reaction
):
    a = chat.Main(Context(), config_for(chat))
    try:
        await a.initialize()
        assert await a.modules.allowed("mention", event(selected=selection)) is mention
        assert (
            await a.modules.allowed("reaction", event(selected=selection)) is reaction
        )
    finally:
        await a.terminate()


@pytest.mark.asyncio
async def test_idle_storage_keeps_resolved_legacy_namespace(monkeypatch):
    from astrbot.core import sp

    c = Context()
    c.metadata[chat.LEGACY["conversation"]] = SimpleNamespace(
        activated=False, plugin_id="actual-author/actual-old-id"
    )
    a = chat.Main(c, config_for(chat))
    try:
        await a.initialize()
        service = a.modules.services["conversation"]
        assert service._legacy_id == "actual-author/actual-old-id"
        await service.get_kv_data("marker", None)
        assert sp.get_async.await_args.args[:3] == (
            "plugin",
            "actual-author/actual-old-id",
            "marker",
        )
        await service.put_kv_data("marker", {"tracking_started_at": 1})
        assert sp.put_async.await_args.args[:3] == (
            "plugin",
            "actual-author/actual-old-id",
            "marker",
        )
    finally:
        await a.terminate()


def test_hidden_tool_uses_private_request_copy():
    image = SimpleNamespace(name="astrbot_generate_image")
    other = SimpleNamespace(name="search")
    shared = SimpleNamespace(tools=[image, other])
    req = SimpleNamespace(func_tool=shared)
    hide_image_tool(req)
    assert req.func_tool is not shared and req.func_tool.tools == [other]
    assert shared.tools == [image, other]


@pytest.mark.asyncio
async def test_disabled_image_module_cannot_leak_tool_or_execute():
    cfg = config_for(media)
    cfg["imagegen"]["enabled"] = False
    b = media.Main(Context(), cfg)
    try:
        await b.initialize()
        req = SimpleNamespace(
            func_tool=SimpleNamespace(
                tools=[SimpleNamespace(name="astrbot_generate_image")]
            )
        )
        await b.imagegen_guide_image_requests(event(), req)
        assert req.func_tool.tools == []
        result = json.loads(await b.imagegen_generate_image(event(), "cat"))
        assert result["error"]["code"] == "feature_disabled"
    finally:
        await b.terminate()


@pytest.mark.asyncio
async def test_plain_chat_does_not_acquire_image_generation_hint_or_extra_call():
    b = media.Main(Context(), config_for(media))
    e = event()
    e.get_message_str = lambda: "今天大家在聊什么？"
    req = ProviderRequest(prompt="hello", system_prompt="persona")
    req.func_tool = SimpleNamespace(
        tools=[
            SimpleNamespace(name="astrbot_generate_image"),
            SimpleNamespace(name="memory"),
        ]
    )
    try:
        await b.initialize()
        client = b.modules.services["imagegen"].client
        client.generate = AsyncMock()
        await b.imagegen_guide_image_requests(e, req)
        assert req.system_prompt == "persona"
        assert [t.name for t in req.func_tool.tools] == ["memory"]
        client.generate.assert_not_awaited()
    finally:
        await b.terminate()


@pytest.mark.asyncio
async def test_current_runner_scope_and_foreign_wrapper_survive_unload(monkeypatch):
    observed = []

    async def original(runner):
        observed.append(current_event())
        yield 1

    monkeypatch.setattr(ToolLoopAgentRunner, "step", original)
    a = chat.Main(Context(), config_for(chat))
    await a.initialize()
    ours = ToolLoopAgentRunner.step

    async def later(runner):
        async for item in ours(runner):
            yield item

    ToolLoopAgentRunner.step = later
    e = event()
    runner = SimpleNamespace(
        run_context=SimpleNamespace(context=SimpleNamespace(event=e))
    )
    assert [n async for n in ToolLoopAgentRunner.step(runner)] == [1]
    assert observed[-1] is e and current_event() is None
    await a.terminate()
    assert ToolLoopAgentRunner.step is later
    assert [n async for n in ToolLoopAgentRunner.step(runner)] == [1]
    assert observed[-1] is None and current_event() is None


def qq_event(text, parts, raw_parts=None):
    from aiocqhttp import Event
    from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.platform_metadata import PlatformMetadata
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
        AiocqhttpMessageEvent,
    )

    message = AstrBotMessage()
    message.type = MessageType.GROUP_MESSAGE
    message.group_id, message.self_id = "123", "456"
    message.sender = MessageMember("789", "offline-user")
    message.message, message.message_str, message.message_id = parts, text, "42"
    message.raw_message = Event(
        {
            "post_type": "message",
            "self_id": 456,
            "user_id": 789,
            "message_type": "group",
            "group_id": 123,
            "message_id": 42,
            "message": raw_parts or [{"type": "text", "data": {"text": text}}],
        }
    )
    bot = SimpleNamespace(call_action=AsyncMock(return_value={"status": "ok"}))
    e = AiocqhttpMessageEvent(
        text, message, PlatformMetadata("aiocqhttp", "offline", "offline"), "123", bot
    )
    e.is_at_or_wake_command = True
    e.send = AsyncMock(side_effect=AssertionError("No real QQ test sends"))
    return e


@pytest.mark.asyncio
async def test_qq_raffle_command_and_mention_use_same_store():
    from datetime import datetime, timedelta

    from astrbot.api.message_components import At, Plain
    from astrbot.core.star.filter.command import CommandFilter
    from astrbot_plugin_qing_chat.features.raffle.store import ZONE

    suite = chat.Main(Context(), config_for(chat))
    deadline = (datetime.now(ZONE) + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
    command = f"发起 1 {deadline} 测试礼物"
    event = qq_event("抽奖 " + command, [Plain("抽奖 " + command)])
    hooks = star_handlers_registry.get_handlers_by_event_type(
        EventType.AdapterMessageEvent, only_activated=False
    )
    hook = next(
        h
        for h in hooks
        if h.handler_module_path == chat.__name__ and h.handler_name == "raffle_command"
    )
    command_filter = next(f for f in hook.event_filters if isinstance(f, CommandFilter))
    assert command_filter.filter(event, {})
    assert event.get_extra("parsed_params") == {"command": command}
    try:
        await suite.initialize()
        event.send = AsyncMock()
        await suite.raffle_command(event, **event.get_extra("parsed_params"))
        event.send.assert_awaited_once()
        assert "抽奖 #1 已发起" in event.send.await_args.args[0].get_plain_text()
        assert event.is_stopped()
        raw = [
            {"type": "at", "data": {"qq": "456"}},
            {"type": "text", "data": {"text": "报名 1"}},
        ]
        event = qq_event("报名 1", [At(qq="456"), Plain("报名 1")], raw)
        event.send = AsyncMock()
        await suite.raffle_mention(event)
        assert event.send.await_args.args[0].get_plain_text() == "报名成功。抽奖 #1，你的报名序号是 1。"
        assert event.is_stopped()
        normal = qq_event(
            "你好",
            [At(qq="456"), Plain("你好")],
            [raw[0], {"type": "text", "data": {"text": "你好"}}],
        )
        await suite.raffle_mention(normal)
        normal.send.assert_not_awaited()
        assert not normal.is_stopped()

        raffle = suite.modules.services["raffle"]
        raffle.form_url = "https://raffle.example.test"
        access = qq_event("抽奖", [Plain("抽奖")])
        assert command_filter.filter(access, {})
        assert access.get_extra("parsed_params") == {"command": ""}
        access.bot.call_action.return_value = {"message_id": 123}
        access.send = AsyncMock()
        await suite.raffle_command(access, **access.get_extra("parsed_params"))
        private = access.bot.call_action.await_args
        assert private.args == ("send_private_msg",)
        assert "/a/" in private.kwargs["message"][0]["data"]["text"]
        assert "已私发" in access.send.await_args.args[0].get_plain_text()
        assert "/a/" not in access.send.await_args.args[0].get_plain_text()
    finally:
        await suite.terminate()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,expected",
    [
        ("mention", True),
        ("someone_else", False),
        ("text_fake", False),
        ("already_plugin_request", False),
    ],
)
async def test_native_mention_enters_original_prefix_gate_only_when_eligible(
    kind, expected
):
    from astrbot.api.message_components import At, Plain
    from astrbot.core import astr_main_agent
    from astrbot_plugin_qing_chat.features.mention.main import MENTION_REQUEST

    a = chat.Main(Context(), config_for(chat))
    original = astr_main_agent._matches_provider_wake_prefix
    e = qq_event(
        "你好", [At(qq="456" if kind != "someone_else" else "999"), Plain("你好")]
    )
    if kind == "text_fake":
        e.message_obj.message = [Plain("@机器人 你好")]
    if kind == "already_plugin_request":
        e.set_extra("provider_request", ProviderRequest(prompt="custom"))
    try:
        await a.initialize()
        await a.mention_mark_explicit_mention(e)
        assert (e.get_extra(MENTION_REQUEST) is True) is expected
        assert astr_main_agent._matches_provider_wake_prefix(e, "aa") is expected
        assert e.message_str == "你好"
        assert a.context.cfg == {"plugin_set": ["*"]}
        e.send.assert_not_awaited()
    finally:
        await a.terminate()
    assert astr_main_agent._matches_provider_wake_prefix is original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,expected",
    [
        ("aa", True),
        ("mention", True),
        ("aa_picture", True),
        ("wrong_command", False),
        ("other_mention", False),
        ("plain", False),
    ],
)
async def test_native_reactions_keep_media_boundaries_and_deduplicate(kind, expected):
    from astrbot.api.message_components import At, Image, Plain

    a = chat.Main(Context(), config_for(chat))
    text = (
        "/aa 你好"
        if kind == "aa"
        else "/aaa"
        if kind == "wrong_command"
        else "/aa看看图"
        if kind == "aa_picture"
        else "你好"
    )
    parts = [Plain(text)]
    raw = None
    if kind in ("mention", "other_mention"):
        parts.insert(0, At(qq="456" if kind == "mention" else "999"))
    if kind == "aa_picture":
        parts = [
            Plain("/aa"),
            Image.fromFileSystem("/offline/image.png"),
            Plain("看看图"),
        ]
        raw = [
            {"type": "text", "data": {"text": "/aa"}},
            {"type": "image", "data": {"file": "offline"}},
            {"type": "text", "data": {"text": "看看图"}},
        ]
    e = qq_event(text, parts, raw)
    try:
        await a.initialize()
        req = ProviderRequest(prompt="unchanged")
        await a.reaction_react_to_llm_request(e, req)
        await a.reaction_react_to_llm_request(e, req)
        assert e.bot.call_action.await_count == int(expected)
        if expected:
            assert e.bot.call_action.await_args.args == ("set_msg_emoji_like",)
        assert req.prompt == "unchanged"
        e.send.assert_not_awaited()
    finally:
        await a.terminate()


def test_accidental_parallel_install_does_not_register_duplicate_tool():
    from astrbot.core.star.star import StarMetadata
    from astrbot_plugin_qing_media.runtime import safe_filter

    metadata = StarMetadata(name="old-image", activated=True)
    star_registry.append(metadata)
    calls = []

    def decorator(function):
        calls.append(function)
        return function

    def callback():
        pass

    try:
        assert safe_filter(decorator, ["old-image"])(callback) is callback
        assert not calls
        metadata.activated = False
        safe_filter(decorator, ["old-image"])(callback)
        assert calls == [callback]
    finally:
        star_registry.remove(metadata)
