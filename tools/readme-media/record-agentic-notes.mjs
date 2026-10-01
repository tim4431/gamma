// README AI demo, in the notes: a slide's equation pasted into the chat, and
// the assistant adds it to the note as KaTeX under the block the cursor is on.
// The edit waits on its approval card (Edit note blocks: Ask).
// Requires a disposable curated workspace with AI on its owning account
// (suite-workspace.mjs with MEDIA_SCRATCH set to this shot's directory).
import fs from 'node:fs';
import path from 'node:path';
import { ROOT, RETINA, launchRetina, startCapture, configureContext, addCursor } from './runtime.mjs';
import { Account } from '../../frontend/tests/e2e/harness.mjs';

const scratch = path.resolve(process.env.MEDIA_SCRATCH || path.join(ROOT, 'artifacts/readme-media/agentic-notes'));
const state = JSON.parse(fs.readFileSync(path.join(scratch, 'workspace.json')));
if (state.removed) throw new Error('Recording workspace was removed');
const account = new Account({ base: state.base }, state.username, '');
account.session = fs.readFileSync(path.join(scratch, 'session.txt'), 'utf8').trim();
account.ws = state.workspace;
const slide = fs.readFileSync(path.join(scratch, 'rabi-slide.png')).toString('base64');
const CURSOR = 'Excited-state population, from the lecture slide:';
const ASK = "Add a block under the one I'm on with this equation in LaTeX, as display math.";

// One tab: an earlier run's pages stay in the workspace, and open tabs follow the account.
await account.api('/api/prefs/open-tabs', { method: 'PUT', body: { value: [] } });
// A fresh page: its heading line and the block the equation goes under.
const note = await account.api('/api/pages', { method: 'POST', body: { title: 'Rabi oscillations' } });
await account.api(`/api/blocks/${note.id}/children`, { method: 'PUT', body: { blocks: [
  { content: 'A two-level atom driven at detuning $\\Delta$ with Rabi frequency $\\Omega$.', children: [] },
  { content: CURSOR, children: [] },
] } });

