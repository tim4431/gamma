// Copying a picture to the clipboard as an image: the object menu's "Copy
// image" (editor/MdObject.jsx), and a page picture's (copyPng, below). The
// picture is the element showing it, an <img> or an inline <svg>. The async clipboard writes image/png in every
// browser and image/svg+xml in current Chromium, and refuses every other
// image type ("Type image/jpeg not supported on write"), so:
//
// - a PNG goes as it is;
// - any other bitmap (JPEG, WebP, GIF …) is redrawn as a PNG, pixel for pixel;
// - a vector picture (an SVG upload, an svg fence's image, a Mermaid figure)
//   goes as an SVG where the browser takes one, plus a PNG of it at twice
//   its size, because desktop apps paste only the PNG. Both are the drawing
//   as shown: at the size and on the background it has on screen, so a
//   Mermaid figure drawn for the dark theme keeps its dark page.
//
// The ClipboardItem is made in the click itself and given promised blobs, so
// Safari keeps the user's gesture while the picture is fetched and drawn.
// The async clipboard exists only in secure contexts (https, localhost), and
// no legacy fallback carries an image: callers ask canCopyImage() first.
import { canvasSize } from "./canvasSize.js";

const PNG = "image/png";
const SVG = "image/svg+xml";
// Backing pixels per shown pixel of a vector picture's PNG.
const VECTOR_RATIO = 2;

export function canCopyImage() {
  return typeof window !== "undefined" && !!window.isSecureContext
    && !!navigator.clipboard?.write && typeof window.ClipboardItem === "function";
}

// Drawn as a vector: an inline <svg>, or an <img> of an SVG file or data: URL.
// (A misnamed SVG file is still found by its type once fetched; this only
// decides up front whether the clipboard gets an SVG flavor.)
export function isVectorPicture(el) {
  if (el?.localName === "svg") return true;
  const src = el?.currentSrc || el?.src || "";
  return /^data:image\/svg\+xml[;,]/i.test(src) || /\.svg(?:[?#]|$)/i.test(src);
}

// The SVG markup as a document of its own at the size it is shown, on the
// background it is shown on. Without a viewBox, numeric width and height
// become one first (as svgSanitize does), so the new size scales the drawing
// instead of cropping it; size declarations in the root's style go, since
// they would override the new size.
export function shownSvg(markup, { width, height, background = null }) {
  const doc = new window.DOMParser().parseFromString(markup, SVG);
  const root = doc.documentElement;
  if (root?.localName !== "svg" || doc.getElementsByTagName("parsererror").length) throw new Error("Not an SVG document.");
  const num = (v) => (/^\s*\d*\.?\d+(px)?\s*$/.test(v || "") ? parseFloat(v) : null);
  const [w0, h0] = [num(root.getAttribute("width")), num(root.getAttribute("height"))];
  if (!root.hasAttribute("viewBox") && w0 && h0) root.setAttribute("viewBox", `0 0 ${w0} ${h0}`);
  root.setAttribute("width", String(Math.round(width)));
  root.setAttribute("height", String(Math.round(height)));
  for (const p of ["width", "height", "min-width", "min-height", "max-width", "max-height"]) root.style?.removeProperty(p);
  if (background) root.style.setProperty("background-color", background);
  return new window.XMLSerializer().serializeToString(doc);
}

// The first opaque background behind an element, as computed.
export function shownBackground(el) {
  for (let node = el; node?.nodeType === 1; node = node.parentElement) {
    const color = getComputedStyle(node).backgroundColor;
    if (color && color !== "transparent" && !/^rgba\(.*,\s*0\)$/.test(color)) return color;
  }
  return null;
}

async function fetchBlob(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.blob();
}

function canvasPng(image, width, height) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  canvas.getContext("2d").drawImage(image, 0, 0, width, height);
  return new Promise((resolve, reject) => canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("Could not draw the picture."))), PNG));
}

// A vector picture's PNG: its shown SVG drawn at VECTOR_RATIO (or the
// screen's ratio, if higher), within the canvas limits. Loaded from a data:
// URL, not a blob: one: Chromium taints the canvas for a blob: SVG that
// holds a foreignObject, which every Mermaid label is.
async function vectorPng(svgBlob, shown) {
  const { width, height } = canvasSize(shown.width, shown.height, Math.max(VECTOR_RATIO, window.devicePixelRatio || 1));
  const image = new Image();
  image.src = `data:${SVG};charset=utf-8,${encodeURIComponent(await svgBlob.text())}`;
  await image.decode();
  return canvasPng(image, width, height);
}

// Resolves true when the picture reached the clipboard.
export async function copyImage(el) {
  if (!el || !canCopyImage()) return false;
  // Measured now, in the click: the picture as the user sees it.
  const box = el.getBoundingClientRect();
  const shown = { width: box.width, height: box.height, background: shownBackground(el) };
  const src = el.localName === "svg" ? null : el.currentSrc || el.src;
  const svgOf = (markup) => new Blob([shownSvg(markup, shown)], { type: SVG });
  const markup = el.localName === "svg" ? new window.XMLSerializer().serializeToString(el) : null;
  const vector = markup != null ? Promise.resolve(markup).then(svgOf)
    : isVectorPicture(el) ? fetchBlob(src).then((blob) => blob.text()).then(svgOf)
    : null;
  const png = vector ? vector.then((svg) => vectorPng(svg, shown)) : fetchBlob(src).then(async (blob) => {
    if (blob.type === PNG) return blob;
    if (blob.type.startsWith(SVG)) return vectorPng(svgOf(await blob.text()), shown);
    const bitmap = await createImageBitmap(blob);
    try {
      const { width, height } = canvasSize(bitmap.width, bitmap.height);
      return await canvasPng(bitmap, width, height);
    } finally {
      bitmap.close();
    }
  });
  const flavors = { [PNG]: png };
  if (vector && window.ClipboardItem.supports?.(SVG)) flavors[SVG] = vector;
  // A failed fetch or drawing is reported by the write; without a write
  // (it threw first) nothing else would handle it.
  for (const blob of Object.values(flavors)) blob.catch(() => {});
  try {
    await navigator.clipboard.write([new window.ClipboardItem(flavors)]);
    return true;
  } catch {
    return false;
  }
}

// A PNG still being made (`png`, a promise of its blob: a picture drawn as
// the page shows it, markup/pictureUpload.js shownPng) to the clipboard.
// The ClipboardItem is made at once, in the click, so Safari keeps the
// gesture while the picture is fetched and drawn. Resolves true when it got
// there.
export async function copyPng(png) {
  png.catch(() => {});
  if (!canCopyImage()) return false;
  try {
    await navigator.clipboard.write([new window.ClipboardItem({ [PNG]: png })]);
    return true;
  } catch {
    return false;
  }
}
