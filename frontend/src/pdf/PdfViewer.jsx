// The pdf.js-based viewer: lazy page rendering, highlights, link
// annotations, text search, and the selection popup. Extracted from
// App.jsx to keep the God component shrinking.
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
// The worker is the legacy build matching the engine below, bundled by Vite
// as a content-hashed asset (?url): always the installed pdfjs-dist version,
// and served immutable for a year like every other asset — a copy under
// public/ was revalidated on every page load, 1.3 MB each time, and that was
// most of a warm reopen.
import pdfWorkerUrl from "pdfjs-dist/legacy/build/pdf.worker.min.mjs?url";
import "pdfjs-dist/web/pdf_viewer.css";
import { createPortal } from "react-dom";
import { BookIcon, CheckIcon, ChevronRightIcon, CopyIcon, LanguagesIcon, LinkIcon, LocateIcon, MessageSquareIcon, OutlineIcon } from "../shared/ui/Icons";
import { MarkupLayers, NO_MARKS } from "../markup/MarkupLayers";
import { armedClasses, usePageTools } from "../markup/PageTools";
import { inkPenDown } from "../ink/InkLayer";
import { ZoomWindow } from "../ink/ZoomWindow.jsx";
import { revealDelta } from "../ink/zoomWindow.js";
import { KEEP_MARGIN, RENDER_MARGIN, cropPage, forgetPixels, installPageRaster, keepPixels, showsKept, takePixels } from "./pageRaster.js";
import { installVerticalScrollSnap } from "./verticalScrollSnap.js";
import { selectionParagraphs, textRuns } from "./pdfTranslate";
import * as translateState from "./translateState.js";
import { langOfKey, sameModel } from "./translateRecall.js";
import { TransLayer } from "./TransOverlay.jsx";
import { maskedBlocks, maskedRuns } from "./transBlock.js";
import { blockAt, blocksQuote, hasMenu, highlightOn, linksOff, marksOver, menuBlockAt, onBlocks, pageReading, paragraphMenu, sameHighlight, selectionBlocks, unitsOnPage } from "./transSelect.js";
import { createPageScale } from "./pageScale.js";
import { BACKFILL_DELAY_MS, chooseTransport, docIdOf, layoutFromManifest, rangeOpenOptions, typicalPageIndex } from "./pdfSource";
import { normalizeChars } from "../shared/lib/textnorm";
import { apiJson, copyText, withShare, withWorkspace } from "../shared/lib/utils";
import { ChatMarkdown, useCopied } from "../shared/ui/Widgets";
import { Chevron, Collapse, IconSwap } from "../shared/ui/MotionWidgets";
import { menuPress } from "../shared/ui/press.js";
import { MenuButton } from "../shared/ui/Menus";
import { lastPointer } from "../shared/lib/pointer.js";
import { composing, heldAlone, isHeldKey } from "../shared/lib/hotkeys.js";
import { PdfCitationOverlay } from "./PdfCitationOverlay";
import { citationRuns, runChars } from "./pdfCitation.js";
import { noteBadgeAnchor } from "./noteAnchor.js";
import { inTextLayer, nearRelease, tipAnchor, tipWaiter } from "./selectionTip.js";
import { beginPenSelect, penSelecting } from "./penSelect.js";
import { nativeHost, postToHost } from "../shared/lib/host.js";
import { commentOnlyHighlight } from "../comments/comments.js";
import { marginLeft as besideMargin, pageLeft } from "../comments/marginLayout.js";
import { CommentMarginContext, CommentMarginSlot, MarginStackContext, createMarginStack } from "../comments/marginSlot";
import { useRecentIn } from "../guide/recent.js";
import { darkPageColor, darkPageLine, lineColor, pickerColors } from "../shared/model/palette.js";
import { isLineStyle } from "../shared/model/markStyle.js";
import { TipMarks } from "./MarkTools.jsx";
import { closestPlace, placedToCheck } from "../shared/model/blockModel.js";
import { HighlightReview, useHighlightReview } from "./HighlightReview.jsx";
import { pastLastPage, reviewList, waitingForAccept } from "./highlightReview.js";
import { usePalette } from "../shared/ui/paletteContext.js";
import { placeFloating, useFloatingPosition } from "../shared/ui/placement.js";
import { t, tn } from "../shared/i18n/i18n.js";
import { ZOOM_MIN, clampZoom } from "../shared/model/zoom.js";
import { SCALE_VAR, installViewerZoom, scaledPx } from "../shared/lib/viewerZoom.js";
import { installSmoothWheel } from "../shared/lib/smoothWheel.js";
// The engine: pdf.js (the legacy build, not the default one: it ships the
// core-js polyfills the modern build assumes — Promise.withResolvers is
// Safari 17.4+, and pdf.js calls it the moment a loading task is created) and
// its worker. A chunk of its own (docs/dev/frontend-refactor.md, "Lazy
// boundaries"), so the library's first screen neither downloads nor parses
// it: App preloads it at startup when the address opens a page, else once
// the library is idle, and a document opening fetches it if neither has.
// Until it loads, `pdfjsLib` is null; everything below that reads it runs on
// an open document, so after the load.
let pdfjsLib = null;
// One worker for every document. pdf.js otherwise starts a fresh worker per
// getDocument — the 1.3 MB script fetched and compiled again per open — and
// a document's destroy() only tears down a worker pdf.js created itself, so
// a shared one outlives every DOC_CACHE eviction. Created with the engine,
// which is what starts its script downloading.
let PDF_WORKER = null;
let enginePending = null;
export function loadPdfEngine() {
  enginePending ||= import("pdfjs-dist/legacy/build/pdf.mjs").then((lib) => {
    // Bypass immutable responses cached with text/plain before the server
    // MIME fix. Keep this stable: Vite's content hash handles later upgrades.
    lib.GlobalWorkerOptions.workerSrc = `${pdfWorkerUrl}?mime=js`;
    try {
      PDF_WORKER = new lib.PDFWorker({ name: "gamma-pdf" });
      // Startup can fail before a document opens. getDocument still receives
      // the rejection and reports it through the load-status UI when needed.
      PDF_WORKER.promise.catch(() => {});
    } catch {}
    pdfjsLib = lib;
    return lib;
  }, (error) => {
    enginePending = null; // a failed fetch is retried by the next open
    throw error;
  });
  return enginePending;
}
export const preloadPdfEngine = () => { loadPdfEngine().catch(() => {}); };
// getDocument parameters every open shares.
const openParams = (params) => (PDF_WORKER ? { ...params, worker: PDF_WORKER } : params);

const EMPTY_MARKS = [];

// Page layout model shared by the placeholder styles and all scroll math:
// page boxes stack with a fixed gap, and unmeasured pages assume page 1's
// size (FALLBACK_* is the last resort before even that is known).
const PAGE_GAP = 8;
// How far past the view (each way, in viewport heights) a page keeps its
// text layer; pdf.js's own viewer keeps about ten pages.
const FAR_MARGIN = "400% 0px";
const FALLBACK_H = 800, FALLBACK_W = 600;
// A page's text layer is built once its scroller has sent no scroll event
// for this long.
const TEXT_REST_MS = 100;
// A paper shown from its kept translations seeds the page being read once the
// reader has stayed on it this long: a quick scroll past seeds nothing.
const SEED_REST_MS = 300;

// Calls `fn` once `el` has been still for `ms` (at once without an element);
// the returned function calls it off.
function afterScrollRest(el, ms, fn) {
  if (!el) { fn(); return () => {}; }
  let timer = 0;
  const off = () => { clearTimeout(timer); el.removeEventListener("scroll", arm); };
  const arm = () => { clearTimeout(timer); timer = setTimeout(() => { off(); fn(); }, ms); };
  el.addEventListener("scroll", arm, { passive: true });
  arm();
  return off;
}

// Whether the page box in `wrapRef` lies within `margin` (an
// IntersectionObserver rootMargin) of its scroller's viewport; `initial`
// until the observer first reports. The second value, `assume()`, counts
// the box within until the observer reports again, which it is asked to
// do afresh: for a page known to be in view before the observer can say
// so (one that came back showing its kept pixels).
function useWithin(wrapRef, margin, pageNumber, initial = false) {
  const [within, setWithin] = useState(initial);
  const obsRef = useRef(null);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const obs = new IntersectionObserver((entries) => {
      setWithin(entries[entries.length - 1].isIntersecting);
    }, { root: el.closest(".pdfViewer"), rootMargin: margin });
    obs.observe(el);
    obsRef.current = obs;
    return () => { obs.disconnect(); obsRef.current = null; };
  }, [pageNumber, margin]);
  const assume = useCallback(() => {
    setWithin(true);
    const obs = obsRef.current, el = wrapRef.current;
    if (obs && el) { obs.unobserve(el); obs.observe(el); }
  }, [wrapRef]);
  return [within, assume];
}

// Content-y of page idx's top edge at the given scale.
function pageTopAt(heights, idx, scale) {
  let y = 0;
  for (let i = 0; i < idx; i++) y += (heights[i] || FALLBACK_H) * scale + PAGE_GAP;
  return y;
}

// The page boxes the scroller shows from `top` when its pages, of natural
// `heights`, are laid out at `scale`. Worked out in the layout model above,
// not from the boxes' rectangles, so it answers for the layout the reader
// saw even in a commit that has already laid the boxes out at another zoom.
function pagesInView(scroller, heights, scale, top) {
  const out = [];
  if (!scroller) return out;
  const bottom = top + scroller.clientHeight;
  for (let i = 0, y = 0; i < heights.length && y < bottom; i++) {
    const h = (heights[i] || FALLBACK_H) * scale;
    const wrap = y + h > top && scroller.querySelector(`.pdfPageWrap[data-page="${i + 1}"]`);
    if (wrap) out.push(wrap);
    y += h + PAGE_GAP;
  }
  return out;
}

// The bytes of the document most recently downloaded, so a reopen while the
// IndexedDB copy below is still being written never re-downloads. One entry
// only: raw bytes are the cheap half of a reopen (re-reading them from
// IndexedDB takes tens of ms) and the memory belongs to DOC_CACHE, which
// holds the expensive half. pdf.js detaches the buffer it's handed, so
// entries are cloned on use.
const PDF_CACHE = new Map(); // url -> ArrayBuffer, insertion order = LRU
const PDF_CACHE_MAX = 1;
function cachePdf(url, buf) {
  PDF_CACHE.delete(url);
  PDF_CACHE.set(url, buf);
  while (PDF_CACHE.size > PDF_CACHE_MAX) PDF_CACHE.delete(PDF_CACHE.keys().next().value);
}

// Parsed documents, kept across tab switches and viewer unmounts. Rebuilding
// a PDFDocumentProxy from cached bytes — xref, page tree, fonts — is most of
// what a reopen costs, and a document handed over ready to render also holds
// the reading position still: its geometry is final in the first frame, so
// nothing settles under the scroll restore. Eviction destroys the oldest,
// never the one being committed (it is set last) nor one a translation job
// is reading (translateState.pinned): a job runs on when the viewer leaves
// its PDF and segments pages with the document, so it stays past
// DOC_CACHE_MAX until the last job reading it ends.
const DOC_CACHE = new Map(); // url -> {doc, heights, widths}, insertion order = LRU
const DOC_CACHE_MAX = 2;
// The documents mounted viewers are asked for, url -> how many: pages side
// by side (docs/dev/tab_groups.md) put a viewer in each group. Eviction
// passes them over, so DOC_CACHE_MAX bounds the documents no viewer shows.
// Letting one go only unpins it: the viewer still shows it until its next
// document is in, and the next document remembered trims the cache.
const DOC_SHOWN = new Map();
function showDoc(url) {
  DOC_SHOWN.set(url, (DOC_SHOWN.get(url) || 0) + 1);
  return () => {
    const n = (DOC_SHOWN.get(url) || 1) - 1;
    if (n > 0) DOC_SHOWN.set(url, n);
    else DOC_SHOWN.delete(url);
  };
}
// The in-document search's page texts, by parsed document (PdfViewer's
// search effect): freed with the document when DOC_CACHE lets it go.
const SEARCH_TEXT = new WeakMap();

// Whether two per-page slices of the comment anchors read the same
// (anchorsByPage), as sameHighlights.
function sameAnchors(a, b) {
  return a.length === b.length && a.every((x, i) => x.id === b[i].id && x.kind === b[i].kind
    && x.threadCount === b[i].threadCount && x.position === b[i].position);
}
// Whether two per-page highlight slices read the same (hlsByPage): the
// highlight objects are rebuilt on every edit, their positions are not.
function sameHighlights(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i], y = b[i];
    if (x.id !== y.id || x.color !== y.color || x.style !== y.style || x.hasNote !== y.hasNote || x.threadCount !== y.threadCount || x.position !== y.position
      || x.note !== y.note || x.quote !== y.quote
      || x.linkTarget?.url !== y.linkTarget?.url || x.linkTarget?.pageId !== y.linkTarget?.pageId
      || x.linkTarget?.blockId !== y.linkTarget?.blockId) return false;
  }
  return true;
}
function rememberDoc(url, entry) {
  DOC_CACHE.delete(url);
  DOC_CACHE.set(url, entry);
  trimDocs();
}
function trimDocs() {
  const newest = [...DOC_CACHE.keys()].at(-1);
  for (const [victim, { doc }] of DOC_CACHE) {
    if (DOC_CACHE.size <= DOC_CACHE_MAX) break;
    if (victim === newest || translateState.pinned(victim) || DOC_SHOWN.has(victim)) continue;
    DOC_CACHE.delete(victim);
    forgetPixels(doc);
    // Its pages may still be mounted mid-swap — destroying now would spam
    // transport-destroyed rejections; after the commit has settled is fine.
    setTimeout(() => doc.destroy().catch(() => {}), 1000);
  }
}

// Persistent second-level cache: survives refreshes, closed tabs, and browser
// restarts, so a paper is downloaded once per month per browser. IndexedDB on
// purpose, NOT the Cache Storage API: caches is undefined in insecure
// contexts, and this app is typically reached over plain http (LAN/Tailscale)
// — which silently disabled the old cache and re-downloaded "cached" papers
// after every reload. IndexedDB works everywhere. Content behind a URL never
// changes (upload names are content hashes; proxy-saved files are written
// once), so serving from disk is safe.
const DISK_CACHE_TTL_MS = 30 * 24 * 3600 * 1000; // one month
const DISK_CACHE_MAX = 30; // papers in the disk cache
// ... and at most this many bytes of them together: thirty scanned books
// would otherwise be gigabytes. The sizes live in a store of their own
// ("meta"), so the eviction walk never loads a buffer.
const DISK_CACHE_MAX_BYTES = 512 * 1024 * 1024;

function idbOpen() {
  return new Promise((resolve, reject) => {
    const rq = indexedDB.open("gamma-pdf-cache", 2);
    rq.onupgradeneeded = (e) => {
      const db = rq.result;
      // Version 1 kept the date beside the buffer; its entries are dropped
      // (a cache: the next open fetches again) rather than walked.
      if (e.oldVersion >= 1 && db.objectStoreNames.contains("pdfs")) db.deleteObjectStore("pdfs");
      db.createObjectStore("pdfs");
      db.createObjectStore("meta").createIndex("at", "at");
    };
    rq.onsuccess = () => resolve(rq.result);
    rq.onerror = () => reject(rq.error);
  });
}
function idbReq(rq) {
  return new Promise((resolve, reject) => {
    rq.onsuccess = () => resolve(rq.result);
    rq.onerror = () => reject(rq.error);
  });
}

async function diskCacheGet(url) {
  let db;
  try {
    db = await idbOpen();
    const meta = await idbReq(db.transaction("meta").objectStore("meta").get(url));
    if (!meta) return null;
    if (Date.now() - meta.at > DISK_CACHE_TTL_MS) {
      const tx = db.transaction(["pdfs", "meta"], "readwrite");
      tx.objectStore("pdfs").delete(url);
      tx.objectStore("meta").delete(url);
      return null;
    }
    return (await idbReq(db.transaction("pdfs").objectStore("pdfs").get(url))) || null;
  } catch {
    return null;
  } finally {
    db?.close();
  }
}

// `owned`: the caller keeps no use for `buf` (the backfill's download), so
// it is stored as it is; otherwise it is copied first, synchronously, before
// the caller hands it to pdf.js (which transfers it to the worker).
async function diskCachePut(url, buf, { owned = false } = {}) {
  let db;
  try {
    const copy = owned ? buf : buf.slice(0);
    db = await idbOpen();
    const tx = db.transaction(["pdfs", "meta"], "readwrite");
    const pdfs = tx.objectStore("pdfs"), meta = tx.objectStore("meta");
    pdfs.put(copy, url);
    await idbReq(meta.put({ at: Date.now(), bytes: copy.byteLength }, url));
    // Evict the oldest entries past the count and the byte caps. The "meta"
    // rows are small: the walk reads every date and size, never a buffer.
    const rows = await idbReq(meta.index("at").getAll());
    let count = rows.length, bytes = rows.reduce((n, r) => n + (r.bytes || 0), 0);
    if (count > DISK_CACHE_MAX || bytes > DISK_CACHE_MAX_BYTES) {
      const keys = await idbReq(meta.index("at").getAllKeys());
      for (let i = 0; i < keys.length && (count > DISK_CACHE_MAX || bytes > DISK_CACHE_MAX_BYTES); i++) {
        if (keys[i] === url) continue; // what was just stored stays
        pdfs.delete(keys[i]);
        meta.delete(keys[i]);
        count--;
        bytes -= rows[i].bytes || 0;
      }
    }
  } catch {} finally {
    db?.close();
  }
}

async function diskCacheHas(url) {
  let db;
  try {
    db = await idbOpen();
    return (await idbReq(db.transaction("meta").objectStore("meta").getKey(url))) !== undefined;
  } catch {
    return false;
  } finally {
    db?.close();
  }
}

// A range open leaves nothing on disk. BACKFILL_DELAY_MS after the reader has
// their page, the whole file is fetched once into IndexedDB so the next open
// is warm — otherwise every later open would pay the round trips again.
const backfilling = new Set();
function backfillLocalCopy(url) {
  if (backfilling.has(url)) return;
  backfilling.add(url);
  setTimeout(async () => {
    try {
      if (await diskCacheHas(url)) return;
      const resp = await fetch(withShare(url), { credentials: "include" });
      if (resp.ok) diskCachePut(url, await resp.arrayBuffer(), { owned: true });
    } catch {} finally {
      backfilling.delete(url);
    }
  }, BACKFILL_DELAY_MS);
}

// The document manifest (GET /api/pdf-info, docs/dev/pdf_loading.md): byte
// size, page count and every page's size, derived server-side once per
// stored PDF. Only uploads have one — the /api/pdf proxy has no local file to
// measure — and a missing or failed manifest just means the older path.
// ?ws= keeps the browser's HTTP cache per workspace.
async function fetchManifest(url) {
  const id = docIdOf(url);
  if (!id) return null;
  try {
    return await apiJson(withWorkspace(`/api/pdf-info/${id}`));
  } catch {
    return null;
  }
}

// The file's byte size from a HEAD (the upload route answers one with
// Content-Length), or null. Raced against the manifest for the transport
// decision: a manifest being computed for the first time (a long book,
// opened the moment it was uploaded) can take seconds, and the open must not
// wait on it — a HEAD is one round trip whatever the file.
async function fetchSize(url) {
  try {
    const r = await fetch(withShare(url), { method: "HEAD", credentials: "include" });
    const n = parseInt(r.headers.get("content-length") || "", 10);
    return r.ok && n > 0 ? n : null;
  } catch {
    return null;
  }
}

// The first non-null value among the promises, or null when none has one.
function firstValue(promises) {
  return new Promise((resolve) => {
    let pending = promises.length;
    for (const p of promises) {
      p.then((v) => { if (v != null) resolve(v); else if (--pending === 0) resolve(null); },
        () => { if (--pending === 0) resolve(null); });
    }
  });
}

// A promise's value, or null once `ms` have passed without it.
function withTimeout(promise, ms) {
  return Promise.race([promise, new Promise((resolve) => setTimeout(() => resolve(null), ms))]);
}

// Abort a download when no bytes arrive for this long — a hung server
// otherwise leaves the fetch (and the UI) waiting forever.
const STALL_MS = 45000;

// Drain a Response body chunk-by-chunk so byte progress can be reported
// while the download runs (arrayBuffer() only resolves at the very end).
async function readBody(resp, onChunk) {
  if (!resp.body?.getReader) {
    const buf = await resp.arrayBuffer();
    onChunk(buf.byteLength);
    return buf;
  }
  const reader = resp.body.getReader();
  const chunks = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    size += value.byteLength;
    onChunk(value.byteLength);
  }
  const buf = new Uint8Array(size);
  let off = 0;
  for (const c of chunks) { buf.set(c, off); off += c.byteLength; }
  return buf.buffer;
}

// Download a PDF (cache-first) and report progress phases to the host:
// start → progress (bytes) → done, or error {detail}. Returns the
// ArrayBuffer, or null when the load failed or was cancelled (both already
// reported via onLoadState).
async function fetchPdfData(url, onLoadState, isCancelled) {
  const cached = PDF_CACHE.get(url);
  if (cached) {
    // Settles a task row registered before the load (URL-box opens);
    // ordinary cache hits have no row and ignore this.
    onLoadState?.(url, { phase: "cached" });
    return cached;
  }
  const disk = await diskCacheGet(url);
  if (disk) {
    if (isCancelled()) return null;
    onLoadState?.(url, { phase: "cached" });
    return disk;
  }
  // Stall watchdog: abort when the connection goes silent — the proxy may sit
  // for a while before its upstream download produces the first byte, but a
  // connection with no bytes for STALL_MS is dead, not slow. The same
  // controller is the tasks popover's stop button (`cancel`).
  const ctrl = new AbortController();
  onLoadState?.(url, { phase: "start", cancel: () => ctrl.abort() });
  let loaded = 0, total = 0, lastByteAt = Date.now(), lastReport = 0, stalled = false;
  const watchdog = setInterval(() => {
    if (Date.now() - lastByteAt > STALL_MS) { stalled = true; ctrl.abort(); }
  }, 5000);
  const beat = (n) => {
    loaded += n;
    lastByteAt = Date.now();
    if (Date.now() - lastReport > 200) {
      lastReport = Date.now();
      onLoadState?.(url, { phase: "progress", loaded, total });
    }
  };
  try {
    // One plain GET. Upload URLs are content-addressed and served with an
    // immutable Cache-Control, so a normal request lets the browser HTTP cache
    // make repeat downloads free — even on plain http where Cache Storage is
    // unavailable. Files too large for this path open by range requests
    // instead (pdfSource.chooseTransport), through pdf.js's own transport.
    const resp = await fetch(url, { credentials: "include", signal: ctrl.signal });
    if (isCancelled()) return null;
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const j = JSON.parse(await resp.text());
        if (typeof j.detail === "string") detail = j.detail;
      } catch {}
      onLoadState?.(url, { phase: "error", detail });
      return null;
    }
    total = parseInt(resp.headers.get("content-length") || "0", 10) || 0;
    const data = await readBody(resp, beat);
    if (isCancelled()) return null;
    diskCachePut(url, data); // fire-and-forget; copies the buffer synchronously
    onLoadState?.(url, { phase: "done", bytes: data.byteLength });
    return data;
  } catch (e) {
    if (!isCancelled()) {
      onLoadState?.(url, {
        phase: "error",
        detail: stalled ? t("no data for {STALL_MS}s — server not responding", { STALL_MS: Math.round(STALL_MS / 1000) }) : (e.message || t("network error")),
      });
    }
    return null;
  } finally {
    clearInterval(watchdog);
  }
}

