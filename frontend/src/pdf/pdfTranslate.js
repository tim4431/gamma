// Pure page-segmentation for the AI translated view: pdf.js text runs in,
// paragraph blocks out. No pdf.js/react imports on purpose (like
// shared/model/blockModel.js) — the geometry math is testable in isolation.
// `textRuns` turns a page's TextContent items into the flat runs consumed
// here, for the viewer (pdf/PdfViewer.jsx) and the translation bench
// (tests/bench/translateBench.mjs) alike.
//
// A run is {str, x, y, w, h}: baseline-left origin at scale-1 viewport
// coordinates, w the advance width, h the font height (both viewport units),
// plus its font's name and glyph metrics (textRuns).
// A block is {x1, y1, x2, y2, size, nLines, lines, indent, text, src, spans,
// translate} — the tight bbox the overlay masks and refills, its per-line
// rects, the dominant font height, the text as extracted, the text for the
// model with its formulas, citations and URLs as tokens and what each token
// stands for (blockSource), and whether it is prose worth sending at all
// (equations, bare numbers, stray symbols and author lines stay original — a
// wrong "translation" of an equation is worse than none).

import { TEXT_TOKEN, lineParts, spanBox } from "./pdfMath.js";

// TextContent items → runs. `transform` is the scale-1 viewport's transform
// (`page.getViewport({scale: 1}).transform`); each item's matrix is mapped
// through it the way pdf.js's Util.transform does. `faces` (pdf/pdfFonts.js)
// names the item's real font, `styles` is the TextContent's own table (its
// ascent and descent, em fractions, bound a run's glyphs for formula crops).
export function textRuns(items, transform, { faces = null, styles = null } = {}) {
  const [a, b, c, d, e, f] = transform;
  return (items || []).map((it) => {
    const [p, q, r, s, t, u] = it.transform;
    const tx = [a * p + c * q, b * p + d * q, a * r + c * s, b * r + d * s, a * t + c * u + e, b * t + d * u + f];
    const h = Math.hypot(tx[2], tx[3]) || 10;
    const font = it.fontName || "";
    const face = faces?.get(font);
    const style = styles?.[font];
    // pdf.js reports ascent = descent = 0 for a font it has no metrics for:
    // that is no glyph box, and the defaults stand in for it.
    const metrics = style?.ascent > 0 && style.ascent - style.descent >= 0.3;
    return {
      str: it.str || "", x: tx[4], y: tx[5], w: it.width || h, h, font,
      // Text not running left to right on screen (after the page's own
      // /Rotate, which the viewport transform includes).
      rotated: Math.abs(tx[1]) > Math.abs(tx[0]) * 0.05 || tx[0] < 0,
      face: face?.name || "", type3: !!face?.type3,
      asc: metrics ? style.ascent : 0.8,
      desc: metrics ? style.descent : -0.25,
    };
  });
}

// Horizontal-gap thresholds (× line height) for splitting a visual line into
// fragments. A HARD gap always splits (scattered axis labels, table cells).
// A SOFT gap splits only where it is a column gutter (~1.4× in revtex) —
// `isGutter` below — while a one-off gap (a loose justified line, the gaps
// of inline math) must not shred the line. The widest justified word gap is
// ~0.8×, a loose line's sentence space can pass 1.05×.
const GAP_HARD = 2.2;
const GAP_SOFT = 1.05;
// A fragment continues the block above it while the baseline step stays
// within this × line height; bigger steps start a new block.
const LINE_STEP = 1.7;
// A soft gap is judged by the lines this × line height above and below.
const GUTTER_REACH = 3;
// A run this much smaller than its line's body text, off its baseline by more
// than SCRIPT_SHIFT × the line height, is a sub- or superscript.
const SCRIPT_SIZE = 0.9;
const SCRIPT_SHIFT = 0.1;

// --- lines → fragments -------------------------------------------------------

