// A page's document (docs/dev/documents.md): the one property
// `properties.doc` = {format, name, hash, ext, at, url?, size?, copy?}. This
// is the one read of what a block carries: every decision on it (layout,
// card glyph, gating, copy) reads pageDocument, never the raw properties.
// Whether a page is a paper is the library rule's (library.js), not its
// document's alone. The twin of gamma/documents/model.py (page_document,
// the predicates); tests/shared/document.json holds both to the same answers.

import { EXTENSION_RE, FORMATS, languageOf, strip } from "./formats.js";

const AT = ["store", "path", "url"];
// The extension a format's document has when none is given.
const FORMAT_EXT = { pdf: ".pdf" };
const FIELDS = ["format", "name", "hash", "ext", "at", "url", "size", "copy"];
// A stored file's stem (db.safe_doc_id).
const HASH_RE = /^[A-Za-z0-9_.-]{1,128}$/;
const UPLOAD_RE = /^\/api\/uploads\/([0-9A-Za-z_-]{1,128})(\.[a-z0-9]{1,12})$/;

// The name a document's bytes are stored under in the workspace's uploads.
export const storedName = (hash, ext) => `${hash}${ext}`;
// The address a stored file is served at.
export const uploadUrl = (name) => `/api/uploads/${name}`;
// {stem, ext} of a stored file's address, else null.
export function parseUploadUrl(url) {
  const m = UPLOAD_RE.exec(String(url || ""));
  return m ? { stem: m[1], ext: m[2] } : null;
}

// A name reduced to one display leaf (storage.display_filename).
function displayName(name) {
  const raw = strip(String(name || "").replaceAll("\x00", "")).replaceAll("\\", "/");
  return [...strip(raw.split("/").pop())].slice(0, 500).join("");
}

// The stored doc a writer's fields make, in canonical key order, or null
// when they are no valid document (documents.model.document's rules).
function canonical({ hash = "", url = "", name = "", at = null, format = "pdf", ext = null, size = null, copy = false }) {
  if (!FORMATS.includes(format)) return null;
  ext = ext || FORMAT_EXT[format];
  if (!ext || !EXTENSION_RE.test(ext) || (format === "pdf" && ext !== ".pdf")) return null;
  hash = strip(hash);
  url = strip(url);
  if (hash && (hash === "." || hash === ".." || !HASH_RE.test(hash))) return null;
  if (!hash && url) {
    const own = parseUploadUrl(url);
    if (own && own.ext === ext) {
      if (at === "path") return null;
      hash = own.stem;
      url = "";
      copy = true;
      at = "store";
    }
  }
  if (hash && url === uploadUrl(storedName(hash, ext))) {
    url = "";
    if (at === "url") at = "store";
  }
  at = at ?? (url ? "url" : "store");
  if (!AT.includes(at)) return null;
  if (at === "url" ? !url : !hash || url) return null;
  if (copy && at !== "store") return null;
  if (size !== null && !(Number.isInteger(size) && size >= 0)) return null;
  const doc = { format, name: displayName(name), hash, ext, at };
  if (url) doc.url = url;
  if (size !== null) doc.size = size;
  if (copy) doc.copy = true;
  return doc;
}

// A stored doc as a writer supplies it, checked (documents.model.
// check_document): only the eight fields, each of its type, rebuilt
// canonically. Null when it is refused.
function checkDocument(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  for (const key of Object.keys(value)) if (!FIELDS.includes(key)) return null;
  for (const key of ["format", "name", "hash", "ext", "at", "url"]) {
    if (key in value && typeof value[key] !== "string") return null;
  }
  if ("size" in value && value.size !== null && typeof value.size !== "number") return null;
  if ("copy" in value && typeof value.copy !== "boolean") return null;
  return canonical({ ...value, at: value.at ?? null, ext: value.ext || null, size: value.size ?? null, copy: !!value.copy });
}

// What a block carries: the view {format, language, name, hash, ext, size,
// at, url, file, copy}, or null for no document (or one that cannot be
// read). `url` is where it is fetched from (its address when it is read from
// one, else its stored file's); `file` the stored file's name ("" when it
// has no hash), whether or not the bytes are there.
export function pageDocument(block) {
  const doc = checkDocument(block?.properties?.doc);
  if (!doc) return null;
  const file = doc.hash ? storedName(doc.hash, doc.ext) : "";
  return {
    format: doc.format,
    language: doc.format === "text" ? languageOf(doc.name, doc.ext) : null,
    name: doc.name,
    hash: doc.hash,
    ext: doc.ext,
    size: doc.size ?? null,
    at: doc.at,
    url: doc.at === "url" ? doc.url : uploadUrl(file),
    file,
    copy: !!doc.copy,
  };
}

// The hash of the document a page owns: "" for none and for a copy, which
// shows a stored file it does not own. What the one-page-per-document
// lookups and the page's own records go by.
export const ownHash = (doc) => (doc && !doc.copy ? doc.hash : "");
// The stem of the stored file a page shows, a copy's included: what reads
// the bytes goes by it.
export const fileHash = (doc) => doc?.hash || "";
export const isPdf = (doc) => doc?.format === "pdf";
// The stem of the stored PDF a page shows (fileHash), "" when its document
// is none or no PDF: what reads a PDF's bytes goes by it.
export const pdfFileHash = (doc) => (isPdf(doc) ? fileHash(doc) : "");
// Read in place from a file on this computer (gamma/linked_files.py).
export const inPlace = (doc) => doc?.at === "path";
// The address a document is read from, when it is one ("" for a stored file
// or one read in place).
export const sourceAddress = (doc) => (doc?.at === "url" ? doc.url : "");

// The same-origin proxy's address for an external PDF. `save` caches a copy
// in uploads; `share` carries the read token in the public view.
export function pdfProxyUrl(sourceUrl, { save = false, share = "" } = {}) {
  return `/api/pdf?source_url=${encodeURIComponent(sourceUrl)}`
    + (save ? "&save=1" : "")
    + (share ? `&share=${encodeURIComponent(share)}` : "");
}

// Where the viewer loads a PDF from, given its address: a stored copy as it
// is, anything else through the proxy; a share visitor's requests carry the
// read token. "" for none.
export function pdfViewerUrl(src, { share = "" } = {}) {
  if (!src) return "";
  if (!src.startsWith("/api/")) return pdfProxyUrl(src, { share });
  return share ? `${src}${src.includes("?") ? "&" : "?"}share=${encodeURIComponent(share)}` : src;
}

// Where the viewer loads a page's PDF document from; "" for no PDF.
export const documentViewerUrl = (doc, options) => (isPdf(doc) ? pdfViewerUrl(doc.url, options) : "");

// The stored doc a duplicate of a page writes, from its view, canonical: a
// stored file is shown as a copy (the original keeps owning it), an address
// is kept without the original's URL hash (an upload's address making it a
// copy of that upload), and a file read in place is shown from its stored
// copy. Null for no document.
export function copyDocument(doc) {
  if (!doc) return null;
  const fields = { format: doc.format, name: doc.name, ext: doc.ext, size: doc.size ?? null };
  return doc.at === "url"
    ? canonical({ ...fields, url: doc.url, at: "url" })
    : canonical({ ...fields, hash: doc.hash, at: "store", copy: true });
}
