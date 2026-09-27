// Highlight palette shared by the PDF viewer, outliner, and context menus.
export const COLORS = [
  "rgba(255, 226, 143, 0.65)",
  "rgba(170, 235, 170, 0.65)",
  "rgba(155, 205, 255, 0.65)",
  "rgba(230, 180, 255, 0.65)"
];

// Which palette entry a stored colour is (0–3), or -1 for any other colour
// (imported highlights carry arbitrary ones). Spacing and case don't count.
// The PDF viewer tags a highlight with it (data-hl-color) so the stylesheet
// can swap in a dark-tuned set on dark pages.
const squash = (c) => String(c || "").replace(/\s+/g, "").toLowerCase();
const PALETTE_KEYS = COLORS.map(squash);
export function paletteIndex(color) {
  return PALETTE_KEYS.indexOf(squash(color));
}
