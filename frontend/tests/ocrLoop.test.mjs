// A scan's pages read in the window (pdf/ocrLoop.js), against a fake server,
// a fake engine and a clock the tests move: the job started and followed,
// the pages the server wants read and sent, retries while no answer comes,
// the job started again after the server lost it, stops, failed and hung
// pages, the engine started afresh.
import { test } from "node:test";
import assert from "node:assert/strict";
import { MAX_RESTARTS, PAGE_TIMEOUT_MS, RETRY_MS, WORKER_PAGES, retryable, runWindow, visibleTimer }
  from "../src/pdf/ocrLoop.js";
import { ENGINE, LANGS, OCR_VERSION } from "../src/pdf/ocrRaster.js";

const CAPABILITY = { where: "window", engine: ENGINE, langs: ["eng", "fra", "deu"], models: "/api/ocr/models/0123456789ab" };
const OWN = { client: "c1", engine: ENGINE, langs: LANGS, ver: OCR_VERSION };
const STOPPED_ANSWERING = "The server stopped answering; recognizing again goes on from the pages read.";
const refused = (status, detail) => Object.assign(new Error(detail || `HTTP ${status}`), { status, data: detail ? { detail } : null });

// A clock that moves only when told (`advance`, and each sleep), and a
// window shown or hidden (`show`).
function fakeEnv() {
  const env = { t: 1_000_000, shown: true, slept: [], timers: new Set(), listeners: new Set() };
  return Object.assign(env, {
    now: () => env.t,
    advance(ms) {
      env.t += ms;
      for (const timer of [...env.timers]) {
        if (timer.at <= env.t && env.timers.delete(timer)) timer.fn();
      }
    },
    sleep: async (ms) => {
      env.slept.push(ms);
      env.advance(ms);
    },
    after(ms, fn) {
      const timer = { at: env.t + ms, fn };
      env.timers.add(timer);
      return () => env.timers.delete(timer);
    },
    visible: () => env.shown,
    onVisibility(fn) {
      env.listeners.add(fn);
      return () => env.listeners.delete(fn);
    },
    show(on) {
      env.shown = on;
      for (const fn of [...env.listeners]) fn();
    },
  });
}

// The server's half: a job per start (reading, then recognizing once read
// again), a plan of the pages not staged yet, a PUT that stages its page
// and answers what is left, a done. `on.<call>` replaces a call (given the
// default as its last argument).
function fakeServer(pages = [1, 2], on = {}) {
  const calls = [];
  const jobs = new Map();
  const staged = new Set();
  const left = () => pages.filter((n) => !staged.has(n));
  const base = {
    async start(body) {
      const job = { id: `job${jobs.size + 1}`, kind: "ocr-tab", state: "running", progress: { phase: "reading" } };
      jobs.set(job.id, job);
      return { ...job };
    },
    async job(id) {
      const job = jobs.get(id);
      if (!job) throw refused(404, "no such task");
      if (job.state === "running" && job.progress.phase === "reading") job.progress = { phase: "recognizing" };
      return { ...job, progress: { ...job.progress } };
    },
    async plan() {
      return { todo: left(), sample: [], done: [...staged], good_layer: false,
        render: { dpi: 300, max_pixels: 2480 * 3508, max_side: 10000 }, limits: { max_words: 20000, max_word_chars: 200, max_body: 1048576 } };
    },
    async put(n, body) {
      staged.add(n);
      return { outcome: body.failed ? "failed" : "staged", shared: null, todo: left(), done: staged.size, total: pages.length };
    },
    async done() { return { ok: true }; },
  };
  const api = {};
  for (const [name, fn] of Object.entries(base)) {
    api[name] = async (...args) => {
      calls.push([name, ...args]);
      return on[name] ? on[name](...args, fn) : fn(...args);
    };
  }
  return { api, calls, jobs, staged, named: (name) => calls.filter((c) => c[0] === name) };
}

