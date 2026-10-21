// The [[ link picker of a CodeMirror editor (BlockCmEditor): a note row's
// and a comment's field. Typing "[[" opens it at the "[[", the query is
// what follows; its rows are pages by title, then blocks from the server's
// search (editor/refLists.js, RefPicker.jsx). A picked row puts [[id]] in
// place of the typed "[[query" and caches the chip's label at once; "]]"
// typed by hand after a page's exact title links that page.
//
// editorRef: the editor's textarea-like facade. excludeId: the block being
// written in or about, left out of the blocks. pages, rootId (the open
// page, left out of the pages), lookupBlock, refCache: App's. write(edit):
// puts a picked row's edit ({from, to, insert, value, caret}) in the editor.
import { useEffect, useMemo, useState } from "react";
import { API, withShare } from "../shared/lib/utils";
import { t } from "../shared/i18n/i18n.js";
import { guideEvents } from "../guide/events.js";
import { pageKindLabel } from "../library/libraryUtils";
import { pickerCounts, rankRefPages, refBlockPath, refBlockText, refInsertEdit, refQueryAt, typedTitleLink } from "./refLists.js";

export function useRefPicker({ editorRef, excludeId, pages, rootId, lookupBlock, refCache, onCacheRef, write }) {
  // { query, anchor } while a "[[" is being typed.
  const [refPopup, setRefPopup] = useState(null);
  const [refSelectedIdx, setRefSelectedIdx] = useState(0);
  const [searchResults, setSearchResults] = useState([]);
  // The server's block scan ran out of time: the picker keeps what it found
  // and its footer says to type more.
  const [searchPartial, setSearchPartial] = useState(false);

  useEffect(() => {
    if (!refPopup) { setSearchResults([]); setSearchPartial(false); return; }
    const q = refPopup.query;
    const timer = setTimeout(async () => {
      try {
        const res = await fetch(withShare(`${API}/block-search?q=${encodeURIComponent(q)}&limit=12`));
        const data = await res.json();
        setSearchResults((data.blocks || []).filter((b) => b.id !== excludeId));
        setSearchPartial(!!data.partial);
      } catch (_) { setSearchResults([]); setSearchPartial(false); }
    }, 120);
    return () => clearTimeout(timer);
  }, [refPopup?.query, excludeId]);
  // The picker's rows (editor/refLists.js): pages by title, then blocks
  // other than pages (a page result is a page row) as plain text.
  const rows = useMemo(() => {
    if (!refPopup) return [];
    const labelOf = (id) => pages?.find((p) => p.id === id)?.content ?? lookupBlock?.(id)?.content ?? refCache?.[id]?.content;
    const pageHits = rankRefPages(pages, refPopup.query, rootId);
    const blockHits = searchResults.filter((b) => b.page_root_id !== b.id);
    const [np, nb] = pickerCounts(pageHits.length, blockHits.length);
    return [
      ...pageHits.slice(0, np).map((p) => ({
        kind: "page", id: p.id, title: p.content, isPdf: !!p._attachment,
        meta: [pageKindLabel(p._attachment), p._folderChips?.[0]?.name].filter(Boolean).join(" · "),
      })),
      ...blockHits.slice(0, nb).map((b) => ({
        kind: "block", id: b.id, title: refBlockText(b.content, labelOf) || t("(empty)"), meta: refBlockPath(b), block: b,
      })),
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refPopup?.query, pages, searchResults, rootId]);
  const shown = !!refPopup && rows.length > 0;
  const selected = Math.min(refSelectedIdx, rows.length - 1);
  useEffect(() => { if (shown) guideEvents.emit("ref.search"); }, [shown]);

  // Opens the picker with no query yet, at `anchor` (the "/" menu's "Link").
  function open(anchor) {
    setRefPopup({ query: "", anchor });
    setRefSelectedIdx(0);
  }
  const close = () => setRefPopup(null);

  // The editor's change ({target, selectionBefore}): a "[[query" before the
  // caret opens or follows the picker, anything else closes it, and one "]"
  // typed to close a [[title]] links it if a page has exactly that title.
  function onEdit(e) {
    const ta = e.target;
    const cursor = ta.selectionStart;
    const before = ta.value.slice(0, cursor);
    const query = refQueryAt(before);
    if (query != null) {
      setRefPopup({ query, anchor: ta.caretCoords(cursor - query.length - 2) });
      setRefSelectedIdx(0);
      return;
    }
    setRefPopup(null);
    const typed = e.selectionBefore?.from === cursor - 1 && e.selectionBefore?.to === cursor - 1;
    if (typed && before.endsWith("]]")) linkTypedTitle(ta.value, cursor);
  }

  // A picked row: the typed "[[query" becomes [[id]] (a "!" before it
  // stays, making it an embed), and the chip's label is cached at once.
  function insert(r) {
    const ta = editorRef.current;
    if (!ta) return;
    const edit = refInsertEdit(ta.value, ta.selectionStart, r.id);
    if (!edit) return;
    write(edit);
    if (onCacheRef) onCacheRef(r.id, r.kind === "page" ? { content: r.title, page_title: r.title } : r.block);
    setRefPopup(null);
  }

  // "]]" typed by hand after a page's exact title: the text becomes that
  // page's [[id]] (only when exactly one page has the title; otherwise it
  // stays text and renders as an unlinked chip). After the keystroke's own
  // update, and only if the text is still what was typed.
  function linkTypedTitle(value, cursor) {
    const link = typedTitleLink(value, cursor, pages);
    if (!link) return;
    const typed = value.slice(link.from, cursor);
    requestAnimationFrame(() => {
      const ta = editorRef.current;
      if (!ta?.view || ta.value.slice(link.from, cursor) !== typed) return;
      onCacheRef?.(link.page.id, { content: link.page.content, page_title: link.page.content });
      // No selection given: the caret maps through the change, so text typed
      // since the "]]" stays where it was typed.
      ta.view.dispatch({ changes: { from: link.from, to: cursor, insert: link.insert }, userEvent: "input" });
    });
  }

  // The picker's keys while it shows: ↑ ↓ choose, Enter or Tab insert,
  // Escape closes it. True when the key was taken.
  function keyDown(e) {
    if (!shown) return false;
    if (e.key === "ArrowDown") { e.preventDefault(); setRefSelectedIdx((i) => Math.min(i + 1, rows.length - 1)); return true; }
    if (e.key === "ArrowUp") { e.preventDefault(); setRefSelectedIdx((i) => Math.max(i - 1, 0)); return true; }
    if (e.key === "Enter" || (e.key === "Tab" && !e.shiftKey)) { e.preventDefault(); insert(rows[selected]); return true; }
    if (e.key === "Escape") { e.preventDefault(); setRefPopup(null); return true; }
    return false;
  }

  return { refPopup, rows, shown, selected, partial: searchPartial, open, close, onEdit, insert, keyDown };
}
