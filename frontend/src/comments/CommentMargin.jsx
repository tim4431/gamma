// One page's comment margin beside the PDF (docs/dev/comments.md "The PDF
// margin"): a card per open thread of each highlight on the page, level
// with the highlight's first line, stacked so none overlaps, across the
// pages too: a page's cards start under the last card of the pages above
// (marginLayout.js stackCards, marginSlot.jsx createMarginStack). A card at rest shows its thread's first
// comment, clamped, and how many replies follow; the active one is the
// whole thread as the popover draws it (Thread.jsx), with its reply field.
// A new comment opens as a card of its own, its field focused. Loaded on
// first use (marginSlot.jsx).
import React, { useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { t } from "../shared/i18n/i18n.js";
import { CommentComposer } from "./CommentComposer";
import { SignInToComment, Thread, useLatestHandler } from "./Thread";
import { isOpen, laterComments } from "./comments.js";
import { CARD_GAP, CARD_W, COLUMN_GAP, stackCards } from "./marginLayout.js";
import { MarginStackContext } from "./marginSlot";

// What a card measures before it is drawn (a collapsed card of two lines).
const CARD_H = 72;
// A press in one of these is not a press outside the active card: another
// card, a menu or popup the card opened (its "⋯", a field's [[, @ and "/"
// menus, its LaTeX preview and completion), the popover.
const INSIDE = "[data-comment-margin], [data-popover], .ctxMenu, .refPopup, .slashMenu, .mathPreviewTip, .latexAcPopup";
const NO_STACK = () => () => {};

// The key of the card that is active: the new comment's, else the thread
// the target names, else the block's first open thread.
function activeKey(target, threadsByBlock) {
  if (!target) return "";
  if (target.compose) return `new:${target.blockId}`;
  return target.threadId || (threadsByBlock.get(target.blockId) || []).find(isOpen)?.id || "";
}

// A thread's card: the first comment at rest, the whole thread once active.
// Its comments past the first screens of text are drawn as they near the
// view, counted when it becomes active (comments.js laterComments).
function ThreadCard({ thread, active, margin, onActivate, onEscape }) {
  const { me, owner, canComment, people, labelsFor, links, onAdd, onEdit, onDelete, onResolve, onCopyLink, onSignIn, onBlockRefClick } = margin;
  const later = useMemo(() => (active ? laterComments([thread]) : new Set()), [active]); // eslint-disable-line react-hooks/exhaustive-deps
  const field = { blockId: thread.block_id, links, labelsFor };
  return (
    <>
      <Thread thread={thread} summary active={active} onActivate={onActivate} canComment={canComment}
        me={me} owner={owner} people={people} labelsFor={labelsFor} later={later} field={field}
        onBlockRefClick={onBlockRefClick} onEdit={onEdit} onDelete={onDelete} onCopyLink={onCopyLink} onEscape={onEscape}
        // resolved, the card leaves the margin and none is active
        onResolve={(id, open) => { onResolve(id, open); if (open) onEscape(); }}
        onReply={(threadId, body) => onAdd(thread.block_id, body, threadId)} />
      {active && !canComment ? <SignInToComment onSignIn={onSignIn} /> : null}
    </>
  );
}

// A new thread on the highlight: its field, focused. Stored, the thread's
// own card takes its place as the active one.
function NewCard({ blockId, margin, onEscape }) {
  const { me, canComment, people, labelsFor, links, onAdd, onSignIn, onActivate } = margin;
  if (!canComment) return <SignInToComment onSignIn={onSignIn} />;
  return (
    <div className="commentThread active commentNew">
      <CommentComposer blockId={blockId} links={links} labelsFor={labelsFor} autoFocus people={people} me={me}
        draftKey={`new:${blockId}`} placeholder={t("Write a comment…")} submitLabel={t("Comment")}
        onSubmit={async (body) => { const id = await onAdd(blockId, body, ""); if (id) onActivate(blockId, id); return id; }}
        onEscape={onEscape} />
    </div>
  );
}

// `anchors`: the page's highlights that have cards, {id, top} (px from the
// page's top); `pageTop`: the page's top, px from the first page's top.
// `margin`: the context App provides (marginSlot.jsx).
export default function CommentMargin({ margin, anchors, pageNumber, pageTop = 0 }) {
  const { threadsByBlock, target } = margin;
  const cards = [];
  for (const a of anchors) {
    for (const th of threadsByBlock.get(a.id) || []) {
      if (isOpen(th)) cards.push({ key: th.id, blockId: a.id, top: a.top, thread: th });
    }
    if (target?.compose && target.blockId === a.id) cards.push({ key: `new:${a.id}`, blockId: a.id, top: a.top, compose: true });
  }
  const active = activeKey(target, threadsByBlock);
  const hasActive = cards.some((c) => c.key === active);

  // Each card's height as drawn, measured before the paint and again when
  // it changes size (a thread opening, a field growing, a formula drawn).
  // Compared with the last measure, not in a state updater: React re-ran
  // the updater against an older state as the observer's updates and the
  // layout effect's interleaved, so it kept finding a change and looped.
  const boxes = useRef(new Map());
  const [heights, setHeights] = useState(() => new Map());
  const measured = useRef(heights);
  const measure = useCallback(() => {
    const prev = measured.current;
    let same = prev.size === boxes.current.size;
    const next = new Map();
    for (const [key, el] of boxes.current) {
      next.set(key, el.offsetHeight);
      if (prev.get(key) !== el.offsetHeight) same = false;
    }
    if (same) return;
    measured.current = next;
    setHeights(next);
  }, []);
  const observer = useRef(null);
  useEffect(() => {
    const ro = new ResizeObserver(measure);
    for (const el of boxes.current.values()) ro.observe(el);
    observer.current = ro;
    return () => { ro.disconnect(); observer.current = null; };
  }, [measure]);
  const refs = useRef(new Map());
  const boxRef = (key) => {
    if (!refs.current.has(key)) {
      refs.current.set(key, (el) => {
        const was = boxes.current.get(key);
        if (was) observer.current?.unobserve(was);
        if (el) { boxes.current.set(key, el); observer.current?.observe(el); } else { boxes.current.delete(key); refs.current.delete(key); }
      });
    }
    return refs.current.get(key);
  };
  useLayoutEffect(measure);
  // The cards start under the last card of the pages above, so a card grown
  // at a page's foot pushes the next page's down; with none above, no
  // higher than the first page's top, out of the reader's reach.
  const stack = useContext(MarginStackContext);
  const floor = useSyncExternalStore(stack ? stack.subscribe : NO_STACK, () => (stack ? stack.floor(pageNumber) : null));
  const min = (floor == null ? 0 : floor + CARD_GAP) - pageTop;
  const sized = cards.map((c) => ({ key: c.key, top: c.top, height: heights.get(c.key) ?? CARD_H }));
  const tops = stackCards(sized, { active, min });
  // and tell the pages below where these end
  const bottom = sized.length ? pageTop + Math.max(...sized.map((c) => tops.get(c.key) + c.height)) : null;
  useLayoutEffect(() => { stack?.set(pageNumber, bottom); }, [stack, pageNumber, bottom]);
  useLayoutEffect(() => () => stack?.set(pageNumber, null), [stack, pageNumber]);

  const close = useLatestHandler(margin.onClose);
  const activate = useLatestHandler(margin.onActivate);
  // A press outside the margin (and off the card's own highlight), or
  // Escape, makes no card active.
  useEffect(() => {
    if (!hasActive) return undefined;
    const own = target.blockId;
    const down = (e) => {
      const el = e.target;
      if (el.closest?.(INSIDE) || el.closest?.(`[data-hl-id="${CSS.escape(own)}"]`)) return;
      close();
    };
    const key = (e) => { if (e.key === "Escape" && !e.defaultPrevented) close(); };
    document.addEventListener("pointerdown", down, true);
    window.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", down, true);
      window.removeEventListener("keydown", key);
    };
  }, [hasActive, target?.blockId, close]);

  return (
    <div className={`commentMargin${hasActive ? " hasActive" : ""}`} data-comment-margin={pageNumber} style={{ left: `calc(100% + ${COLUMN_GAP}px)`, width: CARD_W }}>
      {cards.map((c) => (
        <div key={c.key} ref={boxRef(c.key)} className={`commentCard${c.key === active ? " active" : ""}`}
          data-card-for={c.blockId} style={{ top: tops.get(c.key) ?? c.top }}
          onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); } }}>
          {c.compose ? (
            <NewCard blockId={c.blockId} margin={margin} onEscape={close} />
          ) : (
            <ThreadCard thread={c.thread} active={c.key === active} margin={margin}
              onActivate={(threadId) => activate(c.blockId, threadId)} onEscape={close} />
          )}
        </div>
      ))}
    </div>
  );
}
