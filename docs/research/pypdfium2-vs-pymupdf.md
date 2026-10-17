# pypdfium2 or PyMuPDF for the server

Findings of October 2026. The question: the server reads PDFs through
pypdfium2 (`gamma/pdf_text.py`, the manifest walk, the note placement of
the annotated export, the Logseq export's area pictures), and the iPad
needs a wheel of its own for it ([pypdfium2-ios.md](pypdfium2-ios.md)),
while PyMuPDF has iOS wheels ready-made. Is PyMuPDF the better library for
what the server does, in functions, speed, footprint, threads, licence and
platforms? The viewer is pdf.js in the page and is outside this question.

Measured on the developer's machine (Windows 11, 24 cores, Python 3.14.6)
with the server's pin, pypdfium2 5.11.0 (pdfium 151.0.7920.0), against
PyMuPDF 1.28.2 (MuPDF 1.28.2), on five PDFs from the local library: a
15-page paper (2.2 MB), a 22-page image-heavy paper (14 MB), a 26-page
figure-heavy paper (10.5 MB), a 484-page book (26 MB) and a 1,310-page
book (19.5 MB). Each number is the median of three runs, one run for the
whole-document walks over the books. The workloads are the server's own:
each call closes what it opens, as `pdf_text.py` does.

## Speed

Milliseconds, pdfium / MuPDF.

| Workload | paper | image-heavy | figure-heavy | 484-page book | 1,310-page book |
|---|---|---|---|---|---|
| open and count pages | 0.7 / 1.0 | 0.9 / 1.1 | 1.0 / 1.6 | 4.6 / 21 | 0.7 / 10 |
| page sizes, every page (the manifest walk, `page_sizes`) | 94 / 18 | 94 / 6 | 142 / 7 | 584 / 67 | 923 / 44 |
| text, every page (`iter_page_texts`) | 112 / 75 | 125 / 126 | 161 / 148 | 747 / 1,041 | 1,559 / 1,052 |
| text, first three pages (a chat's head read) | 8.7 / 8.0 | 29 / 30 | 89 / 65 | 5.7 / 26 | 3.4 / 13 |
| glyph boxes, five pages (`GlyphPages`) | 37 / 139 | 94 / 1,057 | 147 / 149 | 17 / 57 | 5 / 142 |
| page-object bounds, five pages (`pdf_notes._page_occupancy`) | 10 / 10 | 79 / 75 | 121 / 94 | 7.5 / 29 | 5.4 / 12 |
| outline | 1.4 / 1.5 | 2.8 / 1.5 | 4.3 / 2.5 | 49 / 29 | 20 / 11 |
| render page 1, longer side 1,568 px (`render_page`) | 7.8 / 10 | 13 / 21 | 13 / 20 | 5.8 / 11 | 24 / 15 |
| render a crop of page 1 | 3.0 / 5.2 | 4.7 / 13 | 4.0 / 11 | 1.5 / 4.2 | 5.6 / 4.5 |
| render the middle page | 9.7 / 11 | 4.6 / 7.2 | 14 / 19 | 3.9 / 5.4 | 28 / 17 |

What the table says:

- **Text extraction is a tie.** Within 40% either way, and the direction
  changes from file to file.
- **Rendering: pdfium is ahead** on four of the five, by 1.3 to 2.8 times,
  MuPDF on the 1,310-page book. Both are small beside the JPEG encode
  (5 to 7 ms through Pillow for either) and the model that reads the
  picture. MuPDF's own JPEG encoder takes 40 to 80 ms; Pillow stays.
- **Glyph boxes: pdfium, unless MuPDF is asked right.** MuPDF's `rawdict`
  carries every image's bytes by default, which is the 1,057 ms on the
  image-heavy paper; with `TEXT_PRESERVE_IMAGES` off it is 19 ms on the
  paper and 61 ms on the image-heavy one, faster than pdfium's per-glyph
  `get_charbox` calls (37 and 94 ms).
- **Page sizes: MuPDF's lead is pdfium misused.** `page_sizes` loads each
  page (`PdfDocument[i].get_size()`), which parses its content. pdfium's
  `FPDF_GetPageSizeByIndexF` reads the size without loading the page:
  1.0 ms on the paper and 22 ms on the 1,310-page book instead of 94 and
  923, with the same values on every page of the four files tried, and
  `/Rotate` applied (a made page with `/Rotate 90` comes back 200 by 400
  from both calls). That is the manifest walk at upload and `/pdf-info`
  for an unmeasured file, forty times faster whichever library stays.
  MuPDF's equivalent, `page_cropbox`, does not apply the rotation.
- **Opening** is lazy in pdfium (0.7 ms for the 1,310-page book) and
  parses the page tree in MuPDF (10 to 21 ms on the books). Not felt.

## Text quality

The word sequences agree: a difflib ratio over the first three pages of
0.98 to 1.00 on each file, word counts within 1%, no replacement
characters from either, the same five CJK characters found. The one
systematic difference is the hyphen at a line's end. pdfium joins some
of them (5 of the paper's 16: "transduc-tion" comes out "transduction"),
keeps a few, and MuPDF keeps every one as "transduc-\ntion"; its
`TEXT_DEHYPHENATE` flag joins them all. For search it does not matter:
`textnorm` re-joins words across hyphenated line breaks before indexing.
For the AI's context pdfium's text reads a little cleaner as it is.

PyPDF2, the fallback the iPad ran on before its pdfium wheel, is what both
replace: 594 ms for the paper against 112 and 75, a 0.988 ratio to
either, and more words glued together.

## Functions

What the server uses today, both have: a document from a path or bytes,
the page count, a page's size with `/Rotate` applied, the page text in
reading order, each glyph with its box, text search on a page (pdfium
gives character ranges, MuPDF quads), the outline, a page rendered whole
or cropped at a scale, the bounds of a page's objects, and the appearance
of `/Ink` and `/Highlight` annotations that carry no `/AP` (what
`pdf_export` writes and `view_ink` relies on): both drew the test page's
ink stroke and highlight, 13.3k and 13.7k non-white pixels.

What only PyMuPDF has as helpers is the writing side: annotations
(`add_ink_annot`, `add_highlight_annot`, `add_rect_annot`,
`add_freetext_annot`), text and images on a page, HTML typeset into a box
(`insert_htmlbox`, `Story`) with the base-14 fonts and Droid Sans
Fallback for CJK built into the library, incremental saves, dictionary
edits at the xref level, redaction, OCR through Tesseract, SVG of a page,
one page shown inside another. That is the ground
`pdf_export.py`, `pdf_notes.py`, `pdf_typeset.py`, `pdf_glyphs.py`,
`pdf_image.py`, `vector_text.py`, `text_box.py`, `pdf_document.py` and
`notebook.py` cover by hand on PyPDF2 today, about 3,800 lines
([dev/pdf_typesetting.md](../dev/pdf_typesetting.md)). pypdfium2's
helpers stop at rendering, text, page objects, images, new and imported
pages and a save; the rest of pdfium's C API (`FPDFPage_CreateAnnot`,
`FPDFAnnot_AddInkStroke`, `FPDFText_LoadFont`, ...) is reachable through
`pypdfium2.raw`, and there is no typesetting and no built-in font: pdfium
draws a non-embedded font from the system's, which is why the Docker
image installs `fonts-droid-fallback` and why pdfium's fonts on an iPad
are still to be seen.

## Footprint

| | pypdfium2 5.11 | PyMuPDF 1.28 |
|---|---|---|
| wheel, win_amd64 | 3.9 MB | 19.8 MB |
| installed | 7.6 MB (`pdfium.dll` 7.2) | 54 MB (`mupdfcpp64.dll` 26, `_mupdf.pyd` 13, headers 6.7, the SWIG module 2.7 and its cache 5) |
| iOS, unpacked | 7 MB dylib (the wheel built in `build-python.sh`) | 57 MB dylib (Flet's wheel) |
| import | 122 ms, +14 MB resident | 68 ms, +33 MB resident |
| after the image-heavy paper's text walk and a render | 33 MB (peak 55) | 52 MB (peak 71) |

## Threads and the GIL

pdfium is not thread-safe, and the test shows it: four threads walking
four documents without the lock gave 9 exceptions and 3 wrong results in
0.2 s. The same walks through PyMuPDF without a lock were all correct,
but its documentation says "PyMuPDF does not support running on multiple
threads - doing so may cause incorrect behaviour or even crash Python
itself", so `pdf_text._lock` would stay, with its per-page hand-over and
the door that keeps it fair ([dev/ai_context.md](../dev/ai_context.md)
"Extraction lock").

The difference is the GIL. pypdfium2 calls pdfium through ctypes, which
releases it: four threads rendering a page ran 2.8 times faster than one,
and a Python thread spinning beside a render kept its full rate. PyMuPDF
holds the GIL inside every call: four threads rendered no faster than
one (0.93 times), and the spinning thread kept 65% of its rate. On this
server that is the event loop: a 20 ms render or a page's text under
PyMuPDF stalls every request and WebSocket for that long, where under
pdfium only the lock's waiters wait. Per-page calls keep each stall
short; a whole-book walk under PyMuPDF would still be a second of pauses
spread over its pages.

The finalizer story is PyMuPDF's gain: its objects are freed by reference
count under the GIL, not by a weakref finalizer the cyclic GC runs on
another thread, so `_serialize_finalizers` would have nothing to net.

## Platforms, Python, licence, upkeep

- **Wheels.** pypdfium2 5.14: Windows x86, x64 and arm64; macOS x64 and
  arm64; glibc Linux x64, aarch64, armv7, i686, ppc64le, s390x, riscv64;
  musl Linux the same seven; Android arm64 and armv7; wasm. No iOS wheel;
  the repo builds one. PyMuPDF 1.28: Windows x86, x64 and arm64; macOS
  x64 and arm64; glibc Linux x64 and aarch64 (manylinux 2.28, which
  `python:3.12-slim` meets); musl x64; wasm; iOS and Android from Flet's
  index at 1.27.2.3 for CPython 3.12 to 3.14.
- **Python.** pypdfium2 is pure Python over ctypes: one wheel per
  platform, any CPython from 3.6, free-threaded included. PyMuPDF ships
  abi3 wheels for 3.10 and later and a free-threaded build for Linux x64
  only.
- **Licence.** pypdfium2 is Apache-2.0 / BSD-3-Clause over pdfium's BSD.
  PyMuPDF is AGPL-3.0 or Artifex's commercial licence. Gamma is AGPL-3.0,
  so PyMuPDF is compatible as things stand; it would tie any future
  relicensing, or a closed build of the desktop or iPad app, to AGPL or
  to a paid licence.
- **Upkeep.** pypdfium2: 20 releases since April 2025, the latest 5.14.0
  on 2026-10-04, one maintainer, pdfium from pdfium-binaries' weekly
  builds of Chromium's library. PyMuPDF: 13 releases since April 2025,
  the latest 1.28.2 on 2026-08-06, Artifex, which also makes MuPDF.

## What a switch would cost

`pdf_text.py` (643 lines, the lock, the finalizer net, the glyph walk,
the renderer), the occupancy read in `pdf_notes.py`, the area render in
`logseq_graph_export.py`, twelve test files that import pypdfium2
(`test_pdfium_lock.py` monkeypatches its classes), the PyInstaller
`--collect-all` lines in `desktop/build_backend.py`, the iPad's wheel
build and framework step, and the docs that name pdfium (ai_context,
ai_tools, pdf_loading, pdf_typesetting, text_boxes, ipad). The lock
stays. Every install grows by 46 MB on disk, the iPad bundle by 50.

## The answer

Stay on pypdfium2 for what the server does. Extraction is a tie and
rendering is faster; it is a seventh of the size; it releases the GIL
where PyMuPDF holds the event loop; its licence keeps the project's
options open; and the iOS wheel is built in the tree with only a device
and TestFlight left to see ([dev/ipad.md](../dev/ipad.md#the-wheels)).

Two things would change the answer. If pdfium does not load or draw
fonts on an iPad, PyMuPDF's ready wheel with its built-in fonts is the
fallback, behind the same guards, at 57 MB. And if the hand-rolled PyPDF2
writers are ever replaced by a library, PyMuPDF's writing side
(annotations, HTML typesetting, CJK without a system font) is the
argument, and reading would move with it. Whichever library reads, the
manifest walk should take page sizes without loading pages.

## Not confirmed

- MuPDF's and pdfium's fonts on an iPad: Droid Sans Fallback is built
  into MuPDF; pdfium's system-font path on iOS is untested.
- Linux timings: everything here was measured on Windows.
- CJK text extraction: the library's PDFs are Latin; the figure-heavy
  paper carries five CJK characters, which both read.
- The stall on a running server: the 65% figure is one Python thread
  spinning beside one render. What a real load feels, requests and
  WebSockets while a book is walked page by page, was not measured.

## Outcome (October 2026)

The server moved to PyMuPDF (`pymupdf==1.28.2`; the iPad on Flet's
1.27.2.3) the same month, against this note's answer. The two reasons:
the fonts and the text extraction — MuPDF carries the base-14 fonts and
Droid Sans Fallback inside the library, so a page whose fonts are not
embedded renders and reads the same on every server with no system font
to find, and its extraction knows each glyph's font and box — and iOS,
where its wheel exists and pypdfium2's would have had to be built
([pypdfium2-ios.md](pypdfium2-ios.md)). The read side kept the design
"What a switch would cost" lists: one lock, taken per page on the long
walks, explicit closes inside it, PyPDF2 only when a file cannot be
opened at all; the finalizer net went, since PyMuPDF frees by reference
count. Of this note's open items, the page-size walk is cheap in MuPDF as
measured above (44 to 67 ms for the books), so `FPDF_GetPageSizeByIndexF`
is moot; the GIL held inside every call is why the calls stay one page's
work. `test_pdfium_lock.py` is `test_pdf_text_lock.py` now. How it reads:
[dev/pdf_loading.md](../dev/pdf_loading.md),
[dev/ai_context.md](../dev/ai_context.md) "Extraction lock",
[dev/pdf_typesetting.md](../dev/pdf_typesetting.md) "Notes drawn on the
page"; the iPad's side: [pymupdf-ios.md](pymupdf-ios.md).

### Measured after the switch

The same machine (Windows 11, 24 cores, Python 3.14.6), pypdfium2 5.11.0
(pdfium 151.0.7920.0) against PyMuPDF 1.28.2 (MuPDF 1.28.2), PyPDF2 3.0.1
and Pillow 12.3.0 under both — this time through the server's own code
rather than raw calls: `dev`'s `pdf_text.py` and `pdf_notes.py` over
pypdfium2 and this branch's over PyMuPDF, each imported in a process of
its own from the same venv, the public API called as the routers call it,
the page-text cache cleared before every run. Eight PDFs from the local
library: two papers of the library's median size (17 pages and 8 pages,
1.9 MB each), a 22-page image-heavy paper (14 MB), a 28-page figure-heavy
paper (17.3 MB), a 32-page image-heavy paper (19.8 MB), a 293-page book
(3.8 MB), and the two books of the table above (484 pages, 25.9 MB;
1,310 pages, 19.5 MB). The library has no CJK file: a probe of the first
three pages of all 103 found no CJK text beyond the five characters of
the earlier comparison's 26-page figure-heavy paper, which the library
holds twice. Median of three runs after a warm-up, one run for a
whole-document walk over the 484- and 1,310-page books.

Milliseconds, pdfium / MuPDF.

| Workload | 17-page paper | 8-page paper | 22-page image-heavy | 28-page figure-heavy | 32-page image-heavy | 293-page book | 484-page book | 1,310-page book |
|---|---|---|---|---|---|---|---|---|
| open and count pages (`page_count`) | 0.3 / 0.6 | 0.5 / 0.6 | 0.9 / 1.1 | 0.7 / 1.3 | 0.9 / 0.9 | 2.4 / 0.9 | 4.6 / 22 | 0.7 / 9.7 |
| page sizes, every page (`page_sizes`, the manifest walk) | 55 / 1.5 | 40 / 2.0 | 82 / 5.9 | 304 / 11 | 277 / 18 | 143 / 17 | 488 / 56 | 638 / 51 |
| text, every page (`extract_pages`, the indexer's walk, no cache) | 65 / 64 | 53 / 53 | 107 / 111 | 357 / 327 | 311 / 236 | 273 / 282 | 747 / 906 | 1,586 / 1,040 |
| text, 6,000 characters (`extract_text`, a chat's head read) | 8.7 / 9.0 | 22 / 24 | 18 / 20 | 37 / 39 | 14 / 15 | 7.1 / 9.4 | 10 / 33 | 7.1 / 15 |
| glyph boxes, five pages (`GlyphPages`) | 33 / 84 | 122 / 274 | 103 / 235 | 121 / 256 | 190 / 243 | 27 / 77 | 18 / 58 | 6.3 / 21 |
| occupancy, five pages (`pdf_notes`'s read into `_page_occupancy`) | 53 / 52 | 117 / 124 | 111 / 116 | 171 / 158 | 171 / 460 | 45 / 55 | 40 / 63 | 34 / 44 |
| one note drawn on page 1 (`render_notes`, the PyPDF2 writer included) | 114 / 123 | 220 / 242 | 402 / 447 | 819 / 819 | 1,223 / 1,229 | 474 / 466 | — | 841 / 875 |
| outline | 0.3 / 0.7 | 1.0 / 0.8 | 2.5 / 1.5 | 3.0 / 1.6 | 1.5 / 1.3 | 2.5 / 1.0 | 43 / 29 | 20 / 9.6 |
| render page 1, longer side 1,568 px (`render_page`, the JPEG included) | 19 / 19 | 34 / 33 | 32 / 32 | 31 / 33 | 25 / 23 | 15 / 17 | 23 / 43 | 35 / 33 |
| render a crop of page 1 | 11 / 12 | 17 / 18 | 18 / 17 | 18 / 22 | 16 / 14 | 7.8 / 10 | 9.6 / 27 | 10 / 17 |
| render the middle page | 19 / 19 | 42 / 47 | 20 / 18 | 40 / 39 | 26 / 23 | 20 / 21 | 24 / 39 | 46 / 40 |

What the numbers say:

- **The manifest walk is where MuPDF wins**, `dev` never having taken
  pdfium's size-by-index call: `page_sizes` is 8 to 37 times faster,
  488 / 56 ms on the 484-page book and 638 / 51 on the 1,310-page one, as
  the raw numbers above said; the server's turn of the lock per page and
  its `/UserUnit` lookup cost nothing visible.
- **Text is still a tie**, through the server's path as through the raw
  calls: within 10% on five files, MuPDF a quarter faster on the 32-page
  image-heavy paper and a third faster on the 1,310-page book, a fifth
  slower on the 484-page book — the figures of the `iter_page_texts` row
  above (747 / 1,041 and 1,559 / 1,052 then), so the lock's turn per page
  and the cache's bookkeeping add nothing measurable. A chat's head read
  is equal on the papers and slower under MuPDF on the books (10 / 33 and
  7.1 / 15 ms), which is the open: MuPDF parses the page tree when it
  opens (`page_count` 4.6 / 22 and 0.7 / 9.7), pdfium not until a page is
  asked for, and every call that opens the file pays it.
- **Rendering is a tie through the server's path.** The call opens the
  file, renders, copies the pixels out and encodes the JPEG in Pillow; the
  whole takes 15 to 46 ms, and on seven of the eight files the two are
  within 3 ms of each other on the full page, on six of them within 5 ms
  on the crop and the middle page as well, each ahead somewhere. pdfium's
  lead on the raw render in the table above (1.3 to 2.8 times) is a few
  milliseconds inside that. The exceptions are the 484-page book, whose
  22 ms open MuPDF pays on every render (23 / 43 ms the page, 9.6 / 27
  the crop), and the 1,310-page book's crop (10 / 17), its open 9.7 ms;
  that book's full and middle page MuPDF renders faster all the same
  (35 / 33, 46 / 40). The pictures come out the same size (the crop a
  pixel or two larger under MuPDF, from rounding), the JPEGs within 6% of
  each other in bytes.
- **Glyph boxes: MuPDF 1.3 to 3.3 times slower, and the difference is
  the wrapper, not the library.** The raw `rawdict` read of a paper's
  first page takes 4.5 ms (5,559 characters); `_glyphs` takes 36 ms over
  it, because it builds a `Rect` and multiplies it by the page matrix per
  character, and PyMuPDF's geometry classes are Python objects, about
  5 µs a glyph. A plain six-float transform would bring the row under
  pdfium's.
- **The occupancy read is equal on the papers** (within 10% on four),
  pays MuPDF's open on the books (40 / 63 ms on the 484-page book, of
  which the open is 4.6 / 22), and is 2.7 times slower on the 32-page
  image-heavy paper (171 / 460 for five pages), where the bbox device
  runs the page's content and pdfium only listed its objects.
  `render_notes` itself is the same under both, within 11%, 114 ms to
  1.2 s: PyPDF2's copy of the whole document is the cost and the one
  page's occupancy a small part of it. The 484-page book is
  AES-encrypted, and PyPDF2 3.0.1 refuses it without PyCryptodome under
  both — the export's matter, not the reader's.
- **The outline** comes faster from MuPDF where there is one (43 / 29 ms
  for 529 entries, 20 / 9.6 for 46), its open included.
- **The GIL.** Beside twenty renders of the 32-page paper's first page, a
  Python thread spinning in a tight loop kept 97% of its idle rate under
  pdfium and 82% under MuPDF (65% in the bare-render test above). The two
  fractions are not comparable: each is taken over the time the twenty
  renders took beside the spinner, and the spinner sets that time — a
  thread that never blocks hands the GIL over only at the switch
  interval, and pdfium's call returns to Python more often, so the
  renders took 8 times longer beside it under pdfium (546 → 4,391 ms for
  the twenty) and 4.6 times under MuPDF (479 → 2,201). The figure in the
  same runs that does not depend on that window is the time the spinner
  was shut out per render, (1 − fraction kept) × window / 20: 18 to 20 ms
  of MuPDF's 24 ms call against 6 to 9 ms of pdfium's 27 — rough figures,
  a rerun of the same spinner giving 12 to 25 and 15 to 21. A thread that
  sleeps and wakes every millisecond, which is what an idle event loop
  does, meets it as one stall, which a rerun repeats within 2 ms: beside
  the same renders (which ran at full speed beside it) it saw a 15 ms gap
  per render under MuPDF, the lock-held open, render and pixel copy end
  to end, and 2 ms at most from that part under pdfium; Pillow's
  `frombuffer` and `convert` add one 6 ms gap per render under both, the
  JPEG encoder alone releasing the GIL. That is the 20 ms stall the
  Threads section describes.
- **Memory.** Python alone is 18.5 MB resident; pypdfium2's import adds
  11.5 MB, PyMuPDF's 29.5 (131 and 124 ms); 73 / 92 MB after the
  484-page book's text walk (peaks 105 / 124); 139 / 206 MB after every
  workload on all eight files (peaks 236 / 293). PyMuPDF holds about
  20 MB more through the imports and the walk and 67 MB more at the end.

The text. Over the first three pages the difflib ratios are 0.93 to 1.00
(1.00 on the three books' front matter), word counts within 5%, no U+FFFD
from either, no ligature characters from either (the server's flags turn
MuPDF's off). Three differences recur. The hyphen at a line's end: MuPDF
keeps every one, "effi-" at the line's end and "cient" at the next's (72,
28, 20 and 32 such lines on the four papers that have them); pdfium joined
each into one word with U+0002 where the hyphen was ("effi\x02cient"),
the only control characters in its text on those four, 72, 28, 20 and 33
of them, none in MuPDF's — not the clean "transduction" the earlier test
saw on another paper. The 17-page paper has no such lines but gives
control codes from both libraries alike, three from pdfium (U+0001 to
U+0003) and four from MuPDF (U+0000 to U+0003), all on its third page:
the large brackets and parentheses of two displayed formulas, glyphs of
TeX's CMEX10 with no Unicode mapping, whose raw codes both pass through.
Lines: pdfium ends them in "\r\n", MuPDF in "\n", and MuPDF makes
more of them (418 against 302 on the 32-page paper, 611 against 364 on the
8-page one), a line of its own for each superscript and subscript,
affiliation mark and index, where pdfium runs them inline, so a subscript
can come out on the line before its base letter. Spacing: pdfium puts a
space before an index ("x (i)") and, on an author line, a line break
between a surname's affiliation mark and the comma after it ("Name1" ends
one line, "," begins the next); MuPDF keeps "x(i)" and "Name1," but
splits "=280" into "= 280". The CJK case stays unmeasured: the library
has no CJK file.
