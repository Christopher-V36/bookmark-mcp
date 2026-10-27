"""Triage: label saved posts by how useful they are for trying, building and learning.

The labelling itself is done by the AI client (Claude) through MCP: it asks for a
batch of unlabelled posts, reads them, and saves labels back. No extra AI API or
cost is involved. Labels then power ``next_up`` ("what should I try next?") and
label filters on search/browse.
"""

from __future__ import annotations

from bookmark_mcp.db import Database, normalize_tag, now

INTENTS: dict[str, str] = {
    "try": "a tool, library, model, app or feature you can install or use hands-on now",
    "build": "a project idea, template, starter kit or something worth building or cloning",
    "learn": "a tutorial, guide, course, explainer thread, paper or talk that teaches a skill",
    "reference": "docs, cheat sheets, resource lists or prompt libraries to keep for later",
    "inspiration": "design, UI, demos or ideas that inspire but are not directly actionable",
    "news": "an announcement or update with no clear action for you",
    "noise": "memes, jokes, hot takes or engagement bait: nothing to act on",
}
USEFULNESS: dict[int, str] = {
    0: "skip: nothing to gain",
    1: "mildly interesting, low priority",
    2: "useful: worth trying, building or studying",
    3: "must do: high value, act on it soon",
}
EFFORTS: dict[str, str] = {
    "quick": "under 1 hour",
    "session": "an afternoon (1-4 hours)",
    "project": "several days",
}
STATUSES = ("todo", "doing", "done", "skipped")
INTENT_PRIORITY = ("try", "build", "learn", "reference", "inspiration", "news", "noise")
MAX_TOPICS = 5
MAX_TEXT = 300


def rubric() -> dict:
    return {
        "goal": "help the user try new technologies, create projects and learn things",
        "intent": INTENTS,
        "usefulness": USEFULNESS,
        "effort": EFFORTS,
        "topics": f"1-{MAX_TOPICS} short lowercase tags, e.g. claude-code, mcp, react, ai-agents",
        "summary": "one line: what the post is",
        "action": "one imperative line: what the user should do with it (empty for noise/news)",
    }


def _remaining(db: Database, collection: str | None) -> int:
    sql = (
        "SELECT COUNT(DISTINCT c.post_id) AS n FROM collections c "
        "WHERE NOT EXISTS (SELECT 1 FROM triage t WHERE t.post_id=c.post_id)"
    )
    params: tuple = ()
    if collection:
        sql += " AND c.collection=?"
        params = (collection,)
    return db._one(sql, params)["n"]


def _compact(db: Database, post_id: str) -> dict:
    post = db.get_post(post_id)
    media = db.get_media(post_id)
    item: dict = {
        "id": post_id,
        "author": f"@{post['author_handle']}" if post.get("author_handle") else None,
        "date": (post.get("created_at") or "")[:10],
        "text": post["text"][:700] + ("..." if len(post["text"]) > 700 else ""),
    }
    links = [
        {"url": link["url"], "title": link.get("title")}
        for link in db.get_links(post_id)
        if link.get("url")
    ]
    if links:
        item["links"] = links[:5]
    if post.get("article_title"):
        item["article"] = {
            "title": post["article_title"],
            "start": (post.get("article_text") or "")[:400],
        }
    if media:
        item["images"] = len(media)
        kinds = sorted({m["kind"] for m in media})
        item["media_kinds"] = kinds
        alts = [m["alt_text"] for m in media if m.get("alt_text")]
        if alts:
            item["image_descriptions"] = alts
    if post.get("quoted_post_id"):
        quoted = db.get_post(post["quoted_post_id"])
        if quoted:
            item["quotes"] = {
                "author": f"@{quoted['author_handle']}" if quoted.get("author_handle") else None,
                "text": quoted["text"][:300],
            }
    return item


