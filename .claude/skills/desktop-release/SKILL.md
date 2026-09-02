---
name: desktop-release
description: Cut a desktop app release — push a desktop-v* tag matching desktop/package.json and watch the GitHub build produce the Windows/macOS installers.
---

# Desktop release

Tag `desktop-v<version>` and push it; that alone triggers
`.github/workflows/desktop-release.yml`, which builds the Windows installer
and macOS dmg and attaches them to a GitHub Release. Details of the pipeline:
[docs/dev/desktop.md](../../../docs/dev/desktop.md).

**Never commit or merge on your own.** This skill only tags and pushes a tag
on work that is already committed and pushed; getting the work there is the
`push_merge` skill.

## Steps

1. **Pick the ref**: `git fetch origin`, default to `origin/main` HEAD.
   Verify the release inputs exist at that ref:

   ```bash
   git ls-tree --name-only origin/main desktop .github/workflows/desktop-release.yml
   ```

   If they don't (desktop work not merged yet, or still uncommitted in the
   tree), stop and tell the user to merge first — offer `push_merge`.

2. **Version = tag**: read `desktop/package.json` at the ref
   (`git show origin/main:desktop/package.json`); the tag is
   `desktop-v<version>`. If that tag already exists (`git tag -l`,
   `git ls-remote --tags origin`), the version was never bumped: ask the
   user to bump `"version"` in `desktop/package.json` (or do it for them if
   asked), get it merged, then re-run. Never delete or move a pushed tag on
   your own.

3. **Tag & push**:

   ```bash
   git tag desktop-v<version> origin/main
   git push origin desktop-v<version>
   ```

4. **Watch the build** (runs ~10–25 min; watch in the background):

   ```bash
   gh run list --workflow=desktop-release.yml --limit 1   # grab the run id
   gh run watch <run-id> --exit-status
   ```

   On failure: `gh run view <run-id> --log-failed`, report the cause, and
   fix it in the tree as normal work — the fix goes through commit/merge and
   a NEW version + tag; don't re-tag the same version.

5. **Report**: link the release (`gh release view desktop-v<version> --json url`)
   and remind the user of the end-user caveats: Windows SmartScreen
   "More info → Run anyway" (unsigned), macOS Gatekeeper "Open Anyway" in
   Privacy & Security (unsigned + un-notarized, Apple Silicon only).
