"""SQLite storage with FTS5 search.

Adapted from x-bookmarks-mcp (MIT, (c) 2026 Poki Balboa): FTS5 index with a
LIKE fallback when FTS5 is unavailable or a query cannot be parsed. Extended
to be platform-agnostic, to keep posts in several collections (bookmarks,
likes, folders...), to track media for download, and to keep every raw
captured response so posts can be re-parsed when a parser improves.

Each thread gets its own connection; WAL mode lets the MCP server and the
ingest API share one database file.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from bookmark_mcp.models import Folder, Membership, Post

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    operation TEXT NOT NULL,
    url TEXT,
    page_url TEXT,
    captured_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    parse_status TEXT NOT NULL DEFAULT 'pending',
    parse_error TEXT,
    posts_found INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    native_id TEXT NOT NULL,
    url TEXT NOT NULL,
    author_id TEXT,
    author_handle TEXT,
    author_name TEXT,
    author_avatar_url TEXT,
    text TEXT NOT NULL DEFAULT '',
    lang TEXT,
    created_at TEXT,
    reply_to_id TEXT,
    conversation_id TEXT,
    quoted_post_id TEXT,
    article_title TEXT,
    article_text TEXT,
    metrics TEXT NOT NULL DEFAULT '{}',
    raw TEXT,
    note TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_author ON posts(author_handle);
CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at);

CREATE TABLE IF NOT EXISTS collections (
    post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    collection TEXT NOT NULL,
    sort_index TEXT,
    added_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (post_id, collection)
);
CREATE INDEX IF NOT EXISTS idx_collections_name ON collections(collection);

CREATE TABLE IF NOT EXISTS folders (
    platform TEXT NOT NULL,
    folder_id TEXT NOT NULL,
    name TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (platform, folder_id)
);

CREATE TABLE IF NOT EXISTS media (
    id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL,
    remote_url TEXT NOT NULL,
    video_url TEXT,
    width INTEGER,
    height INTEGER,
    alt_text TEXT,
    local_path TEXT,
    ai_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    downloaded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_media_post ON media(post_id);
CREATE INDEX IF NOT EXISTS idx_media_status ON media(status);

CREATE TABLE IF NOT EXISTS links (
    post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    display_url TEXT,
    title TEXT,
    description TEXT,
    content TEXT,
    content_type TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    fetched_at TEXT,
    PRIMARY KEY (post_id, url)
);

CREATE TABLE IF NOT EXISTS tags (
    post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (post_id, tag)
);

CREATE TABLE IF NOT EXISTS triage (
    post_id TEXT PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE,
    intent TEXT NOT NULL,
    usefulness INTEGER NOT NULL,
    effort TEXT,
    summary TEXT,
    action TEXT,
    status TEXT NOT NULL DEFAULT 'todo',
    labeled_by TEXT,
    labeled_at TEXT NOT NULL,
    status_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_triage_rank ON triage(usefulness, intent, status);

CREATE TABLE IF NOT EXISTS triage_topics (
    post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    PRIMARY KEY (post_id, topic)
);
CREATE INDEX IF NOT EXISTS idx_triage_topic ON triage_topics(topic);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
    post_id UNINDEXED, author, text, extra,
    tokenize = 'porter unicode61 remove_diacritics 2'
);
"""

