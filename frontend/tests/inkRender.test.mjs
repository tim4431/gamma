// node --test tests/inkRender.test.mjs (from frontend/) — what the server's
// exporters draw as the screen does, pinned by the shared fixtures
// gamma/ink.py reads too (backend/tests/test_shared_fixtures.py): the
// pencil's grain tile (tests/shared/inkgrain.json), the calligraphy nib
// (tests/shared/inknib.json) and a stroke outlined whole
// (tests/shared/inkoutline.json).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { GRAIN_PX, GRAIN_UNITS, grainHex, grainUri } from "../src/ink/grain.js";
import { nibOutline, strokePath } from "../src/ink/ink.js";

const load = (name) => JSON.parse(readFileSync(new URL(`../../tests/shared/${name}`, import.meta.url), "utf8"));
const GRAIN = load("inkgrain.json");
const NIB = load("inknib.json");
const OUTLINE = load("inkoutline.json");

// A PNG's chunks by type.
function chunks(png) {
  const out = {};
  for (let i = 8; i < png.length;) {
    const n = png.readUInt32BE(i);
    out[png.toString("latin1", i + 4, i + 8)] = png.subarray(i + 8, i + 8 + n);
    i += 12 + n;
  }
  return out;
}

test("inkgrain.json: the tile is GRAIN_PX pixels over GRAIN_UNITS page units", () => {
  assert.deepEqual([GRAIN.GRAIN_PX, GRAIN.GRAIN_UNITS], [GRAIN_PX, GRAIN_UNITS]);
});

for (const tile of GRAIN.tiles) {
  test(`grain tile: ${tile.note}`, () => {
    assert.equal(grainUri(tile.color), `data:image/png;base64,${tile.png}`);
    // only the palette differs from one colour to another
    const [png, first] = [tile.png, GRAIN.tiles[0].png].map((b) => Buffer.from(b, "base64"));
    assert.deepEqual(chunks(png).IDAT, chunks(first).IDAT);
    assert.deepEqual(chunks(png).tRNS, Buffer.from([0, 128, 204, 255]));
  });
}

test("grainHex names the tile a colour is painted with, as the server's inkGrain-<rrggbb>", () => {
  for (const tile of GRAIN.tiles) {
    assert.equal(grainHex(tile.color), chunks(Buffer.from(tile.png, "base64")).PLTE.subarray(0, 3).toString("hex"), tile.note);
  }
  assert.equal(grainHex("rgb(255, 0, 0)"), "ff0000", "a colour written with spaces keeps its tile");
  assert.equal(grainHex("#FF0000"), grainHex("rgba(255,0,0,0.5)"), "one tile whatever the case or the alpha");
  assert.notEqual(grainHex("rgb(255,0,0)"), grainHex("rgb(25,50,0)"), "two tiles never share a name");
});

for (const c of NIB.cases) {
  test(`nib: ${c.note}`, () => {
    assert.equal(nibOutline(c.samples.map(([x, y]) => ({ x, y })), c.size, c.nib), c.d);
  });
}

for (const c of NIB.strokes) {
  test(`nib stroke: ${c.note}`, () => {
    const path = strokePath(c.stroke);
    assert.equal(path.d, c.d);
    assert.equal(path.stroke, false, "a filled path");
  });
}

for (const c of OUTLINE.strokes) {
  test(`whole outline: ${c.note}`, () => {
    const path = strokePath(c.stroke);
    assert.equal(path.d, c.d);
    assert.equal(path.stroke, false, "a filled path");
  });
}
