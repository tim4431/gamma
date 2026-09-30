# Notebooks: sheets of paper in a page

A page can hold sheets of paper to write on, like a Notability note:
pages are added as you write, and each has its own paper (size,
orientation, pattern and background). A sheet is an ordinary block, so it
syncs, merges, shares and exports like any other. A notebook is just a
page that has sheets: nothing else marks it. The page shows its sheets
among its notes or, in the notebook view, in the viewer's place. The web
app and the iPad app ([ipad.md](ipad.md)) write them the same way.

Code: `gamma/notebook.py` (server: paper rules, the PDF), `frontend/src/notebook/notebook.js`
(the same rules on the client, pure), `notebook/NotebookViewer.jsx`
(the notebook view and the paper menu), `notebook/NoteSheet.jsx` (a sheet
in the notes view), the sheets half of `app/App.jsx` ("Sheets"), and
`ipad/GammaIPad/Reader/NotebookReader.swift`. Tests:
`backend/tests/test_notebooks.py`, `frontend/tests/notebook.test.mjs`,
the shared cases in `tests/shared/paper.json`, and e2e
`tests/e2e/scenarios/notebooks.mjs`.

## What the user sees

- **Two views of one page.** They show the same sheets and follow the
  same rules; only the layout differs.
  - The **notes view** draws each sheet where it stands among the
    blocks, fitted to the notes' width (at most 1.5 CSS px per point).
    Under a sheet are its pen (the ink strip opens at the top of the
    notes when no viewer holds it), its paper menu, the replay of its
    handwriting ([handwriting.md](handwriting.md) "Replay"), **+** (a
    page right after it) and the switch to the notebook view.
  - The **notebook view** puts the sheets in the viewer's place, one under
    the other, fitted to the width, with the zoom buttons, the pen button
    and the ink strip a PDF has, **Add page** at the end, and the paper
    button for the sheet in the middle of the view. The notes beside them
    list each sheet as a row.
- **Switching.** A sheet's **Notebook view** button (under it in the notes
  view) opens the notebook view; it shows when the page has no PDF. The
  **Notes view** button in the notebook view's side bar, under the paper
  button, goes back, and so does the viewer's close button. View →
  Notebook view does both. The choice is remembered per page in this browser and
  saved the moment it changes. A page with no choice opens in the notes
  view.
- **+ → New notebook** makes a page with one blank A4 sheet and opens it in
  the notebook view, with its title ready to type.
- **Adding a page** works the same in both views:
  - Writing in the bottom quarter of the page's last sheet adds the next
    one right after it, so there is always paper below.
  - **Add page** at the end of the notebook view, and a sheet's **+**, add
    one after that sheet.
  - **/page** in a block's editor ("Page to write on", under Insert) makes
    the block a sheet when nothing else is in it, and otherwise puts one
    right after it. **Add page below** in any block's handle menu does the
    same from that block.
- **A new sheet** takes the paper of the sheet nearest before it, or A4
  blank, and starts folded: its handwriting groups are its children, and
  the sheet itself shows them. Unfolding lists them with their cards,
  captions and replay buttons.
- **The paper menu** sets size (A4, Letter, A5), orientation, pattern
  (blank, ruled, grid, dots), line spacing and background (white, cream,
  grey, dark). Each choice applies to that sheet at once. *Apply to all
  pages* gives every sheet of the page that paper.
- In the notes each sheet is a row: "Page N", numbered in document order,
  or the title typed into it. A note typed under a sheet is a note about
  that page. A handwriting card jumps to its drawing and outlines the
  group, like on a PDF.
- **Export → Annotated PDF** on a page without a PDF that has sheets gives
  them as a PDF: one page per sheet, the paper painted and the
  handwriting drawn as vectors.
- On the iPad a page with sheets opens in the notebook view, and a
  toolbar button shows its notes alone.

## Model

No schema change: sheets are blocks and properties.

