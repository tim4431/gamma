// The page's keepalive requests, through one ledger (docs/dev/collab.md
// "Keepalive"). Browsers carry at most 64 KiB of keepalive request bodies
// in flight per page and refuse a keepalive fetch past that (a network
// error: nothing is sent), so every sender asks here, by priority:
//   'critical' — no other copy: the queued page edits (collabSession's
//                pagehide). The cap, less what is in flight.
//   'normal'   — small and recoverable: the profile, the reading position,
//                the discard of an unfinished upload, an Inbox mark. That,
//                less what the critical senders claim.
//   'low'      — journal-backed: handwriting (ink/inkSaver.js flushInk).
//                That, less NORMAL_RESERVE kept for the normal senders.
// A sender with no other copy claims what it will send at pagehide
// (registerKeepaliveClaim); a claim is read only when a lower priority asks
// for room, once a task however often it asks, never on an edit. A request
// that does not fit its room goes as a plain fetch, which a closing page may
// cut off but the browser never refuses; one with no body always fits. A
// request's bytes count until its answer's body is in, as the browser counts
// them. A lower priority's request from before can still hold room the
// edits need once they have grown (a drawing uploading since the tab was
// last hidden): then the edits go with keepalive and as a plain copy at
// once (keepaliveFetch). No request is aborted to make room: a browser frees
// an aborted keepalive request's share a task later (Chromium) or once its
// answer is in (WebKit), never in the task that needs it, so the request it
// was freed for would be refused (docs/research/keepalive-abort.md). The
// core is pure (createKeepaliveLedger over an injected fetch), so
// tests/keepalive.test.mjs pins the policy; the page's own ledger and its
// wrappers are at the end.

export const KEEPALIVE_CAP = 64 * 1024;
export const NORMAL_RESERVE = 8 * 1024;

// A string's UTF-8 length, as a fetch encodes it (a lone surrogate as
// U+FFFD, three bytes).
const utf8Length = (text) => new TextEncoder().encode(text).length;

// The bytes a fetch sends `body` as: a string's UTF-8 (a string longer than
// `cap` is not measured: its UTF-8 is no shorter, so its length, past the
// cap already, stands for it), a Blob's size, an ArrayBuffer's or a view's
// byteLength, URLSearchParams as their text, 0 for none. A body whose length
// is not known before it goes (FormData, a stream) is Infinity: it never
// goes with keepalive.
export function bodyBytes(body, cap = Infinity) {
  if (body == null) return 0;
  if (typeof body === "string") return body.length > cap ? body.length : utf8Length(body);
  if (typeof Blob !== "undefined" && body instanceof Blob) return body.size;
  if (body instanceof ArrayBuffer || ArrayBuffer.isView(body)) return body.byteLength;
  if (typeof URLSearchParams !== "undefined" && body instanceof URLSearchParams) return utf8Length(body.toString());
  return Infinity;
}

// What a claim stands for: the bytes of `bodies` that go with keepalive
// when they are sent in order, each when it fits, on a page with nothing
// else in flight. A body over the cap goes plain, so it claims nothing.
export function keepaliveShare(bodies) {
  let left = KEEPALIVE_CAP;
  for (const body of bodies) {
    const n = bodyBytes(body, KEEPALIVE_CAP);
    if (n <= left) left -= n;
  }
  return KEEPALIVE_CAP - left;
}

