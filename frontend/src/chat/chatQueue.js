// The chat's queue and Steer, as Codex has them (docs/dev/ai.md "Queue and
// Steer"). What the user sends while a reply runs in a conversation waits
// here, in order, drawn above the composer (ChatQueue.jsx), and goes as the
// next message once the reply ends on its own. A reply that was stopped or
// failed pauses the queue: nothing goes until the user sends again (a
// message, or a queued one's Send). Steer hands a queued message to the
// running reply instead (Gamma AI's: POST /api/ai/chat/steer/{reply},
// gamma/ai_steer.py; a computer agent's turn: POST
// /api/local-agents/chat/turns/{turn}/steer), which takes it at its next
// step. Until the reply says it did (Gamma AI's stream `{"steered"}`, the
// turn's message its `steers`) it stays here, marked as steering at the
// front; a reply that ends first leaves it there as the next message.
//
// Kept per conversation key for the tab's life, apart from the dock, so a
// reply that ends while the dock shows another bucket keeps its queue for
// when the dock comes back to it. A reload loses it, as Codex's.
//
// An item: {id, text, parts, state: "queued" | "steering"}, `parts` what
// went with the message from the composer (Gamma AI's pictures, files,
// passages and notes; a computer agent's passages; for both, the
// transcript's passages it quotes, `quotes`).
import { useCallback, useSyncExternalStore } from "react";
import { quoteMarkdown, withChatQuotes } from "./chatQuotes.js";

const EMPTY = Object.freeze({ items: Object.freeze([]), paused: false, reply: "" });
const queues = new Map(); // key -> {items, paused, reply}: `reply` the running reply's steer id, "" for none
const heard = new Set();

export const queueOf = (key) => queues.get(key) || EMPTY;

function change(key, edit) {
  const before = queueOf(key);
  const after = edit(before);
  if (after === before) return;
  if (!after.items.length && !after.paused && !after.reply) queues.delete(key);
  else queues.set(key, after);
  for (const listener of heard) listener();
}

const withItems = (q, items) => ({ ...q, items });

// A message sent while the reply runs, at the end of the queue.
export function enqueue(key, { id, text, parts = {} }) {
  change(key, (q) => withItems(q, [...q.items, { id, text, parts, state: "queued" }]));
}

export function remove(key, id) {
  change(key, (q) => (q.items.some((m) => m.id === id) ? withItems(q, q.items.filter((m) => m.id !== id)) : q));
}

// A queued message's text, edited in its row.
export function edit(key, id, text) {
  change(key, (q) => withItems(q, q.items.map((m) => (m.id === id && m.state === "queued" ? { ...m, text } : m))));
}

// Moved to `to` (0 … the count: before the message at that index, or last),
// as shared/ui/useDragOrder.js reports a drop. A message being steered in
// stays where it is, at the front, and none goes ahead of it.
export function move(key, id, to) {
  change(key, (q) => {
    const from = q.items.findIndex((m) => m.id === id);
    if (from < 0 || q.items[from].state !== "queued") return q;
    const items = [...q.items];
    const [item] = items.splice(from, 1);
    const front = items.filter((m) => m.state === "steering").length;
    items.splice(Math.max(front, to > from ? to - 1 : to), 0, item);
    return items.every((m, i) => m === q.items[i]) ? q : withItems(q, items);
  });
}

// The message handed to the running reply: marked, behind the others being
// steered in, ahead of everything queued. Its item, or null.
export function steer(key, id) {
  const item = queueOf(key).items.find((m) => m.id === id && m.state === "queued");
  if (!item) return null;
  change(key, (q) => {
    const rest = q.items.filter((m) => m.id !== id);
    const front = rest.filter((m) => m.state === "steering").length;
    return withItems(q, [...rest.slice(0, front), { ...item, state: "steering" }, ...rest.slice(front)]);
  });
  return item;
}

// The reply did not take it (the steer request failed): queued again, at
// the front.
export function unsteer(key, id) {
  change(key, (q) => (q.items.some((m) => m.id === id && m.state === "steering")
    ? withItems(q, q.items.map((m) => (m.id === id ? { ...m, state: "queued" } : m))) : q));
}

