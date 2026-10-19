// The page's own citation record: the Highwire citation_* tags Google Scholar
// asks publishers and repositories for, read the way Zotero's Embedded
// Metadata translator reads them, Dublin Core and PRISM filling what they
// leave out. The clip keeps it on the page (the server's web_meta), where it
// is the record when no registry knows the work: a thesis in a repository, a
// report, a conference paper without a DOI. Classic script, loaded by the
// content script before detect.js, hence a global. `values(name)` answers
// every meta tag's content for that name, empty ones left out. Pure, tested
// in tests/.

globalThis.gammaPageRecord = function pageRecord(values) {
  const clean = (text) => String(text || "").replace(/\s+/g, " ").trim();
  const first = (...names) => {
    for (const name of names) {
      const value = clean(values(name).find((v) => clean(v)));
      if (value) return value;
    }
    return "";
  };
  // Highwire's title is what makes the page a paper's: a blog post may carry
  // Dublin Core, and a blog post is not a work to cite.
  const title = first("citation_title");
  if (!title) return null;
  let names = values("citation_author");
  if (!names.length) names = values("citation_authors").flatMap((v) => String(v).split(/\s*;\s*/));
  if (!names.length) names = values("dc.creator");
  const authors = [];
  for (const raw of names) {
    let name = clean(raw);
    // "Lovelace, Ada" → "Ada Lovelace"; a name with more commas is left as it is.
    const parts = name.split(",");
    if (parts.length === 2 && clean(parts[0]) && clean(parts[1])) name = `${clean(parts[1])} ${clean(parts[0])}`;
    if (/[^\s,.;-]/.test(name) && authors[authors.length - 1] !== name) authors.push(name);
    if (authors.length >= 100) break;
  }
  if (!authors.length) return null;

  const date = first("citation_publication_date", "citation_date", "citation_cover_date", "citation_online_date",
    "citation_year", "dc.date", "dcterms.issued", "prism.publicationdate");
  const thesisBy = first("citation_dissertation_institution");
  const reportBy = first("citation_technical_report_institution");
  const conference = first("citation_conference_title", "citation_conference");
  const book = first("citation_inbook_title", "citation_book_title");
  const journal = first("citation_journal_title", "prism.publicationname");
  const isbn = first("citation_isbn");
  // Embedded Metadata's type rules, the most specific tag first.
  const kind = thesisBy ? "thesis" : reportBy ? "report" : conference ? "conferencePaper"
    : book ? "bookSection" : journal ? "journalArticle" : isbn ? "book" : "";
  const firstPage = first("citation_firstpage", "prism.startingpage");
  const lastPage = first("citation_lastpage", "prism.endingpage");
  return {
    title, authors,
    year: (date.match(/\b(1[5-9]\d\d|20\d\d)\b/) || [])[1] || "",
    // A conference paper's venue is its proceedings when the page names them.
    venue: kind === "conferencePaper" ? journal || book || conference : book || journal,
    volume: first("citation_volume", "prism.volume"),
    pages: firstPage && lastPage && lastPage !== firstPage ? `${firstPage}-${lastPage}` : firstPage,
    publisher: thesisBy || reportBy || first("citation_publisher", "dc.publisher"),
    isbn, kind,
  };
};
