import re
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from astrbot_plugin_qing_chat.features.raffle.dashboard import QQUnavailable
from astrbot_plugin_qing_chat.features.raffle.form import create_app
from astrbot_plugin_qing_chat.features.raffle.main import RaffleService
from astrbot_plugin_qing_chat.features.raffle.store import (
    ZONE,
    Actor,
    RaffleError,
    Store,
)
from astrbot_plugin_qing_chat.features.raffle.web_auth import (
    COOKIE,
    INVITE_TTL,
    SESSION_TTL,
    WebAuth,
)

NOW = 1800000000
ORIGIN = "https://raffle.example.test"
GROUP = "default:GroupMessage:123"
OWNER = Actor(GROUP, "1001", "发起人")
MEMBER = Actor(GROUP, "2001", "=报名人")
ADMIN = Actor(GROUP, "3001", "群管理", True)
OTHER = Actor("default:GroupMessage:456", OWNER.qq, "其他群成员")


@pytest.fixture
def raffle(tmp_path, monkeypatch):
    clock = SimpleNamespace(now=NOW)
    monkeypatch.setattr(time, "time", lambda: clock.now)
    service = RaffleService(None, {})
    service.store = Store(tmp_path / "raffles.db")
    service.store.initialize()
    service.form_url = ORIGIN
    return service, clock


async def sign_in(client, service, actor):
    token = WebAuth(service.store).issue(actor, int(time.time()), private=True)
    path = "/a/" + token
    preview = await client.get(path)
    assert preview.status == 200 and not preview.cookies
    assert preview.headers["Referrer-Policy"] == "same-origin"
    response = await client.post(
        path, headers={"Origin": ORIGIN}, allow_redirects=False
    )
    assert response.status == 303 and response.headers["Location"] == "/panel"
    cookie = response.cookies[COOKIE]
    assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Strict"
    assert cookie["path"] == "/" and not cookie["domain"]
    assert cookie["max-age"] == str(SESSION_TTL)
    assert (await client.get(path)).status == 404
    return {"Cookie": f"{COOKIE}={cookie.value}", "Origin": ORIGIN}


def hidden(name, html):
    return re.search(f'name="{name}" value="([^"]+)"', html)[1]


def make_app(service, roles):
    async def resolve(session):
        return roles.get((session.group, session.qq))

    return create_app(
        service.store,
        ORIGIN,
        resolve_actor=resolve,
        sync_status=lambda group: ((group, "document"), "已绑定「bot 抽奖记录」"),
    )


def test_web_invite_expiration_replacement_and_session_revoke(raffle):
    service, _ = raffle
    auth = WebAuth(service.store)
    first = auth.issue(OWNER, NOW, private=True)
    assert auth.accept_private(first, NOW + INVITE_TTL) is None
    second = auth.issue(OWNER, NOW, private=True)
    third = auth.issue(OWNER, NOW, private=True)
    assert auth.accept_private(second, NOW) is None
    browser = auth.accept_private(third, NOW)
    assert auth.session(browser, NOW).qq == OWNER.qq
    assert auth.session(browser, NOW + SESSION_TTL) is None
    assert auth.accept_private(third, NOW) is None
    auth.logout(browser)
    assert auth.session(browser, NOW) is None


