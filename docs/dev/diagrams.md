# Diagrams

Notes and AI replies render two kinds of closed Markdown fence as figures:

- `mermaid`: Mermaid source (flowcharts, sequence, state, class diagrams …),
  rendered by the bundled Mermaid library.
- `svg`: one complete `<svg>` document, sanitized and shown as an image. It
  is for the free-form figures Mermaid cannot express and models draw
  anyway, such as an energy-level scheme with arrows and labels or a
  geometry sketch.

The default chat prompt tells the model it may answer with either fence
([ai.md](ai.md)). Use `/mermaid` in a note to insert a starter flowchart;
there is no slash command for SVG, which comes from a model or a paste.
Ordinary code blocks render as highlighted code, and only those two
languages (in any case) become figures: an `xml` or `html` fence holding an
`<svg>` stays code, and so does an HTML `<pre><code class="language-svg">`
written into a note.

Both kinds behave the same everywhere. Only the drawing step differs.

## A diagram in a note

A note's diagram is an object like a picture: a click selects it, and a
double-click or double tap opens the source editor, as does its menu's
"Edit markdown source" (the row names the double-click; the menu opens on a
right-click, a held finger or the toolbar's "⋯"). Leaving the editor
renders the updated diagram. The double-click is free for the source
because a diagram's body has no editing of its own: a picture zooms on
double-click, a table edits the cell under the pointer. The toolbar and the
resize grips keep their own double-clicks (`editor/MdObject.jsx`, object
kind `"diagram"`). Hovering a diagram, or selecting a note's, shows its
toolbar, the same flat icon buttons as a note image's strip: zoom, show
source (`</>`), copy source, download SVG, and in a note the object menu's
"⋯". Zoom is there only while the drawing is shown.
The menu's "Copy image" copies the figure as drawn (`shared/lib/copyImage.js`,
shared with note pictures): its SVG at the shown size on the card's
background, so a Mermaid figure keeps its theme's colours and a drawing its
white page, plus a PNG of it at twice that size for apps that paste only
bitmaps. A figure showing its source or an error has nothing to copy, and
the row is left out.

