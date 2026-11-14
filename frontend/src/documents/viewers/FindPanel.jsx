// The code view's Find (docs/dev/ui-design.md "The code view"): the panel
// CodeMirror's search opens at the top of the view (search({createPanel})),
// drawn as the app's find row, the search popover's and the chat's pieces:
// the field in its box (.searchInputWrap, the fields' focus ring), the count
// with the arrows (.searchNavBtn), the toggles (.searchToggle: Match case,
// Match whole word, Regular expression) and the round ×. It drives the
// view's search state: a change to the field or a toggle says the query
// (setSearchQuery) and goes to the first match from the selection, or from
// the top of the view when the selection is out of sight; Enter and
// Shift+Enter, or the arrows, go to the next and the previous match; the ×
// closes it. CodeMirror's keys for its panel (Ctrl+F, F3, Ctrl+G, Escape)
// run in it as in the text.
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { EditorSelection } from "@codemirror/state";
import { EditorView, runScopeHandlers } from "@codemirror/view";
import { SearchQuery, closeSearchPanel, findNext, findPrevious, getSearchQuery, setSearchQuery } from "@codemirror/search";
import { ChevronDownIcon, ChevronUpIcon, XIcon } from "../../shared/ui/Icons";
import { chordLabel, composing } from "../../shared/lib/hotkeys.js";
import { t } from "../../shared/i18n/i18n.js";

// The most matches counted; past it the count reads "1000+".
const MAX_COUNT = 1000;

// The panel search({createPanel}) asks for: an empty box, handed to the
// viewer (`onBox`, a state setter) as it mounts, which draws FindPanel into
// it, and taken back as it goes.
export const findPanelOf = (onBox) => () => {
  const dom = document.createElement("div");
  return { dom, top: true, mount: () => onBox(dom), destroy: () => onBox((was) => (was === dom ? null : was)) };
};

// The query's matches, {list: [{from, to}], more} with at most MAX_COUNT
// listed; null for no query or a regular expression that does not parse.
function matchesOf(state, query) {
  if (!query.valid) return null;
  const list = [];
  const cursor = query.getCursor(state);
  for (let m = cursor.next(); !m.done; m = cursor.next()) {
    if (list.length === MAX_COUNT) return { list, more: true };
    list.push({ from: m.value.from, to: m.value.to });
  }
  return { list, more: false };
}

// The selection on the query's first match from where it stands, or from
// the top of the view when it stands out of sight; wraps to the start.
function seek(view, query) {
  if (!query.valid) return;
  const { state } = view, sel = state.selection.main;
  const from = view.visibleRanges.some((r) => sel.from >= r.from && sel.from <= r.to) ? sel.from
    : view.lineBlockAtHeight(view.scrollDOM.scrollTop).from;
  let hit = query.getCursor(state, from).next();
  if (hit.done) hit = query.getCursor(state, 0, from).next();
  if (hit.done) return;
  const range = EditorSelection.range(hit.value.from, hit.value.to);
  view.dispatch({ selection: EditorSelection.create([range]), effects: EditorView.scrollIntoView(range), userEvent: "select.search" });
}

// `view` the EditorView; `updates` the viewer's set of update listeners.
export default function FindPanel({ view, updates }) {
  // The view's state as the row last read it: again as the text, the
  // selection or the query changes.
  const [cmState, setCmState] = useState(view.state);
  useEffect(() => {
    const fn = (u) => {
      if (u.docChanged || u.selectionSet || u.transactions.some((tr) => tr.effects.some((e) => e.is(setSearchQuery)))) setCmState(u.state);
    };
    updates.add(fn);
    return () => { updates.delete(fn); };
  }, [updates]);
  const query = getSearchQuery(cmState);
  const matches = useMemo(() => matchesOf(cmState, query), [cmState.doc, query]);
  const sel = cmState.selection.main;
  const place = matches ? matches.list.findIndex((m) => m.from === sel.from && m.to === sel.to) + 1 : 0;
  const total = matches?.list.length || 0;

  const inputRef = useRef(null);
  useLayoutEffect(() => { inputRef.current?.focus(); inputRef.current?.select(); }, []);
  const commit = (change) => {
    const next = new SearchQuery({ search: query.search, caseSensitive: query.caseSensitive, wholeWord: query.wholeWord,
      regexp: query.regexp, literal: query.literal, ...change });
    if (next.eq(query)) return;
    view.dispatch({ effects: setSearchQuery.of(next) });
    seek(view, next);
  };
  const onKeyDown = (e) => {
    if (composing(e)) return;
    if (runScopeHandlers(view, e.nativeEvent, "search-panel")) e.preventDefault();
    else if (e.key === "Enter" && e.target === inputRef.current) {
      e.preventDefault();
      (e.shiftKey ? findPrevious : findNext)(view);
    }
  };
  const toggle = (key, label, glyph) => (
    <button type="button" className={`searchToggle${query[key] ? " on" : ""}`} aria-pressed={query[key]} title={label}
      onClick={() => commit({ [key]: !query[key] })}>{glyph}</button>
  );
  // The row hears its controls' keys, as CodeMirror's own panel does.
  return (
    <div className="docFindRow" onKeyDown={onKeyDown}>
      <div className="searchInputWrap">
        <input ref={inputRef} className="searchInput" main-field="true" value={query.search}
          placeholder={t("Find in file…")} aria-label={t("Find")}
          autoCapitalize="off" autoCorrect="off" spellCheck={false}
          onChange={(e) => commit({ search: e.target.value })} />
      </div>
      <span className="searchNavGroup">
        <span className="searchFindCount">{matches ? `${place}/${total}${matches.more ? "+" : ""}` : ""}</span>
        <button type="button" className="searchToggle searchNavBtn" disabled={!total} onClick={() => findPrevious(view)}
          title={t("{label} ({hint})", { label: t("Previous match"), hint: chordLabel("Shift-Enter") })} aria-label={t("Previous match")}>
          <ChevronUpIcon size={14} />
        </button>
        <button type="button" className="searchToggle searchNavBtn" disabled={!total} onClick={() => findNext(view)}
          title={t("{label} ({hint})", { label: t("Next match"), hint: chordLabel("Enter") })} aria-label={t("Next match")}>
          <ChevronDownIcon size={14} />
        </button>
      </span>
      {toggle("caseSensitive", t("Match case"), t("Aa"))}
      {toggle("wholeWord", t("Match whole word"), <u>ab</u>)}
      {toggle("regexp", t("Regular expression"), ".*")}
      <button type="button" className="uiClose uiCloseSm" onClick={() => closeSearchPanel(view)}
        title={t("{label} ({hint})", { label: t("Close find"), hint: chordLabel("Escape") })} aria-label={t("Close find")}>
        <XIcon size={14} />
      </button>
    </div>
  );
}
