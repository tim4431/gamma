// What a file becomes on its way into the library, and what a dropped
// directory leaves out: the pure rules of transfers/documentUploads.js, a
// module of their own so the node test (tests/documentUploads.test.mjs)
// imports them without the browser (documentUploads.js reaches the server
// through shared/lib/utils.js, which wraps window.fetch as it loads).
import { formatOf, storedExtension } from "../documents/formats.js";

// A directory drop past this many files asks first (App's confirm): the
// hosted page cap counts every page made.
export const ASK_PAST_FILES = 100;

// The names an editor or a system leaves beside the files: Office's lock,
// Emacs's lock and backup, a .tmp and Vim's swap file.
const TEMP_NAME = /^(?:~\$|\.#)|~$|\.(?:tmp|swp)$/i;

// An incoming file is a PDF, by its type or its name (documents/formats.js).
export const isPdfFile = (file) => file?.type === "application/pdf" || formatOf(file?.name || "").format === "pdf";
const isMarkdown = (file) => formatOf(file?.name || "").format === "markdown"
  || /^text\/(?:x-)?markdown$/i.test(file?.type || "");

// What a file becomes: "pdf" (the PDF ingest), "markdown" (a note through the
// Markdown importer), "file" (a document of any other format), "" for one the
// server refuses (a blocked extension: storedExtension null).
export function routeFile(file) {
  if (isPdfFile(file)) return "pdf";
  if (isMarkdown(file)) return "markdown";
  return storedExtension(file?.name || "") === null ? "" : "file";
}

// The path of an upload item as the drop or the picker gave it: a {file,
// folder} pair (the dropped-folder walk, `folder` the directory names) or a
// File with `webkitRelativePath` (the directory picker), else its name alone.
function segmentsOf(item) {
  const file = item?.file && !(typeof File !== "undefined" && item instanceof File) ? item.file : item;
  const folder = Array.isArray(item?.folder) ? item.folder : (file?.webkitRelativePath || "").split("/").slice(0, -1);
  return [...folder, file?.name || ""].map((s) => String(s || "").trim());
}

// What a dropped directory leaves out: dot directories and dot files,
// node_modules, an editor's or the system's temporary names, and the
// extensions the server refuses. The drop's own rule, not a work folder's
// scan (gamma/vault/disk.py `listed`, `temporary`): that one lists a dot
// file and a .tmp, .swp, `~` or `.#` name, and leaves out a gamma-sync
// part file (`*.gamma-sync-part`), which a drop keeps. → {kept, skipped},
// the items as they came.
export function dropFilter(files) {
  const kept = [];
  const skipped = [];
  for (const item of Array.from(files || [])) {
    const segments = segmentsOf(item);
    const leaf = segments[segments.length - 1];
    const out = segments.some((s) => s.startsWith(".") || s === "node_modules")
      || TEMP_NAME.test(leaf) || storedExtension(leaf) === null;
    (out ? skipped : kept).push(item);
  }
  return { kept, skipped };
}
