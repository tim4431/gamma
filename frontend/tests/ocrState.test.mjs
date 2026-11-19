// The recognized text a tab knows of its stored PDFs (src/pdf/ocrState.js),
// against a fake server: the state and the pages' words asked for as pages
// read them, the revision that rebuilds a changed page, what the translated
// view and a folder's export read, the Document menu's row and App's search
// nonce.
import test, { mock } from "node:test";
import assert from "node:assert/strict";
import { getDocument } from "pdfjs-dist/legacy/build/pdf.mjs";
import * as ocrState from "../src/pdf/ocrState.js";
import { pageText } from "../src/pdf/pageText.js";
import * as translateState from "../src/pdf/translateState.js";
import { paperLayout } from "../src/pdf/folderTranslation.js";

const linesOf = (n) => [[[`page${n}`, 1000, 1000, 2000, 1150], ["words", 2100, 1000, 2800, 1150]]];
// Lines enough for a paragraph the translation sends: four of a sentence.
const SENTENCE = "Recognized words read from the picture of this scanned page make a sentence long enough to translate".split(" ");
const proseOf = (n) => [0, 1, 2, 3].map((k) => SENTENCE.map((w, i) => [i ? w : `${w}${n}`, 1000 + i * 520, 1000 + k * 200,
  1000 + i * 520 + 480, 1150 + k * 200]));
const flush = async () => { for (let i = 0; i < 20; i++) await null; };
async function until(ok, what) {
  for (let i = 0; i < 200 && !ok(); i++) await new Promise((r) => setTimeout(r, 2));
  assert.ok(ok(), `timed out waiting for ${what}`);
}

// GET /api/ocr/<doc> answers `state`, GET .../pages the recognized pages'
// lines in the range under the state's stamp (`words(n)`); `asked` keeps
// every path, `hold` keeps the state's answers until `release()`, `fail`
// refuses all; what the translated view hears is in `heard`, or goes to
// translateState itself (`translation`).
function server(state = { stamp: "", recognized: [] }, { words = linesOf, translation = false } = {}) {
  const s = { state, asked: [], fail: false, hold: false, held: [], heard: [] };
  s.json = async (path) => {
    s.asked.push(path);
    if (s.fail) throw new Error("offline");
    const pages = /\/pages\?first=(\d+)&last=(\d+)$/.exec(path);
    if (!pages) {
      const answer = structuredClone(s.state);
      return s.hold ? new Promise((resolve) => s.held.push(() => resolve(answer))) : answer;
    }
    const out = {};
    for (let n = Number(pages[1]); n <= Number(pages[2]); n++) if (s.state.recognized.includes(n)) out[n] = { lines: words(n) };
    return { stamp: s.state.stamp, pages: out };
  };
  s.release = () => { const h = s.held; s.held = []; for (const answer of h) answer(); };
  s.pagesAsked = () => s.asked.filter((p) => p.includes("/pages?")).map((p) => p.split("?")[1]);
  s.statesAsked = () => s.asked.filter((p) => !p.includes("/pages?")).length;
  ocrState.reset();
  ocrState.connect({ json: s.json, workspace: () => "w1",
    textChanged: translation ? translateState.textChanged : (url, pages) => s.heard.push([url, pages]) });
  return s;
}

// A pdf.js document as the viewer hands it over, and its pages.
function pdfDoc(numPages) {
  const commonObjs = {};
  return { numPages, _transport: { commonObjs }, page: (n, over = {}) => ({ commonObjs, pageNumber: n, ...over }) };
}

// A pdf.js document whose pages the translation can segment, each page's own
// layer `layer` lines of text (none: a scan with no layer).
function pagedDoc(numPages, layer = []) {
  const commonObjs = { has: () => true, get: (id, then) => { const face = { name: "Times" }; then?.(face); return face; } };
  return {
    numPages, _transport: { commonObjs },
    async getPage(pn) {
      return {
        pageNumber: pn, commonObjs,
        getViewport: () => ({ transform: [1, 0, 0, -1, 0, 792], width: 612, height: 792 }),
        getTextContent: async () => ({ styles: { f: {} },
          items: layer.map((str, k) => ({ str, transform: [10, 0, 0, 10, 72, 700 - 14 * k], width: 400, fontName: "f", hasEOL: true })) }),
        getOperatorList: async () => ({ fnArray: [], argsArray: [] }),
        cleanup() {},
      };
    },
    destroy: async () => {},
  };
}

