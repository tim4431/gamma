// The translated view's state per document (src/pdf/translateState.js) in
// Node: the fixture paper handed over as pdf.js hands a document over, a
// fake server, and no viewer at all.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { markShape } from "../src/pdf/pageShape.js";
import { segmentPage } from "../src/pdf/pdfTranslate.js";
import { BREAK } from "../src/pdf/translatePlan.js";
import * as translateState from "../src/pdf/translateState.js";
import { TRANSLATE_PARALLEL_MAX } from "../src/app/prefDefs.js";
import { attachmentFile, pageAttachment } from "../src/library/libraryUtils.js";
import { docIdOf } from "../src/pdf/pdfSource.js";

const PAPER = JSON.parse(fs.readFileSync(new URL("./fixtures/translatePaper.json", import.meta.url)));
const last = (xs) => xs[xs.length - 1];
// A page of references, one block each.
const LIST_RUNS = Array.from({ length: 8 }, (_, k) => ({
  str: `[${k + 1}] A. Smith, B. Jones, and C. Doe, Phys. Rev. Lett. ${96 + k}, 01040${k} (200${k}).`,
  x: 54, y: 60 + 30 * k, w: 300, h: 9, font: "f", face: "CMR9", asc: 0.69, desc: -0.19,
}));

// The fixture's runs as a pdf.js document: text items in a scale-1
// viewport, their fonts' metrics, and the fonts' names in `commonObjs` —
// there from the start, or (`loaded: false`) only once the page's operator
// list is built, which `drawn(pn)` gives. The pages that let their decoded
// images go are noted in `cleaned`, the pages asked for in `read`.
function pdfDoc(pages = PAPER.pages, { loaded = true, drawn = () => undefined } = {}) {
  const runsOf = new Map(pages.map((p) => [p.pn, p.runs]));
  const cleaned = [];
  const read = [];
  return {
    numPages: pages.length,
    cleaned,
    read,
    async getPage(pn) {
      read.push(pn);
      const runs = runsOf.get(pn) || [];
      const names = new Map(runs.map((r) => [r.font, r.face]));
      let built = loaded;
      return {
        getViewport: () => ({ transform: [1, 0, 0, 1, 0, 0], width: 612, height: 792 }),
        getTextContent: async () => ({
          items: runs.map((r) => ({ str: r.str, transform: [r.h, 0, 0, r.h, r.x, r.y], width: r.w, fontName: r.font })),
          styles: Object.fromEntries(runs.map((r) => [r.font, { ascent: r.asc, descent: r.desc }])),
        }),
        commonObjs: {
          has: (id) => built && names.has(id),
          get: (id, then) => { const face = { name: names.get(id) }; then?.(face); return face; },
        },
        getOperatorList: async () => { built = true; return drawn(pn); },
        cleanup: () => { cleaned.push(pn); },
      };
    },
  };
}

// A text "translated": every part marked, its markers and tokens kept.
const zh = (src) => src.split(BREAK).map((p) => `译${p.trim()}`).join(` ${BREAK} `);

// The server: `answer(texts, call)` makes each request's final reply;
// `asked` keeps every request's texts.
function server(answer = (texts) => ({ translations: texts.map(zh), failed: [] })) {
  const asked = [];
  const translate = async (texts) => {
    asked.push(texts);
    return answer(texts, asked.length);
  };
  return { asked, translate, texts: () => asked.flat() };
}

// A server that holds its requests until `release()` answers them, or
// `free()` answers them and every later one at once; a halt aborts what it
// holds, as it aborts a fetch.
function heldServer() {
  const asked = [];
  let held = [];
  let open = false;
  const translate = (texts, signal) => new Promise((resolve, reject) => {
    asked.push(texts);
    const answer = () => resolve({ translations: texts.map(zh), failed: [] });
    if (open) return answer();
    held.push(answer);
    signal.addEventListener("abort", () => {
      held = held.filter((a) => a !== answer);
      reject(new Error("aborted"));
    });
  });
  const release = () => { const h = held; held = []; for (const answer of h) answer(); };
  return { asked, translate, release, free: () => { open = true; release(); }, held: () => held.length };
}

async function until(ok, what) {
  for (let i = 0; i < 400 && !ok(); i++) await new Promise((r) => setTimeout(r, 5));
  assert.ok(ok(), `timed out waiting for ${what}`);
}

const job = (url, over = {}) => translateState.startJob(url, {
  doc: pdfDoc(), pages: [1, 2], label: "document", key: "zh-CN|model", parallel: 2,
  scope: { url, pageId: "page-1", stored: true }, ...over,
});

function settled(entries) {
  for (const e of entries.values()) {
    assert.equal(e.queued, false);
    assert.ok(!e.busy?.size && !Object.keys(e.partial || {}).length);
  }
}

test("a document's pages are segmented from its pdf.js document as the viewer segments them", async () => {
  const url = "/uploads/segmented.pdf";
  await job(url, { translate: server().translate });
  const { entries } = translateState.snapshot(url);
  for (const { pn, runs } of PAPER.pages) {
    const blocks = segmentPage(runs);
    markShape(blocks);
    assert.deepEqual(entries.get(pn).paras, blocks, `page ${pn}`);
  }
  // A formula token's span is the block's.
  const [pn, blocks] = [...entries].map(([p, e]) => [p, e.paras]).find(([, ps]) => ps.some((b) => b.spans?.length));
  const i = blocks.findIndex((b) => b.spans?.length);
  assert.equal(translateState.span(url, pn, i, 1), blocks[i].spans[0]);
  assert.equal(translateState.span(url, pn, i, blocks[i].spans.length + 1), null);
  assert.equal(translateState.span("/uploads/other.pdf", pn, i, 1), null);
});