| Block | Properties |
|---|---|
| A sheet: any block of a page | `sheet: <paper>`, its paper, and `collapsed: true` when it is made. Its content is its title (empty: "Page N") |
| An ink group: any block under a sheet | `ink_url`, `ink_strokes`. Its file's `space` is `{kind: "canvas", width, height}`, the sheet's frame (points from its top-left) |

- **Sheets are blocks** because adding a page must never lose a page.
  Two devices that each add a page while apart insert two blocks, and
  both survive a merge (fractional positions, [collab.md](collab.md)).
  A page count or a list in a property would be one value, so one side's
  page would win and the other's would be lost.
- **The order of the sheets is document order**: every block carrying
  `sheet`, at any depth, parents before children. Other blocks are notes
  and are listed like any note.
- **Ink belongs to the nearest sheet above it**, at any depth: the tree
  says which page a drawing is on, so there is no property to keep in
  step. A sheet nested under another sheet holds its own drawings.
  Moving a group under another sheet in the notes moves the drawing to
  that page. Deleting a sheet deletes its drawings with it, and the
  mirror's rule that an edit beats a delete keeps a sheet that someone
  else drew on meanwhile ([mirror.md](mirror.md)).
- **The view is not in the document.** Whether a page shows the notebook
  view is the reader's choice, kept per page and browser. Collaborators
  and devices each choose their own.
- **Ids.** A new notebook's sheet id follows from the page's
  (`firstSheetId`), and the id of the sheet after a sheet follows from
  that sheet's (`sheetIdAfter`). Both are an FNV-1a hash of the parts,
  prefixed `s`. So a retried creation, or two devices adding "the page
  after page 3" at once, insert the same id, and inserting an id a page
  already has changes nothing. Writing low on the last sheet adds nothing
  when the sheet after it is there already. Otherwise an id that is taken
  falls back to a random one, and so does a sheet added after a block
  that is no sheet.

### Paper

`{width, height, color, pattern, spacing, line}`: the size in points
(A4 is 595.28 × 841.89), the background as `#rrggbb`, `blank` / `ruled` /
`grid` / `dots` every `spacing` points in the `line` colour.

- Stored paper is read through `normalize_paper` / `normalizePaper`,
  never trusted. A missing or bad key takes the default's value. Sizes are
  clamped to 144–2000 pt and spacing to 12–96 pt, both rounded to
  hundredths, and colours are lowercase.
- The pattern's geometry is `paper_lines` / `paperLines`. Ruled paper has
  a line every `spacing`. Grid paper has the verticals, then the
  horizontals. Dot paper has a dot at each crossing, row by row. All
  offsets are `k × spacing`, strictly inside the page.
- The browser draws it as an SVG under the ink layer (`PaperBackground`),
  the export as PDF operators (`paper_ops`), and the iPad as shape layers.
  All three draw from the same geometry, which `tests/shared/paper.json`
  pins for Python and JavaScript.
- A sheet's paper changing leaves its handwriting where it is: points
  from the top-left corner. A smaller page may crop a drawing on screen,
  and the strokes stay in the file.

## Server

- `gamma/notebook.py`: `normalize_paper`, `paper_lines`, `is_sheet`,
  `sheets_of` (a page's sheets in document order, from its flat blocks,
  each with the blocks under it that no nearer sheet holds), `paper_ops`
  and `notebook_pdf`. `tests/shared/paper.json` `sheets` pins `sheets_of`
  against the client's `sheetsOf` and `inkBySheet`.
- `routers/export.py` `annotated_pdf`: a page without a `doc_id` that has
  sheets becomes `notebook_pdf`. Each sheet is a PDF page of its paper's
  size, in the top-left frame the notes PDF uses (`q 1 0 0 -1 0 h cm`):
  the paper, then every ink group on the sheet through
  `ink.pdf_path_ops`. `X-Annotations-Written` counts the groups drawn. A
  page with neither is refused (400, "page has no PDF").
