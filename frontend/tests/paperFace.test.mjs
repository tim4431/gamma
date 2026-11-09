// The paper's own typeface for a translated block (src/pdf/paperFace.js).
import test from "node:test";
import assert from "node:assert/strict";
import { covers, fontLive, hasGlyph, paperFamily as setIn } from "../src/pdf/paperFace.js";

// A font that draws these letters.
const font = (letters) => (_, ch) => letters.includes(ch);

// The fonts loaded.
const live = (...ids) => (id) => ids.includes(id);

test("a block is set in its font only when that font draws every letter of its words", () => {
  const block = { font: "g_d0_f2", face: "CMR10" };
  const has = font("abcdefghijklmnopqrstuvwxyz.,");
  const paperFamily = (b, texts, h) => setIn(b, texts, h, live("g_d0_f2"));
  assert.equal(paperFamily(block, ["the words, ", " and more."], has), "g_d0_f2");
  assert.equal(paperFamily(block, ["die Wörter"], has), null, "a letter the subset lacks: the serif, whole");
  assert.equal(paperFamily(block, ["the words", "Z"], has), null, "in any of its parts");
  assert.equal(paperFamily({ face: "CMR10" }, ["the words"], has), null, "no font of the file's");
  assert.equal(paperFamily(block, [], has), "g_d0_f2", "nothing to draw yet");
});

test("a block whose font its document took with it when it closed keeps the serif", () => {
  // The blocks outlive the pdf.js document their font ids belong to.
  const block = { font: "g_d0_f2", face: "CMR10" };
  const has = font("abcdefghijklmnopqrstuvwxyz");
  assert.equal(setIn(block, ["the words"], has, live("g_d1_f2")), null);
  assert.equal(setIn(block, [], has, live()), null, "not even with nothing to draw yet");
});

test("blanks and format characters need no glyph", () => {
  const has = (ch) => "ab".includes(ch);
  assert.equal(covers(["a b\u00a0a\tb\nab\u200b\u00ad"], has), true, "TeX's fonts have no space");
  assert.equal(covers(["a-b"], has), false);
});

test("outside a browser no font is read, and the answer is no", () => {
  assert.equal(hasGlyph("g_d0_f2", "a"), false);
  assert.equal(fontLive("g_d0_f2"), false);
});

test("what was read of a font holds only while its FontFace is loaded", () => {
  // A FontFaceSet stand-in, and a canvas on which the font, when named
  // first, gives every letter one width over any fallback.
  const faces = new Set();
  const context = {
    font: "",
    measureText() {
      const width = this.font.includes('"g_d7_f1"') ? 50 : this.font.length;
      return { width, actualBoundingBoxLeft: 0, actualBoundingBoxRight: 10 };
    },
  };
  globalThis.document = { fonts: faces, createElement: () => ({ getContext: () => context }) };
  try {
    const face = { family: '"g_d7_f1"', status: "loaded" };
    assert.equal(fontLive("g_d7_f1"), false, "not loaded yet");
    assert.equal(hasGlyph("g_d7_f1", "a"), false);
    faces.add(face);
    assert.equal(fontLive("g_d7_f1"), true);
    assert.equal(hasGlyph("g_d7_f1", "a"), true, "the no from before it loaded is not kept");
    faces.delete(face); // the viewer destroyed its document
    assert.equal(fontLive("g_d7_f1"), false);
    assert.equal(hasGlyph("g_d7_f1", "a"), false, "the yes is not kept past it");
    assert.equal(setIn({ font: "g_d7_f1" }, ["a"], hasGlyph), null, "gone: the serif, with its weight and slant");
  } finally {
    delete globalThis.document;
  }
});
