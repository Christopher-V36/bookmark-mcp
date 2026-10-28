import sqlite3

from bookmark_mcp import config


def _use_home(tmp_path, monkeypatch):
    monkeypatch.delenv("BOOKMARK_MCP_HOME", raising=False)
    monkeypatch.setattr(config.Path, "home", lambda: tmp_path / "user")
    legacy = tmp_path / "appdata" / "bookmark-mcp"
    monkeypatch.setattr(config, "legacy_data_dir", lambda: legacy)
    return legacy


def test_default_folder_is_in_the_home_folder(tmp_path, monkeypatch):
    _use_home(tmp_path, monkeypatch)
    assert config.data_dir() == tmp_path / "user" / ".bookmark-mcp"


def test_old_appdata_folder_is_copied_once(tmp_path, monkeypatch):
    legacy = _use_home(tmp_path, monkeypatch)
    (legacy / "media" / "x").mkdir(parents=True)
    (legacy / "media" / "x" / "a.jpg").write_bytes(b"img")
    (legacy / "token.txt").write_text("old-token")
    with sqlite3.connect(legacy / "bookmark-mcp.db") as db:
        db.execute("CREATE TABLE posts (id TEXT)")
        db.execute("INSERT INTO posts VALUES ('x:1')")
    db.close()

    new = config.data_dir()
    assert (new / "media" / "x" / "a.jpg").read_bytes() == b"img"
    assert config.token() == "old-token"
    with sqlite3.connect(new / "bookmark-mcp.db") as copy:
        assert copy.execute("SELECT id FROM posts").fetchall() == [("x:1",)]
    copy.close()
    assert (legacy / "bookmark-mcp.db").exists()  # the old folder is left alone

    (legacy / "token.txt").write_text("changed later")
    assert config.token() == "old-token"  # copied only the first time
