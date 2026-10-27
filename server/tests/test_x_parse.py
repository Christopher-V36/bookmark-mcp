from bookmark_mcp.platforms.x import parse
from tests import x_samples as s


def _by_id(result):
    return {post.id: post for post in result.posts}


def test_bookmarks_page_covers_tricky_cases():
    result = parse("Bookmarks", s.rich_bookmarks(), s.graphql_url("Bookmarks"))
    posts = _by_id(result)

    member_ids = [m.post_id for m in result.memberships]
    assert member_ids == ["x:100", "x:101", "x:102", "x:103", "x:104", "x:300", "x:106"]
    assert {m.collection for m in result.memberships} == {"bookmarks"}
    assert result.skipped == 2  # tombstone + empty result
    # quoted post is stored but not bookmarked itself
    assert "x:200" in posts and "x:200" not in member_ids


def test_text_urls_media_and_metrics():
    posts = _by_id(parse("Bookmarks", s.rich_bookmarks()))
    plain = posts["x:100"]
    assert plain.text == "Great thread on agents https://example.com/agents"
    assert plain.url == "https://x.com/alice_dev/status/100"
    assert plain.author_handle == "alice_dev" and plain.author_name == "Alice Dev"
    assert plain.created_at == "2026-10-01T12:34:56+00:00"
    assert plain.metrics == {
        "likes": 10,
        "reposts": 2,
        "replies": 1,
        "quotes": 0,
        "bookmarks": 5,
        "views": 1234,
    }
    assert [link.url for link in plain.links] == ["https://example.com/agents"]
    [media] = plain.media
    assert media.kind == "photo"
    assert media.remote_url == "https://pbs.twimg.com/media/IMG3_1.jpg"
    assert media.alt_text == "A diagram of an agent loop"
    assert (media.width, media.height) == (1200, 800)


def test_visibility_wrapper_legacy_user_and_video():
    post = _by_id(parse("Bookmarks", s.rich_bookmarks()))["x:101"]
    assert post.author_handle == "old_bob"
    [media] = post.media
    assert media.kind == "video"
    assert media.remote_url.endswith("/img/poster.jpg")
    assert media.video_url == "https://video.twimg.com/high.mp4"
    assert post.text == "Limited visibility post"


def test_long_note_tweet_text_is_used():
    post = _by_id(parse("Bookmarks", s.rich_bookmarks()))["x:102"]
    assert post.text.startswith("This is the full long post about MCP servers")
    assert post.text.endswith("Read https://docs.example.org/mcp")
    assert [link.url for link in post.links] == ["https://docs.example.org/mcp"]


def test_quote_card_and_retweet():
    posts = _by_id(parse("Bookmarks", s.rich_bookmarks()))
    assert posts["x:103"].quoted_post_id == "x:200"
    assert posts["x:200"].media[0].alt_text is None

    card = posts["x:104"]
    assert card.links[0].title == "How we built it"
    assert card.links[0].description == "A deep dive"
    assert card.media[-1].kind == "card"
    assert card.media[-1].remote_url.startswith("https://pbs.twimg.com/card_img/")

    assert "x:105" not in posts  # the retweet wrapper resolves to the original
    assert posts["x:300"].author_handle == "dave"


def test_likes_and_folder_timeline():
    likes = parse("Likes", s.likes_payload([s.entry(s.tweet("400"), "5")]))
    assert [(m.post_id, m.collection) for m in likes.memberships] == [("x:400", "likes")]

    url = s.graphql_url("BookmarkFolderTimeline", {"bookmark_collection_id": "77", "count": 20})
    folder = parse("BookmarkFolderTimeline", s.folder_payload([s.entry(s.tweet("401"), "9")]), url)
    assert {(m.post_id, m.collection) for m in folder.memberships} == {
        ("x:401", "bookmarks"),
        ("x:401", "folder:77"),
    }


def test_folders_slice():
    result = parse("bookmarkFoldersSlice", s.folders_slice_payload([("77", "AI"), ("78", "Art")]))
    assert [(f.folder_id, f.name) for f in result.folders] == [("77", "AI"), ("78", "Art")]


def test_unknown_operation_and_bad_payload_are_ignored():
    assert parse("HomeTimeline", s.rich_bookmarks()).posts == []
    assert parse("Bookmarks", ["not", "a", "dict"]).posts == []
    assert parse("Bookmarks", {"data": {}}).posts == []


def test_falls_back_to_recursive_instruction_search():
    payload = {
        "data": {"some_new_wrapper": {"timeline": s.timeline([s.entry(s.tweet("500"), "1")])}}
    }
    assert [p.id for p in parse("Bookmarks", payload).posts] == ["x:500"]
