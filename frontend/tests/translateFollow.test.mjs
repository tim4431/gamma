// What the app says about the PDF translation jobs (src/app/translateFollow.js)
// in Node: the jobs' statuses by hand, and App's side as a fake that keeps
// what it was told.
import test from "node:test";
import assert from "node:assert/strict";
import { SLOW_MS, createJobFollower, jobName } from "../src/app/translateFollow.js";

const IDLE = { running: false, progress: 0, label: "" };

// The jobs, url → status, as translateState reports them; `set` changes one
// (a new object, as translateState makes) and syncs.
function jobs() {
  const map = new Map();
  const source = { statuses: () => [...map], snapshot: (url) => ({ status: map.get(url) || IDLE }) };
  return { map, source };
}

// App's side: its rows, status lines and pills, and what it is set to.
function app(over = {}) {
  const tasks = new Map();
  const said = [];
  const pills = [];
  let n = 0;
  const host = {
    screenUrl: "/a.pdf", langLabel: "Chinese", followsChat: true,
    retried: 0, reruns: 0, opened: 0, exports: 0, references: 0,
    addTask: (row) => { const id = ++n; tasks.set(id, { ...row }); return id; },
    updateTask: (id, patch) => tasks.set(id, { ...tasks.get(id), ...patch }),
    setStatus: (msg, action) => said.push({ msg, action }),
    postPill: (kind, pill) => pills.push(pill ? pill.msg : null),
    retry: () => { host.retried += 1; },
    rerun: () => { host.reruns += 1; },
    translateReferences: () => { host.references += 1; },
    openSettings: () => { host.opened += 1; },
    exportsTranslation: () => false,
    openExport: () => { host.exports += 1; },
    statusFree: () => true,
    ...over,
  };
  return { host, tasks, said, pills };
}

function follower(over) {
  const j = jobs();
  const a = app(over);
  const sync = createJobFollower(() => a.host, j.source);
  const set = (url, status) => { j.map.set(url, { ...IDLE, ...status }); sync(); };
  return { ...j, ...a, sync, set };
}

const run = (progress, more = {}) => ({ running: true, progress, label: "document", reading: false, ...more });
const end = (more = {}) => ({ running: false, progress: 1, label: "document", reading: false, error: null, untranslated: 0, unanswered: 0, medianMs: 1000, ...more });
const HINT = /a faster model can be picked/;
const EXPORT_HINT = /Export now to get a PDF with the translation in it/;
const exportHints = (f) => f.said.filter((s) => EXPORT_HINT.test(s.msg)).length;
// A session on a page whose annotated PDF would carry the translation.
const exporting = (over) => follower({ followsChat: false, exportsTranslation: () => true, ...over });

test("a job's row counts while it runs and says Done at its end", () => {
  const f = follower({ title: () => "Fast gates" });
  f.set("/a.pdf", run(0));
  assert.equal(f.tasks.size, 1);
  const [[id, row]] = f.tasks;
  assert.equal(row.name, "Translate “Fast gates” into Chinese");
  f.set("/a.pdf", run(0.5, { reading: true }));
  assert.equal(f.tasks.get(id).info, "reading the paper…");
  f.set("/a.pdf", run(0.5));
  assert.equal(f.tasks.get(id).info, "50%");
  f.set("/a.pdf", end());
  assert.equal(f.tasks.get(id).state, "done");
  assert.equal(f.tasks.get(id).info, "Done");
  assert.deepEqual(f.said, []);
  // Nothing more for a status seen already.
  f.sync();
  assert.equal(f.tasks.size, 1);
});

test("a job's row names what it translates, the paper on screen, and the language", () => {
  const h = { screenUrl: "/a.pdf", title: () => "Fast gates", langLabel: "Deutsch" };
  assert.equal(jobName({ label: "p.3" }, "/a.pdf", h), "Translate p. 3 of “Fast gates” into Deutsch");
  assert.equal(jobName({ label: "p.3", focus: { pn: 3, i: 2 } }, "/a.pdf", h), "Translate a paragraph on p. 3 of “Fast gates” into Deutsch");
  assert.equal(jobName({ label: "document" }, "/a.pdf", h), "Translate “Fast gates” into Deutsch");
  // Another PDF's job: the page on screen is not its paper.
  assert.equal(jobName({ label: "p.3" }, "/b.pdf", h), "Translate p. 3 into Deutsch");
  assert.equal(jobName({ label: "document" }, "/b.pdf", h), "Translate the document into Deutsch");
  assert.equal(jobName({ label: "p.3", focus: { pn: 3, i: 0 } }, "/a.pdf", { ...h, title: () => "" }), "Translate a paragraph on p. 3 into Deutsch");
});

