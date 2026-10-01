// A page's tree as a flat snapshot and the ops between snapshots — the
// JavaScript twin of gamma/sync_tree.py, for the iPad's replica
// (replica/round.js, docs/dev/ipad.md). A snapshot is {block id: {parent,
// position, content, props}} over the page root and everything under it.
//
// diff(base, target, pageId) is sync_tree.diff: inserts for ids only the
// target has, moves for a changed parent or key, sets for changed content
// (with `base`, the text it was edited from) or a props patch (with
// `base_props` naming the ink_url a changed drawing was edited from and the
// text_box a changed text box was changed from), and deletes of the
// top-most removed subtrees last; inserts and moves in tree order.
// apply(snapshot, ops) is sync_tree.apply (keys as sent, no merge).
// applyLocal(snapshot, ops, ink) is what a server does with ops — the
// replica has no server of its own: a text edited from an older base is
// merged (replica/textmerge.js), a drawing edited from an older ink_url is
// merged by stroke (ink/ink.js mergeInk), a text box changed from an older
// box key by key (markup/textBox.js mergeTextBox), an insert of a known id
// changes nothing.
import { inkProps, mergeInk } from "../ink/ink.js";
import { mergeTextBox } from "../markup/textBox.js";
import { merge as textMerge } from "./textmerge.js";

// Deep equality of JSON values, blind to object key order (Python's ==).
export function same(a, b) {
  if (a === b) return true;
  if (a === null || b === null || typeof a !== "object" || typeof b !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) return a.length === b.length && a.every((v, i) => same(v, b[i]));
  const ka = Object.keys(a), kb = Object.keys(b);
  return ka.length === kb.length && ka.every((k) => Object.prototype.hasOwnProperty.call(b, k) && same(a[k], b[k]));
}

const clone = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
const copyBlock = (b) => ({ parent: b.parent, position: b.position, content: b.content, props: { ...b.props } });

// The set.props patch turning `oldP` into `newP` (null deletes a key).
export function propsPatch(oldP, newP) {
  const o = oldP || {}, n = newP || {}, patch = {};
  for (const k of Object.keys(n)) if (!(k in o) || !same(o[k], n[k])) patch[k] = n[k];
  for (const k of Object.keys(o)) if (!(k in n)) patch[k] = null;
  return patch;
}

// From the nested GET /blocks/{id}/subtree node.
export function snapshotFromTree(node) {
  const out = {};
  const pending = [node];
  while (pending.length) {
    const n = pending.pop();
    out[n.id] = { parent: n.parent_id ?? null, position: n.position || "", content: n.content || "",
      props: { ...(n.properties || {}) } };
    pending.push(...(n.children || []));
  }
  return out;
}

// The nested tree of a snapshot (the page's children, each with its own),
// siblings by key: what an editor shows.
export function treeOf(snapshot, pageId) {
  const kids = childrenOf(snapshot);
  const build = (id) => (kids.get(id) || []).map((bid) => {
    const b = snapshot[bid];
    return { id: bid, content: b.content, properties: b.props, position: b.position, children: build(bid) };
  });
  return build(pageId);
}

// Map parent → [ids sorted by (position, id)].
export function childrenOf(snapshot) {
  const kids = new Map();
  for (const [bid, b] of Object.entries(snapshot)) {
    if (!kids.has(b.parent)) kids.set(b.parent, []);
    kids.get(b.parent).push(bid);
  }
  for (const ids of kids.values()) {
    ids.sort((x, y) => {
      const px = snapshot[x].position, py = snapshot[y].position;
      return px < py ? -1 : px > py ? 1 : x < y ? -1 : x > y ? 1 : 0;
    });
  }
  return kids;
}

// Every id under the page, parents before children, siblings by key.
export function treeOrder(snapshot, pageId) {
  const kids = childrenOf(snapshot);
  const out = [], queue = [...(kids.get(pageId) || [])];
  while (queue.length) {
    const bid = queue.shift();
    out.push(bid);
    queue.push(...(kids.get(bid) || []));
  }
  return out;
}

export function subtreeIds(snapshot, blockId) {
  const kids = childrenOf(snapshot);
  const out = new Set(), queue = [blockId];
  while (queue.length) {
    const bid = queue.pop();
    if (out.has(bid)) continue;
    out.add(bid);
    queue.push(...(kids.get(bid) || []));
  }
  return out;
}

