// sanitizeSvg needs a DOM for DOMPurify; jsdom supplies one under node.
import assert from "node:assert/strict";
import { test } from "node:test";
import { JSDOM } from "jsdom";

globalThis.window = new JSDOM("").window;
const { sanitizeSvg } = await import("../src/shared/lib/svgSanitize.js");

const parse = (svg) => new window.DOMParser().parseFromString(svg, "image/svg+xml").documentElement;
const clean = (source) => sanitizeSvg(source).svg;

test("script, event handlers and javascript: URLs are gone", () => {
  const svg = clean(`<svg onload="alert(1)" width="10" height="10">
    <script>alert(1)</script><script href="https://evil/x.js"/>
    <rect onclick="alert(2)" onmouseover="alert(3)" width="5" height="5"/>
    <textPath href="javascript:alert(4)">t</textPath>
    <animateTransform attributeName="href" values="javascript:alert(5)"/>
  </svg>`);
  assert.doesNotMatch(svg, /script|alert|onload|onclick|onmouseover|javascript:/i);
  assert.match(svg, /<rect width="5" height="5"\/>/);
});

test("foreignObject, image, a, set and font are removed; use stays", () => {
  const svg = clean(`<svg viewBox="0 0 10 10"><rect id="r" width="1" height="1"/>
    <foreignObject><div xmlns="http://www.w3.org/1999/xhtml"><iframe src="https://evil"></iframe></div></foreignObject>
    <image href="https://evil/pixel.png"/><a href="https://evil"><text>link</text></a>
    <use href="#r" x="2"/><use href="https://evil/sprites.svg#r" x="4"/>
    <set attributeName="fill" to="red"/><font><text>f</text></font></svg>`);
  assert.doesNotMatch(svg, /foreignObject|iframe|<image|<a[\s>]|<set|<font|evil/i);
  assert.match(svg, /<text>link<\/text>/, "an unwrapped link keeps its text");
  assert.match(svg, /<use href="#r" x="2"\/><use x="4"\/>/, "a <use> keeps a local href only");
});

test("only #local hrefs survive", () => {
  const root = parse(clean(`<svg xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10">
    <defs><path id="p" d="M0 0H10"/><linearGradient id="g2" href="https://evil/g.svg#x"/></defs>
    <text><textPath href="#p">along</textPath><textPath xlink:href="#p">again</textPath></text>
    <filter id="f"><feImage href="https://evil/y.png"/><feImage xlink:href="data:image/png;base64,AAAA"/></filter></svg>`));
  const hrefs = Array.from(root.querySelectorAll("*")).flatMap((el) => Array.from(el.attributes)
    .filter((a) => a.localName === "href").map((a) => a.value));
  assert.deepEqual(hrefs, ["#p", "#p"]);
  assert.equal(root.querySelectorAll("feImage").length, 2, "the element stays, its href goes");
});

test("data-* attributes are stripped", () => {
  const svg = clean('<svg viewBox="0 0 10 10"><g data-diagram-kind="mermaid" data-block-id="x" class="c"><rect width="5" height="5"/></g></svg>');
  assert.doesNotMatch(svg, /data-/);
  assert.match(svg, /<g class="c">/);
});

