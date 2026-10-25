// The pictures on one page surface, a PDF page or a sheet of paper
// (docs/dev/pictures.md), under its text boxes and its ink. A picture is a
// frame placed and sized in the surface's points (percentages of the
// layer, a size container whose --tb-pt is one point, so a zoom needs no
// re-render), turned by CSS about its centre, holding the image mirrored by
// its flips and placed so its crop fills the frame (markup/picture.js
// pictureLayout): crop, then flips, then the turn. A turned element takes
// pointer events only inside its turned box, so the browser does the hit
// test. With the hand armed or the strip closed a click selects a picture
// (over a link too), and a drag of more than DRAG px moves one: a mouse's
// or a pen's any picture, a finger's only the selected one (fingers
// elsewhere scroll); a second contact makes the press a pinch. A selected
// picture has the lasso's two handles on its turned frame
// (markup/TransformHandles.jsx: a uniform resize, Alt to stretch; a turn
// that snaps to right angles, Shift to 15° steps), a menu, keys of its own
// and a crop mode (the menu's Crop or a double click); the handles and the
// crop are laid over the surface's text boxes and ink, as the lasso's
// handles are, so nothing drawn over the picture takes their presses.
// Read-only, a click shows its note. Image files dragged over an editable
// surface are taken, and dropped land at the drop point, before the app's
// own drop sees them. App owns the pictures and their edits
// (markup/usePictures.js). PictureView is one picture's drawing, which the
// notes' card (PictureCard) draws too.
import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { InkTooltips, swallowClick, useSelectionMenuAnchor } from "../ink/InkLayer";
import { canCopyImage } from "../shared/lib/copyImage.js";
import { isTextField } from "../shared/lib/hotkeys.js";
import { assetUrl } from "../shared/lib/utils.js";
import { t } from "../shared/i18n/i18n.js";
import { ContextMenu } from "../shared/ui/Menus";
import { doublePress } from "../shared/ui/press.js";
import {
  BringToFrontIcon, CheckIcon, CopyIcon, CropIcon, DuplicateIcon, FileTextIcon, FlipHIcon, FlipVIcon, MaximizeIcon, MoreIcon,
  RefreshIcon, SendToBackIcon, TrashIcon, XIcon,
} from "../shared/ui/Icons";
import * as playback from "../replay/playback.js";
import { useBlockFrame } from "../replay/useFrame.js";
import { usePageTools } from "./PageTools";
import { TransformHandles, pct } from "./TransformHandles.jsx";
import {
  captionText, cropTo, dragCrop, fitPicture, flipPicture, imageDelta, movePicture, normalizePicture, pictureLayout,
  resetPicture, scalePicture, stretchPicture, turnPicture, turnedBox, turnedCorners,
} from "./picture.js";
import { isImageFile, onlyImages } from "./pictureUpload.js";
import "./markup.css";

const DRAG = 6; // css px a press moves before it is a drag rather than a click
// A selected picture's arrow keys: 1 pt a press, 10 with Shift.
const NUDGE = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
const MAGNET = 3; // degrees from a right angle within which a turn snaps to it (markup/picture.js turnPicture)
const DUPLICATE_PX = 12; // a duplicate's offset on the screen, right and down
// The handles' names and titles.
const HANDLES = {
  resize: [t("Resize picture"), t("Drag to resize; hold Alt to stretch; arrow keys change size")],
  rotate: [t("Rotate picture"), t("Drag to rotate; it snaps to right angles, to 15° with Shift; arrow keys rotate")],
};
// Crop mode's handles: the corners and the sides of the kept rectangle.
const CROP_HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

// Where a picture's frame stands in a box of `frame` points ({x, y,
// width, height}): its place and size as percentages of the box, turned
// about its centre.
const frameStyle = (pic, frame) => ({
  left: pct(pic.x - frame.x, frame.width), top: pct(pic.y - frame.y, frame.height),
  width: pct(pic.w, frame.width), height: pct(pic.h, frame.height), transform: pic.angle ? `rotate(${pic.angle}deg)` : undefined,
});

