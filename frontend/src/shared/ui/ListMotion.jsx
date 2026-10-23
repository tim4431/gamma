// Lists that change (docs/dev/ui-design.md, "Lists that change"): around a
// list, what moves in it glides from where it stood to where it now
// stands, what is new comes in where it will rest, and what goes leaves
// from where it stood while the rest closes up over its place. The items
// are the elements under it with a `data-motion-key`, unique in the list;
// an item may hold others (a row's children under it), and what it holds
// travels with it.
//
// It reads their boxes as React is about to change the page
// (getSnapshotBeforeUpdate runs before any DOM change of the commit) and
// again once it has, and starts each motion before the frame is painted:
// the page is laid out at once in its new order, and only the drawing
// travels. A glide is a Web Animation of `translate` from where the item
// stood ("list-move", on --dur-slow --ease-out read as it starts), so it
// never touches the item's own animation or transition; the item carries
// `data-moving` while it glides. A new item comes in by its motion.css
// recipe (`enter`: [data-entering]), which stands in for its own animation
// while it holds. One that goes leaves through a still copy (leave.js) of
// it, made while it stands, that plays `leave`'s way out ([data-leaving])
// and goes; the copy drops its key and the attributes named in `strip`
// (what app code finds the list's items by).
//
// It plays when `shape` changed (`same`, Object.is unless given; with
// neither, the `keys` the list renders, in order) within one `scope`
// (another scope is another list: nothing moves), unless `still` (a load,
// another client's change) or `off`; never under reduced motion or while
// the list is out of sight. `moves` false leaves the places alone (they
// transition already). An item holding what matches `hold` (a popup placed
// against the window, which a moving item would carry off) stays still. A
// key typed or a paste in the list lands the glides at once, so what the
// caret opens is placed against a row at rest. Boxes are the window's,
// unless `space` is "content": then the list's own scroller's (a strip
// that scrolls itself to an item as it changes).
import React from "react";
import { listGlides, longestMs, reducedMotion, sameKeys } from "./motion.js";
import { standBack, standing, stillCopy } from "./leave.js";

// How far beyond the window's edges a box still moves, in px: past it
// nobody sees the move.
const BAND = 150;
// A change that moves more items than this, or brings or takes more than
// `most`, does that part at once (a paste of many blocks, Mark all read).
const MOVING = 60;
// Longer than any glide, entry or way out: whatever an event missed ends
// by then.
const LONGEST_MS = 1000;

const shapeOf = (props) => (props.shape !== undefined ? props.shape : props.keys);
const sameOf = (props) => props.same || (props.shape === undefined && props.keys ? sameKeys : Object.is);

