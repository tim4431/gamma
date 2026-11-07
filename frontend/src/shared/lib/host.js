// The native host the web app runs in, asked in one place (docs/dev/ipad.md
// "The host contract"). The iPad and Android apps are web-view shells over
// this web app: before the bundle runs, the shell's document-start script
// declares `window.gammaNative` — the host's platform ("ipad", "android"),
// its version and what it can do: {platform, version, ink (it captures
// claimed pen strokes), inkLooks (what of the claim's look its live line
// draws, `snap` the line along the ruler's edge among them), hold (it sends
// `hold` when a captured pen stays still), pencil (it forwards the pen's
// gestures: the Apple Pencil's double tap and squeeze, a stylus's button
// clicks), pencilGestures (which of them, of "tap" and "squeeze"; a host
// that declares `pencil` without the list, as the iPad app does, forwards
// both), pencilPhases (a squeeze's start and cancel come too, as
// `squeeze`), pencilRoll (its samples carry the pen's barrel roll), haptics
// (it plays `haptic`), openFiles (it holds the files the system opens in
// the app), shellBar (its own bar above the page
// can be hidden, and `shellBar` sets and reports it), save (it takes the
// files the web saves, shared/lib/saveFile.js), editMenu (it turns its own
// edit menu over a selection off while the page says, `editMenu`)} — a
// `receive(msg)` stub that queues what native sends until this module
// takes over, and, where the shell's transport is not WebKit's reply
// handler, `post(msg)`, a promise of native's reply (Android's:
// android/app/src/main/assets/shell/host.js). A flag the host leaves out is
// a thing it does not do, and the web leaves it out too. The web reads
// neither `version`, which only names the build (a problem report quotes
// it), nor `inkLooks`, which only says what the shell's live line can draw:
// a claim carries its whole look either way.
// The messages: the web posts `claim` (the ink layer), `theme` (the colour,
// the scheme and the theme's name) and `haptic` (App), `files`, `fileData`
// and `fileDone` (transfers/openedFiles.js), `shellBar`
// (app/useShellBar.js), `editMenu` (the PDF viewer), and `save`, `saveData`,
// `saveDone` and `saveUrl` (saveFile.js); native sends `stroke` and `hold`
// (the ink layer), `pencil` and `squeeze` (App), `filesOpened`
// (openedFiles.js) and `shellBar`.
// Feature code asks nativeHost() for a capability, sends with postToHost and
// listens with onHostMessage; nothing else in the app knows a host exists.
// In a browser every answer here is "no host".

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

// What the app runs inside: the native host's platform ("ipad", "android"), the
// installed home-screen app ("standalone"), or null for a browser tab. Both
// shells take the full-screen rules (app.css keys off data-shell).
export function appShell() {
  if (nativeHost()) return nativeHost().platform;
  const w = globalThis.window;
  return w?.matchMedia?.("(display-mode: standalone)").matches || w?.navigator?.standalone ? "standalone" : null;
}

// One message to native: a Promise of the host's reply; rejects when there
// is no host. The host's own `post` when it declares one (Android: its
// message port, each post paired with its reply by the document-start
// script), else WebKit's reply handler "gamma" (the iPad:
// WKScriptMessageHandlerWithReply).
export function postToHost(msg) {
  const h = nativeHost();
  const handler = globalThis.window?.webkit?.messageHandlers?.gamma;
  const send = typeof h?.post === "function" ? (m) => h.post(m) : handler ? (m) => handler.postMessage(m) : null;
  if (!h || !send) return Promise.reject(new Error("no native host"));
  try {
    return Promise.resolve(send(msg));
  } catch (err) {
    return Promise.reject(err);
  }
}

// One handler per message type; native awaits what it returns (the reply to
// the iPad's callAsyncJavaScript, the `result` Android's script posts back).
// A type nobody listens for resolves at once with nothing, so a newer
// shell's message never hangs an older page.
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
