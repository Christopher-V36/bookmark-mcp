# Third-party notices

bookmark-mcp builds on ideas and code from these MIT-licensed projects.

## twitter-web-exporter

- Repository: https://github.com/prinsss/twitter-web-exporter
- Used for: the capture technique (hooking `XMLHttpRequest` on x.com to read the
  GraphQL responses the web app already loads) and the timeline/tweet parsing
  rules (`TimelineAddEntries`, `TweetWithVisibilityResults`, tombstones, sort
  indexes). Ported to `extension/src/main-world/hook.ts` and
  `server/bookmark_mcp/platforms/x/parse.py`.

```
MIT License

Copyright (c) 2023 prin

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## x-bookmarks-mcp

- Repository: https://github.com/p0ki/x-bookmarks-mcp
- Used for: the SQLite + FTS5 store with a LIKE fallback, the SSRF-guarded link
  enrichment, the twitter-web-exporter JSON import rules and the MCP tool set.
  Adapted in `server/bookmark_mcp/db.py`, `enrich.py`,
  `importers/twitter_web_exporter.py` and `tools.py`.

```
MIT License

Copyright (c) 2026 Poki Balboa

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