test("CSS that could load something is stripped, judged on the emitted text", () => {
  const svg = clean(`<svg viewBox="0 0 10 10">
    <defs><linearGradient id="g"/></defs>
    <style>@import url(https://evil/a.css); rect { fill: red }</style>
    <style>rect { background: url("https://evil/b.png") }</style>
    <style>rect { background: image-set("https://evil/c.png" 1x) }</style>
    <style>rect { b\\61ckground: \\75rl(https://evil/d.png) }</style>
    <style>/* no url( here */ circle { fill: blue }</style>
    <style>text { font: 12px sans-serif; fill: url(#g) }</style>
    <rect style="fill: url(https://evil/e.png)" width="1" height="1"/>
    <rect style="filter: expression(alert(1))" width="2" height="2"/>
    <rect fill="url(http://evil/f.svg#x)" stroke="url(#g)" width="3" height="3"/>
    <rect style='--a: "/*"; background-image: url(https://evil.example/x.png); --b: "*/"' width="4" height="4"/>
    <rect style="fill: url( '#g' ); stroke: blue" width="5" height="5"/></svg>`);
  assert.doesNotMatch(svg, /evil|expression|@import|circle/);
  assert.equal((svg.match(/<style>/g) || []).length, 1, "only the safe <style> is left");
  assert.match(svg, /<style>text \{ font: 12px sans-serif; fill: url\(#g\) \}<\/style>/);
  assert.match(svg, /<rect width="1" height="1"\/>/);
  assert.match(svg, /<rect stroke="url\(#g\)" width="3" height="3"\/>/);
  assert.match(svg, /<rect width="4" height="4"\/>/, "a comment-smuggled url() loses the attribute");
  assert.match(svg, /<rect style="fill: url\( '#g' \); stroke: blue" width="5" height="5"\/>/);
});

test("the root is a standalone document", () => {
  let root = parse(clean('<svg viewBox="0 0 1 1"><rect width="1" height="1"/></svg>'));
  assert.equal(root.namespaceURI, "http://www.w3.org/2000/svg");
  assert.match(clean('<svg viewBox="0 0 1 1"><rect/></svg>'), /^<svg xmlns="http:\/\/www.w3.org\/2000\/svg"/);
  // xlink:href without a declaration in the source still gets its namespace.
  const svg = clean('<svg viewBox="0 0 1 1"><path id="p" d="M0 0"/><textPath xlink:href="#p">t</textPath></svg>');
  assert.match(svg, /^<svg [^>]*xmlns:xlink="http:\/\/www.w3.org\/1999\/xlink"/);
  root = parse(svg);
  assert.equal(root.querySelector("parsererror"), null);
  assert.equal(root.querySelector("textPath").getAttributeNS("http://www.w3.org/1999/xlink", "href"), "#p");
  assert.doesNotMatch(clean('<svg viewBox="0 0 1 1"><rect/></svg>'), /xmlns:xlink/, "only when xlink is used");
});

test("the root scales: a synthesized viewBox, the natural size, no inline size", () => {
  let r = sanitizeSvg('<svg width="400" height="200px" style="width: 900px; max-width: 10px; font-family: serif"><rect width="1" height="1"/></svg>');
  assert.deepEqual([r.width, r.height], [400, 200]);
  let root = parse(r.svg);
  assert.equal(root.getAttribute("viewBox"), "0 0 400 200");
  assert.equal(root.getAttribute("width"), "400", "the size attributes stay");
  assert.equal(root.getAttribute("style"), "font-family: serif");
  r = sanitizeSvg('<svg viewBox="0 0 300 150" style="height:10px"><rect width="1" height="1"/></svg>');
  assert.deepEqual([r.width, r.height], [300, 150]);
  assert.equal(parse(r.svg).hasAttribute("style"), false);
  r = sanitizeSvg('<svg width="100%" viewBox="0,0,640,480"><rect width="1" height="1"/></svg>');
  assert.deepEqual([r.width, r.height], [640, 480]);
  r = sanitizeSvg('<svg width="320" viewBox="0 0 640 480"><rect width="1" height="1"/></svg>');
  assert.deepEqual([r.width, r.height], [320, 240], "the viewBox gives the missing side");
  root = parse(sanitizeSvg('<svg width="10cm" height="5cm"><rect width="1" height="1"/></svg>').svg);
  assert.equal(root.hasAttribute("viewBox"), false, "no viewBox from units other than px");
  r = sanitizeSvg('<svg><rect width="1" height="1"/></svg>');
  assert.deepEqual([r.width, r.height], [null, null]);
});

test("a prolog is fine; anything but one <svg> throws", () => {
  const svg = clean('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n'
    + '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect width="1" height="1"/></svg>');
  assert.equal(svg, '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect width="1" height="1"/></svg>');
  for (const bad of ["<div>hi</div>", "", "just text", "<svg/><svg/>", "<math><mi>x</mi></math>", "<html><body><p>x</p></body></html>"]) {
    assert.throws(() => sanitizeSvg(bad), /Not an SVG document\./, bad);
  }
  assert.throws(() => sanitizeSvg("<svg><script>alert(1)</script></svg>"), /The SVG draws nothing\./);
  assert.throws(() => sanitizeSvg("<svg><style>rect { fill: url(https://evil/x) }</style></svg>"), /The SVG draws nothing\./);
});

test("the source is capped at 50,000 characters", () => {
  const body = (n) => `<svg><desc>${"x".repeat(n)}</desc><rect/></svg>`;
  assert.ok(sanitizeSvg(body(50000 - body(0).length)).svg);
  assert.throws(() => sanitizeSvg(body(50001 - body(0).length)), /50,000 character limit/);
});

test("HTML smuggled into the SVG is gone", () => {
  const svg = clean('<svg viewBox="0 0 1 1"><rect width="1" height="1"/><body onload="alert(1)"><p>hi<img src=x onerror=alert(2)></p></svg><img src=y onerror=alert(3)>');
  assert.doesNotMatch(svg, /body|onload|onerror|<p|<img|alert/i);
  // An HTML tag breaks out of the SVG while parsing: what is left is not one drawing.
  assert.throws(() => sanitizeSvg('<svg viewBox="0 0 1 1"><rect/><style><img src=x onerror=alert(1)></style><title>t</title></svg>'),
    /Not an SVG document\./);
  const escaped = clean('<svg viewBox="0 0 1 1"><rect/><title>&lt;/title&gt;&lt;img src=x onerror=alert(1)&gt;</title></svg>');
  assert.match(escaped, /<title>&lt;\/title&gt;&lt;img src=x onerror=alert\(1\)&gt;<\/title>/, "escaped markup stays text");
});
