// Server registry: the shell's only persistent state, a JSON file in the
// Electron userData dir. A server is a name plus either a URL (remote) or a
// data directory the shell runs a local Gamma over (local). Gamma's own
// workspaces (the libraries inside a server, with members and roles) live in
// that server's data — the shell only lists them in its switcher.
//
// Local servers also remember the admin credentials the shell generated on
// first run — the server's one-time password print would otherwise be lost
// in the hidden sidecar console — and the port they last ran on (`port`).
//
// Besides the list the file keeps a little shell UX state: the server opened
// last (reopened at launch when `openLastOnLaunch`), the theme the Gamma
// page last reported (so the launcher/shell bar paint in it before any page
// is loaded), and the window bounds.
//
// `mirrors` is the map of offline copies (docs/dev/mirror.md): which local
// server + workspace mirrors which remote workspace (`remoteUrl` origin +
// `remoteWs`). Written when the shell makes a copy and replaced from a
// local server's own `/api/mirrors` whenever that server is open, so copies
// made or stopped from Gamma's Settings show up too. The shell keeps no
// sync state and no token — only this map, for the switcher's cross-links
// and for starting the servers that hold copies at launch.
//
// `zoteroHost` on a local server's entry: that server holds a Zotero link
// that is not detached (docs/dev/zotero_sync.md), as the shell last read
// its `/api/zotero/links`. A link syncs only while its server runs, so
// those servers start at launch like the clones' hosts (`launchHosts`).
//
// `access` is the Mac App Store build's alone: the folders outside its
// sandbox the user picked, as security-scoped bookmarks
// (lib/folderAccess.js).
//
// What the shell knows of the remote servers' addresses and certificates
// (docs/dev/https.md): `pins`, the certificates the person trusted by their
// fingerprint, by origin (lib/certs.js: `{ sha256, subject, issuer,
// validFrom, validTo, pinnedAt }`), which main.js hands to the local
// servers; `moved`, the addresses a server's links moved from, each with
// where they went (`{ to, at, kind }`, setRemoteUrl), so a load of an old
// address goes to the current one (currentOrigin); a remote's `httpsSeen`,
// set once it loaded over https; and `settings.plainAccepted`, the origins
// the person agreed to reach over plain http (main.js beforeMint).
//
// Local server data dirs live under ONE root, `<root>/<server id>`:
// `settings.dataRoot` when set, else `<userData>/workspaces` (the folder
// name predates the rename and stays so existing installs need no move).
// `setDataRoot` switches the root and, on request, moves the existing dirs
// there (copy, verify, re-point the registry, then delete the originals —
// safe across drives; callers stop the sidecars first).

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const hostScope = require('./hostScope');

const FILE = 'servers.json';
const LEGACY_FILE = 'workspaces.json'; // the registry's name before servers had that name

const DEFAULTS = {
  settings: {
    // A dev run's server overrides (a packaged build ignores them); empty
    // strings mean "auto-detect the repo layout".
    pythonPath: '',
    backendDir: '',
    staticDir: '',
    // Reopen the last server at launch instead of showing the launcher.
    openLastOnLaunch: true,
    // Last data-theme the Gamma page reported ('' = never seen: follow the OS).
    lastTheme: '',
    // Folder new local servers are created in ('' = <userData>/workspaces).
    dataRoot: '',
    // Keep running in the tray when the window closes, so clones and
    // synced vaults keep syncing; and start hidden at login.
    background: false,
    openAtLogin: false,
    // The local server that keeps the synced vaults of remote servers on
    // this disk (main.js addFolderVault): started at launch like the clones' hosts.
    folderHost: '',
    // The origins a token may go to over plain http:// without asking again.
    plainAccepted: [],
  },
  servers: [],
  mirrors: [],
  access: [],
  pins: {},
  moved: {},
  lastOpened: null,
  windowBounds: null,
};

let userDataDir = null;

function init(dir) {
  userDataDir = dir;
  fs.mkdirSync(dir, { recursive: true });
  // One-time rename of the registry file, its list moved from `workspaces`
  // to `servers`; the old copy stays for a rollback.
  const legacy = path.join(dir, LEGACY_FILE);
  if (!fs.existsSync(filePath()) && fs.existsSync(legacy)) {
    try {
      const { workspaces, ...rest } = JSON.parse(fs.readFileSync(legacy, 'utf8'));
      save({ ...rest, servers: rest.servers || workspaces || [] });
    } catch {}
  }
}

