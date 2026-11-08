// The translated view's state per document (docs/dev/translation.md "The
// queue"): the blocks its pages were segmented into, the page entries its
// jobs handed back, whether they are shown, its job's status and the job
// itself. Module state rather than the viewer's, so a job runs on when the
// reader leaves the PDF and a document comes back as it was left. Keyed by
// the document's URL, whose content never changes (upload names are content
// hashes), so the segmented blocks stay good when the viewer opens the
// document again.
//
// The viewer (pdf/PdfViewer.jsx) renders a document's `snapshot` through
// useSyncExternalStore and starts its jobs, keeping a pdf.js document in its
// cache while it is `pinned`; the app (app/translateFollow.js) follows every
// document's job through `statuses`.
import { TRANSLATE_PARALLEL_MAX } from "../app/prefDefs.js";
import { isHeldKey } from "../shared/lib/hotkeys.js";
import { pageFaces } from "./pdfFonts.js";
import { segmentPage, textRuns } from "./pdfTranslate.js";
import { readingOrder } from "./pageFlow.js";
import { figureRegions, markFigures } from "./pageFigures.js";
import { markShape } from "./pageShape.js";
import { HEAD_CHARS, createPlanner, runningBlocks } from "./translatePlan.js";
import { results } from "./translateReply.js";
import { createLimit, createTranslateJob, focusPages } from "./translateJob.js";

// Documents kept, the least recently used going first; one whose job runs
// is never let go.
export const MAX_DOCS = 8;

const IDLE = Object.freeze({ running: false, progress: 0, label: "" });
// What a document without a state shows: no entries, nothing hidden.
const EMPTY = Object.freeze({ entries: new Map(), shown: true, status: IDLE });

// url → {url, view: {entries, shown, status}, paras: Map(pn → blocks),
// segmenting: Map(pn → its segmentation under way), job, and what seeding
// from the kept translations holds (`seedPages`): `seeded` ({key, pages}, the
// pages looked up under that key), `seeds` (the seeds under way), `seedCtl`
// (their AbortController) and `epoch` (moved on by a forget)}, in order of use.
// `view` is replaced on every change, so it is the snapshot React compares;
// a change to a state no longer kept (a job halted at the session's end,
// unwinding) tells no one.
const docs = new Map();
const listeners = new Set();
// url → the jobs reading its pdf.js document, which the viewer keeps open
// while there is one. Counted apart from `docs`: a job halted at the
// session's end still reads until it has unwound, and a job started on the
// same document meanwhile must not lose the document when that one ends.
const readers = new Map();
// The chunk requests in flight, all jobs together: as many as one job may
// have, below the account's AI calls (translateJob.js createLimit).
const REQUESTS = createLimit(TRANSLATE_PARALLEL_MAX);
const emit = () => { for (const fn of listeners) fn(); };

// The real font names behind the runs (pdf/pdfFonts.js), per pdf.js
// document: their ids are that open document's own, so a document opened
// again starts afresh, and the names go with the document they belong to.
const FONTS = new WeakMap();

export function subscribe(fn) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

// {entries: Map(pn → entry), shown, status} of a document: the same object
// until something in it changes.
export function snapshot(url) {
  return docs.get(url)?.view || EMPTY;
}

export function entries(url) {
  return snapshot(url).entries;
}

// [url, status] of every document kept, for those following the jobs.
export function statuses() {
  return [...docs].map(([url, d]) => [url, d.view.status]);
}

// A formula token's span (the k-th of block i on page pn): a token the
// model moved across a page join points into the other page's block.
export function span(url, pn, i, k) {
  return docs.get(url)?.paras.get(pn)?.[i]?.spans?.[k - 1] || null;
}

// A page's segmented blocks, or null while it is not segmented.
export function blocks(url, pn) {
  return docs.get(url)?.paras.get(pn) || null;
}

