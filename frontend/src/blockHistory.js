// Undo/redo for the page's block tree, derived from the tree's own state
// transitions — no call site has to declare "this edit is undoable".
//
// Every committed change to `blocks` is classified by diffing it against the
// previous committed tree:
//   - loads (the caller flagged the transition with `loadRef`, the same flag
//     that stops the autosave) and undo/redo applications are never recorded;
//   - opening/closing editors and collapse toggles are not edits;
//   - everything else (add/delete/move/indent, property changes such as a
//     highlight colour or link, any content change — typing, checkboxes,
//     table cells, image size) pushes the previous tree.
// Consecutive content-only edits of the same block merge into one entry:
// for as long as the block's editor stays open (one editing session = one
// entry, so leaving a block and pressing Ctrl+Z retracts what was typed
// there — inside the editor Ctrl+Z is still CodeMirror's fine-grained
// history), and otherwise within EDIT_MERGE_MS (a drag, a run of toggles).
//
// The block-tree helpers never mutate in place, so the previous tree is kept
// by reference; editMode is stripped when a snapshot is restored so undoing
// never pops editors open. The stack belongs to one page and is cleared when
// the page id changes.
import { useCallback, useEffect, useRef } from "react";

const MAX_ENTRIES = 100;
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
// a block id: only that block's content changed (outside an editor).
function classify(prev, next) {
  if (prev === next) return null;
  if (prev.length !== next.length) return true;
  let only = null;
  for (let i = 0; i < prev.length; i++) {
    const a = prev[i], b = next[i];
    if (a === b) continue;
    if (a.id !== b.id) return true;
    if (a.content !== b.content) only = only === null ? a.id : true;
    if (!propsEqual(a.properties, b.properties)) return true;
    const sub = classify(a.children || [], b.children || []);
    if (sub === true) return true;
    if (sub) only = only === null ? sub : true;
    if (only === true) return true;
  }
  return only;
}

function isEditing(list, id) {
  for (const b of list || []) {
    if (b.id === id) return !!b.editMode;
    if (isEditing(b.children, id)) return true;
  }
  return false;
}

function stripEditMode(list) {
  return (list || []).map((b) => ({ ...b, editMode: false, children: stripEditMode(b.children) }));
}

export function useBlockHistory(blocks, setBlocks, { loadRef, pageId, enabled }) {
  const st = useRef({ undo: [], redo: [], prev: blocks, intent: null, lastEdit: null });
  const liveRef = useRef({ setBlocks, enabled });
  liveRef.current = { setBlocks, enabled };

  const clear = useCallback(() => {
    st.current.undo = [];
    st.current.redo = [];
    st.current.lastEdit = null;
  }, []);
  useEffect(clear, [pageId, clear]);

  // Must run before the autosave effect consumes loadRef (effects run in
  // declaration order — call this hook right after the flag is declared).
  useEffect(() => {
    const s = st.current;
    const prev = s.prev;
    s.prev = blocks;
    const intent = s.intent;
    s.intent = null;
    if (loadRef.current || prev === blocks) return;
    if (intent === "undo") { s.redo.push(prev); return; }
    if (intent === "redo") { s.undo.push(prev); return; }
    const kind = classify(prev, blocks);
    if (kind === null) {
      // The editor of the block being merged into closed: the session ends.
      if (s.lastEdit?.editing && !isEditing(blocks, s.lastEdit.id)) s.lastEdit = null;
      return;
    }
    const now = Date.now();
    if (kind !== true && s.lastEdit?.id === kind) {
      const editing = isEditing(blocks, kind);
      if ((s.lastEdit.editing && editing) || now - s.lastEdit.at < EDIT_MERGE_MS) {
        s.lastEdit.at = now;
        s.lastEdit.editing = editing;
        return;
      }
    }
    s.lastEdit = kind === true ? null : { id: kind, at: now, editing: isEditing(blocks, kind) };
    s.undo.push(prev);
    if (s.undo.length > MAX_ENTRIES) s.undo.shift();
    s.redo = [];
  }, [blocks, loadRef]);

  // Stable, so a once-mounted key listener can call it. Returns whether a
  // snapshot was applied.
  const undo = useCallback((redo = false) => {
    if (!liveRef.current.enabled) return false;
    const s = st.current;
    const snap = (redo ? s.redo : s.undo).pop();
    if (!snap) return false;
    s.intent = redo ? "redo" : "undo";
    s.lastEdit = null;
    liveRef.current.setBlocks(stripEditMode(snap));
    return true;
  }, []);

  return { undo, clear };
}
