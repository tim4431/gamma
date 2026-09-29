// Owned by App, not the dock: navigation can unmount the view while its
// request keeps running. Keep the transcript and Stop control together.
// Conversations stream independently: one reply per bucket may be in flight,
// so a question asked on one paper keeps running while another paper is
// asked its own. `active` lists the buckets with a reply in flight.
//
// Another tab or member may hold the same conversation, so saves are
// conditional (routers/chats.py): each conversation carries the version (the
// server's `updated_at`) this tab last read or wrote. A save made from an
// older copy comes back 409 with the stored conversation; the two are merged
// (mergeChats — nothing either side added is lost) and saved again. A save
// that fails on the network or with a 5xx is retried; one that still fails
// marks the conversation in `failed` (the dock says it is not saved) until
// a later save goes through.

const RETRY_DELAYS = [1000, 3000, 8000];
const MAX_MERGES = 5;
const NO_VERSION = { at: "", base: [] };

const messageKey = (m) => (m && m.id) || JSON.stringify(m);
const sameMessage = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// Three-way merge of two copies of one conversation that both grew from
// `base`: every message in `theirs` stays (in its version, unless ours
// changed it since `base` — a reply that kept streaming), except the ones
// this copy dropped since `base` (an edit-and-resend); every message only
// `ours` has is added after the message it follows in ours (at the end when
// it follows none). What `theirs` dropped since `base` stays dropped too —
// the other copy started a New chat (the stored conversation is empty, or
// another one opened from history) or edited and resent — unless ours
// changed its turn since (a question and the replies after it): then the
// whole turn stays, so a reply that finished here keeps its question.
// Messages match by their `id`, else by content.
export function mergeChats(base, ours, theirs) {
  const baseBy = new Map((base || []).map((m) => [messageKey(m), m]));
  const oursBy = new Map((ours || []).map((m) => [messageKey(m), m]));
  const out = [];
  for (const m of theirs || []) {
    const key = messageKey(m);
    const mine = oursBy.get(key);
    if (!mine) {
      if (!baseBy.has(key)) out.push(m); // theirs added it; ours didn't drop it
      continue;
    }
    out.push(baseBy.has(key) && sameMessage(mine, baseBy.get(key)) ? m : mine);
  }
  const changed = (m) => !baseBy.has(messageKey(m)) || !sameMessage(m, baseBy.get(messageKey(m)));
  const kept = new Set(); // ours' messages in a turn ours changed since `base`
  let turn = [];
  const closeTurn = () => { if (turn.some(changed)) turn.forEach((m) => kept.add(m)); turn = []; };
  for (const m of ours || []) {
    if (m?.role === "user") closeTurn();
    turn.push(m);
  }
  closeTurn();
  let at = null;
  for (const m of ours || []) {
    const key = messageKey(m);
    const found = out.findIndex((x) => messageKey(x) === key);
    if (found >= 0) { at = found + 1; continue; }
    if (!kept.has(m)) continue; // theirs dropped it since the base, and ours left its turn alone
    const place = at ?? out.length;
    out.splice(place, 0, m);
    at = place + 1;
  }
  return out;
}

const sameChat = (a, b) => a.length === b.length && a.every((m, i) => sameMessage(m, b[i]));
// A failure worth another try: the network (fetch's TypeError) or the server.
const retryable = (err) => err?.name === "TypeError" || err?.status >= 500;

