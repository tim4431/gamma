// A remote server over HTTPS (docs/dev/https.md, docs/dev/desktop.md
// "Addresses and certificates"). Identity is exact: a view is a given server
// only when its origin is the link's stored origin, and every place that
// mints a token or sends a `remote_url` takes the origin from remoteOf,
// which refuses an address the links moved away from; a load of such an
// address goes to the current one instead (rerouted). An address changes
// only with its links (moveServer: a dry run on the host, the person's word,
// then the move, which the host checks against the new address first). A
// token goes over plain http:// only once the person agreed to it for that
// origin (beforeMint). A certificate the window refuses may be trusted by
// its fingerprint, only the one the launcher's card showed (trustCert) or a
// move to a new address asked about (trustMoveTarget), and the local
// servers accept the same pins (pushPins); one the window accepts
// and a local server does not is trusted for syncing too (trustForSync).

const { session } = require('electron');
const crypto = require('crypto');
const registry = require('./registry');
const sidecar = require('./sidecar');
const startup = require('./startup');
const keeping = require('./keeping');
const certs = require('./certs');
const hostScope = require('./hostScope');

// The shell's shared names, set by init (main.js "the sections").
let ctx = null;

const CLOUD_DOMAIN = certs.cloudDomain();

// The host's scope (lib/hostScope.js) of an origin.
function scopeOf(origin) {
  try {
    return hostScope.literal(new URL(origin).hostname);
  } catch {
    return '';
  }
}

// An address's host name without its port, as the questions and the card name it.
function hostOnly(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return String(url || '');
  }
}

// The origin the window shows (`origin`: another the caller has, a row's),
// for a token or a `remote_url`; never an address the links moved away from.
function remoteOf(origin = ctx.openOrigin()) {
  if (registry.movedAway(origin)) throw new Error(`${origin} is now ${registry.currentOrigin(origin)}: open it there and ask again.`);
  return origin;
}

// The same path and query at the address the links of `url`'s origin moved
// to, or null for an address that did not move.
function rerouted(url) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return null;
  }
  if (!/^https?:$/.test(u.protocol) || !registry.movedAway(u.origin)) return null;
  const to = registry.currentOrigin(u.origin);
  ctx.allowedOrigins.add(to);
  return to + u.pathname + u.search + u.hash;
}

// The window sent from a registered remote's http:// address to https:// on
// the same host name, by HSTS (Chromium's upgrade, which keeps the port) or
// by the server's own redirect (Synology's 5000 to 5001): the content guard
// follows it nowhere, the system browser included (R3: the address is the
// link's), and records it here for the launcher's card (launcherError,
// startup.diagnoseOpen), which offers the move. Each such remote allowed in
// the window -> `{ to, hsts }`, `to` the address offered: the redirect's, or
// the name's own https:// for HSTS. Cleared as that server opens again.
const sentToHttps = new Map();
function sentToHttpsBy(url) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return false;
  }
  if (u.protocol !== 'https:') return false;
  let hit = false;
  for (const s of registry.load().servers) {
    if (s.type !== 'remote' || !/^http:/.test(s.url) || !ctx.allowedOrigins.has(s.url)) continue;
    const from = new URL(s.url);
    if (from.hostname !== u.hostname) continue;
    const hsts = from.port === u.port; // both '' for 80 → 443
    sentToHttps.set(s.url, { to: hsts ? `https://${u.hostname}` : u.origin, hsts });
    hit = true;
  }
  return hit;
}

// A registered remote loaded over https in the window (registry.httpsSeen).
function sawHttps(url) {
  let origin = '';
  try {
    origin = new URL(url).protocol === 'https:' ? new URL(url).origin : '';
  } catch {}
  const srv = origin && ctx.remoteAt(origin);
  if (srv && srv.url === origin) registry.markHttpsSeen(srv.id);
}

