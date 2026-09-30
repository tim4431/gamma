# Notebooks and pages in notes

A notebook is a page written on sheets of paper instead of a PDF, like a
Notability note. Pages are added as you write, and each page has its own
paper: size, orientation, pattern and background. It is an ordinary page
of blocks, so it syncs, merges, shares and exports like any other page.
The web app and the iPad app ([ipad.md](ipad.md)) write it the same way.

The same pages can stand among any page's notes: a note can hold a page
to write on between its text blocks ("Pages in notes").

Code: `gamma/notebook.py` (server: paper rules, the PDF), `frontend/src/notebook/notebook.js`
(the same rules on the client, pure), `notebook/NotebookViewer.jsx`
(the viewer and the paper menu), `notebook/NoteSheet.jsx` (a page among
the notes), the sheets half of `app/App.jsx` ("Sheets"), and
`ipad/GammaIPad/Reader/NotebookReader.swift`. Tests:
`backend/tests/test_notebooks.py`, `frontend/tests/notebook.test.mjs`,
the shared cases in `tests/shared/paper.json`, and e2e
`tests/e2e/scenarios/notebooks.mjs`.

## What the user sees

- **+ → New notebook** makes a notebook with one blank A4 page and opens
  it with its title ready to type.
- The notebook takes the PDF viewer's place: the sheets one under the
  other, fitted to the width, with the same zoom buttons, the same pen
  button and the same ink tool strip as a PDF ([handwriting.md](handwriting.md)).
- **A page is added on demand.** Writing in the bottom quarter of the
  last page adds the next page, so there is always paper below.
  **Add page** at the end adds one too.
- **The paper button** opens the paper menu for the page in the middle of
  the view. It sets size (A4, Letter, A5), orientation, pattern (blank,
  ruled, grid, dots), line spacing and background (white, cream, grey,
  dark). Each choice applies to that page at once. *Use for new pages*
  makes it the paper new pages get. *Apply to all pages* gives every page
  that paper, new pages too.
- In the notes each sheet is a row ("Page 1", or the title typed into
  it), with its handwriting groups under it. A note typed under a page is
  a note about that page. A handwriting card jumps to its page and
  outlines the group, like on a PDF.
- **Export → Annotated PDF** gives the notebook as a PDF: one page per
  sheet, the paper painted and the handwriting drawn as vectors.

### Pages in notes

- **/page** in a block's editor ("Page to write on", under Insert) makes
  the block a page when nothing else is in it, and otherwise puts a page
  right after it. **Add page below** in a block's handle menu does the
  same from any block.
- The page is drawn where it stands, fitted to the notes' width (at most
  1.5 CSS px per point), with the ink layer a notebook's sheet has.
- Under the page are its tools:
  - the pen, which opens the ink strip at the top of the notes when no
    viewer holds it (a PDF page's strip stays in its viewer);
  - the paper menu (no "Use for new pages": a note has no paper of its
    own);
  - the replay of the page's handwriting ([handwriting.md](handwriting.md)
    "Replay");
  - **+**, a page right after this one.
- A stylus writes on the page right away, as on a PDF.
- The page's row reads "Page N", numbered in document order.
- The page starts folded: its handwriting groups are its children, and
  the page itself shows them. Unfolding lists them with their cards,
  captions and replay buttons.
- A new page gets the paper of the page nearest before it, or A4 blank.
- A page is not added by writing low on it (a note is not a stack of
  paper); + and Add page below add one.
- On the iPad such a note opens as its pages, with the notes beside them
  ([ipad.md](ipad.md)).

## Model

No schema change: a notebook is blocks and properties.

| Block | Properties |
|---|---|
| A notebook's root | `notebook: {sheet: <paper>}`, the paper new sheets get |
| A sheet: any block of the page | `sheet: <paper>`, its paper. Its content is the page's title (empty: "Page N"). Among a note's blocks it starts `collapsed: true` |
| An ink group: any block under a sheet | `ink_url`, `ink_strokes`. Its file's `space` is `{kind: "canvas", width, height}`, the sheet's frame (points from its top-left) |

- **Sheets are blocks** because adding a page must never lose a page.
  Two devices that each add a page while apart insert two blocks, and
  both survive a merge (fractional positions, [collab.md](collab.md)).
  A page count or a list in a property would be one value, so one side's
  page would win and the other's would be lost.
- **The order of the sheets is document order**: every block carrying
  `sheet`, at any depth, parents before children. A notebook's pages are
  normally the root's children. Other blocks (a note about the whole
  notebook) are not sheets and are listed in the notes like any note.
- **Ink belongs to the nearest sheet above it**, at any depth: the tree
  says which page a drawing is on, so there is no property to keep in
  step. A page nested under another page holds its own drawings.
  Moving a group under another sheet in the notes moves the drawing to
  that page. Deleting a sheet deletes its drawings with it, and the
  mirror's rule that an edit beats a delete keeps a sheet that someone
  else drew on meanwhile ([mirror.md](mirror.md)).
- **Ids.** The first sheet's id follows from the page's
  (`firstSheetId`), and the id of the sheet after a sheet follows from
  that sheet's (`sheetIdAfter`). Both are an FNV-1a hash of the parts,
  prefixed `s`. So a retried creation, or two devices adding "the page
  after page 3" at once, insert the same id, and inserting an id a page
  already has changes nothing. An id that is taken already falls back to
  a random one, and so does a page added after a block that is no page.

