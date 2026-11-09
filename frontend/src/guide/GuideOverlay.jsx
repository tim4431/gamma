// The guide's visible part: a ring around the current step's anchor and a
// card beside it. Nothing around the ring is dimmed or blocked, so the user
// acts on the real control and the app stays theirs. A step without an
// anchor is a centred card over a dimmed sheet. An anchor inside a popover
// or panel (its registry `surface`) keeps the card clear of that whole
// surface. A step's `scene` plays in its own layer over the real UI
// (guide/scene.js); a demo step moves the ring to whatever it acts on and
// shows a pointer gliding there. Anchors are found by data-guide id,
// retried briefly while the UI mounts; a step whose anchor never appears is
// skipped with a warning, never shown pointing at nothing. One that goes
// once found, or that a dialog or menu now covers, holds: its card waits in
// the corner, ringing nothing, until the anchor is back in sight. An offer
// (a triggered tour's invitation, or a hint) is the same card with a beacon
// in place of the ring. The card never takes focus or counts as a click
// outside the popover it points into.
//
// Layers, bottom to top, all in document order inside one root: a centred
// step's dimmed sheet, or the ring, the demo shield, a beacon, the scene,
// the demo pointer, the card. docs/dev/onboarding.md, "Motion and layers".
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { ANCHORS, anchorElement } from "./anchors.js";
import { keyText, resolveKey } from "./keys.js";
import { mediaRatio } from "./media.js";
import { CARD_W, placeCard } from "./place.js";
import { arriving } from "../shared/ui/motion.js";
import { POINTER_PATH, playScene } from "./scene.js";
import { KeyCaps } from "../shared/ui/KeyCaps.jsx";
import { BrandMark } from "../shared/ui/BrandMark";
import { CheckIcon, ChevronLeftIcon, ChevronRightIcon, HighlightIcon, LabelIcon, PaperIcon, PencilIcon, XIcon } from "../shared/ui/Icons";
import "./guide.css";
import "./media.css";
import { t, tn } from "../shared/i18n/i18n.js";

const PAD = 6;          // spotlight padding around the anchor
const BEACON_INSET = 3; // an offer's beacon sits closer to its anchor than the spotlight
const WAIT_MS = 3000;   // how long a missing anchor may take to mount
const SLOW_MS = 20000;  // a demo's wait this long says it is still working

