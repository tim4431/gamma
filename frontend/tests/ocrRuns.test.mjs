// This window's recognition runs (pdf/ocrRuns.js), with a fake engine chunk
// handed in as the loader (no test loads pdf/ocrTab.js, which would run
// tesseract.js here): one at a time, the later ones waiting their turn; the
// status each reports; a stop, which stops the job through the tasks store;
// the session's end; the screen kept on while a run goes.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as ocrRuns from "../src/pdf/ocrRuns.js";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

// The engine chunk: each run waits until the test ends it (`end(result)`).
function fakeChunk() {
  const runs = [];
  let loads = 0;
  const load = async () => {
    loads++;
    return {
      run: (opts) => new Promise((resolve) => {
        runs.push({ opts, end: resolve });
        opts.signal.addEventListener("abort", () => resolve({ state: "stopped" }));
      }),
    };
  };
  return { load, runs, loads: () => loads };
}

// The browser's screen wake lock, as navigator.wakeLock: `held` the locks
// not given back.
function fakeWakeLock() {
  const lock = { asked: 0, held: 0 };
  Object.defineProperty(globalThis.navigator, "wakeLock", {
    configurable: true,
    value: {
      request: async (type) => {
        assert.equal(type, "screen");
        lock.asked++;
        lock.held++;
        return { release: async () => { lock.held--; }, addEventListener() {} };
      },
    },
  });
  return lock;
}

const CAP = { where: "window", models: "/api/ocr/models/0123456789ab" };

test("one run at a time: a later start waits its turn, and each reports how far it got", async () => {
  ocrRuns.reset();
  const chunk = fakeChunk();
  const heard = [];
  const off = ocrRuns.subscribe(() => heard.push(ocrRuns.status("a")?.phase));
  const a = ocrRuns.start({ docId: "a", pageId: "pa", title: "Paper A", capability: CAP }, { load: chunk.load });
  const b = ocrRuns.start({ docId: "b", pageId: "pb", title: "Paper B", capability: CAP }, { load: chunk.load });
  assert.equal(ocrRuns.start({ docId: "a", pageId: "pa" }, { load: chunk.load }), a, "a start of a running document is its run");
  assert.equal(ocrRuns.status("a").phase, "starting");
  assert.equal(ocrRuns.status("b").phase, "waiting");
  assert.ok(ocrRuns.running("a") && ocrRuns.running("b") && ocrRuns.running());
  await tick();
  assert.equal(chunk.runs.length, 1);
  const [first] = chunk.runs;
  assert.deepEqual({ ...first.opts, signal: undefined, report: undefined, tasks: undefined, meta: undefined },
    { docId: "a", pageId: "pa", capability: CAP, client: ocrRuns.windowClient(), signal: undefined, report: undefined, tasks: undefined, meta: undefined });
  assert.match(ocrRuns.windowClient(), /^[0-9a-z]{16,}$/);
  first.opts.report({ phase: "recognizing", jobId: "j1", page: 3, done: 1, total: 4 });
  assert.deepEqual(ocrRuns.status("a"),
    { docId: "a", title: "Paper A", phase: "recognizing", page: 3, done: 1, total: 4, jobId: "j1", error: "" });
  assert.ok(Object.isFrozen(ocrRuns.status("a")));
  first.end({ state: "done" });
  assert.equal((await a).phase, "done");
  assert.equal(ocrRuns.running("a"), false);
  assert.equal(ocrRuns.status("a").phase, "done", "the end is kept until the next start");
  await tick();
  assert.equal(chunk.runs.length, 2);
  assert.equal(ocrRuns.status("b").phase, "starting", "its turn came");
  chunk.runs[1].end({ state: "failed", error: "This PDF's text is being recognized in another window now." });
  assert.deepEqual(await b, { docId: "b", title: "Paper B", phase: "failed", page: 0, done: 0, total: 0, jobId: null,
    error: "This PDF's text is being recognized in another window now." });
  assert.equal(ocrRuns.running(), false);
  assert.equal(chunk.loads(), 2);
  assert.ok(heard.includes("recognizing") && heard.includes("done"));
  off();
  // a document whose run ended starts again
  const again = ocrRuns.start({ docId: "a", pageId: "pa", capability: CAP }, { load: chunk.load });
  assert.notEqual(again, a);
  assert.equal(ocrRuns.status("a").phase, "starting");
  ocrRuns.reset();
});

