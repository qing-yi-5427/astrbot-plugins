import asyncio
import base64
import json
from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_t2i_style.core import (
    DEFAULTS,
    StyleController,
    StyleStore,
    config_defaults,
    validate_override,
)
from conftest import Event


@pytest.mark.parametrize(
    "patch",
    [
        {"template": "../../secret"},
        {"template": []},
        {"template": "unknown"},
        {"brand_name": "x" * 65},
        {"subtitle": "x" * 97},
        {"brand_name": "a\nb"},
        {"brand_name": None},
        {"show_avatar": 1},
        {"show_footer": "false"},
        {"avatar_base64": "not base64"},
        {"avatar_base64": 42},
        {"url": "https://evil"},
        {"avatar_base64": base64.b64encode(b"<svg/>").decode()},
    ],
)
def test_validation_rejects_bad_values(patch):
    with pytest.raises(ValueError):
        validate_override(patch)


def test_defaults_and_safe_text():
    assert config_defaults({}) == DEFAULTS
    assert config_defaults(
        {"theme": "miku", "brand": "中文", "subtitle": "", "show_avatar": False}
    ) == (
        DEFAULTS
        | {
            "template": "miku",
            "brand_name": "中文",
            "subtitle": "",
            "show_avatar": False,
        }
    )
    text = '#panic("injection") {{ text }} <script>evil</script>'
    assert validate_override({"brand_name": text})["brand_name"] == text


@pytest.mark.asyncio
async def test_layering_persistence_reset_and_reload(tmp_path, event):
    store = StyleStore(tmp_path, {"theme": "genshin", "brand": "Web default"})
    await store.initialize()
    control = StyleController(store)
    await control.execute(event, "global use starrail")
    await control.execute(event, "global name Global Name")
    await control.execute(event, "use miku")
    await control.execute(event, "name Local Name")
    assert store.effective(event.unified_msg_origin)["template"] == "miku"
    other = Event(session="other:GroupMessage:1")
    assert store.effective(other.unified_msg_origin)["brand_name"] == "Global Name"
    reloaded = StyleStore(tmp_path, {"theme": "rio", "brand": "New default"})
    await reloaded.initialize()
    assert reloaded.effective(event.unified_msg_origin)["brand_name"] == "Local Name"
    await control.execute(event, "reset")
    assert store.effective(event.unified_msg_origin)["template"] == "starrail"
    await control.execute(event, "global reset")
    assert store.effective(event.unified_msg_origin)["brand_name"] == "Web default"
    assert not store.state["sessions"]
    assert store.path.stat().st_mode & 0o077 == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "list",
        "status",
        "use rio",
        "global use genshin",
        "name evil",
        "avatar 123456",
        "reset",
    ],
)
async def test_all_control_commands_require_admin(tmp_path, command):
    store = StyleStore(tmp_path)
    await store.initialize()
    download = AsyncMock()
    control = StyleController(store, download)
    text = await control.execute(Event(admin=False), command)
    assert "仅 AstrBot 管理员" in text
    assert store.state == {"version": 1, "global": {}, "sessions": {}}
    assert not store.path.exists()
    download.assert_not_awaited()


@pytest.mark.asyncio
async def test_commands_aliases_full_text_scope_validation(tmp_path, event):
    store = StyleStore(tmp_path)
    await store.initialize()
    control = StyleController(store)
    assert "codex" in await control.execute(event, "列表")
    assert "已保存" in await control.execute(event, "名称 我的 bot name")
    assert store.effective(event.unified_msg_origin)["brand_name"] == "我的 bot name"
    assert "未修改" in await control.execute(event, "use ../../rio")
    assert "未修改" in await control.execute(event, "subtitle " + "x" * 97)
    assert "未修改" in await control.execute(event, "name line\nbreak")
    assert "当前会话" in await control.execute(event, "")
    assert "全局" in await control.execute(event, "全局 状态")
    assert "当前会话" in await control.execute(event, "session status")
    assert "/t2i" in await control.execute(event, "unknown")
    assert "/t2i" in await control.execute(event, "reset another_session")
    assert "缺少当前会话" in await control.execute(Event(session=""), "use rio")
    assert "缺少当前会话" in await control.execute(Event(session=None), "status")


@pytest.mark.asyncio
async def test_concurrent_session_updates_do_not_lose_writes(tmp_path):
    store = StyleStore(tmp_path)
    await store.initialize()
    await asyncio.gather(
        *(
            store.update(f"bot:GroupMessage:{i}", {"brand_name": str(i)})
            for i in range(30)
        )
    )
    restored = StyleStore(tmp_path)
    await restored.initialize()
    assert len(restored.state["sessions"]) == 30
    for i in range(30):
        assert restored.effective(f"bot:GroupMessage:{i}")["brand_name"] == str(i)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "not-json",
        '{"version": 7}',
        '{"version": true, "global": {}, "sessions": {}}',
        json.dumps({"version": 1, "global": {"template": "evil"}, "sessions": {}}),
        json.dumps(
            {
                "version": 1,
                "global": {},
                "sessions": {"s": {"avatar_base64": "corrupt"}},
            }
        ),
    ],
)
async def test_corrupt_storage_fails_closed_and_is_not_overwritten(tmp_path, bad):
    path = tmp_path / "styles.json"
    path.write_text(bad)
    store = StyleStore(tmp_path)
    with pytest.raises((ValueError, json.JSONDecodeError)):
        await store.initialize()
    assert path.read_text() == bad


