"""Keep a cloud copy (Cloudflare Worker + D1) in sync with this computer.

The PC stays the place where posts are captured, parsed, labelled and their images
downloaded. ``sync`` first pulls the edits made in the web app (labels, status, notes,
tags) and applies them here, then pushes every saved post whose content changed since the
last push, plus any images the cloud does not have yet. Only the AI-sized image copies go
up, to stay well inside the free plan.

Settings live in ``<data folder>/cloud.json`` (the Worker URL and the private sync token).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import httpx

from bookmark_mcp import config, media, triage
from bookmark_mcp.db import Database

logger = logging.getLogger(__name__)
_settings_lock = threading.Lock()

PBKDF2_ITERATIONS = 60_000  # the Worker verifies it within the free plan's CPU budget
SYNC_INTERVAL_SECONDS = 300
LOCAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS cloud_posts (post_id TEXT PRIMARY KEY, hash TEXT NOT NULL,
                                        pushed_at TEXT NOT NULL);
"""


# --- settings ---------------------------------------------------------------------------


def settings_path() -> Path:
    return config.data_dir() / "cloud.json"


def load_settings() -> dict:
    """Read cloud.json. Other bookmark-mcp processes may be replacing it right now, so a
    failed read is retried instead of being mistaken for "not configured"."""
    path = settings_path()
    for _ in range(20):
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            time.sleep(0.05)
    raise CloudError(f"{path} is unreadable; fix or delete it, then run `cloud deploy` again.")


def save_settings(settings: dict) -> None:
    """Write cloud.json atomically: readers see the old or the new file, never half."""
    path = settings_path()
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    for attempt in range(20):
        try:
            os.replace(temp, path)
            return
        except PermissionError:  # Windows: another process is reading it this instant
            if attempt == 19:
                raise
            time.sleep(0.05)


def update_settings(**changes) -> dict:
    """Change some keys without dropping the others (sync token, URL...)."""
    with _settings_lock:
        settings = load_settings()
        settings.update(changes)
        save_settings(settings)
        return settings


def sync_token() -> str:
    settings = load_settings()
    if not settings.get("sync_token"):
        settings = update_settings(sync_token=secrets.token_urlsafe(32))
    return settings["sync_token"]


def is_configured() -> bool:
    settings = load_settings()
    return bool(settings.get("url") and settings.get("sync_token"))


# --- client -----------------------------------------------------------------------------


class CloudError(RuntimeError):
    pass


class CloudClient:
    def __init__(self, url: str, token: str, client: httpx.Client | None = None) -> None:
        self.url = url.rstrip("/")
        self.client = client or httpx.Client(timeout=60)
        self.headers = {"Authorization": f"Bearer {token}"}

    def _call(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self.client.request(
                method,
                self.url + path,
                headers={**self.headers, **kwargs.pop("headers", {})},
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise CloudError(f"cannot reach {self.url}: {exc}") from exc
        if response.status_code >= 400:
            try:
                message = response.json().get("error", response.text)
            except ValueError:
                message = response.text[:200]
            raise CloudError(f"{method} {path} failed ({response.status_code}): {message}")
        return response.json()

    def status(self) -> dict:
        return self._call("GET", "/sync/status")

    def changes(self, since: str) -> dict:
        return self._call("GET", "/sync/changes", params={"since": since})

    def push_post(self, post: dict, pulled_at: str) -> dict:
        return self._call("POST", "/sync/post", json={"post": post, "pulled_at": pulled_at})

    def push_meta(self, folders: list[dict], last_capture: str | None) -> dict:
        return self._call(
            "POST", "/sync/meta", json={"folders": folders, "last_capture": last_capture}
        )

    def put_media(self, media_id: str, data: bytes, sha: str) -> dict:
        return self._call(
            "PUT",
            "/sync/media/" + quote(media_id, safe=""),
            content=data,
            headers={"Content-Type": "image/jpeg", "X-Sha": sha},
        )

    def set_user(self, email: str, pw_hash: str, salt: str, iterations: int) -> dict:
        return self._call(
            "PUT",
            "/sync/user",
            json={
                "email": email,
                "pw_hash": pw_hash,
                "salt": salt,
                "iterations": iterations,
            },
        )


def client_from_settings(client: httpx.Client | None = None) -> CloudClient:
    settings = load_settings()
    if not settings.get("url"):
        raise CloudError("No cloud yet: run `bookmark-mcp cloud deploy` first.")
    return CloudClient(settings["url"], sync_token(), client)


def password_hash(password: str, iterations: int = PBKDF2_ITERATIONS) -> tuple[str, str]:
    salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations)
    return digest.hex(), salt


# --- building what to push --------------------------------------------------------------


def _ensure_schema(db: Database) -> None:
    with db._write_lock, db.conn:
        db.conn.executescript(LOCAL_SCHEMA)


def _file_sha(relative: str | None) -> str | None:
    path = media.local_file(relative)
    if path is None:
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:40]


