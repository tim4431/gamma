// A certificate trusted by its fingerprint (lib/certs.js): the digest of a
// certificate as the sidecar computes it, what Chromium's verify proc is
// answered for a pin (only the failures a pin may answer, any port of its
// host, never under the Gamma Cloud's domain), a pin's verdict on a served
// certificate, and which refusals the launcher offers to trust. `npm test`
// in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const certs = require('../lib/certs');

// A public test certificate, no key anywhere (test/nas.pem): CN nas.lan,
// SAN nas.lan and 192.168.1.5, EC P-256, 2026-01-01 for ten years. Its
// digest as `openssl x509 -fingerprint -sha256` reads it (and hashlib over
// its DER).
const PEM = fs.readFileSync(path.join(__dirname, 'nas.pem'), 'utf8');
const DIGEST = 'ce48e27366a4bb077be700fc1bcf08f81392943ca3767b6a0908ce3f9d73e26c';
const OTHER = 'a'.repeat(64);
const CLOUD = 'gammapdf.com';

const pin = (sha256) => ({ sha256, subject: 'CN=nas.lan', issuer: 'CN=nas.lan', validFrom: '', validTo: '', pinnedAt: '' });
const request = (hostname, verificationResult, data = PEM) => ({ hostname, verificationResult, certificate: { data } });

test('a certificate\'s fingerprint is the SHA-256 of its DER, 64 lowercase hex digits, grouped for the card', () => {
  assert.equal(certs.fingerprintOf(PEM), DIGEST);
  assert.equal(certs.fingerprintOf(PEM), DIGEST, 'the second read is the cached one');
  assert.equal(certs.fingerprintOf('not a certificate'), '');
  assert.match(certs.grouped(DIGEST), /^CE:48:E2:73(:[0-9A-F]{2}){28}$/);
});

test('the verify proc: Chromium\'s own answer, unless a failure a pin may answer meets the pin of any port of that host', () => {
  const pins = { 'https://nas.lan:5001': pin(DIGEST), 'https://[fd00::5]:5001': pin(DIGEST), 'https://other.lan': pin(OTHER) };
  assert.equal(certs.decide(pins, request('nas.lan', 'net::OK'), CLOUD), -3, 'a certificate Chromium accepts is Chromium\'s');
  assert.equal(certs.decide(pins, request('nas.lan', certs.AUTHORITY), CLOUD), 0, 'pinned for another port of the host');
  assert.equal(certs.decide(pins, request('NAS.lan', certs.DATE), CLOUD), 0);
  assert.equal(certs.decide(pins, request('nas.lan', 'net::ERR_CERT_REVOKED'), CLOUD), -3, 'never a revoked one');
  assert.equal(certs.decide(pins, request('nas.lan', 'net::ERR_SSL_PROTOCOL_ERROR'), CLOUD), -3);
  assert.equal(certs.decide(pins, request('elsewhere.lan', certs.AUTHORITY), CLOUD), -3, 'unpinned');
  assert.equal(certs.decide(pins, request('other.lan', certs.AUTHORITY), CLOUD), -3, 'another digest pinned');
  assert.equal(certs.decide(pins, request('fd00::5', certs.AUTHORITY), CLOUD), 0, 'an IPv6 host, brackets stripped');
  assert.equal(certs.decide(pins, request('[fd00::5]', certs.AUTHORITY), CLOUD), 0);
  const cloud = { 'https://tim-user.gammapdf.com': pin(DIGEST), 'https://gammapdf.com': pin(DIGEST) };
  assert.equal(certs.decide(cloud, request('tim-user.gammapdf.com', certs.AUTHORITY), CLOUD), -3, 'never under the Cloud\'s domain');
  assert.equal(certs.decide(cloud, request('gammapdf.com', certs.AUTHORITY), CLOUD), -3);
  assert.equal(certs.decide(pins, { hostname: 'nas.lan', verificationResult: certs.AUTHORITY }, CLOUD), -3, 'no certificate');
});

