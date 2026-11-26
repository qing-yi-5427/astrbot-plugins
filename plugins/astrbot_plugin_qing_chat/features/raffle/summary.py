"""Read-only historical snapshots and live group totals."""

import json
import re
import unicodedata
from collections import Counter, defaultdict

from .store import PAGE_SIZE, RaffleError, display_time
from .web_auth import WebAuth
from .web_records import status_of

RANK_LIMIT = 10


def name_key(name):
    return " ".join(unicodedata.normalize("NFKC", name).split()).casefold()


def public_name(name):
    return re.sub(r"(?<!\d)\d{5,}(?!\d)", "•••", " ".join(name.split())) or "群友"


def ranked(counts, names, limit):
    order = sorted(counts, key=lambda key: (-counts[key], name_key(names[key]), key))
    rank, previous = 0, None
    rows = []
    for position, key in enumerate(order[:limit], 1):
        count = counts[key]
        if count != previous:
            rank, previous = position, count
        rows.append({"rank": rank, "name": public_name(names[key]), "count": count})
    return rows


def nickname_root(links, key):
    while key in links:
        key = links[key]
    return key


def live_aliases(live, links):
    aliases = defaultdict(set)
    for entrants, _ in live:
        for person in entrants:
            key = name_key(person["name"])
            if key and key != "群友":
                aliases[nickname_root(links, key)].add(("qq", person["qq"]))
    return aliases


def leaderboards(live, archive, *, limit=RANK_LIMIT, search="", merges=()):
    names = {}
    links = {rule["old_key"]: rule["target_key"] for rule in merges}
    labels = {rule["target_key"]: rule["target_name"] for rule in merges}
    aliases = live_aliases(live, links)
    entered, awarded = Counter(), Counter()
    term = name_key(search)
    matched = {}

    def match(identity, name):
        key = name_key(public_name(name))
        if term in key:
            matched[identity] = matched.get(identity, False) or term == key

    # Live rows are newest first. Keep each QQ identity separate and use its
    # latest recorded display name. Only a unique alias can join an old entry.
    for entrants, winners in live:
        for person in entrants:
            identity = ("qq", person["qq"])
            names.setdefault(identity, person["name"])
            if term:
                match(identity, person["name"])
            entered[identity] += 1
        for person in winners:
            awarded[("qq", person["qq"])] += 1

    def history_identity(name):
        alias = nickname_root(links, name_key(name))
        if len(aliases.get(alias, ())) == 1:
            identity = next(iter(aliases[alias]))
        else:
            identity = ("name", alias)
        names.setdefault(identity, labels.get(alias, name))
        if term:
            match(identity, name)
            match(identity, names[identity])
        return identity

    for entrants, winners in archive:
        entered.update({history_identity(name) for name in entrants if name_key(name)})
        awarded.update({history_identity(name) for name in winners if name_key(name)})

    matches = sorted(
        matched,
        key=lambda key: (
            not matched[key],
            -entered[key],
            -awarded[key],
            name_key(names[key]),
            key,
        ),
    )
    return {
        "entered": ranked(entered, names, limit),
        "awarded": ranked(awarded, names, limit),
        "matches": [
            {
                "name": public_name(names[key]),
                "entered": entered[key],
                "awarded": awarded[key],
            }
            for key in matches
        ],
    }


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


def summary_records(db, group):
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
    return live, archive


def stored_merges(db, group):
    return db.execute(
        "SELECT * FROM raffle_nickname_merges WHERE scope=? ORDER BY id DESC",
        (group,),
    ).fetchall()


def nickname_admin_data(store, group):
    with store.transaction() as db:
        merges = stored_merges(db, group)
        live, archive = summary_records(db, group)
    archived, known = nickname_names(live, archive)
    used = {row["old_key"] for row in merges}
    tokens = nickname_tokens(store, group, known)
    counts = Counter(public_name(name) for name, _ in known.values())

    def option(key):
        name, location = known[key]
        label = public_name(name)
        if counts[label] > 1:
            label = f"{label}（{location}）"
        return {"id": tokens[key], "label": label}

    return {
        "rules": [
            {
                "id": row["id"],
                "old": public_name(row["old_name"]),
                "target": public_name(row["target_name"]),
            }
            for row in merges
        ],
        "old_options": [option(key) for key in sorted(archived.keys() - used)],
        "target_options": [option(key) for key in sorted(known)],
    }


