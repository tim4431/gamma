// node --test tests/overPictures.test.mjs (from frontend/) — where the mask a
// highlighter is drawn through over the pictures (ink/overPictures.js) puts
// each picture: as PictureView draws it, so its crop lands on the turned
// frame picture.js turnedCorners gives, mirrored by its flips.
import assert from "node:assert/strict";
import { test } from "node:test";
import { pictureShape } from "../src/ink/overPictures.js";
import { normalizePicture, turnedCorners } from "../src/markup/picture.js";

const UPLOAD = "/api/uploads/abc.png";

// Where `shape` puts the point (u, v) of its whole image (fractions of it),
// in the surface's points: the image's box, its mirror, the frame's
// viewport (a translation by its corner), then the turn about its centre.
function place(shape, [u, v]) {
  let x = shape.image.x + u * shape.image.width, y = shape.image.y + v * shape.image.height;
  if (shape.flip) {
    const [a, , , d, e, f] = shape.flip.match(/^matrix\((.*)\)$/)[1].split(" ").map(Number);
    [x, y] = [a * x + e, d * y + f];
  }
  x += shape.frame.x;
  y += shape.frame.y;
  if (shape.turn) {
    const [deg, cx, cy] = shape.turn.match(/^rotate\((.*)\)$/)[1].split(" ").map(Number);
    const t = (deg * Math.PI) / 180, dx = x - cx, dy = y - cy;
    [x, y] = [cx + dx * Math.cos(t) - dy * Math.sin(t), cy + dx * Math.sin(t) + dy * Math.cos(t)];
  }
  return [x, y];
}

const near = (got, want, what) => {
  assert.ok(Math.abs(got[0] - want[0]) < 1e-6 && Math.abs(got[1] - want[1]) < 1e-6, `${what}: ${got} is not ${want}`);
};

const CASES = [
  { note: "upright, whole", pic: { x: 100, y: 50, w: 200, h: 100 } },
  { note: "cropped", pic: { x: 40, y: 300, w: 90, h: 120, crop: { x: 0.25, y: 0.1, w: 0.5, h: 0.6 } } },
  { note: "flipped across", pic: { x: 10, y: 20, w: 80, h: 60, crop: { x: 0.2, y: 0, w: 0.8, h: 1 }, flip_h: true } },
  { note: "flipped down", pic: { x: 10, y: 20, w: 80, h: 60, crop: { x: 0, y: 0.3, w: 1, h: 0.7 }, flip_v: true } },
  { note: "both flips, turned", pic: { x: 250, y: 400, w: 160, h: 90, angle: 33, crop: { x: 0.1, y: 0.2, w: 0.6, h: 0.5 }, flip_h: true, flip_v: true } },
  { note: "turned against the clock, stretched", pic: { x: -20, y: 600, w: 300, h: 40, angle: -120, crop: { x: 0.5, y: 0.5, w: 0.5, h: 0.25 } } },
];

for (const { note, pic: raw } of CASES) {
  test(`a picture in the highlighter's mask: ${note}`, () => {
    const pic = normalizePicture({ url: UPLOAD, ...raw }), shape = pictureShape(pic), c = pic.crop;
    assert.deepEqual(shape.frame, { x: pic.x, y: pic.y, width: pic.w, height: pic.h }, "the frame clips the image");
    // the crop's corners, top-left clockwise, on the turned frame's corners as the flips mirror them
    const crop = [[c.x, c.y], [c.x + c.w, c.y], [c.x + c.w, c.y + c.h], [c.x, c.y + c.h]];
    const frame = turnedCorners(pic);
    const mirrored = (i) => {
      const across = pic.flip_h ? [1, 0, 3, 2][i] : i;
      return pic.flip_v ? [3, 2, 1, 0][across] : across;
    };
    crop.forEach((uv, i) => near(place(shape, uv), frame[mirrored(i)], `crop corner ${i}`));
  });
}

test("an upright, unmirrored picture needs no transform", () => {
  const shape = pictureShape(normalizePicture({ url: UPLOAD, x: 1, y: 2, w: 30, h: 40 }));
  assert.equal(shape.flip, null);
  assert.equal(shape.turn, null);
  assert.deepEqual(shape.image, { x: 0, y: 0, width: 30, height: 40 });
});
