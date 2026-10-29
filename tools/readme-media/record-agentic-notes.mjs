// README demo "Write into your notes" (agentic-notes): a lecture slide's
// equation, pasted into the chat, lands in the note as display math under
// the block the caret is on, after the user allows the edit.
//
// The story, on a notes page "Rabi oscillations" seeded off camera (an intro
// line, the Hamiltonian as display math, and the caption the caret is on, so
// the chat shows "Block at your cursor"), chat docked right, Interface size
// 130%:
//   1. the pointer goes into the chat box and a real Ctrl+V pastes the slide;
//   2. the request types and Enter sends it;
//   3. Gamma's agent asks before create_block (Edit note blocks: Ask) and the
//      card shows the LaTeX; Allow once;
//   4. the equation lands rendered under the caption and the one-line reply
//      finishes; the result holds.
// The slide is drawn here with the app's own KaTeX on a slide-like page and
// screenshotted (makeSlide). The model is scripted (models/ai-notes.mjs):
// only its words and its one tool call are written in advance; Gamma builds
// the request, shows the card and makes the edit.
//
//   node tools/readme-media/record-agentic-notes.mjs [--inspect]
// (setup: tools/readme-media/README.md). Writes
// artifacts/readme-media/agentic-notes/{frames/, rabi-slide.png, final.png,
// timing.json}; --inspect plays the story without a capture and leaves
// inspect-*.png there. render_feature_demos.py agentic-notes makes the WebP.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { ROOT, VIEW, chromium, launchRetina, clipboardImage, openChat } from './runtime.mjs';
import { library, openPage, hand, typeText, Take, demoModel, scratch, sleep, INSPECT } from './take.mjs';
import { LATEX } from './models/ai-notes.mjs';

const NAME = 'agentic-notes';
const DIR = scratch(NAME);
const TITLE = 'Rabi oscillations';
const INTRO = 'A **two-level atom** driven on resonance undergoes ==Rabi oscillations==.';
const HAMILTONIAN = '$$H=\\frac{\\hbar\\Omega}{2}\\left(|e\\rangle\\langle g|+|g\\rangle\\langle e|\\right)-\\hbar\\Delta\\,|e\\rangle\\langle e|$$';
const CAPTION = 'Excited-state population, from the lecture slide:';
const ASK = "Add this equation under the block I'm on, as display math.";
const SLIDE = { width: 1140, height: 300 };
const shot = async (page, name) => { if (INSPECT) await page.screenshot({ path: path.join(DIR, `inspect-${name}.png`) }); };

/** hand().glide from the note into the chat, eased and on the clock, that
 *  sends no pointer sample inside `zone` (the sash between the two panes
 *  and its hit margins), so the sash does not light up as the pointer
 *  passes: the step over it is one sample, like any other. */
async function glideOver(page, pointer, to, ms, zone) {
  const from = pointer.at, n = Math.max(1, Math.ceil(ms / 16)), t0 = performance.now();
  for (let i = 1; i <= n; i++) {
    const u = i / n, e = u * u * (3 - 2 * u);
    const p = [from[0] + (to[0] - from[0]) * e, from[1] + (to[1] - from[1]) * e];
    if (i === n || p[0] < zone.x0 || p[0] > zone.x1) await page.mouse.move(p[0], p[1]);
    const rest = t0 + ms * i / n - performance.now();
    if (rest > 0) await sleep(rest);
  }
  await pointer.park(...to);
}

/** The lecture slide: the generalized Rabi formula typeset by the frontend's
 *  KaTeX (its CSS and fonts, served on a private origin) on a white slide,
 *  screenshotted at SLIDE size. PNG bytes. */