test("a halted job's row says where it stopped", () => {
  const f = follower();
  f.set("/a.pdf", run(0.2));
  f.set("/a.pdf", end({ progress: 0.42 }));
  assert.equal(f.tasks.get(1).info, "stopped at 42%");
  assert.deepEqual(f.said, []);
});

test("a job that failed says why", () => {
  const f = follower();
  f.set("/a.pdf", run(0.2));
  f.set("/a.pdf", end({ progress: 0.2, error: "the provider is down" }));
  assert.equal(f.tasks.get(1).info, "stopped at 20%");
  assert.equal(f.tasks.get(1).state, "failed");
  assert.match(f.said[0].msg, /Translation failed: the provider is down/);
  // A job with paragraphs left over did what it could: it is done.
  f.set("/a.pdf", run(0));
  f.set("/a.pdf", end({ untranslated: 2 }));
  assert.equal(f.tasks.get(2).state, "done");
});

test("'Try again' is offered for untranslated paragraphs only while their document is on screen", () => {
  const f = follower();
  f.set("/a.pdf", run(0));
  f.set("/a.pdf", end({ untranslated: 2 }));
  assert.equal(f.tasks.get(1).info, "2 paragraphs untranslated");
  const { action } = f.said.at(-1);
  assert.equal(action.label, "Try again");
  action.run();
  assert.equal(f.host.retried, 1);
  // The reader moved on before clicking: nothing runs in the other PDF.
  f.host.screenUrl = "/b.pdf";
  action.run();
  assert.equal(f.host.retried, 1);
  // A job that ends in the background offers nothing to click.
  f.set("/c.pdf", run(0));
  f.set("/c.pdf", end({ untranslated: 1 }));
  assert.match(f.said.at(-1).msg, /1 paragraph could not be translated/);
  assert.equal(f.said.at(-1).action, undefined);
});

test("paragraphs left in the original because their requests failed say it was the connection", () => {
  const f = follower();
  f.set("/a.pdf", run(0));
  f.set("/a.pdf", end({ progress: 0.8, untranslated: 3, unanswered: 2 }));
  assert.equal(f.tasks.get(1).info, "3 paragraphs untranslated");
  const { msg, action } = f.said.at(-1);
  assert.equal(msg, "3 paragraphs could not be translated (the translation request failed).");
  assert.equal(action.label, "Try again");
  // The model's alone say so.
  f.set("/a.pdf", run(0));
  f.set("/a.pdf", end({ untranslated: 1 }));
  assert.equal(f.said.at(-1).msg, "1 paragraph could not be translated and still shows the original.");
});

test("a stopped job offers to run it again, or the settings when there is no AI provider", () => {
  const f = follower();
  f.set("/a.pdf", run(0));
  f.set("/a.pdf", end({ progress: 0.3, error: "" }));
  assert.equal(f.said.at(-1).msg, "Translation failed.");
  f.said.at(-1).action.run();
  assert.equal(f.host.reruns, 1);
  f.set("/a.pdf", run(0));
  f.set("/a.pdf", end({ progress: 0, error: "No AI provider is set up", refused: 503 }));
  assert.equal(f.said.at(-1).action.label, "Open settings");
  f.said.at(-1).action.run();
  assert.equal(f.host.opened, 1);
});

test("a paragraph asked for again says whether it failed, and keeps its earlier text when it had one", () => {
  const f = follower();
  const focus = { pn: 3, i: 2 };
  f.set("/a.pdf", run(0, { label: "p.3", focus }));
  f.set("/a.pdf", end({ label: "p.3", focus, progress: 0, unchanged: 1 }));
  assert.equal(f.tasks.get(1).info, "earlier translation kept");
  assert.equal(f.tasks.get(1).state, "failed", "no success's check");
  assert.equal(f.said.at(-1).msg, "This paragraph could not be translated again; it keeps its earlier translation.");
  f.said.at(-1).action.run();
  assert.equal(f.host.reruns, 1);
  f.set("/a.pdf", run(0, { label: "p.3", focus }));
  f.set("/a.pdf", end({ label: "p.3", focus, progress: 0, untranslated: 1 }));
  assert.equal(f.tasks.get(2).state, "failed");
  assert.equal(f.said.at(-1).msg, "This paragraph could not be translated.");
  f.said.at(-1).action.run();
  assert.equal(f.host.reruns, 2);
  assert.equal(f.host.retried, 0);
});

