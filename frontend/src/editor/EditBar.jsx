// The editing bar (docs/dev/ipad.md "The editing bar"): on a touch screen,
// while a note's editor is open, a strip pinned above the on-screen keyboard
// with what a hardware keyboard does by keys — the insert menu, outdent and
// indent, moving the block, a new block, bold, italic, inline code,
// strikethrough, link, inline math, Tab's hop in a math snippet, undo and
// redo, and Done. Each tool is a catalog command (app/commands.js).
// A block command runs with the context the row's keydown dispatches with,
// so a button, a key and the palette never disagree. Undo and redo run the
// page's block history (App's undoBlocks).
//
// The keyboard stays up only while focus stays in the editor, so a press
// never takes it (pointerdown and mousedown are cancelled), and a command
// that remounts the row (indent and outdent re-parent it) renders inside
// the tap (flushSync): iPadOS shows the keyboard for a focus() made during
// the gesture, not for one made a frame later.
import React, { useLayoutEffect, useRef, useState } from "react";
import { createPortal, flushSync } from "react-dom";
import { EditorView } from "@codemirror/view";
import { commandById } from "../app/commands.js";
import { commandIcon } from "../app/commandIcons.jsx";
import { t } from "../shared/i18n/i18n.js";
import { CheckIcon } from "../shared/ui/Icons";
import { touchTyping } from "../shared/lib/pointer.js";

// The bar's groups. `keep`: the caret stays in this block's editor, put back
// after a remount; a new block and undo move it themselves.
const GROUPS = [
  [["block.insertMenu", true]],
  [["block.outdent", true], ["block.indent", true], ["block.moveUp", true], ["block.moveDown", true], ["block.newBelow", false]],
  [["block.bold", true], ["block.italic", true], ["block.code", true], ["block.strike", true], ["block.link", true],
    ["block.math", true], ["block.nextSlot", true]],
  [["app.undo", false], ["app.redo", false]],
];

// Tools that show only while their command applies; the others grey out.
const TRANSIENT = new Set(["block.nextSlot"]);

// The room the bar takes at the bottom of the visual viewport while it
// shows, else 0: the editor's scroll margin keeps the caret above it, and
// caret-anchored popups stay clear of it.
let barSpace = 0;
export const editBarSpace = () => barSpace;

// The gap between the bar and what it sits on, as the tool strip floats
// over the page.
const GAP = 6;

// Where the bar goes: `top` in the layout viewport's px, what `position:
// fixed` measures from. It sits on the bottom of the visual viewport, which
// is the keyboard's top (iPadOS does not shrink the layout viewport for its
// keyboard), and never lower than the compact shell's bottom bar, which
// stays reachable with no keyboard up (an editor a new page opened by
// itself, a hardware keyboard). `edge`: on the screen's edge, clear of the
// home indicator.
function measure(el) {
  const vv = window.visualViewport;
  const bottom = vv ? vv.offsetTop + vv.height : window.innerHeight;
  const tabs = document.querySelector(".phoneBottomBar")?.getBoundingClientRect();
  const floor = tabs?.height ? Math.min(bottom, tabs.top) : bottom;
  const top = Math.round(floor - el.offsetHeight - GAP);
  barSpace = Math.max(0, bottom - top);
  return { top, edge: !tabs?.height && bottom >= window.innerHeight - 1 };
}

