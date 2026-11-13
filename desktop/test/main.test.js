// main.js under plain Node, Electron replaced by a stand-in and each local
// server by a fake answering the shell's API calls (the GAMMA_SHELL_TEST
// hooks reach the shell's internals): what a keeping reading records about
// the Zotero links (refreshKeeping -> registry.setZoteroHost, the servers
// startBackgroundHosts starts at launch), a resume made while a reading
// runs, one start per server however many ask, and the synced vaults: the
// panel's actions in the vocabulary, a round followed to its end, a work
// folder made from a directory and its tracking dialog opened in the window,
// a folder with pages through the bar's dialog, the shell's word on the
// routes that take a path, and the focus hint; and a PDF opened with Gamma,
// where its page is. `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('module');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { pages: shellPages, trustedShellSender } = require('../lib/shellSecurity');
const { zipOf } = require('./zipOf');

test('shell IPC accepts only its bundled page in the correct top-level view', () => {
  const bar = { webContents: { mainFrame: { url: shellPages.bar } } };
  const content = { webContents: { mainFrame: { url: shellPages.launcher + '?theme=dark&error=message#status' } } };
  const eventFor = (view) => ({ sender: view.webContents, senderFrame: view.webContents.mainFrame });
  assert.equal(trustedShellSender(eventFor(bar), bar, content), true);
  assert.equal(trustedShellSender(eventFor(content), bar, content), true);
  for (const url of ['file:///tmp/attacker.html', shellPages.launcher + '.html', 'https://gamma.invalid/', shellPages.bar]) {
    content.webContents.mainFrame.url = url;
    assert.equal(trustedShellSender(eventFor(content), bar, content), false, url);
  }
  assert.equal(trustedShellSender({ sender: bar.webContents, senderFrame: { url: shellPages.bar } }, bar, content), false, 'subframe');
  const other = { webContents: { mainFrame: { url: shellPages.bar } } };
  assert.equal(trustedShellSender(eventFor(other), bar, content), false, 'another window');
  assert.equal(trustedShellSender({}, bar, content), false, 'missing frame');
});

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-shell-'));
test.after(() => fs.rmSync(dir, { recursive: true, force: true }));

// The shell's timers of a second or more (the next reading, a notice going)
// are kept and run only when a test runs one, so a reading happens only
// when a test asks for it or when the shell starts one at once.
const realSetTimeout = global.setTimeout;
const realClearTimeout = global.clearTimeout;
const timers = []; // { ms, fn, cleared }, in the order they were set
global.setTimeout = (fn, ms, ...args) => {
  if (!(ms >= 1000)) return realSetTimeout(fn, ms, ...args);
  const timer = { ms, fn, cleared: false };
  timers.push(timer);
  return timer;
};
global.clearTimeout = (timer) => {
  if (timers.includes(timer)) timer.cleared = true;
  else realClearTimeout(timer);
};
// The next keeping reading's timer, as the last reading set it.
const keepingTimer = () => timers.filter((t) => !t.cleared && (t.ms === 15_000 || t.ms === 2_000)).at(-1);

// Electron: what main.js touches when it loads, and the sessions it reads
// the local servers through (`keeping-<server id>`).
const fakes = new Map(); // server id -> fake server
const noop = () => {};
const stub = (props = {}) => new Proxy(props, { get: (t, k) => (k in t ? t[k] : noop) });
const electron = {
  app: stub({
    whenReady: () => new Promise(noop), requestSingleInstanceLock: () => true, isPackaged: false,
    getPath: () => dir, getVersion: () => '0.0.0',
  }),
  session: {
    defaultSession: stub(),
    fromPartition: (name) => ({
      fetch: async (url, init = {}) => fakes.get(name.replace(/^keeping-/, '')).answer(new URL(url).pathname, init, new URL(url)),
    }),
  },
};
for (const name of ['BaseWindow', 'WebContentsView', 'Menu', 'Tray', 'clipboard', 'nativeImage', 'nativeTheme',
  'shell', 'ipcMain', 'net', 'dialog', 'systemPreferences']) {
  electron[name] = stub();
}
const resolveFilename = Module._resolveFilename;
Module._resolveFilename = function (request, ...rest) {
  return request === 'electron' ? 'electron' : resolveFilename.call(this, request, ...rest);
};
require.cache.electron = { id: 'electron', filename: 'electron', loaded: true, exports: electron };

process.env.GAMMA_SHELL_TEST = '1';
const registry = require('../lib/registry');
const sidecar = require('../lib/sidecar');
registry.init(dir);
const running = new Set(); // ids of the local servers that answer
sidecar.status = (id) => (running.has(id) ? { url: 'http://127.0.0.1:50999', port: 50999 } : null);
require('../main.js');
const shell = global.__gammaShell;

// A Zotero link as GET /api/zotero/links lists it, and one detached.
const synced = { last_sync: '2026-10-29T10:00:00Z', backlog: 0 };
const link = (ws, more = {}) => ({
  kind: 'zotero', workspace_id: ws, name: ws, library: 'users/1', library_name: 'My Library',
  mode: 'two-way', detached: false, status: synced, ...more,
});
const detached = (ws) => link(ws, { mode: 'off', detached: true, status: { ...synced, detached_mode: 'two-way' } });

// A local server holding `links` (Zotero links) and `vaults` (its synced
// vaults, `GET /api/folder-links`). `zotero` is how its link list answers:
// 'list', 404 (a server from before the Zotero links) or 'fail' (no
// answer); `broken`, its list of clones fails. The list is read as the
// links are when it is asked for, then `gate()` may hold the answer back.
// Every call is recorded (`calls`: path, query, method, headers, the JSON
// body, the form); `routes(path, init)` answers the vault routes a test sets
// up: a body (a Buffer reads as bytes), or `{ failWith: status, body? }`.
// `remote`, the address of a remote server the fake is instead.
function server(name, links = [], { zotero = 'list', running: on = true, broken = false, remote = '' } = {}) {
  const srv = remote ? registry.addRemote(name, remote) : registry.addLocal(name);
  const ok = (body) => ({ ok: true, status: 200, json: async () => body, arrayBuffer: async () => body });
  const fails = (code, body = { detail: `HTTP ${code}` }) => ({ ok: false, status: code, json: async () => body });
  const fake = {
    links, zotero, reads: 0, gate: async () => {}, vaults: [], calls: [], routes: () => null,
    workspaces: [{ id: 'mine', name: 'Mine', personal: true, role: 'owner', members: 1, access: 'private', mirror_of: '' }],
    async answer(p, init, url) {
      let body = null;
      try { body = typeof init.body === 'string' ? JSON.parse(init.body) : null; } catch {}
      const form = init.body instanceof FormData ? init.body : null;
      fake.calls.push({ path: p, query: url ? url.search : '', method: init.method || 'GET', headers: init.headers || {}, body, form });
      const routed = fake.routes(p, init);
      if (routed && routed.failWith) return fails(routed.failWith, routed.body);
      if (routed) return ok(routed);
      if (p === '/api/session') return ok({ user: fake.user || 'admin', default_workspace: 'mine', workspaces: fake.workspaces });
      if (p === '/api/sync/changes') return ok({ format: 51, changes: [] });
      if (p === '/api/mirrors') return broken ? fails(500) : ok({ mirrors: [] });
      if (p === '/api/folder-links') return ok({ links: fake.vaults });
      if (p === '/api/zotero/links') {
        fake.reads += 1;
        const now = fake.links.map((l) => ({ ...l }));
        await fake.gate();
        if (fake.zotero === 'fail') throw new Error('ECONNRESET');
        return fake.zotero === 404 ? fails(404) : ok({ links: now });
      }
      const m = p.match(/^\/api\/zotero\/links\/([^/]+)\/(relink|detach)$/);
      if (m && init.method === 'POST') {
        const ws = decodeURIComponent(m[1]);
        fake.links = fake.links.map((l) => (l.workspace_id !== ws ? l : m[2] === 'relink' ? link(ws) : detached(ws)));
        return ok({});
      }
      return fails(404);
    },
  };
  fakes.set(srv.id, fake);
  if (on && !remote) running.add(srv.id);
  return { srv, fake, recorded: () => Boolean(registry.get(srv.id).zoteroHost) };
}

function fresh() {
  fs.rmSync(path.join(dir, 'servers.json'), { force: true });
  fakes.clear();
  running.clear();
  shell.pretendShown(null, null);
  shell.asked.length = 0;
  shell.answers.length = 0;
}

// The window's content view on local server `local` (a fake of server()),
// its workspace `ws`: its session reaches that server at the sidecar's
// address and each remote of `remotes` (fakes) at its own. `loaded` records
// the addresses the shell loads into it.
function windowOn(local, remotes = [], ws = 'mine') {
  const at = (origin) => (origin === 'http://127.0.0.1:50999' ? local : remotes.find((r) => r.origin === origin).fake);
  const loaded = [];
  return {
    loaded,
    webContents: {
      isDestroyed: () => false,
      isLoading: () => false,
      getURL: () => `http://127.0.0.1:50999/?ws=${ws}`,
      loadURL: async (url) => { loaded.push(url); },
      executeJavaScript: async () => 'already:admin',
      on: noop, removeListener: noop, focus: noop,
      session: { fetch: async (url, init = {}) => at(new URL(url).origin).answer(new URL(url).pathname, init, new URL(url)) },
    },
  };
}

// The app's window, for a flow that brings it forward.
const appWindow = { isDestroyed: () => false, isMinimized: () => false, show: noop, focus: noop, restore: noop };

// Turns of the event loop until `cond` holds; fails rather than hangs.
async function until(cond, what) {
  for (let i = 0; i < 200 && !cond(); i++) await new Promise((r) => setImmediate(r));
  assert.ok(cond(), what);
}

test('a reading records the servers that hold a Zotero link that is not detached, and only from a list that came', async () => {
  fresh();
  const live = server('Live', [link('w1')]);
  const pull = server('Pull', [link('w1', { mode: 'pull' })]);
  const reconnect = server('Reconnect', [link('w1', { status: { ...synced, last_error: 'Zotero refused the key', error_kind: 'auth' } })]);
  const mixed = server('Mixed', [detached('w1'), link('w2')]);
  const det = server('Detached', [detached('w1')]);
  const off = server('Off', [link('w1', { mode: 'off' })]);
  const gone = server('Gone', []);
  const old = server('Old', [], { zotero: 404 });
  const silent = server('Silent', [], { zotero: 'fail' });
  const broken = server('Broken', [link('w1')], { broken: true });
  const stopped = server('Stopped', [], { running: false });
  const remote = registry.addRemote('NAS', 'http://nas.lan:9001');
  for (const s of [det, off, gone, old, silent, broken, stopped]) registry.setZoteroHost(s.srv.id, true);
  registry.setZoteroHost(remote.id, true);

  await shell.refreshKeeping();

  for (const s of [live, pull, reconnect, mixed]) assert.equal(s.recorded(), true, `${s.srv.name}: holds a link that is not detached`);
  for (const s of [det, off, gone]) assert.equal(s.recorded(), false, `${s.srv.name}: holds none`);
  for (const s of [old, silent, broken]) assert.equal(s.recorded(), true, `${s.srv.name}: no list came, the record stays`);
  assert.equal(stopped.recorded(), true, 'a server that is not running keeps its record');
  assert.equal(stopped.fake.reads, 0, 'and is not read');
  assert.equal(registry.get(remote.id).zoteroHost, undefined);
  assert.deepEqual(registry.launchHosts().map((s) => s.name).sort(),
    ['Broken', 'Live', 'Mixed', 'Old', 'Pull', 'Reconnect', 'Silent', 'Stopped']);

  // the links go, or are detached: the next reading clears the record
  live.fake.links = [];
  mixed.fake.links = [detached('w1'), detached('w2')];
  await shell.refreshKeeping();
  assert.equal(live.recorded(), false);
  assert.equal(mixed.recorded(), false);
  assert.equal(pull.recorded(), true);
});

