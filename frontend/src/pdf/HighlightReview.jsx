// The review card of a highlight placed after its PDF was replaced
// (docs/dev/highlight_relocation.md): one the run drew dashed (a rough
// match, or an area placed by estimate) is pending until the person
// accepts or moves it. Hovering it on the page opens, after a moment, a
// card anchored to it: how alike the texts were, a picture of where it was
// in the previous file with its old rectangles drawn over it, its old quote
// against its current one word by word, and for an editor Accept, Move…
// and Next ("{i} of {n}" pending on the page). The pointer may cross into
// the card; leaving both closes it, as the note badge's tip does
// (PdfViewer.jsx NoteBadge). Touch has no hover, so a tap on a pending
// highlight opens it instead of the tap's usual action (a second tap does
// that action), and a tap outside closes it; so does Escape. One opened by
// a tap, by Next or from the Document menu stays until then.
//
// Only pending highlights' rectangles listen for the pointer (PdfPage), so
// a page of many highlights costs nothing more. The arithmetic is
// highlightReview.js's.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { t } from "../shared/i18n/i18n.js";
import { API, getShareToken, withWorkspace } from "../shared/lib/utils";
import { lastPointer } from "../shared/lib/pointer.js";
import { roughMatchPercent } from "../shared/model/blockModel.js";
import { DEFAULT_HIGHLIGHT, darkPageColor, normalizeColor } from "../shared/model/palette.js";
import { CheckIcon, ChevronRightIcon, LocateIcon } from "../shared/ui/Icons";
import { placeFloating, surfaceSize, useFloatingPosition } from "../shared/ui/placement.js";
import { PAGE_ASPECT, beforeCrop, cropAspect, pendingInOrder, quoteDiff, rectsInCrop, reviewPlace, reviewPrev } from "./highlightReview.js";

// The hover's delays: the card shows once the pointer rests on the
// highlight, and waits a little as it leaves, so the pointer can cross the
// gap into the card.
const SHOW_MS = 150;
const HIDE_MS = 200;
// Gap between the highlight and the card.
const GAP = 6;
// The before picture's tallest, so a figure's area does not fill the window.
const SHOT_MAX_H = 220;
// How long a card opened for a highlight not yet drawn (Next, the Document
// menu: its page still rendering) waits for it.
const WAIT_TRIES = 40, WAIT_MS = 100;

// Which card is open: {id, pinned} or null (pinned: opened by a tap, by
// Next or from the Document menu, so leaving it does not close it), and
// the handlers PdfPage's rectangles and the card call. The handlers are
// stable, so PdfPage's memo holds.
export function useHighlightReview() {
  const [card, setCard] = useState(null);
  const cardRef = useRef(null);
  cardRef.current = card;
  const timer = useRef(0);
  const review = useMemo(() => {
    const later = (ms, fn) => { clearTimeout(timer.current); timer.current = setTimeout(fn, ms); };
    const hideHovered = () => setCard((now) => (now && !now.pinned ? null : now));
    return {
      // The pointer over (or off) a pending highlight's rectangles.
      hover(id, over) {
        if (lastPointer() !== "mouse") return; // a tap fires compatibility mouse events too
        const now = cardRef.current;
        if (over) {
          if (now?.id === id) clearTimeout(timer.current);
          else later(SHOW_MS, () => setCard({ id, pinned: false }));
        } else if (now && !now.pinned) later(HIDE_MS, hideHovered);
        else clearTimeout(timer.current); // the card on its way for it is not wanted any more
      },
      // A tap on a pending highlight: true when it opened the card, which
      // the tap then does instead of its usual action.
      tap(id) {
        if (lastPointer() === "mouse") return false;
        clearTimeout(timer.current);
        if (cardRef.current?.id === id) { setCard(null); return false; }
        setCard({ id, pinned: true });
        return true;
      },
      open(id) { clearTimeout(timer.current); setCard({ id, pinned: true }); },
      close() { clearTimeout(timer.current); setCard(null); },
      // The pointer entering the card keeps it; leaving it closes a hovered one.
      hold() { clearTimeout(timer.current); },
      leave() { if (cardRef.current && !cardRef.current.pinned) later(HIDE_MS, hideHovered); },
    };
  }, []);
  useEffect(() => () => clearTimeout(timer.current), []);
  return { card, review };
}

