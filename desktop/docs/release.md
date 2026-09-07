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
- **Microsoft Store**: an MSIX the Store signs with Microsoft's certificate
  — no SmartScreen, and the Store handles updates. Set up and wired in, see
  *Microsoft Store* below.
- **Homebrew cask** (macOS) does not help: brew leaves the quarantine
  attribute on; only signing + notarization fixes the *damaged* dialog.

## Microsoft Store

The Store build is the `appx` target in `electron-builder.js`. Its `appx`
block carries the product identity from Partner Center (*Apps and games →
GammaPDF → Product management → Product identity*): `identityName`
(`xwtim.GammaPDF`), `publisher` (`CN=<GUID>`), `publisherDisplayName`
(`xwtim`); `displayName` must equal the name reserved in the Store. A
mismatch in any of them is rejected at upload. The Store ID is
`9N8WGWR2J2MV`.

**Build.** Windows only (electron-builder fetches `makeappx`/`signtool`
itself, no Windows SDK needed):

```powershell
cd desktop
npx electron-builder --win appx --config.directories.output=dist-store
```

It logs *AppX is not signed (Windows Store only build)* and writes
`dist-store/Gamma-<version>-win-x64.appx`. Unsigned is correct: the Store
signs the package, and signing it locally would also change the manifest
publisher to the certificate's subject. Never build it with the `AZURE_*`
signing env present — the release workflow blanks those variables for this
step and uploads the result as the `store-windows` workflow artifact (not a
release asset: nobody can install it before the Store signs it).

**Submit.** Partner Center → the product → *Submissions* → new submission →
*Packages*: upload the `.appx`; fill the listing (screenshots, description),
age rating, free pricing and a privacy-policy URL (mandatory because the
app uses the network). The package version must increase per submission
(`package.json` `0.2.0` becomes `0.2.0.0`; the Store requires the fourth
part to be 0, which electron-builder guarantees). Certification takes one
to three days; the reviewer launches the app, so a fresh install must reach
the launcher with no workspace configured.

**Runtime differences of the Store install:**

- `process.windowsStore` is true; `lib/updater.js` reports `unsupported`
  (reason `store`) and the launcher / *Help → Check for Updates…* say the
  Store delivers updates. `electron-updater` is never initialized.
- MSIX virtualizes AppData: Electron's userData (the registry and every
  local workspace's data dir under it, see
  [architecture.md](architecture.md#shell-state)) lands in
  `%LOCALAPPDATA%\Packages\xwtim.GammaPDF_<hash>\LocalCache\Roaming\gamma-desktop`.
  Consequences: a Store install and an NSIS install never see each other's
  workspaces, and **uninstalling the Store app deletes that folder**,
  including local workspaces' PDFs and databases. The launcher's *Local
  workspace storage* setting is the way out: pick a folder outside the
  package (e.g. under *Documents*) and *Move data* relocates the existing
  workspaces there ([architecture.md](architecture.md#shell-state)).
- The install directory (`C:\Program Files\WindowsApps\…`) is read-only.
  Fine for the onedir sidecar, which writes only to `GAMMA_DATA_DIR`.
  Loopback to `127.0.0.1` is unrestricted for full-trust packaged apps, so
  the sidecar and the browser extension work as usual.

**Local install test.** An unsigned MSIX cannot be installed. Sign it with a
self-signed certificate whose subject is exactly the `appx.publisher` GUID
(once, elevated PowerShell; then turn on *Settings → System → For
developers → Developer Mode*):

```powershell
$c = New-SelfSignedCertificate -Type Custom -Subject "CN=2641C414-B740-42FF-BD24-6552C33A850A" `
  -KeyUsage DigitalSignature -FriendlyName "Gamma Store dev" -CertStoreLocation Cert:\CurrentUser\My `
  -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
$pw = ConvertTo-SecureString "dev" -AsPlainText -Force
Export-PfxCertificate -Cert $c -FilePath dev.pfx -Password $pw
Export-Certificate -Cert $c -FilePath dev.cer
Import-Certificate -FilePath dev.cer -CertStoreLocation Cert:\LocalMachine\TrustedPeople
```

Per build (`signtool` is in the Windows SDK, or under
`~\AppData\Local\electron-builder\Cache\winCodeSign\…\windows-10\x64`):

```powershell
signtool sign /fd SHA256 /f dev.pfx /p dev dist-store\Gamma-0.2.0-win-x64.appx
Add-AppxPackage dist-store\Gamma-0.2.0-win-x64.appx
Get-AppxPackage *GammaPDF* | Remove-AppxPackage   # before re-installing the same version
```

Do not point `CSC_LINK` at the dev pfx instead: electron-builder would
then sign the NSIS build with the throwaway certificate too.

Automating the upload later: the `msstore` CLI or the
`microsoft/store-submission` action, both driven by an Entra app
registration linked to the Partner Center account.

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
