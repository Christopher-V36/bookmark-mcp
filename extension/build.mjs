// Bundles the extension into dist/ (load that folder in chrome://extensions).
import { cp, mkdir, rm } from 'node:fs/promises';
import * as esbuild from 'esbuild';

const watch = process.argv.includes('--watch');
const common = { bundle: true, target: 'chrome111', logLevel: 'info', legalComments: 'none' };

await rm('dist', { recursive: true, force: true });
await mkdir('dist', { recursive: true });
await cp('static', 'dist', { recursive: true });

const builds = [
  // Content scripts cannot be ES modules: bundle them as plain scripts.
  { entryPoints: { hook: 'src/main-world/hook.ts' }, format: 'iife' },
  { entryPoints: { relay: 'src/content/relay.ts' }, format: 'iife' },
  { entryPoints: { background: 'src/background/worker.ts' }, format: 'esm' },
  { entryPoints: { popup: 'src/popup/popup.ts' }, format: 'esm' },
].map((options) => ({ ...common, ...options, outdir: 'dist' }));

if (watch) {
  for (const options of builds) await (await esbuild.context(options)).watch();
} else {
  await Promise.all(builds.map((options) => esbuild.build(options)));
}
