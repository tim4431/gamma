# GitHub Actions

Ten workflows live in `.github/workflows/`. A merge to `main` publishes
the Docker image and, when their paths changed, the website and the fleet
agent's image; it releases no app. The desktop app, the Android app and
the browser extension are released together by dispatching `desktop.yml`
— the `release` skill does that — and nothing is bumped or tagged by hand:
versions are computed from the tags. The iPad app is built and signed
outside Actions: a Mac mini watches `main` for a commit message carrying
an `ipad-test-release-NNN` marker and uploads a TestFlight build for it.
Every desktop release from `main` pushes such a marker
([below](#the-ipad-testflight-release)). The
account server (`cloud/`) and the website (`sites/`) are separate from all
of that: each has its own check and its own publish (`cloud.yml`,
`site.yml`), dispatched from any branch (the `build-cloud` / `build-site`
skills), and a PR that touches only one of them skips `check`. The public
demo (demo.gammapdf.com) and the Gamma Cloud share host
(app.gammapdf.com, share.gammapdf.com) run `main`'s `:latest` like the
NAS, and all three pick up a merge's image by themselves through
Watchtower (the `update-demo-server`, `update-share-host` and
`update-server` skills do it at once).

| Workflow | File | Runs when | Produces |
|---|---|---|---|
| `check` | `check.yml` | every pull request to `main`, except one that only touches the account server or the website | pass/fail: brand asset consistency, the mobile apps' pins resolved, the backend suite on the apps' Python 3.13 and pins (`backend-mobile`), backend pytest, the frontend lint, unit tests + build and the desktop shell's unit tests, the browser suite (two shards on two runners, 3 workers each), extension unit tests + zip (~5 min) |
| `desktop` | `desktop.yml` | manual dispatch only (`release` skill) | Windows installer, macOS dmg + zip, Debian/Ubuntu deb, the update-feed files, the Android APK (when signed), the browser extension's `gamma-connector-<version>.zip` and the AI plugins → GitHub Release `v<version>`; after it, the MSIX to the Microsoft Store and the Android bundle to Google Play's internal track when their secrets exist; a Docker tag `<version>`; from `main`, an empty commit on `main` with the next `ipad-test-release-NNN` marker (TestFlight) |
| `chrome-store` | `chrome-store.yml` | dispatched by the desktop release (from `main`, not a pre-release) when the `CWS_*` variables exist; daily at 09:17 UTC for a release the store skipped; manual dispatch with a tag | the release's Connector zip uploaded to the Chrome Web Store and submitted for review (API V2) |
| `docker` | `docker.yml` | every push to `main` except one that only touches the account server or the website; dispatched by the desktop release with a version; manual dispatch from any branch (a `sha-<short>` image only) | `ghcr.io/tim4431/gamma:sha-<short>` on every run; `:latest` only from `main`; `:<version>` and `:<major.minor>` when dispatched with a version; linux/amd64 + arm64 |
| `cloud` | `cloud.yml` | a pull request touching `cloud/`; manual dispatch from any branch (`update-account-server` skill) | pass/fail: the account server's pytest; when dispatched and green, `ghcr.io/tim4431/gamma-cloud:latest` + `:sha-<short>` (`cloud/Dockerfile`, amd64) |
| `fleet` | `fleet.yml` | a pull request or a push to `main` touching `cloud/fleet/`; manual dispatch from any branch | pass/fail: the fleet agent's pytest; on a push to main or a dispatch, when green, `ghcr.io/tim4431/gamma-fleet:latest` + `:sha-<short>` (`cloud/fleet/Dockerfile`, amd64; [hosted.md](hosted.md)) |
| `site` | `site.yml` | a PR or a push to `main` touching `sites/`, `docs/` (the artwork it copies and the documents it renders), `desktop/ui/fonts/` (the font it copies), `docker-compose.yml.example` (the file the front page shows), `PRIVACY.md` or `TERMS.md`; manual dispatch from any branch (`build-site` skill) | pass/fail: the site builds with every internal link resolving and its Worker passes a dry run; on a push or dispatch, gammapdf.com: `sites/dist` deployed as a Cloudflare Worker (static assets only; needs `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID`; [sites/README.md](../../sites/README.md)) |
| `ipad` | `ipad.yml` | a pull request touching `ipad/`, the server's mobile entry and pins (`backend/gamma_mobile.py`, `backend/requirements-mobile.txt`), `backend/requirements.txt`, the ink fixtures its tests read (`tests/shared/inkwidth.json`, `inkhold.json`), the desktop app's chrome pages the app carries (`desktop/ui/`), `tools/build_frontend_if_needed.mjs` or the workflow; manual dispatch | pass/fail on macOS: the app's Python, wheels, server code and web app prepared (`ipad/scripts/build-python.sh`), the Xcode project generated (XcodeGen), the XCTest suite on an iPad simulator with the embedded server started, an unsigned device build ([ipad/README.md](../../ipad/README.md)) |
| `android` | `android.yml` | a pull request touching `android/`, the server's mobile entry and pins, `backend/requirements.txt`, the ink fixtures, `desktop/ui/`, `tools/build_frontend_if_needed.mjs` or the workflow; manual dispatch | pass/fail on Ubuntu: the JVM unit tests, an unsigned 64-bit ARM release build checked for 16 KB alignment, and the device tests on an x86_64 Android 16 emulator with the embedded server started |
| `Assistant plugin package` | `codex-plugin.yml` | PRs touching the plugin or its tooling, or manual dispatch | plugin packaging and installer tests on Windows/macOS/Linux and preview plugin release assets (`plugins-<os>` artifacts) |

The `desktop` workflow also builds the versioned assistant plugin assets —
the Codex and Claude Code plugin zips, the DeepSeek Harness tarball, the
Codex setup scripts for Windows and macOS/Linux, and checksums
(`tools/release_plugins.py`) — and the browser extension zip, and uploads
them onto the same release.

```
PR → main ──▶ check (pytest, npm run lint + test + build, e2e, extension zip)   ← merge skill waits for this
PR touching android/ ──▶ android.yml: unit tests, release build, emulator   ← beside check
PR touching ipad/ ──────▶ ipad.yml: simulator tests, device build           ← beside check
merge ───────▶ docker.yml  ghcr :latest                              ← every merge (not cloud/- or sites/-only ones)
         └──▶ site.yml    check → gammapdf.com                      ← only when sites/, docs/, desktop/ui/fonts/, docker-compose.yml.example, PRIVACY.md or TERMS.md changed
         └──▶ fleet.yml   test → ghcr gamma-fleet :latest           ← only when cloud/fleet/ changed
PR touching cloud/ ──▶ cloud.yml: test                            ← instead of check, for cloud/-only PRs
PR touching sites/ ──▶ site.yml: check                            ← instead of check, for sites/-only PRs
build-site ──▶ site.yml --ref <branch>: check → gammapdf.com        ← no merge needed
update-account-server ──▶ cloud.yml --ref <branch>: test → ghcr gamma-cloud :latest :sha-<short>
                          then pull + restart on the VPS          ← no merge needed
update-demo-server ──▶ pull main's :latest in the demo's project    ← after a merge, like update-server;
                                                                     Watchtower does it within the hour
update-share-host ──▶ pull main's :latest for the share service   ← after a merge, like update-server;
                      in the account project                         Watchtower does it within the hour
update-needed ──▶ compares what each deployment runs with the code, then runs
                  update-account-server → update-demo-server → update-share-host
                  → update-server for the ones behind
release skill ───▶ desktop.yml  meta: version = max(package.json, newest v* tag + patch), versionCode
 (gh workflow           frontend: the web app, built once
  run)                  build Win/mac/Linux with that version pinned, smoke on all three
                        build the Android APK + bundle (signed when the ANDROID_* secrets exist)
                        zip the extension, the same version pinned into its manifest
                        publish: Release v<version> (notes = commits since previous tag)
                        └─▶ dispatch docker.yml -f version   ─▶ ghcr :<version> :<major.minor>
                        └─▶ dispatch chrome-store.yml -f tag ─▶ Chrome Web Store review (if CWS_* variables)
                        └─▶ push an empty commit marked ipad-test-release-NNN to main
                            ─▶ the Mac mini: TestFlight (below)
                        store: MSIX → msstore publish (if PARTNER_CENTER_* secrets)
                        play: bundle → Google Play internal track (if PLAY_SERVICE_ACCOUNT_JSON)
release-ipad ────▶ a commit marked ipad-test-release-NNN merged to main  ← TestFlight between releases
                   └─▶ the Mac mini (polls main every minute): build, test, sign → TestFlight
```

## Versions and tags

The **newest tag is the source of truth**; the files hold a floor.

| Deliverable | Floor | Tag series | Release name |
|---|---|---|---|
| Desktop app, Android app, browser extension | `desktop/package.json` `"version"` | `v<version>` | `Gamma <version>` |

The browser extension has no series of its own: its zip carries the
release's version. Up to `extension-v0.1.1` it was released separately as
`extension-v<version>`; those tags and releases stay, and the `v*` rule
ignores them.

On every run the `meta` job lists the tags of its series on the remote and
takes the newest plain `vX.Y.Z` (a pre-release's tag does not count). If
the floor is higher than that, the floor is the version — **that is how
you make a minor or major release: raise the floor and merge**. Otherwise
the version is the newest tag with the patch number plus one. A version,
an override too, must be `<major>.<minor>.<patch>[-suffix]`, or `meta`
fails. The result is pinned into the build (`npm version` in the desktop
checkout; `jq` into the manifest inside the extension zip; Gradle's
`-PversionName` and `-PversionCode` for Android), so the app, `latest*.yml`,
the MSIX manifest, the zip and the APK all carry it, while the repository
files stay untouched: no bot commits on `main`, nothing for `dev` to
conflict with. The release step creates the tag from the merged commit.

Android needs a second number, `versionCode`, an integer that must grow
with every update and is at most 2,100,000,000. `meta` derives it from the
version: `major × 1,000,000 + minor × 1,000 + patch` (0.3.13 → 3013, 0.4.0
→ 4000, 1.0.0 → 1000000), so it keeps the versions' order and needs no
state. A minor or patch number above 999 does not fit and fails `meta`:
make a minor or major release first. A pre-release shares its final's
code (1.2.0-rc1 and 1.2.0 are both 1002000), one of the reasons a
pre-release goes to no store.

Consequences:

- A version is never reused. Deleting or moving a tag is never the fix; the
  next merge is.
- A merge releases no app; a dispatched run whose build fails on any
  platform releases nothing (the publish job needs every desktop leg, the
  Android build, the plugins and the extension).
- A manual run with `publish=false` builds and keeps the outputs as
  artifacts: 14 days, the store packages (`store-windows`, `store-android`)
  30. A manual `version` input overrides the computation; it fails early
  if that tag already exists.
- `prerelease=true` on a manual run publishes without marking the release
  *Latest*; installed apps ignore pre-releases, and no store gets one.
- A release that is not a pre-release must come from `main`: `meta` fails
  a dispatch from another branch with `publish=true` and
  `prerelease=false`, since the release would become *Latest*.

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
`cancel-in-progress: false`: a dispatch while a run is going waits for it
and gets the next number instead of racing for the same one. GitHub keeps
one waiting run per group, so a third dispatch cancels the one waiting.

## `desktop.yml`

`meta` computes the version (above). A `frontend` job builds the web app
once on Ubuntu (`npm ci`, `npm run build`) and uploads `frontend/dist` as
the `frontend-dist` artifact, which the desktop legs and the Android job
download instead of building it again; Vite's output is the same on every
OS. It is kept 14 days, as long as the artifacts `publish` takes: *Re-run
failed jobs* reuses the upload of the first attempt, so a leg re-run days
later still needs it.

The `desktop` matrix runs on `windows-latest`, `macos-latest` and
`ubuntu-latest` (named `windows`, `macos`, `linux`); every leg: pin the
version, take the built web app, freeze the backend with PyInstaller,
health-check the frozen server, electron-builder, verify the signature,
run the packaged app's `--smoke` self-test (under Xvfb on Linux), and
upload `desktop-<name>`. Each platform's signing secrets reach only its
own build step, and the signature check sees only whether they exist, so
no other step (`npm ci`'s install scripts, a third-party action) can read
them. Windows also builds the unsigned Microsoft Store MSIX
(`store-windows` artifact); that step has none of the signing env, so
electron-builder leaves it unsigned for the Store to sign. macOS is
ad-hoc signed by `desktop/scripts/adhoc-sign.cjs` when no Developer ID is
available (see
[release.md](../../desktop/docs/release.md#code-signing-optional-secret-gated)).
Linux additionally `apt install`s the `.deb` on the runner and runs the
self-test from `/opt/Gamma/gamma` with the sandbox on.

The macOS signature diagnostics must consume all `codesign` output: an early
exit from `grep -q` or `head` can break the pipe and fail the release under
`pipefail` even when the signature is valid. Signature verification errors
must still fail the job.

The `plugins` job tests the plugin packaging and builds the assistant
plugin assets with the release's version (`plugins` artifact). The
`extension` job zips the Connector with `extension/scripts/zip.sh`, which
`check.yml` runs on every PR as well: `extension/` with the version written
into `manifest.json` (a pre-release suffix such as `-rc1` goes to
`version_name`, since Chrome's `version` takes only numbers), without the
store listing (`STORE.md`, `README.md`, `store/`), the script's own
`scripts/` folder and `.DS_Store`, as `gamma-connector-<version>.zip`.
After the release, the publish job dispatches `chrome-store.yml` with the
tag, which submits that zip to the Chrome Web Store (below).

The `android` job builds the Android app on `ubuntu-24.04` with Temurin 21
(setup-java's Gradle cache), Python 3.13 on `PATH` (Chaquopy runs pip under
the app's Python version) and the `frontend-dist` artifact:
`bash ./gradlew :app:assembleRelease :app:bundleRelease` with
`-PversionName`, `-PversionCode`, `-Pgamma.abis=arm64-v8a` (64-bit ARM
only: Chaquopy's Python packages are assets, which no ABI split divides)
and `-Pgamma.webBuild=prebuilt` (`android/app/build.gradle.kts` reads them).
When the `ANDROID_*` secrets exist, the same step decodes the keystore into
the runner's temp folder and the build signs both outputs; no other step
sees the secrets. The outputs are named `Gamma-<version>-android-arm64.apk`
and `Gamma-<version>-android.aab`; `zipalign -c -P 16` proves the APK's
native libraries are aligned to 16 KB pages (Play refuses others), and a
signed build is checked with `apksigner verify` and `jarsigner -verify`.
The APK is the `android` artifact (`android-unsigned` without the
secrets), the bundle the `store-android` artifact. Only a signed APK goes
on the release: the runner's debug key is new on every run, so no later
release could update an app signed with it.

The `publish` job merges the platform, plugin and extension artifacts and
the signed APK, writes the notes, creates the release and its tag, and
starts what follows. The notes are Markdown: a **Download** row of
shields.io buttons for the installers, the first-launch, install and
update instructions folded in a `<details>`, **What's new since
<previous tag>** (folded: the commit subjects since that tag that touch
`desktop/ backend/ frontend/ extension/ android/ ipad/ plugins/`, trailing
punctuation trimmed, repeats dropped; this repo's PR titles are all
"Merge pull request #N from dev", so GitHub's generator would say nothing),
then **iPad app** (the TestFlight button; only for a release from `main`,
the one that sends the iPad build), **Android app** (the APK and, when the
`PLAY_URL` variable is set, a Google Play button; only when the APK is
signed), **Server (Docker)**, **AI assistant plugins** and **Browser
extension**. The release is marked *Latest* unless it is a pre-release
(`make_latest: ${{ inputs.prerelease != true }}`). Then, for a release
made from `main`, it runs `gh workflow run docker.yml --ref v<version> -f
version=…` so the server image gets the same version tag (a tag made with
`GITHUB_TOKEN` would not trigger `docker.yml` by itself); a pre-release
from a branch gets no server image. `docker.yml` also bakes the build
stamp into the image as build args (`GAMMA_VERSION` = the dispatched
version, or `<newest release tag>-dev.<commits since it>` for any other
build, with `GAMMA_BRANCH` = the branch it was built from; `GAMMA_COMMIT` =
the sha), which the admin dashboard shows and compares against the latest
release and, for a `-dev` build, its branch's head (`gamma/version.py`,
[user_db.md](user_db.md)). The checkout is full-depth for the tags. A
release from `main` that is not a pre-release also dispatches
`chrome-store.yml`, and the last step pushes the TestFlight marker (below).

No push trigger: the app bundles the backend and the frontend, so a path
filter would release it on nearly every merge. It runs only when dispatched
(`release` skill).

### The store jobs

The stores come after `publish`, so a version reaches a store only once
its tag exists. A store takes each version number once: submitted from a
build leg, a version would be spent there while another leg's failure
left it without a tag, and the next run, computing the same number again,
would be refused. They key on `publish`'s `released` output, set by a step
right after the release step, with `!cancelled()`, rather than on the
whole job: a Docker or Chrome Web Store dispatch or the TestFlight marker
push that fails after the tag exists still lets them run, since the next
run computes the next number and this one would never reach a store.
Re-running such a `publish` runs them again, and each store refuses the
number it has seen with a warning. Both jobs run only for a release
from `main` that is not a pre-release (a pre-release carries its final's
number), and both are `continue-on-error` on the submission itself: a
store's refusal is a warning, never a failed release.

- **`store`** (Windows) downloads `store-windows` and, when the
  `PARTNER_CENTER_*` secrets exist, submits it with the `msstore` CLI (the
  Store allows one submission in certification at a time, so a second
  release the same day is refused and waits for the next). The
  step's verdict is the CLI's "Submission commit success", else `msstore
  submission status`, not the CLI's exit code — the first run exited 1 on
  a transient re-read after the package was already in certification.
  Without the secrets it says so in a notice.
- **`play`** runs only when the Android build was signed. It downloads
  `store-android` and, when the `PLAY_SERVICE_ACCOUNT_JSON` secret exists,
  sends the bundle to Google Play with `r0adkll/upload-google-play`
  (package `com.gammapdf.gamma`, the track in the `PLAY_TRACK` variable,
  `internal` by default; the status in `PLAY_RELEASE_STATUS`, `completed`
  by default, `draft` while the app is a draft, when the upload waits on
  the track until someone rolls it out by hand in the Console). A notice
  names the version, its versionCode and the track; a warning lists the
  usual causes when Play refused (a versionCode sent before, an app still
  a draft, no first upload by hand).

### Google Play, once

The Play Console is the developer's personal account. A personal account
created after November 2023 reaches production only after a closed test
with at least 12 testers opted in for 14 days, so CI feeds the internal
track and promotion to closed testing and production is done by hand in
the Console. Why each step is so, with Google's sources, is in
[android.md "Publishing on Google Play"](android.md#publishing-on-google-play);
the account's own checks (identity, device verification, the trader
declaration), the listing and the closed test are in
[android/STORE.md "The closed test"](../../android/STORE.md#the-closed-test).

1. **The release key.** `keytool -genkeypair -v -keystore gamma-release.jks
   -alias gamma -keyalg RSA -keysize 2048 -validity 10000`; `keytool -list
   -v -keystore gamma-release.jks -alias gamma` shows its SHA-256. It
   signs the APK and the bundle in CI and becomes the app signing key
   Play signs its installs with (step 3). Play never gives it back, an
   app signed with it can be updated only by an APK signed with it, and
   without it no package can be registered for developer verification,
   so it is backed up off GitHub with the other signing keys. Then the
   four secrets:
   `base64 -w0 gamma-release.jks | gh secret set ANDROID_KEYSTORE_BASE64`,
   and `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS` (`gamma`),
   `ANDROID_KEY_PASSWORD` with `gh secret set NAME`. keytool makes a PKCS12
   keystore, whose key has the keystore's password, so the two password
   secrets are the same. The next release then carries the APK.
2. **The app in the Console**: **Create app** with the package name
   `com.gammapdf.gamma`, which is permanent and registers the package to
   the account for developer verification, accepting the Play App Signing
   terms on the way.
3. **The first release by hand, with the release key as the app signing
   key.** Test and release → Testing → Internal testing → **Create new
   release**. In its *App integrity* section, **Change app signing key**
   (or Protected with Play → Play Store protection → Manage Play app
   signing → **Change key**), and choose to provide a copy of the app
   signing key; the dialog offers `pepk.jar` and
   `encryption_public_key.pem`. Run PEPK from PowerShell or cmd, not Git
   Bash: it asks for the passwords on the console, which mintty does not
   give it.

   ```powershell
   java -jar pepk.jar --keystore=gamma-release.jks --alias=gamma --output=gamma-pepk.zip --include-cert --rsa-aes-encryption --encryption-key-path=encryption_public_key.pem
   ```

   Upload `gamma-pepk.zip`, then the first `store-android` artifact's
   `.aab` (`gh run download <run-id> -n store-android`), and roll the
   release out to internal testing
   ([Play App Signing](https://support.google.com/googleplay/android-developer/answer/9842756)).
   Doing it by hand is also what the upload action's README asks: it says
   the API finds no app before a first upload in the Console. Skipping
   the key change keeps Google's default, a key Google generates, with
   the release key as the upload key only; what that would take is in
   [android.md](android.md#publishing-on-google-play). The key can still
   be changed until a release rolls out on open testing or production,
   and is fixed from then on.
4. **The service account.** In the Google Cloud console, signed in with
   the developer's personal Google account (a Workspace or Cloud Identity
   organisation created on or after 3 May 2024 blocks service-account
   keys by default): a project; the Google Play Android Developer API
   (`androidpublisher.googleapis.com`) enabled in it; IAM & Admin →
   Service accounts → a service account with no roles; its Keys → Add key
   → JSON. Then in the Play Console, Users and permissions → **Invite new
   users**: the service account's e-mail address, the app under *App
   permissions* with *Release apps to testing tracks*, **Invite user**.
   No Cloud project is linked to the Play Console any more
   ([getting started](https://developers.google.com/android-publisher/getting_started)).
   Last, `gh secret set PLAY_SERVICE_ACCOUNT_JSON < key.json`, then
   delete the file.
5. **While the app is a draft** (nothing rolled out yet), community
   reports say the API takes only draft releases:
   `gh variable set PLAY_RELEASE_STATUS --body draft`. Each release's
   upload then waits on the internal track as a draft; roll it out by
   hand in the Console, under Internal testing. Google does not document
   when an app stops being a draft, so try deleting the variable once a
   release has been rolled out from the Console, and set it again if
   Play refuses the next upload (a refusal is only a warning).
6. **Developer verification.** On the Console's *Android developer
   verification* page, under *Package names*, check that
   `com.gammapdf.gamma` lists the release key's SHA-256; if it does not,
   **Add key** with it (Play may ask for an APK signed with the key that
   carries `assets/adi-registration.properties`).
7. **The public link**, only once the app is public on production:
   `gh variable set PLAY_URL --body https://play.google.com/store/apps/details?id=com.gammapdf.gamma`
   puts a Google Play button in the release notes. Never the closed
   test's opt-in link, which only the testers on its lists can use.

## `android.yml`

Two Ubuntu jobs (`ubuntu-24.04`) for the Android app (`android/`), which
carries the Gamma server on Chaquopy's CPython, the built web app and the
desktop's chrome pages. Like `ipad.yml`, it runs for the server's mobile
entry and pins, `backend/requirements.txt`, `desktop/ui/`, the two ink
fixtures the Kotlin pen ports are held to and the frontend's build helper
as well as for `android/`, but not for backend or frontend code, which the
other checks test. Both build the web app first (Node 22, `npm ci`, `npm
run build`) and pass `-Pgamma.webBuild=prebuilt`, with Temurin 21 and
setup-java's Gradle cache, and Python 3.13 on `PATH` for Chaquopy's pip.

- **`build`** (40 minutes): `:app:testDebugUnitTest`, then an unsigned
  `:app:assembleRelease` for 64-bit ARM, what a release ships, whose APK
  must pass `zipalign -c -P 16` (16 KB pages). A failed unit test uploads
  the Gradle test report as `android-unit-test-reports`.
- **`emulator`** (50 minutes): the app and its device tests built for
  `x86_64` alone (`-Pgamma.abis=x86_64`: its Python packages are per ABI),
  KVM opened to the runner's user, then `:app:connectedDebugAndroidTest`
  on an API 36 `google_apis` x86_64 emulator
  (`reactivecircus/android-emulator-runner`). Besides the pure parts, one
  test starts the embedded server, waits for its `/api/health`, then
  uploads a PDF and asks for a page picture, which only PyMuPDF draws
  (`LocalServerStartTest`): a broken Python bundle fails here and not on a
  phone. The apps stay installed after the run, so the script reads the
  server's `files/Gamma/local/logs/server.log` (`adb exec-out run-as`) and
  logcat while the emulator still runs; on a failure a step prints the
  log's tail and logcat around a Python failure, and they are uploaded
  with the test reports as `android-emulator-failures`.

Nothing is signed or uploaded here; the release build is `desktop.yml`'s
`android` job. Locally, from `android/`: `bash ./gradlew
:app:testDebugUnitTest` and `:app:assembleDebug`, with JDK 21 and a
Python 3.13 on `PATH` (or `-Pgamma.buildPython=…`). By default the build
first rebuilds the web app when `frontend/dist` is stale, and `npm run
build` empties that folder meanwhile; `-Pgamma.webBuild=prebuilt` takes it
as it is.

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
draft. A daily scheduled run (09:17 UTC, the latest release) catches up on
a version skipped that way once the review is over; it makes no call
beyond `fetchStatus` when there is nothing to do, and it does not submit
again a version the store already took and did not publish (a rejected
one waits for a manual run or the next release). Setup, including the
store item that only the dashboard can create:
[extension/STORE.md](../../extension/STORE.md). The public listing is
<https://chromewebstore.google.com/detail/gamma-connector/njpeklephhndnpakmlaancinpnabcomn>.

## `check.yml`

Seven parallel Ubuntu jobs on every PR to `main`: brand asset consistency,
the mobile apps' pins (`bash tools/check_mobile_pins.sh` under Python
3.13: every pin of `backend/requirements-mobile.txt`, which the iPad and
Android share, resolves to wheels for the iPad's device and simulator and
for Android's arm64 and x86_64, so a pin that stops resolving fails here
and not in `ipad.yml`'s macOS run or `android.yml`, which a backend-only PR
starts neither of),
the backend suite under the apps' Python and pins (`backend-mobile`:
Python 3.13 with `requirements-mobile.txt` + `requirements-dev.txt`, run as
the 3.12 job below is, so a server change that breaks only in the apps
fails on the PR),
backend pytest (Python 3.12, `requirements.txt` + `requirements-dev.txt`,
`-n auto` over pytest-xdist; the job is capped at 20 minutes and a single
test at 300 s, after which it fails with every thread's stack instead of
holding the job), the frontend lint, unit tests + build (Node 22,
`npm run lint`, `npm test` then `npm run build`) followed by the desktop shell's unit
tests (`node --test desktop/test/*.test.js`: the pure modules,
`main.js` under a stand-in for Electron and the one switch for test
windows, Node's built-ins only, so no install), the browser suite
(`npm run e2e -- --continue --jobs 3 --shard <k>/2`, the two shards as a
matrix of two jobs of about equal time, against a backend started from the checkout with
`GAMMA_E2E_PYTHON=python` — both requirements files, since a scenario
builds its Zotero fixture from a backend test module — Playwright's
Chromium installed with its system deps; on a failure the harness's
`failures/` folders — screenshots, page problems, the error, the server
log tail — are uploaded as the `e2e-failures-<shard>` artifact), and the extension's unit
tests (Node 22, `node --test extension/tests/*.test.mjs` — the pure
modules; its `.e2e.mjs` files need Playwright's full Chromium and are run
by hand) followed by the release's zip of the extension
(`extension/scripts/zip.sh` with a pre-release version, whose manifest
must come out split into `version` and `version_name`). No installers. A PR that changes only
`cloud/`, `cloud.yml`, the `update-account-server`, `update-demo-server` or
`update-share-host` skill or [cloud_accounts.md](cloud_accounts.md) — or only `sites/`, `site.yml`, the
`build-site` skill, `PRIVACY.md` or `TERMS.md` — skips it (`paths-ignore`); `cloud.yml`
and `site.yml` check those. (A PR touching the brand artwork the site
copies still runs `check`: its `branding` job owns those files.) The `merge` skill runs the same suites
locally first and waits for this before merging; a red check, a flaky test
or a real bug, is fixed on the branch in a commit of its own, pushed, and
watched again.

## `ipad.yml`

One macOS job (`macos-15`, 50 minutes) for the iPad app, which carries
the Gamma server and the built web app ([ipad.md](ipad.md#the-local-server)).
It runs for the server's dependencies, its mobile entry and pins
(`backend/gamma_mobile.py`, `backend/requirements-mobile.txt`, shared with
Android) and the frontend's build helper as well as for `ipad/`, but not
for backend or frontend code, which the other checks test. It also runs
for the two ink fixtures the Swift ports of the brush table and the held
shape are held to (`tests/shared/inkwidth.json`, `inkhold.json`, resources
of the test bundle), and for `desktop/ui/`, the desktop app's bar and
launcher, which the app carries as its own chrome
([ipad.md](ipad.md#the-chrome)). A change that matters on the device is
dispatched by hand.

- Python 3.13 (`setup-python`), so pip's environment markers match the
  device's, and Node 22 with `npm ci` in `frontend/`.
- `actions/cache` on `~/Library/Caches/gamma-ipad`, the support tarball,
  the pip venv and pip's cache of the iOS wheels, keyed on
  `ipad/scripts/build-python.sh` and `backend/requirements-mobile.txt`.
- `bash ipad/scripts/build-python.sh` prepares `Python.xcframework` and
  the bundle's Python folder.
- `xcodegen generate` makes the project from `ipad/project.yml`.
- `xcodebuild test` runs on the newest iPad simulator the image has, with
  `ONLY_ACTIVE_ARCH=YES ARCHS=arm64` (`install_python` copies one
  architecture's standard library) and `CODE_SIGNING_ALLOWED=NO`. Besides
  the pure parts, one test starts the embedded server, waits for its
  `/api/health`, then uploads a PDF and asks for a page picture, which
  only PyMuPDF draws (`LocalServerStartTests`): a broken bundle, the
  MuPDF frameworks included, fails here and not on an iPad.
- When that fails, a step prints the simulator's `server.log` (the data
  directory's `logs/`, where a failed start's traceback is) and the raw
  `xcodebuild` output around the failure, which `xcbeautify` leaves out.
- `xcodebuild build -sdk iphoneos -configuration Release` makes an
  unsigned device build, with `CODE_SIGNING_ALLOWED=NO`.

Nothing is signed or uploaded here: running on an iPad takes a developer's
own team, set in Xcode, and the TestFlight upload is the Mac mini's
(below). The pins check (every pin resolved for both apps' platforms, no
Xcode) runs on Linux in `check.yml`, on every PR.

### The iPad TestFlight release

The signed build is made outside GitHub Actions, on a Mac mini that holds
the signing identity of team `A436A36DDC`, whose App Store Connect record
(`net.blitzbuild.gamma`, shared with the Mac App Store build) the builds
go to. It checks `tim4431/gamma`'s `main` every
minute; when a commit's message (subject or body) contains a marker
`ipad-test-release-NNN` it has not seen, it checks that commit out, builds
the app, runs the tests, signs it and uploads it to TestFlight. Nothing is
dispatched by hand, and nothing is submitted to the App Store: that step
stays manual in App Store Connect. Testers join through the public link
<https://testflight.apple.com/join/nTmSHDx6>, which the release notes
carry (the `iPad app` section `desktop.yml` writes for a release from
`main`).

Every desktop release from `main` sends one. The `desktop` publish job's
last step pushes an empty commit, `Gamma <version> to TestFlight
(ipad-test-release-NNN)` with the next number, on top of the released
commit. A push with `GITHUB_TOKEN` starts no workflow, so `docker.yml` and
`site.yml` do not run again. `main` then has a commit `dev` lacks; it
changes no file, so the next merge from `dev` is unaffected. When `main`
has moved past the released commit since the dispatch, the step only
warns: the iPad would get code the release lacks.

- The three-digit `NNN` is a trigger ID, not the app's version
  (`CFBundleShortVersionString` comes from `ipad/project.yml`). Each
  release takes a new number; a marker that was already used is ignored,
  so a retry after a failure takes a new number too.
- A squash merge rewrites the commit message: the marker then has to be
  kept in the squashed commit's title or body. The `merge` skill merges
  with a merge commit, which keeps it.
- The `release-ipad` skill finds the next number from `main`'s history and
  walks through the commit and the merge: a TestFlight build between
  desktop releases, or after a release whose step warned.

## `docker.yml`

buildx for amd64 + arm64, `latest` on every push to `main` (the same
`paths-ignore` as `check.yml`, kept alike by hand: an account-server- or
website-only merge rebuilds nothing — neither is in the image). When dispatched
with a `version` input (the desktop publish job does this on the release
tag) it also pushes `<version>` and `<major.minor>`. Every run pushes
`sha-<short>` (the metadata action's `type=sha`: the commit's first 7
characters) and labels the image `org.opencontainers.image.revision` with
the full commit. Any other build's stamp counts from the newest plain
`vX.Y.Z` tag, as `meta` does, so a pre-release's tag never names a dev
build.

Dispatched on a branch without a version
(`gh workflow run docker.yml --ref <branch>`) it pushes only
`sha-<short>`: `latest` is enabled on the default branch alone
(`{{is_default_branch}}`) and the semver tags only with a version: a way to
try a branch's image without moving what the NAS and the demo pull. A run
with `-f version` is a release (`<version>`, `<major.minor>` and, through
the semver rules, `latest`), so only `main`'s code gets one: its first step
fails unless the commit is already on `main`. The account server's and the
fleet agent's images (`cloud.yml`, `fleet.yml`) are separate and still
publish from any branch.

The public demo and the share host run `main`'s `:latest`, like the NAS: a
Watchtower in each compose project polls GHCR every hour (the example's
six, shortened) and recreates the container when a merge has published a
new image, as in `docker-compose.yml.example`. The two on the VPS each
have a scope (`demo`, `share`), since two unscoped ones would both update
every labelled container and each would remove the other. The
`update-demo-server`, `update-share-host` and `update-server` skills do the
same pull at once ([cloud/deploy/demo/README.md](../../cloud/deploy/demo/README.md),
[cloud/deploy/README.md](../../cloud/deploy/README.md) "The free share
host", [guests.md](guests.md)). A merge that adds a workspace migration
step the desktop release lacks reaches the share host within the hour,
and every publishing desktop app then pauses until a release carries it
([mirror.md](mirror.md) "The data format"): the `merge` skill asks before
merging one, and the `update-share-host` skill's rollback pin (a
`compose.override.yml` naming a `sha-<short>` tag) can hold the share host
back meanwhile. Setup notes: [docs/dev/debugging.md](debugging.md) and the memory
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
app code, only on `sites/`, `PRIVACY.md`, `TERMS.md`, `desktop/ui/fonts/` (the
Inter file the pages use), `docker-compose.yml.example` (the front page shows
it) and `docs/`: the artwork the build copies and the
user guide it renders as a page, so a change under `docs/` on `main` deploys
the site.
`check`: `npm ci`, `node build.mjs --strict` (fails on an internal link,
picture or anchor that resolves to nothing), the key pages present in `dist/` (index,
404, pricing, privacy, terms, the user guide, the sitemap,
`_redirects`, `_headers`, the favicon, the font), no `<!--#` build directive left unexpanded, and `wrangler
deploy --dry-run` (bundles the Worker and reads `wrangler.jsonc` — no
credentials). Runs on a PR touching those paths and first on every deploy. `deploy` (a push to `main` touching those paths, or
a dispatch from any branch — the `build-site` skill, `gh workflow run
site.yml --ref dev`): builds again and `wrangler deploy`s with the
`CLOUDFLARE_*` secrets (`cloudflare/wrangler-action`, which uses the
wrangler `sites/package.json` installs). The live site is the last deploy from whichever
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
gh workflow run android.yml --ref dev                        # the Android checks on a branch

gh run list --limit 5
gh run watch <run-id> --exit-status
gh run view <run-id> --log-failed
gh run download <run-id> -n store-android                    # a release's bundle, for a Play upload by hand
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
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_SIGN_ENDPOINT`, `AZURE_SIGN_ACCOUNT`, `AZURE_SIGN_PROFILE`, optional `AZURE_SIGN_PUBLISHER` | `desktop.yml`, the Windows leg's build step: Azure Trusted Signing |
| `MAC_CERT_P12`, `MAC_CERT_PASSWORD`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID` | `desktop.yml`, the macOS leg's build step: Developer ID + notarization (replaces the ad-hoc signature) |
| `ANDROID_KEYSTORE_BASE64` (the release keystore, base64), `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` | `desktop.yml`, the `android` job's build step: the signed APK on the release and the signed bundle for Google Play ([above](#google-play-once)) |
| `PARTNER_CENTER_TENANT_ID`, `PARTNER_CENTER_SELLER_ID`, `PARTNER_CENTER_CLIENT_ID`, `PARTNER_CENTER_CLIENT_SECRET` | `desktop.yml`, the `store` job: Microsoft Store submission via `msstore` ([release.md](../../desktop/docs/release.md#microsoft-store) says where each value comes from) |
| `PLAY_SERVICE_ACCOUNT_JSON` | `desktop.yml`, the `play` job: the service account's JSON key, invited in the Play Console with *Release apps to testing tracks* ([above](#google-play-once)) |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | `site.yml`, `deploy`: gammapdf.com as a Cloudflare Worker ([sites/README.md](../../sites/README.md)) |
| `GITHUB_TOKEN` (automatic) | releases and tags, the dispatches of `docker.yml` and `chrome-store.yml`, the TestFlight marker's push to `main`, the GHCR pushes (`gamma`, `gamma-cloud`, `gamma-fleet`) |

Set them from a terminal with `gh secret set NAME` (prompts for the value);
never paste secret values into chat or files.

Repository variables (`gh variable list`, `gh variable set NAME --body …`)
hold configuration that is not a credential:

| Variable | Used by |
|---|---|
| `CWS_PUBLISHER_ID`, `CWS_ITEM_ID`, `CWS_SERVICE_ACCOUNT`, `CWS_WORKLOAD_IDENTITY_PROVIDER` | `chrome-store.yml` (and `desktop.yml`, which dispatches it only when `CWS_ITEM_ID` is set): the store item and the keyless Google sign-in ([extension/STORE.md](../../extension/STORE.md)) |
| `PLAY_TRACK` (default `internal`), `PLAY_RELEASE_STATUS` (default `completed`; `draft` while the app is a draft, each upload then rolled out by hand in the Console) | `desktop.yml`, the `play` job |
| `PLAY_URL` | `desktop.yml`, the release notes: a Google Play button beside the APK, the store listing; set only once the app is public on production ([above](#google-play-once)) |

## Adding or changing a workflow

- One deliverable per workflow, a `pull_request` path filter for checks,
  and the tag-derived version rule above for anything that publishes.
  Release workflows are dispatch-only; a deliverable with no release
  series checks and publishes in one workflow (`cloud.yml`, `fleet.yml`,
  `site.yml`).
- A secret goes in the `env:` of the steps that use it, never the job's:
  a job-level secret reaches every step, `npm ci`'s install scripts and
  third-party actions included. A step `if` cannot read secrets, so a job
  turns their presence into a flag (`STORE_SUBMIT: ${{ secrets.X != '' }}`).
- The Linux Electron steps need `xvfb-run`; the unpacked `linux-unpacked`
  dir needs `--no-sandbox` (the `.deb` postinst fixes that for installs).
- Pin every action to a major that runs on the runner's current Node
  (Node 24 as of 2026-11: `actions/checkout@v7`, `setup-node@v7`,
  `setup-python@v7`, `setup-java@v6`, `cache@v6`, `upload-artifact@v7`,
  `download-artifact@v8`, `softprops/action-gh-release@v3`, `docker/*`
  v4/v6/v7, `google-github-actions/auth@v3`,
  `cloudflare/wrangler-action@v4`, `r0adkll/upload-google-play@v1`,
  `reactivecircus/android-emulator-runner@v2`,
  `microsoft/microsoft-store-apppublisher@v1.4`). A run annotated "Node.js
  20 is deprecated … forced to run on Node.js 24" means a pin fell behind;
  check the action's `action.yml` `runs.using` at its latest tag. The
  "UNSIGNED build" warnings are expected until the signing secrets exist.
  Gradle caching is setup-java's `cache: gradle`, not
  `gradle/actions/setup-gradle`, whose cache is a proprietary component
  under Gradle's terms since v6.
- Check a workflow with [actionlint](https://github.com/rhysd/actionlint)
  before pushing it; its Docker image carries shellcheck, which it runs on
  every `run:` script
  (`docker run --rm -v "$PWD:/repo" --workdir /repo rhysd/actionlint:1.7.12`).
  Give a Windows step that is PowerShell `shell: pwsh` explicitly, so
  shellcheck does not read it as bash. Keep this document, the `merge`
  and `release` skills and
  [desktop/docs/release.md](../../desktop/docs/release.md) in sync.