// The engine's half: a document of `numPages`, each page one word "p<n>"
// at a known box; `recognize(n)` may be replaced per page, and so may
// `raster(n, signal)` (the drawing's own call-off signal, kept in `signals`).
function fakeEngine({ numPages = 60, recognize = null, supported = true, raster = null } = {}) {
  const log = { opened: 0, destroyed: 0, workers: 0, terminated: 0, rasters: [], signals: [] };
  let page = 0;
  const engine = {
    supported: () => supported,
    async open() {
      log.opened++;
      return { numPages, destroy: async () => { log.destroyed++; } };
    },
    async raster(doc, n, render, signal) {
      log.rasters.push(n);
      log.signals.push(signal);
      page = n;
      if (raster) return raster(n, signal);
      return { pgm: new Uint8Array(8), width: 1000, height: 2000, dpi: 300 };
    },
    worker() {
      log.workers++;
      return {
        ready: Promise.resolve(),
        recognize: async () => (recognize ? recognize(page) : tsvOf(page)),
        terminate: () => { log.terminated++; },
      };
    },
  };
  return { engine, log };
}
const tsvOf = (n) => `5\t1\t1\t1\t1\t1\t100\t100\t200\t50\t95\tp${n}`;
const linesOf = (n) => [[[`p${n}`, 1000, 500, 3000, 750, 95]]];

function go(server, engine, env, extra = {}) {
  const reports = [];
  const done = runWindow({ docId: "d0c", pageId: "pg1", capability: CAPABILITY, client: "c1", report: (p) => reports.push(p), ...extra },
    { api: server.api, engine: engine.engine, env, timeouts: extra.timeouts });
  return Object.assign(done, { reports });
}

test("a run starts its job, follows it to recognizing and sends each page the server wants, whose job then applies by itself", async () => {
  const server = fakeServer([1, 2]), engine = fakeEngine(), env = fakeEnv();
  const run = go(server, engine, env);
  assert.deepEqual(await run, { state: "done" });
  // the last answer wants no more pages: the job applies without a done,
  // which would come after it ended
  assert.deepEqual(server.calls.map((c) => c[0]), ["start", "job", "plan", "put", "put"]);
  assert.deepEqual(server.named("start")[0][1], { page_id: "pg1", window: { doc_id: "d0c", ...OWN } });
  assert.deepEqual(server.named("put").map((c) => c.slice(1)), [[1, { ...OWN, lines: linesOf(1) }], [2, { ...OWN, lines: linesOf(2) }]]);
  assert.deepEqual({ ...engine.log, signals: undefined }, { opened: 1, destroyed: 1, workers: 1, terminated: 1, rasters: [1, 2], signals: undefined });
  assert.ok(engine.log.signals.every((s) => s instanceof AbortSignal && !s.aborted), "each drawing can be called off, none was");
  const phases = run.reports.map((r) => r.phase).filter(Boolean);
  assert.deepEqual(phases, ["starting", "reading", "recognizing"]);
  assert.equal(run.reports.find((r) => r.jobId).jobId, "job1");
  assert.deepEqual(run.reports.at(-1), { done: 2, total: 2 });
});

test("a page is read once a job, whatever the server answers, and the pages it no longer wants are passed by", async () => {
  // the first answer leaves page 1 among those to do, and drops page 2 (its verdict moved on)
  const server = fakeServer([1, 2, 3], {
    put: async (n) => (n === 1 ? { outcome: "staged", todo: [1, 3], done: 1, total: 2 } : { outcome: "staged", todo: [], done: 2, total: 2 }),
  });
  const engine = fakeEngine();
  assert.deepEqual(await go(server, engine, fakeEnv()), { state: "done" });
  assert.deepEqual(engine.log.rasters, [1, 3]);
  assert.equal(server.named("done").length, 0);
});

