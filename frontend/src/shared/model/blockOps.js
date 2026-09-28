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
//   set    — content and/or a properties PATCH (null deletes a key);
//   delete — the top-most removed subtrees, last (so a block that escaped
//            a deleted parent is moved out before the parent goes).
// applyOps is idempotent: inserting a known id re-parents it, moving or
// deleting an unknown one is a no-op, and a parent the tree lacks leaves
// the block where it is (a new one stays out); siblings stay sorted by key.
import { generateKeyBetween } from "fractional-indexing";
import { findBlock } from "./blockModel.js";

const same = (a, b) => a === b || JSON.stringify(a) === JSON.stringify(b);

// The set.props patch turning `oldP` into `newP`.
export function propsPatch(oldP, newP) {
  const o = oldP || {}, n = newP || {};
  const patch = {};
  for (const k of Object.keys(n)) if (!(k in o) || !same(o[k], n[k])) patch[k] = n[k];
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

// --- applying -----------------------------------------------------------------

function findAndUpdate(list, id, fn) {
  let hit = false;
  const out = (list || []).map((n) => {
    if (n.id === id) { hit = true; return fn(n); }
    if (!n.children?.length) return n;
    const kids = findAndUpdate(n.children, id, fn);
    if (kids === n.children) return n;
    hit = true;
    return { ...n, children: kids };
  });
  return hit ? out : list;
}

function extract(list, id) {
  for (let i = 0; i < (list || []).length; i++) {
    const n = list[i];
    if (n.id === id) return { node: n, rest: [...list.slice(0, i), ...list.slice(i + 1)] };
    if (!n.children?.length) continue;
    const sub = extract(n.children, id);
    if (sub) {
      return { node: sub.node, rest: [...list.slice(0, i), { ...n, children: sub.rest }, ...list.slice(i + 1)] };
    }
  }
  return null;
}

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
  return findAndUpdate(tree, parent, (p) => ({ ...p, children: insertSorted(p.children, node, pos) }));
}

// A parent this tree lacks (an undo snapshot taken before the parent was
// made, a subtree we don't hold): a block already here stays where it is —
// it must never vanish with the op — and a new one stays out.
const placeable = (tree, parent, pageId) => parent === pageId || !!findBlock(tree || [], parent);

export function applyOps(tree, ops, pageId, pos) {
  let out = tree;
  for (const op of ops || []) {
    if (op.op === "set") {
      if (op.id === pageId) continue; // the page block itself: the caller's
      out = findAndUpdate(out, op.id, (b) => ({
        ...b,
        ...(op.content !== undefined ? { content: op.content } : {}),
        ...(op.props ? { properties: applyPatch(b.properties, op.props) } : {}),
      }));
    } else if (op.op === "insert") {
      const ex = extract(out, op.id);
      if (!placeable(ex ? ex.rest : out, op.parent, pageId)) {
        if (ex) out = findAndUpdate(out, op.id, (b) => ({ ...b, content: op.content ?? "", properties: op.props || {} }));
        continue;
      }
      pos.set(op.id, op.position);
      const node = ex
        ? { ...ex.node, content: op.content ?? "", properties: op.props || {}, position: op.position }
        : { id: op.id, content: op.content ?? "", properties: op.props || {}, position: op.position,
            children: [] };
      out = placeUnder(ex ? ex.rest : out, op.parent, node, pageId, pos);
    } else if (op.op === "move") {
      const ex = extract(out, op.id);
      if (!ex || !placeable(ex.rest, op.parent, pageId)) continue;
      pos.set(op.id, op.position);
      out = placeUnder(ex.rest, op.parent, { ...ex.node, position: op.position }, pageId, pos);
    } else if (op.op === "delete") {
      const ex = extract(out, op.id);
      if (ex) out = ex.rest;
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
// for the same block when nothing structural about it sits in between.
// Returns the queued op it was folded into (as it was before), or null when
// it was appended.
export function pushOp(queue, op) {
  if (op.op === "set") {
    for (let i = queue.length - 1; i >= 0; i--) {
      const q = queue[i];
      if (q.id !== op.id) continue;
      if (q.op !== "set") break;
      const merged = { ...q };
      if (op.content !== undefined) {
        merged.content = op.content;
        // The queued op keeps its own base (the earliest text this run of
        // keystrokes started from); only a props-only op adopts the new one.
        if (q.content === undefined) merged.base = op.base;
      }
      if (op.props) merged.props = { ...(q.props || {}), ...op.props };
      queue[i] = merged;
      return q;
    }
  }
  queue.push(op);
  return null;
}
