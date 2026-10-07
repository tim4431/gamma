# GitHub Actions

Nine workflows live in `.github/workflows/`. A merge to `main` publishes
only the Docker image and, when the site or its inputs changed, the website. The desktop app, with the browser extension on the same
release, is released by dispatching `desktop.yml` — the `release` skill
does that — and nothing is bumped or tagged by hand: versions are computed
from the tags. The
account server (`cloud/`) and the website (`sites/`) are separate from all
of that: each has its own check and its own publish (`cloud.yml`,
`site.yml`), dispatched from any branch (the `build-cloud` / `build-site`
skills), and a PR that touches only one of them skips `check`. The public
demo (demo.gammapdf.com) runs `main`'s server image like the NAS, pinned by
the `:sha-<short>` tag a merge's `docker.yml` run pushes (the
`update-demo-server` skill).

| Workflow | File | Runs when | Produces |
|---|---|---|---|
| `check` | `check.yml` | every pull request to `main`, except one that only touches the account server or the website | pass/fail: brand asset consistency, backend pytest, frontend unit tests + build, the browser suite (3 parallel workers), extension unit tests + zip (~5 min) |
| `desktop` | `desktop.yml` | manual dispatch only (`release` skill) | Windows installer, macOS dmg + zip, Debian/Ubuntu deb, the update-feed files, the browser extension's `gamma-connector-<version>.zip` → GitHub Release `v<version>`; the MSIX artifact + a Microsoft Store submission when the secrets exist; a Docker tag `<version>` |
| `chrome-store` | `chrome-store.yml` | dispatched by the desktop release (not a pre-release) when the `CWS_*` variables exist; manual dispatch with a tag | the release's Connector zip uploaded to the Chrome Web Store and submitted for review (API V2) |
| `docker` | `docker.yml` | every push to `main` except one that only touches the account server or the website; dispatched by the desktop release with a version; manual dispatch from any branch (a `sha-<short>` image only) | `ghcr.io/tim4431/gamma:sha-<short>` on every run; `:latest` only from `main`; `:<version>` and `:<major.minor>` when dispatched with a version; linux/amd64 + arm64 |
| `cloud` | `cloud.yml` | a pull request touching `cloud/`; manual dispatch from any branch (`update-account-server` skill) | pass/fail: the account server's pytest; when dispatched and green, `ghcr.io/tim4431/gamma-cloud:latest` + `:sha-<short>` (`cloud/Dockerfile`, amd64) |
| `fleet` | `fleet.yml` | a pull request or a push to `main` touching `cloud/fleet/`; manual dispatch from any branch | pass/fail: the fleet agent's pytest; on a push to main or a dispatch, when green, `ghcr.io/tim4431/gamma-fleet:latest` + `:sha-<short>` (`cloud/fleet/Dockerfile`, amd64; [hosted.md](hosted.md)) |
| `site` | `site.yml` | a PR or a push to `main` touching `sites/`, `docs/` (the artwork it copies and the documents it renders), `PRIVACY.md` or `TERMS.md`; manual dispatch from any branch (`build-site` skill) | pass/fail: the site builds with every internal link resolving and its Worker passes a dry run; on a push or dispatch, gammapdf.com: `sites/dist` deployed as a Cloudflare Worker (static assets only; needs `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID`; [sites/README.md](../../sites/README.md)) |
| `ipad` | `ipad.yml` | a pull request touching `ipad/`, the ink, notebook or replica modules, or the frontend's dependencies; manual dispatch | pass/fail on macOS: the shared JavaScript bundled and run bare, the Xcode project generated (XcodeGen), the XCTest suite on an iPad simulator, an unsigned device build ([ipad/README.md](../../ipad/README.md)) |
| `Assistant plugin package` | `codex-plugin.yml` | PRs touching the plugin or its tooling, or manual dispatch | plugin packaging and installer tests on Windows/macOS/Linux and preview plugin release assets (pins `checkout@v4`/`setup-python@v5`/`upload-artifact@v4`, older than the rule below) |

The `desktop` workflow also builds the versioned assistant plugin assets —
the Codex and Claude Code plugin zips, the DeepSeek Harness tarball, the
Codex setup scripts for Windows and macOS/Linux, and checksums
(`tools/release_plugins.py`) — and the browser extension zip, and uploads
them onto the same release.

