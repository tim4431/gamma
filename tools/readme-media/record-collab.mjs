// README "Share and work together": three people on one page at once, seen
// from the owner's window. In the shared workspace "Quantum lab" (Alex owns
// it, Maya and Sam edit) the page "Reading group: atom arrays" is open in
// three sessions. Maya types a question after "Q1:" and Sam adds a few words
// to the takeaway while Alex clicks into the empty block, pastes a
// screenshot of Fig. 1c, d (a real Ctrl+V), leaves the block with a click
// on the blank page under it and drags the picture's right grip out to size
// it. Everyone's words and the sized picture hold at the end. The edit cuts
// the click out (render_feature_demos.collab), so Maya pauses for it and Sam
// starts after it: the cut drops no key of theirs. The first
// line links the paper itself (a [[ref]] by its id: the curated export is
// imported into Quantum lab), which the take checks shows as a live chip.
//
// Only Alex's window is captured (retina, 130 % interface size), and it is
// narrower than the other recorders' (WINDOW below), so the page head's
// presence avatars sit close to the notes and one framing holds them, the
// peers' lines and the picture at a readable zoom. Maya and Sam
// are real sessions in a plain second browser; their carets and name flags
// are what Gamma draws for them, placed off camera. Their sessions send each
// keystroke 40 ms after it instead of after the app's typing pause
// (take.mjs fastPeerTyping), so their words arrive key by key; Alex's view
// runs the app as it is. Two recording overlays are drawn in Alex's window
// besides the arrow: the ew-resize pointer over a grip, and a small "Ctrl V"
// keycap by the pointer for half a second when the paste key goes down, so
// the viewer sees who pasted.
//
// The page is laid out for that framing: the takeaway and Sam's words fit
// one line, and the screenshot lands at its natural width (well under the
// column's), so Alex widens it into the room beside it and the picture, as
// pasted and as sized, stays in the frame.
//
// The screenshot is cut from the curated paper's own PDF, fetched through the
// API and rendered by collab-figure.py. After the capture the take checks,
// through the API and on every screen, that the picture was saved at the
// dragged width and each session shows it, that Maya's and Sam's words were
// saved and are on Alex's screen, that the upload loads, and the same again
// after Alex reloads.
//
//   node tools/readme-media/record-collab.mjs [--inspect]
//
// Writes artifacts/readme-media/collab/{frames/, timing.json, final.png}.
// --inspect runs the story without a capture and saves screenshots of its
// stages (inspect-*.png) instead.
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium, launchRetina, clipboardImage } from './runtime.mjs';
import { library, openPage, fastPeerTyping, hand, Take, scratch, sleep, ATOMS, INSPECT } from './take.mjs';
import { PYTHON } from '../../frontend/tests/e2e/harness.mjs';

const NAME = 'collab';
const DIR = scratch(NAME);
const HERE = path.dirname(fileURLToPath(import.meta.url));

const TITLE = 'Reading group: atom arrays';
const TAKEAWAY = 'Takeaway: entangled pairs survive a 110 µm move with ==coherence intact==.';
const QUESTIONS = 'Questions for Thursday:';
const Q1 = 'Q1:';
// What the two peers type, each appended to the end of their block. Maya
// types her question in two goes, around Alex's click out of the block.
const MAYA_1 = ' How many';
const MAYA_2 = ' moves before the atoms heat up?';
const MAYA = MAYA_1 + MAYA_2;
const SAM = ' See Fig. 1c, d below.';
// Alex's window in CSS px (the screen stays 2x). 16:10 like the card, so
// the camera's one framing is the window below the tab strip.
const WINDOW = { width: 1040, height: 900 };
// The screenshot's pixel width. A note picture shows at its natural width
// (in CSS px) up to the column's, so it lands this wide, centred.
const FIGURE_PX = 400;
const TARGET_W = 600;          // the width Alex drags the picture out to (CSS px)


/** Type like a person: an uneven 55–190 ms a key (mostly quick, now and then
 *  a slower one), a beat after most words and a longer think after some.
 *  Seeded, so every take types alike. */
