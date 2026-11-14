// A file on its way to being a page's document (docs/dev/documents.md
// "Writing a document"): how the library's drop and Upload files…, the
// paperclip and a file chip's Add to library decide what a file becomes and
// bring it to the server. A PDF and a Markdown file keep their own ways (the
// PDF ingest with its embedded annotations and viewer address, the Markdown
// importer); every other file is uploaded as any file (uploadAnyFile, in parts
// past PART_BYTES) and then named to the server by its stored name, which
// settles the format: `POST /pages/by-file` for a page of its own, `POST` or
// `PUT /pages/{id}/attachment {file}` for the open page. App owns the words,
// the tasks rows and the opening of the page; this module is the routing (the
// pure rules in fileRouting.js, node-tested) and the requests.
import { API, apiJson } from "../shared/lib/utils";
import { uploadAnyFile } from "../shared/lib/uploadParts.js";
import { formatOf } from "../documents/formats.js";
import { parseUploadUrl, storedName, uploadUrl } from "../documents/pageDocument.js";

export { ASK_PAST_FILES, dropFilter, routeFile, uploadName } from "./fileRouting.js";

const post = (path, body, method = "POST") => apiJson(`${API}${path}`, {
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

// The stored name of an upload-file answer ({url}).
function storedOf(up) {
  const parsed = parseUploadUrl(up?.url);
  if (!parsed) throw new Error("the upload named no stored file");
  return storedName(parsed.stem, parsed.ext);
}

// A file of any format as a library page: uploaded (uploadAnyFile: the xhr
// `options` carry onProgress, onAbortable and signal for the tasks row), then
// `POST /pages/by-file` with the shown `name` and `folderId` → {page, created}.
// The server settles the format and answers the page that owns the file when
// one does.
export async function uploadOneFile(file, { folderId = "", name = file.name, ...options } = {}) {
  const up = await uploadAnyFile(file, { name, ...options });
  return await post("/pages/by-file", { file: storedOf(up), name: name || up.name || "", folder: folderId || "" });
}

// A file of any format as the document of the page `pageId`: uploaded, then
// `POST` (attach) or `PUT` (`replace`) /pages/{id}/attachment {file,
// original_filename} → the updated block. A PDF goes the PDF way (App's
// attachPdfToPage): never through here.
export async function attachFile(pageId, file, { replace = false, name = file.name, ...options } = {}) {
  const up = await uploadAnyFile(file, { name, ...options });
  return await post(`/pages/${encodeURIComponent(pageId)}/attachment`,
    { file: storedOf(up), original_filename: name || up.name || "" }, replace ? "PUT" : "POST");
}

// "Add to library" on a file chip: `parsed` the chip's {hash, ext} (the
// extension with or without its dot), `name` its text, `folderId` the page's
// first library folder. A PDF through by-doc (the page carries the file
// already stored under its hash), a Markdown file through from-file (a note,
// a copy), anything else through by-file. → the page block.
export async function promoteChip(parsed, { name = "", folderId = "" } = {}) {
  const ext = parsed.ext.startsWith(".") ? parsed.ext : `.${parsed.ext}`;
  const folder = folderId || "";
  if (ext === ".pdf") {
    return await post(`/blocks/by-doc/${encodeURIComponent(parsed.hash)}`, {
      default_title: "", source_url: uploadUrl(storedName(parsed.hash, ext)), original_filename: name, folder,
    });
  }
  const file = storedName(parsed.hash, ext);
  if (formatOf(file).format === "markdown") {
    return (await post("/pages/from-file", { filename: file, original: name, folder })).page;
  }
  return (await post("/pages/by-file", { file, name, folder })).page;
}