### Paper

`{width, height, color, pattern, spacing, line}`: the size in points
(A4 is 595.28 × 841.89), the background as `#rrggbb`, `blank` / `ruled` /
`grid` / `dots` every `spacing` points in the `line` colour.

- Stored paper is read through `normalize_paper` / `normalizePaper`,
  never trusted. A missing or bad key takes the fallback's value (for a
  sheet, the notebook's paper). Sizes are clamped to 144–2000 pt and
  spacing to 12–96 pt, both rounded to hundredths, and colours are
  lowercase.
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

- `gamma/notebook.py`: `normalize_paper`, `paper_lines`, `page_notebook`
  (the root's notebook, or None), `is_sheet`, `sheets_of` (a page's sheets
  in document order, from its flat blocks, each with the blocks under it
  that no nearer sheet holds), `paper_ops` and `notebook_pdf`.
  `tests/shared/paper.json` `sheets` pins `sheets_of` against the
  client's `sheetsOf` and `inkBySheet`.
- `routers/export.py` `annotated_pdf`: a notebook page (no `doc_id`)
  becomes `notebook_pdf`. Each sheet is a PDF page of its paper's size,
  in the top-left frame the notes PDF uses (`q 1 0 0 -1 0 h cm`): the
  paper, then every ink group under the sheet through `ink.pdf_path_ops`.
  A notebook with no sheet exports one blank page.
  `X-Annotations-Written` counts the groups drawn.
- The agent's `read_block` outline names a sheet ("a page of paper: the
  handwriting under it is written on it") and a group on one
  ("handwriting on the page of paper above"), in a notebook and in a
  note.

## Client

- `notebook/notebook.js` (pure): the paper rules, `pageNotebook`,
  `isSheet`, `sheetsOf` (document order, at any depth), `inkBySheet`
  (sheet id → the ink blocks it draws), `sheetOfBlock` (the nearest sheet),
  `paperBefore` (the paper a page added after a block gets), `stableId`,
  `firstSheetId`, `sheetIdAfter`, `newSheet` (`folded` for a note's),
  `PAPER_SIZES`.
- `notebook/NotebookViewer.jsx`: `NotebookViewer` draws the sheets, each
  a `PaperBackground` under an `InkLayer` keyed by the sheet's id instead
  of a page number, with the stroke handlers held stable so a sheet
  re-renders only for its own ink. It sizes to fit the widest sheet at
  `page-width`, else follows the viewer's zoom (`pdfScale`, Ctrl+wheel),
  and keeps the top of the view in place across a zoom. It reports the
  sheet under the middle of the view and scrolls to a sheet and box on
  request. `PaperMenu` is the paper panel.
- `notebook/NoteSheet.jsx`: `NoteSheet`, a page among a note's blocks,
  and `NoteSheetContext`, the ink state and handlers App gives it (the
  ones the notebook's viewer gets, plus the pen, paper and add-after
  actions). It measures its row, draws `PaperBackground` and an
  `InkLayer` keyed by its id, and swaps the layer for a plain SVG of the
  replay's frame while its replay plays.
- `app/App.jsx`:
  - `notebook` (`pageNotebook` of the open page) is the layout switch
    beside `pageAttach`: a notebook is no page-only page, and the viewer's
    close button, controls and ink strip show for it.
  - `nbSheets` are the open page's sheets on any page. For a page that is
    no notebook, the rows get `inlineSheets`, the numbers `sheetNumbers`
    and `NoteSheetContext`. The strip shows over the notes
    (`notesInkStrip`) when the page has sheets and no viewer holds it.
  - `handleInkStroke` takes a sheet id where a PDF page number goes: the
    group's file is `newCanvasInk`, and a new group's block is inserted
    under its sheet. Writing low on the last sheet adds one only in a
    notebook.
  - `addSheet` (a notebook's next page), `addSheetAfter` (right after a
    block), `insertSheetAt` ("/page"), `setSheetPaper`, `setNotebookPaper`
    (a PATCH of the root) and `applyPaperToAll` (every sheet, at any
    depth) are ordinary tree edits.
  - `createNotebook` posts the page with its `notebook` property, then
    its first sheet as an op.
  - The notes' ink card jumps through `showInkOnPage` to the sheet: in a
    notebook its viewer, among notes the page in the notes (unfolded
    into view).
- `editor/BlockTree.jsx` numbers the sheets in the notes (an untitled
  sheet reads "Page N"), draws a `NoteSheet` in a sheet's row when
  `inlineSheets`, keeps a press on it from opening the editor, and offers
  "Add page below" in the handle menu. `editor/SlashMenu.jsx` has
  `/page` (a command's own name ranks before words that mention it).

## Not built yet

- Reordering sheets from the viewer (the notes can move them), and
  inserting a page between two others from the viewer (in the notes,
  Add page below does).
- Drawing a page among a note's blocks in the Markdown export and the
  notes PDF as a page: its groups export as drawings, like any group's.
- Templates beyond the four patterns (music staves, Cornell margins),
  and a paper image.
- Virtualizing a long notebook's sheets. Every sheet mounts, which suits
  notebooks of tens of pages.
