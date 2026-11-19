// A scan's invisible OCR layer: detected (pdf/scanDetect.js), repaired
// (pdf/scanText.js) and read through one door (pdf/pageText.js). The layers
// here are made the way the study's producers make them
// (docs/research/ocr.md); a born-digital page must come through untouched.

import test from "node:test";
import assert from "node:assert/strict";
import { OPS, getDocument } from "pdfjs-dist/legacy/build/pdf.mjs";
import { ScanMemo, confirm, prescreen } from "../src/pdf/scanDetect.js";
import { repairTextContent } from "../src/pdf/scanText.js";
import { knownScan, pageText } from "../src/pdf/pageText.js";
import { segmentPage, textRuns } from "../src/pdf/pdfTranslate.js";

const W = 510, H = 708;
const UPRIGHT = [1, 0, -0, -1, 0, H]; // the scale-1 viewport of an unrotated W × H page

const PARAGRAPH = [
  "We consider in this paper an atomic system with three levels forming a",
  "configuration in which the two transitions are driven by two laser fields",
  "with frequencies close to the atomic ones and we denote the detunings by",
  "the usual symbols so that the scattering amplitude of the weak field is",
];
const X_ONLY = /^[acemnorsuvwxz]+[.,;:]?$/, DESCENDS = /[gjpqy]/;

// The worst layer of the study (Lounis 1992): a word an item, written in the
// picture's flipped pixel rows ([s, 0, 0, -s, x, top] in PDF space, the print
// hanging down from `top`), its size its own ink's height, its width a
// substitute font's at that size: about 0.73 of the printed word, centred on
// it. pdf.js puts " " items between the words and an empty item with hasEOL
// where it sees a line break, also in the middle of a line.
function flippedLayer(lines, { x0 = 60, top = 600, pitch = 14, em = 10 } = {}) {
  const items = [];
  lines.forEach((line, k) => {
    const T = top - k * pitch, B = T - 0.7 * em, X = B + 0.45 * em, D = B - 0.22 * em;
    let x = x0;
    for (const [n, word] of line.split(" ").entries()) {
      const printed = word.length * 0.5 * em;
      const f = X_ONLY.test(word) || !/[A-Zbdfhiklt0-9]/.test(word) ? X : T;
      const s = f - (DESCENDS.test(word) ? D : B);
      if (n > 0) items.push({ str: " ", dir: "ltr", width: 22.7, height: 0, transform: [s, 0, 0, -s, x - 3, f], fontName: "g_d0_f1", hasEOL: false });
      if (n === 5) items.push({ str: "", dir: "ltr", width: 0, height: 0, transform: [s, 0, 0, -s, x, f], fontName: "g_d0_f1", hasEOL: true });
      items.push({ str: word, dir: "ltr", width: 0.73 * printed, height: s, transform: [s, 0, 0, -s, x + 0.135 * printed, f], fontName: "g_d0_f1", hasEOL: false });
      x += printed + 0.3 * em;
    }
    items.push({ str: "", dir: "ltr", width: 0, height: 0, transform: [1, 0, 0, -1, x, T], fontName: "g_d0_f1", hasEOL: true });
  });
  return { items, styles: { g_d0_f1: { fontFamily: "serif", ascent: 0.683, descent: -0.217, vertical: false } }, lang: null };
}

// An Acrobat Capture layer: upright, one size a line, each box starting at
// its word and a substitute font's width short of it.
function emLayer(lines, { x0 = 60, base = 600, pitch = 14, em = 10 } = {}) {
  const items = [];
  lines.forEach((line, k) => {
    let x = x0;
    const words = line.split(" ");
    words.forEach((word, n) => {
      const printed = word.length * 0.5 * em;
      items.push({ str: word, dir: "ltr", width: 0.8 * printed, height: em, transform: [em, 0, 0, em, x, base - k * pitch], fontName: "g_d0_f2", hasEOL: n === words.length - 1 });
      if (n < words.length - 1) items.push({ str: " ", dir: "ltr", width: 2, height: 0, transform: [em, 0, 0, em, x + 0.8 * printed, base - k * pitch], fontName: "g_d0_f2", hasEOL: false });
      x += printed + 0.3 * em;
    });
  });
  return { items, styles: { g_d0_f2: { fontFamily: "sans-serif", ascent: 0.905, descent: -0.212, vertical: false } }, lang: null };
}

