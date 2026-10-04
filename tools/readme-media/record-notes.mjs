// README "Take notes" demo: a bare note page typed into, Obsidian-style live
// preview — markdown marks, a [[ref]] chip, then a display equation typed
// char by char ($ auto-pairing, \command autocomplete, Tab through {} args,
// live math preview), then /note turns the next block into a sheet of paper
// and a stylus sketches the result on it, right among the notes.
//
// Prepared by `run-case.mjs notes` (an empty page whose id arrives as PAGE_ID).
// Writes retina frames to frames/ and the edit marks to notes_marks.json
// (capture seconds; m0 = the first click).
import { RETINA, launchRetina, startCapture, configureContext, addCursor, pointer, readSession, BASE } from './runtime.mjs';
import fs from 'fs';

const SCRATCH = process.cwd();
const SESSION = readSession(SCRATCH);
const PAGE_ID = process.env.PAGE_ID || 'QDz3vbdoRlFJ';
const VW = 1440, VH = 900;
// This shot's pauses are written a little long; the factor keeps the edit brisk
// without touching the typing delays below.
const beat = (ms) => page.waitForTimeout(ms * 0.7);

// typing: prose 28 ms/key, math a touch slower so the reader can follow
const PROSE = 28, MATH = 38;
const T = (text, delay = PROSE) => page.keyboard.type(text, { delay });
const K = (key) => page.keyboard.press(key);
const value = () => page.evaluate(() => document.querySelector('.cm-content')?.textContent ?? null);

// Tab / Shift+Tab re-parent the block, which remounts its row and closes the
// editor (headless quirk). Re-open it in place with a synthetic mousedown on
// the still-focused row — no pointer movement, the caret simply comes back.
async function reopenFocused() {
  await page.waitForSelector('.blockRow.focused');
  await page.evaluate(() => {
    const row = document.querySelector('.blockRow.focused');
    const body = row.querySelector('.blockBody') || row;
    const r = body.getBoundingClientRect();
    row.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, clientX: r.left + 40, clientY: r.top + r.height / 2 }));
  });
  await page.waitForSelector('.blockEditorCm .cm-content');
}

const browser = await launchRetina();
const ctx = await browser.newContext(RETINA);
await configureContext(ctx);
await ctx.addCookies([{ name: 'session', value: SESSION, url: BASE }]);

// The note text is the whole story: record at a 130% interface size
// (Settings → Appearance). CSS zoom on <html> would misplace the editor's
// popups in current Chromium.
await ctx.addInitScript(() => {
  try {
    localStorage.setItem('gamma-ui-scale', '1.3');
    // Enter = new block (Settings → Notes); the default is Shift+Enter.
    localStorage.setItem('gamma-enter-new-note', '1');
  } catch {}
});
await addCursor(ctx);

