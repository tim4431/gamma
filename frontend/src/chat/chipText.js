// A context chip's one-line preview of a note's source: the markdown syntax
// dropped, math kept apart so the chip can typeset it. Returns segments,
// [{text} | {math}] in order, whitespace collapsed. Pure; the chip renders
// {math} with KaTeX (editor/LatexEditor.jsx renderKatex). The prose between
// formulas goes through search's plainSnippet (search/snippets.js), the one
// markdown-to-words rule, in its `mathApart` form.
import { scanMathSpans } from "../editor/mdScan.js";
import { plainSnippet } from "../search/snippets.js";

export function chipSegments(source) {
  const text = String(source || "");
  const out = [];
  const pushText = (raw) => {
    const clean = plainSnippet(raw, { mathApart: true });
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
