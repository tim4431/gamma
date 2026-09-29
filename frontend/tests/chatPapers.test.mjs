import test from "node:test";
import assert from "node:assert/strict";
import { clipPayload, goodTitle, lookupQuery, paperIds, paperOf, paperPage, replyFetches } from "../src/chat/chatPapers.js";

test("a source's DOI and arXiv id, however they are spelled", () => {
  assert.deepEqual(paperIds("doi:10.1103/PhysRevB.27.1031"), { doi: "10.1103/physrevb.27.1031", arxiv: "" });
  assert.deepEqual(paperIds("https://doi.org/10.1109%2FTMAG.1983.1062440"), { doi: "10.1109/tmag.1983.1062440", arxiv: "" });
  // Publisher URLs build on the DOI; the view suffix is not part of it.
  assert.equal(paperIds("https://pubs.aip.org/aip/apl/article-pdf/doi/10.1063/1.123/pdf").doi, "10.1063/1.123");
  assert.equal(paperIds("(see 10.1002/047134608X.W1312).").doi, "10.1002/047134608x.w1312");
  assert.deepEqual(paperIds("arXiv:2301.01234v2"), { doi: "", arxiv: "2301.01234" });
  assert.deepEqual(paperIds("https://arxiv.org/pdf/2301.01234v1"), { doi: "", arxiv: "2301.01234" });
  assert.equal(paperIds("2301.01234").arxiv, "2301.01234", "a bare new-style id");
  assert.equal(paperIds("arxiv:cond-mat/0101001").arxiv, "cond-mat/0101001");
  assert.equal(paperIds("10.48550/arXiv.2301.01234").arxiv, "2301.01234", "arXiv's own DOI");
  assert.deepEqual(paperIds("https://example.org/paper"), { doi: "", arxiv: "" });
});

test("a reply's fetches: a card per request, a row per document read — once each, in call order", () => {
  const wiley = { id: "req1", host: "onlinelibrary.wiley.com", wall: "captcha", source: "doi:10.1002/047134608X.W1312" };
  const actions = [
    { kind: "fetch", tool: "fetch_paper", args: { source: "arXiv:2301.01234" }, url: "https://arxiv.org/pdf/2301.01234",
      title: "", pdf: true, pages: 12 },
    { kind: "fetch", tool: "fetch_paper", error: true, args: { source: wiley.source }, handoff: wiley },
    // The model reads the next window of the same PDF: still one row.
    { kind: "fetch", tool: "fetch_paper", args: { source: "https://arxiv.org/abs/2301.01234", pdf_offset: "20000" },
      url: "https://arxiv.org/pdf/2301.01234", pdf: true, pages: 12 },
    { kind: "fetch", tool: "fetch_paper", error: true, args: { source: "doi:10.9999/gone" }, result: "error: 404" },
    { kind: "fetch", tool: "fetch_paper", args: { source: "doi:10.1109/TMAG.1983.1062440" },
      url: "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?arnumber=1062440", title: "Josephson current in proximity junction",
      pdf: true, pages: 4, request: "req2" },
    { kind: "fetch", tool: "fetch_paper", error: true, args: { source: wiley.source }, handoff: { ...wiley } },
    { kind: "fetch", tool: "fetch_paper", args: { source: "https://lab.example/report" }, url: "https://lab.example/report",
      title: "A lab report", pdf: false },
    { kind: "search", summary: "Searched library" },
  ];
  const got = replyFetches(actions);
  assert.deepEqual(got.map((it) => (it.card ? `card:${it.card.id}` : it.paper.key)),
    ["arxiv:2301.01234", "card:req1", "doi:10.1109/tmag.1983.1062440", "https://lab.example/report"]);
  // Each shows at the step that first got it; the later windows and retries stay plain steps.
  assert.deepEqual(got.map((it) => it.index), [0, 1, 4, 6]);
  const [arxiv, , ieee, web] = got.map((it) => it.paper);
  assert.deepEqual([arxiv.pdf, arxiv.pages, arxiv.pdfUrl, arxiv.title], [true, 12, "https://arxiv.org/pdf/2301.01234", ""]);
  assert.equal(ieee.request, "req2", "the browser's PDF, held by the server, is what gets saved");
  assert.equal(ieee.title, "Josephson current in proximity junction");
  assert.deepEqual([web.pdf, web.url, web.title], [false, "https://lab.example/report", "A lab report"]);
  assert.deepEqual(replyFetches(undefined), []);
});

test("a handoff card's paper, and titles worth showing", () => {
  const p = paperOf("doi:10.1002/047134608X.W1312", { request: "req1" });
  assert.deepEqual([p.key, p.doi, p.request, p.pdf], ["doi:10.1002/047134608x.w1312", "10.1002/047134608x.w1312", "req1", false]);
  assert.equal(goodTitle("Ultracold Fermi gases with emergent SU(N) symmetry"), true);
  for (const junk of ["", "https://arxiv.org/pdf/2301.01234", "paper.pdf", "10.1103/x", "doi:10.1103/x", "arXiv:2301.01234"]) {
    assert.equal(goodTitle(junk), false, junk);
  }
});

test("adding goes through the clip endpoint with the paper's identifiers", () => {
  const [{ paper }] = replyFetches([{ kind: "fetch", tool: "fetch_paper", args: { source: "doi:10.1234/x" },
    url: "https://pub.example/x.pdf", title: "X", pdf: true }]);
  assert.deepEqual(clipPayload(paper, { folder: "Physics/SC", allowOa: false, saveCopy: true, fetchMetadata: true }), {
    source_url: "https://doi.org/10.1234/x", pdf_url: "https://pub.example/x.pdf", doi: "10.1234/x", arxiv_id: "",
    title: "X", folder: "Physics/SC", labels: [], allow_oa: false, save_copy: true, fetch_metadata: true,
  });
  assert.equal(clipPayload(paper, { docId: "abc" }).doc_id, "abc", "a PDF the browser delivered, stored first");
  assert.equal(paperPage({ arxiv: "2301.01234" }), "https://arxiv.org/abs/2301.01234");
  assert.equal(paperPage({ url: "https://lab.example/report" }), "https://lab.example/report");
  assert.equal(lookupQuery({ doi: "10.1234/x", pdfUrl: "https://pub.example/x.pdf" }),
    "doi=10.1234%2Fx&url=https%3A%2F%2Fpub.example%2Fx.pdf");
});
