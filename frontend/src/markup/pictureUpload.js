// A picture file on its way to a page, and a picture as shown on its way to
// the clipboard (docs/dev/pictures.md "Adding a picture"). PNG, JPEG, GIF
// and WebP up to MAX_PIXELS go up as they are (a GIF keeps its animation);
// anything else the browser decodes (HEIC and HEIF in Safari, TIFF, BMP,
// SVG …), and anything larger, is drawn on a canvas, downscaled, and sent
// as a JPEG, or as a PNG when it came in a format that may hold
// transparency. So the server (POST /api/upload-image) stores what it is
// given and needs no decoder of its own. encodePlan is pure, so node loads
// this module; the rest needs a browser.
import { canvasSize } from "../shared/lib/canvasSize.js";
import { PICTURE_URL } from "./picture.js";

// The most pixels a picture is stored with: a page of camera photos stays
// light. A canvas holds fewer still on an iPad (canvasSize's caps), so a
// picture re-encoded there is smaller again.
export const MAX_PIXELS = 24000000;
// What the server stores as it comes, and the formats that may be
// transparent (a re-encode keeps them PNG).
const AS_IS = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);
const CLEAR = new Set(["image/png", "image/webp", "image/gif", "image/svg+xml"]);
const JPEG_QUALITY = 0.9;

export const isImageFile = (file) => typeof file?.type === "string" && file.type.startsWith("image/");
// The image files a paste's or a drop's DataTransfer carries.
export const imageFiles = (data) => [...(data?.files || [])].filter(isImageFile);
// Whether a drag's DataTransfer carries image files and nothing else, read
// from its items (a dragover cannot read the files themselves yet).
export const onlyImages = (data) => {
  const items = [...(data?.items || [])];
  return items.length > 0 && items.every((it) => it.kind === "file" && isImageFile(it));
};
// Whether drag event `e` brings image files only over a page surface, a
// PDF page or a sheet, whose picture layer takes the drop
// (markup/PictureLayer.jsx): the app's drop overlay and a row's outline
// stay off there.
export const picturesDrag = (e) => onlyImages(e.dataTransfer) && !!e.target?.closest?.(".pdfPageWrap, .nbSheet");

// What becomes of a file of MIME `type` that decodes at `w` × `h` pixels:
// {keep: true, width, height} to send it as it is, else {keep: false,
// width, height, type}: drawn that large (within MAX_PIXELS and the
// canvas's caps, its aspect kept) and sent as `type`.
export function encodePlan(type, w, h) {
  if (AS_IS.has(type) && w * h <= MAX_PIXELS) return { keep: true, width: w, height: h };
  const { width, height } = canvasSize(w, h, Math.min(1, Math.sqrt(MAX_PIXELS / (w * h))));
  return { keep: false, width, height, type: CLEAR.has(type) ? "image/png" : "image/jpeg" };
}

// A Blob decoded as the browser shows it (its EXIF orientation applied):
// {image, width, height, close()}, or null when this browser cannot read
// it. createImageBitmap first; an <img> reads what it refuses (an SVG).
async function decode(blob) {
  try {
    const bitmap = await createImageBitmap(blob, { imageOrientation: "from-image" });
    return { image: bitmap, width: bitmap.width, height: bitmap.height, close: () => bitmap.close() };
  } catch { /* an <img> may still read it */ }
  const url = URL.createObjectURL(blob);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    if (img.naturalWidth && img.naturalHeight) {
      return { image: img, width: img.naturalWidth, height: img.naturalHeight, close: () => URL.revokeObjectURL(url) };
    }
  } catch { /* not a picture this browser reads */ }
  URL.revokeObjectURL(url);
  return null;
}

const toBlob = (canvas, type, quality) => new Promise((done) => canvas.toBlob(done, type, quality));

