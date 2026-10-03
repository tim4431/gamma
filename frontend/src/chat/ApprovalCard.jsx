// The card a tool call waits on while the user decides (chat/approvals.js
// has the rules, gamma/ai_permissions.py the server's side). It says what
// the call would do, shows the change as a word diff (or the folder a page
// moves to), and offers four answers. Allow once runs it. Allow in this chat
// also stops asking for this permission in this conversation. Always allow
// also sets the permission to Allow for this kind of chat in Settings.
// Don't allow opens a line for what to do instead (optional) and leaves the
// call unmade; the assistant gets that line with the decline. The buttons
// never take the focus by themselves, so a keystroke meant for the message
// box can't answer.
import React, { useEffect, useState } from "react";
import { t } from "../shared/i18n/i18n.js";
import { ShieldIcon } from "../shared/ui/Icons";
import { ALLOWING, approvalTitle } from "./approvals.js";
import { permissionIcon, permissionLabel } from "../settings/AssistantTools";
import { guideEvents } from "../guide/events.js";

// A server word diff (`[[kind, text], …]`, ai_tools.text_diff): what goes
// struck out, what comes in tinted. The revert prompt shows one too
// (chat/AgentChanges.jsx).
export function WordDiff({ diff }) {
  return <div className="chatApprovalDiff">
    {diff.map(([kind, text], i) => (kind === "del" ? <del key={i}>{text}</del>
      : kind === "ins" ? <ins key={i}>{text}</ins> : <span key={i}>{text}</span>))}
  </div>;
}

function ApprovalPreview({ preview = {}, args = {} }) {
  if (preview.diff?.length) return <WordDiff diff={preview.diff} />;
  if ("to" in preview) {
    return <div className="chatApprovalDiff">
      {preview.from ? <del>{preview.from}</del> : null}
      {preview.from ? " → " : null}<ins>{preview.to || t("the library root")}</ins>
    </div>;
  }
  // A tool with no preview of its own: the arguments it was called with.
  const shown = Object.entries(args).filter(([, value]) => value);
  return shown.length ? <div className="chatApprovalDiff">
    {shown.map(([key, value]) => <div key={key}><span className="chatApprovalArg">{key}</span> {value}</div>)}
  </div> : null;
}

export default function ApprovalCard({ approval, kindLabel, titleOf, onDecide }) {
  const [sent, setSent] = useState(""); // the decision on its way, then taken
  const [error, setError] = useState("");
  const [declining, setDeclining] = useState(false); // the "what to do instead" line is open
  const [note, setNote] = useState("");
  const permission = permissionLabel(approval.perm);
  const Icon = permissionIcon(approval.perm);
  // The card is up: the hint that explains the four answers may show
  // beside it (guide/tours/hints.js). It goes again with the card.
  useEffect(() => { guideEvents.emit("approval.shown", { tool: approval.tool }); }, [approval.id, approval.tool]);
  const decide = async (decision, said = "") => {
    if (sent) return;
    setSent(decision);
    setError("");
    try {
      await onDecide(approval, decision, said.trim());
    } catch (err) {
      setSent("");
      setError(err?.message || String(err));
    }
  };
  const instead = t("What should the assistant do instead? (optional)");
  return (
    <div className={`chatApproval${sent ? " sent" : ""}`} role="group" data-guide="chat.approval" aria-label={t("Approval needed")}>
      <div className="chatApprovalCaption"><ShieldIcon size={14} />{t("Approval needed")} · {permission}</div>
      <div className="chatApprovalHead"><Icon size={16} /><span>{approvalTitle(approval, { titleOf, permission })}</span></div>
      <ApprovalPreview preview={approval.preview} args={approval.args} />
      {error ? <div className="chatApprovalError" role="alert">{error}</div> : null}
      {sent ? (
        <div className="chatApprovalStatus" role="status">
          {ALLOWING.has(sent) ? t("Allowed. The assistant goes on…") : t("Not allowed. The assistant goes on without it…")}
        </div>
      ) : declining ? (
        <form className="chatApprovalDecline" onSubmit={(e) => { e.preventDefault(); decide("deny", note); }}>
          <input className="aiKeyInput" value={note} maxLength={2000} autoFocus
            placeholder={instead} aria-label={instead} onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setDeclining(false); } }} />
          <div className="chatApprovalActions">
            <button type="submit" className="uiBtn sm">{t("Don't allow")}</button>
            <button type="button" className="uiBtn sm ghost" onClick={() => setDeclining(false)}>{t("Back")}</button>
          </div>
        </form>
      ) : (
        <div className="chatApprovalActions">
          <button type="button" className="uiBtn sm primary" onClick={() => decide("once")}>{t("Allow once")}</button>
          <button type="button" className="uiBtn sm" onClick={() => decide("chat")}
            title={t("Don't ask again for “{permission}” in this conversation", { permission })}>
            {t("Allow in this chat")}
          </button>
          <button type="button" className="uiBtn sm" onClick={() => decide("always")}
            title={t("Set “{permission}” to Allow for all chats of this kind ({kind}). You can change it in Settings → AI → Tool usage.", { permission, kind: kindLabel })}>
            {t("Always allow")}
          </button>
          <span className="chatApprovalSpacer" />
          <button type="button" className="uiBtn sm ghost" onClick={() => setDeclining(true)}>{t("Don't allow")}</button>
        </div>
      )}
    </div>
  );
}
