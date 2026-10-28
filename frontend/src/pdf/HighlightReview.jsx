// The review card of a highlight after its PDF was replaced
// (docs/dev/highlight_relocation.md). One the run drew dashed (a rough
// match, or an area placed by estimate) is pending until the person
// accepts or moves it; one it drew hollow (lost: its text was not found)
// waits for a move, or for its closest match to be applied. Both are one
// queue on the page, in document order (highlightReview.js reviewQueue).
// Hovering either on the page opens, after a moment, a card anchored to
// it. A pending one's: how alike the texts were, a picture of where it was
// in the previous file with its old rectangles drawn over it, its old
// quote against its current one word by word, and for an editor Accept,
// Move… and Next ("{i} of {n}" to review on the page). A lost one's: where
// it was (the picture of the file its rectangles were measured on, else
// its wording), and when the run found a place below the threshold, how
// alike that read, the two texts compared, Show (the place outlined on the
// page) and Use closest match. A step taken on the card (Accept, Use
// closest match, a move started from it) leaves it open on the highlight,
// resolved, saying what it did, with Undo and Next, or Close once nothing
// is left; hovering another highlight leaves it. The pointer may cross
// into the card; leaving both closes a hovered one, as the note badge's
// tip does (PdfViewer.jsx NoteBadge). Touch has no hover, so a tap on such
// a highlight opens it instead of the tap's usual action (a second tap
// does that action), and a tap outside closes it; so does Escape. One
// opened by a tap, by Next, from the Document menu or by a step stays
// until then. A lost one from a page the PDF no longer has, which nothing
// draws, is reached by Next and the Document menu, its card standing in
// the viewer and saying so.
//
// Only dashed and hollow highlights' rectangles listen for the pointer
// (PdfPage), so a page of many highlights costs nothing more. The
// arithmetic is highlightReview.js's.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { t } from "../shared/i18n/i18n.js";
import { API, getShareToken, withWorkspace } from "../shared/lib/utils";
import { lastPointer } from "../shared/lib/pointer.js";
import { DEFAULT_HIGHLIGHT, darkPageColor, normalizeColor } from "../shared/model/palette.js";
import { CheckIcon, ChevronRightIcon, EyeIcon, LocateIcon, UndoIcon } from "../shared/ui/Icons";
import { placeFloating, surfaceSize, useFloatingPosition } from "../shared/ui/placement.js";
import { PAGE_ASPECT, beforeCrop, cropAspect, pastLastPage, rectsInCrop, reviewCardState, reviewPlace, reviewQueue, reviewStep } from "./highlightReview.js";

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

// Which card is open: {id, pinned, done, ghost} or null (pinned: opened by
// a tap, by Next, from the Document menu or by a step, so leaving it does
// not close it; done: the step the card took on it, reviewStep's, for what
// it says and its Undo; ghost: its closest match outlined on the page), and
// the handlers PdfPage's rectangles and the card call; `drawn` counts the
// times PdfPage drew that outline, so the card anchors to it once its page
// renders. The handlers are stable, so PdfPage's memo holds.
export function useHighlightReview() {
  const [card, setCard] = useState(null);
  const [drawn, setDrawn] = useState(0);
  const cardRef = useRef(null);
  cardRef.current = card;
  const timer = useRef(0);
  const review = useMemo(() => {
    const later = (ms, fn) => { clearTimeout(timer.current); timer.current = setTimeout(fn, ms); };
    const hideHovered = () => setCard((now) => (now && !now.pinned ? null : now));
    return {
      // The pointer over (or off) a dashed or hollow highlight's rectangles.
      // A card that took a step (`done`) stays on its highlight, with its
      // Undo and its place in the walk: hovering another leaves it.
      hover(id, over) {
        if (lastPointer() !== "mouse") return; // a tap fires compatibility mouse events too
        const now = cardRef.current;
        if (over) {
          if (now?.id === id) clearTimeout(timer.current);
          else if (!now?.done) later(SHOW_MS, () => setCard((cur) => (cur?.done ? cur : { id, pinned: false, done: null })));
        } else if (now && !now.pinned) later(HIDE_MS, hideHovered);
        else clearTimeout(timer.current); // the card on its way for it is not wanted any more
      },
      // A tap on one: true when it opened the card, which the tap then does
      // instead of its usual action.
      tap(id) {
        if (lastPointer() === "mouse") return false;
        clearTimeout(timer.current);
        if (cardRef.current?.id === id) { setCard(null); return false; }
        setCard({ id, pinned: true, done: null });
        return true;
      },
      // Opened by Next, the Document menu, or after a step (`done`).
      open(id, done = null) { clearTimeout(timer.current); setCard({ id, pinned: true, done }); },
      // Show on a lost one's closest match: outlined on the page while the card is open.
      ghost(id) { setCard((now) => (now?.id === id ? { ...now, pinned: true, ghost: true } : now)); },
      // PdfPage drew that outline (its page rendered after Show scrolled there).
      drawn() { setDrawn((n) => n + 1); },
      close() { clearTimeout(timer.current); setCard(null); },
      // The pointer entering the card keeps it; leaving it closes a hovered one.
      hold() { clearTimeout(timer.current); },
      leave() { if (cardRef.current && !cardRef.current.pinned) later(HIDE_MS, hideHovered); },
    };
  }, []);
  useEffect(() => () => clearTimeout(timer.current), []);
  return { card, review, drawn };
}

