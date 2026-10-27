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

export type TabRequest =
  | { type: 'autoscroll-start' }
  | { type: 'autoscroll-stop' }
  | { type: 'autoscroll-status' };

export interface AutoScrollStatus {
  running: boolean;
  rounds: number;
  captures: number;
  finished: boolean;
}
