// The colour palette (docs/dev/settings.md "Colors"): one named source of
// colours for text highlights, highlighter strokes, pens and text boxes,
// and the colour arithmetic they share. Pure — no React, no DOM — so it
// runs under node --test; the React context is shared/ui/paletteContext.js.
//
// Two roles. The WASH set is light: a text highlight multiplies it onto the
// page at WASH_ALPHA, a highlighter stroke at ink.js HIGHLIGHTER_OPACITY.
// The INK set is dark: pens and text boxes draw it opaque. The account's
// palette (the `palette` preference, {wash, ink}) starts as the defaults
// below and keeps their slot count; a swatch the user changes replaces the
// colour new highlights and the tool strip offer, never the colour a stored
// highlight or stroke already has.
//
// A stored colour is concrete, never a slot: a highlight block's
// `properties.color` is `#rrggbbaa` (hex with alpha), a stroke's hex plus
// its own `opacity`, a text box's hex. normalizeColor reads the older
// `rgba(…)` strings too (migration step 37 rewrites them; an older server
// or an old export can still send one).
import { T } from "../i18n/i18n.js";

// The first four are the highlight colours Gamma always had.
export const WASH_COLORS = ["#ffe28f", "#aaebaa", "#9bcdff", "#e6b4ff", "#f9a8d4", "#fdba74", "#67e8f9", "#d4d4d8"];
export const INK_COLORS = ["#1f1f1f", "#6b7280", "#1d4ed8", "#0284c7", "#0f766e", "#15803d", "#65a30d",
  "#ca8a04", "#ea580c", "#dc2626", "#db2777", "#7c3aed", "#92400e", "#ffffff"];
export const WASH_ALPHA = 0.65;
export const DEFAULT_PALETTE = Object.freeze({ wash: Object.freeze([...WASH_COLORS]), ink: Object.freeze([...INK_COLORS]) });

const HEX6 = /^#([0-9a-f]{6})$/i;

// A stored palette ({wash, ink}, possibly hand-edited or from an older
// build) → a complete one: each slot a lowercase #rrggbb, a missing or bad
// slot its default, the slot counts the defaults'. undefined for a value
// that is no object (the preference falls back to the defaults).
export function normalizePalette(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const role = (list, defaults) => defaults.map((d, i) => {
    const c = Array.isArray(list) ? list[i] : undefined;
    return typeof c === "string" && HEX6.test(c) ? c.toLowerCase() : d;
  });
  return { wash: role(value.wash, WASH_COLORS), ink: role(value.ink, INK_COLORS) };
}

// --- one colour ---------------------------------------------------------------

// Any CSS colour Gamma ever stored (#rgb, #rgba, #rrggbb, #rrggbbaa,
// rgb(…), rgba(…)) → [r, g, b, a] with the channels 0..255 and a 0..1, or
// null for anything else.
export function parseColor(value) {
  const v = String(value || "").trim();
  let m = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec(v);
  if (m) {
    let h = m[1];
    if (h.length <= 4) h = h.replace(/./g, "$&$&");
    const n = [0, 2, 4, 6].map((i) => (i < h.length ? parseInt(h.slice(i, i + 2), 16) : 255));
    return [n[0], n[1], n[2], n[3] / 255];
  }
  m = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+)\s*)?\)$/i.exec(v);
  if (m) {
    const c = m.slice(1, 4).map((x) => Math.min(255, parseInt(x, 10)));
    const a = m[4] === undefined ? 1 : Math.min(1, Math.max(0, parseFloat(m[4])));
    return Number.isFinite(a) ? [...c, a] : null;
  }
  return null;
}

const hex2 = (n) => Math.round(Math.min(255, Math.max(0, n))).toString(16).padStart(2, "0");

// The stored form of a colour: `#rrggbbaa`, lowercase; null when `value`
// is not a colour.
export function normalizeColor(value) {
  const c = parseColor(value);
  return c ? `#${hex2(c[0])}${hex2(c[1])}${hex2(c[2])}${hex2(c[3] * 255)}` : null;
}

// The colour without its alpha, `#rrggbb`; null when not a colour.
export function hexColor(value) {
  const c = parseColor(value);
  return c ? `#${hex2(c[0])}${hex2(c[1])}${hex2(c[2])}` : null;
}

