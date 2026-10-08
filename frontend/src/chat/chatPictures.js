// Pictures in the chat (docs/dev/ai.md "Pictures"): what the composer holds
// and a saved message keeps, as the server reads them (gamma/ai_pictures.py).
// Pure functions, so the dock, App and the tests share them.
//
// A picture is one of:
//   {kind: "pasted" | "file", url, width, height, name?}  a stored upload
//     (POST /api/ai/pictures sized it to the model's size and stored it
//     under its content hash; the message keeps the URL)
//   {kind: "area" | "view", page_id, page, box, ink?}       a region of a
//     PDF page the server renders on demand (an area highlight, or what
//     the user sees), box = [x0, y0, x1, y1] as page fractions
//   "data:image/…"                                           a data URL, as
//     chats saved before the store kept them; shown, never sent again
import { t } from "../shared/i18n/i18n.js";

export const DEFAULT_PICTURE_BUDGET = 12;
export const MAX_PICTURE_BUDGET = 64;
// A file the composer takes: the server reads this much and sizes it down.
export const MAX_PICTURE_BYTES = 25 * 1024 * 1024;
export const PICTURE_ROUTE = "/api/ai/page-image";
export const INK_PICTURE_ROUTE = "/api/ai/ink-image";

// Four decimals of a page fraction: how the server rounds a box (`parse_box`).
export const round4 = (v) => Math.round(v * 10000) / 10000;

export function isPictureFile(file) {
  return !!file && typeof file.type === "string" && file.type.startsWith("image/") && file.size <= MAX_PICTURE_BYTES;
}

// The box of a highlight shape (`pdf_position`: the rectangle in pixels of a
// capture-time render of width × height) as page fractions, top-left
// origin, rounded like the server rounds a saved box; null when unusable.
export function regionBox(position) {
  const r = position?.boundingRect;
  const w = Number(position?.width), h = Number(position?.height);
  if (!r || !(w > 0) || !(h > 0)) return null;
  const clamp = (v) => Math.min(1, Math.max(0, v));
  const box = [clamp(r.x1 / w), clamp(r.y1 / h), clamp(r.x2 / w), clamp(r.y2 / h)].map(round4);
  return box[0] < box[2] && box[1] < box[3] ? box : null;
}

// A region picture of `page` (1-based) of the page `pageId`; `box` null is
// the whole page; `ink` draws the user's handwriting on it.
export function regionPicture(kind, pageId, page, box, { ink = false } = {}) {
  if (!pageId || !(page >= 1)) return null;
  return { kind, page_id: pageId, page, ...(box ? { box } : {}), ...(ink ? { ink: true } : {}) };
}

export function isRegion(p) {
  return !!p && typeof p === "object" && (p.kind === "area" || p.kind === "view");
}

// A picture that came from the PDF (an area drag, a highlight click, "what
// I see"): what the snapshot auto-clear drops; a pasted picture never.
export function isPdfPicture(p) {
  return isRegion(p);
}

// The same-origin URL that shows the picture (before the workspace and
// share parameters a render site adds; a data URL as it is).
export function pictureUrl(p) {
  if (typeof p === "string") return p;
  if (!p || typeof p !== "object") return "";
  if (isRegion(p)) {
    let url = `${PICTURE_ROUTE}/${encodeURIComponent(p.page_id)}?page=${Number(p.page) || 1}`;
    if (Array.isArray(p.box) && p.box.length === 4) url += `&box=${p.box.map((v) => round4(Number(v))).join(",")}`;
    if (p.ink) url += "&ink=1";
    return url;
  }
  return typeof p.url === "string" ? p.url : "";
}

export function samePicture(a, b) {
  return !!a && !!b && pictureUrl(a) === pictureUrl(b);
}

// What a picture reads as, for an alt text and a title.
export function pictureAlt(p) {
  if (typeof p === "string") return t("Pasted image");
  if (!p || typeof p !== "object") return "";
  switch (p.kind) {
    case "file": return p.name ? t("Image file {name}", { name: p.name }) : t("Image file");
    case "area": return t("Region of PDF page {page}", { page: p.page });
    case "view": return p.ink ? t("PDF page {page} as you see it, with your handwriting", { page: p.page })
      : t("PDF page {page} as you see it", { page: p.page });
    default: return t("Pasted image");
  }
}

// The pictures of a saved message as the request's history carries them:
// the stored and region ones (the server reads them back); a data URL of
// an older chat is shown but never sent again.
export function historyPictures(images) {
  return (images || []).filter((p) => p && typeof p === "object" && pictureUrl(p));
}

// The picture a reply's tool chip expands to: the server names it
// (`picture` on the chip, gamma/ai_tools.py); chips saved before that
// derive it from what they carry.
export function chipPicture(action) {
  if (!action || action.error) return "";
  if (typeof action.picture === "string" && action.picture) return action.picture;
  if (action.kind === "view" && action.page_id && action.pdf_page) return pictureUrl(regionPicture("view", action.page_id, action.pdf_page));
  if (action.kind === "ink" && action.block_id) return `${INK_PICTURE_ROUTE}/${encodeURIComponent(action.block_id)}`;
  return "";
}

// Up to `max` pictures added to `list`, skipping ones it holds already.
export function addPictures(list, added, max = MAX_PICTURE_BUDGET) {
  const out = [...list];
  for (const p of added) {
    if (out.length >= max) break;
    if (!out.some((q) => samePicture(q, p))) out.push(p);
  }
  return out.length === list.length ? list : out;
}
