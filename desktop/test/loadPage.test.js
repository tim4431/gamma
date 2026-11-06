// loadURL's false alarm (lib/loadPage.js): the rejection it gives while the
// page goes on and lands, against the failures that are real.
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const test = require('node:test');
const { loadPage, WAIT_MS } = require('../lib/loadPage');

const URL_ = 'http://x/?ws=b';
const stopped = () => Object.assign(new Error(`ERR_FAILED (-2) loading '${URL_}'`), { errno: -2, code: 'ERR_FAILED' });
const WRITTEN = 'http://x/?ws=a';
const inPage = (wc) => { wc.here = WRITTEN; wc.emit('did-navigate-in-page', {}, WRITTEN, true); };

// webContents, whose loadURL runs `load`, and settles as it does.
function view(load, { loading = false } = {}) {
  const wc = new EventEmitter();
  wc.isLoading = () => loading;
  wc.isDestroyed = () => false;
  wc.here = 'http://x/';
  wc.getURL = () => wc.here;
  wc.loadURL = async (...args) => { wc.asked = args; return load(wc); };
  return wc;
}
const falseAlarm = (wc) => { inPage(wc); throw stopped(); };

const events = ['did-finish-load', 'did-fail-load', 'did-start-navigation', 'did-navigate-in-page', 'will-redirect', 'destroyed'];
const noneLeft = (wc) => assert.deepEqual(events.map((e) => wc.listenerCount(e)), events.map(() => 0));
const settled = async (p) => {
  let done = false;
  p.finally(() => { done = true; }).catch(() => {});
  await new Promise((r) => setImmediate(r));
  return done;
};

test('a load that goes well is just that, and passes its options on', async () => {
  const wc = view(async () => {});
  await loadPage(wc, URL_, { httpReferrer: 'r' });
  assert.deepEqual(wc.asked, [URL_, { httpReferrer: 'r' }]);
  noneLeft(wc);
});

test('the page being left writes its address: the wait lasts until the page lands', async () => {
  const wc = view(falseAlarm);
  const load = loadPage(wc, URL_);
  assert.equal(await settled(load), false, 'not while the page is on its way');
  wc.emit('did-start-navigation', {}, URL_, false, true); // this load's own
  assert.equal(await settled(load), false);
  wc.emit('did-finish-load');
  await load;
  noneLeft(wc);
});

test('the load before it, still finishing, is the other false alarm', async () => {
  const wc = view(async () => { throw stopped(); }, { loading: true });
  const load = loadPage(wc, URL_);
  assert.equal(await settled(load), false);
  wc.emit('did-finish-load');
  await load;
});

test('the address written before the load has begun resolves loadURL: the wait lasts until the page lands', async () => {
  const wc = view(async (v) => inPage(v));
  const load = loadPage(wc, URL_);
  assert.equal(await settled(load), false, 'the next load would cancel this one');
  wc.emit('did-start-navigation', {}, URL_, false, true);
  assert.equal(await settled(load), false);
  wc.emit('did-finish-load');
  await load;
  noneLeft(wc);
});

test('a load that only changes the address is done when the address is', async () => {
  const wc = view(async (v) => { v.here = URL_; v.emit('did-navigate-in-page', {}, URL_, true); });
  await loadPage(wc, 'http://x/?ws=b');
  noneLeft(wc);
});

test('a load that finished is done, whatever else went on around it', async () => {
  const wc = view(async (v) => { inPage(v); v.emit('did-finish-load'); });
  await loadPage(wc, URL_);
  noneLeft(wc);
});

test('a page with no address yet is not an error', async () => {
  const wc = view(async (v) => { v.emit('did-navigate-in-page', {}, WRITTEN, true); v.here = ''; }, { loading: true });
  const load = loadPage(wc, URL_);
  assert.equal(await settled(load), false);
  wc.emit('did-finish-load');
  await load;
});

test('a load cancelled outright fails at once: the redirect guard sends no did-fail-load', async () => {
  const wc = view(async () => { throw stopped(); });
  await assert.rejects(loadPage(wc, URL_), /ERR_FAILED \(-2\)/);
  noneLeft(wc);
});

test('a redirect the guard refused fails at once, even with an overlap about', async () => {
  const before = view(async (v) => { inPage(v); v.emit('will-redirect', { defaultPrevented: true }); throw stopped(); });
  await assert.rejects(loadPage(before, URL_), /ERR_FAILED \(-2\)/);
  noneLeft(before);
  const resolved = view(async (v) => { v.emit('will-redirect', { defaultPrevented: true }); inPage(v); });
  await loadPage(resolved, URL_); // as loadURL had it, with nothing left to wait for
  noneLeft(resolved);
});

test('a redirect the guard refuses during the wait ends it; one it allows does not', async () => {
  const wc = view(falseAlarm);
  const load = loadPage(wc, URL_);
  await settled(load);
  wc.emit('will-redirect', { defaultPrevented: false });
  assert.equal(await settled(load), false);
  wc.emit('will-redirect', { defaultPrevented: true });
  await assert.rejects(load, /ERR_FAILED \(-2\)/);
  noneLeft(wc);
});

