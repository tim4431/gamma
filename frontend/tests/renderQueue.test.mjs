import test from "node:test";
import assert from "node:assert/strict";
import { RenderQueue, RENDER_CANCELLED } from "../src/pdf/renderQueue.js";

// A render whose end is in the test's hands.
function render(log, name) {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  const task = { promise, cancel: () => { log.push(`cancel ${name}`); reject(new Error("cancelled")); } };
  return { task, finish: () => resolve(name) };
}

// Queues a render of a page `distance` away; `started` records its starts.
function job(queue, log, name, distance) {
  const renders = [];
  const handle = queue.enter({
    distance: () => (typeof distance === "function" ? distance() : distance),
    start: () => { log.push(`start ${name}`); const r = render(log, name); renders.push(r); return r.task; },
  });
  handle.finish = () => renders.at(-1).finish();
  handle.promise.then((v) => log.push(`done ${v}`), (e) => log.push(`fail ${name}: ${e.message}`));
  return handle;
}
const tick = () => new Promise((r) => setTimeout(r, 0));
// A render ends and the next starts in the same turn; the two log lines
// land in either order.
const same = (actual, expected, msg) => assert.deepEqual([...actual].sort(), [...expected].sort(), msg);

test("one render at a time, the nearest page first", async () => {
  const log = [];
  const q = new RenderQueue();
  const a = job(q, log, "a", 1800);
  const b = job(q, log, "b", 0);
  const c = job(q, log, "c", 900);
  assert.deepEqual(log, ["start a", "cancel a"], "the page in view preempts the far render");
  await tick();
  assert.deepEqual(log.slice(2), ["start b"]);
  b.finish(); await tick();
  same(log.slice(3), ["done b", "start c"], "then the nearer of the rest");
  c.finish(); await tick();
  same(log.slice(5), ["done c", "start a"], "the preempted render starts over");
  a.finish(); await tick();
  assert.deepEqual(log.slice(7), ["done a"]);
});

test("renders away from the view wait while the scroll is fast", async () => {
  const log = [];
  let fast = true;
  const q = new RenderQueue({ fast: () => fast });
  job(q, log, "far", 500);
  assert.deepEqual(log, [], "held");
  const near = job(q, log, "near", 0);
  assert.deepEqual(log, ["start near"], "a page in view never waits");
  near.finish(); await tick();
  assert.deepEqual(log.slice(1), ["done near"], "still held");
  fast = false;
  q.poke();
  assert.deepEqual(log.slice(2), ["start far"], "starts once the scroll slows");
});

test("a cancel before the start rejects with RENDER_CANCELLED; after it, as the task does", async () => {
  const log = [];
  const q = new RenderQueue();
  const a = job(q, log, "a", 0);
  const b = job(q, log, "b", 0);
  b.cancel();
  await tick();
  assert.deepEqual(log, ["start a", `fail b: ${RENDER_CANCELLED.message}`]);
  a.cancel();
  await tick();
  assert.deepEqual(log.slice(2), ["cancel a", "fail a: cancelled"]);
  const c = job(q, log, "c", 0);
  assert.deepEqual(log.slice(4), ["start c"], "the queue goes on");
  c.finish(); await tick();
});

test("a preempted render that is cancelled meanwhile does not come back", async () => {
  const log = [];
  const q = new RenderQueue();
  const far = job(q, log, "far", 2000);
  const near = job(q, log, "near", 0);
  far.cancel();
  await tick();
  near.finish(); await tick();
  assert.deepEqual(log.slice(0, 2), ["start far", "cancel far"]);
  same(log.slice(2), ["fail far: cancelled", "start near", "done near"]);
});

test("a start that throws fails that render only", async () => {
  const log = [];
  const q = new RenderQueue();
  const broken = q.enter({ distance: () => 0, start: () => { throw new Error("no canvas"); } });
  broken.promise.catch((e) => log.push(`fail ${e.message}`));
  const ok = job(q, log, "ok", 0);
  await tick();
  assert.deepEqual(log, ["start ok", "fail no canvas"]);
  ok.finish(); await tick();
});