// What a reader selects and copies: the items joined, a line break after hasEOL.
const selected = (tc) => tc.items.map((it) => it.str + (it.hasEOL ? "\n" : "")).join("");
const wordsOf = (tc) => selected(tc).split(/\s+/).filter(Boolean);
// The repaired items as lines: items up to and including one with hasEOL.
function linesOf(tc) {
  const out = [[]];
  for (const it of tc.items) {
    out[out.length - 1].push(it);
    if (it.hasEOL) out.push([]);
  }
  return out.filter((l) => l.length);
}

test("the prescreen passes a scan's layer by its traces and a typeset page by none", () => {
  assert.equal(prescreen(flippedLayer(PARAGRAPH)).pass, true);
  assert.match(prescreen(flippedLayer(PARAGRAPH)).why, /mirror/);
  const typeset = { items: PARAGRAPH.map((s, k) => ({ str: s, transform: [9.96, 0, 0, 9.96, 72.18 + (k % 2) * 0.37, 600.5 - k * 11.955], width: 300, fontName: "g_d0_f3" })) };
  assert.deepEqual(prescreen(typeset), { pass: false, why: "typeset", fonts: { g_d0_f3: PARAGRAPH.join("").length } });
  assert.equal(prescreen({ items: [] }).why, "no upright text");
  // Tz fitted to each word box (Tesseract's GlyphLessFont) is a trace too.
  const stretched = { items: PARAGRAPH[0].split(" ").map((w, k) => ({ str: w, transform: [8.3 + (k % 3), 0, 0, 10, 60 + 30.13 * k, 600.37], width: 20, fontName: "g_d0_f4" })) };
  assert.match(prescreen(stretched).why, /scaled/);
});

test("the confirm needs nearly all text invisible over pictures covering most of the page", () => {
  const vp = { transform: UPRIGHT, width: W, height: H };
  const glyphs = (s) => [...s].map((c) => ({ unicode: c, isSpace: c === " " }));
  const list = (ops) => ({ fnArray: ops.map((o) => o[0]), argsArray: ops.map((o) => o[1] || null) });
  const picture = (w = W, h = H) => [[OPS.save], [OPS.transform, [w, 0, 0, h, 0, 0]], [OPS.paintImageXObject, ["img_p0_1", 2, 2]], [OPS.restore]];
  const text = (mode, s = "invisible words of the layer") => [[OPS.setTextRenderingMode, [mode]], [OPS.setFont, ["g_d0_f1", 10]], [OPS.showText, [glyphs(s)]]];
  const scan = confirm(list([...picture(), [OPS.save], ...text(3), [OPS.restore]]), vp, OPS);
  assert.equal(scan.scan, true, scan.why);
  assert.deepEqual(scan.fonts, { g_d0_f1: [0, 24] });
  assert.equal(confirm(list([...picture(), ...text(0)]), vp, OPS).scan, false, "visible text over a picture is typeset");
  assert.equal(confirm(list(text(3)), vp, OPS).scan, false, "invisible text alone (LaTeXiT's payload) is no scan");
  assert.equal(confirm(list([...picture(W, H * 0.5), ...text(3)]), vp, OPS).scan, false, "half the page is a figure");
  assert.equal(confirm(list([...picture(W, H * 0.85), ...text(7)]), vp, OPS).scan, true, "Tr 7 is invisible too, and margins leave room");
  // Strips (Acrobat Capture cuts a page into ~19) cover it together.
  const strips = [];
  for (let k = 0; k < 19; k++) strips.push([OPS.save], [OPS.transform, [W, 0, 0, H / 19, 0, (k * H) / 19]], [OPS.paintImageMaskXObject, [{}]], [OPS.restore]);
  assert.equal(confirm(list([...strips, ...text(3)]), vp, OPS).scan, true);
  // A form's own render mode ends with it; annotations are not the page.
  const formed = [[OPS.paintFormXObjectBegin, [[1, 0, 0, 1, 0, 0], null]], [OPS.setTextRenderingMode, [3]], [OPS.paintFormXObjectEnd], ...text(0)];
  assert.equal(confirm(list([...picture(), ...formed]), vp, OPS).invisibleShare, 0);
  assert.equal(confirm(list([...picture(), [OPS.beginAnnotation], ...text(3)]), vp, OPS).why, "no text shown");
});

