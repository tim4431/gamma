// "Ask an AI about your papers", the README's lead demo: one chat on the
// curated atom-arrays paper (Bluvstein et al. 2022, Nature, CC BY 4.0), the
// AI chat docked right, Notes closed.
//
//  1. Page 2, Fig. 1c, d at the foot of the PDF pane, their caption just
//     below it (out of view). A Ctrl+drag boxes panels c and d (the area
//     drag, pdf.areaDrag: Ctrl on Windows) and the figure lands in the
//     composer; "What do panels c and d show?"; the answer streams with a
//     p. 2 citation. Hovering the chip previews its quote, a click jumps the
//     PDF to the caption's marked passage, which was off screen.
//  2. Page 1's abstract, where the clause citing refs 3–5 is already a
//     yellow highlight (made off camera with a real drag and the selection
//     toolbar's colour, and saved). A plain click on it puts its quote in
//     the chat as a "PDF passage" chip; "Read refs 3–5 and compare what each
//     one showed." The agent searches the registries for the three titles
//     (search_papers: "Searching papers, 3 queries…"), reads the three
//     papers the searches found (fetch_paper: "Fetching 3 documents…") and
//     answers with a table citing each entry of the reference list on p. 6.
//
// Only the model's words are scripted (models/ai-reading.mjs, through
// take.mjs demoModel); the searches, downloads, pictures and citations are
// Gamma's own. The model checks each fact of its table against that paper's
// fetched text. The take is kept (timing.json) only when every check passes,
// the saved chat after a reload included.
//
// Isolated: a harness server (free port, throwaway data dir, registry
// lookups on so search_papers reaches Crossref and OpenAlex) with the
// curated demo.zip; never the developer's servers. Before the take the
// three arXiv PDFs are requested once from here, so the first download on
// camera does not wait on a cold mirror (nothing is cached in Gamma).
//
//   node tools/readme-media/record-native-agentic.mjs [--inspect]
// Writes artifacts/readme-media/native-agentic/:
// frames/, timing.json, saved-chat.json, final.png; --inspect stops before
// the take with inspect-*.png. Edit: render_feature_demos.py native-agentic.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { ROOT, launchRetina, openChat } from './runtime.mjs';
import { library, openPage, hand, typeText, Take, demoModel, scratch, sleep, ATOMS, INSPECT } from './take.mjs';
import { REFS, ASKS_REFS } from './models/ai-reading.mjs';

const lib = (p) => import(pathToFileURL(path.join(ROOT, p)).href);
const { getDocument } = await lib('frontend/node_modules/pdfjs-dist/legacy/build/pdf.mjs');
const { citationRuns, matchCitation } = await lib('frontend/src/pdf/pdfCitation.js');
const { parseGammaLink } = await lib('frontend/src/shared/model/gammaLinks.js');

const NAME = 'native-agentic';
const DIR = scratch(NAME);
const Q1 = 'What do panels c and d show?';
const Q2 = 'Read refs 3–5 and compare what each one showed.';
if (!ASKS_REFS.test(Q2) || ASKS_REFS.test(Q1)) throw new Error('The scripted model would not tell the two questions apart');
// The curated PDF's own words the answers cite (pdf.js text; checked below
// with Gamma's matcher before anything is recorded).
const QUOTES = {
  fig: { page: 2, text: 'movement does not observably affect entanglement or coherence' },
  ref3: { page: 6, text: 'Jaksch, D. et al. Fast quantum gates for neutral atoms' },
  ref4: { page: 6, text: 'Urban, E. et al. Observation of Rydberg blockade between two atoms' },
  ref5: { page: 6, text: 'Levine, H. et al. Parallel implementation of high-fidelity multiqubit gates with neutral atoms' },
};
const ARXIV = ['quant-ph/0004038', '0805.0758', '1908.06101'];
// The abstract's clause the second question points at: "excitation into
// Rydberg states is used for entanglement generation", over two lines, up to
// its superscript "3–5" (left out: the chat's bubble quotes the selection as
// plain text, so a superscript would read "generation3–5" there).
const CLAUSE = { from: 'excitation into Rydberg states is', last: 'used for entanglement generation' };
const CLAUSE_RE = /^excitation into Rydberg states is used for entanglement generation$/;
const SASH_X = 760;   // PDF left of it, chat right
// Settings → Interface size: the chat's text readable at README width with
// the whole window in view (the edit frames the window whole but for the
// citation's push-in; the PDF's size is the pane's, not this).
const UI_SCALE = 1.5;
const union = (bs) => {
  bs = bs.filter((b) => b && b.width > 0);
  const x = Math.min(...bs.map((b) => b.x)), y = Math.min(...bs.map((b) => b.y));
  return { x, y, width: Math.max(...bs.map((b) => b.x + b.width)) - x, height: Math.max(...bs.map((b) => b.y + b.height)) - y };
};

