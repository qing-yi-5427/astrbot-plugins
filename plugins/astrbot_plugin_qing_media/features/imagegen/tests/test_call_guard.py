import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_qing_media.features.imagegen.image_client import ImageGenError

from .conftest import FakeEvent
from .test_plugin import RequestToolSet


def response(
    text="行，画张日常穿搭的。", calls=None, usage=7, raw=None, role="assistant"
):
    return SimpleNamespace(
        role=role,
        completion_text=text,
        tools_call_name=calls or [],
        tools_call_args=[{"prompt": "完整描述：主体、背景、日常服装、水彩风格"}]
        if calls
        else [],
        usage=usage,
        raw_completion=raw,
        is_chunk=False,
    )


def runner_for(plugin_module, replies):
    image = SimpleNamespace(name=plugin_module.TOOL_NAME, active=True)
    memory = SimpleNamespace(name="recall_long_term_memory", active=True)
    tools = RequestToolSet([memory, image])
    runner = SimpleNamespace(
        provider=SimpleNamespace(text_chat=AsyncMock(side_effect=replies)),
        stats=SimpleNamespace(token_usage=0),
        streaming=True,
        req=SimpleNamespace(
            func_tool=tools,
            model="persona-model",
            session_id="same-session",
            extra_user_content_parts=[{"type": "text", "text": "recalled facts"}],
        ),
        run_context=SimpleNamespace(
            messages=[
                {"role": "system", "content": "original persona"},
                {"role": "user", "content": "画一张xxx"},
            ]
        ),
        _abort_signal=asyncio.Event(),
    )
    runner._func_tool_for_provider = lambda: runner.req.func_tool
    runner._sanitize_contexts_for_provider = lambda messages: [
        dict(m) for m in messages
    ]

    async def original(*, include_model=True):
        yield await runner.provider.text_chat(
            contexts=runner.run_context.messages,
            func_tool=runner.req.func_tool,
            tool_choice="auto",
        )

    runner._iter_llm_responses = original
    return runner, tools


async def step(runner, include_model=True):
    return [r async for r in runner._iter_llm_responses(include_model=include_model)]


def register(runner, event):
    registry = sys.modules[
        "astrbot.core.pipeline.process_stage.follow_up"
    ]._ACTIVE_AGENT_RUNNERS
    registry[event.unified_msg_origin] = runner


async def test_promise_is_repaired_by_model_with_only_image_tool(plugin_module, config):
    tool_reply = response(calls=[plugin_module.TOOL_NAME], usage=11)
    runner, tools = runner_for(
        plugin_module, [response(), tool_reply, response("已发送图片。")]
    )
    original_contexts = list(runner.run_context.messages)
    original_provider = runner.provider
    event = FakeEvent("/aa 画一张xxx")
    register(runner, event)
    plugin = plugin_module.ImageGenPlugin(None, config)
    await plugin.ensure_image_tool_call(event, runner.run_context)
    results = await step(runner)
    assert results == [tool_reply]
    assert "日常穿搭" not in results[0].completion_text or results[0].tools_call_name
    assert event.get_extra("imagegen_call_guard_active")
    assert event.get_extra("imagegen_tool_issued")
    assert not runner.streaming
    assert runner.provider is original_provider
    assert runner.req.func_tool is tools and len(tools.tools) == 2
    assert runner.run_context.messages == original_contexts
    first, corrective = runner.provider.text_chat.await_args_list
    assert first.kwargs["tool_choice"] == "auto"
    assert corrective.kwargs["tool_choice"] == "required"
    assert corrective.kwargs["func_tool"].tools == [
        tools.get_tool(plugin_module.TOOL_NAME)
    ]
    assert corrective.kwargs["contexts"][:-1] == original_contexts
    assert "完整的生图 prompt" in corrective.kwargs["contexts"][-1]["content"]
    assert (
        corrective.kwargs["extra_user_content_parts"]
        is runner.req.extra_user_content_parts
    )
    assert (
        runner.stats.token_usage == 7
    )  # Correction's 11 tokens are counted by native runner.
    assert results[0].usage == 11
    assert (await step(runner))[0].completion_text == "已发送图片。"
    assert runner.provider.text_chat.await_count == 3
    assert event.sent == []


