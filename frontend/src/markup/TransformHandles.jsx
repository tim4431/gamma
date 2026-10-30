// The resize and rotate handles of a selection on a page surface: the
// lasso's (ink/InkLayer.jsx, docs/dev/handwriting.md "Client") and a
// picture's (markup/PictureLayer.jsx, docs/dev/pictures.md). Two 28 px
// buttons laid over the surface at `positions` (its points), so they keep
// their screen size at any zoom and follow the live preview: the
// bottom-right one resizes about the selection's centre, the top-right one
// turns it. `turn` (degrees) turns both with a turned frame, each about the
// corner it stands at. A drag previews (onPreview(value), null when it
// ends) and its release commits (onCommit(value)); Escape takes the drag
// back and only the drag, as does a blur, and handles that go mid-drag (the
// selection let go of) end its preview too. A focused handle's arrow keys
// commit a step each. The lasso takes the value the handles make,
// {cx, cy, angle (radians), scale}: Shift snaps the turn to 15°, the scale
// stays within 0.1 … maxScale, an arrow key scales by 1.1 or turns by 15°.
// A picture reads the press itself (`valueFor(raw)`: its uniform scale, Alt
// to stretch and its own snaps, markup/picture.js) and steps on the arrow
// keys itself (`onStep(mode, sign)`).
import { useEffect, useRef } from "react";
import { RedoIcon, ResizeIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

// A length as a percentage of `of`: the controls laid over a page and its
// canvases are placed in the page's percentages, so they follow it through
// the viewer's zoom. A focused control's arrow key's sign (right and up are
// 1, left and down -1).
export const pct = (v, of) => `${(v / of) * 100}%`;
export const arrowSign = (key) => (key === "ArrowRight" || key === "ArrowUp" ? 1 : key === "ArrowLeft" || key === "ArrowDown" ? -1 : 0);

// Each handle's name and title: the lasso's.
const HANDWRITING = {
  resize: [t("Resize selected handwriting"), t("Drag to resize; arrow keys change size")],
  rotate: [t("Rotate selected handwriting"), t("Drag to rotate; hold Shift to snap to 15°; arrow keys rotate")],
};

// box: the selection's [x0, y0, x1, y1] in the surface's points as it was
// when the drag began (the gesture's centre); width / height: the surface.
// positions: {resize, rotate}, each [x, y]. labels: {resize, rotate}, each
// [name, title]. className: more classes for both buttons. valueFor(raw):
// the value of a press now, from raw = {mode ("resize" | "rotate"), x, y
// (the pointer from the centre, points, y down), x0, y0 (where it went
// down), scale (its distance from the centre over the distance it went down
// at), angle (radians turned since, in -π … π), shift, alt, clientX,
// clientY}. onStep(mode, sign): a focused handle's arrow key.
export function TransformHandles({ wrapRef, box, width, height, positions, maxScale = 10, onPreview, onCommit, turn = 0,
  labels = HANDWRITING, className = "", valueFor, onStep }) {
  const drag = useRef(null);
  const previewRef = useRef(onPreview);
  previewRef.current = onPreview;
  const cancel = () => { drag.current = null; previewRef.current(null); };
  useEffect(() => {
    // in the capture phase, so the selection's own Escape (a picture's lets
    // go of it) never sees the one that ends a drag
    const escape = (e) => {
      if (e.key !== "Escape" || !drag.current) return;
      e.preventDefault();
      e.stopPropagation();
      cancel();
    };
    window.addEventListener("blur", cancel);
    window.addEventListener("keydown", escape, true);
    return () => {
      if (drag.current) cancel();
      window.removeEventListener("blur", cancel);
      window.removeEventListener("keydown", escape, true);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const valueAt = (e) => {
    const d = drag.current;
    const x = (e.clientX - d.rect.left) / d.k - d.cx, y = (e.clientY - d.rect.top) / d.k - d.cy;
    let angle = Math.atan2(y, x) - d.startAngle;
    angle = Math.atan2(Math.sin(angle), Math.cos(angle));
    if (valueFor) {
      return valueFor({ mode: d.mode, x, y, x0: d.x0, y0: d.y0, scale: Math.hypot(x, y) / d.radius, angle,
        shift: e.shiftKey, alt: e.altKey, clientX: e.clientX, clientY: e.clientY });
    }
    if (e.shiftKey) angle = Math.round(angle / (Math.PI / 12)) * Math.PI / 12;
    return { cx: d.cx, cy: d.cy, angle: d.mode === "rotate" ? angle : 0,
      scale: d.mode === "resize" ? Math.max(0.1, Math.min(d.maxScale, Math.hypot(x, y) / d.radius)) : 1 };
  };
  const begin = (e, mode) => {
    if (e.button !== 0 || drag.current) return;
    e.preventDefault(); e.stopPropagation();
    const rect = wrapRef.current.getBoundingClientRect(), k = rect.width / width;
    const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
    const x = (e.clientX - rect.left) / k - cx, y = (e.clientY - rect.top) / k - cy;
    drag.current = { id: e.pointerId, mode, rect, k, cx, cy, x0: x, y0: y, radius: Math.max(0.01, Math.hypot(x, y)),
      startAngle: Math.atan2(y, x), maxScale };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const move = (e) => {
    if (drag.current?.id !== e.pointerId) return;
    e.preventDefault(); e.stopPropagation(); onPreview(valueAt(e));
  };
  const finish = (e) => {
    if (drag.current?.id !== e.pointerId) return;
    e.preventDefault(); e.stopPropagation();
    const value = valueAt(e);
    cancel();
    e.currentTarget.releasePointerCapture(e.pointerId);
    if (valueFor ? value != null : Math.abs(value.scale - 1) > 0.001 || Math.abs(value.angle) > 0.001) onCommit(value);
  };
  // Turned, each handle stands where the upright one does, turned about
  // its corner with the frame.
  const place = (mode) => {
    const [x, y] = positions[mode];
    const at = { left: pct(x, width), top: pct(y, height) };
    if (!turn) return at;
    return { ...at, transformOrigin: "0 0", transform: `rotate(${turn}deg)${mode === "rotate" ? " translate(0, -100%)" : ""}` };
  };
  return ["resize", "rotate"].map((mode) => <button key={mode} type="button"
    className={`inkTransformHandle inkTransform-${mode}${className ? ` ${className}` : ""}`}
    aria-label={labels[mode][0]} title={labels[mode][1]} style={place(mode)}
    onPointerDown={(e) => begin(e, mode)} onPointerMove={move} onPointerUp={finish}
    onPointerCancel={cancel} onLostPointerCapture={cancel} onClick={(e) => e.stopPropagation()}
    onKeyDown={(e) => {
      const sign = arrowSign(e.key);
      if (!sign) return;
      e.preventDefault(); e.stopPropagation();
      if (onStep) { onStep(mode, sign); return; }
      onCommit({ cx: (box[0] + box[2]) / 2, cy: (box[1] + box[3]) / 2,
        scale: mode === "resize" ? Math.min(maxScale, sign > 0 ? 1.1 : 1 / 1.1) : 1,
        angle: mode === "rotate" ? sign * Math.PI / 12 : 0 });
    }}>{mode === "resize" ? <ResizeIcon /> : <RedoIcon />}</button>);
}
