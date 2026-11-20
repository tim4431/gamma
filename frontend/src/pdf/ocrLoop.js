// A scan's pages read in this window (docs/dev/pdf_loading.md "Recognized
// words"), where the server has no engine of its own: the run of the
// server's `ocr-tab` job, from its start to the window's last page. Its
// parts are handed in, so node tests it against a fake server
// (tests/ocrLoop.test.mjs); pdf/ocrTab.js, the lazy chunk, hands in the
// browser's.
//
// 1. The job starts (POST /api/jobs/ocr with the window's block), and is
//    followed until it reads `recognizing`: the server has read which pages
//    are scans, then asks which of them it wants (GET /api/ocr/<doc>/plan).
// 2. Each page it wants, one at a time: drawn, read by the engine, its
//    words sent (PUT /api/ocr/<doc>/pages/<n>, `{failed: true}` for a page
//    with no result); the server stages them and answers which pages it
//    still wants. A page the server has from this job is not read again.
// 3. None left: the job applies what was read, by itself once the server
//    wants no more pages, or told by POST /api/ocr/<doc>/done when it
//    still wants some this window cannot send (past the end of the
//    document it opened, or sent already). The window's run ends there.
//
// A request that gets no answer, a proxy's 502/503/504, or the server's own
// 503 that says to try again (a page's words not kept now), waits until the
// window is shown and goes again after 1, 2, 4, 8 and 16 s. A 409 is judged
// by the job, as the server says it ended: gone or ended by a restart of the
// server (the apps' local server, recovered as interrupted), or ended by
// its idle timer because no page came for its idle time (`progress.idle`),
// and the run starts it again and goes on (the server skips the pages it
// staged), at most MAX_RESTARTS times; stopped otherwise (by a person, in
// Background tasks on any device, or stopping still), and the run ends as
// stopped. A page that cannot be drawn or read in PAGE_TIMEOUT_MS goes as
// failed, its drawing called off and the engine started afresh; the engine
// is started afresh every WORKER_PAGES pages too, since its memory only
// grows. The timeouts count only the time the window is shown: a hidden or
// locked one is no slower.
//
// `api` (each bound to the document): start(body) → the job, job(id) → the
// job or a 404, plan() → the plan, put(n, body) → the page's answer,
// done(body). `engine`: supported() (the browser runs the core), open() →
// the document (destroy()), raster(doc, n, render, signal) → {pgm, width,
// height, dpi}, called off when `signal` aborts, worker() → {ready (a
// promise), recognize(pgm, dpi) → the TSV, terminate()}. `env`
// (browserEnv's): now(), sleep(ms), after(ms, fn) → cancel, visible(),
// onVisibility(fn) → off.

import { t } from "../shared/i18n/i18n.js";
import { ENGINE, LANGS, LIMITS, OCR_VERSION, parseTsv } from "./ocrRaster.js";

// A page drawn and read within this (the server's own engine's page
// timeout, ocr_engine.PAGE_TIMEOUT_MS), else it goes as failed.
export const PAGE_TIMEOUT_MS = 120_000;
// The engine started within this: its core and the three models are some
// 11 MB to fetch on a slow link.
export const WORKER_TIMEOUT_MS = 180_000;
// Pages one engine reads before it is started afresh.
export const WORKER_PAGES = 50;
// The waits before each retry of a request that got no answer.
export const RETRY_MS = [1000, 2000, 4000, 8000, 16000];
// How many times a run starts its job again.
export const MAX_RESTARTS = 3;
// How often the job is read while the server reads which pages are scans.
export const FOLLOW_MS = 1000;

// How a run ends, thrown through it: "done", "stopped", "failed" (with the
// message for the person; `own`: the window's own failure, not the
// server's word) or "restart" (its job is started again).
class End extends Error {
  constructor(state, message = "", own = false) {
    super(message);
    this.state = state;
    this.own = own;
  }
}
const stopped = () => new End("stopped");
const restart = () => new End("restart");
const finished = () => new End("done");
const failed = (message, own = false) => new End("failed", message, own);
const unanswered = () => failed(t("The server stopped answering; recognizing again goes on from the pages read."));

