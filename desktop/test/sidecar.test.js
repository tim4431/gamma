// The port a local server starts on (lib/sidecar.js pickPort): the one it
// had last time while that is free, else a new one from the dynamic range
// that no other server has remembered. And a server started before is the
// one used while its process runs (stillUp), so a window opened again never
// starts a second one. And which backend runs (resolveBackend), and that a
// quit (stopAll) stops a server still starting. `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const http = require('http');
const net = require('net');
const os = require('os');
const path = require('path');
const { pickPort, stillUp, resolveBackend, start, stopAll } = require('../lib/sidecar');

const listen = (port = 0) => new Promise((resolve, reject) => {
  const srv = net.createServer();
  srv.once('error', reject);
  srv.listen(port, '127.0.0.1', () => resolve(srv));
});
const close = (srv) => new Promise((resolve) => srv.close(resolve));
const dynamic = (p) => Number.isInteger(p) && p >= 49152 && p <= 65535;

test('a free remembered port is kept', async () => {
  const srv = await listen();
  const port = srv.address().port;
  await close(srv);
  assert.equal(await pickPort({ port }, []), port);
});

test('a remembered port another program holds is given up for a dynamic one', async () => {
  const srv = await listen();
  const port = srv.address().port;
  try {
    const got = await pickPort({ port }, []);
    assert.notEqual(got, port);
    assert(dynamic(got), `in the dynamic range: ${got}`);
  } finally {
    await close(srv);
  }
});

test('a new server gets a dynamic port, never one another server remembered', async () => {
  for (let i = 0; i < 20; i++) assert(dynamic(await pickPort({}, [])));
  const avoid = Array.from({ length: 16384 - 8 }, (_, i) => 49152 + i); // all but the top eight
  for (let i = 0; i < 5; i++) {
    const got = await pickPort({ port: 'nonsense' }, avoid);
    assert(!avoid.includes(got), `not remembered by another: ${got}`);
  }
});

test('a server started before is used while its process runs: waited for while it does not answer, given up once it has exited or the time is up', async () => {
  let asked = 0;
  let okFrom = 3; // the health check that answers first
  const srv = http.createServer((_req, res) => {
    asked += 1;
    res.statusCode = asked >= okFrom ? 200 : 500;
    res.end('{}');
  });
  await new Promise((resolve) => srv.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${srv.address().port}`;
  const runs = { exitCode: null, signalCode: null };
  try {
    assert.equal(await stillUp({ url, child: runs }, { every: 10 }), true, 'a busy server is waited for');
    assert.equal(asked, 3);
    asked = 0;
    assert.equal(await stillUp({ url, child: { exitCode: 1, signalCode: null } }, { every: 10 }), false, 'one that exited is not');
    assert.equal(asked, 1);
    asked = 0;
    okFrom = Infinity;
    assert.equal(await stillUp({ url, child: runs }, { within: 100, every: 10 }), false, 'one that never answers is given up');
    assert(asked > 1, 'after asking again');
  } finally {
    srv.closeAllConnections();
    await close(srv);
  }
});

test('a packaged build runs its bundled server only; a dev run takes the launcher\'s Python, else none is found', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-sidecar-'));
  try {
    const python = path.join(dir, 'python');
    fs.writeFileSync(python, '');
    const settings = { pythonPath: python, backendDir: path.join(dir, 'backend'), staticDir: '' };
    const resourcesPath = path.join(dir, 'resources');
    const exe = path.join(resourcesPath, 'gamma-server', process.platform === 'win32' ? 'gamma-server.exe' : 'gamma-server');

    assert.throws(() => resolveBackend(settings, { isPackaged: true, resourcesPath }), /Bundled server missing/, 'never the Python set in a dev run');
    fs.mkdirSync(path.dirname(exe), { recursive: true });
    fs.writeFileSync(exe, '');
    assert.deepEqual(resolveBackend(settings, { isPackaged: true, resourcesPath }), { mode: 'frozen', exe });

    const dev = resolveBackend(settings, { isPackaged: false, resourcesPath });
    assert.equal(dev.mode, 'dev');
    assert.equal(dev.pythonPath, python);
    assert.equal(dev.backendDir, settings.backendDir);
    assert.throws(() => resolveBackend({ ...settings, pythonPath: '' }, { isPackaged: false, resourcesPath }), /No backend found/, 'no backend/venv under that backend dir');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('a quit stops a server still starting, not only the ones that answer', { skip: process.platform === 'win32' && 'a shell script as the Python' }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-sidecar-'));
  try {
    // A "Python" that runs and never answers its health check.
    const python = path.join(dir, 'python');
    fs.writeFileSync(python, '#!/bin/sh\nexec sleep 60\n', { mode: 0o755 });
    const staticDir = path.join(dir, 'dist');
    fs.mkdirSync(staticDir);
    fs.writeFileSync(path.join(staticDir, 'index.html'), '');
    const settings = { pythonPath: python, backendDir: dir, staticDir };
    const started = start({ id: 'starting', dataDir: path.join(dir, 'data') }, settings, { isPackaged: false, userDataDir: dir });
    // The log folder is made right after the spawn.
    while (!fs.existsSync(path.join(dir, 'logs'))) await new Promise((r) => setTimeout(r, 20));
    const t0 = Date.now();
    stopAll();
    await assert.rejects(started, (e) => Boolean(e.startup), 'the start ends with its process');
    assert(Date.now() - t0 < 10_000, 'at once, not at the start timeout');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
