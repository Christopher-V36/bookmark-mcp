"""Where bookmark-mcp keeps its data and how the local ingest API is reached.

Everything personal (database, downloaded images, pairing token) lives in a
per-user data folder outside the repository, e.g.
``C:\\Users\\<you>\\AppData\\Local\\bookmark-mcp`` on Windows.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "bookmark-mcp"
DEFAULT_PORT = 48923


def data_dir() -> Path:
    override = os.environ.get("BOOKMARK_MCP_HOME")
    path = Path(override) if override else Path(user_data_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


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
