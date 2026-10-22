// The strokes (docs/dev/handwriting.md "Client"): a finished stroke, or a
// paste's strokes, into the surface's current group as one edit
// (useInkDocument.js applyInk), the group's saved file loaded first when
// no draft holds it yet and a new group started when it is full; and the
// pill a snapped stroke raises. App composes it after the document.
import { useEffect, useRef } from "react";
import { guideEvents } from "../guide/events.js";
import { t, T } from "../shared/i18n/i18n.js";
import { makeId } from "../shared/lib/utils";
import { flattenBlocks } from "../shared/model/blockModel.js";
import { GROUP_SPLIT_BYTES, MAX_BYTES, MAX_STROKES, appendStroke, appendStrokes, inkBytes, newCanvasInk, newInk, strokeBounds, strokeBytes } from "./ink.js";
import * as inkStore from "./inkStore";

// focusedBlockId: the open page; leaving it, the next stroke starts a
// fresh group. readOnly, and inkStale (the build fence, buildFence.js):
// nothing is written. blocksRef, nbSheetsRef: the page's tree and its
// sheets, read when a stroke lands. applyInk: the document's edit
// (useInkDocument.js). addSheetAfter(id, {once}): the notebook's next
// sheet (App's). noteSqueezeUse(): marks a held squeeze used (the
// Pencil's). undoableInkStatus(msg): the document's pill with Undo.
// setStatus: the status pill.
export function useInkStrokes({ focusedBlockId, readOnly, inkStale, blocksRef, nbSheetsRef,
  applyInk, addSheetAfter, noteSqueezeUse, undoableInkStatus, setStatus }) {
  // The group the next stroke on a surface joins ({page, id}).
  const inkActiveRef = useRef(null);
  // Leaving a page: the next stroke starts a fresh group.
  useEffect(() => () => { inkActiveRef.current = null; }, [focusedBlockId]);
  // `page`: a PDF page number, or a notebook sheet's id; `label` the
  // history entry's (the layer's finishStroke names a straight line or a
  // shape, "Undone: ink shape").
  async function handleInkStroke(page, stroke, size, { label = T("ink stroke") } = {}) {
    if (readOnly || !focusedBlockId || inkStale) return;
    // a stroke drawn while a squeeze holds its tool makes that squeeze no toggle
    noteSqueezeUse();
    let id = inkActiveRef.current?.page === page ? inkActiveRef.current.id : null;
    const existing = id ? flattenBlocks(blocksRef.current).find((b) => b.id === id && b.properties?.ink_url !== undefined) : null;
    if (!existing) id = makeId();
    inkActiveRef.current = { page, id };
    // The group's strokes so far: the draft, else its file (loaded first —
    // a stroke must never replace strokes that just have not arrived yet).
    const loaded = !inkStore.draft(id) && existing?.properties.ink_url
      ? await inkStore.loadInk(existing.properties.ink_url) : null;
    const onSheet = typeof page === "string";
    const fresh = () => (onSheet ? newCanvasInk(size.width, size.height) : newInk(page, size.width, size.height));
    let before = inkStore.draft(id)?.ink || loaded || fresh();
    // A group past the split budget (or full of strokes) is left as it is:
    // the stroke starts the page's next group, invisibly.
    if (before.strokes.length && (inkBytes(before) + strokeBytes(stroke) > GROUP_SPLIT_BYTES || before.strokes.length >= MAX_STROKES)) {
      id = makeId();
      inkActiveRef.current = { page, id };
      before = fresh();
    }
    applyInk([{ id, page, before, after: appendStroke(before, stroke) }], { label });
    if (onSheet) {
      // Writing into the last quarter of the page's last sheet adds the next
      // one right after it, so there is always paper below (Notability's
      // continuous page) — once: a page after it already there is the one.
      const last = nbSheetsRef.current.at(-1), box = strokeBounds(stroke);
      if (last?.id === page && box && box[3] > last.paper.height * 0.75) addSheetAfter(last.id, { once: true });
    }
    guideEvents.emit("ink.stroke");
  }
  // Several strokes at once (a paste): they join the surface's current
  // group when it can take them all under MAX_STROKES and GROUP_SPLIT_BYTES,
  // else a new group takes them all — a paste is never split across groups
  // — as one entry. → {id, ids} of the group they landed in, null when no
  // group could hold them.
  async function handleInkStrokes(page, strokes, size) {
    if (readOnly || !focusedBlockId || inkStale || !strokes.length || !size) return null;
    const onSheet = typeof page === "string";
    const fresh = () => (onSheet ? newCanvasInk(size.width, size.height) : newInk(page, size.width, size.height));
    const bytes = strokes.reduce((n, s) => n + strokeBytes(s), 0);
    if (strokes.length > MAX_STROKES || inkBytes(fresh()) + bytes > MAX_BYTES) {
      setStatus(t("Too much handwriting to paste into one note."));
      return null;
    }
    let id = inkActiveRef.current?.page === page ? inkActiveRef.current.id : null;
    const existing = id ? flattenBlocks(blocksRef.current).find((b) => b.id === id && b.properties?.ink_url !== undefined) : null;
    if (!existing) id = makeId();
    const loaded = existing && !inkStore.draft(id) && existing.properties.ink_url
      ? await inkStore.loadInk(existing.properties.ink_url) : null;
    let before = inkStore.draft(id)?.ink || loaded || fresh();
    if (before.strokes.length && (inkBytes(before) + bytes > GROUP_SPLIT_BYTES || before.strokes.length + strokes.length > MAX_STROKES)) {
      id = makeId();
      before = fresh();
    }
    inkActiveRef.current = { page, id };
    const { ink: after, ids } = appendStrokes(before, strokes);
    applyInk([{ id, page, before, after }], { label: T("ink paste") });
    return { id, ids };
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
