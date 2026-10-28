"""Local web app to browse and manage saved posts: ``bookmark-mcp web``.

Serves a small single-page interface plus a JSON API on 127.0.0.1. It reads and
writes the same database the MCP server uses, and shows the images downloaded
to the local media folder.

Security (browsers visit hostile pages too):
- by default binds to 127.0.0.1 and refuses non-loopback ``Host`` headers (DNS rebinding);
- with ``--lan`` other devices (your phone) can connect, but only after logging in
  with the web code (QR link or typed), which sets an HttpOnly, SameSite=Strict cookie;
- refuses cross-site API calls (``Sec-Fetch-Site``/``Origin``) and requires a
  custom header + JSON body for every change, so other websites cannot post;
- post content is rendered as text by the front end, and a strict
  Content-Security-Policy blocks scripts from anywhere else.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import mimetypes
import re
import socket
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import segno

from bookmark_mcp import __version__, config, triage
from bookmark_mcp.db import Database, fts_query
from bookmark_mcp.httpserver import LocalHTTPServer
from bookmark_mcp.tools import resolve_collection

logger = logging.getLogger(__name__)

DEFAULT_WEB_PORT = 48924
STATIC_DIR = Path(__file__).parent / "web"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
LOOPBACK_CLIENTS = ("127.0.0.1", "::1", "::ffff:127.0.0.1")
SESSION_COOKIE = "bmcp_session"
SESSION_DAYS = 30
CHANGE_HEADER = "X-Bookmark-Mcp"
MAX_BODY = 64 * 1024
SORTS = {
    "useful": "COALESCE(t.usefulness, -1) DESC, saved_index DESC",
    "saved": "saved_index DESC",
    "newest": "p.created_at DESC",
    "oldest": "p.created_at ASC",
}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
    "img-src 'self' data:; media-src 'self' https://video.twimg.com; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


# --- queries ---------------------------------------------------------------------


def _media_url(relative: str | None) -> str | None:
    if not relative:
        return None
    return "/media/" + relative.removeprefix("media/")


def _labels_from_query(query: dict[str, str]) -> dict:
    labels: dict = {}
    for key in ("intent", "effort", "status", "topic"):
        if query.get(key):
            labels[key] = query[key]
    if query.get("min_usefulness", "") != "":
        try:
            labels["min_usefulness"] = max(0, min(3, int(query["min_usefulness"])))
        except ValueError as exc:
            raise ApiError(400, "min_usefulness must be 0-3") from exc
    if query.get("labeled") == "false":
        labels["labeled"] = False
    return labels


def query_posts(db: Database, query: dict[str, str]) -> dict:
    collection = resolve_collection(db, query.get("collection") or None)
    has_images = {"true": True, "false": False}.get(query.get("has_images", ""))
    where, params = db._filters(
        collection, query.get("author") or None, None, None, has_images, _labels_from_query(query)
    )
    where.insert(0, "EXISTS (SELECT 1 FROM collections c0 WHERE c0.post_id=p.id)")
    if query.get("hide_noise", "true") != "false" and query.get("intent") != "noise":
        where.append("(t.intent IS NULL OR t.intent <> 'noise')")

    text = (query.get("q") or "").strip()
    if text:
        expression = fts_query(text, "all")
        if db.fts_available and expression:
            where.append("p.id IN (SELECT post_id FROM posts_fts WHERE posts_fts MATCH ?)")
            params.append(expression)
        else:
            where.append("(p.text LIKE ? OR t.summary LIKE ? OR t.action LIKE ?)")
            params += [f"%{text}%"] * 3

    index_filter = "AND c.collection = ?" if collection else ""
    index_params = [collection] if collection else []
    sort = SORTS.get(query.get("sort", ""), SORTS["useful"] if text else SORTS["saved"])
    try:
        limit = max(1, min(int(query.get("limit", 40)), 200))
        offset = max(0, int(query.get("offset", 0)))
    except ValueError as exc:
        raise ApiError(400, "limit and offset must be numbers") from exc

    base = f"""FROM posts p LEFT JOIN triage t ON t.post_id = p.id
               WHERE {" AND ".join(where)}"""
    total = db._one(f"SELECT COUNT(*) AS n {base}", params)["n"]
    rows = db._rows(
        f"""SELECT p.id, p.url, p.author_handle, p.author_name, p.created_at, p.text,
                   p.article_title, t.intent, t.usefulness, t.effort, t.summary, t.action,
                   t.status,
                   (SELECT MAX(CAST(c.sort_index AS INTEGER)) FROM collections c
                     WHERE c.post_id = p.id {index_filter}) AS saved_index,
                   (SELECT COUNT(*) FROM media m WHERE m.post_id = p.id) AS media_count,
                   (SELECT m.ai_path FROM media m WHERE m.post_id = p.id AND m.status = 'done'
                     ORDER BY m.position LIMIT 1) AS thumb,
                   (SELECT m.kind FROM media m WHERE m.post_id = p.id
                     ORDER BY m.position LIMIT 1) AS thumb_kind
            {base} ORDER BY {sort}, p.id DESC LIMIT ? OFFSET ?""",
        [*index_params, *params, limit, offset],
    )
    items = []
    for row in rows:
        topics = [
            r["topic"]
            for r in db._rows(
                "SELECT topic FROM triage_topics WHERE post_id=? ORDER BY topic", (row["id"],)
            )
        ]
        items.append(
            {
                "id": row["id"],
                "url": row["url"],
                "author": row["author_handle"],
                "author_name": row["author_name"],
                "created_at": row["created_at"],
                "text": row["text"][:500],
                "article_title": row["article_title"],
                "intent": row["intent"],
                "usefulness": row["usefulness"],
                "effort": row["effort"],
                "summary": row["summary"],
                "action": row["action"],
                "status": row["status"],
                "topics": topics,
                "media_count": row["media_count"],
                "thumb": _media_url(row["thumb"]),
                "thumb_kind": row["thumb_kind"],
            }
        )
    return {"total": total, "offset": offset, "limit": limit, "items": items}


def _media_list(db: Database, post_id: str) -> list[dict]:
    return [
        {
            "kind": m["kind"],
            "alt_text": m["alt_text"],
            "status": m["status"],
            "image": _media_url(m["ai_path"]),
            "original": _media_url(m["local_path"]),
            "video_url": m["video_url"],
            "width": m["width"],
            "height": m["height"],
        }
        for m in db.get_media(post_id)
    ]


def post_detail(db: Database, post_id: str) -> dict:
    post = db.get_post(post_id)
    if post is None:
        raise ApiError(404, f"No saved post with id {post_id}")
    names = db.folder_names()
    detail = {
        "id": post["id"],
        "url": post["url"],
        "author": post["author_handle"],
        "author_name": post["author_name"],
        "created_at": post["created_at"],
        "text": post["text"],
        "metrics": post["metrics"],
        "article": {"title": post["article_title"], "text": post["article_text"]}
        if post.get("article_title") or post.get("article_text")
        else None,
        "collections": [names.get(c, c) for c in db.get_collections_of(post_id)],
        "media": _media_list(db, post_id),
        "links": [
            {
                "url": link["url"],
                "title": link["title"],
                "description": link["description"],
                "status": link["status"],
            }
            for link in db.get_links(post_id)
        ],
        "labels": triage.get_labels(db, post_id),
        "tags": db.get_tags(post_id),
        "note": post.get("note"),
        "quoted": None,
    }
    if post.get("quoted_post_id"):
        quoted = db.get_post(post["quoted_post_id"])
        if quoted:
            detail["quoted"] = {
                "id": quoted["id"],
                "url": quoted["url"],
                "author": quoted["author_handle"],
                "author_name": quoted["author_name"],
                "text": quoted["text"],
                "media": _media_list(db, quoted["id"]),
            }
    return detail


def overview(db: Database) -> dict:
    names = db.folder_names()
    progress = triage.progress(db)
    stats = db.stats()
    return {
        "version": __version__,
        "saved_posts": progress["saved_posts"],
        "labeled": progress["labeled"],
        "remaining": progress["remaining"],
        "collections": [
            {
                "key": c["collection"],
                "name": names.get(c["collection"], c["collection"]),
                "count": c["count"],
            }
            for c in db.list_collections()
        ],
        "intents": progress["by_intent"],
        "usefulness": progress["by_usefulness"],
        "statuses": progress["by_status"],
        "efforts": progress["by_effort"],
        "topics": db._rows(
            "SELECT topic, COUNT(*) AS count FROM triage_topics GROUP BY topic "
            "ORDER BY count DESC, topic LIMIT 40"
        ),
        "tags": db.list_tags(),
        "last_capture": stats["last_capture"],
        "images": stats["media"],
        "intent_help": triage.INTENTS,
        "usefulness_help": triage.USEFULNESS,
        "effort_help": triage.EFFORTS,
    }


def update_labels(db: Database, post_id: str, body: dict) -> dict:
    if db.get_post(post_id) is None:
        raise ApiError(404, f"No saved post with id {post_id}")
    current = triage.get_labels(db, post_id) or {}
    merged = {
        "post_id": post_id,
        **{k: current.get(k) for k in ("intent", "usefulness", "effort", "summary", "action")},
        "topics": current.get("topics", []),
    }
    for key in ("intent", "usefulness", "effort", "summary", "action", "topics"):
        if key in body:
            merged[key] = body[key]
    if merged.get("intent") is None or merged.get("usefulness") is None:
        raise ApiError(400, "Choose an intent and a usefulness first")
    result = triage.save_labels(db, [merged], labeled_by="web")
    if result["errors"]:
        raise ApiError(400, result["errors"][0])
    return triage.get_labels(db, post_id)


# --- HTTP ------------------------------------------------------------------------


TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


def lan_addresses() -> list[str]:
    """Addresses a phone can reach: the main network (Wi-Fi) and Tailscale.

    Virtual adapters (VirtualBox, Hyper-V, WSL) are skipped: a phone can't reach them.
    """
    usable: list[str] = []
    try:  # the address of the interface used for outgoing traffic; no packet is sent
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))
            primary = probe.getsockname()[0]
        ip = ipaddress.ip_address(primary)
        if ip.is_private and not ip.is_loopback and not ip.is_link_local:
            usable.append(primary)
    except OSError:
        pass
    try:
        others = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        others = []
    usable += [a for a in others if ipaddress.ip_address(a) in TAILSCALE_NET]
    return list(dict.fromkeys(usable))


def qr_svg_data_uri(text: str) -> str:
    return segno.make(text, error="m").svg_data_uri(scale=6, border=2, dark="#14302b")


class WebServer:
    def __init__(
        self,
        db: Database,
        port: int = DEFAULT_WEB_PORT,
        host: str = "127.0.0.1",
        lan: bool = False,
        code: str | None = None,
        trust_loopback: bool = True,
    ):
        self.db = db
        self.lan = lan
        self.code = code or (config.web_code() if lan else "")
        self.trust_loopback = trust_loopback
        self.session = hmac.new(self.code.encode(), b"session", hashlib.sha256).hexdigest()
        handler = type("BoundWebHandler", (_Handler,), {"app": self})
        self.httpd = LocalHTTPServer(("0.0.0.0" if lan else host, port), handler)
        self.httpd.daemon_threads = True

    def phone_info(self) -> dict:
        if not self.lan:
            return {"lan": False, "urls": [], "command": "bookmark-mcp web --lan"}
        entries = []
        for address in lan_addresses():
            url = f"http://{address}:{self.port}/"
            login = f"{url}login?code={quote(self.code)}"
            in_tailscale = ipaddress.ip_address(address) in TAILSCALE_NET
            entries.append(
                {
                    "kind": "tailscale" if in_tailscale else "wifi",
                    "url": url,
                    "login_url": login,
                    "qr": qr_svg_data_uri(login),
                }
            )
        first = entries[0] if entries else None
        return {
            "lan": True,
            "urls": [e["url"] for e in entries],
            "entries": entries,
            "code": self.code,
            "login_url": first["login_url"] if first else None,
            "qr": first["qr"] if first else None,
        }

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def serve_forever(self) -> None:
        logger.info("Web app on %s", self.url)
        self.httpd.serve_forever()

    def start(self) -> WebServer:
        threading.Thread(target=self.serve_forever, daemon=True, name="web-app").start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


_POST_ROUTE = re.compile(r"^/api/posts/([^/]+)(?:/(labels|status|note|tags)(?:/([^/]+))?)?$")


class _Handler(BaseHTTPRequestHandler):
    app: WebServer
    server_version = "bookmark-mcp-web"

    def log_message(self, fmt: str, *args) -> None:
        logger.debug("%s - %s", self.address_string(), fmt % args)

    # --- responses ------------------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: object) -> None:
        self._send(
            status,
            json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    # --- checks ---------------------------------------------------------------------

    def _is_local(self) -> bool:
        return self.app.trust_loopback and self.client_address[0] in LOOPBACK_CLIENTS

    def _logged_in(self) -> bool:
        if self._is_local():
            return True
        if not self.app.lan:
            return False
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE and hmac.compare_digest(value, self.app.session):
                return True
        return False

    def _host_ok(self) -> bool:
        if not self._is_local():
            return True  # other devices must log in instead (see _logged_in)
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in LOOPBACK_HOSTS:
            self._error(403, "forbidden host")
            return False
        return True

    def _same_origin(self) -> bool:
        if self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != (self.headers.get("Host") or ""):
            return False
        return True

    def _redirect(self, location: str, cookie: str | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _login(self, query: dict) -> None:
        code = query.get("code", "")
        if code and self.app.lan and hmac.compare_digest(code.strip(), self.app.code):
            cookie = (
                f"{SESSION_COOKIE}={self.app.session}; Path=/; HttpOnly; "
                f"SameSite=Strict; Max-Age={SESSION_DAYS * 86400}"
            )
            self._redirect("/", cookie)
            return
        if code:
            time.sleep(1)  # slow down guessing
            self._redirect("/login?error=1")
            return
        page = (STATIC_DIR / "login.html").read_text(encoding="utf-8")
        if query.get("error"):
            page = page.replace("hidden data-error", "data-error")
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

    def _read_json(self) -> dict:
        if self.headers.get(CHANGE_HEADER) != "1":
            raise ApiError(403, "missing request header")
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise ApiError(415, "send JSON")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApiError(411, "Content-Length required") from exc
        if length > MAX_BODY:
            raise ApiError(413, "body too large")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError as exc:
            raise ApiError(400, "invalid JSON") from exc
        if not isinstance(body, dict):
            raise ApiError(400, "expected a JSON object")
        return body

    # --- routing --------------------------------------------------------------------

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
        if path == "/login":
            self._login(query)
            return
        logged_in = self._logged_in()
        if path.startswith("/api/"):
            if not logged_in:
                self._error(401, "log in first")
                return
            if not self._same_origin():
                self._error(403, "cross-site request refused")
                return
            if path == "/api/phone":
                if not self._is_local():
                    self._error(403, "only available on the computer running the app")
                    return
                self._json(200, self.app.phone_info())
                return
            self._handle_api("GET", path, query)
        elif path.startswith("/media/"):
            if not logged_in:
                self._error(401, "log in first")
                return
            self._serve_media(unquote(path[len("/media/") :]))
        elif not logged_in and ("." not in Path(path).name or path.endswith(".html")):
            self._redirect("/login")
        else:
            self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        self._change("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._change("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._change("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._change("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._error(403, "forbidden")

    def _change(self, method: str) -> None:
        if not self._host_ok():
            return
        if not self._logged_in():
            self._error(401, "log in first")
            return
        if not self._same_origin():
            self._error(403, "cross-site request refused")
            return
        self._handle_api(method, urlparse(self.path).path, {})

    def _handle_api(self, method: str, path: str, query: dict) -> None:
        db = self.app.db
        try:
            if method == "GET" and path == "/api/overview":
                self._json(200, overview(db))
                return
            if method == "GET" and path == "/api/posts":
                self._json(200, query_posts(db, query))
                return
            if method == "GET" and path == "/api/next-up":
                self._json(
                    200,
                    triage.next_up(
                        db,
                        query.get("intent") or None,
                        query.get("effort") or None,
                        query.get("topic") or None,
                        int(query.get("min_usefulness", 2)),
                        False,
                        int(query.get("limit", 8)),
                    ),
                )
                return
            match = _POST_ROUTE.match(path)
            if not match:
                raise ApiError(404, "not found")
            post_id, action, extra = unquote(match.group(1)), match.group(2), match.group(3)
            if method == "GET" and action is None:
                self._json(200, post_detail(db, post_id))
                return
            if method in ("GET", "HEAD"):
                raise ApiError(405, "method not allowed")
            body = self._read_json()
            if db.get_post(post_id) is None:
                raise ApiError(404, f"No saved post with id {post_id}")
            if action == "labels" and method == "PATCH":
                self._json(200, {"labels": update_labels(db, post_id, body)})
            elif action == "status" and method == "PUT":
                result = triage.set_status(db, post_id, str(body.get("status", "")))
                if not result["ok"]:
                    raise ApiError(409, result["error"])
                self._json(200, {"labels": triage.get_labels(db, post_id)})
            elif action == "note" and method == "PUT":
                db.set_note(post_id, str(body.get("note") or "")[:5000])
                self._json(200, {"note": db.get_post(post_id)["note"]})
            elif action == "tags" and method == "POST":
                try:
                    db.add_tag(post_id, str(body.get("tag") or ""))
                except ValueError as exc:
                    raise ApiError(400, str(exc)) from exc
                self._json(200, {"tags": db.get_tags(post_id)})
            elif action == "tags" and method == "DELETE" and extra:
                db.remove_tag(post_id, unquote(extra))
                self._json(200, {"tags": db.get_tags(post_id)})
            else:
                raise ApiError(405, "method not allowed")
        except ApiError as exc:
            self._error(exc.status, str(exc))
        except (ValueError, sqlite3.Error) as exc:
            logger.exception("API error")
            self._error(400, f"{type(exc).__name__}: {exc}")

    # --- files ----------------------------------------------------------------------

    def _serve_file(self, base: Path, relative: str, cache: str) -> None:
        try:
            target = (base / relative).resolve()
            target.relative_to(base.resolve())
        except (ValueError, OSError):
            self._error(404, "not found")
            return
        if not target.is_file():
            self._error(404, "not found")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type.endswith("javascript"):
            content_type += "; charset=utf-8"
        self._send(200, target.read_bytes(), content_type, cache)

    def _serve_media(self, relative: str) -> None:
        self._serve_file(config.media_dir(), relative, "private, max-age=86400")

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        if "." not in Path(relative).name:  # client-side routes
            relative = "index.html"
        self._serve_file(STATIC_DIR, relative, "no-cache")