const browser = await launchRetina();
let context, page, capture;
const marks = {}, framing = {}, verified = {}, requests = [];
try {
  context = await account.context(browser, RETINA);
  await configureContext(context);
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: state.base });
  // The note and the chat sit at opposite edges, so the shot stays whole:
  // a 130% interface size (Settings → Appearance) keeps both readable.
  await context.addInitScript(() => { try { localStorage.setItem('gamma-ui-scale', '1.3'); } catch {} });
  await addCursor(context);
  page = await context.newPage();
  page.on('request', r => {
    if (new URL(r.url()).pathname === '/api/ai/chat' && r.method() === 'POST') {
      const body = r.postDataJSON();
      requests.push({ pageId: body.page_id, images: body.images?.length || 0 });
    }
  });
  page.on('pageerror', e => console.log('Page error:', e.message));
  await page.goto(`${state.base}/?page=${note.id}&ws=${account.ws}`);
  const cursorRow = page.locator('.blockRow', { hasText: CURSOR }).first();
  await cursorRow.waitFor({ timeout: 30000 });
  if (!await page.locator('.chatInput').isVisible()) {
    await page.keyboard.press('Control+Shift+p');
    await page.keyboard.type('chat');
    await page.getByRole('dialog', { name: 'Command palette' }).locator('[role="option"][aria-selected="true"]', { hasText: 'chat' }).waitFor();
    await page.keyboard.press('Enter');
  }
  await page.locator('.chatInput').waitFor();
  await page.evaluate(async b64 => {
    const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': new Blob([bytes], { type: 'image/png' }) })]);
  }, slide);
  await page.mouse.move(700, 620);
  await page.waitForTimeout(1200);
  capture = await startCapture(page, path.join(scratch, 'frames'));
  const clock = capture.clock;
  const mark = name => { marks[name] = clock(); console.log(name, marks[name].toFixed(2)); };
  const hold = ms => page.waitForTimeout(ms);
  const glideTo = async (locator, fx = 0.5, fy = 0.5) => {
    const b = await locator.boundingBox();
    await page.mouse.move(b.x + b.width * fx, b.y + b.height * fy, { steps: 25 });
    return b;
  };
  mark('start');
  await hold(900);

  // 1. The cursor goes on the block the equation belongs under.
  framing.note = await glideTo(cursorRow, 0.2, 0.5);
  await page.mouse.down(); await page.mouse.up();
  await page.locator('.blockEditorCm .cm-content').waitFor();
  await page.keyboard.press('End');
  await hold(900); mark('cursor');

  // 2. The slide goes into the chat, with the request.
  await glideTo(page.locator('.chatInput'));
  await page.locator('.chatInput').click();
  await page.keyboard.press('Control+v');
  await page.locator('.chatImgPreview img').waitFor();
  await hold(1000); mark('pasted');
  framing.composer = await page.locator('.chatInput').boundingBox();
  await page.keyboard.type(ASK, { delay: 22 });
  await hold(900);
  await page.keyboard.press('Enter'); mark('sent');

  // 3. The insert waits on its approval card.
  const card = page.locator('.chatApproval:not(.sent)').first();
  await card.waitFor({ timeout: 240000 });
  await hold(400); mark('approval');
  framing.approval = await card.boundingBox();
  framing.chat = await page.locator('.chatPanel').first().boundingBox();
  await hold(2800);
  await glideTo(card.getByRole('button', { name: 'Allow once', exact: true }));
  await hold(300);
  await card.getByRole('button', { name: 'Allow once', exact: true }).click();
  mark('allowed');

  // 4. The block lands in the note, rendered.
  const math = page.locator('.blockRow .katex-display').first();
  await math.waitFor({ timeout: 120000 });
  mark('inserted');
  for (let i = 0; i < 480; i++) {
    if (!await page.locator('.chatStopBtn').count() && !await page.locator('.chatTyping').count()) break;
    await hold(500);
  }
  mark('answered');
  // Rows nest: the innermost row holding the equation is the new block.
  framing.inserted = await page.locator('.blockRow', { has: page.locator('.katex-display') }).last().boundingBox();
  await page.mouse.move(framing.inserted.x + framing.inserted.width * 0.75, framing.inserted.y + framing.inserted.height + 60, { steps: 25 });
  await hold(3200); mark('end');
  framing.frames = await capture.stop(); capture = null;
  await page.screenshot({ path: path.join(scratch, 'agentic-notes-final.png') });

  // The equation is a real block under the cursor's block, and the request had the picture.
  const { block } = await account.api(`/api/blocks/${note.id}/subtree`);
  // "Under" the block: its first child, or the block after it.
  const rows = block.children || [];
  const at = rows.findIndex(b => b.content === CURSOR);
  const added = rows[at]?.children?.[0]?.content || rows[at + 1]?.content || '';
  verified.inserted = added;
  verified.katex = added.includes('$$') && added.includes('\\Omega') && added.includes('\\sin');
  verified.requests = requests;
  if (!verified.katex) throw new Error(`The block under the cursor is not the equation in LaTeX: ${added}`);
  if (!requests.length || requests.some(r => r.pageId !== note.id) || requests[0].images !== 1) throw new Error('The request must carry the pasted picture from this page');
  await page.reload();
  await page.locator('.blockRow .katex-display').first().waitFor({ timeout: 30000 });
  await context.close(); context = null;
  const { frames, ...rest } = framing;
  fs.writeFileSync(path.join(scratch, 'agentic-notes-timeline.json'), JSON.stringify({ frames, page: note.id, marks, framing: rest, verified }, null, 2));
  console.log('Recorded: the pasted equation is a KaTeX block under the cursor, after approval.');
} catch (error) {
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(scratch, 'agentic-notes-failure.png') }).catch(() => {});
  throw error;
} finally {
  if (capture) await capture.stop().catch(() => {});
  if (context) await context.close();
  await browser.close();
}
