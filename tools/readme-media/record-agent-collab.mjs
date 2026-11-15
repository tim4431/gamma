// README demo "Work with an agent on your computer" (agent-collab): lecture
// notes kept as a LaTeX project in a synced folder, commented in Gamma where
// they are read (a source file, the compiled PDF, a figure), each comment
// sent to Codex on this computer, and its answers read in the Inbox.
//
// The story, at a 130% interface size, the folder synced and its three files
// open as tabs (off camera):
//   1. sections/multihead.tex, an empty section: its heading selected,
//      Comment, "Fill this in: heads in parallel, with the equation.", Send to
//      Codex ticked; Codex writes the section, which shows in the source, and
//      rebuilds the notes;
//   2. notes.pdf (its tab), rebuilt: a passage selected, "Divide by √d_k, not
//      d_k, and say why.", the tick remembered; the thread closed, the PDF
//      rebuilt in view, (1) with √d_k and the sentence why;
//   3. figures/attention-map.pdf (its tab): Ctrl+drag a region over the future
//      tokens, "This is the decoder: mask the future tokens."; Codex works
//      through them in turn and the figure turns masked in view (sped up);
//   4. the Inbox holds Codex's three replies;
//   5. the one on multihead.tex asks a question: "Yes, as a footnote." in its
//      thread resumes the session, and Codex adds the footnote, answers and
//      resolves it; the popover closes on the footnote in the source.
// Codex is demo-agent.mjs with the story agents/attention-notes.mjs: Gamma
// starts it, hands it each thread, shows its turn and writes its answers; it
// edits the files and runs `make` for real. Only its words are scripted.
//
//   node tools/readme-media/record-agent-collab.mjs [--inspect]
// (setup: tools/readme-media/README.md; TeX Live's latexmk and pdflatex on
// PATH). Writes artifacts/readme-media/agent-collab/{frames/, final.png,
// timing.json}; --inspect plays the story without a capture and leaves
// inspect-*.png there. render_feature_demos.py agent-collab makes the WebP.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { VIEW, launchRetina } from './runtime.mjs';
import { library, openPage, hand, typeText, Take, scratch, sleep, INSPECT } from './take.mjs';
import { ASK, PLACES, REPLY } from './agents/attention-notes.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const NAME = 'agent-collab';
const DIR = scratch(NAME);
const STORY = 'attention-notes';
const COMPUTER = 'alex-laptop';
const USER = 'alex';
// The desktop app's window (IS_DESKTOP), as the e2e suite's local agents run it.
const DESKTOP_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Gamma/1.0.0 '
  + 'Chrome/138.0.0.0 Electron/38.0.0 Safari/537.36';
// What the synced folder tracks, as Choose files left it: the sources and the
// PDFs, not the build's own directory (latexmk keeps a copy of the PDF there).
const TRACKED = '*\n!*/\n!*.tex\n!*.pdf\n/build/\n';
const FILES = { notes: 'notes.pdf', figure: 'figures/attention-map.pdf', tex: 'sections/multihead.tex' };
const OUT = INSPECT ? scratch(path.join(NAME, 'inspect')) : DIR;
const shot = async (page, name) => { if (INSPECT) await page.screenshot({ path: path.join(OUT, `inspect-${name}.png`) }); };
const sha = (file) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
// The code view's own text: the comment fields in its column are CodeMirror views too.
const CODE = '.docTextView .docTextHost > .cm-editor > .cm-scroller > .cm-content';

/** The notes as they start: the project copied fresh and built off camera,
 *  its files dated a while ago, as an editor that finished earlier left
 *  them, so the first round takes them at once. */
function prepareFolder() {
  const disk = path.join(DIR, 'disk', STORY);
  fs.rmSync(path.dirname(disk), { recursive: true, force: true });
  fs.cpSync(path.join(HERE, 'agents', STORY), disk, { recursive: true });
  execFileSync('make', { cwd: disk, stdio: 'ignore' });
  fs.writeFileSync(path.join(disk, '.gammaignore'), TRACKED);
  const before = new Date(Date.now() - 60_000);
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const at = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(at);
      fs.utimesSync(at, before, before);
    }
  };
  walk(disk);
  return disk;
}

/** The boxes of the first and last characters of `phrase` in the element
 *  `root` (a PDF's text layer, a code view's lines), for a real drag over
 *  it; its words may be broken over lines or spans. */
