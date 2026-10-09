// Logseq-style block model: each block has id, content, properties, children.
// Highlights (link regions too) are blocks with a properties.pdf_position
// that are no ink group, text box or sheet; the block id is the highlight's
// id. Free notes are
// the blocks without one. A pdf_position (gamma/highlights.py) is
// {pageNumber, width, height, boundingRect: {x1, y1, x2, y2}, rects: [{x1,
// y1, x2, y2}, …]} (area: true on an area highlight): the 1-based page,
// the page as measured when the place was taken, and the rectangles in
// that frame, top-left origin. A highlight whose place on its page is not
// known carries pageNumber alone.
//
// The tree is the DOCUMENT and nothing else: every change to it is an edit
// the live session sends and the undo history records. What only this
// viewer sees — the block whose editor is open, the folding they changed —
// is a `view` kept beside the tree ({editingId, folds}, EMPTY_VIEW below),
// never a flag on a node, so revealing a block or opening an editor can't
// be mistaken for an edit, and an edit can't be mistaken for a load.
import { COLORS } from "./highlightColors.js";

const DEFAULT_COLOR = COLORS[0];

// 9 random bytes, base64url — same shape and strength as the backend's
// secrets.token_urlsafe(9) (12 chars, 72 bits; the old Math.random base36
// slice was 8 chars from a non-crypto RNG).
export function makeBlockId() {
  const bytes = new Uint8Array(9);
  if (globalThis.crypto?.getRandomValues) crypto.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-").replace(/\//g, "_");
}

// --- shape helpers ---

const isObject = (v) => Boolean(v) && typeof v === "object";

// A highlight or a link region: a block with a pdf_position that is no ink
// group, text box or sheet (the server's `kind` highlight, and link on the
// page; gamma/highlights.py is_highlight).
export function isHighlightBlock(b) {
  const p = b?.properties;
  return Boolean(p && isObject(p.pdf_position) && p.ink_url === undefined && !isObject(p.text_box) && !isObject(p.sheet));
}

export function blockColor(b) {
  return (b?.properties?.color) || DEFAULT_COLOR;
}

// The PDF page a block is placed on: its position's (a highlight, a link
// region, an ink group), a text box's pdf_page (a box stores no position,
// markup/textBox.js); null for any other block.
export function blockPage(b) {
  const p = b?.properties || {};
  if (isObject(p.text_box)) return p.pdf_page ?? null;
  return p.pdf_position?.pageNumber ?? null;
}

export function blockQuote(b) {
  return b?.properties?.quote || "";
}

export function blockCollapsed(b) {
  return b?.properties?.collapsed ?? false;
}

// The block's place on its PDF page, when it has one: a position with its
// rectangles (not one of its page alone).
export function blockPosition(b) {
  const pos = b?.properties?.pdf_position;
  return pos?.boundingRect ? pos : null;
}

// Backwards-compat accessor view: let old code that reads b.color, b.quote, b.page, b.position
// keep working without changes. Attaches convenience getters.
export function withLegacyAccessors(b) {
  return {
    ...b,
    color: blockColor(b),
    quote: blockQuote(b),
    page: blockPage(b),
    position: blockPosition(b),
  };
}

// --- tree operations (same API as before) ---

export function findBlock(blocks, id) {
  for (const b of blocks) {
    if (b.id === id) return b;
    const x = findBlock(b.children || [], id);
    if (x) return x;
  }
  return null;
}

// The tree with block `id` replaced by `fn(block)`: only the path from the
// root to that block is copied, every other subtree keeps its identity
// (a memoized row on it does not re-render), and the same array comes back
// when the id is not in the tree. `fn` gets the stored block itself: it
// returns a new object and must not change the one it was given.
export function updateBlockTree(blocks, id, fn) {
  const list = blocks || [];
  for (let i = 0; i < list.length; i++) {
    const b = list[i];
    let next;
    if (b.id === id) {
      next = fn(b);
    } else {
      const kids = b.children || [];
      const updated = updateBlockTree(kids, id, fn);
      if (updated === kids) continue;
      next = { ...b, children: updated };
    }
    const out = list.slice();
    out[i] = next;
    return out;
  }
  return list;
}

export function removeBlockTree(blocks, id) {
  const out = [];
  for (const b of blocks || []) {
    if (b.id === id) continue;
    out.push({
      ...b,
      children: removeBlockTree(b.children || [], id)
    });
  }
  return out;
}

export function appendChild(blocks, id, newBlock) {
  return (blocks || []).map((b) => {
    if (b.id === id) {
      return {
        ...b,
        children: [...(b.children || []), newBlock],
      };
    }
    return {
      ...b,
      children: appendChild(b.children || [], id, newBlock)
    };
  });
}