test("a document's confirmed fonts answer for its later pages", () => {
  const memo = new ScanMemo();
  const pre = { fonts: { g_d0_f1: 300 } };
  assert.equal(memo.guess(pre), null, "nothing known yet");
  memo.record({ scan: true, fonts: { g_d0_f1: [0, 1999] } });
  assert.equal(memo.guess(pre), true);
  assert.equal(memo.guess({ fonts: { g_d0_f1: 10, g_d0_f9: 4 } }), null, "a font not seen yet");
  memo.record({ scan: false, fonts: { g_d0_f5: [800, 0] } });
  assert.equal(memo.guess({ fonts: { g_d0_f5: 50 } }), false);
  memo.record({ scan: false, fonts: { g_d0_f1: [5, 0] } });
  assert.equal(memo.guess(pre), null, "a font that also drew visible text is asked about again");
});

test("a flipped, ink-sized layer becomes one upright band a line, its words tiling it", () => {
  const tc = flippedLayer(PARAGRAPH);
  const out = repairTextContent(tc);
  assert.equal(out.repaired, true);
  assert.deepEqual(wordsOf(out), wordsOf(tc), "the words are the layer's, in its order");
  assert.equal(selected(out), `${PARAGRAPH.join("\n")}\n`, "one line break a printed line, none in the middle of one");
  const lines = linesOf(out);
  assert.equal(lines.length, 4);
  lines.forEach((line, k) => {
    const T = 600 - k * 14, D = T - 0.7 * 10 - 0.22 * 10;
    for (const it of line) {
      const [p, b, c, h, , f] = it.transform;
      assert.deepEqual([b, c], [0, 0]);
      // upright; a one-character item ("a" ends the first line) stretched,
      // or TextLayer would draw it at a serif's width instead of its own
      assert.equal(p, it.str.length === 1 ? 2 * h : h);
      assert.ok(Math.abs(f - 0.2 * h - D) < 1e-9 && Math.abs(f + 0.8 * h - T) < 1e-9, "the span is the printed band, descender to ascender");
    }
    for (let i = 0; i + 1 < line.length; i++) {
      assert.ok(Math.abs(line[i].transform[4] + line[i].width - line[i + 1].transform[4]) < 1e-9, "each word runs to the next");
      assert.match(line[i].str, / $/, "with the space folded in");
    }
    assert.equal(line.at(-1).hasEOL, true);
  });
  // Boxes centred on their words: the cut lies in the middle of the gap.
  const [first, second] = lines[0];
  const b0 = tc.items.find((it) => it.str === "We"), b1 = tc.items.find((it) => it.str === "consider");
  assert.ok(Math.abs(second.transform[4] - (b0.transform[4] + b0.width + b1.transform[4]) / 2) < 1e-9);
  assert.equal(first.fontName, "g_d0_f1", "the font id stays, for pdf/pdfFonts.js");
  assert.deepEqual(out.styles.g_d0_f1, { fontFamily: "serif", ascent: 0.8, descent: -0.2, vertical: false });
  // A line's start reaches out over the start of its word, whose centred box
  // begins inside it: as far as the page's box allows, or without it, the
  // page's outermost box.
  const onPage = linesOf(repairTextContent(tc, { view: [0, 0, W, H] }));
  assert.ok(onPage[0][0].transform[4] < b0.transform[4], "out over the word's first letter");
  assert.equal(first.transform[4], b0.transform[4], "the leftmost box stands for the page's edge");
  const edge = b0.transform[4] - 0.35; // the page's edge just left of the first box
  assert.equal(linesOf(repairTextContent(tc, { view: [edge, 0, W, H] }))[0][0].transform[4], edge, "never past the page's edge");
});

test("an em-sized layer's boxes start at their words: the cut lies just before the next box", () => {
  const out = repairTextContent(emLayer(PARAGRAPH.slice(0, 2)));
  const [line] = linesOf(out);
  const H = 0.95 * 10; // the OCR convention: descender 0.2 em, ascender 0.75 em
  assert.ok(Math.abs(line[0].height - H) < 1e-9);
  const words = PARAGRAPH[0].split(" ");
  let x = 60;
  for (let k = 1; k < words.length; k++) {
    const end = x + 0.8 * words[k - 1].length * 5;
    x += words[k - 1].length * 5 + 3;
    // the gap is 3 + the box's shortfall; the cut sits 0.35 H before the box
    const cut = Math.max((end + x) / 2, x - 0.35 * H);
    assert.ok(Math.abs(line[k].transform[4] - cut) < 1e-9, `word ${k}`);
  }
});

