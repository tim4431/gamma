// The bar's reading of what this computer keeps (lib/keeping.js): a clone's
// states in the order Gamma's sync pill checks them, a Zotero link's, a
// folder's, a followed file's, and the one summary the sync button shows.
// `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const keeping = require('../lib/keeping');

const local = { id: 'l1', name: 'Local', type: 'local' };
const servers = [local, { id: 'r1', name: 'NAS', type: 'remote', url: 'http://nas.lan:9001' }];
const now = new Date().toISOString();
const clone = (info) => keeping.clone(local, { workspace_id: 'w1', name: 'Lab (clone)', remote_name: 'Lab', remote_url: 'http://nas.lan:9001', ...info }, servers);
const folder = (link) => keeping.folder(local, { id: 'f1', folder_id: 'x', dest: 'C:\\Papers\\Quantum', remote_url: '', ...link }, servers);
const file = (link) => keeping.file(local, { id: 'p1', kind: 'file', page_id: 'pg', dest: 'D:\\tex\\build\\main.pdf', remote_url: '', title: 'Draft on X', ...link }, servers);

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
  const kept = folder({ status: { last_sync: now, kept: [{ path: 'a.md', why: 'changed', force: true }], folder_path: ['Papers', 'Quantum'] } });
  assert.equal(kept.state, 'ok');
  assert.equal(kept.name, 'Quantum');
  assert.match(kept.text, /· 1 changed here$/);
  assert.equal(folder({ status: {} }).name, 'Quantum', 'named from its directory before a round');
  assert.equal(folder({ folder_id: 'root', status: {} }).name, 'Whole library');
  assert.equal(folder({ remote_url: 'http://other.example:9001', status: {} }).from, 'other.example:9001');
});

test('a folder that sends PDFs back says so, and how many its last round sent', () => {
  assert.equal(folder({ status: { last_sync: now } }).push, false);
  const back = folder({ push: true, status: { last_sync: now, counts: { sent: 2, written: 0 }, kept: [{ path: 'a.pdf', why: 'changed', force: true }] } });
  assert.equal(back.push, true);
  assert.equal(back.state, 'ok');
  assert.match(back.text, /^Up to date · .+ · sends back · 2 sent · 1 changed here$/);
  assert.match(back.title, /sent back to Gamma; the last round sent 2\./);
  const quiet = folder({ push: true, status: { last_sync: now, counts: { sent: 0 } } });
  assert.match(quiet.text, /· sends back$/);
  assert.doesNotMatch(quiet.text, /sent/);
});

test('a PDF a send could not deliver is no change Sync can replace', () => {
  const held = folder({ push: true, status: { last_sync: now, kept: [
    { path: 'a.pdf', why: 'attachment belongs to another page', force: false },
    { path: 'b.pdf', why: 'changed here and in Gamma', force: true },
  ] } });
  assert.equal(held.changed, 1, 'only what a forced round replaces');
  assert.equal(held.held, 1);
  assert.match(held.text, /· 1 changed here · 1 not synced$/);
  assert.match(held.title, /Sync can replace it\./);
  assert.match(held.title, /Not synced: a\.pdf \(attachment belongs to another page\)\./);
  const none = folder({ push: true, status: { last_sync: now, kept: [{ path: 'a.pdf', why: 'the quota', force: false }] } });
  assert.equal(none.changed, 0);
  assert.doesNotMatch(none.text, /changed here/);
  assert.doesNotMatch(none.title, /Sync can replace/);
});

