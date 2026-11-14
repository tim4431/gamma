// main.js under plain Node, Electron replaced by a stand-in and each local
// server by a fake answering the shell's API calls (the GAMMA_SHELL_TEST
// hooks reach the shell's internals): what a keeping reading records about
// the Zotero links (refreshKeeping -> registry.setZoteroHost, the servers
// startBackgroundHosts starts at launch), a resume made while a reading
// runs, one start per server however many ask, and the synced vaults: the
// panel's actions in the vocabulary, a round followed to its end, a work
// folder made from a directory and its tracking dialog opened in the window,
// a folder with pages through the bar's dialog, the shell's word on the
// routes that take a path, and the focus hint; and a file or a folder opened
// with Gamma: what each local server says it is (whereIs, as which account),
// where the window goes for each answer, the question about a Markdown file
// outside every vault, one new work folder at a time, the broad places, and
// a file read where it is. `npm test` in desktop/.

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
  'shell', 'ipcMain', 'net', 'systemPreferences']) {
  electron[name] = stub();
}
electron.dialog = stub({ showMessageBox: async () => ({ response: 0 }) }); // an error box (errorBox), shown to no one
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
  shell.waiting.length = 0;
  shell.errorBoxes.length = 0;
  shell.pretendBooted(true);
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
  s.fake.vaults = [vaultLink({ workspace_id: 'nas-ws', remote_url: 'http://nas.lan:9001', token_id: 't1', work: false })];
  s.fake.routes = (p, init) => (p === '/api/folder-links/v1' && init.method === 'PATCH' ? vaultLink() : null);
  const patches = () => s.fake.calls.filter((c) => c.path === '/api/folder-links/v1' && c.method === 'PATCH').map((c) => c.body);
  await shell.refreshKeeping();
  await assert.rejects(shell.keepingAction('vault', s.srv.id, 'v1', 'pull', false),
    /^Error: A vault of another server pulls, and pushes PDFs at most: a work folder there is made from an empty folder\.$/);
  await assert.rejects(shell.keepingAction('vault', s.srv.id, 'v1', 'push', 'everything'), /pushes PDFs at most/);
  assert.deepEqual(patches(), [], 'nothing was changed');
  await shell.keepingAction('vault', s.srv.id, 'v1', 'pull', true);
  assert.deepEqual(patches(), [{ pull: true }], 'turned on, as the rule wants');
});

test('a work folder of another server pushes everything and pulls: its switches refuse the rest, and it has no agent here until this computer is shown there; its library switch is the host\'s', async () => {
  fresh();
  const s = server('Host');
  s.fake.vaults = [vaultLink({ workspace_id: 'nas-ws', remote_url: 'http://nas.lan:9001', token_id: 't1', push: 'everything', work: true, mine: true })];
  s.fake.routes = (p, init) => (p === '/api/folder-links/v1' && init.method === 'PATCH' ? vaultLink() : null);
  const patches = () => s.fake.calls.filter((c) => c.path === '/api/folder-links/v1' && c.method === 'PATCH');
  await shell.refreshKeeping();
  const [item] = shell.keeping().items;
  assert.deepEqual([item.work, item.agentHere, item.agents, item.push, item.pull], [true, false, null, 'everything', true]);
  const rule = /^Error: A work folder of another server pushes everything and pulls: remove the vault to stop\.$/;
  await assert.rejects(shell.keepingAction('vault', s.srv.id, 'v1', 'pull', false), rule);
  await assert.rejects(shell.keepingAction('vault', s.srv.id, 'v1', 'push', 'pdfs'), rule);
  await assert.rejects(shell.keepingAction('vault', s.srv.id, 'v1', 'push', 'off'), rule);
  await assert.rejects(shell.setAgent(s.srv.id, 'v1', 'codex', true), /^Error: Show this computer on .+ first\.$/);
  assert.deepEqual(patches(), [], 'nothing was changed');
  await shell.keepingAction('vault', s.srv.id, 'v1', 'library', true);
  assert.deepEqual(patches().map((c) => [c.body, c.headers['X-Gamma-Workspace']]), [[{ library: true }, undefined]], 'the account\'s, no workspace of the host');
});

