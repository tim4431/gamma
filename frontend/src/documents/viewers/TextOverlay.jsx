// What the code view draws beside its text (docs/dev/documents.md
// "Viewers", docs/dev/comments.md "The margin"), in two layers, since the
// scroller scrolls both ways when lines do not wrap: what belongs to the
// text rides with it, the column does not.
//
// The badges (`.docTextOverlay`) lie inside CodeMirror's scroller over the
// content's box, so they scroll with the text both ways: a NoteBadge at the
// end of a range's last line (a lost range's beside its tag) for a range
// with a note, or with threads no card shows (a phone, no room).
//
// The comment column (`.docTextMarginHost`) stands outside the scroller, at
// the view's right edge however far the text is scrolled sideways: a track
// moved with the text vertically alone (translateY of the scroller's
// scrollTop, written on its scroll event, no render), holding the one
// CommentMarginSlot, whose cards stand level with their ranges' first lines
// (`topOf`, in the content's px, as the badges are). It stands while a range
// has an open thread or a new comment's card and the view has room for it
// beside the text; the content then keeps that room at its right
// (`onBeside`). Every place is read in CodeMirror's measure phase, again as
// its geometry, viewport or text changes.
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CommentMarginContext, CommentMarginSlot, MarginStackContext, createMarginStack } from "../../comments/marginSlot";
import NoteBadge from "../../comments/NoteBadge.jsx";
import { hasRoom, standingLine } from "./textMarks.js";

const EMPTY = Object.freeze([]);

const sameMap = (a, b) => a.size === b.size && [...a].every(([k, v]) => {
  const w = b.get(k);
  return typeof v === "object" ? !!w && v.x === w.x && v.y === w.y : v === w;
});
const sameLayout = (a, b) => !!a && !!b && ["left", "top", "width", "height", "gutterW", "scrollerW", "scrollerTop", "scrollerH"]
  .every((k) => a[k] === b[k]) && sameMap(a.tops, b.tops) && sameMap(a.badges, b.badges);

