import React, { useEffect, useRef, useState } from "react";
import { API, apiJson } from "../shared/lib/utils";
import { CheckIcon, ShieldIcon } from "../shared/ui/Icons";
import { TOOL_PERMISSION_ROWS } from "../settings/AssistantTools";
import { t } from "../shared/i18n/i18n.js";

const KINDS = { folder: t("folder chats"), pdf: t("PDF chats"), notes: t("notes chats") };
const EDIT_MODES = {
  replace: t("Replace note text"), append: t("Add to end of note"),
  prepend: t("Add to start of note"), patch: t("Replace matching text"),
  selection: t("Replace selected text"),
};

function decisionLabel(state) {
  switch (state) {
    case "allow_once": return t("Allowed once");
    case "allow_always": return t("Always allowed");
    case "deny": return t("Denied");
    case "expired": return t("Permission request expired");
    case "stopped": return t("Permission request closed");
    default: return t("Permission needed");
  }
}

// A request stays in its reply's transcript. Only the live stream can make it
// actionable; loading an old conversation never revives its approval buttons.
export default function ChatApproval({ approval, active, kind }) {
  const [sending, setSending] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState("");
  const [expired, setExpired] = useState(() => !!approval.expires_at && approval.expires_at * 1000 <= Date.now());
  const sendingRef = useRef(false);
  const id = React.useId();
  useEffect(() => {
    if (!approval.expires_at || approval.decision) return undefined;
    const timer = setTimeout(() => setExpired(true), Math.max(0, approval.expires_at * 1000 - Date.now()));
    return () => clearTimeout(timer);
  }, [approval.expires_at, approval.decision]);
  const state = approval.decision || (!active ? "stopped" : expired ? "expired" : "pending");
  const pending = state === "pending";
  const allowed = state === "allow_once" || state === "allow_always";
  const args = approval.args || {};
  const permissionLabel = TOOL_PERMISSION_ROWS.find(([key]) => key === approval.permission)?.[2] || t("Use assistant tool");
  const action = approval.tool === "create_block" ? t("Create note")
    : approval.tool === "move_block" ? t("Move note")
      : approval.tool === "edit_block" ? EDIT_MODES[args.mode || "replace"] || permissionLabel
        : permissionLabel;
  const target = approval.target || {};
  const targetText = target.title || args.source || args.query || target.folder || target.page_id || target.block_id;
  const destination = approval.tool === "move_page" ? target.folder ?? args.folder : args.parent_id;
  const content = typeof args.content === "string" ? args.content : null;

  async function decide(decision) {
    if (!pending || sendingRef.current || submitted) return;
    sendingRef.current = true;
    setSending(true);
    setError("");
    try {
      await apiJson(`${API}/ai/approvals/${encodeURIComponent(approval.id)}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision }),
      });
      setSubmitted(true);
    } catch (err) {
      if ([404, 409, 410].includes(err.status)) setExpired(true);
      else setError(t("Couldn't send your decision: {message}", { message: err.message }));
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  }

  return <section className="chatApproval" data-approval-state={state} aria-labelledby={`${id}-title`}>
    <div className="chatApprovalHeading">
      {allowed ? <CheckIcon size={16} /> : <ShieldIcon size={16} />}
      <span id={`${id}-title`} role="status">{decisionLabel(state)}</span>
    </div>
    <div className="chatApprovalAction">{action}</div>
    {targetText ? <div className="chatApprovalTarget">{targetText}</div> : null}
    {pending ? <>
      {approval.tool === "rename_page" && args.title ? <div className="chatApprovalPreview">{t("New title: {title}", { title: args.title })}</div> : null}
      {["move_page", "move_block"].includes(approval.tool) ? <div className="chatApprovalPreview">{t("Move to: {destination}", { destination: destination || t("Library root") })}</div> : null}
      {content !== null ? <div className="chatApprovalPreview">
        <span className="chatApprovalCaption">{t("Proposed content")}</span>
        <pre>{content}</pre>
      </div> : null}
      <details className="chatApprovalDetails">
        <summary>{t("Action details")}</summary>
        <pre>{JSON.stringify(args, null, 2)}</pre>
      </details>
      <p className="chatApprovalHint">{t("Always allow remembers this permission for {kind}. Change it anytime in Tools.", { kind: KINDS[kind] || t("this chat type") })}</p>
      {error ? <p className="chatApprovalError" role="alert">{error}</p> : null}
      <div className="chatApprovalActions" aria-busy={sending || submitted}>
        <button type="button" className="uiBtn sm" disabled={sending || submitted} onClick={() => decide("deny")}>{t("Deny")}</button>
        <button type="button" className="uiBtn sm" disabled={sending || submitted} onClick={() => decide("allow_always")}>{t("Always allow")}</button>
        <button type="button" className="uiBtn sm primary" disabled={sending || submitted} onClick={() => decide("allow_once")}>{t("Allow once")}</button>
      </div>
    </> : null}
  </section>;
}
