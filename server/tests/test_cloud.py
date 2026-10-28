import io
import json

import httpx
import pytest
from PIL import Image

from bookmark_mcp import cloud, media, triage
from bookmark_mcp.ingest import ingest_event
from tests import x_samples as s


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (30, 20), (1, 2, 3)).save(buffer, "PNG")
    return buffer.getvalue()


class FakeCloud:
    """Minimal stand-in for the Worker's /sync endpoints."""

    def __init__(self):
        self.posts: dict[str, dict] = {}
        self.images: dict[str, bytes] = {}
        self.edits: list[dict] = []
        self.meta: dict = {}
        self.fail_post: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != "Bearer tok-" + "x" * 40:
            return httpx.Response(401, json={"error": "wrong sync token"})
        path = request.url.path
        if path == "/sync/changes":
            since = request.url.params.get("since", "")
            new = [e for e in self.edits if e["edited_at"] > since]
            last = new[-1]["edited_at"] if new else since
            return httpx.Response(200, json={"posts": new, "next_since": last, "more": False})
        if path == "/sync/meta":
            self.meta = json.loads(request.content)
            return httpx.Response(200, json={"ok": True})
        if path == "/sync/post":
            post = json.loads(request.content)["post"]
            if post["id"] in self.fail_post:
                return httpx.Response(400, json={"error": "bad post"})
            self.posts[post["id"]] = post
            missing = [m["id"] for m in post["media"] if m["id"] not in self.images]
            return httpx.Response(
                200, json={"ok": True, "kept_web_edits": False, "missing_media": missing}
            )
        if path.startswith("/sync/media/"):
            media_id = httpx.URL(request.url).path.split("/sync/media/")[1].replace("%3A", ":")
            self.images[media_id] = request.content
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def setup(db, monkeypatch):
    ingest_event(db, "x", "Bookmarks", s.rich_bookmarks())
    triage.save_labels(
        db,
        [
            {
                "post_id": "x:100",
                "intent": "learn",
                "usefulness": 3,
                "topics": ["ai-agents"],
                "summary": "Agents",
            }
        ],
    )
    images = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, headers={"content-type": "image/png"}, content=_png())
        )
    )
    media.download_pending(db, images)
    cloud.save_settings({"url": "https://example.workers.dev", "sync_token": "tok-" + "x" * 40})
    fake = FakeCloud()
    client = cloud.CloudClient(
        "https://example.workers.dev",
        "tok-" + "x" * 40,
        httpx.Client(transport=httpx.MockTransport(fake.handler)),
    )
    return db, fake, client


def test_first_sync_pushes_saved_posts_quotes_and_images(setup):
    db, fake, client = setup
    result = cloud.sync(db, client)
    assert result.errors == []
    # 7 saved posts + the quoted post; quoted posts go first
    assert result.pushed == 8 and set(fake.posts) >= {"x:100", "x:200"}
    assert result.images == 4 and set(fake.images) == {
        m for p in fake.posts.values() for m in [i["id"] for i in p["media"]]
    }
    post = fake.posts["x:100"]
    assert post["labels"]["intent"] == "learn" and post["labels"]["topics"] == ["ai-agents"]
    assert post["collections"][0]["collection"] == "bookmarks"
    assert post["media"][0]["sha"]


def test_second_sync_only_pushes_changes(setup):
    db, fake, client = setup
    cloud.sync(db, client)
    assert cloud.sync(db, client).pushed == 0
    db.set_note("x:102", "try this weekend")
    result = cloud.sync(db, client)
    assert result.pushed == 1 and fake.posts["x:102"]["note"] == "try this weekend"


def test_phone_edits_are_pulled_and_applied(setup):
    db, fake, client = setup
    cloud.sync(db, client)
    fake.edits.append(
        {
            "id": "x:100",
            "edited_at": "2026-10-27T10:00:00+00:00",
            "labels": {
                "intent": "try",
                "usefulness": 2,
                "effort": "quick",
                "summary": "Agents",
                "action": "Do it",
                "status": "doing",
                "topics": ["ai-agents", "mcp"],
            },
            "tags": ["phone"],
            "note": "from my phone",
        }
    )
    result = cloud.sync(db, client)
    assert result.pulled == 1
    labels = triage.get_labels(db, "x:100")
    assert (labels["intent"], labels["status"], labels["topics"]) == (
        "try",
        "doing",
        ["ai-agents", "mcp"],
    )
    assert db.get_tags("x:100") == ["phone"] and db.get_post("x:100")["note"] == "from my phone"
    assert cloud.load_settings()["pulled_at"] == "2026-10-27T10:00:00+00:00"
    assert cloud.sync(db, client).pulled == 0  # not applied twice


def test_failures_are_retried_next_time(setup):
    db, fake, client = setup
    fake.fail_post.add("x:101")
    result = cloud.sync(db, client)
    assert any("x:101" in e for e in result.errors)
    fake.fail_post.clear()
    assert cloud.sync(db, client).pushed == 1


def test_wrong_token_is_reported(setup):
    db, fake, _ = setup
    bad = cloud.CloudClient(
        "https://example.workers.dev",
        "nope",
        httpx.Client(transport=httpx.MockTransport(fake.handler)),
    )
    with pytest.raises(cloud.CloudError, match="401"):
        cloud.sync(db, bad)


def test_password_hash_matches_pbkdf2():
    import hashlib

    digest, salt = cloud.password_hash("correct horse battery", iterations=10_000)
    expected = hashlib.pbkdf2_hmac("sha256", b"correct horse battery", bytes.fromhex(salt), 10_000)
    assert digest == expected.hex() and len(salt) == 32


def test_settings_survive_concurrent_readers_and_writers():
    import threading

    cloud.save_settings({"url": "https://x.workers.dev", "sync_token": "t" * 43})
    seen_missing = []

    def write():
        for i in range(200):
            cloud.update_settings(pulled_at=f"2026-10-28T00:00:{i % 60:02d}")

    def read():
        for _ in range(400):
            settings = cloud.load_settings()
            if settings.get("url") != "https://x.workers.dev" or not settings.get("sync_token"):
                seen_missing.append(settings)

    threads = [threading.Thread(target=write)] + [threading.Thread(target=read) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen_missing == []
    assert cloud.load_settings()["sync_token"] == "t" * 43