// The card, for the open one while there is something to show
// (reviewCardState): it goes when its highlight is resolved elsewhere (its
// menu's Accept, a move not started from it, undone back to solid),
// deleted, or no longer drawn. `fileId` is the stored file the page shows,
// `pageCount` its number of pages (0 while not known): a highlight past
// the last one is not drawn, and its card stands in the viewer. `drawn`
// (useHighlightReview's) changes when the closest match's outline is
// drawn. `onAccept(id)`, `onPlace(id)` (Use closest match), `onMove(id,
// step)` (the card's step, when it took one) and `onUndo(id, step)` are an
// editor's (absent, the card has no actions); `onOpen(id, {position,
// done})` scrolls to that highlight (or that place) and opens its card;
// `onShow(id)` scrolls to its closest match and outlines it.
export function HighlightReview({ card, review, drawn = 0, highlights, viewerRef, darkPage = false, fileId = "", pageCount = 0, onAccept, onPlace, onMove, onUndo, onOpen, onShow }) {
  const queue = useMemo(() => reviewQueue(highlights), [highlights]);
  const h = card ? (highlights || []).find((x) => x.id === card.id) || null : null;
  const done = card?.done || null;
  const state = useMemo(() => (h ? reviewCardState(h, { done, fileId }) : null), [h, done, fileId]);
  useEffect(() => { if (card && !state) review.close(); }, [card, state, review]);
  if (!card || !state) return null;
  const resolved = state.kind === "resolved" ? done : null;
  return createPortal(
    <ReviewCard key={h.id} h={h} state={state} done={resolved} pinned={card.pinned}
      ghost={!!card.ghost && !!state.closest} drawn={drawn} offPage={pastLastPage(h.position, pageCount)}
      place={reviewPlace(queue, h, resolved)} review={review} viewerRef={viewerRef}
      darkPage={darkPage} onAccept={onAccept} onPlace={onPlace} onMove={onMove} onUndo={onUndo} onOpen={onOpen} onShow={onShow} />,
    document.body,
  );
}

