// Render frames of a user guide animation at given seconds, to look at a scene
// without a browser window: the SMIL clock is paused and set to each moment.
//
//   node tools/user_guide/snap.mjs annotate 1 3 5 8     # docs/user_guide/assets/annotate.svg
//   node tools/user_guide/snap.mjs annotate              # every second of the loop
//
// PNGs land in artifacts/user-guide/<stem>-<t>s.png (ignored by git).
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium, ROOT } from '../branding/runtime.mjs';

const [stem, ...rest] = process.argv.slice(2);
if (!stem) throw new Error('Usage: node tools/user_guide/snap.mjs <stem> [seconds…]');
const file = path.join(ROOT, 'docs/user_guide/assets', `${stem}.svg`);
const svg = fs.readFileSync(file, 'utf8');
const width = +svg.match(/<svg\b[^>]*\bwidth="(\d+)"/)[1];
const height = +svg.match(/<svg\b[^>]*\bheight="(\d+)"/)[1];
const loop = Math.max(...[...svg.matchAll(/dur="([\d.]+)s"/g)].map(m => +m[1]));
const times = rest.length ? rest.map(Number) : Array.from({ length: Math.ceil(loop) }, (_, i) => i);
const out = path.join(ROOT, 'artifacts/user-guide');
fs.mkdirSync(out, { recursive: true });

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
  await page.goto(pathToFileURL(file).href);
  await page.evaluate(() => { document.fonts.ready; document.documentElement.pauseAnimations(); });
  for (const t of times) {
    await page.evaluate(t => document.documentElement.setCurrentTime(t), t);
    const destination = path.join(out, `${stem}-${t}s.png`);
    await page.screenshot({ path: destination });
    console.log(path.relative(ROOT, destination));
  }
} finally {
  await browser.close();
}