test('from the panel, a resume records the server at once and a pause at the reading that follows', async () => {
  fresh();
  const z = server('Zotero', [link('w1')]);
  await shell.refreshKeeping();
  assert.equal(z.recorded(), true);

  await shell.keepingAction('zotero', z.srv.id, 'w1', 'pause');
  await until(() => !z.recorded(), 'the reading after the pause clears the record');
  assert.deepEqual(shell.keeping().items.map((i) => i.state), ['detached']);

  await shell.keepingAction('zotero', z.srv.id, 'w1', 'resume');
  assert.equal(z.recorded(), true, 'recorded before any reading');
  await until(() => shell.keeping().items[0].state === 'ok', 'the reading after the resume shows the link syncing');
  assert.equal(z.recorded(), true);
});

test('a reading that read a link before the user resumed it neither undoes the record nor holds back the next reading', async () => {
  fresh();
  const z = server('Zotero', [detached('w1')]);
  await shell.refreshKeeping();
  assert.equal(z.recorded(), false);

  // a reading reads the link while it is detached; its answer comes once
  // the resume has landed
  let asked;
  let release;
  const askedFor = new Promise((r) => { asked = r; });
  const answered = new Promise((r) => { release = r; });
  z.fake.gate = () => {
    z.fake.gate = async () => {};
    asked();
    return answered;
  };
  const stale = shell.refreshKeeping();
  await askedFor;

  await shell.keepingAction('zotero', z.srv.id, 'w1', 'resume');
  assert.equal(z.recorded(), true, 'the resume records the server at once');
  release();
  await stale;
  assert.equal(z.recorded(), true, 'the reading from before the resume leaves the record alone');
  await until(() => z.fake.reads === 3, 'another reading follows at once, not at the next timer');
  await until(() => shell.keeping().items[0].state === 'ok', 'and the panel shows the link resumed');
  assert.equal(z.recorded(), true);
});

test('a timer that comes due while a reading runs leaves that reading be', async () => {
  fresh();
  const z = server('Zotero', [link('w1')]);
  await shell.refreshKeeping();
  assert.equal(z.recorded(), true);

  // the link is detached in Gamma's Settings; the next reading is still
  // reading it when its timer comes due
  z.fake.links = [detached('w1')];
  let asked;
  let release;
  const askedFor = new Promise((r) => { asked = r; });
  const answered = new Promise((r) => { release = r; });
  z.fake.gate = () => {
    z.fake.gate = async () => {};
    asked();
    return answered;
  };
  const timer = keepingTimer();
  assert.equal(timer.ms, 15_000);
  const reading = shell.refreshKeeping();
  await askedFor;
  timer.fn();
  release();
  await reading;
  assert.equal(z.recorded(), false, 'the reading counts');
  await new Promise((r) => setImmediate(r));
  assert.equal(z.fake.reads, 2, 'and no other one follows it');
  assert.deepEqual(shell.keeping().items.map((i) => i.state), ['detached']);
});

test('a server is started once however many ask while it starts: the launch, then the user opening it', async () => {
  fresh();
  const z = server('Zotero', [link('w1')], { running: false });
  server('Idle', [], { running: false });
  registry.addRemote('NAS', 'http://nas.lan:9001');
  registry.setZoteroHost(z.srv.id, true);

  const realStart = sidecar.start;
  const starts = [];
  sidecar.start = (ws) => new Promise((resolve, reject) => starts.push({ id: ws.id, resolve, reject }));
  try {
    shell.startBackgroundHosts();
    await until(() => starts.length === 1, 'the launch starts the Zotero host');
    assert.equal(starts[0].id, z.srv.id, 'and no other server');
    const opened = shell.startLocal(registry.get(z.srv.id));
    const again = shell.startLocal(z.srv);
    await new Promise((r) => setImmediate(r));
    assert.equal(starts.length, 1, 'opening it waits for the start under way');

    const entry = { url: 'http://127.0.0.1:50123', port: 50123 };
    starts[0].resolve(entry);
    assert.equal(await opened, entry);
    assert.equal(await again, entry);
    assert.equal(registry.get(z.srv.id).port, 50123, 'the port it got is remembered');

    // once that start has ended, or failed, the next call starts again
    const later = shell.startLocal(z.srv);
    await until(() => starts.length === 2, 'a call after the start ended starts again');
    starts[1].reject(new Error('could not bind'));
    await assert.rejects(later, /could not bind/);
    const retry = shell.startLocal(z.srv);
    await until(() => starts.length === 3, 'a call after a failed start starts again');
    starts[2].resolve(entry);
    assert.equal(await retry, entry);
  } finally {
    sidecar.start = realStart;
  }
});

// A synced vault of a folder as GET /api/folder-links lists it.
const vaultLink = (more = {}) => ({
  id: 'v1', kind: 'folder', workspace_id: 'mine', folder_id: 'f1', page_id: '', path: 'C:\\Papers', dest: 'C:\\Papers', notes: true,
  pull: true, push: 'off', remote_url: '', token_id: '', status: { last_sync: '2026-11-01T10:00:00Z', folder_path: ['Papers'] }, ...more,
});

test('a vault\'s panel actions: push is a word, Pull now and Push now narrow the round, a force and going on are asked first, removing asks with the counts', async () => {
  fresh();
  const s = server('Vaults');
  s.fake.vaults = [vaultLink()];
  const picked = fs.mkdtempSync(path.join(dir, 'moved-'));
  s.fake.routes = (p, init) => {
    if (p === '/api/folder-links/v1/removal') return { pages: 4, in_place: 0, bytes: 0 };
    if (p === '/api/folder-links/v1/sync') return { ...vaultLink(), status: { counts: { added: 1, sent: 2, unchanged: 5 } } };
    if (p === '/api/folder-links/v1/relink') return vaultLink({ path: picked, dest: picked });
    if (p === '/api/folder-links/v1' && init.method !== 'GET') return init.method === 'DELETE' ? { ok: true } : vaultLink();
    return null;
  };
  const sent = (p, method) => s.fake.calls.filter((c) => c.path === p && c.method === method);
  const rounds = () => sent('/api/folder-links/v1/sync', 'POST');
  await shell.refreshKeeping();
  const [item] = shell.keeping().items;
  assert.deepEqual([item.kind, item.scope, item.push, item.pull], ['vault', 'folder', 'off', true]);

  await shell.keepingAction('vault', s.srv.id, 'v1', 'push', 'pdfs');
  await shell.keepingAction('vault', s.srv.id, 'v1', 'pull', false);
  assert.deepEqual(sent('/api/folder-links/v1', 'PATCH').map((c) => c.body), [{ push: 'pdfs' }, { pull: false }]);
  assert.equal(shell.notice(), 'Papers no longer pulls: its files stay as they are.');
  await assert.rejects(shell.keepingAction('vault', s.srv.id, 'v1', 'push', true), /Push is off, pdfs or everything/, 'a flag is not a word');

  await shell.keepingAction('vault', s.srv.id, 'v1', 'pull-now');
  await shell.keepingAction('vault', s.srv.id, 'v1', 'push-now');
  await shell.keepingAction('vault', s.srv.id, 'v1', 'sync');
  assert.deepEqual(rounds().map((c) => c.query), ['?direction=pull', '?direction=push', '?direction=both'], 'no request held open for a round');
  assert.equal(shell.notice(), 'Papers is up to date: 1 added, 2 pushed.');
  for (const c of rounds()) assert.equal(c.headers['X-Gamma-Workspace'], 'mine', 'in the vault\'s workspace');

  // a force and going on are asked first; under the harness the question takes Cancel
  await shell.keepingAction('vault', s.srv.id, 'v1', 'force-pull');
  await shell.keepingAction('vault', s.srv.id, 'v1', 'go-on');
  assert.equal(rounds().length, 3, 'nothing ran');
  assert.equal(sent('/api/folder-links/v1/removal', 'GET').length, 1, 'the force counted the pages first');
  await shell.forceVault(s.srv.id, 'v1', 'push', true);
  await shell.goOnVault(s.srv.id, 'v1', true);
  assert.deepEqual(rounds().slice(3).map((c) => c.query), ['?force=push', '?confirm=1']);

  // Locate… names the directory with the shell's word
  process.env.GAMMA_SHELL_PICK_DIR = picked;
  try {
    await shell.keepingAction('vault', s.srv.id, 'v1', 'locate');
  } finally {
    delete process.env.GAMMA_SHELL_PICK_DIR;
  }
  const [relink] = sent('/api/folder-links/v1/relink', 'POST');
  assert.deepEqual(relink.body, { path: picked });
  assert.equal(relink.headers['X-Gamma-Shell'], shell.shellSecret);
  assert.equal(shell.notice(), `Papers is synced with ${picked} from now on.`);

  // removing asks with the counts and keeps the files (the question's first answer)
  await shell.keepingAction('vault', s.srv.id, 'v1', 'remove');
  await shell.removeVault(s.srv.id, 'v1', 'trash');
  assert.deepEqual(sent('/api/folder-links/v1', 'DELETE').map((c) => c.query), ['?files=keep', '?files=trash']);
  assert.equal(sent('/api/folder-links/v1/removal', 'GET').length, 2);
  for (const c of [...rounds(), ...sent('/api/folder-links/v1', 'PATCH'), ...sent('/api/folder-links/v1', 'DELETE')]) {
    assert.equal(c.headers['X-Gamma-Shell'], undefined, 'the shell\'s word goes only with a path');
  }
});

