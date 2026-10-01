import { API, apiJson, withShare } from "../shared/lib/utils";
import { bibliographyUrl } from "./transferFormats";

// The two server calls the export dialog makes besides starting the job. The
// URL building itself lives in transferFormats.js, which stays pure and
// testable; this module is only the fetching.

// The bibliography of a page or a folder, as text, from the same endpoint the
// download uses — so the preview shows the exact entries and citation keys
// the file will carry, rendered once on the server rather than twice.
export async function bibliographyText({ pageId = "", folder = "" } = {}) {
  const r = await fetch(withShare(bibliographyUrl(API, { pageId, folder })), { credentials: "include" });
  if (!r.ok) {
    let detail = "";
    try { detail = (await r.json()).detail || ""; } catch { /* not JSON */ }
    throw new Error(detail || `HTTP ${r.status}`);
  }
  return r.text();
}

// The share link of a page or a folder, or "" when it isn't shared. Only a
// link makes the fixed bibliography URL reachable, and nothing here creates
// one: publishing stays the share popover's decision, with its audience.
export async function shareToken({ pageId = "", folder = "" } = {}) {
  const url = folder
    ? `${API}/share-settings/folder?name=${encodeURIComponent(folder)}`
    : `${API}/share-settings/${encodeURIComponent(pageId)}`;
  try {
    return (await apiJson(url))?.token || "";
  } catch {
    return "";  // a viewer who may not read the share settings simply gets no link
  }
}

// The absolute URL that always answers with the current .bib — what a LaTeX
// editor refreshes from. The token names the workspace, so the link needs
// neither a session nor a ?ws=.
export function sharedBibliographyUrl(token, target) {
  return token ? bibliographyUrl(`${window.location.origin}${API}`, { ...target, share: token }) : "";
}
