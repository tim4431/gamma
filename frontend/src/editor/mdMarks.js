// Inline marks: Markdown's (**bold**, *italic*, `code`, ~~strike~~,
// ==highlight==) and the two it has no syntax for, written as the inline
// HTML Obsidian and GitHub-flavored previews render: <u>underline</u> and
// <span style="color:…">colored text</span>. The ONE table both the block
// editor's live rendering and its formatting commands read, so "what counts
// as bold" is the same for the hidden-delimiter display and for Ctrl+B
// deciding between wrap and unwrap. Pure string functions, no CodeMirror —
// the editor adapts the results.
import { T } from "../shared/i18n/locales.js";

// A colored run: the text color and/or a background tint, as inline CSS. Its
// opening tag varies with the style, so the table's `marker` names the kind.
export const COLOR_MARK = "color";
const SPAN_OPEN = '<span style="';
const COLOR_SPAN_RE = /<span style="(?:(?:color|background(?:-color)?):[^";<>]+;?(?:\s*(?:color|background(?:-color)?):[^";<>]+;?)?)">((?:(?!<\/?span\b)[^\n])*)<\/span>/g;

// Matched in this order — code first (its content is literal), then the
// HTML pairs (their tags are unambiguous), italic last (most false-positive
// prone). A Markdown opener or closer may not hug whitespace, and a span
// never crosses a line break. Marks nest: "**a *b* c**" is bold with an
// italic inside, "*a **b** c*" the reverse, and "***x***" is bold+italic in
// one — its `layers` are what Ctrl+B / Ctrl+I peel off. `close` is the
// closing delimiter where it is not the opening `marker` again. Every
// pattern's first group is the span's content.
export const INLINE_MARKS = [
  { marker: "`", cls: "cmInlineCode", re: /`([^`\n]+)`/g },
  { marker: "<u>", close: "</u>", cls: "cmUnderline", re: /<u>((?:(?!<\/?u>)[^\n])+?)<\/u>/g },
  { marker: COLOR_MARK, close: "</span>", cls: "cmColorSpan", re: COLOR_SPAN_RE },
  { marker: "==", cls: "cmHighlight", re: /==(?!\s)([^=\n]+?)(?<!\s)==/g },
  { marker: "***", cls: "cmStrong cmEm", layers: ["**", "*"],
    re: /\*\*\*(?![\s*])([^*\n]+?)(?<![\s*])\*\*\*/g },
  { marker: "**", cls: "cmStrong", re: /\*\*(?![\s*])((?:[^*\n]|\*(?!\*))+?)(?<![\s*])\*\*/g },
  { marker: "~~", cls: "cmStrike", re: /~~(?!\s)([^~\n]+?)(?<!\s)~~/g },
  { marker: "*", cls: "cmEm", re: /(?<!\*)\*(?![\s*])((?:[^*\n]|\*\*)+?)(?<![\s*])\*(?!\*)/g },
];
const MARK_OF = new Map(INLINE_MARKS.map((m) => [m.marker, m]));
const closeOf = (marker) => MARK_OF.get(marker)?.close ?? marker;

// Every inline-mark span in text, outer spans before the spans nested in
// them: [{marker, cls, layers, from, to, openLen, closeLen, style}], from/to
// including the delimiters, `style` a colored run's CSS. `claimed`
// ([[from, to], …]) holds ranges already taken by higher-priority
// constructs (fences, math, refs); spans overlapping one are skipped and
// accepted spans are pushed onto it, so the caller's later scans (links)
// see them too. Two inline spans may overlap only by proper nesting — one
// sits inside the other's content — and nothing nests inside inline code.
export function scanMarks(text, claimed = []) {
  const spans = [];
  const overlaps = (from, to) => claimed.some(([a, b]) => from < b && to > a);
  const inside = (s, t) => t.marker !== "`" && s.from >= t.from + t.openLen && s.to <= t.to - t.closeLen;
  const nests = (s, t) => !(t.from < s.to && t.to > s.from) || inside(s, t) || inside(t, s);
  for (const mark of INLINE_MARKS) {
    const closeLen = (mark.close ?? mark.marker).length;
    for (const m of text.matchAll(mark.re)) {
      const from = m.index, to = m.index + m[0].length;
      const span = { marker: mark.marker, cls: mark.cls, layers: mark.layers, from, to,
        openLen: m[0].length - m[1].length - closeLen, closeLen };
      if (mark.marker === COLOR_MARK) span.style = m[0].slice(SPAN_OPEN.length, span.openLen - 2).trim();
      if (overlaps(from, to) || !spans.every((s) => nests(s, span))) continue;
      spans.push(span);
    }
  }
  for (const s of spans) claimed.push([s.from, s.to]);
  return spans.sort((a, b) => a.from - b.from || b.to - a.to);
}

// Whether a span carries `marker` — directly, or as one layer of "***".
const hasLayer = (mark, marker) =>
  mark.marker === marker || (mark.layers || []).includes(marker);

// The innermost span carrying `marker` whose range contains [from, to]
// (delimiters included, boundaries inclusive — the caret right after
// "**bold**" still counts).
function enclosingMark(spans, marker, from, to) {
  let best = null;
  for (const s of spans) {
    if (!hasLayer(s, marker) || s.from > from || to > s.to) continue;
    if (!best || s.from >= best.from) best = s;
  }
  return best;
}

// What toggling `marker` on [from, to] acts on, and whether the selection
// carries the mark already (`on`: the toggle takes it away):
//   {pair}            the caret sits in an empty pair, "**|**"
//   {span, at}        a span to unwrap: the caret in or at it, or the
//                     selection inside it
//   {at: [a, b]}      a range to wrap: the caret's, or the selection without
//                     the whitespace at its edges ("** word**" renders as
//                     raw asterisks, here and in every markdown viewer)
//   {lines, wrapped, at} a multi-line selection: each non-blank line and the
//                     span already around it, if any
// null when the selection is nothing but whitespace.
function markTarget(text, from, to, marker) {
  const close = closeOf(marker);
  const spans = scanMarks(text);
  if (from === to) {
    if (text.slice(from - marker.length, from) === marker && text.slice(from, from + close.length) === close) {
      return { pair: true, on: true };
    }
    const span = enclosingMark(spans, marker, from, from);
    return { span, at: [from, from], on: !!span };
  }
  const sel = text.slice(from, to);
  const a = from + sel.length - sel.trimStart().length, b = to - (sel.length - sel.trimEnd().length);
  if (a >= b) return null;
  if (text.slice(a, b).includes("\n")) {
    const lines = [];
    let pos = a;
    for (const raw of text.slice(a, b).split("\n")) {
      const lead = raw.length - raw.trimStart().length;
      const trail = raw.length - raw.trimEnd().length;
      if (lead + trail < raw.length) lines.push({ from: pos + lead, to: pos + raw.length - trail });
      pos += raw.length + 1;
    }
    const wrapped = lines.map((ln) => enclosingMark(spans, marker, ln.from, ln.to));
    return { lines, wrapped, at: [a, b], on: wrapped.every(Boolean) };
  }
  const span = enclosingMark(spans, marker, a, b);
  return { span, at: [a, b], on: !!span };
}

// Whether the selection [from, to] carries `marker`: what its toggle would
// take away, and what the selection toolbar shows pressed.
export const markedAt = (text, from, to, marker) => !!markTarget(text, from, to, marker)?.on;

// Toggle `marker` on the selection [from, to] of text, Obsidian-style.
// Returns {changes: [{from, to, insert}], selection: {anchor, head}} — the
// changes address the ORIGINAL text (they don't overlap), the selection the
// text after them — or null when there's nothing sensible to do.
//
//   caret in an empty pair  "**|**"        → pair removed
//   caret in / at a span    "**bo|ld**"    → span unwrapped, caret kept
//   caret elsewhere         "wo|rd"        → "wo**|**rd"
//   selection in a span     "**[bold]**"   → unwrapped, text stays selected
//   selection               "[word]"       → "**[word]**"  (inner selected,
//                                            surrounding whitespace excluded)
//   multi-line selection    each non-blank line wrapped (or all unwrapped
//                           when every one already is)
export function toggleMark(text, from, to, marker) {
  const open = marker, close = closeOf(marker);
  const L = open.length + close.length;
  const target = markTarget(text, from, to, marker);
  if (!target) return null;
  if (target.pair) {
    return { changes: [{ from: from - open.length, to: from + close.length, insert: "" }], selection: { anchor: from - open.length } };
  }
  const [a, b] = target.at;
  // A multi-line selection: one span per non-blank line (a span can't cross
  // a line break). When every such line is already wrapped, unwrap them all.
  if (target.lines) {
    if (target.on) {
      return { changes: target.wrapped.flatMap((s) => unwrapChanges(s, open, close)),
        selection: { anchor: a, head: b - L * target.lines.length } };
    }
    const changes = [];
    target.lines.forEach((ln, i) => {
      if (target.wrapped[i]) return;           // already bold — leave it
      changes.push({ from: ln.from, to: ln.from, insert: open }, { from: ln.to, to: ln.to, insert: close });
    });
    return { changes, selection: { anchor: a, head: b + L * (changes.length / 2) } };
  }
  if (target.span) {
    // The selection maps into the unwrapped text; a position inside a
    // delimiter clamps to the content's edge.
    const s = target.span;
    const shift = (p) => Math.min(Math.max(p - open.length, s.from), s.to - L);
    return { changes: unwrapChanges(s, open, close), selection: { anchor: shift(a), head: shift(b) } };
  }
  if (a === b) return { changes: [{ from: a, to: a, insert: open + close }], selection: { anchor: a + open.length } };
  return {
    changes: [{ from: a, to: a, insert: open }, { from: b, to: b, insert: close }],
    selection: { anchor: a + open.length, head: b + open.length },
  };
}

// The delimiters `open` / `close` of a span deleted (one layer of a "***").
const unwrapChanges = (s, open, close) => [
  { from: s.from, to: s.from + open.length, insert: "" },
  { from: s.to - close.length, to: s.to, insert: "" },
];

// Ctrl+K: turn the selection into a markdown link. With a URL known (the
// clipboard's) the link is complete and the caret lands after it; otherwise
// the caret sits in the empty (…) slot ready for the address. No selection
// → "[|](url)" / "[|]()" with the caret in the text slot.
export function insertLink(text, from, to, url = "") {
  const label = text.slice(from, to);
  const insert = `[${label}](${url})`;
  let caret;
  if (!label) caret = from + 1;
  else if (url) caret = from + insert.length;
  else caret = from + 1 + label.length + 2;
  return { changes: [{ from, to, insert }], selection: { anchor: caret } };
}

export const isUrl = (s) => /^https?:\/\/\S+$/i.test((s || "").trim());

// Images: `![alt](url)`, sized Obsidian-style (`![alt|300](url)`) or with the
// legacy Logseq `{:width N}` suffix. `![[embeds]]` can't match — their "alt"
// holds an unclosed "[" and no "(". Every occurrence in text, in order:
// [{from, to, alt, url, width}] with from/to including the whole construct;
// callers that must skip code/math spans filter the result.
const IMG_RE = /!\[([^\]\n]*)\]\(([^)\n]+)\)(\{:width\s+(\d+)\})?/g;
const ALT_WIDTH_RE = /^(.*?)\|(\d+)(?:x\d+)?$/;
export function scanImageSyntax(text) {
  const out = [];
  for (const m of text.matchAll(IMG_RE)) {
    let alt = m[1], width = m[4] ? Number(m[4]) : null;
    const pipe = ALT_WIDTH_RE.exec(alt);
    if (pipe) {
      alt = pipe[1];
      if (width == null) width = Number(pipe[2]);
    }
    out.push({ from: m.index, to: m.index + m[0].length, alt, url: m[2], width });
  }
  return out;
}