test('a vault synced from the panel is followed to the end of its round, then the bar says how it went', async () => {
  fresh();
  const s = server('Folders');
  const kept = vaultLink({ id: 'fl1', status: { last_sync: '2026-10-29T10:00:00Z', last_error: '', counts: {}, kept: [], folder_path: ['Kept lab'] } });
  s.fake.vaults = [kept];
  // a round 15 minutes in when it is asked for (a long first fill): followed all the same, until its running comes down
  const startedAt = Date.now() / 1000 - 900;
  const reads = [{ running: true, started_at: startedAt, progress: {} }, { running: true, started_at: startedAt, progress: {} },
    { running: false, last_sync: '2026-11-06T10:00:00Z', last_error: '', counts: { added: 2, unchanged: 5 }, kept: [] }];
  s.fake.routes = (p, init) => {
    if (p === '/api/folder-links/fl1/sync') return { ...kept, status: { ...kept.status, running: true, started_at: startedAt } };
    if (p === '/api/folder-links/fl1' && !init.method) return { ...kept, status: reads.shift() };
    return null;
  };
  const rounds = () => s.fake.calls.filter((c) => c.path === '/api/folder-links/fl1/sync').map((c) => `${c.method} ${c.query}`);
  // the next read of the link, a second on (the shell's timers of a second or more wait for the test)
  const next = () => timers.find((t) => !t.cleared && t.ms === 1000);
  const step = async () => {
    await until(() => next(), 'the link is read again a second on');
    const timer = next();
    timer.cleared = true;
    timer.fn();
  };

  const said = shell.notice();
  const synced = shell.syncVault(s.srv.id, 'fl1');
  await step();
  await step();
  assert.equal(shell.notice(), said, 'nothing said while the round still runs');
  await step();
  const after = await synced;
  assert.deepEqual(rounds(), ['POST ?direction=both'], 'the round is asked for, no request held open for it');
  assert.equal(after.status.last_sync, '2026-11-06T10:00:00Z', 'the answer is the link as the round left it');
  assert.equal(shell.notice(), 'Kept lab is up to date: 2 added.');
  assert.equal(reads.length, 0, 'read until its running came down, and no more');

  // forced: the force asked for; a round already over in the answer is not read again
  s.fake.routes = (p) => {
    if (p === '/api/folder-links/fl1/sync') return { ...kept, status: { ...kept.status, running: false, last_error: 'the folder is gone' } };
    if (p === '/api/folder-links/fl1') assert.fail('not read again');
    return null;
  };
  await shell.forceVault(s.srv.id, 'fl1', 'pull', true);
  assert.deepEqual(rounds(), ['POST ?direction=both', 'POST ?force=pull']);
  assert.equal(shell.notice(), 'Kept lab: the folder is gone');
});

