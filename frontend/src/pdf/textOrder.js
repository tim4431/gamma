// Which of two passages of a PDF page reads first: by the order the page's
// text layer holds its text in (pdf.js's spans, in the order the file
// draws its text, as the server's glyphs read it), not by where they lie,
// which a page in columns says wrong: the foot of the left column reads
// before the top of the right one, though lower; a phrase at the end of a
// line reads before one at the start of a line further down, though to its
// right. A highlight in parts keeps its parts in this order
// (shared/model/highlightParts.js).

// The first of the spans (their viewport boxes, in the layer's order) that
// a passage's rects cover, by their middle line: its index, and where the
// passage begins on it. null when no span is under the passage.
function firstUnder(boxes, rects) {
  for (let i = 0; i < boxes.length; i++) {
    const b = boxes[i], mid = (b.top + b.bottom) / 2;
    const on = rects.filter((r) => mid >= r.top && mid <= r.bottom && Math.min(b.right, r.right) - Math.max(b.left, r.left) > 1);
    if (on.length) return { i, x: Math.min(...on.map((r) => Math.max(r.left, b.left))) };
  }
  return null;
}

// Whether the passage `a` reads before the passage `b` on the page whose
// text layer is `textLayer`, each passage its rects in viewport px
// ({left, top, right, bottom}): the first span under it comes first, or
// on the same span it begins further left. null when the layer cannot
// tell (no text under one of them, no layer).
export function readsBefore(textLayer, a, b) {
  if (!textLayer || !a?.length || !b?.length) return null;
  const spans = [...textLayer.querySelectorAll("span")].filter((s) => s.firstChild?.nodeType === Node.TEXT_NODE);
  const boxes = spans.map((s) => s.getBoundingClientRect());
  const fa = firstUnder(boxes, a), fb = firstUnder(boxes, b);
  if (!fa || !fb) return null;
  return fa.i !== fb.i ? fa.i < fb.i : fa.x < fb.x;
}

// A position's rects ({pageNumber, width, height, rects}, in its own frame)
// in viewport px over `pageEl`, the page's box as drawn now.
export function viewportRects(pageEl, pos) {
  const box = pageEl.getBoundingClientRect();
  const kx = box.width / (pos.width || box.width || 1), ky = box.height / (pos.height || box.height || 1);
  return (pos.rects || []).map((r) => ({
    left: box.left + r.x1 * kx, top: box.top + r.y1 * ky, right: box.left + r.x2 * kx, bottom: box.top + r.y2 * ky,
  }));
}