test("pages the server still wants and this window cannot send: the server is told it is done", async () => {
  // page 1 sent, its answer still lists it; page 3 is past the document's end
  const server = fakeServer([1, 3], { put: async () => ({ outcome: "staged", todo: [1, 3], done: 0, total: 2 }) });
  const engine = fakeEngine({ numPages: 2 });
  assert.deepEqual(await go(server, engine, fakeEnv()), { state: "done" });
  assert.deepEqual(engine.log.rasters, [1]);
  assert.deepEqual(server.calls.map((c) => c[0]), ["start", "job", "plan", "put", "done"]);
  assert.deepEqual(server.named("done")[0][1], { client: "c1" });
});

test("nothing to read: no page goes, no engine starts, and the job applies by itself", async () => {
  const server = fakeServer([]), engine = fakeEngine();
  assert.deepEqual(await go(server, engine, fakeEnv()), { state: "done" });
  assert.deepEqual(server.calls.map((c) => c[0]), ["start", "job", "plan"]);
  assert.equal(engine.log.opened, 0);
  assert.equal(engine.log.workers, 0);
});

test("a job that applies or ends before the window looks ends the run as it ended", async () => {
  for (const [job, end] of [
    [{ state: "running", progress: { phase: "updating" } }, { state: "done" }],
    [{ state: "done", progress: {} }, { state: "done" }],
    [{ state: "failed", error: "This PDF is too long to recognize." }, { state: "failed", error: "This PDF is too long to recognize." }],
  ]) {
    const server = fakeServer([1], { job: async (id) => ({ id, ...job }) });
    assert.deepEqual(await go(server, fakeEngine(), fakeEnv()), end);
    assert.equal(server.named("plan").length, 0);
  }
});

test("a request with no answer waits until the window is shown, then goes again after 1, 2, … s", async () => {
  const env = fakeEnv();
  const order = [];
  let tries = 0, mark = 0;
  const server = fakeServer([1], {
    put: async (n, body, put) => {
      order.push(`put shown=${env.shown}`);
      if (++tries <= 2) {
        if (tries === 1) {
          mark = env.slept.length;
          env.shown = false; // the app went to the background
          setTimeout(() => { order.push("shown"); env.show(true); }, 5);
        }
        throw new TypeError("Failed to fetch");
      }
      return put(n, body);
    },
  });
  assert.deepEqual(await go(server, fakeEngine(), env), { state: "done" });
  assert.deepEqual(order, ["put shown=true", "shown", "put shown=true", "put shown=true"]);
  assert.deepEqual(env.slept.slice(mark), [1000, 2000]);
});

test("once the waits run out the run fails, saying the server stopped answering", async () => {
  const env = fakeEnv();
  let mark = -1;
  const server = fakeServer([1], {
    put: async () => {
      if (mark < 0) mark = env.slept.length;
      throw refused(502);
    },
  });
  assert.deepEqual(await go(server, fakeEngine(), env), { state: "failed", error: STOPPED_ANSWERING });
  assert.equal(server.named("put").length, RETRY_MS.length + 1);
  assert.deepEqual(env.slept.slice(mark), RETRY_MS);
});

test("which failures are sent again: no answer, the rate limit, a proxy's 5xx and the server's try again, not Gamma's own reasons", () => {
  assert.equal(retryable(new TypeError("Failed to fetch")), true);
  for (const status of [429, 502, 504]) assert.equal(retryable(refused(status)), true);
  assert.equal(retryable(refused(503)), true);
  assert.equal(retryable(refused(503, "the recognized text cannot be kept now — try again")), true, "a page's words not kept now");
  assert.equal(retryable(refused(503, "Text recognition is turned off on this server.")), false);
  for (const status of [400, 403, 404, 409, 413, 500]) assert.equal(retryable(refused(status)), false);
  assert.equal(retryable(refused(429, "you already have 20 background tasks waiting or running — let some of them finish first"),
    { limited: false }), false, "a 429 that is no rate limit's: waiting does not change it");
});

