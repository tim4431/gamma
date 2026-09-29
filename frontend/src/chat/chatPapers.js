// The papers a reply read or named, for its "Save to library" list
// (ReplyPapers.jsx): the documents its fetch_paper calls read, the ones a
// publisher stopped (the handoff cards — their PDF may have come from the
// user's browser since), and the DOI / arXiv links in its text (search
// results the model listed). Saving goes through POST /api/clip, the ingest
// Gamma Connector's Save uses: dedup by identifier, resolve and store the
// PDF, file the page, look its metadata up. Pure, so node tests it.

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

// One key per work however it was spelled.
function keyOf({ doi, arxiv, url, source }) {
  if (arxiv) return `arxiv:${arxiv}`;
  if (doi) return `doi:${doi}`;
  return url || source || "";
}

// A title worth showing: not a URL, a file name or a bare identifier.
function goodTitle(title) {
  const s = String(title || "").trim();
  return !!s && !isUrl(s) && !/\.pdf$/i.test(s) && !/^(?:doi:\s*)?10\.\d{4,9}\//i.test(s) && !/^arxiv:/i.test(s);
}

// Markdown links and bare identifiers in a reply's text (the chat renders
// bare DOIs / "arXiv:" ids as links too, shared/lib/remarkPaperLinks.js),
// with their labels. Links to anything but a paper are left out.
function linkedPapers(text) {
  const out = [];
  const body = String(text || "").replace(/```[\s\S]*?```/g, " ");
  for (const m of body.matchAll(/\[([^\]\n]{1,400})\]\((https?:\/\/[^)\s]+)\)/g)) {
    const ids = paperIds(m[2]);
    if (ids.doi || ids.arxiv) out.push({ ...ids, title: m[1].replace(/[*_`]/g, "").trim(), url: m[2] });
  }
  const bare = body.replace(/\[[^\]\n]*\]\([^)\s]*\)/g, " ").replace(/https?:\/\/\S+/g, " ");
  for (const m of bare.matchAll(/(?<![\w/])(?:10\.\d{4,9}\/[-a-z0-9._;()/:+]+|arxiv:\s*(?:\d{4}\.\d{4,5}|[a-z][a-z-]*(?:\.[a-z]{2})?\/\d{7})(?:v\d+)?)/gi)) {
    const ids = paperIds(m[0]);
    if (ids.doi || ids.arxiv) out.push({ ...ids, title: "", url: "" });
  }
  return out;
}

// The reply's papers, once each, in the order the reply met them: what its
// tool calls read (`read`, with the PDF address the server read it from
// when it was a PDF — `pdf`), what a publisher stopped (`blocked`, with the
// handoff request whose PDF the user's browser may have sent), then what its
// text links to. `request` names the handoff whose delivered PDF the save
// stores first (POST /api/ai/handoffs/<id>/store).
export function replyPapers(actions, text) {
  const byKey = new Map();
  const add = (paper) => {
    const key = keyOf(paper);
    if (!key) return;
    const have = byKey.get(key);
    if (!have) { byKey.set(key, { key, ...paper }); return; }
    for (const [k, v] of Object.entries(paper)) {
      if (k === "title") { if (!goodTitle(have.title) && goodTitle(v)) have.title = v; }
      else if (v && !have[k]) have[k] = v;
    }
    // Read wins over blocked: the PDF came after all.
    if (paper.read) have.blocked = false;
  };
  for (const a of actions || []) {
    if (a?.kind !== "fetch" || a.tool !== "fetch_paper") continue;
    const source = String(a.args?.source || a.handoff?.source || "").trim();
    const ids = paperIds(source);
    if (a.handoff?.id) {
      add({ ...ids, source, title: "", url: isUrl(source) ? source : "", blocked: true, read: false,
            pdf: false, pdfUrl: "", request: a.handoff.id, host: a.handoff.host || "" });
      continue;
    }
    if (a.error || !a.url) continue;
    const got = paperIds(a.url);
    add({ doi: ids.doi || got.doi, arxiv: ids.arxiv || got.arxiv, source, title: a.title || "",
          url: isUrl(source) ? source : "", read: true, pdf: !!a.pdf, pdfUrl: a.pdf ? a.url : "",
          request: a.request || "" });
  }
  for (const link of linkedPapers(text)) add({ ...link, source: link.arxiv ? `arXiv:${link.arxiv}` : `doi:${link.doi}` });
  return [...byKey.values()].map((p) => ({ ...p, title: goodTitle(p.title) ? p.title : "" }));
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
