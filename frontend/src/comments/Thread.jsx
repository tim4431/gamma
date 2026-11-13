// A comment thread as both comment surfaces draw it (docs/dev/comments.md
// "In the app"): the thread popover (ThreadPopover.jsx) lists a block's
// threads, and the PDF's margin (CommentMargin.jsx) shows one card per
// thread beside its highlight or ink group. Each comment with its author
// (an agent by its name, with whose connection it wrote), time and text
// rendered as a note is (so @people and [[links]] work), its "⋯" menu (Edit,
// Copy link, Report on anyone else's, Delete), the
// thread's Resolve and its reply field once the thread is the active one.
// The first comment's line carries the chip of a Send waiting on the
// thread, and under it a line says when its place moved in a new build.
// A comment that comes (a reply, yours or someone's) rises into place
// (ListMotion).
import React, { startTransition, useCallback, useContext, useEffect, useRef, useState } from "react";
import { ContextMenu, MenuButton, MenuItem } from "../shared/ui/Menus";
import { CheckIcon, FlagIcon, LinkIcon, PencilIcon, RefreshIcon, Trash2Icon } from "../shared/ui/Icons";
import ListMotion from "../shared/ui/ListMotion";
import { IconSwap } from "../shared/ui/MotionWidgets";
import { PersonAvatar } from "../collaboration/Presence";
import { BlockMarkdown } from "../editor/BlockTree";
import { fmtDate, t, tn } from "../shared/i18n/i18n.js";
import { formatRelativeTime } from "../library/libraryUtils";
import { CommentComposer, hasDraft } from "./CommentComposer";
import { authorLabel, authorTitle, canDeleteComment, canEditComment, canReportComment, estimatedLines, isEdited, isOpen, sentChip } from "./comments.js";
import { openReport } from "../support/reportContent.js";
import { ThreadPlaceContext } from "./threadPlaceContext.js";

