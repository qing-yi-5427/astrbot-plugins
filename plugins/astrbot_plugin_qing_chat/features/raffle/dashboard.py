"""Server-rendered raffle dashboard. Group commands authorize and publish changes."""

import asyncio
import csv
import hmac
import io
import re
import time
from html import escape
from pathlib import Path
from urllib.parse import urlencode

from aiohttp import web

from .store import PAGE_SIZE, STATUSES, RaffleError, display_time
from .web_auth import COOKIE, LOGIN_TTL, SESSION_TTL, WebAuth
from .web_records import (
    REMINDERS,
    RecordFilters,
    audit_detail,
    detail,
    export_records,
    overview,
    status_of,
    sync_progress,
)

HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; script-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
EXPORTS = {"participants": "导出名单", "audit": "导出流水"}
OPERATIONS = {"draw": "开奖", "cancel": "取消"}


class QQUnavailable(RuntimeError):
    """A current group role could not be verified."""


def page(title, body, *, header="", status=200):
    return web.Response(
        text=f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} · 群抽奖</title><link rel="stylesheet" href="/assets/raffle.css">
<script src="/assets/raffle.js" defer></script></head><body>
<header><div class="nav"><a class="brand" href="/"><span aria-hidden="true">✦</span> QQ 群抽奖</a>
{header}</div></header><main>{body}</main></body></html>""",
        status=status,
        content_type="text/html",
        headers=HEADERS,
    )


def copy_command(label, text, show=False):
    command = escape(text, quote=True)
    visible = (
        f'<input class="command" aria-label="QQ 指令" readonly value="{command}">'
        if show
        else ""
    )
    return (
        f'<div class="copy">{visible}<button type="button" class="secondary" '
        f'data-copy="{command}">{escape(label)}</button></div>'
    )


def post_button(path, name, csrf=None, css=""):
    field = f'<input type="hidden" name="csrf" value="{csrf}">' if csrf else ""
    return f'<form method="post" action="{path}">{field}<button class="{css}">{name}</button></form>'


def page_number(request):
    value = request.query.get("page", "1")
    return int(value) if re.fullmatch(r"[1-9][0-9]{0,5}", value) else 1


def panel_url(filters, **overrides):
    values = {
        "q": filters.query,
        "status": filters.status,
        "view": filters.view,
        "page": filters.page,
    }
    values.update(overrides)
    query = urlencode(
        {key: value for key, value in values.items() if value and value != 1}
    )
    return f"/panel?{query}" if query else "/panel"


def status_badge(state):
    return f'<span class="badge {state}">{STATUSES[state]}</span>'


def pagination(page_no, more, link):
    previous = (
        f'<a class="secondary" href="{escape(link(page_no - 1))}">上一页</a>'
        if page_no > 1
        else ""
    )
    following = (
        f'<a class="secondary" href="{escape(link(page_no + 1))}">下一页</a>'
        if more
        else ""
    )
    if previous or following:
        return f'<nav class="pager" aria-label="分页">{previous}<span>第 {page_no} 页</span>{following}</nav>'
    return ""


def table(headers, rows, empty):
    if not rows:
        return f'<p class="muted">{empty}</p>'
    head = "".join(f'<th scope="col">{name}</th>' for name in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f'<div class="table"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def list_card(row, actor):
    raffle_id = row["id"]
    labels = []
    if row["owner"] == actor.qq:
        labels.append("我发起的")
    if row["my_number"] is not None:
        labels.append(f"我的报名序号 {row['my_number']}")
    mine = f'<p class="hint">{escape(" · ".join(labels))}</p>' if labels else ""
    return f"""<li class="raffle"><a href="/panel/r/{raffle_id}">
<div class="item-head"><span class="number">#{raffle_id}</span>{status_badge(row["state"])}</div>
<h2>{escape(row["title"])}</h2>
<p class="raffle-meta">{row["people"]} 人报名 <span>·</span> 抽 {row["slots"]} 人</p>
<p class="raffle-meta">{display_time(row["deadline"])} 截止</p>{mine}</a></li>"""


def login_body(challenge):
    if not challenge:
        return f"""<section class="intro">
<h1>群抽奖管理台</h1><p>查看本群抽奖、报名进度和历史结果。请通过 QQ 群验证身份。</p>
{post_button("/login", "通过 QQ 群登录")}</section>"""
    return f"""<section class="intro">
