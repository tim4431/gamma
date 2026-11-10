// The library's page batches (src/library/pageBatch.js): a row of
// Background tasks from ROW_FROM pages on, Stop, failures, the pool, the
// bulk requests of CHUNK pages and their per-page outcomes, and the closing
// status.
import assert from "node:assert/strict";
import { test } from "node:test";
import { CHUNK, POOL, ROW_FROM, batchText, bulkOutcomes, runBatch, runBulk } from "../src/library/pageBatch.js";

// The store's local rows (tasks/useTasks.js): `stop` is the tray's Stop,
// which calls the row's cancel and marks it cancelled.
function fakeTasks() {
  const rows = new Map();
  const patches = [];
  return {
    rows, patches,
    addLocal(row) {
      const id = `row${rows.size + 1}`;
      rows.set(id, { state: "running", ...row });
      return id;
    },
    updateLocal(id, patch) {
      patches.push(patch);
      rows.set(id, { ...rows.get(id), ...patch });
    },
    stop(id) {
      rows.get(id).cancel();
      rows.set(id, { ...rows.get(id), state: "cancelled" });
    },
  };
}

const pages = (n) => Array.from({ length: n }, (_, i) => `p${i}`);
const tick = () => new Promise((resolve) => setImmediate(resolve));

test("under ROW_FROM pages the batch runs without a row", async () => {
  const tasks = fakeTasks();
  const ran = [];
  const result = await runBatch(pages(ROW_FROM - 1), async (id) => { ran.push(id); }, { name: "Delete 9 pages", tasks });
  assert.equal(tasks.rows.size, 0);
  assert.deepEqual(ran.sort(), pages(ROW_FROM - 1).sort());
  assert.deepEqual(result, { done: ran, failed: [], stopped: false });
});

test("from ROW_FROM pages a row counts them, one patch per visible percent, and ends done", async () => {
  const tasks = fakeTasks();
  let first = null;
  const result = await runBatch(pages(340), async () => {
    first ||= { ...tasks.rows.get("row1") };
    await tick();
  }, { name: "Delete 340 pages", tasks });
  assert.equal(first.kind, "batch");
  assert.equal(first.name, "Delete 340 pages");
  assert.equal(first.info, "0 of 340 pages");
  assert.equal(first.fraction, 0);
  assert.equal(typeof first.cancel, "function");
  const progress = tasks.patches.filter((p) => !p.state);
  const steps = progress.map((p) => Math.floor(p.fraction * 100));
  assert.deepEqual(steps, [...new Set(steps)].sort((a, b) => a - b), "each patch a new percent");
  assert(progress.length <= 100, `${progress.length} patches`);
  assert.equal(progress.at(-1).info, "340 of 340 pages");
  const row = tasks.rows.get("row1");
  assert.equal(row.state, "done");
  assert.equal(row.info, "340 of 340 pages");
  assert.equal(row.cancel, null);
  assert.equal(result.done.length, 340);
  assert.equal(result.stopped, false);
});

test("Stop ends the batch before the next page, and the row takes no patch after it", async () => {
  const tasks = fakeTasks();
  const ran = [];
  let patchesAtStop = -1;
  const result = await runBatch(pages(20), async (id) => {
    ran.push(id);
    if (id === "p5") {
      tasks.stop("row1");
      patchesAtStop = tasks.patches.length;
    }
    await tick();
  }, { name: "Pin 20 pages", tasks, pool: 1 });
  assert.deepEqual(ran, pages(6), "the page at hand finishes, no other starts");
  assert.equal(tasks.patches.length, patchesAtStop, "no patch after the Stop");
  assert.equal(tasks.rows.get("row1").state, "cancelled");
  assert.deepEqual(result, { done: pages(6), failed: [], stopped: true });
});

test("a Stop as the last pages ran is a stop all the same, as the row says", async () => {
  const tasks = fakeTasks();
  const result = await runBatch(pages(ROW_FROM), async (id) => {
    if (id === `p${ROW_FROM - 1}`) tasks.stop("row1");
  }, { name: "Pin 10 pages", tasks, pool: 1 });
  assert.equal(tasks.rows.get("row1").state, "cancelled");
  assert.equal(result.stopped, true);
  assert.equal(result.done.length, ROW_FROM);
});

