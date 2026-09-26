// The guide's data stays consistent: every tour step names a registered
// anchor and a catalogued event, ids are unique, versions are integers, and
// every data-guide attribute in the source is registered.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { ANCHORS } from "../src/guide/anchors.js";
import { EVENTS, eventMatches } from "../src/guide/events.js";
import { TOURS } from "../src/guide/tours/index.js";
import { canOffer, createGuideProgress, guideProgressKey, guideStorage, retiresOffer } from "../src/guide/triggers.js";

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.jsx?$/.test(name)) out.push(p);
  }
  return out;
}

test("tours reference registered anchors and catalogued events", () => {
  for (const tour of Object.values(TOURS)) {
    assert.ok(Number.isInteger(tour.version), `${tour.id}: version`);
    assert.ok(tour.steps.length > 0, `${tour.id}: steps`);
    const ids = new Set();
    assert.ok(tour.title || tour.hint, `${tour.id}: a tour needs a title for the menu and its offer`);
    if (tour.hint) {
      assert.ok(tour.trigger, `${tour.id}: a hint only ever appears by its trigger`);
      assert.equal(tour.steps.length, 1, `${tour.id}: a hint is one card`);
    }
    if (tour.trigger) {
      assert.ok(!tour.trigger.event || EVENTS.includes(tour.trigger.event), `${tour.id}: trigger event`);
      assert.ok(!tour.trigger.doneOn || EVENTS.includes(tour.trigger.doneOn.event), `${tour.id}: doneOn event`);
      assert.ok(tour.trigger.event || Object.keys({ ...tour.requires, ...tour.trigger.requires }).length,
        `${tour.id}: trigger needs an event or prerequisites`);
      const anchor = tour.offerAnchor || tour.steps[0].anchor;
      if (anchor) assert.ok(ANCHORS[anchor], `${tour.id}: offer anchor ${anchor}`);
    }
    for (const step of tour.steps) {
      assert.ok(step.id && !ids.has(step.id), `${tour.id}: duplicate or missing step id ${step.id}`);
      ids.add(step.id);
      if (step.anchor) assert.ok(ANCHORS[step.anchor], `${tour.id}/${step.id}: unregistered anchor ${step.anchor}`);
      if (step.advanceOn) assert.ok(EVENTS.includes(step.advanceOn.event), `${tour.id}/${step.id}: unknown event ${step.advanceOn.event}`);
      for (const key of ["creates", "reveal"]) {
        if (step[key]) assert.ok(ANCHORS[step[key]], `${tour.id}/${step.id}: unregistered ${key} anchor ${step[key]}`);
      }
      if (step.creates) assert.ok(step.advanceOn, `${tour.id}/${step.id}: a step that has the user make something completes when they do`);
    }
  }
});

const aiTour = TOURS["ai-chat"];
test("the first tours are manual and AI chat uses compact, conditional steps", () => {
  assert.deepEqual(TOURS["first-run"].requires || {}, {}, "the first-run tour starts from the Tours menu everywhere");
  assert.equal(TOURS["first-run"].trigger.event, undefined, "offered by state, on a demo server only");
  assert.equal(aiTour.trigger, undefined);
  assert.equal(aiTour.show, "chat");
  assert.deepEqual(aiTour.steps.map((s) => s.id), ["chat-question", "chat-voice", "chat-box", "chat-box-context"]);
  assert.ok(aiTour.steps.every((s) => !s.body));
  assert.equal(aiTour.steps[0].do[0].text, "summarize the paper for me");
  assert.deepEqual(aiTour.steps[2].requires, { pdfChatVisible: true });
});

test("progress survives reload, separates accounts, and tolerates broken browser storage", () => {
  const values = new Map();
  const storage = () => ({ getItem: (k) => values.get(k), setItem: (k, v) => values.set(k, v) });
  const progress = createGuideProgress(storage);
  progress.write(aiTour, "alice", { state: "dismissed" });
  const reload = createGuideProgress(storage);
  assert.equal(reload.read(aiTour, "alice").state, "dismissed");
  assert.equal(reload.read(aiTour, "bob"), null);
  assert.equal(guideProgressKey(TOURS["first-run"], "alice"), "gamma-guide:first-run");
  values.set(guideProgressKey(aiTour, "bob"), "{broken");
  assert.equal(reload.read(aiTour, "bob"), null);
  const blocked = createGuideProgress(() => { throw new Error("Storage unavailable"); });
  blocked.write(aiTour, "alice", { state: "offered" });
  assert.equal(blocked.read(aiTour, "alice").state, "offered");
  assert.equal(blocked.read(aiTour, "bob"), null);
});

