// The text the server recognized in a stored PDF's page pictures, as this
// tab knows it (docs/dev/pdf_loading.md "A scan's text layer"): per
// workspace and document (the stored file's hash), which pages read
// recognized words in place of the PDF's own layer, and those pages' words,
// read in chunks as pdf/pageText.js asks for them. Module state, like
// pdf/translateState.js: the viewer registers every document it opens, and
// a page's text, wherever it is read, finds its document here by the
// page's commonObjs, which all of a pdf.js document's pages share.
//
// The state (GET /api/ocr/<doc>) is asked for when the viewer opens the
// document, again each time it opens it, when a recognition or a forget ends
// (`refresh`, tasks/useAppJobs.js), and when the page's socket says the
// file's recognized text changed or may have while it was away (`ocr`,
// collaboration/collabSession.js onOcr). A page waits at most STATE_WAIT_MS for
// the first answer, then reads its own layer until the state comes. What
// the state says of the pages is announced: a local revision, which only
// grows, moves on whenever the recognized pages or their recognition (the
// state's stamp) differ from what was announced, and the translation
// (translateState.textChanged) hears which pages changed. The first answer
// changes only the pages a reader read from their own layer because the
// wait ran out: every other reader waited for it, so a seed or a job that
// waited goes on. The viewer rebuilds a changed page's text layer and its
// Ctrl+F text (useOcrRevision, pageRevision); App re-runs a pinned search
// when the document on screen changes (useOcrNonce). Network errors read
// as no recognized text, unless a state was known.
//
// The Document menu's "Recognize text in this PDF" and "Forget the
// recognized text…" are decided here too (useOcrMenu), so App only places
// their rows; where the server has the pages read in the window that asks,
// the row starts that run here (pdf/ocrRuns.js).

import React from "react";
import { T, fmtDate, t } from "../shared/i18n/i18n.js";
import { isActive } from "../tasks/taskModel.js";
import * as ocrRuns from "./ocrRuns.js";

// How long a page waits for its document's first state before it reads as
// not recognized.
const STATE_WAIT_MS = 3000;
// The recognized pages a request of words asks for at once: the page asked
// for and those nearest it.
const CHUNK = 16;
// The pages' words the tab keeps, of every document together; past this,
// those read longest ago go, whichever document they are of.
const MAX_HELD = 800;
// A run over this many pages or fewer shows its progress in the pill.
const PILL_PAGES = 50;

// What the store reads through, set by the viewer (pdf/PdfViewer.jsx), which
// has the app's fetch helpers: `json(path)` (apiJson: the share token and the
// workspace go with it), `workspace()` (the tab's) and `textChanged(url,
// pages)` (pdf/translateState.js). Unconnected (the node tests that read a
// page's text), no document has recognized pages.
let net = { json: null, workspace: () => "", textChanged: () => {} };
export function connect(parts) {
  net = { ...net, ...parts };
}

// A document's pages → {docId, url, numPages}: the document the viewer
// registered, by what all its pages share (pdf.js gives each page proxy its
// document's commonObjs; the document proxy has them on its transport).
const DOCS = new WeakMap();
// `${workspace}\n${docId}` → the document's entry (`entry`).
const entries = new Map();
// The pages' words held, `${entry key}\n${stamp}\n${page}` → Promise<lines |
// null>, in the order they were last read (MAX_HELD of them at most).
const HELD = new Map();
const listeners = new Set();
// Revisions are handed out from one counter, so none ever repeats.
let lastRev = 0;
const emit = () => { for (const fn of listeners) fn(); };

const NONE = Object.freeze({ stamp: "", recognized: new Set(), kept: new Set(), scanned: null, pages: null, at: "", goodLayer: false,
  todo: null, staged: 0, pending: false });

function entryOf(docId, create = false) {
  if (!docId) return null;
  const key = `${net.workspace() || ""}\n${docId}`;
  let e = entries.get(key);
  if (!e && create) {
    let ready;
    const readyP = new Promise((resolve) => { ready = resolve; });
    e = {
      key, docId, urls: new Set(), numPages: 0,
      state: null, loading: null, ready, first: wait(readyP),
      // the pages read from their own layer while no state was known
      ownRead: new Set(),
      announced: { stamp: "", pages: new Set() }, rev: 0, pageRevs: new Map(),
      noted: false, version: 0,
    };
    entries.set(key, e);
    if (!net.json) arrive(e, NONE);
  }
  return e || null;
}