// **bold**, *italic*, `code`, `{key:…}` key caps (guide/keys.js) and
// blank-line paragraphs — enough for tour copy without pulling in the block
// markdown renderer. Titles use the inline part, bodies the paragraphs.
const INLINE = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\{key:[^{}\s]+\})/g;
function renderInline(text, bindings) {
  return String(text || "").split(INLINE).map((part, j) => {
    if (/^\*\*[^*]+\*\*$/.test(part)) return <strong key={j}>{part.slice(2, -2)}</strong>;
    if (/^\*[^*]+\*$/.test(part)) return <em key={j}>{part.slice(1, -1)}</em>;
    if (/^`[^`]+`$/.test(part)) return <code key={j}>{part.slice(1, -1)}</code>;
    const name = /^\{key:([^{}\s]+)\}$/.exec(part)?.[1];
    const key = name ? resolveKey(name, bindings) : null;
    if (key) return key.chord ? <KeyCaps key={j} chord={key.chord} /> : key.text;
    return part;
  });
}
function renderBody(text, bindings) {
  return String(text || "").split(/\n\s*\n/).map((para, i) => <p key={i}>{renderInline(para, bindings)}</p>);
}
// A practice step may word its body for touch ("Long-press a word…").
const COARSE = () => typeof matchMedia === "function" && matchMedia("(pointer: coarse)").matches;
const REDUCED = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
const pad = (b) => ({ top: b.top - PAD, left: b.left - PAD, right: b.right + PAD, bottom: b.bottom + PAD, width: b.width + PAD * 2, height: b.height + PAD * 2 });
// What covers an anchor when it is out of sight though still laid out: a
// dialog or its backdrop, a menu, a popover opened over it (the API key
// dialog over the Zotero link dialog's Connect row).
const LAYERS = '.reportOverlay, .popover, [role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"]';
// Whether such a layer covers the anchor at every one of five points over
// its visible part (the centre and towards each corner), so a tooltip over
// a corner of it leaves it in sight. The guide's own layers do not count.
function covered(el, b, root) {
  const x0 = Math.max(b.left, 0), x1 = Math.min(b.right, window.innerWidth);
  const y0 = Math.max(b.top, 0), y1 = Math.min(b.bottom, window.innerHeight);
  if (x0 >= x1 || y0 >= y1) return false;
  return [[0.5, 0.5], [0.2, 0.2], [0.8, 0.2], [0.2, 0.8], [0.8, 0.8]].every(([fx, fy]) => {
    const hit = document.elementsFromPoint(x0 + (x1 - x0) * fx, y0 + (y1 - y0) * fy).find((n) => !root?.contains(n));
    if (!hit || el.contains(hit) || hit.contains(el)) return false;
    const layer = hit.closest(LAYERS);
    return !!layer && !layer.contains(el);
  });
}
const ringPath = (r) => `M${r.left},${r.top} h${r.width} a8,8 0 0 1 8,8 v${r.height - 16} a8,8 0 0 1 -8,8 h${-r.width} a8,8 0 0 1 -8,-8 v${-(r.height - 16)} a8,8 0 0 1 8,-8 z`;

// A step's illustration (`media`, guide/media.js): the drawing or clip of
// that id, bundled from guide/media/. It goes into the card as markup rather
// than an <img> so it reads the theme's tokens — these are this repo's own
// build-time files, never anything a user supplies. The box takes its height
// from the registry's ratio (or `ratio`, where a grid wants its boxes even),
// so the card is its final size when place.js measures it. `still` holds the
// finished picture while a demo acts, so the card has one thing moving at a
// time. The picture says the same thing as the copy beside it, so it stays
// out of the accessibility tree.
const DRAWINGS = import.meta.glob("./media/*.svg", { eager: true, query: "?raw", import: "default" });
function Media({ id, still = false }) {
  const svg = DRAWINGS[`./media/${id}.svg`];
  if (!svg) { console.warn(`guide: no drawing for media "${id}"`); return null; }
  return (
    <div className={`guideMedia ${still ? "still" : ""}`} data-media={id} aria-hidden="true"
      style={{ aspectRatio: mediaRatio(id) }} dangerouslySetInnerHTML={{ __html: svg }} />
  );
}

const PlayGlyph = () => <svg width="8" height="9" viewBox="0 0 8 9" aria-hidden="true"><path d="M0 0.5 L8 4.5 L0 8.5 Z" fill="currentColor" /></svg>;
// The copy as one plain line, for an aria-label.
const plainText = (text, bindings) => keyText(text, bindings).replace(/\*\*|\*|`/g, "");
// The ring that marks a control without dimming anything (an offer, a hint,
// the finish card's account button), a little inside the spotlight rect.
const Beacon = ({ rect }) => (
  <div className="guideBeacon" aria-hidden="true"
    style={{ top: rect.top + BEACON_INSET, left: rect.left + BEACON_INSET, width: rect.width - 2 * BEACON_INSET, height: rect.height - 2 * BEACON_INSET }} />
);
// Keep the caret where the user is typing, and keep a popover the card
// points into open (outside-click checks listen on the document).
const keepFocus = (e) => { e.preventDefault(); e.stopPropagation(); };

// The welcome deck: the first tour's arrival offer (its `intro` step, with
// `slides`). What Gamma does, one slide at a time: its drawing on a tinted
// stage, looping while the slide is up (guide.css), beside its topic, title
// and line, and ‹ n / N › under them. ‹ ›, the arrow keys and a swipe across
// the stage turn the slides. The first slide carries the guided tour; a
// slide whose feature is set up elsewhere (Zotero, Notability, an assistant)
// carries its setup guide's way in (`setup`, `go`), where that guide can
// start here. The last slide holds the one way in: the seeded Welcome page
// and its sample PDF (`welcomePage`: the library has it), where the tours
// offer themselves as each thing is first done; without it the tour is the
// way in. × and Esc close the deck, and every tour stays in the account menu.
const SWIPE_PX = 40; // a sideways drag this long across the stage turns the slide
function WelcomeDeck({ step, minutes, welcomePage, setups = [], onAction, onLater, bindings }) {
  const tour = minutes ? tn("Take the {n}-minute tour", "Take the {n}-minute tour", minutes) : t("Take the tour");
  const slides = step.slides;
  const last = slides.length - 1;
  const [at, setAt] = useState(0);
  const turn = useCallback((by) => setAt((n) => Math.max(0, Math.min(last, n + by))), [last]);
  // The arrow keys turn the slides wherever focus is, short of a text field.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      if (e.target instanceof Element && e.target.closest("input, textarea, [contenteditable]")) return;
      e.preventDefault();
      e.stopPropagation();
      turn(e.key === "ArrowRight" ? 1 : -1);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [turn]);
  const swipeFrom = useRef(null);
  const slide = slides[at];
  const setup = slide.setup && setups.includes(slide.setup) ? slide.setup : null;
  return (
    <>
      {/* Keyed by the slide, so its drawing starts from the top. */}
      <div className="welcomeStage"
        onPointerDown={(e) => { swipeFrom.current = e.clientX; }}
        onPointerUp={(e) => {
          const dx = swipeFrom.current == null ? 0 : e.clientX - swipeFrom.current;
          swipeFrom.current = null;
          if (Math.abs(dx) >= SWIPE_PX) turn(dx < 0 ? 1 : -1);
        }}>
        <Media key={slide.media} id={slide.media} />
      </div>
      <div className="welcomeCopy">
        <div className="welcomeHead">
          <BrandMark size={22} className="welcomeMark" />
          <div className="guideTitle">{renderInline(t(step.title), bindings)}</div>
          <button className="uiClose uiCloseLg guideClose" onClick={onLater} title={t("Dismiss guide (Esc)")} aria-label={t("Dismiss guide")}><XIcon size={16} /></button>
        </div>
        <div className="welcomeSlide" key={slide.media} data-slide={slide.media}>
          <div className="welcomeTopic">{t(slide.topic)}</div>
          <div className="welcomeSlideTitle">{t(slide.title)}</div>
          <div className="welcomeLine">{renderInline(t(slide.line), bindings)}</div>
          {at === last || setup || (at === 0 && welcomePage) ? (
            <div className="welcomeActions">
              {at === last ? (
                <button type="button" className="uiBtn primary welcomeWayIn" onClick={() => onAction(welcomePage ? "page" : "tour")}>
                  {welcomePage ? t(step.next) : tour}
                </button>
              ) : null}
              {at === 0 && welcomePage ? (
                <button type="button" className="uiBtn welcomeTour" onClick={() => onAction("tour")}><PlayGlyph />{tour}</button>
              ) : null}
              {setup ? (
                <button type="button" className="uiBtn ghost welcomeSetup" data-setup={setup} onClick={() => onAction(setup)}>{t(slide.go)}</button>
              ) : null}
            </div>
          ) : null}
          {at === last && step.footnote ? <span className="guideFootnote">{t(step.footnote)}</span> : null}
        </div>
        <div className="welcomePager">
          <button type="button" className="ctlBtn" disabled={at === 0} onClick={() => turn(-1)} aria-label={t("Previous slide")} title={t("Previous slide")}>
            <ChevronLeftIcon size={16} />
          </button>
          <span className="welcomeCount" aria-live="polite"><b>{at + 1}</b> / {slides.length}</span>
          <button type="button" className="ctlBtn" disabled={at === last} onClick={() => turn(1)} aria-label={t("Next slide")} title={t("Next slide")}>
            <ChevronRightIcon size={16} />
          </button>
        </div>
      </div>
    </>
  );
}

// A tour's finish card: what the run made (real, and the user's to keep),
// two tiles for what to try next, and Done.
const MADE_ICONS = { page: PaperIcon, highlight: HighlightIcon, note: PencilIcon, label: LabelIcon };
function FinishContent({ finish, onAction, onDone }) {
  // Names from the run (the paper's title, the label) stand out.
  const strong = (args) => Object.fromEntries(Object.entries(args).map(([k, v]) => [k, typeof v === "string" ? <strong key={k}>{v}</strong> : v]));
  return (
    <>
      <div className="guideHead">
        <span className="guideFinishBadge" aria-hidden="true"><CheckIcon size={20} /></span>
        <button className="uiClose uiCloseSm guideClose" onClick={onDone} title={t("Close (Esc)")} aria-label={t("Close")}><XIcon size={14} /></button>
      </div>
      <div className="guideTitle">{t(finish.title)}</div>
      {finish.lead ? <div className="guideBody"><p>{t(finish.lead)}</p></div> : null}
      {finish.made.length ? (
        <ul className="guideMade">
          {finish.made.map((item, i) => {
            const Icon = MADE_ICONS[item.icon];
            const args = strong(item.args);
            return (
              <li key={i}>
                <span className="guideMadeIcon" aria-hidden="true">{Icon ? <Icon size={14} /> : null}</span>
                <span>{item.plural ? tn(item.text, item.plural, item.args.n, args) : t(item.text, args)}</span>
              </li>
            );
          })}
        </ul>
      ) : null}
      {finish.next.length ? (
        <>
          <div className="guideNextLabel">{t("Next")}</div>
          <div className="guideNext">
            {finish.next.map((tile) => (
              <button key={tile.id} type="button" data-finish={tile.id} onClick={() => onAction(tile.id)}>
                <b>{t(tile.title)}</b>
                <small>{t(tile.sub)}</small>
              </button>
            ))}
          </div>
        </>
      ) : null}
      <div className="guideFoot">
        {finish.footnote ? <span className="guideFootnote">{t(finish.footnote)}</span> : null}
        <span className="guideBtns"><button className="uiBtn primary" onClick={onDone}>{t("Done")}</button></span>
      </div>
    </>
  );
}

// keybindings: the account's Settings → Keyboard overrides, so a `{key:…}`
// in the copy shows the chord that actually fires.
export default function GuideOverlay({ guide, keybindings }) {
  const { running, offer, index, count, done, live, back } = guide;
  const inviting = !running && !!offer;
  const visible = running || inviting;
  // Past the last step: the finish card, with a beacon on the account
  // button (where tours are replayed).
  const finishing = !!guide.finishCard;
  const step = inviting ? offer : finishing ? guide.finishCard : guide.step;
  const next = inviting ? guide.acceptOffer : guide.next;
  const dismiss = inviting ? guide.dismissOffer : guide.dismiss;
  const [rect, setRect] = useState(null);   // spotlight rect (padded) or null
  const [missing, setMissing] = useState(false);
  // A step whose anchor went out of sight once found (its menu closed, a
  // dialog opened over it): the card waits in the corner, ringing nothing,
  // for what the user is doing there to finish.
  const [held, setHeld] = useState(false);
  const cardRef = useRef(null);
  const rootRef = useRef(null);
  const [cardPos, setCardPos] = useState(null);
  // A demo waiting on something slow (a paper downloading) says so.
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    setSlow(false);
    if (!live?.status) return undefined;
    const timer = setTimeout(() => setSlow(true), SLOW_MS);
    return () => clearTimeout(timer);
  }, [live?.status, live?.stepId]);
  // A demo step's spotlight follows what it acts on; otherwise the step's anchor.
  const anchor = (!inviting && live?.anchor) || step?.anchor || null;

  // Track the anchor's box: on change, resize, scroll and DOM mutations.
  // Measured before the step is painted, and a later move lands in the
  // frame it is seen in (flushSync): the card is placed from this box, and
  // one measured after the paint shows the new step's card a frame beside
  // the last step's anchor first (shared/ui/placement.js).
  useLayoutEffect(() => {
    if (!visible || !step) return undefined;
    setHeld(false);
    if (!anchor) { setRect(null); setMissing(false); return undefined; }
    let raf = 0;
    let gone = false;
    let marked = null; // the element carrying data-guide-active
    let landed = false; // the anchor has been measured where it stands
    let stopped = false;
    const started = performance.now();
    const measure = () => {
      raf = 0;
      const el = anchorElement(anchor);
      // Controls that only show on hover also show while the guide points
      // at them ([data-guide-active] in their CSS).
      if (el !== marked) {
        marked?.removeAttribute("data-guide-active");
        el?.setAttribute("data-guide-active", "");
        marked = el;
      }
      // An anchor in a surface still arriving (the Add popover growing in,
      // motion.css) is not yet where it will stand: the step's card waits
      // for it to land rather than follow it there, and a card already up
      // keeps its place until then.
      const moving = el ? arriving(el) : [];
      if (moving.length) {
        if (!landed) setRect(null);
        Promise.all(moving.map((a) => a.finished.catch(() => {}))).then(() => { if (!stopped) schedule(); });
        return;
      }
      const b = el?.getBoundingClientRect();
      const laidOut = !!(b?.width && b?.height);
      // Found once, or there but under a dialog or menu, the anchor holds
      // the step while the user is elsewhere (its menu closed, a dialog
      // opened over it): the card waits in the corner, ringing nothing, and
      // the step picks up when the anchor is back in sight.
      if (!laidOut || covered(el, b, rootRef.current)) {
        setRect(null);
        if ((landed || laidOut) && !inviting && !finishing) { setHeld(true); return; }
        if (laidOut) return;
        if (performance.now() - started > WAIT_MS && !gone && !step.do && !finishing) {
          gone = true;
          if (!inviting && !step.optional) console.warn(`guide: anchor "${anchor}" not found, skipping step "${step.id}"`);
          setMissing(true);
        }
        return;
      }
      // The card keeps clear of the surface the anchor sits in (the Add
      // popover around its address box); so it does of a step's `avoid` anchor (the table above its add strip), and, while a
      // demo works inside the step's own anchor (a note in the Notes
      // window), of that whole anchor, so it covers nothing the demo uses next.
      const surfaceId = ANCHORS[anchor]?.surface;
      const s = surfaceId ? anchorElement(surfaceId)?.getBoundingClientRect() : null;
      const own = anchor !== step.anchor && step.anchor && step.placement !== "inside" ? anchorElement(step.anchor)?.getBoundingClientRect() : null;
      const holds = own && b.left >= own.left && b.right <= own.right && b.top >= own.top && b.bottom <= own.bottom;
      const a = step.avoid && anchor === step.anchor ? anchorElement(step.avoid)?.getBoundingClientRect() : holds ? own : null;
      const keep = [s, a].filter((box) => box?.width).map(pad);
      landed = true;
      setHeld(false);
      setRect({ ...pad(b),
        avoid: keep.length ? { top: Math.min(...keep.map((k) => k.top)), left: Math.min(...keep.map((k) => k.left)),
          right: Math.max(...keep.map((k) => k.right)), bottom: Math.max(...keep.map((k) => k.bottom)) } : null });
    };
    const schedule = () => { if (!raf) raf = requestAnimationFrame(() => flushSync(measure)); };
    measure();
    if (!inviting && !finishing) anchorElement(anchor)?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    // The guide's own layers (a scene's shapes, the demo pointer) move no anchor.
    const mo = new MutationObserver((records) => {
      if (records.some((r) => !rootRef.current?.contains(r.target))) schedule();
    });
    mo.observe(document.body, { childList: true, subtree: true, attributes: true });
    // A panel that slides or grows by a CSS transition moves the anchor
    // with no mutation at its end, so the ring is measured again there.
    const MOVES = ["resize", "scroll", "transitionend", "animationend"];
    for (const type of MOVES) window.addEventListener(type, schedule, true);
    const retry = setInterval(schedule, 250); // covers the wait for a late mount
    return () => {
      stopped = true;
      mo.disconnect();
      for (const type of MOVES) window.removeEventListener(type, schedule, true);
      clearInterval(retry);
      if (raf) cancelAnimationFrame(raf);
      marked?.removeAttribute("data-guide-active");
    };
  }, [visible, inviting, finishing, step, anchor]);

  // A missing anchor skips the step.
  useEffect(() => {
    if (missing) { setMissing(false); if (inviting) dismiss(); else next(); }
  }, [missing, inviting, dismiss, next]);

  // Card placement follows the spotlight; measured after render.
  useLayoutEffect(() => {
    if (!visible) return;
    const card = cardRef.current;
    if (!card) return;
    const vw = window.innerWidth, vh = window.innerHeight;
    if (!rect) { setCardPos(null); return; }
    setCardPos(placeCard(rect, card.offsetHeight, vw, vh, step?.placement, rect.avoid));
  }, [visible, rect, step]);

  // The step's scene plays while the step is the user's to look at: not
  // during a demo, the Done moment, an offer or the finish card, and only
  // once its spotlight is measured. It restarts only with the step.
  const sceneRef = useRef(null);
  const busy = !inviting && !!live?.busy;
  const scene = visible && !inviting && !finishing && !busy && !done && !live?.failed && rect ? step?.scene || null : null;
  useEffect(() => {
    const layer = sceneRef.current;
    if (!scene || !layer) return undefined;
    return playScene(layer, scene, { services: { dockZone: guide.dockZone }, reduced: REDUCED(), touch: COARSE() });
  }, [scene]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!visible || !step) return null;
  if (finishing) {
    return (
      <div className="guideRoot" ref={rootRef} data-guide-finish={step.id}>
        <div className="guideScrim" onClick={guide.dismiss} aria-hidden="true" />
        {rect ? <Beacon rect={rect} /> : null}
        <div className="guideCard guideCardCentered guideCardFinish" role="dialog" aria-live="polite" aria-label={t(step.title)}
          onMouseDown={keepFocus} onPointerDown={(e) => e.stopPropagation()}>
          <FinishContent finish={step} onAction={guide.finishAction} onDone={guide.dismiss} />
        </div>
      </div>
    );
  }
  // The welcome deck: the first tour's arrival offer, centred over the
  // finish card's light scrim, which closes it as its × does.
  // It points at nothing.
  if (inviting && offer.welcome) {
    return (
      <div className="guideRoot" ref={rootRef} data-guide-offer={offer.id}>
        <div className="guideScrim" onClick={dismiss} aria-hidden="true" />
        <div className="guideCard guideCardCentered welcomeDeck" role="dialog" aria-live="polite"
          aria-label={plainText(t(offer.title), keybindings)} onMouseDown={keepFocus} onPointerDown={(e) => e.stopPropagation()}>
          <WelcomeDeck step={offer} minutes={offer.minutes} welcomePage={offer.welcomePage} setups={offer.setups} bindings={keybindings}
            onAction={guide.welcomeAction} onLater={dismiss} />
        </div>
      </div>
    );
  }
  const waiting = anchor && !rect && !held;
  const centered = !anchor && !inviting;
  const vw = window.innerWidth, vh = window.innerHeight;
  const ring = rect ? ringPath(rect) : "";
  // What the card is: a demo to watch, the user's turn (a `creates` step:
  // the rest of the tour needs what it makes, so it waits), the
  // acknowledgement of an action, or a light step — one that explains, or
  // invites a try with Next still there (its `advanceOn` ticks it if the
  // user does it).
  const failed = !inviting && !!live?.failed;
  const yourTurn = !inviting && !busy && !failed && !done && (!!step.creates || guide.waits);
  const primaryLabel = inviting ? (offer.hint ? t("Got it") : offer.resume ? t("Continue") : t("Show me")) : failed ? t("Skip") : step.next ? t(step.next)
    : index + 1 >= count ? t("Done") : t("Next");
  const showPrimary = inviting || (!busy && !done && !yourTurn);
  const showBack = !inviting && index > 0 && !busy && !failed && !done;
  const link = busy ? (step.skippable !== false ? t("Skip this demo") : null)
    : yourTurn ? (step.next ? t(step.next) : t("Skip step")) : null;
  const body = !inviting && COARSE() && step.bodyTouch ? step.bodyTouch : step.body;
  // A resumed setup guide's card names its next step.
  const bodyArgs = step.bodyArgs ? Object.fromEntries(Object.entries(step.bodyArgs).map(([k, v]) => [k, t(v)])) : undefined;
  const checklist = !inviting ? guide.checklist : null;

  return (
    <div className={`guideRoot ${inviting ? "guideInvitation" : ""} ${inviting && offer.hint ? "guideHint" : ""} ${done ? "done" : ""} ${busy ? "busy" : ""}`} data-guide-overlay={inviting ? undefined : step.id} data-guide-offer={inviting ? offer.id : undefined} data-guide-busy={busy ? "1" : undefined} ref={rootRef}>
      {/* A centred card's sheet, dimmed, takes the clicks; a step at an
          anchor dims and blocks nothing: its ring marks the control. */}
      {centered ? <div className="guideDim" aria-hidden="true" /> : null}
      {!inviting && !held && rect ? (
        <svg className="guideMarks" width={vw} height={vh} viewBox={`0 0 ${vw} ${vh}`} aria-hidden="true">
          <path d={ring} className="guideHalo" />
          <path d={ring} className="guideRing" />
        </svg>
      ) : null}
      {busy ? <div className="guideShield" aria-hidden="true" /> : null}
      {inviting && rect ? <Beacon rect={rect} /> : null}
      {scene ? <div className="guideScene" ref={sceneRef} aria-hidden="true" data-guide-scene={step.id} /> : null}
      {!inviting && live?.cursor ? (
        <div
          className={`guidePointer guideCursor ${live.cursor.pressed ? "pressed" : ""} ${live.cursor.dragging ? "dragging" : ""} ${live.cursor.faded ? "faded" : ""}`}
          style={{ transform: `translate(${live.cursor.x}px, ${live.cursor.y}px)` }}
          aria-hidden="true"
        >
          <svg width="22" height="26" viewBox="0 0 22 26">
            <path d={POINTER_PATH} />
          </svg>
          {live.cursor.modifier ? <kbd className="guideModifier">{live.cursor.modifier}</kbd> : null}
        </div>
      ) : null}
      {!waiting || busy ? (
        <div
          ref={cardRef}
          className={`guideCard ${rect && rect.top > vh / 2 ? "guideCardAbove" : ""} ${centered && !busy ? "guideCardCentered" : ""} ${cardPos ? `side-${cardPos.side}` : ""} ${(busy || inviting || held) && !cardPos && !centered ? "guideCardCorner" : ""}`}
          style={cardPos ? { top: cardPos.top, left: cardPos.left, width: CARD_W } : undefined}
          onMouseDown={keepFocus}
          onPointerDown={(e) => e.stopPropagation()}
          role="dialog"
          aria-live="polite"
          aria-label={plainText(t(step.title), keybindings)}
        >
          {cardPos?.beak ? (
            <span className={`guideBeak beak-${cardPos.side}`} aria-hidden="true"
              style={cardPos.beak.x != null ? { left: cardPos.beak.x - 7 } : { top: cardPos.beak.y - 7 }} />
          ) : null}
          <div className="guideHead">
            {inviting ? (
              <span className="guideStep">
                {offer.hint ? <span className="guideChip tip">{t("Tip")}</span>
                  : offer.resume ? t("Setup guide · {done} of {count} done", { done: offer.doneCount, count: offer.count })
                  : offer.setup ? tn("Setup guide · {n} step", "Setup guide · {n} steps", offer.count)
                  : tn("Quick tour · {n} step", "Quick tour · {n} steps", offer.count)}
              </span>
            ) : (
              <>
                {failed ? <span className="guideChip failed">{t("couldn't finish")}</span>
                  : done ? <span className="guideChip done guideDone">{t("✓ Done")}</span>
                  : busy ? <span className="guideChip watch"><PlayGlyph />{t("Watch")}</span>
                  : yourTurn ? <span className="guideChip turn">{t("Your turn")}</span> : null}
                <span className="guideCount">{t("Step {n} of {count}", { n: index + 1, count })}</span>
              </>
            )}
            <button className="uiClose uiCloseSm guideClose" onClick={dismiss} title={inviting ? t("Dismiss guide (Esc)") : t("Leave the tour (Esc)")} aria-label={inviting ? t("Dismiss guide") : t("Leave the tour")}><XIcon size={14} /></button>
          </div>
          {/* The step's drawing or clip leads, its copy explains; a clip
              starts once the step's demo has acted. An offer and a hint stay
              a card of words (guide/media.js). */}
          {!inviting && step.media ? <Media id={step.media} key={busy ? "still" : "live"} still={busy} /> : null}
          <div className="guideTitle">{renderInline(t(step.title), keybindings)}</div>
          {t(body) ? <div className="guideBody">{renderBody(t(body, bodyArgs), keybindings)}</div> : null}
          {busy && live?.status ? (
            <div className="guideWait" role="status">
              <span className="guideSpinner" aria-hidden="true" />
              <span>
                {t(live.status)}
                {slow ? <span className="guideWaitSlow">{t("This can take a minute on a slow connection.")}</span> : null}
              </span>
            </div>
          ) : null}
          {busy && live?.progress ? (
            <div className="guideDemoBar" aria-hidden="true"><i style={{ width: `${(100 * live.progress[0]) / Math.max(1, live.progress[1])}%` }} /></div>
          ) : null}
          {/* A setup guide's steps as a list, done ones ticked: it is left
              for other apps and picked up again, so where it stands shows.
              Any step can be gone back to. Other tours show a bar. */}
          {checklist ? (
            <ol className="guideChecklist">
              {checklist.map((item, i) => (
                <li key={item.id} className={`${item.done ? "done" : ""} ${i === index ? "now" : ""}`}>
                  <button type="button" onClick={() => guide.goTo(i)} aria-current={i === index ? "step" : undefined}>
                    <span className="guideCheck" aria-hidden="true">{item.done ? <CheckIcon size={10} /> : null}</span>
                    <span>{renderInline(t(item.title), keybindings)}</span>
                  </button>
                </li>
              ))}
            </ol>
          ) : !inviting ? (
            <div className="guideSegments" aria-hidden="true">
              {Array.from({ length: count }, (_, i) => <i key={i} className={i === index ? "on" : i < index ? "done" : ""} />)}
            </div>
          ) : null}
          {/* The primary button is the call to action only where the step
              itself is the action (Next, Done); a demo can be skipped, and on
              the user's turn the task is the call, not skipping it. Dismiss
              leaves the tour from any step, as × and Esc do. */}
          <div className="guideFoot">
            {!inviting ? <button className="uiBtn sm ghost guideDismiss" onClick={dismiss}>{t("Dismiss")}</button>
              : !offer.hint ? <button className="uiBtn sm ghost" onClick={dismiss}>{t("Not now")}</button> : null}
            {showBack ? <button className="uiBtn" onClick={back}>{t("Back")}</button> : null}
            {link ? <button className="uiBtn sm ghost guideLink" onClick={busy ? guide.skipDemo : next}>{link}</button> : null}
            <span className="guideBtns">
              {showPrimary ? <button className="uiBtn primary" onClick={next}>{primaryLabel}</button> : null}
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