test('a vault of a workspace its maker left is listed as no access, and its maker still pauses, counts and removes it; another account\'s opens nothing', async () => {
  fresh();
  const s = server('Left');
  s.fake.vaults = [vaultLink({ workspace_id: 'gone' }), vaultLink({ id: 'v2', path: '', dest: '' })];
  s.fake.routes = (p, init) => {
    if (p === '/api/folder-links/v1/removal') return { pages: 2, in_place: 0, bytes: 0 };
    if (p === '/api/folder-links/v1' && init.method !== 'GET') return init.method === 'DELETE' ? { ok: true } : vaultLink({ workspace_id: 'gone' });
    return null;
  };
  const sent = (p, method) => s.fake.calls.filter((c) => c.path === p && c.method === method);
  await shell.refreshKeeping();
  const [away, seen] = shell.keeping().items;
  assert.deepEqual([away.away, away.state, away.text], [true, 'error', 'No access to its workspace'], 'the session lists no workspace "gone"');
  assert.deepEqual([seen.away, seen.seen], [false, true]);

  await shell.keepingAction('vault', s.srv.id, 'v1', 'pause');
  assert.deepEqual(sent('/api/folder-links/v1', 'PATCH').map((c) => c.body), [{ paused: true }]);
  await shell.keepingAction('vault', s.srv.id, 'v1', 'remove');
  assert.equal(sent('/api/folder-links/v1/removal', 'GET').length, 1, 'counted first');
  assert.deepEqual(sent('/api/folder-links/v1', 'DELETE').map((c) => c.query), ['?files=keep']);

  await assert.rejects(shell.keepingAction('vault', s.srv.id, 'v2', 'open'), /another account's/);
});

test('a vault of another server keeps pulling and pushes PDFs at most: its switches refuse the rest, as its dialog does', async () => {
  fresh();
  const s = server('Host');
  s.fake.vaults = [vaultLink({ workspace_id: 'nas-ws', remote_url: 'http://nas.lan:9001', token_id: 't1' })];
  s.fake.routes = (p, init) => (p === '/api/folder-links/v1' && init.method === 'PATCH' ? vaultLink() : null);
  const patches = () => s.fake.calls.filter((c) => c.path === '/api/folder-links/v1' && c.method === 'PATCH').map((c) => c.body);
  await shell.refreshKeeping();
  await assert.rejects(shell.keepingAction('vault', s.srv.id, 'v1', 'pull', false), /^Error: A vault of another server pulls, and pushes PDFs at most, for now\.$/);
  await assert.rejects(shell.keepingAction('vault', s.srv.id, 'v1', 'push', 'everything'), /pushes PDFs at most/);
  assert.deepEqual(patches(), [], 'nothing was changed');
  await shell.keepingAction('vault', s.srv.id, 'v1', 'pull', true);
  assert.deepEqual(patches(), [{ pull: true }], 'turned on, as the rule wants');
});

test('a work folder choosing its files is shown so, its Choose files… opens its tracking dialog in the window, and its library switch is the server\'s', async () => {
  fresh();
  const s = server('Work');
  s.fake.vaults = [vaultLink({ push: 'everything', choosing: true, library: false, status: { paused_at: '2026-11-10T10:00:00Z' } })];
  s.fake.routes = (p, init) => (p === '/api/folder-links/v1' && init.method === 'PATCH' ? vaultLink({ push: 'everything', library: true }) : null);
  const view = windowOn(s.fake);
  shell.pretendShown(s.srv, view, appWindow);
  try {
    await shell.refreshKeeping();
    const [item] = shell.keeping().items;
    assert.deepEqual([item.work, item.choosing, item.state, item.text], [true, true, 'pending', 'Choosing files']);
    await shell.keepingAction('vault', s.srv.id, 'v1', 'choose');
    const at = new URL(view.loaded.at(-1));
    assert.deepEqual([at.searchParams.get('ws'), at.searchParams.get('folder'), at.searchParams.get('track')], ['mine', 'f1', 'v1']);
    await shell.keepingAction('vault', s.srv.id, 'v1', 'library', true);
    const patches = s.fake.calls.filter((c) => c.path === '/api/folder-links/v1' && c.method === 'PATCH');
    assert.deepEqual(patches.map((c) => c.body), [{ library: true }]);
    assert.equal(patches[0].headers['X-Gamma-Workspace'], 'mine');
    assert.match(shell.notice(), /from now on are added to the library too/);
  } finally {
    shell.pretendShown(null, null);
  }
});

test('a work folder\'s agent from the panel: the agents found are read, turning one on is asked with the Sends waiting and carries the shell\'s word, off asks nothing', async () => {
  fresh();
  const s = server('Agents');
  const codexOn = { name: 'codex', label: 'Codex', ready: true, problem: '', running: null };
  // the link names its maker by account id, never by username
  s.fake.vaults = [vaultLink({ push: 'everything', mine: true, created_by: 'u_7Xk3q', agent: null })];
  const found = [{ name: 'codex', label: 'Codex', found: true, version: '0.167.0', ready: true, problem: '' },
    { name: 'claude', label: 'Claude Code', found: false, version: '', ready: false, problem: 'Claude Code is not on this computer.' }];
  s.fake.routes = (p, init) => {
    if (p === '/api/local-agents') return { agents: found };
    if (p === '/api/local-agents/waiting') return { waiting: 2 };  // the server counts by its own rule
    if (p === '/api/folder-links/v1' && init.method === 'PATCH') {
      const { agent } = JSON.parse(init.body);
      s.fake.vaults = [vaultLink({ push: 'everything', mine: true, created_by: 'u_7Xk3q', agent: agent ? codexOn : null })];
      return { ...s.fake.vaults[0], ...(agent ? { queued: 2 } : {}) };
    }
    return null;
  };
  const patches = () => s.fake.calls.filter((c) => c.path === '/api/folder-links/v1' && c.method === 'PATCH');
  await shell.refreshKeeping();
  let [item] = shell.keeping().items;
  assert.deepEqual([item.work, item.mine, item.agent], [true, true, '']);
  assert.deepEqual(item.agents.map((a) => [a.name, a.ready]), [['codex', true], ['claude', false]]);
  const read = s.fake.calls.find((c) => c.path === '/api/local-agents');
  assert.equal(read.query, '', 'the panel\'s reading takes what the server has, never a fresh check');

  // asked first, with the maker's Sends that wait; under the harness the question takes Cancel
  await shell.keepingAction('vault', s.srv.id, 'v1', 'agent', 'codex');
  assert.deepEqual(patches(), [], 'nothing turned on');
  assert.equal(shell.asked.at(-1), 'Let Codex work in C:\\Papers? Codex will answer the Sends you make on this folder: it edits files in this directory and runs commands in its sandbox, without network. 2 waiting Sends will be handed to it.');
  const [counted] = s.fake.calls.filter((c) => c.path === '/api/local-agents/waiting');
  assert.equal(new URLSearchParams(counted.query).get('link'), 'v1');
  assert.ok(!s.fake.calls.some((c) => c.path === '/api/feedback/requests'), 'the shell counts nothing itself');

  shell.answers.push(0);
  await shell.keepingAction('vault', s.srv.id, 'v1', 'agent', 'codex');
  const [on] = patches();
  assert.deepEqual(on.body, { agent: 'codex' });
  assert.equal(on.headers['X-Gamma-Shell'], shell.shellSecret, 'turning an agent on is the computer\'s word');
  assert.equal(on.headers['X-Gamma-Workspace'], 'mine');
  assert.equal(shell.notice(), 'Codex now answers the Sends on Papers; 2 waiting were handed to it.');
  await shell.refreshKeeping();
  [item] = shell.keeping().items;
  assert.deepEqual([item.agent, item.agentLabel, item.agentReady], ['codex', 'Codex', true]);

  // the same agent again changes nothing; a name that is none is refused; off asks nothing and carries no word
  const asks = shell.asked.length;
  assert.equal(await shell.setAgent(s.srv.id, 'v1', 'codex'), false);
  await assert.rejects(shell.keepingAction('vault', s.srv.id, 'v1', 'agent', 'gpt'), /codex or claude/);
  await shell.keepingAction('vault', s.srv.id, 'v1', 'agent', '');
  const off = patches().at(-1);
  assert.deepEqual(off.body, { agent: '' });
  assert.equal(off.headers['X-Gamma-Shell'], undefined);
  assert.equal(shell.asked.length, asks, 'nothing asked');
  assert.equal(shell.notice(), 'Codex no longer answers the Sends on Papers: they wait for an agent you run yourself.');

  // the work folder's view asks the server to look again
  await shell.keepingAction('vault', s.srv.id, 'v1', 'check-agents');
  assert.ok(s.fake.calls.some((c) => c.path === '/api/local-agents' && c.query === '?fresh=1'));

  // a folder that is no work folder has no agent; a server that runs none (404) offers none
  s.fake.vaults = [vaultLink({ mine: true })];
  s.fake.routes = () => null;
  await shell.refreshKeeping();
  [item] = shell.keeping().items;
  assert.equal(item.agents, null);
  await assert.rejects(shell.keepingAction('vault', s.srv.id, 'v1', 'agent', 'codex'), /Only a work folder/);
});

test('what a work folder\'s agent may run, from the panel: Ask is asked first with the risk and carries the shell\'s word, Nothing more asks nothing', async () => {
  fresh();
  const s = server('Asking');
  const agentOf = (runs) => ({ name: 'claude', label: 'Claude Code', ready: true, problem: '', running: null, runs });
  s.fake.vaults = [vaultLink({ push: 'everything', mine: true, agent: agentOf('never') })];
  s.fake.routes = (p, init) => {
    if (p === '/api/local-agents') return { agents: [{ name: 'claude', label: 'Claude Code', found: true, ready: true, problem: '' }] };
    if (p === '/api/folder-links/v1' && init.method === 'PATCH') {
      s.fake.vaults = [vaultLink({ push: 'everything', mine: true, agent: agentOf(JSON.parse(init.body).agent_runs) })];
      return s.fake.vaults[0];
    }
    return null;
  };
  const patches = () => s.fake.calls.filter((c) => c.path === '/api/folder-links/v1' && c.method === 'PATCH');
  await shell.refreshKeeping();
  let [item] = shell.keeping().items;
  assert.deepEqual([item.agent, item.agentRuns], ['claude', 'never']);

  // asked first, naming what it may ask for and the risk; under the harness the question takes Cancel
  await shell.keepingAction('vault', s.srv.id, 'v1', 'agent-runs', 'ask');
  assert.deepEqual(patches(), [], 'nothing changed');
  assert.equal(shell.asked.at(-1), 'Let Claude Code ask to run more in C:\\Papers? In the chat of a file of this folder, Claude Code can then ask you to run a command, such as your build; nothing of it runs until you allow it there. A command you allow runs as you, with your files and the network.');

  shell.answers.push(0);
  await shell.keepingAction('vault', s.srv.id, 'v1', 'agent-runs', 'ask');
  const [on] = patches();
  assert.deepEqual(on.body, { agent_runs: 'ask' });
  assert.equal(on.headers['X-Gamma-Shell'], shell.shellSecret, 'letting an agent ask is the computer\'s word');
  assert.equal(on.headers['X-Gamma-Workspace'], 'mine');
  assert.equal(shell.notice(), 'Claude Code may now ask you in the chat to run more on Papers.');
  await shell.refreshKeeping();
  [item] = shell.keeping().items;
  assert.equal(item.agentRuns, 'ask');

  // the same again changes nothing; another word is refused; Nothing more asks nothing and carries no word
  const asks = shell.asked.length;
  assert.equal(await shell.setAgentRuns(s.srv.id, 'v1', 'ask'), false);
  await assert.rejects(shell.keepingAction('vault', s.srv.id, 'v1', 'agent-runs', 'always'), /never or ask/);
  await shell.keepingAction('vault', s.srv.id, 'v1', 'agent-runs', 'never');
  const off = patches().at(-1);
  assert.deepEqual(off.body, { agent_runs: 'never' });
  assert.equal(off.headers['X-Gamma-Shell'], undefined);
  assert.equal(shell.asked.length, asks, 'nothing asked');
  assert.equal(shell.notice(), 'Claude Code no longer asks to run more on Papers: it keeps to its confinement.');

  // a work folder with no agent on has nothing to let it run
  s.fake.vaults = [vaultLink({ push: 'everything', mine: true, agent: null })];
  await shell.refreshKeeping();
  await assert.rejects(shell.keepingAction('vault', s.srv.id, 'v1', 'agent-runs', 'ask'), /Turn an agent on first/);
  assert.equal(patches().length, 2);
});

// What `POST /api/folder-links/preview` finds in the directory `picked`:
// `pages` in the folder, `notes` in the directory, `refusal` why it may not be.
const previewOf = (picked, { notes = 2, pages = 0, refusal = '', marker = null } = {}) => ({
  path: picked, notes, pdfs: 1, pictures: 0, untracked: 0, empty: false, marker, refusal, pages, will_write: ['.gamma/'],
  gammaignore: { exists: false, ours: false },
});

// The folders of the workspace the window shows, as `GET /api/sync/folders` lists them.
const FOLDERS = { folders: [{ id: 'f1', path: ['Kept lab'] }, { id: 'f2', path: ['Empty lab'] }] };

test('a directory added becomes a work folder made to choose its files: nothing asked in the bar, the window goes to its tracking dialog, and the routes that take a path carry the shell\'s word', async () => {
  fresh();
  const host = server('Host');
  const picked = fs.mkdtempSync(path.join(dir, 'Reading-'));
  let found = previewOf(picked);
  host.fake.routes = (p, init) => {
    if (p === '/api/folder-links/preview') return found;
    if (p === '/api/sync/folders') return FOLDERS;
    if (p === '/api/folder-links' && init.method === 'POST') {
      const body = JSON.parse(init.body);
      return vaultLink({ id: 'w1', folder_id: body.folder || 'made', path: picked, dest: picked, push: 'everything', choosing: true });
    }
    return null;
  };
  const made = () => host.fake.calls.filter((c) => c.path === '/api/folder-links' && c.method === 'POST');
  const view = windowOn(host.fake);
  shell.pretendShown(host.srv, view, appWindow);
  process.env.GAMMA_SHELL_PICK_DIR = picked;
  try {
    const link = await shell.addDirectoryVault();
    assert.equal(link.id, 'w1');
    assert.equal(shell.vaultAsk(), null, 'its tracking dialog says what it tracks, not the bar');
    const [create] = made();
    assert.deepEqual(create.body, { path: picked, new_folder: true, pull: true, push: 'everything', choose: true, adopt: false });
    assert.equal(create.headers['X-Gamma-Workspace'], 'mine', 'the account\'s own workspace');
    const [preview] = host.fake.calls.filter((c) => c.path === '/api/folder-links/preview');
    for (const c of [create, preview]) assert.equal(c.headers['X-Gamma-Shell'], shell.shellSecret, `${c.path} carries it`);
    assert.deepEqual(preview.body, { path: picked, folder: '', kind: 'folder' });
    const at = new URL(view.loaded.at(-1));
    assert.deepEqual([at.searchParams.get('ws'), at.searchParams.get('folder'), at.searchParams.get('track')], ['mine', 'made', 'w1']);

    // a folder with no pages synced with a directory of notes and PDFs: a work folder too, of that folder
    await shell.addFolderVault('mine', 'f2');
    assert.equal(shell.vaultAsk(), null);
    assert.deepEqual(made().at(-1).body, { path: picked, folder: 'f2', pull: true, push: 'everything', choose: true, adopt: false });
    assert.equal(new URL(view.loaded.at(-1)).searchParams.get('folder'), 'f2');

    // a refusal the preview tells is said in the bar, and nothing is made
    found = previewOf(picked, { refusal: `${picked} holds the marker of the synced vault C:\\Other: was it copied?` });
    assert.equal(await shell.addDirectoryVault(), null);
    assert.equal(made().length, 2, 'nothing posted');
    assert.equal(shell.notice(), found.refusal);
  } finally {
    delete process.env.GAMMA_SHELL_PICK_DIR;
    shell.pretendShown(null, null);
  }
});

test('a work folder made with no server open: on the host, started with a word of its own that its routes carry', async () => {
  fresh();
  const host = server('Host', [], { running: false });
  const picked = fs.mkdtempSync(path.join(dir, 'Started-'));
  const infos = [];
  const realStart = sidecar.start;
  sidecar.start = async (ws, _settings, info) => {
    infos.push(info);
    running.add(ws.id);
    return { url: 'http://127.0.0.1:50999', port: 50999 };
  };
  host.fake.routes = (p, init) => {
    if (p === '/api/folder-links/preview') return previewOf(picked);
    if (p === '/api/folder-links' && init.method === 'POST') return { failWith: 400, body: { detail: 'A workspace has at most 10 synced vaults of folders.' } };
    return null;
  };
  process.env.GAMMA_SHELL_PICK_DIR = picked;
  try {
    await assert.rejects(shell.addDirectoryVault(), /at most 10 synced vaults/, 'the server\'s refusal, and no window opened for it');
    assert.equal(infos.length, 1, 'the host was started');
    assert.ok(infos[0].shellSecret && infos[0].shellSecret.length >= 32, 'with a word of its own');
    assert.equal(infos[0].shellSecret, shell.shellSecret);
    const [create] = host.fake.calls.filter((c) => c.path === '/api/folder-links' && c.method === 'POST');
    assert.equal(create.headers['X-Gamma-Shell'], infos[0].shellSecret);
    assert.equal(create.headers['X-Gamma-Workspace'], 'mine', 'in the account\'s own workspace on the host');
  } finally {
    sidecar.start = realStart;
    delete process.env.GAMMA_SHELL_PICK_DIR;
  }
});

test('a folder with pages still asks in the bar: the vault dialog stays shown until it is answered, one ask runs at a time, and the shell\'s word goes to a running local server only', async () => {
  fresh();
  const host = server('Host');
  const picked = fs.mkdtempSync(path.join(dir, 'Once-'));
  host.fake.routes = (p, init) => {
    if (p === '/api/folder-links/preview') return previewOf(picked, { notes: 0, pages: 3 });
    if (p === '/api/sync/folders') return FOLDERS;
    if (p === '/api/folder-links' && init.method === 'POST') return vaultLink({ path: picked, dest: picked });
    return null;
  };
  const picks = () => host.fake.calls.filter((c) => c.path === '/api/folder-links/preview').length;
  shell.pretendShown(host.srv, windowOn(host.fake));
  process.env.GAMMA_SHELL_PICK_DIR = picked;
  try {
    const once = shell.oneAsk(shell.addFolderVault);
    const adding = once('mine', 'f1');
    await until(() => shell.vaultAsk(), 'the dialog is up');
    const ask = shell.vaultAsk();
    const { id } = ask;
    assert.deepEqual([ask.newFolder, ask.pull, ask.push, ask.folder, ask.server, ask.path],
      [false, true, 'off', 'Kept lab', 'Host', picked]);
    assert.equal(ask.resolve, undefined, 'the bar gets the dialog, never its promise');
    assert.match(ask.sentences['true:off'].text, /^Pull 3 pages into .+; nothing there is pushed\./);
    assert.equal(ask.sentences['false:off'].ok, false);
    assert.equal(shell.answerVault(id, { pull: false, push: 'off' }), false, 'an answer its sentence refuses');
    assert.equal(shell.answerVault(id + 1, { pull: true, push: 'off' }), false, 'another dialog\'s');
    // a press in the page shrinks the bar to its strip, but not while the dialog waits
    shell.setBarExpanded(false);
    assert.equal(shell.barExpanded(), true, 'the dialog is not hidden while it waits');
    // a second ask while the first runs (a double click, a page asking in a loop) is dropped
    assert.equal(await once('mine', 'f1'), null);
    assert.equal(picks(), 1, 'no second directory was looked at');
    assert.equal(shell.vaultAsk().id, id, 'and the dialog the user is answering stays');
    assert.equal(shell.answerVault(id, null), true);
    assert.equal(await adding, null);
    assert.equal(shell.barExpanded(), false, 'answered, the bar goes back to its strip');
    // once it settled, the next ask runs, and an answer makes the vault as it says
    const again = once('mine', 'f1');
    await until(() => shell.vaultAsk(), 'a later ask shows its dialog');
    shell.answerVault(shell.vaultAsk().id, { pull: true, push: 'pdfs' });
    assert.equal((await again).dest, picked);
    assert.equal(picks(), 2);
    const [create] = host.fake.calls.filter((c) => c.path === '/api/folder-links' && c.method === 'POST');
    assert.deepEqual(create.body, { folder: 'f1', path: picked, notes: true, pull: true, push: 'pdfs', adopt: false });
    // the whole library is never a work folder: its dialog starts pulling only, and pushing everything is refused
    const whole = once('mine', 'root');
    await until(() => shell.vaultAsk(), 'the whole library\'s dialog');
    assert.deepEqual([shell.vaultAsk().whole, shell.vaultAsk().push], [true, 'off']);
    assert.deepEqual(shell.vaultAsk().sentences['true:everything'], { ok: false, text: 'A vault of the whole library cannot push everything: make one of a folder.' });
    shell.answerVault(shell.vaultAsk().id, null);
    assert.equal(await whole, null);
  } finally {
    delete process.env.GAMMA_SHELL_PICK_DIR;
    shell.pretendShown(null, null);
  }
  assert.deepEqual(shell.shellWord('http://127.0.0.1:50999'), { 'X-Gamma-Shell': shell.shellSecret });
  registry.addRemote('NAS', 'http://nas.lan:9001');
  assert.throws(() => shell.shellWord('http://nas.lan:9001'), /only to a server on this computer/, 'never to a remote');
  running.clear();
  assert.throws(() => shell.shellWord('http://127.0.0.1:50999'), /only to a server on this computer/, 'nor to a port no server of this app holds now');
});

// The host's computer links (GET /api/computer-links) and the NAS's tokens,
// for the tests of this computer shown on a remote: `host` and `nas` fakes
// of server(). The NAS mints `t<n>`; the host keeps the links it is handed
// (POST, its answer `replaced` as `replace` says, or `fail`), forgets one on
// DELETE and answers its token.
function computerRoutes(host, nas, { replace = null, fail = null } = {}) {
  const state = { links: [], minted: [], fail };
  const routes = host.fake.routes;
  host.fake.routes = (p, init) => {
    if (p === '/api/computer-links' && init.method === 'POST') {
      if (state.fail) return state.fail;
      const body = JSON.parse(init.body);
      const link = { id: `c${state.links.length + 1}`, remote_url: body.remote_url, workspace_id: body.workspace, account: 'admin', username: 'admin',
        status: { state: 'connected', error: '', at: '2026-11-13T10:00:00Z' }, created_at: '2026-11-13T10:00:00Z', token_id: body.token_id };
      state.links = [...state.links.filter((c) => !(c.remote_url === link.remote_url && c.workspace_id === link.workspace_id)), link];
      const { token_id: _t, ...listed } = link;
      return { link: listed, replaced: replace };
    }
    if (p === '/api/computer-links') return { links: state.links.map(({ token_id: _t, ...c }) => c) };
    const gone = p.match(/^\/api\/computer-links\/([^/]+)$/);
    if (gone && init.method === 'DELETE') {
      const link = state.links.find((c) => c.id === decodeURIComponent(gone[1]));
      state.links = state.links.filter((c) => c !== link);
      return { ok: true, token_id: link.token_id };
    }
    return routes(p, init);
  };
  nas.fake.routes = (p, init) => {
    if (p === '/api/integrations/tokens' && init.method === 'POST') {
      const minted = { id: `t${state.minted.length + 1}`, token: `gamma_${state.minted.length + 1}`, ...JSON.parse(init.body) };
      state.minted.push(minted);
      return minted;
    }
    if (p.startsWith('/api/integrations/tokens/') && init.method === 'DELETE') return { ok: true };
    if (p === '/api/sync/folders') return FOLDERS;
    return null;
  };
  return state;
}

// The tokens revoked on fake `nas`, each `id@workspace`.
const revoked = (nas) => nas.fake.calls.filter((c) => c.method === 'DELETE' && c.path.startsWith('/api/integrations/tokens/'))
  .map((c) => `${c.path.split('/').pop()}@${c.headers['X-Gamma-Workspace']}`);

test('a vault of another server shows this computer there: a computer token minted with the window\'s session, handed to the host with the shell\'s word, once per server, workspace and account; a failure revokes it and the vault stays', async () => {
  fresh();
  const host = server('Host');
  const nas = server('NAS', [], { remote: 'http://nas.lan:9001' });
  const picked = fs.mkdtempSync(path.join(dir, 'Far-'));
  host.fake.routes = (p, init) => {
    if (p === '/api/folder-links/preview') return previewOf(picked, { notes: 0, pages: 3 });
    if (p === '/api/folder-links' && init.method === 'POST') {
      return vaultLink({ path: picked, dest: picked, workspace_id: 'team', remote_url: 'http://nas.lan:9001', token_id: JSON.parse(init.body).token_id });
    }
    return null;
  };
  const shown = computerRoutes(host, nas, { replace: { token_id: 'old' } });
  const realStart = sidecar.start;
  sidecar.start = async () => ({ url: 'http://127.0.0.1:50999', port: 50999 });
  shell.pretendShown(nas.srv, windowOn(host.fake, [{ origin: 'http://nas.lan:9001', fake: nas.fake }], 'team'));
  process.env.GAMMA_SHELL_PICK_DIR = picked;
  const add = async () => {
    const adding = shell.addFolderVault('team', 'f1');
    await until(() => shell.vaultAsk(), 'the dialog is up');
    shell.answerVault(shell.vaultAsk().id, { pull: true, push: 'off' });
    return adding;
  };
  const handed = () => host.fake.calls.filter((c) => c.path === '/api/computer-links' && c.method === 'POST');
  try {
    await add();
    const [vaultToken, computer] = shown.minted;
    assert.deepEqual([vaultToken.scope, computer.scope], ['read', 'computer'], 'a token of its own, never the vault\'s');
    assert.equal(computer.name, `Gamma desktop: ${shell.computerName}`);
    assert.equal(computer.expires_in_days, 365);
    const mint = nas.fake.calls.filter((c) => c.path === '/api/integrations/tokens' && c.method === 'POST')[1];
    assert.equal(mint.headers['X-Gamma-Workspace'], 'team', 'for the vault\'s workspace');
    const [hand] = handed();
    assert.deepEqual(hand.body, { remote_url: 'http://nas.lan:9001', workspace: 'team', token: computer.token, token_id: computer.id });
    assert.equal(hand.headers['X-Gamma-Shell'], shell.shellSecret, 'the shell\'s word');
    assert.deepEqual(revoked(nas), ['old@team'], 'the token the host replaced, and no other');
    assert.equal(shell.notice(), `Kept lab is synced with ${picked}`);
    for (const c of nas.fake.calls) assert.equal(c.headers['X-Gamma-Shell'], undefined, 'the word never goes to the remote');
    assert.ok(!nas.fake.calls.some((c) => c.body && JSON.stringify(c.body).includes(picked)), 'nor a path');

    // another vault of that server and workspace: the host shows this computer there for the account already
    await add();
    assert.equal(shown.minted.length, 3, 'only the vault\'s token');
    assert.equal(handed().length, 1);
    // signed in there as another account: the link stays the first one's, nothing minted for it
    nas.fake.user = 'bob';
    await add();
    assert.equal(shown.minted.length, 4, 'only the vault\'s token');
    assert.equal(handed().length, 1);
    assert.equal(shell.notice(), `Kept lab is synced with ${picked}. ${shell.computerName} is shown on NAS to admin, not to the account signed in there now: sign in there as admin, or Stop showing first.`);
    nas.fake.user = '';

    // one the server refused is handed a new token; a hand-over that fails revokes it, the vault made all the same
    shown.links = shown.links.map((c) => ({ ...c, status: { state: 'refused', error: '', at: '' } }));
    shown.fail = { failWith: 502, body: { detail: 'Cannot reach http://nas.lan:9001' } };
    const made = await add();
    assert.equal(made.dest, picked);
    const minted = shown.minted.at(-1);
    assert.equal(minted.scope, 'computer');
    assert.deepEqual(revoked(nas), ['old@team', `${minted.id}@team`], 'the token the host did not take is revoked');
    assert.equal(shell.notice(), `Kept lab is synced with ${picked}. NAS cannot show this computer: Cannot reach http://nas.lan:9001`);

    // a server older than this app knows no such scope
    shown.links = [];
    shown.fail = null;
    const routes = nas.fake.routes;
    nas.fake.routes = (p, init) => (p === '/api/integrations/tokens' && init.method === 'POST' && JSON.parse(init.body).scope === 'computer'
      ? { failWith: 422, body: { detail: [{ msg: 'string does not match pattern' }] } } : routes(p, init));
    await add();
    assert.equal(shell.notice(), `Kept lab is synced with ${picked}. NAS is older than this app: it cannot show this computer.`);
    assert.equal(handed().length, 2, 'nothing handed to the host');
  } finally {
    sidecar.start = realStart;
    delete process.env.GAMMA_SHELL_PICK_DIR;
    shell.pretendShown(null, null);
  }
});

test('from the panel, Show this computer there and Stop showing on a vault of another server; the last vault of that server and workspace removed stops showing it', async () => {
  fresh();
  const host = server('Host');
  const nas = server('NAS', [], { remote: 'http://nas.lan:9001' });
  const far = (id, ws = 'team') => vaultLink({ id, workspace_id: ws, remote_url: 'http://nas.lan:9001', token_id: `vt-${id}` });
  host.fake.vaults = [far('v1'), far('v2'), far('v3', 'lab')];
  host.fake.routes = (p, init) => {
    const m = p.match(/^\/api\/folder-links\/([^/]+)$/);
    if (m && init.method === 'DELETE') {
      host.fake.vaults = host.fake.vaults.filter((v) => v.id !== m[1]);
      return { ok: true };
    }
    return null;
  };
  const shown = computerRoutes(host, nas);
  // the panel mints and revokes with the window's session, signed into the remote
  electron.session.defaultSession.fetch = async (url, init = {}) => nas.fake.answer(new URL(url).pathname, init, new URL(url));
  const linkCalls = (method) => host.fake.calls.filter((c) => c.path.startsWith('/api/computer-links') && c.method === method);
  try {
    await shell.refreshKeeping();
    let [item] = shell.keeping().items;
    assert.deepEqual([item.computer.text, item.computer.button], ['Not shown on NAS', 'Show this computer there']);

    await shell.keepingAction('vault', host.srv.id, 'v1', 'show-computer');
    const [minted] = shown.minted;
    assert.deepEqual([minted.scope, minted.name], ['computer', `Gamma desktop: ${shell.computerName}`]);
    const [hand] = linkCalls('POST');
    assert.deepEqual(hand.body, { remote_url: 'http://nas.lan:9001', workspace: 'team', token: minted.token, token_id: minted.id });
    assert.equal(hand.headers['X-Gamma-Shell'], shell.shellSecret);
    assert.equal(shell.notice(), `${shell.computerName} is shown on NAS: only your account there sees it, with the vaults it keeps and the assistants here.`);
    await shell.refreshKeeping();
    [item] = shell.keeping().items;
    assert.deepEqual([item.computer.text, item.computer.button], [`Shown on NAS as ${shell.computerName} · connected`, 'Stop showing']);
    assert.equal(shell.keeping().items.find((i) => i.id === 'v3').computer.text, 'Not shown on NAS', 'another workspace of that server is its own');

    await shell.keepingAction('vault', host.srv.id, 'v1', 'hide-computer');
    const [stop] = linkCalls('DELETE');
    assert.equal(stop.path, '/api/computer-links/c1');
    assert.equal(stop.headers['X-Gamma-Shell'], shell.shellSecret);
    assert.deepEqual(revoked(nas), [`${minted.id}@team`], 'its token revoked on the remote');
    assert.equal(shell.notice(), `${shell.computerName} is no longer shown on NAS; its token there is revoked.`);
    assert.deepEqual(host.fake.vaults.map((v) => v.id), ['v1', 'v2', 'v3'], 'the vaults stay');

    // removing a vault while another of that server and workspace stays keeps showing it; the last one stops
    await shell.keepingAction('vault', host.srv.id, 'v2', 'show-computer');
    await shell.removeVault(host.srv.id, 'v1', 'keep');
    assert.equal(linkCalls('DELETE').length, 1, 'v2 keeps it shown');
    assert.deepEqual(revoked(nas).slice(1), ['vt-v1@team'], 'the vault\'s own token');
    await shell.removeVault(host.srv.id, 'v2', 'keep');
    assert.equal(linkCalls('DELETE').at(-1).path, '/api/computer-links/c1');
    assert.deepEqual(revoked(nas).slice(2), ['vt-v2@team', `${shown.minted[1].id}@team`]);
    assert.deepEqual(shown.links, []);

    // a token that server did not revoke (another account signed in there) is said, never claimed revoked
    host.fake.vaults = [far('v4')];
    await shell.refreshKeeping();
    await shell.keepingAction('vault', host.srv.id, 'v4', 'show-computer');
    const routes = nas.fake.routes;
    nas.fake.routes = (p, init) => (p.startsWith('/api/integrations/tokens/') && init.method === 'DELETE' ? { ok: true, revoked: false } : routes(p, init));
    await shell.keepingAction('vault', host.srv.id, 'v4', 'hide-computer');
    assert.equal(shell.notice(), `${shell.computerName} is no longer shown on NAS, but its token there could not be revoked: remove “Gamma desktop: ${shell.computerName}” in NAS's Settings → Integrations, signed in as the account that made it.`);
    nas.fake.routes = routes;
  } finally {
    delete electron.session.defaultSession.fetch;
  }
});

test('a window getting the focus asks each running local server to look at its vaults, at most every ten seconds', async () => {
  fresh();
  const a = server('A');
  const b = server('B');
  const off = server('Off', [], { running: false });
  registry.addRemote('NAS', 'http://nas.lan:9001');
  const hints = (s) => s.fake.calls.filter((c) => c.path === '/api/folder-links/hint' && c.method === 'POST').length;
  shell.hintServers();
  await until(() => hints(a) === 1 && hints(b) === 1, 'both running servers hinted');
  assert.equal(off.fake.calls.length, 0, 'a stopped server is not started for it');
  shell.hintServers();
  for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r));
  assert.deepEqual([hints(a), hints(b)], [1, 1], 'a second focus soon after asks nothing');
});

