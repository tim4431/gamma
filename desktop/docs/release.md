# Package, sign, release, update

## Package locally

```bash
python desktop/build_backend.py   # freeze backend (venv python; ~50 MB onedir)
cd desktop && npm run pack        # unpacked app in dist/win-unpacked (fast test)
npm run e2e:packaged              # the suite against the frozen bundle
npm run dist                      # real installer (NSIS .exe / .dmg + .zip)
```

`electron-builder` (config: `electron-builder.js` — it replaced the
`package.json` `build` key so signing can depend on the environment) copies
`dist-backend/gamma-server` into `resources/gamma-server`; a packaged app is
fully self-contained (no Python, no Node on the user's machine). Installers
are named `Gamma-<version>-<os>-<arch>.<ext>`; next to them land the
update-feed files (`latest.yml` / `latest-mac.yml`, `*.blockmap`), see
*Auto-update* below.

## The `release` workflow

**Releasing** is one workflow, `.github/workflows/release.yml`, run by hand
from the Actions tab (or `gh workflow run release.yml --ref main`; the
`release` skill wraps it). It builds Windows + macOS installers (frontend
build → backend freeze → frozen-server health check → electron-builder →
signature verification → packaged `--smoke`), zips the browser extension,
and publishes everything as ONE GitHub Release `Gamma <version>` — creating
the `v<version>` tag itself, so no tags are pushed by hand. The version is
`desktop/package.json`'s (bump it before releasing; a version that already
has a tag is refused); the extension zip carries `extension/manifest.json`'s
own version. Inputs: `version` override, `prerelease`, and `publish=false`
for artifacts only. The Docker image is a separate workflow (`docker.yml`,
every push to `main`).

## Code signing (optional, secret-gated)

Every signing piece in `electron-builder.js` switches on only when its
credentials are present, so `npm run dist` locally and forks without certs
still produce (unsigned) installers. The workflow feeds these repository
secrets through:

| Platform | Secrets | What it does |
|---|---|---|
| Windows | `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_SIGN_ENDPOINT`, `AZURE_SIGN_ACCOUNT`, `AZURE_SIGN_PROFILE`, optional `AZURE_SIGN_PUBLISHER` | [Azure Trusted Signing](https://learn.microsoft.com/azure/trusted-signing/) via electron-builder's `azureSignOptions`: signs `Gamma.exe`, the frozen `gamma-server.exe` and the NSIS installer. The three `AZURE_*` auth values are an Entra app registration (client secret) holding the *Trusted Signing Certificate Profile Signer* role on the account; endpoint is the region URL (e.g. `https://eus.codesigning.azure.net`), account/profile are the resource names, publisher the certificate subject (`CN=…`). |
| macOS | `MAC_CERT_P12` (base64 of the *Developer ID Application* `.p12`: `base64 -i cert.p12 \| pbcopy`), `MAC_CERT_PASSWORD`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID` | Developer ID signing (electron-builder's `CSC_LINK`/`CSC_KEY_PASSWORD`) with hardened runtime + `build/entitlements.mac.plist` (JIT + `disable-library-validation`, which the PyInstaller sidecar needs to load its Python extension modules; osx-sign walks the whole `.app`, so `Contents/Resources/gamma-server` is signed too), then notarization (`mac.notarize`) with an [app-specific password](https://support.apple.com/102654) and stapling. |

The workflow prints a `::warning::` per platform when it builds unsigned,
and a *Verify signature* step (`Get-AuthenticodeSignature` / `codesign
--verify` + `spctl --assess` + `stapler validate`) fails the job if a
credentialed build didn't actually sign. Release notes state per platform
whether the build is signed.

**Unsigned builds** (the state until the accounts exist): Windows shows
SmartScreen's *More info → Run anyway* (Edge's download shelf: *… → Keep →
Keep anyway*); macOS ≥ 15 reports the app as *damaged* until
`xattr -dr com.apple.quarantine /Applications/Gamma.app` (or *Open Anyway*
in System Settings → Privacy & Security). SmartScreen reputation is per
certificate: even signed, a brand-new certificate can still trigger Edge's
"not commonly downloaded" notice for the first downloads (EV certificates
skip that). Once mac builds are signed, also flip `IN_APP_INSTALL` for
darwin in `lib/updater.js` so macOS updates install in place.

### Cheaper distribution routes

- **winget** (free, Windows): a manifest in
  [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs)
  pointing at the release `.exe` (NSIS installs silently with `/S`); winget
  verifies the SHA-256 itself and runs the installer without the
  mark-of-the-web, so SmartScreen does not interpose. Needs a published
  release to point at, then one PR per version (automatable with
  `wingetcreate` in the workflow).
- **Microsoft Store** (one-time individual registration fee): submit an MSIX
  (`electron-builder` `appx` target). The Store signs it with Microsoft's
  certificate — no SmartScreen, and the Store handles updates (the in-app
  updater must then be disabled for that build). Requires the Partner
  Center identity values (`identityName`, `publisher` GUID CN) in the
  config — nothing to configure until the account exists.
- **Homebrew cask** (macOS) does not help: brew leaves the quarantine
  attribute on; only signing + notarization fixes the *damaged* dialog.

## Auto-update feed

`electron-builder.js` has `publish: { provider: 'github', owner, repo }`.
That does two things and nothing else (the workflow still runs
`--publish never`; it creates the GitHub Release itself):

1. `resources/app-update.yml` is baked into the app, telling
   `electron-updater` where to look.
2. `latest.yml` (Windows) / `latest-mac.yml` (macOS) and the installer
   `.blockmap`s are written next to the installers; the workflow uploads
   them as release assets alongside the `.exe` / `.dmg` / `.zip`.

At run time (`lib/updater.js`; behavior in
[architecture.md](architecture.md#in-app-updates)) the app resolves the
newest non-prerelease release, reads its `latest*.yml`, and downloads the
installer (differential via the blockmap when possible). Consequences:

- A release marked *pre-release* is invisible to installed apps.
- The tag must be `v<version>` and the version must be a higher semver than
  the installed one — both are what the workflow produces.
- Deleting `latest*.yml` from a release, or publishing a draft, makes
  clients report *Update check failed* until the next good release.
- The installer file names must stay `Gamma-<version>-<os>-<arch>.<ext>`
  (electron-builder writes those names into the yml).
- macOS reads `latest-mac.yml` and expects the `.zip` target (kept next to
  the `.dmg` for that reason).
