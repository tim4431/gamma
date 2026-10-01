// Draw through Gamma's real UI in an isolated copy of a curated workspace.
import fs from 'node:fs';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { ROOT, VIEW, RETINA, launchRetina, startCapture, addCursor } from './runtime.mjs';
import { Server, Account } from '../../frontend/tests/e2e/harness.mjs';

const scratch = path.resolve(process.env.MEDIA_SCRATCH || path.join(ROOT, 'artifacts/readme-media'));
const archive = process.env.DEMO_EXPORT || path.join(scratch, 'demo.zip');
if (!fs.existsSync(archive)) throw new Error('Set DEMO_EXPORT to an API export of the curated demo workspace. See README.md.');
fs.mkdirSync(scratch, { recursive: true });
const server = new Server();
let browser, context, page, capture;
const { width: W, height: H } = VIEW;
try {
  await server.start();
  server.manage('create-user', 'media-demo', 'isolated-media-only');
  const account = await new Account(server, 'media-demo', 'isolated-media-only').login();
  await account.upload('/api/import-data', fs.readFileSync(archive), 'demo.zip', 'application/zip');
  browser = await launchRetina();
  context = await account.context(browser, RETINA);
  await context.addInitScript(() => localStorage.setItem('gamma-ink-tools', ''));
  await addCursor(context);
  page = await context.newPage();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('response', r => { if (r.url().includes('/api/') && r.status() >= 400) errors.push(`${r.status()} ${new URL(r.url()).pathname}`); });
  const marks = {}, framing = {};
  const pageId = process.env.PAGE_ID || 'fy0-h_BqOHcH';
  await page.goto(`${server.base}/?page=${pageId}&ws=${account.ws}`);
  await page.locator('[data-page="1"] .textLayer span').first().waitFor({ timeout: 60000 });
  const chatClose = page.getByRole('button', { name: 'Close Chat', exact: true });
  if (await chatClose.isVisible()) await chatClose.click();
  let pointer = [980, 820];
  await page.mouse.move(...pointer);
  await page.waitForTimeout(1500);
  capture = await startCapture(page, path.join(scratch, 'frames-ink'));
  const at = capture.clock;
  // Time-paced samples, not Playwright's unpaced `steps`: retain smooth motion
  // even on a fast machine. Drawing follows its path; transit uses smoothstep.
  async function motion(points, duration, ease = false) {
    const t0 = performance.now();
    const n = Math.ceil(duration / 16);
    for (let i = 1; i <= n; i++) {
      let u = i / n;
      if (ease) u = u * u * (3 - 2 * u);
      const p = u * (points.length - 1), j = Math.min(points.length - 2, Math.floor(p)), f = p - j;
      pointer = points[j].map((v, k) => v + (points[j + 1][k] - v) * f);
      await page.mouse.move(...pointer);
      const remaining = t0 + duration * i / n - performance.now();
      if (remaining > 0) await page.waitForTimeout(remaining);
    }
  }
  const glide = (to, ms = 320) => motion([pointer, to], ms, true);
  async function click(locator) {
    const b = await locator.boundingBox();
    if (!b) throw new Error('Demo target is not visible');
    await glide([b.x + b.width / 2, b.y + b.height / 2], 420);
    await page.mouse.down(); await page.waitForTimeout(80); await page.mouse.up();
    await page.waitForTimeout(100);
    return b;
  }
  // The story: highlight the mechanism, note why it matters, circle the claim
  // it enables and draw the link from one to the other.
  const HIGHLIGHT = 'robust quantum information storage';
  const NOTE = 'Robust storage is what lets qubits move.';
  const CLAIM = 'non-local connectivity';
  const phraseBox = (locator, phrase) => locator.evaluate((el, phrase) => {
    const text = el.firstChild, i = text.textContent.indexOf(phrase);
    const r = document.createRange();
    r.setStart(text, i); r.setEnd(text, i + phrase.length);
    const box = r.getBoundingClientRect();
    return { x: box.x, y: box.y, width: box.width, height: box.height };
  }, phrase);
  // Optional first README story: text highlight and a real annotation before ink.
  if (process.argv.includes('--annotate')) {
    marks.start = at();
    const span = page.locator('[data-page="1"] .textLayer span').filter({ hasText: HIGHLIGHT }).first();
    const b = await phraseBox(span, HIGHLIGHT);
    framing.sentence = b;
    await page.waitForTimeout(400);
    // A double-press drag selects whole words, from "robust" to "storage".
    await glide([b.x+10,b.y+b.height/2], 650);
    await page.waitForTimeout(250);
    await page.mouse.down({clickCount:2});
    await motion([pointer, [b.x+b.width-10,b.y+b.height/2]], 700);
    await page.mouse.up();
    const selected = (await page.evaluate(() => window.getSelection()?.toString()))?.trim();
    if (selected !== HIGHLIGHT) throw new Error(`Text drag selected "${selected}"`);
    await page.locator('.plainTip .colorBtn').first().waitFor();
    framing.tip = await page.locator('.plainTip').boundingBox();
    framing.notesHead = await page.getByRole('button', { name: 'Close Notes', exact: true }).boundingBox();
    await page.waitForTimeout(450);
    await click(page.locator('.plainTip .colorBtn').first());
    await page.waitForTimeout(700);
    marks.textHighlight = at();
    // The new annotation opens focused. Clicking into it would make the reader
    // jump to the highlight (a full-page scroll), so type straight away.
    const editor = page.locator('.blockEditorCm .cm-content');
    await editor.waitFor();
    framing.note = await editor.boundingBox();
    if (!await editor.evaluate(el => el.contains(document.activeElement))) throw new Error('The new annotation is not focused');
    await glide([framing.note.x + framing.note.width * 0.7, framing.note.y + 70], 600);
    await page.keyboard.type(NOTE, {delay:42});
    await page.keyboard.press('Escape');
    await page.waitForTimeout(1100);
    marks.annotation = at();
    const moved = await span.evaluate((el, y) => Math.abs(el.getBoundingClientRect().y - y) > 2, b.y);
    if (moved) throw new Error('The reader scrolled while annotating; the camera framing assumes the first view');
  }
  marks.start ??= at();
  framing.toolbar = await click(page.getByRole('button', { name: 'Handwriting tools', exact: true }));
  await page.waitForTimeout(500);
  framing.inkBar = await page.locator('.pdfInkBar').boundingBox();
  const spans = await page.locator('[data-page="1"] .textLayer span').evaluateAll(es => es.map(e => { const b = e.getBoundingClientRect(); return { text: e.textContent, x: b.x, y: b.y, w: b.width, h: b.height }; }).filter(e => e.y > 50 && e.y < 900));
  fs.writeFileSync(path.join(scratch, 'ink-layout.json'), JSON.stringify({ spans, buttons: await page.locator('.pdfInkBar button').evaluateAll(es => es.map(e => e.getAttribute('aria-label'))) }, null, 2));
  if (!process.argv.includes('--inspect')) {
    async function stroke(points, ms) {
      await glide(points[0], 300);
      await page.mouse.down(); await motion(points, ms); await page.mouse.up();
      await page.waitForTimeout(100);
    }
    async function count(n) { await page.waitForFunction(n => document.querySelectorAll('[data-page="1"] .inkLayer path').length === n, n); }
    // Hand-plotted gestures, gently interpolated: uneven spacing, an open seam,
    // and a curved arrow instead of a mathematical ellipse and straight segments.
    function freehand(points) {
      const out = [];
      for (let i = 0; i < points.length - 1; i++) {
        const a = points[Math.max(0, i - 1)], b = points[i], c = points[i + 1], d = points[Math.min(points.length - 1, i + 2)];
        for (let j = 0; j < 8; j++) {
          const t = j / 8;
          out.push(b.map((v, k) => 0.5 * ((2*v) + (-a[k]+c[k])*t + (2*a[k]-5*v+4*c[k]-d[k])*t*t + (-a[k]+3*v-3*c[k]+d[k])*t*t*t)));
        }
      }
      return [...out, points.at(-1)];
    }
    // Hand-plotted from the text layer: an ellipse round the claim with an open
    // seam, and a bowed arrow from the highlighted phrase up to it.
    const claim = await phraseBox(page.locator('[data-page="1"] .textLayer span').filter({ hasText: CLAIM }).first(), CLAIM);
    const source = await phraseBox(page.locator('[data-page="1"] .textLayer span').filter({ hasText: HIGHLIGHT }).first(), HIGHLIGHT);
    framing.claim = claim;
    const cx = claim.x + claim.width / 2, cy = claim.y + claim.height / 2, rx = claim.width / 2 + 14, ry = claim.height / 2 + 9;
    const circle = Array.from({ length: 13 }, (_, i) => {
      const a = -0.35 + i / 12 * 2.15 * Math.PI, wobble = 1 + 0.04 * Math.sin(i * 2.3);
      return [cx + Math.cos(a) * rx * wobble, cy + Math.sin(a) * ry * wobble - 1.5 * Math.cos(a)];
    });
    const tail = [source.x + source.width * 0.3, source.y - 4], tip = [claim.x + claim.width * 0.28, claim.y + claim.height + 11];
    const along = (u, dx) => [tail[0] + (tip[0] - tail[0]) * u + dx, tail[1] + (tip[1] - tail[1]) * u];
    const arrow = [tail, along(0.35, -12), along(0.7, -9), tip];
    const angle = Math.atan2(tip[1] - arrow[2][1], tip[0] - arrow[2][0]);
    const barb = side => [tip[0] - 12 * Math.cos(angle + side * 0.5), tip[1] - 12 * Math.sin(angle + side * 0.5)];
    const ax = arrow.map(p => p[0]), ay = arrow.map(p => p[1]);
    const box = [Math.min(...ax) - 16, Math.min(...ay) - 14, Math.max(...ax) + 16, Math.max(...ay) + 14];
    const gestures = {
      circle,
      arrow,
      head: [barb(1), tip, barb(-1)],
      lasso: [[box[0] + 6, box[3]], [box[0], (box[1] + box[3]) / 2], [box[0] + 10, box[1]], [(box[0] + box[2]) / 2, box[1] - 4],
        [box[2], box[1] + 6], [box[2] + 4, (box[1] + box[3]) / 2], [box[2] - 8, box[3]], [(box[0] + box[2]) / 2, box[3] + 4], [box[0] + 6, box[3]]],
    };
    const xs = Object.values(gestures).flat().map(p => p[0]), ys = Object.values(gestures).flat().map(p => p[1]);
    framing.ink = { x: Math.min(...xs), y: Math.min(...ys), width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys) };
    // The page's saved ink block, once `ready(ink)` holds.
    async function savedInk(ready) {
      for (let i = 0; i < 60; i++) {
        const tree = await account.api(`/api/blocks/${pageId}/subtree`);
        const block = tree.block.children?.find(b => b.properties?.ink_url?.endsWith('.ink'));
        const ink = block && await account.api(block.properties.ink_url);
        if (ink && ready(ink)) return ink;
        await page.waitForTimeout(150);
      }
      throw new Error('The ink block did not reach the expected state');
    }
    await page.waitForTimeout(300);
    await click(page.locator('.pdfInkBar button[aria-label^="Blue pen"]'));
    marks.draw = at();
    await stroke(freehand(gestures.circle), 1050);
    await stroke(freehand(gestures.arrow), 620);
    await stroke(freehand(gestures.head), 270);
    await count(3);
    await page.locator('.blockInkCard').first().waitFor();
    const drawn = await savedInk(ink => ink.strokes.length === 3);
    // Edit the ink: lasso the arrow and make the link red.
    marks.lasso = at();
    await click(page.locator('.pdfInkBar button[aria-label^="Lasso"]'));
    await stroke(freehand(gestures.lasso), 900);
    const menu = page.locator('.inkEditMenu');
    await menu.waitFor();
    await page.waitForTimeout(350);
    marks.recolor = at();
    await click(menu.getByRole('button', { name: 'Color', exact: true }));
    await page.waitForTimeout(250);
    await click(menu.locator('.inkEditOptions button[aria-label="Red"]'));
    await page.waitForTimeout(900);
    await click(page.getByRole('button', { name: 'Hand', exact: true }));
    marks.notes = at();
    framing.inkCard = await page.locator('.blockInkCard').first().boundingBox();
    await glide([1170, 320], 520);
    await page.waitForTimeout(1600);
    marks.end = at();
    const frames = await capture.stop(); capture = null;
    await page.screenshot({ path: path.join(scratch, 'ink-final.png') });
    // Verify that the edit really persisted, then reload off camera.
    const red = ink => ink.strokes.filter(s => s.color === '#dc2626');
    const edited = await savedInk(ink => ink.strokes.length === 3 && red(ink).length === 2);
    if (!red(edited).every(s => drawn.strokes.some(d => d.id === s.id))) throw new Error('The recolored strokes are not the drawn arrow');
    await page.reload();
    await count(3);
    if (process.argv.includes('--annotate')) {
      const tree = await account.api(`/api/blocks/${pageId}/subtree`);
      if (!JSON.stringify(tree).includes(NOTE)) throw new Error('Annotation did not persist');
    }
    fs.writeFileSync(path.join(scratch, 'ink-timeline.json'), JSON.stringify({ frames, width: W, height: H, marks, framing, verified: { strokes: 3, recolored: 2, reload: true, annotation: process.argv.includes('--annotate') } }, null, 2));
    console.log('Recorded ink demo; strokes and the lasso recolor verified after reload.');
  }
  if (errors.length) throw new Error(errors.join('\n'));
  if (process.argv.includes('--inspect')) console.log('Inspected curated paper; see ink-layout.json.');
} catch (error) {
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(scratch, 'ink-failure.png') }).catch(() => {});
  throw error;
} finally {
  if (capture) await capture.stop().catch(() => {});
  if (context) await context.close();
  if (browser) await browser.close();
  await server.stop();
}
