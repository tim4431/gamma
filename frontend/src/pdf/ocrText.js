// A page's text from the words the server recognized in its picture
// (docs/dev/pdf_loading.md "A scan's text layer"), as a pdf.js TextContent:
// pdf/pageText.js hands a recognized page here, in place of the PDF's own
// layer, so the text layer, Ctrl+F and the translation's segmentation read
// the recognized words where the server's readers do. Pure, no pdf.js import.
//
// The server keeps each page's words in Tesseract's lines and reading order
// (backend gamma/pdf_ocr.py): {lines: [[[text, x0, y0, x1, y1], ...], ...]},
// the boxes integers in 1/10000 of the page as shown (x of its width, y of
// its height, from its top left corner), the frame of highlight rectangles
// and of pdf.js's viewport at scale 1. Each stored line is one band, never
// chained again with pdf/scanText.js's rule (which could split or join
// Tesseract's lines otherwise than the server reads them), so the page's
// text is exactly the server's: a line's words joined by a space, every line
// ending in a line break.
//
// What comes out is pdf/scanText.js's shape: items in reading order, one a
// word, "word " with the space folded in and the line's last word bare with
// hasEOL, the words tiling their line by scanText's own tiling (Tesseract's
// boxes hug the ink: the cut lies in the middle of each gap), a one-character
// item stretched [2h, ...], styles with ascent 0.8 and descent -0.2 under a
// font id of its own (OCR_FONT, which pdf/translateState.js tells
// pdf/pdfFonts.js has no face to wait for). Each line is laid out upright in
// the page as shown, then every item's matrix is taken to PDF user space
// through the inverse of the viewport's transform, so a rotated or cropped
// page reads its words where they are drawn (an unrotated page gives
// [h, 0, 0, h, x, baseline]). It carries `repaired: true`, which the
// translation reads as text read from a picture, and `ocr: true`.

import { bandLines, tileCuts } from "./scanText.js";

// The font id of recognized words: no PDF font draws them.
export const OCR_FONT = "gamma_ocr";
// The boxes' unit: 1/10000 of the page's width and height as shown.
const UNIT = 10000;

const times = (m, n) => [
  m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1],
  m[0] * n[2] + m[2] * n[3], m[1] * n[2] + m[3] * n[3],
  m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5],
];
function inverse([a, b, c, d, e, f]) {
  const det = a * d - b * c;
  return [d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det];
}

// The words of `lines` that can be laid out, as boxes in the page as shown
// with y up (`H` its height): the frame scanText's lines are measured in.
function shownLines(lines, W, H) {
  const out = [];
  for (const line of Array.isArray(lines) ? lines : []) {
    const ws = [];
    for (const w of Array.isArray(line) ? line : []) {
      if (!Array.isArray(w) || typeof w[0] !== "string" || !w[0] || !w.slice(1, 5).every(Number.isFinite)) continue;
      const [t, x0, y0, x1, y1] = w;
      const top = H - (Math.min(y0, y1) / UNIT) * H, bottom = H - (Math.max(y0, y1) / UNIT) * H;
      ws.push({ t, x0: (Math.min(x0, x1) / UNIT) * W, x1: (Math.max(x0, x1) / UNIT) * W, y0: bottom, y1: top, h: top - bottom });
    }
    if (ws.length) out.push({ ws });
  }
  return out;
}

// The TextContent of a recognized page: its stored `lines` laid out in
// `viewport`, the page's pdf.js viewport at scale 1.
export function ocrText(lines, viewport) {
  const W = viewport.width, H = viewport.height;
  const lns = shownLines(lines, W, H);
  const pageGap = bandLines(lns, true);
  // the page as shown, y up → the viewport's y down → PDF user space; a
  // user unit is `s` of the viewport's pixels
  const toUser = times(inverse(viewport.transform), [1, 0, 0, -1, 0, H]);
  const s = Math.hypot(viewport.transform[0], viewport.transform[1]) || 1;
  const items = [];
  for (const l of lns) {
    const { ws, b0, H: h } = l;
    const cuts = tileCuts(l, { pageGap, tight: true, left: 0, right: W });
    ws.forEach((w, k) => {
      const last = k === ws.length - 1;
      const str = last ? w.t : `${w.t} `;
      items.push({
        str,
        dir: "ltr",
        width: (cuts[k + 1] - cuts[k]) / s,
        height: h / s,
        // stretched for TextLayer as scanText's one-character items are (on
        // an upright or upside-down page: TextLayer reads the stretch off the
        // item's own matrix, which a quarter turn puts on its other terms)
        transform: times(toUser, [[...str].length === 1 ? 2 * h : h, 0, 0, h, cuts[k], b0 + 0.2 * h]),
        fontName: OCR_FONT,
        hasEOL: last,
      });
    });
  }
  return {
    items,
    styles: { [OCR_FONT]: { fontFamily: "serif", ascent: 0.8, descent: -0.2, vertical: false } },
    lang: null,
    repaired: true,
    ocr: true,
  };
}
