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

const { app, BaseWindow, WebContentsView, Menu, Tray, nativeImage, nativeTheme, session, shell, ipcMain, net, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { pathToFileURL } = require('url');

const registry = require('./lib/registry');
const sidecar = require('./lib/sidecar');
const updater = require('./lib/updater');
const startup = require('./lib/startup');
const keeping = require('./lib/keeping');

const SMOKE = process.argv.includes('--smoke');
const BAR_H = 38;

// Tests point the shell at a throwaway profile so the real registry, cookies
// and sidecars are never touched.
if (process.env.GAMMA_SHELL_USER_DATA) app.setPath('userData', process.env.GAMMA_SHELL_USER_DATA);

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
  const [w, h] = win.getContentSize();
  // The bar's dropdown needs room below the strip: while a menu is open the
  // bar view grows over the content (its page is transparent outside the
  // strip and the menu, and a click there closes the menu).
  const barH = barExpanded ? Math.min(h, BAR_H + 420) : BAR_H;
  bar.setBounds({ x: 0, y: 0, width: w, height: barH });
  content.setBounds({ x: 0, y: BAR_H, width: w, height: Math.max(0, h - BAR_H) });
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
  applyTheme(); // BaseWindow does not keep the constructor's backgroundColor
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

// A directory in the system's file manager; under the test harness only
// recorded, like the external links.
function openDirectory(p) {
  externalOpens.push(p);
  if (!process.env.GAMMA_SHELL_TEST) shell.openPath(p);
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

// Local servers to run for as long as the app does, whichever server the
// window shows: the hosts of offline copies and of folders kept of remote
// servers (both sync only while their server runs) and, in background mode,
// every local server, since a folder kept of a local workspace is its own
// server's job. Best effort, after the window is up; a server already
// running (the one just opened) is left alone.
function startBackgroundHosts() {
  const state = registry.load();
  const hosts = new Set(state.mirrors.map((m) => m.server));
  if (state.settings.folderHost) hosts.add(state.settings.folderHost);
  const all = Boolean(state.settings.background);
  for (const srv of state.servers) {
    if (srv.type !== 'local' || (!all && !hosts.has(srv.id)) || sidecar.status(srv.id)) continue;
    sidecar
      .start(srv, state.settings, appInfo())
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
// local server or a remote: what keepOffline and the folders on disk use.
function serverApi(ses) {
  return async (origin, apiPath, init) => {
    const r = await ses.fetch(origin + apiPath, { credentials: 'include', cache: 'no-store', ...init });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `${apiPath}: HTTP ${r.status}`);
    return body;
  };
}

// Revoke a token the shell minted on a server for workspace `wsId`. The
// workspace header matters: a token is listed and deleted per workspace.
// Best effort (the caller is already reporting something else).
function revokeToken(api, origin, wsId, tokenId) {
  return api(origin, `/api/integrations/tokens/${tokenId}`, { method: 'DELETE', headers: { 'X-Gamma-Workspace': wsId } }).catch(() => {});
}

// The local server that hosts what this computer keeps of remote servers —
// the clones and the folders on disk: the first local server, made when
// there is none, started, and signed into with its seeded credentials.
async function ensureHost(api) {
  let local = registry.load().servers.find((s) => s.type === 'local');
  if (!local) local = registry.addLocal('Local');
  const entry = await sidecar.start(local, registry.getSettings(), appInfo());
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

// ------------------------------------------------- folders on this computer -----
// "Keep a folder on this computer": a folder of the open server's workspace
// written to a directory of the user's choice — PDFs beside Markdown notes —
// and kept up to date by a local server (docs/dev/folder_sync.md "Folders
// kept by the desktop app"). On a LOCAL server the link is that server's own; it reads
// the workspace in-process. On a REMOTE server the link lives on the host
// local server (ensureHost) with a read-scope token minted on the remote,
// and the host reads the folder over HTTP: no clone, only the folder's files
// come down. The shell asks for the directory (the native picker;
// GAMMA_SHELL_PICK_DIR in tests), mints the token, and makes the link
// through the servers' public API with the content session's cookies; only
// local sidecars keep links (GAMMA_FOLDER_LINKS), and they take the full
// path. Two ways in: the sync panel's chooser, and a folder's "Keep on
// this computer…" in Gamma's own menu, which the page preload passes on
// (shell:keep-folder-from-page). The rounds are the server's; the sync
// panel (below, "kept on this computer") shows each kept folder's state,
// pauses or resumes it (pauseFolder), syncs it (syncFolder) and stops it
// (dropFolder, the files kept or taken back; the token it minted revoked).
// An empty directory is used as it is; one holding anything gets a
// subdirectory named after the folder.

function openOrigin() {
  if (!current || !content) throw new Error('Open a server first');
  return new URL(current.url).origin;
}

// The links of the open server's workspace: a local server's own, or the
// host's links that read this remote's workspace. `server` names the local
// server holding each. No local server yet: none.
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

async function listFolders(wsId) {
  const api = serverApi(content.webContents.session);
  const [tree, links] = await Promise.all([
    api(openOrigin(), '/api/sync/folders', { headers: { 'X-Gamma-Workspace': wsId } }),
    listLinks(api, wsId),
  ]);
  return { folders: tree.folders || [], links };
}

// The native directory picker: the path chosen, null when cancelled.
async function chooseDirectory(opts) {
  const r = win ? await dialog.showOpenDialog(win, opts) : await dialog.showOpenDialog(opts);
  return r.canceled ? null : r.filePaths[0] || null;
}

function pickDirectory(name) {
  if (process.env.GAMMA_SHELL_PICK_DIR) return process.env.GAMMA_SHELL_PICK_DIR;
  return chooseDirectory({
    title: `Keep “${name}” on this computer`,
    message: `Choose where “${name}” goes. An empty folder is used as it is; any other gets a folder named “${name}” inside.`,
    buttonLabel: 'Keep here',
    properties: ['openDirectory', 'createDirectory'],
    defaultPath: app.getPath('documents'),
  });
}

function isEmptyDir(p) {
  try {
    return fs.readdirSync(p).length === 0;
  } catch {
    return true; // not there yet: the server makes it
  }
}

async function keepFolder(wsId, folderId) {
  const origin = openOrigin();
  const remote = current.type === 'remote';
  const api = serverApi(content.webContents.session);
  const g = gamma;
  const wsName = (g && (g.list.find((w) => w.id === wsId) || {}).name) || 'Library';
  const tree = await api(origin, '/api/sync/folders', { headers: { 'X-Gamma-Workspace': wsId } });
  const folder = folderId === 'root' ? { path: [] } : (tree.folders || []).find((f) => f.id === folderId);
  if (!folder) throw new Error('Unknown folder');
  const name = folder.path.length ? folder.path[folder.path.length - 1] : wsName;
  // Kept already: open it rather than make a second copy.
  const kept = (await listLinks(api, wsId)).find((l) => l.folder_id === folderId);
  if (kept) {
    openDirectory(kept.dest);
    showNotice(`${name} is already on this computer at ${kept.dest}`);
    return kept;
  }
  const picked = await pickDirectory(name);
  if (!picked) return null;
  const target = isEmptyDir(picked) ? picked : path.join(picked, name);
  busy = `Keeping ${name} on this computer…`;
  pushState();
  let token = null;
  try {
    let link;
    if (remote) {
      // A read token on the remote, for the host to read the folder with; the link on the host.
      token = await api(origin, '/api/integrations/tokens', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Gamma-Workspace': wsId },
        body: JSON.stringify({ name: `Gamma desktop: ${name} on disk`.slice(0, 80), scope: 'read', expires_in_days: 365 }),
      });
      const host = await ensureHost(api);
      link = await api(host.origin, '/api/folder-links', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder: folderId, path: target, notes: true, remote_url: origin, workspace: wsId, token: token.token, token_id: token.id }),
      });
      token = null; // the host holds it now
      registry.setSettings({ folderHost: host.local.id }); // runs at launch with the clones' hosts
    } else {
      link = await api(origin, '/api/folder-links', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Gamma-Workspace': wsId },
        body: JSON.stringify({ folder: folderId, path: target, notes: true }),
      });
    }
    showNotice(`${name} is being written to ${link.dest}`);
    offerBackground(`“${name}” on this computer`).catch(() => {});
    refreshKeeping().catch(() => {});
    return link;
  } catch (e) {
    if (token) await revokeToken(api, origin, wsId, token.id); // minted for a link that was never made
    throw e;
  } finally {
    busy = null;
    pushState();
  }
}