def batch(db: Database, limit: int = 25, collection: str | None = None) -> dict:
    """Unlabelled saved posts, most recently saved first, in a compact form."""
    limit = max(1, min(limit, 50))
    sql = (
        "SELECT c.post_id, MAX(CAST(c.sort_index AS INTEGER)) AS idx, MAX(c.added_at) AS added "
        "FROM collections c WHERE NOT EXISTS (SELECT 1 FROM triage t WHERE t.post_id=c.post_id)"
    )
    params: list = []
    if collection:
        sql += " AND c.collection=?"
        params.append(collection)
    sql += " GROUP BY c.post_id ORDER BY idx IS NULL, idx DESC, added DESC LIMIT ?"
    rows = db._rows(sql, [*params, limit])
    remaining = _remaining(db, collection)
    return {
        "posts": [_compact(db, row["post_id"]) for row in rows],
        "returned": len(rows),
        "remaining_including_these": remaining,
        "labels": {
            "intent": list(INTENTS),
            "usefulness": "0-3",
            "effort": list(EFFORTS),
        },
        "next_step": (
            "Label every post with label_posts, then call get_triage_batch again until "
            "remaining is 0. Use get_post_images only when the text alone is not enough."
            if rows
            else "All saved posts are labelled. Use next_up to pick what to do."
        ),
    }


def _validate(item: dict) -> tuple[dict | None, str | None]:
    if not isinstance(item, dict):
        return None, "each label must be an object"
    post_id = str(item.get("post_id") or item.get("id") or "").strip()
    if not post_id:
        return None, "missing post_id"
    intent = str(item.get("intent") or "").strip().lower()
    if intent not in INTENTS:
        return None, f"{post_id}: intent must be one of {', '.join(INTENTS)}"
    try:
        usefulness = int(item.get("usefulness"))
    except (TypeError, ValueError):
        return None, f"{post_id}: usefulness must be 0, 1, 2 or 3"
    if usefulness not in USEFULNESS:
        return None, f"{post_id}: usefulness must be 0, 1, 2 or 3"
    effort = item.get("effort")
    effort = str(effort).strip().lower() if effort else None
    if effort is not None and effort not in EFFORTS:
        return None, f"{post_id}: effort must be one of {', '.join(EFFORTS)} or empty"
    topics_raw = item.get("topics") or []
    if isinstance(topics_raw, str):
        topics_raw = [t for t in topics_raw.replace(";", ",").split(",")]
    topics = list(dict.fromkeys(t for t in (normalize_tag(str(x)) for x in topics_raw) if t))
    return {
        "post_id": post_id,
        "intent": intent,
        "usefulness": usefulness,
        "effort": effort,
        "topics": topics[:MAX_TOPICS],
        "summary": (str(item.get("summary") or "").strip()[:MAX_TEXT]) or None,
        "action": (str(item.get("action") or "").strip()[:MAX_TEXT]) or None,
    }, None


def save_labels(db: Database, items: list, labeled_by: str | None = None) -> dict:
    saved, errors = 0, []
    for raw in (items or [])[:100]:
        label, error = _validate(raw)
        if error:
            errors.append(error)
            continue
        if db.get_post(label["post_id"]) is None:
            errors.append(f"{label['post_id']}: no saved post with this id")
            continue
        stamp = now()
        with db._write_lock, db.conn:
            db.conn.execute(
                """INSERT INTO triage (post_id, intent, usefulness, effort, summary, action,
                       labeled_by, labeled_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(post_id) DO UPDATE SET
                       intent=excluded.intent, usefulness=excluded.usefulness,
                       effort=excluded.effort, summary=excluded.summary,
                       action=excluded.action, labeled_by=excluded.labeled_by,
                       labeled_at=excluded.labeled_at""",
                (
                    label["post_id"],
                    label["intent"],
                    label["usefulness"],
                    label["effort"],
                    label["summary"],
                    label["action"],
                    labeled_by,
                    stamp,
                ),
            )
            db.conn.execute("DELETE FROM triage_topics WHERE post_id=?", (label["post_id"],))
            db.conn.executemany(
                "INSERT INTO triage_topics (post_id, topic) VALUES (?, ?)",
                [(label["post_id"], topic) for topic in label["topics"]],
            )
        db.refresh_fts(label["post_id"])
        saved += 1
    return {"saved": saved, "errors": errors, "remaining": _remaining(db, None)}


