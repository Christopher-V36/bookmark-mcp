import http.client
import json

import pytest

from bookmark_mcp import ingest_api
from bookmark_mcp.ingest_api import IngestServer
from tests import x_samples as s

TOKEN = "test-token-123"
EXT_ORIGIN = "chrome-extension://abcdefghijklmnop"


@pytest.fixture
def server(db):
    received = []
    srv = IngestServer(db, TOKEN, port=0, on_ingested=received.append).start()
    srv.received = received
    yield srv
    srv.stop()


def _request(server, method, path, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    data = json.dumps(body).encode() if body is not None else None
    base = {"Content-Type": "application/json"}
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", host or f"127.0.0.1:{server.port}")
    for key, value in {**base, **(headers or {})}.items():
        conn.putheader(key, value)
    if data is not None:
        conn.putheader("Content-Length", str(len(data)))
    conn.endheaders(data)
    response = conn.getresponse()
    payload = json.loads(response.read() or b"{}")
    conn.close()
    return response.status, payload


def _capture():
    return {
        "version": 1,
        "platform": "x",
        "operation": "Bookmarks",
        "url": s.graphql_url("Bookmarks"),
        "page_url": "https://x.com/i/bookmarks",
        "captured_at": "2026-10-27T10:00:00Z",
        "payload": s.rich_bookmarks(),
    }


AUTH = {"Authorization": f"Bearer {TOKEN}", "Origin": EXT_ORIGIN}


def test_valid_capture_is_ingested(server, db):
    status, body = _request(server, "POST", "/ingest", _capture(), AUTH)
    assert status == 200 and body["ok"] and body["posts"] == 8
    assert db.stats()["posts"] == 8
    assert len(server.received) == 1 and server.received[0].new_in_collections == 7


def test_reply_says_how_many_posts_are_new(server):
    # the extension's "Load new" stops at the first page with nothing new
    _, first = _request(server, "POST", "/ingest", _capture(), AUTH)
    _, again = _request(server, "POST", "/ingest", _capture(), AUTH)
    assert first["new_in_collections"] == 7
    assert again["posts"] == 8 and again["new_in_collections"] == 0


def test_health_reports_counts(server):
    _request(server, "POST", "/ingest", _capture(), AUTH)
    status, body = _request(server, "GET", "/health", headers=AUTH)
    assert status == 200 and body["app"] == "bookmark-mcp" and body["posts"] == 8


@pytest.mark.parametrize(
    "headers, expected",
    [
        ({}, 401),
        ({"Authorization": "Bearer wrong", "Origin": EXT_ORIGIN}, 401),
        ({"Authorization": f"Bearer {TOKEN}", "Origin": "https://evil.example"}, 403),
    ],
)
def test_rejects_bad_token_or_web_origin(server, db, headers, expected):
    status, _ = _request(server, "POST", "/ingest", _capture(), headers)
    assert status == expected
    assert db.stats()["posts"] == 0


def test_rejects_dns_rebinding_host(server):
    status, body = _request(server, "GET", "/health", headers=AUTH, host="attacker.example:80")
    assert status == 403 and body["error"] == "forbidden host"


def test_rejects_oversized_body(server, monkeypatch):
    monkeypatch.setattr(ingest_api, "MAX_BODY_BYTES", 100)
    status, _ = _request(server, "POST", "/ingest", _capture(), AUTH)
    assert status == 413


def test_bad_json_and_unsupported_operation(server, db):
    status, _ = _request(server, "POST", "/ingest", {"hello": "world"}, AUTH)
    assert status == 400
    status, body = _request(
        server, "POST", "/ingest", {**_capture(), "operation": "HomeTimeline"}, AUTH
    )
    assert status == 200 and body["status"] == "ignored"
    assert db.stats()["events"] == {}


def test_no_cors_preflight(server):
    status, _ = _request(server, "OPTIONS", "/ingest", headers={"Origin": "https://evil.example"})
    assert status == 403


def test_a_second_instance_cannot_take_the_same_port(db):
    first = IngestServer(db, TOKEN, port=0)
    try:
        with pytest.raises(OSError):
            IngestServer(db, TOKEN, port=first.port)
    finally:
        first.httpd.server_close()