// A plain http:// address asked of the host before a token is minted for it
// (POST /api/remote-servers/check, which resolves its name): one the host
// refuses (a public host: a new link there goes over https only) throws its
// words; a public one an existing link keeps is asked every time; one of
// this network is asked once per origin (settings.plainAccepted), never for
// an origin a link of before this release already used. Cancel throws
// quietly (`quiet`: no error box). A host from before the check (404)
// asks nothing: it refuses a public plain link itself. `about` names the link
// the token is for when it exists already (`vault`: the vault whose token is
// renewed; `workspace`: the computer link's workspace), so the host counts it
// as that link's upkeep, as the route that takes the token will.
async function beforeMint(hostApi, hostOrigin, remoteOrigin, about = {}) {
  let check;
  try {
    check = await hostApi(hostOrigin, '/api/remote-servers/check', { method: 'POST', headers: ctx.JSON_TYPE, body: JSON.stringify({ url: remoteOrigin, ...about }) });
  } catch (e) {
    if (e.status === 404) return;
    throw e;
  }
  if (check.refused) throw new Error(check.refused);
  if (!check.plain) return;
  const far = check.scope === 'far';
  if (!far && check.existing) {
    registry.setPlainAccepted(remoteOrigin, true);
    return;
  }
  if (!far && registry.plainAccepted(remoteOrigin)) return;
  const host = hostOnly(remoteOrigin);
  const q = keeping.plainAsk(host, check.warning || '', Boolean(check.unsure));
  if (await ctx.ask({ type: 'warning', message: q.message, detail: q.detail, buttons: q.buttons, defaultId: 1, cancelId: 1 }, 1) !== 0) {
    throw Object.assign(new Error(`Nothing was sent to ${host} over http://.`), { quiet: true });
  }
  if (!far) registry.setPlainAccepted(remoteOrigin, true);
}

// The links of `from` moved to `to` on the host (POST
// /api/remote-servers/move, docs/dev/https.md "Moving a server's address"): a dry run
// that sends nothing anywhere, then, when it found links, an https:// `to`'s
// certificate trusted there unless it verifies (trustMoveTarget), the person's word
// (keeping.moveAsk: which tokens go to `to`; a plain http:// `to` says they
// travel in clear and allows it, `plain_ok`) unless `auto`, then the move,
// which the host makes only once `to` answered without credentials and each
// link's token there, one at a time, and the tokens it says a link at `to`
// held revoked there. A refusal throws with the host's words, nothing moved.
// Only the host's links move: an offline copy of `from` that a local server
// other than the host keeps stays there (docs/dev/https.md).
// → the move's `moved`, or null when the person cancelled.
async function moveLinks(from, to, { auto = false } = {}) {
  if (!registry.load().servers.some((s) => s.type === 'local')) return {}; // no host: no links
  const host = await ctx.startHost();
  const move = (body) => host.api(host.origin, '/api/remote-servers/move', {
    method: 'POST', headers: { ...ctx.JSON_TYPE, ...ctx.shellWord(host.origin) }, body: JSON.stringify({ from, to, ...body }),
  });
  const plan = await move({ dry_run: true });
  const moved = plan.moved || {};
  if (!(moved.vaults || moved.computers || moved.mirrors)) return moved;
  if (auto && plan.kind !== 'upgrade') throw new Error(`${from} to ${to} is no move to https of the same server.`);
  // the probe needs `to`'s certificate verified or pinned (R2: never asked in the automatic move)
  const pinned = auto ? '' : await trustMoveTarget(to);
  if (pinned === null) return null;
  let done = null;
  try {
    if (!auto) {
      const q = keeping.moveAsk(from, to, plan);
      if (await ctx.ask({ type: 'warning', message: q.message, detail: q.detail, buttons: q.buttons, defaultId: 1, cancelId: 1 }, 1) !== 0) return null;
    }
    ctx.busy = `Moving the links to ${to}…`;
    ctx.pushState();
    try {
      done = await move({ plain_ok: !auto && Boolean(plan.plain) });
    } finally {
      ctx.busy = null;
      ctx.pushState();
    }
  } finally {
    // a pin made for a move that did not happen is no server's
    if (!done && pinned && registry.dropPin(pinned)) await pinsChanged();
  }
  const remote = ctx.serverApi(session.defaultSession);
  for (const r of done.revoke || []) await ctx.revokeToken(remote, to, r.workspace_id, r.token_id);
  return done.moved || moved;
}

