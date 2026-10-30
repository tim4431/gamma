// The bar's reading of what this computer keeps (lib/keeping.js): a clone's
// states in the order Gamma's sync pill checks them, a folder's, a followed
// file's, and the one summary the sync button shows. `npm test` in desktop/.

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

test('the summary names all three kinds; a paused file never raises it', () => {
  const ok = folder({ status: { last_sync: now } });
  const ok2 = folder({ id: 'f2', status: { last_sync: now } });
  const sent = file({ status: { last_sync: now, last_sent: now } });
  const cl = clone({ status: { last_sync: now } });
  assert.equal(keeping.summarize([cl, ok, ok2, sent]).title, '1 clone, 2 folders on disk and 1 file followed on this computer: synced');
  assert.equal(keeping.summarize([sent, file({ id: 'p2', status: { last_sync: now } })]).title, '2 files followed on this computer: synced');
  const paused = file({ id: 'p3', status: { last_error: 'x', paused_at: now } });
  assert.equal(keeping.summarize([ok, paused]).text, 'Synced');
  assert.equal(keeping.summarize([paused]).state, 'paused');
  assert.equal(keeping.summarize([ok, file({ id: 'p4', status: { waiting: 'still being written', last_sync: now } })]).text, 'Not synced yet');
});
