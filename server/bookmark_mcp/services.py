"""Background services shared by `mcp`, `web` and `serve`."""

from __future__ import annotations

from bookmark_mcp import config
from bookmark_mcp.cloud import CloudSyncWorker
from bookmark_mcp.db import Database
from bookmark_mcp.ingest_api import IngestServer
from bookmark_mcp.media import MediaWorker


def start_capture_services(db: Database, port: int | None = None) -> IngestServer | None:
    """Download images, receive captures and keep the cloud copy in sync.

    Only the process that owns the capture port receives captures and syncs to the cloud,
    so two running copies never push at the same time. Returns None when another
    bookmark-mcp process already owns the port (images are still downloaded here).
    """
    try:
        server = IngestServer(db, config.token(), port=port or config.port())
    except OSError:
        MediaWorker(db).schedule()
        return None
    cloud = CloudSyncWorker(db)
    media = MediaWorker(db, after=cloud.schedule)
    server.on_ingested = media.schedule
    media.schedule()  # pick up anything left from earlier runs, then sync
    return server