// The first state, or STATE_WAIT_MS, whichever comes first.
function wait(readyP) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, STATE_WAIT_MS);
    timer?.unref?.();
    readyP.then(() => { clearTimeout(timer); resolve(); });
  });
}

function entryOfPage(page) {
  const doc = page && DOCS.get(page.commonObjs || page);
  if (!doc) return null;
  const known = entryOf(doc.docId);
  const e = known || entryOf(doc.docId, true);
  if (!known) {
    // after a reset, or in another workspace: the document's state afresh
    if (doc.url) e.urls.add(doc.url);
    e.numPages ||= doc.numPages;
    load(e);
  }
  return e;
}

const pageSet = (list) => new Set((Array.isArray(list) ? list : []).filter((n) => Number.isInteger(n) && n > 0));
// A count the state gives as a number, or as the list of its pages; null
// when it has none.
const countOf = (v) => (Array.isArray(v) ? v.length : Number.isInteger(v) && v >= 0 ? v : null);
// `recognized`: the live pages, none before a first apply (no stamp);
// `kept`: the pages a run found reading well, which are done too;
// `goodLayer`: a run found the file's own text reads well, so there is
// nothing to recognize; `todo`: how many pages a run would read now (null
// until a run has read what kind each page is); `staged`: the pages a run
// that stopped recognized, which wait for the next to apply them;
// `pending`: the last apply's finishing steps are still due.
function parseState(a) {
  const stamp = typeof a?.stamp === "string" ? a.stamp : "";
  return {
    stamp,
    recognized: stamp ? pageSet(a.recognized) : new Set(),
    kept: pageSet(a?.kept),
    scanned: Number.isInteger(a?.scanned) ? a.scanned : null,
    pages: Number.isInteger(a?.pages) ? a.pages : null,
    at: typeof a?.at === "string" ? a.at : "",
    goodLayer: a?.good_layer === true,
    todo: countOf(a?.todo),
    staged: countOf(a?.staged) || 0,
    pending: a?.pending === true,
  };
}

function load(e) {
  if (!net.json) return;
  const run = Promise.resolve()
    .then(() => net.json(`/api/ocr/${encodeURIComponent(e.docId)}`))
    .then(parseState, () => null);
  e.loading = run;
  run.then((st) => {
    // reset meanwhile, or asked again: the later answer counts
    if (entries.get(e.key) !== e || e.loading !== run) return;
    e.loading = null;
    if (st) arrive(e, st);
    else if (!e.state) arrive(e, NONE);
  });
}

// A state answered: kept, and what changed of what was announced told.
function arrive(e, st) {
  const was = e.announced;
  const now = st.recognized;
  // The first state changes only the pages read from their own layer
  // because the wait for it ran out: every other reader waited and reads
  // what it says. After it, another recognition (a new stamp) may have
  // changed every page it covers; the same one, only the pages that came
  // or went.
  const changed = (e.state ? [...new Set([...was.pages, ...now])]
    .filter((n) => st.stamp !== was.stamp || was.pages.has(n) !== now.has(n))
    : [...now].filter((n) => e.ownRead.has(n)))
    .sort((a, b) => a - b);
  e.ownRead.clear();
  e.state = st;
  e.announced = { stamp: st.stamp, pages: now };
  // the words of another recognition of the document go
  const mine = `${e.key}\n`, live = `${mine}${st.stamp}\n`;
  for (const k of HELD.keys()) if (k.startsWith(mine) && !k.startsWith(live)) HELD.delete(k);
  e.version += 1;
  e.ready();
  if (changed.length) {
    e.rev = ++lastRev;
    for (const n of changed) e.pageRevs.set(n, e.rev);
    for (const url of e.urls) net.textChanged(url, changed);
  }
  emit();
}

// --- what the viewer and the readers ask -----------------------------------

