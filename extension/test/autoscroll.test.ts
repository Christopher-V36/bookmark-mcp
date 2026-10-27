import { AutoScroller, IDLE_ROUNDS_TO_STOP } from '../src/content/autoscroll';

function fakePage(pagesAvailable: number) {
  let height = 1000;
  let captures = 0;
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
        }
      },
      pageHeight: () => height,
      captures: () => captures,
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
    expect(scroller.finished).toBe(false);
  });
});
