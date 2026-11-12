// node --test tests/  (from frontend/) — how a highlight marks its passage
// and the text highlighters (shared/model/markStyle.js), the block a
// selection makes in a style (blockModel.js addHighlightAsBlock) and the
// viewer's highlight that carries it, and the highlighters' preference.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  MARK_STYLES, MAX_PRESETS, MAX_PRESET_NAME, MAX_SHOWN_PRESETS, STARTER_PRESETS, STYLE_NAMES, addPreset, addStarters, blockStyle,
  duplicatePreset, effectivePresets, isLineStyle, markStyle, movePreset, newPresetId, normalizePresets, placePreset, presetFor, presetKey,
  presetKeys, presetName, removePreset, restorePreset, seedPresets, selectionTint, setPresetHidden, startersMissing, swapIntoStrip,
  swatchOf, withStyle,
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

const W = (hex, alpha = 0.65) => ({ hex, alpha });
const hl = (id, style = "highlight", color = W("#ffe28f"), extra = {}) => ({ id, name: "", style, color, ...extra });
const ids = (list) => list.map((p) => p.id + (p.hidden ? "*" : ""));

test("the list: any stored shape reads, names stay as typed or none, the shown ones first and at most four of them", () => {
  const out = normalizePresets([
    { id: "def", name: "Definition", style: "underline", color: { hex: "#1D4ED8", alpha: 1 } },
    { id: "def", name: "Question", style: "squiggly", color: "#dc2626ff" }, // a duplicate id, a stored colour
    { name: "   ", style: "strikeout", color: "#ffe28fa6" }, // no name: none, called by its look
    { name: "No colour", style: "underline", color: "red" }, // no readable colour: gone
    { name: "Bad style", style: "bold", color: W("#aaebaa") }, // the wash
    "nonsense", null,
  ]);
  assert.equal(out.length, 4);
  assert.deepEqual(out[0], { id: "def", name: "Definition", style: "underline", color: { hex: "#1d4ed8", alpha: 1 } });
  assert.notEqual(out[1].id, "def", "ids are made unique");
  assert.deepEqual([out[1].name, out[1].style, out[1].color], ["Question", "squiggly", { hex: "#dc2626", alpha: 1 }]);
  assert.deepEqual([out[2].name, out[2].style], ["", "strikeout"], "an unnamed one stays unnamed");
  assert.equal(presetName(out[2]), "Strikethrough in Yellow", "and is called by its look");
  assert.equal(out[3].style, "highlight");
  assert.equal(normalizePresets([{ name: "Two  words ", color: "#ffe28f" }])[0].name, "Two  words ", "a name as typed");
  assert.equal(normalizePresets([{ name: "x".repeat(99), color: "#ffe28f" }])[0].name.length, MAX_PRESET_NAME);
  assert.equal(normalizePresets("x"), undefined, "no list: the preference's default");
  assert.deepEqual(normalizePresets([]), [], "nothing set up yet stays so");
  assert.equal(normalizePresets(Array.from({ length: 20 }, (_, i) => hl(`p${i}`))).length, MAX_PRESETS);
  // order: shown first, a fifth shown one hidden, an all-hidden list shows its first
  const six = normalizePresets([hl("a"), hl("h1", "highlight", W("#aaebaa"), { hidden: true }), hl("b"), hl("c"), hl("d"), hl("e")]);
  assert.deepEqual(ids(six), ["a", "b", "c", "d", "e*", "h1*"]);
  assert.equal(MAX_SHOWN_PRESETS, 4);
  assert.deepEqual(ids(normalizePresets([hl("a", "highlight", W("#ffe28f"), { hidden: true }), hl("b", "underline", W("#ffe28f"), { hidden: true })])), ["a", "b*"]);
  assert.equal(normalizePresets([hl("a", "highlight", W("#ffe28f"), { hidden: "yes" })])[0].hidden, undefined, "hidden only when true");
  // a newer build's keys stay, while the item stays small
  const kept = normalizePresets([{ ...hl("a"), tag: "x", huge: "y".repeat(600) }])[0];
  assert.equal(kept.tag, "x");
  assert.equal("huge" in kept, false);
  for (const list of [out, six]) assert.deepEqual(normalizePresets(list), list, "a normalized list is a fixed point");
});

