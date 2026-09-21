# Gamma for iPad

A small native iPadOS 17+ app: the existing Gamma workspace in WKWebView,
with PencilKit handwriting that saves **editable `gamma-ink` JSON**. Your
Gamma server remains the library. No Apple drawing files are uploaded.

## Use

1. Connect to your Gamma server's **HTTPS origin**, for example
   `https://gamma.example.com`, and sign in normally. Run the server and web
   frontend from this checkout so they include the native bridge and save API.
2. Open a PDF. Tap **Write with PencilKit** (the pen marked “iPad” in the
   PDF controls) to write on the current page. To edit existing handwriting,
   select one ink group in the web reader first, then tap that button.
3. Choose **Pen**, **Monoline**, **Highlighter**, or the whole-stroke eraser.
   Fine/Medium/Broad widths, five colors, and Undo/Redo keep the controls small.
   Pencil writes; fingers pan and pinch. Other groups are read-only context.
4. **Save** uploads the open ink and returns to the workspace. **Close** keeps
   a local draft; opening that same group (or a new group on that page) recovers
   it. After a save failure, retry or use **Save as new group** to keep both
   versions. A conflict never silently replaces remote handwriting.

The page background is a PDF.js snapshot in Gamma's existing rotated page
coordinates, capped at 2×, 4096 pixels per side and 8 million pixels. Ink stays
editable at every zoom; the background can soften at high zoom. Return to the
web workspace for text selection, switching pages, search, notes and AI.

## Build from Windows with GitHub Actions

The **iPad** workflow, [`.github/workflows/ipad.yml`](../.github/workflows/ipad.yml),
runs on `macos-15`. It generates the project with XcodeGen, runs XCTest on an
available iPad simulator, and compiles a Release device app with signing off.
It runs on relevant pushes/PRs and can be run manually from GitHub's Actions
tab once the workflow is on the default branch:

```powershell
gh workflow run ipad.yml --ref your-branch
gh run list --workflow ipad.yml
gh run download RUN_ID --name GammaIPad-unsigned
```

Artifacts are retained for 14 days:

- `GammaIPad-unsigned-device.zip`: device `.app`, **not installable until signed**.
- `GammaIPad-simulator.zip`: `.app` for the runner's Mac simulator architecture.
- `GammaIPad-tests`: XCTest results and build logs, also on failure.

This workflow needs no Apple account, signing secrets or provisioning profile.
It does not publish an IPA, TestFlight build or App Store release. Installing
on a physical iPad is the next step, using Apple signing; unsigned compilation
alone cannot install an app on an iPad from Windows.

## Build locally on a Mac

```sh
brew install xcodegen
cd ipad
python3 scripts/prepare-assets.py
xcodegen generate
open GammaIPad.xcodeproj
```

Select a simulator, or configure your signing team and a unique bundle ID
for a physical iPad. Generated assets use Gamma's existing brand icon.
Generated projects, assets and builds are ignored. Use `project.local.yml`
with an `include: [project.yml]` and local settings for persistent signing
overrides; do not commit certificates or credentials.

## Data and boundaries

- Cookies stay in WKWebView's persistent data store. Native code stores the
  server origin and local drawing drafts, not passwords or copied cookies.
- The bridge accepts only main-frame messages from the configured HTTPS
  origin. External links open outside the privileged workspace.
- Draft keys include server, account, workspace, document, parent, PDF page,
  dimensions and ink group. Writes are atomic and protected with iOS file
  protection. A draft contains the original open JSON and a local PencilKit
  editing snapshot; the snapshot is a recovery aid, never a server format.
- The web app sends saves with its normal account/workspace guards. The server
  compares the original `ink_url` under a write transaction and publishes the
  change through the existing operation log. Stable block IDs and canonical
  ink hashes make retries idempotent. Empty ink retains captions/child notes.
- Unchanged imported strokes keep their original JSON, IDs and channels.
  New native strokes are interpolated into `xyptaz` samples; native pen width
  is approximated by Gamma's simple pressure-width model. PencilKit and the
  web renderer are not pixel-identical. Textured brushes and masked erasures
  are intentionally unavailable; unsupported data is rejected, not flattened.
- This is not an offline library: reopening the web workspace/PDF needs the
  server. An already-open native page can keep drafts while disconnected.
  There is no background audio recording or note replay in this client.

## Verification

`GammaIPadTests` covers open/native conversion, unchanged strokes and recovery
archives, unsupported brushes, server-origin rules, draft isolation and corrupt
draft handling. The web bridge has Node tests and a simulated-native browser
scenario; backend tests cover retries, conflicts, deletion and permissions.

Before distributing a signed build, verify on a real iPad: Pencil strokes,
finger zoom/pan, zoomed stroke alignment, rotation/Split View, whole-stroke
erasing and undo, background/relaunch recovery, offline Save/retry, expired
sessions and conflicts from a second client. Simulator tests cannot measure
Pencil latency or validate physical Pencil input.
