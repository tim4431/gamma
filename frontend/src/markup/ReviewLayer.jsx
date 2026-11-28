// An agent's changes on a surface (chat/noteReview.js): a highlight, a
// drawing, a text box or a picture it placed, which the page draws as
// itself. One that is a suggestion (made while the account reviews, until
// it is accepted or rejected) is framed in green, and so is the change a
// click in the chat shows, each with its buttons at the frame's right edge
// (editor/ReviewButtons.jsx: Accept and Reject, or the shown change's
// Hide), as the block's row in the notes has them. A PDF page is its
// page's number, a page of paper its sheet's id (markup/MarkupLayers.jsx
// mounts this over the other layers). The frames take no pointer; the
// buttons do. The shown change's frame carries shownMark, so a press within
// it keeps the change shown (chat/noteReview.js). Each surface watches its
// page's changes (`useWatchPage`), so they are read whether or not the
// notes show.
import { useLayoutEffect, useRef, useState } from "react";
import { clamp } from "../ink/ink.js";
import { CHANGE_STATES, shownMark, surfaceKey, useSurfaceChanges, useWatchPage } from "../chat/noteReview.js";
import { ReviewButtons } from "../editor/ReviewButtons";
import { usePageTools } from "./PageTools";

const PAD = 4; // the frame's room around what it holds, in points
const padded = ([x0, y0, x1, y1]) => [x0 - PAD, y0 - PAD, x1 + PAD, y1 + PAD];
// The layer's units per point of a box measured in `frame` ([width,
// height]; none: the surface's own points).
const scaleOf = (frame, width) => (frame?.[0] ? width / frame[0] : 1);
// What the layer frames: the suggestions, and the change shown from the chat.
const framed = ({ change }) => change.state === CHANGE_STATES.OPEN || !!change.shown;
const stop = (e) => e.stopPropagation();

// The buttons by their frame (`frame`: [x0, y0, x1, y1] in the layer's
// units, of `width` × `height`), kept inside its page, which the next page
// would paint over: below the frame where they fit, else above it, else
// over the frame's foot; ending at the frame's right edge, moved in from
// the page's left. Measured in screen pixels, and again as the page or the
// buttons resize (a zoom, a Reject's refusal). Their presses are not the
// page's, which would start a selection or a stroke.
function ReviewTag({ frame: [, y0, x1, y1], width, height, children }) {
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
        left: clamp((x1 / width) * W - w, 0, W - w),
        top: below + h <= H ? below : above >= 0 ? above : clamp(below, 0, H - h),
      };
      setAt((was) => (was && was.left === next.left && was.top === next.top ? was : next));
    };
    place();
    const sizes = new ResizeObserver(place);
    sizes.observe(layer);
    sizes.observe(tag);
    return () => sizes.disconnect();
  }, [y0, x1, y1, width, height]);
  return (
    <div ref={ref} className="reviewTag"
      style={at || { right: `${(1 - x1 / width) * 100}%`, top: `${(y1 / height) * 100}%` }}>
      <div className="reviewTagBox" onPointerDown={stop} onMouseDown={stop}>{children}</div>
    </div>
  );
}

// A placed block's frame and its buttons; `box` in the layer's units.
function ChangeFrame({ change, box, width, height }) {
  const pct = (v, of) => `${(v / of) * 100}%`;
  const frame = padded(box);
  const [x0, y0, x1, y1] = frame;
  return (
    <>
      <div className="reviewObjectFrame" {...shownMark(change)} style={{ left: pct(x0, width), top: pct(y0, height),
        width: pct(x1 - x0, width), height: pct(y1 - y0, height) }} />
      <ReviewTag frame={frame} width={width} height={height}>
        <ReviewButtons change={change} blockId={change.blockId} />
      </ReviewTag>
    </>
  );
}

// `review` (PageTools): the account's own page, which App's tools alone
// carry; a tab group's view of another page (workspace/PagePane.jsx) has none.
export function ReviewLayer({ surface, width, height }) {
  const { pageId, review } = usePageTools();
  const allowed = !!review;
  useWatchPage(pageId, allowed);
  const changes = useSurfaceChanges(surfaceKey(pageId, surface)).filter(framed);
  if (!allowed || !width || !height || !changes.length) return null;
  return (
    <div className="reviewLayer">
      {changes.map(({ change, frame, box }) => {
        const k = scaleOf(frame, width);
        return <ChangeFrame key={change.blockId} change={change} box={box.map((v) => v * k)} width={width} height={height} />;
      })}
    </div>
  );
}
