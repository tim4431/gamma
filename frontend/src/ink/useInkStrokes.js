// The strokes (docs/dev/handwriting.md "Client"): a finished stroke, or a
// paste's strokes, into the surface's current group as one edit
// (useInkDocument.js applyInk), the group's saved file loaded first when
// no draft holds it yet and a new group started when it is full (the
// arithmetic in inkEdits.js); and the pill a snapped stroke raises. App
// composes it after the document.
import { useEffect, useRef } from "react";
import { guideEvents } from "../guide/events.js";
import { wantsSheetAfter } from "../notebook/notebook.js";
import { t, T } from "../shared/i18n/i18n.js";
import { makeId } from "../shared/lib/utils";
import { appendStroke, appendStrokes, strokeBounds, strokeBytes } from "./ink.js";
import { currentGroup, pasteTooBig, pickGroup } from "./inkEdits.js";
import * as inkStore from "./inkStore";

// How long a stroke waits for its group's file (readyFor).
const READY_WAIT_MS = 3000;

// focusedBlockId: the open page; leaving it, the next stroke starts a
// fresh group. readOnly, and inkStale (the build fence, buildFence.js):
// nothing is written. blocksRef, nbSheetsRef: the page's tree and its
// sheets, read when a stroke lands. applyInk: the document's edit
// (useInkDocument.js). addSheetAfter(id, {once}): the notebook's next
// sheet (App's). noteSqueezeUse(): marks a held squeeze used (the
// Pencil's). undoableInkStatus(msg): the document's pill with Undo.
// setStatus: the status pill. closedGroup(id): the group has a comment
// thread (comments/useInkThreads.js), so it takes no more strokes.
export function useInkStrokes({ focusedBlockId, readOnly, inkStale, blocksRef, nbSheetsRef,
  applyInk, addSheetAfter, noteSqueezeUse, undoableInkStatus, setStatus, closedGroup = () => false }) {
  // The group the next stroke on a surface joins ({page, id}).
  const inkActiveRef = useRef(null);
  // The open page as it is now, for a stroke that waited (readyFor).
  const focusedRef = useRef(focusedBlockId);
  focusedRef.current = focusedBlockId;
  // Leaving a page: the next stroke starts a fresh group.
  useEffect(() => () => { inkActiveRef.current = null; }, [focusedBlockId]);
  // A group with a comment thread takes no more strokes, nor does one whose
  // empty-group DELETE has gone while its block is still on the page
  // (inkStore.isDeleting): the deletion would take the stroke with it.
  const closed = (id) => closedGroup(id) || inkStore.isDeleting(id);
  // Waits for the file of the surface's current group when no draft holds
  // it yet and it is on its way or not asked for (someone saved the group
  // meanwhile), so the strokes join it rather than start another — for
  // READY_WAIT_MS at most, so a slow or hung fetch never holds a stroke. A
  // file that failed, or has not come by then, is not waited for: groupFor
  // passes the group over.
  async function readyFor(page) {
    const g = currentGroup(inkActiveRef.current, page, blocksRef.current, closed);
    const url = g?.properties.ink_url;
    if (!g || inkStore.draft(g.id) || !url) return;
    const state = inkStore.fileState(url);
    if (state !== "unknown" && state !== "loading") return;
    let timer = 0;
    await Promise.race([inkStore.loadInk(url), new Promise((resolve) => { timer = setTimeout(resolve, READY_WAIT_MS); })]);
    clearTimeout(timer);
  }
  // The group `count` strokes of `bytes` join on `page` (`size` its
  // {width, height} in points), with its strokes so far (inkEdits.js
  // pickGroup): the surface's current group while its block is on the page
  // and it is not closed, its draft, else its loaded file; else a new
  // group — a stroke never replaces strokes that have not arrived, and a
  // group past the split budget (or full of strokes) is left as it is: the
  // strokes start the page's next group, invisibly. Called in the same run
  // as the edit it feeds (no await between them), so strokes that waited
  // for one file each land on the draft the one before made. → {id, before}.
  function groupFor(page, size, bytes, count) {
    const picked = pickGroup(inkActiveRef.current, page, size, bytes, count, { blocks: blocksRef.current, closed,
      draftOf: inkStore.draft, fileOf: inkStore.fileOf, makeId });
    inkActiveRef.current = { page, id: picked.id };
    return picked;
  }
  // The group's file ready (readyFor); → false when the page changed while
  // it waited, which leaves the strokes no page to land on. The caller then
  // reads its group (groupFor) and applies in one run.
  async function landed(page) {
    const pageNow = focusedRef.current;
    await readyFor(page);
    return focusedRef.current === pageNow;
  }
  // `page`: a PDF page number, or a notebook sheet's id; `label` the
  // history entry's (the layer's finishStroke names a straight line or a
  // shape, "Undone: ink shape").
  async function handleInkStroke(page, stroke, size, { label = T("ink stroke") } = {}) {
    if (readOnly || !focusedBlockId || inkStale) return;
    // a stroke drawn while a squeeze holds its tool makes that squeeze no toggle
    noteSqueezeUse();
    if (!(await landed(page))) return;
    const { id, before } = groupFor(page, size, strokeBytes(stroke), 1);
    applyInk([{ id, page, before, after: appendStroke(before, stroke) }], { label });
    if (typeof page === "string") {
      // Writing into the last quarter of the page's last sheet adds the next
      // one right after it, so there is always paper below (Notability's
      // continuous page) — once: a page after it already there is the one.
      if (wantsSheetAfter(nbSheetsRef.current, page, strokeBounds(stroke))) addSheetAfter(page, { once: true });
    }
    guideEvents.emit("ink.stroke");
  }
  // Several strokes at once (a paste): they join the surface's current
  // group when it can take them all under MAX_STROKES and GROUP_SPLIT_BYTES,
  // else a new group takes them all — a paste is never split across groups
  // — as one entry, `label` and `link` its (a paste with pictures,
  // useInkSelection.js). → {id, ids} of the group they landed in, null when
  // no group could hold them or the byte budget refused them.
  async function handleInkStrokes(page, strokes, size, { label = T("ink paste"), link = null } = {}) {
    if (readOnly || !focusedBlockId || inkStale || !strokes.length || !size) return null;
    if (pasteTooBig(page, strokes, size)) {
      setStatus(t("Too much handwriting to paste into one note."));
      return null;
    }
    if (!(await landed(page))) return null;
    const { id, before } = groupFor(page, size, strokes.reduce((n, s) => n + strokeBytes(s), 0), strokes.length);
    const { ink: after, ids } = appendStrokes(before, strokes);
    return applyInk([{ id, page, before, after }], { label, link }) ? { id, ids } : null;
  }
  // The pill a snapped stroke raises ("Straightened.", "Shape: circle.",
  // ink/finishStroke.js): Undo is one tap away every time; a `once` status
  // (the first Always straightening of a highlighter) is said once a session.
  const straightHintRef = useRef(true);
  function handleHoldStatus(status) {
    if (!status?.text) return;
    if (status.once) {
      if (!straightHintRef.current) return;
      straightHintRef.current = false;
    }
    undoableInkStatus(status.text);
  }
  return { handleInkStroke, handleInkStrokes, handleHoldStatus };
}
