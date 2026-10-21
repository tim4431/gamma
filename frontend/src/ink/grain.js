// The pencil's grain (docs/dev/handwriting.md "Client"): one tile of
// speckles, about 70 % covered at three strengths, made once here and
// shared by the retained SVG (a <pattern> per pencil colour on a surface,
// filled from grainUri) and the live canvas (a createPattern of
// grainImage). The tile is GRAIN_PX pixels square and spans GRAIN_UNITS
// page units, so the grain zooms with the paper (two grains a point: a
// 2 pt line is four grains wide). Pure: the PNG is written here from a
// fixed seed — a 2-bit palette image, so another colour is another palette
// and nothing else — and every client draws the same grain.
export const GRAIN_PX = 64;
export const GRAIN_UNITS = 32;
// The palette's alpha per index: no grain, and three strengths.
const ALPHAS = [0, 128, 204, 255];
// The share of the tile at each strength, cumulative: 30 % bare paper.
const LEVELS = [0.3, 0.45, 0.7];

// A fixed-seed generator (mulberry32), so the tile is the same everywhere.
function random(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// The tile's palette indices, row by row: white noise over a coarser,
// smooth noise (the paper's tooth), tileable, cut at the LEVELS quantiles.
function tileIndices() {
  const rand = random(0x5eed1e55);
  const CELL = 4, cells = GRAIN_PX / CELL;
  const grid = Array.from({ length: cells * cells }, rand);
  const at = (cx, cy) => grid[((cy + cells) % cells) * cells + ((cx + cells) % cells)];
  const smooth = (f) => f * f * (3 - 2 * f);
  const values = new Float64Array(GRAIN_PX * GRAIN_PX);
  for (let y = 0; y < GRAIN_PX; y++) {
    for (let x = 0; x < GRAIN_PX; x++) {
      const cx = Math.floor(x / CELL), cy = Math.floor(y / CELL);
      const fx = smooth((x % CELL) / CELL), fy = smooth((y % CELL) / CELL);
      const top = at(cx, cy) * (1 - fx) + at(cx + 1, cy) * fx;
      const bottom = at(cx, cy + 1) * (1 - fx) + at(cx + 1, cy + 1) * fx;
      values[y * GRAIN_PX + x] = 0.7 * rand() + 0.3 * (top * (1 - fy) + bottom * fy);
    }
  }
  const sorted = Float64Array.from(values).sort();
  const cuts = LEVELS.map((q) => sorted[Math.floor(q * sorted.length)]);
  return Array.from(values, (v) => (v < cuts[0] ? 0 : v < cuts[1] ? 1 : v < cuts[2] ? 2 : 3));
}

let indices = null;
// The image data a PNG's IDAT holds: each row a filter byte (0) and its
// pixels at two bits each, the first in the high bits.
function scanlines() {
  indices ||= tileIndices();
  const row = 1 + GRAIN_PX / 4, out = new Uint8Array(row * GRAIN_PX);
  for (let y = 0; y < GRAIN_PX; y++) {
    for (let x = 0; x < GRAIN_PX; x++) out[y * row + 1 + (x >> 2)] |= indices[y * GRAIN_PX + x] << (6 - 2 * (x & 3));
  }
  return out;
}

const CRC = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();
function crc32(bytes) {
  let c = 0xffffffff;
  for (const b of bytes) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
const u32 = (v) => [(v >>> 24) & 255, (v >>> 16) & 255, (v >>> 8) & 255, v & 255];
function chunk(type, data) {
  const body = [...type].map((c) => c.charCodeAt(0)).concat([...data]);
  return [...u32(data.length), ...body, ...u32(crc32(body))];
}
// A zlib stream of one stored (uncompressed) block: the tile is small.
function zlibStored(data) {
  let a = 1, b = 0;
  for (const v of data) { a = (a + v) % 65521; b = (b + a) % 65521; }
  const n = data.length;
  return [0x78, 0x01, 0x01, n & 255, n >> 8, ~n & 255, (~n >> 8) & 255, ...data, ...u32(((b << 16) | a) >>> 0)];
}

// A colour as [r, g, b]: #rrggbb, #rgb or rgb[a](r, g, b[, a]); black otherwise.
function rgbOf(color) {
  const s = String(color || "").trim();
  let m = /^#([0-9a-f]{6})$/i.exec(s);
  if (m) return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
  m = /^#([0-9a-f]{3})$/i.exec(s);
  if (m) return [...m[1]].map((h) => parseInt(h + h, 16));
  m = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i.exec(s);
  if (m) return [m[1], m[2], m[3]].map((v) => Math.max(0, Math.min(255, Math.round(Number(v)))));
  return [0, 0, 0];
}

const uris = new Map();
// The grain tile in `color` as a PNG data URI, built once per colour.
export function grainUri(color = "#000000") {
  const key = String(color).toLowerCase();
  let uri = uris.get(key);
  if (uri) return uri;
  const rgb = rgbOf(key);
  const bytes = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
    ...chunk("IHDR", [...u32(GRAIN_PX), ...u32(GRAIN_PX), 2, 3, 0, 0, 0]),
    ...chunk("PLTE", ALPHAS.flatMap(() => rgb)),
    ...chunk("tRNS", ALPHAS),
    ...chunk("IDAT", zlibStored(scanlines())),
    ...chunk("IEND", [])];
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  uri = "data:image/png;base64," + btoa(binary);
  uris.set(key, uri);
  return uri;
}
// The tile in black, the colour a pattern is tinted from.
export const GRAIN_URI = grainUri("#000000");

const images = new Map();
// The tile in `color` decoded, for a canvas's createPattern: null until the
// browser has decoded it (the first frames of a first pencil stroke draw
// plain), and null where there is no Image (node).
export function grainImage(color = "#000000") {
  if (typeof Image === "undefined") return null;
  const key = String(color).toLowerCase();
  let img = images.get(key);
  if (!img) {
    img = new Image();
    img.src = grainUri(key);
    images.set(key, img);
  }
  return img.complete && img.naturalWidth ? img : null;
}
