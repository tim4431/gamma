// What a page in the window may use of this computer (docs/architecture.md
// "What a server's page may use"). Electron grants a page every permission
// it asks for unless a handler decides. A page of this computer, a running
// local sidecar's or one of the two bundled shell pages (`isLocal`), keeps
// that, through lib/microphone.js as before. Any other page, a NAS's or any
// remote server's, gets only what Gamma's pages there need and a question
// can protect: fullscreen, writing the clipboard and notifications (its
// reminders and its Inbox; a notification answers no card, the bar does) are
// allowed; the microphone, the camera and reading the clipboard are asked
// once per origin and kind, the answer kept until the app quits (macOS's own
// check of the microphone still follows a yes); everything else is
// refused: geolocation, the devices (HID, serial, USB, Bluetooth), an
// external protocol opened from the page, the pointer lock, MIDI and any
// permission this list does not name. A request that says no origin is
// refused. While an agent's question is up (`hidden`: the content view is
// hidden then) a remote page's request that would be asked is refused
// without asking or remembering, so no native question comes up over the
// card at a moment the page chose. No Electron in here: `ask` is main.js's
// native question, `microphone` lib/microphone.js's request handler. Unit
// tested by test/permissions.test.js (`npm test`).

const ALLOWED = new Set(['fullscreen', 'clipboard-sanitized-write', 'notifications']);
const MEDIA = ['audio', 'video'];
const MEDIA_WORDS = { audio: 'the microphone', video: 'the camera' };
const SCHEMES = new Set(['http:', 'https:', 'about:', 'blob:', 'data:']);

const hostOf = (url) => {
  try { return new URL(url).host || url; } catch { return String(url || ''); }
};

const originOf = (url) => {
  try { return new URL(url).origin; } catch { return ''; }
};

function create({ isLocal, ask, microphone, hidden = () => false }) {
  // `${origin} ${kind}` -> true or false, the person's answer this run; the
  // question being asked, by the kinds it asks of an origin, so a page
  // asking twice at once gets one question.
  const answers = new Map();
  const pending = new Map();
  let questions = Promise.resolve(); // one native question at a time

  const urlOf = (details, fallback = '') => (details && (details.requestingUrl || details.securityOrigin)) || fallback || '';

  // The kinds a request names that have no answer yet, asked in one
  // question once the questions before it are answered (one of them may
  // have answered these), each answer recorded. → whether every kind is
  // allowed.
  const allowed = (origin, kinds) => kinds.every((k) => answers.get(`${origin} ${k}`) === true);
  function asked(origin, kinds) {
    const open = () => kinds.filter((k) => !answers.has(`${origin} ${k}`));
    if (!open().length) return Promise.resolve(allowed(origin, kinds));
    const key = `${origin} ${open().join('+')}`;
    if (!pending.has(key)) {
      const run = questions.then(async () => {
        const still = open();
        if (!still.length) return;
        const what = still.map((k) => MEDIA_WORDS[k] || 'what you copied').join(' and ');
        const choice = await Promise.resolve(ask({
          type: 'question', buttons: ['Allow', 'Don\'t allow'], defaultId: 1, cancelId: 1,
          message: `Let ${hostOf(origin)} ${still.includes('clipboard-read') ? 'read' : 'use'} ${what}?`,
          detail: `A page of ${origin} asks for it in Gamma's window. Gamma keeps your answer until it quits.`,
        })).catch(() => 1);
        for (const k of still) answers.set(`${origin} ${k}`, choice === 0);
      }).finally(() => pending.delete(key));
      questions = run.catch(() => {});
      pending.set(key, run);
    }
    return pending.get(key).then(() => allowed(origin, kinds));
  }

  // session.setPermissionRequestHandler's handler.
  function request(webContents, permission, callback, details) {
    const url = urlOf(details);
    if (!url) return callback(false);
    if (isLocal(url)) return microphone(webContents, permission, callback, details);
    if (ALLOWED.has(permission)) return callback(true);
    const origin = originOf(url);
    if (!origin || origin === 'null') return callback(false);
    let kinds = null;
    if (permission === 'media') {
      const types = ((details && details.mediaTypes) || []).filter((t) => MEDIA.includes(t));
      kinds = types.length ? types : null;
    } else if (permission === 'clipboard-read') {
      kinds = ['clipboard-read'];
    }
    if (!kinds || hidden()) return callback(false);
    asked(origin, kinds).then((yes) => {
      if (!yes) return callback(false);
      return permission === 'media' ? microphone(webContents, permission, callback, details) : callback(true);
    }, () => callback(false));
  }

  // session.setPermissionCheckHandler's handler, agreeing with the requests:
  // a page of this computer as Electron answers with no handler (yes); a
  // remote page yes for what it is allowed, for a camera or a microphone
  // only once the person allowed it (a check alone would name the devices),
  // and for reading the clipboard unless the person said no, so that the
  // page goes on to ask and the question comes.
  function check(_webContents, permission, requestingOrigin, details) {
    const url = urlOf(details, requestingOrigin);
    if (!url) return false;
    if (isLocal(url)) return true;
    if (ALLOWED.has(permission)) return true;
    const origin = originOf(url);
    if (!origin || origin === 'null') return false;
    if (permission === 'media') {
      const type = details && details.mediaType;
      const kinds = MEDIA.includes(type) ? [type] : MEDIA;
      return kinds.some((k) => answers.get(`${origin} ${k}`) === true);
    }
    if (permission === 'clipboard-read') return answers.get(`${origin} clipboard-read`) !== false;
    return false;
  }

  return { request, check };
}

// Whether a frame of the window's page at `topUrl` may go to `url`
// (webContents' `will-frame-navigate`, the frames below the page: the page
// itself is the content guard's). Under a page of this computer, as before.
// Under a remote page, only a web scheme (http, https, about, blob, data:
// a sign-in's or a challenge's frame loads), never a running local
// sidecar's origin: no frame on a NAS page reaches this computer's servers,
// nor launches an external protocol.
function frameAllowed(url, topUrl, isLocal) {
  if (isLocal(topUrl)) return true;
  let u;
  try {
    u = new URL(url);
  } catch {
    return false;
  }
  if (!SCHEMES.has(u.protocol)) return false;
  return !(/^https?:$/.test(u.protocol) && isLocal(url));
}

module.exports = { create, frameAllowed };
