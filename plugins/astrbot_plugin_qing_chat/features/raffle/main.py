"""QQ commands and durable raffle reminder/sync workers."""

import asyncio
import json
import re
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from astrbot.api.star import StarTools

from ...runtime import FeatureService
from .cloud import AstrBotSheetTools, CloudLedger, MCPUnavailable, group_documents
from .store import Actor, PAGE_SIZE, RaffleError, Store, ZONE, display_time

HELP = (
    "群抽奖（北京时间）\n"
    "/抽奖 发起 中奖人数 YYYY-MM-DD HH:MM 奖品描述\n"
    "例：/抽奖 发起 2 2026-12-01 21:00 Steam 礼物\n"
    "/抽奖 列表 [页] · /抽奖 报名 编号 · /抽奖 名单 编号 [页]\n"
    "/抽奖 开奖 编号 · /抽奖 取消 编号\n"
    "/抽奖 历史 [页] · /抽奖 结果 编号 [页]\n"
    "也可以 @机器人 后输入上述动作，例如 @机器人 报名 12。"
)
ACTIONS = {
    "帮助",
    "发起",
    "报名",
    "列表",
    "名单",
    "开奖",
    "取消",
    "历史",
    "结果",
    "同步状态",
}
STATUSES = {
    "open": "报名中",
    "closed": "待开奖",
    "drawn": "已开奖",
    "cancelled": "已取消",
}


def number(token):
    if not re.fullmatch(r"#?[1-9][0-9]{0,8}", token):
        raise RaffleError("请填写正整数编号、人数或页码。")
    return int(token.lstrip("#"))


def mention_command(event):
    raw = getattr(event.message_obj, "raw_message", None)
    segments = raw.get("message") if isinstance(raw, Mapping) else None
    if not isinstance(segments, list):
        return None
    text = []
    at_bot = False
    for segment in segments:
        if not isinstance(segment, Mapping) or not isinstance(
            segment.get("data"), Mapping
        ):
            return None
        data = segment["data"]
        if segment.get("type") == "at" and str(data.get("qq")) == str(
            event.get_self_id()
        ):
            at_bot = True
        elif segment.get("type") == "text" and isinstance(data.get("text"), str):
            text.append(data["text"])
        else:
            return None
    command = "".join(text).strip()
    tokens = command.split(maxsplit=1)
    if not at_bot or not tokens:
        return None
    if tokens[0] == "抽奖":
        return tokens[1] if len(tokens) > 1 else "帮助"
    return command if tokens[0] in ACTIONS else None


