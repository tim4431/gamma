// The guide's data stays consistent: every tour step names a registered
// anchor and a catalogued event, ids are unique, versions are integers, and
// every data-guide attribute in the source is registered.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { ANCHORS, anchorElement } from "../src/guide/anchors.js";
import { EVENTS, eventMatches } from "../src/guide/events.js";
import { TOURS } from "../src/guide/tours/index.js";
import { canOffer, createGuideProgress, factsMatch, guideProgressKey, guideStorage, retiresOffer, stepApplies } from "../src/guide/triggers.js";
import { keyNames, keyText, resolveKey } from "../src/guide/keys.js";
import { createRunLog, madeItems, recordEvent } from "../src/guide/finish.js";
import { CARD_W, placeCard } from "../src/guide/place.js";
import { MEDIA, mediaRatio } from "../src/guide/media.js";
import { DOCK_WINDOWS, DOCK_ZONES, SCENE_KINDS } from "../src/guide/scene.js";

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
      for (const key of ["creates", "reveal", "avoid"]) {
        if (step[key]) assert.ok(ANCHORS[step[key]], `${tour.id}/${step.id}: unregistered ${key} anchor ${step[key]}`);
      }
      if (step.creates) assert.ok(step.advanceOn, `${tour.id}/${step.id}: a step that has the user make something completes when they do`);
    }
  }
});

const aiTour = TOURS["ai-chat"];
test("window tours require desktop controls and only offer after opening a PDF", () => {
  const tour = TOURS.windows;
  const facts = { phone: false, view: "pdf" };
  const event = { name: "page.opened", payload: {} };
  assert.equal(canOffer(tour, { facts, event, seen: 1 }), true);
  assert.equal(canOffer(tour, { facts: { ...facts, phone: true }, event, seen: 1 }), false);
  assert.equal(canOffer(tour, { facts: { ...facts, view: "page" }, event, seen: 1 }), false);
  assert.equal(canOffer(tour, { facts, seen: 1 }), false, "mere presence offers nothing");
  assert.equal(factsMatch(tour.requires, { phone: false, view: "home" }), true, "manual start works in the library without AI");
  assert.equal(factsMatch(tour.requires, { phone: true }), false);
  // Two gestures on Chat's title, each shown by a scene and ticked by doing it.
  assert.deepEqual(tour.steps.map((s) => s.id), ["window-collapse", "window-move"]);
  for (const s of tour.steps) {
    assert.equal(s.anchor, "chat.grip");
    assert.ok(s.scene?.length && s.advanceOn && !s.required, `${s.id}: shown, light, ticked by the gesture`);
  }
});

test("the first tours are manual; AI chat has steps per place, each ending on the user's Send", () => {
  assert.deepEqual(TOURS["first-run"].requires || {}, {}, "the first-run tour starts from the Tours menu everywhere");
  assert.equal(TOURS["first-run"].trigger.event, undefined, "offered by state, on a demo server or an empty library");
  assert.equal(aiTour.trigger, undefined);
  assert.equal(aiTour.show, "chat");
  const stepsIn = (facts) => aiTour.steps.filter((s) => factsMatch(s.requires, facts)).map((s) => s.id);
  // No AI connected: the setup card, finished by opening Settings → Connections.
  assert.deepEqual(stepsIn({ aiConfigured: false, aiEditable: true, view: "pdf" }), ["chat-setup"]);
  assert.deepEqual(aiTour.steps.find((s) => s.id === "chat-setup").advanceOn, { event: "settings.opened", match: { pane: "ai" } });
  assert.deepEqual(stepsIn({ aiConfigured: false, aiEditable: false, view: "home" }), [], "a guest can't connect: no tour");
  assert.deepEqual(stepsIn({ view: "home" }), [], "nothing before the AI state is known");
  assert.deepEqual(stepsIn({ aiConfigured: true, view: "pdf", pdfChatVisible: true }),
    ["chat-question", "chat-figure", "chat-send-paper"]);
  assert.deepEqual(aiTour.steps.find((s) => s.id === "chat-figure").do.at(-1), { point: "chat.imageContext", wait: 1400 },
    "the figure demo ends on the snapshot it made");
  assert.deepEqual(stepsIn({ aiConfigured: true, view: "pdf", pdfChatVisible: false }), ["chat-question", "chat-send-paper"]);
  assert.deepEqual(stepsIn({ aiConfigured: true, view: "page" }), ["chat-question-notes", "chat-send-notes"]);
  assert.deepEqual(stepsIn({ aiConfigured: true, view: "home" }), ["chat-question-library", "chat-tools", "chat-send-library"]);
  for (const s of aiTour.steps.filter((x) => x.id.startsWith("chat-send-"))) {
    assert.deepEqual(s.advanceOn, { event: "chat.sent" }, `${s.id} waits for the user's own send`);
  }
  // Its demos only type: nothing presses Send or the mic (which carries no anchor to point at).
  assert.ok(aiTour.steps.filter((s) => s.do).every((s) => s.do.every((a) => !a.press && !a.click)), "the tour never sends or records");
});

