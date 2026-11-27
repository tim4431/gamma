// The card a tool call waits on while the user decides (chat/approvals.js
// has the rules, gamma/ai_permissions.py the server's side), on the shell
// every such card shares (ApprovalShell.jsx). It says what the call would
// do, shows the change as a word diff (or the folder a page moves to), and
// offers four answers. A call naming several pages shows one line per page
// in the same box (it scrolls), and notes the pages it leaves out. Allow
// once runs it. Allow in this chat also stops asking for this permission in
// this conversation. Always allow also sets the permission to Allow for
// this kind of chat in Settings. Don't allow opens a line for what to do
// instead (optional) and leaves the call unmade; Gamma AI gets that line
// with the decline, and the card names it ("Allowed. Gamma AI goes on…").
// A change's card (chat/approvals.js isChangeCall) says them as the note's
// review does: Keep, its two grants in the menu beside it, and Undo. A
// deletion's first answer is the red one. What a computer's agent asks is
// a card on the same shell (AskCard, below).
import React, { useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import { t, tn } from "../shared/i18n/i18n.js";
import { ActivityIcon, LocateIcon, PencilIcon, TerminalIcon } from "../shared/ui/Icons";
import { GammaNavContext } from "../shared/ui/Widgets";
import { approvalTitle, batchNotes, deletionNotes, isChangeCall, placeText, toolAnswers } from "./approvals.js";
import ApprovalShell from "./ApprovalShell";
import { pictureSrc } from "./pictureSrc.js";
import { permissionIcon, permissionLabel } from "../settings/AssistantTools";
import { guideEvents } from "../guide/events.js";
import { askCommand, askPlace, askRisk, askTitle, diffRows, markParts, scrolledToEnd } from "./computer/computerChat.js";

// A server word diff (`[[kind, text], …]`, ai_tools.text_diff): what goes
// struck out, what comes in tinted. The revert prompt shows one too
// (chat/AgentChanges.jsx).
export function WordDiff({ diff }) {
  return <div className="chatApprovalDiff">
    {diff.map(([kind, text], i) => (kind === "del" ? <del key={i}>{text}</del>
      : kind === "ins" ? <ins key={i}>{text}</ins> : <span key={i}>{text}</span>))}
  </div>;
}

const DRAWING_TOOLS = new Set(["draw", "add_text_box", "place_picture", "add_sheet"]);

function ApprovalPreview({ tool, preview = {}, args = {} }) {
  // The pictures a change embeds (a clip's region, a note's new pictures),
  // drawn by the server from the same URLs the notes and the chat use.
  const pictures = preview.pictures?.length ? preview.pictures : preview.picture ? [preview.picture] : [];
  const gallery = pictures.length ? (
    <div className="chatApprovalPictures">
      {pictures.map((url) => <img key={url} src={pictureSrc(url)} alt="" loading="lazy" />)}
    </div>
  ) : null;
  // Where a comment, a resolve or a highlight writes, and what a drawing
  // tool makes (gamma/ai_draw.py: "2 shapes: a rectangle around …").
  const place = preview.place ? <div className="chatApprovalNote">{placeText(preview.place)}</div> : null;
  const what = DRAWING_TOOLS.has(tool) && preview.what ? <div className="chatApprovalNote">{preview.what}</div> : null;
  if (preview.diff?.length) {
    return <>
      {what}
      {place}
      {preview.children ? <div className="chatApprovalNote">
        {tn("{n} nested block goes with it.", "{n} nested blocks go with it.", preview.children)}
      </div> : null}
      {[...deletionNotes(tool, preview), ...batchNotes(preview)].map((note) => <div key={note} className="chatApprovalNote">{note}</div>)}
      <WordDiff diff={preview.diff} />
      {gallery}
    </>;
  }
  if (gallery) return <>{what}{place}{gallery}</>;
  if (place || what) return <>{what}{place}</>;
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

// Where a card's change is drawn while it waits (chat/noteReview.js), for
// Show to open: the note it edits, deletes or moves (a move on the note's
// own page, `src_page_id` when it goes to another), the note a new one
// follows (or its parent), the PDF page or the page of paper it draws on.
function drawnAt(approval) {
  const p = approval.preview || {};
  if (!p.page_id) return null;
  if (p.sheet_id) return { block: p.sheet_id, page: p.page_id };
  if (p.shape && p.pdf_page) return { pdf: p.pdf_page, page: p.page_id };
  if (p.block_id && !p.shape) return { block: p.block_id, page: p.src_page_id || p.page_id };
  if (approval.tool === "create_block" && p.parent_id) return { block: p.after_id || p.parent_id, page: p.page_id };
  return null;
}

// `answered`: the decision given elsewhere, on the bar of the note the
// card's change is drawn in (editor/NoteReviewBar.jsx) or under it on its
// page (markup/ReviewLayer.jsx): the card says it as if given here. Show
// opens where it is drawn.
export default function ApprovalCard({ approval, kindLabel, titleOf, onDecide, answered = "" }) {
  const permission = permissionLabel(approval.perm);
  const nav = useContext(GammaNavContext);
  const at = drawnAt(approval);
  const show = !at || !nav ? null : () => (at.pdf
    ? nav.openPage(at.page, { kind: "citation", pageId: at.page, page: at.pdf, quote: "" })
    : nav.openBlock(at.block, at.page));
  // The card is up: the hint that explains a change's answers (Keep, its
  // grants, Undo) may show beside it, not beside another call's card,
  // whose answers are Allow once and Don't allow (guide/tours/hints.js).
  // It goes again with the card.
  useEffect(() => {
    guideEvents.emit("approval.shown", { tool: approval.tool, change: isChangeCall(approval.tool, approval.perm) });
  }, [approval.id, approval.tool, approval.perm]);
  return (
    <ApprovalShell permission={permission} icon={permissionIcon(approval.perm)} guide="chat.approval"
      title={approvalTitle(approval, { titleOf, permission })}
      answers={toolAnswers({ tool: approval.tool, perm: approval.perm, permission, kind: kindLabel })} agent={t("Gamma AI")}
      call={approval}
      onDecide={(decision, note) => onDecide(approval, decision, note)} answered={answered}>
      {show && !answered ? (
        <button type="button" className="uiBtn sm ghost chatApprovalShow" onClick={show}
          title={t("Show the change where it would be made")}>
          <LocateIcon size={14} />{t("Show")}
        </button>
      ) : null}
      <ApprovalPreview tool={approval.tool} preview={approval.preview} args={approval.args} />
    </ApprovalShell>
  );
}

// What a computer's agent asks in the chat (docs/dev/local_agents.md
// "Asking in the chat"), on the same shell: to run a command beyond what
// its confinement lets it, or to change files outside its sandbox, with
// none of Gamma AI's permissions. It shows the command with where it runs,
// or the files with the change as diff rows, and the agent's reason, and
// offers Allow once, Allow in this chat (a command only: Gamma then
// answers that exact command itself for the rest of the conversation; never
// in a co-author's turn, whose asks the server marks `once`) and Don't
// allow with its note; never an Always allow. The command is drawn
// as the server shows it: what would not show marked ⟨U+202E⟩, its lines
// counted, the command a shell wrapper runs first with the wrapper under
// it, and the network address it reaches. Allow waits until the command
// and the diff were scrolled to their end (one rule with the desktop app's
// card of an ask on this computer). `label` names the agent;
// `onAnswer(decision, note)` posts the answer, and a refusal it throws is
// said on the card; an answer taken stays said until the next read drops
// the ask (chat/computer/useComputerChat.js `answer`).
const ASK_ICONS = { command: TerminalIcon, files: PencilIcon };

export function AskCard({ ask, label, onAnswer }) {
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
  if (ask.kind === "command" && !ask.once) {
    answers.push({ decision: "chat", label: t("Allow in this chat"), disabled: !read,
      title: read ? t("Run this exact command without asking for the rest of this conversation") : unread });
  }
  return (
    <ApprovalShell className="chatAsk" icon={ASK_ICONS[ask.kind] || ActivityIcon} title={askTitle(ask, label)}
      answers={answers} onDecide={onAnswer} agent={label}
      footnote={read ? askRisk(ask) : <><div>{unread}</div>{askRisk(ask)}</>}>
      {command.main ? (
        <>
          {command.lines ? <div className="chatApprovalNote">{tn("{n} line", "{n} lines", command.lines)}</div> : null}
          <div ref={commandBox} className="chatApprovalDiff chatAskCommand" onScroll={saw("command")}>
            <code>{markParts(command.main, ask.marked).map((part, i) => (part.mark ? <span key={i} className="chatAskMark">{part.text}</span> : part.text))}</code>
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
        <div className="chatAskPaths">
          {ask.paths.map((path) => <code key={path}>{path === "outside" ? t("a file outside the folder") : path}</code>)}
        </div>
      ) : null}
      {rows.length ? (
        <div ref={diffBox} className="chatApprovalDiff chatAskDiff" onScroll={saw("diff")}>
          {rows.map((row, i) => (row.kind === "del" ? <del key={i}>{row.text}</del>
            : row.kind === "ins" ? <ins key={i}>{row.text}</ins>
            : <span key={i} className={row.kind === "head" ? "chatAskDiffHead" : undefined}>{row.text}</span>))}
        </div>
      ) : null}
    </ApprovalShell>
  );
}