A note's diagram resizes like a note image: a grip on each side
(`shared/ui/ResizeGrip.jsx`, shared with `MdImage`) drags the width. The
figure is centred, so the width changes by twice the pointer's travel, and a
double-click or double tap on a grip restores the natural size. The size is
stored in the fence's info string after the language, `` ```mermaid width=420 ``
or `` ```svg width=420 ``, which other Markdown renderers ignore, so the
source stays portable (the diagram analogue of the Obsidian `![alt|420]`
image size). The figure hugs the drawing even without a stored size, so the
grip always sits at its edge: a Mermaid figure reads the `max-width` cap
Mermaid puts on its SVG, an SVG figure the width the sanitizer reports.
Read-only views and chat replies show no grip.

## Zoom

The toolbar's zoom opens the figure in the lightbox note images use
(`shared/ui/Lightbox.jsx`): over the page, closed by a click anywhere or
Escape. In a chat reply or a read-only note, a click on the drawing zooms it
as well, like a read-only note image; in an editable note that click
selects the object. The zoomed picture is one SVG document made the way
"Copy image" makes it (`shownSvg` and `shownBackground` in
`shared/lib/copyImage.js`): the figure on the background it is shown on, so
a Mermaid figure keeps its theme's page and a drawing its white one, sized
by `lightboxFit` to fill 94% of the window's width or 90% of its height.
Being a vector, it is sharp at that size. It is made from the markup the
figure shows: Mermaid's SVG as the renderer serialized it, or an SVG
fence's sanitized drawing.

## The fence layer

`shared/lib/diagramMarkdown.js` finds and edits the fences:

- `scanDiagramFences(text)` lists a note's diagram fences in source order,
  each with its `kind` (`"mermaid"` or `"svg"`), the opening line's range,
  its info string split into `lang`, `meta` and `width`, and where the whole
  fence ends (`end`, `closed`). Fences in quotes and list items count, and
  both backticks and tildes are supported.
- `remarkDiagrams` marks each diagram fence in the rendered HTML as
  `data-diagram-kind`, `data-diagram-pending` (the closing fence has not
  arrived yet) and `data-diagram-width`. A streaming reply therefore waits for
  the closing fence instead of drawing half a diagram.
- `setDiagramWidth(text, idx, width)` rewrites only the nth diagram's
  opening line; a stale index edits nothing.
- `diagramFence(kind, source)` writes a fence long enough that backticks in
  the source cannot close it (selection copy).
- `mapOutsideCodeFences` and `normalizeChatMarkdown` keep math rewrites out
  of fenced code, so diagram labels and SVG text stay as written.

The two kinds share one index sequence: the nth diagram of a note, whatever
its kind, is the nth fence `scanDiagramFences` reports and the nth figure
`BlockMarkdown` renders (`editor/BlockTree.jsx` keeps one counter). The
object frame, its menu and drag (`findObject(content, "diagram", idx)`), and
`setDiagramWidth` all use that index, so a note mixing both kinds edits the
right fence. It is the same nth-construct idiom as images and tables in
`editor/MdTools.jsx`.

## The figure

`shared/ui/DiagramFigure.jsx` is shared by `editor/BlockTree.jsx` and
`shared/ui/Widgets.jsx` (chat, note tooltips, and other chat-Markdown
consumers). `diagramCodeProps(children)` reads a rendered fence from the
`data-diagram-*` props `remarkDiagrams` put on its `<code>` (the one source
of the kind, so the rendered diagrams line up with `scanDiagramFences`), and
`DiagramFigure` draws it: the toolbar, the "Waiting for the diagram to
finish…" status while the fence is open, the source toggle, the width grips,
and on failure the error card ("Could not render diagram." and the reason)
with the source underneath. The card carries `data-diagram-kind` and the
original `data-diagram-source`, so copying a selection over it yields the
fence again (`fragmentToMarkdown` in `Widgets.jsx`). The download is named
`diagram.svg`. Styles are in `shared/ui/diagram.css` (`.diagramCard`,
`.diagramTools`, `.diagramPreview`, `.diagramFigure`, `.diagramSvg` (Mermaid's
inline SVG), `.diagramImg` (an SVG fence's image), `.diagramStatus`,
`.diagramError`, `.diagramSource`).

A Mermaid figure follows the app's computed light/dark color scheme
(`data-diagram-theme`). An SVG figure has no theme: a hand-drawn SVG almost
always assumes a white page (black strokes, no background of its own), so
its preview area is white in every theme, inside the same bordered card, the
way a note image shows as it is (with light scrollbars to match). A
`prefers-color-scheme` rule inside the drawing follows the browser's own
preference, not the app's theme: Chromium does not pass an image's
`color-scheme` on to the SVG document it loads.

## Mermaid

`shared/lib/mermaidRenderer.js` loads the bundled Mermaid library on demand;
there is no CDN or rendering service. It serializes initialization/rendering
because Mermaid configuration is global, gives every SVG a unique ID, and drops
results belonging to an edited or unmounted component. Temporary measurement
containers are removed even after parse failures. Strict security, HTML label
layout, suppressed automatic error diagrams, a 50,000-character limit and a
500-edge limit are locked against diagram configuration overrides.
SVG anchors are unwrapped after rendering, and Mermaid event bindings are never
installed, so diagram links and callbacks stay inactive in previews/downloads.

Mermaid returns its SVG as HTML: a label's `<br>`, `<img>` or `&nbsp;`
is not XML. The renderer therefore reads the result with the HTML parser,
as the page itself would (a parsed document is inert: nothing in it loads
or runs), and writes it back with `XMLSerializer`. The preview, the download
and the zoom all get the same well-formed SVG document. An XML parse would
put the browser's error page ("This page contains the following errors")
into the drawing.

Stale SVG geometry: Mermaid measures each label inside a `foreignObject`
first sized up to 16384 px, shrinks the box and shifts its group, then lays
the node out from the node group's `getBBox()`. Some Chrome installations
keep answering `getBBox()` with the geometry the elements had when first
laid out, so every node grows by its measuring box and the diagram is drawn
many times too large, its nodes as dots
([research note](../research/mermaid-stale-geometry.md)).
`shared/lib/svgGeometry.js` probes for this once per page
(`staleSvgGeometry`); where it holds, the render runs under
`withComputedBBox`, which answers `getBBox()` inside the render container
from the DOM itself: `foreignObject` and simple shapes' attributes, each
child's transform list, and the browser's box only for paths, text and
other leaves. Other browsers keep the native measurement.

Math in flowchart and sequence labels: Mermaid itself typesets only its
`$$...$$` delimiters, so `mermaidMath` in `shared/lib/diagramMarkdown.js`
upgrades the note editor's `$...$` spans to that form just before
`mermaid.render` (a same-line pair whose content hugs both dollars and is not
followed by a digit — prices and `\$` stay text). Both
`A["$a_1$"] -->|"$J$"| B["$$a_2$$"]` forms therefore render. HTML label layout
is enabled so Mermaid can embed KaTeX's MathML in SVG `foreignObject` elements.
Labels remain sanitized by strict security mode. The stored source and its
copies keep the delimiters as written.

## SVG

An SVG fence's markup is untrusted: a model writes it, and a model can be
steered by the PDF or web page it read. Placed in the page, such markup
could run script, load a tracking image that carries data out, or restyle
the app around it through a `<style>` element, which inside an inline SVG is
a page-wide stylesheet.

So the figure shows the drawing as an image: `<img class="diagramImg">`
with a `data:image/svg+xml` URL of the sanitized markup. The browser loads
an SVG image as a document of its own: it runs no script, loads nothing
from elsewhere, and its `<style>` rules and ids apply inside it only, so two
drawings that both define `<marker id="arrow">` keep their own. That
isolation is the browser's, not a rewrite of the markup. The cost is what an
image cannot do: the drawing does not use the page's web fonts (it falls
back to the system's `sans-serif` and the like), and its text cannot be
selected or found with the browser's search. The `<img>` is not draggable
on its own, so in a note the object frame stays the drag source.

`sanitizeSvg(source)` in `shared/lib/svgSanitize.js` is the second layer. It
keeps the markup inert outside an image too (the downloaded file), and
returns `{ svg, width, height }` or throws a sentence the figure translates:

- [DOMPurify](https://github.com/cure53/DOMPurify) with its SVG and SVG
  filter profiles only, no HTML. Its SVG allowlist leaves out `<script>`,
  `<foreignObject>`, `<set>` and `<animate>`, and it drops `on*` handlers,
  `javascript:` URLs and animations of `href`. The sanitizer adds to that:
  no `<a>` (links), `<image>` (outside pictures) or `<font>` (an HTML
  breakout tag when the markup is parsed again), and no `data-*` attributes.
  It allows `<use>`, which models often draw with, because its `href` is
  held to the drawing like every other. `<animateTransform>` and
  `<animateMotion>` stay, but DOMPurify's attribute list has no `from` or
  `to`, so an animation moves only through `values` or `by`. The sanitizer
  runs its own DOMPurify instance, because hooks are per instance and
  Mermaid sanitizes its labels with the shared default one.
- `href` and `xlink:href` must point at an `#id` inside the drawing.
- No attribute and no `<style>` may hold CSS that could load anything: a
  `url()` other than `url(#id)`, `@import`, `image-set()`, `expression()`,
  `element()`, `src()`, or a backslash (a CSS escape can spell any of
  those). The check reads the text as it is written out, comments included,
  so a comment that mentions `url(` is refused too. An offending attribute
  is dropped, an offending `<style>` removed whole.