test('a followed file: sending, paused, a problem, waiting for the file, not sent yet, then sent or up to date', () => {
  assert.equal(file({ status: { running: true, last_error: 'x' } }).text, 'Sending…');
  const running = file({ status: { running: true, paused_at: now } });
  assert.equal(running.state, 'busy');
  assert.equal(running.paused, true, 'still paused once the round ends');
  const paused = file({ status: { paused_at: now, last_error: 'x', waiting: 'still being written' } });
  assert.equal(paused.state, 'paused');
  assert.match(paused.text, /^Paused · /);
  const bad = file({ status: { last_error: 'the page is no longer in Gamma', waiting: 'still being written', last_sync: now } });
  assert.deepEqual([bad.state, bad.text, bad.title], ['error', 'Problem', 'the page is no longer in Gamma']);
  const waiting = file({ status: { waiting: 'still being written', last_sync: now } });
  assert.deepEqual([waiting.state, waiting.text, waiting.title], ['pending', 'Waiting for the file to finish', 'still being written']);
  assert.deepEqual([file({ status: {} }).state, file({ status: {} }).text], ['new', 'Not sent yet']);
  const sent = file({ status: { last_sync: now, last_sent: now } });
  assert.equal(sent.state, 'ok');
  assert.match(sent.text, /^Sent · /);
  const same = file({ status: { last_sync: now } });
  assert.equal(same.state, 'ok');
  assert.match(same.text, /^Up to date · /);
  assert.match(file({ status: { last_sync: now, last_sent: now, relocation_error: 'too many background tasks' } }).title,
    /The highlights were not moved: too many background tasks$/);
});

test('a followed file is named after its page, else the title its rounds saw, else the file; it says where it goes', () => {
  const f = file({ status: {} });
  assert.deepEqual([f.kind, f.key, f.name, f.where, f.dest, f.from, f.paused], ['file', 'file:l1:p1', 'Draft on X', 'D:\\tex\\build\\main.pdf', 'D:\\tex\\build\\main.pdf', 'Local', false]);
  assert.equal(file({ title: '', status: { title: 'Draft on Y' } }).name, 'Draft on Y');
  assert.equal(file({ title: '', status: {} }).name, 'main.pdf');
  assert.equal(file({ remote_url: 'http://nas.lan:9001', status: {} }).from, 'NAS');
  const [a, b] = keeping.itemsOf(local, { links: [{ id: 'f1', folder_id: 'x', dest: 'C:\\Papers', status: {} }, { id: 'p1', kind: 'file', dest: 'D:\\main.pdf', status: {} }] }, servers);
  assert.deepEqual([a.kind, b.kind], ['folder', 'file'], 'the links split by kind');
});

test('a paused folder reads as paused unless a round asked for runs; a detached clone is paused', () => {
  const paused = folder({ status: { last_sync: now, last_error: 'x', paused_at: now } });
  assert.equal(paused.state, 'paused');
  assert.equal(paused.paused, true);
  assert.match(paused.text, /^Paused · /);
  const running = folder({ status: { running: true, paused_at: now } });
  assert.equal(running.state, 'busy');
  assert.equal(running.paused, true, 'still paused once the round ends');
  assert.equal(folder({ status: { last_sync: now } }).paused, false);
  assert.equal(clone({ mode: 'off' }).paused, true);
  assert.equal(clone({ status: { last_sync: now } }).paused, false);
});

test('the button shows the worst state; a detached clone or a paused folder never raises it', () => {
  assert.equal(keeping.summarize([]).state, 'none');
  const ok = folder({ status: { last_sync: now } });
  const busy = folder({ id: 'f2', status: { running: true } });
  const bad = clone({ status: { last_error: 'x', last_sync: now } });
  const off = clone({ workspace_id: 'w2', mode: 'off' });
  assert.deepEqual(keeping.summarize([ok, off]), { state: 'ok', text: 'Synced', title: '1 clone and 1 folder on disk on this computer: synced' });
  assert.equal(keeping.summarize([ok, busy]).text, 'Syncing…');
  assert.equal(keeping.summarize([ok, busy, bad]).text, '1 problem');
  const paused = folder({ id: 'f3', status: { last_error: 'x', paused_at: now } });
  assert.equal(keeping.summarize([ok, paused]).text, 'Synced');
  assert.deepEqual(keeping.summarize([off, paused]), { state: 'paused', text: 'Paused', title: '1 clone and 1 folder on disk on this computer: paused' });
});

const link = (info) => keeping.zotero(local, { kind: 'zotero', workspace_id: 'w3', name: 'Papers', library: 'users/1', library_name: 'My Library', mode: 'two-way', ...info });

