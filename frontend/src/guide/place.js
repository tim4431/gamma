// Where a guide card goes beside its spotlight. Pure, so node tests it
// (tests/guide.test.mjs); GuideOverlay measures and renders.

export const GAP = 12;     // card distance from the spotlight
export const MARGIN = 12;  // card distance from the viewport edge
export const CARD_W = 320;
const BEAK_INSET = 23;     // the beak's centre stays 16 px (plus its half) from a card corner

// rect: the spotlight ({top, left, right, bottom, width, height}); avoid: a
// box the card must keep clear of as well (a step's `avoid` anchor: the
// table above its add strip), or null. The step's preferred side when it
// fits, else below, above, right, left — each judged clear of both boxes;
// clamped to the viewport. "inside" tucks the card into the target's
// bottom-right corner, for large targets such as the whole viewer.
export function placeCard(rect, cardH, vw, vh, prefer, avoid = null) {
  const r = rect;
  if (prefer === "inside") {
    return {
      side: "inside",
      top: Math.max(MARGIN, r.bottom - GAP - cardH),
      left: Math.max(MARGIN, Math.min(r.right - GAP - CARD_W, vw - CARD_W - MARGIN)),
    };
  }
  const b = avoid ? {
    top: Math.min(r.top, avoid.top), bottom: Math.max(r.bottom, avoid.bottom),
    left: Math.min(r.left, avoid.left), right: Math.max(r.right, avoid.right),
  } : r;
  const fits = {
    bottom: b.bottom + GAP + cardH + MARGIN <= vh,
    top: b.top - GAP - cardH - MARGIN >= 0,
    right: b.right + GAP + CARD_W + MARGIN <= vw,
    left: b.left - GAP - CARD_W - MARGIN >= 0,
  };
  const order = [prefer, "bottom", "top", "right", "left"].filter(Boolean);
  const side = order.find((s) => fits[s]) || "bottom";
  // Beside the box on the chosen side, centred on the spotlight.
  let top, left;
  if (side === "bottom") { top = b.bottom + GAP; left = r.left + r.width / 2 - CARD_W / 2; }
  else if (side === "top") { top = b.top - GAP - cardH; left = r.left + r.width / 2 - CARD_W / 2; }
  else if (side === "right") { left = b.right + GAP; top = r.top + r.height / 2 - cardH / 2; }
  else { left = b.left - GAP - CARD_W; top = r.top + r.height / 2 - cardH / 2; }
  left = Math.max(MARGIN, Math.min(left, vw - CARD_W - MARGIN));
  top = Math.max(MARGIN, Math.min(top, vh - cardH - MARGIN));
  return { top, left, side, beak: beakAt(side, r, b, top, left, cardH) };
}

// The beak on the card edge that faces the anchor, centred on the anchor
// (clamped away from the corners) — none when the clamped card overlaps the
// box it sits beside.
function beakAt(side, r, b, top, left, cardH) {
  const clamp = (v, max) => Math.max(BEAK_INSET, Math.min(v, max - BEAK_INSET));
  if (side === "bottom" || side === "top") {
    if (side === "bottom" ? top < b.bottom : top + cardH > b.top) return null;
    return { x: clamp(r.left + r.width / 2 - left, CARD_W) };
  }
  if (side === "right" ? left < b.right : left + CARD_W > b.left) return null;
  return { y: clamp(r.top + r.height / 2 - top, cardH) };
}
