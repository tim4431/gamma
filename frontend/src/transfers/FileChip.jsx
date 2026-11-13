// The file chip: a same-origin upload linked from a block —
// `[name](/api/uploads/<hash>.<ext>)`, what a dropped or pasted file becomes.
// Files are content, any type, any number per page (docs/dev/block_centric.md,
// "Files and documents"). Every chip looks the same. A file of any format but
// an executable can become a page: "Add to library" in its menu (a right-click
// or a held finger) — a PDF through `POST /blocks/by-doc/<hash>` (the page
// CARRIES the file: viewer, highlights, metadata; nothing is uploaded twice),
// a markdown file through `POST /pages/from-file` (a note page imported from
// it — a copy, the file stays), any other file through `POST /pages/by-file`
// (a page whose document it is: its viewer, notes, comments and chat;
// transfers/documentUploads.js promoteChip). Once the page exists the chip
// shows an "open page" button.
//
// The chip renders inside markdown, so it is inline: it must sit in a
// sentence ("see [data.csv](…) for the raw numbers") as well as alone on a
// line.

import React, { createContext, useContext, useEffect, useReducer, useState } from "react";
import { DownloadIcon, ExternalLinkIcon, FileIcon, PaperIcon, PlusIcon } from "../shared/ui/Icons";
import { ContextMenu, MenuItem } from "../shared/ui/Menus";
import { menuPress } from "../shared/ui/press.js";
import { API, apiJson, assetUrl } from "../shared/lib/utils";
import { saveLinkClick, saveUrl } from "../shared/lib/saveFile.js";
import { uploadAnyFile } from "../shared/lib/uploadParts.js";
import { xhrUpload } from "../shared/lib/xhrUpload";
import { t } from "../shared/i18n/i18n.js";
import { IMAGE_MEDIA_TYPES, formatOf, storedExtension } from "../documents/formats.js";

// What the page around the chip provides: navigation and promotion come from
// App (they need the page's folder and openBlock); a chip rendered with no
// provider (the chat, an AI ghost row) is download-only.
//   { readOnly, canOpen, openPage(pageId), promoteFile(hash, ext, name) → Promise }
export const FileChipContext = createContext(null);

// --- stored name → page lookup, batched per render ---------------------------
// Every chip asks "which page did this file become?"; the asks of one render
// go out as ONE request (`POST /pages/by-docs {files}`, keyed by the stored
// name `<hash>.<ext>`: the same bytes stored under two extensions are two
// files and two pages) and the answers are cached until the page changes
// (App calls forgetDocPages on open; rememberDocPage after a promotion).
const docPages = new Map();          // stored name → {id, title} | null (no page)
const listeners = new Set();
let pending = new Set();
let timer = null;

function notify() {
  listeners.forEach((fn) => fn());
}

export function forgetDocPages() {
  docPages.clear();
  notify();
}

export function rememberDocPage(name, page) {
  if (name) docPages.set(name, page || null);
  notify();
}

