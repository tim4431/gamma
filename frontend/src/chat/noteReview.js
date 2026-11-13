// The AI's note changes shown in the notes themselves, as tracked changes:
// editor/mdDiff.js draws them in the block's rendered view, and
// editor/NoteReviewBar.jsx is the bar under it. What the note shows follows
// the permission the edit ran under, and never asks twice:
//
// - Ask: an edit waiting on its approval card. The block shows the text the
//   card would write (the preview's whole `text`) against what it says now,
//   with the card's four answers, which answer the card. Once answered it
//   shows nothing more: an edit allowed on its card was seen there.
// - Allowed without asking (Allow in Settings, or a grant of the chat or of
//   every chat): an edit_block or create_block action of a reply streamed in
//   this tab (ChatDock feeds them as they arrive), with the block's text
//   before and after it (the action's `revert`), drawn as a record of what
//   changed. Nothing waits on it: Hide stops drawing it, Hide all every one
//   of its page, and Revert takes its edits back newest first through the
//   chat's revert (ChatDock connects it: chat/aiRevert.js, marked reverted
//   in the conversation like a row of the reply); a revert that stops says
//   why, with "Revert anyway" where it can be forced. The block shows it
//   while it still says what the AI wrote; an editor open on it, or a word
//   typed since, shows the text as it is. Several edits of one block in a
//   row are one change, from the text before the first to the text after
//   the last.
//
// Held in this tab, never saved: a reload shows none. The link of a note
// change's row in the chat draws it again (`show`), hidden or not, from
// the saved conversation.
import { useSyncExternalStore } from "react";

const MAX = 200; // changes held; the oldest goes first

// `replying`: the conversations with a reply streaming in this tab.
let snapshot = { changes: new Map(), asking: new Map(), replying: new Set() };
const listeners = new Set();
let reverter = null;

function set(next) {
  snapshot = next;
  for (const listener of listeners) listener();
}

// The change one action made that a note can show: {blockId, pageId,
// before, after}, or null (a read, a failure, a change that changed nothing,
// an action saved before `revert` recorded the texts).
export function noteChange(a) {
  if (!a || a.error || a.noop || a.reverted || !a.block_id) return null;
  if (a.tool !== "edit_block" && a.tool !== "create_block") return null;
  const after = a.revert?.after;
  const before = a.tool === "create_block" ? "" : a.revert?.before;
  if (typeof after !== "string" || typeof before !== "string" || before === after) return null;
  return { blockId: a.block_id, pageId: a.page_id || "", before, after };
}

// `changes` with the action's change added: a step on top of the block's
// change when it starts from the text that change left, else a change of
// its own. `step` is how to revert it: {action, key, reply, index} — the
// conversation, the reply's message id and the action's index in it.
export function withChange(changes, step) {
  const c = noteChange(step.action);
  if (!c) return changes;
  const next = new Map(changes);
  const prev = next.get(c.blockId);
  next.delete(c.blockId);
  if (prev && prev.after === c.before) {
    if (prev.before !== c.after) next.set(c.blockId, { ...prev, after: c.after, steps: [...prev.steps, step] });
  } else {
    next.set(c.blockId, { ...c, steps: [step] });
  }
  while (next.size > MAX) next.delete(next.keys().next().value);
  return next;
}

// The steps of the change an action of a conversation is part of, in order:
// it and the edits of its block just before and after it that start from
// the text the one before left, as `landed` chains them. `reply` and
// `index` name the action; `key` is the conversation's. None for an action
// a note cannot show (a reverted one among them).
export function changeSteps(messages, reply, index, key) {
  const all = [];
  for (const m of messages || []) {
    if (!m?.id || m.role === "user") continue;
    (m.actions || []).forEach((action, i) => { if (noteChange(action)) all.push({ action, key, reply: m.id, index: i }); });
  }
  const at = all.find((s) => s.reply === reply && s.index === index);
  if (!at) return [];
  const own = all.filter((s) => s.action.block_id === at.action.block_id);
  let from = own.indexOf(at), to = from;
  const meets = (x, y) => noteChange(x.action).after === noteChange(y.action).before;
  while (from > 0 && meets(own[from - 1], own[from])) from--;
  while (to + 1 < own.length && meets(own[to], own[to + 1])) to++;
  return own.slice(from, to + 1);
}

