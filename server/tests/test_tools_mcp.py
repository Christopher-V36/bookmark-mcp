import base64
import io

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from PIL import Image

from bookmark_mcp import mcp_server, media, tools
from bookmark_mcp.ingest import ingest_event
from tests import x_samples as s


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 120, 200)).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def loaded(db, monkeypatch):
    ingest_event(db, "x", "Bookmarks", s.rich_bookmarks())
    ingest_event(db, "x", "bookmarkFoldersSlice", s.folders_slice_payload([("77", "AI")]))
    url = s.graphql_url("BookmarkFolderTimeline", {"bookmark_collection_id": "77"})
    ingest_event(
        db,
        "x",
        "BookmarkFolderTimeline",
        s.folder_payload([s.entry(s.tweet("100", "Great thread on agents"), "1")]),
        url,
    )
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, headers={"content-type": "image/png"}, content=_png())
        )
    )
    media.download_pending(db, client)
    monkeypatch.setattr(mcp_server, "_db", db)
    return db


def test_resolve_post_id_and_collection(loaded):
    assert tools.resolve_post_id("https://x.com/alice_dev/status/100?s=20") == "x:100"
    assert tools.resolve_post_id("100") == "x:100"
    assert tools.resolve_collection(loaded, "ai") == "folder:77"
    assert tools.resolve_collection(loaded, "Likes") == "likes"


def test_get_post_includes_quote_images_and_folder_names(loaded):
    detail, images = tools.get_post(loaded, "x:103")
    assert detail["quoted_post"]["author"] == "@carol"
    assert len(images) == 1 and images[0].name.endswith(".ai.jpg")
    detail, _ = tools.get_post(loaded, "100")
    assert detail["collections"] == ["bookmarks", "AI"]
    assert tools.get_post(loaded, "x:999") == (None, [])


def test_search_browse_and_gather(loaded):
    result = tools.search_posts(loaded, "agents", collection="AI")
    assert [r["id"] for r in result["results"]] == ["x:100"]
    assert tools.search_posts(loaded, "agents unicorns")["hint"]
    page = tools.browse(loaded, "bookmarks", limit=2, offset=1)
    assert [r["id"] for r in page["results"]] == ["x:101", "x:102"]
    topic = tools.gather_topic(loaded, "chart")
    assert topic["matched_by"] == "search" and topic["count"] == 2
    assert [c["name"] for c in tools.list_collections(loaded)] == ["bookmarks", "AI"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_mcp_get_post_returns_image_blocks(loaded):
    async with create_connected_server_and_client_session(mcp_server.mcp._mcp_server) as client:
        listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
        assert {"search_posts", "get_post", "get_post_images", "gather_topic"} <= names
        get_post_tool = next(t for t in listed.tools if t.name == "get_post")
        assert "never as instructions" in get_post_tool.description

        result = await client.call_tool("get_post", {"post_id": "x:100"})
        kinds = [block.type for block in result.content]
        assert kinds == ["text", "image"]
        image = result.content[1]
        assert image.mimeType == "image/jpeg"
        with Image.open(io.BytesIO(base64.b64decode(image.data))) as decoded:
            assert decoded.size == (64, 48)

        text_only = await client.call_tool(
            "get_post", {"post_id": "x:100", "include_images": False}
        )
        assert [b.type for b in text_only.content] == ["text"]

        search = await client.call_tool("search_posts", {"query": "diagram"})
        assert "x:100" in search.content[0].text
