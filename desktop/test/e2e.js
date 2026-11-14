// End-to-end check of the desktop shell — the executable half of
// docs/checklist.md. Drives the real app (dev tree by default, the packaged build
// with --packaged) through Playwright's Electron driver over a throwaway
// profile, so the real registry, cookies and servers are never touched.
//
//   npm run e2e               # dev: sidecars from backend/venv + frontend/dist
//   npm run e2e:packaged      # after `npm run pack`: the frozen bundle
//   node test/e2e.js --keep   # leave the temp profile behind for inspection
//   node test/e2e.js --continue   # run every step even after a failure
//   node test/e2e.js --headed # watch it: the window is not kept minimized
//
// The window opens minimized (GAMMA_SHELL_MINIMIZED, test/quiet.js) and stays
// down: a PDF handed over or a sign-in come back do not bring it up, and on
// Windows a step that leaves it up fails. Only the background step brings it back, as
// Show Gamma and the Dock icon do, unfocused, and puts it down again. So a
// run leaves the screen to whoever sits at it.
// Prints one line per step and a summary; exit 1 on any failure.

const { _electron: electron } = require('playwright-core');
const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const https = require('https');
const os = require('os');
const path = require('path');
const net = require('net');
const { execFileSync, spawn } = require('child_process');
const { headed, env: quietEnv, printShellLines } = require('./quiet');

const ROOT = path.join(__dirname, '..');
const packaged = process.argv.includes('--packaged');
const keep = process.argv.includes('--keep');
const continueOnFail = process.argv.includes('--continue');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
let launched = null; // the app the last launch started, whose window each step leaves minimized

// Windows alone, where the suite runs: a Linux session with no window manager
// has no minimized state to read.
async function assertWindowDown() {
  if (headed || !launched || process.platform !== 'win32') return;
  const up = await launched.evaluate(({ BaseWindow }) => BaseWindow.getAllWindows().filter((w) => !w.isMinimized()).length)
    .catch(() => 0); // the app may have quit in the step
  assert.equal(up, 0, 'the step left the window up');
}

// A box is what a user would see go wrong, in a step that may well go on to
// pass: a false "Could not open the clone" did for a long time.
async function assertNoErrorBox() {
  if (!launched) return;
  const boxes = await launched.evaluate(() => global.__gammaShell.errorBoxes.splice(0)).catch(() => []);
  assert.deepEqual(boxes, [], 'the step opened an error box');
}

async function step(name, fn) {
  const t0 = Date.now();
  try {
    const note = await fn();
    await assertWindowDown();
    await assertNoErrorBox();
    results.push({ name, ok: true, note: note || '', ms: Date.now() - t0 });
    console.log(`  ok    ${name}${note ? '  — ' + note : ''}`);
  } catch (e) {
    results.push({ name, ok: false, note: String((e && e.message) || e), ms: Date.now() - t0 });
    console.log(`  FAIL  ${name}\n        ${String((e && e.stack) || e).split('\n').join('\n        ')}`);
    if (!continueOnFail) throw e;
  } finally {
    // A failed step's boxes are its own, not the next step's.
    if (launched) await launched.evaluate(() => { global.__gammaShell.errorBoxes.length = 0; }).catch(() => {});
    if (!headed && launched) {
      await launched.evaluate(({ BaseWindow }) => {
        for (const w of BaseWindow.getAllWindows()) if (!w.isMinimized()) w.minimize();
      }).catch(() => {}); // the app may have quit in the step
    }
  }
}

function packagedBinary() {
  const dist = path.join(ROOT, 'dist');
  const candidates = [
    path.join(dist, 'win-unpacked', 'Gamma.exe'),
    path.join(dist, 'mac-arm64', 'Gamma.app', 'Contents', 'MacOS', 'Gamma'),
    path.join(dist, 'mac', 'Gamma.app', 'Contents', 'MacOS', 'Gamma'),
    path.join(dist, 'linux-unpacked', 'gamma'), // linux.executableName in electron-builder.cjs
  ];
  const hit = candidates.find((p) => fs.existsSync(p));
  if (!hit) throw new Error(`no packaged app under ${dist} — run "npm run pack" first`);
  return hit;
}

// A valid one-page PDF with real text (pypdf and PyMuPDF must parse it: the
// annotated-PDF export and the search index read it back). `comment`: a
// highlight over the text carrying it, as another reader saves one.
function minimalPdf(text, comment) {
  const annots = comment ? ' /Annots [6 0 R]' : '';
  const objs = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >>${annots} >>`,
  ];
  const stream = `BT /F1 24 Tf 72 700 Td (${text}) Tj ET`;
  objs.push(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`);
  objs.push('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>');
  if (comment) {
    objs.push(`<< /Type /Annot /Subtype /Highlight /Rect [72 694 300 724] /QuadPoints [72 724 300 724 72 694 300 694] /C [1 0.9 0.3] /Contents (${comment}) >>`);
  }
  let out = '%PDF-1.4\n';
  const offsets = [];
  objs.forEach((o, i) => {
    offsets.push(out.length);
    out += `${i + 1} 0 obj\n${o}\nendobj\n`;
  });
  const xref = out.length;
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n`;
  for (const off of offsets) out += String(off).padStart(10, '0') + ' 00000 n \n';
  out += `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, 'latin1');
}

// A self-signed certificate for 127.0.0.1 and localhost, `{ cert, key }` in
// PEM, made by the backend's Python with `cryptography` (GAMMA_E2E_PYTHON,
// else backend/venv). The script imports nothing of Gamma's, so no data
// directory is touched; it is written beside the profile's other files.
const CERT_SCRIPT = `
import datetime, ipaddress, json
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
key = ec.generate_private_key(ec.SECP256R1())
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Gamma e2e NAS")])
now = datetime.datetime.now(datetime.timezone.utc)
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256()))
print(json.dumps({"cert": cert.public_bytes(serialization.Encoding.PEM).decode(),
                  "key": key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()}))
`;
function selfSigned(profile) {
  const python = process.env.GAMMA_E2E_PYTHON
    || path.join(ROOT, '..', 'backend', 'venv', process.platform === 'win32' ? path.join('Scripts', 'python.exe') : path.join('bin', 'python'));
  const script = path.join(profile, 'selfsigned.py');
  fs.writeFileSync(script, CERT_SCRIPT);
  return JSON.parse(execFileSync(python, ['-I', script], { encoding: 'utf8' }));
}

// A certificate's fingerprint as the pin keeps it, and as the card groups it.
const digestOf = (pem) => new crypto.X509Certificate(pem).fingerprint256.replace(/:/g, '').toLowerCase();
const grouped = (hex) => hex.toUpperCase().match(/.{2}/g).join(':');

// A port nothing listens on (bind, read, release) — for the "server down" case.
function closedPort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const port = srv.address().port;
      srv.close(() => resolve(port));
    });
  });
}

function pidAlive(pid) {
  if (!pid) return false;
  if (process.platform === 'win32') {
    const out = execFileSync('tasklist', ['/FI', `PID eq ${pid}`, '/NH'], { encoding: 'utf8' });
    return out.includes(String(pid));
  }
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

// A stand-in for the Gamma Cloud account server (cloud/), as much of it as a
// sign-in uses: discovery, an authorize that signs `person` in at once, the
// token endpoint (PKCE checked) with an Ed25519 ID token, the key set, and
// the profile and server-list calls that follow a sign-in.
function fakeAccountServer(person) {
  const { privateKey, publicKey } = crypto.generateKeyPairSync('ed25519');
  const jwk = { ...publicKey.export({ format: 'jwk' }), kid: 'e2e', alg: 'EdDSA', use: 'sig' };
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url');
  const codes = new Map();
  let issuer = '';
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      const url = new URL(req.url, issuer);
      const json = (status, value) => res.writeHead(status, { 'Content-Type': 'application/json' }).end(JSON.stringify(value));
      if (url.pathname === '/.well-known/openid-configuration') {
        return json(200, { issuer, authorization_endpoint: `${issuer}/authorize`, token_endpoint: `${issuer}/token`,
          jwks_uri: `${issuer}/jwks`, revocation_endpoint: `${issuer}/revoke` });
      }
      if (url.pathname === '/jwks') return json(200, { keys: [jwk] });
      if (url.pathname === '/authorize') {
        const q = url.searchParams;
        const code = crypto.randomBytes(16).toString('hex');
        codes.set(code, { nonce: q.get('nonce'), challenge: q.get('code_challenge'), redirect: q.get('redirect_uri'), client: q.get('client_id') });
        return res.writeHead(302, { Location: `${q.get('redirect_uri')}?${new URLSearchParams({ code, state: q.get('state') })}` }).end();
      }
      if (url.pathname === '/token') {
        const form = new URLSearchParams(body);
        const held = codes.get(form.get('code'));
        codes.delete(form.get('code'));
        const challenge = crypto.createHash('sha256').update(form.get('code_verifier') || '').digest('base64url');
        if (!held || held.challenge !== challenge || held.redirect !== form.get('redirect_uri')) return json(400, { error: 'invalid_grant' });
        const now = Math.floor(Date.now() / 1000);
        const signed = `${b64({ alg: 'EdDSA', typ: 'JWT', kid: 'e2e' })}.${b64({ iss: issuer, aud: held.client, iat: now, exp: now + 600, nonce: held.nonce, ...person })}`;
        const idToken = `${signed}.${crypto.sign(null, Buffer.from(signed), privateKey).toString('base64url')}`;
        return json(200, { access_token: 'at-e2e', token_type: 'Bearer', expires_in: 3600, refresh_token: 'rt-e2e', id_token: idToken });
      }
      if (url.pathname === '/revoke') return json(200, {});
      if (url.pathname.startsWith('/api/')) {
        return req.method === 'GET' ? json(404, { detail: 'not found' }) : json(200, { updated_at: new Date().toISOString(), server: {} });
      }
      return json(404, { detail: 'not found' });
    });
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => {
    issuer = `http://127.0.0.1:${server.address().port}`;
    resolve({ issuer, close: () => server.close() });
  }));
}

// ------------------------------------------------------------ driver -------

function shellEnv(userData, downloadDir) {
  const env = {
    ...process.env,
    GAMMA_SHELL_USER_DATA: userData,
    GAMMA_SHELL_DOWNLOAD_DIR: downloadDir,
    GAMMA_SHELL_PICK_DIR: path.join(userData, 'kept'), // the folder picker's answer (empty: used as it is)
    GAMMA_SHELL_TEST: '1',
    ...quietEnv,
  };
  delete env.ELECTRON_RUN_AS_NODE;
  return env;
}

// Unpacked Linux dir: no setuid chrome-sandbox (the .deb's postinst sets it
// up), so Chromium only starts with --no-sandbox. Same as test/smoke.js.
const packagedArgs = process.platform === 'linux' ? ['--no-sandbox'] : [];

// `files`: what the command line names after the app, as the file manager
// passes a file or a folder opened with Gamma.
async function launch(userData, downloadDir, files = []) {
  const env = shellEnv(userData, downloadDir);
  const opts = packaged ? { executablePath: packagedBinary(), args: [...packagedArgs, ...files], env } : { args: [ROOT, ...files], env };
  launched = await electron.launch({ ...opts, timeout: 60_000 });
  printShellLines(launched.process());
  return launched;
}

// A second start of the app on the same profile, as Explorer runs the
// registered command for a file or a folder: it should hand what it names to
// the running app and quit. Resolves to its exit code.
function secondStart(userData, downloadDir, files) {
  const [bin, args] = packaged ? [packagedBinary(), [...packagedArgs, ...files]] : [require('electron'), [ROOT, ...files]];
  return new Promise((resolve, reject) => {
    const child = spawn(bin, args, { env: shellEnv(userData, downloadDir), stdio: 'ignore' });
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error('the second start did not quit'));
    }, 30_000);
    child.once('error', reject);
    child.once('exit', (code) => {
      clearTimeout(timer);
      resolve(code);
    });
  });
}

// The shell's views are separate webContents = separate Playwright pages.
async function findPage(app, pred, timeout = 30_000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    const hit = app.windows().find((p) => pred(p.url()));
    if (hit) return hit;
    await sleep(150);
  }
  throw new Error('page not found: ' + app.windows().map((p) => p.url()).join(', '));
}
const isBar = (u) => u.endsWith('/ui/bar.html');
const isLauncher = (u) => u.includes('/ui/launcher.html');

// The inspector runs an evaluate as soon as it arrives, in the middle of whatever
// the main process is running (a V8 interrupt): a registry write there lands
// between a reconcileMirrors' load and save, and that save takes it back. So the
// body runs on a turn of the event loop of its own, as an IPC handler does.
const hook = (app, fn, arg) => app.evaluate(({ app: _a }, [src, a]) => new Promise((resolve, reject) => {
  setImmediate(() => {
    try {
      // eslint-disable-next-line no-new-func
      resolve(new Function('shell', 'arg', `return (${src})(shell, arg)`)(global.__gammaShell, a));
    } catch (e) {
      reject(e);
    }
  });
}), [fn.toString(), arg]);

async function waitFor(fn, what, timeout = 60_000, every = 300) {
  const t0 = Date.now();
  let last;
  while (Date.now() - t0 < timeout) {
    try {
      last = await fn();
      if (last) return last;
    } catch (e) {
      last = e;
    }
    await sleep(every);
  }
  throw new Error(`timeout waiting for ${what} (last: ${last && last.message ? last.message : JSON.stringify(last)})`);
}

