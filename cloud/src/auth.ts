/**
 * Email + password login for the one owner of this deployment.
 *
 * The password is set from the PC (`bookmark-mcp cloud password`): the PC sends only a
 * PBKDF2-SHA256 hash and salt. Sessions are random tokens kept as SHA-256 hashes in D1 and
 * sent as an HttpOnly, Secure, SameSite=Strict cookie. Repeated failures from one IP are
 * blocked for 15 minutes.
 */

import { HttpError, type Env, timingSafeEqual } from './http';

export const SESSION_COOKIE = 'bmcp_session';
const SESSION_DAYS = 30;
const MAX_FAILURES = 8;
const FAILURE_WINDOW_SECONDS = 15 * 60;

function toHex(buffer: ArrayBuffer): string {
  return [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function fromHex(hex: string): Uint8Array {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return bytes;
}

export async function sha256Hex(value: string): Promise<string> {
  return toHex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)));
}

export async function pbkdf2Hex(password: string, saltHex: string, iterations: number): Promise<string> {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, [
    'deriveBits',
  ]);
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', hash: 'SHA-256', salt: fromHex(saltHex), iterations },
    key,
    256,
  );
  return toHex(bits);
}

function readCookie(request: Request, name: string): string | null {
  for (const part of (request.headers.get('Cookie') ?? '').split(';')) {
    const [key, ...rest] = part.trim().split('=');
    if (key === name) return rest.join('=');
  }
  return null;
}

export async function currentUser(request: Request, env: Env): Promise<string | null> {
  const token = readCookie(request, SESSION_COOKIE);
  if (!token || token.length < 32) return null;
  const row = await env.DB.prepare('SELECT email, expires_at FROM sessions WHERE token_hash = ?')
    .bind(await sha256Hex(token))
    .first<{ email: string; expires_at: number }>();
  if (!row || row.expires_at < Date.now() / 1000) return null;
  return row.email;
}

function clientIp(request: Request): string {
  return request.headers.get('CF-Connecting-IP') ?? 'unknown';
}

async function tooManyFailures(env: Env, ip: string): Promise<boolean> {
  const row = await env.DB.prepare('SELECT count, window_start FROM login_failures WHERE ip = ?')
    .bind(ip)
    .first<{ count: number; window_start: number }>();
  if (!row) return false;
  if (Date.now() / 1000 - row.window_start > FAILURE_WINDOW_SECONDS) return false;
  return row.count >= MAX_FAILURES;
}

async function recordFailure(env: Env, ip: string): Promise<void> {
  const nowSeconds = Math.floor(Date.now() / 1000);
  await env.DB.prepare(
    `INSERT INTO login_failures (ip, count, window_start) VALUES (?1, 1, ?2)
     ON CONFLICT(ip) DO UPDATE SET
       count = CASE WHEN ?2 - window_start > ?3 THEN 1 ELSE count + 1 END,
       window_start = CASE WHEN ?2 - window_start > ?3 THEN ?2 ELSE window_start END`,
  )
    .bind(ip, nowSeconds, FAILURE_WINDOW_SECONDS)
    .run();
}

/** Check the form; returns a Set-Cookie value on success. */
export async function login(request: Request, env: Env): Promise<string> {
  const ip = clientIp(request);
  if (await tooManyFailures(env, ip)) throw new HttpError(429, 'too many attempts');
  const form = await request.formData();
  const email = String(form.get('email') ?? '').trim().toLowerCase();
  const password = String(form.get('password') ?? '');
  const user = email
    ? await env.DB.prepare('SELECT pw_hash, salt, iterations FROM users WHERE email = ?')
        .bind(email)
        .first<{ pw_hash: string; salt: string; iterations: number }>()
    : null;
  const ok =
    user !== null &&
    password.length > 0 &&
    timingSafeEqual(await pbkdf2Hex(password, user.salt, user.iterations), user.pw_hash);
  if (!ok) {
    await recordFailure(env, ip);
    throw new HttpError(401, 'wrong email or password');
  }
  await env.DB.prepare('DELETE FROM login_failures WHERE ip = ?').bind(ip).run();
  const token = toHex(crypto.getRandomValues(new Uint8Array(32)).buffer as ArrayBuffer);
  const expires = Math.floor(Date.now() / 1000) + SESSION_DAYS * 86400;
  await env.DB.batch([
    env.DB.prepare('DELETE FROM sessions WHERE expires_at < ?').bind(Math.floor(Date.now() / 1000)),
    env.DB.prepare('INSERT INTO sessions (token_hash, email, expires_at) VALUES (?, ?, ?)').bind(
      await sha256Hex(token),
      email,
      expires,
    ),
  ]);
  return `${SESSION_COOKIE}=${token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${SESSION_DAYS * 86400}`;
}

export async function logout(request: Request, env: Env): Promise<string> {
  const token = readCookie(request, SESSION_COOKIE);
  if (token) {
    await env.DB.prepare('DELETE FROM sessions WHERE token_hash = ?').bind(await sha256Hex(token)).run();
  }
  return `${SESSION_COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0`;
}

export function syncAuthorized(request: Request, env: Env): boolean {
  const header = request.headers.get('Authorization') ?? '';
  const supplied = header.startsWith('Bearer ') ? header.slice(7) : '';
  return Boolean(env.SYNC_TOKEN) && env.SYNC_TOKEN.length >= 32 && timingSafeEqual(supplied, env.SYNC_TOKEN);
}
