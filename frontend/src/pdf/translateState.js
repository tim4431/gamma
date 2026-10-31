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
import { pageFaces } from "./pdfFonts.js";
import { segmentPage, textRuns } from "./pdfTranslate.js";
import { HEAD_CHARS, readingOrder } from "./translatePlan.js";
import { createLimit, createTranslateJob } from "./translateJob.js";

// Documents kept, the least recently used going first; one whose job runs
// is never let go.
export const MAX_DOCS = 8;

const IDLE = Object.freeze({ running: false, progress: 0, label: "" });
// What a document without a state shows: no entries, nothing hidden.
const EMPTY = Object.freeze({ entries: new Map(), shown: true, status: IDLE });

// url → {url, view: {entries, shown, status}, paras: Map(pn → blocks),
// job}, in order of use. `view` is replaced on every change, so it is the
// snapshot React compares; a change to a state no longer kept (a job
// halted at the session's end, unwinding) tells no one.
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

// Halting aborts the job's requests in flight, so it unwinds at once and
// keeps what came back.
export function halt(url) {
  docs.get(url)?.job?.halt();
}

// Drop a document's translation, which the server has forgotten too ("Forget
// this translation"): its entries and its last job's status go, its
// segmented blocks stay. Not while a job runs, whose entries would land
// after it.
export function forget(url) {
  const d = docs.get(url);
  if (d && !d.job) update(d, { entries: new Map(), status: IDLE });
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
// this translation" names). All of it when the scope is about that
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
  d = { url, view: EMPTY, paras: new Map(), segmenting: new Map(), job: null };
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
// page segmented ahead of the view the operator list is built once, its
// decoded images let go again unless the page is `near` the one being read.
// The first blocks made for a page stay, since an entry is seeded only from
// the blocks it was made from. A page being segmented is waited for, not
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
  const fresh = [...ids].some((id) => id && !fonts.has(id) && !page.commonObjs.has(id));
  const faces = await pageFaces(page, ids, { known: fonts });
  if (fresh && !near(pn)) page.cleanup();
  const paras = segmentPage(textRuns(tc.items, vp.transform, { faces, styles: tc.styles }));
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

// Start a job over `pages` of the document at `url`, one at a time per
// document: pdf/translateJob.js over this document's blocks and entries,
// segmenting with `doc` (its pdf.js document). `translate` is the chunk
// request; `brief({pageId, head}, signal, onStage)` the request for the
// paper's brief. `scope` is the host's (`scopeOf`):
// the page being translated and whether the server has its PDF. The page
// names the paper: a PDF the server has not stored is read from its opening
// text, which only the brief's request carries, unless there is no page
// (then every request carries it). `near(pn)`: whether a page is close to
// the one being read. The document is `pinned` while the job reads it;
// `released()` hears when it lets the document go. Every job's chunk
// requests share one limit (REQUESTS). Resolves when the job ends; null
// when none started.
export function startJob(url, { doc, pages, label, key, parallel, translate, brief = null, scope = null, near = () => false, released = null }) {
  if (!pages.length || docs.get(url)?.job) return null;
  const d = stateOf(url);
  const segmentAt = (pn) => segment(d, doc, pn, near);
  const { pageId, stored } = scopeOf(url, scope);
  const head = stored ? null : documentHead(doc, segmentAt);
  const job = createTranslateJob({
    pages, label, key, parallel, translate,
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