POST_COLUMNS = (
    "id, platform, native_id, url, author_id, author_handle, author_name, author_avatar_url, "
    "text, lang, created_at, reply_to_id, conversation_id, quoted_post_id, article_title, "
    "article_text, metrics, note, first_seen, last_seen"
)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def normalize_tag(tag: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", tag.strip().lower()).strip("-")


def fts_query(query: str, match: str = "all") -> str:
    """Turn free text into a safe FTS5 query.

    Quoted phrases are kept as phrases, other words become prefix terms, and
    FTS5 operators typed by the user are neutralised.
    """
    terms = []
    for phrase, word in re.findall(r'"([^"]+)"|(\S+)', query):
        if phrase:
            terms.append('"' + phrase.replace('"', "") + '"')
            continue
        cleaned = re.sub(r"[^\w]+", " ", word, flags=re.UNICODE).strip()
        if cleaned:
            terms.append('"' + cleaned + '"*' if " " not in cleaned else '"' + cleaned + '"')
    return (" OR " if match == "any" else " AND ").join(terms)


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        conn = self.conn
        conn.executescript(SCHEMA)
        self.fts_available = self._try_create_fts(conn)
        conn.commit()

    # --- connections ------------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _try_create_fts(self, conn: sqlite3.Connection) -> bool:
        try:
            conn.executescript(FTS_SCHEMA)
            return True
        except sqlite3.OperationalError as exc:
            logger.warning("FTS5 unavailable, search will use LIKE: %s", exc)
            return False

    def _rows(self, sql: str, params: tuple | list = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple | list = ()) -> dict | None:
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    # --- raw events -------------------------------------------------------------

    def record_event(
        self,
        platform: str,
        operation: str,
        payload: dict | list | str,
        url: str | None = None,
        page_url: str | None = None,
        captured_at: str | None = None,
    ) -> int:
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        with self._write_lock, self.conn:
            cur = self.conn.execute(
                """INSERT INTO raw_events
                   (platform, operation, url, page_url, captured_at, received_at, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (platform, operation, url, page_url, captured_at or now(), now(), body),
            )
            return int(cur.lastrowid)

    def mark_event(
        self, event_id: int, status: str, posts_found: int = 0, error: str | None = None
    ):
        with self._write_lock, self.conn:
            self.conn.execute(
                "UPDATE raw_events SET parse_status=?, posts_found=?, parse_error=? WHERE id=?",
                (status, posts_found, error, event_id),
            )

    def iter_events(self, platform: str | None = None) -> Iterator[dict]:
        sql = "SELECT * FROM raw_events"
        params: tuple = ()
        if platform:
            sql += " WHERE platform = ?"
            params = (platform,)
        for row in self.conn.execute(sql + " ORDER BY id", params):
            yield dict(row)

    # --- writes -----------------------------------------------------------------

    def save_post(self, post: Post) -> bool:
        """Insert or update a post with its media and links. Returns True if new."""
        stamp = now()
        with self._write_lock, self.conn:
            existing = self._one("SELECT first_seen FROM posts WHERE id=?", (post.id,))
            self.conn.execute(
                """INSERT INTO posts (id, platform, native_id, url, author_id, author_handle,
                       author_name, author_avatar_url, text, lang, created_at, reply_to_id,
                       conversation_id, quoted_post_id, article_title, article_text, metrics,
                       raw, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       url=excluded.url, author_id=excluded.author_id,
                       author_handle=excluded.author_handle, author_name=excluded.author_name,
                       author_avatar_url=excluded.author_avatar_url, text=excluded.text,
                       lang=excluded.lang, created_at=excluded.created_at,
                       reply_to_id=excluded.reply_to_id, conversation_id=excluded.conversation_id,
                       quoted_post_id=COALESCE(excluded.quoted_post_id, posts.quoted_post_id),
                       article_title=excluded.article_title, article_text=excluded.article_text,
                       metrics=excluded.metrics, raw=excluded.raw, last_seen=excluded.last_seen""",
                (
                    post.id,
                    post.platform,
                    post.native_id,
                    post.url,
                    post.author_id,
                    post.author_handle,
                    post.author_name,
                    post.author_avatar_url,
                    post.text,
                    post.lang,
                    post.created_at,
                    post.reply_to_id,
                    post.conversation_id,
                    post.quoted_post_id,
                    post.article_title,
                    post.article_text,
                    json.dumps(post.metrics),
                    json.dumps(post.raw, ensure_ascii=False) if post.raw is not None else None,
                    stamp,
                    stamp,
                ),
            )
            for media in post.media:
                self.conn.execute(
                    """INSERT INTO media (id, post_id, position, kind, remote_url, video_url,
                           width, height, alt_text)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                           position=excluded.position, kind=excluded.kind,
                           video_url=excluded.video_url, width=excluded.width,
                           height=excluded.height, alt_text=excluded.alt_text,
                           status=CASE WHEN media.remote_url = excluded.remote_url
                                       THEN media.status ELSE 'pending' END,
                           remote_url=excluded.remote_url""",
                    (
                        media.id,
                        post.id,
                        media.position,
                        media.kind,
                        media.remote_url,
                        media.video_url,
                        media.width,
                        media.height,
                        media.alt_text,
                    ),
                )
            for link in post.links:
                self.conn.execute(
                    """INSERT INTO links (post_id, url, display_url, title, description)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(post_id, url) DO UPDATE SET
                           display_url=COALESCE(links.display_url, excluded.display_url),
                           title=COALESCE(links.title, excluded.title),
                           description=COALESCE(links.description, excluded.description)""",
                    (post.id, link.url, link.display_url, link.title, link.description),
                )
            self.refresh_fts(post.id)
        return existing is None

    def add_membership(self, membership: Membership) -> bool:
        """Put a post in a collection. Returns True if it was not there before."""
        stamp = now()
        with self._write_lock, self.conn:
            existing = self._one(
                "SELECT 1 FROM collections WHERE post_id=? AND collection=?",
                (membership.post_id, membership.collection),
            )
            self.conn.execute(
                """INSERT INTO collections (post_id, collection, sort_index, added_at, last_seen)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(post_id, collection) DO UPDATE SET
                       sort_index=COALESCE(excluded.sort_index, collections.sort_index),
                       last_seen=excluded.last_seen""",
                (membership.post_id, membership.collection, membership.sort_index, stamp, stamp),
            )
        return existing is None

    def save_folder(self, folder: Folder) -> None:
        with self._write_lock, self.conn:
            self.conn.execute(
                """INSERT INTO folders (platform, folder_id, name, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(platform, folder_id) DO UPDATE SET
                       name=excluded.name, updated_at=excluded.updated_at""",
                (folder.platform, folder.folder_id, folder.name, now()),
            )

    def refresh_fts(self, post_id: str) -> None:
        if not self.fts_available:
            return
        post = self._one(
            "SELECT author_handle, author_name, text, article_title, article_text, note "
            "FROM posts WHERE id=?",
            (post_id,),
        )
        if post is None:
            return
        extra_parts = [post["article_title"], post["article_text"], post["note"]]
        extra_parts += [
            r["alt_text"]
            for r in self._rows("SELECT alt_text FROM media WHERE post_id=?", (post_id,))
        ]
        extra_parts += [
            " ".join(filter(None, (r["url"], r["title"], r["description"], r["content"])))
            for r in self._rows(
                "SELECT url, title, description, content FROM links WHERE post_id=?", (post_id,)
            )
        ]
        extra_parts += [
            r["tag"] for r in self._rows("SELECT tag FROM tags WHERE post_id=?", (post_id,))
        ]
        triage = self._one("SELECT summary, action FROM triage WHERE post_id=?", (post_id,))
        if triage:
            extra_parts += [triage["summary"], triage["action"]]
        extra_parts += [
            r["topic"]
            for r in self._rows("SELECT topic FROM triage_topics WHERE post_id=?", (post_id,))
        ]
        author = " ".join(filter(None, (post["author_handle"], post["author_name"])))
        with self._write_lock, self.conn:
            self.conn.execute("DELETE FROM posts_fts WHERE post_id=?", (post_id,))
            self.conn.execute(
                "INSERT INTO posts_fts (post_id, author, text, extra) VALUES (?, ?, ?, ?)",
                (post_id, author, post["text"], "\n".join(filter(None, extra_parts))),
            )

    def rebuild_fts(self) -> None:
        if not self.fts_available:
            return
        with self._write_lock, self.conn:
            self.conn.execute("DELETE FROM posts_fts")
        for row in self._rows("SELECT id FROM posts"):
            self.refresh_fts(row["id"])

    # --- media ------------------------------------------------------------------

    def pending_media(self, limit: int = 100, retry_errors: bool = False) -> list[dict]:
        statuses = ("pending", "error") if retry_errors else ("pending",)
        marks = ",".join("?" * len(statuses))
        return self._rows(
            f"SELECT * FROM media WHERE status IN ({marks}) ORDER BY rowid LIMIT ?",
            (*statuses, limit),
        )

    def set_media_result(
        self,
        media_id: str,
        status: str,
        local_path: str | None = None,
        ai_path: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._write_lock, self.conn:
            self.conn.execute(
                """UPDATE media SET status=?, local_path=COALESCE(?, local_path),
                       ai_path=COALESCE(?, ai_path), error=?, downloaded_at=? WHERE id=?""",
                (status, local_path, ai_path, error, now(), media_id),
            )

    def get_media(self, post_id: str) -> list[dict]:
        return self._rows("SELECT * FROM media WHERE post_id=? ORDER BY position", (post_id,))

    # --- links ------------------------------------------------------------------

    def pending_links(self, limit: int = 100, refresh: bool = False) -> list[dict]:
        if refresh:
            return self._rows("SELECT * FROM links ORDER BY rowid LIMIT ?", (limit,))
        return self._rows(
            "SELECT * FROM links WHERE status='pending' ORDER BY rowid LIMIT ?", (limit,)
        )

    def set_link_result(
        self,
        post_id: str,
        url: str,
        status: str,
        title: str | None = None,
        content: str | None = None,
        content_type: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._write_lock, self.conn:
            self.conn.execute(
                """UPDATE links SET status=?, title=COALESCE(?, title),
                       content=COALESCE(?, content), content_type=COALESCE(?, content_type),
                       error=?, fetched_at=?
                   WHERE post_id=? AND url=?""",
                (status, title, content, content_type, error, now(), post_id, url),
            )
        self.refresh_fts(post_id)

    def get_links(self, post_id: str) -> list[dict]:
        return self._rows("SELECT * FROM links WHERE post_id=? ORDER BY rowid", (post_id,))

    # --- tags & notes -----------------------------------------------------------

    def add_tag(self, post_id: str, tag: str) -> str:
        tag = normalize_tag(tag)
        if not tag:
            raise ValueError("Tag is empty after normalisation")
        with self._write_lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO tags (post_id, tag) VALUES (?, ?)", (post_id, tag)
            )
        self.refresh_fts(post_id)
        return tag

    def remove_tag(self, post_id: str, tag: str) -> None:
        with self._write_lock, self.conn:
            self.conn.execute(
                "DELETE FROM tags WHERE post_id=? AND tag=?", (post_id, normalize_tag(tag))
            )
        self.refresh_fts(post_id)

    def get_tags(self, post_id: str) -> list[str]:
        return [
            r["tag"]
            for r in self._rows("SELECT tag FROM tags WHERE post_id=? ORDER BY tag", (post_id,))
        ]

    def list_tags(self) -> list[dict]:
        return self._rows(
            "SELECT tag, COUNT(*) AS count FROM tags GROUP BY tag ORDER BY count DESC, tag"
        )

    def set_note(self, post_id: str, note: str) -> None:
        with self._write_lock, self.conn:
            self.conn.execute("UPDATE posts SET note=? WHERE id=?", (note, post_id))
        self.refresh_fts(post_id)

    # --- reads ------------------------------------------------------------------

    def get_post(self, post_id: str) -> dict | None:
        post = self._one(f"SELECT {POST_COLUMNS} FROM posts WHERE id=?", (post_id,))
        if post:
            post["metrics"] = json.loads(post["metrics"] or "{}")
        return post

    def get_collections_of(self, post_id: str) -> list[str]:
        return [
            r["collection"]
            for r in self._rows(
                "SELECT collection FROM collections WHERE post_id=? ORDER BY collection", (post_id,)
            )
        ]

    def folder_names(self) -> dict[str, str]:
        return {
            f"folder:{r['folder_id']}": r["name"]
            for r in self._rows("SELECT folder_id, name FROM folders")
        }

    def list_collections(self) -> list[dict]:
        return self._rows(
            """SELECT collection, COUNT(*) AS count, MAX(added_at) AS last_added
               FROM collections GROUP BY collection ORDER BY count DESC"""
        )

    def _filters(
        self,
        collection: str | None,
        author: str | None,
        since: str | None,
        until: str | None,
        has_images: bool | None,
        labels: dict | None = None,
    ) -> tuple[list[str], list]:
        where: list[str] = []
        params: list = []
        labels = {k: v for k, v in (labels or {}).items() if v not in (None, "")}
        if labels.get("labeled") is False:
            where.append("NOT EXISTS (SELECT 1 FROM triage t WHERE t.post_id=p.id)")
        for field in ("intent", "effort", "status"):
            if field in labels:
                where.append(
                    f"EXISTS (SELECT 1 FROM triage t WHERE t.post_id=p.id AND t.{field}=?)"
                )
                params.append(labels[field])
        if "min_usefulness" in labels:
            where.append("EXISTS (SELECT 1 FROM triage t WHERE t.post_id=p.id AND t.usefulness>=?)")
            params.append(int(labels["min_usefulness"]))
        if "topic" in labels:
            where.append(
                "EXISTS (SELECT 1 FROM triage_topics tt WHERE tt.post_id=p.id AND tt.topic=?)"
            )
            params.append(normalize_tag(str(labels["topic"])))
        if collection:
            where.append(
                "EXISTS (SELECT 1 FROM collections c WHERE c.post_id=p.id AND c.collection=?)"
            )
            params.append(collection)
        if author:
            where.append("(LOWER(p.author_handle)=LOWER(?) OR LOWER(p.author_name) LIKE LOWER(?))")
            params += [author.lstrip("@"), f"%{author}%"]
        if since:
            where.append("p.created_at >= ?")
            params.append(since)
        if until:
            where.append("p.created_at <= ?")
            params.append(until)
        if has_images is not None:
            clause = "EXISTS (SELECT 1 FROM media m WHERE m.post_id=p.id)"
            where.append(clause if has_images else f"NOT {clause}")
        return where, params

    def search(
        self,
        query: str,
        collection: str | None = None,
        author: str | None = None,
        since: str | None = None,
        until: str | None = None,
        has_images: bool | None = None,
        limit: int = 10,
        match: str = "all",
        labels: dict | None = None,
    ) -> list[dict]:
        where, params = self._filters(collection, author, since, until, has_images, labels)
        select = (
            "p.id, p.url, p.author_handle, p.author_name, p.created_at, p.text, "
            "(SELECT COUNT(*) FROM media m WHERE m.post_id=p.id) AS media_count"
        )
        expression = fts_query(query, match)
        if self.fts_available and expression:
            try:
                sql = (
                    f"SELECT {select}, snippet(posts_fts, 2, '[', ']', '...', 24) AS snippet, "
                    "bm25(posts_fts) AS score FROM posts_fts f JOIN posts p ON p.id=f.post_id "
                    "WHERE posts_fts MATCH ?"
                )
                sql += "".join(f" AND {w}" for w in where)
                return self._rows(sql + " ORDER BY score LIMIT ?", [expression, *params, limit])
            except sqlite3.OperationalError as exc:
                logger.warning("FTS query failed, using LIKE fallback: %s", exc)
        like_clauses: list[str] = []
        like_params: list[str] = []
        for word in re.findall(r"\w+", query, flags=re.UNICODE) or [query]:
            like_clauses.append(
                "(p.text LIKE ? OR p.author_handle LIKE ? OR p.article_title LIKE ? "
                "OR EXISTS (SELECT 1 FROM links l WHERE l.post_id=p.id AND "
                "(l.title LIKE ? OR l.content LIKE ?)))"
            )
            like_params += [f"%{word}%"] * 5
        joiner = " OR " if match == "any" else " AND "
        sql = f"SELECT {select}, '' AS snippet, 0 AS score FROM posts p WHERE 1=1"
        sql += "".join(f" AND {w}" for w in where)
        sql += " AND (" + joiner.join(like_clauses) + ")"
        return self._rows(
            sql + " ORDER BY p.created_at DESC LIMIT ?", [*params, *like_params, limit]
        )

    def browse(
        self,
        collection: str | None = None,
        author: str | None = None,
        since: str | None = None,
        until: str | None = None,
        has_images: bool | None = None,
        limit: int = 20,
        offset: int = 0,
        labels: dict | None = None,
    ) -> list[dict]:
        where, params = self._filters(None, author, since, until, has_images, labels)
        select = (
            "p.id, p.url, p.author_handle, p.author_name, p.created_at, p.text, "
            "(SELECT COUNT(*) FROM media m WHERE m.post_id=p.id) AS media_count"
        )
        if collection:
            sql = (
                f"SELECT {select}, c.added_at FROM collections c JOIN posts p ON p.id=c.post_id "
                "WHERE c.collection=?"
            )
            params = [collection, *params]
            order = (
                " ORDER BY c.sort_index IS NULL, CAST(c.sort_index AS INTEGER) DESC, "
                "c.added_at DESC"
            )
        else:
            sql = f"SELECT {select} FROM posts p WHERE 1=1"
            order = " ORDER BY p.created_at DESC"
        sql += "".join(f" AND {w}" for w in where)
        return self._rows(sql + order + " LIMIT ? OFFSET ?", [*params, limit, offset])

    def posts_with_tag(self, tag: str, limit: int = 50) -> list[dict]:
        return self._rows(
            """SELECT p.id FROM tags t JOIN posts p ON p.id=t.post_id WHERE t.tag=?
               ORDER BY p.created_at DESC LIMIT ?""",
            (normalize_tag(tag), limit),
        )

    def stats(self) -> dict:
        one = self._one
        return {
            "posts": one("SELECT COUNT(*) AS n FROM posts")["n"],
            "collections": self.list_collections(),
            "media": {
                r["status"]: r["n"]
                for r in self._rows("SELECT status, COUNT(*) AS n FROM media GROUP BY status")
            },
            "links": {
                r["status"]: r["n"]
                for r in self._rows("SELECT status, COUNT(*) AS n FROM links GROUP BY status")
            },
            "tags": one("SELECT COUNT(DISTINCT tag) AS n FROM tags")["n"],
            "date_range": one(
                "SELECT MIN(created_at) AS earliest, MAX(created_at) AS latest FROM posts"
            ),
            "top_authors": self._rows(
                """SELECT author_handle, COUNT(*) AS count FROM posts p
                   WHERE EXISTS (SELECT 1 FROM collections c WHERE c.post_id=p.id)
                   GROUP BY author_handle ORDER BY count DESC LIMIT 10"""
            ),
            "events": {
                r["parse_status"]: r["n"]
                for r in self._rows(
                    "SELECT parse_status, COUNT(*) AS n FROM raw_events GROUP BY parse_status"
                )
            },
            "last_capture": one("SELECT MAX(received_at) AS t FROM raw_events")["t"],
            "fts": self.fts_available,
        }