// A kept folder in words: its name from the path its last round saw.
function linkName(link) {
  if (link.folder_id === 'root') return 'The whole library';
  const names = (link.status && link.status.folder_path) || [];
  return names.length ? `“${names[names.length - 1]}”` : 'The folder';
}

// The native question, or its answer under the test harness (the first
// choice that writes nothing extra).
async function ask(opts, testAnswer) {
  if (process.env.GAMMA_SHELL_TEST) return testAnswer;
  const r = win ? await dialog.showMessageBox(win, opts) : await dialog.showMessageBox(opts);
  return r.response;
}

// A link of local server `serverId`, with what to call it through.
async function linkOn(serverId, linkId) {
  const at = await localServer(serverId);
  const link = ((await at.api(at.origin, '/api/folder-links')).links || []).find((l) => l.id === linkId);
  if (!link) throw new Error('This folder is not kept on this computer');
  // A link of the server's own workspace is changed in that workspace; one with a remote source is the account's.
  return { ...at, link, headers: link.remote_url ? {} : { 'X-Gamma-Workspace': link.workspace_id } };
}

// A round of a kept folder now, waited for, its outcome in the bar. Files
// changed on disk stay unless the user says to replace them, asked only
// when there are some (`force` answers that in tests).
async function syncFolder(serverId, linkId, force) {
  const { origin, api, link, headers } = await linkOn(serverId, linkId);
  const changed = ((link.status && link.status.kept) || []).length;
  if (force === undefined && changed) {
    const r = await ask({
      type: 'question', buttons: ['Keep my changes', 'Replace them', 'Cancel'], defaultId: 0, cancelId: 2,
      message: `${changed} file${changed === 1 ? '' : 's'} of ${linkName(link)} changed on this computer.`,
      detail: 'The sync leaves files you changed alone. Replace them with what Gamma holds?',
    }, 0);
    if (r === 2) return null;
    force = r === 1;
  }
  busy = `Syncing ${linkName(link)}…`;
  pushState();
  try {
    const after = await api(origin, `/api/folder-links/${link.id}/sync?wait=1${force ? '&force=1' : ''}`, { method: 'POST', headers });
    const s = after.status || {};
    const moved = Object.entries(s.counts || {}).filter(([k, n]) => n && k !== 'unchanged' && k !== 'kept').map(([k, n]) => `${n} ${k}`);
    showNotice(s.last_error ? `${linkName(link)}: ${s.last_error}` : `${linkName(link)} is up to date${moved.length ? `: ${moved.join(', ')}` : ''}.`);
    return after;
  } finally {
    busy = null;
    pushState();
    refreshKeeping().catch(() => {});
  }
}

