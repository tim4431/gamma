// The pure half of "Copy image": which pictures are vectors, and the SVG a
// vector picture is copied as. jsdom supplies the DOMParser and serializer.
import assert from "node:assert/strict";
import { test } from "node:test";
import { JSDOM } from "jsdom";

globalThis.window = new JSDOM("").window;
const { isVectorPicture, shownSvg } = await import("../src/shared/lib/copyImage.js");

const parse = (svg) => new window.DOMParser().parseFromString(svg, "image/svg+xml").documentElement;

test("vector pictures: an inline svg, an SVG data: URL or file; bitmaps are not", () => {
  assert.equal(isVectorPicture({ localName: "svg" }), true);
  assert.equal(isVectorPicture({ localName: "img", src: "data:image/svg+xml;charset=utf-8,%3Csvg%3E" }), true);
  assert.equal(isVectorPicture({ localName: "img", src: "http://h/api/uploads/ab12.svg?ws=w1" }), true);
  assert.equal(isVectorPicture({ localName: "img", currentSrc: "http://h/a.SVG", src: "http://h/a.SVG" }), true);
  assert.equal(isVectorPicture({ localName: "img", src: "http://h/api/uploads/ab12.png" }), false);
  assert.equal(isVectorPicture({ localName: "img", src: "http://h/svg/ab12.jpg" }), false);
  assert.equal(isVectorPicture(null), false);
});

test("the copy takes the shown size; a fixed size without a viewBox gets one, so it scales", () => {
  const root = parse(shownSvg('<svg xmlns="http://www.w3.org/2000/svg" width="320px" height="200"><rect width="5" height="5"/></svg>',
    { width: 160.4, height: 100 }));
  assert.equal(root.getAttribute("viewBox"), "0 0 320 200");
  assert.equal(root.getAttribute("width"), "160");
  assert.equal(root.getAttribute("height"), "100");
  assert.equal(root.querySelector("rect").getAttribute("width"), "5", "the drawing is as it was");
});

test("a viewBox stays; Mermaid's width=100% and max-width give way to the shown size", () => {
  const root = parse(shownSvg('<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="-8 -8 452 300" style="max-width: 452px; font-family: serif"><g/></svg>',
    { width: 452, height: 300, background: "rgb(30, 30, 30)" }));
  assert.equal(root.getAttribute("viewBox"), "-8 -8 452 300");
  assert.equal(root.getAttribute("width"), "452");
  assert.equal(root.style.maxWidth, "");
  assert.equal(root.style.fontFamily, "serif", "other style stays");
  assert.equal(root.style.backgroundColor, "rgb(30, 30, 30)", "the background it is shown on");
});

test("a relative size without a viewBox is not given one", () => {
  const root = parse(shownSvg('<svg xmlns="http://www.w3.org/2000/svg" width="50%" height="10em"/>', { width: 40, height: 20 }));
  assert.equal(root.hasAttribute("viewBox"), false);
  assert.equal(root.getAttribute("width"), "40");
});

test("markup that is not an SVG document is refused", () => {
  assert.throws(() => shownSvg("<html/>", { width: 1, height: 1 }), /Not an SVG document/);
  assert.throws(() => shownSvg("<svg", { width: 1, height: 1 }), /Not an SVG document/);
});
