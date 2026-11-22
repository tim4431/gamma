// What the window's text recognition (pdf/ocrTab.js, docs/dev/pdf_loading.md
// "Recognized words") draws, hands the engine and sends the server, pure so
// node tests it (tests/ocrRaster.test.mjs): the zoom a page is drawn at, the
// grey picture tesseract.js reads, its TSV turned into the server's lines,
// the resolution the engine is told, the engine's options and whether this
// browser can run it.
//
// The page is drawn as the server draws it for its own engine
// (backend gamma/ocr_engine.py: 300 dpi, less past MAX_PIXELS or MAX_SIDE),
// within this device's canvas caps (shared/lib/canvasSize.js), and its words
// travel as the server stores them (gamma/pdf_ocr.py): lines of words
// [text, x0, y0, x1, y1, confidence], the box in UNITS of the page as shown
// (x of its width, y of its height, from its top left corner).

import { t } from "../shared/i18n/i18n.js";

// The engine and what a window's run says it reads with, which the server
// checks (POST /api/jobs/ocr, PUT /api/ocr/<doc>/pages/<n>): the engine's
// name and package.json's pinned version (gamma/ocr_engine.py
// BROWSER_ENGINE; a test holds the two to the pin), the server's languages
// (ocr_engine.LANGS) and its stored rows' version (pdf_ocr.VERSION). A
// window built before either changed is told to reload.
export const ENGINE = "tesseract.js 7.0.0";
export const LANGS = "eng+fra+deu";
export const OCR_VERSION = 1;
// The boxes' unit (pdf_text.OCR_UNITS).
export const UNITS = 10000;
// What the plan (GET /api/ocr/<doc>/plan) says of the drawing and of a
// page's words, the server's own figures until it answers.
export const RENDER = Object.freeze({ dpi: 300, max_pixels: 2480 * 3508, max_side: 10000 });
export const LIMITS = Object.freeze({ max_words: 20000, max_word_chars: 200, max_body: 1048576 });
// The rows read back from the canvas at a time: a strip of an A4 page at
// 300 dpi is 2.5 MB of RGBA, where the whole page would be 35 MB.
const STRIP_ROWS = 256;

// The zoom (pixels per point) a page `width` × `height` (its scale-1
// viewport) is drawn at, and the canvas that makes: `render`'s dpi, less
// where its pixel or side limit, or this device's canvas caps (`caps`,
// canvasLimits()), would be passed. {zoom, width, height}, or null for a
// page with no size.
export function zoomPlan(width, height, caps, render = RENDER) {
  if (!(width > 0 && height > 0 && Number.isFinite(width) && Number.isFinite(height))) return null;
  const r = { ...RENDER, ...render };
  const zoom = Math.min(r.dpi / 72, Math.sqrt(Math.min(r.max_pixels, caps.pixels) / (width * height)),
    Math.min(r.max_side, caps.edge) / Math.max(width, height));
  return { zoom, width: Math.max(1, Math.floor(width * zoom)), height: Math.max(1, Math.floor(height * zoom)) };
}

// The resolution the engine is told a page was drawn at (its
// user_defined_dpi, as the server's SetSourceResolution): the canvas's
// pixels per inch of the page, a page `pageWidth` points wide at scale 1 in
// units of `userUnit` points (pdf.js's viewport leaves /UserUnit out), and
// never under 70, Tesseract's least credible resolution.
export function pageDpi(canvasWidth, pageWidth, userUnit = 1) {
  return Math.max(70, Math.round((72 * canvasWidth) / (pageWidth * (userUnit || 1))));
}

// A grey P5 PGM of a `width` × `height` picture whose RGBA rows
// `readRows(y, rows)` gives, read STRIP_ROWS at a time so the whole page's
// RGBA is never held: grey as 0.30 red, 0.59 green and 0.11 blue, laid on
// white where a pixel is not opaque. What tesseract.js reads without an
// encode (a canvas would go through a PNG on this thread).
export function toPgm(width, height, readRows, strip = STRIP_ROWS) {
  const head = new TextEncoder().encode(`P5\n${width} ${height}\n255\n`);
  const out = new Uint8Array(head.length + width * height);
  out.set(head);
  let o = head.length;
  for (let y = 0; y < height; y += strip) {
    const rows = Math.min(strip, height - y);
    const rgba = readRows(y, rows);
    const end = width * rows * 4;
    for (let i = 0; i < end; i += 4) {
      const a = rgba[i + 3];
      const grey = 0.3 * rgba[i] + 0.59 * rgba[i + 1] + 0.11 * rgba[i + 2];
      out[o++] = Math.round((grey * a) / 255 + 255 - a);
    }
  }
  return out;
}

