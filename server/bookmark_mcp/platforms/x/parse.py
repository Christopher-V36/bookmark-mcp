"""Parse X web-app GraphQL responses into platform-agnostic records.

The extension forwards the raw JSON that x.com already received while you
browsed (no extra requests are made). The parsing rules for timelines follow
twitter-web-exporter (MIT, (c) 2023 prin): take entries from the timeline
instructions, unwrap ``TweetWithVisibilityResults`` and skip tombstones.

X changes field locations from time to time, so every lookup here is
defensive: known paths first, then a recursive search as a fallback.
"""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

from bookmark_mcp.models import Folder, Link, Media, Membership, ParseResult, Post

PLATFORM = "x"

# Lower-cased GraphQL operation name -> what it means for us.
OPERATIONS = {
    "bookmarks": "bookmarks",
    "bookmarkfoldertimeline": "folder",
    "bookmarkfoldersslice": "folders",
    "likes": "likes",
}

# Card binding keys that hold a preview image, best first.
_CARD_IMAGE_KEYS = (
    "photo_image_full_size_original",
    "thumbnail_image_original",
    "summary_photo_image_original",
    "player_image_original",
    "photo_image_full_size_large",
    "thumbnail_image_large",
    "summary_photo_image_large",
    "player_image_large",
)


def parse(operation: str, payload: Any, request_url: str | None = None) -> ParseResult:
    """Parse one captured response. Unknown operations return an empty result."""
    kind = OPERATIONS.get(operation.lower())
    result = ParseResult()
    if kind is None or not isinstance(payload, dict):
        return result

    if kind == "folders":
        result.folders = _parse_folders(payload)
        return result

    collections = ["bookmarks"] if kind in ("bookmarks", "folder") else [kind]
    if kind == "folder":
        folder_id = _request_variable(request_url, "bookmark_collection_id")
        if folder_id:
            collections.append(f"folder:{folder_id}")

    seen: set[str] = set()
    for sort_index, item_content in _timeline_items(payload):
        tweet = unwrap_tweet(item_content.get("tweet_results", {}).get("result"))
        if tweet is None:
            result.skipped += 1
            continue
        main = collect_tweet(tweet, result, seen)
        if main is None:
            result.skipped += 1
            continue
        for collection in collections:
            # A folder numbers its items on its own scale: keep the main bookmarks order.
            index = None if kind == "folder" and collection == "bookmarks" else sort_index
            result.memberships.append(Membership(main.id, collection, index))
    return result


# --- timelines -----------------------------------------------------------------


def _find_instructions(payload: dict) -> list:
    data = payload.get("data", {})
    known = (
        ("bookmark_timeline_v2", "timeline", "instructions"),
        ("bookmark_collection_timeline", "timeline", "instructions"),
        ("bookmark_timeline", "timeline", "instructions"),
        ("user", "result", "timeline", "timeline", "instructions"),
        ("user", "result", "timeline_v2", "timeline", "instructions"),
    )
    for path in known:
        value = _get(data, *path)
        if isinstance(value, list):
            return value
    found = _find_key(data, "instructions")
    return found if isinstance(found, list) else []


def _timeline_items(payload: dict):
    """Yield (sort_index, itemContent) for every tweet entry in the timeline."""
    for instruction in _find_instructions(payload):
        if not isinstance(instruction, dict):
            continue
        entries = instruction.get("entries") or []
        if isinstance(instruction.get("entry"), dict):
            entries = [*entries, instruction["entry"]]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            content = entry.get("content", {})
            sort_index = entry.get("sortIndex")
            if isinstance(content.get("itemContent"), dict):
                item = content["itemContent"]
                if item.get("__typename", "TimelineTweet") == "TimelineTweet":
                    yield sort_index, item
            for module_item in content.get("items") or []:
                item = _get(module_item, "item", "itemContent")
                if isinstance(item, dict) and item.get("__typename") == "TimelineTweet":
                    yield sort_index, item


def unwrap_tweet(result: Any) -> dict | None:
    if not isinstance(result, dict):
        return None
    typename = result.get("__typename")
    if typename == "TweetWithVisibilityResults":
        return unwrap_tweet(result.get("tweet"))
    if typename in ("TweetTombstone", "TweetUnavailable"):
        return None
    if (
        typename in (None, "Tweet")
        and result.get("rest_id")
        and isinstance(result.get("legacy"), dict)
    ):
        return result
    return None


# --- tweets --------------------------------------------------------------------