test('an address written in a frame says nothing of the load', async () => {
  const wc = view(async (v) => { v.emit('did-navigate-in-page', {}, 'http://x/frame#a', false); throw stopped(); });
  await assert.rejects(loadPage(wc, URL_), /ERR_FAILED \(-2\)/);
});

test('a newer load takes over: this one is aborted, and never takes that one\'s landing for its own', async () => {
  const wc = view(falseAlarm);
  const load = loadPage(wc, URL_);
  await settled(load);
  wc.emit('did-start-navigation', {}, 'http://x/?ws=c#in-page', true, true); // the same document
  wc.emit('did-start-navigation', {}, 'http://frame/', false, false); // a frame's
  assert.equal(await settled(load), false);
  wc.emit('did-start-navigation', {}, 'file:///launcher.html', false, true);
  await assert.rejects(load, /ERR_ABORTED \(-3\)/);
  noneLeft(wc);
});

test('a load that fails after a false alarm fails', async () => {
  const wc = view(falseAlarm);
  const load = loadPage(wc, URL_);
  await settled(load);
  wc.emit('did-fail-load', {}, -105, 'ERR_NAME_NOT_RESOLVED', URL_, false); // a frame's: not the load's
  assert.equal(await settled(load), false);
  wc.emit('did-fail-load', {}, -105, 'ERR_NAME_NOT_RESOLVED', URL_, true);
  await assert.rejects(load, /ERR_NAME_NOT_RESOLVED \(-105\)/);
  noneLeft(wc);
});

test('a real ERR_FAILED, which comes with did-fail-load, fails at once', async () => {
  const wc = view(async (v) => {
    inPage(v);
    v.emit('did-fail-load', {}, -2, 'ERR_FAILED', URL_, true);
    throw stopped();
  });
  await assert.rejects(loadPage(wc, URL_), /ERR_FAILED \(-2\)/);
  noneLeft(wc);
});

test('any other rejection is passed on', async () => {
  const wc = view(async (v) => {
    inPage(v);
    throw Object.assign(new Error('ERR_ABORTED (-3)'), { errno: -3, code: 'ERR_ABORTED', url: 'http://x/?ws=newer' });
  });
  await assert.rejects(loadPage(wc, URL_), /ERR_ABORTED/);
  noneLeft(wc);
});

const OLD = 'http://x/?ws=old';
const cancelledOld = (wc) => wc.emit('did-fail-load', {}, -3, '', OLD, true);
const pinned = () => Object.assign(new Error(` (-3) loading '${OLD}'`), { errno: -3, code: '', url: OLD });

test('the load before, cancelled, is not this load\'s failure: rejected as the page lands, it is done', async () => {
  const wc = view(async (v) => { cancelledOld(v); v.emit('did-finish-load'); throw pinned(); });
  await loadPage(wc, URL_);
  noneLeft(wc);
});

test('the load before, cancelled ahead of this one starting: the wait lasts until the page lands', async () => {
  const wc = view(async (v) => { cancelledOld(v); throw pinned(); });
  const load = loadPage(wc, URL_);
  assert.equal(await settled(load), false);
  wc.emit('did-finish-load');
  await load;
  noneLeft(wc);
});

test('a real failure or a refused redirect after the load before was cancelled fails at once', async () => {
  const failing = view(async (v) => { cancelledOld(v); v.emit('did-fail-load', {}, -105, 'ERR_NAME_NOT_RESOLVED', URL_, true); throw pinned(); });
  await assert.rejects(loadPage(failing, URL_), /\(-3\)/);
  noneLeft(failing);
  const refused = view(async (v) => { v.emit('will-redirect', { defaultPrevented: true }); cancelledOld(v); throw pinned(); });
  await assert.rejects(loadPage(refused, URL_), /\(-3\)/);
  noneLeft(refused);
});

test('the load before, cancelled during the wait, does not end it; this load being aborted does', async () => {
  const wc = view(falseAlarm);
  const load = loadPage(wc, URL_);
  await settled(load);
  cancelledOld(wc);
  assert.equal(await settled(load), false);
  wc.emit('did-fail-load', {}, -3, '', URL_, true);
  await assert.rejects(load, /\(-3\)/);
  noneLeft(wc);
});

test('a window closed under the load fails it, during the wait or before it', async () => {
  const during = view(falseAlarm);
  const load = loadPage(during, URL_);
  await settled(load);
  during.emit('destroyed');
  await assert.rejects(load, /ERR_FAILED \(-2\)/);
  noneLeft(during);
  const before = view(falseAlarm);
  before.isDestroyed = () => true;
  await assert.rejects(loadPage(before, URL_), /ERR_FAILED \(-2\)/);
});

test('a load that never ends in a page gives up with the original error', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const wc = view(falseAlarm);
  const load = loadPage(wc, URL_);
  await settled(load);
  t.mock.timers.tick(WAIT_MS);
  await assert.rejects(load, /ERR_FAILED \(-2\)/);
  noneLeft(wc);
});