test("the seed: one wash in the released highlighter's colour, else the first swatch; a stored list wins", () => {
  assert.deepEqual(seedPresets("#aaebaaa6", W("#ffe28f")), [{ id: "h0", name: "", style: "highlight", color: { hex: "#aaebaa", alpha: 0.65 } }]);
  assert.deepEqual(seedPresets("", W("#9bcdff", 0.5))[0].color, { hex: "#9bcdff", alpha: 0.5 });
  assert.deepEqual(seedPresets("nope", null)[0].color, { hex: "#ffe28f", alpha: 0.65 });
  assert.deepEqual(effectivePresets([], "#aaebaaa6", W("#ffe28f")), seedPresets("#aaebaaa6", W("#ffe28f")));
  const mine = [hl("m", "underline")];
  assert.equal(effectivePresets(mine, "#aaebaaa6", W("#ffe28f")), mine);
});

test("keys follow the list: 1–9, the shown ones first", () => {
  const list = normalizePresets([hl("a"), hl("b", "underline"), hl("z", "squiggly", W("#ffe28f"), { hidden: true })]);
  assert.deepEqual(presetKeys(list), ["a", "b", "z"]);
  assert.equal(presetKey(list, "z"), 3);
  assert.equal(presetKey(list, "nope"), 0);
});

test("the edits: add, duplicate, move, place, hide, show, swap in, remove and put back", () => {
  let list = normalizePresets([hl("a"), hl("b", "underline")]);
  // add: a look already there is that one; a new one at the end, hidden on a full strip
  assert.deepEqual(addPreset(list, { style: "underline", color: W("#ffe28f") }), { list, id: "b", existed: true, hidden: false });
  const added = addPreset(list, { style: "strikeout", color: "#dc2626ff" });
  assert.equal(added.existed, false);
  assert.deepEqual(added.list.at(-1), { id: added.id, name: "", style: "strikeout", color: { hex: "#dc2626", alpha: 1 } });
  list = normalizePresets([hl("a"), hl("b", "underline"), hl("c", "squiggly"), hl("d", "strikeout")]);
  assert.equal(addPreset(list, { style: "highlight", color: W("#aaebaa") }).hidden, true);
  const twelve = Array.from({ length: 12 }, (_, i) => hl(`p${i}`, "highlight", W(`#0000${String(i).padStart(2, "0")}`)));
  assert.equal(addPreset(normalizePresets(twelve), { style: "underline", color: W("#123456") }), null);
  // duplicate: a copy right after, unnamed; on a full strip at the head of the hidden ones
  const named = normalizePresets([{ ...hl("a"), name: "Mine" }, hl("b", "underline")]);
  const dup = duplicatePreset(named, "a");
  assert.deepEqual(ids(dup.list), ["a", dup.id, "b"]);
  assert.equal(dup.list[1].name, "");
  const full = duplicatePreset(normalizePresets([...list, hl("h", "highlight", W("#aaebaa"), { hidden: true })]), "b");
  assert.equal(full.hidden, true);
  assert.deepEqual(ids(full.list), ["a", "b", "c", "d", `${full.id}*`, "h*"]);
  // move and place, within the group
  const withHidden = normalizePresets([hl("a"), hl("b", "underline"), hl("x", "squiggly", W("#ffe28f"), { hidden: true }),
    hl("y", "strikeout", W("#ffe28f"), { hidden: true })]);
  assert.deepEqual(ids(movePreset(withHidden, "a", 1)), ["b", "a", "x*", "y*"]);
  assert.equal(movePreset(withHidden, "b", 1), withHidden, "not across into the hidden ones");
  assert.deepEqual(ids(movePreset(withHidden, "y", -1)), ["a", "b", "y*", "x*"]);
  assert.deepEqual(ids(placePreset(withHidden, "a", 2)), ["b", "a", "x*", "y*"], "a drag's slot");
  assert.deepEqual(ids(placePreset(withHidden, "y", 0)), ["a", "b", "y*", "x*"]);
  // hide / show
  assert.deepEqual(ids(setPresetHidden(withHidden, "a", true)), ["b", "a*", "x*", "y*"]);
  assert.equal(setPresetHidden(normalizePresets([hl("a")]), "a", true), null, "the last shown one stays");
  assert.deepEqual(ids(setPresetHidden(withHidden, "y", false)), ["a", "b", "y", "x*"]);
  assert.equal(setPresetHidden(normalizePresets([...list, hl("h", "highlight", W("#aaebaa"), { hidden: true })]), "h", false), null, "not a fifth");
  // swap in: onto a full strip, its last one moving off
  const swapped = swapIntoStrip(normalizePresets([...list, hl("h", "highlight", W("#aaebaa"), { hidden: true })]), "h");
  assert.deepEqual(ids(swapped.list), ["a", "b", "c", "h", "d*"]);
  assert.equal(swapped.out.id, "d");
  // remove, then put back where it was though the list changed meanwhile
  const r = removePreset(withHidden, "b");
  assert.deepEqual(ids(r.list), ["a", "x*", "y*"]);
  assert.equal(removePreset(normalizePresets([hl("a"), hl("x", "underline", W("#ffe28f"), { hidden: true })]), "a"), null, "the last shown one stays");
  const meanwhile = normalizePresets([...r.list, hl("n", "highlight", W("#9bcdff"))]);
  assert.deepEqual(ids(restorePreset(meanwhile, r.item, r.index)), ["a", "b", "n", "x*", "y*"]);
  assert.equal(restorePreset(withHidden, r.item, r.index), withHidden, "one that is back already");
  // undone onto a strip that filled meanwhile: it comes back hidden, moving no other one off
  const four = normalizePresets([hl("a"), hl("b", "underline"), hl("c", "squiggly"), hl("d", "strikeout")]);
  const gone = removePreset(four, "b");
  const refilled = normalizePresets([...gone.list, hl("n", "highlight", W("#9bcdff"))]);
  assert.deepEqual(ids(restorePreset(refilled, gone.item, gone.index)), ["a", "c", "d", "n", "b*"]);
});