- The markup must sanitize to exactly one root element, an `<svg>` ("Not an
  SVG document."), with something left inside it ("The SVG draws
  nothing."). An XML prolog and a doctype are fine. The source is capped at
  50,000 characters, as for Mermaid.
- The result is a standalone document: `XMLSerializer` writes the root's
  `xmlns`, and the sanitizer declares `xmlns:xlink` on the root whenever an
  `xlink:` attribute is left, since a model often omits it.
- Scaling like Mermaid's output: when the root has numeric `width` and
  `height` (unitless or px) but no `viewBox`, the sanitizer adds
  `viewBox="0 0 w h"`, so the drawing scales with the figure instead of being
  cropped. The `width` and `height` attributes stay, but `width`/`height`
  (and `min-`/`max-`) declarations in the root's `style` are removed. The
  natural size it reports is the `width` and `height` attributes, else the
  viewBox's. The figure takes the width, and the image both, so the box keeps
  its shape before the image has decoded.

"Download SVG" saves the same sanitized string the image shows.

DOMPurify is a direct dependency, one copy shared with Mermaid. It is
imported statically, so a drawing appears without waiting for a chunk, and
it is part of the main chunk (about 29 KB minified, 11 KB gzipped). Its
unit tests (`tests/svgSanitize.test.mjs`) run under plain node, with
`jsdom`, a dev dependency, supplying the browser DOM DOMPurify needs.

## Storage and export

A diagram is stored as its Markdown fence: there is no block type, asset
upload or database table for it. Markdown export/import keeps the source.
PDF exports print a diagram's fence as a code block, and the note editor
(CodeMirror) shows the source while the block is being edited.

## Validation

From `frontend/`:

```sh
node --test tests/diagramMarkdown.test.mjs tests/svgSanitize.test.mjs tests/copyImage.test.mjs
npm run build
npm run e2e -- --group diagrams
```

The browser scenarios (`tests/e2e/scenarios/diagrams.mjs`) cover notes,
editing and reload, the slash command, chat streaming, multiple diagrams,
errors, source copying, SVG downloads, theme changes, locked Mermaid
security settings, ordinary code, Markdown round trips, the width grip
(drag → `width=N` stored and kept across a reload, double-click clears it),
and a subgraph with math laid out as in a healthy browser where
`getBBox()` is made to keep a label's measuring box.
For SVG they cover a note mixing an SVG, a Mermaid and an invalid SVG fence
(two drawings and an error card), the drawing loaded as an image with its
natural size, none of its markup, ids or style in the page, script and
handlers gone from the image and the download, the white page in both
themes, source copying, Copy image (the drawing and a flowchart as an SVG at
the shown size plus a drawn PNG twice that), the grips writing `width=N` on the right fence of
the shared index, a streamed reply that waits for the closing fence, two
drawings with the same ids in one reply, and a copied reply that keeps its
`svg` fences.
