// The motion widgets (docs/dev/ui-design.md, "Motion"): what changes in
// place without leaving it. Each is a little markup with its recipe in
// motion.css; at rest it is plain, and a first render never moves.
import React, { useCallback, useLayoutEffect, useRef, useState } from "react";
import { ChevronDownIcon, ChevronRightIcon } from "./Icons";
import { longestMs, reducedMotion, textSplit } from "./motion.js";

// Two icons in one slot (copy and its check, play and pause): `to` shows
// while `swap` holds, `from` otherwise, and a change cross-fades them
// through a soft blur and a scale (motion.css .iconSwap). Both stay in the
// DOM, so the slot never changes size. The control carries the name.
export function IconSwap({ swap, from, to, className = "" }) {
  return (
    <span className={`iconSwap ${className}`.trim()} data-swap={swap ? "to" : "from"} aria-hidden="true">
      <span className="iconSwapFace" data-face="from">{from}</span>
      <span className="iconSwapFace" data-face="to">{to}</span>
    </span>
  );
}

// A short label that changes in place (Copy → Copied, Save → Saved): the
// letters both texts share at the start and the end stay where they are,
// the part between cross-fades through a soft blur, and its width eases
// from the old text's to the new one's, so the control around it resizes
// smoothly (motion.css .morph). Two texts that share nothing cross-fade
// whole. One span of plain text at rest.
export function MorphText({ children, className = "" }) {
  const text = String(children ?? "");
  const [morph, setMorph] = useState(null); // {text, head, from, to, tail}
  const shown = useRef(text);
  const swapRef = useRef(null);

  useLayoutEffect(() => {
    if (shown.current === text) return;
    const from = shown.current;
    shown.current = text;
    setMorph(reducedMotion() ? null : { text, ...textSplit(from, text) });
  }, [text]);

  useLayoutEffect(() => {
    const swap = swapRef.current;
    if (!morph || !swap) return undefined;
    // a label changed again mid-swap: the cell React kept starts over
    swap.classList.remove("go");
    swap.style.width = "";
    const [was, now] = swap.children;
    swap.style.width = `${was.getBoundingClientRect().width}px`;
    void swap.offsetWidth; // the old width stands, so the new one is a change
    swap.classList.add("go");
    swap.style.width = `${now.getBoundingClientRect().width}px`;
    const timer = setTimeout(() => setMorph(null), longestMs(getComputedStyle(swap).transitionDuration) + 50);
    return () => clearTimeout(timer);
  }, [morph]);

  // The label's text is one text node throughout: assistive technology,
  // a live region and whatever finds the label by its text see it whole,
  // once. Mid-swap it is unseen, and the pieces that move are drawn beside
  // it as generated content (motion.css), which holds no text of its own.
  const live = morph?.text === text ? morph : null;
  return (
    <span className={`morph ${live ? "moving" : ""} ${className}`.trim()}>
      <span className="morphText">{text}</span>
      {live ? (
        <span className="morphShow" aria-hidden="true">
          <span data-text={live.head} />
          <span ref={swapRef} className="morphSwap">
            <span className="morphFrom" data-text={live.from} />
            <span className="morphTo" data-text={live.to} />
          </span>
          <span data-text={live.tail} />
        </span>
      ) : null}
    </span>
  );
}

// A body that opens and closes under its header: its height eases between
// nothing and its content's through a grid row (no measuring), and the
// content fades in and sharpens as it opens (motion.css .collapse). Shut,
// nothing is rendered; while it closes the content stays, inert, until the
// row has folded. A first render, open or shut, does not move.
export function Collapse({ open, children, className = "", ...rest }) {
  // shut → opening → open → closing → shut; `open` starts a move, the
  // move's end (its recipe's own length) settles it
  const [phase, setPhase] = useState(open ? "open" : "shut");
  if (open && (phase === "shut" || phase === "closing")) setPhase("opening");
  if (!open && (phase === "open" || phase === "opening")) setPhase("closing");
  const ref = useRef(null);
  useLayoutEffect(() => {
    if (phase !== "opening" && phase !== "closing") return undefined;
    const el = ref.current;
    const cs = el && !reducedMotion() ? getComputedStyle(el) : null;
    const ms = cs ? Math.max(longestMs(cs.transitionDuration), longestMs(cs.animationDuration)) : 0;
    const timer = setTimeout(() => setPhase(phase === "opening" ? "open" : "shut"), ms);
    return () => clearTimeout(timer);
  }, [phase]);
  if (phase === "shut") return null;
  return (
    <div ref={ref} className={`collapse ${className}`.trim()} data-phase={phase}
      inert={phase === "closing" ? "" : undefined} {...rest}>
      <div className="collapseBody">{children}</div>
    </div>
  );
}

// A chevron that turns with what it opens (motion.css .turnChev): one
// glyph, turned rather than swapped for another. `dir` "right" points
// right shut and down open (a disclosure, an outline node); "down" points
// down shut and up open (a pill whose list hangs under it).
export function Chevron({ open, dir = "down", size = 14, className = "" }) {
  const Icon = dir === "right" ? ChevronRightIcon : ChevronDownIcon;
  return <Icon size={size} aria-hidden="true" className={`turnChev ${dir}${open ? " open" : ""} ${className}`.trim()} />;
}

// The highlight under a group's chosen option slides to the option chosen
// next (motion.css .slidePill): render a `<span className="slidePill" />`
// as the group's first child and call this with the group's ref and the
// chosen value. The chosen option is the child marked aria-pressed or
// aria-checked; the pill takes its layout box (offsets, which a surface's
// arrival scale leaves alone) and its corners. Placed still the first
// time, when the group resizes, and under reduced motion.
export function useSlidingPill(ref, value) {
  const placed = useRef(false);
  const place = useCallback((slide) => {
    const group = ref.current;
    if (!group) return;
    const on = group.querySelector(':scope > [aria-pressed="true"], :scope > [aria-checked="true"]');
    if (!on) { group.removeAttribute("data-pill"); placed.current = false; return; }
    const still = !slide || !placed.current;
    if (still) group.setAttribute("data-pill-still", "");
    const set = (name, v) => group.style.setProperty(name, v);
    set("--pill-x", `${on.offsetLeft}px`);
    set("--pill-y", `${on.offsetTop}px`);
    set("--pill-w", `${on.offsetWidth}px`);
    set("--pill-h", `${on.offsetHeight}px`);
    set("--pill-r", getComputedStyle(on).borderRadius);
    group.setAttribute("data-pill", "");
    if (still) { void group.offsetWidth; group.removeAttribute("data-pill-still"); }
    placed.current = true;
  }, [ref]);
  useLayoutEffect(() => { place(true); }, [place, value]);
  useLayoutEffect(() => {
    const group = ref.current;
    if (!group) return undefined;
    const observer = new ResizeObserver(() => place(false));
    observer.observe(group);
    return () => observer.disconnect();
  }, [ref, place]);
}

// Props for an <img> that fades in once it has loaded (motion.css
// img[data-fade]): `<img src={…} {...FADE_IN} />`. One already in the
// cache shows at once, before the first paint; one that fails to load
// shows too, so the browser's broken-image mark is never hidden.
const showImage = (img) => { if (img) img.dataset.fade = "shown"; };
export const FADE_IN = {
  "data-fade": "",
  ref: (img) => { if (img?.complete) showImage(img); },
  onLoad: (e) => showImage(e.currentTarget),
  onError: (e) => showImage(e.currentTarget),
};
