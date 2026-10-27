"""Import JSON files exported by twitter-web-exporter.

Format rules adapted from x-bookmarks-mcp (MIT, (c) 2026 Poki Balboa), which
detects both the raw GraphQL objects and the exporter's flat records.

Best results come from exports made with "Include all metadata" ticked: each
record then carries the full raw tweet in ``metadata`` and is parsed exactly
like a live capture (long text, links, quoted posts, alt text, cards).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from bookmark_mcp.db import Database
from bookmark_mcp.models import Media, Membership, ParseResult, Post
from bookmark_mcp.platforms.x.parse import PLATFORM, collect_tweet, parse_tweet, unwrap_tweet


def detect_format(data: object) -> str:
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return "unknown"
    first = data[0]
    if isinstance(first.get("metadata"), dict) and first["metadata"].get("rest_id"):
        return "exporter-with-metadata"
    if first.get("rest_id") and isinstance(first.get("legacy"), dict):
        return "raw-graphql"
    if "full_text" in first and "id" in first:
        return "exporter-flat"
    return "unknown"


def _parse_flat_date(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%a %b %d %H:%M:%S %z %Y"):
        try:
            return datetime.strptime(value, fmt).astimezone(UTC).isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value).astimezone(UTC).isoformat()
    except ValueError:
        return None


def _flat_post(record: dict) -> Post | None:
    tweet_id = str(record.get("id") or "")
    if not tweet_id:
        return None
    handle = record.get("screen_name")
    media = []
    for position, item in enumerate(record.get("media") or []):
        kind = {"animated_gif": "gif"}.get(item.get("type"), item.get("type") or "photo")
        image = item.get("original") if kind == "photo" else item.get("thumbnail")
        if not image:
            continue
        image = image.replace("name=thumb", "name=large")
        media.append(
            Media(
                id=f"{PLATFORM}:{tweet_id}_{position}",
                kind=kind,
                remote_url=image,
                position=position,
                video_url=item.get("original") if kind != "photo" else None,
                alt_text=item.get("ext_alt_text") or None,
            )
        )
    metrics = {
        "likes": record.get("favorite_count"),
        "reposts": record.get("retweet_count"),
        "replies": record.get("reply_count"),
        "quotes": record.get("quote_count"),
        "bookmarks": record.get("bookmark_count"),
        "views": record.get("views_count"),
    }
    return Post(
        id=f"{PLATFORM}:{tweet_id}",
        platform=PLATFORM,
        native_id=tweet_id,
        url=record.get("url") or f"https://x.com/{handle or 'i'}/status/{tweet_id}",
        text=record.get("full_text") or "",
        author_handle=handle,
        author_name=record.get("name"),
        author_avatar_url=record.get("profile_image_url"),
        created_at=_parse_flat_date(record.get("created_at")),
        metrics={k: v for k, v in metrics.items() if v is not None},
        raw=record,
        media=media,
    )


def import_file(db: Database, path: str | Path, collection: str = "bookmarks") -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    fmt = detect_format(data)
    if fmt == "unknown":
        raise ValueError(f"Not a twitter-web-exporter JSON file: {path}")

    result = {"format": fmt, "records": len(data), "added": 0, "updated": 0, "skipped": 0}
    for index, record in enumerate(data):
        if not isinstance(record, dict):
            result["skipped"] += 1
            continue
        if fmt == "exporter-flat" and not isinstance(record.get("metadata"), dict):
            post = _flat_post(record)
            extra: list[Post] = []
        else:
            tweet = record.get("metadata") if isinstance(record.get("metadata"), dict) else record
            post, extra = _rich_post(tweet)
        if post is None:
            result["skipped"] += 1
            continue
        for other in extra:
            db.save_post(other)
        result["added" if db.save_post(post) else "updated"] += 1
        # Exports are newest first; keep that order with a descending sort index.
        db.add_membership(Membership(post.id, collection, str(10**12 - index)))
    if fmt == "exporter-flat":
        result["tip"] = (
            "Export again with 'Include all metadata' ticked to get long texts, "
            "link previews, quoted posts and image descriptions."
        )
    return result


def _rich_post(tweet: dict) -> tuple[Post | None, list[Post]]:
    """Parse a raw tweet, returning it plus its quoted / retweeted originals."""
    unwrapped = unwrap_tweet(tweet) or (tweet if tweet.get("rest_id") else None)
    if unwrapped is None:
        return None, []
    collected = ParseResult()
    main = collect_tweet(unwrapped, collected, set())
    if main is None:
        return parse_tweet(unwrapped), []
    return main, [p for p in collected.posts if p.id != main.id]
