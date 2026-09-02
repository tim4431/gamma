// Gamma desktop shell. The app itself is untouched Gamma: a workspace is just
// a Gamma server (local sidecar or remote URL) and opening one navigates this
// window to it — the frontend always loads from the server it talks to, so
// there is no version skew and no API-base plumbing. The shell owns only the
// launcher page, the workspace registry, and sidecar lifecycles.

const { app, BrowserWindow, Menu, shell, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');

const registry = require('./lib/registry');
const sidecar = require('./lib/sidecar');

const SMOKE = process.argv.includes('--smoke');

let win = null;
// Origins the window may navigate to (workspace servers + the launcher).
// Anything else is handed to the system browser.
const allowedOrigins = new Set();

function appInfo() {
  return {
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    userDataDir: app.getPath('userData'),
  };
}

// ---------------------------------------------------------------- window ----

function createWindow() {
  win = new BrowserWindow({
    width: 1360,
    height: 900,
    icon: path.join(__dirname, 'build', 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.webContents.on('will-navigate', (event, url) => {
    let origin = null;
    try {
      origin = new URL(url).origin;
    } catch {}
    if (url.startsWith('file:') || (origin && allowedOrigins.has(origin))) return;
    event.preventDefault();
    if (origin) shell.openExternal(url);
  });

  // target=_blank (external link chips, share links, downloads of papers
  // opened in a new tab) goes to the system browser.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });

  win.on('closed', () => {
    win = null;
  });
}

function loadLauncher() {
  if (win) win.loadFile(path.join(__dirname, 'launcher', 'index.html'));
}

// ----------------------------------------------------------- auto-login -----

// Local workspaces log in silently with the credentials the shell seeded.
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

async function openWorkspace(id) {
  const ws = registry.get(id);
  if (!ws) throw new Error('Unknown workspace');
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
  if (!win) createWindow();
  await win.loadURL(url);
  if (creds) {
    const result = await win.webContents
      .executeJavaScript(autoLoginScript(creds.username, creds.password))
      .catch((e) => 'exec-error:' + e);
    return { url, autoLogin: String(result) };
  }
  return { url };
}

// ----------------------------------------------------------------- menu -----

function buildMenu() {
  const { workspaces } = registry.load();
  const template = [
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' }] : []),
    {
      label: 'Workspace',
      submenu: [
        {
          label: 'Open Launcher',
          accelerator: 'CmdOrCtrl+Shift+L',
          click: () => loadLauncher(),
        },
        { type: 'separator' },
        ...workspaces.map((ws) => ({
          label: `${ws.name}${ws.type === 'remote' ? '  (remote)' : ''}`,
          click: () =>
            openWorkspace(ws.id).catch((e) => {
              loadLauncher();
              console.error(e);
            }),
        })),
        { type: 'separator' },
        process.platform === 'darwin' ? { role: 'close' } : { role: 'quit' },
      ],
    },
    { role: 'editMenu' },
    { role: 'viewMenu' },
    { role: 'windowMenu' },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ------------------------------------------------------------------ IPC -----
// The launcher page (file://) is the only surface these are exposed to — the
// preload bridges them only on file: URLs, and we re-check the sender here.

function launcherOnly(handler) {
  return (event, ...args) => {
    const url = event.senderFrame ? event.senderFrame.url : '';
    if (!url.startsWith('file:')) throw new Error('Not allowed');
    return handler(...args);
  };
}

function registerIpc() {
  ipcMain.handle(
    'shell:list',
    launcherOnly(() => {
      const state = registry.load();
      return {
        workspaces: state.workspaces.map((ws) => ({
          ...ws,
          running: ws.type === 'local' ? Boolean(sidecar.status(ws.id)) : undefined,
        })),
        settings: state.settings,
        detected: sidecar.detectDev(state.settings),
        version: app.getVersion(),
        packaged: app.isPackaged,
      };
    })
  );
  ipcMain.handle('shell:add-local', launcherOnly((name) => { const ws = registry.addLocal(name); buildMenu(); return ws; }));
  ipcMain.handle('shell:add-remote', launcherOnly((name, url) => { const ws = registry.addRemote(name, url); buildMenu(); return ws; }));
  ipcMain.handle(
    'shell:remove',
    launcherOnly((id, opts) => {
      sidecar.stop(id);
      registry.remove(id, opts || {});
      buildMenu();
    })
  );
  ipcMain.handle('shell:open', launcherOnly((id) => openWorkspace(id)));
  ipcMain.handle(
    'shell:reveal-data',
    launcherOnly((id) => {
      const ws = registry.get(id);
      if (ws && ws.type === 'local') shell.openPath(ws.dataDir);
    })
  );
  ipcMain.handle('shell:set-settings', launcherOnly((patch) => registry.setSettings(patch)));
}

// ---------------------------------------------------------------- smoke -----
// `electron . --smoke`: headless-ish end-to-end check used by dev + CI.
// Spins up a throwaway local workspace, waits for health, loads it, verifies
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
    await win.loadURL(entry.url);
    const login = await win.webContents.executeJavaScript(
      autoLoginScript(ws.adminUser, ws.adminPassword)
    );
    out.firstLogin = String(login);
    if (String(login) === 'logged-in') {
      await new Promise((r) => setTimeout(r, 1500)); // let the reload settle
      const who = await win.webContents.executeJavaScript(
        `fetch('/api/session',{credentials:'same-origin'}).then(r=>r.json()).then(j=>j.user||'')`
      );
      out.session = String(who);
      out.ok = who === 'admin';
    } else {
      out.ok = String(login).startsWith('already:');
    }
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

app.whenReady().then(() => {
  registry.init(app.getPath('userData'));
  if (SMOKE) {
    runSmoke();
    return;
  }
  registerIpc();
  buildMenu();
  createWindow();
  loadLauncher();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
      loadLauncher();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => sidecar.stopAll());
process.on('exit', () => sidecar.stopAll());
