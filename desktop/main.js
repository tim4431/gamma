// Gamma desktop shell. The app itself is untouched Gamma: a server is a
// local sidecar or a remote URL, and opening one navigates the content view
// to it — the frontend always loads from the server it talks to, so there is
// no version skew and no API-base plumbing. Gamma's own workspaces (the
// libraries inside a server) are switched from the same shell-bar menu: the
// shell reads them off `/api/session` with the page's cookies and navigates
// to `?ws=<id>`.
//
// Window layout (Electron BaseWindow + two WebContentsViews):
//
//   ┌──────────────────────────────────────────────────┐
//   │ shell bar (ui/bar.html, 38px, doubles as the     │  ← shell chrome:
//   │ title bar: workspace switcher + status)           │    file:// page
//   ├──────────────────────────────────────────────────┤
//   │ content view: the launcher (file://) or the       │  ← Gamma, black box
//   │ workspace's own Gamma frontend (http://…)         │
//   └──────────────────────────────────────────────────┘
//
// The shell owns only its chrome (bar + launcher), the server registry and
// sidecar lifecycles. It reads two things off Gamma: the `data-theme`
// attribute the preload mirrors so the chrome paints in the same theme, and
// `/api/session` (public HTTP API) for the workspace list.

const { app, BaseWindow, WebContentsView, Menu, Tray, Notification, clipboard, nativeImage, nativeTheme, session, shell, ipcMain, net, dialog, systemPreferences } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const crypto = require('crypto');
const { pathToFileURL } = require('url');

const SMOKE = process.argv.includes('--smoke');
// Under the test scripts the window opens minimized and stays down, so a run
// leaves the screen and the keyboard to whoever sits at the machine
// (test/e2e.js, test/security.e2e.js and test/smoke.js through test/quiet.js;
// tools/headed.cjs decides).
const TEST_MINIMIZED = Boolean(process.env.GAMMA_SHELL_MINIMIZED && (process.env.GAMMA_SHELL_TEST || SMOKE));
// Electron shows an error nothing caught in a message box, which would wait
// on the screen: such a run fails at once instead. Before the requires
// below, which can throw too.
if (TEST_MINIMIZED) {
  process.on('uncaughtException', (e) => {
    console.error(`[shell] uncaught: ${(e && e.stack) || e}`);
    app.exit(1);
  });
}

const registry = require('./lib/registry');
const sidecar = require('./lib/sidecar');
const updater = require('./lib/updater');
const startup = require('./lib/startup');
const keeping = require('./lib/keeping');
const cloudSignIn = require('./lib/cloudSignIn');
const openFiles = require('./lib/openFiles');
const { zipNames } = require('./lib/zipNames');
const { loadPage } = require('./lib/loadPage');
const { hostPath, goesWithPackage } = require('./lib/hostPath');
const folderAccessFor = require('./lib/folderAccess');
const microphone = require('./lib/microphone');
const permissions = require('./lib/permissions');
const agentAsks = require('./lib/agentAsks');
const fileClipboard = require('./lib/fileClipboard');
const { pages: shellPages, isShellPage, trustedShellSender } = require('./lib/shellSecurity');
const windowControls = require('./lib/windowControls');
const terminal = require('./lib/terminal');
const certs = require('./lib/certs');
const hostScope = require('./lib/hostScope');
const addresses = require('./lib/addresses');
const vaults = require('./lib/vaults');
const thisComputer = require('./lib/thisComputer');
const syncPanel = require('./lib/syncPanel');
const agentAskBar = require('./lib/agentAskBar');
const systemFiles = require('./lib/systemFiles');
const openWithFor = require('./lib/openWith');

const BAR_H = 38;
// The shell's word to its local servers on the routes that take a path on
// this computer (X-Gamma-Shell; lib/sidecar.js hands it to each server as
// GAMMA_SHELL_SECRET): made anew at each start and held only here, so a
// page of Gamma, which shares its server's origin and session, cannot name
// a path itself.
const SHELL_SECRET = crypto.randomBytes(32).toString('hex');
// The Store's own updates page (Library → Get updates); needs no product id.
const STORE_UPDATES_URL = 'ms-windows-store://downloadsandupdates';

// Tests point the shell at a throwaway profile so the real registry, cookies
// and sidecars are never touched.
if (process.env.GAMMA_SHELL_USER_DATA) app.setPath('userData', process.env.GAMMA_SHELL_USER_DATA);

// The files and folders this start names (lib/openFiles.js: the user's
// arguments that exist, as absolute paths), read before the lock so that a
// second start hands them over already parsed.
const handedOver = openFiles.openedPaths(openFiles.launchArgs(process.argv, process.defaultApp), process.cwd()).map((i) => i.path);

// One app per profile: a second one would run a second server on the same
// data directories. A second start (a file or a folder opened from the file
// manager, the Start menu while the app sits in the tray) hands what it
// names to this one (`additionalData`, read by `second-instance` below) and
// quits. The smoke test runs beside it. The Mac App Store build takes no
// lock (the submitted build found Electron's unusable in the sandbox):
// Launch Services already runs one copy of a store app, handing a PDF
// opened from the Finder over as `open-file`.
const primary = SMOKE || process.mas || app.requestSingleInstanceLock({ paths: handedOver });
if (!primary) app.quit();

// The Mac App Store build's folders outside its sandbox (lib/folderAccess.js),
// set up at launch; null in every other build.
let folderAccess = null;

// Title-bar palette per Gamma theme: the shell bar's chrome, Gamma's
// --bg-page in ui/tokens.css (frontend/tests/themes.test.mjs checks it), and
// a text colour for the window controls. '' is the fallback for a theme
// name this build does not know; what the chrome paints before a page has
// reported one is `currentTheme()`.
const THEMES = {
  '': { bg: '#111111', symbol: '#dddddd' },
  dark: { bg: '#111111', symbol: '#dddddd' },
  light: { bg: '#f5f5f5', symbol: '#333333' },
  'gamma-light': { bg: '#e6e5e0', symbol: '#44423c' },
  'gamma-dark': { bg: '#1b1b19', symbol: '#ded9cd' },
  sepia: { bg: '#f0e9d6', symbol: '#073642' },
  solarized: { bg: '#eee8d5', symbol: '#657b83' },
  gray: { bg: '#e9e9e9', symbol: '#2d2d2d' },
};

let win = null;
let bar = null; // shell bar view
let content = null; // Gamma / launcher view
// Linux: the ends of the bar the window controls take, 'left', 'right',
// 'both' or 'none' (lib/windowControls); the bar keeps them clear.
let wcoSides = 'right';
let current = null; // { id, name, type, url } while a server is open
let busy = null; // status text while a server is starting
// Gamma's workspaces on the open server: { list, current, user } or null.
let gamma = null;
let gammaFetch = null; // in-flight /api/session read
let theme = ''; // last data-theme the content page reported
let barExpanded = false;
// Origins the content view may navigate to (registered servers). Anything
// else is handed to the system browser.
const allowedOrigins = new Set();
// Test hook: records what would have opened externally.
const externalOpens = [];
const errorBoxes = []; // under GAMMA_SHELL_TEST: each step of the suite asks for none
let tray = null; // the tray icon while the app keeps running in the background
let notice = null; // a short line in the bar after a shell action ("Quantum is being written to …")
let noticeTimer = null;

// Remote reachability: a cached `/api/health` probe per remote server so
// the launcher and the bar menu can show a dot like the local running one
// (Gamma's health endpoint is public, no session needed). Probes run on
// demand — launcher refresh, bar menu open — behind a short TTL, and their
// results land asynchronously through pushState.
const remoteHealth = new Map(); // id -> { ok: boolean, at: ms }
const remoteProbes = new Map(); // id -> in-flight promise
const HEALTH_TTL_MS = 20_000;
const HEALTH_TIMEOUT_MS = 5_000;

function probeRemotes(force) {
  for (const ws of registry.load().servers) {
    if (ws.type !== 'remote' || remoteProbes.has(ws.id)) continue;
    const cached = remoteHealth.get(ws.id);
    if (!force && cached && Date.now() - cached.at < HEALTH_TTL_MS) continue;
    let target;
    try {
      target = new URL('/api/health', ws.url).href;
    } catch {
      continue;
    }
    const p = net
      .fetch(target, { signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS), cache: 'no-store' })
      .then((r) => r.ok, () => false)
      .then((ok) => remoteHealth.set(ws.id, { ok, at: Date.now() }))
      .finally(() => {
        remoteProbes.delete(ws.id);
        pushState();
      });
    remoteProbes.set(ws.id, p);
  }
}

// true / false once probed, null while unknown, undefined for local ones.
function remoteReachable(ws) {
  if (ws.type !== 'remote') return undefined;
  const h = remoteHealth.get(ws.id);
  return h ? h.ok : null;
}

function appInfo() {
  return {
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    userDataDir: app.getPath('userData'),
    version: app.getVersion(), // the sidecar reports it as its build (GAMMA_VERSION)
    // who installs the next release (GAMMA_UPDATES)
    updates: process.mas ? 'mas' : process.windowsStore ? 'store' : 'desktop',
    access: folderAccess ? folderAccess.generation() : 0, // the folder grants a server starting now inherits
    shellSecret: SHELL_SECRET,
  };
}

// What the chrome paints in: the theme the open page reports, else the one
// it reported last run (no flash while the page loads), else the OS scheme —
// Gamma's own default is System, and the launcher reports no theme at all,
// so a light machine must never get a dark window.
function currentTheme() {
  return theme || registry.getSettings().lastTheme || (nativeTheme.shouldUseDarkColors ? 'dark' : 'light');
}

function palette() {
  return THEMES[currentTheme()] || THEMES[''];
}

// ---------------------------------------------------------------- window ----

function layout() {
  if (!win) return;
  let [w, h] = win.getContentSize();
  // Minimized, the views keep the size the window comes back at (on Windows
  // its content shrinks to nothing meanwhile); the window is frameless, so
  // its bounds are its content.
  if (win.isMinimized()) ({ width: w, height: h } = win.getNormalBounds());
  // The bar's dropdown needs room below the strip: while a menu is open the
  // bar view grows over the content (its page is transparent outside the
  // strip and the menu, and a click there closes the menu). While an
  // agent's question is up (shownKey) the bar takes the whole window and
  // the content view is hidden: its page is neither seen nor clicked.
  const barH = agentAskBar.shownKey ? h : barExpanded ? Math.min(h, BAR_H + 420) : BAR_H;
  bar.setBounds({ x: 0, y: 0, width: w, height: barH });
  content.setBounds({ x: 0, y: BAR_H, width: w, height: Math.max(0, h - BAR_H) });
  content.setVisible(!agentAskBar.shownKey);
  if (TEST_MINIMIZED) for (const v of [bar, content]) emulateViewSize(v);
}

// A page takes its size from the window's client area, of which a minimized
// window on Windows has none: under the test scripts' minimized window each
// page is given its view's size (again with every page, a new one may come
// in a renderer of its own). Only once a view has a page: before its first
// navigation there is no renderer, and Electron crashes on the call.
const sizedViews = new WeakSet();
function emulateViewSize(view) {
  if (!sizedViews.has(view) || view.webContents.isDestroyed()) return;
  const { width, height } = view.getBounds();
  view.webContents.enableDeviceEmulation({
    screenPosition: 'desktop', screenSize: { width, height }, viewPosition: { x: 0, y: 0 },
    deviceScaleFactor: 0, viewSize: { width, height }, scale: 1,
  });
}

// The OS switched light/dark: only the chrome that follows it is repainted
// (a reported or remembered theme stays put).
nativeTheme.on('updated', () => {
  if (theme || registry.getSettings().lastTheme) return;
  applyTheme();
  pushState();
});

