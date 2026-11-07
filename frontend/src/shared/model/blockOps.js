// Block operations, the client half of gamma/ops.py: turn one block-tree
// transition into the ops that express it (diffTrees) and apply a batch of
// ops — ours echoed back, or another client's — to a tree (applyOps).
//
// Pure: no React, no network. Trees are the App's nested block arrays
// ({id, content, properties, children, …} — the document only; the open
// editor and the viewer's folding live beside the tree, blockModel's
// `view`). Positions (fractional-index keys,
// same library as the backend) live in a separate Map id → key that both
// functions read and write, so the tree objects themselves stay untouched
// and history snapshots can share them.
//
// diffTrees emits, top-down and left-to-right:
//   insert — an id the base tree doesn't have (content + full props);
//   move   — a known id under another parent, or out of order among its
//            siblings: the longest increasing run of existing keys keeps
//            them, everything else is re-keyed between its new neighbours
//            (the minimal set of moves);
//   set    — content and/or a properties PATCH (null deletes a key); a
//            changed text box or picture carries `base_props`, the
//            `text_box` or `picture` it was changed from, which the server
//            merges key by key into one changed meanwhile (PLACED);
//   delete — the top-most removed subtrees, last (so a block that escaped
//            a deleted parent is moved out before the parent goes).
// withMerges then folds a block joined to another into one op:
//   merge  — block `id` joined to the end of `into`, both as the server holds
//            them when it arrives, children and all (gamma/ops.py merge);
//            `base` is the text `into` had here, for a rescue of it to send
//            (collabSession), since the tree here holds the join already.
// applyOps is idempotent: inserting a known id re-parents it, moving or
// deleting an unknown one is a no-op, and a parent the tree lacks leaves
// the block where it is (a new one stays out); siblings stay sorted by key.
import { generateKeyBetween } from "fractional-indexing";
import { extractBlock, findBlock, sameJson, updateBlockTree } from "./blockModel.js";
import { withoutFirstMarker } from "../../editor/mdLines.js";
import { mergeTextBox, normalizeTextBox } from "../../markup/textBox.js";
import { mergePicture, normalizePicture } from "../../markup/picture.js";

// The properties that place an object on a page, each with its module's
// normalizer and merge: a text box's `text_box` (markup/textBox.js) and a
// picture's `picture` (markup/picture.js). Every change sends one whole, so
// a set of one names the value it was changed from (`base_props`) and the
// server merges it key by key into the value it holds (gamma/ops.py
// PLACED_KEYS); the live session and the undo history merge alike.
export const PLACED = {
  text_box: { normalize: normalizeTextBox, merge: mergeTextBox },
  picture: { normalize: normalizePicture, merge: mergePicture },
};

// The set.props patch turning `oldP` into `newP`: a value equal to the one
// it replaces, whatever its keys' order, is no change.
export function propsPatch(oldP, newP) {
  const o = oldP || {}, n = newP || {};
  const patch = {};
  for (const k of Object.keys(n)) if (!(k in o) || !sameJson(o[k], n[k])) patch[k] = n[k];
  for (const k of Object.keys(o)) if (!(k in n)) patch[k] = null;
  return patch;
}

export function applyPatch(props, patch) {
  const out = { ...(props || {}) };
  for (const [k, v] of Object.entries(patch || {})) {
    if (v === null || v === undefined) delete out[k];
    else out[k] = v;
  }
  return out;
}

// Whether `test` holds for `node` or any block under it.
export const someNode = (node, test) => test(node) || (node.children || []).some((c) => someNode(c, test));

// `tree` with the text of the blocks in `texts` (id → text) replaced.
export function withTexts(tree, texts) {
  return (tree || []).map((n) => {
    const kids = n.children?.length ? withTexts(n.children, texts) : n.children;
    if (texts.has(n.id)) return { ...n, content: texts.get(n.id), children: kids };
    return kids === n.children ? n : { ...n, children: kids };
  });
}

