import { X_OPERATIONS } from '../platforms/x';
import type {
  AutoScrollStatus,
  HealthReply,
  StatusReply,
  TabRequest,
  WorkerRequest,
} from '../shared/messages';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

function ask<T>(request: WorkerRequest): Promise<T> {
  return chrome.runtime.sendMessage(request) as Promise<T>;
}

async function activeTab(): Promise<chrome.tabs.Tab | undefined> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function askTab(request: TabRequest): Promise<AutoScrollStatus | null> {
  const tab = await activeTab();
  if (tab?.id === undefined) return null;
  try {
    return (await chrome.tabs.sendMessage(tab.id, request)) as AutoScrollStatus;
  } catch {
    return null; // not an x.com tab, or opened before the extension was installed
  }
}

function setConnection(health: HealthReply): void {
  const badge = $('connection');
  badge.className = health.ok ? 'pill ok' : 'pill bad';
  badge.textContent = health.ok ? `Connected · ${health.posts ?? 0} posts saved` : 'Not connected';
  $('connection-detail').textContent = health.ok ? '' : (health.error ?? '');
}

function renderStatus(reply: StatusReply): void {
  const { settings, stats, queued } = reply;
  ($('server-url') as HTMLInputElement).value = settings.serverUrl;
  ($('token') as HTMLInputElement).value = settings.token;
  $('captured').textContent = String(stats.captured);
  $('sent').textContent = String(stats.sent);
  $('queued').textContent = String(queued);
  $('last-error').textContent = stats.lastError ?? '';
  $('last-error').hidden = !stats.lastError;

  const toggles = $('toggles');
  toggles.replaceChildren(
    ...X_OPERATIONS.map((op) => {
      const label = document.createElement('label');
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = settings.enabled[op.name.toLowerCase()] !== false;
      box.addEventListener('change', async () => {
        renderStatus(
          await ask<StatusReply>({
            type: 'save-settings',
            settings: { enabled: { ...settings.enabled, [op.name.toLowerCase()]: box.checked } },
          }),
        );
      });
      label.append(box, ` ${op.label}`);
      return label;
    }),
  );
}

function renderScroll(status: AutoScrollStatus | null): void {
  const text = $('scroll-status');
  const start = $('scroll-start') as HTMLButtonElement;
  const stop = $('scroll-stop') as HTMLButtonElement;
  if (!status) {
    text.textContent = 'Open x.com/i/bookmarks, a bookmark folder or your Likes, then reload it.';
    start.disabled = true;
    stop.disabled = true;
    return;
  }
  start.disabled = status.running;
  stop.disabled = !status.running;
  if (status.running) {
    text.textContent = `Scrolling… ${status.rounds} rounds, ${status.captures} pages captured`;
  } else if (status.finished) {
    text.textContent = `Done: reached the end (${status.captures} pages captured).`;
  } else {
    text.textContent = `${status.captures} pages captured on this tab.`;
  }
}

async function refresh(): Promise<void> {
  renderStatus(await ask<StatusReply>({ type: 'status' }));
  renderScroll(await askTab({ type: 'autoscroll-status' }));
}

async function main(): Promise<void> {
  $('save').addEventListener('click', async () => {
    renderStatus(
      await ask<StatusReply>({
        type: 'save-settings',
        settings: {
          serverUrl: ($('server-url') as HTMLInputElement).value,
          token: ($('token') as HTMLInputElement).value.trim(),
        },
      }),
    );
    setConnection(await ask<HealthReply>({ type: 'test-connection' }));
  });
  $('retry').addEventListener('click', async () => {
    renderStatus(await ask<StatusReply>({ type: 'flush' }));
    setConnection(await ask<HealthReply>({ type: 'test-connection' }));
  });
  $('scroll-start').addEventListener('click', async () =>
    renderScroll(await askTab({ type: 'autoscroll-start' })),
  );
  $('scroll-stop').addEventListener('click', async () =>
    renderScroll(await askTab({ type: 'autoscroll-stop' })),
  );

  await refresh();
  setConnection(await ask<HealthReply>({ type: 'test-connection' }));
  setInterval(() => void refresh(), 2000);
}

void main();