function applyTheme() {
  if (!win) return;
  const p = palette();
  win.setBackgroundColor(p.bg);
  // what the content view shows between one page going and the next painting
  if (content) content.setBackgroundColor(p.bg);
  if (process.platform !== 'darwin') {
    try {
      win.setTitleBarOverlay({ color: p.bg, symbolColor: p.symbol, height: BAR_H });
    } catch {}
  }
}

// The desktop's title-bar layout, pushed to the bar when it changes.
function readWindowControls() {
  windowControls.read(app.getLocale()).then((sides) => {
    if (sides === wcoSides) return;
    wcoSides = sides;
    pushState();
  }, () => {});
}

// `inactive`: made for an agent's question (surfaceForAsk), shown without
// taking the focus, so a key or a click meant for another app reaches no
// button of it.
function createWindow({ inactive = false } = {}) {
  const saved = registry.getWindowBounds();
  const p = palette();
  // a card the window shows again is armed afresh, once the new bar drew it
  agentAskBar.redrawCard();
  win = new BaseWindow({
    show: !TEST_MINIMIZED && !inactive,
    width: 1360,
    height: 900,
    minWidth: 720,
    minHeight: 480,
    ...(saved && saved.width > 400 && saved.height > 300 ? saved : {}),
    title: 'Gamma',
    icon: path.join(__dirname, 'assets', 'icon.png'),
    backgroundColor: p.bg,
    // The shell bar is the title bar: frameless with the OS window controls
    // overlaid (Windows/Linux) or the traffic lights inset (macOS).
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'hidden',
    ...(process.platform === 'darwin'
      ? { trafficLightPosition: { x: 12, y: 11 } }
      : { titleBarOverlay: { color: p.bg, symbolColor: p.symbol, height: BAR_H } }),
    // Native menu stays for its accelerators; Alt reveals it on Windows.
    autoHideMenuBar: true,
  });
  if (TEST_MINIMIZED) {
    // First: on Windows, minimizing a window never shown takes the foreground.
    // It also drops the taskbar button there.
    if (process.platform === 'win32') win.setFocusable(false);
    win.minimize();
  } else if (saved && saved.maximized) win.maximize();
  if (inactive && !TEST_MINIMIZED) win.showInactive();

  const webPreferences = {
    preload: path.join(__dirname, 'preload.js'),
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    additionalArguments: [`--gamma-shell-pages=${JSON.stringify(Object.values(shellPages))}`],
  };
  bar = new WebContentsView({ webPreferences });
  bar.setBackgroundColor('#00000000');
  content = new WebContentsView({ webPreferences });
  content.setBackgroundColor(p.bg);
  win.contentView.addChildView(content);
  win.contentView.addChildView(bar); // on top, so the expanded menu overlays
  layout();
  applyTheme(); // BaseWindow does not keep the constructor's backgroundColor
  win.on('resize', layout);
  win.on('maximize', layout);
  win.on('unmaximize', layout);
  win.on('minimize', layout);
  win.on('restore', layout);
  if (process.platform === 'linux') {
    readWindowControls();
    win.on('focus', readWindowControls);
  }
  win.on('focus', thisComputer.hintServers);
  // An agent's question (lib/agentAskBar.js startArming): the buttons arm
  // after a quiet, focused second, counted again on a focus, a resize or a
  // move; a blur disarms them. The focus also ends the flashing that asked
  // for it.
  win.on('focus', agentAskBar.windowFocused);
  win.on('resize', agentAskBar.rearm);
  win.on('move', agentAskBar.rearm);
  win.on('blur', agentAskBar.windowBlurred);
  if (TEST_MINIMIZED) {
    for (const v of [bar, content]) v.webContents.on('did-navigate', () => { sizedViews.add(v); emulateViewSize(v); });
  }

  bar.webContents.loadFile(path.join(__dirname, 'ui', 'bar.html'));
  bar.webContents.on('did-finish-load', pushState);
  const guardBar = (event, url) => { if (!isShellPage(url, 'bar')) event.preventDefault(); };
  bar.webContents.on('will-navigate', guardBar);
  bar.webContents.on('will-redirect', guardBar);
  bar.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  // Each press, key and wheel the bar is given, before the page sees it: one
  // before the buttons are armed counts the quiet second again, one after
  // is the press an answer needs (answerAgentAsk).
  bar.webContents.on('input-event', (_e, input) => agentAskBar.barInput(input && input.type));

  const cwc = content.webContents;
  // The address the main frame's navigation is at now (its start, then each
  // redirect): where a redirect comes from.
  let navHop = '';
  cwc.on('did-start-navigation', (_e, url, inPlace, mainFrame) => { if (mainFrame && !inPlace) navHop = url; });
  cwc.on('did-redirect-navigation', (_e, url, _inPlace, mainFrame) => { if (mainFrame) navHop = url; });
  const guard = (event, url, redirect = false) => {
    let origin = null;
    try {
      origin = new URL(url).origin;
    } catch {}
    // An address the links moved away from: the same path at the current one.
    const moved = addresses.rerouted(url);
    if (moved) {
      event.preventDefault();
      cwc.loadURL(moved).catch(() => {});
      return;
    }
    // A page that is not this computer's (a NAS's, any remote server's)
    // never takes the window to one of this computer's servers: a host page
    // it placed under the cursor would answer as the host's signed-in
    // window. Nothing is opened for it. The shell's own loads emit no
    // will-navigate, so opening a local server is not this; a redirect is
    // judged by the address it comes from, not by the page shown or who
    // started the load: a local server's redirect within itself passes, a
    // remote server's to a local one (opened from the launcher, or a
    // Reload) never does.
    if (origin && isLocalOrigin(origin) && !isLocalPage(redirect ? navHop : cwc.getURL())) {
      event.preventDefault();
      return;
    }
    // The page's Gamma Cloud sign-in runs in the system browser and comes
    // back here (lib/cloudSignIn.js).
    if (origin && allowedOrigins.has(origin) && cloudSignIn.isStart(url)) {
      event.preventDefault();
      signInWithCloud(url);
      return;
    }
    if (isShellPage(url, 'launcher') || (origin && allowedOrigins.has(origin))) return;
    event.preventDefault();
    // a remote's http:// address sent to https:// on its host: the launcher's card offers the move
    if (redirect && event.isMainFrame !== false && addresses.sentToHttpsBy(url)) return;
    if (/^https?:/.test(url) || url === STORE_UPDATES_URL) openExternal(url);
  };
  cwc.on('will-navigate', guard);
  cwc.on('will-redirect', (event, url) => guard(event, url, true));
  // target=_blank (external link chips, share links, papers opened in a new
  // tab) goes to the system browser; the Store's updates page (Settings →
  // Server in a Store install) to the Store.
  cwc.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url) || url === STORE_UPDATES_URL) openExternal(url);
    return { action: 'deny' };
  });
  // The frames below the page (lib/permissions.js frameAllowed): under a
  // remote page, web schemes only, never this computer's servers.
  cwc.on('will-frame-navigate', (details) => {
    if (!details.isMainFrame && !permissions.frameAllowed(details.url, cwc.getURL(), isLocalPage)) details.preventDefault();
  });
  // A remote page's Bluetooth chooser is cancelled (Electron otherwise picks
  // the first device); a page of this computer's keeps what it had.
  cwc.on('select-bluetooth-device', (event, _devices, callback) => {
    if (isLocalPage(cwc.getURL())) return;
    event.preventDefault();
    callback('');
  });
  // The window's title is the page's, but 'Gamma' while an agent's question
  // hides the page; the page's comes back after.
  cwc.on('page-title-updated', (_e, title) => agentAskBar.pageTitled(title));
  cwc.on('did-navigate', (_e, url) => {
    // a page that landed at a moved-away address all the same (a history step) is replaced
    const moved = addresses.rerouted(url);
    if (moved) {
      cwc.loadURL(moved).catch(() => {});
      return;
    }
    addresses.sawHttps(url);
    pushState();
    refreshGamma();
  });
  cwc.on('did-navigate-in-page', () => { pushState(); refreshGamma(); });
  cwc.on('did-finish-load', refreshGamma);
  // (not while the vault dialog is up: setBarExpanded keeps it shown)
  cwc.on('focus', () => {
    if (barExpanded) setBarExpanded(false);
  });

  // Downloads (backup zips, exports) go through the normal save dialog;
  // tests get them saved straight into a folder.
  // A download a page starts while an agent's question hides it is cancelled.
  cwc.session.on('will-download', (_e, item) => {
    if (agentAskBar.shownKey) {
      item.cancel();
      return;
    }
    const dir = process.env.GAMMA_SHELL_DOWNLOAD_DIR;
    if (dir) item.setSavePath(path.join(dir, item.getFilename()));
  });

  const self = win;
  win.on('close', () => {
    try {
      registry.setWindowBounds({ ...self.getNormalBounds(), maximized: self.isMaximized() });
    } catch {}
  });
  win.on('closed', () => {
    if (win !== self) return; // a window made since is not this one's to clear
    // The views go with the window (they would otherwise live on, detached),
    // and nothing is open any more: a window shown later loads afresh.
    for (const v of [bar, content]) {
      try {
        if (v && !v.webContents.isDestroyed()) v.webContents.close();
      } catch {}
    }
    win = null;
    bar = null;
    content = null;
    current = null;
    gamma = null;
    barExpanded = false;
    barAsked = false;
    agentAskBar.redrawCard(); // a card still waiting is drawn again in the next window, armed afresh
    vaults.cancelVaultAsk(); // its panel went with the window
  });
}

function openExternal(url) {
  externalOpens.push(url);
  if (!process.env.GAMMA_SHELL_TEST) shell.openExternal(url);
}

// A directory in the system's file manager; under the test harness only
// recorded, like the external links. hostPath: where a Store install's
// virtualized AppData really is, for the file manager outside the package.
function openDirectory(p) {
  externalOpens.push(p);
  if (!process.env.GAMMA_SHELL_TEST) shell.openPath(hostPath(p));
}

// A local server's log, where lib/sidecar.js writes it.
const logPath = (id) => path.join(app.getPath('userData'), 'logs', `${id}.log`);

// A file shown in its directory in the system's file manager; under the
// test harness only recorded, like openDirectory.
function revealFile(p) {
  externalOpens.push(p);
  if (!process.env.GAMMA_SHELL_TEST) shell.showItemInFolder(hostPath(p));
}

// Text on the clipboard; under the test harness only recorded, so a run
// leaves the developer's clipboard alone. The write is asynchronous, and a
// refused one is dropped rather than left as an unhandled rejection.
let copied = '';
function copyText(text) {
  copied = text;
  if (!process.env.GAMMA_SHELL_TEST) Promise.resolve(clipboard.writeText(text)).catch(() => {});
}

// A file on the clipboard, as a file manager's Copy leaves it
// (lib/fileClipboard.js); recorded alone under the test harness, as copyText.
let copiedFile = '';
function copyFile(file) {
  copiedFile = file;
  return process.env.GAMMA_SHELL_TEST ? Promise.resolve() : fileClipboard.putFile(file, { clipboard });
}

// The bar view over the content while a dropdown is open. The vault dialog
// (askVault) keeps it expanded until it is answered: a click in the page
// meanwhile (a look at the folder) must not hide a dialog that still waits,
// so only its answer or its cancel (answerVault) shrinks the bar. An
// agent's question (shownKey) keeps it so too, over the whole window, and
// once it goes the bar is as the bar last asked (`barAsked`: a dropdown
// open under the card stays open).
let barAsked = false;
function setBarExpanded(on) {
  barAsked = Boolean(on);
  barExpanded = barAsked || Boolean(vaults.vaultAsk) || Boolean(agentAskBar.shownKey);
  layout();
}