// id → {node, parent} for every block; the page id is the top level's parent.
export function indexTree(tree, pageId) {
  const map = new Map();
  const walk = (list, parent) => {
    for (const n of list || []) {
      map.set(n.id, { node: n, parent });
      walk(n.children, n.id);
    }
  };
  walk(tree, pageId);
  return map;
}

// Seed / refresh the position map from a tree the server sent (its nodes
// carry `position`).
export function seedPositions(tree, pos) {
  const walk = (list) => {
    for (const n of list || []) {
      if (typeof n.position === "string" && n.position) pos.set(n.id, n.position);
      walk(n.children);
    }
  };
  walk(tree);
  return pos;
}

// Indexes (into `keys`) of one longest strictly increasing subsequence.
function lis(keys) {
  const tails = [], tailIdx = [], prev = new Array(keys.length).fill(-1);
  for (let i = 0; i < keys.length; i++) {
    const k = keys[i];
    let lo = 0, hi = tails.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (tails[mid] < k) lo = mid + 1; else hi = mid; }
    tails[lo] = k;
    tailIdx[lo] = i;
    prev[i] = lo > 0 ? tailIdx[lo - 1] : -1;
  }
  const out = [];
  for (let i = tailIdx[tails.length - 1]; i != null && i >= 0; i = prev[i]) out.push(i);
  return out.reverse();
}

export function diffTrees(base, next, pageId, pos) {
  const bi = indexTree(base, pageId);
  const ni = indexTree(next, pageId);
  const ops = [];
  const walk = (list, parent) => {
    const rows = (list || []).map((n) => {
      const b = bi.get(n.id);
      const kept = !!b && b.parent === parent;
      const key = pos.get(n.id) ?? (typeof n.position === "string" ? n.position : null);
      return { n, b, kept, key: kept ? key : null };
    });
    const cand = [];
    rows.forEach((r, i) => { if (r.kept && r.key) cand.push(i); });
    const anchored = new Set(lis(cand.map((i) => rows[i].key)).map((j) => cand[j]));
    let prev = null;
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      if (anchored.has(i)) {
        prev = r.key;
      } else {
        let nextKey = null;
        for (let j = i + 1; j < rows.length; j++) if (anchored.has(j)) { nextKey = rows[j].key; break; }
        const key = generateKeyBetween(prev, nextKey);
        pos.set(r.n.id, key);
        prev = key;
        if (!r.b) {
          ops.push({ op: "insert", id: r.n.id, parent, position: key,
            content: r.n.content || "", props: r.n.properties || {} });
        } else {
          ops.push({ op: "move", id: r.n.id, parent, position: key });
        }
      }
      if (r.b) {
        const set = { op: "set", id: r.n.id };
        let changed = false;
        if ((r.b.node.content || "") !== (r.n.content || "")) {
          // `base`: the text this change was made from — the server applies
          // the change as a patch if the block moved on meanwhile.
          set.content = r.n.content || "";
          set.base = r.b.node.content || "";
          changed = true;
        }
        const patch = propsPatch(r.b.node.properties, r.n.properties);
        if (Object.keys(patch).length) { set.props = patch; changed = true; }
        for (const key of Object.keys(PLACED)) {
          const was = r.b.node.properties?.[key];
          if (patch[key] && was && typeof was === "object" && !Array.isArray(was)) set.base_props = { ...set.base_props, [key]: was };
        }
        if (changed) ops.push(set);
      }
      walk(r.n.children, r.n.id);
    }
  };
  walk(next, pageId);
  for (const [id, b] of bi) {
    if (ni.has(id)) continue;
    // Top-most only: a parent that is itself gone takes its subtree along.
    if (b.parent === pageId || ni.has(b.parent)) ops.push({ op: "delete", id });
  }
  return ops;
}

