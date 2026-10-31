// The translated view's state per document (src/pdf/translateState.js) in
// Node: the fixture paper handed over as pdf.js hands a document over, a
// fake server, and no viewer at all.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { segmentPage } from "../src/pdf/pdfTranslate.js";
import { BREAK } from "../src/pdf/translatePlan.js";
import * as translateState from "../src/pdf/translateState.js";
import { TRANSLATE_PARALLEL_MAX } from "../src/app/prefDefs.js";
import { attachmentFile, pageAttachment } from "../src/library/libraryUtils.js";
import { docIdOf } from "../src/pdf/pdfSource.js";

const PAPER = JSON.parse(fs.readFileSync(new URL("./fixtures/translatePaper.json", import.meta.url)));
const last = (xs) => xs[xs.length - 1];

// The fixture's runs as a pdf.js document: text items in a scale-1
// viewport, their fonts' metrics, and the fonts' names in `commonObjs` —
// there from the start, or (`loaded: false`) only once the page's operator
// list is built. The pages that let their decoded images go are noted in
// `cleaned`, the pages asked for in `read`.
function pdfDoc(pages = PAPER.pages, { loaded = true } = {}) {
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
        getViewport: () => ({ transform: [1, 0, 0, 1, 0, 0] }),
        getTextContent: async () => ({
          items: runs.map((r) => ({ str: r.str, transform: [r.h, 0, 0, r.h, r.x, r.y], width: r.w, fontName: r.font })),
          styles: Object.fromEntries(runs.map((r) => [r.font, { ascent: r.asc, descent: r.desc }])),
        }),
        commonObjs: {
          has: (id) => built && names.has(id),
          get: (id, then) => { const face = { name: names.get(id) }; then?.(face); return face; },
        },
        getOperatorList: async () => { built = true; },
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
    assert.deepEqual(entries.get(pn).paras, segmentPage(runs), `page ${pn}`);
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
  assert.deepEqual(status, { running: false, progress: 1, label: "document", reading: false, error: null, untranslated: 0, unanswered: 0 });
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
  // A page whose fonts were there already (drawn) keeps what it has.
  const drawn = pdfDoc(PAPER.pages);
  await job("/uploads/drawn.pdf", { doc: drawn, pages: [2], translate: server().translate });
  assert.deepEqual(drawn.cleaned, []);
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
    "nor does the selection translator's, nor Forget this translation");
  assert.deepEqual(translateState.scopeOf("/uploads/next.pdf", next), { pageId: "page-next", stored: true, file: "next" });
});

test("a PDF opened from its link names the file the server keeps its translations under, which Forget this translation forgets", () => {
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
