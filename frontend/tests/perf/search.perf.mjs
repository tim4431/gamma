// npm run test:perf (from frontend/) — a PDF page's text made searchable as
// the viewer makes it once per page (pdf/PdfViewer.jsx's search: runChars,
// then textnorm.js normalizeChars), held to a budget (docs/dev/debugging.md
// "Performance budgets"). The viewer's query loop over the cached pages is
// inline in the component and not timed here.
import assert from "node:assert/strict";
import { test } from "node:test";
import { runChars } from "../../src/pdf/pdfCitation.js";
import { normalizeChars } from "../../src/shared/lib/textnorm.js";
import { pageRuns, rng } from "./fixtures.mjs";
import { budget, measure } from "./measure.mjs";

const rnd = rng(3);
const pages = Array.from({ length: 20 }, () => pageRuns(rnd));
const searchText = (runs) => normalizeChars(runChars(runs, { fillSpaces: true }));

test("search text x20 pages: runChars + normalizeChars over 60 runs each", async (t) => {
  const { norm, src } = searchText(pages[0]);
  const text = norm.join("");
  assert.equal(norm.length, src.length);
  assert.ok(text.includes("finite lattice trans"), "the ligature folded and the soft hyphen dropped");
  assert.ok(!/trans-|\n|­|ﬁ/.test(text), "words rejoined across the hyphenated line breaks");
  budget(t, await measure(() => { for (const runs of pages) searchText(runs); }), 20);
});
