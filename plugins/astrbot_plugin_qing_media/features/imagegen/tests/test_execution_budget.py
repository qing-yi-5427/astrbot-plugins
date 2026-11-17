import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_qing_media.features.imagegen.execution_budget import (
    install_execution_budget,
)

from .conftest import FakeEvent
from .test_call_guard import register, response, runner_for
from .test_client import PNG

NAME = "astrbot_generate_image"


class Executor:
    def __init__(self):
        self.calls = []
        self.closed = 0

    async def execute(self, tool, run_context, **tool_args):
        self.calls.append((tool, run_context, tool_args))
        try:
            yield "native result"
        finally:
            self.closed += 1


def runner_for_budget(timeout=60):
    return SimpleNamespace(
        req=object(),
        run_context=SimpleNamespace(
            context=object(), messages=[], tool_call_timeout=timeout
        ),
        tool_executor=Executor(),
    )


@pytest.mark.parametrize("original_timeout,expected", [(60, 448), (900, 900)])
async def test_only_image_call_gets_scoped_timeout(original_timeout, expected):
    runner = runner_for_budget(original_timeout)
    native = runner.tool_executor
    context = runner.run_context
    image = SimpleNamespace(name=NAME)
    memory = SimpleNamespace(name="recall_memory")
    assert install_execution_budget(runner, NAME, 300, 1)
    outputs = await asyncio.gather(
        anext(runner.tool_executor.execute(image, context, prompt="cat")),
        anext(runner.tool_executor.execute(memory, context)),
    )
    assert outputs == ["native result", "native result"]
    image_call, memory_call = native.calls
    assert image_call[0] is image and image_call[2] == {"prompt": "cat"}
    scoped = image_call[1]
    assert scoped is not context and scoped.tool_call_timeout == expected
    assert scoped.context is context.context and scoped.messages is context.messages
    assert memory_call[1] is context and context.tool_call_timeout == original_timeout


async def test_idempotent_install_and_runner_reuse_do_not_stack_or_leak():
    runner = runner_for_budget()
    native = runner.tool_executor
    context = runner.run_context
    assert install_execution_budget(runner, NAME, 300, 1)
    wrapper = runner.tool_executor
    assert install_execution_budget(runner, NAME, 500, 2)
    assert runner.tool_executor is wrapper and wrapper.seconds == 776
    runner.req = object()
    assert [r async for r in wrapper.execute(SimpleNamespace(name=NAME), context)]
    assert native.calls[-1][1] is context
    assert install_execution_budget(runner, NAME, 300, 1)
    assert runner.tool_executor.original is native
    assert [
        r
        async for r in runner.tool_executor.execute(SimpleNamespace(name=NAME), context)
    ]
    assert native.calls[-1][1].tool_call_timeout == 448


async def test_context_mismatch_passes_through():
    runner = runner_for_budget()
    native = runner.tool_executor
    assert install_execution_budget(runner, NAME, 300, 1)
    other = SimpleNamespace(tool_call_timeout=60)
    assert [
        r async for r in runner.tool_executor.execute(SimpleNamespace(name=NAME), other)
    ]
    assert native.calls[-1][1] is other


@pytest.mark.parametrize(
    "kind", ["no_executor", "no_timeout", "invalid_timeout", "no_request"]
)
def test_incompatible_runner_is_untouched(kind):
    runner = runner_for_budget()
    if kind == "no_executor":
        runner.tool_executor = object()
    elif kind == "no_timeout":
        del runner.run_context.tool_call_timeout
    elif kind == "invalid_timeout":
        runner.run_context.tool_call_timeout = True
    else:
        runner.req = None
    executor = runner.tool_executor
    assert not install_execution_budget(runner, NAME, 300, 1)
    assert runner.tool_executor is executor


async def test_delegate_closed_on_early_exit():
    runner = runner_for_budget()
    native = runner.tool_executor
    install_execution_budget(runner, NAME, 300, 1)
    iterator = runner.tool_executor.execute(
        SimpleNamespace(name=NAME), runner.run_context
    )
    assert await anext(iterator) == "native result"
    await iterator.aclose()
    assert native.closed == 1


async def test_abort_cancels_native_execution():
    runner = runner_for_budget()
    entered, closed = asyncio.Event(), asyncio.Event()

    async def execute(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
            yield "not reached"
        finally:
            closed.set()

    runner.tool_executor = SimpleNamespace(execute=execute)
    install_execution_budget(runner, NAME, 300, 1)
    iterator = runner.tool_executor.execute(
        SimpleNamespace(name=NAME), runner.run_context
    )
    task = asyncio.create_task(anext(iterator))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and runner.run_context.tool_call_timeout == 60


async def test_execution_budget_still_installed_when_repair_disabled(
    plugin_module, config
):
    config["ensure_tool_call"] = False
    runner, _ = runner_for(plugin_module, [response()])
    runner.tool_executor = Executor()
    runner.run_context.tool_call_timeout = 60
    original = runner._iter_llm_responses
    event = FakeEvent("/aa 画一张猫")
    register(runner, event)
    await plugin_module.ImageGenPlugin(None, config).ensure_image_tool_call(
        event, runner.run_context
    )
    assert event.get_extra("imagegen_execution_budget") == 448
    assert runner._iter_llm_responses is original and runner.streaming
    assert not event.get_extra("imagegen_call_guard_active")


async def test_slow_progress_notice_does_not_block_generation(
    plugin_module, config, monkeypatch
):
    monkeypatch.setattr(plugin_module, "NOTICE_TIMEOUT", 0.01)
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(return_value=[PNG])
    event = FakeEvent()
    original = event.send
    cancelled = asyncio.Event()

    async def send(result):
        if hasattr(result, "text"):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        await original(result)

    event.send = send
    result = json.loads(await asyncio.wait_for(plugin.generate_image(event, "cat"), 1))
    assert cancelled.is_set() and result["sent"] == 1
    plugin.client.generate.assert_awaited_once()
    assert len(event.sent) == 1


async def test_slow_image_send_is_unknown_not_retried(
    plugin_module, config, monkeypatch
):
    config["show_progress"] = False
    monkeypatch.setattr(plugin_module, "IMAGE_SEND_TIMEOUT", 0.01)
    confirmation = AsyncMock(return_value=False)
    monkeypatch.setattr(plugin_module, "confirm_group_delivery", confirmation)
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(return_value=[PNG])
    event = FakeEvent()
    event.send = AsyncMock(side_effect=lambda result: None)

    async def slow_send(result):
        await asyncio.Event().wait()

    event.send.side_effect = slow_send
    first = await asyncio.wait_for(plugin.generate_image(event, "cat"), 1)
    result = json.loads(first)
    assert result["status"] == "unconfirmed" and result["generated"] == 1
    assert result["delivery_outcome"] == "unknown" and result["sent"] == 0
    assert await plugin.generate_image(event, "rewritten cat") == first
    confirmation.assert_awaited_once()
    event.send.assert_awaited_once()
    plugin.client.generate.assert_awaited_once()
    assert not plugin._busy_sessions and not plugin._slots.locked()