<h1>在 QQ 群确认登录</h1><p>请在要管理的群发送下方指令。指令 5 分钟内有效。</p>
{copy_command("复制登录指令", f"/抽奖 登录 {challenge}", show=True)}
<p class="hint">只发送这个页面显示的指令。每个浏览器使用自己的指令。</p>
<div class="actions"><a class="button" href="/panel">我已发送，进入管理台</a>
{post_button("/login", "重新生成", css="secondary")}</div></section>"""


def filters_bar(filters, counts):
    options = (("", "查看范围"), ("mine", "我发起的"), ("joined", "我报名的"))
    select = "".join(
        f'<option value="{value}"{" selected" if filters.view == value else ""}>{label}</option>'
        for value, label in options
    )
    tabs = []
    for value, label in [("", "全部"), *STATUSES.items()]:
        url = escape(panel_url(filters, status=value, page=1))
        total = counts.get(value, 0) if value else sum(counts.values())
        current = ' aria-current="page"' if value == filters.status else ""
        tabs.append(f'<a href="{url}"{current}>{label} <span>{total}</span></a>')
    return f"""<nav class="tabs" aria-label="抽奖状态">{"".join(tabs)}</nav>
<form class="filters" method="get" action="/panel">
<input type="hidden" name="status" value="{filters.status}">
<input name="q" aria-label="奖品或抽奖编号" placeholder="搜索奖品或抽奖编号" value="{escape(filters.query)}">
<select name="view" aria-label="查看范围">{select}</select>
<button class="secondary">筛选</button></form>"""


def detail_body(
    raffle,
    own,
    total,
    winners,
    entries,
    audit,
    reminders,
    actor,
    now,
    current_page,
    csrf,
):
    number = raffle["id"]
    path = f"/panel/r/{number}"
    state = status_of(raffle, now)
    manage = actor.can_manage(raffle)
    actions = []
    if state == "open" and own is None:
        actions.append(post_button(f"{path}/join", "报名", csrf))
    if manage and state == "closed" and total:
        actions.append(f'<a class="button" href="{path}/draw">开奖</a>')
    if manage and state in {"open", "closed"}:
        actions.append(f'<a class="secondary" href="{path}/cancel">取消抽奖</a>')
    controls = f'<div class="actions">{"".join(actions)}</div>' if actions else ""
    my_entry = (
        f'<p class="hint">我的报名序号 <strong>{own}</strong></p>'
        if own is not None
        else ""
    )
    header = f"""<a class="back" href="/panel">← 抽奖列表</a>
<section class="detail"><div class="item-head"><span class="number">#{number}</span>{status_badge(state)}</div>
<h1>{escape(raffle["title"])}</h1>
<div class="facts"><div><span>报名人数</span><strong>{total}</strong></div>
<div><span>计划中奖</span><strong>{raffle["slots"]}</strong></div>
<div><span>截止时间 · 北京时间</span><strong>{display_time(raffle["deadline"])}</strong></div></div>
{my_entry}{controls}</section>"""

    start = (current_page - 1) * PAGE_SIZE
    results = winners[start : start + PAGE_SIZE + 1]
    sections = []
    if state == "drawn":
        won = table(
            ("抽中序号", "中奖昵称"),
            [(row["number"], row["name"]) for row in results[:PAGE_SIZE]],
            "本页没有中奖记录。",
        )
        sections.append(f"""<section class="surface"><h2>开奖结果</h2>
