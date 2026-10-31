// README demo "ink-import": handwritten notes come in from Notability and
// the reader keeps writing on them in Gamma.
//
// The note is made by make-notability-note.py (the curated atom-arrays
// paper, CC BY, with pencil and highlighter marks), written as Notability's
// own export writes one, so Gamma's real reader parses it. The story, on an
// isolated server holding the curated export:
//   1. library home → ≡ View → Import… → "Notability notes" → Choose files…
//      (the picker is answered by setFiles and never shows) → "Review
//      Notability import" with the note under "Library after import" (PDF)
//      → Import to library → "Import complete" → Done
//   2. the new page: the paper with the pencil marks on it, their ink card in Notes
//   3. Handwriting tools → Blue pen: a ring round "neutral atom arrays" and a
//      short arrow up to the margin's "how?"; a new ink card takes the
//      strokes; then the hand tool (V), so the pen's hover ring leaves the page
// Checks: the imported page carries the PDF and the note's strokes on the
// right pages; the new strokes are saved in the page's ink and survive a reload.
//
//   node tools/readme-media/record-ink-import.mjs [--inspect]
// --inspect runs the story without the screencast and screenshots each beat
// into the scratch folder.
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { launchRetina, phraseBox } from './runtime.mjs';
import { library, openPage, hand, Take, scratch, sleep, smooth, INSPECT } from './take.mjs';
import { PYTHON } from '../../frontend/tests/e2e/harness.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const NAME = 'ink-import';
// The Interface size: the dialogs and the notes read at the README's width without a close zoom.
const UI_SCALE = 1.25;
const dir = scratch(NAME);
let shot = 0;
// An eased glide along a curve through `via` (a quadratic Bézier), on the
// clock like hand()'s moves: to go round a button instead of across it.
async function arc(page, pointer, via, to, ms) {
  const from = pointer.at, t0 = performance.now(), n = Math.max(1, Math.ceil(ms / 16));
  for (let i = 1; i <= n; i++) {
    const u = smooth(i / n), a = (1 - u) ** 2, b = 2 * u * (1 - u), c = u * u;
    await page.mouse.move(a * from[0] + b * via[0] + c * to[0], a * from[1] + b * via[1] + c * to[1]);
    const rest = t0 + ms * i / n - performance.now();
    if (rest > 0) await sleep(rest);
  }
  await pointer.park(to[0], to[1]);
}
const snap = async (page, what) => { if (INSPECT) await page.screenshot({ path: path.join(dir, `inspect-${String(++shot).padStart(2, '0')}-${what}.png`) }); };

// The note, generated fresh: its file and what it holds.
const notePath = path.join(dir, 'Atom arrays — iPad notes.note');
const note = JSON.parse(execFileSync(PYTHON, [path.join(HERE, 'make-notability-note.py'), '--out', notePath], { encoding: 'utf8' }).trim().split('\n').at(-1));
console.log('note:', JSON.stringify({ strokes: note.strokes, pages: note.pages, words: note.words }));

// The page's ink files by PDF page: {page: strokes}.
async function inkByPage(account, pageId) {
  const blocks = (await account.api(`/api/blocks/${pageId}/subtree`)).block.children || [];
  const out = {};
  for (const b of blocks.filter((b) => b.properties?.ink_url)) {
    const ink = await account.api(b.properties.ink_url);
    if (ink.space?.kind === 'pdf-page') out[ink.space.page] = [...(out[ink.space.page] || []), ...ink.strokes];
  }
  return out;
}
const counts = (ink) => Object.fromEntries(Object.entries(ink).map(([p, s]) => [p, s.length]));
const sameCounts = (got, want) => JSON.stringify(Object.entries(got).sort()) === JSON.stringify(Object.entries(want).map(([p, n]) => [p, n]).sort());

