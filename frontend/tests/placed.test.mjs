// node --test tests/placed.test.mjs (from frontend/) — the tree edits a text
// box and a picture share as objects placed on a page surface
// (markup/placed.js): one value replaced, and where a new one goes.
import assert from "node:assert/strict";
import { test } from "node:test";
import { addPlaced, withPlaced } from "../src/markup/placed.js";
import { normalizePicture } from "../src/markup/picture.js";
import { normalizeTextBox } from "../src/markup/textBox.js";

const pic = normalizePicture({ url: "/api/uploads/abc.png", x: 10, y: 20, w: 100, h: 50 });
const box = normalizeTextBox({ x: 5, y: 6 });
const node = (id, properties = {}, children = []) => ({ id, parentId: null, content: "", properties, children });
const tree = () => [
  node("p", { picture: pic, pdf_page: 1 }),
  node("note", {}, [node("b", { text_box: box, pdf_page: 1 })]),
  node("sheet", { sheet: {} }, [node("s1", { picture: { url: "nowhere" } })]),
];

test("one placed value is replaced by what fn makes of it, normalized; the rest of the tree keeps its identity", () => {
  const t = tree();
  const moved = withPlaced(t, "p", "picture", (p) => ({ ...p, x: 30 }));
  assert.equal(moved[0].properties.picture.x, 30);
  assert.equal(moved[0].properties.pdf_page, 1, "the other properties stay");
  assert.equal(moved[1], t[1], "an untouched subtree is the same object");
  const nested = withPlaced(t, "b", "text_box", (b) => ({ ...b, y: 60 }));
  assert.equal(nested[1].children[0].properties.text_box.y, 60, "a box under a note");
  assert.equal(nested[0], t[0]);
  const got = [];
  withPlaced(t, "b", "text_box", (b) => { got.push(b); return b; });
  assert.deepEqual(got, [box], "fn gets the normalized value");
});

test("the same tree when nothing changes, or the block holds no such value", () => {
  const t = tree();
  assert.equal(withPlaced(t, "p", "picture", (p) => ({ ...p })), t, "the same value");
  assert.equal(withPlaced(t, "p", "picture", () => null), t, "fn makes nothing");
  assert.equal(withPlaced(t, "p", "text_box", (b) => ({ ...b, x: 1 })), t, "no box on a picture");
  assert.equal(withPlaced(t, "s1", "picture", (p) => ({ ...p, x: 1 })), t, "a picture that does not normalize");
  assert.equal(withPlaced(t, "gone", "picture", (p) => ({ ...p, x: 1 })), t, "no such block");
});

test("a new one on a PDF page goes at the top level with its pdf_page; on a sheet, its last child", () => {
  const t = tree();
  const props = { t0: 1, picture: pic };
  const onPage = addPlaced(t, 3, "n", props);
  assert.deepEqual(onPage.at(-1), { id: "n", parentId: null, children: [], content: "", properties: { t0: 1, picture: pic, pdf_page: 3 } });
  assert.equal(onPage[0], t[0], "the rest kept");
  const onSheet = addPlaced(t, "sheet", "n", { t0: 1, text_box: box });
  assert.deepEqual(onSheet[2].children.map((c) => c.id), ["s1", "n"]);
  assert.deepEqual(onSheet[2].children[1], { id: "n", parentId: "sheet", children: [], content: "", properties: { t0: 1, text_box: box } },
    "under the sheet, no pdf_page");
  assert.equal(onSheet[0], t[0]);
  assert.equal(addPlaced(t, "gone", "n", props), t, "a sheet gone meanwhile: the same tree");
});
