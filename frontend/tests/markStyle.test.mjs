// node --test tests/  (from frontend/) — how a highlight marks its passage
// and the highlight presets (shared/model/markStyle.js), the block a
// selection makes in a style (blockModel.js addHighlightAsBlock) and the
// viewer's highlight that carries it, and the two preferences.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  MARK_STYLES, MAX_PRESETS, MAX_PRESET_NAME, STYLE_NAMES, blockStyle, isLineStyle, markStyle, newPresetId, normalizePresets,
  presetFor, swatchOf, withStyle,
} from "../src/shared/model/markStyle.js";
import { addHighlightAsBlock, blocksToHighlights } from "../src/shared/model/blockModel.js";
import { PREFS } from "../src/app/prefDefs.js";

const POS = { pageNumber: 1, width: 600, height: 800, boundingRect: { x1: 1, y1: 2, x2: 3, y2: 4 }, rects: [{ x1: 1, y1: 2, x2: 3, y2: 4 }] };

test("the styles: the wash first, three lines named as the PDF markups; anything else reads as the wash", () => {
  assert.deepEqual(MARK_STYLES, ["highlight", "underline", "squiggly", "strikeout"]);
  for (const s of MARK_STYLES) assert.ok(STYLE_NAMES[s], s);
  for (const s of ["underline", "squiggly", "strikeout"]) { assert.equal(markStyle(s), s); assert.ok(isLineStyle(s), s); }
  for (const bad of [undefined, null, "", "Underline", "wavy", 3, {}]) assert.equal(markStyle(bad), "highlight", String(bad));
  assert.equal(isLineStyle("highlight"), false);
  assert.equal(isLineStyle("bogus"), false);
});

test("a block stores a line as its style, the wash as no key, an area never", () => {
  assert.deepEqual(withStyle({ color: "#ffe28fa6" }, "underline"), { color: "#ffe28fa6", style: "underline" });
  assert.deepEqual(withStyle({ color: "#ffe28fa6", style: "underline" }, "highlight"), { color: "#ffe28fa6" }, "back to the wash: the key goes");
  assert.deepEqual(withStyle({ color: "#ffe28fa6", style: "underline" }, "squiggly"), { color: "#ffe28fa6", style: "squiggly" });
  assert.deepEqual(withStyle({ pdf_position: { ...POS, area: true } }, "strikeout"), { pdf_position: { ...POS, area: true } }, "an area is a box");
  assert.deepEqual(withStyle(undefined, "underline"), { style: "underline" });
  assert.equal(blockStyle({ properties: { style: "strikeout", pdf_position: POS } }), "strikeout");
  assert.equal(blockStyle({ properties: { style: "strikeout", pdf_position: { ...POS, area: true } } }), "highlight", "an area reads as the wash");
  assert.equal(blockStyle({ properties: { style: "nope" } }), "highlight");
  assert.equal(blockStyle(null), "highlight");
});

test("a selection's highlight takes its style, and the viewer's highlight carries it", () => {
  const [line] = addHighlightAsBlock([], { id: "h1", quote: "q", position: POS, color: "#9bcdffa6", style: "squiggly" });
  assert.equal(line.properties.style, "squiggly");
  assert.equal(line.properties.color, "#9bcdffa6");
  const [wash] = addHighlightAsBlock([], { id: "h2", quote: "q", position: POS, color: "#9bcdffa6", style: "highlight" });
  assert.equal("style" in wash.properties, false, "the wash stores no style");
  const [plain] = addHighlightAsBlock([], { id: "h3", quote: "q", position: POS, color: "#9bcdffa6" });
  assert.equal("style" in plain.properties, false, "no style given: the wash");
  const [area] = addHighlightAsBlock([], { id: "h4", position: { ...POS, area: true }, color: "#9bcdffa6", style: "underline" });
  assert.equal("style" in area.properties, false);
  assert.deepEqual(blocksToHighlights([line, wash, area]).map((h) => [h.id, h.style]), [["h1", "squiggly"], ["h2", "highlight"], ["h4", "highlight"]]);
});

