"""One-use, bot-issued links and sessions scoped to the requesting QQ member."""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

from .store import RaffleError

COOKIE = "__Host-raffle-access"
INVITE_TTL = 5 * 60
SESSION_TTL = 30 * 60
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
CODE = re.compile(r"([0-9a-f]{32})-([0-9a-f]{32})-([0-9a-f]{24})")


@dataclass(frozen=True)
class Session:
    group: str
    qq: str


class WebAuth:
    def __init__(self, store):
        self.store = store

    def sign(self, purpose, value):
        return hmac.new(
            self.store.secret, f"raffle-web:{purpose}:{value}".encode(), hashlib.sha256
        ).hexdigest()

    @staticmethod
    def key(token):
        return hashlib.sha256(token.encode()).hexdigest()[:32]

    def issue(self, actor, now, *, private, replace=True):
        token = secrets.token_urlsafe(32)
        with self.store.transaction(write=True) as db:
            db.execute("DELETE FROM web_grants WHERE ends<=?", (now,))
            db.execute("DELETE FROM web_sessions WHERE ends<=?", (now,))
            if replace:
                db.execute(
                    "DELETE FROM web_grants WHERE scope=? AND qq=?",
                    (actor.group, actor.qq),
                )
            db.execute(
                "INSERT INTO web_grants VALUES (?,?,?,?,?)",
                (self.key(token), actor.group, actor.qq, now + INVITE_TTL, private),
            )
        return token

    def grant(self, token, now):
        if not TOKEN.fullmatch(token):
            return None
        with self.store.transaction() as db:
            return db.execute(
                "SELECT * FROM web_grants WHERE grant_key=? AND ends>?",
                (self.key(token), now),
            ).fetchone()

    def shared_challenge(self, token):
        browser = secrets.token_urlsafe(32)
        prefix = f"{self.key(token)}-{self.key(browser)}"
        return browser, f"{prefix}-{self.sign('shared', prefix)[:24]}"

    def accept_private(self, token, now):
        if not TOKEN.fullmatch(token):
            return None
        browser = secrets.token_urlsafe(32)
        with self.store.transaction(write=True) as db:
            grant = db.execute(
                "SELECT * FROM web_grants WHERE grant_key=? AND private=1 AND ends>?",
                (self.key(token), now),
            ).fetchone()
            if grant is None:
                return None
            self.activate(db, grant, self.key(browser), now)
        return browser

    def approve_shared(self, actor, code, now):
        match = CODE.fullmatch(code.lower())
        if match is None or not hmac.compare_digest(
            self.sign("shared", f"{match[1]}-{match[2]}")[:24], match[3]
        ):
            raise RaffleError("请复制机器人发来的网页上的访问确认指令。")
        with self.store.transaction(write=True) as db:
            grant = db.execute(
                """SELECT * FROM web_grants WHERE grant_key=? AND private=0
                   AND scope=? AND qq=? AND ends>?""",
                (match[1], actor.group, actor.qq, now),
            ).fetchone()
            if grant is None:
                raise RaffleError(
                    "只有在原群领取链接的 QQ 号才能确认，链接须在有效期内使用一次。"
                )
            self.activate(db, grant, match[2], now)

    @staticmethod
    def activate(db, grant, browser_key, now):
        db.execute(
            """INSERT INTO web_sessions (browser_key,scope,qq,ends,challenge_ends)
               VALUES (?,?,?,?,?)""",
            (browser_key, grant["scope"], grant["qq"], now + SESSION_TTL, 0),
        )
        db.execute(
            "DELETE FROM web_grants WHERE scope=? AND qq=?",
            (grant["scope"], grant["qq"]),
        )

    def session(self, browser, now):
        if not TOKEN.fullmatch(browser):
            return None
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT scope,qq FROM web_sessions WHERE browser_key=? AND ends>?",
                (self.key(browser), now),
            ).fetchone()
            return Session(*row) if row else None

    def logout(self, browser):
        if TOKEN.fullmatch(browser):
            with self.store.transaction(write=True) as db:
                db.execute(
                    "DELETE FROM web_sessions WHERE browser_key=?", (self.key(browser),)
                )