def collect_tweet(tweet: dict, result: ParseResult, seen: set[str]) -> Post | None:
    """Parse a tweet (plus its quoted / retweeted post) into ``result.posts``.

    Returns the post that should receive the collection membership: for a
    retweet that is the original post.
    """
    legacy = tweet.get("legacy", {})
    retweeted = unwrap_tweet(_get(legacy, "retweeted_status_result", "result"))
    if retweeted is not None:
        return collect_tweet(retweeted, result, seen)

    post = parse_tweet(tweet)
    if post is None:
        return None

    quoted = unwrap_tweet(_get(tweet, "quoted_status_result", "result"))
    if quoted is not None:
        quoted_post = collect_tweet(quoted, result, seen)
        if quoted_post is not None:
            post.quoted_post_id = quoted_post.id

    if post.id not in seen:
        seen.add(post.id)
        result.posts.append(post)
    return post


def parse_tweet(tweet: dict) -> Post | None:
    tweet_id = str(tweet.get("rest_id") or "")
    legacy = tweet.get("legacy")
    if not tweet_id or not isinstance(legacy, dict):
        return None

    user = _unwrap_user(_get(tweet, "core", "user_results", "result"))
    handle = _first(_get(user, "core", "screen_name"), _get(user, "legacy", "screen_name"))
    name = _first(_get(user, "core", "name"), _get(user, "legacy", "name"))
    avatar = _first(
        _get(user, "avatar", "image_url"), _get(user, "legacy", "profile_image_url_https")
    )

    note = _get(tweet, "note_tweet", "note_tweet_results", "result") or {}
    url_entities = list(_get(legacy, "entities", "urls") or [])
    url_entities += list(_get(note, "entity_set", "urls") or [])
    media_entities = (
        _get(legacy, "extended_entities", "media") or _get(legacy, "entities", "media") or []
    )

    text = note.get("text") or legacy.get("full_text") or legacy.get("text") or ""
    text = _expand_text(html.unescape(text), url_entities, media_entities)

    post = Post(
        id=f"{PLATFORM}:{tweet_id}",
        platform=PLATFORM,
        native_id=tweet_id,
        url=f"https://x.com/{handle or 'i'}/status/{tweet_id}",
        text=text,
        author_id=_first(_get(user, "rest_id")),
        author_handle=handle,
        author_name=name,
        author_avatar_url=avatar,
        lang=legacy.get("lang"),
        created_at=_parse_date(legacy.get("created_at")),
        reply_to_id=_prefixed(legacy.get("in_reply_to_status_id_str")),
        conversation_id=_prefixed(legacy.get("conversation_id_str")),
        metrics=_metrics(tweet, legacy),
        raw=tweet,
    )

    post.media = _media(tweet_id, media_entities)
    post.links = _links(url_entities)
    _apply_card(tweet, post)
    _apply_article(tweet, post)
    return post


def _unwrap_user(user: Any) -> dict:
    if isinstance(user, dict) and user.get("__typename") in (None, "User"):
        return user
    return {}


def _expand_text(text: str, url_entities: list, media_entities: list) -> str:
    for entity in url_entities:
        short, expanded = entity.get("url"), entity.get("expanded_url")
        if short and expanded:
            text = text.replace(short, expanded)
    for entity in media_entities:
        short = entity.get("url")
        if short:
            text = text.replace(short, "")
    return text.strip()


def _metrics(tweet: dict, legacy: dict) -> dict:
    metrics = {
        "likes": legacy.get("favorite_count"),
        "reposts": legacy.get("retweet_count"),
        "replies": legacy.get("reply_count"),
        "quotes": legacy.get("quote_count"),
        "bookmarks": legacy.get("bookmark_count"),
    }
    views = _get(tweet, "views", "count")
    if views is not None:
        try:
            metrics["views"] = int(views)
        except (TypeError, ValueError):
            pass
    return {k: v for k, v in metrics.items() if v is not None}


def _media(tweet_id: str, entities: list) -> list[Media]:
    kinds = {"photo": "photo", "video": "video", "animated_gif": "gif"}
    media: list[Media] = []
    for position, entity in enumerate(entities):
        image_url = entity.get("media_url_https") or entity.get("media_url")
        if not image_url:
            continue
        key = entity.get("media_key") or entity.get("id_str") or f"{tweet_id}_{position}"
        media.append(
            Media(
                id=f"{PLATFORM}:{key}",
                kind=kinds.get(entity.get("type", ""), "photo"),
                remote_url=image_url,
                position=position,
                video_url=_best_video(entity),
                width=_get(entity, "original_info", "width"),
                height=_get(entity, "original_info", "height"),
                alt_text=entity.get("ext_alt_text") or None,
            )
        )
    return media