test("presets: a named style in a swatch, read from any stored shape", () => {
  const out = normalizePresets([
    { id: "def", name: "Definition", style: "underline", color: { hex: "#1D4ED8", alpha: 1 } },
    { id: "def", name: "Question", style: "squiggly", color: "#dc2626ff" }, // a duplicate id, a stored colour
    { name: "   ", style: "strikeout", color: "#ffe28fa6" }, // no name: the style's
    { name: "No colour", style: "underline", color: "red" }, // no readable colour: gone
    { name: "Bad style", style: "bold", color: { hex: "#aaebaa", alpha: 0.65 } }, // the wash
    "nonsense", null,
  ]);
  assert.equal(out.length, 4);
  assert.deepEqual(out[0], { id: "def", name: "Definition", style: "underline", color: { hex: "#1d4ed8", alpha: 1 } });
  assert.notEqual(out[1].id, "def", "ids are made unique");
  assert.deepEqual([out[1].name, out[1].style, out[1].color], ["Question", "squiggly", { hex: "#dc2626", alpha: 1 }]);
  assert.deepEqual([out[2].name, out[2].style, out[2].color], ["Strikethrough", "strikeout", { hex: "#ffe28f", alpha: 0.65 }]);
  assert.equal(out[3].style, "highlight");
  assert.equal(new Set(out.map((p) => p.id)).size, 4);
  assert.equal(normalizePresets("x"), undefined, "no list: the preference's default");
  assert.equal(normalizePresets({}), undefined);
  const many = normalizePresets(Array.from({ length: 20 }, (_, i) => ({ name: `p${i}`, color: "#ffe28f" })));
  assert.equal(many.length, MAX_PRESETS);
  assert.equal(normalizePresets([{ name: "x".repeat(99), color: "#ffe28f" }])[0].name.length, MAX_PRESET_NAME);
  assert.deepEqual(normalizePresets(out), out, "a normalized list is a fixed point");
});

test("a stored colour reads as a swatch, and a preset is found by its style and colour", () => {
  assert.deepEqual(swatchOf("#ffe28fa6"), { hex: "#ffe28f", alpha: 0.65 });
  assert.deepEqual(swatchOf("rgba(155, 205, 255, 0.5)"), { hex: "#9bcdff", alpha: 0.5 });
  assert.deepEqual(swatchOf({ hex: "#9BCDFF", alpha: 0.4 }), { hex: "#9bcdff", alpha: 0.4 });
  assert.equal(swatchOf("nope"), null);
  assert.equal(swatchOf(null), null);
  const presets = normalizePresets([{ id: "a", name: "A", style: "underline", color: { hex: "#1d4ed8", alpha: 1 } }]);
  assert.equal(presetFor(presets, "underline", "#1d4ed8ff")?.id, "a");
  assert.equal(presetFor(presets, "underline", { hex: "#1d4ed8", alpha: 1 })?.id, "a");
  assert.equal(presetFor(presets, "squiggly", "#1d4ed8ff"), null, "another style");
  assert.equal(presetFor(presets, "underline", "#1d4ed880"), null, "another alpha");
  assert.equal(presetFor([], "underline", "#1d4ed8ff"), null);
  const used = [{ id: newPresetId([]) }];
  assert.match(used[0].id, /^p[a-z0-9]{1,6}$/);
  assert.notEqual(newPresetId(used), used[0].id);
});

test("the style and the presets are account preferences that fall back to the wash and none", () => {
  const style = PREFS.highlightStyle, presets = PREFS.highlightPresets;
  assert.equal(style.key, "gamma-highlight-style");
  assert.equal(style.scope, "account");
  assert.equal(style.default, "highlight");
  assert.equal(style.parse("squiggly"), "squiggly");
  assert.equal(style.parse("bold"), undefined);
  assert.equal(presets.scope, "account");
  assert.deepEqual(presets.default, []);
  assert.deepEqual(presets.parse(JSON.stringify([{ id: "x", name: "N", style: "underline", color: "#1d4ed8" }])),
    [{ id: "x", name: "N", style: "underline", color: { hex: "#1d4ed8", alpha: 1 } }]);
  assert.equal(presets.parse("not json"), undefined);
});
