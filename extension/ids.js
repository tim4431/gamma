// Paper identifiers read off a URL or a meta tag. Loaded by the content script
// (classic script scope, before detect.js) and imported for its side effect by
// the module worker — hence globals, not exports.

// A DOI used as a URL path: doi.org/<doi>, Atypon/Wiley /doi/(abs|full|pdf)/<doi>,
// and publisher PDF paths built on it — APS /prl/pdf/<doi>, Springer
// /content/pdf/<doi>.pdf, IOP /article/<doi>/pdf. The view/file suffix is
// stripped with the same rule as the server's clip.norm_doi
// (_DOI_PATH_TAIL_RE), then trailing punctuation.
globalThis.gammaDoiFromPath = function doiFromPath(pathname) {
  let path = pathname || "";
  try { path = decodeURIComponent(path); } catch {}
  const m = path.match(/\/(10\.\d{4,9}\/.+)$/);
  if (!m) return "";
  return m[1]
    .replace(/(?:\/(?:e?pdf|full|abs(?:tract)?|meta|download))?(?:\.pdf)?$/i, "")
    .replace(/[.,;)\]]+$/, "");
};

// The first DOI in a string (a meta tag's value, an href, a run of text),
// URL-decoded, without the punctuation a sentence or a link puts after it:
// trailing . , ; : and a closing bracket the DOI did not open (DOIs may hold
// balanced ones: 10.1016/S0006-3495(00)76579-8). "" when there is none.
globalThis.gammaCleanDoi = function cleanDoi(text) {
  let s = String(text || "");
  try { s = decodeURIComponent(s); } catch {}  // a link may encode the slash: 10.1002%2Fanie…
  const m = s.match(/10\.\d{4,9}\/[^\s"'<>?#]+/);
  if (!m) return "";
  let doi = m[0];
  for (let before = ""; before !== doi;) {
    before = doi;
    doi = doi.replace(/[.,;:]+$/, "");
    for (const [open, close] of ["()", "[]", "{}"]) {
      if (doi.endsWith(close) && doi.split(open).length < doi.split(close).length) doi = doi.slice(0, -1);
    }
  }
  return doi;
};

// The distinct DOIs a page prints in its text or links to through doi.org,
// in that order, at most `max`, compared without regard to case: what the
// detector falls back on when the page's tags and address name no DOI. One
// is the paper the page is about (a news story, a page without tags);
// several are a reference list or a list of publications, which name none
// (detect.js). Zotero's DOI translator draws the same line, with a picker
// for the second case.
globalThis.gammaTextDois = function textDois(text, hrefs = [], max = 10) {
  const out = [];
  const seen = new Set();
  const add = (doi) => {
    if (doi && !seen.has(doi.toLowerCase()) && out.length < max) { seen.add(doi.toLowerCase()); out.push(doi); }
  };
  for (const m of String(text || "").matchAll(/10\.\d{4,9}\/[^\s"'<>]+/g)) add(globalThis.gammaCleanDoi(m[0]));
  for (const href of hrefs || []) {
    let link = String(href || "");
    try { link = decodeURIComponent(link); } catch {}
    const m = link.match(/^https?:\/\/(?:dx\.)?doi\.org\/(10\.\d{4,9}\/.+)$/i);
    if (m) add(globalThis.gammaCleanDoi(m[1]));
  }
  return out;
};

// The arXiv id, version stripped, in an arxiv.org /abs/, /pdf/ or /html/ URL,
// an "arXiv:<id>" string, or a bare id (citation_arxiv_id). New-style
// (2310.06825) and old-style (cond-mat/0402216, math.GT/0309136) ids — the
// server's ARXIV_ID (routers/pdf.py).
globalThis.gammaArxivId = function arxivId(text) {
  const id = String.raw`([0-9]{4}\.[0-9]{4,5}|[a-z][a-z.-]*\/[0-9]{7})`;
  const s = (text || "").trim();
  const m = s.match(new RegExp(String.raw`arxiv\.org\/(?:abs|pdf|html)\/${id}`, "i"))
    || s.match(new RegExp(String.raw`arxiv:\s*${id}`, "i"))
    || s.match(new RegExp(String.raw`^${id}(?:v\d+)?$`, "i"));
  return m ? m[1] : "";
};
