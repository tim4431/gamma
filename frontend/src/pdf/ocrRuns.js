// The scans this window reads the pages of itself (docs/dev/pdf_loading.md
// "Recognized words"), where the server has no engine of its own
// (/api/server-config's `ocr.where` is "window"): one run at a time, since
// each holds an engine of some 100 MB and a page's picture, and an iPad
// ends a page that holds two. A later start waits for its turn. Module
// state, like pdf/ocrState.js, read through `status` and `subscribe`; the
// Document menu starts and follows a run here, the job's own row in
// Background tasks shows it to every device.
//
// The engine and its loop are a lazy chunk (pdf/ocrTab.js, tesseract.js),
// fetched by the first run; `start` takes the loader as an argument, so the
// node tests hand in a fake one and never load the engine. While a run goes
// the screen is kept on (shared/lib/host.js keepAwake): the run reads pages
// only while the window is in front, which the pill says for the whole run
// (`followPill`, its Stop this run's). `stop` ends it and stops its job
// through the tasks store; a run waiting its turn has no job yet, and a row
// of this tab's own in Background tasks, whose Stop calls it off. The job
// row's Stop (in Background tasks) ends this window's run of it at once
// (`jobStopped`, tasks/taskKinds.js `halt`). A run that fails on this
// window's side stops its job too, so the job ends at once and what was
// read waits for the next run. `reset`, at the session's end, ends every
// run here without a word to the server, whose job then stops on its own.

import { t } from "../shared/i18n/i18n.js";
import { keepAwake } from "../shared/lib/host.js";

// This window's id for the server's run (the job's `window.client`): a run
// is this window's own, and a page sent from another window is refused.
const CLIENT = (() => {
  try {
    return globalThis.crypto.randomUUID().replaceAll("-", "");
  } catch {
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  }
})();
export const windowClient = () => CLIENT;

// docId → its run: {docId, pageId, title, tasks, capability, meta, load,
// controller, status, promise, resolve, jobId, stopping (asked to stop),
// stoppedJob (the job its stop went to), row (its own row in Background
// tasks while it waits its turn), over}.
const runs = new Map();
// The runs waiting for their turn, the oldest first, and the one going.
const queue = [];
let current = null;
const listeners = new Set();
const emit = () => { for (const fn of listeners) fn(); };

const ENDS = new Set(["done", "stopped", "failed"]);

// Read `docId`'s pages in this window: `pageId` the page that shows the
// stored file `docId`, `title` its name, `tasks` the tasks store (the job
// starts and stops through it), `capability` the server's `ocr`, `meta`
// what tasks.start remembers for the job. Resolves with the run's last
// status once it ends; a start of a document whose run is waiting or going
// is that run. `load`: the engine's chunk.
export function start({ docId, pageId, title = "", tasks = null, capability = null, meta = {} }, { load = () => import("./ocrTab.js") } = {}) {
  const known = runs.get(docId);
  if (known && !known.over) return known.promise;
  const run = {
    docId, pageId, title, tasks, capability, meta, load,
    controller: new AbortController(), jobId: null, stopping: false, over: false,
    status: Object.freeze({ docId, title, phase: current || queue.length ? "waiting" : "starting", page: 0, done: 0, total: 0, jobId: null, error: "" }),
  };
  run.promise = new Promise((resolve) => { run.resolve = resolve; });
  runs.set(docId, run);
  queue.push(run);
  // waiting: a row of its own, which its job's takes over at its turn
  if (run.status.phase === "waiting" && tasks?.addLocal) {
    run.row = tasks.addLocal({ kind: "ocr-tab", name: t("Reading the text of “{title}” in a window", { title: title || t("Untitled") }),
      info: t("Waiting for its turn…"), cancel: () => stop(docId) });
  }
  emit();
  next();
  return run.promise;
}

// The row of a run that waited, gone at its turn: the job's own row and
// the pill show it from then on. One stopped keeps its row, as stopped.
function dropRow(run, stopped = false) {
  const { row, tasks } = run;
  if (!row) return;
  run.row = null;
  try {
    if (stopped) tasks.updateLocal?.(row, { state: "cancelled", info: t("stopped"), cancel: null });
    else tasks.removeLocal?.(row);
  } catch { /* the store is gone (the session's end) */ }
}

function next() {
  if (current || !queue.length) return;
  current = queue.shift();
  keepAwake(true);
  dropRow(current);
  if (current.status.phase === "waiting") update(current, { phase: "starting" });
  else emit(); // it goes (`going`)
  go(current);
}

async function go(run) {
  let end;
  try {
    let engine;
    try {
      engine = await run.load();
    } catch {
      throw new Error(t("The text recognition engine could not be loaded: reload the page."));
    }
    end = run.controller.signal.aborted ? { state: "stopped" } : await engine.run({
      docId: run.docId, pageId: run.pageId, capability: run.capability, client: CLIENT,
      tasks: run.tasks, meta: run.meta, signal: run.controller.signal, report: (patch) => update(run, patch),
    });
  } catch (err) {
    end = run.controller.signal.aborted ? { state: "stopped" } : { state: "failed", error: err?.message || t("failed") };
  }
  finish(run, end);
}

