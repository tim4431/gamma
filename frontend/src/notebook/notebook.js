// Sheets of paper to write on (docs/dev/notebooks.md). The client half of
// gamma/notebook.py; pure, so it runs under node --test and in the iPad's
// JavaScriptCore.
//
// A sheet is a block carrying `sheet: <paper>`, anywhere among a page's
// blocks; nothing else marks a page as a notebook — the notebook view is a
// way of showing the sheets it has. A page's sheets are those blocks in
// document order, at any depth; adding a page is inserting a block, so
// sheets two devices add while apart both survive a merge. A sheet's
// handwriting is the ink groups under it that no nearer sheet holds (blocks
// with `ink_url`, their file on a `canvas` space the sheet's size); other
// blocks under a sheet are notes about that page. Stored papers are read
// through normalizePaper, never trusted.

export const DEFAULT_PAPER = Object.freeze({ width: 595.28, height: 841.89, color: "#ffffff", pattern: "blank",
  spacing: 24, line: "#c8d1dc" });
export const PATTERNS = ["blank", "ruled", "grid", "dots"];
export const MIN_SIDE = 144, MAX_SIDE = 2000;
export const MIN_SPACING = 12, MAX_SPACING = 96;
export const LINE_WIDTH = 0.5, DOT_RADIUS = 0.9;
// Sizes the paper menu offers (portrait; the menu turns them).
export const PAPER_SIZES = [
  { key: "a4", width: 595.28, height: 841.89 },
  { key: "letter", width: 612, height: 792 },
  { key: "a5", width: 419.53, height: 595.28 },
];
// Backgrounds the paper menu offers: white, cream, grey, dark.
export const PAPER_COLORS = ["#ffffff", "#fbf7ec", "#f2f3f5", "#2b2d31"];
const HEX = /^#[0-9a-f]{6}$/i;

const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const round2 = (v) => Math.round(v * 100) / 100;
const clamp = (v, lo, hi) => round2(Math.min(hi, Math.max(lo, v)));

// A complete paper from a stored one: each missing or bad key from
// `fallback` (itself normalized; the built-in default when null), sizes
// and spacing clamped and rounded to hundredths, colours lowercase.
export function normalizePaper(raw, fallback = null) {
  const out = { ...(fallback != null ? normalizePaper(fallback) : DEFAULT_PAPER) };
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return out;
  for (const k of ["width", "height"]) {
    const v = num(raw[k]);
    if (v !== null) out[k] = clamp(v, MIN_SIDE, MAX_SIDE);
  }
  const s = num(raw.spacing);
  if (s !== null) out.spacing = clamp(s, MIN_SPACING, MAX_SPACING);
  for (const k of ["color", "line"]) if (typeof raw[k] === "string" && HEX.test(raw[k])) out[k] = raw[k].toLowerCase();
  if (PATTERNS.includes(raw.pattern)) out.pattern = raw.pattern;
  return out;
}

// Offsets k * spacing, k = 1, 2, …, strictly inside `extent`.
function steps(extent, spacing) {
  const out = [];
  for (let k = 1; k * spacing < extent - 1e-9; k++) out.push(round2(k * spacing));
  return out;
}

// The pattern as geometry, in points from the top-left corner: {lines:
// [[x1, y1, x2, y2]], dots: [[x, y]]} — a line every `spacing` for ruled
// paper, grid paper's verticals then horizontals, a dot at every crossing.
export function paperLines(paper) {
  const { width: w, height: h, spacing: s, pattern } = paper;
  const lines = [], dots = [];
  if (pattern === "ruled" || pattern === "grid") {
    if (pattern === "grid") for (const x of steps(w, s)) lines.push([x, 0, x, h]);
    for (const y of steps(h, s)) lines.push([0, y, w, y]);
  } else if (pattern === "dots") {
    const xs = steps(w, s);
    for (const y of steps(h, s)) for (const x of xs) dots.push([x, y]);
  }
  return { lines, dots };
}

