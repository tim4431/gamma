// Text in a page's figures for the PDF translated view: a plot's tick labels,
// axis titles and legends stay original, and the overlay's mask stays off the
// marks they sit on. The drawings come from pdf.js's operator list, so a PDF
// the server has not stored has them too. docs/dev/translation.md "Text in
// figures" describes the rule.
//
// Pure but for `figureRegions`, which reads a page. `markFigures` writes on the
// blocks, so it runs where a page is segmented: the first blocks made for a
// page stay, entries are seeded by their identity, and the page's layout reads
// `translate`.
import { CJK, bodySize, isCaption, isHeading, isProse } from "./pageFlow.js";

// The grid, in points, on which boxes that touch make one cluster.
const CELL = 6;
// A box over this share of the page is its background.
const BACKGROUND = 0.6;
// A rule across the page: this share of the page long, this many points thin.
const RULE_LENGTH = 0.3;
const RULE_THICKNESS = 2.5;
// A region is a picture (images and gradients over this share of it) or this
// many boxes, of at least this size, a picture down to a strip: a gradient bar
// under a panel's labels is a handful of boxes 24 pt tall. One that reaches this
// near both sides of the page is a band, or a cluster of the whole page.
const PICTURE = 0.3;
const MIN_BOXES = 12;
// A plot drawn in a few strokes (a frame, a curve, each axis's ticks as one
// path) is a region of this many boxes or more, once its text is read: tick
// numbers line its bottom and its left or right side, this many on each,
// outside it and within LABEL_MARGIN of it.
const MIN_PLOT_BOXES = 3;
const TICKS = 3;
const TICK = /^[−–-]?\d+(?:[.,]\d+)?(?:\s|$)/;
const MIN_WIDTH = 30;
const MIN_HEIGHT = 25;
const MIN_PICTURE_HEIGHT = 20;
const PAGE_EDGE = 0.02;
// Running text inside a cluster makes it a box, a table or a background.
const PROSE_LINES = 3;
const PROSE_WORDS = 12;
// A table's caption stands above it, a figure's below: a cluster starting
// within this many points under a Table caption is that table.
const TABLE_REACH = 40;
const TABLE_CAPTION = /^(?:(?:supplementa(?:ry|l)(?:\s+information)?|extended\s+data)\s+)?tab/i;
// Panels of one figure: regions this many points apart or nearer.
const PANEL_GAP = 24;
// A block stays original with this share of its lines within this many points
// of a region, unless it is a caption, a larger heading or prose of so many
// words.
const LABEL_MARGIN = 18;
const LABEL_COVER = 0.8;
const LABEL_WORDS = 8;
const HEADING_SIZE = 1.12;

const times = (m, n) => [
  m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1],
  m[0] * n[2] + m[2] * n[3], m[1] * n[2] + m[3] * n[3],
  m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5],
];

function rectThrough(m, x0, y0, x1, y1) {
  const xs = [m[0] * x0 + m[2] * y0, m[0] * x1 + m[2] * y0, m[0] * x0 + m[2] * y1, m[0] * x1 + m[2] * y1];
  const ys = [m[1] * x0 + m[3] * y0, m[1] * x1 + m[3] * y0, m[1] * x0 + m[3] * y1, m[1] * x1 + m[3] * y1];
  return [Math.min(...xs) + m[4], Math.min(...ys) + m[5], Math.max(...xs) + m[4], Math.max(...ys) + m[5]];
}

const cut = (a, b) => (b ? [Math.max(a[0], b[0]), Math.max(a[1], b[1]), Math.min(a[2], b[2]), Math.min(a[3], b[3])] : a);
const round = (v) => Math.round(v * 100) / 100;

// A path's box from its ops, curves' control points included.
function pathBox(ops, args, OPS) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity, a = 0;
  const pt = (x, y) => { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); };
  for (const op of ops) {
    if (op === OPS.rectangle) { pt(args[a], args[a + 1]); pt(args[a] + args[a + 2], args[a + 1] + args[a + 3]); a += 4; }
    else if (op === OPS.moveTo || op === OPS.lineTo) { pt(args[a], args[a + 1]); a += 2; }
    else if (op === OPS.curveTo) { pt(args[a], args[a + 1]); pt(args[a + 2], args[a + 3]); pt(args[a + 4], args[a + 5]); a += 6; }
    else if (op === OPS.curveTo2 || op === OPS.curveTo3) { pt(args[a], args[a + 1]); pt(args[a + 2], args[a + 3]); a += 4; }
  }
  return x0 === Infinity ? null : [x0, y0, x1, y1];
}

