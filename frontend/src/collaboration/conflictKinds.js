// What each kind of sync_conflicts row is: a clone's (gamma/sync_engine.py
// `_conflict`, docs/dev/mirror.md), in git's words — local is this clone,
// remote is origin — and a Zotero link's (gamma/zotero_sync.py
// CONFLICT_KINDS, docs/dev/zotero_sync.md), in Gamma's and Zotero's: its
// title, the sentence under it, its glyph (a key of MergeResolver.jsx's
// icon table), whether it holds two texts to choose between (`textual`),
// whether those are only the two sides, with no merged text (`two`), and
// whether its sides are Gamma and Zotero (`zotero`). A Zotero kind that
// also offers sending Gamma's text anyway says so (`send`); a Zotero text
// kind whose `result` is the row's own sentence, not a text, says so
// (`says`); one whose Gamma side a receive-only link keeps here alone has
// a sentence for that row too (`kept`, `keptHere`). Pure:
// tests/conflictKinds.test.mjs.
import { T, t, tn } from "../shared/i18n/i18n.js";

export const MERGE_KIND = {
  merged: { short: T("Auto-merged"), long: T("Both sides changed this block; the two edits were merged into one text."), icon: "merge", textual: true },
  diverged: { short: T("Diverged"), long: T("Local and remote differed with no common version to merge from; one was kept, the other is here."), icon: "alert", textual: true },
  kept_local_edit: { short: T("Kept local"), long: T("Remote deleted this, but it was edited here, so it stayed and was pushed back."), icon: "up" },
  restored_remote_edit: { short: T("Restored remote"), long: T("This was deleted here, but remote edited it, so it was pulled back."), icon: "down" },
  page_restored: { short: T("Page restored on remote"), long: T("Remote deleted this page; it was edited or restored here, so it was pushed back."), icon: "up" },
  page_restored_from_remote: { short: T("Page restored from remote"), long: T("This page was deleted here but edited on remote, so it was pulled back."), icon: "down" },
  dropped: { short: T("Removed"), long: T("A force or an attach made both sides the same; only the replaced side had this, so it was removed. Its text is kept here."), icon: "trash" },
  ink_merged: { short: T("Handwriting merged"), long: T("Both sides changed this handwriting at once; it was merged stroke by stroke and is the same on both sides now."), icon: "merge" },
  file: { short: T("Handwriting not sent"), long: T("Remote did not accept this handwriting: it runs an older Gamma. The block's other changes were sent; the drawing follows once remote is updated."), icon: "alert" },
  // A Zotero link's: Gamma's side wins a difference both sides made, and
  // Zotero's is kept here to take instead (a receive-only link keeps
  // Gamma's here alone until it sends changes again: `kept`).
  zotero_field: { short: T("Record changed on both sides"), long: T("Gamma and Zotero both changed this paper's record; Gamma's values were kept on both sides, and Zotero's are here."), kept: T("Gamma and Zotero both changed this paper's record; Gamma's values are kept in Gamma and go to Zotero once the link sends changes again, and Zotero's are here."), icon: "alert", textual: true, two: true, zotero: true },
  zotero_note: { short: T("Note changed on both sides"), long: T("Gamma and Zotero both changed this note; Gamma's version was kept on both sides, and Zotero's is here."), kept: T("Gamma and Zotero both changed this note; Gamma's version is kept in Gamma and goes to Zotero once the link sends changes again, and Zotero's is here."), icon: "merge", textual: true, two: true, zotero: true },
  zotero_annotation: { short: T("Annotation changed on both sides"), long: T("Gamma and Zotero both changed this annotation; Gamma's was kept on both sides, and Zotero's is here."), kept: T("Gamma and Zotero both changed this annotation; Gamma's is kept in Gamma and goes to Zotero once the link sends changes again, and Zotero's is here."), icon: "alert", textual: true, two: true, zotero: true },
  zotero_file: { short: T("PDF replaced on both sides"), long: T("Gamma and Zotero both replaced this PDF; Gamma's went to Zotero, and Zotero's file is kept here to take instead."), icon: "alert", textual: true, two: true, zotero: true, says: true },
  zotero_lossy: { short: T("Kept in Gamma"), long: T("Gamma cannot write this back to Zotero without losing what it holds (citations, tables, colours…), so the edit stays in Gamma. Send it anyway, or take Zotero's."), icon: "alert", textual: true, two: true, zotero: true, send: true },
  zotero_deleted_edited: { short: T("Kept: edited after a delete"), long: T("One side deleted this while the other edited it; the edit won, so it was kept on both sides."), icon: "alert", zotero: true },
  zotero_locked: { short: T("Another member's annotation"), long: T("This annotation belongs to another member of the Zotero group, so Gamma sends nothing of your change to it; what became of it is below."), icon: "alert", zotero: true },
  zotero_rejected: { short: T("Change not synced"), long: T("Zotero did not accept this change, or Gamma could not apply Zotero's; why is below. It is tried again once it changes."), icon: "alert", zotero: true },
};