// A remote server's address changed (the launcher's edit form, its card's
// *Use http:// instead* and *Use https://…*, the bar's *Use …*), its links
// moving with it first (moveLinks), and only then the registry: the old
// address leaves the window's allowed origins, and a load of it goes to the
// new one. `id` null: a redirected link of a server the registry does not
// hold, or whose new address it lists already, whose links move alone
// (entryMoving). `auto`: the same host name over https, a
// redirect's move (autoUpgrade), asked of nobody. → the server, or null
// when the person cancelled.
async function moveServer(id, url, { auto = false, from: fromUrl = '' } = {}) {
  const srv = id ? registry.get(id) : null;
  if (id && (!srv || srv.type !== 'remote')) throw new Error('Unknown server');
  const from = srv ? srv.url : registry.originOf(fromUrl);
  const to = registry.parseServerUrl(url).origin;
  if (to === from) return srv;
  // only an entry renamed can clash: links alone may move to an address listed already
  if (srv && registry.load().servers.some((s) => s.type === 'remote' && s.url === to && s.id !== id)) throw new Error(`${to} is already listed`);
  const moved = await moveLinks(from, to, { auto });
  if (!moved) return null;
  let after = null;
  if (srv) {
    const changed = registry.setRemoteUrl(id, to);
    after = changed.srv;
    if (changed.pins) await pinsChanged(); // the old address's pin moved with it, or went
    ctx.allowedOrigins.add(to);
    ctx.allowedOrigins.delete(from);
    if (ctx.current && ctx.current.id === id) ctx.current = null; // the next open loads the new address
    ctx.buildMenu();
  }
  if (moved.vaults || moved.computers || moved.mirrors || auto) ctx.showNotice(keeping.moveSaid(from, to, moved));
  ctx.pushState();
  ctx.refreshKeeping().catch(() => {});
  return after;
}

// The registry's entry whose address a redirect's move from `from` to `to`
// changes: the one at `from`, unless another lists `to` already (then the
// links move alone and both entries stay), or null.
function entryMoving(from, to) {
  const srv = ctx.remoteAt(from);
  return srv && srv.url === from && !ctx.remoteAt(to) ? srv.id : null;
}

// Before links move to an https:// `to`, its certificate as the window sees
// it: the host's probe needs it verified or pinned for exactly `to`
// (docs/dev/https.md "Moving a server's address"), while the launcher's
// card pins only a listed server's own address, which changes only after
// the move. A certificate the window takes by a pin of the same host name
// (any port, certs.decide) is pinned for `to` too, asking nothing; one a pin
// may answer (certs.offerable) is shown with its fingerprint, as the card
// shows it, and pinned for `to` once the person says so. → the origin
// pinned here (moveLinks drops it again should nothing move), '' for none,
// null when the person cancelled.
async function trustMoveTarget(to) {
  if (!/^https:/.test(to) || registry.getPins()[to]) return '';
  const host = hostOnly(to);
  let failed = '';
  try {
    await session.defaultSession.fetch(`${to}/api/health`, { cache: 'no-store', redirect: 'manual' });
  } catch (e) {
    failed = `${(e && e.message) || ''} ${(e && e.cause && e.cause.message) || ''}`;
  }
  const seen = seenCerts.get(certs.bareHost(host)); // what the verify proc saw there, that fetch's or an earlier one
  if (!seen || !certs.OFFERABLE.has(seen.error) || certs.underCloud(host, CLOUD_DOMAIN)) return '';
  const same = Object.entries(registry.getPins())
    .find(([origin, p]) => certs.bareHost(hostOnly(origin)) === certs.bareHost(host) && p.sha256 === seen.cert.sha256);
  if (same) {
    registry.setPin(to, same[1]);
  } else {
    if (!/ERR_CERT_/.test(failed) || !certs.offerable(seen.error, to, scopeOf(to), CLOUD_DOMAIN)) return '';
    const words = startup.diagnoseCert(seen.error, host, seen.cert, false);
    const ok = await ctx.ask({
      type: 'warning', buttons: ['Trust this certificate', 'Cancel'], defaultId: 1, cancelId: 1,
      message: `Trust the certificate of ${host}?`,
      detail: `${words.summary} ${words.hint} Fingerprint: ${certs.grouped(seen.cert.sha256)}`,
    }, 1);
    if (ok !== 0) return null;
    registry.setPin(to, seen.cert);
  }
  await pinsChanged();
  return to;
}

