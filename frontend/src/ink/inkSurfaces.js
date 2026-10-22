// The page's ink by surface (useInkSurfaces.js; docs/dev/handwriting.md
// "Client"), pure: each surface's groups, what messages call a surface,
// and where a paste without a point goes. frontend/tests/
// inkSurfaces.test.mjs pins them. A surface is a PDF page, by its number,
// or a notebook sheet, by its id.

// Each surface's groups: a PDF page's by their position's page
// (`inkBlocks`, the page's groups as {id, properties}), a sheet's by its
// id (`nbInk`, notebook/notebook.js inkBySheet), which take the key over.
export function groupsBySurface(inkBlocks, nbInk) {
  const map = new Map();
  for (const b of inkBlocks) {
    const p = b.properties?.pdf_position?.pageNumber;
    if (!p) continue;
    if (!map.has(p)) map.set(p, []);
    map.get(p).push(b);
  }
  for (const [id, groups] of nbInk) map.set(id, groups);
  return map;
}

// What a stroke's `page` is called in messages: a PDF page's number, or a
// sheet's place among `sheets` (from 1), "?" for a sheet not among them.
export function surfaceLabel(page, sheets) {
  if (typeof page !== "string") return page;
  const i = sheets.findIndex((s) => s.id === page);
  return i >= 0 ? i + 1 : "?";
}

// Where a paste without a point goes, {page, size} in points, or null: in
// the notebook view its current sheet (`current`), else its last, with
// its paper's size; else the PDF page in view (`inView`, {page, size})
// while a PDF shows.
export function pasteTargetOf({ notebook, sheets, current, inView, pdfUrl, pdfHidden }) {
  if (notebook) {
    const sheet = sheets.find((s) => s.id === current) || sheets.at(-1);
    return sheet ? { page: sheet.id, size: { width: sheet.paper.width, height: sheet.paper.height } } : null;
  }
  return inView && pdfUrl && !pdfHidden ? { page: inView.page, size: inView.size } : null;
}
