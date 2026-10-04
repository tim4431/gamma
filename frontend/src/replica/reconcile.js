// The three-way decisions of one page's round — the JavaScript twin of the
// pure parts of gamma/sync_engine.py (docs/dev/mirror.md "One round, one
// page"), for the iPad's replica. Snapshots are replica/tree.js's. Each
// function names the Python one it follows; the rules are the same:
//
// - known: blocks both sides hold that the base lacks count as known at
//   the remote's version (a round cut short after one side got them);
// - reconcileRemoteOps: the remote's diff from the base, adjusted so an
//   edit beats a delete, both ways;
// - ownEdits / split / strays: only what this copy changed since the base
//   is pushed, the rest is put back as the remote has it;
// - unlanded: one op of a push whose answer was lost, split into what the
//   remote shows and what goes again.
import { normalizeTextBox } from "../markup/textBox.js";
import { contains } from "./textmerge.js";
import { ancestors, diff, moved, same, subtreeIds, treeOrder } from "./tree.js";

// _known. `conflicts` collects {id, kind, mine, theirs, result}.
export function known(base, local, remote, conflicts) {
  const extra = Object.keys(local).filter((bid) => bid in remote && !(bid in base));
  if (!extra.length) return base;
  const out = { ...base };
  for (const bid of extra) {
    const mine = local[bid], theirs = remote[bid];
    const props = {};
    for (const [k, v] of Object.entries(theirs.props)) if (k in mine.props) props[k] = v;
    out[bid] = { ...theirs, props };
    if (!mine.content.includes(theirs.content)) {
      conflicts.push({ id: bid, kind: "diverged", mine: mine.content, theirs: theirs.content, result: mine.content });
    }
  }
  return out;
}

// _reconcile_remote_ops.
export function reconcileRemoteOps(base, local, remote, pageId, conflicts) {
  const remoteOps = diff(base, remote, pageId);
  const localOps = diff(base, local, pageId);
  const localEdited = new Set([...localOps.filter((op) => op.op === "set" || op.op === "insert").map((op) => op.id), ...moved(base, local)]);
  for (const op of localOps) if (op.op === "insert") localEdited.add(op.parent);
  const touchedHere = new Set();
  for (const bid of localEdited) {
    touchedHere.add(bid);
    for (const a of ancestors(local, bid)) touchedHere.add(a);
  }
  const localDeleted = localOps.filter((op) => op.op === "delete").map((op) => op.id);
  const remoteTouched = new Set(remoteOps.filter((op) => op.op !== "delete").map((op) => op.id));
  for (const op of remoteOps) if (op.op === "insert" || op.op === "move") remoteTouched.add(op.parent);

  const out = [], restored = new Set(), restoredTops = [], escaped = new Set(), inserted = new Set();
  for (const top of localDeleted) {
    if (!(top in remote)) continue; // the remote let it go too
    const gone = subtreeIds(base, top), still = subtreeIds(remote, top);
    const left = new Set([...gone].filter((bid) => !still.has(bid) && bid in remote));
    for (const bid of left) if (!ancestors(remote, bid).some((a) => left.has(a))) escaped.add(bid);
    if ([...still].some((bid) => remoteTouched.has(bid))) {
      for (const bid of still) restored.add(bid);
      restoredTops.push(top);
    }
  }
  const insertRemote = (bid) => {
    const r = remote[bid];
    const knownParent = r.parent in local || restored.has(r.parent) || inserted.has(r.parent);
    inserted.add(bid);
    out.push({ op: "insert", id: bid, parent: knownParent ? r.parent : pageId, position: r.position, content: r.content,
      props: { ...r.props } });
  };
  if (restored.size) {
    for (const bid of treeOrder(remote, pageId)) if (restored.has(bid) && !(bid in local)) insertRemote(bid);
    for (const top of restoredTops) {
      conflicts.push({ id: top, kind: "restored_remote_edit", theirs: remote[top].content,
        result: "kept the other side's version of a subtree deleted here" });
    }
  }
  for (let op of remoteOps) {
    const bid = op.id;
    if (inserted.has(bid)) continue;
    if (op.op === "move" && escaped.has(bid)) {
      for (const eid of [bid, ...treeOrder(remote, pageId).filter((d) => d !== bid && ancestors(remote, d).includes(bid))]) {
        if (!(eid in local)) insertRemote(eid);
      }
      conflicts.push({ id: bid, kind: "restored_remote_edit", theirs: remote[bid].content,
        result: "kept a block the other side moved out of a subtree deleted here" });
      continue;
    }
    if (op.op === "delete" && (touchedHere.has(bid) || [...subtreeIds(local, bid)].some((x) => touchedHere.has(x)))) {
      conflicts.push({ id: bid, kind: "kept_local_edit", mine: local[bid]?.content || "",
        result: "kept a subtree edited here that the other side deleted", once: true });
      continue;
    }
    if (op.op !== "insert" && !(bid in local)) continue; // gone here, not restored: the other side's change is dropped
    if (op.op === "set" && "base" in op && local[bid].content !== op.base && local[bid].content !== op.content
        && contains(op.base, op.content, local[bid].content)) {
      // the text here holds the change already (typed on since): merging it again would double it
      const { content, base: _b, ...rest } = op; // eslint-disable-line no-unused-vars
      if (!("props" in rest)) continue;
      op = rest;
    }
    if ((op.op === "insert" || op.op === "move") && !(op.parent in local) && !restored.has(op.parent) && !inserted.has(op.parent)) {
      op = { ...op, parent: pageId }; // its parent is gone here: land at the page's top level
    }
    if (op.op === "insert") inserted.add(bid);
    out.push(op);
  }
  return out;
}

