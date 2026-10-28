/**
 * Endpoints the PC uses to keep the cloud copy up to date (Bearer SYNC_TOKEN).
 *
 * The PC pushes one post per request (the free plan allows ~50 database queries per request)
 * and uploads images separately. Labels, status, note and tags edited in the web app after the
 * PC last pulled are kept, so a push never overwrites a newer edit made on the phone.
 */

import { refreshFts, validateLabels } from './api';
import { HttpError, type Env, json, now, readJson } from './http';

const MAX_MEDIA_BYTES = 1_800_000;
const MAX_TAGS = 10;

type Obj = Record<string, unknown>;

function arr(value: unknown): Obj[] {
  return Array.isArray(value) ? (value.filter((v) => typeof v === 'object' && v !== null) as Obj[]) : [];
}

function str(value: unknown, max = 100_000): string | null {
  return value === null || value === undefined || value === '' ? null : String(value).slice(0, max);
}

export async function pushPost(request: Request, env: Env): Promise<Response> {
  const body = await readJson(request, 2_000_000);
  const post = body.post as Obj | undefined;
  if (!post || typeof post.id !== 'string' || typeof post.url !== 'string') {
    throw new HttpError(400, 'expected { post: { id, url, ... } }');
  }
  const id = post.id;
  const pulledAt = str(body.pulled_at) ?? '';
  const edit = await env.DB.prepare('SELECT edited_at FROM web_edits WHERE post_id = ?')
    .bind(id)
    .first<{ edited_at: string }>();
  const keepWeb = Boolean(edit && edit.edited_at > pulledAt);

  const db = env.DB;
  const statements: D1PreparedStatement[] = [
    db
      .prepare(
        `INSERT INTO posts (id, url, author_handle, author_name, created_at, text, article_title,
            article_text, quoted_post_id, metrics, links, note, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(id) DO UPDATE SET url = excluded.url, author_handle = excluded.author_handle,
           author_name = excluded.author_name, created_at = excluded.created_at, text = excluded.text,
           article_title = excluded.article_title, article_text = excluded.article_text,
           quoted_post_id = excluded.quoted_post_id, metrics = excluded.metrics, links = excluded.links,
           note = ${keepWeb ? 'posts.note' : 'excluded.note'}, updated_at = excluded.updated_at`,
      )
      .bind(
        id,
        post.url,
        str(post.author_handle, 200),
        str(post.author_name, 200),
        str(post.created_at, 40),
        String(post.text ?? '').slice(0, 50_000),
        str(post.article_title, 1000),
        str(post.article_text, 200_000),
        str(post.quoted_post_id, 100),
        JSON.stringify(post.metrics ?? {}),
        JSON.stringify(arr(post.links).slice(0, 20)),
        str(post.note, 5000),
        now(),
      ),
    db.prepare('DELETE FROM collections WHERE post_id = ?').bind(id),
    ...arr(post.collections)
      .slice(0, 5)
      .map((c) =>
        db
          .prepare('INSERT INTO collections (post_id, collection, sort_index) VALUES (?, ?, ?)')
          .bind(id, String(c.collection), str(c.sort_index, 40)),
      ),
  ];

  const media = arr(post.media).slice(0, 8);
  for (const m of media) {
    statements.push(
      db
        .prepare(
          `INSERT INTO media (id, post_id, position, kind, alt_text, video_url, width, height, sha)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET post_id = excluded.post_id, position = excluded.position,
             kind = excluded.kind, alt_text = excluded.alt_text, video_url = excluded.video_url,
             width = excluded.width, height = excluded.height,
             data = CASE WHEN media.sha IS excluded.sha THEN media.data ELSE NULL END,
             sha = excluded.sha`,
        )
        .bind(
          String(m.id),
          id,
          Number(m.position ?? 0),
          String(m.kind ?? 'photo'),
          str(m.alt_text, 2000),
          str(m.video_url, 2000),
          m.width ?? null,
          m.height ?? null,
          str(m.sha, 80),
        ),
    );
  }

  if (!keepWeb) {
    const labels = post.labels as Obj | null | undefined;
    statements.push(db.prepare('DELETE FROM label_topics WHERE post_id = ?').bind(id));
    if (labels) {
      const label = validateLabels(labels);
      const status = ['todo', 'doing', 'done', 'skipped'].includes(String(labels.status))
        ? String(labels.status)
        : 'todo';
      statements.push(
        db
          .prepare(
            `INSERT INTO labels (post_id, intent, usefulness, effort, summary, action, status, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)
             ON CONFLICT(post_id) DO UPDATE SET intent = excluded.intent, usefulness = excluded.usefulness,
               effort = excluded.effort, summary = excluded.summary, action = excluded.action,
               status = excluded.status, updated_at = excluded.updated_at`,
          )
          .bind(id, label.intent, label.usefulness, label.effort, label.summary, label.action, status, now()),
        ...label.topics.map((topic) =>
          db.prepare('INSERT INTO label_topics (post_id, topic) VALUES (?, ?)').bind(id, topic),
        ),
      );
    } else {
      statements.push(db.prepare('DELETE FROM labels WHERE post_id = ?').bind(id));
    }
    statements.push(db.prepare('DELETE FROM tags WHERE post_id = ?').bind(id));
    const tags = Array.isArray(post.tags) ? post.tags.map(String).slice(0, MAX_TAGS) : [];
    for (const tag of tags) {
      statements.push(db.prepare('INSERT OR IGNORE INTO tags (post_id, tag) VALUES (?, ?)').bind(id, tag));
    }
  }

  await db.batch(statements);
  await refreshFts(env, id);

  const missing = media.length
    ? (
        await db
          .prepare(`SELECT id FROM media WHERE post_id = ? AND data IS NULL`)
          .bind(id)
          .all<{ id: string }>()
      ).results.map((r) => r.id)
    : [];
  return json({ ok: true, id, kept_web_edits: keepWeb, missing_media: missing });
}

