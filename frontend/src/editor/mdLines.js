// Line prefixes in a note's markdown: quote marks ("> ", nested "> > ") and
// the list marker after them. What the line-break Enter continues, what
// Toggle quote adds or removes (editor/blockCommands.js), and what a paste
// onto a quoted line gives every pasted line (BlockTree's paste handler).
// Every line of a quote carries its marks, a blank one a bare ">", so the
// quote stays one box here, in Obsidian and on GitHub. Pure string
// functions with no imports, like toggleTodoLine (mdMarks.js).

const QUOTE_RE = /^(?: {0,3}> ?)+/;
const LIST_RE = /^(\s*)([-*+] \[[ xX]\] |[-*+] |\d+\. )?/;
const CALLOUT_MARKER_RE = /^\[!\w+\][-+]?[ \t]*/;

function lineAt(text, pos) {
  const from = text.lastIndexOf("\n", pos - 1) + 1;
  const nl = text.indexOf("\n", pos);
  const to = nl < 0 ? text.length : nl;
  return { from, to, text: text.slice(from, to) };
}

// The quote marks of the caret's line ("> ", "> > ", ">"), or "" when the
// line is no quote or the caret sits before its marks.
export function quoteMarksAt(text, pos) {
  const line = lineAt(text, pos);
  const q = QUOTE_RE.exec(line.text)?.[0] || "";
  return q && pos - line.from >= q.trimEnd().length ? q : "";
}

// The line-break Enter at a caret (Obsidian-style): a quote or list line
// continues on the next line with the same marks, a checked box unchecked
// and a number counted on. On an empty item the innermost thing ends
// instead: "> - " keeps the quote and drops the list, "> > " drops a level,
// "> " or "- " leaves an empty line. {from, to, insert, pos} for one
// change and the caret after it, or null for a plain newline.
export function enterPlan(text, pos) {
  const line = lineAt(text, pos);
  const quote = QUOTE_RE.exec(line.text)?.[0] || "";
  const list = LIST_RE.exec(line.text.slice(quote.length));
  const marker = quote + (list[2] ? list[1] + list[2] : "");
  if (!marker || pos - line.from < marker.length) return null;
  if (line.text.trimEnd() === marker.trimEnd()) {
    const keep = list[2] ? quote : quote.replace(/> ?$/, "");
    return { from: line.from, to: line.to, insert: keep, pos: line.from + keep.length };
  }
  let item = list[2] ? list[2].replace(/\[[xX]\]/, "[ ]") : "";
  const num = /^(\d+)\. $/.exec(item);
  if (num) item = `${Number(num[1]) + 1}. `;
  const next = quote + (item ? list[1] + item : "");
  return { from: pos, to: pos, insert: "\n" + next, pos: pos + 1 + next.length };
}

// Pasted text that lands on a quoted line: every line after the first gets
// the line's quote marks, so a multi-line paste stays in the quote or
// callout. The text as given anywhere else.
export function quotePasted(text, pos, pasted) {
  const q = quoteMarksAt(text, pos);
  if (!q || !pasted.includes("\n")) return pasted;
  return pasted.split("\n").map((l, i) => (i === 0 ? l : l.trim() ? q + l : q.trimEnd())).join("\n");
}

// Toggle quote on the lines a selection [from, to] touches: when every
// line with text is quoted, one level comes off (and, off the last level,
// a callout's "[!type]" marker with it); otherwise each line gets "> ", a
// blank line a bare ">". A selection ending at a line's start leaves that
// line alone. {changes, selection} for one dispatch, the selection moved
// with the text.
export function toggleQuotePlan(text, from, to) {
  const start = lineAt(text, from).from;
  const end = lineAt(text, to > from && text[to - 1] === "\n" ? to - 1 : to).to;
  const lines = text.slice(start, end).split("\n");
  const unquote = lines.some((l) => l.trim()) && lines.every((l) => !l.trim() || l.startsWith(">"));
  const changes = [];
  let at = start;
  for (const l of lines) {
    if (unquote) {
      const q = /^> ?/.exec(l);
      if (q) {
        const marker = l.startsWith(">", q[0].length) ? null : CALLOUT_MARKER_RE.exec(l.slice(q[0].length));
        changes.push({ from: at, to: at + q[0].length + (marker ? marker[0].length : 0), insert: "" });
      }
    } else if (l.trim()) {
      changes.push({ from: at, to: at, insert: "> " });
    } else {
      changes.push({ from: at, to: at + l.length, insert: ">" });
    }
    at += l.length + 1;
  }
  // A position after the changes before it; one inside a removed range
  // lands where it was cut, one at an insertion goes after the inserted
  // text unless it starts a selection (which then takes the new marks).
  const map = (pos, before) => {
    let out = pos;
    for (const c of changes) {
      if (c.to < pos || (c.to === pos && !(before && c.from === c.to))) out += c.insert.length - (c.to - c.from);
      else if (c.from < pos) out -= pos - c.from;
    }
    return out;
  };
  const range = from !== to;
  return { changes, selection: { anchor: map(from, range), head: map(to, false) } };
}
