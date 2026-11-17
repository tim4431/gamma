// lib/updater.js under plain Node, Electron and electron-updater replaced by
// stand-ins: Update now pressed while the update still downloads restarts
// into it once the download is done, and a failed download drops the press.
// `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('events');
const stubModules = require('./stubModules');

Object.defineProperty(process, 'platform', { value: 'linux' }); // in-app install (not macOS)
delete process.env.GAMMA_SHELL_TEST;
delete process.env.GAMMA_SHELL_NO_UPDATE;

const autoUpdater = Object.assign(new EventEmitter(), {
  restarts: 0,
  async checkForUpdates() {},
  quitAndInstall() { this.restarts += 1; },
});
stubModules({
  electron: { app: { getVersion: () => '1.0.0', isPackaged: true } },
  'electron-updater': { autoUpdater },
});

const updater = require('../lib/updater');
updater.init({ onChange: () => {}, openExternal: () => {} });
const tick = () => new Promise((resolve) => setImmediate(resolve));

test('Update now while the update downloads restarts into it once the download is done', async () => {
  autoUpdater.emit('update-available', { version: '2.0.0' });
  assert.equal(updater.state().status, 'downloading');
  assert.equal(updater.install(), 'queued');
  assert.equal(updater.state().installing, true);
  await tick();
  assert.equal(autoUpdater.restarts, 0, 'nothing to install yet');
  autoUpdater.emit('download-progress', { percent: 60 });
  autoUpdater.emit('update-downloaded', { version: '2.0.0' });
  await tick();
  assert.equal(autoUpdater.restarts, 1, 'restarted into it');
});

test('a download that fails after Update now drops the press; one done later waits for the next press', async () => {
  autoUpdater.emit('update-available', { version: '2.0.1' });
  updater.install();
  autoUpdater.emit('error', new Error('net::ERR_CONNECTION_RESET\nstack'));
  assert.deepEqual([updater.state().status, updater.state().installing, updater.state().error], ['error', false, 'net::ERR_CONNECTION_RESET']);
  const before = autoUpdater.restarts;
  autoUpdater.emit('update-available', { version: '2.0.1' });
  autoUpdater.emit('update-downloaded', { version: '2.0.1' });
  await tick();
  assert.equal(autoUpdater.restarts, before, 'not pressed this time');
  assert.equal(updater.install(), 'restart');
  await tick();
  assert.equal(autoUpdater.restarts, before + 1);
});
