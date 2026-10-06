import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import { AutoGrowTextarea } from "../shared/ui/Widgets";
import PageOption, { recencySections } from "../library/PageOption";
import { rankLibraryPages } from "../library/librarySearch";
import { insertMention, mentionAt, MAX_CHAT_REFERENCES } from "./paperMentions";
import { composing, sendsOnEnter } from "./enterKey.js";
import { t } from "../shared/i18n/i18n.js";

// The chat composer, with the picker "@" opens over it: quick open's page
// list (rankLibraryPages' rows drawn by PageOption) for the text after the
// "@". `tree`, `recentViews` and `openTabs` are what that ranking reads;
// `selected` the attached page ids, checked in the list.
export default function PaperMentionInput({
  value, onChange, pages, tree, recentViews, openTabs, currentPageId, selected, onAttach, onSend, ...props
}) {
  const input = useRef(null);
  const list = useRef(null);
  const listId = useId();
  const dismissed = useRef(null);
  const [mention, setMention] = useState(null);
  const [active, setActive] = useState(0);
  const results = useMemo(
    () => (mention ? rankLibraryPages(pages, tree, mention.query, { recentViews, openTabs }) : []),
    [pages, tree, recentViews, openTabs, mention?.query, !!mention],
  );
  useEffect(() => { setActive(0); }, [mention?.query]);
  useEffect(() => { list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" }); }, [active]);
  useEffect(() => { if (!value) { setMention(null); dismissed.current = null; } }, [value]);
  const scan = (el) => {
    const next = mentionAt(el.value, el.selectionStart, el.selectionEnd);
    if (next?.start === dismissed.current) { setMention(null); return; }
    dismissed.current = null;
    setMention(next);
  };
  const full = selected.length >= MAX_CHAT_REFERENCES;
  const canAttach = (page) => !!page && (!full || selected.includes(page.id));
  const choose = (page) => {
    if (!mention || !canAttach(page)) return;
    onAttach(page.id);
    const next = insertMention(value, mention, page.content || t("Untitled"));
    onChange(next.text);
    setMention(null);
    requestAnimationFrame(() => { input.current?.focus(); input.current?.setSelectionRange(next.caret, next.caret); });
  };
  const sectionTitle = recencySections();
  return <div className="chatMentionInput">
    {mention && <div className="chatMentionPicker">
      <div className="chatMentionHeading">{t("Mention a library page")} <span>{t("↑↓ choose · Enter add · Esc close")}</span></div>
      <div ref={list} id={listId} role="listbox" aria-label={t("Library pages")}>
        {results.map((r, i) => (
          <React.Fragment key={r.page.id}>
            {r.section !== results[i - 1]?.section && sectionTitle[r.section]
              ? <div className="quickOpenSection" role="presentation">{sectionTitle[r.section]}</div> : null}
            <PageOption row={r} tree={tree} query={mention.query} currentPageId={currentPageId}
              checked={selected.includes(r.page.id)}
              type="button" role="option" id={`${listId}-${i}`} tabIndex={-1}
              aria-selected={i === active} aria-disabled={!canAttach(r.page)}
              className={`slashMenuItem chatMentionOption${i === active ? " selected" : ""}`}
              onPointerDown={(e) => e.preventDefault()} onMouseEnter={() => setActive(i)} onClick={() => choose(r.page)} />
          </React.Fragment>
        ))}
        {!results.length && <div className="popoverHint">{pages.length ? t("No matching pages.") : t("No pages yet.")}</div>}
      </div>
      <div className="chatMentionHint">{full ? t("Up to {MAX_CHAT_REFERENCES} attached pages. Remove one to add another.", { MAX_CHAT_REFERENCES }) : t("Adds paper details and text to chat context. Tools can read more.")}</div>
    </div>}
    <AutoGrowTextarea {...props} ref={input} value={value} role="combobox" aria-label={t("Message AI")}
      aria-autocomplete="list" aria-expanded={!!mention} aria-controls={mention ? listId : undefined}
      aria-activedescendant={mention && results[active] ? `${listId}-${active}` : undefined}
      onChange={(e) => { onChange(e.target.value); scan(e.target); }}
      onSelect={(e) => scan(e.target)} onBlur={() => setMention(null)}
      onKeyDown={(e) => {
        if (composing(e)) return;
        if (mention) {
          if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); dismissed.current = mention.start; setMention(null); return; }
          if (["ArrowDown", "ArrowUp"].includes(e.key)) {
            e.preventDefault(); setActive((i) => (i + (e.key === "ArrowDown" ? 1 : -1) + results.length) % (results.length || 1)); return;
          }
          if ((e.key === "Enter" && !e.shiftKey) || (e.key === "Tab" && !e.shiftKey && canAttach(results[active]?.page))) {
            e.preventDefault(); choose(results[active]?.page); return;
          }
        }
        if (sendsOnEnter(e)) { e.preventDefault(); onSend(); }
      }} />
  </div>;
}