async def test_memory_retrieval_before_image_repair_is_preserved(plugin_module, config):
    memory_reply = response(calls=["recall_long_term_memory"])
    image_reply = response(calls=[plugin_module.TOOL_NAME])
    runner, tools = runner_for(plugin_module, [memory_reply, response(), image_reply])
    event = FakeEvent("/aa 根据记忆画小糖豆")
    register(runner, event)
    await plugin_module.ImageGenPlugin(None, config).ensure_image_tool_call(
        event, runner.run_context
    )
    assert await step(runner) == [memory_reply]
    assert runner.provider.text_chat.await_count == 1
    runner.run_context.messages.append({"role": "tool", "content": "memory result"})
    assert await step(runner) == [image_reply]
    kwargs = runner.provider.text_chat.await_args.kwargs
    assert kwargs["contexts"][-2]["content"] == "memory result"
    assert tools.get_tool("recall_long_term_memory").active


@pytest.mark.parametrize(
    "message",
    [
        "/aa 你好",
        "/aa 介绍一下你记忆里的小糖豆",
        "/aa 怎么画猫",
        "/aa 你能画图吗",
        "/aa 这张图片好看吗",
        "/aa 不要生成猫图",
        "/aa 画猫的提示词怎么写",
    ],
)
async def test_normal_chat_is_not_wrapped_or_changed(plugin_module, config, message):
    runner, tools = runner_for(plugin_module, [response("normal reply")])
    original = runner._iter_llm_responses
    event = FakeEvent(message)
    register(runner, event)
    await plugin_module.ImageGenPlugin(None, config).ensure_image_tool_call(
        event, runner.run_context
    )
    assert runner._iter_llm_responses is original
    assert runner.streaming
    assert runner.req.func_tool is tools
    assert not event.get_extra("imagegen_call_guard_active")
    assert runner.provider.text_chat.await_count == 0
    assert event.sent == []


@pytest.mark.parametrize(
    "reason", ["disabled", "inactive_tool", "missing_tool", "other_turn", "timeout"]
)
async def test_guard_respects_switch_allowlist_and_turn_identity(
    plugin_module, config, reason
):
    runner, tools = runner_for(plugin_module, [response()])
    event = FakeEvent("/aa 画只猫")
    register(runner, event)
    original = runner._iter_llm_responses
    context = runner.run_context
    if reason == "disabled":
        config["ensure_tool_call"] = False
    elif reason == "inactive_tool":
        tools.get_tool(plugin_module.TOOL_NAME).active = False
    elif reason == "missing_tool":
        tools.tools = tools.tools[:1]
    elif reason == "other_turn":
        context = SimpleNamespace(messages=[])
    else:
        config["tool_call_repair_timeout"] = 0
    await plugin_module.ImageGenPlugin(None, config).ensure_image_tool_call(
        event, context
    )
    assert runner._iter_llm_responses is original
    assert runner.streaming and event.sent == []


@pytest.mark.parametrize(
    "reply",
    [
        response("无法生成这类内容。"),
        response("不能帮你画这张图。"),
        response("我不会帮你生成这种内容。"),
        response("好，取消画图，不画了。"),
        response("请补充你想要的主体和风格。"),
        response("你想要什么风格？"),
        response("I cannot help with that request."),
        response("Could you clarify the subject?"),
        response("refused", raw={"output": [{"content": [{"type": "refusal"}]}]}),
        response("refused", raw={"choices": [{"message": {"refusal": "no"}}]}),
        response("failed", role="err"),
        response("incomplete", raw={"status": "incomplete"}),
    ],
)
async def test_refusal_clarification_and_provider_errors_never_force_image(
    plugin_module, config, reply
):
    runner, _ = runner_for(plugin_module, [reply])
    event = FakeEvent("/aa 画只猫")
    register(runner, event)
    await plugin_module.ImageGenPlugin(None, config).ensure_image_tool_call(
        event, runner.run_context
    )
    assert await step(runner) == [reply]
    assert runner.provider.text_chat.await_count == 1
    assert not runner._imagegen_call_guard.repair_used


