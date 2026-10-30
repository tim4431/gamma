// The review card of a highlight after its PDF was replaced
// (docs/dev/highlight_relocation.md). One the run drew dashed (a rough
// match, or an area placed by estimate) is pending until the person
// accepts or moves it; one it drew hollow (lost: its text was not found)
// waits for a move, or for its closest match to be applied. Hovering
// either on the page opens, after a moment, a card anchored to it. A
// pending one's: how alike the texts were, a picture of where it was in
// the previous file with its old rectangles drawn over it, its old quote
// against its current one word by word, and for an editor Accept and
// Move…. A lost one's: where it was (the picture of the file its
// rectangles were measured on, else its wording), and when the run found a
// place below the threshold, how alike that read, the two texts compared,
// Show (the place outlined on the page) and Use closest match, and
// Discard, which takes it off the page (deleted when it carries nothing of
// its own, else kept as a note; highlightReview.js discardKind). Then
// Previous, "{i} of {n}" and Next, over the page's highlights to review
// and those resolved on a card (highlightReview.js reviewList).
//
// A step taken on the card (Accept, Use closest match, a move started from
// it, Discard) leaves the card as it was: only its button changes, in
// place, saying the step is done (Accepted, Placed, Moved, Discarded),
// pressed; pressing it again undoes the step. A button whose action no
// longer applies stays in its place, disabled (reviewActions). A step is
// in effect while the highlight is as it left it (stepInEffect): the
// app's own undo, someone else's change or a move by hand shows the card
// as the highlight is now. A discarded one's card stays where the
// highlight was. A card that took a step stays while the pointer rests on
// another highlight. The pointer may cross into the card; leaving both
// closes a hovered one, as the note badge's tip does (PdfViewer.jsx
// NoteBadge). Touch has no hover, so a tap on such a highlight opens it
// instead of the tap's usual action (a second tap does that action), and a
// tap outside closes it; so does Escape. One opened by a tap, by Previous
// or Next, from the Document menu or by a step stays until then. The card
// follows its highlight as the page scrolls, and stands hidden, all of it,
// while the highlight is out of the viewer's sight. A lost one from a page
// the PDF no longer has, which nothing draws, is reached by Previous, Next
// and the Document menu, its card standing in the viewer and saying so.
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
import { CheckIcon, ChevronLeftIcon, ChevronRightIcon, EyeIcon, LocateIcon, XIcon } from "../shared/ui/Icons";
import { placeFloating, surfaceSize, useFloatingPosition } from "../shared/ui/placement.js";
import { PAGE_ASPECT, beforeCrop, cropAspect, entryPosition, pastLastPage, placeBox, rectsInCrop, reviewActions, reviewCardState, reviewList, reviewPlace, reviewStep } from "./highlightReview.js";

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

const NO_STEPS = new Map();

