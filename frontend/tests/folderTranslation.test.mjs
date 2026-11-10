// A folder's annotated-PDF export with its papers translated
// (src/pdf/folderTranslation.js): which papers, in which order, and what the
// job may carry of them. A paper's own layout is tested with the store's
// seeding (translateState.test.mjs).
import test from "node:test";
import assert from "node:assert/strict";
import { TRANSLATION_BOUNDS, folderPapers, folderTranslations, layoutUse } from "../src/pdf/folderTranslation.js";
import { libraryTree, pageBlocksOf } from "../src/library/libraryUtils.js";

const TREE = libraryTree({ folders: { children: [
  { id: "f-top", content: "Top", children: [{ id: "f-sub", content: "Sub", children: [] }] },
  { id: "f-other", content: "Other", children: [] },
] } });
const row = (id, props = {}, parent = "") => ({ id, content: id, properties: props, ...(parent && { _parentPage: parent }) });

test("a folder's papers are its pages and those below it that show a stored PDF, a page inside one filed where its top is", () => {
  const pages = pageBlocksOf([
    row("in-top", { doc_id: "aa", folders: ["f-top"] }),
    row("in-sub", { doc_id: "bb", folders: ["f-sub"] }),
    row("inside", { doc_id: "cc", folders: ["f-other"] }, "in-top"),
    row("elsewhere", { doc_id: "dd", folders: ["f-other"] }),
    row("notes", { folders: ["f-top"] }),
    row("link-only", { source_url: "https://example.org/a.pdf", folders: ["f-top"] }),
    row("duplicate", { source_url: "/api/uploads/aa.pdf", folders: ["f-top"] }),
  ], TREE);
  assert.deepEqual(folderPapers(pages, TREE, "f-top"), [
    { page_id: "in-top", doc_id: "aa" }, { page_id: "in-sub", doc_id: "bb" }, { page_id: "inside", doc_id: "cc" }]);
  assert.deepEqual(folderPapers(pages, TREE, "f-sub"), [{ page_id: "in-sub", doc_id: "bb" }]);
  assert.deepEqual(folderPapers(pages, TREE, "nowhere"), []);
});

const block = (text, formula = null) => ({ lines: [[0, 0, 100, 10], [0, 12, 100, 22]], size: 10,
  parts: formula ? [text, formula] : [text] });
const layout = (...blocks) => ({ lang: "de", pages: [{ page: 1, blocks }] });

test("what a layout counts against the server's bounds", () => {
  assert.deepEqual(layoutUse(layout(block("abc"), block("de", { page: 1, box: [0, 0, 10, 5], base: 4, text: "x" }))),
    { blocks: 2, lines: 4, parts: 3, chars: 6, area: 50 });
  assert.deepEqual(layoutUse(null), { blocks: 0, lines: 0, parts: 0, chars: 0, area: 0 });
  assert.equal(TRANSLATION_BOUNDS.blocks, 100_000, "routers/export.py TRANSLATION_BLOCKS_MAX");
});

test("each paper goes as it lays out, one at a time and in order; one that fails or holds nothing goes in the original", async () => {
  const papers = ["a", "b", "c", "d"].map((p) => ({ page_id: p, doc_id: `${p}${p}` }));
  const order = [];
  let running = 0, most = 0;
  const out = await folderTranslations(papers, { layoutOf: async (paper) => {
    running += 1;
    most = Math.max(most, running);
    order.push(paper.page_id);
    await new Promise((r) => setTimeout(r, 1));
    running -= 1;
    if (paper.page_id === "b") throw new Error("the PDF could not be read");
    if (paper.page_id === "c") return null;
    return layout(block(`Text of ${paper.page_id}`));
  } });
  assert.deepEqual(order, ["a", "b", "c", "d"]);
  assert.equal(most, 1);
  assert.deepEqual(out.map((t) => [t.page_id, t.doc_id, t.lang]), [["a", "aa", "de"], ["d", "dd", "de"]]);
  assert.deepEqual(out[0].pages, layout(block("Text of a")).pages);
});

test("a paper that would take the folder past the bounds goes in the original, and the papers after it still count", async () => {
  const papers = ["a", "b", "c"].map((p) => ({ page_id: p, doc_id: p }));
  const texts = { a: "x".repeat(6), b: "y".repeat(6), c: "z".repeat(3) };
  const out = await folderTranslations(papers, { layoutOf: async (paper) => layout(block(texts[paper.page_id])),
    bounds: { ...TRANSLATION_BOUNDS, chars: 10 } });
  assert.deepEqual(out.map((t) => t.page_id), ["a", "c"]);
});

test("a stopped read hands back the papers read so far", async () => {
  const ctl = new AbortController();
  const papers = ["a", "b", "c"].map((p) => ({ page_id: p, doc_id: p }));
  const out = await folderTranslations(papers, { signal: ctl.signal, layoutOf: async (paper) => {
    if (paper.page_id === "b") ctl.abort();
    return layout(block(paper.page_id));
  } });
  assert.deepEqual(out.map((t) => t.page_id), ["a"]);
});
