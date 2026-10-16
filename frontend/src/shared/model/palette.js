// The colour palette (docs/dev/settings.md "Colors"): the account's named
// colour groups, which of them each picker shows, and the colour
// arithmetic every picker shares. Pure — no React, no DOM — so it runs
// under node --test; the React context is shared/ui/paletteContext.js.
//
// The `palette` preference is {groups, pickers}. A group is {id, name,
// colors: [{hex, alpha}]}, the user's own set — renamed, added to, pruned
// or deleted in Settings → Appearance › Colors. The four PICKERS (the text
// selection popup and a highlight's recolour menu, a highlighter preset's
// colours, a pen preset's, a text box's) each list the group ids they
// show. The defaults are two groups: "Highlight", the light wash colours a
// text highlight or highlighter stroke multiplies onto the page at their
// alpha, and "Pen", the dark opaque colours pens and text boxes draw.
//
// A stored colour is concrete, never a slot: a highlight block's
// `properties.color` is `#rrggbbaa` (the swatch at its alpha), a stroke's
// hex plus its own `opacity` (the swatch's alpha when picked), a text box's
// hex. A changed swatch replaces what the pickers offer from then on, never
// the colour a stored highlight or stroke already has. normalizeColor reads
// the older `rgba(…)` strings too (migration step 37 rewrote them; an
// older server or an old export can still send one).
import { T } from "../i18n/locales.js";

// The wash colours are the four highlight colours Gamma always had; the
// ink-highlight colours are those four and the four more the tool strip's
// highlighters always offered; the ink colours are the pens'.
export const WASH_COLORS = ["#ffe28f", "#aaebaa", "#9bcdff", "#e6b4ff"];
export const INK_HIGHLIGHT_COLORS = [...WASH_COLORS, "#f9a8d4", "#fdba74", "#67e8f9", "#d4d4d8"];
export const INK_COLORS = ["#1f1f1f", "#6b7280", "#1d4ed8", "#0284c7", "#0f766e", "#15803d", "#65a30d",
  "#ca8a04", "#ea580c", "#dc2626", "#db2777", "#7c3aed", "#92400e", "#ffffff"];
export const WASH_ALPHA = 0.65;           // a text highlight's
export const INK_HIGHLIGHT_ALPHA = 0.6;   // a highlighter stroke's (ink.js HIGHLIGHTER_OPACITY)
export const MIN_ALPHA = 0.1;
export const MAX_GROUPS = 12;
export const MAX_GROUP_COLORS = 24;
export const MAX_GROUP_NAME = 40;

// The pickers: what shows swatches, in the order the pane lists them.
export const PICKERS = ["highlight", "highlighter", "pen", "text"];
// Each picker shows one group (its id).
const DEFAULT_PICKERS = { highlight: "highlight", highlighter: "ink-highlight", pen: "pen", text: "pen" };

const freeze = (v) => (Array.isArray(v) ? Object.freeze(v.map(freeze)) : v && typeof v === "object" ? Object.freeze(Object.fromEntries(Object.entries(v).map(([k, x]) => [k, freeze(x)]))) : v);
export const DEFAULT_PALETTE = freeze({
  groups: [
    { id: "highlight", name: T("Highlight"), colors: WASH_COLORS.map((hex) => ({ hex, alpha: WASH_ALPHA })) },
    { id: "ink-highlight", name: T("Ink highlight"), colors: INK_HIGHLIGHT_COLORS.map((hex) => ({ hex, alpha: INK_HIGHLIGHT_ALPHA })) },
    { id: "pen", name: T("Pen"), colors: INK_COLORS.map((hex) => ({ hex, alpha: 1 })) },
  ],
  pickers: DEFAULT_PICKERS,
});

const HEX6 = /^#([0-9a-f]{6})$/i;
const GROUP_ID = /^[A-Za-z0-9_-]{1,24}$/;

const alphaOf = (v, fallback) => {
  const n = typeof v === "number" ? v : typeof v === "string" ? Number.parseFloat(v) : NaN;
  return Number.isFinite(n) ? Math.round(Math.min(1, Math.max(MIN_ALPHA, n)) * 100) / 100 : fallback;
};

// One stored swatch (an {hex, alpha} or a bare hex string) → {hex, alpha}
// or null. `alpha` is the default when none is stored.
export function normalizeSwatch(value, alpha = 1) {
  const raw = typeof value === "string" ? { hex: value } : value;
  if (!raw || typeof raw !== "object" || typeof raw.hex !== "string" || !HEX6.test(raw.hex)) return null;
  return { hex: raw.hex.toLowerCase(), alpha: alphaOf(raw.alpha, alpha) };
}

// A fresh group id no group in `groups` has.
export function newGroupId(groups) {
  const used = new Set((groups || []).map((g) => g.id));
  let id;
  do { id = "g" + Math.random().toString(36).slice(2, 8); } while (used.has(id));
  return id;
}

