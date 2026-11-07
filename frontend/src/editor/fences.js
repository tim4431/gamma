// Code fences in a block's source, pure: where they are and whether a
// position sits inside one — for the editor's live decorations, blockTree's
// markdown preprocessing and key handling, and the formatting commands
// (markCommands.js). Apart from codeHighlight.js so node tests load them
// without highlight.js and the DOM.

import { parseLine } from "./mdLines.js";

// All ``` fenced regions in the text, in order:
//   [{from, to, innerFrom, innerTo, lang, closed}]
// from/to include the fence marker lines (to = end of the closing line, or
// text end while the fence is still open); innerFrom = start of the first
// code line, innerTo = start of the closing line (== code end + "\n").
export function scanFences(text) {
  const fences = [];
  let open = null;
  let pos = 0;
  for (const line of text.split("\n")) {
    const end = pos + line.length;
    const m = /^(`{3,})(.*)$/.exec(line);
    if (m) {
      if (!open) {
        open = {
          from: pos,
          ticks: m[1].length,
          lang: m[2].trim(),
          innerFrom: Math.min(end + 1, text.length),
        };
      } else if (m[1].length >= open.ticks && !m[2].trim()) {
        fences.push({
          from: open.from, to: end,
          innerFrom: open.innerFrom, innerTo: pos,
          lang: open.lang, closed: true,
        });
        open = null;
      }
    }
    pos = end + 1;
  }
  if (open) {
    fences.push({
      from: open.from, to: text.length,
      innerFrom: open.innerFrom, innerTo: text.length,
      lang: open.lang, closed: false,
    });
  }
  return fences;
}

// What a note renders as code, which is more than scanFences's cards: a fence
// after a quote's or callout's marks, and a ~~~ one. It closes on its own mark
// at least as long, at its own quote depth, or where its quote ends:
//   [{innerFrom, innerTo, quote}]
// innerFrom = start of the first code line, innerTo = where the closing line's
// mark starts (text end while the fence is open), quote = the opening line's
// marks, what a new line of its code starts with.
function codeFences(text) {
  const fences = [];
  let open = null;
  let pos = 0;
  const close = (innerTo) => {
    fences.push({ innerFrom: open.innerFrom, innerTo, quote: open.quote });
    open = null;
  };
  for (const line of text.split("\n")) {
    const { quote, depth } = parseLine(line);
    const m = /^(`{3,}|~{3,})(.*)$/.exec(line.slice(quote.length));
    if (open && depth < open.depth) close(pos - 1);
    if (!open) {
      if (m && !(m[1][0] === "`" && m[2].includes("`"))) {
        open = { mark: m[1], quote, depth, innerFrom: pos + line.length + 1 };
      }
    } else if (m && depth === open.depth && m[1][0] === open.mark[0]
      && m[1].length >= open.mark.length && !m[2].trim()) {
      close(pos + quote.length);
    }
    pos += line.length + 1;
  }
  if (open) close(text.length);
  return fences;
}

// The fence whose CODE the caret sits in (from the end of the opening line
// through the start of the closing line's mark) — where Enter must insert a
// line break instead of a new note, and math/slash popups stay quiet.
export function fenceInnerAt(text, pos) {
  if (!text || !(text.includes("```") || text.includes("~~~"))) return null;
  return codeFences(text).find((f) => pos >= f.innerFrom - 1 && pos <= f.innerTo) || null;
}