test("Gamma's own refusal ends the run with its reason", async () => {
  const server = fakeServer([1], { start: async () => { throw refused(503, "Text recognition is turned off on this server."); } });
  assert.deepEqual(await go(server, fakeEngine(), fakeEnv()), { state: "failed", error: "Text recognition is turned off on this server." });
  assert.equal(server.calls.length, 1);
});

test("a start the account's full task list refuses ends the run with the server's reason at once", async () => {
  const full = "you already have 20 background tasks waiting or running — let some of them finish first";
  const env = fakeEnv();
  const server = fakeServer([1], { start: async () => { throw refused(429, full); } });
  assert.deepEqual(await go(server, fakeEngine(), env), { state: "failed", error: full });
  assert.equal(server.named("start").length, 1, "not sent again");
  assert.deepEqual(env.slept, []);
});

test("a page the server could not keep now goes again, and the run goes on", async () => {
  let tries = 0;
  const server = fakeServer([1, 2], {
    put: async (n, body, put) => {
      if (n === 1 && ++tries <= 2) throw refused(503, "the recognized text cannot be kept now — try again");
      return put(n, body);
    },
  });
  assert.deepEqual(await go(server, fakeEngine(), fakeEnv()), { state: "done" });
  assert.deepEqual(server.named("put").map((c) => c[1]), [1, 1, 1, 2]);
});

test("a 409 after the server restarted its job away starts the job again, and the run goes on", async () => {
  for (const lost of [
    (server, id) => { server.jobs.get(id).state = "failed"; server.jobs.get(id).error = "interrupted: the server restarted while this ran — start it again"; },
    (server, id) => { server.jobs.delete(id); },
  ]) {
    let first = true;
    const server = fakeServer([1, 2], {
      put: async (n, body, put) => {
        if (first) {
          first = false;
          lost(server, "job1");
          throw refused(409, "This PDF's recognition stopped.");
        }
        return put(n, body);
      },
    });
    const engine = fakeEngine();
    assert.deepEqual(await go(server, engine, fakeEnv()), { state: "done" });
    assert.equal(server.named("start").length, 2);
    // the second job reads page 1 again (its first PUT never staged it), then page 2
    assert.deepEqual(server.named("put").map((c) => c[1]), [1, 1, 2]);
    assert.equal(engine.log.opened, 1, "the document stays open across the restart");
    assert.equal(engine.log.workers, 1, "and so does the engine");
  }
});

test("after MAX_RESTARTS restarts the run fails", async () => {
  const server = fakeServer([1], {
    put: async () => {
      server.jobs.clear();
      throw refused(409, "This PDF's recognition stopped.");
    },
  });
  assert.deepEqual(await go(server, fakeEngine(), fakeEnv()), { state: "failed", error: STOPPED_ANSWERING });
  assert.equal(server.named("start").length, MAX_RESTARTS + 1);
});

test("a job a person stopped ends the run as stopped; one its idle timer stopped is started again", async () => {
  // The first page's PUT finds job1 stopped, `wait` after the plan came;
  // `idle`: the job says its idle timer stopped it.
  const stoppedServer = (env, { idle = false, wait = 0 } = {}) => {
    let first = true;
    const server = fakeServer([1], {
      put: async (n, body, put) => {
        if (first) {
          first = false;
          env.advance(wait);
          Object.assign(server.jobs.get("job1"), { state: "cancelled", progress: { phase: "recognizing", ...(idle ? { idle } : {}) } });
          throw refused(409, "This PDF's recognition stopped.");
        }
        return put(n, body);
      },
    });
    return server;
  };
  // stopped in Background tasks, on this device or another, and seen at
  // once or long after (the window was locked or asleep meanwhile): it stays
  // stopped, however long ago its last page came
  for (const wait of [0, 20 * 60_000]) {
    const env = fakeEnv();
    const server = stoppedServer(env, { wait });
    assert.deepEqual(await go(server, fakeEngine(), env), { state: "stopped" }, `seen after ${wait} ms`);
    assert.equal(server.named("start").length, 1);
    assert.equal(server.named("done").length, 0);
  }
  // stopped by the server because no page came for its idle time, which it says
  const env = fakeEnv();
  const server = stoppedServer(env, { idle: 600, wait: 20 * 60_000 });
  assert.deepEqual(await go(server, fakeEngine(), env), { state: "done" });
  assert.equal(server.named("start").length, 2);
});

