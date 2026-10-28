"""Download post images so the AI can look at them.

For every media row we keep the original image and an "AI copy" resized to at
most 1568 px on the long side (the size Claude reads best without wasting
tokens). Only the platforms' public image CDNs are allowed as sources.
"""

from __future__ import annotations

import io
import logging
import re
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from PIL import Image

from bookmark_mcp import config
from bookmark_mcp.db import Database

logger = logging.getLogger(__name__)

ALLOWED_HOSTS = {"pbs.twimg.com", "video.twimg.com"}
MAX_IMAGE_BYTES = 15 * 1024 * 1024
AI_MAX_SIDE = 1568
USER_AGENT = "bookmark-mcp/0.1 (+https://github.com/Christopher-V36/bookmark-mcp)"


def download_url(remote_url: str) -> str:
    """Ask the X CDN for a large rendition (``name=large``) of photos."""
    parsed = urlparse(remote_url)
    if parsed.netloc != "pbs.twimg.com" or not parsed.path.startswith("/media/"):
        return remote_url
    query = dict(parse_qsl(parsed.query))
    path = parsed.path
    match = re.match(r"^(/media/[^.]+)\.(\w+)$", path)
    if match:
        path = match.group(1)
        query.setdefault("format", match.group(2))
    query["name"] = "large"
    return urlunparse(parsed._replace(path=path, query=urlencode(query)))


def _is_allowed(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "media"


def _fetch(client: httpx.Client, url: str) -> tuple[bytes, str]:
    with client.stream("GET", url) as response:
        response.raise_for_status()
        if not _is_allowed(str(response.url)):
            raise ValueError(f"redirected outside the allowlist: {response.url}")
        content_type = response.headers.get("content-type", "").split(";")[0].strip()
        if not content_type.startswith("image/"):
            raise ValueError(f"not an image: {content_type or 'unknown type'}")
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_IMAGE_BYTES:
                raise ValueError("image larger than the size limit")
            chunks.append(chunk)
    return b"".join(chunks), content_type


def _ai_copy(data: bytes, target: Path) -> None:
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")
        image.thumbnail((AI_MAX_SIDE, AI_MAX_SIDE))
        image.save(target, "JPEG", quality=85, optimize=True)


def download_one(db: Database, client: httpx.Client, row: dict) -> str:
    """Download one media row. Returns the new status."""
    url = download_url(row["remote_url"])
    if not _is_allowed(url):
        db.set_media_result(row["id"], "skipped", error="source host not allowed")
        return "skipped"
    try:
        data, content_type = _fetch(client, url)
        platform, _, key = row["id"].partition(":")
        post_native = row["post_id"].partition(":")[2]
        folder = config.media_dir() / _safe_name(platform) / _safe_name(post_native)
        folder.mkdir(parents=True, exist_ok=True)
        extension = {"image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(
            content_type, "jpg"
        )
        original = folder / f"{_safe_name(key)}.{extension}"
        ai_copy = folder / f"{_safe_name(key)}.ai.jpg"
        original.write_bytes(data)
        _ai_copy(data, ai_copy)
    except (httpx.HTTPError, ValueError, OSError) as exc:
        db.set_media_result(row["id"], "error", error=f"{type(exc).__name__}: {exc}"[:500])
        return "error"
    home = config.data_dir()
    db.set_media_result(
        row["id"],
        "done",
        local_path=original.relative_to(home).as_posix(),
        ai_path=ai_copy.relative_to(home).as_posix(),
    )
    return "done"


def download_pending(
    db: Database,
    client: httpx.Client | None = None,
    limit: int | None = None,
    retry_errors: bool = False,
) -> dict[str, int]:
    counts = {"done": 0, "error": 0, "skipped": 0}
    attempted: set[str] = set()
    own_client = client is None
    client = client or httpx.Client(
        timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )
    try:
        while limit is None or len(attempted) < limit:
            size = 50 if limit is None else min(50, limit - len(attempted))
            batch = [
                row
                for row in db.pending_media(size + len(attempted), retry_errors=retry_errors)
                if row["id"] not in attempted
            ][:size]
            if not batch:
                break
            for row in batch:
                attempted.add(row["id"])
                counts[download_one(db, client, row)] += 1
    finally:
        if own_client:
            client.close()
    return counts


def local_file(relative: str | None) -> Path | None:
    if not relative:
        return None
    path = config.data_dir() / relative
    return path if path.exists() else None


class MediaWorker:
    """Background thread that downloads new images after each capture."""

    def __init__(self, db: Database, after: Callable[[], None] | None = None) -> None:
        self.db = db
        self.after = after  # called after each round, e.g. to start a cloud sync
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="media-worker")
        self._thread.start()

    def schedule(self, *_args) -> None:
        self._wake.set()

    def _run(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            try:
                counts = download_pending(self.db)
                if any(counts.values()):
                    logger.info("Media download: %s", counts)
            except Exception:
                logger.exception("Media worker failed")
            if self.after is not None:
                self.after()