// A document opened with no viewer, for work that reads its text (a folder's
// translated export, pdf/folderTranslation.js): its bytes as the viewer gets
// them (the caches above, else one GET), parsed by the shared worker. It
// goes in no cache of documents; the caller destroys it.
export async function openPdfDocument(url) {
  const lib = await loadPdfEngine();
  const data = await fetchPdfData(url, null, () => false);
  if (!data) throw new Error("the PDF could not be read");
  return lib.getDocument(openParams({ data: data.slice(0), disableAutoFetch: true, disableRange: true })).promise;
}

// Handwriting, text boxes and the other page marks (markup/MarkupLayers.jsx):
// `marks` maps a page number to what its layers draw (App's useMarks); the
// tools and their handlers come from PageToolsContext (markup/PageTools.jsx).
// scrollRef.current({position, box?, offset?, behavior?}) scrolls to a
// highlight's position, or with `box` (a text box in the page's points) to
// that box on position.pageNumber.
// flashHighlightId: the highlight a jump just landed on, which pulses once
// (.pdfHlFlash; App's triggerFlash sets it for about a second).
// highlights: blocksToHighlights' (shared/model/blockModel.js), each with
// App's `threadCount` and `linkTarget` when it has them.
// onSelectionFinished(position, quote, hideTip, extras): a selection to be
// made a highlight (or a link, or the place of one waiting to be moved),
// `quote` its text ("" for an area); what it returns is the new
// highlight's id, when it made one.
// selComment: the selection popup offers Comment (the reader may comment on
// the page); a press reaches onSelectionFinished with {comment: true}.
// highlighter: the strip's text highlighter is on, in this colour ("" off):
// a text selection the mouse or the pen lets go of reaches
// onSelectionFinished at once with {highlighter: true}, no popup, and the
// selection is drawn in that colour while it is made. A finger's selection
// has no such end (its handles are still being dragged), so it gets the
// popup, and so does one that places a highlight waiting to be moved.
// onHighlightAction(id, action, color): the bar over a highlight the
// highlighter just made, `action` "color" (with the swatch picked),
// "comment" or "link"; without it that bar is not offered.
// moveMode: a highlight waits to be moved (App's moveHighlightId): the
// selection popup offers only Move here, and highlights let the pointer
// through, so the text under them can be selected.
// commentMargin: what the comment cards beside the pages read (App's
// CommentMarginContext value, comments/marginSlot.jsx: the threads, the
// active card's `target`, the handlers), or null for no margin (a phone, a
// page without comments). anchors: what the cards stand beside, the
// highlights and the ink groups on the pages (app/treeDerived.js
// commentAnchors: {id, kind, position, threadCount}). marginRef.current(id)
// says whether that anchor's page has room for its card; onMarginLost() is
// called when the active card's page no longer has (docs/dev/comments.md
// "The PDF margin").
// The zoom window (ink/ZoomWindow.jsx) docks under the scroller while the
// page tools' ink.zoom is on one of the document's pages: zoomHeight is its
// height in css px and onZoomHeight(px) sets it (inkZoomHeight);
// onZoomChange(update) changes the window, `update(window) → next | null`.
// areaKey / peekKey: the held keys (docs/dev/hotkeys.md "Held keys") that
// make a mouse drag box an area and show a translated page's original.
// onParagraphContext(menu) opens a translated paragraph's menu (docs/dev/
// translation.md "A paragraph's menu"): a right-click on a block of a page
// that shows its translation, the selection popup's ⋯ and a paragraph's
// failed mark send what paragraphMenu (transSelect.js) says of it, with
// {x, y, url, pn, i, unit, running, peek} (openParaMenu); its rows act
// through translateCtlRef.
// A highlight to review after its PDF was replaced (dashed, or hollow:
// lost) has a review card (HighlightReview.jsx): onAcceptHighlight(id),
// onPlaceHighlight(id) (a lost one's closest match applied),
// onMoveHighlight(id, step) (`step` the card's, when it had taken one),
// onDiscardHighlight(id) (one taken off the page: the step taken, or
// null), discardOf(id) (what that Discard would do) and
// onUndoReview(id, step) (a step's button pressed again) are its actions,
// an editor's; reviewPosition(id) is the block's stored pdf_position (null
// when there is no such block), which says whether a step taken on a card
// is still in effect; reviewNote(id) the note the block carries
// (highlightReview.js noteSummary, null for no block), which the card
// shows; onReviewFollow(id) has the notes panel follow a card that went to
// that highlight (a tap, Previous, Next, the advance after a decision, the
// Document menu); onReviewWaiting(ids) is told the highlights a card
// moved or placed on its closest match that wait for its Accept ([] for
// none; the Document menu counts them); `fileId` is the stored file the page shows (a lost
// one's picture is of the file its rectangles were measured on, when that
// is another) and `pageId` the page: another of either starts the card's
// walk afresh; reviewRef.current(id, {done, scroll, follow}) scrolls to
// that one and opens its card (the Document menu's Review, `follow`; App
// after a move started from the card, `done` what it did and scroll
// false).
function PdfViewer({ url, citation = null, highlights, pdfScaleValue, scrollRef, onJump, onHighlightJump, onLinkHighlight, onSelectionFinished, onAreaSelection, onPenSelection, onHighlightContext, onHighlightThread, searchRef, captureRef, onEffectiveScale, onZoomTo, findMarks, onExternalLink, onLinkContext, onBeforeLinkJump, onLoadState, retryRef, areaMode, areaKey = "Mod", peekKey = "Alt", hideEmbeddedAnnots, darkPage = false, translateKey = "", translateScope = null, translateParallel = 3, onTranslate, onTranslateSelection = null, onTranslateBrief, onTranslateLookup, translateRecall = null, translateKeeps = true, onAddTerm = null, translateCtlRef, onTranslateState, onParagraphContext, selTranslate = "", translateLangLabel = "", selComment = false, highlighter = "", onHighlightAction, moveMode = false, marks, flashHighlightId = null, smoothWheel = false, commentMargin = null, anchors = null, marginRef = null, onMarginLost, onPageInView, zoomHeight = 200, onZoomHeight, onZoomChange, onAcceptHighlight, onPlaceHighlight, onMoveHighlight, onDiscardHighlight, discardOf, onUndoReview, reviewPosition = null, reviewNote = null, onReviewFollow, onReviewWaiting, fileId = "", pageId = "", reviewRef = null }) {
  const tools = usePageTools(), { ink, text } = tools;
  const viewerRef = useRef(null);
  const [pdfDoc, setPdfDoc] = useState(null);
  const [numPages, setNumPages] = useState(0);
  const [docSeq, setDocSeq] = useState(0); // bumped per document — keys the page tree so swaps are atomic
  // The page in view (docs/dev/handwriting.md "Copy, cut and paste"): of the
  // page boxes intersecting the scroller, the one whose centre lies nearest
  // the scroller's centre, reported as onPageInView(pageNumber, {width,
  // height} at scale 1) whenever it changes — the intersections from one
  // observer over every page box, the nearest re-judged per scroll frame.
  const onPageInViewRef = useRef(onPageInView);
  onPageInViewRef.current = onPageInView;
  useEffect(() => {
    const root = viewerRef.current;
    if (!root || !onPageInView || !numPages) return undefined;
    const seen = new Map(); // page box → page number, while it intersects
    let last = 0, raf = 0;
    const report = () => {
      raf = 0;
      const r = root.getBoundingClientRect(), mid = r.top + r.height / 2;
      let best = 0, dist = Infinity;
      for (const [el, n] of seen) {
        const b = el.getBoundingClientRect(), d = Math.abs((b.top + b.bottom) / 2 - mid);
        if (d < dist) { dist = d; best = n; }
      }
      if (!best || best === last) return;
      last = best;
      onPageInViewRef.current?.(best, { width: pageWidthsRef.current[best - 1] || FALLBACK_W, height: pageHeightsRef.current[best - 1] || FALLBACK_H });
    };
    const schedule = () => { if (!raf) raf = requestAnimationFrame(report); };
    const obs = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) seen.set(e.target, Number(e.target.dataset.page));
        else seen.delete(e.target);
      }
      schedule();
    }, { root });
    root.querySelectorAll(".pdfPageWrap[data-page]").forEach((el) => obs.observe(el));
    root.addEventListener("scroll", schedule, { passive: true });
    return () => { obs.disconnect(); root.removeEventListener("scroll", schedule); cancelAnimationFrame(raf); };
  }, [numPages, docSeq, !!onPageInView]); // eslint-disable-line react-hooks/exhaustive-deps
  const [displayedUrl, setDisplayedUrl] = useState(""); // url of the document on screen (lags `url` during a load)
  const [skeletonUrl, setSkeletonUrl] = useState(""); // url whose manifest skeleton is on screen (page boxes, no document yet)
  const [retryNonce, setRetryNonce] = useState(0); // bumped by the host's Retry button to re-run a failed load
  // Load progress/errors render no UI here: every phase goes to the host via
  // onLoadState, and the app's single shared status pill displays them.
  useEffect(() => {
    if (retryRef) retryRef.current = () => setRetryNonce((n) => n + 1);
  }, [retryRef]);
  const [forcePages, setForcePages] = useState(new Set());
  const pageHeightsRef = useRef([]); // viewport heights at scale 1, indexed 0..n-1
  const pageWidthsRef = useRef([]); // viewport widths at scale 1 — the zoom anchor's horizontal math needs them
  const heightsExactRef = useRef(true); // false while a long doc's tail heights are estimates still refining
  const lastScrollRef = useRef(0); // scrollTop as of the last scroll event — the pre-clamp value during a zoom commit
  const lastScrollLeftRef = useRef(0);

  // Stable callback identities so memoized pages don't re-render every time a
  // parent state change recreates the handler closures. The wrappers always
  // dispatch to the latest handlers via the ref.
  const cbRef = useRef({});
  cbRef.current = { onJump, onHighlightJump, onLinkHighlight, onHighlightContext, onHighlightThread, onExternalLink, onLinkContext, onLoadState, onZoomTo, onAreaSelection, onPenSelection, onTranslate, onTranslateSelection, onTranslateBrief, onTranslateLookup, onTranslateState, onHighlightAction, onParagraphContext };
  const stableCbs = useMemo(() => ({
    onJump: (...a) => cbRef.current.onJump?.(...a),
    onHighlightJump: (...a) => cbRef.current.onHighlightJump?.(...a),
    onLinkHighlight: (...a) => cbRef.current.onLinkHighlight?.(...a),
    onHighlightContext: (...a) => cbRef.current.onHighlightContext?.(...a),
    onHighlightThread: (...a) => cbRef.current.onHighlightThread?.(...a),
    onExternalLink: (...a) => cbRef.current.onExternalLink?.(...a),
    onLinkContext: (...a) => cbRef.current.onLinkContext?.(...a),
  }), []);

  // The peek key (Alt by default) held while any page shows its translation
  // = peek at the original: the overlays hide (CSS visibility, through a
  // class on the viewer: its pages are memoized and do not render for it)
  // until the key is released. The DOM check keeps the
  // preventDefault (which stops a bare Alt from focusing the browser menu
  // bar) from firing when nothing is translated.
  const [transPeek, setTransPeek] = useState(false);
  const transPeekRef = useRef(false);
  transPeekRef.current = transPeek;
  useEffect(() => {
    const { down, up } = translateState.peekKeys(peekKey, () => !!viewerRef.current?.querySelector(".pdfTransLayer"), setTransPeek);
    const off = () => setTransPeek(false);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", off);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", off);
      setTransPeek(false);
    };
  }, [peekKey]);

  // "rendered" only means blank page boxes committed to the DOM — each canvas
  // paints asynchronously after that. Hold the swapped-in url here until the
  // first page reports a successful paint, then tell the host ("painted") so
  // it can drop its "Rendering page…" message. Later paints (scroll, zoom)
  // find the ref empty and no-op.
  const awaitingPaintRef = useRef(null);
  const onPagePainted = useMemo(() => () => {
    const u = awaitingPaintRef.current;
    if (!u) return;
    awaitingPaintRef.current = null;
    cbRef.current.onLoadState?.(u, { phase: "painted" });
  }, []);

  // Ctrl/⌘ + scroll and the two-finger pinch are read by
  // shared/lib/viewerZoom.js (the notebook viewer shares it). What is left
  // here is each commit — the part that knows this viewer's layout.
  const zoomAnchorRef = useRef(null); // viewport point to zoom around; consumed by the anchor effect, null → viewport center
  // Set when a new document mounts, to the scale its pages were first laid
  // out at; consumed by the next scale change so the anchor effect can tell
  // "fit-width settling for the swapped-in document" apart from a user zoom —
  // the two need different anchoring (see below).
  const docSwapPendingRef = useRef(false);
  const zoomLayerRef = useRef(null);
  const viewerZoomRef = useRef(null);
  useEffect(() => {
    const el = viewerRef.current;
    if (!el) return undefined;
    const zoom = installViewerZoom(el, {
      layer: () => zoomLayerRef.current,
      clamp: clampZoom,
      held: inkPenDown,
      onWheelZoom: (next, at) => {
        zoomAnchorRef.current = at;
        docSwapPendingRef.current = false; // an explicit zoom, whatever mounted before it
        cbRef.current.onZoomTo?.(next);
      },
      onPinchZoom: (next, g) => {
        // Re-base scroll by the midpoint's travel. That alone is what moves
        // the view when the fingers only dragged; after a pinch it also means
        // the content at the final midpoint (at the old scale) is the pinched
        // content, which the anchor effect below then re-places there at the
        // committed scale. The refs get the unclamped values on purpose — the
        // anchor effect reads them instead of live scroll to survive
        // pre-layout clamping.
        const sl = g.sl + g.mx - g.vx, st = g.st + g.my - g.vy;
        el.scrollLeft = sl; el.scrollTop = st;
        lastScrollLeftRef.current = sl; lastScrollRef.current = st;
        if (next === g.scale) return; // a drag, or a pinch already against a limit
        zoomAnchorRef.current = { x: g.vx, y: g.vy };
        docSwapPendingRef.current = false;
        cbRef.current.onZoomTo?.(next);
      },
    });
    viewerZoomRef.current = zoom;
    return () => { viewerZoomRef.current = null; zoom.dispose(); };
  }, []);

  // A notched mouse wheel glides instead of jumping while the setting is on
  // (shared/lib/smoothWheel.js; Settings → Reading › PDFs).
  useEffect(() => {
    const el = viewerRef.current;
    if (el && smoothWheel) return installSmoothWheel(el);
  }, [smoothWheel]);

  // Reset pending touch alignment on zoom/document changes. The helper never
  // writes scroll offsets while a finger or native momentum is moving.
  useEffect(() => {
    const el = viewerRef.current;
    if (el) return installVerticalScrollSnap(el);
  }, [pdfScaleValue, pdfDoc]);

  // Group find marks per page once, sharing one frozen empty array so pages
  // without marks keep referentially-equal props (memo stays effective).
  const marksByPage = useMemo(() => {
    const map = new Map();
    for (const m of findMarks || []) {
      if (!map.has(m.page)) map.set(m.page, []);
      map.get(m.page).push(m);
    }
    return map;
  }, [findMarks]);

  // Highlights grouped per page — and only for the document actually on
  // screen: during a tab switch the incoming page's highlights arrive before
  // its document does, and must not paint onto the outgoing one. A page's
  // slice keeps its identity while its highlights read the same (the
  // highlights are rebuilt from the blocks on every edit), so typing a note
  // re-renders just that highlight's page, not every page with one.
  const hlsPrevRef = useRef(new Map());
  const hlsByPage = useMemo(() => {
    const map = new Map();
    if (displayedUrl !== url) { hlsPrevRef.current = map; return map; }
    for (const h of highlights || []) {
      const p = h.position?.pageNumber;
      if (!p) continue;
      if (!map.has(p)) map.set(p, []);
      map.get(p).push(h);
    }
    for (const [p, list] of map) {
      const old = hlsPrevRef.current.get(p);
      if (old && sameHighlights(old, list)) map.set(p, old);
    }
    hlsPrevRef.current = map;
    return map;
  }, [highlights, displayedUrl, url]);

  // The comment anchors grouped per page, for the document on screen, a
  // page's slice kept while it reads the same, as the highlights are.
  const anchorsPrevRef = useRef(new Map());
  const anchorsByPage = useMemo(() => {
    const map = new Map();
    if (displayedUrl !== url) { anchorsPrevRef.current = map; return map; }
    for (const a of anchors || []) {
      const p = a.position?.pageNumber;
      if (!p) continue;
      if (!map.has(p)) map.set(p, []);
      map.get(p).push(a);
    }
    for (const [p, list] of map) {
      const old = anchorsPrevRef.current.get(p);
      if (old && sameAnchors(old, list)) map.set(p, old);
    }
    anchorsPrevRef.current = map;
    return map;
  }, [anchors, displayedUrl, url]);

  // The comment margin (docs/dev/comments.md "The PDF margin"). While the
  // document has an anchor (a highlight, an ink group) with an open thread,
  // or a new comment's card is open, each page with room for the card
  // column stands beside it, the two centred together
  // (comments/marginLayout.js); pages without room are centred and keep the
  // badge and the popover.
  const [viewerW, setViewerW] = useState(0);
  useEffect(() => {
    const el = viewerRef.current;
    if (!el) return undefined;
    const read = () => setViewerW(el.clientWidth);
    read();
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const marginTarget = commentMargin?.target || null;
  const withMargin = useMemo(() => {
    if (!commentMargin) return false;
    for (const list of anchorsByPage.values()) {
      if (list.some((a) => a.threadCount > 0 || (marginTarget?.compose && a.id === marginTarget.blockId))) return true;
    }
    return false;
  }, [anchorsByPage, commentMargin, marginTarget]);
  const withMarginRef = useRef(withMargin);
  withMarginRef.current = withMargin;

  // Expose full-text search over the loaded document (used by the search
  // panel). Each page's text runs are joined into one string — so matches can
  // span runs — and searched through a normalized view (ligatures folded,
  // hyphenated line breaks re-joined, digit-group separators dropped:
  // textnorm.normalizeChars, the mirror of the server index's rules). Every
  // normalized character remembers its source run, so a match maps back to
  // exact rects (at scale 1) even when normalization changed lengths.
  // Each page's text is read from pdf.js once per parsed document and kept
  // (SEARCH_TEXT, by the document, so it goes with it): a second query over
  // a book is a regex over strings, not a worker round trip per page. A
  // query the panel has moved past stops at its next page and answers
  // `stale` (searchGen), which the panel ignores.
  const searchGenRef = useRef(0);
  useEffect(() => {
    if (!searchRef) return;
    searchRef.current = pdfDoc ? async (re) => {
      const gen = ++searchGenRef.current;
      const out = [];
      let texts = SEARCH_TEXT.get(pdfDoc);
      if (!texts) { texts = new Map(); SEARCH_TEXT.set(pdfDoc, texts); }
      for (let p = 1; p <= pdfDoc.numPages && out.length < 200; p++) {
        let text = texts.get(p);
        if (!text) {
          const page = await pdfDoc.getPage(p);
          const vp = page.getViewport({ scale: 1 });
          const tc = await page.getTextContent();
          if (gen !== searchGenRef.current) return Object.assign([], { stale: true });
          const items = tc.items;
          // Page string: runs joined by their PDF line break or a space,
          // each char tagged with its source run (-1 = synthetic filler).
          const chars = runChars(items.map((it) => ({ text: it.str, hasEOL: it.hasEOL })), { fillSpaces: true });
          const { norm, src } = normalizeChars(chars);
          text = {
            items, pageStr: norm.join(""), transform: vp.transform, width: vp.width, height: vp.height,
            // The char tags as typed arrays: a book's worth stays small.
            src: Int32Array.from(src), it: Int32Array.from(chars, (c) => c.it), off: Int32Array.from(chars, (c) => c.off),
          };
          texts.set(p, text);
        }
        const { items, pageStr } = text;
        const rx = new RegExp(re.source, re.flags.includes("g") ? re.flags : re.flags + "g");
        let m;
        while ((m = rx.exec(pageStr)) && out.length < 200) {
          if (!m[0]) { rx.lastIndex++; continue; }
          // Match range → per-run char spans → one rect per run (sliced
          // proportionally by char position; runs are single-line).
          const spans = new Map(); // run index -> [minOff, maxOff]
          for (let n = m.index; n < m.index + m[0].length; n++) {
            const c = text.src[n];
            const it = text.it[c];
            if (it < 0) continue;
            const off = text.off[c];
            const s = spans.get(it);
            if (s) { s[0] = Math.min(s[0], off); s[1] = Math.max(s[1], off); }
            else spans.set(it, [off, off]);
          }
          const rects = [];
          for (const [ii, [o1, o2]] of spans) {
            const it = items[ii];
            const str = it.str || "";
            const tx = pdfjsLib.Util.transform(text.transform, it.transform);
            const fh = Math.hypot(tx[2], tx[3]) || 10;
            const w = it.width || fh;
            const x1 = tx[4] + w * (o1 / str.length);
            const x2 = tx[4] + w * ((o2 + 1) / str.length);
            rects.push({ x1, y1: tx[5] - fh, x2: Math.max(x1 + 2, x2), y2: tx[5] + fh * 0.25 });
          }
          if (!rects.length) continue;
          const ctxStart = Math.max(0, m.index - 40);
          out.push({
            page: p,
            snippet: pageStr.slice(ctxStart, m.index + m[0].length + 60).trim().slice(0, 140),
            rects,
            pageW: text.width,
            pageH: text.height,
          });
        }
      }
      return out;
    } : null;
    return () => { if (searchRef) searchRef.current = null; };
  }, [pdfDoc, searchRef]);

  // Snapshot an area highlight's region as a PNG data URL. Nothing is stored
  // with the note — clicking the rectangle later re-crops it, rendered
  // offscreen straight from the document, so it works even while the page
  // isn't on screen (e.g. a click in the notes window before scrolling).
  // Only from the document the viewer is asked to show: during a paper
  // switch pdfDoc is still the previous paper's until the new one commits
  // (it stays on screen meanwhile), and a crop taken then would be that
  // paper's page — the caller retries once the new document is rendered.
  const urlRef = useRef(url);
  urlRef.current = url;
  useEffect(() => {
    if (!captureRef) return;
    captureRef.current = pdfDoc ? async (h) => {
      const pos = h?.position;
      const r = pos?.boundingRect;
      const pn = pos?.pageNumber;
      if (!r || !pn) return null;
      if (docUrlRef.current !== urlRef.current) return null;
      try {
        // The stored rect is page-relative at its capture-time render size
        // (the position's width × height).
        return await cropPage(await pdfDoc.getPage(pn), r, pos);
      } catch {
        return null;
      }
    } : null;
    return () => { captureRef.current = null; };
  }, [pdfDoc, captureRef]);
  // Resolve scale: numeric value as-is, "page-width" computes a scale that
  // fits the typical page (typicalPageIndex: the most common width, not
  // page 1, which may be a cover of another size) to the viewer width.
  // Recomputed on viewer resize so it adapts to sidebar drags / phone rotation.
  const [fitWidthScale, setFitWidthScale] = useState(1);
  const numericScale = parseFloat(pdfScaleValue);
  const isFitWidth = isNaN(numericScale);
  const scale = isFitWidth ? fitWidthScale : numericScale;
  // The scale for the code inside a page that needs it as a number (the
  // raster, the text layer, the translation): the pages take none as a prop,
  // they size with SCALE_VAR on the zoom layer. Set after the anchor effect
  // below, so what it starts sees the anchored scroll.
  const [pageScale] = useState(() => createPageScale(scale));
  useEffect(() => () => pageScale.dispose(), [pageScale]);
  // A zoom from elsewhere (the buttons, fit-width, a new document) is what the
  // next gesture compounds on; sync() ignores it while a dispatch is in flight.
  useEffect(() => { viewerZoomRef.current?.sync(scale); }, [scale]);
  useEffect(() => { onEffectiveScale?.(scale); }, [scale, onEffectiveScale]);
  // The scale that fits the typical page of `widths` to the viewer's width,
  // or null while the viewer has none.
  const fitScale = (widths) => {
    const naturalW = widths[typicalPageIndex(widths)];
    const containerW = viewerRef.current?.clientWidth;
    return naturalW > 0 && containerW > 0 ? Math.max(ZOOM_MIN, containerW / naturalW) : null;
  };
  // Before paint, so a document is never seen at the scale the one before
  // it fitted.
  useLayoutEffect(() => {
    if (!isFitWidth || !pdfDoc || !viewerRef.current) return;
    // The widths are in pageWidthsRef, measured before the doc was shown —
    // no async worker round trip per sidebar drag / rotation.
    const compute = () => {
      const fit = fitScale(pageWidthsRef.current);
      if (fit) setFitWidthScale(fit);
    };
    compute();
    const ro = new ResizeObserver(compute);
    ro.observe(viewerRef.current);
    return () => ro.disconnect();
  }, [isFitWidth, pdfDoc, skeletonUrl]);

  // Tell the host which document's pages are in the DOM. Layout effect on
  // purpose: it fires after the swap commit but BEFORE paint, so the host can
  // apply a restored scroll position and the user never sees the document at
  // the wrong offset.
  // `scale`: what the boxes are laid out at, which a restored position is
  // measured in.
  useLayoutEffect(() => {
    if (pdfDoc) {
      awaitingPaintRef.current = url;
      onLoadState?.(url, { phase: "rendered", scale });
      // A page in view at the restored place shows what it showed when its
      // document last left (pageRaster keepPixels): the reader has it
      // already, and no "Rendering page…" rises. Without one the pill stays
      // until a page paints, as for any document.
      const v = viewerRef.current;
      if (v && pagesInView(v, pageHeightsRef.current, scale, v.scrollTop).some(showsKept)) onPagePainted();
    }
  }, [pdfDoc]);

  // The document currently on screen. Kept visible while the next one loads —
  // swapping only when the new doc is fully laid out is what prevents the
  // blank flash and the scrollbar resizing repeatedly during a tab switch. No
  // teardown on unmount: documents belong to DOC_CACHE, which is what makes
  // coming back to a paper instant.
  const displayedDocRef = useRef(null);
  const docUrlRef = useRef(""); // the url pdfDoc belongs to (lags `url` during a load)

  // The three ways a document reaches the screen (docs/dev/pdf_loading.md).
  // Every commit lands heights, page tree and document in ONE render, so the
  // scrollbar changes exactly once. A skeleton is page boxes laid out from
  // the manifest before pdf.js has a document: the reader's restored position
  // lands on it (the host's "layout" phase), and when the real document
  // arrives it fills the same boxes — same keys, nothing remounts.
  const skeletonRef = useRef(null); // url whose skeleton is on screen, else null
  // The document whose pages are on screen leaves them: what the pages the
  // reader last saw show is kept for its return. Those pages are found in
  // the layout the reader saw, at the scale the zoom anchor below last laid
  // them out at and the scroll last seen, not in the DOM: a tab brings its
  // zoom in the same commit as its url, so in that commit the leaving pages
  // are already laid out at the next tab's zoom, under the old scroll. A
  // skeleton on screen holds none of its.
  const keepLeaving = () => {
    if (!displayedDocRef.current || skeletonRef.current) return;
    keepPixels(displayedDocRef.current, pagesInView(viewerRef.current, pageHeightsRef.current, prevScaleRef.current, lastScrollRef.current));
  };
  const commitLayout = (heights, widths, exact) => {
    heightsExactRef.current = exact;
    pageHeightsRef.current = heights;
    pageWidthsRef.current = widths;
    setPageHeights(heights);
    setPageWidths(widths);
    setNumPages(heights.length);
  };
  const commitSkeleton = (url, { heights, widths }) => {
    keepLeaving();
    skeletonRef.current = url;
    commitLayout(heights, widths, true);
    setDocSeq((s) => s + 1);
    setDisplayedUrl(url);
    setSkeletonUrl(url);
  };
  const commitDoc = (url, doc, heights, widths, exact) => {
    if (displayedDocRef.current !== doc) keepLeaving();
    displayedDocRef.current = doc;
    docUrlRef.current = url;
    commitLayout(heights, widths, exact);
    if (skeletonRef.current !== url) setDocSeq((s) => s + 1);
    skeletonRef.current = null;
    setDisplayedUrl(url);
    setPdfDoc(doc);
    rememberDoc(url, { doc, heights, widths });
  };
  // Skeleton boxes are in the DOM, pre-paint: the host applies its pending
  // scroll restore now, before a byte of the PDF has been parsed.
  useLayoutEffect(() => {
    if (skeletonUrl) onLoadState?.(skeletonUrl, { phase: "layout", scale });
  }, [skeletonUrl]);
  // The viewer going (its page's tab left for one without a PDF, the
  // library) keeps what its pages show, as a document swap does. A layout
  // cleanup: React runs it before it takes the viewer's boxes out.
  useLayoutEffect(() => keepLeaving, []); // eslint-disable-line react-hooks/exhaustive-deps

  // A document parsed on an earlier visit goes on screen in the commit
  // that asks for it, before paint and at the scale it is read at, so the
  // viewer's first frame already holds its pages, and what they showed
  // when it left.
  useLayoutEffect(() => {
    const live = url ? DOC_CACHE.get(url) : null;
    if (!live || docUrlRef.current === url) return;
    onLoadState?.(url, { phase: "open" });
    onLoadState?.(url, { phase: "cached" });
    const fit = isFitWidth && fitScale(live.widths);
    if (fit) setFitWidthScale(fit);
    commitDoc(url, live.doc, live.heights, live.widths, true);
  }, [url]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => (url ? showDoc(url) : undefined), [url]);
  useEffect(() => {
    if (!url) return;
    let cancelled = false, failed = false;
    const report = (st) => {
      if (st.phase === "error") failed = true;
      if (!cancelled) onLoadState?.(url, st);
    };
    (async () => {
      try {
        // On screen already from the parsed-document cache (above).
        if (docUrlRef.current === url && displayedDocRef.current === DOC_CACHE.get(url)?.doc) return;
        report({ phase: "open" });
        // A document parsed on an earlier visit is handed over as it is.
        const live = DOC_CACHE.get(url);
        if (live) {
          report({ phase: "cached" });
          commitDoc(url, live.doc, live.heights, live.widths, true);
          return;
        }
        // The engine, when the app has not loaded it ahead, travels with the
        // manifest and the bytes; awaited just before pdf.js is called.
        const engine = loadPdfEngine();
        engine.catch(() => {});
        // The manifest and the bytes travel in parallel. On a cold open the
        // manifest alone lays the document out, while pdf.js is still
        // fetching; for an uncached upload it also decides the transport.
        const manifestP = fetchManifest(url);
        manifestP.then((m) => {
          // A late manifest must not replace a worker/download error with
          // another "Preparing document" skeleton that can never finish.
          if (cancelled || failed || displayedDocRef.current || skeletonRef.current) return;
          const lay = layoutFromManifest(m, { width: FALLBACK_W, height: FALLBACK_H });
          if (lay) commitSkeleton(url, lay);
        });
        let data = PDF_CACHE.get(url) || (await diskCacheGet(url));
        if (cancelled) return;
        let openedByRange = false;
        if (data) {
          report({ phase: "cached" });
        } else {
          // Size from the manifest or a HEAD, whichever answers first.
          const bytes = docIdOf(url) ? await firstValue([manifestP.then((m) => m?.bytes), fetchSize(url)]) : null;
          if (cancelled) return;
          openedByRange = chooseTransport({ url, bytes }) === "range";
          if (!openedByRange) {
            data = await fetchPdfData(url, (_url, st) => report(st), () => cancelled);
            if (!data || cancelled) return;
          }
        }
        await engine;
        if (cancelled) return;
        report({ phase: "parsing" });
        let doc;
        if (openedByRange) {
          // pdf.js fetches the url itself (absolute, once resolved); the fetch
          // wrapper in utils.js tags same-origin absolute urls with the
          // workspace header too, so the ranges land in the right library.
          doc = await pdfjsLib.getDocument(openParams(rangeOpenOptions(withShare(url)))).promise;
        } else {
          cachePdf(url, data); // insert or bump LRU position
          doc = await pdfjsLib.getDocument(openParams({ data: data.slice(0), disableAutoFetch: true, disableRange: true })).promise;
        }
        if (cancelled) { doc.destroy().catch(() => {}); return; }
        report({ phase: "opened" }); // pdf.js has the document: xref and page tree parsed
        const n = doc.numPages;
        // Exact page sizes from the manifest when it describes this file.
        // Bytes from the cache do not wait long for it: a manifest computed
        // for the first time (a long file nobody has opened) may take a
        // second, and measuring is the older, still-correct path.
        const manifest = await (data ? withTimeout(manifestP, 250) : manifestP);
        if (cancelled) { doc.destroy().catch(() => {}); return; }
        const fromManifest = manifest && manifest.pages === n
          ? layoutFromManifest(manifest, { width: FALLBACK_W, height: FALLBACK_H }) : null;
        const EXACT = 8;
        let heights, widths;
        if (fromManifest) {
          ({ heights, widths } = fromManifest);
        } else {
          // Measure the first pages (a worker round trip each) and estimate
          // the rest from the typical one among them — not page 1, which may
          // be a cover of another size — refined below. Kept small: this
          // loop blocks first paint.
          const measured = Math.min(n, EXACT);
          heights = []; widths = [];
          for (let i = 1; i <= measured; i++) {
            report({ phase: "measuring", done: i - 1, total: measured });
            try {
              const vp1 = (await doc.getPage(i)).getViewport({ scale: 1 });
              heights.push(vp1.height); widths.push(vp1.width);
            } catch { heights.push(heights[0] || FALLBACK_H); widths.push(widths[0] || FALLBACK_W); }
            if (cancelled) { doc.destroy().catch(() => {}); return; }
          }
          const typical = typicalPageIndex(widths);
          for (let i = heights.length; i < n; i++) { heights.push(heights[typical] || FALLBACK_H); widths.push(widths[typical] || FALLBACK_W); }
        }
        const exact = !!fromManifest || n <= EXACT;
        commitDoc(url, doc, heights, widths, exact);
        if (openedByRange) backfillLocalCopy(url);
        if (exact) return;
        // Refine the estimated heights in the background (long docs without
        // a manifest only). The arrays are shared with the DOC_CACHE entry,
        // so a later reopen gets the refined layout.
        for (let i = EXACT; i < n; i++) {
          if (cancelled) return;
          try {
            const vp1 = (await doc.getPage(i + 1)).getViewport({ scale: 1 });
            heights[i] = vp1.height; widths[i] = vp1.width;
          } catch {}
          if ((i + 1) % 50 === 0 || i === n - 1) {
            pageHeightsRef.current = [...heights];
            pageWidthsRef.current = [...widths];
            setPageHeights([...heights]);
            setPageWidths([...widths]);
          }
        }
        heightsExactRef.current = true; // layout is final — the zoom settle loop can stand down
      } catch (e) {
        report({ phase: "error", detail: e?.message || t("failed to open the PDF") });
      }
    })();
    return () => {
      cancelled = true;
      // No-op if the download already finished; otherwise clears the
      // now-orphaned "downloading…" task entry.
      onLoadState?.(url, { phase: "cancelled" });
    };
  }, [url, retryNonce]);

  // A document swap replaces the scroller's content wholesale: the host's
  // tab restore has just set scrollTop (pre-paint, in the "rendered"
  // callback), but the scroll-tracking refs still describe the OLD document
  // until its scroll event dispatches. Resync them now — this layout effect
  // is defined after the "rendered" one, so it sees the restored value. Also
  // flag the swap for the anchor effect below.
  useLayoutEffect(() => {
    docSwapPendingRef.current = scale;
    // A settle loop from a zoom on the PREVIOUS document may still be alive
    // (its [scale] cleanup never fires when the swap keeps the scale, e.g.
    // both tabs at the same numeric zoom) — the swap's scrollHeight change
    // would trip it into stamping old-document coordinates over the restored
    // position. Kill it.
    cancelAnimationFrame(anchorRafRef.current);
    if (viewerRef.current) {
      lastScrollRef.current = viewerRef.current.scrollTop;
      lastScrollLeftRef.current = viewerRef.current.scrollLeft;
    }
  }, [docSeq]);

  // Preserve position across zoom changes by keeping one anchor point fixed
  // on screen: the mouse position for Ctrl+scroll (set in zoomAnchorRef by
  // the wheel handler), the viewport center for button zooms and fit-width
  // recomputes. The content under the anchor is found in OLD-scale
  // coordinates (page index + fraction into it — exact despite the fixed 8px
  // inter-page margins) and re-placed at the same screen point at the new
  // scale.
  //
  // Document swaps are the exception (docSwapPendingRef): when the swapped-in
  // document's fit-width scale settles, the scale change is not a zoom. The
  // restored scrollTop was computed against the OLD document's effective
  // scale — the only one known pre-paint — i.e. the ratio was applied to the
  // raw scrollTop, so its exact inverse is a TOP-of-viewport re-map
  // (anchor point 0,0), not a centered one. Anchoring the viewport center
  // here is what made tab switches land hundreds of px off. A document laid
  // out at its own scale in the commit that brought it (one parsed before,
  // committed at its fit) was never seen at the old one, and its restored
  // position is measured at the new one: nothing to re-place.
  //
  // Layout effect + the scroll-tracked refs, on purpose: the page boxes
  // resize in this same commit, and when zooming out that shrinks the scroll
  // range — by the time this runs the browser may have clamped
  // scrollTop/scrollLeft, so reading them live would anchor on the wrong
  // content. The refs still hold the pre-zoom values (the clamp's scroll
  // event hasn't dispatched yet), and writing the corrected position before
  // paint means no visible jump at all.
  const prevScaleRef = useRef(scale);
  const anchorRafRef = useRef(0); // live settle-loop frame — cancelled on re-zoom AND on document swap
  useLayoutEffect(() => {
    const prev = prevScaleRef.current;
    prevScaleRef.current = scale;
    const anchor = zoomAnchorRef.current;
    zoomAnchorRef.current = null;
    const docSwap = docSwapPendingRef.current;
    docSwapPendingRef.current = false;
    if (prev === scale || docSwap === scale || !viewerRef.current) return;
    const v = viewerRef.current;
    const heights = pageHeightsRef.current;
    if (heights.length === 0) return;
    const ax = docSwap ? 0 : anchor ? anchor.x : v.clientWidth / 2;
    const ay = docSwap ? 0 : anchor ? anchor.y : v.clientHeight / 2;

    // Find the page covering the anchor's content-y at the OLD scale. The
    // anchor may sit in the fixed 8px gap below the page — that slice does
    // NOT scale with the zoom, so it's kept separate (gapPx) instead of being
    // folded into the page fraction.
    const oldY = lastScrollRef.current + ay;
    let acc = 0, anchorIdx = 0, fracInPage = 0, gapPx = 0;
    for (let i = 0; i < heights.length; i++) {
      const ph = (heights[i] || FALLBACK_H) * prev;
      if (acc + ph + PAGE_GAP > oldY) {
        anchorIdx = i;
        fracInPage = Math.min(1, (oldY - acc) / ph);
        gapPx = Math.max(0, oldY - acc - ph);
        break;
      }
      acc += ph + PAGE_GAP;
    }

    // Content-x under the anchor, in base (scale-1) page coordinates. A page
    // narrower than the viewport is centered by its auto margins, or stands
    // beside its comment margin; a wider one sits at x=0 — pageLeft covers
    // the three layouts, before and after.
    const clientW = v.clientWidth;
    const pw = pageWidthsRef.current[anchorIdx] || FALLBACK_W;
    const oldLeft = pageLeft(clientW, pw * prev, withMarginRef.current);
    const baseX = (lastScrollLeftRef.current + ax - oldLeft) / prev;

    const place = () => {
      v.scrollTop = pageTopAt(heights, anchorIdx, scale)
        + fracInPage * (heights[anchorIdx] || FALLBACK_H) * scale + gapPx - ay;
      const newLeft = pageLeft(clientW, pw * scale, withMarginRef.current);
      v.scrollLeft = newLeft + baseX * scale - ax;
      lastScrollRef.current = v.scrollTop;
      lastScrollLeftRef.current = v.scrollLeft;
    };
    place();
    if (heightsExactRef.current) return; // every height is measured — nothing can shift, skip the settle loop
    // Safety net: estimated heights (long docs) can still refine right after
    // a zoom and shift the layout — re-place while scrollHeight settles. The
    // cleanup cancel is load-bearing: without it, each step of a continuous
    // Ctrl+scroll leaves this loop alive, and the NEXT step's layout change
    // trips the stale loop into re-placing old-scale coordinates over the
    // fresh ones — the zoom visibly slides off the anchor.
    let tries = 0;
    let lastSH = v.scrollHeight;
    const tick = () => {
      if (!viewerRef.current) return;
      if (viewerRef.current.scrollHeight !== lastSH) { lastSH = viewerRef.current.scrollHeight; place(); }
      if (tries++ < 30) anchorRafRef.current = requestAnimationFrame(tick);
    };
    anchorRafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(anchorRafRef.current);
  }, [scale]);
  useLayoutEffect(() => { pageScale.set(scale); }, [scale, pageScale]);

  // Pre-compute every page's natural height. The values feed both the jump
  // math and the per-page placeholder height below — having every page's
  // wrapper reserve its real size keeps the DOM's scrollHeight in sync with
  // what the jump math assumes, so scrollTo() doesn't get clamped to a
  // smaller scrollable range. Computing metadata-only viewports is cheap.
  // Populated by the load flow above, before the document is shown.
  const [pageHeights, setPageHeights] = useState([]);
  const [pageWidths, setPageWidths] = useState([]);

  // A page's left edge beside its comment margin at this zoom, or null when
  // it has no room for the column (comments/marginLayout.js).
  const pageMargin = (pn) => besideMargin(viewerW, (pageWidths[pn - 1] || 0) * scale);
  // The pages' margins stack as one column: each page's top, and where the
  // cards of the pages above end (comments/marginSlot.jsx).
  const [marginStack] = useState(createMarginStack);
  const pageTops = useMemo(() => {
    if (!withMargin) return [];
    const out = [];
    for (let i = 0, y = 0; i < numPages; i++) { out.push(y); y += (pageHeights[i] || FALLBACK_H) * scale + PAGE_GAP; }
    return out;
  }, [withMargin, numPages, pageHeights, scale]);
  const marginPageOf = (id) => (displayedUrl === url ? (anchors || []).find((a) => a.id === id)?.position?.pageNumber || 0 : 0);
  // App asks, as it opens an anchor's thread, whether it opens as a card.
  if (marginRef) marginRef.current = (id) => !!commentMargin && !!marginPageOf(id) && pageMargin(marginPageOf(id)) != null;
  useEffect(() => () => { if (marginRef) marginRef.current = null; }, [marginRef]);
  // The active card's page lost its room (a zoom, a narrower pane): App
  // shows its thread in the popover instead.
  const targetPage = marginTarget ? marginPageOf(marginTarget.blockId) : 0;
  const targetRoom = !targetPage || pageMargin(targetPage) != null;
  // Its highlight is drawn stronger while it has a card (a thread resolved
  // meanwhile takes the card away); an ink group's emphasis is the ink
  // layer's flash.
  const targetHl = targetPage ? hlsByPage.get(targetPage)?.find((h) => h.id === marginTarget.blockId) : null;
  const activeHl = targetHl && (marginTarget.compose || (targetHl.threadCount || 0) > 0) ? targetHl.id : null;
  const lostRef = useRef(onMarginLost);
  lostRef.current = onMarginLost;
  useEffect(() => { if (marginTarget && !targetRoom) lostRef.current?.(); }, [marginTarget, targetRoom]);

  // Scroll to exact highlight position. Long jumps snap instantly — smooth
  // scrolling across many pages is what made find-next feel sluggish.
  const scrollToPositionRef = useRef(null);
  useEffect(() => {
    scrollToPositionRef.current = async ({ position, box, behavior, offset }) => {
      const pn = position?.pageNumber;
      if (!pn || !viewerRef.current || !pdfDoc) return;
      const r = position?.boundingRect;
      const heights = pageHeightsRef.current;

      // Lazy-compute missing page heights up to target page
      for (let i = heights.length; i < pn; i++) {
        try {
          const page = await pdfDoc.getPage(i + 1);
          heights[i] = page.getViewport({ scale: 1 }).height;
        } catch {
          heights[i] = FALLBACK_H;
        }
      }

      // Compute page-top from cached heights (accurate even for unrendered pages)
      const pageTop = pageTopAt(heights, pn - 1, scale);
      const curH = (heights[pn - 1] || FALLBACK_H) * scale;
      const storedH = position?.height || 1;
      // (the cached heights are the pages at scale 1, the frame a box's points are in)
      const highlightY = box ? box.y * scale : r ? r.y1 * curH / storedH : 0;
      const targetTop = pageTop + highlightY - (offset ?? 80);
      const dist = Math.abs(targetTop - viewerRef.current.scrollTop);
      viewerRef.current.scrollTo({ top: targetTop, behavior: behavior || (dist > 1500 ? "auto" : "smooth") });

      // Force-render target page if not yet visible
      const pageEl = viewerRef.current.querySelector(`[data-page="${pn}"]`);
      if (!pageEl || !pageEl.style.width) {
        setForcePages(prev => new Set([...prev, pn]));
        setTimeout(() => setForcePages(prev => { const s = new Set(prev); s.delete(pn); return s; }), 2000);
      }
    };
    if (scrollRef) scrollRef.current = scrollToPositionRef.current;
  }, [scrollRef, scale, pdfDoc]);

  // The review card of a dashed or hollow highlight (HighlightReview.jsx):
  // opened by hovering or tapping its rectangles (PdfPage), by its Previous
  // and Next and its steps, from the Document menu (reviewRef), which
  // scroll to the highlight first (to `position` when given: a discarded
  // one, drawn no more, where it stood; an undo puts it back there), and by
  // App after a move started from it (`done`, the step, which the card
  // records; no scroll: the selection is in sight). A place past the last
  // page (a lost one from a page the PDF no longer has) is not scrolled to:
  // its card stands in the viewer. Another page or file closes it and
  // starts the card's walk afresh (the steps taken on cards). With
  // `follow` (Previous, Next, the advance after a decision, the Document
  // menu) and on a tap, the notes panel follows it (onReviewFollow).
  const followRef = useRef(null);
  followRef.current = onReviewFollow || null;
  const { card: reviewCard, steps: reviewSteps, review, drawn: reviewDrawn } = useHighlightReview(followRef);
  const openReview = (id, { done = null, scroll = true, position = null, walked = "", follow = false } = {}) => {
    if (displayedUrl !== url) return;
    const h = (highlights || []).find((x) => x.id === id) || null;
    if (done) review.record(id, done, h);
    const to = position || h?.position;
    if (!to) return;
    if (scroll && !pastLastPage(to, numPages)) scrollToPositionRef.current?.({ position: to });
    review.open(id, { walked });
    if (follow) onReviewFollow?.(id);
  };
  if (reviewRef) reviewRef.current = openReview;
  useEffect(() => () => { if (reviewRef) reviewRef.current = null; }, [reviewRef]);
  useEffect(() => { review.reset(); }, [url, fileId, pageId, review]);
  // The highlights a card moved or placed on its closest match, waiting
  // for its Accept (waitingForAccept), told to App (onReviewWaiting), whose
  // Document menu counts them with the review queue: none once the walk
  // starts afresh, or once this viewer is gone.
  const waitingKey = displayedUrl === url && reviewSteps.size
    ? JSON.stringify(waitingForAccept(reviewList(highlights, reviewSteps, reviewPosition))) : "[]";
  const waitingRef = useRef(null);
  waitingRef.current = onReviewWaiting || null;
  useEffect(() => { waitingRef.current?.(JSON.parse(waitingKey)); }, [waitingKey]);
  useEffect(() => () => waitingRef.current?.([]), []);
  // A lost highlight's closest match, outlined on its page while its card
  // shows it (Show), which scrolls there.
  const ghostOf = reviewCard?.ghost && displayedUrl === url ? (highlights || []).find((x) => x.id === reviewCard.id) : null;
  const reviewGhost = useMemo(() => {
    const c = ghostOf?.position?.lost ? closestPlace(ghostOf.position) : null;
    return c ? { id: ghostOf.id, color: ghostOf.color, ...c.position } : null;
  }, [ghostOf]);
  const showClosest = (id) => {
    const h = displayedUrl === url ? (highlights || []).find((x) => x.id === id) : null;
    const c = h ? closestPlace(h.position) : null;
    if (!c) return;
    scrollToPositionRef.current?.({ position: c.position });
    review.ghost(id);
  };

  // Wait for this document, then mount only the cited page. Matching happens
  // after its actual text layer has rendered, including on zoom changes.
  const [activeCitation, setActiveCitation] = useState(null);
  useEffect(() => {
    let cancelled = false;
    setActiveCitation(null);
    if (citation && pdfDoc && displayedUrl === url && citation.page <= pdfDoc.numPages) {
      Promise.resolve(scrollToPositionRef.current?.({ position: { pageNumber: citation.page }, behavior: "auto" }))
        .then(() => { if (!cancelled) setActiveCitation(citation); });
    }
    return () => { cancelled = true; };
  }, [citation, pdfDoc, displayedUrl, url]);
  useEffect(() => {
    if (!activeCitation) return;
    const dismiss = (e) => { if (e.key === "Escape") setActiveCitation(null); };
    const clickAway = (e) => {
      // The marks let pointer input through to the PDF text, so hit-test
      // their rectangles without interfering with selection or links.
      if (e.detail > 0 && viewerRef.current?.contains(e.target)) {
        const marks = viewerRef.current.querySelectorAll(".pdfCitationMark");
        for (const mark of marks) {
          const r = mark.getBoundingClientRect();
          if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) return;
        }
      }
      setActiveCitation(null);
    };
    document.addEventListener("keydown", dismiss);
    document.addEventListener("click", clickAway, true);
    return () => {
      document.removeEventListener("keydown", dismiss);
      document.removeEventListener("click", clickAway, true);
    };
  }, [activeCitation]);

  // Current-page widget (top-right): tracks scrolling, and typing a number
  // jumps. The "current" page is the one covering a point a third of the way
  // down the viewport — closer to "the page I'm reading" than the strict top
  // edge, which flips the counter while the previous page still fills most of
  // the screen. setState bails out when the value is unchanged, so scroll
  // events don't re-render.
  const [curPage, setCurPage] = useState(1);
  const [pageInput, setPageInput] = useState(null); // non-null while the box is being edited
  const syncCurPage = () => {
    const v = viewerRef.current;
    if (!v || !numPages) return;
    const heights = pageHeightsRef.current;
    const y = v.scrollTop + v.clientHeight / 3;
    let acc = 0, pn = numPages;
    for (let i = 0; i < numPages; i++) {
      acc += (heights[i] || FALLBACK_H) * scale + PAGE_GAP;
      if (acc > y) { pn = i + 1; break; }
    }
    setCurPage(pn);
  };
  // Resync outside of scroll events: document swaps (a restore to the same
  // scrollTop fires no scroll event), zoom changes, and height refinement on
  // long documents can all move the page under the reference point. Before
  // paint, after the restore, so a document comes back with its own number.
  useLayoutEffect(() => { syncCurPage(); }, [docSeq, scale, numPages, pageHeights]);
  const jumpToPage = (pn) => {
    if (!numPages || !Number.isFinite(pn)) return;
    const p = Math.max(1, Math.min(numPages, pn));
    // offset 0: land exactly on the page's top edge, not the highlight
    // jump's 80px context margin.
    scrollToPositionRef.current?.({ position: { pageNumber: p }, offset: 0 });
  };

  // --- Translation engine ----------------------------------------------------
  // One job at a time per document, driven from the host's toolbar button:
  // pdf/translateJob.js segments, plans and requests the pages (N workers,
  // Settings → parallel requests) and hands back each page's entry, its
  // results by block index. pdf/translateState.js keeps them per document
  // with its show/hide, its job and the job's status, so a job runs on when
  // the viewer goes (the host follows it there) and a document comes back
  // translated; a re-run asks only for what is missing. PdfPage only
  // *renders* its page's entry (pdf/TransOverlay.jsx). The document is the
  // one whose page boxes are on screen.
  const transUrl = displayedUrl;
  const transUrlRef = useRef(transUrl);
  transUrlRef.current = transUrl;
  // transMap: pageNo -> {key, paras, texts, done, queued, busy, partial,
  // failed}; transShown: show/hide, for ALL pages at once; the view's
  // `status` is the job's (translateJob.js onStatus).
  const transView = useSyncExternalStore(translateState.subscribe, () => translateState.snapshot(transUrl));
  const { entries: transMap, shown: transShown } = transView;
  useEffect(() => { translateState.touch(transUrl); }, [transUrl]);
  const translateKeyRef = useRef(translateKey);
  translateKeyRef.current = translateKey;
  // {url, pageId, stored, file}: the page being translated, the PDF it
  // shows, whether that PDF is stored on the server (else the request
  // carries the document's opening text for the paper's brief) and its
  // file. It names the document on screen only once that is the page's
  // (translateState.scopeOf).
  const translateScopeRef = useRef(translateScope);
  translateScopeRef.current = translateScope;
  const translateParallelRef = useRef(translateParallel);
  translateParallelRef.current = translateParallel;
  const curPageRef = useRef(1);
  curPageRef.current = curPage;
  const numPagesRef = useRef(0);
  numPagesRef.current = numPages;

  // What the host's button needs to know (translateState.buttonState), pushed
  // when some of it changed: a streamed partial changes none of it, and the
  // host re-renders on a push. `url`: the document all this is about (the one
  // on screen, which lags the `url` prop while the next one loads), so the
  // host's controls that name a document name this one.
  const lastButtonRef = useRef(null);
  useEffect(() => {
    const next = { ...translateState.buttonState(transView, translateKey, curPage, numPages), url: transUrl };
    const last = lastButtonRef.current;
    if (last && Object.keys(next).every((k) => next[k] === last[k])) return;
    lastButtonRef.current = next;
    cbRef.current.onTranslateState?.(next);
  }, [transView, translateKey, curPage, numPages, transUrl]);

  // The papers this browser last left showing their translation (the host's
  // translateRecall, pdf/translateRecall.js), by the file the server keeps it
  // under: a document whose translation shows under the key is remembered
  // under it, one the reader shows in the original is let go. A document with
  // nothing shown yet (opening, before its seed lands) changes nothing. Only
  // a translation the workspace keeps can come back (`translateKeeps`: Keep
  // translations is on and the account may write the workspace; a viewer's
  // stays in its own memory, which the lookup never reads), so no other is
  // remembered; Show original still lets an earlier one go.
  const translateRecallRef = useRef(translateRecall);
  translateRecallRef.current = translateRecall;
  const transFile = translateState.scopeOf(transUrl, translateScope).file;
  const transShowing = useMemo(() => transShown && translateState.buttonState(transView, translateKey, 0).pages > 0,
    [transView, transShown, translateKey]);
  useEffect(() => {
    if (!translateRecall || !transFile) return;
    if (transShowing) {
      if (translateKeeps) translateRecall.remember(transFile, translateKey);
    } else if (!transShown) translateRecall.forget(transFile);
  }, [translateRecall, transFile, translateKey, transShowing, transShown, translateKeeps]);

  // `pages` of the document on screen seeded from its kept translations
  // (translateState.seedPages, POST /api/translations/lookup, no model call),
  // only when this browser last left it showing its translation under the key
  // the host gives now, or one naming the same model through another
  // connection (translateRecall.sameModel): a paper never translated here asks
  // nothing, and a colleague's translation of it is not shown uninvited. Not
  // in a share view, nor for a page whose PDF the server keeps nothing of.
  // Resolves once the seeds under way have ended.
  function seedTrans(pages) {
    const url = transUrlRef.current, key = translateKeyRef.current;
    const doc = displayedDocRef.current;
    const lookup = cbRef.current.onTranslateLookup;
    const { pageId, file } = translateState.scopeOf(url, translateScopeRef.current);
    if (!doc || !lookup || !cbRef.current.onTranslate || docUrlRef.current !== url || !pageId || !file
      || !sameModel(translateRecallRef.current?.keyOf(file), key)) return Promise.resolve();
    return translateState.seedPages(url, {
      doc, key, pages, lookup: (texts, signal) => lookup({ pageId, texts }, signal),
      near: (pn) => transUrlRef.current === url && Math.abs(pn - curPageRef.current) <= 2,
    });
  }
  // The page being read and those beside it, as the reader moves; the rest
  // only for an export (translateCtl.seedAll).
  const transRunning = transView.status.running;
  useEffect(() => {
    if (!pdfDoc || transRunning) return;
    const id = setTimeout(() => seedTrans([curPage, curPage + 1, curPage - 1]), SEED_REST_MS);
    return () => clearTimeout(id);
  }, [pdfDoc, transUrl, translateKey, transFile, curPage, transRunning, translateRecall]); // eslint-disable-line react-hooks/exhaustive-deps

  // A formula token's span on any page of the document: a token the model
  // moved across a page join points into the other page's block.
  const transSpan = useCallback((pn, i, k) => translateState.span(transUrl, pn, i, k), [transUrl]);

  // A job over `pages` of the document on screen, once pdf.js has it (not
  // while a skeleton stands in for it). It keeps the chunk and brief
  // requests it starts with, so its replies are in the language and from the
  // model its entries are kept under, and it pins the document in DOC_CACHE
  // so it can segment pages after the viewer has gone. Pages near the one
  // being read keep the decoded images their segmentation brought in; the
  // rest let them go. A document its last job lets go leaves the cache
  // when the cache is past its size. `references`: the job translates the
  // reference lists too. `focus` ({pn, i}): the job asks for that block's
  // paragraph alone, afresh (a paragraph's menu). The last job asked for on
  // each document is kept, for "Try again" after it failed; `whole`: it was
  // "Translate whole document", which asks again for every page not done.
  const lastJobRef = useRef(new Map());
  function runTransJob(pages, label, references = false, focus = null, whole = false) {
    const url = transUrlRef.current;
    const doc = displayedDocRef.current;
    const translate = cbRef.current.onTranslate;
    if (!doc || !translate || docUrlRef.current !== url) return;
    lastJobRef.current.set(url, { pages, label, references, focus, whole });
    translateState.startJob(url, {
      doc, pages, label, key: translateKeyRef.current, parallel: translateParallelRef.current, references, translate,
      focus, fresh: !!focus,
      brief: cbRef.current.onTranslateBrief || null,
      scope: translateScopeRef.current,
      near: (pn) => transUrlRef.current === url && Math.abs(pn - curPageRef.current) <= 2,
      released: trimDocs,
    });
  }

  // The page being read paints first, then outward by distance (forward
  // before backward at equal distance) — a whole-document job never keeps the
  // reader waiting on page 1.
  function nearestFirst(pages) {
    const cur = curPageRef.current;
    return pages.sort((a, b) =>
      (Math.abs(a - cur) - Math.abs(b - cur))
      || ((a < cur ? 1 : 0) - (b < cur ? 1 : 0))
      || (a - b));
  }

  // Imperative surface for the host's toolbar button + its menu, on the
  // document on screen. Halting a job aborts its requests in flight, so it
  // unwinds (and the button frees up for the next job) at once instead of
  // waiting out slow requests.
  useEffect(() => {
    if (!translateCtlRef) return;
    translateCtlRef.current = {
      halt: () => translateState.halt(transUrlRef.current),
      setShown: (shown) => translateState.setShown(transUrlRef.current, shown),
      translatePage: () => runTransJob([curPageRef.current], `p.${curPageRef.current}`),
      translateDoc: () => {
        const key = translateKeyRef.current;
        const done = translateState.entries(transUrlRef.current);
        const pages = [];
        for (let p = 1; p <= numPagesRef.current; p++) {
          const e = done.get(p);
          if (!(e && e.done && e.key === key)) pages.push(p);
        }
        runTransJob(nearestFirst(pages), "document", false, null, true);
      },
      // The pages whose reference lists stayed original, translated now.
      translateReferences: () => {
        const pages = translateState.pagesLeft(transUrlRef.current, translateKeyRef.current);
        runTransJob(nearestFirst(pages), pages.length === 1 ? `p.${pages[0]}` : "document", true);
      },
      // The pages with paragraphs that jobs under the current language and
      // model could not translate (both pages of one that a page break
      // cut), and those the last job could not read: asked for again,
      // nothing else.
      retryUntranslated: () => {
        const key = translateKeyRef.current;
        const { entries, status } = translateState.snapshot(transUrlRef.current);
        const failed = [...entries].filter(([, e]) => e.key === key && e.failed?.size).map(([p]) => p);
        const pages = [...new Set([...failed, ...(status.unread || [])])].sort((a, b) => a - b);
        runTransJob(pages, pages.length === 1 ? `p.${pages[0]}` : "document", translateState.referencesFailed(transUrlRef.current, key));
      },
      // The last job asked for on the document on screen, again: one that
      // stopped, or a paragraph's that could not be translated: its pages,
      // with its references and focus, where a page done already asks for
      // nothing. "Translate whole document" asks again for every page not done.
      rerun: () => {
        const last = lastJobRef.current.get(transUrlRef.current);
        if (!last) return;
        if (last.whole) translateCtlRef.current?.translateDoc();
        else runTransJob(last.pages, last.label, last.references, last.focus);
      },
      // A paragraph's menu (onParagraphContext), on the document `url` it was
      // opened on: the paragraph of block `i` on page `pn` asked for again,
      // alone and afresh (Translate again, Translate this paragraph), and its
      // blocks (`unit`) shown in the original here or translated again.
      translateUnit: (url, pn, i) => {
        if (url === transUrlRef.current) runTransJob([pn], `p.${pn}`, false, { pn, i });
      },
      setOriginal: (url, unit, on) => {
        if (url === transUrlRef.current) translateState.setOriginal(url, translateKeyRef.current, unit, on);
      },
      // Before an export of the document `url`: every page seeded, so a paper
      // shown from its kept translations is written whole, not only where the
      // reader went (seedTrans: nothing for a paper not shown so).
      seedAll: (url) => (url === transUrlRef.current
        ? seedTrans(Array.from({ length: numPagesRef.current }, (_, k) => k + 1)) : Promise.resolve()),
    };
    return () => { translateCtlRef.current = null; };
  }, [translateCtlRef]);

  // A translated paragraph's menu, for block `i` of page `pn`, at (`x`, `y`)
  // in the viewport: what it says of the paragraph (transSelect.js
  // paragraphMenu) goes to the host, with the paragraph's unit, whether a job
  // runs (its rows that ask for one wait for it) and, when the selection lies
  // in the paragraph, what of it Copy takes: the translation as selected, and
  // the original of the blocks it stands for (or the original as selected,
  // from the text layer). `peek`: the original is peeked at (Alt), so nothing
  // is shown in the original by the menu. `highlight`: the id of a highlight
  // on the paragraph, under the press (`at`, pt on the page) or else the
  // first, whose rows the menu carries, since its rects over the translation
  // take no pointer (PdfPage). Only a page that shows its translation under
  // the viewer's key has one.
  const openParaMenu = useCallback(({ pn, i, x, y, at = null }) => {
    const url = transUrlRef.current, key = translateKeyRef.current;
    const entry = translateState.entries(url).get(pn);
    if (!entry || entry.key !== key || !hasMenu(entry.paras[i])) return;
    const size = { width: pageWidthsRef.current[pn - 1] || FALLBACK_W, height: pageHeightsRef.current[pn - 1] || FALLBACK_H };
    const highlight = highlightOn(hlsPrevRef.current.get(pn) || [], entry.paras, i, at?.x, at?.y, size);
    const unit = translateState.unitOf(url, pn, i, key);
    const entryOf = (q) => {
      const e = translateState.entries(url).get(q);
      return e?.key === key ? e : null;
    };
    const about = paragraphMenu(unit, entryOf, (q, b, k) => translateState.span(url, q, b, k), (q) => translateState.blocks(url, q));
    const sel = window.getSelection();
    const said = sel?.toString().trim();
    const on = said ? selectionOnPage(sel, url, key) : null;
    const selection = on && on.pn === pn && on.blocks.some((b) => unit.some((t) => t.pn === pn && t.i === b))
      ? (on.translated ? { translation: said, source: blocksQuote(on.entry.paras, on.blocks).text } : { source: said })
      : null;
    cbRef.current.onParagraphContext?.({
      ...about, x, y, url, pn, i, unit, running: !!translateState.snapshot(url).status.running, peek: transPeekRef.current,
      translation: selection?.translation ?? about.translation, source: selection?.source ?? about.source, highlight,
    });
  }, []);

  // In-PDF link annotations: internal destinations jump within the document.
  async function goToDest(dest) {
    if (!pdfDoc) return;
    try {
      const d = typeof dest === "string" ? await pdfDoc.getDestination(dest) : dest;
      if (!d || d[0] == null) return;
      const pageIdx = typeof d[0] === "object" ? await pdfDoc.getPageIndex(d[0]) : Number(d[0]);
      const pn = pageIdx + 1;
      onBeforeLinkJump?.(); // let the app capture "where I was" for global Back
      const page = await pdfDoc.getPage(pn);
      const vp = page.getViewport({ scale: 1 });
      // Destination y is in PDF user space (origin bottom-left); flip to top-down.
      let destY = 0;
      const kind = d[1]?.name;
      const rawY = kind === "XYZ" ? d[3] : (kind === "FitH" || kind === "FitBH") ? d[2] : null;
      if (typeof rawY === "number") destY = Math.max(0, vp.height - rawY);
      scrollToPositionRef.current?.({
        position: { pageNumber: pn, width: vp.width, height: vp.height, boundingRect: { x1: 0, y1: destY, x2: 0, y2: destY } },
      });
    } catch {}
  }
  const goToDestRef = useRef(null);
  goToDestRef.current = goToDest;
  const goToDestStable = useMemo(() => (d) => goToDestRef.current?.(d), []);

  // Document outline (table of contents). Loaded per document; the toggle
  // button only appears when the PDF actually has one.
  const [outline, setOutline] = useState(null);
  const [outlineOpen, setOutlineOpen] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setOutline(null);
    if (!pdfDoc) return;
    pdfDoc.getOutline()
      .then((o) => { if (!cancelled && o && o.length) setOutline(o); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [pdfDoc]);
  // Clicking anywhere outside the panel (including a TOC jump landing in the
  // document) dismisses it, like a menu.
  useEffect(() => {
    if (!outlineOpen) return;
    function onDown(e) {
      if (e.target.closest?.(".pdfOutlinePanel") || e.target.closest?.(".pdfOutlineBox")) return;
      setOutlineOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [outlineOpen]);

  // Text selection for highlight creation. The popup keeps where the viewer
  // stood when it opened (`scroll`, [left, top]), for the scroll that closes
  // it (onScroll below).
  const [selPopup, setSelPopup] = useState(null);
  const viewerScroll = () => (viewerRef.current ? [viewerRef.current.scrollLeft, viewerRef.current.scrollTop] : null);

  // The area key (Ctrl, ⌘ on a Mac, by default) held when annotating is
  // allowed → crosshair over the pages: the cue that dragging now draws an
  // area note instead of selecting text.
  const canAnnotate = !!onSelectionFinished;
  const [areaCursor, setAreaCursor] = useState(false);
  useEffect(() => {
    if (!canAnnotate) return;
    const kd = (e) => { if (isHeldKey(e, areaKey)) setAreaCursor(true); };
    const ku = (e) => { if (isHeldKey(e, areaKey)) setAreaCursor(false); };
    const off = () => setAreaCursor(false);
    window.addEventListener("keydown", kd);
    window.addEventListener("keyup", ku);
    window.addEventListener("blur", off);
    return () => {
      window.removeEventListener("keydown", kd);
      window.removeEventListener("keyup", ku);
      window.removeEventListener("blur", off);
      setAreaCursor(false);
    };
  }, [canAnnotate, areaKey]);

  // A finished Ctrl+drag on a page: hold the rect (drawn by that page while
  // the popup is up) and offer the same color tip as a text selection. The
  // drawn region also acts as a chat selection — its page and box go to the
  // host as soon as it is drawn (the server renders the region), like text
  // selections attach on mouseup, whether or not a
  // note is then created. In move mode (App's highlight waiting for a new
  // place) the region is only that place and goes nowhere else.
  const moveModeRef = useRef(moveMode);
  moveModeRef.current = moveMode;
  // The highlighter's colour and the confirm it skips the popup with, for
  // the selection listeners below, which subscribe once.
  const highlighterRef = useRef(highlighter);
  highlighterRef.current = highlighter;
  const confirmRef = useRef(null);
  confirmRef.current = confirmSelection;
  const onAreaSelected = useCallback((sel) => {
    setSelPopup({ kind: "area", ...sel, scroll: viewerScroll() });
    // The region goes to the chat as its page and box (chat/chatPictures.js
    // regionPicture); the server draws it from the document.
    if (!moveModeRef.current) cbRef.current.onAreaSelection?.(sel);
  }, []);
  // A pen's press on a page (PdfPage penDrag) clears what a mouse's would,
  // since it sends no mousedown and moves no focus: an open note would keep
  // the keyboard up and write its own selection over the pen's. Its drag
  // over the text goes to the selection listeners below.
  const penSelectRef = useRef(null);
  const onPenSelect = useCallback((e) => penSelectRef.current?.(e), []);
  const onPenPress = useCallback(() => {
    setSelPopup(null);
    setOutlineOpen(false);
    document.activeElement?.blur?.();
    window.getSelection()?.removeAllRanges();
  }, []);

  // Dismiss the color popup when the user mouses down anywhere outside it
  // (without that, removing the textarea/Cancel leaves no way to back out).
  // The primary button only: a right press keeps the selection for the menu
  // its contextmenu opens.
  useEffect(() => {
    if (!selPopup) return;
    function onDown(e) {
      if (e.button !== 0) return;
      const popup = document.querySelector(".plainTip");
      if (popup && popup.contains(e.target)) return;
      setSelPopup(null);
      window.getSelection()?.removeAllRanges();
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [selPopup]);

  // Subscribed once while annotating is allowed: the callback is a fresh
  // closure on every App render, and re-subscribing on it would drop the
  // pending selectionchange timer below.
  useEffect(() => {
    if (!canAnnotate) return;
    // A mouse selection's popup opens where the button was released, when
    // that is near the selection (pdf/selectionTip.js), once the mouse moves
    // on from there (MOVE_TO_SHOW): a double or triple click never has it
    // jump between its presses, and the text stays bare until the hand
    // moves. A selection with no release of the mouse by it (a finger's, a
    // pen's, which may not hover, a script's) has it at once. A press
    // inside the popup (Translate, the translation's header and copy button
    // keep it open) leaves it where it is: rebuilt from that release, it
    // would move.
    const waiter = tipWaiter((tip) => {
      // Placed on the screen: a scroll since then would leave it over other
      // text. And the text selection it is for may be gone (a key).
      const at = viewerScroll();
      if (at && tip.scroll && (Math.abs(at[0] - tip.scroll[0]) >= 1 || Math.abs(at[1] - tip.scroll[1]) >= 1)) return;
      if (tip.kind !== "made" && !window.getSelection()?.toString().trim()) return;
      setSelPopup(tip);
    });
    function showTip(tip) {
      if (tip.point && lastPointer() === "mouse") waiter.wait(tip);
      else setSelPopup(tip);
    }
    function onMouseMove(e) { if (!e.buttons) waiter.move(e.clientX, e.clientY); }
    // A right press opens a menu (the paragraph's, a highlight's, the
    // browser's) and keeps the selection, whose Copy rows read it: the popup
    // goes, and does not come back, since it would cover the menu's first rows
    // (it lies above every menu) at a place the menu has made stale.
    function onMouseDown(e) {
      waiter.press();
      if (e.button === 2 && !document.querySelector(".plainTip")?.contains(e.target)) setSelPopup(null);
    }
    // The primary button's release alone ends a selection.
    function onMouseUp(e) {
      if (e.button !== 0) return;
      if (document.querySelector(".plainTip")?.contains(e.target)) return;
      const point = { x: e.clientX, y: e.clientY };
      setTimeout(() => syncSelPopup(point), 10);
    }
    // `point`: where a mouse was released; `released`: a release of the
    // mouse or the pen ended the selection.
    function syncSelPopup(point = null, released = !!point) {
      // "No text selected" must not dismiss an area popup — the drag that
      // created it ends in a mouseup with an empty selection by design — nor
      // the bar of a highlight the highlighter made, which took the selection.
      const keepArea = (p) => (p && (p.kind === "area" || p.kind === "made") ? p : null);
      waiter.drop();
      const sel = window.getSelection();
      // A click or selection inside the popup (its translation text) moves
      // the document selection there — the popup stays, with the snapshot
      // of the PDF selection it was opened for.
      if (sel?.anchorNode && document.querySelector(".plainTip")?.contains(sel.anchorNode)) return;
      if (!sel || !sel.toString().trim()) { setSelPopup(keepArea); return; }
      const range = sel.getRangeAt(0);
      if (!range) { setSelPopup(keepArea); return; }
      // A selection with an end in translated text stands for the blocks it
      // touches (transSelect.js); any other is the text layer's own.
      const translated = inTranslation(sel.anchorNode) || inTranslation(sel.focusNode);
      const found = translated ? translatedSelection(sel) : layerSelection(range, sel.toString().trim());
      if (!found) { setSelPopup(keepArea); return; }
      // Snapshot the page's offset/size NOW: the confirm click may come after
      // a scroll or zoom, when re-measuring the page would no longer agree
      // with these viewport-space selection rects.
      const { pageEl, text, lineRects, copy = null } = found;
      const pageNumber = parseInt(pageEl.dataset.page, 10);
      const r = found.rect || (lineRects.length ? unionRect(lineRects) : null);
      if (!text || !pageNumber || !r) return;
      const pageRect = pageEl.getBoundingClientRect();
      const pageW = pageRect.width || pageEl.offsetWidth || 1;
      const pageH = pageRect.height || pageEl.offsetHeight || 1;
      const rect = { top: r.top, left: r.left, width: r.width, bottom: r.bottom };
      // What the reader sees selected, which the popup hangs from: over a
      // translation the reflowed text's rects, not the original lines kept
      // (the first part of a paragraph a column break cut lies in the other
      // column).
      const seen = found.shown ? { top: found.shown.top, left: found.shown.left, width: found.shown.width, bottom: found.shown.bottom } : rect;
      // On a page that shows its translation, the paragraph whose menu the
      // popup's ⋯ opens: the one the selection starts in (transSelect.js).
      const on = found.on || (cbRef.current.onParagraphContext ? selectionOnPage(sel, transUrlRef.current, translateKeyRef.current) : null);
      const para = on && [on.from, ...on.blocks].find((b) => b >= 0 && hasMenu(on.entry.paras[b]));
      const snap = {
        text, rect, point: nearRelease(point, seen), lineRects, pageNumber,
        pageLeft: pageRect.left, pageTop: pageRect.top, pageW, pageH, scroll: viewerScroll(),
        ...(found.shown ? { shown: seen } : null),
        ...(translated ? { translated: true, copy } : null),
        ...(para != null ? { para: { pn: on.pn, i: para } } : null),
      };
      // A selection in a translation stands for whole blocks, so one over a
      // paragraph highlighted from it before is that highlight: its bar, as
      // the highlighter's for a highlight it made, and no second one.
      const had = found.same && !moveModeRef.current ? found.same : null;
      if (had) {
        if (released && highlighterRef.current) window.getSelection()?.removeAllRanges();
        if (cbRef.current.onHighlightAction) showTip({ ...snap, kind: "made", id: had });
        else if (released && highlighterRef.current) setSelPopup(null);
        else showTip(snap);
        return;
      }
      // The highlighter: a release of the mouse or the pen ends the
      // selection, which is highlighted then; the highlight it made gets
      // a bar of its own ("made": its colour, comment, link), shown as
      // the popup would be. A tap after a finger's selection also sends a
      // mouseup; that one still gets the popup.
      if (released && highlighterRef.current && !moveModeRef.current && (!point || lastPointer() !== "touch")) {
        const id = confirmRef.current(snap, "", null, { highlighter: true });
        if (id && cbRef.current.onHighlightAction) showTip({ ...snap, kind: "made", id });
        return;
      }
      showTip(snap);
    }
    // A selection of the text layer's: {pageEl, text, lineRects, rect}, rects
    // in viewport px, one per line. pdf.js's text layer has many spans per
    // line and getClientRects() gives one rect per span, so those that share
    // a line merge. A span standing down under a translation (.transMasked)
    // is not selected, but the range still has its rects: they go, so a drag
    // between two blocks left original over a translated one between them
    // highlights those two alone.
    function layerSelection(range, text) {
      const node = range.startContainer;
      const textEl = (node?.nodeType === 3 ? node.parentElement : node)?.closest?.(".textLayer");
      const pageEl = textEl?.closest?.("[data-page]");
      if (!pageEl) return null;
      const masked = Array.from(textEl.querySelectorAll(".transMasked"))
        .filter((s) => range.intersectsNode(s)).map((s) => s.getBoundingClientRect());
      const under = (cr) => {
        const x = (cr.left + cr.right) / 2, y = (cr.top + cr.bottom) / 2;
        return masked.some((m) => x >= m.left && x <= m.right && y >= m.top && y <= m.bottom);
      };
      const raw = Array.from(range.getClientRects())
        .filter(cr => cr.width > 1 && cr.height > 1 && !under(cr))
        .map(cr => ({ top: cr.top, left: cr.left, right: cr.right, bottom: cr.bottom }))
        .sort((a, b) => a.top - b.top || a.left - b.left);
      const lineRects = [];
      for (const cr of raw) {
        const last = lineRects[lineRects.length - 1];
        if (last) {
          const overlap = Math.min(last.bottom, cr.bottom) - Math.max(last.top, cr.top);
          const minH = Math.min(last.bottom - last.top, cr.bottom - cr.top);
          if (overlap >= minH * 0.5) {
            last.left = Math.min(last.left, cr.left);
            last.right = Math.max(last.right, cr.right);
            last.top = Math.min(last.top, cr.top);
            last.bottom = Math.max(last.bottom, cr.bottom);
            continue;
          }
        }
        lineRects.push({ ...cr });
      }
      return { pageEl, text, lineRects, rect: masked.length ? null : range.getBoundingClientRect() };
    }
    // A selection with an end in translated text, on the page its anchor is
    // on: the whole blocks from the block under one end to the block under
    // the other (an end in the original's text, a block left original, is
    // its block), with every part of a paragraph a column break cut
    // (transSelect.js). Its rects are those blocks' ORIGINAL lines and its
    // text their original text, so a highlight made from it lies where they
    // are with the paper's own words, the translation hidden or not; the
    // reflowed translation's rects would mean nothing then. `copy`: what was
    // selected as it reads, the translation. An end on another page takes
    // this page as far as its body text goes that way (selectionOnPage).
    // `shown`: the rect round what is seen selected, which the popup hangs
    // from. `same`: the id of a highlight on the page that lies on exactly
    // these lines, made from such a selection before (transSelect.js
    // sameHighlight).
    function translatedSelection(sel) {
      const on = selectionOnPage(sel, transUrlRef.current, translateKeyRef.current);
      if (!on?.blocks.length) return null;
      const { pageEl, pn, entry, blocks } = on;
      const { rects, text } = blocksQuote(entry.paras, blocks);
      const pageRect = pageEl.getBoundingClientRect();
      const ptW = Number(pageEl.dataset.ptWidth) || 1;
      const k = pageRect.width / ptW;
      const lineRects = rects.map((q) => ({
        left: pageRect.left + q.x1 * k, top: pageRect.top + q.y1 * k, right: pageRect.left + q.x2 * k, bottom: pageRect.top + q.y2 * k,
      }));
      const drawn = drawnRects(sel.getRangeAt(0));
      const size = { width: ptW, height: pageRect.height / k };
      return {
        pageEl, text, lineRects, copy: sel.toString().trim(), on,
        shown: drawn.length ? unionRect(drawn) : null,
        same: sameHighlight(hlsPrevRef.current.get(pn) || [], rects, size),
      };
    }
    // The iPad app's own edit menu (Copy, Look Up, Translate, …) opens where
    // the popup does and covers it: under a host that can turn it off
    // (`editMenu`), it is off while the selection is the PDF's text, and on
    // again once the selection is anything else (a note's, none). Said on
    // every change, not after the popup's wait, so the menu is off before
    // it would open; the popup carries Copy in its place.
    const hostMenu = Boolean(nativeHost()?.editMenu);
    let menuOff = false;
    function syncHostMenu() {
      const off = inTextLayer(window.getSelection());
      if (!hostMenu || off === menuOff) return;
      menuOff = off;
      postToHost({ type: "editMenu", shown: !off }).catch(() => {});
    }
    // iPadOS/iOS never fires mouseup for a long-press selection or for a drag
    // of the selection handles, so touch devices would never get the highlight
    // popup. selectionchange does fire — debounced, since it fires on every
    // pixel of a handle drag — and only after a press that was not the
    // mouse's, so mouse drags keep committing on mouseup (where the modifier
    // key is known), and a pen's drag on its lift.
    let selTimer = null;
    function onSelectionChange() {
      syncHostMenu();
      if (lastPointer() === "mouse") return;
      clearTimeout(selTimer);
      if (!penSelecting()) selTimer = setTimeout(() => syncSelPopup(), 350);
    }
    // A scroll closes the popup (the viewer's onScroll), and a touch scroll
    // keeps the selection: the popup opens again where it is once the
    // scroll stops, as the system's menu does, which may be off.
    const scroller = viewerRef.current;
    function onScroll() {
      if (lastPointer() === "mouse" || penSelecting() || !inTextLayer(window.getSelection())) return;
      clearTimeout(selTimer);
      selTimer = setTimeout(() => syncSelPopup(), 350);
    }
    // A pen's drag over the text: the chat hears of the selection as at a
    // mouse's release, and the popup or the highlighter takes it, or the
    // popup alone from a drag the system cancelled.
    penSelectRef.current = (e) => beginPenSelect(e, scroller, (released) => {
      cbRef.current.onPenSelection?.();
      syncSelPopup(null, released);
    });
    document.addEventListener("mouseup", onMouseUp);
    document.addEventListener("mousemove", onMouseMove, { passive: true });
    document.addEventListener("mousedown", onMouseDown, true);
    document.addEventListener("selectionchange", onSelectionChange);
    scroller?.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      clearTimeout(selTimer);
      penSelectRef.current = null;
      document.removeEventListener("mouseup", onMouseUp);
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mousedown", onMouseDown, true);
      document.removeEventListener("selectionchange", onSelectionChange);
      scroller?.removeEventListener("scroll", onScroll);
      if (menuOff) postToHost({ type: "editMenu", shown: true }).catch(() => {});
    };
  }, [canAnnotate]);

  // The zoom window: on a page of this document only, with the pdf.js page
  // it draws from once that has loaded. As the window moves, the scroller
  // keeps its box on the page in view (revealDelta: only as far as it must).
  const zoom = ink.zoom && pdfDoc && onZoomChange && ink.zoom.page >= 1 && ink.zoom.page <= numPages ? ink.zoom : null;
  const [zoomPage, setZoomPage] = useState(null); // {doc, n, page}
  useEffect(() => {
    if (!zoom || (zoomPage?.doc === pdfDoc && zoomPage.n === zoom.page)) return undefined;
    let cancelled = false;
    pdfDoc.getPage(zoom.page).then((page) => { if (!cancelled) setZoomPage({ doc: pdfDoc, n: zoom.page, page }); }, () => {});
    return () => { cancelled = true; };
  }, [pdfDoc, zoom?.page]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const root = viewerRef.current, width = zoom && pageWidthsRef.current[zoom.page - 1];
    const wrap = width && root?.querySelector(`.pdfPageWrap[data-page="${zoom.page}"]`);
    if (!wrap) return;
    const r = wrap.getBoundingClientRect(), k = r.width / width, v = root.getBoundingClientRect();
    const { dx, dy } = revealDelta(
      { left: r.left + zoom.x * k, top: r.top + zoom.y * k, right: r.left + (zoom.x + zoom.w) * k, bottom: r.top + (zoom.y + zoom.h) * k },
      { left: v.left + root.clientLeft, top: v.top + root.clientTop, right: v.left + root.clientLeft + root.clientWidth,
        bottom: v.top + root.clientTop + root.clientHeight });
    if (dx || dy) root.scrollBy({ left: dx, top: dy });
  }, [zoom?.page, zoom?.x, zoom?.y, zoom?.w, zoom?.h]); // eslint-disable-line react-hooks/exhaustive-deps

  // A selection to onSelectionFinished: the popup's (`sel` its state), or
  // the one the highlighter takes without it. A text selection returns
  // what onSelectionFinished does: the id of the highlight made, if one was.
  function confirmSelection(sel, commentText, color, extra) {
    if (sel.kind === "area") {
      // Rect is already page-relative at capture-time render size. area: true
      // rides inside the position, so it flows through block storage,
      // rendering, and PDF export (/Square) without extra plumbing.
      const { pageNumber, rect, width, height } = sel;
      const position = { pageNumber, width, height, boundingRect: { ...rect }, rects: [{ ...rect }], area: true };
      onSelectionFinished(position, "", () => setSelPopup(null), { color, commentText, ...(extra || {}) });
      return;
    }
    const r = sel.rect;
    // Use the page offset/size snapshotted with the selection — the current
    // page position may have drifted (scroll/zoom) since the rects were taken.
    const curW = sel.pageW, curH = sel.pageH;
    const px = sel.pageLeft, py = sel.pageTop;
    const x1 = r.left - px, y1 = r.top - py;
    const x2 = r.left + r.width - px, y2 = r.bottom - py;
    const lineRects = (sel.lineRects && sel.lineRects.length)
      ? sel.lineRects.map(lr => ({ x1: lr.left - px, y1: lr.top - py, x2: lr.right - px, y2: lr.bottom - py }))
      : [{ x1, y1, x2, y2 }];
    // The page size once: every rect is in its frame (shared/model/blockModel.js).
    const position = { pageNumber: sel.pageNumber, width: curW, height: curH, boundingRect: { x1, y1, x2, y2 }, rects: lineRects };
    return onSelectionFinished(position, sel.text, () => { window.getSelection()?.removeAllRanges(); setSelPopup(null); }, { color, commentText, ...(extra || {}) });
  }
  function handleSelConfirm(commentText, color, extra) {
    if (selPopup) confirmSelection(selPopup, commentText, color, extra);
  }
  // The bar over a highlight the highlighter made acts on that highlight.
  const made = selPopup?.kind === "made" ? selPopup.id : null;
  function madeAction(action, value) {
    setSelPopup(null);
    cbRef.current.onHighlightAction?.(made, action, value);
  }

  return (
    <div style={{ position: "relative", height: "100%" }}>
      {outline ? (
        <div className="pdfCtlBox pdfOutlineBox" data-guide="viewer.outline">
          <button
            className={outlineOpen ? "modeActive" : ""}
            onClick={() => setOutlineOpen((o) => !o)}
            title={outlineOpen ? t("Hide table of contents") : t("Table of contents")}
            aria-label={t("Toggle table of contents")}
            type="button"
          >
            <OutlineIcon size={16} />
          </button>
        </div>
      ) : null}
      {numPages > 0 ? (
        <div className="pdfPageWidget" title={t("Type a page number and press Enter to jump")}>
          <input
            type="text"
            inputMode="numeric"
            value={pageInput ?? String(curPage)}
            style={{ width: `${Math.max(1, (pageInput ?? String(curPage)).length)}ch` }}
            onFocus={(e) => { setPageInput(String(curPage)); e.target.select(); }}
            onChange={(e) => setPageInput(e.target.value.replace(/[^0-9]/g, ""))}
            onKeyDown={(e) => {
              if (composing(e)) return;
              if (e.key === "Enter") {
                jumpToPage(parseInt(pageInput, 10));
                e.currentTarget.blur();
                e.stopPropagation();
              } else if (e.key === "Escape") {
                e.currentTarget.blur();
                e.stopPropagation();
              }
            }}
            onBlur={() => setPageInput(null)}
            aria-label={t("Current page")}
          />
          <span className="pdfPageTotal">/ {numPages}</span>
        </div>
      ) : null}
      {outline && outlineOpen ? (
        <div className="pdfOutlinePanel">
          {outline.map((it, i) => (
            <OutlineNode key={i} item={it} depth={0} onDest={goToDestStable} onUrl={stableCbs.onExternalLink} />
          ))}
        </div>
      ) : null}
      {/* overflow-anchor off: the browser's own scroll anchoring would fight
          the zoom re-placement above with adjustments of its own. The
          scrollbar's room stays reserved while nothing scrolls, so the
          pages are as wide with the scrollbar as without it: the fit-width
          worked out in an empty viewer (a document back from DOC_CACHE is
          committed before its pages are in) is the fit its pages keep. A
          scrollbar arriving with them would fit them a second time, which
          the zoom anchor re-places about the viewport's centre, and each
          return to the tab would land a few px higher. */}
      <div ref={viewerRef} data-guide="pdf.viewer" className={"pdfViewer" + (areaCursor || areaMode ? " areaCursor" : "") + (areaMode ? " areaMode" : "") + (darkPage ? " pdfDark" : "") + (transPeek ? " transPeek" : "") + armedClasses(tools) + (ink.tool?.tool === "select" ? " inkSelect" : "") + (moveMode ? " pdfMoving" : "")}
        style={{ height: zoom ? `calc(100% - ${zoomHeight}px)` : "100%", overflowY: "auto", overflowX: "auto", overflowAnchor: "none", scrollbarGutter: "stable",
          // the selection in the colour the highlighter will give it (app.css)
          ...(highlighter && !moveMode ? { "--selection-color": highlighter } : null) }}
        onScroll={(e) => {
          lastScrollRef.current = e.currentTarget.scrollTop;
          lastScrollLeftRef.current = e.currentTarget.scrollLeft;
          syncCurPage();
          // The tip is placed on the screen, not on the page, so a scroll
          // would leave it over other text; and a touch scroll sends none of
          // the mousedown that otherwise closes it. A scroll event that
          // leaves the viewer where it stood when the tip opened (one a
          // re-layout of the pages sends) moves nothing under it.
          const at = selPopup?.scroll, el = e.currentTarget;
          if (selPopup && (!at || Math.abs(el.scrollLeft - at[0]) >= 1 || Math.abs(el.scrollTop - at[1]) >= 1)) setSelPopup(null);
        }}>
      {/* pdfZoomLayer: the pinch preview's transform target — spans the full
          scroll content so transform-origin 0 0 coincides with content (0,0).
          It carries the committed zoom (SCALE_VAR), which the page boxes
          size with, so a zoom re-lays-out the pages without rendering them. */}
      <div ref={zoomLayerRef} className="pdfZoomLayer" style={{ [SCALE_VAR]: String(scale) }}>
      <CommentMarginContext.Provider value={commentMargin}>
      <MarginStackContext.Provider value={marginStack}>
      {Array.from({ length: numPages }, (_, i) => (
        <PdfPage key={`${docSeq}-${i + 1}`} pageNumber={i + 1} pdfDoc={pdfDoc} zoom={pageScale}
          baseWidth={pageWidths[i] || 0} baseHeight={pageHeights[i] || 0}
          marginLeft={withMargin ? pageMargin(i + 1) : null} pageTop={pageTops[i] || 0} marginScale={withMargin ? scale : 0}
          activeHl={targetPage === i + 1 ? activeHl : null}
          highlights={hlsByPage.get(i + 1) || EMPTY_MARKS} anchors={anchorsByPage.get(i + 1) || EMPTY_MARKS} onJump={stableCbs.onJump}
          // what a highlight, a link highlight or an outside link does on a
          // press, only when this viewer was told: else none, and the press
          // goes on to the page (a tab group's view, PagePane)
          onHighlightJump={onHighlightJump ? stableCbs.onHighlightJump : undefined}
          flashId={flashHighlightId && hlsByPage.get(i + 1)?.some((h) => h.id === flashHighlightId) ? flashHighlightId : null}
          onLinkHighlight={onLinkHighlight ? stableCbs.onLinkHighlight : undefined} onHighlightContext={stableCbs.onHighlightContext}
          onHighlightThread={stableCbs.onHighlightThread} onReview={review}
          reviewGhost={reviewGhost?.pageNumber === i + 1 ? reviewGhost : null}
          citation={displayedUrl === url && activeCitation?.page === i + 1 ? activeCitation : null}
          readOnly={!onSelectionFinished} forceRender={forcePages.has(i + 1) || activeCitation?.page === i + 1}
          hideEmbeddedAnnots={!!hideEmbeddedAnnots}
          areaMode={canAnnotate ? !!areaMode : false} areaKey={areaKey}
          onAreaSelected={canAnnotate ? onAreaSelected : undefined} onPenPress={onPenPress} onPenSelect={onPenSelect}
          pendingArea={selPopup?.kind === "area" && selPopup.pageNumber === i + 1 ? selPopup : null}
          findMarks={marksByPage.get(i + 1) || EMPTY_MARKS}
          trans={transMap.get(i + 1) || null}
          transKey={translateKey}
          transShown={transShown}
          transSpan={transSpan}
          onParaMenu={onParagraphContext && onTranslate ? openParaMenu : null}
          onInternalLink={goToDestStable}
          onExternalLink={onExternalLink ? stableCbs.onExternalLink : undefined}
          onLinkContext={stableCbs.onLinkContext}
          onPainted={onPagePainted}
          toolArmed={!!ink.tool || text.armed}
          // same document guard as the highlights
          marks={(displayedUrl === url && marks.get(i + 1)) || NO_MARKS}
        />
      ))}
      </MarginStackContext.Provider>
      </CommentMarginContext.Provider>
      </div>
      {selPopup && onSelectionFinished && (
        <TipFrame anchor={tipAnchor(selPopup)}>
          {/* keyed by the selection: a new one starts with no translation */}
          <PlainTip key={selPopup.kind === "area" ? "area" : made ? `made:${made}` : selPopup.text} recolor={!!made}
            area={selPopup.kind === "area"}
            onDismiss={() => { if (!made) window.getSelection()?.removeAllRanges(); setSelPopup(null); }}
            onConfirm={made ? (_, color, extra) => madeAction("mark", { color, style: extra?.style }) : handleSelConfirm}
            // a style pressed on the highlighter's highlight restyles it, the bar staying for a colour
            onRestyle={made ? (style) => cbRef.current.onHighlightAction?.(made, "style", style) : null}
            onLink={made ? () => madeAction("link") : () => handleSelConfirm("", null, { link: true })}
            onComment={selComment ? (made ? () => madeAction("comment") : (color) => handleSelConfirm("", color, { comment: true })) : null}
            onMove={moveMode ? () => handleSelConfirm("", null) : null}
            // the system's Copy, where the host turns its menu off for this popup:
            // what was selected, the translation's words over a translation
            copy={nativeHost()?.editMenu && selPopup.kind !== "area" ? (selPopup.copy ?? selPopup.text) : null}
            // the paragraph menu, on a page that shows its translation
            menu={selPopup.para && !moveMode && onParagraphContext && onTranslate
              ? (e) => openParaMenu({ ...selPopup.para, x: e.clientX, y: e.clientY }) : null}
            // a selection that places a moved highlight is not read, so not
            // translated; nor is one in a translation, which is translated already
            translate={!moveMode && selTranslate && selPopup.kind !== "area" && !selPopup.translated && selPopup.text && onTranslate ? {
              text: selPopup.text, auto: selTranslate === "auto", langLabel: translateLangLabel, lang: langOfKey(translateKey),
              // A quick one-off text: the paper's brief rides along when it is
              // made already, never waited for. The page is the selected
              // document's, none while the next one loads.
              // The settings' own request: a paper shown under another key
              // (App's paperKey) does not change what a selection asks.
              run: (texts, signal, onPartial) => (cbRef.current.onTranslateSelection || cbRef.current.onTranslate)?.(texts, signal, onPartial,
                { pageId: translateState.scopeOf(transUrlRef.current, translateScopeRef.current).pageId, waitBrief: false }),
              // the workspace's glossary, for a writer of it
              addTerm: onAddTerm,
            } : null} />
        </TipFrame>
      )}
      </div>
      <HighlightReview card={reviewCard} steps={reviewSteps} review={review} drawn={reviewDrawn}
        highlights={displayedUrl === url ? highlights : null} positionOf={reviewPosition} noteOf={reviewNote}
        viewerRef={viewerRef} darkPage={darkPage} fileId={fileId} pageCount={displayedUrl === url ? numPages : 0}
        onAccept={onAcceptHighlight} onPlace={onPlaceHighlight}
        onMove={onMoveHighlight} onDiscard={onDiscardHighlight} discardOf={discardOf}
        onUndo={onUndoReview} onOpen={openReview} onShow={showClosest} />
      {zoom ? (
        <ZoomWindow zoom={zoom} size={{ width: pageWidths[zoom.page - 1] || FALLBACK_W, height: pageHeights[zoom.page - 1] || FALLBACK_H }}
          page={zoomPage?.doc === pdfDoc && zoomPage.n === zoom.page ? zoomPage.page : null}
          marks={(displayedUrl === url && marks.get(zoom.page)) || NO_MARKS}
          height={zoomHeight} onHeight={(px) => onZoomHeight?.(px)} onChange={onZoomChange} onClose={() => onZoomChange(() => null)}
          annotationMode={pdfjsLib ? (hideEmbeddedAnnots ? pdfjsLib.AnnotationMode.DISABLE : pdfjsLib.AnnotationMode.ENABLE) : undefined}
          isCancel={(err) => !!pdfjsLib && err instanceof pdfjsLib.RenderingCancelledException} dark={darkPage} />
      ) : null}
    </div>
  );
}

// One outline entry: click jumps to its destination, chevron collapses its
// children. Top-level sections start expanded, deeper levels collapsed.
// Children nest inside a wrapper whose left border draws the indent guide.
function OutlineNode({ item, depth, onDest, onUrl }) {
  const [open, setOpen] = useState(depth === 0);
  const kids = item.items || [];
  return (
    <div className="pdfOutlineNode">
      <div className="pdfOutlineRow">
        {kids.length ? (
          <button
            className="pdfOutlineChevron"
            onClick={() => setOpen((o) => !o)}
            aria-label={open ? t("Collapse section") : t("Expand section")}
            type="button"
          >
            <Chevron open={open} dir="right" size={10} />
          </button>
        ) : (
          <span className="pdfOutlineChevron" />
        )}
        <span
          className="pdfOutlineTitle"
          title={item.title}
          onClick={() => {
            if (item.dest) onDest(item.dest);
            else if (item.url) onUrl?.(item.url);
          }}
        >
          {item.title}
        </span>
      </div>
      {kids.length ? (
        <Collapse open={open}>
          <div className="pdfOutlineKids">
            {kids.map((k, i) => (
              <OutlineNode key={i} item={k} depth={depth + 1} onDest={onDest} onUrl={onUrl} />
            ))}
          </div>
        </Collapse>
      ) : null}
    </div>
  );
}

// Speech-bubble badge on a highlight that carries a typed note. The hover
// tooltip renders the note's markdown + KaTeX — a native title attribute is
// plain-text only. Portaled to <body> with fixed coordinates so the pinch
// transform on .pdfZoomLayer (an ancestor transform makes position:fixed
// resolve against it, not the viewport) can never misplace it.
// .pdfNoteTip's CSS max-width is min(NOTE_TIP_W, 100vw - 2·VIEWPORT_PAD) —
// keep these in sync with app.css.
const NOTE_TIP_W = 520;
const VIEWPORT_PAD = 8;
// `threads`: the highlight's open comment threads. Such a badge is drawn in
// the accent and its click (a tap too) also opens them (`onThread(badge)`),
// besides the jump; its tip counts them under the note.
function NoteBadge({ hlId, text, hasNote = true, threads = 0, style, onClick, onContextMenu, onThread }) {
  const [tip, setTip] = useState(null);
  const btnRef = useRef(null);
  const tipRef = useRef(null);
  const timerRef = useRef(0);
  // A press that was not the mouse's (shared/lib/pointer.js). Touch has no
  // hover, so a tap opens the tip instead of jumping to the note — the
  // notes panel it would jump to isn't even on screen in the phone layout.
  const tapped = () => lastPointer() !== "mouse";
  const place = () => {
    const r = btnRef.current?.getBoundingClientRect();
    if (!r) return;
    const below = r.top < window.innerHeight * 0.45;
    // Clamp so the tip's max width fits: .pdfNoteTip caps at NOTE_TIP_W px
    // (app.css) plus VIEWPORT_PAD of breathing room on each side.
    setTip({
      left: Math.max(VIEWPORT_PAD, Math.min(r.left - 12, window.innerWidth - NOTE_TIP_W - 2 * VIEWPORT_PAD)),
      ...(below ? { top: r.bottom + 6 } : { bottom: window.innerHeight - r.top + 6 }),
    });
  };
  const show = () => {
    if (tapped()) return; // a tap fires compatibility mouse events too
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(place, 120);
  };
  // Hide on a short delay so the pointer can cross the gap into the tip;
  // entering the tip cancels it — the tip scrolls, so it must survive hover.
  const hide = () => { clearTimeout(timerRef.current); timerRef.current = setTimeout(() => setTip(null), 200); };
  const hideNow = () => { clearTimeout(timerRef.current); setTip(null); };
  const hold = () => clearTimeout(timerRef.current);
  useEffect(() => () => clearTimeout(timerRef.current), []);
  const menu = menuPress((e) => { hideNow(); onContextMenu(e); });
  // Tapped-open tip: no pointer leaves a touch screen, so it closes on the
  // next tap outside it (or on the badge again).
  useEffect(() => {
    if (!tip || !tapped()) return;
    const away = (e) => {
      if (tipRef.current?.contains(e.target) || btnRef.current?.contains(e.target)) return;
      hideNow();
    };
    document.addEventListener("pointerdown", away, true);
    return () => document.removeEventListener("pointerdown", away, true);
  }, [tip]);
  return (
    <>
      <span className="pdfNoteAnchor" style={style}>
        <button ref={btnRef} type="button" className={`pdfNoteBadge${threads ? " hasThread" : ""}`} data-hl-id={hlId}
          aria-label={threads ? tn("{n} comment", "{n} comments", threads) : t("Show highlight note")}
          {...menu}
          onMouseEnter={show} onMouseLeave={hide}
          onClick={(e) => {
            if (tapped() && !threads) { e.stopPropagation(); clearTimeout(timerRef.current); if (tip) setTip(null); else place(); return; }
            hideNow(); onClick(e);
            if (threads) onThread?.(btnRef.current);
          }}
        >
          <MessageSquareIcon size={10} />
        </button>
      </span>
      {tip ? createPortal(
        <div ref={tipRef} className="pdfNoteTip" style={tip} onMouseEnter={hold} onMouseLeave={hide}>
          {hasNote ? (text ? <ChatMarkdown text={text} /> : t("This highlight has a note")) : null}
          {threads ? <div className="pdfNoteTipThreads">{tn("{n} comment", "{n} comments", threads)}</div> : null}
        </div>,
        document.body
      ) : null}
    </>
  );
}

// marginLeft: the page's left edge beside its comment margin, or null for
// none (centred, with the badge and the popover); pageTop: the page's top,
// px from the first page's top, where its margin's column goes on from the
// pages above; marginScale: the zoom, beside a margin only (0 otherwise:
// a page outside margin mode takes no scale, so a zoom does not render it);
// activeHl: the highlight whose card is the active one, drawn stronger.
// anchors: the page's comment anchors, which its margin's cards stand beside.
// zoom: the viewer's pageScale store; baseWidth × baseHeight: the page's
// size at scale 1 as the viewer knows it, until pdf.js measures it.
const PdfPage = React.memo(function PdfPage({ citation, pageNumber, pdfDoc, zoom, highlights, anchors, flashId, onJump, onHighlightJump, onLinkHighlight, onHighlightContext, onHighlightThread, onReview, reviewGhost = null, readOnly, forceRender, baseWidth = 0, baseHeight = 0, findMarks, onInternalLink, onExternalLink, onLinkContext, onPainted, onAreaSelected, onPenPress, onPenSelect, pendingArea, areaMode, areaKey, hideEmbeddedAnnots, trans, transKey, transShown, transSpan, onParaMenu = null, toolArmed, marks, marginLeft = null, pageTop = 0, marginScale = 0, activeHl = null }) {
  const wrapRef = useRef(null);
  const baseRef = useRef(null);
  const detailRef = useRef(null);
  const rasterRef = useRef(null);
  const textRef = useRef(null);
  const textScaleRef = useRef(0); // the scale the text layer's spans were last measured at
  // The highlight made last, when it is on this page: the guide's highlight
  // tour points at it (guide/recent.js).
  const recentHl = useRecentIn("highlight", highlights);
  const [pageSize, setPageSize] = useState(null);
  const [loaded, setLoaded] = useState(null); // {doc, page}: the pdf.js page, once this one has come near the view
  const page = loaded?.doc === pdfDoc ? loaded.page : null;
  const [paintedPage, setPaintedPage] = useState(null); // the pdf.js page whose first pixels have landed
  const [basePaints, setBasePaints] = useState(0); // counts the whole-page bitmap's paints (pageRaster onBase)
  const [textReady, setTextReady] = useState(null); // {page, layer, runs}: the built pdf.js text layer
  const [visible, assumeVisible] = useWithin(wrapRef, RENDER_MARGIN, pageNumber); // inside the look-ahead: rendered
  const renderVisible = visible || forceRender;
  // Highlights, find marks and link boxes are drawn only inside the
  // look-ahead too. Each is a positioned element, which Blink gives its own
  // paint layer, and every hit test, layout and pre-paint walks all of them:
  // a book with highlights on every page paid that on each wheel notch
  // (docs/dev/pdf_loading.md, "High zoom and touch scrolling").
  const overlays = renderVisible;
  // A lost highlight's closest match outlined here (below): its review
  // card anchors to the outline once it is drawn, this page rendering
  // after Show scrolled to it.
  const ghostDrawn = overlays && !!reviewGhost;
  useEffect(() => { if (ghostDrawn) onReview?.drawn?.(); }, [ghostDrawn, reviewGhost, onReview]);
  const [links, setLinks] = useState([]); // link annotations, rects at scale 1
  // The settled zoom (pdf/pageScale.js), heard inside the look-ahead only:
  // there the text layer is re-measured at it and a translation laid out at
  // it, once the zoom stops. A live change is no new snapshot, so a wheel
  // train does not render the page at all. Away from the view nothing here
  // needs it; the page reads it afresh when it comes back.
  const restOf = useCallback((fn) => (renderVisible ? zoom.subscribe(fn) : () => {}), [zoom, renderVisible]);
  const rest = useSyncExternalStore(restOf, zoom.rest);
  const onPaintedRef = useRef(onPainted);
  onPaintedRef.current = onPainted;
  // Translation entry for this page (from pdf/translateState.js) — display
  // only; the queue and all fetching live in pdf/translateJob.js.
  const transEntry = trans && trans.key === transKey ? trans : null;

  // Farther out, up to KEEP_MARGIN, a page keeps the pixels it has (on a
  // desktop; pdf/pageRaster.js trims them to a memory budget) and draws
  // nothing new, so a scroll back over read pages redraws nothing.
  const [nearby, assumeNearby] = useWithin(wrapRef, KEEP_MARGIN, pageNumber);
  const kept = nearby || renderVisible;

  // Far from the view (FAR_MARGIN past it) a page gives up its text layer
  // too; a long read would otherwise leave every visited page's spans in
  // the DOM. It is rebuilt after the page's next paint. A page rendered on
  // request (a jump, the cited page) is never far.
  const far = !useWithin(wrapRef, FAR_MARGIN, pageNumber, true)[0] && !forceRender;

  // The page's canvases belong to the raster (pdf/pageRaster.js). This
  // component only tells it which page, at what zoom, and whether the page is
  // near enough to the view to hold pixels at all. Installed before paint,
  // so a page coming back starts, in its first frame, from what it showed
  // when its document left the viewer (pageRaster keepPixels); its own
  // first paint replaces that. Such a page was in view as it left, so it
  // counts as in view before that frame too: its highlights, badges and
  // margin cards come with its pixels, not a frame after them, when the
  // observer first reports (it takes over from there).
  useLayoutEffect(() => {
    const raster = installPageRaster({
      wrap: wrapRef.current, base: baseRef.current, detail: detailRef.current,
      onPainted: (p) => { setPaintedPage(p); onPaintedRef.current?.(); },
      // The translated view's formula crops redraw from the new pixels: this
      // page's own, and (by the event) crops of its formulas on another page.
      onBase: () => {
        setBasePaints((n) => n + 1);
        wrapRef.current?.dispatchEvent(new Event("pdfbasepaint"));
      },
      // instanceof, not err.name — minification renames the class
      isCancel: (err) => err instanceof pdfjsLib.RenderingCancelledException,
    });
    rasterRef.current = raster;
    return () => raster.dispose();
  }, []);
  useLayoutEffect(() => {
    const kept = pdfDoc ? takePixels(pdfDoc, pageNumber) : null;
    if (!kept) return;
    rasterRef.current.prime(kept);
    assumeVisible();
    assumeNearby();
  }, [pdfDoc, pageNumber]); // eslint-disable-line react-hooks/exhaustive-deps

  // The pdf.js page and its size, once this page first comes near the view.
  useEffect(() => {
    if (!pdfDoc || !renderVisible || page) return;
    let cancelled = false;
    pdfDoc.getPage(pageNumber).then((p) => {
      if (cancelled) return;
      // Base (scale-1) size — the box is that at the layer's zoom, so it
      // resizes in the same frame as a zoom change instead of keeping its
      // old size until the async redraw completes.
      const vpBase = p.getViewport({ scale: 1 });
      setPageSize({ width: vpBase.width, height: vpBase.height });
      setLoaded({ doc: pdfDoc, page: p });
    }).catch((e) => {
      // A cancelled run rejects mid-await (doc swapped, transport
      // destroyed) — that's teardown, not an error worth logging.
      if (!cancelled) console.error("PdfPage render error:", e);
    });
    return () => { cancelled = true; };
  }, [pdfDoc, pageNumber, renderVisible, page]);

  useEffect(() => {
    // A page away from the view keeps its geometry, text and overlays but
    // releases its raster backing stores. Otherwise a long reading session
    // retains every visited page. Its operator list and decoded images go
    // too (page.cleanup: pdf.js keeps them per page, a scanned page's
    // decoded bitmap among them); the next render asks the worker again.
    if (!page || !kept) {
      rasterRef.current.show(null);
      if (page) page.cleanup();
      return;
    }
    if (!renderVisible) return; // kept: holds what it has, nothing new is drawn
    // At the live zoom, heard from the store: a zoom does not render the
    // page, and the raster keeps its own settle.
    const draw = () => rasterRef.current.show(page, {
      scale: zoom.get(),
      // DISABLE keeps embedded markup annotations (e.g. highlights burned in
      // by a Gamma export, or SumatraPDF/Acrobat ones) out of the canvas so
      // they don't stack under Gamma's own overlay after an import. Link
      // regions are unaffected — they're DOM overlays from getAnnotations().
      annotationMode: hideEmbeddedAnnots ? pdfjsLib.AnnotationMode.DISABLE : pdfjsLib.AnnotationMode.ENABLE,
      pinned: !!forceRender,
    });
    draw();
    return zoom.subscribe(draw);
  }, [page, zoom, renderVisible, kept, hideEmbeddedAnnots, forceRender]);

  // The text layer and the link boxes: built once per page, after its first
  // pixels, and kept when the page scrolls away or the zoom changes. The
  // layer is laid out at scale 1 (pdf.js sizes it, under --scale-factor 1)
  // and scaled by CSS: once built it takes the zoom (SCALE_VAR), so a zoom
  // has nothing to rebuild or render. Inline, so it also overrides
  // pdf_viewer.css's rotation transforms. Until then it is pdf.js's inset 0
  // and fills the box, and 300 empty layers restyle nothing on a zoom. Only
  // a page far from the view drops it (`far`), to be built again after its
  // next paint; nothing else writes its size or transform.
  useEffect(() => {
    if (far) {
      const textL = textRef.current;
      if (textL) {
        textL.innerHTML = "";
        for (const key of ["width", "height", "transform", SCALE_VAR]) textL.style.removeProperty(key);
        textL.removeAttribute("data-main-rotation");
      }
      setTextReady(null);
      setPaintedPage(null);
      return;
    }
    if (!page || paintedPage !== page) return;
    let cancelled = false;
    let layer = null;
    // Built once the scroller rests. Measuring and laying out a page's spans
    // takes the main thread for a frame or two, which a wheel glide (JS-driven,
    // shared/lib/smoothWheel.js) spends standing still; during a fast scroll
    // the pages passing by would be built for nothing (docs/research/pdf-wheel-spin.md).
    const rested = afterScrollRest(wrapRef.current?.closest(".pdfViewer"), TEXT_REST_MS, async () => {
      try {
        const vpBase = page.getViewport({ scale: 1 });
        const textL = textRef.current;
        textL.innerHTML = "";
        const tc = await page.getTextContent();
        if (cancelled) return;
        // pdf.js sizes each span (scaleX) from a canvas measure in the PDF's
        // own language, but the spans would inherit <html lang> — the
        // interface language. Under zh-CN a generic `serif` then renders as
        // SimSun against a Times measure, every span ~25% too wide, and new
        // highlights, selections and search marks with it. An empty lang
        // resolves differently on a canvas and on an element, so "en".
        textL.lang = tc.lang || "en";
        const measuredAt = zoom.get();
        layer = new pdfjsLib.TextLayer({ textContentSource: tc, container: textL, viewport: page.getViewport({ scale: measuredAt }) });
        textL.style.setProperty(SCALE_VAR, "inherit");
        textL.style.transform = `scale(var(${SCALE_VAR}))`;
        await layer.render();
        if (cancelled) return;
        textScaleRef.current = measuredAt;
        setTextReady({ page, layer, runs: citationRuns(tc.items, layer.textDivs),
          boxes: textRuns(tc.items.filter((it) => typeof it.str === "string"), vpBase.transform) });

        // Link annotations (in-PDF references + external URLs), stored at
        // scale 1 and placed in JSX as fractions of the box.
        const annots = await page.getAnnotations();
        if (cancelled) return;
        setLinks(annots
          .filter((a) => a.subtype === "Link" && (a.url || a.dest))
          .map((a) => {
            const r = vpBase.convertToViewportRectangle(a.rect);
            return {
              left: Math.min(r[0], r[2]), top: Math.min(r[1], r[3]),
              w: Math.abs(r[2] - r[0]), h: Math.abs(r[3] - r[1]),
              url: a.url || null, dest: a.dest || null,
            };
          }));
      } catch (e) {
        if (!cancelled) console.error("PdfPage render error:", e);
      }
    });
    return () => { cancelled = true; rested(); layer?.cancel(); };
  }, [page, paintedPage, far]);

  // Once a zoom settles (`rest`) the spans are re-measured at the new size,
  // which is what pdf.js's own viewer does in place of a rebuild: the
  // selection, the spans and the citation runs all survive. Not while a zoom
  // still runs (a layer built mid-train measured at the live scale): the
  // settled value is on its way, and with it this effect.
  useEffect(() => {
    const layer = textReady?.page === page ? textReady.layer : null;
    if (!layer || !renderVisible || textScaleRef.current === rest || rest !== zoom.get()) return;
    layer.update({ viewport: page.getViewport({ scale: rest }) });
    textScaleRef.current = rest;
  }, [page, zoom, rest, renderVisible, textReady]);

  // The page at scale 1: pdf.js's measure once it has loaded, else the
  // viewer's (from the manifest skeleton, exact too, or measured), else
  // nothing yet. The box is that at the layer's zoom (SCALE_VAR), and the
  // overlays are fractions of the box, so a zoom resizes and places them
  // all without a render, on a skeleton page as well.
  const baseW = pageSize ? pageSize.width : (baseWidth > 0 ? baseWidth : undefined);
  const baseH = pageSize ? pageSize.height : (baseHeight > 0 ? baseHeight : undefined);
  const pctX = (v, of = baseW) => `${(v / (of || 1)) * 100}%`;
  const pctY = (v, of = baseH) => `${(v / (of || 1)) * 100}%`;

  // Rectangle drag (screenshot-style area note): a mouse drag with the area
  // key held alone (Ctrl, ⌘ on a Mac, by default: areaKey), or any drag
  // while the rectangle mode (areaMode) is on. Pointer
  // events cover mouse and touch with one path; document-level move/up
  // listeners so the drag survives leaving the page box; rects are clamped
  // to it. Tiny drags are clicks — ignored, so Ctrl+click on highlights
  // (additive chat quote) keeps working.
  const [marquee, setMarquee] = useState(null); // live drag rect, current-render px
  function beginAreaDrag(e) {
    if (readOnly || !onAreaSelected) return;
    if (e.target.closest?.(".commentMargin")) return; // a comment card beside the page
    if (e.button !== 0 || !e.isPrimary) return;
    const viaKey = e.pointerType === "mouse" && heldAlone(e, areaKey);
    if (!areaMode && !viaKey) return;
    const wrap = wrapRef.current;
    if (!wrap) return;
    e.preventDefault(); // keep the text layer from starting a selection
    const pointerId = e.pointerId, pen = e.pointerType === "pen";
    const box = wrap.getBoundingClientRect();
    const sx = e.clientX, sy = e.clientY;
    const clamp = (v, max) => Math.max(0, Math.min(max, v));
    const toRect = (cx, cy) => ({
      x1: clamp(Math.min(sx, cx) - box.left, box.width),
      y1: clamp(Math.min(sy, cy) - box.top, box.height),
      x2: clamp(Math.max(sx, cx) - box.left, box.width),
      y2: clamp(Math.max(sy, cy) - box.top, box.height),
    });
    const detach = () => {
      cancelAnimationFrame(moveRaf);
      document.removeEventListener("pointerdown", onOther, true);
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp, true);
      document.removeEventListener("pointercancel", onCancel);
    };
    // High-rate pointers outpace frames, and each setMarquee re-renders the
    // whole page (highlight rects, link boxes) — coalesce to one per frame.
    let moveRaf = 0, moveX = 0, moveY = 0;
    function onMove(ev) {
      if (ev.pointerId !== pointerId) return;
      moveX = ev.clientX;
      moveY = ev.clientY;
      if (moveRaf) return;
      moveRaf = requestAnimationFrame(() => {
        moveRaf = 0;
        setMarquee(toRect(moveX, moveY));
      });
    }
    function onCancel(ev) {
      if (ev.pointerId !== pointerId) return;
      detach();
      setMarquee(null);
    }
    // A second finger makes the drag a pinch: the area goes. Under a pen
    // it is a palm.
    function onOther(ev) {
      if (ev.pointerId !== pointerId && !pen) onCancel({ pointerId });
    }
    function onUp(ev) {
      if (ev.pointerId !== pointerId) return;
      detach();
      setMarquee(null);
      const r = toRect(ev.clientX, ev.clientY);
      if (r.x2 - r.x1 < 6 || r.y2 - r.y1 < 6) return;
      // Swallow the click this drag would otherwise deliver to whatever sits
      // under the mouse (highlight overlays, link boxes). The timeout clears
      // the trap if no click follows (drag released outside the window).
      const swallow = (ce) => { ce.stopPropagation(); ce.preventDefault(); };
      document.addEventListener("click", swallow, { capture: true, once: true });
      setTimeout(() => document.removeEventListener("click", swallow, { capture: true }), 0);
      // The region doubles as a chat attachment: its page and box go along,
      // and the server draws it from the document (with the handwriting on
      // it when asked) — nothing is captured off the screen.
      onAreaSelected({
        pageNumber, rect: r, width: box.width, height: box.height,
        tip: { left: box.left + r.x1, top: box.top + r.y2 + 8 },
      });
    }
    document.addEventListener("pointerdown", onOther, true);
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp, true);
    document.addEventListener("pointercancel", onCancel);
  }
  // A pen with no ink tool armed does what a mouse does on the page. Its
  // press comes from its ink layer (InkLayer.jsx onPenDrag), which holds the
  // touch for the drag, never the wrap's: the rectangle in rectangle mode,
  // else the text (pdf/penSelect.js). What takes a press of its own keeps it.
  function penDrag(e) {
    onPenPress();
    if (e.target.closest?.(".textBox, .pagePicture, .pdfNoteAnchor")) return;
    if (areaMode) beginAreaDrag(e);
    else onPenSelect(e);
  }

  // Whether translated text is on screen: gates the overlay.
  const transPartials = transEntry?.partial && Object.keys(transEntry.partial).length > 0;
  const transActive = transShown && !!transEntry && (transEntry.texts.some(Boolean) || !!transPartials);
  // Translation still arriving for this page: the overlay also renders the
  // working indicators (and streamed partial text), even before any final
  // text exists.
  const transWorking = transShown && !!transEntry && !transEntry.done
    && (transEntry.queued || transEntry.busy?.size > 0);
  // A paragraph a job could not translate: the overlay marks it.
  const transFailed = transShown && !!transEntry?.failed?.size;
  // A translated paragraph's menu (PdfViewer's openParaMenu): one handler for
  // the page, after a highlight's, a link's or a note's own menu (they call
  // preventDefault) and only where the translation shows, for the block under
  // the pointer, its overlay's (data-block) or, where the overlay draws
  // nothing over it (waiting, failed, left original, shown in the original,
  // peeked at), the block whose lines hold the point. A mouse's right-click
  // only: a finger's hold is the system's selection, whose popup has a ⋯ for
  // this (docs/dev/ui-design.md, "The PDF viewer"). Elsewhere the browser's
  // own menu stays.
  const onTransMenu = (e) => {
    if (e.defaultPrevented || !onParaMenu || !transShown || !transEntry || toolArmed || areaMode) return;
    if (lastPointer() !== "mouse" || e.target.closest?.(".textBox, .pagePicture")) return;
    const el = e.target.closest?.("[data-block]");
    const r = wrapRef.current.getBoundingClientRect();
    const k = r.width / (baseW || 1);
    const at = { x: (e.clientX - r.left) / k, y: (e.clientY - r.top) / k };
    let i = el && wrapRef.current.contains(el) ? Number(el.dataset.block) : -1;
    if (i < 0) i = menuBlockAt(transEntry, at.x, at.y);
    if (i < 0) return;
    e.preventDefault();
    onParaMenu({ pn: pageNumber, i, x: e.clientX, y: e.clientY, at });
  };
  const onFailedMenu = useCallback((i, x, y) => onParaMenu?.({ pn: pageNumber, i, x, y }), [onParaMenu, pageNumber]);
  // The original stands down only under what the overlay masks: where no
  // translation is drawn (a reference list left original, text in a figure, a
  // paragraph still waiting) it stays selectable. Per block, so text streaming
  // into a block already masked touches no span.
  // `masked` is the list as a string, a dependency that changes only with it.
  const masked = transActive || transWorking ? maskedBlocks(transEntry).join() : "";
  const maskedList = useMemo(() => (masked ? masked.split(",").map(Number) : []), [masked]);
  useLayoutEffect(() => {
    if (textReady?.page !== page) return;
    const under = masked ? maskedRuns(textReady.boxes, transEntry.paras, maskedList) : null;
    textReady.layer.textDivs.forEach((div, k) => div.classList.toggle("transMasked", !!under?.has(k)));
  }, [masked, textReady, page]); // eslint-disable-line react-hooks/exhaustive-deps
  // Link boxes and find marks are the original's, where its words are. Over
  // a translation those words have moved: a link box there would take a
  // click or a drag's start meant for the translated text, so it goes (the
  // citation marker in the translation is plain text), and a find hit there
  // marks the paragraph that holds it (transSelect.js).
  const maskedParas = transEntry?.paras;
  const shownLinks = useMemo(() => (masked ? linksOff(links, maskedParas, maskedList) : links),
    [links, masked, maskedList, maskedParas]);
  const shownMarks = useMemo(() => (masked ? marksOver(findMarks, maskedParas, maskedList) : findMarks),
    [findMarks, masked, maskedList, maskedParas]);
  // Highlights stay where the original's words are, but a rect over a block
  // with its translation drawn takes no pointer: it would take the right-click
  // (the paragraph's menu, which carries the highlight's rows), the drag that
  // selects the translation and the press of the pen. Elsewhere on the page it
  // is a highlight as any.
  const onTranslation = useMemo(() => (masked ? onBlocks(maskedParas, maskedList) : null),
    [masked, maskedList, maskedParas]);

  return (
    <div ref={wrapRef} data-page={pageNumber} data-pt-width={baseW} data-guide="pdf.page" className="pdfPageWrap"
      data-base-w={baseW || undefined}
      onPointerDown={(e) => { if (e.pointerType !== "pen") beginAreaDrag(e); }}
      onContextMenu={onTransMenu}
      style={{
        margin: marginLeft != null ? `0 0 ${PAGE_GAP}px ${marginLeft}px` : `0 auto ${PAGE_GAP}px`, position: "relative", background: "#fff",
        width: baseW ? scaledPx(baseW) : undefined,
        height: baseH ? scaledPx(baseH) : undefined,
        minHeight: baseH ? undefined : 200,
      }}>
      {/* The page's bitmaps, drawn by the raster: the whole page and, at high
          zoom, a sharp one over the part in view. Both are sized in percent
          of the page box: on a zoom change the old bitmaps stretch to the new
          size immediately (blurry for a moment) instead of sitting at their
          old size in a resized box until the sharp redraw lands. */}
      <div className="pdfPageCanvas">
        <canvas ref={baseRef} />
        <canvas ref={detailRef} className="pdfDetailCanvas" />
      </div>
      {/* Translated view: masks + refills, drawn inside the look-ahead as the
          other overlays are, over the text layer (z-index 1 to its 0) and
          under the highlights, find marks and link boxes. Its translated text
          takes the pointer and the selection where it is drawn, and the text
          layer's spans under the masks stand down (.transMasked, app.css);
          elsewhere the layer lets the pointer through to the original. A
          selection in it stands for the original's blocks (syncSelPopup).
          Laid out at its own reference scale; one transform carries it to
          the zoom layer's SCALE_VAR. */}
      {overlays && (transActive || transWorking || transFailed) && baseW && baseH ? (
        <TransLayer key={transEntry.key} entry={transEntry} page={pageNumber} width={baseW} height={baseH}
          working={transWorking} spanOf={transSpan} basePaints={basePaints} still={readOnly || toolArmed}
          onMenu={onParaMenu ? onFailedMenu : null} />
      ) : null}
      {/* No text selection on a read-only page, nor while a tool is armed: the tool takes the pointer. */}
      {/* The page at scale 1, scaled up to the page box. Its size and
          transform are the build's (above), never written here: an empty
          layer fills the box (pdf_viewer.css, inset: 0), and a built one is
          pdf.js's size under scale(SCALE_VAR). */}
      <div ref={textRef} className="textLayer" data-guide="pdf.textLayer" style={{
        userSelect: readOnly || toolArmed ? "none" : "text", WebkitUserSelect: readOnly || toolArmed ? "none" : "text",
      }} />
      <PdfCitationOverlay citation={citation} wrapRef={wrapRef}
        ready={textReady?.page === page ? textReady : null} />
      {overlays && shownLinks.map((l, i) => (
        <div
          key={`lnk-${i}`}
          className="pdfLinkBox"
          title={l.url || t("Jump to reference")}
          style={{
            left: pctX(l.left),
            top: pctY(l.top),
            width: `max(4px, ${pctX(l.w)})`,
            height: `max(4px, ${pctY(l.h)})`,
          }}
          onClick={(e) => {
            // A viewer told nothing of outside links (a tab group's read
            // view) lets the press through to the page it is over.
            if (l.url && !onExternalLink) return;
            e.stopPropagation();
            if (l.url) onExternalLink(l.url);
            else onInternalLink?.(l.dest);
          }}
          {...menuPress(l.url ? (e) => {
            e.preventDefault();
            e.stopPropagation();
            onLinkContext?.(l.url);
          } : null)}
        />
      ))}
      {/* A hit in a translated paragraph marks the paragraph: an outline,
          since a wash over a whole paragraph says nothing of where. */}
      {overlays && (shownMarks || []).map((m, i) => (
        <div
          key={`find-${i}`}
          className="pdfFindMark"
          style={{
            position: "absolute",
            zIndex: 3,
            pointerEvents: "none",
            left: pctX(m.rect.x1),
            top: pctY(m.rect.y1),
            width: `max(2px, ${pctX(m.rect.x2 - m.rect.x1)})`,
            height: `max(2px, ${pctY(m.rect.y2 - m.rect.y1)})`,
            background: m.block ? "none" : m.active ? "rgba(255, 140, 0, 0.45)" : "rgba(255, 220, 0, 0.30)",
            outline: m.active ? "2px solid rgba(255, 120, 0, 0.9)" : m.block ? "2px solid rgba(255, 200, 0, 0.8)" : "none",
            borderRadius: 2,
            mixBlendMode: "multiply",
          }}
        />
      ))}
      {overlays && highlights.map(h => {
        const rects = h.position.rects;
        const storedW = h.position.width || 1;
        const storedH = h.position.height || 1;
        const isLink = !!h.linkTarget;
        // A passage with comments and no note reads as Notion's commented
        // text: a lighter wash under a 2px line in a strong tone of its
        // colour (--hl-dark-line, which a flipped page also draws).
        const commented = !isLink && commentOnlyHighlight(h);
        // Its threads as cards in the margin beside the page (they leave
        // the badge), and the active card's highlight drawn stronger.
        const carded = marginLeft != null && (h.threadCount || 0) > 0;
        const active = activeHl === h.id;
        const strong = "color-mix(in srgb, var(--hl-line, var(--hl-dark-line)) 35%, var(--hl))";
        // Area notes (Ctrl+drag rectangles) draw as an outline with a faint
        // wash — a solid multiply fill would tint the figure underneath.
        const isArea = !!h.position?.area;
        // After the PDF was replaced (docs/dev/highlight_relocation.md): a
        // highlight whose text the new file does not have is drawn hollow
        // where it was, a dashed line and no fill, whatever else it is; one
        // to check (approx) keeps its look under a dashed edge: an area's
        // border, a passage's outline in the line tone of its colour (an
        // outline, so a commented or link passage keeps its underline,
        // which is a border). The dash always rides inside a shorthand:
        // React rewrites only the style keys that change, so a shorthand
        // rewritten beside an unchanged border-style would reset the dash
        // to solid. The class pdfHlLost has no style; the browser tests
        // find a lost one by it.
        const lost = !!h.position?.lost;
        const approx = placedToCheck(h.position);
        const edge = approx ? "dashed" : "solid";
        // One to check, or a lost one, is to review: its rectangles alone
        // listen for the pointer, which opens its review card
        // (HighlightReview.jsx) — what it matched or that it was not found,
        // where it was, Accept, its closest match, Move… — so they carry no
        // title of their own.
        const reviewable = (approx || lost) && !!onReview;
        const color = h.color;
        // A passage marked by a line (h.style: an underline, a wavy one, a
        // strikethrough; shared/model/markStyle.js) draws it in --hl-mark,
        // its colour's line tone, by its data-hl-style (app.css), and no
        // wash: a faint one while it is the active card's, or hovered. A
        // link region, a lost highlight and an area keep their own looks.
        const line = !isLink && !lost && isLineStyle(h.style) ? h.style : "";
        // The colour rides in --hl (the border in --hl-line, defaulting to
        // it) beside its dark-page variant in --hl-dark / --hl-dark-line:
        // a flipped page (Flip page colors, Gamma Dark) draws those instead
        // (app.css), whatever app the colour came from.
        // Right-click, or a finger held on it: colour, link, copy as reference.
        // The menu takes the place of a review card, shown or on its way.
        const openMenu = (e) => {
          e.preventDefault();
          if (reviewable) onReview.close();
          onHighlightContext?.({ id: h.id, x: e.clientX, y: e.clientY });
        };
        const elements = [];
        for (const [ri, r] of rects.entries()) {
          // over a translation drawn: the original's place, taking no pointer
          const passive = !!onTranslation && !!baseW && !!baseH
            && onTranslation(((r.x1 + r.x2) / 2) * baseW / storedW, ((r.y1 + r.y2) / 2) * baseH / storedH);
          elements.push(<div key={h.id + "-" + r.x1 + "-" + r.y1} data-hl-id={h.id} data-hl-style={line || undefined}
            data-guide={ri === 0 ? "pdf.highlight" : undefined}
            data-guide-recent={ri === 0 && recentHl === h.id ? "" : undefined}
            className={[flashId === h.id ? "pdfHlFlash" : "", lost ? "pdfHlLost" : ""].filter(Boolean).join(" ") || undefined}
            style={{
            position: "absolute", zIndex: 2, ...(passive ? { pointerEvents: "none" } : { cursor: "pointer" }),
            left: pctX(r.x1, storedW), top: pctY(r.y1, storedH),
            width: `max(1px, ${pctX(r.x2 - r.x1, storedW)})`,
            height: `max(1px, ${pctY(r.y2 - r.y1, storedH)})`,
            mixBlendMode: "multiply",
            "--hl": color,
            "--hl-dark": darkPageColor(color),
            "--hl-dark-line": darkPageLine(color),
            ...(line ? { "--hl-mark": lineColor(color) } : null),
            ...(lost ? {
              boxSizing: "border-box", borderRadius: 2, background: "transparent",
              border: "2px dashed var(--hl-line, var(--hl-dark-line))",
            } : isArea ? {
              boxSizing: "border-box", borderRadius: 3,
              border: `2px ${edge} var(--hl-line, var(--hl))`,
              background: `color-mix(in srgb, var(--hl) ${active ? 50 : 25}%, transparent)`,
            } : line ? (active ? { backgroundColor: "color-mix(in srgb, var(--hl) 45%, transparent)" } : null) : commented ? {
              background: active ? strong : "color-mix(in srgb, var(--hl) 55%, transparent)",
              borderBottom: "2px solid var(--hl-line, var(--hl-dark-line))", borderRadius: 1,
            } : { background: active ? strong : "var(--hl)" }),
            ...(commented && isArea && !lost ? { border: `2px ${edge} var(--hl-line, var(--hl-dark-line))` } : {}),
            ...(isLink && !lost ? (isArea
              ? { border: `2px ${edge} rgba(70, 130, 255, 0.9)` }
              : { borderBottom: "2px solid rgba(70, 130, 255, 0.9)", borderRadius: 1 }) : {}),
            ...(approx && !isArea ? { outline: "1px dashed var(--hl-line, var(--hl-dark-line))" } : {}),
          }} title={isLink ? (h.linkTarget.pageId ? t("Open linked paper") : h.linkTarget.url)
            : reviewable ? undefined : h.note || ""}
            {...(reviewable ? { onMouseEnter: () => onReview.hover(h.id, true), onMouseLeave: () => onReview.hover(h.id, false) } : null)}
            onClick={function (e) {
              // A viewer with nothing to do with a highlight (a tab group's
              // read view) lets the press through to the page it is over.
              if (!reviewable && !(isLink ? onLinkHighlight : onHighlightJump)) return;
              e.stopPropagation();
              // a tap (no hover on touch) opens its card instead; a second tap acts
              if (reviewable && onReview.tap(h.id)) return;
              if (isLink) onLinkHighlight?.(h);
              else onHighlightJump?.(h.id, e.ctrlKey || e.metaKey);
              // a highlight with cards: its card becomes the active one
              if (carded) onHighlightThread?.(h.id, e.currentTarget);
            }}
            {...menuPress(openMenu)}
          />);
        }
        // Speech-bubble badge at the end of the passage when the user typed a
        // note on the highlight or someone commented on it — click behaves
        // like clicking the highlight (and opens the comments, if any).
        // The end is found by geometry (noteBadgeAnchor), not by taking the
        // last stored rect: imported highlights keep per-glyph rects in no
        // particular order, and a stray sliver rect would put it anywhere.
        const r = h.hasNote || (h.threadCount && !carded) ? noteBadgeAnchor(rects) : null;
        if (r) {
          elements.push(
            <NoteBadge key={h.id + "-note"} hlId={h.id}
              text={h.note?.trim() || ""} hasNote={!!h.hasNote} threads={carded ? 0 : h.threadCount || 0}
              onThread={(badge) => onHighlightThread?.(h.id, badge)}
              style={{
                left: `calc(${pctX(r.x2, storedW)} + 2px)`,
                top: `calc(${pctY(r.y1, storedH)} - 8px)`,
              }}
              onClick={(e) => { if (!onHighlightJump) return; e.stopPropagation(); onHighlightJump(h.id, e.ctrlKey || e.metaKey); }}
              onContextMenu={openMenu}
            />
          );
        }
        return elements;
      })}
      {/* A lost highlight's closest match, outlined while its review card
          shows it (Show): its rectangles in its colour's line tone, dotted
          over a faint wash, taking no pointer (app.css .pdfHlGhost). */}
      {overlays && reviewGhost ? reviewGhost.rects.map((r, i) => (
        <div key={`ghost-${i}`} className="pdfHlGhost" data-review-ghost={reviewGhost.id} aria-hidden="true" style={{
          left: pctX(r.x1, reviewGhost.width), top: pctY(r.y1, reviewGhost.height),
          width: `max(1px, ${pctX(r.x2 - r.x1, reviewGhost.width)})`,
          height: `max(1px, ${pctY(r.y2 - r.y1, reviewGhost.height)})`,
          "--hl": reviewGhost.color, "--hl-dark": darkPageColor(reviewGhost.color), "--hl-dark-line": darkPageLine(reviewGhost.color),
        }} />
      )) : null}
      {overlays && marginLeft != null ? <CommentMarginSlot pageNumber={pageNumber} anchors={anchors} pageH={(baseH || 0) * marginScale} pageTop={pageTop} /> : null}
      {/* Over the highlights, note badges and link boxes: text boxes, then ink (app.css). */}
      <MarkupLayers surface={pageNumber} wrapRef={wrapRef} width={baseW} height={baseH} marks={marks} active={renderVisible}
        onPenDrag={readOnly ? undefined : penDrag} />
      {marquee ? (
        <div className="pdfAreaMarquee" style={{
          left: marquee.x1, top: marquee.y1,
          width: marquee.x2 - marquee.x1, height: marquee.y2 - marquee.y1,
        }} />
      ) : null}
      {/* Drag finished, color tip still open: keep the drawn rect visible
          (scaled — the popup survives zoom changes). */}
      {pendingArea ? (
        <div className="pdfAreaMarquee" style={{
          left: pctX(pendingArea.rect.x1, pendingArea.width),
          top: pctY(pendingArea.rect.y1, pendingArea.height),
          width: pctX(pendingArea.rect.x2 - pendingArea.rect.x1, pendingArea.width),
          height: pctY(pendingArea.rect.y2 - pendingArea.rect.y1, pendingArea.height),
        }} />
      ) : null}
    </div>
  );
});

// Fixed-position frame for the selection popup: under the selection, or
// above it when the popup (a long translation) would run past the bottom of
// the window; kept inside the window. Placed again in the frame its size
// changes (placement.js), so a growing translation never shows a frame of
// itself still under the selection, past the window's bottom.
function TipFrame({ anchor, children }) {
  const ref = useRef(null);
  const below = anchor.bottom + anchor.gap;
  const pos = useFloatingPosition(ref, (el) => placeFloating(el.getBoundingClientRect(), { x: anchor.left, y: below, flipY: anchor.top - anchor.gap }),
    [anchor.top, below, anchor.left]);
  return <div ref={ref} style={{ position: "fixed", zIndex: 9999, ...(pos || { top: below, left: anchor.left, visibility: "hidden" }) }}>{children}</div>;
}

// A DOM node's element: a text node's parent.
const elementOf = (node) => (node?.nodeType === 3 ? node.parentElement : node);
// Whether a selection's end lies in a page's translated text.
const inTranslation = (node) => !!elementOf(node)?.closest?.(".pdfTransText");

// The block under a selection's end (`node`, `offset`) on the page `pageEl`
// whose blocks are `paras`: a translated paragraph's own (data-block), else
// the block the original's text there lies in, found by where that character
// is on the page (transSelect.js blockAt), -1 off every block. Null for an end
// off this page.
function endBlock(node, offset, pageEl, paras) {
  const el = elementOf(node);
  if (!el || !pageEl.contains(el)) return null;
  const para = el.closest(".pdfTransPara");
  if (para) return Number(para.dataset.block);
  let box = null;
  if (node.nodeType === 3 && node.length) {
    const range = document.createRange();
    const at = Math.min(offset, node.length - 1);
    range.setStart(node, at);
    range.setEnd(node, at + 1);
    box = range.getBoundingClientRect();
  }
  if (!box?.width) box = (node.nodeType === 3 ? el : el.childNodes[offset] || el).getBoundingClientRect?.();
  if (!box) return -1;
  const page = pageEl.getBoundingClientRect();
  const k = page.width / (Number(pageEl.dataset.ptWidth) || 1);
  return blockAt(paras, (box.left + box.width / 2 - page.left) / k, (box.top + box.height / 2 - page.top) / k);
}

// Whether a selection runs forward, its focus after its anchor in the
// document: the range starts at the anchor. Read from the range, since a node
// that holds the other end (a page's scroller, with an offset) says nothing of
// the way the drag went.
function selectionForward(sel) {
  if (sel.direction === "forward" || sel.direction === "backward") return sel.direction === "forward";
  const range = sel.rangeCount ? sel.getRangeAt(0) : null;
  return !range || (range.startContainer === sel.anchorNode && range.startOffset === sel.anchorOffset);
}

// The blocks a selection `sel` stands for on the page its anchor is on, when
// that page shows the translation of the document at `url` under `key`
// (transSelect.js selectionBlocks), or else on the page its focus is on when
// that one does: {pageEl, pn, entry, blocks, from, translated}, `from` the
// block under the end on that page (-1 off every block) and `translated`
// whether an end lies in translated text; null when neither does. The other
// end, on another page or off every page (the notes beside the PDF, the
// viewer's margin), takes this page as far as its body text goes that way:
// on a later page or after it in the document, to its end; else from its
// start.
function selectionOnPage(sel, url, key) {
  const pageOf = (node) => elementOf(node)?.closest?.("[data-page]") || null;
  const shown = (el) => {
    const entry = el ? translateState.entries(url).get(parseInt(el.dataset.page, 10)) : null;
    return entry && entry.key === key ? entry : null;
  };
  let here = [sel.anchorNode, sel.anchorOffset], there = [sel.focusNode, sel.focusOffset];
  let forward = selectionForward(sel);
  let pageEl = pageOf(here[0]), entry = shown(pageEl);
  if (!entry) {
    pageEl = pageOf(there[0]);
    entry = shown(pageEl);
    if (!entry) return null;
    [here, there, forward] = [there, here, !forward];
  }
  const pn = parseInt(pageEl.dataset.page, 10);
  const reading = pageReading(entry.paras);
  const from = endBlock(here[0], here[1], pageEl, entry.paras) ?? -1;
  let to = endBlock(there[0], there[1], pageEl, entry.paras);
  if (to === null) {
    const other = parseInt(pageOf(there[0])?.dataset.page, 10);
    const later = Number.isFinite(other) ? other > pn : forward;
    to = (later ? reading.flow[reading.flow.length - 1] : reading.flow[0]) ?? -1;
  }
  const units = unitsOnPage((q) => (q === pn ? entry.paras : translateState.blocks(url, q)), pn);
  const blocks = selectionBlocks(entry, from, to, units, reading);
  const translated = inTranslation(sel.anchorNode) || inTranslation(sel.focusNode);
  return { pageEl, pn, entry, blocks, from, translated };
}

// The viewport rects of the text `range` selects as the reader sees it: of the
// translation's text, and of the text layer's spans that do not stand down
// under it (.transMasked). A range's own rects take in the boxes of the
// elements it holds whole, a translated paragraph's masks among them, which
// lie on the original's lines.
function drawnRects(range) {
  const root = range.commonAncestorContainer;
  const out = [];
  const take = (node) => {
    const el = node.parentElement;
    if (!el?.closest(".pdfTransText") && (!el?.closest(".textLayer") || el.closest(".transMasked"))) return;
    const part = document.createRange();
    part.setStart(node, node === range.startContainer ? range.startOffset : 0);
    part.setEnd(node, node === range.endContainer ? range.endOffset : node.length);
    for (const cr of part.getClientRects()) if (cr.width > 1 && cr.height > 1) out.push(cr);
  };
  if (root.nodeType === 3) take(root);
  else {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) if (range.intersectsNode(n)) take(n);
  }
  return out;
}

