# Gamma on the iPad (and other tablets)

There are two ways to use Gamma on an iPad, and both are the same web app:

- **The iPad app** (`ipad/`, [ipad/README.md](../../ipad/README.md)) is a
  shell over Gamma's own web app. It shows it in one `WKWebView` and adds
  what Safari cannot: Apple Pencil strokes captured natively, the
  Pencil's double tap and squeeze, the Apple Pencil Pro's haptics, a
  Gamma Cloud sign-in that works with Google, and a Gamma server of its
  own inside the app, so it works with no connection, as the desktop app
  runs its own ([The local server](#the-local-server)). Why it has this
  shape:
  [research/ipad-web-shell.md](../research/ipad-web-shell.md).
- **The web app installed to the home screen** is every feature, online,
  with Safari's own pen. It is described under
  [The installed web app](#the-installed-web-app).

Handwriting is the same `gamma-ink` file and the same block properties
whichever wrote it ([handwriting.md](handwriting.md)): in the app, the web's
ink layer still encodes and saves every stroke.

## The native app

### What the user does

1. Pick a server. The first launch shows the server picker: **This
   iPad**, the server inside the app, or another server by its address (a
   NAS, a hosted Gamma). The app loads that server's page.
2. Sign in. On This iPad the app signs in by itself with the account it
   made ([below](#the-account-and-the-silent-sign-in)). On another server
   the person signs in on the web app's own login page, with a password or
   Gamma Cloud.
3. From then on everything is the web app: the library, the notes, the
   PDF viewer, settings, sharing. The Pencil writes through the native
   capture ([The pen](#the-pen)): the line under it has the pen's style,
   draw and hold works as in a browser, a stroke by the ruler's edge
   follows the edge, and on an Apple Pencil Pro rolling the Pencil turns
   a calligraphy pen's nib. Its double tap and squeeze
   do what the system's Pencil settings ask for, unless Settings →
   Reading & editing → Apple Pencil, in the web app, says otherwise.
4. **Server…**, in the account menu and on every page shown before the
   library (the sign-in page, an unavailable workspace, a blocked share
   link), brings the picker back. When a server cannot be reached, the
   app shows a page of its own with Try again and Choose a server;
   choosing the same server again retries.
5. For work away from the network, a workspace of another server is
   cloned onto This iPad in the web app's settings
   ([Offline use](#offline-use-clones)).
6. A file shared to Gamma opens in the web app, as the same file picked
   there would. That covers the share sheet, Files' Open in and AirDrop,
   for a PDF, a Markdown note, a Notability `.note` or `.ntb` and a
   `.zip` ([Opened files](#opened-files)).

The Swift side holds no page, stroke, tool or token. The one secret it
keeps is the local server's seeded password, and the one thing it
decides about the pen is that the pen has stayed still
([The pen](#the-pen), step 6). The session is the web view's cookie
(the default data store keeps it across launches), and the library is
the server's, This iPad's included.

### How it is put together

The web app is the one owner of the page. Native code is a window, an
input device, a preview layer and, for This iPad, a process manager, and
nothing in Swift knows what a block is. So a web feature reaches the iPad
when the server has it (This iPad's at the app's next build), with no
iPad work. The two halves share only the host contract below. The files
are listed in [ipad/README.md](../../ipad/README.md).

- **The window.** `ShellView` hosts one controller (`ShellController`),
  made once and kept: a new server or a retry loads into the same web
  view. Its container fills the screen and is painted with the theme's
  background, so the strip under the status bar is the page's colour. The
  web view starts below the status bar (pinned to the safe area's top)
  and runs under the home indicator, as the page's CSS expects
  (`html[data-shell] .app` pads only the bottom, [below](#what-the-install-adds-and-where-it-lives)).
  The live stroke view keeps exactly the web view's frame.
- **One CSS pixel is one point.** The viewport is at scale 1
  (`width=device-width, initial-scale=1`), the document never scrolls
  (`html, body, #root` fill the viewport; the panes scroll), and the web
  view has `preferredContentMode = .desktop` (an iPad mini would get the
  phone's layout otherwise), no bounce and
  `contentInsetAdjustmentBehavior = .never`. So a point in the web view
  is the page's `clientX`/`clientY`, which the pen's places and samples
  rely on.
- **Navigation** (`Navigation.swift`). The server's own pages stay in the
  web view. Another origin, and `target=_blank`, open in a Safari view.
  Other schemes (`mailto:`) go to the system. A download goes to a
  temporary file and then the share sheet. The microphone is granted to
  the server's page for recordings. The Gamma Cloud start is taken over
  ([below](#gamma-cloud)). When the system ends the page's process, the
  shell reloads, and the web app restores itself as after any reload.
- **Theme.** The page posts `theme` where it sets `theme-color`. The shell
  paints the colour under the page and behind it, keeps it for the next
  launch so the app opens in the colour it closed in, and gives the window
  the page's scheme for the status bar's style. The web view itself stays
  at the system's style, so a page on the "system" theme still reads the
  system's preference.

### The host contract

Declared by a `WKUserScript` at document start, before the bundle runs
(`HostInfo` in `Bridge.swift`):

```js
window.gammaNative = {
  platform: "ipad",   // the host
  version: "0.2.0",   // the app's CFBundleShortVersionString
  ink: true,          // the host captures claimed Pencil strokes
  inkLooks: ["pressure", "dash", "blend", "stabilize", "pencil", "calligraphy", "snap"],
                      // what of the claim's look its live line draws
  hold: true,         // the host sends `hold` when a captured Pencil stays still
  pencil: true,       // the host forwards the Pencil's double tap and squeeze
  pencilPhases: true, // a squeeze's start and cancel come too (`squeeze`)
  pencilRoll: true,   // the samples carry Apple Pencil Pro's barrel roll (`r`)
  haptics: true,      // the host plays `haptic`, in an Apple Pencil Pro
  servers: true,      // the host has a server picker (the web shows "Server…")
  openFiles: true,    // the host holds the files the system opens in the app (`files`)
};
document.documentElement.setAttribute("data-host", "ipad");
```

The web app reads `platform` and the flags `ink`, `hold`, `pencil`,
`pencilPhases`, `pencilRoll`, `haptics`, `servers` and `openFiles`, never
`version`:
what a shell does not declare it does not do, and the web leaves it out
(no `hold` listener, no `haptic`, no Apple Pencil settings). `inkLooks`
is information only: it says what of the
claim's look the shell's live line can draw (below), and the page sends
the whole look in every claim whatever a shell lists. The looks:
`pressure`, the width from the Pencil's force by the brush; `dash`;
`blend`, a highlighter multiplied onto the page; `stabilize`, the
smoothing; `pencil`, the Pencil brush's width from the tilt;
`calligraphy`, the calligraphy pen's width from the travel against its
`nib`; and `snap`, a stroke along the ruler's edge. A look a shell does
not list is drawn as the plain line, and the page claims the stroke all
the same: the committed stroke is the web's either way. `pencilRoll`
says the `stroke` and `hold` samples carry the Pencil's barrel roll
(iPadOS 17.5 and later), so a calligraphy preset's nib can follow it and
its *Lock angle* pill shows.

The web half is one module, `frontend/src/shared/lib/host.js`:
`nativeHost()` (the object or null, read once), `postToHost(msg)` (a
promise of the reply; it rejects with no host) and
`onHostMessage(type, fn)`. It also sets `data-shell` on `<html>`, for the
installed app and the shell alike, which the full-screen CSS keys off.
Nothing else in the web app knows a host exists.

Web → app: `window.webkit.messageHandlers.gamma.postMessage(msg)`
(`WKScriptMessageHandlerWithReply`, the page content world). The shell
takes messages only from the server's own main frame.

| Message | Reply | Meaning |
|---|---|---|
| `{type: "claim", pointerId, at: {x, y}, tool: {kind: "pen"\|"highlighter", color, size, opacity, brush?, pressure?, dash?, blend?, stabilize?, nib?, nibRoll?, snap?}}` | `{taken}` | This Pencil touch is a stroke: take it. `at` is where the pointer went down, in viewport CSS px; `size` is the width on screen, in CSS px; the other keys are the preset's look, below |
| `{type: "theme", bg: "#rrggbb", scheme: "light"\|"dark"}` | `{}` | Paint the window behind the page and pick the status bar's style |
| `{type: "servers"}` | `{}` | Show the server picker |
| `{type: "haptic", kind: "snap"\|"select"\|"tool", at?: {x, y}}` | `{}` | Play the kind's own feedback ([The pen](#the-pen) "Gestures"): `snap`, a held stroke snapped to a shape; `select`, a pen lasso selected strokes; `tool`, a Pencil gesture switched the tool (a kind the shell does not know plays as `tool`). `at` is where, in viewport CSS px, when there is a where. Sent once per event, only under `haptics` and with *Haptics* on |
| `{type: "files"}` | `{files: [{id, name, size, type}]}` | The files the system opened in the app that the shell still holds, in the order they came; `type` is the MIME type of the name's extension, `""` when it has none ([Opened files](#opened-files)) |
| `{type: "fileData", id, offset}` | `{data}`, or `{}` | Up to 2 MiB of that file from byte `offset`, in base64; `""` at its end. `{}`: the shell holds no such file, or the offset is past its end |
| `{type: "fileDone", id}` | `{}` | The page is done with that file (taken, refused or put aside): the shell deletes its copy |

The claim's tool (`InkTool` in `Bridge.swift`, made by `ink/hostInk.js`
`claimTool`) carries the preset's look beside its four keys. Each look
key is left out at its default, and a shell decodes only the keys it
knows, so an older one draws the plain line it always drew. `kind` and
`brush` are read leniently: a kind other than `highlighter` draws as a
pen, a brush the shell does not know as the plain Pen.

| Key | Sent | The live line |
|---|---|---|
| `brush` | `"monoline"`, `"brush"`, `"pencil"` or `"calligraphy"` on a pen; absent for the plain Pen | the brush's width law (`InkWidth`): the Pen and the Brush follow the force, the Pencil the force and the tilt, the Calligraphy pen the travel against `nib` (and a little the force), the Ballpoint is even |
| `nib` | a calligraphy pen's angle, an integer 0..179, in degrees counterclockwise as seen (the nib's edge the vector `(cos nib, −sin nib)` in the view's y-down space): the preset's | the calligraphy widths, `w = max(0.12, s) × (0.85 + 0.3p)` per sample with `s` the sine between the travel (from the sample before to the one after) and the edge, as a round-ended ribbon `size × w` wide; the web's committed stroke draws the flat nib itself. Absent, a calligraphy pen takes 45 |
| `nibRoll` | `true` when the preset leaves its angle to the Pencil's roll (*Lock angle* off) | the nib turned by the roll of the stroke's first sample, `(nib − r) mod 180` (`InkTool.liveNib`), the rule the page applies to the `stroke`'s samples; ignored by a shell without `pencilRoll` |
| `pressure` | `false` when the stroke reads no pressure (*Pressure-sensitive strokes* off) | even, except a Brush, which follows its speed law, and a Calligraphy pen, which follows its travel against the nib, as their committed strokes do |
| `dash` | `[on, off]` in CSS px: the preset's `DASH` lengths (`ink.js`) × size × zoom | stroked at `size` with that pattern from phase 0, round caps, constant width even under pressure |
| `blend` | `"multiply"`, on every highlighter | multiplied onto the page (`compositingFilter`) |
| `stabilize` | the smoothing the preset draws with, 1 to 4 (a calligraphy pen's 2 when its preset sets none) | the samples through a lazy brush of 3, 6, 10 or 16 pt (the web's radii), without prediction |
| `snap` | `{x0, y0, angle}` when the stroke starts on or by the ruler's edge: a point of that edge in viewport CSS px, and the ruler's angle in degrees counterclockwise as seen, the edge running along `(cos angle, −sin angle)` | every point projected onto that line, and the line drawn between the two outermost projections, at the tool's width end to end and in its dash (a ruler line is even, like a held shape), with no lazy brush; a shell that does not list `snap` draws the freehand line, and the page commits the projected line either way |

App → web: `window.gammaNative.receive(msg)` through
`callAsyncJavaScript`, which waits for the returned promise. The user
script defines a stub that queues until `host.js` takes over and delivers
the queue, so nothing sent early is lost. `Bridge.send` delivers a
message; `Bridge.ask`, which `hold` goes through, also hands the shell
the value the page resolved with (`{}` when it resolved with nothing).

| Message | Resolves | Meaning |
|---|---|---|
| `{type: "stroke", pointerId, samples: [[x, y, p, t, a, z, r?], …], held?}` | `{committed}`, two frames later (React's commit and a paint) | The claimed stroke ended. x, y in viewport CSS px; p = force / maximum force, the lift keeping the last pressure; t = ms from the first sample, from the hardware timestamps, never going back; a, z = the Pencil's altitude and azimuth in degrees (`altitudeAngle` and `azimuthAngle(in:)` of the web view, the angles WebKit gives the page in radians). A shell with `pencilRoll` adds r, `[x, y, p, t, a, z, r]`: the barrel roll in degrees in [0, 360), clockwise as seen from the screen (PointerEvent.twist's convention), from `UITouch.rollAngle`; without it the nib takes no roll. An older shell sends `[x, y, p, t]`, and the stroke has no tilt. `held`: the `n` of the hold whose shape the shell drew the line as, left out when it drew none; the page commits a held shape only for that hold. No samples: the system cancelled the touch, or the page took a hold (`cancel`), and the claim just ends |
| `{type: "hold", pointerId, samples}` | at once: `{shape: {kind, params}}`, `{cancel: true}` or `{}` | The captured Pencil has stayed within 3 pt of one sample for 600 ms ([The pen](#the-pen), step 6); the samples so far, as `stroke` sends them. `shape`: the stroke snaps; `kind` is `line`, `arrow`, `triangle`, `rect`, `polygon`, `circle` or `ellipse`, and `params` its `shapes.js` parameters in viewport CSS px (points and lengths; `angle` in degrees, `n` a count), which the shell draws and refits. `cancel`: the page took the hold (*Paste here*); the line goes and the touch ends with an empty `stroke`. `{}`: nothing, which is also what a page without a `hold` listener resolves with |
| `{type: "pencil", gesture: "tap"\|"squeeze", action, phase?: "ended"}` | at once | A double tap, or a squeeze's end. `action` is the system's preferred action: `switchEraser`, `switchPrevious`, `showColorPalette`, `showInkAttributes`, `runSystemShortcut` or `ignore`. `phase: "ended"` comes from a shell with `pencilPhases`; a squeeze without it, from an older shell, is a quick squeeze |
| `{type: "squeeze", phase: "began"\|"cancelled", action}` | at once | A squeeze's start, and its cancel, which comes instead of its `pencil` when the system cancels it. A type of its own, so a page that predates it ignores both and still acts once per squeeze, on its `pencil` |
| `{type: "filesOpened"}` | at once | The system opened a file in the app: ask for what is held (`files`). A page that is not signed in to a workspace yet ignores it and asks once it is |

Keeping it small is the point. A message is added in `Bridge.swift` and
`host.js` together, and a flag in `HostInfo` says what the web may rely
on; `version` only names the build. A message type nobody listens for
resolves at once on the web side, so a newer shell never hangs an older
page, and its `hold` and `squeeze` change nothing there. A message the
shell does not know gets an error reply, and keys it does not know are
ignored, so a newer page's claim still draws under an older shell.

### The pen

The web decides, native executes. On a Pencil touch the web's ink layer
decides what the touch is, as it does in Safari: a stroke, a tap, the
eraser, the lasso, a move, the hand. Only a pen or highlighter stroke is
claimed.

1. **The touch begins.** `PencilCapture`, a Pencil-and-direct recognizer
   on the web view's container, records the touch from its first sample
   (the coalesced touches, every sample the Pencil measured), with the
   Pencil's altitude and azimuth (`StrokeRecorder`). It delays nothing
   and recognizes alongside every other recognizer, so the page gets the
   touch as in Safari. The recorder also keeps the stroke's **stillness
   anchor** (`stillSince`): the latest sample that landed more than 3 pt
   (`PencilCapture.holdSlop`) from the anchor before it, at first the
   first sample.
2. **The page claims it.** `InkLayer.jsx` starts its stroke as always,
   draws its own draft, and posts `claim` with the pointer's id, where it
   went down and the tool in the preset's look (`claimForHost`,
   `ink/hostInk.js` `claimTool`).
3. **The shell matches the claim to a touch** (`ClaimQueue`). The page's
   pointer id means nothing natively, so a claim takes the oldest Pencil
   touch that began within 10 points of `at`. A claim whose touch already
   ended takes nothing (a dot the page's own `pointerup` finished first),
   and an ended touch is remembered for half a second, so a late claim
   cannot take the next touch at the same spot. A touch nobody claimed (a
   tap on a button, the eraser, the lasso) only stands in the way of a
   claim at that same spot. A touch going down forgets the long-ended ones.
4. **Taken.** The recognizer begins. That prevents WebKit's recognizers
   still deciding (its touch events, pans, long presses: `canPrevent` is
   true, `canBePrevented` false) and cancels the web view's touch
   (`cancelsTouchesInView`). While it captures, it swallows fingers on the
   screen (the palm). It also draws the live line (`LiveStrokeView`, one
   `CAShapeLayer` a stroke) in the claim's look, from the recorder's
   samples, extended by the predicted touches:
   - with `snap`, the projected line along the ruler's edge (`RulerSnap`,
     the claim's table above);
   - else with `dash`, the line stroked at the tool's width with that
     pattern;
   - else, when the brush has a width law for the stroke (`InkWidth`, the
     brush table of `ink/finishStroke.js` ported and held to
     `tests/shared/inkwidth.json`: a Pen or a Pencil with pressure, and
     any Brush or Calligraphy pen), a filled ribbon `size × w` wide, `w`
     per sample as the web's live canvas has it (the Brush's start taper
     only; the calligraphy widths from the claim's `nib`, turned by the
     first sample's roll under `nibRoll`), a Pencil's at the shade of its
     mean pressure so far;
   - else the plain polyline at the tool's colour, width and opacity (a
     Ballpoint, a highlighter, a stroke without pressure).
   A highlighter is multiplied onto the page, and with `stabilize` the
   samples go through the lazy brush and the prediction is left out. The
   committed stroke is the web's, so the line only has to come close: a
   dashed pressure line is even, a pencil has no grain and a calligraphy
   stroke has round ends until the swap.
5. **The page gives way on the reply.** On `{taken: true}` the layer drops
   its draft without saving (`handOver`), ignores that pointer's own
   events, and treats fingers as palms until the stroke ends. It does not
   wait for a `pointercancel`, which WebKit may not send. So a stroke is
   never saved twice, and the page never pans under the Pencil.
6. **The pen holds still.** UIKit sends no touches while the Pencil
   stays still, and the page sees nothing of a captured stroke before its
   lift, so the shell keeps a timer (`PencilCapture.holdDelay`, 600 ms,
   in the run loop's common modes). It fires 600 ms after the stillness
   anchor's sample and starts again whenever the anchor moves; then the
   shell sends `hold` with the samples so far. The timer is the one
   thing Swift decides about the pen; its rules (`HoldWatch`) are a pure
   part, tested with the time given. The page answers at once, from the
   samples (the claim's `hold`, `InkLayer.jsx`):
   - with strokes on the handwriting clipboard and no sample more than 2
     CSS px from the first, `{cancel: true}`, and *Paste here* opens where
     the pen went down ([handwriting.md](handwriting.md) "Copy, cut and
     paste"). The shell takes the line away and keeps the touch, so palms
     stay swallowed, and at the lift sends `stroke` with no samples;
   - with *Draw and hold* on (and for a highlighter its preset's
     straight not *Off*), `recognise` runs over the samples in page
     points (lines only for a highlighter; through the preset's
     smoothing, as the page's own hold). It runs when their extent, the
     bounding-box diagonal, reaches the recogniser's own floor of max(24
     CSS px, 18 pt) (`shapes.js` `holdQualifies`). At least 250 ms must
     also have passed from the first sample to the anchor's. A closed
     shape counts by its size, though it ends where it began. A shape
     comes back as `{shape: {kind, params}}` in viewport CSS px
     (`shapeToHost`). The page keeps it on the claim with `n`, the samples
     the hold carried, `from`, the last of them, and `tSnap`, the anchor's
     `t` + 600, and posts `haptic` `snap` at `from`;
   - otherwise `{}`.

   On a shape the live line shows the shape instead of the trace, at the
   tool's width end to end (a committed shape has no `w`) and in the
   preset's dash, in the selection blue for its first 34 ms. While the
   pen keeps moving the shell refits it every frame from the snap's
   params (`HeldShape.refit`, `shapes.js` `refitShape` without Shift): a
   line or an arrow takes the latest sample as its end, and a closed
   shape scales about its centre and turns with it. Both sides run one
   rule for it (`ShapeHold.current`, `shapes.js` `followHold`, both held
   to `tests/shared/inkhold.json`). A sample more than 12 pt from `from`
   within 150 ms of `tSnap` drops the shape for the rest of the stroke,
   and the freehand line comes back. Once a sample has been 6 pt from
   `from`, the shape follows the latest one. After a shape or a cancel
   the stroke sends no more holds; after `{}` its next still spell asks
   again. A reply that comes after the lift changes nothing in the shell:
   it drew the freehand line and sent it naming no hold. So the page
   commits that line too, though it kept the shape when it answered (and
   its snap tick may have played).
7. **The lift.** The shell draws its line once more from the samples
   with the lift's own, without the prediction, and sends `stroke` with
   them. The layer converts them with the rect and zoom of the down
   (`hostEvent`, the tilt back to radians for `penTilt`, the roll as
   `twist`) and applies the repeated-point rule of `appendInkSample`. A
   calligraphy stroke takes its `nib` from the first sample's roll
   (`strokeNib`, unless the preset locks it). A stroke that started on
   the ruler's edge has its samples projected onto it, in page points,
   and commits as the line between the farthest projections, through
   `finishStroke`'s hold stage ([handwriting.md](handwriting.md) "The
   ruler"). When its claim kept a shape and the `stroke` names that hold
   (`held`, the hold's `n`), `followHold` runs over the final samples in
   the shell's own space. It gives the shape the shell last drew, or none
   when it was dropped, and that shape goes in page points
   (`shapeFromHost`) to `finishStroke`'s hold stage. The stroke commits
   through the same `commitStroke` as the page's own strokes.
   `finishStroke` decimates the samples and bakes the drawn width from
   their pressure (`w`, the brush law in `ink/finishStroke.js`). Then
   `encodeStroke` writes the stroke with `strokeChannels(style)`
   (`ch: "xyptw"` for a pressure pen, `az` after the `t` when the samples
   carry tilt) and `t0` the web's wall clock at the down, and `onStroke`
   saves it ([handwriting.md](handwriting.md) "Width"). Undo, replay and
   saving work as for the page's own strokes.
   The live line stays until the reply, which comes after the page has
   painted the stroke, so the swap never blinks. A page that never
   answers loses the line after three seconds.
8. **Cancelled.** When the system cancels a captured touch, the line goes,
   and the shell sends `stroke` with no samples, which ends the claim and
   saves nothing.

A claim the shell does not take (`{taken: false}`) leaves the touch to the
page, which finishes it as in Safari. Pencil hover, pressure and tilt
reach the page as Safari gives them.

**Gestures.** `PencilGestures` (`UIPencilInteraction`) forwards the double
tap as `pencil`, and on iPadOS 17.5 and later the squeeze in its phases:
`squeeze` `began` when it starts, `pencil` with `phase: "ended"` when it
ends, or `squeeze` `cancelled` in its place when the system cancels it
(the repeating `changed` is not sent), each with the system's preferred
action. A squeeze in a phase the page does not know (a later shell's
`changed`, a `squeeze` without a phase) does nothing. `ink/usePencil.js`
applies `ink/pencilActions.js` with this browser's
choice for each gesture, *Double tap* and *Squeeze* (`inkPencilTap`,
`inkPencilSqueeze`, in Settings → Reading & editing → Apple Pencil, which
shows only in the app; [settings.md](settings.md) "Reading & editing"):

- *System setting*, the default, follows the preferred action:
  `switchEraser` toggles the eraser, `switchPrevious` picks the previous
  tool, `showColorPalette` and `showInkAttributes` open the armed tool's
  options row, and the rest do nothing.
- *Switch to eraser*, *Switch to last tool*, *Show tool options*, *Undo*
  (what the strip's Undo button does) and *Nothing* stand in for it.
- The squeeze also offers *Eraser while held* and *Last tool while held*
  (under `pencilPhases`). Its start arms what the choice toggles to: for
  *Eraser while held* the eraser, or, with the eraser armed already, the
  tool to go back to; for *Last tool while held* the last tool. It keeps
  the lasso's selection aside. Its end, or its cancel, goes back to the
  tool armed before and restores what is left of the selection (none
  when the squeeze erased it all). A squeeze shorter than 300 ms that
  neither erased nor drew leaves the tool armed instead, as a toggle, so
  short squeezes take the eraser and give it back. *Eraser returns to the
  last tool* waits while the squeeze is held. A squeeze without phases
  (an older shell) toggles.

A gesture that comes while a stroke or a claim is in progress does
nothing, and nor does the end of a squeeze that began then; the end of a
held squeeze is never lost. Each tool switch a gesture makes posts
`haptic` `tool`, a pen lasso that selects posts `select` at its lift,
and a hold that snaps posts `snap` (under `haptics`, with *Haptics* on:
`ink/usePencil.js` `hostHaptic` checks both, and the shell plays what it is
sent). On iPadOS 17.5 and later the shell plays each kind as Apple's
feedback for its event (`PencilHaptics`, as `HapticFeel` picks it). The
sources are Apple's "Playing haptic feedback in your app" and the WWDC24
session "Squeeze the most out of Apple Pencil". Each feedback has one
generator made for the web view (`init(view:)`, the impact's
`init(style:view:)`), kept, and plays at `at` when there is one:

| Kind | Feedback | Apple's use |
|---|---|---|
| `snap` | `UICanvasFeedbackGenerator`, `pathCompleted(at:)` | path completion or shape recognition |
| `select` | `UISelectionFeedbackGenerator`, `selectionChanged(at:)` | a change in selection |
| `tool` | `UIImpactFeedbackGenerator(style: .light)`, `impactOccurred(at:)` | an impact (Apple names no feedback for a tool switch) |

The canvas generator's other feedback, `alignmentOccurred(at:)`, is
Apple's for an object snapping to a guide or a ruler. The shell does not
play it: a stroke along the ruler's edge plays nothing. A gesture's
`tool` tick comes without a place and plays where the Pencil hovered for
that gesture: `PencilGestures` keeps the tap's or the squeeze's
`hoverPose` location, in the web view's points, for a second (none when
the Pencil was too far off to hover). The system goes by the place to
play the feedback in an Apple Pencil Pro (device checks 16 and 21).
Without a place, `select` and `tool` play their generator's call that
takes none (`selectionChanged()`, `impactOccurred()`), and so does
`snap`, as the light impact, since its canvas feedback needs one. Before
17.5 every kind is the plain light impact. An iPad itself has no motor:
only an Apple Pencil Pro plays these.

### Gamma Cloud

Google refuses to sign in inside an embedded web view, so the page's own
start (`/api/auth/cloud/start`) is taken over, as the desktop app does
(`CloudSignIn.swift`; `desktop/lib/cloudSignIn.js`). The shell listens on
`127.0.0.1:<port>` and posts `app-start` from inside the page, so a Link
carries the page's session, with that return address and an S256 PKCE
challenge. It opens the authorize URL in an `ASWebAuthenticationSession`.
The server sends that browser back to the loopback address with a
one-time result. The listener keeps it and redirects the sheet to
`gamma-ipad://cloud-signin`, which closes it. The web view then posts
`app-claim` with the verifier, which sets the session cookie where the
web app runs. The server needs nothing new. A password sign-in is the
web app's login page.

### Opened files

The app declares the types it opens (`CFBundleDocumentTypes` in
`project.yml`), so the share sheet, Files' *Open in* and AirDrop offer it.
The types are PDF, Markdown, Notability's `.note` and `.ntb`, and
`.zip`. The ones other apps own are at rank *Alternate*, so installing
Gamma never makes it their default app. Markdown and the `.note` are
declared as imported types as well, for an iPad where no app declares
them. The `.note` identifier is the one Notability's own Info.plist
exports (`com.gingerlabs.notability.notearchive`), and where Notability is
installed its declaration wins. No app is known to declare the `.ntb`:
the Info.plist published for Notability's Mac app declares `.note` and
`.nbn` only. So Gamma claims
it, as an exported type of its own (`net.gammapdf.ntb`) at rank *Owner*,
and the system reads an `.ntb` as that type wherever no other app
exports one.

The system hands the app a file URL (`onOpenURL` in `GammaApp.swift`).
`OpenedFiles` copies the file into a folder of its own under
`tmp/Opened`, under the file's security scope and a file coordinator, as a
file opened in place from another app needs. A copy iOS left in
`Documents/Inbox`, which Files shows, is moved out instead. A folder is
refused. The copies of an earlier run go when the app starts. The shell
then sends `filesOpened`.

Native code still decides nothing about a file. The page asks for the
list (`files`) once it is signed in to a workspace, and again on each
`filesOpened`. It reads each file in 2 MiB base64 chunks (`fileData`),
then says `fileDone`, and only then does the copy go. So a file opened
at the server picker, on the sign-in page, while This iPad starts or
before a reload waits until a page can take it. The web half is
`frontend/src/transfers/openedFiles.js`, wired in `App.jsx`
(`takeOpenedFiles`):

1. **Where.** As the desktop does with a PDF it is handed
   ([desktop/docs/architecture.md "Opening PDF files"](../../desktop/docs/architecture.md#opening-pdf-files)),
   the files go to the workspace shown when the account can edit there,
   else to its personal workspace. A workspace others see asks first: one
   with other members, a public one, or a clone. The question offers
   *Add to <workspace>*, *Add to <personal workspace>* and *Cancel*, which
   lets the files go. Going to the personal workspace is a workspace
   switch, so the page reloads on it, takes the files the shell still
   holds there, and the status says why when the shown workspace was
   read-only.
2. **What.** Each file is read, then let go, then takes the path a picked
   file takes. A PDF or a Markdown note becomes a page at the library's
   top, as *Upload files…* makes one, and a single one opens. A PDF keeps
   the upload's rules: an already-known file opens its page, and the
   annotations another reader saved in it are imported. A `.note` or
   `.ntb` opens the Notability import's review, several of them as one
   `.zip`, as the picker sends them. A `.zip` opens the Import dialog with
   the file already chosen. It asks which export it is, offering the
   sources that read an archive (Zotero, Markdown, Notability, Gamma),
   and goes on to that source's review with no picker.
3. **What it cannot.** One import dialog shows at a time. With
   Notability notes in the same share, the review takes them, and a
   `.zip` with them is named in the status, to be opened again. A second
   `.zip` is named the same way. A file Gamma does not open is let go,
   and the status names it.

### The local server

"This iPad" is Gamma's own backend running inside the app on
`127.0.0.1:<port>`, the way the desktop app runs a sidecar server beside
its window ([desktop/docs/architecture.md](../../desktop/docs/architecture.md#servers-and-workspaces)).
The shell shows it like any other server: the web view loads its address,
and the page is the web app that server serves. So the whole web app works
with no connection, and the Swift side stays a process manager
(`Server/LocalServer.swift`). It picks the data directory and the port,
seeds the first account, starts the interpreter and says when the server
answers. Nothing in it knows what the server holds.

#### What runs

- **CPython 3.13 for iOS**: BeeWare's
  [Python-Apple-support](https://github.com/beeware/Python-Apple-support)
  `3.13-b16` (Python 3.13.16), embedded as `Python.xcframework`. CPython
  supports iOS since 3.13 (PEP 730), and the app embeds it as
  [its iOS guide](https://docs.python.org/3/using/ios.html) describes.
- **The backend from `backend/`, unchanged**: `app.py` and the `gamma`
  package, copied into the bundle at build time. The server has no iPad
  branch. It reads the environment the desktop's sidecars are given
  ([desktop.md](desktop.md)), and it spawns no processes, which iOS would
  not allow.
- **The built web app** from `frontend/dist`, served at the server's root
  (`GAMMA_STATIC_DIR`). This iPad therefore shows the web app of the app's
  own build, as a desktop's local server does; another server shows its
  own.
- **`ipad/python/gamma_ios.py`**, the entry, the iPad's
  `desktop/backend_entry.py`. It sends all output to the log, imports the
  server once the environment is final (`gamma/config.py` reads it at
  import), and runs uvicorn on `127.0.0.1` at `GAMMA_PORT` under a
  supervisor (`supervise`): a server that stops is run again when the app
  asks (`restart`), on the same port.

`Server/PythonRuntime.swift` runs one interpreter on a thread of its own
(`gamma-server`). The thread has a 16 MiB stack, because a secondary
thread's 512 KiB default is too small for Python's recursion and the
server's imports. The configuration follows the guide:

- UTF-8 mode, whatever the device's locale.
- Unbuffered standard streams, so the log is current.
- No bytecode written, since the signed bundle is read-only.
- No signal handlers: the process's signals are the app's, and this is
  not the main thread.
- Exactly four module paths, all in the bundle: the standard library, its
  `lib-dynload`, `python/app` and `python/app_packages`.

The C API comes in through a bridging header. The interpreter is never
finalized, and it cannot be started twice in one process. The server in
it can be: `PythonRuntime.restart` takes the GIL on another thread and
calls `gamma_ios.restart()`, which stops a server still running and has
the supervisor run a new one ([Starting, and the log](#starting-and-the-log)).
Only the entry's own end (an import that fails) stays failed until the
app is opened again. An uncaught `SystemExit` there would end the whole
app under `PyRun_SimpleString`, so the entry turns it into an ordinary
error; inside the supervisor, uvicorn's `SystemExit` for a port it could
not listen on is a failed start, reported and retried.

#### The wheels

The server's dependencies come as iOS wheels, from PyPI and from
[Flet's index](https://pypi.flet.dev), which publishes iOS builds of the
native packages PyPI lacks. `ipad/python/requirements-ios.txt` is
`backend/requirements.txt`, changed only where a package has no iOS wheel
at the server's pin. Each change is commented in the file:

| Package | Server | iPad | Why |
|---|---|---|---|
| uvicorn | `uvicorn[standard]` | `uvicorn` | uvloop and httptools have no iOS wheels. h11 on the asyncio loop serves the same, websockets is listed on its own, and the other extras serve only the development reloader and config files |
| orjson | 3.12.0 | 3.11.9 | Flet's newest iOS build |
| pydantic | 2.13.2 | 2.13.4 | pydantic pins its pydantic-core exactly, and Flet builds only some cores: 2.13.4's (2.46.4) is there, the server's (2.46.2) and 2.14's (2.50) are not |
| cryptography | 50.0.1 | 43.0.1 | Flet's iOS build. The server uses only `cryptography.fernet`, the same in both. It links OpenSSL 3 without the legacy provider and imports only with `CRYPTOGRAPHY_OPENSSL_NO_LEGACY` set, which `gamma_ios.py` does before the server's imports |
| PyMuPDF | 1.28.2 | 1.27.2.3 | Flet's newest iOS build (its recipe calls the 1.28 series a redesign, not a routine bump). Its two MuPDF dylibs become frameworks in the post-build step ([How the bundle is made](#how-the-bundle-is-made)) |

The native wheels are PyMuPDF (MuPDF and its C++ wrapper as two dylibs,
and two extension modules), pydantic-core, cryptography (with cffi),
orjson, bcrypt, Pillow, and rpds-py (jsonschema's, through mcp); the rest
is pure Python. `backend/tests/test_ios_requirements.py` fails when the server
gains a dependency the iOS file neither lists nor excludes with a reason,
so each new one gets an iOS decision.

With PyMuPDF in the bundle the server on the iPad reads PDFs as every
other server does: text with its glyph boxes for search, the AI and a
highlight's quote (`gamma/pdf_text.py`), page and region pictures for the
AI's `view_pdf_page` and `view_ink` and for MCP, the annotated-PDF
export's notes placed in the page's free space (`pdf_notes.py`), the
Logseq export's area-highlight pictures, and MuPDF's built-in fonts for a
page whose fonts are not embedded. Before it, with pypdfium2 left out for
want of an iOS wheel, each of those fell back
([research/pypdfium2-ios.md](../research/pypdfium2-ios.md)). The imports
stay guarded all the same, inside the functions that need them: a server
without PyMuPDF starts, reads text and page sizes through PyPDF2, renders
nothing, imports highlights without quotes and puts the notes in the
margins, and the import's 70 ms and 30 MB are paid at the first PDF read,
not at the start. The start test ([Tests](#tests)) is what catches a
bundle whose frameworks do not load.

#### Where the data lives

The data directory (`GAMMA_DATA_DIR`) is `Documents/Gamma/local` in the
app's container. It is in Documents because that is the folder the Files
app shows (`UIFileSharingEnabled` and `LSSupportsOpeningDocumentsInPlace`
in `project.yml`). There it appears as On My iPad › Gamma › Gamma › local,
with the log, as the desktop app shows its data folder. The picker shows
its size. It is in the iPad's backups like any app's documents, and
deleting the app deletes it.

#### The address

The server keeps its port, by the desktop's rule (`desktop/lib/sidecar.js`
`pickPort`):

1. The port it had last time (`gamma.local.port` in `UserDefaults`), while
   that is free.
2. Otherwise, the first free port in the IANA dynamic range (49152–65535),
   from a random point.
3. Only then, whatever port the system hands out.

A kept port keeps the origin, and with it what the web app stores per
origin (its local storage). The free check binds `127.0.0.1` with
`SO_REUSEADDR`, as uvicorn does. So a port the last run left in
`TIME_WAIT` (the app killed and opened again at once) still counts as
free, while one another socket listens on does not.

#### The account and the silent sign-in

A first start, on an empty data directory, seeds one account: `admin`,
with a random 12-character password (9 random bytes in base64url, the
desktop's shape and the server's own). The password is kept in the
Keychain (`Server/Keychain.swift`). It is readable after the first unlock
and travels in an encrypted backup, so a restored iPad gets it back with
the data directory. The app passes the account as `GAMMA_ADMIN_USER` and
`GAMMA_ADMIN_PASSWORD` only on that start, since the server ignores them
once it has accounts. The picker shows the account and copies the
password, for signing in again after a sign-out.

A data directory whose password the Keychain does not hold gets no silent
sign-in: one restored from a backup without the Keychain, or one whose
first write to the Keychain was refused. The person then signs in on the
page. A password the server made itself, with no account seeded, is in
its log. A caller can hand `LocalServer` the account a first start seeds
when the Keychain refuses one made for it; the app never does, only the
test host, whose Keychain refuses it on CI ([Tests](#tests)).

The shell signs in to the local server as the desktop does
(`autoLoginScript` in `desktop/main.js`; `WebShell.swift`). After the
first finished main-frame load on the local server's origin, it asks
`/api/session`. When that is anonymous, it posts `/api/login` with the
seeded account and reloads, so the session cookie lands in the web view's
own store.

- It runs through `callAsyncJavaScript` in the app's own content world,
  with the account passed as arguments, so the page's scripts never see
  the password.
- It runs only when the page's origin is the local server's; nothing runs
  in another server's page.
- It runs once per open: a pick in the picker, Try again, or the reload
  after the page's process ended. So a person who signs out stays signed
  out until This iPad is opened again, as on the desktop.

#### The environment

Set with `setenv` before the interpreter starts, since `os.environ` is a
copy taken then (`LocalEnvironment` in `LocalServer.swift`):

| Variable | Value |
|---|---|
| `GAMMA_DATA_DIR` | `<Documents>/Gamma/local` |
| `GAMMA_STATIC_DIR` | `<bundle>/python/frontend_dist` |
| `GAMMA_VERSION` | the app's `CFBundleShortVersionString`, shown as the server's build |
| `GAMMA_CLOUD_DEFAULT_ISSUER` | `https://account.gammapdf.com`: Gamma Cloud sign-in is on until the admin picks another account server or none, as on the desktop ([cloud_accounts.md](cloud_accounts.md)) |
| `GAMMA_PORT` | the port above, read by `gamma_ios.py` |
| `GAMMA_ADMIN_USER`, `GAMMA_ADMIN_PASSWORD` | the seeded account, on a first start only |

`GAMMA_FOLDER_LINKS` is not set: folders kept on disk are the desktop
app's ([folder_sync.md](folder_sync.md#folders-kept-by-the-desktop-app)).

#### Starting, and the log

The server starts at launch when This iPad was the last pick, and
otherwise the first time it is opened. It then runs while the app does,
whichever server the window shows.

- **Before the start**, the app checks that the bundle has the server and
  the web app in it, so a build made without the Python step says so.
- **While it starts**, a page of its own covers the web view
  (`LocalStartView`), and the app asks `/api/health` every 250 ms. It
  counts as up by the desktop's rule: a 2xx, or the 503 with
  `data_directory_not_upgradable`, a server that is up and shows its own
  guidance ([migrations.md](migrations.md)).
- **There is no deadline**: giving up would only hide a server that
  answers later. After a minute the start is marked slow (the first one
  after an install, on an older iPad). The pages then say it can take a
  few minutes, and the polling drops to once a second.
- **A failed start** is what the supervisor reports. It writes
  `<data>/logs/server.state` (`{state, detail, at}`, replaced whole):
  `starting` when a server is about to run, `stopped` with why when one
  ended without a restart asked for (a port it could not listen on, the
  usual case). The app reads the file between health probes
  (`ServerState`) and shows the detail with *Try again*, which calls
  `LocalServer.restart`. The app removes the last launch's file before it
  starts, and `restart()` writes `starting` before it returns, so an old
  failure is never read as the new start's. A failure before the
  interpreter (no free port, no data folder) is tried from the beginning;
  the interpreter's own end stays failed.
- **Back from the background.** iOS suspends the app, and the server with
  it, and can reclaim a suspended app's listening socket, notably while
  the screen is locked. When the app is active again, it asks
  `/api/health` up to three times, a second apart. A server that stays
  silent is run again in the same interpreter (`restart`), on the same
  port, so the page's origin and what the web app keeps per origin stay;
  the start page covers the shell for the moment that takes. The app's
  lifespan (`gamma/app.py`: the sweeps, the backup schedule) ends with
  the old server and runs with the new one.

Everything the server prints goes to `<data>/logs/server.log`
(`gamma_ios.py`). The file is appended across launches, and each start is
marked with a `--- <UTC time> start port <port> ---` line, the desktop
sidecar's marker. `faulthandler` writes the threads' stacks there before a
crash in native code. A failed start shows its message and the log's last
4 KB in the picker, and the Files app reaches the whole file.

#### Offline use: clones

This iPad is a Gamma server like the desktop's local one. So the offline
copies of another server's workspaces are Gamma's own clones (mirrors,
[mirror.md](mirror.md)), made and resolved in the web app as the
desktop's are ([desktop/docs/architecture.md](../../desktop/docs/architecture.md#clones)).
The Swift side keeps no sync state.

The desktop makes a clone with one click on its switcher's *clone* chip.
The iPad shell has no switcher, so a clone is made the way any Gamma
makes one ([mirror.md](mirror.md#making-one)):

1. On the other server, Settings → Integrations: a read-and-write token.
2. On This iPad, Settings → Workspaces → Clones → *Clone a remote
   workspace*, with that server's address and the token.

From then on the sync pill, the merge chips and the conflict cards are the
web app's. A clone syncs only while the local server runs, that is, while
the app is open and This iPad has been opened in that launch.

#### The server picker

`Views/ServersView.swift` shows at first launch, and whenever the web
app's Server… posts `servers`. It is plain SwiftUI with the system's
styling.

- **This iPad**: a dot for the state (green running, orange starting, red
  failed, grey not started), the address or the state with the data
  directory's size, and Open. A failed start adds its message and the
  log's tail. The account's name and Copy the password sit under it.
- **Servers**: the other servers by address, newest first. Each has a
  reachability dot from `/api/health` (a 3 s timeout, the answer kept for
  30 s, as the desktop caches its remotes'), Open, and Edit and Remove on
  a swipe or a long press. Editing the shown server's address loads the
  new one.
- **Add a server**: the address as typed or pasted, opened at once. A bare
  host means `https`, and a page link keeps only its origin.

The pick and the list are kept in `UserDefaults` as one JSON value
(`gamma.servers`, `AppModel.swift`). The single address and recent list an
earlier version kept become the current server and the list the first
time it is read.

#### How the bundle is made

`ipad/scripts/build-python.sh` runs on a Mac before each Xcode build (how
to run it, its outputs and its cache: [ipad/README.md](../../ipad/README.md#build-and-run)).
It downloads the support package once and checks it against a pinned
sha256. It installs the wheels for the device and for the simulator,
copies the server code and the built web app, and compiles their bytecode
with the Mac's Python 3.13. The device writes no bytecode, so without it
each launch would compile the standard library, the wheels and the
server from source.

After Xcode has copied the resources, a post-build phase in `project.yml`
copies `python/app`, `python/frontend_dist` and the platform's wheels into
the bundle. It then runs the support package's `install_python`. That
adds the standard library and turns each compiled module (`.so`) into a
framework of its own, signed, since iOS loads an app's native code only
from frameworks.

Two of the standard library's modules, `_ssl` and `_hashlib`, link OpenSSL
statically, and Apple requires a privacy manifest in every framework that
carries OpenSSL
([third-party SDK requirements](https://developer.apple.com/support/third-party-SDK-requirements)):
App Store Connect refused TestFlight build 9 for both (ITMS-91061).
`install_python` moves a `<module>.xcprivacy` lying beside a module's `.so`
into the framework as `PrivacyInfo.xcprivacy`, and the support package
means to ship one for each of the two, but the 3.13-b16 tarball has none
(its Makefile copies `lib-dynload` into the package before it writes
them). So `build-python.sh` puts OpenSSL's own manifest beside each of the
two modules, in every slice of the framework, on every run:
`ipad/python/OpenSSL.xcprivacy`, the file OpenSSL ships for its macOS
framework, which declares one accessed API (file timestamps, reason
C617.1), no collected data and no tracking. The post-build phase fails a
build whose two frameworks end up without it. Build 9's report named only
these two: the wheels' frameworks drew no complaint and carry none.

`install_python` converts `.so` files only, and PyMuPDF's wheel carries
MuPDF as two plain dylibs beside its extension modules
(`python/app_packages/pymupdf/libmupdf.dylib`, 57 MB, and
`libmupdfcpp.dylib`), which iOS does not load from there and App Store
validation refuses. So a step of our own follows `install_python`: each
dylib becomes `Frameworks/pymupdf.<lib>.framework/pymupdf.<lib>`, the name
`install_python` would give a module's framework and the one the wheel's
own install names already use, with an `Info.plist`, its install name set
and the references in the four binaries rewritten (no-ops with this wheel,
there for one built with bare names), a `.fwork` marker left where the
dylib was (the wheel's `pymupdf/__init__.py` reads it and preloads the
framework's binary), and each framework signed as `install_python` signs
its own. The build fails if a `.dylib` is still loose under
`app_packages`. The step runs on every build, since the copy before
`install_python` is an `rsync --delete` that brings the dylibs back. The
bundle grows by about 75 MB per platform. What the wheel holds, what
`install_python` does and does not do, and what a Mac run must still
confirm: [research/pymupdf-ios.md](../research/pymupdf-ios.md).

### Tests

- `ipad/GammaIPadTests` (XCTest, on macOS in `ipad.yml`), the pure parts:
  - the messages' decoding and encoding and the user script
    (`BridgeTests`): the claim's look, read leniently (`nib`, `nibRoll`
    and `snap` among it, and a claim without them), `haptic`, `hold` and
    its replies, the `stroke`'s `held`, `squeeze` and the `pencil` phase,
    the opened files' `files`, `fileData`, `fileDone` and `filesOpened`,
    the declared flags (`pencilRoll`, `openFiles`, and `calligraphy` and
    `snap` in `inkLooks`)
  - the opened files' store (`OpenedFilesTests`): a file copied and
    listed with its name, size and type, read back whole over several
    chunks, an offset past the end and an unknown id refused, the copy
    gone at `fileDone` and the original kept, a file in the Inbox moved
    out, a folder, a missing file and a web URL not taken, and an earlier
    run's copies gone at launch
  - the cloud sign-in's requests and PKCE (`CloudSignInTests`)
  - claim matching and sampling (`PencilTests`): the samples' tilt, the
    roll's seventh value (degrees in [0, 360), clockwise; none without
    `rollAngle`), the ruler's projection (`RulerSnap`: points onto the
    line, the outermost two kept) and
    the stillness anchor, when a hold is asked (`HoldWatch`, with the time
    given), the lazy brush, the ribbon, a gesture's hover place
    (`GestureHover`), the feedback each `haptic` kind plays
    (`HapticFeel`), and `HeldShape` and `ShapeHold` against every case
    of `tests/shared/inkhold.json`
  - the brush table against every row of `tests/shared/inkwidth.json`
    (`InkWidthTests`), the calligraphy rows with their `nib`. Both
    fixtures are resources of the test bundle (`project.yml`), so the
    Swift ports are held to the rows the web's tests read. Only
    `shapes.test.mjs` reads `inkhold.json`; `finishStroke.test.mjs` and
    the server's `test_shared_fixtures.py` read `inkwidth.json`
  - the address, navigation and colour rules, and the keyboard accessory
    bar hidden on a real `WKWebView` (`ShellTests`)
  - the local server's port rule, environment, password shape, module
    paths, health rule and state file, and the saved servers, an earlier
    version's keys included (`ServerTests`)
- `LocalServerStartTests` (in `ServerTests.swift`) starts the embedded
  server for real in the test host. It checks the bundle's layout first,
  so an unprepared build says what is missing. It starts the server with
  the account the app would, a password made for the Keychain, or, where
  the Keychain refuses it, with an account it seeds itself
  (`LocalServer`'s `seed`): CI's test host is unsigned
  (`CODE_SIGNING_ALLOWED=NO`) and has no Keychain entitlement, so the
  app's path gets nothing there and the server would seed an account
  whose password only its log knows. A test host Xcode signed uses the
  Keychain as the app does, so the app on that simulator signs in
  silently afterwards and the test signs in again on its next run. Then
  it waits up to three minutes for `/api/health`, signs in as that
  account, uploads a one-page PDF it builds, creates the page for it and
  reads the document back: the manifest (`/api/pdf-info`, one
  page of 612 × 792 points) and
  a page picture (`/api/ai/page-image`, an `image/*` body). The picture
  is the proof that PyMuPDF's frameworks load and render: a server
  without PyMuPDF starts and counts pages all the same (`pdf_text` falls
  back to PyPDF2) but answers 404 there. It is CI's one check that the
  bundle runs: a wrong layout, a broken framework conversion, a missing
  wheel or an import error fails here instead of on an iPad, and a call
  that does not answer as expected fails with the server's log tail. It
  skips when the app had already started its own server (a simulator
  whose last pick was This iPad), since an interpreter starts once per
  process, and skips the PDF part on a simulator whose data directory
  has accounts already and the Keychain holds no password for them (the
  seed reaches nothing there; a directory the test itself seeded on an
  unsigned host is one until deleted), or that a newer build upgraded
  (the server is up but answers every `/api` call with the
  `data_directory_not_upgradable` 503).
- `backend/tests/test_ios_requirements.py`: every server dependency is in
  the iOS requirements or excluded with a reason, and nothing excluded is
  still listed.
- `backend/tests/test_ios_entry.py`: `gamma_ios.py`'s supervisor with a
  stand-in for uvicorn's server: a failed start is written to the state
  file and retried on `restart()`, a restart of a running server stops it
  and runs a new one without the state ever saying `stopped`.
- `frontend/tests/host.test.mjs`: `host.js` with a fake `window` (the
  queue delivered, replies, unknown types, no host).
- `frontend/tests/openedFiles.test.mjs`: `openedFiles.js` against a
  stand-in shell. It covers the list, a malformed entry left out, a file
  read whole over several chunks, a file the shell no longer holds, what
  each kind of file becomes, and where files go: the workspace shown, a
  shared, public or clone one with the personal one to offer, and a
  read-only one sending them to the personal one.
- `frontend/tests/hostInk.test.mjs`: the claim's tool from a preset's
  look (keys left out at their defaults, the dash in CSS px, a
  calligraphy pen's `nib` and `nibRoll`), the host's samples as pointer
  events (degrees to radians, no tilt from four values, the seventh value
  as `twist`), and a shape's params between page points and CSS px;
  `shapes.test.mjs` runs `stillSince` and `followHold` over
  `tests/shared/inkhold.json`, and `pencilActions.test.mjs` the gesture
  choices, the squeeze's phases (an unknown one included), the held
  squeeze's toggle either way over `usePencil`'s step (`pencilStep`),
  its gate and the lasso's selection a hold restores, and the busy
  rule.
- The `ipad` browser group's shell steps (`tests/e2e/scenarios/ipad.mjs`)
  fake the host (`fakeShell`, declaring every flag above). A pen down on
  a PDF page is claimed with its place, tool and look (a Brush preset's
  `brush`, a dashed one's `dash` in CSS px, `stabilize`, a highlighter's
  `blend`, `pressure: false` with pressure off); the reply ends the
  draft, and the pointer's own events save nothing. The host's samples
  commit as an ink block with the right page units, pressures and times,
  six-value samples with their tilt (`xyptazwo` for a Pencil preset), and
  a stroke with no samples saves nothing. The stand-in sends `hold`. A
  still line answers `shape` in CSS px and posts `haptic` `snap`, and the
  lift after a slow move commits the refit line (`xyt`, with its `shape`
  record). A fast move right after the snap commits the freehand stroke,
  and so does a lift whose `stroke` names no hold (it beat the answer).
  With strokes copied, a hold that never moved answers `cancel` and opens
  *Paste here*; under a stand-in without `hold` nothing answers it. A
  calligraphy preset's claim carries `brush: "calligraphy"`, its `nib`
  and `nibRoll`. A `stroke` whose first sample rolls commits with the nib
  turned by it, and with *Lock angle* on without. With the ruler on, a
  claim that starts by its edge carries `snap`, and the stroke commits as
  the projected line (`xyt`, a `line` shape record). The theme message
  matches `theme-color`. The gestures switch tools under *System
  setting*, and a double tap set to *Undo* undoes. A squeeze set to
  *Eraser while held* (`squeeze` `began`, then `pencil` `ended`) arms the
  eraser and goes back, as its cancel does. A short one stays, and the
  next short one gives the pen back (a held one, with the eraser armed,
  holds the pen). A squeeze in a phase the page does not know does
  nothing. The lasso's selection comes back with the lasso, and none
  when the squeeze erased it. A gesture during a claim does nothing. Each
  switch posts `haptic` `tool`, and a pen lasso that selects posts
  `select` at its lift, none with *Haptics* off. The Apple Pencil
  settings show only under the stand-in (`ink.mjs` checks a browser has
  none). The account menu's Server… row and the sign-in page's Server…
  link post `servers`, and no install offer shows. The stand-in also
  holds opened files, read in 1000-byte chunks. A PDF held when the page
  loads becomes its page at the library's top and opens. Files handed
  while the page is up, with `filesOpened`, follow: a Markdown note
  becomes a page, an `.ntb` goes through the Notability review, a `.zip`
  opens the Import dialog without Logseq and imports as Markdown, and a
  `.heic` is let go with a status. In a shared workspace the question
  shows: *Cancel* adds nothing, and the personal workspace's button
  reloads there and opens the PDF. A workspace the account only reads
  sends the PDF to the personal workspace, with the status saying why.
  Every file is let go (`fileDone`).

A change to the bridge (`host.js`, the ink layer's claim, App's theme,
gesture or Server wiring, the sign-in pages, or anything under `ipad/`)
runs:

```bash
cd frontend && node --test tests/host.test.mjs tests/hostInk.test.mjs tests/shapes.test.mjs tests/pencilActions.test.mjs tests/openedFiles.test.mjs && npm run e2e -- --group ipad,ink
```

and, for `ipad/**` and for the two fixtures it reads
(`tests/shared/inkwidth.json`, `inkhold.json`), the XCTest suite on a
Mac, which `ipad.yml` does in CI
([github_actions.md](github_actions.md#ipadyml)). A change to
`backend/requirements.txt` runs `test_ios_requirements.py`, and
`ipad.yml` runs for it too. Other backend and frontend changes reach This
iPad at the app's next build, as the same code the other checks test.
`ipad.yml` does not run for them, so a change that matters on the device
(a PyMuPDF call the server did not make before, a native import, a process
spawned) is worth a manual dispatch.
`bash ipad/scripts/build-python.sh --check` resolves every pin for both
platforms without Xcode, on macOS or Linux; `check.yml` runs it on every
pull request.

### Device checks

Only an iPad can show these, and none has been run yet:

1. Whether WebKit sends the page a `pointercancel` once the recognizer
   begins, and whether preventing its recognizers stops a pan begun
   meanwhile. The page does not depend on the `pointercancel` (step 5),
   but a scroll that starts before the claim would move the page.
2. The claim's round trip under load, and the 10-point reach against the
   place WebKit reports for the `pointerdown`.
3. Palms during a captured stroke: swallowed by the recognizer, and
   refused by the page.
4. Pointer events in the web view equal to Safari's: pressure, tilt,
   hover, coalesced and predicted events.
5. The keyboard without its accessory bar (`hideKeyboardAccessoryBar`,
   tested on the simulator's WebKit) on a device's WebKit, and the visual
   viewport's keyboard behaviour the editing bar depends on ("Where it
   sits", below).
6. Recording, exports through the share sheet, the file picker and the
   clipboard.
7. The Gamma Cloud sheet: Google in `ASWebAuthenticationSession`, the
   loopback return, the sheet closed by the custom scheme.
8. The swap from the native line to the web's outline, in every look:
   the ribbon's width and the Brush's start taper, a pencil's shade, a
   dashed line, a highlighter multiplied (`compositingFilter`, which
   Apple documents as unsupported on iOS layers: if it does not blend,
   the highlighter shows at its plain opacity), and a held shape
   refitting under the pen.
9. A terminated web content process: the reload and the web app's
   restoration.
10. The local server's first start on a real iPad: how long it takes,
    and the memory of the server and the page's process together.
11. The server after the background and a locked screen: whether iOS
    reclaims its socket, how often the recheck restarts it, and how long
    the restart takes behind the start page.
12. A device build signed with a real team: `install_python`'s
    frameworks loading on the device, and the two MuPDF frameworks the
    post-build step makes: `import pymupdf`, then a PDF read through the
    local server (its text, a page picture). The simulator's half of this
    is CI's start test ([Tests](#tests)); a device enforces the code
    signature of everything it loads, which the simulator does not, so
    it stays a check of its own.
13. The data directory in Files, and a restore from an encrypted backup
    bringing the password back with the data.
14. A clone on This iPad syncing with another server, and Gamma Cloud
    sign-in on the local server.
15. The hold under a real hand: a resting Pencil's jitter against the
    3 pt slop, the timer firing while WebKit's scroll view tracks, and
    how soon after the pen stops the shape shows.
16. The shell's tilt against Safari's for the same pose, and, on an
    Apple Pencil Pro, the squeeze's phases and the haptics' places: a
    gesture's tick at its hover place, and one made with the Pencil too
    far off to hover (no place: the plain `impactOccurred()`, which may
    play on no motor at all).
17. The live line's frame time on a long Brush or Pencil stroke:
    `LiveStrokeView` builds the whole ribbon again every frame, a disc
    and a quadrilateral per kept sample, filled at once. If it drops
    frames, the fix is to move the settled part into a finished sublayer
    and build only the tail again: a live width never changes once known
    (the live Brush's start taper reads only a sample's own arc length),
    and the Pencil's shade is the layer's one colour.
18. The page edits when the web view goes away with a drawing's upload
    still on its way (the app closed from the app switcher, a server
    switch, a reload): whether their POST lands, with keepalive or as
    its plain copy ([collab.md](collab.md#keepalive)). A closing WebKit
    tab refuses the keepalive copy. Its plain copy reached a local server
    from WebKit on Windows in 13 of 45 closes at 1 KiB and 17 of 90 at
    60–63 KiB. A 65 KiB one was often cut at about 64.8 KB, and almost none
    arrived once a connection took 50 or 200 ms to open
    ([research/keepalive-abort.md](../research/keepalive-abort.md)).
19. The barrel roll on an Apple Pencil Pro: `rollAngle`'s zero and sign
    against the nib (a clockwise roll should turn the nib clockwise), its
    noise at the first sample, and whether WebKit gives the page a
    `twist` for a Pencil it did not claim.
20. The ruler's line under the Pencil against the committed one, at the
    edge's offset after a zoom, and the calligraphy ribbon's swap to the
    web's flat nib.
21. The three haptics on an Apple Pencil Pro: a held shape's
    `pathCompleted`, a pen lasso's `selectionChanged` and a gesture's
    light impact should each feel distinct, at its place, and *Haptics*
    off (Settings → Reading & editing → Apple Pencil) should
    silence all three. `HapticFeel` chooses which feedback each kind
    plays. The impact's style, and a feedback it does not list yet (such
    as `alignmentOccurred(at:)`), are set in `PencilHaptics.play`. The
    shell calls no `prepare()`, so note a tick that lags its shape or its
    selection.
22. Opened files ([Opened files](#opened-files)). Gamma should be listed
    in the share sheet and Files' *Open in* for a PDF, a `.md`, a `.zip`,
    a `.note` and an `.ntb`, with Notability installed and without it.
    An `.ntb` not listed with Notability installed would mean Notability
    exports a type of its own for `.ntb` after all, whose identifier then
    belongs in the NTB document type in `project.yml`. Check a file shared from
    Mail (a copy in `Documents/Inbox`, which should not stay in Files), a
    file opened in place from iCloud Drive (its security scope), a file
    shared at a cold launch and while the server picker is up, and the
    time a large PDF takes to cross the bridge.

### Not built yet

- **Background running.** iOS suspends the app soon after it leaves the
  screen, and the server with it. So a clone syncs only while the app is
  open. A socket reclaimed meanwhile is replaced when the app is active
  again ([Starting, and the log](#starting-and-the-log)); a sync round
  while the app is in the background would take a `BGProcessingTask`,
  and is not built.
- **A store release.** TestFlight builds exist: a Mac mini signs and
  uploads one for every commit on `main` whose message carries a new
  `ipad-test-release-NNN` marker. Every desktop release from `main`
  pushes one, and the `release-ipad` skill sends one between releases
  ([github_actions.md](github_actions.md#the-ipad-testflight-release)).
  CI itself signs and uploads nothing, the App
  Store submission is not automated, and App Review has not been tried
  ([research/ipad-web-shell.md](../research/ipad-web-shell.md), risk 9).
- **PyMuPDF on a device.** The framework step in `project.yml` was
  written and dry-run on Windows. On the simulator CI checks it: the
  start test ([Tests](#tests)) reads a PDF through the server, so a pull
  request whose MuPDF frameworks do not load fails there. No device has
  imported `pymupdf` from the bundle (device check 12), and the only App
  Store validation the hand-made frameworks have had is App Store
  Connect's processing of TestFlight build 9, which complained of nothing
  but the two OpenSSL manifests ([How the bundle is made](#how-the-bundle-is-made)).
  What remains to confirm is listed in
  [research/pymupdf-ios.md](../research/pymupdf-ios.md).
- **Moving data between servers.** Nothing moves a library from This iPad
  to another server or back, beyond clones and the web app's per-workspace
  Export and Import, as on the desktop.

## The installed web app

The home-screen app is the web app installed to the home screen. Its
browser ink layer already gives Apple Pencil pressure, tilt, hover, palm
rejection and Safari's touch-gesture handling
([handwriting.md](handwriting.md)). Every other feature (the block editor,
search, AI, sharing, workspaces) is the same React code the desktop shell
hosts. [research/ipad.md](../research/ipad.md) has the reasoning, and
what the upstream fork's PDFKit/PencilKit app was measured against.

### What the user does

1. Open the server's URL in Safari on the iPad and sign in.
2. Share button → **Add to Home Screen** → Add. Chrome and Edge on a
   desktop or Android tablet offer **Install Gamma** in the address bar
   for the same result.
3. Gamma opens full screen from the icon, without Safari's toolbars,
   signed in as before (the session cookie lives a year; the installed app
   has its own cookie jar, so the first launch may ask once).

Writing then works as everywhere else: a Pencil draws right away, fingers
scroll and pinch, the tool strip's presets are per browser (the installed
app counts as one browser, separate from Safari).

### What the install adds, and where it lives

| Piece | Where | Notes |
|---|---|---|
| The manifest | `frontend/public/media/manifest.webmanifest` | `display: standalone`, `start_url` `/` (the app then picks the last used workspace), the three PNG icons. Unversioned, so under `/media/` (sent `no-cache`, [repository.md](repository.md)). The backend registers its media type, `application/manifest+json`, because Windows and slim images lack it (`gamma/app.py`; `tests/test_static.py`). |
| The icons | `frontend/public/media/icons/apple-touch-icon.png` (180), `icon-192.png`, `icon-512.png`, `icon-maskable-512.png` | Rendered from the one brand mark by `tools/branding/build.mjs` like every other icon ([design/brand](../../design/brand/README.md)); never edited by hand. The `bleed` option of `mark.mjs` gives them a full-bleed square plate — the OS masks the corners itself, and iOS paints transparent corners black — and the maskable one keeps the mark inside the inner 80%. |
| The head tags | `frontend/index.html` | `manifest`, `apple-touch-icon`, `apple-mobile-web-app-capable` / `mobile-web-app-capable`, the title, `apple-mobile-web-app-status-bar-style` **default**; the pre-paint script adds `theme-color` for the stored theme (below). |
| The status bar colour | `index.html`'s pre-paint script, then `app/App.jsx`, `paintStatusBar` (the theme effect and the phone-topbar effect) | With the *default* status-bar style the bar sits above the viewport and is painted with `theme-color`. Before the bundle loads, the pre-paint script writes the resolved theme's chrome colour from a small table of each theme's `--bg-page` (`tests/themes.test.mjs` holds it to the tokens), so a cold start never shows another theme's bar. From then on the meta is set to the topbar's background (the chrome, `--bg-page`) whenever the theme changes, so the bar continues the topbar for every theme — or to the library's (the content surface, `--bg-surface`) while the compact layout shows no topbar. Both are resolved to a hex first (`tokenHex`): most tokens are `color-mix()` expressions. `black-translucent` was rejected: it puts content under the bar with fixed light text, wrong on the light themes. |
| Full-screen CSS | `shared/styles/app.css`, `html[data-shell]` (set by `shared/lib/host.js` when the app runs installed or in the iPad app's shell) | `.app` pads `env(safe-area-inset-bottom)` for the home indicator. There is no top inset to absorb: with the default status bar the bar sits above the viewport, and the shell's web view starts below it. The document's `overscroll-behavior: none` (html/body; the panes still scroll) sits outside the block: a Safari tab gets it too. |

### Layout by orientation

An iPad's Safari sends a desktop-class UA, so the layout follows the
screen, not the device (`PHONE_MQ` / `useIsPhone` in App.jsx):

- **Upright** — a coarse pointer, portrait, at most 1024px wide (every iPad
  up to the 12.9" Pro, and Android tablets) — gets the compact shell the
  phone uses (`.phoneUI`): the PDF or the notes full width, Notes and Chat
  as full-screen panels, one bottom bar (below). Two docks in 820px would
  leave ~200px columns, too narrow for the chat header's buttons. At this
  width the bar's items are roomier and its popovers keep
  a menu's width at the right end instead of spanning the screen (the
  `min-width: 701px` block under the phone rules in `app.css`).
- **Landscape** gets the desktop docks, as on a laptop. Turning the tablet
  swaps the shell live; the dock arrangement is kept for the way back.
- A phone keeps the compact shell both ways: 700px wide or less, or a
  coarse pointer on a screen at most 500px tall.

#### The compact shell

- **The bottom bar** (`.phoneBottomBar`): view tabs on the left — Library
  (home: the topbar has no Home button here, so it stays on a page's bar
  and carries the `header.home` anchor), the page's PDF and Notes, Chat —
  and the topbar's actions on the right: Add, Search, a page's Share (and
  a clone's Sync pill), More. Every item is a 22px icon over its word
  (`.barLabel`, `--fs-2xs`, hidden on the desktop topbar), about 50px tall.
- **A page with sheets** ([notebooks.md](notebooks.md)) has Notebook and
  Notes tabs in place of the page's tab and the Notes panel's: they switch
  between its two views (`setNotebookView`), which close any panel over
  the page.
- **Panels and jumps.** Notes and Chat show as full-screen panels whose
  title is a plain label: no "⠿" grip, since a panel neither drags nor
  folds. A jump from the notes to the page (a handwriting or text box
  row's, a highlight's) closes the panel over it (`showOnPage`,
  `jumpToHighlightId`). Show note on the page brings the notes up
  (`showInNotes`): the Notes panel, or on a page with sheets its notes
  view.
- **More** is the account button (`header.account`) under another name:
  its sheet is the account menu with the rest of the topbar on top —
  Background tasks, the open folder's Share this folder, and the View
  menu's rows (`viewMenuItems`; of the window toggles only PDF, since the
  tabs switch Notes and Chat, then Undo, Redo, Go to page and Command
  palette). The tasks, folder-share and View buttons are
  not rendered; their popovers open from those rows in the sheet's place.
  A flyout (the account menu's Tours) opens inline under its row, since
  beside it the sheet's own scroll box would clip it. While something
  runs, More shows the tasks spinner.
- **The topbar** holds only tabs and Back, so with neither (a library with
  no tab open) it is not shown (`.topbar.phoneBare`), and theme-color
  switches to the library's `--bg-surface` so the status bar continues it
  (`paintStatusBar` in App.jsx).

### The editing bar

The notes take Tab, Shift+Enter and Ctrl chords for their structure and
formatting. The iPad's on-screen keyboard has no Tab or Ctrl, and no
dependable Shift+Return ([research/touch.md](../research/touch.md)). So
while a note's editor is open on a touch screen, a bar sits on top of the
keyboard (`editor/EditBar.jsx`):

**Insert** (the `/` menu) · Outdent · Indent · Move up · Move down · New
block · Bold · Italic · Inline code · Strikethrough · Link · Inline
equation · Next math argument · Undo · Redo … **Done**

The iPad app shows this same bar, since it is the web app.

- **Every tool is a command.** All but Undo and Redo are block commands
  ([hotkeys.md](hotkeys.md)), run with the context the row's keydown
  dispatches with (`commandContext` in `BlockTree.jsx`). So a button does
  what the same command does from a key or the palette.
  - Four of them exist for the bar and have no default chord:
    `block.insertMenu`, `block.newBelow`, `block.math` and
    `block.nextSlot` (each is described in [hotkeys.md](hotkeys.md)).
  - Undo and Redo are the page's block history (App's `undoBlocks`), as
    Ctrl+Z.
  - A button whose command's `when` fails is greyed out: Outdent at the
    top level, Indent and Move up on a first child, Move down on a last.
  - Next math argument is Tab's hop in a math snippet (the next `{}`
    slot, past a `\right`, out of the span), which an on-screen keyboard
    has no key for. It is the one transient tool (`TRANSIENT`): it shows
    only while its `when` holds, the caret in math with somewhere to go,
    instead of greying out.
  - Done blurs the editor, which closes it and the keyboard.
- **When it shows.** It shows when the primary pointer is coarse, or the
  last press was a finger or a pen (a touch laptop). Where the primary
  pointer is fine, a mouse or trackpad press never brings it. It is read
  once when the editor opens (`touchTyping()` in `shared/lib/pointer.js`,
  [ui-design.md](ui-design.md#one-behaviour-for-mouse-and-finger)). A
  read-only page opens no editor, so it has no bar.
- **What it looks like.** The markup tool strip's surface and separators
  (`.pdfInkBar`, `.pdfInkSep`), centred and floating 6 px above what it
  sits on. Its `ctlBtn`s carry the strip's 16 px icons and are 36 px, the
  ink menu's finger size.
- **Where it sits.** It is `position: fixed`, its `top` set from the
  visual viewport's bottom, the top of the keyboard (`measure`). iPadOS
  does not shrink the layout viewport for its keyboard, and `top` is
  measured in the layout viewport, as `visualViewport.offsetTop` is.
  - iPadOS moves the visual viewport itself: the keyboard slides, and a
    scroll pans the view under the keyboard. Safari draws that move at
    once, but a script can only follow a frame later, so a bar chasing it
    would shake. Instead the bar steps aside while its place keeps
    changing (`.moving`: invisible, not pressable). It fades back in once
    its place has been still for 150 ms (`useBarPlace`).
  - Opening an editor counts as a move for 350 ms, the keyboard's rise, so
    the bar appears with the keyboard rather than under it.
  - The events do not always come at the end of a move, and none come when
    the keyboard returns to an editor that kept the focus. So the
    viewport's and the window's events, a focus change and a tap each
    watch the place for a second, a frame at a time.
  - It never sits lower than the top of the compact shell's bottom bar.
    That bar must stay reachable when no keyboard is up: a new page opens
    its first block's editor by itself, and a hardware keyboard raises
    none.
  - With neither a keyboard nor a bottom bar, it sits on the screen's
    edge, clear of the home indicator (`.editBar.atEdge`).
  - Its layer, `--z-editbar`, is over the compact shell's full-screen Notes
    panel and under the editor's own popups.
  - The room it takes at the visual viewport's bottom (`editBarSpace()`)
    is the editor's bottom scroll margin, so the caret scrolls into view
    above it. Caret-anchored popups (the slash menu, the math preview,
    `useCaretAnchored`) leave that room free.
  - On a narrow screen the tools scroll sideways and Done stays at the end.
- **The keyboard stays up.** iPadOS keeps it while focus stays in the
  editor, and raises it again only for a `focus()` made during a gesture.
  - A press never takes focus: the bar cancels `pointerdown` and
    `mousedown`, and its buttons have `tabIndex` −1.
  - A command runs inside `flushSync`, so a row that re-renders, or
    remounts, does so within the tap.
  - Indent and outdent remount the row. App keeps its editor open through
    that (`keepEditorThrough`, [hotkeys.md](hotkeys.md#the-block-commands-plumbing)),
    and the bar puts the caret back in the same tap (`keepCaret`).
  - A Backspace in an empty block moves on to the end of the block shown
    above (`onDelete`'s `focusAbove`).
- **The keyboard's own undo.** The iPad keyboard's undo and redo keys, and
  the three-finger swipes, send `beforeinput` `historyUndo` /
  `historyRedo`. A note row's `BlockCmEditor` hands them to the block
  history (its `onUndo`), so the browser never undoes CodeMirror's DOM
  there. An editor without `onUndo` (a text box's, an embed card's) leaves
  them to the browser.
- **The keyboard's own formats.** The iPad keyboard's B and I, and the
  Format menu over a selection, send `beforeinput` `formatBold`,
  `formatItalic` and `formatStrikeThrough`. Every `BlockCmEditor` runs
  `block.bold`, `block.italic` and `block.strike` for them, the commands
  of Ctrl+B, Ctrl+I and Ctrl+Shift+X.
- **Typing aids.** CodeMirror switches the system's typing aids off. In
  prose the editor turns them back on (`autocorrect`, `autocapitalize`
  sentences, `spellcheck`, `writingsuggestions`); in a code fence or math,
  where a corrected word is a broken command, they stay off. The caret's
  place decides as it moves (`typingAids` in `BlockCmEditor.jsx`).

Apart from the bar and the compact shell, the touch rules are the ones the
ink layer and the viewers already carry, and the shared ones every control
follows ([ui-design.md](ui-design.md#one-behaviour-for-mouse-and-finger)).
The viewport meta asks for no browser zoom, leaving pinch-zoom to each
viewer (the PDF's and the notebook's, [notebooks.md](notebooks.md)).
Safari tabs ignore it, so on any touch screen, in either layout, the app
also refuses Safari's gesture events (`touchScreen()`, `App.jsx`). The
viewers' own pinch runs off `touchstart` / `touchmove` and is unaffected.
`touch-action: manipulation` removes double-tap zoom.
`overscroll-behavior: none` on html and body keeps the document from
rubber-banding or pulling to refresh, in a Safari tab as installed. Only
the panes scroll.

### Not built (and why)

- **No service worker or offline shell.** Installability on iOS does not
  need one, and a cache layer would sit on top of the asset cache rules
  in [repository.md](repository.md). Offline use is the iPad app's,
  through its own server ([The local server](#the-local-server)).
- **Pencil double tap and squeeze** are not exposed to web content by
  iPadOS, and nor are the Apple Pencil Pro's haptics. Pencil hover is
  exposed (a `pen` pointer with no buttons) and shows the tool
  footprint. The iPad app forwards the gestures and plays the haptics
  ([The pen](#the-pen)).

### Tests

- `backend/tests/test_static.py`: the manifest's media type and cache
  header.
- `frontend/tests/editBar.test.mjs`: the Insert and Inline equation edits
  (`slashInsertAt`, `mathInsertAt` in `editor/markCommands.js`; neither
  applies inside math or a code fence), where Move up / down, Indent and
  Outdent apply (their `when`), Next math argument's hop and where it
  applies, and the bar's four commands starting unbound.
- `tests/e2e/scenarios/touch.mjs` (`npm run e2e -- --only touch`), by
  finger on an emulated tablet: Inline code and Strikethrough wrap a
  selection, Next math argument shows only in a math argument and hops to
  the next, the typing aids are on in prose and off in math, and a tapped
  picture's toolbar ⋯ opens the object menu.
- `tests/e2e/scenarios/ipad.mjs` (`npm run e2e -- --only ipad`), in
  Chromium touch contexts:
  - Tablet-sized: the manifest parses with `standalone` display, and every
    icon it and the `apple-touch-icon` link name is a real PNG.
    `theme-color` matches what sits under the status bar (the library with
    no tab open, the topbar once one is) and follows a theme change. The
    document's overscroll is off, the bundled stylesheet carries the
    full-screen rule (`html[data-shell]`, the home-indicator inset), and a
    browser tab gets no `data-shell`. Chromium cannot emulate
    `display-mode`: CDP accepts the feature, but `matchMedia` ignores it.
  - The iPad app's shell, with a stand-in host: [the native app's tests](#tests).
  - Upright (834×1194) is the compact shell: the PDF full width, Notes full
    screen from the bottom bar. Rotating to landscape brings the docks back.
  - A 390px phone: no topbar without tabs, the bar's words (Library Chat
    Add Search More, then Library Notes Chat Add Search Share More on a
    page), the listing bar inside the screen, a long title wrapping with its
    chips under it, More's sheet opening Background tasks, and the Library
    tab going home.
  - The editing bar, upright, by tap. A tap on a note opens it with the bar
    floating centred above the bottom bar, its buttons 36 px wide, Outdent
    greyed out at the top level.
    - A stand-in visual viewport plays the keyboard. The bar lands on it
      when its event comes before the move, stays on it when the view
      scrolls, and finds it back on the next tap when it returns with no
      event. Through a pan that goes on for 40 frames it never shows, and
      it shows where the pan stops.
    - Indent, Outdent and Move down change the saved tree, and after each
      the same block's editor still has the focus.
    - New block focuses a new empty block.
    - Bold wraps the selection; Undo and Redo take it back and put it
      back, and so do the keyboard's `historyUndo` / `historyRedo` input
      events.
    - Inline equation inserts `$x$`, its x typed over.
    - Insert opens the slash menu, and a tapped item applies.
    - Done closes the editor and the bar.
    - A mouse context's editor has no bar.
  - Only the device can check the Add to Home Screen flow, the status-bar
    paint and the home indicator. It is also needed for these editing-bar
    behaviors:
    - the bar riding on the on-screen keyboard (emulation has none)
    - the keyboard staying up through every button, Indent and Outdent
      included
    - the keyboard's own undo key sending `historyUndo`
- `node tools/branding/build.mjs --check` pins the icons to the mark.
