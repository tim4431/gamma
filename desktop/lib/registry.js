// Workspace registry: the shell's only persistent state, a JSON file in the
// Electron userData dir. A workspace is a name plus either a server URL
// (remote) or a data directory the shell runs a local server over (local).
// Local workspaces also remember the admin credentials the shell generated on
// first run — the server's one-time password print would otherwise be lost in
// the hidden sidecar console.

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const FILE = 'workspaces.json';

const DEFAULTS = {
  // Overrides for dev mode; empty strings mean "auto-detect the repo layout".
  settings: { pythonPath: '', backendDir: '', staticDir: '' },
  workspaces: [],
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
    name: name || 'Local workspace',
    type: 'local',
    dataDir: path.join(userDataDir, 'workspaces', id),
    adminUser: 'admin',
    adminPassword: newPassword(),
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
  const ws = {
    id: newId(),
    name: name || parsed.host,
    type: 'remote',
    url: parsed.origin,
  };
  state.workspaces.push(ws);
  save(state);
  return ws;
}

function get(id) {
  return load().workspaces.find((w) => w.id === id) || null;
}

function remove(id, { deleteData = false } = {}) {
  const state = load();
  const ws = state.workspaces.find((w) => w.id === id);
  if (!ws) return;
  state.workspaces = state.workspaces.filter((w) => w.id !== id);
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

function getSettings() {
  return load().settings;
}

function setSettings(patch) {
  const state = load();
  state.settings = { ...state.settings, ...patch };
  save(state);
  return state.settings;
}

module.exports = { init, load, get, addLocal, addRemote, remove, getSettings, setSettings };
