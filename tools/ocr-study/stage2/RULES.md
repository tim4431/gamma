# Ground-truth transcription rules (OCR study)

You transcribe exactly what is printed on a scanned page, as a careful human
typist would, so OCR engines can be scored against it. Do not correct the
printing, modernize spelling, translate, or complete anything you cannot read.

## Output file

Plain UTF-8 text, **one printed line per line**, in reading order: the page
top to bottom; on a two-column page the full-width lines at the top (title,
authors, abstract, running head), then the left column top to bottom, then
the right column top to bottom, then full-width lines at the bottom
(footnotes spanning the page, page number). Tiles are named L1, L2, … (left
column), R1, R2, … (right column) or 1, 2, … (one column); they overlap by
about 70 pixels, so a line at a tile's edge appears in two tiles: write it
once.

## Characters

- Letters exactly as printed, accents included (é, è, ü, ß, ç …). Old
  spellings stay (e.g. German "seyn", French "phénomène").
- A word hyphenated at the line end keeps the hyphen on that line; the next
  line starts with the rest of the word.
- Ligatures as their letters (fi, fl, ff). Long s (ſ) as "s".
- Quotes, apostrophes and dashes as printed (' " - – —); they are unified
  when scoring.
- Inline math inside a text line: the visible characters in plain Unicode,
  Greek as Greek letters (ω, Δ), sub- and superscripts as plain characters
  after their base with no markup (ω0, x2, 10-3, e−iωt), operators as
  printed (=, +, −, ×, ≈, ∫, √). Fractions written on one line as a/b only
  if printed that way; a built-up fraction inside a text line: numerator/denominator.
- Superscript footnote marks and reference numbers: as plain characters
  where they sit ("trapping1", "Fano [3]").

## Line tags (at the very start of the line)

- `§M ` a **display equation** line (an equation set on its own line, with
  or without its number). Transcribe it linearly as best you can, e.g.
  `§M H = ħω0 |e⟩⟨e| + ħωL a+a (12)`. A multi-line display: one §M line per
  printed line.
- `§F ` text **inside a figure or plot** (axis labels, tick numbers, panel
  letters). Captions are normal text, not §F.
- Tables: each printed row is one line, cells separated by single spaces.

## Uncertainty

If a character is truly illegible (smudge, cut off), write your best reading
and add the line number and the doubt to the notes file, never a placeholder
in the text. Read the tile at full size before deciding; zoom by reading the
tile again if needed.

## Notes file

`<page>.notes.txt`: one line per doubt: `line N: "word" could be "w0rd" (smudged)`;
also anything odd about the page (a column you could not separate, a figure
with lots of text).
