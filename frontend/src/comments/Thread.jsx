// A comment thread as both comment surfaces draw it (docs/dev/comments.md
// "In the app"): the thread popover (ThreadPopover.jsx) lists a block's
// threads, and the PDF's margin (CommentMargin.jsx) shows one card per
// thread beside its highlight or ink group. Each comment with its author
// (an agent by its kind's avatar and its name, then its computer and the
// person it wrote for, "on TIM-PC, for tim"), time and text
// rendered as a note is (so @people and [[links]] work, and in a work
// folder maker's own comments its agent's @name), its "⋯" menu (Edit,
// Copy link, Report on anyone else's, Delete), the
// thread's Resolve and its reply field once the thread is the active one.
// Under the first comment a line says when its place moved in a new
// version. While the reader's turn of the thread with an agent is to end
// (docs/dev/comments.md "Sent to an agent"), the thread carries its row
// (comments.js turnChip: Codex is working, with Stop; Codex is waiting for
// you, whose Answer in the chat opens the conversation in the chat, where
// its card is); a send of the reader's that started no turn keeps a row
// there too until the next send (refusalChip: Not sent to Codex, why, Send
// again). The note a turn's end wrote reads as that turn's state, not as an
// answer (turnEnd: Codex could not finish, and why). The newest agent reply
// of a thread the reader sent it has Open in chat, the thread's
// conversation with its steps and what it changed, and each agent comment's
// "⋯" has it too.
// A comment that comes (a reply, yours or someone's) rises into place
// (ListMotion).
import React, { startTransition, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { ContextMenu, MenuButton, MenuItem } from "../shared/ui/Menus";
import {
  AlertCircleIcon, CheckIcon, ClockIcon, FlagIcon, LinkIcon, PencilIcon, RefreshIcon, ShieldIcon, SparklesIcon, StopIcon, Trash2Icon,
} from "../shared/ui/Icons";
import ListMotion from "../shared/ui/ListMotion";
import { IconSwap } from "../shared/ui/MotionWidgets";
import { StateDetail, StateTag } from "../shared/ui/StateTag";
import { ActorAvatar } from "../collaboration/Presence";
import { BlockMarkdown } from "../editor/BlockTree";
import { MentionContext } from "../editor/MentionMenu";
import { fmtDate, t, tn } from "../shared/i18n/i18n.js";
import { formatRelativeTime } from "../library/libraryUtils";
import { CommentComposer, hasDraft } from "./CommentComposer";
import {
  agentFor, authorLabel, authorTitle, canDeleteComment, canEditComment, canReportComment, estimatedLines, isEdited, isOpen, refusalChip,
  resolvedBy, turnChip, turnEnd,
} from "./comments.js";
import { openReport } from "../support/reportContent.js";
import { ThreadPlaceContext } from "./threadPlaceContext.js";

// What a thread says of its place once its file changed, a PDF rebuilt or
// a text file rewritten (App's placeOf through threadPlaceContext.js).
const PLACE_TEXT = {
  moved: () => t("The passage moved in the new version"),
  lost: () => t("The passage was not found in the new version"),
  earlier: () => t("Drawn on an earlier version"),
};

// The glyph a turn's state leads with at rest (StateTag's `icon`; while
// the agent works a spinner instead), by its kind (comments.js turnChip,
// refusalChip, turnEnd): the shield while it waits for the person's
// answer, as the chat's steps pill has it, the clock in the queue, the
// alert for a failure or a send refused, the stop square for a stop.
const TURN_ICONS = {
  asking: ShieldIcon, waiting: ShieldIcon, queued: ClockIcon, failed: AlertCircleIcon, refused: AlertCircleIcon,
  stopped: StopIcon, empty: StopIcon,
};
const withIcon = (state) => ({ ...state, icon: TURN_ICONS[state.kind] || null });

// A thread's turn as its tag, its glyph by its kind, "for bob" after it
// for a co-author's turn of the reader's agent: the thread's turn row
// and the Comments window's row.
export function TurnTag({ state }) {
  return (
    <>
      <StateTag state={withIcon(state)} data-turn={state.kind} />
      {state.person ? <span className="commentTurnFor">{t("for {person}", { person: state.person })}</span> : null}
    </>
  );
}

// The avatar of a comment's writer (Presence.jsx ActorAvatar): an agent's
// by its kind, else its author's; the Comments window's rows draw it too.
export const CommentAvatar = ({ comment }) => (
  <ActorAvatar name={comment.author} kind={comment.agent ? comment.agent_kind || "" : ""} title={authorTitle(comment)} />
);

// A comment's text, rendered as a note is. One put off (`later`) is drawn
// once it comes within a screen of the view (the popover's list, else the
// window), in a transition, so scrolling stays smooth; until then a box of
// about its height stands in.
function CommentText({ comment, later, labelsFor, onBlockRefClick }) {
  const ref = useRef(null);
  const [near, setNear] = useState(!later);
  useEffect(() => {
    if (near) return undefined;
    const el = ref.current;
    const io = new IntersectionObserver((entries) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      io.disconnect();
      startTransition(() => setNear(true));
    }, { root: el.closest(".commentPopBody"), rootMargin: "100% 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, [near]);
  if (!near) {
    return <div ref={ref} className="blockRendered commentBody" aria-busy="true"
      style={{ minHeight: `calc(${estimatedLines(comment.body)}em * var(--lh-ui))` }} />;
  }
  return (
    <div className="blockRendered commentBody">
      <BlockMarkdown content={comment.body} blockId={`comment:${comment.id}`} refLabels={labelsFor(comment.body)}
        onBlockRefClick={onBlockRefClick} />
    </div>
  );
}

// `resolve`, on a thread's first comment only: {open, onClick}, the
// Resolve (or Reopen) button on its author line, beside the "⋯"; `place`,
// on the first comment too: what became of its place (a PLACE_TEXT key).
// `reply`: a comment after the first, stepped in under the first one's
// text. `later`: the ids of the comments drawn as they near the view.
// Without `actions` (a margin card at rest) the line has neither.
// `onOpenChat`: on an agent's comment in a thread the reader sent it, its
// "⋯" menu's Open in chat, and with `chatButton` (the thread's newest such
// reply: one way in a thread) the same as a button on its author line.
// `editRefused`: why an edit of it that names an agent is not sent
// ("closed", "not_newest"; "" when it would be), for its field to say. The
// note a turn's end wrote (`turn_end`) shows that turn's state in place of
// its English text (comments.js turnEnd).
function Comment({ comment, me, owner, canComment, people, labelsFor, later, field, onBlockRefClick, onEdit, onDelete, onCopyLink, resolve = null, place = "", reply = false, actions = true, onOpenChat = null, chatButton = false, editRefused = "" }) {
  const [menu, setMenu] = useState(null);
  const [editing, setEditing] = useState(() => hasDraft(`edit:${comment.id}`)); // an edit left unsaved
  const mayEdit = canComment && canEditComment(comment, me);
  const mayDelete = canComment && canDeleteComment(comment, me, owner);
  const openMenu = (e) => setMenu({ x: e.clientX, y: e.clientY });
  const who = authorLabel(comment);
  const byline = authorTitle(comment);
  const placeText = PLACE_TEXT[place]?.();
  const end = turnEnd(comment);
  // In the reader's own comments an agent's "@name" is that agent's chip:
  // there the server sent the comment to it (the field's `links.agents`).
  const agents = !comment.agent && !!me && comment.author === me ? field?.links?.agents || null : null;
  const named = useContext(MentionContext);
  const mentions = useMemo(() => (agents?.length ? { ...named, agents } : null), [named, agents]);
  const text = <CommentText comment={comment} later={later.has(comment.id)} labelsFor={labelsFor} onBlockRefClick={onBlockRefClick} />;
  return (
    <div className={`commentItem rowMenuHost${reply ? " reply" : ""}`} data-motion-key={comment.id} data-comment-id={comment.id}>
      <CommentAvatar comment={comment} />
      <div className="commentMain">
        <div className="commentMeta">
          <span className="commentAuthor" title={comment.agent ? byline : undefined}>{who || t("Someone")}</span>
          {comment.agent ? <span className="commentFor" title={byline}>{agentFor(comment)}</span> : null}
          <span className="commentTime" title={fmtDate(comment.created_at, { dateStyle: "medium", timeStyle: "short" })}>
            {formatRelativeTime(comment.created_at)}
          </span>
          {isEdited(comment) ? <span className="commentEdited">{t("edited")}</span> : null}
          <span className="commentMetaSpacer" />
          {onOpenChat && chatButton && actions ? (
            <button type="button" className="ctlBtn commentOpenChat" onClick={onOpenChat} data-guide="comments.openChat"
              aria-label={t("Open in chat")} title={t("Open in chat")}>
              <SparklesIcon size={14} />
            </button>
          ) : null}
          {resolve && actions ? (
            <button type="button" className="ctlBtn commentResolve" onClick={resolve.onClick}
              aria-label={resolve.open ? t("Resolve") : t("Reopen")}
              title={resolve.open ? t("Resolve this comment: it leaves the note and stays in the Comments window") : t("Reopen this comment")}>
              <IconSwap swap={!resolve.open} from={<CheckIcon size={14} />} to={<RefreshIcon size={14} />} />
            </button>
          ) : null}
          {!comment.deleted && actions ? <MenuButton open={openMenu} label={t("Comment actions")} /> : null}
        </div>
        {editing ? (
          <CommentComposer {...field} initial={comment.body} refused={editRefused} autoFocus people={people} me={me} draftKey={`edit:${comment.id}`}
            placeholder={t("Edit the comment…")} submitLabel={t("Save")}
            onCancel={() => setEditing(false)}
            // refused, the field stays with the new text back in it
            onSubmit={async (body) => { const ok = await onEdit(comment.id, body); if (ok) setEditing(false); return ok; }} />
        ) : comment.deleted ? (
          <div className="commentDeleted">{t("Comment deleted")}</div>
        ) : end ? (
          <div className="commentEnd" data-turn-end={end.kind}>
            <StateTag state={withIcon(end)} />
            <StateDetail state={end} />
          </div>
        ) : mentions ? <MentionContext.Provider value={mentions}>{text}</MentionContext.Provider> : text}
        {placeText ? <div className="commentThreadState" data-place={place}>{placeText}</div> : null}
      </div>
      {menu ? (
        <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)} aria-label={t("Comment actions")}>
          {mayEdit ? <MenuItem icon={PencilIcon} onClick={() => { setMenu(null); setEditing(true); }}>{t("Edit")}</MenuItem> : null}
          {onOpenChat ? <MenuItem icon={SparklesIcon} onClick={() => { setMenu(null); onOpenChat(); }}>{t("Open in chat")}</MenuItem> : null}
          <MenuItem icon={LinkIcon} onClick={() => { setMenu(null); onCopyLink(comment); }}>{t("Copy link")}</MenuItem>
          {canReportComment(comment, me) ? (
            <MenuItem icon={FlagIcon} title={t("Tell the people who run this server about this comment")}
              onClick={() => {
                setMenu(null);
                openReport({ kind: "comment", commentId: comment.id, text: comment.body, author: comment.author });
              }}>{t("Report…")}</MenuItem>
          ) : null}
          {mayDelete ? (
            <MenuItem icon={Trash2Icon} danger onClick={() => { setMenu(null); onDelete(comment.id); }}>{t("Delete")}</MenuItem>
          ) : null}
        </ContextMenu>
      ) : null}
    </div>
  );
}

// A thread shows its reply field once it is the active one, as in Notion: a
// press on it (not on one of its buttons, links or fields) or the link that
// opened the popover at it makes it active. A thread holding an unsent
// reply keeps its field, so a draft is never hidden. A `summary` (a margin
// card) shows, until it is active, only its first comment, clamped, and how
// many replies follow; one may make it active to read it without the right
// to write.
export function Thread({ thread, focused = false, active, onActivate, canComment, me, people, onResolve, onReply, summary = false, ...rest }) {
  const open = isOpen(thread);
  const ref = useRef(null);
  // The turn row: the reader's turn still to end, else their last send to
  // an agent here that started none, with Send again (the comment saved
  // unchanged with the agent's id, `onEdit`) where it can help.
  const chip = turnChip(thread);
  const refusal = chip ? null : refusalChip(thread.refused);
  const turn = chip || refusal;
  const unsent = refusal?.retry ? thread.comments.find((c) => c.id === refusal.comment && !c.deleted) : null;
  const sendAgain = unsent && canComment && rest.onEdit ? () => rest.onEdit(unsent.id, unsent.body, refusal.to) : null;
  // The reader's conversation of the thread with an agent: its turn's Stop
  // and Open in chat (App's, through the field's `links`), on the newest
  // agent reply's author line and in each agent comment's "⋯".
  const { onOpenChat, onStopTurn } = rest.field?.links || {};
  const openChat = thread.agent_chat && onOpenChat ? () => onOpenChat(thread) : null;
  const byAgent = (c) => c.agent_kind === "computer" && !!c.agent_id;
  const newestAgent = thread.comments.findLast((c) => !c.deleted && byAgent(c))?.id;
  const place = useContext(ThreadPlaceContext)?.(thread) || "";
  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: "nearest" });
  }, [focused]);
  const draft = hasDraft(`reply:${thread.id}`);
  const replying = canComment && open && (active || draft);
  const canActivate = (canComment || summary) && open && !active;
  const collapsed = summary && !active && !draft;
  const comments = collapsed ? thread.comments.slice(0, 1) : thread.comments;
  const replies = thread.comments.length - 1;
  // An edit naming an agent is sent only as the open thread's newest comment (local_agents/threads.py).
  const newest = thread.comments.findLast((c) => !c.deleted)?.id;
  const editRefused = (c) => (!open ? "closed" : c.id === newest ? "" : "not_newest");
  const activate = (e) => {
    if (!canActivate || e.target.closest?.("button, a, input, textarea, .cm-editor, .ctxMenu")) return;
    onActivate(thread.id);
  };
  return (
    <section ref={ref} className={`commentThread${open ? "" : " resolved"}${focused ? " focused" : ""}${active ? " active" : ""}${canActivate ? " canReply" : ""}${collapsed ? " collapsed" : ""}`}
      data-thread-id={thread.id} data-guide="comments.thread"
      onClick={activate}
      tabIndex={canActivate ? 0 : undefined}
      aria-label={canActivate ? (canComment ? t("Reply to this comment") : t("Show this comment")) : undefined}
      onKeyDown={(e) => { if (canActivate && e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onActivate(thread.id); } }}>
      {/* a card opening to its whole thread is another list: nothing comes in */}
      <ListMotion keys={comments.map((c) => c.id)} scope={collapsed} enter="rise" moves={false}>
        {comments.map((c, i) => (
          <Comment key={c.id} comment={c} me={me} canComment={canComment} people={people} {...rest} actions={!collapsed}
            reply={i > 0} resolve={i === 0 && canComment ? { open, onClick: () => onResolve(thread.id, open) } : null}
            place={i === 0 ? place : ""} editRefused={editRefused(c)}
            onOpenChat={byAgent(c) ? openChat : null} chatButton={c.id === newestAgent} />
        ))}
      </ListMotion>
      {collapsed && replies > 0 ? (
        <div className="commentReplies">{tn("{n} reply", "{n} replies", replies)}</div>
      ) : null}
      {turn ? (
        // a live region: the turn's state is read as it changes; its row wraps in a narrow card
        <div className="commentTurn" data-turn={turn.kind} role="status">
          <TurnTag state={turn} />
          <span className="commentMetaSpacer" />
          {chip?.open && openChat ? (
            <button type="button" className="uiBtn sm" onClick={openChat}>
              <SparklesIcon size={14} />{t("Answer in the chat")}
            </button>
          ) : null}
          {chip?.stop && onStopTurn ? (
            <button type="button" className="ctlBtn commentTurnStop" onClick={() => onStopTurn(thread)}
              aria-label={t("Stop {agent}", { agent: thread.agent_chat.label })} title={t("Stop {agent}", { agent: thread.agent_chat.label })}>
              <StopIcon size={14} />
            </button>
          ) : null}
          {sendAgain ? <button type="button" className="uiBtn sm commentTurnRetry" onClick={sendAgain}>{t("Send again")}</button> : null}
          <StateDetail state={turn} />
        </div>
      ) : null}
      {replying ? (
        <CommentComposer {...rest.field} thread={thread} autoFocus={active} people={people} me={me} draftKey={`reply:${thread.id}`} placeholder={t("Reply…")} submitLabel={t("Send reply")}
          onSubmit={(body, to) => onReply(thread.id, body, to)} onEscape={rest.onEscape} />
      ) : null}
      {open ? null : (
        <div className="commentThreadState">
          {resolvedBy(thread) ? t("Resolved by {name}", { name: resolvedBy(thread) }) : t("Resolved")}
        </div>
      )}
    </section>
  );
}

// "Sign in to comment" in place of the fields, for a link visitor with no
// account: a button to the share's sign-in gate when there is one.
export function SignInToComment({ onSignIn }) {
  return (
    <div className="commentSignIn">
      {onSignIn ? <button type="button" className="uiBtn sm primary" onClick={onSignIn}>{t("Sign in to comment")}</button>
        : t("Sign in to comment")}
    </div>
  );
}

// The menus and popups a comment surface opens and draws outside itself: a
// comment's "⋯" menu, a field's [[, @ and "/" menus, its LaTeX preview and
// completion, another popover. A press in one is no press outside the
// popover or the margin card.
export const COMMENT_POPUPS = "[data-popover], .ctxMenu, .refPopup, .slashMenu, .mathPreviewTip, .latexAcPopup";

// A handler that keeps its identity and calls the latest one given: the
// comments' [[link]] clicks, since BlockMarkdown leaves its handlers out of
// its memo (BlockTree.jsx).
export function useLatestHandler(fn) {
  const ref = useRef(fn);
  ref.current = fn;
  return useCallback((...args) => ref.current?.(...args), []);
}
