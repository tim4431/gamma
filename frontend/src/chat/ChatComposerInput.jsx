import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import { AutoGrowTextarea } from "../shared/ui/Widgets";
import PageOption, { recencySections } from "../library/PageOption";
import { rankLibraryPages } from "../library/librarySearch";
import { Matched } from "../editor/LatexEditor";
import { insertMention, mentionAt, MAX_CHAT_REFERENCES } from "./paperMentions";
import { insertSkill, rankSkills, skillAt, skillDescription, skillHint } from "./skills.js";
import ComposerPicker, { pickerKey } from "./ComposerPicker.jsx";
import { composing } from "../shared/lib/hotkeys.js";
import { othersOnEnter, sendsOnEnter } from "./enterKey.js";
import { t } from "../shared/i18n/i18n.js";

// The chat composer, with the pickers it opens over it (ComposerPicker):
// "@" anywhere, quick open's page list (rankLibraryPages' rows drawn by
// PageOption) for the text after the "@", and "/" as the message's first
// character, the skills (chat/skills.js) for the word after it.
// `tree`, `recentViews` and `openTabs` are what the page ranking reads;
// `selected` the attached page ids, checked in the list. `mentions` false
// (a computer's agent, which takes no attached pages) opens no page
// picker; `skills` the skills a message can run (null while unknown or
// where none are offered: no skill picker), `onManageSkills` opens
// Settings → Chat at Your skills from the picker that matched none; `label`
// names the field; `onSteer`, while a reply runs, takes Ctrl/⌘+Enter, the
// other way of sending a follow-up than Enter (chat/enterKey.js
// othersOnEnter, followUpSteers).
export default function ChatComposerInput({
  value, onChange, pages, tree, recentViews, openTabs, currentPageId, selected, onAttach, onSend, onSteer = null, mentions = true,
  skills = null, onManageSkills = null, label = "",
  ...props
}) {
  const input = useRef(null);
  const listId = useId();
  // The picker Escape closed, by its kind and where its trigger is: it
  // stays shut there.
  const dismissed = useRef("");
  const [open, setOpen] = useState(null); // {kind: "page" | "skill", start, end, query}
  const [active, setActive] = useState(0);
  const offersSkills = !!skills?.length;
  const pageRows = useMemo(
    () => (open?.kind === "page" ? rankLibraryPages(pages, tree, open.query, { recentViews, openTabs }) : []),
    [pages, tree, recentViews, openTabs, open?.kind, open?.query],
  );
  const skillRows = useMemo(() => (open?.kind === "skill" ? rankSkills(skills, open.query) : []), [skills, open?.kind, open?.query]);
  useEffect(() => { setActive(0); }, [open?.kind, open?.query]);
  useEffect(() => { if (!value) { setOpen(null); dismissed.current = ""; } }, [value]);
  // No skills left (the catalog read again) closes "/"'s.
  useEffect(() => { if (!offersSkills) setOpen((was) => (was?.kind === "skill" ? null : was)); }, [offersSkills]);
  const scan = (el) => {
    const skill = offersSkills ? skillAt(el.value, el.selectionStart, el.selectionEnd) : null;
    const page = !skill && mentions ? mentionAt(el.value, el.selectionStart, el.selectionEnd) : null;
    const next = skill ? { kind: "skill", ...skill } : page ? { kind: "page", ...page } : null;
    if (next && `${next.kind}:${next.start}` === dismissed.current) { setOpen(null); return; }
    dismissed.current = "";
    setOpen(next);
  };
  const full = selected.length >= MAX_CHAT_REFERENCES;
  const canAttach = (page) => !!page && (!full || selected.includes(page.id));
  const rows = open?.kind === "skill" ? skillRows : pageRows;
  // A listed row takes Enter even when it cannot be added (the sixth page
  // is the limit): a pick, never a send. With no row listed Enter sends.
  const selectable = rows.length > 0;
  // The caret after what a pick inserted (`next`: insertMention's or
  // insertSkill's), once React has put the text in; keys typed before that
  // frame keep their own caret, so a quick typist's letters stay in order.
  const putCaret = (next) => requestAnimationFrame(() => {
    const el = input.current;
    if (!el) return;
    el.focus();
    if (el.value === next.text) el.setSelectionRange(next.caret, next.caret);
  });
  const choosePage = (page) => {
    if (open?.kind !== "page" || !canAttach(page)) return;
    onAttach(page.id);
    const next = insertMention(value, open, page.content || t("Untitled"));
    onChange(next.text);
    setOpen(null);
    putCaret(next);
  };
  const chooseSkill = (row) => {
    if (open?.kind !== "skill" || !row) return;
    const next = insertSkill(value, open, row.name);
    onChange(next.text);
    setOpen(null);
    putCaret(next);
  };
  const choose = (i) => (open?.kind === "skill" ? chooseSkill(skillRows[i]) : choosePage(pageRows[i]?.page));
  const sectionTitle = recencySections();
  const optionProps = (i) => ({
    role: "option", id: `${listId}-${i}`, tabIndex: -1, "aria-selected": i === active,
    onPointerDown: (e) => e.preventDefault(), onMouseEnter: () => setActive(i), onClick: () => choose(i),
  });
  return <div className="chatMentionInput">
    {open?.kind === "page" ? (
      <ComposerPicker id={listId} label={t("Library pages")} active={active}
        heading={t("Mention a library page")} keys={t("↑↓ choose · Enter add · Esc close")}
        foot={full ? t("Up to {MAX_CHAT_REFERENCES} attached pages. Remove one to add another.", { MAX_CHAT_REFERENCES }) : t("Adds paper details and text to chat context. Tools can read more.")}>
        {pageRows.map((r, i) => (
          <React.Fragment key={r.page.id}>
            {r.section !== pageRows[i - 1]?.section && sectionTitle[r.section]
              ? <div className="quickOpenSection" role="presentation">{sectionTitle[r.section]}</div> : null}
            <PageOption row={r} tree={tree} query={open.query} currentPageId={currentPageId}
              checked={selected.includes(r.page.id)} type="button" {...optionProps(i)}
              aria-disabled={!canAttach(r.page)}
              className={`slashMenuItem chatMentionOption${i === active ? " selected" : ""}`} />
          </React.Fragment>
        ))}
        {!pageRows.length && <div className="popoverHint">{pages.length ? t("No matching pages.") : t("No pages yet.")}</div>}
      </ComposerPicker>
    ) : open?.kind === "skill" ? (
      <ComposerPicker id={listId} label={t("Skills")} active={active}
        heading={t("Run a skill")} keys={t("↑↓ choose · Enter or Tab insert · Esc close")}
        foot={skillRows.length ? t("Its instructions go with the message, and what you write after its name is what it works on.") : null}>
        {skillRows.map((row, i) => (
          <button key={row.id} type="button" {...optionProps(i)}
            className={`slashMenuItem chatMentionOption chatSkillOption${i === active ? " selected" : ""}`}>
            <span className="slashMenuText">
              <span className="slashMenuHead">
                <span className="slashMenuLabel">/<Matched text={row.name} at={row.match?.name} /></span>
                {row.source === "builtin" ? <span className="uiTag">{t("Built-in")}</span> : null}
                {row.argument_hint ? <span className="slashMenuName">{skillHint(row)}</span> : null}
              </span>
              {row.description ? (
                <span className="slashMenuHint"><Matched text={skillDescription(row)} at={row.match?.description} /></span>
              ) : null}
            </span>
          </button>
        ))}
        {!skillRows.length ? (
          <div className="popoverHint chatSkillNone">
            {t("No skill matches “/{query}”", { query: open.query })}
            {onManageSkills ? (
              <button type="button" className="chatEmptyLink" onPointerDown={(e) => e.preventDefault()}
                onClick={() => { setOpen(null); onManageSkills(); }}>{t("Manage skills…")}</button>
            ) : null}
          </div>
        ) : null}
      </ComposerPicker>
    ) : null}
    <AutoGrowTextarea {...props} ref={input} value={value} aria-label={label || t("Message AI")}
      {...(mentions || offersSkills ? { role: "combobox", "aria-autocomplete": "list", "aria-expanded": !!open } : {})}
      aria-controls={open ? listId : undefined}
      aria-activedescendant={open && rows[active] ? `${listId}-${active}` : undefined}
      onChange={(e) => { onChange(e.target.value); scan(e.target); }}
      onSelect={(e) => scan(e.target)} onBlur={() => setOpen(null)}
      onKeyDown={(e) => {
        if (composing(e)) return;
        const key = open ? pickerKey(e, selectable) : null;
        if (key === "close") { e.preventDefault(); e.stopPropagation(); dismissed.current = `${open.kind}:${open.start}`; setOpen(null); return; }
        if (key === "up" || key === "down") {
          e.preventDefault(); setActive((i) => (i + (key === "down" ? 1 : -1) + rows.length) % (rows.length || 1)); return;
        }
        if (key === "pick") { e.preventDefault(); choose(active); return; }
        if (onSteer && othersOnEnter(e)) { e.preventDefault(); onSteer(); }
        else if (sendsOnEnter(e)) { e.preventDefault(); onSend(); }
      }} />
  </div>;
}