// Pause a kept folder or resume it (a round runs then). Paused, the server's
// loop leaves it alone; a sync asked for still runs.
async function pauseFolder(serverId, linkId, paused) {
  const { origin, api, link, headers } = await linkOn(serverId, linkId);
  await api(origin, `/api/folder-links/${link.id}`, {
    method: 'PATCH', headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify({ paused }),
  });
  showNotice(paused ? `${linkName(link)} is paused: ${link.dest} stays as it is until you resume or sync it.`
    : `${linkName(link)} is kept up to date again.`);
  refreshKeeping().catch(() => {});
  return true;
}

// Stop keeping a folder: the link goes, and the token the shell minted on
// a remote for it is revoked there. The files stay, or with `how` 'remove'
// what the sync wrote is taken back; asked when not given.
async function dropFolder(serverId, linkId, how) {
  const { origin, api, link, headers } = await linkOn(serverId, linkId);
  if (!how) {
    const r = await ask({
      type: 'question', buttons: ['Stop, keep the files', 'Stop and remove the files', 'Cancel'], defaultId: 0, cancelId: 2,
      message: `Stop keeping ${linkName(link)} on this computer?`,
      detail: `${link.dest} stops updating. Removing the files takes back only what the sync wrote there; files you added or changed stay.`,
    }, 0);
    how = ['keep', 'remove', null][r];
    if (!how) return false;
  }
  busy = 'Stopping…';
  pushState();
  try {
    await api(origin, `/api/folder-links/${link.id}${how === 'remove' ? '?remove_files=1' : ''}`, { method: 'DELETE', headers });
    if (link.remote_url) {
      // The token was minted with the window's session, which is signed into the remote.
      if (link.token_id) await revokeToken(serverApi(session.defaultSession), new URL(link.remote_url).origin, link.workspace_id, link.token_id);
      // The host's last remote folder gone: it need not start at launch for it any more.
      const left = ((await api(origin, '/api/folder-links')).links || []).filter((l) => l.remote_url);
      if (!left.length && registry.getSettings().folderHost === serverId) registry.setSettings({ folderHost: '' });
    }
    showNotice(how === 'remove' ? `${linkName(link)} is no longer on this computer; the files the sync wrote are gone.`
      : `${link.dest} is no longer kept up to date; its files stay.`);
    return true;
  } finally {
    busy = null;
    pushState();
    refreshKeeping().catch(() => {});
  }
}

