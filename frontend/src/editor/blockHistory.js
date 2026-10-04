// THE undo/redo history of the page: one stack, derived from the block
// tree's own state transitions — no call site declares "this is undoable",
// and the block editor keeps no history of its own (CodeMirror's `history()`
// is not installed; Ctrl+Z inside an editor reaches the same stack).
//
// Every committed change to `blocks` is classified by diffing it against the
// previous committed tree:
//   - trees that did not come from an edit here (`originOf(tree)`: "load",
//     fetched from the server, or "remote", another client's ops — the
//     caller marks them) and undo/redo applications are never recorded, nor
//     is an edit here marked "fold": it belongs to the entry before it (a
//     text box's measured size, App's foldBlocks), which then restores both;
//     a load also empties the stack — its snapshots predate what the fetch
//     brought in (a note moved here from another page, an import), and
//     restoring one would delete that (a remote op is folded into every
//     snapshot instead, `rebase`);
//   - opening/closing editors and collapse toggles are not edits;
//   - everything else (add/delete/move/indent, property changes such as a
//     highlight colour or link, any content change — typing, checkboxes,
//     table cells, image size) pushes the previous tree.
// Consecutive content-only edits of the same block merge into one entry
// when they come quickly (TYPING_MERGE_MS while the block's editor is open —
// a run of typing undoes as one chunk, like any editor — else
// EDIT_MERGE_MS: a drag, a run of toggles), and so do consecutive changes
// of one text box's `text_box` alone (EDIT_MERGE_MS: a held arrow key, a
// colour dragged in the picker). A fold that takes the tree back to the
// newest entry's (a text box made and left empty) takes that entry back,
// and brings back the redo steps it cleared.
//
// An entry is {tree, caret, mark}: the tree kept by reference (the helpers
// never mutate in place), when the change came from an editor that
// editor's selection before it, and the history's clock when it was pushed;
// the newest also keeps the redo steps its push cleared (`redo`).
// The open editor is not part of the tree (App's `view`, blockModel.js):
// the caller passes `editingId` and takes `onEditing` back. Restoring while
// an editor is open keeps the caret's block in edit mode and hands the caret
// back through `onCaret` so the editor puts the cursor where the change was;
// restoring with no editor open opens none, so undo never pops editors
// open. The stack belongs to one page and is cleared when the page id
// changes.
//
// Collaborative undo: another client's ops are folded into every entry
// (`rebaseHistory`) — structure and properties as they are, text as a
// change carried over onto the entry's own text, so undoing our typing in a
// block someone else typed in too takes out only ours, and a text box's
// `text_box` as the keys they changed. Undo never takes
// back what someone else changed after an entry was recorded
// (`planRestore`): a block the restore would delete that they edited,
// moved or made stays, as does text of theirs an entry's text couldn't be
// rebased around; the rest of the entry applies, and when nothing is left
// the step says so instead of pretending. An entry that changes nothing any
// more is passed over. The stack's bookkeeping is plain functions over one
// state object (`observeTree`, `rebaseHistory`, `undoStep`, `clearHistory`),
// so the node tests drive it without React.
import { useCallback, useEffect, useRef } from "react";
import { generateNKeysBetween } from "fractional-indexing";
import { applyOps, diffTrees, indexTree, someNode, withTexts } from "../shared/model/blockOps.js";
import { isHighlightBlock } from "../shared/model/blockModel.js";
import { isTextBox, mergeTextBox, normalizeTextBox } from "../markup/textBox.js";
import { t } from "../shared/i18n/i18n.js";

const MAX_ENTRIES = 200;
const TYPING_MERGE_MS = 500;
const EDIT_MERGE_MS = 1000;

