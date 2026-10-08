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

const { app, BaseWindow, WebContentsView, Menu, Tray, nativeImage, shell, ipcMain, net, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { pathToFileURL } = require('url');

const registry = require('./lib/registry');
const sidecar = require('./lib/sidecar');
const updater = require('./lib/updater');
const startup = require('./lib/startup');

const SMOKE = process.argv.includes('--smoke');
const BAR_H = 38;

// Tests point the shell at a throwaway profile so the real registry, cookies
// and sidecars are never touched.
if (process.env.GAMMA_SHELL_USER_DATA) app.setPath('userData', process.env.GAMMA_SHELL_USER_DATA);

// Title-bar palette per Gamma theme: the shell bar's chrome, Gamma's
// --bg-page in ui/tokens.css (frontend/tests/themes.test.mjs checks it), and
// a text colour for the window controls. '' = the page never reported a
// theme → Gamma's default, dark.
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
  };
}

function currentTheme() {
  return theme || registry.getSettings().lastTheme || '';
}

function palette() {
  return THEMES[currentTheme()] || THEMES[''];
}

// ---------------------------------------------------------------- window ----

function layout() {
  if (!win) return;
  const [w, h] = win.getContentSize();
  // The bar's dropdown needs room below the strip: while a menu is open the
  // bar view grows over the content (its page is transparent outside the
  // strip and the menu, and a click there closes the menu).
  const barH = barExpanded ? Math.min(h, BAR_H + 420) : BAR_H;
  bar.setBounds({ x: 0, y: 0, width: w, height: barH });
  content.setBounds({ x: 0, y: BAR_H, width: w, height: Math.max(0, h - BAR_H) });
}

function applyTheme() {
  if (!win) return;
  const p = palette();
  win.setBackgroundColor(p.bg);
  if (process.platform !== 'darwin') {
    try {
      win.setTitleBarOverlay({ color: p.bg, symbolColor: p.symbol, height: BAR_H });
    } catch {}
  }
}

function createWindow() {
  const saved = registry.getWindowBounds();
  const p = palette();
  win = new BaseWindow({
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
  if (saved && saved.maximized) win.maximize();

  const webPreferences = {
    preload: path.join(__dirname, 'preload.js'),
    contextIsolation: true,
    nodeIntegration: false,
  };
  bar = new WebContentsView({ webPreferences });
  bar.setBackgroundColor('#00000000');
  content = new WebContentsView({ webPreferences });
  content.setBackgroundColor(p.bg);
  win.contentView.addChildView(content);
  win.contentView.addChildView(bar); // on top, so the expanded menu overlays
  layout();
  win.on('resize', layout);
  win.on('maximize', layout);
  win.on('unmaximize', layout);

  bar.webContents.loadFile(path.join(__dirname, 'ui', 'bar.html'));
  bar.webContents.on('did-finish-load', pushState);

  const cwc = content.webContents;
  const guard = (event, url) => {
    let origin = null;
    try {
      origin = new URL(url).origin;
    } catch {}
    if (url.startsWith('file:') || (origin && allowedOrigins.has(origin))) return;
    event.preventDefault();
    if (origin) openExternal(url);
  };
  cwc.on('will-navigate', guard);
  cwc.on('will-redirect', guard);
  // target=_blank (external link chips, share links, papers opened in a new
  // tab) goes to the system browser.
  cwc.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) openExternal(url);
    return { action: 'deny' };
  });
  cwc.on('page-title-updated', (_e, title) => {
    if (win) win.setTitle(title || 'Gamma');
  });
  cwc.on('did-navigate', () => { pushState(); refreshGamma(); });
  cwc.on('did-navigate-in-page', () => { pushState(); refreshGamma(); });
  cwc.on('did-finish-load', refreshGamma);
  cwc.on('focus', () => {
    if (barExpanded) setBarExpanded(false);
  });

  // Downloads (backup zips, exports) go through the normal save dialog;
  // tests get them saved straight into a folder.
  cwc.session.on('will-download', (_e, item) => {
    const dir = process.env.GAMMA_SHELL_DOWNLOAD_DIR;
    if (dir) item.setSavePath(path.join(dir, item.getFilename()));
  });

  win.on('close', () => {
    try {
      registry.setWindowBounds({ ...win.getNormalBounds(), maximized: win.isMaximized() });
    } catch {}
  });
  win.on('closed', () => {
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
  });
}

function openExternal(url) {
  externalOpens.push(url);
  if (!process.env.GAMMA_SHELL_TEST) shell.openExternal(url);
}

