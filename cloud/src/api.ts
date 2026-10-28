/**
 * The web app's JSON API, ported from server/bookmark_mcp/webapp.py so the same front end
 * works in the cloud. Responses keep the same shapes.
 */

import { HttpError, type Env, now } from './http';

export const INTENTS = ['try', 'build', 'learn', 'reference', 'inspiration', 'news', 'noise'];
export const EFFORTS = ['quick', 'session', 'project'];
export const STATUSES = ['todo', 'doing', 'done', 'skipped'];
const INTENT_PRIORITY = ['try', 'build', 'learn', 'reference', 'inspiration', 'news', 'noise'];

const SORTS: Record<string, string> = {
  useful: 'COALESCE(l.usefulness, -1) DESC, saved_index DESC',
  saved: 'saved_index DESC',
  newest: 'p.created_at DESC',
  oldest: 'p.created_at ASC',
};

type Row = Record<string, unknown>;

export function normalizeTag(tag: string): string {
  return tag
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/** Free text to a safe FTS5 query (same rules as the Python version). */
export function ftsQuery(query: string): string {
  const terms: string[] = [];
  for (const match of query.matchAll(/"([^"]+)"|(\S+)/g)) {
    if (match[1]) {
      terms.push(`"${match[1].replace(/"/g, '')}"`);
      continue;
    }
    const cleaned = (match[2] ?? '').replace(/[^\p{L}\p{N}_]+/gu, ' ').trim();
    if (cleaned) terms.push(cleaned.includes(' ') ? `"${cleaned}"` : `"${cleaned}"*`);
  }
  return terms.join(' AND ');
}

function mediaUrl(id: unknown): string {
  return `/media/${encodeURIComponent(String(id))}`;
}

async function folderNames(env: Env): Promise<Record<string, string>> {
  const { results } = await env.DB.prepare('SELECT key, name FROM folders').all<{ key: string; name: string }>();
  return Object.fromEntries(results.map((r) => [r.key, r.name]));
}

async function resolveCollection(env: Env, value: string | null): Promise<string | null> {
  if (!value) return null;
  const lower = value.toLowerCase();
  if (lower === 'bookmarks' || lower === 'likes') return lower;
  if (value.startsWith('folder:')) return value;
  for (const [key, name] of Object.entries(await folderNames(env))) {
    if (name.toLowerCase() === lower) return key;
  }
  return value;
}

async function topicsFor(env: Env, ids: string[]): Promise<Map<string, string[]>> {
  const map = new Map<string, string[]>();
  if (!ids.length) return map;
  const marks = ids.map(() => '?').join(',');
  const { results } = await env.DB.prepare(
    `SELECT post_id, topic FROM label_topics WHERE post_id IN (${marks}) ORDER BY topic`,
  )
    .bind(...ids)
    .all<{ post_id: string; topic: string }>();
  for (const row of results) map.set(row.post_id, [...(map.get(row.post_id) ?? []), row.topic]);
  return map;
}

export async function overview(env: Env): Promise<Row> {
  const db = env.DB;
  const counts = async (sql: string) => {
    const { results } = await db.prepare(sql).all<{ k: string | number; n: number }>();
    return Object.fromEntries(results.map((r) => [String(r.k), r.n]));
  };
  const [saved, labeled, collections, names, topics, tags, lastSync] = await Promise.all([
    db.prepare('SELECT COUNT(DISTINCT post_id) AS n FROM collections').first<{ n: number }>(),
    db.prepare('SELECT COUNT(*) AS n FROM labels').first<{ n: number }>(),
    db.prepare('SELECT collection, COUNT(*) AS count FROM collections GROUP BY collection ORDER BY count DESC').all<{
      collection: string;
      count: number;
    }>(),
    folderNames(env),
    db.prepare('SELECT topic, COUNT(*) AS count FROM label_topics GROUP BY topic ORDER BY count DESC, topic LIMIT 40').all(),
    db.prepare('SELECT tag, COUNT(*) AS count FROM tags GROUP BY tag ORDER BY count DESC, tag').all(),
    db.prepare("SELECT value FROM meta WHERE key = 'last_capture'").first<{ value: string }>(),
  ]);
  return {
    saved_posts: saved?.n ?? 0,
    labeled: labeled?.n ?? 0,
    remaining: Math.max(0, (saved?.n ?? 0) - (labeled?.n ?? 0)),
    collections: collections.results.map((c) => ({ key: c.collection, name: names[c.collection] ?? c.collection, count: c.count })),
    intents: await counts('SELECT intent AS k, COUNT(*) AS n FROM labels GROUP BY intent'),
    usefulness: await counts('SELECT usefulness AS k, COUNT(*) AS n FROM labels GROUP BY usefulness'),
    statuses: await counts('SELECT status AS k, COUNT(*) AS n FROM labels GROUP BY status'),
    efforts: await counts('SELECT effort AS k, COUNT(*) AS n FROM labels GROUP BY effort'),
    topics: topics.results,
    tags: tags.results,
    last_capture: lastSync?.value ?? null,
    cloud: true,
  };
}