test('a Zotero link reads as the pill reads it: its error by kind, what waits on Zotero', () => {
  assert.equal(link({ mode: 'off' }).state, 'detached');
  assert.equal(link({ mode: 'off' }).paused, true);
  assert.equal(link({ status: { running: true, progress: { phase: 'first', done: 3, total: 10 } } }).text, 'First sync 3 / 10');
  assert.equal(link({ status: { last_error: 'x' }, conflicts_open: 1 }).text, '1 conflict');
  // a refused key's sentence says "refused": the clone's regex would call it unreachable
  const auth = link({ status: { last_error: 'Zotero refused the key: reconnect your account in Settings', error_kind: 'auth', last_sync: now } });
  assert.equal(auth.state, 'error');
  assert.equal(auth.text, 'Reconnect Zotero');
  assert.equal(link({ status: { last_error: 'getaddrinfo failed', error_kind: 'offline' } }).text, 'Zotero unreachable · your edits wait here');
  assert.equal(link({ library: 'groups/5', library_name: 'Lab', status: { last_error: 'x', error_kind: 'library_gone' } }).text, 'This key no longer reaches Lab');
  assert.equal(link({ status: { last_error: 'x', error_kind: 'busy' } }).text, 'Zotero is busy · retrying');
  assert.match(link({ status: { last_error: 'odd', error_kind: 'other', last_attempt: now } }).text, /^Sync problem · /);
  assert.equal(link({ status: {} }).text, 'Not synced yet');
  assert.equal(link({ status: { last_sync: now }, pending_local: true }).state, 'pending');
  const missing = link({ status: { last_sync: now, waiting: { files: { linked: 2, unavailable: 310 } } } });
  assert.equal(missing.state, 'waiting');
  assert.equal(missing.text, '312 PDFs not in Zotero Storage');
  assert.equal(link({ status: { last_sync: now, waiting: { files: { quota: 4 }, quota_until: now } } }).text, 'Zotero storage is full · 4 PDFs wait');
  assert.equal(link({ status: { last_sync: now, waiting: { files: { quota: 1 } } } }).text, 'Zotero storage is full · 1 PDF waits', 'its wait lifted by Sync now');
  assert.equal(link({ status: { last_sync: now, waiting: { files: { storage: 2 } } } }).text, 'Storage here is full · 2 PDFs wait');
  assert.equal(link({ status: { last_sync: now, waiting: { files: { no_access: 3 } } } }).text, '3 PDFs wait for file access');
  assert.equal(link({ status: { last_sync: now, waiting: { files: { failed: 1 } } } }).text, '1 PDF transfer failed');
  const ok = link({ status: { last_sync: now } });
  assert.equal(ok.state, 'ok');
  assert.equal(ok.where, 'Linked to Zotero · My Library');
  assert.equal(ok.key, 'zotero:l1:w3');
});

test('a Zotero link: annotations held for a look wait, those in the ordinary course do not', () => {
  const held = link({ status: { last_sync: now, waiting: { annotations: { n: 5, reasons: { 'the PDFs differ': 2, 'the PDF is not here yet': 3 } } } } });
  assert.equal(held.state, 'waiting');
  assert.equal(held.text, '2 annotations wait');
  assert.match(held.title, /the PDFs differ \(2\)\.$/);
  const course = link({ status: { last_sync: now, waiting: { annotations: { n: 3, reasons: { 'the PDF is not here yet': 3 } } } } });
  assert.equal(course.state, 'ok');
});

test('a Zotero link syncs on between two bounded rounds of a fill, and changes queued for later are not up to date', () => {
  assert.equal(link({ status: { running: true, forced: 'pull', progress: { phase: 'sync', done: 3, total: 10 } } }).text, 'Force pull 3 / 10');
  const between = link({ status: { last_sync: now, fill: { units: 50, bytes: 0 }, backlog: 7 }, conflicts_open: 1 });
  assert.equal(between.state, 'busy');
  assert.equal(between.text, 'Syncing · 7 left');
  assert.equal(link({ status: { last_sync: now, fill: { units: 50 }, backlog: 7, last_error: 'x', error_kind: 'offline' } }).state, 'error');
  const queued = link({ status: { last_sync: now, backlog: 2 } });
  assert.equal(queued.state, 'queued');
  assert.match(queued.text, /^2 changes queued · /);
  assert.equal(keeping.summarize([queued]).text, 'Changes queued');
  assert.equal(keeping.summarize([queued, link({ workspace_id: 'w4', status: { last_sync: now }, pending_local: true })]).state, 'pending');
});

