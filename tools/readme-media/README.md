# README media tooling

Record real Gamma interactions at 2× and publish small, looping **animated WebP** images.
The production research is in [demo-production.md](../../docs/research/demo-production.md);
the storytelling the README demos follow is in
[demo-film-pipeline.md](../../docs/research/demo-film-pipeline.md).

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
| Story | One story per clip, about 10–20 s, told in beats a visitor follows without the README text: the first frame fully laid out and held a moment, each result held about a second, the end state at least 1.5 s before the loop |
| Camera | Still by default. A zoom or pan (`compose.Camera.move`) is a 0.45–0.6 s eased move with a light motion blur, at most two per clip, plus a move back to the opening framing if the loop needs it. Moving frames are what an animated WebP pays for (up to about 1 MB per move), so use one only where the text would otherwise be too small. Don't reframe inside a dissolve or a stream: blending two framings costs as much as a move |
| Edit | Cut every wait that carries no meaning: the model's first token, network and job progress, scrolls between pages, glides into a box (a wait where only a status pill moves may play at up to 3× instead). Pointer travel may play up to 1.3×, drawing up to 1.5×, streamed AI text up to 2×; typing plays at 1× (cut the middle of a long line instead). A cut dissolves (0.3 s, or the segment's own fade; a dialog that changes in place cuts hard), and the loop dissolves back to the first frame |
| Export | 25 fps animated WebP from a lossless master (`media_output.encode_master`: quality 75, moving frames down to 40), aiming at 2.5 MiB and below 5 MiB; a small H.264 `<name>-preview.mp4` stays in scratch for review, no GIF |

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

## How a take is recorded

The five README stories below (the two AI demos, the Notability import, the
collaboration and the Connector) record on `take.mjs`, which holds what they
share; each recorder holds only its story:

