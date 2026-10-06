// Where the reader is on a page's sheets (docs/dev/notebooks.md): which
// sheet, and how far down it as a fraction of its height. Neither the padding
// around the sheets nor the gaps (or the notes) between them scale with the
// sheets, so a place kept as a scroll offset, or a ratio of one, drifts by
// them; a sheet's own box scales exactly. A zoom, a resize, a switch between
// the notebook and the notes view and a tab switch all keep the place this
// way. Pure: `boxes` are [{id, top, height}] in the scroller's content
// coordinates, in order.

// The place of content height `y`: on the sheet it falls on, else on the
// nearest one, whose fraction then runs outside 0..1 and reads back the same
// way. null without sheets.
export function placeAt(boxes, y) {
  let best = null, dist = Infinity;
  for (const b of boxes) {
    const d = y < b.top ? b.top - y : y > b.top + b.height ? y - b.top - b.height : 0;
    if (d < dist) { dist = d; best = b; }
  }
  return best && best.height ? { id: best.id, fy: (y - best.top) / best.height } : null;
}

// The content height of a place; null when its sheet is not among the boxes.
export function placeY(boxes, place) {
  const b = place && boxes.find((x) => x.id === place.id);
  return b ? b.top + place.fy * b.height : null;
}