// The launcher's `?error=` payload: a JSON object when the failure came with a
// diagnosis (lib/startup.js — a local server that would not come up), so the
// page can explain it and offer the fix; a plain message otherwise. A
// remote whose certificate the window refused and which may be trusted by
// its fingerprint gets the card of it (certCard); one that does not speak
// HTTPS, or whose http:// address sent the window to https:// (sentToHttps,
// taken once), its wording by what the shell knows of it
// (startup.diagnoseOpen).
function launcherError(error, serverId) {
  const message = (error && error.message) || error;
  const srv = serverId ? registry.get(serverId) : null;
  let d = error && error.startup;
  if (!d && srv && srv.type === 'remote') {
    const origin = registry.originOf(srv.url);
    const sentTo = addresses.sentToHttps.get(origin) || null;
    addresses.sentToHttps.delete(origin);
    d = (/ERR_CERT_/.test(String(message)) && addresses.certCard(origin, String(message)))
      || startup.diagnoseOpen(message, { url: srv.url, pinned: Boolean(registry.getPins()[origin]), httpsSeen: Boolean(srv.httpsSeen),
        scope: addresses.scopeOf(origin), sentTo });
  }
  return JSON.stringify({ ...(d || startup.diagnoseOpen(message)), server: serverId || '' });
}

function loadLauncher(error, serverId) {
  if (!win) return;
  vaults.cancelVaultAsk();
  current = null;
  gamma = null;
  busy = null;
  // ?theme=: the page's first paint is in the window's theme, not the OS's.
  const q = new URLSearchParams({ theme: currentTheme() });
  if (error) q.set('error', launcherError(error, serverId));
  content.webContents.loadURL(pathToFileURL(path.join(__dirname, 'ui', 'launcher.html')).href + '?' + q);
  win.setTitle('Gamma');
  pushState();
}

// -------------------------------------------------------------- state --------

// The workspace id the content view is showing (its URL's ?ws=), or ''.
function currentWsId() {
  try {
    return new URL(content.webContents.getURL()).searchParams.get('ws') || '';
  } catch {
    return '';
  }
}

// Gamma's workspaces on the open server, read from `/api/session` with the
// content session's cookies (a public API call, nothing injected into the
// page). Anonymous (not signed in) → null. Lands asynchronously via pushState.
function refreshGamma() {
  if (!current || !content || content.webContents.isDestroyed()) return;
  const url = content.webContents.getURL();
  let origin;
  try {
    origin = new URL(url).origin;
  } catch {
    return;
  }
  if (origin !== new URL(current.url).origin) return;
  if (gammaFetch) return;
  const ses = content.webContents.session;
  const server = current;
  gammaFetch = ses
    .fetch(origin + '/api/session', { credentials: 'include', cache: 'no-store', signal: AbortSignal.timeout(5000) })
    .then((r) => (r.ok ? r.json() : null))
    .then(async (j) => {
      gamma = j && j.user
        ? { user: j.user, current: currentWsId() || j.default_workspace || '', list: j.workspaces || [] }
        : null;
      if (gamma && server.type === 'local') await reconcileMirrors(server.id, origin, ses);
    })
    .catch(() => { gamma = null; })
    .finally(() => {
      gammaFetch = null;
      pushState();
    });
}

// A local server's own list of offline copies (`GET /api/mirrors`, the
// signed-in admin's), into the registry's map — copies made or stopped
// from Gamma's Settings are picked up here, so the switcher's cross-links
// stay right. Best effort.
async function reconcileMirrors(serverId, origin, ses) {
  try {
    const r = await ses.fetch(origin + '/api/mirrors', { credentials: 'include', cache: 'no-store', signal: AbortSignal.timeout(5000) });
    if (!r.ok) return;
    const j = await r.json();
    registry.setServerMirrors(serverId, (j.mirrors || []).map(mirrorEntry));
  } catch {}
}

function mirrorEntry(m) {
  return { workspace: m.workspace_id, name: m.name || '', remoteUrl: m.remote_url, remoteWs: m.remote_ws, remoteName: m.remote_name || '' };
}

// The switcher's cross-links on a workspace row: on a remote server, the
// local copy a workspace has (`copy`); on a local server, the original a
// copy follows (`original`), when that remote server is registered here.
function annotateWorkspace(w, state) {
  if (!current) return w;
  if (current.type === 'remote') {
    const origin = registry.originOf(current.url);
    const m = state.mirrors.find((x) => x.remoteUrl === origin && x.remoteWs === w.id);
    const srv = m && state.servers.find((s) => s.id === m.server);
    return srv ? { ...w, copy: { server: srv.id, serverName: srv.name, workspace: m.workspace, running: Boolean(sidecar.status(srv.id)) } } : w;
  }
  const m = state.mirrors.find((x) => x.server === current.id && x.workspace === w.id);
  const srv = m && state.servers.find((s) => s.type === 'remote' && registry.originOf(s.url) === m.remoteUrl);
  return srv ? { ...w, original: { server: srv.id, serverName: srv.name, workspace: m.remoteWs, name: m.remoteName } } : w;
}

// Open the offline copy of one of the open remote server's workspaces.
async function openCopy(wsId) {
  if (!current || current.type !== 'remote') throw new Error('Open a remote server first');
  const m = registry.findMirror(current.url, wsId);
  if (!m || !registry.get(m.server)) throw new Error('This workspace has no clone');
  await openServer(m.server);
  await openGammaWorkspace(m.workspace);
  buildMenu();
  return { workspace: m.workspace, server: m.server };
}

// Open the origin that a clone on the open local server follows.
async function openOriginal(wsId) {
  if (!current || current.type !== 'local') throw new Error('Open the clone first');
  const m = registry.mirrorOf(current.id, wsId);
  if (!m) throw new Error('This workspace is not a clone');
  const srv = registry.load().servers.find((s) => s.type === 'remote' && registry.originOf(s.url) === m.remoteUrl);
  if (!srv) throw new Error(`${m.remoteUrl} is not one of your servers`);
  await openServer(srv.id);
  await openGammaWorkspace(m.remoteWs);
  buildMenu();
  return { workspace: m.remoteWs, server: srv.id };
}

// Local servers to run for as long as the app does, whichever server the
// window shows (registry.launchHosts): the hosts of offline copies, of
// synced vaults of remote servers and of Zotero links that are not
// detached (all sync only while their server runs) and, in background
// mode, every local server, since a synced vault of a local workspace is
// its own server's job. Best effort, after the window is up;
// a server already running (the one just opened) is left alone.
function startBackgroundHosts() {
  for (const srv of registry.launchHosts()) {
    if (sidecar.status(srv.id)) continue;
    startLocal(srv)
      .then(() => pushState())
      .catch((e) => console.error(`[shell] could not start ${srv.name} for what it keeps in the background: ${e.message || e}`));
  }
}

// "Keep an offline copy": a mirror of the open REMOTE server's workspace
// on a local server (docs/dev/mirror.md). Everything happens through
// Gamma's public API with the content session's cookies — nothing is
// injected into any page: a write-scope integration token is minted on
// the remote for that workspace, the local server is started (made, when
// there is none) and signed into with its seeded credentials, the mirror
// is created there, and the window moves to it. The first fill runs on the
// local server in the background.
async function keepOffline(wsId) {
  if (!current || current.type !== 'remote' || !content) throw new Error('Open a remote server first');
  const g = gamma;
  const ws = g && g.list.find((w) => w.id === wsId);
  if (!ws) throw new Error('Unknown workspace');
  const remoteOrigin = addresses.remoteOf();
  const known = registry.findMirror(remoteOrigin, wsId);
  if (known && registry.get(known.server)) return openCopy(wsId); // one copy per workspace: open it
  const ses = content.webContents.session;
  const api = serverApi(ses);
  busy = `Cloning ${ws.name}…`;
  pushState();
  let token = null;
  const dropToken = () => token && revokeToken(api, remoteOrigin, wsId, token.id);
  try {
    // The host first: it says whether a token may go to that address (beforeMint).
    const { local, origin: localOrigin } = await ensureHost(api);
    await addresses.beforeMint(api, localOrigin, remoteOrigin);
    token = await api(remoteOrigin, '/api/integrations/tokens', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Gamma-Workspace': wsId },
      body: JSON.stringify({ name: 'Gamma desktop offline copy', scope: 'write', expires_in_days: 365 }),
    });
    let mirror;
    try {
      mirror = await api(localOrigin, '/api/mirrors', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ remote_url: remoteOrigin, token: token.token, name: `${ws.name} (clone)` }),
      });
    } catch (e) {
      // The local server already holds a copy the registry did not know
      // (made from Gamma's Settings, or the registry was lost): open that one.
      await reconcileMirrors(local.id, localOrigin, ses);
      if (!registry.findMirror(remoteOrigin, wsId)) throw e;
      await dropToken();
      busy = null;
      return openCopy(wsId);
    }
    token = null; // the mirror holds it now
    registry.addMirror({ server: local.id, ...mirrorEntry(mirror) });
    busy = null;
    await openServer(local.id);
    await openGammaWorkspace(mirror.workspace_id);
    buildMenu();
    offerBackground(`The clone of ${ws.name}`).catch(() => {});
    syncPanel.refreshKeeping().catch(() => {});
    return { workspace: mirror.workspace_id, server: local.id };
  } catch (e) {
    await dropToken(); // a token minted for a copy that was never made
    throw e;
  } finally {
    busy = null;
    pushState();
  }
}

// Gamma's public API through the content session (its cookies), on the
// local server or a remote: what keepOffline and the synced vaults use. A
// refusal throws with its `status` and its `body`. A redirect is never
// followed (`redirect: 'error'`): a token minted or revoked here goes only
// to the address it was asked of (docs/dev/https.md "No redirects between
// Gamma servers").
function serverApi(ses) {
  return async (origin, apiPath, init) => {
    let r;
    try {
      r = await ses.fetch(origin + apiPath, { credentials: 'include', cache: 'no-store', ...init, redirect: 'error' });
    } catch (e) {
      const why = `${(e && e.message) || ''} ${(e && e.cause && e.cause.message) || ''}`;
      if (/redirect/i.test(why)) throw new Error(`${origin} answered with a redirect: Gamma does not send its tokens on to another address.`);
      throw e;
    }
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(body.detail || `${apiPath}: HTTP ${r.status}`), { status: r.status, body });
    return body;
  };
}

// Revoke a token the shell minted on a server for workspace `wsId`. The
// workspace header matters: a token is listed and deleted per workspace.
// Best effort (the caller is already reporting something else).
function revokeToken(api, origin, wsId, tokenId) {
  return api(origin, `/api/integrations/tokens/${tokenId}`, { method: 'DELETE', headers: { 'X-Gamma-Workspace': wsId } }).catch(() => {});
}

// revokeToken, saying whether the token went: false when the server could
// not be reached, refused the window's session, or (a server that says so)
// found no such token of the account signed in there.
function revokedToken(api, origin, wsId, tokenId) {
  return api(origin, `/api/integrations/tokens/${tokenId}`, { method: 'DELETE', headers: { 'X-Gamma-Workspace': wsId } })
    .then((r) => !(r && r.revoked === false), () => false);
}

// A local server's sidecar, started (or the one already running). It gets
// the port it had last time when that is free and remembers the one it got,
// so the server keeps its address — the one the Gamma Connector was set to
// — across launches. A call while that server is still starting (the user
// opening a server that startBackgroundHosts started at launch) waits for
// the same start: a second process on its port and data directory would
// either fail to bind or leave one the shell no longer tracks.
const starting = new Map(); // server id -> its start in flight