// `ops` (a transition's diff from `base`) with each block joined to another
// as one merge: a block removed, its children moved out of it, while another
// block took its text, less a list or heading marker, at its end.
// That is what Backspace at a block's start comes to, and the redo of it and
// the undo of a split; read off the transition, as the undo history reads
// what it records, since a flag set beside the tree outlives a transition
// React skips. As a set and a delete it would lose text to a race: the set
// refused and the delete sent alone, or the delete taking a block someone
// else's join just filled. The merge's moves stay with it, so a refused one
// leaves everything where it was.
export function withMerges(ops, base, pageId) {
  const was = indexTree(base, pageId);
  const moves = new Map(ops.filter((op) => op.op === "move").map((op) => [op.id, op]));
  const merges = new Map(), taken = new Set();
  for (const del of ops) {
    if (del.op !== "delete") continue;
    const node = was.get(del.id).node, text = node.content || "";
    const tail = text && withoutFirstMarker(text);
    const kids = node.children || [];
    const set = tail && kids.every((c) => moves.has(c.id))
      && ops.find((op) => op.op === "set" && op.content === (op.base ?? "") + tail && !taken.has(op));
    if (!set) continue;
    taken.add(set);
    for (const c of kids) if (moves.get(c.id).parent === set.id) taken.add(moves.get(c.id));
    const marker = text.slice(0, text.length - tail.length);
    merges.set(del, { op: "merge", id: del.id, into: set.id, base: set.base ?? "", ...(marker ? { marker } : {}) });
  }
  if (!merges.size) return ops;
  return ops.flatMap((op) => {
    if (merges.has(op)) return [merges.get(op)];
    if (!taken.has(op)) return [op];
    if (op.op !== "set" || !op.props) return [];
    const { content, base: from, ...rest } = op;
    return [rest];
  });
}

// --- applying -----------------------------------------------------------------
// The tree edits copy only the path to the block they change
// (blockModel's updateBlockTree and extractBlock), so an untouched subtree
// keeps its identity.

// Siblings are kept in key order: the whole list is re-sorted (stable, so
// blocks without a key keep their place), which also heals a list whose
// order drifted from the keys.
function insertSorted(siblings, node, pos) {
  const list = [...(siblings || []), node];
  const key = (n) => pos.get(n.id) || "";
  return list
    .map((n, i) => [n, i])
    .sort((a, b) => {
      const ka = key(a[0]), kb = key(b[0]);
      if (ka && kb && ka !== kb) return ka < kb ? -1 : 1;
      return a[1] - b[1];
    })
    .map((x) => x[0]);
}

function placeUnder(tree, parent, node, pageId, pos) {
  if (parent === pageId) return insertSorted(tree, node, pos);
  return updateBlockTree(tree, parent, (p) => ({ ...p, children: insertSorted(p.children, node, pos) }));
}

// A parent this tree lacks (an undo snapshot taken before the parent was
// made, a subtree we don't hold): a block already here stays where it is —
// it must never vanish with the op — and a new one stays out.
const placeable = (tree, parent, pageId) => parent === pageId || !!findBlock(tree || [], parent);

// What `merge` comes to on `tree`, as the server applies it (gamma/ops.py
// merge): none when it would be refused there.
function mergedOps(tree, op, pageId, pos) {
  const index = indexTree(tree, pageId);
  const block = index.get(op.id), into = index.get(op.into);
  if (!block || !into || someNode(block.node, (n) => n.id === op.into)) return [];
  const key = (n) => pos.get(n.id) ?? n.position ?? null;
  const kin = into.node.children || [];
  const at = block.parent === op.into ? kin.findIndex((n) => n.id === op.id) : kin.length - 1;
  let low = at >= 0 ? key(kin[at]) : null;
  const next = block.parent === op.into && kin[at + 1] ? key(kin[at + 1]) : null;
  const high = next !== null && (low === null || next > low) ? next : null;
  const text = block.node.content || "", marker = op.marker || "";
  const ops = (block.node.children || []).map((c) => {
    low = generateKeyBetween(low, high);
    return { op: "move", id: c.id, parent: op.into, position: low };
  });
  const tail = text.startsWith(marker) ? text.slice(marker.length) : text;
  return [...ops, { op: "set", id: op.into, content: (into.node.content || "") + tail }, { op: "delete", id: op.id }];
}

