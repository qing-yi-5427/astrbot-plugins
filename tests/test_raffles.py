from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_qing_chat.features.raffle.cloud import (
    AstrBotSheetTools,
    CloudLedger,
    MCPUnavailable,
    SHEET_NAME,
    group_documents,
)
from astrbot_plugin_qing_chat.features.raffle.main import RaffleService, mention_command
from astrbot_plugin_qing_chat.features.raffle.store import Actor, RaffleError, Store

NOW = 1800000000
OWNER = Actor("default:GroupMessage:123", "1001", "发起人")
GUEST = Actor(OWNER.group, "2001", "报名人")
ADMIN = Actor(OWNER.group, "3001", "管理员", True)


@pytest.fixture
def storage(tmp_path):
    store = Store(tmp_path / "raffles.db")
    store.initialize()
    return store


def test_group_isolation_concurrent_join_and_separate_raffles(storage):
    first = storage.create(OWNER, "礼物一", 1, NOW + 7200, NOW)
    second = storage.create(OWNER, "礼物二", 1, NOW + 7200, NOW)
    with ThreadPoolExecutor(8) as pool:
        joins = list(pool.map(lambda _: storage.join(GUEST, first, NOW), range(12)))
    assert joins.count((1, True)) == 1
    assert joins.count((1, False)) == 11
    assert storage.join(GUEST, second, NOW) == (1, True)
    with pytest.raises(RaffleError, match="找不到"):
        storage.join(
            Actor("default:GroupMessage:456", GUEST.qq, GUEST.name), first, NOW
        )
    with pytest.raises(RaffleError, match="停止报名"):
        storage.join(GUEST, first, NOW + 7200)
    assert [row["kind"] for row in storage.unsynced(0)] == [
        "发起",
        "发起",
        "报名",
        "报名",
    ]


def test_draw_permissions_result_and_no_reroll(storage):
    first = storage.create(OWNER, "礼物", 3, NOW + 60, NOW)
    for seq in range(1, 11):
        storage.join(Actor(OWNER.group, str(8000 + seq), f"群友{seq}"), first, NOW)
    with pytest.raises(RaffleError, match="只有"):
        storage.close(GUEST, first, NOW + 60)
    with pytest.raises(RaffleError, match="未截止"):
        storage.close(OWNER, first, NOW + 59)
    new, raffle, winners = storage.close(ADMIN, first, NOW + 60)
    assert new and len({row["number"] for row in winners}) == 3
    again, _, snapshot = storage.close(OWNER, first, NOW + 120)
    assert not again and snapshot == winners
    reply = RaffleService(None, {}).result(raffle, 1)
    assert "共 10 人报名" in reply
    for winner in winners:
        assert f"随机抽中 {winner['number']} 号，恭喜 {winner['name']} 中奖" in reply
    recorded = [row for row in storage.unsynced(0) if row["kind"] == "中奖"]
    assert len(recorded) == 3


def test_persistent_reminders_cancel_and_create_near_deadline(storage):
    first = storage.create(OWNER, "礼物", 1, NOW + 7200, NOW)
    short = storage.create(OWNER, "短活动", 1, NOW + 1800, NOW)
    cancelled = storage.create(OWNER, "取消活动", 1, NOW + 7200, NOW)
    storage.close(ADMIN, cancelled, NOW + 20, cancel=True)
    assert storage.due(NOW + 1799) == []
    assert [(r["id"], r["kind"]) for r in storage.due(NOW + 1800)] == [(short, "截止")]
    early = next(r for r in storage.due(NOW + 3600) if r["id"] == first)
    assert early["kind"] == "提前一小时"
    assert storage.claim(early, NOW + 3600)
    reopened = Store(storage.path)
    reopened.initialize()
    assert not reopened.claim(early, NOW + 3601)
    assert [(r["id"], r["kind"]) for r in reopened.due(NOW + 7200)] == [
        (short, "截止"),
        (first, "截止"),
    ]


def test_real_mention_only_accepts_group_action():
    at = {"type": "at", "data": {"qq": "123"}}
    text = lambda value: {"type": "text", "data": {"text": value}}

    def event(parts):
        return SimpleNamespace(
            message_obj=SimpleNamespace(raw_message={"message": parts}),
            get_self_id=lambda: "123",
        )

    assert mention_command(event([at, text("抽奖 报名 12")])) == "报名 12"
    assert mention_command(event([at, text("报名 12")])) == "报名 12"
    assert mention_command(event([at, text("你好")])) is None
    assert mention_command(event([at, text("/抽奖 报名 12")])) is None
    assert mention_command(event([text("@机器人 报名 12")])) is None


