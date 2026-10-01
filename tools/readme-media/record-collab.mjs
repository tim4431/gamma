// README "Share and work together" demo: one page of a shared workspace, seen
// by its owner. Maya and the owner type into the same block at once (both
// lines kept), and Sam types a caption into another block and pastes a
// figure from the paper under it.
//
// Runs on an isolated server with the curated export in a shared workspace
// ("Quantum lab": Alex owns it, Maya and Sam edit). Only Alex's window is
// captured; Maya and Sam are real sessions in a second browser, so their
// carets, row chips and avatars are what Gamma itself draws. After the
// recording the server tree is checked for both lines and the picture.
// Writes retina frames to frames/ and collab_marks.json (capture seconds).
// `--inspect` saves a screenshot of the prepared page instead.
import fs from 'node:fs';
import path from 'node:path';
import { chromium, ROOT, RETINA, launchRetina, startCapture, addCursor, pointer } from './runtime.mjs';
import { Server, Account } from '../../frontend/tests/e2e/harness.mjs';

const SCRATCH = path.resolve(process.env.MEDIA_SCRATCH || path.join(ROOT, 'artifacts/readme-media/collab'));
const INSPECT = process.argv.includes('--inspect');
const FIGURE = fs.readFileSync(path.join(SCRATCH, 'fig1cd.png')).toString('base64');
const PAPER = 'A quantum processor based on coherent transport of entangled atom arrays';
const QUESTIONS = 'Questions for Thursday:';
const CAPTION = "Fig. 1c, d: the parity fringes don't care whether the atoms move";
const MINE = 'Why does the fidelity drop above 0.55 µm/µs?';
const MAYAS = 'Is atom loss the only error during the move?';
fs.mkdirSync(SCRATCH, { recursive: true });