// A vault of one file as GET /api/folder-links lists it.
const fileLink = (id, file, more = {}) => vaultLink({ id, kind: 'file', folder_id: '', page_id: `page-${id}`, path: file, dest: file,
  pull: false, push: 'pdfs', ...more });

test('a third vault of one file in one directory asks first: sync the folder (its own dialog, no picker) or push this file only', async () => {
  fresh();
  const s = server('Here');
  const papers = fs.mkdtempSync(path.join(dir, 'Papers-'));
  const third = path.join(papers, 'c.pdf');
  s.fake.vaults = [fileLink('a', path.join(papers, 'a.pdf')), fileLink('elsewhere', path.join(dir, 'x.pdf')),
    fileLink('theirs', path.join(papers, 'b.pdf'), { workspace_id: 'team' })];
  s.fake.routes = (p, init) => {
    if (p === '/api/blocks/p3') return { id: 'p3', content: 'Paper C', properties: {} };
    if (p === '/api/folder-links/preview') {
      return { path: papers, notes: 0, pdfs: 3, pictures: 0, untracked: 0, empty: false, marker: null, refusal: '', pages: 0, will_write: ['.gamma/'] };
    }
    if (p === '/api/folder-links' && init.method === 'POST') return JSON.parse(init.body).kind === 'file' ? fileLink('c', third) : vaultLink({ path: papers, dest: papers });
    return null;
  };
  const made = () => s.fake.calls.filter((c) => c.path === '/api/folder-links' && c.method === 'POST').map((c) => c.body);
  // (the harness records a question with its detail)
  const question = `3 files in ${papers} are synced one by one. Sync the folder instead? A synced vault of the folder takes these files’ pages in, with their notes and highlights, and keeps every PDF and note in it in step, the ones added later too.`;
  shell.pretendShown(s.srv, windowOn(s.fake));
  process.env.GAMMA_SHELL_PICK_FILE = third;
  try {
    // a second file of the directory in this workspace: no question
    await shell.addFileVault('mine', 'p3');
    assert.deepEqual(shell.asked, []);
    assert.deepEqual(made(), [{ kind: 'file', page: 'p3', path: third }]);

    // the third: asked, and pushed from the file only when that is the answer (the harness's own)
    s.fake.vaults.push(fileLink('b', path.join(papers, 'B.pdf')));
    await shell.addFileVault('mine', 'p3');
    assert.deepEqual(shell.asked, [question]);
    assert.equal(made().length, 2);

    shell.answers.push(2); // Cancel
    assert.equal(await shell.addFileVault('mine', 'p3'), null);
    assert.equal(made().length, 2, 'cancelled: nothing made');

    // Sync the folder…: that directory becomes a work folder, with no picker asked; its tracking dialog takes the
    // file vaults in
    shell.answers.push(0);
    assert.equal((await shell.addFileVault('mine', 'p3')).dest, papers);
    assert.equal(shell.vaultAsk(), null);
    assert.deepEqual(made().at(-1), { path: papers, new_folder: true, pull: true, push: 'everything', choose: true, adopt: false });
    assert.equal(made().length, 3);

    // a workspace others see: a directory's vault would not land in it, so no folder is offered
    s.fake.workspaces = [{ ...s.fake.workspaces[0], members: 3 }];
    shell.asked.length = 0;
    await shell.addFileVault('mine', 'p3');
    assert.deepEqual(shell.asked, []);
    assert.deepEqual(made().at(-1), { kind: 'file', page: 'p3', path: third });
  } finally {
    delete process.env.GAMMA_SHELL_PICK_FILE;
    shell.pretendShown(null, null);
  }
});