@pytest.mark.asyncio
async def test_native_mcp_client_is_reused_and_sheet_creation_is_not_retried():
    name = "tencent-docs-sheet"
    reply = SimpleNamespace(
        isError=False, structuredContent={"data": {"sheet_id": "new"}}
    )
    open_session = SimpleNamespace(call_tool=AsyncMock(return_value=reply))
    client = SimpleNamespace(
        session=open_session,
        tools=[
            SimpleNamespace(name="add_sheet"),
            SimpleNamespace(name="sheet.get_sheet_info"),
        ],
        call_tool_with_reconnect=AsyncMock(return_value=reply),
    )
    manager = SimpleNamespace(mcp_client_dict={name: client})
    call = AstrBotSheetTools(
        SimpleNamespace(get_llm_tool_manager=lambda: manager), name
    )
    assert await call("add_sheet", {"name": "抽奖流水"}) == {"sheet_id": "new"}
    assert open_session.call_tool.call_count == 1
    client.call_tool_with_reconnect.assert_not_awaited()
    await call("get_sheet_info", {"file_id": "fake"})
    assert client.call_tool_with_reconnect.call_args.args[0] == "sheet.get_sheet_info"
    manager.mcp_client_dict.clear()
    with pytest.raises(MCPUnavailable):
        await call("get_sheet_info", {})


@pytest.mark.asyncio
async def test_cloud_replicates_after_uncertain_write_using_same_rows(storage):
    raffle = storage.create(OWNER, "=literal text", 1, NOW + 7200, NOW)
    outsider = Actor("default:GroupMessage:456", "5001", "其他群")
    storage.create(outsider, "其他群的数据", 1, NOW + 7200, NOW)
    storage.join(GUEST, raffle, NOW)
    sheets = [{"sheet_id": "original", "sheet_name": "旧标签页", "row_count": 100}]
    cells = {}
    lost = False

    async def call(name, arguments):
        nonlocal lost
        assert arguments["file_id"] == "fake"
        if name == "get_sheet_info":
            return {"sheets": sheets}
        if name == "add_sheet":
            sheets.append(
                {
                    "sheet_id": "separate",
                    "sheet_name": arguments["name"],
                    "row_count": 100,
                }
            )
            return {"sheet_id": "separate"}
        assert arguments["sheet_id"] == "separate"
        if name == "get_cell_data":
            return {
                "cells": [
                    v
                    for (row, col), v in cells.items()
                    if arguments["start_row"] <= row <= arguments["end_row"]
                    and col <= arguments["end_col"]
                ]
            }
        if name == "set_range_value":
            for cell in arguments["values"]:
                assert cell["value_type"] == "STRING"
                cells[cell["row"], cell["col"]] = cell
            if arguments["values"][0]["row"] > 0 and not lost:
                lost = True
                raise TimeoutError("write succeeded but response was lost")
        return {}

    sync = CloudLedger(storage, "123", "fake", call)
    with pytest.raises(TimeoutError):
        await sync.sync()
    assert await sync.sync() == 2
    assert await sync.sync() == 0
    assert len(sheets) == 2 and sheets[1]["sheet_name"] == SHEET_NAME
    assert cells[1, 0]["string_value"] == "R1"
    assert cells[2, 0]["string_value"] == "R3"
    assert cells[2, 6]["string_value"] != GUEST.qq
    assert "=literal text" in cells[1, 7]["string_value"]
    assert max(row for row, _ in cells) == 2
    storage.create(outsider, "其他群的数据2", 1, NOW + 7200, NOW)
    storage.create(OWNER, "本群下一条", 1, NOW + 7200, NOW)
    assert await sync.sync() == 1
    assert cells[3, 0]["string_value"] == "R5"


def test_each_group_and_each_document_only_bound_once():
    base = "https://docs.qq.com/sheet/"
    assert group_documents([f"12345={base}Doc1?tab=1", f"23456={base}Doc2"]) == {
        "12345": "Doc1",
        "23456": "Doc2",
    }
    with pytest.raises(ValueError, match="只能绑定一次"):
        group_documents([f"12345={base}Doc1?tab=1", f"23456={base}Doc1?tab=2"])
    with pytest.raises(ValueError, match="只能绑定一次"):
        group_documents([f"12345={base}Doc1", f"12345={base}Doc2"])