test('the agent of a work folder of another server this computer is shown on: offered, asked with what that server can then do, the shell\'s word, never asking in the chat', async () => {
  fresh();
  registry.addRemote('NAS', 'http://nas.lan:9001');
  const s = server('Host');
  const far = (agent) => vaultLink({ workspace_id: 'nas-ws', remote_url: 'http://nas.lan:9001', plain: 'lan', token_id: 't1', push: 'everything', work: true, mine: true, agent });
  const codexOn = { name: 'codex', label: 'Codex', ready: true, problem: '', running: null, runs: 'never' };
  s.fake.vaults = [far(null)];
  s.fake.routes = (p, init) => {
    if (p === '/api/local-agents') return { agents: [{ name: 'codex', label: 'Codex', found: true, ready: true, problem: '' }] };
    if (p === '/api/computer-links') {
      return { links: [{ id: 'c1', remote_url: 'http://nas.lan:9001', workspace_id: 'nas-ws', account: 'u_1', username: 'tim', status: { state: 'connected', error: '', at: '2026-11-13T10:00:00Z' } }] };
    }
    if (p === '/api/folder-links/v1' && init.method === 'PATCH') {
      const body = JSON.parse(init.body);
      if (body.agent_runs === 'ask') return { failWith: 409, body: { detail: 'Asking in the chat is for a work folder of this computer\'s server.' } };
      s.fake.vaults = [far(body.agent ? codexOn : null)];
      return { ...s.fake.vaults[0], queued: 0 };
    }
    return null;
  };
  const patches = () => s.fake.calls.filter((c) => c.path === '/api/folder-links/v1' && c.method === 'PATCH');
  await shell.refreshKeeping();
  let [item] = shell.keeping().items;
  assert.deepEqual([item.work, item.agentHere, item.agents.map((a) => a.name), item.computer.state], [true, true, ['codex', 'claude'], 'connected']);

  // asked first with what NAS can then do, over plain http from outside this computer; no Sends counted here, where none wait
  await shell.keepingAction('vault', s.srv.id, 'v1', 'agent', 'codex');
  assert.deepEqual(patches(), [], 'nothing turned on');
  assert.equal(shell.asked.at(-1), 'Let Codex work in C:\\Papers? Codex will answer the Sends you make on this folder: it edits files in this directory and runs commands in its sandbox, without network.' +
    ' Whoever can sign in to NAS as tim, and whoever runs NAS, can send work to Codex here. Codex reads your files and its answers are stored on NAS.' +
    ' NAS is reached without encryption: someone on your network could read what is sent and pose as NAS.');
  assert.ok(!s.fake.calls.some((c) => c.path === '/api/local-agents/waiting'), 'the host counts no Sends of another server');

  shell.answers.push(0);
  await shell.keepingAction('vault', s.srv.id, 'v1', 'agent', 'codex');
  const [on] = patches();
  assert.deepEqual(on.body, { agent: 'codex' });
  assert.equal(on.headers['X-Gamma-Shell'], shell.shellSecret, 'turning an agent on is the computer\'s word');
  assert.equal(on.headers['X-Gamma-Workspace'], undefined, 'the account\'s, no workspace of the host');
  assert.equal(shell.notice(), 'Codex now answers the Sends on Papers.');
  await shell.refreshKeeping();
  [item] = shell.keeping().items;
  assert.deepEqual([item.agent, item.agentRuns], ['codex', 'never']);

  // never asking in the chat: refused before anything is asked or sent
  const asks = shell.asked.length;
  await assert.rejects(shell.keepingAction('vault', s.srv.id, 'v1', 'agent-runs', 'ask'), /^Error: Asking in the chat is for a work folder of this computer's server\.$/);
  assert.equal(await shell.setAgentRuns(s.srv.id, 'v1', 'never'), false);
  assert.equal(shell.asked.length, asks, 'nothing asked');
  assert.equal(patches().length, 1);

  // no longer shown there: no agent offered, none turned on; turning it off still goes
  s.fake.routes = ((routes) => (p, init) => (p === '/api/computer-links' ? { links: [] } : routes(p, init)))(s.fake.routes);
  await shell.refreshKeeping();
  [item] = shell.keeping().items;
  assert.deepEqual([item.agentHere, item.agents, item.computer.state], [false, null, 'none']);
  await assert.rejects(shell.setAgent(s.srv.id, 'v1', 'claude', true), /^Error: Show this computer on NAS first\.$/);
  await shell.keepingAction('vault', s.srv.id, 'v1', 'agent', '');
  assert.deepEqual(patches().at(-1).body, { agent: '' });
  assert.equal(patches().at(-1).headers['X-Gamma-Shell'], undefined);
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
// DELETE and answers its token. The NAS's whoami is `state.whoami`.
function computerRoutes(host, nas, { replace = null, fail = null } = {}) {
  const state = { links: [], minted: [], fail, whoami: { user: 'admin', role: 'owner', scope: 'session', work_folders: 1 } };
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
    if (p === '/api/sync/whoami') return state.whoami;
    if (p === '/api/sync/folders') return FOLDERS;
    // Kept lab holds pages there; Empty lab none
    if (p === '/api/sync/folders/f1') return { pages: [{ id: 'a1' }, { id: 'a2' }, { id: 'a3' }] };
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

test('an empty folder of another server synced with a directory of notes becomes a work folder there: a write token, the host\'s vault made choosing, this computer shown, the host\'s own window on its tracking dialog, and its answer takes the window back', async () => {
  fresh();
  const host = server('Host');
  const nas = server('NAS', [], { remote: 'http://nas.lan:9001' });
  const picked = fs.mkdtempSync(path.join(dir, 'FarWork-'));
  let refuse = null;
  host.fake.routes = (p, init) => {
    if (p === '/api/folder-links/preview') return previewOf(picked);
    if (p === '/api/folder-links' && init.method === 'POST') {
      if (refuse) return refuse;
      const body = JSON.parse(init.body);
      const made = vaultLink({ id: 'w1', folder_id: body.folder, path: picked, dest: picked, workspace_id: 'team', remote_url: 'http://nas.lan:9001',
        token_id: body.token_id, push: 'everything', work: true, choosing: true, mine: true });
      host.fake.vaults = [made];
      return made;
    }
    return null;
  };
  const shown = computerRoutes(host, nas);
  const realStart = sidecar.start;
  sidecar.start = async () => ({ url: 'http://127.0.0.1:50999', port: 50999 });
  // a Cancel revokes with the window's session, signed into the remote
  electron.session.defaultSession.fetch = async (url, init = {}) => nas.fake.answer(new URL(url).pathname, init, new URL(url));
  const view = windowOn(host.fake, [{ origin: 'http://nas.lan:9001', fake: nas.fake }], 'team');
  const made = () => host.fake.calls.filter((c) => c.path === '/api/folder-links' && c.method === 'POST');
  process.env.GAMMA_SHELL_PICK_DIR = picked;
  try {
    shell.pretendShown(nas.srv, view, appWindow);
    const link = await shell.addFolderVault('team', 'f2');
    assert.equal(link.id, 'w1');
    assert.equal(shell.vaultAsk(), null, 'nothing asked in the bar: its tracking dialog says what it tracks');
    const [vaultToken, computer] = shown.minted;
    assert.deepEqual([vaultToken.scope, vaultToken.expires_in_days, computer.scope], ['write', 365, 'computer'], 'a token that may write, then this computer\'s own');
    const [create] = made();
    assert.deepEqual(create.body, { folder: 'f2', path: picked, notes: true, pull: true, push: 'everything', choose: true,
      remote_url: 'http://nas.lan:9001', workspace: 'team', token: vaultToken.token, token_id: vaultToken.id });
    assert.equal(create.headers['X-Gamma-Shell'], shell.shellSecret, 'the shell\'s word, to the host');
    assert.equal(create.headers['X-Gamma-Workspace'], undefined, 'no workspace of the host');
    assert.equal(host.fake.calls.filter((c) => c.path === '/api/computer-links' && c.method === 'POST').length, 1, 'this computer shown there');
    for (const c of nas.fake.calls) {
      assert.equal(c.headers['X-Gamma-Shell'], undefined, 'the word never goes to the remote');
      assert.ok(!(c.body && JSON.stringify(c.body).includes(picked)), 'nor a path');
    }
    // the host's own window, over its own workspace, never the remote's ids
    assert.equal(shell.current().id, host.srv.id);
    const at = new URL(view.loaded.at(-1));
    assert.equal(at.origin, 'http://127.0.0.1:50999');
    assert.deepEqual([at.searchParams.get('ws'), at.searchParams.get('folder'), at.searchParams.get('track')], ['mine', null, 'w1']);
    assert.equal(shell.registry.getSettings().folderHost, host.srv.id, 'the host is remembered for launch');

    // only the dialog the shell opened is taken, and only while the window shows its host
    assert.equal(await shell.trackedFromPage('mine', 'other', false), null);
    // Saved (the host chose its files): the window goes back to that server, the folder open
    host.fake.vaults = host.fake.vaults.map((v) => ({ ...v, choosing: false }));
    assert.equal(await shell.trackedFromPage('mine', 'w1', false), true);
    assert.equal(shell.current().id, nas.srv.id);
    assert.equal(view.loaded.at(-1), 'http://nas.lan:9001/?ws=team&folder=f2');
    assert.match(shell.notice(), /pushes the files you chose to NAS/);
    assert.deepEqual(revoked(nas), [], 'nothing revoked');
    assert.equal(await shell.trackedFromPage('mine', 'w1', true), null, 'answered once');

    // Choose files… from the panel opens the host's window the same way; its Cancel of a fresh dialog removed the vault (the page's DELETE)
    await shell.refreshKeeping();
    await shell.keepingAction('vault', host.srv.id, 'w1', 'choose');
    assert.equal(shell.current().id, host.srv.id);
    assert.deepEqual([...new URL(view.loaded.at(-1)).searchParams], [['ws', 'mine'], ['track', 'w1']]);
    host.fake.vaults = [];
    assert.equal(await shell.trackedFromPage('mine', 'w1', true), true);
    assert.deepEqual(revoked(nas), [`${vaultToken.id}@team`, `${computer.id}@team`], 'its token revoked there, and this computer no longer shown');
    assert.equal(host.fake.calls.filter((c) => c.path === '/api/computer-links/c1' && c.method === 'DELETE').length, 1);
    assert.equal(shell.current().id, nas.srv.id, 'and the window back there');
    assert.equal(shell.registry.getSettings().folderHost, '', 'the host keeps nothing of remotes now');

    // a Cancel the host refused (NAS not reached): the vault stays choosing, its token kept, and the bar says so
    assert.equal((await shell.addFolderVault('team', 'f2')).id, 'w1');
    const kept = revoked(nas).length;
    assert.equal(await shell.trackedFromPage('mine', 'w1', false), true);
    assert.equal(revoked(nas).length, kept, 'nothing revoked');
    assert.equal(shell.notice(), '“Empty lab” could not be cancelled, NAS not reached: it waits for its files to be chosen. Choose files… in the sync panel, or remove the vault once NAS answers.');
    assert.equal(shell.current().id, nas.srv.id);
    host.fake.vaults = [];

    // what cannot be a work folder there is asked in the vault dialog, pushing nothing to start, the reason in the bar
    shell.pretendShown(nas.srv, view); // (no app window: the dialog lays nothing out)
    const refused = async (words) => {
      const flow = shell.addFolderVault('team', 'f2');
      await until(() => shell.vaultAsk(), 'the dialog is up');
      assert.deepEqual([shell.vaultAsk().remote, shell.vaultAsk().push, shell.vaultAsk().pull], [true, 'off', true]);
      assert.equal(shell.notice(), `“Empty lab” cannot be a work folder of NAS: ${words} It can be synced as a vault that pulls instead.`);
      shell.answerVault(shell.vaultAsk().id, null);
      assert.equal(await flow, null);
      assert.equal(shell.current().id, nas.srv.id, 'the window stays');
    };
    // the host refuses it (that folder holds pages there after all): the token minted for it is revoked
    const before = made().length;
    refuse = { failWith: 409, body: { detail: 'That folder of NAS is not empty: a work folder of another server starts empty.' } };
    await refused('That folder of NAS is not empty: a work folder of another server starts empty.');
    assert.equal(revoked(nas).at(-1), `${shown.minted.at(-1).id}@team`);
    assert.equal(shown.minted.at(-1).scope, 'write');
    assert.equal(made().length, before + 1, 'asked of the host once');
    refuse = null;
    // that server keeps no work folders, or the account may only view there: nothing minted, nothing asked of the host
    const minted = shown.minted.length;
    shown.whoami = { user: 'admin', role: 'owner', scope: 'session' };
    await refused('NAS is older than this app and keeps no work folders.');
    shown.whoami = { user: 'admin', role: 'viewer', scope: 'session', work_folders: 1 };
    await refused('Your account may only view this workspace there.');
    assert.deepEqual([shown.minted.length, made().length], [minted, before + 1]);
    const whoami = nas.fake.calls.filter((c) => c.path === '/api/sync/whoami');
    assert.equal(whoami.at(-1).headers['X-Gamma-Workspace'], 'team', 'asked of that workspace, with the window\'s session');
  } finally {
    sidecar.start = realStart;
    delete process.env.GAMMA_SHELL_PICK_DIR;
    delete electron.session.defaultSession.fetch;
    shell.pretendShown(null, null);
  }
});

test('removing a work folder of another server that cannot be reached: refused, then forgotten here only when asked', async () => {
  fresh();
  const host = server('Host');
  const nas = server('NAS', [], { remote: 'http://nas.lan:9001' });
  host.fake.vaults = [vaultLink({ workspace_id: 'team', remote_url: 'http://nas.lan:9001', token_id: 'vt', push: 'everything', work: true })];
  computerRoutes(host, nas);
  const routes = host.fake.routes;
  host.fake.routes = (p, init) => (p === '/api/folder-links/v1' && init.method === 'DELETE' ? { ok: true } : routes(p, init));
  // the host refuses a removal it cannot let go of there, unless forced
  const answer = host.fake.answer.bind(host.fake);
  host.fake.answer = async (p, init, url) => {
    if (p !== '/api/folder-links/v1' || init.method !== 'DELETE' || url.search.includes('force=1')) return answer(p, init, url);
    host.fake.calls.push({ path: p, query: url.search, method: 'DELETE', headers: init.headers || {}, body: null });
    return { ok: false, status: 409, json: async () => ({ detail: 'NAS cannot be reached: remove the vault when it can, or let go of the folder there' }) };
  };
  const deletes = () => host.fake.calls.filter((c) => c.path === '/api/folder-links/v1' && c.method === 'DELETE').map((c) => c.query);
  electron.session.defaultSession.fetch = async (url, init = {}) => nas.fake.answer(new URL(url).pathname, init, new URL(url));
  try {
    await shell.refreshKeeping();
    // the question takes Cancel under the harness: nothing forgotten
    assert.equal(await shell.removeVault(host.srv.id, 'v1', 'keep'), false);
    assert.deepEqual(deletes(), ['?files=keep']);
    assert.match(shell.asked.at(-1), /^NAS cannot be reached: remove the work folder “Papers” all the same\? NAS cannot be reached: remove the vault when it can/);
    shell.answers.push(0);
    assert.equal(await shell.removeVault(host.srv.id, 'v1', 'keep'), true);
    assert.deepEqual(deletes(), ['?files=keep', '?files=keep', '?files=keep&force=1']);
    assert.deepEqual(revoked(nas), ['vt@team'], 'its token revoked there, as far as that server answers');
    assert.match(shell.notice(), /^Papers is no longer synced with this computer; on NAS its folder stays a work folder until the workspace's owner lets go of it there\./);

    // asked from the panel: the host could not count its files there, so that question comes first, never the usual one
    host.fake.vaults = [vaultLink({ workspace_id: 'team', remote_url: 'http://nas.lan:9001', token_id: 'vt', push: 'everything', work: true })];
    host.fake.routes = (p, init) => (p === '/api/folder-links/v1/removal' ? { pages: 4, files: 0, in_library: 4, in_place: 0, bytes: 0, unreachable: true }
      : p === '/api/folder-links/v1' && init.method === 'DELETE' ? { ok: true } : routes(p, init));
    await shell.refreshKeeping();
    shell.asked.length = 0;
    assert.equal(await shell.removeVault(host.srv.id, 'v1'), false);
    assert.deepEqual(shell.asked, ['NAS cannot be reached: remove the work folder “Papers” all the same? NAS does not answer. Remove anyway, and this computer forgets it; the folder stays a work folder on NAS, its files locked, until the workspace\'s owner lets go of it there. Or cancel, and remove it once NAS answers.']);
    assert.equal(deletes().length, 3, 'nothing removed');
    shell.answers.push(0);
    assert.equal(await shell.removeVault(host.srv.id, 'v1'), true);
    assert.equal(shell.asked.length, 2, 'one question');
    assert.deepEqual(deletes().slice(3), ['?files=keep&force=1'], 'forced at once, its files kept');
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
    assert.deepEqual(await shell.readFile(pdf), { workspace: 'team', page: 'annotated' });
    const [lookup] = lookups(nas);
    assert.deepEqual([lookup.path, lookup.method, lookup.headers['X-Gamma-Workspace']], [`/api/blocks/by-doc/${docId}`, 'GET', 'team']);
    assert.deepEqual(view.loaded, ['http://nas.lan:9001/?ws=team&page=annotated']);
    assert.ok(nas.fake.calls.every((c) => c.method === 'GET' && !c.form), 'nothing sent there but the hash');
    assert.equal(here.fake.calls.length, 0, 'this computer\'s server is not asked');
    assert.equal(shell.current().id, nas.srv.id, 'the window stays on the NAS');

    // in a clone on this computer: its page there
    view = windowOn(here.fake, [], 'lab');
    shell.pretendShown(here.srv, view, appWindow);
    assert.deepEqual(await shell.readFile(pdf), { workspace: 'lab', page: 'annotated' });
    assert.equal(lookups(here)[0].headers['X-Gamma-Workspace'], 'lab');
    assert.deepEqual(view.loaded, ['http://127.0.0.1:50999/?ws=lab&page=annotated']);
    assert.equal(opens().length, 0);

    // bytes no page of the NAS holds: read on this computer, in the account's own workspace there
    delete carried[`/api/blocks/by-doc/${docId}`];
    shell.pretendShown(nas.srv, onNas(), appWindow);
    assert.deepEqual(await shell.readFile(pdf), { workspace: 'mine', page: 'read-here' });
    assert.equal(lookups(nas).length, 2, 'asked there first');
    const [open] = opens();
    assert.deepEqual([open.body, open.headers['X-Gamma-Workspace'], open.headers['X-Gamma-Shell']], [{ path: pdf }, 'mine', shell.shellSecret]);
    assert.equal(shell.current().id, here.srv.id);

    // a workspace of this computer's server the account writes in: the server looks for the bytes itself
    const asked = lookups(here).length;
    shell.pretendShown(here.srv, windowOn(here.fake, [], 'mine'), appWindow);
    assert.deepEqual(await shell.readFile(pdf), { workspace: 'mine', page: 'read-here' });
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

// ------------------------------------------------------ Open with Gamma -----

// What `POST /api/folder-links/where` answers on fake `s`, by path
// (`answers`, read when asked; a function answers when called; any other
// path is nobody's), the other routes as `more` answers them.
const isDir = (p) => {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
};
function whereOf(s, answers, more = () => null) {
  s.fake.routes = (p, init) => {
    if (p === '/api/folder-links/where') {
      const asked = JSON.parse(init.body).path;
      const a = answers[asked];
      return typeof a === 'function' ? a() : a || { is: 'none', dir: isDir(asked), name: path.basename(asked), refusal: '' };
    }
    return more(p, init);
  };
}
const whereCalls = (s) => s.fake.calls.filter((c) => c.path === '/api/folder-links/where');

// A `where` answer inside a work folder "Paper" of the local server, `link`
// and `more` changing what they name.
const inVault = (link = {}, more = {}) => ({
  is: 'vault', dir: true, name: 'x', rel: '', never: '', folder: 'f1', exact: true,
  link: { id: 'v1', kind: 'folder', workspace: 'mine', folder: 'f1', remote_url: '', work: true, choosing: false, paused: false, mine: true, name: 'Paper', ...link },
  ...more,
});

// A file of `text` at `p`, its directories made.
function put(p, text = 'x') {
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, text);
  return p;
}

// A local server's start under the test: it answers at the fakes' address
// (`fail`: the ids of those that do not start).
function stubStart(fail = []) {
  const real = sidecar.start;
  sidecar.start = async (ws) => {
    if (fail.includes(ws.id)) throw new Error('could not bind');
    running.add(ws.id);
    return { url: 'http://127.0.0.1:50999', port: 50999 };
  };
  return () => { sidecar.start = real; };
}

// The address the window loaded last, as its query.
const lastAt = (view) => Object.fromEntries(new URL(view.loaded.at(-1)).searchParams);

// The routes a work folder made from a directory takes on fake `s`: the
// preview, the vault made (`w<n>`, choosing), and each one read again,
// choosing while `chooses()` says so.
function makesWorkFolders(s, chooses = () => true) {
  let made = 0;
  return (p, init) => {
    if (p === '/api/folder-links/preview') return previewOf(JSON.parse(init.body).path);
    if (p === '/api/folder-links' && init.method === 'POST') {
      made += 1;
      const body = JSON.parse(init.body);
      return vaultLink({ id: `w${made}`, folder_id: `nf${made}`, path: body.path, dest: body.path, push: 'everything', choosing: true });
    }
    const m = p.match(/^\/api\/folder-links\/(w\d+)$/);
    if (m) return { id: m[1], choosing: chooses(m[1]) };
    return null;
  };
}
const made = (s) => s.fake.calls.filter((c) => c.path === '/api/folder-links' && c.method === 'POST');
const opens = (s) => s.fake.calls.filter((c) => c.path === '/api/folder-links/open');
const questions = (prefix) => shell.asked.filter((q) => q.startsWith(prefix));

test('what is handed over before the app is up waits for it, as absolute paths of what exists', async () => {
  fresh();
  const paper = fs.mkdtempSync(path.join(dir, 'Early-'));
  shell.pretendBooted(false);
  assert.deepEqual(await shell.openWithGamma([path.basename(paper), path.join(dir, 'gone.md'), '--hidden'], dir), []);
  assert.deepEqual(shell.pretendBooted(true), [paper], 'the boot opens it');
});

test('whereIs asks the window\'s server through the window\'s session and the others as the shell, the first answer that is not none winning; a signed-out window learns what a path is and is told to sign in before going there', async () => {
  fresh();
  const one = server('One');
  const two = server('Two');
  const paper = fs.mkdtempSync(path.join(dir, 'Where-'));
  const answers1 = {};
  whereOf(one, answers1);
  whereOf(two, { [paper]: inVault({ id: 'v2', name: 'There' }) });
  const through = [];
  const view = windowOn({ answer: (p, init, url) => { through.push(p); return one.fake.answer(p, init, url); } });
  shell.pretendShown(one.srv, view, appWindow);
  try {
    const a = await shell.whereIs(paper);
    assert.deepEqual([a.is, a.srv.id, a.as, a.link.id], ['vault', two.srv.id, 'shell', 'v2'], 'nobody\'s on One, Two\'s vault');
    assert.deepEqual(through, ['/api/session', '/api/folder-links/where'], 'the window\'s server through its session, signed in there');
    assert.deepEqual([whereCalls(one).length, whereCalls(two).length], [1, 1]);
    for (const c of [...whereCalls(one), ...whereCalls(two)]) {
      assert.equal(c.headers['X-Gamma-Shell'], shell.shellSecret, 'a path goes with the shell\'s word');
      assert.equal(c.headers['X-Gamma-Workspace'], undefined, 'and no workspace');
      assert.deepEqual(c.body, { path: paper });
    }
    // the window's server first: its answer wins, the next one is not asked
    answers1[paper] = inVault();
    const b = await shell.whereIs(paper);
    assert.deepEqual([b.srv.id, b.as, b.signedOut, b.link.id], [one.srv.id, 'window', false, 'v1']);
    assert.equal(whereCalls(two).length, 1);

    // signed out in the window: One asked as the shell, only to learn what the path is
    const out = windowOn({ answer: (p, init, url) => (p === '/api/session'
      ? { ok: true, status: 200, json: async () => ({ user: null }) } : one.fake.answer(p, init, url)) });
    shell.pretendShown(one.srv, out, appWindow);
    const c = await shell.whereIs(paper);
    assert.deepEqual([c.srv.id, c.as, c.signedOut], [one.srv.id, 'shell', true]);
    const [got] = await shell.openWithGamma([paper]);
    assert.equal(got.error, `Sign in to One, then open ${path.basename(paper)} again.`);
    assert.deepEqual(out.loaded, [], 'and the window goes nowhere');

    // signed out between the session read and the question (a 401): asked again as the shell
    const flaky = windowOn({ answer: (p, init, url) => (p === '/api/folder-links/where'
      ? { ok: false, status: 401, json: async () => ({ detail: 'Not signed in' }) } : one.fake.answer(p, init, url)) });
    shell.pretendShown(one.srv, flaky, appWindow);
    const d = await shell.whereIs(paper);
    assert.deepEqual([d.srv.id, d.as, d.signedOut, d.link.id], [one.srv.id, 'shell', true, 'v1']);
  } finally {
    shell.pretendShown(null, null);
  }
});

test('whereIs skips a server that does not answer in 10 s or has no such route, starts a stopped local server before a path is believed nobody\'s, names one that does not start, and never asks a remote server', async () => {
  fresh();
  const slow = server('Slow');
  const old = server('Old');
  const later = server('Later', [], { running: false });
  const broken = server('Broken', [], { running: false });
  const nas = server('NAS', [], { remote: 'http://nas.lan:9001' });
  const tex = put(path.join(dir, 'Far', 'main.tex'), '\\documentclass{article}');
  const fresh1 = fs.mkdtempSync(path.join(dir, 'Nobody-'));
  whereOf(slow, { [tex]: () => new Promise(noop) }, makesWorkFolders(slow));
  old.fake.routes = (p) => (p === '/api/folder-links/where' ? { failWith: 404 } : null);
  whereOf(later, { [tex]: { is: 'file_vault', workspace: 'mine', page: 'fv' } });
  const restore = stubStart([broken.srv.id]);
  const view = windowOn(slow.fake, [{ origin: 'http://nas.lan:9001', fake: nas.fake }], 'team');
  shell.pretendShown(nas.srv, view, appWindow);
  try {
    const before = timers.length;
    const asking = shell.whereIs(tex);
    const wait = () => timers.slice(before).filter((t) => t.ms === 10_000 && !t.cleared);
    await until(() => wait().length === 1, 'Slow is asked, with ten seconds to answer');
    wait()[0].fn(); // the ten seconds go by
    const a = await asking;
    assert.deepEqual([a.is, a.srv.id, a.page], ['file_vault', later.srv.id, 'fv'], 'Old\'s 404 skipped, Later started and asked');
    assert.ok(running.has(later.srv.id));
    assert.ok(!running.has(broken.srv.id), 'a server after the one that answered is not started');
    assert.equal(whereCalls(old).length, 1);

    // nobody's: every local server asked, the one that would not start named, and the folder becomes a work folder
    const [got] = await shell.openWithGamma([fresh1]);
    assert.equal(got.track, 'w1');
    assert.deepEqual([whereCalls(slow).length, whereCalls(later).length], [2, 2]);
    assert.equal(made(slow).length, 1, 'on the host');
    assert.equal(shell.notice(), `“${path.basename(fresh1)}” is a work folder of Mine on Slow now: choose the files it tracks. Gamma could not check Broken's synced vaults: it did not start.`);
    assert.equal(nas.fake.calls.length, 0, 'the remote the window showed is never asked');
  } finally {
    restore();
    shell.pretendShown(null, null);
  }
});

test('a folder opened with Gamma opens what Gamma has there: its work folder\'s folder or its tracking dialog while it chooses, a kept folder, a vault of another server on that server, the one vault it holds; refusals and the rest said', async () => {
  fresh();
  const host = server('Host');
  const nas = server('NAS', [], { remote: 'http://nas.lan:9001' });
  const paper = fs.mkdtempSync(path.join(dir, 'Paper-'));
  const sub = path.join(paper, 'sub');
  const deep = path.join(sub, 'deep');
  const modules = path.join(paper, 'node_modules');
  for (const d of [deep, modules]) fs.mkdirSync(d, { recursive: true });
  const around = fs.mkdtempSync(path.join(dir, 'Around-'));
  const nobody = fs.mkdtempSync(path.join(dir, 'Fresh-'));
  const answers = {};
  const works = makesWorkFolders(host);
  whereOf(host, answers, (p, init) => (p === '/api/sync/folders' ? { folders: [{ id: 'f3', path: ['Paper', 'sub'] }] } : works(p, init)));
  host.fake.vaults = [vaultLink({ push: 'everything', choosing: true, mine: true, path: paper, dest: paper })];
  const view = windowOn(host.fake, [{ origin: 'http://nas.lan:9001', fake: nas.fake }]);
  shell.pretendShown(host.srv, view, appWindow);
  const open = async (p) => (await shell.openWithGamma([p]))[0];
  try {
    // a work folder still choosing: its tracking dialog, for its maker only
    answers[paper] = inVault({ choosing: true });
    const dialog = await open(paper);
    assert.deepEqual([dialog.workspace, dialog.track], ['mine', 'v1']);
    assert.deepEqual(lastAt(view), { ws: 'mine', folder: 'f1', track: 'v1' });
    assert.equal(shell.notice(), '“Paper” waits for its files to be chosen.');
    answers[paper] = inVault({ choosing: true, mine: false });
    assert.equal((await open(paper)).error, 'Only the account that made the work folder “Paper” chooses what it tracks.');
    assert.match(shell.errorBoxes.at(-1), new RegExp(`^Could not open ${path.basename(paper)} in Gamma\\. Only the account`));

    // its directory or one inside it: the folder of that directory, a paused vault as it is
    answers[paper] = inVault({ paused: true });
    assert.deepEqual(await open(paper), { path: paper, workspace: 'mine', folder: 'f1' });
    assert.deepEqual(lastAt(view), { ws: 'mine', folder: 'f1' });
    assert.ok(!host.fake.calls.some((c) => c.method === 'PATCH'), 'nothing resumed');
    answers[sub] = inVault({}, { rel: 'sub', folder: 'f3' });
    await open(sub);
    assert.deepEqual(lastAt(view), { ws: 'mine', folder: 'f3' });
    answers[deep] = inVault({}, { rel: 'sub/deep', folder: 'f3', exact: false });
    await open(deep);
    assert.deepEqual(lastAt(view), { ws: 'mine', folder: 'f3' }, 'the nearest directory above it that has a folder');
    assert.equal(shell.notice(), 'Nothing in sub/deep is tracked yet: “Paper” opened at “sub”. Choose files… picks what it tracks.');
    answers[modules] = inVault({}, { rel: 'node_modules', never: 'it is inside node_modules' });
    await open(modules);
    assert.deepEqual(lastAt(view), { ws: 'mine', folder: 'f1' });
    assert.equal(shell.notice(), 'Gamma never tracks node_modules: it is inside node_modules. “Paper” opened.');

    // a kept folder of the whole library: the library
    answers[paper] = inVault({ work: false, folder: 'root', name: 'Library' }, { folder: 'root' });
    await open(paper);
    assert.deepEqual(lastAt(view), { ws: 'mine' });

    // a vault of another server: that server, by its ids; one the registry does not hold is said
    answers[paper] = inVault({ remote_url: 'http://nas.lan:9001', workspace: 'team', folder: 'r1', work: false }, { folder: 'r2' });
    await open(paper);
    assert.equal(view.loaded.at(-1), 'http://nas.lan:9001/?ws=team&folder=r2');
    assert.equal(shell.current().id, nas.srv.id);
    shell.pretendShown(host.srv, view, appWindow);
    answers[paper] = inVault({ remote_url: 'http://far.lan:9001', workspace: 'team', work: false });
    assert.equal((await open(paper)).error, `${paper} is synced with far.lan:9001: add that server to Gamma to open it here.`);

    // another account's, refused; a directory around vaults: the one it holds, or which they are
    answers[paper] = { is: 'hidden' };
    assert.equal((await open(paper)).error, `${paper} is kept by another account on this computer.`);
    const held = { id: 'v1', workspace: 'mine', folder: 'f1', remote_url: '', work: true, choosing: false, mine: true, name: 'Paper', rel: 'Paper' };
    answers[around] = { is: 'around', vaults: [held] };
    await open(around);
    assert.deepEqual(lastAt(view), { ws: 'mine', folder: 'f1' });
    assert.equal(shell.notice(), `${around} holds the work folder “Paper”, which opened: a work folder is never inside or around another.`);
    const loads = view.loaded.length;
    answers[around] = { is: 'around', vaults: [held, { ...held, id: 'v9', work: false, name: 'Kept', rel: 'b/Kept' }] };
    assert.deepEqual(await open(around), { path: around });
    assert.equal(view.loaded.length, loads, 'nothing opened');
    assert.equal(shell.notice(), `${around} holds 2 synced vaults (“Paper”, “Kept”): open one of their folders with Gamma, or pick it in the sync panel.`);

    // a place no vault may have: the server's words, nothing made
    const refusal = 'Pick a project directory: Gamma does not sync your whole home directory or a directory around it.';
    answers[nobody] = { is: 'none', dir: true, name: 'x', refusal };
    assert.equal((await open(nobody)).error, refusal);
    assert.equal(made(host).length, 0);

    // nothing there: a work folder of the workspace the window shows, its dialog open
    delete answers[nobody];
    const fresh1 = await open(nobody);
    assert.deepEqual([fresh1.workspace, fresh1.folder, fresh1.track], ['mine', 'nf1', 'w1']);
    assert.deepEqual(made(host)[0].body, { path: nobody, new_folder: true, pull: true, push: 'everything', choose: true, adopt: false });
    assert.deepEqual(lastAt(view), { ws: 'mine', folder: 'nf1', track: 'w1' });
    assert.equal(shell.notice(), `“${path.basename(nobody)}” is a work folder of Mine on Host now: choose the files it tracks.`);
  } finally {
    shell.pretendShown(null, null);
  }
});

test('one new work folder at a time: a folder opened while the one made before still chooses its files is told to wait and nothing is made, an existing vault still opens, and once the first is saved the next is made', async () => {
  fresh();
  const host = server('Host');
  const first = fs.mkdtempSync(path.join(dir, 'First-'));
  const second = fs.mkdtempSync(path.join(dir, 'Second-'));
  const kept = fs.mkdtempSync(path.join(dir, 'Kept-'));
  let choosing = true;
  whereOf(host, { [kept]: inVault({ work: false, name: 'Kept' }) }, makesWorkFolders(host, () => choosing));
  const view = windowOn(host.fake);
  shell.pretendShown(host.srv, view, appWindow);
  try {
    // several selected: one handed over after another on one queue
    const [a, b, c] = await shell.openWithGamma([first, second, kept]);
    assert.equal(a.track, 'w1');
    assert.deepEqual(b, { path: second });
    assert.deepEqual([c.workspace, c.folder], ['mine', 'f1'], 'an existing vault is not held back');
    assert.equal(made(host).length, 1, 'one marker written, not two');
    const asked = host.fake.calls.find((call) => call.path === '/api/folder-links/w1');
    assert.equal(asked.headers['X-Gamma-Workspace'], 'mine');
    const [again] = await shell.openWithGamma([second]);
    assert.deepEqual(again, { path: second });
    assert.equal(shell.notice(), `Choose the files of “${path.basename(first)}” first, then open ${second} with Gamma again.`);
    assert.equal(made(host).length, 1);

    // its files chosen (saved): the next one is made
    choosing = false;
    assert.equal((await shell.openWithGamma([second]))[0].track, 'w2');
    assert.equal(made(host).length, 2);
  } finally {
    shell.pretendShown(null, null);
  }
});

test('a file opened with Gamma opens its vault\'s page, ticks a file its work folder does not track in the tracking dialog, has the server open one it tracks, and reads one no vault reads where it is', async () => {
  fresh();
  const host = server('Host');
  const nas = server('NAS', [], { remote: 'http://nas.lan:9001' });
  const paper = fs.mkdtempSync(path.join(dir, 'Files-'));
  const tex = put(path.join(paper, 'main.tex'));
  const csv = put(path.join(paper, 'sub', 'data.csv'));
  const cached = put(path.join(paper, '.cache', 'a.tex'));
  const md = put(path.join(paper, 'theirs.md'));
  const answers = {};
  whereOf(host, answers, (p, init) => (p === '/api/folder-links/open' && init.method === 'POST'
    ? { workspace: 'mine', page: 'opened', created: true, title: 'main', format: 'text' } : null));
  host.fake.vaults = [vaultLink({ push: 'everything', mine: true, path: paper, dest: paper }),
    vaultLink({ id: 'v2', remote_url: 'http://nas.lan:9001', workspace_id: 'team', push: 'everything', mine: true, path: paper, dest: paper })];
  const view = windowOn(host.fake, [{ origin: 'http://nas.lan:9001', fake: nas.fake }]);
  shell.pretendShown(host.srv, view, appWindow);
  const open = async (p) => (await shell.openWithGamma([p]))[0];
  const file = (link, more) => inVault(link, { dir: false, page: '', trashed: false, tracked: false, ...more });
  const remote = { id: 'v2', remote_url: 'http://nas.lan:9001', workspace: 'team', folder: 'r1' };
  try {
    answers[cached] = file({}, { rel: '.cache/a.tex', never: 'it is inside a dot directory' });
    assert.equal((await open(cached)).error, 'Gamma never tracks a.tex: it is inside a dot directory.');

    // its page, whatever its format: no new page, nothing copied
    answers[tex] = file({}, { rel: 'main.tex', page: 'p1', tracked: true });
    assert.deepEqual(await open(tex), { path: tex, workspace: 'mine', page: 'p1' });
    assert.deepEqual(lastAt(view), { ws: 'mine', page: 'p1' });
    assert.equal(opens(host).length, 0, 'the server opens nothing');
    answers[tex] = file({}, { rel: 'main.tex', trashed: true });
    assert.equal((await open(tex)).error, 'main.tex is a file of “Paper”, whose page is in Recently deleted: restore it there.');

    // a work folder's file it does not track: its dialog with the file ticked, its maker only
    answers[csv] = file({}, { rel: 'sub/data.csv' });
    assert.deepEqual(await open(csv), { path: csv, workspace: 'mine', folder: 'f1', track: 'v1', tick: 'sub/data.csv' });
    assert.deepEqual(lastAt(view), { ws: 'mine', folder: 'f1', track: 'v1', tick: 'sub/data.csv' });
    assert.equal(shell.notice(), 'data.csv is not tracked by “Paper” yet: tick it and save to open it.');
    answers[csv] = file({ choosing: true }, { rel: 'sub/data.csv', tracked: true });
    assert.equal((await open(csv)).tick, 'sub/data.csv', 'a work folder still choosing ticks it too');
    answers[csv] = file({ mine: false }, { rel: 'sub/data.csv' });
    assert.equal((await open(csv)).error, 'Only the account that made the work folder “Paper” chooses what it tracks: ask it to track data.csv.');

    // one it tracks with no page yet: the server opens it in the vault's workspace, unless the vault is paused
    answers[tex] = file({ paused: true }, { rel: 'main.tex', tracked: true });
    assert.equal((await open(tex)).error, '“Paper” is paused: resume it in the sync panel, then open main.tex again.');
    assert.equal(opens(host).length, 0);
    answers[tex] = file({}, { rel: 'main.tex', tracked: true });
    assert.deepEqual(await open(tex), { path: tex, workspace: 'mine', page: 'opened' });
    const [opened] = opens(host);
    assert.deepEqual([opened.body, opened.headers['X-Gamma-Workspace'], opened.headers['X-Gamma-Shell']], [{ path: tex }, 'mine', shell.shellSecret]);
    assert.deepEqual(lastAt(view), { ws: 'mine', page: 'opened' });

    // a kept folder's file that is not its own: read where it is, as a lone file
    answers[tex] = file({ work: false }, { rel: 'main.tex' });
    assert.equal((await open(tex)).page, 'opened');
    assert.match(shell.notice(), /^main is read from this computer, under Opened files in Mine on Host\.$/);

    // a vault of another server: its page there; the host's dialog for a file it does not track; never a page here
    answers[tex] = file(remote, { rel: 'main.tex', page: 'rp', tracked: true });
    assert.deepEqual(await open(tex), { path: tex, workspace: 'team', page: 'rp' });
    assert.equal(view.loaded.at(-1), 'http://nas.lan:9001/?ws=team&page=rp');
    shell.pretendShown(host.srv, view, appWindow);
    answers[tex] = file(remote, { rel: 'main.tex' });
    assert.equal((await open(tex)).tick, 'main.tex');
    assert.deepEqual(lastAt(view), { ws: 'mine', track: 'v2', tick: 'main.tex' }, 'the host\'s own workspace');
    answers[tex] = file(remote, { rel: 'main.tex', tracked: true });
    assert.equal((await open(tex)).error, 'main.tex has not reached nas.lan:9001 yet: it opens there after the work folder\'s next round.');
    const reads = opens(host).length;
    answers[tex] = file({ ...remote, work: false }, { rel: 'main.tex' });
    assert.equal((await open(tex)).page, 'opened', 'a remote kept folder\'s file it does not push: a lone file');
    assert.equal(opens(host).length, reads + 1);
    assert.equal(nas.fake.calls.length, 0, 'nothing of it went to the NAS');

    // a vault of one file: its page
    answers[tex] = { is: 'file_vault', workspace: 'mine', page: 'fv' };
    assert.deepEqual(await open(tex), { path: tex, workspace: 'mine', page: 'fv' });
    assert.deepEqual(lastAt(view), { ws: 'mine', page: 'fv' });

    // another account's vault: read where it is, a Markdown file never asked about
    answers[md] = { is: 'hidden' };
    assert.equal((await open(md)).page, 'opened');
    assert.deepEqual(questions('Edit '), []);
  } finally {
    shell.pretendShown(null, null);
  }
});

test('a Markdown file outside every vault asks: Just view it reads it with how to edit it, Edit in Gamma makes a work folder of its repository with the file ticked, or of its directory', async () => {
  fresh();
  const host = server('Host');
  const repo = fs.mkdtempSync(path.join(dir, 'Repo-'));
  fs.mkdirSync(path.join(repo, '.git'));
  const md = put(path.join(repo, 'docs', 'notes.md'), '# Notes\n');
  const markdown = put(path.join(repo, 'docs', 'more.markdown'));
  const tex = put(path.join(repo, 'docs', 'main.tex'));
  const kept = put(path.join(repo, 'kept.md'));
  const loose = fs.mkdtempSync(path.join(dir, 'Loose-'));
  const lone = put(path.join(loose, 'lone.md'));
  const holding = fs.mkdtempSync(path.join(dir, 'Holding-'));
  fs.writeFileSync(path.join(holding, '.git'), 'gitdir: ../elsewhere\n'); // a worktree's
  const inner = put(path.join(holding, 'docs', 'inner.md'));
  const refused = fs.mkdtempSync(path.join(dir, 'Refused-'));
  fs.mkdirSync(path.join(refused, '.git'));
  const stray = put(path.join(refused, 'docs', 'stray.md'));
  const around = { is: 'around', vaults: [{ id: 'v9', workspace: 'mine', folder: 'f9', remote_url: '', work: true, choosing: false, mine: true, name: 'Lab', rel: 'lab' }] };
  const system = 'Pick a project directory: it is the operating system\'s.';
  const answers = {
    [kept]: inVault({}, { dir: false, rel: 'kept.md', page: 'pk', tracked: true }),
    [holding]: around,
    [refused]: around,
    [path.join(refused, 'docs')]: { is: 'none', dir: true, name: 'docs', refusal: system },
  };
  let choosing = true;
  const works = makesWorkFolders(host, () => choosing);
  whereOf(host, answers, (p, init) => (p === '/api/folder-links/open' && init.method === 'POST'
    ? { workspace: 'mine', page: `read-${path.basename(JSON.parse(init.body).path)}`, created: true, title: 'x', format: 'text' } : works(p, init)));
  const view = windowOn(host.fake);
  shell.pretendShown(host.srv, view, appWindow);
  const open = async (p) => (await shell.openWithGamma([p]))[0];
  const question = (name, at, repoToo) => `Edit ${name} in Gamma? ${name} is in no work folder. Edit in Gamma makes ${at} a work folder: Gamma writes a hidden .gamma folder there, asks which of its files to track, and saves your edits back to ${name}. Just view it opens the file read-only.${repoToo ? ` ${at} is the repository ${name} is in.` : ''}`;
  const HINT = ' To edit it as a note, open it with Gamma again and pick Edit in Gamma.';
  try {
    // asked, the harness answering Just view it: read where it is, the bar saying how to edit it
    assert.equal((await open(md)).page, 'read-notes.md');
    assert.deepEqual(questions('Edit '), [question('notes.md', repo, true)]);
    assert.ok(shell.notice().endsWith(HINT), shell.notice());
    assert.equal((await open(markdown)).page, 'read-more.markdown');
    assert.equal(questions('Edit ').length, 2, 'a .markdown is asked about too');
    // never for a .tex, a vault's own file, or (above) another account's
    await open(tex);
    assert.ok(!shell.notice().endsWith(HINT));
    assert.equal((await open(kept)).page, 'pk');
    assert.equal(questions('Edit ').length, 2);
    assert.equal(made(host).length, 0);

    // Edit in Gamma: its repository a work folder, the file ticked by its path from there
    shell.answers.push(0);
    const edit = await open(md);
    assert.deepEqual([edit.track, edit.tick], ['w1', 'docs/notes.md']);
    assert.equal(made(host)[0].body.path, repo);
    assert.deepEqual(lastAt(view), { ws: 'mine', folder: 'nf1', track: 'w1', tick: 'docs/notes.md' });
    assert.equal(shell.notice(), `“${path.basename(repo)}” is a work folder of Mine on Host now: choose the files it tracks.`);
    const reads = opens(host).length;

    // while it chooses its files: no question, nothing opened
    const asked = questions('Edit ').length;
    assert.deepEqual(await open(lone), { path: lone });
    assert.equal(shell.notice(), `Choose the files of “${path.basename(repo)}” first, then open lone.md again.`);
    assert.equal(questions('Edit ').length, asked);
    assert.equal(opens(host).length, reads);

    // saved: no repository, its own directory
    choosing = false;
    shell.answers.push(0);
    assert.deepEqual((await open(lone)).tick, 'lone.md');
    assert.equal(made(host)[1].body.path, loose);
    assert.equal(questions('Edit ').at(-1), question('lone.md', loose, false));

    // the repository (a worktree's .git file) around a vault elsewhere in it: the file's own directory
    choosing = false;
    shell.answers.push(0);
    assert.equal((await open(inner)).tick, 'inner.md');
    assert.equal(made(host)[2].body.path, path.join(holding, 'docs'));

    // neither can be one: no question, read where it is, the reason said
    const before = questions('Edit ').length;
    assert.equal((await open(stray)).page, 'read-stray.md');
    assert.equal(questions('Edit ').length, before);
    assert.ok(shell.notice().endsWith(` ${system}`), shell.notice());
    assert.ok(!shell.notice().includes(HINT));
    assert.equal(made(host).length, 3);

    // a Markdown file loose on the Desktop: asked, then asked about the Desktop, whose Cancel makes and reads nothing
    const desk = put(path.join(dir, 'desk.md'));
    const reading = opens(host).length;
    shell.answers.push(0);
    assert.deepEqual(await open(desk), { path: desk });
    assert.equal(questions('Make all of ').length, 1);
    assert.equal(made(host).length, 3);
    assert.equal(opens(host).length, reading, 'the person asked to edit it, not to read it');
  } finally {
    shell.pretendShown(null, null);
  }
});

test('the broad-place question: a directory that is the Desktop is asked about before it becomes a work folder, Cancel making nothing; a project inside Documents is not asked about', async () => {
  fresh();
  const host = server('Host');
  host.fake.routes = makesWorkFolders(host);
  shell.pretendShown(host.srv, windowOn(host.fake), appWindow);
  // (app.getPath answers `dir` for every place under the test: it is the Desktop, Documents and Downloads at once)
  process.env.GAMMA_SHELL_PICK_DIR = dir;
  try {
    assert.equal(await shell.addDirectoryVault(), null);
    assert.deepEqual(shell.asked, [`Make all of Desktop a work folder? ${dir} is your Desktop folder. Gamma would write a hidden .gamma folder there, read the folder while it runs, and ask which of its files to track. For one project, open that project's folder instead.`]);
    assert.equal(host.fake.calls.filter((c) => c.path === '/api/folder-links/preview').length, 0, 'not even looked at');
    assert.equal(made(host).length, 0);
    shell.answers.push(0);
    assert.equal((await shell.addDirectoryVault()).id, 'w1', 'made once the person says so');
    const project = fs.mkdtempSync(path.join(dir, 'paper-'));
    shell.asked.length = 0;
    assert.equal((await shell.addDirectoryVault(project)).id, 'w2');
    assert.deepEqual(questions('Make all of '), []);
    assert.equal(await shell.broadOk(project), true);
    // Sync with this computer… and Locate… need not make a work folder: their words say a synced vault
    shell.asked.length = 0;
    assert.equal(await shell.broadOk(dir, false), false);
    assert.deepEqual(shell.asked, [`Sync all of Desktop with Gamma? ${dir} is your Desktop folder. Gamma would write a hidden .gamma folder there and read the folder while it runs. For one project, pick that project's folder instead.`]);
  } finally {
    delete process.env.GAMMA_SHELL_PICK_DIR;
    shell.pretendShown(null, null);
  }
});

test('a file of another format read where it is: no lookup by its hash and no annotations for a .tex, the window on the NAS asking nothing there, and a shared workspace asked about first', async () => {
  fresh();
  const here = server('Here');
  const nas = server('NAS', [], { remote: 'http://nas.lan:9001' });
  here.fake.workspaces.push({ id: 'lab', name: 'Lab', role: 'owner', members: 1, access: 'private', mirror_of: 'Lab on NAS' },
    { id: 'team', name: 'Team', role: 'editor', members: 3, access: 'private', mirror_of: '' });
  const tex = put(path.join(dir, 'Sources', 'main.tex'));
  const md = put(path.join(dir, 'Sources', 'notes.md'));
  here.fake.routes = (p, init) => (p === '/api/folder-links/open' && init.method === 'POST'
    ? { workspace: init.headers['X-Gamma-Workspace'], page: 'read-here', created: true, title: 'main', format: 'text' } : null);
  const restore = stubStart();
  try {
    // in a clone: the host's own workspace, asked nothing by hash
    shell.pretendShown(here.srv, windowOn(here.fake, [], 'lab'), appWindow);
    assert.deepEqual(await shell.readFile(tex), { workspace: 'mine', page: 'read-here' });
    assert.ok(!here.fake.calls.some((c) => c.path.startsWith('/api/blocks/')), 'neither a lookup by hash nor a page read');
    assert.ok(!here.fake.calls.some((c) => c.path === '/api/import/pdf-annotations'), 'no annotations');
    assert.equal(shell.notice(), 'main is read from this computer, under Opened files in Mine on Here.');

    // the window on the NAS: read on this computer, nothing asked there
    shell.pretendShown(nas.srv, windowOn(here.fake, [{ origin: 'http://nas.lan:9001', fake: nas.fake }], 'team'), appWindow);
    assert.deepEqual(await shell.readFile(tex), { workspace: 'mine', page: 'read-here' });
    assert.equal(nas.fake.calls.length, 0);
    assert.equal(shell.current().id, here.srv.id);

    // a shared workspace: asked first, the account's own offered (the harness's answer)
    shell.pretendShown(here.srv, windowOn(here.fake, [], 'team'), appWindow);
    assert.deepEqual(await shell.readFile(md, { hint: true }), { workspace: 'mine', page: 'read-here' });
    assert.deepEqual(shell.asked, ['Open “notes.md” in Team? Team is shared: everyone in it can see the page and read the file from this computer while Gamma runs.']);
    assert.equal(shell.notice(), 'main is read from this computer, under Opened files in Mine on Here. To edit it as a note, open it with Gamma again and pick Edit in Gamma.');
  } finally {
    restore();
    shell.pretendShown(null, null);
  }
});

test('the queue goes on after a failure: each path handed over is opened in turn, the one that fails said in an error box', async () => {
  fresh();
  const host = server('Host');
  const theirs = fs.mkdtempSync(path.join(dir, 'Theirs-'));
  const pdf = put(path.join(dir, 'Queue', 'paper.pdf'), '%PDF-1.7');
  whereOf(host, { [theirs]: { is: 'hidden' }, [pdf]: { is: 'file_vault', workspace: 'mine', page: 'fv' } });
  const view = windowOn(host.fake);
  shell.pretendShown(host.srv, view, appWindow);
  try {
    const got = await shell.openWithGamma([theirs, path.join(dir, 'gone.pdf'), '--flag', pdf]);
    assert.deepEqual(got, [{ path: theirs, error: `${theirs} is kept by another account on this computer.` }, { path: pdf, workspace: 'mine', page: 'fv' }]);
    assert.deepEqual(shell.errorBoxes, [`Could not open ${path.basename(theirs)} in Gamma. ${theirs} is kept by another account on this computer.`]);
    assert.deepEqual(lastAt(view), { ws: 'mine', page: 'fv' });
  } finally {
    shell.pretendShown(null, null);
  }
});

test('a start that names a file or a folder never goes hidden into the tray, a login start with the background on does', () => {
  fresh();
  registry.setSettings({ background: true });
  process.argv.push('--hidden');
  try {
    assert.equal(shell.bootHidden([]), true);
    assert.equal(shell.bootHidden([dir]), false, 'a folder handed over shows the window');
  } finally {
    process.argv.pop();
    registry.setSettings({ background: false });
  }
});

// ------------------------------------------------ addresses and certificates -----

test('every token minted on another server is asked of the host first, every address sent comes from remoteOf, and the shell follows no redirect', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8');
  const fns = new Map(src.split(/\n(?=(?:async )?function \w+\()/).map((f) => [(/function (\w+)/.exec(f) || [])[1], f]));
  const minting = [...fns].filter(([, f]) => /'\/api\/integrations\/tokens', \{\s*method: 'POST'/.test(f)).map(([name]) => name);
  assert.deepEqual(minting.sort(), ['addFileVault', 'addFolderVault', 'changePush', 'keepOffline', 'makeRemoteWorkFolder', 'showComputer']);
  for (const name of minting) {
    const f = fns.get(name);
    assert.ok(f.includes('beforeMint(') && f.indexOf('beforeMint(') < f.indexOf('\'/api/integrations/tokens\', {'), `${name} asks the host before it mints`);
  }
  // the other two are handed the origin by these
  for (const name of ['keepOffline', 'addFolderVault', 'addFileVault', 'changePush', 'showComputerOf']) assert.match(fns.get(name), /remoteOf\(/, name);
  assert.match(fns.get('serverApi'), /redirect: 'error'/);
});

test('a token for an address over plain http is asked of the host first: refused for the internet, asked once per origin of this network, every time for a link on the internet that exists', async () => {
  fresh();
  const host = server('Host');
  const nas = server('NAS', [], { remote: 'http://nas.lan:9001' });
  host.fake.vaults = [vaultLink({ workspace_id: 'team', remote_url: 'http://nas.lan:9001', plain: 'lan', token_id: 'old' })];
  let check = { origin: 'http://nas.lan:9001', scope: 'lan', plain: true, existing: false, unsure: false, refused: '', warning: 'Clear text.' };
  computerRoutes(host, nas);
  const routes = host.fake.routes;
  host.fake.routes = (p, init) => {
    if (p === '/api/remote-servers/check') return check;
    if (p === '/api/folder-links/v1' && init.method === 'PATCH') return vaultLink({ ...host.fake.vaults[0], token_id: JSON.parse(init.body).token_id });
    return routes(p, init);
  };
  electron.session.defaultSession.fetch = async (url, init = {}) => nas.fake.answer(new URL(url).pathname, init, new URL(url));
  const minted = () => nas.fake.calls.filter((c) => c.path === '/api/integrations/tokens' && c.method === 'POST').length;
  const push = () => shell.setPush(host.srv.id, 'v1', 'pdfs');
  try {
    await assert.rejects(push(), (e) => e.quiet === true && e.message === 'Nothing was sent to nas.lan over http://.');
    assert.equal(shell.asked.at(-1), 'Connect to nas.lan without encryption? Clear text.');
    assert.equal(minted(), 0, 'Cancel mints nothing');
    assert.deepEqual(host.fake.calls.filter((c) => c.path === '/api/remote-servers/check').at(-1).body, { url: 'http://nas.lan:9001', vault: 'v1' }, 'the renewed vault named, as the route that takes the token counts it');

    shell.answers.push(0);
    await push();
    assert.equal(minted(), 1);
    assert.equal(registry.plainAccepted('http://nas.lan:9001'), true);
    let asks = shell.asked.length;
    await push();
    assert.deepEqual([shell.asked.length, minted()], [asks, 2], 'asked once per origin');

    // a new link to the internet over http: the host's refusal, nothing minted
    check = { ...check, scope: 'far', refused: 'nas.lan is on the internet and this address is http://.' };
    await assert.rejects(push(), /^Error: nas\.lan is on the internet and this address is http:\/\/\.$/);
    assert.equal(minted(), 2);
    // a link to the internet that exists: asked each time
    check = { ...check, refused: '', existing: true, warning: 'In clear.' };
    shell.answers.push(0, 0);
    await push();
    await push();
    assert.deepEqual(shell.asked.slice(-2), ['Connect to nas.lan without encryption? In clear.', 'Connect to nas.lan without encryption? In clear.']);
    // a link of this network made before: nothing asked, the origin taken as agreed
    registry.setPlainAccepted('http://nas.lan:9001', false);
    check = { ...check, scope: 'lan', existing: true, unsure: true };
    asks = shell.asked.length;
    await push();
    assert.equal(shell.asked.length, asks);
    assert.equal(registry.plainAccepted('http://nas.lan:9001'), true);
    // https: nothing asked
    check = { ...check, plain: false, existing: false };
    registry.setPlainAccepted('http://nas.lan:9001', false);
    await push();
    assert.equal(shell.asked.length, asks);
  } finally {
    delete electron.session.defaultSession.fetch;
  }
});

// The host's move route (POST /api/remote-servers/move): its dry run `plan`,
// its `answer` to the move, and its `calls`; the window's session records
// what is revoked at the new address (`revoked`).
function moveRoutes(host, plan) {
  const state = { plan, answer: { moved: plan.moved, revoke: [] }, revoked: [] };
  const routes = host.fake.routes;
  host.fake.routes = (p, init) => {
    if (p !== '/api/remote-servers/move') return routes(p, init);
    return JSON.parse(init.body).dry_run ? state.plan : state.answer;
  };
  electron.session.defaultSession.fetch = async (url, init = {}) => {
    state.revoked.push(`${init.method} ${url} ${(init.headers || {})['X-Gamma-Workspace']} ${init.redirect}`);
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  state.calls = () => host.fake.calls.filter((c) => c.path === '/api/remote-servers/move');
  return state;
}

test('a server\'s address changes with its links: a dry run first, asked when it moves any, then moved on the host with the shell\'s word, the tokens it replaced revoked there; a load of the old address goes to the new one', async () => {
  fresh();
  const host = server('Host');
  const nas = registry.addRemote('NAS', 'http://nas.lan:9001');
  const realStart = sidecar.start;
  sidecar.start = async () => ({ url: 'http://127.0.0.1:50999', port: 50999 });
  const moves = moveRoutes(host, { kind: 'upgrade', plain: false, moved: { vaults: 0, computers: 0, mirrors: 0 } });
  try {
    // no links: the address changes, nothing asked, nothing sent anywhere
    await shell.moveServer(nas.id, 'https://nas.lan:5001');
    const [dry] = moves.calls();
    assert.deepEqual(dry.body, { from: 'http://nas.lan:9001', to: 'https://nas.lan:5001', dry_run: true });
    assert.equal(dry.headers['X-Gamma-Shell'], shell.shellSecret, 'the shell\'s word');
    assert.equal(moves.calls().length, 1);
    assert.deepEqual(shell.asked, []);
    assert.equal(registry.get(nas.id).url, 'https://nas.lan:5001');
    assert.equal(shell.rerouted('http://nas.lan:9001/?ws=w1&page=p1#b'), 'https://nas.lan:5001/?ws=w1&page=p1#b');
    assert.equal(shell.rerouted('https://nas.lan:5001/?ws=w1'), null);

    // links to move, over plain http: asked, Cancel changes nothing
    moves.plan = { kind: 'downgrade', plain: true, moved: { vaults: 2, computers: 1, mirrors: 1 } };
    assert.equal(await shell.moveServer(nas.id, 'http://192.168.1.5:9001'), null);
    assert.equal(shell.asked.at(-1), 'Move the links of https://nas.lan:5001 to http://192.168.1.5:9001? Gamma will send the tokens of 2 synced vaults, this computer and 1 offline copy to http://192.168.1.5:9001, and use http://192.168.1.5:9001 from now on. Only continue if http://192.168.1.5:9001 is the same server. http://192.168.1.5:9001 is http://: the tokens will travel without encryption, and someone on your network could read them.');
    assert.equal(moves.calls().length, 2, 'only the dry run');
    assert.equal(registry.get(nas.id).url, 'https://nas.lan:5001');

    // agreed: moved, plain http allowed, the replaced link's token revoked at the new address
    moves.answer = { moved: moves.plan.moved, revoke: [{ workspace_id: 'team', token_id: 't9' }] };
    shell.answers.push(0);
    await shell.moveServer(nas.id, 'http://192.168.1.5:9001');
    assert.deepEqual(moves.calls().at(-1).body, { from: 'https://nas.lan:5001', to: 'http://192.168.1.5:9001', plain_ok: true });
    assert.deepEqual(moves.revoked, ['DELETE http://192.168.1.5:9001/api/integrations/tokens/t9 team error']);
    assert.equal(registry.get(nas.id).url, 'http://192.168.1.5:9001');
    assert.equal(shell.notice(), 'https://nas.lan:5001 is now http://192.168.1.5:9001: 2 synced vaults, this computer and 1 offline copy follow it.');

    // refused there: the host's words, nothing changed here
    moves.answer = { failWith: 409, body: { detail: 'https://nas.example.com refused the token of the synced vault “main”.' } };
    shell.answers.push(0);
    await assert.rejects(shell.moveServer(nas.id, 'https://nas.example.com'), /refused the token of the synced vault “main”/);
    assert.equal(registry.get(nas.id).url, 'http://192.168.1.5:9001');
    const calls = moves.calls().length;
    assert.equal((await shell.moveServer(nas.id, 'http://192.168.1.5:9001/')).url, 'http://192.168.1.5:9001');
    assert.equal(moves.calls().length, calls, 'the same address asks nothing of anyone');
  } finally {
    sidecar.start = realStart;
    delete electron.session.defaultSession.fetch;
  }
});

test('a redirect to the same server over https moves its links by itself, once; a move that fails leaves the row offering it', async () => {
  fresh();
  shell.upgrades.clear();
  const host = server('Host');
  const nas = registry.addRemote('NAS', 'http://nas.lan:9001');
  const redirected = (url, to) => ({ last_error: `${url} now redirects to ${to}`, error_kind: 'redirected', move_to: to, upgrade: true });
  host.fake.vaults = [vaultLink({ workspace_id: 'team', remote_url: 'http://nas.lan:9001', status: redirected('http://nas.lan:9001', 'https://nas.lan:5001') })];
  const realStart = sidecar.start;
  sidecar.start = async () => ({ url: 'http://127.0.0.1:50999', port: 50999 });
  const moves = moveRoutes(host, { kind: 'upgrade', plain: false, moved: { vaults: 1, computers: 0, mirrors: 0 } });
  try {
    await shell.refreshKeeping();
    assert.deepEqual([shell.keeping().items[0].text.replace(/ · .*$/, ''), shell.keeping().items[0].fix], ['Moved to https', undefined]);
    await until(() => registry.get(nas.id).url === 'https://nas.lan:5001', 'moved by itself');
    assert.deepEqual(shell.asked, [], 'nobody asked');
    assert.deepEqual(moves.calls().map((c) => c.body), [{ from: 'http://nas.lan:9001', to: 'https://nas.lan:5001', dry_run: true },
      { from: 'http://nas.lan:9001', to: 'https://nas.lan:5001', plain_ok: false }]);
    await until(() => shell.notice() === 'http://nas.lan:9001 is now https://nas.lan:5001: 1 synced vault follows it.', 'said');

    // another server's redirect whose move the host refuses: its row offers it, asked then
    const other = registry.addRemote('Lab', 'http://lab.lan');
    host.fake.vaults = [vaultLink({ id: 'v2', workspace_id: 'lab', remote_url: 'http://lab.lan', status: redirected('http://lab.lan', 'https://lab.lan') })];
    moves.answer = { failWith: 502, body: { detail: 'https://lab.lan is not trusted' } };
    await shell.refreshKeeping();
    await until(() => shell.upgrades.get('http://lab.lan') === 'failed', 'the move failed');
    await shell.refreshKeeping();
    const [item] = shell.keeping().items;
    assert.deepEqual(item.fix, { action: 'use-https', button: 'Use https://lab.lan' });
    assert.equal(registry.get(other.id).url, 'http://lab.lan');
    assert.equal(moves.calls().filter((c) => c.body.from === 'http://lab.lan' && !c.body.dry_run).length, 1, 'tried once a session');
    moves.answer = { moved: { vaults: 1 }, revoke: [] };
    shell.answers.push(0);
    await shell.keepingAction('vault', host.srv.id, 'v2', 'use-https');
    assert.match(shell.asked.at(-1), /^Move the links of http:\/\/lab\.lan to https:\/\/lab\.lan\?/);
    assert.equal(registry.get(other.id).url, 'https://lab.lan');
  } finally {
    sidecar.start = realStart;
    delete electron.session.defaultSession.fetch;
  }
});

test('a certificate is trusted only as the launcher\'s card showed it: its record taken once, a stale card shown again, a new one asked first, and the local servers handed the pins', async () => {
  fresh();
  const host = server('Host');
  const pushed = [];
  host.fake.routes = (p, init) => (p === '/api/remote-servers/pins' && init.method === 'PUT' ? (pushed.push(JSON.parse(init.body).pins), { pins: [] }) : null);
  const nas = registry.addRemote('NAS', 'https://nas.lan:5001');
  const view = windowOn(host.fake);
  shell.pretendShown(null, view, { ...appWindow, setTitle: noop });
  const served = (file) => ({ data: fs.readFileSync(path.join(__dirname, file), 'utf8'), subjectName: 'nas.lan', issuerName: 'nas.lan',
    validStart: 1767225600, validExpiry: 2082758400 });
  const FAILED = 'ERR_CERT_AUTHORITY_INVALID (-202) loading \'https://nas.lan:5001/\'';
  const card = () => JSON.parse(new URL(view.loaded.at(-1)).searchParams.get('error'));
  try {
    shell.certificateRefused('https://nas.lan:5001/?ws=w1', 'net::ERR_CERT_AUTHORITY_INVALID', served('nas.pem'));
    shell.certificateRefused('https://elsewhere.lan/', 'net::ERR_CERT_AUTHORITY_INVALID', served('nas.pem'));
    assert.equal(shell.certRecord('https://elsewhere.lan'), null, 'only a registered server\'s');
    shell.loadLauncher(new Error(FAILED), nas.id);
    const first = card();
    assert.deepEqual([first.action, first.summary, first.cert.sha256, first.server],
      ['trust', 'nas.lan\'s certificate is not from an authority this computer trusts.', 'ce48e27366a4bb077be700fc1bcf08f81392943ca3767b6a0908ce3f9d73e26c', nas.id]);
    assert.match(first.cert.grouped, /^CE:48:E2/);

    // another nonce than the card's: nothing pinned, the card again with the record there is now
    assert.equal(await shell.trustCert(nas.id, 'not-the-card', first.cert.sha256), false);
    assert.deepEqual(registry.getPins(), {});
    const again = card();
    assert.equal(again.stale, 'The certificate changed while you were looking at it.');
    assert.notEqual(again.nonce, first.nonce);
    assert.equal(await shell.trustCert(nas.id, again.nonce, 'b'.repeat(64)), false, 'another fingerprint than the card\'s');

    const shown = card();
    assert.equal(await shell.trustCert(nas.id, shown.nonce, shown.cert.sha256), true);
    assert.equal(registry.getPins()['https://nas.lan:5001'].sha256, shown.cert.sha256);
    assert.deepEqual(pushed.at(-1), [{ origin: 'https://nas.lan:5001', sha256: shown.cert.sha256, subject: 'nas.lan', issuer: 'nas.lan',
      not_before: '2026-01-01T00:00:00.000Z', not_after: '2036-01-01T00:00:00.000Z' }]);
    assert.equal(view.loaded.at(-1), 'https://nas.lan:5001', 'the server opens');
    assert.equal(await shell.trustCert(nas.id, shown.nonce, shown.cert.sha256), false, 'a record is taken once');

    // the verify proc accepts the pinned one for any port of its host, and only for a failure a pin answers
    const proc = (hostname, verificationResult, file = 'nas.pem') => {
      let answer;
      shell.verifyProc({ hostname, verificationResult, certificate: served(file) }, (a) => { answer = a; });
      return answer;
    };
    assert.equal(proc('nas.lan', 'net::ERR_CERT_AUTHORITY_INVALID'), 0);
    assert.equal(proc('nas.lan', 'net::ERR_CERT_REVOKED'), -3);
    assert.equal(proc('nas.lan', 'net::ERR_CERT_AUTHORITY_INVALID', 'nas-renewed.pem'), -3);
    assert.equal(proc('other.lan', 'net::OK', 'nas-renewed.pem'), -3);
    assert.equal(shell.accepted.get('other.lan'), 'd8a7da64d061189b7239c44933b04b99b6dae7b2aeaddc6ccd1fa50d7b22e795', 'what the window accepted, by host');
    // a refusal the network service kept from before the pin is accepted in certificate-error, by the same rule
    const refused = (url, error, file) => {
      const event = { prevented: false, preventDefault() { this.prevented = true; } };
      let allowed = null;
      shell.certificateError(event, url, error, served(file), (ok) => { allowed = ok; }, true);
      return [event.prevented, allowed];
    };
    assert.deepEqual(refused('https://nas.lan:9443/x', 'net::ERR_CERT_AUTHORITY_INVALID', 'nas.pem'), [true, true], 'any port of the pinned host');
    assert.deepEqual(refused('https://nas.lan:5001/', 'net::ERR_CERT_REVOKED', 'nas.pem'), [false, null]);
    assert.deepEqual(refused('https://nas.lan:5001/', 'net::ERR_CERT_AUTHORITY_INVALID', 'nas-renewed.pem'), [false, null]);

    // a renewed certificate: changed, trusted again only once asked
    shell.certificateRefused('https://nas.lan:5001/', 'net::ERR_CERT_AUTHORITY_INVALID', served('nas-renewed.pem'));
    shell.loadLauncher(new Error(FAILED), nas.id);
    const changed = card();
    assert.deepEqual([changed.action, changed.summary], ['retrust', 'The certificate of nas.lan changed.']);
    assert.equal(await shell.trustCert(nas.id, changed.nonce, changed.cert.sha256), false, 'Cancel');
    assert.match(shell.asked.at(-1), /^Trust the new certificate of nas\.lan\? Only if you replaced it on nas\.lan yourself\. Fingerprint: D8:A7:/);
    assert.equal(registry.getPins()['https://nas.lan:5001'].sha256, shown.cert.sha256);
    // the card stays, and its button asks again rather than saying the certificate changed meanwhile
    const asks = shell.asked.length;
    const loads = view.loaded.length;
    assert.equal(await shell.trustCert(nas.id, changed.nonce, changed.cert.sha256), false, 'Cancel again');
    assert.deepEqual([shell.asked.length, view.loaded.length], [asks + 1, loads], 'asked again, no stale card');
    assert.equal(registry.getPins()['https://nas.lan:5001'].sha256, shown.cert.sha256);

    // forgotten: no pin, and the servers told
    assert.equal(await shell.forgetCert(nas.id), true);
    assert.deepEqual([registry.getPins(), pushed.at(-1)], [{}, []]);
  } finally {
    shell.pretendShown(null, null);
  }
});

// A certificate as Electron hands it over (test/nas.pem, nas-renewed.pem).
const servedCert = (file) => ({ data: fs.readFileSync(path.join(__dirname, file), 'utf8'), subjectName: 'nas.lan', issuerName: 'nas.lan',
  validStart: 1767225600, validExpiry: 2082758400 });
const NAS_DIGEST = 'ce48e27366a4bb077be700fc1bcf08f81392943ca3767b6a0908ce3f9d73e26c';
const procSays = (hostname, verificationResult, file = 'nas.pem') => {
  let answer;
  shell.verifyProc({ hostname, verificationResult, certificate: servedCert(file) }, (a) => { answer = a; });
  return answer;
};

// The pins each local server is handed (PUT /api/remote-servers/pins), recorded beside the routes it had.
function pinsPushed(host) {
  const pushed = [];
  const routes = host.fake.routes;
  host.fake.routes = (p, init) => (p === '/api/remote-servers/pins' && init.method === 'PUT'
    ? (pushed.push(JSON.parse(init.body).pins.map((r) => r.origin)), { pins: [] }) : routes(p, init));
  return pushed;
}

test('removing a remote drops its pin at once: the verify proc refuses the certificate and the local servers are told', async () => {
  fresh();
  const host = server('Host');
  const pushed = pinsPushed(host);
  const nas = registry.addRemote('NAS', 'https://nas.lan:5001');
  registry.setPin('https://nas.lan:5001', { sha256: NAS_DIGEST });
  await shell.pinsChanged();
  assert.equal(procSays('nas.lan', 'net::ERR_CERT_AUTHORITY_INVALID'), 0);
  await shell.removeServer(nas.id);
  assert.deepEqual(registry.getPins(), {});
  assert.equal(procSays('nas.lan', 'net::ERR_CERT_AUTHORITY_INVALID'), -3, 'refused at once, not at the next start');
  assert.deepEqual(pushed.at(-1), []);
});

test('links move to an https:// address only once its certificate verifies or is pinned there: another host\'s asked with its fingerprint, the same host\'s pin carried, a pin made for a move that did not happen dropped', async () => {
  fresh();
  const host = server('Host');
  const nas = registry.addRemote('NAS', 'https://192.168.1.10:5001');
  const realStart = sidecar.start;
  sidecar.start = async () => ({ url: 'http://127.0.0.1:50999', port: 50999 });
  const moves = moveRoutes(host, { kind: 'other', plain: false, moved: { vaults: 1, computers: 0, mirrors: 0 } });
  const pushed = pinsPushed(host);
  // the window's session: /api/health at the new address answers as the verify proc does
  const healthAt = [];
  electron.session.defaultSession.fetch = async (url) => {
    const u = new URL(url);
    if (u.pathname === '/api/health') healthAt.push(u.origin);
    if (u.pathname === '/api/health' && procSays(u.hostname, 'net::ERR_CERT_AUTHORITY_INVALID') !== 0) {
      throw new TypeError('fetch failed', { cause: new Error('net::ERR_CERT_AUTHORITY_INVALID') });
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  const moveCalls = () => moves.calls().filter((c) => !c.body.dry_run).length;
  try {
    // another address (DHCP gave the NAS .11): its certificate asked first, Cancel moves nothing
    assert.equal(await shell.moveServer(nas.id, 'https://192.168.1.11:5001'), null);
    assert.match(shell.asked.at(-1), /^Trust the certificate of 192\.168\.1\.11\? 192\.168\.1\.11's certificate is not from an authority this computer trusts\. .* Fingerprint: CE:48:E2/);
    assert.deepEqual([registry.getPins(), moveCalls(), registry.get(nas.id).url], [{}, 0, 'https://192.168.1.10:5001']);

    // trusted, then the move agreed: pinned for the new address and handed to the host before the move
    shell.answers.push(0, 0);
    await shell.moveServer(nas.id, 'https://192.168.1.11:5001');
    assert.equal(registry.getPins()['https://192.168.1.11:5001'].sha256, NAS_DIGEST);
    assert.ok(pushed.some((list) => list.includes('https://192.168.1.11:5001')));
    assert.deepEqual([moveCalls(), registry.get(nas.id).url], [1, 'https://192.168.1.11:5001']);

    // another port of the same host: the window takes it by that pin, so it is pinned there too, nothing asked of it
    moves.answer = { failWith: 502, body: { detail: 'https://192.168.1.11 could not be reached.' } };
    shell.answers.push(0);
    await assert.rejects(shell.moveServer(nas.id, 'https://192.168.1.11'), /could not be reached/);
    assert.doesNotMatch(shell.asked.at(-1), /^Trust the certificate/);
    assert.deepEqual(Object.keys(registry.getPins()), ['https://192.168.1.11:5001'], 'the move failed: its pin dropped again');
    moves.answer = { moved: moves.plan.moved, revoke: [] };
    shell.answers.push(0);
    await shell.moveServer(nas.id, 'https://192.168.1.11');
    assert.deepEqual(Object.keys(registry.getPins()), ['https://192.168.1.11'], 'the pin moved with the address');
    assert.deepEqual(pushed.at(-1), ['https://192.168.1.11']);

    // to plain http on another host: nothing fetched, the old address's pin goes, the servers told
    const fetched = healthAt.length;
    moves.plan = { kind: 'downgrade', plain: true, moved: { vaults: 1, computers: 0, mirrors: 0 } };
    shell.answers.push(0);
    await shell.moveServer(nas.id, 'http://192.168.1.12:9001');
    assert.deepEqual([healthAt.length, registry.getPins(), pushed.at(-1)], [fetched, {}, []]);
    assert.equal(procSays('192.168.1.11', 'net::ERR_CERT_AUTHORITY_INVALID'), -3, 'the verify proc no longer takes it');
  } finally {
    sidecar.start = realStart;
    delete electron.session.defaultSession.fetch;
  }
});

test('a redirect\'s links move alone when its https:// address is listed already: with or without an entry at the old address', async () => {
  const redirected = { last_error: 'http://nas.lan:9001 now redirects to https://nas.lan:5001', error_kind: 'redirected', move_to: 'https://nas.lan:5001', upgrade: true };
  const realStart = sidecar.start;
  sidecar.start = async () => ({ url: 'http://127.0.0.1:50999', port: 50999 });
  try {
    for (const both of [false, true]) {
      fresh();
      shell.upgrades.clear();
      const host = server('Host');
      const secure = registry.addRemote('NAS', 'https://nas.lan:5001');
      const old = both ? registry.addRemote('Old NAS', 'http://nas.lan:9001') : null;
      host.fake.vaults = [vaultLink({ workspace_id: 'team', remote_url: 'http://nas.lan:9001', status: redirected })];
      const moves = moveRoutes(host, { kind: 'upgrade', plain: false, moved: { vaults: 1, computers: 0, mirrors: 0 } });
      await shell.refreshKeeping();
      await until(() => shell.upgrades.get('http://nas.lan:9001') !== 'running', 'the move ran');
      assert.equal(shell.upgrades.get('http://nas.lan:9001'), 'done', `moved (${both ? 'both entries' : 'no entry at the old address'})`);
      assert.deepEqual(moves.calls().at(-1).body, { from: 'http://nas.lan:9001', to: 'https://nas.lan:5001', plain_ok: false });
      assert.equal(registry.get(secure.id).url, 'https://nas.lan:5001');
      if (old) assert.equal(registry.get(old.id).url, 'http://nas.lan:9001', 'no entry renamed onto another');
    }
  } finally {
    sidecar.start = realStart;
    delete electron.session.defaultSession.fetch;
  }
});

test('an http:// address that sends the window to https:// on its own host is followed nowhere, and the launcher offers that address', () => {
  fresh();
  const host = server('Host');
  const nas = registry.addRemote('NAS', 'http://nas.lan:5000');
  const view = windowOn(host.fake);
  shell.pretendShown(null, view, { ...appWindow, setTitle: noop });
  const card = () => JSON.parse(new URL(view.loaded.at(-1)).searchParams.get('error'));
  try {
    assert.equal(shell.sentToHttpsBy('https://nas.lan:5001/'), false, 'only an address the window was let to');
    shell.allowedOrigins.add('http://nas.lan:5000');
    assert.equal(shell.sentToHttpsBy('http://nas.lan:5001/'), false, 'not https');
    assert.equal(shell.sentToHttpsBy('https://elsewhere.lan/'), false, 'another host');
    assert.equal(shell.sentToHttpsBy('https://nas.lan:5001/?ws=w1'), true);
    shell.loadLauncher(new Error('No page after loading \'http://nas.lan:5000\''), nas.id);
    assert.deepEqual([card().action, card().to, card().summary], ['https', 'https://nas.lan:5001', 'nas.lan now redirects to https://nas.lan:5001.']);
    shell.loadLauncher(new Error('ERR_CONNECTION_REFUSED (-102)'), nas.id);
    assert.equal(card().action, 'retry', 'taken once');
    // HSTS keeps the port: the name's own https:// is offered
    shell.sentToHttpsBy('https://nas.lan:5000/');
    shell.loadLauncher(new Error('No page'), nas.id);
    assert.deepEqual([card().summary, card().to], ['This name is HTTPS-only in this window.', 'https://nas.lan']);
    // the content view's will-redirect guard is what records it, and then opens nothing in the browser
    const src = fs.readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8');
    assert.match(src, /cwc\.on\('will-redirect', \(event, url\) => guard\(event, url, true\)\)/);
    assert.match(src, /if \(redirect && event\.isMainFrame !== false && sentToHttpsBy\(url\)\) return;\r?\n\s+if \(\/\^https\?:\/\.test\(url\)/);
  } finally {
    shell.allowedOrigins.delete('http://nas.lan:5000');
    shell.pretendShown(null, null);
  }
});