- `library()` starts an isolated server through the e2e harness (free port,
  throwaway data dir; never the developer's 9001, 5173 or 9002) with the
  curated export imported, and stops it again if the setup fails. No demo
  login, AI key or suite workspace is needed.
- `openPage()`, `hand()` and `typeText()` give the retina page, a pointer
  paced by the clock and keys typed at a person's speed.
- `Take` is one recording. `watch(probe)` marks the moment the viewer sees a
  change (an answer streaming, a card shown, a picture loaded) from inside the
  page; `mark()` marks the recorder's own actions; `frame()` keeps the boxes a
  camera may use; `check()` records every claim the clip makes, through the
  API and after a reload where it applies. `finish()` writes `timing.json`
  only when every check passed (otherwise `timing.failed.json`), and
  `compose.Take` refuses a folder without it, so only a verified take can be
  published. Screencasts hold a machine-wide lock, so takes in other
  checkouts wait instead of dropping frames.
- `demoModel()` runs `demo-model.mjs`, a scripted OpenAI-compatible chat
  model, and saves it as the account's AI connection ("demo-model"). Gamma
  builds the requests, runs the tools the model calls (real paper searches
  and downloads, real note edits behind their approval cards) and saves the
  chat; only the model's words and tool calls come from `models/<script>.mjs`,
  which checks what it says against the text Gamma sends it.

Every recorder takes `--inspect`, which plays the story without a capture and
leaves screenshots of its beats (record-native-agentic.mjs's stops at the prepared start) in the scratch folder,
`artifacts/readme-media/<name>/`. Its renderer (`render_feature_demos.py
<name>`, or `render-suite.py connector`) builds the edit from the take's
marks: segments in capture seconds, each with its playback rate.

## Highlight, annotate and draw: a Notability note, then Gamma's pen

```powershell
node tools/readme-media/record-ink-import.mjs        # --inspect: the story without a capture
backend/venv/Scripts/python.exe tools/readme-media/render_feature_demos.py ink-import
node tools/readme-media/check-media.mjs ink-import
```

The note is made by `make-notability-note.py`: the curated atom-arrays paper
with the marks a reader makes on an iPad (a highlighter over the abstract's
first sentence, an underline, a circle, a margin bracket, an arrow and the
handwritten words "key!" and "how?"), placed from PyMuPDF's word positions
and written by the backend's `notability_write.py` in the shape of
Notability's own export, without Gamma's private round-trip copy, so the
import goes through Gamma's real Notability reader. The repository's
Notability samples are not cleared for publication, so a note is generated
rather than shown.

The story: the library home, ≡ View → Import… → Notability notes → Choose
files… (answered by `setFiles`, so no picker shows), the review with the note
under "Library after import", Import to library, Import complete, Done; the
new page opens with the pencil marks and their ink cards in Notes; then
Handwriting tools → Blue pen rings "neutral atom arrays" and draws an arrow
up to "how?". The take checks the imported page's PDF and strokes per page,
and the three new blue strokes in the page's ink after a reload. The edit
plays the menus and the pointer at 1.3×, cuts the upload, the job's progress
and the page's load, plays the pen at 1.5×, and closes in once on the paper
while the pen draws.

## Ask an AI about your papers

An isolated harness server (registry lookups on) with the curated export and
the scripted model `models/ai-reading.mjs`; no account or AI key needed, but
the take reaches Crossref, OpenAlex and arxiv.org:

```powershell
$env:GAMMA_E2E_DIST = (Resolve-Path 'artifacts/readme-media/dist')
node tools/readme-media/record-native-agentic.mjs        # --inspect: the prepared start only
backend/venv/Scripts/python.exe tools/readme-media/render_feature_demos.py native-agentic
node tools/readme-media/check-media.mjs native-agentic
```

One chat on the atom-arrays paper, opening on Figure 1 (page 2) with its
caption just below the view, at a 150% interface size: Ctrl+drag panels c and d into the chat and ask "What do panels
c and d show?"; the answer cites p. 2, its chip previews the quote and jumps the
PDF to the marked passage. Then a plain click on the abstract's clause citing
refs 3–5 ("excitation into Rydberg states is used for entanglement generation",
made a highlight off camera by a real drag and the toolbar colour) puts it in
the chat, and "Read refs 3–5 and compare what each one showed."
runs Gamma's real `search_papers` (three titles, `kind: article`) and
`fetch_paper` (the arXiv copies the searches listed; `delegateReads` off) and
answers with a table citing the reference list on p. 6. Only the model's words
are scripted: before the table it looks for each fact in that paper's fetched
text and otherwise says it could not read it. The quotes are checked with
Gamma's citation matcher before the take; timing.json is written only when the
figure was sent, both citations resolve exactly, the passage was sent, the six
calls ran without error, the facts were grounded, the table has three rows and
the chat (two questions, two answers) is there after a reload. The edit cuts
the network waits, plays the model's Thinking waits at 2.5–3× (only the pill
moves), pointer moves at 1.2x and streams at 2x,
and frames the whole window from the first frame to the last but for one eased
push-in on the PDF pane (about 1.9x) as the citation jumps to its marked
passage. The research pill plays each label (Searching, Fetching) from the
first frame it rests whole, held still to about a second when the real call
was quicker, with hard cuts between labels; the recorder marks those frames
from the DOM (`…Shown`, `…Gone`).

## Ask an AI in the notes

```powershell
node tools/readme-media/record-agentic-notes.mjs        # --inspect: the story without a capture
backend/venv/Scripts/python.exe tools/readme-media/render_feature_demos.py agentic-notes
node tools/readme-media/check-media.mjs agentic-notes
```

An isolated server and the scripted model `models/ai-notes.mjs`. The notes
page "Rabi oscillations" is seeded off camera (an intro line, the
Hamiltonian as display math, and the caption the caret is on, so the chat
shows "Block at your cursor"), at a 130% interface size with the chat docked
right. A real Ctrl+V pastes a lecture slide into the chat (the recorder draws
`rabi-slide.png` with the app's own KaTeX), the request asks for the
equation under that block, the agent's `create_block` waits on its approval
card (Edit note blocks is Ask), and **Allow once** lands it rendered under the
caption. The take checks that the request carried the picture and the cursor
block, that the saved chat holds the call, and that after a reload the new
block sits under the caption with both equations rendered.

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
node tools/readme-media/record-collab.mjs        # --inspect: the story without a capture
backend/venv/Scripts/python.exe tools/readme-media/render_feature_demos.py collab
node tools/readme-media/check-media.mjs collab
```

An isolated server with the curated export in a shared workspace, "Quantum
lab": Alex owns it, Maya and Sam edit. Only Alex's window is captured, at a
130% interface size and narrower than the other recorders' so the presence
avatars, the peers' lines and the picture fit one framing. Maya and Sam are
real sessions in a second browser, so their carets, name flags and avatars
are Gamma's own; their sessions send each keystroke after 40 ms instead of the
app's 350 ms typing pause (`take.mjs` `fastPeerTyping`), so their words arrive
key by key, while Alex's view runs the app as it is. Maya types a question
after "Q1:" and Sam adds to the takeaway while Alex pastes a screenshot of
Fig. 1c, d (cut from the paper's PDF by `collab-figure.py`) and drags its grip
to size it. The take checks the picture's saved width on every screen, the
peers' words saved and on Alex's screen, and the same after a reload. The
edit plays at 1×, cutting only Alex's click out of the block.

## Connect your research: the Gamma Connector

```powershell
node tools/readme-media/record-connector.mjs [--inspect]
backend/venv/Scripts/python.exe tools/readme-media/render-suite.py connector
node tools/readme-media/check-media.mjs connector
```

The Connector saves a Physical Review Letters paper (Levine et al.,
10.1103/PhysRevLett.123.170503) from its APS page: the badge has found it, the
popup shows the registry's title and authors, a folder and a label are picked,
Save, and the PDF opens in Gamma with both. The recorder runs on its own
(take.mjs `library()`): an isolated server with registry lookups on and the
atom-arrays paper filed under Quantum/Neutral atoms, so that folder is in the
popup's list. Full Chromium at
2× with the unpacked extension (`launchRetinaExtension`), its page 40 px
shorter than the frame: the render puts a browser toolbar there, screenshots of
the recorder's own toolbar page with the extension's real badge. The popup is
opened as a page through its `?tab=` hook and hung under the toolbar icon.

APS answers a headless browser with a Cloudflare check but serves curl the
page, so the recorder fetches the site's requests (and the PDF behind the
page's `citation_pdf_url`, link.aps.org) with curl and hands them to the
browser at their own address; the extension reads the real page and the save
downloads the real PDF. APS can take half a minute to answer and now and then
answers 502, so a request is tried again on a server error, and the article
and its PDF are fetched while the server starts. The page names the
institution whose network fetched it ("Access Provided by …"): the recorder
removes those notices before recording. The browser's Gamma layout keeps the
notes alone in the right dock (a fresh layout also docks the chat, which on an
account without AI is a panel of "connect a service" cards).

One take (`take.mjs` `Take`), three screencasts: `frames/` the APS page,
`popup/` the popup (its height sampled, since the render cuts it out of a
1440 px page), `gamma/` the paper in Gamma; marks are each piece's own capture
seconds. The popup's capture ends with the pointer on *Open in Gamma* and the
drawn cursor's press ring (a pointerdown sent to the document alone): the real
click closes the popup, as Chrome's does, and a screencast of a closed page
never stops. The Gamma piece is shown once page 1 is painted, the chips are up
and the metadata look-up the opening starts has settled (no spinner or status
pill for 0.7 s). Checks: the badge detected the paper, the popup's title,
authors and "Saved to your library", the APS tab's badge turned to a check,
and through the API the page with its PDF, folder and label; the Gamma tab
opened it and still shows both chips after a reload. `--inspect` runs the
story without capturing and saves a screenshot of each beat.

The extension and the Gamma tab use the server as `localhost:<port>` (the
harness listens on 127.0.0.1, the same loopback server), so the popup's footer
and the address bar read like a local install. Save is pressed on its left
part, where the saved view has nothing, and the pointer stays there through
the save, so the cut past the wait shows one pointer hovering nothing.
The recorder's glides are paced by the clock (a slow page drops steps instead
of stretching a move). The pointer rests on the icon's upper left so the
badge stays in view. The APS page's screencast often sends no frame for the
drawn arrow's hiding, so the render paints the page's top strip under the
icon from its first frame while the toolbar draws the arrow.

The edit (`render-suite.py` `connector`) plays pointer travel at 1.25x, holds
the popup's title 0.85 s and the filled popup about 0.85 s, and cuts hard past
the save's wait: the press plays up to the frame before the progress row
shows (its "saving to your library…" gives way to the PDF fetch's words after
about 0.1 s, too short to read), and a dissolve between the form and the saved
view would ghost the form. "Saved to your library" holds 0.8 s. The new tab's
load is cut hard, like the browser's own switch to the new tab. The camera
closes in on the popup and the icon as the pointer heads for it and keeps
that framing across the cut to Gamma, where the notes column's title, folder
and label chips read at README size; the Gamma tab's pointer rests just below
the spot Open in Gamma was pressed (in the empty notes column, hovering
nothing). That holds 1.3 s; the camera then pulls back to the whole window,
which holds 1.2 s before the 0.3 s loop dissolve, so the loop blends two
whole-window frames: two camera moves.

## Record the published suite cases

The library, search, metadata and reference-links cases still use a
disposable workspace on the curated demo account. Notes runs on an isolated
server (`run-case.mjs` uses `take.mjs` `library()`), as do the five stories
recorded on `take.mjs` above, which are not part of this run.

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
`native-agentic`, `agentic-notes`, `collab`, `ink-import`). Notes,
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
them. The three `gen-*.py` GIF experiments are also retained for reuse; they
write to scratch and have no callers.

`runtime.mjs` resolves Playwright through the frontend dependency, so no second
Node project is needed, and holds the recorders' shared pieces: the retina
launch, context options and capture, `BASE`, `readSession`, the cursor, paced
pointer travel, the curated page ids, a phrase's rect in the text layer
(`phraseBox`), a picture on the clipboard (`clipboardImage`) and opening the
chat (`openChat`). `take.mjs` builds on it for the recorders made on it (see
"How a take is recorded").
