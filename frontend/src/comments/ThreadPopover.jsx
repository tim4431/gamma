// The comments on one block, in a popover beside what opened it (the row's
// control, the page title's, a highlight's badge, the Comments window):
// its open threads oldest first, each drawn by Thread.jsx (which the PDF's
// margin cards share), and "New comment" last (docs/dev/comments.md "In the
// app").
// One instance, rendered by App through a portal and loaded on first use
// (shared/ui/lazy.jsx). It is placed from its measured size before it is
// painted (shared/ui/placement.js) and follows its anchor as the page
// scrolls; one capture-phase pointerdown outside it closes it, but not one
// in a menu or popup it opened. Past its first screens of text, a comment
// is drawn as it nears the view (comments.js laterComments).
import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLeave } from "../shared/ui/leave.js";
import { growFrom, placeFloating, surfaceSize, useFloatingPosition } from "../shared/ui/placement.js";
import { PlusIcon, XIcon } from "../shared/ui/Icons";
import { t, tn, T } from "../shared/i18n/i18n.js";
import { touchTyping } from "../shared/lib/pointer.js";
import { CommentComposer, hasDraft } from "./CommentComposer";
import { COMMENT_POPUPS, SignInToComment, Thread, useLatestHandler } from "./Thread";
import { isOpen, laterComments, popoverThreads, threadAnchor } from "./comments.js";

const TITLES = {
  note: T("Comments on this note"),
  page: T("Comments on this page"),
  highlight: T("Comments on this highlight"),
};

// The box of what the popover hangs from (comments.js threadAnchor).
function anchorRect(target) {
  return threadAnchor(target)?.getBoundingClientRect() || null;
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
  const refClick = useLatestHandler(onBlockRefClick);

  // Under its anchor by their right edges, growing from that corner
  // (growFrom); with no anchor in sight, centred near the top.
  const pos = useFloatingPosition(ref, (el) => {
    const size = surfaceSize(el);
    const r = anchorRect(target);
    if (!r) return { ...placeFloating(size, { x: (window.innerWidth - size.width) / 2, y: 80 }), origin: "50% 0" };
    const at = placeFloating(size, { x: r.right, y: r.bottom + 4, anchorRight: true, flipY: r.top - 4 });
    return { ...at, origin: growFrom(r.right, r.bottom + 4, at) };
  }, [target.blockId, target.anchor], { follow: true });
  const leaveRef = useLeave("surface", ref);

  // A press anywhere else closes it; not one in a menu, a popup or another
  // popover (its own "⋯" menu, a field's [[, @ and "/" menus, its LaTeX
  // preview and completion), nor on its anchor, which toggles.
  const close = useLatestHandler(onClose);
  useEffect(() => {
    const down = (e) => {
      const el = e.target;
      if (ref.current?.contains(el)) return;
      if (el.closest?.(COMMENT_POPUPS)) return;
      if (target.anchor?.contains(el)) return;
      close(false);
    };
    document.addEventListener("pointerdown", down, true);
    return () => document.removeEventListener("pointerdown", down, true);
  }, [target.anchor, close]);

  const escape = () => onClose(true);
  // What every comment field takes besides its own props.
  const field = { blockId: target.blockId, links, labelsFor };
  const common = {
    me, owner, people, labelsFor, later, field, onBlockRefClick: refClick, onEdit, onDelete, onCopyLink, onEscape: escape,
  };
  return createPortal(
    <div
      ref={leaveRef}
      className="popover commentPopover"
      data-popover="thread"
      role="dialog"
      aria-label={t(TITLES[kind] || TITLES.note)}
      style={pos ? { left: pos.left, top: pos.top, transformOrigin: pos.origin } : { left: 0, top: 0, visibility: "hidden" }}
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
          <SignInToComment onSignIn={onSignIn} />
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
