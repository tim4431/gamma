// The lasso selection and the handwriting clipboard (docs/dev/handwriting.md
// "Client"): the selection, {page, items, pictures?}, which belongs to the
// lasso; its edits, each one applyInk across its groups (a move, Delete,
// the selection menu's actions), and those that take its pictures one step
// with their half in the block history (docs/dev/pictures.md "The lasso");
// copy, cut and paste of the strokes and the pictures through the
// clipboard, a ref a paste reads with the system clipboard written beside
// it; Ctrl+D, select all, the ink half of Transcribe, and the Convert
// menu's others: Redraw as shapes and Convert to text, which ask the AI
// and apply its answer, and Copy as SVG and Download SVG
// (docs/dev/handwriting_ai.md). Their pure parts are selectionEdits.js and
// inkAi.js. App composes it after the strokes.
import { useCallback, useEffect, useRef, useState } from "react";
import { guideEvents } from "../guide/events.js";
import { duplicatePicture, movePicture, picturesBySurface } from "../markup/picture.js";
import { addPlaced } from "../markup/placed.js";
import { t, T, tn } from "../shared/i18n/i18n.js";
import { copySvg } from "../shared/lib/copyImage.js";
import { saveBlob } from "../shared/lib/saveFile.js";
import { normalizePaper, paperIsDark, sheetOfBlock } from "../notebook/notebook.js";
import { findBlock, makeId, setBlockText } from "../shared/model/blockModel.js";
import { wallNow } from "../shared/model/wallClock.js";
import { cloneStrokes, removeStrokes, translateStrokes } from "./ink.js";
import { MAX_REDRAW_STROKES, inkAiRequest, inkInTree, penOnly, redrawPlan, requestTooBig, textBoxFor, textPlan } from "./inkAi.js";
import { askInkAi } from "./inkAiClient.js";
import { inkSvg } from "./inkSvg.js";
import * as inkStore from "./inkStore";
import {
  actionEdit, centredOn, clipOf, clipStatus, duplicatePlan, editPictures, inkChips, mergePlan, pasteShift, pastePictures, removePictures,
  selectAllItems, selectAllPictures, selectedPictures, selectedStrokes, selectionBox, selectionChanges, selectionStill as stillOf, splitPlan,
  transcribeGroups, wholeGroup,
} from "./selectionEdits.js";
// Duplicate's 12 css px, as the selection's surface measures them, and the
// surface's size, which the pictures an edit carries stay on
import { duplicateOffset, surfaceSize } from "./surfaceScale.js";