// ------------------------------------------------- kept on this computer -----
// The bar's sync button and its panel (docs/architecture.md "Kept on this
// computer"): every clone and folder on disk the running local servers
// keep, read from each with the default session (signed in on first use),
// turned into states and one summary by lib/keeping.js, and pushed in the
// shell state as `keeping`. Read every KEEPING_MS, every KEEPING_BUSY_MS
// while something syncs, on the panel's opening and after each action. A
// row names its server, so the panel acts whichever server the window shows.

const KEEPING_MS = 15_000;
const KEEPING_BUSY_MS = 2_000;
let keepingState = { items: [], summary: keeping.summarize([]) };
let keepingTimer = null;
let keepingRead = null;

function scheduleKeeping(ms) {
  clearTimeout(keepingTimer);
  keepingTimer = setTimeout(() => refreshKeeping().catch(() => {}), ms);
}

function refreshKeeping() {
  if (keepingRead) return keepingRead;
  keepingRead = (async () => {
    const { servers } = registry.load();
    const items = [];
    for (const srv of servers) {
      if (srv.type !== 'local' || !sidecar.status(srv.id)) continue;
      try {
        const { origin, api } = await localServer(srv.id);
        const [m, f] = await Promise.all([api(origin, '/api/mirrors'), api(origin, '/api/folder-links')]);
        items.push(...keeping.itemsOf(srv, { mirrors: m.mirrors, links: f.links }, servers));
      } catch {
        // a server that does not answer now is left out of this reading
      }
    }
    keepingState = { items, summary: keeping.summarize(items) };
    pushState();
  })().finally(() => {
    keepingRead = null;
    scheduleKeeping(keepingState.summary.state === 'busy' ? KEEPING_BUSY_MS : KEEPING_MS);
  });
  return keepingRead;
}

