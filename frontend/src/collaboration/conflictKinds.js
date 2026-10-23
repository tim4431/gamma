// What each kind of sync_conflicts row a clone records is (gamma/sync_engine.py
// `_conflict`, docs/dev/mirror.md), in git's words — local is this clone,
// remote is origin: its title, the sentence under it, its glyph (a key of
// MergeResolver.jsx's icon table) and whether it holds two texts to choose
// between. Pure: tests/conflictKinds.test.mjs.
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
};

export function kindOf(conflict) {
  return MERGE_KIND[conflict.kind] || { short: conflict.kind, long: "", icon: "alert" };
}

// Textual conflicts hold both texts and take a choice; the others are
// decisions to acknowledge.
export function isTextual(conflict) {
  return Boolean(MERGE_KIND[conflict.kind]?.textual);
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
