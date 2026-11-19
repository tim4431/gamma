// A batch of uploads into the library (src/transfers/uploadBatch.js): what
// a folder holds and when it asks first, what the drop left out, the run
// POOL files at a time with its byte count, its Stop and its failures, and
// what it says once over.
import assert from "node:assert/strict";
import { test } from "node:test";
import { ASK_PAST_FILES, hiddenName, itemPath } from "../src/transfers/fileRouting.js";
import { ASK_PAST_BYTES, POOL, askFirst, batchFraction, batchLine, batchSummary, planUpload, runUploads, skippedGroups,
  uploadRoot, uploadedPage } from "../src/transfers/uploadBatch.js";

const MB = 1024 * 1024;
const item = (name, { size = 1000, path = [], route = "pdf" } = {}) => ({ file: { name, size }, filename: name, path, route });

test("a plan counts the files by route, their bytes, the folders they make and what the storage lacks", () => {
  const items = [
    item("a.pdf", { size: 3 * MB, path: ["thesis"] }),
    item("b.pdf", { size: 2 * MB, path: ["thesis", "papers"] }),
    item("README.md", { size: MB, path: ["thesis"], route: "markdown" }),
    item("fig.png", { size: MB, path: ["thesis", "figures"], route: "file" }),
  ];
  const plan = planUpload(items, { skipped: ["thesis/.git/"] });
  assert.deepEqual(plan.kinds, { pdf: 2, markdown: 1, file: 1 });
  assert.equal(plan.files, 4);
  assert.equal(plan.bytes, 7 * MB);
  assert.equal(plan.stored, 6 * MB, "a note is no stored file");
  assert.equal(plan.folders, 3, "thesis, thesis/papers, thesis/figures");
  assert.deepEqual(plan.skipped, ["thesis/.git/"]);
  assert.equal(plan.left, null, "no quota known");
  assert.equal(planUpload(items, { quota: { quota_mb: 0, used_bytes: 9e9 } }).left, null, "0 is no quota");
  assert.equal(planUpload(items, { quota: { quota_mb: 100, used_bytes: 10 * MB } }).left, null, "room enough");
  assert.equal(planUpload(items, { quota: { quota_mb: 10, used_bytes: 5 * MB } }).left, 5 * MB, "6 MB to store, 5 MB left");
  assert.equal(planUpload(items, { quota: { quota_mb: 10, used_bytes: 20 * MB } }).left, 0, "over already");
});

test("a batch asks first past a hundred files, past 500 MB, or past the room the storage has", () => {
  const plan = (fields) => ({ files: 3, bytes: MB, left: null, ...fields });
  assert.equal(askFirst(plan({})), false);
  assert.equal(askFirst(plan({ files: ASK_PAST_FILES })), false);
  assert.equal(askFirst(plan({ files: ASK_PAST_FILES + 1 })), true);
  assert.equal(askFirst(plan({ bytes: ASK_PAST_BYTES })), false);
  assert.equal(askFirst(plan({ bytes: ASK_PAST_BYTES + 1 })), true);
  assert.equal(askFirst(plan({ left: 0 })), true);
});

test("a folder's own directory names the batch; loose files or two folders have none", () => {
  assert.equal(uploadRoot([item("a", { path: ["thesis"] }), item("b", { path: ["thesis", "figs"] })]), "thesis");
  assert.equal(uploadRoot([item("a", { path: ["thesis"] }), item("b", { path: ["notes"] })]), "");
  assert.equal(uploadRoot([item("a"), item("b")]), "");
  assert.equal(uploadRoot([]), "");
});

test("what a drop left out: a hidden directory once with its count, one never read, and each other file", () => {
  assert.equal(hiddenName(".git"), true);
  assert.equal(hiddenName("node_modules"), true);
  assert.equal(hiddenName("src"), false);
  assert.equal(itemPath({ file: { name: "HEAD" }, folder: ["thesis", ".git"] }), "thesis/.git/HEAD");
  assert.equal(itemPath({ name: "x.pdf", webkitRelativePath: "papers/x.pdf" }), "papers/x.pdf");
  assert.deepEqual(skippedGroups([
    "thesis/.git/HEAD", "thesis/.git/config", "thesis/.git/refs/main", "thesis/~$draft.docx",
    "thesis/node_modules/", "thesis/.DS_Store", "thesis/.cache/a", "thesis/node_modules/",
  ]), [
    { name: "thesis/.git/", files: 3 },
    { name: "thesis/.cache/", files: 1 },
    { name: "thesis/.DS_Store", files: null },
    { name: "thesis/node_modules/", files: null },
    { name: "thesis/~$draft.docx", files: null },
  ]);
  assert.deepEqual(skippedGroups([]), []);
});

// A run whose uploads the test settles one by one: `go(name)` lets that
// item's upload resolve, `fail(name, error)` rejects it.
function controlled() {
  const waiting = new Map();
  const started = [];
  const run = (it, track) => new Promise((resolve, reject) => {
    started.push(it.filename);
    track.abortable(() => reject(Object.assign(new Error("aborted"), { aborted: true })));
    waiting.set(it.filename, { resolve, reject, track });
  });
  const tick = () => new Promise((resolve) => setImmediate(resolve));
  return {
    run, started, waiting, tick,
    go: async (name) => { waiting.get(name).resolve({ kind: "pdf", block: { id: `page-${name}` } }); await tick(); },
    fail: async (name, error) => { waiting.get(name).reject(new Error(error)); await tick(); },
  };
}