function startLocal(srv) {
  let start = starting.get(srv.id);
  if (!start) {
    start = startLocalNow(srv).finally(() => starting.delete(srv.id));
    starting.set(srv.id, start);
  }
  return start;
}

async function startLocalNow(srv) {
  const state = registry.load();
  const ws = registry.get(srv.id) || srv;
  if (folderAccess && !folderAccess.reachable(ws.dataDir)) throw accessError(ws);
  const avoid = state.servers.filter((s) => s.id !== srv.id && s.port).map((s) => s.port);
  const entry = await sidecar.start(ws, state.settings, appInfo(), { avoid });
  registry.setPort(srv.id, entry.port);
  // its trusted certificates as the registry has them, before its first round reads one
  await addresses.pushPins(srv.id);
  // the host started again: its asks are followed again when the last reading wanted them
  if (syncPanel.asksWanted && (state.servers.find((s) => s.type === 'local') || {}).id === srv.id) agentAskBar.followAsks(srv.id);
  return entry;
}

// Mac App Store: a local server's folder outside the sandbox that the app
// could not open this run (lib/folderAccess.js). The launcher's failure
// card offers to choose it again (`access`, shell:grant-folder).
function accessError(ws) {
  const err = new Error(`Gamma cannot open ${ws.dataDir}; choose the folder again.`);
  err.startup = {
    summary: 'Gamma can no longer open this server’s folder.',
    hint: 'macOS lets an App Store app into a folder only once you have chosen it. If the folder was moved or renamed, put it back, or connect the disk it is on, then choose it again.',
    action: 'access',
    detail: ws.dataDir,
  };
  return err;
}

// Mac App Store: the server that is to write into `dir` reaches it only if
// the app held the folder when the server started (lib/folderAccess.js).
// One that started before starts again, on its port, so the page it shows
// carries on (opened again should the port have changed). Says whether it
// restarted.
async function reachFolder(srv, dir) {
  const entry = folderAccess && sidecar.status(srv.id);
  if (!entry || !folderAccess.grantedSince(dir, entry.access)) return false;
  await sidecar.stop(srv.id);
  const next = await startLocal(srv);
  if (current && current.id === srv.id && next.url !== current.url) {
    current = null;
    await openServer(srv.id);
  }
  return true;
}

// The local server that hosts what this computer keeps of remote servers —
// the clones and the synced vaults: the first local server, made when
// there is none (hostOf), started, and signed into with its seeded
// credentials in the session of `api`.
async function ensureHost(api) {
  const local = vaults.hostOf();
  const entry = await startLocal(local);
  const origin = new URL(entry.url).origin;
  allowedOrigins.add(origin);
  await signIn(api, local, origin);
  return { local, origin };
}

// The session signed into a local server with the credentials the shell
// seeded, unless it already is.
async function signIn(api, local, origin) {
  if ((await api(origin, '/api/session')).user) return;
  await api(origin, '/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: local.adminUser, password: local.adminPassword }),
  });
}

// A running local server, signed into, for a call from the main process:
// `{ srv, origin, api }`. What the sync panel reads and acts through. Each
// local server gets a cookie jar of its own (an in-memory partition): every
// sidecar is 127.0.0.1, and a cookie does not tell ports apart, so signing
// into one in the window's session would sign the window out of another.
async function localServer(serverId) {
  const srv = registry.get(serverId);
  const entry = srv && srv.type === 'local' ? sidecar.status(serverId) : null;
  if (!entry) throw new Error('That server is not running');
  const origin = new URL(entry.url).origin;
  const api = serverApi(session.fromPartition(`keeping-${serverId}`));
  await signIn(api, srv, origin);
  return { srv, origin, api };
}

// ---------------------------------------------- the word, pickers and questions -----
// What the sections share: the shell's word to its local servers and which
// origins and pages are this computer's, the server the window shows, the
// native pickers and question (answered by the test under the harness), one
// question at a time, and a short line in the bar.

const JSON_TYPE = { 'Content-Type': 'application/json' };

// The headers of a route that takes a path on this computer: the shell's
// word, for a running local server of this app only. Asked with the origin
// the request goes to, so neither the word nor a path ever reaches a remote
// server, whatever the window opened while a picker was up.
function shellWord(origin) {
  if (!isLocalOrigin(origin)) throw new Error('A path on this computer goes only to a server on this computer.');
  return { 'X-Gamma-Shell': SHELL_SECRET };
}

// Whether `origin` is a running local server's of this app.
function isLocalOrigin(origin) {
  return registry.load().servers.some((s) => {
    const entry = s.type === 'local' && sidecar.status(s.id);
    return entry && new URL(entry.url).origin === origin;
  });
}

// Whether the page at `url` is this computer's: a running local server's,
// or one of the two bundled shell pages. Any other is a remote page, a
// NAS's included (lib/permissions.js, the content guard).
function isLocalPage(url) {
  if (isShellPage(url, 'bar') || isShellPage(url, 'launcher')) return true;
  let origin = '';
  try {
    origin = new URL(url).origin;
  } catch {
    return false;
  }
  return /^https?:/.test(origin) && isLocalOrigin(origin);
}

function openOrigin() {
  if (!current || !content) throw new Error('Open a server first');
  return new URL(current.url).origin;
}

// Where local server `srv` answers now: its sidecar's origin (reachFolder
// may have started it again on another port), never what the window shows.
function localOrigin(srv) {
  const entry = srv && srv.type === 'local' && sidecar.status(srv.id);
  if (!entry) throw new Error(`${srv ? srv.name : 'That server'} is not running.`);
  return new URL(entry.url).origin;
}

// A flow of the window's server goes on through the window's session only
// while the window still shows that server (`at`, the `current` it started
// on): one opened meanwhile, while a picker was up, has its own session.
function stillOn(at) {
  if (!current || current.id !== at.id) throw new Error(`The window left ${at.name} meanwhile: ask again there.`);
}

// The native open dialog, for a directory or a file: the path chosen, null
// when cancelled. In the Mac App Store build the choice is also the app's
// access to it, kept for later runs as a bookmark (lib/folderAccess.js).
async function choosePath(opts) {
  const o = folderAccess ? { ...opts, securityScopedBookmarks: true } : opts;
  const r = win ? await dialog.showOpenDialog(win, o) : await dialog.showOpenDialog(o);
  const chosen = r.canceled ? null : r.filePaths[0] || null;
  if (chosen && folderAccess) folderAccess.add(chosen, r.bookmarks && r.bookmarks[0]);
  return chosen;
}

// `defaultPath`: where the picker opens (the Mac App Store build's *Open
// with Gamma* opens it at the folder handed over, to be chosen there).
function pickDirectory(title, message, defaultPath = app.getPath('documents')) {
  if (process.env.GAMMA_SHELL_PICK_DIR) return process.env.GAMMA_SHELL_PICK_DIR;
  return choosePath({ title, message, buttonLabel: 'Choose', properties: ['openDirectory', 'createDirectory'], defaultPath });
}

// A file of extension `ext` (`.pdf`, `.tex`…; '' for any file), the
// picker showing that extension only.
function pickFile(title, message, ext, buttonLabel = 'Choose') {
  if (process.env.GAMMA_SHELL_PICK_FILE) return process.env.GAMMA_SHELL_PICK_FILE;
  const bare = String(ext || '').replace(/^\./, '');
  return choosePath({
    title, message, buttonLabel, properties: ['openFile'], filters: bare ? [{ name: bare.toUpperCase(), extensions: [bare] }] : [],
    defaultPath: app.getPath('documents'),
  });
}

function pickPdf(title, message, buttonLabel = 'Choose') {
  return pickFile(title, message, '.pdf', buttonLabel);
}

// The native question, or its answer under the test harness: the one a test
// queued (`answers`), else the first choice that writes nothing extra, the
// question then only recorded (`asked`: its message and its detail).
const asked = [];
const answers = [];
async function ask(opts, testAnswer) {
  if (process.env.GAMMA_SHELL_TEST) {
    asked.push(`${opts.message} ${opts.detail || ''}`.trim());
    return answers.length ? answers.shift() : testAnswer;
  }
  const r = win ? await dialog.showMessageBox(win, opts) : await dialog.showMessageBox(opts);
  return r.response;
}

// A page asked for the microphone that macOS keeps from Gamma
// (lib/microphone.js): the answer can only change in System Settings, and
// reaches Gamma at its next start.
async function microphoneRefused() {
  const choice = await ask({
    type: 'info',
    message: 'Gamma may not use the microphone',
    detail: 'Allow Gamma under System Settings → Privacy & Security → Microphone, then quit Gamma and open it again.',
    buttons: ['Open System Settings', 'Cancel'],
    defaultId: 0,
    cancelId: 1,
  }, 1);
  if (choice === 0) openExternal('x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone');
}

// An error in a native message box. The test scripts' minimized runs log it
// instead, so a step that fails leaves no dialog on the screen.
function errorBox(opts) {
  if (process.env.GAMMA_SHELL_TEST) errorBoxes.push(`${opts.message} ${opts.detail}`);
  if (TEST_MINIMIZED) console.error(`[shell] ${opts.message} ${opts.detail}`);
  else (win ? dialog.showMessageBox(win, opts) : dialog.showMessageBox(opts)).catch(() => {});
}

// One flow that asks the user at a time (a picker, a native question, the
// vault dialog), held from its first line until it settles: an ask while
// one runs, a double click or a page asking in a loop, is dropped (null)
// instead of stacking pickers, and a later dialog never cancels the one the
// user is answering.
let asking = false;
function oneAsk(fn) {
  return async (...args) => {
    if (asking) return null;
    asking = true;
    try {
      return await fn(...args);
    } finally {
      asking = false;
    }
  };
}

// A short line in the bar after a shell action, for 8 s.
function showNotice(text) {
  notice = text;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { notice = null; pushState(); }, 8000);
  pushState();
}

// --------------------------------------------------- Gamma Cloud sign-in -----
// Gamma Cloud sign-in, taken over from the page (lib/cloudSignIn.js): the
// account server's page opens in the system browser, and the session it
// ends in is redeemed in this window. The result is for the server that
// started it, so it is dropped if another one is open by then.
async function signInWithCloud(startUrl) {
  const origin = new URL(startUrl).origin;
  let flow = null;
  try {
    flow = await cloudSignIn.listen();
    const r = await content.webContents.session.fetch(origin + cloudSignIn.APP_START_PATH, {
      method: 'POST',
      credentials: 'include',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...cloudSignIn.startParams(startUrl), return_to: flow.returnTo, challenge: flow.challenge }),
    });
    // A server older than the app's sign-in: the browser signs in as it always did.
    if (r.status === 404 || r.status === 405) {
      flow.end(null);
      openExternal(startUrl);
      showNotice('This server is older than the app: the Gamma Cloud sign-in finishes in your browser.');
      return;
    }
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
    openExternal(body.url);
    showNotice('Finish signing in to Gamma Cloud in your browser.');
  } catch (e) {
    if (flow) flow.end(null);
    showNotice(`Gamma Cloud sign-in: ${e.message}`);
    return;
  }
  const answer = await flow.answer;
  if (!answer) return; // given up on, or a newer sign-in took over
  await surfaceWindow();
  if (answer.error) {
    showNotice(`Gamma Cloud sign-in did not finish: ${answer.error}`);
    return;
  }
  if (!current || new URL(current.url).origin !== origin) {
    showNotice('Gamma Cloud sign-in finished for a server that is no longer open. Open it and sign in again.');
    return;
  }
  await loadPage(content.webContents, ...cloudSignIn.claimRequest(origin, flow, answer.result)).catch(() => {});
  refreshGamma();
}