async function typeLike(page, text, seed) {
  let s = seed;
  const random = () => (s = (s * 1103515245 + 12345) % 2 ** 31) / 2 ** 31;
  let words = 0;
  for (const ch of text) {
    await page.keyboard.type(ch);
    const r = random();
    let wait = 55 + 75 * r * r + (random() < 0.12 ? 60 : 0);
    if (ch === ' ') wait += ++words % 3 === 0 ? 220 + 200 * random() : 40 + 120 * random();
    if (ch === ',') wait += 180;
    await sleep(wait);
  }
}

// Recording overlays in Alex's window: over a resize grip, and while dragging
// one, the drawn arrow becomes the ew-resize pointer a person sees there; and
// window.__keycap(keys) shows the pressed keys by the pointer for a moment.
const overlays = () => window.addEventListener('DOMContentLoaded', () => {
  const ew = document.createElement('div');
  ew.innerHTML = '<svg width="28" height="28" viewBox="0 0 28 28"><path d="M2 14l6.4-6v3.6h11.2V8l6.4 6-6.4 6v-3.6H8.4V20z" '
    + 'fill="#16181d" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  ew.style.cssText = 'position:fixed;z-index:2147483647;left:0;top:0;width:28px;height:28px;margin:-14px 0 0 -14px;'
    + 'pointer-events:none;display:none;filter:drop-shadow(0 1.5px 2px rgba(0,0,0,.35))';
  document.body.appendChild(ew);
  let held = false, x = 0, y = 0;
  document.addEventListener('pointerdown', (e) => { held = !!e.target.closest?.('.mdResizeGrip'); }, true);
  document.addEventListener('pointerup', () => { held = false; }, true);
  document.addEventListener('pointermove', (e) => {
    x = e.clientX; y = e.clientY;
    const on = held || !!e.target.closest?.('.mdResizeGrip');
    ew.style.display = on ? 'block' : 'none';
    ew.style.transform = `translate(${x}px,${y}px)`;
    const arrow = document.querySelector('#__fakecur');
    if (arrow) arrow.style.visibility = on ? 'hidden' : '';
  }, true);

  const cap = document.createElement('div');
  cap.style.cssText = 'position:fixed;z-index:2147483646;left:0;top:0;display:flex;gap:5px;padding:6px 8px;border-radius:10px;'
    + 'background:rgba(22,24,29,.9);box-shadow:0 4px 14px rgba(0,0,0,.25);pointer-events:none;opacity:0;'
    + 'transition:opacity 120ms ease-out;font:600 17px/1 system-ui,"Segoe UI",sans-serif;color:#fff';
  document.body.appendChild(cap);
  let hide = 0;
  window.__keycap = (keys, ms = 900) => {
    cap.innerHTML = keys.map((k) => `<span style="display:inline-block;min-width:16px;text-align:center;padding:5px 8px;`
      + `border-radius:6px;background:#3a3d45;box-shadow:inset 0 -2px 0 rgba(0,0,0,.35)">${k}</span>`).join('');
    cap.style.transform = `translate(${x + 22}px,${y + 26}px)`;
    cap.style.opacity = '1';
    clearTimeout(hide);
    hide = setTimeout(() => { cap.style.opacity = '0'; }, ms);
  };
});