// Every block of the tree in document order, each with its depth and
// parentId (folding does not hide a block from a lookup — for what is on
// screen, see visibleBlocks).
export function flattenBlocks(blocks) {
  return walkBlocks(blocks, null);
}

function walkBlocks(blocks, view, depth = 0, parentId = null) {
  const out = [];
  for (const b of blocks || []) {
    out.push(withLegacyAccessors({ ...b, depth, parentId }));
    if (!view || !isFolded(b, view)) {
      out.push(...walkBlocks(b.children || [], view, depth + 1, b.id));
    }
  }
  return out;
}

// --- the viewer's own state, beside the tree ---
// `editingId`: the one block whose editor is open. `folds`: the folding of
// the blocks this viewer touched (id → true folded / false open); a block
// nobody touched here shows its stored `properties.collapsed`, the default
// every viewer opens the page with.
export const EMPTY_VIEW = Object.freeze({ editingId: null, folds: Object.freeze({}) });

export function isFolded(block, view) {
  const own = view?.folds?.[block.id];
  return own === undefined ? blockCollapsed(block) : own;
}

export function withEditing(view, id) {
  return (view || EMPTY_VIEW).editingId === id ? view || EMPTY_VIEW : { ...(view || EMPTY_VIEW), editingId: id };
}

// Close `id`'s editor — a blur from an editor that is no longer the open
// one (another just opened) must not close that one.
export function closeEditing(view, id) {
  return view?.editingId === id ? { ...view, editingId: null } : view || EMPTY_VIEW;
}

// The blocks shown in the outliner, folded subtrees skipped.
export function visibleBlocks(blocks, view) {
  return walkBlocks(blocks, view || EMPTY_VIEW);
}

export function getParentInfo(blocks, id, parent = null) {
  for (let i = 0; i < (blocks || []).length; i++) {
    const b = blocks[i];
    if (b.id === id) {
      return { parent, index: i, block: b, siblings: blocks };
    }
    const x = getParentInfo(b.children || [], id, b);
    if (x) return x;
  }
  return null;
}

export function indentBlock(blocks, id) {
  const info = getParentInfo(blocks, id);
  if (!info) return blocks;
  const { siblings, index, block } = info;
  if (index <= 0) return blocks;

  const prev = siblings[index - 1];
  const without = removeBlockTree(blocks, id);

  return appendChild(without, prev.id, block);
}

export function outdentBlock(blocks, id) {
  const info = getParentInfo(blocks, id);
  if (!info || !info.parent) return blocks;

  const parentInfo = getParentInfo(blocks, info.parent.id);
  const without = removeBlockTree(blocks, id);

  if (!parentInfo) {
    return [...without, info.block];
  }

  if (!parentInfo.parent) {
    // Parent is at root. Insert the outdented block as a root sibling after parent.
    const out = [];
    for (const b of without) {
      out.push(b);
      if (b.id === info.parent.id) out.push(info.block);
    }
    return out;
  }

  // Parent is nested. Insert outdented block as sibling of parent inside grandparent.
  return updateBlockTree(without, parentInfo.parent.id, (gp) => {
    const children = [];
    for (const child of gp.children || []) {
      children.push(child);
      if (child.id === info.parent.id) children.push(info.block);
    }
    return { ...gp, children };
  });
}

export function setBlockText(blocks, id, text) {
  return updateBlockTree(blocks, id, (b) => ({ ...b, content: text }));
}

// Fold or unfold `id`'s children: the viewer's own folding, and the stored
// `properties.collapsed` with it (a document edit — the default the page
// opens with next time, for everyone).
export function toggleFold(blocks, view, id) {
  const block = findBlock(blocks, id);
  if (!block) return { blocks, view };
  const folded = !isFolded(block, view);
  return {
    blocks: updateBlockTree(blocks, id, (b) => ({ ...b, properties: { ...b.properties, collapsed: folded } })),
    view: { ...view, folds: { ...view.folds, [id]: folded } },
  };
}

// The ancestors of `targetId` (the block itself excluded), top-most first;
// [] for a root block, null for an unknown id.
function ancestorsOf(blocks, targetId) {
  const walk = (list, chain) => {
    for (const b of list || []) {
      if (b.id === targetId) return chain;
      const found = walk(b.children, [...chain, b]);
      if (found) return found;
    }
    return null;
  };
  return walk(blocks, []);
}

// Unfold whatever hides `targetId` — a view change only, nothing the
// document notices. The same view comes back when nothing hid it.
export function revealBlock(blocks, view, targetId) {
  const hiding = (ancestorsOf(blocks, targetId) || []).filter((b) => isFolded(b, view));
  if (!hiding.length) return view;
  const folds = { ...view.folds };
  for (const b of hiding) folds[b.id] = false;
  return { ...view, folds };
}