// A run's status moves on. A job started after its run was stopped (the
// start was on its way) is stopped as it comes.
function update(run, patch) {
  if (patch.jobId && patch.jobId !== run.jobId) {
    run.jobId = patch.jobId;
    if (run.stopping) stopJob(run);
  }
  if (run.over || runs.get(run.docId) !== run) return;
  run.status = Object.freeze({ ...run.status, ...patch });
  emit();
}

// A run's end: its last status kept until the document's next start or the
// session's end, the next run's turn. One that failed on this window's side
// (`local`: the engine, the document) stops the job it leaves waiting.
function finish(run, end) {
  if (run.over) return;
  if (runs.get(run.docId) === run) update(run, { phase: ENDS.has(end?.state) ? end.state : "failed", error: end?.error || "" });
  run.over = true;
  dropRow(run, end?.state === "stopped");
  if (end?.state === "failed" && end.local) stopJob(run);
  const i = queue.indexOf(run);
  if (i >= 0) queue.splice(i, 1);
  if (current === run) current = null;
  if (!current && !queue.length) keepAwake(false);
  run.resolve(run.status);
  emit(); // over: no run goes, until the next one's turn
  next();
}

function stopJob(run) {
  const { jobId, tasks } = run;
  if (!jobId || run.stoppedJob === jobId) return Promise.resolve();
  run.stoppedJob = jobId;
  return Promise.resolve()
    .then(() => tasks?.cancel?.({ id: jobId, kind: "ocr-tab", local: false }))
    .catch(() => { /* over already, or the server away: it stops on its own */ });
}

// Stop `docId`'s run, waiting or going: its engine ends at once, its job
// is stopped (Background tasks shows it so), and what it read stays for
// the next run. Resolves once the server has heard.
export function stop(docId) {
  const run = runs.get(docId);
  if (!run || run.over) return Promise.resolve();
  run.stopping = true;
  run.controller.abort();
  const told = stopJob(run);
  finish(run, { state: "stopped" });
  return told;
}

// The job `jobId` is being stopped (its row's Stop in Background tasks,
// which stops it on the server): this window's run of it, if any, ends at
// once, its engine and document with it, instead of at its next request.
export function jobStopped(jobId) {
  const run = jobId ? [...runs.values()].find((r) => !r.over && r.jobId === jobId) : null;
  if (!run) return;
  run.stopping = true;
  run.stoppedJob = jobId; // stopped by the caller
  run.controller.abort();
  finish(run, { state: "stopped" });
}

// Whether `docId`'s run waits or goes in this window (any document's,
// without one).
export function running(docId) {
  if (docId === undefined) return [...runs.values()].some((run) => !run.over);
  const run = runs.get(docId);
  return !!run && !run.over;
}

// `docId`'s run as it is, or as it ended: {docId, title, phase ("waiting",
// "starting", "reading", "recognizing", then "done", "stopped" or
// "failed"), page (the one being read), done, total (the server's count of
// the pages read and to read), jobId, error}; null when it has none.
export function status(docId) {
  return runs.get(docId)?.status || null;
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

// The status of the run that goes in this window now (not one waiting its
// turn), or null.
export function going() {
  return current && !current.over ? current.status : null;
}

// The pill of the run that goes in this window, for the whole run: the
// window must stay open and in front, and the pill's Stop ends the run
// (`stop`), what it read kept for the next. `post` is App's postPill, on the
// channel `ocr:<docId>`. Returns the function that stops following, which
// takes the pill down.
export function followPill(post) {
  let shown = "";
  const sync = () => {
    const run = going();
    const docId = run?.docId || "";
    if (docId === shown) return;
    if (shown) post(`ocr:${shown}`, null);
    shown = docId;
    if (!run) return;
    post(`ocr:${docId}`, {
      msg: t("Reading the pages of “{title}” in this window: keep it open and in front until it is done. A stop keeps what was read.",
        { title: run.title || t("Untitled") }),
      spinner: true,
      action: { label: t("Stop"), run: () => { stop(docId); } },
    });
  };
  sync();
  const off = subscribe(sync);
  return () => {
    off();
    if (shown) post(`ocr:${shown}`, null);
    shown = "";
  };
}

// The session's end (logout, another account): every run ends here, its
// engine and document with it, and no request goes out on the next
// account's behalf; the server's job stops by itself once no page comes.
export function reset() {
  const all = [...runs.values()];
  runs.clear();
  queue.length = 0;
  current = null;
  for (const run of all) {
    if (run.over) continue;
    run.over = true;
    run.controller.abort();
    dropRow(run);
    run.resolve(Object.freeze({ ...run.status, phase: "stopped" }));
  }
  keepAwake(false);
  emit();
}
