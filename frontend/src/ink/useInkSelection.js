// The lasso selection and the handwriting clipboard (docs/dev/handwriting.md
// "Client"): the selection, {page, items}, which belongs to the lasso;
// its edits, each one applyInk across its groups (a move, Delete, the
// selection menu's actions); copy, cut and paste through the clipboard,
// a ref a paste reads with the system clipboard written beside it;
// Ctrl+D, select all and the ink half of Transcribe. Their pure parts are
// selectionEdits.js. App composes it after the strokes.
import { useCallback, useEffect, useRef, useState } from "react";
import { guideEvents } from "../guide/events.js";
import { t, T, tn } from "../shared/i18n/i18n.js";
import { makeId } from "../shared/lib/utils";
import { findBlock } from "../shared/model/blockModel.js";
import { cloneStrokes, removeStrokes, translateStrokes } from "./ink.js";
import * as inkStore from "./inkStore";
import {
  actionEdit, centredOn, clipOf, duplicatePlan, inkChips, mergePlan, pasteShift, selectAllItems, selectedStrokes, selectionChanges,
  selectionStill as stillOf, splitPlan, transcribeGroups,
} from "./selectionEdits.js";
import { duplicateOffset } from "./surfaceScale.js";   // Duplicate's 12 css px, as the selection's surface measures them

// The system clipboard's copy of the handwriting clipboard, best effort
// (docs/dev/handwriting.md "Copy, cut and paste"): text/html a <div
// data-gamma-ink> whose text is the gamma/ink JSON, text/plain the JSON. A
// browser that refuses the write (no permission, no focus, plain http)
// loses nothing: the hook's inkClipRef is what a paste reads.
function writeInkClipboard(clip) {
  try {
    if (!navigator.clipboard?.write || typeof ClipboardItem === "undefined") return;
    const json = JSON.stringify(clip);
    const div = document.createElement("div");
    div.setAttribute("data-gamma-ink", "");
    div.textContent = json;
    navigator.clipboard.write([new ClipboardItem({
      "text/html": new Blob([div.outerHTML], { type: "text/html" }),
      "text/plain": new Blob([json], { type: "text/plain" }),
    })]).catch(() => {});
  } catch { /* the ref stands */ }
}

