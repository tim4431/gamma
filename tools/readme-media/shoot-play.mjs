// The Google Play phone screenshots (android/STORE.md "Graphics"): Gamma for
// Android itself on a phone, This device's own library, captured whole as
// Play shows them (the status bar, the app's bar, the page).
//   1 a paper open on its figure with a sentence highlighted; 2 its notes,
//   the highlights as blocks with a comment and a nested note; 3 the pen on
//   a page: a ring, an arrow and a held straight line; 4 the library with
//   folders, labels and recently viewed; 5 a search through every PDF's text;
//   6 the paper's chat.
// Needs the debug app (WebView debugging is on only in a debug build, so
// build with -PversionName=<release> to keep "-dev" out of sight) on a
// running 1080 x 1920 emulator, Pixel 2 at 420 dpi: adb on PATH, and
// ANDROID_SERIAL when more than one device is attached. The papers are
// arXiv preprints under CC BY 4.0 (PAPERS: each one's license as its arXiv
// record gives it), downloaded once into the scratch folder and added to
// This device through its API over `adb forward`; highlights are made with a
// drag on the page, as a reader makes them. The chat's exchange is saved, not
// generated: the AI connection its composer names is a stand-in on this
// host (adb reverse), which lists its one model, as the app's connection
// check asks, and refuses anything else. Saved as RGB PNGs in
// android/store-screenshots/,
// since Play refuses alpha. `--inspect` keeps the raw captures in the scratch
// folder without writing android/store-screenshots/.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { createRequire } from 'node:module';
import { ROOT, phraseBox } from './runtime.mjs';
import { Account } from '../../frontend/tests/e2e/harness.mjs';

const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { _android } = require('playwright');

const SCRATCH = path.resolve(process.env.MEDIA_SCRATCH || path.join(ROOT, 'artifacts/readme-media/play'));
const OUT = path.join(ROOT, 'android/store-screenshots');
const INSPECT = process.argv.includes('--inspect');
const PYTHON = path.join(ROOT, process.platform === 'win32' ? 'backend/venv/Scripts/python.exe' : 'backend/venv/bin/python');
const PKG = 'com.gammapdf.gamma';
// Host ports for `adb forward` and `adb reverse`, clear of the developer's
// 9001, 5173 and 9002.
const DEVTOOLS = 9123, SERVER = 9150, MODEL = 9160;
const WORKSPACE = 'Research';
// The connection the chat's composer names: the stand-in below, which the
// device reaches at its own 127.0.0.1 through `adb reverse`.
const AI = { protocol: 'openai', name: 'My AI service', api_key: 'placeholder', base_url: `http://127.0.0.1:${MODEL}/v1`, models: 'Assistant' };

// The library: arXiv preprints whose arXiv record gives CC BY 4.0
// (creativecommons.org/licenses/by/4.0), so a store listing may show their
// pages. The first is the one the shots read. Its highlights run from a
// phrase in one line of the text layer (`start`: the span's text, the phrase
// in it) to one in another (`end`), and each ends a full line, so its note
// marker, drawn at the end of its last line, sits in the margin rather than
// on the text; each comment and note is true to the paper. Plain words end them: a drag
// selects by words and stops short of math, which the text layer splits
// into one span a character.
const PAPERS = [
  { arxiv: '2408.13687', version: 1, title: 'Quantum error correction below the surface code threshold',
    folder: 'Quantum/Error correction', labels: ['quantum computing', 'error correction'],
    highlights: [
      { page: 1, start: ['In this work, we present', 'In this work'], end: ['real-time decoder.', 'decoder.'],
        comment: 'Distance 7 on the 105-qubit chip; distance 5 on a 72-qubit one, decoded in real time.',
        child: 'Error suppression factor: $\\Lambda = \\varepsilon_d/\\varepsilon_{d+2} \\approx p_{\\mathrm{thr}}/p$ (p. 1)' },
      { page: 2, start: ['code size to finite', 'finite'], end: ['parasitic couplings between qubits.', 'qubits.'],
        comment: 'Detection probability grows with code size: 7.7%, 8.5% and 8.7% for d = 3, 5 and 7.' },
    ],
    // In the abstract: a phrase circled with the pen, and a held straight
    // line under what it is measured against, on the line below.
    ink: { page: 1, ring: 'beyond break-even', line: 'best physical qubit',
      caption: 'Beyond break-even: it outlives its best physical qubit by a factor of 2.4 ± 0.3.' } },
  { arxiv: '2506.13632', version: 2, title: 'High-fidelity entanglement and coherent multi-qubit mapping in an atom array',
    folder: 'Quantum/Neutral atoms', labels: ['quantum computing', 'neutral atoms'] },
  { arxiv: '2404.19756', version: 5, title: 'KAN: Kolmogorov-Arnold Networks',
    folder: 'Machine learning', labels: ['machine learning'] },
];
const [PAPER] = PAPERS;
// In the running text of both quantum papers, and in no title, folder or
// label.
const SEARCH = 'coherence';
// The paper's chat: a question and an answer written from the paper's own
// text (its Eq. 1, the abstract and Fig. 1d), saved as the conversation.
const CHAT = {
  paper: PAPER,
  question: 'Explain briefly what Λ means here',
  answer: [
    '**Λ is the error suppression factor**: how much the logical error per cycle drops each time the code distance grows by two.',
    '',
    '$$\\Lambda = \\frac{\\varepsilon_d}{\\varepsilon_{d+2}} \\approx \\frac{p_{\\mathrm{thr}}}{p}$$',
    '',
    '1. $\\Lambda > 1$ is the below-threshold regime (roughly $p < p_{\\mathrm{thr}}$): each increase in distance lowers the logical error, so adding qubits helps (p. 1).',
    '2. Here $\\Lambda = 2.14 \\pm 0.02$, and the distance-7 code reaches **0.143% error per cycle** (p. 1).',
    '3. Simulations up to $d = 11$ give $\\Lambda_{\\mathrm{sim}} = 2.25 \\pm 0.02$ (Fig. 1d, inset).',
  ].join('\n'),
};