export const isLandscape = (paper) => paper.width > paper.height;
export const turnPaper = (paper) => ({ ...paper, width: paper.height, height: paper.width });
// Which of PAPER_SIZES a paper is (either way round), or "".
export function paperSizeKey(paper) {
  const hit = PAPER_SIZES.find((s) => (Math.abs(s.width - paper.width) < 0.6 && Math.abs(s.height - paper.height) < 0.6)
    || (Math.abs(s.width - paper.height) < 0.6 && Math.abs(s.height - paper.width) < 0.6));
  return hit ? hit.key : "";
}

// --- the sheets in a page's tree ---------------------------------------------

export const isSheet = (block) => {
  const s = block?.properties?.sheet;
  return !!s && typeof s === "object" && !Array.isArray(s);
};

// The sheets of a page's tree (the page's blocks, nested `children`), in
// document order at any depth: [{id, index (0-based), paper, block}].
export function sheetsOf(tree) {
  const out = [];
  const walk = (list) => {
    for (const b of list || []) {
      if (isSheet(b)) out.push({ id: b.id, index: out.length, paper: normalizePaper(b.properties.sheet), block: b });
      walk(b.children);
    }
  };
  walk(tree);
  return out;
}

// Map sheet id → the ink blocks it draws, in tree order: those under it,
// at any depth, that no nearer sheet holds.
export function inkBySheet(tree) {
  const out = new Map();
  const walk = (list, sheet) => {
    for (const b of list || []) {
      const here = isSheet(b) ? b.id : sheet;
      if (isSheet(b)) out.set(b.id, []);
      else if (here && b.properties?.ink_url !== undefined) out.get(here).push({ id: b.id, properties: b.properties });
      walk(b.children, here);
    }
  };
  walk(tree, null);
  return out;
}

// The sheet a block of the tree is on (itself, or its nearest sheet
// ancestor), or null.
export function sheetOfBlock(tree, id) {
  const walk = (list, sheet) => {
    for (const b of list || []) {
      const here = isSheet(b) ? b.id : sheet;
      if (b.id === id) return here || null;
      const found = walk(b.children, here);
      if (found !== undefined) return found;
    }
    return undefined;
  };
  return walk(tree, null) ?? null;
}

// The paper a sheet added right after block `id` gets: that of the sheet
// nearest before it in document order (itself, when it is one), else the
// default.
export function paperBefore(tree, id) {
  let paper = DEFAULT_PAPER, found = false;
  const walk = (list) => {
    for (const b of list || []) {
      if (found) return;
      if (isSheet(b)) paper = normalizePaper(b.properties.sheet);
      if (b.id === id) { found = true; return; }
      walk(b.children);
    }
  };
  walk(tree);
  return paper;
}

// --- ids ---------------------------------------------------------------------

// FNV-1a 64 over the UTF-8 of the parts: the same parts give the same id on
// every device, so two devices adding "the sheet after this one" at once
// add one sheet (inserting an id a page has is a no-op).
export function stableId(prefix, ...parts) {
  let h = 0xcbf29ce484222325n;
  for (const byte of new TextEncoder().encode(parts.join("\u001f"))) {
    h ^= BigInt(byte);
    h = (h * 0x100000001b3n) & 0xffffffffffffffffn;
  }
  return prefix + h.toString(16).padStart(16, "0");
}
// A new notebook's first sheet, and the sheet that follows `sheetId`.
export const firstSheetId = (pageId) => stableId("s", "first-sheet", pageId);
export const sheetIdAfter = (sheetId) => stableId("s", "sheet-after", sheetId);

// A new sheet block, folded: its handwriting groups are its children, and
// the sheet itself shows them.
export function newSheet(id, paper) {
  return { id, content: "", properties: { sheet: normalizePaper(paper), collapsed: true }, children: [] };
}
