/** Captures waiting for the local app, kept in IndexedDB so they survive restarts. */

import type { IngestBody } from './ingest';

const DB_NAME = 'bookmark-mcp';
const STORE = 'queue';

export interface QueuedItem {
  id: number;
  body: IngestBody;
  attempts: number;
}

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function run<T>(
  mode: IDBTransactionMode,
  action: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await open();
  try {
    return await new Promise<T>((resolve, reject) => {
      const transaction = db.transaction(STORE, mode);
      const request = action(transaction.objectStore(STORE));
      transaction.oncomplete = () => resolve(request.result);
      transaction.onerror = () => reject(transaction.error);
    });
  } finally {
    db.close();
  }
}

export function enqueue(body: IngestBody, attempts = 0): Promise<IDBValidKey> {
  return run('readwrite', (store) => store.add({ body, attempts }));
}

export function peek(limit: number): Promise<QueuedItem[]> {
  return run('readonly', (store) => store.getAll(undefined, limit) as IDBRequest<QueuedItem[]>);
}

export function remove(id: number): Promise<undefined> {
  return run('readwrite', (store) => store.delete(id));
}

export function update(item: QueuedItem): Promise<IDBValidKey> {
  return run('readwrite', (store) => store.put(item));
}

export function count(): Promise<number> {
  return run('readonly', (store) => store.count());
}
