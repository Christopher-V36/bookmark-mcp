"""Tool logic behind the MCP server, kept as plain functions so it is easy to test.

Inspired by x-bookmarks-mcp's tool set (MIT, (c) 2026 Poki Balboa), extended
with collections, filters and images.
"""

from __future__ import annotations

import re
from pathlib import Path

from bookmark_mcp import config, media, triage
from bookmark_mcp.db import Database

DETAIL_LIMITS = {"brief": 280, "detailed": 1500, "guide": 6000}
_STATUS_URL = re.compile(r"(?:x|twitter)\.com/[^/]+/status(?:es)?/(\d+)")


def resolve_post_id(value: str) -> str:
    """Accept 'x:123', '123' or a post URL."""
    value = value.strip()
    match = _STATUS_URL.search(value)
    if match:
        return f"x:{match.group(1)}"
    if value.isdigit():
        return f"x:{value}"
    return value


def resolve_collection(db: Database, value: str | None) -> str | None:
    """Accept 'bookmarks', 'likes', 'folder:<id>' or a folder's name."""
    if not value:
        return None
    value = value.strip()
    if value.lower() in ("bookmarks", "likes") or value.startswith("folder:"):
        return value.lower() if ":" not in value else value
    for key, name in db.folder_names().items():
        if name.lower() == value.lower():
            return key
    return value


def _summary(db: Database, row: dict, folder_names: dict[str, str]) -> dict:
    text = row.get("text") or ""
    labels = triage.get_labels(db, row["id"])
    summary = {
        "id": row["id"],
        "url": row["url"],
        "author": f"@{row['author_handle']}" if row.get("author_handle") else None,
        "author_name": row.get("author_name"),
        "created_at": row.get("created_at"),
        "text": row.get("snippet") or (text if len(text) <= 400 else text[:400] + "..."),
        "images": row.get("media_count", 0),
        "collections": [
            folder_names.get(c, c) if c.startswith("folder:") else c
            for c in db.get_collections_of(row["id"])
        ],
    }
    if labels:
        summary["labels"] = {
            k: labels[k] for k in ("intent", "usefulness", "effort", "status", "topics")
        }
        summary["action"] = labels.get("action")
    return summary


def search_posts(
    db: Database,
    query: str,
    collection: str | None = None,
    author: str | None = None,
    since: str | None = None,
    until: str | None = None,
    has_images: bool | None = None,
    limit: int = 10,
    match: str = "all",
    labels: dict | None = None,
) -> dict:
    if not query.strip():
        return {"count": 0, "results": [], "hint": "Give a search query, or use browse."}
    rows = db.search(
        query,
        collection=resolve_collection(db, collection),
        author=author,
        since=since,
        until=until,
        has_images=has_images,
        limit=max(1, min(limit, 50)),
        match="any" if match == "any" else "all",
        labels=labels,
    )
    names = db.folder_names()
    results = [_summary(db, row, names) for row in rows]
    hint = None
    if not results and match != "any" and len(query.split()) > 1:
        hint = "No post matched every word; try match='any' or fewer words."
    return {"count": len(results), "results": results, "hint": hint}


def browse(
    db: Database,
    collection: str | None = "bookmarks",
    limit: int = 20,
    offset: int = 0,
    author: str | None = None,
    has_images: bool | None = None,
    labels: dict | None = None,
) -> dict:
    rows = db.browse(
        resolve_collection(db, collection),
        author=author,
        has_images=has_images,
        limit=max(1, min(limit, 100)),
        offset=max(0, offset),
        labels=labels,
    )
    names = db.folder_names()
    return {
        "count": len(rows),
        "offset": offset,
        "results": [_summary(db, row, names) for row in rows],
    }


def list_collections(db: Database) -> list[dict]:
    names = db.folder_names()
    return [
        {
            "collection": row["collection"],
            "name": names.get(row["collection"], row["collection"]),
            "posts": row["count"],
            "last_added": row["last_added"],
        }
        for row in db.list_collections()
    ]


def _media_info(rows: list[dict]) -> list[dict]:
    info = []
    for row in rows:
        local = media.local_file(row.get("ai_path"))
        info.append(
            {
                "kind": row["kind"],
                "alt_text": row.get("alt_text"),
                "status": row["status"],
                "local_file": str(local) if local else None,
                "video_url": row.get("video_url"),
            }
        )
    return info


