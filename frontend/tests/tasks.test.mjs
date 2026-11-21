import test from "node:test";
import assert from "node:assert/strict";
import { addLocalRow, afterFollow, followed, fmtElapsed, isActive, LOCAL_LIMIT, localRow, mergeTasks, newlyFinished,
  progressFraction, remainingMs, sortTasks, trayBadge, waitsForYou } from "../src/tasks/taskModel.js";
import { TASK_KINDS, byteProgress, bytesInfo, canOpen, exportAgain, fmtRemaining, hiddenSince, kindOf, noteHidden, ocrAutoSummary,
  ocrDropSummary, ocrEnding, ocrSummary, progressCounts, progressText, quietKinds, relocationSummary, relocationSuperseded, retryOf,
  stopPath, taskActions, taskInfo, taskTime, taskTitle, turnShown } from "../src/tasks/taskKinds.js";

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
  assert.equal(progressCounts({ done: 3, total: 7, unit: "documents" }), "3 of 7 documents");
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
  // one this tab was told ended (a page's socket: a recognition landed),
  // which no listing showed running, is noticed too; one listed already is
  // not again, whatever it is told
  const told = (j) => j.id === "d" || j.id === "c";
  assert.deepEqual(newlyFinished(before, after, told).map((j) => j.id), ["a", "d"]);
  assert.deepEqual(newlyFinished(before, [job({ id: "d", state: "running" })], told), [], "still running: its end is noticed as any");
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
  assert.equal(taskTitle(job({ kind: "import-preview", params: { filename: "vault.zip" } })), "Review vault.zip");
  assert.equal(taskTitle(job({ kind: "scheduled-backup", params: { name: "Nightly" } })), "Backup task “Nightly”");
  assert.equal(taskTitle(job({ kind: "metadata", params: { papers: 12 } })), "Metadata lookup for 12 papers");
  assert.equal(taskTitle(job({ kind: "relocate-highlights", params: { page_id: "p1", title: "Paper", doc_id: "d1" } })),
    "Placing the highlights of “Paper”");
  assert.equal(taskTitle(job({ kind: "future-kind", title: "Something new" })), "Something new");
  assert.equal(taskTitle({ local: true, kind: "upload", name: "paper.pdf" }), "paper.pdf");
  assert.equal(kindOf({ kind: "nope" }).icon, "activity");
});

test("deleting a workspace or an account and the database check are rows of their own", () => {
  assert.equal(taskTitle(job({ kind: "workspace-delete", params: { ws: "w1", name: "Lab notes" } })), "Delete “Lab notes”");
  assert.equal(taskTitle(job({ kind: "account-delete", params: { username: "bob", guest: false } })), "Delete the account “bob”");
  assert.equal(taskTitle(job({ kind: "db-check" })), "Database check");
  assert.equal(kindOf(job({ kind: "workspace-delete" })).icon, "trash");
  assert.equal(kindOf(job({ kind: "account-delete" })).icon, "trash");
  assert.equal(kindOf(job({ kind: "db-check" })).icon, "database");
  assert.equal(kindOf({ local: true, kind: "batch" }).icon, "file", "the library's page batches");
  assert.equal(taskTitle({ local: true, kind: "batch", name: "Moving 40 pages" }), "Moving 40 pages");
  // the final copy counts its bytes and can still be stopped; the delete itself cannot
  const keeping = job({ kind: "workspace-delete", progress: { phase: "keeping", done: 1536, total: 3 * 1073741824, unit: "bytes", item: "Lab notes" } });
  assert.equal(progressText(keeping), "Keeping a final copy · 2 KB of 3.0 GB · Lab notes");
  assert.deepEqual(taskActions(keeping), ["stop"]);
  const deleting = job({ kind: "workspace-delete", stoppable: false, progress: { phase: "deleting", item: "Lab notes" } });
  assert.equal(progressText(deleting), "Deleting — can no longer be stopped · Lab notes");
  assert.deepEqual(taskActions(deleting), []);
  assert.equal(progressText(job({ kind: "db-check", progress: { done: 2, total: 7, unit: "files", item: "Lab notes · pages.db" } })),
    "2 of 7 files · Lab notes · pages.db");
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
  assert.deepEqual(taskActions(job({ kind: "import-preview", state: "failed" })), ["dismiss"], "so is a failed review's");
  assert.deepEqual(taskActions(job({ kind: "restore", state: "failed", params: { source: "upload" } })), ["dismiss"]);
  assert.deepEqual(taskActions(job({ kind: "scheduled-backup", state: "running", stoppable: false, readonly: true })), []);
  // a delete starts again only through its confirm
  assert.deepEqual(taskActions(job({ kind: "workspace-delete", state: "failed", params: { ws: "w1" } })), ["dismiss"]);
  assert.deepEqual(taskActions(job({ kind: "account-delete", state: "cancelled", params: { username: "bob" } })), ["dismiss"]);
  assert.deepEqual(taskActions(job({ kind: "db-check", state: "failed" })), ["retry", "dismiss"]);
  assert.equal(isActive(job({ state: "queued" })), true);
});

