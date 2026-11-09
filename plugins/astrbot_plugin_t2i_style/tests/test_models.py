import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from astrbot_plugin_t2i_style.bridge import StyleBridge
from astrbot_plugin_t2i_style.models import (
    MODEL_KEY,
    clean_model,
    record_response,
    requested_model,
    response_model,
)
from conftest import Event


def response(model=None, *, version=None, role="assistant", is_chunk=False):
    return SimpleNamespace(
        raw_completion={"model": model, "model_version": version},
        role=role,
        is_chunk=is_chunk,
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        ({"model": "actual-gpt"}, "actual-gpt"),
        ({"model": "", "model_version": "gemini-version"}, "gemini-version"),
        (SimpleNamespace(model="actual-object"), "actual-object"),
        (SimpleNamespace(model_version="actual-version"), "actual-version"),
        ({"model": {"secret": "not a string"}}, ""),
        (None, ""),
    ],
)
def test_raw_response_shape_compatibility(raw, expected):
    assert response_model(SimpleNamespace(raw_completion=raw)) == expected


def test_sanitize_bounded_model_name():
    assert clean_model("  GPT\n6\x00 sol\u202e ") == "GPT 6 sol"
    assert clean_model({"secret": "not a model"}) == ""
    assert len(clean_model("x" * 1000)) == 160


@pytest.mark.parametrize(
    "item",
    [
        None,
        response("wrong", role="err"),
        response("wrong", role="error"),
        response("wrong", is_chunk=True),
        response(),
    ],
)
def test_error_chunk_and_absent_model_do_not_replace_valid_record(item):
    event = Event()
    event.set_extra(MODEL_KEY, "correct-final")
    record_response(event, item)
    assert event.get_extra(MODEL_KEY) == "correct-final"


def test_raw_metadata_beats_real_request_fallback():
    event = Event()
    record_response(event, response("raw-final"), requested_model="request-alias")
    assert event.get_extra(MODEL_KEY) == "raw-final"
    record_response(event, response(), requested_model="actual-selected-request")
    assert event.get_extra(MODEL_KEY) == "actual-selected-request"


def test_requested_model_comes_only_from_current_runner_candidate():
    provider = Mock()
    provider.get_model.return_value = "fallback-selected"
    runner = SimpleNamespace(
        req=SimpleNamespace(model="explicit-primary"), provider=provider
    )
    assert requested_model(runner) == "explicit-primary"
    provider.get_model.assert_not_called()
    assert requested_model(runner, include_model=False) == "fallback-selected"
    runner.req.model = ""
    assert requested_model(runner) == "fallback-selected"
    provider.get_model.side_effect = RuntimeError("metadata unavailable")
    assert requested_model(runner) == ""
    # No context/default-provider accessor is present or needed.
    assert requested_model(SimpleNamespace()) == ""


@pytest.fixture
def runner_setup():
    class Stage:
        async def process(self, event):
            yield

    class Network:
        async def render_custom_template(self, tmpl_str, tmpl_data):
            return tmpl_data

    class Runner:
        async def _iter_llm_responses(self, *, include_model=True):
            self.received_include_model = include_model
            try:
                for item in self.responses:
                    await asyncio.sleep(0)
                    yield item
            finally:
                self.closed = True

    bridge = StyleBridge(lambda _: {"template": "miku"})
    original = Runner._iter_llm_responses
    bridge.install(Stage, Network, Runner)
    yield bridge, Stage, Network, Runner, original
    bridge.uninstall()


