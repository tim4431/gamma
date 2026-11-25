// The stroke history's rules (useInkDocument.js `inkHistRef`;
// docs/dev/handwriting.md "Client"), pure: an entry recorded, an undo or
// a redo stepped onto the drawings as they are now, the flushes of one
// eraser pass folded into one entry, and the pill an undo raises.
// frontend/tests/inkHistory.test.mjs pins them. The history is {undo,
// redo}, two lists of entries {changes: [{id, page, before, after}],
// label, pass?, link?}, each change stored forward (before → after).
// `link` {id, page, kind?}: a step of two halves, whose other half is the
// block history's entry of that id (useInkDocument.js inkUndo) — a lasso
// step that took pictures, or (`kind` "text") Convert to text, whose box
// is that half (useInkSelection.js).
import { t, tn } from "../shared/i18n/i18n.js";
import { mergeInk } from "./ink.js";

// The entries undo keeps; past it the oldest goes.
export const HISTORY_CAP = 200;

// An edit's entry on the history `h` (changed in place): `pass` kept on
// it for the eraser pass's later flushes, `link` for its pictures' half,
// the oldest dropped past the cap, and the redo list emptied.
export function recordEntry(h, changes, { label, pass, link }) {
  h.undo.push({ changes, label, ...(pass ? { pass } : {}), ...(link ? { link } : {}) });
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

// Why `entry` cannot step onto its groups as they are now, else null:
// {saving: true} when one's DELETE has gone and its block is still on the
// page (the deletion would take what the step brings back); {loading: url}
// when one with no draft names a file that is not here yet — on its way,
// never asked for, or failed with another try due — since stepped without
// it the step would replace what it holds. A file gone for good does not
// hold it: the step goes onto the drawing it had. `blockOf(id)` the
// group's block on the page, `hasDraft(id)`, `deleting(id)`
// (inkStore.isDeleting), `fileState(url)` (inkStore.fileState).
export function stepWaits(entry, { blockOf, hasDraft, deleting, fileState }) {
  for (const c of entry?.changes || []) {
    const block = blockOf(c.id);
    if (!block) continue;
    if (deleting(c.id)) return { saving: true };
    const url = block.properties?.ink_url || "";
    if (hasDraft(c.id) || !url) continue;
    if (["unknown", "loading", "failed"].includes(fileState(url))) return { loading: url };
  }
  return null;
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

// The entry an undo (or `redo`) steps, taken off its list of `h`: the
// newest, or with `from` (the block history stepped the pictures' half of
// the link of that id: App's stepBlockHistory) the newest only when it
// carries that link. null when there is none to step.
export function takeEntry(h, redo, from = null) {
  const stack = redo ? h.redo : h.undo;
  if (from && stack[stack.length - 1]?.link?.id !== from) return null;
  return stack.pop() || null;
}

// Why the block half of a linked step (its pictures, or Convert to text's
// box) stayed when the stroke history stepped `entry`, `linked` being what
// the block history's undoLinked answered: "blocked" when someone else
// changed it since, "notes" when that history's newest step was another
// (the notes changed since), null when the entry has no such half or it
// stepped too.
export function picturesStayed(entry, linked) {
  if (!entry.link || (linked && !linked.blocked)) return null;
  return linked ? "blocked" : "notes";
}

// The pill after an undo or a redo of `entry`: "Undone: ink move (page
// 2).", with the strokes someone changed since that were kept (`kept`),
// or why its block half stayed (`stayed`, picturesStayed's) — its
// pictures, or the text box of a link of kind "text"; `page` is what
// messages call the entry's first surface (useInkSurfaces.js
// inkPageNumber).
export function undoMessage(entry, redo, kept, page, stayed = null) {
  // a label is kept in English (T) and translated here; one made with t() already stays as it is
  const what = `${redo ? t("Redone") : t("Undone")}: ${t(entry.label)}`;
  const box = entry.link?.kind === "text";
  if (stayed === "blocked") {
    return box ? t("{what} (page {page}; the text box stays as it is: someone else changed it since).", { what, page })
      : t("{what} (page {page}; the pictures stay as they are: someone else changed them since).", { what, page });
  }
  if (stayed) {
    return box ? t("{what} (page {page}; the text box stays as it is: the notes changed since).", { what, page })
      : t("{what} (page {page}; the pictures stay as they are: the notes changed since).", { what, page });
  }
  return kept ? tn("{what} (page {page}; {n} stroke someone changed since was kept).",
    "{what} (page {page}; {n} strokes someone changed since were kept).", kept, { what, page })
    : `${what} (page ${page}).`;
}
