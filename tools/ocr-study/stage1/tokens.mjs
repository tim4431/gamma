// Shared by dump_words.mjs and eval.mjs: the selection string of a text layer
// and its words (tokens), and where TextLayer draws each word's span.

// The text a reader selects from the layer: every string item in order, "\n"
// after an item with hasEOL (TextLayer appends a <br> there).
export function selection(items) {
  let s = "";
  const map = []; // per char: [itemIndex, charIndex] or null for the "\n"
  items.forEach((it, i) => {
    if (typeof it.str !== "string") return;
    for (let c = 0; c < it.str.length; c++) { s += it.str[c]; map.push([i, c]); }
    if (it.hasEOL) { s += "\n"; map.push(null); }
  });
  return { s, map };
}

// Words = maximal runs of non-whitespace in the selection string. Each word
// lists the item char ranges it is made of: parts [[item, c0, c1], ...].
// `eol` = a "\n" sits between this word and the previous one.
export function tokenize(items) {
  const { s, map } = selection(items);
  const toks = [];
  let cur = null, sawEol = false;
  for (let k = 0; k < s.length; k++) {
    const ch = s[k];
    if (/\s/.test(ch)) {
      if (cur) { toks.push(cur); cur = null; }
      if (ch === "\n") sawEol = true;
      continue;
    }
    const [i, c] = map[k];
    if (!cur) { cur = { t: "", parts: [], eol: sawEol }; sawEol = false; }
    cur.t += ch;
    const last = cur.parts[cur.parts.length - 1];
    if (last && last[0] === i && last[2] === c) last[2] = c + 1;
    else cur.parts.push([i, c, c + 1]);
  }
  if (cur) toks.push(cur);
  return { text: s, tokens: toks };
}

export const isRotated = (tr) => Math.abs(tr[1]) > Math.abs(tr[0]) * 0.05 || tr[0] < 0 || Math.abs(tr[2]) > Math.abs(tr[3]) * 0.05 + 1e-9;

// Glyph advances (1/1000 em, ASCII 32..126) of the faces the browser draws
// pdf.js's fallback families in: Times for serif, Helvetica/Arial for
// sans-serif; monospace is uniform. TextLayer stretches a span's natural text
// to the item width, so a word inside a multi-word item starts at the item's
// share of these advances.
const TIMES = [250,333,408,500,500,833,778,333,333,333,500,564,250,333,250,278,500,500,500,500,500,500,500,500,500,500,278,278,564,564,564,444,921,
  722,667,667,722,611,556,722,722,333,389,722,611,889,722,722,556,722,667,556,611,722,722,944,722,722,611,333,278,333,469,500,333,
  444,500,444,500,444,333,500,500,278,278,500,278,778,500,500,500,500,333,389,278,500,500,722,500,500,444,480,200,480,541];
const HELV = [278,278,355,556,556,889,667,222,333,333,389,584,278,333,278,278,556,556,556,556,556,556,556,556,556,556,278,278,584,584,584,556,1015,
  667,667,722,722,667,611,778,722,278,500,667,556,833,722,778,667,778,722,667,611,722,667,944,667,667,611,278,278,278,469,556,222,
  556,556,500,556,556,278,556,556,222,222,500,222,833,556,556,556,556,333,500,278,556,500,722,500,500,500,334,260,334,584];
function adv(ch, family) {
  if (family === "monospace") return 600;
  const tab = family === "sans-serif" ? HELV : TIMES;
  let c = ch.charCodeAt(0);
  if (c < 32 || c > 126) {
    const base = ch.normalize("NFD")[0];
    c = base ? base.charCodeAt(0) : 0;
    if (c < 32 || c > 126) return family === "sans-serif" ? 556 : 500;
  }
  return tab[c - 32];
}
// cumulative advance before each char index (length n + 1), as a 0..1 share
function shares(str, family) {
  const cum = [0];
  for (const ch of str) cum.push(cum[cum.length - 1] + adv(ch, family));
  // surrogate pairs aside, str.length === cum.length - 1
  const tot = cum[cum.length - 1] || 1;
  return cum.map((v) => v / tot);
}

// Where TextLayer draws an item, in PDF user space: x [e, e + width],
// y [f - (1-asc)h, f + asc h] with h = hypot of the matrix's second column
// (its sign ignored: the mirror bug). A word inside a multi-word item gets
// the share of the item from its first char up to the next word's first char
// (the item's end for the last word): spaces belong to the word before them.
export function wordSpans(items, tokens, asc, styles = {}) {
  // next word start per item
  const starts = new Map(); // item -> sorted char starts of words in it
  tokens.forEach((tk) => tk.parts.forEach(([i, c0]) => {
    if (!starts.has(i)) starts.set(i, []);
    starts.get(i).push(c0);
  }));
  for (const v of starts.values()) v.sort((a, b) => a - b);
  return tokens.map((tk) => {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity, rot = false;
    for (const [i, c0] of tk.parts) {
      const it = items[i];
      const tr = it.transform;
      if (isRotated(tr)) { rot = true; continue; }
      const h = Math.hypot(tr[2], tr[3]);
      const len = it.str.length || 1;
      const st = starts.get(i);
      const j = st.indexOf(c0);
      const first = j === 0;
      const end = j + 1 < st.length ? st[j + 1] : len;
      const a = first ? 0 : c0; // a word that opens the item takes its start
      const w = it.width;
      let sa = a / len, se = end / len;
      if (st.length > 1) {
        const sh = shares(it.str, styles[it.fontName]?.fontFamily);
        if (sh.length === len + 1) { sa = sh[a]; se = sh[end]; }
      }
      x0 = Math.min(x0, tr[4] + w * sa);
      x1 = Math.max(x1, tr[4] + w * se);
      y0 = Math.min(y0, tr[5] - (1 - asc) * h);
      y1 = Math.max(y1, tr[5] + asc * h);
    }
    if (!isFinite(x0)) return { rot: true };
    return { x0, x1, y0, y1, rot };
  });
}