// Blank lines the rendered view keeps. Markdown folds any run of blank lines
// into one paragraph break, so in a note "a / blank / blank / b" looked like
// "a / b" once the editor closed while the editor showed both empty rows.
// One blank line stays the paragraph break; every further one becomes an
// empty (`&nbsp;`) paragraph, i.e. a visible empty line. Whitespace-only
// lines count as blank. Callers keep code and math spans out of it.
export function expandBlankLines(text) {
  return text.replace(/\n(?:[ \t]*\n){2,}/g, (run) => {
    const breaks = run.split("\n").length - 1;
    return "\n\n" + "&nbsp;\n\n".repeat(breaks - 2);
  });
}

// Colored text, the Obsidian way: markdown has no color syntax, so a colored
// run is inline HTML — `<span style="color:#e5484d">text</span>` or a tinted
// `<span style="background:#fde04766">text</span>` — which every markdown
// renderer that allows HTML (Obsidian, GitHub-flavored previews, this
// app's rendered view via rehype-raw) shows as intended and the rest show
// as plain text. The spans are inline marks (scanMarks, COLOR_MARK), so a
// run nests with bold, underline and the rest. TEXT_COLORS is the palette
// the selection toolbar and the "/" menu offer (Notion's set); the scanner
// accepts any color value, so hand-written spans work. A run with both a
// color and a tint carries them in one span.
export const TEXT_COLORS = [
  { name: "red", label: T("Red"), color: "#e5484d" }, { name: "orange", label: T("Orange"), color: "#f76b15" },
  { name: "yellow", label: T("Yellow"), color: "#d4a017" }, { name: "green", label: T("Green"), color: "#30a46c" },
  { name: "blue", label: T("Blue"), color: "#3b82f6" }, { name: "purple", label: T("Purple"), color: "#8e4ec6" },
  { name: "pink", label: T("Pink"), color: "#e93d82" }, { name: "gray", label: T("Gray"), color: "#8b8d98" },
];

