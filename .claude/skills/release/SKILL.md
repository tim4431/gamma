---
name: release
description: Cut a Gamma release — dispatch the `desktop` (installers) or `extension` (Connector zip) GitHub workflow on main, or just watch the run a merge already started, and report the outcome.
---

# Release (desktop app / browser extension)

Two workflows, one deliverable each, both in `.github/workflows/` and
described side by side in [docs/dev/github_actions.md](../../../docs/dev/github_actions.md):

- `desktop.yml` — Windows installer, macOS dmg/zip, Debian/Ubuntu deb →
  GitHub Release `v<version>`, version = `desktop/package.json`.
- `extension.yml` — `gamma-connector-<version>.zip` → GitHub Release
  `extension-v<version>`, version = `extension/manifest.json`.

Both run BY THEMSELVES on every push to `main` that touches their inputs
(desktop: `desktop/`, `backend/`, `frontend/`; extension: `extension/`),
and both publish only when their tag does not exist yet — a merge without
a version bump is a build check whose outputs stay workflow artifacts. The
workflows create the tags; nobody pushes tags by hand. The Docker image is
`docker.yml` (every push to main); the desktop release notes link to it.

So "cut a release" usually means: bump the version, merge, watch the run
the merge started. Manual dispatch is for re-running or for a build-only
check (`-f publish=false`).

**Never commit or merge on your own.** This skill only dispatches or
watches workflows on work that is already committed and pushed; getting
the work there is the `push_merge` skill.

## Steps

1. **Pick the ref**: `git fetch origin`, default to `main`. Verify the
   inputs and the workflow exist at that ref:

   ```bash
   git ls-tree --name-only origin/main desktop extension .github/workflows/desktop.yml .github/workflows/extension.yml
   ```

   If they don't (work not merged yet, or still uncommitted in the tree),
   stop and tell the user to merge first — offer `push_merge`.

2. **Version**: read the source of truth at the ref
   (`git show origin/main:desktop/package.json` /
   `git show origin/main:extension/manifest.json`) and check the tag
   (`git ls-remote --tags origin`). If `v<version>` / `extension-v<version>`
   already exists, a run will build but NOT publish — ask the user to bump
   the version (or do it if asked), get it merged, and the merge itself
   releases. Never delete or move a pushed tag.

3. **Find or start the run.** A merge that touched the inputs already
   started one:

   ```bash
   gh run list --workflow=desktop.yml --limit 3
   gh run list --workflow=extension.yml --limit 3
   ```

   Dispatch by hand only when nothing is running for the commit, or for a
   build-only check:

   ```bash
   gh workflow run desktop.yml --ref main                     # releases if the tag is new
   gh workflow run desktop.yml --ref main -f publish=false    # artifacts only
   gh workflow run desktop.yml --ref main -f prerelease=true -f version=1.2.0-rc1
   gh workflow run extension.yml --ref main
   ```

4. **Watch** (desktop ~15–25 min, extension ~1 min; watch in the
   background):

   ```bash
   gh run watch <run-id> --exit-status
   ```

   On failure: `gh run view <run-id> --log-failed`, report the cause, and
   fix it in the tree as normal work — the fix goes through commit/merge
   and a NEW version; don't reuse one that already has a tag.

5. **Report**: link the release (`gh release view v<version> --json url`
   or `extension-v<version>`) and, for the desktop, whether the builds
   were signed: the desktop job logs a `::warning::` per platform when the
   signing secrets are missing (secret names in the workflow header and
   [desktop/docs/release.md](../../../desktop/docs/release.md)) and the
   release notes say so. Installed apps pick the release up by themselves
   (electron-updater resolves the repo's *latest* release and reads its
   `latest*.yml`), so a normal desktop release must be marked *Latest*,
   never pre-release or draft — and an extension release must never be
   *Latest* (the workflow publishes it with `make_latest: false`).
   For UNSIGNED builds remind the user of the end-user caveats: Windows
   SmartScreen "More info → Run anyway" (Edge download shelf: … → Keep →
   Keep anyway), macOS "damaged" until `xattr -dr com.apple.quarantine`
   or "Open Anyway" in Privacy & Security; Linux installs with
   `sudo apt install ./Gamma-<v>-linux-amd64.deb`. For the extension remind
   them that the Chrome Web Store upload of the zip is still manual
   ([extension/STORE.md](../../../extension/STORE.md)).