test("a stop ends the run at once and stops its job through the tasks store, even one still being started", async () => {
  ocrRuns.reset();
  const chunk = fakeChunk();
  const cancelled = [];
  const tasks = { cancel: async (task) => { cancelled.push(task); } };
  const a = ocrRuns.start({ docId: "a", pageId: "pa", tasks, capability: CAP }, { load: chunk.load });
  const b = ocrRuns.start({ docId: "b", pageId: "pb", tasks, capability: CAP }, { load: chunk.load });
  const c = ocrRuns.start({ docId: "c", pageId: "pc", tasks, capability: CAP }, { load: chunk.load });
  await tick();
  chunk.runs[0].opts.report({ jobId: "j1" });
  await ocrRuns.stop("a");
  assert.equal(chunk.runs[0].opts.signal.aborted, true);
  assert.deepEqual(cancelled, [{ id: "j1", kind: "ocr-tab", local: false }]);
  assert.equal((await a).phase, "stopped");
  assert.equal(ocrRuns.running("a"), false);
  // the next run's turn; one stopped while it waits never runs
  await ocrRuns.stop("c");
  assert.equal((await c).phase, "stopped");
  await tick();
  assert.deepEqual(chunk.runs.map((r) => r.opts.docId), ["a", "b"]);
  // stopped before its job was known: the job is stopped as it comes, once
  await ocrRuns.stop("b");
  assert.equal(cancelled.length, 1);
  chunk.runs[1].opts.report({ jobId: "j2" });
  chunk.runs[1].opts.report({ jobId: "j2" });
  await tick();
  assert.deepEqual(cancelled.map((t) => t.id), ["j1", "j2"]);
  assert.equal((await b).phase, "stopped");
  assert.equal(ocrRuns.status("b").jobId, null, "an ended run's status does not move");
  // stopping what does not run does nothing
  await ocrRuns.stop("zzz");
  await ocrRuns.stop("a");
  assert.equal(cancelled.length, 2);
});

// The tasks store as ocrRuns reaches it: the jobs it stops, and this tab's
// own rows (addLocal …), kept in `rows`; `stopRow(id)` is the row's Stop in
// Background tasks (useTasks' stopLocal).
function fakeTasks() {
  const tasks = { cancelled: [], rows: new Map(), n: 0 };
  Object.assign(tasks, {
    cancel: async (task) => { tasks.cancelled.push(task); },
    addLocal: (row) => { const id = `row${++tasks.n}`; tasks.rows.set(id, { state: "running", ...row }); return id; },
    updateLocal: (id, patch) => { if (tasks.rows.has(id)) tasks.rows.set(id, { ...tasks.rows.get(id), ...patch }); },
    removeLocal: (id) => { tasks.rows.delete(id); },
    stopRow: (id) => {
      const row = tasks.rows.get(id);
      row.cancel();
      tasks.rows.set(id, { ...row, state: "cancelled", info: "stopped", cancel: null });
    },
  });
  return tasks;
}

