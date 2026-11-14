// The code view's selection popup (docs/dev/documents.md "Viewers"): the
// PDF's (pdf/PdfViewer.jsx PlainTip, pdf/selectionTip.js) cut to what a
// text file takes. A selection in the text, read off the DOM selection's two
// ends (view.posAtDOM, textAnchors.js spanOf: the one source of a span),
// gets the popup: the marks (pdf/MarkTools.jsx TipMarks: swatches,
// highlighters, the fold, keys 1–9), Copy where the host's own edit menu is
// off, and Comment on this passage (shared/ui/SelectionTip.jsx, the PDF's
// popup's tools); a press makes the range
// (`onAnchor(position, quote, extras)`, App's). With a text highlighter
// armed a release of the mouse or the pen makes it at once, and its bar
// follows where the host acts on it (`onHighlightAction`); a finger's
// selection, whose handles are still moving, gets the popup. The same
// release, or a finger's selection settling, tells the chat
// (`onChatSelection(text, additive, {line, end, file})`), and a plain click
// in the text clears it, except one on a range (whose quote the click adds).
// A selection touching a line the view cut short (textLoad.js cutLinesOf)
// gets neither. While a range waits to be moved (`moveMode`, App's move
// mode) the popup offers Move here alone, as the PDF's does, and the armed
// highlighter makes nothing. No Link or Translate: those are the PDF's.
import React, { useEffect, useRef, useState } from "react";
import { lineSpotOf, spanOf } from "./textAnchors.js";
import { highlightOfEvent, inCode } from "./textMarks.js";
import { TipMarks, useMarkTools } from "../../pdf/MarkTools.jsx";
import { nearRelease, tipAnchor, tipWaiter } from "../../pdf/selectionTip.js";
import { TipFrame } from "../../shared/ui/TipFrame.jsx";
import { TipComment, TipCopy, TipDivider, TipMoveHere } from "../../shared/ui/SelectionTip.jsx";
import { tipPress } from "../../shared/ui/press.js";
import { lastPointer } from "../../shared/lib/pointer.js";
import { nativeHost, postToHost } from "../../shared/lib/host.js";

// A finger's selection is read once it has been still this long (ms).
const SETTLE_MS = 350;
const inTip = (node) => !!document.querySelector(".plainTip")?.contains(node);
// The selection let go of: the view's own as well as the document's, or the
// view draws it again as it redraws (the new range's mark) and takes the
// next press in the text for a drag of it.
function letGo(view) {
  const { main } = view.state.selection;
  if (!main.empty) view.dispatch({ selection: { anchor: main.head } });
  window.getSelection()?.removeAllRanges();
}