def post_payload(db: Database, post_id: str) -> dict | None:
    post = db.get_post(post_id)
    if post is None:
        return None
    labels = triage.get_labels(db, post_id)
    collections = db._rows(
        "SELECT collection, sort_index FROM collections WHERE post_id=? ORDER BY collection",
        (post_id,),
    )
    return {
        "id": post_id,
        "url": post["url"],
        "author_handle": post["author_handle"],
        "author_name": post["author_name"],
        "created_at": post["created_at"],
        "text": post["text"],
        "article_title": post["article_title"],
        "article_text": post["article_text"],
        "quoted_post_id": post["quoted_post_id"],
        "metrics": post["metrics"],
        "note": post["note"],
        "collections": collections,
        "links": [
            {
                "url": link["url"],
                "title": link["title"],
                "description": link["description"],
                "content": (link["content"] or "")[:3000] or None,
            }
            for link in db.get_links(post_id)
        ],
        "labels": {
            k: labels[k]
            for k in ("intent", "usefulness", "effort", "summary", "action", "status", "topics")
        }
        if labels
        else None,
        "tags": db.get_tags(post_id),
        "media": [
            {
                "id": m["id"],
                "position": m["position"],
                "kind": m["kind"],
                "alt_text": m["alt_text"],
                "video_url": m["video_url"],
                "width": m["width"],
                "height": m["height"],
                "sha": _file_sha(m["ai_path"]),
            }
            for m in db.get_media(post_id)
        ],
    }


def _ids_to_sync(db: Database) -> list[str]:
    rows = db._rows(
        """SELECT DISTINCT p.id FROM posts p
           WHERE EXISTS (SELECT 1 FROM collections c WHERE c.post_id = p.id)
              OR p.id IN (SELECT q.quoted_post_id FROM posts q
                          WHERE q.quoted_post_id IS NOT NULL
                            AND EXISTS (SELECT 1 FROM collections c WHERE c.post_id = q.id))
           ORDER BY p.id"""
    )
    # quoted posts first, so a post's quote exists in the cloud when the post arrives
    ids = [r["id"] for r in rows]
    quoted = {
        r["quoted_post_id"]
        for r in db._rows("SELECT quoted_post_id FROM posts WHERE quoted_post_id IS NOT NULL")
    }
    return sorted(ids, key=lambda i: (i not in quoted, i))


def _hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


# --- pulling edits made in the web app ---------------------------------------------------


def apply_web_edit(db: Database, edit: dict) -> None:
    post_id = edit["id"]
    if db.get_post(post_id) is None:
        return
    labels = edit.get("labels")
    if labels:
        triage.save_labels(db, [{**labels, "post_id": post_id}], labeled_by="web")
        if labels.get("status"):
            triage.set_status(db, post_id, labels["status"])
    if (edit.get("note") or None) != (db.get_post(post_id).get("note") or None):
        db.set_note(post_id, edit.get("note") or "")
    wanted = set(edit.get("tags") or [])
    current = set(db.get_tags(post_id))
    for tag in wanted - current:
        db.add_tag(post_id, tag)
    for tag in current - wanted:
        db.remove_tag(post_id, tag)