@pytest.mark.asyncio
async def test_public_invite_requires_the_original_qq_and_browser(raffle):
    service, _ = raffle
    auth = WebAuth(service.store)
    private = auth.issue(OWNER, NOW, private=True)
    shared = auth.issue(OWNER, NOW, private=False, replace=False)
    roles = {(OWNER.group, OWNER.qq): OWNER}
    path = "/a/" + shared
    async with TestClient(TestServer(make_app(service, roles))) as client:
        preview = await client.get(path)
        assert preview.status == 200 and not preview.cookies
        assert (
            await client.post(path, headers={"Origin": "https://bad.example"})
        ).status == 403
        response = await client.post(path, headers={"Origin": ORIGIN})
        cookie = response.cookies[COOKIE].value
        code = re.search(r"/抽奖 访问 ([0-9a-f-]+)", await response.text())[1]
        headers = {"Cookie": f"{COOKIE}={cookie}"}
        assert (await client.get("/panel", headers=headers)).status == 403
        for wrong in (OTHER, MEMBER):
            with pytest.raises(RaffleError, match="原群领取"):
                await service.execute(wrong, f"访问 {code}")
        assert "已确认" in await service.execute(OWNER, f"访问 {code}")
        assert (await client.get("/panel", headers=headers)).status == 200
        assert (await client.get("/panel")).status == 403
        assert (await client.get(path)).status == 404
        assert auth.accept_private(private, NOW) is None
        with pytest.raises(RaffleError, match="有效期"):
            await service.execute(OWNER, f"访问 {code}")


@pytest.mark.asyncio
async def test_dashboard_and_qq_use_same_records_and_permissions(raffle):
    service, clock = raffle
    store = service.store
    roles = {(actor.group, actor.qq): actor for actor in (OWNER, MEMBER, ADMIN, OTHER)}
    other_number = store.create(OTHER, "其他群的奖品", 1, NOW + 900, NOW)
    async with TestClient(TestServer(make_app(service, roles))) as client:
        assert (await client.get("/")).status == 403
        assert (await client.get("/assets/raffle.css")).content_type == "text/css"
        assert (await client.get("/api/plugin/get")).status == 404
        assert (await client.post("/login", headers={"Origin": ORIGIN})).status == 404
        assert (await client.get("/panel", allow_redirects=False)).status == 403

        owner = await sign_in(client, service, OWNER)
        member = await sign_in(client, service, MEMBER)
        admin = await sign_in(client, service, ADMIN)
        owner_panel = await (await client.get("/panel", headers=owner)).text()
        owner_csrf = hidden("csrf", owner_panel)
        assert "其他群的奖品" not in owner_panel
        assert (
            await client.get(f"/panel/r/{other_number}", headers=owner)
        ).status == 404
        draft = await client.post(
            "/panel/new",
            headers=owner,
            data={"csrf": owner_csrf},
            allow_redirects=False,
        )
        assert draft.status == 303
        deadline = datetime.fromtimestamp(NOW + 900, ZONE).strftime("%Y-%m-%dT%H:%M")
        saved = await client.post(
            draft.headers["Location"],
            headers=owner,
            data={"title": "<抽奖礼物>", "slots": 1, "deadline": deadline},
        )
        assert saved.status == 200
        review = await (await client.get("/panel/draft", headers=owner)).text()
        assert "&lt;抽奖礼物&gt;" in review
        data = {"csrf": owner_csrf, "code": hidden("code", review)}
        published = await client.post(
            "/panel/draft", headers=owner, data=data, allow_redirects=False
        )
        assert published.status == 303
        path = published.headers["Location"]
        number = int(path.rsplit("/", 1)[-1])
        assert f"#{number} <抽奖礼物>" in await service.execute(MEMBER, "列表")
        assert (
            await client.post("/panel/draft", headers=owner, data=data)
        ).status == 409

        match = await (
            await client.get("/panel?q=" + str(number), headers=member)
        ).text()
        assert "&lt;抽奖礼物&gt;" in match and "待同步 1 场" in match
        member_html = await (await client.get(path, headers=member)).text()
        member_csrf = hidden("csrf", member_html)
        assert "导出名单" not in member_html
        assert (
            await client.get(path + "/export/participants", headers=member)
        ).status == 403
        assert (
            await client.post(
                path + "/cancel", headers=member, data={"csrf": member_csrf}
            )
        ).status == 403
        assert (
            await client.post(path + "/join", headers=member, data={"csrf": "wrong"})
        ).status == 403
        assert (
            await client.post(
                path + "/join",
                headers=member,
                data={"csrf": member_csrf},
                allow_redirects=False,
            )
        ).status == 303
        assert "你已报名" in await service.execute(MEMBER, f"报名 {number}")
        joined = await (await client.get("/panel?view=joined", headers=member)).text()
        assert "我的报名序号 1" in joined
        owner_csv = await client.get(path + "/export/participants", headers=owner)
        assert owner_csv.status == 200 and "'=报名人" in await owner_csv.text()
        assert (
            await client.post(path + "/draw", headers=owner, data={"csrf": owner_csrf})
        ).status == 409

        clock.now += 900
        admin_page = await (await client.get(path + "/draw", headers=admin)).text()
        assert "确认开奖" in admin_page
        assert (
            await client.post(
                path + "/draw",
                headers=admin,
                data={"csrf": hidden("csrf", admin_page)},
                allow_redirects=False,
            )
        ).status == 303
        assert "已经开过奖" in await service.execute(OWNER, f"开奖 {number}")
        winners = await (await client.get(path, headers=member)).text()
        assert "开奖结果" in winners and "抽中序号" in winners
        assert "恭喜 =报名人 中奖" in await service.execute(MEMBER, f"结果 {number}")
        assert [row["kind"] for row in store.pending_notices()] == [
            "网页发布",
            "网页开奖",
        ]

        roles[(ADMIN.group, ADMIN.qq)] = Actor(ADMIN.group, ADMIN.qq, ADMIN.name)
        assert (await client.get(path + "/export/audit", headers=admin)).status == 403
        roles.pop((MEMBER.group, MEMBER.qq))
        assert (await client.get("/panel", headers=member)).status == 403
        logout = await client.post(
            "/logout", headers=owner, data={"csrf": owner_csrf}, allow_redirects=False
        )
        assert logout.status == 303
        assert (
            await client.get(path, headers=owner, allow_redirects=False)
        ).status == 403


