// A note's change shown in the note itself, as tracked changes (Overleaf's
// review, git's word diff in place): what went is struck out where it stood
// and what came is tinted, rendered the way the note renders, formulas,
// code, lists and tables included. chat/noteReview.js says which changes a
// note shows; BlockTree's BlockRow shows them.
//
// `diffMarkdown(before, after)` merges the two texts into one Markdown
// source with marks around what changed, and BlockMarkdown renders it with
// `rehypeDiffMarks`, which turns the marks into <del class="mdDiff"> and
// <ins class="mdDiff">, and a code block or a display formula into a
// <div class="mdDiffBlock del|ins"> around it. Pure: no DOM, no React.
//
// The texts are compared line by line first, as git compares them, a code
// fence, a formula across lines and a table each one unit. A changed line
// that pairs with a new line of the same shape (a list item with a list
// item, a heading with a heading of its level) is compared word by word,
// each formula, code span, link, picture, [[ref]], mention and HTML tag one
// word, and each CJK character one, as the approval card's diff reads them
// (ai_tools.text_diff). The merged source keeps the new text's structure.
// A word that went goes in without the emphasis marks at its ends or its
// HTML tags, its other Markdown punctuation escaped, so it can neither
// close what the new text opened nor open anything of its own; a line or a
// unit that went stands whole where it stood.
//
// The marks are four characters of the Supplemental Punctuation block,
// which no note holds (any one is dropped from both texts first). Being
// punctuation, an emphasis run beside one parses as beside a comma. They
// never go among a line's leading markers (quotes, list items, a task box,
// a callout's type, a heading's hashes), only inside a formula's dollars
// and a fence's code, so every construct parses as it does without them.
import { codeFences, codeSpans } from "../shared/lib/mdCode.js";
import { diffIndices } from "../shared/lib/wordDiff.js";
import { scanMathSpans, scanTables } from "./mdScan.js";
import { scanMentions } from "./mentions.js";

export const INS = "⸂";
export const INS_END = "⸃";
export const DEL = "⸄";
export const DEL_END = "⸅";
const MARKS = /[⸂-⸅]/g;
const OPEN = { ins: INS, del: DEL };
const CLOSE = { ins: INS_END, del: DEL_END };

// ---- units: lines, and the constructs that span lines ----------------------

// A line's leading markers: its indent, quotes and list items, a task box,
// a callout's type and a heading's hashes.
const PREFIX = /^(?:[ \t]*(?:>[ \t]?|(?:[-+*]|\d{1,9}[.)])(?=[ \t]|$)[ \t]*))*[ \t]*(?:\[[ xX]\][ \t]+)?(?:\[![\w-]+\][+-]?[ \t]*)?(?:#{1,6}(?=[ \t]|$)[ \t]*)?/;
// A divider or a heading's underline: nothing to strike out.
const RULE = /^[ \t]*(?:>[ \t]?)*[ \t]*(?:([-*_])(?:[ \t]*\1){2,}|=+)[ \t]*$/;
// What pairs with what: a numbered item with any numbered item, whatever
// the spacing. A task ticked or unticked is two lines, the old box struck
// out over the new one, since its words alone did not change.
const shapeOf = (prefix) => prefix.replace(/\d{1,9}([.)])/g, "1$1").replace(/\[[xX]\]/g, "[x]").replace(/[ \t]+/g, " ");

function lineUnit(text) {
  if (!text.trim()) return { kind: "blank", text };
  if (RULE.test(text)) return { kind: "rule", text };
  const prefix = PREFIX.exec(text)[0];
  const rest = text.slice(prefix.length);
  // A formula alone on its line keeps its line: never compared word by word.
  const math = scanMathSpans(rest);
  const lone = math.length === 1 && math[0].display && math[0].from === 0 && !rest.slice(math[0].to).trim();
  return { kind: lone ? "formula" : "line", text, prefix, rest, shape: shapeOf(prefix) };
}

