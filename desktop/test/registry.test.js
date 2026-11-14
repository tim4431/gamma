// The local servers the shell starts at launch (lib/registry.js launchHosts,
// main.js startBackgroundHosts): the clones' hosts, the folder host and the
// servers that hold a Zotero link that is not detached (setZoteroHost, as
// refreshKeeping last read their links), or every local server in
// background mode; an old profile's registry file, renamed once at init;
// and the remotes' addresses and certificates: a typed address's scheme,
// the addresses the links moved from, a remote seen over https, its pin
// and its plain http yes. `npm test` in desktop/.

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

test('a typed address gets https:// for the TLS ports and a public host, http:// for this computer and the local network', () => {
  const cases = {
    'nas.example.com:5001': 'https://nas.example.com:5001',
    'nas.example.com': 'https://nas.example.com',
    'nas.example.com:9001': 'https://nas.example.com:9001',
    'mynas.synology.me:5000': 'https://mynas.synology.me:5000',
    'nas:5000': 'http://nas:5000',
    'nas.lan': 'http://nas.lan',
    'nas.home:9001': 'https://nas.home:9001', // .home is no name of this network's in the shared table
    '192.168.1.5:9001': 'http://192.168.1.5:9001',
    '192.168.1.5:5001': 'https://192.168.1.5:5001',
    '203.0.113.5:9001': 'https://203.0.113.5:9001',
    'localhost:9001': 'http://localhost:9001',
    '[fd00::5]:9001': 'http://[fd00::5]:9001',
    'nas.local:8443/gamma': 'https://nas.local:8443/gamma',
    'http://nas.example.com': 'http://nas.example.com',
  };
  for (const [typed, url] of Object.entries(cases)) assert.equal(registry.withScheme(typed), url, typed);
});

test('an address the links moved from is recorded, followed to the current one, and never round a cycle', () => {
  const s = servers();
  const { srv, before } = registry.setRemoteUrl(s.remote.id, 'https://nas.lan:5001');
  assert.deepEqual([srv.url, before], ['https://nas.lan:5001', 'http://nas.lan:9001']);
  const moved = registry.load().moved['http://nas.lan:9001'];
  assert.deepEqual([moved.to, moved.kind], ['https://nas.lan:5001', 'upgrade']);
  assert.equal(registry.load().mirrors[0].remoteUrl, 'https://nas.lan:5001', 'its clones follow');
  assert.equal(registry.currentOrigin('http://nas.lan:9001'), 'https://nas.lan:5001');
  assert.equal(registry.movedAway('http://nas.lan:9001'), true);
  assert.equal(registry.movedAway('https://nas.lan:5001'), false);
  registry.setRemoteUrl(s.remote.id, 'https://nas.example.com');
  assert.equal(registry.currentOrigin('http://nas.lan:9001'), 'https://nas.example.com', 'a chain followed');
  assert.equal(registry.load().moved['https://nas.lan:5001'].kind, 'other');
  // back where it was: that address is current again, and no step goes round
  registry.setRemoteUrl(s.remote.id, 'http://nas.lan:9001');
  assert.equal(registry.movedAway('http://nas.lan:9001'), false);
  assert.equal(registry.currentOrigin('https://nas.example.com'), 'http://nas.lan:9001');
  assert.equal(registry.load().moved['https://nas.example.com'].kind, 'downgrade');
  const state = { moved: { a: { to: 'b' }, b: { to: 'a' } } };
  assert.equal(registry.currentOrigin('a', state), 'b', 'a cycle stops');
  assert.equal(registry.currentOrigin('unknown'), 'unknown');
});

test('an address the links moved from that is added again is in use: no longer moved away', () => {
  const s = servers();
  registry.setRemoteUrl(s.remote.id, 'https://nas.lan:5001');
  registry.remove(s.remote.id);
  assert.equal(registry.movedAway('http://nas.lan:9001'), true, 'removing the server leaves the record');
  registry.addRemote('Another', 'http://nas.lan:9001');
  assert.equal(registry.movedAway('http://nas.lan:9001'), false);
  assert.equal(registry.currentOrigin('http://nas.lan:9001'), 'http://nas.lan:9001');
});

test('a pin moves with its address on the same host name and goes with a move to another one', () => {
  const s = servers();
  const cert = { sha256: 'a'.repeat(64), subject: 'CN=nas', issuer: 'CN=nas', validFrom: '', validTo: '' };
  assert.equal(registry.setRemoteUrl(s.remote.id, 'https://nas.lan:5001').pins, false, 'no pin, nothing changed');
  registry.setPin('https://nas.lan:5001', cert);
  assert.equal(registry.setRemoteUrl(s.remote.id, 'https://nas.lan').pins, true);
  assert.deepEqual(Object.keys(registry.getPins()), ['https://nas.lan'], 'carried to the new port');
  assert.equal(registry.getPins()['https://nas.lan'].sha256, cert.sha256);
  assert.equal(registry.setRemoteUrl(s.remote.id, 'https://192.168.1.11').pins, true);
  assert.deepEqual(registry.getPins(), {}, 'another host name: dropped');
  // a pin the move made for the new address already is kept, the old one dropped
  registry.setPin('https://192.168.1.11', cert);
  registry.setPin('https://192.168.1.11:5001', { ...cert, sha256: 'b'.repeat(64) });
  registry.setRemoteUrl(s.remote.id, 'https://192.168.1.11:5001');
  assert.deepEqual(Object.entries(registry.getPins()).map(([o, p]) => [o, p.sha256]), [['https://192.168.1.11:5001', 'b'.repeat(64)]]);
});

