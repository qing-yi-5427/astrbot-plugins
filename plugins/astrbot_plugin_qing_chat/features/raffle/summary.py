"""Read-only historical snapshots and live group totals."""

import json

from .store import PAGE_SIZE, display_time
from .web_records import status_of


def import_history(store, group, sources, exclusions=()):
    """Apply a verified snapshot for each supplied sheet in one transaction."""
    with store.transaction(write=True) as db:
        for source, rows in sources.items():
            db.execute(
                "DELETE FROM raffle_archive WHERE scope=? AND source=?", (group, source)
            )
            db.executemany(
                """INSERT INTO raffle_archive VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    (
                        group,
                        source,
                        row["row"],
                        row["title"],
                        row["organizer"],
                        row["deadline"],
                        json.dumps(row["entrants"], ensure_ascii=False),
                        json.dumps(row["winners"], ensure_ascii=False),
                    )
                    for row in rows
                ),
            )
        db.executemany(
            """INSERT OR IGNORE INTO raffle_summary_exclusions
               SELECT scope, id FROM raffles WHERE scope=? AND id=?""",
            ((group, number) for number in exclusions),
        )


def group_summary(store, group, now, *, page=None, source=""):
    with store.transaction() as db:
        live = db.execute(
            """SELECT r.id, r.title, r.status, r.deadline, r.winners,
                      (SELECT count(*) FROM entrants e WHERE e.raffle=r.id) AS people
               FROM raffles r WHERE r.scope=? AND NOT EXISTS
                   (SELECT 1 FROM raffle_summary_exclusions x
                    WHERE x.scope=r.scope AND x.raffle=r.id)
               ORDER BY r.id DESC""",
            (group,),
        ).fetchall()
        archive = db.execute(
            """SELECT source, source_row, title, organizer, deadline, entrants, winners
               FROM raffle_archive WHERE scope=? ORDER BY source_row DESC, source""",
            (group,),
        ).fetchall()

    stats = {
        "live": len(live),
        "archive": len(archive),
        "total": len(live) + len(archive),
        "people": 0,
        "with_results": 0,
        "winners": 0,
    }
    details = []
    for row in live:
        state = status_of(row, now)
        winners = [winner["name"] for winner in json.loads(row["winners"] or "[]")]
        stats["people"] += row["people"]
        stats["with_results"] += state == "drawn"
        stats["winners"] += len(winners)
        if page is not None and source != "archive":
            details.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "source": "机器人",
                    "state": state,
                    "deadline": display_time(row["deadline"]),
                    "people": row["people"],
                    "winners": winners,
                }
            )
    for row in archive:
        winners = json.loads(row["winners"])
        people = len(json.loads(row["entrants"]))
        stats["people"] += people
        stats["with_results"] += bool(winners)
        stats["winners"] += len(winners)
        if page is not None and source != "live":
            details.append(
                {
                    "id": None,
                    "title": row["title"],
                    "source": row["source"],
                    "organizer": row["organizer"],
                    "source_row": row["source_row"],
                    "deadline": row["deadline"],
                    "people": people,
                    "winners": winners,
                }
            )
    start = ((page or 1) - 1) * PAGE_SIZE
    return stats, details[start : start + PAGE_SIZE + 1]