// The highlight's rectangles on screen, as one box, or null while they are
// not drawn (their page not rendered yet). With `ghost`, its closest
// match's outline (PdfPage) while that is drawn: Show scrolled to it.
function anchorRect(viewer, id, ghost) {
  const sel = (attr) => `[data-page] > div[${attr}="${CSS.escape(id)}"]`;
  let els = ghost ? viewer?.querySelectorAll(sel("data-review-ghost")) : null;
  if (!els?.length) els = viewer?.querySelectorAll(sel("data-hl-id")) || [];
  let box = null;
  for (const el of els) {
    const r = el.getBoundingClientRect();
    box = box ? { left: Math.min(box.left, r.left), top: Math.min(box.top, r.top), right: Math.max(box.right, r.right), bottom: Math.max(box.bottom, r.bottom) }
      : { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
  }
  return box;
}

// Where the card of a highlight past the PDF's last page stands, nothing
// drawing it: in the viewer, centred near its foot.
function inViewer(size, v) {
  return placeFloating(size, { x: v.left + Math.max(0, (v.width - size.width) / 2), y: v.bottom - 2 * GAP, anchorBottom: true,
    bounds: { top: v.top, bottom: v.bottom } });
}

function cardTitle(state) {
  if (state.kind === "resolved") return state.step === "placed" ? t("Placed") : state.step === "moved" ? t("Moved") : t("Accepted");
  if (state.kind === "lost") return t("Not found in this version");
  return state.percent != null ? t("Matched roughly: {n}%", { n: state.percent }) : t("Placed by estimate");
}

function ReviewCard({ h, state, done, pinned, ghost, drawn, offPage, place, review, viewerRef, darkPage, onAccept, onPlace, onMove, onUndo, onOpen, onShow }) {
  const ref = useRef(null);
  const [tick, setTick] = useState(0);
  const focusedRef = useRef(false);
  // Under the highlight, else over it; kept inside the window, following
  // the page as it scrolls. Hidden while the highlight is out of the
  // viewer's sight, and while it is not drawn yet (`missing`). Placed
  // again when the closest match's outline is drawn (`drawn`), its page
  // rendered after Show scrolled there. One past the PDF's last page,
  // which nothing draws, stands in the viewer (under its outline while
  // Show's is in sight).
  const pos = useFloatingPosition(ref, (el) => {
    const viewer = viewerRef.current;
    const a = anchorRect(viewer, h.id, ghost);
    const v = viewer?.getBoundingClientRect();
    const seen = !!a && !!v && a.bottom > v.top && a.top < v.bottom && a.right > v.left && a.left < v.right;
    if (offPage && v && !seen) return { ...inViewer(surfaceSize(el), v), hidden: false, missing: false };
    if (!a) return { left: 0, top: 0, hidden: true, missing: true };
    const spot = placeFloating(surfaceSize(el), { x: a.left, y: a.bottom + GAP, flipY: a.top - GAP });
    return { ...spot, hidden: !seen, missing: false };
  }, [h.id, h.position, ghost, drawn, offPage, tick], { follow: true });
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
  // where it is. A step, or its Undo, takes it back too: the button pressed
  // is gone with the card's other state.
  const shown = !!pos && !pos.hidden;
  useEffect(() => {
    if (!pinned || !shown || focusedRef.current) return;
    focusedRef.current = true;
    ref.current?.focus({ preventScroll: true });
  }, [pinned, shown]);
  const doneRef = useRef(done);
  useEffect(() => {
    if (doneRef.current === done) return;
    doneRef.current = done;
    if (ref.current && !ref.current.contains(document.activeElement)) ref.current.focus({ preventScroll: true });
  }, [done]);
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

  const title = cardTitle(state);
  const color = normalizeColor(h.color) || DEFAULT_HIGHLIGHT;
  const pageAspect = (h.position.height || 0) / (h.position.width || 1) || PAGE_ASPECT;
  const quote = h.content?.text || "";
  // The steps, each one tree edit (App), the card staying open on it, resolved.
  const accept = () => {
    const step = reviewStep("accepted", { position: h.position });
    onAccept(h.id);
    if (step) review.open(h.id, step);
  };
  // (placed on its closest match, it is shown there, where the card follows it)
  const placeClosest = () => {
    const step = reviewStep("placed", { position: h.position, quote });
    if (!step) return;
    onPlace(h.id);
    if (onOpen) onOpen(h.id, { position: step.after, done: step });
    else review.open(h.id, step);
  };
  // Undo takes the step back (one more tree edit); a step that moved it
  // scrolls back to where it was. A move started from a card already
  // resolved goes back to that card's step (reviewStep's `prior`), whose
  // own Undo the card offers again.
  const undo = () => {
    onUndo(h.id, done);
    if (done.kind === "accepted") review.open(h.id);
    else onOpen?.(h.id, { position: done.before.position, done: done.prior || null });
  };
  // (a resolved card hands its step on, for the move's Undo to come back to)
  const move = () => { review.close(); onMove?.(h.id, done); };
  const next = () => { if (place.next) onOpen?.(place.next.id); };
  const moveBtn = (
    <button type="button" className="uiBtn sm" onClick={move} title={t("Move to a selection…")}>
      <LocateIcon size={14} /> {t("Move…")}
    </button>
  );
  const nextBtn = (primary) => (
    <button type="button" className={`uiBtn sm${primary ? " primary" : ""}`} disabled={!place.next} onClick={next}
      title={t("The next highlight to check on this page")}>
      {t("Next")} <ChevronRightIcon size={14} />
    </button>
  );
  const count = <span className="hlReviewCount">{t("{i} of {n}", { i: place.index + 1, n: place.total })}</span>;
  return (
    <div ref={ref} className="hlReviewCard" role="dialog" aria-label={title} data-review-id={h.id} data-review-state={state.kind}
      tabIndex={-1} style={{ left: pos?.left ?? 0, top: pos?.top ?? 0, visibility: pos && !pos.hidden ? undefined : "hidden" }}
      onMouseEnter={review.hold} onMouseLeave={review.leave}>
      <div className="hlReviewHead">{title}</div>
      {offPage ? <div className="hlReviewOffPage">{t("This version of the PDF has no page {n}.", { n: h.position.pageNumber })}</div> : null}
      {state.before ? <Before prev={state.before} area={state.area} color={color} darkPage={darkPage} pageAspect={pageAspect}
        quoteToo={state.kind === "lost" && !state.closest} /> : null}
      {state.kind === "pending" && state.diff ? <Diff parts={state.diff} /> : null}
      {state.closest ? (
        <div className="hlReviewClosest">
          <div className="hlReviewClosestHead">
            <span className="hlReviewLabel">{t("Closest match: {n}%", { n: state.closest.percent })}</span>
            <button type="button" className={`uiBtn sm ghost${ghost ? " on" : ""}`} aria-pressed={ghost}
              onClick={() => onShow?.(h.id)} title={t("Scroll to the closest match and outline it on the page")}>
              <EyeIcon size={14} /> {t("Show")}
            </button>
          </div>
          {state.diff ? <Diff parts={state.diff} /> : null}
        </div>
      ) : null}
      {state.kind === "resolved" && !place.next ? <div className="hlReviewDone">{t("Every highlight on this page is reviewed.")}</div> : null}
      {onAccept && state.kind === "pending" ? (
        <div className="hlReviewActions">
          <button type="button" className="uiBtn sm primary" onClick={accept}
            title={t("Keep the highlight where it is now and draw it solid")}>
            <CheckIcon size={14} /> {t("Accept")}
          </button>
          {moveBtn}{count}{nextBtn(false)}
        </div>
      ) : null}
      {onAccept && state.kind === "lost" ? (
        <div className="hlReviewActions">
          {state.closest && onPlace ? (
            <button type="button" className="uiBtn sm primary" onClick={placeClosest}
              title={t("Place the highlight on the closest match and take its text")}>
              <CheckIcon size={14} /> {t("Use closest match")}
            </button>
          ) : null}
          {moveBtn}{count}{nextBtn(false)}
        </div>
      ) : null}
      {state.kind === "resolved" ? (
        <div className="hlReviewActions">
          {onUndo && done ? (
            <button type="button" className="uiBtn sm" onClick={undo} title={t("Put the highlight back as it was before")}>
              <UndoIcon size={14} /> {t("Undo")}
            </button>
          ) : null}
          {onMove ? moveBtn : null}
          <span className="hlReviewSpacer" />
          {place.next ? nextBtn(true) : (
            <button type="button" className="uiBtn sm primary" onClick={review.close}>{t("Close")}</button>
          )}
        </div>
      ) : null}
    </div>
  );
}

// A word comparison (quoteDiff's parts): the words gone struck through,
// the words added marked.
function Diff({ parts }) {
  return (
    <div className="hlReviewDiff">
      {parts.map((p, i) => (p.tag === "del" ? <del key={i}>{p.text}</del>
        : p.tag === "add" ? <ins key={i}>{p.text}</ins> : <span key={i}>{p.text}</span>))}
    </div>
  );
}

// Where it was in the previous file: the picture of that page around its
// old rectangles, drawn over it in its colour, a placeholder of the
// picture's shape while it loads; the old quote when the file is no longer
// stored, or in a share view, which asks for no picture (beforeCrop). A
// flipped page's picture is flipped too, as the viewer draws it. A lost
// highlight measured on the page's current file (`prev.current`) has no
// other file to show: its wording alone. `quoteToo`: the quote under the
// picture as well (a lost one with nothing to compare it with).
function Before({ prev, area, color, darkPage, pageAspect, quoteToo = false }) {
  const shared = !!getShareToken();
  const crop = beforeCrop(prev, { area, pageAspect, shared });
  const box = crop?.box || null;
  const src = crop ? withWorkspace(`${API}${crop.path}`) : "";
  const [shot, setShot] = useState({ src: "", state: "loading", ratio: 0 });
  const state = shot.src === src ? shot.state : "loading";
  const ratio = shot.src === src && shot.ratio ? shot.ratio : cropAspect(box, pageAspect);
  const rects = rectsInCrop(prev.rects, box);
  const gone = !src || state === "failed";
  const quote = prev.quote ? <div className="hlReviewQuote">{prev.clipped ? `${prev.quote}…` : prev.quote}</div> : null;
  return (
    <div className="hlReviewBefore">
      <div className="hlReviewLabel">{t("Before")}</div>
      {gone ? (
        <>
          {prev.current && !src ? null : (
            <div className="hlReviewGone">{shared ? t("The previous version is not shown through a share link.")
              : t("The previous version of this PDF is no longer stored.")}</div>
          )}
          {quote}
        </>
      ) : (
        <>
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
          {quoteToo ? quote : null}
        </>
      )}
    </div>
  );
}
