"""Read-only historical snapshots and live group totals."""

import json
import re
import unicodedata
from collections import Counter, defaultdict

from .store import PAGE_SIZE, display_time
from .web_records import status_of

RANK_LIMIT = 10


def name_key(name):
    return " ".join(unicodedata.normalize("NFKC", name).split()).casefold()


def public_name(name):
    return re.sub(r"(?<!\d)\d{5,}(?!\d)", "•••", " ".join(name.split())) or "群友"


def ranked(counts, names):
    order = sorted(counts, key=lambda key: (-counts[key], name_key(names[key]), key))
    rank, previous = 0, None
    rows = []
    for position, key in enumerate(order[:RANK_LIMIT], 1):
        count = counts[key]
        if count != previous:
            rank, previous = position, count
        rows.append({"rank": rank, "name": public_name(names[key]), "count": count})
    return rows


def leaderboards(live, archive):
    names = {}
    aliases = defaultdict(set)
    entered, awarded = Counter(), Counter()

    # Live rows are newest first. Keep each QQ identity separate and use its
    # latest recorded display name. Only a unique alias can join an old entry.
    for entrants, winners in live:
        for person in entrants:
            identity = ("qq", person["qq"])
            names.setdefault(identity, person["name"])
            alias = name_key(person["name"])
            if alias and alias != "群友":
                aliases[alias].add(identity)
            entered[identity] += 1
        for person in winners:
            awarded[("qq", person["qq"])] += 1

    def history_identity(name):
        alias = name_key(name)
        if len(aliases.get(alias, ())) == 1:
            return next(iter(aliases[alias]))
        identity = ("name", alias)
        names.setdefault(identity, name)
        return identity

    for entrants, winners in archive:
        entered.update({history_identity(name) for name in entrants if name_key(name)})
        awarded.update({history_identity(name) for name in winners if name_key(name)})

    return {"entered": ranked(entered, names), "awarded": ranked(awarded, names)}


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
                      (SELECT json_group_array(json_object('qq',e.qq,'name',e.name))
                       FROM entrants e WHERE e.raffle=r.id) AS entrants
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
    live_people = []
    archive_people = []
    for row in live:
        state = status_of(row, now)
        entrants = json.loads(row["entrants"])
        winners = json.loads(row["winners"] or "[]")
        live_people.append((entrants, winners))
        stats["people"] += len(entrants)
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
                    "people": len(entrants),
                    "winners": [winner["name"] for winner in winners],
                }
            )
    for row in archive:
        winners = json.loads(row["winners"])
        entrants = json.loads(row["entrants"])
        archive_people.append((entrants, winners))
        people = len(entrants)
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
    return (
        stats,
        details[start : start + PAGE_SIZE + 1],
        leaderboards(live_people, archive_people),
    )
