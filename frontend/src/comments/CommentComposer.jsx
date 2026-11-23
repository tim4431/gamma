// The field a comment is written in (a new thread, a reply, an edit): the
// note editor (editor/BlockCmEditor.jsx) with a note's typing aids — chips
// for [[links]], @people and dates, inline marks and math drawn in place,
// the "[[" link picker (editor/useRefPicker.js), the "@" menu without
// reminders or "Invite…" but with the agents a comment can be sent to
// (editor/useMentionMenu.js), the "/" menu of the
// commands that write text (editor/useSlashMenu.js: no page, sheet, upload
// or embed), and inside $…$ the live preview and \command completion
// (editor/useMathUi.js). Return sends, as in the chat; Shift+Return breaks
// the line as in a note, continuing a list or quote (chat/enterKey.js
// sendsOnEnter: on an on-screen keyboard Return breaks the line and the
// button sends). An open picker, menu or completion takes
// ↑ ↓ Enter Tab and Escape first; Escape without one is the caller's
// (`onCancel` for an edit, else `onEscape`: the popover closes). The field
// keeps its own undo history. Unsent text is kept by `draftKey` for as long
// as the tab lives, so closing the popover (a press outside it, Escape,
// another block's comments) loses none of it. A new comment's and a
// reply's field has the *Send to {agent}* tick under it while an agent can
// be sent the comment (comments/sendTo.js): ticked, the comment is also
// the instruction of a turn of that agent, which the send button's name
// and the placeholder say ("Send to Codex", "Ask Codex…"); `@codex` typed
// ticks it.
import React, { useContext, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpIcon, BotIcon } from "../shared/ui/Icons";
import { MenuSelect } from "../shared/ui/Menus";
import { t } from "../shared/i18n/i18n.js";
import { chordLabel, composing } from "../shared/lib/hotkeys.js";
import { touchTyping } from "../shared/lib/pointer.js";
import { sendsOnEnter } from "../chat/enterKey.js";
import { BlockCmEditor } from "../editor/BlockCmEditor";
import { renderNoteTable } from "../editor/BlockTree";
import { LatexAcPopup, MathLivePreview } from "../editor/LatexEditor";
import { MentionContext, MentionMenuPopup } from "../editor/MentionMenu";
import { RefPickerPopup } from "../editor/RefPicker";
import { SlashMenuPopup } from "../editor/SlashMenu";
import { codeBreak } from "../editor/blockCommands.js";
import { enterPlan } from "../editor/mdLines.js";
import { NO_LABELS } from "../editor/refLabels.js";
import { useMathUi } from "../editor/useMathUi.js";
import { useMentionMenu } from "../editor/useMentionMenu.js";
import { useRefPicker } from "../editor/useRefPicker.js";
import { useSlashMenu } from "../editor/useSlashMenu.js";
import { chatPick } from "../chat/computer/chatSide.js";
import { MAX_COMMENT } from "./comments.js";
import { guideEvents } from "../guide/events.js";
import { agentName, editHint, namedAgent, sendLabel, sendNote, startsTicked, tickAgent, tickedPlaceholder } from "./sendTo.js";

// The unsent texts by field: `new:<block id>`, `reply:<thread id>`,
// `edit:<comment id>`. Module state: the popover remounts, the tab keeps them.
const drafts = new Map();
export const hasDraft = (key) => drafts.has(key);

const NO_LINKS = {};
const NO_AGENTS = [];
// What the "/" menu's commands may need that a comment lacks: it makes no
// page or sheet, uploads no picture and embeds no note (slashCommands.js).
const COMMENT_ABILITIES = {};

// A key the app reads as a note editor's when it comes from one: the undo
// keys (the page's block history; this field has its own) and F2 (rename
// the page). They stay with the field.
const fieldKey = (e) => e.key === "F2"
  || ((e.ctrlKey || e.metaKey) && !e.altKey && ["z", "y"].includes(e.key.toLowerCase()));

