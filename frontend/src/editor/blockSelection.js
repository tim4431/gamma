// A selection of whole notes, as Notion has it (useBlockSelection.js makes
// it; docs/dev/ui-design.md "Selecting notes"): the blocks picked, the one
// a Shift range starts from (`anchor`) and the end the arrows move (`head`).
// It is the viewer's own, like the open editor (blockModel's `view`): never
// in the tree, never sent to the room. A selected block takes the blocks
// under it along — they show selected and go wherever it goes — so the
// actions work on `selectionRoots`.
import { isPicture, pictureMarkdown } from "../markup/picture.js";
import { visibleBlocks } from "../shared/model/blockModel.js";

export const NO_SELECTION = Object.freeze({ ids: new Set(), anchor: null, head: null });

const shownIds = (blocks, view) => visibleBlocks(blocks, view).map((b) => b.id);

export function selectOne(id) {
  return { ids: new Set([id]), anchor: id, head: id };
}

// The notes `ids` in the order they are shown, from the first to the last.
export function selectIds(ids) {
  return ids.length ? { ids: new Set(ids), anchor: ids[0], head: ids[ids.length - 1] } : NO_SELECTION;
}

// The blocks shown from `anchor` to `head`, both included (folded children
// are not shown: they come with their parent); `head` alone when `anchor`
// is not shown.
export function selectRange(blocks, view, anchor, head) {
  const order = shownIds(blocks, view);
  const a = order.indexOf(anchor), h = order.indexOf(head);
  if (h < 0) return NO_SELECTION;
  if (a < 0) return selectOne(head);
  const ids = order.slice(Math.min(a, h), Math.max(a, h) + 1);
  return { ids: new Set(ids), anchor, head };
}

// `id` picked or let go (Alt+Shift+click, ⌘⇧ on a Mac); a range starts there next.
export function toggleSelected(sel, id) {
  const ids = new Set(sel.ids);
  if (ids.has(id)) ids.delete(id);
  else ids.add(id);
  return ids.size ? { ids, anchor: id, head: id } : NO_SELECTION;
}

// Every note shown (Ctrl+A).
export function selectAll(blocks, view) {
  return selectIds(shownIds(blocks, view));
}

// The arrows over a selection: ↑ / ↓ select the note shown above the top one
// or below the bottom one, and with Shift the selection grows or shrinks
// from its anchor. At either end it stays.
export function stepSelection(blocks, view, sel, dir, extend) {
  const order = shownIds(blocks, view);
  if (extend) {
    const next = order[order.indexOf(sel.head) + dir];
    return next && sel.ids.has(sel.head) ? selectRange(blocks, view, sel.anchor, next) : sel;
  }
  const at = order.flatMap((id, i) => (sel.ids.has(id) ? [i] : []));
  const next = at.length ? order[dir < 0 ? at[0] - 1 : at[at.length - 1] + 1] : null;
  return next ? selectOne(next) : sel;
}

// What the selection acts on, in document order: each selected block whose
// ancestors are not selected (the blocks under it go with it).
export function selectionRoots(blocks, ids) {
  const out = [];
  const walk = (list) => {
    for (const b of list || []) {
      if (ids.has(b.id)) out.push(b.id);
      else walk(b.children);
    }
  };
  if (ids.size) walk(blocks);
  return out;
}

// The selection without the blocks the tree no longer has (a delete,
// another client's, an undo); the same object when it has them all.
export function pruneSelection(blocks, sel) {
  if (!sel.ids.size) return sel;
  const ids = new Set();
  const walk = (list) => {
    for (const b of list || []) {
      if (sel.ids.has(b.id)) ids.add(b.id);
      walk(b.children);
    }
  };
  walk(blocks);
  if (ids.size === sel.ids.size) return sel;
  if (!ids.size) return NO_SELECTION;
  const first = [...ids][0];
  return { ids, anchor: ids.has(sel.anchor) ? sel.anchor : first, head: ids.has(sel.head) ? sel.head : first };
}

// A block's Markdown: its content, or for a picture its image line
// (markup/picture.js pictureMarkdown: the caption as the alt text).
export const blockMarkdown = (b) => (isPicture(b) ? pictureMarkdown(b.content, b.properties.picture) : b.content || "");

// Block subtree → a markdown outline: each block one "- " bullet (extra
// content lines hang under it), children indented two spaces deeper.
export function subtreeMarkdown(b, depth) {
  const indent = "  ".repeat(depth);
  const own = blockMarkdown(b).split("\n")
    .map((l, i) => (i === 0 ? `${indent}- ` : `${indent}  `) + l)
    .join("\n");
  return [own, ...(b.children || []).map((c) => subtreeMarkdown(c, depth + 1))].join("\n");
}
