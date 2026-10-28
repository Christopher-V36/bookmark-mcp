/**
 * "Load all" / "Load new": scrolls the bookmarks / likes page like a person would, so the
 * web app loads the next pages by itself. Waits 1.5-3 s between scrolls and stops when
 * nothing new has loaded for a few rounds. "Load new" also stops as soon as a page arrives
 * whose posts were all saved already: X lists the newest first, so the rest is known.
 */

import type { ScrollMode, ScrollStopReason } from '../shared/messages';

export interface ScrollDeps {
  scrollToBottom(): void;
  pageHeight(): number;
  captures(): number;
  /** Pages with posts that the app said were all saved already (on this tab). */
  knownPages(): number;
  /** Captures the app could not be asked about (it is not running). */
  undelivered(): number;
  sleep(ms: number): Promise<void>;
  random(): number;
}

export const IDLE_ROUNDS_TO_STOP = 5;
export const MAX_ROUNDS = 3000;

export class AutoScroller {
  running = false;
  mode: ScrollMode = 'all';
  rounds = 0;
  reason: ScrollStopReason | null = null;
  private stopRequested = false;

  constructor(private readonly deps: ScrollDeps) {}

  /** True once the last run reached the end of what it was asked to load. */
  get finished(): boolean {
    return this.reason !== null && this.reason !== 'stopped';
  }

  async run(mode: ScrollMode = 'all'): Promise<void> {
    if (this.running) return;
    this.running = true;
    this.mode = mode;
    this.reason = null;
    this.stopRequested = false;
    this.rounds = 0;
    let idle = 0;
    let lastHeight = this.deps.pageHeight();
    let lastCaptures = this.deps.captures();
    const undeliveredBefore = this.deps.undelivered();
    try {
      while (this.rounds < MAX_ROUNDS) {
        if (this.stopRequested) {
          this.reason = 'stopped';
          break;
        }
        if (idle >= IDLE_ROUNDS_TO_STOP) {
          this.reason = 'end';
          break;
        }
        if (mode === 'new' && this.deps.knownPages() > 0) {
          this.reason = 'caught-up';
          break;
        }
        if (mode === 'new' && this.deps.undelivered() > undeliveredBefore) {
          this.reason = 'offline'; // can't tell new from old without the app
          break;
        }
        this.deps.scrollToBottom();
        await this.deps.sleep(1500 + Math.floor(this.deps.random() * 1500));
        this.rounds += 1;
        const height = this.deps.pageHeight();
        const captures = this.deps.captures();
        idle = height === lastHeight && captures === lastCaptures ? idle + 1 : 0;
        lastHeight = height;
        lastCaptures = captures;
      }
      this.reason ??= 'end';
    } finally {
      this.running = false;
    }
  }

  stop(): void {
    this.stopRequested = true;
  }
}
