// electron-builder config. Lives here instead of package.json "build" so the
// signing pieces can depend on the environment: everything is UNSIGNED by
// default (local `npm run dist`, forks, PRs) and switches on only when the
// release workflow injects the credentials — see .github/workflows/release.yml
// and docs/release.md for the secret names.
//
// Windows: Azure Trusted Signing (electron-builder's azureSignOptions; the
//   TrustedSigning PowerShell module signs the exe + NSIS installer, auth via
//   the AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET env vars).
// macOS: Developer ID Application cert via CSC_LINK / CSC_KEY_PASSWORD
//   (electron-builder's own convention), hardened runtime + the entitlements
//   the Electron JIT and the PyInstaller sidecar need, then notarization with
//   an App Store Connect app-specific password. osx-sign walks the whole
//   .app, so the frozen backend under Contents/Resources/gamma-server is
//   signed with the same identity — nothing to configure per binary.

const env = process.env;
const has = (...keys) => keys.every((k) => env[k] && env[k].trim());

const azureSigning = has('AZURE_TENANT_ID', 'AZURE_CLIENT_ID', 'AZURE_CLIENT_SECRET', 'AZURE_SIGN_ENDPOINT', 'AZURE_SIGN_ACCOUNT', 'AZURE_SIGN_PROFILE');
const notarize = has('APPLE_ID', 'APPLE_APP_SPECIFIC_PASSWORD', 'APPLE_TEAM_ID');

module.exports = {
  appId: 'io.github.tim4431.gamma',
  productName: 'Gamma',
  artifactName: 'Gamma-${version}-${os}-${arch}.${ext}',
  // The in-app updater's feed (lib/updater.js): electron-builder bakes this
  // into resources/app-update.yml and writes latest.yml / latest-mac.yml +
  // blockmaps next to the installers — the release workflow uploads them.
  // Nothing is ever published from here (the workflow runs --publish never
  // and creates the GitHub Release itself).
  publish: { provider: 'github', owner: 'tim4431', repo: 'Gamma', releaseType: 'release' },
  directories: { output: 'dist' },
  files: ['main.js', 'preload.js', 'lib/**', 'ui/**', 'build/icon.png', 'package.json'],
  extraResources: [{ from: 'dist-backend/gamma-server', to: 'gamma-server' }],

  win: {
    target: ['nsis'],
    ...(azureSigning
      ? {
          azureSignOptions: {
            endpoint: env.AZURE_SIGN_ENDPOINT, // e.g. https://eus.codesigning.azure.net
            codeSigningAccountName: env.AZURE_SIGN_ACCOUNT,
            certificateProfileName: env.AZURE_SIGN_PROFILE,
            // Optional: the certificate subject ("CN=..."); NSIS verifies the
            // signed uninstaller against it when given.
            ...(has('AZURE_SIGN_PUBLISHER') ? { publisherName: env.AZURE_SIGN_PUBLISHER } : {}),
          },
        }
      : {}),
  },
  nsis: {
    oneClick: false,
    allowToChangeInstallationDirectory: true,
  },

  mac: {
    target: ['dmg', 'zip'],
    category: 'public.app-category.productivity',
    hardenedRuntime: true,
    entitlements: 'build/entitlements.mac.plist',
    entitlementsInherit: 'build/entitlements.mac.plist',
    gatekeeperAssess: false,
    notarize,
  },
};
