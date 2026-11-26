"""Authorized page models and form handlers for the raffle dashboard."""

import asyncio
import csv
import hmac
import io
import json
import re
import secrets
import time
from html import escape
from pathlib import Path
from urllib.parse import urlencode

from aiohttp import web

from .store import PAGE_SIZE, STATUSES, RaffleError, display_time
from .summary import (
    RANK_LIMIT,
    group_summary,
    merge_nickname,
    nickname_merges,
    undo_nickname_merge,
)
from .web_auth import COOKIE, INVITE_TTL, SESSION_TTL, WebAuth
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
    # Native form submissions must retain their Origin for the same-origin check.
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}
EXPORTS = {"participants": "导出名单", "audit": "导出流水"}
OPERATIONS = {"draw": "开奖", "cancel": "取消"}
NICKNAMES_PATH = "/panel/summary/nicknames"


class QQUnavailable(RuntimeError):
    """A current group role could not be verified."""


def page(title, view, data, *, account=None, status=200):
    nonce = secrets.token_urlsafe(16)
    # React Aria reads this nonce for its press and overlay styles.
    policy = HEADERS["Content-Security-Policy"].replace(
        "style-src 'self';", f"style-src 'self' 'nonce-{nonce}';"
    )
    model = json.dumps(
        {"title": title, "view": view, "data": data, "account": account},
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("<", r"\u003c")
    return web.Response(
        text=f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="csp-nonce" nonce="{nonce}">
<title>{escape(title)} · QQ 群抽奖</title><link rel="stylesheet" href="/assets/raffle.css">
<script type="module" src="/assets/raffle.js"></script></head><body>
<div id="raffle-app"></div>
<script id="raffle-model" type="application/json">{model}</script>
<noscript>请启用 JavaScript 后使用抽奖管理台。</noscript></body></html>""",
        status=status,
        content_type="text/html",
        headers={**HEADERS, "Content-Security-Policy": policy},
    )


def notice(title, message, *, status=200, action=None, back=None, account=None):
    return page(
        title,
        "notice",
        {"message": message, "action": action, "back": back},
        status=status,
        account=account,
    )


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


def pagination(number, more, link):
    return {
        "page": number,
        "previous": link(number - 1) if number > 1 else None,
        "next": link(number + 1) if more else None,
    }


def locked():
    return notice(
        "QQ 群抽奖",
        "在 QQ 群发 /抽奖管理，用机器人发来的限时链接进入。",
        status=403,
    )


def invite_invalid():
    return notice(
        "链接已失效",
        "需要新链接时，在原群发 /抽奖管理。",
        status=404,
        action={"label": "返回管理台", "href": "/panel"},
    )


def result_row(row, actor):
    return {
        "id": row["id"],
        "title": row["title"],
        "state": row["state"],
        "deadline": display_time(row["deadline"]),
        "people": row["people"],
        "slots": row["slots"],
        "own": row["my_number"],
        "created": row["owner"] == actor.qq,
    }


def detail_model(
    raffle, own, total, winners, entries, audit, reminders, actor, now, current_page
):
    number = raffle["id"]
    path = f"/panel/r/{number}"
    state = status_of(raffle, now)
    manage = actor.can_manage(raffle)
    actions = []
    if state == "open" and own is None:
        actions.append({"label": "报名", "href": f"{path}/join", "method": "post"})
    if manage and state == "closed" and total:
        actions.append({"label": "开奖", "href": f"{path}/draw"})
    if manage and state in {"open", "closed"}:
        actions.append(
            {"label": "取消抽奖", "href": f"{path}/cancel", "secondary": True}
        )
    start = (current_page - 1) * PAGE_SIZE
    results = winners[start : start + PAGE_SIZE + 1]
    management = None
    if manage:
        management = {
            "participants": [tuple(row) for row in entries[:PAGE_SIZE]],
            "audit": [
                (
                    display_time(row["at"]),
                    row["kind"],
                    row["name"],
                    audit_detail(row["detail"]),
                )
                for row in audit[:PAGE_SIZE]
            ],
            "reminders": [
                (row["kind"], display_time(row["due"]), REMINDERS[row["status"]])
                for row in reminders
            ],
            "exports": {
                kind: {
                    "href": f"{path}/export/{kind}",
                    "label": label,
                    "secondary": True,
                }
                for kind, label in EXPORTS.items()
            },
        }
    return {
        "id": number,
        "state": state,
        "own": own,
        "people": total,
        "slots": raffle["slots"],
        "deadline": display_time(raffle["deadline"]),
        "actions": actions,
        "management": management,
        "winners": {
            "eligible": raffle["eligible"],
            "count": len(winners),
            "rows": [(row["number"], row["name"]) for row in results[:PAGE_SIZE]],
        }
        if state == "drawn"
        else None,
        "pager": pagination(
            current_page,
            max(len(entries), len(audit), len(results)) > PAGE_SIZE,
            lambda n: f"{path}?page={n}",
        ),
    }


def csv_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(
        ("=", "+", "-", "@", "＝", "＋", "－", "＠")
    ):
        return "'" + value
    return value


def operation_error(message, path, account):
    return notice(
        "操作未完成",
        message,
        status=409,
        account=account,
        action={"href": path, "label": "返回查看当前状态"},
    )


class Dashboard:
    def __init__(self, store, origin, resolve_actor, sync_status, require_document):
        self.store = store
        self.auth = WebAuth(store)
        self.origin = origin
        self.resolve_actor = resolve_actor
        self.sync_status = sync_status
        self.require_document = require_document

    @staticmethod
    def browser(request):
        return request.cookies.get(COOKIE, "")

    def csrf(self, request):
        return self.auth.sign("csrf", self.browser(request))

    async def landing(self, request):
        if await asyncio.to_thread(
            self.auth.session, self.browser(request), int(time.time())
        ):
            raise web.HTTPSeeOther("/panel", headers=HEADERS)
        return locked()

    async def invite(self, request):
        token = request.match_info["token"]
        grant = await asyncio.to_thread(self.auth.grant, token, int(time.time()))
        if grant is None:
            return invite_invalid()
        hint = (
            "这是机器人私发的一次性链接。开启后可在此浏览器使用 30 分钟。"
            if grant["private"]
            else "请点击继续，由领取者在原 QQ 群确认本次访问。"
        )
        return notice(
            "打开抽奖管理台",
            hint,
            action={"href": "/a/" + token, "label": "继续", "method": "post"},
        )

    async def activate(self, request):
        await self.check_post(request)
        token = request.match_info["token"]
        now = int(time.time())
        grant = await asyncio.to_thread(self.auth.grant, token, now)
        if grant is None:
            return invite_invalid()
        if not grant["private"]:
            browser, challenge = self.auth.shared_challenge(token)
            response = page(
                "在原群确认访问", "pairing", {"command": f"/抽奖 访问 {challenge}"}
            )
            self.set_cookie(response, browser, INVITE_TTL + SESSION_TTL)
            return response
        browser = await asyncio.to_thread(self.auth.accept_private, token, now)
        if browser is None:
            return invite_invalid()
        response = web.HTTPSeeOther("/panel", headers=HEADERS)
        self.set_cookie(response, browser, SESSION_TTL)
        raise response

    @staticmethod
    def set_cookie(response, browser, max_age):
        response.set_cookie(
            COOKIE,
            browser,
            secure=True,
            httponly=True,
            samesite="Strict",
            max_age=max_age,
        )

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
            return None, locked()
        try:
            actor = await self.resolve_actor(session)
        except QQUnavailable:
            return None, notice(
                "暂时无法核对群身份",
                "请等 QQ 机器人重新连接后再试。",
                status=503,
            )
        if actor is None:
            return None, notice(
                "当前无法访问本群",
                "请确认你仍在该群，或退出后重新领取入口。",
                status=403,
                action={
                    "href": "/logout",
                    "method": "post",
                    "label": "退出登录",
                    "csrf": self.csrf(request),
                },
            )
        return actor, None

    def account(self, request, actor):
        return {
            "name": actor.name,
            "role": "群管理" if actor.moderator else "群成员",
            "group": actor.group.rsplit(":", 1)[-1],
            "csrf": self.csrf(request),
        }

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
            f"已同步 {written} 场 · 待同步 {pending} 场" if replica else "本地保存"
        )
        return page(
            "抽奖管理台",
            "panel",
            {
                "rows": [result_row(row, actor) for row in rows[:PAGE_SIZE]],
                "filters": {
                    "query": filters.query,
                    "status": filters.status,
                    "view": filters.view,
                },
                "tabs": [
                    {
                        "id": key or "all",
                        "label": label,
                        "count": counts.get(key, 0) if key else sum(counts.values()),
                        "href": panel_url(filters, status=key, page=1),
                    }
                    for key, label in [("", "全部"), *STATUSES.items()]
                ],
                "sync": {"state": sync, "progress": progress},
                "pager": pagination(
                    filters.page,
                    len(rows) > PAGE_SIZE,
                    lambda n: panel_url(filters, page=n),
                ),
            },
            account=self.account(request, actor),
        )

    async def new_draft(self, request):
        await self.check_post(request, require_csrf=True)
        actor, failure = await self.need_actor(request)
        if failure is not None:
            return failure
        try:
            if self.require_document is not None:
                self.require_document(actor)
            token = await asyncio.to_thread(
                self.store.new_form, actor, int(time.time())
            )
        except RaffleError as exc:
            return operation_error(str(exc), "/panel", self.account(request, actor))
        raise web.HTTPSeeOther("/f/" + token, headers=HEADERS)

    async def summary(self, request):
        actor, failure = await self.need_actor(request)
        if failure is not None:
            return failure
        number = page_number(request)
        source = request.query.get("source", "")
        rank_query = request.query.get("nickname", "").strip()[:120]
        if source not in {"archive", "live"}:
            source = ""
        stats, rows, rankings = await asyncio.to_thread(
            group_summary,
            self.store,
            actor.group,
            int(time.time()),
            page=number,
            source=source,
            rank_query=rank_query,
        )

        def link(next_page):
            query = {"source": source} if source else {}
            if rank_query:
                query["nickname"] = rank_query
            if next_page > 1:
                query["page"] = str(next_page)
            return "/panel/summary" + ("?" + urlencode(query) if query else "")

        return page(
            "抽奖总结",
            "summary",
            {
                "stats": stats,
                "rankings": rankings,
                "rank_limit": RANK_LIMIT,
                "rank_query": rank_query,
                "rows": rows[:PAGE_SIZE],
                "source": source,
                "pager": pagination(number, len(rows) > PAGE_SIZE, link),
            },
            account=self.account(request, actor),
        )

    async def nickname_admin(self, request):
        actor, failure = await self.need_actor(request)
        if failure is not None:
            return None, failure
        if not actor.moderator:
            return None, notice(
                "需要群管理权限",
                "仅群主和群管理员可以合并本群的历史昵称。",
                status=403,
                account=self.account(request, actor),
                back={"href": "/panel/summary", "label": "抽奖总结"},
            )
        return actor, None

    async def nicknames(self, request):
        actor, failure = await self.nickname_admin(request)
        if failure is not None:
            return failure
        rules = await asyncio.to_thread(nickname_merges, self.store, actor.group)
        feedback = {"merged": "合并已生效。", "undone": "已撤销这条合并。"}
        return page(
            "合并历史昵称",
            "nicknames",
            {"rules": rules, "message": feedback.get(request.query.get("change"))},
            account=self.account(request, actor),
        )

    async def merge_nicknames(self, request):
        await self.check_post(request, require_csrf=True)
        actor, failure = await self.nickname_admin(request)
        if failure is not None:
            return failure
        form = await request.post()
        old, target = form.get("old"), form.get("target")
        if any(
            not isinstance(value, str) or len(value) > 200 for value in (old, target)
        ):
            raise web.HTTPBadRequest(headers=HEADERS)
        try:
            await asyncio.to_thread(
                merge_nickname, self.store, actor.group, old, target
            )
        except RaffleError as exc:
            return operation_error(
                str(exc), NICKNAMES_PATH, self.account(request, actor)
            )
        raise web.HTTPSeeOther(f"{NICKNAMES_PATH}?change=merged", headers=HEADERS)

    async def undo_merge(self, request):
        await self.check_post(request, require_csrf=True)
        actor, failure = await self.nickname_admin(request)
        if failure is not None:
            return failure
        number = int(request.match_info["number"])
        try:
            await asyncio.to_thread(
                undo_nickname_merge, self.store, actor.group, number
            )
        except RaffleError as exc:
            return operation_error(
                str(exc), NICKNAMES_PATH, self.account(request, actor)
            )
        raise web.HTTPSeeOther(f"{NICKNAMES_PATH}?change=undone", headers=HEADERS)

    async def draft(self, request):
        actor, failure = await self.need_actor(request)
        if failure is not None:
            return failure
        row = await asyncio.to_thread(self.store.current_draft, actor, int(time.time()))
        draft = None
        if row is not None:
            draft = {
                "title": row["title"],
                "code": row["code"],
                "slots": row["slots"],
                "deadline": display_time(row["deadline"]),
            }
        return page(
            "待发布草稿",
            "draft",
            {"draft": draft},
            account=self.account(request, actor),
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
            if self.require_document is not None:
                self.require_document(actor)
            number, *_ = await asyncio.to_thread(
                self.store.confirm_form, actor, code, int(time.time()), web=True
            )
        except RaffleError as exc:
            return operation_error(
                str(exc), "/panel/draft", self.account(request, actor)
            )
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
            "raffle",
            detail_model(*result, actor, int(time.time()), current_page),
            account=self.account(request, actor),
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
            else "将关闭报名并在 QQ 群发出取消消息。"
        )
        return notice(
            f"确认{title}",
            f"#{number} · {raffle['title']}。{description}",
            action={
                "href": f"{path}/{operation}",
                "label": f"确认{title}",
                "method": "post",
                "danger": operation == "cancel",
            },
            back={"href": path, "label": "返回抽奖详情"},
            account=self.account(request, actor),
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
            return operation_error(str(exc), path, self.account(request, actor))
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


def add_dashboard(app, store, origin, resolve_actor, sync_status, require_document):
    dashboard = Dashboard(store, origin, resolve_actor, sync_status, require_document)
    assets = Path(__file__).with_name("assets")
    for filename, content_type in (
        ("raffle.css", "text/css"),
        ("raffle.js", "text/javascript"),
    ):
        body = (assets / filename).read_bytes()

        async def asset(_request, data=body, mime=content_type):
            return web.Response(body=data, content_type=mime, headers=HEADERS)

        app.router.add_get("/assets/" + filename, asset)
    app.router.add_get("/", dashboard.landing)
    app.router.add_get(
        r"/a/{token:[A-Za-z0-9_-]{43}}", dashboard.invite, allow_head=False
    )
    app.router.add_post(r"/a/{token:[A-Za-z0-9_-]{43}}", dashboard.activate)
    app.router.add_post("/logout", dashboard.logout)
    app.router.add_get("/panel", dashboard.panel)
    app.router.add_get("/panel/summary", dashboard.summary)
    app.router.add_get(NICKNAMES_PATH, dashboard.nicknames)
    app.router.add_post(NICKNAMES_PATH, dashboard.merge_nicknames)
    app.router.add_post(
        NICKNAMES_PATH + r"/{number:[1-9][0-9]{0,17}}/undo", dashboard.undo_merge
    )
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
