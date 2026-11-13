// What App derives from the open page's block tree each time the tree
// changes, which is every keystroke in a note. Pure functions, so the
// timing budgets (tests/perf/keystroke.perf.mjs) run the code App runs;
// the memos that call them, and the refs that keep a result by identity
// (shared/lib/keepIfSame.js), stay in App.
import { isRecordingBlock } from "../recording/recording.js";
import { anchorOf, blocksToHighlights, flattenBlocks, isInkGroup, isLinkBlock, textSpan } from "../shared/model/blockModel.js";

// Block id → block, over a flattened tree (App's treeById).
export const blocksById = (list) => new Map(list.map((b) => [b.id, b]));

// Sheet id → its number on the page, from 1 (App's sheetNumbers, over
// notebook.js sheetsOf).
export const sheetNumbersOf = (sheets) => new Map(sheets.map((s) => [s.id, s.index + 1]));

// The page's recording blocks as the player reads them, {id, properties},
// in tree order (App's recordingBlocks).
export const recordingList = (blocks) =>
  flattenBlocks(blocks).filter(isRecordingBlock).map((b) => ({ id: b.id, properties: b.properties }));

// Every block stamped with the moment it was written, {id, t0}, in tree
// order (App's stampedBlocks).
export const stampedList = (blocks) =>
  flattenBlocks(blocks).filter((b) => Number.isFinite(b.properties?.t0)).map((b) => ({ id: b.id, t0: b.properties.t0 }));

// The viewer's highlights (blockModel.js blocksToHighlights: the PDF's
// under pdf_position, a picture's regions under image_position, a text
// file's ranges under text_position, `key` the
// page's document's positionKeyOf and `hash` its file) with what App adds
// to them: a highlight's count of open comment threads, from
// `countByBlock` (block id → count; the note badge shows for them too), and
// a link block's target (App's highlights). `treeBlocks` is the tree
// flattened.
export function pageHighlights(blocks, treeBlocks, countByBlock, key = "pdf_position", hash = "") {
  const byId = blocksById(treeBlocks);
  return blocksToHighlights(blocks, key, hash).map((h) => {
    const b = byId.get(h.id), p = b?.properties || {};
    const threads = countByBlock.get(h.id) || 0;
    const out = threads ? { ...h, threadCount: threads } : h;
    return isLinkBlock(b)
      ? { ...out, linkTarget: { url: p.link_url || "", pageId: p.link_page_id || "", blockId: p.link_block_id || "" } } : out;
  });
}

// What a comment card stands beside on the page's document (the viewer's
// `anchors`, docs/dev/comments.md "The margin"): every block anchorOf
// places under `key` by its rectangles (a position with a bounding box, as
// the viewer draws a highlight or a region) or by its lines (a text range
// with a span, textSpan), in document order, {id, kind: "highlight" |
// "ink", position, threadCount}; a region's and a text range's kind is
// "highlight", the position the stored one. `threadCount` is its open
// threads from `countByBlock`, 0 for none: a new comment's card stands on
// its anchor before any thread.
export function commentAnchors(blocks, countByBlock, key = "pdf_position") {
  const out = [];
  const walk = (list) => {
    for (const b of list || []) {
      const at = anchorOf(b, key);
      if (at?.position.boundingRect || textSpan(at?.position)) {
        out.push({ id: b.id, kind: isInkGroup(b) ? "ink" : "highlight", position: at.position,
          threadCount: countByBlock.get(b.id) || 0 });
      }
      if (b.children?.length) walk(b.children);
    }
  };
  walk(blocks);
  return out;
}