def _best_video(entity: dict) -> str | None:
    variants = _get(entity, "video_info", "variants") or []
    mp4 = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
    if not mp4:
        return None
    return max(mp4, key=lambda v: v.get("bitrate") or 0)["url"]


def _links(url_entities: list) -> list[Link]:
    links: list[Link] = []
    seen: set[str] = set()
    for entity in url_entities:
        url = entity.get("expanded_url") or entity.get("url")
        if url and url not in seen:
            seen.add(url)
            links.append(Link(url=url, display_url=entity.get("display_url")))
    return links


def _apply_card(tweet: dict, post: Post) -> None:
    bindings = _card_bindings(_get(tweet, "card", "legacy", "binding_values"))
    if not bindings:
        return

    for key in _CARD_IMAGE_KEYS:
        image = _get(bindings.get(key), "image_value")
        if isinstance(image, dict) and image.get("url"):
            post.media.append(
                Media(
                    id=f"{PLATFORM}:card:{post.native_id}",
                    kind="card",
                    remote_url=image["url"],
                    position=len(post.media),
                    width=image.get("width"),
                    height=image.get("height"),
                    alt_text=_get(bindings.get(key), "image_value", "alt"),
                )
            )
            break

    title = _get(bindings.get("title"), "string_value")
    description = _get(bindings.get("description"), "string_value")
    card_url = _get(bindings.get("card_url"), "string_value")
    if not (title or description):
        return
    target = next((link for link in post.links if card_url and link.url == card_url), None)
    target = target or (post.links[0] if post.links else None)
    if target is None and card_url:
        target = Link(url=card_url)
        post.links.append(target)
    if target is not None:
        target.title = target.title or title
        target.description = target.description or description


def _card_bindings(values: Any) -> dict:
    if isinstance(values, dict):
        return values
    if isinstance(values, list):
        return {
            item["key"]: item.get("value", {})
            for item in values
            if isinstance(item, dict) and "key" in item
        }
    return {}


def _apply_article(tweet: dict, post: Post) -> None:
    article = _get(tweet, "article", "article_results", "result")
    if not isinstance(article, dict):
        return
    post.article_title = article.get("title")
    nodes = _collect_text(article.get("content_state"))
    post.article_text = "\n\n".join(dict.fromkeys(nodes)) or article.get("preview_text")
    cover = _get(article, "cover_media", "media_info", "original_img_url")
    if cover:
        post.media.append(
            Media(
                id=f"{PLATFORM}:article:{post.native_id}",
                kind="photo",
                remote_url=cover,
                position=len(post.media),
            )
        )


def _collect_text(value: Any) -> list[str]:
    """Collect text leaves from an X Article content_state (from x-bookmarks-mcp)."""
    found: list[str] = []
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str) and text.strip():
            found.append(text.strip())
        for child in value.values():
            found.extend(_collect_text(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_collect_text(child))
    return found


# --- bookmark folders ------------------------------------------------------------


def _parse_folders(payload: dict) -> list[Folder]:
    slice_ = _find_key(payload.get("data", {}), "bookmark_collections_slice")
    if not isinstance(slice_, dict):
        return []
    items = slice_.get("items") or []
    folders: list[Folder] = []
    for item in items:
        folder = _get(item, "content", "itemContent", "bookmark_collection") or item
        if not isinstance(folder, dict):
            continue
        folder_id = folder.get("id") or folder.get("rest_id")
        name = folder.get("name") or folder.get("collection_name")
        if folder_id and name:
            folders.append(Folder(PLATFORM, str(folder_id), str(name)))
    return folders


# --- helpers ---------------------------------------------------------------------


def _request_variable(request_url: str | None, name: str) -> str | None:
    if not request_url:
        return None
    raw = parse_qs(urlparse(request_url).query).get("variables", [None])[0]
    if not raw:
        return None
    try:
        value = json.loads(raw).get(name)
    except (ValueError, AttributeError):
        return None
    return str(value) if value else None


def _parse_date(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return None
    return parsed.astimezone(UTC).isoformat()


def _prefixed(native_id: Any) -> str | None:
    return f"{PLATFORM}:{native_id}" if native_id else None


def _get(value: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _first(*values: Any) -> str | None:
    for value in values:
        if value:
            return str(value)
    return None


def _find_key(value: Any, key: str, depth: int = 0) -> Any:
    if depth > 12:
        return None
    if isinstance(value, dict):
        if key in value:
            return value[key]
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return None
    for child in children:
        found = _find_key(child, key, depth + 1)
        if found is not None:
            return found
    return None
