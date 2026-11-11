// main.js under plain Node, Electron replaced by a stand-in and each local
// server by a fake answering the shell's API calls (the GAMMA_SHELL_TEST
// hooks reach the shell's internals): what a keeping reading records about
// the Zotero links (refreshKeeping -> registry.setZoteroHost, the servers
// startBackgroundHosts starts at launch), a resume made while a reading
// runs, one start per server however many ask, and the synced vaults: the
// panel's actions in the vocabulary, a round followed to its end, a vault
// made from a directory through the dialog with the shell's word on the
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
      if (p === '/api/session') return ok({ user: { id: 'admin', username: 'admin' }, default_workspace: 'mine', workspaces: fake.workspaces });
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

test('a synced vault made from a directory with no server open: on the host, in its own workspace, as the dialog says; the routes that take a path carry the word the host was started with', async () => {
  fresh();
  const host = server('Host', [], { running: false });
  const picked = fs.mkdtempSync(path.join(dir, 'Reading-'));
  const infos = [];
  const realStart = sidecar.start;
  sidecar.start = async (ws, _settings, info) => {
    infos.push(info);
    running.add(ws.id);
    return { url: 'http://127.0.0.1:50999', port: 50999 };
  };
  host.fake.routes = (p, init) => {
    if (p === '/api/folder-links/preview') {
      return { path: picked, notes: 2, pdfs: 1, pictures: 0, untracked: 0, empty: false, marker: null, refusal: '', pages: 0, will_write: ['.gamma/'] };
    }
    if (p === '/api/folder-links' && init.method === 'POST') return vaultLink({ path: picked, dest: picked });
    return null;
  };
  const made = () => host.fake.calls.filter((c) => c.path === '/api/folder-links' && c.method === 'POST');
  process.env.GAMMA_SHELL_PICK_DIR = picked;
  try {
    const adding = shell.addDirectoryVault();
    await until(() => shell.vaultAsk(), 'the dialog is up');
    const ask = shell.vaultAsk();
    assert.deepEqual([ask.newFolder, ask.pull, ask.push, ask.folder, ask.workspace, ask.server, ask.path],
      [true, true, 'everything', path.basename(picked), 'Mine', 'Host', picked]);
    assert.equal(ask.resolve, undefined, 'the bar gets the dialog, never its promise');
    assert.match(ask.sentences['true:everything'].text, /^Push 2 notes and 1 PDF from .+ into a new folder “Reading-/);
    assert.equal(ask.sentences['false:off'].ok, false);
    assert.equal(shell.answerVault(ask.id, { pull: false, push: 'off' }), false, 'an answer its sentence refuses');
    assert.equal(shell.answerVault(ask.id + 1, { pull: true, push: 'everything' }), false, 'another dialog\'s');
    assert.equal(shell.answerVault(ask.id, { pull: true, push: 'pdfs' }), true);
    assert.equal((await adding).dest, picked);
    assert.equal(shell.vaultAsk(), null);
    const [create] = made();
    assert.deepEqual(create.body, { path: picked, new_folder: true, pull: true, push: 'pdfs', adopt: false });
    assert.equal(create.headers['X-Gamma-Workspace'], 'mine', 'the account\'s own workspace on the host');
    assert.equal(infos.length, 1, 'the host was started');
    assert.ok(infos[0].shellSecret && infos[0].shellSecret.length >= 32, 'with a word of its own');
    assert.equal(infos[0].shellSecret, shell.shellSecret);
    const [preview] = host.fake.calls.filter((c) => c.path === '/api/folder-links/preview');
    for (const c of [create, preview]) assert.equal(c.headers['X-Gamma-Shell'], infos[0].shellSecret, `${c.path} carries it`);
    assert.deepEqual(preview.body, { path: picked, folder: '', kind: 'folder' });

    // cancelled: nothing is made
    const again = shell.addDirectoryVault();
    await until(() => shell.vaultAsk(), 'the dialog is up again');
    assert.equal(shell.answerVault(shell.vaultAsk().id, null), true);
    assert.equal(await again, null);
    assert.equal(made().length, 1);
  } finally {
    sidecar.start = realStart;
    delete process.env.GAMMA_SHELL_PICK_DIR;
  }
});

test('the vault dialog stays shown until it is answered, one ask runs at a time, and the shell\'s word goes to a running local server only', async () => {
  fresh();
  const host = server('Host');
  const picked = fs.mkdtempSync(path.join(dir, 'Once-'));
  host.fake.routes = (p, init) => {
    if (p === '/api/folder-links/preview') {
      return { path: picked, notes: 0, pdfs: 0, pictures: 0, untracked: 0, empty: true, marker: null, refusal: '', pages: 0, will_write: ['.gamma/'] };
    }
    if (p === '/api/folder-links' && init.method === 'POST') return vaultLink({ path: picked, dest: picked });
    return null;
  };
  const picks = () => host.fake.calls.filter((c) => c.path === '/api/folder-links/preview').length;
  const realStart = sidecar.start;
  sidecar.start = async () => ({ url: 'http://127.0.0.1:50999', port: 50999 });
  process.env.GAMMA_SHELL_PICK_DIR = picked;
  try {
    const once = shell.oneAsk(shell.addDirectoryVault);
    const adding = once();
    await until(() => shell.vaultAsk(), 'the dialog is up');
    const { id } = shell.vaultAsk();
    // a press in the page shrinks the bar to its strip, but not while the dialog waits
    shell.setBarExpanded(false);
    assert.equal(shell.barExpanded(), true, 'the dialog is not hidden while it waits');
    // a second ask while the first runs (a double click, a page asking in a loop) is dropped
    assert.equal(await once(), null);
    assert.equal(picks(), 1, 'no second directory was looked at');
    assert.equal(shell.vaultAsk().id, id, 'and the dialog the user is answering stays');
    assert.equal(shell.answerVault(id, null), true);
    assert.equal(await adding, null);
    assert.equal(shell.barExpanded(), false, 'answered, the bar goes back to its strip');
    // once it settled, the next ask runs
    const again = once();
    await until(() => shell.vaultAsk(), 'a later ask shows its dialog');
    shell.answerVault(shell.vaultAsk().id, null);
    assert.equal(await again, null);
    assert.equal(picks(), 2);
  } finally {
    sidecar.start = realStart;
    delete process.env.GAMMA_SHELL_PICK_DIR;
  }
  assert.deepEqual(shell.shellWord('http://127.0.0.1:50999'), { 'X-Gamma-Shell': shell.shellSecret });
  registry.addRemote('NAS', 'http://nas.lan:9001');
  assert.throws(() => shell.shellWord('http://nas.lan:9001'), /only to a server on this computer/, 'never to a remote');
  running.clear();
  assert.throws(() => shell.shellWord('http://127.0.0.1:50999'), /only to a server on this computer/, 'nor to a port no server of this app holds now');
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

    // Sync the folder…: the directory's own dialog, about that directory, with no picker asked
    shell.answers.push(0);
    const syncing = shell.addFileVault('mine', 'p3');
    await until(() => shell.vaultAsk(), 'the vault dialog is up');
    assert.deepEqual([shell.vaultAsk().path, shell.vaultAsk().newFolder, shell.vaultAsk().workspace], [papers, true, 'Mine']);
    shell.answerVault(shell.vaultAsk().id, { pull: true, push: 'everything' });
    assert.equal((await syncing).dest, papers);
    assert.deepEqual(made().at(-1), { path: papers, new_folder: true, pull: true, push: 'everything', adopt: false });
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
