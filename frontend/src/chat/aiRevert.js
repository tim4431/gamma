// Taking back the agent's note changes from the reply that made them. Each
// edit, new note or move the agent made carries `revert` (what undoing it
// needs, recorded by its tool — gamma/ai_tools.py); POST /api/ai/revert
// undoes it as a merge into the note as it is now (gamma/ai_revert.py) and
// the action is marked `reverted` in the saved conversation, which tells
// the model on the next turn (ai_context's replay). The rows that do it are
// chat/AgentChanges.jsx.
import { changePlace, isChange } from "./agentSteps.js";

const REVERTIBLE = new Set(["edit", "create", "move", "delete"]);

// A note change recorded for reverting (changes saved by an older version
// have no `revert`).
function recorded(a) {
  return isChange(a) && changePlace(a) === "notes" && REVERTIBLE.has(a.kind) && !!a.block_id && !!a.revert;
}

// A note change this reply can still take back.
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
    if (!a?.revert) return a;
    const { revert: _kept, ...rest } = a;
    return rest;
  });
}

// The indexes "Revert all" takes back, newest first: a later change to the
// same note comes out before the one it was made on top of.
export function revertOrder(actions = []) {
  return actions.map((a, i) => (canRevert(a) ? i : -1)).filter((i) => i >= 0).reverse();
}

// `messages` with the actions at `indexes` of the message `id` marked
// reverted, or with `reverted` false unmarked again after a redo (the same
// list when nothing changes).
export function markReverted(messages, id, indexes, reverted = true) {
  const marks = new Set(indexes);
  let changed = false;
  const next = messages.map((m) => {
    if (m.id !== id || !m.actions?.length) return m;
    return { ...m, actions: m.actions.map((a, i) => {
      if (!marks.has(i) || !!a.reverted === reverted) return a;
      changed = true;
      if (reverted) return { ...a, reverted: true };
      const { reverted: _was, ...rest } = a;
      return rest;
    }) };
  });
  return changed ? next : messages;
}

// A refused revert (the API error, whose `data` is `{detail, conflict?,
// preview?}`) as its row shows it: `{detail, status, conflict, preview}`.
// A 409 `conflict` "changed", "filled" or "moved" can be forced, "gone"
// cannot.
export function revertRefusal(err) {
  const data = err?.data && typeof err.data === "object" ? err.data : {};
  return {
    detail: err?.message || String(err),
    status: err?.status || 0,
    conflict: err?.status === 409 ? data.conflict || "" : "",
    preview: data.preview || null,
  };
}
