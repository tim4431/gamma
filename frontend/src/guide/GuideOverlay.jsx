// The guide's visible part: a dimmed sheet with a cut-out around the current
// step's anchor (clicks pass through the hole, so the user acts on the real
// control) and a card beside it. A step without an anchor is a centred card.
// A demo step moves the spotlight to whatever it acts on and shows a pointer
// gliding there. Anchors are found by data-guide id, retried briefly while
// the UI mounts; a step whose anchor never appears is skipped with a warning,
// never shown pointing at nothing. An offer (a triggered tour's invitation,
// or a hint) is the same card without the dimmed sheet. The card never takes
// focus or counts as a click outside the popover it points into.
// docs/dev/onboarding.md.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { anchorElement } from "./anchors.js";
import { keyText, resolveKey } from "./keys.js";
import { mediaRatio } from "./media.js";
import { CARD_W, placeCard } from "./place.js";
import { KeyCaps } from "../shared/ui/KeyCaps.jsx";
import { CheckIcon, HighlightIcon, LabelIcon, PaperIcon, PencilIcon, XIcon } from "../shared/ui/Icons";
import "./guide.css";
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

// A step's illustration (`media`, guide/media.js): the drawing of that id,
// bundled from guide/media/. It goes into the card as markup rather than an
// <img> so it reads the theme's tokens — these are this repo's own
// build-time files, never anything a user supplies. The box takes its height
// from the registry's ratio, so the card is its final size when place.js
// measures it. The drawing says the same thing as the copy beside it, so it
// stays out of the accessibility tree.
const DRAWINGS = import.meta.glob("./media/*.svg", { eager: true, query: "?raw", import: "default" });
function Media({ id }) {
  const svg = DRAWINGS[`./media/${id}.svg`];
  if (!svg) { console.warn(`guide: no drawing for media "${id}"`); return null; }
  return (
    <div className="guideMedia" data-media={id} aria-hidden="true"
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

// A welcome tour's intro (and its offer): what the tour does and how long
// it takes, an outline marking what Gamma shows and what the user tries,
// Start, and a way out that says the tour stays available.
function WelcomeContent({ step, minutes, onStart, onLater, bindings }) {
  return (
    <>
      <div className="guideHead">
        {minutes ? <span className="guideChip accent">{tn("{n}-minute tour", "{n}-minute tour", minutes)}</span> : null}
        <button className="uiClose uiCloseSm guideClose" onClick={onLater} title={t("Dismiss guide (Esc)")} aria-label={t("Dismiss guide")}><XIcon size={14} /></button>
      </div>
      <div className="guideTitle">{renderInline(t(step.title), bindings)}</div>
      {step.body ? <div className="guideBody">{renderBody(t(step.body), bindings)}</div> : null}
      {step.outline?.length ? (
        <ol className="guideOutline">
          {step.outline.map((item, i) => (
            <li key={i}>
              <span className="guideOutlineNum" aria-hidden="true">{i + 1}</span>
              <span>{t(item.text)}</span>
              <span className="guideOutlineKind">{item.kind === "try" ? t("you try") : t("watch")}</span>
            </li>
          ))}
        </ol>
      ) : null}
      <div className="guideFoot">
        <button className="uiBtn ghost" onClick={onLater}>{step.later ? t(step.later) : t("Not now")}</button>
        <span className="guideBtns"><button className="uiBtn primary" onClick={onStart}>{step.next ? t(step.next) : t("Start")}</button></span>
      </div>
      {step.footnote ? <div className="guideFootnote">{t(step.footnote)}</div> : null}
    </>
  );
}

// A tour's finish card: what the run made (real, and the user's to keep),
// two tiles for what to try next, and Done.
const MADE_ICONS = { page: PaperIcon, highlight: HighlightIcon, note: PencilIcon, label: LabelIcon };
function FinishContent({ finish, onAction, onDone }) {
  // Names from the run (the paper's title, the label) stand out.
  const strong = (args) => Object.fromEntries(Object.entries(args).map(([k, v]) => [k, typeof v === "string" ? <strong>{v}</strong> : v]));
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
  const cardRef = useRef(null);
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
  useEffect(() => {
    if (!visible || !step) return undefined;
    if (!anchor) { setRect(null); setMissing(false); return undefined; }
    let raf = 0;
    let gone = false;
    let marked = null; // the element carrying data-guide-active
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
      const b = el?.getBoundingClientRect();
      if (!b?.width || !b?.height) {
        setRect(null);
        if (performance.now() - started > WAIT_MS && !gone && !step.do && !finishing) {
          gone = true;
          if (!inviting && !step.optional) console.warn(`guide: anchor "${anchor}" not found, skipping step "${step.id}"`);
          setMissing(true);
        }
        return;
      }
      // A step's `avoid` anchor is a box its card keeps clear of too (the
      // table above its add strip).
      const a = step.avoid && anchor === step.anchor ? anchorElement(step.avoid)?.getBoundingClientRect() : null;
      setRect({ top: b.top - PAD, left: b.left - PAD, width: b.width + PAD * 2, height: b.height + PAD * 2,
        right: b.right + PAD, bottom: b.bottom + PAD,
        avoid: a?.width ? { top: a.top - PAD, left: a.left - PAD, right: a.right + PAD, bottom: a.bottom + PAD } : null });
    };
    const schedule = () => { if (!raf) raf = requestAnimationFrame(measure); };
    measure();
    if (!inviting && !finishing) anchorElement(anchor)?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    const mo = new MutationObserver(schedule);
    mo.observe(document.body, { childList: true, subtree: true, attributes: true });
    window.addEventListener("resize", schedule);
    window.addEventListener("scroll", schedule, true);
    const retry = setInterval(schedule, 250); // covers the wait for a late mount
    return () => {
      mo.disconnect();
      window.removeEventListener("resize", schedule);
      window.removeEventListener("scroll", schedule, true);
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

  if (!visible || !step) return null;
  if (finishing) {
    return (
      <div className="guideRoot" data-guide-finish={step.id}>
        <div className="guideScrim" onClick={guide.dismiss} aria-hidden="true" />
        {rect ? <Beacon rect={rect} /> : null}
        <div className="guideCard guideCardCentered guideCardWelcome guideCardFinish" role="dialog" aria-live="polite" aria-label={t(step.title)}
          onMouseDown={keepFocus} onPointerDown={(e) => e.stopPropagation()}>
          <FinishContent finish={step} onAction={guide.finishAction} onDone={guide.dismiss} />
        </div>
      </div>
    );
  }
  const waiting = anchor && !rect;
  // The welcome card: a welcome tour's intro step, or its offer.
  const welcome = inviting ? !!offer.welcome : !!step.intro && !anchor;
  const centered = !anchor && (!inviting || welcome);
  const busy = !inviting && !!live?.busy;
  const vw = window.innerWidth, vh = window.innerHeight;
  const hole = rect
    ? `M${rect.left},${rect.top} h${rect.width} a8,8 0 0 1 8,8 v${rect.height - 16} a8,8 0 0 1 -8,8 h${-rect.width} a8,8 0 0 1 -8,-8 v${-(rect.height - 16)} a8,8 0 0 1 8,-8 z`
    : "";
  // What the card is: a demo to watch, the user's turn (a step that waits
  // for their action), the acknowledgement of it, or a step that explains.
  const failed = !inviting && !!live?.failed;
  const yourTurn = !inviting && !busy && !failed && !done && !!step.advanceOn;
  const primaryLabel = inviting ? (offer.hint ? t("Got it") : t("Show me")) : failed ? t("Skip") : step.next ? t(step.next)
    : index + 1 >= count ? t("Done") : t("Next");
  const showPrimary = inviting || (!busy && !done && !yourTurn);
  const showBack = !inviting && index > 0 && !busy && !failed && !done;
  const link = busy ? (step.skippable !== false ? t("Skip this demo") : null)
    : yourTurn ? (step.next ? t(step.next) : t("Skip step")) : null;
  const body = !inviting && COARSE() && step.bodyTouch ? step.bodyTouch : step.body;

  return (
    <div className={`guideRoot ${inviting ? "guideInvitation" : ""} ${inviting && offer.hint ? "guideHint" : ""} ${done ? "done" : ""} ${busy ? "busy" : ""}`} data-guide-overlay={inviting ? undefined : step.id} data-guide-offer={inviting ? offer.id : undefined} data-guide-busy={busy ? "1" : undefined}>
      {!inviting ? <svg className="guideDim" width={vw} height={vh} viewBox={`0 0 ${vw} ${vh}`} aria-hidden="true">
        <path
          d={`M0,0 H${vw} V${vh} H0 Z ${hole}`}
          fillRule="evenodd"
          className="guideDimFill"
          style={{ pointerEvents: centered || busy ? "auto" : "visiblePainted" }}
        />
        {rect ? <path d={hole} className="guideRing" /> : null}
      </svg> : null}
      {busy && rect ? <div className="guideShield" aria-hidden="true" /> : null}
      {inviting && rect ? <Beacon rect={rect} /> : null}
      {!inviting && live?.cursor ? (
        <div
          className={`guideCursor ${live.cursor.pressed ? "pressed" : ""} ${live.cursor.dragging ? "dragging" : ""} ${live.cursor.faded ? "faded" : ""}`}
          style={{ transform: `translate(${live.cursor.x}px, ${live.cursor.y}px)` }}
          aria-hidden="true"
        >
          <svg width="22" height="26" viewBox="0 0 22 26">
            <path d="M2 2 L2 20 L7 15.5 L10.5 23 L14 21.5 L10.5 14 L17 14 Z" fill="#fff" stroke="#111" strokeWidth="1.4" strokeLinejoin="round" />
          </svg>
          {live.cursor.modifier ? <kbd className="guideModifier">{live.cursor.modifier}</kbd> : null}
        </div>
      ) : null}
      {!waiting || busy ? (
        <div
          ref={cardRef}
          className={`guideCard ${rect && rect.top > vh / 2 ? "guideCardAbove" : ""} ${centered && !busy ? "guideCardCentered" : ""} ${welcome ? "guideCardWelcome" : ""} ${cardPos ? `side-${cardPos.side}` : ""} ${(busy || inviting) && !cardPos && !centered ? "guideCardCorner" : ""}`}
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
          {welcome ? (
            <WelcomeContent step={step} minutes={inviting ? offer.minutes : guide.tour?.minutes} bindings={keybindings}
              onStart={next} onLater={dismiss} />
          ) : (
            <>
              <div className="guideHead">
                {inviting ? (
                  <span className="guideStep">
                    {offer.hint ? <span className="guideChip tip">{t("Tip")}</span> : tn("Quick tour · {n} step", "Quick tour · {n} steps", offer.count)}
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
              {/* The step's drawing leads, its copy explains. An offer and a
                  hint stay a card of words (guide/media.js). */}
              {!inviting && step.media ? <Media id={step.media} /> : null}
              <div className="guideTitle">{renderInline(t(step.title), keybindings)}</div>
              {t(body) ? <div className="guideBody">{renderBody(t(body), keybindings)}</div> : null}
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
              {!inviting ? (
                <div className="guideSegments" aria-hidden="true">
                  {Array.from({ length: count }, (_, i) => <i key={i} className={i === index ? "on" : i < index ? "done" : ""} />)}
                </div>
              ) : null}
              {/* The primary button is the call to action only where the step
                  itself is the action (Next, Done); a demo can be skipped, and on
                  the user's turn the task is the call, not skipping it. The row
                  stays (empty) through the Done moment, so the card does not jump. */}
              {showPrimary || showBack || link || done ? (
                <div className="guideFoot">
                  {inviting && !offer.hint ? <button className="uiBtn sm ghost" onClick={dismiss}>{t("Not now")}</button> : null}
                  {showBack ? <button className="uiBtn" onClick={back}>{t("Back")}</button> : null}
                  {link ? <button className="uiBtn sm ghost guideLink" onClick={next}>{link}</button> : null}
                  <span className="guideBtns">
                    {showPrimary ? <button className="uiBtn primary" onClick={next}>{primaryLabel}</button> : null}
                  </span>
                </div>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
