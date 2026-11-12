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

const { app, BaseWindow, WebContentsView, Menu, Tray, clipboard, nativeImage, nativeTheme, session, shell, ipcMain, net, dialog, systemPreferences } = require('electron');
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
const { hostPath } = require('./lib/hostPath');
const folderAccessFor = require('./lib/folderAccess');
const microphone = require('./lib/microphone');
const { pages: shellPages, isShellPage, trustedShellSender } = require('./lib/shellSecurity');
const windowControls = require('./lib/windowControls');

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

// One app per profile: a second one would run a second server on the same
// data directories. A second start (a PDF opened from the file manager, the
// Start menu while the app sits in the tray) hands its command line to this
// one (`second-instance`, below) and quits. The smoke test runs beside it.
// The Mac App Store build takes no lock (the submitted build found
// Electron's unusable in the sandbox): Launch Services already runs one
// copy of a store app, handing a PDF opened from the Finder over as
// `open-file`.
const primary = SMOKE || process.mas || app.requestSingleInstanceLock();
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
  // strip and the menu, and a click there closes the menu).
  const barH = barExpanded ? Math.min(h, BAR_H + 420) : BAR_H;
  bar.setBounds({ x: 0, y: 0, width: w, height: barH });
  content.setBounds({ x: 0, y: BAR_H, width: w, height: Math.max(0, h - BAR_H) });
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

