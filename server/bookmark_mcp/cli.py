"""Command-line interface: ``bookmark-mcp <command>``."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click

from bookmark_mcp import __version__, config
from bookmark_mcp.db import Database


def _db() -> Database:
    return Database(config.db_path())


def _echo_json(data: object) -> None:
    click.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


@click.group()
@click.version_option(__version__, prog_name="bookmark-mcp")
@click.option("--verbose", "-v", is_flag=True, help="Show debug logs.")
def cli(verbose: bool) -> None:
    """bookmark-mcp - let your AI research the posts you saved."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )


@cli.command()
def mcp() -> None:
    """Run the MCP server over stdio (this is what Claude starts)."""
    from bookmark_mcp.mcp_server import run

    run()


@cli.command()
@click.option("--port", type=int, default=None, help=f"Port (default {config.DEFAULT_PORT}).")
def serve(port: int | None) -> None:
    """Receive captures from the browser extension (without Claude running)."""
    from bookmark_mcp.ingest_api import IngestServer
    from bookmark_mcp.media import MediaWorker

    db = _db()
    worker = MediaWorker(db)
    worker.schedule()
    server = IngestServer(
        db, config.token(), port=port or config.port(), on_ingested=worker.schedule
    )
    click.echo(f"Listening on http://127.0.0.1:{server.port} - press Ctrl+C to stop.")
    click.echo(f"Pairing token: {config.token()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.stop()


@cli.command()
@click.option("--port", type=int, default=None, help="Web app port (default 48924).")
@click.option("--no-browser", is_flag=True, help="Don't open the browser.")
@click.option(
    "--lan",
    is_flag=True,
    help="Also allow your phone and other devices on your network (they log in with a code).",
)
def web(port: int | None, no_browser: bool, lan: bool) -> None:
    """Open the web app to browse and manage your saved posts.

    It also receives captures from the browser extension while it runs.
    """
    import os
    import webbrowser

    from bookmark_mcp.ingest_api import IngestServer
    from bookmark_mcp.media import MediaWorker
    from bookmark_mcp.webapp import DEFAULT_WEB_PORT, WebServer

    db = _db()
    worker = MediaWorker(db)
    worker.schedule()
    try:
        IngestServer(db, config.token(), port=config.port(), on_ingested=worker.schedule).start()
        receiving = "and receiving captures from the extension"
    except OSError:
        receiving = "(another bookmark-mcp process is receiving captures)"
    web_port = port or int(os.environ.get("BOOKMARK_MCP_WEB_PORT", DEFAULT_WEB_PORT))
    server = WebServer(db, port=web_port, lan=lan)
    click.echo(f"bookmark-mcp web app on {server.url} {receiving}. Ctrl+C to stop.")
    if lan:
        info = server.phone_info()
        if info["login_url"]:
            import segno

            for entry in info["entries"]:
                where = (
                    "anywhere, with Tailscale on both devices"
                    if entry["kind"] == "tailscale"
                    else "on the same Wi-Fi"
                )
                click.echo(f"\nOn your phone ({where}), scan:\n")
                segno.make(entry["login_url"], error="m").terminal(compact=True)
                click.echo(f"or open {entry['url']} and type the code: {info['code']}")
        else:
            click.echo("No network address found: is this computer connected to Wi-Fi?")
    if not no_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.stop()


@cli.command()
def token() -> None:
    """Print the pairing token to paste into the browser extension."""
    click.echo(config.token())


@cli.command(name="import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--collection",
    default="bookmarks",
    show_default=True,
    help="Collection to put the posts in (bookmarks, likes, ...).",
)
@click.option("--no-media", is_flag=True, help="Don't download images now.")
def import_cmd(path: Path, collection: str, no_media: bool) -> None:
    """Import a JSON export from twitter-web-exporter."""
    from bookmark_mcp.importers.twitter_web_exporter import import_file
    from bookmark_mcp.media import download_pending

    db = _db()
    result = import_file(db, path, collection)
    _echo_json(result)
    if not no_media:
        click.echo("Downloading images...")
        _echo_json(download_pending(db))


@cli.command()
@click.option("--retry-errors", is_flag=True, help="Also retry images that failed before.")
@click.option("--limit", type=int, default=None)
def media(retry_errors: bool, limit: int | None) -> None:
    """Download images that are still pending."""
    from bookmark_mcp.media import download_pending

    _echo_json(download_pending(_db(), limit=limit, retry_errors=retry_errors))


@cli.command()
@click.option("--refresh", is_flag=True, help="Fetch again pages that were already fetched.")
@click.option("--limit", type=int, default=None)
def enrich(refresh: bool, limit: int | None) -> None:
    """Fetch the text of linked articles so they become searchable.

    This visits the linked websites (they see your IP address like a normal visit).
    """
    from bookmark_mcp.enrich import enrich_pending

    _echo_json(enrich_pending(_db(), limit=limit, refresh=refresh))


@cli.command()
def reparse() -> None:
    """Re-run the parsers over every captured response (after an update)."""
    from bookmark_mcp.ingest import reparse_all

    db = _db()
    _echo_json(reparse_all(db))
    db.rebuild_fts()


@cli.command()
def stats() -> None:
    """Show what is stored."""
    from bookmark_mcp.tools import sync_status

    _echo_json(sync_status(_db()))


@cli.command()
def doctor() -> None:
    """Check that everything needed is in place."""
    import socket

    db = _db()
    checks = {
        "data_folder": str(config.data_dir()),
        "database": str(config.db_path()),
        "full_text_search": db.fts_available,
        "posts": db.stats()["posts"],
    }
    with socket.socket() as sock:
        busy = sock.connect_ex(("127.0.0.1", config.port())) == 0
    checks["ingest_port"] = config.port()
    checks["ingest_running"] = busy
    checks["mcp_command"] = f'"{sys.executable}" -m bookmark_mcp mcp'
    _echo_json(checks)
    if not busy:
        click.echo("\nThe ingest API is not running: start Claude (MCP) or `bookmark-mcp serve`.")


def main() -> None:
    cli()
