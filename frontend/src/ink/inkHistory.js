// The stroke history's rules (useInkDocument.js `inkHistRef`;
// docs/dev/handwriting.md "Client"), pure: an entry recorded, an undo or
// a redo stepped onto the drawings as they are now, the flushes of one
// eraser pass folded into one entry, and the pill an undo raises.
// frontend/tests/inkHistory.test.mjs pins them. The history is {undo,
// redo}, two lists of entries {changes: [{id, page, before, after}],
// label, pass?}, each change stored forward (before → after).
import { t, tn } from "../shared/i18n/i18n.js";
import { mergeInk } from "./ink.js";

// The entries undo keeps; past it the oldest goes.
export const HISTORY_CAP = 200;

// An edit's entry on the history `h` (changed in place): `pass` kept on
// it for the eraser pass's later flushes, the oldest dropped past the
// cap, and the redo list emptied.
export function recordEntry(h, changes, { label, pass }) {
  h.undo.push(pass ? { changes, label, pass } : { changes, label });
  if (h.undo.length > HISTORY_CAP) h.undo.shift();
  h.redo = [];
}

// An entry stepped back (undo: after → before) or again (redo: before →
// after), each change onto its group as it is NOW by stroke id
// (mergeInk; `inkOf(id)` the group's strokes now, the step's start for a
// group that has none), so strokes someone else drew meanwhile stay.
// What someone changed since is kept over the step: `kept` counts it.
// → {changes, kept}.
export function stepChanges(entry, redo, inkOf) {
  let kept = 0;
  const changes = entry.changes.map((c) => {
    const [from, to] = redo ? [c.before, c.after] : [c.after, c.before];
    const now = inkOf(c.id) || from;
    const merged = mergeInk(from, to, now);
    if (!merged.clean) kept += Math.max(1, merged.lostOurs.length);
    return { ...c, before: now, after: merged.ink };
  });
  return { changes, kept };
}

// Whether an eraser flush (`changes`, of the pass `pass`) folds into the
// last entry of `h`: that entry is the same pass's, and each group it
// holds is still drawn as its `after`. A group changed meanwhile (a save
// that merged someone's strokes) starts a new entry instead, so undoing
// the pass never takes theirs away. `held(c)` is the entry's change of
// c's group, if it has one. → {fold, held}.
export function passFold(h, changes, pass) {
  const last = h.undo[h.undo.length - 1];
  const held = (c) => last.changes.find((x) => x.id === c.id);
  const fold = !!pass && last?.pass === pass && changes.every((c) => !held(c) || held(c).after === c.before);
  return { fold, held };
}
// The flush folded into that entry, `last` (changed in place): a group
// the entry holds takes the flush's `after`, another joins it.
export function foldInto(last, changes, held) {
  for (const c of changes) {
    const entry = held(c);
    if (entry) entry.after = c.after; else last.changes.push({ ...c });
  }
}

// The pill after an undo or a redo of `entry`: "Undone: ink move (page
// 2).", with the strokes someone changed since that were kept (`kept`);
// `page` is what messages call the entry's first surface
// (useInkSurfaces.js inkPageNumber).
export function undoMessage(entry, redo, kept, page) {
  // a label is kept in English (T) and translated here; one made with t() already stays as it is
  const what = `${redo ? t("Redone") : t("Undone")}: ${t(entry.label)}`;
  return kept ? tn("{what} (page {page}; {n} stroke someone changed since was kept).",
    "{what} (page {page}; {n} strokes someone changed since were kept).", kept, { what, page })
    : `${what} (page ${page}).`;
}
