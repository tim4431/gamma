// How a text highlight marks its passage (docs/dev/api.md "The highlight
// shape"): a wash over the text, the default, or a line in its colour —
// an underline, a wavy underline or a strikethrough. A highlight block
// stores a line as `properties.style`: "underline", "squiggly" or
// "strikeout", the names of the PDF annotations the annotated PDF writes
// for them; no style is the wash. An area highlight is a box whatever it
// stores. Beside the styles, the account's text highlighters (the
// `highlightPresets` preference, docs/dev/settings.md "Text
// highlighters"): an ordered list of a style and a colour each, named if
// the person likes, the first MAX_SHOWN_PRESETS of them buttons on the
// viewer's strip and in the selection popup. Pure — no React, no DOM — so
// it runs under node --test.
import { T, t } from "../i18n/i18n.js";
import { colorName, hexColor, highlightColor, lineColor, normalizeSwatch, parseColor, withAlpha } from "./palette.js";

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

// What a style in a colour (a stored #rrggbbaa or a swatch) is called:
// "Underline in Blue", translated.
export const markName = (style, color) => (style === "highlight"
  ? t("Highlight in {color}", { color: t(colorName(color)) })
  : t("{style} in {color}", { style: t(STYLE_NAMES[style]), color: t(colorName(color)) }));

// --- text highlighters ------------------------------------------------------------

// A highlighter: {id, name, style, color: {hex, alpha}, hidden?}. `name` is
// "" for one called by its look; `color` a swatch, concrete like a
// swatch's (a palette edit never moves it); `hidden` off the strip, still
// in the list. The list's order is the strip's, the popup's, Settings' and
// the keys' (1–9): the shown ones first, then the hidden ones.
export const MAX_PRESETS = 12;
export const MAX_SHOWN_PRESETS = 4;
export const MAX_PRESET_NAME = 40;
const PRESET_ID = /^[A-Za-z0-9_-]{1,24}$/;
// The keys a highlighter is known by here; any other key it carries is kept
// as it is while it serialises under PRESET_MAX_BYTES (ink.js's rule for
// the pen presets), so an older build that edits one never strips what a
// newer one wrote.
const KNOWN_PRESET_KEYS = new Set(["id", "name", "style", "color", "hidden"]);
const PRESET_MAX_BYTES = 512;
const utf8Bytes = (s) => new TextEncoder().encode(s).length;

// A fresh id no highlighter in `presets` has.
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

// A stored list (the profile, possibly hand-edited, from an older or a
// newer build) → a valid one, the only shape any write stores: each item
// with a readable colour (others go), a known style (else the wash), an id
// of its own, its name as typed (up to MAX_PRESET_NAME, "" for none),
// `hidden` only when true, and the keys this build does not know; at most
// MAX_PRESETS. Then the shown ones first and the hidden ones after, each in
// their order, any shown past MAX_SHOWN_PRESETS hidden, and the first
// shown when every one is hidden. Idempotent, so two devices converge.
// [] stays [] (nothing set up yet: the seed stands in, effectivePresets);
// undefined for what is no list (the preference's default).
export function normalizePresets(value) {
  if (!Array.isArray(value)) return undefined;
  const out = [];
  const seen = new Set();
  for (const p of value) {
    if (!p || typeof p !== "object") continue;
    const color = swatchOf(p.color);
    if (!color) continue;
    let id = typeof p.id === "string" && PRESET_ID.test(p.id) ? p.id : newPresetId(out);
    while (seen.has(id)) id = newPresetId(out);
    seen.add(id);
    const raw = typeof p.name === "string" ? p.name.slice(0, MAX_PRESET_NAME) : "";
    const item = { id, name: raw.trim() ? raw : "", style: markStyle(p.style), color, ...(p.hidden === true ? { hidden: true } : {}) };
    for (const [key, v] of Object.entries(p)) {
      if (KNOWN_PRESET_KEYS.has(key) || v === undefined) continue;
      if (utf8Bytes(JSON.stringify({ ...item, [key]: v })) > PRESET_MAX_BYTES) continue;
      item[key] = v;
    }
    out.push(item);
    if (out.length >= MAX_PRESETS) break;
  }
  let shown = out.filter((p) => !p.hidden);
  const hidden = out.filter((p) => p.hidden);
  if (!shown.length && hidden.length) {
    const { hidden: _h, ...first } = hidden.shift();
    shown = [first];
  }
  const over = shown.slice(MAX_SHOWN_PRESETS).map((p) => ({ ...p, hidden: true }));
  return [...shown.slice(0, MAX_SHOWN_PRESETS), ...over, ...hidden];
}