const unit = (f) => Math.min(UNITS, Math.max(0, Math.round(UNITS * f)));

// The lines of a page tesseract.js read (its TSV: level, page_num,
// block_num, par_num, line_num, word_num, left, top, width, height, conf,
// text) on a `width` × `height` canvas, as the server takes them: words in
// reading order, each [text, x0, y0, x1, y1, conf], the box in UNITS of the
// canvas, which is the page as shown, the confidence 0 to 100 to a tenth. A
// word loses its white space, and one left empty is dropped; a line begins
// at each new block, paragraph or line number (Tesseract numbers lines
// within their paragraph). Null when the page passes `limits`, as the
// server's engine leaves such a page without a result: more than max_words
// words, or a word of more than max_word_chars characters.
export function parseTsv(tsv, width, height, limits = LIMITS) {
  const maxWords = limits?.max_words ?? LIMITS.max_words;
  const maxChars = limits?.max_word_chars ?? LIMITS.max_word_chars;
  const lines = [];
  let line = null, at = null, count = 0;
  for (const row of String(tsv || "").split("\n")) {
    const c = row.replace(/\r$/, "").split("\t");
    if (c.length < 12 || c[0].trim() !== "5") continue;
    const text = c.slice(11).join("").replace(/\s+/gu, "");
    if (!text) continue;
    const [left, top, w, h, conf] = c.slice(6, 11).map(Number);
    if (![left, top, w, h].every(Number.isFinite)) continue;
    if ([...text].length > maxChars || ++count > maxWords) return null;
    const key = `${c[2]}\t${c[3]}\t${c[4]}`;
    if (key !== at) {
      at = key;
      line = [];
      lines.push(line);
    }
    line.push([text, unit(left / width), unit(top / height), unit((left + w) / width), unit((top + h) / height),
      Math.round(Math.min(100, Math.max(0, Number.isFinite(conf) ? conf : 0)) * 10) / 10]);
  }
  return lines;
}

// Whether `path` is a file of this page's own server: http(s) and the same
// origin as `base` (a path with no origin of its own is).
function ownFile(path, base) {
  if (typeof path !== "string" || !path) return false;
  try {
    const at = new URL(base);
    const url = new URL(path, at);
    return (url.protocol === "http:" || url.protocol === "https:") && url.origin === at.origin;
  } catch {
    return false;
  }
}

// What tesseract.js's createWorker(langs, oem, options) and the worker's
// first setParameters take for a run: the worker script and the core this
// build ships (`workerPath`, `corePath`) and the server's models
// (`models`, the capability's /api/ocr/models/<tag>), each of which must be
// this server's own (`base`: the page's address), since every default of
// tesseract.js is a CDN's; the models as plain files fetched each run
// (immutable on the server, so the browser's cache keeps them) rather than
// in IndexedDB under a key that names no version; a worker of its own
// script, not a blob; the LSTM engine alone, the server's three languages
// and its page segmentation (automatic: Tesseract's default reads a page as
// one block, running columns together). What Tesseract prints as it works
// ("Detected 199 diacritics") goes to its `debug_file`, the core's
// /dev/null, instead of its stderr, which tesseract.js's core writes to the
// console as errors.
export function workerOptions({ models, workerPath, corePath }, base = globalThis.location?.href || "http://same.invalid/") {
  for (const path of [models, workerPath, corePath]) {
    if (!ownFile(path, base)) throw new Error(t("The text recognition files must come from this server."));
  }
  return {
    langs: LANGS,
    oem: 1, // OEM.LSTM_ONLY
    options: { workerPath, corePath, langPath: models, gzip: false, cacheMethod: "none", workerBlobURL: false },
    parameters: { tessedit_pageseg_mode: "3", debug_file: "/dev/null" }, // PSM.AUTO
  };
}

// wasm-feature-detect's SIMD probe, a module of one v128 function: the one
// core this build ships needs it (every browser Gamma supports has it).
const SIMD_PROBE = new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123, 3, 2, 1, 0, 10, 10, 1, 8, 0, 65, 0, 253, 15, 253, 98, 11]);
export function hasSimd(wasm = globalThis.WebAssembly) {
  try {
    return typeof wasm === "object" && !!wasm && wasm.validate(SIMD_PROBE);
  } catch {
    return false;
  }
}
