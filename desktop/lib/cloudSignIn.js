// Gamma Cloud sign-in from the desktop app (docs/architecture.md "Gamma Cloud
// sign-in"; the server side is gamma/cloud_auth.py). The page's own start
// (the login page's button, Settings' Link) would send the window to the
// account server, which the navigation guard hands to the system browser,
// and the session the callback mints would land in that browser. So the
// shell takes the start over: it listens on a free loopback port, asks the
// server for a sign-in that reports there (app-start, with the S256
// challenge of a verifier only the shell keeps) and opens it in the system
// browser, where Google's sign-in works (it refuses embedded windows). The
// server sends that browser back to http://127.0.0.1:<port>/cloud-signin
// with a one-time result, and the content view redeems it with the verifier
// (app-claim), so the session cookie lands in the window. Only this computer
// reaches that address (RFC 8252 §7.3): a sign-in link someone else started
// and sent here never hands its result back to them.
//
// Node-only (no Electron), so node --test covers it (test/cloudSignIn.test.js).

const http = require('http');
const crypto = require('crypto');

const START_PATH = '/api/auth/cloud/start';
const APP_START_PATH = '/api/auth/cloud/app-start';
const CLAIM_PATH = '/api/auth/cloud/app-claim';
const RETURN_PATH = '/cloud-signin';
const WAIT_MS = 10 * 60_000; // as long as the server keeps a pending sign-in

// Is `url` the page's Cloud sign-in start, which the shell takes over?
function isStart(url) {
  try {
    return new URL(url).pathname === START_PATH;
  } catch {
    return false;
  }
}

// What the page's start asked for: where to land afterwards, and whether to
// link the signed-in account instead of signing in.
function startParams(url) {
  const q = new URL(url).searchParams;
  return { next: q.get('next') || '/', link: Boolean(q.get('link')) };
}

const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

// The browser tab's last page: what happened, and that the app has it now.
function page(error) {
  const [title, lines] = error
    ? ['Sign-in did not finish', [error, 'Go back to the Gamma app to try again.']]
    : ['Signed in to Gamma', ['You can close this tab and go back to the Gamma app.']];
  return `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Gamma</title>
<style>body{font:15px/1.5 system-ui,sans-serif;max-width:28rem;margin:18vh auto;padding:0 1.5rem;color:#333;background:#f5f5f5}
h1{font-size:1.25rem;font-weight:600}@media (prefers-color-scheme:dark){body{color:#ddd;background:#111}}</style>
<h1>${title}</h1>${lines.map((l) => `<p>${escapeHtml(l)}</p>`).join('')}`;
}

let active = null; // the sign-in waiting for its browser; one at a time

// Starts waiting for the browser's return. Resolves, once listening, with
// { returnTo, challenge, verifier, answer, end }: `answer` resolves with
// { result } or { error } from the browser, or null when a newer sign-in
// replaces this one, `end(null)` gives up on it, or WAIT_MS passes.
function listen({ waitMs = WAIT_MS } = {}) {
  if (active) active.end(null);
  const verifier = crypto.randomBytes(32).toString('base64url');
  const challenge = crypto.createHash('sha256').update(verifier).digest('base64url');
  let settle;
  const answer = new Promise((resolve) => { settle = resolve; });
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    const result = url.searchParams.get('result') || '';
    const error = url.searchParams.get('error') || '';
    if (req.method !== 'GET' || url.pathname !== RETURN_PATH || (!result && !error)) {
      res.writeHead(404, { 'Content-Type': 'text/plain' }).end('Not found');
      return;
    }
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
    res.end(page(error));
    flow.end(result ? { result } : { error });
  });
  let timer = null;
  const flow = {
    verifier,
    challenge,
    answer,
    returnTo: '',
    end(value) {
      clearTimeout(timer);
      server.close();
      if (active === flow) active = null;
      settle(value);
    },
  };
  active = flow;
  timer = setTimeout(() => flow.end(null), waitMs);
  return new Promise((resolve, reject) => {
    server.once('error', (e) => {
      flow.end(null);
      reject(e);
    });
    server.listen(0, '127.0.0.1', () => {
      flow.returnTo = `http://127.0.0.1:${server.address().port}${RETURN_PATH}`;
      resolve(flow);
    });
  });
}

// The content view's request that redeems a result: loadURL's arguments. The
// header is what tells the server the window, not some page, is claiming.
function claimRequest(origin, flow, result) {
  return [origin + CLAIM_PATH, {
    postData: [{ type: 'rawData', bytes: Buffer.from(new URLSearchParams({ result, verifier: flow.verifier }).toString()) }],
    extraHeaders: 'Content-Type: application/x-www-form-urlencoded\nX-Gamma-Desktop: claim',
  }];
}

module.exports = { APP_START_PATH, RETURN_PATH, isStart, startParams, listen, claimRequest, page };
