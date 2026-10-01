// The text boxes on one page surface, a PDF page or a sheet of paper
// (docs/dev/text_boxes.md), under its ink: each box's text as rendered
// Markdown in the box's font, size and colour, or the block editor while it
// is edited. A box is placed and sized in the surface's points, which the
// layer resolves as a size container (--tb-pt, markup.css), so a zoom
// needs no re-render; its frame, handle and menu keep their screen size.
// With the Text tool armed the surface is a place to type: a press is
// claimed before anything under it (links, highlights, the text layer) sees
// it, as InkLayer claims a stroke; a tap makes a box whose first line is
// under the pointer, a drag one as wide as the drag, and a tap on a box
// opens its editor there. Without the tool a click selects a box and a
// click on the selected box edits it. A selected box has a width handle,
// a menu and keys of its own. A drag moves a box: a mouse's or a pen's any
// box, a finger's only the selected one (fingers elsewhere scroll), and
// while a box is edited, the band around its frame. Read-only, a click
// shows its note. App owns the boxes and their edits
// (markup/useTextBoxes.js); after an edit here the layer reports the size
// the box rendered at.
import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { BlockCmEditor } from "../editor/BlockCmEditor";
import { BlockMarkdown } from "../editor/BlockTree";
import { sourceOffsetAtPoint } from "../editor/clickToSource";
import { PEN_COLORS } from "../ink/ink";
import { ColorChoices, InkTooltips, swallowClick, useSelectionMenuAnchor } from "../ink/InkLayer";
import { isTextField } from "../shared/lib/hotkeys.js";
import { t } from "../shared/i18n/i18n.js";
import { ContextMenu } from "../shared/ui/Menus";
import { CopyIcon, FileTextIcon, PaintBucketIcon, PaletteIcon, TextSizeIcon, TrashIcon } from "../shared/ui/Icons";
import { usePageTools } from "./PageTools";
import { TEXT_BACKGROUNDS, TEXT_SIZES, moveBox, normalizeTextBox, placeBox, resizeBox } from "./textBox.js";
import "./markup.css";

const DRAG = 6; // css px a press moves before it is a drag rather than a tap
const NO_LABELS = {}; // a box resolves no [[ref]] chips: they read as typed
// A selected box's arrow keys: 1 pt a press, 10 with Shift.
const NUDGE = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
// TEXT_BACKGROUNDS in words: none, white, note yellow, light blue.
const BACKGROUND_LABELS = [t("No background"), t("White background"), t("Yellow background"), t("Light blue background")];

// A box's place as percentages of its surface.
const frame = (box, width, height) => ({ left: `${box.x / width * 100}%`, top: `${box.y / height * 100}%` });

