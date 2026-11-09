"""Exercise the installed AstrBot runtime against a local HTTP boundary."""

import asyncio
import importlib.metadata
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("astrbot")
from aiohttp import web
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.message.components import Image, Plain
from astrbot.core.message.message_event_result import (
    MessageEventResult,
    ResultContentType,
)
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.permission import PermissionTypeFilter
from astrbot.core.star.star_handler import EventType, star_handlers_registry
from astrbot.core.utils.t2i.renderer import HtmlRenderer
from astrbot_plugin_t2i_style import main
from astrbot_plugin_t2i_style.bridge import _RENDER_EVENT, BRAND_MARKER
from astrbot_plugin_t2i_style.models import MODEL_KEY


def real_event(message="t2i status", session="native-session", admin=True, llm=False):
    obj = AstrBotMessage()
    obj.type = MessageType.GROUP_MESSAGE
    obj.message = [Plain(message)]
    obj.message_str = message
    obj.message_id = "local-test"
    obj.self_id = "local-bot"
    obj.sender = MessageMember(user_id="123456", nickname="Local test")
    event = AstrMessageEvent(
        message, obj, PlatformMetadata("test", "Local only", "test-instance"), session
    )
    event.role = "admin" if admin else "member"
    event.is_at_or_wake_command = True
    event.plugins_name = []
    if llm:
        event.set_result(
            MessageEventResult()
            .message("本地测试正文 " * 50)
            .set_result_content_type(ResultContentType.LLM_RESULT)
        )
    return event


def stage_config(endpoint):
    return {
        "platform_settings": {
            "reply_prefix": "",
            "reply_with_mention": False,
            "reply_with_quote": False,
            "forward_threshold": 100000,
            "segmented_reply": {
                "words_count_threshold": 500,
                "enable": False,
                "only_llm_result": True,
                "regex": ".*",
                "content_cleanup_rule": "",
            },
        },
        "t2i_word_threshold": 200,
        "t2i_strategy": "remote",
        "t2i_active_template": "codex",
        "provider_tts_settings": {"enable": False},
        "content_safety": {"also_use_in_response": False},
        "t2i": True,
        "t2i_use_file_service": False,
        "callback_api_base": "",
        "t2i_endpoint": endpoint,
    }


async def make_native_runner(
    event,
    response,
    *,
    override="explicit-primary-model",
    selected="selected-provider-model",
    fallback=None,
    streaming=False,
):
    provider = SimpleNamespace(
        provider_config={"id": "local-primary"},
        get_model=lambda: selected,
        text_chat=AsyncMock(return_value=response),
    )
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=ProviderRequest(prompt="Local runner test", model=override),
        run_context=ContextWrapper(context=SimpleNamespace(event=event)),
        tool_executor=SimpleNamespace(),
        agent_hooks=SimpleNamespace(),
        fallback_providers=[fallback] if fallback is not None else [],
        streaming=streaming,
    )
    return runner


