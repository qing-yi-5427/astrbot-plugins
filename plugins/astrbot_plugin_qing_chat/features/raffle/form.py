"""A mobile form that saves drafts for publication in QQ or its verified dashboard."""

import asyncio
import base64
import hashlib
import time
from datetime import datetime
from html import escape
from urllib.parse import urlsplit

from aiohttp import web

from .dashboard import add_dashboard
from .store import ZONE, RaffleError, display_time, validate_details

FORM_PORT = 8769
SCRIPT = """document.querySelector('#copy').addEventListener('click', async (event) => {
  try {
    await navigator.clipboard.writeText(document.querySelector('#command').value);
    event.target.textContent = '已复制';
  } catch {
    document.querySelector('#command').select();
  }
});"""
SCRIPT_HASH = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
STYLE = """
:root { color-scheme: light; font-family: system-ui, sans-serif; color: #172931; background: #edf3f1; }
* { box-sizing: border-box; }
body { margin: 0; padding: 28px 16px; }
main { max-width: 460px; margin: auto; background: #fff; border: 1px solid #ccdcd6; border-radius: 20px; padding: 24px; }
h1 { font-size: 1.65rem; margin: 0 0 12px; }
p { line-height: 1.55; margin: 0 0 16px; color: #435960; }
label { display: block; margin-top: 20px; font-weight: 650; }
input { display: block; width: 100%; min-height: 48px; margin-top: 7px; padding: 10px 12px; border: 1px solid #a4bab3; border-radius: 9px; color: inherit; background: #fff; font: inherit; }
input:focus, button:focus { outline: 2px solid #176957; outline-offset: 2px; }
button { width: 100%; min-height: 48px; margin-top: 20px; padding: 12px; border: 0; border-radius: 9px; background: #176152; color: white; font: inherit; font-weight: 650; }
.error { background: #fff4f1; color: #8b301e; border-radius: 8px; padding: 10px; }
.prize { color: #172931; font-weight: 650; overflow-wrap: anywhere; }
"""


def form_origin(value):
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.port
    ):
        raise ValueError("表单地址须为 HTTPS 域名")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("表单地址只填域名")
    return f"https://{parts.netloc}"


def page(title, body, status=200):
    html = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title><style>{STYLE}</style>
<main><h1>{escape(title)}</h1>{body}<p><a href="/">返回抽奖管理台</a></p></main></html>"""
    return web.Response(
        text=html,
        status=status,
        content_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": f"default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{SCRIPT_HASH}'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        },
    )


def editor(now, *, title="", slots="1", deadline="", error=""):
    def when(stamp):
        return datetime.fromtimestamp(stamp, ZONE).strftime("%Y-%m-%dT%H:%M")

    deadline = deadline or when(now + 7200)
    notice = f'<p class="error" role="alert">{escape(error)}</p>' if error else ""
    return page(
        "发起抽奖",
        f"""<p>填完后回到原 QQ 群确认发布。</p>{notice}
<form method="post">
  <label>奖品<input name="title" required maxlength="120" value="{escape(title)}" placeholder="例如：Steam 礼物"></label>
  <label>中奖人数<input name="slots" type="number" inputmode="numeric" min="1" max="999999999" required value="{escape(slots)}"></label>
  <label>报名截止（北京时间）<input name="deadline" type="datetime-local" required min="{when(now + 60)}" value="{escape(deadline)}"></label>
  <button type="submit">保存并获取确认指令</button>
</form>""",
        status=400 if error else 200,
    )


def saved(row):
    return page(
        "草稿已保存",
        f"""<p class="prize">{escape(row["title"])}</p>
<p>抽 {row["slots"]} 人 · {display_time(row["deadline"])} 截止（北京时间）</p>
<p>请用发起表单的 QQ 号，在原群发送这条指令。</p>
<label>确认指令<input id="command" readonly value="/抽奖 确认 {escape(row["code"])}"></label>
<button id="copy" type="button">复制确认指令</button>
<p>已通过 QQ 群登录？<a href="/panel/draft">在管理台发布这场抽奖</a></p>
<script>{SCRIPT}</script>""",
    )


def create_app(store, origin, *, resolve_actor=None, sync_status=None):
    async def form(request):
        token = request.match_info["token"]
        now = int(time.time())
        draft = await asyncio.to_thread(store.form, token, now)
        if draft is None:
            return page(
                "表单已失效", "<p>请回到 QQ 群发送「/抽奖 发起」获取新链接。</p>", 404
            )
        if request.method == "GET":
            return saved(draft) if draft["title"] is not None else editor(now)
        if (
            request.headers.get("Origin", origin) != origin
            or request.headers.get("Sec-Fetch-Site") == "cross-site"
        ):
            return page("无法提交", "<p>请从机器人发的原链接打开表单。</p>", 403)
        if request.content_type != "application/x-www-form-urlencoded":
            return page("无法提交", "<p>请使用页面上的表单提交。</p>", 415)
        data = await request.post()
        title, slots, deadline = (
            str(data.get(key, "")) for key in ("title", "slots", "deadline")
        )
        try:
            if not slots.isascii() or not slots.isdecimal() or len(slots) > 9:
                raise RaffleError("中奖人数必须为正整数。")
            details = validate_details(title, int(slots), deadline, now)
            await asyncio.to_thread(store.save_form, token, *details, now)
        except RaffleError as exc:
            return editor(
                now, title=title, slots=slots, deadline=deadline, error=str(exc)
            )
        draft = await asyncio.to_thread(store.form, token, int(time.time()))
        if draft is None:
            return page("表单已失效", "<p>请回到 QQ 群重新发起。</p>", 404)
        return saved(draft)

    async def landing(_request):
        return page(
            "QQ 群抽奖",
            "<p>在 QQ 群发送「/抽奖 发起」，使用机器人回复的专用链接填写。</p>",
        )

    app = web.Application(client_max_size=4096)
    if resolve_actor is None:
        app.router.add_get("/", landing)
    else:
        add_dashboard(app, store, origin, resolve_actor, sync_status)
    app.router.add_get(r"/f/{token:[A-Za-z0-9_-]{43}}", form, allow_head=False)
    app.router.add_post(r"/f/{token:[A-Za-z0-9_-]{43}}", form)
    return app