// A recording overlay (the house keycap, as in record-collab.mjs): while
// window.__keycap(keys) shows, a dark key badge rides above-right of the
// pointer (the figure sits at the pane's foot, so below it would leave the
// window), so the README's viewer sees that the box is a Ctrl+drag.
const keycapOverlay = () => window.addEventListener('DOMContentLoaded', () => {
  const cap = document.createElement('div');
  cap.style.cssText = 'position:fixed;z-index:2147483646;left:0;top:0;display:flex;gap:5px;padding:6px 8px;border-radius:10px;'
    + 'background:rgba(22,24,29,.9);box-shadow:0 4px 14px rgba(0,0,0,.25);pointer-events:none;opacity:0;'
    + 'transition:opacity 120ms ease-out;font:600 17px/1 system-ui,"Segoe UI",sans-serif;color:#fff';
  document.body.appendChild(cap);
  let x = 0, y = 0;
  const place = () => { cap.style.transform = `translate(${x + 18}px,${y - 50}px)`; };
  document.addEventListener('pointermove', (e) => { x = e.clientX; y = e.clientY; place(); }, true);
  window.__keycap = (keys) => {
    cap.innerHTML = keys.map((k) => `<span style="display:inline-block;min-width:16px;text-align:center;padding:5px 8px;`
      + `border-radius:6px;background:#3a3d45;box-shadow:inset 0 -2px 0 rgba(0,0,0,.35)">${k}</span>`).join('');
    place();
    cap.style.opacity = keys.length ? '1' : '0';
  };
});

// What the viewer sees, every animation frame (Take.watch): the first frame
// each is true is its mark. window.__naHl is the highlight's id. The steps
// pill's label morphs from one text to the next (MorphText): "…Shown" is
// the first frame its label rests whole (no morph under way), "…Gone" the
// first frame it starts to change away, so the edit plays only clean labels.
const PROBE = () => {
  const rows = [...document.querySelectorAll('.chatBubbleRow.ai')];
  const text = (r) => (r ? [...r.querySelectorAll('.chatBubble.ai p, .chatBubble.ai li, .chatBubble.ai td')].reduce((n, e) => n + e.textContent.length, 0) : 0);
  const stop = !!document.querySelector('.chatStopBtn');
  const live = (r) => !!r?.querySelector('.chatSteps.live');
  const pill = (r) => r?.querySelector('.chatSteps .chatPillText .morphText')?.textContent || '';
  const moving = (r) => !!r?.querySelector('.chatSteps .morph.moving');
  const rests = (r, re) => re.test(pill(r)) && !moving(r);
  const tip = document.querySelector('.plainTip');
  const S = window.__naPill = window.__naPill || {};
  const img = document.querySelector('.chatImgPreview img');
  const viewer = document.querySelector('.pdfViewer')?.getBoundingClientRect();
  const hl = window.__naHl ? [...document.querySelectorAll(`[data-hl-id="${window.__naHl}"]`)] : [];
  const [r1, r2] = rows;
  return {
    figure: !!img && img.complete && img.naturalWidth > 0,
    areaTip: !!tip,
    tipSettled: !!tip && getComputedStyle(tip).opacity === '1' && tip.getAnimations({ subtree: true }).every((a) => a.playState !== 'running'),
    a1Start: text(r1) > 0,
    a1Resp: text(r1) > 0 && (!r1.querySelector('.chatSteps') || rests(r1, /Responding/)),
    a1Done: text(r1) > 80 && !stop && !live(r1),
    preview: !!document.querySelector('.citePreview'),
    marked: !!document.querySelector('.pdfCitationMark'),
    hlVisible: !!viewer && hl.some((e) => { const b = e.getBoundingClientRect(); return b.width > 0 && b.top >= viewer.top && b.bottom <= viewer.bottom; }),
    chip: [...document.querySelectorAll('.chatSelChip .chatSelChipLabel')].some((e) => /PDF passage/.test(e.textContent)),
    thinking2: !!r2,
    searching: /Searching papers, 3 queries/.test(pill(r2)),
    searchingShown: (S.s = S.s || rests(r2, /^Searching papers, 3 queries/)),
    searchingGone: !!S.s && !/^Searching papers, 3 queries/.test(pill(r2)),
    fetching: /Fetching 3 documents/.test(pill(r2)),
    fetchingShown: (S.f = S.f || rests(r2, /^Fetching 3 documents/)),
    fetchingGone: !!S.f && !/^Fetching 3 documents/.test(pill(r2)),
    a2Start: text(r2) > 0,
    a2Resp: text(r2) > 0 && rests(r2, /^Responding/),
    a2Done: text(r2) > 200 && !stop && !live(r2),
    a2Settled: text(r2) > 200 && !stop && !live(r2) && rests(r2, /steps/),
  };
};

