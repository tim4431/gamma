// node --test tests/pictureEdits.test.mjs (from frontend/) — what the page
// does with a picture beyond its stored rules (picture.test.mjs): the crop
// mode's geometry, the z-order, the Markdown line a row copies as, what a
// file becomes before it goes up and what its upload answers, and which
// drags a surface takes.
import assert from "node:assert/strict";
import { test } from "node:test";
import { captionText, dragCrop, imageDelta, normalizePicture, orderPicture, pictureMarkdown } from "../src/markup/picture.js";
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

// A page with two pictures on PDF page 1, one on page 2, and a sheet with
// two of its own, one of which has a note under it.
const node = (id, properties = {}, children = []) => ({ id, content: "", properties, children });
const picOn = (id, page, children = []) => node(id, { picture: { url: URL_ }, ...(page ? { pdf_page: page } : {}) }, children);
const tree = () => [
  picOn("a", 1),
  node("note", {}, [picOn("b", 1)]),
  picOn("c", 2),
  picOn("d", 1, [node("dNote")]),
  node("sheet", { sheet: {} }, [picOn("s1", null, [node("s1Note")]), picOn("s2", null)]),
];
const ids = (list) => list.flatMap((b) => [b.id, ...ids(b.children || [])]);

test("bring to front puts a picture after the last of its surface's, with its notes", () => {
  assert.deepEqual(ids(orderPicture(tree(), "a", "front")), ["note", "b", "c", "d", "dNote", "a", "sheet", "s1", "s1Note", "s2"]);
  assert.deepEqual(ids(orderPicture(tree(), "s1", "front")), ["a", "note", "b", "c", "d", "dNote", "sheet", "s2", "s1", "s1Note"]);
  const t = tree();
  assert.equal(orderPicture(t, "d", "front"), t, "already in front: the same tree");
  assert.equal(orderPicture(t, "c", "back"), t, "alone on its page");
});

test("send to back puts a picture before the first of its surface's, beside it in the notes", () => {
  const out = orderPicture(tree(), "d", "back");
  assert.deepEqual(ids(out), ["d", "dNote", "a", "note", "b", "c", "sheet", "s1", "s1Note", "s2"]);
  assert.equal(out[0].parentId, null, "a top-level block now");
  const nested = orderPicture(tree(), "a", "front");
  assert.equal(nested.at(-2).id, "a");
  const sheet = orderPicture(tree(), "s2", "back");
  assert.deepEqual(ids(sheet.at(-1).children), ["s2", "s1", "s1Note"]);
  assert.equal(sheet.at(-1).children[0].parentId, "sheet", "it stays under the sheet");
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

test("an upload never rejects: a canvas refused or tainted, or a failed post, says why instead", async () => {
  const cases = [
    ["no context (WebKit's canvas memory used up)", { canvas: { getContext: () => null } }, "decode"],
    ["a draw that throws", { canvas: { getContext: () => ({ fillRect() {}, drawImage() { throw new TypeError("bad image"); } }) } }, "decode"],
    ["a tainted canvas's toBlob throws", { canvas: { toBlob() { throw new Error("SecurityError"); } } }, "decode"],
    ["no blob", { canvas: { toBlob: (done) => done(null) } }, "decode"],
    ["the post throws", { post: async () => { throw new Error("offline"); } }, "upload"],
    ["the server answers no picture", { post: async () => ({ url: "/api/uploads/x.svg" }) }, "upload"],
  ];
  for (const [what, stubs, error] of cases) {
    const { out, canvases, posted } = await upload(heic(), stubs);
    assert.deepEqual(out, { error }, what);
    assert.ok(canvases.every((c) => c.width === 0 && c.height === 0), `${what}: the canvas emptied`);
    assert.equal(posted.length, error === "decode" ? 0 : 1, `${what}: posted only when ready`);
  }
});
