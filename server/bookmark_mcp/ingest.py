"""Turn captured responses into stored posts.

Every capture is first stored untouched in ``raw_events``; parsing happens
afterwards, so ``reparse`` can rebuild posts after a parser fix.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from bookmark_mcp.db import Database
from bookmark_mcp.models import ParseResult
from bookmark_mcp.platforms import x

logger = logging.getLogger(__name__)

Parser = Callable[[str, object, str | None], ParseResult]

PARSERS: dict[str, Parser] = {"x": x.parse}
OPERATIONS: dict[str, set[str]] = {"x": set(x.OPERATIONS)}


@dataclass
class IngestSummary:
    event_id: int | None = None
    status: str = "ok"
    posts: int = 0
    new_posts: int = 0
    new_in_collections: int = 0
    folders: int = 0
    skipped: int = 0
    collections: list[str] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def is_supported(platform: str, operation: str) -> bool:
    return operation.lower() in OPERATIONS.get(platform, set())


def ingest_event(
    db: Database,
    platform: str,
    operation: str,
    payload: object,
    url: str | None = None,
    page_url: str | None = None,
    captured_at: str | None = None,
) -> IngestSummary:
    """Store one captured response and parse it."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    event_id = db.record_event(platform, operation, payload, url, page_url, captured_at)
    summary = apply_event(db, event_id, platform, operation, payload, url)
    summary.event_id = event_id
    return summary


def apply_event(
    db: Database,
    event_id: int,
    platform: str,
    operation: str,
    payload: object,
    url: str | None,
) -> IngestSummary:
    summary = IngestSummary(event_id=event_id)
    parser = PARSERS.get(platform)
    if parser is None or not is_supported(platform, operation):
        summary.status = "ignored"
        db.mark_event(event_id, "ignored")
        return summary
    try:
        result = parser(operation, payload, url)
        for folder in result.folders:
            db.save_folder(folder)
        for post in result.posts:
            if db.save_post(post):
                summary.new_posts += 1
        collections: set[str] = set()
        for membership in result.memberships:
            if db.add_membership(membership):
                summary.new_in_collections += 1
            collections.add(membership.collection)
    except Exception as exc:  # keep the raw event; a later reparse can fix it
        logger.exception("Failed to parse %s/%s event %s", platform, operation, event_id)
        summary.status = "error"
        summary.error = f"{type(exc).__name__}: {exc}"
        db.mark_event(event_id, "error", error=summary.error)
        return summary

    summary.posts = len(result.posts)
    summary.folders = len(result.folders)
    summary.skipped = result.skipped
    summary.collections = sorted(collections)
    db.mark_event(event_id, "ok", posts_found=summary.posts)
    return summary


def reparse_all(db: Database, platform: str | None = None) -> dict:
    """Re-run the parsers over every stored raw event."""
    totals = {"events": 0, "ok": 0, "error": 0, "ignored": 0, "posts": 0}
    for event in db.iter_events(platform):
        totals["events"] += 1
        payload = json.loads(event["payload"])
        summary = apply_event(
            db, event["id"], event["platform"], event["operation"], payload, event["url"]
        )
        totals[summary.status] = totals.get(summary.status, 0) + 1
        totals["posts"] += summary.posts
    return totals