// The background a palette color paints behind the text: the color, translucent.
export const tint = (color) => `${color}55`;

// A run's style, {color, background} with either absent, from its CSS and back.
function parseStyle(css) {
  const style = {};
  for (const part of String(css || "").split(";")) {
    const i = part.indexOf(":");
    if (i < 0) continue;
    const key = part.slice(0, i).trim().toLowerCase(), value = part.slice(i + 1).trim();
    if (key === "color") style.color = value;
    else if (key === "background" || key === "background-color") style.background = value;
  }
  return style;
}
const styleCss = (s) => [s.color && `color:${s.color}`, s.background && `background:${s.background}`].filter(Boolean).join("; ");
const sameStyle = (x, y) => x.color === y.color && x.background === y.background;

// The span markup for a color choice: text color, or a translucent
// background tint of the same hue.
export function colorSpan(color, background) {
  return `${SPAN_OPEN}${styleCss(background ? { background: tint(color) } : { color })}">`;
}

// [from, to] cut at its line breaks: a run never crosses one.
function lineRanges(text, from, to) {
  const out = [];
  for (let a = from; a < to;) {
    const nl = text.indexOf("\n", a);
    const b = nl < 0 || nl >= to ? to : nl;
    if (b > a) out.push([a, b]);
    a = b + 1;
  }
  return out;
}

