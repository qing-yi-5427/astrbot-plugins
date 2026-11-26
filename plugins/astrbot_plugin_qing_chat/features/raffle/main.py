"""QQ commands and durable raffle reminder/sync workers."""

import asyncio
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from aiohttp import web
from astrbot.api.event import MessageChain, filter
from astrbot.api.star import StarTools

from ...runtime import FeatureService
from .cloud import AstrBotSheetTools, CloudLedger, MCPUnavailable, group_documents
from .dashboard import QQUnavailable
from .form import FORM_PORT, create_app, form_origin
from .store import (
    PAGE_SIZE,
    STATUSES,
    Actor,
    EarlyDrawRequired,
    RaffleError,
    Store,
    display_time,
    validate_details,
)
from .web_auth import INVITE_TTL, WebAuth

HELP_CARD = Path(__file__).with_name("assets") / "help.png"
HELP = (
    "【QQ群抽奖使用说明】\n\n"
    "发 /抽奖，查看本群正在报名的场次。\n\n"
    "① 发起｜发 /抽奖 发起，打开链接填奖品、中奖人数和截止时间（北京时间）。"
    "表单 15 分钟内有效。填好后把页面上的 /抽奖 确认 指令发回原群，"
    "或登录网页管理台发布。发布后群友才能报名。\n\n"
    "② 报名｜发 /抽奖 报名。只有一场可报名时直接加入；"
    "有多场时机器人会列出编号和奖品，2 分钟内在原群只发对应的数字，例如 12。"
    "直接发数字，不要引用回复或 @ 机器人。也可以发 /抽奖 报名 12。"
    "每人每场只报一次，报名后会拿到固定序号。\n\n"
    "③ 开奖｜发起人发 /抽奖 开奖。未到截止时间时，机器人会询问是否提前开奖，"
    "在原群于 2 分钟内发 /抽奖 确认 即可按当前名单开奖。若有多场需要选择，"
    "按提示发 /抽奖 开奖 编号。机器人从报名序号里随机抽取，"
    "公布抽中的号码，并 @ 开奖人和获奖人。群主和群管理员也可指定编号开奖。"
    "重复开奖只显示原结果。\n\n"
    "④ 查询与管理\n"
    "/抽奖 进行中 [页码] · 查看可报名的场次\n"
    "/抽奖 列表 [页码] · 查看报名中和待开奖的场次\n"
    "/抽奖 名单 编号 [页码] · 查报名序号和昵称\n"
    "/抽奖 历史 [页码] · 查已结束的场次\n"
    "/抽奖 结果 编号 [页码] · 查原开奖结果\n"
    "/抽奖 取消 编号 · 发起人或群管理取消\n"
    "/抽奖管理 · 领取 5 分钟有效的网页入口，优先私发。"
    "收到群内入口时，按页面提示在原群确认\n"
    "/抽奖 同步状态 · 查看腾讯文档同步进度\n\n"
    "报名截止时会 @ 发起人；离截止超过 1 小时发起的场次还有一次提前提醒。"
    "完整流水可在网页查询。抽奖、参与者和获奖人会更新到本群绑定的腾讯文档「bot 抽奖记录」。"
)
ACTIONS = {
    "帮助",
    "发起",
    "管理",
    "登录",
    "访问",
    "确认",
    "报名",
    "进行中",
    "列表",
    "名单",
    "开奖",
    "取消",
    "历史",
    "结果",
    "同步状态",
}
CHOICE_SECONDS = 120
_join_prompts = {}


@dataclass(frozen=True)
class DrawReply:
    raffle: Mapping
    drawer: str


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
        return tokens[1] if len(tokens) > 1 else ""
    if command == "抽奖管理":
        return "管理"
    return command if tokens[0] in ACTIONS else None


def group_scope(event):
    return f"{event.get_platform_id()}:GroupMessage:{event.get_group_id()}"


def selected_raffle(event):
    key = (group_scope(event), str(event.get_sender_id()))
    prompt = _join_prompts.get(key)
    if prompt is None or prompt[0] <= time.monotonic():
        return None
    raw = getattr(event.message_obj, "raw_message", None)
    segments = raw.get("message") if isinstance(raw, Mapping) else None
    if not isinstance(segments, list):
        return None
    text = []
    for segment in segments:
        if not isinstance(segment, Mapping) or segment.get("type") != "text":
            return None
        data = segment.get("data")
        value = data.get("text") if isinstance(data, Mapping) else None
        if not isinstance(value, str):
            return None
        text.append(value)
    digits = "".join(text).strip()
    if re.fullmatch(r"[1-9][0-9]{0,8}", digits) and int(digits) in prompt[1]:
        return int(digits)
    return None


