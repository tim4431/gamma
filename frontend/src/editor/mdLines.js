// The structure of a note's markdown lines. A line is
//
//   > >   - [ ] milk
//   quote indent marker body
//
// its quote marks and indentation (where it sits), a marker (what it is: a
// bullet, number, to-do, heading, or a callout's "[!type]" title on a
// quote's line) and its text. parseLine reads that once; the line-break
// Enter (enterPlan), the new-block key (newBlockPlan), Backspace after a
// marker (backspacePlan), Toggle quote (toggleQuotePlan), a paste
// (pastePlacements) and the "/" menu's line commands (retypeLine, through
// slashInserts.js) all work on it. Every line of a quote carries its
// marks, a blank one a bare ">", so a quote stays one box here, in
// Obsidian and on GitHub. Pure string functions with no imports, like
// toggleTodoLine (mdMarks.js); the iPad app bundles them too.

const QUOTE_RE = /^(?: {0,3}> ?)*/;
const LEVEL_RE = / {0,3}> ?/y;
const MARKER_RE = /^(?:[-*+] \[[ xX]\] |[-*+] |\d{1,9}[.)] |#{1,6} )/;
const CALLOUT_MARKER_RE = /^\[!\w+\][-+]?[ \t]*/;
const LIST_KINDS = new Set(["bullet", "number", "task"]);

export function lineAt(text, pos) {
  const from = text.lastIndexOf("\n", pos - 1) + 1;
  const nl = text.indexOf("\n", pos);
  const to = nl < 0 ? text.length : nl;
  return { from, to, text: text.slice(from, to) };
}

function kindOf(marker) {
  if (!marker) return null;
  if (marker.startsWith("[!")) return "callout";
  if (marker.startsWith("#")) return "heading";
  if (/\[[ xX]\]/.test(marker)) return "task";
  return /^\d/.test(marker) ? "number" : "bullet";
}

// One line's parts: { quote, depth (its quote levels), indent, marker,
// kind ("bullet" | "number" | "task" | "heading" | "callout" | null), body }.
// quote + indent + marker + body is the line.
export function parseLine(line) {
  const quote = QUOTE_RE.exec(line)[0];
  const depth = quote.split(">").length - 1;
  const indent = /^[ \t]*/.exec(line.slice(quote.length))[0];
  const rest = line.slice(quote.length + indent.length);
  const marker = (MARKER_RE.exec(rest) || (depth ? CALLOUT_MARKER_RE.exec(rest) : null))?.[0] || "";
  return { quote, depth, indent, marker, kind: kindOf(marker), body: rest.slice(marker.length) };
}

const uncheckedMarker = (p) => p.marker.replace(/\[[xX]\]/, "[ ]");

// The marker of the item after this one: a number counted on, a checked
// box unchecked.
function nextMarker(p) {
  const num = /^(\d+)([.)]) $/.exec(p.marker);
  return num ? `${Number(num[1]) + 1}${num[2]} ` : uncheckedMarker(p);
}

// What an empty item leaves when the innermost thing ends: "> - " keeps the
// quote and drops the list, "> > " drops a level.
const endedItem = (p) => (LIST_KINDS.has(p.kind) ? p.quote : p.quote.replace(/> ?$/, ""));

