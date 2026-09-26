// Code fences in a block's source, pure: where they are and whether a
// position sits inside one — for the editor's live decorations, blockTree's
// markdown preprocessing and key handling, and the formatting commands
// (markCommands.js). Apart from codeHighlight.js so node tests load them
// without highlight.js and the DOM.

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

// The fence whose CODE the caret sits in (from the end of the opening ```
// line through the start of the closing line) — where Enter must insert a
// line break instead of a new note, and math/slash popups stay quiet.
export function fenceInnerAt(text, pos) {
  if (!text || !text.includes("```")) return null;
  return scanFences(text).find((f) => pos >= f.innerFrom - 1 && pos <= f.innerTo) || null;
}
