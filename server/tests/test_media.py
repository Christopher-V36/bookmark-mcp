import io

import httpx
from PIL import Image

from bookmark_mcp import config, media
from bookmark_mcp.ingest import ingest_event
from tests import x_samples as s


def _png(width=3000, height=2000, mode="RGBA") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (width, height), (200, 30, 30, 255) if mode == "RGBA" else 128).save(
        buffer, "PNG"
    )
    return buffer.getvalue()


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_download_url_requests_large_rendition():
    assert media.download_url("https://pbs.twimg.com/media/IMG3_1.jpg") == (
        "https://pbs.twimg.com/media/IMG3_1?format=jpg&name=large"
    )
    card = "https://pbs.twimg.com/card_img/999/abc?format=jpg&name=orig"
    assert media.download_url(card) == card


def test_downloads_originals_and_ai_copies(db):
    ingest_event(db, "x", "Bookmarks", s.rich_bookmarks())
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "image/png"}, content=_png())

    counts = media.download_pending(db, _client(handler))
    assert counts == {"done": 4, "error": 0, "skipped": 0}  # photo, video poster, quoted, card
    assert all("twimg.com" in url for url in requested)

    [row] = db.get_media("x:100")
    assert row["status"] == "done"
    original = config.data_dir() / row["local_path"]
    ai_copy = config.data_dir() / row["ai_path"]
    assert original.exists() and original.suffix == ".png"
    with Image.open(ai_copy) as image:
        assert image.format == "JPEG" and max(image.size) == media.AI_MAX_SIDE

    # nothing left to do on a second pass
    assert media.download_pending(db, _client(handler)) == {"done": 0, "error": 0, "skipped": 0}


def test_rejects_non_images_oversize_and_foreign_redirects(db, monkeypatch):
    ingest_event(db, "x", "Bookmarks", s.rich_bookmarks())
    monkeypatch.setattr(media, "MAX_IMAGE_BYTES", 10_000)

    def handler(request):
        if "IMG3_1" in str(request.url):
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")
        if "poster" in str(request.url):
            return httpx.Response(302, headers={"location": "https://evil.example/x.png"})
        if "evil.example" in str(request.url):
            return httpx.Response(200, headers={"content-type": "image/png"}, content=_png(5, 5))
        return httpx.Response(200, headers={"content-type": "image/png"}, content=_png())

    counts = media.download_pending(db, _client(handler))
    assert counts == {"done": 0, "error": 4, "skipped": 0}
    errors = {row["post_id"]: row["error"] for row in db.pending_media(10, retry_errors=True)}
    assert "not an image" in errors["x:100"]
    assert "allowlist" in errors["x:101"]
    assert "size limit" in errors["x:200"]


def test_disallowed_source_host_is_skipped(db):
    post = s.tweet(
        "900", "x", media=[{**s.photo("9_9"), "media_url_https": "https://evil.example/a.jpg"}]
    )
    ingest_event(db, "x", "Bookmarks", s.bookmarks_payload([s.entry(post, "1")]))
    counts = media.download_pending(db, _client(lambda r: httpx.Response(500)))
    assert counts == {"done": 0, "error": 0, "skipped": 1}