export async function queryPosts(env: Env, q: URLSearchParams): Promise<Row> {
  const where = ['EXISTS (SELECT 1 FROM collections c0 WHERE c0.post_id = p.id)'];
  const params: unknown[] = [];
  const collection = await resolveCollection(env, q.get('collection'));
  if (collection) {
    where.push('EXISTS (SELECT 1 FROM collections c WHERE c.post_id = p.id AND c.collection = ?)');
    params.push(collection);
  }
  const author = q.get('author');
  if (author) {
    where.push('(LOWER(p.author_handle) = LOWER(?) OR LOWER(p.author_name) LIKE LOWER(?))');
    params.push(author.replace(/^@/, ''), `%${author}%`);
  }
  const hasImages = q.get('has_images');
  if (hasImages === 'true' || hasImages === 'false') {
    const clause = 'EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id)';
    where.push(hasImages === 'true' ? clause : `NOT ${clause}`);
  }
  for (const field of ['intent', 'effort', 'status']) {
    const value = q.get(field);
    if (value) {
      where.push(`l.${field} = ?`);
      params.push(value);
    }
  }
  const minUseful = q.get('min_usefulness');
  if (minUseful !== null && minUseful !== '') {
    const value = Number(minUseful);
    if (!Number.isInteger(value) || value < 0 || value > 3) throw new HttpError(400, 'min_usefulness must be 0-3');
    where.push('l.usefulness >= ?');
    params.push(value);
  }
  const topic = q.get('topic');
  if (topic) {
    where.push('EXISTS (SELECT 1 FROM label_topics t WHERE t.post_id = p.id AND t.topic = ?)');
    params.push(normalizeTag(topic));
  }
  if (q.get('labeled') === 'false') where.push('l.post_id IS NULL');
  if (q.get('hide_noise') !== 'false' && q.get('intent') !== 'noise') {
    where.push("(l.intent IS NULL OR l.intent <> 'noise')");
  }
  const text = (q.get('q') ?? '').trim();
  if (text) {
    const expression = ftsQuery(text);
    if (expression) {
      where.push('p.id IN (SELECT post_id FROM posts_fts WHERE posts_fts MATCH ?)');
      params.push(expression);
    }
  }

  const limit = Math.max(1, Math.min(Number(q.get('limit') ?? 40) || 40, 200));
  const offset = Math.max(0, Number(q.get('offset') ?? 0) || 0);
  const sort = SORTS[q.get('sort') ?? ''] ?? (text ? SORTS.useful : SORTS.saved);
  const indexFilter = collection ? 'AND c.collection = ?' : '';
  const base = `FROM posts p LEFT JOIN labels l ON l.post_id = p.id WHERE ${where.join(' AND ')}`;

  const total = await env.DB.prepare(`SELECT COUNT(*) AS n ${base}`)
    .bind(...params)
    .first<{ n: number }>();
  const { results } = await env.DB.prepare(
    `SELECT p.id, p.url, p.author_handle, p.author_name, p.created_at, p.text, p.article_title,
            l.intent, l.usefulness, l.effort, l.summary, l.action, l.status,
            (SELECT MAX(CAST(c.sort_index AS INTEGER)) FROM collections c
              WHERE c.post_id = p.id ${indexFilter}) AS saved_index,
            (SELECT COUNT(*) FROM media m WHERE m.post_id = p.id) AS media_count,
            (SELECT m.id FROM media m WHERE m.post_id = p.id AND m.data IS NOT NULL
              ORDER BY m.position LIMIT 1) AS thumb_id,
            (SELECT m.kind FROM media m WHERE m.post_id = p.id ORDER BY m.position LIMIT 1) AS thumb_kind
     ${base} ORDER BY ${sort}, p.id DESC LIMIT ? OFFSET ?`,
  )
    .bind(...(collection ? [collection] : []), ...params, limit, offset)
    .all<Row>();
  const topics = await topicsFor(env, results.map((r) => String(r.id)));
  return {
    total: total?.n ?? 0,
    offset,
    limit,
    items: results.map((r) => ({
      id: r.id,
      url: r.url,
      author: r.author_handle,
      author_name: r.author_name,
      created_at: r.created_at,
      text: String(r.text ?? '').slice(0, 500),
      article_title: r.article_title,
      intent: r.intent ?? null,
      usefulness: r.usefulness ?? null,
      effort: r.effort ?? null,
      summary: r.summary ?? null,
      action: r.action ?? null,
      status: r.status ?? null,
      topics: topics.get(String(r.id)) ?? [],
      media_count: r.media_count,
      thumb: r.thumb_id ? mediaUrl(r.thumb_id) : null,
      thumb_kind: r.thumb_kind,
    })),
  };
}

