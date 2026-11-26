"""Raffle state and cloud rows committed in the same local transaction."""

import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from random import SystemRandom
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Asia/Shanghai")
PAGE_SIZE = 20
DRAFT_TTL = 15 * 60


def display_time(timestamp):
    return datetime.fromtimestamp(timestamp, ZONE).strftime("%Y-%m-%d %H:%M")


class RaffleError(ValueError):
    pass


def validate_details(title, slots, when, now):
    title = " ".join(title.split())
    if not 1 <= len(title) <= 120:
        raise RaffleError("请输入 1–120 字的奖品描述。")
    if not 1 <= slots <= 999_999_999:
        raise RaffleError("中奖人数必须为正整数。")
    try:
        deadline = int(
            datetime.strptime(when.replace("T", " "), "%Y-%m-%d %H:%M")
            .replace(tzinfo=ZONE)
            .timestamp()
        )
    except (ValueError, OverflowError) as exc:
        raise RaffleError("请填写有效的截止时间（北京时间）。") from exc
    if deadline <= now:
        raise RaffleError("截止时间必须晚于现在。")
    return title, slots, deadline


@dataclass(frozen=True)
class Actor:
    group: str
    qq: str
    name: str
    moderator: bool = False


class Store:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def transaction(self, write=False):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self):
        with self.transaction(write=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS raffles (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, title TEXT NOT NULL,
                owner TEXT NOT NULL, deadline INTEGER NOT NULL, slots INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open', eligible INTEGER, winners TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS entrants (
                raffle INTEGER NOT NULL REFERENCES raffles(id), qq TEXT NOT NULL,
                name TEXT NOT NULL, number INTEGER NOT NULL,
                PRIMARY KEY(raffle, qq), UNIQUE(raffle, number))""")
            db.execute("""CREATE TABLE IF NOT EXISTS reminders (
                raffle INTEGER NOT NULL REFERENCES raffles(id), kind TEXT NOT NULL,
                due INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                PRIMARY KEY(raffle, kind))""")
            db.execute("""CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, raffle INTEGER NOT NULL,
                at INTEGER NOT NULL, kind TEXT NOT NULL, qq TEXT NOT NULL,
                name TEXT NOT NULL, detail TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS form_drafts (
                token_hash TEXT PRIMARY KEY, scope TEXT NOT NULL, qq TEXT NOT NULL,
                code TEXT NOT NULL, expires INTEGER NOT NULL, title TEXT,
                slots INTEGER, deadline INTEGER, UNIQUE(scope, qq))""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS reminders_due ON reminders(status, due)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS raffles_scope ON raffles(scope, status, id)"
            )
            db.execute(
                "INSERT OR IGNORE INTO settings VALUES ('secret', ?)",
                (secrets.token_hex(32),),
            )
            self.secret = bytes.fromhex(
                db.execute("SELECT value FROM settings WHERE key='secret'").fetchone()[
                    0
                ]
            )

    def setting(self, key):
        with self.transaction() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            return row[0] if row else None

    def save_setting(self, key, value):
        with self.transaction(write=True) as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))

    @staticmethod
    def record(db, raffle, at, kind, qq="", name="", detail=None):
        db.execute(
            "INSERT INTO ledger (scope, raffle, at, kind, qq, name, detail) VALUES (?,?,?,?,?,?,?)",
            (
                raffle["scope"],
                raffle["id"],
                at,
                kind,
                qq,
                name,
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )

    @staticmethod
    def get(db, scope, number):
        raffle = db.execute(
            "SELECT * FROM raffles WHERE scope=? AND id=?", (scope, number)
        ).fetchone()
        if raffle is None:
            raise RaffleError("本群找不到这场抽奖。用 /抽奖 列表 查看编号。")
        return raffle

    def create(self, who, title, slots, deadline, now):
        with self.transaction(write=True) as db:
            return self._create(db, who, title, slots, deadline, now)

    def _create(self, db, who, title, slots, deadline, now):
        number = db.execute(
            "INSERT INTO raffles (scope,title,owner,deadline,slots) VALUES (?,?,?,?,?)",
            (who.group, title, who.qq, deadline, slots),
        ).lastrowid
        raffle = self.get(db, who.group, number)
        self.record(
            db,
            raffle,
            now,
            "发起",
            who.qq,
            who.name,
            {"奖品": title, "中奖人数": slots, "截止时间": display_time(deadline)},
        )
        db.execute(
            "INSERT INTO reminders (raffle,kind,due) VALUES (?,?,?)",
            (number, "截止", deadline),
        )
        if deadline - now > 3600:
            db.execute(
                "INSERT INTO reminders (raffle,kind,due) VALUES (?,?,?)",
                (number, "提前一小时", deadline - 3600),
            )
        return number

    def new_form(self, who, now):
        token = secrets.token_urlsafe(32)
        with self.transaction(write=True) as db:
            db.execute(
                "DELETE FROM form_drafts WHERE expires<=? OR (scope=? AND qq=?)",
                (now, who.group, who.qq),
            )
            db.execute(
                "INSERT INTO form_drafts (token_hash,scope,qq,code,expires) VALUES (?,?,?,?,?)",
                (
                    self._form_hash(token),
                    who.group,
                    who.qq,
                    secrets.token_hex(4).upper(),
                    now + DRAFT_TTL,
                ),
            )
        return token

    @staticmethod
    def _form_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def form(self, token, now):
        with self.transaction() as db:
            return db.execute(
                "SELECT code,title,slots,deadline FROM form_drafts WHERE token_hash=? AND expires>?",
                (self._form_hash(token), now),
            ).fetchone()

    def save_form(self, token, title, slots, deadline, now):
        with self.transaction(write=True) as db:
            row = db.execute(
                "SELECT code,title FROM form_drafts WHERE token_hash=? AND expires>?",
                (self._form_hash(token), now),
            ).fetchone()
            if row is None:
                raise RaffleError("表单已失效，请回 QQ 重新发起。")
            if row["title"] is not None:
                return False
            db.execute(
                "UPDATE form_drafts SET title=?,slots=?,deadline=? WHERE token_hash=?",
                (title, slots, deadline, self._form_hash(token)),
            )
            return True

    def confirm_form(self, who, code, now):
        with self.transaction(write=True) as db:
            row = db.execute(
                """SELECT * FROM form_drafts WHERE scope=? AND qq=? AND code=?
                             AND expires>? AND title IS NOT NULL""",
                (who.group, who.qq, code, now),
            ).fetchone()
            if row is None:
                raise RaffleError(
                    "找不到可确认的表单。请由发起人在原群发送表单里的确认指令。"
                )
            if row["deadline"] <= now:
                raise RaffleError("截止时间已过，请重新发起。")
            raffle = self._create(
                db, who, row["title"], row["slots"], row["deadline"], now
            )
            db.execute(
                "DELETE FROM form_drafts WHERE token_hash=?", (row["token_hash"],)
            )
            return raffle, row["title"], row["slots"], row["deadline"]

    def join(self, who, number, now):
        with self.transaction(write=True) as db:
            raffle = self.get(db, who.group, number)
            if raffle["status"] != "open" or now >= raffle["deadline"]:
                raise RaffleError("这场抽奖已停止报名。")
            previous = db.execute(
                "SELECT number FROM entrants WHERE raffle=? AND qq=?", (number, who.qq)
            ).fetchone()
            if previous:
                return previous[0], False
            slot = db.execute(
                "SELECT count(*) + 1 FROM entrants WHERE raffle=?", (number,)
            ).fetchone()[0]
            db.execute(
                "INSERT INTO entrants VALUES (?,?,?,?)",
                (number, who.qq, who.name, slot),
            )
            self.record(db, raffle, now, "报名", who.qq, who.name, {"报名序号": slot})
            return slot, True

    def close(self, who, number, now, *, cancel=False):
        with self.transaction(write=True) as db:
            raffle = self.get(db, who.group, number)
            if who.qq != raffle["owner"] and not who.moderator:
                raise RaffleError("只有该场发起人、群主或群管理员可以开奖和取消。")
            if raffle["status"] == "drawn" and not cancel:
                return False, raffle, json.loads(raffle["winners"])
            if raffle["status"] not in ("open", "closed"):
                raise RaffleError("这场抽奖已经开奖或取消。")
            if cancel:
                db.execute(
                    "UPDATE raffles SET status='cancelled' WHERE id=?", (number,)
                )
                self.record(db, raffle, now, "取消", who.qq, who.name)
                db.execute(
                    "UPDATE reminders SET status='skipped' WHERE raffle=? AND status='pending'",
                    (number,),
                )
                return True, raffle, []
            if now < raffle["deadline"]:
                raise RaffleError(
                    f"报名还未截止，{display_time(raffle['deadline'])} 后可以开奖。"
                )
            entrants = db.execute(
                "SELECT number, qq, name FROM entrants WHERE raffle=? ORDER BY number",
                (number,),
            ).fetchall()
            if not entrants:
                raise RaffleError("本场无人报名，可用 /抽奖 取消 结束。")
            if raffle["status"] == "open":
                self.record(db, raffle, now, "报名截止", detail={"人数": len(entrants)})
            winners = [
                dict(row)
                for row in SystemRandom().sample(
                    entrants, min(raffle["slots"], len(entrants))
                )
            ]
            db.execute(
                "UPDATE raffles SET status='drawn', eligible=?, winners=? WHERE id=?",
                (len(entrants), json.dumps(winners, ensure_ascii=False), number),
            )
            self.record(
                db,
                raffle,
                now,
                "开奖",
                who.qq,
                who.name,
                {
                    "报名总人数": len(entrants),
                    "设置中奖人数": raffle["slots"],
                    "实际中奖人数": len(winners),
                },
            )
            for winner in winners:
                self.record(
                    db,
                    raffle,
                    now,
                    "中奖",
                    winner["qq"],
                    winner["name"],
                    {"抽中序号": winner["number"]},
                )
            return True, self.get(db, who.group, number), winners

    def list_raffles(self, group, page, history):
        statuses = ("drawn", "cancelled") if history else ("open", "closed")
        with self.transaction() as db:
            return db.execute(
                """SELECT r.*, (SELECT count(*) FROM entrants WHERE raffle=r.id) AS people
                FROM raffles r WHERE scope=? AND status IN (?,?) ORDER BY id DESC LIMIT ? OFFSET ?""",
                (group, *statuses, PAGE_SIZE + 1, PAGE_SIZE * (page - 1)),
            ).fetchall()

    def details(self, group, number, page):
        with self.transaction() as db:
            raffle = self.get(db, group, number)
            entries = db.execute(
                "SELECT number, name FROM entrants WHERE raffle=? ORDER BY number LIMIT ? OFFSET ?",
                (number, PAGE_SIZE + 1, PAGE_SIZE * (page - 1)),
            ).fetchall()
            return raffle, entries

    def due(self, now):
        with self.transaction(write=True) as db:
            # Deduplicate reminders even across reloads and worker restarts.
            db.execute(
                """UPDATE reminders SET status='skipped' WHERE status='pending'
                AND kind='提前一小时' AND raffle IN (SELECT id FROM raffles WHERE deadline<=?)""",
                (now,),
            )
            for raffle in db.execute(
                "SELECT * FROM raffles WHERE status='open' AND deadline<=?", (now,)
            ).fetchall():
                count = db.execute(
                    "SELECT count(*) FROM entrants WHERE raffle=?", (raffle["id"],)
                ).fetchone()[0]
                db.execute(
                    "UPDATE raffles SET status='closed' WHERE id=?", (raffle["id"],)
                )
                self.record(db, raffle, now, "报名截止", detail={"人数": count})
            return db.execute(
                """SELECT r.*, m.kind FROM reminders m JOIN raffles r ON r.id=m.raffle
                WHERE m.status='pending' AND m.due<=? AND r.status<>'cancelled'
                ORDER BY m.due""",
                (now,),
            ).fetchall()

    def claim(self, raffle, now):
        with self.transaction(write=True) as db:
            result = db.execute(
                """UPDATE reminders SET status='attempted' WHERE raffle=? AND kind=?
                AND status='pending' AND EXISTS (SELECT 1 FROM raffles WHERE id=? AND status<>'cancelled')""",
                (raffle["id"], raffle["kind"], raffle["id"]),
            )
            if result.rowcount:
                self.record(
                    db,
                    raffle,
                    now,
                    "提醒发起",
                    raffle["owner"],
                    detail={"类型": raffle["kind"]},
                )
            return result.rowcount == 1

    def finish_reminder(self, raffle, now, confirmed):
        with self.transaction(write=True) as db:
            status = "sent" if confirmed else "unconfirmed"
            db.execute(
                "UPDATE reminders SET status=? WHERE raffle=? AND kind=?",
                (status, raffle["id"], raffle["kind"]),
            )
            self.record(
                db,
                raffle,
                now,
                "提醒已发送" if confirmed else "提醒发送状态未确认",
                raffle["owner"],
                detail={"类型": raffle["kind"]},
            )

    def unsynced(self, after, group=None):
        with self.transaction() as db:
            if group is not None:
                return db.execute(
                    "SELECT * FROM ledger WHERE id>? AND scope LIKE ? ORDER BY id LIMIT 30",
                    (after, f"%:GroupMessage:{group}"),
                ).fetchall()
            return db.execute(
                "SELECT * FROM ledger WHERE id>? ORDER BY id LIMIT 30", (after,)
            ).fetchall()

    def anonymous(self, value):
        return (
            hmac.new(self.secret, value.encode(), hashlib.sha256).hexdigest()[:16]
            if value
            else ""
        )