const isActive = (job) => job?.state === "queued" || job?.state === "running";
const pageList = (list) => (Array.isArray(list) ? list.filter((n) => Number.isInteger(n) && n > 0) : []);
const countOf = (v) => (Array.isArray(v) ? v.length : Number.isFinite(v) ? v : 0);
const byteLength = (text) => new TextEncoder().encode(text).length;

// Whether a failed request is worth sending again: no answer came (the
// server away, the apps' local server restarting), the account's request
// rate was passed (`limited`: a 429 is the rate limit's; a start's says the
// account's tasks are full, which waiting does not change), a proxy in
// front of the server answered for it, or the server says to try again (a
// page's words not kept now). Any other 503 of Gamma's own carries its
// reason, and stands.
export function retryable(err, { limited = true } = {}) {
  const status = err?.status;
  if (status === undefined || status === null) return true;
  if (status === 429) return limited;
  if (status === 502 || status === 504) return true;
  const detail = err?.data?.detail;
  return status === 503 && (typeof detail !== "string" || /\btry again$/.test(detail));
}

// Calls `fire` once `ms` of the time the window is shown have passed; the
// returned function calls it off.
export function visibleTimer(ms, env, fire) {
  let left = ms, since = 0, cancel = null, over = false;
  const arm = () => {
    if (over || cancel || !env.visible()) return;
    since = env.now();
    cancel = env.after(left, () => {
      cancel = null;
      end();
      fire();
    });
  };
  const pause = () => {
    if (!cancel) return;
    cancel();
    cancel = null;
    left = Math.max(0, left - (env.now() - since));
  };
  const off = env.onVisibility(() => (env.visible() ? arm() : pause()));
  const end = () => {
    over = true;
    cancel?.();
    cancel = null;
    off();
  };
  arm();
  return end;
}

// Resolves once the window is shown.
export function whenVisible(env) {
  if (env.visible()) return Promise.resolve();
  return new Promise((resolve) => {
    const off = env.onVisibility(() => {
      if (!env.visible()) return;
      off();
      resolve();
    });
  });
}

// The browser's clock and visibility.
export function browserEnv() {
  const doc = globalThis.document;
  return {
    now: () => Date.now(),
    sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    after: (ms, fn) => {
      const timer = setTimeout(fn, ms);
      return () => clearTimeout(timer);
    },
    visible: () => doc?.visibilityState !== "hidden",
    onVisibility: (fn) => {
      doc?.addEventListener("visibilitychange", fn);
      return () => doc?.removeEventListener("visibilitychange", fn);
    },
  };
}