// What the host's button shows of a document's snapshot `view`, under the
// language and model `key`, on the page being read. `pages` counts only pages
// with visible translated text under that key: entries from a previous key
// don't render, and counting them would leave the button a dead show/hide
// toggle after a settings switch instead of translating afresh. `current`:
// the page being read is fully translated under that key, so the button
// toggles show/hide there and translates this page anywhere else (a halted,
// half-done page counts as untranslated, so a click finishes it from the
// cache). A page that is all references, with nothing translated anywhere,
// has nothing to show or hide: there a click runs the job, which says so.
// `translated`: some page holds translated text under any key, which "Forget
// this paper's translations" would drop. `refsLeft`: the blocks of reference lists
// left original under that key, which "Translate references too" asks for.
// `docDone`: each of the document's `numPages` pages is done under that key,
// so "Translate the whole document" has nothing to ask for.
export function buttonState(view, key, curPage, numPages = 0) {
  let pages = 0, translated = false, refsLeft = 0, done = 0;
  for (const [pn, e] of view.entries) {
    if (e.key === key) refsLeft += e.left?.size || 0;
    if (e.key === key && e.done && pn <= numPages) done += 1;
    if (!e.texts.some(Boolean)) continue;
    translated = true;
    if (e.key === key) pages += 1;
  }
  const cur = view.entries.get(curPage);
  const current = !!(cur && cur.key === key && cur.done && (pages > 0 || !cur.left?.size));
  const docDone = numPages > 0 && done === numPages;
  return { running: view.status.running, progress: view.status.progress, shown: view.shown, pages, current, translated, refsLeft, docDone };
}

// The pages of a document with blocks of a reference list left original
// under `key`.
export function pagesLeft(url, key) {
  return [...entries(url)].filter(([, e]) => e.key === key && e.left?.size).map(([pn]) => pn);
}

// The held peek key's handlers (the viewer's window listeners): `set(on)`
// shows the original over the translation while the key is down. The press
// counts only while `anyShown()`, and only then is the key's default stopped
// (a bare Alt focusing the browser menu bar), but its release always ends the
// peek, since the last overlay can go while the key is held.
export function peekKeys(key, anyShown, set) {
  return {
    down: (e) => { if (isHeldKey(e, key) && anyShown()) { e.preventDefault(); set(true); } },
    up: (e) => {
      if (!isHeldKey(e, key)) return;
      if (anyShown()) e.preventDefault();
      set(false);
    },
  };
}

// Whether blocks a job could not translate under `key` are left ones: only a
// job with `references` asks for those, so asking again takes one too.
export function referencesFailed(url, key) {
  return [...entries(url).values()].some((e) => e.key === key && [...(e.failed || [])].some((i) => e.left?.has(i)));
}

// Whether a job is reading the document at `url` (the viewer's DOC_CACHE
// keeps it open meanwhile).
export function pinned(url) {
  return readers.has(url);
}

// The document is in use (on screen): it goes last.
export function touch(url) {
  const d = docs.get(url);
  if (!d) return;
  docs.delete(url);
  docs.set(url, d);
}

// Show or hide a document's translation, on all its pages at once.
export function setShown(url, shown) {
  const d = docs.get(url);
  if (d && d.view.shown !== shown) update(d, { shown });
}

// Show the original of `blocks` ([{pn, i}], a paragraph's unit) in place of
// their translation, or the translation again (`on` false): the page entries'
// `original` sets, which the overlay draws nothing over, so the text layer
// works there. Only entries under `key`. The reader's choice for the session:
// a job carries it (translateJob.js), a block asked for again drops it, and
// Forget drops it with the entries.
export function setOriginal(url, key, blocks, on) {
  const d = docs.get(url);
  if (!d) return;
  const changed = new Map();
  for (const { pn, i } of blocks) {
    const e = changed.get(pn) || d.view.entries.get(pn);
    if (!e || e.key !== key || !!e.original?.has(i) === on) continue;
    const original = new Set(e.original || []);
    if (on) original.add(i);
    else original.delete(i);
    const { original: _, ...rest } = e;
    changed.set(pn, original.size ? { ...rest, original } : rest);
  }
  if (changed.size) update(d, { entries: new Map([...d.view.entries, ...changed]) });
}

