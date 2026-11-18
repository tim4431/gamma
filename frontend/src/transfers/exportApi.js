import { API, apiJson } from "../shared/lib/utils";
import { bibliographyPreviewUrl, bibliographyUrl } from "./transferFormats";

// The server calls an export makes besides starting its job: the export
// dialog's two, and the preview of a copy into another workspace (its pages
// exported and merged there, workspaceCopy.js). The URL building lives in
// transferFormats.js, which stays pure and testable; this module is only
// the fetching.

// What a copy of the pages (`pages`; null: every page) of the workspace
// `ws` would take, as the server reads it: {pages, zotero_pdfs}, the pages
// with the pages inside them and how many come without a PDF their Zotero
// item holds and the workspace has not brought yet.
export const copyPreview = ({ ws, pages = null }) => apiJson(`${API}/workspace-copy/preview`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ws, ...(pages ? { pages } : {}) }),
});

// The bibliography a BibTeX export would write, as data: an entry per
// citable page (title, citation key, whether it is pinned, the entry) plus
// the pages left out with the reason, and the file's own text. From the same
// builder as the download, so the review cannot disagree with the file.
// `subPages`: a page's with the pages inside it.
export function bibliographyPreview({ pageId = "", folder = "", subPages = false } = {}) {
  return apiJson(bibliographyPreviewUrl(API, { pageId, folder, subPages }));
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
