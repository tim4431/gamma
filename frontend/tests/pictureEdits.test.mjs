// node --test tests/pictureEdits.test.mjs (from frontend/) — what the page
// does with a picture beyond its stored rules (picture.test.mjs): the crop
// mode's geometry, the z-order, the pictures a lasso takes and how its
// edits carry them, the Markdown line a row copies as, what a file becomes
// before it goes up and what its upload answers, and which drags a surface
// takes.
import assert from "node:assert/strict";
import { test } from "node:test";
import { transformPoint } from "../src/ink/ink.js";
import {
  MAX_Z, MIN_SIDE, captionText, carriedPicture, dragCrop, duplicatePicture, imageDelta, lassoTakes, mirrorPicture, movePicture,
  normalizePicture, onTop, orderPicture, pictureAt, pictureMarkdown, picturesBySurface, transformPicture, turnedBox,
} from "../src/markup/picture.js";
import { addPlaced } from "../src/markup/placed.js";
import { findBlock } from "../src/shared/model/blockModel.js";
import {
  MAX_PIXELS, encodePlan, imageFiles, isImageFile, onlyImages, picturesDrag, uploadPicture,
} from "../src/markup/pictureUpload.js";

const URL_ = "/api/uploads/abc.png";
const pic = (raw = {}) => normalizePicture({ url: URL_, x: 100, y: 100, w: 200, h: 100, ...raw });
const near = (a, b, what) => assert.ok(Math.abs(a - b) < 1e-9, `${what}: ${a}, not ${b}`);

test("a drag on the screen is the same drag across the image, the crop's scale applied", () => {
  const d = imageDelta(pic({ crop: { x: 0, y: 0, w: 0.5, h: 0.25 } }), 20, 10);
  near(d.x, 20 * 0.5 / 200, "across"); // 200 pt show half the image's width
  near(d.y, 10 * 0.25 / 100, "down");
});

test("on a turned or flipped picture the drag is undone through the turn and the flips", () => {
  // turned a quarter clockwise, the frame's own x runs down the screen
  const turned = imageDelta(pic({ angle: 90 }), 0, 20);
  near(turned.x, 20 / 200, "a drag down the screen is along the image's width");
  near(turned.y, 0, "and not across it");
  const left = imageDelta(pic({ angle: 90 }), 20, 0);
  near(left.y, -20 / 100, "a drag right is up the image");
  const flipped = imageDelta(pic({ flip_h: true, flip_v: true }), 20, 10);
  near(flipped.x, -20 / 200, "mirrored across");
  near(flipped.y, -10 / 100, "and down");
});

test("the crop moves inside the image, never past its edges", () => {
  const c = { x: 0.2, y: 0.2, w: 0.5, h: 0.5 };
  assert.deepEqual(dragCrop(c, "move", 0.1, -0.1), { x: 0.3, y: 0.1, w: 0.5, h: 0.5 });
  assert.deepEqual(dragCrop(c, "move", 0.9, -0.9), { x: 0.5, y: 0, w: 0.5, h: 0.5 });
});

test("a corner moves two edges, a side one; an edge stops at the image and CROP_MIN short of the other", () => {
  const c = { x: 0.2, y: 0.2, w: 0.5, h: 0.5 };
  assert.deepEqual(dragCrop(c, "se", 0.1, 0.1), { x: 0.2, y: 0.2, w: 0.6, h: 0.6 });
  assert.deepEqual(dragCrop(c, "nw", -0.5, -0.1), { x: 0, y: 0.1, w: 0.7, h: 0.6 });
  assert.deepEqual(dragCrop(c, "e", 0.1, 0.4), { x: 0.2, y: 0.2, w: 0.6, h: 0.5 }, "a side moves only its edge");
  assert.deepEqual(dragCrop(c, "n", 0.3, 0.9), { x: 0.2, y: 0.69, w: 0.5, h: 0.01 });
  assert.deepEqual(dragCrop(c, "w", 0.9, 0), { x: 0.69, y: 0.2, w: 0.01, h: 0.5 });
});