<p>共 {raffle["eligible"]} 人报名，从固定报名序号中随机抽中 {len(winners)} 人。</p>{won}</section>""")
    if manage:
        links = {
            key: f'<a class="text-link" href="{path}/export/{key}">{name}</a>'
            for key, name in EXPORTS.items()
        }
        roster = table(
            ("序号", "昵称"),
            [tuple(row) for row in entries[:PAGE_SIZE]],
            "本页没有报名记录。",
        )
        history = table(
            ("时间", "事件", "昵称", "详情"),
            [
                (
                    display_time(row["at"]),
                    row["kind"],
                    row["name"],
                    audit_detail(row["detail"]),
                )
                for row in audit[:PAGE_SIZE]
            ],
            "本页没有操作记录。",
        )
        alerts = table(
            ("提醒", "预定时间", "发送状态"),
            [
                (row["kind"], display_time(row["due"]), REMINDERS[row["status"]])
                for row in reminders
            ],
            "这场抽奖没有提醒记录。",
        )
        sections.extend(
            [
                f'<section class="surface"><div class="item-head"><h2>报名名单</h2>{links["participants"]}</div>{roster}</section>',
                f'<section class="surface"><div class="item-head"><h2>操作流水</h2>{links["audit"]}</div>{history}</section>',
                f'<section class="surface"><h2>QQ 提醒与公告</h2>{alerts}</section>',
            ]
        )
    pager = pagination(
        current_page,
        max(len(entries), len(audit), len(results)) > PAGE_SIZE,
        lambda n: f"{path}?page={n}",
    )
    return header + "".join(sections) + pager


def csv_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(
        ("=", "+", "-", "@", "＝", "＋", "－", "＠")
    ):
        return "'" + value
    return value


def operation_error(message, path):
    return page(
        "操作未完成",
        f'<section class="intro"><h1>操作未完成</h1><p>{escape(message)}</p>'
        f'<a class="text-link" href="{path}">返回查看当前状态</a></section>',
        status=409,
    )


class Dashboard:
    def __init__(self, store, origin, resolve_actor, sync_status):
        self.store = store
        self.auth = WebAuth(store)
        self.origin = origin
        self.resolve_actor = resolve_actor
        self.sync_status = sync_status

    @staticmethod
    def browser(request):
        return request.cookies.get(COOKIE, "")

    def csrf(self, request):
        return self.auth.sign("csrf", self.browser(request))

    async def login(self, request):
        browser = self.browser(request)
        now = int(time.time())
        if await asyncio.to_thread(self.auth.session, browser, now):
            raise web.HTTPSeeOther("/panel", headers=HEADERS)
        challenge = self.auth.challenge(browser, now)
        return page("登录", login_body(challenge))

    async def start(self, request):
        await self.check_post(request)
        browser = self.auth.start(int(time.time()))
        response = web.HTTPSeeOther("/login", headers=HEADERS)
        response.set_cookie(
            COOKIE,
            browser,
            secure=True,
            httponly=True,
            samesite="Strict",
            max_age=SESSION_TTL + LOGIN_TTL,
        )
        raise response

    async def check_post(self, request, require_csrf=False):
        if (
            request.headers.get("Origin") != self.origin
            or request.headers.get("Sec-Fetch-Site") == "cross-site"
        ):
            raise web.HTTPForbidden(headers=HEADERS)
        if require_csrf:
            form = await request.post()
            expected = self.csrf(request)
            actual = form.get("csrf")
            if (
                not isinstance(actual, str)
                or not actual.isascii()
                or not hmac.compare_digest(actual, expected)
            ):
                raise web.HTTPForbidden(headers=HEADERS)

    async def need_actor(self, request):
        session = await asyncio.to_thread(
            self.auth.session, self.browser(request), int(time.time())
        )
        if session is None:
            raise web.HTTPSeeOther("/login", headers=HEADERS)
        try:
            actor = await self.resolve_actor(session)
        except QQUnavailable:
            return None, page(
                "QQ 暂不可用",
                '<section class="intro"><h1>暂时无法核对群身份</h1><p>请等 QQ 机器人重新连接后再试。</p></section>',
                status=503,
            )
        if actor is None:
            return None, page(
                "无法访问本群",
                f'<section class="intro"><h1>当前无法访问本群</h1><p>请确认你仍在该群，或退出后重新登录。</p>'
                f"{post_button('/logout', '退出登录', self.csrf(request))}</section>",
                status=403,
            )
        return actor, None

    def header(self, request, actor):
        role = "群管理" if actor.moderator else "群成员"
        logout = post_button("/logout", "退出", self.csrf(request), "secondary")
        return f'<div class="identity"><span>{escape(actor.name)} · {role}</span>{logout}</div>'

    async def logout(self, request):
        await self.check_post(request, require_csrf=True)
        await asyncio.to_thread(self.auth.logout, self.browser(request))
        response = web.HTTPSeeOther("/", headers=HEADERS)
        response.set_cookie(
            COOKIE, "", max_age=0, secure=True, httponly=True, samesite="Strict"
        )
        raise response

    async def panel(self, request):
        actor, failure = await self.need_actor(request)
        if failure is not None:
            return failure
        group = actor.group.rsplit(":", 1)[-1]
        status, view = request.query.get("status", ""), request.query.get("view", "")
        filters = RecordFilters(
            request.query.get("q", "").strip()[:120],
            status if status in STATUSES else "",
            view if view in {"mine", "joined"} else "",
            page_number(request),
        )
        rows, counts = await asyncio.to_thread(
            overview, self.store, actor, filters, int(time.time())
        )
        replica, sync = self.sync_status(group)
        written, pending = await asyncio.to_thread(sync_progress, self.store, replica)
        progress = (
            f"已同步 {written} 条 · 待同步 {pending} 条" if replica else "本地保存"
        )
        listing = (
            f'<ul class="raffles">{"".join(list_card(row, actor) for row in rows[:PAGE_SIZE])}</ul>'
            if rows
            else '<section class="surface empty"><h2>没有匹配的抽奖</h2><p>可以调整筛选，或准备一场新抽奖。</p></section>'
        )
        compose = post_button("/panel/new", "准备新抽奖", self.csrf(request))
        pager = pagination(
            filters.page, len(rows) > PAGE_SIZE, lambda n: panel_url(filters, page=n)
        )
        return page(
            "抽奖管理台",
            f"""<section class="hero"><div><p class="group">QQ群 {escape(group)}</p>
