// The PDF viewer's page layout model (pdf/PdfViewer.jsx), shared by its page
// boxes' styles and all of its scroll math, and the reads of a viewer's
// scroller that App and a tab group's view make (docs/dev/pdf_loading.md,
// "Where a page stands").
//
// The pages run one way: down, one under another (the default), or across,
// side by side from left to right (Settings → Reading › PDFs › Page
// direction), PAGE_GAP apart along the run. A page's length along the run is
// its height down and its width across. Across the run, a page narrower
// (down) or shorter (across) than the line it stands in is centred in it.
// Pages not measured yet count as FALLBACK_* until they are.

export const PAGE_GAP = 8;
export const FALLBACK_H = 800, FALLBACK_W = 600;
export const ACROSS = "across";

// Whether the pages of a viewer's scroller run across: the viewer marks the
// scroller with data-flow="across".
export const runsAcross = (scroller) => scroller?.dataset?.flow === ACROSS;

// Page idx's start edge along the run at `scale`, `sizes` the pages'
// natural lengths along it.
export function pageStartAt(sizes, idx, scale, fallback) {
  let at = 0;
  for (let i = 0; i < idx; i++) at += (sizes[i] || fallback) * scale + PAGE_GAP;
  return at;
}

// The page that covers run position `at` (content px) at `scale`: its
// index, how far into the page (0 to 1), and how far into the gap after it
// (px, which no zoom scales). A position in the gap after a page counts as
// that page; one past the last page as the last.
export function pageAt(sizes, scale, at, fallback) {
  let acc = 0;
  for (let i = 0; i < sizes.length; i++) {
    const len = (sizes[i] || fallback) * scale;
    if (acc + len + PAGE_GAP > at) {
      return { idx: i, frac: len > 0 ? Math.min(1, Math.max(0, (at - acc) / len)) : 0, gap: Math.max(0, at - acc - len) };
    }
    acc += len + PAGE_GAP;
  }
  return { idx: Math.max(0, sizes.length - 1), frac: 1, gap: 0 };
}

// Across the run, where a page `len` long starts in a line `line` long:
// centred, or at the line's start once it is the longer of the two.
export const crossStart = (line, len) => Math.max(0, (line - len) / 2);

// Where a page box stands in its scroller, along the run: its start in the
// view (`at`, px from the view's start edge), its length, and the view's.
function placeOf(scroller, box) {
  const across = runsAcross(scroller);
  const s = scroller.getBoundingClientRect(), b = box.getBoundingClientRect();
  return across
    ? { across, at: b.left - s.left - scroller.clientLeft, len: b.width, view: scroller.clientWidth, scroll: scroller.scrollLeft, range: scroller.scrollWidth }
    : { across, at: b.top - s.top - scroller.clientTop, len: b.height, view: scroller.clientHeight, scroll: scroller.scrollTop, range: scroller.scrollHeight };
}

// Where a jump to a page puts its start edge in the view: `offset` px in
// from the view's start, so the end of the page before shows; across, a
// page the view holds whole stands in its middle instead.
const landingAt = (p, offset) => (p.across && p.len <= p.view ? (p.view - p.len) / 2 : offset);

// The scroll that lands a page box where a jump to it does (landingAt):
// what to pass to the scroller's scrollTo.
export function pageLanding(scroller, box, offset = 80) {
  const p = placeOf(scroller, box);
  const to = Math.max(0, p.scroll + p.at - landingAt(p, offset));
  return p.across ? { left: to } : { top: to };
}

// Whether a page box stands where a jump to it lands, give or take `slack`
// px; a page too near either end of the document to get there counts once
// the scroller is at that end, short of its place (at the start, a first
// page that cannot be centred) or past it (at the end, a short last page
// that cannot come up to the top).
export function pageLanded(scroller, box, offset = 80, slack = 150) {
  const p = placeOf(scroller, box);
  const want = landingAt(p, offset);
  return Math.abs(p.at - want) <= slack
    || (p.scroll <= 1 && p.at <= want)
    || (p.scroll + p.view >= p.range - 2 && p.at >= want);
}

// Of `pages`, the scroller's page boxes in document order, the one under
// the middle of the view along the run, or null for none (a gap). A binary
// search: a few layout reads, not one per page before it.
export function pageInMiddle(scroller, pages) {
  const across = runsAcross(scroller), cr = scroller.getBoundingClientRect();
  const mid = across ? cr.left + cr.width / 2 : cr.top + cr.height / 2;
  let lo = 0, hi = pages.length - 1;
  while (lo <= hi) {
    const m = (lo + hi) >> 1, r = pages[m].getBoundingClientRect();
    if ((across ? r.right : r.bottom) < mid) lo = m + 1;
    else if ((across ? r.left : r.top) > mid) hi = m - 1;
    else return pages[m];
  }
  return null;
}