// Collapsed lives in both the block and its properties; neither is an edit.
function propsEqual(a, b) {
  if (a === b) return true;
  const ka = Object.keys(a || {}).filter((k) => k !== "collapsed");
  const kb = Object.keys(b || {}).filter((k) => k !== "collapsed");
  if (ka.length !== kb.length) return false;
  for (const k of ka) {
    const va = a[k], vb = b[k];
    if (va === vb) continue;
    if (typeof va !== "object" || typeof vb !== "object" || va === null || vb === null) return false;
    if (JSON.stringify(va) !== JSON.stringify(vb)) return false;
  }
  return true;
}

// null: nothing undoable changed; true: structural/property edit;
// a block id: only that block's content changed. Exported for its tests.
export function classifyTransition(prev, next) {
  if (prev === next) return null;
  if (prev.length !== next.length) return true;
  let only = null;
  for (let i = 0; i < prev.length; i++) {
    const a = prev[i], b = next[i];
    if (a === b) continue;
    if (a.id !== b.id) return true;
    if (a.content !== b.content) only = only === null ? a.id : true;
    if (!propsEqual(a.properties, b.properties)) return true;
    const sub = classifyTransition(a.children || [], b.children || []);
    if (sub === true) return true;
    if (sub) only = only === null ? sub : true;
    if (only === true) return true;
  }
  return only;
}

// The id of the one block whose `text_box` alone changed from `prev` to
// `next` (no text, place or other property), else null. Exported for its
// tests.
export function boxChange(prev, next) {
  let id = null;
  const walk = (a, b) => {
    if (a === b) return true;
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) {
      const x = a[i], y = b[i];
      if (x === y) continue;
      if (x.id !== y.id || x.content !== y.content) return false;
      if (!propsEqual(x.properties, y.properties)) {
        if (id !== null || !propsEqual(withoutBox(x.properties), withoutBox(y.properties))) return false;
        id = x.id;
      }
      if (!walk(x.children || [], y.children || [])) return false;
    }
    return true;
  };
  return walk(prev, next) ? id : null;
}
const withoutBox = (props) => ({ ...(props || {}), text_box: null });

// Describe the forward action, even when the caller is restoring it backward.
// Compare rebased trees so a collaborator's edits are not named as our undo.
export function describeTransition(before, after) {
  const index = (tree, parent = null, out = new Map()) => {
    tree.forEach((b, order) => { out.set(b.id, { ...b, parent, order }); index(b.children || [], b.id, out); });
    return out;
  };
  const a = index(before), b = index(after);
  const added = [...b.values()].filter((n) => !a.has(n.id));
  const removed = [...a.values()].filter((n) => !b.has(n.id));
  const preview = (n) => {
    const text = (n?.content || "").replace(/\s+/g, " ").trim();
    return text ? `: “${text.length > 48 ? text.slice(0, 47) + "…" : text}”` : "";
  };
  if (added.length && removed.length) return t("note replacement ({n} removed, {n2} added)", { n: removed.length, n2: added.length });
  if (added.length === 1 && isTextBox(added[0])) return t("text box creation{added}", { added: preview(added[0]) });
  if (added.length) return added.length === 1 ? t("note creation{added}", { added: preview(added[0]) }) : t("creation of {n} notes", { n: added.length });
  if (removed.length === 1 && isTextBox(removed[0])) return t("text box deletion{removed}", { removed: preview(removed[0]) });
  if (removed.length) return removed.length === 1 ? t("note deletion{removed}", { removed: preview(removed[0]) }) : t("deletion of {n} notes", { n: removed.length });
  const moved = [...b.values()].filter((n) => a.get(n.id)?.parent !== n.parent || a.get(n.id)?.order !== n.order);
  if (moved.length) return t("note move");
  const text = [...b.values()].filter((n) => a.get(n.id)?.content !== n.content);
  const props = [...b.values()].filter((n) => !propsEqual(a.get(n.id)?.properties, n.properties));
  // Typing in a text box also stores the size it then measured at.
  const boxText = text.length === 1 && isTextBox(text[0]) && props.every((n) => n.id === text[0].id
    && propsEqual(withoutBox(a.get(n.id)?.properties), withoutBox(n.properties)));
  if (boxText) return t("text box text edit{text}", { text: preview(text[0]) });
  if (text.length && props.length) return t("note text and properties edit");
  if (text.length) return text.length === 1 ? t("note text edit{text}", { text: preview(text[0]) }) : t("text edits in {n} notes", { n: text.length });
  if (props.length) {
    if (props.every((n) => n.properties?.ink_url !== undefined)) return t("handwriting note update");
    if (props.every(isTextBox)) return textBoxChange(props.map((n) => [a.get(n.id)?.properties?.text_box, n.properties.text_box]));
    if (props.every(isHighlightBlock)) {
      return props.every((n) => a.get(n.id)?.properties?.color !== n.properties.color) ? t("highlight color change") : t("highlight edit");
    }
    return t("note properties change");
  }
  return t("note edit");
}

