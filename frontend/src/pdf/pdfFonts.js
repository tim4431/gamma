// The real font names behind a page's text runs, for the translated view's
// formula detection (pdf/pdfMath.js). No pdf.js import on purpose: it works
// on any PDFPageProxy, so the viewer and the translation bench
// (tests/bench/translateBench.mjs) read names the same way.
//
// A text item's `fontName` is pdf.js's per-document id (`g_d3_f12`), not the
// PDF's name. The name ("KXFCTZ+CMMI10") is on the FontFaceObject that
// pdf.js puts in `page.commonObjs` under that id, but only once an operator
// list has been built for a page that uses the font: getTextContent alone
// never sends it. A rendered page has it; for a page only segmented ahead of
// the view, `page.getOperatorList()` sends it. The object resolves after the
// font face binds, so `has()` can still be false right after the list
// resolves, and `get(id, cb)` waits for it.

const SUBSET = /^[A-Z]{6}\+/;

// What the classifier needs of a font: its name without the subset tag, and
// whether it is a Type 3 font (glyph procedures, often bitmap TeX fonts,
// whose name is just "Type3").
function faceOf(obj) {
  if (!obj || typeof obj !== "object") return null; // an error font resolves to a string
  return { name: String(obj.name || "").replace(SUBSET, ""), type3: !!obj.isType3Font };
}

// Map(fontId → {name, type3}) for `ids`, from `known` first (a document-wide
// cache the caller keeps: ids are stable within one open document), then
// the page's commonObjs, building the page's operator list once when a name
// is missing. Ids that never resolve within `timeout` ms are left out; the
// classifier then falls back to the characters alone. `opList` is the
// caller's say over the operator list: false skips it (reads only what is
// already there).
export async function pageFaces(page, ids, { known = null, timeout = 3000, opList = true } = {}) {
  const out = new Map();
  const objs = page?.commonObjs;
  const missing = [];
  for (const id of ids) {
    if (!id) continue;
    if (known?.has(id)) { const f = known.get(id); if (f) out.set(id, f); continue; }
    if (objs?.has(id)) {
      const f = faceOf(objs.get(id));
      known?.set(id, f);
      if (f) out.set(id, f);
    } else missing.push(id);
  }
  if (!missing.length || !objs || !opList) return out;
  try { await page.getOperatorList(); } catch { return out; }
  let timer;
  const deadline = new Promise((resolve) => { timer = setTimeout(resolve, timeout); });
  await Promise.race([
    Promise.all(missing.map((id) => new Promise((resolve) => {
      try { objs.get(id, resolve); } catch { resolve(); }
    }))),
    deadline,
  ]);
  clearTimeout(timer);
  for (const id of missing) {
    if (!objs.has(id)) continue;
    const f = faceOf(objs.get(id));
    known?.set(id, f);
    if (f) out.set(id, f);
  }
  return out;
}
