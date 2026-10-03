import { API, apiJson } from "../shared/lib/utils";
import { bibliographyPreviewUrl, bibliographyUrl } from "./transferFormats";

// The two server calls the export dialog makes besides starting the job. The
// URL building lives in transferFormats.js, which stays pure and testable;
// this module is only the fetching.

// The bibliography a BibTeX export would write, as data: an entry per
// citable page (title, citation key, whether it is pinned, the entry) plus
// the pages left out with the reason, and the file's own text. From the same
// builder as the download, so the review cannot disagree with the file.
export function bibliographyPreview({ pageId = "", folder = "" } = {}) {
  return apiJson(bibliographyPreviewUrl(API, { pageId, folder }));
}

// The share link of a page or a folder, or "" when it isn't shared. Only a
// link makes the fixed bibliography URL reachable, and nothing here creates
// one: publishing stays the share popover's decision, with its audience.
export async function shareToken({ pageId = "", folder = "" } = {}) {
  const url = folder
    ? `${API}/share-settings/folder/${encodeURIComponent(folder)}`
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