export async function pushMeta(request: Request, env: Env): Promise<Response> {
  const body = await readJson(request, 200_000);
  const folders = arr(body.folders).slice(0, 30);
  const statements = folders.map((f) =>
    env.DB.prepare(
      'INSERT INTO folders (key, name) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET name = excluded.name',
    ).bind(String(f.key), String(f.name).slice(0, 200)),
  );
  if (body.last_capture) {
    statements.push(
      env.DB.prepare(
        "INSERT INTO meta (key, value) VALUES ('last_capture', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
      ).bind(String(body.last_capture)),
    );
  }
  if (statements.length) await env.DB.batch(statements);
  return json({ ok: true, folders: folders.length });
}

export async function putMedia(request: Request, env: Env, mediaId: string): Promise<Response> {
  const length = Number(request.headers.get('Content-Length') ?? '0');
  if (length > MAX_MEDIA_BYTES) throw new HttpError(413, 'image too large');
  const mime = request.headers.get('Content-Type') ?? 'image/jpeg';
  if (!mime.startsWith('image/')) throw new HttpError(415, 'images only');
  const data = await request.arrayBuffer();
  if (data.byteLength > MAX_MEDIA_BYTES) throw new HttpError(413, 'image too large');
  const result = await env.DB.prepare('UPDATE media SET data = ?, mime = ?, size = ?, sha = COALESCE(?, sha) WHERE id = ?')
    .bind(data, mime, data.byteLength, request.headers.get('X-Sha'), mediaId)
    .run();
  if (!result.meta.changes) throw new HttpError(404, 'push the post before its images');
  return json({ ok: true, id: mediaId, size: data.byteLength });
}

export async function changes(env: Env, since: string): Promise<Response> {
  const { results } = await env.DB.prepare(
    'SELECT post_id, edited_at FROM web_edits WHERE edited_at > ? ORDER BY edited_at LIMIT 10',
  )
    .bind(since)
    .all<{ post_id: string; edited_at: string }>();
  const posts = [];
  for (const row of results) {
    const [labels, topics, tags, note] = await Promise.all([
      env.DB.prepare('SELECT intent, usefulness, effort, summary, action, status FROM labels WHERE post_id = ?')
        .bind(row.post_id)
        .first<Obj>(),
      env.DB.prepare('SELECT topic FROM label_topics WHERE post_id = ?').bind(row.post_id).all<{ topic: string }>(),
      env.DB.prepare('SELECT tag FROM tags WHERE post_id = ?').bind(row.post_id).all<{ tag: string }>(),
      env.DB.prepare('SELECT note FROM posts WHERE id = ?').bind(row.post_id).first<{ note: string | null }>(),
    ]);
    posts.push({
      id: row.post_id,
      edited_at: row.edited_at,
      labels: labels ? { ...labels, topics: topics.results.map((t) => t.topic) } : null,
      tags: tags.results.map((t) => t.tag),
      note: note?.note ?? null,
    });
  }
  const last = results[results.length - 1];
  return json({ posts, next_since: last ? last.edited_at : since, more: results.length === 10 });
}

export async function setUser(request: Request, env: Env): Promise<Response> {
  const body = await readJson(request, 10_000);
  const email = String(body.email ?? '').trim().toLowerCase();
  const hash = String(body.pw_hash ?? '');
  const salt = String(body.salt ?? '');
  const iterations = Number(body.iterations ?? 0);
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) throw new HttpError(400, 'invalid email');
  if (!/^[0-9a-f]{64}$/.test(hash) || !/^[0-9a-f]{32,}$/.test(salt)) throw new HttpError(400, 'invalid hash');
  if (!Number.isInteger(iterations) || iterations < 10_000 || iterations > 100_000) {
    throw new HttpError(400, 'iterations must be 10000-100000');
  }
  await env.DB.batch([
    env.DB.prepare('DELETE FROM users'),
    env.DB.prepare('DELETE FROM sessions'),
    env.DB.prepare('INSERT INTO users (email, pw_hash, salt, iterations, updated_at) VALUES (?, ?, ?, ?, ?)').bind(
      email,
      hash,
      salt,
      iterations,
      now(),
    ),
  ]);
  return json({ ok: true, email });
}

export async function status(env: Env): Promise<Response> {
  const [posts, saved, labels, media, withData, user] = await Promise.all([
    env.DB.prepare('SELECT COUNT(*) AS n FROM posts').first<{ n: number }>(),
    env.DB.prepare('SELECT COUNT(DISTINCT post_id) AS n FROM collections').first<{ n: number }>(),
    env.DB.prepare('SELECT COUNT(*) AS n FROM labels').first<{ n: number }>(),
    env.DB.prepare('SELECT COUNT(*) AS n FROM media').first<{ n: number }>(),
    env.DB.prepare('SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS bytes FROM media WHERE data IS NOT NULL').first<{
      n: number;
      bytes: number;
    }>(),
    env.DB.prepare('SELECT email FROM users LIMIT 1').first<{ email: string }>(),
  ]);
  return json({
    posts: posts?.n ?? 0,
    saved_posts: saved?.n ?? 0,
    labels: labels?.n ?? 0,
    media: media?.n ?? 0,
    media_uploaded: withData?.n ?? 0,
    media_bytes: withData?.bytes ?? 0,
    login_email: user?.email ?? null,
  });
}