test("a page reads its recognized words in chunks of the recognized pages nearest it", async () => {
  const s = server({ stamp: "s1", recognized: Array.from({ length: 40 }, (_, k) => k + 1), kept: [], scanned: 40, pages: 40 });
  const doc = pdfDoc(40);
  ocrState.register(doc, "d1", "/api/uploads/d1.pdf");
  assert.deepEqual(await ocrState.lines(doc.page(20)), linesOf(20));
  assert.deepEqual(s.pagesAsked(), ["first=13&last=28"], "the page and the 15 nearest it, the one after first");
  assert.deepEqual(await ocrState.lines(doc.page(21)), linesOf(21));
  assert.equal(s.pagesAsked().length, 1, "a page read with them asks nothing");
  assert.deepEqual(await ocrState.lines(doc.page(30)), linesOf(30));
  assert.deepEqual(s.pagesAsked(), ["first=13&last=28", "first=29&last=40"], "then the pages not read yet");
  assert.equal(await ocrState.lines(doc.page(41)), null);
  assert.equal(await ocrState.lines(pdfDoc(3).page(1)), null, "a document never registered reads its own text");
  assert.equal(s.pagesAsked().length, 2);
});

test("the first answer changes no page a reader waited for; each later change moves the revision on, and the translation hears which", async () => {
  const s = server({ stamp: "s1", recognized: [2, 3], kept: [1], scanned: 4 });
  const doc = pdfDoc(4), url = "/api/uploads/d2.pdf";
  ocrState.register(doc, "d2", url);
  assert.deepEqual(await ocrState.lines(doc.page(2)), linesOf(2), "a page waits for the first answer");
  await flush();
  assert.equal(ocrState.revision("d2"), 0, "no reader read other words than the first answer's");
  assert.deepEqual(s.heard, []);
  // The same answer again (the PDF opened again) changes nothing.
  ocrState.register(doc, "d2", url);
  await until(() => s.statesAsked() === 2, "the state asked again");
  await flush();
  assert.equal(ocrState.revision("d2"), 0);
  assert.deepEqual(s.heard, []);
  // A new recognition: every page it or the last one covered may read otherwise.
  s.state = { stamp: "s2", recognized: [3, 4], kept: [1], scanned: 4 };
  ocrState.refresh("d2");
  await until(() => s.heard.length === 1, "the new state");
  const first = ocrState.revision("d2");
  assert.ok(first > 0);
  assert.deepEqual(s.heard, [[url, [2, 3, 4]]]);
  assert.deepEqual([1, 2, 3, 4].map((n) => ocrState.pageRevision("d2", n)), [0, first, first, first], "the changed pages rebuild, the others not");
  // The same recognition, a page more: only that one.
  s.state = { stamp: "s2", recognized: [2, 3, 4], kept: [1], scanned: 4 };
  ocrState.refresh("d2");
  await until(() => s.heard.length === 2, "the state with a page more");
  const second = ocrState.revision("d2");
  assert.ok(second > first);
  assert.deepEqual(s.heard[1], [url, [2]]);
  assert.deepEqual([1, 2, 3, 4].map((n) => ocrState.pageRevision("d2", n)), [0, second, first, first]);
  assert.equal(ocrState.revision("another"), 0);
});

test("a seed that waited for the first answer goes on, and finds the translations kept of the recognized words", async () => {
  const s = server({ stamp: "s1", recognized: [1, 2], kept: [], scanned: 2, pages: 2 }, { words: proseOf, translation: true });
  s.hold = true; // the state comes after the viewer seeds (SEED_REST_MS)
  const doc = pagedDoc(2), url = "/api/uploads/d7.pdf";
  ocrState.register(doc, "d7", url);
  const asked = [];
  const seeding = translateState.seedPages(url, { doc, key: "zh-CN|model", pages: [1, 2],
    lookup: async (texts) => { asked.push(...texts); return texts.map(() => "译文"); } });
  await new Promise((r) => setTimeout(r, 20));
  s.release();
  await seeding;
  assert.ok(asked.some((text) => text.startsWith("Recognized1 words")), "looked up by the recognized words");
  assert.deepEqual([...translateState.entries(url).keys()].sort(), [1, 2], "both pages seeded");
});

