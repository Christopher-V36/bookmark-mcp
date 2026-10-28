// Builds dist/: the same web app the PC serves, plus the cloud login page.
import { cp, mkdir, rm } from 'node:fs/promises';

const web = new URL('../server/bookmark_mcp/web/', import.meta.url);
await rm('dist', { recursive: true, force: true });
await mkdir('dist', { recursive: true });
await cp(web, 'dist', {
  recursive: true,
  filter: (source) => !source.endsWith('login.html'),
});
await cp('static', 'dist', { recursive: true });
console.log('built dist/');