test("a Stop the job has not reached yet ends the run as stopped, whether a page or the done meets it", async () => {
  // The job is stopping (a person's Stop, which the job sees at its next
  // report): the PUT's 409 meets a job still running.
  const stopping = (server) => Object.assign(server.jobs.get("job1"), { stopping: true, progress: { phase: "recognizing" } });
  const atPage = fakeServer([1, 2], {
    put: async () => { stopping(atPage); throw refused(409, "This PDF's recognition stopped."); },
  });
  assert.deepEqual(await go(atPage, fakeEngine(), fakeEnv()), { state: "stopped" });
  assert.equal(atPage.named("start").length, 1);
  // the done that tells the job to apply meets a Stop: stopping, or over already
  for (const [stop, end] of [
    [stopping, { state: "stopped" }],
    [(server) => Object.assign(server.jobs.get("job1"), { state: "cancelled", progress: { phase: "recognizing" } }), { state: "stopped" }],
    [(server) => Object.assign(server.jobs.get("job1"), { progress: { phase: "updating" } }), { state: "done" }],
  ]) {
    const atDone = fakeServer([1, 3], {
      put: async () => ({ outcome: "staged", todo: [1, 3], done: 0, total: 2 }),
      done: async () => { stop(atDone); throw refused(409, "This PDF's recognition stopped."); },
    });
    assert.deepEqual(await go(atDone, fakeEngine({ numPages: 2 }), fakeEnv()), end);
    assert.equal(atDone.named("start").length, 1);
  }
});

test("a 409 while the job still recognizes is the server's refusal, and ends the run with it", async () => {
  const server = fakeServer([1, 2], {
    put: async () => { throw refused(409, "This PDF's text is being recognized in another window now."); },
  });
  assert.deepEqual(await go(server, fakeEngine(), fakeEnv()),
    { state: "failed", error: "This PDF's text is being recognized in another window now." });
  assert.equal(server.named("start").length, 1);
  // one applying already took the page's place: the run is done
  const late = fakeServer([1], {
    put: async () => {
      late.jobs.get("job1").progress = { phase: "updating" };
      throw refused(409, "This PDF's recognition stopped.");
    },
  });
  assert.deepEqual(await go(late, fakeEngine(), fakeEnv()), { state: "done" });
});

test("a page the engine cannot read goes as failed, and a fresh engine reads the next", async () => {
  const server = fakeServer([1, 2]);
  const engine = fakeEngine({ recognize: (n) => { if (n === 1) throw new Error("bad picture"); return tsvOf(n); } });
  assert.deepEqual(await go(server, engine, fakeEnv()), { state: "done" });
  assert.deepEqual(server.named("put").map((c) => c.slice(1)), [[1, { ...OWN, failed: true }], [2, { ...OWN, lines: linesOf(2) }]]);
  assert.equal(engine.log.workers, 2);
  assert.equal(engine.log.terminated, 2);
});