// surface: the PDF page number or the sheet id; width / height: the surface
// in points; marks: its slice (markup/MarkupLayers.jsx).
export function TextBoxLayer({ surface, wrapRef, width, height, marks }) {
  const { readOnly, text, actions } = usePageTools();
  const [ghost, setGhost] = useState(null); // the box a drag is making
  const live = useRef(null);
  live.current = { surface, width, height, armed: text.armed, style: text.style, actions };

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    let press = null; // {id, x, y, rect, k (css px per pt), start (pt), drag}
    const toPt = (e) => ({ x: (e.clientX - press.rect.left) / press.k, y: (e.clientY - press.rect.top) / press.k });
    const boxAt = (e) => placeBox(live.current, press.start, press.drag ? toPt(e) : null, live.current.style);
    const end = () => { press = null; setGhost(null); };
    const onDown = (e) => {
      const L = live.current;
      if (!L.armed || !L.width) return;
      if (press) { end(); return; } // a second finger: a pinch, not a box
      if (e.button !== 0 || e.target.closest?.(".textBox")) return;
      e.preventDefault();
      e.stopPropagation();
      // The box being typed in is left at the press, an edit before the new box exists.
      L.actions.onBoxDeselect();
      const rect = el.getBoundingClientRect();
      press = { id: e.pointerId, x: e.clientX, y: e.clientY, rect, k: rect.width / L.width, drag: false };
      press.start = toPt(e);
      try { el.setPointerCapture(e.pointerId); } catch { /* capture is a nicety */ }
    };
    const onMove = (e) => {
      if (e.pointerId !== press?.id) return;
      if (!press.drag && Math.hypot(e.clientX - press.x, e.clientY - press.y) <= DRAG) return;
      press.drag = true;
      setGhost(boxAt(e));
    };
    const onUp = (e) => {
      if (e.pointerId !== press?.id) return;
      const box = boxAt(e);
      end();
      swallowClick();
      live.current.actions.onBoxCreate(live.current.surface, box);
    };
    const onCancel = (e) => { if (e.pointerId === press?.id) end(); };
    el.addEventListener("pointerdown", onDown, true);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onCancel);
    el.addEventListener("lostpointercapture", onCancel);
    return () => {
      el.removeEventListener("pointerdown", onDown, true);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", onCancel);
      el.removeEventListener("lostpointercapture", onCancel);
    };
  }, [wrapRef]);

  if (!width || !height || (!marks.boxes.length && !ghost)) return null;
  return (
    <div className="textBoxLayer" style={{ "--tb-pt": `calc(100cqw / ${width})` }}>
      {marks.boxes.map((b) => (
        <TextBox key={b.id} block={b} width={width} height={height} readOnly={readOnly} armed={text.armed} actions={actions}
          selected={marks.boxSelection === b.id} editing={marks.boxEditing?.id === b.id ? marks.boxEditing : null}
          flash={marks.boxFlash?.id === b.id ? marks.boxFlash : null} />
      ))}
      {ghost ? <div className="textBoxGhost" style={{ ...frame(ghost, width, height), width: `${ghost.w / width * 100}%`,
        height: `${ghost.h / height * 100}%` }} /> : null}
    </div>
  );
}