// A page with three pictures on PDF page 1, one on page 2, and a sheet with
// two of its own, one of which has a note under it.
const node = (id, properties = {}, children = []) => ({ id, content: "", properties, children });
const picOn = (id, page, children = [], raw = {}) =>
  node(id, { picture: { url: URL_, ...raw }, ...(page ? { pdf_page: page } : {}) }, children);
const tree = () => [
  picOn("a", 1),
  node("note", {}, [picOn("b", 1)]),
  picOn("c", 2),
  picOn("d", 1, [node("dNote")]),
  node("sheet", { sheet: {} }, [picOn("s1", null, [node("s1Note")]), picOn("s2", null)]),
];
const ids = (list) => list.flatMap((b) => [b.id, ...ids(b.children || [])]);
const stacked = (t, surface) => picturesBySurface(t).get(surface).map((b) => b.id);
const zOf = (t, id) => findBlock(t, id).properties.picture.z;

test("bring to front sets a picture's z just above its surface's highest, its block where it is", () => {
  const out = orderPicture(tree(), "a", "front");
  assert.deepEqual(ids(out), ids(tree()), "the notes keep their order");
  assert.deepEqual(stacked(out, 1), ["b", "d", "a"]);
  assert.equal(zOf(out, "a"), 1);
  const again = orderPicture(out, "b", "front");
  assert.equal(zOf(again, "b"), 2, "above the highest");
  assert.deepEqual(stacked(again, 1), ["d", "a", "b"]);
  assert.deepEqual(stacked(orderPicture(tree(), "s1", "front"), "sheet"), ["s2", "s1"]);
  const t = tree();
  assert.equal(orderPicture(t, "d", "front"), t, "last in document order: in front already, the same tree");
  assert.equal(orderPicture(out, "a", "front"), out, "in front by its z");
  assert.equal(orderPicture(t, "c", "back"), t, "alone on its page");
});

test("send to back sets z just below the lowest, under a sheet too; a z back at 0 is left out", () => {
  const out = orderPicture(tree(), "d", "back");
  assert.deepEqual(ids(out), ids(tree()));
  assert.equal(zOf(out, "d"), -1);
  assert.deepEqual(stacked(out, 1), ["d", "a", "b"]);
  const sheet = orderPicture(tree(), "s2", "back");
  assert.deepEqual(ids(sheet), ids(tree()), "it stays under the sheet, after s1");
  assert.deepEqual(stacked(sheet, "sheet"), ["s2", "s1"]);
  const low = [picOn("x", 1, [], { z: -1 }), picOn("y", 1, [], { z: -3 })];
  const up = orderPicture(low, "y", "front");
  assert.equal("z" in findBlock(up, "y").properties.picture, false, "just above -1 is 0, the default");
  assert.deepEqual(stacked(up, 1), ["x", "y"]);
});

test("a picture goes over its own notes' picture, and a locked one keeps its place in the stack", () => {
  const nested = [picOn("p", 1, [picOn("q", 1)])];
  assert.deepEqual(stacked(orderPicture(nested, "p", "front"), 1), ["q", "p"]);
  const locked = [picOn("l", 1, [], { locked: true, z: 3 }), picOn("m", 1)];
  assert.equal(orderPicture(locked, "l", "back"), locked, "locked: the same tree");
  assert.deepEqual(stacked(orderPicture(locked, "m", "front"), 1), ["l", "m"], "another goes over it");
});

test("at ±MAX_Z a picture that would still tie behind the end it is sent to stays as it is", () => {
  const top = [picOn("p", 1), picOn("q", 1, [], { z: MAX_Z })];
  assert.equal(orderPicture(top, "p", "front"), top, "clamped to MAX_Z, p would still be under q");
  const later = [picOn("q", 1, [], { z: MAX_Z }), picOn("p", 1, [], { z: 5 })];
  assert.deepEqual(stacked(orderPicture(later, "p", "front"), 1), ["q", "p"], "after q in the notes, the tie puts it over");
  const bottom = [picOn("p", 1, [], { z: -MAX_Z }), picOn("q", 1)];
  assert.equal(orderPicture(bottom, "q", "back"), bottom, "clamped to -MAX_Z, q would still be over p");
});

