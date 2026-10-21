// The comments on one block, in a popover beside what opened it (the row's
// control, the page title's, a highlight's badge, the Comments window):
// its open threads oldest first, each comment with its author, time and
// text rendered as a note is (so @people and [[links]] work), a reply field
// per thread, and "New comment" last (docs/dev/comments.md "In the app").
// One instance, rendered by App through a portal and loaded on first use
// (shared/ui/lazy.jsx). It is placed from its measured size before it is
// painted (shared/ui/placement.js) and follows its anchor as the page
// scrolls; one capture-phase pointerdown outside it closes it, but not one
// in a menu or popup it opened. Past its first screens of text, a comment
// is drawn as it nears the view (comments.js laterComments).
import React, { startTransition, useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useFloatingPosition, placeFloating } from "../shared/ui/placement.js";
import { ContextMenu, MenuButton, MenuItem } from "../shared/ui/Menus";
import { CheckIcon, LinkIcon, PencilIcon, PlusIcon, RefreshIcon, Trash2Icon, XIcon } from "../shared/ui/Icons";
import { PeerAvatar, nameColor } from "../collaboration/Presence";
import { BlockMarkdown } from "../editor/BlockTree";
import { fmtDate, t, tn, T } from "../shared/i18n/i18n.js";
import { formatRelativeTime } from "../library/libraryUtils";
import { touchTyping } from "../shared/lib/pointer.js";
import { CommentComposer, hasDraft } from "./CommentComposer";
import { canDeleteComment, canEditComment, estimatedLines, isEdited, isOpen, laterComments, popoverThreads } from "./comments.js";

const TITLES = {
  note: T("Comments on this note"),
  page: T("Comments on this page"),
  highlight: T("Comments on this highlight"),
};

// The element the popover hangs from: the one that opened it while it is
// in the document, else the block's row control (a row that remounted).
function anchorRect(target) {
  const el = target.anchor?.isConnected ? target.anchor
    : document.querySelector(`[data-comment-for="${CSS.escape(target.blockId)}"]`);
  return el ? el.getBoundingClientRect() : null;
}

