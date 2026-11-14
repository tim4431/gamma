// The card of what the agent on this computer asks in the chat (docs/dev/
// local_agents.md "Asking in the chat"): to run a command beyond what its
// confinement lets it, or to change files outside its sandbox. It is the
// approval card's shell (chat/ApprovalShell.jsx) with none of Gamma AI's
// permissions: it shows the command with where it runs, or the files with
// the change as diff rows, and the agent's reason, and offers Allow once,
// Allow in this chat (a command only: Gamma then answers that exact command
// itself for the rest of the conversation) and Don't allow, which opens a
// line for a note the agent gets with the decline. There is never an Always
// allow. The command is drawn as the server shows it: what would not show
// marked ⟨U+202E⟩, its lines counted, the command a shell wrapper runs
// first with the wrapper under it, and the network address it reaches;
// Allow waits until the command and the diff were scrolled to their end
// (one rule with the desktop app's card of an ask on this computer).
import { useLayoutEffect, useRef, useState } from "react";
import { ActivityIcon, PencilIcon, TerminalIcon } from "../../shared/ui/Icons";
import { t, tn } from "../../shared/i18n/i18n.js";
import ApprovalShell from "../ApprovalShell";
import { askCommand, askPlace, askRisk, askTitle, diffRows, markParts, scrolledToEnd } from "./computerChat.js";

const ASK_ICONS = { command: TerminalIcon, files: PencilIcon };

// `onAnswer(decision, note)` posts the answer; a refusal it throws is said
// on the card. An answer taken stays said on it until the side's next read
// drops the ask (chat/computer/useComputerChat.js `answer`).
export default function ComputerAsk({ ask, label, onAnswer }) {
  const rows = diffRows(ask.diff);
  const command = askCommand(ask);
  // Whether the command's box and the diff's were each read to their end
  // (a box not drawn, or with nothing to scroll, from the first paint).
  const commandBox = useRef(null);
  const diffBox = useRef(null);
  const [seen, setSeen] = useState({ command: false, diff: false });
  useLayoutEffect(() => {
    setSeen((s) => ({ command: s.command || scrolledToEnd(commandBox.current), diff: s.diff || scrolledToEnd(diffBox.current) }));
  }, []);
  const saw = (box) => (e) => { if (scrolledToEnd(e.currentTarget)) setSeen((s) => (s[box] ? s : { ...s, [box]: true })); };
  const read = seen.command && seen.diff;
  const unread = t("Scroll to the end first: Allow waits until you have seen all of it");
  const answers = [{ decision: "once", label: t("Allow once"), primary: true, disabled: !read, title: read ? undefined : unread }];
  if (ask.kind === "command") {
    answers.push({ decision: "chat", label: t("Allow in this chat"), disabled: !read,
      title: read ? t("Run this exact command without asking for the rest of this conversation") : unread });
  }
  return (
    <ApprovalShell className="computerAsk" icon={ASK_ICONS[ask.kind] || ActivityIcon} title={askTitle(ask, label)}
      answers={answers} onDecide={onAnswer} agent={label} footnote={askRisk(ask)}>
      {command.main ? (
        <>
          {command.lines ? <div className="chatApprovalNote">{tn("{n} line", "{n} lines", command.lines)}</div> : null}
          <div ref={commandBox} className="chatApprovalDiff computerAskCommand" onScroll={saw("command")}>
            <code>{markParts(command.main, ask.marked).map((part, i) => (part.mark ? <span key={i} className="computerAskMark">{part.text}</span> : part.text))}</code>
          </div>
          {command.through ? <div className="chatApprovalNote">{command.through}</div> : null}
          {ask.marked ? (
            <div className="chatApprovalNote">{t("This holds {n} characters that do not show: Gamma marks them ⟨U+…⟩.", { n: ask.marked })}</div>
          ) : null}
          <div className="chatApprovalNote">{askPlace(ask.cwd)}</div>
          {ask.host ? <div className="chatApprovalNote">{t("It reaches {host} on the network", { host: ask.host })}</div> : null}
        </>
      ) : null}
      {ask.reason ? <div className="chatApprovalNote">{ask.reason}</div> : null}
      {ask.paths?.length ? (
        <div className="computerAskPaths">
          {ask.paths.map((path) => <code key={path}>{path === "outside" ? t("a file outside the folder") : path}</code>)}
        </div>
      ) : null}
      {rows.length ? (
        <div ref={diffBox} className="chatApprovalDiff computerAskDiff" onScroll={saw("diff")}>
          {rows.map((row, i) => (row.kind === "del" ? <del key={i}>{row.text}</del>
            : row.kind === "ins" ? <ins key={i}>{row.text}</ins>
            : <span key={i} className={row.kind === "head" ? "computerAskDiffHead" : undefined}>{row.text}</span>))}
        </div>
      ) : null}
    </ApprovalShell>
  );
}