// `view` the EditorView; `updates` the viewer's set of update listeners;
// `regions`, `anchors`, `commentMargin`, `marginRef`, `onMarginLost`,
// `onRegionJump`, `onRegionContext`, `onRegionThread` as TextViewer takes
// them; `phone` a phone's layout (no column); `onBeside(on)` says whether
// the column stands.
export default function TextOverlay({ view, updates, phone = false, regions = EMPTY, anchors = EMPTY, commentMargin = null, marginRef = null,
  onMarginLost = null, onRegionJump = null, onRegionContext = null, onRegionThread = null, onBeside = null }) {
  const [layout, setLayout] = useState(null);
  const room = !!layout && !phone && hasRoom(layout.scrollerW, layout.gutterW);
  const target = commentMargin?.target || null;
  const composing = target?.compose ? target.blockId : "";
  const beside = !!commentMargin && room && anchors.some((a) => a.threadCount > 0 || a.id === composing);
  const carded = (h) => beside && (h.threadCount > 0 || composing === h.id);
  const badged = regions.filter((h) => h.hasNote || (h.threadCount && !carded(h)));

  // What the measure reads: the cards' lines and the badges' places.
  const wanted = useRef({ anchors, badged });
  wanted.current = { anchors, badged };
  const key = useMemo(() => ({}), []);
  const measure = useRef(null);
  measure.current = () => view.requestMeasure({
    key,
    read(v) {
      const content = v.contentDOM, scroller = v.scrollDOM, doc = v.state.doc;
      if (!scroller.clientWidth) return null; // the pane is closed: the places stand as they were
      const pad = content.offsetTop + v.documentPadding.top;
      const box = content.getBoundingClientRect();
      const tops = new Map();
      for (const a of wanted.current.anchors) {
        const n = standingLine(a.position, doc.lines);
        if (n) tops.set(a.id, pad + v.lineBlockAt(doc.line(n).from).top);
      }
      const badges = new Map();
      for (const h of wanted.current.badged) {
        const end = badgeEnd(v, h, box);
        if (end) badges.set(h.id, end);
      }
      return {
        left: content.offsetLeft, top: content.offsetTop, width: Math.max(content.offsetWidth, content.scrollWidth), height: content.offsetHeight,
        gutterW: content.offsetLeft, scrollerW: scroller.clientWidth, scrollerTop: scroller.offsetTop, scrollerH: scroller.clientHeight, tops, badges,
      };
    },
    write(next) { if (next) setLayout((prev) => (sameLayout(prev, next) ? prev : next)); },
  });
  useEffect(() => {
    const fn = (u) => { if (u.geometryChanged || u.viewportChanged || u.docChanged || u.heightChanged) measure.current(); };
    updates.add(fn);
    measure.current();
    return () => { updates.delete(fn); };
  }, [view, updates]);
  useEffect(() => { measure.current(); }, [anchors, regions, room, beside, composing]);

  // App asks, before it opens a range's thread, whether its card can stand
  // beside the text; a resize or a wrap that takes the room away from the
  // active card hands it to the popover.
  if (marginRef) marginRef.current = (id) => !!commentMargin && room && anchors.some((a) => a.id === id);
  useEffect(() => () => { if (marginRef) marginRef.current = null; }, [marginRef]);
  const lostRef = useRef(onMarginLost);
  lostRef.current = onMarginLost;
  const targetHere = !!target && anchors.some((a) => a.id === target.blockId);
  const measured = !!layout;
  useEffect(() => { if (targetHere && measured && !room) lostRef.current?.(); }, [targetHere, room, measured]);
  const besideRef = useRef(onBeside);
  besideRef.current = onBeside;
  useLayoutEffect(() => { besideRef.current?.(beside); }, [beside]);
  useEffect(() => () => besideRef.current?.(false), []);

  // The track follows the text's vertical scroll alone.
  const trackRef = useRef(null);
  useLayoutEffect(() => {
    const track = trackRef.current, scroller = view.scrollDOM;
    if (!track) return undefined;
    const follow = () => { track.style.transform = `translateY(${-scroller.scrollTop}px)`; };   // i18n-ignore: a CSS value
    follow();
    scroller.addEventListener("scroll", follow, { passive: true });
    return () => scroller.removeEventListener("scroll", follow);
  }, [view, beside]);
  const [stack] = useState(createMarginStack);

  if (!layout) return null;
  const topOf = (a) => layout.tops.get(a.id) ?? 0;
  return (
    <>
      {createPortal(
        <div className="docTextOverlay" style={{ left: layout.left, top: layout.top, width: layout.width, height: layout.height }}>
          {badged.map((h) => {
            const at = layout.badges.get(h.id);
            if (!at) return null;
            const openMenu = onRegionContext ? (e) => { e.preventDefault(); onRegionContext({ id: h.id, x: e.clientX, y: e.clientY }); } : null;
            return (
              <NoteBadge key={h.id} hlId={h.id} text={h.note?.trim() || ""} hasNote={!!h.hasNote} threads={carded(h) ? 0 : h.threadCount || 0}
                style={{ left: at.x + 2, top: at.y - 8 }}
                onThread={(el) => onRegionThread?.(h.id, el)}
                onClick={onRegionJump ? (e) => { e.stopPropagation(); onRegionJump(h.id, e.ctrlKey || e.metaKey); } : undefined}
                onMenu={openMenu} />
            );
          })}
        </div>,
        view.scrollDOM,
      )}
      {beside ? createPortal(
        <div className="docTextMarginHost" style={{ top: layout.scrollerTop, height: layout.scrollerH }}>
          <div ref={trackRef} className="docTextMarginTrack">
            <CommentMarginContext.Provider value={commentMargin}>
              <MarginStackContext.Provider value={stack}>
                <CommentMarginSlot pageNumber={1} anchors={anchors} pageH={0} pageTop={0} topOf={topOf} />
              </MarginStackContext.Provider>
            </CommentMarginContext.Provider>
          </div>
        </div>,
        view.dom,
      ) : null}
    </>
  );
}

// Where a range's badge stands, {x, y} in the content's px: past the end of
// its last line's text, or a lost range's past its tag; null while that
// line is not drawn (CodeMirror draws the lines near the view alone).
function badgeEnd(view, h, box) {
  if (h.position?.lost) {
    const tag = view.contentDOM.querySelector(`.docHlLost[data-hl-id="${CSS.escape(h.id)}"]`);
    if (!tag) return null;
    const r = tag.getBoundingClientRect();
    return { x: Math.round(r.right - box.left), y: Math.round(r.top - box.top) };
  }
  const mark = view.contentDOM.querySelectorAll(`[data-hl-id="${CSS.escape(h.id)}"]`);
  const last = mark[mark.length - 1];
  if (!last) return null;
  const rects = last.getClientRects();
  const r = rects[rects.length - 1] || last.getBoundingClientRect();
  return { x: Math.round(r.right - box.left), y: Math.round(r.top - box.top) };
}