const take = new Take(DIR, NAME);
let server, model, browser;
try {
  const lib0 = await library({ env: { GAMMA_METADATA_LOOKUP: 'on' } });
  server = lib0.server;
  const account = lib0.account;
  const pageId = ATOMS.id;
  // Light English UI; the chat reads a paper itself with fetch_paper ("Read
  // long papers with a helper" off), so its pill says "Fetching …".
  await account.api('/api/prefs/profile', { method: 'PATCH', body: { set: { theme: 'light', language: 'en', delegateReads: false, aiLoginCheck: 'ping' } } });
  await account.api(`/api/chats/${pageId}`, { method: 'DELETE' }).catch(() => {});
  await account.api('/api/prefs/open-tabs', { method: 'PUT', body: { value: [] } }).catch(() => {});

  // The PDF's text as the viewer's pdf.js reads it, for Gamma's citation matcher.
  const block = await account.api(`/api/blocks/${pageId}`);
  const pdfUrl = block.properties.source_url || `/api/uploads/${block.properties.doc_id}.pdf`;
  const pdf = await getDocument({ data: new Uint8Array(await (await account.api(pdfUrl, { raw: true })).arrayBuffer()), verbosity: 0 }).promise;
  const runs = [];
  for (let n = 1; n <= pdf.numPages; n++) runs[n] = citationRuns((await (await pdf.getPage(n)).getTextContent()).items);
  const refList = (await (await pdf.getPage(6)).getTextContent()).items.map((i) => i.str + (i.hasEOL ? ' ' : '')).join('').replace(/\s+/g, ' ');
  await pdf.destroy();
  const exact = (n, quote) => { const m = runs[n] && matchCitation(runs[n], quote); return !!m && m.status === 'matched' && !m.approximate; };
  for (const [k, q] of Object.entries(QUOTES)) if (!exact(q.page, q.text)) throw new Error(`Quote ${k} is not verbatim on p. ${q.page}: ${q.text}`);
  // Refs 3–5 are the papers the model compares.
  for (const r of REFS) if (!refList.includes(`${r.n}. ${r.who}`) || !refList.toLowerCase().includes(r.title.toLowerCase()) || !refList.includes(`(${r.year})`)) throw new Error(`Ref. ${r.n} is not ${r.who} ${r.year} on p. 6`);
  const citationsIn = (md) => [...String(md || '').matchAll(/\]\((\/\?page=[^)\s]+)\)/g)].map((m) => {
    const p = parseGammaLink(m[1], server.base);
    return p?.kind === 'citation' ? { pageId: p.pageId, page: p.page, quote: p.quote, exact: p.pageId === pageId && !!p.quote && exact(p.page, p.quote) } : null;
  }).filter(Boolean);

  model = await demoModel(account, 'ai-reading', { quotes: QUOTES });
  // Warm the arXiv mirror once from here (Gamma keeps nothing of it).
  await Promise.allSettled(ARXIV.map((id) => fetch(`https://arxiv.org/pdf/${id}`, { signal: AbortSignal.timeout(30000) }).then((r) => r.arrayBuffer())));

  browser = await launchRetina();
  const page = await openPage(browser, account, { url: `/?page=${pageId}&ws=${account.ws}`, uiScale: UI_SCALE, init: keycapOverlay });
  take.page = page;   // failure.png
  const requests = [];
  page.on('request', (r) => {
    if (new URL(r.url()).pathname !== '/api/ai/chat' || r.method() !== 'POST') return;
    const b = r.postDataJSON() || {};
    requests.push({ prompt: b.prompt, page_id: b.page_id, agent_scope: b.agent_scope, delegate_reads: b.delegate_reads,
      images: (b.images || []).map((i) => (typeof i === 'string' ? 'data' : { kind: i.kind, page: i.page, box: i.box })),
      selections: (b.selections || []).map((s) => ({ text: s.text, page: s.page })) });
  });
  const problems = [];
  page.on('response', (r) => { const u = new URL(r.url()); if (u.pathname.startsWith('/api/') && r.status() >= 400) problems.push(`${r.status()} ${u.pathname}`); });

  await page.locator('[data-page="1"] .textLayer span').first().waitFor({ timeout: 60000 });
  const notesClose = page.getByRole('button', { name: 'Close Notes', exact: true });
  if (await notesClose.isVisible()) await notesClose.click();
  await openChat(page);
  const input = page.locator('.chatInput');
  await input.waitFor();
  if (await page.locator('.chatSetup').count()) throw new Error('The chat shows its setup card: the demo connection is not visible to it');
  // The split: the sash to SASH_X.
  {
    const sash = page.locator('[role="separator"][data-panel-group-direction="horizontal"]').first();
    const b = await sash.boundingBox();
    await page.mouse.move(b.x + b.width / 2, b.y + 200);
    await page.mouse.down();
    for (let i = 1; i <= 20; i++) await page.mouse.move(b.x + b.width / 2 + (SASH_X - b.x - b.width / 2) * i / 20, b.y + 200);
    await page.mouse.up();
    await sleep(500);
  }

  // ---- off camera: the clause becomes a yellow highlight, as a reader makes one
  // The PDF's scroller, and the scrollTop that puts the clause at 44% of it (the title in view).
  const clauseScroll = () => page.evaluate((from) => {
    const span = [...document.querySelectorAll('[data-page="1"] .textLayer span')].find((s) => !s.childElementCount && s.textContent.includes(from));
    if (!span) return null;
    let sc = span.parentElement;
    while (sc && !(sc.scrollHeight > sc.clientHeight + 4 && /auto|scroll/.test(getComputedStyle(sc).overflowY))) sc = sc.parentElement;
    if (!sc) return null;
    sc.setAttribute('data-na-scroller', '');
    const top = sc.scrollTop + span.getBoundingClientRect().top - sc.getBoundingClientRect().top - sc.clientHeight * 0.44;
    return Math.max(0, Math.round(top));
  }, CLAUSE.from);
  const scrollTo = (top) => page.evaluate((top) => { document.querySelector('[data-na-scroller]').scrollTop = top; }, top);
  let clauseTop = await clauseScroll();
  if (clauseTop === null) throw new Error('The abstract clause or the PDF scroller was not found');
  await scrollTo(clauseTop);
  await sleep(900);
  const geo = await page.evaluate(({ from, last }) => {
    const spans = [...document.querySelectorAll('[data-page="1"] .textLayer span')].filter((s) => !s.childElementCount);
    const a = spans.find((s) => s.textContent.includes(from));
    const c = spans.find((s) => s.textContent.includes(last));
    if (!a || !c) return null;
    const box = (el, i) => { const r = document.createRange(); r.setStart(el.firstChild, i); r.setEnd(el.firstChild, i + 1); return r.getBoundingClientRect(); };
    const f = box(a, a.textContent.indexOf(from)), n = box(c, c.textContent.indexOf(last) + last.length - 1);
    // From the left edge of "excitation" to the right part of generation's
    // last "n", on whole pixels.
    return { start: [Math.ceil(f.x + 1), Math.round(f.y + f.height / 2)], end: [Math.floor(n.x + n.width * 0.8), Math.round(n.y + n.height / 2)],
      texts: [a.textContent, c.textContent] };
  }, CLAUSE);
  if (!geo) throw new Error('The clause was not found in the text layer');
  console.log('clause spans:', JSON.stringify(geo.texts));
  // The reference link over the "3–5" next to it could take the release
  // point (pdf.js then selects to the page's end): it lets the pointer
  // through for this drag.
  await page.addStyleTag({ content: '.pdfLinkBox { pointer-events: none !important }' }).then((h) => h.evaluate((el) => el.setAttribute('data-na-drag', '')));
  const hlBefore = await page.locator('[data-hl-id]').evaluateAll((es) => es.map((e) => e.dataset.hlId));
  await page.mouse.move(...geo.start);
  await page.mouse.down();
  for (let i = 1; i <= 24; i++) await page.mouse.move(geo.start[0] + (geo.end[0] - geo.start[0]) * i / 24, geo.start[1] + (geo.end[1] - geo.start[1]) * i / 24);
  await page.mouse.up();
  const selected = (await page.evaluate(() => String(window.getSelection() || ''))).replace(/\s+/g, ' ').trim();
  await page.evaluate(() => document.querySelector('style[data-na-drag]')?.remove());
  if (!CLAUSE_RE.test(selected)) throw new Error(`The drag selected ${JSON.stringify(selected)}`);
  const color = page.locator('.plainTip .colorBtn').first();
  await color.waitFor();
  await sleep(200);
  await color.click();
  let hlId = null;
  for (let i = 0; i < 40 && !hlId; i++) {
    await sleep(200);
    hlId = (await page.locator('[data-page="1"] [data-hl-id]').evaluateAll((es, before) => es.map((e) => e.dataset.hlId).find((id) => !before.includes(id)) || null, hlBefore));
  }
  if (!hlId) throw new Error('No highlight appeared');
  let hlBlock = null;
  for (let i = 0; i < 20 && !hlBlock; i++) {
    await sleep(250);
    const find = (node) => (node?.id === hlId || node?.properties?.highlight_id === hlId ? node : (node?.children || []).map(find).find(Boolean));
    hlBlock = find((await account.api(`/api/blocks/${pageId}/subtree`)).block);
  }
  if (!hlBlock) throw new Error('The highlight was not saved');
  take.details.highlight = { id: hlId, selected, block: hlBlock.id };
  if (INSPECT) await page.screenshot({ path: path.join(DIR, 'inspect-highlight.png') });
  // Back to a clean start: no selection, no chips, Notes closed.
  await page.evaluate(() => window.getSelection()?.removeAllRanges());
  if (await notesClose.isVisible()) await notesClose.click();
  for (let i = 0; i < 8; i++) {
    const close = page.locator('.chatSelChip .chatSelChipClose').first();
    if (!await close.count()) break;
    await close.click();
    await sleep(200);
  }
  await page.evaluate((id) => { window.__naHl = id; }, hlId);
  if (INSPECT) await page.screenshot({ path: path.join(DIR, 'inspect-clean.png') });

  // ---- the first view: Fig. 1 on page 2
  // Fig. 1's caption just under the PDF pane's foot, so panels c and d sit
  // at the bottom of the view and the passage the answer cites is off
  // screen: the citation's click is then a jump the viewer sees.
  await page.evaluate(() => { const sc = document.querySelector('[data-na-scroller]'), p = document.querySelector('[data-page="2"]'); sc.scrollTop += p.getBoundingClientRect().top - sc.getBoundingClientRect().top; });
  await page.locator('[data-page="2"] .textLayer span').first().waitFor({ state: 'attached', timeout: 30000 });
  await sleep(700);
  for (let i = 0; i < 4; i++) {
    const off = await page.evaluate(() => {
      const sc = document.querySelector('[data-na-scroller]');
      const cap = [...document.querySelectorAll('[data-page="2"] .textLayer span')].filter((s) => /Quantum information architecture|state \(Methods\), and are then/.test(s.textContent));
      if (!cap.length) return null;
      const d = Math.min(...cap.map((s) => s.getBoundingClientRect().top)) - (sc.getBoundingClientRect().bottom + 3);
      sc.scrollTop += d;
      return d;
    });
    if (off === null) throw new Error('Fig. 1 caption was not found on page 2');
    await sleep(700);
    if (Math.abs(off) < 2) break;
  }
  await page.mouse.move(SASH_X - 60, 860);
  await sleep(3000);   // the login check runs 1.5 s after load
  if (await page.locator('.chatHealthStrip').count()) throw new Error(`The chat reports the connection unhealthy: ${await page.locator('.chatHealthStrip').innerText()}`);
  if (await page.locator('.chatSelChip, .chatImgPreview').count()) throw new Error('The composer is not empty at the start');
  if (await notesClose.isVisible()) throw new Error('Notes is open at the start');
  const sheet = await page.locator('[data-page="2"]').first().boundingBox();
  // Fig. 1c,d: the parity and fidelity plots, relative to the page's width.
  const fig = { x: sheet.x + sheet.width * 0.515, y: sheet.y + sheet.width * 0.365, width: sheet.width * 0.43, height: sheet.width * 0.215 };
  // The cited words are below the view before the click.
  take.details.passageBefore = await page.evaluate((words) => {
    const sc = document.querySelector('[data-na-scroller]').getBoundingClientRect();
    return [...document.querySelectorAll('[data-page="2"] .textLayer span')].filter((s) => s.textContent.includes(words)).map((s) => ({ top: s.getBoundingClientRect().top, paneBottom: sc.bottom }));
  }, 'movement does not observably affect');
  // The page's right column (the caption's, and page 1's): its left edge,
  // where the close framing starts so it cuts no word.
  await take.frame('column', await page.evaluate(() => {
    const sheet = document.querySelector('[data-page="2"]').getBoundingClientRect();
    const xs = [...document.querySelectorAll('[data-page="1"] .textLayer span, [data-page="2"] .textLayer span')].map((s) => s.getBoundingClientRect())
      .filter((b) => b.width > 20 && b.left > sheet.left + sheet.width * 0.46 && b.left < sheet.left + sheet.width * 0.56 && b.top > 60 && b.bottom < innerHeight).map((b) => b.left);
    return { x: Math.min(...xs), y: 0, width: 1, height: 1 };
  }));
  await take.frame('figure', fig);
  await take.frame('pdf', '.pdfViewer');
  await take.frame('chat', page.locator('.chatPanel').first());
  await take.frame('composer', '.chatComposer');
  if (INSPECT) {
    await page.screenshot({ path: path.join(DIR, 'inspect-start.png') });
    console.log(JSON.stringify({ framing: take.framing, sheet, highlight: take.details.highlight }, null, 1));
    throw Object.assign(new Error('inspect: stopped before the take'), { inspect: true });
  }

  // ---- the take
  const h = hand(page, [fig.x + fig.width * 0.3, fig.y - 70]);
  await h.park(...h.at);
  await take.watch(PROBE);
  await sleep(500);
  await take.start(page);
  await sleep(900);   // the first frame, held in the edit

  // 1. The area drag: Ctrl held, panels c and d boxed.
  const c0 = [fig.x, fig.y], c1 = [fig.x + fig.width, fig.y + fig.height];
  take.mark('glide1');
  await h.glide(c0, 600);
  await page.evaluate(() => window.__keycap(['Ctrl']));
  await sleep(250);
  take.mark('dragStart');
  await h.drag([c0, c1], 800, { modifiers: ['Control'] });
  take.mark('drop');
  await page.evaluate(() => window.__keycap([]));
  take.mark('keyUp');
  await take.until('figure', 15000);
  await take.until('tipSettled', 5000).catch(() => {});
  take.details.composerChips = await page.locator('.chatSelChip').allInnerTexts();
  await take.frame('figureChip', '.chatImgPreview');
  await sleep(500);

  // The question, typed; Send.
  take.mark('toInput');
  await h.click(input, 550, 0.2);
  await sleep(100);
  take.mark('type1');
  await typeText(page, Q1);
  take.mark('typed1');
  await sleep(200);
  // Enter sends (the pointer's way to the Send button would only be cut).
  take.mark('send1');
  await page.keyboard.press('Enter');
  await sleep(150);
  await h.glide([h.at[0] - 40, h.at[1] - 150], 500);
  await take.until('a1Start', 60000);
  await take.until('a1Done', 60000);
  await take.until('a1Resp', 1000).catch(() => {});
  await take.frame('answer1', page.locator('.chatBubbleRow.ai').last());
  await sleep(350);

  // The citation: hover (its quote), click (the passage, marked in the PDF).
  const chip = page.locator('.chatBubbleRow.ai').last().locator('a.citePill.gammaLink-citation').first();
  const href1 = await chip.getAttribute('href');
  take.details.cite1 = href1;
  await take.frame('chip1', chip);
  take.mark('toChip');
  await h.to(chip, 550);
  await take.until('preview', 5000);
  await take.frame('preview', '.citePreview');
  await sleep(800);
  take.mark('click1');
  await h.press();
  await take.until('marked', 20000);
  // Still while the camera pushes in on the passage (the edit).
  await sleep(700);
  take.details.cite1Notice = await page.locator('.pdfCitationNotice').allInnerTexts();
  take.details.cite1Marks = await page.locator('.pdfCitationMark').count();
  const marks = await page.locator('.pdfCitationMark').evaluateAll((es) => es.map((e) => { const b = e.getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height }; }));
  await take.frame('passage', union(marks));
  // The pointer goes over to the marked passage and rests on it, at the
  // end of its last line, so the eye follows it from the chip to the jump's
  // target.
  const lastLine = marks.reduce((a, b) => (b.y > a.y + 2 ? b : a));
  take.mark('toPassage');
  await h.glide([lastLine.x + lastLine.width * 0.8, lastLine.y + lastLine.height * 0.55], 650);
  take.mark('pointed');
  await sleep(1300);
  take.mark('passageEnd');

  // 2. Up to page 1's abstract (cut in the edit): the highlight in view.
  await h.glide([fig.x + fig.width * 0.4, fig.y + fig.height + 60], 400);
  take.mark('scroll');
  clauseTop = await clauseScroll();
  await scrollTo(clauseTop);
  await take.until('hlVisible', 10000);
  await page.locator('[data-page="1"] .textLayer span').first().waitFor();
  await sleep(900);
  take.mark('part2');
  const hlRects = await page.locator(`[data-hl-id="${hlId}"]`).evaluateAll((es) => es.map((e) => { const b = e.getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height }; }).filter((b) => b.width > 1));
  await take.frame('highlight', union(hlRects));
  // A point on the clause's longest line that the highlight itself takes.
  const line = hlRects.reduce((a, b) => (b.width > a.width ? b : a));
  let target = null;
  for (const f of [0.45, 0.35, 0.55, 0.65, 0.25]) {
    const p = [Math.round(line.x + line.width * f), Math.round(line.y + line.height / 2)];
    if (await page.evaluate(({ p, id }) => document.elementFromPoint(...p)?.closest?.('[data-hl-id]')?.getAttribute('data-hl-id') === id, { p, id: hlId })) { target = p; break; }
  }
  if (!target) throw new Error('No point of the highlight takes a click');
  await h.glide(target, 600);
  await sleep(200);
  take.mark('hlClick');
  await h.press();
  await take.until('chip', 5000);
  await take.frame('passageChip', page.locator('.chatSelChip').first());
  take.details.notesOpenedByClick = await notesClose.isVisible();
  await sleep(800);   // the chip holds before the question is typed

  take.mark('toInput2');
  await h.click(input, 550, 0.2);
  await sleep(100);
  take.mark('type2');
  await typeText(page, Q2);
  take.mark('typed2');
  await sleep(200);
  take.mark('send2');
  await page.keyboard.press('Enter');
  await sleep(120);
  // Off the transcript, to the window's right edge by the composer.
  await h.glide([1440 - 14, h.at[1] + 30], 500);
  await take.until('thinking2', 20000);
  await take.until('searchingShown', 60000);
  await take.until('searchingGone', 120000);
  await take.until('fetchingShown', 120000);
  await take.until('fetchingGone', 240000);
  await take.until('a2Start', 240000);
  await take.until('a2Done', 120000);
  await take.until('a2Settled', 10000);
  await take.seen();
  const reply2 = page.locator('.chatBubbleRow.ai').last();
  await take.frame('answer2', reply2);
  await take.frame('table', reply2.locator('table').first());
  await take.frame('steps', reply2.locator('.chatSteps').first());
  await take.frame('transcript', '.chatMessages');
  // The finished answer's steps pill and table are both in the chat's view.
  take.details.inView = await page.evaluate(() => {
    const view = document.querySelector('.chatMessages').getBoundingClientRect();
    const row = [...document.querySelectorAll('.chatBubbleRow.ai')].at(-1);
    const inside = (el) => { const b = el?.getBoundingClientRect(); return !!b && b.top >= view.top - 1 && b.bottom <= view.bottom + 1; };
    return { pill: inside(row.querySelector('.chatSteps')), table: inside(row.querySelector('table')) };
  });
  take.details.table = await reply2.locator('table').first().evaluate((t) => ({
    rows: [...t.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map((td) => td.innerText.replace(/\s+/g, ' ').trim())),
    overflow: t.scrollWidth > t.clientWidth + 1 }));
  // The finished table, held in the edit before the loop.
  await sleep(2500);

  take.mark('end');
  await take.stop();
  await page.screenshot({ path: path.join(DIR, 'final.png') });

  // ---- what was shown is what was saved
  const saved = await account.api(`/api/chats/${pageId}`);
  fs.writeFileSync(path.join(DIR, 'saved-chat.json'), JSON.stringify(saved, null, 1));
  const users = (saved.messages || []).filter((m) => m.role === 'user');
  const ais = (saved.messages || []).filter((m) => m.role === 'ai');
  const log = (await model.log()).filter((e) => e.stream);
  take.details.requests = requests;
  take.details.modelLog = log;
  take.details.problems = problems;
  take.check('the cited passage was off screen before the click', take.details.passageBefore.length > 0 && take.details.passageBefore.every((b) => b.top > b.paneBottom), take.details.passageBefore);
  const pills = ['searchingShown', 'searchingGone', 'fetchingShown', 'fetchingGone', 'a2Resp', 'a2Settled'];
  take.check('each tool label rested on screen', pills.every((k) => k in take.marks)
    && take.marks.searchingShown < take.marks.searchingGone && take.marks.searchingGone <= take.marks.fetchingShown && take.marks.fetchingShown < take.marks.fetchingGone
    && take.marks.fetchingGone <= take.marks.a2Resp, pills.map((k) => take.marks[k]));
  take.check('figure attached to question 1', requests[0]?.images.length === 1 && requests[0].images[0].kind === 'area' && requests[0].images[0].page === 2
    && (users[0]?.images?.length || 0) === 1 && log[0]?.images === 1, requests[0]?.images);
  const c1s = citationsIn(ais[0]?.text);
  const shown1 = parseGammaLink(href1, server.base);
  take.check('answer 1 cites p. 2 exactly', c1s.length >= 1 && c1s.every((c) => c.exact && c.page === 2) && shown1?.page === 2 && exact(2, shown1.quote), c1s);
  take.check('citation click marks the passage without a notice', take.details.cite1Marks > 0 && !take.details.cite1Notice.length, { marks: take.details.cite1Marks, notice: take.details.cite1Notice });
  take.check('passage chip sent with question 2', requests[1]?.selections.length === 1 && CLAUSE_RE.test(requests[1].selections[0].text.replace(/\s+/g, ' ').trim()) && requests[1].selections[0].page === 1, requests[1]?.selections);
  take.check('question 2 is a page agent chat without the read helper', requests[1]?.agent_scope === 'page' && requests[1]?.delegate_reads === false && requests[1]?.page_id === pageId);
  const actions = ais[1]?.actions || [];
  const searches = actions.filter((a) => a.tool === 'search_papers'), fetches = actions.filter((a) => a.tool === 'fetch_paper');
  take.details.actions = actions.map((a) => ({ tool: a.tool, args: a.args, error: !!a.error, summary: a.summary, ms: a.ms }));
  take.check('3 searches and 3 fetches ran without error', searches.length === 3 && fetches.length === 3 && actions.length === 6
    && actions.every((a) => !a.error) && searches.every((a) => !/— 0 results|no results/i.test(a.summary || '')), take.details.actions);
  take.check('every fact grounded in its paper', log.at(-1)?.grounded === true, log.at(-1)?.grounding);
  const c2s = citationsIn(ais[1]?.text);
  take.check('p. 6 citations exact', c2s.length === 3 && c2s.every((c) => c.exact && c.page === 6), c2s);
  // A busy machine drops screencast frames and slows the pointer: the
  // pointer's moves (a repaint every 16 ms while it travels) then stutter in
  // the clip, so such a take is recorded again.
  {
    const ts = JSON.parse(fs.readFileSync(path.join(DIR, 'frames', 'frames.json'), 'utf8')).frames.map((f) => f.t);
    const fps = (a, b) => ts.filter((t) => t >= a && t <= b).length / Math.max(0.1, b - a);
    const rates = { drag: fps(take.marks.dragStart, take.marks.drop), toChip: fps(take.marks.toChip, take.marks.toChip + 0.5), toPassage: fps(take.marks.toPassage, take.marks.toPassage + 0.6) };
    const dragMs = (take.marks.drop - take.marks.dragStart) * 1000;
    take.check('the capture kept up (>= 18 fps while the pointer moves, the drag on time)', Object.values(rates).every((r) => r >= 18) && dragMs < 1600, { rates, dragMs });
  }
  take.check('table has 3 rows', take.details.table.rows.length === 3 && !take.details.table.overflow, take.details.table);
  take.check('the steps pill and the table in view at the end', take.details.inView.pill && take.details.inView.table, take.details.inView);
  // After a reload: both questions and both answers are there.
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('.chatBubbleRow.ai').nth(1).waitFor({ timeout: 20000 }).catch(() => {});
  const after = { user: await page.locator('.chatBubbleRow.user').count(), ai: await page.locator('.chatBubbleRow.ai').count(),
    table: await page.locator('.chatBubbleRow.ai').last().locator('table tbody tr').count() };
  take.check('chat persisted after reload', users.length === 2 && ais.length === 2 && after.user === 2 && after.ai === 2 && after.table === 3, after);
  await take.finish();
} catch (error) {
  if (error.inspect) console.log(error.message);
  else await take.fail(error);
} finally {
  await browser?.close().catch(() => {});
  model?.close();
  await server?.stop().catch(() => {});
}
