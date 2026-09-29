// The papers a reply fetched, each shown under it (FetchHandoffCards.jsx):
// a row for a document its fetch_paper read, the card for one a publisher
// stopped — both with an "Add to library" button (FetchedPaper.jsx). Adding
// goes through POST /api/clip, the ingest Gamma Connector's Save uses:
// dedup by identifier, resolve and store the PDF, file the page, look its
// metadata up. Pure, so node tests it.

const DOI_RE = /\b(10\.\d{4,9}\/[-a-z0-9._;()/:+]+)/i;
const ARXIV_RE = /(?:arxiv\.org\/(?:abs|pdf|html)\/|\barxiv:\s*|^)(\d{4}\.\d{4,5}|[a-z][a-z-]*(?:\.[a-z]{2})?\/\d{7})(?:v\d+)?(?!\d)/i;

// A DOI with what follows it in a URL path or a sentence trimmed off.
function cleanDoi(raw) {
  let doi = raw.replace(/[.,;:]+$/, "");
  while (doi.endsWith(")") && (doi.match(/\)/g) || []).length > (doi.match(/\(/g) || []).length) {
    doi = doi.slice(0, -1).replace(/[.,;:]+$/, "");
  }
  return doi.replace(/\/(?:e?pdf|full|abstract|abs)$/i, "").replace(/\.pdf$/i, "").toLowerCase();
}

// The DOI and arXiv id a source names (a DOI or arXiv id in any spelling,
// or a URL carrying one); "" for each it does not.
export function paperIds(source) {
  let s = String(source || "").trim();
  try { s = decodeURIComponent(s); } catch {}
  const arxiv = s.match(ARXIV_RE);
  const doi = s.match(DOI_RE);
  return {
    doi: doi ? cleanDoi(doi[1]) : "",
    arxiv: arxiv ? arxiv[1].toLowerCase() : (doi && doi[1].match(/^10\.48550\/arxiv\.(.+)$/i)?.[1].toLowerCase()) || "",
  };
}

const isUrl = (s) => /^https?:\/\//i.test(s || "");

// A title worth showing: not a URL, a file name or a bare identifier.
export function goodTitle(title) {
  const s = String(title || "").trim();
  return !!s && !isUrl(s) && !/\.pdf$/i.test(s) && !/^(?:doi:\s*)?10\.\d{4,9}\//i.test(s) && !/^arxiv:/i.test(s);
}

// The paper a source names, for a handoff card's "Add to library".
export function paperOf(source, extra = {}) {
  const s = String(source || "").trim();
  const ids = paperIds(s);
  return { key: ids.arxiv ? `arxiv:${ids.arxiv}` : ids.doi ? `doi:${ids.doi}` : s, source: s, ...ids,
           url: isUrl(s) ? s : "", title: "", pdf: false, pdfUrl: "", pages: 0, request: "", ...extra };
}

// What a reply's fetch_paper calls got, in call order: a card per request a
// wall opened ({card: handoff}), and a row per document read ({paper}) —
// once each, however many windows of it the model read.
export function replyFetches(actions) {
  const out = [];
  const seen = new Set();
  for (const a of actions || []) {
    if (a?.kind !== "fetch" || a.tool !== "fetch_paper") continue;
    if (a.handoff?.id) {
      if (!seen.has(`card:${a.handoff.id}`)) out.push({ card: a.handoff });
      seen.add(`card:${a.handoff.id}`);
      continue;
    }
    if (a.error || !a.url) continue;
    const source = String(a.args?.source || "").trim() || a.url;
    const read = paperIds(a.url);
    const title = goodTitle(a.title) ? a.title : "";
    const paper = paperOf(source, { title, pdf: !!a.pdf, pdfUrl: a.pdf ? a.url : "", pages: a.pages || 0,
                                   request: a.request || "" });
    if (!paper.doi) paper.doi = read.doi;
    if (!paper.arxiv) paper.arxiv = read.arxiv;
    if (paper.arxiv || paper.doi) paper.key = paper.arxiv ? `arxiv:${paper.arxiv}` : `doi:${paper.doi}`;
    if (seen.has(paper.key)) continue;
    seen.add(paper.key);
    out.push({ paper });
  }
  return out;
}

// The web page a saved paper keeps as its source (properties.web_url): its
// DOI or arXiv page, else the address the chat read it from.
export function paperPage(paper) {
  if (paper.arxiv) return `https://arxiv.org/abs/${paper.arxiv}`;
  if (paper.doi) return `https://doi.org/${paper.doi}`;
  return paper.url || (paper.pdf ? "" : paper.pdfUrl) || "";
}

// The body of POST /api/clip for one paper. `docId`: the PDF the user's
// browser delivered, already stored in the workspace.
export function clipPayload(paper, { folder = "", allowOa = true, saveCopy = true, fetchMetadata = true, docId = "" } = {}) {
  return {
    source_url: paperPage(paper),
    pdf_url: paper.pdfUrl || "",
    doi: paper.doi || "",
    arxiv_id: paper.arxiv || "",
    title: paper.title || "",
    folder: folder || "",
    labels: [],
    allow_oa: !!allowOa,
    save_copy: !!saveCopy,
    fetch_metadata: !!fetchMetadata,
    ...(docId ? { doc_id: docId } : {}),
  };
}

// The library lookup's query for a paper (GET /api/library/lookup).
export function lookupQuery(paper) {
  const q = new URLSearchParams();
  if (paper.doi) q.set("doi", paper.doi);
  if (paper.arxiv) q.set("arxiv_id", paper.arxiv);
  const url = paper.pdfUrl || paper.url;
  if (url) q.set("url", url);
  return q.toString();
}