export async function nextUp(env: Env, q: URLSearchParams): Promise<Row> {
  const where = ["l.status IN ('todo', 'doing')", 'l.usefulness >= ?'];
  const params: unknown[] = [Math.max(0, Math.min(Number(q.get('min_usefulness') ?? 2) || 0, 3))];
  const intent = q.get('intent');
  if (intent) {
    where.push('l.intent = ?');
    params.push(intent);
  } else {
    where.push("l.intent IN ('try', 'build', 'learn', 'reference')");
  }
  const effort = q.get('effort');
  if (effort) {
    where.push('l.effort = ?');
    params.push(effort);
  }
  const topic = q.get('topic');
  if (topic) {
    where.push('EXISTS (SELECT 1 FROM label_topics t WHERE t.post_id = l.post_id AND t.topic = ?)');
    params.push(normalizeTag(topic));
  }
  const priority = INTENT_PRIORITY.map((name, i) => `WHEN '${name}' THEN ${i}`).join(' ');
  const limit = Math.max(1, Math.min(Number(q.get('limit') ?? 10) || 10, 50));
  const { results } = await env.DB.prepare(
    `SELECT l.post_id, l.intent, l.usefulness, l.effort, l.summary, l.action, l.status,
            p.url, p.author_handle, p.created_at
     FROM labels l JOIN posts p ON p.id = l.post_id
     WHERE ${where.join(' AND ')}
     ORDER BY l.status = 'doing' DESC, l.usefulness DESC, CASE l.intent ${priority} ELSE 99 END,
              p.created_at DESC
     LIMIT ?`,
  )
    .bind(...params, limit)
    .all<Row>();
  const topics = await topicsFor(env, results.map((r) => String(r.post_id)));
  return {
    count: results.length,
    items: results.map((r) => ({ ...r, topics: topics.get(String(r.post_id)) ?? [] })),
  };
}

async function labelsFor(env: Env, postId: string): Promise<Row | null> {
  const row = await env.DB.prepare(
    'SELECT intent, usefulness, effort, summary, action, status, updated_at AS labeled_at FROM labels WHERE post_id = ?',
  )
    .bind(postId)
    .first<Row>();
  if (!row) return null;
  return { ...row, topics: (await topicsFor(env, [postId])).get(postId) ?? [] };
}

async function mediaList(env: Env, postId: string): Promise<Row[]> {
  const { results } = await env.DB.prepare(
    `SELECT id, kind, alt_text, video_url, width, height, data IS NOT NULL AS has_data
     FROM media WHERE post_id = ? ORDER BY position`,
  )
    .bind(postId)
    .all<Row>();
  return results.map((m) => ({
    kind: m.kind,
    alt_text: m.alt_text,
    status: m.has_data ? 'done' : 'pending',
    image: m.has_data ? mediaUrl(m.id) : null,
    original: m.has_data ? mediaUrl(m.id) : null,
    video_url: m.video_url,
    width: m.width,
    height: m.height,
  }));
}

async function tagsFor(env: Env, postId: string): Promise<string[]> {
  const { results } = await env.DB.prepare('SELECT tag FROM tags WHERE post_id = ? ORDER BY tag')
    .bind(postId)
    .all<{ tag: string }>();
  return results.map((r) => r.tag);
}