test("a column gutter and a jump back end a line; stream order is never sorted", () => {
  const left = flippedLayer(["the atom is in a state of the field", "and the laser drives it now"], { x0: 40 });
  const right = flippedLayer(["a second column at the same height", "goes on below it for a while"], { x0: 300 });
  // OCR writes the left column, then the right one.
  const tc = { ...left, items: [...left.items, ...right.items] };
  const out = repairTextContent(tc);
  assert.deepEqual(linesOf(out).map((l) => l.map((it) => it.str.trim()).join(" ")),
    ["the atom is in a state of the field", "and the laser drives it now", "a second column at the same height", "goes on below it for a while"]);
  // One OCR line across the gutter splits there too.
  const across = flippedLayer(["words on the left and more", "of them"]);
  const far = across.items.find((it) => it.str === "more");
  far.transform = [...far.transform.slice(0, 4), far.transform[4] + 200, far.transform[5]];
  assert.equal(linesOf(repairTextContent(across)).length, 3);
});

test("rotated text passes through, and a word glued onto it keeps the join", () => {
  const tc = emLayer(["one two three"]);
  const label = { str: "axis", dir: "ltr", width: 20, height: 10, transform: [0, 10, -10, 0, 300, 300], fontName: "g_d0_f2", hasEOL: false };
  tc.items.push(label);
  const out = repairTextContent(tc);
  assert.deepEqual(out.items.at(-1), { ...label, hasEOL: true }, "the rotated item as it was, ending its line");
  assert.equal(selected(out), "one two three\naxis\n");
  // Glued to a word with no space between them, it keeps the join.
  const glued = emLayer(["one two three"]);
  glued.items.at(-1).hasEOL = false;
  glued.items.push({ ...label, str: "²" });
  const joined = repairTextContent(glued);
  assert.equal(selected(joined), "one two three²\n");
  // A rotated line (a table turned on its side, a skewed caption) keeps
  // pdf.js's breaks: none between its words.
  const turned = (str, y, eol = false) => ({ str, dir: "ltr", width: 20, height: 10, transform: [0, 10, -10, 0, 300, y], fontName: "g_d0_f2", hasEOL: eol });
  const table = emLayer(["one two three"]);
  table.items.push(turned("Table", 100), turned(" ", 125), turned("of", 128), turned(" ", 140), turned("results", 143, true));
  assert.equal(selected(repairTextContent(table)), "one two three\nTable of results\n");
});

test("repaired lines read as one paragraph to the translation's segmenter", () => {
  const vp = { transform: UPRIGHT };
  const raw = flippedLayer(PARAGRAPH);
  const before = segmentPage(textRuns(raw.items, vp.transform, { styles: raw.styles }));
  const fixed = repairTextContent(raw);
  const after = segmentPage(textRuns(fixed.items, vp.transform, { styles: fixed.styles }));
  assert.ok(before.length > 4, `the raw layer falls apart (${before.length} blocks)`);
  assert.equal(after.length, 1);
  assert.equal(after[0].nLines, 4);
  assert.equal(after[0].text, PARAGRAPH.join(" "));
});

// --- one door ----------------------------------------------------------------

// A page as pdf.js hands it out, counting its operator lists.
function mockPage(tc, { scan, commonObjs = {}, lists }) {
  return {
    commonObjs,
    getTextContent: async () => structuredClone(tc),
    getViewport: () => ({ transform: UPRIGHT, width: W, height: H }),
    getOperatorList: async () => {
      lists.n++;
      const glyphs = [...selected(tc)].map((c) => ({ unicode: c }));
      return {
        fnArray: [OPS.save, OPS.transform, OPS.paintImageXObject, OPS.restore, OPS.setTextRenderingMode, OPS.setFont, OPS.showText],
        argsArray: [null, [W, 0, 0, H, 0, 0], ["img", 1, 1], null, [scan ? 3 : 0], ["g_d0_f1", 1], [glyphs]],
      };
    },
  };
}