// One picture drawn in a box of `frame` points (the surface on a page, the
// turned frame on a card): its frame, turned, and in it the image, flipped
// and placed by its crop. Other props go to the frame; `children` sit in
// the frame over the image.
export const PictureView = React.forwardRef(function PictureView({ pic, frame, alt = "", className = "", children, ...rest }, ref) {
  const layout = pictureLayout(pic);
  return (
    <div ref={ref} className={"pagePicture" + className} {...rest} style={frameStyle(pic, frame)}>
      <div className="pagePictureFlip" style={{ transform: layout.flip }}>
        <img src={assetUrl(pic.url)} alt={alt} loading="lazy" decoding="async" draggable={false} style={layout.img} />
      </div>
      {children}
    </div>
  );
});

// surface: the PDF page number or the sheet id; width / height: the surface
// in points; marks: its slice (markup/MarkupLayers.jsx).
export function PictureLayer({ surface, wrapRef, width, height, marks }) {
  const { readOnly, actions } = usePageTools();
  const live = useRef(null);
  live.current = { surface, width, height, readOnly, actions };

  // Image files dragged over an editable surface: the surface takes the
  // drop, whether or not the app around it does (an editable share's view
  // takes no files). Dropped: in the capture phase on its wrapper, so the
  // app's drop (every file a block at the end of the page) and a row's (a
  // sheet among the notes) never see them. A drag with anything else in it
  // stays the app's.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const onDragOver = (e) => {
      const L = live.current;
      if (L.readOnly || !L.width || !onlyImages(e.dataTransfer)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    };
    const onDrop = (e) => {
      const L = live.current;
      const files = [...(e.dataTransfer?.files || [])];
      if (L.readOnly || !L.width || !files.length || !files.every(isImageFile)) return;
      e.preventDefault();
      e.stopPropagation();
      el.closest(".app")?.classList.remove("dragOver"); // the app's overlay, which its own drop would take down
      const r = el.getBoundingClientRect(), k = r.width / L.width;
      L.actions.onPictureDrop(L.surface, files, { x: (e.clientX - r.left) / k, y: (e.clientY - r.top) / k },
        { width: L.width, height: L.height });
    };
    el.addEventListener("dragover", onDragOver);
    el.addEventListener("drop", onDrop, true);
    return () => {
      el.removeEventListener("dragover", onDragOver);
      el.removeEventListener("drop", onDrop, true);
    };
  }, [wrapRef]);

  if (!width || !height || !marks.pictures.length) return null;
  return (
    <div className="pictureLayer" style={{ "--tb-pt": `calc(100cqw / ${width})` }}>
      {marks.pictures.map((b) => (
        <PagePicture key={b.id} block={b} wrapRef={wrapRef} width={width} height={height} readOnly={readOnly} actions={actions}
          selected={marks.pictureSelection === b.id} cropping={marks.pictureCropping === b.id}
          flash={marks.pictureFlash?.id === b.id ? marks.pictureFlash : null} />
      ))}
    </div>
  );
}

