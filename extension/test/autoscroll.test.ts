import { AutoScroller, IDLE_ROUNDS_TO_STOP } from '../src/content/autoscroll';

/**
 * A bookmarks page with `pagesAvailable` pages to load. The first `newPages` hold posts
 * you haven't saved yet; the app answers for each page unless `appRunning` is false.
 */
function fakePage(pagesAvailable: number, { newPages = pagesAvailable, appRunning = true } = {}) {
  let height = 1000;
  let captures = 0;
  let knownPages = 0;
  let undelivered = 0;
  const waits: number[] = [];
  return {
    waits,
    get captures() {
      return captures;
    },
    deps: {
      scrollToBottom() {
        if (captures < pagesAvailable) {
          captures += 1;
          height += 800;
          if (!appRunning) undelivered += 1;
          else if (captures > newPages) knownPages += 1;
        }
      },
      pageHeight: () => height,
      captures: () => captures,
      knownPages: () => knownPages,
      undelivered: () => undelivered,
      sleep: async (ms: number) => {
        waits.push(ms);
      },
      random: () => 0.5,
    },
  };
}

describe('AutoScroller', () => {
  it('keeps scrolling while new pages load, then stops at the end', async () => {
    const page = fakePage(7);
    const scroller = new AutoScroller(page.deps);
    await scroller.run();
    expect(page.captures).toBe(7);
    expect(scroller.rounds).toBe(7 + IDLE_ROUNDS_TO_STOP);
    expect(scroller.reason).toBe('end');
    expect(scroller.finished).toBe(true);
    expect(scroller.running).toBe(false);
  });

  it('waits like a person between scrolls (1.5-3 s)', async () => {
    const page = fakePage(2);
    await new AutoScroller(page.deps).run();
    expect(page.waits.every((ms) => ms >= 1500 && ms <= 3000)).toBe(true);
  });

  it('can be stopped', async () => {
    const page = fakePage(1000);
    const scroller = new AutoScroller({
      ...page.deps,
      sleep: async () => {
        if (scroller.rounds >= 2) scroller.stop();
      },
    });
    await scroller.run();
    expect(scroller.rounds).toBe(3);
    expect(scroller.reason).toBe('stopped');
    expect(scroller.finished).toBe(false);
  });

  it('"Load new" stops at the first page of posts already saved', async () => {
    const page = fakePage(40, { newPages: 3 });
    const scroller = new AutoScroller(page.deps);
    await scroller.run('new');
    expect(page.captures).toBe(4); // 3 new pages + the first known one
    expect(scroller.reason).toBe('caught-up');
    expect(scroller.finished).toBe(true);
  });

  it('"Load new" does not scroll when the top of the page is already known', async () => {
    const page = fakePage(40, { newPages: 0 });
    page.deps.scrollToBottom(); // the first page X loads when you open it
    const scroller = new AutoScroller(page.deps);
    await scroller.run('new');
    expect(scroller.rounds).toBe(0);
    expect(scroller.reason).toBe('caught-up');
  });

  it('"Load new" stops when the app cannot say what is new', async () => {
    const page = fakePage(40, { appRunning: false });
    const scroller = new AutoScroller(page.deps);
    await scroller.run('new');
    expect(page.captures).toBe(1);
    expect(scroller.reason).toBe('offline');
    expect(scroller.finished).toBe(true);
  });

  it('"Load all" ignores known pages and keeps going to the end', async () => {
    const page = fakePage(6, { newPages: 1 });
    const scroller = new AutoScroller(page.deps);
    await scroller.run('all');
    expect(page.captures).toBe(6);
    expect(scroller.reason).toBe('end');
  });
});
