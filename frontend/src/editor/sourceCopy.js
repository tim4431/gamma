// The note source Gamma last put on the clipboard: a copy or cut in a
// BlockCmEditor, an object or a block copied as Markdown. Its \[ … \] and
// \( … \) are what the note says (an escaped bracket an older note holds, a
// formula kept as copied), so a paste of exactly that text is left as it is
// (pasteMath.js). It is this tab's memory: a copy made in another tab
// converts like any other text.
let last = null;
// Whether it is an outline: a block and its sub-blocks as nested "- " lines,
// each "- " a block and no list, so Paste as Blocks gets them back as they
// were. The "- a\n- b" of one block is list source, and stays a list.
let lastOutline = false;

export function sourceCopied(text, { outline = false } = {}) {
  last = (text || "").replace(/\r\n?/g, "\n");
  lastOutline = outline;
}

export const isSourceCopy = (text) => last !== null && text === last;

export const isOutlineCopy = (text) => lastOutline && isSourceCopy(text);

// The text CodeMirror's copy puts on the clipboard (its copiedRange): the
// selected ranges, or with nothing selected the lines the carets are on.
export function copiedText(state) {
  const picked = state.selection.ranges.filter((r) => !r.empty).map((r) => state.sliceDoc(r.from, r.to));
  if (picked.length) return picked.join(state.lineBreak);
  const lines = [];
  let upto = -1;
  for (const { from } of state.selection.ranges) {
    const line = state.doc.lineAt(from);
    if (line.number > upto) lines.push(line.text);
    upto = line.number;
  }
  return lines.join(state.lineBreak);
}
