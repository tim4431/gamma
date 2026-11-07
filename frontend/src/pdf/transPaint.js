// The ground under a block's lines and the colour of its ink, read from the
// page's own bitmap for the translated view's overlay: raw page colours, which
// the page's treatment acts on as it does on the canvas (app.css). Pure, so the
// tests run it in Node: an RGBA buffer of the page in, colours out. Read once a
// page, kept with the fit (transFit.js paintsOf), so a zoom, a remount or a
// theme reads nothing. docs/dev/translation.md "Ground and ink" describes it.
// gamma/pdf_translated.py reads a file's pages by the same rules and numbers.

// Buffer pixels per pt of the page. Enough for a body stem to hold a pixel of
// its full colour when read without smoothing.
export const SAMPLE_SCALE = 2;
// At most, in the buffer: a bigger page is read coarser. Past some area a canvas
// reads back as zeros, or throws.
const SAMPLE_PIXELS = 4_000_000;
// Around each line, in pt, so a heavy line's own ink does not outvote its ground.
const RING = 1.5;
const MOST_PIXELS = 6000; // read of a block, about
// Colours within a bucket of this many low bits are one: antialiasing, a scan's
// or a JPEG's noise.
const BUCKET_BITS = 3;
const BUCKETS = 1 << (3 * (8 - BUCKET_BITS));
// A ground lies around its lines: this share of the ring, where no ink reaches.
// A gradient's many colours leave the text's own the commonest, and that is no
// ground: the block has none, and a mask of paper reads best there.
const IN_RING = 0.05;
// A colour this share of the lines themselves is their ground, if it reaches the
// ring at all, as a band does past the ends of its line: on a band no taller
// than its line, the ring above and below adds paper, and white text on the band
// with it outvotes the band. A line's own ink never fills half of it.
const LINE_SHARE = 0.5;
const OWN_IN_RING = 0.01;
// A ground this near white in every channel is plain paper: its mask is the solid paper.
const PAPER = 250;
// Ink is what lies farthest from the ground: antialiased edges lie between the
// two. Its colour is the mode among the pixels this far out, of how far the
// farthest (bar a stray few) lie, and takes this many pixels to say anything.
const FAR = 0.7;
const FARTHEST = 0.98;
const FEWEST = 6;
// The block is in that colour when this share of those pixels lie within this
// of it: a black paragraph thick with blue citations has a blue mode, no more.
const MOST_INK = 0.5;
const SAME_INK = 48;
// An ink is its own colour with this much chroma (channel spread), or on a ground
// this dark: white on a band. A grey on a light ground is the page's usual ink
// and takes the treatment's: thin black text at a low zoom holds no darker pixel.
const CHROMA = 40;
const DARK_GROUND = 128;
// An ink stands this far from its ground, as a contrast ratio, or it is no ink: a
// file's yellow highlight has more area than the text on it and ties it for
// farthest from white. The lowest genuine ink over 142 papers came to 2.55.
const MIN_CONTRAST = 2;

const hist = new Uint32Array(BUCKETS);
const bucketOf = (px, at) => ((px[at] >> BUCKET_BITS) << (2 * (8 - BUCKET_BITS)))
  | ((px[at + 1] >> BUCKET_BITS) << (8 - BUCKET_BITS)) | (px[at + 2] >> BUCKET_BITS);
const css = ([r, g, b]) => `rgb(${r} ${g} ${b})`;

// The pixels per pt to read a page of `width` by `height` pt at, `bitmap` being
// its bitmap's: no more than the bitmap holds, and no more than SAMPLE_PIXELS.
export const sampleScale = (width, height, bitmap = SAMPLE_SCALE) => Math.min(SAMPLE_SCALE, bitmap, Math.sqrt(SAMPLE_PIXELS / (width * height)));

// The buffer offsets of what a block's lines cover, `lines`, and of the `ring`
// around them, at `k` pixels per pt: every `step`th pixel each way so that a long
// paragraph costs what a short one does, it has no more colours. A row of a ring
// that lies in the next line's height is that line's, since ink reaches it.
function pixelsOf(p, width, height, k) {
  const boxes = (p.lines || []).map((l) => ({
    x1: Math.max(0, Math.floor((l.x1 - RING) * k)), x2: Math.min(width, Math.ceil((l.x2 + RING) * k)),
    y1: Math.max(0, Math.floor((l.y1 - RING) * k)), y2: Math.min(height, Math.ceil((l.y2 + RING) * k)),
    left: Math.floor(l.x1 * k), right: Math.ceil(l.x2 * k), top: Math.floor(l.y1 * k), bottom: Math.ceil(l.y2 * k),
  }));
  const area = boxes.reduce((n, r) => n + Math.max(0, r.x2 - r.x1) * Math.max(0, r.y2 - r.y1), 0);
  const step = Math.max(1, Math.round(Math.sqrt(area / MOST_PIXELS)));
  const lines = [], ring = [];
  const inHeight = (b, y) => !!b && y >= b.top && y < b.bottom;
  boxes.forEach((r, j) => {
    for (let y = r.y1; y < r.y2; y += step) {
      const own = inHeight(r, y), near = !own && (inHeight(boxes[j - 1], y) || inHeight(boxes[j + 1], y));
      for (let x = r.x1; x < r.x2; x += step) {
        (near || (own && x >= r.left && x < r.right) ? lines : ring).push((y * width + x) * 4);
      }
    }
  });
  return { lines, ring };
}

