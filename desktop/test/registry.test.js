// The local servers the shell starts at launch (lib/registry.js launchHosts,
// main.js startBackgroundHosts): the clones' hosts, the folder host and the
// servers that hold a Zotero link that is not detached (setZoteroHost, as
// refreshKeeping last read their links), or every local server in
// background mode. `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const registry = require('../lib/registry');

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-registry-'));
const file = path.join(dir, 'servers.json');
registry.init(dir);
test.after(() => fs.rmSync(dir, { recursive: true, force: true }));

const names = (list) => list.map((s) => s.name).sort();

// A registry of its own for each test: a clone's host, the folder host, a
// server for Zotero, one that keeps nothing, and a remote.
function servers() {
  fs.rmSync(file, { force: true });
  const s = {
    clone: registry.addLocal('Clone host'),
    folder: registry.addLocal('Folder host'),
    zotero: registry.addLocal('Zotero'),
    idle: registry.addLocal('Idle'),
    remote: registry.addRemote('NAS', 'http://nas.lan:9001'),
  };
  registry.addMirror({ server: s.clone.id, workspace: 'w1', name: 'Lab', remoteUrl: 'http://nas.lan:9001', remoteWs: 'r1', remoteName: 'Lab' });
  registry.setSettings({ folderHost: s.folder.id });
  return s;
}

test('a server with a Zotero link that is not detached starts at launch, beside the clones\' and folders\' hosts', () => {
  const s = servers();
  assert.deepEqual(names(registry.launchHosts()), ['Clone host', 'Folder host'], 'nothing for Zotero recorded yet');
  registry.setZoteroHost(s.zotero.id, true);
  assert.equal(registry.get(s.zotero.id).zoteroHost, true);
  assert.deepEqual(names(registry.launchHosts()), ['Clone host', 'Folder host', 'Zotero']);
});

test('a server whose links all went or were detached no longer starts at launch', () => {
  const s = servers();
  registry.setZoteroHost(s.zotero.id, true);
  registry.setZoteroHost(s.zotero.id, false);
  assert.equal('zoteroHost' in registry.get(s.zotero.id), false, 'the entry carries no flag once cleared');
  assert.deepEqual(names(registry.launchHosts()), ['Clone host', 'Folder host']);
});

test('in background mode every local server starts, never a remote one', () => {
  const s = servers();
  registry.setZoteroHost(s.zotero.id, true);
  registry.setSettings({ background: true });
  assert.deepEqual(names(registry.launchHosts()), ['Clone host', 'Folder host', 'Idle', 'Zotero']);
});

test('the record is written only when it changes, and only for a local server', () => {
  const s = servers();
  registry.setZoteroHost(s.zotero.id, true);
  // a key the registry does not keep: gone at its next write, so it marks one
  const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
  fs.writeFileSync(file, JSON.stringify({ ...raw, marker: 1 }));
  registry.setZoteroHost(s.zotero.id, true);
  registry.setZoteroHost(s.idle.id, false);
  assert.equal(JSON.parse(fs.readFileSync(file, 'utf8')).marker, 1, 'the same reading again writes nothing');
  registry.setZoteroHost(s.zotero.id, false);
  assert.equal(JSON.parse(fs.readFileSync(file, 'utf8')).marker, undefined, 'a change is written');
  registry.setZoteroHost(s.remote.id, true);
  registry.setZoteroHost('no-such-server', true);
  assert.equal(registry.get(s.remote.id).zoteroHost, undefined);
  assert.deepEqual(names(registry.launchHosts()), ['Clone host', 'Folder host']);
});

test('a removed server no longer starts at launch', () => {
  const s = servers();
  registry.setZoteroHost(s.zotero.id, true);
  registry.remove(s.zotero.id);
  registry.remove(s.clone.id);
  assert.deepEqual(names(registry.launchHosts()), ['Folder host']);
  assert.deepEqual(registry.load().mirrors, [], 'the removed host\'s clone map goes with it');
});
