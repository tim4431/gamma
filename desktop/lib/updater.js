// In-app updates, VS Code style: check quietly a little after launch and
// every few hours, download in the background, and from the moment a newer
// version is found offer *Update now*: the shell bar's update icon (and a
// status line + button in the launcher's Settings). Pressed once the
// download is done it restarts into the update; pressed while it
// downloads, it restarts as soon as the download is done. The feed is the GitHub Release itself: electron-builder writes
// `latest.yml` / `latest-mac.yml` (+ installer blockmaps) next to the
// installers and the release workflow uploads them; electron-updater reads
// the newest non-prerelease `v<version>` tag from `publish` in
// electron-builder.cjs (baked into resources/app-update.yml).
//
// macOS: Squirrel.Mac refuses to install into an unsigned app ("Could not
// get code signature for running application"), so while the mac builds are
// unsigned the shell only NOTIFIES and hands the user the release page.
// Flip IN_APP_INSTALL to `true` for darwin once the release workflow signs
// and notarizes (docs/release.md); Windows NSIS updates work unsigned
// (electron-updater only verifies a publisher when one is configured).
// Linux (.deb): resources/package-type says `deb`, so electron-updater uses
// its DebUpdater — the new .deb downloads in the background and *Update
// now* installs it with `dpkg -i` through pkexec (a password prompt),
// then relaunches. Reads `latest-linux.yml`.
//
// Nothing here runs in dev (`app.isPackaged` false: electron-updater needs
// the packaged app-update.yml), under the test harness, or in a Microsoft
// Store install (`process.windowsStore`: the Store delivers updates and
// there is no installer for electron-updater to run). Nor in the Mac App
// Store build (`process.mas`), which may not update any other way than
// through the App Store (App Review 2.4.5) and is never told about a
// GitHub release.

const { app } = require('electron');

const REPO_URL = 'https://github.com/tim4431/Gamma';
const CHECK_DELAY_MS = 15_000;
const CHECK_EVERY_MS = 4 * 60 * 60 * 1000;
const IN_APP_INSTALL = process.platform !== 'darwin';

let state = {
  // idle | unsupported | checking | up-to-date | available | downloading | downloaded | error
  status: 'idle',
  current: app.getVersion(),
  version: null, // the newer version once known
  percent: 0,
  error: null,
  installing: false, // Update now pressed while downloading: restart once it is done
  inApp: IN_APP_INSTALL, // false = "available" means: open the download page
  releaseUrl: REPO_URL + '/releases/latest',
  line: '', // what an update found offers, in words (lineOf)
};
let onChange = () => {};
let openExternal = () => {};
let updater = null;

// The one sentence every surface says of an update found (the bar's icon's
// title, the launcher's Updates row, Help → Check for Updates…'s box), ""
// while none is.
function lineOf(s) {
  const found = `Gamma ${s.version}`;
  if (s.status === 'available') return `${found} is available. This build cannot update itself; the download page opens in your browser.`;
  if (s.status === 'downloading' && s.installing) return `${found} is downloading (${s.percent || 0}%); Gamma restarts into it once it is done.`;
  if (s.status === 'downloading') return `${found} is downloading (${s.percent || 0}%). Update now restarts Gamma into it once it is done.`;
  if (s.status === 'downloaded') return `${found} is ready. Update now restarts Gamma into it; otherwise it installs when Gamma quits.`;
  return '';
}

function set(patch) {
  state = { ...state, ...patch };
  state.line = lineOf(state);
  onChange(state);
}

function releaseUrlFor(version) {
  return version ? `${REPO_URL}/releases/tag/v${version}` : REPO_URL + '/releases/latest';
}

function init(opts = {}) {
  onChange = opts.onChange || onChange;
  openExternal = opts.openExternal || openExternal;
  if (process.env.GAMMA_SHELL_TEST || process.env.GAMMA_SHELL_NO_UPDATE) {
    set({ status: 'unsupported', error: 'disabled' });
    return;
  }
  if (!app.isPackaged) {
    set({ status: 'unsupported', error: 'dev build' });
    return;
  }
  if (process.windowsStore) {
    set({ status: 'unsupported', error: 'store' });
    return;
  }
  if (process.mas) {
    set({ status: 'unsupported', error: 'mas' });
    return;
  }
  let mod;
  try {
    mod = require('electron-updater');
  } catch (e) {
    set({ status: 'unsupported', error: String((e && e.message) || e) });
    return;
  }
  updater = mod.autoUpdater;
  updater.autoDownload = IN_APP_INSTALL;
  updater.autoInstallOnAppQuit = IN_APP_INSTALL;
  updater.allowPrerelease = false;
  updater.on('checking-for-update', () => set({ status: 'checking', error: null }));
  updater.on('update-available', (info) =>
    set({
      status: IN_APP_INSTALL ? 'downloading' : 'available',
      version: info.version,
      percent: 0,
      releaseUrl: releaseUrlFor(info.version),
    })
  );
  updater.on('update-not-available', () => set({ status: 'up-to-date', version: null, checkedAt: Date.now() }));
  updater.on('download-progress', (p) => set({ status: 'downloading', percent: Math.round(p.percent || 0) }));
  updater.on('update-downloaded', (info) => {
    set({ status: 'downloaded', version: info.version, percent: 100 });
    if (state.installing) restart();
  });
  updater.on('error', (e) => {
    // A failed check (offline, GitHub down) is not worth a dialog: the
    // launcher's Updates row shows it and the next periodic check retries.
    // A download that failed after Update now drops the press with it.
    const msg = String((e && e.message) || e).split('\n')[0];
    set({ status: state.status === 'downloaded' ? 'downloaded' : 'error', error: msg, installing: false });
  });
  const t1 = setTimeout(check, CHECK_DELAY_MS);
  const t2 = setInterval(check, CHECK_EVERY_MS);
  if (t1.unref) t1.unref();
  if (t2.unref) t2.unref();
}

// Runs a check; resolves with the state once the check itself finished
// (a download, when one starts, continues in the background).
async function check() {
  if (!updater) return state;
  if (state.status === 'checking' || state.status === 'downloading' || state.status === 'downloaded') return state;
  try {
    await updater.checkForUpdates();
  } catch {
    // reported through the 'error' event
  }
  return state;
}

function restart() {
  setImmediate(() => updater.quitAndInstall(true, true));
}

// The one user action, Update now: restart into the downloaded update; while
// it still downloads, note the press and restart once it is done; on macOS
// (no in-app install) open the release page. Returns what it did.
function install() {
  if (state.status === 'downloaded' && updater && IN_APP_INSTALL) {
    restart();
    return 'restart';
  }
  if (state.status === 'downloading') {
    if (!state.installing) set({ installing: true });
    return 'queued';
  }
  if (state.status === 'available' || state.status === 'downloaded') {
    openExternal(state.releaseUrl);
    return 'browser';
  }
  return 'noop';
}

module.exports = { init, check, install, state: () => state };
