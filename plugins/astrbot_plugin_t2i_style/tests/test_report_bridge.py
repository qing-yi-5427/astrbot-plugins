"""Per-group themes, prepared privacy-filtered data and report lifecycle."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_t2i_style.report_bridge import REPORT_PLUGIN, ReportBridge, Snapshot


class Templates:
    def render_template(self, template_name, **kwargs):
        return "<html>native report</html>"


class Generator:
    def __init__(self):
        self.html_templates = Templates()

    async def generate_image_report(
        self, analysis_result, group_id, html_render_func, avatar_cache_namespace=None
    ):
        render = self.html_templates.render_template
        render(
            "topic_item.html",
            topics=[
                {
                    "topic": {"topic": "话题-" + group_id},
                    "contributors": "群友",
                    "detail": "<p>正文</p>",
                    "index": 1,
                }
            ],
        )
        await asyncio.sleep(0)
        render(
            "user_title_item.html",
            titles=[
                {
                    "name": "",
                    "title": "称号",
                    "mbti": "INTJ",
                    "reason": "已脱敏",
                    "avatar_data": "private-hidden-avatar",
                    "user_id": "private-hidden-id",
                }
            ],
        )
        render(
            "quote_item.html",
            quotes=[
                {
                    "sender": "",
                    "content": "金句",
                    "reason": "点评",
                    "avatar_url": "http://private-avatar",
                }
            ],
        )
        render("chat_quality_item.html", title="锐评", summary="总结", dimensions=[])
        render(
            "activity_chart.html",
            chart_data=[{"hour": h, "count": h} for h in range(24)],
        )
        html = render(
            "image_template.html",
            current_date="2026年11月17日",
            message_count=100,
            participant_count=5,
        )
        return await html_render_func(html, {}, False, {"type": "png"})


def bridge():
    resolver = lambda session: {
        "template": "starrail" if session == "bot:GroupMessage:2" else "genshin"
    }
    repository = lambda identity: {"id": identity, "source": "{{CONTENT}}", "files": {}}
    return ReportBridge(
        resolver,
        repository,
        AsyncMock(return_value=True),
        lambda session: "http://renderer/text2img",
    )


@pytest.mark.asyncio
async def test_two_concurrent_groups_get_their_own_themes_and_report_data():
    generator = Generator()
    binding = bridge()
    binding.attach_generator(generator)
    calls = []

    async def render(html, data, *args):
        calls.append(data)
        return "PNG"

    assert await asyncio.gather(
        *(
            generator.generate_image_report(
                {}, str(group), render, avatar_cache_namespace="bot"
            )
            for group in (1, 2)
        )
    ) == ["PNG", "PNG"]
    by_topic = {d["t2i_report"]["topics"][0]["title"]: d for d in calls}
    assert by_topic["话题-1"]["t2i_style"]["template"] == "genshin"
    assert by_topic["话题-2"]["t2i_style"]["template"] == "starrail"
    assert by_topic["话题-2"]["t2i_theme"]["id"] == "starrail"
    binding.uninstall()


@pytest.mark.asyncio
async def test_private_raw_analysis_and_avatar_data_are_never_copied():
    generator = Generator()
    binding = bridge()
    binding.attach_generator(generator)
    receiver = AsyncMock(return_value="PNG")
    await generator.generate_image_report(
        {"raw_secret": "do-not-copy"}, "1", receiver, avatar_cache_namespace="bot"
    )
    data = receiver.call_args.args[1]
    assert receiver.call_args.args[0] == ""
    serialized = json.dumps(data)
    for forbidden in (
        "do-not-copy",
        "private-hidden",
        "http://private-avatar",
        "raw_secret",
    ):
        assert forbidden not in serialized
    assert data["t2i_report"]["people"][0]["name"] == ""
    assert data["t2i_report"]["quotes"][0]["sender"] == ""
    binding.uninstall()


def test_prepared_mentions_keep_identity_but_drop_large_avatars_and_hidden_text():
    snapshot = Snapshot()
    fragment = (
        '<span class="user-capsule"><span style="background-image:url(data:image/jpeg;base64,'
        + "A" * 100000
        + ')"></span><span>海胆脑袋已治好</span></span><span style="display:none">private-hidden-name</span>正文'
    )
    snapshot.capture(
        "topic_item.html",
        {
            "topics": [
                {
                    "topic": {"topic": "主题"},
                    "contributors": "A<admin>、海胆脑袋已治好",
                    "detail": fragment,
                }
            ]
        },
    )
    prepared = snapshot.data["topics"][0]
    assert (
        'class="user-capsule"' in prepared["detail"]
        and "海胆脑袋已治好" in prepared["detail"]
    )
    assert (
        "private-hidden-name" not in prepared["detail"]
        and "data:image" not in prepared["detail"]
    )
    assert len(prepared["detail"]) < 200
    assert "A&lt;admin&gt;" in prepared["contributors"]


@pytest.mark.asyncio
async def test_missing_platform_uses_global_style_without_guessing_another_group():
    generator = Generator()
    binding = bridge()
    binding.resolver = lambda session: {
        "template": "genshin" if session is None else "wrong"
    }
    binding.attach_generator(generator)
    receiver = AsyncMock()
    await generator.generate_image_report({}, "2", receiver)
    assert receiver.call_args.args[1]["t2i_style"]["template"] == "genshin"
    binding.uninstall()


@pytest.mark.asyncio
async def test_old_service_keeps_original_callback_data():
    generator = Generator()
    binding = bridge()
    binding.capability_check.return_value = False
    binding.attach_generator(generator)
    receiver = AsyncMock()
    await generator.generate_image_report({}, "1", receiver)
    assert receiver.call_args.args[1] == {}
    binding.uninstall()


@pytest.mark.asyncio
async def test_native_callback_receives_the_matching_session_umo():
    generator = Generator()
    binding = bridge()
    binding.attach_generator(generator)
    calls = []

    async def render(html, data, return_url, options, umo=None):
        calls.append(umo)

    await generator.generate_image_report({}, "2", render, avatar_cache_namespace="bot")
    assert calls == ["bot:GroupMessage:2"]
    binding.uninstall()


@pytest.mark.asyncio
async def test_disabled_during_capability_probe_cannot_inject_late():
    generator = Generator()
    binding = bridge()
    gate = asyncio.Event()
    started = asyncio.Event()

    async def capability(**kwargs):
        started.set()
        await gate.wait()
        return True

    binding.capability_check = capability
    binding.attach_generator(generator)
    receiver = AsyncMock()
    task = asyncio.create_task(generator.generate_image_report({}, "1", receiver))
    await started.wait()
    binding.uninstall()
    gate.set()
    await task
    assert receiver.call_args.args[1] == {}


@pytest.mark.asyncio
async def test_uninstall_and_repeated_attachment_do_not_stack_wrappers():
    generator = Generator()
    binding = bridge()
    original = generator.generate_image_report
    assert binding.attach_generator(generator)
    wrapper = generator.generate_image_report
    assert (
        not binding.attach_generator(generator)
        and generator.generate_image_report is wrapper
    )
    binding.uninstall()
    assert generator.generate_image_report == original
    receiver = AsyncMock()
    await wrapper({}, "1", receiver)
    assert receiver.call_args.args[1] == {}


@pytest.mark.asyncio
async def test_plugin_reload_attaches_the_new_generator_instance():
    binding = bridge()
    old = Generator()
    new = Generator()
    for generator in (old, new):
        assert binding.attach(
            SimpleNamespace(
                name=REPORT_PLUGIN,
                activated=True,
                star_cls=SimpleNamespace(report_generator=generator),
            )
        )
        receiver = AsyncMock()
        await generator.generate_image_report(
            {}, "2", receiver, avatar_cache_namespace="bot"
        )
        assert receiver.call_args.args[1]["t2i_style"]["template"] == "starrail"
    assert not binding.attach(
        SimpleNamespace(
            name="another-plugin",
            star_cls=SimpleNamespace(report_generator=Generator()),
        )
    )
    binding.uninstall()


@pytest.mark.asyncio
async def test_reloaded_report_plugin_releases_and_revokes_the_old_generator():
    binding = bridge()
    old = Generator()
    new = Generator()
    metadata = lambda generator: SimpleNamespace(
        name=REPORT_PLUGIN,
        activated=True,
        star_cls=SimpleNamespace(report_generator=generator),
    )
    binding.attach(metadata(old))
    retained = old.generate_image_report
    binding.attach(metadata(new))
    assert old not in binding.owners and new in binding.owners
    receiver = AsyncMock()
    await retained({}, "1", receiver)
    assert receiver.call_args.args[1] == {}
    binding.uninstall()


@pytest.mark.asyncio
async def test_unrecognized_prepared_data_does_not_break_the_upstream_report():
    generator = Generator()
    binding = bridge()
    binding.attach_generator(generator)
    # Simulate an upstream shape change in a visible template, after the original
    # template still returned HTML; integration must become a pass-through.
    original_capture = generator.html_templates.render_template

    def changed(name, **data):
        if name == "topic_item.html":
            data = {"topics": [None]}
        return original_capture(name, **data)

    generator.html_templates.render_template = changed
    receiver = AsyncMock()
    await generator.generate_image_report({}, "1", receiver)
    assert receiver.call_args.args[1] == {}
    binding.uninstall()
