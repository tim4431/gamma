// Where Markdown source is code: its fences and its code spans. The one
// reading of them that the keys in a note (editor/fences.js), a note's paste
// (editor/pasteMath.js), the editor's protected spans, inline-code marks and
// formulas (editor/mdScan.js, editor/mdMarks.js, editor/latexCompletion.js)
// and the chat's math (diagramMarkdown.normalizeChatMarkdown) share, so a span
// one of them takes for code is code to all, and to the renderer.
// backend/gamma/paste_math.py is the twin of codeFences and codeSpans;
// tests/shared/codefences.json and tests/shared/pastemath.json pin both.
// Pure: no DOM, no React.

// What a line can start, once its containers are read.
const FENCE_OPEN = /^(`{3,}|~{3,})([^\n]*)$/;
const FENCE_CLOSE = /^(`{3,}|~{3,})[ \t]*$/;
const LIST_MARKER = /^(?:[-+*]|\d{1,9}[.)])(?=[ \t]|$)/;
const FOOTNOTE = /^\[\^[^\][ \t\r\n]+\]:/;
const DIVIDER = /^([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const HEADING = /^#{1,6}(?:[ \t]|$)/;

// A quote on the stack of containers; a list item is its width in columns, a
// footnote's definition 4. Past this many on one line the rest of it is text,
// which keeps a line of nothing but markers from costing its length squared.
const QUOTE = 0;
const MAX_CONTAINERS = 32;

// The columns of indent from where the line has been read to, moving past it:
// `col` is the column at index `i`, tabs going to the next stop of 4, and `at`
// the column the innermost container's text starts at.
function indentOf(r) {
  while (r.line[r.i] === " " || r.line[r.i] === "\t") {
    r.col += r.line[r.i] === "\t" ? 4 - (r.col % 4) : 1;
    r.i++;
  }
  return r.col - r.at;
}

// Past a ">" and the one column after it that belongs to the mark.
function enterQuote(r) {
  r.i++;
  r.at = ++r.col;
  if (r.line[r.i] === " ") {
    r.i++;
    r.col++;
    r.at++;
  } else if (r.line[r.i] === "\t") r.at++;
}

// Past a list marker of `length` characters, `indent` columns in. The item's
// text starts behind the spaces after it, or one column on where there is none
// or they make it indented code. The item's width.
function enterItem(r, indent, length) {
  r.i += length;
  r.col += length;
  const [i, col] = [r.i, r.col];
  r.at = col;
  const spaces = indentOf(r);
  const pad = r.i >= r.line.length || spaces > 4 ? 1 : spaces;
  if (spaces > 4) [r.i, r.col] = [i, col];
  r.at = col + pad;
  return indent + length + pad;
}

// Past a footnote definition's label of `length` characters: its first line's
// text starts behind the spaces after it.
function enterNote(r, length) {
  r.i += length;
  r.col += length;
  r.at = r.col;
  indentOf(r);
  r.at = r.col;
}

// The last note's reading, which a keystroke asks for a dozen times.
let lastRead = { text: null, fences: [] };

// The code fences in `text`, in order, as the renderer reads them: with a stack
// of the quotes, list items and footnotes open at each line, so a fence holds
// what its container holds and goes with it, and a "> ```" in a plain fence is
// code. Each: `from` the opening line's start, `inner` its end, `to` the closing
// line's start, `mark` where its mark starts and `end` its end (all three where
// the code ends while it is open, `closed: false`: the text's end, or the last
// line its container held), with the opening line's `prefix` (quote marks, list
// marker, indent), `ticks` and `info` string, and `cont`, what a new line of its
// code starts with. A backtick line with a backtick in its info string opens
// none. HTML blocks, $$ blocks and indented code are not read, and a list starts
// wherever a marker stands, also where a paragraph would take it as text.
export function codeFences(text) {
  const doc = text || "";
  if (!doc.includes("```") && !doc.includes("~~~")) return [];
  if (doc === lastRead.text) return lastRead.fences;
  const out = [], stack = [];
  let open = null, paragraph = false, off = 0;
  const finish = (to, mark, end, closed) => {
    out.push({ ...open, to, mark, end, closed });
    open = null;
  };
  for (const raw of doc.split("\n")) {
    const line = raw.replace(/\r$/, "");
    const r = { line, i: 0, col: 0, at: 0 };
    // The containers this line is in; a blank one stays in a list item.
    let kept = 0;
    for (; kept < stack.length; kept++) {
      const indent = indentOf(r);
      if (stack[kept] === QUOTE) {
        if (indent > 3 || line[r.i] !== ">") break;
        enterQuote(r);
      } else if (r.i < line.length) {
        if (indent < stack[kept]) break;
        r.at += stack[kept];
      }
    }
    if (open) {
      if (kept === stack.length) {
        const close = indentOf(r) < 4 && FENCE_CLOSE.exec(line.slice(r.i));
        if (close && close[1][0] === open.ticks[0] && close[1].length >= open.ticks.length) {
          finish(off, off + r.i, off + raw.length, true);
        }
        off += raw.length + 1;
        continue;
      }
      finish(off - 1, off - 1, off - 1, false);
    }
    // The containers it opens.
    const enter = (width) => {
      stack.length = kept++;
      stack.push(width);
      paragraph = false;
    };
    let indent = indentOf(r), rest = line.slice(r.i);
    while (rest && indent < 4 && kept < MAX_CONTAINERS) {
      const marker = !DIVIDER.test(rest) && LIST_MARKER.exec(rest);
      const note = FOOTNOTE.exec(rest);
      if (rest[0] === ">") {
        enterQuote(r);
        enter(QUOTE);
      } else if (marker) {
        const at = r.at, width = enterItem(r, indent, marker[0].length);
        r.at = at + width;
        enter(width);
      } else if (note) {
        enterNote(r, note[0].length);
        enter(4);
      } else break;
      indent = indentOf(r);
      rest = line.slice(r.i);
    }
    // What the line itself is. A line of text with a paragraph open stays in the
    // containers it left (a lazy line); anything else leaves them.
    const fence = indent < 4 && FENCE_OPEN.exec(rest);
    const opens = fence && !(fence[1][0] === "`" && fence[2].includes("`"));
    const starts = opens || (indent < 4 && (HEADING.test(rest) || DIVIDER.test(rest)));
    if (!rest || starts || !paragraph) stack.length = kept;
    if (opens) {
      const prefix = line.slice(0, r.i);
      open = { from: off, inner: off + raw.length, prefix, ticks: fence[1], info: fence[2],
        cont: prefix.replace(/\[\^.*\]:[ \t]*/, "    ").replace(/[^> \t]/g, " ") };
    }
    paragraph = Boolean(rest) && !starts && (paragraph || indent < 4);
    off += raw.length + 1;
  }
  if (open) finish(doc.length, doc.length, doc.length, false);
  lastRead = { text: doc, fences: out };
  return out;
}

// The inline code spans in `text`, [from, to) in order, by CommonMark's
// rule kept to one line: a run of backticks opens a span that the next run
// of the same length closes ("``a`b``" is one span). A backslash escapes an
// opener's first backtick ("\`x`" is no code, "\``x`" is code after a
// literal backtick), never a closer's: inside code a backslash is literal.
// `besideMath` reads them as the renderer does beside formulas, whichever
// starts first holding the other: a "$" with a closer on its line takes the
// backticks up to it, and a span the dollars in it.
export function codeSpans(text, besideMath = false) {
  const spans = [];
  const marks = besideMath ? /`+|\$\$?/g : /`+/g;
  let off = 0;
  for (const line of (text || "").split("\n")) {
    const runs = [...line.matchAll(marks)].map((m) => [m.index, m[0]]);
    // Where each mark's runs are, and how far a search for one has got.
    const byMark = new Map(), seen = new Map();
    runs.forEach(([, mark], k) => {
      const list = byMark.get(mark);
      if (list) list.push(k); else byMark.set(mark, [k]);
    });
    let floor = 0;
    for (let k = 0; k < runs.length; k++) {
      let [at, mark] = runs[k];
      const dollar = mark[0] === "$";
      let slashes = 0;
      while (at - slashes - 1 >= floor && line[at - slashes - 1] === "\\") slashes++;
      if (slashes % 2) {
        if (dollar) continue;
        at++;
        mark = mark.slice(1);
      }
      const closers = byMark.get(mark) || [];
      let i = seen.get(mark) || 0;
      while (i < closers.length && closers[i] <= k) i++;
      seen.set(mark, i);
      if (!mark || i >= closers.length) continue;
      const [closeAt] = runs[closers[i]];
      if (!dollar) spans.push([off + at, off + closeAt + mark.length]);
      k = closers[i];
      floor = closeAt + mark.length;
    }
    off += line.length + 1;
  }
  return spans;
}

// The fences a formula leaves: a line of ~~~ inside a $$ is TeX, and whatever
// opens in a formula is none to the math readers.
function fencesBesideMath(text) {
  const inSpan = codeTest(text, false), kept = [];
  let open = false, pos = 0;
  for (const f of codeFences(text)) {
    for (const m of text.slice(pos, f.from).matchAll(/\$\$/g)) if (!inSpan(pos + m.index)) open = !open;
    pos = open ? f.from : f.end;
    if (!open) kept.push([f.from, f.end]);
  }
  return kept;
}

// A test of whether a position in `text` is code: in a code span beside the
// formulas (codeSpans) or, with `fences`, in a fence outside them, its delimiters
// too. Asked in ascending order, as math reads its dollars, and read at the
// first asking.
export function codeTest(text, fences = true) {
  if (!text || !(text.includes("`") || text.includes("~~~"))) return () => false;
  let ranges = null, k = 0;
  return (pos) => {
    ranges ??= [...(fences ? fencesBesideMath(text) : []), ...codeSpans(text, true)]
      .sort((a, b) => a[0] - b[0]);
    while (k < ranges.length && ranges[k][1] <= pos) k++;
    return k < ranges.length && ranges[k][0] <= pos;
  };
}

// `text` with `fn` applied to each stretch outside its fences and code spans
// (empty stretches too), the code kept as written.
export function mapProse(text, fn) {
  let out = "", pos = 0;
  for (const f of codeFences(text)) {
    out += outsideSpans(text.slice(pos, f.from), fn) + text.slice(f.from, f.end);
    pos = f.end;
  }
  return out + outsideSpans((text || "").slice(pos), fn);
}

function outsideSpans(s, fn) {
  let out = "", pos = 0;
  for (const [from, to] of codeSpans(s)) {
    out += fn(s.slice(pos, from)) + s.slice(from, to);
    pos = to;
  }
  return out + fn(s.slice(pos));
}