const lib = await library({ users: ['Alex', 'Maya', 'Sam'], shared: 'Quantum lab' });
const { server } = lib;
const [alex, maya, sam] = lib.accounts;
// An inspection keeps its Take apart, so the kept take's timing.json stays.
const take = new Take(INSPECT ? scratch(path.join(NAME, 'inspect')) : DIR, NAME);
let recorder, peers;
try {
  // The screenshot, from the paper's PDF as the server stores it.
  const paper = await alex.api(`/api/blocks/${ATOMS.id}`);
  const docId = paper.properties?.doc_id;
  if (!docId) throw new Error('The curated paper has no stored PDF');
  const pdf = await alex.api(`/api/uploads/${docId}.pdf`, { raw: true });
  if (!pdf.ok) throw new Error(`GET the paper's PDF: ${pdf.status}`);
  fs.writeFileSync(path.join(take.dir, 'paper.pdf'), Buffer.from(await pdf.arrayBuffer()));
  const figurePath = path.join(take.dir, 'fig1cd.png');
  execFileSync(PYTHON, ['-I', path.join(HERE, 'collab-figure.py'), path.join(take.dir, 'paper.pdf'), figurePath, String(FIGURE_PX)], { stdio: 'inherit' });
  const FIGURE = fs.readFileSync(figurePath).toString('base64');

  // The shared page: the paper link, the takeaway (Sam's), the questions,
  // "Q1:" (Maya's) and the empty block Alex pastes into.
  const note = await alex.api('/api/pages', { method: 'POST', body: { title: TITLE } });
  await alex.api(`/api/blocks/${note.id}/children`, { method: 'PUT', body: { blocks: [
    { content: `Paper: [[${ATOMS.id}]]`, children: [] },
    { content: TAKEAWAY, children: [] },
    { content: QUESTIONS, children: [] },
    { content: Q1, children: [] },
    { content: '', children: [] },
  ] } });
  const seeded = (await alex.api(`/api/blocks/${note.id}/subtree`)).block.children;
  const ids = { paper: seeded[0].id, take: seeded[1].id, q1: seeded[3].id, pic: seeded[4].id };
  const url = `/?page=${note.id}&ws=${lib.ws}`;
  const row = (page, id) => page.locator(`.blockRowWrap[data-block-id="${id}"]`);
  const settle = async (page) => {
    await page.goto(new URL(url, server.base).href, { waitUntil: 'networkidle' });
    await page.locator('.blockRow', { hasText: QUESTIONS }).first().waitFor({ timeout: 30000 });
    const close = page.getByRole('button', { name: 'Close Chat', exact: true });
    if (await close.isVisible().catch(() => false)) await close.click();
  };
  const editing = (page, id) => page.waitForFunction((id) => {
    const cm = document.querySelector(`.blockRowWrap[data-block-id="${id}"] .blockEditorCm .cm-content`);
    return cm && cm.contains(document.activeElement);
  }, id);
  // The box of a block's words as they read (not its full-width row), with
  // the name flag a peer's caret carries above its end.
  const inkBox = (page, id) => row(page, id).locator('.blockRendered').first().evaluate((el) => {
    const rects = [];
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      if (!n.textContent.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      for (const b of range.getClientRects()) if (b.width > 0 && b.height > 0) rects.push({ l: b.left, t: b.top, r: b.right, b: b.bottom });
    }
    for (const c of el.querySelectorAll('.peerCaret')) {
      const b = c.getBoundingClientRect();
      rects.push({ l: b.left, t: b.top - 24, r: b.right + 52, b: b.bottom });
    }
    const l = Math.min(...rects.map((b) => b.l)), t = Math.min(...rects.map((b) => b.t));
    return { x: l, y: t, width: Math.max(...rects.map((b) => b.r)) - l, height: Math.max(...rects.map((b) => b.b)) - t };
  });

  // Maya and Sam: plain sessions in a browser of their own.
  peers = await chromium.launch({ headless: true });
  const peer = async (account) => {
    const context = await account.context(peers, { locale: 'en-US', colorScheme: 'light' });
    await context.addInitScript(() => { try { localStorage.setItem('gamma-suggest-tours', '0'); } catch {} });
    await context.addInitScript(fastPeerTyping);
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    await settle(page);
    return page;
  };
  const [M, S] = await Promise.all([peer(maya), peer(sam)]);

  // Alex: the recorded window.
  // (Chromium takes the last --window-size, so this one wins over runtime's.)
  recorder = await launchRetina({ args: [`--window-size=${WINDOW.width},${WINDOW.height}`] });
  const A = await openPage(recorder, alex, { uiScale: 1.3, clipboard: true, init: overlays });
  const errors = [];
  A.on('pageerror', (e) => errors.push(e.message));
  A.on('response', (r) => { if (r.url().includes('/api/') && r.status() >= 400) errors.push(`${r.status()} ${new URL(r.url()).pathname}`); });
  await settle(A);
  const windowGot = await A.evaluate(() => [innerWidth, innerHeight, devicePixelRatio]);
  if (windowGot.join() !== [WINDOW.width, WINDOW.height, 2].join()) throw new Error(`Alex's window is ${windowGot.join(' x ')}`);
  await A.locator('.presenceBar .peerAvatar').nth(1).waitFor({ timeout: 15000 });
  // The paper line is a live link to the paper (its title, from the page),
  // not the dashed chip of a title no page answers.
  const paperRow = row(A, ids.paper);
  await paperRow.locator('.blockRefChip').first().waitFor({ timeout: 10000 });
  const chip = await paperRow.locator('.blockRefChip').first().textContent();
  take.check('the paper line links the paper', chip.trim() === ATOMS.title && !(await paperRow.locator('.unlinkedRef').count()), chip);

  // Off camera: Maya opens the end of "Q1:", Sam the end of the takeaway,
  // so their carets and name flags are on Alex's page; the screenshot waits
  // on Alex's clipboard.
  for (const [page, id] of [[M, ids.q1], [S, ids.take]]) {
    await row(page, id).locator('.blockBody').first().click();
    await editing(page, id);
    await page.keyboard.press('Control+End');
  }
  for (const id of [ids.q1, ids.take]) await row(A, id).locator('.peerCaret').first().waitFor({ timeout: 10000 });
  await clipboardImage(A, FIGURE);
  // Alex's pointer rests on blank page right of the empty block, in the frame.
  const empty = await row(A, ids.pic).boundingBox();
  const rest = [780, empty.y + empty.height + 120];
  const pointer = hand(A, rest);
  await pointer.park(...rest);
  await sleep(1200);
  const takeLength = (await row(A, ids.take).locator('.blockRendered').first().textContent()).trim().length;

  // --inspect: a map of what lies under each point (# a row, o a control,
  // t the page head, . blank page), to choose the pointer's spots.
  const dumpMap = async (label) => {
    if (!INSPECT) return;
    const map = await A.evaluate(() => {
      const lines = [];
      for (let y = 60; y < 900; y += 15) {
        let line = String(y).padStart(4) + ' ';
        for (let x = 0; x < innerWidth; x += 15) {
          const el = document.elementFromPoint(x, y);
          line += !el ? ' ' : el.closest('.blockRowWrap') ? '#' : el.closest('input, textarea, button, a, [contenteditable="true"], [role="button"]') ? 'o' : el.closest('.pageTitleRow') ? 't' : '.';
        }
        lines.push(line);
      }
      return lines.join('\n');
    });
    console.log(`${label}\n${map}`);
  };
  await dumpMap('ready');

  // What the viewer sees, marked on the frame it first shows.
  await A.evaluate((watch) => { window.__collab = watch; }, { ...ids, takeLength, maya: MAYA.trim(), sam: SAM.trim() });
  take.page = A;
  if (INSPECT) { const t0 = Date.now(); take.t0 = t0; take.clock = () => (Date.now() - t0) / 1000; }
  await take.watch(() => {
    const w = window.__collab;
    const at = (id) => document.querySelector(`.blockRowWrap[data-block-id="${id}"]`);
    const text = (id) => at(id)?.querySelector('.blockRendered')?.textContent.trim() || '';
    const img = at(w.pic)?.querySelector('img[src*="/api/uploads/"]');
    const loaded = !!(img && img.complete && img.naturalWidth > 0 && img.getBoundingClientRect().height > 0);
    return {
      editorOpen: !!at(w.pic)?.querySelector('.blockEditorCm'),
      mayaFirst: text(w.q1).length > 3,
      mayaDone: text(w.q1).endsWith(w.maya),
      samFirst: text(w.take).length > w.takeLength,
      samDone: text(w.take).endsWith(w.sam),
      picture: loaded,
      rendered: loaded && !!at(w.pic).querySelector('.mdImgFrame') && !at(w.pic).querySelector('.blockEditorCm'),
      sized: loaded && Number(img.getAttribute('width')) > 0 && !document.querySelector('.mdResizeGrip:active'),
      settled: !!w.away && loaded && !document.querySelector('.mdObjectSelected, .mdImgFrame:hover'),
    };
  });
  await take.frame('presence', '.presenceBar');
  await take.frame('title', A.locator('.pageTitleRow input, .pageTitleRow [contenteditable], .pageTitleRow h1, .pageTitleRow').first());
  await take.frame('paper', await inkBox(A, ids.paper));
  await take.frame('emptyBlock', row(A, ids.pic).locator('.blockRow').first());
  const shot = (name) => INSPECT && A.screenshot({ path: path.join(DIR, `inspect-${name}.png`) });
  await shot('0-ready');

  if (!INSPECT) await take.start(A);
  // Maya starts her question while Alex reaches for the empty block; Sam
  // follows once the picture is in (his words point at it).
  // Maya stops after her first words until Alex is back from the click
  // out of the block (the edit cuts that click; her pause is in the cut).
  let mayaGo;
  const mayaResume = new Promise((resolve) => { mayaGo = resolve; });
  const mayaFirstWords = (async () => { await sleep(650); take.mark('mayaTypes'); await typeLike(M, MAYA_1, 7); take.mark('mayaPause'); })();
  const mayaTyping = (async () => { await mayaFirstWords; await mayaResume; take.mark('mayaResumes'); await typeLike(M, MAYA_2, 9); })();
  await sleep(750);

  // Alex clicks into the empty block and pastes the screenshot.
  take.mark('reach');
  await pointer.click(row(A, ids.pic).locator('.blockBody').first(), 480, 0.12, 0.5);
  take.mark('clickBlock');
  await editing(A, ids.pic);
  await sleep(300);
  // The keycap shows from just before the key until shortly after the
  // picture lands, and is gone before the pointer moves on.
  await A.evaluate(() => window.__keycap(['Ctrl', 'V'], 480));
  await sleep(160);
  take.mark('paste');
  await A.keyboard.press('Control+V');
  await take.until('picture', 15000);
  const pictureAt = take.clock();
  await take.frame('pasted', row(A, ids.pic).locator('img[src*="/api/uploads/"]').first());
  await shot('1-pasted');
  // The landed picture shows 0.65 s before the cut (its last 0.3 s is the
  // dissolve), and Maya has stopped 0.35 s before it, so no key of hers
  // falls in the dissolve.
  await mayaFirstWords;
  const mayaStopped = take.clock();
  await sleep(Math.max(0, (Math.max(pictureAt + 0.65, mayaStopped + 0.35) - take.clock()) * 1000));
  take.mark('leaveGo');

  // Alex leaves the block with a click on the blank page just under it:
  // below the notes' tail (a click on the tail itself would start a new
  // block), still in the frame while the picture is small.
  const leaveAt = await A.evaluate((id) => {
    const tail = document.querySelector('.notesTail')?.getBoundingClientRect();
    if (!tail) return null;
    const pic = document.querySelector(`.blockRowWrap[data-block-id="${id}"] img[src*="/api/uploads/"]`)?.getBoundingClientRect();
    const x = Math.round(pic ? pic.left + pic.width * 0.55 : tail.left + tail.width / 2), y = Math.round(tail.bottom + 16);
    const el = document.elementFromPoint(x, y);
    const blank = !!el && !el.closest('.notesTail, .blockRowWrap, input, textarea, button, a, [contenteditable="true"], [role="button"]');
    return { x, y, blank, under: el && String(el.className || el.tagName) };
  }, ids.pic);
  if (INSPECT) console.log('leave spot:', JSON.stringify(leaveAt));
  if (!leaveAt?.blank) throw new Error(`No blank page under the notes: ${JSON.stringify(leaveAt)}`);
  await pointer.glide([leaveAt.x, leaveAt.y], 420);
  await sleep(70);
  take.mark('leave');
  await pointer.press();
  await take.until('rendered', 3000);
  await shot('2-rendered');
  const blocks = (await alex.api(`/api/blocks/${note.id}/subtree`)).block.children.length;
  take.check('leaving the block adds no block', blocks === seeded.length, { blocks });
  const img = row(A, ids.pic).locator('.mdImgFrame img.mdImg');
  const before = await img.boundingBox();
  await take.frame('picture0', before);
  await sleep(120);
  // Back from the click: the edit resumes here. Maya goes on and Sam starts
  // once the dissolve into this point is over.
  take.mark('toGrip');
  setTimeout(() => mayaGo(), 420);
  const samTyping = (async () => { await sleep(650); take.mark('samTypes'); await typeLike(S, SAM, 11); })();

  // Onto the right grip and drag it out: the picture is centred, so the
  // width grows by twice the pointer's travel and the grip stays under it.
  // The grip is held a little under its middle (it is 42 px tall), so the
  // pointer keeps clear of the hover toolbar at the picture's top.
  const gripAt = (w) => [before.x + before.width / 2 + w / 2 - 1, before.y + before.height / 2 + 14];
  await pointer.glide(gripAt(before.width), 520);
  await sleep(160);
  const grip = row(A, ids.pic).locator('.mdResizeGrip.right');
  const opacity = Number(await grip.evaluate((el) => getComputedStyle(el).opacity));
  take.check('grip shows under the pointer', opacity > 0.5, { opacity });
  take.mark('dragStart');
  await A.mouse.down();
  await sleep(60);
  const from = gripAt(before.width), to = gripAt(TARGET_W);
  const DRAG_MS = 750;
  const steps = Math.ceil(DRAG_MS / 16), t0 = performance.now();
  for (let i = 1; i <= steps; i++) {
    const u = i / steps, e = u * u * (3 - 2 * u);
    await A.mouse.move(from[0] + (to[0] - from[0]) * e, from[1]);
    const left = t0 + DRAG_MS * i / steps - performance.now();
    if (left > 0) await sleep(left);
  }
  await sleep(60);
  const dragged = Math.round((await img.boundingBox()).width);
  // On release the picture re-renders at its saved width, which drops its
  // hover toolbar and grips for a frame; were the pointer to rest on it, they
  // would fade back in and out again as it left. So the hand moves off at
  // once, quick at first and easing out (a flick away), to the blank right
  // of the picture: grips and toolbar go once, with the pointer, and it
  // reads as done. The row keeps the light tint Gamma gives the row last
  // clicked: Alex's row.
  await A.evaluate(() => { window.__collab.away = true; });
  take.mark('release');
  await A.mouse.up();
  take.mark('dragEnd');
  const off = [Math.min(WINDOW.width - 70, before.x + before.width / 2 + TARGET_W / 2 + 95), to[1] + 30];
  const OFF_MS = 420, offSteps = Math.ceil(OFF_MS / 16), o0 = performance.now();
  for (let i = 1; i <= offSteps; i++) {
    const e = 1 - (1 - i / offSteps) ** 3;
    await A.mouse.move(to[0] + (off[0] - to[0]) * e, to[1] + (off[1] - to[1]) * e);
    const left = o0 + OFF_MS * i / offSteps - performance.now();
    if (left > 0) await sleep(left);
  }
  await pointer.park(...off);
  take.mark('offPicture');
  await take.until('settled', 4000);
  await shot('3-sized');
  await dumpMap('sized');
  await Promise.all([mayaTyping, samTyping]);
  take.mark('peersTyped');
  await take.until('mayaDone', 5000);
  await take.until('samDone', 5000);
  await take.frame('picture', img);
  await take.frame('takeaway', await inkBox(A, ids.take));
  await take.frame('q1', await inkBox(A, ids.q1));
  await take.frame('page', A.locator('.blockRowWrap').last());
  // The end holds every edit at least 1.8 s, from the last of them.
  const last = Math.max(take.marks.settled, take.marks.mayaDone, take.marks.samDone);
  await sleep(Math.max(0, (last + 1.8 - take.clock()) * 1000));
  take.mark('end');
  await shot('4-end');
  if (!INSPECT) await take.stop();
  await A.screenshot({ path: path.join(take.dir, 'final.png') });

  // Checks: every edit is on the server (the peers' went out as they typed).
  const picRe = /^!\[\|(\d+)\]\((\/api\/uploads\/[^)]+)\)$/;
  let tree = [];
  for (let i = 0; i < 40; i++) {
    tree = (await alex.api(`/api/blocks/${note.id}/subtree`)).block.children;
    const pic = picRe.exec((tree.find((b) => b.id === ids.pic)?.content || '').trim());
    if (pic && tree.find((b) => b.id === ids.q1)?.content === Q1 + MAYA && tree.find((b) => b.id === ids.take)?.content === TAKEAWAY + SAM) break;
    await sleep(250);
  }
  const content = (id) => tree.find((b) => b.id === id)?.content;
  const saved = picRe.exec((content(ids.pic) || '').trim());
  take.check('picture saved at the dragged width', saved && Number(saved[1]) === dragged && Math.abs(dragged - TARGET_W) <= 1, { saved: content(ids.pic), dragged });
  take.check("Maya's question saved", content(ids.q1) === Q1 + MAYA, content(ids.q1));
  take.check("Sam's words saved", content(ids.take) === TAKEAWAY + SAM, content(ids.take));
  take.check('the page keeps its five blocks', tree.length === seeded.length, tree.map((b) => b.content));
  const upload = saved ? await alex.api(saved[2], { raw: true }) : null;
  take.check('the upload loads', upload?.ok && (upload.headers.get('content-type') || '').startsWith('image/'), upload && { status: upload.status, type: upload.headers.get('content-type') });
  const widthOn = (page) => page.waitForFunction(({ id, w }) => {
    const i = document.querySelector(`.blockRowWrap[data-block-id="${id}"] .mdImgFrame img.mdImg`);
    return i && i.complete && i.naturalWidth > 0 && Number(i.getAttribute('width')) === w && Math.abs(i.getBoundingClientRect().width - w) <= 1;
  }, { id: ids.pic, w: dragged }, { timeout: 10000 }).then(() => true, () => false);
  const onScreen = async (page, id, text) => (await row(page, id).locator('.blockRendered').first().textContent().catch(() => '')).includes(text);
  for (const [who, page] of [['Alex', A], ['Maya', M], ['Sam', S]]) take.check(`${who} sees the picture at ${dragged} px`, await widthOn(page));
  take.check("Maya's question on Alex's screen", await onScreen(A, ids.q1, MAYA.trim()));
  take.check("Sam's words on Alex's screen", await onScreen(A, ids.take, SAM.trim()));
  await settle(A);
  take.check('after a reload: the picture at its width', await widthOn(A));
  take.check("after a reload: Maya's and Sam's words", await onScreen(A, ids.q1, MAYA.trim()) && await onScreen(A, ids.take, SAM.trim()));
  take.check('no page errors or failed calls', !errors.length, errors);

  if (INSPECT) {
    await take.seen();
    console.log('inspect:', JSON.stringify({ marks: take.marks, framing: take.framing, checks: take.checks, details: take.details }, null, 1));
  } else await take.finish({ window: WINDOW, verified: { page: note.id, workspace: 'Quantum lab', picture: content(ids.pic), dragged, maya: content(ids.q1), sam: content(ids.take) } });
} catch (error) {
  await take.fail(error);
} finally {
  await take.stop().catch(() => {});
  if (recorder) await recorder.close().catch(() => {});
  if (peers) await peers.close().catch(() => {});
  await server.stop();
}
