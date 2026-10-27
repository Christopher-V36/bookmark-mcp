import http.client
import io
import json

import httpx
import pytest
from PIL import Image

from bookmark_mcp import media, triage
from bookmark_mcp.ingest import ingest_event
from bookmark_mcp.webapp import WebServer
from tests import x_samples as s

CHANGE = {"X-Bookmark-Mcp": "1", "Content-Type": "application/json"}


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (0, 128, 0)).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def web(db):
    ingest_event(db, "x", "Bookmarks", s.rich_bookmarks())
    ingest_event(db, "x", "Likes", s.likes_payload([s.entry(s.tweet("400", "A meme"), "1")]))
    triage.save_labels(
        db,
        [
            {
                "post_id": "x:100",
                "intent": "learn",
                "usefulness": 3,
                "effort": "session",
                "topics": ["ai-agents"],
                "summary": "Agent loops explained",
                "action": "Read it",
            },
            {
                "post_id": "x:102",
                "intent": "try",
                "usefulness": 2,
                "effort": "quick",
                "topics": ["mcp"],
                "summary": "MCP with images",
                "action": "Install it",
            },
            {"post_id": "x:400", "intent": "noise", "usefulness": 0, "summary": "A meme"},
        ],
    )
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, headers={"content-type": "image/png"}, content=_png())
        )
    )
    media.download_pending(db, client)
    server = WebServer(db, port=0).start()
    yield server
    server.stop()


def call(server, method, path, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    data = json.dumps(body).encode() if body is not None else None
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", host or f"127.0.0.1:{server.port}")
    for key, value in (headers or {}).items():
        conn.putheader(key, value)
    conn.putheader("Content-Length", str(len(data or b"")))
    conn.endheaders(data)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    kind = response.getheader("Content-Type", "")
    return response.status, (json.loads(raw) if "json" in kind else raw), response


def test_serves_app_with_strict_csp(web):
    status, body, response = call(web, "GET", "/")
    assert status == 200 and b"bookmark-mcp" in body
    assert "script-src 'self'" in response.getheader("Content-Security-Policy")
    assert call(web, "GET", "/app.js")[0] == 200
    assert call(web, "GET", "/fonts/literata.woff2")[0] == 200
    assert call(web, "GET", "/board")[0] == 200  # client-side route falls back to the app


def test_overview(web):
    _, data, _ = call(web, "GET", "/api/overview")
    assert data["saved_posts"] == 8 and data["labeled"] == 3
    assert data["intents"] == {"learn": 1, "noise": 1, "try": 1}
    assert {c["key"] for c in data["collections"]} == {"bookmarks", "likes"}


def test_posts_filters_hide_noise_and_search(web):
    _, data, _ = call(web, "GET", "/api/posts")
    ids = [i["id"] for i in data["items"]]
    assert data["total"] == 7 and "x:400" not in ids and "x:200" not in ids
    _, data, _ = call(web, "GET", "/api/posts?hide_noise=false")
    assert data["total"] == 8
    _, data, _ = call(web, "GET", "/api/posts?min_usefulness=2&sort=useful")
    assert [i["id"] for i in data["items"]] == ["x:100", "x:102"]
    first = data["items"][0]
    assert first["summary"] == "Agent loops explained" and first["topics"] == ["ai-agents"]
    assert first["thumb"].startswith("/media/x/100/")
    _, data, _ = call(web, "GET", "/api/posts?q=loops")
    assert [i["id"] for i in data["items"]] == ["x:100"]
    _, data, _ = call(web, "GET", "/api/posts?collection=likes&intent=noise")
    assert [i["id"] for i in data["items"]] == ["x:400"]
    _, data, _ = call(web, "GET", "/api/posts?limit=2&offset=2")
    assert len(data["items"]) == 2 and data["offset"] == 2


def test_detail_and_media(web):
    _, post, _ = call(web, "GET", "/api/posts/x:103")
    assert post["quoted"]["author"] == "carol"
    image = post["quoted"]["media"][0]["image"]
    status, body, response = call(web, "GET", image)
    assert status == 200 and response.getheader("Content-Type") == "image/jpeg"
    assert call(web, "GET", "/api/posts/x:999")[0] == 404


def test_media_path_traversal_is_blocked(web):
    for path in (
        "/media/../bookmark-mcp.db",
        "/media/..%2Fbookmark-mcp.db",
        "/media/x/../../token.txt",
        "/../server/pyproject.toml",
    ):
        assert call(web, "GET", path)[0] == 404


def test_edit_labels_status_note_and_tags(web):
    status, data, _ = call(
        web,
        "PATCH",
        "/api/posts/x:104/labels",
        {"intent": "build", "usefulness": 3, "topics": ["side-project"]},
        CHANGE,
    )
    assert status == 200 and data["labels"]["intent"] == "build"
    status, data, _ = call(web, "PATCH", "/api/posts/x:104/labels", {"action": "Clone it"}, CHANGE)
    assert data["labels"]["usefulness"] == 3 and data["labels"]["action"] == "Clone it"
    status, data, _ = call(web, "PUT", "/api/posts/x:104/status", {"status": "doing"}, CHANGE)
    assert data["labels"]["status"] == "doing"
    status, data, _ = call(web, "PUT", "/api/posts/x:104/note", {"note": "weekend"}, CHANGE)
    assert data["note"] == "weekend"
    status, data, _ = call(web, "POST", "/api/posts/x:104/tags", {"tag": "Must Read"}, CHANGE)
    assert data["tags"] == ["must-read"]
    status, data, _ = call(web, "DELETE", "/api/posts/x:104/tags/must-read", {}, CHANGE)
    assert data["tags"] == []
    assert call(web, "GET", "/api/posts?q=weekend")[1]["items"][0]["id"] == "x:104"


def test_edit_validation(web):
    assert call(web, "PATCH", "/api/posts/x:101/labels", {"action": "x"}, CHANGE)[0] == 400
    assert call(web, "PATCH", "/api/posts/x:100/labels", {"intent": "maybe"}, CHANGE)[0] == 400
    assert call(web, "PUT", "/api/posts/x:101/status", {"status": "done"}, CHANGE)[0] == 409


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "application/json"},  # missing custom header
        {**CHANGE, "Origin": "https://evil.example"},
        {**CHANGE, "Sec-Fetch-Site": "cross-site"},
        {"X-Bookmark-Mcp": "1", "Content-Type": "text/plain"},
    ],
)
def test_changes_from_other_sites_are_refused(web, headers):
    status, _, _ = call(web, "PUT", "/api/posts/x:100/status", {"status": "done"}, headers)
    assert status in (403, 415)
    assert triage.get_labels(web.db, "x:100")["status"] == "todo"