def get_post(db: Database, post_id: str, max_images: int = 4) -> tuple[dict | None, list[Path]]:
    """Return a post's full details and the image files to show (AI copies)."""
    post_id = resolve_post_id(post_id)
    post = db.get_post(post_id)
    if post is None:
        return None, []
    media_rows = db.get_media(post_id)
    names = db.folder_names()
    detail = {
        "id": post["id"],
        "url": post["url"],
        "author": f"@{post['author_handle']}" if post.get("author_handle") else None,
        "author_name": post.get("author_name"),
        "created_at": post.get("created_at"),
        "text": post["text"],
        "metrics": post["metrics"],
        "collections": [names.get(c, c) for c in db.get_collections_of(post_id)],
        "media": _media_info(media_rows),
        "links": [
            {k: link.get(k) for k in ("url", "title", "description", "status")}
            | {"content": (link.get("content") or "")[:3000] or None}
            for link in db.get_links(post_id)
        ],
        "tags": db.get_tags(post_id),
        "note": post.get("note"),
        "labels": triage.get_labels(db, post_id),
    }
    if post.get("article_title") or post.get("article_text"):
        detail["article"] = {
            "title": post.get("article_title"),
            "text": (post.get("article_text") or "")[:8000],
        }
    if post.get("reply_to_id"):
        detail["reply_to"] = post["reply_to_id"]
    images = [Path(f) for f in (m["local_file"] for m in detail["media"]) if f]
    if post.get("quoted_post_id"):
        quoted = db.get_post(post["quoted_post_id"])
        if quoted:
            quoted_media = db.get_media(quoted["id"])
            detail["quoted_post"] = {
                "id": quoted["id"],
                "url": quoted["url"],
                "author": f"@{quoted['author_handle']}" if quoted.get("author_handle") else None,
                "text": quoted["text"],
                "media": _media_info(quoted_media),
            }
            images += [
                Path(m["local_file"]) for m in detail["quoted_post"]["media"] if m["local_file"]
            ]
    missing = [m for m in detail["media"] if m["status"] != "done"]
    if missing:
        detail["images_note"] = (
            f"{len(missing)} image(s) not downloaded yet; "
            "run `bookmark-mcp media` or wait for the background download."
        )
    return detail, images[: max(0, max_images)]


def gather_topic(
    db: Database, query_or_tag: str, detail_level: str = "detailed", limit: int = 25
) -> dict:
    """Collect posts about a topic (tag first, then search) for the AI to synthesise."""
    max_chars = DETAIL_LIMITS.get(detail_level, DETAIL_LIMITS["detailed"])
    ids = [row["id"] for row in db.posts_with_tag(query_or_tag, limit)]
    source = "tag"
    if not ids:
        source = "search"
        ids = [row["id"] for row in db.search(query_or_tag, limit=limit, match="any")]
    posts = []
    for post_id in ids:
        post = db.get_post(post_id)
        if post is None:
            continue
        item = {
            "id": post_id,
            "url": post["url"],
            "author": post.get("author_handle"),
            "created_at": post.get("created_at"),
            "text": post["text"][:max_chars],
            "image_descriptions": [m["alt_text"] for m in db.get_media(post_id) if m["alt_text"]],
            "images": len(db.get_media(post_id)),
        }
        if detail_level != "brief":
            item["links"] = [
                {
                    "url": link["url"],
                    "title": link.get("title"),
                    "content": (link.get("content") or "")[:max_chars] or None,
                }
                for link in db.get_links(post_id)
            ]
            if post.get("article_title"):
                item["article"] = {
                    "title": post["article_title"],
                    "text": (post.get("article_text") or "")[:max_chars],
                }
        posts.append(item)
    return {
        "topic": query_or_tag,
        "matched_by": source,
        "count": len(posts),
        "detail_level": detail_level,
        "posts": posts,
        "next_step": "Synthesise these posts. Call get_post(id) to see a post's images.",
    }


def sync_status(db: Database) -> dict:
    stats = db.stats()
    return {
        "posts": stats["posts"],
        "collections": list_collections(db),
        "last_capture": stats["last_capture"],
        "captures": stats["events"],
        "images": stats["media"],
        "links": stats["links"],
        "data_folder": str(config.data_dir()),
        "ingest_port": config.port(),
    }