// One picture on the page. `cropping`: its crop mode is on.
const PagePicture = React.memo(function PagePicture({ block, wrapRef, width, height, readOnly, actions, selected, cropping, flash }) {
  // Under a playing recording that covers the picture (docs/dev/recording.md):
  // shaded until the moment it was placed, and a click plays from there.
  const replayState = useBlockFrame(block.properties?.t0);
  const ref = useRef(null);
  // A press on the picture: {id, x, y, k (css px per point), el (the
  // element holding the pointer once it moved), moved, dead (a second
  // contact made it a pinch), off (its document listeners go)}.
  const press = useRef(null);
  useEffect(() => () => press.current?.off(), []);
  const [drag, setDrag] = useState(null); // the picture a move is showing
  const [handled, setHandled] = useState(null); // the picture a handle is showing
  // Whether the turn being dragged sits in the right angle's magnet, so its
  // haptic plays once as it snaps.
  const magnet = useRef(false);
  // A handle's preview; null when its drag ends, however it ends.
  const preview = (value) => {
    if (!value) magnet.current = false;
    setHandled(value);
  };
  const pic = normalizePicture(block.properties.picture);
  const surface = { width, height };
  const scale = () => wrapRef.current.getBoundingClientRect().width / width; // css px per point

  // A selected picture: a press anywhere but on it, its controls, its menu
  // and the tool strip (whose Undo is its) lets go of it; its keys (a
  // focused field or dialog keeps its own) move, delete or leave it. In
  // crop mode the crop takes the keys (PictureCrop).
  useEffect(() => {
    if (!selected) return undefined;
    const away = (e) => {
      if (ref.current?.contains(e.target) || e.target.closest?.(".pdfInkBar, .pictureMenu, .pictureHandle, .pictureCrop")) return;
      actions.onPictureDeselect();
    };
    const key = (e) => {
      if (cropping || isTextField(e.target) || e.target.closest?.(".reportOverlay") || e.ctrlKey || e.metaKey || e.altKey) return;
      const step = NUDGE[e.key], d = e.shiftKey ? 10 : 1;
      if (step) actions.onPictureChange(block.id, (p) => movePicture(p, step[0] * d, step[1] * d, surface));
      else if (e.key === "Delete" || e.key === "Backspace") actions.onPictureDelete(block.id);
      else if (e.key === "Escape") actions.onPictureDeselect();
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
  }, [selected, cropping, actions, block.id, width, height]); // eslint-disable-line react-hooks/exhaustive-deps

  // A double click (a finger's double tap) crops.
  const twice = doublePress(readOnly ? null : () => actions.onPictureCrop(block.id));
  if (!pic) return null;

  // The picture press `p`, the pointer now at `e`, makes of `b`: moved by
  // the pointer's travel.
  const moved = (p, e, b) => movePicture(b, (e.clientX - p.x) / p.k, (e.clientY - p.y) / p.k, surface);
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
  // The pointer is taken once the press moves. The press ends wherever its
  // pointer is let go, since it may leave the picture before it moves far
  // enough to take the pointer; another contact anywhere (a second finger:
  // a pinch zooms) drops it, and so does Escape once it moves: the drag is
  // taken back and the picture keeps its place and its selection (the key
  // goes no further, so the selected picture's Escape does not let go).
  const begin = (e) => {
    const p = { id: e.pointerId, x: e.clientX, y: e.clientY, k: scale(), el: null, moved: false };
    const other = (ev) => { if (ev.pointerId !== p.id) end(p, true); };
    const escape = (ev) => {
      if (ev.key !== "Escape" || !p.moved || p.dead) return;
      ev.preventDefault();
      ev.stopPropagation();
      end(p, true);
    };
    // Let go over the picture, or held by it: its own pointerup ends it.
    const up = (ev) => { if (ev.pointerId === p.id && (p.dead || !ref.current?.contains(ev.target))) end(p); };
    p.off = () => {
      document.removeEventListener("pointerdown", other, true);
      document.removeEventListener("pointerup", up, true);
      document.removeEventListener("pointercancel", up, true);
      document.removeEventListener("keydown", escape, true);
    };
    document.addEventListener("pointerdown", other, true);
    document.addEventListener("pointerup", up, true);
    document.addEventListener("pointercancel", up, true);
    document.addEventListener("keydown", escape, true);
    press.current = p;
  };
  const onPointerDown = (e) => {
    e.stopPropagation(); // the page's own drags (an area note) never start on a picture
    if (press.current) return; // a second contact: the press is a pinch now
    if (readOnly || cropping || e.button !== 0) return;
    if (e.pointerType === "touch" && !selected) return; // a finger elsewhere scrolls
    begin(e);
  };
  const onPointerMove = (e) => {
    const p = press.current;
    if (p?.id !== e.pointerId || p.dead) return;
    if (!e.buttons) { end(p); return; } // its release went unseen
    if (!p.moved) {
      if (Math.hypot(e.clientX - p.x, e.clientY - p.y) <= DRAG) return;
      p.moved = true;
      p.el = ref.current;
      try { p.el.setPointerCapture(e.pointerId); } catch { /* capture is a nicety */ }
    }
    e.preventDefault();
    setDrag(moved(p, e, pic));
  };
  const onPointerUp = (e) => {
    twice.onPointerUp?.(e);
    const p = press.current;
    if (p?.id !== e.pointerId) return;
    end(p);
    if (!p.moved || p.dead) return;
    swallowClick();
    const last = { clientX: e.clientX, clientY: e.clientY };
    actions.onPictureChange(block.id, (b) => moved(p, last, b));
    if (!selected) actions.onPictureSelect(block.id);
  };
  const cancel = (e) => { if (press.current?.id === e.pointerId) end(press.current); };
  const lost = (e) => { if (e.target === press.current?.el) cancel(e); };
  const onClick = (e) => {
    e.stopPropagation();
    if (replayState && !selected && playback.seekBefore(block.properties.t0)) return;
    if (readOnly) { actions.onJump(block.id); return; }
    if (!selected) actions.onPictureSelect(block.id);
  };
  // A right-click selects it, which opens its menu.
  const onContextMenu = (e) => {
    if (readOnly) return;
    e.preventDefault();
    e.stopPropagation();
    if (!selected) actions.onPictureSelect(block.id);
  };

  // A handle's value: a uniform scale about the centre, or with Alt the
  // frame stretched by the pointer's travel along its own axes; a turn by
  // the angle swept, which snaps (turnPicture) and ticks the iPad's haptic
  // as the right angle's magnet takes it.
  const handleValue = (raw) => {
    if (raw.mode === "resize") {
      if (!raw.alt) return scalePicture(pic, raw.scale, surface);
      const a = pic.angle * (Math.PI / 180), cos = Math.cos(a), sin = Math.sin(a);
      const along = (x, y) => [x * cos + y * sin, -x * sin + y * cos]; // the frame's own axes
      const [lx0, ly0] = along(raw.x0, raw.y0), [lx, ly] = along(raw.x, raw.y);
      return stretchPicture(pic, pic.w + 2 * (lx - lx0), pic.h + 2 * (ly - ly0), surface);
    }
    const angle = pic.angle + (raw.angle * 180) / Math.PI;
    const held = !raw.shift && Math.abs(angle - Math.round(angle / 90) * 90) <= MAGNET;
    if (held && !magnet.current) actions.onHaptic?.("snap", { x: raw.clientX, y: raw.clientY });
    magnet.current = held;
    return turnPicture(pic, angle, { shift: raw.shift, surface });
  };
  // The menu's buttons.
  const change = (fn) => actions.onPictureChange(block.id, fn);
  const act = (kind) => {
    if (kind === "crop") actions.onPictureCrop(block.id);
    else if (kind === "flipH") change((p) => flipPicture(p, "h"));
    else if (kind === "flipV") change((p) => flipPicture(p, "v"));
    else if (kind === "fit") change((p) => fitPicture(p, surface));
    else if (kind === "reset") {
      const img = ref.current?.querySelector("img");
      change((p) => resetPicture(p, img?.naturalWidth ? { w: img.naturalWidth, h: img.naturalHeight } : null, surface));
    } else if (kind === "front" || kind === "back") actions.onPictureOrder(block.id, kind);
    else if (kind === "duplicate") {
      const d = DUPLICATE_PX / scale();
      actions.onPictureDuplicate(block.id, (p) => movePicture(p, d, d, surface));
    } else if (kind === "copy") actions.onPictureCopy(block.id);
    else if (kind === "note") { actions.onPictureDeselect(); actions.onJump(block.id); }
    else if (kind === "delete") actions.onPictureDelete(block.id);
  };

  const shown = handled || drag || pic;
  const corners = turnedCorners(shown);
  const editable = selected && !readOnly && !cropping;
  // The handles and the crop sit in the surface's wrapper over its text
  // boxes and ink (markup.css), where a box or a stroke over them cannot
  // take their presses.
  const over = wrapRef.current;
  return <>
    <PictureView ref={ref} pic={shown} frame={{ x: 0, y: 0, width, height }} alt={captionText(block.content)} data-picture-id={block.id}
      className={(selected ? " selected" : "") + (cropping ? " cropping" : "") + (replayState === "pending" ? " replayPending" : "")}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={cancel}
      onLostPointerCapture={lost} onClick={onClick} onDoubleClick={twice.onDoubleClick} onContextMenu={onContextMenu}>
      {flash ? <span key={flash.nonce} className="pictureFlash" /> : null}
    </PictureView>
    {editable && over ? createPortal(
      <TransformHandles wrapRef={wrapRef} box={turnedBox(pic)} width={width} height={height}
        positions={{ resize: corners[2], rotate: corners[1] }} turn={shown.angle} labels={HANDLES} className="pictureHandle"
        valueFor={handleValue} onPreview={preview} onCommit={(next) => change(() => next)}
        onStep={(mode, sign) => change((p) => (mode === "resize" ? scalePicture(p, sign > 0 ? 1.1 : 1 / 1.1, surface)
          : turnPicture(p, p.angle + sign * 15, { surface })))} />, over,
    ) : null}
    {editable && !drag && !handled ? <PictureMenu frameRef={ref} pic={pic} onAction={act} onClose={actions.onPictureDeselect} /> : null}
    {cropping && !readOnly && over ? createPortal(
      <PictureCrop pic={pic} wrapRef={wrapRef} width={width} height={height}
        onApply={(crop) => { change((p) => cropTo(p, crop, surface)); actions.onPictureCropEnd(block.id); }}
        onCancel={() => actions.onPictureCropEnd(block.id)} />, over,
    ) : null}
  </>;
});

// Whether the menu's second row (More edits) stands open: kept for the
// session, as the handwriting menu's is.
let moreOpen = false;

// The selected picture's menu, placed and drawn like the handwriting's and
// a text box's (useSelectionMenuAnchor, InkTooltips, at most seven buttons
// a row): Crop, Flip horizontal, Flip vertical, Duplicate, Copy image (where
// the clipboard takes one), More and Delete, and in More Fit to page, Reset,
// Bring to front, Send to back and Show note. onAction(kind). A press
// outside is the picture's to judge, so the menu closes itself only on
// Escape (onClose).
function PictureMenu({ frameRef, pic, onAction, onClose }) {
  const [more, setMoreState] = useState(moreOpen);
  const setMore = (on) => { moreOpen = on; setMoreState(on); };
  const everywhere = useRef(document.body);
  const anchor = useSelectionMenuAnchor(frameRef, (r) => r, [pic.x, pic.y, pic.w, pic.h, pic.angle]);
  if (!anchor) return null;
  const button = (kind, label, Icon, title = label, className = "ctlBtn") => (
    <button type="button" className={className} aria-label={label} title={title} onClick={() => onAction(kind)}>
      <Icon aria-hidden="true" />
    </button>
  );
  return (
    <ContextMenu {...anchor} ignoreRef={everywhere} onClose={onClose} className="inkEditMenu textBoxMenu pictureMenu" role="none">
      <InkTooltips role="toolbar" aria-label={t("Edit picture")} onPointerDown={(e) => e.stopPropagation()}>
        <div className="inkEditRow">
          {button("crop", t("Crop"), CropIcon, t("Crop: drag the corners and sides; a double click crops too"))}
          {button("flipH", t("Flip horizontal"), FlipHIcon, t("Flip horizontal: mirror left to right"))}
          {button("flipV", t("Flip vertical"), FlipVIcon, t("Flip vertical: mirror top to bottom"))}
          {button("duplicate", t("Duplicate"), DuplicateIcon)}
          {canCopyImage() ? button("copy", t("Copy image"), CopyIcon, t("Copy the picture as it shows here")) : null}
          <button type="button" className={"ctlBtn" + (more ? " modeActive" : "")} aria-label={t("More")} title={t("More edits")}
            aria-expanded={more} onClick={() => setMore(!more)}><MoreIcon aria-hidden="true" /></button>
          {button("delete", t("Delete"), TrashIcon, t("Delete this picture"), "ctlBtn inkDeleteBtn")}
        </div>
        {more ? <div className="inkEditOptions inkEditMore" aria-label={t("More edits")}>
          {button("fit", t("Fit to page"), MaximizeIcon, t("Fit to page: as wide as the page, centred"))}
          {button("reset", t("Reset"), RefreshIcon, t("Reset: the whole picture, upright and unflipped, at its own proportions"))}
          {button("front", t("Bring to front"), BringToFrontIcon, t("Bring to front: over the other pictures"))}
          {button("back", t("Send to back"), SendToBackIcon, t("Send to back: under the other pictures"))}
          {button("note", t("Show note"), FileTextIcon)}
        </div> : null}
      </InkTooltips>
    </ContextMenu>
  );
}

// Crop mode: the whole image dimmed in the picture's own frame space
// (placed, flipped and turned as the picture is), the part kept bright
// with a handle at each corner and side. A drag of a handle moves those
// edges and a drag inside moves the crop, never past the image's edges
// (markup/picture.js imageDelta, dragCrop). Enter or the check applies it
// (onApply(crop): one change, the frame shrinking or growing so the part
// kept stays where it was, cropTo); Escape, the cross or a press outside
// (the picture's own rule) cancels (onCancel).
function PictureCrop({ pic, wrapRef, width, height, onApply, onCancel }) {
  const [crop, setCrop] = useState(pic.crop);
  const cropRef = useRef(crop);
  cropRef.current = crop;
  const boxRef = useRef(null);
  const drag = useRef(null); // {id, x, y, from (the crop it began at), moved, off}
  const latest = useRef(null);
  latest.current = { onApply, onCancel };
  useEffect(() => {
    const key = (e) => {
      if (isTextField(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "Enter" && !e.target.closest?.("button")) latest.current.onApply(cropRef.current);
      else if (e.key === "Escape") latest.current.onCancel();
      else return;
      e.preventDefault();
      e.stopPropagation();
    };
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("keydown", key); drag.current?.off(); };
  }, []);
  const begin = (e, handle) => {
    if (e.button !== 0 || drag.current) return;
    e.preventDefault();
    e.stopPropagation();
    const k = wrapRef.current.getBoundingClientRect().width / width;
    const d = { id: e.pointerId, x: e.clientX, y: e.clientY, from: cropRef.current, moved: false };
    const move = (ev) => {
      if (ev.pointerId !== d.id) return;
      ev.preventDefault();
      d.moved = true;
      const by = imageDelta(pic, (ev.clientX - d.x) / k, (ev.clientY - d.y) / k);
      setCrop(dragCrop(d.from, handle, by.x, by.y));
    };
    const up = (ev) => {
      if (ev.pointerId !== d.id) return;
      d.off();
      if (d.moved) swallowClick();
    };
    d.off = () => {
      window.removeEventListener("pointermove", move, true);
      window.removeEventListener("pointerup", up, true);
      window.removeEventListener("pointercancel", up, true);
      if (drag.current === d) drag.current = null;
    };
    window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", up, true);
    window.addEventListener("pointercancel", up, true);
    drag.current = d;
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* capture is a nicety */ }
  };
  const layout = pictureLayout(pic);
  const of = (v) => `${v * 100}%`;
  const src = assetUrl(pic.url);
  return <>
    <div ref={boxRef} className="pictureCrop" style={frameStyle(pic, { x: 0, y: 0, width, height })}>
      <div className="pictureCropFlip" style={{ transform: layout.flip }}>
        <div className="pictureCropImage" style={layout.img}>
          <img className="pictureCropDim" src={src} alt="" draggable={false} />
          <img className="pictureCropKept" src={src} alt="" draggable={false}
            style={{ clipPath: `inset(${of(crop.y)} ${of(1 - crop.x - crop.w)} ${of(1 - crop.y - crop.h)} ${of(crop.x)})` }} />
          <div className="pictureCropBox" style={{ left: of(crop.x), top: of(crop.y), width: of(crop.w), height: of(crop.h) }}
            aria-label={t("Part of the picture kept")} onPointerDown={(e) => begin(e, "move")}>
            {CROP_HANDLES.map((h) => <span key={h} className={`pictureCropHandle pictureCrop-${h}`} data-handle={h}
              onPointerDown={(e) => begin(e, h)} />)}
          </div>
        </div>
      </div>
    </div>
    <PictureCropBar boxRef={boxRef} crop={crop} onApply={() => onApply(crop)} onCancel={onCancel} />
  </>;
}

