# Zotero's annotations in a PDF (October 2026)

What a PDF must carry for Zotero to import Gamma's highlights, area notes,
handwriting and text boxes; what Zotero writes for its own; and what each
side keeps of the other's. The mechanics Gamma built from this are in
[dev/import_export.md](../dev/import_export.md#zotero-rdf-export) and
[dev/text_boxes.md](../dev/text_boxes.md#server-and-interchange).

## How it was found

From Zotero 9.0.6 as installed (`app/omni.ja`): the PDF worker
(`chrome/content/zotero/xpcom/pdfWorker/worker.js`), its manager
(`pdfWorker/manager.js`), the reader (`resource/reader/reader.js`) and the
"Zotero RDF" translator in the data directory. The rules below were read
from that code. The worker was also run in Node, outside Zotero: a shim for
`self.onmessage`/`postMessage`, the fonts and CMaps from
`resource/reader/pdf/web/` answering its requests, and stubs for `DOMMatrix`
and `Path2D`. Its `import` action showed what Zotero imports from Gamma's
exports, and its `export` action wrote Zotero's own annotations for Gamma's
importer to read. No Zotero window or library was touched.

## What Zotero reads (`readRawAnnotation`)

- Kinds: `/Text` is a note, `/Highlight`, `/Underline`, `/Square` an
  image, `/Ink`, `/FreeText` a text annotation. Anything else (`/Squiggly`,
  `/StrikeOut`, `/Circle`, `/Polygon`, stamps) is not imported.
- An id: `/Zotero:Key`, else an `/NM` shaped `Zotero-<key>`. An image, ink
  or text annotation without one is dropped. Highlights, underlines and
  notes need none.
- A text annotation also needs `/Zotero:Rect` (its box before any turn),
  `/Zotero:Rotation` and `/Zotero:FontSize`, or it is dropped. Ink needs an
  `/InkList` of even-length paths and a numeric `/BS /W`.
- The colour is `/Zotero:Color`, else `/C`, else `/IC`. When `/CA` is a
  number, the colour is blended toward white by it (`c·a + 255·(1−a)`,
  `a` at least 0.1).
- The comment is `/Contents`, the author `/Zotero:AuthorName` (not `/T`),
  the tags `/Zotero:Tags` (a JSON list). A highlight's or underline's text
  comes from the glyphs under it, and a comment that repeats it is dropped.

## When Zotero reads them

- Opening a PDF, or a check after its file changed, imports the embedded
  annotations as *external*: shown, locked, not editable. An unchanged file
  is skipped.
- The reader's File → Import Annotations… (`transferFromPDF`, enabled while
  external ones exist) moves the ones with an id, and all highlights,
  underlines and notes, into Zotero's database and deletes them from the
  file.
- The RDF import reads no annotations. Export with "Include Annotations"
  writes Zotero's own, non-external annotations into each copied PDF
  (`PDFWorker.export`); a PDF with none is copied unchanged.

## What Zotero writes (`annotationToRaw`)

Every annotation gets `/NM Zotero-<key>`, `/Zotero:Key`, `/T` and
`/Zotero:AuthorName`, `/M`, `/F 4`, and `/Zotero:Tags` when tagged. The
manager strips `<i> <b> <sub> <sup>` from comments first.

| Zotero kind | Written as |
|---|---|
| highlight, underline | `/Highlight`, `/Underline`: `/QuadPoints`, `/C`, an appearance, no `/CA`; a `/Popup` when commented |
| note | `/Text`, a 22 pt square, `/C`, `/CA 1` |
| image | `/Square`, `/BS /W 2`, `/C` |
| ink | `/Ink`: `/InkList`, `/BS /W` its width, `/C`, `/CA 1` |
| text | `/FreeText`: the `/Zotero:*` keys, `/DA (/Helvetica <size> Tf r g b rg)`, `/Contents` the text, an appearance; `/Rect` the bounding box of the turned rect; no `/C` |

The reader makes a text annotation with rotation 0 and draws it around its
rect's centre, turned by the page's rotation less its own.

## Gamma against it, as found

Gamma → Zotero:

| Gamma | Annotated PDF | Zotero export's copy | What Zotero took |
|---|---|---|---|
| Highlight | `/Highlight`, no id, `/CA 0.65` | the same | a highlight, paler (`#ffe28f` became `#ffecb6`) |
| Area note | `/Square` with an id | the same | an image, paler |
| Handwriting | `/Ink` with an id | not written | ink from the annotated PDF; from the Zotero export only its caption, as a note |
| Text box | `/FreeText` with an id, no `/Zotero:*` | not written; a note | nothing from the annotated PDF |

The Zotero export predated handwriting (August against September 2026),
and its builder was never given the ink. Also found:

- An item's key was its place in the export (`#gamma_item_<n>`). Two pages
  exported one at a time were both `#gamma_item_1`, and importing the
  second merged it into the first and dropped it.
- A Gamma export brought back into its own library made a second page,
  with every highlight a second time.
- The importer truncated colour channels, so a colour written to three
  decimals lost a step (`0.831` became 211, not 212).

Zotero → Gamma: each of the six kinds imported. Highlights and underlines
became highlights, images area highlights, ink handwriting, text and notes
text boxes; importing again added nothing. A text annotation turned in
Zotero came in as a 40 × 200 box instead of 200 × 40, from its turned
`/Rect`.

## What was decided

- The Zotero export's PDF copies carry what the annotated PDF does: ink
  with the highlights switch, text boxes with either switch. An ink group's
  caption is its `/Ink` comment, not a note as well. A text box is a note
  as well only when notes sit under it.
- Gamma's `/FreeText` carries the `/Zotero:*` keys: the box as shown around
  its centre, the page's rotation, the size, the text colour. Checked on a
  page turned 90°: Zotero's worker read the box, its writer rebuilt Gamma's
  `/Rect` from those keys to the point, and Gamma's import restored the box.
- Keys come from ids (`#gamma_page_<id>`, `#gamma_note_<id>`), and the
  import merges a Gamma export into the page it came from.
- A Zotero text annotation imports as its `/Zotero:Rect` upright on the
  same centre; colour channels are rounded.
- Colours were left as they are at first (below). In October 2026 the
  Zotero copies stopped writing `/CA` on highlights and area notes, Gamma's
  alpha in the private `/GammaAlpha` instead, which the import reads first
  ([zotero-roundtrip.md](zotero-roundtrip.md)), and every annotation names
  its author in `/Zotero:AuthorName`.

## Still open

- **Colours.** Zotero's own come in at the importer's default 0.45,
  unlike Gamma's 0.65. Gamma's now reach Zotero unblended (above), and
  another viewer draws such a copy's highlight at full strength, since a
  highlight there has no appearance stream. Mapping onto Zotero's eight
  colours (`#ffd400 #ff6666 #5fb236 #2ea8e5 #a28ae5 #e56eee #f19837
  #aaaaaa`) by nearest hue, as the Logseq names are mapped, was not done.
  None of it was checked in a running Zotero.
- **Annotation tags** (`/Zotero:Tags`) have no place on a block.
- **Shapes Gamma lacks:** an underline is a highlight, a note's colour is
  dropped, a turned text annotation stands upright.
- **Round trip through Zotero.** After Import Annotations… and an export
  from Zotero, Zotero has its own item keys and its own annotations, without
  `/GammaInk` or `/GammaTextBox`. The import makes a new page, the ink
  loses pressure and timing, and a box loses its Markdown and fill.
- Zotero 6 drops text boxes. Nothing was checked in a running Zotero
  window, only the worker in Node and the reader's code.
