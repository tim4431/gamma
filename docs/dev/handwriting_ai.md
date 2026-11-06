# Handwriting and AI

What the lasso selection's **Convert** menu does with the selected
strokes. Redraw as shapes asks the chat's model which strokes are parts of
figures and what clean figure each group of them is, and puts shape strokes
in their place. Convert to text asks it to read the handwriting, and puts a
text box in its place. Copy as SVG and Download SVG need no AI. The menu's
first item, Transcribe in the chat, is the chat's
([handwriting.md](handwriting.md#what-the-user-sees) "Transcribe a
selection"). The stroke file, its shape records and the selection menu are
[handwriting.md](handwriting.md); the providers, the call and the shared
allowance are [ai.md](ai.md); the text box is
[text_boxes.md](text_boxes.md).

Code:

- Client, in `frontend/src/`: `ink/inkAi.js` (pure: the request and the
  edits an answer makes), `ink/inkAiClient.js` (the request itself),
  `ink/inkSvg.js` (pure: the SVG) and `shared/lib/copyImage.js` `copySvg`.
  The wiring is `ink/InkLayer.jsx` (`InkSelectionMenu`'s Convert menu,
  and `InkAiWork`, the wash while the AI works), `ink/useInkSelection.js`
  (`handleInkAction`, `inkAiWork`), `ink/inkHistory.js` (the linked
  step's pill), `markup/MarkupLayers.jsx` and `app/App.jsx`, which gives
  the hook its inputs and the layers the wash.
- Server: `gamma/ink_ai.py` (pure but for drawing the picture: the digest,
  the picture, the prompts, reading the replies) and
  `gamma/routers/ink_ai.py` (the two routes).
- The `curve` shape kind the redraw writes: `ink/shapes.js` and
  `gamma/ink.py` ([Curves](#curves)).

Tests are listed [below](#tests).

## What the user sees

- **Convert** (sparkles; its title "Transcribe, redraw or convert the
  selected handwriting") is in the selection menu's first row, after Copy
  ([handwriting.md](handwriting.md#what-the-user-sees) "selection menu").
  It opens a menu under it:
  - *Transcribe in the chat*: the chat reads the strokes.
  - *Redraw as shapes* and *Convert to text*, the AI's two edits.
  - After a divider, *Copy as SVG* and *Download SVG*.
  - A selection of pictures alone greys Convert, and one of highlighters
    alone greys Redraw as shapes and Convert to text, which take pen
    strokes only. Their titles then say why: "Redrawing takes pen strokes;
    highlighters are left as they are" and "Converting takes pen strokes;
    highlighters are left as they are".
  - Where the clipboard cannot take a picture (plain http, not localhost;
    `copyImage.js` `canCopyImage`), Copy as SVG is greyed, as Copy image
    is, its title "Copying an image needs a secure connection (HTTPS or
    localhost)". Download SVG works there.
  - The menu closes when the selection menu moves (a scroll, a zoom, the
    selection's box changing) and when the More row opens or folds.
- **Redraw as shapes** sends the selected pen strokes to the chat's model,
  which picks out the figures: lines, arrows, rectangles, circles and
  ellipses, triangles and other polygons, curves, axes and plotted graphs.
  Each figure's strokes become one clean shape, a shape stroke like the
  Shapes tool's, with handles to reshape it.
  - Words, numbers, formula symbols and marks the model is unsure of stay
    as drawn. Highlighter strokes never reach the model and are never
    changed.
  - The model tidies as it redraws: nearly level or plumb lines exactly
    so, nearly right angles right, near-equal sizes equal, nearly aligned
    edges aligned, and the ends of a line that touches a shape exactly on
    its outline.
  - A shape takes the colour, opacity, line, width (held to the pen's
    sizes) and fill of the first stroke it replaces, and that stroke's
    place in the drawing order. A replay draws it when that stroke was
    drawn.
  - While the model works, the strokes sent shimmer under a wash
    ([While the AI works](#while-the-ai-works)), the pill says "Redrawing
    the selection as shapes…" and the tasks tray has a row, "Redraw as
    shapes — {page title}" (the label alone on a page without one), whose
    Stop cancels the request: "Stopped: the handwriting stays as it
    was.", and an answer that still comes changes nothing.
  - The answer is applied at once as one undo step, and the new shapes
    become the selection while the one asked about still is. The pill says
    "Redrawn as 3 shapes." with Undo. A shape whose strokes were changed
    or erased before the answer came is left out, and the pill adds "1
    skipped: the strokes changed meanwhile."
  - With no figure found: "The AI found no shapes to redraw in the
    selection." With every figure's strokes changed: "Nothing was
    redrawn: the selected strokes changed meanwhile."
  - More than 300 pen strokes are refused before anything is sent: "Too
    much handwriting to redraw at once; select less." So is a selection
    past what one stroke file holds (5000 strokes, 500 000 samples or
    6 MB, its groups together), with that sentence or Convert to text's
    "Too much handwriting to convert at once; select less."
- **Convert to text** sends a picture of the selected pen strokes to the
  chat's model, which transcribes it: Markdown, line for line, math as
  LaTeX (`$…$`, a displayed formula as `$$…$$`), `[illegible]` for a word
  it cannot read.
  - The strokes go, and one text box with the transcription takes their
    place: at their top-left corner, as wide as the handwriting, its font
    size the handwriting's line height and its colour the ink's.
  - One undo brings the strokes back and takes the box away. The pill says
    "Converted the handwriting to text." with Undo.
  - A stroke moved, resized, turned or flipped before the answer came
    stays where it is now, and the box goes on the strokes that are still
    as they were sent. The pill adds "1 skipped: the strokes changed
    meanwhile."
  - While it works: the wash, "Reading the handwriting…", and a task row,
    "Convert to text — {page title}", as for a redraw. With no writing
    found: "The AI found no writing in the selection.", and nothing
    changes. With none of the strokes left as they were sent: "Nothing
    was converted: the selected handwriting changed meanwhile." when some
    changed, else "Nothing was converted: the selected handwriting is
    gone."
  - A model that reads text only cannot do it: "Reading the handwriting
    failed: This model cannot see pictures, which reading handwriting
    needs; pick another model for the chat." A redraw with such a model
    goes on from the strokes' numbers alone.
- **Copy as SVG** puts the selected strokes on the clipboard as a picture:
  a PNG that every app pastes, the SVG's markup as text, and the SVG itself
  where the browser writes one (Chromium). The pill says "Copied the
  selected handwriting as SVG.", or "The browser did not let the
  handwriting onto the clipboard."
- **Download SVG** saves them as `handwriting.svg`. Both draw the strokes
  as the page shows them, in their own colours, and leave the selection's
  pictures out. The ground is transparent, but on a sheet of dark paper,
  whose light ink would vanish where it is pasted: there the SVG carries
  the paper's colour under the strokes.
- The AI's two ask the model the chat uses (the model chip), one request
  at a time on a page: a second one while the first is out says "The AI is
  still working on the last selection…" Leaving the page stops the
  request. A note deleted while the AI works, alone or with its sheet,
  stays deleted: the answer leaves its strokes out.
- A failure says why, in the server's words: "Redrawing failed: …" or
  "Reading the handwriting failed: …" (an allowance used up, a provider's
  error), and the task row is marked failed.
- A shared page's view has no AI: "Redrawing needs the AI, which a shared
  page cannot use." and "Converting handwriting needs the AI, which a
  shared page cannot use."
- There is no preview to keep or discard: the result is the edit, and
  Undo (the pill's, Ctrl+Z, the strip's) takes it back.

## While the AI works

Redraw as shapes and Convert to text show what the AI is changing the way
a PDF paragraph being translated does (`.pdfTransPending.busy`,
[translation.md](translation.md#a-pages-entry)): a wash of the accent over the strokes
sent, shimmering left to right, which fades out once the answer lands.

- `useInkSelection` holds `inkAiWork`, `{page, box}`: the request's
  surface and its pen strokes' `box` (`inkAiRequest`), set when the
  request goes out and cleared when it ends, however it ends. An applied
  answer (`applyRedraw` or `applyConversion` returning true) sets it again
  as `{page, box, landed: true}` for `LANDED_MS` (700 ms). Leaving the
  page clears it.
- App puts it on the page tools as `ink.aiWork`, and `MarkupLayers` gives
  it to the ink layer of that surface, the zoom window's copy included.
- `InkLayer` draws it as `InkAiWork`, a `.inkAiWork` div over the box grown
  by 4 pt (the selection box's padding), above the strokes and under the
  selection's handles, taking no pointer and hidden from assistive
  technology (the pill says what is happening). The box is where the
  strokes were sent from: strokes moved meanwhile leave it there.
- `app.css` gives `.inkAiWork` the translation's shimmer
  (`pdfTransShimmer`), and `.inkAiWork.landed` its landed fade
  (`pdfTransLanded`, 0.6 s). Under reduced motion the wash stands still
  and the fade ends at once.
- A failure, a refusal or a Stop clears it with no fade, and so does an
  answer that changes nothing (no shapes, no writing, the strokes changed
  meanwhile).

## Asking

`handleInkAction("redraw" | "to-text")` (`ink/useInkSelection.js`) runs
`askAboutSelection("shapes" | "text")`, which makes the request and
applies its answer:

1. It refuses, in this order: in a shared page's view (App passes
   `ai: null`), for a selection without a pen stroke ("Select pen strokes
   to redraw." / "Select handwriting to convert."), while the page view's
   request is still out (`aiRef`; the sentence ends in "…", so the pill
   keeps its spinner until the answer's or the Stop's replaces it), and
   with the server's sentences for a redraw of more than
   `MAX_REDRAW_STROKES` (300) pen strokes or a request past one stroke
   file's caps (`requestTooBig`: `MAX_STROKES`, `MAX_SAMPLES` and
   `MAX_BYTES` less 4 KB for the file's wrapper, the pen strokes alone
   counted, as the server counts them).
2. `inkAiRequest(sel, inkOf)` (`ink/inkAi.js`) → `{page, space, strokes,
   refs, box}`, or null when the selection holds no pen stroke:
   - `strokes` are the selected strokes as the groups' drafts hold them,
     saved or not, in `selectedStrokes`' order (each group's in its file's
     order, the groups in the selection's). Every tool goes: the server
     leaves the highlighters out but keeps their indices.
   - A stroke whose id an earlier group of the selection used goes out
     under a fresh id, since the server reads the strokes as one file,
     where an id is unique. The answer names strokes by index, never by id.
   - `refs[i]`, `{group, id, pts}`, is where request stroke i lives, under
     its real id, and the very `pts` array that was sent.
   - `space` is the first group's, and `box` the pen strokes' union
     `strokeBounds` (widths included), where a text box goes.
3. `askInkAi(kind, body, signal)` (`ink/inkAiClient.js`) posts `{model,
   space, strokes}` to `/api/ai/ink/shapes` or `/api/ai/ink/text`. `model`
   is `ai.model`, App's `chatSendModel`. The fetch carries the cookie,
   and the share token on the URL in a share's view (`withShare`), and
   goes through the app's wrapper (`shared/lib/utils.js`), which adds the
   session's user, workspace and link-name headers.
   - A non-OK answer throws `apiError`'s `Error` (`shared/lib/utils.js`):
     the server's `detail` (a proxy's 413 named as such), with `.status`
     and the parsed body as `.data`.
   - The body is NDJSON (`readNdjson`). A `{"ping": 1}` line is skipped,
     an `{"error"}` line throws its message, and the last other line is the
     answer. It resolves null when the stream ends without one.
   - The task row (`ai.addTask({name, kind: "ai", info, cancel})`, the
     tasks store's local row) is named "Redraw as shapes — {title}" or
     "Convert to text — {title}" from `ai.pageTitle` (its first 48
     characters), as the tab's other AI rows are. It holds the cancel that
     aborts the fetch, and `ai.updateTask` settles it as done, failed or
     cancelled.
4. Leaving the page aborts the request. An aborted one (the row's Stop,
   the page left), or one whose page changed or became read-only before
   its answer, changes nothing: "Stopped: the handwriting stays as it
   was."
5. A refusal, an error line or a stream without an answer ("the AI sent
   no answer") is "Redrawing failed: {message}" or "Reading the
   handwriting failed: {message}", the server's own prefix ("redrawing
   failed: ", "reading the handwriting failed: ") taken off the message.
6. Otherwise the hook builds the edit from the groups as they are then
   (`inkNow()`: `inkOf` as `live` holds it, the hook's inputs of its latest
   render, through `inkInTree`), so strokes drawn, erased or moved
   meanwhile are seen as they are
   ([Applying it](#applying-it), [One step for both](#one-step-for-both)).
   A group whose block has left the page's tree (deleted, alone or with
   its sheet) reads as gone: `inkOf` still answers with the draft it left
   behind, and an edit of it would put its block back (`applyInk`).

## The routes

`POST /api/ai/ink/shapes` and `POST /api/ai/ink/text`
(`gamma/routers/ink_ai.py`, plain `def`s, so the call runs in the
threadpool) take `InkAiRequest {model, space, strokes}` and check, in
order:

1. `require_user_id`.
2. The pen strokes alone as a version 2 file (`_pen_ink`): the request's
   strokes without those of another tool (an entry that is no stroke
   object stays, for the parser to refuse), as `{format, version, space,
   strokes}`, go through `ink.parse_ink` as JSON bytes. So its caps (5000
   strokes, 500 000 samples, 6 MB) and its refusal of NaN apply as to an
   upload, and a highlighter counts toward none of them.
   - Past a cap (`ink.InkTooLarge`) is a 413 in a sentence of the route's
     own: the shapes route's "Too much handwriting to redraw at once;
     select less." (`TOO_MANY_STROKES`), the text route's "Too much
     handwriting to convert at once; select less."
     (`TOO_MUCH_TO_CONVERT`).
   - Any other file it refuses is a 400 "invalid handwriting: …".
3. The pen strokes (`ink_ai.pen_strokes`: `tool == "pen"`, which a stroke
   without a tool is) and their indices among all the request's strokes,
   the other tools' included, which is what the answer names. None is a
   400 "the selection holds no pen strokes".
4. The shapes route only: more than `MAX_REDRAW_STROKES` (300) pen strokes
   is a 413 "Too much handwriting to redraw at once; select less."
   (`TOO_MANY_STROKES`).
5. `require_ai_runtime` (a 503 without a provider), then `resolve_model`,
   which takes the account's default model for an id it does not know.
   The model sees pictures unless `ai_catalog.image_input` says it does
   not (`is not False`): an unknown model is sent them, as in the chat
   ([ai.md](ai.md#other-services)).
6. The text route only: a model that does not see them is a 400,
   `CANNOT_SEE`, "This model cannot see pictures, which reading
   handwriting needs; pick another model for the chat." The shapes route
   goes on without the picture.
7. `_check_door` (`routers/translate.py`): a used-up shared allowance, or
   every call slot of the account taken, is a 429 before the stream
   starts.

The answer is always NDJSON, `StreamingResponse(keepalive_lines(…,
"ai_ink_shapes" | "ai_ink_text"))` as `/ai/translate/brief` answers: a
`{"ping": 1}` line in every 15 s of silence, since a reasoning model can be
quiet past a proxy's idle limit, then one line:

- shapes: `{"shapes": [{"kind", "params", "strokes"}]}`, `[]` when nothing
  is a figure ([Reading the reply](#reading-the-reply));
- text: `{"text": "<markdown>"}`, `""` when the picture holds no writing;
- a failure: `{"error": failure_detail(e, …)}`, "redrawing failed: …" or
  "reading the handwriting failed: …", and a call Gamma refused in that
  refusal's own words.

The call is one `call_ai` with the route's system prompt and one user
turn, the picture on it (`image_part`): `max_tokens` 8192, or the chat's
own cap for the model when that is larger (`ai_catalog.reply_cap`: a
service whose models count their thinking toward the cap, where 8192 can
go on the thinking alone), a 180 s socket timeout, one retry of a failure
before any of the reply came, and no effort, so the provider's default.
It runs inside `call_slot(user_id)`. Its tokens are recorded under the
usage kind `ink` (`ai_usage.recorder("ink", …)`,
[ai.md](ai.md#token-usage)), which the shared allowance counts. The
router imports the transport under its own names (`_call_ai`,
`_call_slot`), so a test patches `gamma.routers.ink_ai._call_ai`.

A reply the cap cut off (a stop reason `ai_protocols.base.truncated_stop`
reads as one) is an error, never an answer: "the AI's answer was cut off
at its length limit; select less" (`CUT_OFF`), after the route's prefix. A
transcription stopped partway would replace every stroke with part of
their text, and a shapes object left open would read as no list of
shapes.

**The picture** (`ink_ai.picture`) is the pen strokes alone on blank
white paper the size of their space, cropped around them, with no PDF page
under them and no labels: what is drawn is what was selected.

- The paper is the default paper (`normalize_paper(None)`: white, blank)
  at the space's own width and height, not held to a sheet's 144..2000 pt,
  so a poster's handwriting past 2000 pt is on it, as `ink_view.picture`
  draws the chat's. The crop is `ink_view.crop_box`, the chat's crop of
  handwriting: padded by max(18 pt, 10% of the longer side), at least
  144 pt a side (or the paper's side, when less), inside the paper.
- A pen colour too light to show on white, luminance over `LIGHT_INK`
  (0.8, as `notebook.js` `paperIsDark` reads it: white ink picked for a
  dark slide or dark paper), is drawn in `DARK_INK` (`#1f1f1f`), in the
  picture alone.
- `notebook_pdf` draws it and `pdf_text.render_page` rasterizes the crop
  with its longer side at up to `RENDER_MAX_SIDE` (1568 px), under its 4×
  zoom cap (288 dpi), so a crop under 392 pt comes out smaller (the 144 pt
  minimum gives 576 px).
- It is drawn inside the stream, after the door, so a refused call draws
  nothing.
- When it cannot be drawn, a redraw goes on with the digest alone and a
  transcription ends with "reading the handwriting failed: the handwriting
  could not be drawn as a picture".

## Redraw as shapes

### The digest

The redraw's user turn (`ink_ai.digest`) gives the pen strokes in
numbers, so the model places its shapes by them while the picture shows
what they are:

```
Redraw the figures in this handwriting as clean shapes.
Selection: 412 × 230 pt. Coordinates are points from the selection's top-left corner, x to the right, y down.
The picture shows x from -41 to 453 and y from -41 to 271 of this frame.
Strokes (number: width in pt; box x0,y0,x1,y1; open|closed; points along it):
1: 2 pt; box 10,12,120,80; closed; 10,12 118,13 120,79 11,80 10,13
```

- The frame's origin is the top-left corner of the pen strokes' union box,
  widths included (`ink.bounding_box`). Coordinates are
  whole points, a half rounding up.
- The picture's line says which part of the frame the picture covers. It
  is left out without a picture.
- Strokes are numbered from 1 among the pen strokes, in request order:
  stroke n is request index `offered[n − 1]`.
- A stroke's width is its mean drawn width to 0.1 pt, and its box that of
  its centreline (`ink.stroke_polyline`).
- It is closed when it has more than two samples and its ends are within
  max(6 pt, 12% of its box's diagonal) and within half that diagonal. So a
  tick, a dash or a short V is open, though its ends are within 6 pt, and
  a small loop or a dot, which comes back to its start, is closed.
- Its points are its samples simplified by Ramer–Douglas–Peucker
  (`simplified`) at max(1 pt, 1.5% of the diagonal), the tolerance half as
  much again until at most `DIGEST_POINTS` (24) are left, the ends kept. A
  point that rounds to the one before it is written once.
  - When a step would leave fewer than four (every point standing about
    alike, as in a long zigzag or a scribble), the 24 that stand highest
    are kept instead, the ends among them, in drawing order. So a long
    stroke never comes down to a segment.
  - RDP reads at most `RDP_SAMPLES` (1000) of a stroke's samples, evenly
    spaced, the ends kept (`_thinned`), and fewer the more strokes there
    are: sqrt(`RDP_WORK` / strokes), `RDP_WORK` being 4 000 000 (about 115
    samples each for 300 strokes), never under 96. RDP's work grows as the
    square of what it reads when every split peels off one point (a
    zigzag), and one pen stroke may hold 500 000 samples.
  - The distance is to the segment, not to its line, so a closed stroke,
    whose ends meet, keeps its far side.
  - `importance` reads each point's standing in one pass, and RDP at any
    tolerance keeps the points standing above it (`rdp`). A span whose
    farthest point is within the first tolerance is not split further, so
    a long straight run costs one pass, not one per point.

### The prompt

`SHAPES_SYSTEM` asks the model to tidy hand-drawn figures, with these
rules:

- only strokes that are clearly parts of figures (lines, arrows,
  rectangles, circles and ellipses, triangles and other polygons, curves,
  axes and plotted graphs), never letters, words, numbers or formula
  symbols, not even a label in or beside a figure; when unsure, leave the
  stroke out, and it stays as drawn;
- several strokes may form one shape (a box drawn in four strokes, an
  arrow whose head is a stroke of its own), and each stroke belongs to one
  shape at most;
- tidy what the drawing means (level and plumb lines, right angles, equal
  sizes, aligned centres and edges, ends on outlines), and otherwise stay
  within a few points of it;
- angles in degrees, clockwise, since y points down.

The reply is one JSON object, with no fence and no prose, `{"shapes":
[...]}`, each entry one of:

```
{"kind": "line",     "strokes": [1], "from": [x, y], "to": [x, y]}
{"kind": "arrow",    "strokes": [2, 3], "from": [x, y], "to": [x, y]}
{"kind": "rect",     "strokes": [4], "cx": x, "cy": y, "w": w, "h": h, "angle": 0}
{"kind": "ellipse",  "strokes": [5], "cx": x, "cy": y, "rx": rx, "ry": ry, "angle": 0}
{"kind": "triangle", "strokes": [6], "points": [[x, y], [x, y], [x, y]]}
{"kind": "polygon",  "strokes": [7], "points": [[x, y], ...]}
{"kind": "curve",    "strokes": [8], "points": [[x, y], ...], "closed": false, "smooth": true}
```

An arrow's `to` is its tip, and an optional `head` its barbs' length. A
polygon is any other closed figure with straight sides. A curve takes up
to 31 points, straight segments between them (`smooth: false`: a zigzag, a
plotted graph's line) or a smooth curve through them. A worked example
ends the prompt: a box, an arrow drawn in two strokes leaving it, and a W
written inside, which the reply leaves out.

### Reading the reply

`ink_ai.read_shapes(raw, offered, box)` turns the reply into records of
the stroke file's own kinds and params
([handwriting.md](handwriting.md#the-stroke-file-gamma-ink-v2) "Shape
records"):

- The answer is the last JSON object in the reply that holds a `shapes`
  list, found by `raw_decode` at every `{`, so reasoning text, a fence or
  an earlier draft around it is passed over (as `metadata._answer` reads a
  record). With none, the call fails with "the AI's answer was not a list
  of shapes".
- The reading is lenient where that costs nothing: a kind in any case,
  `rectangle` for `rect`, a `circle` `{cx, cy, r}` as an ellipse, a point
  as `{"x", "y"}`, a bare number for `strokes`, and a closed figure's last
  point dropped when it repeats the first (within 0.5 pt, of four points
  or more).
- Numbers must be finite (a boolean is none, nor an integer past a
  double's range) and points pairs. An entry that is no shape is passed
  over.
- An angle is taken into (−360°, 360°) before it is rounded (`_turn`,
  exact for any finite number), so a huge one cannot overflow the 1/100
  grid and lose the whole reply.
- Each kind's record, made in the digest's frame:
  - `line` and `arrow` `{x1, y1, x2, y2}` (`x2, y2` the tip), an arrow's
    `head` kept only within 2..48 pt. One within 3° (`SNAP_DEG`) of level
    gets both ends their mean y, and one within 3° of plumb their mean x.
    One shorter than 1 pt is skipped.
  - `rect` `{cx, cy, w, h, angle}`: the sides taken positive and over
    0.5 pt, the angle brought into [−45°, 45°) with the sides swapped for
    each quarter turn taken out (`shapes.js` `normaliseRect`'s rule), and
    within 3° of upright made 0.
  - `ellipse` `{cx, cy, rx, ry, angle}`: radii over 0.25 pt, `rx` the
    longer (a swap adds 90°), the angle in [0°, 180°). Radii within 2%
    (`ROUND_RATIO`) make a circle, both their mean, at angle 0. Otherwise
    an angle within 3° of 0° or 180° becomes 0, and one within 3° of 90°
    becomes 90.
  - `triangle` `{x1, y1, x2, y2, x3, y3}`: exactly three points. A
    triangle or a curve whose points span less than 1 pt across
    (`_spread`, their box's diagonal) is skipped, as a short line is: in
    place of its strokes it would look like their deletion.
  - `polygon`: three points are a triangle, and 4..31 a `curve`, closed
    and not smooth. Any other count is skipped. The regular `polygon` kind
    (`{cx, cy, r, n, angle}`) is never written, nor `circle`.
  - `curve` `{x1, y1, …, xN, yN, closed, smooth}`: 2..31 points
    (`CURVE_POINTS`: 62 point params and the two flags fill the 64 a shape
    may have). Both flags are always written, each true only for the
    boolean `true`. Two points, not smooth, are a `line`, snapped as one.
- A shape with a point outside the selection's box grown by max(24 pt, a
  quarter of its longer side) is skipped. The points are a rectangle's or
  a triangle's corners, an ellipse's extent, a line's ends and a curve's
  points.
- Its strokes are the numbers it names in 1..offered, each once, without
  those an earlier shape took. A shape left with none is skipped. Only an
  accepted shape takes strokes, so one skipped for its geometry leaves its
  strokes to a later one.
- The params go back to page points: `cx` and every `xN` plus the frame's
  x, `cy` and every `yN` plus its y, every number on the 1/100 grid
  (`ink.round2`). The strokes become request indices, sorted.

### Applying it

`redrawPlan(request, shapes, inkOf, makeStrokeId?)` (`ink/inkAi.js`) is
the edit, built when the answer arrives → `{changes, items, count,
skipped}`, or `{refused: true}`:

- A shape's indices are sorted and deduplicated. One that names no pen
  stroke of the request, or a stroke an earlier shape took, is dropped,
  and a shape left with none is left out.
- A shape is applied only while every one of its strokes is still in its
  group, a pen, with the samples it was sent with (the same `pts` array, or
  the same numbers). Otherwise the strokes changed meanwhile: the shape is
  left out and counted in `skipped`, and its strokes stay as they are.
- `shapeStroke(kind, params, look, {t0, span, id})` makes the stroke as
  the Shapes tool's commit does: a pen, `brush: "monoline"`, `pen: false`,
  `ch: "xyt"`, constant width, the record `{kind, params}` and
  `shapeSamples(kind, params, {span})` as its samples. A `circle` is
  written as the ellipse the file stores. It is null for params that are
  not all finite numbers and booleans, or a record that draws nothing, and
  the plan leaves such a shape out without counting it.
- The look is the first replaced stroke's (the lowest request index): its
  `color`, `opacity` and `dash`, its `size` held to the pen's sizes (0.6
  to 8 pt), and its `fill` when the new kind is a closed one
  (`CLOSED_SHAPES`, never a curve). Its timing too: `t0` is the stroke's
  `t0` plus its first sample's `t`, and the samples are spread over the
  stroke's duration.
- The new stroke takes an id its group has not held, and the first
  replaced stroke's place in that stroke's group. The other replaced
  strokes leave their groups, whichever they are.
- `changes` are `{id, page, before, after}` per group changed, and
  `items` the new strokes by group, for the selection. A group that would
  hold more than `MAX_STROKES` refuses the whole plan.

The hook (`applyRedraw`) applies it as one entry across its groups,
`applyInk(plan.changes, {label: T("ink redraw as shapes")})`, through the
byte budget as any edit:

- `{refused}` raises the full note's pill, "This handwriting note is full.
  Move part of it to a new note, or start a new one."
- With no shape drawn, the pill is "Nothing was redrawn: the selected
  strokes changed meanwhile." when some were skipped, else "The AI found
  no shapes to redraw in the selection."
- While the selection is still the one asked about (the lasso armed,
  nothing else selected since), `plan.items` become the selection.
- The pill is `undoableInkStatus`'s, tn("Redrawn as {n} shape.",
  "Redrawn as {n} shapes.", n), with the skipped count after it.

### Limits

- 300 pen strokes a redraw, refused on the client first with the server's
  sentence.
- One stroke file's caps a request, its groups together (5000 strokes,
  500 000 samples, 6 MB), the pen strokes alone counted. The client
  refuses first, with the server's sentence.
- The digest's 24 points a stroke, from at most 1000 of its samples
  (about 115 each of 300 strokes): a long zigzag keeps the 24 points that
  stand highest rather than every turn, and a detail finer than the
  samples' spacing can be lost there, while the picture still shows the
  stroke as drawn.
- A model that reads text only redraws from the digest alone.
- A curve takes 31 points, and a longer one in the reply is skipped.
- Every figure becomes one of the kinds above. A regular polygon becomes
  a closed straight curve, with a handle on each corner rather than a
  radius and a turn, and a curve takes no fill.

## Convert to text

### The prompt and the reply

The user turn is "Transcribe the handwriting in the picture."
(`TEXT_TURN`) with the picture on it, and `TEXT_SYSTEM` gives the rules:

- transcribe exactly, in reading order, with no correcting, completing,
  translating or explaining;
- one output line per handwritten line;
- Markdown, a handwritten list kept a list, "- " or "1. " as written;
- math as LaTeX, inline as `$…$`, and a formula displayed on a line of its
  own as `$$…$$` on its own line;
- `[illegible]` for a word it cannot read, never a guess;
- the transcription alone, with no preamble, notes or code fence, and
  `[no text]` for a picture without writing.

A worked example ends it. `ink_ai.read_text(raw)` strips the reply and one
enclosing code fence (`translate_context._FENCED`), reads `[no text]` (in
any case, a trailing period too) as `""`, and keeps at most
`MAX_TEXT_CHARS` (20 000) characters.

### The text box

`textBoxFor(at, text)` (`ink/inkAi.js`) is the `text_box` the
transcription goes in, through `normalizeTextBox`
([text_boxes.md](text_boxes.md#model)). `at` is `{box, strokes}`: the hook
passes `textPlan`'s, the strokes it removes as they are now and their
union `strokeBounds`, so the box sits on the handwriting it replaces and
is sized by it (the request, whose `box` and `strokes` are as sent, has
the same shape). It is null without a box.

- Its lines are the text's lines with writing on them, but a bare `$$` (a
  displayed formula's fence), and at least 1.
- `size` is the handwriting's line height: the box's height over the
  lines over `LINE` (1.25), rounded and held to 6..96 pt.
- `x, y` is the strokes' top-left corner less `PAD` (4 pt), so the text
  starts where the handwriting did. `w` is the handwriting's width and the
  padding, `h` the lines at that size and the padding, and `auto: true`, so
  the width then follows the text.
- `color` is the pen colour drawn longest among `at.strokes` (by the
  polylines' total length per colour), as `#rrggbb` (an `rgb()` or
  `rgba()` one converted, `palette.js` `hexColor`), else `#1f1f1f`. `bg`
  is null.

### One step for both

`textPlan(request, inkOf)` → `{changes, removed, skipped, box, strokes}`
removes the request's pen strokes that are still in their groups as they
were sent: a pen with the samples it was sent with, `redrawPlan`'s test.
So handwriting moved, resized, turned or flipped meanwhile, which keeps
its ids, stays where it is now.

- `changes` are as `redrawPlan`'s, and `removed` the strokes they take.
- `skipped` counts the strokes left because they changed: still in their
  group by id, but no longer as sent. One erased away (an eraser's cut
  leaves pieces under new ids), or whose group is gone, is neither.
- `box` and `strokes` are the removed strokes' union `strokeBounds` and
  the strokes as they are now, for `textBoxFor`; null and `[]` with none.

The hook (`applyConversion`) changes nothing for a transcription that is
empty once trimmed ("The AI found no writing in the selection.") or with
`removed` 0: "Nothing was converted: the selected handwriting changed
meanwhile." when some were skipped, else "Nothing was converted: the
selected handwriting is gone."

The removal and the box are one step of both histories, the linked step
that a lasso edit taking pictures makes ([pictures.md](pictures.md#the-lasso)):

- `addPlaced(tree, page, boxId, {t0: wallNow(), text_box})` puts the box
  where the Text tool puts one: on a PDF page at the top level with its
  `pdf_page`, on a sheet as the sheet's last child
  ([text_boxes.md](text_boxes.md#client)). When the sheet is gone it gives
  the tree back as it was, which the hook checks on the tree as it stands
  before anything is applied: "The handwriting's page of paper is gone."
- `link = {id: makeId(), page, kind: "text"}`, then
  `applyInk(plan.changes, {label: T("ink conversion to text"), link})`
  and, in the same tick, `stepBlocks(link.id, fn)`, whose `fn` places the
  box in the tree and gives it the text (`setBlockText`), and
  `touchTextBox(boxId)`, which refits the box to its text as typing in its
  row does.
- The selection asked about is let go when it still is the selection, and
  the pill says "Converted the handwriting to text." with Undo, and "1
  skipped: the strokes changed meanwhile." after it (`tn`, by the count)
  when some were.
- One Ctrl+Z, from either history, takes both halves back while the other
  history's newest entry is that link. Otherwise only the half in the
  history that was stepped goes back:
  - From the stroke history the strokes come back and the box stays, and
    the pill says why in a text box's words (`undoMessage` for a link of
    kind `text`): "(page N; the text box stays as it is: someone else
    changed it since)" or "(page N; the text box stays as it is: the notes
    changed since)".
  - From the block history (App.jsx `stepBlockHistory`) the box goes and
    the strokes stay, and `reportUndo` says "Undone: {what} — the
    handwriting stays as it is: it changed since." (`inkStayed`).

## SVG

`inkSvg(strokes, {background = null})` (`ink/inkSvg.js`) →
`{markup, width, height}`, or null when no stroke has a sample. The hook
(`selectionSvg`) passes it `selectedStrokes(sel, inkOf).strokes`: every
tool, and none of the selection's pictures. On a sheet of dark paper (the
sheet of the selection's first group, `paperIsDark`) it passes the
paper's colour as `background`: ink there is written light, which a
transparent picture pasted on white would lose.

- The document is `<svg xmlns="http://www.w3.org/2000/svg" width="W"
  height="H" viewBox="0 0 W H">` around the strokes' union `strokeBounds`
  grown by 4 pt on every side, its corner and size on the 1/100 grid, outward. With a
  `background`, a `<rect>` of it fills the document first. One `<g
  transform="translate(-x0 -y0)">` holds a `<path>` for each part drawn.
- The strokes go in the layer's draw order, plane by plane (`ink.js`
  `strokePlane`: band 0's other strokes, band 0's highlighters, band 1's
  other strokes, band 1's highlighters), each plane in the order given,
  each from `strokePath`, the path the screen draws, with the attributes
  `InkLayer.jsx` `Strokes` gives it:
  - a filled closed shape's inside first, at its fill's opacity (0.25 when
    it has none of its own);
  - a pencil's shade bands, each at its alpha × the stroke's opacity;
  - a stroked centreline (a dashed constant-width pen, a highlighter, a
    tool this build does not know): `fill="none"`, `stroke`,
    `stroke-width`, round caps and joins, `stroke-dasharray`, and
    `style="mix-blend-mode:multiply"` for a highlighter, but not over a
    `background`;
  - every other pen's filled outline.
- Opacity attributes are written only below 1, numbers to three decimals,
  and every attribute value is escaped.
- Where it differs from the screen, on purpose: the stored colours, never
  a dark page's inversion; a pencil's grain becomes its plain colour, since
  the grain is a pattern in the layer's `<defs>`; a highlighter
  multiplies only with what the SVG draws under it, on a transparent
  ground; and on dark paper the paper is drawn under the strokes and a
  highlighter is not blended, as the sheet draws one (`notebook.css`),
  since one multiplied into dark paper all but vanishes.

**Copy as SVG** is `copySvg(markup, {width, height})`
(`shared/lib/copyImage.js`), gated by `canCopyImage()` (a secure context
with the async clipboard). It writes one `ClipboardItem`, made in the click
with nothing awaited before `navigator.clipboard.write`, so Safari keeps
the gesture:

- `image/png`, the SVG drawn at twice its size or the screen's ratio if
  higher (`vectorPng`), given as a promise;
- `text/plain`, the markup, for an editor or a code field;
- `image/svg+xml` only where `ClipboardItem.supports?.("image/svg+xml")`,
  since one type the browser refuses fails the whole write.

It resolves true when the item was written, and false when it was refused
or the browser has no async clipboard.

**Download SVG** saves the markup as `handwriting.svg`: a Blob of type
`image/svg+xml;charset=utf-8` through `saveBlob` (`shared/lib/saveFile.js`),
as a diagram's download is ([diagrams.md](diagrams.md#the-figure)). In a
browser that is an `<a download>`, its URL revoked after two seconds;
under a native host that takes the files the page saves (`save`, the
Android app) the host gets it ([ipad.md](ipad.md) "The host contract").

## Curves

The redraw's curves and its polygons of four corners or more are `curve`
shape records: points `x1, y1, …, xN, yN` and the flags `closed` and
`smooth`, drawn as straight sides or as the Catmull-Rom curve through the
points, with a handle on each point, and never filled. The kind is defined
in [handwriting.md](handwriting.md#the-stroke-file-gamma-ink-v2) "Shape
records". `tests/shared/inkshapes.json` `samples` holds `shapes.js`
`shapeSamples` and `ink.py` `shape_samples` to the same samples, and the
redraw writes both flags on every curve.

## Tests

- `backend/tests/test_ink_ai.py`:
  - the digest: the pen strokes numbered in a frame at the selection's
    corner, the picture's line, the closed rule (a tick or a dash open, a
    small loop closed), RDP from one pass of standings equal to RDP at
    every tolerance, a long straight run read at once, the samples RDP
    reads bounded however long the strokes and however many, a dense
    zigzag kept at 24 points rather than its ends, a long stroke down to
    24 points with its ends kept;
  - the reply: each kind to its stored record in page points, the records
    baking into samples, a polygon as a triangle or a closed straight
    curve, a curve's 2..31 points and its 2-point line, a rectangle's and
    an ellipse's normalisation and snaps, a huge angle taken into a turn,
    a figure under 1 pt across skipped, the level and plumb snap and the
    short line dropped, a shape past the grown box skipped, each stroke in
    one shape at most, entries that are no shape, and the answer found past
    reasoning, fences and earlier drafts;
  - the transcription stripped, unfenced and capped, and the prompts'
    rules and reply formats, the shapes example's reply reading as a
    rectangle and an arrow;
  - the picture: light ink drawn dark in it and left as it is in the
    strokes, and handwriting past 2000 pt on a poster's PDF page in it;
  - the routes, with `gamma.routers.ink_ai._call_ai` patched: the digest
    and the picture sent and the strokes mapped back past the
    highlighters, a text-only model's digest alone, a failure's error
    line, 300 pen strokes and the 413 past them, the 400 for handwriting
    the file format refuses, the transcription's picture and Markdown, its
    400 for a text-only model, a reply cut off at its length limit an
    error on both routes, a thinking service's larger reply cap, the 413
    in a sentence past the stroke file's caps with the highlighters
    counted toward none, the tokens counted under `ink`, and the 503
    without a provider.
- `backend/tests/test_ai_allowance.py`: both routes in the loop over every
  other token-spending route, a 429 that names the allowance with nothing
  sent upstream.
- `frontend/tests/inkAi.test.mjs`: `penOnly`, `inkAiRequest` (the order,
  the refs, a reused id sent fresh), `shapeStroke` for every kind the
  redraw writes (a curve's flags kept booleans, the line and fill, a
  circle as an ellipse), `redrawPlan` (shapes across groups, the first
  stroke's place, look and timing, a fill kept, a shape skipped when its
  strokes changed, fresh ids, indices dropped, the refusal past
  `MAX_STROKES`), `textBoxFor` (the place, the size from the line height,
  a bare `$$`, the colour drawn longest), `textPlan` (the strokes removed
  group by group, handwriting moved meanwhile left where it is and
  counted, the box on the strokes it removes), `inkInTree` (a group whose
  block left the tree is gone), `requestTooBig` (strokes, samples or
  bytes past one file, highlighters counted), and `askInkAi` over a faked
  fetch (the pings skipped, an error line, a refusal's detail and status).
- `frontend/tests/inkSvg.test.mjs`: a pen's outline, the 4 pt pad, a dashed
  pen, a highlighter, a pencil's bands, a filled shape, the planes' order,
  a background under the strokes with the highlighter unblended, none
  without one, escaped attributes that read back, and null for no
  strokes.
- `frontend/tests/placement.test.mjs`: a menu from a button on the
  button's other side when its own has no room, never over it.
- `frontend/tests/copyImage.test.mjs`: `copySvg`'s one item in the click
  (the PNG at twice the size, the markup as text, the SVG flavour only
  where the browser takes one), and false when refused.
- `frontend/tests/shapes.test.mjs` and
  `backend/tests/test_shared_fixtures.py`: a curve's samples from the
  shared fixture on both sides, its handles and their drags, and its
  points carried through a move, a transform and a flip.
- `frontend/tests/selectionEdits.test.mjs`: the four new actions among
  those `actionEdit` leaves to the hook. `inkHistory.test.mjs`: the
  linked step's pill in a text box's words.
- Browser: `inkAi.mjs`, group `ink-ai`
  ([debugging.md](debugging.md#browser-end-to-end-suite)), with the AI
  routes stubbed by NDJSON answers: Convert in the first row, and its
  menu's five rows under it; a rough box redrawn as a stored `rect` that
  is selected, and Ctrl+Z bringing the drawn stroke back; one request at a
  time, the shimmering wash over the box while it is out, and the task
  row's Stop, or leaving the page, taking the wash away and leaving the
  strokes as they were though the answer comes; Convert to text putting a
  text box with the stubbed text where the strokes were, in their colour,
  the wash shimmering then landing and going, and one Ctrl+Z restoring
  both; Download SVG's file of the selection's paths and Copy as SVG's PNG
  and markup on the clipboard; an edit share's Redraw and Convert to text
  saying a shared page cannot use the AI, sending nothing; and in a 740 ×
  620 window the menu opening above a button low in the window, never over
  it, a scroll closing it as More does from the keyboard, and the greyed
  rows' titles saying why. `inkEditing.mjs` and `inkZoom.mjs` find
  Transcribe in the chat in the Convert menu.

## Not built yet

- A preview to keep or discard before the result lands: the result is
  applied, and Undo is the way back.
- Redraw as shapes and Convert to text on a whole group from its ⋮⋮ menu:
  they take a lasso selection.
- The selection's pictures in its SVG: it draws the strokes alone.
