# bookmark-mcp

**Let your AI research the posts you saved, including their images. Local, free, no API keys.**

You save interesting posts all the time and almost never find them again. bookmark-mcp keeps
a private, searchable copy of everything you save, and gives Claude (or any
[MCP](https://modelcontextprotocol.io) client) tools to search it, read it and **look at the
images**, so you can ask things like:

> *"What have I saved about AI agents this year? Group the ideas and show me the diagrams."*
> *"Find the post with the chart about GPU prices and explain the chart."*
> *"Make a reading list from my bookmarks folder 'Design', most useful first."*

Today it supports **X (Twitter): bookmarks, bookmark folders and likes**. Reddit, Facebook and
others are next. The design is platform-agnostic from day one.

## How it works

```
 Chrome, logged in to x.com as you
 └─ bookmark-mcp extension
      reads the replies x.com already sent to your browser
      (Bookmarks, bookmark folders, Likes) - it never sends its own requests to X
            │  http://127.0.0.1 only, with a pairing token
            ▼
 bookmark-mcp app (Python, on your computer)
      stores raw captures → parses posts → downloads images → indexes everything (SQLite FTS5)
            │  MCP (stdio)
            ▼
 Claude Code / Claude Desktop: search_posts, get_post (+ images), gather_topic, ...
```

- **No ban risk from extra traffic:** the extension never logs in, never calls X's API and never
  sends requests of its own. It copies the responses your browser already received while you
  scrolled. An optional *Load all* button simply scrolls the page for you, slowly.
- **Free:** no X API, no paid services.
- **Private:** everything stays on your computer in `%LOCALAPPDATA%\bookmark-mcp`
  (Windows) / `~/.local/share/bookmark-mcp` (Linux) / `~/Library/Application Support/bookmark-mcp`
  (macOS). The only outgoing connections are to X's image CDN (to download images of your saved
  posts) and, only if you run `bookmark-mcp enrich`, to the websites your saved posts link to.
- **Future-proof:** every captured response is stored untouched. When X changes its format, a
  parser fix plus `bookmark-mcp reparse` rebuilds everything, and nothing is lost.

## Install

You need **Python 3.11+**, **Node 18+** (only to build the extension) and **Chrome** (or another
Chromium browser).

```bash
git clone https://github.com/Christopher-V36/bookmark-mcp.git
cd bookmark-mcp
```

### 1. The app

```bash
cd server
python -m venv .venv
.venv/Scripts/python -m pip install -e .        # Windows
# .venv/bin/python -m pip install -e .          # macOS / Linux
```

Connect it to Claude Code (use the full path to the venv's Python):

```bash
claude mcp add --scope user bookmark-mcp -- "<repo>/server/.venv/Scripts/python.exe" -m bookmark_mcp mcp
```

<details><summary>Claude Desktop instead</summary>

Add this to `claude_desktop_config.json` and restart Claude Desktop:

```json
{
  "mcpServers": {
    "bookmark-mcp": {
      "command": "<repo>/server/.venv/Scripts/python.exe",
      "args": ["-m", "bookmark_mcp", "mcp"]
    }
  }
}
```
</details>

### 2. The browser extension

```bash
cd extension
npm ci
npm run build
```

Then open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked** and pick
the `extension/dist` folder.

### 3. Pair them

```bash
server/.venv/Scripts/bookmark-mcp token
```

Click the extension's icon, paste the token under **Pairing**, then **Save & test**. It should say
*Connected*.

The app receives captures whenever Claude has the MCP server running. To capture without Claude
open, run `bookmark-mcp serve`. If neither is running, the extension keeps captures in a queue
and delivers them later.

## Use it

1. Open <https://x.com/i/bookmarks> (or a bookmark folder, or your Likes) and **reload the page**
   once after installing the extension.
2. Scroll, or press **Load all** in the extension popup, which scrolls to the end slowly.
3. Ask Claude. Examples:
   - "Search my saved posts about prompt caching and summarise what people recommend."
   - "Show me the images of the post <https://x.com/someone/status/123>."
   - "Use gather_topic for 'design systems' and write me a guide with links."
   - "Tag every post about Rust as `rust` and add a note on the best one."

### The web app

```bash
bookmark-mcp web
```

This opens <http://127.0.0.1:48924> in your browser. It is your workbench for everything you
saved, and it shows the same data Claude sees:

- **Next up:** your best things to try, build and learn, with must-dos highlighted.
- **All posts:** search plus filters by collection, kind, usefulness, progress and topic.
  Noise is hidden unless you ask for it.
- **Board:** move posts from *To do* to *Doing* to *Done* by dragging or with buttons.
- **Post details:** the full text, images and playable videos, quoted posts and links, plus
  editable labels, next action, topics, notes and tags. Every change is saved straight away.

Keyboard: `/` searches, `j` / `k` move through the list, `Enter` opens a post, `Esc` closes it.

While it runs it also receives captures from the extension, so you only need this one command.
It is local only: it listens on 127.0.0.1, refuses requests from other websites, and loads no
fonts or scripts from the internet.

### MCP tools

| Tool | What it does |
|---|---|
| `search_posts` | Full-text search over text, authors, image alt text, linked articles, tags and notes. Filters: collection, author, dates, has images. |
| `browse` | List a collection (bookmarks, likes, a folder by name), most recently saved first. |
| `get_post` | One post in full (quoted post, links, metrics, tags, note) **plus its images**. |
| `get_post_images` | Only the images and their alt text. |
| `gather_topic` | All posts on a topic, with linked-article text, ready for a synthesis. |
| `list_collections`, `get_stats`, `sync_status` | What is stored and when it last synced. |
| `add_tag`, `add_note`, `list_tags` | Organise your research. |
| `get_triage_batch`, `label_posts`, `triage_rubric` | Label posts (see below). |
| `next_up` | "What should I try, build or learn next?", best labelled posts first. |
| `set_post_status`, `triage_progress` | Track what you're doing or have done; label stats. |

Search and browse also filter by label: `intent`, `min_usefulness`, `topic`, `status`.

### Label everything: what's worth doing?

Your AI can label every saved post for one goal: **try new technologies, build projects,
learn things**. Each post gets:

- **intent**: `try`, `build`, `learn`, `reference`, `inspiration`, `news` or `noise`
- **usefulness**: 0-3
- **effort**: `quick`, `session` or `project`
- **topics**
- a one-line **summary** and **action**
- a **status** you update as you go: `todo`, `doing`, `done` or `skipped`

The labelling runs in your Claude session (your plan, no extra API cost), in batches, and
resumes if interrupted.

- **Claude Code:** copy `skills/triage-bookmarks` to `~/.claude/skills/`, then run
  `/triage-bookmarks`.
- **Claude Desktop or other clients:** use the server's built-in `triage_bookmarks` prompt.

Afterwards just ask "what should I try next?", "give me quick things to learn about MCP" or
"I'm done with the X post".

### Command line

| Command | |
|---|---|
| `bookmark-mcp web` | Open the web app (and receive captures). |
| `bookmark-mcp serve` | Receive captures without Claude running. |
| `bookmark-mcp token` | Show the pairing token. |
| `bookmark-mcp stats` | What is stored. |
| `bookmark-mcp media [--retry-errors]` | Download images that are still pending. |
| `bookmark-mcp enrich` | Fetch the text of linked articles (visits those websites). |
| `bookmark-mcp import FILE.json` | Import a [twitter-web-exporter](https://github.com/prinsss/twitter-web-exporter) JSON export (tick *Include all metadata*). |
| `bookmark-mcp reparse` | Rebuild posts from stored captures after an update. |
| `bookmark-mcp doctor` | Check the setup. |

## Security notes

- The local API listens on `127.0.0.1` only. It requires the pairing token, refuses requests
  coming from web pages (by `Origin`) and from non-loopback `Host` names, and limits body size.
- Images are only downloaded from X's CDN hosts. Linked pages are fetched only on `enrich`, only
  from public IP addresses (checked again on every redirect), and with size and type limits.
- Posts are written by other people. The MCP tools tell the model to treat post content as data,
  not instructions. Still, be thoughtful about which other tools you let the same agent use.
- `scripts/check_private_data.py` runs in CI and refuses databases, captures, images, secrets and
  e-mail addresses in the repository.

## Development

```bash
cd server && .venv/Scripts/python -m pip install -e ".[dev]" && .venv/Scripts/python -m pytest
cd extension && npm test && npm run typecheck
```

All test data is synthetic. See [docs/adding-a-platform.md](docs/adding-a-platform.md) to add
Reddit, Facebook or anything else.

## Roadmap

- Full threads (capture the conversation when you open a saved post)
- Reddit saved posts and comments
- Facebook saved items
- Key frames from videos
- Firefox build

## Credits

Built on ideas and code from two MIT projects, with thanks:
[twitter-web-exporter](https://github.com/prinsss/twitter-web-exporter) (capture technique and
timeline parsing) and [x-bookmarks-mcp](https://github.com/p0ki/x-bookmarks-mcp) (SQLite/FTS
store, safe link enrichment, MCP tool ideas). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Not affiliated with X Corp., Reddit, Meta or Anthropic. This tool only reads data your own browser
already received, for your personal use. Respect the terms of the platforms you use.

## License

[MIT](LICENSE)