// The rect round viewport rects ({left, top, right, bottom}), as a selection's
// bounding rect is given.
function unionRect(rects) {
  const left = Math.min(...rects.map((q) => q.left)), right = Math.max(...rects.map((q) => q.right));
  return { left, top: Math.min(...rects.map((q) => q.top)), width: right - left, bottom: Math.max(...rects.map((q) => q.bottom)) };
}

// Longest selection the popup translates (characters, after rejoining lines).
const SEL_TRANSLATE_MAX = 5000;

// Selection popup: highlight colors, the reference link and, when enabled
// (Settings → Language and Translation), the selection translator. `translate`:
// {text, auto, langLabel, lang, run(texts, signal, onPartial) → the server's reply
// ({translations, failed}) | null, addTerm (SelTranslation) | null} — run is the page translator's request, so
// the same model/engine, language and server cache apply. Auto starts it on
// open; the button starts it, then folds/unfolds the result. `recolor`:
// the selection is a highlight already (the highlighter's), which the
// colours and presets change and `onRestyle(style)` restyles as the style
// row is pressed. `area`: a dragged rectangle, which has no style.
// `onDismiss()` puts the popup and its selection away. The
// style row, the colours, the presets and the colour fold are TipMarks'
// (MarkTools.jsx); a pick reaches onConfirm("", swatch, {style}). `copy`:
// the selection's text, for a Copy button (where the host's own menu,
// which has one, is off). `menu`: the opener of the paragraph menu of a
// page that shows its translation, for the ⋯ that is a finger's and a
// pen's way to it (MenuButton): their hold selects.
function PlainTip({ onConfirm, onLink, onComment, translate, onMove, copy = null, menu = null, recolor = false, onRestyle = null, area = false, onDismiss = null }) {
  const palette = usePalette();
  const colors = pickerColors(palette, "highlight");
  const [copied, flashCopied] = useCopied(1200);
  const [trans, setTrans] = useState(null); // {status: "loading"|"done"|"error", text, open}
  const ctlRef = useRef(null);
  const source = useMemo(() => (translate ? selectionParagraphs(translate.text) : ""), [translate?.text]);
  const tooLong = source.length > SEL_TRANSLATE_MAX;
  function start() {
    ctlRef.current?.abort();
    if (tooLong) { setTrans({ status: "error", text: "", open: true }); return; }
    const ctl = new AbortController();
    ctlRef.current = ctl;
    setTrans({ status: "loading", text: "", open: true });
    let reason = ""; // the server's detail (a used-up AI allowance says what to do)
    Promise.resolve(translate.run([source], ctl.signal, (_, partial) => {
      if (!ctl.signal.aborted) setTrans((prev) => prev && { ...prev, text: partial });
    })).catch((err) => { reason = err?.message || ""; return null; }).then((res) => {
      if (ctl.signal.aborted) return;
      // A text the server could not translate comes back as itself.
      const text = res?.failed?.length ? "" : res?.translations?.[0];
      setTrans((prev) => ({ open: prev?.open ?? true, ...(text ? { status: "done", text } : { status: "error", text: "", reason }) }));
    });
  }
  useEffect(() => {
    if (translate?.auto) start();
    return () => ctlRef.current?.abort();
  }, []);
  const keep = (e) => { e.preventDefault(); e.stopPropagation(); }; // keep the PDF selection
  // A button acts on the mouse's press, which keeps the PDF selection, and on
  // a click no press led to (`detail` 0: Enter or Space on it, an assistive
  // technology's activation), as triggerPress does (shared/ui/press.js).
  const act = (run) => ({
    onMouseDown: (e) => { keep(e); run(); },
    onClick: (e) => { if (!e.detail) run(); },
  });
  const fold = () => setTrans((prev) => ({ ...prev, open: !prev.open }));
  if (onMove) {
    // A highlight waiting to be moved (App's move mode): this selection is
    // its new place, nothing else is offered.
    return (
      <div className="plainTip">
        <div className="colorRow">
          <button className="colorBtn linkTipBtn tipMoveBtn" type="button" {...act(onMove)}
            title={t("Move the highlight here")} aria-label={t("Move the highlight here")}>
            <LocateIcon size={14} />
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="plainTip">
      <TipMarks area={area} recolor={recolor} act={act} guide="pdf.highlightColor" onStyle={onRestyle} onLeave={onDismiss}
        onMark={({ color, style }) => onConfirm("", color, { style })} tools={<>
        {/* the colours, then the tools */}
        {copy || onComment || onLink || translate || menu ? <span className="tipDivider" aria-hidden="true" /> : null}
        {copy ? (
          <button
            className="colorBtn linkTipBtn"
            onMouseDown={keep}
            onClick={async () => { if (await copyText(copy)) flashCopied(); }}
            type="button"
            title={t("Copy")}
            aria-label={t("Copy")}
          >
            <IconSwap swap={copied} from={<CopyIcon size={14} />} to={<CheckIcon size={14} />} />
          </button>
        ) : null}
        {onComment ? (
          <button
            className="colorBtn linkTipBtn"
            {...act(() => onComment(colors[0]))}
            type="button"
            title={t("Comment on this passage")}
            aria-label={t("Comment on this passage")}
          >
            <MessageSquareIcon size={14} />
          </button>
        ) : null}
        {onLink ? (
          <button
            className="colorBtn linkTipBtn"
            {...act(onLink)}
            type="button"
            title={t("Link this reference to a paper (DOI / arXiv / existing PDF)")}
            aria-label={t("Link this reference to a paper (DOI / arXiv / existing PDF)")}
          >
            <LinkIcon size={14} />
          </button>
        ) : null}
        {translate ? (
          <button
            className={`colorBtn linkTipBtn ${trans ? "selected" : ""}`}
            {...act(() => {
              if (!trans || trans.status === "error") start();
              else fold();
            })}
            type="button"
            aria-label={t("Translate selection")}
            aria-expanded={!!trans?.open}
            title={t("Translate the selected text into {lang}", { lang: translate.langLabel })}
          >
            <LanguagesIcon size={14} />
          </button>
        ) : null}
        {menu ? <MenuButton open={menu} label={t("Paragraph actions")} className="colorBtn linkTipBtn" onMouseDown={keep} /> : null}
      </>} />
      {translate && trans ? <SelTranslation trans={trans} lang={translate.lang} langLabel={translate.langLabel} tooLong={tooLong}
        onToggle={fold} keep={keep} source={source} addTerm={translate.addTerm} /> : null}
    </div>
  );
}

// The popup's translation: a header that folds the result (with the target
// language, a spinner while it arrives, Add to glossary and a copy button),
// and the text — selectable, typed in as it streams. `addTerm(lang, source,
// target)`: the workspace's glossary takes a term (App, for a writer of the
// workspace), offered once the translation is in, for a selection short
// enough to be one; its form holds the selection and its translation, both
// editable.
function SelTranslation({ trans, lang, langLabel, tooLong, onToggle, keep, source = "", addTerm = null }) {
  const [copied, flashCopied] = useCopied(1200);
  const [term, setTerm] = useState(null); // {source, target}
  const loading = trans.status === "loading";
  const offerTerm = !!addTerm && trans.status === "done" && !!source && source.length <= GLOSSARY_TERM_MAX;
  return (
    <div className="selTrans">
      <div className="selTransHead">
        <button type="button" className="selTransToggle" aria-expanded={trans.open}
          onMouseDown={keep} onClick={onToggle}>
          <Chevron open={trans.open} dir="right" size={14} />
          <span>{t("Translation")}</span>
          <span className="selTransLang">{langLabel}</span>
          {loading ? <span className="pillSpin" aria-hidden="true" /> : null}
        </button>
        {offerTerm ? (
          <button type="button" className={`ctlBtn selTransCopy ${term ? "modeActive" : ""}`} title={t("Add to glossary")}
            aria-label={t("Add to glossary")} aria-expanded={!!term} onMouseDown={keep}
            onClick={() => setTerm(term ? null : { source, target: Array.from(trans.text.trim()).slice(0, GLOSSARY_TERM_MAX).join("") })}>
            <BookIcon size={16} />
          </button>
        ) : null}
        {trans.status === "done" ? (
          <button type="button" className="ctlBtn selTransCopy" title={t("Copy translation")} aria-label={t("Copy translation")}
            onMouseDown={keep}
            onClick={async () => { if (await copyText(trans.text)) flashCopied(); }}>
            <IconSwap swap={copied} from={<CopyIcon size={16} />} to={<CheckIcon size={16} />} />
          </button>
        ) : null}
      </div>
      {/* It opens at once: the popup it grows is placed again in every frame
          its size changes (TipFrame), and growing over frames could move it
          to the other side of the selection partway (docs/dev/ui-design.md,
          "Menus and popups open in place"). Only its chevron turns. */}
      {trans.open ? (
        // Busy while it streams: a screen reader reads the translation once,
        // when it is in, not each piece as it arrives.
        <div className={`selTransBody ${trans.status === "error" ? "error" : ""}`} aria-live="polite" aria-busy={loading}
          lang={trans.status !== "error" && trans.text ? lang : undefined}>
          {trans.status === "error"
            ? (tooLong ? t("Select less text to translate (up to {n} characters).", { n: SEL_TRANSLATE_MAX })
              : trans.reason ? t("Translation failed: {message}", { message: trans.reason })
                : t("Translation failed. Press 文A above to try again."))
            : trans.text || (loading ? t("Translating…") : "")}
        </div>
      ) : null}
      {term && offerTerm ? <GlossaryTermForm initial={term} lang={lang} addTerm={addTerm} onDone={() => setTerm(null)} /> : null}
    </div>
  );
}

// A term's longest side the glossary keeps (translate_terms.TERM_MAX_CHARS).
const GLOSSARY_TERM_MAX = 100;

// Add to glossary, in the selection's translation: the term and its
// translation, prefilled and editable, then Add. The fields take the focus
// and the keys; the PDF's selection stays as the popup opened on it. Added,
// the form says so and goes; the paragraphs on screen keep their words
// until they are translated again.
function GlossaryTermForm({ initial, lang, addTerm, onDone }) {
  const [draft, setDraft] = useState(initial);
  const [state, setState] = useState({ busy: false, done: false, error: "" });
  const ready = draft.source.trim() && draft.target.trim();
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  function submit(e) {
    e.preventDefault();
    if (!ready || state.busy || state.done) return;
    setState({ busy: true, done: false, error: "" });
    Promise.resolve(addTerm(lang, draft.source, draft.target)).then(() => {
      setState({ busy: false, done: true, error: "" });
      timer.current = setTimeout(onDone, 1500);
    }, (err) => setState({ busy: false, done: false, error: err?.message || t("Couldn't add the term") }));
  }
  const field = (key, label) => (
    <input className="aiKeyInput" type="text" spellCheck={false} autoComplete="off" maxLength={GLOSSARY_TERM_MAX}
      aria-label={label} placeholder={label} value={draft[key]} lang={key === "target" ? lang : undefined}
      disabled={state.busy || state.done}
      onChange={(event) => setDraft((prev) => ({ ...prev, [key]: event.target.value }))} />
  );
  return (
    <form className="selTransTerm" onSubmit={submit}>
      <div className="selTransTermPair">
        {field("source", t("Term"))}
        <ChevronRightIcon size={14} />
        {field("target", t("Translation"))}
      </div>
      <div className="selTransTermBtns">
        <span className={`selTransTermNote ${state.error ? "error" : ""}`} role="status">
          {state.error || (state.done ? t("Added to the glossary") : t("For every translation in this workspace"))}
        </span>
        <button type="button" className="uiBtn sm" onClick={onDone}>{t("Cancel")}</button>
        <button type="submit" className="uiBtn sm primary" disabled={!ready || state.busy || state.done}>{t("Add")}</button>
      </div>
    </form>
  );
}

export default PdfViewer;