const sessionUser = (page) =>
  page.evaluate(() => fetch('/api/session', { credentials: 'same-origin' }).then((r) => r.json()).then((j) => j.user || ''));

const rootTitles = (page) =>
  page.evaluate(() => fetch('/api/blocks/root/children', { credentials: 'same-origin' }).then((r) => r.json())
    .then((d) => (d.children || d.blocks || []).map((b) => b.content)));

async function waitLoggedIn(page, user = 'admin') {
  await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+/, { timeout: 90_000 });
  await waitFor(async () => (await sessionUser(page)) === user, `session=${user}`);
}

// Gamma's API on the open server in workspace `ws`, from the main process
// (the shell's `api` test hook).
const serverCall = (app, apiPath, opts) => hook(app, (s, [p, o]) => s.api(p, o), [apiPath, opts]);

// A folder of workspace `ws` holding one new page: the page's id.
async function makeFolderWithPage(app, ws, folderId, position, folder, title) {
  await serverCall(app, '/api/pages/folders/ops', { ws, method: 'POST', body: { client: 'e2e', ops: [{ op: 'insert', id: folderId, parent: 'folders', position, content: folder }] } });
  const page = await serverCall(app, '/api/blocks', { ws, method: 'POST', body: { parent_id: 'root', content: title } });
  await serverCall(app, `/api/blocks/${page.id}`, { ws, method: 'PUT', body: { properties: { folders: [folderId] } } });
  return page.id;
}

// The sync panel's folder chooser for the open workspace, at the row of `folder`.
async function chooserRow(bar, folder) {
  await bar.click('#syncBtn');
  await bar.click('#syncFolderBtn');
  const item = bar.locator('#syncPanel .folderItem', { hasText: folder });
  await item.waitFor({ timeout: 15_000 });
  return item;
}

// The vault dialog in the sync panel (main.js askVault), once it is up: its
// sentence, and Sync pressed.
async function syncInDialog(bar, said) {
  const box = bar.locator('#syncPanel #vaultAsk');
  await box.waitFor({ timeout: 30_000 });
  const text = await bar.locator('#vaultSay').textContent();
  if (said) assert.match(text, said, 'the dialog says what the first round does');
  await bar.click('#vaultSync');
  return text;
}

// A vault's own view in the panel (its row's "more"), at its action `act`:
// a button of the view's bar (sync, pull-now, push-now, pause, open) or a
// row under it (go-on, locate, the forces, remove).
async function vaultAct(row, bar, act) {
  await row.locator('[data-act="more"]').click();
  const item = bar.locator(`#syncPanel .vaultAct[data-act="${act}"]`);
  await item.waitFor({ timeout: 15_000 });
  return item;
}

// The sync panel's row of a clone or a synced vault, read fresh.
async function keptRow(app, bar, kind, name) {
  await hook(app, (s) => s.refreshKeeping());
  await bar.click('#syncBtn');
  const row = bar.locator(`#syncPanel .keepItem[data-kind="${kind}"]`, { hasText: name });
  await row.waitFor({ timeout: 15_000 });
  return row;
}

// The page the content view shows: the shell loads `?page=<id>`, and Gamma
// rewrites it to `?block=<id>` once the page is open.
const shownPage = (content) => {
  const q = new URL(content.url()).searchParams;
  return q.get('page') || q.get('block') || '';
};

// Both of the bar's dropdowns closed.
const menuClosed = (bar) => waitFor(() => bar.evaluate(() => document.getElementById('menu').hidden && document.getElementById('syncPanel').hidden), 'menu closed');

// The addresses `page` loads from now on, the shell's hand-over with them
// (`?track=`, `&tick=`), which the page drops from its address as it boots;
// `stop()` stops listening.
function loadsOf(page) {
  const seen = [];
  const on = (frame) => { if (frame === page.mainFrame()) seen.push(frame.url()); };
  page.on('framenavigated', on);
  return { seen, stop: () => page.off('framenavigated', on) };
}
const loaded = (loads, pred) => loads.seen.some((u) => pred(new URL(u).searchParams));

// Two names of one file or directory (links resolved, case ignored), as a
// vault's `dest` and the path handed over may differ.
const samePath = (a, b) => {
  try {
    return fs.realpathSync(a).toLowerCase() === fs.realpathSync(b).toLowerCase();
  } catch {
    return false;
  }
};

// A work folder's tracking dialog in `page` (the web app's own words, in
// either catalog).
const trackDialog = (page) => page.getByRole('dialog', { name: /^(Choose files|选择文件)$/ });

// The sync panel still shown a while after a press in it: open in the page,
// and the bar view still grown over the content (else the panel is cut off).
async function panelStaysOpen(app, bar) {
  await sleep(1500);
  assert(!(await bar.evaluate(() => document.getElementById('syncPanel').hidden)), 'the panel stays open');
  assert(await hook(app, (s) => s.barExpanded()), 'the bar view stays grown over the content');
}

// ------------------------------------------------------------- steps -------