// One box. `editing` is {id, at} while its editor is open (`at`: the tap
// that opened it, for the caret).
const TextBox = React.memo(function TextBox({ block, width, height, readOnly, armed, actions, selected, editing, flash }) {
  const ref = useRef(null);
  const editorRef = useRef(null);
  // A press on the box, its band or its width handle: {id, mode ("move" |
  // "width"), x, y, shown (its size as drawn), el (the element holding the
  // pointer), moved, dead (a second contact made it a pinch), off (its
  // document listeners go)}; `drag` is the box it is showing once it moves.
  const press = useRef(null);
  useEffect(() => () => press.current?.off(), []);
  const [drag, setDrag] = useState(null);
  const box = normalizeTextBox(block.properties.text_box);
  const surface = { width, height };

  // After an edit here changed the box or its text: the size it rendered at.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !actions.wantsMeasure(block.id)) return;
    const k = el.parentElement.getBoundingClientRect().width / width;
    const r = el.getBoundingClientRect();
    if (k > 0) actions.onBoxMeasured(block.id, { w: r.width / k, h: r.height / k }, width);
  }, [block.content, block.properties, actions, width]);

  // The box's size as drawn, in points (the text decides it: the stored
  // size may be stale), and `k`, css px per point.
  const shown = () => {
    const r = ref.current.getBoundingClientRect(), k = ref.current.parentElement.getBoundingClientRect().width / width;
    return { w: r.width / k, h: r.height / k, k };
  };

  // A selected box: a press anywhere else but its menu and the tool strip
  // (whose Text options restyle it) lets go of it; its keys (a focused
  // field or dialog keeps its own) move, delete, open or leave it.
  useEffect(() => {
    if (!selected) return undefined;
    const away = (e) => { if (!ref.current?.contains(e.target) && !e.target.closest?.(".pdfInkBar, .textBoxMenu")) actions.onBoxDeselect(); };
    const key = (e) => {
      if (isTextField(e.target) || e.target.closest?.(".reportOverlay") || e.ctrlKey || e.metaKey || e.altKey) return;
      const step = NUDGE[e.key], d = e.shiftKey ? 10 : 1;
      if (step) {
        const s = shown();
        actions.onBoxChange(block.id, (b) => moveBox(b, step[0] * d, step[1] * d, surface, s));
      } else if (e.key === "Delete" || e.key === "Backspace") actions.onBoxDelete(block.id);
      else if (e.key === "F2" || (e.key === "Enter" && !e.target.closest?.("button"))) actions.onBoxOpen(block.id);
      else if (e.key === "Escape") actions.onBoxDeselect();
      else return;
      e.preventDefault();
      e.stopPropagation();
    };
    document.addEventListener("pointerdown", away, true);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", away, true);
      document.removeEventListener("keydown", key);
    };
  }, [selected, actions, block.id, width, height]); // eslint-disable-line react-hooks/exhaustive-deps

  // The box press `p`, the pointer now at `e`, makes of `b`: moved by the
  // pointer's travel, or for the handle as wide as it was plus that travel.
  const dragged = (p, e, b) => {
    const s = p.shown, dx = (e.clientX - p.x) / s.k, dy = (e.clientY - p.y) / s.k;
    return p.mode === "width" ? resizeBox(b, s.w + dx, surface) : moveBox(b, dx, dy, surface, s);
  };
  // The press ends, nothing moved; `dead`: a second contact made it a
  // pinch, which it stays until its pointer lifts.
  const end = (p, dead = false) => {
    if (dead) {
      p.dead = true;
      if (p.el?.hasPointerCapture?.(p.id)) p.el.releasePointerCapture(p.id);
    } else {
      p.off();
      if (press.current === p) press.current = null;
    }
    setDrag(null);
  };
  // `now`: the band and the handle take the pointer at once; a press on
  // the text only once it moves, so a Ctrl+click still finds its link. The
  // press ends wherever its pointer is let go, since it may leave the box
  // before it moves far enough to take the pointer; another contact
  // anywhere (a second finger: a pinch zooms) drops it.
  const begin = (e, mode, now) => {
    const p = { id: e.pointerId, mode, x: e.clientX, y: e.clientY, shown: shown(), el: now ? e.currentTarget : null, moved: false };
    const other = (ev) => { if (ev.pointerId !== p.id) end(p, true); };
    // Let go over the box, or held by it: the box's own pointerup ends it.
    const up = (ev) => { if (ev.pointerId === p.id && (p.dead || !ref.current?.contains(ev.target))) end(p); };
    p.off = () => {
      document.removeEventListener("pointerdown", other, true);
      document.removeEventListener("pointerup", up, true);
      document.removeEventListener("pointercancel", up, true);
    };
    document.addEventListener("pointerdown", other, true);
    document.addEventListener("pointerup", up, true);
    document.addEventListener("pointercancel", up, true);
    press.current = p;
    if (now) e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerDown = (e) => {
    e.stopPropagation(); // the page's own drags (an area note) never start on a box
    if (press.current) return; // a second contact: the press is a pinch now
    if (readOnly || editing || e.button !== 0) return;
    if (e.pointerType === "touch" && !selected && !armed) return; // a finger elsewhere scrolls
    begin(e, "move", false);
  };
  const onPointerMove = (e) => {
    const p = press.current;
    if (p?.id !== e.pointerId || p.dead) return;
    if (!e.buttons) { end(p); return; } // its release went unseen
    if (!p.moved) {
      if (Math.hypot(e.clientX - p.x, e.clientY - p.y) <= DRAG) return;
      p.moved = true;
      if (!p.el) {
        p.el = ref.current;
        try { p.el.setPointerCapture(e.pointerId); } catch { /* capture is a nicety */ }
      }
    }
    e.preventDefault();
    setDrag(dragged(p, e, box));
  };
  const onPointerUp = (e) => {
    const p = press.current;
    if (p?.id !== e.pointerId) return;
    end(p);
    if (!p.moved || p.dead) return;
    swallowClick();
    const last = { clientX: e.clientX, clientY: e.clientY };
    actions.onBoxChange(block.id, (b) => dragged(p, last, b));
    if (!selected && !editing) actions.onBoxSelect(block.id);
  };
  const cancel = (e) => { if (press.current?.id === e.pointerId) end(press.current); };
  // Only the element the press holds the pointer by counts: taking it from
  // the text a finger landed on (touch captures there) is no loss.
  const lost = (e) => { if (e.target === press.current?.el) cancel(e); };

  // In the capture phase, so a plain click on a link in the text is the
  // box's (Ctrl or Cmd follows it). Clicks inside the menu, portalled but
  // still the box's in React, and on the handle are theirs.
  const onClick = (e) => {
    if (editing || !ref.current.contains(e.target) || e.target.closest?.(".textBoxWidth")
      || ((e.ctrlKey || e.metaKey) && e.target.closest?.("a"))) return;
    e.preventDefault();
    e.stopPropagation();
    if (readOnly) { actions.onJump(block.id); return; }
    if (!armed && !selected) { actions.onBoxSelect(block.id); return; }
    const rendered = ref.current.querySelector(".textBoxText");
    const offset = rendered ? sourceOffsetAtPoint(rendered, block.content || "", e.clientX, e.clientY) : null;
    actions.onBoxOpen(block.id, { x: e.clientX, y: e.clientY, offset });
  };

  const at = drag || box;
  const keepFocus = (e) => e.preventDefault(); // a press on the band or the handle leaves the editor open
  return (
    <div ref={ref} data-box-id={block.id}
      className={"textBox" + (at.auto ? " auto" : "") + (selected ? " selected" : "") + (editing ? " editing" : "")}
      style={{
        ...frame(at, width, height),
        ...(at.auto ? { maxWidth: `${(width - at.x) / width * 100}%` } : { width: `${at.w / width * 100}%` }),
        "--tb-size": box.size,
      }}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
      onPointerCancel={cancel} onLostPointerCapture={lost} onClickCapture={onClick}>
      <div className="textBoxBody" style={{ color: box.color, background: box.bg || undefined }}>
        {editing ? (
          <BlockCmEditor ref={editorRef} autoFocus className="blockEditorCm textBoxEditor" value={block.content || ""}
            clickPos={editing.at} pinScroll refLabels={NO_LABELS}
            onChange={(e) => actions.onBoxText(block.id, e.target.value)}
            onBlur={() => actions.onBoxLeave(block.id, false)}
            onKeyDown={(e) => {
              if (e.key !== "Escape") { actions.onBoxKey(e, editorRef.current); return; }
              e.preventDefault();
              e.stopPropagation();
              actions.onBoxLeave(block.id, true);
            }} />
        ) : (
          <div className="textBoxText">
            <BlockMarkdown content={block.content || ""} blockId={`textbox:${block.id}`} refLabels={NO_LABELS} />
          </div>
        )}
      </div>
      {editing && !readOnly ? (
        <span className="textBoxBand" aria-hidden="true" onMouseDown={keepFocus}
          onPointerDown={(e) => { e.stopPropagation(); if (e.button === 0) begin(e, "move", true); }} />
      ) : null}
      {(selected || editing) && !readOnly ? (
        <button type="button" className="textBoxWidth" aria-label={t("Text box width")}
          title={t("Drag to set the width; arrow keys change it")} onMouseDown={keepFocus}
          onPointerDown={(e) => { e.stopPropagation(); if (e.button === 0) begin(e, "width", true); }}
          onKeyDown={(e) => {
            const step = { ArrowLeft: -1, ArrowRight: 1 }[e.key];
            if (!step) return;
            e.preventDefault();
            e.stopPropagation();
            const s = shown();
            actions.onBoxChange(block.id, (b) => resizeBox(b, s.w + step * (e.shiftKey ? 10 : 1), surface));
          }} />
      ) : null}
      {flash ? <span key={flash.nonce} className="textBoxFlash" /> : null}
      {selected && !readOnly && !drag ? (
        <TextBoxMenu boxRef={ref} box={box}
          onStyle={(patch) => actions.onBoxChange(block.id, (b) => normalizeTextBox({ ...b, ...patch }))}
          onDuplicate={() => {
            const s = shown(), d = 12 / s.k; // 12 screen pixels
            actions.onBoxDuplicate(block.id, (b) => moveBox(b, d, d, surface, s));
          }}
          onShowNote={() => { actions.onBoxDeselect(); actions.onJump(block.id); }}
          onDelete={() => actions.onBoxDelete(block.id)} onClose={actions.onBoxDeselect} />
      ) : null}
    </div>
  );
});