// A stored palette (possibly hand-edited, from an older build, or the
// {wash, ink} shape the preference had first) → a complete one: every
// group a valid id, a name and lowercase swatches with an alpha, within
// the caps; every picker the id of an existing group (a list, from a
// build that let a picker show several, keeps its first). No usable group
// leaves the defaults. undefined for a value that is no object (the
// preference falls back to the defaults).
export function normalizePalette(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  if (!Array.isArray(value.groups) && (Array.isArray(value.wash) || Array.isArray(value.ink))) {
    // The first shape: two fixed slot lists, 8 wash and 14 ink. The wash
    // slots become the Ink highlight group and their first four the
    // Highlight group; the ink slots the Pen group.
    const slots = (list, defaults, alpha) => defaults.map((d, i) => normalizeSwatch(Array.isArray(list) ? list[i] : null, alpha) || { hex: d, alpha });
    return normalizePalette({ groups: [
      { id: "highlight", name: DEFAULT_PALETTE.groups[0].name, colors: slots(value.wash, WASH_COLORS, WASH_ALPHA) },
      { id: "ink-highlight", name: DEFAULT_PALETTE.groups[1].name, colors: slots(value.wash, INK_HIGHLIGHT_COLORS, INK_HIGHLIGHT_ALPHA) },
      { id: "pen", name: DEFAULT_PALETTE.groups[2].name, colors: slots(value.ink, INK_COLORS, 1) },
    ] });
  }
  const groups = [];
  const ids = new Set();
  for (const g of Array.isArray(value.groups) ? value.groups : []) {
    if (!g || typeof g !== "object") continue;
    let id = typeof g.id === "string" && GROUP_ID.test(g.id) ? g.id : newGroupId(groups);
    while (ids.has(id)) id = newGroupId(groups);
    ids.add(id);
    const name = (typeof g.name === "string" ? g.name.trim().slice(0, MAX_GROUP_NAME) : "") || T("Group");
    const colors = (Array.isArray(g.colors) ? g.colors : []).map((c) => normalizeSwatch(c)).filter(Boolean).slice(0, MAX_GROUP_COLORS);
    groups.push({ id, name, colors });
    if (groups.length >= MAX_GROUPS) break;
  }
  if (!groups.some((g) => g.colors.length)) return { groups: DEFAULT_PALETTE.groups.map((g) => ({ ...g, colors: g.colors.map((c) => ({ ...c })) })), pickers: { ...DEFAULT_PICKERS } };
  // A picker names an existing group: the one given, else its default
  // group, else the first group with a colour.
  const fallback = groups.find((g) => g.colors.length).id;
  const pickers = {};
  for (const key of PICKERS) {
    const given = value.pickers && typeof value.pickers === "object" ? value.pickers[key] : undefined;
    const id = Array.isArray(given) ? given[0] : given;
    pickers[key] = ids.has(id) ? id : ids.has(DEFAULT_PICKERS[key]) ? DEFAULT_PICKERS[key] : fallback;
  }
  return { groups, pickers };
}

// The swatches a picker offers: its group's colours, or, when that group
// has none (emptied while being edited), the first group's that has any —
// a picker is never empty.
export function pickerColors(palette, picker) {
  const p = palette || DEFAULT_PALETTE;
  const own = p.groups.find((g) => g.id === p.pickers?.[picker])?.colors;
  return own?.length ? own : p.groups.find((g) => g.colors.length)?.colors || DEFAULT_PALETTE.groups[0].colors;
}

// Whether `palette` is the defaults (the Reset button's state).
export const isDefaultPalette = (palette) => JSON.stringify(normalizePalette(palette)) === JSON.stringify(normalizePalette(DEFAULT_PALETTE));

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

// What a highlight stores for a swatch ({hex, alpha}, or a bare hex at the
// wash alpha): the colour at the swatch's alpha.
export function highlightColor(swatch) {
  if (!swatch) return null;
  return typeof swatch === "string" ? withAlpha(swatch, WASH_ALPHA) : withAlpha(swatch.hex, swatch.alpha ?? WASH_ALPHA);
}
// A swatch as CSS (#rrggbbaa), for drawing it.
export const swatchCss = (swatch) => highlightColor(swatch);
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
// groups and the highlighter colours older presets still carry.
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
// what is not a colour. A swatch object names its hex.
export function colorName(color) {
  const hex = nearestColor(color && typeof color === "object" ? color.hex : color, NAME_KEYS);
  return hex ? NAMES[NAME_KEYS.indexOf(hex)][1] : null;
}

// --- the colour picker's space (shared/ui/ColorPicker.jsx) ---------------------

// `#rrggbb` → [h 0..360, s 0..1, v 0..1]; null for what is not a colour.
// A gray keeps hue 0.
export function hexToHsv(hex) {
  const c = parseColor(hex);
  if (!c) return null;
  const r = c[0] / 255, g = c[1] / 255, b = c[2] / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  let h = 0;
  if (d) {
    if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) * 60;
    else if (max === g) h = ((b - r) / d + 2) * 60;
    else h = ((r - g) / d + 4) * 60;
  }
  return [h, max ? d / max : 0, max];
}

// [h, s, v] → `#rrggbb`, each clamped into range.
export function hsvToHex(h, s, v) {
  h = ((h % 360) + 360) % 360;
  s = Math.min(1, Math.max(0, s));
  v = Math.min(1, Math.max(0, v));
  const f = (n) => {
    const k = (n + h / 60) % 6;
    return v - v * s * Math.max(0, Math.min(k, 4 - k, 1));
  };
  return `#${hex2(f(5) * 255)}${hex2(f(3) * 255)}${hex2(f(1) * 255)}`;
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