// What a change of text boxes (pairs of their `text_box` before and after)
// did: a move when `x` or `y` changed, else a style change when `size`,
// `color` or `bg` did, else a resize. A move or a restyle also stores the
// size the box then measured at; it is named for what the user did.
function textBoxChange(pairs) {
  const changed = (key) => pairs.some(([before, after]) => normalizeTextBox(before)?.[key] !== normalizeTextBox(after)?.[key]);
  if (changed("x") || changed("y")) return t("text box move");
  if (changed("size") || changed("color") || changed("bg")) return t("text box style change");
  return t("text box resize");
}

function hasBlock(list, id) {
  for (const b of list || []) {
    if (b.id === id || hasBlock(b.children, id)) return true;
  }
  return false;
}

// The history's state: the two stacks, the tree last seen, the bookkeeping
// of the transition being recorded, and what other clients touched when:
// `tick` counts their batches, `touched` maps a block to the tick that last
// changed it (an entry's `mark` is the tick it was pushed at).
export function createHistory(tree = []) {
  return { undo: [], redo: [], prev: tree, prevCaret: null, prevEditing: null, displaced: null, intent: null, lastEdit: null,
    tick: 0, touched: new Map() };
}

export function clearHistory(s) {
  s.undo = [];
  s.redo = [];
  s.lastEdit = null;
  s.touched = new Map();
}

// Another client's change `from → to` of one block's text carried over onto
// `text`, that block's text in a snapshot (`from` changed by our own edits
// since): each change taken as one replaced span (past the common prefix
// and suffix), theirs lands in the snapshot unless the two spans overlap —
// then null: it can't be told apart from ours.
export function rebaseText(from, to, text) {
  if (text === from) return to;
  if (to === from) return text;
  const span = (a, b) => {
    const max = Math.min(a.length, b.length);
    let head = 0;
    while (head < max && a.charCodeAt(head) === b.charCodeAt(head)) head++;
    let tail = 0;
    while (tail < max - head && a.charCodeAt(a.length - 1 - tail) === b.charCodeAt(b.length - 1 - tail)) tail++;
    return [head, a.length - tail, b.slice(head, b.length - tail)];
  };
  const [ours0, ours1] = span(from, text);
  const [theirs0, theirs1, inserted] = span(from, to);
  if (theirs1 <= ours0) return text.slice(0, theirs0) + inserted + text.slice(theirs1);
  if (theirs0 >= ours1) {
    const shift = text.length - from.length;
    return text.slice(0, theirs0 + shift) + inserted + text.slice(theirs1 + shift);
  }
  return null;
}

