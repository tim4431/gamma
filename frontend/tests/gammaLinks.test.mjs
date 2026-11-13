import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  citesSeveralSources, gammaLinkIds, isSourceLink, parseGammaLink, relativeGammaLink,
} from "../src/shared/model/gammaLinks.js";

const ORIGIN = "https://gamma.test";

test("classifies block, page and citation links, relative or absolute", () => {
  const quote = "A result (n=42) & its evidence";
  assert.deepEqual(parseGammaLink(`/?page=paper&pdf_page=7&quote=${encodeURIComponent(quote)}`, ORIGIN),
    { kind: "citation", pageId: "paper", page: 7, quote, foreign: false });
  assert.deepEqual(parseGammaLink("?block=blk_1", ORIGIN), { kind: "block", blockId: "blk_1", foreign: false });
  assert.deepEqual(parseGammaLink(`${ORIGIN}/?page=paper&ws=w1`, ORIGIN),
    { kind: "page", pageId: "paper", foreign: false });
  // A quote is optional: the link then just opens the paper at that page.
  assert.deepEqual(parseGammaLink("/?page=paper&pdf_page=2", ORIGIN),
    { kind: "citation", pageId: "paper", page: 2, quote: "", foreign: false });
});

test("a link written against another host stays a Gamma link, marked foreign", () => {
  // The server moved (sidecar → NAS), or this is somebody else's Gamma: the
  // caller decides by resolving the id, so the shape must still parse.
  assert.deepEqual(parseGammaLink("https://old.host:9001/?page=paper&pdf_page=3&quote=long%20enough", ORIGIN),
    { kind: "citation", pageId: "paper", page: 3, quote: "long enough", foreign: true });
});

test("rejects non-Gamma URLs and malformed page/quote values", () => {
  for (const href of [
    "https://arxiv.org/abs/2401.00001",
    "/api/uploads/abc.pdf",
    "/?page=paper&pdf_page=-1",
    "/?page=paper&pdf_page=1.5",
    "/?page=paper&pdf_page=9999999",
    "/?page=bad%20id",
    "/?page=paper&pdf_page=1&quote=tiny",
    `/?page=paper&pdf_page=1&quote=${"x".repeat(2001)}`,
    "/?ws=w1",
  ]) assert.equal(parseGammaLink(href, ORIGIN), null, href);
});

test("a text file's line link: one line or a range, the first line's text as its quote", () => {
  assert.deepEqual(parseGammaLink("/?page=file_1&line=12", ORIGIN),
    { kind: "line", pageId: "file_1", line: 12, to: null, quote: "", foreign: false });
  const quote = "We fit a small network to the training set by gradient descent.";
  assert.deepEqual(parseGammaLink(`${ORIGIN}/?ws=w1&page=file_1&line=2-4&quote=${encodeURIComponent(quote)}`, ORIGIN),
    { kind: "line", pageId: "file_1", line: 2, to: 4, quote, foreign: false });
  // No least length: a code line is often a lone brace or \fi.
  assert.deepEqual(parseGammaLink("/?page=f&line=7&quote=%7D", ORIGIN),
    { kind: "line", pageId: "f", line: 7, to: null, quote: "}", foreign: false });
  assert.equal(parseGammaLink("/?page=f&line=9-9", ORIGIN).to, 9);
  assert.equal(parseGammaLink("/?page=f&line=10000000", ORIGIN).line, 10_000_000);
  assert.equal(parseGammaLink("https://old.host/?page=f&line=3", ORIGIN).foreign, true);
  for (const href of [
    "/?page=f&line=0",
    "/?page=f&line=-1",
    "/?page=f&line=1.5",
    "/?page=f&line=",
    "/?page=f&line=3-",
    "/?page=f&line=L3",
    "/?page=f&line=10000001",
    "/?page=f&line=1-10000001",
    "/?page=f&line=5-3", // a range that ends before it starts
    "/?page=f&line=3&pdf_page=3", // two documents' places at once
  ]) assert.equal(parseGammaLink(href, ORIGIN), null, href);
});

const LINELINKS = JSON.parse(readFileSync(new URL("../../tests/shared/linelinks.json", import.meta.url), "utf8"));

test("a line link's line and quote parse as read_gamma_link takes them (the shared fixture)", () => {
  for (const c of LINELINKS.cases) {
    const href = `/?page=f&line=${encodeURIComponent(c.line)}${c.quote_chars ? `&quote=${"x".repeat(c.quote_chars)}` : ""}`;
    const got = parseGammaLink(href, ORIGIN);
    assert.deepEqual(got && [got.line, got.to], c.parsed, c.name);
  }
});

test("a PDF passage and a file's lines are both citations; several sources name theirs", () => {
  const line = parseGammaLink("/?page=f&line=3", ORIGIN);
  const cite = parseGammaLink("/?page=p&pdf_page=2", ORIGIN);
  assert.equal(isSourceLink(line), true);
  assert.equal(isSourceLink(cite), true);
  assert.equal(isSourceLink(parseGammaLink("/?page=p", ORIGIN)), false);
  assert.equal(isSourceLink(null), false);
  assert.equal(citesSeveralSources("[p. 2](/?page=p&pdf_page=2) and [main.tex:3](/?page=f&line=3)"), true);
  assert.equal(citesSeveralSources("[a](/?page=f&line=3) [b](/?page=f&line=9-12)"), false);
  // A page link is no citation: it never makes the pills name their source.
  assert.equal(citesSeveralSources("[p. 2](/?page=p&pdf_page=2) see [the file](/?page=f)"), false);
});

test("collects the ids a note's markdown links to, from targets and bare URLs", () => {
  const text = "see [p. 3](/?page=paper&pdf_page=3&quote=long%20enough%20text), "
    + "[notes](?block=blk_2) and https://old.host/?page=other plus https://example.com/?page=x "
    + "and [main.tex:7](/?page=file_1&line=7)";
  assert.deepEqual(gammaLinkIds(text), ["paper", "blk_2", "other", "x", "file_1"]);
  assert.deepEqual(gammaLinkIds("no links here"), []);
});

test("pasted links are stored host-free and workspace-free", () => {
  assert.equal(relativeGammaLink("https://old.host/?page=paper&ws=w1&pdf_page=2", ORIGIN), "/?page=paper&pdf_page=2");
  assert.equal(relativeGammaLink("?block=blk_1", ORIGIN), "/?block=blk_1");
  assert.equal(relativeGammaLink("https://old.host/?ws=w1&page=f&line=3-5&quote=%7D", ORIGIN), "/?page=f&line=3-5&quote=%7D");
});