test("a new picture goes over its surface's pictures, one brought to the front too", () => {
  const add = (t, surface, id) => addPlaced(t, surface, id, { picture: onTop(t, surface, { url: URL_ }) });
  const front = orderPicture(tree(), "a", "front");
  const out = add(front, 1, "new");
  assert.deepEqual(stacked(out, 1), ["b", "d", "a", "new"]);
  assert.equal(zOf(out, "new"), 1, "the top one's z, last in document order");
  const sheet = add(orderPicture(tree(), "s1", "front"), "sheet", "new");
  assert.deepEqual(stacked(sheet, "sheet"), ["s2", "s1", "new"], "on a sheet too");
  const plain = add(tree(), 1, "new");
  assert.deepEqual(stacked(plain, 1), ["a", "b", "d", "new"]);
  assert.equal("z" in findBlock(plain, "new").properties.picture, false, "no z over pictures that carry none");
  const back = add(orderPicture([picOn("x", 2), picOn("y", 2)], "y", "back"), 2, "new");
  assert.deepEqual(stacked(back, 2), ["y", "x", "new"]);
  assert.equal("z" in findBlock(back, "new").properties.picture, false, "nor over one sent to the back");
  assert.equal("z" in findBlock(add(tree(), 9, "new"), "new").properties.picture, false, "nor on a page without pictures");
});

test("a duplicate keeps its original's z and goes in right after it, so it stacks just over it", () => {
  const page = [picOn("top", 1, [node("topNote")], { z: 2 }), picOn("mid", 1), picOn("other", 1, [], { z: 1 }), picOn("tie", 1, [], { z: 2 })];
  const out = duplicatePicture(page, "top", "copy", (p) => movePicture(p, 12, 12, { width: 612, height: 792 }), 5);
  assert.deepEqual(ids(out), ["top", "topNote", "copy", "mid", "other", "tie"], "after it in the notes, the notes under it not copied");
  assert.equal(zOf(out, "copy"), 2, "the original's z");
  assert.equal(findBlock(out, "copy").properties.picture.x, 12, "placed by `place`");
  assert.deepEqual(stacked(out, 1), ["mid", "other", "top", "copy", "tie"], "just over the original, under a later picture at its z");
});

// A lasso round the box [x0, y0, x1, y1], as a polygon.
const lasso = ([x0, y0, x1, y1]) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
// Where a picture shows the point (u, v) of its frame, from the frame's
// centre before the flips and the turn: what any drawing of it does.
const shows = (p, u, v) => {
  const a = (p.angle * Math.PI) / 180, fu = p.flip_h ? -u : u, fv = p.flip_v ? -v : v;
  return [p.x + p.w / 2 + fu * Math.cos(a) - fv * Math.sin(a), p.y + p.h / 2 + fu * Math.sin(a) + fv * Math.cos(a)];
};

test("a lasso takes a picture when more than half its turned frame lies inside, never a locked one", () => {
  const upright = pic(); // its frame is [100, 100, 300, 200]
  assert.equal(lassoTakes(upright, lasso([90, 90, 310, 210])), true, "all of it");
  assert.equal(lassoTakes(upright, lasso([90, 90, 210, 210])), true, "three fifths of it");
  assert.equal(lassoTakes(upright, lasso([90, 90, 190, 210])), false, "two fifths of it");
  assert.equal(lassoTakes(pic({ locked: true }), lasso([0, 0, 600, 600])), false, "locked, however much of it lies inside");
  assert.equal(lassoTakes(upright, [[0, 0], [600, 600]]), false, "no loop");
  // a band across the middle holds three fifths of the upright frame, and a
  // fifth of the same frame turned on its side, whose turned frame stands
  // across it: the turned frame is what is weighed
  const band = lasso([90, 120, 310, 180]);
  assert.equal(lassoTakes(upright, band), true);
  const turned = pic({ angle: 90 });
  assert.equal(lassoTakes(turned, band), false);
  assert.equal(lassoTakes(turned, lasso(turnedBox(turned).map((v, i) => v + (i < 2 ? -1 : 1)))), true, "a lasso round its turned frame");
});