// What an operator list paints, as `{k: "fill"|"stroke"|"image"|"shade", b:
// [x1, y1, x2, y2]}`, each box once, under `transform`. A scale-1 viewport's
// holds /Rotate, the crop box and /UserUnit, so the boxes come out in the
// blocks' frame. The CTM and the clip's box are kept through save, restore and
// form XObjects. `OPS` is pdf.js's table of operators. The page's annotations
// come last, each in a frame of its own, and a reader's highlight or ink is no
// drawing of the page.
export function paintedBoxes(list, transform, OPS) {
  const boxes = new Map();
  const stack = [];
  let ctm = transform, clip = null, path = null, clipping = false;
  const add = (k, box) => {
    const c = cut(box, clip);
    if (c[2] < c[0] || c[3] < c[1]) return;
    const b = c.map(round);
    boxes.set(`${k}:${b}`, { k, b });
  };
  const image = (m) => add("image", rectThrough(m, 0, 0, 1, 1));
  const pop = () => { const s = stack.pop(); if (s) ({ ctm, clip } = s); };
  const paint = (k) => {
    if (path) {
      if (clipping) clip = cut(path, clip);
      add(k, path);
    }
    path = null;
    clipping = false;
  };
  const { fnArray: fns, argsArray: args } = list;
  for (let i = 0; i < fns.length; i++) {
    const fn = fns[i], a = args[i];
    if (fn === OPS.beginAnnotation) break;
    switch (fn) {
      case OPS.save: stack.push({ ctm, clip }); break;
      case OPS.restore: pop(); break;
      case OPS.transform: ctm = times(ctm, a); break;
      case OPS.constructPath: {
        const box = pathBox(a[0], a[1], OPS);
        path = box && rectThrough(ctm, ...box);
        clipping = false;
        break;
      }
      case OPS.clip: case OPS.eoClip: clipping = true; break;
      case OPS.endPath:
        if (path && clipping) clip = cut(path, clip);
        path = null;
        clipping = false;
        break;
      case OPS.stroke: case OPS.closeStroke: paint("stroke"); break;
      case OPS.fill: case OPS.eoFill: case OPS.fillStroke: case OPS.eoFillStroke:
      case OPS.closeFillStroke: case OPS.closeEOFillStroke:
        paint("fill");
        break;
      case OPS.paintFormXObjectBegin:
        stack.push({ ctm, clip });
        if (a[0]) ctm = times(ctm, a[0]);
        if (a[1]) clip = cut(rectThrough(ctm, a[1][0], a[1][1], a[1][2], a[1][3]), clip);
        break;
      case OPS.paintFormXObjectEnd: pop(); break;
      case OPS.paintImageXObject: case OPS.paintInlineImageXObject:
      case OPS.paintImageMaskXObject: case OPS.paintSolidColorImageMask:
        image(ctm);
        break;
      case OPS.paintImageMaskXObjectGroup:
        for (const im of a[0] || []) image(times(ctm, im.transform || [1, 0, 0, 1, 0, 0]));
        break;
      case OPS.paintImageMaskXObjectRepeat: {
        const [, sx, kx, ky, sy, at] = a;
        for (let p = 0; p + 1 < at.length; p += 2) image(times(ctm, [sx, kx, ky, sy, at[p], at[p + 1]]));
        break;
      }
      case OPS.paintImageXObjectRepeat: {
        const [, sx, sy, at] = a;
        for (let p = 0; p + 1 < at.length; p += 2) image(times(ctm, [sx, 0, 0, sy, at[p], at[p + 1]]));
        break;
      }
      case OPS.shadingFill:
        if (clip) add("shade", clip);
        break;
      default:
    }
  }
  return [...boxes.values()];
}

