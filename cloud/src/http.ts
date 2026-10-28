/** Response helpers shared by every route. */

export interface Env {
  DB: D1Database;
  ASSETS: Fetcher;
  SYNC_TOKEN: string;
}

export const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "font-src 'self'",
  "img-src 'self' data:",
  "media-src 'self' https://video.twimg.com",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "base-uri 'none'",
  "form-action 'self'",
].join('; ');

export class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export function secure(response: Response): Response {
  const headers = new Headers(response.headers);
  headers.set('X-Content-Type-Options', 'nosniff');
  headers.set('Referrer-Policy', 'same-origin');
  headers.set('Content-Security-Policy', CSP);
  headers.set('Strict-Transport-Security', 'max-age=31536000');
  return new Response(response.body, { status: response.status, headers });
}

export function json(data: unknown, status = 200, extra: HeadersInit = {}): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...extra },
  });
}

export function error(status: number, message: string): Response {
  return json({ error: message }, status);
}

export function redirect(location: string, cookie?: string): Response {
  const headers: Record<string, string> = { Location: location, 'Cache-Control': 'no-store' };
  if (cookie) headers['Set-Cookie'] = cookie;
  return new Response(null, { status: 303, headers });
}

export function now(): string {
  return new Date().toISOString().replace(/\.\d+Z$/, '+00:00');
}

export async function readJson(request: Request, maxBytes: number): Promise<Record<string, unknown>> {
  const length = Number(request.headers.get('Content-Length') ?? '0');
  if (length > maxBytes) throw new HttpError(413, 'body too large');
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    throw new HttpError(400, 'invalid JSON');
  }
  if (typeof body !== 'object' || body === null || Array.isArray(body)) {
    throw new HttpError(400, 'expected a JSON object');
  }
  return body as Record<string, unknown>;
}

export function timingSafeEqual(a: string, b: string): boolean {
  const encoder = new TextEncoder();
  const left = encoder.encode(a);
  const right = encoder.encode(b);
  let diff = left.length ^ right.length;
  for (let i = 0; i < Math.max(left.length, right.length); i++) {
    diff |= (left[i] ?? 0) ^ (right[i] ?? 0);
  }
  return diff === 0;
}