test("a folder's export reads a recognized paper's words, as the viewer does", async () => {
  server({ stamp: "s1", recognized: [1], kept: [], scanned: 1, pages: 1 }, { words: proseOf });
  const garbled = Array.from({ length: 4 }, () => "Rksum4 garbled layr txt of the scanned page with many misread wrds in it");
  const asked = [];
  let destroyed = 0;
  const doc = pagedDoc(1, garbled);
  doc.destroy = async () => { destroyed += 1; };
  await paperLayout({ url: "/api/uploads/d8.pdf", docId: "d8", pageId: "p8", key: "zh-CN|model", open: async () => doc,
    lookup: async ({ texts }) => { asked.push(...texts); return texts.map(() => null); } });
  assert.ok(asked.some((text) => text.startsWith("Recognized1 words")), "the recognized words looked up");
  assert.ok(!asked.some((text) => text.includes("Rksum4")), "not the PDF's own layer");
  assert.equal(destroyed, 1);
});

test("the words held of every document together are bounded, those read longest ago let go first", async () => {
  const all = Array.from({ length: 300 }, (_, k) => k + 1);
  const s = server({ stamp: "s1", recognized: all, kept: [], scanned: 300, pages: 300 });
  const docs = ["h1", "h2", "h3"].map((id) => { const doc = pdfDoc(300); ocrState.register(doc, id, ""); return doc; });
  for (const doc of docs) for (const n of all) await ocrState.lines(doc.page(n));
  const asked = s.pagesAsked().length;
  assert.deepEqual(await ocrState.lines(docs[2].page(300)), linesOf(300));
  assert.deepEqual(await ocrState.lines(docs[1].page(1)), linesOf(1));
  assert.equal(s.pagesAsked().length, asked, "the 800 pages read last are held");
  assert.deepEqual(await ocrState.lines(docs[0].page(1)), linesOf(1));
  assert.equal(s.pagesAsked().length, asked + 1, "the first document's first pages were let go, though it holds fewer than 800");
});

test("a state that comes after the first page was read rebuilds that page from the recognized words", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const s = server({ stamp: "s1", recognized: [1], kept: [], scanned: 1 });
    s.hold = true;
    const doc = pdfDoc(1), url = "/api/uploads/d3.pdf";
    ocrState.register(doc, "d3", url);
    let read = 0;
    const layer = { items: [{ str: "the PDF's own words", transform: [10, 0, 0, 10, 72, 700], width: 90, fontName: "g_d0_f1", hasEOL: true }], styles: {} };
    const page = doc.page(1, {
      getTextContent: async () => { read += 1; return structuredClone(layer); },
      getViewport: () => ({ transform: [1, 0, 0, -1, 0, 792], width: 612, height: 792 }),
    });
    const before = pageText(page);
    await flush();
    mock.timers.tick(3000); // the state has not come: the page reads its own layer
    assert.deepEqual(await before, layer);
    assert.equal(read, 1);
    assert.equal(ocrState.pageRevision("d3", 1), 0);
    s.release();
    await flush();
    assert.ok(ocrState.pageRevision("d3", 1) > 0, "its text layer is built again");
    assert.deepEqual(s.heard, [[url, [1]]], "and its paragraphs segmented again");
    const after = await pageText(page);
    assert.equal(after.ocr, true);
    assert.equal(after.items.map((it) => it.str).join(""), "page1 words");
    assert.equal(read, 1, "a recognized page never reads the PDF's layer");
  } finally {
    mock.timers.reset();
  }
});

test("the network failing reads as no recognized text, but a failed refresh keeps what was known", async () => {
  const s = server({ stamp: "s1", recognized: [1], kept: [], scanned: 1 });
  s.fail = true;
  const doc = pdfDoc(2), url = "/api/uploads/d4.pdf";
  ocrState.register(doc, "d4", url);
  assert.equal(await ocrState.lines(doc.page(1)), null);
  s.fail = false;
  ocrState.register(doc, "d4", url); // opened again
  await until(() => ocrState.revision("d4") > 0, "the state");
  assert.deepEqual(await ocrState.lines(doc.page(1)), linesOf(1));
  const rev = ocrState.revision("d4");
  s.fail = true;
  ocrState.refresh("d4");
  await until(() => s.asked.length === 4, "the refresh");
  await flush();
  assert.equal(ocrState.revision("d4"), rev);
  assert.deepEqual(await ocrState.lines(doc.page(1)), linesOf(1));
  // Pages answered under another stamp were recognized again meanwhile: the
  // state is read again, which announces it.
  s.fail = false;
  s.state = { stamp: "s2", recognized: [1, 2], kept: [], scanned: 2 };
  assert.equal(await ocrState.lines(doc.page(2)), null, "page 2 is not recognized as far as the tab knows");
  ocrState.refresh("d4");
  await until(() => ocrState.revision("d4") > rev, "the new state");
  const rev2 = ocrState.revision("d4");
  s.state.stamp = "s3";
  assert.equal(await ocrState.lines(doc.page(2)), null, "words of another recognition are not used");
  await until(() => ocrState.revision("d4") > rev2, "the state read again");
  assert.deepEqual(await ocrState.lines(doc.page(2)), linesOf(2));
  // The session's end: nothing is known, and a page asks afresh.
  ocrState.reset();
  assert.equal(ocrState.revision("d4"), 0);
  assert.deepEqual(await ocrState.lines(doc.page(2)), linesOf(2));
});

