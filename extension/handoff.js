// Fetches the chat hands to this browser (the server's gamma/fetch_handoff.py):
// a chat card asks the worker to open the publisher's page (bridge.js), or its
// "Open" goes to <server>/api/ai/handoffs/<id>/go on its way there, so the tab
// that loads that address is fetching for the chat. The worker binds the tab
// to the request and, on each page the tab finishes loading, tries the URLs
// below with the browser's own session; the first real PDF goes back to the
// chat. Pure rules, tested in tests/.

// The request id when `url` is a Gamma server's /go address, else "". Any
// host: the same server is often reached as localhost and 127.0.0.1, or
// through a proxy. The worker then asks its own server, which takes only a
// request of the signed-in account (a look-alike address gets a 404).
export function handoffIdFrom(url) {
  if (!url) return "";
  let u;
  try { u = new URL(url); } catch { return ""; }
  if (!/^https?:$/.test(u.protocol)) return "";
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
// another paper, whose PDF must not answer this request. A DOI only guessed
// from the page's text (kind "maybe": often a reference's) does not count.
export function sameWork(candidate, request) {
  const want = sourceIds(request?.source);
  const doi = (candidate?.kind === "maybe" ? "" : candidate?.doi || "").toLowerCase();
  const arxiv = (candidate?.arxiv_id || "").toLowerCase();
  if (want.doi && doi && want.doi !== doi) return false;
  if (want.arxiv && arxiv && want.arxiv !== arxiv) return false;
  return true;
}

// The site part of a URL's host (no leading www.).
export function siteOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; }
}

const sameSite = (a, b) => a && b && (a === b || a.endsWith("." + b) || b.endsWith("." + a));

// A sign-in page's address: a login / SSO path or host — the server's rule
// (gamma/ai_web.py _LOGIN_PATH_RE / _LOGIN_HOST_RE).
const SIGN_IN_PATH = /\/(?:(?:show)?login|log-in|logon|signin|sign-in|sso|ssostart|idp|shibboleth|wayf|saml2?|authorize|authorization|auth\/realms|cas\/login)(?:[/.?;]|$)/i;
const SIGN_IN_HOST = /^(?:login|idp|sso|signin|auth|shibboleth|wayf)\./i;
export function signInUrl(url) {
  try {
    const u = new URL(url);
    return SIGN_IN_PATH.test(u.pathname) || SIGN_IN_HOST.test(u.hostname);
  } catch { return false; }
}

// What to try downloading in the tab, in order: the tab itself when it shows
// a PDF (Chrome's viewer runs no content script — `viewer`), the PDF link the
// page advertises, its "View PDF"-style links on the same site, and the link
// the server found — only on a page of its site that names a paper, so a
// sign-in page (or another host's) costs no request.
export function harvestUrls(candidate, request, { tabUrl = "", viewer = false } = {}) {
  const out = [];
  const add = (u) => { if (u && /^https?:/i.test(u) && !out.includes(u)) out.push(u); };
  if (viewer || candidate?.is_pdf_tab) add(tabUrl);
  add(candidate?.pdf_url);
  for (const u of candidate?.pdf_links || []) if (sameSite(siteOf(u), siteOf(tabUrl))) add(u);
  const here = siteOf(tabUrl);
  const there = siteOf(request?.pdf_url);
  const paper = viewer || (candidate?.kind && candidate.kind !== "none");
  if (paper && sameSite(here, there)) add(request.pdf_url);
  return out;
}

export const MAX_OPENS = 3;

const bare = (url) => (url || "").split("#")[0];

// Of the URLs a harvest tried in vain, the one to open in the tab itself, as
// a click would: the browser then runs what a script's download cannot — a
// redirect page's script (ScienceDirect's pdfft), a redirect to another host
// (Silverchair's watermark server), a check that wants a real navigation —
// and the PDF it ends on is taken from Chrome's viewer. `tried` is each URL
// with where its download ended (`landed`, "" when unknown). One that ended
// on a sign-in page, or back on this very page, says the user has no access
// yet: opening it would only lead away from the page's sign-in buttons, so
// it waits (the card asks the user to sign in). `opened` counts the opens
// per URL; a URL opened before is opened again only when the tab has been
// `away` on another site since (a university sign-in, after which the site
// sends the user back to the article), and at most MAX_OPENS times, so a
// redirect loop ends. Never from the viewer itself.
export function nextToOpen(tried, { tabUrl = "", viewer = false, opened = {}, away = false } = {}) {
  if (viewer) return "";
  const urls = tried.filter(({ landed }) => !landed || (!signInUrl(landed) && bare(landed) !== bare(tabUrl)))
    .map(({ url }) => url);
  return urls.find((u) => !opened[u])
    || (away ? urls.find((u) => opened[u] < MAX_OPENS) : "")
    || "";
}

// Whether the downloads' ends say the user must sign in first.
export function needsSignIn(tried) {
  return tried.some(({ landed }) => landed && signInUrl(landed));
}

// A bot check or CAPTCHA page, by what the tab shows: a check host (the
// server's ai_web._CHECK_HOST_RE), a challenge's title, or what the page's
// content script found (detect.js `check`: a challenge widget on a page with
// little else).
const CHECK_HOST = /(?:^|\.)(?:perfdrive\.com|captcha-delivery\.com|hcaptcha\.com|challenges\.cloudflare\.com)$/i;
const CHECK_TITLE = /^\s*(?:just a moment|attention required|one more step|are you (?:a )?(?:robot|human)|verify(?:ing)? (?:that )?you are (?:a )?human|human verification|security check|bot (?:check|verification)|please verify|请稍候)/i;
export function checkPage({ url = "", title = "", check = false } = {}) {
  if (check) return true;
  let host = "";
  try { host = new URL(url).hostname; } catch {}
  return CHECK_HOST.test(host) || CHECK_TITLE.test(title || "");
}

// Out-of-sight fetches (the chat's "in the background" setting) load in a
// minimized window of their own, a few at a time; the rest wait their turn.
// A tab that waits for the user (these notes) does not hold a turn.
export const MAX_BACKGROUND = 3;
export const NEEDS_YOU = new Set(["signin", "refused", "looking", "check", "other"]);

// Whether every turn is taken, from the worker's bindings (tabId → binding).
export function backgroundBusy(bindings) {
  const working = new Set(Object.values(bindings || {})
    .filter((b) => b && b.background && !NEEDS_YOU.has(b.note || ""))
    .map((b) => b.id));
  return working.size >= MAX_BACKGROUND;
}

// What a tab out of sight needs from the user, as a sentence to notify
// with — "" when nothing is wanted (it is still working, or the user is
// looking at the tab anyway and can see for themselves).
export function needsYouMessage(binding, note, host) {
  if (!binding?.background || !NEEDS_YOU.has(note)) return "";
  const where = host || "a publisher";
  switch (note) {
    case "check": return `${where} is showing a bot check — open the tab to pass it.`;
    case "signin": return `${where} wants you to sign in before it hands over the PDF.`;
    case "other": return `The tab for ${where} shows another paper — open this one there.`;
    case "refused": return `${where} didn't hand over the PDF — open the tab and save it yourself.`;
    default: return `No PDF link on ${where} yet — open the tab and have a look.`;
  }
}