// The clusters of a page's boxes: those that touch through a grid's cells,
// `{x1, y1, x2, y2, boxes, imageArea}`. A page's background and the rules
// across it tie everything to everything, and go first.
function clusters(boxes, width, height) {
  const cols = Math.ceil(width / CELL) + 2;
  const owner = new Int32Array(cols * (Math.ceil(height / CELL) + 2)).fill(-1);
  const kept = [];
  for (const { k, b } of boxes) {
    const x1 = Math.max(0, b[0]), y1 = Math.max(0, b[1]), x2 = Math.min(width, b[2]), y2 = Math.min(height, b[3]);
    const w = x2 - x1, h = y2 - y1;
    if (w < 0 || h < 0 || w * h > BACKGROUND * width * height) continue;
    if ((w >= RULE_LENGTH * width && h <= RULE_THICKNESS) || (h >= RULE_LENGTH * height && w <= RULE_THICKNESS)) continue;
    kept.push({ image: k === "image" || k === "shade", x1, y1, x2, y2 });
  }
  const parent = kept.map((_, i) => i);
  const find = (i) => {
    while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; }
    return i;
  };
  kept.forEach((b, i) => {
    for (let r = Math.floor(b.y1 / CELL); r <= Math.floor(b.y2 / CELL); r++) {
      for (let c = Math.floor(b.x1 / CELL); c <= Math.floor(b.x2 / CELL); c++) {
        const k = r * cols + c;
        if (owner[k] < 0) owner[k] = i;
        else parent[find(i)] = find(owner[k]);
      }
    }
  });
  const groups = new Map();
  kept.forEach((b, i) => {
    const root = find(i);
    let g = groups.get(root);
    if (!g) groups.set(root, (g = { x1: b.x1, y1: b.y1, x2: b.x2, y2: b.y2, boxes: 0, imageArea: 0 }));
    g.x1 = Math.min(g.x1, b.x1); g.y1 = Math.min(g.y1, b.y1);
    g.x2 = Math.max(g.x2, b.x2); g.y2 = Math.max(g.y2, b.y2);
    g.boxes += 1;
    if (b.image) g.imageArea += (b.x2 - b.x1) * (b.y2 - b.y1);
  });
  return [...groups.values()];
}

// The clusters that can be a figure, whatever the page's text says:
// `{x1, y1, x2, y2}`, in the frame of `boxes`. One of fewer than MIN_BOXES
// boxes and no picture is `sparse`: a figure only if ticks frame it.
export function pageRegions(boxes, width, height) {
  const out = [];
  for (const c of clusters(boxes, width, height)) {
    const w = c.x2 - c.x1, h = c.y2 - c.y1;
    const picture = c.imageArea >= PICTURE * w * h;
    if (w < MIN_WIDTH || h < (picture ? MIN_PICTURE_HEIGHT : MIN_HEIGHT)) continue;
    if (c.x1 <= width * PAGE_EDGE && c.x2 >= width * (1 - PAGE_EDGE)) continue;
    const { x1, y1, x2, y2 } = c;
    if (c.boxes >= MIN_BOXES || picture) out.push({ x1, y1, x2, y2 });
    else if (c.boxes >= MIN_PLOT_BOXES) out.push({ x1, y1, x2, y2, sparse: true });
  }
  return out;
}

// How much of a block's lines lies within `margin` of a region.
function coverage(p, r, margin) {
  let inside = 0, all = 0;
  for (const l of p.lines) {
    all += Math.max(0, l.x2 - l.x1) * Math.max(0.1, l.y2 - l.y1);
    const w = Math.min(l.x2, r.x2 + margin) - Math.max(l.x1, r.x1 - margin);
    const h = Math.min(l.y2, r.y2 + margin) - Math.max(l.y1, r.y1 - margin);
    if (w > 0 && h > 0) inside += w * h;
  }
  return all ? inside / all : 0;
}

// A word's worth of text: what spaces part, and each CJK character.
function wordCount(text) {
  const t = text.trim();
  return t.split(/\s+/).filter(Boolean).length + (t.match(new RegExp(CJK.source, "g")) || []).length;
}

const overlaps = (a, b) => Math.min(a.x2, b.x2) > Math.max(a.x1, b.x1) && Math.min(a.y2, b.y2) > Math.max(a.y1, b.y1);

