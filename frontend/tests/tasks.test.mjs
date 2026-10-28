import test from "node:test";
import assert from "node:assert/strict";
import { addLocalRow, fmtElapsed, isActive, LOCAL_LIMIT, localRow, mergeTasks, newlyFinished, progressFraction,
  sortTasks, trayBadge } from "../src/tasks/taskModel.js";
import { canOpen, kindOf, progressCounts, progressText, quietKinds, relocationSummary, relocationSuperseded, retryOf,
  taskActions, taskInfo, taskTime, taskTitle } from "../src/tasks/taskKinds.js";

const at = (minutes) => new Date(Date.UTC(2026, 8, 29, 12, minutes)).toISOString();
const job = (fields) => ({ kind: "export", state: "running", params: {}, progress: {}, error: "", artifact: null,
  downloaded: false, stoppable: true, created_at: at(0), started_at: "", finished_at: "", ...fields });

test("the tray lists what runs or waits first, oldest on top, then what ended, newest on top", () => {
  const rows = sortTasks([
    job({ id: "old-done", state: "done", finished_at: at(5) }),
    job({ id: "late-run", state: "running", started_at: at(9) }),
    job({ id: "new-done", state: "failed", finished_at: at(8) }),
    job({ id: "queued", state: "queued", created_at: at(10) }),
    job({ id: "early-run", state: "running", started_at: at(2) }),
  ]);
  assert.deepEqual(rows.map((r) => r.id), ["early-run", "late-run", "queued", "new-done", "old-done"]);
  const merged = mergeTasks([job({ id: "server" })], [localRow({ kind: "upload", name: "a.pdf" }, "local", Date.UTC(2026, 8, 29))]);
  assert.deepEqual(merged.map((r) => [r.id, r.local]), [["local", true], ["server", false]]);
});

test("local rows keep the newest; past the limit the oldest finished row goes, never a running one", () => {
  let rows = [];
  for (let n = 0; n < LOCAL_LIMIT; n++) rows = addLocalRow(rows, localRow({ state: n === 0 ? "done" : "running" }, `r${n}`));
  rows = addLocalRow(rows, localRow({}, "newest"));
  assert.equal(rows.length, LOCAL_LIMIT);
  assert.equal(rows[0].id, "newest");
  assert(!rows.some((r) => r.id === "r0"), "the only finished row made room");
  rows = addLocalRow(rows, localRow({}, "one more"));
  assert.equal(rows.length, LOCAL_LIMIT + 1, "running rows are never dropped");
});

test("progress: a local fraction, a job's counts, or nothing to tell", () => {
  assert.equal(progressFraction({ fraction: 1.4 }), 1);
  assert.equal(progressFraction(job({ progress: { done: 3, total: 12 } })), 0.25);
  assert.equal(progressFraction(job({ progress: { phase: "checking" } })), undefined);
  assert.equal(progressCounts({ done: 3, total: 40, unit: "pages" }), "3 of 40 pages");
  assert.equal(progressCounts({ done: 1, total: 1, unit: "files" }), "1 of 1 file");
  assert.equal(progressCounts({ done: 1536, total: 3 * 1073741824, unit: "bytes" }), "2 KB of 3.0 GB");
  assert.equal(progressCounts({ done: 1, total: 2, unit: "highlights" }), "1 of 2 highlights");
  assert.equal(progressCounts({}), "");
  assert.equal(progressText(job({ progress: { phase: "packing", done: 2, total: 5, unit: "files" } })), "Packing the files · 2 of 5 files");
  assert.equal(progressText(job({ progress: { done: 1, total: 3, unit: "pages", item: "Attention" } })), "1 of 3 pages · Attention");
  assert.equal(progressText(job({ stopping: true })), "Stopping…");
  assert.equal(progressText(job({})), "Working…");
});

test("a job that ends between two listings is noticed once, a new one only once it was seen running", () => {
  const before = [job({ id: "a" }), job({ id: "b", state: "queued" }), job({ id: "c", state: "done" })];
  const after = [job({ id: "a", state: "done" }), job({ id: "b", state: "running" }), job({ id: "c", state: "done" }),
    job({ id: "d", state: "done" })];
  assert.deepEqual(newlyFinished(before, after).map((j) => j.id), ["a"]);
});