test('a pin\'s verdict, what the launcher offers to trust, and the Cloud\'s domain', () => {
  const pins = { 'https://nas.lan:5001': pin(DIGEST) };
  assert.equal(certs.verdict(pins, 'https://nas.lan:5001', DIGEST), 'pinned');
  assert.equal(certs.verdict(pins, 'https://nas.lan:5001', OTHER), 'changed');
  assert.equal(certs.verdict(pins, 'https://nas.lan', DIGEST), 'none', 'per origin');

  assert.equal(certs.offerable(certs.AUTHORITY, 'https://nas.example.com', 'name', CLOUD), true);
  assert.equal(certs.offerable(certs.DATE, 'https://192.168.1.5:5001', 'lan', CLOUD), true);
  assert.equal(certs.offerable(certs.NAME, 'https://nas.lan', 'lan', CLOUD), true, 'another name, on this network');
  assert.equal(certs.offerable(certs.NAME, 'https://localhost:9443', 'loopback', CLOUD), true);
  assert.equal(certs.offerable(certs.NAME, 'https://nas.example.com', 'name', CLOUD), false, 'a public name can get a public certificate');
  assert.equal(certs.offerable('net::ERR_CERT_REVOKED', 'https://nas.lan', 'lan', CLOUD), false);
  assert.equal(certs.offerable('net::ERR_CERT_WEAK_KEY', 'https://nas.lan', 'lan', CLOUD), false);
  assert.equal(certs.offerable(certs.AUTHORITY, 'https://tim-user.gammapdf.com', 'name', CLOUD), false);

  assert.equal(certs.cloudDomain('https://account.gammapdf.com'), 'gammapdf.com');
  assert.equal(certs.cloudDomain('https://login.example.co.uk'), 'example.co.uk');
  assert.equal(certs.cloudDomain('not an address'), '');
  assert.equal(certs.cloudDomain('https://gammapdf.com'), 'gammapdf.com', 'a two-label host is the domain itself');
  assert.equal(certs.cloudDomain('http://127.0.0.1:9002'), '', 'a test\'s account server names none');
  assert.equal(certs.cloudDomain('http://account:9002'), '');
  assert.equal(certs.underCloud('anything', ''), false);
  assert.equal(certs.underCloud('a.b.gammapdf.com', 'gammapdf.com'), true);
  assert.equal(certs.underCloud('evilgammapdf.com', 'gammapdf.com'), false);
  // a name typed with its trailing dot is the same name, as the sidecar reads it (tls_trust.under_cloud)
  const dotted = new URL('https://alice-user.gammapdf.com./').hostname;
  assert.equal(dotted, 'alice-user.gammapdf.com.');
  assert.equal(certs.underCloud(dotted, 'gammapdf.com'), true);
  assert.equal(certs.offerable(certs.AUTHORITY, 'https://alice-user.gammapdf.com./', 'name', CLOUD), false);
  assert.equal(certs.decide({ 'https://alice-user.gammapdf.com.': pin(DIGEST) }, request(dotted, certs.AUTHORITY), CLOUD), -3);
  assert.equal(certs.decide(pins, request('nas.lan.', certs.AUTHORITY), CLOUD), 0, 'nas.lan. is nas.lan');
});

test('what a certificate says of itself is capped where it is read', () => {
  const said = certs.describe({ data: PEM, subjectName: 'x'.repeat(2000), issuerName: 'CN=nas.lan', validStart: 1767225600, validExpiry: 2082758400 });
  assert.equal(said.sha256, DIGEST);
  assert.equal(said.subject.length, 500);
  assert.equal(said.issuer, 'CN=nas.lan');
  assert.equal(said.validFrom, '2026-01-01T00:00:00.000Z');
  assert.equal(said.validTo, '2036-01-01T00:00:00.000Z');
  assert.deepEqual(certs.describe(null), { sha256: '', subject: '', issuer: '', validFrom: '', validTo: '' });
});