<h1>抽奖管理台</h1><p>网页与 QQ 群共用抽奖记录，时间按北京时间显示。</p></div>
<div class="compose">{compose}<a href="/panel/draft">待发布草稿</a></div></section>
<section class="sync"><strong>腾讯文档</strong><span>{escape(sync)}</span><span>{progress}</span></section>
{filters_bar(filters, counts)}{listing}{pager}""",
            header=self.header(request, actor),
        )

    async def new_draft(self, request):
        await self.check_post(request, require_csrf=True)
        actor, failure = await self.need_actor(request)
        if failure is not None:
            return failure
        token = await asyncio.to_thread(self.store.new_form, actor, int(time.time()))
        raise web.HTTPSeeOther("/f/" + token, headers=HEADERS)

    async def draft(self, request):
        actor, failure = await self.need_actor(request)
        if failure is not None:
            return failure
        row = await asyncio.to_thread(self.store.current_draft, actor, int(time.time()))
        if row is None:
            body = "<p>没有可发布的草稿。</p>"
        else:
            code = escape(row["code"])
            body = f"""<p><strong>{escape(row["title"])}</strong></p>
<p>抽 {row["slots"]} 人 · {display_time(row["deadline"])} 截止（北京时间）</p>
<p>发布后，机器人会在原 QQ 群发出报名公告。</p>
<form method="post" action="/panel/draft">
<input type="hidden" name="csrf" value="{self.csrf(request)}">
<input type="hidden" name="code" value="{code}"><button>发布到 QQ 群</button></form>"""
        return page(
            "待发布草稿",
            f'<a class="back" href="/panel">← 抽奖列表</a><section class="detail"><h1>待发布草稿</h1>{body}</section>',
            header=self.header(request, actor),
        )

    async def publish(self, request):
        await self.check_post(request, require_csrf=True)
        actor, failure = await self.need_actor(request)
        if failure is not None:
            return failure
        data = await request.post()
        code = data.get("code")
        if not isinstance(code, str) or not re.fullmatch(r"[A-F0-9]{8}", code):
            raise web.HTTPBadRequest(headers=HEADERS)
        try:
            number, *_ = await asyncio.to_thread(
                self.store.confirm_form, actor, code, int(time.time()), web=True
            )
        except RaffleError as exc:
            return operation_error(str(exc), "/panel/draft")
        raise web.HTTPSeeOther(f"/panel/r/{number}", headers=HEADERS)

    async def raffle(self, request):
        actor, failure = await self.need_actor(request)
        if failure is not None:
            return failure
        number = int(request.match_info["number"])
        current_page = page_number(request)
        try:
            result = await asyncio.to_thread(
                detail, self.store, actor, number, current_page
            )
        except RaffleError:
            raise web.HTTPNotFound(headers=HEADERS) from None
        return page(
            result[0]["title"],
            detail_body(
                *result, actor, int(time.time()), current_page, self.csrf(request)
            ),
            header=self.header(request, actor),
        )

    async def confirm_operation(self, request):
        actor, failure = await self.need_actor(request)
        if failure is not None:
            return failure
        number = int(request.match_info["number"])
        operation = request.match_info["operation"]
        path = f"/panel/r/{number}"
        try:
            raffle, _ = await asyncio.to_thread(
                self.store.details, actor.group, number, 1
            )
        except RaffleError:
            raise web.HTTPNotFound(headers=HEADERS) from None
        if not actor.can_manage(raffle):
            raise web.HTTPForbidden(headers=HEADERS)
        title = OPERATIONS[operation]
        description = (
            "将从截止时的报名名单中抽取中奖序号，并在 QQ 群公布。"
            if operation == "draw"
            else "将关闭报名并在 QQ 群发出取消公告。"
        )
        return page(
            f"确认{title}",
            f"""<a class="back" href="{path}">← 返回抽奖详情</a>
