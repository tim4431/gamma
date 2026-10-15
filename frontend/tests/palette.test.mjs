// node --test tests/  (from frontend/) — the colour palette module
// (shared/model/palette.js) and the palette preference's codec.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  DEFAULT_HIGHLIGHT, DEFAULT_PALETTE, INK_COLORS, WASH_ALPHA, WASH_COLORS, colorName, darkPageColor, darkPageLine,
  hexColor, highlightColor, nearestColor, normalizeColor, normalizePalette, parseColor, withAlpha,
} from "../src/shared/model/palette.js";
import { PREFS, readProfile } from "../src/app/prefDefs.js";
import { DEFAULT_TOOLS } from "../src/ink/ink.js";

test("the defaults: the four classic highlight colours lead the wash set, every slot is lowercase hex", () => {
  assert.deepEqual(WASH_COLORS.slice(0, 4), ["#ffe28f", "#aaebaa", "#9bcdff", "#e6b4ff"]);
  for (const c of [...WASH_COLORS, ...INK_COLORS]) assert.match(c, /^#[0-9a-f]{6}$/);
  assert.equal(new Set([...WASH_COLORS, ...INK_COLORS]).size, WASH_COLORS.length + INK_COLORS.length, "no colour twice");
  assert.deepEqual(DEFAULT_PALETTE, { wash: WASH_COLORS, ink: INK_COLORS });
  assert.ok(Object.isFrozen(DEFAULT_PALETTE.wash));
  // the strip's first presets draw palette colours
  for (const t of DEFAULT_TOOLS) assert.ok((t.kind === "pen" ? INK_COLORS : WASH_COLORS).includes(t.color), t.id);
});

test("every colour Gamma ever stored parses, and the stored form is #rrggbbaa", () => {
  assert.deepEqual(parseColor("rgba(255, 226, 143, 0.65)"), [255, 226, 143, 0.65]);
  assert.deepEqual(parseColor("rgb(1,2,3)"), [1, 2, 3, 1]);
  assert.deepEqual(parseColor("#abc"), [170, 187, 204, 1]);
  assert.deepEqual(parseColor("#abcd"), [170, 187, 204, 221 / 255]);
  assert.deepEqual(parseColor(" #FFE28F "), [255, 226, 143, 1]);
  assert.deepEqual(parseColor("#ffe28fa6"), [255, 226, 143, 166 / 255]);
  assert.deepEqual(parseColor("rgba(300, 0, 0, 2)"), [255, 0, 0, 1], "channels clamp");
  for (const bad of ["red", "", null, undefined, 3, "#ggg", "rgba(1, 2)", "hsl(1, 2%, 3%)"]) assert.equal(parseColor(bad), null, String(bad));

  // the legacy palette strings keep their hue and alpha, only the spelling changes
  assert.equal(normalizeColor("rgba(255, 226, 143, 0.65)"), "#ffe28fa6");
  assert.equal(normalizeColor("rgba(170, 235, 170, 0.65)"), "#aaebaaa6");
  assert.equal(normalizeColor("#ffe28fa6"), "#ffe28fa6", "the stored form is a fixed point");
  assert.equal(normalizeColor("#FFE28F"), "#ffe28fff", "opaque when no alpha is given");
  assert.equal(normalizeColor("#abc"), "#aabbccff");
  assert.equal(normalizeColor("red"), null);
  assert.equal(hexColor("#ffe28fa6"), "#ffe28f");
  assert.equal(hexColor("rgba(1, 2, 3, 0.5)"), "#010203");
  assert.equal(withAlpha("#ffe28f", 0.5), "#ffe28f80");
  assert.equal(withAlpha("nope", 0.5), null);
});

test("a highlight stores a wash swatch at the wash alpha; the default is the first swatch", () => {
  assert.equal(WASH_ALPHA, 0.65);
  assert.equal(highlightColor(WASH_COLORS[0]), "#ffe28fa6");
  assert.equal(highlightColor(WASH_COLORS[0]), normalizeColor("rgba(255, 226, 143, 0.65)"), "the classic yellow, exactly");
  assert.equal(DEFAULT_HIGHLIGHT, "#ffe28fa6");
  assert.equal(highlightColor(undefined), null);
});

test("nearest colour and names: a listed colour is itself, a custom one borrows the nearest name", () => {
  for (const c of [...WASH_COLORS, ...INK_COLORS]) assert.equal(nearestColor(c, [...WASH_COLORS, ...INK_COLORS]), c);
  assert.equal(nearestColor("#DC2626", INK_COLORS), "#dc2626");
  assert.equal(nearestColor("#e11d48", INK_COLORS), "#dc2626", "rose reads as red");
  assert.equal(nearestColor("#000", INK_COLORS), "#1f1f1f");
  assert.equal(nearestColor("#ffe28fa6", WASH_COLORS), "#ffe28f", "alpha does not count");
  assert.equal(nearestColor("nope", INK_COLORS), null);
  assert.equal(nearestColor("#000", []), null);
  assert.equal(colorName("#ffe28f"), "Yellow");
  assert.equal(colorName("#f9a8d4"), "Pink");
  assert.equal(colorName("#1f1f1f"), "Black");
  assert.equal(colorName("#fde047"), "Yellow", "the older highlighter yellow a preset may still carry");
  assert.equal(colorName("#86efac"), "Mint");
  assert.equal(colorName("#fef08a"), "Yellow", "a paler yellow");
  assert.equal(colorName("rgb(1, 2, 3)"), "Black", "any colour has a name");
  assert.equal(colorName(""), null);
});

test("a dark page draws a saturated mid-tone of the same hue, mostly transparent; neutrals stay neutral", () => {
  const hue = (rgba) => {
    const [r, g, b] = rgba.match(/\d+/g).map(Number).map((v) => v / 255);
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    if (!d) return null;
    const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return h * 60;
  };
  const yellow = darkPageColor("#ffe28fa6");
  assert.match(yellow, /^rgba\(\d+, \d+, \d+, 0\.3\)$/);
  assert.ok(Math.abs(hue(yellow) - 46) < 3, `yellow keeps its hue: ${yellow}`);
  const [r, g, b] = yellow.match(/\d+/g).map(Number);
  assert.ok(r > 200 && g > 150 && b < 80, `a strong yellow, not a pastel: ${yellow}`);
  assert.equal(darkPageLine("#ffe28f"), yellow.replace("0.3)", "0.75)"), "the outline is the same colour, stronger");
  const blue = darkPageColor("#9bcdff");
  assert.ok(Math.abs(hue(blue) - 210) < 3, `blue keeps its hue: ${blue}`);
  const gray = darkPageColor("#d4d4d8");
  const [gr, gg, gb] = gray.match(/\d+/g).map(Number);
  assert.ok(Math.max(gr, gg, gb) - Math.min(gr, gg, gb) < 12, `a neutral stays neutral: ${gray}`);
  assert.equal(darkPageColor("rgba(255, 0, 0, 0.4)"), darkPageColor("#ff0000"), "alpha and spelling do not count");
  assert.equal(darkPageColor("nope"), null);
});

test("the palette preference: slots fill from the defaults, counts are fixed, bad values fall back", () => {
  assert.deepEqual(normalizePalette({}), DEFAULT_PALETTE);
  const mine = normalizePalette({ wash: ["#FF0000", 3, "red"], ink: null, extra: 1 });
  assert.equal(mine.wash[0], "#ff0000", "lowercased");
  assert.deepEqual(mine.wash.slice(1), WASH_COLORS.slice(1), "a bad or missing slot is its default");
  assert.deepEqual(mine.ink, INK_COLORS);
  assert.deepEqual(Object.keys(mine), ["wash", "ink"]);
  assert.equal(normalizePalette({ wash: [...WASH_COLORS, "#000000"] }).wash.length, WASH_COLORS.length, "no extra slots");
  for (const bad of [null, "x", 3, [], undefined]) assert.equal(normalizePalette(bad), undefined, String(bad));

  const def = PREFS.palette;
  assert.equal(def.scope, "account");
  assert.deepEqual(def.default, DEFAULT_PALETTE);
  assert.deepEqual(def.parse(def.serialize({ wash: ["#123456"] })).wash[0], "#123456");
  assert.equal(def.parse("not json"), undefined);
  assert.deepEqual(readProfile({ palette: { wash: ["#123456"] } }).palette.wash[0], "#123456");
  assert.equal(readProfile({ palette: "nope" }).palette, undefined, "a profile value of the wrong type is dropped");
});