// onSubmit(text, to) → a promise of whether it was stored, `to` the id of
// the agent it is sent to, "" for none (the tick shown and unticked), null
// for a field with no tick (an edit, no agents: the server reads an
// `@name`): the field empties at once and gets the text back when it was
// refused. `initial`: an edit's text; `onCancel`
// shows Cancel beside the button (an edit, which has no tick: an `@name` it
// adds is read by the server) and drops its draft. `draftKey`: where the
// field's unsent text is kept. `blockId`: the block the thread is on, left
// out of the link picker's blocks; `thread`: the thread a reply goes in
// (null for a new one). `links`: App's {pages, rootId (the open page),
// lookupBlock, refCache, onCacheRef, agents, sendTo} for the pickers,
// `agents` the agents a comment on the page can be sent to (chat/agents.js
// commentAgents): the @ menu lists them, the field chips them and ticks
// for them; `sendTo` {ticked, remember(ticked)}: how the person left the
// tick last (an account preference) and keeping a change of it.
// `refused`: for an edit the server will not send to an agent, its code
// (Thread.jsx), said there instead; `labelsFor(text)`: the labels of the
// [[links]] in a text.
export function CommentComposer({ initial = "", placeholder, submitLabel, autoFocus = false, people = [], me = "",
  draftKey = "", blockId = "", thread = null, links = NO_LINKS, refused = "", labelsFor, onSubmit, onCancel, onEscape }) {
  const [text, setText] = useState(() => (draftKey && drafts.has(draftKey) ? drafts.get(draftKey) : initial));
  useEffect(() => {
    if (!draftKey) return;
    if (text.trim() && text !== initial) drafts.set(draftKey, text);
    else drafts.delete(draftKey);
  }, [draftKey, text, initial]);
  const cancel = onCancel ? () => { if (draftKey) drafts.delete(draftKey); onCancel(); } : null;
  const ref = useRef(null);
  const { pages, rootId, lookupBlock, refCache, onCacheRef, agents = NO_AGENTS, sendTo = null } = links;
  // Whom the field's chips name: the page's people, and the agents.
  const named = useContext(MentionContext);
  const mentions = useMemo(() => (agents.length ? { ...named, agents } : named), [named, agents]);
  // The tick (an edit has none): as the person set it, else its default
  // (sendTo.js startsTicked); the agent it names, as chosen in its menu,
  // else its default (tickAgent). `@codex` typed ticks it for that agent.
  const ticks = !onCancel && agents.length > 0;
  // Written in, a field that can ask an agent says so once (the guide's
  // Send to hint, which points at this field's tick).
  const [wrote, setWrote] = useState(false);
  const [tick, setTick] = useState(null);
  const [chosen, setChosen] = useState("");
  const ticked = ticks && (tick ?? startsTicked(thread, sendTo?.ticked, me));
  const target = ticks ? tickAgent(agents, { thread, picked: chatPick(rootId), chosen }) : null;
  const toggle = (on) => {
    setTick(on);
    sendTo?.remember(on);
  };
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
  const atPicker = useMentionMenu({ editorRef: ref, people, me, pages, rootId, reminders: false, agents, onCacheRef });
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
  // Ticked, what may keep the agent from answering; an edit's, what saving
  // an `@name` it adds does.
  const hint = onCancel ? editHint({ agents, text, initial, refused }) : ticked ? sendNote(target) : null;
  // The button's name is its action, ticked "Send to Codex" (sendTo.js
  // sendLabel); its hover names its key too ("Enter", "↩" on a Mac), on an
  // on-screen keyboard, where Return breaks the line, Ctrl/⌘+Enter
  // (sendsOnEnter).
  const send = sendLabel(ticked ? target : null, submitLabel, chordLabel(touchTyping() ? "Mod-Enter" : "Enter"));
  const fieldLabel = ticked ? tickedPlaceholder(target, placeholder, !!thread) : placeholder;
  const submit = async () => {
    if (!trimmed || tooLong) return;
    const sent = text;
    setText("");
    closePopups();
    const ok = await onSubmit(sent, ticks ? (ticked && target ? target.id : "") : null);
    if (!ok) setText((now) => now || sent);
  };
  const edited = (value) => {
    setText(value);
    const said = ticks ? namedAgent(agents, value, text) : null;
    if (said) { setTick(true); setChosen(said.id); }
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
    if (sendsOnEnter(e)) { e.preventDefault(); submit(); return; }
    // A line break: a list or quote continues (mdLines.enterPlan), as in a
    // note, but not inside a code fence or $$…$$, where only a quote's marks
    // go on; otherwise the editor's own.
    const at = ta.selectionStart;
    const code = codeBreak(ta.value, at, ta.selectionEnd);
    const plan = code === "\n" ? null
      : code ? { from: at, to: ta.selectionEnd, insert: code, pos: at + code.length }
      : at === ta.selectionEnd ? enterPlan(ta.value, at) : null;
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
          placeholder={fieldLabel}
          ariaLabel={fieldLabel}
          refLabels={labelsFor ? labelsFor(text) : NO_LABELS}
          renderTable={renderNoteTable}
          mentions={mentions}
          onChange={(e) => {
            if (ticks && !wrote) {
              setWrote(true);
              guideEvents.emit("comment.agentOffered");
            }
            edited(e.target.value);
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
            composer does; an edit saves from the bar under it. The name is
            the action (ticked, the agent it starts), the title adds the key. */}
        {cancel ? null : (
          <button type="button" className="uiBtn primary chatCircleBtn commentSend" disabled={!trimmed || tooLong}
            onMouseDown={(e) => e.preventDefault()} onClick={submit} title={send.title} aria-label={send.label}>
            {/* ui-allow icon-size: the 24 px send circle's arrow (ui-design.md, the comments row of the file map) */}
            <ArrowUpIcon size={12} />
          </button>
        )}
      </div>
      {tooLong || cancel || hint || ticks ? (
        <div className="commentComposerBar">
          {ticks ? (
            <span className="commentSendTo" data-send-to={ticked ? target?.name : ""} data-guide={wrote ? "comments.sendTo" : undefined}>
              <label className="commentSendTick">
                <input type="checkbox" checked={ticked} onChange={(e) => toggle(e.target.checked)}
                  aria-label={t("Send to {agent}", { agent: agentName(target) })} />
                {agents.length > 1 ? t("Send to") : t("Send to {agent}", { agent: agentName(target) })}
              </label>
              {/* the agent a choice inside the line's words: frameless, each with its glyph */}
              {agents.length > 1 ? (
                <MenuSelect up ghost label={t("Agent")} value={target?.id || ""} onChange={(id) => { setChosen(id); toggle(true); }}
                  options={agents.map((a) => [a.id, agentName(a), BotIcon])} />
              ) : null}
            </span>
          ) : null}
          {tooLong ? <span className="commentTooLong">{t("At most {n} characters", { n: MAX_COMMENT.toLocaleString() })}</span>
            : hint ? <span className={`commentAgentHint${hint.warn ? " agentProblem" : ""}`} data-agent-hint>{hint.text}</span> : null}
          <span className="commentComposerSpacer" />
          {cancel ? <button type="button" className="uiBtn sm ghost" onClick={cancel}>{t("Cancel")}</button> : null}
          {cancel ? (
            <button type="button" className="uiBtn sm primary commentSend" disabled={!trimmed || tooLong}
              onMouseDown={(e) => e.preventDefault()} onClick={submit} title={send.title} aria-label={submitLabel}>
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