test("the model hint is said once, after a slow first job, when translation follows the chat model", () => {
  const f = follower();
  f.set("/a.pdf", run(0));
  f.set("/a.pdf", end({ medianMs: SLOW_MS + 1 }));
  assert.equal(f.said.length, 1);
  assert.match(f.said[0].msg, HINT);
  assert.equal(f.said[0].action.label, "Open settings");
  f.said[0].action.run();
  assert.equal(f.host.opened, 1);
  // Never again in the session, however slow.
  f.set("/b.pdf", run(0));
  f.set("/b.pdf", end({ medianMs: SLOW_MS * 3 }));
  assert.equal(f.said.length, 1);
});

test("no model hint with a model picked, or after a fast first job", () => {
  const picked = follower({ followsChat: false });
  picked.set("/a.pdf", run(0));
  picked.set("/a.pdf", end({ medianMs: SLOW_MS * 2 }));
  assert.deepEqual(picked.said, []);

  const fast = follower();
  fast.set("/a.pdf", run(0));
  fast.set("/a.pdf", end({ medianMs: SLOW_MS }));
  // The first job decided it: a slow one later says nothing.
  fast.set("/b.pdf", run(0));
  fast.set("/b.pdf", end({ medianMs: SLOW_MS * 2 }));
  assert.deepEqual(fast.said, []);
});

test("a job that reached no model leaves the model hint to a later one", () => {
  const f = follower();
  f.set("/a.pdf", run(0));
  f.set("/a.pdf", end({ medianMs: null }));
  assert.deepEqual(f.said, []);
  f.set("/b.pdf", run(0));
  f.set("/b.pdf", end({ medianMs: SLOW_MS + 1 }));
  assert.match(f.said.at(-1).msg, HINT);
});

test("the model hint waits past a job that ended with something else to say", () => {
  const f = follower();
  f.set("/a.pdf", run(0));
  f.set("/a.pdf", end({ medianMs: SLOW_MS + 1, untranslated: 3 }));
  assert.equal(f.said.length, 1);
  assert.doesNotMatch(f.said[0].msg, HINT);
  f.set("/b.pdf", run(0));
  f.set("/b.pdf", end({ progress: 0.3, error: "the provider is down", medianMs: 5 }));
  assert.equal(f.said.length, 2);
  assert.doesNotMatch(f.said[1].msg, HINT);
  f.set("/c.pdf", run(0));
  f.set("/c.pdf", end({ medianMs: 5 }));
  assert.equal(f.said.length, 3);
  assert.match(f.said[2].msg, HINT);
});

test("a finished translation says once a session that Export carries it, and its button opens the dialog", () => {
  const f = exporting();
  f.set("/a.pdf", run(0.5));
  assert.equal(exportHints(f), 0, "not while the job runs");
  f.set("/a.pdf", end());
  assert.equal(exportHints(f), 1);
  const { action } = f.said.at(-1);
  assert.equal(action.label, "Export…");
  action.run();
  assert.equal(f.host.exports, 1);
  f.set("/a.pdf", run(0.5));
  f.set("/a.pdf", end());
  f.set("/b.pdf", run(0.5));
  f.set("/b.pdf", end());
  assert.equal(exportHints(f), 1, "said once, whichever document ends next");
});

test("the export hint waits past a job that did not finish cleanly on the PDF on screen", () => {
  const f = exporting();
  const ends = (url, more) => { f.set(url, run(0.5)); f.set(url, end(more)); };
  ends("/a.pdf", { progress: 0.4 });
  assert.equal(exportHints(f), 0, "a stopped job");
  ends("/a.pdf", { progress: 0.4, error: "no key" });
  assert.equal(exportHints(f), 0, "a failed job");
  ends("/a.pdf", { untranslated: 2 });
  assert.equal(exportHints(f), 0, "a job with paragraphs left");
  ends("/b.pdf");
  assert.equal(exportHints(f), 0, "another document's job: Export writes the open page");
  ends("/a.pdf");
  assert.equal(exportHints(f), 1, "none of those spent it");
});

test("the export hint needs a translation the export would carry, and waits for one", () => {
  let carried = false;
  const f = exporting({ exportsTranslation: () => carried });
  f.set("/a.pdf", run(0.5));
  f.set("/a.pdf", end());
  assert.equal(exportHints(f), 0, "a PDF without a stored file, or the translation hidden");
  carried = true;
  f.set("/a.pdf", run(0.5));
  f.set("/a.pdf", end());
  assert.equal(exportHints(f), 1);
});

test("the export hint leaves a pill holding an error or a button alone, and waits for a later job", () => {
  let free = false;
  const f = exporting({ statusFree: () => free });
  f.set("/a.pdf", run(0.5));
  f.set("/a.pdf", end());
  assert.equal(exportHints(f), 0);
  free = true;
  f.set("/a.pdf", run(0.5));
  f.set("/a.pdf", end());
  assert.equal(exportHints(f), 1);
});

