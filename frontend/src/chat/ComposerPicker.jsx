// The picker the chat composer opens above its field (chat/
// ChatComposerInput.jsx): "@"'s library pages and "/"'s skills, one shell
// and one set of key rules for both. The field keeps the focus and is the
// combobox; the rows are the listbox's options, which a press never
// focuses.
import React, { useEffect, useRef } from "react";
import { touchTyping } from "../shared/lib/pointer.js";

// What a key pressed in the field does while a picker is open (an input
// method's Enter is the caller's to leave alone): "close" (Escape), "up" /
// "down", "pick", or null for the field's own meaning. The selection keys
// are taken only while the picker lists a row (`selectable`; a row that
// cannot be added takes them and does nothing), so with none listed
// Enter sends the text as typed; Ctrl/⌘+Enter keeps its steer meaning; on
// an on-screen keyboard Return keeps its line break, and a tap or Tab
// picks (chat/enterKey.js).
export function pickerKey(e, selectable, onScreen = touchTyping()) {
  if (e.key === "Escape") return "close";
  if (!selectable) return null;
  if (e.key === "ArrowDown") return "down";
  if (e.key === "ArrowUp") return "up";
  if (e.key === "Tab" && !e.shiftKey) return "pick";
  if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !onScreen) return "pick";
  return null;
}

// The shell: a heading with its key hints, the listbox (`id`, named by
// `label`) holding the rows (`children`, each role="option" with its id
// `${id}-${i}`), and a line under it (`foot`). `active` is the row the
// keys are on, kept in view.
export default function ComposerPicker({ id, label, heading, keys, foot = null, active, children }) {
  const list = useRef(null);
  useEffect(() => { list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" }); }, [active]);
  return (
    <div className="chatMentionPicker">
      <div className="chatMentionHeading">{heading} <span>{keys}</span></div>
      <div ref={list} id={id} role="listbox" aria-label={label}>{children}</div>
      {foot ? <div className="chatMentionHint">{foot}</div> : null}
    </div>
  );
}