fs.mkdirSync(SCRATCH, { recursive: true });
const wait = (ms) => new Promise(r => setTimeout(r, ms));
const adb = (...args) => execFileSync('adb', args, { maxBuffer: 64 << 20 });
const adbText = (...args) => adb(...args).toString().trim();
const sysui = (command, extras = {}) => adb('shell', 'am', 'broadcast', '-a', 'com.android.systemui.demo', '-e', 'command', command,
  ...Object.entries(extras).flatMap(([k, v]) => ['-e', k, String(v)]));

// The phone as a store shows it: light, no animations to wait out, and
// System UI's demo mode for a clean status bar (a set time, full battery
// and Wi-Fi, no notifications).
function prepareDevice() {
  const size = adbText('shell', 'wm', 'size');
  if (!size.includes('1080x1920')) throw new Error(`The emulator must be 1080 x 1920 (${size})`);
  adb('shell', 'cmd', 'uimode', 'night', 'no');
  for (const s of ['animator_duration_scale', 'transition_animation_scale', 'window_animation_scale']) adb('shell', 'settings', 'put', 'global', s, '0');
  adb('shell', 'settings', 'put', 'global', 'sysui_demo_allowed', '1');
  sysui('enter');
  sysui('clock', { hhmm: '0941' });
  sysui('battery', { level: 100, plugged: 'false', powersave: 'false' });
  sysui('network', { wifi: 'show', level: 4, fully: 'true' });
  sysui('network', { mobile: 'hide' });
  sysui('network', { airplane: 'hide' });
  sysui('notifications', { visible: 'false' });
  sysui('status', { volume: 'hide', bluetooth: 'hide', location: 'hide', alarm: 'hide', sync: 'hide', mute: 'hide', speakerphone: 'hide' });
}

// The app's WebViews behind its one DevTools socket. Playwright's Android
// page() waits on a page that never loaded, which the web app's WebView is
// until a server opens, so the launcher's Open goes through plain CDP first.
async function targets() {
  const pid = adbText('shell', 'pidof', PKG);
  if (!pid) return [];
  adb('forward', `tcp:${DEVTOOLS}`, `localabstract:webview_devtools_remote_${pid}`);
  try { return await (await fetch(`http://127.0.0.1:${DEVTOOLS}/json`)).json(); } catch { return []; }
}
function evaluateIn(target, expression) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    ws.addEventListener('open', () => ws.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: { expression, awaitPromise: true, returnByValue: true } })));
    ws.addEventListener('message', (e) => { const m = JSON.parse(e.data); if (m.id === 1) { ws.close(); resolve(m.result?.result?.value); } });
    ws.addEventListener('error', reject);
  });
}
const appUrl = (u) => /^http:\/\/127\.0\.0\.1:\d+\//.test(u);
async function openThisDevice() {
  adb('shell', 'am', 'start', '-n', `${PKG}/.app.MainActivity`);
  for (let i = 0; ; i++) {
    const list = await targets();
    if (list.some(t => t.type === 'page' && appUrl(t.url))) return;
    const launcher = list.find(t => t.url.includes('/chrome/launcher.html'));
    if (launcher && i % 10 === 0) await evaluateIn(launcher, "gammaShell.open('local'), 1").catch(() => {});
    if (i === 600) throw new Error('This device never opened');
    await wait(1000);
  }
}