test("starting the same work again names the route and the body the first start had", () => {
  const exported = { page_id: "p1", folder: "", mode: "notes-pdf", pdf: false, highlights: true, notes: false,
    comments: false, notes_at: "beside" };
  assert.deepEqual(retryOf(job({ params: { ...exported, sub_pages: false } })), ["export", { ...exported, sub_pages: false }]);
  assert.deepEqual(retryOf(job({ params: { ...exported, mode: "annotated-pdf", comments: true, notes_at: "page", sub_pages: true } })),
    ["export", { ...exported, mode: "annotated-pdf", comments: true, notes_at: "page", sub_pages: true }],
    "an annotated PDF's cards and the pages inside the page come again");
  assert.deepEqual(retryOf(job({ kind: "workspace-export", params: { ws: "w1", uploads: true } })),
    ["workspace-export", { ws: "w1", user: "", uploads: true }]);
  assert.deepEqual(retryOf(job({ kind: "restore", params: { source: "snapshot", ws: "w1", snapshot: "20260929-x", mode: "merge" } })),
    ["restore-snapshot", { ws: "w1", name: "20260929-x", mode: "merge" }]);
  assert.deepEqual(retryOf(job({ kind: "relocate-highlights", params: { page_id: "p1", title: "Paper", doc_id: "d1" } })),
    ["relocate-highlights", { page_id: "p1" }], "Try again places the page's highlights in the file it carries now");
  assert.deepEqual(retryOf(job({ kind: "db-check", state: "failed" })), ["db-check", {}]);
  assert.deepEqual(retryOf(job({ id: "j1", params: { ...exported, mode: "annotated-pdf", translation: "zh-CN", sub_pages: false } })),
    ["export", { ...exported, mode: "annotated-pdf", sub_pages: false, translation_of: "j1" }],
    "an export that wrote the viewer's translation names its job, which keeps the translation it was sent");
  assert.deepEqual(retryOf(job({ params: { ...exported, mode: "annotated-pdf", translation: "", sub_pages: false } })),
    ["export", { ...exported, mode: "annotated-pdf", sub_pages: false }], "one without names none");
  assert.deepEqual(taskActions(job({ id: "j1", state: "failed", params: { mode: "annotated-pdf", translation: "zh-CN" } })),
    ["retry", "dismiss"], "the tray offers it Start again");
  assert.equal(retryOf(job({ kind: "workspace-delete", params: { ws: "w1", name: "Lab" } })), null);
  assert.equal(retryOf(job({ kind: "indexing" })), null);
  assert.equal(retryOf({ local: true, kind: "download" }), null);
});

