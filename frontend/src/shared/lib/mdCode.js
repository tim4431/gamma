// Where Markdown source is code: its fences and its code spans. The one
// reading of them that a note's paste (editor/pasteMath.js), the editor's
// protected spans and inline-code marks (editor/mdScan.js, editor/mdMarks.js)
// and the chat's math (diagramMarkdown.normalizeChatMarkdown) share, so a
// span one of them takes for code is code to all. backend/gamma/paste_math.py
// is the twin of codeFences and codeSpans; tests/shared/pastemath.json pins
// both. Pure: no DOM, no React.

// A fence's opening line, with the quote/list prefix Markdown allows in front:
// [, prefix, ticks, info string]. Quote marks as (?:[ \t]*>)*, the space after
// one left to the next [ \t]*: an optional space of its own would backtrack
// exponentially on "> > > …".
export const FENCE_LINE = /^((?:[ \t]*>)*(?:[ \t]*(?:[-+*]|\d+[.)])[ \t]+)?[ \t]*)(`{3,}|~{3,})([^\r\n]*)/;

// The code fences in `text`, in order: `from` the opening line's start,
// `inner` its end, `to` the closing line's start and `end` its end (the
// text's end while it is open, `closed: false`), with the opening line's
// `prefix` (quote marks, list marker, indent), `ticks` and `info` string.
// Quoted, listed and ~~~ fences count; a backtick line with a backtick in
// its info string opens none.
export function codeFences(text) {
  const out = [];
  let open = null, off = 0;
  for (const line of (text || "").split("\n")) {
    const m = FENCE_LINE.exec(line);
    if (open) {
      if (m && m[2][0] === open.ticks[0] && m[2].length >= open.ticks.length && !m[3].trim()) {
        out.push({ ...open, to: off, end: off + line.length, closed: true });
        open = null;
      }
    } else if (m && !(m[2][0] === "`" && m[3].includes("`"))) {
      open = { from: off, inner: off + line.length, prefix: m[1], ticks: m[2], info: m[3] };
    }
    off += line.length + 1;
  }
  if (open) out.push({ ...open, to: (text || "").length, end: (text || "").length, closed: false });
  return out;
}

// The inline code spans in `text`, [from, to) in order, by CommonMark's
// rule kept to one line: a run of backticks opens a span that the next run
// of the same length closes ("``a`b``" is one span). A backslash escapes an
// opener's first backtick ("\`x`" is no code, "\``x`" is code after a
// literal backtick), never a closer's: inside code a backslash is literal.
export function codeSpans(text) {
  const spans = [];
  let off = 0;
  for (const line of (text || "").split("\n")) {
    const runs = [...line.matchAll(/`+/g)].map((m) => [m.index, m[0].length]);
    // Where each length's runs are, and how far a search for one has got.
    const byLength = new Map(), seen = new Map();
    runs.forEach(([, n], k) => {
      const list = byLength.get(n);
      if (list) list.push(k); else byLength.set(n, [k]);
    });
    let floor = 0;
    for (let k = 0; k < runs.length; k++) {
      let [at, n] = runs[k];
      let slashes = 0;
      while (at - slashes - 1 >= floor && line[at - slashes - 1] === "\\") slashes++;
      if (slashes % 2) [at, n] = [at + 1, n - 1];
      const closers = byLength.get(n) || [];
      let i = seen.get(n) || 0;
      while (i < closers.length && closers[i] <= k) i++;
      seen.set(n, i);
      if (!n || i >= closers.length) continue;
      const [closeAt] = runs[closers[i]];
      spans.push([off + at, off + closeAt + n]);
      k = closers[i];
      floor = closeAt + n;
    }
    off += line.length + 1;
  }
  return spans;
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