```
PR → main ──▶ check (pytest, npm test + build, e2e, extension zip)   ← merge skill waits for this
merge ───────▶ docker.yml  ghcr :latest                              ← every merge (not cloud/- or sites/-only ones)
         └──▶ site.yml    check → gammapdf.com                      ← only when sites/, docs/, PRIVACY.md or TERMS.md changed
PR touching cloud/ ──▶ cloud.yml: test                            ← instead of check, for cloud/-only PRs
PR touching sites/ ──▶ site.yml: check                            ← instead of check, for sites/-only PRs
build-site ──▶ site.yml --ref <branch>: check → gammapdf.com        ← no merge needed
update-account-server ──▶ cloud.yml --ref <branch>: test → ghcr gamma-cloud :latest :sha-<short>
                          then pull + restart on the VPS          ← no merge needed
update-demo-server ──▶ main's newest docker.yml run: pin its :sha-<short>
                       in the demo's project                      ← after a merge, like update-server
update-needed ──▶ compares what each deployment runs with the code, then runs
                  update-account-server → update-demo-server → update-server for the ones behind
release skill ───▶ desktop.yml  meta: version = max(package.json, newest v* tag + patch)
 (gh workflow           build Win/mac/Linux with that version pinned, smoke on all three
  run)                  zip the extension, the same version pinned into its manifest
                        publish: Release v<version> (notes = commits since previous tag)
                        └─▶ dispatch docker.yml -f version   ─▶ ghcr :<version> :<major.minor>
                        └─▶ dispatch chrome-store.yml -f tag ─▶ Chrome Web Store review (if CWS_* variables)
                        Windows leg: MSIX → msstore publish (if PARTNER_CENTER_* secrets)
```

## Versions and tags

The **newest tag is the source of truth**; the files hold a floor.

| Deliverable | Floor | Tag series | Release name |
|---|---|---|---|
| Desktop app | `desktop/package.json` `"version"` | `v<version>` | `Gamma <version>` |

The browser extension has no series of its own: its zip carries the
release's version. Up to `extension-v0.1.1` it was released separately as
`extension-v<version>`; those tags and releases stay, and the `v*` rule
ignores them.

On every run the `meta` step lists the tags of its series on the remote and
takes the newest. If the floor is higher than that, the floor is the
version — **that is how you make a minor or major release: raise the
floor and merge**. Otherwise the version is the newest tag with the patch
number plus one. The result is pinned into the build (`npm version` in the
desktop checkout; `jq` into the manifest inside the extension zip), so the
app, `latest*.yml`, the MSIX manifest and the zip all carry it, while the
repository files stay untouched: no bot commits on `main`, nothing for `dev`
to conflict with. The release step creates the tag from the merged commit.

Consequences:

- A version is never reused. Deleting or moving a tag is never the fix; the
  next merge is.
- A merge releases nothing but the Docker `latest` image; a dispatched run
  whose build fails on any platform releases nothing (the publish job needs
  all three).
- A manual run with `publish=false` builds and keeps the outputs as
  artifacts (14 days). A manual `version` input overrides the computation;
  it fails early if that tag already exists.
- `prerelease=true` on a manual run publishes without marking the release
  *Latest*; installed apps ignore pre-releases.

