// How the viewer gets a PDF's bytes (docs/dev/pdf_loading.md): pure decisions,
// no DOM and no network, so they are unit-tested (tests/pdfSource.test.mjs).
//
// A stored upload has a manifest (GET /api/pdf-info/<doc_id>: byte size, page
// count, every page's size) and is served by a FileResponse that answers
// range requests. Small files are still fetched whole in one GET — the
// browser's HTTP cache and the IndexedDB copy then make every later open
// free. Large files are opened by pdf.js range requests instead, so the first
// page is on screen after a few hundred KB rather than after the whole file;
// a whole copy is fetched into IndexedDB afterwards (browsers never store 206
// responses), so the second open is warm too. The /api/pdf proxy streams from
// its upstream and cannot answer ranges: it always downloads whole.

// Above this an uncached upload is opened by range requests.
export const WHOLE_MAX_BYTES = 12 * 1024 * 1024;
// pdf.js range chunk. 256 KB: a page's objects usually sit within one or two.
export const RANGE_CHUNK = 256 * 1024;
// A range open leaves nothing on disk; this long after the page painted —
// late enough not to compete with the ranges — the whole file is fetched
// once, quietly, for IndexedDB.
export const BACKFILL_DELAY_MS = 3000;

// The doc id of an upload URL (`/api/uploads/<hex>.pdf`, any query), else null.
export function docIdOf(url) {
  const m = /^\/api\/uploads\/([0-9a-f]+)\.pdf(?:[?#]|$)/.exec(url || "");
  return m ? m[1] : null;
}

// The address a proxy URL reads (`/api/pdf?source_url=<address>`, any other
// query: save=1, share=, annots=), else null.
export function proxiedSourceOf(url) {
  const m = /^\/api\/pdf\?([^#]*)/.exec(url || "");
  return m ? new URLSearchParams(m[1]).get("source_url") : null;
}

// "memory" (bytes already cached), "whole" (one GET) or "range".
export function chooseTransport({ url, bytes, cached = false }) {
  if (cached) return "memory";
  if (!docIdOf(url)) return "whole";
  return bytes > WHOLE_MAX_BYTES ? "range" : "whole";
}

// pdf.js getDocument options for a range open. disableStream is the
// load-bearing one: with autofetch off but streaming on, pdf.js's full-file
// reader keeps running underneath and saturates the link the range requests
// are racing, while the log looks healthy.
export function rangeOpenOptions(url) {
  return {
    url,
    disableAutoFetch: true,
    disableRange: false,
    disableStream: true,
    rangeChunkSize: RANGE_CHUNK,
    withCredentials: true,
  };
}

// The page layout (scale-1 heights and widths) a manifest describes, or null
// when it does not describe a readable document. Sizes that are missing or
// nonsense fall back so a damaged entry can never yield a zero-height page.
export function layoutFromManifest(m, fallback = { width: 600, height: 800 }) {
  if (!m || !Number.isInteger(m.pages) || m.pages <= 0) return null;
  if (!Array.isArray(m.dims) || m.dims.length !== m.pages) return null;
  const heights = [], widths = [];
  for (const d of m.dims) {
    const w = Number(d?.[0]), h = Number(d?.[1]);
    widths.push(w > 0 ? w : fallback.width);
    heights.push(h > 0 ? h : fallback.height);
  }
  return { heights, widths };
}

// How much wider than the most common width a page may be and still be of
// the document's size: a scan's pages differ by a few points, and a document
// merged from A4 and Letter pages by 2.8 %. A cover or a landscape page
// differs by far more.
const NEAR_WIDTH = 0.05;

// The index of the page that stands for the document's size: the widest
// page at most NEAR_WIDTH wider than the most common width (to the point;
// on a tie, the wider width), or -1 when no width is known. Fit-width sizes
// to it, and the measure loop estimates the unmeasured pages from it. Page 1
// alone is not enough: a scanned book's cover is often an image of another
// size (252 pt against 444 pt text pages), and fitting it put every text
// page 1.76× past the viewer's edge. The widest near it, because widths
// differ even when they round alike (alternating crop boxes, 444.235 and
// 444.239 pt), and a page even a pixel wider than the viewer lets the view
// slide sideways at fit-width (615 pt pages among 612 pt ones, 4 px).
export function typicalPageIndex(widths) {
  const counts = new Map(); // width to the point → its pages
  for (const w of widths) if (w > 0) counts.set(Math.round(w), (counts.get(Math.round(w)) || 0) + 1);
  let common = 0, most = 0;
  for (const [w, n] of counts) if (n > most || (n === most && w > common)) { common = w; most = n; }
  let best = -1;
  widths.forEach((w, i) => {
    if (w > 0 && w <= common * (1 + NEAR_WIDTH) && (best < 0 || w > widths[best])) best = i;
  });
  return best;
}
