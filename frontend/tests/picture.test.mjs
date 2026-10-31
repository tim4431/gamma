// node --test tests/picture.test.mjs (from frontend/) — the picture rules.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { round2 } from "../src/notebook/notebook.js";
import {
  CROP_MIN, DEFAULT_SIDE, MAX_COORD, MIN_SIDE, PICTURE_URL, cropTo, fitPicture, flipPicture, isPicture, keepOnSurface,
  mergePicture, movePicture, normalizePicture, pictureLayout, pictureMarkdown, picturesBySurface, placePicture, resetPicture,
  scalePicture, stretchPicture, turnPicture, turnedBox, turnedCorners,
} from "../src/markup/picture.js";

// tests/shared/picture.json and picturemerge.json: the same cases
// gamma/picture.py passes (backend/tests/test_shared_fixtures.py).
const PICTURE = JSON.parse(readFileSync(new URL("../../tests/shared/picture.json", import.meta.url), "utf8"));
const MERGE = JSON.parse(readFileSync(new URL("../../tests/shared/picturemerge.json", import.meta.url), "utf8"));
const r = (v) => round2(v) + 0; // hundredths, -0 as 0
for (const c of PICTURE.normalize) {
  test(`picture: ${c.note}`, () => assert.deepEqual(normalizePicture(c.input), c.output));
}
for (const c of PICTURE.keep) {
  test(`picture kept on its surface: ${c.note}`, () => assert.deepEqual(keepOnSurface(c.picture, c.surface), c.output));
}
for (const c of PICTURE.turned) {
  test(`picture turned: ${c.note}`, () => {
    assert.deepEqual(turnedCorners(c.picture).map(([x, y]) => [r(x), r(y)]), c.corners);
    assert.deepEqual(turnedBox(c.picture).map(r), c.box);
  });
}
for (const c of PICTURE.markdown) {
  test(`picture as Markdown: ${c.note}`, () => assert.equal(pictureMarkdown(c.content, c.picture), c.output));
}
for (const c of MERGE.merge) {
  test(`picture merge: ${c.note}`, () => assert.deepEqual(mergePicture(c.stored, c.mine, c.base), c.output));
}

test("the constants agree with the server's", () => {
  assert.deepEqual([MIN_SIDE, MAX_COORD, CROP_MIN, DEFAULT_SIDE], [8, 14400, 0.01, 144]);
  assert.equal(PICTURE_URL.test("/api/uploads/a.PnG"), true);
  assert.equal(PICTURE_URL.test("/api/uploads/a.png/"), false);
});

const URL_ = "/api/uploads/abc.png";
const pic = (raw = {}) => normalizePicture({ url: URL_, ...raw });
const page = { width: 612, height: 792 };

test("a block is a picture when its picture is an object and it is neither ink nor a text box", () => {
  assert.equal(isPicture({ id: "a", properties: { picture: { url: URL_ } } }), true);
  assert.equal(isPicture({ id: "a", properties: { picture: {} } }), true, "a picture whose file is missing is still one");
  for (const picture of [null, undefined, "pic", 3, true, []]) {
    assert.equal(isPicture({ id: "a", properties: { picture } }), false, String(picture));
  }
  assert.equal(isPicture({ id: "a", properties: { picture: {}, ink_url: "/api/uploads/a.ink" } }), false, "ink first");
  assert.equal(isPicture({ id: "a", properties: { picture: {}, ink_url: null } }), false, "an ink_url key, whatever its value");
  assert.equal(isPicture({ id: "a", properties: { picture: {}, text_box: {} } }), false, "a text box next");
  assert.equal(isPicture({ id: "a", properties: { picture: {}, text_box: "no" } }), true, "a text_box that is no object");
  assert.equal(isPicture({ id: "a" }), false);
  assert.equal(isPicture(null), false);
});

const picture = (id, props = {}, children = []) => ({
  id, content: id, properties: { picture: { url: URL_, x: 10, y: 20 }, ...props }, children,
});
const note = (id, props = {}, children = []) => ({ id, content: id, properties: props, children });
const sheet = (id, children = []) => ({ id, content: "", properties: { sheet: {} }, children });