async function attach() {
  const [device] = await _android.devices({ omitDriverInstall: true });
  const webView = await device.webView({ pkg: PKG }, { timeout: 30000 });
  const ctx = (await webView.page()).context();
  const find = async (test) => {
    for (let i = 0; i < 240; i++, await wait(500)) { const p = ctx.pages().find(p => test(p.url())); if (p) return p; }
    throw new Error(`No page: ${ctx.pages().map(p => p.url()).join(', ')}`);
  };
  const bar = await find(u => u.includes('/chrome/bar.html'));
  let state;
  for (let i = 0; !(state = await bar.evaluate(() => gammaShell.state())).servers?.[0]?.running; i++) {
    if (i === 600) throw new Error('This device is not running');
    await wait(1000);
  }
  const app = await find(appUrl);
  return { ctx, bar, app, local: state.servers[0] };
}

// The papers' PDFs, fetched once from arXiv into the scratch folder.
async function pdfOf(paper) {
  const file = path.join(SCRATCH, 'papers', `${paper.arxiv}v${paper.version}.pdf`);
  if (!fs.existsSync(file)) {
    const r = await fetch(`https://arxiv.org/pdf/${paper.arxiv}v${paper.version}`);
    if (!r.ok) throw new Error(`arXiv ${paper.arxiv}: ${r.status}`);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, Buffer.from(await r.arrayBuffer()));
  }
  return fs.readFileSync(file);
}

// This device's library as the shots want it: the workspace named, no tour
// offer and no notice dot, the seeded Welcome page and Sketchbook gone, each
// paper added and filed, and the chat's exchange saved. Run again, it keeps
// what is there.
async function seed(account) {
  await account.api('/api/prefs/profile', { method: 'PATCH', body: { set: { suggestTours: false } } });
  await account.api(`/api/workspaces/${account.ws}`, { method: 'PUT', body: { name: WORKSPACE } });
  for (const n of (await account.api('/api/notices')).notices || []) {
    await account.api(`/api/notices/${encodeURIComponent(n.id)}/seen`, { method: 'POST', body: { fingerprint: n.fingerprint } });
  }
  const entry = ((await account.api('/api/ai/settings')).providers || []).find(p => p.name === AI.name);
  if (!entry) await account.api('/api/ai/providers', { method: 'POST', body: AI });
  else if (entry.base_url !== AI.base_url || entry.models !== AI.models) await account.api(`/api/ai/providers/${entry.id}`, { method: 'PUT', body: { ...AI, api_key: '' } });
  const titles = new Set(PAPERS.map(p => p.title));
  for (const page of (await account.api('/api/blocks/root/children')).children) {
    if (!titles.has(page.content)) await account.api(`/api/blocks/${page.id}`, { method: 'DELETE' });
  }
  for (const paper of PAPERS) {
    const up = await account.upload('/api/uploads', await pdfOf(paper), `${paper.arxiv}.pdf`, 'application/pdf');
    let page = await account.api(`/api/blocks/by-doc/${up.doc_id}`).catch(() => null);
    page = page?.id ? page : await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: 'POST',
      body: { default_title: paper.title, source_url: up.source_url } });
    paper.id = page.id; paper.doc = up.doc_id;
    await account.file(page.id, { folders: [paper.folder], labels: paper.labels });
  }
  await account.api(`/api/chats/${CHAT.paper.id}`, { method: 'PUT', body: { messages: [
    { role: 'user', text: CHAT.question, pdfs: [CHAT.paper.title], pdfDocs: [CHAT.paper.doc] },
    { role: 'ai', text: CHAT.answer },
  ] } });
  // Every PDF indexed, for the search.
  for (let i = 0; ; i++) {
    const s = await account.api('/api/document-search', { method: 'POST', body: { q: SEARCH, limit: 1 } });
    if (!s.indexing) break;
    if (i === 300) throw new Error('The PDFs were never indexed');
    await wait(2000);
  }
}

const notesOf = async (account, paper) => (await account.api(`/api/blocks/${paper.id}/subtree`)).block.children || [];

