// Where a viewer hosts its comment margin (docs/dev/comments.md "The
// margin"): App provides what the cards read (`CommentMarginContext`:
// the threads, the active card, the handlers the popover takes), and each
// rendered page that has room draws a `CommentMarginSlot`. The slot reads
// the context, so a new comment re-renders the margins and not the pages;
// the cards themselves (CommentMargin.jsx) load on first use. The pages'
// margins are one column: each tells the viewer's `MarginStackContext`
// where its last card ends, and a later page's cards start under that.
import React, { createContext, useContext } from "react";
import { LazyBoundary, lazySurface } from "../shared/ui/lazy";
import { anchorTop, cardedAnchors } from "./comments.js";

export const CommentMarginContext = createContext(null);
export const MarginStackContext = createContext(null);

// Where each drawn page's cards end, in px from the first page's top (one
// per viewer). `floor(page)`: the lowest end among the pages above `page`,
// null when none of them has cards; `set(page, bottom)` (null: none now)
// tells the later pages' margins, which read it with
// useSyncExternalStore(subscribe). A page's floor comes from the pages
// above it only, so the margins settle page by page.
export function createMarginStack() {
  const bottoms = new Map();
  const subs = new Set();
  return {
    floor(page) {
      let low = null;
      for (const [p, b] of bottoms) if (p < page && (low == null || b > low)) low = b;
      return low;
    },
    set(page, bottom) {
      if (bottom == null ? !bottoms.has(page) : bottoms.get(page) === bottom) return;
      if (bottom == null) bottoms.delete(page); else bottoms.set(page, bottom);
      for (const fn of subs) fn();
    },
    subscribe(fn) {
      subs.add(fn);
      return () => subs.delete(fn);
    },
  };
}

const CommentMargin = lazySurface(() => import("./CommentMargin"));
export const preloadCommentMargin = () => CommentMargin.preload();

// `anchors`: the page's comment anchors (the viewer's, app/treeDerived.js
// commentAnchors: its highlights and ink groups, with `threadCount`);
// `pageH`: the page box's height at the current zoom; `pageTop`: its top,
// in px from the first page's top; `topOf(anchor)`, when given, the top of
// an anchor's card in px of the slot itself, in place of its rectangles'
// (the code view, whose ranges stand at lines). The column stands while it
// has no card too, so a page's first card grows in and its last one
// shrinks away.
export function CommentMarginSlot({ pageNumber, anchors, pageH, pageTop, topOf }) {
  const margin = useContext(CommentMarginContext);
  if (!margin) return null;
  const composing = margin.target?.compose ? margin.target.blockId : "";
  const carded = cardedAnchors(anchors, composing, topOf || ((a) => anchorTop(a, pageH)));
  return (
    <LazyBoundary>
      <CommentMargin margin={margin} anchors={carded} pageNumber={pageNumber} pageTop={pageTop} />
    </LazyBoundary>
  );
}