// A comment's text, rendered as a note is. One the popover put off
// (`later`) is drawn once it comes within a screen of the popover's view,
// in a transition, so scrolling stays smooth; until then a box of about its
// height stands in.
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
// the ids of the comments drawn as they near the view.
function Comment({ comment, me, owner, canComment, people, labelsFor, later, field, onBlockRefClick, onEdit, onDelete, onCopyLink, resolve = null }) {
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
          {resolve ? (
            <button type="button" className="ctlBtn commentResolve" onClick={resolve.onClick}
              aria-label={resolve.open ? t("Resolve") : t("Reopen")}
              title={resolve.open ? t("Resolve this thread: it leaves the note and stays in the Comments window") : t("Reopen this thread")}>
              {resolve.open ? <CheckIcon size={14} /> : <RefreshIcon size={14} />}
            </button>
          ) : null}
          {!comment.deleted ? <MenuButton open={openMenu} label={t("Comment actions")} /> : null}
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
// reply keeps its field, so a draft is never hidden.
function Thread({ thread, focused, active, onActivate, canComment, me, people, onResolve, onReply, ...rest }) {
  const open = isOpen(thread);
  const ref = useRef(null);
  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: "nearest" });
  }, [focused]);
  const replying = canComment && open && (active || hasDraft(`reply:${thread.id}`));
  const canActivate = canComment && open && !active;
  const activate = (e) => {
    if (!canActivate || e.target.closest?.("button, a, input, textarea, .cm-editor, .ctxMenu")) return;
    onActivate(thread.id);
  };
  return (
    <section ref={ref} className={`commentThread${open ? "" : " resolved"}${focused ? " focused" : ""}${active ? " active" : ""}${canActivate ? " canReply" : ""}`}
      data-thread-id={thread.id}
      onClick={activate}
      tabIndex={canActivate ? 0 : undefined}
      aria-label={canActivate ? t("Reply to this thread") : undefined}
      onKeyDown={(e) => { if (canActivate && e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onActivate(thread.id); } }}>
      {thread.comments.map((c, i) => (
        <Comment key={c.id} comment={c} me={me} canComment={canComment} people={people} {...rest}
          resolve={i === 0 && canComment ? { open, onClick: () => onResolve(thread.id, open) } : null} />
      ))}
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

// target: {blockId, threadId (the thread to show and scroll to), anchor
// (the element it opened from), compose (focus "New comment")}. `threads`:
// the block's threads (open and resolved). `kind`: what the block is
// (note, page, highlight). `canComment`: the reader may write (signed in);
// else a link visitor reads only and is asked to sign in (`onSignIn`).
// onClose(returnFocus): Escape gives the focus back to the anchor.
// `links`: what the fields' [[ and @ pickers read (App's {pages, rootId,
// lookupBlock, refCache, onCacheRef}; CommentComposer.jsx).
export default function ThreadPopover({ target, threads, kind = "note", me, owner, canComment, people, labelsFor, links,
  onBlockRefClick, onAdd, onEdit, onDelete, onResolve, onCopyLink, onSignIn, onClose }) {
  const ref = useRef(null);
  const [withResolved, setWithResolved] = useState(false);
  const shown = popoverThreads(threads, { focus: target.threadId, withResolved });
  const resolvedCount = (threads || []).filter((th) => !isOpen(th)).length;
  const hiddenResolved = (threads || []).filter((th) => !isOpen(th) && !shown.includes(th)).length;
  // The comments shown at opening past its first screens of text, which are
  // drawn as they near the view; one written or shown since is drawn at once.
  const [later] = useState(() => laterComments(shown, { focus: target.threadId }));
  // The thread whose reply field shows: the one a link opened the popover
  // at, then the one last pressed.
  const [activeThread, setActiveThread] = useState(target.threadId || "");
  useEffect(() => { if (target.threadId) setActiveThread(target.threadId); }, [target.threadId]);
  // App keys the popover by its block: another block's is a fresh one. A
  // new request on this block to write (the handle menu's Comment,
  // Mod-Shift-M) opens "New comment" and puts the focus in it.
  const [composing, setComposing] = useState(!!target.compose || !shown.length || hasDraft(`new:${target.blockId}`));
  // (the first target is the mount's, whose field focuses itself)
  const [focusNew, setFocusNew] = useState(0);
  const firstTargetRef = useRef(true);
  useEffect(() => {
    if (firstTargetRef.current) { firstTargetRef.current = false; return; }
    if (!target.compose) return;
    setComposing(true);
    setFocusNew((n) => n + 1);
  }, [target]);
  useEffect(() => {
    if (!focusNew || touchTyping()) return;
    ref.current?.querySelector(".commentNew .cm-content")?.focus({ preventScroll: true });
  }, [focusNew]);
  // The comments' [[link]] clicks reach the latest handler: BlockMarkdown
  // leaves its handlers out of its memo (BlockTree.jsx), so it is given
  // one that keeps its identity.
  const refClickRef = useRef(onBlockRefClick);
  refClickRef.current = onBlockRefClick;
  const refClick = useCallback((...args) => refClickRef.current?.(...args), []);

  const pos = useFloatingPosition(ref, (el) => {
    const size = el.getBoundingClientRect();
    const r = anchorRect(target);
    if (!r) return placeFloating(size, { x: (window.innerWidth - size.width) / 2, y: 80 });
    return placeFloating(size, { x: r.right, y: r.bottom + 4, anchorRight: true, flipY: r.top - 4 });
  }, [target.blockId, target.anchor], { follow: true });

  // A press anywhere else closes it; not one in a menu, a popup or another
  // popover (its own "⋯" menu, a field's [[, @ and "/" menus, its LaTeX
  // preview and completion), nor on its anchor, which toggles.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const down = (e) => {
      const el = e.target;
      if (ref.current?.contains(el)) return;
      if (el.closest?.("[data-popover], .ctxMenu, .refPopup, .slashMenu, .mathPreviewTip, .latexAcPopup")) return;
      if (target.anchor?.contains(el)) return;
      closeRef.current(false);
    };
    document.addEventListener("pointerdown", down, true);
    return () => document.removeEventListener("pointerdown", down, true);
  }, [target.anchor]);

  const escape = () => onClose(true);
  // What every comment field takes besides its own props.
  const field = { blockId: target.blockId, links, labelsFor };
  const common = {
    me, owner, people, labelsFor, later, field, onBlockRefClick: refClick, onEdit, onDelete, onCopyLink, onEscape: escape,
  };
  return createPortal(
    <div
      ref={ref}
      className="popover commentPopover"
      data-popover="thread"
      role="dialog"
      aria-label={t(TITLES[kind] || TITLES.note)}
      style={pos ? { left: pos.left, top: pos.top } : { left: 0, top: 0, visibility: "hidden" }}
      onMouseDown={(e) => e.stopPropagation()}
      onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); escape(); } }}
    >
      <div className="commentPopHead">
        <span className="commentPopTitle">{t(TITLES[kind] || TITLES.note)}</span>
        <button type="button" className="uiClose uiCloseSm" onClick={() => onClose(true)} aria-label={t("Close")} title={t("Close")}>
          <XIcon size={14} />
        </button>
      </div>
      <div className="commentPopBody">
        {shown.map((th) => (
          <Thread key={th.id} thread={th} focused={th.id === target.threadId} canComment={canComment}
            active={th.id === activeThread} onActivate={setActiveThread}
            onResolve={onResolve} onReply={(threadId, body) => onAdd(target.blockId, body, threadId)} {...common} />
        ))}
        {hiddenResolved ? (
          <button type="button" className="uiBtn sm ghost commentShowResolved" onClick={() => setWithResolved(true)}>
            {tn("Show {n} resolved thread", "Show {n} resolved threads", hiddenResolved)}
          </button>
        ) : withResolved && resolvedCount ? (
          <button type="button" className="uiBtn sm ghost commentShowResolved" onClick={() => setWithResolved(false)}>
            {t("Hide resolved threads")}
          </button>
        ) : null}
        {!canComment ? (
          <div className="commentSignIn">
            {onSignIn ? <button type="button" className="uiBtn sm primary" onClick={onSignIn}>{t("Sign in to comment")}</button>
              : t("Sign in to comment")}
          </div>
        ) : composing ? (
          <div className="commentNew">
            <CommentComposer {...field} autoFocus people={people} me={me} draftKey={`new:${target.blockId}`}
              placeholder={shown.length ? t("New comment…") : t("Write a comment…")} submitLabel={t("Comment")}
              // stored, the thread shows with its reply field; "New comment" starts another
              onSubmit={async (body) => { const ok = await onAdd(target.blockId, body, ""); if (ok) setComposing(false); return ok; }}
              onEscape={escape} />
          </div>
        ) : (
          <button type="button" className="uiBtn sm ghost commentNewBtn" onClick={() => setComposing(true)}>
            <PlusIcon size={14} /> {t("New comment")}
          </button>
        )}
      </div>
    </div>,
    document.body,
  );
}
