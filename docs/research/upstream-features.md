# Upstream feature study

Study date: 2026-09-18, a source and history audit of the upstream fork, not runtime validation or an approved plan. Findings only; the iPad decision it led to is in [ipad.md](ipad.md).

## Provenance and scope

- This repository (`tim4431/Gamma`) is forked from [`amogadget/Gamma`](https://github.com/amogadget/Gamma). GitHub identifies that repository as both parent and original source: [repository metadata](https://api.github.com/repos/tim4431/Gamma).
- Compared local `dev` at `b21a0d1` (September 18) with upstream `main` at `2dbde2df8fe49a980c3fab29914ad552b897b934` (September 11). `git ls-remote upstream` confirmed that the local upstream snapshot matched the live branch when checked.
- Reviewed older history, implementation files, tests, release notes, and this repository's existing research. Upstream's `client-app`, `desktop-more-platforms`, and `ipad-app` branch tips are all ancestors of its `main`.
- The merge base is `445e63343b5628f8bcb34a56fc379ad20a5dfccb` (August 2). There are 218 local-only and 335 upstream-only commits, but exchanged/backported/rewritten changes make these counts unsuitable as feature-gap counts.
- The ranking below considers research-workflow value, breadth of use, and existing alternatives. Several entries are related parts of the same iPad system, not independent ready-to-cherry-pick changes.

## Ranked feature gaps

### 1. Native iPad app with Apple Pencil support

**Priority:** High. **Implemented upstream:** September 10. Adds a dedicated tablet experience; handwriting itself already exists in this fork.

**Done here (2026-09-18), differently:** the iPad app is the web app added to the home screen — manifest, full-bleed icons from the brand mark, theme-colored status bar, standalone-mode CSS — because the browser ink layer already covers Pencil input and a PencilKit client would reintroduce the opaque format the handwriting research rejected. Design: [ipad.md](ipad.md); implementation: [dev/ipad.md](../dev/ipad.md).

A SwiftUI app combines PDFKit reading/text selection with PencilKit drawing, keeping existing Gamma document and block IDs. Each explicitly created ink group becomes one `pdf_ink` block with editable PencilKit source, annotation text, and child notes.

**Where:** [PDFInkView.swift](https://github.com/amogadget/Gamma/blob/2dbde2d/ipad/GammaIPad/Reader/PDFInkView.swift), [InkPageOverlay.swift](https://github.com/amogadget/Gamma/blob/2dbde2d/ipad/GammaIPad/Reader/InkPageOverlay.swift), [setup guide](https://github.com/amogadget/Gamma/blob/2dbde2d/ipad/README.md).

### 2. Intentional offline library downloads and offline startup

**Priority:** High. **Implemented upstream:** September 11. Enables disconnected reading and travel workflows.

An account/server-scoped manifest tracks a persistent download queue and separate readiness for PDFs, notes, handwriting, and recordings, retaining completed components for retries. The app can reopen a saved local account without a network session, with download selection, cancellation, storage-size reporting, conservative local removal, and same-account reconnection.

**Where:** [GammaWorkspaceOffline.swift](https://github.com/amogadget/Gamma/blob/2dbde2d/ipad/GammaIPad/App/GammaWorkspaceOffline.swift), [GammaOfflineCache.swift](https://github.com/amogadget/Gamma/blob/2dbde2d/ipad/GammaIPad/Storage/GammaOfflineCache.swift), [offline behavior and limits](https://github.com/amogadget/Gamma/blob/2dbde2d/ipad/OFFLINE.md).

### 3. Audio synchronized with handwriting: Note Replay

**Priority:** High. **Implemented upstream:** September 10. A distinctive lecture/meeting-review capability.

Recording captures stroke and page events against the audio clock, letting native and browser players seek across pages, progressively reveal ink, and jump from a stroke to its audio time. Browser playback uses source-hash-validated per-stroke PNGs and numeric paths exported from PencilKit, with audio driving the reveal masks.

**Where:** [NoteReplayPlayer.jsx](https://github.com/amogadget/Gamma/blob/2dbde2d/frontend/src/NoteReplayPlayer.jsx), [ReplayInkLayer.jsx](https://github.com/amogadget/Gamma/blob/2dbde2d/frontend/src/ReplayInkLayer.jsx), [GammaReplay.swift](https://github.com/amogadget/Gamma/blob/2dbde2d/ipad/GammaIPad/Storage/GammaReplay.swift), [browser replay design](https://github.com/amogadget/Gamma/blob/2dbde2d/docs/dev/note-replay.md).

### 4. Persistent audio-recording blocks with recovery

**Priority:** High–medium. **Implemented upstream:** September 10. Useful independently of replay; distinct from existing chat voice dictation.

Native `AVAudioRecorder` writes AAC segments, finalizing on pause and rolling over at roughly five minutes while persisting recovery metadata before capture. Finalized segments upload as private assets referenced by one revision-checked `audio` block, with sequential playback, retry, and interrupted-file recovery.

**Where:** [GammaRecordingController.swift](https://github.com/amogadget/Gamma/blob/2dbde2d/ipad/GammaIPad/App/GammaRecordingController.swift), [routers/ink.py](https://github.com/amogadget/Gamma/blob/2dbde2d/backend/gamma/routers/ink.py) (`save_audio`), [recording guide](https://github.com/amogadget/Gamma/blob/2dbde2d/ipad/RECORDING.md).

### 5. Durable offline edits and explicit sync-conflict resolution

**Priority:** High–medium. **Implemented upstream:** September 10–11. Persistence across process restarts is the gap, not live collaboration generally.

Atomic page snapshots persist editable data together with an outbox before networking, and retries reuse stable IDs to avoid duplicate blocks. Revision-checked native updates stop on conflicts and expose local/remote choices, while pending work survives relaunch and sign-out.

**Where:** [GammaCache.swift](https://github.com/amogadget/Gamma/blob/2dbde2d/ipad/GammaIPad/Storage/GammaCache.swift), [GammaWorkspace.swift](https://github.com/amogadget/Gamma/blob/2dbde2d/ipad/GammaIPad/App/GammaWorkspace.swift), [routers/ink.py](https://github.com/amogadget/Gamma/blob/2dbde2d/backend/gamma/routers/ink.py).

### 6. Automatic AI model-catalog refresh

**Priority:** Medium; best small addition. **Implemented upstream:** August 6. Basic model discovery already exists here.

A background watcher checks provider entries every ten minutes and refreshes catalogs older than a configurable TTL, defaulting to 24 hours, while retaining the previous catalog on failure. A manual `/api/ai/models/refresh` endpoint forces discovery and returns refreshed choices for the chat UI.

**Where:** [routers/ai.py](https://github.com/amogadget/Gamma/blob/2dbde2d/backend/gamma/routers/ai.py) (`refresh_entry_catalog`, `_catalog_watcher_loop`, `ai_models_refresh`), [ai_settings.py](https://github.com/amogadget/Gamma/blob/2dbde2d/backend/gamma/ai_settings.py), [original commit df2d6a0](https://github.com/amogadget/Gamma/commit/df2d6a0).

### 7. Hybrid iPad workspace: full web app ↔ native reader

**Priority:** Medium. **Implemented upstream:** September 10. Preserves feature coverage without rebuilding every screen natively.

A `WKWebView` hosts the existing Gamma web interface and passes the selected document to the native reader through a bridge validated against the main-frame origin, session, and document identity. Returning to the web editor waits for pending native changes to sync and reloads the tree to avoid stale edits overwriting them.

**Where:** [GammaWebWorkspace.swift](https://github.com/amogadget/Gamma/blob/2dbde2d/ipad/GammaIPad/Web/GammaWebWorkspace.swift), [nativeBridge.js](https://github.com/amogadget/Gamma/blob/2dbde2d/frontend/src/nativeBridge.js), [parity matrix](https://github.com/amogadget/Gamma/blob/2dbde2d/ipad/WEB_PARITY.md).

### 8. Server-rendered PDF previews while pdf.js loads

**Priority:** Medium–low; previously deferred here. **Implemented upstream:** August 3. Potentially useful for image-heavy scans.

PDFium renders cached JPEG page previews that appear while pdf.js prepares the interactive canvas, then disappear when its real render finishes. The server restricts preview widths and maintains a roughly 512 MiB per-user disk cache, while selection and highlight geometry remain controlled by pdf.js.

**Where:** [pageimage.py](https://github.com/amogadget/Gamma/blob/2dbde2d/backend/gamma/routers/pageimage.py), [pdfViewer.jsx](https://github.com/amogadget/Gamma/blob/2dbde2d/frontend/src/pdfViewer.jsx), [original commit ff9085b](https://github.com/amogadget/Gamma/commit/ff9085b).

### 9. Automatic optimization of slow scanned PDFs

**Priority:** Low / specialized; previously rejected here. **Implemented upstream:** August 2. Has quality and storage tradeoffs.

A background worker detects large masks in mixed-raster-content scans and replaces supported image-bearing forms with flattened JPEGs while preserving the page's OCR text stream. It writes a separate `-flat.pdf` and switches `source_url` only after completion, retaining the document ID used by annotations and search.

**Where:** [flatten_mrc.py](https://github.com/amogadget/Gamma/blob/2dbde2d/backend/gamma/flatten_mrc.py), [flatten_queue.py](https://github.com/amogadget/Gamma/blob/2dbde2d/backend/gamma/flatten_queue.py), [original commit 6ba39ae](https://github.com/amogadget/Gamma/commit/6ba39ae).

## Features already present here or deliberately implemented differently

- **Handwriting:** browser drawing, lasso operations, erasing, and undo already exist. [Our handwriting research](handwriting.md) evaluated upstream and chose an open vector format rather than PencilKit source plus raster derivatives.
- **AI model discovery:** already present; unattended refresh and persisted catalogs are the additional upstream capability.
- **Live collaboration:** already present. [Our collaboration documentation](../dev/collab.md) states that queued edits live in memory rather than durable offline storage.
- **PDF loading:** range requests, server-provided page geometry, and parsed-document caching already exist. [Our loading study](pdf_loading.md) and [implementation notes](../dev/pdf_loading.md) document adopting those ideas, deferring JPEG previews, and rejecting MRC flattening.
- **Other existing counterparts:** desktop packaging, browser capture, translation, Zotero import/export, note-PDF export, themes, and editable embeds. These should not be counted as missing features merely because upstream has different commit IDs.

## Maturity and integration caveats

- Replay reconstructs **final surviving strokes**, not historical erasures/undo states. Its in-progress raster reveal is approximate, and old recordings without timing events cannot gain synchronization retroactively.
- Recording pauses on backgrounding, leaving the document, or interruptions; continuous background capture and gapless segment rollover are not promised.
- The iPad application requires building/signing from source; source availability is not an App Store release.
- Upstream documents simulator and integration coverage, including 110 passed / 5 skipped in its final offline simulator regression. Physical-device flight-mode relaunch, long Pencil/microphone sessions, storage exhaustion, and large-library performance remain acceptance boundaries; this audit did not rerun those tests.
- Upstream ink/audio assets require the owning account; public share links do not grant access. Its per-user storage and native raw-SQL/revision endpoints differ from this fork's workspace permissions, operation log, and live fan-out.
- Our handwriting research explicitly rejects copying that separate native endpoint family and opaque drawing/preview format directly. Reuse behavioral design while adapting persistence and access control to this fork.

**Evidence:** [v0.3.0 release notes](https://github.com/amogadget/Gamma/blob/2dbde2d/docs/releases/v0.3.0.md), [v0.4.0 release notes](https://github.com/amogadget/Gamma/blob/2dbde2d/docs/releases/v0.4.0.md), [offline validation boundaries](https://github.com/amogadget/Gamma/blob/2dbde2d/ipad/OFFLINE.md), [replay design](https://github.com/amogadget/Gamma/blob/2dbde2d/docs/dev/note-replay.md).

## Suggested follow-up candidates

These are recommendations for future prioritization, not approved implementation tasks.

- [ ] Evaluate automatic model-catalog refresh as a small addition, preserving explicit user model choices and current provider behavior.
- [ ] Design audio blocks and synchronized replay around this fork's existing vector ink and block operations.
- [ ] Scope durable offline storage and conflict UX separately from native iPad UI work.
- [ ] Evaluate a native iPad client using current workspace identities, permissions, and synchronization APIs.
- [ ] Revisit server previews only with a representative scan demonstrating a remaining first-paint bottleneck.
- [ ] Keep MRC flattening deferred unless requirements justify revisiting the documented quality/storage tradeoff.