// The commonest colour among `pixels`: the exact one that leads its commonest
// bucket, since a mask's edge shows a colour one level off. null with none.
function modal(px, pixels) {
  hist.fill(0);
  let top = -1, count = 0;
  for (const at of pixels) {
    const k = bucketOf(px, at);
    hist[k] += 1;
    if (hist[k] > count) { count = hist[k]; top = k; }
  }
  if (top < 0) return null;
  const exact = new Map();
  let best = 0, most = 0;
  for (const at of pixels) {
    if (bucketOf(px, at) !== top) continue;
    const colour = (px[at] << 16) | (px[at + 1] << 8) | px[at + 2];
    const n = (exact.get(colour) || 0) + 1;
    exact.set(colour, n);
    if (n > most) { most = n; best = colour; }
  }
  return [best >> 16, (best >> 8) & 255, best & 255];
}

const distance = (px, at, c) => Math.max(Math.abs(px[at] - c[0]), Math.abs(px[at + 1] - c[1]), Math.abs(px[at + 2] - c[2]));

// The share of `pixels` in `colour`'s bucket.
function shareOf(px, pixels, colour) {
  const top = bucketOf(colour, 0);
  let n = 0;
  for (const at of pixels) if (bucketOf(px, at) === top) n += 1;
  return n / pixels.length;
}

// The ground under a block's `lines`, with the `ring` around them: their own
// commonest colour when it fills them and reaches the ring, else the commonest
// of both, or null when that is not in the ring. With no ring, a line off the
// page's edge, nothing says otherwise.
function groundOf(px, lines, ring) {
  const around = (colour, share) => !ring.length || shareOf(px, ring, colour) >= share;
  const own = modal(px, lines);
  if (own && shareOf(px, lines, own) >= LINE_SHARE && around(own, OWN_IN_RING)) return own;
  const ground = modal(px, lines.concat(ring));
  return ground && around(ground, IN_RING) ? ground : null;
}

// The ink among `pixels`, apart from `ground`.
function inkOf(px, pixels, ground) {
  const far = new Uint32Array(256);
  for (const at of pixels) far[distance(px, at, ground)] += 1;
  let farthest = 255;
  for (let stray = pixels.length * (1 - FARTHEST); farthest > 0 && stray > 0; farthest--) stray -= far[farthest];
  if (!farthest) return null;
  const inked = pixels.filter((at) => distance(px, at, ground) >= farthest * FAR);
  if (inked.length < FEWEST) return null;
  const ink = modal(px, inked);
  const near = inked.filter((at) => distance(px, at, ink) <= SAME_INK).length;
  return near >= inked.length * MOST_INK ? ink : null;
}

const chroma = (c) => Math.max(...c) - Math.min(...c);
const isDark = (c) => (c[0] + c[1] + c[2]) / 3 < DARK_GROUND;
const channel = (v) => (v / 255 <= 0.04045 ? v / 255 / 12.92 : ((v / 255 + 0.055) / 1.055) ** 2.4);
const luminance = ([r, g, b]) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
const contrast = (a, b) => (Math.max(luminance(a), luminance(b)) + 0.05) / (Math.min(luminance(a), luminance(b)) + 0.05);

// What the buffer `px` (RGBA, `width` by `height`, at `k` pixels per pt) says of
// a block: {ground, ink}, CSS colours, `ground` null on plain paper and `ink` null
// for the usual dark one; null for a block with neither, which is most, or with
// no ground to tell, or a dark one with no ink of its own: the usual ink could
// not be read on it, on paper it can.
export function blockPaint(px, width, height, p, k = SAMPLE_SCALE) {
  const { lines, ring } = pixelsOf(p, width, height, k);
  const ground = groundOf(px, lines, ring);
  if (!ground) return null;
  const ink = inkOf(px, lines.concat(ring), ground);
  const own = ink && (chroma(ink) >= CHROMA || isDark(ground)) && contrast(ink, ground) >= MIN_CONTRAST;
  if (isDark(ground) && !own) return null;
  const paint = { ground: Math.min(...ground) >= PAPER ? null : css(ground), ink: own ? css(ink) : null };
  return paint.ground || paint.ink ? paint : null;
}

// The page's translated blocks' paints, by index: only those that have one.
export function pagePaints(paras, px, width, height, k = SAMPLE_SCALE) {
  const out = new Map();
  paras.forEach((p, i) => {
    const paint = p.translate ? blockPaint(px, width, height, p, k) : null;
    if (paint) out.set(i, paint);
  });
  return out;
}
