// The Zotero link dialog's counts (ZoteroLinkDialog.jsx), from GET
// /api/zotero/links/preview (gamma/zotero_sync.py preview): what the library
// holds, and, into an existing workspace, what pairs with its papers and
// what the first rounds write to Zotero. The server reads at most a bounded
// part of a large library: its answer's `partial` makes the PDF counts a
// lower bound ("at least"), and past that bound the match counts are null
// (the first round pairs everything all the same). A count the server or
// Zotero kept busy (429, 503) is asked again a few times before the dialog
// gives up calmly; linking never waits for it. Pure, so node tests it
// (tests/zoteroPreview.test.mjs).
import { fmtBytes } from "../shared/lib/format.js";
import { t, tn } from "../shared/i18n/i18n.js";

// Above this many PDFs the dialog suggests leaving them in Zotero until
// their page is opened.
export const MANY_PDFS = 1000;

// The waits before each new try of a busy count: a short backoff, then the
// dialog stops asking.
export const PREVIEW_RETRY_MS = [2000, 4000, 8000];

const num = (v) => Number(v || 0).toLocaleString();

// What the library holds, one line: its PDFs in Zotero Storage, their size,
// the linked files Gamma cannot download ("" before the counts).
export function libraryLine(p) {
  if (!p) return "";
  const n = p.pdfs || 0, linked = p.linked_files || 0, size = fmtBytes(p.pdf_bytes || 0);
  const parts = p.partial ? [
    tn("at least {n} PDF", "at least {n} PDFs", n, { n: num(n) }),
    t("at least {size} in Zotero Storage", { size }),
    linked ? tn("at least {n} linked file Gamma cannot download", "at least {n} linked files Gamma cannot download", linked, { n: num(linked) }) : "",
  ] : [
    tn("{n} PDF", "{n} PDFs", n, { n: num(n) }),
    t("{size} in Zotero Storage", { size }),
    linked ? tn("{n} linked file Gamma cannot download", "{n} linked files Gamma cannot download", linked, { n: num(linked) }) : "",
  ];
  return parts.filter(Boolean).join(" · ");
}

// Into an existing workspace, as the dialog stands (`mode`, `upload`: Send
// PDFs to Zotero; `notes`: false when the key cannot read notes): the
// papers that match and those added to Zotero, then what else the first
// rounds send there whatever side wins where a page differs (folders Zotero
// has no collection for, labels the matching items lack as tags, pages'
// own notes, highlights Zotero has no annotation for: `highlights_to_send`
// on papers whose PDF Zotero has, and, with uploads on, `highlights_new` on
// papers whose PDF this link sends, which go once it does; absent counts
// as none). A receive-only link sends nothing. [] before the counts.
export function intoLines(p, { mode = "two-way", upload = false, notes = true } = {}) {
  if (!p) return [];
  if (p.matches == null) {
    return [t("This library is too large to compare with the workspace in advance; the first sync matches the papers all the same.")];
  }
  const sends = mode !== "pull";
  const match = tn("{n} paper matches", "{n} papers match", p.matches, { n: num(p.matches) });
  const send = !sends || !p.to_send ? ""
    : !p.to_send_bytes ? t("{n} will be added to Zotero", { n: num(p.to_send) })
    : upload ? t("{n} will be added to Zotero ({size})", { n: num(p.to_send), size: fmtBytes(p.to_send_bytes) })
    : tn("{n} will be added to Zotero, without its PDF", "{n} will be added to Zotero, without their PDFs", p.to_send, { n: num(p.to_send) });
  const lines = [[match, send].filter(Boolean).join(t(", "))];
  if (!sends) return lines;
  const count = (v) => Number(v) || 0;
  const highlights = count(p.highlights_to_send) + (upload ? count(p.highlights_new) : 0);
  const also = [
    count(p.collections_to_add) ? tn("{n} folder as a collection", "{n} folders as collections", count(p.collections_to_add), { n: num(p.collections_to_add) }) : "",
    count(p.tags_to_add) ? tn("{n} tag from your labels on matching papers", "{n} tags from your labels on matching papers", count(p.tags_to_add), { n: num(p.tags_to_add) }) : "",
    notes && count(p.notes_to_send) ? tn("{n} page's notes as a Zotero note", "{n} pages' notes as Zotero notes", count(p.notes_to_send), { n: num(p.notes_to_send) }) : "",
    highlights ? tn("{n} highlight as an annotation", "{n} highlights as annotations", highlights, { n: num(highlights) }) : "",
  ].filter(Boolean);
  if (also.length) lines.push(t("Also sent to Zotero: {parts}", { parts: also.join(" · ") }));
  return lines;
}

// Which PDFs come by default: as their pages open for a large library, one
// whose counts stopped short (`partial`: it may hold many more), and on the
// iPad's own server; else every one.
export const suggestedDownload = (p, { ipad = false } = {}) =>
  ((p && (p.partial || p.pdfs > MANY_PDFS)) || ipad ? "open" : "all");

// A count worth asking again: the account's last one still runs or too
// many were asked (429), or Zotero kept it waiting (503).
export const previewRetryable = (err) => err?.status === 429 || err?.status === 503;

const pause = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

// `ask()` (the preview request), again after each of `waits` while it is
// busy (previewRetryable): the counts, the last error once the tries are
// spent or for a refusal no wait mends, or null when the dialog moved on
// meanwhile (`alive()` false: another library, closed).
export async function askPreview(ask, { alive = () => true, sleep = pause, waits = PREVIEW_RETRY_MS } = {}) {
  for (let tried = 0; ; tried += 1) {
    try {
      return await ask();
    } catch (err) {
      if (!previewRetryable(err) || tried >= waits.length) throw err;
    }
    await sleep(waits[tried]);
    if (!alive()) return null;
  }
}

// What the dialog says once it gives up on the counts (beside Try again).
export const previewFailure = (err) => (previewRetryable(err)
  ? t("Zotero is busy, so this library could not be counted yet; you can link without the counts.")
  : t("Could not count what this library holds: {message}", { message: err?.message || String(err) }));