def get_labels(db: Database, post_id: str) -> dict | None:
    row = db._one(
        "SELECT intent, usefulness, effort, summary, action, status, labeled_at "
        "FROM triage WHERE post_id=?",
        (post_id,),
    )
    if row is None:
        return None
    row["topics"] = [
        r["topic"]
        for r in db._rows(
            "SELECT topic FROM triage_topics WHERE post_id=? ORDER BY topic", (post_id,)
        )
    ]
    return row


def set_status(db: Database, post_id: str, status: str) -> dict:
    status = status.strip().lower()
    if status not in STATUSES:
        return {"ok": False, "error": f"status must be one of {', '.join(STATUSES)}"}
    with db._write_lock, db.conn:
        cur = db.conn.execute(
            "UPDATE triage SET status=?, status_at=? WHERE post_id=?", (status, now(), post_id)
        )
    if cur.rowcount == 0:
        return {"ok": False, "error": f"{post_id} has no labels yet; label it first"}
    return {"ok": True, "post_id": post_id, "status": status}


def next_up(
    db: Database,
    intent: str | None = None,
    effort: str | None = None,
    topic: str | None = None,
    min_usefulness: int = 2,
    include_done: bool = False,
    limit: int = 10,
) -> dict:
    """The most valuable saved posts to act on, best first."""
    where = ["t.usefulness >= ?"]
    params: list = [max(0, min(int(min_usefulness), 3))]
    if not include_done:
        where.append("t.status IN ('todo', 'doing')")
    if intent:
        where.append("t.intent = ?")
        params.append(intent.lower())
    else:
        where.append("t.intent IN ('try', 'build', 'learn', 'reference')")
    if effort:
        where.append("t.effort = ?")
        params.append(effort.lower())
    if topic:
        where.append(
            "EXISTS (SELECT 1 FROM triage_topics tt WHERE tt.post_id=t.post_id AND tt.topic=?)"
        )
        params.append(normalize_tag(topic))
    priority = " ".join(f"WHEN '{name}' THEN {i}" for i, name in enumerate(INTENT_PRIORITY))
    rows = db._rows(
        f"""SELECT t.post_id, t.intent, t.usefulness, t.effort, t.summary, t.action, t.status,
                   p.url, p.author_handle, p.created_at
            FROM triage t JOIN posts p ON p.id = t.post_id
            WHERE {" AND ".join(where)}
            ORDER BY t.status = 'doing' DESC, t.usefulness DESC,
                     CASE t.intent {priority} ELSE 99 END, p.created_at DESC
            LIMIT ?""",
        [*params, max(1, min(limit, 50))],
    )
    for row in rows:
        row["topics"] = [
            r["topic"]
            for r in db._rows("SELECT topic FROM triage_topics WHERE post_id=?", (row["post_id"],))
        ]
    return {"count": len(rows), "items": rows}


def progress(db: Database) -> dict:
    total = db._one("SELECT COUNT(DISTINCT post_id) AS n FROM collections")["n"]
    labeled = db._one("SELECT COUNT(*) AS n FROM triage")["n"]

    def counts(column: str) -> dict:
        return {
            str(r["k"]): r["n"]
            for r in db._rows(
                f"SELECT {column} AS k, COUNT(*) AS n FROM triage GROUP BY {column} ORDER BY n DESC"
            )
        }

    return {
        "saved_posts": total,
        "labeled": labeled,
        "remaining": _remaining(db, None),
        "by_intent": counts("intent"),
        "by_usefulness": counts("usefulness"),
        "by_effort": counts("effort"),
        "by_status": counts("status"),
        "top_topics": db._rows(
            "SELECT topic, COUNT(*) AS posts FROM triage_topics GROUP BY topic "
            "ORDER BY posts DESC, topic LIMIT 25"
        ),
    }
