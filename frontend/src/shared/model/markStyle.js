// How a text highlight marks its passage (docs/dev/api.md "The highlight
// shape"): a wash over the text, the default, or a line in its colour —
// an underline, a wavy underline or a strikethrough. A highlight block
// stores a line as `properties.style`: "underline", "squiggly" or
// "strikeout", the names of the PDF annotations the annotated PDF writes
// for them; no style is the wash. An area highlight is a box whatever it
// stores. Beside the styles, the account's highlight presets (the
// `highlightPresets` preference): a named style and colour, offered with
// the colours wherever a highlight is made or changed. Pure — no React,
// no DOM — so it runs under node --test.
import { T } from "../i18n/locales.js";
import { hexColor, normalizeSwatch, parseColor } from "./palette.js";

export const MARK_STYLES = ["highlight", "underline", "squiggly", "strikeout"];
// English names (T() keys — the caller translates with t()).
export const STYLE_NAMES = Object.freeze({
  highlight: T("Highlight"), underline: T("Underline"), squiggly: T("Wavy underline"), strikeout: T("Strikethrough"),
});

// A stored or picked value as a style: one of MARK_STYLES, the wash for
// anything else (an older block, a hand-edited one).
export const markStyle = (value) => (MARK_STYLES.includes(value) ? value : "highlight");
// Whether `style` draws a line rather than a wash.
export const isLineStyle = (style) => style !== "highlight" && MARK_STYLES.includes(style);
// A highlight block's style; an area's is always the wash's box.
export const blockStyle = (b) => (b?.properties?.pdf_position?.area ? "highlight" : markStyle(b?.properties?.style));

// A highlight's properties with `style` set: a line is stored, the wash is
// no key at all (so a block that never had a style keeps none), and an
// area takes none.
export function withStyle(props, style) {
  const { style: _old, ...rest } = props || {};
  return isLineStyle(style) && !rest.pdf_position?.area ? { ...rest, style } : rest;
}

// --- presets ----------------------------------------------------------------------

export const MAX_PRESETS = 12;
export const MAX_PRESET_NAME = 40;
const PRESET_ID = /^[A-Za-z0-9_-]{1,24}$/;

// A fresh preset id no preset in `presets` has.
export function newPresetId(presets) {
  const used = new Set((presets || []).map((p) => p.id));
  let id;
  do { id = "p" + Math.random().toString(36).slice(2, 8); } while (used.has(id));
  return id;
}

// A colour as a swatch {hex, alpha}: a swatch as itself, a stored colour
// (#rrggbbaa, any spelling palette.js reads) at its own alpha; null for
// what is neither.
export function swatchOf(color) {
  if (typeof color !== "string") return normalizeSwatch(color);
  const c = parseColor(color);
  return c ? normalizeSwatch({ hex: hexColor(color), alpha: c[3] }) : null;
}

// A stored preset list (possibly hand-edited) → [{id, name, style, color:
// {hex, alpha}}], the colour a swatch as the palette keeps one, so a preset
// stores as a swatch does (palette.js highlightColor). A preset without a
// readable colour goes; one without a name is named by its style; ids are
// made unique; at most MAX_PRESETS. undefined for what is no list (the
// preference falls back to none).
export function normalizePresets(value) {
  if (!Array.isArray(value)) return undefined;
  const out = [];
  for (const p of value) {
    if (!p || typeof p !== "object") continue;
    const color = swatchOf(p.color);
    if (!color) continue;
    const style = markStyle(p.style);
    const name = (typeof p.name === "string" ? p.name.trim().slice(0, MAX_PRESET_NAME) : "") || STYLE_NAMES[style];
    const id = typeof p.id === "string" && PRESET_ID.test(p.id) && !out.some((q) => q.id === p.id) ? p.id : newPresetId(out);
    out.push({ id, name, style, color });
    if (out.length >= MAX_PRESETS) break;
  }
  return out;
}

// The preset that is exactly `style` in `color` (a stored #rrggbbaa or a
// swatch), for a picker to mark; null when none is.
export function presetFor(presets, style, color) {
  const want = swatchOf(color);
  return (presets || []).find((p) => p.style === markStyle(style) && want && p.color.hex === want.hex
    && Math.abs(p.color.alpha - want.alpha) < 0.005) || null;
}
