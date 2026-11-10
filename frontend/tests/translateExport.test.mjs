import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { segmentPage } from "../src/pdf/pdfTranslate.js";
import { exportFile, exportLayout, exportPages } from "../src/pdf/translateExport.js";

const KEY = "zh-CN|model-a";

// A block as the segmenter makes it: its line boxes, its size and its spans
// (formulas, citation markers, URLs), at scale 1.
const block = ({ lines = [[54, 100, 300, 110]], size = 10, spans = [], translate = true } = {}) => ({
  x1: Math.min(...lines.map((l) => l[0])), y1: Math.min(...lines.map((l) => l[1])),
  x2: Math.max(...lines.map((l) => l[2])), y2: Math.max(...lines.map((l) => l[3])),
  size, translate, spans, text: "", src: "", nLines: lines.length,
  lines: lines.map(([x1, y1, x2, y2]) => ({ x1, y1, x2, y2 })),
});
const math = (str, x1, y1, x2, y2, base) => ({ kind: "math", str, x1, y1, x2, y2, base });
// A page entry as translateJob.js hands it back.
const entry = (paras, texts, extra = {}) => ({ key: KEY, paras, texts, done: true, queued: false,
  busy: new Set(), partial: {}, failed: new Set(), ...extra });
const view = (pages, shown = true) => ({ entries: new Map(pages), shown, status: {} });

test("the translation is written only while it is shown, under the key the viewer shows", () => {
  const pages = [[1, entry([block()], ["译文"])]];
  assert.deepEqual(exportLayout(view(pages), KEY), {
    pages: [{ page: 1, blocks: [{ lines: [[54, 100, 300, 110]], size: 10, parts: ["译文"] }] }],
  });
  assert.equal(exportLayout(view(pages, false), KEY), null, "hidden: the original is exported");
  assert.equal(exportLayout(view(pages), "zh-CN|model-b"), null, "another model's entries do not render");
  assert.equal(exportLayout(view(pages), "de|model-a"), null);
  assert.equal(exportLayout(view([]), KEY), null);
  assert.equal(exportLayout(null, KEY), null);
  assert.equal(exportLayout(view(pages), ""), null);
});

test("only final texts are written: a block not landed or that came back as its source keeps its original", () => {
  const paras = [block(), block(), block(), block(), block({ translate: false })];
  const texts = [undefined, null, "", "第四段", "不翻译的块"];
  const e = entry(paras, texts, { done: false, queued: true, busy: new Set([0]), partial: { 0: "正在流式" } });
  const out = exportLayout(view([[3, e]]), KEY);
  assert.deepEqual(out.pages.map((p) => p.page), [3]);
  // "" is masked and written nothing; the partial text of block 0 never goes.
  assert.deepEqual(out.pages[0].blocks.map((b) => b.parts), [[], ["第四段"]]);
  // A page with only streamed text, or only masks, holds no translation yet.
  assert.equal(exportLayout(view([[1, entry([block()], [], { partial: { 0: "流" } })]]), KEY), null);
  assert.equal(exportLayout(view([[1, entry([block(), block()], ["", null])]]), KEY), null);
});

test("a block shown in the original here is written translated, and a figure's text the reader asked for is written", () => {
  const paras = [block(), block({ translate: false }), { ...block({ translate: false }), figure: true }];
  const e = entry(paras, ["第一段", undefined, "图中文字"], { original: new Set([0]) });
  assert.deepEqual(exportLayout(view([[1, e]]), KEY).pages[0].blocks.map((b) => b.parts), [["第一段"], ["图中文字"]]);
});

test("a formula token is the original's box on its page, from the block itself or another page's block", () => {
  const own = block({ spans: [math("x^2", 60.123, 101.456, 70.789, 109.994, 108.006), { kind: "text", str: "[12]" }] });
  const elsewhere = math("Ω", 400.5, 50.25, 410.75, 60.5, 58.333);
  const spanOf = (pn, i, k) => (pn === 2 && i === 4 && k === 1 ? elsewhere : null);
  const text = "其中 {v1} 是平方{v2}，而 {v:2.4.1} 为拉比频率，{v7} 和 {v:2.9.9} 不在";
  const out = exportLayout(view([[1, entry([own], [text])]]), KEY, spanOf);
  assert.deepEqual(out.pages[0].blocks[0].parts, [
    "其中 ",
    { page: 1, box: [60.12, 101.46, 70.79, 109.99], base: 108.01, text: "x^2" },
    " 是平方[12]，而 ",
    { page: 2, box: [400.5, 50.25, 410.75, 60.5], base: 58.33, text: "Ω" },
    " 为拉比频率， 和  不在",
  ], "a citation marker is its text, a token naming no span is dropped, the texts around them one string");
  // Without a lookup the other page's token is dropped as unknown, as the overlay drops it.
  assert.deepEqual(exportLayout(view([[1, entry([own], ["甲 {v:2.4.1} 乙"])]]), KEY).pages[0].blocks[0].parts, ["甲  乙"]);
});

test("coordinates are rounded to two decimals and pages go in order", () => {
  const b = block({ lines: [[54.004, 100.006, 300.3333, 110.6666], [54.1, 112.129, 280.556, 122.001]], size: 9.96312 });
  const pages = [[12, entry([b], ["十二"])], [2, entry([b], ["二"])], [7, entry([b], [null])], [3, entry([b], ["三"])]];
  const out = exportLayout(view(pages), KEY);
  assert.deepEqual(out.pages.map((p) => p.page), [2, 3, 12]);
  assert.deepEqual(out.pages[0].blocks[0].lines, [[54, 100.01, 300.33, 110.67], [54.1, 112.13, 280.56, 122]]);
  assert.equal(out.pages[0].blocks[0].size, 9.96);
});