test("a tour's end state is `restore`, never to be confused with its `finishCard`", () => {
  assert.equal(TOURS.handwriting.restore, "pen", "the handwriting tour re-arms the pen when it runs to its end");
  for (const tour of Object.values(TOURS)) assert.ok(!("finish" in tour), `${tour.id}: \`finish\` is now \`restore\``);
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

test("a step's drawing is a registered id with a file, and every drawing is used", () => {
  const dir = new URL("../src/guide/media/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
  const files = new Set(readdirSync(dir).filter((n) => n.endsWith(".svg")).map((n) => n.replace(/\.svg$/, "")));
  const used = new Set();
  for (const tour of Object.values(TOURS)) {
    for (const step of tour.steps) {
      if (!step.media) continue;
      assert.ok(MEDIA[step.media], `${tour.id}/${step.id}: unregistered media ${step.media}`);
      assert.ok(files.has(step.media), `${tour.id}/${step.id}: no guide/media/${step.media}.svg`);
      used.add(step.media);
    }
  }
  for (const id of Object.keys(MEDIA)) {
    assert.ok(files.has(id), `media ${id} is registered but guide/media/${id}.svg is missing`);
    assert.ok(used.has(id), `media ${id} is registered but no step shows it`);
  }
  for (const id of files) assert.ok(MEDIA[id], `guide/media/${id}.svg is not in guide/media.js`);
  // The card is 320 px wide: a very tall drawing would push the copy off a
  // short viewport, and place.js measures the card once.
  for (const [id, m] of Object.entries(MEDIA)) {
    assert.ok(m.ratio >= 1.4 && m.ratio <= 2.2, `media ${id}: ratio ${m.ratio} outside 1.4-2.2`);
    assert.ok(m.description, `media ${id}: needs a one-line description`);
    assert.equal(mediaRatio(id), m.ratio);
  }
  assert.equal(mediaRatio("nope"), 1.6, "an unknown id still gives the box a height");
});

// A drawing is inlined into the card, so it is built from guide/media.css's
// grammar alone: shapes carrying its classes, which are scoped under
// .guideMedia and paint from the theme's tokens (eight themes, so a drawing
// with colours of its own would be wrong in most of them). A drawing brings
// no <style>, no colour, no words and no motion of its own.
test("a drawing keeps its colours and its rules to itself", () => {
  const dir = new URL("../src/guide/media/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
  const RAW_COLOUR = /#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?)\(/i;
  const css = readFileSync(new URL("../src/guide/media.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  assert.ok(/^import "\.\/media\.css";/m.test(readFileSync(new URL("../src/guide/GuideOverlay.jsx", import.meta.url), "utf8")),
    'GuideOverlay, which inlines the drawings, loads their grammar: import "./media.css"');
  // The style rules' selectors, through @media; what @keyframes and
  // @property hold are not style rules.
  const selectors = [];
  const open = [];
  let at = 0;
  for (let i = 0; i < css.length; i++) {
    if (css[i] === "{") {
      const prelude = css.slice(at, i).trim();
      if (!prelude.startsWith("@") && open.every((p) => p.startsWith("@media"))) selectors.push(...prelude.split(",").map((s) => s.trim()));
      open.push(prelude);
    } else if (css[i] === "}") open.pop();
    if ("{};".includes(css[i])) at = i + 1;
  }
  for (const s of selectors) assert.match(s, /^\.guideMedia\b/, `media.css: "${s}" reaches outside a drawing's box`);
  assert.doesNotMatch(css, RAW_COLOUR, "media.css: raw colour, use a var(--…) token");
  const grammar = new Set(selectors.map((s) => s.match(/^\.guideMedia \.([\w-]+)$/)?.[1]).filter(Boolean));
  assert.ok(grammar.has("m-card") && grammar.has("m-draw"), "media.css: the grammar's .guideMedia .m-… rules");

  const SHAPES = new Set(["svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon"]);
  const ATTRS = new Set(["xmlns", "viewBox", "focusable", "class", "style", "d", "pathLength", "transform",
    "x", "y", "width", "height", "rx", "ry", "cx", "cy", "r", "x1", "y1", "x2", "y2", "points"]);
  for (const id of Object.keys(MEDIA)) {
    const svg = readFileSync(join(dir, `${id}.svg`), "utf8");
    assert.match(svg, /^<svg\b[^>]*>\s*<!--/, `${id}: opens with a comment saying what it shows`);
    const markup = svg.replace(/<!--[\s\S]*?-->/g, "");
    assert.doesNotMatch(markup, /<style\b/, `${id}: no <style>: its look and motion are media.css's classes`);
    assert.doesNotMatch(markup, /<text\b/, `${id}: no <text>: words belong on the card, where they are translated`);
    assert.doesNotMatch(markup, RAW_COLOUR, `${id}: raw colour, use media.css's classes`);
    const root = markup.match(/<svg\b([^>]*)>/)[1];
    assert.match(root, /\sviewBox="/, `${id}: needs a viewBox`);
    assert.doesNotMatch(root, /\s(?:width|height)=/, `${id}: the root sizes itself from CSS, not from width/height`);
    for (const [, name, attrText] of markup.matchAll(/<([a-zA-Z][\w:-]*)\b([^>]*?)\/?>/g)) {
      const attrs = Object.fromEntries([...attrText.matchAll(/([\w:-]+)\s*=\s*"([^"]*)"/g)].map((m) => [m[1], m[2]]));
      const where = `${id}: <${name}${attrs.class ? ` class="${attrs.class}"` : ""}>`;
      assert.ok(SHAPES.has(name), `${where} is not a shape`);
      for (const a of Object.keys(attrs)) assert.ok(ATTRS.has(a), `${where}: ${a}= — paint, ids and handlers stay out; the classes carry the look`);
      const classes = (attrs.class || "").split(/\s+/).filter(Boolean);
      for (const c of classes) assert.ok(grammar.has(c), `${where}: no .guideMedia .${c} rule in media.css`);
      if (attrs.style !== undefined) {
        const order = attrs.style.match(/^\s*--m-at:\s*(\d*\.?\d+)\s*;?\s*$/);
        assert.ok(order, `${where}: the only inline style is the motion order, --m-at`);
        // the clock reaches 1 at the end of a loop: a later start would not
        // finish, and the picture would not rest complete
        assert.ok(+order[1] <= 0.7, `${where}: --m-at ${order[1]} is over 0.7`);
      }
      if (classes.includes("m-draw")) assert.equal(attrs.pathLength, "1", `${where}: m-draw needs pathLength="1"`);
      if (classes.includes("m-appear")) assert.ok(!attrs.transform, `${where}: m-appear owns the transform; move it to a parent <g>`);
    }
  }
});

test("eventMatches honours the payload match", () => {
  assert.equal(eventMatches({ event: "popover.opened", match: { name: "add" } }, "popover.opened", { name: "add" }), true);
  assert.equal(eventMatches({ event: "popover.opened", match: { name: "add" } }, "popover.opened", { name: "user" }), false);
  assert.equal(eventMatches({ event: "popover.opened" }, "page.opened", {}), false);
});

const facts = { view: "page", onPage: true, editable: true, hasPdf: true, guideAvailable: true, phone: false };
const at = (name, payload = {}) => ({ name, payload });

test("a triggered tour is offered after its event, once per version", () => {
  const tables = TOURS.tables;
  assert.equal(canOffer(tables, { facts, progress: null, event: at("table.created"), seen: 1 }), true);
  assert.equal(canOffer(tables, { facts, progress: null, event: at("page.opened"), seen: 1 }), false);
  assert.equal(canOffer(tables, { facts, progress: null, event: at("table.shown"), seen: 1 }), false,
    "a table that merely renders (opening a page that has one) offers nothing");
  assert.equal(canOffer(tables, { facts, progress: null }), false, "an event trigger never fires on a state check");
  assert.equal(canOffer(tables, { facts, progress: { state: "dismissed", version: tables.version }, event: at("table.created"), seen: 1 }), false);
  assert.equal(canOffer(tables, { facts, progress: { state: "done", version: tables.version - 1 }, event: at("table.created"), seen: 1 }), true,
    "a new version offers again");
  assert.equal(canOffer(TOURS.handwriting, { facts: { ...facts, hasPdf: false }, progress: null, event: at("ink.stroke"), seen: 1 }), false,
    "requires gates the offer");
});

test("count, doneOn and state triggers", () => {
  const palette = TOURS["quick-open"];
  assert.equal(canOffer(palette, { facts, progress: null, event: at("home.opened"), seen: 3 }), false);
  assert.equal(canOffer(palette, { facts, progress: null, event: at("home.opened"), seen: 4 }), true);
  assert.equal(canOffer(palette, { facts: { ...facts, phone: true }, progress: null, event: at("home.opened"), seen: 4 }), false,
    "no Ctrl+P hint on the phone layout");
  assert.equal(retiresOffer(palette, at("palette.opened")), true);
  assert.equal(retiresOffer(palette, at("home.opened")), false);
  const ws = TOURS.workspaces;
  assert.equal(canOffer(ws, { facts: { ...facts, sharedWorkspace: true }, progress: null }), true);
  assert.equal(canOffer(ws, { facts: { ...facts, sharedWorkspace: false }, progress: null }), false);
  assert.equal(canOffer(ws, { facts: { ...facts, sharedWorkspace: true }, progress: null, event: at("page.opened") }), false,
    "a state trigger is not an event trigger");
});

test("the Back hint comes with the first link jump and retires once Back is used", () => {
  const back = TOURS.back;
  assert.equal(canOffer(back, { facts, progress: null, event: at("nav.pushed"), seen: 1 }), true);
  assert.equal(retiresOffer(back, at("nav.back")), true);
  assert.equal(retiresOffer(back, at("nav.pushed")), false);
});

test("the first-run tour is offered on a demo server's library or an empty one, and stays manual elsewhere", () => {
  const firstRun = TOURS["first-run"];
  const home = { ...facts, view: "home", onPage: false, emptyLibrary: false };
  assert.equal(canOffer(firstRun, { facts: { ...home, demo: true }, progress: null }), true);
  assert.equal(canOffer(firstRun, { facts: { ...home, demo: false }, progress: null }), false, "not on a library with pages in it");
  assert.equal(canOffer(firstRun, { facts: { ...home, demo: false, emptyLibrary: true }, progress: null }), true, "every new account is invited");
  assert.equal(canOffer(firstRun, { facts: { ...home, emptyLibrary: true, editable: false }, progress: null }), false,
    "not where the tour could not add its paper");
  assert.equal(canOffer(firstRun, { facts: { ...home, emptyLibrary: true }, progress: { state: "running", step: 3, version: firstRun.version } }), false,
    "not once the tour has been started from the menu");
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

test("the sharing tour follows the popover and words access for an anyone-with-the-link share", () => {
  assert.equal(factsMatch({ shareAudience: ["", "list"] }, { shareAudience: "list" }), true, "an array requires one of its values");
  assert.equal(factsMatch({ shareAudience: ["", "list"] }, { shareAudience: "anyone" }), false);
  const steps = (shareAudience) => TOURS.sharing.steps.filter((s) => factsMatch(s.requires, { shareAudience })).map((s) => s.id);
  assert.deepEqual(steps("anyone"), ["share-create", "share-link", "share-people", "share-access-anyone"]);
  for (const audience of ["", "users", "list"]) {
    assert.deepEqual(steps(audience), ["share-create", "share-link", "share-people", "share-access"], `audience "${audience}"`);
  }
  for (const id of ["share-access-anyone", "share-access"]) {
    const s = TOURS.sharing.steps.find((x) => x.id === id);
    assert.match(s.body, /Stop sharing/, `${id}: the last card says how to turn the link off`);
    assert.ok(s.next, `${id}: the last card`);
  }
  const create = TOURS.sharing.steps[0];
  assert.equal(create.creates, "share.link", "the link exists only once the page is shared");
  assert.equal(create.advanceOn.event, "share.created");
});

test("the viewer tours share their zoom and pen, and drop them once the sibling is done", () => {
  const [pdf, nb] = [TOURS["pdf-viewer"], TOURS["notebook-view"]];
  assert.equal(pdf.sibling, nb.id);
  assert.equal(nb.sibling, pdf.id);
  const shared = (tour) => tour.steps.filter((s) => s.shared);
  assert.deepEqual(shared(pdf).map((s) => s.id), ["viewer-zoom", "viewer-pen"]);
  shared(pdf).forEach((s, i) => assert.equal(s, shared(nb)[i], `${s.id}: the same object in both tours`));
  assert.equal(shared(nb).length, 2);
  const ids = (tour, facts, siblingDone) => tour.steps.filter((s) => stepApplies(s, facts, siblingDone)).map((s) => s.id);
  const desk = { editable: true, phone: false };
  assert.deepEqual(ids(pdf, desk, false), ["pdf-outline", "viewer-zoom", "viewer-pen", "pdf-translate"]);
  assert.deepEqual(ids(pdf, desk, true), ["pdf-outline", "pdf-translate"], "after the notebook view: what a PDF adds");
  assert.deepEqual(ids(nb, desk, false), ["viewer-zoom", "viewer-pen", "nbv-paper", "nbv-notes"]);
  assert.deepEqual(ids(nb, desk, true), ["nbv-paper", "nbv-notes"], "after a PDF: what the notebook view adds");
  assert.deepEqual(ids(nb, { editable: false }, false), ["viewer-zoom", "nbv-notes"], "read only: no pen, no paper");
  assert.deepEqual(ids(nb, { editable: false }, true), ["nbv-notes"]);
  assert.deepEqual(ids(pdf, { editable: true, touch: true }, false),
    ["pdf-outline", "viewer-zoom", "viewer-pen", "pdf-translate", "pdf-select"], "a touch screen picks what a drag does");
  // Without a sibling done, a shared step always shows.
  assert.equal(stepApplies({ id: "s", shared: true }, {}), true);
  assert.equal(stepApplies({ id: "s", shared: true, requires: { phone: true } }, { phone: false }), false, "requires still holds");
  // Arrange windows takes the first paper's offer; the viewer comes later.
  const order = Object.keys(TOURS);
  assert.ok(order.indexOf("windows") < order.indexOf("pdf-viewer"), "the registry lists Arrange windows first");
  assert.equal(canOffer(nb, { facts: { notebookView: true, viewerTools: true }, progress: null }), true, "offered in the notebook view");
  assert.equal(canOffer(nb, { facts: { notebookView: false, viewerTools: true }, progress: null }), false);
  assert.equal(canOffer(pdf, { facts: { hasPdf: true, viewerTools: false }, progress: null, event: { name: "page.opened" }, seen: 1 }), false,
    "not while the viewer is closed or under a phone panel");
});

test("the Connector and Cloud hints wait for what they suggest to be missing", () => {
  const connector = TOURS.connector;
  const base = { ...facts, connectorHere: false };
  assert.equal(canOffer(connector, { facts: base, progress: null, event: at("paper.fetched"), seen: 1 }), true);
  assert.equal(canOffer(connector, { facts: { ...base, connectorHere: true }, progress: null, event: at("paper.fetched"), seen: 1 }), false,
    "a browser that has the Connector is not offered it");
  assert.equal(canOffer(connector, { facts: { ...base, connectorHere: undefined }, progress: null, event: at("paper.fetched"), seen: 1 }), false,
    "nothing is suggested while the answer is unknown (the desktop app, or still asking)");
  assert.equal(canOffer(connector, { facts: base, progress: null }), false, "mere presence offers nothing");

  const cloud = TOURS["cloud-account"];
  const linkable = { ...facts, cloudLinkable: true, prefsChanged: true };
  assert.equal(canOffer(cloud, { facts: linkable, progress: null }), true, "a state trigger needs no event");
  assert.equal(canOffer(cloud, { facts: { ...linkable, prefsChanged: false }, progress: null }), false,
    "it comes with a setting that would travel, not before");
  assert.equal(canOffer(cloud, { facts: { ...linkable, cloudLinkable: false }, progress: null }), false,
    "an account already linked, or a server without cloud sign-in, hears nothing");
});

test("hints are single cards kept out of the Tours menu", () => {
  const hints = Object.values(TOURS).filter((t) => t.hint).map((t) => t.id);
  assert.deepEqual(hints, ["math-keys", "block-refs", "quick-open", "back", "conflicts", "folders", "install",
    "approvals", "export-page", "clone-sync", "connector", "cloud-account"]);
});

// A hint interrupts, so it stays one card of words: nothing drawn, nothing acted out.
test("no hint has a scene or a drawing", () => {
  for (const tour of Object.values(TOURS).filter((t) => t.hint)) {
    for (const step of tour.steps) {
      assert.ok(!step.scene, `${tour.id}: a hint has no scene`);
      assert.ok(!step.media, `${tour.id}: a hint has no drawing`);
    }
  }
});

// A scene is a looping drawing over the real UI: each primitive is one of
// the kinds guide/scene.js draws, and names registered anchors, like a step.
test("every scene primitive is a known kind on a registered anchor", () => {
  let scenes = 0;
  for (const tour of Object.values(TOURS)) {
    for (const step of tour.steps) {
      if (!step.scene) continue;
      scenes++;
      const where = `${tour.id}/${step.id}`;
      assert.ok(Array.isArray(step.scene) && step.scene.length > 0, `${where}: a scene is a non-empty list`);
      for (const p of step.scene) {
        const kinds = SCENE_KINDS.filter((k) => k in p);
        assert.equal(kinds.length, 1, `${where}: ${JSON.stringify(p)} needs exactly one of ${SCENE_KINDS.join(", ")}`);
        const [kind] = kinds;
        if (kind === "wait") { assert.ok(Number.isFinite(p.wait) && p.wait > 0, `${where}: wait is a duration`); continue; }
        assert.ok(ANCHORS[p[kind]], `${where}: ${kind} names unregistered anchor ${p[kind]}`);
        if (p.at) {
          assert.equal(p.at.length, kind === "stroke" ? 4 : 2, `${where}: ${kind}'s at`);
          assert.ok(p.at.every((f) => f >= 0 && f <= 1), `${where}: at is fractions of the anchor's box`);
        }
        if (kind === "click") {
          assert.ok(p.count === undefined || [1, 2].includes(p.count), `${where}: click count`);
          assert.ok(p.button === undefined || ["left", "right"].includes(p.button), `${where}: click button`);
        }
        if (kind === "drag") {
          assert.ok(DOCK_ZONES.includes(p.to?.zone), `${where}: drag zone ${p.to?.zone}`);
          assert.ok(DOCK_WINDOWS.includes(p.to.window), `${where}: drag window ${p.to.window}`);
        }
      }
    }
    // A demo's `point` is a pause on an anchor too.
    for (const step of tour.steps) {
      for (const a of step.do || []) {
        if (a.point) assert.ok(ANCHORS[a.point], `${tour.id}/${step.id}: demo points at unregistered anchor ${a.point}`);
      }
    }
  }
  assert.ok(scenes > 0, "some step shows a scene");
});

// Keys in guide copy: `{key:<command id>}` shows the account's chord for a
// catalog command, `{key:<chord>}` a fixed key (guide/keys.js).
test("every {key:…} in guide copy names a catalog command or a key", () => {
  for (const tour of Object.values(TOURS)) {
    for (const name of keyNames(JSON.stringify(tour))) {
      assert.ok(resolveKey(name, {}), `${tour.id}: {key:${name}} is neither a catalog command nor a key`);
    }
  }
});

test("a {key:…} follows the account's keybindings", () => {
  assert.deepEqual(resolveKey("app.quickOpen", {}), { chord: "Mod-p" });
  assert.deepEqual(resolveKey("app.quickOpen", { "app.quickOpen": "Mod-Shift-o" }), { chord: "Mod-Shift-o" }, "a rebound command shows its new chord");
  assert.match(resolveKey("app.quickOpen", { "app.quickOpen": null }).text, /^“.+”$/, "an unbound command falls back to its palette name");
  assert.deepEqual(resolveKey("Tab", {}), { chord: "Tab" });
  assert.equal(resolveKey("app.noSuchCommand", {}), null);
  assert.equal(keyText("Press {key:app.quickOpen} or {key:Tab}", { "app.quickOpen": "Mod-Shift-o" }), "Press Ctrl+Shift+O or Tab");
});

// An offer says what just happened and why the tour is worth it; both lines
// are T()-marked, so the catalog has them.
test("offer copy is catalogued", () => {
  const zh = JSON.parse(readFileSync(new URL("../src/shared/i18n/locales/zh.json", import.meta.url), "utf8"));
  for (const tour of Object.values(TOURS).filter((t) => t.offer)) {
    assert.ok(tour.trigger && !tour.hint, `${tour.id}: only a triggered tour has an offer`);
    for (const key of ["title", "line"]) {
      assert.ok(tour.offer[key] && tour.offer[key] in zh, `${tour.id}: offer.${key} must be a T()-marked string`);
    }
  }
});

test("anyOf: at least one of the trigger's fact sets must hold", () => {
  const tour = { id: "t", version: 1, trigger: { requires: { view: "home" }, anyOf: [{ a: true }, { b: true }] }, steps: [{ id: "s" }] };
  assert.equal(canOffer(tour, { facts: { view: "home", a: true }, progress: null }), true);
  assert.equal(canOffer(tour, { facts: { view: "home", b: true }, progress: null }), true);
  assert.equal(canOffer(tour, { facts: { view: "home" }, progress: null }), false);
  assert.equal(canOffer(tour, { facts: { view: "page", a: true }, progress: null }), false, "requires still holds");
});

test("a welcome tour opens on an uncounted intro card, one per situation", () => {
  const firstRun = TOURS["first-run"];
  assert.equal(firstRun.welcome, true);
  const intros = firstRun.steps.filter((s) => s.intro);
  assert.deepEqual(intros.map((s) => s.id), ["welcome", "welcome-demo"]);
  for (const s of intros) {
    assert.equal(s.anchor, null, "the welcome card is centred");
    assert.equal(s.outline.length, 3);
    assert.ok(s.next && s.later && s.footnote);
  }
  assert.ok(firstRun.steps.slice(2).every((s) => !s.intro), "intro steps come first");
  // Five counted steps. The add demo cannot be skipped; each highlight step
  // shows its gesture, then the user's own highlight of that kind ticks it.
  const steps = firstRun.steps.filter((s) => !s.intro);
  assert.deepEqual(steps.map((s) => s.id), ["add-demo", "highlight", "area", "note-label", "home"]);
  assert.equal(steps[0].skippable, false);
  assert.ok(steps.every((s) => !s.required), "nothing in it waits: the add demo opens the paper by itself");
  for (const [id, preview, kind] of [["highlight", "previewHighlight", "text"], ["area", "previewArea", "area"]]) {
    const s = steps.find((x) => x.id === id);
    assert.equal(s.do[0][preview], true, `${id}: the demo runs first`);
    assert.deepEqual(s.advanceOn, { event: "highlight.created", match: { kind } }, `${id}: then the user's own ticks it`);
  }
});

// A finish card lists what the run made, read from what happened during it.
test("the finish card lists only what the run made", () => {
  const finish = TOURS["first-run"].finishCard;
  const log = createRunLog();
  assert.deepEqual(madeItems(finish, log), [], "nothing happened, nothing listed");
  recordEvent(log, "page.opened", { id: "p1", title: "Attention" });
  recordEvent(log, "page.opened", { id: "p2", title: "Elsewhere" });
  recordEvent(log, "highlight.created", { kind: "text" });
  recordEvent(log, "highlight.created", { kind: "area" });
  assert.deepEqual(madeItems(finish, log).map((m) => m.icon), ["page", "highlight"], "the skipped note-and-label demo made neither");
  log.completed.add("note-label");
  const made = madeItems(finish, log);
  assert.deepEqual(made.map((m) => m.icon), ["page", "highlight", "note", "label"]);
  assert.equal(made[0].args.title, "Attention", "the paper the tour opened first");
  assert.equal(made[1].args.n, 2);
  assert.equal(made[3].args.label, "llm");
  for (const item of finish.made) assert.ok(!item.event || EVENTS.includes(item.event), `finish event ${item.event}`);
  for (const item of finish.made) assert.ok(!item.step || TOURS["first-run"].steps.some((s) => s.id === item.step), `finish step ${item.step}`);
});

// A repeated anchor: the newest one ("last"), or the one inside the element
// the app marks data-guide-recent ("recent") — the table the user just made,
// not the page's first — else the first.
test("a repeated anchor picks the newest, the marked, or the first element", () => {
  for (const id of ["notes.table", "notes.tableAdd", "notes.tableCorner"]) assert.equal(ANCHORS[id].pick, "recent", id);
  const el = (marked = false) => ({ closest: (sel) => (marked && sel === "[data-guide-recent]" ? {} : null) });
  const saved = globalThis.document;
  const dom = (...els) => { globalThis.document = { querySelectorAll: () => els }; };
  try {
    const old = el(), made = el(true), other = el();
    dom(old, made);
    assert.equal(anchorElement("notes.tableAdd"), made, "the table the user just made");
    dom(old, other);
    assert.equal(anchorElement("notes.tableAdd"), old, "nothing marked: the first");
    dom(old, other);
    assert.equal(anchorElement("chat.citation"), other, "the latest reply's citation");
    assert.equal(anchorElement("header.home"), old);
    dom();
    assert.equal(anchorElement("notes.table"), null);
  } finally { globalThis.document = saved; }
});

// A card keeps clear of its step's `avoid` box too: the table above its add
// strip. Near the bottom of the window it goes above the table when there
// is room there, else beside it — never flipped onto the table.
test("a card that must keep clear of a box goes above it, else beside it", () => {
  const cardH = 120, vh = 800;
  const table = { top: 100, bottom: 780, left: 400, right: 900 };
  const strip = { top: 780, bottom: 796, left: 400, right: 900, width: 500, height: 16 };
  const covers = (p, box) => p.left < box.right && p.left + CARD_W > box.left && p.top < box.bottom && p.top + cardH > box.top;
  const plain = placeCard(strip, cardH, 1400, vh, "bottom");
  assert.equal(plain.side, "top");
  assert.ok(covers(plain, table), "without the box the flipped card lands on the table");
  const right = placeCard(strip, cardH, 1400, vh, "bottom", table);
  assert.equal(right.side, "right");
  assert.ok(!covers(right, table));
  assert.ok(right.beak, "the beak still points at the strip");
  const left = placeCard(strip, cardH, 1100, vh, "bottom", table);
  assert.equal(left.side, "left", "no room on the right");
  assert.ok(!covers(left, table));
  const short = { ...table, top: 400 };
  const above = placeCard(strip, cardH, 1400, vh, "bottom", short);
  assert.equal(above.side, "top");
  assert.ok(!covers(above, short), "above the whole table, not just above the strip");
  const roomy = placeCard({ ...strip, top: 380, bottom: 396 }, cardH, 1400, vh, "bottom", { ...table, bottom: 380 });
  assert.equal(roomy.side, "bottom", "the preferred side when it fits");
});

// A popover's anchors name the popover as their surface: the spotlight
// leaves the whole popover undimmed and the card keeps clear of it. The
// surface comes up by the same clicks as the anchor inside it.
test("a surface is a registered anchor revealed by the same path as the anchors in it", () => {
  const surfaces = Object.entries(ANCHORS).filter(([, a]) => a.surface);
  assert.ok(surfaces.length > 0);
  for (const [id, a] of surfaces) {
    const surface = ANCHORS[a.surface];
    assert.ok(surface, `${id}: unregistered surface ${a.surface}`);
    assert.ok(!surface.surface, `${id}: a surface sits in no other surface`);
    assert.deepEqual(surface.open || [], a.open || [], `${id}: its surface ${a.surface} opens the same way`);
  }
});