// focusedBlockId: the open page; leaving it drops the selection.
// readOnly: nothing is selected or edited. inkUi: the strip's state
// (useInkStrip.js); a tool other than the lasso drops the selection.
// blocksRef: the page's tree, read when an action runs. inkOf, applyInk:
// a group's strokes now, and the edit (useInkDocument.js). flushInk: the
// save (useInkSave.js), awaited before whole groups are transcribed.
// handleInkStrokes: a paste's strokes into a group (useInkStrokes.js).
// pickInkTool, revealInkStrip(): the strip's (the lasso armed; the strip
// shown with its row closed). inkBySurface, pasteTarget(): the page's
// groups by surface, and the surface in view (useInkSurfaces.js).
// onBoxDeselect(): the text boxes let go of theirs. showInNotes(id):
// App's. transcribe({blocks} | {chips}): the chat half of Transcribe
// (App's askToTranscribe), null in a shared page's view. setStatus: the
// status pill.
export function useInkSelection({ focusedBlockId, readOnly, inkUi, blocksRef, inkOf, applyInk, flushInk,
  handleInkStrokes, pickInkTool, revealInkStrip, inkBySurface, pasteTarget, onBoxDeselect, showInNotes,
  transcribe, setStatus }) {
  // The lasso selection {page, items}.
  const [inkSelection, setInkSelection] = useState(null);
  // The handwriting clipboard (docs/dev/handwriting.md "Copy, cut and
  // paste"): {type: "gamma/ink", version: 2, space, box: {x1, y1, x2, y2},
  // strokes} — the file's space, the selection's box in points and the raw
  // stroke objects. The ref is the one source a paste reads (a ClipboardItem
  // is written beside it, best effort, for other apps); `inkClip` mirrors
  // whether it holds strokes, for the command guards and the layers.
  const inkClipRef = useRef(null);
  const [inkClip, setInkClip] = useState(false);
  // The selection's commands for App and the other owners: let go of it
  // (an undo, a box selected, Play, Esc), or put back one kept aside
  // (the end of a held E or squeeze, as selectionStill leaves it).
  const clearInkSelection = useCallback(() => setInkSelection(null), []);
  const restoreInkSelection = useCallback((sel) => setInkSelection(sel), []);
  // Leaving a page drops the selection.
  useEffect(() => () => { setInkSelection(null); }, [focusedBlockId]);
  // A selection belongs to the lasso tool: switching away drops it.
  useEffect(() => { if (inkUi.tool !== "select") setInkSelection(null); }, [inkUi.tool]);
  function handleInkSelect(page, items) {
    if (readOnly) return;
    setInkSelection(items.length ? { page, items } : null);
    if (items.length) {
      revealInkStrip();
      onBoxDeselect();
      guideEvents.emit("ink.selected");
    }
  }
  // The lasso selection, edited group by group: edit(ink, ids) -> ink.
  function editInkSelection(edit, label) {
    if (readOnly || !inkSelection) return;
    applyInk(selectionChanges(inkSelection, inkOf, edit), { label });
  }
  function handleInkMoveSelection(page, dx, dy) {
    editInkSelection((ink, ids) => translateStrokes(ink, ids, dx, dy), t("ink move"));
  }
  function deleteInkSelection() {
    editInkSelection(removeStrokes, t("ink deletion"));
    setInkSelection(null);
  }
  // A selection kept aside while a held E or squeeze armed another tool,
  // as it stands now: without the strokes erased meanwhile, null when none
  // is left.
  function selectionStill(selection) {
    return stillOf(selection, inkOf);
  }
  function handleInkAction(action, value) {
    if (readOnly || !inkSelection) return;
    // A style, a shape handle's drag, a turn or a resize, and a flip about
    // the selection box's centre edit the selected strokes in place.
    const inPlace = actionEdit(action, value, action === "flip" ? selectedInk()?.box : null);
    if (inPlace) editInkSelection(inPlace.edit, inPlace.label);
    else if (action === "delete") deleteInkSelection();
    else if (action === "select-note") {
      handleInkSelect(inkSelection.page, inkSelection.items.map((item) => ({
        id: item.id, ids: (inkOf(item.id)?.strokes || []).map((s) => s.id),
      })));
    } else if (action === "show-note") {
      showInNotes(inkSelection.items[0].id);
      setInkSelection(null);
    } else if (action === "duplicate") {
      const plan = duplicatePlan(inkSelection, inkOf, value);
      if (plan.refused) {
        setStatus(plan.refused);
        return;
      }
      applyInk(plan.changes, { label: T("ink duplication") });
      handleInkSelect(inkSelection.page, plan.items);
    } else if (action === "copy") copyInkSelection(false);
    else if (action === "cut") copyInkSelection(true);
    else if (action === "transcribe") transcribeInkSelection();
    else if (action === "split") splitInkSelection();
    else if (action === "merge") mergeInkSelection();
  }
  // The selected strokes with their box and space, null with none.
  function selectedInk() {
    return selectedStrokes(inkSelection, inkOf);
  }
  // Copy (Ctrl+C, the menu) keeps the selection on the handwriting
  // clipboard; cut (Ctrl+X) deletes it as well, the entry "ink cut". The
  // pill offers Paste, the way to paste without a keyboard.
  function copyInkSelection(cut) {
    if (readOnly) return false;
    const sel = selectedInk();
    if (!sel) return false;
    const clip = clipOf(sel);
    inkClipRef.current = clip;
    setInkClip(true);
    writeInkClipboard(clip);
    const n = sel.strokes.length;
    if (cut) {
      editInkSelection(removeStrokes, T("ink cut"));
      setInkSelection(null);
    }
    setStatus(cut ? tn("Cut {n} stroke.", "Cut {n} strokes.", n) : tn("Copied {n} stroke.", "Copied {n} strokes.", n),
      { label: t("Paste"), run: () => pasteAtCentre() });
    return true;
  }
  // The clipboard's strokes onto `page` ({width, height} in points): fresh
  // copies with the copied box's top-left at `at` and the box kept inside
  // the surface (one too small for it keeps the top-left corner on), at
  // the same point size whatever the surface. They become the selection,
  // the lasso armed.
  async function pasteInk(page, at, size) {
    const clip = inkClipRef.current;
    if (!clip?.strokes?.length || readOnly || !size) return;
    const { dx, dy } = pasteShift(clip, at, size);
    const landed = await handleInkStrokes(page, cloneStrokes(clip.strokes, dx, dy), size);
    if (!landed) return;
    pickInkTool("select");
    handleInkSelect(page, [{ id: landed.id, ids: landed.ids }]);
  }
  // Ctrl+V, the pill's Paste and More → Paste handwriting: the copied box
  // centred on the surface in view.
  function pasteAtCentre() {
    const target = pasteTarget(), clip = inkClipRef.current;
    if (!target || !clip) return;
    const { page, size } = target;
    pasteInk(page, centredOn(clip, size), size);
  }
  // Ctrl+D: the menu's Duplicate, the copies the same 12 css px away, in
  // the points of the selection's own surface as its layer measures them.
  function duplicateInkSelection() {
    if (inkSelection) handleInkAction("duplicate", duplicateOffset(inkSelection.page));
  }
  // Ctrl+A, More → Select all: every stroke of every group on the
  // selection's surface, else the one in view, the lasso armed.
  function selectAllInk() {
    const page = inkSelection?.page ?? pasteTarget()?.page;
    if (page == null) return false;
    const items = selectAllItems(inkBySurface.get(page), inkOf);
    if (!items.length) return false;
    pickInkTool("select");
    handleInkSelect(page, items);
    return true;
  }
  // Transcribe (the selection menu's second row). A selection of whole
  // groups asks for their text as "Transcribe with AI" does: the page's
  // drafts are saved first (the server reads the stored files) and each
  // group's block rides as a chip, the oldest attached block making room.
  // Part of a group — or a group whose save has not landed — sends the
  // selected strokes themselves, as the drafts hold them: one chip per
  // group, the server's ink_selections (gamma/ai_context.py), within its
  // caps; past them nothing is sent. The chat half is `transcribe`'s
  // (App's askToTranscribe), which a shared page's view does not pass.
  async function transcribeInkSelection() {
    const sel = inkSelection;
    if (!sel || !focusedBlockId) return;
    if (!transcribe) { setStatus(t("Transcribing needs the AI chat, which a shared page cannot use.")); return; }
    const { groups, whole } = transcribeGroups(sel, inkOf);
    if (whole) {
      await flushInk();
      const unsaved = new Set(inkStore.dirtyDrafts().map((d) => d.id));
      const blocks = groups.map(({ id }) => findBlock(blocksRef.current, id));
      if (blocks.every(Boolean) && !groups.some(({ id }) => unsaved.has(id))) {
        transcribe({ blocks });
        return;
      }
    }
    const chips = inkChips(groups, focusedBlockId);
    if (!chips.length) return;
    transcribe({ chips });
  }
  // New note from selection: the selected strokes leave their groups for a
  // new ink block on the same surface, ids kept, as one entry across the
  // groups and the new block (applyInk inserts it; undo empties it again,
  // and flushInk deletes the empty block). A selection that already is a
  // whole note of its own is left as it is.
  function splitInkSelection() {
    const plan = splitPlan(inkSelection, inkOf, makeId);
    if (!plan) return;
    if (plan.refused) { setStatus(plan.refused); return; }
    applyInk(plan.changes, { label: T("ink split") });
    handleInkSelect(inkSelection.page, plan.items);
  }
  // Merge into note: the selection's whole group appended to the surface's
  // previous group (the nearest before it in page order, else the first
  // after it) and this one emptied, one entry; a group whose block holds a
  // caption or notes is refused, since its block would stay behind empty.
  function mergeInkSelection() {
    const plan = mergePlan(inkSelection, inkOf, blocksRef.current, inkBySurface);
    if (!plan) return;
    if (plan.refused) { setStatus(plan.refused); return; }
    applyInk(plan.changes, { label: T("ink merge") });
    handleInkSelect(inkSelection.page, plan.items);
  }
  return { inkSelection, inkClip, clearInkSelection, restoreInkSelection, handleInkSelect, handleInkMoveSelection,
    deleteInkSelection, selectionStill, handleInkAction, copyInkSelection, pasteInk, pasteAtCentre,
    duplicateInkSelection, selectAllInk };
}
