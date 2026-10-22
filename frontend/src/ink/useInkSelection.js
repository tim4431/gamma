// The lasso selection and the handwriting clipboard (docs/dev/handwriting.md
// "Client"): the selection, {page, items}, which belongs to the lasso;
// its edits, each one applyInk across its groups (a move, Delete, the
// selection menu's actions); copy, cut and paste through the clipboard,
// a ref a paste reads with the system clipboard written beside it;
// Ctrl+D, select all and the ink half of Transcribe. App composes it
// after the strokes.
import { useCallback, useEffect, useRef, useState } from "react";
import { guideEvents } from "../guide/events.js";
import { t, T, tn } from "../shared/i18n/i18n.js";
import { makeId } from "../shared/lib/utils";
import { findBlock } from "../shared/model/blockModel.js";
import { restyleBrush } from "./finishStroke.js";   // the selection's brush change, which bakes the widths again
import { MAX_STROKES, appendStrokes, boundsOf, cloneStrokes, duplicateStrokes, flipStrokes, removeStrokes, restyleStrokes, transformStrokes, translateStrokes, unionBox } from "./ink.js";
import * as inkStore from "./inkStore";
import { reshapeInk } from "./shapes.js";   // a shape handle's drag, the stroke written again from its params
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
    const changes = [];
    for (const item of inkSelection.items) {
      const before = inkOf(item.id);
      if (!before) continue;
      const after = edit(before, item.ids);
      if (after !== before) changes.push({ id: item.id, page: inkSelection.page, before, after });
    }
    applyInk(changes, { label });
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
    const items = (selection?.items || []).map((item) => {
      const have = new Set((inkOf(item.id)?.strokes || []).map((s) => s.id));
      return { ...item, ids: item.ids.filter((id) => have.has(id)) };
    }).filter((item) => item.ids.length);
    return items.length ? { ...selection, items } : null;
  }
  function handleInkAction(action, value) {
    if (readOnly || !inkSelection) return;
    // A brush change bakes the selected pens' widths again (null: the plain
    // pen); a line change sets or clears their dash; a fill sets or clears
    // the closed shapes' (null: none); else a colour or a width. A shape
    // handle's drag writes its stroke again from the new params.
    if (action === "style" && "brush" in value) editInkSelection((ink, ids) => restyleBrush(ink, ids, value.brush), T("ink style change"));
    else if (action === "style" && "dash" in value) editInkSelection((ink, ids) => restyleStrokes(ink, ids, { dash: value.dash }), T("ink line change"));
    else if (action === "style" && "fill" in value) editInkSelection((ink, ids) => restyleStrokes(ink, ids, { fill: value.fill }), T("ink fill change"));
    else if (action === "reshape") {
      editInkSelection((ink, ids) => (ids.includes(value?.id) ? reshapeInk(ink, value.id, value.params) : ink), T("ink shape edit"));
    }
    else if (action === "style") editInkSelection((ink, ids) => restyleStrokes(ink, ids, value), value.color ? t("ink color change") : t("ink width change"));
    else if (action === "transform") editInkSelection((ink, ids) => transformStrokes(ink, ids, value), value.angle ? t("ink rotation") : t("ink resize"));
    else if (action === "delete") deleteInkSelection();
    else if (action === "select-note") {
      handleInkSelect(inkSelection.page, inkSelection.items.map((item) => ({
        id: item.id, ids: (inkOf(item.id)?.strokes || []).map((s) => s.id),
      })));
    } else if (action === "show-note") {
      showInNotes(inkSelection.items[0].id);
      setInkSelection(null);
    } else if (action === "duplicate") {
      const changes = [], items = [];
      for (const item of inkSelection.items) {
        const before = inkOf(item.id);
        if (!before) continue;
        const count = before.strokes.filter((s) => item.ids.includes(s.id)).length;
        if (before.strokes.length + count > MAX_STROKES) {
          setStatus(t("This handwriting note is full. Start a new note before duplicating."));
          return;
        }
        const result = duplicateStrokes(before, item.ids, value.dx, value.dy);
        if (!result.ids.length) continue;
        changes.push({ id: item.id, page: inkSelection.page, before, after: result.ink });
        items.push({ id: item.id, ids: result.ids });
      }
      applyInk(changes, { label: T("ink duplication") });
      handleInkSelect(inkSelection.page, items);
    } else if (action === "copy") copyInkSelection(false);
    else if (action === "cut") copyInkSelection(true);
    else if (action === "flip") {
      const sel = selectedInk();
      if (!sel) return;
      const cx = (sel.box[0] + sel.box[2]) / 2, cy = (sel.box[1] + sel.box[3]) / 2;
      editInkSelection((ink, ids) => flipStrokes(ink, ids, { cx, cy, axis: value?.axis }), T("ink flip"));
    } else if (action === "transcribe") transcribeInkSelection();
    else if (action === "split") splitInkSelection();
    else if (action === "merge") mergeInkSelection();
  }
  // The selected strokes as the groups hold them, in selection order, with
  // their union box and the first group's space; null with none.
  function selectedInk() {
    if (!inkSelection) return null;
    const strokes = [];
    let box = null, space = null;
    for (const item of inkSelection.items) {
      const ink = inkOf(item.id);
      if (!ink) continue;
      const want = new Set(item.ids);
      const mine = ink.strokes.filter((s) => want.has(s.id));
      if (!mine.length) continue;
      strokes.push(...mine);
      box = unionBox(box, boundsOf(ink, item.ids));
      space = space || ink.space;
    }
    return strokes.length ? { strokes, box, space } : null;
  }
  // Copy (Ctrl+C, the menu) keeps the selection on the handwriting
  // clipboard; cut (Ctrl+X) deletes it as well, the entry "ink cut". The
  // pill offers Paste, the way to paste without a keyboard.
  function copyInkSelection(cut) {
    if (readOnly) return false;
    const sel = selectedInk();
    if (!sel) return false;
    const [x1, y1, x2, y2] = sel.box;
    const clip = { type: "gamma/ink", version: 2, space: sel.space, box: { x1, y1, x2, y2 }, strokes: sel.strokes };
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
    const { box } = clip, w = box.x2 - box.x1, h = box.y2 - box.y1;
    const x = Math.max(0, Math.min(size.width - w, at.x)), y = Math.max(0, Math.min(size.height - h, at.y));
    const landed = await handleInkStrokes(page, cloneStrokes(clip.strokes, x - box.x1, y - box.y1), size);
    if (!landed) return;
    pickInkTool("select");
    handleInkSelect(page, [{ id: landed.id, ids: landed.ids }]);
  }
  // Ctrl+V, the pill's Paste and More → Paste handwriting: the copied box
  // centred on the surface in view.
  function pasteAtCentre() {
    const target = pasteTarget(), clip = inkClipRef.current;
    if (!target || !clip) return;
    const { page, size } = target, w = clip.box.x2 - clip.box.x1, h = clip.box.y2 - clip.box.y1;
    pasteInk(page, { x: (size.width - w) / 2, y: (size.height - h) / 2 }, size);
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
    const items = (inkBySurface.get(page) || [])
      .map((b) => ({ id: b.id, ids: (inkOf(b.id)?.strokes || []).map((s) => s.id) })).filter((item) => item.ids.length);
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
    const groups = sel.items.map((item) => ({ id: item.id, want: new Set(item.ids), ink: inkOf(item.id) })).filter((g) => g.ink);
    const whole = groups.length && groups.every(({ want, ink }) => ink.strokes.length === want.size && ink.strokes.every((s) => want.has(s.id)));
    if (whole) {
      await flushInk();
      const unsaved = new Set(inkStore.dirtyDrafts().map((d) => d.id));
      const blocks = groups.map(({ id }) => findBlock(blocksRef.current, id));
      if (blocks.every(Boolean) && !groups.some(({ id }) => unsaved.has(id))) {
        transcribe({ blocks });
        return;
      }
    }
    const chips = groups.map(({ id, want, ink }) => {
      const strokes = ink.strokes.filter((s) => want.has(s.id));
      return { kind: "ink", id, page_id: focusedBlockId, space: ink.space, strokes,
        text: tn("Selected handwriting · {n} stroke", "Selected handwriting · {n} strokes", strokes.length) };
    }).filter((c) => c.strokes.length);
    if (!chips.length) return;
    transcribe({ chips });
  }
  // New note from selection: the selected strokes leave their groups for a
  // new ink block on the same surface, ids kept, as one entry across the
  // groups and the new block (applyInk inserts it; undo empties it again,
  // and flushInk deletes the empty block). A selection that already is a
  // whole note of its own is left as it is.
  function splitInkSelection() {
    const sel = selectedInk();
    if (!sel) return;
    const page = inkSelection.page, changes = [];
    for (const item of inkSelection.items) {
      const before = inkOf(item.id);
      if (!before) continue;
      const after = removeStrokes(before, item.ids);
      if (after.strokes.length !== before.strokes.length) changes.push({ id: item.id, page, before, after });
    }
    if (!changes.length) return;
    if (changes.length === 1 && !changes[0].after.strokes.length) { setStatus(t("This handwriting is already a note of its own.")); return; }
    const id = makeId(), src = changes[0].before;
    changes.push({ id, page, before: { ...src, strokes: [] }, after: { ...src, strokes: sel.strokes } });
    applyInk(changes, { label: T("ink split") });
    handleInkSelect(page, [{ id, ids: sel.strokes.map((s) => s.id) }]);
  }
  // Merge into note: the selection's whole group appended to the surface's
  // previous group (the nearest before it in page order, else the first
  // after it) and this one emptied, one entry; a group whose block holds a
  // caption or notes is refused, since its block would stay behind empty.
  function mergeInkSelection() {
    const sel = selectedInk();
    if (!sel || inkSelection.items.length !== 1) return;
    const page = inkSelection.page, from = inkSelection.items[0].id, source = inkOf(from);
    if (!source || source.strokes.length !== sel.strokes.length) return;
    const block = findBlock(blocksRef.current, from);
    if ((block?.content || "").trim() || block?.children?.length) {
      setStatus(t("Not merged: this note has a caption or notes under it."));
      return;
    }
    const groups = (inkBySurface.get(page) || []).map((b) => b.id).filter((gid) => inkOf(gid)?.strokes?.length);
    const at = groups.indexOf(from);
    const targetId = at > 0 ? groups[at - 1] : groups.find((gid) => gid !== from);
    const target = targetId ? inkOf(targetId) : null;
    if (!target) return;
    if (target.strokes.length + sel.strokes.length > MAX_STROKES) { setStatus(t("This handwriting note is full. Move part of it to a new note, or start a new one.")); return; }
    const { ink: after, ids } = appendStrokes(target, sel.strokes);
    applyInk([{ id: targetId, page, before: target, after }, { id: from, page, before: source, after: { ...source, strokes: [] } }],
      { label: T("ink merge") });
    handleInkSelect(page, [{ id: targetId, ids }]);
  }
  return { inkSelection, inkClip, clearInkSelection, restoreInkSelection, handleInkSelect, handleInkMoveSelection,
    deleteInkSelection, selectionStill, handleInkAction, copyInkSelection, pasteInk, pasteAtCentre,
    duplicateInkSelection, selectAllInk };
}