- The agent's `read_block` outline names a sheet ("a page of paper: the
  handwriting under it is written on it") and a group on one
  ("handwriting on the page of paper above").

## Client

- `notebook/notebook.js` (pure): the paper rules, `isSheet`, `sheetsOf`
  (document order, at any depth), `inkBySheet` (sheet id → the ink blocks
  it draws), `sheetOfBlock` (the nearest sheet), `paperBefore` (the paper
  a sheet added after a block gets), `stableId`, `firstSheetId`,
  `sheetIdAfter`, `newSheet` (folded), `PAPER_SIZES`.
- `notebook/NotebookViewer.jsx`: `NotebookViewer` draws the notebook view,
  each sheet a `PaperBackground` under an `InkLayer` keyed by the sheet's
  id instead of a page number, with the stroke handlers held stable so a
  sheet re-renders only for its own ink. It sizes to fit the widest sheet
  at `page-width`, else follows the viewer's zoom (`pdfScale`,
  Ctrl+wheel), and keeps the top of the view in place across a zoom. It
  reports the sheet under the middle of the view and scrolls to a sheet
  and box on request. `PaperMenu` is the paper panel.
- `notebook/NoteSheet.jsx`: `NoteSheet`, a sheet in the notes view, and
  `NoteSheetContext`, the ink state and handlers App gives it (the ones
  the notebook view gets, plus the pen, paper and add-after actions). It
  measures its row, draws `PaperBackground` and an `InkLayer` keyed by its
  id, and swaps the layer for a plain SVG of the replay's frame while its
  replay plays.
- `app/App.jsx`:
  - `nbSheets` are the open page's sheets. `hasSheets`: it has some and
    no PDF, so the notebook view can show them. `notebook`: the notebook
    view is on for the page (`nbViewPages`, set by `setNotebookView`,
    kept in localStorage `gamma-notebook-view:<user>@<workspace>`). It is
    the layout switch beside `pageAttach`: the viewer's close button,
    controls and ink strip show for it, and `viewerHidden` (a closed PDF)
    never hides it.
  - In the notes view the rows get `inlineSheets`, the numbers
    `sheetNumbers` and `NoteSheetContext`. The strip shows over the notes
    (`notesInkStrip`) when the page has sheets and no viewer holds it.
  - `handleInkStroke` takes a sheet id where a PDF page number goes: the
    group's file is `newCanvasInk`, and a new group's block is inserted
    under its sheet. Writing low on the last sheet calls `addSheetAfter`
    with `once`.
  - `addSheetAfter` (right after a block), `addPageAtEnd` (after the last
    sheet), `insertSheetAt` ("/page"), `setSheetPaper` and
    `applyPaperToAll` (every sheet, at any depth) are ordinary tree edits.
  - `createNotebook` posts a page, then its first sheet as an op, and
    turns the notebook view on for it.
  - The notes' ink card jumps through `showInkOnPage` to the drawing: in
    the notebook view on the viewer, in the notes view on the sheet in the
    notes (unfolded into view).
- `editor/BlockTree.jsx` numbers the sheets in the notes (an untitled
  sheet reads "Page N"), draws a `NoteSheet` in a sheet's row when
  `inlineSheets`, keeps a press on it from opening the editor, and offers
  "Add page below" in the handle menu. `editor/SlashMenu.jsx` has
  `/page` (a command's own name ranks before words that mention it).

## Not built yet

- Reordering sheets from the notebook view (the notes can move them),
  and inserting a sheet between two others there (in the notes, Add page
  below does).
- Drawing a sheet in the Markdown export and the notes PDF as a page: its
  groups export as drawings, like any group's.
- Templates beyond the four patterns (music staves, Cornell margins),
  and a paper image.
- Virtualizing a long notebook's sheets. Every sheet mounts, which suits
  notebooks of tens of pages.
