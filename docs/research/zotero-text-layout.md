# How Zotero's reader lays out a text annotation (October 2026)

Why a text annotation synced from Zotero broke its lines at other words in
Gamma, what Zotero's reader does with a text annotation in each product,
and why a box synced with Zotero takes Zotero's layout. The mechanics are
in [dev/text_boxes.md](../dev/text_boxes.md#zotero-boxes) and
[dev/zotero_sync.md](../dev/zotero_sync.md#text-boxes); what Zotero reads
and writes in a PDF is in [zotero-annotations.md](zotero-annotations.md).

## How it was found

- **The reader's code**, read at the commits each product pins: zotero/reader
  `master` (293d7e1, 2026-10-29); Zotero 7.0 desktop (reader 7f32b0e,
  Firefox 115 ESR); Zotero 10.0 desktop, the current release 10.0.7
  (reader 3c83fed, Firefox 140 ESR); zotero/zotero `main`, the next desktop
  release (reader 678994c); and the web library's `master` (reader
  fd9e949). The `viewer.css` zotero.org serves is byte-identical to
  master's `pdfjs/viewer.css`, and its `reader.js` holds master's measuring
  code. Also the reader's pdf.js submodule (zotero/pdf.js 5f95f71), the
  pdf-worker that writes Zotero's exported PDF, and the textarea rules of
  Firefox's `forms.css` and Chromium's `html.css`.
- **The fonts** on a Windows machine: ascent, descent and line gap with
  fontTools, advance widths shaped with HarfBuzz (kerning on, as Chrome
  shapes) and checked against PyMuPDF's `Font.text_length`.
- **A user's library**: three text annotations as the Web API returned
  them (GET only), and the blocks a two-way link had made of them, read
  from the server's databases opened read-only.
- **Zotero's sizing replayed**: `text-annotation.js` `measureWidth` and
  `measureHeight` run on those widths, keystroke by keystroke, at the web
  reader's scales.

## The reader's layout

The reader draws every text annotation, to view it and to edit it, as one
`<textarea class="textAnnotation">` in the PDF page's DOM over the canvas
(`src/pdf/page.js` `_renderTextAnnotations`). Its left, top, width and
height are the rect's, and its font size the annotation's `fontSize`, each
times pdf.js's `--scale-factor` (zoom × 96/72), so the text keeps its size
against the rect and the page at any zoom.

| | Web library (zotero.org), and Zotero desktop from its next release | Zotero desktop 7.0 to 10.0.x |
|---|---|---|
| Font | the computed `font-family` of the reader's `<body>` (`window.computedFontFamily`), the literal stack `-apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif` (`_base.scss`) | the same to draw; Zotero 7.0 measured in `"Lucida Grande", "Lucida Sans Unicode", "Lucida Sans", Geneva, -apple-system, sans-serif` |
| Line height | none set: the textarea's `line-height: normal`, the font's ascent + descent + line gap | the same |
| Inset | none: `.textAnnotation` has `padding: 0; margin: 0; border: none; box-sizing: border-box`, so the content box is the rect | the textarea's own padding, 2 CSS px on every side whatever the zoom, and Gecko's `margin-block: 1px`, so the text wraps 4 px narrower than the rect |
| Wrap | the textarea's `white-space: pre-wrap` and `word-break: break-word`: words wrap, a word wider than the box breaks anywhere, spaces and `\n` are kept, a space at a line's end hangs | the same, at the narrower width |
| Height | the rect's, `overflow: hidden`; re-measured only when the reader's own user edits the text, resizes the box or changes its size | the same |
| Measured at | the rendered scale, in the pdf.js iframe | 1 px per point, in the reader's top document |

The inset differs because reader commit 65c3ebd ("PDF: Measure text
annotations at rendered scale", 2026-08-22, reader issue #243) added
`margin: 0` and `padding: 0` to `.textAnnotation` and moved measuring to
the rendered scale. Desktop builds up to 10.0.x predate it.

Other paths draw a text annotation otherwise:

- **Print and thumbnails** draw it on a canvas (`render.js`
  `drawAnnotationsOnCanvas`): words split on spaces only, the trailing
  space counted, `measureText`, a line height of 1.2 em, a `\n` drawn as a
  space.
- **The exported PDF** (pdf-worker `write.js`): a `/FreeText` whose
  appearance is drawn in an embedded Liberation Sans (Arial's widths)
  although `/DA` names Helvetica, 1.2 em a line, no inset, a long word not
  split, the size cut by 0.5 pt (down to 4 pt) until the lines fit the
  rect, and no appearance at all for a comment holding `\n` or a character
  outside Liberation Sans. `/Zotero:Rect` is the unturned rect, which
  Zotero reads back.

## How the rect is made

- **A click** with the Text tool makes a rect `fontSize` square around the
  pointer, rotation 0. The tool's default size is 14.
- **Typing** re-measures the rect (`adjustTextAnnotationPosition`). A box
  less than 2 × `fontSize` tall is one line and takes the text's width:
  `measureWidth`, which is the textarea's `scrollWidth` + 1, plus 5 more,
  so the text's width + 6, up to 300 pt, then it wraps there. A taller box
  keeps its width and takes `measureHeight`, the textarea's `scrollHeight`
  at that width. The box grows right and down from its top-left.
- **A side handle** keeps the width and re-measures the height; on release
  a box that now fits on one line snaps to its text's width + 6. **A corner
  handle** scales the font with the box, in 0.5 pt steps, and measures
  nothing. A new size from the menu keeps the width and re-measures the
  height. The narrowest box is 10 pt (`MIN_TEXT_ANNOTATION_WIDTH`), the
  menu's sizes 6 to 192.
- **A synced annotation** is drawn with its rect as it comes and never
  re-measured (`annotation-manager.js` `setAnnotations`): a rect too short
  cuts the last line off, one too narrow wraps there.

So a rect is the text's own extent at the reader's normal line height,
with no inset in the reader since 65c3ebd.

### The stored rects replayed

At the web reader's 125% zoom, 5/3 CSS px per point, with Segoe UI's
advances and Chrome's whole-pixel line boxes, the replay gives exactly what
Zotero stored:

| Item | Comment, size | Width | Height |
|---|---|---|---|
| 3LQYSDYS | `from zotero\nonline edit` (typed over two lines), 14 pt | round(121.68 px) / (5/3) + 1 = **74.2** | 2 lines × 31 px / (5/3) = **37.2** |
| APBJTDAC | `some tests`, 14 pt | round(110.08 px) / (5/3) + 1 + 5 = **72.0** | 31 px / (5/3) = **18.6** |

The widths come from fractional advances, so Chrome on Windows lays the
text out at the same width against the page at any zoom. Replayed at
1 px per point, Arial predicts 71.80 × 31.28 and 73.69 × 15.64 for the same
two, so the reader measured in Segoe UI.

## The widths

What the reader's stack resolves to:

- **Windows**, Chromium or Gecko: Segoe UI. `-apple-system` is a macOS
  alias and `BlinkMacSystemFont` Blink's.
- **macOS**: the system font, San Francisco, whose optical size follows the
  pixel size, so its widths can shift with the zoom. Not measured.
- **Linux**: `Helvetica` through fontconfig's metric aliases (Nimbus Sans,
  TeX Gyre Heros), else Arial's (Liberation Sans, Arimo), else the
  `sans-serif` default. It depends on the distribution. Not measured.
- **The iPad**: Zotero for iOS may not draw PDFs with the web reader. Not
  checked.

Line heights at `normal`, from the font files: Segoe UI 1.330 em (ascent
1.079 em), Lucida Sans Unicode 1.537 em. Chromium rounds
the ascent and the descent each to whole pixels, so a Segoe UI line takes
1.31 to 1.39 em by size and zoom: 25 px at 18.67 px (14 pt at 100%), 31 px
at 23.33 px (14 pt at 125%).

Advances at 14 pt, in points:

| Text | Segoe UI | Arial | Helvetica (AFM) |
|---|---|---|---|
| from zotero | 73.01 | 70.80 | 70.80 |
| online edit | 65.69 | 63.83 | 63.81 |
| some tests | 66.05 | 67.69 | 67.69 |
| from gamma (12 pt) | 68.53 | 67.35 | |

Segoe UI and Arial are the same width on average over the sentences tried
(a ratio of 0.9967), but single letters differ both ways (in em, Arial
against Segoe UI): s 0.500 / 0.424, a 0.556 / 0.509, z 0.500 / 0.452, but
t 0.278 / 0.339, f 0.278 / 0.313, o 0.556 / 0.586. A line that nearly
fills its width breaks differently in the two.

## Gamma against it, as found

A Gamma box draws in `Helvetica, Arial, …` (Arial on Windows) at 1.25 em a
line, padded 4 pt on every side, and the link took Zotero's rect as the
box's outer edge, so a fixed box wrapped at the rect's width less 8 pt:

- `from zotero\nonline edit` in a 74.2 pt rect wrapped at 66.2 pt: "from
  zotero" (70.80 in Arial, 73.01 in Segoe UI) fits 74.2 and not 66.2, so
  Gamma showed "from" / "zotero" / "online edit" where Zotero shows two
  lines. The font was not the cause.
- `some tests` in a 72 pt rect wrapped at 64 pt, under Arial's 67.69, so
  Gamma showed two lines where Zotero shows one.
- An auto box made in Gamma ("from gamma", 75.34 pt: Arial's 67.35 + 8)
  went to Zotero at its outer width, and the 8 pt of padding were the slack
  Segoe UI's 68.53 needed.

Ten sentences at widths 30 to 319 pt and sizes 10 to 16, Arial against
Segoe UI, broke at the same words in about 60 to 65% of the cases with
the rect as the outer edge, and in about 84 to 90% with the rect as the
text area. The rest is the face.

## What was decided

| Option | What it does | Same breaks on one machine |
|---|---|---|
| A. Mapping only | the rect grows by the padding coming in and shrinks by it going out | 84 to 90% on Windows; the rest differ by face |
| **B. Zotero's layout per box** | a box synced with Zotero is drawn as the reader draws it: the rect is the box, no padding, the reader's stack, `line-height: normal` | all, by construction, wherever both resolve the stack alike |
| C. Metric fitting | the server computes Zotero's breaks from a Segoe UI width table and picks a Gamma width that breaks alike | 99.4% on Windows only; wrong on macOS and Linux |
| D. Per workspace | every box in a linked workspace takes Zotero's layout | as B, but every box moves and may rewrap when a link is made or removed, and copies and exports need the workspace's state |

B was built: the `zotero` key on a text box. It also needs no change to
the rect mapping, which was already the identity, so a box synced before
gains the key with no geometry rewritten and nothing sent. Along with it:

- A box made in Gamma takes Zotero's look when it first goes to Zotero,
  its text area becoming the rect. Kept as it is, it would match about
  86% of the time.
- An auto box goes 1 pt wider than its measured text, as `measureWidth`
  adds 1, since Zotero has no auto width; a rect the server makes allows
  1.4 em a line, Segoe UI's 1.33 plus Chrome's rounding, so Zotero cuts no
  line off.
- Blank lines travel both ways, each a line, as in Zotero's textarea.
  Leading spaces and runs of spaces stay lossy.
- Markdown keeps rendering in a Zotero box; the match is for the plain
  text Zotero holds.
- The PDF keeps Helvetica and 1.25 em rows, since the server has no other
  metrics and the PDF only has to fit its own appearance. Zotero reads
  `/Zotero:Rect`, not the appearance. Zotero's own export has the same
  split between its screen and its PDF.
- Gamma follows the web library and the reader since 65c3ebd, with no
  padding, and makes up nothing for desktop 7.0 to 10.0.x, since Gamma
  cannot tell which Zotero will open an annotation.
- Gamma's limits stay (6 to 96 pt, at least 24 pt wide), against Zotero's
  (up to 192 pt, at least 10 pt wide). A 14 pt one-character annotation
  widens to 24 pt here, which matters only for text that wraps under 24
  pt; the two bases keep it from reading as an edit.
- Boxes imported from Zotero PDFs before the key, in a workspace never
  linked, stay Gamma boxes: a numbered workspace step would hold every
  clone until both sides ran it, and a link converts them anyway.

## Still open

- **macOS, Linux and the iPad** are not verified. The browser suite's step
  compares a box with a textarea styled as the reader's on the machine it
  runs on, and has run on Windows only. SF's size-dependent tracking may
  move a nearly full line between zoom levels, in Zotero too.
- **Zotero desktop** draws with Gecko; its breaks were not measured
  against Gamma's Chromium. Desktop 7.0 to 10.0.x wraps 4 px narrower than
  the rect until it ships 65c3ebd.
- **Two machines** whose stacks resolve to different faces cannot match;
  Zotero's own clients do not either.
- **Heights.** Chrome's whole-pixel line boxes make a measured height vary
  by about 0.05 em a line between zoom levels, in Zotero too. Zotero cuts
  off what overflows a synced rect until its user edits the box.
- **A box converted on the server** keeps its old outer width as slack
  until its next edit, where a line longer than about 250 pt that Segoe UI
  makes more than 8 pt wider than Helvetica could still wrap in Zotero.
