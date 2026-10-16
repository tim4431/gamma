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

// The index of the page that stands for the document's size: the widest of
// the pages sharing the most common width (to the point; on a tie, the wider
// width), or -1 when no width is known. Fit-width sizes to it, and the
// measure loop estimates the unmeasured pages from it. Page 1 alone is not
// enough: a scanned book's cover is often an image of another size (252 pt
// against 444 pt text pages), and fitting it put every text page 1.76× past
// the viewer's edge. The widest of the group, because widths that round
// alike still differ (alternating crop boxes, 444.235 and 444.239 pt) and a
// fraction of a pixel too wide is enough for a horizontal scrollbar.
export function typicalPageIndex(widths) {
  const groups = new Map(); // width to the point → { count, i: its widest page }
  widths.forEach((w, i) => {
    if (!(w > 0)) return;
    const key = Math.round(w), g = groups.get(key);
    if (!g) groups.set(key, { count: 1, i });
    else { g.count++; if (w > widths[g.i]) g.i = i; }
  });
  let best = null, bestW = 0;
  for (const [w, g] of groups) {
    if (!best || g.count > best.count || (g.count === best.count && w > bestW)) { best = g; bestW = w; }
  }
  return best ? best.i : -1;
}
