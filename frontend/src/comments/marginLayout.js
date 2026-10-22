// The PDF's comment margin (docs/dev/comments.md "The PDF margin"): where a
// page stands once a column of comment cards goes beside it, and how the
// cards stack in that column. Pure, so node tests it
// (tests/marginLayout.test.mjs).

// The column's width, the space between the page and it, the least space
// left at the viewer's edges, and the space between two cards (CSS px).
export const CARD_W = 280;
export const COLUMN_GAP = 24;
export const EDGE = 16;
export const CARD_GAP = 8;

// The page's left edge in a viewer `viewerW` wide when the column goes
// beside a page `pageW` wide: the page and the column are centred together,
// so the page moves left by half of what the column takes. Null when that
// leaves less than EDGE at either side (a fit-width page, a narrow viewer):
// then there is no margin.
export function marginLeft(viewerW, pageW) {
  if (!(viewerW > 0) || !(pageW > 0)) return null;
  const left = Math.floor((viewerW - pageW - COLUMN_GAP - CARD_W) / 2);
  return left >= EDGE ? left : null;
}

// The page's left edge as the viewer lays it out: beside its margin while the
// document has cards (`withMargin`) and the column fits, else centred by its
// auto margins, at 0 once it is wider than the viewer.
export function pageLeft(viewerW, pageW, withMargin) {
  const beside = withMargin ? marginLeft(viewerW, pageW) : null;
  return beside ?? Math.max(0, (viewerW - pageW) / 2);
}

// The cards' tops, by key. `items`: {key, top (its highlight's first line,
// px from the page's top), height}, in the order cards with the same top
// should stand. Sorted by top, each card goes at its highlight's top, or
// just under the card above when that would overlap it. The `active` card
// stays level with its highlight, as in Notion: the cards under it are
// pushed down, the ones over it up, but none above `min` (the first page's
// top, which the reader could not scroll past, or under the last card of the
// pages above): when they would go there, the cards stack down from it and
// the active one stands as near its highlight as they leave room for.
export function stackCards(items, { active = "", gap = CARD_GAP, min = -Infinity } = {}) {
  const list = (items || []).map((it, i) => ({ ...it, i })).sort((a, b) => a.top - b.top || a.i - b.i);
  const at = Math.max(0, list.findIndex((it) => it.key === active));
  const tops = new Map();
  if (!list.length) return tops;
  if (place(list, at, gap, tops) < min) {
    tops.clear();
    place(list, 0, gap, tops, min);
  }
  return tops;
}

// Places `list` (sorted) into `tops` from the card at `at` (at its top, or
// `min` when lower) down, then up from it; answers the first card's top.
function place(list, at, gap, tops, min = -Infinity) {
  // From the active card (else the first) down.
  let bottom = -Infinity;
  for (let i = at; i < list.length; i++) {
    const it = list[i];
    const top = i === at ? Math.max(it.top, min) : Math.max(it.top, bottom + gap);
    tops.set(it.key, top);
    bottom = top + (it.height || 0);
  }
  // And up from it: each card ends a gap over the one under it.
  let ceiling = tops.get(list[at].key);
  for (let i = at - 1; i >= 0; i--) {
    const it = list[i];
    const top = Math.min(it.top, ceiling - gap - (it.height || 0));
    tops.set(it.key, top);
    ceiling = top;
  }
  return tops.get(list[0].key);
}
