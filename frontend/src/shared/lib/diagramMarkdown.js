// The diagram fences of notes and chat replies: ```mermaid (Mermaid source,
// lib/mermaidRenderer.js) and ```svg (a whole <svg> document,
// lib/svgSanitize.js). The two kinds share one index sequence in source
// order: the nth diagram of a note, whatever its kind, is its nth rendered
// one (docs/dev/diagrams.md).
import { codeFences, mapProse } from "./mdCode.js";

const DIAGRAM_LANG = /^(mermaid|svg)$/i;
const diagramKind = (lang) => DIAGRAM_LANG.exec(lang || "")?.[1].toLowerCase() || null;

// A diagram's display width lives in the fence's info string, after the
// language: "```mermaid width=420". Other Markdown renderers ignore the
// info string past the first word, so the source stays portable — the same
// idea as the Obsidian `![alt|420]` image size. Absent = natural size.
const WIDTH_TOKEN = /(^|\s)width=(\d+)(?=\s|$)/;
export function diagramWidth(meta) {
  const m = WIDTH_TOKEN.exec(meta || "");
  return m ? Number(m[2]) : null;
}

// Mark diagram fences before mdast becomes HTML: their kind, whether they
// are still open (a streaming reply can contain valid diagram syntax before
// its closing fence has arrived) and their width (see diagramWidth), as
// data-diagram-kind / -pending / -width.
export function remarkDiagrams() {
  return (tree, file) => {
    const source = String(file);
    function visit(node) {
      const kind = node.type === "code" ? diagramKind(node.lang) : null;
      if (kind) {
        const raw = source.slice(node.position.start.offset, node.position.end.offset);
        const lines = raw.split(/\r?\n/);
        const opener = /^(`{3,}|~{3,})/.exec(lines[0]);
        const closed = opener && lines.length > 1 && new RegExp(
          `^[ \\t>]*${opener[1][0]}{${opener[1].length},}[ \\t]*$`,
        ).test(lines.at(-1));
        const width = diagramWidth(node.meta);
        node.data = { ...node.data, hProperties: {
          ...node.data?.hProperties, "data-diagram-kind": kind, "data-diagram-pending": closed ? "false" : "true",
          ...(width ? { "data-diagram-width": String(width) } : {}),
        } };
      }
      node.children?.forEach(visit);
    }
    visit(tree);
  };
}

// The diagram fences of a note in source order — the same order the
// rendered view shows them, so the nth diagram is the nth entry (quotes and
// list items included: mdCode.codeFences). Each: its `kind` ("mermaid" |
// "svg"), the opener line's range {from, to} and its info string split into
// {lang, meta, width}, plus `end`, where the whole fence ends (the closing
// line's end; the text's end while it is still open, `closed: false`).
export function scanDiagramFences(text) {
  const out = [];
  for (const f of codeFences(text)) {
    const info = f.info.trim();
    const lang = info.split(/\s+/)[0] || "";
    const kind = diagramKind(lang);
    if (!kind) continue;
    const meta = info.slice(lang.length).trim();
    out.push({ kind, from: f.from, to: f.inner, prefix: f.prefix, ticks: f.ticks, lang, meta,
      width: diagramWidth(meta), end: f.end, closed: f.closed });
  }
  return out;
}

// The note with its nth diagram's width set (0 clears it) — a rewrite of
// that fence's opening line only. null when there is no such diagram, so a
// stale index never edits the wrong fence.
export function setDiagramWidth(text, idx, width) {
  const f = scanDiagramFences(text)[idx];
  if (!f) return null;
  const rest = f.meta.replace(WIDTH_TOKEN, "$1").trim();
  const meta = [rest, width ? `width=${Math.round(width)}` : ""].filter(Boolean).join(" ");
  const line = `${f.prefix}${f.ticks}${f.lang}${meta ? " " + meta : ""}`;
  return text.slice(0, f.from) + line + text.slice(f.to);
}

// A chat reply's math as the $ and $$ remark-math reads: every \[ … \] a
// display block, every \( … \) inline. Not the note rule
// (editor/pasteMath.dollarMath), on purpose: a model writes \[ x \] only as
// display math, while in a note "\[x\]" may be an escaped bracket, so a
// note converts only a pair shaped as a formula is written. What the two
// share is where code is (mdCode.mapProse: fences and code spans).
function normalizeMath(text) {
  return text
    .replace(/\\\[([\s\S]*?)\\\]/g, (_, m) => `\n$$\n${m}\n$$\n`)
    .replace(/\\\(([\s\S]*?)\\\)/g, (_, m) => `$${m}$`)
    // Escape math pipes so GFM doesn't interpret them as table cell boundaries.
    .replace(/\$\$[\s\S]*?\$\$|\$([^$\n]+)\$/g, (m, inner) =>
      inner == null || !inner.includes("|") ? m
        : `$${inner.replace(/\\\|/g, "\\Vert ").replace(/\|/g, "\\vert ")}$`);
}

// `text` with `transform` applied outside its code fences (unfinished ones,
// tildes, and fences in lists and quotes: mdCode.codeFences), each fence
// kept with its closing line's line break. Inline code is left to the
// transform: the rendered view's preprocessing reads it with the math.
export function mapOutsideCodeFences(text, transform) {
  let result = "", pos = 0;
  for (const f of codeFences(text)) {
    const end = text[f.end] === "\n" ? f.end + 1 : f.end;
    result += transform(text.slice(pos, f.from)) + text.slice(f.from, end);
    pos = end;
  }
  return result + transform((text || "").slice(pos));
}

// A reply's math, outside its fences and code spans, so diagram labels and
// code stay as written.
export const normalizeChatMarkdown = (text) => mapProse(text || "", normalizeMath);

// A diagram's source as a fence of its kind, with enough backticks that
// none in the source can close it.
export function diagramFence(kind, source) {
  const ticks = "`".repeat(Math.max(3, ...Array.from(source.matchAll(/`+/g), (m) => m[0].length + 1)));
  return `${ticks}${kind}\n${source}\n${ticks}`;
}

// Mermaid typesets only `$$…$$` labels (its katexRegex), while notes write
// math as `$…$`. Upgrade a note-style span to the form Mermaid reads: a
// same-line pair whose content hugs both dollars and is not followed by a
// digit, so prices ("$5 and $6") and escaped `\$` stay text; existing
// `$$…$$` spans pass through untouched.
export function mermaidMath(source) {
  return (source || "").replace(/\$\$[^$\n]*\$\$|\\\$|\$([^$\s][^$\n]*?[^$\s]|[^$\s])\$(?!\d)/g,
    (m, inner) => inner == null ? m : `$$${inner}$$`);
}