**The repository's "latest release" must be a desktop release.** Installed
apps update through electron-updater, which resolves
`https://github.com/tim4431/Gamma/releases/latest` and reads that release's
`latest*.yml`. The old `extension-v*` releases were therefore published
with `make_latest: false`. If you ever create a release by hand, keep that
invariant or every installed app reports *Update check failed* until the
next desktop release. Emergency lever for a bad desktop release: edit it
and tick *pre-release* — the previous release becomes *Latest* again and
clients stop seeing the bad one. Feed details:
[desktop/docs/release.md](../../desktop/docs/release.md#auto-update-feed).

The release workflow has a `concurrency` group per ref with
`cancel-in-progress: false`, so two merges in quick succession queue and get
consecutive numbers instead of racing.

## `desktop.yml`

Matrix over `windows-latest`, `macos-latest`, `ubuntu-latest`; every leg:
pin the version, build the frontend, freeze the backend with PyInstaller,
health-check the frozen server, electron-builder, verify the signature, run
the packaged app's `--smoke` self-test (under Xvfb on Linux). Per platform:
Windows also builds the unsigned Microsoft Store MSIX (`store-windows`
artifact) and, when the `PARTNER_CENTER_*` secrets exist and the run
publishes, submits it with the `msstore` CLI (`continue-on-error`: the Store
allows one submission in certification at a time, so a second release the
same day is refused and simply waits for the next). The step's verdict is
`msstore submission status` after the publish, not the CLI's exit code —
the first run exited 1 on a transient re-read after the package was
already in certification. macOS is ad-hoc signed
by `desktop/scripts/adhoc-sign.cjs` when no Developer ID is available (see
[release.md](../../desktop/docs/release.md#code-signing-optional-secret-gated)).
Linux additionally `apt install`s the `.deb` on the runner and runs the
self-test from `/opt/Gamma/gamma` with the sandbox on.

The macOS signature diagnostics must consume all `codesign` output: an early
exit from `grep -q` or `head` can break the pipe and fail the release under
`pipefail` even when the signature is valid. Signature verification errors
must still fail the job.

The `extension` job copies `extension/` with the version written into
`manifest.json` (a pre-release suffix such as `-rc1` goes to `version_name`,
since Chrome's `version` takes only numbers), leaves out `STORE.md`,
`README.md` and `.DS_Store`, and zips it as `gamma-connector-<version>.zip`.
After the release, the publish job dispatches `chrome-store.yml` with the
tag, which submits that zip to the Chrome Web Store (below).

The `publish` job merges the platform, plugin and extension artifacts,
writes the notes — download table, per-platform install hints, **Changes:
the commit subjects since the previous tag, restricted to
`desktop/ backend/ frontend/ extension/`** (this repo's PR
titles are all "Merge pull request #N from dev", so GitHub's generator
would say nothing) — creates the release + tag with `make_latest: true`,
then runs `gh workflow run docker.yml --ref v<version> -f version=…` so the
server image gets the same version tag (a tag made with `GITHUB_TOKEN`
would not trigger `docker.yml` by itself). `docker.yml` also bakes the
build stamp into the image as build args (`GAMMA_VERSION` = the dispatched
version, or `<newest v* tag>-dev.<commits since it>` for any other build,
with `GAMMA_BRANCH` = the branch it was built from; `GAMMA_COMMIT` = the
sha), which the admin dashboard shows and compares against the latest
release and, for a `-dev` build, its branch's head (`gamma/version.py`,
[user_db.md](user_db.md)). The checkout is full-depth for the tags.

No push trigger: the app bundles the backend and the frontend, so a path
filter would release it on nearly every merge. It runs only when dispatched
(`release` skill).

## `chrome-store.yml`

One Ubuntu job, dispatched by the desktop publish job with the new tag (a
release made with `GITHUB_TOKEN` fires no `release` event) or by hand with
any tag, blank meaning the latest release. It refuses a pre-release, since
that zip carries the final version number and the store accepts each
version once. It downloads `gamma-connector-*.zip` from the release, signs
in with Workload Identity Federation (`google-github-actions/auth`, no
stored key) as the service account linked to the publisher, and calls the
Chrome Web Store API V2: `fetchStatus`, `upload`, polling until the upload
is processed, then `publish`. It skips with a notice when an earlier
version is still in review (it never cancels a review) or when the store
already has that version. A review shows in `fetchStatus` as
`PENDING_REVIEW`, except during an item's first one, when the status
carries no revision at all; the upload is then refused with
`NOT_UPDATEABLE`, which counts as the same skip. `publish=false` leaves the upload as a
draft. Setup, including the store item that only the dashboard can create:
[extension/STORE.md](../../extension/STORE.md).

## `check.yml`

Five parallel Ubuntu jobs on every PR to `main`: brand asset consistency,
backend pytest (Python 3.12, `requirements.txt` + `requirements-dev.txt`,
`-n auto` over pytest-xdist; the job is capped at 20 minutes and a single
test at 300 s, after which it fails with every thread's stack instead of
holding the job), the frontend unit tests + build (Node 22,
`npm test` then `npm run build`, then the iPad app's JavaScript bundled and
run in a bare context, `ipad/scripts/core.test.mjs`), the browser suite
(`npm run e2e -- --continue --jobs 3` against a backend started from the checkout with
`GAMMA_E2E_PYTHON=python` — both requirements files, since a scenario
builds its Zotero fixture from a backend test module — Playwright's
Chromium installed with its system deps; on a failure the harness's
`failures/` folders — screenshots, page problems, the error, the server
log tail — are uploaded as the `e2e-failures` artifact), and the extension's unit
tests (Node 22, `node --test extension/tests/*.test.mjs` — the pure
modules; its `.e2e.mjs` files need Playwright's full Chromium and are run
by hand) followed by a manifest parse + zip of the extension. No installers. A PR that changes only
`cloud/`, `cloud.yml`, the `update-account-server` or `update-demo-server`
skill or [cloud_accounts.md](cloud_accounts.md) — or only `sites/`, `site.yml`, the
`build-site` skill, `PRIVACY.md` or `TERMS.md` — skips it (`paths-ignore`); `cloud.yml`
and `site.yml` check those. (A PR touching the brand artwork the site
copies still runs `check`: its `branding` job owns those files.) The `merge` skill waits for it before merging; a
red check is fixed on the branch as normal work.

## `ipad.yml`

One macOS job (`macos-15`) for the iPad app. It runs `npm ci` in
`frontend/`, then `ipad/scripts/build-core.mjs` and its bare-context test.
`xcodegen generate` makes the project from `ipad/project.yml`.
`xcodebuild test` runs on the newest iPad simulator the image has.
`xcodebuild build -sdk iphoneos` makes an unsigned device build, with
`CODE_SIGNING_ALLOWED=NO`. Nothing is signed or uploaded: running on an
iPad takes a developer's own team, set in Xcode.

## `docker.yml`

buildx for amd64 + arm64, `latest` on every push to `main` (the same
`paths-ignore` as `check.yml`: an account-server- or website-only merge
rebuilds nothing — neither is in the image). When dispatched
with a `version` input (the desktop publish job does this on the release
tag) it also pushes `<version>` and `<major.minor>`. Every run pushes
`sha-<short>` (the metadata action's `type=sha`: the commit's first 7
characters) and labels the image `org.opencontainers.image.revision` with
the full commit.

Dispatched on a branch without a version
(`gh workflow run docker.yml --ref <branch>`) it pushes only
`sha-<short>`: `latest` is enabled on the default branch alone
(`{{is_default_branch}}`) and the semver tags only with a version: a way to
try a branch's image without moving what the NAS pulls. Never pass
`-f version` for such a build: that adds the release tags, and the semver
rule adds `latest` with them.

The public demo runs `main`: the `update-demo-server` skill takes the
`sha-<short>` tag of `main`'s newest green run and pins it in the demo's own
compose project on the VPS, as `GAMMA_TAG` in its `.env`
([cloud/deploy/demo/README.md](../../cloud/deploy/demo/README.md),
[guests.md](guests.md)). Pinned rather than `:latest`, it changes only when
deployed, and a rollback is one line. Setup notes: [docs/dev/debugging.md](debugging.md) and the memory
note on GHCR.

## `cloud.yml`

The account server ([cloud_accounts.md](cloud_accounts.md)) outside the
release flow. `test`: its pytest with `cloud/requirements*.txt` — on a PR
that touches `cloud/`, and first on every dispatch. `publish` (dispatch
only, after a green `test`): `cloud/Dockerfile` for amd64, tagged `latest`
and `sha-<short>`, pushed to `ghcr.io/tim4431/gamma-cloud`. Dispatch it
from the branch that holds the work
(`gh workflow run cloud.yml --ref dev`) — nothing needs to reach `main`.
So `:latest` is whatever was
published last, from whichever branch; the `sha-` tag and the image's
revision label say which commit. The `build-cloud` skill dispatches it;
`update-account-server` builds through it, then deploys. Like every
dispatch, it needs the file on `main` once before the first run.

## `site.yml`

gammapdf.com ([sites/README.md](../../sites/README.md)); it depends on no
app code, only on `sites/`, `PRIVACY.md`, `TERMS.md` and `docs/`: the artwork
the build copies and the user guide it renders as a page, so a change under
`docs/` on `main` deploys the site.
`check`: `npm ci`, `node build.mjs --strict` (fails on an internal link or
anchor that resolves to nothing), the key pages present in `dist/` (index,
404, pricing, privacy, terms, the user guide, the sitemap, `_redirects`,
`_headers`, the favicon), no `<!--#include` left unexpanded, and `wrangler
deploy --dry-run` (bundles the Worker and reads `wrangler.jsonc` — no
credentials). Runs on a PR touching those paths and first on every deploy. `deploy` (a push to `main` touching those paths, or
a dispatch from any branch — the `build-site` skill, `gh workflow run
site.yml --ref dev`): builds again and `wrangler deploy`s with the
`CLOUDFLARE_*` secrets. The live site is the last deploy from whichever
branch; the next merge to `main` touching the site replaces a branch
deploy with `main`'s.

## Running and watching by hand

```bash
gh workflow run desktop.yml --ref main                       # release (next version) — the `release` skill
gh workflow run desktop.yml --ref main -f publish=false      # build check only
gh workflow run desktop.yml --ref main -f prerelease=true -f version=1.2.0-rc1
gh workflow run docker.yml --ref v0.2.3 -f version=0.2.3     # re-tag an image
gh workflow run chrome-store.yml --ref main                  # submit the latest release's Connector zip again
gh workflow run docker.yml --ref dev                         # branch image :sha-<short> only (never :latest)
gh workflow run site.yml --ref dev                           # check + deploy gammapdf.com — the `build-site` skill
gh workflow run cloud.yml --ref dev                          # test + publish the account server — the `build-cloud` skill

gh run list --limit 5
gh run watch <run-id> --exit-status
gh run view <run-id> --log-failed
gh release view v<version> --json url,assets
```

A workflow can only be dispatched once its file exists on `main`
(`workflow_dispatch` is read from the default branch); pass `--ref dev` to
run the `dev` copy after that.

## Secrets

All optional; without them builds are unsigned / not submitted, and the
workflows say so with a `::warning::` or `::notice::`. `gh secret list`
shows which exist.

| Secret | Used by |
|---|---|
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_SIGN_ENDPOINT`, `AZURE_SIGN_ACCOUNT`, `AZURE_SIGN_PROFILE`, optional `AZURE_SIGN_PUBLISHER` | `desktop.yml`, Windows leg: Azure Trusted Signing |
| `MAC_CERT_P12`, `MAC_CERT_PASSWORD`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID` | `desktop.yml`, macOS leg: Developer ID + notarization (replaces the ad-hoc signature) |
| `PARTNER_CENTER_TENANT_ID`, `PARTNER_CENTER_SELLER_ID`, `PARTNER_CENTER_CLIENT_ID`, `PARTNER_CENTER_CLIENT_SECRET` | `desktop.yml`, Windows leg: Microsoft Store submission via `msstore` ([release.md](../../desktop/docs/release.md#microsoft-store) says where each value comes from) |
| `GITHUB_TOKEN` (automatic) | releases and tags, the docker dispatch, the GHCR pushes (`gamma`, `gamma-cloud`, `gamma-fleet`) |

Set them from a terminal with `gh secret set NAME` (prompts for the value);
never paste secret values into chat or files.

Repository variables (`gh variable list`, `gh variable set NAME --body …`)
hold configuration that is not a credential:

| Variable | Used by |
|---|---|
| `CWS_PUBLISHER_ID`, `CWS_ITEM_ID`, `CWS_SERVICE_ACCOUNT`, `CWS_WORKLOAD_IDENTITY_PROVIDER` | `chrome-store.yml` (and `desktop.yml`, which dispatches it only when `CWS_ITEM_ID` is set): the store item and the keyless Google sign-in ([extension/STORE.md](../../extension/STORE.md)) |

## Adding or changing a workflow

- One deliverable per workflow, a `pull_request` path filter for checks
  (release workflows are dispatch-only; `cloud.yml` is the one workflow that
  both checks and publishes, since its deliverable has no release series), and the tag-derived version rule
  above for anything that publishes.
- The Linux Electron steps need `xvfb-run`; the unpacked `linux-unpacked`
  dir needs `--no-sandbox` (the `.deb` postinst fixes that for installs).
- Pin every action to a major that runs on the runner's current Node
  (Node 24 as of 2026-09: `actions/checkout@v7`, `setup-node@v7`,
  `setup-python@v7`, `upload-artifact@v7`, `download-artifact@v8`,
  `softprops/action-gh-release@v3`, `docker/*` v4/v6/v7,
  `microsoft/microsoft-store-apppublisher@v1.4`). A run annotated "Node.js
  20 is deprecated … forced to run on Node.js 24" means a pin fell behind;
  check the action's `action.yml` `runs.using` at its latest tag. The
  "UNSIGNED build" warnings are expected until the signing secrets exist.
- Validate YAML locally with the desktop tree's `js-yaml`
  (`node -e "require('js-yaml').load(require('fs').readFileSync(f,'utf8'))"`
  from `desktop/`), and keep this document, the `merge` and `release` skills and
  [desktop/docs/release.md](../../desktop/docs/release.md) in sync.
