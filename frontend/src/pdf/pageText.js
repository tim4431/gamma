// A page's text as every reader of it gets it: the words the server
// recognized in the page's picture (pdf/ocrState.js, laid out by
// pdf/ocrText.js) where it has them, else pdf.js's TextContent, with a
// scan's invisible OCR layer repaired (pdf/scanText.js) when
// pdf/scanDetect.js calls the page one. The text layer and Ctrl+F
// (pdf/PdfViewer.jsx), the translation's segmentation (pdf/translateState.js)
// and the bench read it here, so what a reader selects, what a search marks
// and the paragraphs a translation sends are made of the same words in the
// same places.
//
// Only the verdict is kept, per page proxy (pdf.js hands out one proxy a page
// until the document is destroyed): the TextContent is read afresh each time
// as before, since a book read through would otherwise hold every page's
// items, and the repair costs a few milliseconds. A document's pages are
// decided one at a time, so the one that needs the operator list teaches its
// fonts to the next (ScanMemo); the document is known by its commonObjs,
// which all its pages share. A recognized page is never decided: its words
// are the server's, and no operator list is built for them.

import { ScanMemo, confirm, prescreen } from "./scanDetect.js";
import { repairTextContent } from "./scanText.js";
import { ocrText } from "./ocrText.js";
import * as ocrState from "./ocrState.js";

const VERDICT = new WeakMap(); // PDFPageProxy -> boolean | Promise<boolean>
const DOCS = new WeakMap();    // a document's commonObjs -> {memo, turn}
const RECOGNIZED = new WeakSet(); // the page proxies last read from recognized words

// The page's TextContent: built from its recognized words (it then carries
// `repaired: true` and `ocr: true`), or repaired when the page is a scan
// with an OCR layer (`repaired: true`).
export async function pageText(page) {
  const lines = await ocrState.lines(page);
  if (lines) {
    RECOGNIZED.add(page);
    return ocrText(lines, page.getViewport({ scale: 1 }));
  }
  RECOGNIZED.delete(page);
  const tc = await page.getTextContent();
  const scan = await isScan(page, tc);
  if (scan || !tc.items.some((it) => /\S/.test(it.str || ""))) ocrState.noteScan(page);
  return scan ? repairTextContent(tc, { view: page.view }) : tc;
}

// What is known of the page so far: true or false once decided, undefined
// while it is not (its text not read yet). A page read from recognized
// words is a scan's.
export function knownScan(page) {
  if (page && RECOGNIZED.has(page)) return true;
  const v = page && VERDICT.get(page);
  return typeof v === "boolean" ? v : undefined;
}

function isScan(page, tc) {
  let v = VERDICT.get(page);
  if (v === undefined) {
    const pre = prescreen(tc);
    v = pre.pass ? decide(page, pre) : false;
    VERDICT.set(page, v);
    if (typeof v !== "boolean") v.then((scan) => VERDICT.set(page, scan));
  }
  return v;
}

function decide(page, pre) {
  const key = page.commonObjs || page;
  let d = DOCS.get(key);
  if (!d) DOCS.set(key, (d = { memo: new ScanMemo(), turn: Promise.resolve() }));
  const verdict = d.turn.then(async () => {
    const guess = d.memo.guess(pre);
    if (guess !== null) return guess;
    try {
      const [list, { OPS }] = await Promise.all([page.getOperatorList(), import("pdfjs-dist/legacy/build/pdf.mjs")]);
      const found = confirm(list, page.getViewport({ scale: 1 }), OPS);
      d.memo.record(found);
      return found.scan;
    } catch {
      // A page whose operator list cannot be read (its document going away)
      // keeps its text as pdf.js gives it.
      return false;
    }
  });
  d.turn = verdict;
  return verdict;
}
