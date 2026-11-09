import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from astrbot_plugin_t2i_style.bridge import _RENDER_EVENT, BRAND_MARKER, StyleBridge
from astrbot_plugin_t2i_style.models import MODEL_KEY, UNKNOWN_MODEL
from conftest import Event


@pytest.fixture
def owners():
    class Network:
        async def render_custom_template(self, tmpl_str, tmpl_data, *args, **kwargs):
            await asyncio.sleep(0)
            return dict(tmpl_data), args, kwargs

    class Stage:
        async def process(self, event):
            if getattr(event, "fail", False):
                raise RuntimeError("stage failure")
            event.rendered.append(
                await Network().render_custom_template(
                    event.template,
                    event.data,
                    True,
                    {"quality": 85},
                    endpoint="http://configured:8000/text2img",
                )
            )
            yield "first"
            event.rendered.append(
                await Network().render_custom_template(event.template, event.data)
            )

    class Runner:
        async def _iter_llm_responses(self, **kwargs):
            yield SimpleNamespace(
                raw_completion={"model": "actual-model"},
                role="assistant",
                is_chunk=False,
            )

    return Stage, Network, Runner


async def consume(stage, event):
    async for _ in stage().process(event):
        assert _RENDER_EVENT.get() is None


@pytest.mark.asyncio
async def test_repository_bundle_is_added_only_to_the_render_request(owners):
    stage, network, _ = owners
    bundle = {"id": "custom", "source": "{{CONTENT}}", "files": {}}
    bridge = StyleBridge(
        lambda _: {"template": "custom"},
        AsyncMock(return_value=True),
        theme_resolver=lambda _: bundle,
        theme_capability_check=AsyncMock(return_value=True),
    )
    bridge.install(stage, network)
    try:
        event = Event()
        await consume(stage, event)
        assert all(data[0]["t2i_theme"] == bundle for data in event.rendered)
        assert all(data[0]["model_name"] == "actual-model" for data in event.rendered)
        assert "t2i_theme" not in event.data
    finally:
        bridge.uninstall()


@pytest.mark.asyncio
async def test_old_renderer_gets_builtin_fallback_instead_of_an_unknown_id(owners):
    stage, network, _ = owners
    bridge = StyleBridge(
        lambda _: {"template": "custom"},
        AsyncMock(return_value=True),
        theme_resolver=lambda _: {"id": "custom"},
        theme_capability_check=AsyncMock(return_value=False),
    )
    bridge.install(stage, network)
    try:
        event = Event()
        await consume(stage, event)
        assert all(
            data[0]["t2i_style"]["template"] == "codex" for data in event.rendered
        )
        assert all("t2i_theme" not in data[0] for data in event.rendered)
    finally:
        bridge.uninstall()


@pytest.mark.asyncio
async def test_unload_during_theme_capability_check_leaves_request_untouched(owners):
    stage, network, _ = owners
    started = asyncio.Event()
    release = asyncio.Event()

    async def check(*args, **kwargs):
        started.set()
        await release.wait()
        return True

    bridge = StyleBridge(
        lambda _: {"template": "custom"},
        AsyncMock(return_value=True),
        theme_resolver=lambda _: {"id": "custom"},
        theme_capability_check=check,
    )
    bridge.install(stage, network)
    event = Event()
    task = asyncio.create_task(consume(stage, event))
    await started.wait()
    bridge.uninstall()
    release.set()
    await task
    assert all(
        "t2i_theme" not in data[0] and "t2i_style" not in data[0]
        for data in event.rendered
    )


@pytest.mark.asyncio
async def test_concurrent_sessions_preserve_data_and_forward_options(owners):
    stage, network, _ = owners
    resolver = lambda session: {"template": "miku" if session == "a" else "rio"}
    bridge = StyleBridge(resolver)
    bridge.install(stage, network)
    try:
        first, second = Event("a"), Event("b")
        await asyncio.gather(consume(stage, first), consume(stage, second))
        for event, expected in [(first, "miku"), (second, "rio")]:
            assert len(event.rendered) == 2
            for data, args, kwargs in event.rendered:
                assert data["t2i_style"] == {"template": expected}
                assert data["model_name"] == "actual-model"
            assert event.rendered[0][1] == (True, {"quality": 85})
            assert event.rendered[0][2] == {
                "endpoint": "http://configured:8000/text2img"
            }
            assert "t2i_style" not in event.data
        direct = await network().render_custom_template(
            BRAND_MARKER, {"text": "direct"}
        )
        assert "t2i_style" not in direct[0]
    finally:
        bridge.uninstall()
    assert _RENDER_EVENT.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "llm, template", [(False, BRAND_MARKER), (True, "<html>different plugin</html>")]
)
async def test_only_llm_codex_brand_is_enhanced(owners, llm, template):
    stage, network, _ = owners
    check = AsyncMock(return_value=True)
    bridge = StyleBridge(lambda _: {"template": "miku"}, check)
    bridge.install(stage, network)
    try:
        event = Event(llm=llm)
        event.template = template
        await consume(stage, event)
        assert all("t2i_style" not in value[0] for value in event.rendered)
        assert all(
            value[0]["model_name"] == "existing-model" for value in event.rendered
        )
        check.assert_not_awaited()
    finally:
        bridge.uninstall()


@pytest.mark.asyncio
async def test_capability_failure_disables_injection_and_endpoint_is_forwarded(owners):
    stage, network, _ = owners
    check = AsyncMock(return_value=False)
    bridge = StyleBridge(lambda _: {"template": "miku"}, check)
    bridge.install(stage, network)
    try:
        event = Event()
        await consume(stage, event)
        assert all("t2i_style" not in value[0] for value in event.rendered)
        assert all(value[0]["model_name"] == "actual-model" for value in event.rendered)
        assert check.await_args_list[0].kwargs == {
            "endpoint": "http://configured:8000/text2img"
        }
    finally:
        bridge.uninstall()