test("a job's entries, show/hide and status are its document's, each snapshot the same object until it changes", async () => {
  const url = "/uploads/snapshot.pdf";
  const before = translateState.snapshot(url);
  assert.equal(translateState.snapshot(url), before);
  assert.equal(before.entries.size, 0);
  assert.equal(before.shown, true);
  assert.equal(before.status.running, false);

  let heard = 0;
  const unsubscribe = translateState.subscribe(() => { heard += 1; });
  await job(url, { translate: server().translate });
  unsubscribe();
  assert.ok(heard > 2, "every change is heard");

  const after = translateState.snapshot(url);
  assert.equal(translateState.snapshot(url), after);
  assert.equal(translateState.entries(url), after.entries);
  assert.ok([...after.entries.values()].every((e) => e.done && e.key === "zh-CN|model"));
  settled(after.entries);
  const { medianMs, ...status } = after.status;
  assert.deepEqual(status, { running: false, progress: 1, label: "document", focus: null, reading: false, error: null, refused: 0, untranslated: 0, unanswered: 0, unchanged: 0, unread: [], refsLeft: 0, refsOnly: false });
  assert.equal(typeof medianMs, "number");
  assert.equal(translateState.snapshot("/uploads/untouched.pdf").entries.size, 0, "other documents hold nothing");

  translateState.setShown(url, false);
  const hidden = translateState.snapshot(url);
  assert.notEqual(hidden, after, "a change is a new snapshot");
  assert.equal(hidden.shown, false);
  assert.equal(hidden.entries, after.entries);
  translateState.setShown(url, false);
  assert.equal(translateState.snapshot(url), hidden, "no change, the same snapshot");
  // A new job shows the translation again.
  await job(url, { pages: [1], translate: server().translate });
  assert.equal(translateState.snapshot(url).shown, true);
});

test("a job with no pages to ask for shows what the document holds, and leaves a running job alone", async () => {
  const url = "/uploads/all-done.pdf";
  await job(url, { translate: server().translate });
  translateState.setShown(url, false);
  assert.equal(job(url, { pages: [], translate: server().translate }), null, "no job starts");
  assert.equal(translateState.snapshot(url).shown, true);

  const s = heldServer();
  const running = job(url, { pages: [1], translate: s.translate, key: "de|model" });
  await until(() => translateState.snapshot(url).status.running, "the job");
  translateState.setShown(url, false);
  assert.equal(job(url, { pages: [], translate: server().translate }), null);
  assert.equal(translateState.snapshot(url).shown, false, "the running job's document is left as it is");
  s.free();
  await running;
  assert.equal(job("/uploads/never-seen.pdf", { pages: [], translate: server().translate }), null);
  assert.equal(translateState.statuses().some(([u]) => u === "/uploads/never-seen.pdf"), false, "and no state is made for it");
});

test("the button's state holds what the host reads and comes out equal while only streamed text changes", () => {
  const entry = (over) => ({ key: "zh", texts: [], done: false, queued: true, busy: new Set(), partial: {}, ...over });
  const view = (entries, over = {}) => ({
    entries: new Map(entries), shown: true,
    status: { running: true, progress: 0.25, label: "document", reading: false, medianMs: null }, ...over,
  });
  const busy = view([[1, entry({ texts: ["译"], done: true, queued: false })], [2, entry({ partial: { 0: "译" } })]]);
  assert.deepEqual(translateState.buttonState(busy, "zh", 1),
    { running: true, progress: 0.25, shown: true, pages: 1, current: true, translated: true, refsLeft: 0, docDone: false });
  assert.equal(translateState.buttonState(busy, "zh", 2).current, false, "a page still pending");
  assert.equal(translateState.buttonState(busy, "zh", 9).current, false, "a page with no entry");
  // Text under another key: translated, but no page to show and no toggle.
  assert.deepEqual(translateState.buttonState(busy, "de", 1),
    { running: true, progress: 0.25, shown: true, pages: 0, current: false, translated: true, refsLeft: 0, docDone: false });
  // The whole document is done once each of its pages is, under the key.
  assert.equal(translateState.buttonState(busy, "zh", 1, 2).docDone, false, "page 2 is pending");
  assert.equal(translateState.buttonState(busy, "zh", 1, 1).docDone, true, "a one-page document");
  assert.equal(translateState.buttonState(busy, "de", 1, 1).docDone, false, "another key's pages are not done");
  assert.equal(translateState.buttonState(busy, "zh", 1, 3).docDone, false, "a page never segmented");
  // A streamed partial makes a new view and the same state.
  const streamed = view([[1, entry({ texts: ["译"], done: true, queued: false })], [2, entry({ partial: { 0: "译文" } })]]);
  assert.deepEqual(translateState.buttonState(streamed, "zh", 1), translateState.buttonState(busy, "zh", 1));
  assert.deepEqual(translateState.buttonState(translateState.snapshot("/uploads/none.pdf"), "zh", 1),
    { running: false, progress: 0, shown: true, pages: 0, current: false, translated: false, refsLeft: 0, docDone: false });
});

test("a page of references left original under a language still translates at a click while nothing shows anywhere", () => {
  const entry = (over) => ({ key: "zh", texts: [null, null], done: true, queued: false, busy: new Set(), partial: {}, left: new Set([0, 1]), ...over });
  const status = { running: false, progress: 1, label: "p.9" };
  const refs = { entries: new Map([[9, entry()]]), shown: true, status };
  const alone = translateState.buttonState(refs, "zh", 9);
  assert.deepEqual([alone.pages, alone.current, alone.refsLeft], [0, false, 2],
    "nothing to show or hide: a click runs the job, which says what happened");
  // A translated page elsewhere: the button lights and toggles from here too.
  const mixed = { ...refs, entries: new Map([...refs.entries, [1, entry({ texts: ["译"], left: new Set() })]]) };
  const shown = translateState.buttonState(mixed, "zh", 9);
  assert.deepEqual([shown.pages, shown.current, shown.refsLeft], [1, true, 2]);
  assert.equal(translateState.buttonState(mixed, "de", 9).refsLeft, 0, "what another key left is not this key's");
});

test("a job with `references` asks for the blocks a job left, on those pages, and the row goes", async () => {
  const pages = [{ pn: 1, runs: PAPER.pages[0].runs }, { pn: 2, runs: LIST_RUNS }];
  const url = "/uploads/references.pdf";
  const first = server();
  await job(url, { doc: pdfDoc(pages), key: "zh", translate: first.translate });
  const paras = translateState.entries(url).get(2).paras;
  assert.equal(translateState.entries(url).get(2).left.size, paras.length);
  assert.deepEqual(translateState.pagesLeft(url, "zh"), [2]);
  assert.deepEqual(translateState.pagesLeft(url, "de"), []);
  assert.ok(!first.texts().some((t) => t.includes("Phys. Rev. Lett.")));

  const again = server();
  await job(url, { doc: pdfDoc(pages), key: "zh", pages: translateState.pagesLeft(url, "zh"), references: true, translate: again.translate });
  assert.ok(again.texts().some((t) => t.includes("Phys. Rev. Lett.")));
  assert.deepEqual(translateState.pagesLeft(url, "zh"), []);
  assert.ok(translateState.entries(url).get(2).done);
  assert.equal(translateState.entries(url).get(2).texts.filter(Boolean).length, paras.length);
});

