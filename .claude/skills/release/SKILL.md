---
name: release
description: Release the desktop app and the browser extension from main by dispatching desktop.yml → GitHub Release v<next>, which carries the installers, the Gamma Connector zip and the AI plugins. A merge to main never does this on its own.
---

# Release from main

A merge to `main` only publishes the Docker image. Shipping a new desktop
app and extension version is this explicit step: dispatch the workflow on
`main` and it computes the next version from the tags, builds, and
publishes the GitHub Release. Nothing is bumped or tagged by hand
(`docs/dev/github_actions.md`; version rule: newest `v*` tag + patch, or
`desktop/package.json`'s version when that floor is higher — raise it for
a minor/major and merge first). The browser extension has no release of
its own: its zip rides on the same release with the same version.

**Argument**: none needed. Optional `--dry` builds without publishing
(`-f publish=false`), `--prerelease` marks the release as a pre-release.

## Steps

1. **Make sure main is what you want to ship**: `git fetch -q origin`,
   `git log --oneline origin/main -5`. Unmerged branch work → run `merge`
   first; this skill never commits, pushes or merges.

2. **Nothing already running**: `gh run list --workflow desktop.yml --limit 1`.
   An in-progress run queues the new one behind it
   (concurrency group); say so rather than double-dispatching.

3. **Dispatch**:
   ```bash
   gh workflow run desktop.yml --ref main                   # next v<version>
   gh workflow run desktop.yml --ref main -f publish=false  # --dry
   gh workflow run desktop.yml --ref main -f prerelease=true
   ```

4. **Report**: a few seconds later
   `gh run list --workflow desktop.yml --limit 1 --json status,url,databaseId`
   gives the run; the `meta` job's notice names the version it will
   publish
   (`gh run view <id> --json jobs --jq '.jobs[] | select(.name=="meta")'`).
   Report the link and version. Do not wait for the desktop build
   (~20 min) unless asked; `gh run watch <id> --exit-status` if so. The
   desktop publish job re-dispatches `docker.yml` with the version, so the
   image gets a matching `:<version>` tag on its own, and uploads the
   Gamma Connector zip and the Codex plugin zip, its setup scripts and
   checksums onto the same release. When the `CWS_*` repository variables
   exist it also dispatches `chrome-store.yml`, which submits the Connector
   zip to the Chrome Web Store (`gh run list --workflow chrome-store.yml
   --limit 1` once the release is out; a notice says when an earlier
   version is still in review and this one was skipped).