// The card, for the open one while its highlight is still pending; it goes
// when the highlight is accepted, moved, deleted or undone back to solid.
// `onAccept(id)` and `onMove(id)` are an editor's (absent, the card has no
// actions); `onNext(id)` scrolls to that highlight and opens its card.
export function HighlightReview({ card, review, highlights, viewerRef, darkPage = false, onAccept, onMove, onNext }) {
  const pending = useMemo(() => pendingInOrder(highlights), [highlights]);
  const h = card ? pending.find((x) => x.id === card.id) : null;
  useEffect(() => { if (card && !h) review.close(); }, [card, h, review]);
  if (!card || !h) return null;
  return createPortal(
    <ReviewCard key={h.id} h={h} pinned={card.pinned} place={reviewPlace(pending, h.id)} review={review} viewerRef={viewerRef}
      darkPage={darkPage} onAccept={onAccept} onMove={onMove} onNext={onNext} />,
    document.body,
  );
}

// The highlight's rectangles on screen, as one box, or null while they are
// not drawn (their page not rendered yet).
function anchorRect(viewer, id) {
  const els = viewer?.querySelectorAll(`[data-page] > div[data-hl-id="${CSS.escape(id)}"]`) || [];
  let box = null;
  for (const el of els) {
    const r = el.getBoundingClientRect();
    box = box ? { left: Math.min(box.left, r.left), top: Math.min(box.top, r.top), right: Math.max(box.right, r.right), bottom: Math.max(box.bottom, r.bottom) }
      : { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
  }
  return box;
}

function ReviewCard({ h, pinned, place, review, viewerRef, darkPage, onAccept, onMove, onNext }) {
  const ref = useRef(null);
  const [tick, setTick] = useState(0);
  const focusedRef = useRef(false);
  // Under the highlight, else over it; kept inside the window, following
  // the page as it scrolls. Hidden while the highlight is out of the
  // viewer's sight, and while it is not drawn yet (`missing`).
  const pos = useFloatingPosition(ref, (el) => {
    const viewer = viewerRef.current;
    const a = anchorRect(viewer, h.id);
    if (!a) return { left: 0, top: 0, hidden: true, missing: true };
    const v = viewer.getBoundingClientRect();
    const seen = a.bottom > v.top && a.top < v.bottom && a.right > v.left && a.left < v.right;
    const spot = placeFloating(surfaceSize(el), { x: a.left, y: a.bottom + GAP, flipY: a.top - GAP });
    return { ...spot, hidden: !seen, missing: false };
  }, [h.id, h.position, tick], { follow: true });
  // A card opened for a highlight whose page is still rendering waits for
  // its rectangles, and gives up after a few seconds.
  const missing = !pos || pos.missing;
  useEffect(() => {
    if (!missing) return undefined;
    let tries = 0;
    const id = setInterval(() => {
      if (++tries > WAIT_TRIES) review.close();
      else setTick((n) => n + 1);
    }, WAIT_MS);
    return () => clearInterval(id);
  }, [missing, review]);
  // One opened by a tap, by Next or from the Document menu takes the focus
  // once it is seen, so the keys reach its buttons (Tab) and Next's own
  // focus is not lost with the card it left; a hovered one leaves the focus
  // where it is.
  const shown = !!pos && !pos.hidden;
  useEffect(() => {
    if (!pinned || !shown || focusedRef.current) return;
    focusedRef.current = true;
    ref.current?.focus({ preventScroll: true });
  }, [pinned, shown]);
  // Escape, or a press anywhere but the card and its highlight, closes it.
  useEffect(() => {
    const key = (e) => { if (e.key === "Escape") review.close(); };
    const away = (e) => {
      if (ref.current?.contains(e.target) || e.target?.closest?.(`[data-hl-id="${CSS.escape(h.id)}"]`)) return;
      review.close();
    };
    window.addEventListener("keydown", key);
    document.addEventListener("pointerdown", away, true);
    return () => {
      window.removeEventListener("keydown", key);
      document.removeEventListener("pointerdown", away, true);
    };
  }, [h.id, review]);

  const percent = roughMatchPercent(h.position);
  const title = percent != null ? t("Matched roughly: {n}%", { n: percent }) : t("Placed by estimate");
  const prev = reviewPrev(h.position);
  const area = !!h.position.area;
  const diff = prev && !area ? quoteDiff(prev.quote, h.content?.text || "", { clipped: prev.clipped }) : null;
  const color = normalizeColor(h.color) || DEFAULT_HIGHLIGHT;
  return (
    <div ref={ref} className="hlReviewCard" role="dialog" aria-label={title} data-review-id={h.id} tabIndex={-1}
      style={{ left: pos?.left ?? 0, top: pos?.top ?? 0, visibility: pos && !pos.hidden ? undefined : "hidden" }}
      onMouseEnter={review.hold} onMouseLeave={review.leave}>
      <div className="hlReviewHead">{title}</div>
      {prev ? <Before prev={prev} area={area} color={color} darkPage={darkPage}
        pageAspect={(h.position.height || 0) / (h.position.width || 1) || PAGE_ASPECT} /> : null}
      {diff ? (
        <div className="hlReviewDiff">
          {diff.map((p, i) => (p.tag === "del" ? <del key={i}>{p.text}</del>
            : p.tag === "add" ? <ins key={i}>{p.text}</ins> : <span key={i}>{p.text}</span>))}
        </div>
      ) : null}
      {onAccept ? (
        <div className="hlReviewActions">
          <button type="button" className="uiBtn sm primary" onClick={() => onAccept(h.id)}
            title={t("Keep the highlight where it is now and draw it solid")}>
            <CheckIcon size={14} /> {t("Accept")}
          </button>
          <button type="button" className="uiBtn sm" onClick={() => { review.close(); onMove?.(h.id); }}
            title={t("Move to a selection…")}>
            <LocateIcon size={14} /> {t("Move…")}
          </button>
          <span className="hlReviewCount">{t("{i} of {n}", { i: place.index + 1, n: place.total })}</span>
          <button type="button" className="uiBtn sm" disabled={!place.next} onClick={() => place.next && onNext?.(place.next.id)}
            title={t("The next highlight to check on this page")}>
            {t("Next")} <ChevronRightIcon size={14} />
          </button>
        </div>
      ) : null}
    </div>
  );
}

// Where it was in the previous file: the picture of that page around its
// old rectangles, drawn over it in its colour, a placeholder of the
// picture's shape while it loads; the old quote when the file is no longer
// stored, or in a share view, which asks for no picture (beforeCrop). A
// flipped page's picture is flipped too, as the viewer draws it.
function Before({ prev, area, color, darkPage, pageAspect }) {
  const shared = !!getShareToken();
  const crop = beforeCrop(prev, { area, pageAspect, shared });
  const box = crop?.box || null;
  const src = crop ? withWorkspace(`${API}${crop.path}`) : "";
  const [shot, setShot] = useState({ src: "", state: "loading", ratio: 0 });
  const state = shot.src === src ? shot.state : "loading";
  const ratio = shot.src === src && shot.ratio ? shot.ratio : cropAspect(box, pageAspect);
  const rects = rectsInCrop(prev.rects, box);
  const gone = !src || state === "failed";
  return (
    <div className="hlReviewBefore">
      <div className="hlReviewLabel">{t("Before")}</div>
      {gone ? (
        <>
          <div className="hlReviewGone">{shared ? t("The previous version is not shown through a share link.")
            : t("The previous version of this PDF is no longer stored.")}</div>
          {prev.quote ? <div className="hlReviewQuote">{prev.clipped ? `${prev.quote}…` : prev.quote}</div> : null}
        </>
      ) : (
        <div className={`hlReviewShot${darkPage ? " pdfDark" : ""}${state === "loaded" ? " loaded" : ""}`}
          style={{ aspectRatio: String(ratio), width: `min(100%, ${Math.round(SHOT_MAX_H * ratio)}px)` }}>
          <img src={src} alt={t("The highlight in the previous version of the PDF")} draggable={false}
            onLoad={(e) => setShot({ src, state: "loaded", ratio: e.currentTarget.naturalWidth / e.currentTarget.naturalHeight || 0 })}
            onError={() => setShot({ src, state: "failed", ratio: 0 })} />
          {state === "loaded" ? rects.map((r, i) => (
            <span key={i} className="hlReviewRect" style={{
              left: `${r.left * 100}%`, top: `${r.top * 100}%`, width: `${r.width * 100}%`, height: `${r.height * 100}%`,
              "--hl": color, "--hl-dark": darkPageColor(color),
            }} />
          )) : null}
        </div>
      )}
    </div>
  );
}
