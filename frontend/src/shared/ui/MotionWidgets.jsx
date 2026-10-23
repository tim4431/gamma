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

// A label that changes faster than this shows each new text at once: a
// count or a progress line would never rest between swaps.
const QUICK_MS = 600;

// Whether a label's box cuts its text (an ellipsis): a moving part that
// crosses that edge is hidden whole, so such a label swaps at once.
const cut = (el) => {
  const box = el?.parentElement;
  return !!box && box.scrollWidth > box.clientWidth + 1;
};

// A short label that changes in place (Copy → Copied, Save → Saved): the
// letters both texts share at the start and the end stay where they are,
// the part between cross-fades through a soft blur, and its width eases
// from the old text's to the new one's, so the control around it resizes
// smoothly (motion.css .morph). Two texts that share nothing cross-fade
// whole. One span of plain text at rest. It shows the new text at once
// under reduced motion, when the text changes faster than a swap takes,
// and when the old or the new text is cut by its box.
export function MorphText({ children, className = "" }) {
  const text = String(children ?? "");
  const [morph, setMorph] = useState(null); // {text, head, from, to, tail}
  const shown = useRef(text);
  const changed = useRef(-Infinity); // when the text last changed
  const fits = useRef(true); // the text shown at rest fits its box
  const rootRef = useRef(null);
  const swapRef = useRef(null);

  useLayoutEffect(() => { fits.current = !cut(rootRef.current); }, []);
  useLayoutEffect(() => {
    if (shown.current === text) return;
    const from = shown.current;
    shown.current = text;
    const now = performance.now(), quick = now - changed.current < QUICK_MS;
    changed.current = now;
    // this render shows the new text plainly: its fit is measured now
    const fitted = fits.current;
    fits.current = !cut(rootRef.current);
    setMorph(reducedMotion() || quick || !fitted || !fits.current ? null : { text, ...textSplit(from, text) });
  }, [text]);

  useLayoutEffect(() => {
    const swap = swapRef.current;
    if (!morph || !swap) return undefined;
    // a label changed again mid-swap: the cell React kept starts over
    swap.classList.remove("go");
    swap.style.width = "";
    // widths in the cell's own CSS pixels (a button's interface zoom, an
    // ancestor's arrival scale, are not in them)
    const [was, now] = swap.children;
    const width = (el) => `${parseFloat(getComputedStyle(el).width) || 0}px`;
    swap.style.width = width(was);
    void swap.offsetWidth; // the old width stands, so the new one is a change
    swap.classList.add("go");
    swap.style.width = width(now);
    const timer = setTimeout(() => setMorph(null), longestMs(getComputedStyle(swap).transitionDuration) + 50);
    return () => clearTimeout(timer);
  }, [morph]);

  // The label's text is one text node throughout: assistive technology,
  // a live region and whatever finds the label by its text see it whole,
  // once. Mid-swap it is unseen, and the pieces that move are drawn beside
  // it as generated content (motion.css), which holds no text of its own.
  const live = morph?.text === text ? morph : null;
  return (
    <span ref={rootRef} className={`morph ${live ? "moving" : ""} ${className}`.trim()}>
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
// row has folded. A first render, open or shut, does not move. In a column
// with a gap the gap above it folds with it (--fold-gap), and while it
// moves nothing scrolls it (a field focusing itself as it mounts). While
// `still` holds, a change of `open` takes effect at once (what opened it
// is about to measure or scroll to what it holds); a move under way ends
// on its own.
export function Collapse({ open, still = false, children, className = "", ...rest }) {
  // shut → opening → open → closing → shut; `open` starts a move, the
  // move's end (its recipe's own length) settles it
  const [phase, setPhase] = useState(open ? "open" : "shut");
  if (open && (phase === "shut" || phase === "closing")) setPhase(still ? "open" : "opening");
  if (!open && (phase === "open" || phase === "opening")) setPhase(still ? "shut" : "closing");
  const ref = useRef(null);
  const moving = phase === "opening" || phase === "closing";
  useLayoutEffect(() => {
    if (!moving) return undefined;
    const el = ref.current;
    if (el?.parentElement) {
      const gap = parseFloat(getComputedStyle(el.parentElement).rowGap) || 0;
      el.style.setProperty("--fold-gap", `${gap}px`);
    }
    const cs = el && !reducedMotion() ? getComputedStyle(el) : null;
    const ms = cs ? Math.max(longestMs(cs.transitionDuration), longestMs(cs.animationDuration)) : 0;
    const timer = setTimeout(() => setPhase(phase === "opening" ? "open" : "shut"), ms);
    return () => clearTimeout(timer);
  }, [phase, moving]);
  if (phase === "shut") return null;
  return (
    <div ref={ref} className={`collapse ${className}`.trim()} data-phase={phase}
      inert={phase === "closing" ? "" : undefined} {...rest}>
      <div className="collapseBody" onScroll={moving ? (e) => { e.currentTarget.scrollTop = 0; } : undefined}>{children}</div>
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
// aria-checked; the pill takes its box and its corners, in the group's own
// CSS pixels (an option carries the interface zoom the group does not; a
// surface arriving around the group scales both). Placed still the first
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
    const g = group.getBoundingClientRect(), r = on.getBoundingClientRect();
    const k = group.offsetWidth ? g.width / group.offsetWidth : 1; // the group's px to the window's
    const z = on.offsetWidth ? r.width / on.offsetWidth / k : 1; // the option's px to the group's
    set("--pill-x", `${(r.left - g.left) / k - group.clientLeft}px`);
    set("--pill-y", `${(r.top - g.top) / k - group.clientTop}px`);
    set("--pill-w", `${r.width / k}px`);
    set("--pill-h", `${r.height / k}px`);
    set("--pill-r", getComputedStyle(on).borderRadius.replace(/[\d.]+px/g, (v) => `${parseFloat(v) * z}px`));
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