test("a page whose drawing runs out of time goes as failed, and its drawing is called off", async () => {
  const env = fakeEnv();
  const engine = fakeEngine({
    raster: (n) => {
      if (n !== 1) return { pgm: new Uint8Array(8), width: 1000, height: 2000, dpi: 300 };
      setTimeout(() => env.advance(PAGE_TIMEOUT_MS), 0);
      return new Promise(() => {}); // a stream that stalls the PDF's worker
    },
  });
  const server = fakeServer([1, 2]);
  assert.deepEqual(await go(server, engine, env), { state: "done" });
  assert.deepEqual(server.named("put")[0].slice(1), [1, { ...OWN, failed: true }]);
  assert.equal(engine.log.signals[0]?.aborted, true, "the stalled drawing is cancelled, its canvas released");
  assert.equal(engine.log.signals[1]?.aborted, false, "a drawing that came back is not");
  // a stop calls off the drawing under way too
  const controller = new AbortController();
  const stopped = fakeEngine({ raster: () => { setTimeout(() => controller.abort(), 0); return new Promise(() => {}); } });
  assert.deepEqual(await go(fakeServer([1]), stopped, fakeEnv(), { signal: controller.signal }), { state: "stopped" });
  assert.equal(stopped.log.signals[0]?.aborted, true);
});

test("a page that hangs past its time goes as failed; the time counts only while the window is shown", async () => {
  const env = fakeEnv();
  const engine = fakeEngine({
    recognize: (n) => {
      if (n !== 1) return tsvOf(n);
      setTimeout(() => {
        env.advance(PAGE_TIMEOUT_MS - 1);
        env.show(false);
        env.advance(10 * PAGE_TIMEOUT_MS); // hidden: not counted
        env.show(true);
        env.advance(1);
      }, 0);
      return new Promise(() => {});
    },
  });
  const server = fakeServer([1, 2]);
  assert.deepEqual(await go(server, engine, env), { state: "done" });
  assert.deepEqual(server.named("put")[0].slice(1), [1, { ...OWN, failed: true }]);
  assert.equal(engine.log.workers, 2);
  assert.equal(env.timers.size, 0, "no timer outlives the run");
});

test("the engine is started afresh every WORKER_PAGES pages", async () => {
  const pages = Array.from({ length: WORKER_PAGES + 1 }, (_, i) => i + 1);
  const engine = fakeEngine();
  assert.deepEqual(await go(fakeServer(pages), engine, fakeEnv()), { state: "done" });
  assert.equal(engine.log.workers, 2);
  assert.equal(engine.log.terminated, 2);
});

test("a body past max_body, or one the server refuses as malformed, goes as failed", async () => {
  const big = fakeServer([1], {
    plan: async (plan) => ({ ...(await plan()), limits: { max_words: 20000, max_word_chars: 200, max_body: 40 } }),
  });
  assert.deepEqual(await go(big, fakeEngine(), fakeEnv()), { state: "done" });
  assert.deepEqual(big.named("put")[0].slice(1), [1, { ...OWN, failed: true }]);
  const malformed = fakeServer([1], { put: async (n, body, put) => { if (body.lines) throw refused(422, "bad words"); return put(n, body); } });
  assert.deepEqual(await go(malformed, fakeEngine(), fakeEnv()), { state: "done" });
  assert.deepEqual(malformed.named("put").map((c) => !!c[2].failed), [false, true]);
});

test("a stop ends the run at once, the engine and the document with it", async () => {
  const controller = new AbortController();
  const engine = fakeEngine({ recognize: () => { setTimeout(() => controller.abort(), 0); return new Promise(() => {}); } });
  const server = fakeServer([1, 2]);
  assert.deepEqual(await go(server, engine, fakeEnv(), { signal: controller.signal }), { state: "stopped" });
  assert.equal(server.named("put").length, 0);
  assert.equal(engine.log.terminated, 1);
  assert.equal(engine.log.destroyed, 1);
});

test("a stop while the job is being started still hears of the job, so it can be stopped too", async () => {
  const controller = new AbortController();
  const server = fakeServer([1], { start: async (body, start) => { controller.abort(); return start(body); } });
  const run = go(server, fakeEngine(), fakeEnv(), { signal: controller.signal });
  assert.deepEqual(await run, { state: "stopped" });
  assert.equal(run.reports.find((r) => r.jobId)?.jobId, "job1");
  assert.deepEqual(server.calls.map((c) => c[0]), ["start"]);
});