// The vertical middle of a run's glyphs. Lines group on it rather than on the
// baseline: a big operator (CMEX's Σ, ascent ~0, descent −0.6) sits on the
// baseline of the line ABOVE the one it is drawn on.
function midOf(r) {
  return r.y - r.h * (r.asc + r.desc) / 2;
}

// Group runs into visual lines. Only the last few lines are candidates — the
// sort means anything older can't match. A line is anchored on its longest
// run: its body text, not a superscript sorted first nor a tall glyph beside
// it (a drop cap, an integral sign set large), which would otherwise draw
// the other column's lines into it and pass for the line's height.
function groupLines(items) {
  items.sort((a, b) => (a.mid - b.mid) || (a.x - b.x));
  const lines = [];
  for (const it of items) {
    let line = null;
    for (let i = lines.length - 1; i >= 0 && i >= lines.length - 6; i--) {
      if (Math.abs(lines[i].mid - it.mid) <= Math.min(lines[i].h, it.h) * 0.45) { line = lines[i]; break; }
    }
    const chars = it.str.trim().length;
    if (!line) { line = { mid: it.mid, h: it.h, chars, runs: [] }; lines.push(line); }
    line.runs.push(it);
    if (chars > line.chars) { line.mid = it.mid; line.h = it.h; line.chars = chars; }
  }
  return lines;
}

// A line's runs in chains: left to right, each within `reach` of the last.
function chains(runs, reach) {
  const out = [];
  for (const r of [...runs].sort((a, b) => a.x - b.x)) {
    const c = out[out.length - 1];
    if (c && r.x - c.x2 <= reach) {
      c.runs.push(r);
      c.x2 = Math.max(c.x2, r.x + r.w);
      if (r.h > c.h) { c.h = r.h; c.mid = r.mid; }
    } else out.push({ runs: [r], x1: r.x, x2: r.x + r.w, h: r.h, mid: r.mid });
  }
  return out;
}

// A superscript raised past the grouping tolerance forms a "line" of its
// own above its base glyph (a smaller subscript can fall below). Fold each
// such script back into the line it is attached to. A script is short — a
// chain of at most 12 characters and five glyph heights, smaller than the
// glyph it touches (before it, as an affiliation's ¹, or after it, as x²)
// and within that glyph's height of it. A line of smaller text (a reference
// beside the body, a caption, a sidebar) never folds: it is no script, and
// folded it would interleave two lines' words.
function foldScripts(lines) {
  for (let i = 0; i < lines.length; i++) {
    const S = lines[i];
    const kept = [];
    for (const c of chains(S.runs, S.h * 0.6)) {
      const chars = c.runs.reduce((n, r) => n + r.str.replace(/\s/g, "").length, 0);
      let best = null, bestDist = Infinity;
      for (let j = Math.max(0, i - 3); chars <= 12 && j <= Math.min(lines.length - 1, i + 3); j++) {
        if (j === i) continue;
        for (const n of lines[j].runs) {
          if (c.h > n.h * 0.85 || c.x2 - c.x1 > n.h * 5) continue;
          if (Math.max(n.x - c.x2, c.x1 - (n.x + n.w)) > n.h * 0.4) continue;
          const dist = Math.abs(c.mid - n.mid);
          if (dist <= n.h * 0.9 && dist < bestDist) { best = lines[j]; bestDist = dist; }
        }
      }
      if (best) best.runs.push(...c.runs); else kept.push(...c.runs);
    }
    S.runs = kept;
  }
  return lines.filter((L) => L.runs.length);
}

