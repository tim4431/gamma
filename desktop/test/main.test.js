// main.js under plain Node, Electron replaced by a stand-in and each local
// server by a fake answering the shell's API calls (the GAMMA_SHELL_TEST
// hooks reach the shell's internals): what a keeping reading records about
// the Zotero links (refreshKeeping -> registry.setZoteroHost, the servers
// startBackgroundHosts starts at launch), a resume made while a reading
// runs, one start per server however many ask, and a kept folder's sync
// followed to the end of its round. `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('module');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { pages: shellPages, trustedShellSender } = require('../lib/shellSecurity');

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
      fetch: async (url, init = {}) => fakes.get(name.replace(/^keeping-/, '')).answer(new URL(url).pathname, init, new URL(url).search),
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

// A local server holding `links`. `zotero` is how its link list answers:
// 'list', 404 (a server from before the Zotero links) or 'fail' (no
// answer); `broken`, its list of clones fails. The list is read as the
// links are when it is asked for, then `gate()` may hold the answer back.
function server(name, links = [], { zotero = 'list', running: on = true, broken = false } = {}) {
  const srv = registry.addLocal(name);
  const ok = (body) => ({ ok: true, status: 200, json: async () => body });
  const fails = (code) => ({ ok: false, status: code, json: async () => ({ detail: `HTTP ${code}` }) });
  const fake = {
    links, zotero, reads: 0, gate: async () => {},
    async answer(p, init) {
      if (p === '/api/session') return ok({ user: { id: 'admin', username: 'admin' } });
      if (p === '/api/mirrors') return broken ? fails(500) : ok({ mirrors: [] });
      if (p === '/api/folder-links') return ok({ links: [] });
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
  if (on) running.add(srv.id);
  return { srv, fake, recorded: () => Boolean(registry.get(srv.id).zoteroHost) };
}

function fresh() {
  fs.rmSync(path.join(dir, 'servers.json'), { force: true });
  fakes.clear();
  running.clear();
}

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

test('a kept folder synced from the panel is followed to the end of its round, then the bar says how it went', async () => {
  fresh();
  const { srv, fake } = server('Folders');
  const kept = { id: 'fl1', kind: 'folder', folder_id: 'f1', workspace_id: 'w1', dest: path.join(dir, 'Kept'), remote_url: '',
    status: { last_sync: '2026-10-29T10:00:00Z', last_error: '', counts: {}, kept: [], folder_path: ['Kept lab'] } };
  const ok = (body) => ({ ok: true, status: 200, json: async () => body });
  // a round 15 minutes in when it is asked for (a long first fill): followed all the same, until its running comes down
  const startedAt = Date.now() / 1000 - 900;
  const asked = [];
  const reads = [{ running: true, started_at: startedAt, progress: {} }, { running: true, started_at: startedAt, progress: {} },
    { running: false, last_sync: '2026-11-06T10:00:00Z', last_error: '', counts: { added: 2, unchanged: 5 }, kept: [] }];
  const answer = fake.answer;
  fake.answer = async (p, init, search) => {
    if (p === '/api/folder-links') return ok({ links: [kept] });
    if (p === '/api/folder-links/fl1/sync') {
      asked.push(`${init.method} ${search}`);
      return ok({ ...kept, status: { ...kept.status, running: true, started_at: startedAt } });
    }
    if (p === '/api/folder-links/fl1') return ok({ ...kept, status: reads.shift() });
    return answer(p, init, search);
  };
  // the next read of the link, a second on (the shell's timers of a second or more wait for the test)
  const next = () => timers.find((t) => !t.cleared && t.ms === 1000);
  const step = async () => {
    await until(() => next(), 'the link is read again a second on');
    const timer = next();
    timer.cleared = true;
    timer.fn();
  };

  const said = shell.notice();
  const synced = shell.syncFolder(srv.id, 'fl1', false);
  await step();
  await step();
  assert.equal(shell.notice(), said, 'nothing said while the round still runs');
  await step();
  const after = await synced;
  assert.deepEqual(asked, ['POST '], 'the round is asked for, no request held open for it');
  assert.equal(after.status.last_sync, '2026-11-06T10:00:00Z', 'the answer is the link as the round left it');
  assert.equal(shell.notice(), '“Kept lab” is up to date: 2 added.');
  assert.equal(reads.length, 0, 'read until its running came down, and no more');

  // forced: the force asked for; a round already over in the answer is not read again
  fake.answer = async (p, init, search) => {
    if (p === '/api/folder-links/fl1/sync') {
      asked.push(`${init.method} ${search}`);
      return ok({ ...kept, status: { ...kept.status, running: false, last_error: 'the folder is gone' } });
    }
    if (p === '/api/folder-links/fl1') assert.fail('not read again');
    return p === '/api/folder-links' ? ok({ links: [kept] }) : answer(p, init, search);
  };
  await shell.syncFolder(srv.id, 'fl1', true);
  assert.deepEqual(asked, ['POST ', 'POST ?force=1']);
  assert.equal(shell.notice(), '“Kept lab”: the folder is gone');
});