@pytest.mark.asyncio
async def test_failed_save_is_transactional(tmp_path, event, monkeypatch):
    store = StyleStore(tmp_path)
    await store.initialize()

    def fail(state):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_save", fail)
    assert "保存样式失败" in await StyleController(store).execute(event, "use rio")
    assert store.effective(event.unified_msg_origin)["template"] == "codex"


@pytest.mark.asyncio
async def test_cancelled_save_finishes_consistently(tmp_path, monkeypatch):
    import threading

    store = StyleStore(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = store._save

    def delayed(state):
        entered.set()
        assert release.wait(3)
        original(state)

    monkeypatch.setattr(store, "_save", delayed)
    task = asyncio.create_task(store.update("session", {"template": "rio"}))
    assert await asyncio.to_thread(entered.wait, 3)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    restored = StyleStore(tmp_path)
    await restored.initialize()
    assert restored.effective("session") == store.effective("session")
    assert store.effective("session")["template"] == "rio"


@pytest.mark.asyncio
async def test_theme_defaults_omitted_and_default_masks_global_custom(tmp_path, event):
    from astrbot_plugin_t2i_style.avatars import validate_image
    from conftest import image_bytes

    avatar = validate_image(image_bytes())
    store = StyleStore(tmp_path)
    await store.initialize()
    assert store.render_style(event.unified_msg_origin) == {
        "template": "codex",
        "show_avatar": True,
        "show_footer": True,
    }
    await store.update(None, {"brand_name": "Global custom", "avatar_base64": avatar})
    controller = StyleController(store, downloader=AsyncMock(return_value=avatar))
    await controller.execute(event, "使用 初音")
    assert store.render_style(event.unified_msg_origin)["template"] == "miku"
    assert store.render_style(event.unified_msg_origin)["avatar_base64"] == avatar
    await controller.execute(event, "头像 默认")
    await controller.execute(event, "名称 默认")
    assert "avatar_base64" not in store.render_style(event.unified_msg_origin)
    assert "brand_name" not in store.render_style(event.unified_msg_origin)
    assert store.render_style("other")["avatar_base64"] == avatar
    reloaded = StyleStore(tmp_path)
    await reloaded.initialize()
    assert "avatar_base64" not in reloaded.render_style(event.unified_msg_origin)
    await controller.execute(event, "avatar inherit")
    assert store.render_style(event.unified_msg_origin)["avatar_base64"] == avatar
    await controller.execute(event, "avatar 123456")
    assert store.render_style(event.unified_msg_origin)["avatar_base64"] == avatar


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "alias, expected",
    [
        ("原版", "codex"),
        ("初音", "miku"),
        ("原神", "genshin"),
        ("铁道", "starrail"),
        ("星穹铁道", "starrail"),
        ("绝区零", "zzz"),
        ("莉音", "rio"),
    ],
)
async def test_official_theme_aliases(tmp_path, event, alias, expected):
    store = StyleStore(tmp_path)
    await store.initialize()
    assert "已保存" in await StyleController(store).execute(event, "use " + alias)
    assert store.render_style(event.unified_msg_origin)["template"] == expected


@pytest.mark.parametrize("theme", ["midnight", "sakura", "forest", "paper"])
def test_draft_themes_are_not_supported(theme):
    with pytest.raises(ValueError):
        validate_override({"template": theme})


@pytest.mark.asyncio
async def test_service_warning_not_silent_success(tmp_path, event):
    store = StyleStore(tmp_path)
    warning = AsyncMock(return_value="旧服务不支持 style_protocol: 1，未启用主题注入。")
    controller = StyleController(store, capability_status=warning)
    text = await controller.execute(event, "use miku")
    assert "已保存" in text and "未启用" in text
    assert "旧服务" in await controller.execute(event, "status")


@pytest.mark.asyncio
async def test_storage_symlink_refused_without_reading_target(tmp_path):
    target = tmp_path / "private.json"
    target.write_text("must not be read")
    (tmp_path / "styles.json").symlink_to(target)
    with pytest.raises((ValueError, OSError)):
        await StyleStore(tmp_path).initialize()
    assert target.read_text() == "must not be read"


@pytest.mark.asyncio
async def test_oversized_storage_refused(tmp_path, monkeypatch):
    from astrbot_plugin_t2i_style import core

    monkeypatch.setattr(core, "MAX_STATE_BYTES", 16)
    (tmp_path / "styles.json").write_bytes(b"x" * 17)
    with pytest.raises(ValueError):
        await StyleStore(tmp_path).initialize()


@pytest.mark.asyncio
async def test_local_config_avatar_default_inheritance(tmp_path, event):
    from conftest import image_bytes

    source = tmp_path / "configured.png"
    source.write_bytes(image_bytes())
    store = StyleStore(tmp_path, {"theme": "miku", "avatar_path": str(source)})
    await store.initialize()
    assert store.render_style(event.unified_msg_origin)["avatar_base64"]
    controller = StyleController(store)
    await controller.execute(event, "avatar default")
    assert "avatar_base64" not in store.render_style(event.unified_msg_origin)
    await controller.execute(event, "reset")
    assert store.render_style(event.unified_msg_origin)["avatar_base64"]
