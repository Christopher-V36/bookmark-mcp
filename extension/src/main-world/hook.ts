/**
 * Runs inside x.com's own page (MAIN world) at document_start.
 *
 * Technique from twitter-web-exporter (MIT, (c) 2023 prin): wrap
 * XMLHttpRequest.prototype.open so that, when one of the GraphQL calls we care
 * about finishes, we copy its JSON reply. `fetch` is wrapped too in case X
 * switches. Nothing new is ever requested from X - we only read replies the
 * page already received.
 */

import { matchOperation } from '../platforms/x';
import { PAGE_MESSAGE_TAG, type PageCapture } from '../shared/messages';

declare global {
  interface Window {
    __bookmarkMcpHooked?: boolean;
  }
}

function forward(platform: string, operation: string, url: string, body: string): void {
  const message: PageCapture = {
    [PAGE_MESSAGE_TAG]: true,
    kind: 'capture',
    platform,
    operation,
    url,
    pageUrl: location.href,
    capturedAt: new Date().toISOString(),
    body,
  };
  window.postMessage(message, location.origin);
}

function absolute(url: string | URL): string {
  try {
    return new URL(String(url), location.href).href;
  } catch {
    return String(url);
  }
}

function hookXhr(): void {
  const originalOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (
    this: XMLHttpRequest,
    method: string,
    url: string | URL,
    ...rest: unknown[]
  ) {
    const href = absolute(url);
    const match = matchOperation(href);
    if (match) {
      this.addEventListener('load', () => {
        if (this.status < 200 || this.status >= 300) return;
        let body: string | null = null;
        if (this.responseType === '' || this.responseType === 'text') body = this.responseText;
        else if (this.responseType === 'json') body = JSON.stringify(this.response);
        if (body) forward(match.platform, match.operation, href, body);
      });
    }
    return (originalOpen as (...args: unknown[]) => void).call(this, method, url, ...rest);
  } as typeof XMLHttpRequest.prototype.open;
}

function hookFetch(): void {
  const originalFetch = window.fetch;
  window.fetch = async function (this: unknown, input: RequestInfo | URL, init?: RequestInit) {
    const response = await originalFetch.call(this, input, init);
    try {
      const raw = input instanceof Request ? input.url : String(input);
      const href = absolute(raw);
      const match = matchOperation(href);
      if (match && response.ok) {
        response
          .clone()
          .text()
          .then((body) => forward(match.platform, match.operation, href, body))
          .catch(() => undefined);
      }
    } catch {
      // never break the page
    }
    return response;
  } as typeof window.fetch;
}

if (!window.__bookmarkMcpHooked) {
  window.__bookmarkMcpHooked = true;
  hookXhr();
  hookFetch();
}