test("a PDF page's pictures key by pdf_page, in document order, wherever they sit in the notes", () => {
  const p1 = picture("p1", { pdf_page: 2 }), p2 = picture("p2", { pdf_page: 1 });
  const moved = picture("moved", { pdf_page: 2 });
  const tree = [
    p1,
    note("hl", { pdf_position: { pageNumber: 2 } }, [moved]), // under a highlight: still on its page
    p2,
    note("tb", { pdf_page: 1, text_box: { x: 1 }, picture: { url: URL_ } }), // a text box that carries a picture
    note("ink", { pdf_page: 1, ink_url: "/api/uploads/a.ink", picture: { url: URL_ } }),
    picture("missing", { pdf_page: 1, picture: { url: "/api/uploads/a.svg" } }), // draws nothing
    picture("loose"), // no page, no sheet: on no surface
    picture("bad", { pdf_page: "3" }),
    picture("zero", { pdf_page: 0 }),
  ];
  const map = picturesBySurface(tree);
  assert.deepEqual([...map.keys()].sort(), [1, 2]);
  assert.deepEqual(map.get(2).map((b) => b.id), ["p1", "moved"]);
  assert.deepEqual(map.get(1).map((b) => b.id), ["p2"]);
  // the tree's own objects, so an unchanged picture keeps its identity
  assert.equal(map.get(2)[0], p1);
  assert.equal(map.get(2)[1], moved);
  assert.equal(map.get(1)[0], p2);
});

test("a sheet's pictures are those under it that no nearer sheet holds", () => {
  const tree = [
    note("intro"),
    sheet("s1", [
      picture("t1"),
      note("why", {}, [picture("t2")]),
      sheet("s2", [picture("t3"), picture("stale", { pdf_page: 4 })]), // the nearest sheet decides, not a leftover pdf_page
      picture("t4"),
    ]),
    note("bullet", {}, [sheet("s3"), picture("after")]), // after s3 in document order, but not under it
  ];
  const map = picturesBySurface(tree);
  assert.deepEqual([...map.keys()], ["s1", "s2"]);
  assert.deepEqual(map.get("s1").map((b) => b.id), ["t1", "t2", "t4"]);
  assert.deepEqual(map.get("s2").map((b) => b.id), ["t3", "stale"]);
  assert.equal(map.has("s3"), false); // a surface without pictures has no entry
  assert.equal(map.has(4), false);
  assert.equal(picturesBySurface([]).size, 0);
});

test("a new picture keeps its natural aspect, fitted inside half the surface, centred", () => {
  const frame = placePicture(page, { w: 3000, h: 2000 }, null);
  assert.equal(frame.url, undefined, "the caller adds the url");
  assert.deepEqual(normalizePicture({ url: URL_, ...frame }), { url: URL_, ...frame });
  // half the width (306) is tighter than half the height (396) for 3:2
  assert.deepEqual([frame.w, frame.h, frame.x, frame.y], [306, 204, 153, 294]);
  assert.deepEqual([frame.angle, frame.crop, frame.flip_h, frame.flip_v], [0, { x: 0, y: 0, w: 1, h: 1 }, false, false]);
  const tall = placePicture(page, { w: 1000, h: 4000 }, null);
  assert.deepEqual([tall.w, tall.h], [99, 396], "a tall one by half the height");
});

test("a new picture is never larger than its pixels as points", () => {
  const small = placePicture(page, { w: 120, h: 80 }, null);
  assert.deepEqual([small.w, small.h, small.x, small.y], [120, 80, 246, 356]);
});

test("a new picture is centred on a point, and kept on the surface", () => {
  const at = placePicture(page, { w: 100, h: 50 }, { x: 200, y: 300 });
  assert.deepEqual([at.x, at.y, at.w, at.h], [150, 275, 100, 50]);
  const corner = placePicture(page, { w: 100, h: 50 }, { x: 5, y: 790 });
  assert.deepEqual([corner.x, corner.y], [0, 742]);
});