// ---------------------------------------------------------- background -----
// "Keep running in the background": closing the window leaves the app in
// the tray with every local server running, so clones and synced vaults
// keep syncing (docs/architecture.md "Background and tray"). Off, closing
// the last window quits (macOS keeps the dock process either way). "Start
// at login" launches it hidden, straight into the tray.

function trayIcon() {
  const img = nativeImage.createFromPath(path.join(__dirname, 'assets', 'icon.png'));
  const size = process.platform === 'darwin' ? 18 : 16;
  return img.resize({ width: size, height: size });
}

// The two switches as menu items, under the labels the menu's case wants
// (the Mac App Store build has no login item, applyLoginItem).
function backgroundItems(keepLabel, loginLabel) {
  const s = registry.getSettings();
  return [
    { label: keepLabel, type: 'checkbox', checked: Boolean(s.background), click: (item) => setBackground(item.checked) },
    ...(process.mas ? [] : [{ label: loginLabel, type: 'checkbox', checked: Boolean(s.openAtLogin), click: (item) => setOpenAtLogin(item.checked) }]),
  ];
}

// The tray's menu: first, while an agent's question waits, a line that
// brings the window to it.
function trayMenu() {
  const waits = agentAsks.trayLabel(agentAskBar.askQueue);
  return Menu.buildFromTemplate([
    ...(waits ? [{ label: waits, click: () => showWindow() }, { type: 'separator' }] : []),
    { label: 'Open Gamma', click: () => showWindow() },
    { type: 'separator' },
    ...backgroundItems('Keep running in the background', 'Start at login'),
    { type: 'separator' },
    { label: 'Quit Gamma', click: () => app.quit() },
  ]);
}

function ensureTray() {
  if (tray) {
    tray.setContextMenu(trayMenu());
    return;
  }
  tray = new Tray(trayIcon());
  tray.setToolTip(agentAsks.trayTip(agentAskBar.askQueue));
  tray.setContextMenu(trayMenu());
  tray.on('click', () => showWindow());
}

function dropTray() {
  if (tray) {
    tray.destroy();
    tray = null;
  }
}

// The window, back or new, the one way every entry brings it (the tray,
// Window → Show Gamma, the Dock icon, a second start): a hidden or
// minimized window comes back as it was; a closed one is made again, where
// the user left off (the launcher is one click away in the bar) or on the
// launcher, over the local server still running from before (startLocal
// hands it back: no second Python); either way it ends focused (a new
// window opens focused). Resolves once that is showing.
function showWindow() {
  if (win && !win.isDestroyed()) {
    if (TEST_MINIMIZED) win.showInactive(); // back, without the keyboard
    else {
      if (win.isMinimized()) win.restore();
      win.show();
      win.focus();
    }
    return Promise.resolve();
  }
  createWindow();
  const last = registry.getSettings().openLastOnLaunch ? registry.getLastOpened() : null;
  if (!last) {
    loadLauncher();
    return Promise.resolve();
  }
  return openServer(last.id).then(buildMenu).catch((e) => loadLauncher(e, last.id));
}

// The window for what the app brings up by itself, a PDF handed over or a
// sign-in come back; the test scripts' minimized window stays down for those.
function surfaceWindow() {
  return TEST_MINIMIZED && win && !win.isDestroyed() ? Promise.resolve() : showWindow();
}

function setBackground(on) {
  registry.setSettings({ background: Boolean(on) });
  if (on) ensureTray();
  else {
    dropTray();
    if (!win) app.quit(); // turned off from the tray with no window: nothing is left to run for
  }
  buildMenu();
  pushState();
}

function setOpenAtLogin(on) {
  registry.setSettings({ openAtLogin: Boolean(on) });
  applyLoginItem();
  buildMenu();
  pushState();
}

// The OS login item: Electron's on Windows and macOS, an autostart entry on
// Linux — a packaged app only (in dev it would register the electron
// binary; under the test harness nothing is touched). Started that way with
// the background on, the app opens hidden, into the tray: Windows and Linux
// pass it `--hidden`, and macOS, which passes a login item no arguments,
// reports the login start (`wasOpenedAtLogin`). Not in the Mac App Store
// build: a sandboxed app registers a login item only through a helper of
// its own, which it does not have yet, and the launcher hides the switch.
function applyLoginItem() {
  const on = Boolean(registry.getSettings().openAtLogin);
  if (!app.isPackaged || process.env.GAMMA_SHELL_TEST || process.mas) return;
  if (process.platform === 'linux') {
    const dir = path.join(app.getPath('home'), '.config', 'autostart');
    const file = path.join(dir, 'gamma-desktop.desktop');
    try {
      if (on) {
        fs.mkdirSync(dir, { recursive: true });
        fs.writeFileSync(file, ['[Desktop Entry]', 'Type=Application', 'Name=Gamma', `Exec="${process.execPath}" --hidden`, 'X-GNOME-Autostart-enabled=true', ''].join('\n'));
      } else {
        fs.rmSync(file, { force: true });
      }
    } catch (e) {
      console.error(`[shell] autostart entry: ${e.message || e}`);
    }
    return;
  }
  app.setLoginItemSettings({ openAtLogin: on, args: ['--hidden'] });
}

// A clone or a synced vault stays in sync only while Gamma runs: when one
// is made and the app still quits with its window, offer the background.
async function offerBackground(what) {
  if (registry.getSettings().background || !win) return;
  const answer = await ask({
    type: 'question',
    buttons: ['Keep running', 'Not now'],
    defaultId: 0,
    cancelId: 1,
    message: 'Keep Gamma running in the background?',
    detail: `${what} stays in sync only while Gamma runs. In the background Gamma keeps running after the window is closed, with an icon in the tray, and can start at login.`,
  }, 1);
  if (answer === 0) setBackground(true);
}

// Navigate the open server to one of its Gamma workspaces, or in it to a page
// (`page`, or the second argument as a string), to a folder (`folder`), or to
// a work folder's tracking dialog (`track`, its link's id, with its folder),
// a file ticked in it (`tick`, its path in the work folder, which the page
// reads and drops from the address at once, as it does `track`).
function openGammaWorkspace(id, at = {}) {
  if (!current || !content) throw new Error('No server open');
  const { page, folder, track, tick } = typeof at === 'string' ? { page: at } : at || {};
  const target = new URL(current.url);
  const q = new URLSearchParams({ ws: String(id || '') });
  if (page) q.set('page', page);
  if (folder) q.set('folder', folder);
  if (track) q.set('track', track);
  if (track && tick) q.set('tick', tick);
  target.search = '?' + q;
  return loadPage(content.webContents, target.href);
}

// What the shell bar (and the launcher, for the switcher part) renders.
function barState() {
  const state = registry.load();
  return {
    platform: process.platform,
    wco: process.platform === 'linux' ? wcoSides : undefined,
    version: app.getVersion(),
    packaged: app.isPackaged,
    theme: currentTheme(),
    current,
    busy,
    notice,
    // what this computer keeps (the sync button and its panel)
    keeping: syncPanel.keepingState,
    // the vault dialog the sync panel shows (askVault), or null
    vaultAsk: vaults.vaultAskState(),
    // an agent's question on this computer, the card the bar draws over the
    // whole window with whether its buttons are armed, or null
    agentAsk: agentAskBar.agentAskState(),
    update: updater.state(),
    // Gamma's workspaces on the open server (null until known / signed in),
    // with the one the content view shows and the offline-copy cross-links.
    gamma: gamma ? { ...gamma, current: currentWsId() || gamma.current, list: gamma.list.map((w) => annotateWorkspace(w, state)) } : null,
    servers: state.servers.map((ws) => ({
      id: ws.id,
      name: ws.name,
      type: ws.type,
      url: ws.type === 'remote' ? ws.url : (sidecar.status(ws.id) || {}).url,
      running: ws.type === 'local' ? Boolean(sidecar.status(ws.id)) : undefined,
      reachable: remoteReachable(ws),
    })),
  };
}

// A storage-folder move under way (shell:set-data-root): the servers it
// moves, and the bytes copied of all of theirs. null when none runs.
let dataMove = null;

// The launcher's switches over Gamma's entries in File Explorer
// (lib/openWith.js): the module once the app is ready, and what it last
// read, null where there is nothing to switch.
let openWith = null;
let openWithState = null;
function readOpenWith() {
  openWith.read().then((s) => { openWithState = s; pushState(); }, (e) => console.error(`[shell] Explorer entries: ${e.message || e}`));
}
function dataMoveState() {
  return dataMove && { name: dataMove.name, done: dataMove.done, total: dataMove.total };
}

// The launcher's fuller view: credentials, data dirs, sizes, settings.
function fullState() {
  const state = registry.load();
  const { pins } = state;
  const plain = state.settings.plainAccepted || [];
  return {
    ...barState(),
    servers: state.servers.map((ws) => ({
      ...ws,
      // A local server's address while it runs (the Connector's server).
      url: ws.type === 'remote' ? ws.url : (sidecar.status(ws.id) || {}).url,
      running: ws.type === 'local' ? Boolean(sidecar.status(ws.id)) : undefined,
      reachable: remoteReachable(ws),
      // A remote's trusted certificate and its plain http:// yes (the edit form's Forget… and Ask again…).
      pinned: ws.type === 'remote' ? Boolean(pins[ws.url]) : undefined,
      plainAccepted: ws.type === 'remote' ? plain.includes(ws.url) : undefined,
      // Paths as the file manager can find them (hostPath, a Store install).
      dataDir: hostPath(ws.dataDir),
      sizeBytes: ws.type === 'local' ? registry.dirSize(ws.dataDir) : undefined,
      logPath: ws.type === 'local' ? hostPath(logPath(ws.id)) : undefined,
    })),
    settings: state.settings,
    lastOpened: state.lastOpened,
    detected: sidecar.detectDev(state.settings),
    userDataDir: hostPath(app.getPath('userData')),
    dataRoot: hostPath(registry.dataRoot(state)),
    // A Store install's libraries in the package's own AppData, which the
    // Store's uninstall deletes with no question (the launcher says so).
    dataRootGoesWithApp: goesWithPackage(registry.dataRoot(state)),
    defaultDataRoot: hostPath(registry.defaultDataRoot()),
    // Names of the local servers a root change would move.
    movable: registry.localsUnderRoot(state).map((w) => w.name),
    dataMove: dataMoveState(),
    mas: Boolean(process.mas), // the launcher hides what a Mac App Store build lacks
    openWith: openWithState,
  };
}

function pushState() {
  if (!bar || bar.webContents.isDestroyed() || bar.webContents.isLoading()) return;
  bar.webContents.send('shell:state', barState());
  if (content && !content.webContents.isDestroyed() && isShellPage(content.webContents.getURL(), 'launcher')) {
    content.webContents.send('shell:state', barState());
  }
}

// ----------------------------------------------------------- auto-login -----

// Local servers log in silently with the credentials the shell seeded.
// Runs in the page after load: if /api/session says anonymous, POST the
// stored credentials and answer 'logged-in'. Harmless when already logged in.
// The page does not reload itself: the caller loads it again through
// loadPage and waits (signedInAgain), so the reload is over before whatever
// load comes next. A location.reload() here ran on after the shell had moved
// on, and the next load (a server, then its workspace) started under it.
function autoLoginScript(username, password) {
  return `(async () => {
    try {
      const s = await fetch('/api/session', { credentials: 'same-origin' });
      if (s.ok) {
        const j = await s.json().catch(() => null);
        if (j && j.user) return 'already:' + j.user;
      }
      const r = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ username: ${JSON.stringify(username)}, password: ${JSON.stringify(password)} }),
      });
      if (r.ok) return 'logged-in';
      return 'login-failed:' + r.status;
    } catch (e) { return 'error:' + e; }
  })()`;
}

