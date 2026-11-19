// A page's pdf.js text runs as characters, for the viewer's search
// (PdfViewer's find, normalizeChars in shared/lib/textnorm.js after it).

// One entry per text-layer character, tagged with its run (`it`) and offset.
// Runs are separated by their PDF line break (`it: -1`), and by a synthetic
// space where two runs touch without one.
export function runChars(runs) {
  const chars = [];
  runs.forEach((run, it) => {
    const text = run.text || "";
    for (let off = 0; off < text.length; off++) chars.push({ ch: text[off], it, off });
    if (run.hasEOL) chars.push({ ch: "\n", it: -1, off: 0 });
    else if (text && !/\s$/.test(text) && runs[it + 1]?.text && !/^\s/.test(runs[it + 1].text)) {
      chars.push({ ch: " ", it: -1, off: 0 });
    }
  });
  return chars;
}
