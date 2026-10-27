// electron-builder config of the Mac App Store build (docs/release.md, "Mac
// App Store"): the same app as the dmg/zip, packaged for Apple's store.
// Made on a Mac whose keychain holds the App Store team's certificates,
// never by the release workflow:
//
//   npm run dist:mas      the signed .pkg for App Store Connect (dist-mas/)
//   npm run pack:mas-dev  a development-signed Gamma.app to try on a Mac the
//                         development profile lists (dist-mas/mas-dev-*)
//
// What makes it a Store build, beside the base config it extends:
// - the `mas` targets, for which electron-builder fetches Electron's Mac App
//   Store runtime (`process.mas` at run time: no updater, no login item,
//   folder access through bookmarks, lib/folderAccess.js);
// - the App Sandbox: assets/entitlements.mas.plist for the app,
//   assets/entitlements.mas.inherit.plist for every other binary, Electron's
//   helpers and the frozen server under Resources/gamma-server alike, so the
//   server runs inside the app's sandbox;
// - the bundle id of the App Store Connect record the iPad app is listed
//   under, so the two share one listing, and that team's id;
// - the versions App Store Connect counts, from the environment:
//   GAMMA_MAS_VERSION (CFBundleShortVersionString, also what About and the
//   server report) must be higher than the last one approved, and
//   GAMMA_MAS_BUILD (CFBundleVersion) higher than every build uploaded;
// - the provisioning profile, GAMMA_MAS_PROFILE: the Mac App Store profile
//   for dist:mas, a development profile for pack:mas-dev. The certificates
//   come from the keychain (Apple Distribution and Mac Installer
//   Distribution, or Apple Development); CSC_NAME picks among several.
//
// The npm scripts set umask 022 and scripts/mas-modes.cjs opens up the file
// modes, since the store installs the .pkg's files as they are packed.

const base = require('./electron-builder.cjs');

const APP_ID = 'net.blitzbuild.gamma'; // App Store Connect: "Gamma - PDF Reader"
const TEAM_ID = 'A436A36DDC';

function required(name, pattern, what) {
  const value = (process.env[name] || '').trim();
  if (!pattern.test(value)) throw new Error(`Set ${name}: ${what}.`);
  return value;
}

const version = required('GAMMA_MAS_VERSION', /^\d+\.\d+(\.\d+)?$/, 'the App Store version, e.g. 1.1, higher than the last one approved');
const build = required('GAMMA_MAS_BUILD', /^\d+$/, 'the build number, higher than every build uploaded before');
const profile = required('GAMMA_MAS_PROFILE', /\.provisionprofile$/, 'the path of the provisioning profile (.provisionprofile)');

module.exports = {
  ...base,
  appId: APP_ID,
  artifactName: 'Gamma-${version}-mas-${arch}.${ext}',
  directories: { ...base.directories, output: 'dist-mas' },
  // package.json's version is a floor the release workflow pins; electron-builder needs semver.
  extraMetadata: { version: version.split('.').length === 2 ? `${version}.0` : version },
  buildVersion: build,
  publish: null, // no update feed: the App Store delivers updates
  afterPack: 'scripts/mas-modes.cjs',
  forceCodeSigning: true,
  mac: {
    ...base.mac,
    hardenedRuntime: false,
    notarize: false,
    extendInfo: {
      CFBundleShortVersionString: version,
      ElectronTeamID: TEAM_ID,
      // Recording audio into a note, and dictation in the chat.
      NSMicrophoneUsageDescription: 'Gamma uses the microphone when you record audio into a note or dictate to the assistant.',
      // HTTPS only, as the iPad app declares (ipad/project.yml).
      ITSAppUsesNonExemptEncryption: false,
    },
  },
  mas: {
    type: 'distribution',
    hardenedRuntime: false,
    entitlements: 'assets/entitlements.mas.plist',
    entitlementsInherit: 'assets/entitlements.mas.inherit.plist',
    provisioningProfile: profile,
  },
  masDev: {
    type: 'development',
  },
};
