// The card of what the agent on this computer asks in the chat (docs/dev/
// local_agents.md "Asking in the chat"): to run a command beyond what its
// confinement lets it, or to change files outside its sandbox. It has the
// Gamma AI approval card's look (chat/ApprovalCard.jsx) but none of its
// permissions: it shows the command with where it runs, or the files with
// the change as diff rows, and the agent's reason, and offers Allow once,
// Allow in this chat (a command only: Gamma then answers that exact command
// itself for the rest of the conversation) and Decline…, which opens a line
// for a note the agent gets with the decline. There is never an Always
// allow. The buttons never take the focus by themselves, so a keystroke
// meant for the message box can't answer.
import { useState } from "react";
import { ActivityIcon, PencilIcon, ShieldIcon, TerminalIcon } from "../../shared/ui/Icons";
import { t } from "../../shared/i18n/i18n.js";
import { NOTE_MAX, askPlace, askRisk, askTitle, diffRows } from "./computerChat.js";

const ASK_ICONS = { command: TerminalIcon, files: PencilIcon };

// `onAnswer(decision, note)` posts the answer; a refusal it throws is said
// on the card.
export default function ComputerAsk({ ask, label, onAnswer }) {
  const [sent, setSent] = useState(""); // the decision on its way, then taken
  const [error, setError] = useState("");
  const [declining, setDeclining] = useState(false); // the note's line is open
  const [note, setNote] = useState("");
  const Icon = ASK_ICONS[ask.kind] || ActivityIcon;
  const risk = askRisk(ask);
  const rows = diffRows(ask.diff);
  const decide = async (decision, said = "") => {
    if (sent) return;
    setSent(decision);
    setError("");
    try {
      await onAnswer(decision, said);
    } catch (err) {
      setSent("");
      setError(err?.message || String(err));
    }
  };
  const why = t("Tell {agent} why, or what to do instead (optional)", { agent: label });
  return (
    <div className={`chatApproval computerAsk${sent ? " sent" : ""}`} role="group" aria-label={t("Approval needed")}>
      <div className="chatApprovalCaption"><ShieldIcon size={14} />{t("Approval needed")}</div>
      <div className="chatApprovalHead"><Icon size={16} /><span>{askTitle(ask, label)}</span></div>
      {ask.command ? (
        <>
          <div className="chatApprovalDiff computerAskCommand"><code>{ask.command}</code></div>
          <div className="chatApprovalNote">{askPlace(ask.cwd)}</div>
        </>
      ) : null}
      {ask.reason ? <div className="chatApprovalNote">{ask.reason}</div> : null}
      {ask.paths?.length ? (
        <div className="computerAskPaths">
          {ask.paths.map((path) => <code key={path}>{path === "outside" ? t("a file outside the folder") : path}</code>)}
        </div>
      ) : null}
      {rows.length ? (
        <div className="chatApprovalDiff computerAskDiff">
          {rows.map((row, i) => (row.kind === "del" ? <del key={i}>{row.text}</del>
            : row.kind === "ins" ? <ins key={i}>{row.text}</ins>
            : <span key={i} className={row.kind === "head" ? "computerAskDiffHead" : undefined}>{row.text}</span>))}
        </div>
      ) : null}
      {error ? <div className="chatApprovalError" role="alert">{error}</div> : null}
      {sent ? (
        <div className="chatApprovalStatus" role="status">
          {sent === "deny" ? t("Declined. {agent} goes on without it…", { agent: label }) : t("Allowed. {agent} goes on…", { agent: label })}
        </div>
      ) : declining ? (
        <form className="chatApprovalDecline" onSubmit={(e) => { e.preventDefault(); decide("deny", note.trim()); }}>
          <input className="aiKeyInput" value={note} maxLength={NOTE_MAX} autoFocus
            placeholder={why} aria-label={why} onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setDeclining(false); } }} />
          <div className="chatApprovalActions">
            <button type="submit" className="uiBtn sm">{t("Decline")}</button>
            <button type="button" className="uiBtn sm ghost" onClick={() => setDeclining(false)}>{t("Back")}</button>
          </div>
        </form>
      ) : (
        <div className="chatApprovalActions">
          <button type="button" className="uiBtn sm primary" onClick={() => decide("once")}>{t("Allow once")}</button>
          {ask.kind === "command" ? (
            <button type="button" className="uiBtn sm" onClick={() => decide("chat")}
              title={t("Run this exact command without asking for the rest of this conversation")}>
              {t("Allow in this chat")}
            </button>
          ) : null}
          <span className="chatApprovalSpacer" />
          <button type="button" className="uiBtn sm ghost" onClick={() => setDeclining(true)}>{t("Decline…")}</button>
        </div>
      )}
      {risk && !sent ? <div className="chatApprovalNote">{risk}</div> : null}
    </div>
  );
}