// The chain of parents above a block, nearest first (the page root last).
export function ancestors(snapshot, blockId) {
  const out = [], seen = new Set();
  let cur = snapshot[blockId];
  while (cur && cur.parent && cur.parent in snapshot && !seen.has(cur.parent)) {
    seen.add(cur.parent);
    out.push(cur.parent);
    cur = snapshot[cur.parent];
  }
  return out;
}

function setOp(bid, b, t, withBase) {
  const op = { op: "set", id: bid };
  if (!b || t.content !== b.content) {
    op.content = t.content;
    if (withBase && b) op.base = b.content;
  }
  const patch = propsPatch(b ? b.props : {}, t.props);
  if ("content" in op && "auto_title" in t.props && !("auto_title" in patch)) {
    // a content write drops the automatic-title marker unless the patch names it (ops.py)
    patch.auto_title = t.props.auto_title;
  }
  if (Object.keys(patch).length) {
    op.props = patch;
    const baseProps = {};
    if (withBase && b && "ink_url" in patch) baseProps.ink_url = b.props.ink_url || "";
    const box = b?.props.text_box;
    if (withBase && "text_box" in patch && box && typeof box === "object" && !Array.isArray(box)) baseProps.text_box = box;
    if (Object.keys(baseProps).length) op.base_props = baseProps;
  }
  return Object.keys(op).length > 2 ? op : null;
}

// The ops turning base into target. The page root is only ever `set`,
// and only when both snapshots have it.
export function diff(base, target, pageId, { withBase = true } = {}) {
  const ops = [];
  if (base[pageId] && target[pageId]) {
    const op = setOp(pageId, base[pageId], target[pageId], withBase);
    if (op) ops.push(op);
  }
  for (const bid of treeOrder(target, pageId)) {
    const t = target[bid], b = base[bid];
    if (!b) {
      ops.push({ op: "insert", id: bid, parent: t.parent, position: t.position, content: t.content, props: { ...t.props } });
      continue;
    }
    if (t.parent !== b.parent || t.position !== b.position) ops.push({ op: "move", id: bid, parent: t.parent, position: t.position });
    const op = setOp(bid, b, t, withBase);
    if (op) ops.push(op);
  }
  for (const bid of Object.keys(base)) {
    if (bid in target || bid === pageId) continue;
    const parent = base[bid].parent;
    if (parent in target || parent === pageId || !(parent in base)) ops.push({ op: "delete", id: bid });
  }
  return ops;
}

function applyPatch(props, patch) {
  for (const [k, v] of Object.entries(patch || {})) {
    if (v === null || v === undefined) delete props[k];
    else props[k] = clone(v);
  }
}

// `snapshot` after `ops` as a server applies them, keys taken as sent. An
// op whose block or parent is missing changes nothing.
export function apply(snapshot, ops) {
  const out = {};
  for (const [k, v] of Object.entries(snapshot)) out[k] = copyBlock(v);
  for (const op of ops) {
    const bid = op.id;
    if (op.op === "insert") {
      if (!(bid in out) && op.parent in out) {
        out[bid] = { parent: op.parent, position: op.position || "", content: op.content || "", props: clone(op.props || {}) };
      }
    } else if (op.op === "move") {
      if (bid in out && op.parent in out) out[bid] = { ...out[bid], parent: op.parent, position: op.position || out[bid].position };
    } else if (op.op === "set") {
      if (!(bid in out)) continue;
      const b = out[bid], patch = op.props || {};
      if (op.content !== undefined && op.content !== null) {
        b.content = op.content;
        if (!("auto_title" in patch)) delete b.props.auto_title;
      }
      applyPatch(b.props, patch);
    } else if (op.op === "delete" && bid in out) {
      for (const gone of subtreeIds(out, bid)) delete out[gone];
    }
  }
  return out;
}