test("references whose request failed are told apart, so that asking again asks for them", async () => {
  const pages = [{ pn: 1, runs: LIST_RUNS }];
  const url = "/uploads/references-failed.pdf";
  await job(url, { doc: pdfDoc(pages), pages: [1], key: "zh", translate: server().translate });
  assert.equal(translateState.referencesFailed(url, "zh"), false, "left, and not asked for");
  const failing = server(() => { throw new Error("provider down"); });
  await job(url, { doc: pdfDoc(pages), pages: [1], key: "zh", references: true, translate: failing.translate });
  assert.ok(failing.asked.length > 0);
  assert.deepEqual(translateState.pagesLeft(url, "zh"), [1], "they stay left");
  assert.equal(translateState.referencesFailed(url, "zh"), true);
  assert.equal(translateState.referencesFailed(url, "de"), false);
});

test("the peek ends when its key is released, whatever is on screen by then; only a shown translation takes the key", () => {
  let shown = true, peek = false, stopped = 0;
  const { down, up } = translateState.peekKeys("Alt", () => shown, (on) => { peek = on; });
  const key = (name) => ({ key: name, preventDefault: () => { stopped += 1; } });
  down(key("Control"));
  assert.deepEqual([peek, stopped], [false, 0], "another key");
  down(key("Alt"));
  assert.deepEqual([peek, stopped], [true, 1]);
  // The last overlay goes while the key is held.
  shown = false;
  up(key("Alt"));
  assert.deepEqual([peek, stopped], [false, 1], "released, the peek ends; nothing shown, the key keeps its default");
  down(key("Alt"));
  assert.deepEqual([peek, stopped], [false, 1], "nothing to peek past");
  shown = true;
  down(key("Alt"));
  up(key("Alt"));
  assert.deepEqual([peek, stopped], [false, 3]);
  up(key("Shift"));
  assert.equal(stopped, 3, "another key's release");
});

test("a job runs on with no viewer, holds its document meanwhile, and stops by its document's URL", async () => {
  const url = "/uploads/held.pdf";
  const pins = [];
  const s = heldServer();
  const done = job(url, {
    parallel: 1, translate: s.translate,
    released: () => pins.push("let go"),
  });
  await until(() => s.asked.length > 0, "the first request");
  assert.equal(translateState.pinned(url), true);
  assert.deepEqual(pins, []);
  assert.equal(translateState.snapshot(url).status.running, true);
  assert.deepEqual(translateState.statuses().find(([u]) => u === url)[1], translateState.snapshot(url).status);
  assert.equal(job(url, { translate: server().translate }), null, "one job at a time per document");

  // The first chunk lands; the halt aborts the second.
  s.release();
  await until(() => s.asked.length > 1, "the second request");
  translateState.halt(url);
  await done;
  assert.deepEqual(pins, ["let go"]);
  assert.equal(translateState.pinned(url), false);
  const { status, entries } = translateState.snapshot(url);
  assert.equal(status.running, false);
  assert.equal(status.error, null, "a halt is no failure");
  assert.ok(status.progress > 0 && status.progress < 1);
  assert.ok([...entries.values()].some((e) => e.texts.some((t) => t?.startsWith("译"))), "the first chunk's text stays");
  settled(entries);
});

test("a re-run asks only for what the document's earlier jobs left", async () => {
  const everything = server();
  await job("/uploads/everything.pdf", { translate: everything.translate });

  const url = "/uploads/rerun.pdf";
  const first = server((texts, call) => {
    if (call === 1) return { translations: texts.map(zh), failed: [] };
    translateState.halt(url);
    throw new Error("aborted");
  });
  await job(url, { parallel: 1, translate: first.translate });
  const landed = first.asked[0];

  const rest = server();
  await job(url, { translate: rest.translate });
  assert.ok(landed.every((t) => !rest.texts().includes(t)), "finished texts are not asked again");
  assert.deepEqual([...landed, ...rest.texts()].sort(), everything.texts().sort());
  assert.ok([...translateState.entries(url).values()].every((e) => e.done));
});

test("a page segmented ahead of the view lets its decoded images go unless it is near the page being read", async () => {
  const far = pdfDoc(PAPER.pages, { loaded: false });
  await job("/uploads/far.pdf", { doc: far, pages: [2], near: () => false, translate: server().translate });
  assert.deepEqual(far.cleaned, [2]);
  const near = pdfDoc(PAPER.pages, { loaded: false });
  await job("/uploads/near.pdf", { doc: near, pages: [2], near: (pn) => pn === 2, translate: server().translate });
  assert.deepEqual(near.cleaned, []);
  // The figures build the list on a page whose fonts were there already too.
  const drawn = pdfDoc(PAPER.pages);
  await job("/uploads/drawn.pdf", { doc: drawn, pages: [2], translate: server().translate });
  assert.deepEqual(drawn.cleaned, [2]);
});

test("a page's figures are marked before its blocks are kept: their text goes to no model and the page is done", async () => {
  const { OPS } = await import("pdfjs-dist/legacy/build/pdf.mjs");
  // A paragraph, and a label in a frame with its ticks.
  const run = (str, x, y, w) => ({ str, x, y, w, h: 9, font: "f", face: "CMR9", asc: 0.69, desc: -0.19 });
  const lines = ["The ensemble of atoms shares one excitation and loses its coherence", "when the blockade is broken by the probe beam, so the signal falls",
    "with the detuning of the probe and the time spent in the excited state."];
  const pages = [{ pn: 1, width: 612, height: 792, runs: [...lines.map((s, k) => run(s, 54, 60 + 11 * k, 300)), run("Time in ms", 200, 390, 60)] }];
  const fnArray = [], argsArray = [];
  const rect = (x, y, w, h) => {
    fnArray.push(OPS.constructPath, OPS.stroke);
    argsArray.push([[OPS.rectangle], [x, y, w, h], null], null);
  };
  rect(100, 200, 300, 200);
  for (let k = 1; k <= 12; k++) rect(100 + 22 * k, 395, 1, 5);
  const url = "/uploads/figure.pdf";
  const s = server();
  await job(url, { doc: pdfDoc(pages, { drawn: () => ({ fnArray, argsArray }) }), pages: [1], translate: s.translate });
  const entry = translateState.entries(url).get(1);
  const label = entry.paras.find((p) => p.text.includes("Time in ms"));
  assert.ok(label.figure && !label.translate);
  assert.ok(s.texts().every((t) => !t.includes("Time in ms")));
  assert.equal(s.texts().length, 1);
  assert.ok(entry.done);
  // A page whose operator list fails plans as if it had no drawing.
  const none = server();
  await job("/uploads/nofigure.pdf", { doc: pdfDoc(pages, { drawn: () => { throw new Error("gone"); } }), pages: [1], translate: none.translate });
  assert.ok(none.texts().some((t) => t.includes("Time in ms")));
});

