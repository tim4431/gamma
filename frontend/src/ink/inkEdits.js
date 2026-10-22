// The arithmetic of a page's ink edits (useInkDocument.js applyInk and the
// erasers, useInkStrokes.js; docs/dev/handwriting.md "Client"), pure: the
// byte budget, the group blocks an edit puts back, a fresh group's file,
// when strokes start a new group, and the changes an eraser makes.
// frontend/tests/inkEdits.test.mjs pins them. A change is {id, page,
// before, after}: a group's block id, its surface (a PDF page's number, a
// sheet's id) and its drawing before and after.
import { findBlock, updateBlockTree } from "../shared/model/blockModel.js";
import {
  GROUP_SPLIT_BYTES, MAX_BYTES, MAX_STROKES, eraseAlong, hitStrokesAlong, inkBytes, newCanvasInk, newInk, pdfPositionOf, removeStrokes,
  strokeBytes,
} from "./ink.js";

// The byte budget: an edit that would take a group past the server's cap
// (MAX_BYTES) is refused; one that shrinks a group over it is not.
export function overBudget(changes) {
  return changes.some((c) => inkBytes(c.after) > MAX_BYTES && inkBytes(c.after) > inkBytes(c.before));
}

// The changes that leave strokes in a group with no block in the tree
// (`present`, the tree's ids): one undone away, or erased empty and
// deleted.
export function missingGroups(changes, present) {
  return changes.filter((c) => c.after.strokes.length && !present.has(c.id));
}

// The tree (`prev`) with the `missing` groups' blocks back, each a fresh
// one with no file: a sheet's under its sheet (`page` is then the sheet's
// id), a PDF page's at the page's top level with its `pdf_position`; a
// sheet's whose sheet is gone goes there too.
export function withGroupBlocks(prev, missing) {
  return missing.reduce((tree, c) => {
    const onSheet = typeof c.page === "string";
    const node = { id: c.id, parentId: onSheet ? c.page : null, children: [], content: "",
      properties: onSheet ? { ink_url: "", ink_strokes: 0 } : { ink_url: "", ink_strokes: 0, pdf_position: pdfPositionOf(c.after) } };
    return onSheet && findBlock(tree, c.page)
      ? updateBlockTree(tree, c.page, (sheet) => ({ ...sheet, children: [...(sheet.children || []), node] }))
      : [...tree, node];
  }, prev);
}

// A fresh group's file on surface `page`, `size` ({width, height}) in its
// points: a sheet's canvas, or a PDF page's.
export function freshInk(page, size) {
  return typeof page === "string" ? newCanvasInk(size.width, size.height) : newInk(page, size.width, size.height);
}

// Whether `count` strokes of `bytes` (ink.js strokeBytes) start the
// surface's next group rather than join `before`: they would take a group
// that has strokes past GROUP_SPLIT_BYTES or past MAX_STROKES.
export function splitsGroup(before, bytes, count) {
  return before.strokes.length > 0 && (inkBytes(before) + bytes > GROUP_SPLIT_BYTES || before.strokes.length + count > MAX_STROKES);
}

// A paste no group can hold: more strokes than MAX_STROKES, or more bytes
// than a fresh group on `page` takes under MAX_BYTES.
export function pasteTooBig(page, strokes, size) {
  return strokes.length > MAX_STROKES || inkBytes(freshInk(page, size)) + strokes.reduce((n, s) => n + strokeBytes(s), 0) > MAX_BYTES;
}

// A scribble's targets ([{gid, ids}], ink/gestures.js scribbleTargets)
// through the eraser filter `keep` (ink.js eraserKeep), `inkOf(id)` a
// group's strokes now: the changes across their groups, and `n` the
// strokes that go.
export function eraseManyChanges(page, targets, inkOf, keep) {
  const changes = [];
  let n = 0;
  for (const { gid, ids } of targets || []) {
    const before = inkOf(gid);
    if (!before) continue;
    const allowed = before.strokes.filter((s) => ids.includes(s.id) && keep(s)).map((s) => s.id);
    if (!allowed.length) continue;
    n += allowed.length;
    changes.push({ id: gid, page, before, after: removeStrokes(before, allowed) });
  }
  return { changes, n };
}

// The eraser's sweep along `path` (page points, radius `r`) over a
// surface's `groups` (their blocks): each group cut along it (`partial`,
// ink.js eraseAlong) or losing the strokes it touched (hitStrokesAlong),
// through `keep`. The changes of the groups it changed.
export function eraseAlongChanges(page, groups, path, r, partial, inkOf, keep) {
  const changes = [];
  for (const block of groups) {
    const before = inkOf(block.id);
    if (!before?.strokes?.length) continue;
    let after = before;
    if (partial) after = eraseAlong(before, path, r, keep).ink;
    else {
      const ids = hitStrokesAlong(before, path, r, keep);
      if (ids.length) after = removeStrokes(before, ids);
    }
    if (after !== before) changes.push({ id: block.id, page, before, after });
  }
  return changes;
}
