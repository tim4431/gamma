// A context chip's one-line preview of a note's source: the markdown syntax
// dropped, math kept apart so the chip can typeset it. Returns segments,
// [{text} | {math}] in order, whitespace collapsed. Pure; the chip renders
// {math} with KaTeX (editor/LatexEditor.jsx renderKatex).
import { scanMathSpans } from "../editor/mdScan.js";

// One prose run without its markdown: images keep their alt text, links and
// [[refs]] their words, inline HTML (the colour spans) its content; emphasis,
// highlight, strike and code marks, heading / quote / list / todo / callout
// markers and fence lines go.
export function stripMarkdown(text) {
  return String(text || "")
    .replace(/^\s*```.*$/gm, " ")
    .replace(/^\s{0,3}(?:#{1,6}\s+|>\s*(?:\[![\w-]+\][+-]?\s*)?|[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)/gm, "")
    .replace(/!\[([^\]|]*)(?:\|[^\]]*)?\]\([^)]*\)(?:\{[^}]*\})?/g, "$1")
    .replace(/!?\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_, ref, alias) => alias || ref)
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/<\/?[a-z][^>]*>/gi, "")
    .replace(/(\*\*|__|~~|==)(?=\S)([\s\S]*?\S)\1/g, "$2")
    .replace(/(^|[^\w*])[*_](?=\S)([^*_\n]*?\S)[*_](?!\w)/g, "$1$2")
    .replace(/`([^`\n]+)`/g, "$1")
    .replace(/\\\$/g, "$");
}

export function chipSegments(source) {
  const text = String(source || "");
  const out = [];
  const pushText = (raw) => {
    const clean = stripMarkdown(raw).replace(/\s+/g, " ");
    if (!clean) return;
    const last = out[out.length - 1];
    if (last && "text" in last) last.text += clean;
    else out.push({ text: clean });
  };
  let at = 0;
  for (const span of scanMathSpans(text)) {
    pushText(text.slice(at, span.from));
    const delim = span.display ? 2 : 1;
    const tex = text.slice(span.from + delim, span.to - delim).trim();
    if (tex) out.push({ math: tex });
    at = span.to;
  }
  pushText(text.slice(at));
  if (out.length && "text" in out[0]) out[0].text = out[0].text.trimStart();
  const last = out[out.length - 1];
  if (last && "text" in last) last.text = last.text.trimEnd();
  return out.filter((s) => s.math || s.text);
}
