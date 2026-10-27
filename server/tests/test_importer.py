import json

import pytest

from bookmark_mcp.importers.twitter_web_exporter import detect_format, import_file
from tests import x_samples as s


def _flat(tweet_id, text, metadata=None):
    record = {
        "id": tweet_id,
        "created_at": "2026-10-01 14:34:56 +02:00",
        "full_text": text,
        "media": [
            {
                "type": "photo",
                "url": "https://t.co/m",
                "thumbnail": f"https://pbs.twimg.com/media/F{tweet_id}?format=jpg&name=thumb",
                "original": f"https://pbs.twimg.com/media/F{tweet_id}?format=jpg&name=orig",
                "ext_alt_text": "A cat",
            }
        ],
        "screen_name": "erin",
        "name": "Erin",
        "favorite_count": 3,
        "url": f"https://x.com/erin/status/{tweet_id}",
    }
    if metadata is not None:
        record["metadata"] = metadata
    return record


def test_detect_format():
    assert detect_format([_flat("1", "a")]) == "exporter-flat"
    assert detect_format([_flat("1", "a", metadata=s.tweet("1"))]) == "exporter-with-metadata"
    assert detect_format([s.tweet("1")]) == "raw-graphql"
    assert detect_format({"not": "a list"}) == "unknown"


def test_import_flat_export(db, tmp_path):
    path = tmp_path / "export.json"
    path.write_text(json.dumps([_flat("10", "newest"), _flat("11", "older")]), encoding="utf-8")
    result = import_file(db, path)
    assert result["added"] == 2 and "tip" in result
    post = db.get_post("x:10")
    assert post["author_handle"] == "erin"
    assert post["created_at"] == "2026-10-01T12:34:56+00:00"
    [media] = db.get_media("x:10")
    assert media["alt_text"] == "A cat" and media["remote_url"].endswith("name=orig")
    assert [r["id"] for r in db.browse("bookmarks")] == ["x:10", "x:11"]


def test_import_with_metadata_uses_full_parser(db, tmp_path):
    quoted = s.tweet("20", "quoted original", author=s.user("carol", "Carol", "1003"))
    rich = s.tweet(
        "21",
        "short",
        note_tweet={"note_tweet_results": {"result": {"text": "the long version of the post"}}},
        quoted_status_result={"result": quoted},
    )
    path = tmp_path / "export.json"
    path.write_text(json.dumps([_flat("21", "short", metadata=rich)]), encoding="utf-8")
    result = import_file(db, path, collection="likes")
    assert result["format"] == "exporter-with-metadata" and "tip" not in result
    post = db.get_post("x:21")
    assert post["text"] == "the long version of the post"
    assert post["quoted_post_id"] == "x:20" and db.get_post("x:20") is not None
    assert db.get_collections_of("x:21") == ["likes"]


def test_reimport_updates_instead_of_duplicating(db, tmp_path):
    path = tmp_path / "export.json"
    path.write_text(json.dumps([_flat("30", "v1")]), encoding="utf-8")
    import_file(db, path)
    path.write_text(json.dumps([_flat("30", "v2")]), encoding="utf-8")
    assert import_file(db, path)["updated"] == 1
    assert db.get_post("x:30")["text"] == "v2"


def test_unknown_file_is_rejected(db, tmp_path):
    path = tmp_path / "other.json"
    path.write_text(json.dumps([{"foo": 1}]), encoding="utf-8")
    with pytest.raises(ValueError):
        import_file(db, path)