@dataclass
class SyncResult:
    pulled: int = 0
    pushed: int = 0
    unchanged: int = 0
    images: int = 0
    kept_web_edits: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


_sync_lock = threading.Lock()


def sync(db: Database, client: CloudClient | None = None, push_images: bool = True) -> SyncResult:
    with _sync_lock:
        return _sync(db, client or client_from_settings(), push_images)


def _sync(db: Database, client: CloudClient, push_images: bool) -> SyncResult:
    _ensure_schema(db)
    result = SyncResult()
    settings = load_settings()
    pulled_at = settings.get("pulled_at", "")

    while True:
        page = client.changes(pulled_at)
        for edit in page["posts"]:
            try:
                apply_web_edit(db, edit)
                result.pulled += 1
            except Exception as exc:  # one bad edit must not stop the sync
                result.errors.append(f"pull {edit.get('id')}: {exc}")
        pulled_at = page.get("next_since") or pulled_at
        if not page.get("more"):
            break
    if pulled_at != settings.get("pulled_at", ""):
        update_settings(pulled_at=pulled_at)

    folders = [{"key": k, "name": v} for k, v in db.folder_names().items()]
    client.push_meta(folders, db.stats()["last_capture"])

    known = {r["post_id"]: r["hash"] for r in db._rows("SELECT post_id, hash FROM cloud_posts")}
    for post_id in _ids_to_sync(db):
        payload = post_payload(db, post_id)
        if payload is None:
            continue
        digest = _hash(payload)
        if known.get(post_id) == digest:
            result.unchanged += 1
            continue
        try:
            reply = client.push_post(payload, pulled_at)
        except CloudError as exc:
            result.errors.append(f"push {post_id}: {exc}")
            continue
        result.pushed += 1
        result.kept_web_edits += int(bool(reply.get("kept_web_edits")))
        complete = (
            _upload_missing_images(db, client, post_id, reply, result)
            if push_images
            else not reply.get("missing_media")
        )
        if complete:  # otherwise retry next time (e.g. an image is still downloading)
            with db._write_lock, db.conn:
                db.conn.execute(
                    "INSERT INTO cloud_posts (post_id, hash, pushed_at) VALUES (?, ?, "
                    "datetime('now')) ON CONFLICT(post_id) DO UPDATE SET hash = excluded.hash, "
                    "pushed_at = excluded.pushed_at",
                    (post_id, digest),
                )
    return result


def _upload_missing_images(
    db: Database, client: CloudClient, post_id: str, reply: dict, result: SyncResult
) -> bool:
    """Upload the images the cloud reported missing. True when none is left missing."""
    complete = True
    rows = {m["id"]: m for m in db.get_media(post_id)}
    for media_id in reply.get("missing_media") or []:
        row = rows.get(media_id)
        path = media.local_file(row["ai_path"]) if row else None
        if path is None:
            complete = False
            continue
        data = path.read_bytes()
        try:
            client.put_media(media_id, data, hashlib.sha256(data).hexdigest()[:40])
            result.images += 1
        except CloudError as exc:
            result.errors.append(f"image {media_id}: {exc}")
            complete = False
    return complete


class CloudSyncWorker:
    """Syncs in the background: every few minutes, and soon after new captures."""

    def __init__(self, db: Database, interval: int = SYNC_INTERVAL_SECONDS) -> None:
        self.db = db
        self.interval = interval
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="cloud-sync")
        self._thread.start()

    def schedule(self, *_args) -> None:
        self._wake.set()

    def _run(self) -> None:
        while True:
            self._wake.wait(self.interval)
            self._wake.clear()
            if not is_configured():
                continue
            try:
                result = sync(self.db)
                if result.pulled or result.pushed or result.errors:
                    logger.info("Cloud sync: %s", result.as_dict())
            except Exception as exc:
                logger.warning("Cloud sync failed: %s", exc)
