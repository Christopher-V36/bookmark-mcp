"""Platform-agnostic records produced by parsers and stored by the database."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Media:
    id: str
    kind: str  # photo | video | gif | card
    remote_url: str  # the image we can download (photo, video poster or card image)
    position: int = 0
    video_url: str | None = None
    width: int | None = None
    height: int | None = None
    alt_text: str | None = None


@dataclass
class Link:
    url: str
    display_url: str | None = None
    title: str | None = None
    description: str | None = None


@dataclass
class Post:
    id: str  # "<platform>:<native id>"
    platform: str
    native_id: str
    url: str
    text: str = ""
    author_id: str | None = None
    author_handle: str | None = None
    author_name: str | None = None
    author_avatar_url: str | None = None
    lang: str | None = None
    created_at: str | None = None  # ISO 8601, UTC
    reply_to_id: str | None = None
    conversation_id: str | None = None
    quoted_post_id: str | None = None
    article_title: str | None = None
    article_text: str | None = None
    metrics: dict = field(default_factory=dict)
    raw: dict | None = None
    media: list[Media] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)


@dataclass
class Membership:
    post_id: str
    collection: str  # "bookmarks" | "likes" | "folder:<id>"
    sort_index: str | None = None


@dataclass
class Folder:
    platform: str
    folder_id: str
    name: str


@dataclass
class ParseResult:
    posts: list[Post] = field(default_factory=list)
    memberships: list[Membership] = field(default_factory=list)
    folders: list[Folder] = field(default_factory=list)
    skipped: int = 0  # tombstones, unavailable posts, unknown entries
