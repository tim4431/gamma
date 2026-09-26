// The source scanners for a note's math, images and tables, shared by the
// rendered view (BlockTree's mdPreprocess, the tools in MdTools.jsx, the
// object frame in MdObject.jsx) and the block editor's live rendering
// (BlockCmEditor.jsx), so all of them agree on which construct is "the nth
// one". Pure: no DOM, no React.
import { escapedAt } from "./latexInput.js";
import { scanFences } from "./fences.js";
import { scanImageSyntax } from "./mdMarks.js";

// All CLOSED math spans in the text: [{from, to, display}] with from/to
// including the delimiters. Same tokenizer as latexEditor's findMathAtCursor
// (escaped \$ skipped), but only complete pairs — an unclosed opener stays
// raw text while it's being typed. Inline spans must sit on one line and be
// non-empty; "$5 and $3" across prose otherwise pairs into a bogus formula.
export function scanMathSpans(text) {
  const re = /\$\$?/g;
  const spans = [];
  let m, open = null;
  while ((m = re.exec(text))) {
    if (escapedAt(text, m.index)) continue;
    const tok = { i: m.index, len: m[0].length };
    if (!open) {
      open = tok;
    } else if (tok.len === open.len) {
      const inner = text.slice(open.i + open.len, tok.i);
      const ok = inner.trim() && (open.len === 2 || !inner.includes("\n"));
      if (ok) spans.push({ from: open.i, to: tok.i + tok.len, display: open.len === 2 });
      open = null;
    } else {
      // Mismatched pair ($ ... $$): treat the later token as a fresh opener.
      open = tok;
    }
  }
  return spans;
}

// Ranges markdown syntax must not be recognized inside (math, ``` fences,
// inline code), sorted: mdPreprocess rewrites only outside them, and the
// image scan skips them, so the nth scanned image is the nth rendered one.
export function protectedSpans(content) {
  const spans = scanMathSpans(content).map((s) => ({ from: s.from, to: s.to }));
  for (const f of scanFences(content)) spans.push({ from: f.from, to: f.to });
  for (const m of content.matchAll(/`[^`\n]+`/g)) {
    spans.push({ from: m.index, to: m.index + m[0].length });
  }
  return spans.sort((a, b) => a.from - b.from);
}
const inSpan = (spans, pos) => spans.some((s) => pos >= s.from && pos < s.to);

// The images the rendered view shows, in order. The syntax (Obsidian
// `![alt|300]` size, legacy Logseq `{:width N}` suffix) is scanImageSyntax in
// mdMarks.js; both forms render, edits write the Obsidian form.
export function scanImages(content) {
  const spans = protectedSpans(content);
  return scanImageSyntax(content).filter((im) => !inSpan(spans, im.from));
}

// The multi-line constructs (``` fences, $$ display math) a line boundary
// must not fall inside — what clickToSource's gapInSource takes as `spans`.
export function blockSpans(content) {
  return [...scanMathSpans(content), ...scanFences(content)];
}

// GFM row → trimmed cells (outer pipes dropped, unescaped | splits — per the
// spec a | inside `code` still delimits cells unless written \|).
function splitCells(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim());
}
const DELIM_CELL_RE = /^:?-+:?$/;

// Table line-groups in source order (matching remark-gfm's render order).
// Tables inside blockquotes are still counted — the nth rendered table must
// stay the nth entry — but marked editable:false (ops would have to re-prefix
// every line with ">"; not worth it).
export function scanTables(content) {
  // Only multi-line spans can hide a fake "table" (a ``` fence or $$ display
  // math with | characters on its lines); inline spans can't span rows.
  const spans = scanFences(content).concat(
    scanMathSpans(content).filter((s) => content.slice(s.from, s.to).includes("\n")),
  );
  const lines = [];
  let off = 0;
  for (const text of content.split("\n")) {
    lines.push({ text, start: off, end: off + text.length });
    off += text.length + 1;
  }
  const hidden = (l) => spans.some((s) => l.start < s.to && l.end > s.from);
  const out = [];
  for (let i = 0; i + 1 < lines.length; ) {
    const H = lines[i], D = lines[i + 1];
    const quoted = /^\s*>/.test(H.text);
    const strip = (t) => (quoted ? t.replace(/^[\s>]+/, "") : t);
    const head = strip(H.text), delim = strip(D.text);
    const ok =
      head.includes("|") && !hidden(H) && !hidden(D) &&
      /^\s*>/.test(D.text) === quoted &&
      delim.includes("-") &&
      (() => {
        const dc = splitCells(delim);
        return dc.length === splitCells(head).length && dc.every((c) => DELIM_CELL_RE.test(c));
      })();
    if (!ok) { i += 1; continue; }
    let j = i + 2;
    while (
      j < lines.length && lines[j].text.includes("|") && !hidden(lines[j]) &&
      /^\s*>/.test(lines[j].text) === quoted
    ) j += 1;
    out.push({ from: H.start, to: lines[j - 1].end, editable: !quoted });
    i = j;
  }
  return out;
}

export function parseTable(text) {
  const rows = text.split("\n").map(splitCells);
  const aligns = rows[1].map((c) =>
    c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : c.startsWith(":") ? "left" : null);
  return { header: rows[0], aligns, body: rows.slice(2) };
}

// Pretty-printed GFM: cells padded to the column width so the source stays
// readable after every edit.
export function serializeTable({ header, aligns, body }) {
  const nCols = Math.max(header.length, 1, ...body.map((r) => r.length));
  const pad = (r) => { while (r.length < nCols) r.push(""); return r; };
  pad(header);
  body.forEach(pad);
  while (aligns.length < nCols) aligns.push(null);
  const w = Array.from({ length: nCols }, (_, c) =>
    Math.max(3, header[c].length, ...body.map((r) => r[c].length)));
  const row = (r) => `| ${r.map((t, c) => t + " ".repeat(w[c] - t.length)).join(" | ")} |`;
  const dcell = (c) => {
    const a = aligns[c];
    if (a === "center") return ":" + "-".repeat(Math.max(1, w[c] - 2)) + ":";
    if (a === "right") return "-".repeat(Math.max(1, w[c] - 1)) + ":";
    if (a === "left") return ":" + "-".repeat(Math.max(1, w[c] - 1));
    return "-".repeat(w[c]);
  };
  const delim = `| ${Array.from({ length: nCols }, (_, c) => dcell(c)).join(" | ")} |`;
  return [row(header), delim, ...body.map(row)].join("\n");
}
