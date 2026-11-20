// What a window draws for its text recognition, hands tesseract.js and
// sends the server (pdf/ocrRaster.js): the zoom plan, the grey PGM, the TSV
// as the server's lines, the resolution, the engine's options and the SIMD
// probe.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { ENGINE, LANGS, LIMITS, OCR_VERSION, RENDER, UNITS, hasSimd, pageDpi, parseTsv, toPgm, workerOptions, zoomPlan }
  from "../src/pdf/ocrRaster.js";

const ROOMY = { pixels: 16 * 1024 * 1024, edge: 8192 };
const TOUCH = { pixels: 8 * 1024 * 1024, edge: 4096 };

test("a page is drawn at 300 dpi, within the server's pixel and side limits and the device's canvas caps", () => {
  // Letter at 300 dpi: 2550 × 3300, under MAX_PIXELS
  assert.deepEqual(zoomPlan(612, 792, ROOMY), { zoom: 300 / 72, width: 2550, height: 3300 });
  // A4 at 300 dpi passes 2480 × 3508 pixels by a little: the server's cap
  const a4 = zoomPlan(595.28, 841.89, ROOMY);
  assert.equal(a4.width, 2480);
  assert.equal(a4.height, 3507);
  assert.ok(a4.zoom < 300 / 72);
  // a touch device's 8 Mi pixels bite first: about 295 dpi
  const touch = zoomPlan(595.28, 841.89, TOUCH);
  assert.ok(touch.width * touch.height <= TOUCH.pixels);
  assert.equal(pageDpi(touch.width, 595.28), 295);
  // a long strip: the side limit, the device's edge where it is shorter
  assert.equal(zoomPlan(100, 5000, ROOMY).zoom, 8192 / 5000);
  assert.equal(zoomPlan(100, 5000, { pixels: 1e9, edge: 20000 }).zoom, 10000 / 5000);
  // the plan's own figures win
  assert.equal(zoomPlan(612, 792, ROOMY, { dpi: 150 }).zoom, 150 / 72);
  assert.equal(zoomPlan(0, 792, ROOMY), null);
  assert.equal(zoomPlan(612, Number.NaN, ROOMY), null);
  assert.deepEqual(RENDER, { dpi: 300, max_pixels: 2480 * 3508, max_side: 10000 });
});

test("the resolution the engine is told is the canvas's pixels per inch of the page, never under 70", () => {
  assert.equal(pageDpi(2550, 612), 300);
  // pdf.js's viewport leaves /UserUnit out: a page of 2-point units is twice as wide in inches
  assert.equal(pageDpi(2550, 612, 2), 150);
  assert.equal(pageDpi(100, 612), 70);
  assert.equal(pageDpi(2550, 612, 0), 300);
});

test("the PGM is a P5 header and one grey byte a pixel, read in strips, laid on white", () => {
  const width = 3, height = 5;
  // pixel i of the picture: red i*10, green 100, blue 200, opaque, but the last one transparent
  const rgba = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < width * height; i++) rgba.set([i * 10, 100, 200, i === width * height - 1 ? 0 : 255], i * 4);
  const asked = [];
  const pgm = toPgm(width, height, (y, rows) => {
    asked.push([y, rows]);
    return rgba.subarray(y * width * 4, (y + rows) * width * 4);
  }, 2);
  assert.deepEqual(asked, [[0, 2], [2, 2], [4, 1]]);
  const head = "P5\n3 5\n255\n";
  assert.equal(new TextDecoder().decode(pgm.subarray(0, head.length)), head);
  assert.equal(pgm.length, head.length + width * height);
  const grey = [...pgm.subarray(head.length)];
  for (let i = 0; i < width * height - 1; i++) assert.equal(grey[i], Math.round(0.3 * i * 10 + 0.59 * 100 + 0.11 * 200));
  assert.equal(grey.at(-1), 255);
  // white stays white, black black
  const ends = toPgm(2, 1, () => new Uint8ClampedArray([255, 255, 255, 255, 0, 0, 0, 255]));
  assert.deepEqual([...ends.subarray(-2)], [255, 0]);
});

const HEAD = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext";
const row = (level, block, par, line, word, box, conf, text) => [level, 1, block, par, line, word, ...box, conf, text].join("\t");