const page = await ctx.newPage();
const { glide, at } = pointer(page, VW / 2, VH / 2);
let capture;
try {
page.on('console', m => { const t = m.text(); if (t.startsWith('SCRIPT:')) console.log(t); });

// 0. open the empty page; close the chat dock so the notes take the width ---
await page.goto(BASE + '/?page=' + PAGE_ID, { waitUntil: 'networkidle' });
await page.mouse.move(at().x, at().y);
await beat(600);
if (await page.locator('[aria-label="Close Chat"]').count()) {
  await page.click('[aria-label="Close Chat"]');
  await beat(700);
}
await page.waitForSelector('.blockRow');
await beat(800);
capture = await startCapture(page, SCRATCH + '/frames');
const clock = capture.clock;

// 1. click the empty first block ---------------------------------------------
const row = await page.locator('.blockRow').first().boundingBox();
await glide(row.x + 120, row.y + row.height / 2 + 6, 30);
await beat(250);
const m0 = clock();
await page.mouse.click(at().x, at().y);
await page.waitForSelector('.blockEditorCm .cm-content');
await glide(row.x + 120, row.y + 420, 24);   // park the pointer well below the text
await beat(400);

// 2. markdown that renders as the caret leaves each construct ----------------
await T('A **two-level atom** driven on resonance undergoes ==Rabi oscillations==, cf. [[quantum processor');
await page.waitForSelector('.refPopup .refPopupItem', { timeout: 5000 });
await beat(900);
await K('Enter');                                  // [[ref]] chip
await beat(250);
await T('.');
console.log('SCRIPT: block 1 =', await value());
await beat(700);

// 3. new block, nested, the equation -----------------------------------------
await K('Enter');
await beat(450);
await K('Tab');
await beat(350);
await reopenFocused();
await beat(500);
await T('$', 220); await beat(350);                // $|$
await T('$', 220); await beat(500);                // $$|$$
console.log('SCRIPT: after $$ =', await value());
await T('H = \\fr', MATH);
await page.waitForSelector('.latexAcPopup', { timeout: 4000 });
await beat(1000);                                  // let the popup be seen
await K('Tab');                                    // accept → \frac{|}{}
await beat(500);
await T('\\hbar\\Omega', MATH);
await beat(500);                                   // popup: Ω \Omega
await T(' ', MATH);                                // a space dismisses it
await beat(300);
await K('Tab');                                    // hop to the 2nd {}
await beat(350);
await T('2', MATH);
await beat(350);
await K('Tab');                                    // out past the closer
await beat(400);
await T(' \\left(', MATH);                         // ( auto-pairs → \left(|)
await beat(500);
await T('|e\\rangle\\langle g| + |g\\rangle\\langle e|', MATH);
await beat(900);                                   // live preview moment
await K('Tab');                                    // hop over the auto-paired \right)
await beat(300);
await T(' - \\hbar\\Delta\\,|e\\rangle\\langle e|', MATH);
console.log('SCRIPT: equation =', await value());
await beat(1200);

// 4. leave the equation: /note makes the next block a sheet of paper -------
await K('End');                                    // past the closing $$
await beat(300);
await K('Enter');
await beat(400);
await K('Shift+Tab');
await beat(350);
await reopenFocused();
await beat(500);
await T('/note', 70);
await page.waitForSelector('.slashMenu .slashMenuItem.selected');
const pick = await page.locator('.slashMenu .slashMenuItem.selected .slashMenuLabel').innerText();
if (pick !== 'Handwritten note') throw new Error(`/note picked ${pick}`);
await beat(700);
const sheetAt = clock();
await K('Enter');
const sheet = page.locator('.noteSheet .nbSheet').first();
await sheet.waitFor({ timeout: 5000 });
await beat(900);

// 5. write on it with a stylus: a pen draws on a sheet with the tools closed --
let paper = await sheet.boundingBox();
const room = 250;                                  // drawing height needed on screen
if (paper.y + room > VH - 20) {
  await glide(paper.x + paper.width * 0.8, Math.min(VH - 60, paper.y + 60), 24);
  const need = paper.y + room - (VH - 20);
  for (let i = 0; i < 12; i++) { await page.mouse.wheel(0, need / 12); await page.waitForTimeout(28); }
  await beat(500);
  paper = await sheet.boundingBox();
}
const cdp = await ctx.newCDPSession(page);
const pen = (type, [x, y], force = 0) => cdp.send('Input.dispatchMouseEvent',
  { type, x, y, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount: 1, pointerType: 'pen', force });
async function write(points, ms) {
  await glide(...points[0], 20);
  await pen('mousePressed', points[0], 0.45);
  const t0 = Date.now(), n = Math.ceil(ms / 16);
  for (let i = 1; i <= n; i++) {
    const u = i / n, f = u * (points.length - 1), j = Math.min(points.length - 2, Math.floor(f)), r = f - j;
    const p = [0, 1].map(k => points[j][k] + (points[j + 1][k] - points[j][k]) * r);
    await pen('mouseMoved', p, 0.45 + 0.2 * Math.sin(u * Math.PI));
    const wait = t0 + ms * i / n - Date.now();
    if (wait > 0) await page.waitForTimeout(wait);
  }
  await pen('mouseReleased', points.at(-1));
  await page.mouse.move(...points.at(-1));
  await beat(200);
}
// A hand sketch of the equation's result: P_e rising and falling twice.
const x0 = paper.x + 70, y0 = paper.y + 36, w = 380, h = 150;
const curve = Array.from({ length: 49 }, (_, i) => {
  const t = i / 48 * 4 * Math.PI;
  return [x0 + 12 + t / (4 * Math.PI) * w, y0 + h - Math.sin(t / 2) ** 2 * (h - 22) + Math.sin(i * 1.7) * 1.2];
});
const peak = curve[12];
const ring = Array.from({ length: 25 }, (_, i) => {
  const a = -Math.PI / 2 + i / 24 * 2.15 * Math.PI;
  return [peak[0] + Math.cos(a) * 26, peak[1] + 4 + Math.sin(a) * 18];
});
// The formula itself: KaTeX's display wrappers are full-width blocks.
const equation = await page.locator('.katex-display .base').evaluateAll(els => {
  const boxes = els.map(e => e.getBoundingClientRect());
  const x = Math.min(...boxes.map(b => b.x)), y = Math.min(...boxes.map(b => b.y));
  return { x, y, width: Math.max(...boxes.map(b => b.right)) - x, height: Math.max(...boxes.map(b => b.bottom)) - y };
});
const sketch = { x: x0 - 10, y: y0 - 16, width: w + 50, height: h + 30 };
const drawAt = clock();
await write([[x0, y0 - 6], [x0 + 1, y0 + h * 0.6], [x0, y0 + h], [x0 + w * 0.5, y0 + h + 1], [x0 + w + 30, y0 + h]], 900);
await write(curve, 1500);
await write(ring, 700);
await page.waitForFunction(() => document.querySelectorAll('.noteSheet .inkLayer path').length === 3);
await glide(Math.min(VW - 120, x0 + w + 260), y0 + 40, 30);
await beat(2400);
const m1 = clock();
const frames = await capture.stop(); capture = null;
// Where the note ended up, for the render's camera.
const content = await page.locator('.blockRow').evaluateAll(rows => {
  const boxes = rows.map(r => r.getBoundingClientRect()).filter(b => b.height);
  const x = Math.min(...boxes.map(b => b.x)), y = Math.min(...boxes.map(b => b.y));
  return { x, y, width: Math.max(...boxes.map(b => b.right)) - x, height: Math.max(...boxes.map(b => b.bottom)) - y };
});
await page.screenshot({ path: SCRATCH + '/notes-final.png' });
await page.reload({ waitUntil: 'networkidle' });
await page.waitForFunction(() => document.querySelectorAll('.noteSheet .inkLayer path').length === 3, null, { timeout: 15000 });
if (!(await value() ?? await page.locator('.blockRow').first().innerText()).includes('two-level atom')) throw new Error('The note text did not persist');
await page.screenshot({ path: SCRATCH + '/notes-reloaded.png' });
fs.writeFileSync(SCRATCH + '/notes-verified.json', JSON.stringify({ sheet: true, strokes: 3, persisted: true }, null, 2));

fs.writeFileSync(SCRATCH + '/notes_marks.json', JSON.stringify({ frames, m0, sheetAt, drawAt, m1, framing: { first: row, content, sheet: paper, equation, sketch } }));
console.log('SCRIPT: frames saved', frames, 'm0', m0.toFixed(2), 'm1', m1.toFixed(2));
} catch (error) {
  await page.screenshot({ path: SCRATCH + '/notes-error.png' });
  throw error;
} finally {
  if (capture) await capture.stop().catch(() => {});
  if (ctx) await ctx.close();
  await browser.close();
}
