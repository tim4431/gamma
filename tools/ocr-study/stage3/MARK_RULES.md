# Marking display equations (OCR study, Stage 3)

You mark every DISPLAY equation on scanned physics pages so formula
finders and readers can be scored. A display equation is math set on a
line (or lines) of its own, apart from the running text — not math inside
a sentence (inline math), not a table, not a figure.

## Input

For each page `<page>`: tiles in
`$OCR_STUDY_DIR/stage3/grid/<page>/<tile>.png`
(the page at 300 dpi, cut into tiles; tiles overlap by 80 px; L*/R* are the
left/right column of a two-column page). Each tile carries a green grid
every 100 px labelled with the PAGE's pixel coordinates (x along the top,
y down the left). Read coordinates from the labels, not from the tile's own
pixels (a tile may be shown to you scaled down). `index.json` beside the
folders gives each tile's box on the page.

## What to write

One file per page: `$OCR_STUDY_DIR/stage3/marks/<page>.json`:

```json
{"page": "lounis-p8", "equations": [
  {"box": [420, 865, 1560, 975], "number": "(3.18)", "number_box": [1690, 900, 1800, 945],
   "rows": 1, "inline_heavy_text_nearby": false, "note": ""}
], "notes": ""}
```

- `box`: the equation's body only, in page pixels, a little generous
  (about 10–20 px of white around the ink); EXCLUDE the equation number.
  Snapping to the ink is done afterwards, so err on including all of the
  equation (tall fraction bars, sub/superscripts, brackets, accents) rather
  than cutting it.
- `number`: the printed equation number as text ("(3.18)", "(12a)"), or
  null; `number_box` its box, or null.
- One entry per numbered equation, or per visually separate display without
  a number. A display of several rows that share one number (aligned
  rows, a matrix, a system with a brace) is ONE entry with `rows` > 1. Rows
  that each carry their own number are separate entries.
- Leave out: inline math, tables, figures and their labels, a displayed
  line that is plain words, running heads, footnotes (unless a footnote
  holds a display equation: mark it).
- If an equation runs across two tiles, use both tiles to get its full box.
- Look at EVERY tile of every page. A page with none gets `"equations": []`.
- `notes`: anything unusual (a damaged equation, a display you were unsure
  counts as display).