// What a thread says of its place once its PDF was rebuilt (App's placeOf
// through threadPlaceContext.js).
const PLACE_TEXT = {
  moved: () => t("The passage moved in the new build"),
  lost: () => t("The passage was not found in the new build"),
  earlier: () => t("Drawn on an earlier build"),
};

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
// Resolve (or Reopen) button on its author line, beside the "⋯"; `chip` and
// `place`, on the first comment too: the Send waiting on the thread
// (comments.js sentChip) and what became of its place (a PLACE_TEXT key).
// `reply`: a comment after the first, stepped in under the first one's
// text. `later`: the ids of the comments drawn as they near the view.
// Without `actions` (a margin card at rest) the line has neither.
function Comment({ comment, me, owner, canComment, people, labelsFor, later, field, onBlockRefClick, onEdit, onDelete, onCopyLink, resolve = null, chip = null, place = "", reply = false, actions = true }) {
  const [menu, setMenu] = useState(null);
  const [editing, setEditing] = useState(() => hasDraft(`edit:${comment.id}`)); // an edit left unsaved
  const mayEdit = canComment && canEditComment(comment, me);
  const mayDelete = canComment && canDeleteComment(comment, me, owner);
  const openMenu = (e) => setMenu({ x: e.clientX, y: e.clientY });
  const who = authorLabel(comment);
  const byline = authorTitle(comment);
  const placeText = PLACE_TEXT[place]?.();
  return (
    <div className={`commentItem rowMenuHost${reply ? " reply" : ""}`} data-motion-key={comment.id} data-comment-id={comment.id}>
      <PersonAvatar name={who || "?"} title={byline} />
      <div className="commentMain">
        <div className="commentMeta">
          <span className="commentAuthor" title={comment.agent ? byline : undefined}>{who || t("Someone")}</span>
          <span className="commentTime" title={fmtDate(comment.created_at, { dateStyle: "medium", timeStyle: "short" })}>
            {formatRelativeTime(comment.created_at)}
          </span>
          {isEdited(comment) ? <span className="commentEdited">{t("edited")}</span> : null}
          {chip ? <span className={`uiTag ${chip.tone}`} data-sent={chip.state} title={chip.title}>{chip.label}</span> : null}
          <span className="commentMetaSpacer" />
          {resolve && actions ? (
            <button type="button" className="ctlBtn commentResolve" onClick={resolve.onClick}
              aria-label={resolve.open ? t("Resolve") : t("Reopen")}
              title={resolve.open ? t("Resolve this thread: it leaves the note and stays in the Comments window") : t("Reopen this thread")}>
              <IconSwap swap={!resolve.open} from={<CheckIcon size={14} />} to={<RefreshIcon size={14} />} />
            </button>
          ) : null}
          {!comment.deleted && actions ? <MenuButton open={openMenu} label={t("Comment actions")} /> : null}
        </div>
        {editing ? (
          <CommentComposer {...field} initial={comment.body} autoFocus people={people} me={me} draftKey={`edit:${comment.id}`}
            placeholder={t("Edit the comment…")} submitLabel={t("Save")}
            onCancel={() => setEditing(false)}
            // refused, the field stays with the new text back in it
            onSubmit={async (body) => { const ok = await onEdit(comment.id, body); if (ok) setEditing(false); return ok; }} />
        ) : comment.deleted ? (
          <div className="commentDeleted">{t("Comment deleted")}</div>
        ) : (
          <CommentText comment={comment} later={later.has(comment.id)} labelsFor={labelsFor} onBlockRefClick={onBlockRefClick} />
        )}
        {placeText ? <div className="commentThreadState" data-place={place}>{placeText}</div> : null}
      </div>
      {menu ? (
        <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)} aria-label={t("Comment actions")}>
          {mayEdit ? <MenuItem icon={PencilIcon} onClick={() => { setMenu(null); setEditing(true); }}>{t("Edit")}</MenuItem> : null}
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
  const chip = sentChip(thread);
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
  const activate = (e) => {
    if (!canActivate || e.target.closest?.("button, a, input, textarea, .cm-editor, .ctxMenu")) return;
    onActivate(thread.id);
  };
  return (
    <section ref={ref} className={`commentThread${open ? "" : " resolved"}${focused ? " focused" : ""}${active ? " active" : ""}${canActivate ? " canReply" : ""}${collapsed ? " collapsed" : ""}`}
      data-thread-id={thread.id} data-guide="comments.thread"
      onClick={activate}
      tabIndex={canActivate ? 0 : undefined}
      aria-label={canActivate ? (canComment ? t("Reply to this thread") : t("Show this thread")) : undefined}
      onKeyDown={(e) => { if (canActivate && e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onActivate(thread.id); } }}>
      {/* a card opening to its whole thread is another list: nothing comes in */}
      <ListMotion keys={comments.map((c) => c.id)} scope={collapsed} enter="rise" moves={false}>
        {comments.map((c, i) => (
          <Comment key={c.id} comment={c} me={me} canComment={canComment} people={people} {...rest} actions={!collapsed}
            reply={i > 0} resolve={i === 0 && canComment ? { open, onClick: () => onResolve(thread.id, open) } : null}
            chip={i === 0 ? chip : null} place={i === 0 ? place : ""} />
        ))}
      </ListMotion>
      {collapsed && replies > 0 ? (
        <div className="commentReplies">{tn("{n} reply", "{n} replies", replies)}</div>
      ) : null}
      {replying ? (
        <CommentComposer {...rest.field} autoFocus={active} people={people} me={me} draftKey={`reply:${thread.id}`} placeholder={t("Reply…")} submitLabel={t("Send reply")}
          onSubmit={(body) => onReply(thread.id, body)} onEscape={rest.onEscape} />
      ) : null}
      {open ? null : (
        <div className="commentThreadState">
          {thread.resolved_agent || thread.resolved_by
            ? t("Resolved by {name}", { name: thread.resolved_agent || thread.resolved_by }) : t("Resolved")}
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