// The text as units, in order: a line each, but a code fence, a formula
// across lines and a table (with the rest of the lines they start and end
// on) one unit, its constructs' offsets in `spans`.
function unitsOf(text) {
  const lines = text.split("\n");
  const starts = [];
  let off = 0;
  for (const line of lines) { starts.push(off); off += line.length + 1; }
  const lineOf = (pos) => {
    let lo = 0, hi = lines.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (starts[mid] <= pos) lo = mid; else hi = mid - 1;
    }
    return lo;
  };
  const spans = [
    ...codeFences(text).map((f) => ({ kind: "fence", from: f.from, to: f.end, inner: f.inner, body: f.to })),
    ...scanMathSpans(text).filter((s) => s.display && text.slice(s.from, s.to).includes("\n"))
      .map((s) => ({ kind: "math", from: s.from, to: s.to })),
    ...scanTables(text).map((s) => ({ kind: "table", from: s.from, to: s.to })),
  ].sort((a, b) => a.from - b.from);
  const groups = [];
  for (const s of spans) {
    const first = lineOf(s.from), last = lineOf(Math.max(s.from, s.to - 1));
    const g = groups[groups.length - 1];
    if (g && first <= g.last) {
      g.last = Math.max(g.last, last);
      g.spans.push(s);
    } else groups.push({ first, last, spans: [s] });
  }
  const units = [];
  for (let i = 0, gi = 0; i < lines.length;) {
    const g = groups[gi];
    if (g && g.first === i) {
      const at = starts[i];
      const shift = (n) => (n == null ? n : n - at);
      units.push({ kind: "unit", text: lines.slice(i, g.last + 1).join("\n"),
        spans: g.spans.map((s) => ({ ...s, from: s.from - at, to: s.to - at, inner: shift(s.inner), body: shift(s.body) })) });
      i = g.last + 1;
      gi++;
    } else units.push(lineUnit(lines[i++]));
  }
  return units;
}

// ---- words -----------------------------------------------------------------

const CJK = "\\u2e80-\\u9fff\\uac00-\\ud7af\\uf900-\\ufaff\\uff00-\\uffef";
// A [[ref]] or ![[embed]], a link or picture (with Logseq's size), an HTML tag.
const ATOM_RE = /!?\[\[[^[\]\n]+\]\]|!?\[[^\]\n]*\]\([^)\n]*\)(?:\{:width\s+\d+\})?|<\/?[A-Za-z][^<>\n]*>/y;
const SPACE_RE = /\s+/y;
const CJK_RE = new RegExp(`[${CJK}]`, "y");
const WORD_RE = new RegExp(`[^\\s${CJK}[!<]+|[[!<]`, "y");

// A line's words: {t, k}, `k` "space", "math", "code", "mention", "atom"
// (a ref, link or picture), "tag" (HTML), "cjk" or "word".
export function wordsOf(line) {
  const spans = [
    ...scanMathSpans(line).map((s) => ({ from: s.from, to: s.to, k: "math" })),
    ...codeSpans(line).map(([from, to]) => ({ from, to, k: "code" })),
    ...scanMentions(line).map((m) => ({ from: m.from, to: m.to, k: "mention" })),
  ].sort((a, b) => a.from - b.from);
  const out = [];
  let pos = 0, si = 0;
  while (pos < line.length) {
    while (si < spans.length && spans[si].from < pos) si++;
    const span = spans[si];
    if (span && span.from === pos) {
      out.push({ t: line.slice(span.from, span.to), k: span.k });
      pos = span.to;
      continue;
    }
    ATOM_RE.lastIndex = pos;
    const atom = ATOM_RE.exec(line);
    if (atom) {
      out.push({ t: atom[0], k: atom[0][0] === "<" ? "tag" : "atom" });
      pos += atom[0].length;
      continue;
    }
    const limit = span ? span.from : line.length;
    let word = null, k = "word";
    for (const [re, kind] of [[SPACE_RE, "space"], [CJK_RE, "cjk"], [WORD_RE, "word"]]) {
      re.lastIndex = pos;
      const m = re.exec(line);
      if (m) { word = m[0].slice(0, limit - pos); k = kind; break; }
    }
    word ||= line[pos];
    out.push({ t: word, k });
    pos += word.length;
  }
  return out;
}

