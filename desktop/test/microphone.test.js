// The page's permission requests (lib/microphone.js): everything granted,
// but the microphone on macOS only once the system allows it. `npm test`
// in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const { create } = require('../lib/microphone');

// Electron's systemPreferences with the system's answer `status`; the
// user's answer to the question, when it is asked, is `answer`.
function setup({ platform = 'darwin', status = 'granted', answer = true } = {}) {
  const asked = [];
  const denied = [];
  const prefs = {
    getMediaAccessStatus: () => status,
    askForMediaAccess: async (type) => {
      asked.push(type);
      if (answer instanceof Error) throw answer;
      return answer;
    },
  };
  const handler = create({ platform, prefs, onDenied: (s) => denied.push(s) });
  const request = (permission, details = {}) => new Promise((resolve) => handler(null, permission, resolve, details));
  return { asked, denied, request };
}

const MIC = { mediaTypes: ['audio'] };

test('every other permission is granted, as Electron grants it without a handler', async () => {
  const { asked, request } = setup({ status: 'denied' });
  assert.equal(await request('notifications'), true);
  assert.equal(await request('clipboard-sanitized-write'), true);
  assert.equal(await request('media', { mediaTypes: ['video'] }), true);
  assert.deepEqual(asked, []);
});

test('outside macOS the microphone is granted without asking the system', async () => {
  const { asked, denied, request } = setup({ platform: 'win32', status: 'denied' });
  assert.equal(await request('media', MIC), true);
  assert.deepEqual(asked, []);
  assert.deepEqual(denied, []);
});

test('macOS: a microphone the system already allows is granted', async () => {
  const { asked, request } = setup({ status: 'granted' });
  assert.equal(await request('media', MIC), true);
  assert.deepEqual(asked, []);
});

test('macOS: the first time, the system asks and its answer is the page\'s', async () => {
  const yes = setup({ status: 'not-determined', answer: true });
  assert.equal(await yes.request('media', MIC), true);
  assert.deepEqual(yes.asked, ['microphone']);
  const no = setup({ status: 'not-determined', answer: false });
  assert.equal(await no.request('media', MIC), false);
  assert.deepEqual(no.denied, []);
});

test('macOS: a refused or restricted microphone is refused, and the user is told where', async () => {
  for (const status of ['denied', 'restricted']) {
    const { asked, denied, request } = setup({ status });
    assert.equal(await request('media', { mediaTypes: ['audio', 'video'] }), false);
    assert.deepEqual(asked, []);
    assert.deepEqual(denied, [status]);
  }
});

test('macOS: a question that fails refuses the page', async () => {
  const { request } = setup({ status: 'not-determined', answer: new Error('no entitlement') });
  assert.equal(await request('media', MIC), false);
});
