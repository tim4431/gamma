// The paper's own typeface for a translated block in the overlay
// (pdf/TransOverlay.jsx): the font most of the block is set in
// (pdfTranslate.js blockFace, `font`, pdf.js's id for it), when that font draws
// every letter of the translation, and none otherwise, so the block keeps the
// overlay's serif (--font-page). pdf.js loads an embedded font into the
// document's fonts under that id, with the characters it knows mapped, so CSS
// sets text in it as the page draws its own. A subset holds only the letters
// the paper used, so a translation into another language often needs one it
// lacks, and one face per block, not a letter in another face here and there,
// is the rule. The export decides the same thing on the file's own font
// (gamma/pdf_translated.py _paper_faces). docs/dev/translation.md "How a
// translation is set" ("The paper's own face").
//
// The id is one open document's: the translation keeps a document's blocks
// longer than the viewer keeps its pdf.js document, and destroying that
// document takes its fonts out of `document.fonts`. A block whose font is
// gone is set in the serif, with its own weight and slant, and what was read
// of a font holds only while the FontFace it was read from is there.

// What needs no glyph of the font: blanks (TeX's fonts have no space, which the
// browser takes from the next face) and format characters.
const UNSEEN = /[\s\p{Cf}]/u;

// Whether `has(ch)` says yes to every character of `texts` but the unseen.
export function covers(texts, has) {
  for (const text of texts) {
    for (const ch of text) if (!UNSEEN.test(ch) && !has(ch)) return false;
  }
  return true;
}

// The family a block's translation is set in, its text being `texts` (the
// words, the formulas left out: they are pictures, or their own text), or
// null for the overlay's own. `has(font, ch)` says whether the font draws a
// character, `live(font)` whether the font is loaded at all.
export function paperFamily(block, texts, has, live = fontLive) {
  const font = block?.font;
  return font && live(font) && covers(texts, (ch) => has(font, ch)) ? font : null;
}

// The FontFace pdf.js added to the document's fonts for `font`, its id, or
// null once it is gone (or outside a browser). What is known of the font is
// kept with the FontFace it was read from (`SEEN`), so a font gone and
// another under its id start over.
function liveFace(font) {
  const fonts = typeof document === "undefined" ? null : document.fonts;
  if (!fonts) return null;
  const seen = SEEN.get(font);
  if (seen && fonts.has(seen.face)) return seen.face;
  SEEN.delete(font);
  for (const face of fonts) {
    if (face.family.replace(/^["']|["']$/g, "") === font && face.status !== "error") return face;
  }
  return null;
}

// Whether `font` is in the document's fonts.
export function fontLive(font) {
  return liveFace(font) !== null;
}

// Whether `font`, a family in the document's fonts, draws `ch`, read from a
// canvas: the character measured in it with two fallbacks behind it is as wide
// both ways only when the font has it (two fallbacks that differ alone, or
// nothing can be told and the answer is no), and its glyph must hold ink,
// since a subset can keep a letter's place and empty its outline. Kept per
// character while the font's FontFace is loaded; a font gone draws nothing.
const SIZE = 100;
const FALLBACKS = ["monospace", "serif", "sans-serif"];
const SEEN = new Map(); // font id → {face: its FontFace, known: Map(ch → bool)}
let canvas = null;
export function hasGlyph(font, ch) {
  const face = liveFace(font);
  if (!face) return false;
  let seen = SEEN.get(font);
  if (!seen) SEEN.set(font, (seen = { face, known: new Map() }));
  if (!seen.known.has(ch)) seen.known.set(ch, measure(font, ch));
  return seen.known.get(ch);
}

function measure(font, ch) {
  if (typeof document === "undefined") return false;
  try {
    canvas ||= document.createElement("canvas").getContext("2d");
    const metrics = (family) => { canvas.font = `${SIZE}px ${family}`; return canvas.measureText(ch); };
    const widths = FALLBACKS.map((family) => metrics(family).width);
    const pair = [[0, 1], [0, 2], [1, 2]].find(([i, j]) => widths[i] !== widths[j]);
    if (!pair) return false;
    const [a, b] = pair.map((k) => metrics(`"${font}", ${FALLBACKS[k]}`));
    return a.width === b.width && a.actualBoundingBoxLeft + a.actualBoundingBoxRight > 0;
  } catch {
    return false;
  }
}