// A soft gap becomes a cut only where a column gutter runs: whitespace that
// the lines around it leave open too, a river. Inside a paragraph the lines
// above and below run through a wide word space (or the gaps of a line of
// inline math, or a centred heading's); beside a gutter they leave it open,
// whether or not the columns' baselines line up, and wherever within the
// gap the river runs (a line ending a little short of its column widens its
// gap past the gutter). So: among the full-size lines within GUTTER_REACH
// line heights that have text near the gap (a line of another column far
// off says nothing about it), there must be a stretch of the gap half a line
// height wide (the whole gap, if narrower) that all but a third of them
// leave open — a page footer under the gutter or the end of an abstract may
// reach into the window — and that two of them leave open.
function isGutter(lines, i, br) {
  const L = lines[i];
  const n1 = br.x1 - L.h * 4, n2 = br.x2 + L.h * 4;
  const near = [];
  for (const dir of [-1, 1]) {
    for (let j = i + dir; j >= 0 && j < lines.length; j += dir) {
      const N = lines[j];
      if (Math.abs(N.mid - L.mid) > L.h * GUTTER_REACH) break;
      if (N.h >= L.h * 0.7 && N.runs.some((r) => r.x < n2 && r.x + r.w > n1)) near.push(N);
    }
  }
  if (!near.length) return false;
  // How many of them have ink at each quarter point of the gap.
  const step = 0.25;
  const cells = Math.max(1, Math.ceil((br.x2 - br.x1) / step));
  const cover = new Array(cells).fill(0);
  for (const N of near) {
    const inked = new Uint8Array(cells);
    for (const r of N.runs) {
      const a = Math.max(0, Math.floor((r.x - br.x1) / step));
      const b = Math.min(cells, Math.ceil((r.x + r.w - br.x1) / step));
      for (let k = a; k < b; k++) inked[k] = 1;
    }
    for (let k = 0; k < cells; k++) cover[k] += inked[k];
  }
  const allowance = Math.floor(near.length / 3);
  let best = null, start = -1;
  for (let k = 0; k <= cells; k++) {
    if (k < cells && cover[k] <= allowance) { if (start < 0) start = k; continue; }
    if (start >= 0 && (!best || k - start > best[1] - best[0])) best = [start, k];
    start = -1;
  }
  if (!best || (best[1] - best[0]) * step < Math.min(br.x2 - br.x1, L.h * 0.5) - step) return false;
  // The river, inside the gap and half a point in from its edges: justified
  // lines end and start exactly at the column margins.
  const r1 = br.x1 + best[0] * step + 0.5, r2 = Math.min(br.x2, br.x1 + best[1] * step) - 0.5;
  if (r2 <= r1) return false;
  const open = near.filter((N) => !N.runs.some((r) => r.x < r2 && r.x + r.w > r1)).length;
  // A single line around it (the page's last line under a gap, a line
  // beside a heading's space) vouches only for a gap of a gutter's width.
  if (near.length === 1) return open === 1 && br.x2 - br.x1 >= L.h * 1.5;
  return open >= 2;
}

// The median of `value` over `runs`, each weighted by its characters.
function weightedMedian(runs, value) {
  const weight = (r) => r.str.trim().length || 1;
  const sorted = [...runs].sort((a, b) => value(a) - value(b));
  let half = sorted.reduce((n, r) => n + weight(r), 0) / 2;
  let v = value(sorted[0]);
  for (const r of sorted) { v = value(r); half -= weight(r); if (half <= 0) break; }
  return v;
}

// A fragment's baseline and body height: the height most of its text is
// set in, and the baseline of its runs at that height with an ordinary glyph
// box (not a big operator, not a tall glyph like a drop cap).
function bodyLine(runs) {
  const h = weightedMedian(runs, (r) => r.h);
  const body = runs.filter((r) => r.h >= h * SCRIPT_SIZE && r.h <= h * 1.5 && r.asc > 0.3);
  return { y: weightedMedian(body.length ? body : runs, (r) => r.y), h };
}

// A sub- or superscript: clearly smaller than the body text and off its
// baseline, or a little smaller and well off it. An OCR text layer's words
// vary a little in size and baseline and are no scripts.
function scriptOf(r, y, h) {
  const shift = Math.abs(r.y - y);
  const small = (r.h < h * 0.8 && shift > h * SCRIPT_SHIFT) || (r.h < h * SCRIPT_SIZE && shift > h * 0.25);
  return small ? (r.y < y ? "sup" : "sub") : null;
}