// A ledger: `fetch(url, init)` is the transport (the global fetch, looked
// up at each call, by default). → {available, fetch, registerClaim,
// unregisterClaim, inFlight}.
export function createKeepaliveLedger({ fetch: transport } = {}) {
  const send = transport || ((url, init) => globalThis.fetch(url, init));
  let inFlight = 0;
  const claims = new Map();   // key → fn() → bytes
  // Each claim's bytes, read once a task: the page edits' claim serializes
  // their whole queue, and one flush asks for room once per request. The
  // reads are forgotten in a microtask, so the next task (an edit typed
  // since) reads afresh, and at once when a claim is registered or dropped
  // or a critical request goes (it spends what its sender claimed).
  let memo = null;   // key → bytes
  const claimOf = (key, fn) => {
    if (!memo) {
      const m = memo = new Map();
      queueMicrotask(() => { if (memo === m) memo = null; });
    }
    if (!memo.has(key)) {
      let bytes = 0;
      try { bytes = fn(); } catch { /* a claim that fails claims nothing */ }
      memo.set(key, Number.isFinite(bytes) && bytes > 0 ? bytes : 0);
    }
    return memo.get(key);
  };
  const claimed = () => {
    let n = 0;
    for (const [key, fn] of claims) n += claimOf(key, fn);
    return n;
  };
  let criticalInFlight = 0;   // of inFlight, the critical requests'
  // The body bytes a request of `priority` may carry with keepalive now;
  // never below 0, so a request with no body (a DELETE) always fits: it
  // takes no room, and the browser never refuses it.
  function available(priority = "normal") {
    let room = KEEPALIVE_CAP - inFlight;
    if (priority !== "critical") room -= claimed() + (priority === "low" ? NORMAL_RESERVE : 0);
    return Math.max(0, room);
  }
  // fetch(url, init) with keepalive when the body fits the priority's room,
  // else as a plain fetch; an init's own `keepalive` is not asked. A
  // critical request short of room only because lower priorities' requests
  // hold it goes both ways at once: with keepalive, which a browser leaving
  // the page for another lets through (a navigation or a reload no longer
  // counts the page's requests from before) and a closing tab refuses, and
  // as the plain copy it would be otherwise; its batch id makes the copy
  // that lands second a replay. → the fetch's promise (the keepalive
  // copy's, or the plain copy's when that one fails).
  function keepaliveFetch(url, init = {}, { priority = "normal" } = {}) {
    if (priority === "critical") memo = null;
    const { keepalive: _asked, ...plain } = init || {};
    const bytes = bodyBytes(plain.body, KEEPALIVE_CAP);
    if (bytes <= available(priority)) return counted(url, plain, bytes, priority);
    if (priority !== "critical" || !(bytes <= KEEPALIVE_CAP - criticalInFlight)) return send(url, plain);
    const sent = counted(url, plain, bytes, priority);
    const copy = Promise.resolve(send(url, plain));
    copy.catch(() => { /* the keepalive copy's answer stands for it */ });
    return Promise.resolve(sent).catch(() => copy);
  }
  // fetch(url, init) with keepalive, its bytes counted until the answer's
  // body is in, or the request fails.
  function counted(url, plain, bytes, priority) {
    const critical = priority === "critical";
    inFlight += bytes;
    if (critical) criticalInFlight += bytes;
    let settled = false;
    const release = () => {
      if (settled) return;
      settled = true;
      inFlight -= bytes;
      if (critical) criticalInFlight -= bytes;
    };
    // The browser counts the request until its answer's body is in, a
    // moment after the fetch's promise (the headers): the ledger holds the
    // bytes as long, reading a copy of the answer to its end.
    const untilBody = (answer) => {
      let rest = null;
      try { rest = typeof answer?.clone === "function" ? answer.clone().arrayBuffer() : null; } catch { /* a used body */ }
      if (rest && typeof rest.then === "function") rest.then(release, release);
      else release();
    };
    let sent;
    try { sent = send(url, { ...plain, keepalive: true }); } catch (err) { release(); throw err; }
    Promise.resolve(sent).then(untilBody, release);
    return sent;
  }
  // `fn() → bytes`: what a sender with no other copy will send at pagehide,
  // held off the lower priorities' room. → the unregister function.
  function registerClaim(key, fn) {
    claims.set(key, fn);
    memo = null;
    return () => { if (claims.get(key) === fn) unregisterClaim(key); };
  }
  function unregisterClaim(key) {
    claims.delete(key);
    memo = null;
  }
  return {
    available, fetch: keepaliveFetch, registerClaim, unregisterClaim,
    get inFlight() { return inFlight; },
  };
}

// The page's ledger: every keepalive request of the page goes through it.
const page = createKeepaliveLedger();
export const keepaliveFetch = page.fetch;
export const available = page.available;
export const registerKeepaliveClaim = page.registerClaim;