test("a tap with the lasso takes the topmost picture whose turned frame holds the point, never a locked one", () => {
  // `under` from 100 to 300 across; `over`, on top, turned on its side: from 150 to 250 across, 50 to 250 down
  const shown = [{ id: "under", pic: pic() }, { id: "over", pic: pic({ angle: 90 }) }];
  assert.equal(pictureAt(shown, 200, 150), "over", "where both are, the one on top");
  assert.equal(pictureAt(shown, 120, 150), "under", "beside the turned one, inside its unturned frame: the one under it");
  assert.equal(pictureAt(shown, 200, 60), "over", "above the frame before the turn, inside the turned one");
  assert.equal(pictureAt(shown, 320, 150), null, "on neither");
  assert.equal(pictureAt([shown[0], { id: "held", pic: pic({ angle: 90, locked: true }) }], 200, 150), null,
    "a locked one on top: nothing, not the one under it");
  assert.equal(pictureAt([shown[0], { id: "held", pic: pic({ angle: 90, locked: true }) }], 120, 150), "under");
  assert.equal(pictureAt(undefined, 200, 150), null, "a surface with no pictures");
});

test("a picture carried by the lasso's handles: its centre where the strokes' points go, its frame scaled and turned with them", () => {
  const surface = { width: 1000, height: 1000 };
  const was = pic({ x: 400, y: 450, crop: { x: 0.1, y: 0, w: 0.8, h: 1 }, flip_h: true, opacity: 0.5, z: 3 });
  const value = { cx: 400, cy: 400, scale: 1.5, angle: Math.PI / 2 };
  const got = transformPicture(was, value, surface);
  const [cx, cy] = transformPoint(500, 500, value);
  near(got.x + got.w / 2, cx, "the centre across");
  near(got.y + got.h / 2, cy, "and down");
  assert.deepEqual([got.w, got.h, got.angle], [300, 150, 90], "scaled, its aspect kept, and turned a quarter clockwise");
  assert.deepEqual([got.crop, got.flip_h, got.opacity, got.z], [was.crop, true, 0.5, 3], "its crop, flip, opacity and z kept");
  const tiny = transformPicture(was, { cx: 500, cy: 500, scale: 0.01, angle: 0 }, surface);
  assert.deepEqual([tiny.w, tiny.h], [2 * MIN_SIDE, MIN_SIDE], "no side under MIN_SIDE");
  const big = transformPicture(was, { cx: 500, cy: 500, scale: 10, angle: 0 }, { width: 612, height: 792 });
  const [x0, y0, x1, y1] = turnedBox(big);
  assert.ok(x0 >= 0 && y0 >= 0 && x1 <= 612 + 1e-9 && y1 <= 792, `kept on the surface: ${[x0, y0, x1, y1]}`);
  assert.deepEqual(carriedPicture(was, value, surface), got, "the handles' preview is the edit");
  assert.deepEqual(carriedPicture(was, { dx: 12, dy: -8 }, surface), movePicture(was, 12, -8, surface), "a drag's is a move");
});

test("a picture flipped with the lasso's strokes shows as its own mirror image about the selection's centre line", () => {
  const surface = { width: 1000, height: 1000 };
  const was = pic({ x: 300, y: 300, angle: 30, flip_v: true, crop: { x: 0, y: 0.2, w: 1, h: 0.8 } });
  for (const axis of ["h", "v"]) {
    const got = mirrorPicture(was, { cx: 500, cy: 450, axis }, surface);
    const mirror = ([x, y]) => (axis === "h" ? [1000 - x, y] : [x, 900 - y]);
    for (const [u, v] of [[-100, -50], [100, -50], [100, 50], [30, 10]]) {
      const [ex, ey] = mirror(shows(was, u, v)), [gx, gy] = shows(got, u, v);
      near(gx, ex, `${axis}: (${u}, ${v}) across`);
      near(gy, ey, `${axis}: (${u}, ${v}) down`);
    }
    assert.deepEqual([got.w, got.h, got.crop], [was.w, was.h, was.crop], `${axis}: its frame and crop as they were`);
  }
});

