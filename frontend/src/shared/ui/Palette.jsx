// The palette: Ctrl+P's dialog (library/QuickOpen.jsx), which the page
// pickers (library/PagePicker.jsx: Move to page, Move into page, the PDF's
// link dialog) are too. A field near the top of the window over a list of
// options in sections, a line under them saying what a pick does and the
// keys. ↑↓ move the selection, round from the last to the first, Enter takes
// it, Esc closes, and so does a press on the backdrop (backdropPress). While
// it is open the field keeps the focus: a page that finishes loading right
// after it opened may focus its editor, and the keys meant for the palette
// would land in the page. Its classes are quick open's (library.css).
//
// `rows` are the options in order, each {key, section?}: a row whose section
// is not the one before it opens under `sectionTitle[section]`.
// `renderRow(row, props)` draws one, spreading `props` (its option role, the
// selection, hover and click) on its button. `onChoose(row)` takes a pick.
// `onKeyDown(e, row)` sees the field's keys first and returns true for one it
// took (quick open's Ctrl+Enter and Shift+Enter). The selection goes back to
// the first row whenever the query changes. `note` (what a pick does) and
// `hint` (the keys) make the line under the list.

import React, { useEffect, useRef, useState } from "react";
import { DialogOverlay } from "./Surfaces";
import { backdropPress } from "./press.js";

export default function Palette({
  label, listLabel, icon, placeholder, guide, query, onQuery, rows, sectionTitle = {}, renderRow,
  onChoose, onKeyDown, onClose, empty, note, hint,
}) {
  const [active, setActive] = useState(0);
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const at = Math.min(active, rows.length - 1);

  useEffect(() => {
    const hold = (e) => {
      const input = inputRef.current;
      if (input && !input.closest('[role="dialog"]')?.contains(e.target)) input.focus();
    };
    document.addEventListener("focusin", hold, true);
    return () => document.removeEventListener("focusin", hold, true);
  }, []);
  useEffect(() => { setActive(0); }, [query]);
  useEffect(() => {
    listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [at]);

  const choose = (row) => { if (row) onChoose(row); };
  const optionProps = (row, i) => ({
    type: "button", role: "option", tabIndex: -1,
    "aria-selected": i === at,
    className: `slashMenuItem chatMentionOption quickOpenRow${i === at ? " selected" : ""}`,
    onPointerDown: (e) => e.preventDefault(),
    onMouseEnter: () => setActive(i),
    onClick: () => choose(row),
  });
  return (
    <DialogOverlay className="quickOpenOverlay" {...backdropPress(onClose)}>
      <div className="reportModal quickOpen" role="dialog" aria-label={label}>
        <div className="quickOpenInput" data-guide={guide}>
          {icon}
          <input
            ref={inputRef}
            autoFocus
            className="searchInput"
            placeholder={placeholder}
            aria-label={placeholder}
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.nativeEvent.isComposing || e.keyCode === 229) return;
              if (onKeyDown?.(e, rows[at])) return;
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                setActive((i) => (Math.min(i, rows.length - 1) + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % (rows.length || 1));
              } else if (e.key === "Enter") {
                e.preventDefault();
                choose(rows[at]);
              } else if (e.key === "Escape") {
                e.preventDefault();
                onClose();
              }
            }}
          />
        </div>
        <div ref={listRef} className="quickOpenList" role="listbox" aria-label={listLabel}>
          {rows.map((row, i) => (
            <React.Fragment key={row.key}>
              {row.section && row.section !== rows[i - 1]?.section && sectionTitle[row.section] ? (
                <div className="quickOpenSection" role="presentation">{sectionTitle[row.section]}</div>
              ) : null}
              {renderRow(row, optionProps(row, i))}
            </React.Fragment>
          ))}
          {!rows.length && <div className="popoverHint">{empty}</div>}
        </div>
        <div className="chatMentionHint quickOpenHint">
          {note ? <span className="quickOpenNote">{note}</span> : null}
          <span>{hint}</span>
        </div>
      </div>
    </DialogOverlay>
  );
}