@pytest.mark.asyncio
async def test_scope_reset_on_errors_and_early_close(owners):
    stage, network, _ = owners
    bridge = StyleBridge(lambda _: {"template": "miku"})
    bridge.install(stage, network)
    try:
        iterator = stage().process(Event())
        await anext(iterator)
        assert _RENDER_EVENT.get() is None
        await iterator.aclose()
        event = Event()
        event.fail = True
        with pytest.raises(RuntimeError):
            await consume(stage, event)
        assert _RENDER_EVENT.get() is None
    finally:
        bridge.uninstall()


@pytest.mark.asyncio
async def test_cancellation_and_uninstall_while_probe_in_flight(owners):
    stage, network, _ = owners
    entered, release = asyncio.Event(), asyncio.Event()

    async def check(*args, **kwargs):
        entered.set()
        await release.wait()
        return True

    bridge = StyleBridge(lambda _: {"template": "miku"}, check)
    bridge.install(stage, network)
    task = asyncio.create_task(consume(stage, Event()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _RENDER_EVENT.get() is None
    entered.clear()
    event = Event()
    task = asyncio.create_task(consume(stage, event))
    await entered.wait()
    bridge.uninstall()
    release.set()
    await task
    assert all("t2i_style" not in value[0] for value in event.rendered)


@pytest.mark.asyncio
@pytest.mark.parametrize("style_first", [True, False])
@pytest.mark.parametrize("uninstall_style_first", [True, False])
async def test_real_footer_four_install_uninstall_orders(
    owners, style_first, uninstall_style_first
):
    footer_module = pytest.importorskip("astrbot_plugin_model_footer.bridge")
    stage, network, runner = owners
    bridge = StyleBridge(lambda _: {"template": "rio"})
    footer = footer_module.FooterBridge()
    install_style = lambda: bridge.install(stage, network, runner)
    install_footer = lambda: footer.install(stage, network, runner)
    for install in (
        [install_style, install_footer]
        if style_first
        else [install_footer, install_style]
    ):
        install()

    async def call_runner(event):
        event.set_extra(MODEL_KEY, None)
        instance = runner()
        instance.run_context = SimpleNamespace(context=SimpleNamespace(event=event))
        async for _ in instance._iter_llm_responses():
            pass

    event = Event()
    try:
        await call_runner(event)
        await consume(stage, event)
        assert event.rendered[0][0]["model_name"] == "actual-model"
        assert event.rendered[0][0]["t2i_style"]["template"] == "rio"
        first, second = (bridge, footer) if uninstall_style_first else (footer, bridge)
        first.uninstall()
        event = Event()
        await call_runner(event)
        await consume(stage, event)
        assert ("t2i_style" in event.rendered[0][0]) is (not uninstall_style_first)
        # Either remaining plugin can now provide the per-reply model on its own.
        assert event.rendered[0][0]["model_name"] == "actual-model"
        second.uninstall()
        event = Event()
        await call_runner(event)
        assert event.get_extra(MODEL_KEY) is None
        await consume(stage, event)
        assert "t2i_style" not in event.rendered[0][0]
        assert event.rendered[0][0]["model_name"] == "existing-model"
        assert _RENDER_EVENT.get() is None
        assert footer_module._RENDER_EVENT.get() is None
    finally:
        bridge.uninstall()
        footer.uninstall()


@pytest.mark.asyncio
async def test_retained_wrapper_stays_inactive_after_reinstall(owners):
    stage, network, _ = owners
    resolver = Mock(return_value={"template": "rio"})
    bridge = StyleBridge(resolver)
    original_stage, original_render = stage.process, network.render_custom_template
    bridge.install(stage, network)
    retained_stage, retained_render = stage.process, network.render_custom_template

    async def later_stage(instance, event):
        async for item in retained_stage(instance, event):
            yield item

    async def later_render(*args, **kwargs):
        return await retained_render(*args, **kwargs)

    stage.process, network.render_custom_template = later_stage, later_render
    bridge.uninstall()
    assert network.render_custom_template is later_render
    # Simulate the later bridge restoring this plugin's now-inert wrappers.
    stage.process, network.render_custom_template = retained_stage, retained_render
    await consume(stage, Event())
    resolver.assert_not_called()
    bridge.install(stage, network)
    try:
        event = Event()
        await consume(stage, event)
        assert resolver.call_count == 2  # Once per render, never once per old layer.
        assert all(
            value[0]["t2i_style"]["template"] == "rio" for value in event.rendered
        )
    finally:
        bridge.uninstall()
    event = Event()
    await consume(stage, event)
    assert all("t2i_style" not in value[0] for value in event.rendered)
    # Restore the isolated fixture's originals for a strict final assertion.
    stage.process, network.render_custom_template = original_stage, original_render


@pytest.mark.asyncio
async def test_model_unknown_is_explicit_and_show_footer_is_presentation_only(owners):
    stage, network, runner = owners
    bridge = StyleBridge(lambda _: {"template": "miku", "show_footer": False})
    bridge.install(stage, network, runner)
    try:
        event = Event()
        event.set_extra(MODEL_KEY, None)
        await consume(stage, event)
        assert event.rendered[0][0]["model_name"] == UNKNOWN_MODEL
        assert event.rendered[0][0]["t2i_style"]["show_footer"] is False
        # The caller dictionary remains untouched, including its original metadata.
        assert event.data["model_name"] == "existing-model"
    finally:
        bridge.uninstall()
