// Why a server would not open, as the launcher's failure card says it
// (lib/startup.js): the rule a sidecar's last lines match, the first one
// winning, else the timeout or exit wording; and a URL that would not load,
// from Chromium's error, the certificate rule with its `http` action (the
// card's *Use http:// instead*). `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { diagnose, diagnoseOpen, startupError } = require('../lib/startup');

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

test('a URL that would not load is named from Chromium\'s error; a refused https:// address is offered http://', () => {
  const cert = diagnoseOpen('ERR_CERT_AUTHORITY_INVALID (-202) loading https://nas.lan/');
  assert.equal(cert.action, 'http');
  assert.match(cert.detail, /ERR_CERT_AUTHORITY_INVALID/);
  const refused = diagnoseOpen('ERR_CONNECTION_REFUSED (-102)');
  assert.match(refused.summary, /could not reach/);
  assert.equal(refused.action, 'retry');
  assert.match(diagnoseOpen('ERR_NAME_NOT_RESOLVED').summary, /does not exist/);
  const other = diagnoseOpen('Something else');
  assert.equal(other.action, 'retry');
  assert.equal(other.summary, 'Could not open this server.');
});