// An opened PDF's page on local server `here`, as the routes answer it there:
// its state (`mine`: the window's account opened the file), its row, its
// export (`zip`), the digest of what a carry takes and the pages it takes
// (`digest`), and the page let go once it is there (`letGo`; `{ failWith:
// 409 }`: written on since the digest).
function opened(here, { zip, missing = false, mine = true, digest = { digest: 'dg1', pages: ['p1'] }, letGo = { ok: true } } = {}) {
  here.fake.routes = (p, init) => {
    if (p === '/api/folder-links/page/p1') return { vault: null, reader: { path: 'C:\\Downloads\\attention.pdf', missing, mine } };
    if (p === '/api/blocks/p1' && (init.method || 'GET') === 'GET') {
      return { id: 'p1', content: 'Attention', properties: { doc: { format: 'pdf', name: 'attention.pdf', hash: 'd1', ext: '.pdf', at: 'path' } } };
    }
    if (p === '/api/pages/p1/export') return zip;
    if (p === '/api/folder-links/opened/p1/carried') return (init.method || 'GET') === 'GET' ? digest : letGo;
    return null;
  };
}

const CARRIED = '/api/folder-links/opened/p1/carried';

// A server the page is carried to, as its routes answer: the merge (`merged`,
// or `{ failWith }`), the page taken out of *Opened files*, `has`, the pages
// it holds already by path, and `trashed`, those it holds in Recently
// deleted (a 404 that names the page's entry there, as routers/blocks.py
// answers one).
function carriedTo(target, { merged = { ok: true, pages_added: 1 }, has = [], trashed = [] } = {}) {
  target.fake.routes = (p, init) => {
    if (p === '/api/import-data' && init.method === 'POST') return merged;
    if (p === '/api/blocks/p1' && init.method === 'PUT') return { id: 'p1', properties: { parent_page: null } };
    if (has.includes(p)) return { id: 'there' };
    if (trashed.includes(p)) return { failWith: 404, body: { detail: 'page is in Recently deleted', trashed: { id: 'p1', title: 'Attention' } } };
    return null;
  };
}

const exportZip = (more = {}) => zipOf({ 'manifest.json': '{"format": "gamma-backup-1"}', 'pages.db': 'db', ...more });

// carryPage's questions as the harness records them, each with its detail.
const LET_GO = 'Once it is there, the page here is moved to Recently deleted here; the file on this computer stays as it is.';
const whichServer = (ask) => `${ask} Its notes, highlights, comments and chat go with it, and its PDF is stored there. ${LET_GO}`;
const whichWorkspace = (ask) => `${ask} ${LET_GO}`;