const server = new Server();
let recorder, peers, capture;
try {
  await server.start();
  const password = 'isolated-media-only';
  for (const name of ['Alex', 'Maya', 'Sam']) server.manage('create-user', name, password);
  const out = server.manage('create-workspace', 'Quantum lab', 'Alex', 'shared');
  const team = (out.match(/workspace (\S+)/) || [])[1];
  if (!team) throw new Error(`No workspace id in: ${out}`);
  server.manage('set-member', team, 'Maya', 'editor');
  server.manage('set-member', team, 'Sam', 'editor');
  const [alex, maya, sam] = await Promise.all(['Alex', 'Maya', 'Sam'].map(n => new Account(server, n, password).login()));
  for (const a of [alex, maya, sam]) a.ws = team;
  await alex.upload('/api/import-data', fs.readFileSync(path.join(ROOT, 'artifacts/readme-media/demo.zip')), 'demo.zip', 'application/zip');
  await alex.api('/api/blocks/fy0-h_BqOHcH', { method: 'PUT', body: { properties: { folder: 'Papers' } } });
  const note = await alex.api('/api/pages', { method: 'POST', body: { title: 'Reading group: atom arrays' } });
  await alex.api('/api/blocks/' + note.id, { method: 'PUT', body: { properties: { folder: 'Papers' } } });
  await alex.api(`/api/blocks/${note.id}/children`, { method: 'PUT', body: { blocks: [
    { content: `Paper: [[${PAPER}]]`, children: [] },
    { content: 'Takeaway: two-atom entanglement survives a 110 µm move, and ==coherence is unaffected by transport==.', children: [] },
    { content: QUESTIONS, children: [] },
    // The block both type into: two empty items, Maya's line first, so the
    // name over Maya's caret sits above the block, not over Alex's line.
    { content: '* \n* ', children: [] },
    // Sam's: empty until Sam types the caption and pastes the figure.
    { content: '', children: [] },
  ] } });
  const blocks = (await alex.api(`/api/blocks/${note.id}/subtree`)).block.children;
  const listId = blocks[3].id, figureId = blocks[4].id;
  const url = `${server.base}/?page=${note.id}&ws=${team}`;
  const settle = async (page) => {
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.locator('.blockRow', { hasText: QUESTIONS }).first().waitFor({ timeout: 30000 });
    if (await page.locator('[aria-label="Close Chat"]').count()) await page.click('[aria-label="Close Chat"]');
  };

  // Maya and Sam: plain sessions in a browser of their own (Sam's clipboard is its own).
  peers = await chromium.launch({ headless: true });
  const open = async (account) => {
    const ctx = await account.context(peers, { locale: 'en-US' });
    await ctx.addInitScript(() => { try { localStorage.setItem('gamma-suggest-tours', '0'); } catch {} });
    await ctx.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: server.base });
    const page = await ctx.newPage();
    await settle(page);
    return page;
  };
  const M = await open(maya);
  const S = await open(sam);

  recorder = await launchRetina();
  const ctx = await alex.context(recorder, RETINA);
  // The block text is the story: a 130% interface size (Settings → Appearance).
  await ctx.addInitScript(() => { try { localStorage.setItem('gamma-ui-scale', '1.3'); } catch {} });
  await addCursor(ctx);
  const A = await ctx.newPage();
  A.on('pageerror', e => console.log('Page error:', e.message));
  await settle(A);
  await A.locator('.presenceBar .peerAvatar').nth(1).waitFor({ timeout: 15000 });
  const { glide, glideTo } = pointer(A, 980, 760);
  await A.mouse.move(980, 760);
  await A.waitForTimeout(1200);
  if (INSPECT) {
    await A.screenshot({ path: path.join(SCRATCH, 'collab-inspect.png') });
    console.log('Saved', path.join(SCRATCH, 'collab-inspect.png'));
  } else {
    const box = (sel, text) => A.locator(sel, text ? { hasText: text } : {}).first().boundingBox();
    const framing = { presence: await box('.presenceBar') };
    capture = await startCapture(A, path.join(SCRATCH, 'frames'));
    const clock = capture.clock;
    const marks = {};
    const mark = (name) => { marks[name] = clock(); console.log(name, marks[name].toFixed(2)); };
    const hold = (ms) => A.waitForTimeout(ms);
    // Human cadence: 55–95 ms a key, a little longer after a word, and a
    // pause every two words. Edits are sent once the typist pauses
    // (collabSession's typing debounce), so the pauses are what let the
    // others see the text arrive while it is being written.
    const typeLike = async (page, text, seed) => {
      let r = seed, words = 0;
      const rand = () => (r = (r * 9301 + 49297) % 233280) / 233280;
      for (const ch of text) {
        await page.keyboard.type(ch);
        let wait = 55 + rand() * 40;
        if (ch === ' ') wait += ++words % 2 ? 60 : 420 + rand() * 260;
        await page.waitForTimeout(wait);
      }
    };
    const list = A.locator(`[data-block-id="${listId}"] .blockRow`).first();
    mark('start');
    await hold(900);

    // 1. Alex opens the list block, on its second line.
    framing.questions = await glideTo(list, 0.3, 0.75);
    await A.mouse.down(); await A.mouse.up();
    await A.waitForSelector('.blockEditorCm .cm-content');
    await A.keyboard.press('Control+End');
    await glide(1250, 250);
    mark('alexIn');

    // 2. Maya joins the same block, on the first line; both type.
    await M.locator(`[data-block-id="${listId}"] .blockBody`).first().click();
    await M.waitForSelector('.blockEditorCm .cm-content');
    await M.keyboard.press('Control+Home');
    await M.keyboard.press('End');
    await A.locator('.blockEditorCm .cmRemoteCaret').first().waitFor({ timeout: 10000 });
    mark('mayaIn');
    await hold(700);
    const mayaTyping = typeLike(M, MAYAS, 7);
    await hold(500);
    const alexTyping = typeLike(A, MINE, 3);

    // 3. Meanwhile Sam captions the last block and pastes the figure under it.
    const samWork = (async () => {
      await S.waitForTimeout(900);
      await S.evaluate(async (b64) => {
        const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': new Blob([bytes], { type: 'image/png' }) })]);
      }, FIGURE);
      await S.locator(`[data-block-id="${figureId}"] .blockBody`).first().click();
      await S.waitForSelector('.blockEditorCm .cm-content');
      mark('samIn');
      await typeLike(S, CAPTION, 11);
      await S.waitForTimeout(500);
      await S.keyboard.press('Enter');
      await S.keyboard.press('Control+v');
      await A.locator(`[data-block-id="${figureId}"] img`).first().waitFor({ timeout: 20000 });
      mark('picture');
    })();
    await Promise.all([mayaTyping, alexTyping, samWork]);
    mark('typed');
    await hold(900);

    // 4. Everyone leaves the block; the merged text renders.
    await M.evaluate(() => document.activeElement?.blur());
    await S.evaluate(() => document.activeElement?.blur());
    await hold(600);
    await A.evaluate(() => document.activeElement?.blur());
    await A.waitForSelector('.blockEditorCm', { state: 'detached' });
    mark('closed');
    framing.questions = await list.boundingBox();
    framing.figure = await A.locator(`[data-block-id="${figureId}"] .blockRow`).first().boundingBox();
    await hold(2600);
    mark('end');
    const frames = await capture.stop(); capture = null;

    // The server holds both lines in one block and the picture in the other.
    const tree = (await alex.api(`/api/blocks/${note.id}/subtree`)).block.children;
    const q = tree.find(b => b.id === listId)?.content || '';
    const f = tree.find(b => b.id === figureId)?.content || '';
    const verified = { bothLines: q === `* ${MAYAS}\n* ${MINE}`, picture: f.startsWith(`${CAPTION}\n`) &&/!\[[^\]]*\]\(\/api\/uploads\//.test(f) };
    if (!verified.bothLines || !verified.picture) throw new Error(`Not saved as expected: ${JSON.stringify({ q, f })}`);
    await A.screenshot({ path: path.join(SCRATCH, 'collab-final.png') });
    fs.writeFileSync(path.join(SCRATCH, 'collab_marks.json'), JSON.stringify({ frames, marks, framing, verified }, null, 1));
    console.log('Recorded:', JSON.stringify(verified));
  }
} finally {
  if (capture) await capture.stop().catch(() => {});
  if (recorder) await recorder.close();
  if (peers) await peers.close();
  await server.stop();
}
