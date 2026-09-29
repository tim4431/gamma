import assert from "node:assert/strict";
import { test } from "node:test";
import { handoffIdFrom, harvestUrls, sameWork, sourceIds } from "../handoff.js";

const ORIGIN = "https://gamma.example";

test("a tab is fetching for the chat only on its own server's /go address", () => {
  assert.equal(handoffIdFrom(`${ORIGIN}/api/ai/handoffs/AbC_12-xyzXYZ09/go`, ORIGIN), "AbC_12-xyzXYZ09");
  for (const url of [
    "https://evil.example/api/ai/handoffs/AbC_12-xyzXYZ09/go",   // another site's look-alike
    `${ORIGIN}/api/ai/handoffs/AbC_12-xyzXYZ09`,                // the status, not the page
    `${ORIGIN}/api/ai/handoffs/AbC_12-xyzXYZ09/go/extra`,
    `${ORIGIN}/api/ai/handoffs/a/go`,                           // not an id's shape
    "not a url", "",
  ]) assert.equal(handoffIdFrom(url, ORIGIN), "", url);
  assert.equal(handoffIdFrom(`${ORIGIN}/api/ai/handoffs/AbC_12-xyzXYZ09/go`, ""), "");
  assert.equal(handoffIdFrom("http://localhost:5173/api/ai/handoffs/AbC_12-xyzXYZ09/go?x=1", "http://localhost:5173"),
    "AbC_12-xyzXYZ09");
});

test("a request's source names its DOI or arXiv id in any spelling", () => {
  for (const s of ["doi:10.1126/Science.ABC", "10.1126/science.abc", "https://doi.org/10.1126/science.abc"]) {
    assert.equal(sourceIds(s).doi, "10.1126/science.abc", s);
  }
  assert.equal(sourceIds("arXiv:2301.12345v2").arxiv, "2301.12345");
  assert.equal(sourceIds("https://arxiv.org/abs/hep-th/9901001").arxiv, "hep-th/9901001");
  assert.deepEqual(sourceIds("https://lab.example/paper.pdf"), { doi: "", arxiv: "" });
});

test("another paper's PDF never answers the request", () => {
  const request = { source: "doi:10.1103/PhysRevLett.1.1" };
  assert.equal(sameWork({ doi: "10.1103/physrevlett.1.1" }, request), true);
  assert.equal(sameWork({ doi: "10.1103/PhysRevLett.9.9" }, request), false);
  assert.equal(sameWork({}, request), true, "a page that names no DOI may still be it (a PDF viewer)");
  assert.equal(sameWork({ doi: "10.1/x" }, { source: "https://lab.example/p.pdf" }), true);
  assert.equal(sameWork({ arxiv_id: "2101.00001" }, { source: "arXiv:2301.12345" }), false);
});

test("what the tab tries: itself when it shows a PDF, the advertised link, the server's on its site", () => {
  const request = { pdf_url: "https://www.science.org/doi/pdf/10.1126/science.abc" };
  // Chrome's PDF viewer (no content script): the tab's own URL first.
  assert.deepEqual(harvestUrls({}, request, { tabUrl: "https://www.science.org/doi/pdf/10.1126/science.abc", viewer: true }),
    ["https://www.science.org/doi/pdf/10.1126/science.abc"]);
  // The article page after signing in: its citation_pdf_url, then the server's link.
  assert.deepEqual(harvestUrls({ kind: "pdf", pdf_url: "https://science.org/doi/epdf/10.1126/science.abc" }, request,
    { tabUrl: "https://science.org/doi/10.1126/science.abc" }),
  ["https://science.org/doi/epdf/10.1126/science.abc", "https://www.science.org/doi/pdf/10.1126/science.abc"]);
  // A page of the site naming the paper only by its DOI still tries the server's link.
  assert.deepEqual(harvestUrls({ kind: "doi", doi: "10.1126/science.abc" }, request,
    { tabUrl: "https://www.science.org/doi/abs/10.1126/science.abc" }), [request.pdf_url]);
  // A sign-in page — the site's own or the university's — costs no request.
  assert.deepEqual(harvestUrls({ kind: "none" }, request, { tabUrl: "https://www.science.org/action/ssostart" }), []);
  assert.deepEqual(harvestUrls({}, request, { tabUrl: "https://idp.university.edu/login" }), []);
  assert.deepEqual(harvestUrls({ pdf_url: "javascript:alert(1)" }, {}, { tabUrl: "https://x.example/" }), []);
});