test('an opened PDF carried to another server: asked where, exported from here with its PDF, merged there, out of Opened files, then moved to Recently deleted here by the digest read before the export', async () => {
  fresh();
  const here = server('Here');
  const nas = server('NAS', [], { remote: 'http://nas.lan:9001' });
  const lab = server('Lab');
  nas.fake.workspaces = [
    { id: 'team', name: 'Team', role: 'editor', members: 4, access: 'private', mirror_of: '' },
    { id: 'clone', name: 'Clone', role: 'owner', members: 1, access: 'private', mirror_of: 'Elsewhere' },
    { id: 'nas-mine', name: 'Mine', personal: true, role: 'owner', members: 1, access: 'private', mirror_of: '' },
  ];
  const zip = exportZip({ 'uploads/d1.pdf': '%PDF-1.7' });
  opened(here, { zip, digest: { digest: 'dg1', pages: ['p1', 'p2'] } }); // p2: a page inside it
  // what NAS had been sent when the page here was let go
  const routes = here.fake.routes;
  let atLetGo = null;
  here.fake.routes = (p, init) => {
    if (p === CARRIED && init.method === 'POST') atLetGo = nas.fake.calls.map((c) => `${c.method} ${c.path}`);
    return routes(p, init);
  };
  carriedTo(nas);
  shell.pretendShown(here.srv, windowOn(here.fake, [{ origin: 'http://nas.lan:9001', fake: nas.fake }]));
  const sent = (s, p, method) => s.fake.calls.filter((c) => c.path === p && c.method === method);
  try {
    // the harness's answer is Cancel: nothing is read or written, no other server reached
    assert.equal(await shell.carryPage('mine', 'p1'), null);
    assert.deepEqual(shell.asked, [whichServer('Add “Attention” to the library of which server?')]);
    assert.equal(sent(here, '/api/pages/p1/export', 'GET').length, 0);
    for (const s of [nas, lab]) assert.equal(s.fake.calls.length, 0, `${s.srv.name} not asked before the answer`);

    shell.answers.push(0, 1); // NAS, then its second place: Team (its own comes first)
    const done = await shell.carryPage('mine', 'p1');
    assert.deepEqual(done, { server: nas.srv.id, serverName: 'NAS', workspace: 'team', workspaceName: 'Team', page: 'p1', title: 'Attention' });
    assert.equal(shell.asked.at(-1), whichWorkspace('Add “Attention” to the library of which workspace of NAS?'));

    const [exported] = sent(here, '/api/pages/p1/export', 'GET');
    assert.equal(exported.query, '?mode=gamma&sub_pages=1', 'a Gamma export, with the pages inside it');
    assert.equal(exported.headers['X-Gamma-Workspace'], 'mine');
    const [merged] = sent(nas, '/api/import-data', 'POST');
    assert.equal(merged.query, '?mode=merge&ws=team');
    assert.equal(merged.headers['X-Gamma-Workspace'], 'team');
    assert.deepEqual(Buffer.from(await merged.form.get('file').arrayBuffer()), zip, 'the export as it came');
    assert.deepEqual(sent(nas, '/api/blocks/p1', 'PUT').map((c) => c.body), [{ properties: { parent_page: null } }]);
    for (const p of ['/api/blocks/p1', '/api/blocks/by-doc/d1', '/api/blocks/p2']) assert.equal(sent(nas, p, 'GET').length, 1, `${p} looked at first`);
    // the digest read before the export, and handed back only after all that:
    // the page here goes to Recently deleted, and nothing is purged
    const steps = here.fake.calls.filter((c) => c.path === CARRIED || c.path === '/api/pages/p1/export').map((c) => `${c.method} ${c.path}`);
    assert.deepEqual(steps, [`GET ${CARRIED}`, 'GET /api/pages/p1/export', `POST ${CARRIED}`]);
    const [letGo] = sent(here, CARRIED, 'POST');
    assert.deepEqual([letGo.body, letGo.headers['X-Gamma-Workspace']], [{ digest: 'dg1' }, 'mine']);
    assert.ok(atLetGo.includes('POST /api/import-data') && atLetGo.includes('PUT /api/blocks/p1'), 'let go once it is there');
    assert.equal(here.fake.calls.filter((c) => c.method === 'DELETE').length, 0, 'nothing deleted');
    for (const s of [here, nas]) for (const c of s.fake.calls) assert.equal(c.headers['X-Gamma-Shell'], undefined, 'no path is named');
    assert.equal(lab.fake.calls.length, 0, 'the server not chosen is not asked');

    // to a local server: read in its own session; its one workspace is not asked about
    shell.answers.push(1);
    shell.asked.length = 0;
    carriedTo(lab);
    const local = await shell.carryPage('mine', 'p1');
    assert.deepEqual([local.server, local.workspace], [lab.srv.id, 'mine']);
    assert.equal(sent(lab, '/api/import-data', 'POST')[0].query, '?mode=merge&ws=mine');
    assert.deepEqual(shell.asked, [whichServer('Add “Attention” to the library of which server?')]);
  } finally {
    shell.pretendShown(null, null);
  }
});

test('an opened PDF that cannot be carried is kept here, and the error says why', async () => {
  fresh();
  const here = server('Here');
  const nas = server('NAS', [], { remote: 'http://nas.lan:9001' });
  shell.pretendShown(here.srv, windowOn(here.fake, [{ origin: 'http://nas.lan:9001', fake: nas.fake }]));
  const letGo = () => here.fake.calls.filter((c) => (c.path === CARRIED && c.method === 'POST') || c.method === 'DELETE').length;
  const merges = () => nas.fake.calls.filter((c) => c.path === '/api/import-data').length;
  const carry = (answers) => {
    shell.answers.length = 0;
    shell.answers.push(...answers);
    return shell.carryPage('mine', 'p1');
  };
  try {
    opened(here, { zip: exportZip({ 'uploads/d1.pdf': '%PDF' }), missing: true });
    await assert.rejects(carry([0]), /not there: locate it first/);
    assert.deepEqual(shell.asked, [], 'refused before any question');

    // another editor of the workspace opened the file: it is on that
    // account's computer, so the page goes nowhere from this one (as
    // POST /opened/<id>/add refuses another workspace)
    opened(here, { zip: exportZip({ 'uploads/d1.pdf': '%PDF' }), mine: false });
    await assert.rejects(carry([0]), /Only the account that opened this file may take it to another server/);
    assert.deepEqual(shell.asked, [], 'refused before any question');
    assert.equal(nas.fake.calls.length, 0, 'NAS not reached');
    assert.equal(here.fake.calls.filter((c) => c.path === '/api/pages/p1/export').length, 0, 'not exported');
    // a server that does not say whose the file is: refused the same
    here.fake.routes = (p) => (p === '/api/folder-links/page/p1' ? { vault: null, reader: { path: 'C:\\Downloads\\attention.pdf', missing: false } } : null);
    await assert.rejects(carry([0]), /Only the account that opened this file/);

    // one other server: it is still asked about before it is reached, and a
    // Cancel (the harness's answer) reaches nothing there; its one workspace is not asked about
    opened(here, { zip: exportZip({ 'uploads/d1.pdf': '%PDF' }) });
    carriedTo(nas);
    assert.equal(await carry([]), null);
    assert.deepEqual(shell.asked, [whichServer('Add “Attention” to the library of NAS?')]);
    assert.equal(nas.fake.calls.length, 0, 'NAS not reached before the answer');

    // the export came without the PDF (the file changed meanwhile)
    shell.asked.length = 0;
    opened(here, { zip: exportZip() });
    await assert.rejects(carry([0]), /The PDF did not come with the page/);
    assert.equal(merges(), 0, 'nothing went there');
    assert.deepEqual(shell.asked, [whichServer('Add “Attention” to the library of NAS?')]);

    // a page inside it whose PDF NAS has a page of: refused before the merge
    opened(here, { zip: exportZip({ 'uploads/d1.pdf': '%PDF', 'uploads/d2.pdf': '%PDF' }) });
    carriedTo(nas, { has: ['/api/blocks/by-doc/d2'] });
    await assert.rejects(carry([0]), /A page inside “Attention” has a PDF that Mine on NAS has a page of already/);
    assert.equal(merges(), 0, 'nothing went there');
    // a page inside it that NAS holds already, or holds in Recently deleted,
    // where the merge would put the copy in its place and delete it for good
    // with what was written on it there: refused before the export
    const exports = () => here.fake.calls.filter((c) => c.path === '/api/pages/p1/export').length;
    for (const held of [{ has: ['/api/blocks/p2'] }, { trashed: ['/api/blocks/p2'] }]) {
      const before = exports();
      opened(here, { zip: exportZip({ 'uploads/d1.pdf': '%PDF' }), digest: { digest: 'dg1', pages: ['p1', 'p2'] } });
      carriedTo(nas, held);
      await assert.rejects(carry([0]), held.has
        ? /A page inside “Attention” is a page of Mine on NAS already: take it out of “Attention” first\./
        : /A page inside “Attention” is a page of Mine on NAS already, in Recently deleted: take it out of “Attention” first\./);
      assert.equal(exports(), before, 'not exported');
    }
    assert.equal(merges(), 0, 'nothing went there');
    // a page inside it the merge skipped all the same: the page here is kept
    opened(here, { zip: exportZip({ 'uploads/d1.pdf': '%PDF' }) });
    carriedTo(nas, { merged: { ok: true, pages_added: 1, pages_skipped: 1 } });
    await assert.rejects(carry([0]), /a page inside it did not: it has them already\. The page here is kept/);
    assert.equal(merges(), 1);
    nas.fake.calls.length = 0;

    // the other server has a page of the PDF already, or this very page
    here.fake.calls.length = 0;
    opened(here, { zip: exportZip({ 'uploads/d1.pdf': '%PDF' }) });
    carriedTo(nas, { has: ['/api/blocks/by-doc/d1'] });
    await assert.rejects(carry([0]), /Mine on NAS has a page of this PDF already/);
    carriedTo(nas, { has: ['/api/blocks/p1'] });
    await assert.rejects(carry([0]), /Mine on NAS has this page already/);
    // or this very page in Recently deleted (carried before and kept here
    // too, then deleted there): the merge would put the copy in its place
    // and delete that one for good, with what was written on it there
    carriedTo(nas, { trashed: ['/api/blocks/p1'] });
    await assert.rejects(carry([0]), /Mine on NAS has this page in Recently deleted: restore it there and open it\./);
    assert.equal(here.fake.calls.filter((c) => c.path === '/api/pages/p1/export').length, 0, 'not exported');

    // the merge refused, or added nothing
    carriedTo(nas, { merged: { failWith: 507 } });
    await assert.rejects(carry([0]), /HTTP 507/);
    carriedTo(nas, { merged: { ok: true, pages_added: 0 } });
    await assert.rejects(carry([0]), /has this page already/);
    assert.equal(merges(), 2);

    // signed out there
    nas.fake.routes = (p) => (p === '/api/session' ? { user: null } : null);
    await assert.rejects(carry([0]), /Sign in to NAS first/);

    // the digest refused (the server says whose the file is): nothing exported, nothing went there
    here.fake.calls.length = 0;
    nas.fake.calls.length = 0;
    opened(here, { zip: exportZip({ 'uploads/d1.pdf': '%PDF' }), digest: { failWith: 400 } });
    carriedTo(nas);
    await assert.rejects(carry([0]), /HTTP 400/);
    assert.equal(here.fake.calls.filter((c) => c.path === '/api/pages/p1/export').length, 0, 'not exported');
    assert.equal(merges(), 0, 'nothing went there');

    assert.equal(letGo(), 0, 'the page here was never let go');

    // written on here since the digest: the server keeps it (409), the copy there stays, and it is in both places
    opened(here, { zip: exportZip({ 'uploads/d1.pdf': '%PDF' }), letGo: { failWith: 409 } });
    await assert.rejects(carry([0]), /“Attention” is in Mine on NAS now, and stays here too: it changed here while it was carried, so nothing here was deleted\. It is in both places now\./);
    assert.equal(merges(), 1, 'the copy there stays');
    assert.equal(here.fake.calls.filter((c) => c.method === 'DELETE').length, 0, 'nothing purged');
    // or the server could not let it go
    opened(here, { zip: exportZip({ 'uploads/d1.pdf': '%PDF' }), letGo: { failWith: 500 } });
    await assert.rejects(carry([0]), /is in Mine on NAS now, but the page here could not be moved to Recently deleted: HTTP 500\. It is in both places now: delete the page here to keep only the copy there\./);

    // nowhere to carry it: said, and how to add one
    fresh();
    const alone = server('Alone');
    opened(alone, { zip: exportZip({ 'uploads/d1.pdf': '%PDF' }) });
    shell.pretendShown(alone.srv, windowOn(alone.fake));
    assert.equal(await shell.carryPage('mine', 'p1'), null);
    assert.deepEqual(shell.asked, ['This app knows no other Gamma server to add “Attention” to. Add one under Server → All Servers… (New local server or Add remote server…), then add the page to its library from here again.']);
  } finally {
    shell.pretendShown(null, null);
  }
});