test(`at most ${translateState.MAX_DOCS} documents are kept, the least recently used going first, never one whose job runs`, async () => {
  const kept = () => translateState.statuses().map(([url]) => url);
  // A document without text: its job ends at once.
  const quick = (url) => job(url, { doc: pdfDoc([]), pages: [1], translate: server().translate });
  for (let n = 0; n < translateState.MAX_DOCS; n++) await quick(`/uploads/w${n}.pdf`);
  assert.deepEqual(kept(), Array.from({ length: translateState.MAX_DOCS }, (_, n) => `/uploads/w${n}.pdf`));

  const s = heldServer();
  const running = job("/uploads/running.pdf", { pages: [1], parallel: 1, translate: s.translate });
  translateState.touch("/uploads/w1.pdf");
  for (let n = 0; n < translateState.MAX_DOCS - 2; n++) await quick(`/uploads/d${n}.pdf`);
  assert.ok(kept().includes("/uploads/w1.pdf"), "a document used again stays");
  assert.ok(!kept().includes("/uploads/w2.pdf"));
  for (let n = translateState.MAX_DOCS - 2; n < translateState.MAX_DOCS; n++) await quick(`/uploads/d${n}.pdf`);
  assert.equal(kept().length, translateState.MAX_DOCS);
  assert.equal(kept()[0], "/uploads/running.pdf", "the oldest, kept while its job runs");
  assert.ok(!kept().includes("/uploads/w1.pdf"));

  await until(() => s.asked.length > 0, "the running job's request");
  s.free();
  await running;
  assert.ok(translateState.snapshot("/uploads/running.pdf").entries.size > 0);
  await quick("/uploads/next.pdf");
  assert.ok(!kept().includes("/uploads/running.pdf"), "once its job ended, it goes first");
  assert.equal(translateState.snapshot("/uploads/running.pdf").entries.size, 0);
  assert.equal(last(kept()), "/uploads/next.pdf");
});

test("the page names the paper, and only the brief's request reads a PDF the server has not stored", async () => {
  // A job's requests: each chunk's context, and the brief's.
  const run = async (url, scope) => {
    const asked = [];
    const briefs = [];
    const doc = pdfDoc();
    await job(url, {
      doc, scope,
      translate: async (texts, signal, onPartial, context) => {
        asked.push(context);
        return { translations: texts.map(zh), failed: [] };
      },
      brief: async (paper) => {
        briefs.push(paper);
        return { ready: true };
      },
    });
    assert.ok(asked.length > 1);
    assert.deepEqual(doc.read.toSorted(), [...new Set(doc.read)].sort(), "each page is segmented once");
    return { asked, briefs };
  };
  const stored = await run("/uploads/named.pdf", { url: "/uploads/named.pdf", pageId: "page-1", stored: true });
  assert.deepEqual(stored.briefs, [{ pageId: "page-1", head: "" }], "the server reads its own copy");
  assert.ok(stored.asked.every((c) => c.pageId === "page-1" && !c.head));

  const proxied = await run("/proxy/named.pdf", { url: "/proxy/named.pdf", pageId: "page-2", stored: false });
  assert.equal(proxied.briefs.length, 1);
  const head = proxied.briefs[0].head;
  assert.equal(proxied.briefs[0].pageId, "page-2");
  assert.ok(head.length > 200, "the brief's request carries the opening text");
  assert.ok(head.includes(translateState.entries("/proxy/named.pdf").get(1).paras.find((p) => p.translate).text));
  assert.ok(proxied.asked.every((c) => c.pageId === "page-2" && !c.head), "the chunks name the page alone");

  const pageless = await run("/proxy/pageless.pdf", { url: "/proxy/pageless.pdf", pageId: "", stored: false });
  assert.deepEqual(pageless.briefs, [{ pageId: "", head }]);
  assert.ok(pageless.asked.every((c) => c.head === head && !c.pageId), "without a page, every request carries it");

  const nameless = await run("/uploads/nameless.pdf", { url: "/uploads/nameless.pdf", pageId: "", stored: true });
  assert.deepEqual(nameless.briefs, [], "nothing names the paper: no brief is asked for");

  // The host has moved on to the next page while the viewer still shows the
  // last document: that page names no request of this one, which is read
  // from its opening text like a PDF without a page.
  const moved = await run("/uploads/shown.pdf", { url: "/uploads/next.pdf", pageId: "page-next", stored: true });
  assert.equal(moved.briefs.length, 1);
  assert.equal(moved.briefs[0].pageId, "");
  assert.ok(moved.briefs[0].head.length > 200);
  assert.ok(moved.asked.every((c) => !c.pageId && c.head === moved.briefs[0].head), "no request names the next page");
  const next = { url: "/uploads/next.pdf", pageId: "page-next", stored: true, file: "next" };
  assert.deepEqual(translateState.scopeOf("/uploads/shown.pdf", next), { pageId: "", stored: false, file: "" },
    "nor does the selection translator's, nor Forget this paper's translations");
  assert.deepEqual(translateState.scopeOf("/uploads/next.pdf", next), { pageId: "page-next", stored: true, file: "next" });
});

test("a PDF opened from its link names the file the server keeps its translations under, which Forget this paper's translations forgets", () => {
  // The page App builds the scope from: its attachment id is the link's
  // hash, under which the proxy saves its copy and the server keeps the
  // paper's translations. The viewer reads it through the proxy, whose URL
  // names no upload.
  const link = "https://arxiv.org/pdf/2401.00001";
  const block = { properties: { doc_id: "0123456789abcdef01234567", source_url: link } };
  const url = `/api/pdf?source_url=${encodeURIComponent(link)}&save=1`;
  assert.equal(docIdOf(url), null, "the viewer's URL alone names no file");
  const scope = { url, pageId: "page-arxiv", stored: !!docIdOf(url), file: attachmentFile(pageAttachment(block)) };
  assert.deepEqual(translateState.scopeOf(url, scope), { pageId: "page-arxiv", stored: false, file: "0123456789abcdef01234567" });
  // A duplicated page carries only its upload's URL.
  const copy = { properties: { source_url: "/api/uploads/89abcdef0123456789abcdef.pdf" } };
  assert.equal(attachmentFile(pageAttachment(copy)), "89abcdef0123456789abcdef");
});