function buildFragments(runs) {
  // Rotated text (a vertical arXiv stamp in the margin, a rotated axis
  // label) has no place in the horizontal line model: read as horizontal it
  // becomes one long run across the column. It stays as it is on the page.
  const items = runs.filter((r) => r.str && r.str.trim() && r.h > 0 && r.w > 0 && !r.rotated)
    .map((r) => ({ ...r, mid: midOf(r) }));
  const lines = foldScripts(groupLines(items));
  // Candidate split gaps per line (x-interval of the whitespace).
  for (const L of lines) {
    L.runs.sort((a, b) => a.x - b.x);
    L.breaks = [];
    let end = null;
    for (const r of L.runs) {
      if (end !== null && r.x - end > L.h * GAP_SOFT) {
        L.breaks.push({ x1: end, x2: r.x, hard: r.x - end > L.h * GAP_HARD });
      }
      end = end === null ? r.x + r.w : Math.max(end, r.x + r.w);
    }
  }
  const frags = [];
  lines.forEach((L, i) => {
    const cuts = L.breaks.filter((br) => br.hard || isGutter(lines, i, br));
    let cur = null;
    let end = null;
    for (const r of L.runs) {
      const atCut = cur && cuts.some((c) => c.x1 >= end - 0.5 && c.x2 <= r.x + 0.5);
      if (!cur || atCut) {
        cur = { x1: r.x, x2: r.x + r.w, runs: [] };
        frags.push(cur);
      }
      cur.runs.push(r);
      cur.x2 = Math.max(cur.x2, r.x + r.w);
      end = end === null ? r.x + r.w : Math.max(end, r.x + r.w);
    }
  });
  for (const f of frags) {
    const { y, h } = bodyLine(f.runs);
    f.y = y;
    f.h = h;
    // Glyph extent, scripts and big operators included: what the line's mask
    // has to cover.
    f.top = Math.min(y - h, ...f.runs.map((r) => r.y - r.h * r.asc));
    f.bottom = Math.max(y + h * 0.25, ...f.runs.map((r) => r.y - r.h * r.desc));
    let text = "";
    let prevEnd = null;
    const fonts = {};
    for (const r of f.runs) {
      r.script = scriptOf(r, y, h);
      const gap = prevEnd === null ? 0 : r.x - prevEnd;
      const needsSpace = text && gap > Math.max(h * (r.script ? 0.3 : 0.12), 0)
        && !/\s$/.test(text) && !/^\s/.test(r.str);
      r.spaced = !!needsSpace;
      text += (needsSpace ? " " : "") + r.str;
      prevEnd = prevEnd === null ? r.x + r.w : Math.max(prevEnd, r.x + r.w);
      fonts[r.font || ""] = (fonts[r.font || ""] || 0) + r.str.length;
    }
    f.text = text;
    // Dominant font per fragment + how dominant it is — the block matcher
    // uses a CONFIDENT font change (roman body vs italic affiliation, an
    // all-symbols equation line) as a paragraph boundary. lastFont: the font
    // the line ends in, which a wrapped URL or italic phrase carries over.
    let total = 0, best = 0;
    for (const k in fonts) {
      total += fonts[k];
      if (fonts[k] > best) { best = fonts[k]; f.font = k; }
    }
    f.fontShare = total ? best / total : 0;
    f.lastFont = [...f.runs].reverse().find((r) => r.str.trim())?.font || "";
  }
  frags.sort((a, b) => (a.y - b.y) || (a.x1 - b.x1));
  return frags;
}

// --- fragments → blocks ------------------------------------------------------

function xOverlap(a, b) {
  return Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1);
}

// A word broken at a line's end: its hyphen, which a lower-case start on the
// next line rejoins (joinLine here; pdf/translatePlan.js joins the same way
// across a column or page break).
export const HYPHENATED = /[A-Za-z]-$/;

