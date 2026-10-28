-- bookmark-mcp cloud database (Cloudflare D1 / SQLite).
-- The PC is the source of posts; edits made in the web app are tracked in web_edits
-- so the PC can pull them back and a later push does not overwrite them.

CREATE TABLE IF NOT EXISTS posts (
  id TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  author_handle TEXT,
  author_name TEXT,
  created_at TEXT,
  text TEXT NOT NULL DEFAULT '',
  article_title TEXT,
  article_text TEXT,
  quoted_post_id TEXT,
  metrics TEXT NOT NULL DEFAULT '{}',
  links TEXT NOT NULL DEFAULT '[]',
  note TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at);

CREATE TABLE IF NOT EXISTS collections (
  post_id TEXT NOT NULL,
  collection TEXT NOT NULL,
  sort_index TEXT,
  PRIMARY KEY (post_id, collection)
);
CREATE INDEX IF NOT EXISTS idx_collections_name ON collections(collection);

CREATE TABLE IF NOT EXISTS folders (
  key TEXT PRIMARY KEY,
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media (
  id TEXT PRIMARY KEY,
  post_id TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL,
  alt_text TEXT,
  video_url TEXT,
  width INTEGER,
  height INTEGER,
  sha TEXT,
  mime TEXT,
  size INTEGER,
  data BLOB
);
CREATE INDEX IF NOT EXISTS idx_media_post ON media(post_id);

CREATE TABLE IF NOT EXISTS labels (
  post_id TEXT PRIMARY KEY,
  intent TEXT NOT NULL,
  usefulness INTEGER NOT NULL,
  effort TEXT,
  summary TEXT,
  action TEXT,
  status TEXT NOT NULL DEFAULT 'todo',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS label_topics (
  post_id TEXT NOT NULL,
  topic TEXT NOT NULL,
  PRIMARY KEY (post_id, topic)
);
CREATE INDEX IF NOT EXISTS idx_label_topics_topic ON label_topics(topic);

CREATE TABLE IF NOT EXISTS tags (
  post_id TEXT NOT NULL,
  tag TEXT NOT NULL,
  PRIMARY KEY (post_id, tag)
);

CREATE TABLE IF NOT EXISTS web_edits (
  post_id TEXT PRIMARY KEY,
  edited_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
  post_id UNINDEXED, author, text, extra,
  tokenize = 'porter unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS users (
  email TEXT PRIMARY KEY,
  pw_hash TEXT NOT NULL,
  salt TEXT NOT NULL,
  iterations INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS login_failures (
  ip TEXT PRIMARY KEY,
  count INTEGER NOT NULL,
  window_start INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
