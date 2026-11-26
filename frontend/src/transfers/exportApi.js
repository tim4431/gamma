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

// The bibliography an export would write in `format` (a citation file of
// citations/formats.js FILE_FORMATS; BibTeX by default), as data
// (routers/export.py GET /api/bibliography), passed on as the server
// answers it: {format, entries, skipped, text}. An entry per citable page:
// {page_id, title, key, pinned, text (its entry in `format`; for CSL JSON
// a one-item array), csl (its CSL JSON item, id = key, whatever the
// format)}; `skipped` the pages left out, with the reason; `text` the file
// as the export downloads it. From the same builder as the download, so
// the review cannot disagree with the file. `subPages`: a page's with the
// pages inside it.
export function bibliographyPreview({ pageId = "", folder = "", subPages = false, format = "bibtex" } = {}) {
  return apiJson(bibliographyPreviewUrl(API, { pageId, folder, subPages, format }));
}

// The same answer for a selection (POST /api/bibliography): the pages
// `pageIds` (at most 1000), their entries in that order, and as `text`
// those entries laid out as the file lays them out, without a .bib's head
// comment: what a copy of the selection pastes. The keys are made unique
// as the .bib export makes them: over these pages, or with `keyFolder`
// over that folder's papers, so a copy made inside a folder cites the keys
// that folder's .bib has.
export function bibliographyFor({ pageIds = [], keyFolder = null, format = "bibtex" } = {}) {
  return apiJson(`${API}/bibliography`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ page_ids: [...new Set(pageIds)], key_folder: keyFolder || null, format }),
  });
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
