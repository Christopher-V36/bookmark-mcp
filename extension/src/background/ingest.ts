/** Talking to the local bookmark-mcp app (pure helpers, easy to test). */

import type { CaptureMessage, HealthReply, Settings } from '../shared/messages';

export const DEFAULT_SERVER_URL = 'http://127.0.0.1:48923';

export interface IngestBody {
  version: 1;
  platform: string;
  operation: string;
  url: string;
  page_url: string;
  captured_at: string;
  payload: unknown;
}

/** Turn a capture into the JSON the app expects; null when the reply is not JSON. */
export function buildIngestBody(capture: CaptureMessage): IngestBody | null {
  let payload: unknown;
  try {
    payload = JSON.parse(capture.body);
  } catch {
    return null;
  }
  if (typeof payload !== 'object' || payload === null) return null;
  return {
    version: 1,
    platform: capture.platform,
    operation: capture.operation,
    url: capture.url,
    page_url: capture.pageUrl,
    captured_at: capture.capturedAt,
    payload,
  };
}

export function normalizeServerUrl(value: string): string {
  const trimmed = value.trim().replace(/\/+$/, '');
  return trimmed || DEFAULT_SERVER_URL;
}

export type SendResult =
  | { ok: true; posts: number; collections: string[] }
  | { ok: false; retry: boolean; error: string };

export async function sendToApp(
  settings: Settings,
  body: IngestBody,
  fetchImpl: typeof fetch = fetch,
): Promise<SendResult> {
  if (!settings.token) {
    return { ok: false, retry: true, error: 'Not paired yet: paste the token in the popup.' };
  }
  let response: Response;
  try {
    response = await fetchImpl(`${normalizeServerUrl(settings.serverUrl)}/ingest`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${settings.token}`,
      },
      body: JSON.stringify(body),
    });
  } catch {
    return {
      ok: false,
      retry: true,
      error: 'bookmark-mcp app is not running (start Claude or `bookmark-mcp serve`).',
    };
  }
  if (response.status === 401) {
    return { ok: false, retry: true, error: 'Wrong pairing token: run `bookmark-mcp token`.' };
  }
  if (response.status === 413 || response.status === 400) {
    return { ok: false, retry: false, error: `The app rejected a capture (${response.status}).` };
  }
  if (!response.ok) {
    return { ok: false, retry: true, error: `The app answered ${response.status}.` };
  }
  const reply = (await response.json().catch(() => ({}))) as {
    posts?: number;
    collections?: string[];
  };
  return { ok: true, posts: reply.posts ?? 0, collections: reply.collections ?? [] };
}

export async function checkHealth(
  settings: Settings,
  fetchImpl: typeof fetch = fetch,
): Promise<HealthReply> {
  try {
    const response = await fetchImpl(`${normalizeServerUrl(settings.serverUrl)}/health`, {
      headers: { Authorization: `Bearer ${settings.token}` },
    });
    const reply = (await response.json().catch(() => ({}))) as HealthReply;
    if (response.status === 401) return { ok: false, error: 'Wrong pairing token.' };
    return { ...reply, ok: response.ok && reply.ok !== false };
  } catch {
    return { ok: false, error: 'Cannot reach the app. Is Claude or `bookmark-mcp serve` running?' };
  }
}
