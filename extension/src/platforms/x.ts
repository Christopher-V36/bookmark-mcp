/**
 * Which X web-app GraphQL responses we copy. The page already requests these while
 * you browse your bookmarks, bookmark folders and likes; we only read the replies.
 */

export const X_PLATFORM = 'x';

export interface OperationInfo {
  /** GraphQL operation name as X spells it. */
  name: string;
  /** Short label shown in the popup. */
  label: string;
}

export const X_OPERATIONS: OperationInfo[] = [
  { name: 'Bookmarks', label: 'Bookmarks' },
  { name: 'BookmarkFolderTimeline', label: 'Bookmark folders (posts)' },
  { name: 'bookmarkFoldersSlice', label: 'Bookmark folders (names)' },
  { name: 'Likes', label: 'Likes' },
];

const BY_LOWER = new Map(X_OPERATIONS.map((op) => [op.name.toLowerCase(), op]));

const GRAPHQL_PATH = /\/i\/api\/graphql\/[^/]+\/([A-Za-z0-9_]+)$/;
const X_HOSTS = new Set(['x.com', 'twitter.com', 'api.x.com', 'api.twitter.com']);

export interface MatchedOperation {
  platform: string;
  operation: string;
}

/** Return the operation when `url` is an X GraphQL call we capture, else null. */
export function matchOperation(url: string): MatchedOperation | null {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  const host = parsed.hostname.replace(/^www\./, '');
  if (!X_HOSTS.has(host)) return null;
  const match = GRAPHQL_PATH.exec(parsed.pathname);
  if (!match || !match[1]) return null;
  const op = BY_LOWER.get(match[1].toLowerCase());
  return op ? { platform: X_PLATFORM, operation: op.name } : null;
}

export function defaultEnabled(): Record<string, boolean> {
  return Object.fromEntries(X_OPERATIONS.map((op) => [op.name.toLowerCase(), true]));
}
