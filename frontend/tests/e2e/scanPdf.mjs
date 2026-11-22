// A scanned paper as the browser suite's scan-layer scenario reads it
// (docs/dev/pdf_loading.md "A scan's text layer"): a born-digital cover, as
// HAL puts before a scan, then a page that is a picture with an invisible OCR
// layer over it, written the way the study's worst producer writes one
// (docs/research/ocr.md, the Lounis 1992 layer). The picture is the whole
// page, its print a dark box of ink a word; over it each word is a text
// object of its own in render mode 3, in the picture's flipped pixel rows
// (`1 0 0 -1 0 h cm`), sized by its own ink (4.5 pt for x-height letters, 9 pt
// with an ascender and a descender, in 0.05 pt steps as an ink measure
// gives them) and set in Times at that size: a box narrower than the printed
// word, centred on it. pdf.js reads that layer flipped, one item a word, which
// its TextLayer draws a line above the print.

import zlib from "node:zlib";

// Times-Roman's advances (1/1000 em, ASCII 32..126), written into the OCR
// font's /Widths so that the file and the fixture agree on every box.
const TIMES = [250, 333, 408, 500, 500, 833, 778, 333, 333, 333, 500, 564, 250, 333, 250, 278, 500, 500, 500, 500, 500, 500, 500, 500,
  500, 500, 278, 278, 564, 564, 564, 444, 921, 722, 667, 667, 722, 611, 556, 722, 722, 333, 389, 722, 611, 889, 722, 722, 556, 722,
  667, 556, 611, 722, 722, 944, 722, 722, 611, 333, 278, 333, 469, 500, 333, 444, 500, 444, 500, 444, 333, 500, 500, 278, 278, 500,
  278, 778, 500, 500, 500, 500, 333, 389, 278, 500, 500, 722, 500, 500, 444, 480, 200, 480, 541];
const timesWidth = (text, size) => [...text].reduce((w, c) => w + TIMES[c.charCodeAt(0) - 32], 0) * size / 1000;
// How wide `text` is printed (pt).
export const printedWidth = (text) => timesWidth(text, EM);

export const PAGE_W = 612, PAGE_H = 792;

// The cover: HAL's words, in Helvetica, visible.
export const COVER_TITLE = "A scanned paper of 1992";
export const COVER = [
  "HAL is a multidisciplinary open access archive for the deposit and",
  "dissemination of scientific research documents, whether they are",
  "published or not. The documents may come from teaching and research",
  "institutions in France or abroad, or from public or private research centers.",
];

// The scan's one paragraph, four printed lines.
export const PARAGRAPH = [
  "We consider in this paper an atomic system with three levels forming a",
  "configuration in which the two transitions are driven by two laser fields",
  "with frequencies close to the atomic ones and we denote the detunings by",
  "the usual symbols so that the scattering amplitude of the weak field is",
];

// The print: Times at 10 pt from x = 72, baselines 14 pt apart from 160 pt
// below the page's top; ink 6.8 pt over the baseline with an ascender or a
// capital (4.5 without), 2.2 under it with a descender.
const LEFT = 72, BASE = 160, PITCH = 14, EM = 10, SPACE = 2.5;
const ASCENDS = /[bdfhiklt0-9A-Z]/, DESCENDS = /[gjpqy]/;

// The printed words, in reading order: {line, text, x0, x1, top, bottom, size,
// ox}, in pt from the page's top-left corner (y down). [x0, x1] × [top,
// bottom] is the word's ink in the picture; `size` is the OCR layer's (its ink
// height) and `ox` where the layer's box starts (centred on the print).
export function scanWords() {
  const out = [];
  let last = 0;
  PARAGRAPH.forEach((line, k) => {
    const base = BASE + PITCH * k;
    let x = LEFT;
    for (const text of line.split(" ")) {
      const top = base - (ASCENDS.test(text) ? 6.8 : 4.5);
      // an ink measure: in 0.05 pt steps, never the size of the word before,
      // as on the real layer (pdf.js would glue two of one size into one item)
      let size = Math.round(((base + (DESCENDS.test(text) ? 2.2 : 0) - top) + 0.05 * (out.length % 3)) * 20) / 20;
      if (Math.abs(size - last) < 1e-9) size = Math.round((size + 0.05) * 20) / 20;
      last = size;
      const printed = timesWidth(text, EM), box = timesWidth(text, size);
      out.push({ line: k, text, x0: x, x1: x + printed, top, bottom: top + size, size, ox: x + (printed - box) / 2 });
      x += printed + SPACE;
    }
  });
  return out;
}

