# Notebook paper

A notebook is a Gamma page with paper sheets and normal annotation blocks.
It uses the existing page operations, permissions, export and Mirror paths.
The stored notebook is not a PDF rewritten after each extra sheet.

The root has `properties.notebook = {version: 1, default_paper: Paper}`.
A direct child sheet has `properties = {type: "notebook-sheet", paper: Paper}`;
its ordinary block ID is its permanent sheet identity and its fractional
`position` orders the sheets. Ink groups can be children of the sheet and
carry `sheet_id` alongside `ink_url` and `ink_strokes`. Root notebook
settings remain subject to existing root-property permissions: an edit
share can add or change sheets, but cannot change the notebook defaults.

`Paper` contains `width` and `height` in points (72 per inch), `color`
(`#RRGGBB`), `pattern` (`blank`, `ruled`, `grid`, `dots`), `spacing` in
points and `line_color` (`#RRGGBB`). Defaults are A4 portrait, white, blank,
24-point spacing and `#d6dce5` lines. Each created sheet snapshots resolved
paper settings. Changing new-page defaults does not restyle existing pages.
Dimensions are 24–10000 points, spacing 4–1000 points; generated dot paper
is limited to 100000 dots per sheet.

Appending means inserting one newly identified sheet block, so concurrent
offline additions both survive. A trailing empty page affordance is local
view state until the user adds the page or starts writing. Never sync an
incremented page count or one replacement array of all sheet definitions.
Keep imported PDF pages on their existing path. The notebook browser
surface draws paper under the ordinary ink layer; native PDFKit can use
generated paper pages as a disposable local display representation.

Notebook ink uses `gamma-ink` version 2:

```json
{"format":"gamma-ink","version":2,"space":{"kind":"notebook-page","sheet_id":"sheetA","width":612,"height":792},"strokes":[]}
```

Coordinates are absolute points from the displayed page's top left. Sheet
IDs, rather than numeric ordinals, anchor ink and recording events. Paper
resizing preserves point coordinates and stroke widths; out-of-page ink
is retained. Scaling content to fit is a separate explicit ink transform.
The original imported-PDF version 1 format remains supported unchanged.

`GET /api/pages/{id}/export-pdf` generates vector paper in sheet order,
then projects stable sheet IDs to PDF page numbers and writes standard
`/Ink` annotations. The ordinary embedded `/GammaInk` payload retains
pressure, timing and fragment lineage for re-import. The projection uses
the destination dimensions without changing point coordinates, avoiding
the implicit scaling used when an ordinary PDF attachment is replaced.
The exported PDF is an interchange artifact: re-importing it produces an
ordinary PDF attachment, not a second copy of the live notebook sheet IDs.
Gamma backup export retains the original editable notebook tree and assets.

Backend: `gamma/notebooks.py`, `gamma/ink.py`, `gamma/ops.py`, and
`gamma/routers/export.py`. Tests cover concurrent appends, ordering,
geometry-preserving export, import, malformed paper and share permissions.