export const noteReview = {
  subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot: () => snapshot,
  // An action of a streaming reply landed (ChatDock).
  landed(step) {
    if (step.action?.approval) return; // allowed on its card, seen there
    const changes = withChange(snapshot.changes, step);
    if (changes !== snapshot.changes) set({ ...snapshot, changes });
  },
  // Draw a change again, asked for from its row in the chat
  // (`changeSteps`): hidden, allowed on its card or from before a reload.
  // The note draws it while it still says what the AI wrote.
  show(steps) {
    if (!steps?.length) return;
    let changes = new Map(snapshot.changes);
    changes.delete(steps[0].action.block_id);
    for (const step of steps) changes = withChange(changes, step);
    set({ ...snapshot, changes });
  },
  // A note edit waits on its approval card; `decide(decision)` answers it
  // ("once", "chat", "always" or "deny") as the card does, and `labels` are
  // the card's words for the answers' titles: {permission, kind}.
  ask(approval, decide, labels = {}) {
    const p = approval?.preview;
    if (approval?.tool !== "edit_block" || !p?.block_id || typeof p.text !== "string") return;
    const asking = new Map(snapshot.asking);
    asking.set(p.block_id, { id: approval.id, blockId: p.block_id, pageId: p.page_id || "", text: p.text, decide,
      permission: labels.permission || "", kind: labels.kind || "" });
    set({ ...snapshot, asking });
  },
  // A reply of conversation `key` streams, or ended (ChatDock). Its changes
  // revert once it ended, as its rows in the chat do: a revert's mark in
  // the conversation would go with the reply's next save.
  replying(key, on) {
    if (snapshot.replying.has(key) === on) return;
    const replying = new Set(snapshot.replying);
    if (on) replying.add(key); else replying.delete(key);
    set({ ...snapshot, replying });
  },
  // The card was answered, or its reply ended.
  settle(id) {
    const left = [...snapshot.asking.values()].filter((a) => a.id !== id);
    if (left.length !== snapshot.asking.size) set({ ...snapshot, asking: new Map(left.map((a) => [a.blockId, a])) });
  },
  // Stop drawing the block's change; the note keeps its text.
  hide(blockId) {
    if (!snapshot.changes.has(blockId)) return;
    const changes = new Map(snapshot.changes);
    changes.delete(blockId);
    set({ ...snapshot, changes });
  },
  // Stop drawing every change of a page.
  hidePage(pageId) {
    const changes = new Map([...snapshot.changes].filter(([, c]) => c.pageId !== pageId));
    if (changes.size !== snapshot.changes.size) set({ ...snapshot, changes });
  },
  // Take the block's change back, its steps newest first. Resolves to null,
  // or to {text, force} when a step's revert stopped: why, and the label
  // of the button that reverts anyway (none when it can't be forced).
  async revert(blockId, { force = false } = {}) {
    const change = snapshot.changes.get(blockId);
    if (!change || !reverter || change.steps.some((s) => snapshot.replying.has(s.key))) return null;
    for (let k = change.steps.length - 1; k >= 0; k--) {
      const refused = await reverter(change.steps[k], { force });
      if (refused) {
        // A change partly taken back no longer shows: its rows in the chat
        // revert the rest.
        if (k < change.steps.length - 1) noteReview.hide(blockId);
        return refused;
      }
    }
    noteReview.hide(blockId);
    return null;
  },
  // The chat's revert, connected once by ChatDock: `(step, {force})` takes
  // one step back and resolves to null, or to {text, force} when it stopped.
  connect(fn) { reverter = fn; },
  // The reply's row reverted or redid one of a block's steps: what the
  // block shows is the chat's to say now.
  forget(blockId) { noteReview.hide(blockId); },
};

// The change a block shows, and the edit of it waiting on its card: each
// row reads only its own, so a change elsewhere renders nothing here.
export const useNoteChange = (blockId) =>
  useSyncExternalStore(noteReview.subscribe, () => snapshot.changes.get(blockId) || null);
export const useNoteAsk = (blockId) =>
  useSyncExternalStore(noteReview.subscribe, () => snapshot.asking.get(blockId) || null);
// How many changes a page holds, for the bar's "Hide all".
const countOn = (pageId) => {
  let n = 0;
  for (const c of snapshot.changes.values()) if (c.pageId === pageId) n++;
  return n;
};
export const usePageChanges = (pageId) => useSyncExternalStore(noteReview.subscribe, () => countOn(pageId));
// Whether a reply that made this change still streams: its Revert waits.
export const useStillReplying = (change) => useSyncExternalStore(noteReview.subscribe,
  () => !!change?.steps.some((s) => snapshot.replying.has(s.key)));
