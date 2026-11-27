// The bar of an agent's change where it lands (chat/noteReview.js; the
// diff itself is editor/mdDiff.js's in a note, markup/ReviewLayer.jsx's on
// a page). Three kinds, one look:
//
// - A call waiting on its approval card (`AskBar`): what it would do ("Gamma
//   AI wants to delete this note") and the card's answers — Keep, its
//   grants in the menu beside it, and Undo, which opens the card's line for
//   what to do instead. They answer the card: the card and the drawing are
//   one question. A ghost row and a shape on a page carry the same bar. An
//   assistant over MCP asks by its name ("Codex wants to…"), and its one
//   grant is its connection's: Keep, and don't ask again.
// - A change made without asking, waiting for review: who made it, Keep and
//   Undo. When the note no longer says what the agent wrote
//   (`changedSince`: someone typed in it), the change is not drawn and the
//   bar says so; Undo then merges, as ever.
// - A kept change unfolded from its mark (or from its row in the chat):
//   who made it, Undo, and Fold, which folds it back to its mark.
//
// An Undo that stops says why, with what undoing anyway would write and the
// button that does it where it can be forced; a Keep that fails says why
// too. Both are the store's (`useRefusal`), so every place that draws the
// block, its note and its frame on a page, says it alike.
import React, { useState } from "react";
import { t, tn } from "../shared/i18n/i18n.js";
import { CheckIcon, ChevronUpIcon, ShieldIcon, SparklesIcon, UndoIcon } from "../shared/ui/Icons";
import { CHANGE_STATES, noteReview, useRefusal } from "../chat/noteReview.js";
import { remoteAnswers, toolAnswers } from "../chat/approvals.js";
import { ApprovalAnswers, WordDiff, useApprovalAnswer } from "../chat/ApprovalShell";
import { refusalCopy, revertRefusal } from "../chat/aiRevert.js";

// The bar's own presses are not the row's or the page's, which would open
// an editor or start a drag.
const bar = (label, children, { guide } = {}) => (
  <div className="noteReviewBar" role="group" aria-label={label} data-guide={guide}
    onMouseDown={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()}>
    {children}
  </div>
);

// How an Undo that stopped reads on the bar: the chat row's words, and the
// diff of what undoing anyway would write.
export const refusedOnBar = (err) => {
  const refusal = revertRefusal(err);
  const copy = refusalCopy(refusal, false);
  return { text: copy.text, force: copy.force || "", diff: refusal.preview?.diff || null };
};

// Who made a change, by its kind (chat/noteReview.js noteChanges' type),
// for its bar and, with `show`, the title of its mark (a note's row, a
// placed block on its page).
export function changedBy(change, { show = false } = {}) {
  const agent = change.agent || t("Gamma AI");
  if (change.type === "edit") return show ? t("Edited by {agent}: show the change", { agent }) : t("Edited by {agent}", { agent });
  if (change.type === "move") return show ? t("Moved by {agent}: show the change", { agent }) : t("Moved by {agent}", { agent });
  return show ? t("Added by {agent}: show the change", { agent }) : t("Added by {agent}", { agent });
}

// What a waiting call would do, in a sentence by its tool. A move is drawn
// on the note's page; one to another page names that page.
function askWords(ask) {
  const agent = ask.agent || t("Gamma AI");
  const p = ask.preview || {};
  switch (ask.tool) {
    case "delete_block": return p.children
      ? tn("{agent} wants to delete this note, with the {n} note under it", "{agent} wants to delete this note, with the {n} notes under it",
        p.children, { agent })
      : t("{agent} wants to delete this note", { agent });
    case "move_block":
      if (p.src_page_id) {
        const title = p.title || t("Untitled");
        return p.parent ? t("{agent} wants to move this note under “{parent}” in “{title}”", { agent, parent: p.parent, title })
          : t("{agent} wants to move this note to “{title}”", { agent, title });
      }
      return p.parent ? t("{agent} wants to move this note under “{parent}”", { agent, parent: p.parent })
        : t("{agent} wants to move this note to the page's top level", { agent });
    case "create_block": return (p.count || 1) > 1
      ? tn("{agent} wants to add {n} note here", "{agent} wants to add {n} notes here", p.count, { agent })
      : t("{agent} wants to add this note", { agent });
    case "draw": return t("{agent} wants to draw this", { agent });
    case "add_text_box": return t("{agent} wants to add this text box", { agent });
    case "place_picture": return t("{agent} wants to place this picture", { agent });
    case "highlight": return t("{agent} wants to highlight this", { agent });
    default: return t("{agent} wants to make this change", { agent });
  }
}

