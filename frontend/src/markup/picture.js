// Pictures: a photo, a screenshot or a figure placed on a PDF page or a
// sheet of paper (docs/dev/pictures.md). The client half of
// gamma/picture.py; pure, so it runs under node --test.
//
// A picture is a block whose `content` is its caption (it may be empty) and
// whose `picture` property places it: `{url, x, y, w, h, angle, crop,
// flip_h, flip_v}` in points on its surface, placed as a text box is (on the
// nearest sheet above it, else on its `pdf_page`, else on no surface). `x`,
// `y` is the top-left of the frame before the turn (negative where a turned
// frame's own corner lies off the surface) and `w`, `h` its size, the
// picture as shown after the crop and before the turn. `angle` is the
// turn about the frame's centre in degrees, clockwise as seen (CSS
// `rotate`), in [-180, 180). `crop` is `{x, y, w, h}` as fractions of the
// image as the browser shows it. `flip_h`, `flip_v` mirror it about the
// frame's own axes. Everything that draws one applies the crop, then the
// flips, then the turn. `locked`, stored only when true, keeps it where it
// is: the page neither moves nor edits it (PictureLayer.jsx, usePictures.js
// change). `opacity`, in (0, 1] and stored only below 1, is how strongly
// the image shows over what lies under it. `z`, stored only when not 0,
// stacks it among its surface's pictures (stackOrder). Stored values are
// read through normalizePicture, never trusted; tests/shared/picture.json
// and picturemerge.json pin both halves. The second half is the geometry
// the handles, the menu and the lasso use.
import { pointInPolygon, transformPoint } from "../ink/ink.js";
import { isSheet, round2 } from "../notebook/notebook.js";
import { findBlock, insertSibling, isInkGroup, updateBlockTree } from "../shared/model/blockModel.js";
import { MAX_COORD, isTextBox } from "./textBox.js";

export { MAX_COORD }; // a text box's bound: the largest page side a PDF allows
export const MIN_SIDE = 8; // the least side of a frame, in points
export const CROP_MIN = 0.01; // the least side of a crop, as a fraction of the image
export const MIN_OPACITY = 0.01; // the least opacity stored: one hundredth, the rounding's step
export const MAX_Z = 1e9; // the bound of `z` either way: more Bring to front steps than a note takes
export const DEFAULT_SIDE = 144;
// An upload reference to a picture the browser draws and the PDF writers
// embed: the stem as stored, an extension in any case.
export const PICTURE_URL = /^\/api\/uploads\/[0-9A-Za-z_-]{1,128}\.(?:[pP][nN][gG]|[jJ][pP][eE]?[gG]|[gG][iI][fF]|[wW][eE][bB][pP]|[sS][vV][gG])$/;
export const isSvgUrl = (url) => /\.svg$/i.test(url || "");
const WHOLE = Object.freeze({ x: 0, y: 0, w: 1, h: 1 });
const CROP_KEYS = ["x", "y", "w", "h"];

const finite = (v) => typeof v === "number" && Number.isFinite(v);
const num = (v, lo, hi, fallback) => (finite(v) ? round2(Math.min(hi, Math.max(lo, v))) + 0 : fallback); // -0 as 0
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const isObject = (v) => !!v && typeof v === "object" && !Array.isArray(v);
// Ten-thousandths, halves up, as Math.round rounds them (gamma/picture.py
// _round4 does the same).
const round4 = (v) => Math.round(v * 10000) / 10000;

// Degrees in [-180, 180), hundredths; 0 when it is not a finite number. `%`
// is Python's math.fmod, so both sides wrap a value alike; a turn that
// rounds to 180 is -180, and -0 is 0.
function wrapAngle(v) {
  if (!finite(v)) return 0;
  const a = round2(((((v + 180) % 360) + 360) % 360) - 180);
  return a === 180 ? -180 : a + 0;
}

// The crop from a stored `crop`: the whole image when it is not an object,
// each bad key its default. Rounded to ten-thousandths first, then the sides
// clamped to CROP_MIN..1 and the corner pulled in so the crop stays inside
// the image.
function normalizeCrop(raw) {
  if (!isObject(raw)) return { ...WHOLE };
  const w = clamp(finite(raw.w) ? round4(raw.w) : 1, CROP_MIN, 1);
  const h = clamp(finite(raw.h) ? round4(raw.h) : 1, CROP_MIN, 1);
  return {
    x: round4(clamp(finite(raw.x) ? round4(raw.x) : 0, 0, 1 - w)),
    y: round4(clamp(finite(raw.y) ? round4(raw.y) : 0, 0, 1 - h)),
    w,
    h,
  };
}

