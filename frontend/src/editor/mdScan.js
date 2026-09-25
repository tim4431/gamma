// The source scanners for a note's images and tables, shared by the rendered
// view's tools (MdTools.jsx), the object frame (MdObject.jsx) and the block
// editor's live rendering (BlockCmEditor.jsx), so all three agree on which
// construct is "the nth one". Pure: no DOM, no React.
import { scanMathSpans } from "./markCommands";
import { scanFences } from "./fences.js";
import { scanImageSyntax } from "./mdMarks";

// Ranges an image regex must not fire inside — mirrors mdPreprocess's span
// protection (math, ``` fences, inline code) so the nth scanned image is the
// nth rendered one.
function protectedSpans(content) {
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