// `save(key, messages, updatedAt)` resolves to `{updated_at}`; a 409 rejects
// with `err.status` 409 and `err.data` the stored `{messages, updated_at}`.
export function createChatSession(save, { retryDelays = RETRY_DELAYS,
  wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms)) } = {}) {
  let snapshot = { replies: new Map(), active: new Set(), failed: new Map() };
  const controllers = new Map();
  const listeners = new Set();
  const timers = new Map();
  const writes = new Map();
  const saved = new WeakSet();
  const versions = new Map(); // key -> {at, base}: the stored conversation as last read or written here
  const inputs = new Map();   // key -> the list a reply last handed in (rebased after a merge)
  const merges = new Map();   // key -> merges so far: a save queued before one saves what is shown now
  const emit = () => { for (const listener of listeners) listener(); };
  const setFailed = (key, message) => {
    if ((snapshot.failed.get(key) || "") === message) return;
    const failed = new Map(snapshot.failed);
    if (message) failed.set(key, message); else failed.delete(key);
    snapshot = { ...snapshot, failed };
    emit();
  };
  const show = (key, messages) => {
    if (!snapshot.replies.has(key)) return;
    snapshot = { ...snapshot, replies: new Map(snapshot.replies).set(key, {
      ...snapshot.replies.get(key), messages,
    }) };
    emit();
  };
  const write = async (key, messages, epoch) => {
    // A merge since this save was queued made what is shown a superset of
    // the list it captured: that is what to save now.
    if ((merges.get(key) || 0) !== epoch && snapshot.replies.has(key)) messages = snapshot.replies.get(key).messages;
    for (let retries = 0, tries = 0; ;) {
      if (saved.has(messages)) return;
      const seen = versions.get(key) || NO_VERSION;
      try {
        const res = await save(key, messages, seen.at);
        versions.set(key, { at: res?.updated_at ?? seen.at, base: messages });
        saved.add(messages);
        setFailed(key, "");
        return;
      } catch (err) {
        if (err?.status === 409 && err.data && tries < MAX_MERGES && snapshot.replies.has(key)) {
          // Someone saved this conversation since we read it: keep both.
          tries += 1;
          merges.set(key, (merges.get(key) || 0) + 1);
          const theirs = Array.isArray(err.data.messages) ? err.data.messages : [];
          versions.set(key, { at: err.data.updated_at || "", base: theirs });
          const merged = mergeChats(seen.base, snapshot.replies.get(key).messages, theirs);
          if (sameChat(merged, theirs)) {
            show(key, theirs); // everything of ours is stored already
            saved.add(theirs);
            setFailed(key, "");
            return;
          }
          show(key, merged);
          messages = merged;
        } else if (retryable(err) && retries < retryDelays.length) {
          await wait(retryDelays[retries]);
          retries += 1;
        } else {
          setFailed(key, err?.message || String(err));
          throw err;
        }
      }
    }
  };
  const flush = (key) => {
    clearTimeout(timers.get(key));
    timers.delete(key);
    const reply = snapshot.replies.get(key);
    if (!reply || saved.has(reply.messages)) return writes.get(key) || Promise.resolve();
    // Serialize checkpoints and completion so a slow partial save can never
    // overwrite the final answer. A failed checkpoint must not block retries.
    const epoch = merges.get(key) || 0;
    const pending = (writes.get(key) || Promise.resolve()).catch(() => {})
      .then(() => write(key, reply.messages, epoch));
    writes.set(key, pending);
    return pending;
  };
  return {
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    getSnapshot: () => snapshot,
    isActive: (key) => snapshot.active.has(key),
    isSaved: (key) => !snapshot.active.has(key) && saved.has(snapshot.replies.get(key)?.messages),
    // Why the conversation's last save failed ("" = it is saved, or saving).
    saveError: (key) => snapshot.failed.get(key) || "",
    // The stored conversation as this tab just read it (the dock's load, an
    // opened history entry): what later saves are conditional on.
    seen(key, messages, updatedAt) {
      versions.set(key, { at: updatedAt || "", base: messages || [] });
    },
    // The version the tab's copy is based on — "New chat" and opening a
    // history entry send it, so a newer stored conversation is kept.
    version: (key) => (versions.get(key) || NO_VERSION).at,
    start(key, messages, title, ctrl, replyId) {
      if (snapshot.active.has(key)) return false;
      controllers.set(key, ctrl);
      inputs.set(key, messages);
      snapshot = {
        ...snapshot,
        replies: new Map(snapshot.replies).set(key, { messages, title, replyId }),
        active: new Set(snapshot.active).add(key),
      };
      emit();
      flush(key).catch(() => {}); // a failure shows as saveError
      return true;
    },
    update(key, messages, final = false) {
      // After a merge brought in messages this reply doesn't know, its next
      // list is rebased onto what is shown, so they stay.
      const prev = inputs.get(key);
      const shown = snapshot.replies.get(key)?.messages;
      inputs.set(key, messages);
      const next = prev && shown && shown !== prev ? mergeChats(prev, messages, shown) : messages;
      snapshot = { ...snapshot, replies: new Map(snapshot.replies).set(key, {
        ...snapshot.replies.get(key), messages: next,
      }) };
      emit();
      if (final) return flush(key);
      if (!timers.has(key)) timers.set(key, setTimeout(() => { flush(key).catch(() => {}); }, 500));
    },
    finish(key) {
      controllers.delete(key);
      const active = new Set(snapshot.active);
      active.delete(key);
      snapshot = { ...snapshot, active };
      emit();
    },
    stop(key) { controllers.get(key)?.abort(); },
    flush,
    // Drop the tab's copy (the stored conversation moved to history, or the
    // server's copy is shown again); its version goes with it.
    forget(key) {
      clearTimeout(timers.get(key));
      timers.delete(key);
      versions.delete(key);
      inputs.delete(key);
      const replies = new Map(snapshot.replies);
      replies.delete(key);
      const failed = new Map(snapshot.failed);
      failed.delete(key);
      snapshot = { ...snapshot, replies, failed };
      emit();
    },
  };
}
