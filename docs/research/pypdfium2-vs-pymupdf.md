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
