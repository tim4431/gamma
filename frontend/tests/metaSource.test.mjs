// How a page's paper metadata is worded on every surface (metaSource.js):
// what tied the record to the document (meta.match) shows on the Source
// row, a record nothing confirmed warns with a hint that says why, and a
// record from before the server kept the match reads as it always did.
import assert from "node:assert/strict";
import { test } from "node:test";
import { isUnverifiedPaperMeta, metaSourceInfo } from "../src/shared/lib/metaSource.js";

test("a confirmed registry record says what tied it to the PDF", () => {
  const says = (match) => metaSourceInfo({ source: "doi", match, unverified: false });
  assert.equal(says("ai").label, "doi.org · confirmed by AI");
  assert.equal(says("title").label, "doi.org · title page match");
  assert.equal(says("source").label, "doi.org · via its link");
  assert.equal(says("typed").label, "doi.org · from your identifier");
  assert.equal(says("page").label, "doi.org · via its web page");
  for (const match of ["ai", "title", "source", "typed", "page"]) {
    assert.equal(says(match).warn, false);
    assert.equal(says(match).short, "doi.org");  // the Settings table keeps the name alone
  }
  assert.match(says("ai").hint, /Confirmed by AI/);
});

test("an unconfirmed record warns, and the hint says why", () => {
  const phrase = metaSourceInfo({ source: "doi", match: "phrase", unverified: true });
  assert.equal(phrase.label, "doi.org — unconfirmed");
  assert.equal(phrase.short, "doi.org ?");
  assert.equal(phrase.warn, true);
  assert.match(phrase.hint, /searching for the PDF's title/);
  const idOnly = metaSourceInfo({ source: "doi", match: "id_only", unverified: true });
  assert.equal(idOnly.warn, true);
  assert.match(idOnly.hint, /identifier printed in the PDF/);
  const titleOnly = metaSourceInfo({ source: "doi", match: "title_only", unverified: true });
  assert.equal(titleOnly.warn, true);
  assert.match(titleOnly.hint, /no author of it was found/);
});

test("a record from before the match was kept reads as before", () => {
  const old = metaSourceInfo({ source: "doi", unverified: false });
  assert.equal(old.label, "doi.org");
  assert.equal(old.warn, false);
  assert.match(old.hint, /was not recorded/);
  // An import was never looked up: its hint says where it came from.
  assert.equal(metaSourceInfo({ source: "zotero" }).label, "Zotero");
  assert.equal(metaSourceInfo({ source: "zotero" }).hint, "Imported from Zotero");
  assert.equal(metaSourceInfo({ source: "doi", unverified: true }).warn, true);
  // The flag from before the flag: an AI record of a checkable kind.
  assert.equal(isUnverifiedPaperMeta("ai", "journalArticle"), true);
  assert.equal(isUnverifiedPaperMeta("ai", "document"), false);
  assert.equal(isUnverifiedPaperMeta("doi", "journalArticle"), false);
});

test("hand edits, the web page's record, AI readings and the user's check keep their words", () => {
  assert.equal(metaSourceInfo({ source: "manual" }).hint, "Fields edited by hand");
  assert.equal(metaSourceInfo({ source: "page", match: "page", unverified: false }).label, "web page tags");
  assert.equal(metaSourceInfo({ source: "ai", kind: "journalArticle", match: "ai_reading", unverified: true }).label,
    "AI-extracted — unverified");
  const vouched = metaSourceInfo({ source: "doi", match: "phrase", unverified: false, user_verified: true });
  assert.equal(vouched.label, "doi.org — verified by hand");
  assert.equal(vouched.warn, false);
  assert.equal(metaSourceInfo(null), null);
});
