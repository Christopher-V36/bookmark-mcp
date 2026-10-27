"""Fetch the readable text of pages that saved posts link to.

Ported from x-bookmarks-mcp (MIT, (c) 2026 Poki Balboa). Because links come from
other people's posts, every request is guarded: only http(s), only public IP
addresses (re-checked on every redirect), bounded size, text/HTML only, and a
timeout. Links back to X itself are skipped (they need your login).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura

from bookmark_mcp.db import Database

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_CONTENT_TYPES = ("text/", "application/xhtml+xml")
SKIPPED_HOSTS = ("x.com", "twitter.com", "t.co", "pic.twitter.com", "mobile.twitter.com")
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 5_000_000
MAX_CONTENT_CHARS = 50_000
USER_AGENT = (
    "Mozilla/5.0 (compatible; bookmark-mcp/0.1; +https://github.com/Christopher-V36/bookmark-mcp)"
)

Resolver = Callable[[str, int], list[str]]


def _system_resolver(host: str, port: int) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def detect_content_type(url: str) -> str:
    parsed = urlparse(url.lower())
    host, path = parsed.hostname or "", parsed.path or ""
    if "github.com" in host or "gitlab.com" in host:
        return "repo"
    if "youtube.com" in host or "youtu.be" in host:
        return "video"
    if host.startswith(("docs.", "documentation.", "developer.", "devdocs.")):
        return "docs"
    if "readthedocs." in host or path.startswith(("/docs/", "/reference/", "/api/", "/guide/")):
        return "docs"
    if path.endswith(".pdf"):
        return "pdf"
    return "article"


def is_public_url(url: str, resolver: Resolver = _system_resolver) -> bool:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    host = parsed.hostname
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    try:
        addresses = resolver(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError:
        return False
    return bool(addresses) and all(_is_global(address) for address in addresses)


def _is_global(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%")[0]).is_global
    except ValueError:
        return False


def is_skipped_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == skipped or host.endswith("." + skipped) for skipped in SKIPPED_HOSTS)


def download_html(
    client: httpx.Client, url: str, resolver: Resolver = _system_resolver
) -> tuple[str, str]:
    """Return (html, final_url). Raises ValueError with a readable reason otherwise."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not is_public_url(current, resolver):
            raise ValueError(f"blocked non-public or invalid address: {current}")
        with client.stream("GET", current, follow_redirects=False) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("redirect without a location")
                current = urljoin(str(response.url), location)
                continue
            response.raise_for_status()
            mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if mime and not mime.startswith(ALLOWED_CONTENT_TYPES):
                raise ValueError(f"unsupported content type {mime}")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ValueError("page too large")
            return body.decode(response.encoding or "utf-8", errors="replace"), current
    raise ValueError("too many redirects")


def extract(html: str) -> tuple[str | None, str | None]:
    text = trafilatura.extract(
        html, include_comments=False, include_tables=True, output_format="txt"
    )
    metadata = trafilatura.extract_metadata(html)
    title = metadata.title if metadata and metadata.title else None
    return title, (text[:MAX_CONTENT_CHARS] if text else None)


def enrich_pending(
    db: Database,
    client: httpx.Client | None = None,
    resolver: Resolver = _system_resolver,
    limit: int | None = None,
    refresh: bool = False,
    delay_seconds: float = 1.0,
) -> dict[str, int]:
    counts = {"done": 0, "error": 0, "skipped": 0}
    own_client = client is None
    client = client or httpx.Client(timeout=10, headers={"User-Agent": USER_AGENT})
    try:
        rows = db.pending_links(limit or 1_000_000, refresh=refresh)
        for index, row in enumerate(rows):
            post_id, url = row["post_id"], row["url"]
            if is_skipped_host(url):
                db.set_link_result(post_id, url, "skipped", error="links to X need your login")
                counts["skipped"] += 1
                continue
            if index and delay_seconds:
                time.sleep(delay_seconds)
            try:
                html, final_url = download_html(client, url, resolver)
                title, content = extract(html)
            except (httpx.HTTPError, ValueError) as exc:
                db.set_link_result(post_id, url, "error", error=f"{exc}"[:500])
                counts["error"] += 1
                continue
            db.set_link_result(
                post_id,
                url,
                "done",
                title=title,
                content=content,
                content_type=detect_content_type(final_url),
            )
            counts["done"] += 1
    finally:
        if own_client:
            client.close()
    return counts
