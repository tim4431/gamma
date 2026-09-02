// Workspace registry: the shell's only persistent state, a JSON file in the
// Electron userData dir. A workspace is a name plus either a server URL
// (remote) or a data directory the shell runs a local server over (local).
// Local workspaces also remember the admin credentials the shell generated on
// first run — the server's one-time password print would otherwise be lost in
// the hidden sidecar console.
//
// Besides the list the file keeps a little shell UX state: the workspace
// opened last (reopened at launch when `openLastOnLaunch`), the theme the
// Gamma page last reported (so the launcher/shell bar paint in it before any
// workspace is loaded), and the window bounds.

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const FILE = 'workspaces.json';

const DEFAULTS = {
  settings: {
    // Overrides for dev mode; empty strings mean "auto-detect the repo layout".
    pythonPath: '',
    backendDir: '',
    staticDir: '',
    // Reopen the last workspace at launch instead of showing the launcher.
    openLastOnLaunch: true,
    // Last data-theme the Gamma page reported ('' = never seen → dark).
    lastTheme: '',
  },
  workspaces: [],
  lastOpened: null,
  windowBounds: null,
};

let userDataDir = null;

function init(dir) {
  userDataDir = dir;
  fs.mkdirSync(dir, { recursive: true });
}

function filePath() {
  return path.join(userDataDir, FILE);
}

function load() {
  try {
    const raw = JSON.parse(fs.readFileSync(filePath(), 'utf8'));
    return {
      settings: { ...DEFAULTS.settings, ...(raw.settings || {}) },
      workspaces: Array.isArray(raw.workspaces) ? raw.workspaces : [],
      lastOpened: raw.lastOpened || null,
      windowBounds: raw.windowBounds || null,
    };
  } catch {
    return JSON.parse(JSON.stringify(DEFAULTS));
  }
}

function save(state) {
  fs.mkdirSync(userDataDir, { recursive: true });
  fs.writeFileSync(filePath(), JSON.stringify(state, null, 2));
}

function newId() {
  return crypto.randomBytes(6).toString('hex');
}

function newPassword() {
  return crypto.randomBytes(9).toString('base64url'); // 12 chars, like the server's own seed
}

function addLocal(name) {
  const state = load();
  const id = newId();
  const ws = {
    id,
    name: (name || '').trim() || 'Local workspace',
    type: 'local',
    dataDir: path.join(userDataDir, 'workspaces', id),
    adminUser: 'admin',
    adminPassword: newPassword(),
    createdAt: new Date().toISOString(),
  };
  fs.mkdirSync(ws.dataDir, { recursive: true });
  state.workspaces.push(ws);
  save(state);
  return ws;
}

function addRemote(name, url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    throw new Error('Invalid URL');
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error('Workspace URL must be http:// or https://');
  }
  const state = load();
  if (state.workspaces.some((w) => w.type === 'remote' && w.url === parsed.origin)) {
    throw new Error(`${parsed.origin} is already a workspace`);
  }
  const ws = {
    id: newId(),
    name: (name || '').trim() || parsed.host,
    type: 'remote',
    url: parsed.origin,
    createdAt: new Date().toISOString(),
  };
  state.workspaces.push(ws);
  save(state);
  return ws;
}

function get(id) {
  return load().workspaces.find((w) => w.id === id) || null;
}

function rename(id, name) {
  const state = load();
  const ws = state.workspaces.find((w) => w.id === id);
  if (!ws) throw new Error('Unknown workspace');
  const clean = (name || '').trim();
  if (!clean) throw new Error('Name cannot be empty');
  ws.name = clean;
  save(state);
  return ws;
}

function remove(id, { deleteData = false } = {}) {
  const state = load();
  const ws = state.workspaces.find((w) => w.id === id);
  if (!ws) return;
  state.workspaces = state.workspaces.filter((w) => w.id !== id);
  if (state.lastOpened === id) state.lastOpened = null;
  save(state);
  if (deleteData && ws.type === 'local' && ws.dataDir) {
    // Guard: only ever delete directories we created under our own userData.
    const root = path.join(userDataDir, 'workspaces');
    const resolved = path.resolve(ws.dataDir);
    if (resolved.startsWith(root + path.sep)) {
      fs.rmSync(resolved, { recursive: true, force: true });
    }
  }
}

// Remember which workspace is open (reopened at next launch).
function markOpened(id) {
  const state = load();
  const ws = state.workspaces.find((w) => w.id === id);
  if (!ws) return;
  ws.lastOpenedAt = new Date().toISOString();
  state.lastOpened = id;
  save(state);
}

function getLastOpened() {
  const state = load();
  return state.workspaces.find((w) => w.id === state.lastOpened) || null;
}

function getSettings() {
  return load().settings;
}

function setSettings(patch) {
  const state = load();
  state.settings = { ...state.settings, ...patch };
  save(state);
  return state.settings;
}

function getWindowBounds() {
  return load().windowBounds;
}

function setWindowBounds(bounds) {
  const state = load();
  state.windowBounds = bounds;
  save(state);
}

// Bytes on disk under a local workspace's data dir (SQLite files + uploads).
// Synchronous walk; libraries are at most a few thousand files.
function dirSize(dir) {
  let total = 0;
  const walk = (d) => {
    let entries;
    try {
      entries = fs.readdirSync(d, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.isFile()) {
        try {
          total += fs.statSync(p).size;
        } catch {}
      }
    }
  };
  walk(dir);
  return total;
}

module.exports = {
  init, load, get, addLocal, addRemote, rename, remove, markOpened, getLastOpened,
  getSettings, setSettings, getWindowBounds, setWindowBounds, dirSize,
};