test('an opened PDF carried to another server is let go here whatever the window opens while it goes, so it is never left in both places for that', async () => {
  fresh();
  const here = server('Here');
  const nas = server('NAS', [], { remote: 'http://nas.lan:9001' });
  const other = server('Other');
  // The window's view on Here. Once it opens another server on this
  // computer, its session holds that one's cookie (a cookie does not tell
  // ports apart), and Here no longer knows it (`signedOut`: 401).
  let signedOut = false;
  const view = windowOn(here.fake, [{ origin: 'http://nas.lan:9001', fake: nas.fake }]);
  const reach = view.webContents.session.fetch;
  view.webContents.session.fetch = async (url, init) => (signedOut && new URL(url).origin === 'http://127.0.0.1:50999'
    ? { ok: false, status: 401, json: async () => ({ detail: 'Not signed in' }) } : reach(url, init));
  // carried to NAS, the window opening `meanwhile` while the merge runs there
  const carry = (meanwhile) => {
    carriedTo(nas);
    const merge = nas.fake.routes;
    nas.fake.routes = (p, init) => {
      if (p === '/api/import-data' && init.method === 'POST') meanwhile();
      return merge(p, init);
    };
    signedOut = false;
    shell.pretendShown(here.srv, view);
    shell.answers.length = 0;
    shell.answers.push(0);
    return shell.carryPage('mine', 'p1');
  };
  const toOther = () => {
    shell.pretendShown(other.srv, windowOn(other.fake));
    signedOut = true;
  };
  const letGo = () => here.fake.calls.filter((c) => c.path === CARRIED && c.method === 'POST');
  try {
    opened(here, { zip: exportZip({ 'uploads/d1.pdf': '%PDF' }) });
    // the launcher: the window's session is still Here's, and lets it go
    assert.equal((await carry(() => shell.pretendShown(null, null))).page, 'p1');
    assert.deepEqual(letGo().map((c) => [c.body, c.headers['X-Gamma-Workspace']]), [[{ digest: 'dg1' }, 'mine']]);

    // another server on this computer: Here's own session lets it go
    assert.equal((await carry(toOther)).page, 'p1');
    assert.deepEqual(letGo().map((c) => c.body), [{ digest: 'dg1' }, { digest: 'dg1' }]);

    // refused there too (that session's account did not open the file): the
    // page is kept here, and the message says what keeps only the copy there
    opened(here, { zip: exportZip({ 'uploads/d1.pdf': '%PDF' }), letGo: { failWith: 400 } });
    await assert.rejects(carry(toOther), /“Attention” is in Mine on NAS now, but the page here could not be moved to Recently deleted: HTTP 400\. It is in both places now: delete the page here to keep only the copy there\./);
    assert.equal(here.fake.calls.filter((c) => c.method === 'DELETE').length, 0, 'nothing deleted');
  } finally {
    shell.pretendShown(null, null);
  }
});

test('a PDF opened with Gamma: bytes the workspace shown carries open their page there, on a remote or in a clone, asked by the file\'s hash alone; anything else is read on this computer', async () => {
  fresh();
  const pdf = path.join(dir, 'attention.pdf');
  fs.writeFileSync(pdf, '%PDF-1.7 attention');
  const docId = require('crypto').createHash('sha256').update(fs.readFileSync(pdf)).digest('hex').slice(0, 24);
  const here = server('Here');
  const nas = server('NAS', [], { remote: 'http://nas.lan:9001' });
  here.fake.workspaces.push({ id: 'lab', name: 'Lab', role: 'owner', members: 1, access: 'private', mirror_of: 'Lab on NAS' });
  nas.fake.workspaces = [{ id: 'team', name: 'Team', role: 'editor', members: 4, access: 'private', mirror_of: '' }];
  const carried = { [`/api/blocks/by-doc/${docId}`]: { id: 'annotated', content: 'Attention' } };
  nas.fake.routes = (p) => carried[p] || null;
  here.fake.routes = (p, init) => (p === '/api/folder-links/open' && init.method === 'POST'
    ? { workspace: 'mine', page: 'read-here', created: false, title: 'attention' } : carried[p] || null);
  const lookups = (s) => s.fake.calls.filter((c) => c.path.startsWith('/api/blocks/by-doc/'));
  const opens = () => here.fake.calls.filter((c) => c.path === '/api/folder-links/open');
  const onNas = () => windowOn(here.fake, [{ origin: 'http://nas.lan:9001', fake: nas.fake }], 'team');
  const realStart = sidecar.start;
  sidecar.start = async () => ({ url: 'http://127.0.0.1:50999', port: 50999 });
  try {
    // on the NAS: the page there that holds these bytes, its highlights and notes with it
    let view = onNas();
    shell.pretendShown(nas.srv, view, appWindow);
    assert.deepEqual(await shell.readPdf(pdf), { workspace: 'team', page: 'annotated' });
    const [lookup] = lookups(nas);
    assert.deepEqual([lookup.path, lookup.method, lookup.headers['X-Gamma-Workspace']], [`/api/blocks/by-doc/${docId}`, 'GET', 'team']);
    assert.deepEqual(view.loaded, ['http://nas.lan:9001/?ws=team&page=annotated']);
    assert.ok(nas.fake.calls.every((c) => c.method === 'GET' && !c.form), 'nothing sent there but the hash');
    assert.equal(here.fake.calls.length, 0, 'this computer\'s server is not asked');
    assert.equal(shell.current().id, nas.srv.id, 'the window stays on the NAS');

    // in a clone on this computer: its page there
    view = windowOn(here.fake, [], 'lab');
    shell.pretendShown(here.srv, view, appWindow);
    assert.deepEqual(await shell.readPdf(pdf), { workspace: 'lab', page: 'annotated' });
    assert.equal(lookups(here)[0].headers['X-Gamma-Workspace'], 'lab');
    assert.deepEqual(view.loaded, ['http://127.0.0.1:50999/?ws=lab&page=annotated']);
    assert.equal(opens().length, 0);

    // bytes no page of the NAS holds: read on this computer, in the account's own workspace there
    delete carried[`/api/blocks/by-doc/${docId}`];
    shell.pretendShown(nas.srv, onNas(), appWindow);
    assert.deepEqual(await shell.readPdf(pdf), { workspace: 'mine', page: 'read-here' });
    assert.equal(lookups(nas).length, 2, 'asked there first');
    const [open] = opens();
    assert.deepEqual([open.body, open.headers['X-Gamma-Workspace'], open.headers['X-Gamma-Shell']], [{ path: pdf }, 'mine', shell.shellSecret]);
    assert.equal(shell.current().id, here.srv.id);

    // a workspace of this computer's server the account writes in: the server looks for the bytes itself
    const asked = lookups(here).length;
    shell.pretendShown(here.srv, windowOn(here.fake, [], 'mine'), appWindow);
    assert.deepEqual(await shell.readPdf(pdf), { workspace: 'mine', page: 'read-here' });
    assert.equal(lookups(here).length, asked);
    assert.equal(opens().length, 2);
  } finally {
    sidecar.start = realStart;
    shell.pretendShown(null, null);
  }
});

test('a page\'s Continue in a terminal: the command is the open server\'s answer, asked in the page\'s workspace, its refusals said in the notice', async () => {
  fresh();
  const terminal = require('../lib/terminal');
  const realOnPath = terminal.onPath;
  terminal.onPath = (name) => (name === 'osascript' ? null : `/found/${name}`); // a terminal on every platform
  try {
    const s = server('Local');
    const argv = [path.join(dir, 'codex'), 'resume', 'thread-1'];
    let answer = { cwd: dir, argv, agent: 'codex', label: 'Codex' };
    s.fake.routes = (p) => (p === '/api/local-agents/chat/terminal' ? answer : null);
    shell.pretendShown(s.srv, windowOn(s.fake));
    const asks = () => s.fake.calls.filter((c) => c.path === '/api/local-agents/chat/terminal');

    const run = await shell.terminalFromPage('mine', 'p1');
    const [call] = asks();
    assert.deepEqual([call.query, call.method, call.headers['X-Gamma-Workspace']], ['?page=p1', 'GET', 'mine']);
    assert.deepEqual(shell.terminals.at(-1), run);
    assert.equal(run.options.cwd, dir);
    assert.ok(run.args.includes('thread-1'), 'the session resumed');
    assert.equal(shell.notice(), 'Opened Codex’s conversation in a terminal.');

    // the server's refusals, said as they are; nothing opened
    const opened = shell.terminals.length;
    for (const [status, detail] of [[409, 'Codex is still working: stop it first.'], [404, 'This conversation has no session to continue.']]) {
      s.fake.routes = (p) => (p === '/api/local-agents/chat/terminal' ? { failWith: status, body: { detail } } : null);
      assert.equal(await shell.terminalFromPage('mine', 'p1'), null);
      assert.equal(shell.notice(), detail);
    }
    // an answer that is no command, or a directory not there
    s.fake.routes = (p) => (p === '/api/local-agents/chat/terminal' ? answer : null);
    answer = { cwd: dir, argv: 'codex resume thread-1', label: 'Codex' };
    assert.equal(await shell.terminalFromPage('mine', 'p1'), null);
    assert.equal(shell.notice(), 'The answer names no command to run in the terminal.');
    const gone = path.join(dir, 'gone');
    answer = { cwd: gone, argv, label: 'Codex' };
    assert.equal(await shell.terminalFromPage('mine', 'p1'), null);
    assert.equal(shell.notice(), `${gone} is not on this computer any more.`);
    assert.equal(shell.terminals.length, opened);

    // a remote server's page is not asked of
    const remote = server('NAS', [], { remote: 'https://nas.example' });
    shell.pretendShown(remote.srv, windowOn(s.fake, [{ origin: 'https://nas.example', fake: remote.fake }]));
    assert.equal(await shell.terminalFromPage('mine', 'p1'), null);
    assert.equal(remote.fake.calls.length, 0);
    assert.equal(shell.terminals.length, opened);
  } finally {
    terminal.onPath = realOnPath;
    shell.pretendShown(null, null);
  }
});