test('a remote seen over https is recorded until its host name changes; its pin goes with it, its plain http yes is per origin', () => {
  const s = servers();
  registry.markHttpsSeen(s.remote.id);
  assert.equal(registry.get(s.remote.id).httpsSeen, undefined, 'an http:// address is never seen over https');
  registry.setRemoteUrl(s.remote.id, 'https://nas.lan:5001');
  registry.markHttpsSeen(s.remote.id);
  assert.equal(registry.get(s.remote.id).httpsSeen, true);
  registry.setRemoteUrl(s.remote.id, 'https://nas.lan');
  assert.equal(registry.get(s.remote.id).httpsSeen, true, 'the same host name');
  registry.setRemoteUrl(s.remote.id, 'https://nas.example.com');
  assert.equal(registry.get(s.remote.id).httpsSeen, undefined, 'another host name');

  const cert = { sha256: 'a'.repeat(64), subject: 'CN=nas', issuer: 'CN=nas', validFrom: '2026-01-01T00:00:00.000Z', validTo: '2027-01-01T00:00:00.000Z' };
  const kept = registry.setPin('https://nas.example.com', cert);
  assert.deepEqual({ ...kept, pinnedAt: '' }, { ...cert, pinnedAt: '' });
  assert.ok(kept.pinnedAt);
  assert.deepEqual(Object.keys(registry.getPins()), ['https://nas.example.com']);
  assert.equal(registry.dropPin('https://nas.example.com'), true);
  assert.equal(registry.dropPin('https://nas.example.com'), false);
  registry.setPin('https://nas.example.com', cert);
  registry.remove(s.remote.id);
  assert.deepEqual(registry.getPins(), {}, 'removed with its server');

  registry.setPlainAccepted('http://nas.lan:9001', true);
  assert.equal(registry.plainAccepted('http://nas.lan:9001'), true);
  assert.equal(registry.plainAccepted('http://nas.lan:9002'), false);
  registry.setPlainAccepted('http://nas.lan:9001', false);
  assert.equal(registry.plainAccepted('http://nas.lan:9001'), false);
});

// A profile from before servers had that name keeps its list: init() copies
// workspaces.json to servers.json once, the list under `servers`.
function profile(files) {
  const own = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-registry-'));
  for (const [name, body] of Object.entries(files)) fs.writeFileSync(path.join(own, name), JSON.stringify(body));
  registry.init(own);
  return own;
}
const legacy = {
  settings: { lastTheme: 'light' },
  workspaces: [{ id: 'a1', name: 'Lab', type: 'local', dataDir: '/data/workspaces/a1', adminUser: 'admin', adminPassword: 'pw' }],
  lastOpened: 'a1',
  windowBounds: { x: 1, y: 2, width: 900, height: 700 },
};

test('an old profile\'s workspaces.json becomes servers.json, its list under `servers`, and stays in place', () => {
  const own = profile({ 'workspaces.json': legacy });
  try {
    const written = JSON.parse(fs.readFileSync(path.join(own, 'servers.json'), 'utf8'));
    assert.deepEqual(written.servers, legacy.workspaces);
    assert.equal('workspaces' in written, false, 'the old key is not carried over');
    assert.deepEqual(written.windowBounds, legacy.windowBounds);
    assert.equal(registry.getLastOpened().name, 'Lab');
    assert.equal(registry.getSettings().lastTheme, 'light');
    assert(fs.existsSync(path.join(own, 'workspaces.json')), 'the legacy file is kept');
  } finally {
    registry.init(dir);
    fs.rmSync(own, { recursive: true, force: true });
  }
});

test('a servers.json is never replaced by a legacy file, and one without `servers` lists none', () => {
  const own = profile({
    'servers.json': { servers: [{ id: 'b2', name: 'Kept', type: 'remote', url: 'http://nas.lan:9001' }] },
    'workspaces.json': legacy,
  });
  try {
    assert.deepEqual(names(registry.load().servers), ['Kept']);
    fs.writeFileSync(path.join(own, 'servers.json'), JSON.stringify({ workspaces: legacy.workspaces }));
    assert.deepEqual(registry.load().servers, []);
  } finally {
    registry.init(dir);
    fs.rmSync(own, { recursive: true, force: true });
  }
});