// How alike two lines read: the share of their words they have in common.
function likeness(a, b) {
  const wa = a.filter((w) => w.k !== "space"), wb = b.filter((w) => w.k !== "space");
  if (!wa.length || !wb.length) return 0;
  const left = new Map();
  for (const w of wa) left.set(w.t, (left.get(w.t) || 0) + 1);
  let common = 0;
  for (const w of wb) {
    const n = left.get(w.t);
    if (n) { common++; left.set(w.t, n - 1); }
  }
  return (2 * common) / (wa.length + wb.length);
}

// ---- writing the marks -----------------------------------------------------

// Emphasis marks at a word's ends (inside its quotes and brackets, outside
// its stops), and the punctuation Markdown reads, escaped.
const EDGE_OPEN = /^([(["'“‘«]*)[*_~=]+/;
const EDGE_CLOSE = /[*_~=]+([)\]"'”’».,;:!?]*)$/;
const URL_RE = /^https?:\/\/\S/i;
const URL_TAIL = /[.,;:!?)\]'"]+$/;

// A web address in angle brackets, so it links beside a mark: GFM links a
// bare one only after a space or the line's start, and runs it into the
// next mark.
function linked(word) {
  if (!URL_RE.test(word)) return word;
  const tail = URL_TAIL.exec(word)?.[0] || "";
  return `<${word.slice(0, word.length - tail.length)}>${tail}`;
}

// A word that went, inside a line the new text keeps: plain text.
function plainWord(word) {
  if (URL_RE.test(word)) return linked(word);
  return word.replace(EDGE_OPEN, "$1").replace(EDGE_CLOSE, "$1").replace(/[\\`*_~=$[\]<>]/g, "\\$&");
}

// A formula with its marks inside its dollars.
function markFormula(text, kind) {
  const d = text.startsWith("$$") ? 2 : 1;
  return text.slice(0, d) + OPEN[kind] + text.slice(d, text.length - d) + CLOSE[kind] + text.slice(text.length - d);
}

// Words that went (`del`) or came (`ins`), marked. A formula carries its
// marks inside its dollars; the spaces at either end stay outside the marks.
// `inline`: a stretch that went from a line the new text keeps, whose words
// become plain text and whose HTML tags go.
function markWords(words, kind, inline = false) {
  let out = "", run = [];
  const flush = () => {
    let a = 0, b = run.length;
    while (a < b && run[a].k === "space") a++;
    while (b > a && run[b - 1].k === "space") b--;
    const text = (from, to) => run.slice(from, to).map((w) => w.t).join("");
    out += text(0, a) + (b > a ? OPEN[kind] + text(a, b) + CLOSE[kind] : "") + text(b, run.length);
    run = [];
  };
  for (const w of words) {
    if (w.k === "math") {
      flush();
      out += markFormula(w.t, kind);
      continue;
    }
    if (inline && kind === "del" && w.k === "tag") continue;
    const t = w.k !== "word" ? w.t : inline && kind === "del" ? plainWord(w.t) : linked(w.t);
    if (t) run.push({ t, k: w.k });
  }
  flush();
  return out;
}

const size = (words) => words.reduce((n, w) => n + w.t.length, 0);

// Two lines that pair, word by word. A stretch both kept between two
// changes is folded into them when it is only space or no longer than the
// changes on either side, so a rewritten phrase reads as one change
// ("⸄The cat sat⸅⸂A dog ran⸃", not each word apart).
function mergeLine(a, b) {
  const groups = [];
  for (const [tag, i, j] of diffIndices(a.map((w) => w.t), b.map((w) => w.t))) {
    const same = tag === "same";
    let g = groups[groups.length - 1];
    if (!g || g.same !== same) groups.push(g = same ? { same, words: [] } : { same, del: [], ins: [] });
    if (same) g.words.push(b[j]);
    else if (tag === "del") g.del.push(a[i]);
    else g.ins.push(b[j]);
  }
  for (let k = 1; k + 1 < groups.length;) {
    const [left, kept, right] = [groups[k - 1], groups[k], groups[k + 1]];
    const length = kept.same ? size(kept.words) : 0;
    const fold = kept.same && !left.same && !right.same && (kept.words.every((w) => w.k === "space")
      || (length <= Math.max(size(left.del), size(left.ins)) && length <= Math.max(size(right.del), size(right.ins))));
    if (!fold) { k++; continue; }
    groups.splice(k - 1, 3, {
      same: false, del: [...left.del, ...kept.words, ...right.del], ins: [...left.ins, ...kept.words, ...right.ins],
    });
    k = Math.max(1, k - 1);
  }
  return groups.map((g) => {
    if (g.same) return g.words.map((w) => w.t).join("");
    // Space alone that went leaves nothing to show.
    const del = g.del.some((w) => w.k !== "space") ? markWords(g.del, "del", true) : "";
    const ins = markWords(g.ins, "ins");
    // A formula that went right before one that came: "$a$$b$" would read
    // as one "$$".
    return del + (del.endsWith("$") && ins.startsWith("$") ? " " : "") + ins;
  }).join("");
}

// A unit that went or came, with its marks inside: in a fence, on its
// first line of code past the quote and list indent; in a formula, inside
// its dollars (or around its lines, when text shares them); in a table,
// from its first cell to its last.
function markUnit(unit, kind) {
  let text = unit.text;
  const at = [];
  for (const s of unit.spans) {
    if (s.kind === "fence") {
      // Its first line of code that is not blank: a mark at the start of a
      // blank line would end the list item the fence is in. A fence with
      // none has nowhere to mark.
      for (let line = s.inner + 1; line < s.body;) {
        let pos = line;
        while (pos < s.body && /[ \t>]/.test(text[pos])) pos++;
        if (pos < s.body && text[pos] !== "\n") {
          at.push([pos, OPEN[kind]]);
          break;
        }
        const next = text.indexOf("\n", pos);
        if (next < 0) break;
        line = next + 1;
      }
    } else if (s.kind === "math") {
      const before = text.slice(text.lastIndexOf("\n", s.from - 1) + 1, s.from);
      const end = text.indexOf("\n", s.to);
      const after = text.slice(s.to, end < 0 ? text.length : end);
      if (/^[ \t>]*$/.test(before) && !after.trim()) {
        // On the formula's own lines, past a quote's marks: a mark on the
        // line of its $$ would be read as the fence's info and dropped.
        let open = s.from + 2, close = s.to - 2;
        while (open < close && /[ \t\n>]/.test(text[open])) open++;
        while (close > open && /[ \t\n>]/.test(text[close - 1])) close--;
        at.push([open, OPEN[kind]], [close, CLOSE[kind]]);
      } else {
        const prefix = PREFIX.exec(text)[0].length;
        at.push([prefix, OPEN[kind]], [text.length, CLOSE[kind]]);
      }
    } else if (s.kind === "table") {
      const head = /^[ \t>]*\|?[ \t]*/.exec(text.slice(s.from))[0].length;
      let end = s.to;
      while (end > s.from && /[ \t]/.test(text[end - 1])) end--;
      if (text[end - 1] === "|" && text[end - 2] !== "\\") end--;
      while (end > s.from && /[ \t]/.test(text[end - 1])) end--;
      at.push([s.from + head, OPEN[kind]], [Math.max(end, s.from + head), CLOSE[kind]]);
    }
  }
  at.sort((x, y) => y[0] - x[0]);
  for (const [pos, mark] of at) text = text.slice(0, pos) + mark + text.slice(pos);
  return text;
}

// ---- the merge ---------------------------------------------------------------

const pairable = (x, y) => x.kind === "line" && y.kind === "line" && x.shape === y.shape;
const LIKE = 0.3; // the share of words two lines of a larger change need in common to pair

// Which lines of a change pair, as [index in `gone`, index in `came`], in
// order: the one line on each side when that is all it changed, else the
// most alike lines that are alike enough.
function pairsOf(gone, came, words) {
  const lineGone = gone.filter((u) => u.kind === "line"), lineCame = came.filter((u) => u.kind === "line");
  if (lineGone.length === 1 && lineCame.length === 1) {
    return pairable(lineGone[0], lineCame[0]) ? [[gone.indexOf(lineGone[0]), came.indexOf(lineCame[0])]] : [];
  }
  const n = gone.length, m = came.length;
  if (n * m > 10_000) return [];
  const like = (i, j) => (pairable(gone[i], came[j]) ? likeness(words(gone[i]), words(came[j])) : 0);
  const best = Array.from({ length: n + 1 }, () => new Float64Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      const w = like(i, j);
      best[i][j] = Math.max(best[i + 1][j], best[i][j + 1], w >= LIKE ? w + best[i + 1][j + 1] : 0);
    }
  }
  const pairs = [];
  for (let i = 0, j = 0; i < n && j < m;) {
    const w = like(i, j);
    if (w >= LIKE && best[i][j] === w + best[i + 1][j + 1]) { pairs.push([i, j]); i++; j++; }
    else if (best[i][j] === best[i + 1][j]) i++;
    else j++;
  }
  return pairs;
}

// One changed stretch of units, in order: each pair where it falls, what
// went before what came.
function changeItems(gone, came, words) {
  const items = [];
  let i = 0, j = 0;
  for (const [pi, pj] of pairsOf(gone, came, words)) {
    while (i < pi) items.push({ op: "del", unit: gone[i++] });
    while (j < pj) items.push({ op: "ins", unit: came[j++] });
    items.push({ op: "pair", old: gone[i++], unit: came[j++] });
  }
  while (i < gone.length) items.push({ op: "del", unit: gone[i++] });
  while (j < came.length) items.push({ op: "ins", unit: came[j++] });
  return items;
}

function written(item, words) {
  const u = item.unit;
  if (item.op === "same") return u.text;
  if (item.op === "pair") return u.prefix + mergeLine(words(item.old), words(u));
  const kind = item.op;
  if (u.kind === "blank") return kind === "ins" ? u.text : "";
  if (u.kind === "rule") return kind === "ins" ? u.text : null;
  if (u.kind === "unit") return markUnit(u, kind);
  if (u.kind === "formula") return u.prefix + markFormula(u.rest.trimEnd(), kind);
  if (!u.rest.trim()) return kind === "ins" ? u.text : null;
  return u.prefix + markWords(words(u), kind);
}

// The two texts as one Markdown source with the change marked, or the
// text as it is when nothing changed.
export function diffMarkdown(before, after) {
  const clean = (s) => String(s || "").replace(/\r\n?/g, "\n").replace(MARKS, "");
  const a = unitsOf(clean(before)), b = unitsOf(clean(after));
  const cache = new Map();
  const words = (u) => {
    if (!cache.has(u)) cache.set(u, wordsOf(u.rest));
    return cache.get(u);
  };
  const items = [];
  const ops = diffIndices(a.map((u) => u.text), b.map((u) => u.text));
  for (let k = 0; k < ops.length;) {
    if (ops[k][0] === "same") {
      items.push({ op: "same", unit: b[ops[k][2]] });
      k++;
      continue;
    }
    const gone = [], came = [];
    for (; k < ops.length && ops[k][0] !== "same"; k++) {
      if (ops[k][0] === "del") gone.push(a[ops[k][1]]);
      else came.push(b[ops[k][2]]);
    }
    items.push(...changeItems(gone, came, words));
  }
  // A blank line that went stays only beside text that went, which it kept
  // apart from what stands around it. A table that went, or one beside text
  // that went, stands apart: the line after a table is one of its rows.
  const table = (x) => (x.op === "del" || x.op === "ins") && x.unit.kind === "unit"
    && x.unit.spans.some((s) => s.kind === "table");
  const out = [];
  let last = null;
  items.forEach((item, k) => {
    if (item.op === "del" && item.unit.kind === "blank") {
      const gone = (x) => x?.op === "del" && x.unit.kind !== "blank";
      if (gone(items[k - 1]) || gone(items[k + 1])) out.push("");
      last = null;
      return;
    }
    const text = written(item, words);
    if (text == null) return;
    if (last && text && out[out.length - 1] && (table(last) || table(item)) && (last.op === "del" || item.op === "del")) out.push("");
    out.push(text);
    last = text ? item : null;
  });
  return out.join("\n");
}

// Whether a merged source marks anything.
export const marksChange = (source) => /[⸂⸄]/.test(source || "");

// ---- rendering the marks -----------------------------------------------------

// Where a mark cannot reach past: a stray one (a construct read differently
// than its scan) ends with the block it is in.
const BLOCKS = new Set(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "table", "ul", "ol",
  "details", "summary", "div", "dl", "dt", "dd"]);
// Marked whole, never inside: a link (a chip or a card), a picture, inline code.
const WHOLE = new Set(["a", "img", "code"]);
const KIND = { [INS]: "ins", [DEL]: "del" };

const classes = (el) => (Array.isArray(el.properties?.className) ? el.properties.className : []);
const inlineMath = (el) => el.tagName === "code" && classes(el).includes("math-inline");

// The marks inside an element's text, taken out: the kinds they opened.
function unmark(node, kinds = new Set()) {
  if (node.type === "text") {
    for (const ch of node.value.match(MARKS) || []) if (KIND[ch]) kinds.add(KIND[ch]);
    node.value = node.value.replace(MARKS, "");
  }
  for (const child of node.children || []) unmark(child, kinds);
  return kinds;
}

const mark = (kind, children) => ({ type: "element", tagName: kind, properties: { className: ["mdDiff"] }, children });

// The rehype step that draws the marks, after the sanitizer (its classes
// are ours) and before KaTeX (which then sets each formula inside its mark).
export function rehypeDiffMarks() {
  return (tree) => {
    let kind = null;
    const text = (out, value) => {
      let buf = "";
      const flush = () => {
        if (!buf) return;
        const node = { type: "text", value: buf };
        out.push(kind ? mark(kind, [node]) : node);
        buf = "";
      };
      for (const ch of value) {
        if (ch === INS || ch === DEL) { flush(); kind = KIND[ch]; }
        else if (ch === INS_END || ch === DEL_END) { flush(); kind = null; }
        else buf += ch;
      }
      flush();
    };
    // A code block or display formula, and an inline formula: marked whole,
    // by the marks inside it or the stretch it stands in.
    const whole = (el, block) => {
      const kinds = unmark(el);
      const k = kinds.size > 1 ? "mixed" : kinds.size ? [...kinds][0] : kind;
      if (!k) return el;
      if (!block) return mark(k === "mixed" ? "ins" : k, [el]);
      return { type: "element", tagName: "div", properties: { className: ["mdDiffBlock", k] }, children: [el] };
    };
    const walk = (node) => {
      const out = [];
      for (const child of node.children || []) {
        if (child.type === "text") text(out, child.value);
        else if (child.type !== "element") out.push(child);
        else if (child.tagName === "pre") out.push(whole(child, true));
        else if (inlineMath(child)) out.push(whole(child, false));
        else if (WHOLE.has(child.tagName)) {
          unmark(child);
          out.push(kind ? mark(kind, [child]) : child);
        } else {
          const block = BLOCKS.has(child.tagName);
          if (block) kind = null;
          walk(child);
          if (block) kind = null;
          // A paragraph that held nothing but marks.
          const empty = child.tagName === "p" && child.children.every((c) => c.type === "text" && !c.value.trim());
          if (!empty) out.push(child);
        }
      }
      node.children = out;
    };
    walk(tree);
  };
}