test('a Zotero link: a dead key shows over the conflicts; edits that wait and an interrupted fill are queued, not syncing', () => {
  // conflicts left open, then the key revoked: every round fails, and only connecting again mends it
  const dead = link({ status: { last_sync: now, last_error: 'Zotero refused the key', error_kind: 'auth' }, conflicts_open: 12 });
  assert.equal(dead.state, 'error');
  assert.equal(dead.text, 'Reconnect Zotero');
  assert.equal(link({ status: { last_error: 'x', error_kind: 'library_gone' }, conflicts_open: 2 }).state, 'error');
  assert.equal(link({ status: { last_error: 'x', error_kind: 'offline' }, conflicts_open: 2 }).state, 'conflicts', 'Gamma retries that by itself');
  // pending_local counts a page whose notes wait for quiet, or a write backing off: queued with the backlog
  const waits = link({ on_change: true, pending_local: true, status: { last_sync: now, backlog: 1 } });
  assert.equal(waits.state, 'queued');
  assert.equal(link({ on_change: true, pending_local: true, status: { last_sync: now, backlog: 0 } }).state, 'busy', 'a write since the last round');
  // a fill a restart cut short asks for no round any more
  const cut = link({ status: { last_sync: now, fill: { units: 50 }, backlog: 7, interrupted: true } });
  assert.equal(cut.state, 'queued');
});

test('a server lists its Zotero links among what it keeps, and the button counts them', () => {
  const items = keeping.itemsOf(local, { mirrors: [], links: [], zotero: [{ workspace_id: 'w3', library: 'groups/5', library_name: 'Lab', status: { last_sync: now } }] }, servers);
  assert.deepEqual(items.map((i) => [i.kind, i.name]), [['zotero', 'Lab']]);
  assert.equal(keeping.itemsOf(local, { mirrors: [], links: [] }, servers).length, 0, 'a server with no list of links');
  const waiting = link({ status: { last_sync: now, waiting: { files: { linked: 1 } } } });
  assert.deepEqual(keeping.summarize([waiting]), { state: 'waiting', text: 'Waiting', title: '1 Zotero link on this computer: waiting' });
  const ok = folder({ status: { last_sync: now } });
  const off = clone({ workspace_id: 'w2', mode: 'off' });
  assert.equal(keeping.summarize([ok, off, link({ status: { last_sync: now } })]).title, '1 clone, 1 Zotero link and 1 folder on disk on this computer: synced');
  assert.equal(keeping.summarize([link({ status: { last_error: 'x', error_kind: 'auth' } }), ok]).state, 'error');
});

test('the summary names every kind; a paused file never raises it', () => {
  const ok = folder({ status: { last_sync: now } });
  const ok2 = folder({ id: 'f2', status: { last_sync: now } });
  const sent = file({ status: { last_sync: now, last_sent: now } });
  const cl = clone({ status: { last_sync: now } });
  assert.equal(keeping.summarize([cl, ok, ok2, sent]).title, '1 clone, 2 folders on disk and 1 file followed on this computer: synced');
  const zl = link({ status: { last_sync: now } });
  assert.equal(keeping.summarize([cl, zl, ok, ok2, sent]).title, '1 clone, 1 Zotero link, 2 folders on disk and 1 file followed on this computer: synced');
  assert.equal(keeping.summarize([sent, file({ id: 'p2', status: { last_sync: now } })]).title, '2 files followed on this computer: synced');
  const paused = file({ id: 'p3', status: { last_error: 'x', paused_at: now } });
  assert.equal(keeping.summarize([ok, paused]).text, 'Synced');
  assert.equal(keeping.summarize([paused]).state, 'paused');
  assert.equal(keeping.summarize([ok, file({ id: 'p4', status: { waiting: 'still being written', last_sync: now } })]).text, 'Not synced yet');
});
