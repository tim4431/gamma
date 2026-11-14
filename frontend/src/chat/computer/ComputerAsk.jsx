// The card of what the agent on this computer asks in the chat (docs/dev/
// local_agents.md "Asking in the chat"): to run a command beyond what its
// confinement lets it, or to change files outside its sandbox. It is the
// approval card's shell (chat/ApprovalShell.jsx) with none of Gamma AI's
// permissions: it shows the command with where it runs, or the files with
// the change as diff rows, and the agent's reason, and offers Allow once,
// Allow in this chat (a command only: Gamma then answers that exact command
// itself for the rest of the conversation) and Don't allow, which opens a
// line for a note the agent gets with the decline. There is never an Always
// allow.
import { ActivityIcon, PencilIcon, TerminalIcon } from "../../shared/ui/Icons";
import { t } from "../../shared/i18n/i18n.js";
import ApprovalShell from "../ApprovalShell";
import { askPlace, askRisk, askTitle, diffRows } from "./computerChat.js";

const ASK_ICONS = { command: TerminalIcon, files: PencilIcon };

// `onAnswer(decision, note)` posts the answer; a refusal it throws is said
// on the card. An answer taken stays said on it until the side's next read
// drops the ask (chat/computer/useComputerChat.js `answer`).
export default function ComputerAsk({ ask, label, onAnswer }) {
  const rows = diffRows(ask.diff);
  const answers = [{ decision: "once", label: t("Allow once"), primary: true }];
  if (ask.kind === "command") {
    answers.push({ decision: "chat", label: t("Allow in this chat"),
      title: t("Run this exact command without asking for the rest of this conversation") });
  }
  return (
    <ApprovalShell className="computerAsk" icon={ASK_ICONS[ask.kind] || ActivityIcon} title={askTitle(ask, label)}
      answers={answers} onDecide={onAnswer} agent={label} footnote={askRisk(ask)}>
      {ask.command ? (
        <>
          <div className="chatApprovalDiff computerAskCommand">{ask.command}</div>
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
    </ApprovalShell>
  );
}