test("a failed export's dialog offers the job again, or for a translated one the setup while its page is open", () => {
  const params = { page_id: "p1", folder: "", mode: "annotated-pdf", pdf: true, highlights: true, notes: false,
    comments: false, notes_at: "beside", sub_pages: false };
  assert.equal(exportAgain(job({ state: "failed", params }), "p2"), "retry", "its params start it again, whatever page is open");
  const translated = job({ state: "failed", params: { ...params, translation: "zh-CN" } });
  assert.equal(exportAgain(translated, "p1"), "setup", "its page is open: the setup exports it again from the viewer");
  assert.equal(exportAgain(translated, "p2"), "retry",
    "another page is open: the setup would export that one, so the job again with the translation it kept");
  assert.equal(exportAgain(translated, ""), "retry");
  assert.equal(exportAgain(job({ state: "cancelled", params: { ...params, translation: "zh-CN" } }), "p1"), "setup");
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

test("a text file's run is worded for the file: a dashed range is checked from its menu, a lost one is a tag", () => {
  const result = (fields) => ({ format: "text", highlights: 0, moved: 0, kept: 0, approx: 0, lost: 0, changed: 0, ...fields });
  assert.equal(relocationSummary(result({ highlights: 10, moved: 5, kept: 1 })),
    "Highlights placed in the file: 5 moved, 0 matched roughly, 1 stayed in place, 0 not found.");
  assert.equal(relocationSummary(result({ highlights: 10, moved: 5, kept: 1, approx: 2, lost: 2, changed: 2 })), [
    "Highlights placed in the file: 5 moved, 2 matched roughly, 1 stayed in place, 2 not found.",
    "A dashed highlight moved: right-click it to accept it or move it.",
    "2 that someone moved meanwhile were left as they are.",
    "A hollow tag marks a highlight whose text is gone: right-click it and choose Move to a selection or Use the closest match.",
  ].join(" "));
  assert.doesNotMatch(relocationSummary(result({ approx: 1 })), /PDF|Document menu|Hover/);
});

test("a recognition's row: its paper, the phases, the time left at this run's pace, and Try again on the page", () => {
  const params = { page_id: "p1", doc_id: "d1", title: "Lounis 1992" };
  const ocr = (fields) => job({ kind: "ocr", params, ...fields });
  assert.equal(taskTitle(ocr({})), "Recognizing the text of “Lounis 1992”");
  assert.equal(kindOf(ocr({})).icon, "scanText");
  assert.deepEqual(retryOf(ocr({ state: "failed" })), ["ocr", { page_id: "p1" }], "the route startJob puts under /api/jobs/");
  assert.equal(taskInfo(ocr({ state: "queued" })), "Waiting for its turn…", "one recognition runs at a time");
  assert.equal(progressText(ocr({ progress: { phase: "reading", done: 3, total: 15, unit: "pages" } })), "Reading the PDF · 3 of 15 pages");
  // A run that goes on from 40 pages an earlier one recognized: 10 more in
  // 50 s leaves 50 pages, about 4 minutes; counted from 0, under a minute.
  const since = Date.parse(at(0));
  const running = ocr({ progress: { phase: "recognizing", done: 50, total: 100, unit: "pages", since, from: 40 } });
  assert.equal(progressText(running, since + 50000), "Recognizing text · 50 of 100 pages · about 4 minutes left");
  assert.equal(progressText(running, since + 1000), "Recognizing text · 50 of 100 pages", "too soon to tell");
  const updating = ocr({ stoppable: false, progress: { phase: "updating" } });
  assert.equal(progressText(updating), "Updating search and highlights — can no longer be stopped");
  assert.deepEqual(taskActions(updating), []);
});

test("a recognition's end says what it changed, that the PDF's text already reads well, or how far it got", () => {
  const ocr = (fields) => job({ kind: "ocr", state: "done", params: { page_id: "p1", doc_id: "d1", title: "Lounis 1992" }, ...fields });
  const result = (fields) => ({ scanned: 12, recognized: 0, kept: 0, empty: 0, failed: 0, good_layer: false, shared: null,
    highlights_respelled: 0, highlights_kept: 0, ...fields });
  assert.equal(ocrSummary(ocr({}), result({ recognized: 11, empty: 1, highlights_respelled: 3 })),
    "Text recognized on 11 pages of “Lounis 1992”. 3 highlights took the new text.");
  assert.equal(ocrSummary(ocr({}), result({ recognized: 1, failed: 2 })),
    "Text recognized on 1 page of “Lounis 1992”. 2 pages could not be read; recognizing again tries them once more.");
  assert.equal(ocrSummary(ocr({}), result({ good_layer: true, kept: 3, shared: 0.976 })),
    "“Lounis 1992”'s own text already reads well (98% of its words match a fresh recognition), so it was kept.");
  assert.equal(ocrSummary(ocr({}), result({ good_layer: true, kept: 3, shared: 0.976, recognized: 2 })),
    "“Lounis 1992”'s own text already reads well (98% of its words match a fresh recognition), so it was kept. Text recognized on its 2 picture pages.",
    "its picture pages are recognized all the same");
  assert.equal(ocrSummary(ocr({}), result({ scanned: 0 })), "“Lounis 1992” has no scanned pages, so its text was left as it is.");
  assert.equal(ocrSummary(ocr({ state: "cancelled", progress: { phase: "recognizing", done: 7, total: 12, unit: "pages" } })),
    "Text recognition of “Lounis 1992” stopped after 7 of 12 pages; recognizing again goes on from there.");
  // Stopped while the PDF's pages were read (counted over all of them, as
  // the server reports it): nothing was recognized yet.
  assert.equal(ocrSummary(ocr({ state: "cancelled", progress: { phase: "reading", done: 3, total: 15, unit: "pages" } })),
    "Text recognition of “Lounis 1992” was stopped.");
});

test("the server's own run of a scan says so; a run read in a window is its own row, timed by the time its window was in front", () => {
  const params = { page_id: "p1", doc_id: "d1", title: "Lounis 1992" };
  assert.equal(taskTitle(job({ kind: "ocr", params: { ...params, auto: true } })), "Recognizing the text of “Lounis 1992” automatically");
  assert.deepEqual(retryOf(job({ kind: "ocr", state: "cancelled", params: { ...params, auto: true } })), ["ocr", { page_id: "p1" }],
    "started again, it is a run someone asked for");
  const window = { doc_id: "d1", engine: "tesseract.js 7.0.0", langs: "eng+fra+deu", ver: 1, client: "c1" };
  const tab = (fields) => job({ kind: "ocr-tab", params: { ...params, window }, ...fields });
  assert.equal(taskTitle(tab({})), "Reading the text of “Lounis 1992” in a window");
  assert.equal(kindOf(tab({})).icon, "scanText");
  assert.deepEqual(retryOf(tab({ state: "failed" })), ["ocr", { page_id: "p1", window }]);
  assert.deepEqual(taskActions(tab({})), ["stop"], "any device can stop it");
  assert.equal(typeof kindOf(tab({})).halt, "function", "its Stop here ends this window's run of it at once (tests/ocrRuns.test.mjs)");
  kindOf(tab({ id: "not-read-here" })).halt(tab({ id: "not-read-here" }));
  assert.equal(progressText(tab({ progress: { phase: "reading", done: 2, total: 6, unit: "pages" } })), "Reading the PDF · 2 of 6 pages");
  // 10 pages in 100 s since the phase began, 40 of them hidden: 10 pages
  // left read in a minute here, at 10 s a page, and in 100 s elsewhere.
  const since = Date.parse(at(0));
  noteHidden(true, since + 20000);
  noteHidden(false, since + 60000);
  assert.equal(hiddenSince(since, since + 100000), 40000);
  assert.equal(hiddenSince(since + 50000, since + 100000), 10000, "only what falls after since");
  const recognizing = tab({ progress: { phase: "recognizing", done: 10, total: 20, unit: "pages", since, from: 0 } });
  const left = TASK_KINDS["ocr-tab"].left;
  assert.equal(left(recognizing, since + 100000, true), "about 1 minute left");
  assert.equal(left(recognizing, since + 100000, false), "about 2 minutes left", "the window that reads it is another");
  assert.equal(progressText(recognizing, since + 100000), "Recognizing text · 10 of 20 pages · about 2 minutes left",
    "the tray asks whether this window reads it");
  noteHidden(true, since + 100000); // hidden again, still: up to now
  assert.equal(hiddenSince(since, since + 110000), 50000);
  noteHidden(true, since + 105000);
  assert.equal(hiddenSince(since, since + 110000), 50000, "hidden twice is one span");
  noteHidden(false, since + 110000);
  assert.equal(left(tab({ progress: { phase: "updating" } }), since, true), "");
});

test("forgetting the recognized text is a row of its own: no stop, no retry", () => {
  const drop = (fields) => job({ kind: "ocr-drop", stoppable: false, params: { page_id: "p1", doc_id: "d1", title: "Lounis 1992" }, ...fields });
  assert.equal(taskTitle(drop({})), "Forgetting the recognized text of “Lounis 1992”");
  assert.equal(kindOf(drop({})).icon, "scanText");
  assert.equal(progressText(drop({ progress: { phase: "updating" } })), "Updating search and highlights — can no longer be stopped");
  assert.deepEqual(taskActions(drop({})), []);
  assert.equal(retryOf(drop({ state: "failed" })), null, "its confirm starts it again");
  assert.deepEqual(taskActions(drop({ state: "failed", error: "boom" })), ["dismiss"]);
});

test("a forget's end says which pages read their own text again and what their highlights did", () => {
  const drop = { kind: "ocr-drop", state: "done", params: { page_id: "p1", doc_id: "d1", title: "Lounis 1992" } };
  const result = (fields) => ({ pages: 0, highlights_respelled: 0, highlights_kept: 0, highlights_changed: 0, highlights_unread: 0, ...fields });
  assert.equal(ocrDropSummary(drop, result({ pages: 1 })), "“Lounis 1992” reads its own text again on 1 page.");
  assert.equal(ocrDropSummary(drop, result({ pages: 12, highlights_respelled: 3, highlights_kept: 1, highlights_unread: 2 })),
    "“Lounis 1992” reads its own text again on 12 pages. 3 highlights took the PDF's own text as their quote. "
    + "1 highlight kept its quote, which does not read like the PDF's own text under it. "
    + "2 highlights on pages with no text of their own keep the recognized words as their quotes.");
  assert.equal(ocrDropSummary(drop, result({ pages: [1, 2], highlights_respelled: 1 })),
    "“Lounis 1992” reads its own text again on 2 pages. 1 highlight took the PDF's own text as its quote.", "pages given as a list");
});

test("an OCR job's end: the starter hears how it went, a tab showing the page hears of the server's own run, a window's run that goes on says nothing", () => {
  const params = { page_id: "p1", doc_id: "d1", title: "Lounis 1992" };
  const ended = (kind, state, fields = {}) => ({ kind, state, params, progress: {}, error: "", ...fields });
  // The tab that started it.
  for (const kind of ["ocr", "ocr-tab"]) {
    assert.equal(ocrEnding(ended(kind, "done"), { started: true }).result, ocrSummary, kind);
    assert.deepEqual(ocrEnding(ended(kind, "cancelled"), { started: true }), { text: "Text recognition of “Lounis 1992” was stopped." });
  }
  assert.equal(ocrEnding(ended("ocr-drop", "done"), { started: true }).result, ocrDropSummary);
  assert.deepEqual(ocrEnding(ended("ocr", "failed", { error: "Too long" }), { started: true }),
    { text: "Recognizing the text of “Lounis 1992” failed: Too long" });
  assert.deepEqual(ocrEnding(ended("ocr-drop", "failed", { error: "Busy" }), { started: true }),
    { text: "Forgetting the recognized text of “Lounis 1992” failed: Busy" });
  assert.equal(ocrEnding(ended("ocr-tab", "failed", { error: "interrupted" }), { started: true, running: true }), null,
    "the window still reads: it starts the job again");
  assert.deepEqual(ocrEnding(ended("ocr-tab", "failed", { error: "interrupted" }), { started: true, running: false }),
    { text: "Reading the text of “Lounis 1992” in a window failed: interrupted" });
  // Its idle timer stopped it while the window was hidden: the window,
  // shown again, starts it again and goes on. A person's Stop says so.
  const byPerson = { phase: "recognizing", done: 3, total: 9, unit: "pages" };
  const idle = ended("ocr-tab", "cancelled", { progress: { ...byPerson, idle: 600 } });
  assert.equal(ocrEnding(idle, { started: true, running: true }), null);
  assert.deepEqual(ocrEnding(idle, { started: true, running: false }),
    { text: "Text recognition of “Lounis 1992” stopped after 3 of 9 pages; recognizing again goes on from there." });
  assert.deepEqual(ocrEnding({ ...idle, progress: byPerson }, { started: true, running: true }),
    { text: "Text recognition of “Lounis 1992” stopped after 3 of 9 pages; recognizing again goes on from there." });
  // The window's run failed here and said why, stopping its job: nothing
  // more, unless the job applied after all.
  assert.equal(ocrEnding({ ...idle, progress: byPerson }, { started: true, told: true }), null);
  assert.equal(ocrEnding(ended("ocr-tab", "failed", { error: "x" }), { started: true, told: true }), null);
  assert.equal(ocrEnding(ended("ocr-tab", "done"), { started: true, told: true }).result, ocrSummary);
  // A tab that did not start it.
  const auto = ended("ocr", "done", { params: { ...params, auto: true } });
  assert.equal(ocrEnding(auto, { here: true, pageId: "p1" }).result, ocrAutoSummary, "the page on screen");
  assert.equal(ocrEnding(auto, { here: true, pageId: "p2" }), null, "another page");
  assert.equal(ocrEnding(auto, { here: false, pageId: "p1" }), null, "another workspace");
  assert.equal(ocrEnding({ ...auto, state: "cancelled" }, { here: true, pageId: "p1" }), null, "it gave way, or someone stopped it");
  assert.equal(ocrEnding(ended("ocr", "done"), { here: true, pageId: "p1" }), null, "someone else's run");
  assert.equal(ocrAutoSummary(auto, { recognized: 1 }), "Text recognized automatically on 1 page of “Lounis 1992”.");
  assert.equal(ocrAutoSummary(auto, { recognized: 14 }), "Text recognized automatically on 14 pages of “Lounis 1992”.");
  assert.equal(ocrAutoSummary(auto, { recognized: 0 }), "", "nothing recognized, nothing said");
});

test("a click opens the export and import dialogs again, or where the work is managed", () => {
  assert.equal(canOpen(job({})), true);
  assert.equal(kindOf(job({ kind: "import" })).open, "import");
  assert.equal(kindOf(job({ kind: "import-preview" })).open, "import", "a review opens the import dialog on it");
  assert.equal(kindOf(job({ kind: "snapshot" })).open, "settings:backups");
  assert.equal(kindOf(job({ kind: "metadata" })).open, "settings:maintenance");
  assert.equal(kindOf(job({ kind: "db-check" })).open, "settings:server");
  assert.equal(canOpen(job({ kind: "workspace-export" })), false);
  assert.equal(canOpen(job({ kind: "workspace-delete" })), false, "the workspace's Settings row shows it");
  assert.equal(canOpen({ local: true, kind: "download" }), false);
  assert.equal(canOpen({ local: true, kind: "uploads" }), true, "a folder upload's row opens its dialog");
  assert.equal(kindOf({ local: true, kind: "uploads" }).open, "uploads");
});

test("the time left: at the rate kept so far, once the work has run a moment and moved", () => {
  assert.equal(remainingMs(25, 100, 0, 10000), 30000, "a quarter in 10 s: 30 s to go");
  assert.equal(remainingMs(25, 100, 0, 2000), undefined, "too soon to tell");
  assert.equal(remainingMs(0, 100, 0, 10000), undefined, "nothing moved yet");
  assert.equal(remainingMs(100, 100, 0, 10000), undefined, "through");
  assert.equal(remainingMs(5, 0, 0, 10000), undefined, "no total");
  assert.equal(fmtRemaining(undefined), "");
  assert.equal(fmtRemaining(20000), "less than a minute left");
  assert.equal(fmtRemaining(29000), "less than a minute left");
  assert.equal(fmtRemaining(60000), "about 1 minute left");
  assert.equal(fmtRemaining(3.4 * 60000), "about 3 minutes left");
  assert.equal(fmtRemaining(59.7 * 60000), "about 1 hour left", "rounded up past the minutes");
  assert.equal(fmtRemaining(2.6 * 3600000), "about 3 hours left");
});

test("an upload's row: its bytes and the time left, patched once per visible percent", () => {
  const MB = 1024 * 1024;
  assert.equal(bytesInfo(12 * MB, 40 * MB, 0, 1000), "12.0 MB / 40.0 MB", "too soon for the time left");
  assert.equal(bytesInfo(10 * MB, 40 * MB, 0, 10000), "10.0 MB / 40.0 MB · less than a minute left");
  assert.equal(bytesInfo(3 * MB, 0, 0, 10000), "3.0 MB", "no total");
  let clock = 0;
  const patches = [];
  const report = byteProgress((patch) => patches.push(patch), () => clock);
  report(0, 1000);
  report(4, 1000);
  report(9, 1000);
  assert.equal(patches.length, 1, "the same percent patches once");
  clock = 4000;
  report(500, 1000);
  assert.equal(patches.length, 2);
  assert.deepEqual(patches[1], { info: "500 B / 1000 B · less than a minute left", fraction: 0.5 });
});

test("a share view follows only the share's own jobs that wait or run", () => {
  assert.equal(followed(job({ id: "a", owner: "share:tok" })), true);
  assert.equal(followed(job({ id: "b", owner: "share:tok", state: "queued" })), true);
  assert.equal(followed(job({ id: "c", owner: "share:tok", state: "done" })), false, "over");
  assert.equal(followed(job({ id: "d", owner: "u-visitor", kind: "transcribe" })), false, "the visitor's own job");
  assert.equal(followed(job({ id: "e" })), false, "no owner");
});

test("a follow round: the answer, the job failed when the share refuses it or it is gone, else the row kept", () => {
  const row = job({ id: "j", owner: "share:tok" });
  const answer = { ...row, state: "done" };
  assert.equal(afterFollow(row, { job: answer }), answer);
  const now = Date.UTC(2026, 8, 29, 13, 0);
  for (const status of [401, 403, 404]) {
    const out = afterFollow(row, { error: { status, message: `why ${status}` } }, now);
    assert.equal(out.state, "failed", `${status}`);
    assert.equal(out.error, `why ${status}`);
    assert.equal(out.stoppable, false);
    assert.equal(out.finished_at, new Date(now).toISOString());
    assert.deepEqual(newlyFinished([row], [out]).map((j) => j.id), ["j"], "it settles");
  }
  for (const error of [{ status: 500, message: "boom" }, new Error("Failed to fetch"), {}]) {
    assert.equal(afterFollow(row, { error }), row, "offline or a 5xx: asked again next round");
  }
});

test("a computer turn's row: the agent and the page, waiting for the person while it asks, its own Stop", () => {
  const params = { bucket: "p1", turn: "t1", agent: "codex", label: "Codex", title: "Thesis · draft" };
  const turn = (fields = {}) => job({ id: "computer-t1", kind: "computer-turn", title: "Codex · Thesis · draft", workspace: "w1",
    params, readonly: true, ...fields });
  assert.equal(taskTitle(turn()), "Codex on this computer: Thesis · draft", "the page's own title, a · in it kept");
  assert.equal(taskTitle(turn({ params: { ...params, title: "" } })), "Codex on this computer: Untitled");
  assert.equal(taskTitle(turn({ params: { ...params, bucket: "home", title: "" } })), "Codex on this computer: Library");
  assert.equal(taskTitle(turn({ params: { ...params, title: undefined }, title: "Thesis" })), "Codex on this computer: Thesis",
    "an older server's row: its title");
  assert.equal(taskTitle(turn({ params: { ...params, computer: "TIM-PC" } })), "Codex on TIM-PC: Thesis · draft",
    "a turn the computer that keeps another server's work folder runs");
  assert.equal(taskInfo(turn({ progress: { phase: "asking" } })), "Waiting for you");
  assert.equal(taskInfo(turn({ progress: { phase: "asking", there: true }, params: { ...params, computer: "TIM-PC" } })),
    "Waiting for your answer on TIM-PC", "a turn on another computer is answered there");
  assert.equal(taskInfo(turn({ progress: { phase: "asking" }, params: { ...params, computer: "TIM-PC" } })), "Waiting for you",
    "one it relays is answered in the chat");
  assert.equal(taskInfo(turn({ params: { ...params, computer: "TIM-PC" } })), "Working…");
  assert.equal(taskInfo(turn()), "Working…");
  assert.equal(taskInfo(turn({ state: "queued" })), "Waiting for its turn…");
  assert.equal(kindOf(turn()).open, "computer");
  assert.equal(canOpen(turn()), true);
  assert.deepEqual(taskActions(turn()), ["stop"]);
  assert.equal(stopPath(turn()), "/local-agents/chat/turns/t1/stop?ws=w1", "in the workspace the turn runs in");
  assert.equal(stopPath(turn({ workspace: "", params: { turn: "a b" } })), "/local-agents/chat/turns/a%20b/stop");
  assert.equal(stopPath(job({})), "", "other jobs stop through their cancel");
  assert.equal(stopPath({ local: true, kind: "computer-turn" }), "");
  // waiting for the person: nothing spins, and the tray button has the accent dot
  assert.equal(waitsForYou(turn({ progress: { phase: "asking" } })), true);
  assert.equal(waitsForYou(turn()), false);
  assert.equal(trayBadge([job({}), turn({ progress: { phase: "asking" } })], quietKinds), "ready");
  assert.equal(trayBadge([job({}), turn()], quietKinds), "running");
});

test("a paper handed to the browser: named by its title, waiting for you until the Connector takes it, and stoppable", () => {
  const params = { request: "r 1", host: "harvest.aps.org", wall: "captcha", source: "doi:10.1103/PhysRevA.1", title: "" };
  const paper = (more = {}, fields = {}) => job({ id: "paper-r1", kind: "paper-handoff", readonly: true,
    params: { ...params, ...more }, progress: { phase: "browser", item: "harvest.aps.org" }, ...fields });
  assert.equal(taskTitle(paper({ title: "Robust cooling of ions" })), "Get “Robust cooling of ions” in your browser");
  assert.equal(taskTitle(paper()), "Get “doi:10.1103/PhysRevA.1” in your browser", "no title: the identifier asked for");
  assert.equal(taskTitle(paper({ source: "https://harvest.aps.org/v2/x" })), "Get “harvest.aps.org” in your browser",
    "an address: its site");
  assert.equal(taskInfo(paper()), "Waiting for you in your browser · harvest.aps.org");
  // nobody has taken it up: it waits for the person, no spinner, the accent dot
  assert.equal(waitsForYou(paper()), true);
  assert.equal(trayBadge([job({}), paper()], quietKinds), "ready");
  const working = paper({}, { progress: { phase: "connector", item: "harvest.aps.org" } });
  assert.equal(waitsForYou(working), false);
  assert.equal(taskInfo(working), "Gamma Connector is on it · harvest.aps.org");
  assert.deepEqual(taskActions(paper()), ["stop"]);
  assert.equal(stopPath(paper()), "/ai/handoffs/r%201/stop");
});

test("a computer turn's row opens its conversation: its thread's, a co-author's, else the chat's own", () => {
  const params = { bucket: "p1", turn: "t1", agent: "codex", label: "Codex", title: "Draft", conversation: "c1" };
  const turn = (more = {}) => job({ id: "computer-t1", kind: "computer-turn", params: { ...params, ...more }, readonly: true });
  assert.equal(turnShown(turn()), null, "the reader's own chat");
  assert.deepEqual(turnShown(turn({ thread: "th1" })), { thread: "th1" });
  assert.deepEqual(turnShown(turn({ thread: "th1", person: "bob" })), { thread: "th1" }, "a co-author's thread turn");
  assert.deepEqual(turnShown(turn({ person: "bob" })), { conversation: "c1" }, "a co-author's chat");
  assert.equal(taskTitle(turn({ person: "bob" })), "Codex on this computer, for bob: Draft");
  assert.equal(taskTitle(turn({ person: "bob", computer: "TIM-PC" })), "Codex on TIM-PC, for bob: Draft");
  assert.equal(taskTitle(turn({ person: "" })), "Codex on this computer, for Someone: Draft", "an account gone");
});
