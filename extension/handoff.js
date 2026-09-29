// Fetches the chat hands to this browser (the server's gamma/fetch_handoff.py):
// a chat card's "Open" goes to <server>/api/ai/handoffs/<id>/go on its way to
// the publisher, so the tab that loads that address is fetching for the chat.
// The worker binds the tab to the request and, on each page the tab finishes
// loading, tries the URLs below with the browser's own session; the first real
// PDF goes back to the chat. Pure rules, tested in tests/.

// The request id when `url` is the /go address of `origin`'s server, else "".
export function handoffIdFrom(url, origin) {
  if (!url || !origin) return "";
  let u;
  try { u = new URL(url); } catch { return ""; }
  if (u.origin !== origin) return "";
  const m = u.pathname.match(/^\/api\/ai\/handoffs\/([A-Za-z0-9_-]{8,64})\/go$/);
  return m ? m[1] : "";
}

// The DOI / arXiv id a request's source names (a DOI in any spelling, an
// arXiv id or abs/pdf URL), lower-cased; "" for other URLs.
export function sourceIds(source) {
  const s = (source || "").trim();
  const doi = s.match(/^(?:https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*)?(10\.\d{4,9}\/\S+)$/i);
  const arxiv = s.match(/^(?:https?:\/\/arxiv\.org\/(?:abs|pdf)\/|arxiv:\s*)?(\d{4}\.\d{4,5}|[a-z][a-z.-]*\/\d{7})(?:v\d+)?$/i);
  return { doi: doi ? doi[1].toLowerCase() : "", arxiv: arxiv ? arxiv[1].toLowerCase() : "" };
}

// Whether the tab's paper can be the one the request is for: false only when
// both name a DOI (or arXiv id) and the two differ — the user wandered off to
// another paper, whose PDF must not answer this request.
export function sameWork(candidate, request) {
  const want = sourceIds(request?.source);
  const doi = (candidate?.doi || "").toLowerCase();
  const arxiv = (candidate?.arxiv_id || "").toLowerCase();
  if (want.doi && doi && want.doi !== doi) return false;
  if (want.arxiv && arxiv && want.arxiv !== arxiv) return false;
  return true;
}

const site = (url) => {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; }
};

// What to try downloading in the tab, in order: the tab itself when it shows
// a PDF (Chrome's viewer runs no content script — `viewer`), the PDF link the
// page advertises, and the link the server found — only on a page of its
// site that names a paper, so a sign-in page (or another host's) costs no
// request.
export function harvestUrls(candidate, request, { tabUrl = "", viewer = false } = {}) {
  const out = [];
  const add = (u) => { if (u && /^https?:/i.test(u) && !out.includes(u)) out.push(u); };
  if (viewer || candidate?.is_pdf_tab) add(tabUrl);
  add(candidate?.pdf_url);
  const here = site(tabUrl);
  const there = site(request?.pdf_url);
  const paper = viewer || (candidate?.kind && candidate.kind !== "none");
  if (paper && here && there && (here === there || here.endsWith("." + there) || there.endsWith("." + here))) {
    add(request.pdf_url);
  }
  return out;
}
