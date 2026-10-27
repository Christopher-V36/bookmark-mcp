"""Fail if anything personal is about to be committed.

Idea from x-bookmarks-mcp (MIT, (c) 2026 Poki Balboa). Checks every file git
would commit (tracked + untracked, minus .gitignore) for databases, captured X
responses, downloaded images, secrets and e-mail addresses.

Run: python scripts/check_private_data.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".har", ".zip"}
ALLOWED_IMAGES = re.compile(
    r"^(extension/static/icons/\d+\.png|server/bookmark_mcp/web/icon-\d+\.png)$"
)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

SECRET_PATTERNS = {
    "X auth_token cookie": re.compile(r"auth_token\s*[=:]\s*['\"]?[0-9a-f]{40}"),
    "X ct0 cookie": re.compile(r"ct0\s*[=:]\s*['\"]?[0-9a-f]{32,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "Anthropic key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "OpenAI key": re.compile(r"\bsk-[A-Za-z0-9]{40,}"),
    "long bearer token": re.compile(r"Bearer\s+[A-Za-z0-9%_-]{60,}"),
}
CAPTURE_MARKERS = ("bookmark_timeline_v2", "bookmark_collection_timeline", "\"tweet_results\"")
CAPTURE_ALLOWED = {"server/tests/x_samples.py", "server/bookmark_mcp/platforms/x/parse.py",
                   "scripts/check_private_data.py", "extension/test/ingest.test.ts"}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_ALLOWED = re.compile(r"(noreply@|@example\.|@users\.noreply\.github\.com)")


def candidate_files() -> list[str]:
    output = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [line for line in output.splitlines() if line and (ROOT / line).is_file()]


def check(path: str) -> list[str]:
    problems: list[str] = []
    suffix = Path(path).suffix.lower()
    if suffix in FORBIDDEN_SUFFIXES or path.endswith(("-wal", "-shm")):
        problems.append("database/archive files must not be committed")
    if suffix in IMAGE_SUFFIXES and not ALLOWED_IMAGES.match(path):
        problems.append("images (possibly downloaded media) must not be committed")
    if (ROOT / path).stat().st_size > 2_000_000:
        problems.append("file larger than 2 MB")
    if suffix in IMAGE_SUFFIXES:
        return problems
    try:
        text = (ROOT / path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return problems
    for name, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            problems.append(f"looks like it contains a {name}")
    if path not in CAPTURE_ALLOWED and any(marker in text for marker in CAPTURE_MARKERS):
        problems.append("looks like a captured X response (use synthetic test data)")
    for email in set(EMAIL.findall(text)):
        if not EMAIL_ALLOWED.search(email):
            problems.append(f"contains an e-mail address ({email})")
    return problems


def main() -> int:
    failures = {path: issues for path in candidate_files() if (issues := check(path))}
    for path, issues in failures.items():
        for issue in issues:
            print(f"{path}: {issue}")
    if failures:
        print(f"\n{len(failures)} file(s) may contain private data. Nothing should be published.")
        return 1
    print("No private data found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