// The selected box's menu, placed like the handwriting's (InkSelectionMenu):
// its size, colour and background, Duplicate, Show note and Delete. A press
// outside is the box's to judge (it lets go, but not for the tool strip,
// whose Text options restyle it), so the menu closes itself only on Escape.
function TextBoxMenu({ boxRef, box, onStyle, onDuplicate, onShowNote, onDelete, onClose }) {
  const [options, setOptions] = useState(null);
  const everywhere = useRef(document.body);
  const { anchor, contentRef } = useSelectionMenuAnchor(boxRef, (r) => r, [box.x, box.y]);
  if (!anchor) return null;
  const choice = (kind, label, Icon) => (
    <button type="button" className={"ctlBtn" + (options === kind ? " modeActive" : "")} aria-label={label} title={label}
      aria-expanded={options === kind} onClick={() => setOptions(options === kind ? null : kind)}><Icon aria-hidden="true" /></button>
  );
  return (
    <ContextMenu x={anchor.x} y={anchor.y} ignoreRef={everywhere} onClose={onClose} className="inkEditMenu textBoxMenu">
      <InkTooltips contentRef={contentRef} role="toolbar" aria-label={t("Edit text box")} onPointerDown={(e) => e.stopPropagation()}>
        <div className="inkEditRow">
          {choice("size", t("Size"), TextSizeIcon)}
          {choice("color", t("Color"), PaletteIcon)}
          {choice("bg", t("Background"), PaintBucketIcon)}
          <button type="button" className="ctlBtn" aria-label={t("Duplicate")} title={t("Duplicate")} onClick={onDuplicate}><CopyIcon aria-hidden="true" /></button>
          <button type="button" className="ctlBtn" aria-label={t("Show note")} title={t("Show note")} onClick={onShowNote}><FileTextIcon aria-hidden="true" /></button>
          <button type="button" className="ctlBtn inkDeleteBtn" aria-label={t("Delete")} title={t("Delete this text box")} onClick={onDelete}><TrashIcon aria-hidden="true" /></button>
        </div>
        {options ? <div className="inkEditOptions"><TextStyleChoices kind={options} style={box} onStyle={onStyle} /></div> : null}
      </InkTooltips>
    </ContextMenu>
  );
}