test("the TSV's words become the server's lines, a line at each new block, paragraph or line number", () => {
  const tsv = [
    HEAD,
    row(1, 0, 0, 0, 0, [0, 0, 1000, 2000], -1, ""),
    row(2, 1, 0, 0, 0, [100, 100, 800, 300], -1, ""),
    row(4, 1, 1, 1, 0, [100, 100, 800, 50], -1, ""),
    row(5, 1, 1, 1, 1, [100, 100, 200, 50], 96.4567, "Résumé"),
    row(5, 1, 1, 1, 2, [320, 100, 100, 50], 101, "de"),
    row(5, 1, 1, 2, 1, [100, 200, 300, 50], -3, "phénomène"),
    // a new paragraph numbers its lines from 1 again
    row(5, 1, 2, 1, 1, [100, 300, 100, 50], 88, "Collège"),
    // white space goes; a word left empty, a short row and a bad box are dropped
    row(5, 1, 2, 1, 2, [250, 300, 100, 50], 90, " a b "),
    row(5, 1, 2, 1, 3, [400, 300, 100, 50], 90, "  "),
    "5\t1\t1\t2\t1\t4\t1\t1",
    row(5, 1, 2, 1, 4, ["x", 300, 100, 50], 90, "bad"),
    // a block of its own, its box past the canvas clamped
    row(5, 2, 1, 1, 1, [900, 1900, 300, 300], 70, "end\r"),
    "",
  ].join("\n");
  assert.deepEqual(parseTsv(tsv, 1000, 2000), [
    [["Résumé", 1000, 500, 3000, 750, 96.5], ["de", 3200, 500, 4200, 750, 100]],
    [["phénomène", 1000, 1000, 4000, 1250, 0]],
    [["Collège", 1000, 1500, 2000, 1750, 88], ["ab", 2500, 1500, 3500, 1750, 90]],
    [["end", 9000, 9500, UNITS, UNITS, 70]],
  ]);
  assert.deepEqual(parseTsv("", 1000, 2000), []);
  assert.deepEqual(parseTsv(undefined, 1000, 2000), []);
});

test("a page past the word limits has no result, as the server's engine leaves it", () => {
  const words = (n, text = "w") => Array.from({ length: n }, (_, i) => row(5, 1, 1, 1, i + 1, [i, 0, 1, 1], 90, text)).join("\n");
  assert.equal(parseTsv(words(3), 100, 100, { ...LIMITS, max_words: 3 }).flat().length, 3);
  assert.equal(parseTsv(words(4), 100, 100, { ...LIMITS, max_words: 3 }), null);
  // characters, not UTF-16 units: an astral letter is one
  assert.equal(parseTsv(words(1, "𝔸".repeat(200)), 100, 100).flat().length, 1);
  assert.equal(parseTsv(words(1, "x".repeat(201)), 100, 100), null);
  assert.equal(parseTsv(words(20001), 100, 100), null);
});

test("the engine's options keep every file on this server and read the page as the server's engine does", () => {
  const base = "https://gamma.example/library?page=1";
  const files = { models: "/api/ocr/models/0123456789ab", workerPath: "/assets/worker.min-AbCd1234.js",
    corePath: "/assets/tesseract-core-simd-lstm.wasm-AbCd1234.js" };
  assert.deepEqual(workerOptions(files, base), {
    langs: "eng+fra+deu",
    oem: 1,
    options: { workerPath: files.workerPath, corePath: files.corePath, langPath: files.models,
      gzip: false, cacheMethod: "none", workerBlobURL: false },
    // Tesseract's own reports ("Detected 199 diacritics") go nowhere, not
    // to the console as errors
    parameters: { tessedit_pageseg_mode: "3", debug_file: "/dev/null" },
  });
  // the same origin spelled out passes; Vite's dev server serves node_modules under /node_modules
  assert.ok(workerOptions({ ...files, corePath: "https://gamma.example/node_modules/tesseract.js-core/x.wasm.js" }, base));
  for (const bad of ["https://cdn.jsdelivr.net/npm/tesseract.js@v7.0.0/dist/worker.min.js", "//cdn.jsdelivr.net/x.js",
    "http://gamma.example/assets/x.js", "https://gamma.example:8443/assets/x.js", "data:text/javascript,0", "blob:https://gamma.example/1",
    "", undefined, null]) {
    for (const key of Object.keys(files)) {
      assert.throws(() => workerOptions({ ...files, [key]: bad }, base), /must come from this server/, `${key}: ${bad}`);
    }
  }
  // with no page address, only paths of no origin pass
  assert.ok(workerOptions(files));
  assert.throws(() => workerOptions({ ...files, models: "https://gamma.example/api/ocr/models/1" }), /must come from this server/);
});

test("the engine the window names is the one package.json pins, exactly", () => {
  const pkg = JSON.parse(fs.readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  assert.equal(pkg.dependencies["tesseract.js"], "7.0.0");
  assert.equal(pkg.dependencies["tesseract.js-core"], "7.0.0");
  assert.equal(ENGINE, `tesseract.js ${pkg.dependencies["tesseract.js"]}`);
  const lock = JSON.parse(fs.readFileSync(new URL("../package-lock.json", import.meta.url), "utf8"));
  assert.equal(lock.packages["node_modules/tesseract.js"].version, "7.0.0");
  assert.equal(lock.packages["node_modules/tesseract.js-core"].version, "7.0.0");
  assert.equal(LANGS, "eng+fra+deu");
  assert.equal(OCR_VERSION, 1);
});

test("the SIMD probe is wasm-feature-detect's: Node has SIMD; a WebAssembly that refuses it, or none, has not", () => {
  assert.equal(hasSimd(), true);
  assert.equal(hasSimd({ validate: () => false }), false);
  assert.equal(hasSimd({ validate: () => { throw new Error("no"); } }), false);
  assert.equal(hasSimd(null), false);
  assert.equal(hasSimd("WebAssembly"), false);
});