async function makeSlide() {
  const katex = path.dirname(createRequire(path.join(ROOT, 'frontend/package.json')).resolve('katex/dist/katex.min.css'));
  // A plain headless browser of its own: the retina one's forced screen
  // scale does not take a page-sized screenshot reliably.
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: SLIDE, deviceScaleFactor: 2 });
  try {
    const page = await context.newPage();
    await page.route('http://slide.invalid/**', (route) => {
      const name = new URL(route.request().url()).pathname.slice(1);
      if (name === '') return route.fulfill({ contentType: 'text/html', body: SLIDE_HTML });
      const file = path.join(katex, name);
      if (!file.startsWith(katex) || !fs.existsSync(file)) return route.fulfill({ status: 404 });
      return route.fulfill({ path: file });
    });
    await page.goto('http://slide.invalid/');
    await page.evaluate((tex) => {
      window.katex.render(tex, document.getElementById('formula'), { displayMode: true, throwOnError: true });
      return document.fonts.ready;
    }, LATEX.replace(/^\$\$|\$\$$/g, ''));
    const fonts = await page.evaluate(() => [...document.fonts].filter((f) => f.family.startsWith('KaTeX') && f.status === 'loaded').length);
    if (!fonts) throw new Error('The slide was drawn without the KaTeX fonts');
    return await page.screenshot({ scale: 'css' });
  } finally {
    await browser.close();
  }
}
const SLIDE_HTML = `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="katex.min.css"><script src="katex.min.js"></script>
<style>
  html, body { margin: 0; width: ${SLIDE.width}px; height: ${SLIDE.height}px; overflow: hidden; }
  body { background: #fdfcf9; font-family: "Segoe UI", Arial, sans-serif; color: #1b2230; position: relative; }
  .bar { position: absolute; left: 0; top: 0; bottom: 0; width: 10px; background: #2f5d9e; }
  .head { position: absolute; left: 48px; top: 30px; font-size: 21px; color: #5b6475; letter-spacing: .2px; }
  .head b { color: #2f5d9e; font-weight: 600; }
  #formula { position: absolute; left: 0; right: 0; top: 78px; font-size: 40px; }
  #formula .katex-display { margin: 0; }
  .foot { position: absolute; left: 48px; right: 40px; bottom: 22px; font-size: 15px; color: #8a91a0; display: flex; justify-content: space-between; }
</style></head><body>
  <div class="bar"></div>
  <div class="head"><b>Lecture 4</b> · Generalized Rabi formula</div>
  <div id="formula"></div>
  <div class="foot"><span>Detuning Δ, Rabi frequency Ω, atom starts in |g⟩</span><span>12</span></div>
</body></html>`;

// What the viewer sees, logged on every animation frame (Take.watch).
const PROBE = () => {
  const rows = document.querySelectorAll('.chatBubbleRow.ai');
  const bubble = rows[rows.length - 1]?.querySelector('.chatBubble.ai');
  const reply = bubble ? [...bubble.querySelectorAll('p, li')].reduce((n, e) => n + e.textContent.length, 0) : 0;
  const katex = [...document.querySelectorAll('.blockRow .blockRendered .katex-display')].filter((k) => !k.closest('.aiGhostWrap')).length;
  const card = document.querySelector('.chatApproval:not(.sent)');
  const busy = !!document.querySelector('.chatStopBtn') || !!document.querySelector('.chatSteps.live');
  return {
    pasted: [...document.querySelectorAll('.chatImgPreview img')].some((i) => i.complete && i.naturalWidth > 0),
    asked: document.querySelectorAll('.chatBubbleRow.user').length > 0,
    card: !!card && card.getBoundingClientRect().height > 40,
    landed: katex >= 2,
    replyStart: reply > 0,
    replyDone: reply > 0 && !busy,
  };
};