// The page again after a silent login, now signed in, as location.reload()
// would load it, and over by the time this resolves.
function signedInAgain(webContents) {
  return loadPage(webContents, webContents.getURL());
}

let opening = null; // serialize opens: a second click while one is in flight waits

async function openServer(id) {
  if (opening) await opening.catch(() => {});
  opening = openServerNow(id);
  try {
    return await opening;
  } finally {
    opening = null;
  }
}

async function openServerNow(id) {
  const ws = registry.get(id);
  if (!ws) throw new Error('Unknown server');
  if (dataMove && dataMove.ids.has(id)) throw new Error(`${ws.name} is being moved to the new storage folder: open it once the move is done.`);
  if (!win) createWindow();
  if (current && current.id === id) return { url: current.url, autoLogin: 'already-open' };
  vaults.cancelVaultAsk(); // it was asked about what the window showed
  busy = ws.type === 'local' ? `Starting ${ws.name}…` : `Connecting to ${ws.name}…`;
  pushState();
  try {
    let url;
    let creds = null;
    if (ws.type === 'remote') {
      url = ws.url;
      addresses.sentToHttps.delete(registry.originOf(url));
    } else {
      const entry = await startLocal(ws);
      url = entry.url;
      if (ws.adminUser) creds = { username: ws.adminUser, password: ws.adminPassword };
    }
    allowedOrigins.add(new URL(url).origin);
    await loadPage(content.webContents, url);
    if (ws.type === 'remote') remoteHealth.set(ws.id, { ok: true, at: Date.now() });
    current = { id: ws.id, name: ws.name, type: ws.type, url };
    gamma = null;
    registry.markOpened(ws.id);
    content.webContents.focus();
    if (creds) {
      const result = await content.webContents
        .executeJavaScript(autoLoginScript(creds.username, creds.password))
        .catch((e) => 'exec-error:' + e);
      if (String(result) === 'logged-in') await signedInAgain(content.webContents);
      refreshGamma();
      return { url, autoLogin: String(result) };
    }
    return { url };
  } catch (e) {
    current = null;
    if (ws.type === 'remote') remoteHealth.set(ws.id, { ok: false, at: Date.now() });
    throw e;
  } finally {
    busy = null;
    pushState();
  }
}

// The launcher's Remove: a local server stopped first; a remote's pinned
// certificate goes with it (registry.remove), and the verify proc and the
// local servers stop accepting it at once (pinsChanged).
async function removeServer(id, opts = {}) {
  if (current && current.id === id) loadLauncher();
  await sidecar.stop(id);
  const srv = registry.get(id);
  const pinned = Boolean(srv && srv.type === 'remote' && registry.getPins()[srv.url]);
  registry.remove(id, opts);
  if (pinned) await addresses.pinsChanged();
  buildMenu();
  pushState();
}

// Help → Check for Updates…: the only update flow that answers with a
// dialog; the automatic checks stay silent (the bar's update icon / launcher row).
async function checkForUpdatesInteractive() {
  const st = await updater.check();
  const opts = { title: 'Gamma', message: '', buttons: ['OK'] };
  if (st.status === 'unsupported') {
    opts.message = 'Updates are not available in this build.';
    opts.detail =
      st.error === 'dev build' ? 'Development build: update from git.'
      : st.error === 'store' ? 'Installed from the Microsoft Store: updates arrive through the Store.'
      : st.error === 'mas' ? 'Installed from the Mac App Store: updates arrive through the App Store.'
      : String(st.error || '');
  } else if (st.status === 'downloaded' || (st.status === 'downloading' && !st.installing)) {
    opts.message = st.line; // lib/updater.js words the update found
    opts.buttons = ['Update now', 'Later'];
  } else if (st.status === 'downloading') {
    opts.message = st.line;
  } else if (st.status === 'available') {
    opts.message = st.line;
    opts.buttons = ['Download', 'Later'];
  } else if (st.status === 'error') {
    opts.message = 'Could not check for updates.';
    opts.detail = String(st.error || '');
  } else {
    opts.message = `You are on the latest version (${st.current}).`;
  }
  // Under the test harness the last button, Later or OK, which installs nothing.
  const r = await ask(opts, opts.buttons.length - 1);
  if (r === 0 && opts.buttons.length > 1) updater.install();
}

// ----------------------------------------------------------------- menu -----

