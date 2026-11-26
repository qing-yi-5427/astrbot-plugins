"""Group-scoped views over the existing raffle and replication records."""

import json
from dataclasses import dataclass

from .store import PAGE_SIZE

REMINDERS = {
    "pending": "待发送",
    "attempted": "已尝试",
    "sent": "已取得发送回执",
    "skipped": "已跳过",
    "unconfirmed": "发送状态未确认",
}


@dataclass(frozen=True)
class RecordFilters:
    query: str = ""
    status: str = ""
    view: str = ""
    page: int = 1


def overview(store, actor, filters, now):
    conditions = ["r.scope=?"]
    params = [actor.group]
    if filters.query:
        number = filters.query.lstrip("#")
        if number.isascii() and number.isdecimal() and len(number) <= 9:
            conditions.append("(r.id=? OR instr(r.title,?)>0)")
            params.extend((int(number), filters.query))
        else:
            conditions.append("instr(r.title,?)>0")
            params.append(filters.query)
    if filters.view == "mine":
        conditions.append("r.owner=?")
        params.append(actor.qq)
    elif filters.view == "joined":
        conditions.append("e.qq IS NOT NULL")
    where = " AND ".join(conditions)
    with store.transaction() as db:
        rows = db.execute(
            f"""WITH visible AS (
                SELECT r.id,r.title,r.slots,r.deadline,r.owner,
                  CASE WHEN r.status='open' AND r.deadline<=? THEN 'closed'
                       ELSE r.status END AS state,
                  e.number AS my_number
                FROM raffles r
                LEFT JOIN entrants e ON e.raffle=r.id AND e.qq=?
                WHERE {where}
            )
            SELECT v.*, (SELECT count(*) FROM entrants WHERE raffle=v.id) AS people
            FROM visible v WHERE (?='' OR v.state=?)
            ORDER BY v.id DESC LIMIT ? OFFSET ?""",
            (
                now,
                actor.qq,
                *params,
                filters.status,
                filters.status,
                PAGE_SIZE + 1,
                PAGE_SIZE * (filters.page - 1),
            ),
        ).fetchall()
        counts = dict(
            db.execute(
                """SELECT CASE WHEN status='open' AND deadline<=? THEN 'closed'
                           ELSE status END AS state, count(*)
                   FROM raffles WHERE scope=? GROUP BY state""",
                (now, actor.group),
            )
        )
        return rows, counts


def status_of(raffle, now):
    if raffle["status"] == "open" and raffle["deadline"] <= now:
        return "closed"
    return raffle["status"]


def detail(store, actor, number, page):
    with store.transaction() as db:
        raffle = store.get(db, actor.group, number)
        own = db.execute(
            "SELECT number FROM entrants WHERE raffle=? AND qq=?", (number, actor.qq)
        ).fetchone()
        total = db.execute(
            "SELECT count(*) FROM entrants WHERE raffle=?", (number,)
        ).fetchone()[0]
        entries = audit = reminders = ()
        if actor.can_manage(raffle):
            entries = db.execute(
                """SELECT number,name FROM entrants WHERE raffle=? ORDER BY number
                   LIMIT ? OFFSET ?""",
                (number, PAGE_SIZE + 1, PAGE_SIZE * (page - 1)),
            ).fetchall()
            audit = db.execute(
                """SELECT at,kind,name,detail FROM ledger
                   WHERE raffle=? AND scope=? ORDER BY id DESC LIMIT ? OFFSET ?""",
                (number, actor.group, PAGE_SIZE + 1, PAGE_SIZE * (page - 1)),
            ).fetchall()
            reminders = db.execute(
                """SELECT kind,due,status FROM reminders WHERE raffle=?
                   UNION ALL SELECT kind,due,status FROM web_notices WHERE raffle=? ORDER BY due""",
                (number, number),
            ).fetchall()
        winners = json.loads(raffle["winners"] or "[]")
        return (
            raffle,
            own[0] if own else None,
            total,
            winners,
            entries,
            audit,
            reminders,
        )


def export_records(store, actor, number, kind):
    with store.transaction() as db:
        raffle = store.get(db, actor.group, number)
        if not actor.can_manage(raffle):
            return None
        if kind == "participants":
            winners = {r["number"] for r in json.loads(raffle["winners"] or "[]")}
            return ("序号", "昵称", "中奖"), [
                (row["number"], row["name"], "是" if row["number"] in winners else "")
                for row in db.execute(
                    "SELECT number,name FROM entrants WHERE raffle=? ORDER BY number",
                    (number,),
                )
            ]
        return ("时间（北京时间）", "事件", "昵称", "详情"), db.execute(
            """SELECT at,kind,name,detail FROM ledger
               WHERE raffle=? AND scope=? ORDER BY id""",
            (number, actor.group),
        ).fetchall()


def sync_progress(store, replica):
    if replica is None:
        return 0, 0
    group, file_id = replica
    key = f"tencent:group:{group}:{file_id}:position"
    with store.transaction() as db:
        saved = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        last_id, written = json.loads(saved[0]) if saved else (0, 0)
        pending = db.execute(
            "SELECT count(*) FROM ledger WHERE scope LIKE ? AND id>?",
            (f"%:GroupMessage:{group}", last_id),
        ).fetchone()[0]
        return written, pending


def audit_detail(value):
    return "，".join(f"{key} {item}" for key, item in json.loads(value).items())