test("a run waiting its turn has a row of its own, whose Stop calls it off; at its turn the row goes", async () => {
  ocrRuns.reset();
  const chunk = fakeChunk();
  const tasks = fakeTasks();
  const a = ocrRuns.start({ docId: "a", pageId: "pa", title: "Paper A", tasks, capability: CAP }, { load: chunk.load });
  assert.equal(tasks.rows.size, 0, "a run that goes at once has its job's row");
  const b = ocrRuns.start({ docId: "b", pageId: "pb", title: "Paper B", tasks, capability: CAP }, { load: chunk.load });
  const c = ocrRuns.start({ docId: "c", pageId: "pc", tasks, capability: CAP }, { load: chunk.load });
  assert.deepEqual([...tasks.rows.values()].map(({ kind, name, info, state }) => ({ kind, name, info, state })), [
    { kind: "ocr-tab", name: "Reading the text of “Paper B” in a window", info: "Waiting for its turn…", state: "running" },
    { kind: "ocr-tab", name: "Reading the text of “Untitled” in a window", info: "Waiting for its turn…", state: "running" },
  ]);
  // c's row stopped in Background tasks: it never runs, and its row says so
  tasks.stopRow("row2");
  assert.equal((await c).phase, "stopped");
  assert.equal(ocrRuns.running("c"), false);
  assert.equal(tasks.rows.get("row2").state, "cancelled");
  // b stopped elsewhere (ocrRuns.stop): its row reads stopped too
  await tick();
  chunk.runs[0].end({ state: "done" });
  await a;
  await tick();
  assert.equal(tasks.rows.has("row1"), false, "b's turn: its job's row and the pill show it now");
  assert.deepEqual(chunk.runs.map((r) => r.opts.docId), ["a", "b"]);
  await ocrRuns.stop("b");
  assert.equal((await b).phase, "stopped");
  // one waiting at the session's end: its row goes
  ocrRuns.start({ docId: "d", pageId: "pd", tasks, capability: CAP }, { load: chunk.load });
  ocrRuns.start({ docId: "e", pageId: "pe", tasks, capability: CAP }, { load: chunk.load });
  assert.equal(tasks.rows.has("row3"), true);
  ocrRuns.reset();
  assert.equal(tasks.rows.has("row3"), false);
  ocrRuns.reset();
});

test("a run that fails on this window's side stops the job it leaves waiting; one the server ended does not", async () => {
  ocrRuns.reset();
  const chunk = fakeChunk();
  const tasks = fakeTasks();
  const a = ocrRuns.start({ docId: "a", pageId: "pa", tasks, capability: CAP }, { load: chunk.load });
  await tick();
  chunk.runs[0].opts.report({ jobId: "j1", phase: "recognizing" });
  chunk.runs[0].end({ state: "failed", error: "The text recognition engine did not start in this window.", local: true });
  const end = await a;
  assert.deepEqual([end.phase, end.jobId, end.error], ["failed", "j1", "The text recognition engine did not start in this window."]);
  await tick();
  assert.deepEqual(tasks.cancelled, [{ id: "j1", kind: "ocr-tab", local: false }]);
  const b = ocrRuns.start({ docId: "b", pageId: "pb", tasks, capability: CAP }, { load: chunk.load });
  await tick();
  chunk.runs[1].opts.report({ jobId: "j2" });
  chunk.runs[1].end({ state: "failed", error: "This PDF's text is being recognized in another window now." });
  await b;
  await tick();
  assert.equal(tasks.cancelled.length, 1, "the job is the other window's now");
  ocrRuns.reset();
});

test("the job's Stop in Background tasks ends this window's run of it at once, and the pill says the run goes for its whole run", async () => {
  ocrRuns.reset();
  const chunk = fakeChunk();
  const tasks = fakeTasks();
  const pills = [];
  const unfollow = ocrRuns.followPill((channel, entry) => pills.push([channel, entry]));
  assert.deepEqual(pills, [], "nothing goes");
  const a = ocrRuns.start({ docId: "a", pageId: "pa", title: "Paper A", tasks, capability: CAP }, { load: chunk.load });
  const b = ocrRuns.start({ docId: "b", pageId: "pb", title: "Paper B", tasks, capability: CAP }, { load: chunk.load });
  assert.equal(pills.length, 1, "the run that goes, not the one that waits");
  const [channel, entry] = pills[0];
  assert.equal(channel, "ocr:a");
  assert.deepEqual({ ...entry, action: { label: entry.action.label } }, {
    msg: "Reading the pages of “Paper A” in this window: keep it open and in front until it is done. A stop keeps what was read.",
    spinner: true, action: { label: "Stop" },
  });
  await tick();
  chunk.runs[0].opts.report({ jobId: "j1", phase: "recognizing", page: 2 });
  assert.equal(pills.length, 1, "its progress posts nothing more");
  // its job's row stopped here: the run ends at once, the job stopped by the row
  ocrRuns.jobStopped("j1");
  assert.equal(chunk.runs[0].opts.signal.aborted, true);
  assert.equal((await a).phase, "stopped");
  assert.deepEqual(tasks.cancelled, [], "the row's Stop stops the job, not the run");
  assert.deepEqual(pills.slice(1).map(([ch, e]) => [ch, e?.msg.slice(0, 30) || null]),
    [["ocr:a", null], ["ocr:b", "Reading the pages of “Paper B”"]], "the next run's pill");
  ocrRuns.jobStopped("j1");
  ocrRuns.jobStopped("nope");
  assert.equal(ocrRuns.running("b"), true, "another job's Stop leaves it");
  // the pill's Stop
  await tick();
  chunk.runs[1].opts.report({ jobId: "j2" });
  pills.at(-1)[1].action.run();
  assert.equal((await b).phase, "stopped");
  await tick();
  assert.deepEqual(tasks.cancelled.map((task) => task.id), ["j2"]);
  assert.deepEqual(pills.at(-1), ["ocr:b", null], "the pill goes with the run");
  // stops following, taking a pill down
  ocrRuns.start({ docId: "c", pageId: "pc", tasks, capability: CAP }, { load: chunk.load });
  unfollow();
  assert.deepEqual(pills.at(-1), ["ocr:c", null]);
  const count = pills.length;
  ocrRuns.reset();
  assert.equal(pills.length, count);
});