// `view`: the EditorView (null while none); `hash` the stem of the file it
// shows; `cutLines` its lines cut short; `file` the file's name, for the
// chat; `highlights` the page's ranges, whose look a made bar reads;
// `moveMode` a range waits to be moved here; `onAnchor`,
// `onHighlightAction`, `onChatSelection` as above, each null when not given
// (read-only: no popup).
export default function TextTip({ view, hash = "", cutLines = null, file = "", highlights = null, moveMode = false, onAnchor = null,
  onHighlightAction = null, onChatSelection = null }) {
  const mark = useMarkTools();
  const [tip, setTip] = useState(null); // {kind: "sel" | "made", position, quote, rect, point, scroll, id?}
  // the latest of what the listeners read, which subscribe once per view
  const latest = useRef(null);
  latest.current = { hash, cutLines, file, onAnchor, onHighlightAction, onChatSelection, armed: !!mark.armedId && !moveMode };
  useEffect(() => { setTip(null); }, [hash, view]);

  useEffect(() => {
    if (!view) return undefined;
    const scroller = view.scrollDOM;
    const at = () => [scroller.scrollLeft, scroller.scrollTop];
    const moved = (was) => !was || Math.abs(scroller.scrollLeft - was[0]) >= 1 || Math.abs(scroller.scrollTop - was[1]) >= 1;
    // A mouse selection's popup waits for the hand to move on from the
    // release (selectionTip.js), and opens only if nothing scrolled and the
    // selection is still there.
    const waiter = tipWaiter((next) => {
      if (moved(next.scroll)) return;
      if (next.kind !== "made" && !inCode(window.getSelection(), view.dom)) return;
      setTip(next);
    });
    const show = (next) => {
      if (next.point && lastPointer() === "mouse") waiter.wait(next);
      else setTip(next);
    };
    const keepMade = (p) => (p?.kind === "made" ? p : null);
    // `point` where a mouse was released; `released` a release of the mouse
    // or the pen ended it; `additive` Ctrl or ⌘ was held; `clears` the
    // release was a plain click in the text, off every range, which an
    // empty selection then reports to the chat.
    function sync({ point = null, released = false, additive = false, clears = false } = {}) {
      waiter.drop();
      const L = latest.current;
      const sel = window.getSelection();
      if (sel?.anchorNode && inTip(sel.anchorNode)) return;
      if (!inCode(sel, view.dom)) {
        setTip(keepMade);
        if (clears && !additive && !sel?.toString().trim()) L.onChatSelection?.("", false, null);
        return;
      }
      const range = sel.getRangeAt(0);
      let from, to;
      try {
        from = view.posAtDOM(range.startContainer, range.startOffset);
        to = view.posAtDOM(range.endContainer, range.endOffset);
      } catch {
        setTip(keepMade);
        return;
      }
      const span = spanOf(view.state, from, to, L.hash, L.cutLines);
      if (!span) { setTip(keepMade); return; }
      const lines = lineSpotOf(view.state.doc, from, to);
      L.onChatSelection?.(view.state.sliceDoc(Math.min(from, to), Math.max(from, to)), released && additive, { ...lines, file: L.file });
      if (!L.onAnchor) { setTip(null); return; }
      const r = range.getBoundingClientRect();
      const rect = { top: r.top, left: r.left, width: r.width, bottom: r.bottom };
      const snap = { kind: "sel", position: span.position, quote: span.quote, rect, point: nearRelease(point, rect), scroll: at() };
      if (released && L.armed && (!point || lastPointer() !== "touch")) {
        const id = L.onAnchor(span.position, span.quote, { highlighter: true });
        letGo(view);
        if (id && L.onHighlightAction) show({ ...snap, kind: "made", id });
        else setTip(null);
        return;
      }
      show(snap);
    }
    function onMouseDown(e) {
      waiter.press();
      if ((e.button === 0 || e.button === 2) && !inTip(e.target)) setTip(null);
    }
    function onMouseMove(e) { if (!e.buttons) waiter.move(e.clientX, e.clientY); }
    function onMouseUp(e) {
      if (e.button !== 0 || inTip(e.target)) return;
      const here = scroller.contains(e.target) && !e.target.closest?.(".docTextOverlay");
      const opts = {
        point: { x: e.clientX, y: e.clientY }, released: true, additive: e.ctrlKey || e.metaKey,
        clears: here && !highlightOfEvent(view, e),
      };
      setTimeout(() => sync(opts), 10);
    }
    // The host's own edit menu (the iPad's) covers the popup: off while the
    // selection is the code's, as over the PDF's text; the popup carries
    // Copy in its place.
    const hostMenu = Boolean(nativeHost()?.editMenu);
    let menuOff = false;
    function syncHostMenu() {
      const off = inCode(window.getSelection(), view.dom);
      if (!hostMenu || off === menuOff) return;
      menuOff = off;
      postToHost({ type: "editMenu", shown: !off }).catch(() => {});
    }
    // A finger's selection sends no mouseup: read once it settles, and again
    // where it stands after a touch scroll.
    let timer = null;
    const settle = () => { clearTimeout(timer); timer = setTimeout(() => sync(), SETTLE_MS); };
    function onSelectionChange() {
      syncHostMenu();
      if (lastPointer() !== "mouse") settle();
    }
    function onScroll() {
      setTip((p) => (p && moved(p.scroll) ? null : p));
      if (lastPointer() !== "mouse" && inCode(window.getSelection(), view.dom)) settle();
    }
    document.addEventListener("mousedown", onMouseDown, true);
    document.addEventListener("mousemove", onMouseMove, { passive: true });
    document.addEventListener("mouseup", onMouseUp);
    document.addEventListener("selectionchange", onSelectionChange);
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      clearTimeout(timer);
      document.removeEventListener("mousedown", onMouseDown, true);
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
      document.removeEventListener("selectionchange", onSelectionChange);
      scroller.removeEventListener("scroll", onScroll);
      if (menuOff) postToHost({ type: "editMenu", shown: true }).catch(() => {});
    };
  }, [view]);

  if (!tip || !onAnchor) return null;
  return (
    <TipFrame anchor={tipAnchor(tip)}>
      <TipRow key={tip.kind === "made" ? `made:${tip.id}` : `${tip.position.line}:${tip.position.col}:${tip.quote}`} tip={tip}
        highlights={highlights} moveMode={moveMode && tip.kind !== "made"} onAnchor={onAnchor} onHighlightAction={onHighlightAction}
        copy={nativeHost()?.editMenu && tip.kind !== "made" ? tip.quote : null}
        onDone={(clear) => { if (clear) letGo(view); setTip(null); }} />
    </TipFrame>
  );
}

// The popup's row: on a selection, a press makes its range (a swatch or a
// highlighter its look, Comment one with its thread's field open); on the
// bar of a range the highlighter made (`tip.kind` "made"), the row changes
// that range, as the PDF's bar does; in `moveMode` its one button moves the
// waiting range here. `onDone(clear)` puts it away, the selection with it
// when `clear`.
function TipRow({ tip, highlights, moveMode, onAnchor, onHighlightAction, copy, onDone }) {
  const made = tip.kind === "made" ? tip.id : null;
  const own = made ? (highlights || []).find((h) => h.id === made) : null;
  const look = own ? { style: own.style || "highlight", color: own.color } : null;
  const anchor = (extras) => { onDone(true); onAnchor(tip.position, tip.quote, { commentText: "", ...extras }); };
  const onMade = (action, value) => { onDone(false); onHighlightAction?.(made, action, value); };
  if (moveMode) {
    return (
      <div className="plainTip docTextTip">
        <TipMoveHere onMove={() => { onDone(true); onAnchor(tip.position, tip.quote, {}); }} />
      </div>
    );
  }
  // Every button acts on the press, which keeps the selection, as the PDF's
  // popup's do (shared/ui/press.js tipPress).
  return (
    <div className="plainTip docTextTip">
      <TipMarks made={!!made} own={look} act={tipPress} onLeave={() => onDone(!made)}
        onMark={({ color, style }) => (made ? onMade("mark", { color, style }) : anchor({ color, style }))}
        onStyle={made ? (style) => onHighlightAction?.(made, "style", style) : null}
        onSave={made && look ? () => onMade("save", look) : null} onRemove={made ? () => onMade("remove") : null}
        tools={<>
          <TipDivider />
          {copy ? <TipCopy text={copy} /> : null}
          <TipComment onComment={(color) => (made ? onMade("comment") : anchor({ color, comment: true }))} />
        </>} />
    </div>
  );
}
