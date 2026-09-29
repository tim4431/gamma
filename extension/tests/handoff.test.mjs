import assert from "node:assert/strict";
import { test } from "node:test";
import {
  MAX_BACKGROUND, MAX_OPENS, backgroundBusy, checkPage, handoffIdFrom, harvestUrls, needsSignIn, nextToOpen, sameWork,
  signInUrl, sourceIds,
} from "../handoff.js";

const ORIGIN = "https://gamma.example";

test("a tab is fetching for the chat on any server's /go address", () => {
  assert.equal(handoffIdFrom(`${ORIGIN}/api/ai/handoffs/AbC_12-xyzXYZ09/go`), "AbC_12-xyzXYZ09");
  // The Connector set to 127.0.0.1 while the app is open on localhost: the
  // server it asks decides (a look-alike elsewhere just gets a 404 there).
  assert.equal(handoffIdFrom("http://localhost:9001/api/ai/handoffs/AbC_12-xyzXYZ09/go?x=1"), "AbC_12-xyzXYZ09");
  for (const url of [
    `${ORIGIN}/api/ai/handoffs/AbC_12-xyzXYZ09`,                // the status, not the page
    `${ORIGIN}/api/ai/handoffs/AbC_12-xyzXYZ09/go/extra`,
    `${ORIGIN}/api/ai/handoffs/a/go`,                           // not an id's shape
    "file:///api/ai/handoffs/AbC_12-xyzXYZ09/go",
    "not a url", "",
  ]) assert.equal(handoffIdFrom(url), "", url);
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
  // A DOI only guessed from the page's text — often one of its references —
  // must not turn the paper's own page away (ScienceDirect names none in meta).
  assert.equal(sameWork({ kind: "maybe", doi: "10.1103/PhysRevLett.9.9" }, request), true);
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
  // The page's own "View PDF" links (ScienceDirect's pdfft), same site only.
  const sd = "https://www.sciencedirect.com/science/article/pii/0031916362913690";
  assert.deepEqual(harvestUrls({ kind: "none", pdf_links: [`${sd}/pdfft?md5=a&pid=b.pdf`, "https://ads.example/x.pdf"] }, {},
    { tabUrl: sd }), [`${sd}/pdfft?md5=a&pid=b.pdf`]);
});

test("when downloads fail, the tab opens the next untried link — never from the viewer", () => {
  const urls = ["https://a.example/pdf/1", "https://a.example/pdf/2"];
  const tried = urls.map((url) => ({ url, landed: url })); // refused or an HTML redirect page
  assert.equal(nextToOpen(tried), urls[0]);
  assert.equal(nextToOpen(tried, { opened: { [urls[0]]: 1 } }), urls[1]);
  const both = { [urls[0]]: 1, [urls[1]]: 1 };
  assert.equal(nextToOpen(tried, { opened: both }), "", "each link once: then the card says so");
  // Opened too early, then back from the university's sign-in on another
  // site: worth opening again.
  assert.equal(nextToOpen(tried, { opened: both, away: true }), urls[0]);
  const spent = { [urls[0]]: MAX_OPENS, [urls[1]]: MAX_OPENS };
  assert.equal(nextToOpen(tried, { opened: spent, away: true }), "", "a redirect loop ends");
  assert.equal(nextToOpen(tried, { viewer: true }), "", "the viewer's own URL is the PDF already");
});

test("a download that ends on a sign-in page, or back on the article, waits for the user", () => {
  const article = "https://pub.example/article/1";
  const toLogin = [{ url: "https://pub.example/pdf/1", landed: "https://pub.example/action/showLogin?uri=%2Fpdf%2F1" }];
  assert.equal(nextToOpen(toLogin, { tabUrl: article }), "", "opening it would leave the page's sign-in buttons");
  assert.equal(needsSignIn(toLogin), true);
  const back = [{ url: "https://pub.example/pdf/1", landed: `${article}#no-access` }];
  assert.equal(nextToOpen(back, { tabUrl: article }), "", "the link leads back here: no access yet");
  assert.equal(needsSignIn(back), false);
  assert.equal(signInUrl("https://idp.stanford.edu/idp/profile/SAML2/Redirect/SSO"), true);
  assert.equal(signInUrl("https://www.sciencedirect.com/science/article/pii/1/pdfft"), false);
});

test("a bot check shows by its host, its title, or the challenge the page carries", () => {
  assert.equal(checkPage({ url: "https://validate.perfdrive.com/?ssa=1", title: "" }), true);
  assert.equal(checkPage({ url: "https://www.worldscientific.com/doi/10.1142/x", title: "Just a moment..." }), true);
  assert.equal(checkPage({ url: "https://www.science.org/doi/x", title: "Attention Required! | Cloudflare" }), true);
  assert.equal(checkPage({ url: "https://pubs.example/x", title: "Verifying you are human" }), true);
  assert.equal(checkPage({ url: "https://pubs.example/x", title: "An article", check: true }), true, "detect.js saw a challenge");
  assert.equal(checkPage({ url: "https://onlinelibrary.wiley.com/doi/10.1002/x", title: "Josephson Junctions - Wiley" }), false);
  assert.equal(checkPage({}), false);
});

test("out-of-sight fetches take a few turns; a tab waiting for the user holds none", () => {
  const bg = (id, note = "") => ({ id, background: true, note });
  assert.equal(backgroundBusy({}), false);
  const full = Object.fromEntries(Array.from({ length: MAX_BACKGROUND }, (_, i) => [i + 1, bg(`r${i}`)]));
  assert.equal(backgroundBusy(full), true);
  // Two tabs of one request (a PDF link opened in a new tab) are one turn.
  assert.equal(backgroundBusy({ 1: bg("a"), 2: bg("a"), 3: bg("b") }), false);
  assert.equal(backgroundBusy({ ...full, 1: bg("r0", "signin") }), false, "a sign-in waits for the user");
  assert.equal(backgroundBusy({ ...full, 1: bg("r0", "check") }), false);
  assert.equal(backgroundBusy({ ...full, 1: { id: "r0", background: false } }), false, "a tab the user sees");
  assert.equal(backgroundBusy({ ...full, 1: bg("r0", "opening") }), true, "opening a PDF link still works on it");
});