// The crop's own two buttons, where the menu was: apply (the check) and
// cancel (the cross).
function PictureCropBar({ boxRef, crop, onApply, onCancel }) {
  const everywhere = useRef(document.body);
  const anchor = useSelectionMenuAnchor(boxRef, (r) => r, [crop.x, crop.y, crop.w, crop.h]);
  if (!anchor) return null;
  return (
    <ContextMenu {...anchor} ignoreRef={everywhere} onClose={onCancel} className="inkEditMenu pictureMenu pictureCropBar" role="none">
      <InkTooltips role="toolbar" aria-label={t("Crop picture")} onPointerDown={(e) => e.stopPropagation()}>
        <div className="inkEditRow">
          <button type="button" className="ctlBtn" aria-label={t("Apply crop")} title={t("Apply the crop (Enter)")} onClick={onApply}>
            <CheckIcon aria-hidden="true" /></button>
          <button type="button" className="ctlBtn" aria-label={t("Cancel crop")} title={t("Cancel the crop (Esc)")} onClick={onCancel}>
            <XIcon aria-hidden="true" /></button>
        </div>
      </InkTooltips>
    </ContextMenu>
  );
}

// The largest a card in the notes draws a picture, in css px.
const CARD_WIDTH = 320, CARD_HEIGHT = 240;

// A picture block in the notes (editor/BlockTree.jsx): the picture as
// placed, cropped, flipped and turned, in its turned frame's box; a click
// shows it on its page (onJump(id)). A picture whose value does not
// normalize (no stored file named) says it is missing.
export function PictureCard({ block, onJump }) {
  const pic = normalizePicture(block.properties?.picture);
  if (!pic) return <div className="blockPictureCard blockPictureMissing">{t("Picture missing")}</div>;
  const [x0, y0, x1, y1] = turnedBox(pic);
  const box = { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
  const caption = captionText(block.content);
  return (
    <div className="blockPictureCard" role="img" aria-label={caption || t("Picture")}
      title={onJump ? t("Show it on its page") : undefined}
      style={{ width: `${Math.round(Math.min(box.width, CARD_WIDTH, (CARD_HEIGHT * box.width) / box.height))}px`,
        aspectRatio: `${box.width} / ${box.height}` }}
      onClick={onJump ? (e) => { e.stopPropagation(); onJump(block.id); } : undefined}>
      <PictureView pic={pic} frame={box} alt="" />
    </div>
  );
}