// iPadOS moves the visual viewport itself: the keyboard slides in and out,
// and a scroll pans the view under the keyboard. Safari draws that move at
// once, a script only a frame later, so a bar that chased it would shake.
// Instead the bar steps aside (`shown` false: invisible, not pressable)
// while its place keeps changing, and comes back once it has been still for
// SETTLE_MS. Opening an editor counts as a move for FIRST_MS, the keyboard's
// rise, so the bar appears with the keyboard rather than under it. The
// events do not always come at the end of a move (and none come when the
// keyboard returns to an editor that kept the focus), so every event, focus
// change and tap watches the place for a moment, a frame at a time.
const FOLLOW_MS = 1000;
const SETTLE_MS = 150;
const FIRST_MS = 350;
function useBarPlace(ref) {
  const [place, setPlace] = useState(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    let last = measure(el);
    let settled = performance.now() + FIRST_MS;
    let frame = 0, until = 0;
    const show = (shown) => setPlace((p) => (p && p.shown === shown && p.top === last.top && p.edge === last.edge
      ? p : { ...last, shown }));
    const step = () => {
      const now = performance.now();
      const next = measure(el);
      if (next.top !== last.top || next.edge !== last.edge) {
        last = next;
        settled = now + SETTLE_MS;
      }
      const shown = now >= settled;
      show(shown);
      frame = now < until || !shown ? requestAnimationFrame(step) : 0;
    };
    const follow = () => {
      until = performance.now() + FOLLOW_MS;
      if (!frame) frame = requestAnimationFrame(step);
    };
    show(false);
    follow();
    const vv = window.visualViewport;
    const targets = [[vv, "resize"], [vv, "scroll"], [window, "resize"], [window, "scroll"],
      [document, "focusin"], [document, "focusout"], [document, "touchend"]];
    for (const [target, type] of targets) target?.addEventListener(type, follow, { passive: true });
    return () => {
      for (const [target, type] of targets) target?.removeEventListener(type, follow);
      cancelAnimationFrame(frame);
      barSpace = 0;
    };
  }, [ref]);
  return place;
}

// The block's editor after a command: the same view, or the one its
// remounted row opened. A remount lost the selection, so it goes back.
function keepCaret(id, before, sel) {
  const host = document.querySelector(`.blockEditorCm[data-block-id="${CSS.escape(id)}"] .cm-editor`);
  const view = host && EditorView.findFromDOM(host);
  if (!view) return;
  if (view !== before && sel) {
    const len = view.state.doc.length;
    view.dispatch({ selection: { anchor: Math.min(sel.anchor, len), head: Math.min(sel.head, len) } });
  }
  if (!view.hasFocus) view.focus();
}

const hold = (e) => e.preventDefault();

// `context()`: the row's command context, built fresh for each press;
// `onUndo(redo)`: the page's block history.
export default function EditBar({ context, onUndo }) {
  const [shown] = useState(touchTyping);
  const ref = useRef(null);
  const place = useBarPlace(ref);
  if (!shown) return null;

  const ctx = context();
  function press(id, keep) {
    const c = context();
    const view = c.editor?.view;
    const sel = view?.state.selection.main;
    flushSync(() => {
      if (id === "app.undo" || id === "app.redo") onUndo?.(id === "app.redo");
      else commandById(id).run(c);
    });
    if (keep) keepCaret(c.block.id, view, sel);
  }
  function done() {
    context().editor?.view?.contentDOM.blur();
  }
  const label = (id) => commandById(id).label;
  const enabled = (id) => {
    const cmd = commandById(id);
    return !cmd.when || cmd.when(ctx);
  };

  // Measured before the first paint; out of sight while its place moves.
  return createPortal(
    <div ref={ref} className={`editBar${place?.edge ? " atEdge" : ""}${place?.shown ? "" : " moving"}`}
      style={place ? { top: place.top } : undefined}
      role="toolbar" aria-label={t("Editing tools")} onPointerDown={hold} onMouseDown={hold}>
      <div className="editBarTools">
        {GROUPS.map((group, i) => (
          <React.Fragment key={i}>
            {i ? <span className="pdfInkSep" /> : null}
            {group.map(([id, keep]) => {
              const on = enabled(id);
              if (!on && TRANSIENT.has(id)) return null;
              const Icon = commandIcon({ id });
              return (
                <button key={id} type="button" tabIndex={-1} className="ctlBtn"
                  data-command={id} title={label(id)} aria-label={label(id)} disabled={!on}
                  onClick={() => press(id, keep)}>
                  <Icon size={16} />
                </button>
              );
            })}
          </React.Fragment>
        ))}
      </div>
      <span className="pdfInkSep" />
      <button type="button" tabIndex={-1} className="ctlBtn" title={t("Done")} aria-label={t("Done")} onClick={done}>
        <CheckIcon size={16} />
      </button>
    </div>,
    document.body,
  );
}
