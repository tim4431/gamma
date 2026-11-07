// Notion's colours as the spans the editor writes: tests/shared/notioncolors.json
// is what backend/gamma/notion_blocks.py reads and writes
// (backend/tests/test_notion_blocks.py), held here to mdMarks.js.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { COLOR_MARK, TEXT_COLORS, colorSpan, scanMarks, tint } from "../src/editor/mdMarks.js";

const COLORS = JSON.parse(readFileSync(new URL("../../tests/shared/notioncolors.json", import.meta.url), "utf8"));

test("the palette is the editor's", () => {
  assert.deepEqual(Object.fromEntries(TEXT_COLORS.map((c) => [c.name, c.color])), COLORS.text);
  assert.equal(tint("#000000"), `#000000${COLORS.tint}`);
});

test("brown has its own value and no swatch", () => {
  assert.equal(TEXT_COLORS.some((c) => c.name === "brown" || c.color === COLORS.brown), false);
});

test("each colour reads as the span the toolbar writes", () => {
  for (const [name, color] of Object.entries({ ...COLORS.text, brown: COLORS.brown })) {
    assert.equal(COLORS.spans[name], colorSpan(color, false));
    assert.equal(COLORS.spans[`${name}_bg`], colorSpan(color, true));
  }
});

test("a run with both colours is one span the editor reads", () => {
  const spans = scanMarks(`${COLORS.pair.span}x</span>`);
  assert.equal(spans.length, 1);
  assert.equal(spans[0].marker, COLOR_MARK);
});