// `color` at `alpha` (0..1), as stored.
export function withAlpha(color, alpha) {
  const c = parseColor(color);
  return c ? `#${hex2(c[0])}${hex2(c[1])}${hex2(c[2])}${hex2(alpha * 255)}` : null;
}

// What a highlight stores for a wash swatch: the swatch at WASH_ALPHA.
export const highlightColor = (swatch) => withAlpha(swatch, WASH_ALPHA);
// The colour a highlight without one (or with an unreadable one) draws in.
export const DEFAULT_HIGHLIGHT = highlightColor(WASH_COLORS[0]);

// The entry of `colors` nearest to `color` by the "redmean" weighted RGB
// distance (a listed colour maps to itself); null when `color` is not a
// colour or the list is empty. Alpha is ignored.
export function nearestColor(color, colors) {
  const c = parseColor(color);
  if (!c) return null;
  let best = null, bestD = Infinity;
  for (const p of colors) {
    const q = parseColor(p);
    if (!q) continue;
    const rm = (c[0] + q[0]) / 2;
    const d = (2 + rm / 256) * (c[0] - q[0]) ** 2 + 4 * (c[1] - q[1]) ** 2 + (2 + (255 - rm) / 256) * (c[2] - q[2]) ** 2;
    if (d < bestD) { best = p; bestD = d; }
  }
  return best;
}

// Words for colours, so a swatch reads "Pink highlighter", not "#f9a8d4":
// a fixed table of reference colours, and any colour takes the name of the
// nearest one — a user's own swatch included. The table holds the default
// palettes and the highlighter colours older presets still carry.
const NAMES = [
  ["#1f1f1f", T("Black")], ["#6b7280", T("Gray")], ["#1d4ed8", T("Blue")], ["#0284c7", T("Sky blue")],
  ["#0f766e", T("Teal")], ["#15803d", T("Green")], ["#65a30d", T("Lime")], ["#ca8a04", T("Amber")],
  ["#ea580c", T("Orange")], ["#dc2626", T("Red")], ["#db2777", T("Pink")], ["#7c3aed", T("Violet")],
  ["#92400e", T("Brown")], ["#ffffff", T("White")],
  ["#ffe28f", T("Yellow")], ["#aaebaa", T("Green")], ["#9bcdff", T("Blue")], ["#e6b4ff", T("Purple")],
  ["#f9a8d4", T("Pink")], ["#fdba74", T("Peach")], ["#67e8f9", T("Cyan")], ["#d4d4d8", T("Light gray")],
  ["#fde047", T("Yellow")], ["#86efac", T("Mint")], ["#7dd3fc", T("Light blue")], ["#c4b5fd", T("Lavender")],
];
const NAME_KEYS = NAMES.map(([hex]) => hex);
// The English name (a T() key — the caller translates with t()); null for
// what is not a colour.
export function colorName(color) {
  const hex = nearestColor(color, NAME_KEYS);
  return hex ? NAMES[NAME_KEYS.indexOf(hex)][1] : null;
}

// --- dark pages -----------------------------------------------------------------

function toHsl([r, g, b]) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h;
  if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return [h * 60, s, l];
}

function fromHsl(h, s, l) {
  const f = (n) => {
    const k = (n + h / 30) % 12;
    const a = s * Math.min(l, 1 - l);
    return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
  };
  return [f(0) * 255, f(8) * 255, f(4) * 255];
}

// A page shown with its colours flipped (Flip page colors, Gamma Dark) is
// near black with light text: a pastel wash screened onto it turns into a
// mid-tone the text can't be read on (about 2.4:1 on yellow). Every
// highlight instead draws a dark-tuned colour of the same hue with normal
// blending (about 7:1): saturated, at a middle lightness, mostly
// transparent. A neutral stays neutral. `alpha` is the fill's; an area
// outline uses darkPageLine, a stronger line of the same hue.
export function darkPageColor(color, alpha = 0.3) {
  const c = parseColor(color);
  if (!c) return null;
  const [h, s, l] = toHsl(c);
  const neutral = s < 0.15;
  const [r, g, b] = fromHsl(h, neutral ? s : Math.max(s, 0.9), neutral ? 0.7 : Math.min(0.62, Math.max(0.5, l - 0.2)));
  return `rgba(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)}, ${alpha})`;
}
export const darkPageLine = (color) => darkPageColor(color, 0.75);