// Another client's batch landed: fold it into every entry, so undoing our
// own edits never reverts theirs (what a collaborative undo means).
// `before` is the tree the ops were applied to (the session's base): a
// content set is carried over onto each entry's text as the change
// before → after (`rebaseText`); where it can't be, the entry keeps its
// text and marks the block `contested`. An entry whose snapshot differs
// from `before` on a block the batch touches is `hit`: should it change
// nothing any more, that is because of them. Every block the batch
// changed is stamped in `touched`.
export function rebaseHistory(s, ops, { pageId, pos, before = [] }) {
  const stamp = ++s.tick;
  const was = indexTree(before, pageId);
  const texts = new Map(); // id → [text before the batch, after it]
  const boxes = new Map(); // id → [text_box before the batch, after it]
  const shaped = [];
  for (const op of ops || []) {
    if (op.op === "set") {
      const props = Object.keys(op.props || {}).some((k) => k !== "collapsed");
      if (op.content !== undefined || props) s.touched.set(op.id, stamp);
      const had = was.get(op.id)?.node;
      if (had && op.props && "text_box" in op.props) {
        boxes.set(op.id, [boxes.has(op.id) ? boxes.get(op.id)[0] : had.properties?.text_box, op.props.text_box]);
      }
      if (op.content !== undefined && had) {
        texts.set(op.id, [texts.get(op.id)?.[0] ?? (had.content || ""), op.content]);
        if (op.props) shaped.push({ op: "set", id: op.id, props: op.props });
        continue;
      }
    } else if (op.op === "insert" || op.op === "move") s.touched.set(op.id, stamp);
    shaped.push(op);
  }
  const ids = [...new Set((ops || []).map((op) => op.id))];
  const rebase = (e) => {
    const mine = !e.hit || texts.size || boxes.size ? indexTree(e.tree, pageId) : null;
    const hit = e.hit || ids.some((id) => {
      const a = mine.get(id), b = was.get(id);
      if (!a || !b) return !!a !== !!b;
      return a.parent !== b.parent || (a.node.content || "") !== (b.node.content || "")
        || !propsEqual(a.node.properties, b.node.properties);
    });
    // A text box, which every change sends whole, takes only the keys
    // they changed: undoing our move never takes back their restyle, nor
    // their measured size, and theirs never blocks ours.
    const theirs = boxes.size ? shaped.map((op) => (op.op === "set" && boxes.has(op.id) && op.props && "text_box" in op.props
      ? { ...op, props: { ...op.props, text_box: mergeTextBox(mine.get(op.id)?.node.properties?.text_box,
        boxes.get(op.id)[1], boxes.get(op.id)[0]) } } : op)) : shaped;
    let tree = theirs.length ? applyOps(e.tree, theirs, pageId, pos) : e.tree;
    let contested = e.contested;
    const changed = new Map();
    for (const [id, [from, to]] of texts) {
      const node = mine.get(id)?.node;
      if (!node) continue;
      const text = rebaseText(from, to, node.content || "");
      if (text === null) contested = new Set([...(contested || []), id]);
      else if (text !== (node.content || "")) changed.set(id, text);
    }
    if (changed.size) tree = withTexts(tree, changed);
    return { ...e, tree, hit, contested, ...(e.redo ? { redo: e.redo.map(rebase) } : {}) };
  };
  if (s.undo.length) s.undo = s.undo.map(rebase);
  if (s.redo.length) s.redo = s.redo.map(rebase);
}

// Scratch positions for planning a restore: the order the tree has now.
function orderKeys(tree, pos = new Map()) {
  const keys = generateNKeysBetween(null, null, (tree || []).length);
  (tree || []).forEach((n, i) => { pos.set(n.id, keys[i]); orderKeys(n.children, pos); });
  return pos;
}
const PLAN_PAGE = "\u0000page";