const { server, accounts } = await library({ users: ['demo', 'check'] });
const [account, checker] = accounts;
let browser, page;
const take = new Take(dir, NAME);
try {
  // Off camera, the note once through the import's API on a second account:
  // the strokes Notability's file holds come through on the PDF pages they
  // were drawn on (and the server has read this PDF once before the take).
  const fd = new FormData();
  fd.append('file', new Blob([fs.readFileSync(notePath)], { type: 'application/octet-stream' }), path.basename(notePath));
  fd.append('folder', '');
  await checker.api('/api/import/notability', { method: 'POST', form: fd });
  const trial = (await checker.api('/api/blocks/root/children')).children.find((p) => p.content === note.title);
  const trialInk = trial && counts(await inkByPage(checker, trial.id));
  take.check('trialImport', trial?.properties?.doc_id && sameCounts(trialInk, note.pages), { trialInk, want: note.pages });

  browser = await launchRetina();
  // Automatic paper-metadata lookup is off (Settings → PDFs): the harness server
  // has no lookup, and its "no metadata found" status would cover the paper.
  page = await openPage(browser, account, { url: `/?ws=${account.ws}`, uiScale: UI_SCALE,
    init: () => { try { localStorage.setItem('gamma-meta-auto', '0'); } catch {} } });
  const problems = [];
  // The harness turns metadata lookup off, so the new page's lookup answers 404.
  page.on('response', (r) => { if (r.url().includes('/api/') && r.status() >= 400 && !r.url().includes('/api/metadata/fetch')) problems.push(`${r.status()} ${new URL(r.url()).pathname}`); });
  await page.locator('[data-popover="menu"] > button').waitFor();
  await page.waitForLoadState('networkidle');
  // The library alone: the chat dock's welcome would crowd the home.
  const chatClose = page.getByRole('button', { name: 'Close Chat', exact: true });
  if (await chatClose.isVisible().catch(() => false)) { await chatClose.click(); await chatClose.waitFor({ state: 'detached' }); }
  // The Import dialog once off camera, so its source logos are loaded and
  // decoded before the take (the Zotero logo otherwise fades in on camera).
  await page.locator('[data-popover="menu"] > button').click();
  await page.getByRole('menuitem', { name: 'Import…', exact: true }).click();
  const warm = page.getByRole('dialog', { name: 'Import', exact: true });
  await warm.waitFor();
  await page.waitForFunction(() => [...document.querySelectorAll('[role="dialog"] img')].every((i) => i.complete && i.naturalWidth > 0));
  await sleep(400);
  await page.keyboard.press('Escape');
  await warm.waitFor({ state: 'detached' });
  await page.waitForFunction(() => !document.querySelector('[role="dialog"]'));
  const pointer = hand(page, [1040, 640]);
  await pointer.park(1040, 640);
  await sleep(1400);
  await snap(page, 'home');

  if (!INSPECT) await take.start(page);
  else take.mark('start', 0);
  const clock = () => (INSPECT ? 0 : take.clock());
  // What the viewer sees, marked when it paints.
  if (!INSPECT) await take.watch(() => {
    const dialog = (name) => [...document.querySelectorAll('[role="dialog"]')].find((d) => d.getAttribute('aria-label') === name
      || d.querySelector('h1,h2,h3')?.textContent?.trim() === name);
    return {
      importDialog: !!dialog('Import'),
      review: !!document.querySelector('section[aria-label="Library after import"] .importKind'),
      complete: !!dialog('Import complete'),
      // The import job's small progress card (its Stop button) in place of the review.
      working: [...(dialog('Review Notability import')?.querySelectorAll('button') || [])].some((b) => b.textContent.trim() === 'Stop'),
      inkShown: document.querySelectorAll('[data-page="1"] .inkLayer path').length >= 10 && !!document.querySelector('.blockInkCard'),
      pdfText: document.querySelectorAll('[data-page="1"] .textLayer span').length > 100 && document.querySelectorAll('[data-page="1"] .inkLayer path').length >= 10,
      newCard: document.querySelectorAll('.blockInkCard').length >= 3,
      // Every dialog gone (and its close animation ended) after "Import complete".
      dialogGone: (window.__sawComplete = window.__sawComplete || !!dialog('Import complete')) && !document.querySelector('[role="dialog"]'),
    };
  });
  await sleep(900);

  // 1. Import the note.
  take.mark('menu', clock());
  await pointer.click('[data-popover="menu"] > button', 460);
  const importItem = page.getByRole('menuitem', { name: 'Import…', exact: true });
  await importItem.waitFor();
  await sleep(180);
  await pointer.click(importItem, 420);
  const dialog = page.getByRole('dialog', { name: 'Import', exact: true });
  await dialog.waitFor();
  await sleep(220);
  if (!INSPECT) await take.frame('importDialog', dialog);
  await snap(page, 'import-dialog');
  take.mark('card', clock());
  await pointer.click(dialog.getByRole('button', { name: 'Notability notes', exact: true }), 460);
  // The dialog grows and re-centres; the eye catches up before the pointer moves on.
  await sleep(350);
  await snap(page, 'card');
  const choose = dialog.getByRole('button', { name: 'Choose files…', exact: true });
  // On the button's left part: the review's Cancel opens under its right half,
  // and would sit hovered when the review shows.
  await pointer.to(choose, 430, 0.22, 0.5);
  await sleep(120);
  const chooser = page.waitForEvent('filechooser');
  take.mark('choose', clock());
  await pointer.press();
  await (await chooser).setFiles(notePath);
  take.mark('picked', clock());
  const review = page.getByRole('dialog', { name: 'Review Notability import', exact: true });
  const after = review.getByRole('region', { name: 'Library after import', exact: true });
  await after.getByText(note.title, { exact: true }).waitFor({ timeout: 60000 });
  const kind = (await after.locator('.importKind').allTextContents()).join(',');
  take.check('reviewKind', kind === 'PDF', kind);
  take.mark('reviewReady', clock());
  if (!INSPECT) await take.frame('review', review);
  await snap(page, 'review');
  // Straight on to Import to library, over Cancel rather than across it.
  const importBtn = review.getByRole('button', { name: 'Import to library', exact: true });
  const cancelBox = await review.getByRole('button', { name: 'Cancel', exact: true }).boundingBox();
  const ib = await importBtn.boundingBox();
  const goal = [ib.x + ib.width * 0.45, ib.y + ib.height * 0.5];
  await arc(page, pointer, [(pointer.at[0] + goal[0]) / 2, cancelBox.y - 90], goal, 560);
  await sleep(120);
  take.mark('importPress', clock());
  await pointer.press();
  const report = page.getByRole('dialog', { name: 'Import complete', exact: true });
  await report.waitFor({ timeout: 120000 });
  take.mark('completeReady', clock());
  const summary = await report.locator('.importReviewHeader').innerText();
  take.check('reportSummary', /1 new page/.test(summary), summary);
  if (!INSPECT) await take.frame('report', report);
  await sleep(680);
  await snap(page, 'complete');
  await pointer.click(report.getByRole('button', { name: 'Done', exact: true }), 460);
  take.mark('done', clock());
  // While the dialog closes (cut hard), the pointer goes to the new page's row,
  // so the library shows with the pointer already resting on it.
  const row = page.locator('.fileRow', { hasText: note.title }).first();
  await row.waitFor();
  const rowName = row.getByText(note.title, { exact: true });
  const name = await pointer.to(rowName, 520);
  take.mark('rowArrived', clock());
  await report.waitFor({ state: 'detached' });
  await page.waitForFunction(() => !document.querySelector('[role="dialog"]'));
  await sleep(Math.max(0, 1300 - (clock() - take.marks.done) * 1000));
  take.mark('rowRest', clock());
  await snap(page, 'after-done');

  // The page the import made: the note's PDF and its strokes on the right pages.
  const imported = (await account.api('/api/blocks/root/children')).children.find((p) => p.content === note.title);
  take.check('importedPdf', !!imported?.properties?.doc_id, imported?.properties);
  const importedInk = imported ? counts(await inkByPage(account, imported.id)) : {};
  take.check('importedStrokes', sameCounts(importedInk, note.pages), { importedInk, want: note.pages });

  // 2. Open it from the library.
  // A row opens on a double click (a single click selects it).
  if (!INSPECT) await take.frame('row', row);
  await sleep(380);  // the pointer rests on the row (its hover band) before the double click
  take.mark('openRow', clock());
  await page.mouse.dblclick(name.x + name.width / 2, name.y + name.height / 2);
  // While the page opens (cut), the pointer moves to the paper's left margin
  // beside the Handwriting tools button, so the next beat's reach is short.
  const toolsBtn = page.getByRole('button', { name: 'Handwriting tools', exact: true });
  await toolsBtn.waitFor({ timeout: 60000 });
  const tb = await toolsBtn.boundingBox();
  await pointer.glide([tb.x + tb.width + 120, tb.y + tb.height / 2 + 20], 320);
  take.mark('nearTools', clock());
  await page.locator('[data-page="1"] .textLayer span').first().waitFor({ timeout: 60000 });
  await page.waitForFunction((n) => document.querySelectorAll('[data-page="1"] .inkLayer path').length >= n, note.pages['1'], { timeout: 30000 });
  await page.locator('.blockInkCard').first().waitFor();
  take.mark('pageOpen', clock());
  if (await chatClose.isVisible().catch(() => false)) throw new Error('The chat dock is open on the imported page');
  await sleep(1000);
  await snap(page, 'page');

  // Where things are on screen: the PDF page's box maps the note's PDF points.
  const wrap = await page.locator('[data-page="1"]').first().boundingBox();
  const k = wrap.width / 595.276;
  const how = note.words.how;
  const howBox = { x: wrap.x + how[0] * k, y: wrap.y + how[1] * k, width: (how[2] - how[0]) * k, height: (how[3] - how[1]) * k };
  const span = page.locator('[data-page="1"] .textLayer span').filter({ hasText: 'neutral atom arrays' }).first();
  const claim = await phraseBox(span, 'neutral atom arrays');
  const cards = page.locator('.blockInkCard');
  const cardCount = await cards.count();
  // The imported marks on the page, for the close-up.
  const oldInk = await page.evaluate(() => {
    const rs = [...document.querySelectorAll('[data-page="1"] .inkLayer path')].map((p) => p.getBoundingClientRect()).filter((r) => r.width || r.height);
    const x = Math.min(...rs.map((r) => r.left)), y = Math.min(...rs.map((r) => r.top));
    return { x, y, width: Math.max(...rs.map((r) => r.right)) - x, height: Math.max(...rs.map((r) => r.bottom)) - y };
  });
  if (!INSPECT) {
    await take.frame('oldInk', oldInk);
    await take.frame('how', howBox);
    await take.frame('claim', claim);
    await take.frame('inkCard', cards.first());
    await take.frame('notesTitle', page.locator('.pageTitleRow').first());
    await take.frame('pdfPage1', wrap);
    // The paper's title (its text spans), which the close-up stays below.
    await take.frame('title', await page.evaluate(() => {
      const rs = [...document.querySelectorAll('[data-page="1"] .textLayer span')]
        .filter((s) => /entangled atom arrays|quantum processor based/.test(s.textContent)).map((s) => s.getBoundingClientRect())
        .filter((r) => r.top < 400);
      const x = Math.min(...rs.map((r) => r.left)), y = Math.min(...rs.map((r) => r.top));
      return { x, y, width: Math.max(...rs.map((r) => r.right)) - x, height: Math.max(...rs.map((r) => r.bottom)) - y };
    }));
  }
  if (INSPECT) fs.writeFileSync(path.join(dir, 'layout.json'), JSON.stringify({ wrap, k, howBox, claim, cardCount,
    buttons: await page.locator('button[aria-label]').evaluateAll((es) => es.map((e) => e.getAttribute('aria-label')).filter(Boolean)) }, null, 2));

  const beforeIds = new Set(((await inkByPage(account, imported.id))['1'] || []).map((s) => s.id));
  const before = beforeIds.size;

  // 3. Keep writing: the blue pen rings the answer and draws the arrow to "how?".
  take.mark('tools', clock());
  await pointer.click(toolsBtn, 360);
  await page.locator('.pdfInkBar').waitFor();
  await sleep(120);
  if (!INSPECT) await take.frame('inkBar', page.locator('.pdfInkBar'));
  await pointer.click(page.locator('.pdfInkBar button[aria-label^="Blue pen"]'), 400);
  take.mark('pen', clock());
  await snap(page, 'pen');

  const freehand = (points, per = 8) => {
    const out = [];
    for (let i = 0; i < points.length - 1; i++) {
      const a = points[Math.max(0, i - 1)], b = points[i], c = points[i + 1], d = points[Math.min(points.length - 1, i + 2)];
      for (let j = 0; j < per; j++) {
        const t = j / per;
        out.push(b.map((v, q) => 0.5 * ((2 * v) + (-a[q] + c[q]) * t + (2 * a[q] - 5 * v + 4 * c[q] - d[q]) * t * t + (-a[q] + 3 * v - 3 * c[q] + d[q]) * t * t * t)));
      }
    }
    return [...out, points.at(-1)];
  };
  // A ring round the phrase with an open seam, then a bowed arrow from its
  // upper left up to the margin word.
  const cx = claim.x + claim.width / 2, cy = claim.y + claim.height / 2, rx = claim.width / 2 + 6, ry = claim.height / 2 + 7;
  const ring = Array.from({ length: 13 }, (_, i) => {
    const a = 2.55 + i / 12 * 2.12 * Math.PI, wobble = 1 + 0.045 * Math.sin(i * 2.1);
    return [cx + Math.cos(a) * rx * wobble, cy + Math.sin(a) * ry * wobble - 1.2 * Math.cos(a)];
  });
  const tail = [claim.x - 4, claim.y - 4];
  const tip = [howBox.x + howBox.width + 3, howBox.y + howBox.height + 1];
  const shaft = [tail, [(tail[0] + tip[0]) / 2 + 1, (tail[1] + tip[1]) / 2 + 3], tip];
  const angle = Math.atan2(tip[1] - shaft[1][1], tip[0] - shaft[1][0]);
  const barb = (side) => [tip[0] - 10 * Math.cos(angle + side * 0.5), tip[1] - 10 * Math.sin(angle + side * 0.5)];
  const gestures = { ring: freehand(ring), shaft: freehand(shaft), head: freehand([barb(1), tip, barb(-1)], 4) };
  const all = Object.values(gestures).flat();
  const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
  const inkBox = { x: Math.min(...xs), y: Math.min(...ys), width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys) };
  if (!INSPECT) await take.frame('newInk', inkBox);
  // A paced glide from the pen to a rest just below the ring's start, then
  // the pointer rests there while the edit's camera moves in on the paper.
  await sleep(120);
  take.mark('toRest', clock());
  await pointer.glide([gestures.ring[0][0] - 12, gestures.ring[0][1] + 20], 520);
  take.mark('rested', clock());
  await sleep(620);
  take.mark('draw', clock());
  await pointer.drag(gestures.ring, 1000);
  await sleep(120);
  await pointer.drag(gestures.shaft, 420);
  await sleep(90);
  await pointer.drag(gestures.head, 260);
  take.mark('drawn', clock());
  await page.waitForFunction((n) => document.querySelectorAll('[data-page="1"] .inkLayer path').length >= n, note.pages['1'] + 3);
  await sleep(60);
  if (!INSPECT) await take.frame('cards', page.locator('.blockInkCard').nth(2));
  // The hand tool (V): the pen's grey hover ring would sit beside the
  // pointer through the final hold like a second cursor. Then, with no cut,
  // the pointer moves down into the paper's empty left margin, off the ink,
  // the Notes cards and the column splitter (which lights up under it).
  // Off the pen's button first, so the key press does not draw a focus ring on it.
  await page.evaluate(() => document.activeElement?.blur());
  await page.keyboard.press('v');
  const handOn = await page.locator('.pdfInkBar button[aria-label="Hand"][aria-pressed="true"]').waitFor({ timeout: 3000 }).then(() => true, () => false);
  take.check('handTool', handOn);
  take.mark('away', clock());
  await pointer.glide([howBox.x + 4, claim.y + 70], 450);
  take.mark('parked', clock());
  // The final state: a short hold on the close-up, the move back out and the
  // long hold on the whole page.
  await sleep(2700);
  take.mark('end', clock());
  await snap(page, 'final');
  if (!INSPECT) await take.stop();

  // The new strokes are saved in the page's ink, and come back after a reload.
  let saved = [];
  for (let i = 0; i < 60 && saved.length < before + 3; i++) {
    saved = (await inkByPage(account, imported.id))['1'] || [];
    if (saved.length < before + 3) await sleep(150);
  }
  const added = saved.filter((s) => !beforeIds.has(s.id));
  take.check('newStrokesSaved', before === note.pages['1'] && saved.length === before + 3 && added.length === 3,
    { before, saved: saved.length, colors: added.map((s) => s.color) });
  take.check('newStrokesBlue', added.length === 3 && added.every((s) => s.color === '#1d4ed8'),
    added.map((s) => s.color));
  await page.reload();
  await page.locator('[data-page="1"] .textLayer span').first().waitFor({ timeout: 60000 });
  const reloaded = await page.waitForFunction((n) => document.querySelectorAll('[data-page="1"] .inkLayer path').length >= n, note.pages['1'] + 3)
    .then(() => true, () => false);
  take.check('afterReload', reloaded, await page.locator('[data-page="1"] .inkLayer path').count());
  take.check('noApiErrors', problems.length === 0, problems);
  if (INSPECT) {
    console.log('inspected:', JSON.stringify(take.checks), 'screenshots in', dir);
  } else await take.finish({ note: { strokes: note.strokes, pages: note.pages } });
} catch (error) {
  await take.fail(error);
} finally {
  if (browser) await browser.close().catch(() => {});
  await server.stop();
}