// The redirects to the same server over https that the bar's reading found
// (keeping.upgradeOf), each moved by itself once a session: `from` -> its
// state, 'running', 'done' or 'failed' (the row then offers *Use …*).
const upgrades = new Map();
function autoUpgrade(from, to) {
  if (upgrades.has(from)) return;
  upgrades.set(from, 'running');
  moveServer(entryMoving(from, to), to, { auto: true, from })
    .then(() => upgrades.set(from, 'done'), (e) => {
      upgrades.set(from, 'failed');
      console.error(`[shell] ${from} redirects to ${to}, and its links could not move there: ${(e && e.message) || e}`);
      ctx.refreshKeeping().catch(() => {});
    });
}

// The certificates the window refused, by origin, while the launcher's card
// may trust them: `{ nonce, error, cert, changed, at }` (certificate-error),
// kept CERT_RECORD_MS and replaced by the next refusal of that origin. The
// nonce binds the card's button to the record it showed.
const certErrors = new Map();
const CERT_RECORD_MS = 10 * 60_000;

// Per bare host name, the digest of the last certificate Chromium accepted
// there: what *Trust it for syncing too* compares a local server's refusal
// with. At most 64 hosts.
const accepted = new Map();

// Per bare host name, what the verify proc last saw there, `{ error, cert }`
// (certs.describe): what a move to an address no server has yet trusts
// (trustMoveTarget). At most 64 hosts.
const seenCerts = new Map();

function keepLast(map, key, value) {
  map.delete(key);
  map.set(key, value);
  if (map.size > 64) map.delete(map.keys().next().value);
}

// The pins as the verify proc reads them, at every connection (trustPins
// reads them again after a change).
let trusted = {};
function trustPins() {
  trusted = registry.getPins();
}

function certRecord(origin) {
  const r = certErrors.get(origin);
  if (r && Date.now() - r.at > CERT_RECORD_MS) certErrors.delete(origin);
  return certErrors.get(origin) || null;
}

// Chromium's answer for a connection, pins applied (certs.decide).
function verifyProc(request, callback) {
  if (request.certificate) {
    const host = certs.bareHost(request.hostname);
    keepLast(seenCerts, host, { error: request.verificationResult, cert: certs.describe(request.certificate) });
    if (request.verificationResult === 'net::OK') keepLast(accepted, host, certs.fingerprintOf(request.certificate.data));
  }
  callback(certs.decide(trusted, request, CLOUD_DOMAIN));
}

// The verify proc on the window's session (the local servers' partitions
// reach 127.0.0.1 over http only), again after a pin changed, with its
// connections closed. The network service keeps the proc's answers, and
// Chromium a certificate accepted in certificate-error, for the session: a
// pin newly made is answered there (certificateError), and a forgotten one
// stops answering for a certificate already accepted at the next start.
function applyVerifyProc() {
  const ses = session.defaultSession;
  ses.setCertificateVerifyProc(verifyProc);
  return Promise.resolve(ses.closeAllConnections && ses.closeAllConnections()).catch(() => {});
}

// Chromium's `certificate-error`. The network service keeps the verify
// proc's answers for the session, so a certificate refused before the person
// trusted it stays refused there: such a load is accepted here, by the
// verify proc's own rule (certs.decide), and Chromium then keeps that for
// the page's requests. Any other main-frame refusal of a registered remote
// is recorded for the launcher's card, and the load fails.
function certificateError(event, url, error, certificate, callback, isMainFrame) {
  let hostname = '';
  try {
    hostname = new URL(url).hostname;
  } catch {}
  if (hostname && certs.decide(trusted, { hostname, verificationResult: error, certificate }, CLOUD_DOMAIN) === 0) {
    event.preventDefault();
    callback(true);
    return;
  }
  if (isMainFrame) certificateRefused(url, error, certificate);
}

// A main-frame certificate refusal of a registered remote, recorded for the
// launcher's card. The load still fails: nothing is accepted here.
function certificateRefused(url, error, certificate) {
  let origin = '';
  try {
    origin = new URL(url).origin;
  } catch {
    return;
  }
  if (!ctx.remoteAt(origin) || ctx.remoteAt(origin).url !== origin) return;
  const cert = certs.describe(certificate);
  certErrors.set(origin, { nonce: crypto.randomUUID(), error, cert, changed: certs.verdict(trusted, origin, cert.sha256) === 'changed', at: Date.now() });
}