// One part of a text box's style to choose, `kind` "size", "color" (the pen
// palette, named as ink's colours are, plus a custom colour) or "bg"
// (TEXT_BACKGROUNDS): the Text tool's options row shows all three, the box
// menu one at a time. `style` {size, color, bg}; onStyle(patch).
export function TextStyleChoices({ kind, style, onStyle }) {
  if (kind === "size") {
    return TEXT_SIZES.map((sz) => (
      <button key={sz} type="button" className={"ctlBtn inkTextSize" + (style.size === sz ? " modeActive" : "")}
        onClick={() => onStyle({ size: sz })} title={t("{sz} pt text", { sz })} aria-label={t("Text size {sz} pt", { sz })}
        aria-pressed={style.size === sz}>{sz}</button>
    ));
  }
  if (kind === "color") return <ColorChoices colors={PEN_COLORS} color={style.color} onColor={(color) => onStyle({ color })} />;
  return TEXT_BACKGROUNDS.map((bg, i) => (
    <button key={bg || "none"} type="button" className={"colorBtn inkSwatch" + (bg ? "" : " textNoBackground")
      + (style.bg === bg ? " selected" : "")} style={bg ? { background: bg } : undefined}
      onClick={() => onStyle({ bg })} title={BACKGROUND_LABELS[i]} aria-label={BACKGROUND_LABELS[i]} aria-pressed={style.bg === bg} />
  ));
}