// Every key of a picture but its `url`, normalized. A corner may be as far
// off the surface as MAX_COORD: what is kept on it is the turned frame.
function normalizeFrame(raw) {
  const opacity = num(raw.opacity, MIN_OPACITY, 1, 1), z = num(raw.z, -MAX_Z, MAX_Z, 0);
  return {
    x: num(raw.x, -MAX_COORD, MAX_COORD, 0),
    y: num(raw.y, -MAX_COORD, MAX_COORD, 0),
    w: num(raw.w, MIN_SIDE, MAX_COORD, DEFAULT_SIDE),
    h: num(raw.h, MIN_SIDE, MAX_COORD, DEFAULT_SIDE),
    angle: wrapAngle(raw.angle),
    crop: normalizeCrop(raw.crop),
    flip_h: raw.flip_h === true,
    flip_v: raw.flip_v === true,
    ...(raw.locked === true ? { locked: true } : {}),
    ...(opacity < 1 ? { opacity } : {}),
    ...(z ? { z } : {}),
  };
}

// A complete picture from a stored `picture`, or null when it is not an
// object or its `url` is no picture upload: each missing or bad key takes its
// default, coordinates and sizes clamped and rounded to hundredths, the turn
// wrapped, the crop kept inside the image, only a real boolean a flip,
// `locked` only when it is a real true (left out otherwise), `opacity`
// clamped to MIN_OPACITY..1 and left out at 1, `z` clamped to ±MAX_Z and
// left out at 0, unknown keys dropped.
export function normalizePicture(raw) {
  if (!isObject(raw) || typeof raw.url !== "string" || !PICTURE_URL.test(raw.url)) return null;
  return { url: raw.url, ...normalizeFrame(raw) };
}

// A picture block: `picture` an object, on a block that is neither ink (an
// `ink_url` key) nor a text box, the order the server's `kind` column tests
// them in. A picture whose value does not normalize is still one (its card
// says the file is missing); it draws nothing on the page.
export const isPicture = (block) => {
  const p = block?.properties;
  return !!p && !isInkGroup(block) && !isTextBox(block) && isObject(p.picture);
};

const same = (a, b) => (isObject(a) ? CROP_KEYS.every((k) => a[k] === b[k]) : a === b);
const FRAME = ["x", "y", "w", "h"];
// A centre that moved by less has not moved: a resize about the centre
// rounds x and y to hundredths, which shifts it by half of one at most.
const MOVED = 0.0075;
const centre = (p) => [p.x + p.w / 2, p.y + p.h / 2];
// The keys normalizePicture leaves out at their default. The merge spells
// them out, so a key taken back to its default is a change too.
const OMITTED = { locked: false, opacity: 1, z: 0 };

// The picture a writer's change `base` → `mine` makes of the one `stored`
// now (gamma/picture.py merge_picture, where the server applies it to a set
// whose base_props names the picture): the stored picture with the keys the
// writer changed taken from `mine`, normalized, so a default spelled out is
// no change (the crop compared by value; a key left out at its default,
// `locked`, `opacity` or `z`, compares as that default) and where both
// changed a key the writer's value wins. The frame merges as its size, `w`
// and `h` key by key, and its centre, as one, since a resize or a turn about
// the centre rewrites `x` and `y`: when the stored frame is the base's the
// writer's is taken whole; else a centre the writer did not move is the
// stored one, where both moved it and neither cropped (two moves) it is the
// writer's, and otherwise the writer's shift of it goes onto the stored
// centre, so the part a crop keeps stays put on a picture moved meanwhile,
// and a picture cropped meanwhile moves as far as the writer moved it.
// `mine` as sent when it is no picture (null removes it), normalized when
// `stored` or `base` is none. tests/shared/picturemerge.json pins both
// halves.
export function mergePicture(stored, mine, base) {
  const sent = normalizePicture(mine);
  if (!sent) return mine;
  const [ours, now, was] = [sent, normalizePicture(stored), normalizePicture(base)].map((p) => p && { ...OMITTED, ...p });
  if (!now || !was) return sent;
  const out = { ...now };
  for (const k of Object.keys(ours)) if (!same(ours[k], was[k])) out[k] = ours[k];
  if (FRAME.every((k) => now[k] === was[k])) return normalizePicture(out);
  const [mx, my] = centre(ours), [bx, by] = centre(was), [sx, sy] = centre(now);
  const moved = (dx, dy) => Math.abs(dx) > MOVED || Math.abs(dy) > MOVED;
  let cx, cy;
  if (!moved(mx - bx, my - by)) [cx, cy] = [sx, sy]; // a resize, a turn, a flip
  else if (moved(sx - bx, sy - by) && same(ours.crop, was.crop) && same(now.crop, was.crop)) [cx, cy] = [mx, my]; // two moves
  else [cx, cy] = [sx + (mx - bx), sy + (my - by)]; // a crop's shift, or a move of a picture resized or cropped meanwhile
  return normalizePicture({ ...out, x: cx - out.w / 2, y: cy - out.h / 2 });
}