async function phraseEnds(page, root, phrase) {
  return page.locator(root).first().evaluate((el, phrase) => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const nodes = [];
    let text = '';
    while (walker.nextNode()) { nodes.push([walker.currentNode, text.length]); text += walker.currentNode.textContent; }
    const flat = text.replace(/\s+/g, ' ');
    const map = [];
    for (let i = 0, j = 0; i < text.length; i++) {
      if (/\s/.test(text[i]) && i > 0 && /\s/.test(text[i - 1])) continue;
      map[j++] = i;
    }
    const at = flat.indexOf(phrase);
    if (at < 0) return null;
    const box = (offset) => {
      const index = map[offset];
      const [node, start] = nodes.findLast(([, s]) => s <= index);
      const r = document.createRange();
      r.setStart(node, index - start); r.setEnd(node, index - start + 1);
      const b = r.getBoundingClientRect();
      return { x: b.x, y: b.y, width: b.width, height: b.height };
    };
    return { first: box(at), last: box(at + phrase.length - 1) };
  }, phrase);
}

// What the viewer sees, logged on every animation frame (Take.watch).
const PROBE = () => {
  const code = '.docTextView .docTextHost > .cm-editor > .cm-scroller > .cm-content';   // CODE (the probe runs in the page)
  const visible = (el) => !!el && el.getBoundingClientRect().height > 2;
  const active = document.querySelector('.tabStrip .tab.active')?.textContent || '';
  const layer = document.querySelector('[data-page="1"] .textLayer')?.textContent || '';
  const source = document.querySelector(code)?.textContent || '';
  const chips = [...document.querySelectorAll('.commentTurn .stateTag')].map((c) => c.textContent.trim());
  const badge = document.querySelector('[data-guide="header.inbox"] .inboxBadge')?.textContent.trim() || '';
  const popover = document.querySelector('.commentPopover');
  const said = popover ? popover.textContent : '';
  const thread = document.querySelector('.commentPopover .commentThread, .commentCard.active .commentThread');
  const replies = thread ? [...thread.querySelectorAll('.commentItem.reply')].map((r) => r.textContent) : [];
  return {
    code: document.querySelectorAll(`${code} > .cm-line`).length > 2,
    selected: (window.getSelection()?.toString() || '').length > 10,
    tip: visible(document.querySelector('.plainTip')),
    card: visible(document.querySelector('.commentNew .commentField')),
    sendTicked: !!document.querySelector('.commentNew .commentSendTo input:checked'),
    working: chips.some((c) => /Codex/.test(c)),
    // notes.pdf as Codex's first build left it: section 2 written
    notesRebuilt: /notes/.test(active) && layer.includes('One attention layer'),
    // and as the passage's turn left them: the sentence why under (1)
    pdfFixed: /notes/.test(active) && layer.includes('Dividing by'),
    figure: /attention-map/.test(active) && layer.includes('query'),
    badge3: badge === '3',
    inbox: document.querySelectorAll('.inboxPopover .inboxRow').length >= 3,
    question: visible(popover) && said.includes('Should I add the cost'),
    footnoteWorking: visible(popover) && said.includes('Yes, as a footnote') && chips.some((c) => /Codex/.test(c)),
    answered: replies.some((r) => r.includes('Added it as a footnote')),
    texFilled: source.includes('One attention layer'),
    // Codex's answer in the source's card (read later, from the Inbox)
    texAnswered: [...document.querySelectorAll('.docTextView .commentCard')].some((c) => c.textContent.includes('Should I add the cost')),
    footnoteShown: source.includes('\\footnote{'),
  };
};

const disk = prepareFolder();
const agentLog = path.join(DIR, 'agent');
fs.rmSync(agentLog, { recursive: true, force: true });
const { server, account } = await library({
  users: [USER], curated: false, computer: COMPUTER,
  env: {
    // the desktop app's own server, as the e2e suite runs it (docs/dev/local_agents.md "Tests")
    GAMMA_FOLDER_LINKS: '1', GAMMA_SHELL_SECRET: '', GAMMA_LOCAL_AGENTS: '1',
    GAMMA_AGENT_CODEX: JSON.stringify([process.execPath, path.join(HERE, 'demo-agent.mjs'), STORY, agentLog]),
    // no Claude Code on the clip's computer, whatever this one has
    GAMMA_AGENT_CLAUDE: path.join(DIR, 'no-claude-code-here'),
  },
});
let browser = null, page = null;
const take = new Take(OUT, NAME);
const turns = () => {
  try { return fs.readFileSync(path.join(agentLog, 'turns.jsonl'), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)); }
  catch { return []; }
};
// A file's page, by its path in the folder (quick open's listing).
const pageOf = async (rel) => ((await account.api(`/api/files?q=${encodeURIComponent(rel)}`)).files || [])
  .find((f) => f.path === rel || f.path.endsWith(`/${rel}`));
