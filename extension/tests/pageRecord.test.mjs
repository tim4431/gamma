import assert from "node:assert/strict";
import { test } from "node:test";
import "../pageRecord.js";

const { gammaPageRecord: pageRecord } = globalThis;

// The meta tags of a page as `values(name)` answers them: names compared
// without regard to case, as the content script's selector does.
const tags = (pairs) => (name) => pairs.filter(([n]) => n.toLowerCase() === name.toLowerCase()).map(([, v]) => v);

test("a repository's thesis: its type, its university as publisher, names turned around", () => {
  const record = pageRecord(tags([
    ["citation_title", "  Trapped   ions in optical cavities "],
    ["citation_author", "Lovelace, Ada"], ["citation_author", "Lovelace, Ada"], ["citation_author", "Babbage, Charles"],
    ["citation_publication_date", "2019/05/03"],
    ["citation_dissertation_institution", "Example University"],
    ["DC.publisher", "Example University Library"],
  ]));
  assert.deepEqual(record, {
    title: "Trapped ions in optical cavities", authors: ["Ada Lovelace", "Charles Babbage"], year: "2019",
    venue: "", volume: "", pages: "", publisher: "Example University", isbn: "", kind: "thesis",
  });
});

test("a conference paper cites its proceedings, a journal article its journal and pages", () => {
  const paper = pageRecord(tags([
    ["citation_title", "Attention is all you need"], ["citation_authors", "Ashish Vaswani; Noam Shazeer"],
    ["citation_conference_title", "NeurIPS 2017"], ["citation_journal_title", "Advances in Neural Information Processing Systems"],
    ["citation_date", "2017"], ["citation_volume", "30"], ["citation_firstpage", "5998"], ["citation_lastpage", "6008"],
  ]));
  assert.equal(paper.kind, "conferencePaper");
  assert.equal(paper.venue, "Advances in Neural Information Processing Systems");
  assert.deepEqual(paper.authors, ["Ashish Vaswani", "Noam Shazeer"]);
  assert.equal(paper.pages, "5998-6008");
  const article = pageRecord(tags([
    ["citation_title", "A paper"], ["citation_author", "A. Author"], ["citation_journal_title", "Phys. Rev. A"],
    ["citation_firstpage", "052101"], ["citation_lastpage", "052101"], ["citation_online_date", "March 2008"],
  ]));
  assert.equal(article.kind, "journalArticle");
  assert.equal(article.pages, "052101");
  assert.equal(article.year, "2008");
});

test("without Highwire's title or any author there is no record", () => {
  // A blog post's Dublin Core is not a work to cite.
  assert.equal(pageRecord(tags([["dc.title", "My post"], ["dc.creator", "Me"]])), null);
  assert.equal(pageRecord(tags([["citation_title", "Untitled"], ["citation_author", " , "]])), null);
  // Dublin Core fills the authors Highwire left out.
  assert.deepEqual(pageRecord(tags([["citation_title", "Report 7"], ["dc.creator", "Org Name"],
    ["citation_technical_report_institution", "Lab"]])).kind, "report");
  assert.equal(pageRecord(tags([["citation_title", "Book"], ["citation_author", "A"], ["citation_isbn", "9780000000002"]])).kind,
    "book");
});
