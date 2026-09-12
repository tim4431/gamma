# GitHub Actions

Three workflows live in `.github/workflows/`. Each owns one deliverable,
runs on its own trigger, and never depends on the others.

| Workflow | File | Runs when | Produces |
|---|---|---|---|
| `docker` | `docker.yml` | every push to `main`, any `v*` tag pushed by hand, manual | the server image `ghcr.io/tim4431/gamma` (`latest` from main, semver tags from a `v*` tag), linux/amd64 + arm64 |
| `desktop` | `desktop.yml` | push to `main` touching `desktop/`, `backend/`, `frontend/` or the workflow itself; manual | Windows installer, macOS dmg + zip, Debian/Ubuntu deb, the update-feed files, the unsigned Store MSIX artifact; GitHub Release `v<version>` when the version is new |
| `extension` | `extension.yml` | push to `main` touching `extension/` or the workflow itself; manual | `gamma-connector-<version>.zip`; GitHub Release `extension-v<version>` when the version is new |

Merging to `main` therefore builds whatever the merge changed. Nothing is
published unless a version was bumped, and no tag is ever pushed by hand:
the workflows create their own tags through the release step.

## Versions, tags, and the "build only" rule

Both release workflows follow the same rule. A `meta` step reads the
version from the source of truth, computes the tag, and checks with
`git ls-remote` whether that tag already exists on the remote:

- **Tag missing** → the run builds, tests, and publishes a GitHub Release
  with that tag (created from the commit that ran).
- **Tag exists** → the run still builds and tests everything, prints a
  `::notice::` saying so, and leaves the outputs as workflow artifacts
  (14 days). This is the normal outcome of a merge that did not bump a
  version: a CI check that the frozen app or the zip still builds.
- Manual dispatch with `publish=false` forces the build-only path.

| Deliverable | Version source | Tag | Release name |
|---|---|---|---|
| Desktop app | `desktop/package.json` `"version"` (or the `version` dispatch input) | `v<version>` | `Gamma <version>` |
| Extension | `extension/manifest.json` `"version"` | `extension-v<version>` | `Gamma Connector <version>` |

Never delete or move a pushed tag; fix forward with a new version.

**The repository's "latest release" must stay the desktop release.**
Installed desktop apps update through electron-updater, which resolves
`https://github.com/tim4431/Gamma/releases/latest` and then reads the
`latest*.yml` assets of that release. The extension workflow therefore
publishes with `make_latest: false`, and the desktop workflow with
`make_latest: true` (except for pre-releases, which installed apps ignore
anyway). If you ever create a release by hand, keep that invariant, or
every installed app reports *Update check failed* until the next desktop
release. Details of the feed:
[desktop/docs/release.md](../../desktop/docs/release.md#auto-update-feed).

Each workflow has a `concurrency` group per ref with
`cancel-in-progress: false`, so two merges in quick succession queue rather
than race for the same tag.

## `desktop.yml`

Matrix over `windows-latest`, `macos-latest`, `ubuntu-latest`; every leg
does the same thing: build the frontend, freeze the backend with
PyInstaller, health-check the frozen server, run electron-builder, verify
the signature when signing secrets exist, and run the packaged app's
`--smoke` self-test (under Xvfb on Linux). Extras per platform: Windows also
builds the unsigned Microsoft Store MSIX (`store-windows` artifact, never a
release asset); Linux additionally `apt install`s the `.deb` on the runner
and runs the self-test from `/opt/Gamma/gamma` with the sandbox on. The
`publish` job runs only when `meta` said the tag is new; it merges the
three artifacts, writes the release notes (download table, signing state,
install hints, a pointer to the extension releases and the Docker image)
and creates the release. Signing is secret-gated and documented in the
workflow header and in
[desktop/docs/release.md](../../desktop/docs/release.md#code-signing-optional-secret-gated).

Path filter: the app bundles the backend and the frontend, so changes to
those directories rebuild it too. Narrow the `paths` list in the workflow if
that turns out to be too eager.

Dispatch inputs: `version` (override the package.json version for that run;
it is also pinned into the app), `prerelease`, `publish`.

## `extension.yml`

One Ubuntu job: read the manifest version, zip `extension/` (minus
`STORE.md`, `README.md`, `.DS_Store`), upload the zip as an artifact, and,
when the tag is new, publish the `extension-v<version>` release with
`make_latest: false`. The Chrome Web Store upload stays manual
([extension/STORE.md](../../extension/STORE.md)).

## `docker.yml`

Unchanged by the split: buildx for amd64 + arm64, pushes `latest` on every
push to `main`. Tags that the desktop workflow creates with `GITHUB_TOKEN`
never trigger other workflows (GitHub's recursion guard), so a
semver-tagged image only appears when a `v*` tag is pushed by hand. Setup
notes: [docs/dev/debugging.md](debugging.md) and the memory note on GHCR.

## Running and watching by hand

```bash
gh workflow run desktop.yml --ref main                       # release if the version is new
gh workflow run desktop.yml --ref main -f publish=false      # build check only
gh workflow run desktop.yml --ref main -f prerelease=true -f version=1.2.0-rc1
gh workflow run extension.yml --ref main

gh run list --workflow=desktop.yml --limit 3
gh run watch <run-id> --exit-status
gh run view <run-id> --log-failed
gh release view v<version> --json url,assets
```

A workflow can only be dispatched once its file exists on `main`
(`workflow_dispatch` is read from the default branch); pass `--ref dev` to
run the `dev` copy after that. The `release` skill (`.claude/skills/release`)
wraps the dispatch-and-watch loop for the desktop and extension workflows.

## Secrets

All optional; without them every build is unsigned and the workflows say
so with a `::warning::`.

| Secret | Used by |
|---|---|
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_SIGN_ENDPOINT`, `AZURE_SIGN_ACCOUNT`, `AZURE_SIGN_PROFILE`, optional `AZURE_SIGN_PUBLISHER` | `desktop.yml`, Windows leg: Azure Trusted Signing |
| `MAC_CERT_P12`, `MAC_CERT_PASSWORD`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID` | `desktop.yml`, macOS leg: Developer ID + notarization |
| `GITHUB_TOKEN` (automatic) | releases and tags in `desktop.yml` / `extension.yml`, GHCR push in `docker.yml` |

`gh secret list` shows which exist. The Linux `.deb` is never signed.

## Adding or changing a workflow

- Keep one deliverable per workflow and a path filter that matches its
  inputs; a workflow that publishes must use the tag-exists check above so
  a merge without a bump stays a build.
- The Linux Electron steps need `xvfb-run`; the unpacked `linux-unpacked`
  dir needs `--no-sandbox` (the `.deb` postinst fixes that for installs).
- Pin every action to a major that runs on the runner's current Node
  (Node 24 as of 2026-09: `actions/checkout@v7`, `setup-node@v7`,
  `setup-python@v7`, `upload-artifact@v7`, `download-artifact@v8`,
  `softprops/action-gh-release@v3`, `docker/*` v4/v6/v7). A run annotated
  "Node.js 20 is deprecated … forced to run on Node.js 24" means a pin fell
  behind; check the action's `action.yml` `runs.using` at its latest tag.
  The "UNSIGNED build" warnings are expected until the signing secrets
  exist.
- Validate YAML locally with the desktop tree's `js-yaml`
  (`node -e "require('js-yaml').load(require('fs').readFileSync(f,'utf8'))"`
  from `desktop/`), and keep this document, the `release` skill, and
  [desktop/docs/release.md](../../desktop/docs/release.md) in sync.
