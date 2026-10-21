// A comment thread as both comment surfaces draw it (docs/dev/comments.md
// "In the app"): the thread popover (ThreadPopover.jsx) lists a block's
// threads, and the PDF's margin (CommentMargin.jsx) shows one card per
// thread beside its highlight. Each comment with its author, time and text
// rendered as a note is (so @people and [[links]] work), its "⋯" menu, the
// thread's Resolve and its reply field once the thread is the active one.
import React, { startTransition, useCallback, useEffect, useRef, useState } from "react";
import { ContextMenu, MenuButton, MenuItem } from "../shared/ui/Menus";
import { CheckIcon, LinkIcon, PencilIcon, RefreshIcon, Trash2Icon } from "../shared/ui/Icons";
import { PeerAvatar, nameColor } from "../collaboration/Presence";
import { BlockMarkdown } from "../editor/BlockTree";
import { fmtDate, t, tn } from "../shared/i18n/i18n.js";
import { formatRelativeTime } from "../library/libraryUtils";
import { CommentComposer, hasDraft } from "./CommentComposer";
import { canDeleteComment, canEditComment, estimatedLines, isEdited, isOpen } from "./comments.js";

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
// Resolve (or Reopen) button on its author line, beside the "⋯". `later`:
// the ids of the comments drawn as they near the view. Without `actions`
// (a margin card at rest) the line has neither.
function Comment({ comment, me, owner, canComment, people, labelsFor, later, field, onBlockRefClick, onEdit, onDelete, onCopyLink, resolve = null, actions = true }) {
  const [menu, setMenu] = useState(null);
  const [editing, setEditing] = useState(() => hasDraft(`edit:${comment.id}`)); // an edit left unsaved
  const mayEdit = canComment && canEditComment(comment, me);
  const mayDelete = canComment && canDeleteComment(comment, me, owner);
  const openMenu = (e) => setMenu({ x: e.clientX, y: e.clientY });
  return (
    <div className="commentItem rowMenuHost" data-comment-id={comment.id}>
      <PeerAvatar peer={{ name: comment.author || "?", color: nameColor(comment.author || "?"), anchor: 0 }} title={comment.author} />
      <div className="commentMain">
        <div className="commentMeta">
          <span className="commentAuthor">{comment.author || t("Someone")}</span>
          <span className="commentTime" title={fmtDate(comment.created_at, { dateStyle: "medium", timeStyle: "short" })}>
            {formatRelativeTime(comment.created_at)}
          </span>
          {isEdited(comment) ? <span className="commentEdited">{t("edited")}</span> : null}
          <span className="commentMetaSpacer" />
          {resolve && actions ? (
            <button type="button" className="ctlBtn commentResolve" onClick={resolve.onClick}
              aria-label={resolve.open ? t("Resolve") : t("Reopen")}
              title={resolve.open ? t("Resolve this thread: it leaves the note and stays in the Comments window") : t("Reopen this thread")}>
              {resolve.open ? <CheckIcon size={14} /> : <RefreshIcon size={14} />}
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
      </div>
      {menu ? (
        <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)}>
          {mayEdit ? <MenuItem icon={PencilIcon} onClick={() => { setMenu(null); setEditing(true); }}>{t("Edit")}</MenuItem> : null}
          <MenuItem icon={LinkIcon} onClick={() => { setMenu(null); onCopyLink(comment); }}>{t("Copy link")}</MenuItem>
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
      data-thread-id={thread.id}
      onClick={activate}
      tabIndex={canActivate ? 0 : undefined}
      aria-label={canActivate ? (canComment ? t("Reply to this thread") : t("Show this thread")) : undefined}
      onKeyDown={(e) => { if (canActivate && e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onActivate(thread.id); } }}>
      {comments.map((c, i) => (
        <Comment key={c.id} comment={c} me={me} canComment={canComment} people={people} {...rest} actions={!collapsed}
          resolve={i === 0 && canComment ? { open, onClick: () => onResolve(thread.id, open) } : null} />
      ))}
      {collapsed && replies > 0 ? (
        <div className="commentReplies">{tn("{n} reply", "{n} replies", replies)}</div>
      ) : null}
      {replying ? (
        <CommentComposer {...rest.field} autoFocus={active} people={people} me={me} draftKey={`reply:${thread.id}`} placeholder={t("Reply…")} submitLabel={t("Send reply")}
          onSubmit={(body) => onReply(thread.id, body)} onEscape={rest.onEscape} />
      ) : null}
      {open ? null : (
        <div className="commentThreadState">
          {thread.resolved_by ? t("Resolved by {name}", { name: thread.resolved_by }) : t("Resolved")}
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

// A handler that keeps its identity and calls the latest one given: the
// comments' [[link]] clicks, since BlockMarkdown leaves its handlers out of
// its memo (BlockTree.jsx).
export function useLatestHandler(fn) {
  const ref = useRef(fn);
  ref.current = fn;
  return useCallback((...args) => ref.current?.(...args), []);
}
