import { buildIngestBody, checkHealth, normalizeServerUrl, sendToApp } from '../src/background/ingest';
import type { CaptureMessage, Settings } from '../src/shared/messages';

const capture: CaptureMessage = {
  type: 'capture',
  platform: 'x',
  operation: 'Bookmarks',
  url: 'https://x.com/i/api/graphql/q/Bookmarks',
  pageUrl: 'https://x.com/i/bookmarks',
  capturedAt: '2026-10-27T10:00:00.000Z',
  body: '{"data":{"bookmark_timeline_v2":{}}}',
};

const settings: Settings = { serverUrl: 'http://127.0.0.1:48923/', token: 'tok', enabled: {} };

function fakeFetch(status: number, body: unknown = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const impl = (async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(body), { status });
  }) as unknown as typeof fetch;
  return { impl, calls };
}

describe('buildIngestBody', () => {
  it('wraps the captured JSON', () => {
    expect(buildIngestBody(capture)).toEqual({
      version: 1,
      platform: 'x',
      operation: 'Bookmarks',
      url: capture.url,
      page_url: capture.pageUrl,
      captured_at: capture.capturedAt,
      payload: { data: { bookmark_timeline_v2: {} } },
    });
  });

  it('drops replies that are not JSON objects', () => {
    expect(buildIngestBody({ ...capture, body: '<html>' })).toBeNull();
    expect(buildIngestBody({ ...capture, body: '42' })).toBeNull();
  });
});

describe('sendToApp', () => {
  const body = buildIngestBody(capture)!;

  it('posts with the pairing token', async () => {
    const { impl, calls } = fakeFetch(200, {
      posts: 3,
      new_in_collections: 2,
      collections: ['bookmarks'],
    });
    expect(await sendToApp(settings, body, impl)).toEqual({
      ok: true,
      posts: 3,
      newPosts: 2,
      collections: ['bookmarks'],
    });
    // an older app that doesn't report new posts
    expect(await sendToApp(settings, body, fakeFetch(200, { posts: 3 }).impl)).toMatchObject({
      newPosts: null,
    });
    expect(calls[0]?.url).toBe('http://127.0.0.1:48923/ingest');
    expect((calls[0]?.init?.headers as Record<string, string>).Authorization).toBe('Bearer tok');
  });

  it('keeps captures for retry when the app is down or the token is wrong', async () => {
    const down = (async () => {
      throw new TypeError('Failed to fetch');
    }) as unknown as typeof fetch;
    expect(await sendToApp(settings, body, down)).toMatchObject({ ok: false, retry: true });
    expect(await sendToApp(settings, body, fakeFetch(401).impl)).toMatchObject({
      ok: false,
      retry: true,
    });
    expect(await sendToApp({ ...settings, token: '' }, body, fakeFetch(200).impl)).toMatchObject({
      ok: false,
      retry: true,
    });
  });

  it('gives up on captures the app rejects', async () => {
    expect(await sendToApp(settings, body, fakeFetch(413).impl)).toMatchObject({ retry: false });
  });
});

describe('helpers', () => {
  it('normalises the server url', () => {
    expect(normalizeServerUrl(' http://localhost:1234/// ')).toBe('http://localhost:1234');
    expect(normalizeServerUrl('')).toBe('http://127.0.0.1:48923');
  });

  it('reports health', async () => {
    expect(await checkHealth(settings, fakeFetch(200, { ok: true, posts: 5 }).impl)).toMatchObject({
      ok: true,
      posts: 5,
    });
    expect(await checkHealth(settings, fakeFetch(401).impl)).toEqual({
      ok: false,
      error: 'Wrong pairing token.',
    });
  });
});