class RaffleService(FeatureService):
    def __init__(self, context, config):
        super().__init__(context, config)
        self.tasks = []
        self.sync_states = {}
        self.binding_error = ""

    async def initialize(self):
        directory = Path(StarTools.get_data_dir("astrbot_plugin_qing_chat")) / "raffle"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.store = Store(directory / "raffles.sqlite3")
        await asyncio.to_thread(self.store.initialize)
        self.store.path.chmod(0o600)
        server = self.config.get("mcp_server", "tencent-docs-sheet").strip()
        try:
            bindings = group_documents(self.config.get("group_documents", []))
            if bindings and not server:
                raise ValueError("请填写腾讯文档 MCP 服务名称")
        except ValueError as exc:
            self.binding_error = str(exc)
        else:
            tools = AstrBotSheetTools(self.context, server)
            for group, file_id in bindings.items():
                cloud = CloudLedger(self.store, group, file_id, tools)
                self.sync_states[group] = "等待首次同步"
                self.tasks.append(asyncio.create_task(self.sync_loop(group, cloud)))
        self.tasks.append(asyncio.create_task(self.reminder_loop()))
        self.logger.info(
            "群抽奖模块已加载，腾讯文档同步：%s",
            self.binding_error or f"已绑定 {len(self.sync_states)} 个群",
        )

    async def terminate(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

    @staticmethod
    def in_group(event):
        return event.get_platform_name() == "aiocqhttp" and bool(event.get_group_id())

    async def handle(self, event, command):
        if not self.in_group(event) or str(event.get_sender_id()) == str(
            event.get_self_id()
        ):
            return
        group = f"{event.get_platform_id()}:GroupMessage:{event.get_group_id()}"
        raw = getattr(event.message_obj, "raw_message", None)
        sender = raw.get("sender") if isinstance(raw, Mapping) else None
        moderator = isinstance(sender, Mapping) and sender.get("role") in {
            "admin",
            "owner",
        }
        qq = str(event.get_sender_id())
        if not qq.isascii() or not qq.isdecimal():
            return
        nickname = " ".join((event.get_sender_name() or "").split())[:80]
        actor = Actor(
            group, qq, nickname if nickname and nickname != qq else "群友", moderator
        )
        event.should_call_llm(False)
        try:
            reply = await self.execute(actor, command)
        except RaffleError as exc:
            reply = str(exc)
        except Exception as exc:
            self.logger.error("群抽奖记录操作异常 (%s)", type(exc).__name__)
            reply = "抽奖记录处理异常，请稍后查询状态。"
        event.stop_event()
        return event.plain_result(reply).use_t2i(False)

    async def execute(self, actor, command):
        parts = (command.strip() or "帮助").split(maxsplit=4)
        action, *args = parts
        now = int(time.time())
        if action == "帮助" or action not in ACTIONS:
            return HELP
        if action == "发起":
            if len(args) != 4:
                return HELP
            slots = number(args[0])
            try:
                deadline = int(
                    datetime.strptime(f"{args[1]} {args[2]}", "%Y-%m-%d %H:%M")
                    .replace(tzinfo=ZONE)
                    .timestamp()
                )
            except (ValueError, OverflowError) as exc:
                raise RaffleError("请用 YYYY-MM-DD HH:MM 填写有效的北京时间。") from exc
            if deadline <= now:
                raise RaffleError("截止时间必须晚于现在。")
            title = " ".join(args[3].split())
            if len(title) > 120:
                raise RaffleError("奖品描述最多 120 字。")
            raffle = await asyncio.to_thread(
                self.store.create, actor, title, slots, deadline, now
            )
            reminder = (
                "截止前一小时和截止时会提醒发起人。"
                if deadline - now > 3600
                else "距截止不超过一小时，截止时会提醒发起人。"
            )
            return f"抽奖 #{raffle} 已发起｜{title}\n抽 {slots} 人，{display_time(deadline)} 截止（北京时间）。\n报名：/抽奖 报名 {raffle}\n{reminder}"
        if action == "同步状态" and not args:
            group = actor.group.rsplit(":", 1)[-1]
            state = self.binding_error or self.sync_states.get(
                group, "本群未绑定文档，记录保存在本地"
            )
            return "腾讯文档同步：" + state
        if action in ("列表", "历史") and len(args) <= 1:
            page = number(args[0]) if args else 1
            rows = await asyncio.to_thread(
                self.store.list_raffles, actor.group, page, action == "历史"
            )
            lines = [f"抽奖{action} · 第 {page} 页"]
            for row in rows[:PAGE_SIZE]:
                status = (
                    "待开奖"
                    if row["status"] == "open" and now >= row["deadline"]
                    else STATUSES[row["status"]]
                )
                lines.append(
                    f"#{row['id']} {row['title']}｜{status}｜{row['people']} 人｜{display_time(row['deadline'])} 截止"
                )
            if not rows:
                lines.append("这一页没有记录。")
            return self.paginate(lines, rows, f"/抽奖 {action}", page)
        if action in ("名单", "结果") and 1 <= len(args) <= 2:
            raffle_id = number(args[0])
            page = number(args[1]) if len(args) == 2 else 1
            raffle, people = await asyncio.to_thread(
                self.store.details, actor.group, raffle_id, page
            )
            if action == "结果":
                if raffle["status"] != "drawn":
                    return f"抽奖 #{raffle_id} {STATUSES[raffle['status']]}。"
                return self.result(raffle, page)
            lines = [f"抽奖 #{raffle_id} 报名名单 · 第 {page} 页"]
            lines.extend(
                f"{row['number']}. {row['name']}" for row in people[:PAGE_SIZE]
            )
            if not people:
                lines.append("这一页没有报名记录。")
            return self.paginate(lines, people, f"/抽奖 名单 {raffle_id}", page)
        if action in ("报名", "开奖", "取消") and len(args) == 1:
            raffle_id = number(args[0])
            if action == "报名":
                slot, created = await asyncio.to_thread(
                    self.store.join, actor, raffle_id, now
                )
                state = "报名成功" if created else "你已报名"
                return f"{state}。抽奖 #{raffle_id}，你的报名序号是 {slot}。"
            fresh, raffle, _ = await asyncio.to_thread(
                self.store.close, actor, raffle_id, now, cancel=action == "取消"
            )
            if action == "取消":
                return f"抽奖 #{raffle_id} 已取消。"
            return ("" if fresh else "这场已经开过奖，以下是原结果。\n") + self.result(
                raffle, 1
            )
        return HELP

    @staticmethod
    def paginate(lines, records, command, page):
        if len(records) > PAGE_SIZE:
            lines.append(f"下一页：{command} {page + 1}")
        return "\n".join(lines)

    def result(self, raffle, page):
        winners = json.loads(raffle["winners"])
        start = PAGE_SIZE * (page - 1)
        rows = winners[start : start + PAGE_SIZE + 1]
        lines = [
            f"抽奖 #{raffle['id']}｜{raffle['title']}",
            f"共 {raffle['eligible']} 人报名，从序号 1–{raffle['eligible']} 随机抽出 {len(winners)} 个不同序号。",
        ]
        if len(winners) < raffle["slots"]:
            lines.append(f"原定抽 {raffle['slots']} 人，实际按报名人数开奖。")
        lines.extend(
            f"随机抽中 {row['number']} 号，恭喜 {row['name']} 中奖！"
            for row in rows[:PAGE_SIZE]
        )
        if not rows:
            lines.append("这一页没有中奖记录。")
        return self.paginate(lines, rows, f"/抽奖 结果 {raffle['id']}", page)

    async def reminder_loop(self):
        while True:
            try:
                for raffle in await asyncio.to_thread(self.store.due, int(time.time())):
                    await self.remind(raffle)
            except Exception as exc:
                self.logger.warning("群抽奖提醒轮询失败 (%s)", type(exc).__name__)
            await asyncio.sleep(5)

    async def remind(self, raffle):
        platform_id, _, group = raffle["scope"].split(":", 2)
        if not await self.is_session_enabled(raffle["scope"]):
            return
        platform = next(
            (
                item
                for item in self.context.platform_manager.platform_insts
                if item.meta().id == platform_id and item.meta().name == "aiocqhttp"
            ),
            None,
        )
        if platform is None:
            return
        try:
            async with asyncio.timeout(4):
                status = await platform.bot.call_action("get_status")
            if not isinstance(status, dict) or status.get("online") is False:
                return
        except Exception:
            return  # No send was attempted; a later poll can try again.
        now = int(time.time())
        if not await asyncio.to_thread(self.store.claim, raffle, now):
            return
        raffle_id = raffle["id"]
        if raffle["kind"] == "提前一小时":
            text = f" 抽奖 #{raffle_id}「{raffle['title']}」还有不到一小时截止（{display_time(raffle['deadline'])}）。"
        elif raffle["status"] == "drawn":
            text = f" 抽奖 #{raffle_id}「{raffle['title']}」已截止并开奖，用 /抽奖 结果 {raffle_id} 查看。"
        else:
            text = f" 抽奖 #{raffle_id}「{raffle['title']}」报名已截止。准备好后发 /抽奖 开奖 {raffle_id}。"
        confirmed = False
        try:
            async with asyncio.timeout(12):
                response = await platform.bot.call_action(
                    "send_group_msg",
                    group_id=int(group),
                    message=[
                        {"type": "at", "data": {"qq": raffle["owner"]}},
                        {"type": "text", "data": {"text": text}},
                    ],
                )
            confirmed = (
                isinstance(response, dict) and response.get("message_id") is not None
            )
        except Exception:
            self.logger.warning("抽奖 #%s 提醒发送状态未确认", raffle_id)
        finally:
            await asyncio.to_thread(
                self.store.finish_reminder, raffle, int(time.time()), confirmed
            )

    async def sync_loop(self, group, cloud):
        delay = 10
        while True:
            try:
                synced = await cloud.sync()
                state = "已绑定「bot 抽奖记录」，后台自动同步"
                if self.sync_states[group] != state:
                    self.logger.info("本群的腾讯文档抽奖标签页已就绪")
                self.sync_states[group] = state
                delay = 10
                await asyncio.sleep(2 if synced else 20)
            except MCPUnavailable as exc:
                state = f"{exc}，记录保存在本地，等待重试"
                if self.sync_states[group] != state:
                    self.logger.info("群抽奖文档同步暂停：%s", exc)
                self.sync_states[group] = state
                await asyncio.sleep(20)
            except Exception as exc:
                if self.sync_states[group] != "同步失败，记录保存在本地，等待重试":
                    self.logger.warning(
                        "群抽奖腾讯文档同步失败 (%s)", type(exc).__name__
                    )
                self.sync_states[group] = "同步失败，记录保存在本地，等待重试"
                await asyncio.sleep(delay)
                delay = min(delay * 2, 300)
