// KaTeX parse errors of a raw math span, as ranges the block editor
// underlines (VSCode's squiggle). Pure and node-testable: no editor here.
//
// KaTeX's ParseError carries the offending token's position and length
// (an undefined \command, a mismatched \end, a stray closer); an unfinished
// formula reports a zero-length error at the end of the input, which lands
// on the last character so the underline has somewhere to sit. A trailing
// lone backslash is a \command being typed and is parsed without it, like
// the live preview does.
import katex from "katex";
import { escapedAt } from "./latexInput.js";

const _cache = new Map();

// [{from, to, message}] in offsets of `tex` (empty when it parses).
export function latexErrors(tex, display) {
  const src = escapedAt(tex, tex.length) ? tex.slice(0, -1) : tex;
  if (!src.trim()) return [];
  const key = (display ? "D:" : "I:") + src;
  let errors = _cache.get(key);
  if (errors === undefined) {
    errors = [];
    try {
      katex.__parse(src, { displayMode: !!display, throwOnError: true, strict: false });
    } catch (e) {
      if (typeof e?.position === "number") {
        let from = Math.max(0, Math.min(e.position, src.length));
        let to = Math.min(src.length, from + (e.length || 0));
        // A control word's token swallows the blank after it.
        while (to > from && /\s/.test(src[to - 1])) to--;
        if (to <= from) {
          // Zero-length: nothing to point at, so mark the last character.
          const tail = src.replace(/\s+$/, "");
          from = Math.max(0, tail.length - 1);
          to = Math.max(from + 1, Math.min(tail.length, src.length));
        }
        errors.push({ from, to, message: e.rawMessage || String(e.message || "") });
      }
    }
    _cache.set(key, errors);
    if (_cache.size > 500) _cache.delete(_cache.keys().next().value);
  }
  return errors;
}

// The errors worth showing while the caret is at `caret` (tex offset):
// any range the caret touches is what's being typed right now, so it is
// left alone until the caret moves on. A caret outside the span (null)
// shows everything.
export function visibleLatexErrors(errors, caret) {
  if (caret == null) return errors;
  return errors.filter((e) => caret < e.from || caret > e.to);
}
