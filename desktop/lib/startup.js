// Why opening a server failed, in words a user can act on.
//
// A sidecar that exits during startup, or never answers, leaves its reason in
// its log, a file appended to across runs. This module reads back only the
// failing run's lines and answers two questions: what happened (one sentence
// plus the server's own wording) and what would fix it (`action`, which the
// launcher turns into a button). A server that cannot upgrade its data
// directory is no failure here: it stays up, and the window shows its guidance
// page (lib/sidecar.js healthy).

const fs = require('fs');

// `from` is the byte offset the failing run's header was written at, so only
// this run's lines are read back.
function logTail(logPath, from = 0, lines = 60) {
  try {
    const buf = fs.readFileSync(logPath);
    const text = buf.subarray(Math.min(from, buf.length)).toString('utf8');
    return text.split(/\r?\n/).filter((l) => l.trim()).slice(-lines).join('\n') || '(no output)';
  } catch {
    return '(no log)';
  }
}

// First rule whose pattern appears in the log wins; the matched line is kept as
// `detail` so the server still speaks for itself.
const FAILURES = [
  {
    match: /EADDRINUSE|address already in use/i,
    summary: 'The port the server picked was taken by something else.',
    hint: 'Opening the server again picks a new one.',
    action: 'retry',
  },
  {
    match: /unable to open database file|disk I\/O error|database disk image is malformed/i,
    summary: 'The server could not open its database files.',
    hint: 'Check that the data folder still exists and sits on a drive that is connected — a network or removable disk that went away is the usual cause.',
    action: 'data',
  },
  {
    match: /Permission denied|EACCES|EPERM/i,
    summary: 'The server is not allowed to write to its data folder.',
    hint: 'Check the folder’s permissions, or point local server storage at a folder your account owns.',
    action: 'data',
  },
  {
    match: /ModuleNotFoundError|ImportError|No module named/i,
    summary: 'The server’s Python environment is missing a package.',
    hint: 'In a source checkout: install backend/requirements.txt into the virtualenv the server runs from (backend/venv, or the Python set in the launcher’s advanced settings).',
  },
];

// reason: 'exit' (the process quit) or 'timeout' (it never answered /api/health).
function diagnose(log, reason) {
  const lines = String(log || '').split(/\r?\n/);
  for (const rule of FAILURES) {
    const line = lines.find((l) => rule.match.test(l));
    if (line) return { summary: rule.summary, hint: rule.hint, action: rule.action || 'log', detail: line.trim() };
  }
  if (reason === 'timeout') {
    return {
      summary: 'The server started but never answered.',
      hint: 'A slow disk or a security scanner holding up the first launch can do this; opening it again usually works.',
      action: 'retry',
    };
  }
  return {
    summary: 'The server stopped while starting up.',
    hint: 'Its last output is below.',
    action: 'log',
  };
}

// The other way an open fails: the content view could not load the URL at all,
// which arrives as Chromium's own `ERR_…` string. Same treatment — say what
// happened, keep the raw message as the detail.
const OPEN_FAILURES = [
  {
    match: /ERR_NAME_NOT_RESOLVED/,
    summary: 'That address does not exist on this network.',
    hint: 'Check the spelling of the server URL; a machine name that works elsewhere may not resolve from here.',
  },
  {
    match: /ERR_INTERNET_DISCONNECTED|ERR_NETWORK_CHANGED/,
    summary: 'This computer is offline.',
    hint: 'Reconnect and open the server again.',
  },
  {
    match: /ERR_CERT/,
    summary: 'The server’s HTTPS certificate was rejected.',
    hint: 'It is revoked, uses weak cryptography, or is not one Gamma can trust here. Install a certificate the system trusts on the server.',
  },
  {
    match: /ERR_SSL/,
    summary: 'The secure connection to the server failed.',
    hint: 'The server, or something between this computer and it, ended the TLS handshake.',
  },
  {
    match: /ERR_CONNECTION_REFUSED|ERR_CONNECTION_TIMED_OUT|ERR_ADDRESS_UNREACHABLE|ERR_CONNECTION_RESET|ERR_EMPTY_RESPONSE/,
    summary: 'Gamma could not reach this server.',
    hint: 'It may be switched off, or the address may be wrong — check that the machine is running Gamma and is reachable from here.',
  },
];

// The answers that mean "this address does not speak HTTPS": a plain-http
// server reached at https://, or one that an http:// address was upgraded
// from in this window (HSTS, a redirect).
const NOT_HTTPS = /ERR_SSL_PROTOCOL_ERROR|ERR_SSL_VERSION_OR_CIPHER_MISMATCH/;

