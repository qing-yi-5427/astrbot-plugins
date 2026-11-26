from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_qing_chat.features.raffle.cloud import (
    HEADERS,
    LEGACY_HEADERS,
    SHEET_NAME,
    AstrBotSheetTools,
    CloudLedger,
    MCPUnavailable,
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


class MockRollSheet:
    def __init__(self):
        self.sheets = [
            {"sheet_id": "original", "sheet_name": "旧标签页", "row_count": 100}
        ]
        self.cells = {}
        self.lose_row = False
        self.lose_clear = False

    def add(self):
        self.sheets.append(
            {
                "sheet_id": "separate",
                "sheet_name": SHEET_NAME,
                "row_count": 100,
                "col_count": 26,
            }
        )

    async def __call__(self, name, arguments):
        assert arguments["file_id"] == "fake"
        if name == "get_sheet_info":
            return {"sheets": self.sheets}
        if name == "add_sheet":
            self.add()
            return {"sheet_id": "separate"}
        assert arguments["sheet_id"] == "separate"
        if name == "get_cell_data":
            return {
                "cells": [
                    cell
                    for (row, col), cell in self.cells.items()
                    if arguments["start_row"] <= row <= arguments["end_row"]
                    and arguments["start_col"] <= col <= arguments["end_col"]
                ]
            }
        if name == "set_range_value":
            for cell in arguments["values"]:
                self.cells[cell["row"], cell["col"]] = cell
            if arguments["values"][0]["row"] > 0 and self.lose_row:
                self.lose_row = False
                raise TimeoutError("write succeeded but response was lost")
        if name == "clear_range_cells":
            for row, col in tuple(self.cells):
                if (
                    arguments["start_row"] <= row <= arguments["end_row"]
                    and arguments["start_col"] <= col <= arguments["end_col"]
                ):
                    del self.cells[row, col]
            if self.lose_clear:
                self.lose_clear = False
                raise TimeoutError("clear succeeded but response was lost")
        return {}


@pytest.mark.asyncio
async def test_cloud_replicates_after_uncertain_write_using_same_rows(storage):
    raffle = storage.create(OWNER, "=literal text", 1, NOW + 7200, NOW)
    outsider = Actor("default:GroupMessage:456", "5001", "其他群")
    storage.create(outsider, "其他群的数据", 1, NOW + 7200, NOW)
    storage.join(GUEST, raffle, NOW)
    backend = MockRollSheet()
    backend.lose_row = True
    sync = CloudLedger(storage, "123", "fake", backend)
    with pytest.raises(TimeoutError):
        await sync.sync()
    assert await sync.sync() == 1
    assert await sync.sync() == 0
    cells = backend.cells
    assert len(backend.sheets) == 2
    assert [cells[0, col]["string_value"] for col in range(len(HEADERS))] == list(
        HEADERS
    )
    assert cells[1, 0]["string_value"] == OWNER.name
    assert cells[1, 1]["string_value"] == "=literal text"
    assert cells[1, 3]["value_type"] == "NUMBER"
    assert cells[1, 4]["string_value"] == GUEST.name
    assert (1, 2) not in cells
    storage.close(OWNER, raffle, NOW + 7200)
    assert await sync.sync() == 1
    assert cells[1, 2]["string_value"] == GUEST.name
    assert max(row for row, _ in cells) == 1
    storage.create(outsider, "其他群的数据2", 1, NOW + 7200, NOW)
    storage.create(OWNER, "本群下一场", 1, NOW + 7200, NOW)
    assert await sync.sync() == 1
    assert cells[2, 1]["string_value"] == "本群下一场"


@pytest.mark.asyncio
async def test_roll_migration_keeps_tab_and_recovers_after_clear_timeout(storage):
    raffle = storage.create(OWNER, "历史奖品", 1, NOW + 7200, NOW)
    storage.join(GUEST, raffle, NOW)
    backend = MockRollSheet()
    backend.add()
    sync = CloudLedger(storage, "123", "fake", backend)
    await sync.save("sheet_id", "separate")
    await sync.save("position", "[2, 2]")
    legacy_rows = [(*LEGACY_HEADERS, sync.marker)]
    legacy_rows.extend(sync.legacy_columns(row) for row in storage.unsynced(0, "123"))
    for row, values in enumerate(legacy_rows):
        for col, value in enumerate(values):
            backend.cells[row, col] = sync.cell(row, col, value)
    backend.lose_clear = True
    with pytest.raises(TimeoutError):
        await sync.sync()
    assert await sync.sync() == 1
    assert len(backend.sheets) == 2
    assert await sync.setting("sheet_id") == "separate"
    assert backend.cells[1, 4]["string_value"] == GUEST.name
    assert all(
        not sync.populated(cell)
        for (r, c), cell in backend.cells.items()
        if r == 0 and c >= len(HEADERS)
    )
    assert max(row for row, _ in backend.cells) == 1


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
        assert preview.headers["Referrer-Policy"] == "same-origin"
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


def group_event(qq, text, *, group="123", quoted=False):
    from unittest.mock import Mock

    segments = [{"type": "text", "data": {"text": text}}]
    if quoted:
        segments.insert(0, {"type": "reply", "data": {"id": "555"}})
    return SimpleNamespace(
        message_obj=SimpleNamespace(raw_message={"message": segments}),
        get_platform_id=lambda: "default",
        get_platform_name=lambda: "aiocqhttp",
        get_group_id=lambda: group,
        get_self_id=lambda: "9999",
        get_sender_id=lambda: qq,
        get_sender_name=lambda: "群友",
        should_call_llm=Mock(),
        stop_event=Mock(),
        send=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_short_join_and_bare_number_stay_in_own_group(storage, monkeypatch):
    from astrbot_plugin_qing_chat.features.raffle import main

    monkeypatch.setattr(main, "_join_prompts", {})
    monkeypatch.setattr(main.time, "time", lambda: NOW)
    service = RaffleService(None, {})
    service.store = storage
    assert "没有可报名" in await service.execute(GUEST, "报名")
    expired = storage.create(OWNER, "已过截止", 1, NOW, NOW - 60)
    first = storage.create(OWNER, "礼物甲", 1, NOW + 60, NOW)
    assert f"#{first}" in await service.execute(GUEST, "报名")
    assert "你已报名" in await service.execute(GUEST, "报名")
    second = storage.create(OWNER, "礼物乙", 1, NOW + 120, NOW)
    prompt = await service.execute(GUEST, "报名")
    assert f"{first} · 礼物甲" in prompt and f"{second} · 礼物乙" in prompt
    assert "已过截止" not in prompt and "不要回复或 @ 机器人" in prompt
    listener = main.RaffleFollowup()
    invalid_events = (
        group_event(GUEST.qq, str(expired)),
        group_event(GUEST.qq, str(second), quoted=True),
        group_event(OWNER.qq, str(second)),
        group_event(GUEST.qq, str(second), group="456"),
    )
    for event in invalid_events:
        assert not listener.filter(event, {})
        await service.handle_followup(event)
        event.send.assert_not_awaited()
        event.stop_event.assert_not_called()

    event = group_event(GUEST.qq, str(second))
    assert listener.filter(event, {})
    await service.handle_followup(event)
    assert f"#{second}" in event.send.await_args.args[0].get_plain_text()
    event.stop_event.assert_called_once()
    assert not listener.filter(event, {})

    await service.execute(GUEST, "报名")
    with monkeypatch.context() as patch:
        patch.setattr(main, "time", SimpleNamespace(monotonic=lambda: 1e20))
        assert not listener.filter(event, {})


@pytest.mark.asyncio
async def test_short_draw_mentions_drawer_and_all_winners_once(storage, monkeypatch):
    import time

    monkeypatch.setattr(time, "time", lambda: NOW)
    service = RaffleService(None, {})
    service.store = storage
    first = storage.create(OWNER, "礼物", 25, NOW, NOW - 120)
    storage.create(OWNER, "明天的礼物", 1, NOW + 7200, NOW)
    for user in range(1101, 1126):
        storage.join(Actor(OWNER.group, str(user), f"群友 {user}"), first, NOW - 60)
    event = group_event(OWNER.qq, "/抽奖 开奖")

    async def send_before_stop(_message):
        event.stop_event.assert_not_called()

    event.send.side_effect = send_before_stop
    await service.handle(event, "开奖")
    assert event.send.await_count == 2
    messages = [call.args[0] for call in event.send.await_args_list]
    assert "共 25 人报名" in messages[0].get_plain_text()
    assert "随机抽中" in messages[1].get_plain_text()
    mentions = {
        str(part.qq)
        for message in messages
        for part in message.chain
        if hasattr(part, "qq")
    }
    assert mentions == {OWNER.qq, *(str(user) for user in range(1101, 1126))}
    event.stop_event.assert_called_once()
    event.should_call_llm.assert_called_once_with(False)
    again = group_event(OWNER.qq, f"/抽奖 开奖 {first}")
    await service.handle(again, f"开奖 {first}")
    assert "已经开过奖" in again.send.await_args.args[0].get_plain_text()
    assert not any(hasattr(p, "qq") for p in again.send.await_args.args[0].chain)
    assert [row["kind"] for row in storage.unsynced(0)].count("开奖") == 1


@pytest.mark.asyncio
async def test_help_and_new_raffle_binding(storage):
    service = RaffleService(None, {"require_document": True})
    service.store = storage
    service.form_url = "https://raffle.example.test"
    event = group_event(OWNER.qq, "/抽奖 帮助")
    await service.handle(event, "帮助")
    text = event.send.await_args.args[0].get_plain_text()
    assert all(
        item in text
        for item in (
            "发起",
            "确认",
            "报名",
            "开奖",
            "名单",
            "管理",
            "历史",
            "取消",
            "不要引用回复",
            "腾讯文档",
            "领取 5 分钟有效",
        )
    )
    with pytest.raises(RaffleError, match="未绑定"):
        await service.execute(OWNER, "发起")
    assert not storage.unsynced(0)
    service.document_bindings["123"] = "file-test"
    assert f"{service.form_url}/f/" in await service.execute(OWNER, "发起")


@pytest.mark.asyncio
async def test_private_send_failure_shares_only_confirmation_link(storage, monkeypatch):
    import time

    from astrbot_plugin_qing_chat.features.raffle.web_auth import WebAuth

    monkeypatch.setattr(time, "time", lambda: NOW)
    service = RaffleService(None, {})
    service.store = storage
    service.form_url = "https://raffle.example.test"
    event = group_event(OWNER.qq, "/抽奖")
    event.bot = SimpleNamespace(call_action=AsyncMock(side_effect=TimeoutError()))
    await service.handle(event, "")
    direct = event.bot.call_action.await_args.kwargs["message"][0]["data"]["text"]
    shared = event.send.await_args.args[0].get_plain_text()
    private_token = direct.rsplit("/", 1)[-1]
    shared_token = shared.rsplit("/", 1)[-1]
    assert private_token != shared_token and private_token not in shared
    auth = WebAuth(storage)
    assert auth.grant(private_token, NOW)["private"] == 1
    assert auth.grant(shared_token, NOW)["private"] == 0
    assert auth.accept_private(shared_token, NOW) is None


@pytest.mark.asyncio
async def test_early_draw_explicit_confirmation_and_publish_message(
    storage, monkeypatch
):
    from astrbot_plugin_qing_chat.features.raffle import main

    moment = SimpleNamespace(time=lambda: NOW, monotonic=lambda: 15.0)
    monkeypatch.setattr(main, "time", moment)
    service = RaffleService(None, {})
    service.store = storage
    token = storage.new_form(OWNER, NOW)
    storage.save_form(token, "提前开奖测试", 1, NOW + 7200, NOW)
    code = storage.current_draft(OWNER, NOW)["code"]
    publication = await service.execute(OWNER, f"确认 {code}")
    assert "已发起｜提前开奖测试" in publication
    assert f"发起人：{OWNER.name}" in publication and "截止：" in publication
    with pytest.raises(RaffleError, match="找不到"):
        await service.execute(OWNER, f"确认 {code}")
    storage.join(GUEST, 1, NOW)
    with pytest.raises(RaffleError, match="只有"):
        await service.execute(GUEST, "开奖 1")
    assert "提前开奖吗" in await service.execute(OWNER, "开奖")
    assert "没有待确认" in await service.execute(GUEST, "确认")
    assert storage.details(OWNER.group, 1, 1)[0]["status"] == "open"

    moment.monotonic = lambda: 200.0
    assert "没有待确认" in await service.execute(OWNER, "确认")
    assert "提前开奖吗" in await service.execute(OWNER, "开奖")
    assert "随机抽中 1 号" in await service.execute(OWNER, "确认")
    assert "没有待确认" in await service.execute(OWNER, "确认")
    assert "已经开过奖" in await service.execute(OWNER, "开奖 1")
    assert storage.due(NOW + 7200) == []
    assert len([row for row in storage.unsynced(0) if row["kind"] == "开奖"]) == 1
