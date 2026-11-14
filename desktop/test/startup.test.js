// Why a server would not open, as the launcher's failure card says it
// (lib/startup.js): the rule a sidecar's last lines match, the first one
// winning, else the timeout or exit wording; and a URL that would not load,
// from Chromium's error, by what the shell knows of its server for an
// address that does not speak HTTPS (the card's *Use http:// instead*,
// *Use https://…*, or the suspicious wording), and a certificate the card
// may trust. `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { diagnose, diagnoseOpen, diagnoseCert, startupError } = require('../lib/startup');

const INFO = 'INFO:     127.0.0.1:37568 - "GET /api/session HTTP/1.1" 200 OK';

test('a known cause gets its sentence, its fix, and the server\'s own line', () => {
  const port = diagnose([INFO, '[Errno 98] error while attempting to bind on address: address already in use'].join('\n'), 'exit');
  assert.equal(port.action, 'retry');
  assert.match(port.summary, /port the server picked was taken/);
  assert.equal(port.detail, '[Errno 98] error while attempting to bind on address: address already in use');

  const db = diagnose('  sqlite3.OperationalError: unable to open database file  ', 'exit');
  assert.equal(db.action, 'data');
  assert.match(db.summary, /could not open its database files/);
  assert.equal(db.detail, 'sqlite3.OperationalError: unable to open database file', 'trimmed');

  assert.equal(diagnose("PermissionError: [Errno 13] Permission denied: '/data/users.db'", 'exit').action, 'data');
  assert.equal(diagnose("ModuleNotFoundError: No module named 'fitz'", 'exit').action, 'log', 'a missing package shows the log');
});

test('the first rule that matches wins, wherever its line sits', () => {
  const d = diagnose(['OSError: [Errno 98] address already in use', 'sqlite3.OperationalError: disk I/O error'].join('\n'), 'exit');
  assert.equal(d.action, 'retry');
  assert.match(d.detail, /address already in use/);
});

test('nothing recognised: a server that never answered is tried again, one that stopped shows its log', () => {
  const timeout = diagnose(INFO, 'timeout');
  assert.equal(timeout.action, 'retry');
  assert.match(timeout.summary, /never answered/);
  assert.equal(timeout.detail, undefined);
  const exit = diagnose('', 'exit');
  assert.equal(exit.action, 'log');
  assert.match(exit.summary, /stopped while starting up/);
});