def nickname_names(live, archive):
    archived = {}
    for record in archive:
        names = json.loads(record["entrants"]) + json.loads(record["winners"])
        for position, name in enumerate(names, 1):
            key = name_key(name)
            if key:
                archived.setdefault(
                    key,
                    (
                        name,
                        f"{record['source']} 第 {record['source_row']} 行第 {position} 项",
                    ),
                )
    known = dict(archived)
    for record in live:
        for position, person in enumerate(json.loads(record["entrants"]), 1):
            key = name_key(person["name"])
            if key and key != "群友":
                known.setdefault(
                    key, (person["name"], f"抽奖 #{record['id']} 第 {position} 项")
                )
    return archived, known


def nickname_tokens(store, group, names):
    auth = WebAuth(store)
    return {
        key: auth.sign("nickname-choice", json.dumps([group, key]))[:32]
        for key in names
    }


def merge_nickname(store, group, old, target, *, selected=False):
    merge_nicknames(store, group, [old], target, selected=selected)


def merge_nicknames(store, group, old_names, target, *, selected=False):
    with store.transaction(write=True) as db:
        live, archive = summary_records(db, group)
        archived, known = nickname_names(live, archive)
        if selected:
            choices = {
                token: key
                for key, token in nickname_tokens(store, group, known).items()
            }
            old_keys = {choices.get(name) for name in old_names}
            target_key = choices.get(target)
            if None in old_keys or target_key is None:
                raise RaffleError("下拉选项已更新，请重新选择昵称。")
        else:
            old_keys = {name_key(name) for name in old_names}
            target_key = name_key(target)
        if not old_keys or "" in old_keys or not target_key or target_key in old_keys:
            raise RaffleError("请选择至少一个旧昵称，并确保归入昵称与它们不同。")
        if target_key not in known:
            raise RaffleError("找不到归入昵称，请从候选列表重新选择。")

        links = {row["old_key"]: row["target_key"] for row in stored_merges(db, group)}
        root = nickname_root(links, target_key)
        pending = []
        for old_key in sorted(old_keys):
            if links.get(old_key) == target_key:
                continue
            if old_key in links:
                raise RaffleError("有旧昵称已有合并，请先撤销对应的原合并。")
            if root == old_key:
                raise RaffleError("有旧昵称与归入昵称指向同一条合并链。")
            if old_key not in archived:
                raise RaffleError("有旧昵称在旧表的参与或中奖记录中找不到。")
            pending.append(old_key)
        if not pending:
            return
        links.update(dict.fromkeys(pending, target_key))
        live_people = [(json.loads(row["entrants"]), ()) for row in live]
        if len(live_aliases(live_people, links).get(root, ())) > 1:
            raise RaffleError("这些昵称对应多个 QQ 账号，无法合并。")
        db.executemany(
            """INSERT INTO raffle_nickname_merges
               (scope, old_key, old_name, target_key, target_name) VALUES (?, ?, ?, ?, ?)""",
            (
                (group, old_key, archived[old_key][0], target_key, known[target_key][0])
                for old_key in pending
            ),
        )


def undo_nickname_merge(store, group, number):
    with store.transaction(write=True) as db:
        deleted = db.execute(
            "DELETE FROM raffle_nickname_merges WHERE scope=? AND id=?", (group, number)
        )
        if not deleted.rowcount:
            raise RaffleError("找不到这条合并记录。")


def group_summary(store, group, now, *, page=None, source="", rank_query=""):
    with store.transaction() as db:
        live, archive = summary_records(db, group)
        merges = stored_merges(db, group)

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
        leaderboards(
            live_people,
            archive_people,
            limit=RANK_LIMIT if page is None else None,
            search=rank_query,
            merges=merges,
        ),
    )
