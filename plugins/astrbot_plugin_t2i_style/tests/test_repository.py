"""Git revision pinning, dynamic themes, validation, cache and rollback tests."""

import asyncio
import base64
import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_t2i_style.core import TEMPLATES, StyleController, StyleStore
from astrbot_plugin_t2i_style.repository import RepositoryError, ThemeRepository
from conftest import Event

REV_A = "a" * 40
REV_B = "b" * 40


def snapshot(revision=REV_A, identity="custom", source="{{CONTENT}}"):
    return {
        "version": 1,
        "revision": revision,
        "repository": "qing-yi-5427/astrbot-plugins",
        "directory": "t2i-themes",
        "bundles": {
            identity: {
                "schema": 1,
                "id": identity,
                "source": source,
                "meta": {"name": "自定义主题"},
                "files": {},
            }
        },
    }


@pytest.fixture
def repo(tmp_path):
    return ThemeRepository(
        tmp_path,
        {"repository_enabled": True},
        AsyncMock(return_value={"id": "custom", "name": "自定义主题"}),
    )


@pytest.mark.asyncio
async def test_success_uses_a_validated_local_cache_and_survives_reload(repo):
    repo._download = AsyncMock(return_value=snapshot())
    assert "已同步" in await repo.update()
    assert repo.path.stat().st_mode & 0o077 == 0
    loaded = ThemeRepository(
        repo.path.parent, {"repository_enabled": True}, repo.validator
    )
    loaded.load()
    assert loaded.bundle("custom")["source"] == "{{CONTENT}}"
    assert loaded.names() == {"custom": "自定义主题"}
    assert REV_A[:12] in loaded.describe()


@pytest.mark.asyncio
async def test_failed_validation_keeps_disk_and_memory_unchanged(repo):
    repo._download = AsyncMock(return_value=snapshot())
    await repo.update()
    before = repo.path.read_bytes()
    repo._download.return_value = snapshot(REV_B)
    repo.validator.side_effect = ValueError("invalid theme")
    assert "未替换" in await repo.update()
    assert repo.path.read_bytes() == before
    assert repo.snapshot["revision"] == REV_A


@pytest.mark.asyncio
async def test_network_failure_keeps_existing_themes(repo):
    repo._download = AsyncMock(side_effect=TimeoutError("network timeout"))
    assert "未替换" in await repo.update()
    assert repo.names() == {}
    assert not repo.path.exists()


@pytest.mark.asyncio
async def test_rollback_keeps_the_reverted_commit_blocked(repo):
    repo._download = AsyncMock(side_effect=[snapshot(), snapshot(REV_B)])
    await repo.update()
    await repo.update()
    assert repo.snapshot["revision"] == REV_B
    assert "已回退" in await repo.update(rollback=True)
    assert repo.snapshot["revision"] == REV_A
    assert repo.snapshot["blocked_revision"] == REV_B
    repo._download = AsyncMock(return_value=None)
    assert "保留回退版本" in await repo.update()


@pytest.mark.asyncio
async def test_disabled_and_concurrent_updates_do_not_download(repo):
    repo._download = AsyncMock()
    repo.enabled = False
    assert "启用" in await repo.update()
    repo.enabled = True
    async with repo.lock:
        assert "正在进行" in await repo.update()
    repo._download.assert_not_awaited()


def test_bad_cache_is_reported_and_builtin_fallback_remains_available(repo):
    repo.path.write_text("{")
    repo.load()
    assert repo.names() == {}
    assert "缓存未加载" in repo.last_error


@pytest.mark.asyncio
async def test_new_theme_id_is_listed_and_selectable_without_code_changes(tmp_path):
    store = StyleStore(tmp_path, templates=TEMPLATES | {"new-theme": "新的主题"})
    await store.initialize()
    controller = StyleController(store)
    assert "new-theme" in await controller.execute(Event(), "list")
    assert "已保存" in await controller.execute(Event(), "use 新的主题")
    assert store.render_style(Event().unified_msg_origin)["template"] == "new-theme"
    store.templates = dict(TEMPLATES)
    assert store.render_style(Event().unified_msg_origin)["template"] == "codex"
    assert store.effective(Event().unified_msg_origin)["template"] == "new-theme"
    assert "暂不可用" in await controller.execute(Event(), "status")


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["sync", "rollback", "同步", "global sync"])
async def test_repository_commands_require_admin(tmp_path, command):
    store = StyleStore(tmp_path)
    await store.initialize()
    callback = AsyncMock()
    controller = StyleController(store, repository_action=callback)
    assert "仅 AstrBot 管理员" in await controller.execute(Event(admin=False), command)
    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_git_download_pins_every_file_to_one_commit_and_checks_blob_hash(repo):
    contents = {
        "t2i-themes/custom/theme.typ": b"{{CONTENT}}",
        "t2i-themes/custom/theme.json": b'{"name":"custom"}',
        "t2i-themes/custom/assets/extra.typ": b"#let x = 1",
    }
    entries = [
        {
            "path": name,
            "type": "blob",
            "mode": "100644",
            "size": len(raw),
            "sha": hashlib.sha1(
                b"blob " + str(len(raw)).encode() + b"\0" + raw
            ).hexdigest(),
        }
        for name, raw in contents.items()
    ]
    urls = []

    async def get(session, url, limit):
        urls.append(url)
        if "/commits/" in url:
            return json.dumps(
                {"sha": REV_A, "commit": {"tree": {"sha": "c" * 40}}}
            ).encode()
        if "/git/trees/" in url:
            return json.dumps({"tree": entries}).encode()
        return contents[url.split(REV_A + "/", 1)[1]]

    repo._get = get
    candidate = await repo._download()
    assert candidate["revision"] == REV_A
    assert candidate["bundles"]["custom"]["source"] == "{{CONTENT}}"
    assert (
        base64.b64decode(candidate["bundles"]["custom"]["files"]["assets/extra.typ"])
        == b"#let x = 1"
    )
    assert all(REV_A in url for url in urls if "raw.githubusercontent.com" in url)
    contents["t2i-themes/custom/theme.typ"] = b"tampered"
    with pytest.raises(RepositoryError, match="校验失败"):
        await repo._download()


@pytest.mark.asyncio
async def test_commit_failure_does_not_change_the_active_snapshot(repo, monkeypatch):
    repo._download = AsyncMock(return_value=snapshot())
    monkeypatch.setattr(
        repo, "_save", lambda value: (_ for _ in ()).throw(OSError("disk error"))
    )
    assert "未替换" in await repo.update()
    assert repo.snapshot["revision"] == ""


@pytest.mark.asyncio
async def test_cancelled_validation_never_persists_a_partial_revision(repo):
    repo._download = AsyncMock(return_value=snapshot())
    started = asyncio.Event()

    async def validate(bundle):
        started.set()
        await asyncio.Event().wait()

    repo.validator = validate
    task = asyncio.create_task(repo.update())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not repo.path.exists()
