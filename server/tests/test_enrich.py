import httpx

from bookmark_mcp import enrich
from bookmark_mcp.ingest import ingest_event
from tests import x_samples as s

ARTICLE = """<html><head><title>Agents explained</title></head><body><article>
<h1>Agents explained</h1>
<p>An agent loop calls tools until the task is done. This paragraph is long enough for the
extractor to keep it as the main content of the page, which is what we want to index.</p>
<p>Memory, planning and evaluation are the three hard parts of building reliable agents.</p>
</article></body></html>"""


def public_resolver(host, port):
    return {"evil-internal.example": ["10.0.0.5"]}.get(host, ["93.184.216.34"])


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _post_with_links(*urls):
    tweet = s.tweet(
        "700",
        "links",
        urls=[{"url": f"https://t.co/{i}", "expanded_url": u} for i, u in enumerate(urls)],
    )
    return s.bookmarks_payload([s.entry(tweet, "1")])


def test_enriches_articles_and_makes_them_searchable(db):
    ingest_event(db, "x", "Bookmarks", _post_with_links("https://blog.example/agents"))

    def handler(request):
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, text=ARTICLE
        )

    counts = enrich.enrich_pending(db, _client(handler), public_resolver, delay_seconds=0)
    assert counts == {"done": 1, "error": 0, "skipped": 0}
    [link] = db.get_links("x:700")
    assert link["title"] == "Agents explained" and "planning" in link["content"]
    assert [r["id"] for r in db.search("planning evaluation")] == ["x:700"]


def test_blocks_private_addresses_redirects_and_skips_x_links(db):
    ingest_event(
        db,
        "x",
        "Bookmarks",
        _post_with_links(
            "http://127.0.0.1:8080/admin",
            "https://evil-internal.example/",
            "https://redirector.example/go",
            "https://x.com/someone/status/1",
            "https://files.example/data.zip",
        ),
    )

    def handler(request):
        if request.url.host == "redirector.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
        return httpx.Response(200, headers={"content-type": "application/zip"}, content=b"PK")

    counts = enrich.enrich_pending(db, _client(handler), public_resolver, delay_seconds=0)
    assert counts == {"done": 0, "error": 4, "skipped": 1}
    errors = {row["url"]: row["error"] for row in db.get_links("x:700")}
    assert "non-public" in errors["http://127.0.0.1:8080/admin"]
    assert "non-public" in errors["https://evil-internal.example/"]
    assert "169.254.169.254" in errors["https://redirector.example/go"]
    assert "content type" in errors["https://files.example/data.zip"]


def test_detect_content_type():
    assert enrich.detect_content_type("https://github.com/a/b") == "repo"
    assert enrich.detect_content_type("https://docs.python.org/3/") == "docs"
    assert enrich.detect_content_type("https://youtu.be/x") == "video"
    assert enrich.detect_content_type("https://blog.example/post") == "article"
