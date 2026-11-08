// What App derives from the open page's block tree each time the tree
// changes, which is every keystroke in a note. Pure functions, so the
// timing budgets (tests/perf/keystroke.perf.mjs) run the code App runs;
// the memos that call them, and the refs that keep a result by identity
// (shared/lib/keepIfSame.js), stay in App.
import { isRecordingBlock } from "../recording/recording.js";
import { blocksToHighlights, flattenBlocks, isLinkBlock } from "../shared/model/blockModel.js";

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

// The viewer's highlights (blockModel.js blocksToHighlights) with what App
// adds to them: a highlight's count of open comment threads, from
// `countByBlock` (block id → count; the note badge shows for them too), and
// a link block's target (App's highlights). `treeBlocks` is the tree
// flattened.
export function pageHighlights(blocks, treeBlocks, countByBlock) {
  const byId = blocksById(treeBlocks);
  return blocksToHighlights(blocks).map((h) => {
    const b = byId.get(h.id), p = b?.properties || {};
    const threads = countByBlock.get(h.id) || 0;
    const out = threads ? { ...h, threadCount: threads } : h;
    return isLinkBlock(b)
      ? { ...out, linkTarget: { url: p.link_url || "", pageId: p.link_page_id || "", blockId: p.link_block_id || "" } } : out;
  });
}
