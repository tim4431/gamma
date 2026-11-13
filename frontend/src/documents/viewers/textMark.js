// Where a line link marks a text file (docs/dev/documents.md "Viewers",
// docs/dev/pdf_citations.md "Line links"): the link's number, unless its
// quote, the first line's verbatim text, says the line has moved since the
// link was written; then the nearest line that holds that text, the range
// kept as long. Pure, so node tests it (tests/textMark.test.mjs).

const fold = (s) => s.replace(/\s+/g, " ").trim();

// The index of the line nearest `at` (0-based; ties go to the earlier one)
// for which `hit(i)` holds, -1 for none.
function nearest(count, at, hit) {
  if (!count) return -1;
  const start = Math.max(0, Math.min(at, count - 1));
  for (let d = 0; d < count; d++) {
    const up = start - d, down = start + d;
    if (up < 0 && down >= count) break;
    if (up >= 0 && hit(up)) return up;
    if (d && down < count && hit(down)) return down;
  }
  return -1;
}

// `lines` the file's lines as loaded; `target` {line, to, quote} the link's
// (`to` null for one line, `quote` "" for the number alone) →
// {from, to, moved, past, lost}, 1-based and inclusive. `past`: the line is
// past the end and no line holds the quote, so nothing is marked; `moved`:
// the quote is found on another line, marked there; `lost`: a quote no line
// holds, the number marked as it stands. A line past what is loaded is only
// past the file's end once the whole file is loaded: the caller decides.
export function placeMark(lines, { line, to = null, quote = "" }) {
  const count = lines.length;
  const span = Math.max(0, (to ?? line) - line);
  const at = (from, moved, lost = false) => ({ from, to: Math.min(from + span, count), moved, past: false, lost });
  const want = String(quote || "").trim();
  if (want) {
    if (line <= count && lines[line - 1].trim() === want) return at(line, false);
    const folded = fold(want);
    const found = [
      (i) => lines[i].trim() === want,
      (i) => fold(lines[i]) === folded,
      (i) => fold(lines[i]).includes(folded),
    ].reduce((got, hit) => (got >= 0 ? got : nearest(count, line - 1, hit)), -1);
    if (found >= 0) return at(found + 1, found + 1 !== line);
    if (line <= count) return at(line, false, true);
  } else if (line <= count) {
    return at(line, false);
  }
  return { from: 0, to: 0, moved: false, past: true, lost: !!want };
}