test("the tray button: a spinner while anything runs, then a failure, then a file to download", () => {
  assert.equal(trayBadge([job({}), job({ state: "failed" })], quietKinds), "running");
  assert.equal(trayBadge([job({ state: "failed" }), job({ state: "done", artifact: { name: "x" } })], quietKinds), "failed");
  assert.equal(trayBadge([{ kind: "ai", state: "failed", local: true }], quietKinds), "", "a lookup that found nothing is routine");
  assert.equal(trayBadge([job({ state: "done", artifact: { name: "x" } })], quietKinds), "ready");
  assert.equal(trayBadge([job({ state: "done", artifact: { name: "x" }, downloaded: true })], quietKinds), "");
});

test("titles read the job's params; local rows keep their own name", () => {
  assert.equal(taskTitle(job({ params: { name: "Reading", mode: "annotated-pdf" } })), "Export “Reading” as Annotated PDF");
  assert.equal(taskTitle(job({ kind: "workspace-export", params: { all: true, uploads: false } })), "Database export of every personal workspace");
  assert.equal(taskTitle(job({ kind: "snapshot", params: { workspaces: ["a", "b"], names: ["A", "B"] } })), "Snapshots of 2 workspaces");
  assert.equal(taskTitle(job({ kind: "snapshot", params: { workspaces: ["a"], names: ["Lab"] } })), "Snapshot of “Lab”");
  assert.equal(taskTitle(job({ kind: "restore", params: { name: "Lab", mode: "merge", source: "upload", filename: "b.zip" } })), "Merge b.zip into “Lab”");
  assert.equal(taskTitle(job({ kind: "restore", params: { name: "Lab", mode: "replace", source: "snapshot" } })), "Restore “Lab” from a snapshot");
  assert.equal(taskTitle(job({ kind: "import", params: { filename: "zotero.zip" } })), "Import zotero.zip");
  assert.equal(taskTitle(job({ kind: "scheduled-backup", params: { name: "Nightly" } })), "Backup task “Nightly”");
  assert.equal(taskTitle(job({ kind: "metadata", params: { papers: 12 } })), "Metadata lookup for 12 papers");
  assert.equal(taskTitle(job({ kind: "relocate-highlights", params: { page_id: "p1", title: "Paper", doc_id: "d1" } })),
    "Placing the highlights of “Paper”");
  assert.equal(taskTitle(job({ kind: "future-kind", title: "Something new" })), "Something new");
  assert.equal(taskTitle({ local: true, kind: "upload", name: "paper.pdf" }), "paper.pdf");
  assert.equal(kindOf({ kind: "nope" }).icon, "activity");
});

test("the line under a task says where it is, or how it ended", () => {
  assert.equal(taskInfo(job({ state: "queued" })), "Waiting for its turn…");
  assert.equal(taskInfo(job({ state: "done", artifact: { name: "Lab.zip", size: 2 * 1048576 } })), "Lab.zip · 2.0 MB");
  assert.equal(taskInfo(job({ state: "done", kind: "snapshot" })), "Done");
  assert.equal(taskInfo(job({ state: "failed", error: "not a zip file" })), "not a zip file");
  assert.equal(taskInfo(job({ state: "cancelled" })), "Stopped");
  assert.equal(taskInfo({ local: true, info: "12 MB / 40 MB" }), "12 MB / 40 MB");
  const now = Date.parse(at(3)) + 5000;
  assert.equal(taskTime(job({ started_at: at(0) }), now), "3:05");
  assert.equal(taskTime(job({ state: "queued" }), now), "");
  assert.equal(fmtElapsed(3723000), "1:02:03");
});

test("what a row offers: stop while it can, then download, start again and remove", () => {
  assert.deepEqual(taskActions(job({})), ["stop"]);
  assert.deepEqual(taskActions(job({ stoppable: false })), []);
  assert.deepEqual(taskActions({ local: true, state: "running", cancel: () => {} }), ["stop"]);
  assert.deepEqual(taskActions({ local: true, state: "running" }), []);
  assert.deepEqual(taskActions(job({ state: "done", artifact: { name: "x" } })), ["download", "dismiss"]);
  assert.deepEqual(taskActions(job({ state: "failed" })), ["retry", "dismiss"]);
  assert.deepEqual(taskActions(job({ kind: "import", state: "failed" })), ["dismiss"], "an import's upload is gone");
  assert.deepEqual(taskActions(job({ kind: "restore", state: "failed", params: { source: "upload" } })), ["dismiss"]);
  assert.deepEqual(taskActions(job({ kind: "scheduled-backup", state: "running", stoppable: false, readonly: true })), []);
  assert.equal(isActive(job({ state: "queued" })), true);
});