function createWindow() {
  const saved = registry.getWindowBounds();
  const p = palette();
  win = new BaseWindow({
    show: !TEST_MINIMIZED,
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
  win.on('focus', hintServers);
  if (TEST_MINIMIZED) {
    for (const v of [bar, content]) v.webContents.on('did-navigate', () => { sizedViews.add(v); emulateViewSize(v); });
  }

  bar.webContents.loadFile(path.join(__dirname, 'ui', 'bar.html'));
  bar.webContents.on('did-finish-load', pushState);
  const guardBar = (event, url) => { if (!isShellPage(url, 'bar')) event.preventDefault(); };
  bar.webContents.on('will-navigate', guardBar);
  bar.webContents.on('will-redirect', guardBar);
  bar.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));

  const cwc = content.webContents;
  const guard = (event, url) => {
    let origin = null;
    try {
      origin = new URL(url).origin;
    } catch {}
    // The page's Gamma Cloud sign-in runs in the system browser and comes
    // back here (lib/cloudSignIn.js).
    if (origin && allowedOrigins.has(origin) && cloudSignIn.isStart(url)) {
      event.preventDefault();
      signInWithCloud(url);
      return;
    }
    if (isShellPage(url, 'launcher') || (origin && allowedOrigins.has(origin))) return;
    event.preventDefault();
    if (/^https?:/.test(url) || url === STORE_UPDATES_URL) openExternal(url);
  };
  cwc.on('will-navigate', guard);
  cwc.on('will-redirect', guard);
  // target=_blank (external link chips, share links, papers opened in a new
  // tab) goes to the system browser; the Store's updates page (Settings →
  // Server in a Store install) to the Store.
  cwc.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url) || url === STORE_UPDATES_URL) openExternal(url);
    return { action: 'deny' };
  });
  cwc.on('page-title-updated', (_e, title) => {
    if (win) win.setTitle(title || 'Gamma');
  });
  cwc.on('did-navigate', () => { pushState(); refreshGamma(); });
  cwc.on('did-navigate-in-page', () => { pushState(); refreshGamma(); });
  cwc.on('did-finish-load', refreshGamma);
  // (not while the vault dialog is up: setBarExpanded keeps it shown)
  cwc.on('focus', () => {
    if (barExpanded) setBarExpanded(false);
  });

  // Downloads (backup zips, exports) go through the normal save dialog;
  // tests get them saved straight into a folder.
  cwc.session.on('will-download', (_e, item) => {
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
    if (vaultAsk) answerVault(vaultAsk.id, null); // its panel went with the window
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

// The bar view over the content while a dropdown is open. The vault dialog
// (askVault) keeps it expanded until it is answered: a click in the page
// meanwhile (a look at the folder) must not hide a dialog that still waits,
// so only its answer or its cancel (answerVault) shrinks the bar.
function setBarExpanded(on) {
  barExpanded = Boolean(on) || Boolean(vaultAsk);
  layout();
}

// The launcher's `?error=` payload: a JSON object when the failure came with a
// diagnosis (lib/startup.js — a local server that would not come up), so the
// page can explain it and offer the fix; a plain message otherwise.
function launcherError(error, serverId) {
  const d = (error && error.startup) || startup.diagnoseOpen((error && error.message) || error);
  return JSON.stringify({ ...d, server: serverId || '' });
}

function loadLauncher(error, serverId) {
  if (!win) return;
  cancelVaultAsk();
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
  const remoteOrigin = new URL(current.url).origin;
  const known = registry.findMirror(remoteOrigin, wsId);
  if (known && registry.get(known.server)) return openCopy(wsId); // one copy per workspace: open it
  const ses = content.webContents.session;
  const api = serverApi(ses);
  busy = `Cloning ${ws.name}…`;
  pushState();
  let token = null;
  const dropToken = () => token && revokeToken(api, remoteOrigin, wsId, token.id);
  try {
    token = await api(remoteOrigin, '/api/integrations/tokens', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Gamma-Workspace': wsId },
      body: JSON.stringify({ name: 'Gamma desktop offline copy', scope: 'write', expires_in_days: 365 }),
    });
    const { local, origin: localOrigin } = await ensureHost(api);
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
    refreshKeeping().catch(() => {});
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
// refusal throws with its `status` and its `body`.
function serverApi(ses) {
  return async (origin, apiPath, init) => {
    const r = await ses.fetch(origin + apiPath, { credentials: 'include', cache: 'no-store', ...init });
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
  const local = hostOf();
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

// ------------------------------------------------------- synced vaults -----
// A synced vault (docs/research/vaults.md): a folder of a workspace and a
// directory on this computer kept in step, or a page and one PDF file, by a
// local server (docs/dev/synced_vaults.md "The desktop app").
// Gamma's server is the remote, the directory or the file is local; a vault
// pulls (Gamma to disk), pushes (disk to Gamma: nothing, its PDFs, or
// everything), or both. On a LOCAL server the vault is that server's own and
// reads the workspace in-process. A REMOTE server's folder is synced by the
// host local server (ensureHost) with a token minted on the remote, read
// over HTTP: no clone, only the folder's files come down. The shell asks for
// the directory or the file (the native picker; GAMMA_SHELL_PICK_DIR and
// GAMMA_SHELL_PICK_FILE in tests), shows what the server found there and
// what the first round would do (the vault dialog, a view of the sync panel:
// askVault), mints the token, and makes the vault through the servers'
// public API. Only the shell names a path: the routes that take one want its
// word (shellWord, X-Gamma-Shell). The ways in: a folder's "Sync with this
// computer…" in Gamma's page and the panel's chooser (addFolderVault), "Add a
// synced vault…" in the launcher and the panel (addDirectoryVault), a page's
// "Push from a file on this computer…" (addFileVault). A work folder (a vault
// of a local server's folder that pushes everything, docs/dev/work_folders.md:
// a directory added, or an empty folder synced with one) asks nothing here:
// the shell makes it paused and choosing, then opens the server's window on
// its folder with its tracking dialog (`?track=`, makeWorkFolder), where the
// person chooses what it tracks; the page answers through the server, never
// through the shell. The rounds are the server's; the sync panel (below, "the
// sync panel") shows each vault and acts on it. In the Mac App Store build the
// server that is to read or write there first starts again when it started
// before the pick (reachFolder).

const JSON_TYPE = { 'Content-Type': 'application/json' };

// The headers of a route that takes a path on this computer: the shell's
// word, for a running local server of this app only. Asked with the origin
// the request goes to, so neither the word nor a path ever reaches a remote
// server, whatever the window opened while a picker was up.
function shellWord(origin) {
  const local = registry.load().servers.some((s) => {
    const entry = s.type === 'local' && sidecar.status(s.id);
    return entry && new URL(entry.url).origin === origin;
  });
  if (!local) throw new Error('A path on this computer goes only to a server on this computer.');
  return { 'X-Gamma-Shell': SHELL_SECRET };
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

// The vaults of the open server's workspace, of folders and of files: a
// local server's own, or the host's that sync this remote's workspace.
// `server` names the local server holding each. No local server yet: none.
async function listLinks(api, wsId) {
  const origin = openOrigin();
  if (current.type === 'local') {
    const all = (await api(origin, '/api/folder-links')).links || [];
    return all.filter((l) => !l.remote_url && l.workspace_id === wsId).map((l) => ({ ...l, server: current.id }));
  }
  if (!registry.load().servers.some((s) => s.type === 'local')) return [];
  const host = await ensureHost(api);
  const all = (await api(host.origin, '/api/folder-links')).links || [];
  return all.filter((l) => l.remote_url === origin && l.workspace_id === wsId).map((l) => ({ ...l, server: host.local.id }));
}

// The open workspace's folders, and its folders' vaults (not its files').
async function listFolders(wsId) {
  const api = serverApi(content.webContents.session);
  const [tree, links] = await Promise.all([
    api(openOrigin(), '/api/sync/folders', { headers: { 'X-Gamma-Workspace': wsId } }),
    listLinks(api, wsId),
  ]);
  return { folders: tree.folders || [], links: links.filter((l) => l.kind !== 'file') };
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

function pickDirectory(title, message) {
  if (process.env.GAMMA_SHELL_PICK_DIR) return process.env.GAMMA_SHELL_PICK_DIR;
  return choosePath({ title, message, buttonLabel: 'Choose', properties: ['openDirectory', 'createDirectory'], defaultPath: app.getPath('documents') });
}

function pickPdf(title, message, buttonLabel = 'Choose') {
  if (process.env.GAMMA_SHELL_PICK_FILE) return process.env.GAMMA_SHELL_PICK_FILE;
  return choosePath({
    title, message, buttonLabel, properties: ['openFile'], filters: [{ name: 'PDF', extensions: ['pdf'] }], defaultPath: app.getPath('documents'),
  });
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

// The vault dialog, a view of the sync panel (ui/bar.html), which answers
// through shell:answer-vault: the directory, what the server found there
// (`preview`, POST /api/folder-links/preview), the switches Pull and Push,
// and what the first round would do, a sentence for each setting of them
// (keeping.firstRound; one that is not `ok` shuts Sync). `view`: `{ path,
// server, workspace, folder, newFolder, remote, preview, pull, push }`, the
// last two the switches as they start. One at a time: a newer one cancels
// the one shown. Resolves to `{ pull, push }`, or null when cancelled. While
// it is up the bar stays expanded on it (setBarExpanded), the page's asks
// wait (fromPage), and the window going to the launcher or to another server
// cancels it (cancelVaultAsk): it was asked about what the window showed.
let vaultAsk = null; // the dialog shown, with its resolve
let vaultAsks = 0;
function askVault(view) {
  if (vaultAsk) vaultAsk.resolve(null);
  return new Promise((resolve) => {
    const sentences = {};
    for (const pull of [true, false]) {
      for (const push of keeping.PUSH) sentences[`${pull}:${push}`] = keeping.firstRound({ ...view, pull, push });
    }
    vaultAsk = { ...view, id: ++vaultAsks, sentences, resolve };
    setBarExpanded(true);
    pushState();
  });
}

// The dialog answered: true when `id` is the one shown and the answer one
// its sentences allow (or null, a cancel).
function answerVault(id, answer) {
  if (!vaultAsk || vaultAsk.id !== id) return false;
  const choice = answer ? { pull: Boolean(answer.pull), push: keeping.pushWord(answer.push) } : null;
  if (choice && !(vaultAsk.sentences[`${choice.pull}:${choice.push}`] || {}).ok) return false;
  const { resolve } = vaultAsk;
  vaultAsk = null;
  setBarExpanded(false);
  pushState();
  resolve(choice);
  return true;
}

function cancelVaultAsk() {
  if (vaultAsk) answerVault(vaultAsk.id, null);
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

// The dialog as the bar renders it.
function vaultAskState() {
  if (!vaultAsk) return null;
  const { resolve: _resolve, ...view } = vaultAsk;
  return view;
}

// What making a vault of `dir` would find and write, writing nothing, on the
// local server at `origin` (`headers`: the workspace it would be made in).
function previewVault(api, origin, headers, dir, folder) {
  return api(origin, '/api/folder-links/preview', {
    method: 'POST', headers: { ...headers, ...JSON_TYPE, ...shellWord(origin) }, body: JSON.stringify({ path: dir, folder: folder || '', kind: 'folder' }),
  });
}

// A directory holding the marker of a vault no link holds any more takes it up again.
const adopts = (preview) => Boolean(preview.marker && !preview.marker.link_id);

// How a folder's vault starts: pulling, and pushing nothing while the folder
// has pages; an empty folder and a directory with notes or PDFs push
// everything (never to another server, for now).
function firstPush(preview, remote) {
  return !remote && !preview.pages && (preview.notes || preview.pdfs) ? 'everything' : 'off';
}

// The pages a folder of a remote server holds, for the dialog's sentence (the
// host's preview cannot count them). Best effort: 0.
async function remotePages(api, origin, headers, folderId) {
  try {
    return ((await api(origin, `/api/sync/folders/${encodeURIComponent(folderId)}`, { headers })).pages || []).length;
  } catch {
    return 0;
  }
}

// A work folder made from the directory `picked` on the local server `srv`
// (at `origin`, through `api`) in its workspace `ws` (`headers`), into a new
// folder named `name` or the folder `body.folder`: what `preview` refuses (a
// marker another vault holds among it) is said in the bar and nothing is
// made; else the vault is made paused and choosing, and the server's window,
// opened when another shows, goes to its folder with its tracking dialog,
// which saves what it tracks and runs its first round. → the link, or null.
async function makeWorkFolder({ srv, origin, api, ws, headers, name, picked, preview, body }) {
  if (preview.refusal) {
    showNotice(preview.refusal);
    return null;
  }
  busy = `Adding the work folder ${name}…`;
  pushState();
  let link;
  try {
    link = await api(origin, '/api/folder-links', {
      method: 'POST',
      headers: { ...JSON_TYPE, ...shellWord(origin), ...headers },
      body: JSON.stringify({ path: picked, ...body, pull: true, push: 'everything', choose: true, adopt: adopts(preview) }),
    });
  } finally {
    busy = null;
    pushState();
  }
  if (!current || current.id !== srv.id) {
    await openServer(srv.id);
    buildMenu();
  }
  await openGammaWorkspace(ws.id, { folder: link.folder_id, track: link.id });
  refreshKeeping().catch(() => {});
  offerBackground(`The work folder “${name}”`).catch(() => {});
  return link;
}

// Folder `folderId` of the open server's workspace `wsId` synced with a
// directory the user picks, as the dialog says; an empty folder of a local
// server that the directory's notes and PDFs would fill (firstPush) becomes
// a work folder instead (makeWorkFolder), with no dialog here. The whole
// library is never one (keeping.WHOLE_RULE): its dialog starts pulling
// only. A folder that has a vault already opens its directory instead.
async function addFolderVault(wsId, folderId) {
  const origin = openOrigin();
  const shown = current; // the server it is asked of, whatever the window opens while the picker is up
  const remote = shown.type === 'remote';
  const serverName = shown.name;
  const api = serverApi(content.webContents.session);
  const g = gamma;
  const wsName = (g && (g.list.find((w) => w.id === wsId) || {}).name) || 'Library';
  const wsHeaders = { 'X-Gamma-Workspace': wsId };
  const tree = await api(origin, '/api/sync/folders', { headers: wsHeaders });
  const folder = folderId === 'root' ? { path: [] } : (tree.folders || []).find((f) => f.id === folderId);
  if (!folder) throw new Error('Unknown folder');
  const name = folder.path.length ? folder.path[folder.path.length - 1] : wsName;
  const synced = (await listLinks(api, wsId)).find((l) => l.kind !== 'file' && l.folder_id === folderId);
  if (synced) {
    openDirectory(synced.dest);
    showNotice(`${name} is already synced with ${synced.dest}`);
    return synced;
  }
  const picked = await pickDirectory(`Sync “${name}” with this computer`,
    `Choose the directory “${name}” is synced with: an empty one, or one whose notes and PDFs it takes.`);
  if (!picked) return null;
  stillOn(shown);
  // The server that keeps the vault: the open one, or the host for a remote's folder.
  let at = remote ? await ensureHost(api) : { local: registry.get(shown.id), origin };
  if (await reachFolder(at.local, picked)) at = remote ? await ensureHost(api) : { local: at.local, origin: localOrigin(at.local) };
  const preview = await previewVault(api, at.origin, remote ? {} : wsHeaders, picked, remote ? '' : folderId);
  if (remote) preview.pages = await remotePages(api, origin, wsHeaders, folderId);
  const whole = folderId === 'root';
  if (!whole && firstPush(preview, remote) === 'everything') {
    stillOn(shown);
    return makeWorkFolder({ srv: at.local, origin: at.origin, api, ws: { id: wsId, name: wsName }, headers: wsHeaders, name, picked,
      preview, body: { folder: folderId } });
  }
  const choice = await askVault({ path: picked, server: serverName, workspace: wsName, folder: name, newFolder: false, remote, whole,
    preview, pull: true, push: whole ? 'off' : firstPush(preview, remote) });
  if (!choice) return null;
  busy = `Syncing ${name} with this computer…`;
  pushState();
  let token = null;
  try {
    const body = { folder: folderId, path: picked, notes: true, pull: choice.pull, push: choice.push };
    let link;
    if (remote) {
      // A token on the remote for the host to read the folder with, one that
      // may write when the vault pushes; the vault on the host.
      token = await api(origin, '/api/integrations/tokens', {
        method: 'POST',
        headers: { ...JSON_TYPE, ...wsHeaders },
        body: JSON.stringify({ name: `Gamma desktop: ${name} synced vault`.slice(0, 80), scope: choice.push === 'off' ? 'read' : 'write', expires_in_days: 365 }),
      });
      link = await api(at.origin, '/api/folder-links', {
        method: 'POST',
        headers: { ...JSON_TYPE, ...shellWord(at.origin) },
        body: JSON.stringify({ ...body, remote_url: origin, workspace: wsId, token: token.token, token_id: token.id }),
      });
      token = null; // the host holds it now
      registry.setSettings({ folderHost: at.local.id }); // runs at launch with the clones' hosts
    } else {
      stillOn(shown);
      link = await api(at.origin, '/api/folder-links', {
        method: 'POST',
        headers: { ...JSON_TYPE, ...shellWord(at.origin), ...wsHeaders },
        body: JSON.stringify({ ...body, adopt: adopts(preview) }),
      });
    }
    showNotice(`${name} is synced with ${link.dest}`);
    offerBackground(`The synced vault “${name}”`).catch(() => {});
    refreshKeeping().catch(() => {});
    return link;
  } catch (e) {
    if (token) await revokeToken(api, origin, wsId, token.id); // minted for a vault that was never made
    throw e;
  } finally {
    busy = null;
    pushState();
  }
}

// The host: the first local server, made when there is none.
function hostOf() {
  return registry.load().servers.find((s) => s.type === 'local') || registry.addLocal('Local');
}

// The host, started, and signed into in its own session (localServer).
async function startHost() {
  const local = hostOf();
  const entry = await startLocal(local);
  allowedOrigins.add(new URL(entry.url).origin);
  return localServer(local.id);
}

// Where a vault made from a directory lands (openFiles.readerPlace): `{ srv,
// origin, api, ws }`, the open local server through the window's session in
// the workspace the window shows when the account writes there, it is no
// clone and no one else sees it (else the account's own there), else the
// host in the account's personal workspace. A shared workspace takes a
// directory through its folder's "Sync with this computer…", which names it.
// `wsId`: the workspace asked in, in place of the one the window shows.
async function vaultPlace(wsId) {
  if (current && current.type === 'local' && content) {
    const origin = openOrigin();
    const api = serverApi(content.webContents.session);
    const place = openFiles.readerPlace(await api(origin, '/api/session'), wsId || currentWsId());
    const ws = place && (place.audience ? place.mine : place.ws);
    if (ws) return { srv: registry.get(current.id), origin, api, ws };
  }
  const host = await startHost();
  const ws = openFiles.personalOf(await host.api(host.origin, '/api/session'));
  if (!ws) throw new Error(`Your account on ${host.srv.name} has no workspace of its own.`);
  return { ...host, ws };
}

// "Add a synced vault…" (the launcher, the sync panel): a directory the user
// picks becomes a work folder, a new folder named after it whose files the
// person chooses in its tracking dialog (makeWorkFolder). No server need be
// open (vaultPlace). `dir`, the directory already chosen (oneByOne's "Sync
// the folder…"), asks for none; `wsId`, the workspace it was asked in there.
async function addDirectoryVault(dir, wsId) {
  const picked = dir || await pickDirectory('Add a synced vault',
    'Choose a directory: it becomes a folder of Gamma named after it, and you choose the files it tracks.');
  if (!picked) return null;
  let place = await vaultPlace(wsId);
  if (await reachFolder(place.srv, picked)) place = await vaultPlace(wsId);
  const { srv, origin, api, ws } = place;
  const headers = { 'X-Gamma-Workspace': ws.id };
  const name = path.basename(picked) || picked;
  const preview = await previewVault(api, origin, headers, picked, '');
  return makeWorkFolder({ srv, origin, api, ws, headers, name, picked, preview, body: { new_folder: true } });
}

// Page `pageId` of the open server's workspace `wsId` pushed from a PDF the
// user picks: a vault of one file, which only pushes (each new version of
// the file becomes the page's PDF as "Replace the PDF…" makes it, the
// highlights moved to where their text is) and never writes the file. On a
// REMOTE server the vault is the host's, with a token minted there that may
// write. A page that has one already offers to show the file or to remove
// the vault.
async function addFileVault(wsId, pageId) {
  const origin = openOrigin();
  const shown = current; // the server it is asked of, whatever the window opens while the picker is up
  const remote = shown.type === 'remote';
  const api = serverApi(content.webContents.session);
  const wsHeaders = { 'X-Gamma-Workspace': wsId };
  const page = await api(origin, `/api/blocks/${encodeURIComponent(pageId)}`, { headers: wsHeaders });
  const title = page.content || 'Untitled';
  const synced = (await listLinks(api, wsId)).find((l) => l.kind === 'file' && l.page_id === pageId);
  if (synced) {
    const r = await ask({
      type: 'question', buttons: ['Show in folder', 'Remove the vault…', 'Cancel'], defaultId: 0, cancelId: 2,
      message: `“${title}” is pushed from ${synced.dest}`,
      detail: 'Each new version of the file becomes its PDF.',
    }, 0);
    if (r === 0) revealFile(synced.dest);
    if (r === 1) await removeVault(synced.server, synced.id);
    return synced;
  }
  const picked = await pickPdf(`Push “${title}” from a file`, `Choose a PDF on this computer. Each time it changes, it becomes the PDF of “${title}”.`, 'Push from it');
  if (!picked) return null;
  if (!remote) {
    const way = await oneByOne(api, localOrigin(registry.get(shown.id)), wsId, picked);
    if (!way) return null;
    stillOn(shown);
    if (way === 'folder') return addDirectoryVault(path.dirname(picked), wsId);
  }
  const file = path.basename(picked);
  busy = `Pushing ${file}…`;
  pushState();
  let token = null;
  try {
    let link;
    if (remote) {
      // A write token on the remote, for the host to replace the page's PDF with; the vault on the host.
      token = await api(origin, '/api/integrations/tokens', {
        method: 'POST',
        headers: { ...JSON_TYPE, ...wsHeaders },
        body: JSON.stringify({ name: `Gamma desktop: ${title} from ${file}`.slice(0, 80), scope: 'write', expires_in_days: 365 }),
      });
      let host = await ensureHost(api);
      if (await reachFolder(host.local, picked)) host = await ensureHost(api);
      link = await api(host.origin, '/api/folder-links', {
        method: 'POST',
        headers: { ...JSON_TYPE, ...shellWord(host.origin) },
        body: JSON.stringify({ kind: 'file', page: pageId, path: picked, remote_url: origin, workspace: wsId, token: token.token, token_id: token.id }),
      });
      token = null; // the host holds it now
      registry.setSettings({ folderHost: host.local.id }); // runs at launch with the clones' hosts
    } else {
      stillOn(shown);
      const local = registry.get(shown.id);
      await reachFolder(local, picked);
      stillOn(shown);
      // its origin now: reachFolder may have restarted the server on another port
      const at = localOrigin(local);
      link = await api(at, '/api/folder-links', {
        method: 'POST',
        headers: { ...JSON_TYPE, ...shellWord(at), ...wsHeaders },
        body: JSON.stringify({ kind: 'file', page: pageId, path: picked }),
      });
    }
    showNotice(`“${title}” is pushed from ${file}: each new version becomes its PDF.`);
    offerBackground(`“${title}”`).catch(() => {});
    refreshKeeping().catch(() => {});
    return link;
  } catch (e) {
    if (token) await revokeToken(api, origin, wsId, token.id); // minted for a vault that was never made
    throw e;
  } finally {
    busy = null;
    pushState();
  }
}

// A path as the file system tells paths apart: by case only on Linux.
const samePath = (p) => (process.platform === 'linux' ? path.resolve(p) : path.resolve(p).toLowerCase());

// The one hint about vaults of one file (docs/research/vaults.md "A vault of
// one file, and .gamma/"): the vault of `file` would be the third or a later
// one of the files of its directory in workspace `wsId` of the local server
// at `origin`, so the question offers the directory's vault instead, which
// takes the files' pages in. → 'file' (push this file only), 'folder' (sync
// the folder), or null (cancelled). Only a local server's own vaults, of a
// workspace a directory's vault would land in (vaultPlace: no clone, no one
// else sees it): a directory's vault of a remote's pages, or of a shared
// workspace's, is a folder of its, which the folder's "Sync with this
// computer…" makes.
async function oneByOne(api, origin, wsId, file) {
  const place = openFiles.readerPlace(await api(origin, '/api/session'), wsId);
  if (!place || place.audience || place.ws.id !== wsId) return 'file';
  const dir = path.dirname(file);
  const beside = ((await api(origin, '/api/folder-links')).links || []).filter((l) => l.kind === 'file' && !l.remote_url &&
    l.workspace_id === wsId && l.dest && samePath(l.dest) !== samePath(file) && samePath(path.dirname(l.dest)) === samePath(dir));
  if (beside.length < 2) return 'file';
  const r = await ask({
    type: 'question', buttons: ['Sync the folder…', 'Push this file only', 'Cancel'], defaultId: 0, cancelId: 2,
    message: `${beside.length + 1} files in ${dir} are synced one by one. Sync the folder instead?`,
    detail: 'A synced vault of the folder takes these files’ pages in, with their notes and highlights, and keeps every PDF and note in it in step, the ones added later too.',
  }, 1);
  return ['folder', 'file', null][r] ?? null;
}

// A vault of local server `serverId`, with what to call it through, and as
// the panel reads it (`item`, lib/keeping.js).
async function vaultOn(serverId, linkId) {
  const at = await localServer(serverId);
  const link = ((await at.api(at.origin, '/api/folder-links')).links || []).find((l) => l.id === linkId);
  if (!link) throw new Error('That vault is no longer synced with this computer');
  // A vault of the server's own workspace is changed in that workspace; one of a remote's is the account's.
  return { ...at, link, item: keeping.vault(at.srv, link, registry.load().servers),
    headers: link.remote_url ? {} : { 'X-Gamma-Workspace': link.workspace_id } };
}

// A round of a link asked for and followed to its end: the server answers
// at once with the round's `running` set (the one it started, or one
// already running, after which the round asked for runs, the link reading
// `running` until that one ends), and the link is read again every
// ROUND_POLL_MS until `running` comes down, however long the round takes:
// the server reads `running` from the round's lock, so a mark a server
// stopped mid-round left never reads as a round (gamma/folder_links.py
// _round_held). No request stays open for the round. → the link after it.
const ROUND_POLL_MS = 1_000;
async function followRound(api, origin, link, headers, query) {
  let after = await api(origin, `/api/folder-links/${link.id}/sync?${query}`, { method: 'POST', headers });
  while (after.status && after.status.running) {
    await new Promise((done) => setTimeout(done, ROUND_POLL_MS));
    after = await api(origin, `/api/folder-links/${link.id}`, { headers });
  }
  return after;
}

// A round of a vault now (`query`: its direction, a force, the guard passed),
// followed to its end (followRound), its outcome in the bar.
async function roundOf(at, query, doing) {
  const { origin, api, link, item, headers } = at;
  busy = doing;
  pushState();
  try {
    const after = await followRound(api, origin, link, headers, query);
    showNotice(keeping.roundSaid(item, after.status));
    return after;
  } finally {
    busy = null;
    pushState();
    refreshKeeping().catch(() => {});
  }
}

// Sync now (`both`: push, then pull), Pull now or Push now. A paused vault
// syncs too and stays paused.
async function syncVault(serverId, linkId, direction = 'both') {
  const at = await vaultOn(serverId, linkId);
  const doing = direction === 'pull' ? 'Pulling' : direction === 'push' || at.item.scope === 'file' ? 'Pushing' : 'Syncing';
  return roundOf(at, `direction=${direction}`, `${doing} ${at.item.name}…`);
}

// Force pull (the files become what Gamma holds) or force push (the pages
// become what their files say), after a count and a confirmation;
// `confirmed` answers it in tests, where the question takes Cancel.
async function forceVault(serverId, linkId, which, confirmed) {
  const at = await vaultOn(serverId, linkId);
  if (!confirmed) {
    const removal = await at.api(at.origin, `/api/folder-links/${at.link.id}/removal`, { headers: at.headers });
    const q = keeping.forceAsk(at.item, which, removal);
    if (await ask({ type: 'warning', message: q.message, detail: q.detail, buttons: q.buttons, defaultId: 1, cancelId: 1 }, 1) !== 0) return null;
  }
  return roundOf(at, `force=${which}`, `Force ${which === 'pull' ? 'pulling' : 'pushing'} ${at.item.name}…`);
}

// A vault its guard held (many files gone from the directory at once) goes
// on, once the user says so; `confirmed` answers it in tests.
async function goOnVault(serverId, linkId, confirmed) {
  const at = await vaultOn(serverId, linkId);
  if (!confirmed) {
    const q = keeping.goOnAsk(at.item);
    if (await ask({ type: 'question', message: q.message, detail: q.detail, buttons: q.buttons, defaultId: 0, cancelId: 1 }, 1) !== 0) return null;
  }
  return roundOf(at, 'confirm=1', `Syncing ${at.item.name}…`);
}

// Pause a vault, or resume it (a round runs then). Paused, the server's loop
// leaves it alone; a sync asked for still runs.
async function pauseVault(serverId, linkId, paused) {
  const { origin, api, link, item, headers } = await vaultOn(serverId, linkId);
  await api(origin, `/api/folder-links/${link.id}`, {
    method: 'PATCH', headers: { ...headers, ...JSON_TYPE }, body: JSON.stringify({ paused }),
  });
  showNotice(paused ? `${item.name} is paused: nothing is pulled or pushed until you resume or sync it.` : `${item.name} syncs again.`);
  refreshKeeping().catch(() => {});
  return true;
}

// Whether a vault pulls: Gamma's changes written to its directory. A vault
// of another server keeps pulling (keeping.REMOTE_RULE).
async function setPull(serverId, linkId, pull) {
  const { origin, api, link, item, headers } = await vaultOn(serverId, linkId);
  if (!pull && link.remote_url) throw new Error(keeping.REMOTE_RULE);
  await api(origin, `/api/folder-links/${link.id}`, {
    method: 'PATCH', headers: { ...headers, ...JSON_TYPE }, body: JSON.stringify({ pull }),
  });
  showNotice(pull ? `${item.name} pulls Gamma's changes again.` : `${item.name} no longer pulls: its files stay as they are.`);
  refreshKeeping().catch(() => {});
  return true;
}

// A work folder's library switch: whether the PDFs and notes it finds from
// now on are added to the library too (the files it holds keep theirs).
async function setLibrary(serverId, linkId, on) {
  const { origin, api, link, item, headers } = await vaultOn(serverId, linkId);
  await api(origin, `/api/folder-links/${link.id}`, {
    method: 'PATCH', headers: { ...headers, ...JSON_TYPE }, body: JSON.stringify({ library: on }),
  });
  showNotice(on ? `The PDFs and notes ${item.name} finds from now on are added to the library too.`
    : `The PDFs and notes ${item.name} finds from now on stay in the work folder only.`);
  refreshKeeping().catch(() => {});
  return true;
}

// Choose files…: the server's window on the work folder, its tracking dialog
// open (makeWorkFolder's navigation).
async function chooseFiles(serverId, linkId) {
  const { link } = await vaultOn(serverId, linkId);
  if (!current || current.id !== serverId) {
    await openServer(serverId);
    buildMenu();
  }
  await openGammaWorkspace(link.workspace_id, { folder: link.folder_id, track: link.id });
  return true;
}

// What a vault pushes: 'off', 'pdfs' or 'everything' (the Push segment of
// the vault's own view); a remote server's folder PDFs at most
// (keeping.REMOTE_RULE), the whole library not everything
// (keeping.WHOLE_RULE). A folder of a remote server needs a write token
// for it, where it was made with a read one: a write token is minted on the
// remote with the window's session (signed into the remote, as for
// revokeToken) and handed to the host in place of the old one, which is
// then revoked; the new one is revoked instead when the host refuses it, or
// when the vault the host answers with holds another token. One change per
// vault at a time: a second click while one runs is ignored, so no two
// write tokens are minted for one vault.
const pushing = new Set();
async function setPush(serverId, linkId, word) {
  const key = `${serverId}:${linkId}`;
  if (pushing.has(key)) return false;
  pushing.add(key);
  try {
    return await changePush(serverId, linkId, word);
  } finally {
    pushing.delete(key);
  }
}

const PUSH_SAID = {
  off: (name) => `${name} no longer pushes.`,
  pdfs: (name) => `${name} pushes the PDFs you change there.`,
  everything: (name) => `${name} pushes everything: notes, PDFs, new files, renames and deletions.`,
};

async function changePush(serverId, linkId, word) {
  if (!keeping.PUSH.includes(word)) throw new Error(`Push is off, pdfs or everything, not ${word}`);
  const { origin, api, link, item, headers } = await vaultOn(serverId, linkId);
  if (word === 'everything' && link.remote_url) throw new Error(keeping.REMOTE_RULE);
  if (word === 'everything' && link.folder_id === 'root') throw new Error(keeping.WHOLE_RULE);
  const patch = (body) => api(origin, `/api/folder-links/${link.id}`, {
    method: 'PATCH', headers: { ...headers, ...JSON_TYPE }, body: JSON.stringify(body),
  });
  if (word !== 'off' && link.remote_url && keeping.pushWord(link.push) === 'off') {
    const remote = serverApi(session.defaultSession);
    const remoteOrigin = new URL(link.remote_url).origin;
    const token = await remote(remoteOrigin, '/api/integrations/tokens', {
      method: 'POST',
      headers: { ...JSON_TYPE, 'X-Gamma-Workspace': link.workspace_id },
      body: JSON.stringify({ name: `Gamma desktop: ${item.name} synced vault`.slice(0, 80), scope: 'write', expires_in_days: 365 }),
    });
    let after;
    try {
      after = await patch({ push: word, token: token.token, token_id: token.id });
    } catch (e) {
      await revokeToken(remote, remoteOrigin, link.workspace_id, token.id);
      throw e;
    }
    if (after && after.token_id && after.token_id !== token.id) {
      await revokeToken(remote, remoteOrigin, link.workspace_id, token.id); // another change won: ours is no vault's
    } else if (link.token_id && link.token_id !== token.id) {
      await revokeToken(remote, remoteOrigin, link.workspace_id, link.token_id);
    }
  } else {
    await patch({ push: word });
  }
  showNotice(PUSH_SAID[word](item.name));
  refreshKeeping().catch(() => {});
  return true;
}

// Locate…: a vault found where it is now, a directory that moved or a disk
// mounted elsewhere (its marker must be the vault's), or the file of a
// vault of one file that moved; a round follows there.
async function locateVault(serverId, linkId) {
  const { srv, origin, api, link, item, headers } = await vaultOn(serverId, linkId);
  const picked = item.scope === 'file'
    ? await pickPdf(`Where is ${path.basename(item.dest)} now?`, `Choose the PDF “${item.name}” is pushed from.`)
    : await pickDirectory(`Where is “${item.name}” now?`, `Choose the directory of the synced vault “${item.name}”, wherever it is now.`);
  if (!picked) return null;
  let at = { origin, api };
  if (await reachFolder(srv, picked)) at = await localServer(serverId);
  const after = await at.api(at.origin, `/api/folder-links/${link.id}/relink`, {
    method: 'POST', headers: { ...headers, ...JSON_TYPE, ...shellWord(at.origin) }, body: JSON.stringify({ path: picked }),
  });
  showNotice(`${item.name} is synced with ${after.dest} from now on.`);
  refreshKeeping().catch(() => {});
  return after;
}

// A vault of a remote server is gone: the token the shell minted for it is
// revoked there, with the window's session, which is signed into the
// remote, and once the host keeps nothing more of remote servers it need
// not start at launch for them.
async function releaseRemote(origin, api, serverId, link) {
  if (link.token_id) await revokeToken(serverApi(session.defaultSession), new URL(link.remote_url).origin, link.workspace_id, link.token_id);
  const left = ((await api(origin, '/api/folder-links')).links || []).filter((l) => l.remote_url);
  if (!left.length && registry.getSettings().folderHost === serverId) registry.setSettings({ folderHost: '' });
}

// Remove the vault…: its pages stay (the PDFs it read where they are stored
// in Gamma first), and the token the shell minted on a remote for it is
// revoked there. Asked first with GET .../removal's counts, unless `files`
// says: `keep`, the directory's files stay as they are; `trash`, the files
// the sync wrote move to the vault's trash (.gamma/trash). A file's vault
// never touches its file.
async function removeVault(serverId, linkId, files) {
  const { origin, api, link, item, headers } = await vaultOn(serverId, linkId);
  if (!files) {
    const q = keeping.removalAsk(item, await api(origin, `/api/folder-links/${link.id}/removal`, { headers }));
    const r = await ask({ type: 'question', message: q.message, detail: q.detail, buttons: q.buttons, defaultId: 0, cancelId: q.buttons.length - 1 }, 0);
    files = q.choices[r];
    if (!files) return false;
  }
  busy = `Removing the synced vault ${item.name}…`;
  pushState();
  try {
    await api(origin, `/api/folder-links/${link.id}?files=${files === 'trash' ? 'trash' : 'keep'}`, { method: 'DELETE', headers });
    if (link.remote_url) await releaseRemote(origin, api, serverId, link);
    showNotice(item.scope === 'file' ? `${item.name} is no longer pushed from ${path.basename(item.dest)}; the file stays as it is.`
      : item.offline || item.stopped ? `${item.name} is no longer synced; nothing in ${item.dest} was touched.`
      : files === 'trash' ? `${item.name} is no longer synced; the files the sync wrote are in its trash, .gamma/trash.`
      : `${item.dest} is no longer synced; its files stay.`);
    return true;
  } finally {
    busy = null;
    pushState();
    refreshKeeping().catch(() => {});
  }
}

// Where page `pageId` of the open server's workspace `wsId` is on this
// computer: `{ path, vault, reader, server }`. On a local server as it says
// (GET /api/folder-links/page/<id>: the vault that keeps the page, or the
// PDF it reads where it is); on a remote one, the host's vault of one file
// that pushes to it, read only while the host runs: a page's ask starts no
// server. null for a page with no file here.
async function pageOnDisk(wsId, pageId) {
  if (current.type === 'local') {
    const api = serverApi(content.webContents.session);
    const s = await api(openOrigin(), `/api/folder-links/page/${encodeURIComponent(pageId)}`, { headers: { 'X-Gamma-Workspace': wsId } });
    if (s.vault) return { path: s.vault.path, vault: s.vault, reader: null, server: current.id };
    if (s.reader) return { path: s.reader.path, vault: null, reader: s.reader, server: current.id };
    return null;
  }
  const host = registry.load().servers.find((s) => s.type === 'local');
  if (!host || !sidecar.status(host.id)) return null;
  const at = await localServer(host.id);
  const origin = openOrigin();
  const link = ((await at.api(at.origin, '/api/folder-links')).links || []).find((l) =>
    l.kind === 'file' && l.remote_url === origin && l.workspace_id === wsId && l.page_id === pageId);
  return link ? { path: link.dest, vault: { id: link.id, scope: 'file', name: link.title || '' }, reader: null, server: host.id } : null;
}

// A page's "Show in folder": its file in the system's file manager. Only a
// local server's page asks (its chip, vault/VaultChip.jsx): a remote's page
// has no chip, and a remote server opens no file manager on this computer
// (its ask is dropped); its vault of one file shows its file from the sync
// panel and from "Push from a file on this computer…".
async function revealOnDisk(wsId, pageId) {
  if (current.type !== 'local') return null;
  const found = await pageOnDisk(wsId, pageId);
  if (!found || !found.path) throw new Error('This page has no file on this computer yet.');
  revealFile(found.path);
  return found;
}

// A page's missing file located: a PDF read where it is takes the PDF the
// user picks (POST /api/folder-links/opened/<id>/locate), and the page of a
// vault has its vault located (locateVault).
async function relocateFile(wsId, pageId) {
  const shown = current; // the server it is asked of, whatever the window opens while the picker is up
  const found = await pageOnDisk(wsId, pageId);
  if (!found) throw new Error('This page reads no file on this computer.');
  if (found.vault) return locateVault(found.server, found.vault.id);
  const picked = await pickPdf(`Where is ${path.basename(found.path) || 'the file'} now?`, 'Choose the PDF this page reads.');
  if (!picked) return null;
  stillOn(shown);
  const local = registry.get(shown.id);
  await reachFolder(local, picked);
  stillOn(shown);
  // its origin now: reachFolder may have restarted the server on another port
  const at = localOrigin(local);
  const page = await serverApi(content.webContents.session)(at, `/api/folder-links/opened/${encodeURIComponent(pageId)}/locate`, {
    method: 'POST', headers: { 'X-Gamma-Workspace': wsId, ...JSON_TYPE, ...shellWord(at) }, body: JSON.stringify({ path: picked }),
  });
  showNotice(`“${page.content || 'The page'}” reads ${path.basename(picked)} from now on.`);
  return page;
}

// A window got the focus: each running local server's vaults look at their
// disks now (POST /api/folder-links/hint: the server's tick, in the
// background), at most every HINT_MS. Best effort.
const HINT_MS = 10_000;
let hintedAt = 0;
function hintServers() {
  if (Date.now() - hintedAt < HINT_MS) return;
  hintedAt = Date.now();
  for (const srv of registry.load().servers) {
    if (srv.type !== 'local' || !sidecar.status(srv.id)) continue;
    localServer(srv.id).then(({ origin, api }) => api(origin, '/api/folder-links/hint', { method: 'POST' })).catch(() => {});
  }
}

// -------------------------------------------------------- the sync panel -----
// The bar's sync button and its panel (docs/architecture.md "The sync
// panel"): every clone, Zotero link and synced vault the running local
// servers keep, read from each in its own session
// (localServer, signed in on first use), turned into states and one
// summary by lib/keeping.js, and pushed in the shell state as `keeping`.
// Read every KEEPING_MS, every KEEPING_BUSY_MS while something syncs, on
// the panel's opening and after each action. A row names its server, so
// the panel acts whichever server the window shows. Each reading also
// records which servers hold a Zotero link that is not detached
// (registry.setZoteroHost), the ones startBackgroundHosts starts.

const KEEPING_MS = 15_000;
const KEEPING_BUSY_MS = 2_000;
let keepingState = { items: [], summary: keeping.summarize([]) };
let keepingTimer = null;
let keepingRead = null; // the reading in flight
let keepingNext = null; // the reading asked for while it ran, which follows it

// A timer that comes due while a reading runs leaves it be: that reading
// schedules the next when it ends.
function scheduleKeeping(ms) {
  clearTimeout(keepingTimer);
  keepingTimer = setTimeout(() => keepingRead || refreshKeeping().catch(() => {}), ms);
}

// A call while a reading runs is answered by a reading that starts once
// that one ends. An action calls it right after changing something on a
// server, which the running reading may have read before the change: that
// reading's result is dropped, and the panel and the launch record wait
// for the one that follows.
function refreshKeeping() {
  if (keepingRead) {
    keepingNext ||= keepingRead.catch(() => {}).then(() => {
      keepingNext = null;
      return refreshKeeping();
    });
    return keepingNext;
  }
  keepingRead = (async () => {
    const { servers } = registry.load();
    const items = [];
    const zoteroHosts = new Map(); // server id -> holds a link that is not detached
    for (const srv of servers) {
      if (srv.type !== 'local' || !sidecar.status(srv.id)) continue;
      try {
        const { origin, api } = await localServer(srv.id);
        // (a server from before the Zotero links has no list of them: none; the session's
        // workspaces say which vaults' workspaces the account is still in, unread when it does not answer)
        const [m, f, z, who] = await Promise.all([api(origin, '/api/mirrors'), api(origin, '/api/folder-links'),
          api(origin, '/api/zotero/links').catch(() => null), api(origin, '/api/session').catch(() => null)]);
        const roles = who && Array.isArray(who.workspaces) ? who.workspaces : null;
        const kept = keeping.itemsOf(srv, { mirrors: m.mirrors, links: f.links, zotero: z ? z.links : [], roles }, servers);
        // A server with a link that is not detached starts at launch; one
        // whose links all went or were detached no longer does. A list that
        // did not come leaves that as it was.
        if (z) zoteroHosts.set(srv.id, kept.some((i) => i.kind === 'zotero' && !i.paused));
        items.push(...kept);
      } catch {
        // a server that does not answer now is left out of this reading
      }
    }
    if (keepingNext) return;
    for (const [id, on] of zoteroHosts) registry.setZoteroHost(id, on);
    keepingState = { items, summary: keeping.summarize(items) };
    pushState();
  })().finally(() => {
    keepingRead = null;
    scheduleKeeping(keepingState.summary.state === 'busy' ? KEEPING_BUSY_MS : KEEPING_MS);
  });
  return keepingRead;
}

// What a vault's row and its view in the panel do (VAULT_ACTIONS), `value`
// the switch's new setting for `pull` and `library` (a bool) and `push` (a
// word); `choose` opens a work folder's tracking dialog in Gamma.
const VAULT_ACTIONS = {
  pause: (serverId, id) => pauseVault(serverId, id, true),
  resume: (serverId, id) => pauseVault(serverId, id, false),
  sync: (serverId, id) => syncVault(serverId, id, 'both'),
  'pull-now': (serverId, id) => syncVault(serverId, id, 'pull'),
  'push-now': (serverId, id) => syncVault(serverId, id, 'push'),
  pull: (serverId, id, value) => setPull(serverId, id, Boolean(value)),
  push: (serverId, id, value) => setPush(serverId, id, value),
  'force-pull': (serverId, id) => forceVault(serverId, id, 'pull'),
  'force-push': (serverId, id) => forceVault(serverId, id, 'push'),
  'go-on': (serverId, id) => goOnVault(serverId, id),
  locate: (serverId, id) => locateVault(serverId, id),
  remove: (serverId, id) => removeVault(serverId, id),
  choose: (serverId, id) => chooseFiles(serverId, id),
  library: (serverId, id, value) => setLibrary(serverId, id, Boolean(value)),
};

// What a panel row does: a clone or a Zotero link opens, pauses (detaches)
// or resumes (reattaches, what both sides did meanwhile merging), or syncs;
// a vault opens its directory or shows its file, or does one of
// VAULT_ACTIONS.
async function keepingAction(kind, serverId, id, action, value) {
  const item = keepingState.items.find((i) => i.kind === kind && i.server === serverId && i.id === id);
  if (!item) throw new Error('That no longer syncs with this computer');
  const link = `/api/zotero/links/${encodeURIComponent(id)}`;
  if ((kind === 'clone' || kind === 'zotero') && action === 'open') {
    await openServer(serverId);
    await openGammaWorkspace(id);
    buildMenu();
  } else if (kind === 'clone' && (action === 'pause' || action === 'resume')) {
    const { origin, api } = await localServer(serverId);
    await api(origin, `/api/mirrors/${encodeURIComponent(id)}/${action === 'pause' ? 'detach' : 'relink'}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    showNotice(action === 'pause' ? `${item.name} is paused: nothing is pulled or pushed until you resume it.`
      : `${item.name} follows its origin again; what both sides did meanwhile merges.`);
  } else if (kind === 'clone' && action === 'sync') {
    const { origin, api } = await localServer(serverId);
    await api(origin, `/api/mirrors/${encodeURIComponent(id)}/sync`, { method: 'POST' });
    showNotice(`Syncing ${item.name}…`);
  } else if (kind === 'zotero' && (action === 'pause' || action === 'resume')) {
    const { origin, api } = await localServer(serverId);
    await api(origin, `${link}/${action === 'pause' ? 'detach' : 'relink'}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    // a resumed link starts its server at launch again (a pause is settled by the reading below)
    if (action === 'resume') registry.setZoteroHost(serverId, true);
    showNotice(action === 'pause' ? `${item.name} is paused: nothing goes to or comes from Zotero until you resume it.`
      : `${item.name} syncs with Zotero again; what both sides did meanwhile merges.`);
  } else if (kind === 'zotero' && action === 'sync') {
    const { origin, api } = await localServer(serverId);
    await api(origin, `${link}/sync`, { method: 'POST' });
    showNotice(`Syncing ${item.name} with Zotero…`);
  } else if (kind === 'vault' && action === 'open') {
    if (!item.dest) throw new Error('This vault is another account\'s: where its files are is that account\'s to see.');
    if (item.scope === 'file') revealFile(item.dest);
    else openDirectory(item.dest);
  } else if (kind === 'vault' && Object.prototype.hasOwnProperty.call(VAULT_ACTIONS, action)) {
    return VAULT_ACTIONS[action](serverId, id, value);
  } else {
    throw new Error(`Unknown action: ${kind} ${action}`);
  }
  refreshKeeping().catch(() => {});
  return true;
}

function showNotice(text) {
  notice = text;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { notice = null; pushState(); }, 8000);
  pushState();
}

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

// ------------------------------------------------- PDFs from the system -----
// A PDF the operating system hands the app ("Open with → Gamma", Gamma as
// the default PDF app, a file dropped on the app's icon) or Server → Open a
// PDF… picks is read where it is (readPdf, docs/research/vaults.md "Reading
// a PDF where it is"): a page of a local server reads the file, and nothing
// is stored or written beside it. The account's preference to add opened
// PDFs to the library, and the Mac App Store build, take the older way
// (addPdf): the file is added to the library the window shows and opened
// there, as an upload in Gamma adds it: stored, the page carrying it found
// or made (`POST /api/blocks/by-doc/<hash>`, so a file already in the
// library opens its page), the annotations another reader saved in it
// imported, and the window moves to the page. With no server open the last
// one opens (the first local server, made when there is none). A workspace
// others see asks first and offers the account's own; one the account can
// only read hands the file to its own (lib/openFiles.js;
// docs/architecture.md "Opening PDF files"). One file at a time, in the
// order they came.

// A proxy in front of a server caps a request's body, so past this a file
// goes in parts, as Gamma's own upload sends it (frontend/src/shared/lib/uploadParts.js).
const PART_BYTES = 32 * 1024 * 1024;
let booted = false; // until then files wait in earlyPdfs (macOS hands them over before `ready`)
const earlyPdfs = [];
let pdfQueue = Promise.resolve();

// One entry per file: `{ file, workspace, page }` once it is open, `{ file,
// error }` when it could not be (said in a dialog), `{ file }` when the
// question was cancelled.
function openPdfFiles(files) {
  if (!booted) {
    earlyPdfs.push(...files);
    return Promise.resolve([]);
  }
  const run = pdfQueue.then(async () => {
    const done = [];
    for (const file of files) {
      try {
        done.push({ file, ...(await readPdf(file)) });
      } catch (e) {
        const detail = String((e && e.message) || e);
        done.push({ file, error: detail });
        console.error(`[shell] could not open ${file}: ${detail}`);
        errorBox({ type: 'error', title: 'Gamma', message: `Could not open ${path.basename(file)} in Gamma.`, detail, buttons: ['OK'] });
      }
    }
    return done;
  });
  pdfQueue = run.catch(() => {});
  return run;
}

// An error in a native message box. The test scripts' minimized runs log it
// instead, so a step that fails leaves no dialog on the screen.
function errorBox(opts) {
  if (process.env.GAMMA_SHELL_TEST) errorBoxes.push(`${opts.message} ${opts.detail}`);
  if (TEST_MINIMIZED) console.error(`[shell] ${opts.message} ${opts.detail}`);
  else (win ? dialog.showMessageBox(win, opts) : dialog.showMessageBox(opts)).catch(() => {});
}

// A PDF read where it is: `POST /api/folder-links/open` on a local server,
// never a remote one, so a paper opened on a train is not sent to the NAS.
// It lands in the workspace the window shows when that is a local server's,
// the account writes there and it is no clone, else in the host's personal
// workspace (openFiles.readerPlace), as a page inside *Opened files*, and
// the bar says where. Before the host takes it, a page of the workspace
// the window shows that carries those bytes already opens instead
// (openCarried): a paper annotated on the NAS, in a clone or in a workspace
// the account only reads opens its page there. A workspace others see asks
// first, its members being able to read the file through the page, and
// offers the account's own. The server opens a file inside a vault as that
// vault's page, bytes a page of the workspace stores as that page, and a
// file opened before as its page. The annotations
// another reader saved in a new page's file become Gamma's
// (importAnnotations). `openAddsToLibrary` in the account's profile on the
// open server, and the Mac App Store build, whose servers reach only the
// folders the app held when they started, add the file instead (addPdf).
async function readPdf(file) {
  if (process.mas) return addPdf(file);
  await surfaceWindow();
  if (opening) await opening.catch(() => {});
  if (!current) {
    await openServer(hostOf().id);
    buildMenu();
  }
  const api = serverApi(content.webContents.session);
  if (await addsToLibrary(api, openOrigin())) return addPdf(file);
  const name = path.basename(file);
  // (a remote's session is not asked: a NAS out of reach still lets the file be read here)
  let info = current.type === 'local' ? await api(openOrigin(), '/api/session') : null;
  let place = info && openFiles.readerPlace(info, currentWsId());
  if (!place) {
    const carried = await openCarried(api, file);
    if (carried) return carried;
    const host = hostOf();
    if (current.id !== host.id) {
      await openServer(host.id);
      buildMenu();
    }
    info = await api(openOrigin(), '/api/session');
    const mine = openFiles.personalOf(info);
    if (!mine) throw new Error(`Sign in to ${current.name}, then open the file again.`);
    place = { ws: mine, audience: '', mine: null };
  }
  if (current.type !== 'local') throw new Error('A PDF is read where it is by a server on this computer only.');
  const at = current; // the server it is read on, whatever the window opens meanwhile
  let ws = place.ws;
  // A page of a workspace others see reads the file for each of them: asked
  // first, as addPdf asks, with the account's own offered instead.
  if (place.audience) {
    const buttons = [...(place.mine ? [`Open in ${place.mine.name}`] : []), `Open in ${ws.name}`, 'Cancel'];
    const r = await ask({
      type: 'question', buttons, defaultId: 0, cancelId: buttons.length - 1,
      message: `Open “${name}” in ${ws.name}?`,
      detail: `${ws.name} is shared: everyone in it can see the page and read the file from this computer while Gamma runs.`,
    }, 0);
    if (r === buttons.length - 1) return {};
    if (r === 0 && place.mine) ws = place.mine;
  }
  const origin = localOrigin(registry.get(at.id));
  busy = `Opening ${name}…`;
  pushState();
  try {
    const opened = await api(origin, '/api/folder-links/open', {
      method: 'POST',
      headers: { 'X-Gamma-Workspace': ws.id, ...JSON_TYPE, ...shellWord(origin) },
      body: JSON.stringify({ path: file }),
    });
    const headers = { 'X-Gamma-Workspace': opened.workspace };
    if (opened.created) {
      const page = await api(origin, `/api/blocks/${encodeURIComponent(opened.page)}`, { headers });
      const doc = (page.properties || {}).doc;
      if (doc && doc.format === 'pdf' && doc.hash) await importAnnotations(api, origin, headers, opened.page, doc.hash);
    }
    // Said always, a page that was there already too: where the file is read.
    const where = ((info.workspaces || []).find((w) => w.id === opened.workspace) || ws).name;
    showNotice(opened.created ? `${opened.title || name} is read from this computer, under Opened files in ${where} on ${at.name}.`
      : `${opened.title || name} is read from this computer, in ${where} on ${at.name}.`);
    if (!current || current.id !== at.id) await openServer(at.id);
    await openGammaWorkspace(opened.workspace, opened.page);
    return { workspace: opened.workspace, page: opened.page };
  } finally {
    busy = null;
    pushState();
  }
}

// The page of the workspace the window shows that carries the file's bytes
// already, opened there as an added file's page opens (`GET
// /api/blocks/by-doc/<hash>`, the hash made here, so nothing of the file
// is sent): `{ workspace, page }`. null when no page there holds them or
// the server does not answer: the file is then read on this computer.
async function openCarried(api, file) {
  const shown = currentWsId();
  if (!shown) return null;
  const at = current; // the server asked, whatever the window opens meanwhile
  const origin = openOrigin();
  const name = path.basename(file);
  busy = `Opening ${name}…`;
  pushState();
  let page;
  try {
    page = await openFiles.docIdOf(file)
      .then((docId) => api(origin, `/api/blocks/by-doc/${docId}`, {
        headers: { 'X-Gamma-Workspace': shown }, signal: AbortSignal.timeout(5000),
      }))
      .catch(() => null);
  } finally {
    busy = null;
    pushState();
  }
  if (!page || !page.id) return null;
  stillOn(at);
  const where = (((gamma && gamma.list) || []).find((w) => w.id === shown) || { name: 'this workspace' }).name;
  showNotice(`${page.content || name} is in ${where} on ${at.name} already.`);
  await openGammaWorkspace(shown, page.id);
  return { workspace: shown, page: page.id };
}

// Whether the account wants an opened PDF added to its library, as before
// (Settings: `openAddsToLibrary` in its profile on the server at `origin`).
// Not said, or not read: no.
async function addsToLibrary(api, origin) {
  try {
    const profile = await api(origin, '/api/prefs/profile', { signal: AbortSignal.timeout(5000) });
    return (profile.value || {}).openAddsToLibrary === true;
  } catch {
    return false;
  }
}

// An opened PDF's "Add to library…" for another server (docs/research/vaults.md
// "Reading a PDF where it is"): the page `pageId` of the open local server's
// workspace `wsId`, which reads its file where it is, carried to a library of
// another server the user picks (carryPage), then opened there. Another
// workspace of this server is the page's own "Add to library…" (POST
// /api/folder-links/opened/<id>/add).
async function carryToServer(wsId, pageId) {
  const done = await carryPage(wsId, pageId);
  if (!done) return null;
  showNotice(`“${done.title}” is in ${done.workspaceName} on ${done.serverName} now, with its notes, highlights and chat.`);
  await openServer(done.server);
  buildMenu();
  await openGammaWorkspace(done.workspace, done.page);
  return done;
}

// What carryToServer carries: the server asked (the other servers of the
// registry, before any is started or reached), then the workspace when it
// has several (a remote's workspaces as the window's session has them, a
// local one's as its own session does), the page and the pages inside
// it exported from here as a Gamma export (GET /api/pages/<id>/export?mode=gamma,
// which takes the PDF's bytes from the file and leaves its link to the file
// behind), merged in there (POST /api/import-data?mode=merge), taken out of
// *Opened files*, whose page stays here, and only then the page here moved
// to Recently deleted with the pages inside it, as a carry to another
// workspace here moves it (POST /api/folder-links/opened/<id>/carried with
// the digest its GET gave before the export), so the file opened again is a
// new page rather than this one back. A page written on here since that
// digest stays here (409): the copy there lacks what was written, so it is
// in both places. Each check comes before anything is written there (none
// of the pages may be a page there already, live or in Recently deleted,
// nor any of their PDFs), and nothing here goes unless the page and every
// page inside it are there, with the PDF; once they are, the page here is
// let go whatever the window opened meanwhile. Only the account
// that opened the file carries it (the page state's `reader.mine`), as only
// it takes the page to another workspace here: the file is on its computer,
// and another editor of the workspace would take that account's page, its
// notes and chat with it, away. → `{ server, serverName, workspace,
// workspaceName, page, title }`, or null when a question was cancelled.
async function carryPage(wsId, pageId) {
  if (!current || current.type !== 'local' || !content) throw new Error('Only a page of a server on this computer reads a file here.');
  const shown = current; // the server it is carried from, whatever the window opens while a question is up
  const ses = content.webContents.session;
  const api = serverApi(ses);
  const origin = localOrigin(registry.get(shown.id));
  const headers = { 'X-Gamma-Workspace': wsId };
  const state = await api(origin, `/api/folder-links/page/${encodeURIComponent(pageId)}`, { headers });
  if (!state.reader) throw new Error('This page does not read a file on this computer.');
  if (state.reader.mine !== true) {
    throw new Error('Only the account that opened this file may take it to another server: the file is on that account\'s computer.');
  }
  if (state.reader.missing) throw new Error('The file this page reads is not there: locate it first.');
  const page = await api(origin, `/api/blocks/${encodeURIComponent(pageId)}`, { headers });
  const title = page.content || 'Untitled';
  // The page's own PDF (its `doc`: a duplicated page's `copy` owns none).
  const pageDoc = (page.properties || {}).doc;
  if (!pageDoc || pageDoc.format !== 'pdf' || !pageDoc.hash || pageDoc.copy) throw new Error('This page has no PDF.');
  const docId = pageDoc.hash;

  const others = registry.load().servers.filter((s) => s.id !== shown.id);
  if (!others.length) {
    await ask({
      type: 'info', buttons: ['OK'], defaultId: 0,
      message: `This app knows no other Gamma server to add “${title}” to.`,
      detail: 'Add one under Server → All Servers… (New local server or Add remote server…), then add the page to its library from here again.',
    }, 0);
    return null;
  }
  // Asked before the server is reached: a page's ask starts no server and
  // sends nothing anywhere on its own.
  const pick = await ask({
    type: 'question', buttons: [...others.map((s) => `To ${s.name}`), 'Cancel'], defaultId: 0, cancelId: others.length,
    message: others.length > 1 ? `Add “${title}” to the library of which server?` : `Add “${title}” to the library of ${others[0].name}?`,
    detail: 'Its notes, highlights, comments and chat go with it, and its PDF is stored there. Once it is there, the page here is moved to Recently deleted here; the file on this computer stays as it is.',
  }, others.length);
  if (!(pick >= 0 && pick < others.length)) return null;
  const srv = others[pick];
  stillOn(shown);
  let at;
  if (srv.type === 'local') {
    if (!sidecar.status(srv.id)) await startLocal(srv);
    at = await localServer(srv.id);
  } else {
    at = { srv, origin: registry.originOf(srv.url), api };
  }
  const info = await at.api(at.origin, '/api/session');
  if (!info.user) throw new Error(`Sign in to ${srv.name} first, then add the page to its library again.`);
  const places = openFiles.carryPlaces(info);
  if (!places.length) throw new Error(`Your account on ${srv.name} has no workspace it can add pages to.`);
  let ws = places[0];
  if (places.length > 1) {
    const r = await ask({
      type: 'question', buttons: [...places.map((w) => `To ${w.name}`), 'Cancel'], defaultId: 0, cancelId: places.length,
      message: `Add “${title}” to the library of which workspace of ${srv.name}?`,
      detail: 'Once it is there, the page here is moved to Recently deleted here; the file on this computer stays as it is.',
    }, places.length);
    if (!(r >= 0 && r < places.length)) return null;
    ws = places[r];
  }
  stillOn(shown);

  const there = { 'X-Gamma-Workspace': ws.id };
  // What the workspace there holds of `apiPath`: 'live', 'trashed' for a
  // page in its Recently deleted (a 404 that names its entry there), or ''
  // for nothing. The merge skips a live page, and puts the copy sent in the
  // place of a trashed one, which it deletes for good with all that was
  // written on it there (ws_backup._merge): either is refused before
  // anything is sent, as a carry to another workspace here refuses both.
  const holds = (apiPath) => at.api(at.origin, apiPath, { headers: there }).then(() => 'live', (e) => {
    if (e.status !== 404) throw e;
    return e.body && e.body.trashed ? 'trashed' : '';
  });
  const held = await holds(`/api/blocks/${encodeURIComponent(pageId)}`);
  if (held === 'trashed') throw new Error(`${ws.name} on ${srv.name} has this page in Recently deleted: restore it there and open it.`);
  if (held) throw new Error(`${ws.name} on ${srv.name} has this page already: open it there.`);
  if (await holds(`/api/blocks/by-doc/${encodeURIComponent(docId)}`)) throw new Error(`${ws.name} on ${srv.name} has a page of this PDF already: open it there.`);
  // The pages go in this server's shape (its workspace data format, named
  // by the change feed): a release older than it would keep them in a shape
  // it cannot read, so it is refused before anything is sent.
  const formatOf = (feed) => (Number.isInteger(feed.format) ? feed.format : 0);
  const [here, thereFeed] = await Promise.all([
    api(origin, '/api/sync/changes?limit=1', { headers }),
    at.api(at.origin, '/api/sync/changes?limit=1', { headers: there }),
  ]);
  if (formatOf(thereFeed) < formatOf(here)) throw new Error(`${srv.name} runs an older Gamma than this app: update it, then add the page to its library again.`);

  busy = `Adding ${title} to the library of ${srv.name}…`;
  pushState();
  try {
    // What the export takes, read before it: anything written here since
    // keeps the page here (the POST below). Each page inside it is looked
    // for there as the page was.
    const carried = `/api/folder-links/opened/${encodeURIComponent(pageId)}/carried`;
    const { digest, pages } = await api(origin, carried, { headers });
    for (const id of pages) {
      const inside = id !== pageId && await holds(`/api/blocks/${encodeURIComponent(id)}`);
      if (inside) {
        throw new Error(`A page inside “${title}” is a page of ${ws.name} on ${srv.name} already${inside === 'trashed' ? ', in Recently deleted' : ''}: take it out of “${title}” first.`);
      }
    }
    const zip = await apiBytes(ses, origin, `/api/pages/${encodeURIComponent(pageId)}/export?mode=gamma&sub_pages=1`, { headers });
    const names = zipNames(zip);
    if (!names.includes(`uploads/${docId}${pageDoc.ext}`)) {
      throw new Error('The PDF did not come with the page: its file is gone or has just changed. Locate it, or try again in a moment.');
    }
    // A page inside it whose PDF is a page there already would not go (the
    // merge skips it), and would go to Recently deleted here with its page.
    for (const name of names) {
      const doc = /^uploads\/([\w-]+)\.pdf$/.exec(name);
      if (doc && doc[1] !== docId && await holds(`/api/blocks/by-doc/${encodeURIComponent(doc[1])}`)) {
        throw new Error(`A page inside “${title}” has a PDF that ${ws.name} on ${srv.name} has a page of already: take it out of “${title}” first.`);
      }
    }
    const form = new FormData();
    form.append('file', new Blob([zip], { type: 'application/zip' }), `${pageId}-gamma.zip`);
    const merged = await at.api(at.origin, `/api/import-data?mode=merge&ws=${encodeURIComponent(ws.id)}`, { method: 'POST', headers: there, body: form });
    if (!merged.pages_added) throw new Error(`${ws.name} on ${srv.name} has this page already: open it there.`);
    try {
      await at.api(at.origin, `/api/blocks/${encodeURIComponent(pageId)}`, {
        method: 'PUT', headers: { ...there, ...JSON_TYPE }, body: JSON.stringify({ properties: { parent_page: null } }),
      });
    } catch (e) {
      throw new Error(`“${title}” went to ${ws.name} on ${srv.name}, but stays inside a page it does not have there (${e.message}). The page here is kept.`);
    }
    // Every page sent arrived, or nothing here goes: the ones skipped would
    // go to Recently deleted here with their page, with no copy there.
    if (merged.pages_skipped) {
      throw new Error(`“${title}” went to ${ws.name} on ${srv.name}, but ${merged.pages_skipped === 1 ? 'a page' : `${merged.pages_skipped} pages`} inside it did not: it has them already. The page here is kept.`);
    }
    // The page here let go on the server it came from, whatever the window
    // shows now: through the window's session, else, once the window has
    // signed into another server on this computer (a cookie does not tell
    // ports apart, so that one's is all it holds: 401), through the
    // server's own (localServer). Either way the server lets it go only for
    // the account that opened the file.
    const letGo = { method: 'POST', headers: { ...headers, ...JSON_TYPE }, body: JSON.stringify({ digest }) };
    try {
      await api(localOrigin(registry.get(shown.id)), carried, letGo).catch(async (e) => {
        if (e.status !== 401) throw e;
        const own = await localServer(shown.id);
        return own.api(own.origin, carried, letGo);
      });
    } catch (e) {
      if (e.status === 409) {
        throw new Error(`“${title}” is in ${ws.name} on ${srv.name} now, and stays here too: it changed here while it was carried, so nothing here was deleted. It is in both places now.`);
      }
      throw new Error(`“${title}” is in ${ws.name} on ${srv.name} now, but the page here could not be moved to Recently deleted: ${e.message.replace(/\.$/, '')}. It is in both places now: delete the page here to keep only the copy there.`);
    }
    return { server: srv.id, serverName: srv.name, workspace: ws.id, workspaceName: ws.name, page: pageId, title };
  } finally {
    busy = null;
    pushState();
  }
}

// A route's answer as bytes (an export's zip), through session `ses`.
async function apiBytes(ses, origin, apiPath, init) {
  const r = await ses.fetch(origin + apiPath, { credentials: 'include', cache: 'no-store', ...init });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `${apiPath}: HTTP ${r.status}`);
  }
  return Buffer.from(await r.arrayBuffer());
}

// Server → Open a PDF…: a PDF from the native picker, read where it is as
// one the system hands over (openPdfFiles).
async function openPdfDialog() {
  const file = await pickPdf('Open a PDF', 'Choose a PDF on this computer to open in Gamma.', 'Open');
  if (file) await openPdfFiles([file]);
}

async function addPdf(file) {
  const name = path.basename(file);
  await surfaceWindow();
  if (opening) await opening.catch(() => {});
  if (!current) {
    const { servers } = registry.load();
    const srv = registry.getLastOpened() || servers.find((s) => s.type === 'local') || registry.addLocal('Local');
    await openServer(srv.id);
    buildMenu();
  }
  const origin = openOrigin();
  const api = serverApi(content.webContents.session);
  const info = await api(origin, '/api/session');
  if (!info.user) throw new Error(`Sign in to ${current.name}, then open the file again.`);
  const place = openFiles.placeFor(info, currentWsId());
  if (!place) throw new Error(`Your account cannot add files to ${current.name}.`);
  let ws = place.ws;
  if (place.audience) {
    const buttons = [`Add to ${ws.name}`, ...(place.mine ? [`Add to ${place.mine.name}`] : []), 'Cancel'];
    const r = await ask({
      type: 'question', buttons, defaultId: 0, cancelId: buttons.length - 1,
      message: `Add “${name}” to ${ws.name}?`,
      detail: place.audience === 'clone' ? `${ws.name} is a clone: what you add reaches the workspace it follows too.`
        : `${ws.name} is shared: everyone in it can see what you add.`,
    }, 0);
    if (r === buttons.length - 1) return {};
    if (r === 1 && place.mine) ws = place.mine;
  }
  busy = `Adding ${name}…`;
  pushState();
  try {
    const headers = { 'X-Gamma-Workspace': ws.id };
    const up = await uploadPdf(api, origin, headers, file, name);
    const page = await api(origin, `/api/blocks/by-doc/${encodeURIComponent(up.doc_id)}`, {
      method: 'POST',
      headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify({ default_title: name, source_url: up.source_url, original_filename: name }),
    });
    await importAnnotations(api, origin, headers, page.id, up.doc_id);
    await openGammaWorkspace(ws.id, page.id);
    if (place.readOnly) showNotice(`${name} went into ${ws.name}: you can only read ${place.readOnly.name}.`);
    return { workspace: ws.id, page: page.id };
  } finally {
    busy = null;
    pushState();
  }
}

// The file into the workspace's uploads → `{ doc_id, source_url }`: one
// request, or in parts past PART_BYTES (open, each part, finish), read from
// disk as it goes.
async function uploadPdf(api, origin, headers, file, name) {
  const blob = await fs.openAsBlob(file, { type: 'application/pdf' });
  if (blob.size <= PART_BYTES) {
    const form = new FormData();
    form.append('file', blob, name);
    return api(origin, '/api/uploads', { method: 'POST', headers, body: form });
  }
  const opened = await api(origin, '/api/uploads/parts', {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ size: blob.size, name }),
  });
  const parts = `/api/uploads/parts/${opened.token}`;
  const step = Math.max(1, Math.min(PART_BYTES, opened.part_bytes || PART_BYTES));
  try {
    for (let offset = 0; offset < blob.size;) {
      const form = new FormData();
      form.append('offset', String(offset));
      form.append('part', blob.slice(offset, Math.min(offset + step, blob.size)), 'part');
      offset = (await api(origin, parts, { method: 'POST', headers, body: form })).received;
      busy = `Adding ${name}… ${Math.round((offset / blob.size) * 100)}%`;
      pushState();
    }
    return await api(origin, `${parts}/finish`, { method: 'POST', headers });
  } catch (e) {
    api(origin, parts, { method: 'DELETE', headers }).catch(() => {}); // the server drops what it held
    throw e;
  }
}

// Annotations another reader saved in the file become Gamma highlights, as
// on an upload in Gamma, and leave the stored copy unless the account chose
// to only hide them (Settings → Embedded PDF annotations: `embAnnots` in its
// profile, frontend/src/app/prefDefs.js). Best effort: the page opens either way.
async function importAnnotations(api, origin, headers, pageId, docId) {
  let strip = true;
  try {
    const profile = await api(origin, '/api/prefs/profile', { headers, signal: AbortSignal.timeout(5000) });
    strip = (profile.value || {}).embAnnots !== 'hide';
  } catch {}
  await api(origin, '/api/import/pdf-annotations', {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ block_id: pageId, doc_id: docId, strip }),
  }).catch((e) => console.error(`[shell] annotations in ${docId}: ${e.message || e}`));
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

function trayMenu() {
  return Menu.buildFromTemplate([
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
  tray.setToolTip('Gamma');
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
// a work folder's tracking dialog (`track`, its link's id, with its folder).
function openGammaWorkspace(id, at = {}) {
  if (!current || !content) throw new Error('No server open');
  const { page, folder, track } = typeof at === 'string' ? { page: at } : at || {};
  const target = new URL(current.url);
  const q = new URLSearchParams({ ws: String(id || '') });
  if (page) q.set('page', page);
  if (folder) q.set('folder', folder);
  if (track) q.set('track', track);
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
    keeping: keepingState,
    // the vault dialog the sync panel shows (askVault), or null
    vaultAsk: vaultAskState(),
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
function dataMoveState() {
  return dataMove && { name: dataMove.name, done: dataMove.done, total: dataMove.total };
}

// The launcher's fuller view: credentials, data dirs, sizes, settings.
function fullState() {
  const state = registry.load();
  return {
    ...barState(),
    servers: state.servers.map((ws) => ({
      ...ws,
      // A local server's address while it runs (the Connector's server).
      url: ws.type === 'remote' ? ws.url : (sidecar.status(ws.id) || {}).url,
      running: ws.type === 'local' ? Boolean(sidecar.status(ws.id)) : undefined,
      reachable: remoteReachable(ws),
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
    defaultDataRoot: hostPath(registry.defaultDataRoot()),
    // Names of the local servers a root change would move.
    movable: registry.localsUnderRoot(state).map((w) => w.name),
    dataMove: dataMoveState(),
    mas: Boolean(process.mas), // the launcher hides what a Mac App Store build lacks
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
  cancelVaultAsk(); // it was asked about what the window showed
  busy = ws.type === 'local' ? `Starting ${ws.name}…` : `Connecting to ${ws.name}…`;
  pushState();
  try {
    let url;
    let creds = null;
    if (ws.type === 'remote') {
      url = ws.url;
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

// Help → Check for Updates…: the only update flow that answers with a
// dialog; the automatic checks stay silent (bar pill / launcher row).
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
  } else if (st.status === 'downloaded') {
    opts.message = `Gamma ${st.version} is ready to install.`;
    opts.detail = 'It installs when you restart.';
    opts.buttons = ['Restart to update', 'Later'];
  } else if (st.status === 'downloading') {
    opts.message = `Downloading Gamma ${st.version}…`;
    opts.detail = 'You will be offered a restart when it is ready.';
  } else if (st.status === 'available') {
    opts.message = `Gamma ${st.version} is available.`;
    opts.detail = 'This build cannot update itself; the download page opens in your browser.';
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
  const template = [
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' }] : []),
    {
      label: 'Server',
      submenu: [
        { label: 'All Servers…', accelerator: 'CmdOrCtrl+Shift+L', click: () => loadLauncher() },
        { label: 'Open a PDF…', click: () => openPdfDialog().catch(() => {}) },
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
        process.platform === 'darwin' ? { role: 'close' } : { role: 'quit' },
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
        process.platform === 'darwin' ? { role: 'zoom' } : { role: 'close' },
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

function registerIpc() {
  ipcMain.handle('shell:state', shellOnly(() => { probeRemotes(); refreshGamma(); return barState(); }));
  ipcMain.handle('shell:list', shellOnly(() => { probeRemotes(); return fullState(); }));
  ipcMain.handle('shell:update-check', shellOnly(() => updater.check()));
  ipcMain.handle('shell:update-install', shellOnly(() => updater.install()));
  ipcMain.handle('shell:add-local', shellOnly((name) => { const ws = registry.addLocal(name); buildMenu(); pushState(); return ws; }));
  ipcMain.handle('shell:add-remote', shellOnly((name, url) => { const ws = registry.addRemote(name, url); buildMenu(); pushState(); return ws; }));
  ipcMain.handle('shell:set-url', shellOnly((id, url) => {
    const srv = registry.setRemoteUrl(id, url);
    allowedOrigins.add(srv.url);
    if (current && current.id === id) current = null; // the next open loads the new address
    buildMenu();
    pushState();
    return srv;
  }));
  ipcMain.handle('shell:rename', shellOnly((id, name) => {
    const ws = registry.rename(id, name);
    if (current && current.id === id) current = { ...current, name: ws.name };
    buildMenu();
    pushState();
    return ws;
  }));
  ipcMain.handle('shell:remove', shellOnly(async (id, opts) => {
    if (current && current.id === id) loadLauncher();
    await sidecar.stop(id);
    registry.remove(id, opts || {});
    buildMenu();
    pushState();
  }));
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
  ipcMain.handle('shell:folders', shellOnly((wsId) => listFolders(wsId)));
  ipcMain.handle('shell:add-folder-vault', shellOnly((wsId, folderId) =>
    withDialog('Could not sync the folder with this computer.', oneAsk(() => addFolderVault(wsId, folderId)))()));
  ipcMain.handle('shell:add-vault', shellOnly(() => withDialog('Could not add the synced vault.', oneAsk(() => addDirectoryVault()))()));
  ipcMain.handle('shell:answer-vault', shellOnly((id, answer) => answerVault(id, answer)));
  ipcMain.handle('shell:keeping', shellOnly(() => { refreshKeeping().catch(() => {}); return keepingState; }));
  // Of a vault's actions, those that change it or run a round leave the sync
  // panel open on the new state (bar.html keptRow); the others ask first, or
  // open something.
  const STAYS_OPEN = ['pause', 'resume', 'sync', 'pull-now', 'push-now', 'pull', 'push'];
  ipcMain.handle('shell:keeping-action', shellOnly((kind, serverId, id, action, value) =>
    withDialog('Could not do that.', () => keepingAction(kind, serverId, id, action, value), !STAYS_OPEN.includes(action))()));
  // A page's asks about this computer in Gamma's own menus (a folder's "Sync
  // with this computer…", a page's "Push from a file on this computer…", its
  // file shown in its folder, its missing file located, an opened PDF carried
  // to another server's library), passed on by the
  // page preload: only from the content view's page of the open server,
  // with plain ids, one action at a time (oneAsk: none while a picker, a
  // question or the vault dialog is up), and at most one a second, so a page
  // asking in a loop opens no row of file manager windows. The native picker
  // is the user's say, so the page can start nothing on its own.
  const PAGE_ASK_MS = 1000;
  let pageAskAt = 0;
  const fromPage = (event, ...ids) => {
    if (!content || event.sender !== content.webContents || busy || vaultAsk || asking || !current) return false;
    let from = '';
    try { from = new URL(event.senderFrame.url).origin; } catch {}
    if (from !== openOrigin() || !ids.every((id) => /^[\w-]{1,64}$/.test(id))) return false;
    if (Date.now() - pageAskAt < PAGE_ASK_MS) return false;
    pageAskAt = Date.now();
    return true;
  };
  const PAGE_ASKS = {
    'shell:sync-folder-from-page': ['Could not sync the folder with this computer.', addFolderVault],
    'shell:push-file-from-page': ['Could not push from the file.', addFileVault],
    'shell:reveal-from-page': ['Could not show the file.', revealOnDisk],
    'shell:relocate-from-page': ['Could not locate the file.', relocateFile],
    'shell:carry-from-page': ['Could not add the page to the other server\'s library.', carryToServer],
  };
  for (const [channel, [message, run]] of Object.entries(PAGE_ASKS)) {
    ipcMain.on(channel, (event, wsId, id) => {
      if (!fromPage(event, wsId, id)) return;
      withDialog(message, oneAsk(() => run(wsId, id)))().catch(() => {});
    });
  }
  // Reveal a directory a link of the open server's workspace writes — only such a one.
  ipcMain.handle('shell:open-path', shellOnly(async (wsId, p) => {
    const { links } = await listFolders(wsId);
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
    sidecar.stopAll();
    try {
      fs.rmSync(tmp, { recursive: true, force: true });
    } catch {}
  }
  process.stdout.write('SMOKE ' + JSON.stringify(out) + '\n');
  app.exit(out.ok ? 0 : 1);
}

// ----------------------------------------------------------------- boot -----

app.whenReady().then(async () => {
  if (!primary) return; // quitting: the running app has the command line
  registry.init(app.getPath('userData'));
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
  session.defaultSession.setPermissionRequestHandler(microphone.create({
    prefs: systemPreferences,
    onDenied: () => microphoneRefused().catch(() => {}),
  }));
  updater.init({ onChange: () => pushState(), openExternal });
  buildMenu();
  const settings = registry.getSettings();
  if (settings.background) ensureTray();
  applyLoginItem();
  const files = [...earlyPdfs.splice(0), ...openFiles.pdfPaths(process.argv.slice(1), process.cwd())];
  // Started at login (`--hidden`, or `wasOpenedAtLogin` on macOS, which
  // passes a login item no arguments): no window, the servers run in the
  // background for the clones, the Zotero links and the synced vaults.
  const hidden = !files.length && Boolean(settings.background) && (process.argv.includes('--hidden') ||
    (process.platform === 'darwin' && app.getLoginItemSettings().wasOpenedAtLogin));
  if (hidden) startBackgroundHosts();
  else showWindow().finally(startBackgroundHosts);
  scheduleKeeping(3000); // what the servers keep, once they are up
  booted = true;
  if (files.length) openPdfFiles(files);

  // The Dock icon: the window back, as Window → Show Gamma brings it.
  app.on('activate', () => showWindow());
});

// A second start's command line (the lock above): its PDFs are added, and
// the window comes forward either way — unless it was a login start.
app.on('second-instance', (_event, argv, cwd) => {
  const files = openFiles.pdfPaths(argv.slice(1), cwd);
  if (files.length) openPdfFiles(files);
  else if (booted && !argv.includes('--hidden')) showWindow();
});

// macOS hands a file over as an event instead, at launch before `ready` too.
app.on('open-file', (event, file) => {
  event.preventDefault();
  openPdfFiles(openFiles.pdfPaths([file]));
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
    addFolderVault,
    addDirectoryVault,
    addFileVault,
    vaultAsk: vaultAskState,
    answerVault,
    oneAsk,
    shellWord,
    setBarExpanded,
    barExpanded: () => barExpanded,
    syncVault,
    pauseVault,
    setPull,
    setPush,
    forceVault,
    goOnVault,
    locateVault,
    removeVault,
    revealOnDisk,
    relocateFile,
    carryPage,
    carryToServer,
    asked,
    answers,
    // main.test.js: the window shows server `srv`, `view` standing in for its
    // content view and `window` (none by default) for the window itself.
    pretendShown: (srv, view, window = null) => {
      current = srv && { id: srv.id, name: srv.name, type: srv.type, url: srv.type === 'local' ? sidecar.status(srv.id).url : srv.url };
      content = view;
      win = window;
    },
    hintServers,
    shellSecret: SHELL_SECRET,
    listFolders,
    refreshKeeping,
    keepingAction,
    keeping: () => keepingState,
    startLocal,
    startBackgroundHosts,
    openPdfFiles,
    readPdf,
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
    update: () => updater.state(),
    remoteHealth,
    probeRemotes,
    bounds: () => ({ bar: bar && bar.getBounds(), content: content && content.getBounds() }),
  };
}