// Map surface → its picture blocks in the order they stack (stackOrder), the
// tree's own objects (so an unchanged picture keeps its identity), as
// textBoxesBySurface keys text boxes: a sheet's id for a picture under a
// sheet (the nearest one), else the picture's pdf_page. Only pictures that
// draw are listed: one whose value does not normalize, or with neither a
// sheet nor a page, is on no surface. A surface without pictures has no
// entry.
export function picturesBySurface(tree) {
  const out = new Map();
  const walk = (list, sheet) => {
    for (const b of list || []) {
      if (isSheet(b)) { walk(b.children, b.id); continue; }
      const page = b.properties?.pdf_page;
      const surface = sheet ?? (Number.isInteger(page) && page > 0 ? page : null);
      if (surface !== null && isPicture(b) && normalizePicture(b.properties.picture)) {
        if (!out.has(surface)) out.set(surface, []);
        out.get(surface).push(b);
      }
      walk(b.children, sheet);
    }
  };
  walk(tree, null);
  for (const [surface, list] of out) out.set(surface, stackOrder(list));
  return out;
}

// The picture's turn as a function of a point (dx, dy) from the frame's
// centre, y down, so clockwise as seen: (dx cos a - dy sin a, dx sin a +
// dy cos a). The radians as Python's math.radians makes them.
function turning(pic) {
  const a = pic.angle * (Math.PI / 180);
  const cos = Math.cos(a), sin = Math.sin(a);
  return (dx, dy) => [dx * cos - dy * sin, dx * sin + dy * cos];
}

// The corners of the (normalized) picture's frame turned about its centre,
// in points of its surface: [x, y] of the unturned frame's top-left,
// top-right, bottom-right, bottom-left.
export function turnedCorners(pic) {
  const cx = pic.x + pic.w / 2, cy = pic.y + pic.h / 2;
  const hw = pic.w / 2, hh = pic.h / 2, turn = turning(pic);
  return [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]].map(([dx, dy]) => {
    const [tx, ty] = turn(dx, dy);
    return [cx + tx, cy + ty];
  });
}