async function flushLookups() {
  timer = null;
  const names = Array.from(pending);
  pending = new Set();
  if (!names.length) return;
  try {
    const data = await apiJson(`${API}/pages/by-docs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ files: names }),
    });
    names.forEach((name) => docPages.set(name, data?.files?.[name] || null));
  } catch {
    names.forEach((name) => docPages.set(name, null));
  }
  notify();
}

function useDocPage(name, enabled) {
  const [, rerender] = useReducer((n) => n + 1, 0);
  useEffect(() => {
    if (!name || !enabled) return undefined;
    listeners.add(rerender);
    if (!docPages.has(name) && !pending.has(name)) {
      pending.add(name);
      if (!timer) timer = setTimeout(flushLookups, 40);
    }
    return () => listeners.delete(rerender);
  }, [name, enabled]);
  return name && enabled ? docPages.get(name) : undefined;
}

// --- the markdown side ---------------------------------------------------------
const UPLOAD_RE = /^\/api\/uploads\/([0-9a-f]+)\.([a-z0-9]+)(?:[?#].*)?$/i;

// {hash, ext} of an upload URL, or null.
export function parseUploadUrl(href) {
  const m = UPLOAD_RE.exec(href || "");
  return m ? { hash: m[1], ext: m[2].toLowerCase() } : null;
}

// The chip's markdown for an upload-file reply ({url, name}).
function fileBlockMarkdown(up) {
  return `[${(up.name || "file").replace(/[\[\]]/g, "")}](${up.url})`;
}

// Where uploads report themselves: App installs {start(file) → id,
// progress(id, loaded, total), done(id, ok, detail)} so every upload made
// from a drop or paste shows in the background-tasks list and the status
// pill (with a percentage while the bytes go up). Without one (tests, the
// share view) uploads are silent.
let reporter = null;
export function setUploadReporter(r) { reporter = r; }

// Multipart POST of one file → the JSON reply, or null on refusal/failure
// (callers treat null as "nothing inserted"), reported as a background-tasks
// row (shared/lib/xhrUpload.js does the request). Shared with the image upload.
export function postFile(endpoint, file) {
  let abort = null;
  const id = reporter?.start(file, () => abort?.()); // the tasks popover's stop button
  const form = new FormData();
  form.append("file", file);
  return xhrUpload(endpoint, form, {
    onProgress: (loaded, total) => reporter?.progress(id, loaded, total),
    onAbortable: (fn) => { abort = fn; },
  }).then((data) => { reporter?.done(id, true, ""); return data; },
    (err) => { reporter?.done(id, false, String(err?.message || err)); return null; });
}

// Any file as `POST /api/upload-file` stores it, in parts past PART_BYTES
// (shared/lib/uploadParts.js uploadAnyFile) → the JSON reply, or null on
// refusal/failure, reported as `postFile` reports.
function postAnyFile(file) {
  let abort = null;
  const id = reporter?.start(file, () => abort?.());
  return uploadAnyFile(file, {
    onProgress: (loaded, total) => reporter?.progress(id, loaded, total),
    onAbortable: (fn) => { abort = fn; },
  }).then((data) => { reporter?.done(id, true, ""); return data; },
    (err) => { reporter?.done(id, false, String(err?.message || err)); return null; });
}

// {url, name} | null: any file a block takes as a chip (drop on a row, drop
// on the page).
async function uploadOtherFile(file) {
  const data = file ? await postAnyFile(file) : null;
  return data?.url ? { url: data.url, name: data.name || file.name } : null;
}

// The image types `/upload-image` takes (documents/formats.js, the image
// rows' media types): a picture of one is inline; an image of any other type
// (TIFF, HEIC) is a file like any other.
const PICTURE_TYPES = new Set(Object.values(IMAGE_MEDIA_TYPES));

// Uploads every file and returns one markdown line per success: pictures
// inline (`![](url)`), everything else a file chip. Used by a drop on a block
// row and a drop on the page body.
export async function uploadFilesAsLines(files) {
  const lines = [];
  for (const file of files) {
    if (PICTURE_TYPES.has(file.type || "")) {
      const url = (await postFile("/api/upload-image", file))?.url;
      if (url) lines.push(`![](${url})`);
    } else {
      const up = await uploadOtherFile(file);
      if (up) lines.push(fileBlockMarkdown(up));
    }
  }
  return lines;
}

// A human word for the file's kind (the tooltip), from its extension: finer
// than the registry's formats, and the tooltip's words alone — no decision
// reads it.
const KINDS = [
  [/^pdf$/, "PDF"],
  [/^(png|jpe?g|gif|webp|svg)$/, t("Image")],
  [/^(docx?|odt|rtf)$/, t("Document")],
  [/^(xlsx?|ods|csv)$/, t("Spreadsheet")],
  [/^(pptx?|odp|key)$/, t("Slides")],
  [/^(zip|gz|tgz|tar|7z|rar|whl|dmg|deb|rpm)$/, t("Archive")],
  [/^nb$/, t("Mathematica notebook")],
  [/^ipynb$/, t("Jupyter notebook")],
  [/^(py|m|jl|r|sh|js|ts|c|cpp|h|hpp|java|go|rs|cu)$/, t("Code")],
  [/^(md|txt|tex|bib|json|ya?ml|toml|xml|log)$/, t("Text")],
  [/^(h5|hdf5|mat|npy|npz|parquet|fits)$/, t("Data")],
  [/^(mp4|mov|mkv|webm)$/, t("Video")],
  [/^(mp3|wav|flac|ogg)$/, t("Audio")],
];
function fileKindLabel(ext) {
  const e = (ext || "").toLowerCase();
  const hit = KINDS.find(([re]) => re.test(e));
  return hit ? hit[1] : e ? `${e.toUpperCase()} file` : t("File");
}

const stop = (e) => e.stopPropagation();

// Every file looks the same: icon, name, a download arrow. A file that
// already has a page additionally shows a small "open page" button before the
// arrow; making that page ("Add to library") lives in the chip's menu (a
// right-click or a held finger), so a chip never advertises it inline. Every
// format can become a page (a markdown file a note, any other a document) but
// an executable's, which the server never stored (storedExtension null).
export function FileChip({ href, text }) {
  const ctx = useContext(FileChipContext);
  const parsed = parseUploadUrl(href);
  const ext = parsed?.ext || "";
  const { format } = formatOf(`x.${ext}`);
  const isPdf = format === "pdf";
  const name = (text || "").trim() || decodeURIComponent((href.split("/").pop() || "file").split("?")[0]);
  const [menu, setMenu] = useState(null);
  const canOpen = !!ctx?.canOpen && !!parsed && storedExtension(`x.${ext}`) !== null;
  const page = useDocPage(parsed ? `${parsed.hash}.${ext}` : "", canOpen);
  const url = assetUrl(href);
  const close = () => setMenu(null);
  const openPage = () => ctx.openPage(page.id);
  const makePage = () => ctx.promoteFile(parsed.hash, ext, name);

  let pageItem = null;
  if (canOpen && page) {
    pageItem = (
      <MenuItem icon={PaperIcon} title={t("Open \"{name}\"", { name: page.title || name })} onClick={() => { close(); openPage(); }}>{t("Open page")}</MenuItem>
    );
  } else if (canOpen && page === null && !ctx.readOnly) {
    pageItem = (
      <MenuItem icon={PlusIcon}
        title={isPdf
          ? t("Make a page for this PDF: the viewer, highlights, chat and metadata — the file is not uploaded again")
          : format === "markdown"
            ? t("Import this markdown as a note page — a copy; the file stays as it is")
            : t("Make a page for this file: its viewer, notes, comments and chat")}
        onClick={() => { close(); makePage(); }}>{t("Add to library")}</MenuItem>
    );
  }
  return (
    <span
      className="fileChip"
      title={t("{ext} — {name}", { ext: fileKindLabel(ext), name: name })}
      onMouseDown={stop}
      onClick={stop}
      {...menuPress((e) => { e.preventDefault(); e.stopPropagation(); setMenu({ x: e.clientX, y: e.clientY }); })}
    >
      <a className="fileChipMain" href={url} target="_blank" rel="noreferrer">
        <span className={`fileChipIcon fileChipIcon-${isPdf ? "pdf" : "file"}`}>
          {isPdf ? <PaperIcon size={14} /> : <FileIcon size={14} />}
        </span>
        <span className="linkChipText">{name}</span>
      </a>
      {canOpen && page ? (
        <button type="button" className="fileChipBtn fileChipOpen" title={t("Open the page \"{name}\"", { name: page.title || name })} aria-label={t("Open page")}
          onClick={openPage}>
          <ExternalLinkIcon size={14} />
        </button>
      ) : null}
      {/* The link downloads in a browser; an app that takes saves gets the file instead (saveFile.js). */}
      <a className="fileChipBtn fileChipDownload" href={url} download={name} title={t("Download")} aria-label={t("Download")}
        onClick={saveLinkClick(url, name)}>
        <DownloadIcon size={14} />
      </a>
      {menu ? (
        <ContextMenu x={menu.x} y={menu.y} onClose={close}>
          {pageItem}
          <MenuItem icon={DownloadIcon} onClick={() => { close(); saveUrl(url, name).catch(() => {}); }}>{t("Download")}</MenuItem>
        </ContextMenu>
      ) : null}
    </span>
  );
}