// _own_edits: Map id → Set of "new" | "deleted" | "content" | "props" | "place".
export function ownEdits(base, local) {
  const out = new Map();
  for (const [bid, b] of Object.entries(local)) {
    const was = base[bid];
    if (!was) { out.set(bid, new Set(["new"])); continue; }
    const kinds = new Set();
    if (b.content !== was.content) kinds.add("content");
    if (!same(b.props, was.props)) kinds.add("props");
    if (kinds.size) out.set(bid, kinds);
  }
  for (const bid of moved(base, local)) {
    if (!out.has(bid)) out.set(bid, new Set());
    out.get(bid).add("place");
  }
  for (const bid of Object.keys(base)) if (!(bid in local)) out.set(bid, new Set(["deleted"]));
  return out;
}

const mineOf = (bid, edits) => edits.get(bid) || new Set();

// _split, with no typing during the round (the replica rereads the page
// instead): the part of the ops (remote tree → this copy) that is this
// copy's to push.
export function split(ops, edits) {
  const out = [];
  for (const op of ops) {
    const mine = mineOf(op.id, edits);
    if (op.op === "insert" || mine.has("new")) out.push(op);
    else if (op.op === "delete") { if (mine.has("deleted")) out.push(op); }
    else if (op.op === "move") { if (mine.has("place")) out.push(op); }
    else {
      const part = { op: "set", id: op.id };
      if (mine.has("content") && "content" in op) { part.content = op.content; if ("base" in op) part.base = op.base; }
      if (mine.has("props") && op.props) { part.props = op.props; if (op.base_props) part.base_props = op.base_props; }
      if ("content" in part || "props" in part) out.push(part);
    }
  }
  return out;
}