export function applyOps(tree, ops, pageId, pos) {
  let out = tree;
  for (const op of ops || []) {
    if (op.op === "set") {
      if (op.id === pageId) continue; // the page block itself: the caller's
      out = updateBlockTree(out, op.id, (b) => ({
        ...b,
        ...(op.content !== undefined ? { content: op.content } : {}),
        ...(op.props ? { properties: applyPatch(b.properties, op.props) } : {}),
      }));
    } else if (op.op === "insert") {
      const ex = extractBlock(out, op.id);
      if (!placeable(ex ? ex.remaining : out, op.parent, pageId)) {
        if (ex) out = updateBlockTree(out, op.id, (b) => ({ ...b, content: op.content ?? "", properties: op.props || {} }));
        continue;
      }
      pos.set(op.id, op.position);
      const node = ex
        ? { ...ex.extracted, content: op.content ?? "", properties: op.props || {}, position: op.position }
        : { id: op.id, content: op.content ?? "", properties: op.props || {}, position: op.position,
            children: [] };
      out = placeUnder(ex ? ex.remaining : out, op.parent, node, pageId, pos);
    } else if (op.op === "move") {
      const ex = extractBlock(out, op.id);
      if (!ex || !placeable(ex.remaining, op.parent, pageId)) continue;
      pos.set(op.id, op.position);
      out = placeUnder(ex.remaining, op.parent, { ...ex.extracted, position: op.position }, pageId, pos);
    } else if (op.op === "delete") {
      const ex = extractBlock(out, op.id);
      if (ex) out = ex.remaining;
    } else if (op.op === "merge") {
      out = applyOps(out, mergedOps(out, op, pageId, pos), pageId, pos);
    }
  }
  return out;
}

// Whether another client's `ops` move the row of block `id` in the DOM:
// the block or an ancestor is moved (re-parented rows remount, so an open
// editor in it unmounts), or a sibling list holding one of them is
// reordered (React may move our row's node). Either blurs the editor open
// on it, which the caller must not take for the person leaving it.
export function displacedRow(tree, ops, id, pageId) {
  if (!id || !(ops || []).some((op) => op.op === "move" || op.op === "insert")) return false;
  const index = indexTree(tree, pageId);
  const lineage = new Set(), lists = new Set();
  for (let at = index.get(id); at; at = index.get(at.parent)) {
    lineage.add(at.node.id);
    lists.add(at.parent);
  }
  if (!lineage.size) return false;
  return ops.some((op) => (op.op === "move" || op.op === "insert") && index.has(op.id)
    && (lineage.has(op.id) || (op.parent === index.get(op.id).parent && lists.has(op.parent))));
}

// Coalesce a queue of ops: a `set` for a block folds into the last `set`
// for the same block when nothing structural about it sits in between (a
// merge into the block counts: the set's text holds what that merge will
// join), never into the first `floor` ops (ones already sent under a batch's
// name, collabSession's hide). Returns the queued op it was folded into
// (as it was before), or null when it was appended.
export function pushOp(queue, op, floor = 0) {
  if (op.op === "set") {
    for (let i = queue.length - 1; i >= floor; i--) {
      const q = queue[i];
      if (q.op === "merge" && q.into === op.id) break;
      if (q.id !== op.id) continue;
      if (q.op !== "set") break;
      const merged = { ...q };
      if (op.content !== undefined) {
        merged.content = op.content;
        // The queued op keeps its own base (the earliest text this run of
        // keystrokes started from); only a props-only op adopts the new one.
        if (q.content === undefined) merged.base = op.base;
      }
      if (op.props) {
        merged.props = { ...(q.props || {}), ...op.props };
        // A key keeps the base of its first change in the run, as the text
        // does (none, when that change named none).
        const bases = { ...(q.base_props || {}) };
        for (const [k, v] of Object.entries(op.base_props || {})) if (!(k in (q.props || {}))) bases[k] = v;
        if (Object.keys(bases).length) merged.base_props = bases;
      }
      queue[i] = merged;
      return q;
    }
  }
  queue.push(op);
  return null;
}