// The runs a recolor of [a, b] (on one line) rewrites: the region [from,
// to] around [a, b] and the color spans touching it, as pieces of text with
// their style, cut at a and b ({from, to, style, picked}: the piece's text
// in the source, and whether the selection holds it). A selection edge
// inside a tag moves to the content's edge, and one that would cut another
// mark in two (half of a **bold**) moves out to take all of it, so a
// recolor never splits a mark's delimiters. Null when no text is selected.
function colorRuns(text, spans, a, b) {
  const colored = spans.filter((s) => s.marker === COLOR_MARK);
  for (const s of colored) {
    if (a > s.from && a < s.from + s.openLen) a = s.from + s.openLen;
    else if (a > s.to - s.closeLen && a < s.to) a = s.to;
    if (b > s.from && b < s.from + s.openLen) b = s.from;
    else if (b > s.to - s.closeLen && b < s.to) b = s.to - s.closeLen;
  }
  for (let grown = true; grown;) {
    grown = false;
    for (const s of spans) {
      if (s.marker === COLOR_MARK || !(s.from < b && a < s.to)) continue;
      const holds = s.from >= a && s.to <= b;
      const within = a >= s.from + s.openLen && b <= s.to - s.closeLen;
      if (holds || within) continue;
      a = Math.min(a, s.from);
      b = Math.max(b, s.to);
      grown = true;
    }
  }
  if (a >= b) return null;
  const region = colored.filter((s) => s.from <= b && a <= s.to);
  const from = Math.min(a, ...region.map((s) => s.from)), to = Math.max(b, ...region.map((s) => s.to));
  const pieces = [];
  const add = (x, y, style) => {
    const cuts = [x, ...[a, b].filter((p) => p > x && p < y), y];
    for (let i = 1; i < cuts.length; i++) {
      if (cuts[i] > cuts[i - 1]) pieces.push({ from: cuts[i - 1], to: cuts[i], style, picked: cuts[i - 1] >= a && cuts[i] <= b });
    }
  };
  let pos = from;
  for (const s of region) {
    add(pos, s.from, {});
    add(s.from + s.openLen, s.to - s.closeLen, parseStyle(s.style));
    pos = s.to;
  }
  add(pos, to, {});
  return { from, to, pieces };
}

