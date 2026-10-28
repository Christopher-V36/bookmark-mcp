/**
 * Content script (isolated world). Receives captures from the page hook and hands
 * them to the background worker, and runs the "Load all" / "Load new" auto-scroll for the
 * popup.
 */

import {
  PAGE_MESSAGE_TAG,
  type AutoScrollStatus,
  type CaptureMessage,
  type CaptureReply,
  type PageCapture,
  type TabRequest,
} from '../shared/messages';
import { AutoScroller } from './autoscroll';

let captures = 0;
let knownPages = 0;
let undelivered = 0;
let newPosts = 0;

function countReply(reply: CaptureReply | undefined): void {
  if (reply?.status === 'skipped') return;
  if (reply?.status !== 'delivered') {
    undelivered += 1;
  } else if (reply.newPosts !== null) {
    newPosts += reply.newPosts;
    if (reply.posts > 0 && reply.newPosts === 0) knownPages += 1;
  }
}

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
  chrome.runtime
    .sendMessage(message)
    .then((reply: CaptureReply | undefined) => countReply(reply))
    .catch(() => countReply(undefined));
});

const scroller = new AutoScroller({
  scrollToBottom: () => window.scrollTo(0, document.documentElement.scrollHeight),
  pageHeight: () => document.documentElement.scrollHeight,
  captures: () => captures,
  knownPages: () => knownPages,
  undelivered: () => undelivered,
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  random: Math.random,
});

function status(): AutoScrollStatus {
  return {
    running: scroller.running,
    mode: scroller.mode,
    rounds: scroller.rounds,
    captures,
    newPosts,
    reason: scroller.reason,
  };
}

chrome.runtime.onMessage.addListener((request: TabRequest, _sender, sendResponse) => {
  if (request.type === 'autoscroll-start' && !scroller.running) {
    void scroller.run(request.mode ?? 'all');
  } else if (request.type === 'autoscroll-stop') {
    scroller.stop();
  }
  sendResponse(status());
  return false;
});