const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg}: ${a} vs ${b}`);
// The turned frame of `p` within `surface` to `tol` points (the rounding of x,
// y, w and h to hundredths), touching the edges `touch` names ("l", "t", "r", "b").
function onSurface(p, surface, touch = "", tol = 0.011) {
  const [x0, y0, x1, y1] = turnedBox(p);
  assert.ok(x0 >= -tol && y0 >= -tol && x1 <= surface.width + tol && y1 <= surface.height + tol,
    `the turned frame ${[x0, y0, x1, y1].map(r)} on ${surface.width} × ${surface.height}`);
  if (touch.includes("l")) near(x0, 0, tol, "at the left edge");
  if (touch.includes("t")) near(y0, 0, tol, "at the top edge");
  if (touch.includes("r")) near(x1, surface.width, tol, "at the right edge");
  if (touch.includes("b")) near(y1, surface.height, tol, "at the bottom edge");
}
const A4 = { width: 595.28, height: 841.89 };

test("a move keeps the turned frame on the surface", () => {
  const p = pic({ x: 100, y: 100, w: 80, h: 60, angle: 30, crop: { x: 0.1, y: 0, w: 0.5, h: 1 } });
  const moved = movePicture(p, 12.345, -40, page);
  assert.deepEqual([moved.x, moved.y], [112.35, 60]);
  assert.deepEqual({ ...moved, x: p.x, y: p.y }, p, "a move keeps the rest");
  onSurface(movePicture(p, 1000, 1000, page), page, "rb");
  onSurface(movePicture(p, -500, -500, page), page, "lt");
  const upright = pic({ x: 100, y: 100, w: 80, h: 60 });
  const far = movePicture(upright, 1000, 1000, page);
  assert.deepEqual([far.x, far.y], [612 - 80, 792 - 60], "upright, the frame itself");
});

test("a wide picture turned a quarter goes into the corner by its turned frame, its own corner past the edge", () => {
  const p = turnPicture(pic({ x: 100, y: 300, w: 400, h: 100 }), 90);
  const corner = movePicture(p, -1000, -1000, A4);
  assert.deepEqual([corner.x, corner.y], [-150, 150], "the frame before the turn starts 150 pt left of the page");
  assert.deepEqual(turnedBox(corner).map(r), [0, 0, 100, 400], "what shows is in the corner, none of it off the page");
  onSurface(movePicture(p, 1000, 1000, A4), A4, "rb");
});

test("a scale goes about the centre and keeps the aspect, a stretch too", () => {
  const p = pic({ x: 100, y: 100, w: 100, h: 50 });
  const big = scalePicture(p, 1.5, page);
  assert.deepEqual([big.x, big.y, big.w, big.h], [75, 87.5, 150, 75]);
  const stretched = pic({ x: 100, y: 100, w: 120, h: 40 });
  const half = scalePicture(stretched, 0.5, page);
  assert.deepEqual([half.w, half.h], [60, 20], "a stretched picture stays stretched");
});

test("a turned picture's scale stops where its turned frame meets the surface", () => {
  const p = pic({ x: 200, y: 300, w: 100, h: 50, angle: 90 });
  const huge = scalePicture(p, 100, page);
  assert.deepEqual([huge.w, huge.h], [792, 396], "its width, standing upright, as tall as the page");
  onSurface(huge, page, "tb");
  const tilted = scalePicture(pic({ x: 200, y: 300, w: 100, h: 50, angle: 30 }), 100, page);
  onSurface(tilted, page, "lr");
});

test("a scale stops at MIN_SIDE and at the surface", () => {
  const p = pic({ x: 100, y: 100, w: 100, h: 50 });
  const tiny = scalePicture(p, 0.01, page);
  assert.deepEqual([tiny.w, tiny.h], [16, MIN_SIDE], "the shorter side at MIN_SIDE, the aspect kept");
  assert.deepEqual([tiny.x + tiny.w / 2, tiny.y + tiny.h / 2], [150, 125], "about the centre");
  const huge = scalePicture(p, 100, page);
  assert.deepEqual([huge.w, huge.h], [612, 306], "the surface's width at most");
  assert.deepEqual([huge.x, huge.y], [0, 0], "moved onto the surface");
  const tall = scalePicture(pic({ x: 0, y: 0, w: 50, h: 100 }), 100, { width: 612, height: 400 });
  assert.deepEqual([tall.w, tall.h], [200, 400], "or its height");
});

test("a stretch sets both sides about the centre, within MIN_SIDE and the surface", () => {
  const p = pic({ x: 100, y: 100, w: 100, h: 50, angle: 45 });
  const s = stretchPicture(p, 200, 30, page);
  assert.deepEqual([s.x, s.y, s.w, s.h, s.angle], [50, 110, 200, 30, 45]);
  const tiny = stretchPicture(p, 1, 2, page);
  assert.deepEqual([tiny.w, tiny.h], [MIN_SIDE, MIN_SIDE]);
  const upright = pic({ x: 100, y: 100, w: 100, h: 50 });
  const wide = stretchPicture(upright, 5000, 50, page);
  assert.deepEqual([wide.x, wide.w, wide.h], [0, 612, 50], "upright, each side stops at its own edge");
  const quarter = stretchPicture({ ...upright, angle: 90 }, 5000, 50, page);
  assert.deepEqual([quarter.w, quarter.h], [792, 50], "turned a quarter, its width stands along the page's height");
  onSurface(quarter, page, "tb");
  const turned = stretchPicture(p, 5000, 50, page);
  onSurface(turned, page, "lr");
  near(turned.w / turned.h, (612 - 8 * Math.SQRT1_2) / Math.SQRT1_2 / 50, 0.01, "scaled down whole once both sides are too much");
});

test("a turn with the surface keeps the turned frame on it", () => {
  const edge = pic({ x: 400, y: 0, w: 200, h: 40 });
  const turned = turnPicture(edge, 90, { surface: page });
  assert.equal(turned.angle, 90);
  assert.deepEqual([turned.w, turned.h], [200, 40], "moved, not scaled, while there is room");
  onSurface(turned, page, "t");
  assert.deepEqual(turnPicture(edge, 90).y, 0, "without the surface, only turned");
  const fitted = pic({ x: 6, y: 196, w: 600, h: 400 });
  const tilted = turnPicture(fitted, 45, { surface: page });
  onSurface(tilted, page, "lr");
  near(tilted.w / tilted.h, 1.5, 0.001, "scaled down about its centre, its aspect kept");
  near(tilted.x + tilted.w / 2, 306, 0.01, "about its centre");
});

test("a turn snaps to 15° with Shift, else to a quarter turn within 3°", () => {
  const p = pic({ x: 10, y: 10 });
  assert.equal(turnPicture(p, 37).angle, 37);
  assert.equal(turnPicture(p, 37, { shift: true }).angle, 30);
  assert.equal(turnPicture(p, 38, { shift: true }).angle, 45);
  assert.equal(turnPicture(p, -7, { shift: true }).angle, 0);
  assert.equal(turnPicture(p, 92.5).angle, 90, "the magnet");
  assert.equal(turnPicture(p, 87).angle, 90, "3° off snaps");
  assert.equal(turnPicture(p, 86).angle, 86, "4° off does not");
  assert.equal(turnPicture(p, 94).angle, 94);
  assert.equal(turnPicture(p, 2).angle, 0);
  assert.equal(turnPicture(p, -2.5).angle, 0);
  assert.equal(turnPicture(p, 178).angle, -180, "a half turn, normalized");
  assert.equal(turnPicture(p, 268.5).angle, -90);
  assert.equal(turnPicture(p, 400.123).angle, 40.12, "normalized and rounded");
  assert.deepEqual({ ...turnPicture(p, 30), angle: 0 }, p, "a turn keeps the rest");
});

test("a flip toggles one axis", () => {
  const p = pic({ flip_v: true });
  assert.deepEqual([flipPicture(p, "h").flip_h, flipPicture(p, "h").flip_v], [true, true]);
  assert.deepEqual([flipPicture(p, "v").flip_h, flipPicture(p, "v").flip_v], [false, false]);
  assert.equal(flipPicture(flipPicture(p, "h"), "h").flip_h, false);
});

// Where a point of the image (fractions of the whole image as shown) lands
// on the page: the crop, then the flips about the frame's centre, then the
// turn about it, as the screen draws it.
function pagePoint(p, u, v) {
  let dx = ((u - p.crop.x) / p.crop.w - 0.5) * p.w;
  let dy = ((v - p.crop.y) / p.crop.h - 0.5) * p.h;
  if (p.flip_h) dx = -dx;
  if (p.flip_v) dy = -dy;
  const a = (p.angle * Math.PI) / 180;
  return [p.x + p.w / 2 + dx * Math.cos(a) - dy * Math.sin(a), p.y + p.h / 2 + dx * Math.sin(a) + dy * Math.cos(a)];
}

test("a crop keeps what it keeps where it was on the page, at the same scale", () => {
  const before = pic({ x: 200, y: 250, w: 240, h: 120, angle: 30, flip_h: true, crop: { x: 0.1, y: 0.2, w: 0.6, h: 0.5 } });
  const crops = [
    { x: 0.3, y: 0.3, w: 0.3, h: 0.3 },
    { x: 0.15, y: 0.25, w: 0.5, h: 0.4 },
    { x: 0, y: 0, w: 1, h: 1 }, // uncropped again: the parts cropped away come back around it
    { x: 0.4, y: 0.6, w: 0.2, h: 0.1 },
  ];
  for (const crop of crops) {
    const after = cropTo(before, crop);
    assert.deepEqual(after.crop, crop);
    assert.deepEqual([after.angle, after.flip_h, after.flip_v, after.url], [30, true, false, URL_]);
    near(after.w / after.crop.w, before.w / before.crop.w, 0.05, "the image's width in points");
    near(after.h / after.crop.h, before.h / before.crop.h, 0.05, "its height");
    // points of the image both crops keep
    const u0 = Math.max(crop.x, before.crop.x), u1 = Math.min(crop.x + crop.w, before.crop.x + before.crop.w);
    const v0 = Math.max(crop.y, before.crop.y), v1 = Math.min(crop.y + crop.h, before.crop.y + before.crop.h);
    for (const [s, t] of [[0, 0], [1, 0], [0.5, 0.5], [0.25, 1], [1, 1]]) {
      const u = u0 + s * (u1 - u0), v = v0 + t * (v1 - v0);
      const [x0, y0] = pagePoint(before, u, v), [x1, y1] = pagePoint(after, u, v);
      near(x1, x0, 0.02, `x of (${u}, ${v}) through ${JSON.stringify(crop)}`);
      near(y1, y0, 0.02, `y of (${u}, ${v}) through ${JSON.stringify(crop)}`);
    }
  }
});

test("a crop of a flipped-both-ways quarter turn moves the frame the way the picture shows it", () => {
  const before = pic({ x: 300, y: 300, w: 100, h: 100, angle: 90, flip_h: true, flip_v: true });
  const after = cropTo(before, { x: 0.5, y: 0, w: 0.5, h: 0.5 }); // the image's top-right quarter
  assert.deepEqual([after.w, after.h], [50, 50]);
  // flipped both ways, the top-right quarter shows bottom-left; a quarter turn clockwise takes that to top-left
  assert.deepEqual([after.x, after.y], [300, 300]);
  const [x0, y0] = pagePoint(before, 0.75, 0.25), [x1, y1] = pagePoint(after, 0.75, 0.25);
  near(x1, x0, 1e-9, "x");
  near(y1, y0, 1e-9, "y");
});

test("a bad crop is the whole image", () => {
  const before = pic({ x: 100, y: 100, w: 50, h: 50, crop: { x: 0.5, y: 0.5, w: 0.5, h: 0.5 } });
  const after = cropTo(before, "all");
  assert.deepEqual([after.crop, after.x, after.y, after.w, after.h], [{ x: 0, y: 0, w: 1, h: 1 }, 50, 50, 100, 100]);
});

test("fit to page: as wide as the surface less 24 pt a side, shrunk to fit its height, centred", () => {
  const wide = fitPicture(pic({ x: 5, y: 5, w: 100, h: 50, flip_h: true }), page);
  assert.deepEqual([wide.x, wide.y, wide.w, wide.h], [24, 255, 564, 282]);
  assert.equal(wide.flip_h, true, "the rest kept");
  const tall = fitPicture(pic({ w: 50, h: 100 }), page);
  assert.deepEqual([tall.x, tall.y, tall.w, tall.h], [120, 24, 372, 744]);
});

test("fit to page fits the turned frame, centred", () => {
  const margins = (p, s) => {
    const [x0, y0, x1, y1] = turnedBox(p);
    near(x0 + x1, s.width, 0.02, "centred across");
    near(y0 + y1, s.height, 0.02, "centred down");
    assert.ok(x0 >= 24 - 0.011 && y0 >= 24 - 0.011, `within the margins: ${[x0, y0].map(r)}`);
    return [x1 - x0, y1 - y0];
  };
  const tilted = fitPicture(pic({ x: 5, y: 5, w: 100, h: 50, angle: 15, flip_h: true }), page);
  assert.deepEqual([tilted.angle, tilted.flip_h], [15, true], "the rest kept");
  near(margins(tilted, page)[0], 564, 0.02, "as wide as the page less the margins");
  near(tilted.w / tilted.h, 2, 0.001, "its aspect kept");
  // a portrait picture turned a quarter on A4: wide as it shows
  const portrait = fitPicture(pic({ w: 100, h: 200, angle: 90 }), A4);
  near(margins(portrait, A4)[0], 595.28 - 48, 0.02, "as wide as the page less the margins");
  // a phone photo turned on its side: as tall as the page less the margins, its own corner off the page
  const sideways = fitPicture(pic({ w: 400, h: 100, angle: 90 }), A4);
  near(margins(sideways, A4)[1], 841.89 - 48, 0.02, "as tall as the page less the margins");
  assert.ok(sideways.x < 0, `the frame before the turn starts left of the page (${sideways.x})`);
});

test("reset: the whole image, unflipped, upright, its natural aspect, as wide as it was about its centre", () => {
  const p = pic({ x: 100, y: 100, w: 200, h: 50, angle: 40, flip_h: true, flip_v: true, crop: { x: 0.2, y: 0.2, w: 0.5, h: 0.5 } });
  const reset = resetPicture(p, { w: 400, h: 300 });
  assert.deepEqual(reset, { url: URL_, x: 100, y: 50, w: 200, h: 150, angle: 0, crop: { x: 0, y: 0, w: 1, h: 1 },
    flip_h: false, flip_v: false });
  assert.equal(resetPicture(p, { w: 0, h: 0 }).h, 50, "no natural size: the aspect it has");
});

test("reset and crop with the surface keep the result on it", () => {
  // a wide picture turned on its side against the left edge: upright again, it is moved in
  const side = movePicture(turnPicture(pic({ x: 100, y: 300, w: 400, h: 100 }), 90), -1000, 0, page);
  assert.equal(side.x, -150);
  assert.equal(resetPicture(side, { w: 400, h: 100 }).x, -150, "without the surface, about the same centre");
  const upright = resetPicture(side, { w: 400, h: 100 }, page);
  assert.deepEqual([upright.x, upright.y, upright.w, upright.h, upright.angle], [0, 300, 400, 100, 0]);
  // a crop taken back at the right edge: the whole image grows past it, so it is moved in
  const cropped = movePicture(pic({ w: 100, h: 100, crop: { x: 0, y: 0, w: 0.5, h: 1 } }), 1000, 0, page);
  assert.equal(cropped.x, 512);
  assert.equal(cropTo(cropped, { x: 0, y: 0, w: 1, h: 1 }).x, 512, "without the surface, the part kept where it was");
  const whole = cropTo(cropped, { x: 0, y: 0, w: 1, h: 1 }, page);
  assert.deepEqual([whole.x, whole.w, whole.crop.w], [412, 200, 1]);
});

test("the layout draws the crop to fill the frame and the flips on the inner box", () => {
  assert.deepEqual(pictureLayout(pic()), {
    flip: "scale(1, 1)", img: { width: "100%", height: "100%", left: "0%", top: "0%" },
  });
  assert.deepEqual(pictureLayout(pic({ flip_h: true, crop: { x: 0.25, y: 0.1, w: 0.5, h: 0.8 } })), {
    flip: "scale(-1, 1)", img: { width: "200%", height: "125%", left: "-50%", top: "-12.5%" },
  });
  assert.equal(pictureLayout(pic({ flip_v: true })).flip, "scale(1, -1)");
  assert.equal(pictureLayout(pic({ crop: { x: 0, y: 0, w: 0.3, h: 1 } })).img.width, "333.3333%");
});

// The merge of two edits made at once from the same picture lands as the
// two made one after the other: each edit of the menu and the handles
// against a move made meanwhile, either way round, to the hundredths each
// rounds to.
test("an edit and a move made at once both land, whichever reaches the server first", () => {
  const base = pic({ x: 150, y: 200, w: 240, h: 120, angle: 30, flip_h: true, crop: { x: 0.1, y: 0.2, w: 0.6, h: 0.5 } });
  const move = (p) => movePicture(p, 60, -45, page);
  const edits = {
    scale: (p) => scalePicture(p, 1.1, page),
    shrink: (p) => scalePicture(p, 1 / 1.1, page),
    stretch: (p) => stretchPicture(p, p.w + 30, p.h - 20, page),
    turn: (p) => turnPicture(p, 75),
    flip: (p) => flipPicture(p, "v"),
    "crop by the top left": (p) => cropTo(p, { x: 0.3, y: 0.35, w: 0.4, h: 0.35 }),
    "crop by the bottom right": (p) => cropTo(p, { x: 0.1, y: 0.2, w: 0.4, h: 0.3 }),
    uncrop: (p) => cropTo(p, { x: 0, y: 0, w: 1, h: 1 }),
  };
  const close = (a, b, what) => {
    for (const k of ["x", "y", "w", "h"]) near(a[k], b[k], 0.011, `${what}: ${k}`);
    assert.deepEqual([a.angle, a.crop, a.flip_h, a.flip_v], [b.angle, b.crop, b.flip_h, b.flip_v], what);
  };
  for (const [name, edit] of Object.entries(edits)) {
    const moved = move(base), edited = edit(base);
    close(mergePicture(moved, edited, base), edit(moved), `${name} over a move`);
    close(mergePicture(edited, moved, base), move(edited), `a move over the ${name}`);
  }
});
