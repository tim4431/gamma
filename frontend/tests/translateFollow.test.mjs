// What the app says about the PDF translation jobs (src/app/translateFollow.js)
// in Node: the jobs' statuses by hand, and App's side as a fake that keeps
// what it was told.
import test from "node:test";
import assert from "node:assert/strict";
import { SLOW_MS, createJobFollower } from "../src/app/translateFollow.js";

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
    retried: 0, opened: 0,
    addTask: (row) => { const id = ++n; tasks.set(id, { ...row }); return id; },
    updateTask: (id, patch) => tasks.set(id, { ...tasks.get(id), ...patch }),
    setStatus: (msg, action) => said.push({ msg, action }),
    postPill: (kind, pill) => pills.push(pill ? pill.msg : null),
    retry: () => { host.retried += 1; },
    openSettings: () => { host.opened += 1; },
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

test("a job's row counts while it runs and says 100% at its end", () => {
  const f = follower();
  f.set("/a.pdf", run(0));
  assert.equal(f.tasks.size, 1);
  const [[id, row]] = f.tasks;
  assert.equal(row.name, "Translate document → Chinese");
  f.set("/a.pdf", run(0.5, { reading: true }));
  assert.equal(f.tasks.get(id).info, "reading the paper…");
  f.set("/a.pdf", run(0.5));
  assert.equal(f.tasks.get(id).info, "50%");
  f.set("/a.pdf", end());
  assert.equal(f.tasks.get(id).state, "done");
  assert.equal(f.tasks.get(id).info, "100%");
  assert.deepEqual(f.said, []);
  // Nothing more for a status seen already.
  f.sync();
  assert.equal(f.tasks.size, 1);
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
  assert.match(f.said[0].msg, /Translation failed: the provider is down/);
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
  assert.equal(msg, "3 paragraphs could not be translated (the connection to the AI provider failed).");
  assert.equal(action.label, "Try again");
  // The model's alone say so.
  f.set("/a.pdf", run(0));
  f.set("/a.pdf", end({ untranslated: 1 }));
  assert.equal(f.said.at(-1).msg, "1 paragraph could not be translated and still shows the original.");
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