// The reply took these in (`{"steered": {ids}}`): they leave the queue for
// the transcript. Returns them, in their order here.
export function landed(key, ids) {
  const taken = queueOf(key).items.filter((m) => ids.includes(m.id));
  if (taken.length) change(key, (q) => withItems(q, q.items.filter((m) => !ids.includes(m.id))));
  return taken;
}

// A reply started in the conversation: whatever the user sent resumes a
// paused queue.
export function started(key) {
  change(key, (q) => (q.paused || q.reply ? { ...q, paused: false, reply: "" } : q));
}

// The running reply takes steered messages under `reply` (the stream's
// `{"steer"}` line).
export function steerTo(key, reply) {
  change(key, (q) => ({ ...q, reply }));
}

// The reply ended: what it did not take in is queued again, and the queue
// goes on (`ok`) or pauses (stopped, failed).
export function ended(key, ok) {
  change(key, (q) => {
    const steering = q.items.some((m) => m.state === "steering");
    if (!steering && q.paused === !ok && !q.reply) return q;
    return { items: steering ? q.items.map((m) => ({ ...m, state: "queued" })) : q.items, paused: !ok, reply: "" };
  });
}

// The next message to send now, taken off the queue; null while paused or
// empty.
export function next(key) {
  const q = queueOf(key);
  const item = !q.paused && q.items.find((m) => m.state === "queued");
  if (!item) return null;
  remove(key, item.id);
  return item;
}

// --- what a message carries ---------------------------------------------

const SHOWN_QUOTE = 280;    // characters of the quoted passages a sent message's bubble shows
const STEER_MAX = 30_000;   // characters a steered message carries (gamma/ai_steer.py MAX_TEXT)

// The passages a message quotes: PDF selections, the selection in the note
// the cursor is in, the notes attached (their text).
export function quotesOf(parts = {}) {
  return [...(parts.selections || []).map((s) => s.text), parts.cursor?.sel?.text,
    ...(parts.notes || []).map((n) => n.text)].filter(Boolean);
}

// A sent message's text as its bubble shows it: the message, then the start
// of what it quotes.
export function quotedText(text, quotes) {
  const quoted = quotes.join("\n\n---\n\n");
  return quoted ? `${text}\n\n${quoteMarkdown(`${quoted.slice(0, SHOWN_QUOTE)}${quoted.length > SHOWN_QUOTE ? "…" : ""}`)}` : text;
}

// What a message says as it goes: the transcript's passages it quotes
// (chat/chatQuotes.js), then what the user wrote.
export const messageText = (text, parts = {}) => withChatQuotes(String(text || "").trim(), parts?.quotes);

// Whether Steer can carry the message: its text, pictures and quoted
// passages go into the reply; files, attached blocks and handwriting only
// go with a message of their own.
export function canSteer(item) {
  const parts = item?.parts || {};
  return !parts.files?.length && (parts.notes || []).every((n) => n.kind === "note");
}

// The steer request's body (POST /api/ai/chat/steer/{reply}): the text with
// the passages it quotes in full, and its pictures.
export function steerBody(item) {
  const quoted = quotesOf(item.parts).map((q) => q.split("\n").map((line) => `> ${line}`).join("\n")).join("\n\n---\n\n");
  const said = messageText(item.text, item.parts);
  const text = quoted ? `${said}\n\n${quoted}` : said;
  return { id: item.id, text: text.slice(0, STEER_MAX), images: item.parts?.images || [] };
}

// The user's message the transcript shows where the reply took steered
// messages in: their texts as sent messages read, their pictures.
export function steeredMessage(items, id) {
  const images = items.flatMap((m) => m.parts?.images || []);
  return {
    id, role: "user", steered: true,
    text: items.map((m) => quotedText(messageText(m.text, m.parts), quotesOf(m.parts))).join("\n\n"),
    ...(images.length ? { images } : {}),
  };
}

// The conversation's queue, `key` "" for none.
export function useChatQueue(key) {
  const subscribe = useCallback((listener) => { heard.add(listener); return () => heard.delete(listener); }, []);
  return useSyncExternalStore(subscribe, () => (key ? queueOf(key) : EMPTY));
}
