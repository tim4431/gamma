// A ```svg fence's markup, checked before the figure shows it
// (docs/dev/diagrams.md). The browser does the isolating: the figure loads
// the result as an <img>, a document of its own that runs no script, loads
// nothing from elsewhere, and whose <style> and ids cannot reach the page.
// This is the second layer, which also keeps the downloaded file inert:
// - DOMPurify with its SVG and SVG-filter profiles only, no HTML. Its
//   allowlist already leaves out <script>, <foreignObject>, <set> and
//   <animate>, and it drops on* handlers, javascript: URLs and animations of
//   href. On top of that: no <a>, <image> or <font>, no data-* attributes,
//   <use> allowed, and href / xlink:href only to an "#id" in the drawing.
// - No attribute or <style> whose CSS could load something (unsafeCss).
// - Exactly one <svg> root with something inside.
// The root also scales like Mermaid's output: a viewBox from its width and
// height when it has none, and no inline width/height over the figure's CSS.
// Returns { svg, width, height } (the natural size, null where unknown) or
// throws an Error with an English sentence for the caller to translate.
import DOMPurify from "dompurify";
import { T } from "../i18n/i18n.js";

const LIMIT = 50000;
const SVG_NS = "http://www.w3.org/2000/svg";
const XLINK_NS = "http://www.w3.org/1999/xlink";
const XMLNS_NS = "http://www.w3.org/2000/xmlns/";

// CSS that could load something or run script, judged on the text as it is
// emitted: any url() but a reference to "#id", @import, image-set(),
// expression(), element(), src(), and backslashes (an escape can spell any
// of those). A comment that mentions url( counts too.
const LOCAL_URL = /url\(\s*(["']?)#[^"')\s]*\1\s*\)/gi;
const unsafeCss = (css) => /url\(|@import|image-set\(|expression\(|element\(|src\(|\\/i.test(css.replace(LOCAL_URL, ""));

// Its own instance: hooks are per instance, and Mermaid sanitizes its labels
// with the shared default one.
let purifier = null;
function purify() {
  if (purifier) return purifier;
  purifier = DOMPurify(window);
  purifier.addHook("uponSanitizeAttribute", (node, data) => {
    const value = data.attrValue || "";
    const href = data.attrName === "href" || data.attrName === "xlink:href";
    if (href ? !value.startsWith("#") : unsafeCss(value)) data.keepAttr = false;
  });
  return purifier;
}

// A length in user units (a bare number or px), else null.
const userUnits = (value) => (/^\s*(\d+\.?\d*|\.\d+)(px)?\s*$/i.test(value || "") ? parseFloat(value) || null : null);

export function sanitizeSvg(source) {
  const text = String(source || "");
  if (text.length > LIMIT) throw new Error(T("Diagram exceeds the 50,000 character limit."));
  const fragment = purify().sanitize(text, {
    USE_PROFILES: { svg: true, svgFilters: true },
    ADD_TAGS: ["use"],
    FORBID_TAGS: ["a", "image", "font"],
    ALLOW_DATA_ATTR: false,
    RETURN_DOM_FRAGMENT: true,
  });
  const roots = Array.from(fragment.children);
  const root = roots.length === 1 && roots[0].namespaceURI === SVG_NS && roots[0].localName === "svg" ? roots[0] : null;
  if (!root) throw new Error(T("Not an SVG document."));
  for (const style of root.querySelectorAll("style")) if (unsafeCss(style.textContent)) style.remove();
  if (!root.children.length) throw new Error(T("The SVG draws nothing."));

  // A standalone document: the image (and the download) needs the xlink
  // prefix declared on the root when any attribute uses it.
  const all = [root, ...root.querySelectorAll("*")];
  if (all.some((el) => Array.from(el.attributes).some((a) => a.namespaceURI === XLINK_NS))) {
    root.setAttributeNS(XMLNS_NS, "xmlns:xlink", XLINK_NS);
  }
  // The figure's CSS sizes the drawing (max-width: 100%, height: auto).
  const inline = root.getAttribute("style");
  if (inline != null) {
    const kept = inline.split(";").map((d) => d.trim())
      .filter((d) => d && !/^(?:min-|max-)?(?:width|height)\s*:/i.test(d)).join("; ");
    if (kept) root.setAttribute("style", kept); else root.removeAttribute("style");
  }
  let width = userUnits(root.getAttribute("width")), height = userUnits(root.getAttribute("height"));
  if (!root.hasAttribute("viewBox") && width && height) root.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const box = (root.getAttribute("viewBox") || "").trim().split(/[\s,]+/).map(Number);
  if (box.length === 4 && box.every(Number.isFinite) && box[2] > 0 && box[3] > 0) {
    width ??= height ? height * box[2] / box[3] : box[2];
    height ??= width * box[3] / box[2];
  }
  return { svg: new window.XMLSerializer().serializeToString(root), width, height };
}
