# Gamma for iPad

The iPad application uses a native PDFKit reader, native Pencil input, and a
local SQLite library. It imports PDFs and creates notebooks without a server.
A connected library keeps a local copy and uses Gamma's existing Mirror
protocol. There is no bundled Python runtime or localhost HTTP server.

## Reading and writing

Open **On this iPad**, choose **Add → Import PDF** or **New notebook**, then
open the document. Pencil writes; fingers scroll and pinch. The toolbar has
Pen, Monoline, Highlighter, whole-stroke Eraser, Partial eraser, and Select.
Select draws a lasso; drag inside its box to move the selected strokes. The
more menu contains color, width, duplicate, delete, resize, rotation, undo,
redo, and New ink group. Double-tapping Pencil switches the eraser.

Each completed edit saves immediately to the local library. Empty ink groups
retain their caption and child notes. Notes opens the document's ordinary
block tree; edit a note or add children through its context menu. PDFKit text
selection can become a Gamma highlight through the more menu.

Notebook pages are ordinary child blocks with stable IDs. **Add a page** at
the bottom creates the next page on demand. **Paper settings** selects blank,
ruled, grid, or dotted paper, color, spacing, dimensions, and orientation.
Apply to the current page or use the settings for future pages. Changing
paper does not transform handwriting. PDF export is generated from these
pages by the shared server/exporter rather than rewriting a PDF after every
new notebook page.

## Recordings

The microphone starts a mono AAC recording. Pause finalizes the current
segment; continuing starts another segment. Long recordings roll into
five-minute segments. Handwriting and page changes are mapped to the audio
recorder clock. Play follows pages and progressively reveals surviving ink;
tap a stroke during replay to seek to its audio with a two-second lead-in.
Untimed handwriting stays visible. Partial erasure preserves the original
stroke reference and timing in its remaining fragments. Playback does not
reconstruct erased writing or a full edit history.

Backgrounding, interruption, and leaving the reader stop the recording and
finalize it locally. Finalized recordings and their event sidecars are kept
until the canonical audio block commits, and retried on reopening. A damaged
unfinished audio container is retained and reported instead of being deleted.

## Connected libraries

Choose **Connect a library**, supply the HTTPS origin, account, workspace ID,
and an access token from the server's **Settings → Integrations**. The token
is stored in iPad Keychain, separate from the local database. Each connection
has its own local workspace directory.

Mirror sync runs on foregrounding and every thirty seconds while active;
**Sync now** is available in the library and reader. The status shows pending
changes and failed syncs. Both ways, Download only, Detach/Reattach, and Remove
connection keep the same meanings as the desktop Mirror UI. Removing a
connection retains local files. Conflict review appears in the library.
The app does not depend on background execution for durability or syncing.

After successful sync, local asset maintenance removes up to 200 unreferenced
files older than seven days. Full passes continue on subsequent successful
syncs until the backlog clears, then maintenance returns to a daily cadence.
Current documents, pending operations, retry checkpoints, conflicts, and
unacknowledged edits retain their assets. Offline-only libraries can run
the same pass through `GammaRepository.maintainAssets()`.

## Data and implementation

- `GammaCore/`: the local SQLite repository, immutable upload assets, operation
  journal, Mirror reconciliation, authentication guards and retry checkpoints.
- `GammaIPad/InkCanvasView.swift`: native coalesced Pencil capture, normalized
  pressure, estimated-property correction, temporary prediction, and commands.
- `scripts/ink-entry.mjs`: bundles the browser's **same pure ink module** and
  perfect-freehand into JavaScriptCore. The native renderer consumes the same
  curve outline with CoreGraphics. Native edits call the same codec, eraser,
  lasso and transformation functions; no PencilKit conversion is involved.
- `GammaIPad/ReaderController.swift`: PDFKit overlays, notebook sheets, common
  local saves, conflict-preserving refresh, paper and audio integration.
- `GammaIPad/AudioSession.swift`: segmented capture, recovery and playback.
- `GammaIPad/NotesController.swift`: ordinary block notes and their text editor.
- `GammaIPad/GammaApp.swift`: local libraries, Keychain connections and sync UI.

Gamma JSON remains the authoritative data: v1 PDF ink stays readable; v2
adds stable notebook anchors and source-stroke provenance for erased pieces.
PencilKit archives and per-stroke PNGs are never required. Strokes preserve
their IDs through style/geometry edits, and missing timing is not fabricated.
Immutable file bytes retain their content hashes during Mirror transport.

## Build and test

On a Mac with Xcode and XcodeGen:

```sh
npm ci --prefix frontend
node ipad/scripts/prepare-assets.mjs
node --test ipad/scripts/ink-entry.test.mjs
node --test frontend/tests/ink*.test.mjs
cd ipad
swift test
xcodegen generate
open GammaIPad.xcodeproj
```

Select an iPad simulator. For a physical iPad, configure your signing team and
a unique bundle identifier. Generated projects, resources and build outputs
are ignored. Windows can run the shared JavaScript tests, but cannot compile
UIKit or launch the iPad simulator.

[The iPad workflow](../.github/workflows/ipad.yml) builds resources, tests
GammaCore, runs native codec/rendering and UI tests on a simulator, and builds
an unsigned device app. Shared ink tests exercise both the source modules and
the generated JavaScriptCore bundle. Native audio recovery tests check that
recordings survive missing or failed saves and are removed only after a
successful commit. Changes to the frontend dependency manifests and source
app icon also trigger the workflow. Build logs, XCTest results and screenshots are
retained as artifacts. An unsigned `.app` still requires Apple signing before
installation; the workflow does not publish to TestFlight or the App Store.
The device archive is checked for its iPad-only device family and bundled ink
and icon resources. The device family is set on each target so XcodeGen's
target defaults cannot override it with iPhone support.

Before distributing, exercise real Pencil latency, pressure/tilt, palm
rejection, rotated/cropped PDFs, Split View, long recordings, interrupted
recording recovery, and two-device offline edits. Simulator tests cannot
validate physical Pencil input or guarantee microphone interruption recovery.