test("jobs on several documents together send no more chunk requests at once than one job may", async () => {
  const servers = [heldServer(), heldServer(), heldServer()];
  const running = servers.map((s, n) => job(`/uploads/side-by-side-${n}.pdf`, {
    pages: [1, 2], parallel: TRANSLATE_PARALLEL_MAX, translate: s.translate,
  }));
  const inFlight = () => servers.reduce((n, s) => n + s.held(), 0);
  await until(() => inFlight() === TRANSLATE_PARALLEL_MAX, "the requests in flight");
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(inFlight(), TRANSLATE_PARALLEL_MAX, "the others wait for a place");
  // Answering one lets the next in, whichever document it is for.
  let most = 0;
  while (servers.some((s) => s.held())) {
    servers.find((s) => s.held()).release();
    await new Promise((r) => setTimeout(r, 5));
    most = Math.max(most, inFlight());
  }
  for (const s of servers) s.free();
  await Promise.all(running);
  assert.ok(most <= TRANSLATE_PARALLEL_MAX, `at most ${TRANSLATE_PARALLEL_MAX} at once, saw ${most}`);
  for (let n = 0; n < servers.length; n++) {
    assert.ok([...translateState.entries(`/uploads/side-by-side-${n}.pdf`).values()].every((e) => e.done), `document ${n} translated`);
  }
});

test("forgetting a document's translation drops its entries and status but keeps its blocks, and waits for its job", async () => {
  const url = "/uploads/forgotten.pdf";
  const first = server();
  await job(url, { translate: first.translate });
  const blocks = translateState.entries(url).get(1).paras;
  translateState.forget(url);
  const { entries, status } = translateState.snapshot(url);
  assert.equal(entries.size, 0);
  assert.equal(status.running, false);
  assert.equal(status.progress, 0);
  const again = server();
  await job(url, { translate: again.translate });
  assert.deepEqual(again.texts().sort(), first.texts().sort(), "the next job asks for everything again");
  assert.equal(translateState.entries(url).get(1).paras, blocks, "from the blocks segmented before");

  const busy = "/uploads/forgotten-busy.pdf";
  const s = heldServer();
  const running = job(busy, { parallel: 1, translate: s.translate });
  await until(() => s.asked.length > 0, "the running job's request");
  const before = translateState.snapshot(busy);
  translateState.forget(busy);
  assert.equal(translateState.snapshot(busy), before, "nothing changes while the job runs");
  s.free();
  await running;
  assert.ok(translateState.entries(busy).size > 0);
});

test("a paragraph is shown in the original as a whole, under its key, and asked for again alone, afresh", async () => {
  const url = "/uploads/paragraph.pdf";
  await job(url, { translate: server().translate });
  const paras = translateState.entries(url).get(1).paras;
  const a = paras.findIndex((b) => b.text.includes("In the rotating frame the"));
  const b = paras.findIndex((x) => x.text.includes("Hamiltonian reads"));
  // Either part is the whole paragraph, the column break that cut it aside.
  for (const i of [a, b]) {
    assert.deepEqual(translateState.unitOf(url, 1, i, "zh-CN|model").map((t) => t.i).sort(), [a, b].sort());
  }
  assert.deepEqual(translateState.unitOf("/uploads/never.pdf", 1, a, "zh-CN|model"), [{ pn: 1, i: a }]);
  const unit = translateState.unitOf(url, 1, a, "zh-CN|model");
  translateState.setOriginal(url, "de|model", unit, true);
  assert.equal(translateState.entries(url).get(1).original, undefined, "another key's entries are not this one's");
  translateState.setOriginal(url, "zh-CN|model", unit, true);
  assert.deepEqual([...translateState.entries(url).get(1).original].sort(), [a, b].sort());
  const before = translateState.snapshot(url);
  translateState.setOriginal(url, "zh-CN|model", unit, true);
  assert.equal(translateState.snapshot(url), before, "no change, the same snapshot");

  // Asked again, alone and afresh: its blocks show their new text.
  const asked = [];
  await job(url, { pages: [1], label: "p.1", focus: { pn: 1, i: b }, fresh: true,
    translate: async (texts, signal, onPartial, context) => {
      asked.push({ texts, context });
      return { translations: texts.map((t) => zh(t).replaceAll("译", "再译")), failed: [] };
    } });
  assert.equal(asked.length, 1);
  assert.equal(asked[0].context.fresh, true);
  assert.ok(asked[0].texts[0].includes(BREAK));
  const e = translateState.entries(url).get(1);
  assert.equal(e.original, undefined, "a block asked for again shows its translation");
  assert.ok(e.texts[a].startsWith("再译") && e.texts[b].startsWith("再译"));
  assert.equal(e.done, true);
  translateState.setOriginal(url, "zh-CN|model", [{ pn: 1, i: a }], true);
  translateState.setOriginal(url, "zh-CN|model", [{ pn: 1, i: a }], false);
  assert.equal(translateState.entries(url).get(1).original, undefined, "turned off again, the set goes");
});

test("a paragraph's unit runs into the next page unless that page holds another key's translation", async () => {
  const url = "/uploads/paragraph-pages.pdf";
  await job(url, { translate: server().translate });
  const across = translateState.unitOf(url, 1, 27, "zh-CN|model");
  assert.deepEqual(across, [{ pn: 1, i: 27 }, { pn: 2, i: 1 }]);
  assert.deepEqual(translateState.unitOf(url, 1, 27, "ja|model"), [{ pn: 1, i: 27 }], "page 2 is another key's");
});

test("clearing the workspace's kept translations drops every document's entries but a running job's", async () => {
  const urls = ["/uploads/cleared-1.pdf", "/uploads/cleared-2.pdf"];
  for (const url of urls) await job(url, { translate: server().translate });
  const busy = "/uploads/cleared-busy.pdf";
  const s = heldServer();
  const running = job(busy, { parallel: 1, translate: s.translate });
  await until(() => s.asked.length > 0, "the running job's request");
  translateState.forgetAll();
  for (const url of urls) assert.equal(translateState.entries(url).size, 0, url);
  assert.equal(translateState.snapshot(busy).status.running, true, "the running job goes on");
  s.free();
  await running;
  assert.ok(translateState.entries(busy).size > 0);
  const again = server();
  await job(urls[0], { translate: again.translate });
  assert.ok(again.asked.length > 0, "the next job asks for everything again");
});

