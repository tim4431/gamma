import { API, apiJson } from "../shared/lib/utils";
import { uploadFile } from "../shared/lib/uploadParts";
import { t, T } from "../shared/i18n/i18n.js";
import { zipFolder } from "./folderZip.js";

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
// `file` is a File, or a picked folder (folderZip.js folderPick), packed into
// a .zip first. `onProgress` gets {phase: "packing", loaded, total} while a
// folder is read, {phase: "upload", loaded, total} while the bytes go up,
// then {phase: "processing"} while the server reads the archive.
export async function requestImport({ source, file, folder = "", strip }, { signal, onProgress } = {}) {
  const upload = file.folder
    ? await zipFolder(file, { signal, onProgress: (loaded, total) => onProgress?.({ phase: "packing", loaded, total }) })
    : file;
  const format = importFormat(source, upload);
  const fields = { source: format.endpoint };
  if (source !== "gamma") fields.folder = folder;
  if (source === "zotero") fields.strip = Boolean(strip);
  const options = {
    signal,
    onProgress: (loaded, total) => onProgress?.({ phase: "upload", loaded, total }),
    onProcessing: () => onProgress?.({ phase: "processing" }),
  };
  // A large file goes in parts (shared/lib/uploadParts.js); an unfinished
  // one is dropped where a review is.
  return uploadFile(upload, { url: `${API}/import/review`, parts: `${API}/import/review/parts`, fields,
    discard: (token) => `${API}/import/review/${encodeURIComponent(token)}` }, options);
}

// The import itself is a background job: tasks.start("import", importJobBody(…)).
export const importJobBody = (reviewId, selected) => ({ review_id: reviewId, selected: [...selected] });
export function discardImport(reviewId) {
  if (reviewId) return apiJson(`${API}/import/review/${encodeURIComponent(reviewId)}`, { method: "DELETE" }).catch(() => {});
}