test("the Document menu offers recognition while scanned pages are left, or once the viewer met a scan, and waits for a run", async () => {
  const s = server({ stamp: "", recognized: [] });
  const doc = pdfDoc(12);
  ocrState.register(doc, "d5", "/api/uploads/d5.pdf");
  await until(() => s.asked.length === 1, "the state");
  await flush();
  const row = (over = {}) => ocrState.menuRow({ docId: "d5", offered: true, ...over });
  assert.equal(row({ offered: false }), null, "a reader, a server without the engine, a file not stored");
  assert.equal(row(), null, "nothing known of the file's pages yet");
  ocrState.noteScan(doc.page(3));
  assert.deepEqual(row(), {
    busy: false, pill: true,
    title: "Read the words of this PDF's scanned pages from their pictures: selection, search, the AI and translation read them, and each highlight there keeps its place and takes the new text. The PDF file itself is unchanged.",
  });
  const known = async (state) => {
    s.state = state;
    const version = s.asked.length;
    ocrState.refresh("d5");
    await until(() => s.asked.length > version, "the state");
    await flush();
  };
  await known({ stamp: "a", recognized: [1, 2, 3, 4, 5], kept: [6], scanned: 10 });
  assert.equal(row().pill, true, "4 pages left");
  await known({ stamp: "b", recognized: [1, 2, 3, 4, 5, 7, 8, 9, 10], kept: [6], scanned: 10 });
  assert.equal(row(), null, "every scanned page done");
  await known({ stamp: "", recognized: [], scanned: 0 });
  assert.equal(row(), null, "no scanned pages, whatever the viewer met");
  await known({ stamp: "g", recognized: [], kept: [2, 3, 4], scanned: 10, good_layer: true });
  assert.equal(row(), null, "a run found the PDF's own text reads well, its other layered pages not read");
  await known({ stamp: "c", recognized: [1], kept: [], scanned: 200 });
  assert.equal(row().pill, false, "a long book's run is followed in Background tasks");
  const running = { kind: "ocr", state: "running", workspace: "w1", params: { page_id: "p1", doc_id: "d5" } };
  assert.deepEqual(row({ jobs: [running] }), { busy: true, pill: false, title: "This PDF's text is being recognized — see Background tasks" });
  assert.equal(row({ jobs: [{ ...running, workspace: "w2" }] }).busy, false, "the same file in another workspace is another");
  assert.match(row({ translated: true }).title, / Its translation of those pages is made again from the new text when you next translate it\.$/);
  const started = [];
  await ocrState.startRecognizing({ start: async (...a) => started.push(a) }, "p1", row());
  assert.deepEqual(started, [["ocr", { page_id: "p1" }, { pill: false }]]);
});

test("the metadata popover says the text was recognized, or where to have it recognized", () => {
  assert.match(ocrState.textStatusTitle({ found: true, ok: true, ocr: { recognized: 3, at: "2026-11-18T10:00:00Z" } }, false),
    /^Text recognized from the page pictures on .*2026.*: the AI and search read it; the PDF file itself is unchanged\.$/);
  assert.equal(ocrState.textStatusTitle({ found: true, ok: false }, true),
    "No text layer — a scan? Choose Recognize text in this PDF in the Document menu so the AI and search can read it.");
  assert.equal(ocrState.textStatusTitle({ found: true, ok: false }, false), null, "the menu has no row: the popover's own title");
  // Recognized, and too little text for the AI: no row to point to.
  assert.match(ocrState.textStatusTitle({ found: true, ok: false, ocr: { recognized: 2, at: "2026-11-18T10:00:00Z" } }, true),
    /^The scanned pages were recognized from their pictures on .*2026.*, but they hold little or no readable text\.$/);
  assert.equal(ocrState.textStatusTitle({ found: true, ok: true }, true), null);
  assert.equal(ocrState.textStatusTitle({ checking: true }, true), null);
});