export async function postDetail(env: Env, postId: string): Promise<Row> {
  const post = await env.DB.prepare('SELECT * FROM posts WHERE id = ?').bind(postId).first<Row>();
  if (!post) throw new HttpError(404, `No saved post with id ${postId}`);
  const names = await folderNames(env);
  const { results: collections } = await env.DB.prepare(
    'SELECT collection FROM collections WHERE post_id = ? ORDER BY collection',
  )
    .bind(postId)
    .all<{ collection: string }>();
  let quoted: Row | null = null;
  if (post.quoted_post_id) {
    const q = await env.DB.prepare('SELECT * FROM posts WHERE id = ?').bind(post.quoted_post_id).first<Row>();
    if (q) {
      quoted = {
        id: q.id,
        url: q.url,
        author: q.author_handle,
        author_name: q.author_name,
        text: q.text,
        media: await mediaList(env, String(q.id)),
      };
    }
  }
  return {
    id: post.id,
    url: post.url,
    author: post.author_handle,
    author_name: post.author_name,
    created_at: post.created_at,
    text: post.text,
    metrics: JSON.parse(String(post.metrics ?? '{}')),
    article: post.article_title || post.article_text ? { title: post.article_title, text: post.article_text } : null,
    collections: collections.map((c) => names[c.collection] ?? c.collection),
    media: await mediaList(env, postId),
    links: JSON.parse(String(post.links ?? '[]')),
    labels: await labelsFor(env, postId),
    tags: await tagsFor(env, postId),
    note: post.note ?? null,
    quoted,
  };
}

// --- edits from the web app -----------------------------------------------------------

export async function refreshFts(env: Env, postId: string): Promise<void> {
  const post = await env.DB.prepare(
    'SELECT author_handle, author_name, text, article_title, article_text, note, links FROM posts WHERE id = ?',
  )
    .bind(postId)
    .first<Row>();
  if (!post) return;
  const [labels, topics, tags, media] = await Promise.all([
    env.DB.prepare('SELECT summary, action FROM labels WHERE post_id = ?').bind(postId).first<Row>(),
    env.DB.prepare('SELECT topic FROM label_topics WHERE post_id = ?').bind(postId).all<{ topic: string }>(),
    env.DB.prepare('SELECT tag FROM tags WHERE post_id = ?').bind(postId).all<{ tag: string }>(),
    env.DB.prepare('SELECT alt_text FROM media WHERE post_id = ?').bind(postId).all<{ alt_text: string | null }>(),
  ]);
  const links = JSON.parse(String(post.links ?? '[]')) as Row[];
  const extra = [
    post.article_title,
    post.article_text,
    post.note,
    labels?.summary,
    labels?.action,
    ...topics.results.map((r) => r.topic),
    ...tags.results.map((r) => r.tag),
    ...media.results.map((r) => r.alt_text),
    ...links.map((l) => [l.url, l.title, l.description, l.content].filter(Boolean).join(' ')),
  ]
    .filter(Boolean)
    .join('\n');
  const author = [post.author_handle, post.author_name].filter(Boolean).join(' ');
  await env.DB.batch([
    env.DB.prepare('DELETE FROM posts_fts WHERE post_id = ?').bind(postId),
    env.DB.prepare('INSERT INTO posts_fts (post_id, author, text, extra) VALUES (?, ?, ?, ?)').bind(
      postId,
      author,
      post.text ?? '',
      extra,
    ),
  ]);
}

async function markWebEdit(env: Env, postId: string): Promise<void> {
  await env.DB.prepare(
    'INSERT INTO web_edits (post_id, edited_at) VALUES (?, ?) ON CONFLICT(post_id) DO UPDATE SET edited_at = excluded.edited_at',
  )
    .bind(postId, now())
    .run();
  await refreshFts(env, postId);
}

async function requirePost(env: Env, postId: string): Promise<void> {
  const exists = await env.DB.prepare('SELECT 1 AS x FROM posts WHERE id = ?').bind(postId).first();
  if (!exists) throw new HttpError(404, `No saved post with id ${postId}`);
}

export interface LabelInput {
  intent?: unknown;
  usefulness?: unknown;
  effort?: unknown;
  summary?: unknown;
  action?: unknown;
  topics?: unknown;
}

export function validateLabels(merged: LabelInput): {
  intent: string;
  usefulness: number;
  effort: string | null;
  summary: string | null;
  action: string | null;
  topics: string[];
} {
  const intent = String(merged.intent ?? '').toLowerCase();
  if (!INTENTS.includes(intent)) throw new HttpError(400, `intent must be one of ${INTENTS.join(', ')}`);
  const usefulness = Number(merged.usefulness);
  if (!Number.isInteger(usefulness) || usefulness < 0 || usefulness > 3) {
    throw new HttpError(400, 'usefulness must be 0, 1, 2 or 3');
  }
  const effort = merged.effort ? String(merged.effort).toLowerCase() : null;
  if (effort && !EFFORTS.includes(effort)) throw new HttpError(400, `effort must be one of ${EFFORTS.join(', ')}`);
  const rawTopics = Array.isArray(merged.topics)
    ? merged.topics
    : typeof merged.topics === 'string'
      ? merged.topics.split(',')
      : [];
  const topics = [...new Set(rawTopics.map((t) => normalizeTag(String(t))).filter(Boolean))].slice(0, 5);
  const text = (value: unknown) => (value ? String(value).trim().slice(0, 300) || null : null);
  return { intent, usefulness, effort, summary: text(merged.summary), action: text(merged.action), topics };
}

