# Adding a platform (Reddit, Facebook, ...)

Everything after capture is platform-agnostic: posts, collections, media, links, tags and notes
all carry a `platform` and ids look like `<platform>:<native id>`. Adding a platform means three
small pieces.

## 1. Tell the extension what to copy

Create `extension/src/platforms/<name>.ts` next to `x.ts`:

- a list of the platform's API calls that return your saved items (watch the browser's DevTools
  *Network* tab while you open your saved page);
- a `matchOperation(url)` that returns `{ platform, operation }` for those calls only.

Then add the site to `content_scripts.matches` in `extension/static/manifest.json` and make
`hook.ts` consult your matcher. The hook only **reads replies the page already received**. Never
add code that sends requests to the platform by itself.

## 2. Parse the replies

Create `server/bookmark_mcp/platforms/<name>/parse.py` with:

```python
OPERATIONS = {"savedposts": "saved"}           # lower-cased operation -> meaning

def parse(operation: str, payload, request_url: str | None) -> ParseResult:
    ...  # fill result.posts (with media + links) and result.memberships
```

Register it in `server/bookmark_mcp/ingest.py` (`PARSERS` and `OPERATIONS`) and add the image CDN
host(s) to `ALLOWED_HOSTS` in `server/bookmark_mcp/media.py`.

Collections are free-form strings: use names like `saved`, `upvoted` or `folder:<id>`.

## 3. Test with synthetic data

Write a `tests/<name>_samples.py` factory in the style of `tests/x_samples.py`: **made-up**
accounts, text and URLs that mimic the real response shape. Never commit real captures;
`scripts/check_private_data.py` blocks the common ones in CI.

Because raw captures are stored, users who already captured data before your parser existed
can run `bookmark-mcp reparse` to pick it up.
