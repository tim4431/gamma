// An agent's changes on a surface, as a diff shows them (chat/noteReview.js).
// Asked for: a highlight's wash, a drawing's strokes, a text box with its
// text, a picture, each where the call would put it (its preview's `shape`,
// in the surface's points: gamma/ai_tools/drawing.py _shape_of,
// commenting.py _highlight_shape), drawn as the page will draw it once
// written, faded and inside a dashed frame, as a diff marks what is not
// written yet, and under the frame the card's own answers
// (editor/NoteReviewBar.jsx AskBar): Keep there or on the card, it is one
// question. Made: the block is on the page as itself; one waiting for
// review is framed, solid, with Keep and Undo under it, and a kept one
// leaves a mark at its left, the notes' own mark (.aiChangeMark), which
// unfolds it to its frame with Undo and Fold — the same unfolding as its
// row's mark in the notes, since it is the same block (`noteReview.toggle`).
// A PDF page is its page's number, a page of paper its sheet's id
// (markup/MarkupLayers.jsx mounts this over the other layers). The shapes
// take no pointer; the marks and the bars do. Each surface watches its
// page's changes (`useWatchPage`), so they are read whether or not the notes
// show.
import { useLayoutEffect, useRef, useState } from "react";
import { DEFAULT_HIGHLIGHT, lineColor } from "../shared/model/palette.js";
import { assetUrl } from "../shared/lib/utils.js";
import { t } from "../shared/i18n/i18n.js";
import { dashArray } from "../ink/ink.js";
import { textStyle } from "./textBox.js";
import { CHANGE_STATES, noteReview, surfaceKey, useExpanded, useSurfaceAsks, useSurfaceChanges,
  useWatchPage } from "../chat/noteReview.js";
import { AskBar, NoteReviewBar, changedBy } from "../editor/NoteReviewBar";
import { usePageTools } from "./PageTools";

const PAD = 4; // the frame's room around what it holds, in points

// A shape's own frame in the surface's points: [x0, y0, x1, y1].
function boundsOf(shape, k) {
  const xs = [], ys = [];
  if (shape.kind === "highlight") {
    for (const [x0, y0, x1, y1] of shape.rects) { xs.push(x0 * k, x1 * k); ys.push(y0 * k, y1 * k); }
  } else if (shape.kind === "ink") {
    for (const s of shape.strokes) for (const [x, y] of s.points) { xs.push(x * k); ys.push(y * k); }
  } else if (shape.box) {
    xs.push(shape.box.x * k, (shape.box.x + shape.box.w) * k);
    ys.push(shape.box.y * k, (shape.box.y + shape.box.h) * k);
  }
  if (!xs.length) return null;
  return [Math.min(...xs) - PAD, Math.min(...ys) - PAD, Math.max(...xs) + PAD, Math.max(...ys) + PAD];
}

// A highlight in the colour it will store (its alpha in it), a line style in
// that colour's line, as the PDF draws a stored one (pdf/PdfViewer.jsx).
function Highlight({ shape, k }) {
  const fill = shape.color || DEFAULT_HIGHLIGHT;
  const line = lineColor(fill) || fill;
  return shape.rects.map(([x0, y0, x1, y1], i) => {
    const [x, y, w, h] = [x0 * k, y0 * k, (x1 - x0) * k, (y1 - y0) * k];
    if (shape.style === "underline" || shape.style === "squiggly") {
      return <line key={i} x1={x} y1={y + h} x2={x + w} y2={y + h} stroke={line} strokeWidth={1.5}
        strokeDasharray={shape.style === "squiggly" ? "2 2" : undefined} />;
    }
    if (shape.style === "strikeout") return <line key={i} x1={x} y1={y + h / 2} x2={x + w} y2={y + h / 2} stroke={line} strokeWidth={1.5} />;
    return <rect key={i} x={x} y={y} width={w} height={h} fill={fill} />;
  });
}

// A drawing's strokes, dashed by the ink's own table (ink/ink.js DASH).
function Strokes({ shape, k }) {
  return shape.strokes.map((s, i) => (
    <polyline key={i} points={s.points.map(([x, y]) => `${x * k},${y * k}`).join(" ")} fill={s.fill ? s.color : "none"}
      fillOpacity={s.fill ? 0.25 : undefined} stroke={s.color} strokeWidth={s.size * k} strokeLinecap="round" strokeLinejoin="round"
      strokeDasharray={dashArray({ dash: s.dash, size: s.size * k }) ?? undefined} />
  ));
}

