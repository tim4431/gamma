import assert from "node:assert/strict";
import { test } from "node:test";
import { addPictures, chipPicture, historyPictures, isPdfPicture, isPictureFile, pictureAlt, pictureUrl, regionBox,
  regionPicture, samePicture } from "../src/chat/chatPictures.js";

test("a highlight shape's rectangle becomes a box of page fractions", () => {
  const position = { width: 800, height: 1000, boundingRect: { x1: 80, y1: 100, x2: 400, y2: 300 } };
  assert.deepEqual(regionBox(position), [0.1, 0.1, 0.5, 0.3]);
  // Rounded like the server rounds a saved box; clamped to the page.
  assert.deepEqual(regionBox({ width: 3, height: 3, boundingRect: { x1: -1, y1: 1, x2: 2, y2: 9 } }), [0, 0.3333, 0.6667, 1]);
  assert.equal(regionBox({ width: 800, height: 1000, boundingRect: { x1: 5, y1: 5, x2: 5, y2: 9 } }), null);
  assert.equal(regionBox({ width: 0, height: 1000, boundingRect: { x1: 1, y1: 1, x2: 2, y2: 2 } }), null);
  assert.equal(regionBox(null), null);
});

test("region pictures name their page and box, and read as URLs the server renders", () => {
  const area = regionPicture("area", "p1", 4, [0.1, 0.2, 0.3, 0.4]);
  assert.deepEqual(area, { kind: "area", page_id: "p1", page: 4, box: [0.1, 0.2, 0.3, 0.4] });
  assert.equal(pictureUrl(area), "/api/ai/page-image/p1?page=4&box=0.1,0.2,0.3,0.4");
  const view = regionPicture("view", "p1", 2, null, { ink: true });
  assert.deepEqual(view, { kind: "view", page_id: "p1", page: 2, ink: true });
  assert.equal(pictureUrl(view), "/api/ai/page-image/p1?page=2&ink=1");
  assert.equal(regionPicture("area", "", 1, null), null);
  assert.equal(regionPicture("area", "p1", 0, null), null);
  assert.ok(isPdfPicture(area) && isPdfPicture(view));
});

test("stored pictures and old data URLs read as their own URL", () => {
  const stored = { kind: "pasted", url: "/api/uploads/abc.jpg", width: 10, height: 10 };
  assert.equal(pictureUrl(stored), "/api/uploads/abc.jpg");
  assert.equal(pictureUrl("data:image/png;base64,AAAA"), "data:image/png;base64,AAAA");
  assert.equal(pictureUrl({ kind: "pasted" }), "");
  assert.equal(pictureUrl(null), "");
  assert.ok(!isPdfPicture(stored) && !isPdfPicture("data:image/png;base64,AAAA"));
  assert.ok(samePicture(stored, { kind: "file", url: "/api/uploads/abc.jpg" }));
  assert.ok(!samePicture(stored, { kind: "pasted", url: "/api/uploads/xyz.jpg" }));
});

test("pictures are named for the user", () => {
  assert.equal(pictureAlt({ kind: "pasted", url: "/api/uploads/a.jpg" }), "Pasted image");
  assert.equal(pictureAlt({ kind: "file", url: "/api/uploads/a.jpg", name: "setup.png" }), "Image file setup.png");
  assert.equal(pictureAlt({ kind: "area", page_id: "p", page: 3 }), "Region of PDF page 3");
  assert.equal(pictureAlt({ kind: "view", page_id: "p", page: 3, ink: true }), "PDF page 3 as you see it, with your handwriting");
  assert.equal(pictureAlt("data:image/png;base64,AAAA"), "Pasted image");
});

test("the request's history carries stored and region pictures, never a data URL", () => {
  const stored = { kind: "pasted", url: "/api/uploads/abc.jpg" };
  const area = { kind: "area", page_id: "p1", page: 1, box: [0, 0, 1, 1] };
  assert.deepEqual(historyPictures([stored, "data:image/png;base64,AAAA", area, null, { kind: "pasted" }]), [stored, area]);
  assert.deepEqual(historyPictures(undefined), []);
});

test("a chip's picture is the server's, else derived from what the chip carries", () => {
  assert.equal(chipPicture({ kind: "view", picture: "/api/ai/page-image/p?page=2", page_id: "p", pdf_page: 2 }), "/api/ai/page-image/p?page=2");
  assert.equal(chipPicture({ kind: "view", page_id: "p", pdf_page: 3 }), "/api/ai/page-image/p?page=3");
  assert.equal(chipPicture({ kind: "ink", block_id: "b" }), "/api/ai/ink-image/b");
  assert.equal(chipPicture({ kind: "clip", url: "/api/uploads/c.jpg" }), "/api/uploads/c.jpg");
  assert.equal(chipPicture({ kind: "view", page_id: "p", pdf_page: 3, error: true }), "");
  assert.equal(chipPicture({ kind: "read", page_id: "p" }), "");
  assert.equal(chipPicture(null), "");
});

test("pictures are added once each, up to the budget; nothing changes when nothing is added", () => {
  const a = { kind: "pasted", url: "/api/uploads/a.jpg" };
  const b = { kind: "area", page_id: "p", page: 1, box: [0, 0, 1, 1] };
  const list = [a];
  assert.equal(addPictures(list, [{ ...a }]), list);
  assert.deepEqual(addPictures(list, [b, { ...b }]), [a, b]);
  assert.deepEqual(addPictures(list, [b], 1), [a]);
  assert.equal(addPictures([], [], 3).length, 0);
});

test("only images of a size the server takes are attached", () => {
  assert.ok(isPictureFile({ type: "image/png", size: 1000 }));
  assert.ok(!isPictureFile({ type: "application/pdf", size: 1000 }));
  assert.ok(!isPictureFile({ type: "image/png", size: 30 * 1024 * 1024 }));
  assert.ok(!isPictureFile(null));
});