// `image` drawn on a canvas of `plan`'s size and encoded as its type: a
// Blob, or null when no canvas is to be had (WebKit refuses a context once
// the page's canvases hold its memory) or it will not encode (a canvas an
// SVG tainted throws).
async function redraw(image, plan) {
  const canvas = document.createElement("canvas");
  canvas.width = plan.width;
  canvas.height = plan.height;
  try {
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    // a JPEG has no transparency: what was clear comes out white, not black
    if (plan.type === "image/jpeg") { ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, plan.width, plan.height); }
    ctx.drawImage(image, 0, 0, plan.width, plan.height);
    return await toBlob(canvas, plan.type, JPEG_QUALITY);
  } catch {
    return null;
  } finally {
    canvas.width = 0;
    canvas.height = 0;
  }
}

// `file` ready to upload: {file, natural: {w, h}} with its pixel size as
// shown, the file itself or its re-encoding (encodePlan); null when this
// browser cannot decode it or cannot draw it again.
export async function preparePicture(file) {
  const decoded = await decode(file);
  if (!decoded) return null;
  try {
    const plan = encodePlan(file.type, decoded.width, decoded.height);
    if (plan.keep) return { file, natural: { w: plan.width, h: plan.height } };
    const blob = await redraw(decoded.image, plan);
    if (!blob) return null;
    const name = `${(file.name || "picture").replace(/\.[^.]*$/, "")}.${plan.type === "image/png" ? "png" : "jpg"}`;
    return { file: new File([blob], name, { type: plan.type }), natural: { w: plan.width, h: plan.height } };
  } finally {
    decoded.close();
  }
}

// `file` uploaded through `post` (transfers/FileChip.jsx postFile, which
// reports the upload in the background tasks): {url, natural} with the
// stored picture's reference and pixel size, or {error: "decode"} for a
// file this browser cannot read or draw, {error: "upload"} for one the
// server did not take. It never rejects, so a drop of several files places
// every one it can (markup/usePictures.js place).
export async function uploadPicture(file, post) {
  let ready = null, url = null;
  try { ready = await preparePicture(file); } catch { /* unreadable here */ }
  if (!ready) return { error: "decode" };
  try { url = (await post("/api/upload-image", ready.file))?.url; } catch { /* not taken */ }
  if (typeof url !== "string" || !PICTURE_URL.test(url)) return { error: "upload" };
  return { url, natural: ready.natural };
}

// A PNG of picture `pic` as the page shows it, its image fetched from `src`
// (the upload through shared/lib/utils.js assetUrl): the crop at its own
// pixel density, mirrored by the flips and turned by its angle, on a clear
// canvas the size of the turned frame (within canvasSize's caps). For
// "Copy image" (shared/lib/copyImage.js copyPng).
export async function shownPng(src, pic) {
  const res = await fetch(src);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const decoded = await decode(await res.blob());
  if (!decoded) throw new Error("Could not read the picture.");
  try {
    const { image, width: nw, height: nh } = decoded;
    const c = pic.crop, sw = c.w * nw, sh = c.h * nh;
    const px = Math.max(sw / pic.w, sh / pic.h); // pixels per point
    const fw = pic.w * px, fh = pic.h * px;
    const a = pic.angle * (Math.PI / 180), cos = Math.abs(Math.cos(a)), sin = Math.abs(Math.sin(a));
    const bw = fw * cos + fh * sin, bh = fw * sin + fh * cos;
    const size = canvasSize(bw, bh);
    const k = size.width / bw;
    const canvas = document.createElement("canvas");
    canvas.width = size.width;
    canvas.height = size.height;
    const ctx = canvas.getContext("2d");
    ctx.translate(size.width / 2, size.height / 2);
    ctx.rotate(a);
    ctx.scale(pic.flip_h ? -k : k, pic.flip_v ? -k : k);
    ctx.drawImage(image, c.x * nw, c.y * nh, sw, sh, -fw / 2, -fh / 2, fw, fh);
    const blob = await toBlob(canvas, "image/png");
    if (!blob) throw new Error("Could not draw the picture.");
    return blob;
  } finally {
    decoded.close();
  }
}