@pytest.fixture
async def local_service():
    requests = []

    async def health(request):
        return web.json_response(
            {"status": "ok", "engine": "typst", "style_protocol": 1}
        )

    async def generate(request):
        payload = await request.json()
        requests.append(payload)
        return web.json_response({"data": {"id": "local-image"}})

    app = web.Application(client_max_size=4 * 1024 * 1024)
    app.router.add_get("/text2img/health", health)
    app.router.add_post("/text2img/generate", generate)
    runner = web.AppRunner(app)
    await runner.setup()
    # Bind only loopback; no external or NAS connection is made.
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/text2img", requests
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_real_entry_permissions_parser_initialize_and_terminate(
    tmp_path, monkeypatch, local_service
):
    endpoint, _ = local_service
    monkeypatch.setattr(main.star.StarTools, "get_data_dir", lambda name: tmp_path)
    config = stage_config(endpoint)
    context = SimpleNamespace(get_config=lambda session=None: config)
    plugin = main.T2IStylePlugin(context, {})
    original_stage = main.ResultDecorateStage.process
    original_render = main.NetworkRenderStrategy.render_custom_template
    original_runner = ToolLoopAgentRunner._iter_llm_responses
    await plugin.initialize()
    try:
        assert main.ResultDecorateStage.process is not original_stage
        assert main.NetworkRenderStrategy.render_custom_template is not original_render
        assert ToolLoopAgentRunner._iter_llm_responses is not original_runner
        installed = (
            main.ResultDecorateStage.process,
            main.NetworkRenderStrategy.render_custom_template,
            ToolLoopAgentRunner._iter_llm_responses,
        )
        await plugin.initialize()
        assert installed == (
            main.ResultDecorateStage.process,
            main.NetworkRenderStrategy.render_custom_template,
            ToolLoopAgentRunner._iter_llm_responses,
        )
        normal_hook = next(
            item
            for item in star_handlers_registry.get_handlers_by_event_type(
                EventType.OnLLMResponseEvent, only_activated=False
            )
            if item.handler_module_path == main.__name__
        )
        assert normal_hook.extras_configs["priority"] == -10000
        metadata = next(
            item
            for item in star_handlers_registry.get_handlers_by_event_type(
                EventType.AdapterMessageEvent, only_activated=False
            )
            if item.handler_module_path == main.__name__ and item.handler_name == "t2i"
        )
        command = next(
            item for item in metadata.event_filters if isinstance(item, CommandFilter)
        )
        permission = next(
            item
            for item in metadata.event_filters
            if isinstance(item, PermissionTypeFilter)
        )
        event = real_event("t2i name 我的 bot name")
        assert command.filter(event, config)
        assert event.get_extra("parsed_params") == {"command": "name 我的 bot name"}
        assert permission.filter(event, config)
        replies = [
            result
            async for result in plugin.t2i(event, **event.get_extra("parsed_params"))
        ]
        assert "已保存" in replies[0].get_plain_text()
        assert "style_protocol: 1" in replies[0].get_plain_text()
        assert event.is_stopped()
        assert not event._has_send_oper
        assert (
            plugin.store.render_style(event.unified_msg_origin)["brand_name"]
            == "我的 bot name"
        )
        blank = real_event("t2i")
        assert command.filter(blank, config)
        assert blank.get_extra("parsed_params") == {"command": ""}
        alias = real_event("图样式 使用 初音")
        assert command.filter(alias, config)
        results = [
            result
            async for result in plugin.t2i(alias, **alias.get_extra("parsed_params"))
        ]
        assert "已保存" in results[0].get_plain_text()
        assert plugin.store.render_style(alias.unified_msg_origin)["template"] == "miku"
        member = real_event("t2i global use rio", admin=False)
        assert not permission.filter(member, config)
        before = dict(plugin.store.state["global"])
        results = [result async for result in plugin.t2i(member, "global use rio")]
        assert "仅 AstrBot 管理员" in results[0].get_plain_text()
        assert plugin.store.state["global"] == before
    finally:
        await plugin.terminate()
    assert main.ResultDecorateStage.process is original_stage
    assert main.NetworkRenderStrategy.render_custom_template is original_render
    assert ToolLoopAgentRunner._iter_llm_responses is original_runner
    assert importlib.metadata.version("astrbot") in {"4.28.2", "4.29.4"}