class RaffleFollowup(filter.CustomFilter):
    def filter(self, event, cfg):
        return RaffleService.in_group(event) and (
            mention_command(event) is not None or selected_raffle(event) is not None
        )


class RaffleService(FeatureService):
    def __init__(self, context, config):
        super().__init__(context, config)
        self.tasks = []
        self.sync_states = {}
        self.early_draws = {}
        self.document_bindings = {}
        self.binding_error = ""
        self.form_url = ""
        self.form_runner = None

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
            self.document_bindings = bindings
            tools = AstrBotSheetTools(self.context, server)
            for group, file_id in bindings.items():
                cloud = CloudLedger(self.store, group, file_id, tools)
                self.sync_states[group] = "等待首次同步"
                self.tasks.append(asyncio.create_task(self.sync_loop(group, cloud)))
        await self.start_form()
        self.tasks.append(asyncio.create_task(self.reminder_loop()))
        self.logger.info(
            "群抽奖模块已加载，腾讯文档同步：%s",
            self.binding_error or f"已绑定 {len(self.sync_states)} 个群",
        )

    async def start_form(self):
        url = self.config.get("form_url", "").strip()
        if not url:
            return
        runner = None
        try:
            origin = form_origin(url)
            app = create_app(
                self.store,
                origin,
                resolve_actor=self.web_actor,
                sync_status=self.web_sync_status,
                require_document=self.require_document,
            )
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", FORM_PORT).start()
        except Exception as exc:
            if runner is not None:
                await runner.cleanup()
            self.logger.warning(
                "群抽奖表单启动失败 (%s)，可继续使用文字指令", type(exc).__name__
            )
            return
        self.form_runner = runner
        self.form_url = origin
        self.logger.info("群抽奖管理台已启动，监听端口 %s", FORM_PORT)

    def qq_platform(self, platform_id):
        return next(
            (
                item
                for item in self.context.platform_manager.platform_insts
                if item.meta().id == platform_id and item.meta().name == "aiocqhttp"
            ),
            None,
        )

    async def web_actor(self, session):
        if not await self.is_session_enabled(session.group):
            return None
        platform_id, _, group = session.group.split(":", 2)
        platform = self.qq_platform(platform_id)
        if platform is None:
            raise QQUnavailable
        try:
            async with asyncio.timeout(4):
                member = await platform.bot.call_action(
                    "get_group_member_info",
                    group_id=int(group),
                    user_id=int(session.qq),
                    no_cache=True,
                )
        except Exception as exc:
            raise QQUnavailable from exc
        if (
            not isinstance(member, Mapping)
            or str(member.get("group_id")) != group
            or str(member.get("user_id")) != session.qq
            or member.get("role") not in {"owner", "admin", "member"}
        ):
            return None
        name = " ".join((member.get("card") or member.get("nickname") or "").split())[
            :80
        ]
        return Actor(
            session.group, session.qq, name or "群友", member["role"] != "member"
        )

    def web_sync_status(self, group):
        file_id = self.document_bindings.get(group)
        state = self.binding_error or self.sync_states.get(group, "本群未绑定文档")
        return ((group, file_id) if file_id else None), state

    async def terminate(self):
        self.form_url = ""
        _join_prompts.clear()
        if self.form_runner is not None:
            await self.form_runner.cleanup()
            self.form_runner = None
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

    @staticmethod
    def in_group(event):
        return event.get_platform_name() == "aiocqhttp" and bool(event.get_group_id())

    def require_document(self, actor):
        group = actor.group.rsplit(":", 1)[-1]
        if (
            self.config.get("require_document", False)
            and group not in self.document_bindings
        ):
            raise RaffleError("本群还未绑定腾讯文档，请先联系管理员设置抽奖记录文档。")

    async def handle_followup(self, event):
        choice = selected_raffle(event)
        if choice is not None:
            _join_prompts.pop((group_scope(event), str(event.get_sender_id())), None)
            await self.handle(event, f"报名 {choice}")
            return
        command = mention_command(event)
        if command is not None:
            await self.handle(event, command)

    async def handle(self, event, command):
        if not self.in_group(event) or str(event.get_sender_id()) == str(
            event.get_self_id()
        ):
            return
        group = group_scope(event)
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
        command = command.strip()
        try:
            if command == "管理":
                reply = await self.send_access(event, actor)
            elif command == "帮助" and HELP_CARD.is_file():
                reply = MessageChain(use_t2i_=False).file_image(str(HELP_CARD))
            else:
                reply = await self.execute(actor, command, with_mentions=True)
        except RaffleError as exc:
            reply = str(exc)
        except Exception as exc:
            self.logger.error("群抽奖记录操作异常 (%s)", type(exc).__name__)
            reply = "抽奖记录处理异常，请稍后查询状态。"
        if isinstance(reply, DrawReply):
            messages = self.draw_messages(reply.raffle, reply.drawer)
        elif isinstance(reply, MessageChain):
            messages = (reply,)
        else:
            messages = (
                MessageChain(use_t2i_=False, use_markdown_=False).message(reply),
            )
        try:
            for message in messages:
                try:
                    async with asyncio.timeout(12):
                        await event.send(message)
                except Exception as exc:
                    self.logger.warning(
                        "抽奖消息发送状态未确认 (%s)", type(exc).__name__
                    )
        finally:
            event.stop_event()

    async def send_access(self, event, actor):
        if not self.form_url:
            return "抽奖管理台暂未开启，可用 /抽奖 列表 查看记录。"
        auth = WebAuth(self.store)
        private_token = await asyncio.to_thread(
            auth.issue, actor, int(time.time()), private=True
        )
        minutes = INVITE_TTL // 60
        url = f"{self.form_url}/a/{private_token}"
        try:
            async with asyncio.timeout(5):
                data = await event.bot.call_action(
                    "send_private_msg",
                    user_id=int(actor.qq),
                    message=[
                        {
                            "type": "text",
                            "data": {
                                "text": (
                                    f"你的群抽奖管理入口，{minutes} 分钟内使用一次。请勿转发。\n{url}"
                                )
                            },
                        }
                    ],
                )
            if isinstance(data, Mapping) and data.get("message_id") is not None:
                return "抽奖管理入口已私发，请查看机器人私聊。"
        except Exception as exc:
            self.logger.info("抽奖访问链接私发回执未确认 (%s)", type(exc).__name__)
        token = await asyncio.to_thread(
            auth.issue, actor, int(time.time()), private=False, replace=False
        )
        return (
            f"可以用这个入口打开抽奖管理台，{minutes} 分钟内由领取者在原群确认。\n"
            f"{self.form_url}/a/{token}"
        )

    async def execute(self, actor, command, *, with_mentions=False):
        parts = (command.strip() or "进行中").split(maxsplit=4)
        action, *args = parts
        now = int(time.time())
        if action == "帮助" or action not in ACTIONS:
            return HELP
        if action == "管理" and not args:
            return "在原 QQ 群发 /抽奖管理 领取管理入口。"
        if action == "登录":
            return "网页入口已改为限时链接。在本群发 /抽奖管理 领取。"
        if action == "访问" and len(args) == 1 and self.form_url:
            await asyncio.to_thread(
                WebAuth(self.store).approve_shared, actor, args[0], now
            )
            return "已确认本群访问权限。回到原网页，点击「我已确认，进入管理台」。"
        if action == "发起":
            self.require_document(actor)
            if not args and self.form_url:
                token = await asyncio.to_thread(self.store.new_form, actor, now)
                return f"打开表单填写奖品、人数和截止时间（链接 15 分钟有效）：\n{self.form_url}/f/{token}\n填完后复制确认指令，回本群发布。"
            if len(args) != 4:
                return HELP
            title, slots, deadline = validate_details(
                args[3], number(args[0]), f"{args[1]} {args[2]}", now
            )
            raffle = await asyncio.to_thread(
                self.store.create, actor, title, slots, deadline, now
            )
            return self.created_reply(raffle, actor.name, title, slots, deadline, now)
        if action == "确认" and not args:
            pending = self.early_draws.pop((actor.group, actor.qq), None)
            if pending is None or pending[0] <= time.monotonic():
                return "没有待确认的提前开奖，请先发 /抽奖 开奖。"
            return await self.draw(actor, pending[1], now, with_mentions, early=True)
        if action == "确认" and len(args) == 1:
            self.require_document(actor)
            code = args[0].upper()
            if not re.fullmatch(r"[A-F0-9]{8}", code):
                raise RaffleError("请复制表单上的确认指令。")
            raffle, title, slots, deadline = await asyncio.to_thread(
                self.store.confirm_form, actor, code, now
            )
            return self.created_reply(raffle, actor.name, title, slots, deadline, now)
        if action == "同步状态" and not args:
            group = actor.group.rsplit(":", 1)[-1]
            state = self.binding_error or self.sync_states.get(
                group, "本群未绑定文档，记录保存在本地"
            )
            return "腾讯文档同步：" + state
        if action in ("进行中", "列表", "历史") and len(args) <= 1:
            page = number(args[0]) if args else 1
            open_only = action == "进行中"
            rows = await asyncio.to_thread(
                self.store.list_raffles,
                actor.group,
                page,
                action == "历史",
                active_at=now if open_only else None,
            )
            if open_only:
                lines = [
                    f"【正在报名的抽奖】第 {page} 页"
                    if page > 1
                    else "【正在报名的抽奖】"
                ]
            else:
                lines = [f"抽奖{action} · 第 {page} 页"]
            for row in rows[:PAGE_SIZE]:
                status = (
                    "待开奖"
                    if row["status"] == "open" and now >= row["deadline"]
                    else STATUSES[row["status"]]
                )
                label = "" if open_only else f"{status}｜"
                lines.append(
                    f"#{row['id']} {row['title']}｜{label}{row['people']} 人｜{display_time(row['deadline'])} 截止"
                )
            if not rows:
                lines.append(
                    "本群当前没有可报名的抽奖。"
                    if open_only and page == 1
                    else "这一页没有记录。"
                )
            elif open_only:
                lines.append("报名：/抽奖 报名")
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
        if action == "报名" and not args:
            return await self.join_prompt(actor, now)
        if action == "开奖":
            self.early_draws.pop((actor.group, actor.qq), None)
        if action == "开奖" and not args:
            return await self.choose_draw(actor, now, with_mentions)
        if action in ("报名", "开奖", "取消") and len(args) == 1:
            raffle_id = number(args[0])
            if action == "报名":
                return await self.join(actor, raffle_id, now)
            if action == "取消":
                await asyncio.to_thread(
                    self.store.close, actor, raffle_id, now, cancel=True
                )
                return f"抽奖 #{raffle_id} 已取消。"
            return await self.draw(actor, raffle_id, now, with_mentions)
        return HELP

    async def join(self, actor, raffle_id, now):
        slot, created = await asyncio.to_thread(self.store.join, actor, raffle_id, now)
        _join_prompts.pop((actor.group, actor.qq), None)
        state = "报名成功" if created else "你已报名"
        return f"{state}。抽奖 #{raffle_id}，你的报名序号是 {slot}。"

    async def join_prompt(self, actor, now):
        rows = await asyncio.to_thread(self.store.open_for_join, actor.group, now)
        key = (actor.group, actor.qq)
        _join_prompts.pop(key, None)
        if not rows:
            return "本群当前没有可报名的抽奖。"
        if len(rows) == 1:
            return await self.join(actor, rows[0]["id"], now)
        moment = time.monotonic()
        for identity, (until, _) in tuple(_join_prompts.items()):
            if until <= moment:
                _join_prompts.pop(identity, None)
        _join_prompts[key] = (moment + CHOICE_SECONDS, {row["id"] for row in rows})
        lines = ["本群有多场抽奖正在报名："]
        lines.extend(f"{r['id']} · {r['title'][:48]}" for r in rows[:PAGE_SIZE])
        if len(rows) > PAGE_SIZE:
            lines.append(f"共 {len(rows)} 场，其余用 /抽奖 进行中 2 或在管理台查看。")
        lines.append(
            "请在 2 分钟内直接发对应数字，不要回复或 @ 机器人。也可以发 /抽奖 报名 编号。"
        )
        return "\n".join(lines)

    async def choose_draw(self, actor, now, with_mentions):
        rows = await asyncio.to_thread(self.store.owned_pending, actor)
        if not rows:
            return "你在本群没有待开奖的场次。可用 /抽奖 历史 查看已结束场次。群管理可以发 /抽奖 开奖 编号。"
        ready = [row for row in rows if row["deadline"] <= now and row["people"]]
        candidates = ready or rows
        if len(candidates) == 1:
            return await self.draw(actor, candidates[0]["id"], now, with_mentions)
        lines = ["你发起了多场抽奖，请发 /抽奖 开奖 编号 选择一场："]
        lines.extend(
            f"#{r['id']} · {r['title'][:48]} · {r['people']} 人 · {display_time(r['deadline'])} 截止"
            for r in candidates[:PAGE_SIZE]
        )
        if len(candidates) > PAGE_SIZE:
            lines.append("其余场次可以用 /抽奖 列表 或在管理台查看。")
        return "\n".join(lines)

    async def draw(self, actor, raffle_id, now, with_mentions, *, early=False):
        try:
            fresh, raffle, _ = await asyncio.to_thread(
                self.store.close, actor, raffle_id, now, early=early
            )
        except EarlyDrawRequired as exc:
            moment = time.monotonic()
            self.early_draws = {
                key: pending
                for key, pending in self.early_draws.items()
                if pending[0] > moment
            }
            self.early_draws[(actor.group, actor.qq)] = (
                moment + CHOICE_SECONDS,
                raffle_id,
            )
            return (
                f"抽奖 #{raffle_id}「{exc.title}」原定 {display_time(exc.deadline)} 截止（北京时间）。"
                "要按当前报名名单提前开奖吗？请在 2 分钟内发 /抽奖 确认。"
            )
        if fresh and with_mentions:
            return DrawReply(dict(raffle), actor.qq)
        prefix = "" if fresh else "这场已经开过奖，以下是原结果。\n"
        return prefix + self.result(raffle, 1)

    @staticmethod
    def created_reply(raffle, creator, title, slots, deadline, now, *, state="open"):
        text = (
            f"抽奖 #{raffle} 已发起｜{title}\n发起人：{creator}｜抽 {slots} 人\n"
            f"截止：{display_time(deadline)}（北京时间）"
        )
        if state != "open":
            return f"{text}\n当前状态：{STATUSES[state]}。"
        reminder = (
            "截止前一小时和截止时会提醒发起人。"
            if deadline - now > 3600
            else "距截止不超过一小时，截止时会提醒发起人。"
        )
        return f"{text}\n报名：/抽奖 报名 或 /抽奖 报名 {raffle}\n{reminder}"

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

    def draw_messages(self, raffle, drawer):
        winners = json.loads(raffle["winners"])
        for start in range(0, len(winners), PAGE_SIZE):
            message = MessageChain(use_t2i_=False, use_markdown_=False)
            if start == 0 and drawer:
                message.message("开奖人 ").at("", drawer).message("\n")
            message.message(self.result(raffle, start // PAGE_SIZE + 1))
            mentions = [
                winner
                for winner in winners[start : start + PAGE_SIZE]
                if start or winner["qq"] != drawer
            ]
            if mentions:
                message.message("\n获奖通知 ")
                for winner in mentions:
                    message.at(winner["name"], winner["qq"]).message(" ")
            yield message

    async def reminder_loop(self):
        while True:
            try:
                for raffle in await asyncio.to_thread(self.store.due, int(time.time())):
                    await self.remind(raffle)
                for raffle in await asyncio.to_thread(self.store.pending_notices):
                    await self.announce(raffle)
            except Exception as exc:
                self.logger.warning("群抽奖通知轮询失败 (%s)", type(exc).__name__)
            await asyncio.sleep(5)

    async def online_platform(self, scope):
        platform_id, _, _ = scope.split(":", 2)
        if not await self.is_session_enabled(scope):
            return None
        platform = self.qq_platform(platform_id)
        if platform is None:
            return None
        try:
            async with asyncio.timeout(4):
                status = await platform.bot.call_action("get_status")
            if not isinstance(status, dict) or status.get("online") is False:
                return None
        except Exception:
            return None
        return platform

    async def announce(self, raffle):
        platform = await self.online_platform(raffle["scope"])
        if platform is None or not await asyncio.to_thread(
            self.store.claim_notice, raffle
        ):
            return
        number = raffle["id"]
        now = int(time.time())
        if raffle["kind"] == "网页开奖":
            messages = self.draw_messages(raffle, raffle["drawer"])
        elif raffle["kind"] == "网页取消":
            messages = (
                MessageChain().message(
                    f"抽奖 #{number}「{raffle['title']}」已从网页管理台取消。"
                ),
            )
        else:
            state = raffle["status"]
            if state == "open" and now >= raffle["deadline"]:
                state = "closed"
            text = self.created_reply(
                number,
                raffle["creator"] or "群友",
                raffle["title"],
                raffle["slots"],
                raffle["deadline"],
                raffle["due"],
                state=state,
            )
            messages = (MessageChain().message(text),)
        group = int(raffle["scope"].rsplit(":", 1)[-1])
        confirmed = False
        try:
            all_confirmed = True
            for message in messages:
                try:
                    async with asyncio.timeout(12):
                        response = await platform.bot.call_action(
                            "send_group_msg",
                            group_id=group,
                            message=[part.toDict() for part in message.chain],
                        )
                    all_confirmed &= (
                        isinstance(response, dict)
                        and response.get("message_id") is not None
                    )
                except Exception:
                    all_confirmed = False
            confirmed = all_confirmed
            if not confirmed:
                self.logger.warning("抽奖 #%s 网页操作群消息发送状态未确认", number)
        finally:
            await asyncio.to_thread(
                self.store.finish_notice, raffle, int(time.time()), confirmed
            )

    async def remind(self, raffle):
        platform = await self.online_platform(raffle["scope"])
        if platform is None:
            return
        group = raffle["scope"].rsplit(":", 1)[-1]
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
