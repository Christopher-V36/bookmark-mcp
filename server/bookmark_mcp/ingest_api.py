"""Local HTTP endpoint the browser extension sends captured responses to.

Security model (it only ever listens on 127.0.0.1):
- every request needs the pairing token (``Authorization: Bearer <token>``);
- requests coming from web pages are refused (their ``Origin`` is http/https;
  the extension's is ``chrome-extension://...``), and no CORS headers are sent;
- the ``Host`` header must be the loopback address (blocks DNS rebinding);
- bodies are size-limited.
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bookmark_mcp import __version__
from bookmark_mcp.db import Database
from bookmark_mcp.ingest import IngestSummary, ingest_event, is_supported

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 20 * 1024 * 1024
EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "safari-web-extension://")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")


class IngestServer:
    def __init__(
        self,
        db: Database,
        token: str,
        host: str = "127.0.0.1",
        port: int = 0,
        on_ingested: Callable[[IngestSummary], None] | None = None,
    ) -> None:
        self.db = db
        self.token = token
        self.on_ingested = on_ingested
        handler = type("BoundHandler", (_Handler,), {"app": self})
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.httpd.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    def serve_forever(self) -> None:
        logger.info("Ingest API listening on http://127.0.0.1:%s", self.port)
        self.httpd.serve_forever()

    def start(self) -> IngestServer:
        self._thread = threading.Thread(target=self.serve_forever, daemon=True, name="ingest-api")
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class _Handler(BaseHTTPRequestHandler):
    app: IngestServer
    server_version = "bookmark-mcp"

    def log_message(self, fmt: str, *args) -> None:  # stdout belongs to MCP; use logging
        logger.debug("%s - %s", self.address_string(), fmt % args)

    # --- checks -------------------------------------------------------------------

    def _reply(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in LOOPBACK_HOSTS:
            self._reply(403, {"ok": False, "error": "forbidden host"})
            return False
        origin = self.headers.get("Origin")
        if origin and not origin.startswith(EXTENSION_ORIGINS):
            self._reply(403, {"ok": False, "error": "forbidden origin"})
            return False
        auth = self.headers.get("Authorization", "")
        supplied = auth[7:] if auth.startswith("Bearer ") else ""
        if not hmac.compare_digest(supplied.encode(), self.app.token.encode()):
            self._reply(401, {"ok": False, "error": "missing or wrong pairing token"})
            return False
        return True

    # --- routes -------------------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802 - no CORS for web pages
        self._reply(403, {"ok": False, "error": "forbidden"})

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/health":
            self._reply(404, {"ok": False, "error": "not found"})
            return
        if not self._allowed():
            return
        stats = self.app.db.stats()
        self._reply(
            200,
            {
                "ok": True,
                "app": "bookmark-mcp",
                "version": __version__,
                "posts": stats["posts"],
                "collections": stats["collections"],
                "last_capture": stats["last_capture"],
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/ingest":
            self._reply(404, {"ok": False, "error": "not found"})
            return
        if not self._allowed():
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._reply(411, {"ok": False, "error": "Content-Length required"})
            return
        if length > MAX_BODY_BYTES:
            self._reply(413, {"ok": False, "error": "body too large"})
            return
        try:
            body = json.loads(self.rfile.read(length))
            platform = str(body["platform"])
            operation = str(body["operation"])
            payload = body["payload"]
        except (ValueError, KeyError, TypeError):
            self._reply(
                400, {"ok": False, "error": "expected JSON with platform, operation, payload"}
            )
            return

        if not is_supported(platform, operation):
            self._reply(200, {"ok": True, "status": "ignored"})
            return

        summary = ingest_event(
            self.app.db,
            platform,
            operation,
            payload,
            url=_str_or_none(body.get("url")),
            page_url=_str_or_none(body.get("page_url")),
            captured_at=_str_or_none(body.get("captured_at")),
        )
        if self.app.on_ingested is not None:
            try:
                self.app.on_ingested(summary)
            except Exception:  # never fail the capture because of a follow-up job
                logger.exception("on_ingested callback failed")
        self._reply(200, {"ok": summary.status != "error", **summary.as_dict()})


def _str_or_none(value: object) -> str | None:
    return str(value)[:4000] if value else None