// The rows of a Zotero collection: their page is the folders tree
// (blocks_store.FOLDERS), their block the folder; the labels tree's the same.
const TREES = { folders: "folder", labels: "label" }; // i18n-ignore: the trees' ids
// Where a row's Open goes: "folder" or "label" (its block_id), else "page".
export const conflictPlace = (conflict) => (Object.hasOwn(TREES, conflict?.page_id ?? "") ? TREES[conflict.page_id] : "page");

// A folder's own words where a kind's speak of a paper.
const FOLDER_KIND = {
  zotero_field: { short: T("Folder changed on both sides"), long: T("Gamma and Zotero both changed this folder (its Zotero collection); Gamma's was kept on both sides, and Zotero's is here."), kept: T("Gamma and Zotero both changed this folder (its Zotero collection); Gamma's is kept in Gamma and goes to Zotero once the link sends changes again, and Zotero's is here.") },
};

// How the engine's `result` ends when a receive-only link kept Gamma's side
// of a difference here without sending it (gamma/zotero_sync_rows.py
// KEPT_HERE): the card says so instead of "kept on both sides".
export const KEPT_HERE_END = "once the link sends changes again"; // i18n-ignore: the engine's sentence, matched
export const keptHere = (conflict) => isZotero(conflict) && String(conflict?.result || "").endsWith(KEPT_HERE_END);

export function kindOf(conflict) {
  const kind = MERGE_KIND[conflict.kind] || { short: conflict.kind, long: "", icon: "alert" };
  const own = conflictPlace(conflict) === "folder" ? FOLDER_KIND[conflict.kind] : null;
  const words = own ? { ...kind, ...own } : kind;
  return words.kept && keptHere(conflict) ? { ...words, long: words.kept } : words;
}

// The row's own sentence a Zotero card shows (its `result`): why Zotero
// refused, Gamma giving up after its tries, another member's annotation
// back, a PDF of Zotero's that could not be kept. A text kind's result is
// otherwise the text in the block, or its kind's sentence again: "".
export function zoteroReason(conflict) {
  const kind = MERGE_KIND[conflict?.kind];
  if (!conflict?.result || !isZotero(conflict) || (kind?.textual && !kind.says)) return "";
  return String(conflict.result);
}

// Textual conflicts hold both texts and take a choice; the others are
// decisions to acknowledge.
export function isTextual(conflict) {
  return Boolean(MERGE_KIND[conflict.kind]?.textual);
}

// A Zotero link's row: its sides are Gamma and Zotero.
export function isZotero(conflict) {
  return Boolean(MERGE_KIND[conflict.kind]?.zotero) || String(conflict.kind || "").startsWith("zotero_");
}

// What the remote's merge of a drawing did (an `ink_merged` row: `theirs`
// is its report, `{clean, lost_ours, returned}` as JSON), one line per side
// that lost something — this clone's stroke changes that gave way, strokes
// remote had erased that came back — or one line when neither did. The row
// is only acknowledged: the merged drawing is on both sides already, and
// putting this clone's back whole would erase what remote drew meanwhile.
export function inkMergeLines(conflict) {
  let report = null;
  try { report = JSON.parse(conflict.theirs || "null"); } catch { /* not a report: the plain line */ }
  const count = (key) => (Array.isArray(report?.[key]) ? report[key].length : 0);
  const lost = count("lost_ours"), back = count("returned");
  const lines = [];
  if (lost) {
    lines.push({ side: "mine", text: tn("The change made here to {n} stroke was not applied: remote changed it too",
      "The changes made here to {n} strokes were not applied: remote changed them too", lost) });
  }
  if (back) {
    lines.push({ side: "theirs", text: tn("{n} stroke erased on remote came back: it was changed here",
      "{n} strokes erased on remote came back: they were changed here", back) });
  }
  return lines.length ? lines : [{ side: "result", text: t("Merged with changes made on remote meanwhile.") }];
}