// It drops every document the tests above kept, so they come first.
test("the session's end halts every job and drops every document's state", async () => {
  const kept = "/uploads/before-logout.pdf";
  await job(kept, { translate: server().translate });
  const url = "/uploads/at-logout.pdf";
  const pins = [];
  const s = heldServer();
  const running = job(url, {
    parallel: 1, translate: s.translate,
    released: () => pins.push("let go"),
  });
  await until(() => s.asked.length > 0, "the first request");
  let heard = 0;
  const unsubscribe = translateState.subscribe(() => { heard += 1; });
  translateState.reset();
  assert.equal(heard, 1, "those following hear it");
  assert.deepEqual(translateState.statuses(), []);
  assert.equal(translateState.entries(kept).size, 0, "the next account sees no translation");
  assert.equal(translateState.snapshot(url).status.running, false);
  await running;
  unsubscribe();
  assert.equal(s.asked.length, 1, "the request in flight was aborted and no other went");
  assert.deepEqual(pins, ["let go"], "the halted job lets its document go");
  assert.equal(heard, 1, "the halted job's end lands in no kept state");
  assert.deepEqual(translateState.statuses(), []);
  // The document starts afresh.
  const again = server();
  await job(url, { pages: [1], translate: again.translate });
  assert.ok(again.asked.length > 0);
  assert.equal(translateState.snapshot(url).status.running, false);
});

test("a job started on a document while a halted one still unwinds keeps it held when that one ends", async () => {
  const url = "/uploads/held-twice.pdf";
  const pins = [];
  const s = heldServer();
  const halted = job(url, { parallel: 1, translate: s.translate, released: () => pins.push("halted let go") });
  await until(() => s.asked.length > 0, "the first request");
  translateState.reset();
  assert.equal(translateState.pinned(url), true, "the halted job reads until it has unwound");
  // The next session translates the same document before then.
  const next = heldServer();
  const running = job(url, { pages: [1], parallel: 1, translate: next.translate, released: () => pins.push("next let go") });
  await halted;
  assert.deepEqual(pins, ["halted let go"]);
  assert.equal(translateState.pinned(url), true, "the running job still holds it");
  next.free();
  await running;
  assert.deepEqual(pins, ["halted let go", "next let go"]);
  assert.equal(translateState.pinned(url), false);
  translateState.reset();
});

// --- seeding from the kept translations -------------------------------------

// What the server keeps after `jobs` ran (each a job's options over a fresh
// document): every text asked, as the fake translated it.
async function keptAfter(...jobs) {
  const rows = new Map();
  for (const [k, over] of jobs.entries()) {
    const s = server();
    await job(`/uploads/kept-by-${k}-${Math.random()}.pdf`, { translate: s.translate, ...over });
    for (const t of s.texts()) rows.set(t, zh(t));
  }
  return rows;
}
// POST /api/translations/lookup over `rows`: a text or null each.
function lookupOf(rows) {
  const asked = [];
  const lookup = async (texts) => {
    asked.push(texts);
    return texts.map((t) => rows.get(t) ?? null);
  };
  return { asked, lookup, texts: () => asked.flat() };
}
const seed = (url, over) => translateState.seedPages(url, { doc: pdfDoc(), key: "zh-CN|model", pages: [1, 2], ...over });
// A page's entry, as the button and the overlay read it.
const shownOf = (url, pn) => {
  const e = translateState.entries(url).get(pn);
  return e && { key: e.key, done: e.done, texts: e.texts };
};

test("a lookup goes in requests within the caps a translation request has", () => {
  assert.deepEqual(translateState.lookupBatches(["a", "b", "c"], 2, 100), [["a", "b"], ["c"]]);
  assert.deepEqual(translateState.lookupBatches(["aaa", "bb", "c"], 10, 4), [["aaa"], ["bb", "c"]]);
  assert.deepEqual(translateState.lookupBatches(["far too long", "ok"], 10, 4), [["ok"]], "a text past the cap was never sent");
  assert.deepEqual(translateState.lookupBatches([]), []);
});

test("a paper's kept translation comes back with no model call, in one lookup, as the job left it", async () => {
  const rows = await keptAfter({ pages: [1, 2] });
  const url = "/uploads/seeded.pdf";
  const made = "/uploads/seeded-made.pdf";
  await job(made, { translate: server().translate });
  const k = lookupOf(rows);
  await seed(url, { lookup: k.lookup });
  assert.equal(k.asked.length, 1, "one lookup for both pages");
  assert.equal(new Set(k.texts()).size, k.texts().length, "each text once");
  for (const pn of [1, 2]) {
    assert.deepEqual(shownOf(url, pn), shownOf(made, pn), `page ${pn} as the job left it`);
    const e = translateState.entries(url).get(pn);
    assert.equal(e.queued, false);
    assert.ok(!e.busy?.size && !e.failed.size);
  }
  const view = translateState.snapshot(url);
  assert.equal(view.shown, true);
  assert.equal(view.status.running, false, "no job, no status");
  // Looked up once: the same pages are not asked again under this key.
  await seed(url, { lookup: k.lookup });
  assert.equal(k.asked.length, 1);
  // Nothing found: no entry.
  const none = lookupOf(new Map());
  await seed("/uploads/never-kept.pdf", { lookup: none.lookup });
  assert.equal(none.asked.length, 1);
  assert.equal(translateState.entries("/uploads/never-kept.pdf").size, 0);
});

test("a paragraph a page break cut is looked up joined and split, and either fills both pages", async () => {
  const url = "/uploads/forms.pdf";
  const paras = new Map(PAPER.pages.map(({ pn, runs }) => { const b = segmentPage(runs); markShape(b); return [pn, b]; }));
  const units = translateState.seedUnits((pn) => paras.get(pn) || null, [1, 2]);
  const alone = (pn, i) => units.find((u) => u.targets.length === 1 && u.targets[0].pn === pn && u.targets[0].i === i);
  const joined = units.find((u) => u.targets.some((t) => t.pn === 1) && u.targets.some((t) => t.pn === 2));
  assert.ok(joined?.src.includes(BREAK), "the joined form");
  assert.ok(alone(1, 27) && alone(2, 1), "each page's part alone");

  // Kept from jobs over one page at a time: the split forms.
  const split = await keptAfter({ pages: [1] }, { pages: [2] });
  assert.ok(!split.has(joined.src));
  const k = lookupOf(split);
  await seed(url, { lookup: k.lookup });
  assert.ok(k.texts().includes(joined.src), "the joined form is asked too");
  assert.ok(translateState.entries(url).get(1).done && translateState.entries(url).get(2).done, "both pages, from the split forms");
  assert.equal(translateState.entries(url).get(1).texts[27], split.get(alone(1, 27).src));

  // Kept both ways: the joined form comes first.
  const both = await keptAfter({ pages: [1, 2] });
  const rows = new Map([...split].map(([t, x]) => [t, `split:${x}`]));
  for (const [t, x] of both) if (!rows.has(t)) rows.set(t, x);
  const again = "/uploads/forms-joined.pdf";
  await seed(again, { lookup: lookupOf(rows).lookup });
  const e1 = translateState.entries(again).get(1), e2 = translateState.entries(again).get(2);
  assert.ok(!e1.texts[27].startsWith("split:") && !e2.texts[1].startsWith("split:"), "the joined form fills its parts");
  assert.ok(e1.done && e2.done);
});

