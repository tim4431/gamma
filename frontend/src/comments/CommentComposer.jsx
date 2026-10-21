// The field a comment is written in (a new thread, a reply, an edit): the
// note editor (editor/BlockCmEditor.jsx) with a note's typing aids — chips
// for [[links]], @people and dates, inline marks and math drawn in place,
// the "[[" link picker (editor/useRefPicker.js), the "@" menu without
// reminders or "Invite…" (editor/useMentionMenu.js), the "/" menu of the
// commands that write text (editor/useSlashMenu.js: no page, sheet, upload
// or embed), and inside $…$ the live preview and \command completion
// (editor/useMathUi.js). Return breaks the line as in a note, continuing a
// list or quote; Shift+Return or Ctrl/⌘+Return sends (chat/enterKey.js
// sendsOnShiftEnter). An open picker, menu or completion takes
// ↑ ↓ Enter Tab and Escape first; Escape without one is the caller's
// (`onCancel` for an edit, else `onEscape`: the popover closes). The field
// keeps its own undo history. Unsent text is kept by `draftKey` for as long
// as the tab lives, so closing the popover (a press outside it, Escape,
// another block's comments) loses none of it.
import React, { useContext, useEffect, useRef, useState } from "react";
import { ArrowUpIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";
import { chordLabel } from "../shared/lib/hotkeys.js";
import { touchTyping } from "../shared/lib/pointer.js";
import { composing, sendsOnShiftEnter } from "../chat/enterKey.js";
import { BlockCmEditor } from "../editor/BlockCmEditor";
import { findMathAtCursor, LatexAcPopup, MathLivePreview } from "../editor/LatexEditor";
import { MentionContext, MentionMenuPopup } from "../editor/MentionMenu";
import { RefPickerPopup } from "../editor/RefPicker";
import { SlashMenuPopup } from "../editor/SlashMenu";
import { fenceInnerAt } from "../editor/fences.js";
import { enterPlan } from "../editor/mdLines.js";
import { NO_LABELS } from "../editor/refLabels.js";
import { useMathUi } from "../editor/useMathUi.js";
import { useMentionMenu } from "../editor/useMentionMenu.js";
import { useRefPicker } from "../editor/useRefPicker.js";
import { useSlashMenu } from "../editor/useSlashMenu.js";
import { MAX_COMMENT } from "./comments.js";

// The unsent texts by field: `new:<block id>`, `reply:<thread id>`,
// `edit:<comment id>`. Module state: the popover remounts, the tab keeps them.
const drafts = new Map();
export const hasDraft = (key) => drafts.has(key);

const NO_LINKS = {};
// What the "/" menu's commands may need that a comment lacks: it makes no
// page or sheet, uploads no picture and embeds no note (slashCommands.js).
const COMMENT_ABILITIES = {};

// A key the app reads as a note editor's when it comes from one: the undo
// keys (the page's block history; this field has its own) and F2 (rename
// the page). They stay with the field.
const fieldKey = (e) => e.key === "F2"
  || ((e.ctrlKey || e.metaKey) && !e.altKey && ["z", "y"].includes(e.key.toLowerCase()));

// onSubmit(text) → a promise of whether it was stored: the field empties
// at once and gets the text back when it was refused. `initial`: an edit's
// text; `onCancel` shows Cancel beside the button (an edit) and drops its
// draft. `draftKey`: where the field's unsent text is kept. `blockId`: the
// block the thread is on, left out of the link picker's blocks. `links`:
// App's {pages, rootId (the open page), lookupBlock, refCache, onCacheRef}
// for the pickers; `labelsFor(text)`: the labels of the [[links]] in a text.
export function CommentComposer({ initial = "", placeholder, submitLabel, autoFocus = false, people = [], me = "",
  draftKey = "", blockId = "", links = NO_LINKS, labelsFor, onSubmit, onCancel, onEscape }) {
  const [text, setText] = useState(() => (draftKey && drafts.has(draftKey) ? drafts.get(draftKey) : initial));
  useEffect(() => {
    if (!draftKey) return;
    if (text.trim() && text !== initial) drafts.set(draftKey, text);
    else drafts.delete(draftKey);
  }, [draftKey, text, initial]);
  const cancel = onCancel ? () => { if (draftKey) drafts.delete(draftKey); onCancel(); } : null;
  const ref = useRef(null);
  const mentions = useContext(MentionContext);
  const { pages, rootId, lookupBlock, refCache, onCacheRef } = links;
  const { mathUi, setMathUi, mathAcIdx, updateMathUi, acceptCompletion, acKey, tabJumpKey } = useMathUi();
  const refPicker = useRefPicker({
    editorRef: ref, excludeId: blockId, pages, rootId, lookupBlock, refCache, onCacheRef,
    write: (edit) => {
      ref.current?.view?.dispatch({
        changes: { from: edit.from, to: edit.to, insert: edit.insert },
        selection: { anchor: edit.caret },
        userEvent: "input",
      });
      ref.current?.focus();
    },
  });
  const atPicker = useMentionMenu({ editorRef: ref, people, me, pages, rootId, reminders: false, onCacheRef });
  // A command's text replaces the field's in one edit, which the field's
  // undo takes back; "Link to note" opens this field's [[ picker.
  const slash = useSlashMenu({
    editorRef: ref, refPicker, can: COMMENT_ABILITIES,
    setText: (newVal, selStart, selEnd) => {
      const ta = ref.current;
      if (!ta?.view) return;
      ta.view.dispatch({
        changes: { from: 0, to: ta.view.state.doc.length, insert: newVal },
        selection: { anchor: selStart, head: selEnd ?? selStart },
        userEvent: "input",
      });
      ta.focus();
      updateMathUi(ta, false);
    },
  });

  // Focused a frame after mounting: a popover stands hidden until it is
  // placed, and a hidden field takes no focus (so no `autoFocus`). Not on
  // an on-screen keyboard, which the focus would raise.
  useEffect(() => {
    if (!autoFocus || touchTyping()) return undefined;
    const id = requestAnimationFrame(() => {
      const ta = ref.current;
      if (!ta?.view || ta.view.hasFocus) return; // focused already: its caret stays
      ta.focus();
      ta.setSelectionRange(ta.value.length);
    });
    return () => cancelAnimationFrame(id);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const closePopups = () => {
    refPicker.close();
    atPicker.close();
    slash.close();
    setMathUi(null);
  };
  const trimmed = text.trim();
  const tooLong = text.length > MAX_COMMENT;
  // The button's hover names its key ("Shift+Enter", "⇧↩" on a Mac); on
  // an on-screen keyboard, where Shift+Return breaks the line, Ctrl/⌘+Enter
  // (sendsOnShiftEnter).
  const sendTitle = t("{label} ({hint})", { label: submitLabel, hint: chordLabel(touchTyping() ? "Mod-Enter" : "Shift-Enter") });
  const submit = async () => {
    if (!trimmed || tooLong) return;
    const sent = text;
    setText("");
    closePopups();
    const ok = await onSubmit(sent);
    if (!ok) setText((now) => now || sent);
  };

  const onKeyDown = (e) => {
    if (composing(e)) return; // the input method's
    const ta = ref.current;
    // An open picker, menu or completion first; the key goes no further
    // (the popover's Escape, the app's keys).
    if (refPicker.keyDown(e) || slash.keyDown(e) || atPicker.keyDown(e) || acKey(e, ta)) { e.stopPropagation(); return; }
    if (tabJumpKey(e, ta)) return;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      if (cancel) cancel(); else onEscape?.();
      return;
    }
    if (fieldKey(e)) { e.stopPropagation(); return; }
    if (e.key !== "Enter" || !ta) return;
    if (sendsOnShiftEnter(e)) { e.preventDefault(); submit(); return; }
    // A line break: a list or quote continues (mdLines.enterPlan), as in a
    // note, but not inside a code fence or $$…$$; otherwise the editor's own.
    const at = ta.selectionStart;
    const block = !!fenceInnerAt(ta.value, at) || !!findMathAtCursor(ta.value, at)?.display;
    const plan = !block && at === ta.selectionEnd ? enterPlan(ta.value, at) : null;
    if (plan) {
      e.preventDefault();
      ta.view?.dispatch({
        changes: { from: plan.from, to: plan.to, insert: plan.insert },
        selection: { anchor: plan.pos },
        userEvent: plan.insert.startsWith("\n") ? "input" : "delete",
      });
    }
  };

  return (
    <div className="commentComposer">
      <div className="commentBox">
        <BlockCmEditor
          ref={ref}
          ownHistory
          className="blockEditorCm commentField"
          value={text}
          placeholder={placeholder}
          ariaLabel={placeholder}
          refLabels={labelsFor ? labelsFor(text) : NO_LABELS}
          mentions={mentions}
          onChange={(e) => {
            setText(e.target.value);
            refPicker.onEdit(e);
            updateMathUi(e.target, true);
            slash.update(e.target, true);
            atPicker.update(e.target, true);
          }}
          onSelect={(e) => { updateMathUi(e.target, false); slash.update(e.target, false); atPicker.update(e.target, false); }}
          onBlur={() => {
            setMathUi(null);
            slash.close();
            atPicker.close();
            setTimeout(refPicker.close, 120);
          }}
          onKeyDown={onKeyDown}
        />
        {/* A new comment or a reply sends from the round button inside the
            box, beside the text (its last line once it wraps), as the chat's
            composer does; an edit saves from the bar under it. The title
            names the key, the name stays the plain action. */}
        {cancel ? null : (
          <button type="button" className="uiBtn primary chatCircleBtn commentSend" disabled={!trimmed || tooLong}
            onMouseDown={(e) => e.preventDefault()} onClick={submit} title={sendTitle} aria-label={submitLabel}>
            <ArrowUpIcon size={12} />
          </button>
        )}
      </div>
      {tooLong || cancel ? (
        <div className="commentComposerBar">
          {tooLong ? <span className="commentTooLong">{t("At most {n} characters", { n: MAX_COMMENT.toLocaleString() })}</span> : null}
          <span className="commentComposerSpacer" />
          {cancel ? <button type="button" className="uiBtn sm ghost" onClick={cancel}>{t("Cancel")}</button> : null}
          {cancel ? (
            <button type="button" className="uiBtn sm primary commentSend" disabled={!trimmed || tooLong}
              onMouseDown={(e) => e.preventDefault()} onClick={submit} title={sendTitle} aria-label={submitLabel}>
              {submitLabel}
            </button>
          ) : null}
        </div>
      ) : null}
      {mathUi ? (
        <>
          <MathLivePreview tex={mathUi.tex} display={mathUi.display} anchor={mathUi.previewAnchor} caret={mathUi.caret} />
          {mathUi.ac ? (
            <LatexAcPopup items={mathUi.ac.items} selected={mathAcIdx} anchor={mathUi.anchor}
              onPick={(c) => acceptCompletion(ref.current, c)} />
          ) : null}
        </>
      ) : null}
      {slash.popup ? <SlashMenuPopup {...slash.popup} /> : null}
      {atPicker.shown ? (
        <MentionMenuPopup rows={atPicker.atMenu.rows} selected={atPicker.selected} anchor={atPicker.atMenu.anchor}
          query={atPicker.atMenu.query} onPick={atPicker.pick} />
      ) : null}
      {refPicker.shown ? (
        <RefPickerPopup rows={refPicker.rows} selected={refPicker.selected} anchor={refPicker.refPopup.anchor}
          query={refPicker.refPopup.query} partial={refPicker.partial} onPick={refPicker.insert} />
      ) : null}
    </div>
  );
}