// Which card is open: {id, pinned, walked, ghost} or null (pinned: opened
// by a tap, by Previous or Next, from the Document menu or by a step, so
// leaving it does not close it; walked: which of Previous and Next opened
// it; ghost: its closest match outlined on the page); the steps taken on
// cards while the viewer shows this page and file (`steps`: Map id →
// {step, h, discard}, the step last taken on that highlight's card, the
// highlight as the card had it, and what its Discard does, discardKind's),
// which keep it in the card's walk (reviewList) and its card as it was;
// and the handlers PdfPage's rectangles and the card call. `drawn` counts
// the times PdfPage drew that outline, so the card anchors to it once its
// page renders. The handlers are stable, so PdfPage's memo holds.
export function useHighlightReview() {
  const [card, setCard] = useState(null);
  const [steps, setSteps] = useState(NO_STEPS);
  const [drawn, setDrawn] = useState(0);
  const cardRef = useRef(null);
  cardRef.current = card;
  const stepsRef = useRef(steps);
  stepsRef.current = steps;
  const timer = useRef(0);
  const review = useMemo(() => {
    const later = (ms, fn) => { clearTimeout(timer.current); timer.current = setTimeout(fn, ms); };
    const hideHovered = () => setCard((now) => (now && !now.pinned ? null : now));
    // a card on a highlight that took a step stays while another is hovered
    const held = (now) => !!now && stepsRef.current.has(now.id);
    return {
      // The pointer over (or off) a dashed or hollow highlight's rectangles.
      hover(id, over) {
        if (lastPointer() !== "mouse") return; // a tap fires compatibility mouse events too
        const now = cardRef.current;
        if (over) {
          if (now?.id === id) clearTimeout(timer.current);
          else if (!held(now)) later(SHOW_MS, () => setCard((cur) => (held(cur) ? cur : { id, pinned: false })));
        } else if (now && !now.pinned) later(HIDE_MS, hideHovered);
        else clearTimeout(timer.current); // the card on its way for it is not wanted any more
      },
      // A tap on one: true when it opened the card, which the tap then does
      // instead of its usual action.
      tap(id) {
        if (lastPointer() === "mouse") return false;
        clearTimeout(timer.current);
        if (cardRef.current?.id === id) { setCard(null); return false; }
        setCard({ id, pinned: true });
        return true;
      },
      // Opened by Previous or Next (`walked`: "prev" or "next", the button
      // the card it opens focuses), the Document menu, or after a step (on
      // the same highlight: Show's outline stays as it was).
      open(id, { walked = "" } = {}) {
        clearTimeout(timer.current);
        setCard((now) => (now?.id === id ? { ...now, pinned: true } : { id, pinned: true, walked }));
      },
      // A step taken on highlight `id`'s card (reviewStep's, discardStep's),
      // `h` the highlight before it, `discard` what its Discard would do
      // (kept: the card shows Discard the same after the step).
      record(id, step, h, discard = null) {
        setSteps((m) => new Map(m).set(id, { step, h: h || m.get(id)?.h || null, discard: discard || m.get(id)?.discard || null }));
      },
      // `step` undone: the card is back on the one before it (a move's
      // prior), else on the highlight as it was.
      undone(id, step) {
        setSteps((m) => {
          const next = new Map(m);
          if (step?.prior) next.set(id, { ...m.get(id), step: step.prior });
          else next.delete(id);
          return next;
        });
      },
      // Another page or file: a walk afresh.
      reset() { clearTimeout(timer.current); setCard(null); setSteps(NO_STEPS); },
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
  return { card, steps, review, drawn };
}

// The card, for the open one while there is something to show
// (reviewCardState, over its entry in the walk): it goes when its
// highlight is resolved elsewhere (its menu's Accept, a move not started
// from it, undone back to solid), deleted by others, or no longer drawn.
// `steps` is useHighlightReview's, `positionOf(id)` the block's stored
// pdf_position (stepInEffect). `fileId` is the stored file the page shows,
// `pageCount` its number of pages (0 while not known): a highlight past
// the last one is not drawn, and its card stands in the viewer. `drawn`
// (useHighlightReview's) changes when the closest match's outline is
// drawn. `onAccept(id)`, `onPlace(id)` (Use closest match), `onMove(id,
// step)` (the card's step in effect, when there is one), `onDiscard(id)`
// (Discard on a lost one: the step it took, discardStep's, or null),
// `discardOf(id)` (which of the two Discard would do, discardKind's) and
// `onUndo(id, step)` are an editor's (absent, the card has no actions);
// `onOpen(id, {position})` scrolls to that highlight (or that place) and
// opens its card; `onShow(id)` scrolls to its closest match and outlines
// it.
export function HighlightReview({ card, steps = NO_STEPS, review, drawn = 0, highlights, positionOf = null, viewerRef, darkPage = false, fileId = "", pageCount = 0, onAccept, onPlace, onMove, onDiscard, discardOf, onUndo, onOpen, onShow }) {
  const list = useMemo(() => reviewList(highlights, steps, positionOf), [highlights, steps, positionOf]);
  const entry = card ? list.find((e) => e.id === card.id) || null : null;
  const h = entry?.h || null;
  const step = entry?.step || null;
  const state = useMemo(() => (h ? reviewCardState(h, { done: step, fileId }) : null), [h, step, fileId]);
  useEffect(() => { if (card && !state) review.close(); }, [card, state, review]);
  if (!card || !state) return null;
  // a discarded one, drawn no more, stays where it stood
  const drawnNow = (highlights || []).some((x) => x.id === h.id);
  const stood = !drawnNow && step?.kind === "discarded" ? step.before.position : null;
  // what Discard does: as the card found it before its step, which may have changed it
  const discard = state.kind !== "lost" || !onDiscard ? null
    : step ? steps.get(h.id)?.discard || (step.kind === "discarded" ? (step.deleted ? "delete" : "unplace") : null)
      : discardOf?.(h.id) || null;
  return createPortal(
    <ReviewCard key={h.id} h={h} state={state} step={step} pinned={card.pinned} walked={card.walked || ""}
      ghost={!!card.ghost && !!state.closest && !step} drawn={drawn}
      offPage={pastLastPage(state.position, pageCount)} undrawn={pastLastPage(stood || h.position, pageCount)}
      stood={stood} place={reviewPlace(list, h.id)} review={review} viewerRef={viewerRef}
      darkPage={darkPage} actions={onAccept ? reviewActions(state, { place: !!onPlace, discard }) : []}
      onAccept={onAccept} onPlace={onPlace} onMove={onMove}
      discard={discard} onDiscard={onDiscard} onUndo={onUndo} onOpen={onOpen} onShow={onShow} />,
    document.body,
  );
}

// The highlight's rectangles on screen, as one box, or null while they are
// not drawn (their page not rendered yet). With `ghost`, its closest
// match's outline (PdfPage) while that is drawn: Show scrolled to it.
// `stood`: the position of one no longer drawn (discarded), whose box is
// worked out on its page (placeBox).
function anchorRect(viewer, id, ghost, stood = null) {
  if (stood) {
    const page = viewer?.querySelector(`.pdfPageWrap[data-page="${stood.pageNumber}"]`);
    return page ? placeBox(page.getBoundingClientRect(), stood) : null;
  }
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
  if (state.kind === "lost") return t("Not found in this version");
  return state.percent != null ? t("Matched roughly: {n}%", { n: state.percent }) : t("Placed by estimate");
}

// A step button's faces: its label (and the icon of a secondary step)
// before the step, and after it the check and what was done, with the
// titles of both. The check marks a step done, so Accept and Use closest
// match show none before. `discard` is what Discard does (discardKind's),
// for its title.
function stepFaces(key, discard) {
  switch (key) {
    case "accept": return {
      icon: null, label: t("Accept"), title: t("Keep the highlight where it is now and draw it solid"),
      done: t("Accepted"), undo: t("Accepted: press again to undo"),
    };
    case "place": return {
      icon: null, label: t("Use closest match"), title: t("Place the highlight on the closest match and take its text"),
      done: t("Placed"), undo: t("Placed on the closest match: press again to undo"),
    };
    case "move": return {
      icon: LocateIcon, label: t("Move…"), title: t("Move to a selection…"),
      done: t("Moved"), undo: t("Moved: press again to undo"),
    };
    default: return {
      icon: XIcon, label: t("Discard"),
      title: discard === "delete" ? t("Delete the highlight: it holds no note or comment, and its text is not in this version")
        : t("Take the highlight off the PDF and keep it as a note, with its quote, note and comments"),
      done: t("Discarded"), undo: t("Discarded: press again to undo"),
    };
  }
}

// One of the card's step buttons (reviewActions' `a`). It keeps its size
// whether its step is taken or not: both faces share one cell, the one not
// shown hidden (app.css .hlReviewStep), so nothing on the card moves. A
// `compact` one shows its icon alone, its label its name and title (a
// lost card's Move… and Discard beside Use closest match, so the row and
// Previous, the count and Next fit on one line).
function StepButton({ a, primary, compact, discard, onPress }) {
  const f = stepFaces(a.key, discard);
  const Icon = f.icon;
  const face = (on) => (
    <span className={on === a.pressed ? "hlReviewFace" : "hlReviewFace hlReviewRoom"} aria-hidden={on === a.pressed ? undefined : true}>
      {on ? <CheckIcon size={14} /> : Icon ? <Icon size={14} /> : null}{compact ? null : <span>{on ? f.done : f.label}</span>}
    </span>
  );
  return (
    <button type="button" data-step={a.key} aria-pressed={a.pressed} disabled={a.disabled}
      className={`uiBtn sm hlReviewStep${compact ? " iconSq" : ""}${a.pressed ? " on" : primary ? " primary" : ""}`}
      aria-label={compact ? (a.pressed ? f.done : f.label) : undefined} title={a.pressed ? f.undo : f.title}
      onClick={onPress}>
      {face(false)}{face(true)}
    </button>
  );
}

function ReviewCard({ h, state, step, pinned, walked, ghost, drawn, offPage, undrawn, stood, place, review, viewerRef, darkPage, actions, onAccept, onPlace, onMove, discard, onDiscard, onUndo, onOpen, onShow }) {
  const ref = useRef(null);
  const [tick, setTick] = useState(0);
  const focusedRef = useRef(false);
  // Under the highlight, else over it; kept inside the window, following
  // the page as it scrolls (and the highlight, when a step took it
  // elsewhere). Hidden while the highlight is out of the viewer's sight,
  // and while it is not drawn yet (`missing`): the whole card, nothing in
  // it seen on its own (app.css .hlReviewShot). Placed again when the
  // closest match's outline is drawn (`drawn`), its page rendered after
  // Show scrolled there. One past the PDF's last page (`undrawn`), which
  // nothing draws, stands in the viewer (under its outline while Show's is
  // in sight). A discarded one's stays where the highlight was (`stood`).
  const pos = useFloatingPosition(ref, (el) => {
    const viewer = viewerRef.current;
    const a = anchorRect(viewer, h.id, ghost, stood);
    const v = viewer?.getBoundingClientRect();
    const seen = !!a && !!v && a.bottom > v.top && a.top < v.bottom && a.right > v.left && a.left < v.right;
    if (undrawn && v && !seen) return { ...inViewer(surfaceSize(el), v), hidden: false, missing: false };
    if (!a) return { left: 0, top: 0, hidden: true, missing: true };
    const spot = placeFloating(surfaceSize(el), { x: a.left, y: a.bottom + GAP, flipY: a.top - GAP });
    return { ...spot, hidden: !seen, missing: false };
  }, [h.id, h.position, ghost, drawn, undrawn, stood, tick], { follow: true });
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
  // One opened by a tap, by Previous or Next or from the Document menu
  // takes the focus once it is seen, so the keys reach its buttons (Tab);
  // reached by Previous or Next, that button of its own, so Enter walks on.
  // A hovered one leaves the focus where it is. A step keeps it on its
  // button, which stays (a hovered card that took one is pinned with the
  // focus in it already), through a scroll the step makes too (below).
  const shown = !!pos && !pos.hidden;
  useEffect(() => {
    if (!pinned || !shown || focusedRef.current) return;
    focusedRef.current = true;
    if (ref.current?.contains(document.activeElement)) return;
    const nav = walked ? ref.current?.querySelector(`[data-review-nav="${walked}"]:not(:disabled)`) : null;
    (nav || ref.current)?.focus({ preventScroll: true });
  }, [pinned, shown, walked]);
  // The browser takes the focus from what it hides, so a card hidden a
  // moment (the viewer scrolling to where Use closest match took its
  // highlight, or back on an undo; the highlight scrolled out of sight)
  // drops it to the page. `lastFocus` is the control in the card the focus
  // was last on, forgotten once the person puts the focus somewhere else;
  // shown again with the focus still on nothing, the card puts it back
  // there (on the card itself when that control is disabled now), so
  // Enter presses the step's button again.
  const lastFocus = useRef(null);
  const focusIn = (e) => { lastFocus.current = e.target; };
  const focusOut = (e) => { if (e.relatedTarget && !ref.current?.contains(e.relatedTarget)) lastFocus.current = null; };
  useEffect(() => {
    const el = ref.current, b = lastFocus.current;
    if (!shown || !el || !b) return;
    const now = document.activeElement;
    if (now && now !== document.body) return;
    (b !== el && el.contains(b) && !b.disabled ? b : el).focus({ preventScroll: true });
  }, [shown]);
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
  const pageAspect = (state.position.height || 0) / (state.position.width || 1) || PAGE_ASPECT;
  const quote = h.content?.text || "";
  // The steps, each one tree edit (App), recorded for the walk and the
  // card's look; the card stays open on the highlight, as it was.
  const take = {
    accept() {
      const s = reviewStep("accepted", { position: h.position });
      if (!s) return;
      onAccept(h.id);
      review.record(h.id, s, h);
      review.open(h.id);
    },
    // (placed on its closest match, it is shown there, where the card follows it)
    place() {
      const s = reviewStep("placed", { position: h.position, quote });
      if (!s) return;
      onPlace(h.id);
      review.record(h.id, s, h, discard);
      if (onOpen) onOpen(h.id, { position: s.after });
      else review.open(h.id);
    },
    // (the card makes way for the selection; App opens it again after the
    // move, the step in effect handed on for the move's undo to come back to)
    move() { review.close(); onMove?.(h.id, step); },
    // (deleted, or kept as a note off the PDF: the card stays where it was)
    discard() {
      const s = onDiscard?.(h.id);
      if (!s) return;
      review.record(h.id, s, h, discard);
      review.open(h.id);
    },
  };
  // The pressed button undoes its step (one more tree edit); a step that
  // moved the highlight scrolls back to where it was. A move started from a
  // card already resolved goes back to that card's step (reviewStep's
  // `prior`), its button pressed again. A discarded one comes back where
  // the card stands.
  const undo = () => {
    onUndo(h.id, step);
    review.undone(h.id, step);
    if (step.kind === "accepted" || step.kind === "discarded" || !onOpen) review.open(h.id);
    else onOpen(h.id, { position: step.before.position });
  };
  const walk = (to, dir) => { if (to) onOpen?.(to.id, { position: entryPosition(to), walked: dir }); };
  const many = actions.length > 2;
  const count = t("{i} of {n}", { i: place.index + 1, n: place.total });
  const all = t("All reviewed");
  return (
    <div ref={ref} className="hlReviewCard" role="dialog" aria-label={title} data-review-id={h.id} data-review-state={state.kind}
      data-review-step={step?.kind || undefined}
      tabIndex={-1} style={{ left: pos?.left ?? 0, top: pos?.top ?? 0, visibility: pos && !pos.hidden ? undefined : "hidden" }}
      onMouseEnter={review.hold} onMouseLeave={review.leave} onFocus={focusIn} onBlur={focusOut}>
      <div className="hlReviewHead">{title}</div>
      {offPage ? <div className="hlReviewOffPage">{t("This version of the PDF has no page {n}.", { n: state.position.pageNumber })}</div> : null}
      {state.before ? <Before prev={state.before} area={state.area} color={color} darkPage={darkPage} pageAspect={pageAspect}
        quoteToo={state.kind === "lost" && !state.closest} /> : null}
      {state.kind === "pending" && state.diff ? <Diff parts={state.diff} /> : null}
      {state.closest ? (
        <div className="hlReviewClosest">
          <div className="hlReviewClosestHead">
            <span className="hlReviewLabel">{t("Closest match: {n}%", { n: state.closest.percent })}</span>
            {/* outlining a lost one's closest match: not while a step has taken it off the review */}
            <button type="button" className={`uiBtn sm ghost${ghost ? " on" : ""}`} aria-pressed={ghost} disabled={!!step}
              onClick={() => onShow?.(h.id)} title={t("Scroll to the closest match and outline it on the page")}>
              <EyeIcon size={14} /> {t("Show")}
            </button>
          </div>
          {state.diff ? <Diff parts={state.diff} /> : null}
        </div>
      ) : null}
      {actions.length ? (
        <div className="hlReviewActions">
          {actions.map((a, i) => (
            <StepButton key={a.key} a={a} primary={i === 0 && a.key !== "move"} compact={many && i > 0} discard={discard}
              onPress={() => (a.pressed ? undo() : take[a.key]())} />
          ))}
          {/* Previous, the count and Next: the count keeps the room of both its texts, so a step that
              leaves nothing to check moves nothing */}
          <span className="hlReviewNav">
            <button type="button" className="ctlBtn" data-review-nav="prev" disabled={!place.prev} onClick={() => walk(place.prev, "prev")}
              aria-label={t("Previous highlight")} title={t("The previous highlight to review on this page")}>
              <ChevronLeftIcon size={16} />
            </button>
            <span className="hlReviewCountBox">
              <span className="hlReviewCount">{place.left ? count : all}</span>
              <span className="hlReviewCount hlReviewRoom" aria-hidden="true">{place.left ? all : count}</span>
            </span>
            <button type="button" className="ctlBtn" data-review-nav="next" disabled={!place.next} onClick={() => walk(place.next, "next")}
              aria-label={t("Next highlight")} title={t("The next highlight to review on this page")}>
              <ChevronRightIcon size={16} />
            </button>
          </span>
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
