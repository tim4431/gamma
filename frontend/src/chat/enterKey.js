// The Return key in the chat's text fields, decided here once: the composer,
// an edit and re-send, the history's rename and a hand-off card's note; and
// in a comment's field, which has it the other way round. An Enter an input
// method is composing with is the input method's (`composing`,
// shared/lib/hotkeys.js).
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

// Whether an Enter sends a comment (comments/CommentComposer.jsx). The field
// is a note's editor holding one block, so Enter breaks the line and Shift+Enter
// sends. On an on-screen keyboard Return breaks the line, Shift or not, and
// the button sends. Ctrl/⌘+Enter sends on either.
export function sendsOnShiftEnter(e, onScreen = touchTyping()) {
  if (e.key !== "Enter" || composing(e)) return false;
  return e.ctrlKey || e.metaKey || (e.shiftKey && !onScreen);
}
