"""Synthetic X GraphQL responses shaped like the real web-app payloads.

Everything here is made up: no real accounts, posts or images.
"""

from __future__ import annotations

import json
from urllib.parse import quote


def user(handle: str = "alice_dev", name: str = "Alice Dev", user_id: str = "1001") -> dict:
    return {
        "__typename": "User",
        "rest_id": user_id,
        "core": {"screen_name": handle, "name": name},
        "avatar": {"image_url": f"https://pbs.twimg.com/profile_images/{user_id}/a_normal.jpg"},
        "legacy": {},
    }


def legacy_user(handle: str = "old_bob", name: str = "Old Bob", user_id: str = "1002") -> dict:
    """Older payloads kept screen_name/name under legacy."""
    return {
        "__typename": "User",
        "rest_id": user_id,
        "legacy": {
            "screen_name": handle,
            "name": name,
            "profile_image_url_https": "https://pbs.twimg.com/profile_images/x/b_normal.jpg",
        },
    }


def photo(media_key: str = "3_1", alt: str | None = "A diagram of an agent loop") -> dict:
    return {
        "type": "photo",
        "media_key": media_key,
        "id_str": media_key.split("_")[-1],
        "url": "https://t.co/media111",
        "media_url_https": f"https://pbs.twimg.com/media/IMG{media_key}.jpg",
        "ext_alt_text": alt,
        "original_info": {"width": 1200, "height": 800},
    }


def video(media_key: str = "7_9") -> dict:
    return {
        "type": "video",
        "media_key": media_key,
        "url": "https://t.co/media222",
        "media_url_https": f"https://pbs.twimg.com/ext_tw_video_thumb/{media_key}/img/poster.jpg",
        "original_info": {"width": 1280, "height": 720},
        "video_info": {
            "variants": [
                {"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/a.m3u8"},
                {
                    "content_type": "video/mp4",
                    "bitrate": 256000,
                    "url": "https://video.twimg.com/low.mp4",
                },
                {
                    "content_type": "video/mp4",
                    "bitrate": 2176000,
                    "url": "https://video.twimg.com/high.mp4",
                },
            ]
        },
    }


def tweet(
    tweet_id: str,
    text: str = "Hello world",
    author: dict | None = None,
    media: list | None = None,
    urls: list | None = None,
    created_at: str = "Wed Oct 01 12:34:56 +0000 2026",
    **extra,
) -> dict:
    entities: dict = {"urls": urls or []}
    legacy = {
        "full_text": text,
        "created_at": created_at,
        "lang": "en",
        "favorite_count": 10,
        "retweet_count": 2,
        "reply_count": 1,
        "quote_count": 0,
        "bookmark_count": 5,
        "conversation_id_str": tweet_id,
        "entities": entities,
    }
    if media:
        entities["media"] = media
        legacy["extended_entities"] = {"media": media}
    legacy.update(extra.pop("legacy", {}))
    result = {
        "__typename": "Tweet",
        "rest_id": tweet_id,
        "core": {"user_results": {"result": author or user()}},
        "views": {"count": "1234"},
        "legacy": legacy,
    }
    result.update(extra)
    return result


def entry(result: dict | None, sort_index: str, entry_id: str | None = None) -> dict:
    return {
        "entryId": entry_id or f"tweet-{sort_index}",
        "sortIndex": sort_index,
        "content": {
            "entryType": "TimelineTimelineItem",
            "itemContent": {
                "__typename": "TimelineTweet",
                "tweet_results": {"result": result} if result is not None else {},
            },
        },
    }


def cursor(sort_index: str) -> dict:
    return {
        "entryId": f"cursor-bottom-{sort_index}",
        "sortIndex": sort_index,
        "content": {"entryType": "TimelineTimelineCursor", "value": "abc", "cursorType": "Bottom"},
    }


def timeline(entries: list) -> dict:
    return {"instructions": [{"type": "TimelineAddEntries", "entries": entries}]}


def bookmarks_payload(entries: list) -> dict:
    return {"data": {"bookmark_timeline_v2": {"timeline": timeline(entries)}}}