def test_cross_site_reads_and_rebinding_are_refused(web):
    assert call(web, "GET", "/api/overview", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert call(web, "GET", "/api/overview", host="evil.example:80")[0] == 403
    assert call(web, "GET", "/api/overview", headers={"Sec-Fetch-Site": "same-origin"})[0] == 200


# --- phone mode (--lan) ---------------------------------------------------------------


@pytest.fixture
def lan_web(db):
    ingest_event(db, "x", "Bookmarks", s.rich_bookmarks())
    triage.save_labels(db, [{"post_id": "x:100", "intent": "learn", "usefulness": 3}])
    # trust_loopback=False makes requests from the test behave like another device
    server = WebServer(db, port=0, lan=True, code="phone-code-123", trust_loopback=False).start()
    yield server
    server.stop()


def _cookie(response) -> str:
    return response.getheader("Set-Cookie").split(";")[0]


def test_other_devices_must_log_in(lan_web):
    status, _, response = call(lan_web, "GET", "/", host="192.168.1.20:48924")
    assert status == 303 and response.getheader("Location") == "/login"
    assert call(lan_web, "GET", "/api/overview", host="192.168.1.20:48924")[0] == 401
    assert call(lan_web, "GET", "/media/x/100/a.jpg", host="192.168.1.20:48924")[0] == 401
    assert call(lan_web, "PUT", "/api/posts/x:100/status", {"status": "done"}, CHANGE)[0] == 401
    status, body, _ = call(lan_web, "GET", "/login")
    assert status == 200 and b"Log in" in body
    assert call(lan_web, "GET", "/app.css")[0] == 200  # public assets for the login page


def test_wrong_code_is_rejected(lan_web):
    status, _, response = call(lan_web, "GET", "/login?code=nope")
    assert status == 303 and response.getheader("Location") == "/login?error=1"
    assert response.getheader("Set-Cookie") is None
    status, body, _ = call(lan_web, "GET", "/login?error=1")
    assert b"didn't match" in body and b"hidden data-error" not in body
    bad = {"Cookie": "bmcp_session=forged"}
    assert call(lan_web, "GET", "/api/overview", headers=bad)[0] == 401


def test_code_logs_in_with_strict_cookie(lan_web):
    host = "192.168.1.20:48924"
    status, _, response = call(lan_web, "GET", "/login?code=phone-code-123", host=host)
    assert status == 303 and response.getheader("Location") == "/"
    cookie_header = response.getheader("Set-Cookie")
    assert "HttpOnly" in cookie_header and "SameSite=Strict" in cookie_header
    auth = {"Cookie": _cookie(response)}
    assert call(lan_web, "GET", "/", headers=auth, host=host)[0] == 200
    status, data, _ = call(lan_web, "GET", "/api/overview", headers=auth, host=host)
    assert status == 200 and data["saved_posts"] == 7
    status, data, _ = call(
        lan_web,
        "PUT",
        "/api/posts/x:100/status",
        {"status": "doing"},
        {**CHANGE, **auth, "Origin": f"http://{host}"},
        host=host,
    )
    assert status == 200 and data["labels"]["status"] == "doing"
    # another site cannot use the phone's session
    status, _, _ = call(
        lan_web,
        "PUT",
        "/api/posts/x:100/status",
        {"status": "done"},
        {**CHANGE, **auth, "Origin": "http://evil.example"},
        host=host,
    )
    assert status == 403
    # the setup page with the code is only for the computer itself
    assert call(lan_web, "GET", "/api/phone", headers=auth, host=host)[0] == 403


def test_without_lan_other_devices_are_refused(db):
    server = WebServer(db, port=0, trust_loopback=False).start()
    try:
        assert call(server, "GET", "/login?code=")[0] == 200
        assert call(server, "GET", "/api/overview")[0] == 401
    finally:
        server.stop()


def test_phone_info_on_the_computer(db, monkeypatch):
    from bookmark_mcp import webapp

    monkeypatch.setattr(webapp, "lan_addresses", lambda: ["192.168.1.50"])
    server = WebServer(db, port=0, lan=True, code="abc").start()
    try:
        status, info, _ = call(server, "GET", "/api/phone")
        assert status == 200 and info["lan"] is True
        assert info["urls"] == [f"http://192.168.1.50:{server.port}/"]
        assert info["entries"][0]["kind"] == "wifi"
        assert info["login_url"].endswith("/login?code=abc")
        assert info["qr"].startswith("data:image/svg+xml")
    finally:
        server.stop()
    local_only = WebServer(db, port=0).start()
    try:
        assert call(local_only, "GET", "/api/phone")[1] == {
            "lan": False,
            "urls": [],
            "command": "bookmark-mcp web --lan",
        }
    finally:
        local_only.stop()
