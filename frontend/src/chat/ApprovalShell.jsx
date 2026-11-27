// The card a call waits on while the person decides, whoever asks: Gamma
// AI's tool call (ApprovalCard.jsx) and what a computer's agent asks
// (ApprovalCard.jsx AskCard). A caption, the head (an icon and what
// the call would do), the card's preview as its children, then the
// answers: the card's own that allow (`answers`, [{decision, label,
// primary, danger, title, disabled, menu}]: a `menu` one sits in the menu
// beside the buttons) and, after a spacer, the one that declines (Undo for
// a change, else Don't allow: chat/approvals.js answerWords, by `call`),
// which opens a line for what to do instead (optional; Enter declines with
// it, Escape or Back closes it). Once answered the card says so, by the
// agent that asked (`agent`: "Kept. Codex goes on…", "Not allowed. Gamma
// AI goes on without it…"); a refusal is said on it, and the answers come
// back. The bar of a note an edit is drawn in
// (editor/NoteReviewBar.jsx) has the same answers beside its own label
// (`useApprovalAnswer` and `ApprovalAnswers`). The buttons never take the
// focus by themselves, so a keystroke meant for the message box can't
// answer.
import { useState } from "react";
import { t } from "../shared/i18n/i18n.js";
import { ChevronDownIcon, ShieldIcon } from "../shared/ui/Icons";
import { ActionMenu } from "../shared/ui/Menus.jsx";
import { touchTyping } from "../shared/lib/pointer.js";
import { answerWords } from "./approvals.js";

// How long the line of what to do instead may be (both servers' cap).
const NOTE_MAX = 2000;

// A server word diff (`[[kind, text], …]`, ai_tools.text_diff): what goes
// struck out, what comes in tinted. A card's preview shows one, and so do
// a revert that stopped (chat/AgentChanges.jsx, RevertChanges.jsx) and a
// note's review bar (editor/NoteReviewBar.jsx).
export function WordDiff({ diff }) {
  return <div className="chatApprovalDiff">
    {diff.map(([kind, text], i) => (kind === "del" ? <del key={i}>{text}</del>
      : kind === "ins" ? <ins key={i}>{text}</ins> : <span key={i}>{text}</span>))}
  </div>;
}

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
// goes on ("Codex"); Gamma AI when none (its edit's bar in a note). `call`
// ({tool, perm}) picks the words (chat/approvals.js answerWords). The
// line takes the focus as it opens unless that would raise an on-screen
// keyboard (touchTyping).
export function ApprovalAnswers({ answer, answers, agent = "", call }) {
  const { taken, error, declining, setDeclining, note, setNote, decide } = answer;
  const who = agent || t("Gamma AI");
  const words = answerWords(call, who);
  const instead = t("Tell {agent} why, or what to do instead (optional)", { agent: who });
  const status = taken === "deny" ? words.denied : words.allowed;
  const buttons = answers.filter((a) => !a.menu);
  const more = answers.filter((a) => a.menu && !a.disabled);
  return <>
    {error ? <div className="chatApprovalError" role="alert">{error}</div> : null}
    {taken ? (
      <div className="chatApprovalStatus" role="status">{status}</div>
    ) : declining ? (
      <form className="chatApprovalDecline" onSubmit={(e) => { e.preventDefault(); decide("deny", note); }}>
        <input className="aiKeyInput" value={note} maxLength={NOTE_MAX} autoFocus={!touchTyping()}
          placeholder={instead} aria-label={instead} onChange={(e) => setNote(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setDeclining(false); } }} />
        <div className="chatApprovalActions">
          <button type="submit" className="uiBtn sm">{words.deny}</button>
          <button type="button" className="uiBtn sm ghost" onClick={() => setDeclining(false)}>{t("Back")}</button>
        </div>
      </form>
    ) : (
      <div className="chatApprovalActions">
        {buttons.map(({ decision, label, primary, danger, title, disabled }) => (
          <button key={decision} type="button" title={title} disabled={disabled} onClick={() => decide(decision)}
            className={`uiBtn sm${primary ? " primary" : ""}${danger ? " danger" : ""}`}>
            {label}
          </button>
        ))}
        {more.length ? (
          <ActionMenu label={t("More ways to keep it")} icon={ChevronDownIcon} iconOnly
            items={more.map(({ decision, label, title }) => ({ label, title, onClick: () => decide(decision) }))} />
        ) : null}
        <span className="chatApprovalSpacer" />
        <button type="button" className="uiBtn sm ghost" onClick={() => setDeclining(true)}>{words.deny}</button>
      </div>
    )}
  </>;
}

// The card: `permission` named in the caption when there is one, `icon`
// and `title` its head, `guide` its guide anchor, `footnote` a line under
// the answers while they wait.
export default function ApprovalShell({
  permission = "", icon: Icon, title, answers, onDecide, answered = "", agent = "", call, className = "", guide, footnote, children,
}) {
  const answer = useApprovalAnswer(onDecide, answered);
  return (
    <div className={`chatApproval${className ? ` ${className}` : ""}${answer.taken ? " sent" : ""}`}
      role="group" data-guide={guide} aria-label={t("Approval needed")}>
      <div className="chatApprovalCaption"><ShieldIcon size={14} />{t("Approval needed")}{permission ? ` · ${permission}` : null}</div>
      <div className="chatApprovalHead"><Icon size={16} /><span>{title}</span></div>
      {children}
      <ApprovalAnswers answer={answer} answers={answers} agent={agent} call={call} />
      {footnote && !answer.taken ? <div className="chatApprovalNote">{footnote}</div> : null}
    </div>
  );
}
