# README media tooling

Record real Gamma interactions at 2× and publish small, looping **animated WebP** images.
The production research is in [demo-production.md](../../docs/research/demo-production.md).

| Location | Contents |
|---|---|
| This directory | Recorders, renderers, shared helpers and [shot recipes](WORKFLOW.md) |
| `docs/assets/demos/` | Published WebP animations |
| `docs/assets/screenshots/` | Documentation stills |
| `extension/store/` | Chrome Web Store screenshots (`shoot-store.mjs`, [recipe](WORKFLOW.md#stills)) beside the brand build's promo images |
| `artifacts/readme-media/` | Ignored workspace export, private build, frame captures, timing files, masters and QA frames |
| `.claude/skills/readme-media/SKILL.md` | Agent entry point pointing here |

Delivery rules (the retina renderers apply them; `media_output.py` checks the result):

| Rule | Value |
|---|---|
| Capture | A 1440 × 900 window on a 2× screen: `launchRetina()` and `startCapture()` (`runtime.mjs`) save the screencast as 2880 × 1800 JPEG frames. Playwright's `recordVideo`, even with `deviceScaleFactor: 2`, records at 1×. Light English UI, an arrow cursor with a ring on each press, eased pointer travel |
| Frame | 1600 × 900: warm paper `#f6f4ef`, the app on a rounded card with a fine edge `#e3e0d8` and the illustrations' soft shadow (`compose.Frame`) |
| Camera | Still by default. A zoom or pan (`compose.Camera.move`) is a 0.5–0.6 s eased move with a light motion blur. Moving frames are what an animated WebP pays for (up to about 1 MB per move), so use one only where the text would otherwise be too small. Don't reframe inside a dissolve: blending two framings costs as much as a move |
| Edit | Trim setup and long still stretches (`compose.quiet`), cut model waits; each cut and the loop back to the start dissolve. Never speed up typing, drawing or streaming |
| Export | 25 fps animated WebP from a lossless master (`media_output.encode_master`: quality 75, moving frames down to 40), each no larger than the file it replaces and below 5 MiB; no MP4 or GIF copy |

## Setup

Use the existing frontend dependencies, Playwright Chromium, and the backend venv
with `imageio-ffmpeg`, Pillow and NumPy. From the repository root in PowerShell:

```powershell
# Only if dependencies/browser are missing:
npm --prefix frontend ci
npm --prefix frontend exec -- playwright install chromium
backend/venv/Scripts/python.exe -m pip install imageio-ffmpeg pillow numpy

# Prompts for the curated demo password, or accepts DEMO_PASSWORD.
backend/venv/Scripts/python.exe tools/readme-media/export-demo.py

# Avoid replacing a running developer frontend's build.
npm --prefix frontend run build -- --outDir ../artifacts/readme-media/dist
$env:GAMMA_E2E_DIST = (Resolve-Path artifacts/readme-media/dist).Path
```

On Unix use `backend/venv/bin/python` and
`export GAMMA_E2E_DIST="$PWD/artifacts/readme-media/dist"`.
Initialize fnm normally if Node is not on PATH. If the browser is installed in a
custom cache, set `PLAYWRIGHT_BROWSERS_PATH` for both installation and recording.
The extension recorder needs full Chromium, not just the headless shell.
`launchRetina()` turns on GPU compositing: with software compositing the 2×
readback throttles input, and a scripted one-second stroke took three.

## Highlight, annotate and draw

```powershell
$env:DEMO_EXPORT = (Resolve-Path artifacts/readme-media/demo.zip).Path
$env:MEDIA_SCRATCH = Join-Path (Get-Location) 'artifacts/readme-media/annotate-and-ink'
node tools/readme-media/record-ink.mjs --annotate
backend/venv/Scripts/python.exe tools/readme-media/render_feature_demos.py annotate-and-ink
node tools/readme-media/check-media.mjs annotate-and-ink
```

The recorder starts an isolated backend through the e2e harness, imports the
curated export into a throwaway account, records, and stops the server. The
story: highlight "robust quantum information storage" and note why it matters,
circle the claim it makes possible ("non-local connectivity"), draw an arrow
from the phrase up to the claim, then lasso the arrow and recolor it red. After
a reload it checks the annotation, the three strokes and the two recolored ones.

`DEMO_EXPORT` overrides the archive, `MEDIA_SCRATCH` the capture directory, and
`PAGE_ID` the paper. The gestures are placed from the text layer of the curated
atom-arrays paper (at 1440 x 900 with Notes open). `--inspect` saves the text
layout.
`render-ink.py --scratch PATH --out PATH` renders the ink-only capture (no
annotation) to `demo-ink.webp`.

## Ask an AI about your papers

Use the demo account's configured AI provider and a disposable workspace. Set
`DEMO_PASSWORD` in the environment without logging it. Then:

```powershell
$env:MEDIA_SCRATCH = Join-Path (Get-Location) 'artifacts/readme-media/revised'
node tools/readme-media/suite-workspace.mjs
node tools/readme-media/record-native-agentic.mjs
backend/venv/Scripts/python.exe tools/readme-media/render_feature_demos.py native-agentic
node tools/readme-media/check-media.mjs native-agentic
node tools/readme-media/suite-workspace.mjs --remove
Remove-Item Env:MEDIA_SCRATCH
```

One PDF chat, opening on Figure 1 (page 2): Ctrl+drag panels c and d into the
chat and ask about them, hover a citation in the answer for its quote and
follow it to the passage, then "find the two most-cited papers that build on
this one and save them". The agent searches online, the save waits on its
approval card (Save papers is Ask by default), and **Allow in this chat** lets
both land; the render closes in on those steps. The recorder verifies the
figure on the first question, the exact citation match, both questions with the
PDF's context, and two saved actions with two new pages in the library. Record
in a fresh workspace each time: a second run finds the papers there already and
shows no card, and a paper deleted for good came back when saved again.

`--inspect` captures the layout without sending AI requests.
`GAMMA_MEDIA_DIST` points at a private frontend build; only its static files are
served, PDFs and APIs still reach the real server. The render cuts the model's
waits. Model timing varies, so inspect the timestamps and sample frames after
each capture.

## Ask an AI in the notes

```powershell
$env:MEDIA_SCRATCH = Join-Path (Get-Location) 'artifacts/readme-media/agentic-notes'
node tools/readme-media/suite-workspace.mjs
node tools/readme-media/record-agentic-notes.mjs
backend/venv/Scripts/python.exe tools/readme-media/render_feature_demos.py agentic-notes
node tools/readme-media/check-media.mjs agentic-notes
node tools/readme-media/suite-workspace.mjs --remove
Remove-Item Env:MEDIA_SCRATCH
```

The shot needs `rabi-slide.png` in that directory: a picture of an equation (it
was drawn with matplotlib's mathtext, Computer Modern). A new notes page, at a
130% interface size since the note and the chat sit at opposite edges: the
cursor goes on a block, the slide is pasted into the chat (a picture in a note
block does not reach the model; one in the chat does), and the request asks
for the equation under that block. The insert waits on its approval card (Edit
note blocks is Ask), and **Allow once** lands it rendered. The recorder
verifies that the block under the cursor's holds the LaTeX and that the request
carried the picture.

## Take notes: a page to write on

After building the private frontend above, run:

```powershell
node tools/readme-media/run-case.mjs notes
backend/venv/Scripts/python.exe tools/readme-media/render-suite.py notes
node tools/readme-media/check-media.mjs notes
```

Notes uses an isolated server and imports `artifacts/readme-media/demo.zip`;
it needs no demo login or suite workspace. It records at a 130% interface size
(Settings → Appearance): markdown and a `[[` link, a display equation typed
with autocomplete, then `/note` makes the next block a sheet and a stylus
sketches on it. The pen is real pen input (CDP `pointerType: 'pen'`), which
writes on a sheet with the tools closed, since the sheet's own pen button sits
below it. After a reload the recorder checks the text and the sheet's strokes.

## Search: in the paper, and a label by Ctrl+P

```powershell
node tools/readme-media/run-case.mjs search
backend/venv/Scripts/python.exe tools/readme-media/render-suite.py search
node tools/readme-media/check-media.mjs search
```

It runs in the suite workspace (below). The case files the two quantum papers
under topic labels ("quantum computing" on both, "neutral atoms" and "error
correction"; "machine learning" on the Attention paper), and resets the
account's paper-view search to its compact default. Reading the atom-arrays
paper: Ctrl+F finds "rydberg" in this PDF and Enter steps to the second and
third match; then Ctrl+P, "quantum comp", and the label row opens the label's
two papers.

## Share and work together

```powershell
node tools/readme-media/record-collab.mjs
backend/venv/Scripts/python.exe tools/readme-media/render_feature_demos.py collab
node tools/readme-media/check-media.mjs collab
```

An isolated server with the curated export in a shared workspace, "Quantum
lab": Alex owns it, Maya and Sam edit. The shot needs `fig1cd.png` in
`artifacts/readme-media/collab/`, Fig. 1c–d of the atom-arrays paper (pages
render with the backend's pypdfium2). Only Alex's window is captured, at a 130%
interface size; Maya and Sam are real sessions in a second browser, so their
carets, name tags, row chips and avatars are Gamma's own. Alex and Maya type
two questions into one list block, Maya on its first line so Maya's name tag sits
above the block, not over Alex's line; meanwhile Sam types a caption into an empty
block and pastes the figure under it. The typing pauses every two words: an
edit is sent once its writer pauses (collabSession's typing debounce), so
without the pauses the others' text would arrive in one piece. The recorder
checks that the list block holds both lines and the other the caption and the
picture. `--inspect` saves the prepared page.

## Connect your research: the Gamma Connector

```powershell
node tools/readme-media/run-case.mjs connector
backend/venv/Scripts/python.exe tools/readme-media/render-suite.py connector
node tools/readme-media/check-media.mjs connector
```

The Connector saves a Physical Review Letters paper (Levine et al.,
10.1103/PhysRevLett.123.170503) from its APS page: the badge has found it, the
popup shows the registry's title and authors, a folder and a label are picked,
Save, and the PDF opens in Gamma with both. The case starts an isolated server
with registry lookups on and the atom-arrays paper filed under
Quantum/Neutral atoms, so that folder is in the popup's list. Full Chromium at
2× with the unpacked extension (`launchRetinaExtension`), its page 40 px
shorter than the frame: the render puts a browser toolbar there, screenshots of
the recorder's own toolbar page with the extension's real badge. The popup is
opened as a page through its `?tab=` hook and hung under the toolbar icon.

APS answers a headless browser with a Cloudflare check but serves curl the
page, so the recorder fetches the site's requests with curl and hands them to
the browser at their own address; the extension reads the real page. The page
names the institution whose network fetched it ("Access Provided by …"): the
recorder removes those notices before recording. The popup's capture ends with
the pointer on *Open in Gamma*: the click closes the popup, as Chrome's does,
and a screencast of a closed page never stops. The recorder checks the saved
page's PDF, folder and label through the API. `--inspect` saves the APS page
and the popup with its folder menu.

## Record the published suite cases

The suite uses a disposable workspace on the curated demo account so AI shots
can use its configured provider. Notes and the connector use an isolated
server/account instead (the extension targets the default workspace), as do
the ink and collaboration demos above.

Set `DEMO_PASSWORD` in the shell without committing or logging it; `BASE_URL`
defaults to `http://127.0.0.1:9001`, and `DEMO_USER` to `demo`. Then:

```powershell
node tools/readme-media/suite-workspace.mjs
# Run sequentially: scenarios change the disposable library.
node tools/readme-media/run-case.mjs notes
node tools/readme-media/run-case.mjs library
node tools/readme-media/run-case.mjs search
node tools/readme-media/run-case.mjs metadata
node tools/readme-media/run-case.mjs reference-links
node tools/readme-media/run-case.mjs connector
backend/venv/Scripts/python.exe tools/readme-media/render-suite.py all
node tools/readme-media/check-media.mjs
node tools/readme-media/suite-workspace.mjs --remove
```

`run-case.mjs` prepares each case in that disposable workspace: it clears the
note page, removes a paper that must visibly be fetched, or resets tabs. Run
library and search before reference-links, which removes and fetches the
QEC paper again. The library search demo is shown nowhere at the moment; the
README and the website show search. A failed recording leaves the workspace
available for inspection; remove it after finishing. Never read account
databases for credentials or commit cookies, passwords, exports, or raw
recordings.

## Delivery and review

`render-suite.py <name>` re-renders one published slot; `all` renders every
slot, including the four feature demos (`render_feature_demos.py`:
`annotate-and-ink`, `native-agentic`, `agentic-notes`, `collab`). Notes,
library, search, the connector and the feature demos are retina captures
composed by `compose.py` (the connector's three captures under a toolbar,
`render-suite.py`'s `Browser`). Metadata and reference-links are still 1× WebM
recordings, rendered through FFmpeg (`encode_webp`).

`encode_master` writes the WebP itself: each frame re-encodes only the 16 px
tiles that changed since that tile was last encoded, or that were last encoded
at a lower quality, and leaves the rest transparent. libwebp's animation
encoder compares each frame with the previous one, so slow changes (a settling
camera, a dissolve, a popup's fading shadow) piled up on screen as faint
blocks. `media_output.py` checks the container, frame timing, loop flag and
size before publishing. `check-media.mjs` then decodes every encoded frame in
Chromium (FFmpeg builds can encode animated WebP without decoding it) and saves
contact sheets in `artifacts/readme-media/qa/`. Review the contact sheets and
loop playback before updating README links. Timing manifests, masters and
render reports stay next to the captures in scratch.

## Optional experiments

The retained `agent` and `download-and-chat` cases need the account's AI
provider. They are not part of README production: `render-suite.py` writes
their renders as `preview.webp` next to the capture, and no README slot uses
them. The ink-only renderer and the three `gen-*.py` GIF experiments are also
retained for reuse; the GIF experiments write to scratch and have no callers.

`runtime.mjs` resolves Playwright through the frontend dependency, so no second
Node project is needed, and holds the recorders' shared pieces: the retina
launch, context options and capture, `BASE`, `readSession`, the cursor, paced
pointer travel, the curated page ids, a phrase's rect in the text layer
(`phraseBox`), a picture on the clipboard (`clipboardImage`) and opening the
chat (`openChat`).
