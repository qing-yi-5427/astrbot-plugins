"""Pair a browser with the QQ member who verifies its one-time code."""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

from .store import RaffleError

COOKIE = "__Host-raffle"
LOGIN_TTL = 5 * 60
SESSION_TTL = 8 * 60 * 60
BROWSER = re.compile(r"([0-9a-f]{8})\.([A-Za-z0-9_-]{43})")
CODE = re.compile(r"([0-9a-f]{8})-([0-9a-f]{24})-([0-9a-f]{24})")


@dataclass(frozen=True)
class Session:
    group: str
    qq: str


class WebAuth:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def start(now):
        return f"{now + LOGIN_TTL:x}.{secrets.token_urlsafe(32)}"

    def sign(self, purpose, value):
        return hmac.new(
            self.store.secret, f"raffle-web:{purpose}:{value}".encode(), hashlib.sha256
        ).hexdigest()

    def challenge(self, browser, now):
        match = BROWSER.fullmatch(browser)
        if match is None or not now < int(match[1], 16) <= now + LOGIN_TTL:
            return None
        prefix = f"{match[1]}-{self.key(browser)}"
        return f"{prefix}-{self.sign('login', prefix)[:24]}"

    @staticmethod
    def key(browser):
        return hashlib.sha256(browser.encode()).hexdigest()[:24]

    def approve(self, actor, code, now):
        match = CODE.fullmatch(code.lower())
        if match is None:
            raise RaffleError("请复制你自己在抽奖网页上看到的登录指令。")
        expires = int(match[1], 16)
        expected = self.sign("login", f"{match[1]}-{match[2]}")[:24]
        if not now < expires <= now + LOGIN_TTL or not hmac.compare_digest(
            expected, match[3]
        ):
            raise RaffleError("登录指令无效或已过期，请在网页重新生成。")
        with self.store.transaction(write=True) as db:
            db.execute(
                "DELETE FROM web_sessions WHERE ends<=? AND challenge_ends<=?",
                (now, now),
            )
            result = db.execute(
                """INSERT OR IGNORE INTO web_sessions
                (browser_key,scope,qq,ends,challenge_ends) VALUES (?,?,?,?,?)""",
                (match[2], actor.group, actor.qq, now + SESSION_TTL, expires),
            )
            if not result.rowcount:
                raise RaffleError("这条登录指令已使用，请返回原浏览器或重新生成。")

    def session(self, browser, now):
        if not BROWSER.fullmatch(browser):
            return None
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT scope,qq FROM web_sessions WHERE browser_key=? AND ends>?",
                (self.key(browser), now),
            ).fetchone()
            return Session(*row) if row else None

    def logout(self, browser):
        # Keep the consumed challenge until it expires to prevent re-approval.
        if BROWSER.fullmatch(browser):
            with self.store.transaction(write=True) as db:
                db.execute(
                    "UPDATE web_sessions SET ends=0 WHERE browser_key=?",
                    (self.key(browser),),
                )
