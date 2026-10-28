"""Where bookmark-mcp keeps its data and how the local ingest API is reached.

Everything personal (database, downloaded images, pairing token) lives in a
per-user data folder outside the repository: ``~/.bookmark-mcp``
(``C:\\Users\\<you>\\.bookmark-mcp`` on Windows), or ``BOOKMARK_MCP_HOME`` if set.

It is not under AppData on purpose: Windows redirects AppData writes made by packaged
apps (like the Claude desktop app) into a private copy, so the MCP server Claude starts
and a normal terminal would each see different data.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import sqlite3
import threading
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "bookmark-mcp"
DEFAULT_PORT = 48923

logger = logging.getLogger(__name__)
_migrate_lock = threading.Lock()


def legacy_data_dir() -> Path:
    """Where versions up to 0.1 kept the data (``%LOCALAPPDATA%\\bookmark-mcp``)."""
    return Path(user_data_dir(APP_NAME, appauthor=False))


def data_dir() -> Path:
    override = os.environ.get("BOOKMARK_MCP_HOME")
    if override:
        path = Path(override)
    else:
        path = Path.home() / f".{APP_NAME}"
        with _migrate_lock:
            if not path.exists():
                _migrate(legacy_data_dir(), path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _migrate(old: Path, new: Path) -> None:
    """Copy the old data folder to the new one, once. The old folder is left as it was."""
    if not (old / "bookmark-mcp.db").exists():
        return
    logger.info("Moving bookmark-mcp data from %s to %s", old, new)
    staging = new.with_name(new.name + ".moving")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(
        old,
        staging,
        ignore=shutil.ignore_patterns("bookmark-mcp.db*", "*.tmp"),
    )
    # The backup API gives a consistent copy even while another process writes (WAL).
    source = sqlite3.connect(old / "bookmark-mcp.db")
    target = sqlite3.connect(staging / "bookmark-mcp.db")
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    staging.rename(new)


def db_path() -> Path:
    return data_dir() / "bookmark-mcp.db"


def media_dir() -> Path:
    path = data_dir() / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def port() -> int:
    return int(os.environ.get("BOOKMARK_MCP_PORT", DEFAULT_PORT))


def _secret(filename: str) -> str:
    path = data_dir() / filename
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(24)
    path.write_text(value, encoding="utf-8")
    return value


def token() -> str:
    """Return the extension pairing token, creating one on first use."""
    return _secret("token.txt")


def web_code() -> str:
    """Return the code other devices (your phone) use to log in to the web app."""
    return _secret("web_code.txt")