// [x0, y0, x1, y1], the bounding box of the turned frame.
export function turnedBox(pic) {
  const corners = turnedCorners(pic);
  const xs = corners.map(([x]) => x), ys = corners.map(([, y]) => y);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

// --- the geometry the handles and the menu use ------------------------------
// Every result is normalized. What the PDFs draw of a picture is cut at the
// page's edge, so what is kept on the surface is the turned frame, not the
// frame before the turn (gamma/picture.py keep_on_surface).

// The frame of `pic` at size `w` × `h` about the same centre.
const resized = (pic, w, h) => ({ ...pic, w, h, x: pic.x + (pic.w - w) / 2, y: pic.y + (pic.h - h) / 2 });

// `pic` moved so that its turned frame lies on a surface of `width` ×
// `height` points, at its left or top edge when it is wider or taller; its
// size left alone.
function within(pic, { width, height }) {
  const [x0, y0, x1, y1] = turnedBox(pic);
  const dx = clamp(x0, 0, Math.max(0, width - (x1 - x0))) - x0;
  const dy = clamp(y0, 0, Math.max(0, height - (y1 - y0))) - y0;
  return { ...pic, x: pic.x + dx, y: pic.y + dy };
}
const inside = (pic, surface) => normalizePicture(within(pic, surface));

// The (normalized) picture kept on a surface of `width` × `height` points
// (gamma/picture.py keep_on_surface): scaled down about its centre, its
// aspect kept, when its turned frame is wider or taller than the surface,
// then moved so that the turned frame lies on it; normalized.
export function keepOnSurface(pic, surface) {
  const [x0, y0, x1, y1] = turnedBox(pic);
  const k = Math.min(1, surface.width / (x1 - x0), surface.height / (y1 - y0));
  return inside(resized(pic, pic.w * k, pic.h * k), surface);
}

// A new picture's frame on a surface of `width` × `height` points, for an
// image of `natural` {w, h} pixels: its natural aspect, fitted inside half the
// surface's width and height but never larger than its pixels as points,
// centred on `at` ({x, y} points) or on the surface's centre, kept on the
// surface. Every key but `url`, which the caller adds: `{ url, ...frame }`.
export function placePicture({ width, height }, natural, at = null) {
  const nw = natural?.w > 0 ? natural.w : DEFAULT_SIDE, nh = natural?.h > 0 ? natural.h : DEFAULT_SIDE;
  const k = Math.min(1, width / 2 / nw, height / 2 / nh);
  const w = nw * k, h = nh * k;
  const cx = at ? at.x : width / 2, cy = at ? at.y : height / 2;
  return normalizeFrame(within({ x: cx - w / 2, y: cy - h / 2, w, h, angle: 0 }, { width, height }));
}

// `pic` moved by `dx`, `dy` points, its turned frame kept on the surface.
export function movePicture(pic, dx, dy, surface) {
  return inside({ ...pic, x: pic.x + dx, y: pic.y + dy }, surface);
}

// `pic` scaled by `k` about its centre, its aspect as it is (a stretched
// picture stays stretched): no side under MIN_SIDE, its turned frame no
// wider or taller than the surface, then kept on it.
export function scalePicture(pic, k, surface) {
  const [x0, y0, x1, y1] = turnedBox(pic);
  const least = MIN_SIDE / Math.min(pic.w, pic.h);
  const most = Math.min(surface.width / (x1 - x0), surface.height / (y1 - y0));
  const f = Math.max(least, Math.min(most, k));
  return inside(resized(pic, pic.w * f, pic.h * f), surface);
}

// `pic` at `w` × `h` points about its centre (the Alt resize, which changes
// the aspect): each side from MIN_SIDE up to the most its turned frame can
// take of the surface with the other side at MIN_SIDE (upright, the
// surface's width and height), then kept on it (keepOnSurface), which
// scales a turned one down when both sides together are too much.
export function stretchPicture(pic, w, h, surface) {
  const a = pic.angle * (Math.PI / 180), c = Math.abs(Math.cos(a)), s = Math.abs(Math.sin(a));
  const { width, height } = surface;
  // the most a side can be with `free` points of the surface's side for it,
  // `per` of its length lying along that side
  const most = (free, per) => (per > 1e-9 ? free / per : Infinity);
  const mostW = Math.min(most(width - MIN_SIDE * s, c), most(height - MIN_SIDE * c, s));
  const mostH = Math.min(most(width - MIN_SIDE * c, s), most(height - MIN_SIDE * s, c));
  return keepOnSurface(resized(pic, clamp(w, MIN_SIDE, Math.max(MIN_SIDE, mostW)),
    clamp(h, MIN_SIDE, Math.max(MIN_SIDE, mostH))), surface);
}

// Degrees from a right angle within which a turn snaps to it: the magnet
// Notability's handle has.
export const MAGNET = 3;

// `pic` turned to `angle` degrees: with Shift to the nearest 15°, else to a
// multiple of 90 within MAGNET degrees of it. With a `surface`, kept on it
// (keepOnSurface): moved off its edges, and scaled down when the turned
// frame is wider or taller than it.
export function turnPicture(pic, angle, { shift = false, surface = null } = {}) {
  let a = angle;
  if (shift) a = Math.round(angle / 15) * 15;
  else {
    const square = Math.round(angle / 90) * 90;
    if (Math.abs(angle - square) <= MAGNET) a = square;
  }
  const turned = normalizePicture({ ...pic, angle: a });
  return surface ? keepOnSurface(turned, surface) : turned;
}

// `pic` mirrored about its frame's vertical axis ("h") or horizontal one ("v").
export function flipPicture(pic, axis) {
  const key = axis === "v" ? "flip_v" : "flip_h";
  return normalizePicture({ ...pic, [key]: !pic[key] });
}

// `pic` locked (`on` true) or unlocked: a locked picture stays where it is.
export const lockPicture = (pic, on) => normalizePicture({ ...pic, locked: on });

// `pic` at `opacity` (MIN_OPACITY to 1): how strongly its image shows over
// what lies under it.
export const fadePicture = (pic, opacity) => normalizePicture({ ...pic, opacity });

// `pic` with the crop `crop`, the part kept staying where it was on the page
// at the same scale: the frame shrinks or grows to the new crop (a stretch
// kept), and its centre moves by the offset of the new crop's centre from
// the old one's, mirrored by the flips and turned by the angle as the
// picture is. With a `surface`, then kept on it (keepOnSurface), as a crop
// taken back at an edge needs.
export function cropTo(pic, crop, surface = null) {
  const next = normalizeCrop(crop), was = pic.crop;
  const sx = pic.w / was.w, sy = pic.h / was.h; // points a whole image's width and height takes
  const w = sx * next.w, h = sy * next.h;
  let dx = sx * (next.x + next.w / 2 - (was.x + was.w / 2));
  let dy = sy * (next.y + next.h / 2 - (was.y + was.h / 2));
  if (pic.flip_h) dx = -dx;
  if (pic.flip_v) dy = -dy;
  const [tx, ty] = turning(pic)(dx, dy);
  const cx = pic.x + pic.w / 2 + tx, cy = pic.y + pic.h / 2 + ty;
  const cropped = normalizePicture({ ...pic, crop: next, w, h, x: cx - w / 2, y: cy - h / 2 });
  return surface ? keepOnSurface(cropped, surface) : cropped;
}

const FIT_MARGIN = 24; // points left free on each side by Fit to page

// `pic` with its turned frame as wide as the surface less FIT_MARGIN each
// side, its aspect kept, shrunk to fit the height less FIT_MARGIN each side,
// centred on the surface.
export function fitPicture(pic, { width, height }) {
  const [x0, y0, x1, y1] = turnedBox(pic);
  const room = (side) => Math.max(MIN_SIDE, side - 2 * FIT_MARGIN);
  const k = Math.min(room(width) / (x1 - x0), room(height) / (y1 - y0));
  const w = pic.w * k, h = pic.h * k;
  return normalizePicture({ ...pic, w, h, x: (width - w) / 2, y: (height - h) / 2 });
}

// `pic` as it came: the whole image, unflipped, upright, at the natural
// aspect of an image of `natural` {w, h} pixels, as wide as it was and about
// the same centre. With a `surface`, then kept on it (keepOnSurface): a wide
// picture turned on its side at an edge comes upright past it.
export function resetPicture(pic, natural, surface = null) {
  const aspect = natural?.w > 0 && natural?.h > 0 ? natural.h / natural.w : pic.h / pic.w;
  const reset = normalizePicture({
    ...resized(pic, pic.w, pic.w * aspect), angle: 0, crop: WHOLE, flip_h: false, flip_v: false,
  });
  return surface ? keepOnSurface(reset, surface) : reset;
}

// --- the lasso ---------------------------------------------------------------
// A lasso selection takes pictures with its strokes (ink/InkLayer.jsx,
// ink/useInkSelection.js), and its edits carry them by the values that
// move, scale, turn and mirror the strokes (ink.js). Each result is kept on
// the surface; its crop, lock, opacity and `z` stay.

const LASSO_GRID = 5; // a lasso weighs a picture at LASSO_GRID × LASSO_GRID points of its frame

// Whether a lasso of `polygon` ([[x, y], …] in the surface's points) takes
// `pic`: never a locked one; else when more than half of its turned frame
// lies inside, as a stroke is taken when more than half its samples do
// (ink.js strokesInLasso). The frame is weighed at the centres of
// LASSO_GRID × LASSO_GRID equal parts of it.
export function lassoTakes(pic, polygon) {
  if (pic.locked || !polygon || polygon.length < 3) return false;
  const cx = pic.x + pic.w / 2, cy = pic.y + pic.h / 2, turn = turning(pic);
  let taken = 0;
  for (let i = 0; i < LASSO_GRID; i++) {
    for (let j = 0; j < LASSO_GRID; j++) {
      const [tx, ty] = turn(((i + 0.5) / LASSO_GRID - 0.5) * pic.w, ((j + 0.5) / LASSO_GRID - 0.5) * pic.h);
      if (pointInPolygon(cx + tx, cy + ty, polygon)) taken++;
    }
  }
  return taken * 2 > LASSO_GRID * LASSO_GRID;
}

// The picture a tap with the lasso at (x, y) takes, among `shown` ([{id,
// pic}] in stacking order, the bottom first): the topmost whose turned
// frame holds the point, unless it is locked. null when none is there, or
// the one on top is locked.
export function pictureAt(shown, x, y) {
  const top = (shown || []).findLast(({ pic }) => pointInPolygon(x, y, turnedCorners(pic)));
  return top && !top.pic.locked ? top.id : null;
}

// `pic` scaled and turned with the lasso's strokes by its handles' value
// {cx, cy, angle (radians), scale} (ink.js transformStrokes): its centre
// moved as their points are (transformPoint), its frame scaled by `scale`,
// no side under MIN_SIDE, and turned by `angle`.
export function transformPicture(pic, { cx, cy, scale = 1, angle = 0 }, surface) {
  const [x, y] = transformPoint(pic.x + pic.w / 2, pic.y + pic.h / 2, { cx, cy, scale, angle });
  const k = Math.max(scale, MIN_SIDE / Math.min(pic.w, pic.h));
  const w = pic.w * k, h = pic.h * k;
  return keepOnSurface({ ...pic, w, h, x: x - w / 2, y: y - h / 2, angle: pic.angle + (angle * 180) / Math.PI }, surface);
}

// `pic` mirrored with the lasso's strokes about the vertical ("h") or
// horizontal ("v") line through (cx, cy) (ink.js flipStrokes): its centre
// mirrored, its image flipped about the same axis and its turn reversed,
// so it shows as its own mirror image.
export function mirrorPicture(pic, { cx, cy, axis }, surface) {
  const v = axis === "v";
  const mx = pic.x + pic.w / 2, my = pic.y + pic.h / 2;
  const [x, y] = v ? [mx, 2 * cy - my] : [2 * cx - mx, my];
  return inside({ ...flipPicture(pic, v ? "v" : "h"), x: x - pic.w / 2, y: y - pic.h / 2, angle: -pic.angle }, surface);
}

// `pic` as a lasso selection's live preview `value` shows it: {dx, dy}, a
// drag of the selection (movePicture), or {cx, cy, angle, scale}, its
// handles' (transformPicture).
export const carriedPicture = (pic, value, surface) => ("dx" in value
  ? movePicture(pic, value.dx, value.dy, surface) : transformPicture(pic, value, surface));

const percent = (v) => `${round4(v)}%`;

// What the screen draws inside a picture's frame (left, top, width, height
// from the picture, turned by `rotate(angle deg)`, overflow hidden): an
// inner box of 100% × 100% mirrored by `flip`, holding the image sized and
// placed by `img` so the crop fills the frame. The frame's aspect against
// the crop's is the stretch, so object-fit is never needed. An SVG of
// `natural` {w, h} pixels is stretched by `svgFill`.
export function pictureLayout(pic, natural = null) {
  const c = pic.crop;
  return {
    flip: `scale(${pic.flip_h ? -1 : 1}, ${pic.flip_v ? -1 : 1})`,
    img: {
      height: percent(100 / c.h), left: percent((-c.x / c.w) * 100), top: percent((-c.y / c.h) * 100),
      ...svgFill(pic, natural, 100 / c.w),
    },
  };
}

// How the image of `pic` fills a box `width` percent of its parent across,
// when it is an SVG of `natural` {w, h} pixels. A browser keeps an SVG's
// own aspect inside the box an <img> gives it, where the PDFs and every
// other image stretch to the box. So the SVG is drawn at its own aspect and
// scaled across to the box: the stretch the frame's aspect against the
// crop's makes. With no natural size, the image fills the box as it is.
export function svgFill(pic, natural, width) {
  const k = natural?.w > 0 && natural?.h > 0 ? (pic.w * pic.crop.h * natural.h) / (pic.h * pic.crop.w * natural.w) : 1;
  if (Math.abs(k - 1) < 0.001) return { width: percent(width) };
  return { width: percent(width / k), transform: `scaleX(${round4(k)})`, transformOrigin: "0 0" };
}

// --- an SVG's size -----------------------------------------------------------
// gamma/picture.py svg_size is the twin, and tests/shared/picture.json pins
// both. The server reads the first SVG_HEAD bytes, this the text of them.
// Each pattern matches a text one way only (a comment ends at its first
// -->, a number's digits are one run), so a file that fails costs one pass
// whatever junk it holds.

export const SVG_HEAD = 65536;
const SVG_NS = "http://www.w3.org/2000/svg";
const SVG_ROOT = /^﻿?(?:[ \t\r\n]|<\?xml\b(?:[^?]|\?(?!>))*\?>|<!--(?:[^-]|-(?!->))*-->|<!DOCTYPE\b[^[>]*(?:\[(?:[^\]]|\](?![ \t\r\n]*>))*\][ \t\r\n]*)?>)*<svg((?:[ \t\r\n]+[^ \t\r\n=/>]+[ \t\r\n]*=[ \t\r\n]*(?:"[^"]*"|'[^']*'))*)[ \t\r\n]*\/?>/;
const SVG_ATTR = /[ \t\r\n]+([^ \t\r\n=/>]+)[ \t\r\n]*=[ \t\r\n]*(?:"([^"]*)"|'([^']*)')/g;
const SVG_NUMBER = /([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)/.source;
const SVG_LENGTH = new RegExp(`^[ \\t\\r\\n]*${SVG_NUMBER}[ \\t\\r\\n]*(?:(px|pt|pc|in|cm|mm|q|r?em)[ \\t\\r\\n]*)?$`, "i");
const SVG_VIEW_BOX = new RegExp(`^[ \\t\\r\\n]*${SVG_NUMBER}${`[ \\t\\r\\n,]+${SVG_NUMBER}`.repeat(3)}[ \\t\\r\\n]*$`);
const SVG_UNITS = { px: 1, pt: 96 / 72, pc: 16, in: 96, cm: 96 / 2.54, mm: 96 / 25.4, q: 96 / 101.6, em: 16, rem: 16 };
const SVG_BIG = 1e9; // a length or an aspect past it counts as it, so no product overflows

// An SVG length in px, at most SVG_BIG (px when it has no unit, an em or a
// rem 16 px even where the file sets another font size); null for one
// missing, relative to anything else (%, ex, ch, vw), not positive or
// malformed.
function svgLength(value) {
  const m = SVG_LENGTH.exec(value ?? "");
  if (!m) return null;
  const px = Number(m[1]) * SVG_UNITS[(m[2] || "px").toLowerCase()];
  return px > 0 ? Math.min(px, SVG_BIG) : null;
}

// A viewBox's height over its width, within 1/SVG_BIG and SVG_BIG; null
// unless it is four numbers with a positive width and height.
function svgAspect(value) {
  const m = SVG_VIEW_BOX.exec(value ?? "");
  if (!m) return null;
  const w = Number(m[3]), h = Number(m[4]);
  if (!(w > 0 && w < Infinity && h > 0 && h < Infinity)) return null;
  return Math.min(SVG_BIG, Math.max(1 / SVG_BIG, h / w));
}

// The size of an SVG as the browser shows it, {w, h} in pixels, from the
// root element of `text` (the file's first SVG_HEAD bytes): `width` and
// `height` when both are lengths it reads (svgLength); one of them with the
// other through the `viewBox`'s aspect, else 150 high or 300 wide; a viewBox
// alone fitted in 300 × 150, the size of an image that names none; neither,
// 300 × 150. Scaled down, its aspect kept, so that the longer side is at
// most MAX_COORD, then each side rounded to a whole pixel, halves up, and at
// least 1. null for a file that is no SVG: the root element, after an XML
// declaration, comments and a DOCTYPE, is an unprefixed `svg` whose `xmlns`
// is the SVG namespace and that names no attribute twice. The browser draws
// none of the others but one with a prefixed root (`<s:svg xmlns:s=…>`).
export function svgSize(text) {
  const m = SVG_ROOT.exec(String(text ?? "").slice(0, SVG_HEAD));
  if (!m) return null;
  const attrs = new Map();
  for (const [, name, double, single] of m[1].matchAll(SVG_ATTR)) {
    if (attrs.has(name)) return null;
    attrs.set(name, double ?? single);
  }
  if (attrs.get("xmlns") !== SVG_NS) return null;
  const w = svgLength(attrs.get("width")), h = svgLength(attrs.get("height")), r = svgAspect(attrs.get("viewBox"));
  let size;
  if (w && h) size = [w, h];
  else if (w) size = [w, r ? w * r : 150];
  else if (h) size = [r ? h / r : 300, h];
  else if (r) size = r > 0.5 ? [150 / r, 150] : [300, 300 * r];
  else size = [300, 150];
  const k = Math.min(1, MAX_COORD / Math.max(...size));
  const [sw, sh] = size.map((side) => Math.max(1, Math.floor(side * k + 0.5)));
  return { w: sw, h: sh };
}

// --- the z-order -------------------------------------------------------------

const zOf = (b) => normalizePicture(b.properties?.picture)?.z ?? 0;

// One surface's picture blocks, `list` in document order, as they stack,
// the lowest first: by `z`, a picture without one at 0, then in document
// order where `z` is equal. So pictures that carry no `z` stack in document
// order. The page draws them so, the last over the rest and the one a press
// where they overlap takes. gamma/picture.py stack_order is the twin.
export function stackOrder(list) {
  const z = new Map(list.map((b) => [b, zOf(b)]));
  return [...list].sort((a, b) => z.get(a) - z.get(b));
}

// The stack picture `id` of `tree` is in (stackOrder), and whether it is
// the stack's top (`front`) or its bottom.
const stackOf = (tree, id) => [...picturesBySurface(tree).values()].find((pics) => pics.some((b) => b.id === id));
const atEnd = (stack, id, front) => (front ? stack.at(-1) : stack[0]).id === id;

// `tree` with picture `id` brought to the front of its surface's pictures
// (`where` "front": its `z` one above the highest of the others') or sent
// to the back ("back": one below the lowest), its block left where it is
// in the notes. The same tree when it is there already, or locked, or when
// the others' `z` is at ±MAX_Z and the clamped one would still tie behind.
export function orderPicture(tree, id, where) {
  const stack = stackOf(tree, id);
  const front = where === "front";
  if (!stack || atEnd(stack, id, front)) return tree;
  const pic = normalizePicture(stack.find((b) => b.id === id).properties.picture);
  if (pic.locked) return tree;
  const others = stack.filter((b) => b.id !== id).map(zOf);
  const z = front ? Math.max(...others) + 1 : Math.min(...others) - 1;
  const out = updateBlockTree(tree, id, (b) => ({ ...b, properties: { ...b.properties, picture: normalizePicture({ ...pic, z }) } }));
  return atEnd(stackOf(out, id), id, front) ? out : tree;
}

// `pic` as a new picture that goes in last on `surface` of `tree`, after
// the surface's pictures in document order: with the top one's `z` when
// that is above 0, so that it ties with it and stacks over every one.
export function onTop(tree, surface, pic) {
  const top = picturesBySurface(tree).get(surface)?.at(-1);
  const z = top ? zOf(top) : 0;
  return z > 0 ? normalizePicture({ ...pic, z }) : pic;
}

// `tree` with a copy of picture block `id`, block `copyId`, right after it,
// so that with the original's `z` it lands just over it: the caption, not
// the notes under it, its `pdf_page`, `t0` and `place(the picture)`. The
// menu's Duplicate and the lasso's make one so. The same tree when `id` is
// no picture that draws.
export function duplicatePicture(tree, id, copyId, place, t0) {
  const src = findBlock(tree, id);
  const pic = normalizePicture(src?.properties?.picture);
  if (!pic) return tree;
  const { pdf_page } = src.properties;
  const properties = { t0, picture: place(pic), ...(pdf_page !== undefined ? { pdf_page } : {}) };
  return insertSibling(tree, id, { id: copyId, children: [], content: src.content || "", properties }, true);
}

// --- crop mode ---------------------------------------------------------------

// A move of `dx`, `dy` points on the surface as fractions of `pic`'s whole
// image ({x, y}): the turn undone, then the flips, then divided by the points
// a whole image's width and height take in the frame (w / crop.w, h /
// crop.h). What a drag in crop mode moves the crop by.
export function imageDelta(pic, dx, dy) {
  const a = pic.angle * (Math.PI / 180);
  const cos = Math.cos(a), sin = Math.sin(a);
  let lx = dx * cos + dy * sin, ly = -dx * sin + dy * cos;
  if (pic.flip_h) lx = -lx;
  if (pic.flip_v) ly = -ly;
  return { x: (lx * pic.crop.w) / pic.w, y: (ly * pic.crop.h) / pic.h };
}

// The crop a drag of `handle` by `dx`, `dy` (fractions of the image, from
// imageDelta) makes of `crop`: "move" slides it, never past the image's
// edges; a corner ("nw", "ne", "se", "sw") or a side ("n", "e", "s", "w")
// moves those edges, at most to the image's edge and never closer than
// CROP_MIN to the edge across. Normalized.
export function dragCrop(crop, handle, dx, dy) {
  const c = normalizeCrop(crop);
  if (handle === "move") return normalizeCrop({ ...c, x: clamp(c.x + dx, 0, 1 - c.w), y: clamp(c.y + dy, 0, 1 - c.h) });
  let left = c.x, top = c.y, right = c.x + c.w, bottom = c.y + c.h;
  if (handle.includes("w")) left = clamp(left + dx, 0, right - CROP_MIN);
  if (handle.includes("e")) right = clamp(right + dx, left + CROP_MIN, 1);
  if (handle.includes("n")) top = clamp(top + dy, 0, bottom - CROP_MIN);
  if (handle.includes("s")) bottom = clamp(bottom + dy, top + CROP_MIN, 1);
  return normalizeCrop({ x: left, y: top, w: right - left, h: bottom - top });
}

// --- the Markdown of a picture ------------------------------------------------

// What CommonMark would read as syntax, as gamma/text_box.py escape_markdown
// finds it: a bare URL (left whole), a backslash, code, emphasis, math, an
// opening bracket (a "]" opens nothing), an HTML tag or a table pipe
// anywhere, a run of =, an entity; and where a line starts, a heading,
// quote, list, rule or setext underline.
const MD_ANYWHERE = /(https?:\/\/[^\s<>()[\]]+)|[\\`*_~$[<|]|(?<==)=|=(?==)|&(?=#?\w+;)/g;
const MD_LINE_START = /^(\s*)(?:([#>+=-])|(\d+)(?=[.)]))/;
const escapeMarkdown = (text) => text.split("\n").map((line) => line
  .replace(MD_ANYWHERE, (m, url) => url || `\\${m}`)
  .replace(MD_LINE_START, (m, space, mark, digits) => space + (mark ? `\\${mark}` : `${digits}\\`))).join("\n");

// A picture's caption on one line: every run of whitespace one space, the
// ends stripped (gamma/picture.py caption_text).
export const captionText = (content) => String(content || "").replace(/\s+/g, " ").trim();

// What a line of a caption holds as it is: an escaped character (as itself),
// a code span (its text), math (as written), a bare URL; the leftmost first.
const HELD = /\\([!-/:-@[-`{-~])|(`+)(.+?)\2(?!`)|(\$\$.+?\$\$|\$(?=[^\s$]).*?[^\s\\$]\$|https?:\/\/[^\s<>()[\]]+)/g;
// Emphasis, strike and highlight marks, the content kept; `_` not within a word.
const MARKS = [
  [/(\*\*|~~|==)(?=\S)(.*?\S)\1/g, "$2"],
  [/(?<![\p{L}\p{N}_])__(?=\S)(.*?\S)__(?![\p{L}\p{N}_])/gu, "$1"],
  [/\*(?=[^\s*])(.*?[^\s*])\*/g, "$1"],
  [/(?<![\p{L}\p{N}_])_(?=\S)(.*?\S)_(?![\p{L}\p{N}_])/gu, "$1"],
];

// A caption's Markdown as the words it shows, as gamma/text_box.py
// plain_text reads it for the server's line (the cases of
// tests/shared/picture.json `markdown` agree): a line's indent, a heading's
// hashes, a quote's marks and a fence go, a bullet is •, a to-do keeps its
// box; within a line an escaped character is itself, a code span its text,
// a link its text, an image its alt text, a [[ref]] or ![[embed]] its
// target, a colour span its content, emphasis, strike and highlight marks
// go, and math stays as written.
function captionPlain(md) {
  const held = [];
  const hold = (text) => String.fromCharCode(0xe000 + held.push(text) - 1);
  const lines = [];
  let fenced = false;
  for (const raw of String(md || "").split("\n")) {
    if (/^\s*```/.test(raw)) { fenced = !fenced; continue; }
    if (fenced) { lines.push(raw.trim()); continue; }
    let line = raw.trim().replace(/[\ue000-\uf8ff]/g, hold)
      .replace(/^(?:>\s?)+/, "").replace(/^#{1,6}(?:\s+|$)/, "")
      .replace(/^[-*+]\s+(?:\[([ xX])\]\s+)?/, (m, box) => (box === undefined ? "• " : box === " " ? "[ ] " : "[x] "))
      .replace(HELD, (m, escaped, ticks, code, kept) => hold(escaped ?? (ticks ? code.trim() : kept)))
      .replace(/!?\[\[([^\]|]*)(?:\|[^\]]*)?\]\]/g, "$1")
      .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/<\/?span\b[^>]*>/gi, "");
    for (let was = null; was !== line;) {
      was = line;
      for (const [re, to] of MARKS) line = line.replace(re, to);
    }
    lines.push(line.replace(/[\ue000-\uf8ff]/g, (c) => held[c.charCodeAt(0) - 0xe000]));
  }
  return lines.join("\n");
}

// The `![caption](url)` line a picture block is as Markdown (gamma/picture.py
// markdown_of): the caption as a reader sees it (captionPlain), on one line,
// without the brackets and pipes that would end its alt text or read as an
// Obsidian size, and escaped again for the alt text; "" when `raw` is no
// picture.
export function pictureMarkdown(content, raw) {
  const pic = normalizePicture(raw);
  if (!pic) return "";
  return `![${escapeMarkdown(captionText(captionPlain(content).replace(/[[\]|]/g, "")))}](${pic.url})`;
}