// A passage highlighted with a drag over the page's text (a double press
// selects whole words), as a reader makes it, then its comment and any note
// under it through the API: on a phone the colour's tap leaves the comment
// to the notes.
const spanOf = (app, page, text) => app.locator(`[data-page="${page}"] .textLayer span`).filter({ hasText: text }).first();
const quoteOf = (b) => b.properties?.quote?.replace(/\s+/g, ' ').trim();
// A quote's end without its closing stop: a drag by words may leave it out.
const bare = (s) => s.replace(/[.,;:]+$/, '');
const made = (h) => (b) => !!quoteOf(b)?.startsWith(h.start[1]) && bare(quoteOf(b)).endsWith(bare(h.end[1]));
async function highlight(app, account, paper, h) {
  const { page, start, end, comment, child } = h, first = start[1], ofIt = made(h);
  let block = (await notesOf(account, paper)).find(ofIt);
  if (!block) {
    await app.evaluate((n) => document.querySelector(`[data-page="${n}"]`)?.scrollIntoView({ block: 'center' }), page);
    await spanOf(app, page, start[0]).waitFor({ timeout: 60000 });
    await spanOf(app, page, start[0]).scrollIntoViewIfNeeded();
    const a = await phraseBox(spanOf(app, page, start[0]), start[1]);
    const b = await phraseBox(spanOf(app, page, end[0]), end[1]);
    await app.mouse.move(a.x + 2, a.y + a.height / 2);
    await app.mouse.down({ clickCount: 2 });
    // Inside the last word, which a drag by words takes whole: at a line's
    // very end the text layer runs the selection on into the lines below.
    await app.mouse.move(b.x + b.width * 0.6, b.y + b.height / 2, { steps: 16 });
    await app.mouse.up();
    await app.mouse.move(b.x + b.width * 0.6 + 4, b.y + b.height / 2 + 8, { steps: 2 }); // the popup opens as the pointer moves on
    const selected = (await app.evaluate(() => getSelection()?.toString()))?.replace(/\s+/g, ' ').trim();
    if (!selected?.startsWith(first) || !bare(selected).endsWith(bare(end[1]))) throw new Error(`The drag selected "${selected}"`);
    await app.locator('.plainTip .colorBtn').first().click();
    for (let i = 0; !(block = (await notesOf(account, paper)).find(ofIt)); i++) {
      if (i === 20) throw new Error(`The highlight from "${first}" was not saved`);
      await wait(500);
    }
    await app.evaluate(() => { document.activeElement?.blur(); getSelection()?.removeAllRanges(); });
  }
  if (block.content !== comment) await account.api(`/api/blocks/${block.id}`, { method: 'PUT', body: { content: comment } });
  if (child && !(block.children || []).some(c => c.content === child)) {
    await account.api(`/api/pages/${paper.id}/ops`, { method: 'POST',
      body: { ops: [{ op: 'insert', id: `play${Math.random().toString(36).slice(2, 10)}`, parent: block.id, content: child }] } });
  }
}

// A capture of the whole screen: the status bar, the app's bar and the
// page, as Play shows it. Nothing focused (no keyboard), nothing hovered,
// no surface still on its way out.
async function capture(app, shots, name) {
  await app.evaluate(() => { document.activeElement?.blur?.(); getSelection()?.removeAllRanges(); });
  await app.mouse.move(2, 2);
  for (let i = 0; i < 40 && await app.locator('[data-leaving]').count(); i++) await wait(100);
  await wait(1500);
  const raw = path.join(SCRATCH, `${name}.png`);
  fs.writeFileSync(raw, adb('exec-out', 'screencap', '-p'));
  shots.push([raw, name]);
  console.log('Shot', name);
}
const tab = (app, label) => app.locator(`.phoneBottomBar [aria-label="${label}"]`).first().click();
const scrollToTop = (app, selector) => app.evaluate((sel) => {
  let el = document.querySelector(sel);
  while (el && !(el.scrollHeight > el.clientHeight + 1 && /auto|scroll/.test(getComputedStyle(el).overflowY))) el = el.parentElement;
  if (el) el.scrollTop = 0;
}, selector);