export default class ListMotion extends React.Component {
  root = React.createRef();
  gliding = new Set(); // the glides under way
  entering = new Set(); // new items coming in now
  copies = new Set(); // copies of items gone, on their way out
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
    const { scope, still, off } = this.props;
    if (prev.scope !== scope) { this.land(); return null; }
    if (still || off || sameOf(this.props)(shapeOf(prev), shapeOf(this.props)) || !this.seen()) return null;
    const before = this.read(); // where each item is seen now, a glide under way included
    before.leaving = this.goneCopies(prev, before);
    return before;
  }

  componentDidUpdate(_prev, _state, before) {
    if (!before) return;
    this.settle(); // an item still gliding starts again from where it was seen
    this.play(before, this.read());
    for (const { copy, at, scrolls } of before.leaving) this.standCopy(copy, at, scrolls);
    clearTimeout(this.timer);
    this.timer = setTimeout(this.land, LONGEST_MS);
  }

  // Whether anyone can see the list move.
  seen() {
    const box = this.root.current?.parentElement;
    if (!box || reducedMotion() || document.hidden) return false;
    return box.checkVisibility ? box.checkVisibility({ visibilityProperty: true }) : true;
  }

  // The list's own CSS pixels per window pixel's worth: an interface or
  // text zoom around the list scales the window's boxes, not its lengths.
  scale() {
    const box = this.root.current.parentElement;
    return box.offsetWidth ? box.getBoundingClientRect().width / box.offsetWidth : 1;
  }

  // The items in document order, each with its box (in the window, or in
  // the scroller's content).
  read() {
    const root = this.root.current;
    const list = [], byKey = new Map();
    let dx = 0, dy = 0;
    if (this.props.space === "content") {
      const k = this.scale(), box = root.parentElement;
      dx = box.scrollLeft * k;
      dy = box.scrollTop * k;
    }
    for (const el of root.querySelectorAll("[data-motion-key]")) {
      if (el.closest("[data-list-motion]") !== root) continue; // a list inside one of these items
      const key = el.dataset.motionKey;
      const seen = byKey.get(key);
      if (seen) { seen.twin = true; continue; } // one key twice: which is which is unknown
      const r = el.getBoundingClientRect();
      const item = { el, key, box: { x: r.left + dx, y: r.top + dy, h: r.height }, twin: false };
      byKey.set(key, item);
      list.push(item);
    }
    return { list, byKey };
  }

  // Copies of the items the coming change takes away, made while they
  // stand: those near the window, unless more than `most` go.
  goneCopies(prev, before) {
    const { leave, keys, strip = [] } = this.props;
    if (!leave || !prev.keys || !keys) return [];
    const staying = new Set(keys);
    const gone = prev.keys.filter((key) => !staying.has(key));
    if (!gone.length || gone.length > (this.props.most ?? 8)) return [];
    const bottom = window.innerHeight + BAND;
    const out = [];
    for (const key of gone) {
      const item = before.byKey.get(key);
      if (!item || item.twin) continue;
      const r = item.el.getBoundingClientRect();
      if (!r.width || r.bottom < -BAND || r.top > bottom) continue;
      const { copy, scrolls } = stillCopy(item.el);
      for (const name of ["data-motion-key", ...strip]) {
        copy.removeAttribute(name);
        for (const el of copy.querySelectorAll(`[${name}]`)) el.removeAttribute(name);
      }
      copy.dataset.leaving = leave;
      out.push({ copy, at: standing(item.el), scrolls });
    }
    return out;
  }

  standCopy(copy, at, scrolls) {
    if (!standBack(copy, at)) return;
    for (const [el, top, left] of scrolls) { el.scrollTop = top; el.scrollLeft = left; }
    this.copies.add(copy);
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
      moving: this.props.moves === false ? 0 : MOVING, entering: this.props.most ?? 8,
    });
    if (moves.length) {
      const k = this.scale();
      const tokens = getComputedStyle(document.documentElement);
      const timing = {
        duration: longestMs(tokens.getPropertyValue("--dur-slow")),
        easing: tokens.getPropertyValue("--ease-out").trim() || "ease-out",
        id: "list-move",
      };
      for (const { i, x, y } of moves) this.glide(after.list[i].el, x / k, y / k, timing);
    }
    const enter = this.props.enter === undefined ? "fade" : this.props.enter;
    if (enter) {
      for (const i of enters) {
        const el = after.list[i].el;
        el.dataset.entering = enter;
        this.entering.add(el);
      }
    }
  }

  // From where it stood to its own place: a keyframe list with one end
  // plays to the item's own look.
  glide(el, x, y, timing) {
    const glide = el.animate([{ translate: `${x}px ${y}px`, offset: 0 }], timing);
    el.dataset.moving = "";
    const done = () => {
      if (!this.gliding.delete(glide)) return;
      if (![...this.gliding].some((g) => g.effect?.target === el)) delete el.dataset.moving;
    };
    glide.onfinish = done;
    glide.oncancel = done;
    this.gliding.add(glide);
  }

  // Every glide ends where its item rests.
  settle = () => {
    for (const glide of [...this.gliding]) glide.cancel();
  };

  // Everything under way ends at once.
  land = () => {
    this.settle();
    for (const el of this.entering) delete el.dataset.entering;
    this.entering.clear();
    for (const copy of this.copies) copy.remove();
    this.copies.clear();
  };

  onKey = (e) => {
    if ((this.gliding.size || this.entering.size) && e.key.length === 1 && !e.ctrlKey && !e.metaKey) this.land();
  };

  onEnd = (e) => {
    const el = e.target;
    if (this.entering.delete(el)) delete el.dataset.entering;
    else if (this.copies.delete(el)) el.remove();
  };

  render() {
    return <div ref={this.root} className="listMotion" data-list-motion="">{this.props.children}</div>;
  }
}