// The blocks of the unit block `i` on page `pn` is part of, as a job under
// `key` asking for it alone plans it (translatePlan planBlock) from the pages
// segmented so far, a page beside it holding another key's translation left
// out (translateJob focusPages): [{pn, i}], the block alone when it is no part
// of one.
export function unitOf(url, pn, i, key) {
  const d = docs.get(url);
  if (!d?.paras.get(pn)) return [{ pn, i }];
  const pages = new Set(focusPages(pn, key, (q) => d.view.entries.get(q)));
  const planner = createPlanner({ paras: (q) => d.paras.get(q) || null, inJob: (q) => pages.has(q) });
  return planner.planBlock(pn, i)?.targets || [{ pn, i }];
}

// Halting aborts the job's requests in flight, so it unwinds at once and
// keeps what came back.
export function halt(url) {
  docs.get(url)?.job?.halt();
}

// Drop a document's translation, which the server has forgotten too ("Forget
// this paper's translations"): its entries and its last job's status go, its
// segmented blocks stay. A seed under way is stopped, and one whose lookup
// answers after this is dropped (its `epoch` has moved on). Not while a job
// runs, whose entries would land after it.
export function forget(url) {
  const d = docs.get(url);
  if (!d || d.job) return;
  stopSeeds(d);
  d.epoch += 1;
  d.seeded = null;
  update(d, { entries: new Map(), status: IDLE });
}

// Every document's translation, which the server has forgotten for the
// whole workspace (Settings › Clear kept translations), dropped as `forget`
// drops one; a document whose job runs keeps what it lands.
export function forgetAll() {
  for (const url of [...docs.keys()]) forget(url);
}

// What the host's `scope` says of the document at `url`. The scope is
// {url, pageId, stored, file}: the host's page, the PDF it shows, whether
// the server has that PDF to read the paper from, and the page's file, the
// doc id the server keeps the paper's translations under (which "Forget
// this paper's translations" names). All of it when the scope is about that
// document, else no page and no file: the viewer shows the last document
// while the next one loads, and the host has moved on to the next page
// already, whose id would name the wrong paper (its brief carried, the
// translations kept under it) and whose file the wrong one to forget.
export function scopeOf(url, scope) {
  return scope?.url === url
    ? { pageId: scope.pageId || "", stored: !!scope.stored, file: scope.file || "" }
    : { pageId: "", stored: false, file: "" };
}

// The session's end (logout, another account or workspace): every job
// halts, its requests in flight aborted, so none goes out on the next
// session's cookie, and every document's state goes, so the next account
// sees none of this one's translations. A halted job still unwinds as
// usual (its document let go), into a state no longer kept.
export function reset() {
  const jobs = [...docs.values()].map((d) => d.job).filter(Boolean);
  for (const d of docs.values()) stopSeeds(d);
  docs.clear();
  for (const job of jobs) job.halt();
  emit();
}

function update(d, patch) {
  d.view = { ...d.view, ...patch };
  if (docs.get(d.url) === d) emit();
}

function stateOf(url) {
  let d = docs.get(url);
  if (d) {
    touch(url);
    return d;
  }
  d = { url, view: EMPTY, paras: new Map(), segmenting: new Map(), job: null, seeded: null, seeds: new Set(), seedCtl: null, epoch: 0 };
  docs.set(url, d);
  trim();
  return d;
}

// Past MAX_DOCS, the oldest documents without a running job go, never the
// one used last.
function trim() {
  const last = [...docs.keys()].at(-1);
  let dropped = false;
  for (const [url, d] of docs) {
    if (docs.size <= MAX_DOCS) break;
    if (d.job || url === last) continue;
    docs.delete(url);
    dropped = true;
  }
  if (dropped) emit();
}

// A page's blocks, segmented once per document. The real font names tell a
// formula's runs (pdf/pdfMath.js): a page drawn already has them, and for a
// page segmented ahead of the view the operator list is built once. The
// figures on a page with text to translate come from that list too
// (pdf/pageFigures.js), so a page not `near` the one being read lets its
// decoded images go again. The first blocks made for a page stay, since an
// entry is seeded only from the blocks it was made from, so they are marked
// here, before they are kept. A page being segmented is waited for, not
// segmented again: a job reads the document's opening text while it
// segments its own pages.
function segment(d, doc, pn, near) {
  if (d.paras.has(pn)) return Promise.resolve(d.paras.get(pn));
  if (!d.segmenting.has(pn)) {
    d.segmenting.set(pn, segmentNow(d, doc, pn, near).finally(() => d.segmenting.delete(pn)));
  }
  return d.segmenting.get(pn);
}