test("pages that fail are counted, the rest go on, and the row fails with the first error", async () => {
  const tasks = fakeTasks();
  const result = await runBatch(pages(12), async (id) => {
    if (id === "p3") throw new Error("Not found");
    if (id === "p7") throw new Error("Forbidden");
  }, { name: "Delete 12 pages", tasks, pool: 1 });
  assert.equal(result.done.length, 10);
  assert.deepEqual(result.failed.map((err) => err.message), ["Not found", "Forbidden"]);
  assert.equal(result.stopped, false);
  const row = tasks.rows.get("row1");
  assert.equal(row.state, "failed");
  assert.equal(row.info, "2 pages could not be changed: Not found");
});

test("the pool runs at most `pool` requests at once", async () => {
  for (const pool of [POOL, 1]) {
    let running = 0, most = 0;
    await runBatch(pages(30), async () => {
      most = Math.max(most, ++running);
      await tick();
      running--;
    }, { name: "Move 30 pages", tasks: fakeTasks(), pool });
    assert.equal(most, pool);
  }
});

test("runBulk sends CHUNK pages a request, counts each page's outcome, and fails a refused request's pages", async () => {
  const tasks = fakeTasks();
  const sent = [];
  const result = await runBulk(pages(CHUNK * 2 + 40), async (chunk) => {
    sent.push(chunk);
    if (sent.length === 2) throw new Error("Bad gateway");
    return chunk.map((id) => (id === "p3" ? new Error("block not found") : null));
  }, { name: "Delete 240 pages", tasks });
  assert.deepEqual(sent.map((chunk) => chunk.length), [CHUNK, CHUNK, 40]);
  assert.deepEqual(sent.flat(), pages(CHUNK * 2 + 40), "every page once, in order");
  assert.equal(result.failed.length, CHUNK + 1);
  assert.equal(result.failed[0].message, "block not found");
  assert.equal(result.done.length, CHUNK * 2 + 40 - CHUNK - 1);
  assert(!result.done.includes("p3") && !result.done.includes(`p${CHUNK}`));
  const row = tasks.rows.get("row1");
  assert.equal(row.state, "failed");
  assert.equal(row.info, `${CHUNK + 1} pages could not be changed: block not found`);
  assert.equal(tasks.patches.filter((p) => !p.state).length, 3, "one patch a request");
});

test("runBulk's Stop takes effect before the next request", async () => {
  const tasks = fakeTasks();
  let requests = 0;
  const result = await runBulk(pages(25), async (chunk) => {
    requests++;
    tasks.stop("row1");
    return chunk.map(() => null);
  }, { name: "Pin 25 pages", tasks, size: 10 });
  assert.equal(requests, 1);
  assert.deepEqual(result, { done: pages(10), failed: [], stopped: true });
  assert.equal(tasks.rows.get("row1").state, "cancelled");
});

test("bulkOutcomes turns POST /blocks/bulk's results into null or an Error with the status", () => {
  const [went, refused] = bulkOutcomes([{ id: "a", ok: true }, { id: "b", ok: false, status: 404, detail: "block not found" }]);
  assert.equal(went, null);
  assert.equal(refused.message, "block not found");
  assert.equal(refused.status, 404);
});

test("batchText says what was done, where a Stop left it and what could not be done", () => {
  const err = new Error("Not found");
  const moved = "Moved 12 pages.";
  assert.equal(batchText(moved, { done: pages(12), failed: [], stopped: false }, 12), moved);
  assert.equal(batchText("Moved 5 pages.", { done: pages(5), failed: [err], stopped: true }, 340),
    "Moved 5 pages. Stopped after 6 of 340 pages. 1 page could not be changed: Not found");
  assert.equal(batchText("Moved 9 pages.", { done: pages(9), failed: [err, err], stopped: false }, 11),
    "Moved 9 pages. 2 pages could not be changed: Not found");
  // nothing done: the note alone, not words that would read as done
  assert.equal(batchText("Already in “A”.", { done: [], failed: [err], stopped: false }, 1),
    "1 page could not be changed: Not found");
  assert.equal(batchText("Moved 0 pages.", { done: [], failed: [], stopped: true }, 1), "Stopped after 0 of 1 page.");
  assert.equal(batchText("", { done: pages(3), failed: [], stopped: false }, 3), "");
});
