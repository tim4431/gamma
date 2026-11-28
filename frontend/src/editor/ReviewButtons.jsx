// The buttons of an agent's change where it is drawn (chat/noteReview.js;
// the diff itself is editor/mdDiff.js's in a note, markup/ReviewLayer.jsx's
// frame on a page): small icon buttons grouped in one pill at the right
// edge of its row or its frame, apart from the row's own tools, as
// Overleaf's suggestion card has them.
//
// - A suggestion (made while the account reviews): ✓ Accept and ✗ Reject,
//   their tooltips naming the agent and when. When the note no longer says
//   what the agent wrote (`changedSince`: someone typed in it), the
//   tooltips say so; Reject then merges, as ever.
// - The change a press on its row in the chat shows: Hide alone (an eye
//   struck through); its Undo stays on the chat's row.
//
// A Reject that stops says why in a small card under the buttons, with what
// rejecting anyway would write and the button that does it where it can be
// forced; an Accept that fails says why too. Both are the store's
// (`useRefusal`), so every place that draws the block, its row in the notes
// and its frame on a page, says it alike.
import React, { useState } from "react";
import { fmtMoment, t } from "../shared/i18n/i18n.js";
import { CheckIcon, EyeOffIcon, XIcon } from "../shared/ui/Icons";
import { noteReview, useRefusal } from "../chat/noteReview.js";
import { stampLabel } from "../chat/agents.js";
import { WordDiff } from "../chat/ApprovalShell";
import { refusalCopy, revertRefusal } from "../chat/aiRevert.js";

// How a Reject that stopped reads by the buttons: the chat row's words, and
// the diff of what rejecting anyway would write.
export const refusedOnBar = (err) => {
  const refusal = revertRefusal(err);
  const copy = refusalCopy(refusal, false);
  return { text: copy.text, force: copy.force || "", diff: refusal.preview?.diff || null };
};

// The agent that made a change, as the app names one (chat/agents.js
// stampLabel: with the person it acted for when that is not the viewer).
const agentOf = (change) => stampLabel(change.stamp) || change.agent || t("Gamma AI");

// Who made a change, by its kind (chat/noteReview.js noteChanges' type).
export function changedBy(change) {
  const agent = agentOf(change);
  switch (change.type) {
    case "edit": return t("Edited by {agent}", { agent });
    case "move": return t("Moved by {agent}", { agent });
    case "delete": return t("Deleted by {agent}", { agent });
    default: return t("Added by {agent}", { agent });
  }
}

// The buttons' presses are not the row's or the page's, which would open an
// editor, start a drag or a stroke, or hide the shown change.
const stop = (e) => e.stopPropagation();

// `change`: chat/noteReview.js useNoteChange's; `blockId` the block it is
// drawn on (its own by default).
export function ReviewButtons({ change, blockId = change.blockId, changedSince = false }) {
  const [busy, setBusy] = useState(false);
  const refusal = useRefusal(blockId);
  // Accept and Reject resolve with why they stopped, which the store holds
  // and the card then shows (`refusal`); neither throws.
  const run = async (fn) => {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  };
  const reject = (force = false) => run(() => noteReview.rejectBlock(blockId, { force, refused: refusedOnBar }));
  const what = changedSince ? t("Changed by {agent}, and edited since", { agent: agentOf(change) }) : changedBy(change);
  const who = [what, change.at ? fmtMoment(change.at) : ""].filter(Boolean).join(" · ");
  return (
    <div className="reviewBtns" role="group" aria-label={change.shown ? t("AI change") : t("AI suggestion")}
      data-guide="notes.review" onMouseDown={stop} onPointerDown={stop} onClick={stop} onDoubleClick={stop}>
      {change.shown ? (
        <button type="button" className="ctlBtn" title={t("Hide the change: {change}", { change: who })}
          aria-label={t("Hide the change")} onClick={() => noteReview.hide()}>
          <EyeOffIcon size={14} />
        </button>
      ) : <>
        <button type="button" className="ctlBtn reviewAccept" disabled={busy} title={t("Accept: {change}", { change: who })}
          aria-label={t("Accept")} onClick={() => run(() => noteReview.acceptBlock(blockId))}>
          <CheckIcon size={14} />
        </button>
        <button type="button" className="ctlBtn danger" disabled={busy} title={t("Reject: {change}", { change: who })}
          aria-label={t("Reject")} onClick={() => reject()}>
          <XIcon size={14} />
        </button>
      </>}
      {refusal ? (
        <div className="reviewRefusal" role="alert">
          <span>{refusal.text}</span>
          {refusal.diff?.length ? <WordDiff diff={refusal.diff} /> : null}
          <span className="reviewRefusalActions">
            {refusal.force ? (
              <button type="button" className="uiBtn sm" disabled={busy} onClick={() => reject(true)}>{refusal.force}</button>
            ) : null}
            <button type="button" className="uiBtn sm ghost" onClick={() => noteReview.clearRefusal(blockId)}>
              {refusal.force ? t("Keep it") : t("Dismiss")}
            </button>
          </span>
        </div>
      ) : null}
    </div>
  );
}