test("starting the same work again names the route and the body the first start had", () => {
  assert.deepEqual(retryOf(job({ params: { page_id: "p1", folder: "", mode: "notes-pdf", pdf: false, highlights: true, notes: false } })),
    ["export", { page_id: "p1", folder: "", mode: "notes-pdf", pdf: false, highlights: true, notes: false }]);
  assert.deepEqual(retryOf(job({ kind: "workspace-export", params: { ws: "w1", uploads: true } })),
    ["workspace-export", { ws: "w1", user: "", uploads: true }]);
  assert.deepEqual(retryOf(job({ kind: "restore", params: { source: "snapshot", ws: "w1", snapshot: "20260929-x", mode: "merge" } })),
    ["restore-snapshot", { ws: "w1", name: "20260929-x", mode: "merge" }]);
  assert.deepEqual(retryOf(job({ kind: "relocate-highlights", params: { page_id: "p1", title: "Paper", doc_id: "d1" } })),
    ["relocate-highlights", { page_id: "p1" }], "Try again places the page's highlights in the file it carries now");
  assert.equal(retryOf(job({ kind: "indexing" })), null);
  assert.equal(retryOf({ local: true, kind: "download" }), null);
});

test("a highlights run the PDF's next replacement took over is no news", () => {
  const run = (id, page, doc, minutes, state = "running") => job({ id, kind: "relocate-highlights", state,
    params: { page_id: page, title: "Paper", doc_id: doc }, created_at: at(minutes) });
  const stopped = run("a", "p1", "v2", 1, "cancelled");
  // the replacement's own run for the next file, adopted here or seen in the listing from another tab
  assert.equal(relocationSuperseded(stopped, [stopped, run("c", "p1", "v3", 2)]), true);
  assert.equal(relocationSuperseded(stopped, [run("c", "p1", "v3", 2, "done"), stopped]), true);
  // a stop the person asked for, a run on another page or an older one, or another kind: it reports
  assert.equal(relocationSuperseded(stopped, [stopped]), false);
  assert.equal(relocationSuperseded(stopped, [stopped, run("c", "p2", "v3", 2)]), false);
  assert.equal(relocationSuperseded(stopped, [stopped, run("o", "p1", "v1", 0, "done")]), false);
  assert.equal(relocationSuperseded(stopped, [stopped, run("s", "p1", "v2", 2)]), false, "the same file again is the same run");
  assert.equal(relocationSuperseded(stopped, [stopped, job({ id: "e", created_at: at(3), params: { page_id: "p1" } })]), false);
});

test("a finished highlights run says how many moved, are dashed to check, stayed and were not found", () => {
  const result = (fields) => ({ highlights: 0, moved: 0, kept: 0, approx: 0, lost: 0, changed: 0, ...fields });
  assert.equal(relocationSummary(result({ highlights: 3, moved: 2, kept: 1 })),
    "Highlights placed in the PDF: 2 moved, 0 matched roughly or estimated, 1 stayed in place, 0 not found.");
  // a dashed one says where to compare and accept it (its review card, several the Document menu's
  // Review), a hollow one how to move it; one moved meanwhile is counted apart
  assert.equal(relocationSummary(result({ highlights: 5, moved: 1, approx: 2, lost: 1, changed: 1 })), [
    "Highlights placed in the PDF: 1 moved, 2 matched roughly or estimated, 0 stayed in place, 1 not found.",
    "Hover a dashed highlight to compare and accept it, or review all 2 from the Document menu.",
    "1 that someone moved meanwhile was left as it is.",
    "A hollow highlight's text is gone: right-click it and choose Move to a selection.",
  ].join(" "));
  assert.match(relocationSummary(result({ highlights: 1, approx: 1 })), / Hover the dashed highlight to compare and accept it\.$/);
  // a result written before the dashed count existed reads as none
  assert.equal(relocationSummary({ moved: 1, kept: 0, lost: 0 }),
    "Highlights placed in the PDF: 1 moved, 0 matched roughly or estimated, 0 stayed in place, 0 not found.");
});

test("a click opens the export and import dialogs again, or where the work is managed", () => {
  assert.equal(canOpen(job({})), true);
  assert.equal(kindOf(job({ kind: "import" })).open, "import");
  assert.equal(kindOf(job({ kind: "snapshot" })).open, "settings:backups");
  assert.equal(kindOf(job({ kind: "metadata" })).open, "settings:maintenance");
  assert.equal(canOpen(job({ kind: "workspace-export" })), false);
  assert.equal(canOpen({ local: true, kind: "download" }), false);
});
