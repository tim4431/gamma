// Taking back the agent's note changes from the reply that made them. Each
// edit, new note, move or deletion the agent made carries `revert` (what
// undoing it needs, recorded by its tool — gamma/ai_tools.py), and so do a
// folder or label it deleted and a page tool's rename, move or label change
// (`revert.pages`, one page or many); POST /api/ai/revert
// undoes it as a merge into the notes or the library as they are now
// (gamma/ai_revert.py) and the action is marked `reverted` in the saved
// conversation, which tells the model on the next turn (ai_context's
// replay). A revert or redo that left pages as they were saves how many
// (`kept`), which the replay tells the model. The request's history leaves
// out `revert` and a batch's `items`. The rows that do it are
// chat/AgentChanges.jsx; "Revert all" covers the notes only.
import { changePlace, isChange } from "./agentSteps.js";

const REVERTIBLE = new Set(["edit", "create", "move", "delete"]);
// The library changes that record a revert: a deleted folder or label, put
// back with its pages filed in it again (gamma/ai_revert.py _plan_tree_delete).
const LIBRARY_REVERTIBLE = new Set(["delete_folder", "delete_label"]);
// The page tools record each page's title, folders or labels before and
// after, one page or many (`revert.pages`): their row reverts them together
// (gamma/ai_revert.py _plan_pages), and needs no block id.
const PAGE_REVERTIBLE = new Set(["rename_page", "move_page", "label_page"]);

// A change recorded for reverting (changes saved by an older version have
// no `revert`).
function recorded(a) {
  if (!isChange(a) || !a.revert) return false;
  if (PAGE_REVERTIBLE.has(a.tool)) return Array.isArray(a.revert.pages) && a.revert.pages.length > 0;
  if (!a.block_id) return false;
  return changePlace(a) === "notes" ? REVERTIBLE.has(a.kind) : LIBRARY_REVERTIBLE.has(a.tool);
}

// The revert endpoint's kind for an action: "pages" for a page tool's
// record (a page move's own kind, "move", is the note move's there; a
// deleted folder's or label's `revert.pages` is its own, kind "delete").
export const revertKind = (a) => (PAGE_REVERTIBLE.has(a?.tool) ? "pages" : a?.kind);

// A change this reply can still take back.
export function canRevert(a) {
  return recorded(a) && !a.reverted;
}

// A reverted change that can be put back. A new note needs the place it
// was made in, which older actions did not record.
export function canRedo(a) {
  return recorded(a) && !!a.reverted && (a.kind !== "create" || typeof a.revert.parent === "string");
}

// The actions as the request's history sends them for the replay: the
// texts kept for reverting stay with the saved conversation.
export function forReplay(actions) {
  return actions.map((a) => {
    if (!a?.revert && !a?.items) return a;
    const { revert: _kept, items: _pages, ...rest } = a;
    return rest;
  });
}

// The indexes "Revert all" takes back, newest first: a later change to the
// same note comes out before the one it was made on top of. It lists the
// note changes only.
export function revertOrder(actions = []) {
  return actions.map((a, i) => (canRevert(a) && changePlace(a) === "notes" ? i : -1)).filter((i) => i >= 0).reverse();
}

// `messages` with the actions at `indexes` of the message `id` marked
// reverted, or with `reverted` false unmarked again after a redo, with
// `fields` in place of the last ones (`kept`: the pages a page tool's revert
// or redo left as they were; the same list when nothing changes).
export function markReverted(messages, id, indexes, reverted = true, fields = {}) {
  const marks = new Set(indexes);
  let changed = false;
  const next = messages.map((m) => {
    if (m.id !== id || !m.actions?.length) return m;
    return { ...m, actions: m.actions.map((a, i) => {
      if (!marks.has(i) || !!a.reverted === reverted) return a;
      changed = true;
      const { reverted: _was, kept: _left, ...rest } = a;
      return reverted ? { ...rest, reverted: true, ...fields } : { ...rest, ...fields };
    }) };
  });
  return changed ? next : messages;
}

// A refused revert (the API error, whose `data` is `{detail, conflict?,
// preview?}`) as its row shows it: `{detail, status, conflict, preview}`.
// A 409 `conflict` "changed", "filled" or "moved" can be forced, "gone"
// and "kept" cannot.
export function revertRefusal(err) {
  const data = err?.data && typeof err.data === "object" ? err.data : {};
  return {
    detail: err?.message || String(err),
    status: err?.status || 0,
    conflict: err?.status === 409 ? data.conflict || "" : "",
    preview: data.preview || null,
  };
}