test("the export hint's button does nothing once its document has left the screen", () => {
  const f = exporting();
  f.set("/a.pdf", run(0.5));
  f.set("/a.pdf", end());
  const { action } = f.said.at(-1);
  f.host.screenUrl = "/b.pdf";
  action.run();
  assert.equal(f.host.exports, 0);
});

test("the model hint goes first, and the export hint at the next job's end", () => {
  const f = exporting({ followsChat: true });
  f.set("/a.pdf", run(0.5));
  f.set("/a.pdf", end({ medianMs: SLOW_MS + 1 }));
  assert.equal(f.said.filter((s) => HINT.test(s.msg)).length, 1);
  assert.equal(exportHints(f), 0, "one hint at a job's end");
  f.set("/a.pdf", run(0.5));
  f.set("/a.pdf", end());
  assert.equal(exportHints(f), 1);
});

const REFS_ONLY = /Nothing else to translate here/;
const REFS_HINT = /The references stay in the original/;

test("a job that found only references says so every time, with the action to ask for them", () => {
  const f = follower({ followsChat: false });
  for (let k = 1; k <= 2; k++) {
    f.set("/a.pdf", run(0));
    f.set("/a.pdf", end({ refsLeft: 8, refsOnly: true, medianMs: null }));
    assert.equal(f.said.length, k, "the click is never dead");
    assert.match(f.said.at(-1).msg, REFS_ONLY);
    assert.equal(f.said.at(-1).action.label, "Translate references too");
  }
  f.said.at(-1).action.run();
  assert.equal(f.host.references, 1);
  f.host.screenUrl = "/b.pdf";
  f.said.at(-1).action.run();
  assert.equal(f.host.references, 1, "the button does nothing once its document has left the screen");
});

test("a job that translated text says once a session that the references stayed, and the export hint goes at the next job's end", () => {
  const f = exporting();
  const ends = (url, more) => { f.set(url, run(0.5)); f.set(url, end(more)); };
  ends("/b.pdf", { refsLeft: 4 });
  assert.equal(f.said.length, 0, "not of a document off screen");
  ends("/a.pdf", { refsLeft: 4 });
  assert.equal(f.said.filter((s) => REFS_HINT.test(s.msg)).length, 1);
  assert.equal(exportHints(f), 0, "one hint at a job's end");
  ends("/a.pdf", { refsLeft: 4 });
  assert.equal(f.said.filter((s) => REFS_HINT.test(s.msg)).length, 1);
  assert.equal(exportHints(f), 1);
});

test("the references wait for a failed job, for paragraphs still to ask for and for a pill that is taken", () => {
  let free = false;
  const f = follower({ followsChat: false, statusFree: () => free });
  const ends = (more) => { f.set("/a.pdf", run(0.5)); f.set("/a.pdf", end({ refsLeft: 4, ...more })); };
  ends({ error: "no key" });
  ends({ untranslated: 2 });
  assert.equal(f.said.filter((s) => REFS_HINT.test(s.msg)).length, 0);
  ends();
  assert.equal(f.said.filter((s) => REFS_HINT.test(s.msg)).length, 0, "the pill holds an error or a button");
  free = true;
  ends();
  assert.equal(f.said.filter((s) => REFS_HINT.test(s.msg)).length, 1);
});

test("the reading pill follows the PDF on screen", () => {
  const f = follower();
  f.set("/b.pdf", run(0, { reading: true }));
  assert.deepEqual(f.pills, [], "another document's wait puts up no pill");
  f.set("/a.pdf", run(0, { reading: true }));
  assert.match(f.pills.at(-1), /Reading the paper first/);
  f.host.screenUrl = "/b.pdf";
  f.sync();
  assert.equal(f.pills.length, 1, "still reading, on the PDF now on screen");
  f.set("/b.pdf", run(0.1));
  assert.equal(f.pills.at(-1), null, "the pill comes down");
  assert.equal(f.pills.length, 2);
});

test("a document dropped while its job ran closes its row as stopped", () => {
  const f = follower();
  f.set("/a.pdf", run(0.3));
  f.map.clear();
  f.sync();
  assert.equal(f.tasks.get(1).state, "done");
  assert.equal(f.tasks.get(1).info, "stopped at 30%");
  assert.deepEqual(f.said, []);
  // The same document's next job gets a row of its own.
  f.set("/a.pdf", run(0));
  assert.equal(f.tasks.size, 2);
});