test("a block goes as the overlay sets it: its room, centred, bold, italic, an opener's indent, and only what it has", () => {
  const set = { ...block({ lines: [[100, 100, 150, 110]] }), room: { x1: 54.004, x2: 300.006 }, centred: true, bold: true, italic: true };
  const hanging = { ...block(), indent: -12 };
  const opener = { ...block(), indent: 9.962 };
  const [first, plain, under, opens] = exportLayout(view([[1, entry([set, block(), hanging, opener], ["标题", "正文", "悬挂", "开头"])]]), KEY).pages[0].blocks;
  assert.deepEqual(first.box, [54, 100, 300.01, 110]);
  assert.deepEqual([first.centred, first.bold, first.italic], [true, true, true]);
  assert.deepEqual(Object.keys(plain).sort(), ["lines", "parts", "size"]);
  assert.deepEqual(Object.keys(under).sort(), ["lines", "parts", "size"], "a hanging indent has nowhere to go, as on screen");
  assert.equal(opens.indent, 9.96);
});

test("a block justified in the original goes justified, and with the name of the face it is set in", () => {
  const set = { ...block(), justified: true, font: "g_d0_f2", face: "CMR10" };
  const [written] = exportLayout(view([[1, entry([set], ["正文"])]]), KEY).pages[0].blocks;
  assert.equal(written.justified, true);
  assert.equal(written.face, "CMR10", "its name, which the file knows it by, not pdf.js's id");
  assert.equal(written.font, undefined);
});

test("a block's foot goes as on screen", () => {
  const [written] = exportLayout(view([[1, entry([{ ...block(), foot: 120.123 }], ["正文"])]]), KEY).pages[0].blocks;
  assert.equal(written.foot, 120.12);
});

test("a centred block goes with no indent, as on screen: its first line is only shorter", () => {
  const centred = { ...block(), centred: true, indent: 10.5 };
  const [written] = exportLayout(view([[1, entry([centred], ["标题"])]]), KEY).pages[0].blocks;
  assert.equal(written.centred, true);
  assert.equal(written.indent, undefined);
});

test("the dialog's count is the pages the export writes", () => {
  const pages = [
    [1, entry([block(), block()], ["一", ""])],
    [2, entry([block()], [null])],
    [3, entry([block()], ["三"], { key: "zh-CN|model-b" })],
    [4, entry([block()], [], { partial: { 0: "四" } })],
    [5, entry([block()], ["五"])],
  ];
  assert.equal(exportPages(view(pages), KEY), 2);
  assert.equal(exportPages(view(pages), KEY), exportLayout(view(pages), KEY).pages.length);
  assert.equal(exportPages(view(pages, false), KEY), 0);
  assert.equal(exportPages(view([]), KEY), 0);
  assert.equal(exportPages(null, KEY), 0);
});

test("the translation goes into the page's own stored file only, the one the job reads", () => {
  const H = "0123abcdef0123abcdef0123abcdef01";
  // an upload: the file on screen is the page's doc_id
  const upload = { kind: "pdf", id: H, url: `/api/uploads/${H}.pdf`, name: "a.pdf" };
  assert.equal(exportFile(`/api/uploads/${H}.pdf`, upload), H);
  assert.equal(exportFile(`/api/uploads/${H}.pdf?annots=17`, upload), H);
  assert.equal(exportFile("/api/uploads/ffff.pdf", upload), "", "another file than the page's");
  // a duplicated page: no doc_id, only the upload's address, so its
  // annotated PDF has no file (the job refuses "page has no PDF") and it
  // keeps exporting the file it shows
  const duplicate = { kind: "pdf", id: "", url: `/api/uploads/${H}.pdf`, name: "" };
  assert.equal(exportFile(`/api/uploads/${H}.pdf`, duplicate), "");
  assert.equal(exportFile("", null), "");
  // a paper added from its link: read through the proxy, whose copy is
  // saved under the page's doc_id
  const src = "https://arxiv.org/pdf/2401.00001";
  const link = { kind: "pdf", id: H, url: src, name: "" };
  assert.equal(exportFile(`/api/pdf?source_url=${encodeURIComponent(src)}&save=1`, link), H);
  assert.equal(exportFile(`/api/pdf?source_url=${encodeURIComponent("https://example.org/b.pdf")}`, link), "",
    "the proxy reading another link");
});

test("a segmented page's formulas and citation markers go out as the segmenter found them", () => {
  const paper = JSON.parse(fs.readFileSync(new URL("./fixtures/translatePaper.json", import.meta.url)));
  const paras = segmentPage(paper.pages.find((p) => p.pn === 1).runs);
  const i = paras.findIndex((p) => p.translate && p.spans.some((s) => s.kind === "math") && p.spans.some((s) => s.kind === "text"));
  const p = paras[i];
  const k = p.spans.findIndex((s) => s.kind === "math") + 1;
  const c = p.spans.findIndex((s) => s.kind === "text") + 1;
  const texts = [];
  texts[i] = `公式 {v${k}} 与引用 {v${c}}`;
  const out = exportLayout(view([[1, entry(paras, texts)]]), KEY);
  const f = p.spans[k - 1];
  const r = (n) => Math.round(n * 100) / 100;
  assert.deepEqual(out.pages[0].blocks, [{
    lines: p.lines.map((l) => [r(l.x1), r(l.y1), r(l.x2), r(l.y2)]),
    size: r(p.size),
    parts: ["公式 ", { page: 1, box: [r(f.x1), r(f.y1), r(f.x2), r(f.y2)], base: r(f.base), text: f.str },
      ` 与引用 ${p.spans[c - 1].str}`],
    indent: r(p.indent),
    face: "CMR10",
  }]);
});