test("a seed fills only blocks still without a result, and what it did not find stays for the button to ask", async () => {
  const url = "/uploads/seed-partial.pdf";
  // A job whose server could not translate a chunk's first paragraphs.
  await job(url, { pages: [1], translate: server((texts) => ({ translations: texts.map((t) => `job:${t}`), failed: [0, 1] })).translate });
  const before = translateState.entries(url).get(1);
  const failed = [...before.failed];
  assert.ok(failed.length > 0 && !before.done);
  const rows = await keptAfter({ pages: [1, 2] });
  await seed(url, { lookup: lookupOf(rows).lookup });
  const after = translateState.entries(url).get(1);
  for (let i = 0; i < before.paras.length; i++) {
    if (failed.includes(i)) assert.ok(after.texts[i]?.startsWith("译"), `block ${i}, failed, found now`);
    else assert.equal(after.texts[i], before.texts[i], `block ${i} keeps what it had`);
  }
  assert.equal(after.failed.size, 0);
  assert.ok(after.done);

  // Found in part: the page is not done, and a job asks only for the rest.
  const half = "/uploads/seed-half.pdf";
  const some = new Map([...rows].filter((_, n) => n % 2 === 0));
  await seed(half, { lookup: lookupOf(some).lookup });
  const e = translateState.entries(half).get(1);
  assert.ok(e.paras.some((p, i) => p.translate && e.texts[i] === undefined) && !e.done);
  assert.equal(translateState.buttonState(translateState.snapshot(half), "zh-CN|model", 1).current, false, "the button translates");
  const rest = server();
  await job(half, { pages: [1], translate: rest.translate });
  assert.ok(rest.texts().length > 0);
  assert.ok(rest.texts().every((t) => !some.has(t)), "nothing found is asked again");
  assert.ok(translateState.entries(half).get(1).done);
});

test("a reference list a job left is left again, and one translated with references too comes back", async () => {
  const pages = [{ pn: 1, runs: PAPER.pages[0].runs }, { pn: 2, runs: LIST_RUNS }];
  const mixed = [{ pn: 1, runs: [...PAPER.pages[1].runs, ...LIST_RUNS.map((r) => ({ ...r, y: r.y + 600 }))] }];
  const s = server();
  const made = "/uploads/seed-refs-made.pdf";
  await job(made, { doc: pdfDoc(pages), key: "zh", translate: s.translate });
  await job("/uploads/seed-refs-mixed.pdf", { doc: pdfDoc(mixed), pages: [1], key: "zh", translate: s.translate });
  const rows = new Map(s.texts().map((t) => [t, zh(t)]));
  const lookup = lookupOf(rows).lookup;

  const url = "/uploads/seed-refs.pdf";
  await translateState.seedPages(url, { doc: pdfDoc(pages), key: "zh", pages: [1, 2], lookup });
  assert.ok(translateState.entries(url).get(1).done);
  assert.equal(translateState.entries(url).get(2), undefined, "a page of references alone with nothing found: no entry");
  // A page with a list below its text is done, its list left as the job left it.
  const back = "/uploads/seed-refs-mixed-back.pdf";
  await translateState.seedPages(back, { doc: pdfDoc(mixed), key: "zh", pages: [1], lookup });
  const left = translateState.entries(back).get(1);
  const shaped = translateState.entries("/uploads/seed-refs-mixed.pdf").get(1);
  assert.ok(shaped.left.size > 0, "the job left the list");
  assert.ok(left.done);
  assert.deepEqual([...left.left].sort(), [...shaped.left].sort());
  assert.deepEqual(left.texts, shaped.texts);

  // Asked for with Translate references too, kept, and back.
  await job(made, { doc: pdfDoc(pages), key: "zh", pages: [2], references: true, translate: s.translate });
  const all = new Map(s.texts().map((t) => [t, zh(t)]));
  const refs = "/uploads/seed-refs-back.pdf";
  await translateState.seedPages(refs, { doc: pdfDoc(pages), key: "zh", pages: [1, 2], lookup: lookupOf(all).lookup });
  const e = translateState.entries(refs).get(2);
  assert.ok(e.done && e.left.size === 0);
  assert.ok(e.texts.some((t) => t?.includes("Phys. Rev. Lett.")), "the list's translation is back");
});

test("a page where nothing but the running head and foot it shares with the others was found gets no entry", async () => {
  const run = (str, y, h = 10, font = "b") => ({ str, x: 54, y, w: str.length * 5, h, font, face: font === "b" ? "CMR10" : "CMR8", asc: 0.69, desc: -0.19 });
  const pages = ["one", "two", "three"].map((name, k) => ({ pn: k + 1, runs: [
    run("Nature Communications", 30, 8, "h"),
    run(`The atoms of page ${name} sit in a lattice of light that holds them`, 100),
    run(`apart, so that page ${name} can say how far they are from each other`, 112),
    run(`and how the drive moves them once it is switched on and off again.`, 124),
    run("Article published under a licence of its own", 760, 8, "h"),
  ] }));
  // A page of references a job finished: its heads translated, its list left.
  pages.push({ pn: 4, runs: [run("Nature Communications", 30, 8, "h"), ...LIST_RUNS, run("Article published under a licence of its own", 760, 8, "h")] });
  const s = server();
  await job("/uploads/heads-made.pdf", { doc: pdfDoc(pages), pages: [1, 4], key: "zh", translate: s.translate });
  const rows = new Map(s.texts().map((t) => [t, zh(t)]));
  assert.ok(rows.has("Nature Communications"), `page 1's head went with it: ${JSON.stringify([...rows.keys()])}`);

  const url = "/uploads/heads.pdf";
  await translateState.seedPages(url, { doc: pdfDoc(pages), key: "zh", pages: [1, 2, 3, 4], lookup: lookupOf(rows).lookup });
  const e1 = translateState.entries(url).get(1);
  assert.ok(e1?.done && e1.texts.includes("译Nature Communications"), "the page kept comes back, its head with it");
  assert.equal(translateState.entries(url).get(2), undefined, "a page never translated shows no translated head");
  assert.equal(translateState.entries(url).get(3), undefined);
  const e4 = translateState.entries(url).get(4);
  assert.ok(e4?.done && e4.left.size === LIST_RUNS.length && e4.texts.includes("译Nature Communications"),
    "a page of references comes back done, as the job left it");
  assert.equal(translateState.buttonState(translateState.snapshot(url), "zh", 2).pages, 2, "two pages count as translated");
});