def configured_runner(
    cls, event, responses, *, selected="selected-provider", override="explicit-request"
):
    runner = cls()
    runner.run_context = SimpleNamespace(context=SimpleNamespace(event=event))
    runner.req = SimpleNamespace(model=override)
    runner.provider = SimpleNamespace(get_model=lambda: selected)
    runner.responses = responses
    runner.closed = False
    return runner


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "include_model, raw, expected",
    [
        (True, "raw-model", "raw-model"),
        (True, None, "explicit-request"),
        (False, None, "selected-provider"),
        (False, "fallback-real-model", "fallback-real-model"),
    ],
)
async def test_runner_capture_and_fallback_candidate(
    runner_setup, include_model, raw, expected
):
    bridge, _, _, cls, _ = runner_setup
    event = Event()
    event.set_extra(MODEL_KEY, None)
    runner = configured_runner(
        cls,
        event,
        [
            response("chunk-wrong", is_chunk=True),
            response("error-wrong", role="err"),
            response(raw),
        ],
    )
    items = [
        item async for item in runner._iter_llm_responses(include_model=include_model)
    ]
    assert len(items) == 3
    assert runner.received_include_model is include_model
    assert event.get_extra(MODEL_KEY) == expected
    assert runner.closed
    bridge.uninstall()


@pytest.mark.asyncio
async def test_runner_concurrency_never_crosses_event_model(runner_setup):
    _, _, _, cls, _ = runner_setup
    first, second = Event("first"), Event("second")
    runners = [
        configured_runner(cls, first, [response("first-raw")]),
        configured_runner(cls, second, [response(version="second-version")]),
    ]

    async def consume(runner):
        return [item async for item in runner._iter_llm_responses()]

    await asyncio.gather(*(consume(runner) for runner in runners))
    assert first.get_extra(MODEL_KEY) == "first-raw"
    assert second.get_extra(MODEL_KEY) == "second-version"


@pytest.mark.asyncio
async def test_runner_early_close_and_disable_mid_stream(runner_setup):
    bridge, _, _, cls, _ = runner_setup
    event = Event()
    event.set_extra(MODEL_KEY, None)
    runner = configured_runner(
        cls, event, [response(is_chunk=True), response("late-model")]
    )
    iterator = runner._iter_llm_responses()
    await anext(iterator)
    bridge.uninstall()
    await anext(iterator)
    assert event.get_extra(MODEL_KEY) is None
    await iterator.aclose()
    assert runner.closed


@pytest.mark.asyncio
async def test_runner_retained_wrapper_cannot_reactivate_after_reinstall(runner_setup):
    bridge, stage, network, cls, original = runner_setup
    retained = cls._iter_llm_responses

    async def later(instance, **kwargs):
        async for item in retained(instance, **kwargs):
            yield item

    cls._iter_llm_responses = later
    bridge.uninstall()
    assert cls._iter_llm_responses is later
    cls._iter_llm_responses = retained
    bridge.install(stage, network, cls)
    try:
        event = Event()
        event.set_extra(MODEL_KEY, None)
        recorder = Mock(wraps=event.set_extra)
        event.set_extra = recorder
        runner = configured_runner(cls, event, [response("real-final")])
        _ = [item async for item in runner._iter_llm_responses()]
        recorder.assert_called_once_with(MODEL_KEY, "real-final")
    finally:
        bridge.uninstall()
    event.set_extra(MODEL_KEY, None)
    runner = configured_runner(cls, event, [response("must-not-record")])
    _ = [item async for item in runner._iter_llm_responses()]
    assert event.get_extra(MODEL_KEY) is None
    cls._iter_llm_responses = original


@pytest.mark.asyncio
async def test_runner_cancellation_closes_original_without_recording():
    entered = asyncio.Event()
    closed = []

    class Runner:
        async def _iter_llm_responses(self, **kwargs):
            try:
                entered.set()
                await asyncio.Event().wait()
                yield response("must-not-record")
            finally:
                closed.append(True)

    class Stage:
        async def process(self, event):
            yield

    class Network:
        async def render_custom_template(self, tmpl_str, tmpl_data):
            return tmpl_data

    event = Event()
    event.set_extra(MODEL_KEY, None)
    bridge = StyleBridge(lambda _: {"template": "miku"})
    bridge.install(Stage, Network, Runner)
    runner = configured_runner(Runner, event, [])

    async def consume():
        return [item async for item in runner._iter_llm_responses()]

    task = asyncio.create_task(consume())
    await entered.wait()
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert event.get_extra(MODEL_KEY) is None
        assert closed == [True]
    finally:
        bridge.uninstall()