@pytest.mark.asyncio
async def test_onebot_live_role_and_notice_receipts(raffle):
    service, _ = raffle
    store = service.store
    await service.execute(OWNER, "发起")
    token = store.new_form(OWNER, NOW)
    store.save_form(token, "奖品", 1, NOW + 900, NOW)
    code = store.form(token, NOW)["code"]
    number, *_ = store.confirm_form(OWNER, code, NOW, web=True)
    pending = store.pending_notices()[0]
    bot = SimpleNamespace(
        call_action=AsyncMock(
            side_effect=[
                {
                    "group_id": 123,
                    "user_id": 1001,
                    "nickname": "发起人",
                    "role": "admin",
                },
                {"group_id": 123, "user_id": 7777, "role": "owner"},
                TimeoutError(),
                {"online": False},
                {"online": True},
                {"message_id": 321},
                {"online": True},
            ]
        )
    )
    platform = SimpleNamespace(
        meta=lambda: SimpleNamespace(name="aiocqhttp", id="default"), bot=bot
    )
    service.context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[platform])
    )
    auth = WebAuth(store)
    token = auth.issue(OWNER, NOW, private=True)
    browser = auth.accept_private(token, NOW)
    session = auth.session(browser, NOW)
    verified = await service.web_actor(session)
    assert verified.moderator and verified.qq == OWNER.qq
    assert await service.web_actor(session) is None
    with pytest.raises(QQUnavailable):
        await service.web_actor(session)
    await service.announce(pending)
    assert store.pending_notices()
    await service.announce(pending)
    await service.announce(pending)
    sent = [
        call
        for call in bot.call_action.await_args_list
        if call.args == ("send_group_msg",)
    ]
    assert len(sent) == 1 and sent[0].kwargs["group_id"] == 123
    assert f"抽奖 #{number} 已发起" in sent[0].kwargs["message"][0]["data"]["text"]
    assert [row["kind"] for row in store.unsynced(0)] == ["发起", "QQ 群消息已取得回执"]