async function segmentNow(d, doc, pn, near) {
  if (!doc || pn < 1 || pn > doc.numPages) return [];
  const page = await doc.getPage(pn);
  const vp = page.getViewport({ scale: 1 });
  const tc = await page.getTextContent();
  let fonts = FONTS.get(doc);
  if (!fonts) FONTS.set(doc, (fonts = new Map()));
  const ids = new Set(tc.items.map((it) => it.fontName));
  const faces = await pageFaces(page, ids, fonts);
  const paras = segmentPage(textRuns(tc.items, vp.transform, { faces, styles: tc.styles }));
  if (paras.some((p) => p.translate)) markShape(paras, markFigures(paras, await figureRegions(page)));
  if (!near(pn)) page.cleanup();
  if (!d.paras.has(pn)) d.paras.set(pn, paras);
  return d.paras.get(pn);
}

// The document's opening text, for a PDF the server has not stored: it
// makes the paper's brief from it (backend gamma/translate_context.py), and
// knows a paper without a page by it.
async function documentHead(doc, segmentAt) {
  let text = "";
  for (let pn = 1; pn <= Math.min(3, doc.numPages) && text.length < HEAD_CHARS; pn++) {
    let paras = [];
    try { paras = await segmentAt(pn); } catch {}
    for (const i of readingOrder(paras)) text += `${paras[i].text}\n`;
  }
  return text.slice(0, HEAD_CHARS);
}

// --- Seeding from the kept translations ------------------------------------
// (docs/dev/translation.md "A paper opened again")

// The lookup (POST /api/translations/lookup) takes as much as a translation
// request: at most this many texts, of this many characters in all.
const LOOKUP_TEXTS = 200;
const LOOKUP_CHARS = 60000;

// `texts` → the lookups they go in, each within the caps. A text past the
// characters' cap alone was never sent either.
export function lookupBatches(texts, maxTexts = LOOKUP_TEXTS, maxChars = LOOKUP_CHARS) {
  const batches = [];
  let cur = [], chars = 0;
  for (const t of texts) {
    if (t.length > maxChars) continue;
    if (cur.length && (cur.length >= maxTexts || chars + t.length > maxChars)) {
      batches.push(cur);
      cur = [];
      chars = 0;
    }
    cur.push(t);
    chars += t.length;
  }
  if (cur.length) batches.push(cur);
  return batches;
}

// The units a job could have sent for `pages` (`paras(pn)`: a page's blocks,
// null while not segmented), the forms of a paragraph a page break cut both
// there: first as a whole-document job plans them, joined with the page
// beside it, then as a job over one page plans them, split at the break.
// Which was sent depended on what was segmented then. Planned with no
// `leave`, so a reference list asked for with "Translate references too"
// comes back too; and each block of a figure's text as the unit of its own
// "Translate this paragraph" sent, which no page's plan holds.
export function seedUnits(paras, pages) {
  const joined = createPlanner({ paras });
  const units = pages.flatMap((pn) => joined.planPage(pn));
  for (const pn of pages) units.push(...createPlanner({ paras, inJob: (q) => q === pn }).planPage(pn));
  for (const pn of pages) {
    (paras(pn) || []).forEach((p, i) => {
      const unit = p.figure ? joined.planBlock(pn, i) : null;
      if (unit) units.push(unit);
    });
  }
  return units;
}

