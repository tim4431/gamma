// The text position under a point in the viewport, {node, offset} or null:
// the standard call where there is one, else the older one every WebKit and
// Blink has (the iPad's WebKit before Safari 26.2).
export function caretAt(x, y) {
  if (document.caretPositionFromPoint) {
    const p = document.caretPositionFromPoint(x, y);
    return p && { node: p.offsetNode, offset: p.offset };
  }
  const r = document.caretRangeFromPoint?.(x, y);
  return r && { node: r.startContainer, offset: r.startOffset };
}