// The run of `docId`'s pages, shown on `pageId`, for `capability`
// (/api/server-config's `ocr`, where "window"), as this window's `client`
// (ocrRuns' per-load id): resolves with how it ended, {state: "done"} (the
// job applies what was read), {state: "stopped"} or {state: "failed",
// error, local}, never rejects. `local`: it failed on this window's side
// (the engine, the document, a request the server refused outright) while
// its job still waits for pages, which the caller then stops, so the job
// ends at once and what was read waits for the next run. `signal` stops it
// at once; `report(patch)` hears how far it got: {phase: "starting" |
// "reading" | "recognizing", jobId, page, done, total}. The engine and the
// document go at every end.
export async function runWindow(run, { api, engine, env = browserEnv(), timeouts = {} }) {
  const { pageId, capability, client, report = () => {} } = run;
  const signal = run.signal || new AbortController().signal;
  const pageMs = timeouts.page ?? PAGE_TIMEOUT_MS;
  const workerMs = timeouts.worker ?? WORKER_TIMEOUT_MS;
  const own = { client, engine: ENGINE, langs: LANGS, ver: OCR_VERSION };
  // the document, the engine and the pages it read
  const win = { doc: null, worker: null, pages: 0 };
  // the job this run started and has not seen end
  let live = null;

  const halted = new Promise((_, reject) => {
    if (signal.aborted) reject(stopped());
    else signal.addEventListener("abort", () => reject(stopped()), { once: true });
  });
  halted.catch(() => {});
  const guard = (promise) => Promise.race([promise, halted]);
  const within = (promise, ms) => {
    let cancel = () => {};
    const timeout = new Promise((_, reject) => {
      cancel = visibleTimer(ms, env, () => reject(new Error("timed out")));
    });
    return guard(Promise.race([promise, timeout])).finally(() => cancel());
  };

  // A request, sent again while no answer comes (`retryable`, `limited` as
  // its), until the waits run out. `free`: not cut short by a stop, so a job
  // started meanwhile is known and can be stopped.
  async function ask(send, { free = false, limited = true } = {}) {
    for (let i = 0; ; i++) {
      try {
        return await (free ? send() : guard(send()));
      } catch (err) {
        if (err instanceof End) throw err;
        if (signal.aborted) throw stopped();
        if (!retryable(err, { limited })) throw err;
        if (i >= RETRY_MS.length) throw unanswered();
        await guard(whenVisible(env));
        await guard(env.sleep(RETRY_MS[i]));
      }
    }
  }

  // The job, or null when the server no longer has it.
  async function jobOf(id) {
    try {
      return await ask(() => api.job(id));
    } catch (err) {
      if (err?.status === 404) return null;
      throw err;
    }
  }

  // How a job that is gone or over ends the run, as the server says it
  // ended: one its idle timer stopped (it says so: `progress.idle`), or a
  // restart of the server lost, is started again; one stopped otherwise was
  // stopped by a person, however long this window was away.
  function ending(job) {
    live = null;
    if (!job) return restart();
    if (job.state === "done") return finished();
    if (job.state === "cancelled") return job.progress?.idle ? restart() : stopped();
    return /^interrupted\b/.test(job.error || "") ? restart() : failed(job.error || t("failed"));
  }

  // A request's 409, judged by the job: over or gone (`ending`), being
  // stopped (a Stop the job has not reached yet: it ends at its next
  // report), applying already (done), else the refusal stands.
  async function conflict(err, id) {
    const job = await jobOf(id);
    if (!isActive(job)) return ending(job);
    if (job.stopping) return stopped();
    if (job.progress?.phase === "updating") return finished();
    return failed(err.message || t("failed"));
  }

  // The job followed until the server wants pages, or until it ended.
  async function follow(job) {
    for (;;) {
      if (!isActive(job)) throw ending(job);
      const phase = job.state === "running" ? job.progress?.phase : "";
      if (phase === "recognizing") return;
      if (phase === "updating") throw finished();
      await guard(env.sleep(FOLLOW_MS));
      job = await jobOf(job.id);
    }
  }

  function dropWorker() {
    win.worker?.terminate();
    win.worker = null;
  }

  async function readyWorker() {
    if (win.worker && win.pages >= WORKER_PAGES) dropWorker();
    if (!win.worker) {
      win.worker = engine.worker();
      win.pages = 0;
      try {
        await within(win.worker.ready, workerMs);
      } catch (err) {
        dropWorker();
        if (err instanceof End) throw err;
        throw failed(t("The text recognition engine did not start in this window."), true);
      }
    }
    return win.worker;
  }

  // Page `n`'s words ({lines}), or {failed: true} when it has none. A
  // drawing that ran out of time, or a stop, calls the drawing off, so its
  // canvas goes at once.
  async function readPage(n, render, limits) {
    let raster;
    const drawing = new AbortController();
    try {
      raster = await within(engine.raster(win.doc, n, render, drawing.signal), pageMs);
    } catch (err) {
      drawing.abort();
      if (err instanceof End) throw err;
      return { failed: true };
    }
    const worker = await readyWorker();
    let tsv;
    try {
      tsv = await within(worker.recognize(raster.pgm, raster.dpi), pageMs);
    } catch (err) {
      if (err instanceof End) throw err;
      dropWorker(); // hung or broken: a fresh one reads the next page
      return { failed: true };
    }
    win.pages += 1;
    const lines = parseTsv(tsv, raster.width, raster.height, limits);
    return lines ? { lines } : { failed: true };
  }

  // Page `n` sent; a body the server refuses as too big or malformed goes
  // again as failed.
  async function sendPage(jobId, n, words, limits) {
    const put = async (body) => {
      try {
        return await ask(() => api.put(n, body));
      } catch (err) {
        if (err?.status === 409) throw await conflict(err, jobId);
        throw err;
      }
    };
    const fails = { ...own, failed: true };
    const body = words.lines ? { ...own, lines: words.lines } : fails;
    if (body.lines && byteLength(JSON.stringify(body)) > limits.max_body) return put(fails);
    try {
      return await put(body);
    } catch (err) {
      if (body.lines && [400, 413, 422].includes(err?.status)) return put(fails);
      throw err;
    }
  }

  // One job, from its start to the window's last page. The start of the
  // account's own job going for the file (another window's, or this one's
  // before a reload) answers that job, which this window then reads on.
  async function session() {
    report({ phase: "starting", page: 0 });
    const job = await ask(() => api.start({ page_id: pageId, window: { doc_id: run.docId, ...own } }), { free: true, limited: false });
    live = job.id;
    report({ jobId: job.id });
    if (signal.aborted) throw stopped();
    report({ phase: "reading" });
    await follow(job);
    let plan;
    try {
      plan = await ask(() => api.plan());
    } catch (err) {
      throw err?.status === 409 ? await conflict(err, job.id) : err;
    }
    const limits = { ...LIMITS, ...plan.limits };
    let todo = pageList(plan.todo);
    let done = countOf(plan.done), total = done + todo.length;
    report({ phase: "recognizing", done, total });
    const sent = new Set();
    for (;;) {
      if (todo.some((p) => !sent.has(p))) win.doc ||= await guard(engine.open());
      const n = todo.find((p) => !sent.has(p) && p <= (win.doc?.numPages || 0));
      if (n === undefined) break;
      report({ page: n });
      const words = await readPage(n, plan.render, limits);
      sent.add(n);
      const answer = await sendPage(job.id, n, words, limits);
      todo = pageList(answer?.todo);
      if (Number.isFinite(answer?.done)) done = answer.done;
      if (Number.isFinite(answer?.total)) total = answer.total;
      report({ done, total });
    }
    // An empty list is the server's own end: its job applies without a word,
    // and is over by the time one came.
    if (!todo.length) return;
    try {
      await ask(() => api.done({ client }));
    } catch (err) {
      // refused as a page is: judged by the job (applying already, or over:
      // done; stopped meanwhile: stopped)
      throw err?.status === 409 ? await conflict(err, job.id) : err;
    }
  }

  try {
    if (!capability?.models) return { state: "failed", error: t("This server sends no recognition models.") };
    if (!engine.supported()) return { state: "failed", error: t("This browser cannot recognize text: it lacks WebAssembly SIMD.") };
    for (let restarts = 0; ; restarts++) {
      try {
        await session();
        return { state: "done" };
      } catch (err) {
        if (!(err instanceof End && err.state === "restart")) throw err;
        if (restarts >= MAX_RESTARTS) throw unanswered();
      }
    }
  } catch (err) {
    if (signal.aborted) return { state: "stopped" };
    if (err instanceof End && err.state !== "failed") return { state: err.state };
    // the window's own failure (any but the server's word on the job) while
    // the job waits for its pages
    const local = !!live && (!(err instanceof End) || err.own);
    return { state: "failed", error: err?.message || t("failed"), ...(local ? { local } : {}) };
  } finally {
    dropWorker();
    try {
      await win.doc?.destroy();
    } catch { /* already gone */ }
  }
}
