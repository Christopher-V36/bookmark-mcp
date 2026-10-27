import pytest

from bookmark_mcp.db import Database


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never touch the real data folder during tests."""
    monkeypatch.setenv("BOOKMARK_MCP_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()