// _strays: from the ops this copy → remote tree, what differs here only
// through the round's own writes, put back as the remote has it.
// `elsewhere`: ids another local page holds.
export function strays(back, local, edits, elsewhere) {
  const out = [], here = new Set(Object.keys(local));
  for (const op of back) {
    const bid = op.id, mine = mineOf(bid, edits);
    if (mine.has("new")) continue;
    if (op.op === "move") {
      if (!mine.has("place") && bid in local && here.has(op.parent)) out.push(op);
    } else if (op.op === "insert") {
      if (!mine.has("deleted") && !elsewhere.has(bid) && here.has(op.parent)) { out.push(op); here.add(bid); }
    } else if (op.op === "set" && bid in local) {
      const part = { op: "set", id: bid };
      if (!mine.has("content") && "content" in op) { part.content = op.content; if ("base" in op) part.base = op.base; }
      if (!mine.has("props") && op.props) { part.props = op.props; if (op.base_props) part.base_props = op.base_props; }
      if ("content" in part || "props" in part) out.push(part);
    }
  }
  const moving = new Set(out.filter((op) => op.op === "move").map((op) => op.id));
  const held = new Map(Object.entries(local).map(([h, b]) => [`${b.parent}\u0000${b.position}`, h]));
  return out.filter((op) => op.op !== "move" || moving.has(held.get(`${op.parent}\u0000${op.position}`) ?? op.id));
}

// An ink group's drawing and what is derived from it: they travel together.
export const INK_KEYS = ["ink_url", "ink_strokes", "pdf_position"];

// _unlanded: [the part the remote shows, the part to send again]; null for
// either that is empty.
export function unlanded(op, base, remote) {
  const bid = op.id;
  if (op.op === "insert") return bid in remote ? [op, null] : [null, op];
  if (op.op === "delete") {
    if (!(bid in remote)) return [op, null];
    const there = subtreeIds(remote, bid), was = subtreeIds(base, bid);
    const untouched = there.size === was.size && [...there].every((d) => was.has(d) && same(remote[d], base[d]));
    return untouched ? [null, op] : [null, null];
  }
  if (!(bid in remote)) return [null, null];
  const now = remote[bid], was = base[bid] || { props: {} };
  if (op.op === "move") {
    const atNow = now.parent === op.parent && now.position === op.position;
    const atBase = now.parent === was.parent && now.position === was.position;
    return !atNow && atBase ? [null, op] : [op, null];
  }
  const landed = { op: "set", id: bid }, rest = { op: "set", id: bid };
  if ("content" in op) {
    const before = "base" in op ? op.base : was.content || "";
    const shows = now.content === op.content || contains(before, op.content, now.content);
    const target = shows ? landed : rest;
    target.content = op.content;
    if ("base" in op) target.base = op.base;
  }
  const props = op.props || {}, baseProps = op.base_props || {};
  const ink = "ink_url" in props && "ink_url" in baseProps && now.props.ink_url !== props.ink_url;
  // A text box merged key by key is judged key by key (_box_again).
  const box = "text_box" in props && "text_box" in baseProps ? boxAgain(props.text_box, baseProps.text_box, now.props.text_box) : false;
  for (const [k, v] of Object.entries(props)) {
    if (ink && INK_KEYS.includes(k)) { (rest.props ||= {})[k] = v; continue; }
    if (k === "text_box" && box !== false) {
      if (box === null) (landed.props ||= {})[k] = v;
      else (rest.props ||= {})[k] = box;
      continue;
    }
    const unchanged = same(now.props[k] ?? null, was.props?.[k] ?? null) && !same(now.props[k] ?? null, v);
    ((unchanged ? rest : landed).props ||= {})[k] = v;
  }
  if (ink) rest.base_props = op.base_props;
  if (box) rest.base_props = { ...(rest.base_props || {}), text_box: now.props.text_box };
  const size = (o) => Object.keys(o).length - ("base_props" in o ? 1 : 0);
  return [size(landed) > 2 ? landed : null, size(rest) > 2 ? rest : null];
}

// _box_again: the box to send again (the remote's, with the keys of ours it
// still shows as they were), null when it shows each as ours or changed
// since, false when one of them is no box (the plain rule).
function boxAgain(mine, base, now) {
  const m = normalizeTextBox(mine), b = normalizeTextBox(base), n = normalizeTextBox(now);
  if (!m || !b || !n) return false;
  const pending = Object.fromEntries(Object.entries(m).filter(([k, v]) => v !== b[k] && n[k] === b[k]));
  return Object.keys(pending).length ? { ...n, ...pending } : null;
}