// A stroke of the armed pen through `points` (CSS px), held still at its end
// for `hold` ms (a held stroke snaps to its shape). It is drawn with the
// mouse, which the pen tool takes as on a desktop: CDP's pen events never
// reach the app's native stylus layer.
async function stroke(app, points, ms, hold = 0) {
  await app.mouse.move(...points[0]);
  await app.mouse.down();
  const n = Math.ceil(ms / 16);
  for (let i = 1; i <= n; i++) {
    const u = i / n, f = u * (points.length - 1), j = Math.min(points.length - 2, Math.floor(f)), r = f - j;
    await app.mouse.move(...[0, 1].map(k => points[j][k] + (points[j + 1][k] - points[j][k]) * r));
    await wait(16);
  }
  if (hold) await wait(hold);
  await app.mouse.up();
  await wait(500);
}
const phraseIn = async (app, page, text) => {
  const span = app.locator(`[data-page="${page}"] .textLayer span`).filter({ hasText: text }).first();
  await span.waitFor({ timeout: 30000 });
  return phraseBox(span, text);
};

const model = http.createServer((req, res) => {
  const list = req.method === 'GET' && req.url.endsWith('/models');
  res.writeHead(list ? 200 : 503, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(list ? { object: 'list', data: [{ id: 'assistant', object: 'model', owned_by: 'local' }] }
    : { error: { message: 'A stand-in for the screenshots: it only lists its model.' } }));
}).listen(MODEL, '127.0.0.1');
prepareDevice();
await openThisDevice();
adb('reverse', `tcp:${MODEL}`, `tcp:${MODEL}`);
const { app, local } = await attach();
app.on('pageerror', e => console.log('Page error:', e.message));
const origin = new URL(local.url).origin;
adb('forward', `tcp:${SERVER}`, `tcp:${new URL(origin).port}`);
const account = await new Account({ base: `http://127.0.0.1:${SERVER}` }, local.adminUser, local.adminPassword).login();
await seed(account);
const open = async (paper) => {
  await app.goto(`${origin}/?page=${paper.id}&ws=${account.ws}`);
  await app.waitForSelector('[data-page="1"] .textLayer span', { timeout: 120000 });
  await wait(4000); // its thumbnail for the library is taken from the open page
};
// No tab left from an earlier run; the other papers first, so the tabs
// show a reading session.
for (let i = 0; i < 20 && await app.locator('.tabStrip .tab .tabClose').count(); i++) {
  await app.evaluate(() => document.querySelector('.tabStrip .tab .tabClose')?.click());
  await wait(400);
}
for (const paper of [...PAPERS].reverse()) await open(paper);
await app.keyboard.press('Escape');
// The notes list highlights in the order they were made, so unless every
// one is there they are all made again, in the page's order, and the page
// is read again first: a drag that starts on a highlight's mark opens it
// instead of selecting text.
// The pen tool left armed would draw the drags below instead.
if (await app.locator('.pdfInkBar').count()) await app.keyboard.press('Escape');
const blocks = await notesOf(account, PAPER);
if (!PAPER.highlights.every(h => blocks.some(made(h)))) {
  for (const b of blocks) if (quoteOf(b)) await account.api(`/api/blocks/${b.id}`, { method: 'DELETE' });
  await open(PAPER);
  await app.keyboard.press('Escape');
}
for (const h of PAPER.highlights) await highlight(app, account, PAPER, h);

const shots = [];
// Taken first, though third in the store's order: its marks are made
// here, and the notes shot shows their card after the highlights.
// 3 — the pen on the abstract, zoomed in: a ring and a held straight line.
// The marks are drawn again each time, after the highlights, and the page
// read again without the old ones first: an open page saves the strokes it
// holds back with the next one.
for (const b of await notesOf(account, PAPER)) {
  if (b.properties?.ink_url) await account.api(`/api/blocks/${b.id}`, { method: 'DELETE' });
}
await open(PAPER);
await app.locator('[aria-label="Fit to width"]').first().click();
await wait(800);
await app.evaluate(() => document.querySelector('[data-page="1"]').scrollIntoView({ block: 'start' }));
// One step in keeps the abstract's whole width on the screen, its left edge
// just right of the viewer's tool rail.
await app.locator('[aria-label="Zoom in"]').first().click();
await wait(800);
const line = await app.locator(`[data-page="${PAPER.ink.page}"] .textLayer span`).filter({ hasText: PAPER.ink.ring }).first().boundingBox();
await app.mouse.move(206, 400);
const rail = await app.locator('[aria-label="Zoom in"]').first().boundingBox();
await app.mouse.wheel(line.x - (rail.x + rail.width + 10), line.y - 330);
await wait(1200);
await app.locator('[aria-label="Handwriting tools"]').first().click();
await app.locator('.pdfInkBar button[aria-label^="Blue pen"]').first().click();
const r = await phraseIn(app, PAPER.ink.page, PAPER.ink.ring);
const cx = r.x + r.width / 2, cy = r.y + r.height / 2, rx = r.width / 2 + 7, ry = r.height / 2 + 1.5;
await stroke(app, Array.from({ length: 48 }, (_, i) => {
  const a = -2.4 + i / 47 * Math.PI * 2.1;
  return [cx + Math.cos(a) * rx * (1 + 0.04 * Math.sin(3 * a)), cy + Math.sin(a) * ry];
}), 900);
const l = await phraseIn(app, PAPER.ink.page, PAPER.ink.line);
const y = l.y + l.height * 0.9;
await stroke(app, [[l.x + 2, y], [l.x + l.width * 0.5, y + 1.5], [l.x + l.width - 2, y]], 500, 800);
// The snapped line's "Straightened." pill goes by itself. The marks' card
// in the notes gets a caption, as a reader writes one.
for (let i = 0; i < 100 && await app.getByText('Straightened.').count(); i++) await wait(100);
for (let i = 0; i < 20; i++, await wait(500)) {
  const card = (await notesOf(account, PAPER)).find(b => b.properties?.ink_url);
  if (card) { await account.api(`/api/blocks/${card.id}`, { method: 'PUT', body: { content: PAPER.ink.caption } }); break; }
}
await capture(app, shots, '03-pen');
await app.keyboard.press('Escape');