test("a picture's Markdown is its image line, the caption's words on one line, without brackets and pipes, escaped", () => {
  assert.equal(pictureMarkdown("A [cap]|tion *x* 1. http://a.b/c_d", { url: URL_ }), `![A caption x 1. http://a.b/c_d](${URL_})`);
  assert.equal(pictureMarkdown("# head\n  two", { url: URL_ }), `![head two](${URL_})`);
  assert.equal(pictureMarkdown("a \\*star\\* kept", { url: URL_ }), `![a \\*star\\* kept](${URL_})`, "an escaped mark escaped again");
  assert.equal(pictureMarkdown("", { url: URL_ }), `![](${URL_})`);
  assert.equal(pictureMarkdown("lost", { url: "/elsewhere.png" }), "", "no picture, no line");
  assert.equal(captionText("  a\n\tb  c "), "a b c");
});

test("PNG, JPEG, GIF and WebP up to 24 MP go up as they are", () => {
  for (const type of ["image/png", "image/jpeg", "image/gif", "image/webp"]) {
    assert.deepEqual(encodePlan(type, 4000, 3000), { keep: true, width: 4000, height: 3000 }, type);
  }
  assert.equal(encodePlan("image/jpeg", 6000, 4000).keep, true, "24 MP itself");
});

test("anything else, and anything larger, is drawn smaller and sent as a JPEG, or a PNG when it may be clear", () => {
  const heic = encodePlan("image/heic", 4032, 3024);
  assert.equal(heic.keep, false);
  assert.equal(heic.type, "image/jpeg");
  assert.ok(heic.width * heic.height <= MAX_PIXELS && Math.abs(heic.width / heic.height - 4032 / 3024) < 0.01, "within the limits, the aspect kept");
  for (const type of ["image/png", "image/webp", "image/svg+xml"]) {
    const big = encodePlan(type, 8000, 6000);
    assert.equal(big.type, "image/png", type);
    assert.ok(big.width * big.height <= MAX_PIXELS && big.width < 8000, `${type}: downscaled`);
  }
  assert.equal(encodePlan("image/tiff", 100, 100).type, "image/jpeg");
});

test("only image files are a paste's or a drop's pictures", () => {
  const files = [{ type: "image/png" }, { type: "application/pdf" }, { type: "" }, { type: "image/heic" }];
  assert.deepEqual(imageFiles({ files }), [files[0], files[3]]);
  assert.equal(isImageFile(null), false);
  assert.deepEqual(imageFiles(null), []);
});

test("a drag of image files and nothing else over a PDF page or a sheet is the surface's", () => {
  const drag = (...types) => ({ items: types.map((type) => ({ kind: type.includes("/") ? "file" : "string", type })) });
  assert.equal(onlyImages(drag("image/png", "image/heic")), true);
  assert.equal(onlyImages(drag("image/png", "application/pdf")), false, "a file of another kind keeps it the app's");
  assert.equal(onlyImages(drag("image/png", "text")), false, "so does a string");
  assert.equal(onlyImages(drag()), false);
  assert.equal(onlyImages(null), false);
  const over = (cls) => ({ closest: (sel) => (sel.split(",").some((s) => s.trim() === cls) ? {} : null) });
  for (const cls of [".pdfPageWrap", ".nbSheet"]) {
    assert.equal(picturesDrag({ dataTransfer: drag("image/png"), target: over(cls) }), true, cls);
  }
  assert.equal(picturesDrag({ dataTransfer: drag("image/png"), target: over(".blockRowWrap") }), false, "a row's text: the row's");
  assert.equal(picturesDrag({ dataTransfer: drag("application/pdf"), target: over(".nbSheet") }), false);
});

