// Real Electron boundary regression, with a throwaway profile and a fake
// loopback server; never starts a Gamma backend or touches a saved profile.
// The window stays minimized (GAMMA_SHELL_MINIMIZED, test/quiet.js); --headed shows it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { _electron: electron } = require('playwright-core');
const { env: quietEnv, printShellLines } = require('./quiet');

async function run() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-security-shell-'));
  const role = '<img src="invalid" onerror="window.__shellProbe=1">';
  const server = http.createServer((req, res) => {
    if (req.url.startsWith('/api/')) {
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify(req.url.startsWith('/api/session')
        ? { user: 'tester', default_workspace: 'shared', workspaces: [{ id: 'shared', name: 'Shared', role, members: 1 }] }
        : {}));
    } else {
      res.setHeader('Content-Type', 'text/html');
      res.end('<!doctype html><p id="remote">Synthetic remote server</p>');
    }
  });
  // A server that sends everything to another origin: the guard refuses the
  // redirect in the content view, so the load is cancelled, with no did-fail-load.
  const redirector = http.createServer((req, res) => { res.writeHead(302, { Location: 'https://example.org/signin' }); res.end(); });
  // What stands in for a local server of this computer.
  const localHits = [];
  const local = http.createServer((req, res) => { localHits.push(req.url); res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><p>Local</p>'); });
  // A remote server whose pages send the window to that local server (set
  // once it listens); its API answers as the fixture's.
  let toLocal = '';
  const sender = http.createServer((req, res) => {
    if (req.url.startsWith('/api/')) return server.emit('request', req, res);
    res.writeHead(302, { Location: toLocal });
    res.end();
  });
  let app;
  try {
    // The OS's pick, again if it is below 9100: those ports are the developer's,
    // and a dynamic range can start at 1024.
    do {
      if (server.listening) await new Promise((resolve) => server.close(resolve));
      await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    } while (server.address().port < 9100);
    const origin = `http://127.0.0.1:${server.address().port}`;
    const env = {
      ...process.env,
      GAMMA_SHELL_TEST: '1',
      GAMMA_SHELL_NO_UPDATE: '1',
      GAMMA_SHELL_USER_DATA: profile,
      ...quietEnv,
    };
    delete env.ELECTRON_RUN_AS_NODE;
    app = await electron.launch({ args: [path.resolve(__dirname, '..')], timeout: 60_000, env });
    printShellLines(app.process());
    async function pageMatching(match) {
      for (let i = 0; i < 100; i++) {
        const page = app.windows().find((p) => match(p.url()));
        if (page) return page;
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      const urls = await app.evaluate(({ webContents }) => webContents.getAllWebContents().map((wc) => wc.getURL()));
      throw new Error(`Expected Electron view did not load: ${JSON.stringify(urls)}`);
    }
    const launcher = await pageMatching((url) => url.includes('/ui/launcher.html'));
    assert.equal(await launcher.evaluate(() => typeof gammaShell.list), 'function');
    const remote = await launcher.evaluate((url) => gammaShell.addRemote('Security fixture', url), origin);
    await app.evaluate(async (_, id) => { await global.__gammaShell.openServer(id); }, remote.id);
    const content = await pageMatching((url) => url.startsWith(origin));
    assert.equal(await content.evaluate(() => typeof window.gammaShell), 'undefined');
    const bar = await pageMatching((url) => url.endsWith('/ui/bar.html'));
    await bar.evaluate(() => gammaShell.state());
    await bar.waitForSelector('.wsItem', { state: 'attached' });
    assert.equal(await bar.locator('.wsItem .sub').textContent(), role);
    assert.equal(await bar.locator('.wsItem img').count(), 0);
    assert.equal(await bar.evaluate(() => window.__shellProbe), undefined);

    // This computer's servers are out of a remote page's reach: a local
    // server stands in (its sidecar's status, as the shell reads it, is
    // the fixture's), and the remote page's navigations to it, by script,
    // by a link and in a frame, go nowhere and open nothing.
    do {
      if (local.listening) await new Promise((resolve) => local.close(resolve));
      await new Promise((resolve) => local.listen(0, '127.0.0.1', resolve));
    } while (local.address().port < 9100);
    const localOrigin = `http://127.0.0.1:${local.address().port}`;
    await app.evaluate((_, url) => {
      const shell = global.__gammaShell;
      const srv = shell.registry.addLocal('Pretend host');
      const real = shell.sidecar.status;
      shell.sidecar.status = (id) => (id === srv.id ? { url, port: Number(new URL(url).port) } : real(id));
    }, localOrigin);
    const opensBefore = await app.evaluate(() => global.__gammaShell.externalOpens.length);
    assert.equal(await app.evaluate((_, url) => global.__gammaShell.isLocalPage(url), `${localOrigin}/`), true);
    assert.equal(await app.evaluate((_, url) => global.__gammaShell.isLocalPage(url), `${origin}/`), false, 'the fixture is a remote page');
    await content.evaluate((url) => { window.location.href = `${url}/`; }, localOrigin);
    await new Promise((resolve) => setTimeout(resolve, 400));
    assert.ok(content.url().startsWith(origin), 'a remote page cannot navigate to a local server');
    await content.evaluate((url) => {
      const a = document.createElement('a');
      a.href = `${url}/?ws=mine`;
      a.id = 'toLocal';
      a.textContent = 'local';
      document.body.appendChild(a);
    }, localOrigin);
    await content.evaluate(() => document.getElementById('toLocal').click());
    await new Promise((resolve) => setTimeout(resolve, 400));
    assert.ok(content.url().startsWith(origin), 'nor by a link');
    await content.evaluate((url) => {
      const f = document.createElement('iframe');
      f.src = `${url}/api/session`;
      document.body.appendChild(f);
    }, localOrigin);
    await new Promise((resolve) => setTimeout(resolve, 600));
    assert.ok(!content.frames().some((f) => f.url().startsWith(localOrigin)), 'nor in a frame');
    assert.equal(await app.evaluate(() => global.__gammaShell.externalOpens.length), opensBefore, 'and nothing opened for it');
    // What a remote page may use: notifications, not the location
    assert.equal(await content.evaluate(() => Notification.requestPermission()), 'granted');
    assert.equal(await content.evaluate(() => navigator.permissions.query({ name: 'geolocation' }).then((p) => p.state)), 'denied');
    assert.equal(await content.evaluate(() => new Promise((resolve) => navigator.geolocation.getCurrentPosition(() => resolve('allowed'),
      (e) => resolve(e.code === 1 ? 'denied' : `error ${e.code}`)))), 'denied');

    const file = path.join(profile, 'untrusted.html');
    fs.writeFileSync(file, '<!doctype html><p>Untrusted local file</p>');
    const fileUrl = pathToFileURL(file).href;
    await content.evaluate((url) => { window.location.href = url; }, fileUrl);
    await new Promise((resolve) => setTimeout(resolve, 200));
    assert.ok(content.url().startsWith(origin), 'remote page cannot navigate into local files');
    // Direct main-process loads do not emit will-navigate; even then an
    // unrelated local file must never receive the bridge.
    await app.evaluate(async ({ webContents }, { origin, fileUrl }) => {
      const view = webContents.getAllWebContents().find((wc) => wc.getURL().startsWith(origin));
      await view.loadURL(fileUrl);
    }, { origin, fileUrl });
    await content.waitForURL(fileUrl);
    assert.equal(await content.evaluate(() => typeof window.gammaShell), 'undefined');

    await new Promise((resolve) => redirector.listen(0, '127.0.0.1', resolve));
    const redirecting = `http://127.0.0.1:${redirector.address().port}`;
    const refused = await app.evaluate(async (_, { url, fixture }) => {
      const shell = global.__gammaShell;
      const { id } = shell.registry.addRemote('Redirecting', url);
      const open = async () => {
        const t0 = Date.now();
        const error = await shell.openServer(id).then(() => '', (e) => e.message);
        return { error, ms: Date.now() - t0 };
      };
      const alone = await open();
      // Right after another load, still finishing: what a queued open meets.
      await shell.openServer(shell.registry.addRemote('Just before', fixture).id);
      const overlapped = await open();
      return { alone, overlapped, opened: shell.externalOpens.slice() };
    }, { url: redirecting, fixture: origin.replace('127.0.0.1', 'localhost') });
    for (const open of [refused.alone, refused.overlapped]) {
      assert.match(open.error, /ERR_FAILED/, 'the open fails');
      assert.ok(open.ms < 10_000, `the open fails at once, not after a wait: ${open.ms} ms`);
    }
    assert.ok(refused.opened.includes('https://example.org/signin'), 'the redirect went to the system browser');

    // A remote server's redirect to a local server, on the shell's own load
    // (no frame started it), the local origin allowed as one opened before:
    // the window never lands there, and nothing is opened for it.
    toLocal = `${localOrigin}/?ws=mine`;
    await new Promise((resolve) => sender.listen(0, '127.0.0.1', resolve));
    const hitsBefore = localHits.length;
    const sent = await app.evaluate(async ({ webContents }, { url, localOrigin }) => {
      const shell = global.__gammaShell;
      shell.allowedOrigins.add(localOrigin);
      // opened from the launcher: the page shown is this computer's
      const view = webContents.getAllWebContents().find((wc) => !wc.getURL().endsWith('/ui/bar.html'));
      await shell.loadLauncher();
      for (let i = 0; i < 50 && !view.getURL().includes('/ui/launcher.html'); i++) await new Promise((resolve) => setTimeout(resolve, 100));
      const before = shell.externalOpens.length;
      const error = await shell.openServer(shell.registry.addRemote('Sends to the host', url).id).then(() => '', (e) => e.message);
      return { error, opened: shell.externalOpens.slice(before) };
    }, { url: `http://localhost:${sender.address().port}`, localOrigin });
    await new Promise((resolve) => setTimeout(resolve, 300));
    assert.match(sent.error, /ERR_FAILED/, 'the open fails');
    assert.ok(!content.url().startsWith(localOrigin), `the window is not on the local server: ${content.url()}`);
    // (the shell's own readings of it, /api/…, go on meanwhile)
    assert.deepEqual(localHits.slice(hitsBefore).filter((u) => !u.startsWith('/api/')), [], `the local server was never asked for a page: ${localHits.slice(hitsBefore)}`);
    assert.deepEqual(sent.opened, [], 'and nothing was opened for it');
    console.log('PASS: bundled chrome bridge works, remote/local untrusted documents have no bridge, role HTML is inert, remote file navigation is blocked, a remote page reaches no local server, nor a remote server\'s redirect, and gets notifications but not the location, a refused redirect fails the open at once.');
  } finally {
    if (app) await app.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    redirector.closeAllConnections();
    if (redirector.listening) await new Promise((resolve) => redirector.close(resolve));
    sender.closeAllConnections();
    if (sender.listening) await new Promise((resolve) => sender.close(resolve));
    local.closeAllConnections();
    if (local.listening) await new Promise((resolve) => local.close(resolve));
    fs.rmSync(profile, { recursive: true, force: true });
  }
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
