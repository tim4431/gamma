// README "Search" demo, reading a paper: Ctrl+F finds a word in this PDF (the
// compact find bar counts the matches and Enter steps through them, the
// reader following), then Ctrl+P finds a label by name and opens its papers.
//
// Prepared by `run-case.mjs search` (topic labels on the curated papers).
// Writes retina frames to frames/ and marks to search_marks.json (capture seconds).
import { VIEW, RETINA, launchRetina, startCapture, configureContext, addCursor, pointer, readSession, BASE, CURATED } from './runtime.mjs';
import fs from 'fs';

const SCRATCH = process.cwd();
const SESSION = readSession(SCRATCH);
const QEC = process.env.QEC_ID || CURATED.qec;
const { width: VW, height: VH } = VIEW;
const WORD = 'rydberg', LABEL = 'quantum computing';
const beat = (ms) => page.waitForTimeout(ms);

const browser = await launchRetina();
const ctx = await browser.newContext(RETINA);
await configureContext(ctx);
await ctx.addCookies([{ name: 'session', value: SESSION, url: BASE }]);
// The paper view's default: the compact find bar, only this PDF's matches.
await ctx.addInitScript(() => { try { localStorage.setItem('gamma-search-details', '0'); } catch {} });
await addCursor(ctx);
const page = await ctx.newPage();
const { glide, glideTo } = pointer(page, VW * 0.62, VH * 0.55);
let capture;
try {
  // Recents come from real visits; the shot starts on the atom-arrays paper.
  for (const id of [QEC, CURATED.atoms]) {
    await page.goto(`${BASE}/?page=${id}`, { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-page="1"] .textLayer span', { timeout: 60000 });
    const closeChat = page.getByRole('button', { name: 'Close Chat', exact: true });
    if (await closeChat.isVisible()) await closeChat.click();
    await page.locator('.pdfViewer').evaluate(e => e.scrollTo(0, 0));
    await beat(1500);
  }
  await page.mouse.move(VW * 0.62, VH * 0.55);
  capture = await startCapture(page, SCRATCH + '/frames');
  const mark = capture.clock;
  const marks = {}, framing = {};
  await beat(900);

  // 1. Ctrl+F: this PDF only; Enter steps from match to match ---------------
  marks.m0 = mark();
  await page.keyboard.press('Control+f');
  const find = page.locator('.searchPopover .searchInput');
  await find.waitFor();
  await beat(700);
  await page.keyboard.type(WORD, { delay: 90 });
  const count = page.locator('.searchPopover .searchFindCount');
  await count.waitFor({ timeout: 15000 });
  await page.waitForFunction(() => [...document.querySelectorAll('.pdfFindMark')].some(m => { const r = m.getBoundingClientRect(); return r.top > 60 && r.bottom < 880; }), null, { timeout: 15000 });
  framing.find = await page.locator('.searchPopover').boundingBox();
  framing.match = await page.locator('.pdfFindMark').evaluateAll(ms => {
    const r = ms.map(m => m.getBoundingClientRect()).find(r => r.top > 60 && r.bottom < 880);
    return { x: r.x, y: r.y, width: r.width, height: r.height };
  });
  const first = await count.innerText();
  if (!/^1\/\d+$/.test(first) || Number(first.split('/')[1]) < 3) throw new Error(`Expected several matches in this PDF, got ${first}`);
  await beat(1400);
  marks.next = mark();
  for (const n of [2, 3]) {
    await page.keyboard.press('Enter');
    await page.waitForFunction(n => document.querySelector('.searchPopover .searchFindCount')?.textContent.startsWith(`${n}/`), n);
    await beat(1300);
  }
  await page.keyboard.press('Escape');
  await beat(700);

  // 2. Ctrl+P: a label by name, and the papers under it ---------------------
  marks.palette = mark();
  await page.keyboard.press('Control+p');
  const dialog = page.getByRole('dialog', { name: 'Open a page' });
  await dialog.getByRole('textbox', { name: 'Search pages by title or label' }).waitFor();
  await beat(900);
  await page.keyboard.type('quantum comp', { delay: 90 });
  const row = dialog.locator('[role="option"][data-kind="label"]', { hasText: LABEL });
  await row.waitFor();
  framing.palette = await dialog.boundingBox();
  await beat(1100);
  await glideTo(row, 0.3, 0.5, 26);
  await beat(500);
  marks.label = mark();
  await row.click();
  const cards = page.locator('.fileRow');   // the label's list; the recents above are cards
  await page.waitForFunction(() => new URLSearchParams(location.search).has('label'), null, { timeout: 15000 });   // ?label=<the label's id>
  await cards.first().waitFor({ timeout: 15000 });
  const shown = await cards.count();
  if (shown !== 2) throw new Error(`The label view shows ${shown} pages, expected the two quantum papers`);
  await glide(VW * 0.7, VH * 0.6, 24);
  await beat(2600);
  marks.end = mark();
  const frames = await capture.stop(); capture = null;
  await page.screenshot({ path: SCRATCH + '/search-final.png' });
  fs.writeFileSync(SCRATCH + '/search_marks.json', JSON.stringify({ frames, ...marks, framing }));
  console.log('SCRIPT: frames', frames, JSON.stringify(marks));
} catch (error) {
  await page.screenshot({ path: SCRATCH + '/search-error.png' }).catch(() => {});
  throw error;
} finally {
  if (capture) await capture.stop().catch(() => {});
  await ctx.close();
  await browser.close();
}
