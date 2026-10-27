/**
 * Content script (isolated world). Receives captures from the page hook and hands
 * them to the background worker, and runs the "Load all" auto-scroll for the popup.
 */

import {
  PAGE_MESSAGE_TAG,
  type AutoScrollStatus,
  type CaptureMessage,
  type PageCapture,
  type TabRequest,
} from '../shared/messages';
import { AutoScroller } from './autoscroll';

let captures = 0;

function isPageCapture(data: unknown): data is PageCapture {
  if (typeof data !== 'object' || data === null) return false;
  const value = data as Record<string, unknown>;
  return (
    value[PAGE_MESSAGE_TAG] === true &&
    value.kind === 'capture' &&
    typeof value.operation === 'string' &&
    typeof value.body === 'string'
  );
}

window.addEventListener('message', (event: MessageEvent) => {
  if (event.source !== window || event.origin !== location.origin) return;
  if (!isPageCapture(event.data)) return;
  const data = event.data;
  captures += 1;
  const message: CaptureMessage = {
    type: 'capture',
    platform: data.platform,
    operation: data.operation,
    url: data.url,
    pageUrl: data.pageUrl,
    capturedAt: data.capturedAt,
    body: data.body,
  };
  chrome.runtime.sendMessage(message).catch(() => undefined);
});

const scroller = new AutoScroller({
  scrollToBottom: () => window.scrollTo(0, document.documentElement.scrollHeight),
  pageHeight: () => document.documentElement.scrollHeight,
  captures: () => captures,
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  random: Math.random,
});

function status(): AutoScrollStatus {
  return {
    running: scroller.running,
    rounds: scroller.rounds,
    captures,
    finished: scroller.finished,
  };
}

chrome.runtime.onMessage.addListener((request: TabRequest, _sender, sendResponse) => {
  if (request.type === 'autoscroll-start') {
    void scroller.run();
  } else if (request.type === 'autoscroll-stop') {
    scroller.stop();
  }
  sendResponse(status());
  return false;
});