// How long the AI's wash stays once its answer lands: the fade's 0.6 s
// (app.css .inkAiWork.landed) and a little.
const LANDED_MS = 700;

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
// lassoPictures: whether the lasso takes pictures (its row's Pictures),
// which select all honours.
// blocksRef: the page's tree, read when an action runs. inkOf, applyInk:
// a group's strokes now, and the edit (useInkDocument.js). flushInk: the
// save (useInkSave.js), awaited before whole groups are transcribed.
// handleInkStrokes: a paste's strokes into a group (useInkStrokes.js).
// pickInkTool, revealInkStrip(): the strip's (the lasso armed; the strip
// shown with its row closed). inkBySurface, pasteTarget(): the page's
// groups by surface, and the surface in view (useInkSurfaces.js).
// onBoxDeselect(): the text boxes let go of theirs. showInNotes(id):
// App's. transcribe({blocks} | {chips}): the chat half of Transcribe
// (App's askToTranscribe), null in a shared page's view. ai {model,
// addTask, updateTask, pageTitle}: what Redraw as shapes and Convert to
// text ask with — the model the chat sends (App's chatSendModel), the
// background tasks' rows (tasks/useTasks.js addLocal, updateLocal) and the
// page's title, which names a row — null in a shared page's view, which
// has no AI. stepBlocks(link, fn): App's tree edit
// fn(tree) → tree that is the block history's half of a step, linked to
// the stroke history's entry by `link`. touchTextBox(id): the text boxes'
// touch, a box measured in the commit that shows it (Convert to text's).
// setStatus: the status pill; undoableInkStatus(msg): the pill with Undo
// on it, which steps the stroke history (useInkDocument.js).
// onComment(groupId): a new comment thread on that group, the menu's
// Comment; threaded(groupId): the group has a thread, so no merge takes
// its strokes away nor adds to them (comments/useInkThreads.js).
export function useInkSelection({ focusedBlockId, readOnly, inkUi, lassoPictures, blocksRef, inkOf, applyInk, flushInk,
  handleInkStrokes, pickInkTool, revealInkStrip, inkBySurface, pasteTarget, onBoxDeselect, showInNotes,
  transcribe, ai, stepBlocks, touchTextBox, setStatus, undoableInkStatus, onComment, threaded = () => false }) {
  // The lasso selection {page, items, pictures?}.
  const [inkSelection, setInkSelection] = useState(null);
  // The handwriting clipboard (docs/dev/handwriting.md "Copy, cut and
  // paste"): {type: "gamma/ink", version: 2, space, box: {x1, y1, x2, y2},
  // strokes, pictures?} — the file's space, the selection's box in points,
  // the raw stroke objects and the pictures with their captions
  // (selectionEdits.js clipOf). The ref is the one source a paste reads (a
  // ClipboardItem is written beside it, best effort, for other apps); it
  // lives as long as the tab, and a switch of workspace reloads the tab,
  // so a paste is always in the workspace whose uploads its pictures name.
  // `inkClip` mirrors whether it holds anything, for the command guards
  // and the layers.
  const inkClipRef = useRef(null);
  const [inkClip, setInkClip] = useState(false);
  // The selection's commands for App and the other owners: let go of it
  // (an undo, a box selected, Play, Esc), or put back one kept aside
  // (the end of a held E or squeeze, as selectionStill leaves it).
  const clearInkSelection = useCallback(() => setInkSelection(null), []);
  const restoreInkSelection = useCallback((sel) => setInkSelection(sel), []);
  // The AI request in flight (Redraw as shapes, Convert to text), {ctl,
  // task}: one at a time in a page view, its AbortController and its task
  // row. An answer is applied seconds after it was asked for, with the
  // hook's inputs as they are then, which `live` holds (the closure that
  // asked holds the render it began in).
  const aiRef = useRef(null);
  // What the layers wash while the AI works ({page, box, landed?}: the
  // request's surface and its pen strokes' box, InkLayer.jsx InkAiWork):
  // set while the request is out, `landed` for LANDED_MS once its answer
  // is applied, null otherwise.
  const [inkAiWork, setInkAiWork] = useState(null);
  const live = useRef(null);
  live.current = { focusedBlockId, readOnly, inkSelection, inkOf, applyInk, stepBlocks, touchTextBox, handleInkSelect,
    setStatus, undoableInkStatus };
  // Leaving a page drops the selection, and stops the AI request asked
  // there: its answer would be the other page's.
  useEffect(() => () => {
    setInkSelection(null);
    aiRef.current?.ctl.abort();
    aiRef.current = null;
    setInkAiWork(null);
  }, [focusedBlockId]);
  // A selection belongs to the lasso tool: switching away drops it.
  useEffect(() => { if (inkUi.tool !== "select") setInkSelection(null); }, [inkUi.tool]);
  // `items` the strokes, `pictures` the picture blocks the lasso took.
  function handleInkSelect(page, items, pictures = []) {
    if (readOnly) return;
    const any = items.length > 0 || pictures.length > 0;
    setInkSelection(any ? { page, items, ...(pictures.length ? { pictures } : {}) } : null);
    if (any) {
      revealInkStrip();
      onBoxDeselect();
      guideEvents.emit("ink.selected");
    }
  }
  // The picture blocks the selection's surface draws (picturesBySurface).
  function surfacePictures() {
    return picturesBySurface(blocksRef.current).get(inkSelection.page);
  }
  // The selection's pictures an edit takes now (selectedPictures: those its
  // surface still draws, none locked since), and the size of that surface,
  // which keeps them on it (surfaceSize). None while no layer shows it.
  function takenPictures() {
    const surface = surfaceSize(inkSelection.page);
    return { surface, ids: surface ? selectedPictures(inkSelection, surfacePictures()).map((p) => p.id) : [] };
  }
  // A step of the selection: `changes` to its strokes (applyInk) and
  // blocks(tree) → tree, its pictures' half. That half goes to the block
  // history (stepBlocks), linked to the stroke history's entry, so the two
  // undo as one step. `label` names a step of strokes alone, `both` one that
  // changed pictures too. → whether it applied (the byte budget may refuse
  // it).
  function stepSelection(changes, label, both = label, blocks = null) {
    const tree = blocksRef.current;
    const link = blocks && blocks(tree) !== tree ? { id: makeId(), page: inkSelection.page } : null;
    if (!applyInk(changes, { label: link ? both : label, link })) return false;
    if (link) stepBlocks(link.id, blocks);
    return true;
  }
  // The lasso selection edited in place: its strokes group by group
  // (edit(ink, ids) → ink) and, with `pictures` {label, each(pic, surface)
  // → pic}, its pictures, kept on its surface, the step then named
  // `pictures.label`.
  function editInkSelection(edit, label, pictures = null) {
    if (readOnly || !inkSelection) return;
    const { surface, ids } = pictures ? takenPictures() : { ids: [] };
    stepSelection(selectionChanges(inkSelection, inkOf, edit), label, pictures?.label,
      ids.length ? (tree) => editPictures(tree, ids, (pic) => pictures.each(pic, surface)) : null);
  }
  function handleInkMoveSelection(page, dx, dy) {
    editInkSelection((ink, ids) => translateStrokes(ink, ids, dx, dy), t("ink move"),
      { label: T("selection move"), each: (pic, surface) => movePicture(pic, dx, dy, surface) });
  }
  // Delete (the menu's, the strip's key): the strokes and the pictures.
  function deleteInkSelection() {
    removeInkSelection(t("ink deletion"), T("selection deletion"));
  }
  // The selection taken off the page as one step, `label` its entry's
  // label, or `both` when it held pictures: Delete's and Cut's, which
  // keeps the notes under the pictures (`keepNotes`, removePictures). A
  // removal needs no surface size, so it takes the pictures a cut copies
  // (clipOf) even while no layer shows their surface.
  function removeInkSelection(label, both, keepNotes = false) {
    if (readOnly || !inkSelection) return;
    const ids = selectedPictures(inkSelection, surfacePictures()).map((p) => p.id);
    stepSelection(selectionChanges(inkSelection, inkOf, removeStrokes), label, both,
      ids.length ? (tree) => removePictures(tree, ids, keepNotes) : null);
    setInkSelection(null);
  }
  // A selection kept aside while a held E or squeeze armed another tool,
  // as it stands now: without the strokes erased meanwhile, null when
  // nothing is left.
  function selectionStill(selection) {
    return stillOf(selection, inkOf);
  }
  // The selection menu's actions. A turn, a resize, a flip, Delete,
  // Duplicate, Copy and Cut take the selection's pictures too. The rest act
  // on its strokes alone: Select note, New note and Merge into note keep
  // them selected beside them.
  function handleInkAction(action, value) {
    if (readOnly || !inkSelection) return;
    // A style, a shape handle's drag, a turn or a resize, and a flip about
    // the selection box's centre edit the selection in place.
    const inPlace = actionEdit(action, value, action === "flip" ? selectionBox(inkSelection, inkOf, surfacePictures()) : null);
    if (inPlace) editInkSelection(inPlace.edit, inPlace.label, inPlace.pictures);
    else if (action === "delete") deleteInkSelection();
    else if (action === "select-note") {
      handleInkSelect(inkSelection.page, inkSelection.items.map((item) => ({
        id: item.id, ids: (inkOf(item.id)?.strokes || []).map((s) => s.id),
      })), inkSelection.pictures);
    } else if (action === "show-note") {
      showInNotes(inkSelection.items[0]?.id ?? inkSelection.pictures[0]);
      setInkSelection(null);
    } else if (action === "duplicate") {
      const plan = duplicatePlan(inkSelection, inkOf, value);
      if (plan.refused) {
        setStatus(plan.refused);
        return;
      }
      // copies of the pictures too, as far off as the strokes', each just
      // over its original (duplicatePicture)
      const { surface, ids } = takenPictures();
      const copies = ids.map(() => makeId()), t0 = wallNow();
      const place = (pic) => movePicture(pic, value.dx, value.dy, surface);
      // the copies become the selection once they are on the page (the
      // byte budget may refuse them, leaving the selection as it was)
      if (stepSelection(plan.changes, T("ink duplication"), T("selection duplication"),
        ids.length ? (tree) => ids.reduce((out, id, i) => duplicatePicture(out, id, copies[i], place, t0), tree) : null)) {
        handleInkSelect(inkSelection.page, plan.items, copies);
      }
    } else if (action === "copy") copyInkSelection(false);
    else if (action === "cut") copyInkSelection(true);
    else if (action === "transcribe") transcribeInkSelection();
    else if (action === "redraw") askAboutSelection("shapes");
    else if (action === "to-text") askAboutSelection("text");
    else if (action === "copy-svg") copySvgSelection();
    else if (action === "download-svg") downloadSvgSelection();
    else if (action === "split") splitInkSelection();
    else if (action === "merge") mergeInkSelection();
    else if (action === "comment") commentInkSelection();
  }
  // Copy (Ctrl+C, the menu) keeps the selection, its strokes and its
  // pictures, on the handwriting clipboard; cut (Ctrl+X) deletes it as
  // well, one step as Delete's is, "ink cut" or "selection cut". The pill
  // offers Paste, the way to paste without a keyboard.
  function copyInkSelection(cut) {
    if (readOnly || !inkSelection) return false;
    const clip = clipOf(inkSelection, inkOf, surfacePictures());
    if (!clip) return false;
    inkClipRef.current = clip;
    setInkClip(true);
    writeInkClipboard(clip);
    if (cut) removeInkSelection(T("ink cut"), T("selection cut"), true);
    setStatus(clipStatus(clip, cut), { label: t("Paste"), run: () => pasteAtCentre() });
    return true;
  }
  // The clipboard onto `page` ({width, height} in points): fresh copies of
  // its strokes with the copied box's top-left at `at` and the box kept
  // inside the surface (one too small for it keeps the top-left corner
  // on), at the same point size whatever the surface. Its pictures come as
  // new blocks naming the same uploads, as far off as the strokes
  // (pastePictures), one step of both histories with them as a lasso
  // step's is: "selection paste", else "ink paste". The link is made only
  // when the pictures land on the tree as it is (a sheet gone takes none),
  // as stepSelection's is. They become the selection, the lasso armed.
  async function pasteInk(page, at, size) {
    const clip = inkClipRef.current;
    if (!clip || readOnly || !size) return;
    const shift = pasteShift(clip, at, size), t0 = wallNow();
    const ids = (clip.pictures || []).map(() => makeId()), now = blocksRef.current;
    const blocks = (tree) => pastePictures(tree, clip, page, ids, shift, size, t0);
    const pictures = ids.length && blocks(now) !== now ? ids : [];
    const link = pictures.length ? { id: makeId(), page } : null;
    const label = link ? T("selection paste") : T("ink paste");
    let items = [];
    if (clip.strokes.length) {
      const landed = await handleInkStrokes(page, cloneStrokes(clip.strokes, shift.dx, shift.dy), size, { label, link });
      if (!landed) return;
      items = [{ id: landed.id, ids: landed.ids }];
    } else if (!link || !applyInk([], { label, link })) return;
    if (link) stepBlocks(link.id, blocks);
    pickInkTool("select");
    handleInkSelect(page, items, pictures);
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
  // selection's surface, else the one in view, and its unlocked pictures
  // unless the lasso's row leaves them out, the lasso armed.
  function selectAllInk() {
    const page = inkSelection?.page ?? pasteTarget()?.page;
    if (page == null) return false;
    const items = selectAllItems(inkBySurface.get(page), inkOf);
    const pictures = lassoPictures ? selectAllPictures(picturesBySurface(blocksRef.current).get(page)) : [];
    if (!items.length && !pictures.length) return false;
    pickInkTool("select");
    handleInkSelect(page, items, pictures);
    return true;
  }
  // Transcribe in the chat (the Convert menu, in the selection menu's first
  // row). A selection of whole groups asks for their text as
  // "Transcribe with AI" does: the page's drafts are saved first (the
  // server reads the stored files) and each group's block rides as a chip,
  // the oldest attached block making room.
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
  // The selected strokes as an SVG document of their own (inkSvg.js), null
  // with none. On a sheet of dark paper the document carries the paper's
  // colour as its ground: the ink there is written light, which a
  // transparent picture pasted on white would lose (inkSvg's `background`).
  function selectionSvg() {
    const sel = selectedStrokes(inkSelection, inkOf);
    if (!sel) return null;
    const tree = blocksRef.current, sheetId = sheetOfBlock(tree, inkSelection.items[0]?.id);
    const paper = sheetId ? normalizePaper(findBlock(tree, sheetId)?.properties?.sheet) : null;
    return inkSvg(sel.strokes, paper && paperIsDark(paper) ? { background: paper.color } : {});
  }
  // Copy as SVG: a PNG, the markup as text and, where the browser takes
  // one, the SVG (copyImage.js copySvg). Called in the click itself, with
  // nothing awaited before the clipboard's write, so Safari keeps the
  // gesture.
  function copySvgSelection() {
    const svg = selectionSvg();
    if (!svg) return;
    copySvg(svg.markup, svg).then((ok) => setStatus(ok ? t("Copied the selected handwriting as SVG.")
      : t("The browser did not let the handwriting onto the clipboard.")));
  }
  // Download SVG: the same document as handwriting.svg (as a diagram's
  // Download SVG saves one, shared/ui/DiagramFigure.jsx).
  function downloadSvgSelection() {
    const svg = selectionSvg();
    if (!svg) return;
    saveBlob(new Blob([svg.markup], { type: "image/svg+xml;charset=utf-8" }), "handwriting.svg").then(
      (saved) => { if (!saved) setStatus(t("The app did not take {filename}.", { filename: "handwriting.svg" })); },
      (err) => setStatus(t("Could not save {filename}: {message}", { filename: "handwriting.svg", message: err.message })));
  }
  // Redraw as shapes (`kind` "shapes") and Convert to text ("text"): the
  // selection's strokes go to the AI (inkAi.js inkAiRequest, inkAiClient.js)
  // and its answer is applied as one step with Undo on the pill
  // (docs/dev/handwriting_ai.md). The request is made of the selection as
  // it is now; the answer, seconds later, is applied to the groups as they
  // are then (redrawPlan, textPlan: what changed meanwhile is left alone),
  // and only on the page that asked: leaving it stops the request, and an
  // answer that still comes is dropped. One request at a time in a page
  // view; its task row's Stop aborts it. A shared page's view has no AI.
  async function askAboutSelection(kind) {
    const sel = inkSelection, pageId = focusedBlockId, redraw = kind === "shapes";
    if (!sel || !pageId) return;
    if (!ai) {
      setStatus(redraw ? t("Redrawing needs the AI, which a shared page cannot use.")
        : t("Converting handwriting needs the AI, which a shared page cannot use."));
      return;
    }
    const request = inkAiRequest(sel, inkOf);
    if (!request) { setStatus(redraw ? t("Select pen strokes to redraw.") : t("Select handwriting to convert.")); return; }
    // (an in-progress sentence, so the pill keeps spinning until the answer's or the Stop's replaces it)
    if (aiRef.current) { setStatus(t("The AI is still working on the last selection…")); return; }
    // the server's own caps, before anything is sent: a redraw's pen strokes
    // (gamma/routers/ink_ai.py, its sentence), and the one file every
    // request is read as (inkAi.js requestTooBig)
    if ((redraw && penOnly(request.strokes).length > MAX_REDRAW_STROKES) || requestTooBig(request)) {
      setStatus(redraw ? t("Too much handwriting to redraw at once; select less.")
        : t("Too much handwriting to convert at once; select less."));
      return;
    }
    const ctl = new AbortController();
    // the row named after its page, as the tab's other AI rows are
    const label = redraw ? t("Redraw as shapes") : t("Convert to text");
    const pageName = String(ai.pageTitle || "").trim().slice(0, 48);
    const task = ai.addTask({ name: pageName ? `${label} — ${pageName}` : label, kind: "ai", info: t("asking…"),
      cancel: () => ctl.abort() });
    const job = { ctl, task };
    aiRef.current = job;
    const work = { page: request.page, box: request.box };
    setInkAiWork(work);
    const settle = (state, info = "") => ai.updateTask(task, { state, info, cancel: null });
    setStatus(redraw ? t("Redrawing the selection as shapes…") : t("Reading the handwriting…"));
    let answer;
    try {
      answer = await askInkAi(kind, { model: ai.model || "", space: request.space, strokes: request.strokes }, ctl.signal);
    } catch (err) {
      if (ctl.signal.aborted) { stoppedAsking(settle); return; }
      failedAsking(redraw, err, settle);
      return;
    } finally {
      if (aiRef.current === job) aiRef.current = null;
      setInkAiWork((w) => (w === work ? null : w));
    }
    // Stopped (the task row, the page left) or out of date: nothing
    // changes, and no pill says otherwise.
    const now = live.current;
    if (ctl.signal.aborted || now.focusedBlockId !== pageId || now.readOnly) { stoppedAsking(settle); return; }
    const applied = redraw ? applyRedraw(request, sel, answer, settle) : applyConversion(request, sel, answer, settle);
    // the wash fades where the answer landed, as a translated paragraph's does
    if (applied) {
      const landed = { ...work, landed: true };
      setInkAiWork(landed);
      setTimeout(() => setInkAiWork((w) => (w === landed ? null : w)), LANDED_MS);
    }
  }
  // A request stopped before its answer was applied: its row says so (a
  // Stop from the row has said it already), and the waiting pill goes.
  function stoppedAsking(settle) {
    settle("cancelled", t("stopped"));
    live.current.setStatus(t("Stopped: the handwriting stays as it was."));
  }
  // A request the server refused (no AI, an allowance used up, a model
  // that cannot see pictures …), or whose stream ended in an error or
  // without an answer. An error line already names what failed in the
  // server's words ("redrawing failed: …", gamma/routers/ink_ai.py), which
  // the pill says in its own.
  function failedAsking(redraw, err, settle) {
    const message = String(err?.message || err || "").replace(/^(redrawing failed|reading the handwriting failed): /i, "");
    settle("failed", message);
    live.current.setStatus(redraw ? t("Redrawing failed: {message}", { message })
      : t("Reading the handwriting failed: {message}", { message }));
  }
  // The groups as they are now, for an answer's plan: one whose block left
  // the tree meanwhile (deleted, or its sheet was) is gone, its strokes
  // skipped, so the answer never brings it back (inkAi.js inkInTree).
  function inkNow() {
    return inkInTree(live.current.inkOf, blocksRef.current);
  }
  // Redraw as shapes, answered ({shapes}): the shapes take the place of the
  // strokes they were drawn from, one stroke-history step "ink redraw as
  // shapes", and become the selection while the one asked about still is
  // (the lasso armed, nothing else selected since). → whether it applied.
  function applyRedraw(request, sel, answer, settle) {
    const now = live.current;
    if (!Array.isArray(answer?.shapes)) { failedAsking(true, new Error(t("the AI sent no answer")), settle); return; }
    const plan = redrawPlan(request, answer.shapes, inkNow());
    if (plan.refused) {
      settle("failed");
      now.setStatus(t("This handwriting note is full. Move part of it to a new note, or start a new one."));
      return;
    }
    if (!plan.count) {
      settle("done");
      now.setStatus(plan.skipped ? t("Nothing was redrawn: the selected strokes changed meanwhile.")
        : t("The AI found no shapes to redraw in the selection."));
      return;
    }
    // (the byte budget may refuse it, and says so)
    if (!now.applyInk(plan.changes, { label: T("ink redraw as shapes") })) { settle("failed"); return; }
    if (now.inkSelection === sel) now.handleInkSelect(request.page, plan.items);
    settle("done");
    now.undoableInkStatus(tn("Redrawn as {n} shape.", "Redrawn as {n} shapes.", plan.count)
      + (plan.skipped ? tn(" {n} skipped: the strokes changed meanwhile.", " {n} skipped: the strokes changed meanwhile.", plan.skipped) : ""));
    return true;
  }
  // Convert to text, answered ({text}): the pen strokes still there as they
  // were sent go (inkAi.js textPlan: one moved or reshaped meanwhile
  // stays), and one text box with the text takes their place, on them as
  // they are now (textBoxFor), one linked step of the two histories, "ink
  // conversion to text": the strokes' half the stroke history's, the box
  // the block history's (stepBlocks), measured in the commit that shows
  // it. The selection asked about goes with its strokes. → whether it
  // applied.
  function applyConversion(request, sel, answer, settle) {
    const now = live.current;
    if (typeof answer?.text !== "string") { failedAsking(false, new Error(t("the AI sent no answer")), settle); return; }
    const text = answer.text.trim();
    if (!text) { settle("done"); now.setStatus(t("The AI found no writing in the selection.")); return; }
    const plan = textPlan(request, inkNow());
    if (!plan.removed) {
      settle("done");
      now.setStatus(plan.skipped ? t("Nothing was converted: the selected handwriting changed meanwhile.")
        : t("Nothing was converted: the selected handwriting is gone."));
      return;
    }
    const boxId = makeId(), t0 = wallNow(), text_box = textBoxFor(plan, text);
    const place = (tree) => addPlaced(tree, request.page, boxId, { t0, text_box });
    // a sheet deleted meanwhile takes no box (addPlaced leaves the tree as it is)
    if (place(blocksRef.current) === blocksRef.current) {
      settle("failed");
      now.setStatus(t("The handwriting's page of paper is gone."));
      return;
    }
    const link = { id: makeId(), page: request.page, kind: "text" };
    if (!now.applyInk(plan.changes, { label: T("ink conversion to text"), link })) { settle("failed"); return; }
    now.stepBlocks(link.id, (tree) => {
      const placed = place(tree);
      return placed === tree ? tree : setBlockText(placed, boxId, text);
    });
    now.touchTextBox(boxId);
    if (now.inkSelection === sel) setInkSelection(null);
    settle("done");
    now.undoableInkStatus(t("Converted the handwriting to text.")
      + (plan.skipped ? tn(" {n} skipped: the strokes changed meanwhile.", " {n} skipped: the strokes changed meanwhile.", plan.skipped) : ""));
    return true;
  }
  // New note from selection: the selected strokes leave their groups for a
  // new ink block on the same surface, ids kept, as one entry across the
  // groups and the new block (applyInk inserts it; undo empties it again,
  // and flushInk deletes the empty block). A selection that already is a
  // whole note of its own is left as it is. The new note becomes the
  // selection once applyInk applied it: the byte budget may refuse it, as
  // it may a merge, leaving the selection as it was.
  function splitInkSelection() {
    const plan = splitPlan(inkSelection, inkOf, makeId);
    if (!plan) return;
    if (plan.refused) { setStatus(plan.refused); return; }
    if (applyInk(plan.changes, { label: T("ink split") })) handleInkSelect(inkSelection.page, plan.items, inkSelection.pictures);
  }
  // Merge into note: the selection's whole group appended to the surface's
  // previous group (the nearest before it in page order, else the first
  // after it) and this one emptied, one entry; a group whose block holds a
  // caption or notes is refused, since its block would stay behind empty.
  function mergeInkSelection() {
    const plan = mergePlan(inkSelection, inkOf, blocksRef.current, inkBySurface, threaded);
    if (!plan) return;
    if (plan.refused) { setStatus(plan.refused); return; }
    if (applyInk(plan.changes, { label: T("ink merge") })) handleInkSelect(inkSelection.page, plan.items, inkSelection.pictures);
  }
  // Comment: a thread on the selected handwriting, its card opened to
  // write in and the selection let go. A selection of one whole group is
  // that group's; any other becomes a group of its own first, as New note
  // makes one, and that group's thread opens once the page has drawn it,
  // so its card stands beside it. The split stays if the comment is never
  // written: it is still the person's drawing.
  function commentInkSelection() {
    if (!onComment) return;
    const sel = inkSelection, whole = wholeGroup(sel, inkOf);
    if (whole) {
      setInkSelection(null);
      onComment(whole);
      return;
    }
    const plan = splitPlan(sel, inkOf, makeId);
    if (!plan) return;
    if (plan.refused) { setStatus(plan.refused); return; }
    if (!applyInk(plan.changes, { label: T("ink split") })) return;
    setInkSelection(null);
    const id = plan.items[0].id;
    requestAnimationFrame(() => { if (live.current.focusedBlockId === focusedBlockId) onComment(id); });
  }
  return { inkSelection, inkClip, inkAiWork, clearInkSelection, restoreInkSelection, handleInkSelect, handleInkMoveSelection,
    deleteInkSelection, selectionStill, handleInkAction, copyInkSelection, pasteInk, pasteAtCentre,
    duplicateInkSelection, selectAllInk };
}
