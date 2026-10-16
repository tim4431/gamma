// node --test tests/  (from frontend/) — the colour palette module
// (shared/model/palette.js) and the palette preference's codec.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  DEFAULT_HIGHLIGHT, DEFAULT_PALETTE, INK_COLORS, INK_HIGHLIGHT_ALPHA, INK_HIGHLIGHT_COLORS, MAX_GROUPS, MAX_GROUP_COLORS, PICKERS,
  WASH_ALPHA, WASH_COLORS, colorName,
  darkPageColor, darkPageLine, hexColor, hexToHsv, highlightColor, hsvToHex, isDefaultPalette, nearestColor, newGroupId,
  normalizeColor, normalizePalette, normalizeSwatch, parseColor, pickerColors, swatchCss, withAlpha,
} from "../src/shared/model/palette.js";
import { PREFS, readProfile } from "../src/app/prefDefs.js";
import { DEFAULT_TOOLS, HIGHLIGHTER_OPACITY } from "../src/ink/ink.js";

const clone = (v) => JSON.parse(JSON.stringify(v));

test("the defaults: two groups, the four classic highlight colours leading the wash group, every picker filled", () => {
  assert.deepEqual(WASH_COLORS.slice(0, 4), ["#ffe28f", "#aaebaa", "#9bcdff", "#e6b4ff"]);
  for (const c of [...WASH_COLORS, ...INK_COLORS]) assert.match(c, /^#[0-9a-f]{6}$/);
  assert.equal(new Set([...WASH_COLORS, ...INK_COLORS]).size, WASH_COLORS.length + INK_COLORS.length, "no colour twice");
  assert.deepEqual(DEFAULT_PALETTE.groups.map((g) => [g.id, g.name, g.colors.length]),
    [["highlight", "Highlight", 4], ["ink-highlight", "Ink highlight", 8], ["pen", "Pen", 14]]);
  assert.deepEqual(INK_HIGHLIGHT_COLORS.slice(0, 4), WASH_COLORS, "the ink highlights start with the text highlight colours");
  assert.deepEqual(DEFAULT_PALETTE.groups[0].colors[0], { hex: "#ffe28f", alpha: WASH_ALPHA });
  assert.deepEqual(DEFAULT_PALETTE.groups[1].colors[4], { hex: "#f9a8d4", alpha: INK_HIGHLIGHT_ALPHA });
  assert.deepEqual(DEFAULT_PALETTE.groups[2].colors[0], { hex: "#1f1f1f", alpha: 1 });
  assert.deepEqual(DEFAULT_PALETTE.pickers, { highlight: "highlight", highlighter: "ink-highlight", pen: "pen", text: "pen" });
  assert.deepEqual(PICKERS, Object.keys(DEFAULT_PALETTE.pickers));
  assert.ok(Object.isFrozen(DEFAULT_PALETTE.groups[0].colors[0]));
  assert.ok(isDefaultPalette(DEFAULT_PALETTE) && isDefaultPalette(clone(DEFAULT_PALETTE)));
  // the strip's first presets draw palette colours, and a highlighter stroke's usual opacity is the group's
  for (const t of DEFAULT_TOOLS) assert.ok((t.kind === "pen" ? INK_COLORS : INK_HIGHLIGHT_COLORS).includes(t.color), t.id);
  assert.equal(HIGHLIGHTER_OPACITY, INK_HIGHLIGHT_ALPHA);
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

test("a highlight stores a swatch at the swatch's alpha; a bare hex takes the wash alpha", () => {
  assert.equal(WASH_ALPHA, 0.65);
  assert.equal(highlightColor(WASH_COLORS[0]), "#ffe28fa6");
  assert.equal(highlightColor({ hex: "#ffe28f", alpha: 0.65 }), "#ffe28fa6");
  assert.equal(highlightColor({ hex: "#ff8800", alpha: 0.5 }), "#ff880080");
  assert.equal(highlightColor(WASH_COLORS[0]), normalizeColor("rgba(255, 226, 143, 0.65)"), "the classic yellow, exactly");
  assert.equal(DEFAULT_HIGHLIGHT, "#ffe28fa6");
  assert.equal(highlightColor(undefined), null);
  assert.equal(swatchCss({ hex: "#1f1f1f", alpha: 1 }), "#1f1f1fff");
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
  assert.equal(colorName({ hex: "#f9a8d4", alpha: 0.3 }), "Pink", "a swatch names its hex");
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

test("a swatch: lowercase hex with an alpha in 0.1..1, a bare hex at the given default", () => {
  assert.deepEqual(normalizeSwatch("#FF8800", 0.65), { hex: "#ff8800", alpha: 0.65 });
  assert.deepEqual(normalizeSwatch({ hex: "#ff8800" }), { hex: "#ff8800", alpha: 1 });
  assert.deepEqual(normalizeSwatch({ hex: "#ff8800", alpha: 0.333 }), { hex: "#ff8800", alpha: 0.33 });
  assert.deepEqual(normalizeSwatch({ hex: "#ff8800", alpha: 0 }), { hex: "#ff8800", alpha: 0.1 }, "never invisible");
  assert.deepEqual(normalizeSwatch({ hex: "#ff8800", alpha: "2" }), { hex: "#ff8800", alpha: 1 });
  assert.deepEqual(normalizeSwatch({ hex: "#ff8800", alpha: "x" }, 0.5), { hex: "#ff8800", alpha: 0.5 });
  for (const bad of ["red", "#abc", { hex: 3 }, null, 7, {}]) assert.equal(normalizeSwatch(bad), null, JSON.stringify(bad));
});

test("the palette preference: groups keep valid swatches and ids, pickers name existing groups, junk falls back", () => {
  assert.deepEqual(normalizePalette(clone(DEFAULT_PALETTE)), clone(DEFAULT_PALETTE));
  assert.deepEqual(normalizePalette({}), clone(DEFAULT_PALETTE), "nothing usable → the defaults");
  assert.deepEqual(normalizePalette({ groups: [{ id: "a", name: "Empty", colors: [] }] }), clone(DEFAULT_PALETTE), "no colour anywhere → the defaults");
  for (const bad of [null, "x", 3, [], undefined]) assert.equal(normalizePalette(bad), undefined, String(bad));

  const mine = normalizePalette({
    groups: [
      { id: "work", name: "  Work  ", colors: [{ hex: "#FF0000", alpha: 0.5 }, "#00ff00", "junk", { hex: "#0000ff", alpha: 9 }] },
      { id: "bad id!", name: "", colors: ["#123456"] },
      { id: "work", name: "Dup", colors: ["#654321"] },
      "not a group",
      { id: "empty", name: "Empty", colors: [] },
    ],
    pickers: { highlight: ["work", "nope"], highlighter: "nope", pen: 3, text: "empty" },
  });
  assert.equal(mine.groups.length, 4);
  assert.deepEqual(mine.groups[0], { id: "work", name: "Work", colors: [{ hex: "#ff0000", alpha: 0.5 }, { hex: "#00ff00", alpha: 1 }, { hex: "#0000ff", alpha: 1 }] });
  assert.match(mine.groups[1].id, /^g[a-z0-9]{6}$/, "a bad id is replaced");
  assert.equal(mine.groups[1].name, "Group", "a blank name gets one");
  assert.notEqual(mine.groups[2].id, "work", "a duplicate id is replaced");
  assert.deepEqual(mine.groups[3], { id: "empty", name: "Empty", colors: [] }, "an empty group is kept while it is being edited");
  assert.deepEqual(mine.pickers, { highlight: "work", highlighter: "work", pen: "work", text: "empty" },
    "a list keeps its first; an unknown or bad id falls to the picker's default group, else the first group with a colour; an empty group may be chosen");

  const many = normalizePalette({ groups: Array.from({ length: 20 }, (_, i) => ({ id: `g${i}`, name: `G${i}`, colors: Array(40).fill("#101010") })) });
  assert.equal(many.groups.length, MAX_GROUPS);
  assert.equal(many.groups[0].colors.length, MAX_GROUP_COLORS);
  assert.ok(!isDefaultPalette(mine) && !isDefaultPalette(many));

  const ids = new Set(["highlight", "pen"]);
  for (let i = 0; i < 50; i++) assert.ok(!ids.has(newGroupId(DEFAULT_PALETTE.groups)));
});

test("the first shape of the preference, {wash, ink}, becomes the two default groups with its colours", () => {
  const wash = [...WASH_COLORS];
  wash[0] = "#ff8800";
  const moved = normalizePalette({ wash, ink: ["#000000", 3] });
  assert.deepEqual(moved.groups.map((g) => g.id), ["highlight", "ink-highlight", "pen"]);
  assert.deepEqual(moved.groups[0].colors[0], { hex: "#ff8800", alpha: WASH_ALPHA });
  assert.deepEqual(moved.groups[0].colors.slice(1), DEFAULT_PALETTE.groups[0].colors.slice(1));
  assert.deepEqual(moved.groups[1].colors[0], { hex: "#ff8800", alpha: INK_HIGHLIGHT_ALPHA }, "the wash slots fill the ink highlights too");
  assert.deepEqual(moved.groups[1].colors.slice(1), DEFAULT_PALETTE.groups[1].colors.slice(1));
  assert.deepEqual(moved.groups[2].colors[0], { hex: "#000000", alpha: 1 });
  assert.deepEqual(moved.groups[2].colors.slice(1), DEFAULT_PALETTE.groups[2].colors.slice(1), "a bad slot is its default");
  assert.deepEqual(moved.pickers, DEFAULT_PALETTE.pickers);
  // the shape in between, pickers as lists, keeps each list's first group
  const listed = normalizePalette({ ...clone(DEFAULT_PALETTE), pickers: { highlight: ["pen", "highlight"] } });
  assert.deepEqual(listed.pickers, { ...DEFAULT_PALETTE.pickers, highlight: "pen" });
});

test("a picker's colours: its group's, else the first group's with any; never empty", () => {
  assert.deepEqual(pickerColors(DEFAULT_PALETTE, "highlight"), DEFAULT_PALETTE.groups[0].colors);
  assert.deepEqual(pickerColors(DEFAULT_PALETTE, "highlighter"), DEFAULT_PALETTE.groups[1].colors);
  assert.deepEqual(pickerColors(DEFAULT_PALETTE, "text"), DEFAULT_PALETTE.groups[2].colors);
  assert.deepEqual(pickerColors(undefined, "pen"), DEFAULT_PALETTE.groups[2].colors, "no palette → the defaults");
  const three = normalizePalette({ groups: [
    { id: "a", name: "A", colors: ["#111111"] }, { id: "highlight", name: "H", colors: ["#222222"] }, { id: "b", name: "B", colors: ["#333333", "#444444"] },
  ], pickers: { highlight: "b", pen: "nope" } });
  assert.deepEqual(pickerColors(three, "highlight").map((c) => c.hex), ["#333333", "#444444"]);
  assert.deepEqual(pickerColors(three, "highlighter").map((c) => c.hex), ["#111111"], "no 'ink-highlight' group: the first group with a colour");
  assert.deepEqual(pickerColors(three, "pen").map((c) => c.hex), ["#111111"], "an unknown id: the same");
  const emptied = normalizePalette({ groups: [{ id: "x", name: "X", colors: ["#555555"] }, { id: "e", name: "E", colors: [] }], pickers: { text: "e" } });
  assert.equal(emptied.pickers.text, "e");
  assert.deepEqual(pickerColors(emptied, "text").map((c) => c.hex), ["#555555"], "a chosen group with no colours does not empty the picker");
});

test("the picker's space: hex ↔ hue, saturation, value round-trips; grays keep hue 0; inputs clamp", () => {
  assert.deepEqual(hexToHsv("#ff0000"), [0, 1, 1]);
  assert.deepEqual(hexToHsv("#00ff00"), [120, 1, 1]);
  assert.deepEqual(hexToHsv("#0000ff"), [240, 1, 1]);
  assert.deepEqual(hexToHsv("#ffffff"), [0, 0, 1]);
  assert.deepEqual(hexToHsv("#000000"), [0, 0, 0]);
  assert.equal(hexToHsv("nope"), null);
  for (const hex of [...WASH_COLORS, ...INK_COLORS, "#ff8800", "#123456"]) assert.equal(hsvToHex(...hexToHsv(hex)), hex, hex);
  assert.equal(hsvToHex(360, 1, 1), "#ff0000", "a full turn is red again");
  assert.equal(hsvToHex(-120, 1, 1), "#0000ff", "negative hues wrap");
  assert.equal(hsvToHex(0, 2, -1), "#000000", "saturation and value clamp");
  const [h] = hexToHsv("#ffe28f");
  assert.ok(Math.abs(h - 44.5) < 1, `yellow's hue: ${h}`);
});

test("the preference entry: account scope, the codec round trip, a profile value of the wrong type dropped", () => {
  const def = PREFS.palette;
  assert.equal(def.scope, "account");
  assert.deepEqual(def.default, DEFAULT_PALETTE);
  const stored = def.parse(def.serialize({ groups: [{ id: "a", name: "A", colors: [{ hex: "#123456", alpha: 0.4 }] }], pickers: { highlight: "a" } }));
  assert.deepEqual(stored.groups[0].colors, [{ hex: "#123456", alpha: 0.4 }]);
  assert.equal(stored.pickers.highlight, "a");
  assert.equal(def.parse("not json"), undefined);
  assert.deepEqual(readProfile({ palette: { wash: ["#123456"] } }).palette.groups[0].colors[0], { hex: "#123456", alpha: WASH_ALPHA }, "the old shape still reads");
  assert.equal(readProfile({ palette: "nope" }).palette, undefined);
});