function setBarExpanded(on) {
  barExpanded = Boolean(on);
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
  current = null;
  gamma = null;
  busy = null;
  const q = error ? '?error=' + encodeURIComponent(launcherError(error, serverId)) : '';
  content.webContents.loadURL(pathToFileURL(path.join(__dirname, 'ui', 'launcher.html')).href + q);
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

// Local servers that hold offline copies run for as long as the app does —
// a copy syncs only while its server runs, and it should keep up in the
// background whichever server the window shows. Best effort, after the
// window is up; a server already running (the one just opened) is left alone.
// Local servers to run for as long as the app does: the hosts of offline
// copies (a copy syncs only while its server runs) and, in background mode,
// every local server — a folder kept on this computer is its server's job too.
function startBackgroundHosts() {
  const state = registry.load();
  const hosts = new Set(state.mirrors.map((m) => m.server));
  const all = Boolean(state.settings.background);
  for (const srv of state.servers) {
    if (srv.type !== 'local' || (!all && !hosts.has(srv.id)) || sidecar.status(srv.id)) continue;
    sidecar
      .start(srv, state.settings, appInfo())
      .then(() => pushState())
      .catch((e) => console.error(`[shell] could not start ${srv.name} for its offline copies: ${e.message || e}`));
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
  const dropToken = () => token && api(remoteOrigin, `/api/integrations/tokens/${token.id}`, { method: 'DELETE' }).catch(() => {});
  try {
    token = await api(remoteOrigin, '/api/integrations/tokens', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Gamma-Workspace': wsId },
      body: JSON.stringify({ name: 'Gamma desktop offline copy', scope: 'write', expires_in_days: 365 }),
    });
    let local = registry.load().servers.find((s) => s.type === 'local');
    if (!local) local = registry.addLocal('Local');
    const entry = await sidecar.start(local, registry.getSettings(), appInfo());
    const localOrigin = new URL(entry.url).origin;
    allowedOrigins.add(localOrigin);
    const session = await api(localOrigin, '/api/session');
    if (!session.user) {
      await api(localOrigin, '/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: local.adminUser, password: local.adminPassword }),
      });
    }
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
// local server or a remote: what keepOffline and the folders on disk use.
function serverApi(ses) {
  return async (origin, apiPath, init) => {
    const r = await ses.fetch(origin + apiPath, { credentials: 'include', cache: 'no-store', ...init });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `${apiPath}: HTTP ${r.status}`);
    return body;
  };
}

// ------------------------------------------------- folders on this computer -----
// "Keep a folder on this computer": a folder of a LOCAL server's workspace
// written to a directory of the user's choice — PDFs beside Markdown notes —
// and kept up to date by that server (docs/dev/folder_sync.md "Links kept by
// the server"). The shell only asks for the directory (the native picker;
// GAMMA_SHELL_PICK_DIR in tests) and makes the link through the server's
// public API with the content session's cookies; local sidecars run with
// GAMMA_FOLDERS_ANYWHERE, so the server takes the absolute path. The rounds,
// the status and the removal are the server's: Settings → Workspaces →
// Folders on disk there. An empty directory is used as it is; one holding
// anything gets a subdirectory named after the folder.

function localOrigin() {
  if (!current || current.type !== 'local' || !content) throw new Error('Open a local server first');
  return new URL(current.url).origin;
}

async function listFolders(wsId) {
  const origin = localOrigin();
  const api = serverApi(content.webContents.session);
  const headers = { 'X-Gamma-Workspace': wsId };
  const [tree, links] = await Promise.all([
    api(origin, '/api/sync/folders', { headers }),
    api(origin, '/api/folder-links', { headers }),
  ]);
  return { folders: tree.folders || [], links: links.links || [], root: links.root || '' };
}

async function pickDirectory(name) {
  if (process.env.GAMMA_SHELL_PICK_DIR) return process.env.GAMMA_SHELL_PICK_DIR;
  const opts = {
    title: `Keep “${name}” on this computer`,
    message: `Choose where “${name}” goes. An empty folder is used as it is; any other gets a folder named “${name}” inside.`,
    buttonLabel: 'Keep here',
    properties: ['openDirectory', 'createDirectory'],
    defaultPath: app.getPath('documents'),
  };
  const r = win ? await dialog.showOpenDialog(win, opts) : await dialog.showOpenDialog(opts);
  return r.canceled ? null : r.filePaths[0] || null;
}

function isEmptyDir(p) {
  try {
    return fs.readdirSync(p).length === 0;
  } catch {
    return true; // not there yet: the server makes it
  }
}

async function keepFolder(wsId, folderId) {
  const origin = localOrigin();
  const api = serverApi(content.webContents.session);
  const g = gamma;
  const wsName = (g && (g.list.find((w) => w.id === wsId) || {}).name) || 'Library';
  const tree = await api(origin, '/api/sync/folders', { headers: { 'X-Gamma-Workspace': wsId } });
  const folder = folderId === 'root' ? { path: [] } : (tree.folders || []).find((f) => f.id === folderId);
  if (!folder) throw new Error('Unknown folder');
  const name = folder.path.length ? folder.path[folder.path.length - 1] : wsName;
  const picked = await pickDirectory(name);
  if (!picked) return null;
  const target = isEmptyDir(picked) ? picked : path.join(picked, name);
  busy = `Keeping ${name} on this computer…`;
  pushState();
  try {
    const link = await api(origin, '/api/folder-links', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Gamma-Workspace': wsId },
      body: JSON.stringify({ folder: folderId, path: target, notes: true }),
    });
    showNotice(`${name} is being written to ${link.dest}`);
    offerBackground(`“${name}” on this computer`).catch(() => {});
    return link;
  } finally {
    busy = null;
    pushState();
  }
}