// uploadPicture in a browser of stubs: createImageBitmap decodes any file
// at 4032 × 3024, a canvas behaves as `canvas` says, `post` answers as
// given. Each call returns what it answered and the canvases it drew on.
async function upload(file, { canvas = {}, post = async () => ({ url: "/api/uploads/up.jpg" }) } = {}) {
  const saved = { createImageBitmap: globalThis.createImageBitmap, document: globalThis.document };
  const canvases = [];
  globalThis.createImageBitmap = async () => ({ width: 4032, height: 3024, close() {} });
  globalThis.document = {
    createElement: () => {
      const c = { width: 0, height: 0, getContext: () => ({ fillRect() {}, drawImage() {} }),
        toBlob: (done, type) => done(new Blob(["x"], { type })), ...canvas };
      canvases.push(c);
      return c;
    },
  };
  const posted = [];
  try {
    const out = await uploadPicture(file, async (path, f) => { posted.push(f); return post(path, f); });
    return { out, canvases, posted };
  } finally {
    Object.assign(globalThis, saved);
  }
}
const heic = () => new File(["heic"], "photo.heic", { type: "image/heic" });

test("a picture redrawn before it goes up is sent as its re-encoding, and its canvas let go", async () => {
  const { out, canvases, posted } = await upload(heic());
  assert.deepEqual(out.url, "/api/uploads/up.jpg");
  assert.equal(posted[0].name, "photo.jpg");
  assert.equal(posted[0].type, "image/jpeg");
  assert.deepEqual([canvases[0].width, canvases[0].height], [0, 0], "the canvas emptied");
});

test("an SVG goes up as it is, at the size its root element gives; one the server cannot size goes as a PNG", async () => {
  const svg = (text) => new File([text], "plot.svg", { type: "image/svg+xml" });
  const sized = svg('<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20"><rect/></svg>');
  let { out, canvases, posted } = await upload(sized, { post: async () => ({ url: "/api/uploads/up.svg" }) });
  assert.deepEqual(out, { url: "/api/uploads/up.svg", natural: { w: 300, h: 150 } }, "svgSize, not the decoded size");
  assert.equal(posted[0], sized, "the file itself");
  assert.equal(canvases.length, 0, "never drawn");
  ({ out, posted } = await upload(svg('<svg viewBox="0 0 40 20"/>'), { post: async () => ({ url: "/api/uploads/up.png" }) }));
  assert.equal(posted[0].type, "image/png", "no SVG namespace: the server would draw none, so it is redrawn");
  assert.equal(out.natural.w / out.natural.h, 4032 / 3024, "at the size the browser decoded");
});

test("an upload never rejects: a canvas refused or tainted, or a failed post, says why instead", async () => {
  const cases = [
    ["no context (WebKit's canvas memory used up)", { canvas: { getContext: () => null } }, "decode"],
    ["a draw that throws", { canvas: { getContext: () => ({ fillRect() {}, drawImage() { throw new TypeError("bad image"); } }) } }, "decode"],
    ["a tainted canvas's toBlob throws", { canvas: { toBlob() { throw new Error("SecurityError"); } } }, "decode"],
    ["no blob", { canvas: { toBlob: (done) => done(null) } }, "decode"],
    ["the post throws", { post: async () => { throw new Error("offline"); } }, "upload"],
    ["the server answers no picture", { post: async () => ({ url: "/api/uploads/x.bmp" }) }, "upload"],
  ];
  for (const [what, stubs, error] of cases) {
    const { out, canvases, posted } = await upload(heic(), stubs);
    assert.deepEqual(out, { error }, what);
    assert.ok(canvases.every((c) => c.width === 0 && c.height === 0), `${what}: the canvas emptied`);
    assert.equal(posted.length, error === "decode" ? 0 : 1, `${what}: posted only when ready`);
  }
});
