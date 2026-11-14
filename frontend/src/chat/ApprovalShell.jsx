// The card a call waits on while the person decides, whoever asks: Gamma
// AI's tool call (ApprovalCard.jsx) and what the agent on this computer
// asks (computer/ComputerAsk.jsx). A caption, the head (an icon and what
// the call would do), the card's preview as its children, then the
// answers: the card's own that allow (`answers`, [{decision, label,
// primary, danger, title}]) and, after a spacer, Don't allow, which opens
// a line for what to do instead (optional; Enter declines with it, Escape
// or Back closes it). Once answered the card says so ("Allowed. … goes
// on…" or "Not allowed. … goes on without it…"); a refusal is said on it,
// and the answers come back. The bar of a note an edit is drawn in
// (editor/NoteReviewBar.jsx) has the same answers beside its own label
// (`useApprovalAnswer` and `ApprovalAnswers`). The buttons never take the
// focus by themselves, so a keystroke meant for the message box can't
// answer.
import { useState } from "react";
import { t } from "../shared/i18n/i18n.js";
import { ShieldIcon } from "../shared/ui/Icons";

// How long the line of what to do instead may be (both servers' cap).
const NOTE_MAX = 2000;

// The answer's state: `onDecide(decision, note)` sends it ("deny" with the
// line's note, trimmed) and throws a refusal; `answered` is a decision
// given elsewhere, said as if given here.
export function useApprovalAnswer(onDecide, answered = "") {
  const [sent, setSent] = useState(""); // the decision on its way, then taken
  const [error, setError] = useState("");
  const [declining, setDeclining] = useState(false); // the "what to do instead" line is open
  const [note, setNote] = useState("");
  const taken = sent || answered;
  const decide = async (decision, said = "") => {
    if (taken) return;
    setSent(decision);
    setError("");
    try {
      await onDecide(decision, said.trim());
    } catch (err) {
      setSent("");
      setError(err?.message || String(err));
    }
  };
  return { taken, error, declining, setDeclining, note, setNote, decide };
}

// The answers of `answer` (useApprovalAnswer): the refusal, then what was
// taken, the line of what to do instead, or the buttons. `agent` names who
// goes on ("Codex"); the assistant when none.
export function ApprovalAnswers({ answer, answers, agent = "" }) {
  const { taken, error, declining, setDeclining, note, setNote, decide } = answer;
  const instead = agent ? t("Tell {agent} why, or what to do instead (optional)", { agent })
    : t("What should the assistant do instead? (optional)");
  const status = taken === "deny"
    ? (agent ? t("Not allowed. {agent} goes on without it…", { agent }) : t("Not allowed. The assistant goes on without it…"))
    : (agent ? t("Allowed. {agent} goes on…", { agent }) : t("Allowed. The assistant goes on…"));
  return <>
    {error ? <div className="chatApprovalError" role="alert">{error}</div> : null}
    {taken ? (
      <div className="chatApprovalStatus" role="status">{status}</div>
    ) : declining ? (
      <form className="chatApprovalDecline" onSubmit={(e) => { e.preventDefault(); decide("deny", note); }}>
        <input className="aiKeyInput" value={note} maxLength={NOTE_MAX} autoFocus
          placeholder={instead} aria-label={instead} onChange={(e) => setNote(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setDeclining(false); } }} />
        <div className="chatApprovalActions">
          <button type="submit" className="uiBtn sm">{t("Don't allow")}</button>
          <button type="button" className="uiBtn sm ghost" onClick={() => setDeclining(false)}>{t("Back")}</button>
        </div>
      </form>
    ) : (
      <div className="chatApprovalActions">
        {answers.map(({ decision, label, primary, danger, title }) => (
          <button key={decision} type="button" title={title} onClick={() => decide(decision)}
            className={`uiBtn sm${primary ? " primary" : ""}${danger ? " danger" : ""}`}>
            {label}
          </button>
        ))}
        <span className="chatApprovalSpacer" />
        <button type="button" className="uiBtn sm ghost" onClick={() => setDeclining(true)}>{t("Don't allow")}</button>
      </div>
    )}
  </>;
}

// The card: `permission` named in the caption when there is one, `icon`
// and `title` its head, `guide` its guide anchor, `footnote` a line under
// the answers while they wait.
export default function ApprovalShell({
  permission = "", icon: Icon, title, answers, onDecide, answered = "", agent = "", className = "", guide, footnote, children,
}) {
  const answer = useApprovalAnswer(onDecide, answered);
  return (
    <div className={`chatApproval${className ? ` ${className}` : ""}${answer.taken ? " sent" : ""}`}
      role="group" data-guide={guide} aria-label={t("Approval needed")}>
      <div className="chatApprovalCaption"><ShieldIcon size={14} />{t("Approval needed")}{permission ? ` · ${permission}` : null}</div>
      <div className="chatApprovalHead"><Icon size={16} /><span>{title}</span></div>
      {children}
      <ApprovalAnswers answer={answer} answers={answers} agent={agent} />
      {footnote && !answer.taken ? <div className="chatApprovalNote">{footnote}</div> : null}
    </div>
  );
}