// The viewer opened `pdfDoc`, the stored file `docId` (a URL document's
// copy under its link's hash, for a PDF shown from its link), at `url`
// (what the translated view keys it by): its pages read what the server
// recognized in it, and its state is asked for afresh. A document with no
// docId reads its own text.
export function register(pdfDoc, docId, url = "") {
  const key = pdfDoc?._transport?.commonObjs || pdfDoc;
  if (!key) return;
  if (!docId) {
    DOCS.delete(key);
    return;
  }
  DOCS.set(key, { docId, url, numPages: pdfDoc.numPages || 0 });
  const e = entryOf(docId, true);
  if (url) e.urls.add(url);
  if (pdfDoc.numPages) e.numPages = pdfDoc.numPages;
  load(e);
}

// The state of `docId` read again (a recognition or a forget of it ended,
// the socket said its recognized text changed), when this tab has the
// document. Always asked: the later answer counts, and one that says
// nothing new announces nothing.
export function refresh(docId) {
  const e = entryOf(docId);
  if (e) load(e);
}

// The page's recognized lines ({lines} as GET /api/ocr/<doc>/pages has
// them: pdf/ocrText.js lays them out), [] for a recognized page with no
// text, or null when the page reads its own layer.
export async function lines(page) {
  const e = entryOfPage(page);
  if (!e) return null;
  if (!e.state) await e.first;
  const st = e.state;
  const n = page.pageNumber;
  if (!st) {
    // the wait ran out: the page reads its own layer, which the first
    // state changes should it recognize the page
    e.ownRead.add(n);
    return null;
  }
  if (!st.stamp || !st.recognized.has(n)) return null;
  return pageLines(e, st, n);
}

function pageLines(e, st, n) {
  const at = (p) => `${e.key}\n${st.stamp}\n${p}`;
  const known = HELD.get(at(n));
  if (known) {
    // read again: the last to go
    HELD.delete(at(n));
    HELD.set(at(n), known);
    return known;
  }
  // n and the recognized pages nearest it not asked for yet, one chunk
  const want = [];
  for (let d = 0; d <= CHUNK && want.length < CHUNK; d++) {
    for (const p of d ? [n + d, n - d] : [n]) {
      if (want.length < CHUNK && st.recognized.has(p) && !HELD.has(at(p))) want.push(p);
    }
  }
  want.sort((a, b) => a - b);
  const query = `first=${want[0]}&last=${want[want.length - 1]}`;
  const asked = Promise.resolve()
    .then(() => net.json(`/api/ocr/${encodeURIComponent(e.docId)}/pages?${query}`))
    .then((a) => {
      if (a?.stamp === st.stamp) return a.pages || {};
      // recognized again since the state was read: read it again, which
      // announces the new recognition
      if (entries.get(e.key) === e && !e.loading) load(e);
      return null;
    }, () => null);
  for (const p of want) {
    const k = at(p);
    const one = asked.then((pages) => {
      const got = pages?.[String(p)]?.lines;
      if (Array.isArray(got)) return got;
      if (HELD.get(k) === one) HELD.delete(k); // asked again next time
      return null;
    });
    HELD.set(k, one);
  }
  // those read longest ago go first; the chunk just asked for is the last
  // read, and a chunk is far fewer than MAX_HELD
  for (const k of HELD.keys()) {
    if (HELD.size <= MAX_HELD) break;
    HELD.delete(k);
  }
  return HELD.get(at(n));
}