// The printed lines: {text, x0, x1, top, bottom} (the union of their words' ink).
export function scanLines(words = scanWords()) {
  return PARAGRAPH.map((text, k) => {
    const ws = words.filter((w) => w.line === k);
    return {
      text, x0: Math.min(...ws.map((w) => w.x0)), x1: Math.max(...ws.map((w) => w.x1)),
      top: Math.min(...ws.map((w) => w.top)), bottom: Math.max(...ws.map((w) => w.bottom)),
    };
  });
}

// The page-sized picture: grey paper, a dark box of ink a word, 2 px a pt.
const DPI_SCALE = 2;
function picture(words) {
  const w = PAGE_W * DPI_SCALE, h = PAGE_H * DPI_SCALE;
  const px = Buffer.alloc(w * h, 248);
  for (const word of words) {
    const r0 = Math.floor(word.top * DPI_SCALE), r1 = Math.ceil(word.bottom * DPI_SCALE);
    const c0 = Math.floor(word.x0 * DPI_SCALE), c1 = Math.ceil(word.x1 * DPI_SCALE);
    for (let r = r0; r < r1; r++) px.fill(70, r * w + c0, r * w + c1);
  }
  return { w, h, data: zlib.deflateSync(px) };
}

const esc = (s) => s.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");

// The two-page PDF's bytes, and what it printed.
export function scanPaper() {
  const words = scanWords();
  const cover = [`BT /F1 14 Tf 72 740 Td (${esc(COVER_TITLE)}) Tj ET`,
    ...COVER.map((line, k) => `BT /F1 10 Tf 72 ${700 - 12 * k} Td (${esc(line)}) Tj ET`)].join("\n");
  const scan = [
    `q ${PAGE_W} 0 0 ${PAGE_H} 0 0 cm /Im1 Do Q`,
    `q 1 0 0 -1 0 ${PAGE_H} cm`,
    ...words.map((w) => `BT 3 Tr /F2 ${w.size.toFixed(2)} Tf 1 0 0 1 ${w.ox.toFixed(3)} ${w.top.toFixed(2)} Tm (${esc(w.text)}) Tj ET`),
    "Q",
  ].join("\n");
  const image = picture(words);
  const stream = (dict, body) => [Buffer.from(`<< ${dict} /Length ${body.length} >>\nstream\n`, "latin1"), body, Buffer.from("\nendstream", "latin1")];
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
    `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAGE_W} ${PAGE_H}] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>`,
    `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAGE_W} ${PAGE_H}] /Resources << /Font << /F2 6 0 R >> /XObject << /Im1 9 0 R >> >> /Contents 8 0 R >>`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman /FirstChar 32 /LastChar 126 /Widths [${TIMES.join(" ")}] >>`,
    stream("", Buffer.from(cover, "latin1")),
    stream("", Buffer.from(scan, "latin1")),
    stream(`/Type /XObject /Subtype /Image /Width ${image.w} /Height ${image.h} /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode`, image.data),
  ];
  const parts = [Buffer.from("%PDF-1.4\n%\xe2\xe3\xcf\xd3\n", "latin1")];
  let size = parts[0].length;
  const offsets = objects.map((body, k) => {
    const at = size;
    const chunk = [Buffer.from(`${k + 1} 0 obj\n`, "latin1"), ...(Array.isArray(body) ? body : [Buffer.from(body, "latin1")]), Buffer.from("\nendobj\n", "latin1")];
    for (const c of chunk) { parts.push(c); size += c.length; }
    return at;
  });
  parts.push(Buffer.from(`xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.map((n) => `${String(n).padStart(10, "0")} 00000 n \n`).join("")}`
    + `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${size}\n%%EOF\n`, "latin1"));
  return { bytes: Buffer.concat(parts), words, lines: scanLines(words) };
}
