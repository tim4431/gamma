// A certificate the person trusts by its fingerprint, SSH known_hosts
// style (docs/dev/https.md "Pinned certificates"): a NAS that made
// its own certificate, or one signed by an authority this computer does not
// hold. Pure: main.js answers Chromium's verify proc and the
// `certificate-error` event with these, keeps the pins in the registry
// (registry.setPin) and hands them to its local servers, whose sidecar
// accepts the same pins for syncing (backend/gamma/tls_trust.py).
//
// A pin is the SHA-256 of the leaf certificate's DER, 64 lowercase hex
// digits (what `openssl x509 -fingerprint -sha256` and a browser show),
// kept per origin (`https://host[:port]`, URL.origin's form). Chromium's
// verify proc sees only the host name, so a pin answers for any port of
// its host there; the sidecar, which has the port, pins per origin.
//
// A pin answers only the failures in OFFERABLE (an authority this computer
// does not trust, another name, a date), never a revoked or weak
// certificate or a TLS failure, and never a host under the Gamma Cloud's
// domain: the same rules decide what the launcher offers to trust and what
// the verify proc accepts, so a certificate pinned while merely expired is
// refused once it is revoked. Another name is offered only for a host of
// this network (lib/hostScope.js): a public name can get a public
// certificate.

const crypto = require('crypto');
const net = require('net');

const AUTHORITY = 'net::ERR_CERT_AUTHORITY_INVALID';
const NAME = 'net::ERR_CERT_COMMON_NAME_INVALID';
const DATE = 'net::ERR_CERT_DATE_INVALID';
const OFFERABLE = new Set([AUTHORITY, NAME, DATE]);

// What a certificate says of itself is the server's to write: capped where
// it is read, and shown as text only (docs/dev/https.md "Rules", R4).
const CAP = 500;
const capped = (s) => String(s == null ? '' : s).slice(0, CAP);

// The Gamma Cloud's account server, as lib/sidecar.js hands it to the local
// servers; the hosted servers are `<label>-user.` its domain.
const DEFAULT_ISSUER = 'https://account.gammapdf.com';

// The Cloud's domain, as backend/gamma/tls_trust.py cloud_domain reads it:
// the issuer's host without its first label (account.gammapdf.com →
// gammapdf.com), a two-label host itself, none for an IP address or a
// single label (a test's account server).
function cloudDomain(issuer = process.env.GAMMA_CLOUD_ISSUER || process.env.GAMMA_CLOUD_DEFAULT_ISSUER || DEFAULT_ISSUER) {
  let host = '';
  try { host = new URL(issuer).hostname.toLowerCase().replace(/\.$/, ''); } catch { return ''; }
  if (!host.includes('.') || net.isIP(host.replace(/^\[(.*)\]$/, '$1'))) return '';
  const labels = host.split('.');
  return labels.length > 2 ? labels.slice(1).join('.') : host;
}

// Fingerprints by PEM: the verify proc runs at each connection.
const fingerprints = new Map();
function fingerprintOf(pem) {
  const key = String(pem || '');
  if (fingerprints.has(key)) return fingerprints.get(key);
  let hex = '';
  try { hex = new crypto.X509Certificate(key).fingerprint256.replace(/:/g, '').toLowerCase(); } catch {}
  if (fingerprints.size >= 64) fingerprints.delete(fingerprints.keys().next().value);
  fingerprints.set(key, hex);
  return hex;
}

// A fingerprint as the card shows it: "AB:CD:…".
const grouped = (hex) => (String(hex || '').toUpperCase().match(/.{1,2}/g) || []).join(':');

const iso = (seconds) => {
  const d = new Date(Number(seconds) * 1000);
  return Number.isNaN(d.getTime()) ? '' : d.toISOString();
};

// What an Electron Certificate says of itself, for the card and the pin.
function describe(certificate) {
  const c = certificate || {};
  return {
    sha256: fingerprintOf(c.data), subject: capped(c.subjectName), issuer: capped(c.issuerName),
    validFrom: iso(c.validStart), validTo: iso(c.validExpiry),
  };
}

// A host name as two are compared: lowercase, IPv6 brackets and trailing
// dots stripped (`nas.lan.` is nas.lan, as tls_trust.under_cloud reads it).
const bareHost = (h) => String(h || '').toLowerCase().replace(/^\[(.*)\]$/, '$1').replace(/\.+$/, '');

function underCloud(host, domain) {
  const h = bareHost(host);
  const d = String(domain || '').toLowerCase();
  return Boolean(d) && (h === d || h.endsWith(`.${d}`));
}

const hostOf = (origin) => {
  try { return new URL(origin).hostname; } catch { return ''; }
};

// The digests pinned for any port of `hostname`.
function pinsOfHost(pins, hostname) {
  const h = bareHost(hostname);
  return Object.entries(pins || {}).filter(([origin]) => bareHost(hostOf(origin)) === h).map(([, p]) => p && p.sha256).filter(Boolean);
}

// setCertificateVerifyProc's answer: 0 accepts, -3 leaves Chromium's own.
function decide(pins, request, domain) {
  const r = request || {};
  if (r.verificationResult === 'net::OK') return -3;
  if (!OFFERABLE.has(r.verificationResult) || underCloud(r.hostname, domain)) return -3;
  const digest = fingerprintOf(r.certificate && r.certificate.data);
  return digest && pinsOfHost(pins, r.hostname).includes(digest) ? 0 : -3;
}

// What the pins say of a certificate `sha256` served at `origin`: 'pinned',
// 'changed' (the origin pins another) or 'none'.
function verdict(pins, origin, sha256) {
  const p = (pins || {})[origin];
  if (!p) return 'none';
  return p.sha256 === sha256 ? 'pinned' : 'changed';
}

// Whether the launcher offers to trust a certificate that failed with
// `errorCode` at `origin` (`scope`, its host's, lib/hostScope.js).
function offerable(errorCode, origin, scope, domain) {
  if (!OFFERABLE.has(errorCode) || underCloud(hostOf(origin), domain)) return false;
  return errorCode !== NAME || scope === 'lan' || scope === 'loopback';
}

module.exports = {
  AUTHORITY, NAME, DATE, OFFERABLE, CAP, cloudDomain, fingerprintOf, grouped, describe, bareHost, underCloud, pinsOfHost,
  decide, verdict, offerable,
};
