// Where a highlight's note badge sits: the end of its last line, found from
// the highlight's stored rects by geometry. Stored order is trusted for one
// thing only, which column the passage ends in. The viewer's own
// highlights are one rect per line, top to bottom, and a highlight in parts
// keeps its parts in reading order (shared/model/highlightParts.js), so its
// last rect is in the column it ends in: the foot of the left column and
// the top of the right one end at the right one's last line, not at the
// lower line. Within that column the order counts for nothing: imported
// highlights (embedded PDF annotations, Zotero) keep per-glyph rects in
// whatever order the file listed them, and a selection can leave a sliver
// rect nowhere near the text, so "the last rect" put the bubble at the
// wrong line, or far off in the margin.

// Rects narrower than this (in the highlight's stored page units) are
// slivers, never the passage's end; ignored unless nothing else is left.
const SLIVER_W = 3;

// Rects whose runs across the page lie further apart than this share of a
// line's height (the median rect's) stand in different columns: a column
// gutter is wider, a word's space or the gap between two glyphs narrower.
export const COLUMN_GAP = 0.75;

// The rects of `pool` in the column that holds `end`: a run across the
// page that its rects cover with no gap wider than COLUMN_GAP.
function columnOf(pool, end) {
  const heights = pool.map((r) => r.y2 - r.y1).sort((a, b) => a - b);
  const gap = heights[heights.length >> 1] * COLUMN_GAP;
  const runs = [];
  for (const r of [...pool].sort((a, b) => a.x1 - b.x1)) {
    const run = runs[runs.length - 1];
    if (run && r.x1 <= run.x2 + gap) run.x2 = Math.max(run.x2, r.x2);
    else runs.push({ x1: r.x1, x2: r.x2 });
  }
  const run = runs.find((c) => end.x1 >= c.x1 && end.x1 <= c.x2);
  return pool.filter((r) => r.x1 >= run.x1 && r.x1 <= run.x2);
}

// The rect the badge hangs off: among the solid rects of the column the
// last of them is in (columnOf), the one whose vertical centre is lowest
// picks the last line; the badge goes at the right edge of that line (the
// max x2 of every rect of the column sharing its band) with that line's
// top. Null when there is nothing to anchor to.
export function noteBadgeAnchor(rects) {
  const all = (rects || []).filter((r) => r && Number.isFinite(r.x1) && Number.isFinite(r.x2)
    && Number.isFinite(r.y1) && Number.isFinite(r.y2));
  if (!all.length) return null;
  const solid = all.filter((r) => r.x2 - r.x1 >= SLIVER_W);
  const kept = solid.length ? solid : all;
  const pool = columnOf(kept, kept.at(-1));
  const mid = (r) => (r.y1 + r.y2) / 2;
  const last = pool.reduce((a, r) => (mid(r) > mid(a) ? r : a));
  const band = pool.filter((r) => mid(r) >= last.y1 && mid(r) <= last.y2);
  return {
    x1: Math.min(...band.map((r) => r.x1)),
    x2: Math.max(...band.map((r) => r.x2)),
    y1: Math.min(...band.map((r) => r.y1)),
    y2: Math.max(...band.map((r) => r.y2)),
  };
}
