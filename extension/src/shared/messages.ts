/** Messages exchanged between the page hook, the content script, the worker and the popup. */

export const PAGE_MESSAGE_TAG = '__bookmarkMcp';

/** Posted by the MAIN-world hook to the content script through window.postMessage. */
export interface PageCapture {
  [PAGE_MESSAGE_TAG]: true;
  kind: 'capture';
  platform: string;
  operation: string;
  url: string;
  pageUrl: string;
  capturedAt: string;
  body: string;
}

export interface CaptureMessage {
  type: 'capture';
  platform: string;
  operation: string;
  url: string;
  pageUrl: string;
  capturedAt: string;
  body: string;
}

/**
 * The worker's answer to a capture: delivered to the app (with how much of it was new),
 * queued because the app is not running, or skipped (capture turned off, not JSON).
 */
export interface CaptureReply {
  ok: true;
  status: 'delivered' | 'queued' | 'skipped';
  posts: number;
  newPosts: number | null;
}

export interface Settings {
  serverUrl: string;
  token: string;
  /** Operation name (lower case) -> capture enabled. */
  enabled: Record<string, boolean>;
}

export interface Stats {
  captured: number;
  sent: number;
  failed: number;
  lastSentAt: string | null;
  lastError: string | null;
  perCollection: Record<string, number>;
}

export interface StatusReply {
  settings: Settings;
  stats: Stats;
  queued: number;
}

export interface HealthReply {
  ok: boolean;
  error?: string;
  posts?: number;
  collections?: { collection: string; count: number }[];
  last_capture?: string | null;
}

export type WorkerRequest =
  | CaptureMessage
  | { type: 'status' }
  | { type: 'save-settings'; settings: Partial<Settings> }
  | { type: 'test-connection' }
  | { type: 'flush' };

/** "all" scrolls to the end; "new" stops at the first page of posts you already had. */
export type ScrollMode = 'all' | 'new';

/** Why the last run stopped: the end of the list, posts already saved, or no app to ask. */
export type ScrollStopReason = 'end' | 'caught-up' | 'offline' | 'stopped';

export type TabRequest =
  | { type: 'autoscroll-start'; mode?: ScrollMode }
  | { type: 'autoscroll-stop' }
  | { type: 'autoscroll-status' };

export interface AutoScrollStatus {
  running: boolean;
  mode: ScrollMode;
  rounds: number;
  captures: number;
  /** Posts this tab added to your collections, as the app reported. */
  newPosts: number;
  reason: ScrollStopReason | null;
}