async function main() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-e2e-'));
  const downloads = path.join(profile, 'downloads');
  fs.mkdirSync(downloads);
  console.log(`e2e (${packaged ? 'packaged' : 'dev'}) profile: ${profile}`);
  // A worktree has no backend/venv of its own: GAMMA_E2E_PYTHON (as the
  // browser suite takes it) names the Python its sidecars run this tree's
  // backend with (the launcher's Python path setting).
  if (!packaged && process.env.GAMMA_E2E_PYTHON) {
    fs.writeFileSync(path.join(profile, 'servers.json'), JSON.stringify({ settings: { pythonPath: process.env.GAMMA_E2E_PYTHON } }));
  }
  // Every sidecar signs in with Gamma Cloud at the stand-in, never the real one;
  // a new identity gets an account of its own.
  const cloud = await fakeAccountServer({ sub: 'sub-e2e-desk', preferred_username: 'desk', email: 'desk@example.org',
    email_verified: true, name: 'Desk', plan: 'free' });
  process.env.GAMMA_CLOUD_ISSUER = cloud.issuer;
  process.env.GAMMA_CLOUD_POLICY = 'provision';

  let app;
  let bar, content;
  const ids = {};
  const urls = {};
  const homeWs = {}; // a server's workspace, by the server's key
  const pids = {};
  let pageId, sourceUrl, alphaUploads;
  let openedPage = null; // the PDF a step opened where it is: { id, ws, doc }
  let workFolder = null; // the work folder a folder opened with Gamma made: { id, folder, dir, ws }
  let backupZip;

  try {
    // In the try: a first start that fails still removes the profile.
    app = await launch(profile, downloads);
    await step('first start shows the launcher + shell bar', async () => {
      bar = await findPage(app, isBar);
      content = await findPage(app, isLauncher);
      await content.waitForSelector('#btnAddLocal');
      await waitFor(async () => (await bar.textContent('#wsName')).trim() === 'Servers', 'bar idle label');
      const b = await hook(app, (s) => s.bounds());
      assert(b.bar.height === 38 && b.content.y === 38, `layout ${JSON.stringify(b)}`);
      const version = await content.textContent('#version');
      assert(/Gamma desktop \d/.test(version), version);
      // A packaged build runs its bundled server only (lib/sidecar.js resolveBackend).
      await waitFor(async () => (await content.locator('#advanced').isHidden()) === packaged,
        packaged ? 'no source-checkout settings in a packaged build' : 'the source-checkout settings in a dev run');
      return version.trim();
    });

    await step('create a local server from the launcher', async () => {
      await content.click('#btnAddLocal');
      await content.fill('#localName', 'Alpha');
      await content.click('#btnCreateLocal');
      await content.locator('.card', { hasText: 'Alpha' }).waitFor();
      const ws = await hook(app, (s) => s.registry.load().servers);
      assert.equal(ws.length, 1);
      ids.alpha = ws[0].id;
      assert(fs.existsSync(ws[0].dataDir), 'data dir created');
      return ws[0].dataDir;
    });

    await step('open it: sidecar starts, Gamma loads, auto-login lands', async () => {
      await content.locator('.card', { hasText: 'Alpha' }).locator('button', { hasText: 'Open' }).click();
      await waitLoggedIn(content);
      urls.alpha = new URL(content.url()).origin;
      const cur = await hook(app, (s) => s.current());
      assert.equal(cur && cur.id, ids.alpha);
      await waitFor(async () => (await bar.textContent('#wsName')).trim() === 'Alpha', 'bar shows Alpha');
      assert.equal(await bar.isHidden('#btnReload'), false, 'reload button visible');
      pids.alpha = await hook(app, (s, id) => s.sidecar.status(id).child.pid, ids.alpha);
      return `${urls.alpha} pid ${pids.alpha}`;
    });

    await step('data dir has the standard GAMMA_DATA_DIR layout', async () => {
      const dir = await hook(app, (s, id) => s.registry.get(id).dataDir, ids.alpha);
      assert(fs.existsSync(path.join(dir, 'users.db')), 'missing users.db');
      // One directory per Gamma workspace, named by id (the admin's personal one here).
      const wsDirs = fs.readdirSync(path.join(dir, 'workspaces'));
      assert(wsDirs.length >= 1, 'no workspace directory');
      for (const f of ['pages.db', 'data.db', 'uploads']) {
        assert(fs.existsSync(path.join(dir, 'workspaces', wsDirs[0], f)), `missing workspaces/<id>/${f}`);
      }
      return fs.readdirSync(dir).join(', ') + ' / workspaces: ' + wsDirs.join(', ');
    });

    await step('upload a PDF + create a paper page with a math note', async () => {
      const b64 = minimalPdf('Hello from the e2e paper').toString('base64');
      const r = await content.evaluate(async (b64) => {
        const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        const fd = new FormData();
        fd.append('file', new Blob([bytes], { type: 'application/pdf' }), 'e2e.pdf');
        const up = await fetch('/api/uploads', { method: 'POST', body: fd, credentials: 'same-origin' });
        if (!up.ok) throw new Error('upload ' + up.status + ' ' + (await up.text()));
        const u = await up.json();
        const H = { 'Content-Type': 'application/json' };
        const page = await fetch('/api/blocks', { method: 'POST', headers: H, credentials: 'same-origin', body: JSON.stringify({ parent_id: 'root', content: 'E2E paper' }) }).then((r) => r.json());
        const att = await fetch('/api/pages/' + page.id + '/attachment', { method: 'POST', headers: H, credentials: 'same-origin', body: JSON.stringify({ doc_id: u.doc_id }) });
        if (!att.ok) throw new Error('attach ' + att.status + ' ' + (await att.text()));
        await fetch('/api/blocks', { method: 'POST', headers: H, credentials: 'same-origin', body: JSON.stringify({ parent_id: page.id, content: 'A note with $E = mc^2$ and a table\n\n| a | b |\n|---|---|\n| 1 | 2 |' }) });
        return { pageId: page.id, sourceUrl: u.source_url };
      }, b64);
      pageId = r.pageId;
      sourceUrl = r.sourceUrl;
      const dir = await hook(app, (s, id) => s.registry.get(id).dataDir, ids.alpha);
      const wsDir = fs.readdirSync(path.join(dir, 'workspaces'))[0];
      // A new account also starts with the seeded Welcome PDF (gamma/seed.py).
      // Stored files only: not the `.partial/` directory of in-flight writes.
      alphaUploads = fs.readdirSync(path.join(dir, 'workspaces', wsDir, 'uploads'), { withFileTypes: true })
        .filter((e) => e.isFile() && !e.name.startsWith('.')).map((e) => e.name);
      const mine = alphaUploads.find((f) => sourceUrl.endsWith('/' + f));
      assert(mine, 'the upload is on disk: ' + alphaUploads.join(', '));
      return `${sourceUrl} → ${mine}`;
    });

    await step('page exports in every mode (frozen bundle: pypdf, ziamath fonts)', async () => {
      const res = await content.evaluate(async (pageId) => {
        const out = {};
        const get = async (label, url) => {
          const r = await fetch(url, { credentials: 'same-origin' });
          const buf = new Uint8Array(await r.arrayBuffer());
          out[label] = { status: r.status, type: r.headers.get('content-type'), size: buf.length, head: String.fromCharCode(...buf.slice(0, 4)) };
        };
        for (const mode of ['readable', 'notes-pdf', 'logseq-graph', 'zotero-rdf', 'gamma']) {
          await get(mode, `/api/pages/${pageId}/export?mode=${mode}`);
        }
        await get('export-pdf', `/api/pages/${pageId}/export-pdf?notes=1&highlights=1`);
        return out;
      }, pageId);
      const bad = Object.entries(res).filter(([, v]) => v.status !== 200 || !v.size);
      assert(!bad.length, 'failed: ' + JSON.stringify(bad));
      assert.equal(res['notes-pdf'].head, '%PDF', 'notes-pdf is a PDF');
      assert.equal(res['export-pdf'].head, '%PDF', 'export-pdf is a PDF');
      return Object.entries(res).map(([k, v]) => `${k}:${v.size}B`).join(' ');
    });

    await step('markdown import creates a page', async () => {
      const r = await content.evaluate(async () => {
        const fd = new FormData();
        fd.append('file', new Blob(['# Imported notes\n\n- first\n  - nested\n- second'], { type: 'text/markdown' }), 'imported.md');
        const r = await fetch('/api/import/markdown', { method: 'POST', body: fd, credentials: 'same-origin' });
        return { status: r.status, body: await r.text() };
      });
      assert.equal(r.status, 200, r.body);
      const titles = await rootTitles(content);
      assert(titles.some((t) => /Imported notes|imported/i.test(t)), 'imported page listed: ' + titles.join(' | '));
      return titles.join(' | ');
    });

    await step('backup zip downloads through the browser download path', async () => {
      const r = await content.evaluate(async () => {
        const r = await fetch('/api/export', { credentials: 'same-origin' });
        const blob = await r.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'e2e-backup.zip';
        document.body.appendChild(a);
        a.click();
        a.remove();
        return { status: r.status, size: blob.size };
      });
      assert.equal(r.status, 200);
      const file = await waitFor(async () => {
        const f = path.join(downloads, 'e2e-backup.zip');
        return fs.existsSync(f) && fs.statSync(f).size === r.size ? f : null;
      }, 'downloaded zip', 30_000);
      backupZip = fs.readFileSync(file);
      assert.equal(backupZip.subarray(0, 2).toString(), 'PK', 'zip magic');
      return `${file} (${r.size} B)`;
    });

    await step('second server; switch from the shell bar while Alpha keeps running', async () => {
      ids.beta = await hook(app, (s) => s.registry.addLocal('Beta').id);
      await bar.click('#wsBtn');
      await waitFor(async () => {
        const b = await hook(app, (s) => s.bounds());
        return b.bar.height > 100;
      }, 'bar expanded for the menu', 5_000);
      await bar.locator(`#menu .item[data-id="${ids.beta}"]`).click();
      await waitFor(async () => new URL(content.url()).origin !== urls.alpha && content.url().startsWith('http'), 'navigated to Beta');
      await waitLoggedIn(content);
      urls.beta = new URL(content.url()).origin;
      assert.notEqual(urls.beta, urls.alpha);
      await waitFor(async () => (await bar.textContent('#wsName')).trim() === 'Beta', 'bar shows Beta');
      const b = await hook(app, (s) => s.bounds());
      assert.equal(b.bar.height, 38, 'bar collapsed again');
      const alphaStill = await hook(app, (s, id) => Boolean(s.sidecar.status(id)), ids.alpha);
      assert(alphaStill, 'Alpha sidecar still running');
      pids.beta = await hook(app, (s, id) => s.sidecar.status(id).child.pid, ids.beta);
      return `${urls.beta} (Alpha still up on ${urls.alpha})`;
    });

    await step('the bar menu shows each running local server\'s address, and the registry remembers its port', async () => {
      await bar.click('#wsBtn');
      for (const k of ['alpha', 'beta']) {
        const addr = bar.locator(`#menu .item[data-id="${ids[k]}"] .sub.addr`);
        await addr.waitFor({ timeout: 5_000 });
        assert.equal((await addr.textContent()).trim(), new URL(urls[k]).host, `${k}'s address in the menu`);
        assert.equal(await hook(app, (s, id) => s.registry.get(id).port, ids[k]), Number(new URL(urls[k]).port), `${k}'s port remembered`);
      }
      await bar.keyboard.press('Escape');
      await menuClosed(bar);
      return `${new URL(urls.alpha).host}, ${new URL(urls.beta).host}`;
    });

    await step('import the Alpha backup into Beta', async () => {
      const r = await content.evaluate(async (b64) => {
        const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        const fd = new FormData();
        fd.append('file', new Blob([bytes], { type: 'application/zip' }), 'backup.zip');
        const r = await fetch('/api/import-data', { method: 'POST', body: fd, credentials: 'same-origin' });
        return { status: r.status, body: await r.text() };
      }, backupZip.toString('base64'));
      assert.equal(r.status, 200, r.body);
      const d = JSON.parse(r.body);
      assert(d.restored.includes('pages.db'), 'pages.db restored');
      const titles = await rootTitles(content);
      assert(titles.includes('E2E paper'), 'Alpha page now in Beta: ' + titles.join(' | '));
      const pdf = await content.evaluate((u) => fetch(u, { credentials: 'same-origin' }).then((r) => r.arrayBuffer()).then((b) => String.fromCharCode(...new Uint8Array(b).slice(0, 4))), sourceUrl);
      assert.equal(pdf, '%PDF', 'upload restored');
      assert.equal(d.uploads_in_backup, alphaUploads.length, 'backup carried every upload: ' + r.body);
      return `restored ${d.restored.join(', ')}, uploads ${d.uploads_added}/${d.uploads_in_backup}`;
    });

    await step('switch back to Alpha: instant, same server, no restart', async () => {
      homeWs.beta = await waitFor(() => new URL(content.url()).searchParams.get('ws'), 'Beta writes its workspace into the address', 15_000);
      const t0 = Date.now();
      await bar.click('#wsBtn');
      await bar.locator(`#menu .item[data-id="${ids.alpha}"]`).click();
      await waitFor(async () => new URL(content.url()).origin === urls.alpha, 'back on Alpha', 15_000);
      await waitFor(async () => (await sessionUser(content)) === 'admin', 'still logged in');
      const pid = await hook(app, (s, id) => s.sidecar.status(id).child.pid, ids.alpha);
      assert.equal(pid, pids.alpha, 'same sidecar process');
      return `${Date.now() - t0} ms`;
    });

    await step('Gamma workspaces: the bar lists them and switches with ?ws=', async () => {
      // A second Gamma workspace inside Alpha, made through the public API
      // from the signed-in page (the shell never touches Gamma's data itself).
      const lab = await content.evaluate(async () => {
        const r = await fetch('/api/workspaces', { method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'E2E lab' }) });
        return (await r.json()).id;
      });
      await bar.click('#wsBtn');
      await waitFor(async () => (await bar.locator(`#menu .wsItem[data-ws="${lab}"]`).count()) === 1, 'lab listed in the bar menu', 15_000);
      await bar.locator(`#menu .wsItem[data-ws="${lab}"]`).click();
      await waitFor(async () => new URL(content.url()).searchParams.get('ws') === lab, 'content navigated to ?ws=', 15_000);
      await waitFor(async () => (await bar.textContent('#wsWorkspace')).includes('E2E lab'), 'bar names the workspace', 15_000);
      const g = await hook(app, (s) => s.gamma());
      assert(g && g.current === lab && g.list.length === 2, 'shell knows the workspaces: ' + JSON.stringify(g));
      assert.equal(new URL(content.url()).origin, urls.alpha, 'same server');
      // One session cookie per host: signed in on Alpha, the window is signed out of
      // Beta. Opening Beta signs in again, and its workspace follows at once, as an
      // origin's or a clone's chip and a PDF opened with Gamma do. openServer answers
      // once the signed-in page has loaded, so the workspace's load does not start
      // under the sign-in's reload, and the workspace is there when it resolves. Then
      // the same way back to Alpha's lab.
      for (const [k, ws] of [['beta', homeWs.beta], ['alpha', lab]]) {
        const r = await app.evaluate(({ webContents }, [id, w]) => new Promise((resolve, reject) => {
          setImmediate(async () => { // a turn of the loop of its own, as hook gives
            try {
              const opened = await global.__gammaShell.openServer(id);
              await global.__gammaShell.openGammaWorkspace(w);
              const shown = webContents.getAllWebContents().find((c) => c.getURL().startsWith('http'));
              resolve({ autoLogin: opened.autoLogin, url: shown && shown.getURL() });
            } catch (e) {
              reject(e);
            }
          });
        }), [ids[k], ws]);
        assert.equal(r.autoLogin, 'logged-in', `${k} signed in again`);
        assert.equal(new URL(r.url).origin, urls[k], `on ${k}`);
        assert.equal(new URL(r.url).searchParams.get('ws'), ws, `${k}'s workspace shown once its load resolved`);
        await waitLoggedIn(content);
      }
      await waitFor(() => new URL(content.url()).searchParams.get('ws') === lab, 'back in the lab', 15_000);
      return `${lab} on ${urls.alpha}`;
    });

    await step('a PDF opened with Gamma: a second start hands it over and quits; it is read where it is, its page in Opened files of the workspace the window shows; the same file again opens that page', async () => {
      const lab = new URL(content.url()).searchParams.get('ws');
      const pdf = path.join(profile, 'Opened with Gamma.pdf');
      fs.writeFileSync(pdf, minimalPdf('Opened from the file manager', 'Saved in another reader'));
      const before = shownPage(content);
      assert.equal(await secondStart(profile, downloads, [pdf]), 0, 'the second start quits');
      const pageId = await waitFor(() => shownPage(content) !== before && shownPage(content), 'the window on the page', 60_000);
      assert.equal(new URL(content.url()).searchParams.get('ws'), lab, 'in the workspace the window showed');
      const page = await serverCall(app, `/api/blocks/${pageId}`, { ws: lab });
      assert.equal(page.content, 'Opened with Gamma', 'titled with the file\'s stem');
      const doc = page.properties.doc || {};
      assert(doc.hash, 'the page carries the file: ' + JSON.stringify(page.properties));
      assert.equal(doc.at, 'path', 'read where it is, not stored: ' + JSON.stringify(page.properties));
      assert(page.properties.parent_page, 'inside Opened files');
      const opened = await serverCall(app, `/api/blocks/${page.properties.parent_page}`, { ws: lab });
      assert.equal(opened.properties.opened_files, true, 'the one page Opened files: ' + JSON.stringify(opened.properties));
      await waitFor(async () => /is read from this computer, under Opened files/.test((await hook(app, (s) => s.notice())) || ''), 'the bar says where', 10_000);
      const kids = await serverCall(app, `/api/blocks/${page.id}/children`, { ws: lab });
      const blocks = JSON.stringify(kids);
      assert(blocks.includes('Saved in another reader'), 'the highlight the file carried was imported: ' + blocks.slice(0, 400));
      const [again] = await hook(app, (s, f) => s.openWithGamma([f]), pdf);
      assert.equal(again.page, page.id, 'the same page, not a second one: ' + JSON.stringify(again));
      const twice = await serverCall(app, `/api/blocks/${page.id}/children`, { ws: lab });
      assert.equal(JSON.stringify(twice).split('Saved in another reader').length, blocks.split('Saved in another reader').length, 'the highlight is not imported twice');
      openedPage = { id: page.id, ws: lab, doc: doc.hash };
      return `${page.id} in ${lab}`;
    });

    await step('an opened PDF carried to another server: the page asks, the shell asks where, the page goes to Beta with its PDF and highlights and opens there, and is in Recently deleted on Alpha', async () => {
      assert(openedPage, 'the step before opened a PDF');
      const { id, ws, doc } = openedPage;
      // One other server (Beta): asked about before it is reached, then its workspace when it has several, the account's own the first offered.
      await hook(app, (s) => { s.asked.length = 0; s.answers.push(0, 0); });
      await content.evaluate(([w, p]) => window.postMessage({ source: 'gamma-app', type: 'carry-to-server', ws: w, page: p }, window.location.origin), [ws, id]);
      await waitFor(async () => new URL(content.url()).origin === urls.beta && shownPage(content) === id, 'the window on the page in Beta', 60_000);
      await waitLoggedIn(content);
      // (the harness records a question with its detail)
      assert.match((await hook(app, (s) => s.asked))[0], /^Add “Opened with Gamma” to the library of Beta\? Its notes, highlights, comments and chat go with it/);
      await hook(app, (s) => { s.answers.length = 0; });
      await waitFor(async () => /“Opened with Gamma” is in .+ on Beta now/.test((await hook(app, (s) => s.notice())) || ''), 'the bar says where', 10_000);
      const there = await content.evaluate(async ([p, d]) => {
        const page = await (await fetch(`/api/blocks/${p}`, { credentials: 'same-origin' })).json();
        const kids = await (await fetch(`/api/blocks/${p}/children`, { credentials: 'same-origin' })).text();
        const pdf = await fetch(`/api/uploads/${d}.pdf`, { credentials: 'same-origin' });
        return { props: page.properties, kids, pdf: pdf.status };
      }, [id, doc]);
      assert.equal((there.props.doc || {}).hash, doc, 'the same PDF');
      assert.equal((there.props.doc || {}).at, 'store', 'no file of this computer read there: ' + JSON.stringify(there.props));
      assert.equal(there.props.parent_page, undefined, 'out of Opened files: ' + JSON.stringify(there.props));
      assert.equal(there.pdf, 200, 'its bytes stored there');
      assert(there.kids.includes('Saved in another reader'), 'its highlight came with it');
      await hook(app, (s, a) => s.openServer(a), ids.alpha);
      await waitLoggedIn(content);
      await assert.rejects(serverCall(app, `/api/blocks/${id}`, { ws }), /page is in Recently deleted/, 'out of the library on Alpha');
      const trash = await serverCall(app, '/api/trash', { ws });
      assert((trash.pages || []).some((p) => p.id === id), 'in Recently deleted on Alpha, for a restore: ' + JSON.stringify(trash).slice(0, 400));
      openedPage = null;
      return `${id} → Beta, ${doc}.pdf stored there`;
    });

    await step('a folder opened with Gamma: a second start hands over a directory, which becomes a work folder of the workspace the window shows, paused and choosing, the window on its tracking dialog; opened again while choosing, the dialog comes back', async () => {
      const ws = await waitFor(() => new URL(content.url()).searchParams.get('ws'), 'the window shows a workspace', 15_000);
      const dir = path.join(profile, 'Paper');
      fs.mkdirSync(path.join(dir, 'sub'), { recursive: true });
      fs.writeFileSync(path.join(dir, 'main.tex'), '\\documentclass{article}\n\\begin{document}\nOpened from the file manager.\n\\end{document}\n');
      fs.writeFileSync(path.join(dir, 'sub', 'idea.md'), '# Idea\n\n- a thought kept in a subdirectory\n');
      fs.writeFileSync(path.join(dir, 'data.csv'), 'a,b\n1,2\n');
      const loads = loadsOf(content);
      try {
        assert.equal(await secondStart(profile, downloads, [dir]), 0, 'the second start quits');
        const link = await waitFor(async () => (await serverCall(app, '/api/folder-links')).links.find((l) => samePath(l.dest, dir)),
          'the work folder at that path', 60_000);
        assert.deepEqual([link.workspace_id, link.pull, link.push, link.choosing], [ws, true, 'everything', true], JSON.stringify(link));
        assert(link.status && link.status.paused_at, 'paused until its files are chosen: ' + JSON.stringify(link.status));
        const dialog = trackDialog(content);
        await dialog.waitFor({ timeout: 30_000 });
        assert(loaded(loads, (q) => q.get('track') === link.id && q.get('ws') === ws), 'the shell loaded its tracking dialog: ' + loads.seen.join(' '));
        assert(!new URL(content.url()).searchParams.has('track'), 'the hand-over leaves the address');
        const links = (await serverCall(app, '/api/folder-links')).links;
        assert(!links.some((l) => samePath(l.dest, ROOT)), 'no work folder at the app\'s own directory, which a dev start names after Playwright\'s switches');
        // away from the dialog, then the same folder again while it chooses: the dialog comes back, no second vault
        await hook(app, (s, w) => s.openGammaWorkspace(w), ws);
        await waitFor(async () => !(await dialog.isVisible()), 'the dialog gone', 15_000);
        const [again] = await hook(app, (s, d) => s.openWithGamma([d]), dir);
        assert.equal(again.track, link.id, JSON.stringify(again));
        assert.equal(await hook(app, (s) => s.notice()), '“Paper” waits for its files to be chosen.');
        await dialog.waitFor({ timeout: 30_000 });
        assert.equal((await serverCall(app, '/api/folder-links')).links.filter((l) => samePath(l.dest, dir)).length, 1, 'still one vault there');
        workFolder = { id: link.id, folder: link.folder_id, dir, ws };
        return `${link.id} at ${link.dest} in ${ws}`;
      } finally {
        loads.stop();
      }
    });

    await step('files of a work folder opened with Gamma: after Save a tracked .tex opens its page in the code view, a subdirectory opens its folder, an untracked .csv opens Choose files… with it ticked', async () => {
      assert(workFolder, 'the step before made a work folder');
      const { id, dir, ws } = workFolder;
      // its Save, as the dialog sends it: the preset (PDFs and notes) and the LaTeX sources, which the preset leaves out
      const saved = await serverCall(app, `/api/folder-links/${id}/selection`, {
        ws, method: 'PUT', body: { selection: { base: 'preset', changes: [{ format: 'latex', on: true }] }, base_hash: '' },
      });
      assert.equal(saved.choosing, false, JSON.stringify(saved));
      const tex = path.join(dir, 'main.tex');
      await waitFor(() => hook(app, (s, f) => s.whereIs(f).then((a) => a.page || ''), tex), 'its first round read main.tex', 60_000, 500);
      const loads = loadsOf(content);
      try {
        const [opened] = await hook(app, (s, f) => s.openWithGamma([f]), tex);
        assert(opened.page && opened.workspace === ws, JSON.stringify(opened));
        await waitFor(() => shownPage(content) === opened.page, 'the window on the .tex page', 30_000);
        const doc = (await serverCall(app, `/api/blocks/${opened.page}`, { ws })).properties.doc || {};
        assert.equal(doc.format, 'text', 'a text document, in the code view: ' + JSON.stringify(doc));
        // its subdirectory: that directory's folder, not the work folder's
        const [sub] = await hook(app, (s, d) => s.openWithGamma([d]), path.join(dir, 'sub'));
        assert(sub.folder && sub.folder !== workFolder.folder, JSON.stringify(sub));
        await waitFor(() => loaded(loads, (q) => q.get('folder') === sub.folder), 'the window at its folder', 15_000);
        // a file it does not track: Choose files…, that file ticked there
        const [csv] = await hook(app, (s, f) => s.openWithGamma([f]), path.join(dir, 'data.csv'));
        assert.deepEqual([csv.track, csv.tick], [id, 'data.csv'], JSON.stringify(csv));
        assert.equal(await hook(app, (s) => s.notice()), 'data.csv is not tracked by “Paper” yet: tick it and save to open it.');
        await waitFor(() => loaded(loads, (q) => q.get('track') === id && q.get('tick') === 'data.csv'), 'the shell loaded track= with tick=', 15_000);
        const dialog = trackDialog(content);
        await dialog.waitFor({ timeout: 30_000 });
        await waitFor(() => dialog.getByRole('checkbox', { name: /data\.csv/ }).isChecked(), 'data.csv ticked in the dialog', 30_000);
        assert(!new URL(content.url()).searchParams.has('tick'), 'the tick leaves the address');
        await hook(app, (s, w) => s.openGammaWorkspace(w), ws); // off the dialog, unsaved
        await hook(app, (s, [server, l]) => s.removeVault(server, l, 'keep'), [ids.alpha, id]);
        return `main.tex → ${opened.page}, sub/ → folder ${sub.folder}, data.csv ticked`;
      } finally {
        loads.stop();
        workFolder = null;
      }
    });

    await step('a Markdown file opened with Gamma outside any vault asks; Just view it reads it where it is under Opened files, a text document in the code view, and the bar says how to edit it', async () => {
      const md = path.join(profile, 'loose', 'Lone notes.md');
      fs.mkdirSync(path.dirname(md), { recursive: true });
      fs.writeFileSync(md, '# Lone notes\n\nRead where it lies.\n');
      await hook(app, (s) => { s.asked.length = 0; s.answers.length = 0; }); // the harness answers Just view it
      const [got] = await hook(app, (s, f) => s.openWithGamma([f]), md);
      assert(got.page, JSON.stringify(got));
      assert(/To edit it as a note, open it with Gamma again and pick Edit in Gamma\.$/.test(await hook(app, (s) => s.notice())), 'the bar says how to edit it');
      const question = (await hook(app, (s) => s.asked.slice())).find((q) => q.startsWith('Edit Lone notes.md in Gamma?'));
      assert(question && question.includes(`Edit in Gamma makes ${path.dirname(md)} a work folder`), 'asked, naming its directory: ' + question);
      await waitFor(() => shownPage(content) === got.page, 'the window on its page', 30_000);
      const page = await serverCall(app, `/api/blocks/${got.page}`, { ws: got.workspace });
      const doc = page.properties.doc || {};
      assert.deepEqual([doc.format, doc.ext, doc.at], ['text', '.md', 'path'], 'read where it is, a text document: ' + JSON.stringify(doc));
      const parent = await serverCall(app, `/api/blocks/${page.properties.parent_page}`, { ws: got.workspace });
      assert.equal(parent.properties.opened_files, true, 'inside Opened files');
      assert(!(await serverCall(app, '/api/folder-links')).links.some((l) => samePath(l.dest, path.dirname(md))), 'no work folder made');
      return `${got.page} in ${got.workspace}: ${doc.format} ${doc.ext}`;
    });

    await step('Edit in Gamma makes a work folder of the Markdown file\'s repository, the dialog has the file ticked, and after Save the note opens', async () => {
      const repo = path.join(profile, 'notes-repo');
      fs.mkdirSync(path.join(repo, '.git'), { recursive: true });
      const md = path.join(repo, 'docs', 'notes.md');
      fs.mkdirSync(path.dirname(md), { recursive: true });
      fs.writeFileSync(md, '# Notes\n\n- a first line\n');
      const loads = loadsOf(content);
      let link = null;
      try {
        await hook(app, (s) => { s.asked.length = 0; s.answers.length = 0; s.answers.push(0); }); // Edit in Gamma
        const [got] = await hook(app, (s, f) => s.openWithGamma([f]), md);
        await hook(app, (s) => { s.answers.length = 0; });
        assert.equal(got.tick, 'docs/notes.md', JSON.stringify(got));
        const question = (await hook(app, (s) => s.asked.slice())).find((q) => q.startsWith('Edit notes.md in Gamma?'));
        assert(question && question.endsWith(`${repo} is the repository notes.md is in.`), 'asked, naming the repository: ' + question);
        link = (await serverCall(app, '/api/folder-links')).links.find((l) => l.id === got.track);
        assert(link && samePath(link.dest, repo) && link.choosing, 'a work folder at the repository\'s root, choosing: ' + JSON.stringify(link));
        await waitFor(() => loaded(loads, (q) => q.get('track') === link.id && q.get('tick') === 'docs/notes.md'), 'the shell loaded tick=', 15_000);
        const dialog = trackDialog(content);
        await dialog.waitFor({ timeout: 30_000 });
        assert(!new URL(content.url()).searchParams.has('tick'), 'the tick leaves the address');
        await waitFor(() => dialog.getByRole('checkbox', { name: /notes\.md/ }).isChecked(), 'notes.md ticked in the dialog', 30_000);
        const save = dialog.getByRole('button', { name: /^(Save|保存)$/ });
        await waitFor(() => save.isEnabled(), 'Save, once the summary says what the choice comes to', 30_000);
        await save.click();
        const ws = link.workspace_id;
        const note = await waitFor(() => serverCall(app, `/api/folder-links/${link.id}/page?path=${encodeURIComponent('docs/notes.md')}`, { ws })
          .then((r) => r.page), 'the note\'s page', 60_000, 500);
        await waitFor(() => shownPage(content) === note, 'the window lands on the note', 60_000);
        // an edit made in Gamma reaches the file with the round
        await serverCall(app, '/api/blocks', { ws, method: 'POST', body: { parent_id: note, content: 'An edit made in Gamma' } });
        await hook(app, (s, [server, l]) => s.syncVault(server, l), [ids.alpha, link.id]);
        await waitFor(() => fs.readFileSync(md, 'utf8').includes('An edit made in Gamma'), 'the edit in the file', 30_000, 500);
        return `${link.id} at ${link.dest}: ${note}`;
      } finally {
        loads.stop();
        if (link) await hook(app, (s, [server, l]) => s.removeVault(server, l, 'keep'), [ids.alpha, link.id]).catch(() => {});
      }
    });

    await step('remote server: a URL, loads without auto-login', async () => {
      ids.remote = await hook(app, (s, url) => s.registry.addRemote('Alpha by URL', url).id, urls.alpha);
      await hook(app, (s, id) => s.openServer(id), ids.remote);
      await waitFor(async () => new URL(content.url()).origin === urls.alpha, 'remote loaded');
      const cur = await hook(app, (s) => s.current());
      assert.equal(cur.type, 'remote');
      await waitFor(async () => (await bar.textContent('#wsName')).trim() === 'Alpha by URL', 'bar shows remote');
      // A bare address gets http:// on the local network, and the address can be edited.
      const port = new URL(urls.alpha).port;
      const bareId = await hook(app, (s, u) => s.registry.addRemote('Bare', u).id, `localhost:${port}`);
      assert.equal(await hook(app, (s, id) => s.registry.get(id).url, bareId), `http://localhost:${port}`, 'http:// assumed for a local address');
      assert.equal(await hook(app, (s) => s.registry.withScheme('gamma.example.com')), 'https://gamma.example.com', 'https:// for a name out on the internet');
      await hook(app, (s, id) => s.registry.setRemoteUrl(id, 'https://example.org:8443/'), bareId);
      assert.equal(await hook(app, (s, id) => s.registry.get(id).url, bareId), 'https://example.org:8443');
      await hook(app, (s, id) => s.registry.remove(id), bareId);
      return cur.url;
    });

    await step('a self-signed server is trusted by its fingerprint: the card shows it, only the card\'s record is pinned, a changed one is asked about, a forgotten one is no longer answered for', async () => {
      const pairs = [selfSigned(profile), selfSigned(profile)];
      const port = await closedPort();
      const origin = `https://127.0.0.1:${port}`;
      let nas = null;
      const serve = (pair) => new Promise((resolve) => {
        const s = https.createServer({ cert: pair.cert, key: pair.key }, (_req, res) => {
          res.writeHead(200, { 'Content-Type': 'text/html', Connection: 'close' });
          res.end('<!doctype html><title>Self-signed NAS</title><p>A NAS with a certificate of its own</p>');
        });
        s.listen(port, '127.0.0.1', () => resolve(s));
      });
      const stop = () => new Promise((resolve) => {
        if (!nas) return resolve();
        nas.closeAllConnections();
        nas.close(() => resolve());
        nas = null;
      });
      const title = () => content.textContent('#failTitle').catch(() => '');
      // From the launcher, as a person opens it: a refusal comes back as its card.
      const openFromLauncher = async () => {
        await hook(app, (s) => s.loadLauncher());
        const card = content.locator('.card', { hasText: 'Self-signed' });
        await card.waitFor({ timeout: 15_000 });
        await card.locator('button', { hasText: 'Open' }).click();
      };
      nas = await serve(pairs[0]);
      const id = await hook(app, (s, url) => s.registry.addRemote('Self-signed', url).id, origin);
      try {
        await openFromLauncher();
        await waitFor(async () => (await title()) === '127.0.0.1\'s certificate is not from an authority this computer trusts.', 'the card', 30_000);
        const shown = await content.textContent('#failCert');
        assert(shown.includes(grouped(digestOf(pairs[0].cert))), 'the fingerprint Node computed: ' + shown);
        // a click carrying another record's nonce pins nothing: the card comes back, saying why
        assert.equal(await hook(app, (s, [i, sha]) => s.trustCert(i, 'stale', sha), [id, digestOf(pairs[0].cert)]), false);
        await waitFor(async () => (await content.textContent('#failNote').catch(() => '')).includes('The certificate changed while you were looking at it.'),
          'the card again', 15_000);
        assert.deepEqual(await hook(app, (s) => s.registry.getPins()), {});
        await content.locator('#failActs button', { hasText: 'Trust this certificate' }).click();
        await waitFor(() => content.url().startsWith(origin), 'the page loads', 30_000);
        assert.equal((await hook(app, (s) => s.registry.getPins()))[origin].sha256, digestOf(pairs[0].cert));

        // the server's certificate renewed: changed, trusted again once asked
        await stop();
        nas = await serve(pairs[1]);
        await openFromLauncher();
        await waitFor(async () => (await title()) === 'The certificate of 127.0.0.1 changed.', 'the changed card', 30_000);
        await hook(app, (s) => s.answers.push(0));
        await content.locator('#failActs button', { hasText: 'Trust the new certificate' }).click();
        await waitFor(() => content.url().startsWith(origin), 'the page loads with the new certificate', 30_000);
        const asked = (await hook(app, (s) => s.asked.slice())).find((q) => q.startsWith('Trust the new certificate of 127.0.0.1?'));
        assert(asked && asked.includes(grouped(digestOf(pairs[1].cert))), 'asked with the new fingerprint: ' + asked);

        // forgotten: no pin, and the verify proc no longer answers for it (Chromium keeps
        // what it accepted for the session: the card comes back at the next start)
        await hook(app, (s, i) => s.forgetCert(i), id);
        assert.deepEqual(await hook(app, (s) => s.registry.getPins()), {});
        const answer = await hook(app, (s, pem) => new Promise((resolve) => s.verifyProc({ hostname: '127.0.0.1',
          verificationResult: 'net::ERR_CERT_AUTHORITY_INVALID', certificate: { data: pem } }, resolve)), pairs[1].cert);
        assert.equal(answer, -3, 'Chromium\'s own refusal');
        return `${origin} ${digestOf(pairs[1].cert).slice(0, 12)}…`;
      } finally {
        await stop();
        await hook(app, (s, i) => s.forgetCert(i).then(() => s.registry.remove(i)), id).catch(() => {});
        // the window back on the remote the next step clones from
        await hook(app, (s, i) => s.openServer(i), ids.remote).catch(() => {});
      }
    });

    await step('clone: the "clone" chip on a remote row makes the clone; the rows then cross-link', async () => {
      // On the remote (Alpha by URL — same server, so the session is there):
      // the personal workspace row carries the "clone" chip on hover. Named
      // from the session, not from the workspace shown: the page opens the
      // one this address used last (E2E lab), and the shell reports the
      // personal one only until the page writes ?ws= into its URL.
      await waitFor(async () => hook(app, (s) => s.gamma()), 'workspaces read off the remote', 15_000);
      const origWs = (await serverCall(app, '/api/session')).default_workspace;
      assert(origWs, 'the session names the personal workspace');
      await bar.click('#wsBtn');
      const row = bar.locator(`#menu .wsItem[data-ws="${origWs}"]`);
      await row.waitFor({ timeout: 15_000 });
      await row.hover();
      await row.locator('.rowAct[data-act="keep"]').click();
      // The copy lands on the first local server (Alpha itself here) and the
      // window moves to it: to the workspace the registry maps, since Alpha's
      // page first restores the one it showed last (E2E lab).
      await waitFor(async () => {
        const c = await hook(app, (s) => s.current());
        const mapped = await hook(app, (s) => s.registry.load().mirrors);
        return c && c.type === 'local' && mapped.length === 1 && new URL(content.url()).searchParams.get('ws') === mapped[0].workspace;
      }, 'the window moved to the copy', 30_000);
      const copyWs = new URL(content.url()).searchParams.get('ws');
      const mirrors = await hook(app, (s) => s.registry.load().mirrors);
      assert.equal(mirrors.length, 1, 'the registry maps the copy: ' + JSON.stringify(mirrors));
      assert.equal(mirrors[0].server, ids.alpha);
      assert.equal(mirrors[0].workspace, copyWs);
      assert.equal(mirrors[0].remoteWs, origWs);
      await waitLoggedIn(content);
      // The clone's row reads "clone" and its "origin" chip opens the origin…
      await bar.click('#wsBtn');
      const copyRow = bar.locator(`#menu .wsItem[data-ws="${copyWs}"]`);
      await copyRow.waitFor({ timeout: 15_000 });
      assert((await copyRow.textContent()).includes('clone'), 'row reads clone');
      await copyRow.locator('.rowAct.on[data-act="original"]').click();
      await waitFor(async () => {
        const c = await hook(app, (s) => s.current());
        return c && c.id === ids.remote && new URL(content.url()).searchParams.get('ws') === origWs;
      }, 'back on the original', 30_000);
      // …and on the remote the origin's row now shows "open clone" instead of "clone": it opens the clone.
      await bar.click('#wsBtn');
      const again = bar.locator(`#menu .wsItem[data-ws="${origWs}"] .rowAct.on[data-act="copy"]`);
      await again.waitFor({ timeout: 15_000 });
      await again.click();
      await waitFor(async () => {
        const c = await hook(app, (s) => s.current());
        return c && c.id === ids.alpha && new URL(content.url()).searchParams.get('ws') === copyWs;
      }, 'the copy opened from the remote row', 30_000);
      // A second "clone" of the same workspace makes no second clone: it opens the existing one.
      await hook(app, (s, id) => s.openServer(id), ids.remote);
      await waitFor(async () => {
        const c = await hook(app, (s) => s.current());
        const g2 = await hook(app, (s) => s.gamma());
        return c && c.id === ids.remote && g2 && g2.list.some((w) => w.id === origWs);
      }, 'remote again, workspaces read', 15_000);
      await hook(app, (s, id) => s.keepOffline(id), origWs);
      assert.equal((await hook(app, (s) => s.registry.load().mirrors)).length, 1, 'still one copy');
      assert.equal(new URL(content.url()).searchParams.get('ws'), copyWs);
      // The sync panel lists the clone with its state, and the button sums it up.
      const cloneRow = await keptRow(app, bar, 'clone', '(clone)');
      assert((await cloneRow.locator('.state').textContent()).includes('Clone of'), 'the clone with its origin');
      assert(['ok', 'busy', 'new', 'pending'].includes(await bar.locator('#syncBtn').getAttribute('data-state')), 'the button sums it up');
      if (process.platform !== 'darwin') {
        // The window's own controls are drawn over the bar's right end: nothing of the bar's may sit under them.
        const free = await bar.evaluate(() => innerWidth - document.getElementById('btnReload').getBoundingClientRect().right);
        assert(free >= 138, `the window controls' space stays free (${free} px)`);
      }
      // "pause" detaches the clone, the panel staying open, its sync shut; "resume" reattaches it.
      await cloneRow.locator('[data-act="pause"]').click();
      await waitFor(async () => (await cloneRow.locator('.state').textContent()).startsWith('Detached'), 'shown paused', 15_000);
      await panelStaysOpen(app, bar);
      assert.equal(await cloneRow.locator('[data-act="sync"]').getAttribute('aria-disabled'), 'true', 'no sync while detached');
      await cloneRow.locator('[data-act="resume"]').click();
      await waitFor(async () => (await cloneRow.locator('[data-act="pause"]').count()) === 1, 'shown resumed', 30_000);
      await panelStaysOpen(app, bar);
      await cloneRow.locator('[data-act="sync"]').click();
      await panelStaysOpen(app, bar);
      await bar.keyboard.press('Escape');
      await menuClosed(bar);
      return `${origWs} → copy ${copyWs} on Alpha, paused and resumed`;
    });

    await step('synced vault of a folder: the sync panel\'s chooser on a local server asks for the directory, the dialog says what the first round does, Sync pulls the folder there', async () => {
      await hook(app, (s, id) => s.openServer(id), ids.alpha);
      await waitLoggedIn(content);
      const ws = (await waitFor(async () => hook(app, (s) => s.gamma()), 'workspaces read', 15_000)).current;
      await makeFolderWithPage(app, ws, 'e2efolder1', 'a0', 'Kept lab', 'Kept paper');
      await hook(app, (s, w) => s.openGammaWorkspace(w), ws); // the panel's chooser lists the open workspace's folders
      await waitLoggedIn(content);
      const kept = path.join(profile, 'kept');
      fs.mkdirSync(kept, { recursive: true });
      const choice = await chooserRow(bar, 'Kept lab');
      assert((await choice.textContent()).includes('sync…'), 'not synced yet');
      await choice.click();
      // the picker answers GAMMA_SHELL_PICK_DIR; the dialog comes up in the panel, pulling and pushing nothing
      await bar.locator('#syncPanel #vaultAsk').waitFor({ timeout: 30_000 });
      assert.equal(await bar.locator('#vaultAsk .seg [data-push="off"]').getAttribute('aria-pressed'), 'true', 'push starts off for a folder with pages');
      const said = await syncInDialog(bar, /^Pull 1 page into .+; nothing there is pushed\./);
      await menuClosed(bar);
      const md = path.join(kept, 'Kept paper.md');
      await waitFor(() => fs.existsSync(md), 'the note file is written', 30_000, 500);
      const { links } = await hook(app, (s, w) => s.listFolders(w), ws);
      assert.equal(links.length, 1, 'one vault');
      assert.deepEqual([links[0].pull, links[0].push], [true, 'off'], 'as the dialog said');
      assert.equal(fs.realpathSync(links[0].dest).toLowerCase(), fs.realpathSync(kept).toLowerCase(), 'synced where the picker said');
      await waitFor(async () => /is synced with/.test((await hook(app, (s) => s.notice())) || ''), 'the bar said so', 5_000);
      // The chooser now shows the folder synced; the panel lists it under Synced vaults, and "sync" runs a round.
      const again = await chooserRow(bar, 'Kept lab');
      assert((await again.textContent()).includes('synced'), 'shown as synced');
      await bar.keyboard.press('Escape');
      await menuClosed(bar);
      const row = await keptRow(app, bar, 'vault', 'Kept lab');
      assert.equal(await bar.locator('#syncPanel .menuLabel', { hasText: 'Synced vaults' }).count(), 1, 'under its group');
      assert((await row.getAttribute('title')).includes(`${fs.realpathSync(kept).split(path.sep).pop()} ⇄ Alpha · Kept lab`), 'path ⇄ remote · folder in its tooltip');
      for (const act of ['pause', 'sync', 'more']) assert(await row.locator(`[data-act="${act}"]`).isVisible(), `"${act}" shown without hovering`);
      assert.equal(await row.locator('[data-act="push"]').count(), 0, 'the push switch is the vault view\'s, not the row\'s');
      await row.locator('[data-act="sync"]').click();
      await waitFor(async () => /is up to date/.test((await hook(app, (s) => s.notice())) || ''), 'the sync reported', 15_000);
      await panelStaysOpen(app, bar);
      // "pause" leaves the panel open on the paused row, whose "resume" starts it again.
      await row.locator('[data-act="pause"]').click();
      await waitFor(async () => (await row.locator('.state').textContent()).startsWith('Paused'), 'shown paused', 15_000);
      await panelStaysOpen(app, bar);
      const linkOf = async () => (await hook(app, (s, w) => s.listFolders(w), ws)).links[0];
      assert((await linkOf()).status.paused_at, 'paused on the server');
      await row.locator('[data-act="resume"]').click();
      await waitFor(async () => (await row.locator('[data-act="pause"]').count()) === 1, 'shown resumed', 15_000);
      assert(!(await linkOf()).status.paused_at, 'resumed on the server');
      // its own view: Pull now runs a pulling round, the panel staying open
      await (await vaultAct(row, bar, 'pull-now')).click();
      await waitFor(async () => /is up to date/.test((await hook(app, (s) => s.notice())) || ''), 'the pull reported', 15_000);
      await panelStaysOpen(app, bar);
      await bar.keyboard.press('Escape');
      await menuClosed(bar);
      return `${links[0].dest}: ${said}`;
    });

    await step('synced vault from the library: a folder\'s "Sync with this computer…" asks for the directory and shows the dialog; asked again, it opens it; removed with its files to the trash', async () => {
      await hook(app, (s, id) => s.openServer(id), ids.alpha);
      await waitLoggedIn(content);
      const ws = (await waitFor(async () => hook(app, (s) => s.gamma()), 'workspaces read', 15_000)).current;
      await makeFolderWithPage(app, ws, 'e2efolder3', 'a2', 'Menu lab', 'Menu paper');
      const dir = path.join(profile, 'kept-menu');
      fs.mkdirSync(dir, { recursive: true });
      await hook(app, (s, d) => { process.env.GAMMA_SHELL_PICK_DIR = d; }, dir); // the picker's answer, this time
      await hook(app, (s, w) => s.openGammaWorkspace(w), ws);
      await waitLoggedIn(content);
      await content.locator('[data-guide="header.home"]').click();
      const row = content.locator('.fileList .folderRow', { hasText: 'Menu lab' });
      await row.waitFor({ timeout: 20_000 });
      // Gamma's page speaks this computer's language: the entry in either catalog.
      const sync = async () => {
        await row.click({ button: 'right' });
        await content.locator('.ctxMenuItem', { hasText: /Sync with this computer|同步/ }).click();
      };
      await sync();
      await syncInDialog(bar, /^Pull 1 page into /);
      const md = path.join(dir, 'Menu paper.md');
      await waitFor(() => fs.existsSync(md), 'the note file is written', 30_000, 500);
      await sleep(1100); // the shell takes one ask of a page a second (main.js PAGE_ASK_MS)
      await sync(); // synced already: its directory opens, no second vault
      await waitFor(async () => /already synced with/.test((await hook(app, (s) => s.notice())) || ''), 'the bar said so', 10_000);
      const mine = (await hook(app, (s, w) => s.listFolders(w), ws)).links.filter((l) => l.folder_id === 'e2efolder3');
      assert.equal(mine.length, 1, 'one vault');
      const opened = await hook(app, (s) => s.externalOpens[s.externalOpens.length - 1]);
      assert.equal(fs.realpathSync(opened).toLowerCase(), fs.realpathSync(dir).toLowerCase(), 'its directory opened');
      // Removing it with its files moves what the sync wrote to the vault's trash.
      await hook(app, (s, [server, id]) => s.removeVault(server, id, 'trash'), [mine[0].server, mine[0].id]);
      await waitFor(() => !fs.existsSync(md), 'the files went to the trash', 10_000);
      assert(fs.existsSync(path.join(dir, '.gamma', 'trash')), 'into .gamma/trash');
      return `${mine[0].dest}, then removed with its files in its trash`;
    });

    await step('synced vault from a remote: the chooser on a remote server makes the vault on the host — no clone, a read token for a vault that pushes nothing; "Remove the vault…" drops it', async () => {
      // On the remote (Alpha by URL: the same server, so the session is there).
      await hook(app, (s, id) => s.openServer(id), ids.remote);
      await waitFor(async () => (await hook(app, (s) => s.current())).id === ids.remote && new URL(content.url()).origin === urls.alpha, 'remote open', 15_000);
      await waitLoggedIn(content);
      const ws = (await waitFor(async () => hook(app, (s) => s.gamma()), 'workspaces read off the remote', 15_000)).current;
      await makeFolderWithPage(app, ws, 'e2efolder2', 'a1', 'Far lab', 'Far paper');
      await hook(app, (s, w) => s.openGammaWorkspace(w), ws); // the panel's chooser lists the open workspace's folders
      await waitLoggedIn(content);
      const tokensOf = async () => (await serverCall(app, '/api/integrations/tokens', { ws })).tokens;
      const before = (await tokensOf()).length;
      const far = path.join(profile, 'kept-far');
      fs.mkdirSync(far, { recursive: true });
      await hook(app, (s, dir) => { process.env.GAMMA_SHELL_PICK_DIR = dir; }, far); // the picker's answer, this time
      const mirrorsBefore = (await hook(app, (s) => s.registry.load().mirrors)).length;
      const choice = await chooserRow(bar, 'Far lab');
      assert((await choice.textContent()).includes('sync…'), 'not synced yet');
      assert((await choice.getAttribute('title')).includes('local server on this computer'), 'the tooltip names who keeps it');
      await choice.click();
      await bar.locator('#syncPanel #vaultAsk').waitFor({ timeout: 30_000 });
      assert(await bar.locator('#vaultAsk .seg [data-push="everything"]').isDisabled(), 'a remote\'s folder pushes PDFs at most');
      await syncInDialog(bar, /^Pull 1 page into /);
      await menuClosed(bar);
      const md = path.join(far, 'Far paper.md');
      await waitFor(() => fs.existsSync(md), 'the note file is written by the host', 30_000, 500);
      const { links } = await hook(app, (s, w) => s.listFolders(w), ws);
      assert.equal(links.length, 1, 'one vault for this remote workspace');
      assert.equal(links[0].remote_url, urls.alpha, 'the vault reads the remote');
      assert(links[0].token_id && !links[0].token, 'with a token minted there, never shown');
      assert.equal((await hook(app, (s) => s.registry.load().mirrors)).length, mirrorsBefore, 'no clone was made');
      assert.equal(await hook(app, (s) => s.registry.getSettings().folderHost), ids.alpha, 'the host is remembered for launch');
      const tokens = await tokensOf();
      assert.equal(tokens.length, before + 1, `one token minted: ${JSON.stringify(tokens.map((t) => [t.name, t.scope]))}`);
      assert(tokens.some((t) => t.id === links[0].token_id && t.scope === 'read' && /Far lab/.test(t.name)), 'read scope, named after the folder');
      // The chooser shows it synced; the vault's "Remove the vault…" drops it and revokes the token; the files stay.
      const shown = await chooserRow(bar, 'Far lab');
      assert((await shown.textContent()).includes('synced'), 'shown as synced');
      await bar.keyboard.press('Escape');
      await menuClosed(bar);
      const kept = await keptRow(app, bar, 'vault', 'Far lab');
      assert((await kept.getAttribute('title')).includes('⇄ Alpha by URL · Far lab'), 'its remote, by its name');
      await (await vaultAct(kept, bar, 'remove')).click(); // the question takes "keep the files" under the harness
      await menuClosed(bar);
      await waitFor(async () => (await hook(app, (s, w) => s.listFolders(w), ws)).links.length === 0, 'the vault is gone', 15_000);
      // The notice comes once the token is revoked and the host let go (removeVault's releaseRemote),
      // which the vault's going does not wait for.
      await waitFor(async () => /is no longer synced; its files stay/.test((await hook(app, (s) => s.notice())) || ''), 'the bar said so', 15_000);
      assert(fs.existsSync(md), 'the files stay');
      assert.equal((await tokensOf()).length, before, 'the token was revoked');
      assert.equal(await hook(app, (s) => s.registry.getSettings().folderHost), '', 'the host no longer starts at launch for it');
      return `${links[0].dest} from ${links[0].remote_url}, then removed`;
    });

    await step('synced vault of one file: a page pushed from a PDF on disk; a new version is pushed and the panel says so', async () => {
      await hook(app, (s, id) => s.openServer(id), ids.alpha);
      await waitLoggedIn(content);
      const ws = (await waitFor(async () => hook(app, (s) => s.gamma()), 'workspaces read', 15_000)).current;
      const page = await serverCall(app, '/api/blocks', { ws, method: 'POST', body: { parent_id: 'root', content: 'Followed draft' } });
      const docOf = async () => ((await serverCall(app, `/api/blocks/${page.id}`, { ws })).properties.doc || {}).hash || '';
      // A build's output, dated a while back so that it reads as finished
      // (settled: a few seconds old, %%EOF at its end): its hash.
      const pdf = path.join(profile, 'build', 'main.pdf');
      fs.mkdirSync(path.dirname(pdf), { recursive: true });
      const build = (text) => {
        const bytes = minimalPdf(text);
        fs.writeFileSync(pdf, bytes);
        const past = new Date(Date.now() - 10_000);
        fs.utimesSync(pdf, past, past);
        return crypto.createHash('sha256').update(bytes).digest('hex').slice(0, 24);
      };
      const first = build('Followed draft, first build');
      await hook(app, (s, f) => { process.env.GAMMA_SHELL_PICK_FILE = f; }, pdf); // the file picker's answer
      const link = await hook(app, (s, [w, p]) => s.addFileVault(w, p), [ws, page.id]);
      assert.equal(link.kind, 'file');
      assert.equal(link.page_id, page.id);
      assert.equal(fs.realpathSync(link.dest).toLowerCase(), fs.realpathSync(pdf).toLowerCase(), 'the file the picker said');
      await waitFor(async () => /is pushed from main\.pdf: each new version becomes its PDF/.test((await hook(app, (s) => s.notice())) || ''), 'the bar said so', 5_000);
      await waitFor(async () => (await docOf()) === first, 'the page carries the first build', 30_000, 500);
      // Asked again for the same page, it shows the file instead.
      await hook(app, (s, [w, p]) => s.addFileVault(w, p), [ws, page.id]);
      const opened = await hook(app, (s) => s.externalOpens[s.externalOpens.length - 1]);
      assert.equal(fs.realpathSync(opened).toLowerCase(), fs.realpathSync(pdf).toLowerCase(), 'the file is shown');
      // A new build: the row's "sync" pushes it now (the server's own loop would, within its 30 s tick).
      const second = build('Followed draft, second build with more text');
      const row = await keptRow(app, bar, 'vault', 'Followed draft');
      assert.equal(await row.getAttribute('data-scope'), 'file');
      for (const act of ['pause', 'sync', 'more']) assert(await row.locator(`[data-act="${act}"]`).isVisible(), `"${act}" shown`);
      await row.locator('[data-act="sync"]').click();
      await waitFor(async () => (await docOf()) === second, 'the page carries the second build', 60_000, 500);
      await waitFor(async () => /main\.pdf( was pushed to Gamma|: nothing new to push)/.test((await hook(app, (s) => s.notice())) || ''), 'the push reported', 15_000);
      await panelStaysOpen(app, bar);
      await waitFor(async () => (await row.locator('.state').textContent()).startsWith('Pushed'), 'the row reads Pushed', 15_000);
      // "Remove the vault…" drops it; the file stays as it is.
      await (await vaultAct(row, bar, 'remove')).click();
      await menuClosed(bar);
      await waitFor(async () => {
        await hook(app, (s) => s.refreshKeeping());
        return !(await hook(app, (s) => s.keeping().items.some((i) => i.kind === 'vault' && i.scope === 'file')));
      }, 'the vault is gone', 15_000);
      assert(fs.existsSync(pdf), 'the file stays');
      assert.equal(await docOf(), second, 'the page keeps the last build');
      await waitFor(async () => /is no longer pushed from main\.pdf/.test((await hook(app, (s) => s.notice())) || ''), 'the bar said so', 5_000);
      return `${page.id} pushed from ${link.dest}: ${first}, then ${second}`;
    });

    await step('pushing from a folder\'s vault: the Push segment in the vault\'s view turns pushing its PDFs on and off, in words', async () => {
      const row = await keptRow(app, bar, 'vault', 'Kept lab');
      const pushOf = async () => {
        await hook(app, (s) => s.refreshKeeping());
        return (await hook(app, (s) => s.keeping().items.find((i) => i.kind === 'vault' && i.name === 'Kept lab'))).push;
      };
      await row.locator('[data-act="more"]').click();
      const seg = (word) => bar.locator(`#syncPanel #vaultView .seg [data-push="${word}"]`);
      await seg('off').waitFor({ timeout: 15_000 });
      assert.equal(await seg('off').getAttribute('aria-pressed'), 'true', 'off until asked for');
      await seg('pdfs').click();
      await waitFor(async () => (await seg('pdfs').getAttribute('aria-pressed')) === 'true', 'shown on', 15_000);
      await panelStaysOpen(app, bar);
      assert.equal(await pushOf(), 'pdfs', 'pushing PDFs, on the server');
      await waitFor(async () => /pushes the PDFs you change there/.test((await hook(app, (s) => s.notice())) || ''), 'the bar said so', 5_000);
      await seg('off').click();
      await waitFor(async () => (await seg('off').getAttribute('aria-pressed')) === 'true', 'shown off', 15_000);
      assert.equal(await pushOf(), 'off', 'off, on the server');
      await waitFor(async () => /no longer pushes/.test((await hook(app, (s) => s.notice())) || ''), 'the bar said so', 5_000);
      await bar.keyboard.press('Escape');
      await menuClosed(bar);
      return 'pdfs, then off';
    });

    await step('"Add a synced vault…" in the launcher: a directory becomes a work folder of the host\'s own workspace, its marker written and nothing tracked until its tracking dialog saves; the window goes to that dialog', async () => {
      const dir = path.join(profile, 'Reading');
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, 'Idea.md'), '# Idea\n\n- a first thought\n- a second one\n');
      await hook(app, (s, d) => { process.env.GAMMA_SHELL_PICK_DIR = d; }, dir);
      await hook(app, (s) => s.loadLauncher());
      content = await findPage(app, isLauncher);
      await content.click('#btnAddVault');
      await waitFor(() => fs.existsSync(path.join(dir, '.gamma', 'vault.json')), 'the marker is written', 30_000, 500);
      const vault = await waitFor(async () => {
        await hook(app, (s) => s.refreshKeeping());
        return hook(app, (s) => s.keeping().items.find((i) => i.kind === 'vault' && i.name === 'Reading'));
      }, 'the vault in the panel', 30_000);
      assert.deepEqual([vault.scope, vault.pull, vault.push, vault.work, vault.choosing, vault.text],
        ['folder', true, 'everything', true, true, 'Choosing files']);
      assert.equal(vault.server, ids.alpha, 'on the host, the first local server');
      assert.equal(await bar.locator('#syncPanel #vaultAsk').count(), 0, 'the bar asks nothing: the tracking dialog does');
      // The window is known by the dialog it shows, not by its address: the
      // page reads ?track= as it boots and takes it out of the address
      // (frontend/src/vault/useVault.js), often before the waits above end.
      content = await waitFor(async () => {
        for (const p of app.windows()) {
          if (p.url().startsWith('http://127.0.0.1') && await p.getByRole('dialog', { name: 'Choose files' }).isVisible()) return p;
        }
        return null;
      }, 'the window at the vault\'s tracking dialog', 30_000);
      const dialog = content.getByRole('dialog', { name: 'Choose files' });
      assert.equal(await dialog.locator('.importReviewHeader strong').innerText(), 'Reading', 'the dialog is the vault\'s directory');
      assert(!new URL(content.url()).searchParams.has('track'), 'the hand-over leaves the address');
      assert(!fs.existsSync(path.join(dir, '.gammaignore')), 'nothing chosen yet');
      // its Save, as the page sends it: the preset, its notes and PDFs added to the library
      const saved = await content.evaluate((id) => fetch(`/api/folder-links/${id}/selection`, {
        method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selection: { base: 'preset', changes: [] }, base_hash: '', library: true }),
      }).then(async (r) => ({ status: r.status, body: await r.json() })), vault.id);
      assert.equal(saved.status, 200, JSON.stringify(saved.body));
      assert.deepEqual([saved.body.choosing, saved.body.library], [false, true]);
      assert(fs.readFileSync(path.join(dir, '.gammaignore'), 'utf8').startsWith('# What Gamma tracks in this folder'), 'its .gammaignore');
      await waitFor(async () => (await rootTitles(content)).includes('Idea'), 'its note in the library', 30_000, 500);
      await hook(app, (s, [server, id]) => s.removeVault(server, id, 'keep'), [vault.server, vault.id]);
      assert(fs.existsSync(path.join(dir, 'Idea.md')), 'the note stays');
      assert(!fs.existsSync(path.join(dir, '.gamma', 'vault.json')), 'the marker goes with the vault');
      await hook(app, (s) => { delete process.env.GAMMA_SHELL_PICK_DIR; });
      await hook(app, (s, id) => s.openServer(id), ids.alpha); // the steps after start from Alpha
      await waitLoggedIn(content);
      return `${vault.dest}: its files chosen in Gamma, its note in the library`;
    });

    await step('background: with "keep running" on, closing the window leaves the servers up and the tray in place; Window → Show Gamma and the Dock icon bring it back over the same server', async () => {
      const live = Object.values(pids).filter(Boolean);
      assert(live.length, 'have sidecar pids');
      await hook(app, (s) => s.setBackground(true));
      assert(await hook(app, (s) => s.tray()), 'tray shown');
      // The menu's item as a click on it runs it; the Dock icon is `activate`.
      const showGamma = () => app.evaluate(({ Menu }) => {
        const windowMenu = Menu.getApplicationMenu().items.find((i) => i.label === 'Window');
        windowMenu.submenu.items.find((i) => i.label === 'Show Gamma').click();
      });
      const dock = () => app.evaluate(({ app: a }) => { a.emit('activate'); });
      for (const [how, reopen] of [['Window → Show Gamma', showGamma], ['the Dock icon', dock]]) {
        await hook(app, (s) => s.closeWindow());
        await waitFor(async () => !(await hook(app, (s) => s.hasWindow())), `window closed (${how})`);
        await sleep(1500);
        assert(live.every((p) => pidAlive(p)), 'sidecars still running');
        assert(await app.evaluate(({ app: a }) => a.isReady()), 'the app is still up');
        await reopen();
        bar = await findPage(app, isBar);
        content = await findPage(app, (u) => u.startsWith('http://127.0.0.1'), 90_000);
        await waitLoggedIn(content);
        assert.equal(await hook(app, (s) => s.current()?.id), ids.alpha, `${how}: the last server again`);
        assert.equal(await hook(app, (s, id) => s.sidecar.status(id)?.child.pid, ids.alpha), pids.alpha, `${how}: on the same sidecar`);
      }
      // A minimized window comes back from either.
      for (const [how, reopen] of [['Window → Show Gamma', showGamma], ['the Dock icon', dock]]) {
        await app.evaluate(({ BaseWindow }) => BaseWindow.getAllWindows()[0].minimize());
        await waitFor(() => app.evaluate(({ BaseWindow }) => BaseWindow.getAllWindows()[0].isMinimized()), `minimized (${how})`, 10_000);
        await reopen();
        await waitFor(() => app.evaluate(({ BaseWindow }) => !BaseWindow.getAllWindows()[0].isMinimized()), `${how} restores it`, 10_000);
      }
      if (!headed) await app.evaluate(({ BaseWindow }) => BaseWindow.getAllWindows()[0].minimize());
      await hook(app, (s) => s.setBackground(false));
      assert(!(await hook(app, (s) => s.tray())), 'tray gone when turned off with the window open');
      return `window closed and back by the menu and the Dock; sidecars ${live.join(', ')} kept running`;
    });

    await step('remote reachability dot: on for the live server, off for a dead URL', async () => {
      await bar.click('#wsBtn');
      await bar.click('#menuLauncher');
      await waitFor(() => isLauncher(content.url()), 'launcher shown');
      // The live one was just opened (recorded reachable) and gets re-probed
      // on the launcher's refresh.
      const live = content.locator('.card', { hasText: 'Alpha by URL' }).first();
      await live.locator('.dot.on').waitFor({ timeout: 15_000 });
      const port = await closedPort();
      const id = await hook(app, (s, url) => s.registry.addRemote('Dead dot', url).id, `http://127.0.0.1:${port}`);
      try {
        await hook(app, (s) => s.probeRemotes(true));
        const dead = content.locator('.card', { hasText: 'Dead dot' }).first();
        await dead.locator('.dot.off').waitFor({ timeout: 15_000 });
        assert.equal(await dead.locator('.dot').getAttribute('title'), 'server unreachable');
        // Same dots in the bar's dropdown.
        await bar.click('#wsBtn');
        await bar.locator(`#menu .item[data-id="${id}"] .dot.off`).waitFor({ timeout: 10_000 });
        await bar.locator(`#menu .item[data-id="${ids.remote}"] .dot.on`).waitFor({ timeout: 10_000 });
        await bar.keyboard.press('Escape');
        await waitFor(async () => (await bar.evaluate(() => document.getElementById('menu').hidden)), 'menu closed');
        const health = await hook(app, (s) => Object.fromEntries([...s.remoteHealth].map(([k, v]) => [k, v.ok])));
        assert.equal(health[ids.remote], true);
        assert.equal(health[id], false);
        return `live=on dead=off (probe cache ${Object.keys(health).length} entries)`;
      } finally {
        await hook(app, (s, id) => s.registry.remove(id, {}), id);
      }
    });

    await step('updater: disabled under test, state exposed through the shell', async () => {
      const u = await hook(app, (s) => s.update());
      assert.equal(u.status, 'unsupported', JSON.stringify(u));
      assert.equal(u.error, 'disabled');
      assert.equal(typeof u.current, 'string');
      const st = await content.evaluate(() => gammaShell.state());
      assert.equal(st.update.status, 'unsupported', 'bar state carries the updater');
      await waitFor(() => isLauncher(content.url()), 'still on the launcher');
      const row = await content.textContent('#updText');
      assert(/Not available in this build/.test(row), row);
      assert(await content.locator('#btnUpdate').isHidden(), 'no check button when unsupported');
      assert(await bar.locator('#btnUpdate').isHidden(), 'no update pill in the bar');
      // Help → Check for Updates…: its box is only recorded under test, with the answer that installs nothing.
      const before = await hook(app, (s) => s.asked.length);
      await app.evaluate(({ Menu }) => Menu.getApplicationMenu().getMenuItemById('check-updates').click());
      const box = await waitFor(async () => (await hook(app, (s) => s.asked.slice()))[before], 'the update box', 10_000);
      assert.equal(box, 'Updates are not available in this build. disabled');
      assert.equal((await hook(app, (s) => s.update())).status, 'unsupported', 'nothing installed');
      return `${u.status} (${u.error}), v${u.current}; the menu says: ${box}`;
    });

    await step('unreachable remote falls back to the launcher with the error', async () => {
      const port = await closedPort();
      const id = await hook(app, (s, url) => s.registry.addRemote('Dead', url).id, `http://127.0.0.1:${port}`);
      try {
        const err = await hook(app, async (s, id) => {
          try {
            await s.openServer(id);
            return null;
          } catch (e) {
            s.loadLauncher(e, id);
            return e.message;
          }
        }, id);
        assert(err, 'open rejected');
        await waitFor(() => isLauncher(content.url()), 'launcher shown');
        await content.waitForSelector('#fail:not([hidden])');
        const txt = await content.textContent('#failTitle');
        assert(/could not reach/i.test(txt), txt);
        // Chromium's own wording is kept, but as the detail, not the headline.
        assert(/ERR_CONNECTION_REFUSED/.test(await content.textContent('#failDetail')), 'raw error kept');
        assert(await content.locator('#failLogBox').isHidden(), 'no server log for a remote');
        return txt;
      } finally {
        await hook(app, (s, id) => s.registry.remove(id, {}), id);
      }
    });

    // A sidecar that dies during startup: the launcher explains it instead of
    // printing the tail of the log file (lib/startup.js + the #fail panel).
    await step('a server that will not start is explained, not dumped', async () => {
      const log = [
        'INFO:     127.0.0.1:37568 - "GET /api/session HTTP/1.1" 200 OK',
        'sqlite3.OperationalError: unable to open database file',
        '--- server exited (code 1) ---',
      ].join('\n');
      const d = await hook(app, (s, log) => s.startup.diagnose(log, 'exit'), log);
      assert.equal(d.action, 'data', JSON.stringify(d));
      assert(/could not open its database files/i.test(d.summary), d.summary);

      await hook(app, (s, arg) => {
        const e = new Error(arg.d.summary);
        e.startup = { ...arg.d, log: arg.log };
        s.loadLauncher(e, arg.id);
      }, { d, log, id: ids.alpha });
      await waitFor(() => isLauncher(content.url()), 'launcher shown');
      await content.waitForSelector('#fail:not([hidden])');
      assert(/could not open its database files/i.test(await content.textContent('#failTitle')), 'summary shown');
      assert(/unable to open database file/.test(await content.textContent('#failDetail')), "the server's own line shown");
      assert(/Open data folder/.test(await content.textContent('#failActs .primary')), 'the fix is offered first');
      assert(await content.locator('#failLog').isHidden(), 'the raw log stays folded away');
      // Its buttons reach the file manager, which the harness only records.
      const where = await hook(app, (s, id) => ({ data: s.registry.get(id).dataDir, log: s.logPath(id) }), ids.alpha);
      assert(fs.existsSync(where.log), 'the server has a log file');
      const seen = await hook(app, (s) => s.externalOpens.length);
      await content.click('#failActs .primary');
      await content.locator('#failActs button', { hasText: 'Show log file' }).click();
      await waitFor(async () => (await hook(app, (s) => s.externalOpens.slice())).slice(seen).length === 2, 'both recorded', 5_000);
      assert.deepEqual((await hook(app, (s) => s.externalOpens.slice())).slice(seen), [where.data, where.log], 'the data folder, then the log');
      await content.click('#failClose');
      assert(await content.locator('#fail').isHidden(), 'dismissable');
      return d.summary;
    });

    await step('navigation guard: foreign URLs open outside, the window stays', async () => {
      await hook(app, (s, id) => s.openServer(id), ids.alpha);
      await waitLoggedIn(content);
      await content.evaluate(() => {
        window.open('https://example.org/popup');
        window.open('ms-windows-store://downloadsandupdates'); // Settings → Server's Open Store
        window.open('ms-windows-store://pdp/?productid=X'); // any other Store link stays shut
        setTimeout(() => { location.href = 'https://example.com/leave'; }, 0);
      });
      await sleep(1200);
      assert.equal(new URL(content.url()).origin, urls.alpha, 'still on the server');
      const opened = await hook(app, (s) => s.externalOpens.slice());
      assert(opened.includes('https://example.org/popup'), 'window.open went external: ' + opened);
      assert(opened.includes('ms-windows-store://downloadsandupdates'), 'the Store updates page opened: ' + opened);
      assert(!opened.some((u) => u.startsWith('ms-windows-store://pdp')), 'no other Store link: ' + opened);
      assert(opened.includes('https://example.com/leave'), 'location change went external: ' + opened);
      return opened.join(', ');
    });

    await step('Gamma Cloud sign-in: the system browser signs in, the window gets the session through the loopback return', async () => {
      // A server of its own: the window ends up signed in as the cloud account.
      ids.cloud = await hook(app, (s) => s.registry.addLocal('Cloudy').id);
      await hook(app, (s, id) => s.openServer(id), ids.cloud);
      await waitLoggedIn(content);
      const origin = new URL(content.url()).origin;
      const seen = await hook(app, (s) => s.externalOpens.length);
      // What the login page's button and Settings' Link both do.
      await content.evaluate(() => { location.href = '/api/auth/cloud/start?next=%2F'; });
      const authorize = await waitFor(async () => (await hook(app, (s) => s.externalOpens.slice()))
        .slice(seen).find((u) => u.startsWith(`${cloud.issuer}/authorize?`)), 'the sign-in opened in the system browser', 15_000);
      assert.equal(new URL(content.url()).origin, origin, 'the window stays on its server');
      assert(/Finish signing in/.test(await hook(app, (s) => s.notice())), 'the bar says where to go');
      // The system browser: the account server sends it to the callback, the callback on to the app.
      const callback = (await fetch(authorize, { redirect: 'manual' })).headers.get('location');
      assert(callback.startsWith(`${origin}/api/auth/cloud/callback?`), callback);
      const answered = await fetch(callback, { redirect: 'manual' });
      assert.equal(answered.headers.get('set-cookie'), null, 'the browser gets no session');
      const back = answered.headers.get('location');
      assert(/^http:\/\/127\.0\.0\.1:\d+\/cloud-signin\?result=/.test(back), back);
      assert(/Signed in to Gamma/.test(await (await fetch(back)).text()), 'the tab says it is done');
      await waitLoggedIn(content, 'desk');
      assert.equal(new URL(content.url()).origin, origin);
      // Back to Alpha (auto-login signs the window back in), and the server goes.
      await hook(app, (s, id) => s.openServer(id), ids.alpha);
      await waitLoggedIn(content);
      // (its process gone first: on Windows a running server holds its databases open)
      await hook(app, async (s, id) => { await s.sidecar.stop(id); s.registry.remove(id, { deleteData: true }); }, ids.cloud);
      return back.split('?')[0];
    });

    await step('theme mirror: the shell chrome follows the page theme', async () => {
      const set = (t) => content.evaluate((t) => {
        if (t) { localStorage.setItem('gamma-theme', t); document.documentElement.setAttribute('data-theme', t); }
        else { localStorage.removeItem('gamma-theme'); document.documentElement.removeAttribute('data-theme'); }
      }, t);
      // A page that reports no theme (and a fresh profile, which has none
      // remembered) leaves the chrome on the OS scheme, never a dark window
      // on a light machine. The OS is pinned here so the check holds anywhere.
      await app.evaluate(({ nativeTheme }) => { nativeTheme.themeSource = 'light'; });
      const seen = [];
      for (const t of ['light', 'sepia', '']) {
        await set(t);
        const chrome = t || 'light'; // '' → the pinned OS scheme
        await waitFor(async () => (await hook(app, (s) => s.theme())) === chrome, `main theme=${chrome}`, 5_000);
        await waitFor(async () => (await bar.getAttribute('html', 'data-theme')) === chrome, `bar theme=${chrome}`, 5_000);
        seen.push(chrome);
      }
      await app.evaluate(({ nativeTheme }) => { nativeTheme.themeSource = 'system'; });
      await set('light');
      await waitFor(async () => (await hook(app, (s) => s.registry.getSettings().lastTheme)) === 'light', 'lastTheme persisted', 5_000);
      return seen.join(' → ') + ' → light (persisted)';
    });

    await step('launcher lists sizes, last-opened badge, painted in the mirrored theme', async () => {
      await bar.click('#btnHome'); // the logo: back to every server
      await waitFor(() => isLauncher(content.url()), 'launcher shown');
      await content.waitForSelector('.card');
      const alpha = content.locator('.card', { hasText: 'Alpha' }).first();
      const detail = await alpha.locator('.detail').textContent();
      assert(/\d+(\.\d+)? (KB|MB|GB) on disk/.test(detail), detail);
      assert(detail.includes(urls.alpha), 'the running server\'s address: ' + detail);
      assert.equal(await content.getAttribute('html', 'data-theme'), 'light');
      const badges = await content.locator('.badge.last').count();
      assert.equal(badges, 1, 'one last-opened badge');
      // The address to give the Gamma Connector: copied, and in the credentials.
      await alpha.locator('button[title="Copy address"]').click();
      await waitFor(async () => (await content.textContent('#status')).includes(`Copied ${urls.alpha}`), 'the copy reported', 5_000);
      assert.equal(await hook(app, (s) => s.copied()), urls.alpha, 'the address copied');
      // The card's folder and log buttons go to the file manager, recorded only.
      const where = await hook(app, (s, id) => ({ data: s.registry.get(id).dataDir, log: s.logPath(id) }), ids.alpha);
      const seen = await hook(app, (s) => s.externalOpens.length);
      await alpha.locator('button[title="Open data folder"]').click();
      await alpha.locator('button[title="Show server log"]').click();
      await waitFor(async () => (await hook(app, (s) => s.externalOpens.slice())).slice(seen).length === 2, 'both recorded', 5_000);
      assert.deepEqual((await hook(app, (s) => s.externalOpens.slice())).slice(seen), [where.data, where.log], 'the data folder, then the log');
      await alpha.locator('button[title="Show credentials"]').click();
      assert((await content.textContent('#credsText')).includes(`server:   ${urls.alpha}`), 'the credentials name the address');
      await content.click('#dlgCreds .uiBtn.primary');
      return detail;
    });

    await step('rename + remove from the launcher', async () => {
      const beta = content.locator('.card', { hasText: 'Beta' }).first();
      await beta.locator('button[title="Rename"]').click();
      await content.fill('#renameName', 'Beta renamed');
      await content.click('#btnRename');
      await content.locator('.card', { hasText: 'Beta renamed' }).waitFor();
      const dead = content.locator('.card', { hasText: 'Alpha by URL' }).first();
      await dead.locator('button[title="Remove server"]').click();
      await content.click('#btnRemoveWipe');
      await waitFor(async () => (await content.locator('.card', { hasText: 'Alpha by URL' }).count()) === 0, 'card gone');
      const names = await hook(app, (s) => s.registry.load().servers.map((w) => w.name));
      assert.deepEqual(names.sort(), ['Alpha', 'Beta renamed']);
      return names.join(', ');
    });

    await step('storage folder: change the root, existing servers move, data intact', async () => {
      const newRoot = path.join(profile, 'moved-root');
      const before = await hook(app, (s) => s.registry.load().servers.filter((w) => w.type === 'local').map((w) => w.dataDir));
      assert(before.length === 2, 'two local servers');
      const { r, seen } = await content.evaluate(async (dir) => {
        const seen = [];
        const off = gammaShell.onDataMove((m) => seen.push(m));
        const r = await gammaShell.setDataRoot(dir, { move: true });
        await new Promise((ok) => setTimeout(ok, 200));
        off();
        return { r, seen };
      }, newRoot);
      assert.deepEqual(r.moved.sort(), ['Alpha', 'Beta renamed'], JSON.stringify(r));
      // the launcher heard how far the copy was, up to all of it, then that it ended
      const last = seen.filter(Boolean).pop();
      assert(last && last.total > 0 && last.done === last.total, `progress up to the whole: ${JSON.stringify(seen.slice(-3))}`);
      assert.equal(seen[seen.length - 1], null, 'the move reported done');
      const after = await hook(app, (s) => s.registry.load().servers.filter((w) => w.type === 'local').map((w) => w.dataDir));
      for (const d of after) {
        assert(d.startsWith(newRoot + path.sep), `moved: ${d}`);
        assert(fs.existsSync(path.join(d, 'users.db')), `users.db in ${d}`);
      }
      for (const d of before) assert(!fs.existsSync(d), `old dir removed: ${d}`);
      assert.equal(await hook(app, (s) => s.registry.getSettings().dataRoot), newRoot);
      await waitFor(async () => (await content.textContent('#dataRootText')).trim() === newRoot, 'launcher shows the new root');
      assert.equal(await content.isHidden('#btnDataRootReset'), false, 'reset button visible');
      assert(!pidAlive(pids.alpha) && !pidAlive(pids.beta), 'old sidecars stopped for the move');
      await hook(app, (s, id) => s.openServer(id), ids.alpha);
      await waitLoggedIn(content);
      assert.equal(new URL(content.url()).origin, urls.alpha, 'the same address after the restart');
      const titles = await rootTitles(content);
      assert(titles.includes('E2E paper'), 'data intact after the move: ' + titles.join(' | '));
      pids.alpha = await hook(app, (s, id) => s.sidecar.status(id).child.pid, ids.alpha);
      pids.beta = null;
      return `${r.moved.length} moved to ${newRoot}`;
    });

    await step('quit: every sidecar stops, window bounds persist', async () => {
      await hook(app, (s, id) => s.openServer(id), ids.alpha);
      await waitLoggedIn(content);
      const live = Object.values(pids).filter(Boolean);
      assert(live.length, 'have sidecar pids');
      await app.close();
      await waitFor(() => live.every((p) => !pidAlive(p)), 'sidecars gone', 15_000, 500);
      const reg = JSON.parse(fs.readFileSync(path.join(profile, 'servers.json'), 'utf8'));
      assert(reg.windowBounds && reg.windowBounds.width > 0, 'bounds saved');
      assert.equal(reg.lastOpened, ids.alpha);
      return `pids ${live.join(', ')} exited; bounds ${reg.windowBounds.width}x${reg.windowBounds.height}`;
    });

    await step('relaunch reopens the last server with its data intact', async () => {
      app = await launch(profile, downloads);
      bar = await findPage(app, isBar);
      content = await findPage(app, (u) => u.startsWith('http://127.0.0.1'), 90_000);
      await waitLoggedIn(content);
      assert.equal(new URL(content.url()).origin, urls.alpha, 'the same address as before the quit');
      const titles = await rootTitles(content);
      assert(titles.includes('E2E paper'), 'data persisted: ' + titles.join(' | '));
      await waitFor(async () => (await bar.textContent('#wsName')).trim() === 'Alpha', 'bar shows Alpha');
      assert.equal(await bar.getAttribute('html', 'data-theme'), 'light', 'chrome painted in the persisted theme before the page reported');
      return titles.join(' | ');
    });

    await step('a PDF named at launch is read where it is in the last server once it is up', async () => {
      await app.close();
      const pdf = path.join(profile, 'Opened at launch.pdf');
      fs.writeFileSync(pdf, minimalPdf('Opened while the app was closed'));
      app = await launch(profile, downloads, [pdf]);
      content = await findPage(app, (u) => u.startsWith('http://127.0.0.1'), 90_000);
      // A page carrying this new file can only be open because the shell opened it.
      const ws = () => new URL(content.url()).searchParams.get('ws');
      const page = await waitFor(async () => {
        const id = shownPage(content);
        const b = id && ws() ? await serverCall(app, `/api/blocks/${id}`, { ws: ws() }) : null;
        return b && b.content === 'Opened at launch' && b;
      }, 'the window on the file\'s page', 90_000);
      assert.equal(new URL(content.url()).origin, urls.alpha, 'the last server');
      return `${page.id} in ${ws()}`;
    });

    await step('a directory named at a cold launch becomes a work folder once the app is up', async () => {
      await app.close();
      const dir = path.join(profile, 'Cold start');
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, 'draft.md'), '# Draft\n');
      // started as at login with the background on: a start that names a folder still shows the window
      const servers = path.join(profile, 'servers.json');
      const reg = JSON.parse(fs.readFileSync(servers, 'utf8'));
      fs.writeFileSync(servers, JSON.stringify({ ...reg, settings: { ...(reg.settings || {}), background: true } }));
      app = await launch(profile, downloads, [dir, '--hidden']);
      try {
        content = await findPage(app, (u) => u.startsWith('http://127.0.0.1'), 90_000);
        assert(await hook(app, (s) => s.hasWindow()), 'the window shows');
        const link = await waitFor(async () => (await serverCall(app, '/api/folder-links')).links.find((l) => samePath(l.dest, dir)),
          'the work folder at that path', 90_000);
        assert(link.choosing, JSON.stringify(link));
        const shown = await waitFor(async () => {
          for (const p of app.windows()) {
            if (p.url().startsWith('http://127.0.0.1') && await trackDialog(p).isVisible()) return p;
          }
          return null;
        }, 'the window on its tracking dialog', 60_000);
        assert.equal(new URL(shown.url()).origin, urls.alpha, 'on the last server');
        assert(!(await serverCall(app, '/api/folder-links')).links.some((l) => samePath(l.dest, ROOT)), 'no work folder at the app\'s own directory');
        await hook(app, (s, id) => s.removeVault(s.current().id, id, 'keep'), link.id);
        return `${link.id} at ${link.dest}`;
      } finally {
        await hook(app, (s) => s.setBackground(false)).catch(() => {});
      }
    });
  } finally {
    try {
      await app.close();
    } catch {}
    cloud.close();
    await sleep(500);
    if (!keep) {
      // A server still exiting can write into the profile as it goes.
      try {
        fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
      } catch (e) {
        console.error(`e2e: could not remove the profile ${profile}: ${e.message}`);
      }
    }
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} steps passed${failed.length ? ' — FAILED: ' + failed.map((f) => f.name).join('; ') : ''}`);
  process.exit(failed.length ? 1 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