// True when a folded ancestor hides `targetId` — what revealBlock opens.
export function isFoldedAway(blocks, targetId, view) {
  return (ancestorsOf(blocks, targetId) || []).some((b) => isFolded(b, view));
}

function makeNewBlock({ parentId = null, properties = {} } = {}) {
  return {
    id: makeBlockId(),
    parentId,
    children: [],
    content: "",
    properties: { ...properties },
  };
}

// New empty sibling next to `id` — below by default (Enter), above with
// `above` (the row's "+" handle with Alt held, Notion-style).
export function addSiblingBlock(blocks, id, { above = false } = {}) {
  const info = getParentInfo(blocks, id);
  const newBlock = makeNewBlock({ parentId: info?.parent?.id || null });
  return {
    blocks: insertSibling(blocks, id, newBlock, !above),
    newId: newBlock.id
  };
}

// Append a fresh empty block at the end of the top level (clicking the
// space under the last block — works even when the page has no blocks yet).
export function addRootBlock(blocks) {
  const newBlock = makeNewBlock();
  return {
    blocks: [...(blocks || []), newBlock],
    newId: newBlock.id
  };
}

export function addChildBlock(blocks, id) {
  const newBlock = makeNewBlock({ parentId: id });
  return {
    blocks: appendChild(blocks, id, newBlock),
    newId: newBlock.id
  };
}

// --- highlight integration: instead of rebuilding the tree from highlights,
//     just append a new highlight-backed block without disturbing existing tree ---

export function addHighlightAsBlock(blocks, highlight) {
  const block = {
    id: highlight.id || makeBlockId(),
    parentId: null,
    children: [],
    content: highlight.comment?.text || "",
    properties: {
      color: highlight.color || DEFAULT_COLOR,
      quote: highlight.content?.text || "",
      pdf_position: highlight.position,
    },
  };
  return [...(blocks || []), block];
}

// --- derive highlights (for react-pdf-highlighter) from blocks ---

export function blocksToHighlights(blocks) {
  const out = [];
  // A highlight "has a note" when the user typed anything on it: the block's
  // own content (the comment) or any non-empty block nested under it.
  function hasChildNote(b) {
    return (b.children || []).some((c) => (c.content || "").trim() !== "" || hasChildNote(c));
  }
  function walk(list) {
    for (const b of list || []) {
      if (isHighlightBlock(b) && blockPosition(b)) {
        out.push({
          id: b.id,
          content: { text: blockQuote(b) },
          comment: { text: b.content || "" },
          hasNote: (b.content || "").trim() !== "" || hasChildNote(b),
          color: blockColor(b),
          position: blockPosition(b),
        });
      }
      if (b.children?.length) walk(b.children);
    }
  }
  walk(blocks);
  return out;
}

// --- normalize blocks from server: every node gets a children array ---

export function normalizeBlocks(blocks) {
  return (blocks || []).map((b) => ({
    ...b,
    children: normalizeBlocks(b.children || []),
  }));
}

// --- markdown export (kept for syncPdfPage → pages.content) ---

export function blocksToPageMarkdown(title, sourceUrl, docId, blocks) {
  const lines = [
    `# ${title}`,
    "",
    `Source: ${sourceUrl || ""}`,
    `Doc ID: ${docId || ""}`,
    "",
  ];
  lines.push(...blocksToMarkdownLines(blocks, 0));
  return lines.join("\n");
}

function blocksToMarkdownLines(blocks, depth) {
  const lines = [];
  for (const b of blocks || []) {
    const indent = "  ".repeat(depth);
    const page = blockPage(b);
    const label = page ? `page ${page}` : "note";
    const first = (b.content || "").trim() || `(${label})`;
    lines.push(`${indent}- ${first}`);
    const quote = blockQuote(b);
    if (quote.trim()) {
      for (const line of quote.split("\n")) {
        lines.push(`${indent}  > ${line}`);
      }
    }
    if (b.children?.length) {
      lines.push(...blocksToMarkdownLines(b.children, depth + 1));
    }
  }
  return lines;
}


// --- Phase B3 tree helpers ---

// True if `descendantId` is inside the subtree rooted at `ancestorId`
export function isDescendant(blocks, ancestorId, descendantId) {
  if (ancestorId === descendantId) return true;
  for (const b of blocks || []) {
    if (b.id === ancestorId) {
      return containsId(b.children || [], descendantId);
    }
    const hit = isDescendant(b.children || [], ancestorId, descendantId);
    if (hit) return true;
  }
  return false;
}

