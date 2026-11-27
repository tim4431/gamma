// A picture an AI's change replaced by another where it stood, drawn in the
// note's tracked changes as one compare frame (editor/mdDiff.js
// pairPictureDiffs makes the pair; BlockMarkdown hands it here). Swipe, the
// default: the two stacked, the one before left of a divider the reader
// drags, clicks to or moves with the arrow keys, the one after right of it. Side
// by side: each in its own column under its label, a click zooms it. Spans
// only: it stands in a <p>, as a note's pictures do.
import React, { useRef, useState } from "react";
import { Segmented } from "../settings/SettingsKit";
import { Lightbox } from "../shared/ui/Lightbox";
import { DRAG_MIN } from "../shared/ui/press.js";
import { T, t } from "../shared/i18n/i18n.js";

const MODES = [["swipe", T("Swipe")], ["sides", T("Side by side")]];
// How far a key moves the divider, in percent of the frame.
const KEY_STEP = { ArrowLeft: -5, ArrowDown: -5, ArrowRight: 5, ArrowUp: 5, PageDown: -25, PageUp: 25 };
const clamp = (n) => Math.min(100, Math.max(0, n));

// Under the swipe frame: the caption both share, or its change marked as
// the note's words are.
function SwipeCaption({ before, after }) {
  const was = before.alt || "", now = after.alt || "";
  if (was === now) return was ? <span className="mdImgCaption">{was}</span> : null;
  return (
    <span className="mdImgCaption">
      {was ? <del className="mdDiff">{was}</del> : null}
      {now ? <ins className="mdDiff">{now}</ins> : null}
    </span>
  );
}

// `before`, `after`: {src, alt, width} of each picture, its URL resolved.
export function PictureDiff({ before, after }) {
  const [mode, setMode] = useState("swipe");
  const [split, setSplit] = useState(50);
  const [zoomed, setZoomed] = useState(null);
  const frameRef = useRef(null);
  const handleRef = useRef(null);
  const pressRef = useRef(null); // the press on the frame: where, and whether it has gone across
  const moveTo = (e) => {
    const r = frameRef.current.getBoundingClientRect();
    if (r.width) setSplit(clamp(((e.clientX - r.left) / r.width) * 100));
    handleRef.current?.focus({ preventScroll: true });
  };
  const sides = [["before", before, t("Before")], ["after", after, t("After")]];
  // A press here is the frame's: it must not reach the row, whose
  // mousedown opens the editor.
  const stop = (e) => e.stopPropagation();
  return (
    <span className="pictureDiff" onMouseDown={stop} onClick={stop}>
      <span className="pictureDiffBar"><Segmented value={mode} onChange={setMode} options={MODES} /></span>
      {mode === "swipe" ? (
        <>
          <span
            ref={frameRef}
            className="pictureDiffSwipe"
            style={{ "--split": `${split}%` }}
            // Any pointer alike (docs/dev/ui-design.md, "One behaviour for
            // mouse and finger"): a drag across carries the divider, a press
            // let go where it was puts it there. Up or down is the notes'
            // scroll (touch-action: pan-y), which cancels the press.
            onPointerDown={(e) => {
              if (e.button !== 0) return;
              e.currentTarget.setPointerCapture?.(e.pointerId);
              pressRef.current = { x: e.clientX, y: e.clientY, across: false };
            }}
            onPointerMove={(e) => {
              const press = pressRef.current;
              if (!press || !e.currentTarget.hasPointerCapture?.(e.pointerId)) return;
              if (!press.across) {
                const dx = Math.abs(e.clientX - press.x);
                if (dx < DRAG_MIN || dx < Math.abs(e.clientY - press.y)) return;
                press.across = true;
              }
              moveTo(e);
            }}
            onPointerUp={(e) => {
              if (pressRef.current && !pressRef.current.across) moveTo(e);
              pressRef.current = null;
            }}
            onPointerCancel={() => { pressRef.current = null; }}
            // No text selection or picture drag starts under the divider.
            onMouseDown={(e) => e.preventDefault()}
          >
            <img className="pictureDiffImg" src={after.src} alt={after.alt || ""} width={after.width || undefined} draggable={false} />
            <span className="pictureDiffTag after">{t("After")}</span>
            <span className="pictureDiffBefore">
              <img className="pictureDiffImg" src={before.src} alt={before.alt || ""} draggable={false} />
              <span className="pictureDiffTag before">{t("Before")}</span>
            </span>
            <span
              ref={handleRef}
              className="pictureDiffHandle"
              role="slider"
              tabIndex={0}
              aria-label={t("Divider between the picture before and after")}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(split)}
              onKeyDown={(e) => {
                const to = e.key === "Home" ? 0 : e.key === "End" ? 100 : e.key in KEY_STEP ? split + KEY_STEP[e.key] : null;
                if (to == null) return;
                e.preventDefault();
                e.stopPropagation();
                setSplit(clamp(to));
              }}
            />
          </span>
          <SwipeCaption before={before} after={after} />
        </>
      ) : (
        <span className="pictureDiffSides">
          {sides.map(([side, pic, label]) => (
            <span key={side} className="pictureDiffSide">
              <span className={`pictureDiffLabel ${side}`}>{label}</span>
              <img className="pictureDiffImg" src={pic.src} alt={pic.alt || ""} width={pic.width || undefined}
                draggable={false} onClick={() => setZoomed(pic)} />
              {pic.alt ? <span className="mdImgCaption">{pic.alt}</span> : null}
            </span>
          ))}
        </span>
      )}
      {zoomed ? <Lightbox src={zoomed.src} alt={zoomed.alt || ""} caption={zoomed.alt} onClose={() => setZoomed(null)} /> : null}
    </span>
  );
}