test("the run keeps POOL files on their way, counts their bytes and says how far it got", async () => {
  const items = ["a", "b", "c", "d", "e"].map((name) => item(name, { size: 1000 }));
  const c = controlled();
  const states = [];
  let clock = 0;
  const { finished } = runUploads(items, c.run, { onChange: (s) => states.push(s), now: () => clock });
  await c.tick();
  assert.equal(POOL, 3);
  assert.deepEqual(c.started, ["a", "b", "c"], "three at a time");
  c.waiting.get("a").track.progress(500, 1000);
  assert.equal(states.at(-1).sent, 500);
  c.waiting.get("a").track.progress(1001, 1000);
  assert.equal(states.at(-1).sent, 1000, "never past the file's size");
  const seen = states.length;
  c.waiting.get("a").track.progress(1001, 1000);
  assert.equal(states.length, seen, "the same percent is heard once");
  clock = 10000;
  await c.go("a");
  assert.deepEqual(c.started, ["a", "b", "c", "d"], "the next starts as one ends");
  let s = states.at(-1);
  assert.equal(s.done, 1);
  assert.deepEqual(s.current, ["b", "c", "d"]);
  assert.equal(batchFraction(s), 0.2);
  assert.equal(batchLine(s, 10000), "1 of 5 files · 1000 B of 5 KB · less than a minute left");
  await c.fail("b", "Storage quota exceeded");
  for (const name of ["c", "d", "e"]) await c.go(name);
  const end = await finished;
  assert.equal(end.done.length, 4);
  assert.deepEqual(end.failed.map((f) => [f.item.filename, f.error]), [["b", "Storage quota exceeded"]]);
  assert.deepEqual(end.unsent, []);
  assert.equal(end.stopped, false);
  s = states.at(-1);
  assert.deepEqual(s.failed, [{ name: "b", error: "Storage quota exceeded" }]);
  assert.equal(batchFraction(s), 1);
  assert.equal(uploadedPage(end.done[0].made), "page-a");
});

test("Stop aborts the files on their way and starts no more; what went stays done", async () => {
  const items = ["a", "b", "c", "d", "e"].map((name) => item(name));
  const c = controlled();
  const states = [];
  const { stop, finished } = runUploads(items, c.run, { onChange: (s) => states.push(s) });
  await c.tick();
  await c.go("a");
  stop();
  assert.equal(states.at(-1).stopped, true);
  const end = await finished;
  assert.deepEqual(end.done.map((d) => d.item.filename), ["a"]);
  assert.deepEqual(end.unsent.map((it) => it.filename).sort(), ["b", "c", "d", "e"], "aborted and never started");
  assert.deepEqual(end.failed, [], "an abort is no failure");
  assert.deepEqual(c.started, ["a", "b", "c", "d"]);
  stop(); // a second Stop does nothing
});

test("an upload that names its abort after the Stop is aborted at once", async () => {
  let ctl = null;
  const run = (it, track) => new Promise((resolve, reject) => {
    setImmediate(() => {
      ctl.stop();
      track.abortable(() => reject(Object.assign(new Error("aborted"), { aborted: true })));
    });
  });
  ctl = runUploads([item("a")], run);
  const end = await ctl.finished;
  assert.deepEqual(end.unsent.map((it) => it.filename), ["a"]);
});

test("files without bytes count by their number; an empty batch ends at once", async () => {
  const end = await runUploads([item("a", { size: 0 }), item("b", { size: 0 })], async () => ({ kind: "file", page: { id: "p" } }),
    { onChange: (s) => assert.ok(batchFraction(s) >= 0) }).finished;
  assert.equal(end.done.length, 2);
  assert.equal(batchFraction({ bytes: 0, total: 2, done: 1, failed: [] }), 0.5);
  assert.deepEqual(await runUploads([], async () => ({})).finished, { done: [], failed: [], unsent: [], stopped: false });
});

test("the closing line: all of them, some of them with the failures, or where a Stop left it", () => {
  assert.equal(batchSummary({ total: 3, done: 3, failed: [], stopped: false }), "Uploaded 3 files.");
  assert.equal(batchSummary({ total: 1, done: 1, failed: [], stopped: false }), "Uploaded 1 file.");
  const failed = [{ name: "thesis/big.pdf", error: "Storage quota exceeded" }, { name: "x.bin", error: "HTTP 500" }];
  assert.equal(batchSummary({ total: 5, done: 3, failed, stopped: false }), "Uploaded 3 of 5 files. 2 files could not be uploaded.");
  assert.equal(batchSummary({ total: 5, done: 3, failed, stopped: false }, { first: true }),
    "Uploaded 3 of 5 files. 2 could not be uploaded, among them thesis/big.pdf (Storage quota exceeded).");
  assert.equal(batchSummary({ total: 340, done: 12, failed: [], stopped: true }),
    "Stopped after 12 of 340 files: what went up stays in the library.");
  assert.equal(uploadedPage({ kind: "markdown", data: { block_id: "n1" } }), "n1");
  assert.equal(uploadedPage({ kind: "file", page: { id: "d1" } }), "d1");
  assert.equal(uploadedPage(null), "");
});
