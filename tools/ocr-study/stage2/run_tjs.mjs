// tesseract.js 7.0.0 (Tesseract compiled to WASM) in Node over the rendered
// pages, with the language data a browser would fetch by default
// (jsdelivr @tesseract.js-data/<lang>, cached under OCR_STUDY_TJS_CACHE).
//   node run_tjs.mjs [--name tjs] [--psm N] [ids...]
// Settings: OCR_STUDY_DIR (default tools/ocr-study/.work); OCR_STUDY_TJS, the
// folder tesseract.js is installed in (`npm install tesseract.js@7.0.0` there;
// default $OCR_STUDY_DIR/tjs); OCR_STUDY_TJS_CACHE (default $OCR_STUDY_DIR/tjs-cache).
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WORK = path.resolve(process.env.OCR_STUDY_DIR || path.join(HERE, "..", ".work"));
const ROOT = path.join(WORK, "stage2");
const TJS = path.resolve(process.env.OCR_STUDY_TJS || path.join(WORK, "tjs"));
const require = createRequire(path.join(TJS, "package.json"));
const { createWorker } = require("tesseract.js");

const args = process.argv.slice(2);
const nameAt = args.indexOf("--name");
const engine = nameAt >= 0 ? args[nameAt + 1] : "tjs";
const psmAt = args.indexOf("--psm");
const psm = psmAt >= 0 ? args[psmAt + 1] : null;
const ids = args.filter((a, i) => !a.startsWith("--") && args[i - 1] !== "--name" && args[i - 1] !== "--psm");
const spec = JSON.parse(fs.readFileSync(path.join(HERE, "pages.json"), "utf8"));
const pages = spec.pages.filter((p) => !ids.length || ids.includes(p.id) || ids.includes(p.file));
const S = 72 / 300;
const box = (b) => [b.x0, b.y0, b.x1, b.y1].map((v) => Math.round(v * S * 100) / 100);
const cachePath = path.resolve(process.env.OCR_STUDY_TJS_CACHE || path.join(WORK, "tjs-cache"));
fs.mkdirSync(cachePath, { recursive: true });

const workers = new Map();
for (const p of pages) {
  const langs = p.lang.split("+");
  const key = p.lang;
  if (!workers.has(key)) {
    const t0 = performance.now();
    const wk = await createWorker(langs, 1, { cachePath });
    if (psm) await wk.setParameters({ tessedit_pageseg_mode: psm });
    workers.set(key, wk);
    console.log("worker", key, `${((performance.now() - t0) / 1000).toFixed(2)}s to load`);
  }
  const w = workers.get(key);
  const img = fs.readFileSync(path.join(ROOT, "img", `${p.id}.png`));
  const t = performance.now();
  const { data } = await w.recognize(img, {}, { blocks: true, text: false });
  const dt = (performance.now() - t) / 1000;
  const lines = [];
  for (const b of data.blocks || []) for (const par of b.paragraphs) for (const l of par.lines) {
    const words = l.words.filter((x) => x.text.trim()).map((x) => ({ text: x.text, bbox: box(x.bbox), conf: x.confidence }));
    if (words.length) lines.push({ text: words.map((x) => x.text).join(" "), bbox: box(l.bbox), words });
  }
  const dir = path.join(ROOT, "out", engine);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, `${p.id}.json`), JSON.stringify({ engine, page: p.id, time: Math.round(dt * 1000) / 1000, lines }));
  console.log(engine, p.id, `${dt.toFixed(2)}s`, lines.length, "lines");
}
for (const w of workers.values()) await w.terminate();
