// What the notes lock refuses (vaultState.js notesLocked: a vault that pushes
// everything and does not pull writes nothing of Gamma's to its files, so a
// note changed here would part from the file its notes are written in).
// BlockTree draws every row but a highlight's or a region's with no editor;
// those rows keep their own, and App's row handlers ask these before a
// change it starts would make, remove, move or open a note. Pure:
// tests/notesLock.test.mjs.
import { getParentInfo, isPlaceBlock, visibleNeighbor } from "../shared/model/blockModel.js";

// A row the lock holds: anything but a highlight or a picture's region.
export const lockedRow = (block) => !!block && !isPlaceBlock(block);

const holdsLocked = (block) => lockedRow(block) || (block.children || []).some(holdsLocked);

// Whether `change` to row `id` reaches a locked row: "delete" (the row and
// all it holds go, or its children are lifted into its place, or it goes to
// another page), "indent" (under the row above it), "outdent" (out of its
// parent), "move" one step among its siblings (`dir`). Any of them when the
// row holds a locked one. A row not in the tree: true.
export function touchesLocked(blocks, id, change, dir = 0) {
  const info = getParentInfo(blocks, id);
  if (!info || holdsLocked(info.block)) return true;
  const { parent, siblings, index } = info;
  if (change === "indent") return index > 0 && lockedRow(siblings[index - 1]);
  if (change === "outdent") return lockedRow(parent);
  if (change === "move") return lockedRow(siblings[index + dir]);
  return false;
}

// The next row shown above (`dir` -1) or below (1) whose editor opens: the
// arrows' hop skips the locked rows. null when there is none.
export function hopTarget(blocks, id, dir, view) {
  let target = visibleNeighbor(blocks, id, dir, view);
  while (target && lockedRow(target)) target = visibleNeighbor(blocks, target.id, dir, view);
  return target;
}
