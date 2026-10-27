/**
 * "Load all": scrolls the bookmarks / likes page like a person would, so the web app
 * loads the next pages by itself. Waits 1.5-3 s between scrolls and stops when
 * nothing new has loaded for a few rounds.
 */

export interface ScrollDeps {
  scrollToBottom(): void;
  pageHeight(): number;
  captures(): number;
  sleep(ms: number): Promise<void>;
  random(): number;
}

export const IDLE_ROUNDS_TO_STOP = 5;
export const MAX_ROUNDS = 3000;

export class AutoScroller {
  running = false;
  finished = false;
  rounds = 0;
  private stopRequested = false;

  constructor(private readonly deps: ScrollDeps) {}

  async run(): Promise<void> {
    if (this.running) return;
    this.running = true;
    this.finished = false;
    this.stopRequested = false;
    this.rounds = 0;
    let idle = 0;
    let lastHeight = this.deps.pageHeight();
    let lastCaptures = this.deps.captures();
    try {
      while (!this.stopRequested && idle < IDLE_ROUNDS_TO_STOP && this.rounds < MAX_ROUNDS) {
        this.deps.scrollToBottom();
        await this.deps.sleep(1500 + Math.floor(this.deps.random() * 1500));
        this.rounds += 1;
        const height = this.deps.pageHeight();
        const captures = this.deps.captures();
        idle = height === lastHeight && captures === lastCaptures ? idle + 1 : 0;
        lastHeight = height;
        lastCaptures = captures;
      }
      this.finished = !this.stopRequested;
    } finally {
      this.running = false;
    }
  }

  stop(): void {
    this.stopRequested = true;
  }
}
