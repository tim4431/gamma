// The page's keepalive requests, through one ledger (docs/dev/collab.md
// "Keepalive"). Browsers carry at most 64 KiB of keepalive request bodies
// in flight per page and refuse a keepalive fetch past that (a network
// error: nothing is sent), so every sender asks here, by priority:
//   'critical' — no other copy: the queued page edits (collabSession's
//                pagehide). The cap, less what is in flight.
//   'normal'   — small and recoverable: the profile, the reading position,
//                the discard of an unfinished upload. That, less what the
//                critical senders claim.
//   'low'      — journal-backed: handwriting (App's flushInk). That, less
//                NORMAL_RESERVE kept for the normal senders.
// A sender with no other copy claims what it will send at pagehide
// (registerKeepaliveClaim); a claim is read only when a lower priority asks
// for room, never on an edit. A request that does not fit its room goes as
// a plain fetch, which a closing page may cut off but the browser never
// refuses; one with no body always fits. A request's bytes count until its
// answer's body is in, as the browser counts them. The core is pure
// (createKeepaliveLedger over an injected fetch), so
// tests/keepalive.test.mjs pins the policy; the page's own ledger and its
// wrappers are at the end.

export const KEEPALIVE_CAP = 64 * 1024;
export const NORMAL_RESERVE = 8 * 1024;

// A string's UTF-8 length, as a fetch encodes it (a lone surrogate as
// U+FFFD, three bytes).
function utf8Length(text) {
  let n = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    if (c < 0x80) n += 1;
    else if (c < 0x800) n += 2;
    else if (c >= 0xd800 && c <= 0xdbff && i + 1 < text.length
      && text.charCodeAt(i + 1) >= 0xdc00 && text.charCodeAt(i + 1) <= 0xdfff) { n += 4; i++; }
    else n += 3;
  }
  return n;
}

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
export function keepaliveShare(bodies, cap = KEEPALIVE_CAP) {
  let left = cap;
  for (const body of bodies) {
    const n = bodyBytes(body, cap);
    if (n <= left) left -= n;
  }
  return cap - left;
}

// A ledger: `fetch(url, init)` is the transport (the global fetch, looked
// up at each call, by default). → {available, fetch, registerClaim,
// unregisterClaim, inFlight}.
export function createKeepaliveLedger({ cap = KEEPALIVE_CAP, reserve = NORMAL_RESERVE, fetch: transport } = {}) {
  const send = transport || ((url, init) => globalThis.fetch(url, init));
  let inFlight = 0;
  const claims = new Map();   // key → fn() → bytes
  const claimed = () => {
    let n = 0;
    for (const fn of claims.values()) {
      let bytes = 0;
      try { bytes = fn(); } catch { /* a claim that fails claims nothing */ }
      if (Number.isFinite(bytes) && bytes > 0) n += bytes;
    }
    return n;
  };
  // The body bytes a request of `priority` may carry with keepalive now;
  // never below 0, so a request with no body (a DELETE) always fits: it
  // takes no room, and the browser never refuses it.
  function available(priority = "normal") {
    const room = cap - inFlight;
    if (priority === "critical") return room;
    const rest = room - claimed() - (priority === "low" ? reserve : 0);
    return Math.max(0, rest);
  }
  // fetch(url, init) with keepalive when the body fits the priority's room
  // (its bytes then count until the answer's body is in, or the request
  // fails), else as a plain fetch; an init's own `keepalive` is not asked.
  // → the fetch's promise.
  function keepaliveFetch(url, init = {}, { priority = "normal" } = {}) {
    const { keepalive: _asked, ...plain } = init || {};
    const bytes = bodyBytes(plain.body, cap);
    if (!(bytes <= available(priority))) return send(url, plain);
    inFlight += bytes;
    let settled = false;
    const release = () => { if (!settled) { settled = true; inFlight -= bytes; } };
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
    return () => { if (claims.get(key) === fn) claims.delete(key); };
  }
  return {
    available, fetch: keepaliveFetch, registerClaim,
    unregisterClaim: (key) => { claims.delete(key); },
    get inFlight() { return inFlight; },
  };
}

// The page's ledger: every keepalive request of the page goes through it.
const page = createKeepaliveLedger();
export const keepaliveFetch = page.fetch;
export const available = page.available;
export const registerKeepaliveClaim = page.registerClaim;
export const unregisterKeepaliveClaim = page.unregisterClaim;