// What restoring `entry` over the tree now (`s.prev`) comes to: `tree`
// (null when it would change nothing) and `held` — part of it was left as
// it is, since someone else changed it after the entry was recorded: a
// block the restore would delete (with what it holds) that they edited,
// moved or made, and text of theirs the entry's text couldn't be rebased
// around. The rest applies.
export function planRestore(s, entry) {
  const current = s.prev, target = entry.tree;
  if (classifyTransition(current, target) === null) return { tree: null, held: !!entry.hit };
  const touched = (id) => (s.touched.get(id) || 0) > (entry.mark || 0);
  const pos = orderKeys(current);
  const ops = diffTrees(current, target, PLAN_PAGE, pos);
  const here = indexTree(current, PLAN_PAGE), there = indexTree(target, PLAN_PAGE);
  const kept = [];
  let held = false;
  for (const op of ops) {
    if (op.op === "delete" && someNode(here.get(op.id).node, (n) => !there.has(n.id) && touched(n.id))) {
      held = true; // (what the target has elsewhere is moved out first, never deleted)
      continue;
    }
    if (op.op === "set" && op.content !== undefined && entry.contested?.has(op.id)) {
      held = true;
      if (op.props) kept.push({ op: "set", id: op.id, props: op.props });
      continue;
    }
    kept.push(op);
  }
  if (!held) return { tree: target, held: false };
  const tree = applyOps(current, kept, PLAN_PAGE, pos);
  return { tree: classifyTransition(current, tree) === null ? null : tree, held: true };
}

// Selection of the editor open on block `id`: the live one while that
// editor is still the open one, else what it was at the previous commit
// (the editor has since moved to another block, e.g. Enter made a new one).
function caretOf(s, id, o) {
  if (!id) return null;
  const live = o.caretRef?.current;
  const c = live?.id === id ? live : s.prevCaret?.id === id ? s.prevCaret : null;
  return c ? { id, from: c.from, to: c.to } : null;
}

// A committed tree (`o`: the hook's options, `editingId` the block whose
// editor is open): push the previous tree when the transition was an edit
// here, merge it into the running chunk, or record nothing; a load empties
// the stack.
export function observeTree(s, blocks, o, editingId = null) {
  const prev = s.prev;
  const prevEditing = s.prevEditing;
  s.prev = blocks;
  const intent = s.intent;
  s.intent = null;
  const displaced = s.displaced;
  s.displaced = null;
  try {
    // The editor of the block being merged into closed: the run ends.
    if (s.lastEdit?.editing && editingId !== s.lastEdit.id) s.lastEdit = null;
    if (prev === blocks) return;
    const origin = o.originOf?.(blocks);
    if (origin === "load") { clearHistory(s); return; }
    if (origin === "fold") {
      // Back to the newest entry's tree (a text box made and left empty):
      // the entry goes, and the redo steps it cleared come back.
      const top = s.undo[s.undo.length - 1];
      if (top && classifyTransition(top.tree, blocks) === null) {
        s.undo.pop();
        if (top.redo) s.redo = top.redo;
        s.lastEdit = null;
      }
      return;
    }
    if (origin) return;
    if (intent === "undo") { s.redo.push({ tree: prev, caret: displaced, mark: s.tick }); return; }
    if (intent === "redo") { s.undo.push({ tree: prev, caret: displaced, mark: s.tick }); return; }
    const kind = classifyTransition(prev, blocks);
    if (kind === null) return;
    const now = Date.now();
    const box = kind === true ? boxChange(prev, blocks) : null;
    if (box && s.lastEdit?.box === box && now - s.lastEdit.at < EDIT_MERGE_MS) {
      s.lastEdit.at = now;
      return;
    }
    const editing = kind !== true && editingId === kind;
    if (kind !== true && s.lastEdit?.id === kind
        && now - s.lastEdit.at < (editing && s.lastEdit.editing ? TYPING_MERGE_MS : EDIT_MERGE_MS)) {
      s.lastEdit.at = now;
      s.lastEdit.editing = editing;
      return;
    }
    s.lastEdit = box ? { box, at: now } : kind === true ? null : { id: kind, at: now, editing };
    // A content change from an editor carries the selection it started from.
    const before = o.caretBeforeRef?.current;
    const caret = kind !== true && before?.id === kind ? { ...before } : caretOf(s, prevEditing, o);
    // Only the newest entry keeps the redo steps it cleared (a fold may take it back).
    const top = s.undo[s.undo.length - 1];
    if (top?.redo) delete top.redo;
    s.undo.push({ tree: prev, caret, mark: s.tick, ...(s.redo.length ? { redo: s.redo } : {}) });
    if (s.undo.length > MAX_ENTRIES) s.undo.shift();
    s.redo = [];
  } finally {
    const live = o.caretRef?.current;
    s.prevCaret = live ? { ...live } : null;
    s.prevEditing = editingId;
  }
}

