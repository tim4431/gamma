// A recognized page's words as a TextContent (src/pdf/ocrText.js): the
// server's lines laid out in scanText's shape, upright in the page as shown
// whatever the page's rotation and crop box, read through pdf.js's own
// viewports.
import test from "node:test";
import assert from "node:assert/strict";
import { Util, getDocument } from "pdfjs-dist/legacy/build/pdf.mjs";
import { OCR_FONT, ocrText } from "../src/pdf/ocrText.js";
import { segmentPage, textRuns } from "../src/pdf/pdfTranslate.js";

// Two printed lines as Tesseract boxes them, in 1/10000 of the page as
// shown: words hugging their ink, "a" an x-height word ending the first line.
const LINES = [
  [["We", 1000, 1000, 1260, 1150], ["consider", 1330, 1000, 2100, 1150], ["a", 2170, 1040, 2240, 1150]],
  [["three-level", 1000, 1170, 2000, 1320], ["atom.", 2080, 1170, 2560, 1320]],
];
const TEXT = "We consider a\nthree-level atom.\n";

const selected = (tc) => tc.items.map((it) => it.str + (it.hasEOL ? "\n" : "")).join("");
const close = (a, b, what) => assert.ok(Math.abs(a - b) < 1e-6, `${what}: ${a} vs ${b}`);

// A one-page PDF with `page` entries (/Rotate, /CropBox) in pdf.js, for its
// scale-1 viewport.
async function viewportOf(page = "") {
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] ${page} /Contents 4 0 R >>`,
    "<< /Length 0 >>\nstream\n\nendstream",
  ];
  let out = "%PDF-1.4\n";
  const at = objects.map((o, k) => { const start = out.length; out += `${k + 1} 0 obj\n${o}\nendobj\n`; return start; });
  const xref = out.length;
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${at.map((n) => `${String(n).padStart(10, "0")} 00000 n \n`).join("")}`;
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  const doc = await getDocument({ data: Uint8Array.from(out, (c) => c.charCodeAt(0)), verbosity: 0 }).promise;
  try {
    return (await doc.getPage(1)).getViewport({ scale: 1 });
  } finally {
    await doc.destroy();
  }
}

// Each item as it is drawn: its matrix in the viewport, and where it starts.
const shown = (tc, vp) => tc.items.map((it) => ({ str: it.str, m: Util.transform(vp.transform, it.transform), width: it.width }));

test("a recognized page reads the server's words, line by line, each line one band its words tile", async () => {
  const vp = await viewportOf();
  const tc = ocrText(LINES, vp);
  assert.equal(tc.repaired, true);
  assert.equal(tc.ocr, true);
  assert.equal(selected(tc), TEXT, "the server's page text: a space between words, a line break after every line");
  assert.deepEqual(tc.styles, { [OCR_FONT]: { fontFamily: "serif", ascent: 0.8, descent: -0.2, vertical: false } });
  assert.ok(tc.items.every((it) => it.fontName === OCR_FONT));
  const [we, consider, a, three, atom] = tc.items;
  assert.deepEqual([we.str, consider.str, a.str, a.hasEOL, three.str, atom.str, atom.hasEOL], ["We ", "consider ", "a", true, "three-level ", "atom.", true]);
  // Upright on an unrotated page, the band from the ink's top to its
  // bottom (y up: 800 - 0.1 × 800 = 720 to 800 - 0.115 × 800 = 708).
  for (const it of [we, consider]) {
    const [h, b, c, d, , f] = it.transform;
    assert.deepEqual([b, c], [0, 0]);
    close(h, d, "not stretched");
    close(d, 12, "the line's height");
    close(f - 0.2 * d, 708, "the band's bottom");
  }
  assert.equal(a.transform[0], 2 * a.transform[3], "a one-character word is stretched, as scanText's are");
  // Boxes hug the ink: each word runs to the middle of the gap after it.
  close(we.transform[4] + we.width, consider.transform[4], "the tiles meet");
  close(consider.transform[4], (0.126 + 0.133) / 2 * 600, "the cut in the middle of the gap");
  // The line's ends reach out over its first and last letters, by half the
  // line's median gap.
  close(we.transform[4], 60 - 0.5 * ((0.133 - 0.126 + 0.217 - 0.21) / 2) * 600, "out over the first letter");
  // The translation's segmenter reads one paragraph of two lines.
  const blocks = segmentPage(textRuns(tc.items, vp.transform, { styles: tc.styles }));
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].text, "We consider a three-level atom.");
});

test("a rotated or cropped page lays the words out where they are shown", async () => {
  const flat = await viewportOf();
  const plain = shown(ocrText(LINES, flat), flat);
  for (const page of ["/Rotate 90", "/Rotate 180", "/Rotate 270", "/CropBox [40 30 560 780]", "/Rotate 90 /CropBox [40 30 560 780]"]) {
    const vp = await viewportOf(page);
    const tc = ocrText(LINES, vp);
    assert.equal(selected(tc), TEXT, page);
    // Drawn upright in the page as shown: the same layout, scaled to the
    // shown page's size.
    const sx = vp.width / flat.width, sy = vp.height / flat.height;
    shown(tc, vp).forEach((it, k) => {
      const want = plain[k];
      close(it.m[1], 0, `${page}: ${it.str} not turned`);
      close(it.m[2], 0, `${page}: ${it.str} not skewed`);
      close(it.m[4], want.m[4] * sx, `${page}: ${it.str} starts where it is shown`);
      close(it.m[5], want.m[5] * sy, `${page}: ${it.str} stands on its line`);
      close(it.width, want.width * sx, `${page}: ${it.str}'s tile`);
    });
  }
  // In PDF space a quarter turn puts the words on their side, and a crop
  // box shifts them with it.
  const turned = ocrText(LINES, await viewportOf("/Rotate 90"));
  close(turned.items[0].transform[0], 0, "a quarter turn");
  const cropped = ocrText(LINES, await viewportOf("/CropBox [40 30 560 780]"));
  close(cropped.items[0].transform[5] - 0.2 * cropped.items[0].transform[3], 30 + 750 * (1 - 0.115), "the band from the crop box's bottom");
});

test("a recognized page with no lines has no text, and unreadable words are left out", async () => {
  const vp = await viewportOf();
  assert.deepEqual(ocrText([], vp).items, []);
  const tc = ocrText([[["", 0, 0, 10, 10], ["ok", 100, 100, 300, 200], ["bad", "x", 0, 1, 1]], [], "nope"], vp);
  assert.equal(selected(tc), "ok\n");
});