// 1 — the paper on Fig. 1, a passage under it highlighted; one step out
// from the page's width keeps the text clear of the viewer's tool rail.
await app.locator('[aria-label="Fit to width"]').first().click();
await wait(800);
await app.locator('[aria-label="Zoom out"]').first().click();
await wait(800);
await app.evaluate(() => document.querySelector('[data-page="2"]').scrollIntoView({ block: 'start' }));
await app.locator('[data-page="2"] .textLayer span').first().waitFor();
await capture(app, shots, '01-read');

// 2 — its notes: the highlights as blocks, a comment on each, a note under one.
await tab(app, 'Notes');
await app.locator('.phonePanel .blockRow').first().waitFor({ timeout: 20000 });
// No block chosen (the last one made is), and the list from its top.
await app.evaluate(() => document.activeElement?.blur?.());
await app.keyboard.press('Escape');
await wait(800);
await scrollToTop(app, '.phonePanel .blockRow');
await capture(app, shots, '02-notes');

// 4 — the paper's chat, from its first line.
await tab(app, 'AI chat');
await app.locator('.chatBubbleRow.ai').first().waitFor({ timeout: 20000 });
await app.click('.chatSelChip.isCursor .chatSelChipClose', { timeout: 2000 }).catch(() => {});
await scrollToTop(app, '.chatBubbleRow');
await capture(app, shots, '04-chat');

// 5 — the library: its folders and the papers filed in them, with their
// labels, from the list's head.
await tab(app, 'Library');
await app.locator('.fileList .fileListHead').waitFor({ timeout: 20000 });
await app.evaluate(() => {
  const head = document.querySelector('.fileList .fileListHead');
  let el = head.parentElement;
  while (el && !(el.scrollHeight > el.clientHeight + 1 && /auto|scroll/.test(getComputedStyle(el).overflowY))) el = el.parentElement;
  el.scrollTop += head.getBoundingClientRect().top - el.getBoundingClientRect().top - 8;
});
await wait(1500);
await capture(app, shots, '05-library');

// 6 — one search through every PDF's text, from the library's top.
await scrollToTop(app, '.fileList .fileListHead');
await wait(800);
await tab(app, 'Search');
await app.locator('.searchInput').first().waitFor();
await app.keyboard.type(SEARCH, { delay: 40 });
await app.locator('.searchSection', { hasText: 'Library PDFs' }).first().waitFor({ timeout: 30000 });
await wait(1500);
await capture(app, shots, '06-search');

sysui('exit');
adb('forward', '--remove', `tcp:${SERVER}`);
adb('reverse', '--remove', `tcp:${MODEL}`);
model.close();
if (!INSPECT) {
  fs.mkdirSync(OUT, { recursive: true });
  for (const [raw, name] of shots) {
    execFileSync(PYTHON, ['-I', '-c', 'import sys; from PIL import Image; Image.open(sys.argv[1]).convert("RGB").save(sys.argv[2], optimize=True)',
      raw, path.join(OUT, `${name}.png`)]);
  }
  console.log(`Saved ${shots.length} screenshots in ${path.relative(ROOT, OUT)}`);
}
process.exit(0);