<section class="intro"><h1>确认{title}</h1>
<p>#{number} · {escape(raffle["title"])}</p><p>{description}</p>
{post_button(f"{path}/{operation}", f"确认{title}", self.csrf(request))}</section>""",
            header=self.header(request, actor),
        )

    async def operate(self, request):
        await self.check_post(request, require_csrf=True)
        actor, failure = await self.need_actor(request)
        if failure is not None:
            return failure
        number = int(request.match_info["number"])
        operation = request.match_info["operation"]
        path = f"/panel/r/{number}"
        now = int(time.time())
        if operation != "join":
            try:
                raffle, _ = await asyncio.to_thread(
                    self.store.details, actor.group, number, 1
                )
            except RaffleError:
                raise web.HTTPNotFound(headers=HEADERS) from None
            if not actor.can_manage(raffle):
                raise web.HTTPForbidden(headers=HEADERS)
        try:
            if operation == "join":
                await asyncio.to_thread(self.store.join, actor, number, now, web=True)
            else:
                await asyncio.to_thread(
                    self.store.close,
                    actor,
                    number,
                    now,
                    cancel=operation == "cancel",
                    web=True,
                )
        except RaffleError as exc:
            return operation_error(str(exc), path)
        raise web.HTTPSeeOther(path, headers=HEADERS)

    async def export(self, request):
        actor, failure = await self.need_actor(request)
        if failure is not None:
            return failure
        number, kind = int(request.match_info["number"]), request.match_info["kind"]
        try:
            result = await asyncio.to_thread(
                export_records, self.store, actor, number, kind
            )
        except RaffleError:
            raise web.HTTPNotFound(headers=HEADERS) from None
        if result is None:
            raise web.HTTPForbidden(headers=HEADERS)
        headings, rows = result
        if kind == "audit":
            rows = [
                (
                    display_time(row["at"]),
                    row["kind"],
                    row["name"],
                    audit_detail(row["detail"]),
                )
                for row in rows
            ]
        data = io.StringIO()
        writer = csv.writer(data)
        writer.writerow(headings)
        writer.writerows([csv_cell(value) for value in row] for row in rows)
        return web.Response(
            body=data.getvalue().encode("utf-8-sig"),
            content_type="text/csv",
            headers={
                **HEADERS,
                "Content-Disposition": f'attachment; filename="raffle-{number}-{kind}.csv"',
            },
        )


def add_dashboard(app, store, origin, resolve_actor, sync_status):
    dashboard = Dashboard(store, origin, resolve_actor, sync_status)
    assets = Path(__file__).with_name("assets")
    for filename, content_type in (
        ("raffle.css", "text/css"),
        ("raffle.js", "text/javascript"),
    ):
        body = (assets / filename).read_bytes()

        async def asset(_request, data=body, mime=content_type):
            return web.Response(body=data, content_type=mime, headers=HEADERS)

        app.router.add_get("/assets/" + filename, asset)
    app.router.add_get("/", dashboard.login)
    app.router.add_get("/login", dashboard.login)
    app.router.add_post("/login", dashboard.start)
    app.router.add_post("/logout", dashboard.logout)
    app.router.add_get("/panel", dashboard.panel)
    app.router.add_post("/panel/new", dashboard.new_draft)
    app.router.add_get("/panel/draft", dashboard.draft)
    app.router.add_post("/panel/draft", dashboard.publish)
    app.router.add_get(r"/panel/r/{number:[1-9][0-9]{0,8}}", dashboard.raffle)
    app.router.add_get(
        r"/panel/r/{number:[1-9][0-9]{0,8}}/{operation:draw|cancel}",
        dashboard.confirm_operation,
    )
    app.router.add_post(
        r"/panel/r/{number:[1-9][0-9]{0,8}}/{operation:join|draw|cancel}",
        dashboard.operate,
    )
    app.router.add_get(
        r"/panel/r/{number:[1-9][0-9]{0,8}}/export/{kind:participants|audit}",
        dashboard.export,
    )