// Append `s` to the block text: rejoin hyphenated line breaks and URLs
// broken after a path separator, no space between CJK, a space otherwise.
function joinLine(out, s) {
  if (!out) return s;
  // After a period only a path goes on ("example." / "org/data"): a URL
  // that ends a sentence is followed by the next sentence.
  if (/https?:\/\/\S*[/\-_~#?=&]$/.test(out) && /^[\w%~#/-]/.test(s)) return out + s;
  if (/https?:\/\/\S*\.$/.test(out) && /^[a-z0-9][\w%~#-]*\//i.test(s)) return out + s;
  if (HYPHENATED.test(out) && /^[a-z]/.test(s)) return out.slice(0, -1) + s;
  if (/[぀-ヿ㐀-鿿]$/.test(out) && /^[぀-ヿ㐀-鿿]/.test(s)) return out + s;
  return out + " " + s;
}

// A text selection's string as one paragraph for the selection translator:
// the text layer puts a line break at every visual line, rejoined the same
// way as a segmented block's lines. Blank lines (a selection spanning
// paragraphs) stay paragraph breaks.
export function selectionParagraphs(text) {
  return (text || "").split(/\n\s*\n/)
    .map((para) => para.split("\n").map((l) => l.trim()).filter(Boolean).reduce(joinLine, ""))
    .filter(Boolean)
    .join("\n\n");
}

// Whether a block's text should go to the model. Mostly-symbolic content
// (equations, tables of numbers, page footers) is left in place.
export function isTranslatable(text) {
  const t = text.trim();
  const dense = t.replace(/\s/g, "");
  if (dense.length < 2) return false;
  const cjk = (t.match(/[぀-ヿ㐀-鿿가-힯]/g) || []).length;
  if (cjk >= 2) return true;
  const letters = (t.match(/[A-Za-zÀ-ɏЀ-ӿ]/g) || []).length;
  if (letters < 4 || letters / dense.length < 0.5) return false;
  // At least one real word (two adjacent letters, Latin or Cyrillic) — a
  // scatter of single-letter variables like "x y z p q" isn't prose.
  return /[A-Za-zÀ-ɏЀ-ӿ]{2}/.test(t);
}

// Whether a block's text for the model (`src`, its formulas as tokens) is an
// author line, which has nothing to translate: names separated by commas (or
// "and", "&"), most of them a first and a last name, and an affiliation mark
// (a superscript, so a token) ending most of them and at least two. A mark
// ends a name when it follows it, or the comma after it as APS sets it
// ("Thompson,{v1} T. G. Tiecke"). One glued to the word after it opens an
// affiliation instead ("{v1}Harvard University, {v2}Google Research"), one
// before the first name with a space after it is a stray, and one inside a
// piece means the piece is no name ("… and Entanglement of {v1}Rb Atoms").
// A name is one to four capitalised words with no digit, and its only
// lower-case words are particles ("van der", "de la"): a title or heading in
// title case writes "of", "the" or "in" between its words.
const NAME_PARTICLES = new Set(["al", "bin", "d", "da", "das", "de", "del", "della", "den", "der", "di", "do", "dos",
  "du", "e", "el", "et", "ibn", "la", "le", "ten", "ter", "van", "von", "y", "zu"]);
// Marks before or after a name: tokens, with the footnote signs set beside
// them ("Satzinger{v1}*", "Endres{v6}✉").
const LEAD_MARKS = /^\s*(?:(?:\{v\d+\}|[*∗†‡§¶✉])\s*)*/;
const TRAIL_MARKS = /(?:(?:\{v\d+\}|[*∗†‡§¶✉])\s*)*$/;
const TOKEN_MARK = /\{v\d+\}/;
// An accent on no letter: TeX sets the caron of Š as a glyph of its own,
// which can land after the comma ("Tamara Sumarac,ˇ{v2} Peiran Niu").
const LOOSE_ACCENT = /[`\u00a8\u00af\u00b4\u02c6-\u02dd](?!\p{L})/gu;
export function isNameList(src) {
  if (!TOKEN_MARK.test(src)) return false;
  const names = [];
  let opening = 0;
  for (const piece of src.replace(LOOSE_ACCENT, "").split(/,|&|\band\b/)) {
    const lead = piece.match(LEAD_MARKS)[0];
    const rest = piece.slice(lead.length);
    const trail = rest.match(TRAIL_MARKS)[0];
    const body = rest.slice(0, rest.length - trail.length).trim();
    if (TOKEN_MARK.test(body)) return false;
    const before = names[names.length - 1];
    if (TOKEN_MARK.test(lead)) {
      // Glued to the comma: the name before's. Glued to the word after: an
      // affiliation's. Else the name before's, or a stray before the first.
      if (before && !/^\s/.test(piece)) before.marked = true;
      else if (body && !/\s$/.test(lead)) opening += 1;
      else if (before) before.marked = true;
    }
    if (body) names.push({ words: body.split(/\s+/), marked: TOKEN_MARK.test(trail) });
  }
  const marked = names.filter((n) => n.marked).length;
  const words = names.flatMap((n) => n.words);
  const capital = (w) => /^\p{Lu}/u.test(w);
  // The words besides the particles are capitalised, or pieces of a name an
  // accent split ("Vuleti ´c").
  const others = words.filter((w) => !/^\p{Ll}/u.test(w));
  return marked >= 2 && marked * 2 >= names.length && opening < marked
    && !/\d/.test(words.join(""))
    && words.every((w) => !/^\p{Ll}/u.test(w) || NAME_PARTICLES.has(w.replace(/[.’'-].*$/, "")))
    && others.filter(capital).length >= others.length * 0.7
    && names.filter((n) => n.words.length >= 2).length * 2 > names.length
    && names.every((n) => n.words.filter(capital).length <= 4);
}

// A block's text for the model, its formulas, citation markers and URLs as
// numbered tokens ({v1}, {v2}…, pdf/pdfMath.js), and what each token stands
// for: a formula's box on the page (the overlay crops it back into the
// translation) or literal text. Lines are joined first and the tokens
// numbered after, so a URL broken over two lines is one token. `plain` is
// the text without its tokens: whether the block is prose at all.
const OPEN = String.fromCharCode(1), CLOSE = String.fromCharCode(2);
const TOKENS = new RegExp(`${OPEN}(\\d+)${CLOSE}|${TEXT_TOKEN.source}`, "g");

function blockSource(lines) {
  const formulas = [];
  const joined = lines.reduce((acc, l) => joinLine(acc, lineParts(l.runs, l.h).map((p) => {
    if (p.kind === "text") return p.str;
    formulas.push({ str: p.str.trim(), ...spanBox(p.runs, l.base) });
    return `${OPEN}${formulas.length - 1}${CLOSE}`;
  }).join("").trim()), "");
  const spans = [];
  let src = "", plain = "", at = 0;
  for (const m of joined.matchAll(TOKENS)) {
    src += joined.slice(at, m.index);
    plain += joined.slice(at, m.index);
    spans.push(m[1] !== undefined ? { kind: "math", ...formulas[Number(m[1])] } : { kind: "text", str: m[0] });
    src += `{v${spans.length}}`;
    at = m.index + m[0].length;
  }
  src += joined.slice(at);
  plain += joined.slice(at);
  return { src, spans, plain };
}

// A block accumulated too eagerly can wrap AROUND a figure: full-width lines
// above it, short lines beside it. One bounding rect for that block covers
// the figure. Split the line list where the right edge jumps — widening
// always ends a unit (a paragraph or a figure-constrained stretch just
// finished); narrowing only when the NEXT line is narrow too (a lone short
// line is just a paragraph's last line).
function splitByWidth(lines, size) {
  const parts = [];
  let cur = [lines[0]];
  for (let i = 1; i < lines.length; i++) {
    const prev = lines[i - 1], L = lines[i];
    const widen = L.x2 - prev.x2 > size * 2.5;
    const narrow = prev.x2 - L.x2 > size * 2.5
      && i + 1 < lines.length && Math.abs(lines[i + 1].x2 - L.x2) < size * 1.5;
    if (widen || narrow) { parts.push(cur); cur = []; }
    cur.push(L);
  }
  parts.push(cur);
  return parts;
}

export function segmentPage(runs) {
  const frags = buildFragments(runs);
  const blocks = [];
  for (const f of frags) {
    const fTop = f.y - f.h, fW = f.x2 - f.x1;
    let best = null, bestOv = 0;
    for (const b of blocks) {
      const step = f.y - b.lastY;
      // Same baseline (another column's fragment) or too far below — no merge.
      if (step <= f.h * 0.3 || step > Math.max(b.size, f.h) * LINE_STEP) continue;
      if (Math.max(b.size, f.h) / Math.min(b.size, f.h) > 1.34) continue; // font break (heading vs body)
      // First-line indent = a new paragraph (wrapped lines start at the
      // column edge; only paragraph openers sit deeper). Also keeps centered
      // author/affiliation stacks one block per line, and splits lines that
      // wrap around a figure on the LEFT (they start much deeper).
      if (f.x1 - b.x1 > f.h * 0.9) continue;
      // A confident font change (both sides ≥70% one font) is a boundary:
      // roman authors vs italic affiliations, a display-equation line inside
      // a paragraph. Mixed lines (inline math) stay mergeable, and so does a
      // line in the font the line above ended in: a URL or an italic phrase
      // wrapped onto the next line.
      if (b.font !== f.font && b.fontShare > 0.7 && f.fontShare > 0.7 && b.lastFont !== f.font) continue;
      const ov = xOverlap(b, f);
      if (ov >= 0.5 * Math.min(b.x2 - b.x1, fW) && ov > bestOv) { best = b; bestOv = ov; }
    }
    const line = { x1: f.x1, x2: f.x2, y1: Math.min(fTop, f.top), y2: f.bottom, text: f.text.trim(), h: f.h,
                   base: f.y, runs: f.runs };
    if (best) {
      best.x1 = Math.min(best.x1, f.x1);
      best.x2 = Math.max(best.x2, f.x2);
      best.lastY = f.y;
      best.lastFont = f.lastFont;
      best.lines.push(line);
      // Dominant size: lean on the first lines, headings rarely mix mid-block.
      best.size = Math.min(best.size, Math.max(f.h, best.size * 0.85));
    } else {
      blocks.push({
        x1: f.x1, x2: f.x2, lastY: f.y, size: f.h, lines: [line],
        font: f.font, fontShare: f.fontShare, lastFont: f.lastFont,
      });
    }
  }
  const out = [];
  for (const b of blocks) {
    for (const lines of splitByWidth(b.lines, b.size)) {
      const x1 = Math.min(...lines.map((l) => l.x1));
      const x2 = Math.max(...lines.map((l) => l.x2));
      const y1 = Math.min(...lines.map((l) => l.y1));
      const y2 = Math.max(...lines.map((l) => l.y2));
      if (x2 - x1 <= 1 || y2 - y1 <= 1) continue;
      const text = lines.reduce((acc, l) => joinLine(acc, l.text), "");
      const { src, spans, plain } = blockSource(lines);
      out.push({
        x1, x2, y1, y2, size: b.size, nLines: lines.length, text, src, spans,
        // Per-line rects: the overlay masks exactly these, so a box that
        // (still) brushes a figure never paints over it.
        lines: lines.map(({ x1, x2, y1, y2 }) => ({ x1, x2, y1, y2 })),
        // The first line's left edge against the next line's: a paragraph
        // opener sits deeper than its wrapped lines (pdf/translatePlan.js
        // reads it to tell a new paragraph from a continued one).
        indent: lines.length > 1 ? lines[0].x1 - lines[1].x1 : null,
        // Prose, judged without its formulas: a display equation (all
        // formula, maybe a word or two of operator names) stays original,
        // and so does an author line.
        translate: isTranslatable(plain) && !isNameList(src),
      });
    }
  }
  return out;
}
