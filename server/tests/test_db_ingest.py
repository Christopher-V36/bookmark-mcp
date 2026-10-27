import json

from bookmark_mcp.db import fts_query
from bookmark_mcp.ingest import ingest_event, reparse_all
from tests import x_samples as s


def _ingest_rich(db):
    return ingest_event(db, "x", "Bookmarks", s.rich_bookmarks(), s.graphql_url("Bookmarks"))


def test_ingest_stores_posts_collections_and_media(db):
    summary = _ingest_rich(db)
    assert summary.status == "ok"
    assert summary.posts == 8  # 7 bookmarked (retweet resolves to its original) + 1 quoted
    assert summary.new_in_collections == 7
    assert summary.collections == ["bookmarks"]

    post = db.get_post("x:100")
    assert post["author_handle"] == "alice_dev"
    assert post["metrics"]["views"] == 1234
    assert db.get_collections_of("x:100") == ["bookmarks"]
    assert [m["status"] for m in db.get_media("x:100")] == ["pending"]
    assert db.stats()["events"] == {"ok": 1}


def test_reingest_is_idempotent_and_keeps_added_at(db):
    _ingest_rich(db)
    first = db.browse("bookmarks")[0]["added_at"]
    again = _ingest_rich(db)
    assert again.new_posts == 0 and again.new_in_collections == 0
    assert db.browse("bookmarks")[0]["added_at"] == first
    assert db.stats()["posts"] == 8


def test_browse_orders_by_sort_index(db):
    _ingest_rich(db)
    ids = [row["id"] for row in db.browse("bookmarks")]
    assert ids == ["x:100", "x:101", "x:102", "x:103", "x:104", "x:300", "x:106"]


def test_post_in_bookmarks_likes_and_folder(db):
    _ingest_rich(db)
    ingest_event(db, "x", "Likes", s.likes_payload([s.entry(s.tweet("100", "Great thread"), "1")]))
    url = s.graphql_url("BookmarkFolderTimeline", {"bookmark_collection_id": "77"})
    ingest_event(
        db,
        "x",
        "BookmarkFolderTimeline",
        s.folder_payload([s.entry(s.tweet("100", "Great thread"), "1")]),
        url,
    )
    ingest_event(db, "x", "bookmarkFoldersSlice", s.folders_slice_payload([("77", "AI")]))
    assert db.get_collections_of("x:100") == ["bookmarks", "folder:77", "likes"]
    assert db.folder_names() == {"folder:77": "AI"}


def test_search_fts_filters_and_safe_queries(db):
    _ingest_rich(db)
    assert [r["id"] for r in db.search("agents")] == ["x:100"]
    assert {r["id"] for r in db.search("MCP servers")} == {"x:102"}
    # alt text and card titles are searchable
    assert [r["id"] for r in db.search("diagram")] == ["x:100"]
    assert [r["id"] for r in db.search("how we built")] == ["x:104"]
    # FTS operators and punctuation don't crash
    assert db.search('claude-code OR "unbalanced') == []
    assert db.search("NEAR( * ") == []
    # filters
    assert [r["id"] for r in db.search("chart", has_images=True)] == ["x:200"]
    assert db.search("agents", author="carol") == []
    assert [r["id"] for r in db.search("post", collection="bookmarks", match="any")]


def test_like_fallback_when_fts_missing(db):
    _ingest_rich(db)
    db.fts_available = False
    assert [r["id"] for r in db.search("agents")] == ["x:100"]
    assert [r["id"] for r in db.search("chart", author="carol")] == ["x:200"]


def test_tags_and_notes_are_searchable(db):
    _ingest_rich(db)
    assert db.add_tag("x:104", "  Must Read! ") == "must-read"
    db.set_note("x:104", "compare with our architecture")
    assert [r["id"] for r in db.search("architecture")] == ["x:104"]
    assert db.list_tags() == [{"tag": "must-read", "count": 1}]


def test_fts_query_builder():
    assert fts_query("agents memory") == '"agents"* AND "agents"*'.replace(
        'AND "agents"*', 'AND "memory"*'
    )
    assert fts_query('"exact phrase" mcp', match="any") == '"exact phrase" OR "mcp"*'
    assert fts_query("claude-code") == '"claude code"'
    assert fts_query("***") == ""


def test_broken_parser_keeps_raw_event_and_reparse_recovers(db, monkeypatch):
    from bookmark_mcp import ingest

    def boom(*_args):
        raise KeyError("new X format")

    monkeypatch.setitem(ingest.PARSERS, "x", boom)
    summary = _ingest_rich(db)
    assert summary.status == "error" and "new X format" in summary.error
    assert db.stats()["posts"] == 0

    monkeypatch.undo()
    totals = reparse_all(db)
    assert totals["ok"] == 1 and db.stats()["posts"] == 8


def test_unsupported_operation_is_ignored(db):
    summary = ingest_event(db, "x", "HomeTimeline", json.dumps({"data": {}}))
    assert summary.status == "ignored"
