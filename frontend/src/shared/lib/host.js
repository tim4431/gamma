// The native host the web app runs in, asked in one place (docs/dev/ipad.md).
// The iPad app is a WKWebView shell over this web app: before the bundle
// runs, its user script declares `window.gammaNative` (the host's platform,
// its version and what it can do: {platform, version, ink, pencil, servers})
// and a `receive(msg)` stub that queues what native sends until this module
// takes over. Feature code asks nativeHost() for a capability, sends with
// postToHost and listens with onHostMessage; nothing else in the app knows a
// host exists. In a browser every answer here is "no host".

let host;
// The host object as the user script declared it, or null; read once, since
// it never changes for the life of the page.
export function nativeHost() {
  if (host === undefined) {
    const h = globalThis.window?.gammaNative;
    host = h && typeof h === "object" && typeof h.platform === "string" ? h : null;
  }
  return host;
}

// What the app runs inside: the native host's platform ("ipad"), the
// installed home-screen app ("standalone"), or null for a browser tab. Both
// shells take the full-screen rules (app.css keys off data-shell).
export function appShell() {
  if (nativeHost()) return nativeHost().platform;
  const w = globalThis.window;
  return w?.matchMedia?.("(display-mode: standalone)").matches || w?.navigator?.standalone ? "standalone" : null;
}

// One message to native (WKScriptMessageHandlerWithReply, handler "gamma"):
// a Promise of the host's reply; rejects when there is no host.
export function postToHost(msg) {
  const handler = globalThis.window?.webkit?.messageHandlers?.gamma;
  if (!nativeHost() || !handler) return Promise.reject(new Error("no native host"));
  try {
    return Promise.resolve(handler.postMessage(msg));
  } catch (err) {
    return Promise.reject(err);
  }
}

// One handler per message type; native awaits what it returns (the reply to
// callAsyncJavaScript). A type nobody listens for resolves at once with
// nothing, so a newer shell's message never hangs an older page.
const handlers = new Map();

function receive(msg) {
  const fn = handlers.get(msg?.type);
  if (!fn) return Promise.resolve(undefined);
  try {
    return Promise.resolve(fn(msg));
  } catch (err) {
    return Promise.reject(err);
  }
}

// Listen for one message type (a second listener replaces the first: a
// message has one answer); returns the unsubscribe.
export function onHostMessage(type, fn) {
  handlers.set(type, fn);
  return () => { if (handlers.get(type) === fn) handlers.delete(type); };
}

// At load: mark <html> for CSS (data-shell for either shell; data-host as
// the user script already does), and under a host take over its receive
// stub, delivering what the stub queued (`queue`: [{msg, resolve, reject}]).
function install() {
  const root = globalThis.document?.documentElement;
  const shell = appShell();
  if (root && shell) root.setAttribute("data-shell", shell);
  const h = nativeHost();
  if (!h) return;
  if (root) root.setAttribute("data-host", h.platform);
  const queued = Array.isArray(h.queue) ? h.queue : [];
  h.queue = null;
  h.receive = receive;
  for (const { msg, resolve, reject } of queued) receive(msg).then(resolve, reject);
}
install();