const { server, account } = await library();
let model = null, browser = null, page = null;
const take = new Take(INSPECT ? scratch(path.join(NAME, 'inspect')) : DIR, NAME);
try {
  model = await demoModel(account, 'ai-notes');

  // Off camera: the page and its three blocks, through the API.
  await account.api('/api/prefs/open-tabs', { method: 'PUT', body: { value: [] } }).catch(() => {});
  const note = await account.api('/api/pages', { method: 'POST', body: { title: TITLE } });
  await account.api(`/api/blocks/${note.id}/children`, { method: 'PUT', body: { blocks: [INTRO, HAMILTONIAN, CAPTION].map((content) => ({ content, children: [] })) } });
  const seeded = (await account.api(`/api/blocks/${note.id}/subtree`)).block.children || [];
  if (JSON.stringify(seeded.map((b) => b.content)) !== JSON.stringify([INTRO, HAMILTONIAN, CAPTION])) throw new Error(`Seeded ${JSON.stringify(seeded.map((b) => b.content))}`);
  const captionId = seeded[2].id;

  const slide = await makeSlide();
  browser = await launchRetina();
  fs.writeFileSync(path.join(DIR, 'rabi-slide.png'), slide);
  const slideSize = [slide.readUInt32BE(16), slide.readUInt32BE(20)];
  if (slideSize.join() !== `${SLIDE.width},${SLIDE.height}`) throw new Error(`The slide is ${slideSize.join(' x ')}`);

  page = await openPage(browser, account, { url: `/?page=${note.id}&ws=${account.ws}`, uiScale: 1.3, clipboard: true });
  const requests = [];
  page.on('request', (r) => {
    if (new URL(r.url()).pathname !== '/api/ai/chat' || r.method() !== 'POST') return;
    const b = r.postDataJSON() || {};
    requests.push({ prompt: b.prompt, page_id: b.page_id, focus_block_id: b.focus_block_id, agent_scope: b.agent_scope, images: (b.images || []).length });
  });
  const captionRow = page.locator('.blockRow', { hasText: CAPTION }).last();
  await captionRow.waitFor({ timeout: 30000 });
  await page.waitForFunction(() => document.querySelectorAll('.blockRow .blockRendered .katex-display').length === 1, null, { timeout: 15000 });
  const uiScale = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--ui-scale').trim());
  if (+uiScale !== 1.3) throw new Error(`The interface size is ${uiScale}`);
  await openChat(page);
  const input = page.locator('.chatInput');
  await input.waitFor();
  if (await page.locator('.chatSetup').count()) throw new Error('The chat shows its setup card: no AI connection');
  await sleep(2500);   // the login check runs 1.5 s after load
  if (await page.locator('.chatHealthStrip').count()) throw new Error(`The chat reports: ${await page.locator('.chatHealthStrip').innerText()}`);
  // The sash between the note and the chat, with its 6 px hit margins.
  const chatLeft = (await page.locator('.dockSlot').filter({ has: input }).first().boundingBox()).x;
  const sashes = await page.locator('.sash-horizontal').evaluateAll((els) => els.map((el) => { const b = el.getBoundingClientRect(); return [b.x, b.right]; }));
  const near = sashes.filter(([x0, x1]) => x1 <= chatLeft + 1 && x1 >= chatLeft - 20);
  if (near.length !== 1) throw new Error(`No sash beside the chat: ${JSON.stringify(sashes)} / ${chatLeft}`);
  const sash = { x0: near[0][0] - 9, x1: near[0][1] + 9 };

  // Off camera: the caret at the end of the caption, so the chat shows the
  // "Block at your cursor" chip; the pointer rests where it clicked.
  // The rendered caption's words (its row and paragraph are wider).
  const text = await captionRow.locator('.blockRendered').first().evaluate((el) => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let last = null;
    while (walker.nextNode()) if (walker.currentNode.textContent.trim()) last = walker.currentNode;
    const r = document.createRange();
    r.selectNodeContents(last);
    const b = r.getBoundingClientRect();
    return { x: b.x, y: b.y, width: b.width, height: b.height };
  });
  const pointer = hand(page);
  await pointer.park(text.x + text.width - 30, text.y + text.height / 2);
  await pointer.press();
  await page.locator('.blockEditorCm .cm-content').waitFor();
  await page.keyboard.press('End');
  const chip = page.locator('.chatSelChip.isCursor');
  await chip.waitFor();
  const chipText = (await chip.locator('.chatSelChipText').innerText()).replace(/\s+/g, ' ').trim();
  await pointer.glide([text.x + text.width + 60, text.y + text.height + 70], 300);
  await page.mouse.move(...pointer.at);
  // The slide goes on the page's clipboard for the real paste.
  await clipboardImage(page, slide.toString('base64'));
  // A full-window layer at opacity .001 keeps the screencast sampling the
  // whole window at 60 Hz instead of locking onto the typing (invisible).
  await page.evaluate(() => {
    const d = document.createElement('div');
    d.id = '__keepalive';
    d.style.cssText = 'position:fixed;inset:0;background:#fff;opacity:.001;pointer-events:none;z-index:2147483645';
    document.body.appendChild(d);
    d.animate([{ opacity: 0.001 }, { opacity: 0.002 }], { duration: 400, iterations: Infinity, direction: 'alternate' });
  });
  await sleep(1000);
  await shot(page, '0-start');

  if (INSPECT) { take.page = page; take.t0 = Date.now(); take.clock = () => (Date.now() - take.t0) / 1000; take.mark('start'); }
  else await take.start(page);
  await take.watch(PROBE);
  await take.frame('title', page.locator('.blockRow', { hasText: 'two-level atom' }).first());
  await take.frame('caption', captionRow);
  await take.frame('chat', page.locator('.dockSlot').filter({ has: input }).first());
  await take.frame('composer', page.locator('.chatComposer').first());
  await sleep(1000);

  // 1. Into the chat box, and the slide is pasted.
  take.mark('glide');
  const box = await input.boundingBox();
  await glideOver(page, pointer, [box.x + box.width * 0.25, box.y + box.height / 2], 520, sash);
  await sleep(90);
  await pointer.press();
  take.mark('clicked');
  await sleep(120);
  // Off the composer, into the blank chat space just above it, right of
  // the card's buttons-to-be: the "Block at your cursor" chip, the
  // thumbnail and the words typed stay clear while the slide is pasted and
  // the request types; after Send the card comes up above this spot.
  const cb = await page.locator('.chatComposer').first().boundingBox();
  // (the composer grows by the thumbnail row and a second line, about
  // 85 px, once pasted and typed; the card ends about 9 px above this)
  const rest = [cb.x + cb.width * 0.88, cb.y - 113];
  take.mark('stepOff');
  await pointer.glide(rest, 340);
  await sleep(60);
  take.mark('paste');
  await page.keyboard.press('Control+v');
  await take.until('pasted', 8000);
  await take.frame('preview', page.locator('.chatImgPreview').first());
  await sleep(450);
  await shot(page, '1-pasted');

  // 2. The request, typed; Enter sends it.
  take.mark('typeStart');
  await typeText(page, ASK, { ms: 30 });
  take.mark('typed');
  const typedValue = await input.inputValue();
  await take.frame('composer', page.locator('.chatComposer').first());
  await take.frame('chip', chip);
  await sleep(400);
  await shot(page, '2-typed');
  take.mark('sent');
  await page.keyboard.press('Enter');
  await take.until('asked', 8000);

  // 3. The card with the LaTeX; Allow once.
  await take.until('card', 60000);
  const card = page.locator('.chatApproval:not(.sent)').first();
  await sleep(150);
  await take.frame('approval', card);
  const cardText = (await card.innerText()).replace(/\s+/g, ' ').trim();
  await shot(page, '3-card');
  await sleep(1000);
  take.mark('toAllow');
  await pointer.click(card.getByRole('button', { name: 'Allow once', exact: true }), 480, 0.45, 0.5);
  take.mark('allowed');
  await sleep(120);
  const after = take.framing.approval;   // the card folds away once answered
  await pointer.glide([after.x + after.width * 0.55, Math.min(VIEW.height - 260, after.y + after.height + 90)], 450);

  // 4. The equation lands; the reply finishes; the result holds.
  await take.until('landed', 30000);
  await take.until('replyDone', 30000);
  const inserted = page.locator('.blockRow', { has: page.locator('.blockRendered .katex-display') }).last();
  await take.frame('inserted', inserted);
  await take.frame('reply', page.locator('.chatBubbleRow.ai').last());
  await sleep(2200);
  take.mark('end');
  await shot(page, '4-end');
  if (!INSPECT) await take.stop();
  await page.evaluate(() => document.getElementById('__keepalive')?.remove());
  await page.screenshot({ path: path.join(DIR, INSPECT ? 'inspect-final.png' : 'final.png') });
  await take.seen();

  // ---- what the clip claims, checked through the API -------------------
  const m = take.marks;
  take.check('slide', slideSize.join() === `${SLIDE.width},${SLIDE.height}`, slideSize);
  take.check('uiScale', +uiScale === 1.3, uiScale);
  take.check('cursorChip', chipText === CAPTION, chipText);
  take.check('typed', typedValue === ASK, typedValue);
  const req = requests[0];
  take.check('request', requests.length === 1 && req.prompt === ASK && req.page_id === note.id && req.focus_block_id === captionId
    && req.images === 1 && req.agent_scope === 'page', requests);
  const log = (await model.log()).filter((e) => e.stream);
  const asked = log.find((e) => Array.isArray(e.decided) && e.decided.includes('create_block'));
  take.check('modelSaw', !!asked && asked.images === 1 && asked.pageId === note.id && asked.cursor?.id === captionId, log);
  // The resting pointer covers neither the chip nor the composer while the
  // request types, nor the card while it is read.
  const inside = ([x, y], b, pad = 4) => x >= b.x - pad && x <= b.x + b.width + pad && y >= b.y - pad && y <= b.y + b.height + pad;
  take.check('pointerClear', !inside(rest, take.framing.chip) && !inside(rest, take.framing.composer) && !inside(rest, take.framing.approval),
    { rest, chip: take.framing.chip, composer: take.framing.composer, approval: take.framing.approval });
  take.check('card', /Edit note blocks/.test(cardText) && cardText.includes('\\Omega') && cardText.includes('\\sin'), cardText.slice(0, 400));
  let saved = null;
  for (let i = 0; i < 40; i++) {
    saved = await account.api(`/api/chats/${note.id}`).catch(() => null);
    if ((saved?.messages || []).some((x) => x.role === 'ai' && x.text)) break;
    await sleep(250);
  }
  const ai = (saved?.messages || []).filter((x) => x.role === 'ai').at(-1);
  const actions = (ai?.actions || []).map((a) => ({ tool: a.tool, kind: a.kind, error: a.error || '', block_id: a.block_id, args: a.args }));
  const created = actions.find((a) => (a.tool === 'create_block' || a.kind === 'create') && !a.error);
  take.check('savedChat', !!created && /^Added the generalized Rabi formula/.test(ai.text || ''), { actions, reply: ai?.text });
  const kids = (await account.api(`/api/blocks/${note.id}/subtree`)).block.children || [];
  const at = kids.findIndex((b) => b.id === captionId);
  take.check('insertedAfterCaption', kids.length === 4 && at === 2 && kids[3].content === LATEX && !(kids[2].children || []).length
    && (!created?.block_id || created.block_id === kids[3].id), kids.map((b) => b.content));
  take.check('marks', m.pasted > m.paste && m.asked >= m.sent && m.card > m.sent && m.allowed > m.card && m.landed > m.allowed
    && m.replyDone >= m.landed && m.end - m.replyDone >= 1.5, m);
  await page.reload();
  await page.locator('.blockRow', { hasText: CAPTION }).first().waitFor({ timeout: 30000 });
  await page.waitForFunction(() => document.querySelectorAll('.blockRendered .katex-display').length >= 2, null, { timeout: 15000 }).catch(() => {});
  const reload = await page.evaluate(() => ({ katex: document.querySelectorAll('.blockRendered .katex-display').length,
    errors: document.querySelectorAll('.katex-error').length }));
  take.check('reloadKatex', reload.katex === 2 && !reload.errors, reload);

  if (INSPECT) console.log(JSON.stringify({ marks: take.marks, framing: take.framing, checks: take.checks, details: take.details }, null, 1));
  else await take.finish({ page: note.id, latex: LATEX });
} catch (error) {
  await take.fail(error);
} finally {
  if (browser) await browser.close().catch(() => {});
  model?.close();
  await server.stop();
}
