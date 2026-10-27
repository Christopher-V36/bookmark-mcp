import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from bookmark_mcp import mcp_server, tools, triage
from bookmark_mcp.ingest import ingest_event
from tests import x_samples as s


@pytest.fixture
def loaded(db, monkeypatch):
    ingest_event(db, "x", "Bookmarks", s.rich_bookmarks())
    ingest_event(db, "x", "Likes", s.likes_payload([s.entry(s.tweet("400", "A meme"), "1")]))
    monkeypatch.setattr(mcp_server, "_db", db)
    return db


LABELS = [
    {
        "post_id": "x:100",
        "intent": "learn",
        "usefulness": 3,
        "effort": "session",
        "topics": ["AI Agents", "agents loop"],
        "summary": "Thread on agent loops",
        "action": "Read the thread and build a tiny agent loop",
    },
    {
        "post_id": "x:102",
        "intent": "try",
        "usefulness": 2,
        "effort": "quick",
        "topics": "mcp, claude-code",
        "summary": "MCP servers with images",
        "action": "Install an MCP server that returns images",
    },
    {
        "post_id": "x:104",
        "intent": "build",
        "usefulness": 3,
        "effort": "project",
        "topics": ["side-project"],
        "summary": "How they built it",
        "action": "Clone the idea as a weekend project",
    },
    {"post_id": "x:400", "intent": "noise", "usefulness": 0, "topics": []},
]


def test_batch_returns_only_saved_unlabelled_posts(loaded):
    first = triage.batch(loaded, limit=50)
    ids = {p["id"] for p in first["posts"]}
    assert "x:200" not in ids  # quoted-only posts are not saved, so not triaged
    assert len(ids) == first["remaining_including_these"] == 8  # 7 bookmarks + 1 like
    quoting = next(p for p in first["posts"] if p["id"] == "x:103")
    assert quoting["quotes"]["author"] == "@carol"
    plain = next(p for p in first["posts"] if p["id"] == "x:100")
    assert plain["images"] == 1 and plain["image_descriptions"] == ["A diagram of an agent loop"]

    triage.save_labels(loaded, LABELS)
    second = triage.batch(loaded, limit=50)
    assert {p["id"] for p in second["posts"]} == ids - {"x:100", "x:102", "x:104", "x:400"}
    assert triage.batch(loaded, collection="likes")["returned"] == 0


def test_save_labels_validates_and_normalises(loaded):
    result = triage.save_labels(
        loaded,
        [
            *LABELS,
            {"post_id": "x:101", "intent": "maybe", "usefulness": 2},
            {"post_id": "x:101", "intent": "try", "usefulness": 7},
            {"post_id": "x:101", "intent": "try", "usefulness": 1, "effort": "forever"},
            {"post_id": "x:999", "intent": "try", "usefulness": 1},
            {"intent": "try", "usefulness": 1},
        ],
    )
    assert result["saved"] == 4 and len(result["errors"]) == 5
    assert result["remaining"] == 4
    labels = triage.get_labels(loaded, "x:100")
    assert labels["topics"] == ["agents-loop", "ai-agents"]
    assert labels["status"] == "todo"
    assert triage.get_labels(loaded, "x:102")["topics"] == ["claude-code", "mcp"]


def test_relabel_keeps_status(loaded):
    triage.save_labels(loaded, LABELS)
    assert triage.set_status(loaded, "x:100", "doing")["ok"]
    triage.save_labels(loaded, [{**LABELS[0], "usefulness": 2}])
    labels = triage.get_labels(loaded, "x:100")
    assert labels["usefulness"] == 2 and labels["status"] == "doing"
    assert not triage.set_status(loaded, "x:100", "maybe")["ok"]
    assert not triage.set_status(loaded, "x:101", "done")["ok"]  # not labelled yet


def test_next_up_ranking_and_filters(loaded):
    triage.save_labels(loaded, LABELS)
    ranked = [i["post_id"] for i in triage.next_up(loaded)["items"]]
    # most useful first; at equal usefulness try > build > learn; noise never shows up
    assert ranked == ["x:104", "x:100", "x:102"]
    triage.set_status(loaded, "x:102", "doing")
    assert triage.next_up(loaded)["items"][0]["post_id"] == "x:102"  # in progress first
    triage.set_status(loaded, "x:104", "done")
    assert "x:104" not in [i["post_id"] for i in triage.next_up(loaded)["items"]]
    assert [i["post_id"] for i in triage.next_up(loaded, effort="quick")["items"]] == ["x:102"]
    assert [i["post_id"] for i in triage.next_up(loaded, topic="AI Agents")["items"]] == ["x:100"]


def test_labels_are_searchable_and_filterable(loaded):
    triage.save_labels(loaded, LABELS)
    assert [r["id"] for r in loaded.search("weekend")] == ["x:104"]
    result = tools.search_posts(loaded, "thread", labels={"intent": "learn"})
    assert [r["id"] for r in result["results"]] == ["x:100"]
    assert result["results"][0]["labels"]["usefulness"] == 3
    page = tools.browse(loaded, "bookmarks", labels={"min_usefulness": 3})
    assert [r["id"] for r in page["results"]] == ["x:100", "x:104"]
    page = tools.browse(loaded, None, labels={"topic": "mcp"})
    assert [r["id"] for r in page["results"]] == ["x:102"]
    detail, _ = tools.get_post(loaded, "x:104")
    assert detail["labels"]["action"] == "Clone the idea as a weekend project"


def test_progress(loaded):
    triage.save_labels(loaded, LABELS)
    progress = triage.progress(loaded)
    assert progress["saved_posts"] == 8 and progress["labeled"] == 4
    assert progress["remaining"] == 4
    assert progress["by_intent"] == {"build": 1, "learn": 1, "noise": 1, "try": 1}
    assert {"topic": "mcp", "posts": 1} in progress["top_topics"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_triage_over_mcp(loaded):
    async with create_connected_server_and_client_session(mcp_server.mcp._mcp_server) as client:
        prompts = await client.list_prompts()
        assert "triage_bookmarks" in {p.name for p in prompts.prompts}
        prompt = await client.get_prompt("triage_bookmarks", {"batch_size": "10"})
        assert "get_triage_batch(limit=10)" in prompt.messages[0].content.text

        batch = await client.call_tool("get_triage_batch", {"limit": 3})
        assert json.loads(batch.content[0].text)["returned"] == 3
        saved = await client.call_tool("label_posts", {"labels": LABELS[:2]})
        assert json.loads(saved.content[0].text)["saved"] == 2
        nxt = await client.call_tool("next_up", {})
        assert [i["post_id"] for i in json.loads(nxt.content[0].text)["items"]] == [
            "x:100",
            "x:102",
        ]