const FALLBACK_SWATCH = Object.freeze({ hex: "#ffe28f", alpha: 0.65 });
// The list an account without one has: one wash highlighter in the colour
// the text highlighter had (`seedColor`, the released `highlighterColor`
// preference, #rrggbbaa or ""), else `firstSwatch` (the Text highlights
// group's first). Deterministic; stored only by the first edit.
export const seedPresets = (seedColor, firstSwatch) => [{
  id: "h0", name: "", style: "highlight", color: swatchOf(seedColor) || swatchOf(firstSwatch) || { ...FALLBACK_SWATCH },
}];
// The list in effect: the stored one, or the seed while none is stored.
export const effectivePresets = (stored, seedColor, firstSwatch) => (stored?.length ? stored : seedPresets(seedColor, firstSwatch));

export const shownPresets = (list) => (list || []).filter((p) => !p.hidden);
export const hiddenPresets = (list) => (list || []).filter((p) => p.hidden);
// The ids keys 1–9 press, in list order.
export const presetKeys = (list) => (list || []).slice(0, 9).map((p) => p.id);
// A highlighter's key (1–9), or 0 for none.
export const presetKey = (list, id) => presetKeys(list).indexOf(id) + 1;
// A highlighter's look in words, and what it is called: its name, else its
// look, which follows its edits.
export const presetLook = (p) => markName(p.style, highlightColor(p.color));
export const presetName = (p) => p.name || presetLook(p);

// The highlighter that is exactly `style` in `color` (a stored #rrggbbaa or a
// swatch): what marks a highlight's own look in a menu; null when none is.
export function presetFor(presets, style, color) {
  const want = swatchOf(color);
  return (presets || []).find((p) => p.style === markStyle(style) && want && p.color.hex === want.hex
    && Math.abs(p.color.alpha - want.alpha) < 0.005) || null;
}

// The edits: each pure, each through normalizePresets, each null where it
// cannot apply (a full list, the last shown one).

// `look` ({style, color}) as a new unnamed highlighter at the end, hidden
// when the strip is full → {list, id, existed, hidden}; the one that has
// that look already when one does (existed); null at MAX_PRESETS.
export function addPreset(list, look, { name = "" } = {}) {
  const have = presetFor(list, look.style, look.color);
  if (have) return { list, id: have.id, existed: true, hidden: !!have.hidden };
  const color = swatchOf(look.color);
  if (!color || list.length >= MAX_PRESETS) return null;
  const hidden = shownPresets(list).length >= MAX_SHOWN_PRESETS;
  const id = newPresetId(list);
  const item = { id, name, style: markStyle(look.style), color, ...(hidden ? { hidden: true } : {}) };
  return { list: normalizePresets([...list, item]), id, existed: false, hidden };
}

// An unnamed copy of `id` right after it (at the head of the hidden ones when
// the strip is full) → {list, id, hidden}; null at MAX_PRESETS.
export function duplicatePreset(list, id) {
  const at = list.findIndex((p) => p.id === id);
  if (at < 0 || list.length >= MAX_PRESETS) return null;
  const { hidden: _h, ...src } = list[at];
  const hide = !!list[at].hidden || shownPresets(list).length >= MAX_SHOWN_PRESETS;
  const copy = { ...src, id: newPresetId(list), name: "", ...(hide ? { hidden: true } : {}) };
  const next = [...list];
  next.splice(hide && !list[at].hidden ? shownPresets(list).length : at + 1, 0, copy);
  return { list: normalizePresets(next), id: copy.id, hidden: hide };
}