@pytest.mark.asyncio
@pytest.mark.parametrize("with_footer", [False, True])
@pytest.mark.parametrize("model_source", ["normal", "runner"])
async def test_real_stage_renderer_network_http_payload_and_concurrent_isolation(
    tmp_path, monkeypatch, local_service, with_footer, model_source
):
    from astrbot.core.pipeline.result_decorate import stage as stage_module

    endpoint, requests = local_service
    renderer = HtmlRenderer(endpoint)
    # Only template loading is supplied locally; real stage, renderer, network
    # preparation and aiohttp POST execute unchanged.
    renderer.network_strategy.get_template = AsyncMock(return_value=BRAND_MARKER)
    supports_endpoint = (
        "endpoint"
        in inspect.signature(
            renderer.network_strategy.render_custom_template
        ).parameters
    )
    if supports_endpoint:
        # Prove 4.29.4's per-call local endpoint wins over an unused old pool.
        renderer.network_strategy.endpoints = ["https://unused.invalid/text2img"]
        renderer.network_strategy.BASE_RENDER_URL = "https://unused.invalid/text2img"
    monkeypatch.setattr(stage_module, "html_renderer", renderer)
    monkeypatch.setattr(main, "html_renderer", renderer)
    monkeypatch.setattr(main.star.StarTools, "get_data_dir", lambda name: tmp_path)
    config = stage_config(endpoint)
    context = SimpleNamespace(
        get_config=lambda session=None: config,
        get_using_tts_provider_async=AsyncMock(return_value=None),
    )
    plugin = main.T2IStylePlugin(context, {})
    footer = None
    if with_footer:
        footer_module = pytest.importorskip("astrbot_plugin_model_footer.bridge")
        footer = footer_module.FooterBridge()
        footer.install(
            main.ResultDecorateStage, main.NetworkRenderStrategy, ToolLoopAgentRunner
        )
    await plugin.initialize()
    try:
        first, second = (
            real_event(session="first", llm=True),
            real_event(session="second", llm=True),
        )
        responses = [
            LLMResponse(
                role="assistant",
                completion_text="local",
                raw_completion={"model": "actual-first-model"},
            ),
            LLMResponse(
                role="assistant",
                completion_text="local",
                raw_completion=SimpleNamespace(model_version="actual-second-model"),
            ),
        ]
        if model_source == "normal":
            await asyncio.gather(
                *(
                    plugin.remember_response_model(event, response)
                    for event, response in zip((first, second), responses)
                )
            )
        else:
            runners = [
                await make_native_runner(event, response)
                for event, response in zip((first, second), responses)
            ]

            async def consume_runner(runner):
                return [
                    response
                    async for response in runner._iter_llm_responses_with_fallback()
                ]

            await asyncio.gather(*(consume_runner(runner) for runner in runners))
        await plugin.store.update(first.unified_msg_origin, {"template": "miku"})
        await plugin.store.update(
            second.unified_msg_origin, {"template": "rio", "brand_name": "Custom Rio"}
        )
        stage = main.ResultDecorateStage()
        await stage.initialize(
            SimpleNamespace(
                astrbot_config=config, plugin_manager=SimpleNamespace(context=context)
            )
        )

        async def consume(event):
            async for _ in stage.process(event):
                assert _RENDER_EVENT.get() is None

        await asyncio.gather(consume(first), consume(second))
        assert len(requests) == 2
        by_theme = {
            payload["tmpldata"]["t2i_style"]["template"]: payload
            for payload in requests
        }
        assert set(by_theme) == {"miku", "rio"}
        assert "brand_name" not in by_theme["miku"]["tmpldata"]["t2i_style"]
        assert "avatar_base64" not in by_theme["miku"]["tmpldata"]["t2i_style"]
        assert by_theme["rio"]["tmpldata"]["t2i_style"]["brand_name"] == "Custom Rio"
        assert by_theme["miku"]["json"] is True
        assert by_theme["miku"]["options"]["type"] == "jpeg"
        # Both standalone merged mode and temporary legacy coexistence use actual metadata.
        assert by_theme["miku"]["tmpldata"]["model_name"] == "actual-first-model"
        assert by_theme["rio"]["tmpldata"]["model_name"] == "actual-second-model"
        for event in (first, second):
            assert len(event.get_result().chain) == 1
            assert isinstance(event.get_result().chain[0], Image)
            assert not event._has_send_oper
        assert _RENDER_EVENT.get() is None
        if supports_endpoint:
            assert renderer.network_strategy.endpoints == [
                "https://unused.invalid/text2img"
            ]
        assert (
            "endpoint" in inspect.signature(HtmlRenderer.render_t2i).parameters
            or importlib.metadata.version("astrbot") == "4.28.2"
        )
    finally:
        await plugin.terminate()
        if footer:
            footer.uninstall()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, {"model_version": "fallback-actual-version"}])
