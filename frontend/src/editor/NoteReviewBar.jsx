// The bar under a note that shows an AI's change as tracked changes
// (chat/noteReview.js; editor/mdDiff.js draws the change). An edit waiting
// on its approval card: the card's four answers, which answer the card
// (Don't allow without the card's line for what to do instead). An edit
// that ran without asking: a record, nothing waits on it: Revert (taken back
// as its row in the chat would take it, once the reply ended), Hide (the
// note keeps its text) and, with others on the page, Hide all. A revert that
// stops says why, with the button that reverts anyway where it can be forced.
import React, { useState } from "react";
import { t, tn } from "../shared/i18n/i18n.js";
import { EyeOffIcon, ShieldIcon, SparklesIcon, UndoIcon } from "../shared/ui/Icons";
import { noteReview, usePageChanges, useStillReplying } from "../chat/noteReview.js";

export function NoteReviewBar({ change, ask, onStatus }) {
  // {working} while a request runs, {refused: {text, force}} once a revert stopped
  const [state, setState] = useState(null);
  const onPage = usePageChanges(change?.pageId ?? "");
  // The reply that made it still streams: its Revert waits, as its row's does.
  const replying = useStillReplying(change);
  const busy = !!state?.working;
  // The bar's own presses are not the row's, which would open the editor.
  const bar = (label, children) => (
    <div className="noteReviewBar" role="group" aria-label={label} onMouseDown={(e) => e.stopPropagation()}>
      {children}
    </div>
  );
  if (ask) {
    const decide = async (decision) => {
      setState({ working: true });
      try {
        await ask.decide(decision);
        noteReview.settle(ask.id);
      } catch (err) {
        setState(null);
        onStatus?.(err?.message || String(err));
      }
    };
    const { permission, kind } = ask;
    return bar(t("Approval needed"), <>
      <span className="noteReviewLabel"><ShieldIcon size={14} />{t("Waiting for your approval")}</span>
      <button type="button" className="uiBtn sm primary" disabled={busy} onClick={() => decide("once")}>{t("Allow once")}</button>
      <button type="button" className="uiBtn sm" disabled={busy} onClick={() => decide("chat")}
        title={t("Don't ask again for “{permission}” in this conversation", { permission })}>
        {t("Allow in this chat")}
      </button>
      <button type="button" className="uiBtn sm" disabled={busy} onClick={() => decide("always")}
        title={t("Set “{permission}” to Allow for all chats of this kind ({kind}). You can change it in Settings → AI → Tool usage.", { permission, kind })}>
        {t("Always allow")}
      </button>
      <button type="button" className="uiBtn sm ghost" disabled={busy} onClick={() => decide("deny")}>{t("Don't allow")}</button>
    </>);
  }
  const revert = async (force = false) => {
    setState({ working: true });
    try {
      const refused = await noteReview.revert(change.blockId, { force });
      setState(refused ? { refused } : null);
    } catch (err) {
      setState(null);
      onStatus?.(err?.message || String(err));
    }
  };
  if (state?.refused) {
    const { text, force } = state.refused;
    return bar(t("AI change"), <>
      <span className="noteReviewLabel" role="alert">{text}</span>
      {force ? <button type="button" className="uiBtn sm" disabled={busy || replying} onClick={() => revert(true)}>{force}</button> : null}
      <button type="button" className="uiBtn sm ghost" onClick={() => setState(null)}>{force ? t("Keep it") : t("Dismiss")}</button>
    </>);
  }
  return bar(t("AI change"), <>
    <span className="noteReviewLabel"><SparklesIcon size={14} />{change.before ? t("Edited by AI") : t("Added by AI")}</span>
    <button type="button" className="uiBtn sm ghost" disabled={busy || replying} title={t("Revert this change")} onClick={() => revert()}>
      {busy ? <span className="transferSpin inline" aria-hidden="true" /> : <UndoIcon size={14} />}{t("Revert")}
    </button>
    <button type="button" className="uiBtn sm ghost" disabled={busy} title={t("Stop marking this change; the note keeps it")}
      onClick={() => noteReview.hide(change.blockId)}>
      <EyeOffIcon size={14} />{t("Hide")}
    </button>
    {onPage > 1 ? (
      <button type="button" className="uiBtn sm ghost" disabled={busy}
        title={tn("Stop marking the AI's change on this page", "Stop marking the AI's {n} changes on this page", onPage)}
        onClick={() => noteReview.hidePage(change.pageId)}>
        {t("Hide all")}
      </button>
    ) : null}
  </>);
}
