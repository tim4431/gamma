---
name: release
description: Cut a Gamma release — run the `release` GitHub workflow (no tags to push) and watch it build the desktop installers + extension zip into one GitHub Release.
---

# Release (desktop app + browser extension)

One workflow, `.github/workflows/release.yml`, builds everything a user
installs and publishes it as ONE GitHub Release named `Gamma <version>`:
the Windows installer, the macOS dmg/zip, and the Gamma Connector zip. It
creates the `v<version>` tag itself from the ref it runs on — nobody pushes
tags by hand any more. The Docker image is a separate workflow
(`docker.yml`, every push to `main`); the release notes only link to it.
Pipeline details: [desktop/README.md](../../../desktop/README.md).

**Never commit or merge on your own.** This skill only dispatches the
workflow on work that is already committed and pushed; getting the work
there is the `push_merge` skill.

## Steps

1. **Pick the ref**: `git fetch origin`, default to `main`. Verify the
   release inputs exist at that ref:

   ```bash
   git ls-tree --name-only origin/main desktop extension .github/workflows/release.yml
   ```

   If they don't (work not merged yet, or still uncommitted in the tree),
   stop and tell the user to merge first — offer `push_merge`.

2. **Version**: read `desktop/package.json` at the ref
   (`git show origin/main:desktop/package.json`). The release is
   `v<version>`; if that tag already exists (`git ls-remote --tags origin`)
   the version was never bumped — ask the user to bump `"version"` in
   `desktop/package.json` (or do it for them if asked), get it merged, then
   re-run. Never delete or move a pushed tag. The extension zip is named
   after `extension/manifest.json`'s own version; bump that too when the
   extension changed.

3. **Dispatch**:

   ```bash
   gh workflow run release.yml --ref main
   # pre-release / one-off version override:
   gh workflow run release.yml --ref main -f prerelease=true -f version=1.2.0-rc1
   ```

4. **Watch the build** (~10–25 min; watch in the background):

   ```bash
   gh run list --workflow=release.yml --limit 1   # grab the run id
   gh run watch <run-id> --exit-status
   ```

   On failure: `gh run view <run-id> --log-failed`, report the cause, and
   fix it in the tree as normal work — the fix goes through commit/merge
   and a NEW version; don't reuse one that already has a tag.

5. **Report**: link the release (`gh release view v<version> --json url`)
   and remind the user of the end-user caveats: Windows SmartScreen
   "More info → Run anyway" (unsigned), macOS Gatekeeper "Open Anyway" in
   Privacy & Security (unsigned + un-notarized, Apple Silicon only), and
   that the Chrome Web Store upload of the extension zip is still manual
   ([extension/STORE.md](../../../extension/STORE.md)).
