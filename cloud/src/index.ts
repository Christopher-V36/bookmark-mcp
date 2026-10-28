/**
 * bookmark-mcp in the cloud: one Cloudflare Worker serving the web app (static assets), its
 * JSON API backed by D1, email + password login, and the sync endpoints the PC uses.
 */

import * as api from './api';
import { currentUser, login, logout, syncAuthorized } from './auth';
import { HttpError, type Env, error, json, readJson, redirect, secure } from './http';
import * as sync from './sync';

const POST_ROUTE = /^\/api\/posts\/([^/]+)(?:\/(labels|status|note|tags)(?:\/([^/]+))?)?$/;

function sameOrigin(request: Request, url: URL): boolean {
  const site = request.headers.get('Sec-Fetch-Site');
  if (site === 'cross-site' || site === 'same-site') return false;
  const origin = request.headers.get('Origin');
  return !origin || origin === url.origin;
}

async function loginPage(request: Request, env: Env, url: URL): Promise<Response> {
  // the asset server serves login.html at /login (it drops ".html")
  const page = await env.ASSETS.fetch(new Request(new URL('/login', url.origin)));
  const message = url.searchParams.get('error');
  if (!message) return page;
  const text =
    message === 'locked'
      ? 'Too many attempts. Wait 15 minutes and try again.'
      : 'Wrong email or password.';
  return new HTMLRewriter()
    .on('[data-error]', {
      element(el) {
        el.removeAttribute('hidden');
        el.setInnerContent(text);
      },
    })
    .transform(page);
}

async function handleApi(request: Request, env: Env, url: URL): Promise<Response> {
  const path = url.pathname;
  const method = request.method;
  if (method === 'GET' && path === '/api/overview') return json(await api.overview(env));
  if (method === 'GET' && path === '/api/posts') return json(await api.queryPosts(env, url.searchParams));
  if (method === 'GET' && path === '/api/next-up') return json(await api.nextUp(env, url.searchParams));
  const match = POST_ROUTE.exec(path);
  if (!match) throw new HttpError(404, 'not found');
  const postId = decodeURIComponent(match[1] ?? '');
  const action = match[2];
  const extra = match[3];
  if (method === 'GET' && !action) return json(await api.postDetail(env, postId));
  if (method === 'GET') throw new HttpError(405, 'method not allowed');

  if (request.headers.get('X-Bookmark-Mcp') !== '1') throw new HttpError(403, 'missing request header');
  if (!(request.headers.get('Content-Type') ?? '').startsWith('application/json')) {
    throw new HttpError(415, 'send JSON');
  }
  const body = await readJson(request, 64 * 1024);
  if (action === 'labels' && method === 'PATCH') return json({ labels: await api.updateLabels(env, postId, body) });
  if (action === 'status' && method === 'PUT') {
    return json({ labels: await api.setStatus(env, postId, String(body.status ?? '')) });
  }
  if (action === 'note' && method === 'PUT') return json({ note: await api.setNote(env, postId, String(body.note ?? '')) });
  if (action === 'tags' && method === 'POST') return json({ tags: await api.addTag(env, postId, String(body.tag ?? '')) });
  if (action === 'tags' && method === 'DELETE' && extra) {
    return json({ tags: await api.removeTag(env, postId, decodeURIComponent(extra)) });
  }
  throw new HttpError(405, 'method not allowed');
}

async function handleSync(request: Request, env: Env, url: URL): Promise<Response> {
  if (!syncAuthorized(request, env)) throw new HttpError(401, 'wrong sync token');
  const path = url.pathname;
  if (request.method === 'GET' && path === '/sync/status') return sync.status(env);
  if (request.method === 'GET' && path === '/sync/changes') return sync.changes(env, url.searchParams.get('since') ?? '');
  if (request.method === 'POST' && path === '/sync/post') return sync.pushPost(request, env);
  if (request.method === 'POST' && path === '/sync/meta') return sync.pushMeta(request, env);
  if (request.method === 'PUT' && path === '/sync/user') return sync.setUser(request, env);
  const media = /^\/sync\/media\/([^/]+)$/.exec(path);
  if (request.method === 'PUT' && media) return sync.putMedia(request, env, decodeURIComponent(media[1] ?? ''));
  throw new HttpError(404, 'not found');
}

async function route(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname;

  if (path.startsWith('/sync/')) return handleSync(request, env, url);

  if (path === '/login') {
    if (request.method === 'POST') {
      if (!sameOrigin(request, url)) throw new HttpError(403, 'cross-site request refused');
      try {
        return redirect('/', await login(request, env));
      } catch (exc) {
        if (exc instanceof HttpError && exc.status === 429) return redirect('/login?error=locked');
        if (exc instanceof HttpError && exc.status === 401) return redirect('/login?error=1');
        throw exc;
      }
    }
    if (await currentUser(request, env)) return redirect('/');
    return loginPage(request, env, url);
  }
  if (path === '/logout' && request.method === 'POST') {
    if (!sameOrigin(request, url)) throw new HttpError(403, 'cross-site request refused');
    return redirect('/login', await logout(request, env));
  }

  const user = await currentUser(request, env);
  if (path === '/' || path === '/index.html') {
    if (!user) return redirect('/login');
    return env.ASSETS.fetch(request);
  }
  if (path.startsWith('/api/') || path.startsWith('/media/')) {
    if (!user) throw new HttpError(401, 'log in first');
    if (!sameOrigin(request, url)) throw new HttpError(403, 'cross-site request refused');
    if (path.startsWith('/media/')) {
      if (request.method !== 'GET' && request.method !== 'HEAD') throw new HttpError(405, 'method not allowed');
      return api.mediaResponse(env, decodeURIComponent(path.slice('/media/'.length)));
    }
    return handleApi(request, env, url);
  }
  return env.ASSETS.fetch(request);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    try {
      return secure(await route(request, env));
    } catch (exc) {
      if (exc instanceof HttpError) return secure(error(exc.status, exc.message));
      console.error(exc);
      return secure(error(500, 'internal error'));
    }
  },
} satisfies ExportedHandler<Env>;
