/**
 * Background service worker: receives captures from x.com tabs and delivers them to
 * the local bookmark-mcp app. When the app is not running, captures wait in an
 * IndexedDB queue and are retried every minute.
 */

import { defaultEnabled } from '../platforms/x';
import type {
  CaptureReply,
  Settings,
  Stats,
  StatusReply,
  WorkerRequest,
} from '../shared/messages';
import { DEFAULT_SERVER_URL, buildIngestBody, checkHealth, sendToApp } from './ingest';
import * as queue from './queue';

const MAX_ATTEMPTS = 500;
const FLUSH_ALARM = 'flush-queue';

const EMPTY_STATS: Stats = {
  captured: 0,
  sent: 0,
  failed: 0,
  lastSentAt: null,
  lastError: null,
  perCollection: {},
};

async function getSettings(): Promise<Settings> {
  const { settings } = (await chrome.storage.local.get('settings')) as { settings?: Settings };
  return {
    serverUrl: settings?.serverUrl || DEFAULT_SERVER_URL,
    token: settings?.token || '',
    enabled: { ...defaultEnabled(), ...(settings?.enabled ?? {}) },
  };
}

async function getStats(): Promise<Stats> {
  const { stats } = (await chrome.storage.local.get('stats')) as { stats?: Stats };
  return { ...EMPTY_STATS, ...(stats ?? {}) };
}

let statsChain: Promise<unknown> = Promise.resolve();

/** Serialise read-modify-write updates of the counters. */
function updateStats(change: (stats: Stats) => void): Promise<void> {
  const next = statsChain.then(async () => {
    const stats = await getStats();
    change(stats);
    await chrome.storage.local.set({ stats });
  });
  statsChain = next.catch(() => undefined);
  return next;
}

async function refreshBadge(): Promise<void> {
  const [waiting, stats] = await Promise.all([queue.count(), getStats()]);
  await chrome.action.setBadgeBackgroundColor({ color: stats.lastError ? '#d93025' : '#1a73e8' });
  await chrome.action.setBadgeText({ text: waiting > 0 ? String(Math.min(waiting, 999)) : '' });
}

let flushing: Promise<void> | null = null;
/** What the app said about recently delivered captures, by queue id (kept briefly). */
const delivered = new Map<number, { posts: number; newPosts: number | null }>();
const DELIVERED_KEPT = 200;

function flush(): Promise<void> {
  flushing ??= (async () => {
    try {
      const settings = await getSettings();
      for (;;) {
        const items = await queue.peek(20);
        if (items.length === 0) break;
        for (const item of items) {
          const result = await sendToApp(settings, item.body);
          if (result.ok) {
            await queue.remove(item.id);
            delivered.set(item.id, { posts: result.posts, newPosts: result.newPosts });
            if (delivered.size > DELIVERED_KEPT) {
              delivered.delete(delivered.keys().next().value as number);
            }
            await updateStats((stats) => {
              stats.sent += 1;
              stats.lastSentAt = new Date().toISOString();
              stats.lastError = null;
              for (const name of result.collections) {
                stats.perCollection[name] = (stats.perCollection[name] ?? 0) + result.posts;
              }
            });
          } else if (!result.retry || item.attempts + 1 >= MAX_ATTEMPTS) {
            await queue.remove(item.id);
            await updateStats((stats) => {
              stats.failed += 1;
              stats.lastError = result.error;
            });
          } else {
            await queue.update({ ...item, attempts: item.attempts + 1 });
            await updateStats((stats) => {
              stats.lastError = result.error;
            });
            return; // the app is unreachable: try again on the next alarm
          }
        }
      }
    } finally {
      flushing = null;
      await refreshBadge();
    }
  })();
  return flushing;
}

async function handleCapture(
  message: Extract<WorkerRequest, { type: 'capture' }>,
): Promise<CaptureReply> {
  const settings = await getSettings();
  if (settings.enabled[message.operation.toLowerCase()] === false) {
    return { ok: true, status: 'skipped', posts: 0, newPosts: null };
  }
  const body = buildIngestBody(message);
  if (!body) return { ok: true, status: 'skipped', posts: 0, newPosts: null };
  const id = (await queue.enqueue(body)) as number;
  await updateStats((stats) => {
    stats.captured += 1;
  });
  await flush();
  // A flush that was already finishing may have missed this item: run one more.
  if (!delivered.has(id)) await flush();
  const result = delivered.get(id);
  if (!result) return { ok: true, status: 'queued', posts: 0, newPosts: null };
  delivered.delete(id);
  return { ok: true, status: 'delivered', ...result };
}

async function status(): Promise<StatusReply> {
  const [settings, stats, queued] = await Promise.all([getSettings(), getStats(), queue.count()]);
  return { settings, stats, queued };
}

chrome.runtime.onMessage.addListener((request: WorkerRequest, _sender, sendResponse) => {
  const reply = (async () => {
    switch (request.type) {
      case 'capture':
        return handleCapture(request);
      case 'status':
        return status();
      case 'save-settings': {
        const current = await getSettings();
        await chrome.storage.local.set({ settings: { ...current, ...request.settings } });
        void flush();
        return status();
      }
      case 'test-connection':
        return checkHealth(await getSettings());
      case 'flush':
        await flush();
        return status();
    }
  })();
  reply.then(sendResponse, (error: unknown) => sendResponse({ ok: false, error: String(error) }));
  return true; // keep the channel open for the async reply
});

chrome.runtime.onInstalled.addListener(() => {
  void chrome.alarms.create(FLUSH_ALARM, { periodInMinutes: 1 });
  void refreshBadge();
});

chrome.runtime.onStartup.addListener(() => {
  void chrome.alarms.create(FLUSH_ALARM, { periodInMinutes: 1 });
  void flush();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === FLUSH_ALARM) void flush();
});
