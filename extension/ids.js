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
