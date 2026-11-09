// A folder's annotated-PDF export with its papers translated
// (docs/dev/translation.md "A folder's export"). No viewer shows those
// papers, so each one this browser left showing its translation is read
// here: its PDF opened with no viewer, every page seeded from the kept
// translations (translateState.seedDetached, no model call) and laid out as
// the viewer's export lays it out (translateExport.exportLayout). The
// layouts go with the job as `translations`, one per paper by its page.
// Pure but for what is handed in, so the tests run it in Node.
import { folderSubtree } from "../library/libraryUtils.js";
import { exportLayout } from "./translateExport.js";
import { seedDetached } from "./translateState.js";
import { langOfKey } from "./translateRecall.js";

// The papers of a folder's export that may carry a translation: the pages
// filed in `folder` or below it (`tree`, libraryUtils.libraryTree), a page
// inside another filed where its top is, as the server walks them
// (blocks_store.pages_in_folder), each with the stored PDF it shows. `pages`
// are the library's rows (libraryUtils.pageBlocksOf). A page with no doc_id
// (a duplicated page, a link page whose copy was not kept) has no file the
// job writes a translation into (translateExport.exportFile).
export function folderPapers(pages, tree, folder) {
  const below = folderSubtree(tree, folder);
  if (!below.size) return [];
  return (pages || []).filter((p) => p._attachment?.id && (p._folders || []).some((f) => below.has(f)))
    .map((p) => ({ page_id: p.id, doc_id: p._attachment.id }));
}

// What the server takes of a job's translations, all papers together
// (routers/export.py _translation_bounds): a paper that would take them past
// it goes in the original, so the folder's export is never refused for it.
export const TRANSLATION_BOUNDS = Object.freeze({
  blocks: 100_000, items: 1_000_000, chars: 5_000_000, area: 20 * 612 * 792,
});

// What a layout ({pages: [{blocks}]}) counts against TRANSLATION_BOUNDS.
export function layoutUse(layout) {
  const use = { blocks: 0, lines: 0, parts: 0, chars: 0, area: 0 };
  for (const page of layout?.pages || []) {
    for (const block of page.blocks) {
      use.blocks += 1;
      use.lines += block.lines.length;
      use.parts += block.parts.length;
      for (const part of block.parts) {
        if (typeof part === "string") use.chars += part.length;
        else {
          use.chars += (part.text || "").length;
          use.area += Math.max(0, part.box[2] - part.box[0]) * Math.max(0, part.box[3] - part.box[1]);
        }
      }
    }
  }
  return use;
}

const plus = (a, b) => Object.fromEntries(Object.keys(a).map((k) => [k, a[k] + b[k]]));
const within = (u, bounds) => u.blocks <= bounds.blocks && Math.max(u.lines, u.parts) <= bounds.items
  && u.chars <= bounds.chars && u.area <= bounds.area;

// The translations a folder's export carries: for each of `papers` (in
// order, one at a time) what `layoutOf(paper, signal)` resolves to,
// {lang, pages} or null (the paper goes in the original), as {page_id,
// doc_id, lang, pages}. A paper whose layout fails, or that would take the
// whole past `bounds`, goes in the original. Stopped by `signal`: the papers
// read so far.
export async function folderTranslations(papers, { layoutOf, signal = null, bounds = TRANSLATION_BOUNDS }) {
  const out = [];
  let used = layoutUse(null);
  for (const paper of papers) {
    if (signal?.aborted) break;
    let layout = null;
    try { layout = await layoutOf(paper, signal); } catch {}
    if (signal?.aborted) break;
    if (!layout?.pages?.length) continue;
    const next = plus(used, layoutUse(layout));
    if (!within(next, bounds)) continue;
    used = next;
    out.push({ page_id: paper.page_id, doc_id: paper.doc_id, lang: layout.lang, pages: layout.pages });
  }
  return out;
}

// One paper's layout with no viewer, under `key` (the key it opens under,
// translateRecall.openKey): its PDF at `url` opened (`open(url)`, a pdf.js
// document; PdfViewer's openPdfDocument), every page seeded through
// `lookup({pageId, texts}, signal)` (POST /api/translations/lookup under the
// key's language and model) and laid out. {lang, pages}, or null when no
// page holds a translation under the key. The document is destroyed after.
export async function paperLayout({ url, pageId, key, open, lookup, signal = null }) {
  const doc = await open(url);
  try {
    const { view, span } = await seedDetached({ doc, key, signal,
      lookup: (texts, sig) => lookup({ pageId, texts }, sig) });
    const layout = exportLayout(view, key, span);
    return layout && { lang: langOfKey(key), ...layout };
  } finally {
    try { await doc.destroy?.(); } catch {}
  }
}