// One undo (or redo) step (`o`: the hook's options): the newest entry that
// still changes something is restored, as far as `planRestore` lets it.
// Returns {description, kept} (kept: what someone else changed since was
// left as it is), {blocked: true} when nothing of the entry could be
// restored for that reason (the entry is used up), or false when the stack
// is empty. `inEditor`: from an open editor — the restored block stays in
// edit mode with the cursor where the change was; otherwise nothing opens.
export function undoStep(s, o, redo = false, inEditor = false) {
  const stack = redo ? s.redo : s.undo;
  for (;;) {
    const entry = stack.pop();
    if (!entry) return false;
    const plan = planRestore(s, entry);
    if (!plan.tree) {
      if (plan.held) return { blocked: true };
      continue; // it changes nothing any more: the one before it
    }
    const description = redo ? describeTransition(s.prev, plan.tree) : describeTransition(plan.tree, s.prev);
    s.intent = redo ? "redo" : "undo";
    s.lastEdit = null;
    // The state being displaced keeps the cursor it has right now (read
    // before the restore re-syncs the editor's document).
    s.displaced = caretOf(s, o.editingId, o);
    const caret = inEditor && entry.caret && hasBlock(plan.tree, entry.caret.id) ? entry.caret : null;
    o.setBlocks(plan.tree);
    o.onEditing?.(caret?.id || null);
    if (caret) o.onCaret?.(caret);
    return { description, kept: plan.held };
  }
}

// Options:
//   originOf(tree) — "load" for a tree fetched from the server, "remote" for
//                 one with another client's ops applied, "fold" for an edit
//                 that joins the entry before it, else undefined
//   pageId      — the stack is cleared when it changes
//   enabled     — false while read-only / no page
//   caretRef    — {id, from, to} the open editor's live selection (App keeps
//                 it current from the editor's selection/change events)
//   caretBeforeRef — {id, from, to} the last editor change reported as its
//                 pre-change selection (App sets it in onChangeText)
//   onCaret({id, from, to}) — called after a restore that should land the
//                 cursor in the (kept-open) editor of that block
//   editingId   — the block whose editor is open right now (null: none)
//   onEditing(id) — a restore says which editor should be open afterwards
//                 (null: none)
export function useBlockHistory(blocks, setBlocks, opts) {
  const st = useRef(null);
  if (!st.current) st.current = createHistory(blocks);
  const optsRef = useRef({ setBlocks, ...opts });
  optsRef.current = { setBlocks, ...opts };
  const { pageId, editingId = null } = opts;

  const clear = useCallback(() => clearHistory(st.current), []);
  useEffect(clear, [pageId, clear]);

  // Must run before the autosave effect forgets the tree's origin (effects
  // run in declaration order — call this hook before that effect).
  useEffect(() => { observeTree(st.current, blocks, optsRef.current, editingId); }, [blocks, editingId]);

  // Stable, so a once-mounted key listener can call it (`undoStep`).
  const undo = useCallback((redo = false, inEditor = false) => {
    const o = optsRef.current;
    if (!o.enabled) return false;
    return undoStep(st.current, o, redo, inEditor);
  }, []);

  // Another client's ops landed (`rebaseHistory`): {pageId, pos, before}.
  const rebase = useCallback((ops, where) => rebaseHistory(st.current, ops, where), []);

  return { undo, clear, rebase };
}