export async function updateLabels(env: Env, postId: string, body: LabelInput): Promise<Row | null> {
  await requirePost(env, postId);
  const current = (await labelsFor(env, postId)) ?? {};
  const merged: LabelInput = { ...current };
  for (const key of ['intent', 'usefulness', 'effort', 'summary', 'action', 'topics'] as const) {
    if (key in body) merged[key] = body[key];
  }
  if (merged.intent == null || merged.usefulness == null) {
    throw new HttpError(400, 'Choose an intent and a usefulness first');
  }
  const label = validateLabels(merged);
  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO labels (post_id, intent, usefulness, effort, summary, action, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(post_id) DO UPDATE SET intent = excluded.intent, usefulness = excluded.usefulness,
         effort = excluded.effort, summary = excluded.summary, action = excluded.action,
         updated_at = excluded.updated_at`,
    ).bind(postId, label.intent, label.usefulness, label.effort, label.summary, label.action, now()),
    env.DB.prepare('DELETE FROM label_topics WHERE post_id = ?').bind(postId),
    ...label.topics.map((topic) =>
      env.DB.prepare('INSERT INTO label_topics (post_id, topic) VALUES (?, ?)').bind(postId, topic),
    ),
  ]);
  await markWebEdit(env, postId);
  return labelsFor(env, postId);
}

export async function setStatus(env: Env, postId: string, status: string): Promise<Row | null> {
  await requirePost(env, postId);
  if (!STATUSES.includes(status)) throw new HttpError(400, `status must be one of ${STATUSES.join(', ')}`);
  const result = await env.DB.prepare('UPDATE labels SET status = ?, updated_at = ? WHERE post_id = ?')
    .bind(status, now(), postId)
    .run();
  if (!result.meta.changes) throw new HttpError(409, `${postId} has no labels yet; label it first`);
  await markWebEdit(env, postId);
  return labelsFor(env, postId);
}

export async function setNote(env: Env, postId: string, note: string): Promise<string | null> {
  await requirePost(env, postId);
  await env.DB.prepare('UPDATE posts SET note = ? WHERE id = ?').bind(note.slice(0, 5000) || null, postId).run();
  await markWebEdit(env, postId);
  const row = await env.DB.prepare('SELECT note FROM posts WHERE id = ?').bind(postId).first<{ note: string | null }>();
  return row?.note ?? null;
}

export async function addTag(env: Env, postId: string, tag: string): Promise<string[]> {
  await requirePost(env, postId);
  const clean = normalizeTag(tag);
  if (!clean) throw new HttpError(400, 'Tag is empty after normalisation');
  await env.DB.prepare('INSERT OR IGNORE INTO tags (post_id, tag) VALUES (?, ?)').bind(postId, clean).run();
  await markWebEdit(env, postId);
  return tagsFor(env, postId);
}

export async function removeTag(env: Env, postId: string, tag: string): Promise<string[]> {
  await requirePost(env, postId);
  await env.DB.prepare('DELETE FROM tags WHERE post_id = ? AND tag = ?').bind(postId, normalizeTag(tag)).run();
  await markWebEdit(env, postId);
  return tagsFor(env, postId);
}

export async function mediaResponse(env: Env, mediaId: string): Promise<Response> {
  const row = await env.DB.prepare('SELECT data, mime, sha FROM media WHERE id = ?')
    .bind(mediaId)
    .first<{ data: ArrayBuffer | number[] | null; mime: string | null; sha: string | null }>();
  if (!row || !row.data) throw new HttpError(404, 'not found');
  const bytes = row.data instanceof ArrayBuffer ? new Uint8Array(row.data) : new Uint8Array(row.data);
  return new Response(bytes, {
    headers: {
      'Content-Type': row.mime ?? 'image/jpeg',
      'Cache-Control': 'private, max-age=604800, immutable',
      ETag: `"${row.sha ?? ''}"`,
    },
  });
}