// `id` a place up (-1) or down (+1) within its own group, shown or hidden;
// the list as it was at an end.
export function movePreset(list, id, by) {
  const at = list.findIndex((p) => p.id === id);
  const to = at + by;
  if (at < 0 || to < 0 || to >= list.length || !!list[to].hidden !== !!list[at].hidden) return list;
  const next = [...list];
  [next[at], next[to]] = [next[to], next[at]];
  return normalizePresets(next);
}

// `id` to slot `to` (0 … its group's length) within its own group, as a
// drag in Settings drops it; the list as it was for another group's slot.
export function placePreset(list, id, to) {
  const p = list.find((x) => x.id === id);
  if (!p) return list;
  const group = list.filter((x) => !!x.hidden === !!p.hidden);
  const rest = group.filter((x) => x.id !== id);
  const at = Math.max(0, Math.min(to > group.indexOf(p) ? to - 1 : to, rest.length));
  rest.splice(at, 0, p);
  return normalizePresets(p.hidden ? [...shownPresets(list), ...rest] : [...rest, ...hiddenPresets(list)]);
}

// `id` off the strip (to the head of the hidden ones) or onto it (to the end
// of the shown ones); null for the last shown one, or a fifth.
export function setPresetHidden(list, id, hidden) {
  const p = list.find((x) => x.id === id);
  if (!p) return null;
  if (!!p.hidden === hidden) return list;
  const shown = shownPresets(list), rest = hiddenPresets(list);
  if (hidden) {
    if (shown.length <= 1) return null;
    return normalizePresets([...shown.filter((x) => x.id !== id), { ...p, hidden: true }, ...rest]);
  }
  if (shown.length >= MAX_SHOWN_PRESETS) return null;
  const { hidden: _h, ...on } = p;
  return normalizePresets([...shown, on, ...rest.filter((x) => x.id !== id)]);
}

// Hidden `id` onto the strip, in place of its last shown one when it is full,
// which goes to the head of the hidden ones → {list, out: the one moved off,
// or null}.
export function swapIntoStrip(list, id) {
  const p = list.find((x) => x.id === id);
  if (!p?.hidden) return { list, out: null };
  const shown = shownPresets(list), rest = hiddenPresets(list).filter((x) => x.id !== id);
  const { hidden: _h, ...on } = p;
  if (shown.length < MAX_SHOWN_PRESETS) return { list: normalizePresets([...shown, on, ...rest]), out: null };
  const out = shown[shown.length - 1];
  return { list: normalizePresets([...shown.slice(0, -1), on, { ...out, hidden: true }, ...rest]), out };
}

// `id` out of the list → {list, item, index}, for an Undo to put back; null
// for the last shown one: a written list never empties.
export function removePreset(list, id) {
  const index = list.findIndex((p) => p.id === id);
  if (index < 0) return null;
  const item = list[index];
  if (!item.hidden && shownPresets(list).length <= 1) return null;
  return { list: normalizePresets(list.filter((p) => p.id !== id)), item, index };
}

// The one highlighter a remove took, back at its place in the list as it is
// now (an edit synced in meanwhile stays), or at the head of the hidden ones
// when the strip has filled since, so it moves no other one off; the list as
// it is when one of its id is back already or no room is left.
export function restorePreset(list, item, index) {
  if (!item || list.some((p) => p.id === item.id) || list.length >= MAX_PRESETS) return list;
  const next = [...list];
  const shown = shownPresets(list).length;
  if (!item.hidden && shown >= MAX_SHOWN_PRESETS) next.splice(shown, 0, { ...item, hidden: true });
  else next.splice(Math.min(index, next.length), 0, item);
  return normalizePresets(next);
}

// The colour a selection is drawn in while a highlighter is armed
// (PdfViewer's --selection-color): the wash it lays, or a light tint of its
// line's tone, so an armed underline does not preview as a wash.
export function selectionTint(p) {
  if (!p) return "";
  return p.style === "highlight" ? highlightColor(p.color) : withAlpha(lineColor(p.color.hex), 0.25);
}