// A paper this browser last left showing its translation under `key`, the
// translation the server keeps of it put back on `pages` with no model call:
// each page and those beside it segmented (with `doc`, as a job does), its
// units planned (`seedUnits`), their texts looked up (`lookup(texts, signal)`,
// resolving to a translation or null per text) and each block still without a
// result filled from the first unit found that holds it, the joined form
// first. A block of a reference list nothing was found for is left, as a job
// leaves it, on a page that got something or had an entry under the key. A
// block nothing was found for stays pending, so the page is not done and the
// button asks for the rest. A page where nothing but its running head or foot
// was found (translatePlan.runningBlocks: the same on every page, kept from
// another) gets no entry, unless all it has besides is a reference list; a
// figure's text found is the page's own, whatever its place.
// Nothing is shown or hidden here: what the view
// shows is the reader's. A page looked up under the key, or done, is not
// looked up again; a page holding another key's translation is left alone. No
// seed while a job runs; a job started aborts the seeds under way, a forget
// drops what they bring after it, and so does another key. Resolves when every
// seed of the document under way has ended.
export function seedPages(url, { doc, key, pages, lookup, near = () => false }) {
  const d = stateOf(url);
  if (d.job || !doc || !key) return Promise.resolve();
  if (d.seeded?.key !== key) d.seeded = { key, pages: new Set() };
  const seeded = d.seeded;
  const want = [...new Set(pages)].filter((pn) => {
    if (pn < 1 || pn > doc.numPages || seeded.pages.has(pn)) return false;
    const e = d.view.entries.get(pn);
    return !e || (e.key === key && !e.done);
  });
  if (want.length) {
    for (const pn of want) seeded.pages.add(pn);
    d.seedCtl ||= new AbortController();
    const run = seedNow(d, { doc, key, pages: want, lookup, near, signal: d.seedCtl.signal, seeded })
      .finally(() => d.seeds.delete(run));
    d.seeds.add(run);
  }
  return Promise.all([...d.seeds]).then(() => {});
}

function stopSeeds(d) {
  d.seedCtl?.abort();
  d.seedCtl = null;
}

async function seedNow(d, { doc, key, pages, lookup, near, signal, seeded }) {
  const epoch = d.epoch;
  // Aborted by a job: those pages may be looked up once it has ended.
  const stopped = () => {
    if (!signal.aborted) return false;
    for (const pn of pages) seeded.pages.delete(pn);
    return true;
  };
  const found = new Map();
  let units;
  try {
    for (const pn of new Set(pages.flatMap((p) => [p, p - 1, p + 1]))) {
      if (stopped()) return;
      try { await segment(d, doc, pn, near); } catch {}
    }
    const paras = (pn) => d.paras.get(pn) || null;
    units = seedUnits(paras, pages.filter((pn) => d.paras.has(pn)));
    for (const batch of lookupBatches([...new Set(units.map((u) => u.src).filter(Boolean))])) {
      if (stopped()) return;
      const got = await lookup(batch, signal);
      batch.forEach((t, k) => found.set(t, typeof got?.[k] === "string" ? got[k] : null));
    }
  } catch {
    // A lookup that failed: the original shows, and the button asks.
    stopped();
    return;
  }
  if (stopped() || d.epoch !== epoch || d.job || d.seeded !== seeded) return;

  const entries = d.view.entries;
  // pn → the page's results as they are being filled; null for a page not
  // to touch (not segmented, or another key's).
  const outs = new Map();
  const outOf = (pn) => {
    if (!outs.has(pn)) {
      const e = entries.get(pn), blocks = d.paras.get(pn);
      outs.set(pn, !blocks ? null : !e ? [] : e.key === key && e.paras === blocks ? e.texts.slice() : null);
    }
    return outs.get(pn);
  };
  const filled = new Map();
  for (const u of units) {
    const text = found.get(u.src);
    if (typeof text !== "string") continue;
    for (const r of results(u, text)) {
      const out = outOf(r.pn);
      if (!out || out[r.i] !== undefined) continue;
      out[r.i] = r.text;
      if (!filled.has(r.pn)) filled.set(r.pn, new Set());
      filled.get(r.pn).add(r.i);
    }
  }
  const leftOf = new Map();
  const lists = createPlanner({
    paras: (pn) => d.paras.get(pn) || null,
    filled: (pn, i) => outOf(pn)?.[i] !== undefined,
    leave: (pn, i) => {
      if (!leftOf.has(pn)) leftOf.set(pn, new Set());
      leftOf.get(pn).add(i);
    },
  });
  for (const pn of pages) {
    if (outOf(pn) && (filled.has(pn) || entries.get(pn)?.key === key)) lists.planPage(pn);
  }
  // A page's running head and foot are the same text on every page, kept
  // from any page translated: where nothing else was found and the page has
  // more to translate than its reference list, they tell nothing of it, and
  // it gets no entry (it would count as translated, and its export would
  // write translated heads around an original body). A page of references a
  // job finished keeps them.
  const running = runningBlocks((pn) => d.paras.get(pn) || null, [...d.paras.keys()]);
  for (const [pn, got] of filled) {
    const heads = [...got].every((i) => running.has(`${pn}:${i}`) && !d.paras.get(pn)[i].figure);
    if (entries.get(pn)?.key === key || !heads) continue;
    const left = leftOf.get(pn);
    if (d.paras.get(pn).some((p, i) => p.translate && !got.has(i) && !left?.has(i))) {
      filled.delete(pn);
      leftOf.delete(pn);
    }
  }
  const changed = new Map();
  for (const [pn, out] of outs) {
    if (!out || !(filled.has(pn) || leftOf.has(pn))) continue;
    const e = entries.get(pn), blocks = d.paras.get(pn);
    const left = new Set(e?.left || []), failed = new Set(e?.failed || []);
    for (const i of leftOf.get(pn) || []) { out[i] = null; left.add(i); }
    for (const i of filled.get(pn) || []) failed.delete(i);
    const done = blocks.every((p, i) => !p.translate || out[i] !== undefined);
    changed.set(pn, { key, paras: blocks, texts: out, done, queued: false, busy: null, partial: null, failed, left,
                      ...(e?.original && { original: e.original }) });
  }
  if (changed.size) update(d, { entries: new Map([...d.view.entries, ...changed]) });
}