// A line that is one thing whole: a table row, a divider, or a code fence's
// or display math's delimiter. The new-block key does not cut one, and a
// merge joins no text to one.
const WHOLE_LINE_RE = /^ {0,3}(?:\||`{3,}|~{3,}|\$\$|([-*_])(?: *\1){2,} *$)/;

export function isWholeLine(line) {
  return WHOLE_LINE_RE.test(line.slice(parseLine(line).quote.length));
}

// The line without its first n quote levels.
function cutLevels(line, n) {
  let at = 0;
  for (let i = 0; i < n; i++) {
    LEVEL_RE.lastIndex = at;
    if (!LEVEL_RE.exec(line)) break;
    at = LEVEL_RE.lastIndex;
  }
  return line.slice(at);
}

// The quote marks of the caret's line ("> ", "> > ", ">"), or "" when the
// line is no quote or the caret sits before its marks.
export function quoteMarksAt(text, pos) {
  const line = lineAt(text, pos);
  const { quote } = parseLine(line.text);
  return quote && pos - line.from >= quote.trimEnd().length ? quote : "";
}

// The line-break Enter at a caret (Obsidian-style): a quote or list line
// continues on the next line with the same marks, a checked box unchecked
// and a number counted on. On an empty item the innermost thing ends
// instead: "> - " keeps the quote and drops the list, "> > " drops a level,
// "> " or "- " leaves an empty line. {from, to, insert, pos} for one
// change and the caret after it, or null for a plain newline.
export function enterPlan(text, pos) {
  const line = lineAt(text, pos);
  const p = parseLine(line.text);
  const list = LIST_KINDS.has(p.kind);
  const prefix = p.quote + (list ? p.indent + p.marker : "");
  if (!prefix || pos - line.from < prefix.length) return null;
  if (!line.text.slice(prefix.length).trim()) {
    const keep = endedItem(p);
    return { from: line.from, to: line.to, insert: keep, pos: line.from + keep.length };
  }
  const next = p.quote + (list ? p.indent + nextMarker(p) : "");
  return { from: pos, to: pos, insert: "\n" + next, pos: pos + 1 + next.length };
}

// Where a block's text starts: past the first line's quote marks and marker,
// which is where the caret goes in a block made by the new-block key.
function textStart(text) {
  const nl = text.indexOf("\n");
  const line = nl < 0 ? text : text.slice(0, nl);
  return line.length - parseLine(line).body.length;
}

// The new-block key over [from, to] in a plain note, which works in blocks
// where enterPlan works in lines. The selection goes, then the block splits
// at the caret: a list goes on with the next item, the text after the caret
// taking that marker; a quote's text keeps its marks; a heading's goes on
// plain. Lines after the caret's go along as they are, and a table row, a
// divider or a delimiter is not cut. Before the block's text the new block
// is made above it, and the caret stays. An empty item is dropped, and the
// block splits where it was; one that is the whole block ends the innermost
// thing instead, as in enterPlan, and no block is made. {keep, next, caret}:
// this block's text, the new block's, the caret in it. {keep, stay: true,
// caret}: no block, the caret in this one. {keep, above: true, next}: the
// caret stays. Inside a fence or $$ the key is a line break (BlockTree's
// keydown, checked first), so this never sees one; a caret in inline math
// comes here past the formula (blockCommands.splitPlan).
export function newBlockPlan(text, from, to = from) {
  const t = text.slice(0, from) + text.slice(to);
  const line = lineAt(t, from);
  const p = parseLine(line.text);
  const list = LIST_KINDS.has(p.kind);
  const lead = line.text.length - p.body.length;
  const at = from - line.from;
  const split = (head, tail) => {
    const keep = head.replace(/\n+$/, ""), next = tail.replace(/^\n+/, "");
    return { keep, next, caret: textStart(next) };
  };
  if ((list || (p.depth && !p.kind)) && !p.body.trim() && at >= lead) {
    if (line.from || line.to < t.length) return split(t.slice(0, line.from), t.slice(line.to));
    const keep = endedItem(p);
    return { keep, stay: true, caret: keep.length };
  }
  if (!line.from && at <= lead && t.trim() && (!at || at < lead || p.body.trim())) {
    return { keep: t, above: true, next: list && at === lead ? p.quote + uncheckedMarker(p) : "" };
  }
  if (isWholeLine(line.text)) return { keep: t, next: "", caret: 0 };
  if (line.from && at <= lead) return split(t.slice(0, line.from), t.slice(line.from));
  const marks = p.quote + (list ? nextMarker(p) : "");
  const head = t.slice(0, from), tail = t.slice(from);
  if (from === t.length) return { keep: head, next: list ? marks : "", caret: list ? marks.length : 0 };
  return tail.startsWith("\n") ? split(head, tail) : { keep: head, next: marks + tail, caret: marks.length };
}

// Backspace right after a list marker, on any line of the block: the marker
// goes in one press, and whatever holds the line (quote marks, indentation)
// stays. {from, to, pos}, the range to delete and the caret after it, or
// null with no marker just before the caret. A heading or a quote stays raw
// Markdown: there Backspace takes one character.
export function backspacePlan(text, pos) {
  const line = lineAt(text, pos);
  const p = parseLine(line.text);
  if (!LIST_KINDS.has(p.kind)) return null;
  const from = line.from + p.quote.length + p.indent.length;
  const to = from + p.marker.length;
  return pos === to ? { from, to, pos: from } : null;
}

// The first line of a block merged into the text above: its list or heading
// marker goes, the rest stays.
export function withoutFirstMarker(text) {
  const nl = text.indexOf("\n");
  return parseLine(nl < 0 ? text : text.slice(0, nl)).body + (nl < 0 ? "" : text.slice(nl));
}

// Where pasted lines go on the line they land on, by its innermost
// container: beside a list item or under it (its text column), in a quote
// or in a quote inside it, or — no container — just here.
function placesOf(p) {
  if (LIST_KINDS.has(p.kind)) {
    const bullet = p.marker.replace(/\[[ xX]\] $/, "");
    return { kind: "list", here: p.quote + p.indent, nested: p.quote + p.indent + " ".repeat(bullet.length) };
  }
  if (p.depth) return { kind: "quote", here: p.quote, nested: p.quote + "> " };
  return { kind: "none", here: "", nested: null };
}

// The pasted text's own shape: its outer quote level q (the fewest quote
// levels of a line with text), whether that level is a callout (a box,
// whose level is part of it), and each line without those q levels and
// without the indentation all its lines share.
function shapeOf(pasted) {
  const lines = pasted.split("\n");
  const parsed = lines.map(parseLine);
  const filled = parsed.map((p) => !!(p.indent + p.marker + p.body).trim());
  const withText = parsed.filter((_, i) => filled[i]);
  const q = withText.length ? Math.min(...withText.map((p) => p.depth)) : 0;
  const rests = lines.map((l, i) => (filled[i] ? cutLevels(l, q) : cutLevels(l, q).trim()));
  const pads = rests.filter((_, i) => filled[i]).map((r) => /^[ \t]*/.exec(r)[0]);
  let pad = pads[0] || "";
  for (const s of pads) while (!s.startsWith(pad)) pad = pad.slice(0, -1);
  return {
    q,
    box: q > 0 && withText[0]?.depth === q && withText[0]?.kind === "callout",
    filled,
    lines: rests.map((r, i) => (filled[i] ? r.slice(pad.length) : r)),
  };
}

// A paste of `pasted` over [from, to]: the pasted lines keep their shape
// (deeper quote levels, markers, sub-items) and move to the level of the
// line they land on. Each placement replaces one range [from, to]:
//   here   — beside the line: list items as its siblings, lines in its quote
//   nested — one level in: under the list item, or in a quote inside it
//   copied — the text as it came
// A quoted paste (lines copied out of a quote) merges its outer level into
// the quote it lands in; a pasted callout keeps its own, so it stays a box
// inside the quote. In a list, pasted lines of text become items. On an
// empty line the first pasted line takes the line's place (its own marker
// wins over "- "); after a line's text a pasted list or quote starts on a
// new line, and at the start of it, above it. The default comes first;
// null when every placement is the text as it came. A single line only
// ever fills an empty line. `copied` is the clipboard's own text when
// `pasted` is it with its math converted (pasteMath.js).
export function pastePlacements(text, from, to, pasted, copied = pasted) {
  const t = text.slice(0, from) + text.slice(to);
  const line = lineAt(t, from);
  const p = parseLine(line.text);
  const prefixLen = line.text.length - p.body.length;
  const places = placesOf(p);
  const shape = shapeOf(pasted);
  const keep = p.depth && shape.q && !shape.box ? shape.q - 1 : shape.q;
  const rel = shape.lines.map((r) => "> ".repeat(keep) + r);
  const first = parseLine(rel[0]);
  const ownMarker = first.depth > 0 || first.kind != null;
  // A list or a quote, as against lines of text, which join the line's.
  const shaped = ownMarker || shape.q > 0;
  const empty = !line.text.slice(prefixLen).trim()
    && from - line.from >= line.text.slice(0, prefixLen).trimEnd().length;
  if (rel.length < 2 && !(empty && shaped)) return null;

  const mode = empty ? "fill" : !shaped ? "inline" : from - line.from === prefixLen ? "above" : "after";
  const range = mode === "fill" ? [line.from, line.to] : mode === "above" ? [line.from, from] : [from, from];
  // In a list a pasted line of text becomes an item, as Enter would make
  // it: the line's marker, a box unchecked, numbers counting on from the
  // line's (from 1 under it). The first line of a fill or an inline paste
  // is the line's own text.
  const num = /^(\d+)([.)]) $/.exec(p.marker);
  const itemMarker = (k, under) => (num ? `${under ? k : Number(num[1]) + k}${num[2]} ` : uncheckedMarker(p));
  const build = (where) => {
    let k = 0;
    const out = rel.map((r, i) => {
      if (!shape.filled[i]) return (where + r).trimEnd();
      const own = i === 0 && (mode === "fill" || mode === "inline");
      const rp = parseLine(r);
      if (places.kind !== "list" || own || rp.kind || rp.depth) return where + r;
      return where + rp.indent + itemMarker(++k, where !== places.here) + rp.body;
    });
    if (mode === "fill" && !ownMarker && where === places.here) out[0] = line.text.slice(0, prefixLen) + rel[0];
    if (mode === "above") return out.join("\n") + "\n" + line.text.slice(0, prefixLen);
    if (mode === "after") return "\n" + out.join("\n");
    if (mode === "inline") out[0] = pasted.split("\n")[0];
    return out.join("\n");
  };
  const options = [{ name: "here", insert: build(places.here) }];
  // One level in means nothing for a box already inside a quote, nor for
  // a paste that lands before a list item's text or in its place.
  const nested = places.nested && !(places.kind === "quote" && shape.box)
    && !(places.kind === "list" && (mode === "fill" || mode === "above"));
  if (nested) options.push({ name: "nested", insert: build(places.nested) });
  options.push({ name: "copied", insert: t.slice(range[0], from) + copied + t.slice(from, range[1]) });
  const unique = options.filter((o, i) => options.findIndex((x) => x.insert === o.insert) === i);
  if (unique.length < 2) return null;
  // Back in the text's own offsets: the range starts at or before the
  // selection and ends past it, so the paste takes the selection's place.
  const back = (pos) => (pos < from ? pos : pos + to - from);
  return { from: range[0], to: back(range[1]), kind: places.kind, options: unique };
}

// The "/" menu's line commands on one line (caret: an offset in it; above:
// the line before it, "" for none): they change what the line is and keep
// where it sits. A heading, to-do, bullet or number takes the place of the
// line's marker, and text takes it away; quote adds a quote level in front
// of it; callout makes the quote this line starts a callout, or, on any
// other line, starts a new one here, one level in. {line, caret}, the caret
// kept in the text.
const LINE_MARKERS = { text: "", h1: "# ", h2: "## ", h3: "### ", todo: "- [ ] ", bullet: "- ", number: "1. " };

export function retypeLine(line, caret, kind, above = "") {
  const p = parseLine(line);
  const inText = Math.max(0, caret - (line.length - p.body.length));
  let prefix;
  if (kind === "quote") prefix = p.quote + "> " + p.indent + p.marker;
  else if (kind === "callout") prefix = (p.depth > parseLine(above).depth ? p.quote : p.quote + "> ") + "[!note] ";
  else prefix = p.quote + p.indent + LINE_MARKERS[kind];
  return { line: prefix + p.body, caret: prefix.length + inText };
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