// Whether tick numbers line a sparse region's bottom and one of its sides.
function framedByTicks(paras, r) {
  const ticks = paras.filter((p) => !p.translate && TICK.test(p.text.trim()));
  const rows = (x1, x2) => ticks.filter((p) => p.y1 >= r.y1 - LABEL_MARGIN && p.y2 <= r.y2 + LABEL_MARGIN
    && p.x1 >= x1 && p.x2 <= x2).length >= TICKS;
  const below = ticks.filter((p) => p.y1 >= r.y2 - 2 && p.y1 - r.y2 <= LABEL_MARGIN
    && p.x1 >= r.x1 - LABEL_MARGIN && p.x2 <= r.x2 + LABEL_MARGIN).length >= TICKS;
  return below && (rows(r.x1 - LABEL_MARGIN, r.x1 + 2) || rows(r.x2 - 2, r.x2 + LABEL_MARGIN));
}

// The regions that are figures once the page's text is read: a sparse one
// framed by ticks, none holding running text or standing under a Table
// caption, and panels near each other are one, as long as that takes in no
// running text.
function figuresAmong(paras, regions) {
  const prose = paras.filter((p) => p.translate && !isCaption(p) && p.nLines >= PROSE_LINES
    && isProse(p.text) && wordCount(p.text) >= PROSE_WORDS);
  const holdsProse = (r) => prose.some((p) => coverage(p, r, 0) >= LABEL_COVER);
  const tableCaptions = paras.filter((p) => isCaption(p) && TABLE_CAPTION.test(p.text.trim()));
  const underTable = (r) => tableCaptions.some((p) => r.y1 - p.y2 < TABLE_REACH && r.y1 - p.y1 > -4
    && Math.min(r.x2, p.x2) > Math.max(r.x1, p.x1));
  const figures = regions.filter((r) => (!r.sparse || framedByTicks(paras, r)) && !holdsProse(r) && !underTable(r))
    .map(({ x1, y1, x2, y2 }) => ({ x1, y1, x2, y2 }));
  for (let merged = true; merged;) {
    merged = false;
    for (let i = 0; i < figures.length && !merged; i++) {
      for (let j = i + 1; j < figures.length && !merged; j++) {
        const a = figures[i], b = figures[j];
        if (Math.max(a.x1, b.x1) - Math.min(a.x2, b.x2) > PANEL_GAP || Math.max(a.y1, b.y1) - Math.min(a.y2, b.y2) > PANEL_GAP) continue;
        const both = { x1: Math.min(a.x1, b.x1), y1: Math.min(a.y1, b.y1), x2: Math.max(a.x2, b.x2), y2: Math.max(a.y2, b.y2) };
        if (holdsProse(both)) continue;
        Object.assign(a, both);
        figures.splice(j, 1);
        merged = true;
      }
    }
  }
  return figures;
}

// Marks a page's blocks by the regions of `pageRegions`, in place. A block
// that lies in a figure and reads as a label takes `figure` and stays original
// (`translate` false). A block with no text that lies in one (a tick number,
// "NA = 0.55") takes `inFigure`. Another translated block whose box overlaps a
// figure takes `overFigure`: it is written over the drawing, not over paper.
// Gives the figures, which pdf/pageShape.js keeps text out of.
export function markFigures(paras, regions) {
  if (!regions.length) return [];
  const figures = figuresAmong(paras, regions);
  const body = bodySize(paras);
  const label = (p) => !isCaption(p) && !(isHeading(p, body) && p.size >= body * HEADING_SIZE)
    && !(isProse(p.text) && wordCount(p.text) >= LABEL_WORDS);
  for (const p of paras) {
    const inside = (!p.translate || label(p)) && figures.some((r) => coverage(p, r, LABEL_MARGIN) >= LABEL_COVER);
    if (inside && p.translate) {
      p.figure = true;
      p.translate = false;
    } else if (inside) p.inFigure = true;
    else if (p.translate && figures.some((r) => overlaps(p, r))) p.overFigure = true;
  }
  return figures;
}

// The regions of a pdf.js page, or none when its operator list fails: the page
// then plans as if it had no drawing. The list is the one a page's real font
// names come from (pdf/pdfFonts.js), so that is not built twice, and pdf.js
// is the viewer's engine, loaded already, or the bench's.
export async function figureRegions(page) {
  try {
    const vp = page.getViewport({ scale: 1 });
    const list = await page.getOperatorList();
    if (!list?.fnArray?.length) return [];
    const { OPS } = await import("pdfjs-dist/legacy/build/pdf.mjs");
    return pageRegions(paintedBoxes(list, vp.transform, OPS), vp.width, vp.height);
  } catch {
    return [];
  }
}