// The answers of the card `ask` (noteReview.ask), its own for each card;
// an assistant's over MCP its own (chat/approvals.js remoteAnswers).
export function AskBar({ ask }) {
  const answer = useApprovalAnswer(async (decision, note) => {
    await ask.decide(decision, note);
    noteReview.settle(ask.id);
  });
  const call = { tool: ask.tool, perm: ask.perm };
  return bar(t("Approval needed"), <>
    <span className="noteReviewLabel"><ShieldIcon size={14} />{askWords(ask)}</span>
    <ApprovalAnswers answer={answer} call={call} agent={ask.agent}
      answers={ask.remote ? remoteAnswers({ tool: ask.tool, agent: ask.agent })
        : toolAnswers({ ...call, permission: ask.permission, kind: ask.kind })} />
  </>);
}

// The bar of a change made (`change`, chat/noteReview.js withChange):
// waiting for review, or kept and unfolded from its mark.
export function NoteReviewBar({ change, changedSince = false }) {
  const [busy, setBusy] = useState(false);
  const refusal = useRefusal(change.blockId);
  const waiting = change.state === CHANGE_STATES.OPEN;
  const agent = change.agent || t("Gamma AI");
  // Keep and Undo resolve with why they stopped, which the store holds and
  // the bar then shows (`refusal`); neither throws.
  const run = async (fn) => {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  };
  const undo = (force = false) => run(() => noteReview.undoBlock(change.blockId, { force, refused: refusedOnBar }));
  if (refusal) {
    return bar(t("AI change"), <>
      <span className="noteReviewLabel" role="alert">{refusal.text}</span>
      {refusal.diff?.length ? <WordDiff diff={refusal.diff} /> : null}
      {refusal.force ? <button type="button" className="uiBtn sm" disabled={busy} onClick={() => undo(true)}>{refusal.force}</button> : null}
      <button type="button" className="uiBtn sm ghost" onClick={() => noteReview.clearRefusal(change.blockId)}>
        {refusal.force ? t("Keep it") : t("Dismiss")}
      </button>
    </>);
  }
  const label = changedSince ? t("Changed by {agent}, and edited since", { agent }) : changedBy(change);
  return bar(t("AI change"), <>
    <span className="noteReviewLabel"><SparklesIcon size={14} />{label}</span>
    {waiting ? (
      <button type="button" className="uiBtn sm" disabled={busy} title={t("Keep this change")}
        onClick={() => run(() => noteReview.keepBlock(change.blockId))}>
        <CheckIcon size={14} />{t("Keep")}
      </button>
    ) : null}
    <button type="button" className="uiBtn sm ghost" disabled={busy} title={t("Undo this change")} onClick={() => undo()}>
      {busy ? <span className="transferSpin inline" aria-hidden="true" /> : <UndoIcon size={14} />}{t("Undo")}
    </button>
    {waiting ? null : (
      <button type="button" className="uiBtn sm ghost" title={t("Fold the change back to its mark")}
        onClick={() => noteReview.toggle(change.blockId, false)}>
        <ChevronUpIcon size={14} />{t("Fold")}
      </button>
    )}
  </>, { guide: "notes.review" });
}