test("pageText repairs a scan's page, leaves a typeset one as it is, and asks the operator list once a document", async () => {
  const lists = { n: 0 };
  const doc = {};
  const page1 = mockPage(flippedLayer(PARAGRAPH), { scan: true, commonObjs: doc, lists });
  const page2 = mockPage(flippedLayer(PARAGRAPH.slice(1)), { scan: true, commonObjs: doc, lists });
  assert.equal(knownScan(page1), undefined);
  const [a, b] = await Promise.all([pageText(page1), pageText(page2)]);
  assert.equal(a.repaired && b.repaired, true);
  assert.equal(lists.n, 1, "the second page's fonts were known from the first");
  assert.equal(knownScan(page1), true);
  await pageText(page1);
  assert.equal(lists.n, 1, "the verdict is kept per page");
  // A typeset page whose text the prescreen passes (on a device grid) is
  // confirmed and handed back untouched.
  const flat = { items: PARAGRAPH.map((s, k) => ({ str: s, transform: [10, 0, 0, 10, 72, 600 - 12 * k], width: 300, fontName: "g_d0_f1", hasEOL: true })), styles: {} };
  const typeset = mockPage(flat, { scan: false, commonObjs: {}, lists });
  const same = await pageText(typeset);
  assert.equal(same.repaired, undefined);
  assert.deepEqual(same, flat);
  assert.equal(knownScan(typeset), false);
  // An operator list that fails leaves the text as pdf.js gave it.
  const broken = { ...mockPage(flippedLayer(PARAGRAPH), { scan: true, commonObjs: {}, lists }), getOperatorList: async () => { throw new Error("destroyed"); } };
  assert.equal((await pageText(broken)).repaired, undefined);
});

// A one-page scan as the study's worst producer writes it: a picture over the
// page, then the words in render mode 3, each its own text object in the
// picture's flipped rows, sized by its ink.
function scanPdf() {
  const content = ["q 510 0 0 708.48 0 0 cm /Im1 Do Q", "q 1 0 0 -1 0 708.48 cm BT 3 Tr"];
  PARAGRAPH.slice(0, 3).forEach((line, k) => {
    let x = 60;
    for (const word of line.split(" ")) {
      const size = X_ONLY.test(word) ? 4.5 : 7;
      content.push(`/F1 ${size} Tf 1 0 0 1 ${x.toFixed(2)} ${(110 + 14 * k + (size === 7 ? 0 : 2.5)).toFixed(2)} Tm (${word}) Tj`);
      // printed at about 10 pt: 0.42 em a letter, a 0.3 em space
      x += word.length * 4.2 + 3;
    }
  });
  content.push("ET Q");
  const stream = content.join("\n");
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 510 708.48] /Resources << /Font << /F1 4 0 R >> /XObject << /Im1 6 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman >>",
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
    "<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 4 >>\nstream\n\xff\xf0\xf0\xff\nendstream",
  ];
  let out = "%PDF-1.4\n";
  const at = objects.map((o, k) => { const start = out.length; out += `${k + 1} 0 obj\n${o}\nendobj\n`; return start; });
  const xref = out.length;
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${at.map((n) => `${String(n).padStart(10, "0")} 00000 n \n`).join("")}`;
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Uint8Array.from(out, (c) => c.charCodeAt(0));
}

test("a scan written by hand is found and repaired through pdf.js's own operator list", async () => {
  const doc = await getDocument({ data: scanPdf(), verbosity: 0 }).promise;
  try {
    const page = await doc.getPage(1);
    const raw = await page.getTextContent();
    assert.ok(raw.items.some((it) => it.transform[3] < 0), "pdf.js reads the layer flipped");
    const tc = await pageText(page);
    assert.equal(tc.repaired, true);
    assert.equal(knownScan(page), true);
    assert.equal(selected(tc), `${PARAGRAPH.slice(0, 3).join("\n")}\n`);
    assert.ok(tc.items.every((it) => it.transform[3] > 0), "upright");
    const tops = new Set(linesOf(tc).map((l) => l[0].transform[5]));
    assert.equal(tops.size, 3, "one baseline a line");
  } finally {
    await doc.destroy();
  }
});