// The launcher's card for a remote at `origin` whose load failed with
// `message`: what its certificate said of itself and the button that trusts
// it (startup.diagnoseCert), or null when there is no record of it or it is
// not one a pin may answer (certs.offerable).
function certCard(origin, message, { stale = false } = {}) {
  const r = certRecord(origin);
  if (!r || !certs.offerable(r.error, origin, scopeOf(origin), CLOUD_DOMAIN)) return null;
  const words = startup.diagnoseCert(r.error, hostOnly(origin), r.cert, r.changed);
  return { ...words, detail: message || r.error, nonce: r.nonce, cert: { ...r.cert, grouped: certs.grouped(r.cert.sha256) },
    ...(stale ? { stale: 'The certificate changed while you were looking at it.' } : {}) };
}

// The launcher's *Trust this certificate* (or *Trust the new certificate*)
// for server `id`: main's record of its origin, taken once, pinned only when
// it is the one the card showed (`nonce` and `sha256` its), after a
// question when it replaces another pin (Cancel puts the record back, so the
// card's button still works, unless a newer refusal took its place);
// otherwise the card is shown again with the record there is now. Then the
// local servers are handed the pins, and the server opens.
async function trustCert(id, nonce, sha256) {
  const srv = registry.get(id);
  if (!srv || srv.type !== 'remote') throw new Error('Unknown server');
  const origin = srv.url;
  const r = certRecord(origin);
  certErrors.delete(origin);
  if (!r || r.nonce !== nonce || r.cert.sha256 !== sha256 || !certs.offerable(r.error, origin, scopeOf(origin), CLOUD_DOMAIN)) {
    if (r) certErrors.set(origin, { ...r, nonce: crypto.randomUUID() });
    const card = certCard(origin, '', { stale: true });
    ctx.loadLauncher(card ? Object.assign(new Error(card.summary), { startup: card }) : new Error('The certificate changed while you were looking at it.'), id);
    return false;
  }
  if (r.changed) {
    const ok = await ctx.ask({
      type: 'warning', buttons: ['Trust the new certificate', 'Cancel'], defaultId: 1, cancelId: 1,
      message: `Trust the new certificate of ${hostOnly(origin)}?`,
      detail: `Only if you replaced it on ${hostOnly(origin)} yourself. Fingerprint: ${certs.grouped(r.cert.sha256)}`,
    }, 1);
    if (ok !== 0) {
      if (!certErrors.has(origin)) certErrors.set(origin, r);
      return false;
    }
  }
  registry.setPin(origin, r.cert);
  await pinsChanged();
  await ctx.openServer(id);
  ctx.buildMenu();
  return true;
}

// The launcher's *Forget the trusted certificate* of server `id`.
async function forgetCert(id) {
  const srv = registry.get(id);
  if (!srv || srv.type !== 'remote' || !registry.dropPin(srv.url)) return false;
  await pinsChanged();
  ctx.pushState();
  return true;
}

async function pinsChanged() {
  trustPins();
  await applyVerifyProc();
  await pushPins();
}

// The pins as the local servers keep them (PUT /api/remote-servers/pins:
// the whole list, snake_case).
function pinRows() {
  return Object.entries(registry.getPins()).map(([origin, p]) => ({
    origin, sha256: p.sha256, subject: p.subject || '', issuer: p.issuer || '', not_before: p.validFrom || '', not_after: p.validTo || '',
  }));
}

// The pins handed to each running local server (or to `serverId` alone, as
// it starts), so their syncing accepts what the window does. Best effort: a
// server that does not take them keeps the list it had.
async function pushPins(serverId = '') {
  const body = JSON.stringify({ pins: pinRows() });
  for (const srv of registry.load().servers) {
    if (srv.type !== 'local' || !sidecar.status(srv.id) || (serverId && srv.id !== serverId)) continue;
    try {
      const { origin, api } = await ctx.localServer(srv.id);
      await api(origin, '/api/remote-servers/pins', { method: 'PUT', headers: { ...ctx.JSON_TYPE, ...ctx.shellWord(origin) }, body });
    } catch (e) {
      if (e.status !== 404) console.error(`[shell] ${srv.name} did not take the trusted certificates: ${(e && e.message) || e}`);
    }
  }
}