// What the ops do to a snapshot the replica holds, as a server would apply
// them: → {snapshot, applied (the ops as applied), merged: [{id, kind,
// mine, theirs, result, base}]} — `merged` names every text two sides
// changed (the mirror's `merged` conflicts). `ink`: {read(url) → ink file
// or null, store(ink) → url}, for drawings edited from an older ink_url.
export async function applyLocal(snapshot, ops, ink) {
  const out = {}, applied = [], merged = [];
  for (const [k, v] of Object.entries(snapshot)) out[k] = copyBlock(v);
  for (const op of ops) {
    const bid = op.id;
    if (op.op === "insert") {
      if (bid in out || !(op.parent in out)) continue; // a known id stays as it is; no parent, no block
      out[bid] = { parent: op.parent, position: op.position || "", content: op.content || "", props: clone(op.props || {}) };
      applied.push(op);
    } else if (op.op === "move") {
      if (!(bid in out) || !(op.parent in out) || subtreeIds(out, bid).has(op.parent)) continue;
      out[bid] = { ...out[bid], parent: op.parent, position: op.position || out[bid].position };
      applied.push(op);
    } else if (op.op === "set") {
      if (!(bid in out)) continue;
      const b = out[bid], echo = { op: "set", id: bid };
      let patch = op.props ? { ...op.props } : null;
      if (op.content !== undefined && op.content !== null) {
        let content = op.content;
        const base = op.base;
        if (base !== undefined && base !== null && base !== b.content && base !== content && content !== b.content) {
          const [text] = textMerge(base, content, b.content);
          merged.push({ id: bid, kind: "merged", mine: b.content, theirs: content, result: text, base });
          content = text;
        }
        b.content = content;
        echo.content = content;
        if (!(patch && "auto_title" in patch)) delete b.props.auto_title;
      }
      if (patch && op.base_props && ink) patch = await mergeDrawing(b.props, patch, op.base_props, ink);
      if (patch && "text_box" in patch && "text_box" in (op.base_props || {})) {
        patch.text_box = mergeTextBox(b.props.text_box, patch.text_box, op.base_props.text_box);
      }
      if (patch) { applyPatch(b.props, patch); echo.props = patch; }
      if (Object.keys(echo).length > 2) applied.push(echo);
    } else if (op.op === "delete") {
      if (!(bid in out)) continue;
      for (const gone of subtreeIds(out, bid)) delete out[gone];
      applied.push(op);
    }
  }
  return { snapshot: out, applied, merged };
}

// ops.py merge_ink on the replica's files: the patch naming the merge of
// the drawing it carries into the stored one, or as sent.
async function mergeDrawing(props, patch, baseProps, ink) {
  const next = patch.ink_url, base = baseProps.ink_url, now = props.ink_url || "";
  if (typeof next !== "string" || !next || typeof base !== "string" || now === base || now === next) return patch;
  const ours = await ink.read(next);
  const theirs = now ? await ink.read(now) : null;
  if (!ours || (now && !theirs)) return patch;
  const empty = { format: "gamma-ink", version: 1, space: ours.space, strokes: [] };
  const was = base ? await ink.read(base) : empty;
  const { ink: result } = mergeInk(was, ours, theirs || empty);
  const url = result === ours ? next : result === theirs ? now : await ink.store(result);
  const derived = inkProps(result, url);
  const out = { ...patch, ink_url: url, ink_strokes: derived.ink_strokes };
  if ("pdf_position" in patch) out.pdf_position = derived.pdf_position;
  return out;
}

// The blocks target holds somewhere else than base did: under another
// parent, or on a key that puts them elsewhere among their siblings (a key
// the server only re-keyed between the same neighbours is no move).
export function moved(base, target) {
  const out = new Set();
  for (const [parent, ids] of childrenOf(target)) {
    ids.forEach((bid, n) => {
      const was = base[bid];
      if (!was || (was.parent === parent && was.position === target[bid].position)) return;
      const lower = n ? target[ids[n - 1]].position : null;
      const upper = n + 1 < ids.length ? target[ids[n + 1]].position : null;
      const key = was.position;
      if (was.parent !== parent || !((lower === null || lower < key) && (upper === null || key < upper))) out.add(bid);
    });
  }
  return out;
}

// The upload names blocks reference (storage.upload_refs): /api/uploads/<name>
// in content or props, and a page's doc_id (its PDF).
const UPLOAD_REF = /\/api\/uploads\/([0-9A-Za-z_-]+\.[0-9A-Za-z]{1,12})(?![0-9A-Za-z])/g;
const DOC_STEM = /^[0-9A-Za-z_-]{1,128}$/;
export function uploadRefs(blocks) {
  const names = new Set();
  for (const b of blocks) {
    for (const text of [b.content || "", JSON.stringify(b.props || {})]) {
      if (text.includes("/api/uploads/")) for (const m of text.matchAll(UPLOAD_REF)) names.add(m[1]);
    }
    const doc = b.props?.doc_id;
    if (typeof doc === "string" && DOC_STEM.test(doc)) names.add(`${doc}.pdf`);
  }
  return names;
}