test("a figure's label translated by hand comes back from its kept row, and the page's done is the same", async () => {
  const { OPS } = await import("pdfjs-dist/legacy/build/pdf.mjs");
  const run = (str, x, y, w) => ({ str, x, y, w, h: 9, font: "f", face: "CMR9", asc: 0.69, desc: -0.19 });
  const lines = ["The ensemble of atoms shares one excitation and loses its coherence", "when the blockade is broken by the probe beam, so the signal falls",
    "with the detuning of the probe and the time spent in the excited state."];
  const pages = [{ pn: 1, width: 612, height: 792, runs: [...lines.map((s, k) => run(s, 54, 60 + 11 * k, 300)), run("Time in ms", 200, 390, 60)] }];
  const fnArray = [], argsArray = [];
  const rect = (x, y, w, h) => {
    fnArray.push(OPS.constructPath, OPS.stroke);
    argsArray.push([[OPS.rectangle], [x, y, w, h], null], null);
  };
  rect(100, 200, 300, 200);
  for (let k = 1; k <= 12; k++) rect(100 + 22 * k, 395, 1, 5);
  const doc = () => pdfDoc(pages, { drawn: () => ({ fnArray, argsArray }) });
  const s = server();
  const made = "/uploads/seed-figure-made.pdf";
  await job(made, { doc: doc(), pages: [1], key: "zh", translate: s.translate });
  const label = translateState.entries(made).get(1).paras.findIndex((p) => p.figure);
  assert.ok(label >= 0);
  const body = new Map(s.texts().map((t) => [t, zh(t)]));
  // Translate this paragraph on the label: a focused job, fresh.
  await job(made, { doc: doc(), pages: [1], key: "zh", focus: { pn: 1, i: label }, fresh: true, translate: s.translate });
  const rows = new Map(s.texts().map((t) => [t, zh(t)]));
  assert.ok(rows.size > body.size, "the label went to the model");

  const url = "/uploads/seed-figure.pdf";
  await translateState.seedPages(url, { doc: doc(), key: "zh", pages: [1], lookup: lookupOf(rows).lookup });
  const e = translateState.entries(url).get(1);
  assert.equal(e.texts[label], "译Time in ms", "the label comes back");
  assert.ok(e.done);
  // Its row not kept: the label stays original and the page is done all the same.
  const plain = "/uploads/seed-figure-plain.pdf";
  await translateState.seedPages(plain, { doc: doc(), key: "zh", pages: [1], lookup: lookupOf(body).lookup });
  const p = translateState.entries(plain).get(1);
  assert.equal(p.texts[label], undefined);
  assert.ok(p.done);
  // Only the label kept: the page gets it, and the button asks for the rest.
  const only = "/uploads/seed-figure-only.pdf";
  const labelRows = new Map([...rows].filter(([t]) => !body.has(t)));
  await translateState.seedPages(only, { doc: doc(), key: "zh", pages: [1], lookup: lookupOf(labelRows).lookup });
  const o = translateState.entries(only).get(1);
  assert.equal(o.texts[label], "译Time in ms");
  assert.ok(!o.done);
});

test("a job aborts a seed under way, a forget drops one that answers after it, and another key's page is left alone", async () => {
  const rows = await keptAfter({ pages: [1, 2] });
  // A lookup that answers only when told, and rejects at an abort, as a fetch does.
  const held = () => {
    const asked = [];
    let answer = null;
    const lookup = (texts, signal) => new Promise((resolve, reject) => {
      asked.push({ texts, signal });
      answer = () => resolve(texts.map((t) => rows.get(t) ?? null));
      signal.addEventListener("abort", () => reject(new Error("aborted")));
    });
    return { asked, lookup, answer: () => answer?.() };
  };

  const url = "/uploads/seed-aborted.pdf";
  const h = held();
  const seeding = seed(url, { lookup: h.lookup });
  await until(() => h.asked.length === 1, "the lookup");
  const s = server();
  const running = job(url, { pages: [1], translate: s.translate });
  assert.equal(h.asked[0].signal.aborted, true, "the job aborts it");
  await Promise.all([seeding, running]);
  assert.ok(s.asked.length > 0, "the job asked for page 1");
  // Page 2 was not seeded: a later seed looks it up.
  const later = lookupOf(rows);
  await seed(url, { lookup: later.lookup });
  assert.equal(later.asked.length, 1);
  assert.ok(translateState.entries(url).get(2).done);

  const gone = "/uploads/seed-forgotten.pdf";
  const g = held();
  const pending = seed(gone, { lookup: g.lookup });
  await until(() => g.asked.length === 1, "the lookup");
  translateState.forget(gone);
  g.answer();
  await pending;
  assert.equal(translateState.entries(gone).size, 0, "the forgotten paper stays forgotten");
  const again = lookupOf(rows);
  await seed(gone, { lookup: again.lookup });
  assert.equal(again.asked.length, 1, "after a forget the pages may be looked up again");

  // A seed answering after the key changed lands nothing.
  const switched = "/uploads/seed-switched.pdf";
  const w = held();
  const first = seed(switched, { lookup: w.lookup });
  await until(() => w.asked.length === 1, "the lookup");
  // (Its promise waits for every seed under way, the first included.)
  const second = seed(switched, { key: "ja|model", lookup: lookupOf(new Map()).lookup });
  w.answer();
  await Promise.all([first, second]);
  assert.equal(translateState.entries(switched).size, 0);

  // A page holding another key's translation is not this key's to fill.
  const other = "/uploads/seed-other-key.pdf";
  await job(other, { pages: [1], key: "ja|model", translate: server().translate });
  await seed(other, { lookup: lookupOf(rows).lookup });
  assert.equal(translateState.entries(other).get(1).key, "ja|model");
  assert.equal(translateState.entries(other).get(2).key, "zh-CN|model");

  // No seed while a job runs.
  const busy = "/uploads/seed-busy.pdf";
  const hs = heldServer();
  const runningJob = job(busy, { parallel: 1, translate: hs.translate });
  await until(() => hs.asked.length > 0, "the job's request");
  const none = lookupOf(rows);
  await seed(busy, { lookup: none.lookup });
  assert.equal(none.asked.length, 0);
  hs.free();
  await runningJob;
});