// pdf/pageText.js met a scan's layer, or a page with no text, in a
// registered document: the Document menu may offer to recognize it before
// the server has said whether it has scanned pages.
export function noteScan(page) {
  const doc = page && DOCS.get(page.commonObjs || page);
  const e = doc && entryOf(doc.docId);
  if (!e || e.noted) return;
  e.noted = true;
  e.version += 1;
  emit();
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

// The document's local revision: 0 until a change of its pages' text was
// announced, and greater with each announced change.
export function revision(docId) {
  return entryOf(docId)?.rev || 0;
}

// The revision at which page `n` of the document last changed (0: never),
// which its text layer is built under.
export function pageRevision(docId, n) {
  return entryOf(docId)?.pageRevs.get(n) || 0;
}

// `revision`, followed: the caller renders again when it moves on.
export function useOcrRevision(docId) {
  return React.useSyncExternalStore(subscribe, () => revision(docId));
}

// How many times the revision of `docId` (App's: the document on screen)
// moved, as this caller saw it: another document's recognition leaves it,
// and it never goes back when another document comes on screen with a
// lower revision, as App's search nonce must not (SearchPanel compares it).
export function useOcrNonce(docId) {
  const rev = useOcrRevision(docId);
  const seen = React.useRef(null);
  if (!seen.current) seen.current = { rev, n: 0 };
  else if (seen.current.rev !== rev) seen.current = { rev, n: seen.current.n + 1 };
  return seen.current.n;
}

// The session's end (logout, another account): every document's state goes.
export function reset() {
  entries.clear();
  HELD.clear();
  emit();
}

// --- the Document menu -------------------------------------------------------

// The jobs that change a stored file's recognized text (gamma/jobs.py
// OCR_KINDS, one of them at a time per file): a run on the server, one read
// in a window, and a forget.
const OCR_KINDS = new Set(["ocr", "ocr-tab", "ocr-drop"]);
// Which jobs are those of the stored file `docId` (any workspace's): what
// the tasks store looks for when the page's socket says the file's
// recognized text changed (App's onOcr, useTasks' expectEnd).
export const fileJobs = (docId) => (job) => OCR_KINDS.has(job?.kind) && job.params?.doc_id === docId;

// The OCR job of the stored file `docId` in this workspace that waits or
// runs, among `jobs` (the tasks store's: the account's own, and the
// workspace's automatic run), or null.
function activeJob(jobs, docId) {
  const ws = net.workspace() || "";
  return jobs.find((job) => OCR_KINDS.has(job.kind) && isActive(job) && job.params?.doc_id === docId
    && (!job.workspace || !ws || job.workspace === ws)) || null;
}

// A row's title while `job` (null: this window's own run) changes the file's
// text, or while this window's run of it waits for another document's to end
// (`queued`: it has no job yet).
const busyTitle = (job, queued = false) => (job?.kind === "ocr-drop" ? t("This PDF's recognized text is being forgotten — see Background tasks")
  : !job && queued ? t("Waiting for its turn…") : t("This PDF's text is being recognized — see Background tasks"));
// Whether this window's run of `docId` waits for its turn.
const queuedHere = (docId) => ocrRuns.running(docId) && ocrRuns.status(docId)?.phase === "waiting";

// The "Recognize text in this PDF" row of the page's Document menu, or null
// when it has none: offered (`offered`: a writer with an account, a PDF)
// for the stored file `docId` where the server recognizes text
// (`capability`, /api/server-config's `ocr`, null for none; its `where`:
// "server", or "window" when the pages are read in the window that asks)
// while a run has pages to read (`todo`), a stopped run's pages wait to be
// applied (`staged`) or an apply's finishing steps are due (`pending`).
// Before a run has read what kind each page is: while the server counts
// more scanned pages than it recognized or kept, or, before it has read the
// file, once the viewer met a scan's layer or a page with no text in it,
// never once a run found the file's own text reads well. `jobs` (the tasks
// store's) tell an OCR job of this file waiting or running, and `running`
// this window's own run of it (pdf/ocrRuns.js; `queued` while it waits for
// its turn), during which the row waits for it; `translated`: the paper
// holds a translation, made again from the new text on the pages it
// changes. {busy, dropping, title, label, pill, where, docId, capability}:
// `dropping` while the job is a forget; `label` says the run goes on while
// staged pages wait and nothing runs, or while the account's own window
// run of the file goes on with no window here reading it (`elsewhere`: the
// job of a window reloaded since, or of another window of the account),
// which the row moves to this window (the server hands the start that job,
// and the other window's next page is refused); `pill` when the pages to
// recognize are few enough for the pill to follow a server run.
export function menuRow({ docId, offered, capability = null, jobs = [], translated = false, running = ocrRuns.running(docId),
  queued = queuedHere(docId) }) {
  const where = capability?.where || "";
  if (!offered || !docId || !where) return null;
  const e = entryOf(docId);
  const job = activeJob(jobs, docId);
  const inWindow = where === "window";
  const elsewhere = inWindow && !running && readElsewhere(job);
  const busy = (!!job && !elsewhere) || !!running;
  const st = e?.state;
  const waiting = !!st?.pending || st?.staged > 0;
  const left = Number.isInteger(st?.todo) ? st.todo
    : Number.isInteger(st?.scanned) ? st.scanned - st.recognized.size - st.kept.size : null;
  const wanted = Number.isInteger(st?.todo) ? left > 0 || waiting
    : waiting || (!st?.goodLayer && (left === null ? !!e?.noted : left > 0));
  if (!busy && !wanted && !elsewhere) return null;
  const pages = left ?? e?.numPages ?? 0;
  const title = busy ? busyTitle(job, running && queued)
    : elsewhere ? t("Go on reading this PDF's pages in this window, from the pages read: a window reading them now stops. Keep this one open and in front until it is done.")
      : [t("Read the words of this PDF's scanned pages from their pictures: selection, search, the AI and translation read them, and each highlight there keeps its place and takes the new text. The PDF file itself is unchanged."),
        inWindow ? t("The pages are read in this window: keep it open and in front until it is done.") : "",
        translated ? t("Its translation of those pages is made again from the new text when you next translate it.") : ""].filter(Boolean).join(" ");
  const label = !busy && (elsewhere || st?.staged > 0) ? t("Go on recognizing this PDF's text") : t("Recognize text in this PDF");
  return { busy, dropping: job?.kind === "ocr-drop", title, label, pill: pages > 0 && pages <= PILL_PAGES,
    where: inWindow ? "window" : "server", docId, capability };
}

// Whether `job` is the account's own run of the file in a window that is
// not this one (the tasks store lists no one else's), going and not being
// stopped: this window can take it over.
const readElsewhere = (job) => job?.kind === "ocr-tab" && job.state === "running" && !job.stopping
  && job.params?.window?.client !== ocrRuns.windowClient();

// The "Forget the recognized text…" row of the page's Document menu, or
// null when it has none: offered (`offered`, as menuRow's; the server needs
// no engine to forget) while the stored file `docId` has pages that read
// recognized words. It waits, as menuRow's, while an OCR job of the file
// waits or runs (`jobs`) or this window reads its pages (`running`). {busy, title}.
export function dropRow({ docId, offered, jobs = [], running = ocrRuns.running(docId), queued = queuedHere(docId) }) {
  if (!offered || !docId || !entryOf(docId)?.state?.recognized.size) return null;
  const job = activeJob(jobs, docId);
  const busy = !!job || !!running;
  return { busy, title: busy ? busyTitle(job, running && queued)
    : t("Read this PDF's own text again in place of the words recognized from its page pictures, for everyone in this workspace") };
}

// The store and this window's runs, followed together.
function subscribeMenu(fn) {
  const own = subscribe(fn);
  const runs = ocrRuns.subscribe(fn);
  return () => { own(); runs?.(); };
}

// The two rows (menuRow's opts): {recognize, forget}, each null when the
// menu has none; the caller renders again when the document's state or
// this window's run of it changes.
export function useOcrMenu(opts) {
  React.useSyncExternalStore(subscribeMenu,
    () => `${entryOf(opts.docId)?.version || 0}:${ocrRuns.running(opts.docId) ? 1 : 0}:${queuedHere(opts.docId) ? 1 : 0}`);
  return { recognize: menuRow(opts), forget: dropRow(opts) };
}

// Start the recognition of the page's PDF as the menu `row` says: on the
// server (POST /api/jobs/ocr) through the tasks store, which rejects when
// it could not start; or read in this window (`row.where` "window":
// pdf/ocrRuns.js, which starts its own job through `tasks`, a run waiting
// its turn behind another document's) under the page's `title`. A window
// run that fails says why through `say` (the status line): its job's end
// says how it went otherwise (tasks/taskKinds.js ocrEnding), but a run can
// fail before its job starts (this browser, the engine, the server's
// refusal) or after the job ended (its server stopped answering). `runs`:
// pdf/ocrRuns.js, or the tests' own.
export function startRecognizing(tasks, pageId, row, { title = "", say = () => {}, runs = ocrRuns } = {}) {
  if (row?.where !== "window") return tasks.start("ocr", { page_id: pageId }, { pill: !!row?.pill });
  Promise.resolve(runs.start({ docId: row.docId, pageId, title, tasks, capability: row.capability }))
    .then((end) => {
      if (end?.phase !== "failed") return;
      say(t("{name} failed: {message}", { name: t("Reading the text of “{title}” in a window", { title: title || t("Untitled") }),
        message: end.error || t("failed") }));
    }, () => {});
  return Promise.resolve(null);
}

// A window run's job started again (Background tasks' retry of an
// `ocr-tab` row): read in this window where the server still has the pages
// read in the window that asks (`capability`, /api/server-config's `ocr`),
// else a run on the server, which one that now recognizes text itself
// starts (and one that recognizes none refuses, saying why). `say` and
// `runs` as startRecognizing's.
export function recognizeAgain(tasks, task, capability, { say, runs } = {}) {
  const { page_id: pageId = "", doc_id: docId = "", title = "" } = task?.params || {};
  const row = { where: capability?.where === "window" ? "window" : "server", docId, capability, pill: false };
  return startRecognizing(tasks, pageId, row, { title, say, runs });
}

// The confirm box ("Forget the recognized text…") whose Forget starts the
// forget of the page's PDF (POST /api/jobs/ocr-drop) through the tasks
// store, its progress in the pill; `name` names the paper, `setStatus` says
// why it could not start.
export function forgetBox(tasks, pageId, name, setStatus) {
  return {
    title: T("Forget the recognized text?"),
    message: t("The words recognized from the page pictures of “{name}” are forgotten for everyone in this workspace: selection, search, the AI and translation read the PDF's own text again, and a page that is only a picture has no text. Highlights there take the PDF's own text as their quote where it reads under them. Recognizing it again takes the time it took.", { name }),
    confirmLabel: t("Forget"),
    danger: true,
    onConfirm: () => tasks.start("ocr-drop", { page_id: pageId }, { pill: true })
      .catch((err) => setStatus?.(t("Could not start: {message}", { message: err.message }))),
  };
}

// The metadata popover's title for its text cell (GET /api/pdf-text-status,
// `info`), where recognition has a say: its text being recognized (`row`,
// the Document menu's Recognize row, busy with a run), text recognized from
// the page pictures (`info.ocr`, pages that read recognized words), too
// little of it to read, or none to read while the Document menu offers to
// recognize it (the row is there); else null, the popover's own titles.
export function textStatusTitle(info, row) {
  if (!info || info.checking || info.error || !info.found) return null;
  if (row?.busy && !row.dropping) return t("Its text is being recognized from the page pictures — see Background tasks.");
  const canRecognize = !!row;
  const ocr = info.ocr;
  if (ocr?.recognized > 0) {
    const at = new Date(ocr.at || "");
    const date = Number.isNaN(at.getTime()) ? "—" : fmtDate(at, { dateStyle: "medium" });
    return info.ok
      ? t("Text recognized from the page pictures on {date}: the AI and search read it; the PDF file itself is unchanged.", { date })
      : t("The scanned pages were recognized from their pictures on {date}, but they hold little or no readable text.", { date });
  }
  if (!info.ok && canRecognize) return t("No text layer — a scan? Choose Recognize text in this PDF in the Document menu so the AI and search can read it.");
  return null;
}

// What the status of a replaced PDF adds when the server's answer (PUT
// /pages/{id}/attachment) says the old file's text was recognized and the
// new one's is not (`text_not_recognized`), where the Document menu would
// offer to recognize it (`offered`: a writer with an account, a server with
// the engine); else "".
export function replacedNote(answer, offered) {
  return answer?.text_not_recognized && offered
    ? t("The new file's text is not recognized: choose Recognize text in this PDF in the Document menu.") : "";
}