// `server`: what the shell knows of the server that would not load, for the
// failures whose fix depends on it (docs/dev/https.md): its `url`, whether
// its origin has a pinned certificate (`pinned`), whether it loaded over
// https before (`httpsSeen`), its host's scope (lib/hostScope.js), and
// where the window was sent from its http:// address (`sentTo`, main.js
// sentToHttps: `{ to, hsts }`). *Use http:// instead* (`http`) is offered
// only to an https:// address of this network that never spoke TLS here;
// the others get the suspicious wording with Try again, and an http://
// address sent to https:// in this window is offered that address
// (`https`, `to`).
function diagnoseOpen(message, server = {}) {
  const text = String(message || '');
  let sent = null;
  try { sent = server.sentTo && server.url && new URL(server.url).protocol === 'http:' ? server.sentTo : null; } catch {}
  if (sent) {
    const host = new URL(server.url).hostname;
    if (sent.hsts) return httpsOnly(host, text);
    return { summary: `${host} now redirects to ${sent.to}.`, action: 'https', to: sent.to, detail: text,
      hint: `Gamma does not follow a server to another address. Use ${sent.to}: this server's synced vaults, computer link and offline copies move there with it.` };
  }
  if (NOT_HTTPS.test(text) && server.url) {
    let u = null;
    try { u = new URL(server.url); } catch {}
    const host = u ? u.hostname : '';
    if (u && u.protocol === 'http:' && /ERR_SSL_PROTOCOL_ERROR/.test(text)) return httpsOnly(host, text);
    if (u && u.protocol === 'https:') {
      const local = server.scope === 'lan' || server.scope === 'loopback';
      if (local && !server.pinned && !server.httpsSeen) {
        return { summary: `${host} does not speak HTTPS at this address.`, action: 'http', detail: text,
          hint: 'A Gamma reached without a TLS proxy in front answers plain http://. Use http:// on your own network, or put a TLS proxy in front of it.' };
      }
      return { summary: `${host} answered without encryption where it used HTTPS before.`, action: 'retry', detail: text,
        hint: `Someone between this computer and ${host} may be interfering. Try again on another network before changing anything.` };
    }
  }
  for (const rule of OPEN_FAILURES) {
    if (rule.match.test(text)) return { summary: rule.summary, hint: rule.hint, action: rule.action || 'retry', detail: text };
  }
  return { summary: 'Could not open this server.', hint: '', action: 'retry', detail: text };
}

// An http:// address of a name that told this window to use https:// only (HSTS).
function httpsOnly(host, text) {
  return { summary: 'This name is HTTPS-only in this window.', action: 'https', to: `https://${host}`, detail: text,
    hint: `${host} told this window to use https:// only. Open it at https://${host}, or reach this port by its IP address instead of the name.` };
}

// A certificate the launcher's card offers to trust (lib/certs.js
// offerable): `error`, Chromium's; `host`; `cert`, what it says of itself
// (certs.describe); `changed`, whether its origin pins another. → the
// card's words, `action` `trust` or `retrust`.
function diagnoseCert(error, host, cert, changed) {
  const c = cert || {};
  const expired = c.validTo && Date.parse(c.validTo) < Date.now() ? `This certificate expired on ${c.validTo.slice(0, 10)}.` : '';
  if (changed) {
    return { summary: `The certificate of ${host} changed.`, action: 'retrust', expired,
      hint: `Gamma trusted another certificate for ${host}. If you did not replace it yourself, someone may be posing as ${host}: don't continue.` };
  }
  if (/ERR_CERT_COMMON_NAME_INVALID/.test(error)) {
    return { summary: `${host}'s certificate is issued to another name (${c.subject || 'unnamed'}).`, action: 'trust', expired,
      hint: `If ${host} is your own server, check that the fingerprint below is the one it shows, then trust it for this address.` };
  }
  return { summary: `${host}'s certificate is not from an authority this computer trusts.`, action: 'trust', expired,
    hint: `If ${host} is your own server and made this certificate itself, check that the fingerprint below is the one ${host} shows, then trust it. Gamma will accept this certificate, and only this one, from ${host}.` };
}

// An Error carrying what the launcher needs. The message stays readable on its
// own, for the console and for anything that only has a string.
function startupError(reason, logPath, from) {
  const log = logTail(logPath, from);
  const d = diagnose(log, reason);
  const err = new Error(`${d.summary} ${d.hint || ''}`.trim());
  err.startup = { ...d, log };
  return err;
}

module.exports = { diagnose, diagnoseOpen, diagnoseCert, startupError };