function filePath() {
  return path.join(userDataDir, FILE);
}

const isMap = (v) => Boolean(v) && typeof v === 'object' && !Array.isArray(v);

function load() {
  try {
    const raw = JSON.parse(fs.readFileSync(filePath(), 'utf8'));
    return {
      settings: { ...DEFAULTS.settings, ...(raw.settings || {}) },
      servers: Array.isArray(raw.servers) ? raw.servers : [],
      mirrors: Array.isArray(raw.mirrors) ? raw.mirrors : [],
      access: Array.isArray(raw.access) ? raw.access : [],
      pins: isMap(raw.pins) ? raw.pins : {},
      moved: isMap(raw.moved) ? raw.moved : {},
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

function defaultDataRoot() {
  return path.join(userDataDir, 'workspaces');
}

// Where local servers are created now.
function dataRoot(state = load()) {
  return path.resolve(state.settings.dataRoot || defaultDataRoot());
}

function isUnder(dir, root) {
  const norm = (s) => (process.platform === 'win32' ? path.resolve(s).toLowerCase() : path.resolve(s));
  return norm(dir).startsWith(norm(root) + path.sep);
}

// The local servers whose data dir sits under the current root — the ones a
// root change moves.
function localsUnderRoot(state = load()) {
  const root = dataRoot(state);
  return state.servers.filter((s) => s.type === 'local' && s.dataDir && isUnder(s.dataDir, root));
}

// The bytes under `dir`, counted the way copyTree copies them (links not followed).
async function treeBytes(dir) {
  let n = 0;
  for (const e of await fs.promises.readdir(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) n += await treeBytes(p);
    else if (e.isFile()) n += (await fs.promises.stat(p)).size;
  }
  return n;
}

// `from` copied to `to`, a file at a time and asynchronously, so the shell
// stays live while it runs; `onBytes(n)` after each file, and after each chunk of a
// large one. Throws on any failed file, like fs.cpSync.
const STREAM_FROM = 8 * 1024 * 1024;
async function copyTree(from, to, onBytes) {
  await fs.promises.mkdir(to, { recursive: true });
  for (const e of await fs.promises.readdir(from, { withFileTypes: true })) {
    const src = path.join(from, e.name);
    const dest = path.join(to, e.name);
    if (e.isDirectory()) {
      await copyTree(src, dest, onBytes);
    } else if (e.isSymbolicLink()) {
      await fs.promises.symlink(await fs.promises.readlink(src), dest);
    } else if (e.isFile()) {
      const { size } = await fs.promises.stat(src);
      if (size < STREAM_FROM) {
        await fs.promises.copyFile(src, dest, fs.constants.COPYFILE_EXCL);
        onBytes(size);
      } else {
        await new Promise((resolve, reject) => {
          const r = fs.createReadStream(src);
          const w = fs.createWriteStream(dest, { flags: 'wx' });
          r.on('data', (chunk) => onBytes(chunk.length));
          r.on('error', reject);
          w.on('error', reject);
          w.on('finish', resolve);
          r.pipe(w);
        });
      }
    }
  }
}

// Change the storage root. `newRoot` '' resets to the default. With `move`
// every local server under the old root is copied to `<newRoot>/<id>`; only
// after ALL copies succeed does the registry switch over and the old copies
// get deleted, so a failure (disk full, permissions) leaves everything as it
// was. `onProgress({ name, done, total })`, in bytes over all the servers,
// as the copy goes. Returns { root, moved: [names] }.
async function setDataRoot(newRoot, { move = true, onProgress = () => {} } = {}) {
  const state = load();
  const oldRoot = dataRoot(state);
  const target = newRoot ? path.resolve(newRoot) : '';
  const targetRoot = target || path.resolve(defaultDataRoot());
  if (targetRoot === oldRoot) return { root: targetRoot, moved: [] };
  if (isUnder(targetRoot, oldRoot) || isUnder(oldRoot, targetRoot)) {
    throw new Error('The new folder must not be inside the current one (or contain it).');
  }
  fs.mkdirSync(targetRoot, { recursive: true });
  const moved = [];
  if (move) {
    const servers = localsUnderRoot(state);
    let total = 0;
    for (const srv of servers) if (fs.existsSync(srv.dataDir)) total += await treeBytes(srv.dataDir);
    let done = 0;
    for (const srv of servers) {
      const dest = path.join(targetRoot, path.basename(srv.dataDir));
      onProgress({ name: srv.name, done, total });
      try {
        if (fs.existsSync(dest)) throw new Error('already exists');
        await copyTree(srv.dataDir, dest, (n) => { done += n; onProgress({ name: srv.name, done, total }); });
      } catch (e) {
        for (const m of moved) fs.rmSync(m.dest, { recursive: true, force: true });
        fs.rmSync(dest, { recursive: true, force: true });
        throw new Error(`Could not move "${srv.name}" to ${dest}: ${e.message}`);
      }
      moved.push({ srv, from: srv.dataDir, dest });
    }
    for (const m of moved) m.srv.dataDir = m.dest;
  }
  state.settings.dataRoot = target;
  save(state);
  for (const m of moved) {
    try {
      fs.rmSync(m.from, { recursive: true, force: true });
    } catch {}
  }
  return { root: targetRoot, moved: moved.map((m) => m.srv.name) };
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
  const srv = {
    id,
    name: (name || '').trim() || 'Local',
    type: 'local',
    dataDir: path.join(dataRoot(state), id),
    adminUser: 'admin',
    adminPassword: newPassword(),
    createdAt: new Date().toISOString(),
  };
  fs.mkdirSync(srv.dataDir, { recursive: true });
  state.servers.push(srv);
  save(state);
  return srv;
}

// A typed address without a scheme, by the shared host table
// (lib/hostScope.js): https:// for the ports TLS answers on (443, 5001,
// Synology's, 8443) and for a public name or address, whatever the port;
// http:// for this computer and the local network, the self-hosted Gamma's
// plain port.
const HTTPS_PORTS = ['443', '5001', '8443'];
function withScheme(text) {
  const t = String(text || '').trim();
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(t)) return t;
  const authority = t.split(/[/?#]/)[0];
  const [, host, port] = /^(\[[^\]]*\]|[^:]*)(?::(\d+))?$/.exec(authority) || [null, authority, ''];
  const scope = hostScope.literal(host);
  const secure = HTTPS_PORTS.includes(port || '') || scope === hostScope.NAME || scope === hostScope.FAR;
  return (secure ? 'https://' : 'http://') + t;
}

function parseServerUrl(url) {
  let parsed;
  try {
    parsed = new URL(withScheme(url));
  } catch {
    throw new Error('Invalid URL');
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error('Server URL must be http:// or https://');
  }
  return parsed;
}

// What an address change is: the same host name from http:// to https://
// (any port: Synology's 5000 → 5001), https:// to http://, or another.
function moveKind(before, after) {
  try {
    const a = new URL(before);
    const b = new URL(after);
    if (a.hostname === b.hostname && a.protocol === 'http:' && b.protocol === 'https:') return 'upgrade';
    if (a.protocol === 'https:' && b.protocol === 'http:') return 'downgrade';
  } catch {}
  return 'other';
}

const hostOfUrl = (url) => {
  try { return new URL(url).hostname; } catch { return ''; }
};

// A remote server's address, changed once its links moved there (main.js
// moveServer): the mirrors of its workspaces follow (they are keyed by the
// remote's origin), the old address is recorded as moved (`moved`, which
// currentOrigin follows), a host name it no longer has was not seen over
// https, and the old address's pin goes with it: to the new address on the
// same host name (where the window already took it for any port), dropped
// for another one. → `{ srv, before, pins }`, `pins` whether they changed.
function setRemoteUrl(id, url) {
  const state = load();
  const srv = state.servers.find((s) => s.id === id && s.type === 'remote');
  if (!srv) throw new Error('Unknown server');
  const parsed = parseServerUrl(url);
  if (state.servers.some((s) => s.id !== id && s.type === 'remote' && s.url === parsed.origin)) {
    throw new Error(`${parsed.origin} is already listed`);
  }
  const before = srv.url;
  srv.url = parsed.origin;
  for (const m of state.mirrors) if (m.remoteUrl === before) m.remoteUrl = parsed.origin;
  let pins = false;
  if (before !== parsed.origin) {
    state.moved[before] = { to: parsed.origin, at: new Date().toISOString(), kind: moveKind(before, parsed.origin) };
    delete state.moved[parsed.origin]; // an address in use again is no longer a moved one
    const sameHost = hostOfUrl(before) === parsed.hostname;
    if (!sameHost) delete srv.httpsSeen;
    if (state.pins[before]) {
      if (sameHost && /^https:/.test(parsed.origin) && !state.pins[parsed.origin]) state.pins[parsed.origin] = state.pins[before];
      delete state.pins[before];
      pins = true;
    }
  }
  save(state);
  return { srv, before, pins };
}

// Where an address the links moved from is now: `moved` followed, at most 8
// steps and never round a cycle; any other address as it is.
function currentOrigin(origin, state = load()) {
  let at = origin;
  const seen = new Set([at]);
  for (let i = 0; i < 8 && state.moved[at]; i++) {
    const next = state.moved[at].to;
    if (!next || seen.has(next)) break;
    seen.add(next);
    at = next;
  }
  return at;
}

// Whether `origin` is one the links moved away from.
function movedAway(origin, state = load()) {
  return currentOrigin(origin, state) !== origin;
}

// A remote loaded over https in the window: from then on an answer without
// TLS there reads as interference, never as a server without it
// (lib/startup.js).
function markHttpsSeen(id) {
  const state = load();
  const srv = state.servers.find((s) => s.id === id && s.type === 'remote');
  if (!srv || srv.httpsSeen || !/^https:/.test(srv.url || '')) return;
  srv.httpsSeen = true;
  save(state);
}

function addRemote(name, url) {
  const parsed = parseServerUrl(url);
  const state = load();
  if (state.servers.some((s) => s.type === 'remote' && s.url === parsed.origin)) {
    throw new Error(`${parsed.origin} is already listed`);
  }
  const srv = {
    id: newId(),
    name: (name || '').trim() || parsed.host,
    type: 'remote',
    url: parsed.origin,
    createdAt: new Date().toISOString(),
  };
  state.servers.push(srv);
  delete state.moved[parsed.origin]; // an address listed again is in use, no longer a moved one
  save(state);
  return srv;
}

function get(id) {
  return load().servers.find((s) => s.id === id) || null;
}

function rename(id, name) {
  const state = load();
  const srv = state.servers.find((s) => s.id === id);
  if (!srv) throw new Error('Unknown server');
  const clean = (name || '').trim();
  if (!clean) throw new Error('Name cannot be empty');
  srv.name = clean;
  save(state);
  return srv;
}

function remove(id, { deleteData = false } = {}) {
  const state = load();
  const srv = state.servers.find((s) => s.id === id);
  if (!srv) return;
  state.servers = state.servers.filter((s) => s.id !== id);
  state.mirrors = state.mirrors.filter((m) => m.server !== id);
  // its certificate's pin goes with it, unless another entry has that address
  if (srv.type === 'remote' && !state.servers.some((s) => s.type === 'remote' && s.url === srv.url)) delete state.pins[srv.url];
  if (state.lastOpened === id) state.lastOpened = null;
  save(state);
  if (deleteData && srv.type === 'local' && srv.dataDir) {
    // Guard: only ever delete directories we created — under the default
    // root or the configured one — never an arbitrary folder.
    const resolved = path.resolve(srv.dataDir);
    if (isUnder(resolved, defaultDataRoot()) || isUnder(resolved, dataRoot(state))) {
      fs.rmSync(resolved, { recursive: true, force: true });
    }
  }
}

// The port a local server ran on, asked for again at its next start
// (lib/sidecar.js), so its address outlives a restart.
function setPort(id, port) {
  const state = load();
  const srv = state.servers.find((s) => s.id === id && s.type === 'local');
  if (!srv || srv.port === port) return;
  srv.port = port;
  save(state);
}

// Whether local server `id` holds a Zotero link that is not detached, as
// the shell last read its links (main.js refreshKeeping); such a server
// starts at launch. Written only when it changes.
function setZoteroHost(id, on) {
  const state = load();
  const srv = state.servers.find((s) => s.id === id && s.type === 'local');
  if (!srv || Boolean(srv.zoteroHost) === Boolean(on)) return;
  if (on) srv.zoteroHost = true;
  else delete srv.zoteroHost;
  save(state);
}

// The local servers to run for as long as the app does (main.js
// startBackgroundHosts): the hosts of clones, of synced vaults of remote
// servers (`settings.folderHost`) and of Zotero links, which all sync only
// while their server runs; in background mode, every local server.
function launchHosts(state = load()) {
  const hosts = new Set(state.mirrors.map((m) => m.server));
  if (state.settings.folderHost) hosts.add(state.settings.folderHost);
  const all = Boolean(state.settings.background);
  return state.servers.filter((s) => s.type === 'local' && (all || hosts.has(s.id) || Boolean(s.zoteroHost)));
}

// Remember which server is open (reopened at next launch).
function markOpened(id) {
  const state = load();
  const srv = state.servers.find((s) => s.id === id);
  if (!srv) return;
  srv.lastOpenedAt = new Date().toISOString();
  state.lastOpened = id;
  save(state);
}

function getLastOpened() {
  const state = load();
  return state.servers.find((s) => s.id === state.lastOpened) || null;
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

function getAccess() {
  return load().access;
}

function setAccess(list) {
  const state = load();
  state.access = list;
  save(state);
}

// --- certificates and plain http ------------------------------------------

// The pins by origin (lib/certs.js).
function getPins() {
  return load().pins;
}

// `cert`: what lib/certs.js describe() read of the certificate trusted.
function setPin(origin, cert) {
  const state = load();
  state.pins[origin] = {
    sha256: cert.sha256, subject: cert.subject || '', issuer: cert.issuer || '', validFrom: cert.validFrom || '',
    validTo: cert.validTo || '', pinnedAt: new Date().toISOString(),
  };
  save(state);
  return state.pins[origin];
}

function dropPin(origin) {
  const state = load();
  if (!state.pins[origin]) return false;
  delete state.pins[origin];
  save(state);
  return true;
}

// Whether a token goes to `origin` over plain http:// without a question;
// `on` records (true) or forgets (false) the person's yes.
function plainAccepted(origin) {
  return (load().settings.plainAccepted || []).includes(origin);
}

function setPlainAccepted(origin, on) {
  const state = load();
  const list = (state.settings.plainAccepted || []).filter((o) => o !== origin);
  if (on) list.push(origin);
  state.settings.plainAccepted = list;
  save(state);
}

// --- offline copies -------------------------------------------------------

function originOf(url) {
  try {
    return new URL(url).origin;
  } catch {
    return String(url || '');
  }
}

// The copy of a remote workspace, if the shell knows one: { server, workspace, name, remoteUrl, remoteWs, remoteName }.
function findMirror(remoteUrl, remoteWs) {
  const origin = originOf(remoteUrl);
  return load().mirrors.find((m) => m.remoteUrl === origin && m.remoteWs === remoteWs) || null;
}

// The entry of a local server's workspace when that workspace is a copy.
function mirrorOf(serverId, workspaceId) {
  return load().mirrors.find((m) => m.server === serverId && m.workspace === workspaceId) || null;
}

function addMirror(entry) {
  const state = load();
  const clean = { ...entry, remoteUrl: originOf(entry.remoteUrl) };
  state.mirrors = state.mirrors.filter((m) => !(m.server === clean.server && m.workspace === clean.workspace));
  state.mirrors.push(clean);
  save(state);
  return clean;
}

// Replace everything known about one local server's copies with its own list.
function setServerMirrors(serverId, entries) {
  const state = load();
  state.mirrors = state.mirrors
    .filter((m) => m.server !== serverId)
    .concat(entries.map((e) => ({ ...e, server: serverId, remoteUrl: originOf(e.remoteUrl) })));
  save(state);
}

// Bytes on disk under a local server's data dir (SQLite files + uploads).
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
  init, load, get, addLocal, addRemote, setRemoteUrl, moveKind, currentOrigin, movedAway, markHttpsSeen, withScheme,
  parseServerUrl, rename, remove, setPort, setZoteroHost, launchHosts,
  markOpened, getLastOpened,
  getSettings, setSettings, getWindowBounds, setWindowBounds, getAccess, setAccess, dirSize,
  defaultDataRoot, dataRoot, localsUnderRoot, setDataRoot,
  getPins, setPin, dropPin, plainAccepted, setPlainAccepted,
  originOf, findMirror, mirrorOf, addMirror, setServerMirrors,
};