def likes_payload(entries: list) -> dict:
    return {"data": {"user": {"result": {"timeline": {"timeline": timeline(entries)}}}}}


def folder_payload(entries: list) -> dict:
    return {"data": {"bookmark_collection_timeline": {"timeline": timeline(entries)}}}


def folders_slice_payload(folders: list[tuple[str, str]]) -> dict:
    return {
        "data": {
            "viewer": {
                "user_results": {
                    "result": {
                        "bookmark_collections_slice": {
                            "items": [{"id": fid, "name": name} for fid, name in folders]
                        }
                    }
                }
            }
        }
    }


def graphql_url(operation: str, variables: dict | None = None) -> str:
    url = f"https://x.com/i/api/graphql/AbC123/{operation}"
    if variables is not None:
        url += "?variables=" + quote(json.dumps(variables))
    return url


def rich_bookmarks() -> dict:
    """A bookmarks page covering the tricky cases."""
    plain = tweet(
        "100",
        "Great thread on agents https://t.co/link1 https://t.co/media111",
        media=[photo("3_1")],
        urls=[
            {
                "url": "https://t.co/link1",
                "expanded_url": "https://example.com/agents",
                "display_url": "example.com/agents",
            }
        ],
    )
    hidden = {
        "__typename": "TweetWithVisibilityResults",
        "tweet": tweet(
            "101",
            "Limited visibility post https://t.co/media222",
            author=legacy_user(),
            media=[video("7_9")],
        ),
    }
    tombstone = {"__typename": "TweetTombstone", "tombstone": {"text": {"text": "Deleted"}}}
    long_post = tweet(
        "102",
        "Short version...",
        note_tweet={
            "note_tweet_results": {
                "result": {
                    "text": "This is the full long post about MCP servers and images. " * 3
                    + "Read https://t.co/long1",
                    "entity_set": {
                        "urls": [
                            {
                                "url": "https://t.co/long1",
                                "expanded_url": "https://docs.example.org/mcp",
                            }
                        ]
                    },
                }
            }
        },
    )
    quoted = tweet(
        "200",
        "The quoted original with a chart",
        author=user("carol", "Carol", "1003"),
        media=[photo("3_2", alt=None)],
    )
    quoting = tweet(
        "103",
        "Look at this chart",
        legacy={"is_quote_status": True},
        quoted_status_result={"result": quoted},
    )
    card = tweet(
        "104",
        "New blog post https://t.co/card1",
        urls=[{"url": "https://t.co/card1", "expanded_url": "https://blog.example.net/post"}],
        card={
            "legacy": {
                "binding_values": [
                    {"key": "title", "value": {"string_value": "How we built it"}},
                    {"key": "description", "value": {"string_value": "A deep dive"}},
                    {"key": "card_url", "value": {"string_value": "https://t.co/card1"}},
                    {
                        "key": "thumbnail_image_original",
                        "value": {
                            "image_value": {
                                "url": "https://pbs.twimg.com/card_img/999/abc?format=jpg&name=orig",
                                "width": 800,
                                "height": 419,
                            }
                        },
                    },
                ]
            }
        },
    )
    original = tweet("300", "Original being retweeted", author=user("dave", "Dave", "1004"))
    retweet = tweet(
        "105",
        "RT @dave: Original being retweeted",
        legacy={"retweeted_status_result": {"result": original}},
    )
    module = {
        "entryId": "bookmark-module-1",
        "sortIndex": "1799",
        "content": {
            "entryType": "TimelineTimelineModule",
            "items": [
                {
                    "item": {
                        "itemContent": {
                            "__typename": "TimelineTweet",
                            "tweet_results": {"result": tweet("106", "In a module")},
                        }
                    }
                },
            ],
        },
    }
    return bookmarks_payload(
        [
            entry(plain, "1806"),
            entry(hidden, "1805"),
            entry(tombstone, "1804"),
            entry(long_post, "1803"),
            entry(quoting, "1802"),
            entry(card, "1801"),
            entry(retweet, "1800"),
            module,
            entry(None, "1798"),
            cursor("1797"),
        ]
    )