@pytest.mark.parametrize(
    "existing", ["issued", "attempted", "failed", "partial", "success"]
)
async def test_actual_attempt_or_result_prevents_repair(
    plugin_module, config, existing
):
    reply = response("本次失败，请检查连接。")
    runner, _ = runner_for(plugin_module, [reply])
    event = FakeEvent("/aa 画只猫")
    if existing in {"issued", "attempted"}:
        event.set_extra(
            "imagegen_tool_" + existing
            if existing == "issued"
            else "imagegen_attempted",
            True,
        )
    else:
        event.set_extra("imagegen_results", {"cat": json.dumps({"status": existing})})
    register(runner, event)
    await plugin_module.ImageGenPlugin(None, config).ensure_image_tool_call(
        event, runner.run_context
    )
    assert await step(runner) == [reply]
    assert runner.provider.text_chat.await_count == 1


async def test_ignored_tool_choice_returns_honest_notice_without_loop(
    plugin_module, config
):
    runner, _ = runner_for(
        plugin_module, [response(), response("已经画好了。", usage=11)]
    )
    event = FakeEvent("/aa 画只猫")
    register(runner, event)
    await plugin_module.ImageGenPlugin(None, config).ensure_image_tool_call(
        event, runner.run_context
    )
    reply = (await step(runner))[0]
    assert "未实际执行" in reply.completion_text and "已停止" in reply.completion_text
    assert "已经画好了" not in reply.completion_text
    assert runner.provider.text_chat.await_count == 2
    assert reply.usage == 11
    assert event.sent == []


async def test_repair_does_not_execute_multiple_image_calls(plugin_module, config):
    runner, _ = runner_for(
        plugin_module,
        [
            response(),
            response(calls=[plugin_module.TOOL_NAME, plugin_module.TOOL_NAME]),
        ],
    )
    event = FakeEvent("/aa 画只猫")
    register(runner, event)
    await plugin_module.ImageGenPlugin(None, config).ensure_image_tool_call(
        event, runner.run_context
    )
    reply = (await step(runner))[0]
    assert not getattr(reply, "tools_call_name", [])
    assert "未实际执行" in reply.completion_text
    assert not event.get_extra("imagegen_tool_issued")
    assert runner.provider.text_chat.await_count == 2


@pytest.mark.parametrize(
    "error", [TimeoutError(), RuntimeError("secret-key; private history")]
)
async def test_repair_failure_is_bounded_safe_and_does_not_bill_image(
    plugin_module, config, caplog, error
):
    runner, _ = runner_for(plugin_module, [response(), error])
    event = FakeEvent("/aa 画只猫")
    register(runner, event)
    await plugin_module.ImageGenPlugin(None, config).ensure_image_tool_call(
        event, runner.run_context
    )
    reply = (await step(runner))[0]
    assert "未实际执行" in reply.completion_text
    assert not event.get_extra("imagegen_attempted")
    assert runner.provider.text_chat.await_count == 2
    assert "secret-key" not in reply.completion_text + caplog.text
    assert "private history" not in reply.completion_text + caplog.text


async def test_cancel_during_repair_propagates_and_never_calls_image(
    plugin_module, config
):
    entered = asyncio.Event()

    async def calls(**kwargs):
        if kwargs["tool_choice"] == "auto":
            return response()
        entered.set()
        await asyncio.Event().wait()

    runner, _ = runner_for(plugin_module, [])
    runner.provider.text_chat = AsyncMock(side_effect=calls)
    event = FakeEvent("/aa 画只猫")
    register(runner, event)
    await plugin_module.ImageGenPlugin(None, config).ensure_image_tool_call(
        event, runner.run_context
    )
    task = asyncio.create_task(step(runner))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not event.get_extra("imagegen_attempted")


async def test_repair_can_refuse_without_retrying(plugin_module, config):
    declined = response("无法生成这类图片。")
    runner, _ = runner_for(plugin_module, [response(), declined])
    event = FakeEvent("/aa 画只猫")
    register(runner, event)
    await plugin_module.ImageGenPlugin(None, config).ensure_image_tool_call(
        event, runner.run_context
    )
    assert await step(runner) == [declined]
    assert runner.provider.text_chat.await_count == 2


