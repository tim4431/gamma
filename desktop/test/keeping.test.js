// The bar's reading of what this computer keeps (lib/keeping.js): a clone's
// states in the order Gamma's sync pill checks them, a folder's, and the one
// summary the sync button shows. `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const keeping = require('../lib/keeping');

const local = { id: 'l1', name: 'Local', type: 'local' };
const servers = [local, { id: 'r1', name: 'NAS', type: 'remote', url: 'http://nas.lan:9001' }];
const now = new Date().toISOString();
const clone = (info) => keeping.clone(local, { workspace_id: 'w1', name: 'Lab (clone)', remote_name: 'Lab', remote_url: 'http://nas.lan:9001', ...info }, servers);
const folder = (link) => keeping.folder(local, { id: 'f1', folder_id: 'x', dest: 'C:\\Papers\\Quantum', remote_url: '', ...link }, servers);

test('a clone reads as the sync pill reads it, in the same order', () => {
  assert.equal(clone({ mode: 'off', status: { running: true } }).state, 'detached');
  assert.equal(clone({ status: { running: true, last_error: 'x' }, conflicts_open: 2 }).state, 'busy');
  assert.equal(clone({ status: { last_error: 'x' }, conflicts_open: 2 }).text, '2 conflicts');
  const down = clone({ status: { last_error: 'cannot reach http://nas.lan:9001', last_sync: now } });
  assert.equal(down.state, 'error');
  assert.match(down.text, /^Remote unreachable/);
  assert.equal(clone({ status: {} }).text, 'Not cloned yet');
  assert.equal(clone({ status: { last_sync: now }, pending_local: true, on_change: true }).state, 'busy');
  assert.equal(clone({ status: { last_sync: now }, pending_local: true }).state, 'pending');
  const ok = clone({ status: { last_sync: now } });
  assert.equal(ok.state, 'ok');
  assert.match(ok.text, /^Up to date · /);
  assert.equal(ok.where, 'Clone of Lab on NAS', 'the remote by its registered name');
  assert.equal(ok.key, 'clone:l1:w1');
});

test('a folder: syncing, a problem, not written yet, or up to date with files left alone', () => {
  assert.equal(folder({ status: { running: true } }).state, 'busy');
  assert.equal(folder({ status: { last_error: 'disk full' } }).title, 'disk full');
  assert.equal(folder({ status: {} }).state, 'new');
  const kept = folder({ status: { last_sync: now, kept: [{ path: 'a.md', why: 'changed' }], folder_path: ['Papers', 'Quantum'] } });
  assert.equal(kept.state, 'ok');
  assert.equal(kept.name, 'Quantum');
  assert.match(kept.text, /· 1 changed here$/);
  assert.equal(folder({ status: {} }).name, 'Quantum', 'named from its directory before a round');
  assert.equal(folder({ folder_id: 'root', status: {} }).name, 'Whole library');
  assert.equal(folder({ remote_url: 'http://other.example:9001', status: {} }).from, 'other.example:9001');
});

test('the button shows the worst state; a detached clone never raises it', () => {
  assert.equal(keeping.summarize([]).state, 'none');
  const ok = folder({ status: { last_sync: now } });
  const busy = folder({ id: 'f2', status: { running: true } });
  const bad = clone({ status: { last_error: 'x', last_sync: now } });
  const off = clone({ workspace_id: 'w2', mode: 'off' });
  assert.deepEqual(keeping.summarize([ok, off]), { state: 'ok', text: 'Synced', title: '1 clone and 1 folder on disk on this computer: synced', clones: 1, folders: 1 });
  assert.equal(keeping.summarize([ok, busy]).text, 'Syncing…');
  assert.equal(keeping.summarize([ok, busy, bad]).text, '1 problem');
  assert.equal(keeping.summarize([off]).state, 'ok');
});