// Pieces back to markup: neighbours of one style share a span, and a piece
// with no style is plain text. `selFrom` / `selTo`: where the picked text
// begins and ends in the markup.
function renderRuns(text, pieces) {
  let out = "", run = null, selFrom = null, selTo = null;
  const flush = () => {
    if (!run) return;
    const css = styleCss(run.style);
    const open = css ? `${SPAN_OPEN}${css}">` : "";
    if (run.selFrom != null) selFrom = out.length + open.length + run.selFrom;
    if (run.selTo != null) selTo = out.length + open.length + run.selTo;
    out += open + run.text + (css ? "</span>" : "");
    run = null;
  };
  for (const p of pieces) {
    if (!run || !sameStyle(run.style, p.style)) { flush(); run = { style: p.style, text: "" }; }
    if (p.picked && selFrom == null && run.selFrom == null) run.selFrom = run.text.length;
    run.text += text.slice(p.from, p.to);
    if (p.picked) run.selTo = run.text.length;
  }
  flush();
  return { insert: out, selFrom, selTo };
}

// The selection [from, to] recolored: `patch` sets `color` and/or
// `background` (a CSS color) or clears them (null) on every run of it,
// and the text around it keeps its own. Part of a colored run splits it,
// runs that end up alike merge, and a run left with no style loses its
// span. {changes, selection} as toggleMark returns, with the recolored text
// still selected; null when nothing changes.
export function recolor(text, from, to, patch) {
  const spans = scanMarks(text);
  const changes = [];
  let anchor = null, head = null, delta = 0;
  for (const [a, b] of lineRanges(text, from, to)) {
    const r = colorRuns(text, spans, a, b);
    if (!r) continue;
    for (const p of r.pieces) {
      if (!p.picked) continue;
      p.style = { ...p.style };
      for (const [key, value] of Object.entries(patch)) {
        if (value) p.style[key] = value;
        else delete p.style[key];
      }
    }
    const out = renderRuns(text, r.pieces);
    if (out.insert !== text.slice(r.from, r.to)) changes.push({ from: r.from, to: r.to, insert: out.insert });
    if (anchor == null) anchor = r.from + delta + out.selFrom;
    head = r.from + delta + out.selTo;
    delta += out.insert.length - (r.to - r.from);
  }
  return changes.length ? { changes, selection: { anchor, head } } : null;
}

// The style every run of the selection [from, to] shares — {color,
// background}, either absent; {} for plain text — or null when they
// differ. A caret reads the run it is in. What the selection toolbar's
// color row marks.
export function colorAt(text, from, to) {
  const spans = scanMarks(text);
  if (from === to) {
    const s = spans.filter((x) => x.marker === COLOR_MARK && x.from + x.openLen <= from && from <= x.to - x.closeLen).pop();
    return s ? parseStyle(s.style) : {};
  }
  let shared = null;
  for (const [a, b] of lineRanges(text, from, to)) {
    for (const p of colorRuns(text, spans, a, b)?.pieces || []) {
      if (!p.picked) continue;
      if (!shared) shared = p.style;
      else if (!sameStyle(shared, p.style)) return null;
    }
  }
  return shared || {};
}

// Toggle to-do (editor/blockCommands.js) on a line: "- [ ] " ↔ "- [x] "; a line without a checkbox gets
// one in front of its text (after a list marker it already has). Returns
// the new text and where the caret lands, moved by the inserted marker,
// plus the line's replacement ({from, to, insert}) for a minimal dispatch.
export function toggleTodoLine(text, pos) {
  const start = text.lastIndexOf("\n", pos - 1) + 1;
  const endNl = text.indexOf("\n", pos);
  const end = endNl < 0 ? text.length : endNl;
  const line = text.slice(start, end);
  const box = /^(\s*(?:[-*+]|\d+\.)\s+)\[([ xX])\]\s?/.exec(line);
  let next;
  if (box) {
    const checked = box[2] !== " ";
    next = `${box[1]}[${checked ? " " : "x"}]${line.slice(box[0].length - (/\s$/.test(box[0]) ? 1 : 0))}`;
  } else {
    const marker = /^(\s*)((?:[-*+]|\d+\.)\s+)?/.exec(line);
    next = `${marker[1]}${marker[2] || "- "}[ ] ${line.slice(marker[0].length)}`;
  }
  const delta = next.length - line.length;
  return {
    text: text.slice(0, start) + next + text.slice(end), pos: Math.max(start, pos + delta),
    from: start, to: end, insert: next,
  };
}