async def test_real_runner_primary_error_then_selected_fallback_without_default_guess(
    tmp_path, monkeypatch, local_service, raw
):
    endpoint, _ = local_service
    monkeypatch.setattr(main.star.StarTools, "get_data_dir", lambda name: tmp_path)

    def forbidden_default(*args, **kwargs):
        raise AssertionError("Global default provider must never be consulted")

    context = SimpleNamespace(
        get_config=lambda session=None: stage_config(endpoint),
        get_using_provider=forbidden_default,
        get_using_provider_async=forbidden_default,
    )
    plugin = main.T2IStylePlugin(context, {})
    await plugin.initialize()
    try:
        event = real_event(llm=True)

        async def fallback_response(**kwargs):
            assert event.get_extra(MODEL_KEY) is None
            assert (
                "model" not in kwargs
            )  # Actual AstrBot fallback drops primary override.
            return LLMResponse(
                role="assistant", completion_text="fallback success", raw_completion=raw
            )

        fallback = SimpleNamespace(
            provider_config={"id": "local-fallback"},
            get_model=lambda: "fallback-current-model",
            text_chat=AsyncMock(side_effect=fallback_response),
        )
        runner = await make_native_runner(
            event,
            LLMResponse(
                role="err",
                completion_text="primary error",
                raw_completion={"model": "wrong-error-model"},
            ),
            fallback=fallback,
        )
        responses = [item async for item in runner._iter_llm_responses_with_fallback()]
        assert len(responses) == 1 and responses[0].role == "assistant"
        assert event.get_extra(MODEL_KEY) == (
            "fallback-actual-version" if raw else "fallback-current-model"
        )
        assert runner.provider is fallback
        assert runner.req.model == "explicit-primary-model"
        assert "model" not in fallback.text_chat.await_args.kwargs
    finally:
        await plugin.terminate()


@pytest.mark.asyncio
async def test_normal_hook_ignores_errors_chunks_and_disabled_callback(
    tmp_path, monkeypatch, local_service
):
    endpoint, _ = local_service
    monkeypatch.setattr(main.star.StarTools, "get_data_dir", lambda name: tmp_path)
    plugin = main.T2IStylePlugin(
        SimpleNamespace(get_config=lambda session=None: stage_config(endpoint)), {}
    )
    await plugin.initialize()
    event = real_event(llm=True)
    try:
        await plugin.remember_response_model(
            event,
            LLMResponse(
                role="assistant",
                completion_text="final",
                raw_completion={"model": "actual-final"},
            ),
        )
        await plugin.remember_response_model(
            event,
            LLMResponse(
                role="assistant",
                completion_text="chunk",
                is_chunk=True,
                raw_completion={"model": "wrong-chunk"},
            ),
        )
        await plugin.remember_response_model(
            event,
            LLMResponse(
                role="err",
                completion_text="error",
                raw_completion={"model": "wrong-error"},
            ),
        )
        await plugin.remember_response_model(
            event, LLMResponse(role="assistant", completion_text="no metadata")
        )
        assert event.get_extra(MODEL_KEY) == "actual-final"
    finally:
        await plugin.terminate()
    await plugin.remember_response_model(
        event,
        LLMResponse(
            role="assistant",
            completion_text="late",
            raw_completion={"model": "must-not-replace"},
        ),
    )
    assert event.get_extra(MODEL_KEY) == "actual-final"


@pytest.mark.asyncio
async def test_v100_styles_persist_without_migration_or_reset(
    tmp_path, monkeypatch, local_service
):
    import json

    endpoint, _ = local_service
    state = {
        "version": 1,
        "global": {"template": "genshin", "brand_name": "Existing global"},
        "sessions": {
            "test-instance:GroupMessage:kept": {"template": "rio", "avatar_base64": ""}
        },
    }
    path = tmp_path / "styles.json"
    payload = json.dumps(state, ensure_ascii=False)
    path.write_text(payload)
    monkeypatch.setattr(main.star.StarTools, "get_data_dir", lambda name: tmp_path)
    plugin = main.T2IStylePlugin(
        SimpleNamespace(get_config=lambda session=None: stage_config(endpoint)), {}
    )
    await plugin.initialize()
    try:
        assert plugin.store.state == state
        assert (
            plugin.store.render_style("test-instance:GroupMessage:kept")["template"]
            == "rio"
        )
        assert plugin.store.render_style("other")["template"] == "genshin"
        assert "avatar_base64" not in plugin.store.render_style(
            "test-instance:GroupMessage:kept"
        )
    finally:
        await plugin.terminate()
    assert path.read_text() == payload
