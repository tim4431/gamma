// What a page in the window may use (lib/permissions.js): a page of this
// computer as before, a remote page only what it is allowed, the
// microphone, the camera and reading the clipboard after a question kept
// until quit, nothing else; and the frames under a remote page. `npm test`
// in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const { create, frameAllowed } = require('../lib/permissions');

const LOCAL = 'http://127.0.0.1:50999';
const BAR = 'file:///C:/Gamma/ui/bar.html';
const isLocal = (url) => url === BAR || url.startsWith(`${LOCAL}/`) || url === LOCAL;

// The handlers with the person's answers `answers` (0 allows, 1 does not),
// what they were asked (`asked`), and the microphone handler's calls.
function setup({ answers = [], hidden = false } = {}) {
  const asked = [];
  const mic = [];
  const handlers = create({
    isLocal,
    ask: async (opts) => {
      asked.push(opts);
      return answers.length ? answers.shift() : 1;
    },
    microphone: (_wc, permission, callback, details) => {
      mic.push([permission, details && details.mediaTypes]);
      callback(true);
    },
    hidden: () => hidden,
  });
  const request = (permission, url, more = {}) => new Promise((resolve) =>
    handlers.request(null, permission, resolve, { requestingUrl: url, ...more }));
  const check = (permission, url, more = {}) => handlers.check(null, permission, url ? new URL(url).origin : '', { requestingUrl: url, ...more });
  return { asked, mic, request, check, handlers };
}

const NAS = 'https://nas.lab:5001/?ws=team';

test('a page of this computer keeps what it had: every request through the microphone handler, every check yes', async () => {
  const { asked, mic, request, check } = setup();
  for (const p of ['geolocation', 'media', 'openExternal', 'notifications', 'midi']) {
    assert.equal(await request(p, `${LOCAL}/?ws=mine`, { mediaTypes: ['audio'] }), true, p);
  }
  assert.equal(await request('clipboard-read', BAR), true, 'a shell page too');
  assert.equal(mic.length, 6);
  assert.deepEqual(asked, []);
  for (const p of ['geolocation', 'media', 'hid', 'clipboard-read']) assert.equal(check(p, `${LOCAL}/`), true, p);
});

test('a remote page gets fullscreen, writing the clipboard and notifications, and nothing else unasked', async () => {
  const { asked, mic, request, check } = setup();
  for (const p of ['fullscreen', 'clipboard-sanitized-write', 'notifications']) {
    assert.equal(await request(p, NAS), true, p);
    assert.equal(check(p, NAS), true, p);
  }
  for (const p of ['geolocation', 'openExternal', 'pointerLock', 'midi', 'midiSysex', 'hid', 'serial', 'usb', 'display-capture',
    'idle-detection', 'storage-access', 'local-network-access', 'something-new']) {
    assert.equal(await request(p, NAS), false, p);
    assert.equal(check(p, NAS), false, p);
  }
  assert.deepEqual([asked, mic], [[], []], 'nothing asked, the microphone handler never reached');
});

test('a request that names no origin is refused', async () => {
  const { request, check, handlers } = setup();
  assert.equal(await new Promise((resolve) => handlers.request(null, 'notifications', resolve, {})), false);
  assert.equal(await request('fullscreen', 'about:blank'), true, 'an allowed kind needs no origin of its own');
  assert.equal(await request('media', 'about:blank', { mediaTypes: ['audio'] }), false, 'an opaque origin is never asked about');
  assert.equal(check('notifications', ''), false);
});

test('the microphone of a remote page is asked once per origin and kind, the answer kept, macOS\'s check after a yes', async () => {
  const { asked, mic, request, check } = setup({ answers: [0, 1] });
  assert.equal(check('media', NAS, { mediaType: 'audio' }), false, 'no device names before a yes');
  assert.equal(await request('media', NAS, { mediaTypes: ['audio'] }), true);
  assert.deepEqual(asked.map((q) => [q.message, q.defaultId, q.cancelId]), [['Let nas.lab:5001 use the microphone?', 1, 1]]);
  assert.match(asked[0].detail, /^A page of https:\/\/nas\.lab:5001 asks for it in Gamma's window\. Gamma keeps your answer until it quits\.$/);
  assert.deepEqual(mic, [['media', ['audio']]], 'then the system\'s own check');
  assert.equal(await request('media', NAS, { mediaTypes: ['audio'] }), true, 'not asked again');
  assert.equal(check('media', NAS, { mediaType: 'audio' }), true);
  // the camera is its own question, and a no is kept too
  assert.equal(await request('media', NAS, { mediaTypes: ['audio', 'video'] }), false);
  assert.equal(asked.at(-1).message, 'Let nas.lab:5001 use the camera?');
  assert.equal(await request('media', NAS, { mediaTypes: ['video'] }), false);
  assert.equal(asked.length, 2);
  assert.equal(check('media', NAS, { mediaType: 'video' }), false);
  // another origin is asked of its own; under the harness's answer, no
  assert.equal(await request('media', 'https://other.example/', { mediaTypes: ['audio'] }), false);
  assert.equal(asked.length, 3);
});

test('two requests at once get one question', async () => {
  const { asked, request } = setup({ answers: [0] });
  const both = await Promise.all([request('media', NAS, { mediaTypes: ['audio'] }), request('media', NAS, { mediaTypes: ['audio'] })]);
  assert.deepEqual(both, [true, true]);
  assert.equal(asked.length, 1);
});

test('reading the clipboard is asked once; its check lets the page ask until the person says no', async () => {
  const { asked, request, check } = setup({ answers: [1] });
  assert.equal(check('clipboard-read', NAS), true, 'so the page goes on to ask');
  assert.equal(await request('clipboard-read', NAS), false);
  assert.equal(asked[0].message, 'Let nas.lab:5001 read what you copied?');
  assert.equal(check('clipboard-read', NAS), false);
  assert.equal(await request('clipboard-read', NAS), false);
  assert.equal(asked.length, 1);
});

test('while an agent\'s question is up, a remote page\'s request is refused unasked and nothing is kept', async () => {
  const state = setup({ hidden: true, answers: [0] });
  assert.equal(await state.request('media', NAS, { mediaTypes: ['audio'] }), false);
  assert.equal(await state.request('notifications', NAS), true, 'what is allowed stays allowed');
  assert.deepEqual(state.asked, []);
  assert.equal(state.check('clipboard-read', NAS), true, 'no answer was kept');
});

test('the frames under a remote page: web schemes only, never a local sidecar; under a page of this computer, anything as before', () => {
  for (const url of ['https://challenges.cloudflare.com/x', 'http://nas.lab/frame', 'about:blank', 'blob:https://nas.lab/1', 'data:text/html,hi']) {
    assert.equal(frameAllowed(url, NAS, isLocal), true, url);
  }
  for (const url of [`${LOCAL}/api/session`, 'file:///C:/Users/tim/secret.html', 'ms-settings:privacy', 'zoommtg://join', 'javascript:alert(1)', 'not a url']) {
    assert.equal(frameAllowed(url, NAS, isLocal), false, url);
  }
  assert.equal(frameAllowed('zoommtg://join', `${LOCAL}/?ws=mine`, isLocal), true);
  assert.equal(frameAllowed(`${LOCAL}/x`, `${LOCAL}/?ws=mine`, isLocal), true);
});