// A picture or a text box in its frame: the text box in the page's own box
// face (.textBoxBody: its padding, size and colours, one point `k`), so it
// wraps as the written box will.
function Placed({ shape, k }) {
  const { x, y, w, h } = shape.box;
  if (shape.kind === "picture") {
    return <image href={assetUrl(shape.url)} x={x * k} y={y * k} width={w * k} height={h * k} preserveAspectRatio="none" />;
  }
  const { size, color, bg } = textStyle(shape);
  return (
    <foreignObject x={x * k} y={y * k} width={w * k} height={h * k}>
      <div className="textBoxBody reviewTextBox" style={{ "--tb-pt": `${k}px`, "--tb-size": size, color,
        background: bg || undefined }}>{shape.text}</div>
    </foreignObject>
  );
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

// A bar under its frame (`frame`: [x0, y0, x1, y1] in the layer's units,
// of `width` × `height`), kept inside its page, which the next page would
// paint over: below the frame where it fits, else above it, else over the
// frame's foot; from the frame's left, moved in from the page's right edge.
// Measured in screen pixels, and again as the page or the bar resizes (a
// zoom, a longer line).
function ReviewTag({ frame: [x0, y0, , y1], width, height, children }) {
  const ref = useRef(null);
  const [at, setAt] = useState(null);
  useLayoutEffect(() => {
    const tag = ref.current, layer = tag?.parentElement;
    if (!tag || !layer) return undefined;
    const place = () => {
      const W = layer.clientWidth, H = layer.clientHeight, w = tag.offsetWidth, h = tag.offsetHeight;
      if (!W || !H) return;
      const below = (y1 / height) * H, above = (y0 / height) * H - h;
      const next = {
        left: clamp((x0 / width) * W, 0, W - w),
        top: below + h <= H ? below : above >= 0 ? above : clamp(below, 0, H - h),
      };
      setAt((was) => (was && was.left === next.left && was.top === next.top ? was : next));
    };
    place();
    const sizes = new ResizeObserver(place);
    sizes.observe(layer);
    sizes.observe(tag);
    return () => sizes.disconnect();
  }, [x0, y0, y1, width, height]);
  return (
    <div ref={ref} className="reviewTag"
      style={at || { left: `${(x0 / width) * 100}%`, top: `${(y1 / height) * 100}%` }}>
      {children}
    </div>
  );
}

// A placed block an agent made, on its surface: framed with its bar while
// it waits for review or is unfolded, else its mark. `box` is in the
// surface's points.
function MadeObject({ change, box: [x0, y0, x1, y1], width, height }) {
  const unfolded = useExpanded(change.blockId);
  const waiting = change.state === CHANGE_STATES.OPEN;
  const pct = (v, of) => `${(v / of) * 100}%`;
  const kind = change.steps[0]?.tool === "highlight" ? "highlight" : "object";
  if (!waiting && !unfolded) {
    return (
      <button type="button" className="aiChangeMark aiChangeMark-object reviewObjectMark" aria-expanded="false"
        style={{ left: pct(x0 - PAD, width), top: pct(y0, height), height: pct(y1 - y0, height) }}
        title={changedBy(change, { show: true })} aria-label={t("Show the AI's change")}
        onPointerDown={(e) => e.stopPropagation()} onClick={() => noteReview.toggle(change.blockId, true)} />
    );
  }
  return (
    <>
      <div className={`reviewObjectFrame ${kind}`} style={{ left: pct(x0 - PAD, width), top: pct(y0 - PAD, height),
        width: pct(x1 - x0 + 2 * PAD, width), height: pct(y1 - y0 + 2 * PAD, height) }} />
      <ReviewTag frame={[x0 - PAD, y0 - PAD, x1 + PAD, y1 + PAD]} width={width} height={height}>
        <NoteReviewBar change={change} />
      </ReviewTag>
    </>
  );
}

export function ReviewLayer({ surface, width, height }) {
  const { pageId, preview, review } = usePageTools();
  const shown = !!review && !preview;
  useWatchPage(pageId, shown);
  const key = surfaceKey(pageId, surface);
  const asks = useSurfaceAsks(key);
  const made = useSurfaceChanges(key);
  if (!shown || !width || !height || (!asks.length && !made.length)) return null;
  const drawn = asks.map((ask) => {
    const shape = ask.preview.shape;
    const k = shape.frame?.[0] ? width / shape.frame[0] : 1;
    return { ask, shape, k, bounds: boundsOf(shape, k) };
  }).filter((d) => d.bounds);
  const inked = drawn.filter((d) => d.shape.kind === "ink");
  // The strokes in a layer of their own, which a dark page flips as it
  // flips the page's ink (app.css); the rest, and every frame, over them.
  return (
    <div className="reviewLayer">
      {inked.length ? (
        <svg className="reviewShapes reviewInk" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
          {inked.map(({ ask, shape, k }) => <g key={ask.id} className="reviewShape"><Strokes shape={shape} k={k} /></g>)}
        </svg>
      ) : null}
      <svg className="reviewShapes" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
        {drawn.map(({ ask, shape, k, bounds: [x0, y0, x1, y1] }) => (
          <g key={ask.id}>
            {shape.kind === "ink" ? null : (
              <g className="reviewShape">
                {shape.kind === "highlight" ? <Highlight shape={shape} k={k} /> : <Placed shape={shape} k={k} />}
              </g>
            )}
            <rect className="reviewFrame" x={x0} y={y0} width={x1 - x0} height={y1 - y0} />
          </g>
        ))}
      </svg>
      {drawn.map(({ ask, bounds }) => (
        <ReviewTag key={ask.id} frame={bounds} width={width} height={height}>
          <AskBar ask={ask} />
        </ReviewTag>
      ))}
      {made.map(({ change, frame, box }) => {
        const k = frame?.[0] ? width / frame[0] : 1;
        return <MadeObject key={change.blockId} change={change} box={box.map((v) => v * k)} width={width} height={height} />;
      })}
    </div>
  );
}
