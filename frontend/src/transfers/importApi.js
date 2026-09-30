import { API, apiJson } from "../shared/lib/utils";
import { xhrUpload } from "../shared/lib/xhrUpload";
import { t, T } from "../shared/i18n/i18n.js";

const FORMATS = {
  zotero: { label: T("Zotero"), endpoint: "zotero", instructions: T("Collections become folders. Additional PDFs become separate pages. Existing PDFs are kept.") },
  markdown: { label: T("Markdown"), endpoint: "markdown-zip", instructions: T("Notes become pages; linked files travel with the selected notes. Links to deselected notes stay as written.") },
  gamma: { label: T("Gamma"), endpoint: "gamma", instructions: T("Selected pages include their notes, attachments and chats. Pages already in your library are kept unchanged.") },
};
export function importFormat(source, file) {
  const format = FORMATS[source];
  if (!format) throw new Error("Unsupported import source");
  return source === "markdown" && !/\.zip$/i.test(file.name) ? { ...format, endpoint: "markdown-file" } : format;
}
// The dialog's source of a staged review's (an import job's params.source).
export function dialogSource(endpoint) {
  return Object.keys(FORMATS).find((source) => FORMATS[source].endpoint === endpoint)
    || (endpoint === "markdown-file" ? "markdown" : "zotero");
}
// `onProgress` gets {phase: "upload", loaded, total} while the bytes go up,
// then {phase: "processing"} while the server reads the archive.
export function requestImport({ source, file, folder = "", strip }, { signal, onProgress } = {}) {
  const format = importFormat(source, file);
  const body = new FormData();
  body.append("file", file);
  body.append("source", format.endpoint);
  if (source !== "gamma") body.append("folder", folder);
  if (source === "zotero") body.append("strip", String(Boolean(strip)));
  return xhrUpload(`${API}/import/review`, body, {
    signal,
    onProgress: (loaded, total) => onProgress?.({ phase: "upload", loaded, total }),
    onProcessing: () => onProgress?.({ phase: "processing" }),
  });
}

// The import itself is a background job: tasks.start("import", importJobBody(…)).
export const importJobBody = (reviewId, selected) => ({ review_id: reviewId, selected: [...selected] });
export function discardImport(reviewId) {
  if (reviewId) return apiJson(`${API}/import/review/${encodeURIComponent(reviewId)}`, { method: "DELETE" }).catch(() => {});
}
