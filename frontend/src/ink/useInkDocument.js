// A page's handwriting as a document (docs/dev/handwriting.md "Client"):
// every ink edit goes through applyInk, which keeps the byte budget, the
// drafts and the stroke history, and puts back a group's block when an
// edit brings its strokes back; undo and redo step that history onto
// the drawings as they are now; the eraser's sweeps and a scribble's
// erasures are edits, the flushes of one eraser pass folding into one
// entry; and a draft the journal restored gets its pill. Their pure parts
// are inkHistory.js (the history) and inkEdits.js (the budget, the blocks
// put back, the erasers' changes). App composes it after the surfaces.
import { useEffect, useRef, useState } from "react";
import { guideEvents } from "../guide/events.js";
import * as playback from "../replay/playback.js";
import { t, T, tn } from "../shared/i18n/i18n.js";
import { flattenBlocks } from "../shared/model/blockModel.js";
import { eraserKeep, mergeInk } from "./ink.js";
import { eraseAlongChanges, eraseManyChanges, missingGroups, overBudget, withGroupBlocks } from "./inkEdits.js";
import { foldInto, passFold, recordEntry, stepChanges, undoMessage } from "./inkHistory.js";
import * as inkStore from "./inkStore";

// focusedBlockId: the open page; leaving it empties the history.
// readOnly: nothing is edited. blocksRef: the page's tree, read when an
// edit runs; setBlocks: its writer, which puts back a group's block.
// inkBySurface, inkPageNumber: the page's groups by surface and what
// messages call a surface (useInkSurfaces.js). inkEraserFilter: the
// account's Erases (Settings › Handwriting). scheduleInk: the save
// (useInkSave.js). liveRemoved(changes): live co-drawing's drops
// (useInkLive.js). clearInkSelection(): the lasso's, which an undo
// clears. boxUndo, undoBlocks(redo): the block history has the strip's
// Undo (the Text tool or a text box), and its step (App's).
// sayUndoable(msg, run): App's pill with Undo. setStatus: the status
// pill. setInkFingerUndo: this browser's finger-undo preference, which
// the first chord's pill turns off.
export function useInkDocument({ focusedBlockId, readOnly, blocksRef, setBlocks, inkBySurface, inkPageNumber,
  inkEraserFilter, scheduleInk, liveRemoved, clearInkSelection, boxUndo, undoBlocks, sayUndoable,
  setStatus, setInkFingerUndo }) {
  // Stroke-level history for the strip's Ctrl+Z and Undo/Redo buttons
  // (entries: {changes: [{id, page, before, after}], label} per action;
  // inkHistoryState mirrors the lengths for the buttons).
  const inkHistRef = useRef({ undo: [], redo: [] });
  const [inkHistoryState, setInkHistoryState] = useState({ undo: 0, redo: 0 });
  // The strip's Undo and Redo buttons, and a Pencil gesture's Undo.
  const stripUndo = (redo) => (boxUndo ? undoBlocks(redo) : inkUndo(redo));
  // Leaving a page empties the history (the strip's buttons follow it).
  useEffect(() => () => {
    inkHistRef.current = { undo: [], redo: [] };
    setInkHistoryState({ undo: 0, redo: 0 });
  }, [focusedBlockId]);
  // The strokes a group has right now: its draft, else its loaded file.
  function inkOf(blockId) {
    const block = flattenBlocks(blocksRef.current).find((b) => b.id === blockId);
    return inkStore.draft(blockId)?.ink || (block ? inkStore.inkFor(block) : null);
  }
  // Every ink edit goes through here: the drafts change, the action lands
  // on the stroke history, the upload is scheduled. A group whose block is
  // not in the tree (undone away, or erased empty and deleted) gets its
  // block back first. `pass` marks the entry as an eraser pass's, which the
  // pass's later flushes fold into (handleInkEraseAlong). → whether the
  // edit applied (the byte budget may refuse it).
  function applyInk(changes, { record = true, label = t("ink stroke"), pass = null } = {}) {
    if (!changes.length) return false;
    // The byte budget: an edit that would take a group past the server's
    // cap is refused (one that shrinks a group over it is not).
    if (overBudget(changes)) {
      setStatus(t("This handwriting note is full. Move part of it to a new note, or start a new one."));
      return false;
    }
    for (const c of changes) playback.stop(c.id);
    const present = new Set(flattenBlocks(blocksRef.current).map((b) => b.id));
    const missing = missingGroups(changes, present);
    if (missing.length) {
      // A block coming back is fresh: no base for its draft (inkStore.dropDraft).
      for (const c of missing) inkStore.dropDraft(c.id);
      // A PDF page's group goes at the page's top level; a sheet's under
      // its sheet (`page` is then the sheet's id).
      setBlocks((prev) => withGroupBlocks(prev, missing));
    }
    const byId = new Map(flattenBlocks(blocksRef.current).map((b) => [b.id, b]));
    for (const c of changes) inkStore.setDraft(c.id, c.after, byId.get(c.id)?.properties?.ink_url || "");
    // a stroke sent live and taken away before any save held it: its wet stroke goes from the peers' pages
    liveRemoved(changes);
    if (record) {
      const h = inkHistRef.current;
      recordEntry(h, changes, { label, pass });
      setInkHistoryState({ undo: h.undo.length, redo: h.redo.length });
    }
    scheduleInk();
    return true;
  }
  function inkUndo(redo) {
    if (readOnly) return false;
    const h = inkHistRef.current;
    const entry = (redo ? h.redo : h.undo).pop();
    if (!entry) { setStatus(redo ? t("Nothing to redo in handwriting.") : t("Nothing to undo in handwriting.")); return false; }
    // The step lands on each group as it is now (inkHistory.js
    // stepChanges): strokes someone else drew meanwhile stay, and what
    // someone changed since is kept over it, which the pill says.
    const { changes, kept } = stepChanges(entry, redo, inkOf);
    applyInk(changes, { record: false });
    (redo ? h.undo : h.redo).push(entry);
    setInkHistoryState({ undo: h.undo.length, redo: h.redo.length });
    clearInkSelection();
    setStatus(undoMessage(entry, redo, kept, inkPageNumber(entry.changes[0].page)));
    if (!redo) guideEvents.emit("ink.undone");
    return true;
  }
  // A stroke edit's pill (a snapped shape, a scribble erase, a stamped
  // line): its Undo steps the stroke history, as the strip's Undo does.
  function undoableInkStatus(msg) {
    sayUndoable(msg, () => inkUndo(false));
  }
  // The eraser filter (Settings › Handwriting › Erases, the eraser row's
  // trio) is one predicate every erasure goes through here: the eraser's
  // sweep, whole strokes or partial, from the tool or the pen's eraser end
  // and barrel, and a scribble. The layer hands over where the eraser went
  // and a scribble's candidates; what goes is decided here.
  const inkKeep = eraserKeep(inkEraserFilter);
  // A scribble's targets ([{gid, ids}], ink/gestures.js scribbleTargets)
  // go as one entry across their groups, with Undo on the pill.
  function handleInkEraseMany(page, targets) {
    if (readOnly) return;
    const { changes, n } = eraseManyChanges(page, targets, inkOf, inkKeep);
    if (!changes.length) return;
    applyInk(changes, { label: T("ink scribble erase") });
    undoableInkStatus(tn("Erased {n} stroke.", "Erased {n} strokes.", n));
    guideEvents.emit("ink.erased");
  }
  // The eraser's sweep, which the layer hands over once an animation frame
  // and at the lift: `path` the page points it crossed since the last
  // flush, `r` its radius in points. Every group of the surface is cut along
  // it (`mode` 'partial', ink.js eraseAlong) or loses the strokes it touched
  // ('stroke', hitStrokesAlong), through the eraser filter, as one applyInk
  // a flush (inkEdits.js eraseAlongChanges). The flushes of one pass
  // (`pass`, minted at the down) fold into one history entry, across
  // groups, so Ctrl+Z undoes the whole pass (inkHistory.js passFold and
  // foldInto: a group changed meanwhile starts a new entry).
  function handleInkEraseAlong(page, path, r, { mode, pass } = {}) {
    if (readOnly || !path?.length) return;
    const partial = mode === "partial", changes = eraseAlongChanges(page, inkBySurface.get(page) || [], path, r, partial, inkOf, inkKeep);
    if (!changes.length) return;
    const h = inkHistRef.current, last = h.undo[h.undo.length - 1];
    const { fold, held } = passFold(h, changes, pass);
    if (!applyInk(changes, { record: !fold, label: partial ? T("partial ink erasure") : T("ink erasure"), pass })) return;
    if (fold) foldInto(last, changes, held);
    guideEvents.emit("ink.erased");
  }
  // Handwriting a closed tab left unsaved, restored from the draft journal
  // (ink/inkStore.js onInkRestored): its draft saves like an edit, and the
  // pill offers Discard — the restored change taken back (restored → the
  // file it was laid over, applied onto the group as it is now, as an undo
  // is) as one undoable entry. Only while the group is on this page.
  const inkRestoreRef = useRef(null);
  inkRestoreRef.current = {
    schedule: () => scheduleInk(),
    discard: (id, restored, prior) => {
      const block = flattenBlocks(blocksRef.current).find((b) => b.id === id);
      const now = block ? inkOf(id) : null;
      if (!now) return;
      const after = mergeInk(restored, prior, now).ink;
      if (after === now) return;
      const page = now.space?.kind === "canvas" ? block.parentId : now.space?.page;
      applyInk([{ id, page, before: now, after }], { label: T("ink restore discarded") });
    },
  };
  useEffect(() => inkStore.onInkRestored(({ id, ink, prior }) => {
    inkRestoreRef.current.schedule();
    setStatus(t("Unsaved handwriting from a previous visit was restored."),
      { label: t("Discard"), run: () => inkRestoreRef.current.discard(id, ink, prior) });
  }), [setStatus]);
  // Two fingers tapped together undo, three redo (ink/fingerChords.js):
  // exactly what the strip's buttons do — the block history while the Text
  // tool or a text box has it, else the stroke history; with nothing to
  // undo, nothing. The first chord of a session says so, with Turn off.
  const fingerUndoHintRef = useRef(true);
  function handleUndoGesture(kind) {
    if (readOnly) return;
    const redo = kind === "redo";
    if (boxUndo) { undoBlocks(redo); return; }
    const h = inkHistRef.current;
    if (!(redo ? h.redo : h.undo).length) return;
    if (!inkUndo(redo)) return;
    if (!fingerUndoHintRef.current) return;
    fingerUndoHintRef.current = false;
    setStatus(redo ? t("Three fingers redid a stroke.") : t("Two fingers undid a stroke."),
      { label: t("Turn off"), run: () => setInkFingerUndo("off") });
  }
  return { inkHistoryState, inkOf, applyInk, inkUndo, undoableInkStatus, stripUndo, handleUndoGesture,
    handleInkEraseMany, handleInkEraseAlong };
}