test("the starters: the issue's three meanings, once each, hidden past a full strip", () => {
  assert.deepEqual(STARTER_PRESETS.map((s) => [s.name, s.style]), [["Definition", "underline"], ["Question", "squiggly"], ["Disputed", "strikeout"]]);
  const seed = seedPresets("", W("#ffe28f"));
  assert.ok(startersMissing(seed));
  const r = addStarters(seed);
  assert.equal(r.added, 3);
  assert.deepEqual(r.list.map((p) => [p.name, p.style, !!p.hidden]),
    [["", "highlight", false], ["Definition", "underline", false], ["Question", "squiggly", false], ["Disputed", "strikeout", false]]);
  assert.equal(startersMissing(r.list), false);
  assert.equal(addStarters(r.list).added, 0, "not twice");
  const three = normalizePresets([hl("a"), hl("b", "highlight", W("#aaebaa")), hl("c", "highlight", W("#9bcdff"))]);
  assert.deepEqual(addStarters(three).list.map((p) => !!p.hidden), [false, false, false, false, true, true]);
  assert.equal(addStarters(seed, (n) => `«${n}»`).list[1].name, "«Definition»", "names translated as they are added");
});

test("an armed highlighter's selection tint: the wash it lays, a light tint of a line's tone", () => {
  assert.equal(selectionTint(hl("a", "highlight", W("#ffe28f"))), "#ffe28fa6");
  assert.equal(selectionTint(hl("a", "underline", W("#9bcdff"))), "#006bd640");
  assert.equal(selectionTint(null), "");
});

test("a stored colour reads as a swatch, and a highlighter is found by its look", () => {
  assert.deepEqual(swatchOf("#ffe28fa6"), { hex: "#ffe28f", alpha: 0.65 });
  assert.deepEqual(swatchOf("rgba(155, 205, 255, 0.5)"), { hex: "#9bcdff", alpha: 0.5 });
  assert.deepEqual(swatchOf({ hex: "#9BCDFF", alpha: 0.4 }), { hex: "#9bcdff", alpha: 0.4 });
  assert.equal(swatchOf("nope"), null);
  const list = normalizePresets([{ id: "a", name: "A", style: "underline", color: { hex: "#1d4ed8", alpha: 1 } }]);
  assert.equal(presetFor(list, "underline", "#1d4ed8ff")?.id, "a");
  assert.equal(presetFor(list, "squiggly", "#1d4ed8ff"), null, "another style");
  assert.equal(presetFor(list, "underline", "#1d4ed880"), null, "another alpha");
  assert.match(newPresetId([]), /^p[a-z0-9]{1,6}$/);
});

test("the highlighters are an account preference that falls back to none; the sticky style is gone", () => {
  const presets = PREFS.highlightPresets;
  assert.equal(presets.scope, "account");
  assert.deepEqual(presets.default, []);
  assert.deepEqual(presets.parse(JSON.stringify([{ id: "x", name: "N", style: "underline", color: "#1d4ed8" }])),
    [{ id: "x", name: "N", style: "underline", color: { hex: "#1d4ed8", alpha: 1 } }]);
  assert.equal(presets.parse("not json"), undefined);
  assert.equal(PREFS.highlightStyle, undefined);
  assert.equal(PREFS.highlighterColor.scope, "account", "the released colour stays, read for the seed");
});