@pytest.mark.asyncio
async def test_named_tab_with_existing_data_is_not_overwritten(storage):
    call = AsyncMock(
        side_effect=[
            {
                "sheets": [
                    {
                        "sheet_id": "occupied",
                        "sheet_name": SHEET_NAME,
                        "used_range": {"row_count": 15},
                    }
                ]
            },
            {"cells": []},
        ]
    )
    with pytest.raises(MCPUnavailable, match="已有数据"):
        await CloudLedger(storage, "123", "fake", call).sync()
    assert [t.args[0] for t in call.await_args_list] == [
        "get_sheet_info",
        "get_cell_data",
    ]


@pytest.mark.asyncio
async def test_reminder_uses_real_at_segment_once_and_recovers_offline(
    storage, monkeypatch
):
    import time

    monkeypatch.setattr(time, "time", lambda: NOW + 7200)
    storage.create(OWNER, "礼物", 1, NOW + 7200, NOW)
    bot = SimpleNamespace(
        call_action=AsyncMock(
            side_effect=[
                {"online": False},
                {"online": True},
                {"message_id": 42},
                {"online": True},
            ]
        )
    )
    platform = SimpleNamespace(
        meta=lambda: SimpleNamespace(id="default", name="aiocqhttp"), bot=bot
    )
    service = RaffleService(
        SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[platform])), {}
    )
    service.store = storage
    due = storage.due(NOW + 7200)[0]
    await service.remind(due)
    assert storage.due(NOW + 7200)
    await service.remind(due)
    await service.remind(due)
    assert storage.due(NOW + 7200) == []
    calls = [
        c for c in bot.call_action.await_args_list if c.args == ("send_group_msg",)
    ]
    assert len(calls) == 1
    assert calls[0].kwargs["group_id"] == 123
    assert calls[0].kwargs["message"][0] == {"type": "at", "data": {"qq": OWNER.qq}}


@pytest.mark.asyncio
async def test_form_to_qq_confirmation_is_scoped_and_one_time(storage, monkeypatch):
    import re
    from datetime import datetime

    from aiohttp.test_utils import TestClient, TestServer

    from astrbot_plugin_qing_chat.features.raffle import form
    from astrbot_plugin_qing_chat.features.raffle.store import ZONE

    monkeypatch.setattr(form.time, "time", lambda: NOW)
    service = RaffleService(None, {})
    service.store = storage
    service.form_url = "https://raffle.example.test"
    result = await service.execute(OWNER, "发起")
    path = next(
        line.removeprefix(service.form_url)
        for line in result.splitlines()
        if line.startswith(service.form_url)
    )
    deadline = datetime.fromtimestamp(NOW + 7200, ZONE).strftime("%Y-%m-%dT%H:%M")
    data = {"title": "<示例奖品>", "slots": "2", "deadline": deadline}
    async with TestClient(
        TestServer(form.create_app(storage, service.form_url))
    ) as client:
        assert (await client.get("/")).status == 200
        assert (await client.get("/api/plugin/get")).status == 404
        preview = await client.get(path)
        assert "中奖人数" in await preview.text()
        assert preview.headers["Cache-Control"] == "no-store"
        assert (
            await client.post(path, data=data, headers={"Origin": "https://other.test"})
        ).status == 403
        assert (await client.post(path, data={**data, "slots": "-1"})).status == 400
        saved = await client.post(path, data=data, headers={"Origin": service.form_url})
        html = await saved.text()
        assert saved.status == 200 and "&lt;示例奖品&gt;" in html
        code = re.search(r"/抽奖 确认 ([A-F0-9]{8})", html)[1]
        another = await client.post(path, data={**data, "title": "另一份奖品"})
        assert "另一份奖品" not in await another.text()
        assert storage.unsynced(0) == []
        for who in (GUEST, Actor("default:GroupMessage:456", OWNER.qq, OWNER.name)):
            with pytest.raises(RaffleError, match="找不到"):
                await service.execute(who, f"确认 {code}")
        assert "已发起｜<示例奖品>" in await service.execute(OWNER, f"确认 {code}")
        assert [row["kind"] for row in storage.unsynced(0)] == ["发起"]
        assert (await client.get(path)).status == 404
        with pytest.raises(RaffleError, match="找不到"):
            await service.execute(OWNER, f"确认 {code}")


def test_form_expires_and_new_link_replaces_old_draft(storage):
    from astrbot_plugin_qing_chat.features.raffle.store import DRAFT_TTL

    first = storage.new_form(OWNER, NOW)
    assert storage.form(first, NOW + DRAFT_TTL - 1) is not None
    assert storage.form(first, NOW + DRAFT_TTL) is None
    new = storage.new_form(OWNER, NOW + 5)
    assert storage.form(first, NOW + 5) is None
    assert storage.form(new, NOW + 5) is not None