function buildMenu() {
  const { servers } = registry.load();
  const wc = () => (content ? content.webContents : null);
  // Ctrl+W (⌘W) is the page's: Gamma's Close tab, as in a browser. The
  // window closes on Ctrl+Shift+W (⇧⌘W), Chrome's key for it.
  const closeWindow = { role: 'close', accelerator: 'CmdOrCtrl+Shift+W' };
  const template = [
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' }] : []),
    {
      label: 'Server',
      submenu: [
        { label: 'All Servers…', accelerator: 'CmdOrCtrl+Shift+L', click: () => loadLauncher() },
        { label: 'Open a PDF…', click: () => systemFiles.openPdfDialog().catch(() => {}) },
        { type: 'separator' },
        ...servers.map((ws) => ({
          label: `${ws.name}${ws.type === 'remote' ? '  (remote)' : ''}`,
          type: 'checkbox',
          checked: Boolean(current && current.id === ws.id),
          click: () => openServer(ws.id).catch((e) => loadLauncher(e, ws.id)),
        })),
        { type: 'separator' },
        ...backgroundItems('Keep Running in the Background', 'Start at Login'),
        { type: 'separator' },
        process.platform === 'darwin' ? closeWindow : { role: 'quit' },
      ],
    },
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [
        { label: 'Reload', accelerator: 'CmdOrCtrl+R', click: () => wc() && wc().reload() },
        { label: 'Toggle Developer Tools', accelerator: process.platform === 'darwin' ? 'Alt+Cmd+I' : 'Ctrl+Shift+I', click: () => wc() && wc().toggleDevTools() },
        { type: 'separator' },
        { label: 'Actual Size', accelerator: 'CmdOrCtrl+0', click: () => wc() && wc().setZoomLevel(0) },
        { label: 'Zoom In', accelerator: 'CmdOrCtrl+=', click: () => wc() && wc().setZoomLevel(wc().getZoomLevel() + 0.5) },
        { label: 'Zoom Out', accelerator: 'CmdOrCtrl+-', click: () => wc() && wc().setZoomLevel(wc().getZoomLevel() - 0.5) },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    {
      // The roles' Window menu, with Show Gamma, which is there with no
      // window too: macOS keeps the app and its menu bar once the window
      // is closed (App Review 4: the menu must bring it back).
      label: 'Window',
      role: 'window', // macOS lists the open windows under it
      submenu: [
        { role: 'minimize' },
        process.platform === 'darwin' ? { role: 'zoom' } : closeWindow,
        { type: 'separator' },
        { label: 'Show Gamma', click: () => showWindow() },
        ...(process.platform === 'darwin' ? [{ type: 'separator' }, { role: 'front' }] : []),
      ],
    },
    {
      role: 'help',
      submenu: [
        { id: 'check-updates', label: 'Check for Updates…', click: () => checkForUpdatesInteractive().catch(() => {}) },
        { label: 'Gamma on GitHub', click: () => openExternal('https://github.com/tim4431/Gamma') },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ------------------------------------------------------------------ IPC -----
// The bundled launcher and bar, in their own top-level views, are the only
// privileged surfaces. Re-check both the frame and its exact URL here.

function shellOnly(handler) {
  return (event, ...args) => {
    if (!trustedShellSender(event, bar, content)) throw new Error('Not allowed');
    return handler(...args);
  };
}

// The bar's main frame alone, never the launcher: what answers an agent's
// question on this computer.
function barOnly(handler) {
  return (event, ...args) => {
    if (!trustedShellSender(event, bar, content) || !bar || event.sender !== bar.webContents) throw new Error('Not allowed');
    return handler(...args);
  };
}

function registerIpc() {
  ipcMain.handle('shell:state', shellOnly(() => { probeRemotes(); refreshGamma(); return barState(); }));
  ipcMain.handle('shell:list', shellOnly(() => { probeRemotes(); return fullState(); }));
  ipcMain.handle('shell:update-check', shellOnly(() => updater.check()));
  ipcMain.handle('shell:update-install', shellOnly(() => updater.install()));
  ipcMain.handle('shell:add-local', shellOnly((name) => { const ws = registry.addLocal(name); buildMenu(); pushState(); return ws; }));
  ipcMain.handle('shell:add-remote', shellOnly((name, url) => { const ws = registry.addRemote(name, url); buildMenu(); pushState(); return ws; }));
  // An address changes with its links (moveServer); null when the person cancelled.
  ipcMain.handle('shell:set-url', shellOnly((id, url) => addresses.moveServer(id, url)));
  // The launcher's certificate card and the server's edit form.
  ipcMain.handle('shell:trust-cert', shellOnly((id, nonce, sha256) => addresses.trustCert(id, String(nonce || ''), String(sha256 || ''))));
  ipcMain.handle('shell:forget-cert', shellOnly((id) => addresses.forgetCert(id)));
  ipcMain.handle('shell:forget-plain', shellOnly((id) => {
    const srv = registry.get(id);
    if (srv && srv.type === 'remote') registry.setPlainAccepted(srv.url, false);
    pushState();
    return true;
  }));
  ipcMain.handle('shell:rename', shellOnly((id, name) => {
    const ws = registry.rename(id, name);
    if (current && current.id === id) current = { ...current, name: ws.name };
    buildMenu();
    pushState();
    return ws;
  }));
  ipcMain.handle('shell:remove', shellOnly((id, opts) => removeServer(id, opts || {})));
  ipcMain.handle('shell:open-workspace', shellOnly(async (id) => {
    setBarExpanded(false);
    await openGammaWorkspace(id);
  }));
  // collapse: the bar view shrinks back to its strip first (the dropdown that
  // asked closes); false for a panel action that leaves the panel open.
  const withDialog = (message, fn, collapse = true) => async (id) => {
    if (collapse) setBarExpanded(false);
    try {
      return await fn(id);
    } catch (e) {
      if (e && e.quiet) return null; // a question the person cancelled (beforeMint)
      errorBox({ type: 'error', title: 'Gamma', message, detail: String(e && e.message || e), buttons: ['OK'] });
      throw e;
    }
  };
  ipcMain.handle('shell:keep-offline', shellOnly(withDialog('Could not clone the workspace.', keepOffline)));
  ipcMain.handle('shell:open-copy', shellOnly(withDialog('Could not open the clone.', openCopy)));
  ipcMain.handle('shell:open-original', shellOnly(withDialog('Could not open the origin.', openOriginal)));
  ipcMain.handle('shell:open', shellOnly(async (id) => {
    setBarExpanded(false);
    try {
      const r = await openServer(id);
      buildMenu();
      return r;
    } catch (e) {
      loadLauncher(e, id);
      throw e;
    }
  }));
  ipcMain.handle('shell:launcher', shellOnly(() => { setBarExpanded(false); loadLauncher(); }));
  ipcMain.handle('shell:reload', shellOnly(() => content && content.webContents.reload()));
  ipcMain.handle('shell:reveal-data', shellOnly((id) => {
    const ws = registry.get(id);
    if (ws && ws.type === 'local') openDirectory(ws.dataDir);
  }));
  ipcMain.handle('shell:reveal-log', shellOnly((id) => {
    const p = logPath(id);
    if (fs.existsSync(p)) revealFile(p);
  }));
  // A running local server's address, for the Gamma Connector's options.
  ipcMain.handle('shell:copy-address', shellOnly((id) => {
    const entry = sidecar.status(id);
    if (!entry) return null;
    copyText(entry.url);
    return entry.url;
  }));
  ipcMain.handle('shell:set-settings', shellOnly((patch) => {
    const s = registry.setSettings(patch);
    if ('background' in patch) setBackground(s.background);
    if ('openAtLogin' in patch) setOpenAtLogin(s.openAtLogin);
    return s;
  }));
  // A switch of the launcher's: `files` or `folders` on or off.
  ipcMain.handle('shell:set-open-with', shellOnly(async (part, on) => {
    openWithState = await openWith.set(String(part), on === true);
    return openWithState;
  }));
  ipcMain.handle('shell:folders', shellOnly((wsId) => vaults.listFolders(wsId)));
  ipcMain.handle('shell:add-folder-vault', shellOnly((wsId, folderId) =>
    withDialog('Could not sync the folder with this computer.', oneAsk(() => vaults.addFolderVault(wsId, folderId)))()));
  ipcMain.handle('shell:add-vault', shellOnly(() => withDialog('Could not add the synced folder.', oneAsk(() => vaults.addDirectoryVault()))()));
  ipcMain.handle('shell:answer-vault', shellOnly((id, answer) => vaults.answerVault(id, answer)));
  ipcMain.handle('shell:keeping', shellOnly(() => { syncPanel.refreshKeeping().catch(() => {}); return syncPanel.keepingState; }));
  // Of a vault's actions, those that change it or run a round leave the sync
  // panel open on the new state (bar.html keptRow); the others ask first, or
  // open something.
  const STAYS_OPEN = ['pause', 'resume', 'sync', 'pull-now', 'push-now', 'pull', 'push', 'library', 'show-computer', 'hide-computer'];
  ipcMain.handle('shell:keeping-action', shellOnly((kind, serverId, id, action, value) =>
    withDialog('Could not do that.', () => syncPanel.keepingAction(kind, serverId, id, action, value), !STAYS_OPEN.includes(action))()));
  // A page's asks about this computer in Gamma's own menus (a folder's "Sync
  // with this computer…", a page's "Push from a file on this computer…", its
  // file shown in its folder, its missing file located, an opened PDF carried
  // to another server's library, its computer chat continued in a terminal,
  // the tracking dialog of a work folder of another server answered in the
  // host's window, `flag` saying whether it was cancelled; Settings → AI's
  // agents of this computer turned on or off, let ask or not, a directory
  // offered to them, a person let use one; the servers this computer
  // answers added, removed, let answer its agents' asks or not), passed on
  // by the page preload: only from the content
  // view's page of the open server, with plain ids, one action at a time
  // (oneAsk: none while a picker, a question or the vault dialog is up), and
  // at most one a second, so a page asking in a loop opens no row of file
  // manager windows. The native picker and the native question are the
  // user's say, so the page can start nothing on its own.
  const PAGE_ASK_MS = 1000;
  let pageAskAt = 0;
  const fromPage = (event, ...ids) => {
    if (!content || event.sender !== content.webContents) return false;
    // (while an agent's question hides the page, it is answered first)
    if (agentAskBar.shownKey) {
      showNotice(agentAsks.answerFirst(agentAskBar.askCard()));
      return false;
    }
    if (busy || vaults.vaultAsk || asking || !current) return false;
    let from = '';
    try { from = new URL(event.senderFrame.url).origin; } catch {}
    if (from !== openOrigin() || !ids.every((id) => /^[\w-]{1,64}$/.test(id))) return false;
    if (Date.now() - pageAskAt < PAGE_ASK_MS) return false;
    pageAskAt = Date.now();
    return true;
  };
  const PAGE_ASKS = {
    'shell:sync-folder-from-page': ['Could not sync the folder with this computer.', vaults.addFolderVault],
    'shell:push-file-from-page': ['Could not push from the file.', vaults.addFileVault],
    'shell:reveal-from-page': ['Could not show the file.', thisComputer.revealOnDisk],
    'shell:relocate-from-page': ['Could not locate the file.', thisComputer.relocateFile],
    'shell:carry-from-page': ['Could not add the page to the other server\'s library.', systemFiles.carryToServer],
    'shell:copy-file-from-page': ['Could not copy the file.', thisComputer.copyFileFromPage],
    'shell:terminal-from-page': ['Could not open a terminal.', thisComputer.terminalFromPage],
    'shell:tracked-from-page': ['Could not go back to the synced folder.', vaults.trackedFromPage],
    'shell:set-agent-from-page': ['Could not change the agent.', thisComputer.setAgentFromPage],
    'shell:agent-runs-from-page': ['Could not change what the agent may run.', thisComputer.agentRunsFromPage],
    'shell:agent-dirs-from-page': ['Could not offer the directory.', thisComputer.agentDirsFromPage],
    'shell:agent-person-from-page': ['Could not let that person use the agent.', thisComputer.agentPersonFromPage],
    'shell:add-server-from-page': ['Could not show this computer on that server.', thisComputer.addServerFromPage],
    'shell:remove-server-from-page': ['Could not stop answering that server.', thisComputer.removeServerFromPage],
    'shell:link-asks-from-page': ['Could not change what that server may answer.', thisComputer.linkAsksFromPage],
  };
  for (const [channel, [message, run]] of Object.entries(PAGE_ASKS)) {
    ipcMain.on(channel, (event, wsId, id, flag, ...more) => {
      if (!fromPage(event, wsId, id, ...more)) return;
      withDialog(message, oneAsk(() => run(wsId, id, flag === true, ...more)))().catch(() => {});
    });
  }
  // Reveal a directory a link of the open server's workspace writes — only such a one.
  ipcMain.handle('shell:open-path', shellOnly(async (wsId, p) => {
    const { links } = await vaults.listFolders(wsId);
    if (links.some((l) => l.dest === p)) openDirectory(p);
  }));
  ipcMain.handle('shell:pick-folder', shellOnly((defaultPath) =>
    choosePath({ properties: ['openDirectory', 'createDirectory'], defaultPath: defaultPath || undefined })));
  // Mac App Store: the launcher's answer to a server whose folder the app
  // can no longer open (accessError). The folder chosen must hold the
  // server's data; true once it does.
  ipcMain.handle('shell:grant-folder', shellOnly(async (id) => {
    const srv = registry.get(id);
    if (!folderAccess || !srv || srv.type !== 'local') throw new Error('Nothing to choose');
    const dir = await choosePath({
      title: `Choose the folder of “${srv.name}”`,
      message: `Choose ${srv.dataDir}, or a folder it is in.`,
      buttonLabel: 'Choose',
      properties: ['openDirectory'],
      defaultPath: path.dirname(srv.dataDir),
    });
    if (!dir) return false;
    if (!folderAccess.reachable(srv.dataDir)) throw new Error(`That folder does not hold ${srv.dataDir}.`);
    return true;
  }));
  // Storage root for local servers ('' = default). Moving needs the SQLite
  // files closed, so every sidecar under the old root is stopped, and has
  // exited, first; they restart on the next open. A local server that is
  // open goes back to the launcher (its process is about to be killed).
  // While the copy runs the launcher shows how far it is (shell:data-move,
  // a few times a second), and the servers being moved do not open.
  ipcMain.handle('shell:set-data-root', shellOnly(async (dir, { move = true } = {}) => {
    if (dataMove) throw new Error('The server data is already being moved.');
    if (!move) {
      const r = await registry.setDataRoot(dir, { move });
      buildMenu();
      pushState();
      return r;
    }
    const moving = new Set(registry.localsUnderRoot().map((w) => w.id));
    dataMove = { ids: moving, name: '', done: 0, total: 0 };
    let sentAt = 0;
    const send = (force) => {
      if (!force && Date.now() - sentAt < 150) return;
      sentAt = Date.now();
      if (content && !content.webContents.isDestroyed() && isShellPage(content.webContents.getURL(), 'launcher')) {
        content.webContents.send('shell:data-move', dataMoveState());
      }
    };
    try {
      if (current && moving.has(current.id)) loadLauncher();
      await Promise.all([...moving].map((id) => sidecar.stop(id)));
      return await registry.setDataRoot(dir, {
        move,
        onProgress: (p) => { Object.assign(dataMove, p); send(false); },
      });
    } finally {
      send(true); // the last count, which the throttle may have held back
      dataMove = null;
      send(true);
      buildMenu();
      pushState();
    }
  }));
  ipcMain.handle('shell:bar-expand', shellOnly((on) => setBarExpanded(on)));
  // An agent's question on this computer: the bar drew the card `key` (its
  // buttons arm a quiet second after), and its answer.
  ipcMain.handle('shell:agent-ask-drawn', barOnly((key) => agentAskBar.agentAskDrawn(String(key || ''))));
  ipcMain.handle('shell:answer-agent-ask', barOnly((key, decision) => agentAskBar.answerAgentAsk(String(key || ''), String(decision || ''))));

  // Theme mirror: the preload on http(s) pages reports data-theme changes.
  ipcMain.on('shell:theme', (event, t) => {
    if (!content || event.sender !== content.webContents) return;
    const clean = typeof t === 'string' && THEMES[t] ? t : '';
    if (clean === theme) return;
    theme = clean;
    registry.setSettings({ lastTheme: clean });
    applyTheme();
    pushState();
  });
}

// ---------------------------------------------------------------- smoke -----
// `electron . --smoke`: headless-ish end-to-end check used by dev + CI.
// Spins up a throwaway local server, waits for health, loads it, verifies
// the auto-login lands, prints one JSON line, exits 0/1.

async function runSmoke() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-smoke-'));
  const ws = {
    id: 'smoke',
    name: 'smoke',
    type: 'local',
    dataDir: tmp,
    adminUser: 'admin',
    adminPassword: 'smoke-test-pass',
  };
  const out = { ok: false };
  try {
    const entry = await sidecar.start(ws, registry.getSettings(), appInfo());
    out.url = entry.url;
    allowedOrigins.add(new URL(entry.url).origin);
    createWindow();
    await content.webContents.loadURL(entry.url);
    const login = await content.webContents.executeJavaScript(
      autoLoginScript(ws.adminUser, ws.adminPassword)
    );
    out.firstLogin = String(login);
    if (String(login) === 'logged-in') {
      await signedInAgain(content.webContents);
      const who = await content.webContents.executeJavaScript(
        `fetch('/api/session',{credentials:'same-origin'}).then(r=>r.json()).then(j=>j.user||'')`
      );
      out.session = String(who);
      out.ok = who === 'admin';
    } else {
      out.ok = String(login).startsWith('already:');
    }
    out.dataDir = fs.readdirSync(tmp).sort();
  } catch (e) {
    out.error = String(e && e.message ? e.message : e);
  } finally {
    // Once it has exited: Windows removes no file the server holds open.
    await sidecar.stop(ws.id);
    try {
      fs.rmSync(tmp, { recursive: true, force: true });
    } catch {}
  }
  process.stdout.write('SMOKE ' + JSON.stringify(out) + '\n');
  app.exit(out.ok ? 0 : 1);
}

// ---------------------------------------------------------- the sections -----
// The shell's sections in lib/ (addresses.js, vaults.js, thisComputer.js,
// syncPanel.js, agentAskBar.js, systemFiles.js) share one context, `ctx`:
// main.js's window and server state as accessors, the functions of main.js
// they call, and every section's exported names, its state through getters.
// A section reaches the rest of the shell only through it (`ctx.openServer`,
// `ctx.busy = …`), so no section requires another; main.js names a
// section's function by its module (`vaults.addFolderVault`). Each section
// is handed the context before the app is ready, and runs only after.
const ctx = {
  TEST_MINIMIZED,
  get win() { return win; },
  get content() { return content; },
  get current() { return current; },
  set current(v) { current = v; },
  get busy() { return busy; },
  set busy(v) { busy = v; },
  get gamma() { return gamma; },
  allowedOrigins,
  get tray() { return tray; },
  createWindow,
  openDirectory,
  revealFile,
  copyFile,
  get barAsked() { return barAsked; },
  setBarExpanded,
  loadLauncher,
  currentWsId,
  serverApi,
  revokeToken,
  revokedToken,
  startLocal,
  reachFolder,
  ensureHost,
  localServer,
  JSON_TYPE,
  shellWord,
  openOrigin,
  localOrigin,
  stillOn,
  pickDirectory,
  pickFile,
  pickPdf,
  ask,
  errorBox,
  showNotice,
  trayMenu,
  showWindow,
  surfaceWindow,
  offerBackground,
  openGammaWorkspace,
  pushState,
  get opening() { return opening; },
  openServer,
  buildMenu,
};
for (const section of [addresses, vaults, thisComputer, syncPanel, agentAskBar, systemFiles]) {
  const { init, ...names } = Object.getOwnPropertyDescriptors(section);
  Object.defineProperties(ctx, names);
  section.init(ctx);
}

// ----------------------------------------------------------------- boot -----

// Whether a start goes hidden into the tray: started at login (`--hidden`,
// or `wasOpenedAtLogin` on macOS, which passes a login item no arguments)
// with the background on, and naming no file or folder (`files`): no
// window, the servers run in the background for the clones, the Zotero
// links and the synced vaults. A start that names one shows it.
function bootHidden(files) {
  return !files.length && Boolean(registry.getSettings().background) && (process.argv.includes('--hidden') ||
    (process.platform === 'darwin' && Boolean((app.getLoginItemSettings() || {}).wasOpenedAtLogin)));
}

app.whenReady().then(async () => {
  if (!primary) return; // quitting: the running app has the command line
  registry.init(app.getPath('userData'));
  openWith = openWithFor.create({
    installed: app.isPackaged && !process.env.GAMMA_SHELL_TEST,
    store: Boolean(process.windowsStore),
    dataDir: app.getPath('userData'),
  });
  readOpenWith();
  // Before any server starts: a server reaches only the folders the app
  // holds when it starts.
  if (process.mas) {
    folderAccess = folderAccessFor.create({
      load: registry.getAccess,
      save: registry.setAccess,
      start: (bookmark) => app.startAccessingSecurityScopedResource(bookmark),
      container: app.getPath('userData'),
    });
    folderAccess.restore();
  }
  if (SMOKE) {
    runSmoke();
    return;
  }
  registerIpc();
  // The certificates trusted by their fingerprint (lib/certs.js), in the
  // window's session; a refusal of a remote's is kept for the launcher's card.
  addresses.trustPins();
  addresses.applyVerifyProc();
  app.on('certificate-error', (event, _wc, url, error, certificate, callback, isMainFrame) =>
    addresses.certificateError(event, url, error, certificate, callback, isMainFrame));
  // What a page may use (lib/permissions.js): a page of this computer as
  // before, through lib/microphone.js; a remote page's own table.
  const allow = permissions.create({
    isLocal: isLocalPage,
    ask: (opts) => ask(opts, 1),
    microphone: microphone.create({ prefs: systemPreferences, onDenied: () => microphoneRefused().catch(() => {}) }),
    hidden: () => Boolean(agentAskBar.shownKey),
  });
  session.defaultSession.setPermissionRequestHandler(allow.request);
  session.defaultSession.setPermissionCheckHandler(allow.check);
  // The notifications of an agent's question carry the installer's id, so
  // Windows shows them (the Store's package has its own).
  if (process.platform === 'win32' && !process.windowsStore) app.setAppUserModelId('io.github.tim4431.gamma');
  updater.init({ onChange: () => pushState(), openExternal });
  buildMenu();
  const settings = registry.getSettings();
  if (settings.background) ensureTray();
  applyLoginItem();
  const files = [...systemFiles.boot(), ...handedOver];
  if (bootHidden(files)) startBackgroundHosts();
  else showWindow().finally(startBackgroundHosts);
  syncPanel.scheduleKeeping(3000); // what the servers keep, once they are up
  if (files.length) systemFiles.openWithGamma(files);

  // The Dock icon: the window back, as Window → Show Gamma brings it.
  app.on('activate', () => showWindow());
});

// A second start (the lock above): the files and folders it names are
// opened, as it parsed them itself (`data.paths`, which no reordering of
// argv by Chromium touches), else as its command line names them (an older
// start passes no data; a packaged one has no app path in it), and the
// window comes forward either way — unless it was a login start.
app.on('second-instance', (_event, argv, cwd, data) => {
  const paths = Array.isArray(data && data.paths) ? data.paths
    : openFiles.openedPaths(openFiles.launchArgs(argv, false), cwd).map((i) => i.path);
  if (paths.length) systemFiles.openWithGamma(paths, cwd);
  else if (systemFiles.booted && !argv.includes('--hidden')) showWindow();
});

// macOS hands a file or a folder over as an event instead (Open With, a drop
// on the Dock icon, `open -a Gamma`), at launch before `ready` too.
app.on('open-file', (event, file) => {
  event.preventDefault();
  systemFiles.openWithGamma([file]);
});

app.on('window-all-closed', () => {
  // In the background the servers keep syncing and the tray brings the window back.
  if (registry.getSettings().background) {
    ensureTray();
    return;
  }
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  dropTray();
  sidecar.stopAll();
});
process.on('exit', () => sidecar.stopAll());

// Test hook (only with GAMMA_SHELL_TEST): lets the e2e driver reach shell
// internals from the main process.
if (process.env.GAMMA_SHELL_TEST) {
  global.__gammaShell = {
    registry,
    sidecar,
    startup,
    openServer,
    openGammaWorkspace,
    openCopy,
    openOriginal,
    keepOffline,
    addFolderVault: vaults.addFolderVault,
    addDirectoryVault: vaults.addDirectoryVault,
    addFileVault: vaults.addFileVault,
    vaultAsk: vaults.vaultAskState,
    answerVault: vaults.answerVault,
    oneAsk,
    shellWord,
    setBarExpanded,
    barExpanded: () => barExpanded,
    syncVault: vaults.syncVault,
    pauseVault: vaults.pauseVault,
    setPull: vaults.setPull,
    setPush: vaults.setPush,
    setAgent: thisComputer.setAgent,
    setAgentRuns: thisComputer.setAgentRuns,
    agentDirs: thisComputer.agentDirs,
    setAgentFromPage: thisComputer.setAgentFromPage,
    agentRunsFromPage: thisComputer.agentRunsFromPage,
    agentDirsFromPage: thisComputer.agentDirsFromPage,
    agentPerson: thisComputer.agentPerson,
    agentPersonFromPage: thisComputer.agentPersonFromPage,
    addLinkedServer: thisComputer.addLinkedServer,
    removeLinkedServer: thisComputer.removeLinkedServer,
    setLinkAsks: thisComputer.setLinkAsks,
    addServerFromPage: thisComputer.addServerFromPage,
    linkAsksFromPage: thisComputer.linkAsksFromPage,
    forceVault: vaults.forceVault,
    goOnVault: vaults.goOnVault,
    locateVault: vaults.locateVault,
    removeVault: thisComputer.removeVault,
    computerName: thisComputer.COMPUTER_NAME,
    revealOnDisk: thisComputer.revealOnDisk,
    relocateFile: thisComputer.relocateFile,
    carryPage: systemFiles.carryPage,
    carryToServer: systemFiles.carryToServer,
    terminalFromPage: thisComputer.terminalFromPage,
    trackedFromPage: vaults.trackedFromPage,
    chooseFiles: vaults.chooseFiles,
    terminals: thisComputer.terminals,
    asked,
    answers,
    // addresses and certificates: e2e.js's self-signed step and main.test.js
    moveServer: addresses.moveServer,
    trustCert: addresses.trustCert,
    forgetCert: addresses.forgetCert,
    trustForSync: addresses.trustForSync,
    certificateRefused: addresses.certificateRefused,
    certificateError: addresses.certificateError,
    certRecord: addresses.certRecord,
    verifyProc: addresses.verifyProc,
    pushPins: addresses.pushPins,
    pinsChanged: addresses.pinsChanged,
    removeServer,
    sentToHttpsBy: addresses.sentToHttpsBy,
    allowedOrigins,
    rerouted: addresses.rerouted,
    upgrades: addresses.upgrades,
    accepted: addresses.accepted,
    launcherUrl: () => (content ? content.webContents.getURL() : ''),
    // main.test.js: the window shows server `srv`, `view` standing in for its
    // content view, `window` (none by default) for the window itself and
    // `barView` (none) for the bar's.
    pretendShown: (srv, view, window = null, barView = null) => {
      current = srv && { id: srv.id, name: srv.name, type: srv.type, url: srv.type === 'local' ? sidecar.status(srv.id).url : srv.url };
      content = view;
      win = window;
      bar = barView;
    },
    // an agent's ask on this computer: main.test.js and e2e.js
    agentAsk: agentAskBar.agentAskState,
    answerAgentAsk: agentAskBar.answerAgentAsk,
    agentAskDrawn: agentAskBar.agentAskDrawn,
    pretendAsks: agentAskBar.pretendAsks,
    pressed: (type) => agentAskBar.barInput(type),
    following: () => (agentAskBar.askFollower ? agentAskBar.askFollower.server : ''),
    stopAsks: agentAskBar.stopAsks,
    surfaced: agentAskBar.surfaced,
    forgetSurfaced: agentAskBar.forgetSurfaced,
    notifications: agentAskBar.notifications,
    askAnswers: agentAskBar.askAnswers,
    isLocalPage,
    trayMenu,
    // main.test.js: the app up (`on`) or not yet, as the boot sets it; → what was handed over meanwhile.
    pretendBooted: systemFiles.boot,
    hintServers: thisComputer.hintServers,
    shellSecret: SHELL_SECRET,
    listFolders: vaults.listFolders,
    refreshKeeping: syncPanel.refreshKeeping,
    keepingAction: syncPanel.keepingAction,
    keeping: () => syncPanel.keepingState,
    startLocal,
    startBackgroundHosts,
    openWithGamma: systemFiles.openWithGamma,
    readFile: systemFiles.readFile,
    whereIs: systemFiles.whereIs,
    broadOk: vaults.broadOk,
    bootHidden,
    waiting: systemFiles.waiting,
    // Gamma's API on the open server in a named workspace, as the shell calls
    // it. A page's own fetch cannot do that: Gamma's wrapper stamps the
    // workspace the page has settled on, or none before it has.
    api: (apiPath, { ws = '', method = 'GET', body } = {}) => serverApi(content.webContents.session)(openOrigin(), apiPath, {
      method,
      headers: { 'Content-Type': 'application/json', ...(ws ? { 'X-Gamma-Workspace': ws } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
    showWindow,
    setBackground,
    tray: () => Boolean(tray),
    hasWindow: () => Boolean(win),
    closeWindow: () => win && win.close(),
    notice: () => notice,
    loadLauncher,
    current: () => current,
    gamma: () => gamma,
    theme: () => currentTheme(),
    externalOpens,
    errorBoxes,
    logPath,
    copied: () => copied,
    copiedFile: () => copiedFile,
    update: () => updater.state(),
    remoteHealth,
    probeRemotes,
    bounds: () => ({ bar: bar && bar.getBounds(), content: content && content.getBounds(),
      contentVisible: content && typeof content.getVisible === 'function' ? content.getVisible() : null }),
  };
}