try {
  // ---- off camera ------------------------------------------------------
  const codex = await account.api('/api/local-agents/codex', { method: 'PATCH', body: { enabled: true } });
  if (!codex.enabled || !codex.ready) throw new Error(`Codex is not on and ready: ${JSON.stringify(codex)}`);
  // The synced folder, made and read as the desktop app makes one.
  const link = await account.api('/api/folder-links', { method: 'POST',
    body: { path: disk, new_folder: true, pull: true, push: 'everything' } });
  await account.api(`/api/folder-links/${link.id}/sync?wait=1`, { method: 'POST' });
  const files = {};
  for (const [key, rel] of Object.entries(FILES)) {
    for (let i = 0; i < 60 && !files[key]; i++) { files[key] = await pageOf(rel); if (!files[key]) await sleep(500); }
    if (!files[key]) throw new Error(`No page for ${rel}`);
  }
  browser = await launchRetina();
  // The window's layout (a browser preference): no right dock, since the
  // notes and the chat a fresh layout docks there are no part of this story;
  // the comments take the margin.
  const layout = () => { try { localStorage.setItem('gamma-layout', JSON.stringify({ left: [], right: [], bottom: [] })); } catch {} };
  page = await openPage(browser, account, { uiScale: 1.3, userAgent: DESKTOP_UA, init: layout });
  const open = async (key, ready) => {
    await page.goto(`${account.server.base}/?ws=${account.ws}&page=${files[key].id}`, { waitUntil: 'networkidle' });
    await page.waitForFunction(ready, null, { timeout: 30000 });
    await sleep(900);   // the tabs reach the account's preference
  };
  // The three files open as tabs, the notes, the figure and the section.
  await open('notes', () => document.querySelectorAll('[data-page="1"] .textLayer span').length > 10);
  await open('figure', () => [...document.querySelectorAll('[data-page="1"] .textLayer span')].some((s) => s.textContent.trim() === 'query'));
  await open('tex', () => document.querySelectorAll('.docTextView .docTextHost > .cm-editor > .cm-scroller > .cm-content > .cm-line').length > 2);
  const tabs = await page.locator('.tabStrip .tab').allInnerTexts();
  if (tabs.map((t) => t.trim()).join('|') !== 'notes|attention-map|multihead.tex') throw new Error(`The tabs: ${JSON.stringify(tabs)}`);
  const tab = (name) => page.locator('.tabStrip .tab', { hasText: name }).first();
  const pointer = hand(page);
  await pointer.park(VIEW.width * 0.55, VIEW.height * 0.55);
  await sleep(800);
  await shot(page, '0-start');

  if (INSPECT) take.rehearse(page);
  else await take.start(page);
  await take.watch(PROBE);
  await sleep(1000);

  // 1. The empty section's source: its heading, a comment for Codex.
  const heading = await phraseEnds(page, CODE, PLACES.multihead);
  if (!heading) throw new Error('No heading in the code view');
  const hFrom = [heading.first.x - 1, heading.first.y + heading.first.height / 2];
  const hTo = [heading.last.x + heading.last.width + 1, heading.last.y + heading.last.height / 2];
  take.mark('selectTex');
  await pointer.drag([hFrom, hTo], 600);
  await pointer.glide([hTo[0] + 6, hTo[1] + 8], 120);   // moving on from the release opens the selection's tip
  await take.until('tip', 5000);
  await sleep(300);
  take.mark('toCommentTex');
  await pointer.click(page.locator('.plainTip').getByRole('button', { name: 'Comment on this passage' }), 420);
  await take.until('card', 5000);
  await sleep(250);
  // The field has the focus: the words, then the first comment for Codex
  // ticks Send to Codex (the next ones remember it) and sends.
  take.mark('typeMultihead');
  await typeText(page, ASK.multihead, { ms: 32 });
  take.mark('typedMultihead');
  await sleep(300);
  take.mark('toTick');
  await pointer.click(page.locator('.commentNew .commentSendTick').last(), 520, 0.12, 0.5);
  await take.until('sendTicked', 3000);
  await sleep(350);
  await shot(page, '1-tex-typed');
  take.mark('toSend');
  await pointer.click(page.locator('.commentNew .commentSend').last(), 380);
  take.mark('sentMultihead');
  await take.until('working', 10000);
  // the pointer off the text, beside it, while Codex writes the section
  await pointer.glide([VIEW.width * 0.6, VIEW.height * 0.62], 500);
  await shot(page, '2-tex-sent');
  // Codex's section shows in the source as Gamma reads the edited file (sped up).
  await take.until('texFilled', 60000);
  await sleep(1500);
  take.mark('texRead');
  await shot(page, '2b-tex-filled');

  // 2. The notes, rebuilt with the new section by the time they are read: a
  // passage, a comment, the tick remembered.
  take.mark('toNotes');
  await pointer.click(tab('notes'), 600);
  take.mark('openNotes');
  await take.until('notesRebuilt', 60000);
  await sleep(1100);   // the page settles (its text layer, the chips)
  take.mark('notesShown');
  await shot(page, '3-notes');
  const ends = await phraseEnds(page, '[data-page="1"] .textLayer', PLACES.scale);
  if (!ends) throw new Error(`No "${PLACES.scale}" on notes.pdf's page 1`);
  const from = [ends.first.x - 1, ends.first.y + ends.first.height / 2];
  const to = [ends.last.x + ends.last.width + 1, ends.last.y + ends.last.height / 2];
  take.mark('select');
  await pointer.drag([from, to], 650);
  await pointer.glide([to[0] + 6, to[1] + 8], 120);
  await page.waitForFunction(() => document.querySelector('.plainTip')?.getBoundingClientRect().height > 2, null, { timeout: 5000 });
  await sleep(350);
  take.mark('toComment');
  await pointer.click(page.locator('.plainTip').getByRole('button', { name: 'Comment on this passage' }), 420);
  await page.locator('.commentNew .commentField .cm-content').last().waitFor();
  await sleep(250);
  take.mark('typeScale');
  await typeText(page, ASK.scale, { ms: 32 });
  take.mark('typedScale');
  const scaleTicked = await page.locator('.commentNew .commentSendTo input').last().isChecked();
  await sleep(350);
  await shot(page, '4-scale-typed');
  await page.keyboard.press('Shift+Enter');
  take.mark('sentScale');
  await sleep(1000);
  await shot(page, '5-scale-sent');
  // The thread closes, the pointer off the text: Codex's fix shows in the
  // rebuilt PDF, (1) with √d_k and the sentence why under it.
  await page.keyboard.press('Escape');
  await page.locator('.commentPopover').waitFor({ state: 'detached', timeout: 5000 });
  await pointer.glide([VIEW.width - 150, VIEW.height * 0.5], 450);
  take.mark('scaleClosed');
  await take.until('pdfFixed', 60000);
  await sleep(1700);
  take.mark('pdfRead');
  await shot(page, '5b-pdf-fixed');

  // 3. The figure: a region over the future tokens.
  take.mark('toFigure');
  await pointer.click(tab('attention-map'), 600);
  take.mark('openFigure');
  await take.until('figure', 30000);
  await sleep(900);
  take.mark('figureShown');
  await shot(page, '6-figure');
  const labels = await page.evaluate(() => [...document.querySelectorAll('[data-page="1"] .textLayer span')]
    .map((s) => { const b = s.getBoundingClientRect(); return { t: s.textContent.trim(), x: b.x, y: b.y, w: b.width, h: b.height }; })
    .filter((s) => s.t));
  // The key labels run along the top, the query labels down the left.
  const words = ['the', 'cat', 'sat', 'on', 'mat'];
  const keyRow = labels.filter((l) => words.includes(l.t)).sort((a, b) => a.y - b.y);
  const top = keyRow[0].y;
  const keys = keyRow.filter((l) => Math.abs(l.y - top) < 4).sort((a, b) => a.x - b.x);
  const queries = labels.filter((l) => words.includes(l.t) && Math.abs(l.y - top) >= 4).sort((a, b) => a.y - b.y);
  if (keys.length !== 6 || queries.length !== 6) throw new Error(`The figure's labels: ${JSON.stringify({ keys, queries })}`);
  const cell = keys[1].x - keys[0].x;
  // from above the "sat" key's label to the right of the grid, down to the "cat" row's foot
  const r0 = [keys[2].x + keys[2].w / 2 - cell * 0.5, keys[2].y - 6];
  const r1 = [keys[5].x + keys[5].w / 2 + cell * 0.5 + 4, queries[1].y + queries[1].h / 2 + cell * 0.5 + 2];
  take.mark('region');
  await pointer.drag([r0, r1], 700, { modifiers: ['Control'] });
  await page.waitForFunction(() => document.querySelector('.plainTip')?.getBoundingClientRect().height > 2, null, { timeout: 5000 });
  await sleep(350);
  await shot(page, '7-region');
  take.mark('toCommentFigure');
  await pointer.click(page.locator('.plainTip').getByRole('button', { name: /^Comment on this/ }), 420);
  await page.locator('.commentNew .commentField .cm-content').last().waitFor();
  await sleep(250);
  take.mark('typeMask');
  await typeText(page, ASK.mask, { ms: 32 });
  take.mark('typedMask');
  const maskTicked = await page.locator('.commentNew .commentSendTo input').last().isChecked();
  await sleep(350);
  await page.keyboard.press('Shift+Enter');
  take.mark('sentMask');
  await sleep(1000);
  // the pointer off the figure, toward the Inbox, while Codex works
  await pointer.glide([VIEW.width - 260, 160], 600);
  await shot(page, '8-mask-sent');

  // 4. Codex works through the three in turn (sped up): the figure turns
  // masked in view, the Inbox counts its answers to three.
  take.mark('waitAgent');
  await take.until('badge3', 180000);
  const figureFile = path.join(disk, FILES.figure);
  for (let i = 0; i < 40 && (await pageOf(FILES.figure))?.properties?.doc?.hash !== sha(figureFile).slice(0, 24); i++) await sleep(250);
  await sleep(1800);   // the masked figure drawn in the view
  take.mark('agentDone');
  await shot(page, '9-answered');
  // the figure's thread, answered, closes: the Inbox opens over the figure
  await page.keyboard.press('Escape');
  await page.locator('.commentPopover').waitFor({ state: 'detached', timeout: 5000 });
  await sleep(400);
  const bell = page.locator('[data-guide="header.inbox"]');
  take.mark('toInbox');
  await pointer.click(bell, 500);
  await take.until('inbox', 10000);
  await sleep(1300);
  await take.frame('inbox', page.locator('.inboxPopover'));
  await shot(page, '10-inbox');
  // 5. The answer on the source asks a question: answered in its thread,
  // which resumes Codex's session.
  const rowOf = (text) => page.locator('.inboxPopover .inboxRow').filter({ hasText: text }).first();
  take.mark('toTexReply');
  await pointer.click(rowOf('comment on multihead.tex').locator('.inboxMain'), 600);
  const thread = page.locator('.commentPopover .commentThread').first();
  await take.until('question', 20000);
  await sleep(1500);
  await shot(page, '13-question');
  take.mark('toReply');
  // The reply field, opened by a press on the thread when it is not open yet (as in Notion).
  const replyField = thread.locator('.commentField .cm-content').last();
  if (!(await replyField.isVisible().catch(() => false))) {
    await pointer.click(thread.locator('.commentBody').last(), 500);
    await replyField.waitFor();
  }
  await pointer.click(replyField, 450, 0.3, 0.5);
  const followTicked = await thread.locator('.commentSendTo input').last().isChecked();
  take.mark('typeFootnote');
  await typeText(page, ASK.footnote, { ms: 34 });
  take.mark('typedFootnote');
  await sleep(350);
  await page.keyboard.press('Shift+Enter');
  take.mark('sentFootnote');
  await take.until('answered', 120000);
  await sleep(1500);
  await shot(page, '14-answered');
  // Read, the popover closes on the source: the footnote Codex wrote.
  await take.until('footnoteShown', 30000);
  take.mark('closeAnswer');
  await page.keyboard.press('Escape');
  await page.locator('.commentPopover').waitFor({ state: 'detached', timeout: 5000 });
  // off the text, into the empty page under it, so the footnote reads whole
  await pointer.glide([VIEW.width * 0.62, VIEW.height - 170], 450);
  await sleep(1700);
  take.mark('end');
  await shot(page, '15-end');
  if (!INSPECT) await take.stop();
  await page.screenshot({ path: path.join(OUT, INSPECT ? 'inspect-final.png' : 'final.png') });
  await take.seen();

  // ---- what the clip claims, checked through the API and on disk ----------
  const m = take.marks;
  take.check('marks', m.tip > m.selectTex && m.working > m.sentMultihead && m.texFilled > m.working && m.texRead - m.texFilled >= 1.2
    && !(m.texAnswered <= m.texRead)
    && m.notesRebuilt > m.openNotes && m.pdfFixed > m.scaleClosed && m.pdfRead - m.pdfFixed >= 1.2
    && m.figure > m.openFigure && m.badge3 > m.sentMask && m.question > m.toTexReply
    && m.footnoteWorking > m.sentFootnote && m.answered > m.footnoteWorking && m.closeAnswer >= m.footnoteShown
    && m.end - m.closeAnswer >= 1.5, m);
  take.check('ticks', scaleTicked && maskTicked && followTicked, { scaleTicked, maskTicked, followTicked });
  const log = turns();
  take.check('turns', JSON.stringify(log.map((t) => [t.turn, t.problems.length])) === JSON.stringify([['multihead', 0], ['scale', 0], ['mask', 0], ['footnote', 0]]),
    log.map((t) => ({ turn: t.turn, problems: t.problems, steps: t.steps })));
  const named = (name) => log.find((t) => t.turn === name);
  take.check('resumed', named('footnote')?.resumed && named('footnote').session === named('multihead')?.session,
    log.map((t) => [t.turn, t.session, t.resumed]));
  take.check('built', log.every((t) => t.steps.some((s) => s.run === 'make' && s.ok)), log.map((t) => t.steps));
  const tex = fs.readFileSync(path.join(disk, 'sections', 'attention.tex'), 'utf8');
  const head = fs.readFileSync(path.join(disk, 'sections', 'multihead.tex'), 'utf8');
  take.check('edits', tex.includes('{\\sqrt{d_k}}') && fs.readFileSync(path.join(disk, 'figures', 'attention-map.tex'), 'utf8').includes('\\causaltrue')
    && head.includes('MultiHead') && head.includes('\\footnote{'), { tex: tex.slice(0, 400), head });
  const all = [];
  for (const rel of Object.values(FILES)) {
    const file = await pageOf(rel);
    all.push(...((await account.api(`/api/pages/${file.id}/comments?state=all`)).threads || []).map((t) => ({ ...t, file: rel })));
  }
  const codexReplies = all.map((t) => ({ file: t.file, resolved: !!t.resolved_at, said: t.comments.filter((c) => c.agent === 'Codex').map((c) => c.body) }));
  const of = (rel) => codexReplies.find((t) => t.file === rel);
  take.check('replies', all.length === 3 && of(FILES.notes)?.said.join('|') === REPLY.scale
    && of(FILES.figure)?.said.join('|') === REPLY.mask
    && of(FILES.tex)?.said.join('|') === `${REPLY.multihead}|${REPLY.footnote}`
    && of(FILES.tex).resolved && !of(FILES.notes).resolved && !of(FILES.figure).resolved, codexReplies);
  const asked = all.map((t) => t.comments[0]?.body).sort();
  take.check('asked', JSON.stringify(asked) === JSON.stringify([ASK.scale, ASK.mask, ASK.multihead].sort()), asked);
  // After a round each PDF's page holds what the last build wrote (a
  // document's hash is its content's sha256[:24], docs/dev/documents.md):
  // the notes were not open while Codex rebuilt them last.
  await account.api(`/api/folder-links/${link.id}/sync?wait=1`, { method: 'POST' });
  const held = {};
  for (const rel of [FILES.notes, FILES.figure]) {
    const want = sha(path.join(disk, rel)).slice(0, 24);
    for (let i = 0; i < 40; i++) {
      held[rel] = { want, has: (await pageOf(rel))?.properties?.doc?.hash || '' };
      if (held[rel].has === want) break;
      await sleep(500);
    }
  }
  take.check('rebuiltHeld', Object.values(held).every((h) => h.has === h.want), held);
  await page.reload();
  await page.locator(`${CODE} > .cm-line`).first().waitFor({ timeout: 30000 });
  const reloaded = await page.locator(CODE).innerText();
  take.check('reloadTex', reloaded.includes('\\footnote{'), reloaded.slice(0, 300));

  if (INSPECT) console.log(JSON.stringify({ marks: take.marks, framing: take.framing, checks: take.checks, details: take.details }, null, 1));
  else await take.finish({ folder: link.folder_id, computer: COMPUTER });
} catch (error) {
  await take.fail(error);
} finally {
  if (browser) await browser.close().catch(() => {});
  await server.stop();
}
