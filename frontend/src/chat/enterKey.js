// The Return key in the chat's text fields, decided here once: the composer,
// an edit and re-send, the history's rename, a hand-off card's note and a
// comment's field. An Enter an input method is composing with is the input
// method's (`composing`, shared/lib/hotkeys.js).
import { composing } from "../shared/lib/hotkeys.js";
import { touchTyping } from "../shared/lib/pointer.js";

// Whether an Enter sends the message being written. With a keyboard, Enter
// sends and Shift+Enter breaks the line. On an on-screen keyboard Return is
// the only line break there is, so it breaks the line and the Send button
// sends. Ctrl/⌘+Enter sends on either, for a tablet with a keyboard on it.
export function sendsOnEnter(e, onScreen = touchTyping()) {
  if (e.key !== "Enter" || e.shiftKey || composing(e)) return false;
  return !onScreen || e.ctrlKey || e.metaKey;
}

// Whether an Enter is the other way of sending a follow-up (below):
// Ctrl/⌘+Enter, on any keyboard, as Codex's.
export function othersOnEnter(e) {
  return e.key === "Enter" && (e.ctrlKey || e.metaKey) && !composing(e);
}

// Whether a message sent while a reply runs is handed to it at once (Steer,
// chat/chatQueue.js) rather than queued for when it ends. `followUp` the
// preference (Settings → AI → Chat, "steer", the default, or "queue"):
// Enter, the send button and a dictation's send do what it says, and
// Ctrl/⌘+Enter (`other`) the other one.
export function followUpSteers(followUp, other = false) {
  return (followUp !== "queue") !== other;
}