function containsId(blocks, id) {
  for (const b of blocks || []) {
    if (b.id === id) return true;
    if (containsId(b.children || [], id)) return true;
  }
  return false;
}

// Find a block by id along with its parent chain and position
// Returns { block, parentId, index, depth, ancestors: [id, ...] } or null
export function findBlockContext(blocks, id, depth = 0, ancestors = []) {
  const list = blocks || [];
  for (let i = 0; i < list.length; i++) {
    const b = list[i];
    if (b.id === id) {
      return {
        block: b,
        parentId: ancestors[ancestors.length - 1] ?? null,
        index: i,
        depth,
        ancestors,
      };
    }
    const found = findBlockContext(b.children || [], id, depth + 1, [...ancestors, b.id]);
    if (found) return found;
  }
  return null;
}

// Extract a block (with subtree) from the tree, returning both the extracted block and the remaining tree.
export function extractBlock(blocks, id) {
  const list = blocks || [];
  for (let i = 0; i < list.length; i++) {
    const b = list[i];
    if (b.id === id) {
      const extracted = b;
      const remaining = [...list.slice(0, i), ...list.slice(i + 1)];
      return { extracted, remaining };
    }
    const sub = extractBlock(b.children || [], id);
    if (sub) {
      const newChildren = sub.remaining;
      const newBlock = { ...b, children: newChildren };
      const newList = [...list.slice(0, i), newBlock, ...list.slice(i + 1)];
      return { extracted: sub.extracted, remaining: newList };
    }
  }
  return null;
}

// Insert `newBlock` as a sibling of `siblingId` (after=true means after, else before)
export function insertSibling(blocks, siblingId, newBlock, after) {
  const list = blocks || [];
  for (let i = 0; i < list.length; i++) {
    const b = list[i];
    if (b.id === siblingId) {
      const insertAt = after ? i + 1 : i;
      return [...list.slice(0, insertAt), newBlock, ...list.slice(insertAt)];
    }
    const childResult = insertSibling(b.children || [], siblingId, newBlock, after);
    if (childResult !== b.children) {
      return [...list.slice(0, i), { ...b, children: childResult }, ...list.slice(i + 1)];
    }
  }
  return list;
}

// Insert `newBlock` as a child of `parentId` (atEnd=true for last child, else first)
export function insertChild(blocks, parentId, newBlock, atEnd = false) {
  const list = blocks || [];
  for (let i = 0; i < list.length; i++) {
    const b = list[i];
    if (b.id === parentId) {
      const existing = b.children || [];
      const newChildren = atEnd ? [...existing, newBlock] : [newBlock, ...existing];
      return [...list.slice(0, i), { ...b, children: newChildren }, ...list.slice(i + 1)];
    }
    const childResult = insertChild(b.children || [], parentId, newBlock, atEnd);
    if (childResult !== b.children) {
      return [...list.slice(0, i), { ...b, children: childResult }, ...list.slice(i + 1)];
    }
  }
  return list;
}

// --- Keyboard block commands (editor/blockCommands.js) ---------------------

// Move a block one step among its siblings (dir -1 up, +1 down); the
// tree comes back unchanged (same reference) at the edge of its parent.
export function moveSibling(blocks, id, dir) {
  const list = blocks || [];
  const i = list.findIndex((b) => b.id === id);
  if (i >= 0) {
    const j = i + dir;
    if (j < 0 || j >= list.length) return list;
    const next = [...list];
    [next[i], next[j]] = [next[j], next[i]];
    return next;
  }
  for (let k = 0; k < list.length; k++) {
    const b = list[k];
    if (!b.children?.length) continue;
    const children = moveSibling(b.children, id, dir);
    if (children !== b.children) {
      return [...list.slice(0, k), { ...b, children }, ...list.slice(k + 1)];
    }
  }
  return list;
}

// Remove a block, its children taking its place among the siblings (what
// deleting a line does to the indented lines under it: nothing).
export function removeBlockKeepChildren(blocks, id) {
  const list = blocks || [];
  const i = list.findIndex((b) => b.id === id);
  if (i >= 0) return [...list.slice(0, i), ...(list[i].children || []), ...list.slice(i + 1)];
  return list.map((b) => {
    if (!b.children?.length) return b;
    const children = removeBlockKeepChildren(b.children, id);
    return children === b.children ? b : { ...b, children };
  });
}

// The block shown right above (dir -1) or below (+1) `id` in the outliner,
// folded subtrees skipped, or null at either end.
export function visibleNeighbor(blocks, id, dir, view) {
  const flat = visibleBlocks(blocks, view);
  const i = flat.findIndex((b) => b.id === id);
  if (i < 0) return null;
  return flat[i + dir] || null;
}