// What a panel row does: a clone opens, pauses (detaches) or resumes
// (reattaches, what both sides did meanwhile merging), or syncs; a folder
// opens its directory, pauses or resumes, syncs, or stops.
async function keepingAction(kind, serverId, id, action) {
  const item = keepingState.items.find((i) => i.kind === kind && i.server === serverId && i.id === id);
  if (!item) throw new Error('That is no longer kept on this computer');
  if (kind === 'clone' && action === 'open') {
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
  } else if (kind === 'folder' && action === 'open') {
    openDirectory(item.dest);
  } else if (kind === 'folder' && (action === 'pause' || action === 'resume')) {
    return pauseFolder(serverId, id, action === 'pause');
  } else if (kind === 'folder' && action === 'sync') {
    return syncFolder(serverId, id);
  } else if (kind === 'folder' && action === 'stop') {
    return dropFolder(serverId, id);
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

// ---------------------------------------------------------- background -----
// "Keep running in the background": closing the window leaves the app in
// the tray with every local server running, so clones and folders on disk
// keep syncing (docs/architecture.md "Background and tray"). Off, closing
// the last window quits (macOS keeps the dock process either way). "Start
// at login" launches it hidden, straight into the tray.

function trayIcon() {
  const img = nativeImage.createFromPath(path.join(__dirname, 'assets', 'icon.png'));
  const size = process.platform === 'darwin' ? 18 : 16;
  return img.resize({ width: size, height: size });
}

// The two switches as menu items, under the labels the menu's case wants.
function backgroundItems(keepLabel, loginLabel) {
  const s = registry.getSettings();
  return [
    { label: keepLabel, type: 'checkbox', checked: Boolean(s.background), click: (item) => setBackground(item.checked) },
    { label: loginLabel, type: 'checkbox', checked: Boolean(s.openAtLogin), click: (item) => setOpenAtLogin(item.checked) },
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

// The window, back or new: where the user left off (the launcher is one
// click away in the bar), or the launcher. Resolves once that is showing.
function showWindow() {
  if (win) {
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
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
    // what this computer keeps (the sync button and its panel)
    keeping: keepingState,
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
  ipcMain.handle('shell:keeping', shellOnly(() => { refreshKeeping().catch(() => {}); return keepingState; }));
  ipcMain.handle('shell:keeping-action', shellOnly((kind, serverId, id, action) =>
    withDialog('Could not do that.', () => keepingAction(kind, serverId, id, action))()));
  // A folder's "Keep on this computer…" in Gamma's own menu, passed on by the
  // page preload: only from the content view's page of the open server, one
  // action at a time. The native directory picker is the user's say, so the
  // page can start nothing on its own.
  ipcMain.on('shell:keep-folder-from-page', (event, wsId, folderId) => {
    if (!content || event.sender !== content.webContents || busy || !current) return;
    let from = '';
    try { from = new URL(event.senderFrame.url).origin; } catch {}
    if (from !== openOrigin() || !/^[\w-]{1,64}$/.test(wsId) || !/^[\w-]{1,64}$/.test(folderId)) return;
    withDialog('Could not keep the folder on this computer.', () => keepFolder(wsId, folderId))().catch(() => {});
  });
  // Reveal a directory a link of the open server's workspace writes — only such a one.
  ipcMain.handle('shell:open-path', shellOnly(async (wsId, p) => {
    const { links } = await listFolders(wsId);
    if (links.some((l) => l.dest === p)) openDirectory(p);
  }));
  ipcMain.handle('shell:pick-folder', shellOnly((defaultPath) =>
    chooseDirectory({ properties: ['openDirectory', 'createDirectory'], defaultPath: defaultPath || undefined })));
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
  if (hidden) startBackgroundHosts();
  else showWindow().finally(startBackgroundHosts);
  scheduleKeeping(3000); // what the servers keep, once they are up

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
    dropFolder,
    syncFolder,
    listFolders,
    refreshKeeping,
    keepingAction,
    keeping: () => keepingState,
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
    update: () => updater.state(),
    remoteHealth,
    probeRemotes,
    barExpanded: () => barExpanded,
    bounds: () => ({ bar: bar && bar.getBounds(), content: content && content.getBounds() }),
  };
}