test("the session's end ends every run here without a word to the server", async () => {
  ocrRuns.reset();
  const chunk = fakeChunk();
  const cancelled = [];
  const tasks = { cancel: async (task) => { cancelled.push(task); } };
  const a = ocrRuns.start({ docId: "a", pageId: "pa", tasks, capability: CAP }, { load: chunk.load });
  const b = ocrRuns.start({ docId: "b", pageId: "pb", tasks, capability: CAP }, { load: chunk.load });
  await tick();
  chunk.runs[0].opts.report({ jobId: "j1", phase: "recognizing" });
  let heard = 0;
  const off = ocrRuns.subscribe(() => heard++);
  ocrRuns.reset();
  off();
  assert.equal(heard, 1);
  assert.equal(chunk.runs[0].opts.signal.aborted, true);
  assert.equal((await a).phase, "stopped");
  assert.equal((await b).phase, "stopped");
  assert.equal(ocrRuns.status("a"), null);
  assert.equal(ocrRuns.running(), false);
  // what the ended run still says changes nothing
  chunk.runs[0].opts.report({ phase: "recognizing", page: 9 });
  await tick();
  assert.equal(ocrRuns.status("a"), null);
  assert.equal(chunk.runs.length, 1, "the waiting run never started");
  assert.deepEqual(cancelled, []);
});

test("the screen is kept on from a run's start until the last run ends, however it ends", async () => {
  ocrRuns.reset();
  const lock = fakeWakeLock();
  try {
    const chunk = fakeChunk();
    const a = ocrRuns.start({ docId: "a", pageId: "pa", capability: CAP }, { load: chunk.load });
    ocrRuns.start({ docId: "b", pageId: "pb", capability: CAP }, { load: chunk.load });
    await tick();
    assert.deepEqual(lock, { asked: 1, held: 1 });
    chunk.runs[0].end({ state: "done" });
    await a;
    await tick();
    assert.deepEqual(lock, { asked: 1, held: 1 }, "kept on into the next run");
    await ocrRuns.stop("b");
    await tick();
    assert.deepEqual(lock, { asked: 1, held: 0 });
    // a failed engine chunk, and the session's end, let it go too
    const failed = ocrRuns.start({ docId: "c", pageId: "pc", capability: CAP }, { load: async () => { throw new Error("Failed to fetch dynamically imported module"); } });
    assert.deepEqual(await failed, { docId: "c", title: "", phase: "failed", page: 0, done: 0, total: 0, jobId: null,
      error: "The text recognition engine could not be loaded: reload the page." });
    await tick();
    assert.equal(lock.held, 0);
    ocrRuns.start({ docId: "d", pageId: "pd", capability: CAP }, { load: chunk.load });
    await tick();
    assert.equal(lock.held, 1);
    ocrRuns.reset();
    await tick();
    assert.equal(lock.held, 0);
  } finally {
    delete globalThis.navigator.wakeLock;
    ocrRuns.reset();
  }
});
