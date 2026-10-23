// Lists that change (docs/dev/ui-design.md, "Lists that change"): around a
// list, what moves in it glides from where it stood to where it now
// stands, and what is new fades in where it will rest; what goes is gone
// at once while the rest closes up. The items are the elements under it
// with a `data-motion-key`, unique in the list; an item may hold others (a
// row's children under it), and what it holds travels with it.
//
// It reads their boxes as React is about to change the page
// (getSnapshotBeforeUpdate runs before any DOM change of the commit) and
// again once it has, and starts each glide before the frame is painted:
// the page is laid out at once in its new order, and only the drawing
// travels. A glide is a Web Animation of `translate` from where the item
// stood ("list-move", on --dur-slow --ease-out read as it starts), so it
// never touches the item's own animation or transition; the item carries
// `data-moving` while it glides. A new item fades in by its motion.css
// recipe ([data-entering]), which stands in for its own animation while
// it holds.
//
// It plays when `shape` changed (`same`, Object.is unless given) within
// one `scope` (another scope is another list: nothing moves), unless
// `still` (a load, another client's change) or `off`; never under reduced
// motion or while the list is out of sight. An item holding what matches
// `hold` (a popup placed against the window, which a moving item would
// carry off) stays still. A key typed or a paste in the list lands the
// glides at once, so what the caret opens is placed against a row at rest.
import React from "react";
import { listGlides, longestMs, reducedMotion } from "./motion.js";

// How far beyond the window's edges a box still glides, in px: past it
// nobody sees the move.
const BAND = 150;
// A change that moves more items than this, or brings more new ones than
// `most`, does that part at once (a paste of many blocks).
const MOVING = 60;
// Longer than any glide or fade: whatever an event missed ends by then.
const LONGEST_MS = 1000;

export default class ListMotion extends React.Component {
  root = React.createRef();
  gliding = new Set(); // the glides under way
  fading = new Set(); // new items fading in now
  timer = null;

  componentDidMount() {
    const root = this.root.current;
    root.addEventListener("keydown", this.onKey, true);
    root.addEventListener("paste", this.land, true);
    root.addEventListener("animationend", this.onEnd);
    root.addEventListener("animationcancel", this.onEnd);
  }

  componentWillUnmount() {
    const root = this.root.current;
    root?.removeEventListener("keydown", this.onKey, true);
    root?.removeEventListener("paste", this.land, true);
    root?.removeEventListener("animationend", this.onEnd);
    root?.removeEventListener("animationcancel", this.onEnd);
    clearTimeout(this.timer);
    this.land();
  }

  getSnapshotBeforeUpdate(prev) {
    const { scope, shape, same = Object.is, still, off } = this.props;
    if (prev.scope !== scope) { this.land(); return null; }
    if (still || off || same(prev.shape, shape) || !this.seen()) return null;
    return this.read(); // where each item is seen now, a glide under way included
  }

  componentDidUpdate(_prev, _state, before) {
    if (!before) return;
    this.settle(); // an item still gliding starts again from where it was seen
    this.play(before, this.read());
  }

  // Whether anyone can see the list move.
  seen() {
    const box = this.root.current?.parentElement;
    if (!box || reducedMotion() || document.hidden) return false;
    return box.checkVisibility ? box.checkVisibility({ visibilityProperty: true }) : true;
  }

  // The items in document order, each with its box in the window.
  read() {
    const root = this.root.current;
    const list = [], byKey = new Map();
    for (const el of root.querySelectorAll("[data-motion-key]")) {
      if (el.closest("[data-list-motion]") !== root) continue; // a list inside one of these items
      const key = el.dataset.motionKey;
      const seen = byKey.get(key);
      if (seen) { seen.twin = true; continue; } // one key twice: which is which is unknown
      const r = el.getBoundingClientRect();
      const item = { el, key, box: { x: r.left, y: r.top, h: r.height }, twin: false };
      byKey.set(key, item);
      list.push(item);
    }
    return { list, byKey };
  }

  play(before, after) {
    const root = this.root.current;
    const index = new Map(after.list.map((it, i) => [it.el, i]));
    const upOf = (el) => {
      let p = el.parentElement?.closest("[data-motion-key]");
      while (p && !index.has(p)) p = p.parentElement?.closest("[data-motion-key]");
      return p ? index.get(p) : -1;
    };
    const items = after.list.map((it) => {
      const was = before.byKey.get(it.key);
      const twin = it.twin || was?.twin;
      return { was: twin ? it.box : was?.box || null, now: it.box, up: upOf(it.el), held: twin };
    });
    if (this.props.hold) {
      for (const popup of root.querySelectorAll(this.props.hold)) {
        for (let i = upOf(popup); i >= 0; i = items[i].up) items[i].held = true;
      }
    }
    const { moves, enters } = listGlides(items, {
      top: -BAND, bottom: window.innerHeight + BAND, far: window.innerHeight,
      moving: MOVING, entering: this.props.most ?? 8,
    });
    if (!moves.length && !enters.length) return;
    // the glides are in the list's own CSS pixels: the boxes are in the
    // window's, which an interface or text zoom around the list scales
    const box = root.parentElement;
    const k = box.offsetWidth ? box.getBoundingClientRect().width / box.offsetWidth : 1;
    const tokens = getComputedStyle(document.documentElement);
    const timing = {
      duration: longestMs(tokens.getPropertyValue("--dur-slow")),
      easing: tokens.getPropertyValue("--ease-out").trim() || "ease-out",
      id: "list-move",
    };
    for (const { i, x, y } of moves) {
      const el = after.list[i].el;
      // from where it stood to its own place (a keyframe list with one end)
      const glide = el.animate([{ translate: `${x / k}px ${y / k}px`, offset: 0 }], timing);
      el.dataset.moving = "";
      const done = () => {
        if (!this.gliding.delete(glide)) return;
        if (![...this.gliding].some((g) => g.effect?.target === el)) delete el.dataset.moving;
      };
      glide.onfinish = done;
      glide.oncancel = done;
      this.gliding.add(glide);
    }
    for (const i of enters) {
      const el = after.list[i].el;
      el.dataset.entering = this.props.enter || "fade";
      this.fading.add(el);
    }
    clearTimeout(this.timer);
    this.timer = setTimeout(this.land, LONGEST_MS);
  }

  // Every glide ends where its item rests.
  settle = () => {
    for (const glide of [...this.gliding]) glide.cancel();
  };

  // Every glide and fade ends at once.
  land = () => {
    this.settle();
    for (const el of this.fading) delete el.dataset.entering;
    this.fading.clear();
  };

  onKey = (e) => {
    if ((this.gliding.size || this.fading.size) && e.key.length === 1 && !e.ctrlKey && !e.metaKey) this.land();
  };

  onEnd = (e) => {
    if (e.animationName === "list-in" && this.fading.delete(e.target)) delete e.target.dataset.entering;
  };

  render() {
    return <div ref={this.root} className="listMotion" data-list-motion="">{this.props.children}</div>;
  }
}
