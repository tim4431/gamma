// The card a tool call waits on while the user decides (chat/approvals.js
// has the rules, gamma/ai_permissions.py the server's side), on the shell
// every such card shares (ApprovalShell.jsx). It says what the call would
// do, shows the change as a word diff (or the folder a page moves to), and
// offers four answers. A call naming several pages shows one line per page
// in the same box (it scrolls), and notes the pages it leaves out. Allow
// once runs it. Allow in this chat also stops asking for this permission in
// this conversation. Always allow also sets the permission to Allow for
// this kind of chat in Settings. Don't allow opens a line for what to do
// instead (optional) and leaves the call unmade; the assistant gets that
// line with the decline. A deletion's Allow once is the red one.
import React, { useEffect } from "react";
import { t, tn } from "../shared/i18n/i18n.js";
import { approvalTitle, batchNotes, deletionNotes, toolAnswers } from "./approvals.js";
import ApprovalShell from "./ApprovalShell";
import { pictureSrc } from "./pictureSrc.js";
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

function ApprovalPreview({ tool, preview = {}, args = {} }) {
  // The pictures a change embeds (a clip's region, a note's new pictures),
  // drawn by the server from the same URLs the notes and the chat use.
  const pictures = preview.pictures?.length ? preview.pictures : preview.picture ? [preview.picture] : [];
  const gallery = pictures.length ? (
    <div className="chatApprovalPictures">
      {pictures.map((url) => <img key={url} src={pictureSrc(url)} alt="" loading="lazy" />)}
    </div>
  ) : null;
  if (preview.diff?.length) {
    return <>
      {preview.children ? <div className="chatApprovalNote">
        {tn("{n} nested block goes with it.", "{n} nested blocks go with it.", preview.children)}
      </div> : null}
      {[...deletionNotes(tool, preview), ...batchNotes(preview)].map((note) => <div key={note} className="chatApprovalNote">{note}</div>)}
      <WordDiff diff={preview.diff} />
      {gallery}
    </>;
  }
  if (gallery) return gallery;
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

// `answered`: the decision given elsewhere, on the bar of the note the
// card's edit is drawn in (editor/NoteReviewBar.jsx): the card says it as
// if given here.
export default function ApprovalCard({ approval, kindLabel, titleOf, onDecide, answered = "" }) {
  const permission = permissionLabel(approval.perm);
  // The card is up: the hint that explains the four answers may show
  // beside it (guide/tours/hints.js). It goes again with the card.
  useEffect(() => { guideEvents.emit("approval.shown", { tool: approval.tool }); }, [approval.id, approval.tool]);
  return (
    <ApprovalShell permission={permission} icon={permissionIcon(approval.perm)} guide="chat.approval"
      title={approvalTitle(approval, { titleOf, permission })}
      answers={toolAnswers({ tool: approval.tool, permission, kind: kindLabel })}
      onDecide={(decision, note) => onDecide(approval, decision, note)} answered={answered}>
      <ApprovalPreview tool={approval.tool} preview={approval.preview} args={approval.args} />
    </ApprovalShell>
  );
}
