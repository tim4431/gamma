// Where a host is, by its name alone (lib/hostScope.js): the cases the
// server's table answers too (backend/tests/data/host_scope_cases.json,
// read by backend/tests/test_host_scope.py), so the shell and the server
// tell this network from the internet alike. `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const hostScope = require('../lib/hostScope');

const CASES = path.join(__dirname, '..', '..', 'backend', 'tests', 'data', 'host_scope_cases.json');

test('every shared case reads as the server reads it', () => {
  const cases = JSON.parse(fs.readFileSync(CASES, 'utf8'));
  assert.ok(cases.length > 10, 'the shared cases are there');
  for (const { host, literal } of cases) assert.equal(hostScope.literal(host), literal, host);
});

test('a URL\'s hostname reads as the host: brackets and a trailing dot dropped, any case', () => {
  assert.equal(hostScope.literal('[::1]'), 'loopback');
  assert.equal(hostScope.literal('[FD00::5]'), 'lan');
  assert.equal(hostScope.literal('NAS.Example.com.'), 'name');
  assert.equal(hostScope.literal('Gamma.LOCAL'), 'lan');
});
