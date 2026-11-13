// The translated view as an annotated-PDF export writes it: what the overlay
// shows over a document's pages (pdf/TransOverlay.jsx), in the terms of POST
// /api/jobs/export's `translation` (backend gamma/pdf_translated.py, which
// redacts each block's original lines and lays its translation out in their
// box). Pure, so the tests run it in Node: a document's view
// (translateState.snapshot) in, the pages to write out.
import { pdfFileHash } from "../documents/pageDocument.js";
import { docIdOf, proxiedSourceOf } from "./pdfSource.js";
import { translatable } from "./transBlock.js";
import { textParts } from "./translateReply.js";

const round = (n) => Math.round(Number(n) * 100) / 100;
const box = (r) => [round(r.x1), round(r.y1), round(r.x2), round(r.y2)];

// The blocks of a page's entry that the overlay draws a translation over, by
// index: a block it translates whose final text has landed, a string. The
// entry's results are a translation, "" (its words went to the part before
// it: masked, nothing written), null (it came back as its source, or was left
// original as part of a reference list, the entry's `left`: the original
// shows) or missing (not landed); the text streamed so far (`partial`) is the
// screen's alone, a file gets only what is final. A page none of whose finals
// holds text has no overlay once its job ended, so it gets nothing either, its
// "" blocks included: a page of nothing but references is neither written nor
// counted. A figure's text the reader asked for is written as the screen shows
// it. A block the reader shows in the original for now (the entry's
// `original`, "Show original here") is written translated all the same: that
// is a look at the page, not a choice of what the file holds.
function written(entry, key) {
  if (!entry || entry.key !== key || !entry.texts?.some(Boolean)) return [];
  const out = [];
  (entry.paras || []).forEach((p, i) => {
    if (translatable(p) && typeof entry.texts[i] === "string") out.push(i);
  });
  return out;
}

// How a block is set besides its size, as the overlay sets it, and only what it
// has: the `box` a one-line block's text may fill and the `foot` a block's text
// stops at (pdf/pageShape.js), the same as on screen, `centred` or `justified`,
// `bold`, `italic`, a paragraph opener's `indent`, which a centred block does
// not have (transBlock.js blockSetting), and the `face` it is mostly set in,
// the font's name, which the server looks for in the file (pdf/paperFace.js).
// Its leading the server works out from the lines, by the overlay's rule, and
// its ground and ink it reads from the file: a page exported may never have
// been shown, so what the overlay sampled is not there to send.
const setting = (p) => ({
  ...(p.room && { box: box({ ...p, ...p.room }) }), ...(p.foot != null && { foot: round(p.foot) }),
  ...(p.centred && { centred: true }), ...(p.justified && { justified: true }),
  ...(p.bold && { bold: true }), ...(p.italic && { italic: true }),
  ...(p.indent > 0 && !p.centred && { indent: round(p.indent) }), ...(p.face && { face: p.face }),
});

// What the export writes of a document's translated view: {pages: [{page,
// blocks: [{lines, size, parts}]}]}, pages in order, or null when it writes
// nothing (the translation hidden, or no page holds one under `key`, the
// language and model the viewer shows). A block's `lines` are its original
// line boxes, `size` its font size, both at scale 1 as the viewer reads the
// page, and `parts` its translation: text, and for each formula token the
// formula's {page, box, base, text}, the box it is printed in, its baseline
// and its extracted text. Its tokens are parsed as the overlay parses them
// (translateReply.js textParts); `spanOf(pn, i, k)` looks up a token another
// block owns (translateState.span). The block's `setting` goes with them.
export function exportLayout(view, key, spanOf = null) {
  if (!view?.shown || !key) return null;
  const pages = [];
  for (const [pn, entry] of [...(view.entries || [])].sort((a, b) => a[0] - b[0])) {
    const blocks = written(entry, key).map((i) => {
      const p = entry.paras[i];
      const parts = textParts(entry.texts[i], { spans: p.spans, page: pn, spanOf }).map((part) => (
        typeof part === "string" ? part
          : { page: part.page, box: box(part.span), base: round(part.span.base), text: part.span.str || "" }));
      return { lines: (p.lines || []).map(box), size: round(p.size), parts, ...setting(p) };
    });
    if (blocks.length) pages.push({ page: pn, blocks });
  }
  return pages.length ? { pages } : null;
}

// The file a page's annotated PDF writes the translation into, or "" when it
// writes none. The job reads the stored file the page's document names
// (`doc`, its pageDocument: pdfFileHash, a duplicated page's the original's)
// and refuses another, so the address the viewer opens for the page (`url`,
// the one its translation is kept under) must be that file: the upload
// itself, or the proxy reading the page's link, whose saved copy the job
// reads (a link page with no copy kept fails its annotated PDF, translated
// or not). A page shown from an address alone names no stored file, and
// writes none.
export function exportFile(url, doc) {
  const file = pdfFileHash(doc);
  if (!file) return "";
  return docIdOf(url) === file || (!docIdOf(url) && proxiedSourceOf(url) === doc.url) ? file : "";
}

// How many pages exportLayout would write, without laying them out: what the
// export dialog says.
export function exportPages(view, key) {
  if (!view?.shown || !key) return 0;
  let n = 0;
  for (const entry of (view.entries || new Map()).values()) if (written(entry, key).length) n += 1;
  return n;
}