async def test_native_stop_during_repair_does_not_yield_a_response(
    plugin_module, config
):
    runner, _ = runner_for(plugin_module, [response()])

    async def stopped(pending):
        pending.close()
        return None

    runner._await_or_stop = stopped
    event = FakeEvent("/aa 画只猫")
    register(runner, event)
    await plugin_module.ImageGenPlugin(None, config).ensure_image_tool_call(
        event, runner.run_context
    )
    assert await step(runner) == []
    assert not event.get_extra("imagegen_tool_issued")
    assert not event.get_extra("imagegen_attempted")


async def test_reused_runner_does_not_guard_the_next_normal_turn(plugin_module, config):
    runner, _ = runner_for(plugin_module, [response("normal reply")])
    event = FakeEvent("/aa 画只猫")
    register(runner, event)
    await plugin_module.ImageGenPlugin(None, config).ensure_image_tool_call(
        event, runner.run_context
    )
    # Simulate a framework reusing/resetting this instance for a different turn.
    runner.run_context = SimpleNamespace(messages=[{"role": "user", "content": "你好"}])
    runner.req = SimpleNamespace(**vars(runner.req))
    runner.streaming = True
    assert (await step(runner))[0].completion_text == "normal reply"
    assert runner.provider.text_chat.await_count == 1
    assert runner.streaming
    assert not runner._imagegen_call_guard.repair_used
    assert not event.get_extra("imagegen_tool_issued")


async def test_reused_runner_gets_a_fresh_guard_for_new_image_turn(
    plugin_module, config
):
    image_reply = response(calls=[plugin_module.TOOL_NAME])
    runner, _ = runner_for(plugin_module, [response(), image_reply])
    event = FakeEvent("/aa 画只猫")
    register(runner, event)
    plugin = plugin_module.ImageGenPlugin(None, config)
    await plugin.ensure_image_tool_call(event, runner.run_context)
    first = runner._imagegen_call_guard
    first.repair_used = True
    runner.run_context = SimpleNamespace(
        messages=[{"role": "user", "content": "画只狗"}]
    )
    runner.req = SimpleNamespace(**vars(runner.req))
    next_event = FakeEvent("/aa 画只狗")
    register(runner, next_event)
    await plugin.ensure_image_tool_call(next_event, runner.run_context)
    assert runner._imagegen_call_guard is not first
    assert await step(runner) == [image_reply]
    assert runner.provider.text_chat.await_count == 2
    assert next_event.get_extra("imagegen_tool_issued")
    assert not event.get_extra("imagegen_tool_issued")


async def test_unknown_runner_interface_is_skipped(plugin_module, config, caplog):
    runner, _ = runner_for(plugin_module, [response()])
    runner._sanitize_contexts_for_provider = None
    original = runner._iter_llm_responses
    event = FakeEvent("/aa 画只猫")
    register(runner, event)
    await plugin_module.ImageGenPlugin(None, config).ensure_image_tool_call(
        event, runner.run_context
    )
    assert runner._iter_llm_responses is original and runner.streaming
    assert "incompatible" in caplog.text


async def test_duplicate_hook_does_not_stack_wrappers(plugin_module, config):
    runner, _ = runner_for(plugin_module, [response(calls=[plugin_module.TOOL_NAME])])
    event = FakeEvent("/aa 画只猫")
    register(runner, event)
    plugin = plugin_module.ImageGenPlugin(None, config)
    await plugin.ensure_image_tool_call(event, runner.run_context)
    original = runner._iter_llm_responses
    await plugin.ensure_image_tool_call(event, runner.run_context)
    assert runner._iter_llm_responses is original
    assert (await step(runner))[0].tools_call_name == [plugin_module.TOOL_NAME]
    assert runner.provider.text_chat.await_count == 1


async def test_failed_image_prompt_cannot_be_rewritten_to_retry_same_event(
    plugin_module, config
):
    config["show_progress"] = False
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(side_effect=ImageGenError("内容被拒绝"))
    event = FakeEvent("/aa 画只猫")
    failed = await plugin.generate_image(event, "cat")
    assert await plugin.generate_image(event, "different cat") == failed
    plugin.client.generate.assert_awaited_once()
    # Only a fresh user message can retry; no automatic content-policy bypass.
    await plugin.generate_image(FakeEvent("/aa 再画一张"), "different cat")
    assert plugin.client.generate.await_count == 2