test('the error reads back only the failing run\'s lines', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-startup-'));
  try {
    const log = path.join(dir, 'a1.log');
    const before = 'sqlite3.OperationalError: unable to open database file\n--- server exited (code 1) ---\n';
    fs.writeFileSync(log, before + '\n--- start (dev) port 50000 ---\nOSError: address already in use\n');
    const err = startupError('exit', log, Buffer.byteLength(before));
    assert.equal(err.startup.action, 'retry', 'the earlier run\'s database line is not read');
    assert(!/database/.test(err.startup.log));
    assert.match(err.message, /^The port the server picked was taken/);
    assert.equal(startupError('timeout', path.join(dir, 'none.log'), 0).startup.log, '(no log)');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('a URL that would not load is named from Chromium\'s error; a refused certificate is never offered http://', () => {
  const cert = diagnoseOpen('ERR_CERT_AUTHORITY_INVALID (-202) loading https://nas.lan/', { url: 'https://nas.lan', scope: 'lan' });
  assert.equal(cert.action, 'retry', 'a certificate is trusted by its card, never by plain http');
  assert.match(cert.detail, /ERR_CERT_AUTHORITY_INVALID/);
  const refused = diagnoseOpen('ERR_CONNECTION_REFUSED (-102)');
  assert.match(refused.summary, /could not reach/);
  assert.equal(refused.action, 'retry');
  assert.match(diagnoseOpen('ERR_NAME_NOT_RESOLVED').summary, /does not exist/);
  const other = diagnoseOpen('Something else');
  assert.equal(other.action, 'retry');
  assert.equal(other.summary, 'Could not open this server.');
});

test('an address that does not speak HTTPS: http:// is offered only to one of this network that never spoke TLS here', () => {
  const SSL = 'ERR_SSL_PROTOCOL_ERROR (-107) loading \'https://nas.lan:9001/\'';
  const at = (url, more = {}) => diagnoseOpen(SSL, { url, scope: 'lan', pinned: false, httpsSeen: false, ...more });
  const offered = at('https://nas.lan:9001');
  assert.equal(offered.action, 'http');
  assert.equal(offered.summary, 'nas.lan does not speak HTTPS at this address.');
  assert.equal(diagnoseOpen('ERR_SSL_VERSION_OR_CIPHER_MISMATCH', { url: 'https://192.168.1.5', scope: 'lan' }).action, 'http');
  assert.equal(at('https://localhost:9443', { scope: 'loopback' }).action, 'http');
  // pinned, seen over https before, or a public host: suspicious, Try again only
  for (const more of [{ pinned: true }, { httpsSeen: true }, { scope: 'name' }, { scope: 'far' }]) {
    const d = at('https://nas.lan:9001', more);
    assert.equal(d.action, 'retry', JSON.stringify(more));
    assert.equal(d.summary, 'nas.lan answered without encryption where it used HTTPS before.');
    assert.match(d.hint, /may be interfering/);
  }
  // an http:// address this window upgraded (HSTS, a redirect): its https:// address, never http://
  const upgraded = at('http://nas.example.com:9001', { scope: 'name' });
  assert.equal(upgraded.action, 'https');
  assert.equal(upgraded.summary, 'This name is HTTPS-only in this window.');
  assert.match(upgraded.hint, /Open it at https:\/\/nas\.example\.com, or reach this port by its IP address/);
  assert.equal(diagnoseOpen(SSL).action, 'retry', 'a failure of no known server');
});

test('an http:// address that sent the window to https:// (main.js sentToHttps) is offered that address, whatever the load failed with', () => {
  const ABORTED = 'ERR_ABORTED (-3) loading \'http://nas.lan:5000/\'';
  const redirect = diagnoseOpen(ABORTED, { url: 'http://nas.lan:5000', scope: 'lan', sentTo: { to: 'https://nas.lan:5001', hsts: false } });
  assert.deepEqual([redirect.action, redirect.to, redirect.summary], ['https', 'https://nas.lan:5001', 'nas.lan now redirects to https://nas.lan:5001.']);
  assert.match(redirect.hint, /synced vaults, computer link and offline copies move there with it/);
  const hsts = diagnoseOpen(ABORTED, { url: 'http://nas.example.com:9001', scope: 'name', sentTo: { to: 'https://nas.example.com', hsts: true } });
  assert.deepEqual([hsts.action, hsts.to, hsts.summary], ['https', 'https://nas.example.com', 'This name is HTTPS-only in this window.']);
  // an https:// address is never offered another by this
  assert.equal(diagnoseOpen(ABORTED, { url: 'https://nas.lan:5001', sentTo: { to: 'https://nas.lan:5002', hsts: false } }).action, 'retry');
});

test('a certificate the card may trust is worded by what it is: another authority, another name, expired, changed', () => {
  const cert = { sha256: 'a'.repeat(64), subject: 'CN=other.lan', issuer: 'CN=other.lan', validFrom: '2020-01-01T00:00:00.000Z', validTo: '2021-01-01T00:00:00.000Z' };
  const authority = diagnoseCert('net::ERR_CERT_AUTHORITY_INVALID', 'nas.lan', { ...cert, validTo: '2999-01-01T00:00:00.000Z' }, false);
  assert.deepEqual([authority.action, authority.summary, authority.expired], ['trust', 'nas.lan\'s certificate is not from an authority this computer trusts.', '']);
  assert.match(authority.hint, /Gamma will accept this certificate, and only this one, from nas\.lan\.$/);
  const name = diagnoseCert('net::ERR_CERT_COMMON_NAME_INVALID', 'nas.lan', cert, false);
  assert.equal(name.summary, 'nas.lan\'s certificate is issued to another name (CN=other.lan).');
  assert.equal(name.expired, 'This certificate expired on 2021-01-01.');
  const changed = diagnoseCert('net::ERR_CERT_AUTHORITY_INVALID', 'nas.lan', cert, true);
  assert.deepEqual([changed.action, changed.summary], ['retrust', 'The certificate of nas.lan changed.']);
  assert.match(changed.hint, /someone may be posing as nas\.lan: don't continue\.$/);
});