test("a replaced PDF's status says the new file's text is not recognized, where the menu would offer it", () => {
  assert.equal(ocrState.replacedNote({ text_not_recognized: true }, true),
    "The new file's text is not recognized: choose Recognize text in this PDF in the Document menu.");
  assert.equal(ocrState.replacedNote({ text_not_recognized: true }, false), "", "a guest, or a server without the engine");
  assert.equal(ocrState.replacedNote({ text_not_recognized: false }, true), "");
  assert.equal(ocrState.replacedNote(null, true), "");
});

test("a pdf.js document is known by what its pages share", async () => {
  const objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Contents 4 0 R >>", "<< /Length 0 >>\nstream\n\nendstream"];
  let pdf = "%PDF-1.4\n";
  const at = objects.map((o, k) => { const start = pdf.length; pdf += `${k + 1} 0 obj\n${o}\nendobj\n`; return start; });
  const xref = pdf.length;
  pdf += `xref\n0 5\n0000000000 65535 f \n${at.map((n) => `${String(n).padStart(10, "0")} 00000 n \n`).join("")}trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  const doc = await getDocument({ data: Uint8Array.from(pdf, (c) => c.charCodeAt(0)), verbosity: 0 }).promise;
  try {
    server({ stamp: "s1", recognized: [1], kept: [], scanned: 1 });
    ocrState.register(doc, "d6", "/api/uploads/d6.pdf");
    assert.deepEqual(await ocrState.lines(await doc.getPage(1)), linesOf(1));
  } finally {
    await doc.destroy();
  }
});

// Last: it gives the process a DOM, which it takes away after.
test("App's search nonce moves with the recognized words of the PDF on screen only, and never goes back", async () => {
  const s = server({ stamp: "", recognized: [], kept: [], scanned: 4 });
  ocrState.register(pdfDoc(4), "nA", "/api/uploads/nA.pdf");
  ocrState.register(pdfDoc(4), "nB", "/api/uploads/nB.pdf");
  await until(() => s.statesAsked() === 2, "both states");
  await flush();
  const { JSDOM } = await import("jsdom");
  const dom = new JSDOM("<!doctype html><body></body>");
  const had = { window: globalThis.window, document: globalThis.document };
  Object.assign(globalThis, { window: dom.window, document: dom.window.document });
  try {
    const React = (await import("react")).default;
    const { flushSync } = (await import("react-dom")).default;
    const { createRoot } = (await import("react-dom/client")).default;
    const seen = [];
    function Probe({ docId }) {
      seen.push(ocrState.useOcrNonce(docId));
      return null;
    }
    const root = createRoot(document.createElement("div"));
    const show = (docId) => flushSync(() => root.render(React.createElement(Probe, { docId })));
    const recognize = async (docId, state) => {
      s.state = state;
      const rev = ocrState.revision(docId);
      ocrState.refresh(docId);
      await until(() => ocrState.revision(docId) > rev, `${docId}'s new recognition`);
      await new Promise((r) => setTimeout(r, 10));
    };
    show("nA");
    assert.deepEqual(seen, [0]);
    // Another pane's PDF recognized: the pinned search on A stays as it is.
    await recognize("nB", { stamp: "b1", recognized: [1, 2, 3], kept: [], scanned: 4 });
    assert.equal(seen.at(-1), 0);
    await recognize("nA", { stamp: "a1", recognized: [1], kept: [], scanned: 4 });
    assert.equal(seen.at(-1), 1, "A's own recognition re-runs it");
    await recognize("nA", { stamp: "a2", recognized: [1, 2], kept: [], scanned: 4 });
    assert.equal(seen.at(-1), 2);
    // B on screen: its revision is below A's, and the nonce still grows.
    assert.ok(ocrState.revision("nB") < ocrState.revision("nA"));
    show("nB");
    assert.equal(seen.at(-1), 3);
    show("nB");
    assert.equal(seen.at(-1), 3, "the same document, the same revision");
    assert.ok(seen.every((n, i) => !i || n >= seen[i - 1]), "never goes back");
    root.unmount();
    await new Promise((r) => setTimeout(r, 20)); // React's last work, while it has its DOM
  } finally {
    for (const [name, value] of Object.entries(had)) {
      if (value === undefined) delete globalThis[name];
      else globalThis[name] = value;
    }
    dom.window.close();
  }
});