test("without the server's models or the browser's SIMD the run fails before any request", async () => {
  const server = fakeServer([1]);
  const noModels = runWindow({ docId: "d0c", pageId: "pg1", capability: { where: "window" }, client: "c1" },
    { api: server.api, engine: fakeEngine().engine, env: fakeEnv() });
  assert.deepEqual(await noModels, { state: "failed", error: "This server sends no recognition models." });
  assert.deepEqual(await go(server, fakeEngine({ supported: false }), fakeEnv()),
    { state: "failed", error: "This browser cannot recognize text: it lacks WebAssembly SIMD." });
  assert.equal(server.calls.length, 0);
});

test("an engine that never gets ready ends the run, and is ended", async () => {
  const env = fakeEnv();
  const engine = fakeEngine();
  let ended = 0;
  engine.engine.worker = () => {
    setTimeout(() => env.advance(10 * 60_000), 0);
    return { ready: new Promise(() => {}), recognize: async () => "", terminate: () => { ended++; } };
  };
  // its job waits for pages: the caller stops it (`local`)
  assert.deepEqual(await go(fakeServer([1]), engine, env),
    { state: "failed", error: "The text recognition engine did not start in this window.", local: true });
  assert.equal(ended, 1);
});

test("a run that fails on this window's side says its job waits for pages; the server's word on the job does not", async () => {
  // the stored file does not open, or a page's PUT is refused outright
  const unopened = fakeEngine();
  unopened.engine.open = async () => { throw new Error("Invalid PDF structure."); };
  assert.deepEqual(await go(fakeServer([1]), unopened, fakeEnv()), { state: "failed", error: "Invalid PDF structure.", local: true });
  const refusedPage = fakeServer([1], { put: async () => { throw refused(500, "Internal Server Error"); } });
  assert.deepEqual(await go(refusedPage, fakeEngine(), fakeEnv()), { state: "failed", error: "Internal Server Error", local: true });
  // the server's own word: its refusal while the job reads, a job that
  // failed, no answer, or a start refused before any job
  const otherWindow = fakeServer([1], { put: async () => { throw refused(409, "This PDF's text is being recognized in another window now."); } });
  assert.equal((await go(otherWindow, fakeEngine(), fakeEnv())).local, undefined, "the job is the other window's now");
  const failedJob = fakeServer([1], { job: async (id) => ({ id, state: "failed", error: "This PDF is too long to recognize." }) });
  assert.equal((await go(failedJob, fakeEngine(), fakeEnv())).local, undefined);
  const away = fakeServer([1], { put: async () => { throw refused(502); } });
  assert.equal((await go(away, fakeEngine(), fakeEnv())).local, undefined);
  const notStarted = fakeServer([1], { start: async () => { throw refused(403, "Recognizing text needs an account."); } });
  assert.equal((await go(notStarted, fakeEngine(), fakeEnv())).local, undefined);
});

test("a visible-time timer stops while the window is hidden and goes on when it is shown", () => {
  const env = fakeEnv();
  let fired = 0;
  visibleTimer(100, env, () => fired++);
  env.advance(40);
  env.show(false);
  env.advance(1000);
  env.show(true);
  env.advance(59);
  assert.equal(fired, 0);
  env.advance(1);
  assert.equal(fired, 1);
  assert.equal(env.timers.size + env.listeners.size, 0);
  // started hidden: nothing counts until it is shown; called off, it never fires
  env.show(false);
  const off = visibleTimer(10, env, () => fired++);
  env.advance(100);
  env.show(true);
  off();
  env.advance(100);
  assert.equal(fired, 1);
  assert.equal(env.timers.size + env.listeners.size, 0);
});
