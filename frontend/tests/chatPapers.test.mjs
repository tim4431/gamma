import test from "node:test";
import assert from "node:assert/strict";
import { clipPayload, lookupQuery, paperIds, paperLabel, paperPage, replyPapers } from "../src/chat/chatPapers.js";

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

test("a reply's papers: what it read, what was blocked, what it links — once each", () => {
  const actions = [
    { kind: "fetch", tool: "fetch_paper", args: { source: "arXiv:2301.01234" }, url: "https://arxiv.org/pdf/2301.01234",
      title: "https://arxiv.org/pdf/2301.01234", pdf: true },
    { kind: "fetch", tool: "fetch_paper", error: true, args: { source: "doi:10.1002/047134608X.W1312" },
      handoff: { id: "req1", host: "onlinelibrary.wiley.com", wall: "captcha", source: "doi:10.1002/047134608X.W1312" } },
    { kind: "fetch", tool: "fetch_paper", error: true, args: { source: "doi:10.9/gone" }, result: "error: 404" },
    { kind: "fetch", tool: "fetch_paper", args: { source: "doi:10.1109/TMAG.1983.1062440" },
      url: "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?arnumber=1062440", title: "Josephson current in proximity junction",
      pdf: true, request: "req2" },
    { kind: "search", summary: "Searched library" },
  ];
  const text = [
    "1. [Proximity effect in superconductors](https://arxiv.org/abs/2301.01234) — the one I read.",
    "2. [Josephson junction review](https://doi.org/10.1142/9789811209260_0001)",
    "3. Also see 10.1103/PhysRevB.27.1031 and arXiv:cond-mat/0101001.",
    "```\n10.9999/in.code\n```",
    "[the Gamma docs](https://example.org/docs)",
  ].join("\n");
  const papers = replyPapers(actions, text);
  assert.deepEqual(papers.map((p) => p.key), [
    "arxiv:2301.01234", "doi:10.1002/047134608x.w1312", "doi:10.1109/tmag.1983.1062440",
    "doi:10.1142/9789811209260_0001", "doi:10.1103/physrevb.27.1031", "arxiv:cond-mat/0101001",
  ]);
  const [arxiv, wiley, ieee, linked, bare] = papers;
  // The link's label names the paper the fetch only knew by its URL.
  assert.equal(arxiv.title, "Proximity effect in superconductors");
  assert.equal(arxiv.read, true);
  assert.equal(arxiv.pdfUrl, "https://arxiv.org/pdf/2301.01234");
  assert.equal(wiley.blocked, true);
  assert.equal(wiley.request, "req1", "the handoff's PDF, if the browser sent it, is what gets saved");
  assert.equal(ieee.request, "req2");
  assert.equal(ieee.title, "Josephson current in proximity junction");
  assert.equal(linked.read, undefined);
  assert.equal(linked.title, "Josephson junction review");
  assert.equal(bare.title, "", "the registry names it when the list opens");
  assert.deepEqual(replyPapers(undefined, ""), []);
});

test("a PDF read from a repository and a link to the same title are one paper", () => {
  const actions = [
    { kind: "fetch", tool: "fetch_paper", args: { source: "https://repository.example.de/files/Li_2022_NJP.pdf" },
      url: "https://repository.example.de/files/Li_2022_NJP.pdf", title: "Robust polarization gradient cooling of trapped ions", pdf: true },
    { kind: "fetch", tool: "fetch_paper", args: { source: "https://lab.example.edu/uploads/07_enhanced.pdf" },
      url: "https://lab.example.edu/uploads/07_enhanced.pdf", title: "", pdf: true },
  ];
  const text = "See [Robust Polarization-Gradient Cooling of Trapped Ions](https://doi.org/10.1088/1367-2630/ac6233) "
    + "and [Enhanced laser cooling](https://doi.org/10.1103/other).";
  const papers = replyPapers(actions, text);
  assert.equal(papers.length, 3, "the repository's PDF and the DOI link are one row; the untitled PDF stays apart");
  const [li, enhanced] = papers;
  assert.equal(li.doi, "10.1088/1367-2630/ac6233");
  assert.equal(li.pdfUrl, "https://repository.example.de/files/Li_2022_NJP.pdf", "saved with the PDF the reply read");
  // Two identifiers that differ are two works, whatever their titles.
  const twins = replyPapers([], "[A title long enough](https://doi.org/10.1234/a) and [A title long enough](https://doi.org/10.1234/b)");
  assert.equal(twins.length, 2);

  // How each shows: never a raw address.
  assert.deepEqual(paperLabel(li), { name: "Robust polarization gradient cooling of trapped ions",
    where: "doi:10.1088/1367-2630/ac6233", href: "https://repository.example.de/files/Li_2022_NJP.pdf" });
  assert.deepEqual(paperLabel(enhanced), { name: "07 enhanced", where: "lab.example.edu",
    href: "https://lab.example.edu/uploads/07_enhanced.pdf" });
  assert.deepEqual(paperLabel({ doi: "10.1234/x", source: "doi:10.1234/x", title: "" }),
    { name: "doi:10.1234/x", where: "", href: "https://doi.org/10.1234/x" });
  assert.equal(paperLabel({ url: "https://www.lab.example/report", source: "https://www.lab.example/report", title: "" }).name,
    "lab.example");
});

test("saving goes through the clip endpoint with the paper's identifiers", () => {
  const [paper] = replyPapers([{ kind: "fetch", tool: "fetch_paper", args: { source: "doi:10.1234/x" },
    url: "https://pub.example/x.pdf", title: "X", pdf: true }], "");
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