// A row of local server `serverId` as its server lists it now: a clone
// (`GET /api/mirrors`) or a vault (`GET /api/folder-links`), never what a
// page said of it.
async function rowOf(kind, serverId, id) {
  const { origin, api } = await ctx.localServer(serverId);
  const row = kind === 'clone'
    ? ((await api(origin, '/api/mirrors')).mirrors || []).find((m) => m.workspace_id === id)
    : ((await api(origin, '/api/folder-links')).links || []).find((l) => l.id === id);
  if (!row || !row.remote_url) throw new Error('That no longer syncs with another server.');
  return { ...row, status: row.status || {} };
}

// The bar's *Trust it for syncing too*: the certificate a local server
// refused for a row of another server is the one the window accepted at
// that host (`accepted`), so it is pinned for its origin after a question,
// with no fingerprint to compare: the window already took that exact one.
async function trustForSync(kind, serverId, id) {
  const row = await rowOf(kind, serverId, id);
  const origin = registry.originOf(row.remote_url);
  const host = hostOnly(origin);
  const cert = row.status.cert || {};
  const bare = certs.bareHost(new URL(origin).hostname);
  if (row.status.error_kind !== 'certificate' || !/^https:/.test(origin) || !cert.sha256 || accepted.get(bare) !== cert.sha256
    || certs.underCloud(bare, CLOUD_DOMAIN)) {
    throw new Error(`${host}'s certificate is not the one this window trusts: open ${host} in Gamma to check it.`);
  }
  const ok = await ctx.ask({
    type: 'question', buttons: ['Trust it for syncing', 'Cancel'], defaultId: 1, cancelId: 1,
    message: `Trust ${host}'s certificate for syncing too?`,
    detail: `This window already trusts it; Gamma's server on this computer does not. Fingerprint: ${certs.grouped(cert.sha256)}`,
  }, 1);
  if (ok !== 0) return false;
  registry.setPin(origin, { sha256: cert.sha256, subject: cert.subject, issuer: cert.issuer, validFrom: cert.not_before, validTo: cert.not_after });
  await pinsChanged();
  ctx.showNotice(`Gamma's server on this computer trusts ${host}'s certificate now.`);
  return true;
}

// The bar's fixes of a row of another server (keeping.failureOf), each
// reading the row again from its server: Open <server> (its card in the
// launcher, for a certificate), Use <https address> (the move a redirect
// asks for, after the question), Trust it for syncing too.
async function fixRow(item, action) {
  const row = await rowOf(item.kind, item.server, item.id);
  const nas = ctx.remoteAt(row.remote_url);
  if (action === 'open-server') {
    if (!nas) throw new Error(`${hostOnly(row.remote_url)} is not one of your servers.`);
    return ctx.openServer(nas.id).then(ctx.buildMenu, (e) => ctx.loadLauncher(e, nas.id));
  }
  if (action === 'use-https') {
    const to = row.status.error_kind === 'redirected' ? keeping.upgradeOf(row.remote_url, row.status.move_to || '') : '';
    if (!to) throw new Error(`${hostOnly(row.remote_url)} no longer redirects to its https:// address.`);
    const from = registry.originOf(row.remote_url);
    upgrades.delete(from);
    return moveServer(entryMoving(from, to), to, { from });
  }
  return trustForSync(item.kind, item.server, item.id);
}

// What the bar's rows read beside their status (keeping.failureOf).
function netState() {
  const { servers } = registry.load();
  return {
    accepted: Object.fromEntries(accepted), pinned: Object.keys(registry.getPins()),
    httpsSeen: servers.filter((s) => s.type === 'remote' && s.httpsSeen).map((s) => s.url),
    moveFailed: [...upgrades].filter(([, state]) => state === 'failed').map(([from]) => from),
  };
}

function init(shell) {
  ctx = shell;
}

module.exports = {
  init,
  remoteOf,
  beforeMint,
  hostOnly,
  autoUpgrade,
  fixRow,
  netState,
  scopeOf,
  rerouted,
  sentToHttps,
  sentToHttpsBy,
  sawHttps,
  moveServer,
  upgrades,
  accepted,
  trustPins,
  certRecord,
  verifyProc,
  applyVerifyProc,
  certificateError,
  certificateRefused,
  certCard,
  trustCert,
  forgetCert,
  pinsChanged,
  pushPins,
  trustForSync,
};