function showNotice(text) {
  notice = text;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { notice = null; pushState(); }, 8000);
  pushState();
}

// ---------------------------------------------------------- background -----
// "Keep running in the background": closing the window leaves the app in
// the tray with every local server running, so clones and folders on disk
// keep syncing (docs/architecture.md "Background and tray"). Off, the app
// quits with its last window as before (macOS keeps the dock process either
// way). "Start at login" launches it hidden, straight into the tray.

function trayIcon() {
  const img = nativeImage.createFromPath(path.join(__dirname, 'assets', 'icon.png'));
  const size = process.platform === 'darwin' ? 18 : 16;
  return img.resize({ width: size, height: size });
}

function trayMenu() {
  const s = registry.getSettings();
  return Menu.buildFromTemplate([
    { label: 'Open Gamma', click: () => showWindow() },
    { type: 'separator' },
    { label: 'Keep running in the background', type: 'checkbox', checked: Boolean(s.background), click: (item) => setBackground(item.checked) },
    { label: 'Start at login', type: 'checkbox', checked: Boolean(s.openAtLogin), click: (item) => setOpenAtLogin(item.checked) },
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

// The window, back or new: where the user left off, or the launcher.
function showWindow() {
  if (win) {
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
    return;
  }
  createWindow();
  const last = registry.getSettings().openLastOnLaunch ? registry.getLastOpened() : null;
  if (last) openServer(last.id).then(buildMenu).catch((e) => loadLauncher(e, last.id));
  else loadLauncher();
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
// binary; under the test harness nothing is touched). Started that way the
// app opens hidden, into the tray (`--hidden`).
function applyLoginItem() {
  const on = Boolean(registry.getSettings().openAtLogin);
  if (!app.isPackaged || process.env.GAMMA_SHELL_TEST) return;
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
  app.setLoginItemSettings({ openAtLogin: on, openAsHidden: true, args: ['--hidden'] });
}

// A clone or a folder on disk stays in sync only while Gamma runs: when one
// is made and the app still quits with its window, offer the background.
async function offerBackground(what) {
  if (registry.getSettings().background || process.env.GAMMA_SHELL_TEST || !win) return;
  const r = await dialog.showMessageBox(win, {
    type: 'question',
    buttons: ['Keep running', 'Not now'],
    defaultId: 0,
    cancelId: 1,
    message: 'Keep Gamma running in the background?',
    detail: `${what} stays in sync only while Gamma runs. In the background Gamma keeps running after the window is closed, with an icon in the tray, and can start at login.`,
  });
  if (r.response === 0) setBackground(true);
}

// Navigate the open server to one of its Gamma workspaces.
function openGammaWorkspace(id) {
  if (!current || !content) throw new Error('No server open');
  const target = new URL(current.url);
  target.search = '?ws=' + encodeURIComponent(String(id || ''));
  return content.webContents.loadURL(target.href);
}

// What the shell bar (and the launcher, for the switcher part) renders.
function barState() {
  const state = registry.load();
  return {
    platform: process.platform,
    version: app.getVersion(),
    packaged: app.isPackaged,
    theme: currentTheme(),
    current,
    busy,
    notice,
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

// The launcher's fuller view: credentials, data dirs, sizes, settings.
function fullState() {
  const state = registry.load();
  return {
    ...barState(),
    servers: state.servers.map((ws) => ({
      ...ws,
      running: ws.type === 'local' ? Boolean(sidecar.status(ws.id)) : undefined,
      reachable: remoteReachable(ws),
      sizeBytes: ws.type === 'local' ? registry.dirSize(ws.dataDir) : undefined,
      logPath: ws.type === 'local' ? path.join(app.getPath('userData'), 'logs', `${ws.id}.log`) : undefined,
    })),
    settings: state.settings,
    lastOpened: state.lastOpened,
    detected: sidecar.detectDev(state.settings),
    userDataDir: app.getPath('userData'),
    dataRoot: registry.dataRoot(state),
    defaultDataRoot: registry.defaultDataRoot(),
    // Names of the local servers a root change would move.
    movable: registry.localsUnderRoot(state).map((w) => w.name),
  };
}

function pushState() {
  if (!bar || bar.webContents.isDestroyed() || bar.webContents.isLoading()) return;
  bar.webContents.send('shell:state', barState());
  if (content && !content.webContents.isDestroyed() && content.webContents.getURL().startsWith('file:')) {
    content.webContents.send('shell:state', barState());
  }
}

// ----------------------------------------------------------- auto-login -----

// Local servers log in silently with the credentials the shell seeded.
// Runs in the page after load: if /api/session says anonymous, POST the
// stored credentials and reload. Harmless when already logged in.
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
      if (r.ok) { location.reload(); return 'logged-in'; }
      return 'login-failed:' + r.status;
    } catch (e) { return 'error:' + e; }
  })()`;
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
  if (!win) createWindow();
  if (current && current.id === id) return { url: current.url, autoLogin: 'already-open' };
  busy = ws.type === 'local' ? `Starting ${ws.name}…` : `Connecting to ${ws.name}…`;
  pushState();
  try {
    let url;
    let creds = null;
    if (ws.type === 'remote') {
      url = ws.url;
    } else {
      const entry = await sidecar.start(ws, registry.getSettings(), appInfo());
      url = entry.url;
      if (ws.adminUser) creds = { username: ws.adminUser, password: ws.adminPassword };
    }
    allowedOrigins.add(new URL(url).origin);
    await content.webContents.loadURL(url);
    if (ws.type === 'remote') remoteHealth.set(ws.id, { ok: true, at: Date.now() });
    current = { id: ws.id, name: ws.name, type: ws.type, url };
    gamma = null;
    registry.markOpened(ws.id);
    content.webContents.focus();
    if (creds) {
      const result = await content.webContents
        .executeJavaScript(autoLoginScript(creds.username, creds.password))
        .catch((e) => 'exec-error:' + e);
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
  const r = win ? await dialog.showMessageBox(win, opts) : await dialog.showMessageBox(opts);
  if (r.response === 0 && opts.buttons.length > 1) updater.install();
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
        { type: 'separator' },
        ...servers.map((ws) => ({
          label: `${ws.name}${ws.type === 'remote' ? '  (remote)' : ''}`,
          type: 'checkbox',
          checked: Boolean(current && current.id === ws.id),
          click: () => openServer(ws.id).catch((e) => loadLauncher(e, ws.id)),
        })),
        { type: 'separator' },
        { label: 'Keep Running in the Background', type: 'checkbox', checked: Boolean(registry.getSettings().background), click: (item) => setBackground(item.checked) },
        { label: 'Start at Login', type: 'checkbox', checked: Boolean(registry.getSettings().openAtLogin), click: (item) => setOpenAtLogin(item.checked) },
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
    { role: 'windowMenu' },
    {
      role: 'help',
      submenu: [
        { label: 'Check for Updates…', click: () => checkForUpdatesInteractive().catch(() => {}) },
        { label: 'Gamma on GitHub', click: () => openExternal('https://github.com/tim4431/Gamma') },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ------------------------------------------------------------------ IPC -----
// The shell's own pages (file://: launcher + bar) are the only surface these
// are exposed to — the preload bridges them only on file: URLs, and the
// sender is re-checked here.

function shellOnly(handler) {
  return (event, ...args) => {
    const url = event.senderFrame ? event.senderFrame.url : '';
    if (!url.startsWith('file:')) throw new Error('Not allowed');
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
  ipcMain.handle('shell:rename', shellOnly((id, name) => {
    const ws = registry.rename(id, name);
    if (current && current.id === id) current = { ...current, name: ws.name };
    buildMenu();
    pushState();
    return ws;
  }));
  ipcMain.handle('shell:remove', shellOnly((id, opts) => {
    if (current && current.id === id) loadLauncher();
    sidecar.stop(id);
    registry.remove(id, opts || {});
    buildMenu();
    pushState();
  }));
  ipcMain.handle('shell:open-workspace', shellOnly(async (id) => {
    setBarExpanded(false);
    await openGammaWorkspace(id);
  }));
  const withDialog = (message, fn) => async (id) => {
    setBarExpanded(false);
    try {
      return await fn(id);
    } catch (e) {
      dialog.showMessageBox(win, { type: 'error', title: 'Gamma', message, detail: String(e && e.message || e), buttons: ['OK'] }).catch(() => {});
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
    if (ws && ws.type === 'local') shell.openPath(ws.dataDir);
  }));
  ipcMain.handle('shell:reveal-log', shellOnly((id) => {
    const p = path.join(app.getPath('userData'), 'logs', `${id}.log`);
    if (fs.existsSync(p)) shell.showItemInFolder(p);
  }));
  ipcMain.handle('shell:set-settings', shellOnly((patch) => {
    const s = registry.setSettings(patch);
    if ('background' in patch) setBackground(s.background);
    if ('openAtLogin' in patch) setOpenAtLogin(s.openAtLogin);
    return s;
  }));
  ipcMain.handle('shell:folders', shellOnly((wsId) => listFolders(wsId)));
  ipcMain.handle('shell:keep-folder', shellOnly((wsId, folderId) =>
    withDialog('Could not keep the folder on this computer.', () => keepFolder(wsId, folderId))()));
  // Reveal a directory a link of the open local server writes — only such a one.
  ipcMain.handle('shell:open-path', shellOnly(async (wsId, p) => {
    const { links } = await listFolders(wsId);
    if (links.some((l) => l.dest === p)) shell.openPath(p);
  }));
  ipcMain.handle('shell:pick-folder', shellOnly(async (defaultPath) => {
    const opts = { properties: ['openDirectory', 'createDirectory'], defaultPath: defaultPath || undefined };
    const r = win ? await dialog.showOpenDialog(win, opts) : await dialog.showOpenDialog(opts);
    return r.canceled ? null : r.filePaths[0] || null;
  }));
  // Storage root for local servers ('' = default). Moving needs the SQLite
  // files closed, so every sidecar under the old root is stopped first; they
  // restart on the next open. A local server that is open goes back to the
  // launcher (its process is about to be killed).
  ipcMain.handle('shell:set-data-root', shellOnly((dir, { move = true } = {}) => {
    if (move) {
      const moving = new Set(registry.localsUnderRoot().map((w) => w.id));
      if (current && moving.has(current.id)) loadLauncher();
      for (const id of moving) sidecar.stop(id);
    }
    const r = registry.setDataRoot(dir, { move });
    buildMenu();
    pushState();
    return r;
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
      await new Promise((r) => setTimeout(r, 1500)); // let the reload settle
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
  registry.init(app.getPath('userData'));
  if (SMOKE) {
    runSmoke();
    return;
  }
  registerIpc();
  updater.init({ onChange: () => pushState(), openExternal });
  buildMenu();
  const settings = registry.getSettings();
  if (settings.background) ensureTray();
  applyLoginItem();
  // Started at login (`--hidden`; macOS says so itself): no window, the
  // servers run in the background for the clones and the folders on disk.
  const hidden = Boolean(settings.background) && (process.argv.includes('--hidden') ||
    (process.platform === 'darwin' && app.getLoginItemSettings().wasOpenedAsHidden));
  if (hidden) {
    startBackgroundHosts();
  } else {
    createWindow();
    // Reopen where the user left off; the launcher is one click away in the bar.
    const last = settings.openLastOnLaunch ? registry.getLastOpened() : null;
    if (last) {
      openServer(last.id).then(buildMenu).catch((e) => loadLauncher(e, last.id)).finally(startBackgroundHosts);
    } else {
      loadLauncher();
      startBackgroundHosts();
    }
  }

  app.on('activate', () => {
    if (!win) showWindow();
  });
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
    keepFolder,
    listFolders,
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
    update: () => updater.state(),
    remoteHealth,
    probeRemotes,
    barExpanded: () => barExpanded,
    bounds: () => ({ bar: bar && bar.getBounds(), content: content && content.getBounds() }),
  };
}