test("every data-guide attribute in the source is registered", () => {
  const used = new Set();
  for (const file of walk(new URL("../src", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"))) {
    // guide="id", or every id in a conditional guide={… ? "id" : …}.
    for (const m of readFileSync(file, "utf8").matchAll(/\bguide=(?:"([^"]+)"|\{([^}]*)\})/g)) {
      if (m[1]) used.add(m[1]);
      else for (const id of m[2].matchAll(/"([a-z]+\.[A-Za-z]+)"/g)) used.add(id[1]);
    }
  }
  for (const id of used) assert.ok(ANCHORS[id], `data-guide="${id}" is not in guide/anchors.js`); // DockWindow passes it as guide="…"
  for (const id of Object.keys(ANCHORS)) assert.ok(used.has(id), `anchor ${id} is registered but no element carries it`);
});

test("eventMatches honours the payload match", () => {
  assert.equal(eventMatches({ event: "popover.opened", match: { name: "add" } }, "popover.opened", { name: "add" }), true);
  assert.equal(eventMatches({ event: "popover.opened", match: { name: "add" } }, "popover.opened", { name: "user" }), false);
  assert.equal(eventMatches({ event: "popover.opened" }, "page.opened", {}), false);
});

const facts = { view: "page", onPage: true, editable: true, hasPdf: true, guideAvailable: true };
const at = (name, payload = {}) => ({ name, payload });

test("a triggered tour is offered after its event, once per version", () => {
  const tables = TOURS.tables;
  assert.equal(canOffer(tables, { facts, progress: null, event: at("table.shown"), seen: 1 }), true);
  assert.equal(canOffer(tables, { facts, progress: null, event: at("page.opened"), seen: 1 }), false);
  assert.equal(canOffer(tables, { facts, progress: null }), false, "an event trigger never fires on a state check");
  assert.equal(canOffer(tables, { facts, progress: { state: "dismissed", version: tables.version }, event: at("table.shown"), seen: 1 }), false);
  assert.equal(canOffer(tables, { facts, progress: { state: "done", version: tables.version - 1 }, event: at("table.shown"), seen: 1 }), true,
    "a new version offers again");
  assert.equal(canOffer(TOURS.handwriting, { facts: { ...facts, hasPdf: false }, progress: null, event: at("ink.stroke"), seen: 1 }), false,
    "requires gates the offer");
});

test("count, doneOn and state triggers", () => {
  const palette = TOURS["quick-open"];
  assert.equal(canOffer(palette, { facts, progress: null, event: at("home.opened"), seen: 3 }), false);
  assert.equal(canOffer(palette, { facts, progress: null, event: at("home.opened"), seen: 4 }), true);
  assert.equal(retiresOffer(palette, at("palette.opened")), true);
  assert.equal(retiresOffer(palette, at("home.opened")), false);
  const ws = TOURS.workspaces;
  assert.equal(canOffer(ws, { facts: { ...facts, sharedWorkspace: true }, progress: null }), true);
  assert.equal(canOffer(ws, { facts: { ...facts, sharedWorkspace: false }, progress: null }), false);
  assert.equal(canOffer(ws, { facts: { ...facts, sharedWorkspace: true }, progress: null, event: at("page.opened") }), false,
    "a state trigger is not an event trigger");
});

test("the first-run tour is offered on a demo server's library only, and stays manual elsewhere", () => {
  const firstRun = TOURS["first-run"];
  const home = { ...facts, view: "home", onPage: false };
  assert.equal(canOffer(firstRun, { facts: { ...home, demo: true }, progress: null }), true);
  assert.equal(canOffer(firstRun, { facts: { ...home, demo: false }, progress: null }), false, "never offered off a demo server");
  assert.equal(canOffer(firstRun, { facts: { ...facts, demo: true }, progress: null }), false, "offered on the library, not on a page");
  assert.equal(canOffer(firstRun, { facts: { ...home, demo: true }, progress: null, event: at("home.opened") }), false,
    "a state trigger is not an event trigger");
  assert.equal(canOffer(firstRun, { facts: { ...home, demo: true }, progress: { state: "dismissed", version: firstRun.version } }), false,
    "once per version");
});

test("a demo server keeps guide progress in sessionStorage, anyone else in localStorage", () => {
  const saved = { local: globalThis.localStorage, session: globalThis.sessionStorage };
  const store = () => { const values = new Map(); return { values, getItem: (k) => values.get(k) ?? null, setItem: (k, v) => values.set(k, v) }; };
  const local = store(), session = store();
  globalThis.localStorage = local;
  globalThis.sessionStorage = session;
  try {
    createGuideProgress(guideStorage(true)).write(aiTour, "guest-a", { state: "done" });
    assert.equal(session.values.size, 1);
    assert.equal(local.values.size, 0, "a demo visit leaves nothing behind");
    createGuideProgress(guideStorage(false)).write(aiTour, "alice", { state: "done" });
    assert.equal(local.values.size, 1);
    assert.equal(createGuideProgress(guideStorage(false)).read(aiTour, "guest-a"), null);
  } finally {
    globalThis.localStorage = saved.local;
    globalThis.sessionStorage = saved.session;
  }
});

test("hints are single cards kept out of the Tours menu", () => {
  const hints = Object.values(TOURS).filter((t) => t.hint).map((t) => t.id);
  assert.deepEqual(hints, ["math-keys", "block-refs", "quick-open", "folders", "install"]);
});