// Start a job over `pages` of the document at `url`, one at a time per
// document: pdf/translateJob.js over this document's blocks and entries,
// segmenting with `doc` (its pdf.js document). `translate` is the chunk
// request; `brief({pageId, head}, signal, onStage)` the request for the
// paper's brief. `scope` is the host's (`scopeOf`):
// the page being translated and whether the server has its PDF. The page
// names the paper: a PDF the server has not stored is read from its opening
// text, which only the brief's request carries, unless there is no page
// (then every request carries it). `near(pn)`: whether a page is close to
// the one being read. `references`: the job translates the reference lists
// too. `focus` ({pn, i}) and `fresh`: the job asks for that block's unit
// alone, afresh (translateJob.js), over `pages` [pn]. The document is
// `pinned` while the job reads it; `released()` hears when it lets the
// document go. Every job's chunk requests share one limit (REQUESTS). With no
// pages to ask for, what the document holds shows, as a job would show it.
// Resolves when the job ends; null when none started.
export function startJob(url, { doc, pages, label, key, parallel, references = false, focus = null, fresh = false, translate, brief = null, scope = null, near = () => false, released = null }) {
  if (docs.get(url)?.job) return null;
  if (!pages.length) {
    setShown(url, true);
    return null;
  }
  const d = stateOf(url);
  // A seed under way gives way: the job asks for what it finds missing.
  stopSeeds(d);
  const segmentAt = (pn) => segment(d, doc, pn, near);
  const { pageId, stored } = scopeOf(url, scope);
  const head = stored ? null : documentHead(doc, segmentAt);
  const job = createTranslateJob({
    pages, label, key, parallel, references, focus, fresh, translate,
    brief: brief && (pageId || head)
      ? async (signal, onStage) => brief({ pageId, head: head ? await head : "" }, signal, onStage) : null,
    segment: segmentAt,
    cachedParas: (pn) => d.paras.get(pn) || null,
    seed: (pn) => d.view.entries.get(pn),
    context: pageId || !head ? { pageId } : head.then((text) => ({ head: text })),
    onPages: (changed) => update(d, { entries: new Map([...d.view.entries, ...changed]) }),
    onStatus: (status) => update(d, { status }),
    limit: REQUESTS,
  });
  d.job = job;
  readers.set(url, (readers.get(url) || 0) + 1);
  update(d, { shown: true });
  return job.run().finally(() => {
    d.job = null;
    const n = readers.get(url) - 1;
    if (n > 0) readers.set(url, n);
    else readers.delete(url);
    released?.();
    trim();
  });
}
