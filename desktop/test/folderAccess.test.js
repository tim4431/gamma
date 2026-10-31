// The Mac App Store build's folders outside its sandbox (lib/folderAccess.js):
// which bookmarks are kept, which folders a server reaches, and when a
// server has to start again to reach one. `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { create } = require('../lib/folderAccess');

const roots = [];
test.after(() => roots.forEach((r) => fs.rmSync(r, { recursive: true, force: true })));

// A temp tree: the app's container plus folders outside it. `start` stands
// in for app.startAccessingSecurityScopedResource: a bookmark is the folder
// path it opens, "bad" throws as a stale one does.
function setup(stored = []) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-access-'));
  roots.push(root);
  const dir = (...parts) => {
    const p = path.join(root, ...parts);
    fs.mkdirSync(p, { recursive: true });
    return p;
  };
  let list = stored.map((g) => ({ ...g }));
  const opened = [];
  const stopped = [];
  const access = create({
    load: () => list,
    save: (next) => { list = next; },
    start: (bookmark) => {
      if (bookmark === 'bad') throw new Error('bookmarkDataIsStale');
      opened.push(bookmark);
      return () => stopped.push(bookmark);
    },
    container: dir('container'),
  });
  return { root, dir, access, opened, stopped, stored: () => list };
}

test('a pick is kept as a bookmark and reaches what is inside it', () => {
  const t = setup();
  const books = t.dir('Books');
  t.access.add(books, 'bm-books');
  assert.deepEqual(t.stored(), [{ path: books, bookmark: 'bm-books' }]);
  assert.equal(t.access.reachable(path.join(books, 'srv1')), true);
  assert.equal(t.access.reachable(t.dir('Elsewhere')), false);
  assert.equal(t.access.reachable(`${books}-other`), false, 'a sibling with the same prefix is not inside');
});

test('the container needs no grant, and a folder inside a kept one no bookmark', () => {
  const t = setup();
  t.access.add(path.join(t.root, 'container', 'workspaces'), 'bm-container');
  assert.deepEqual(t.stored(), []);
  assert.equal(t.access.reachable(path.join(t.root, 'container', 'workspaces', 'srv1')), true);
  const books = t.dir('Books');
  t.access.add(books, 'bm-books');
  t.access.add(t.dir('Books', 'Physics'), 'bm-physics');
  assert.deepEqual(t.stored().map((g) => g.bookmark), ['bm-books']);
});

test('a pick around kept folders replaces their bookmarks', () => {
  const t = setup();
  const a = t.dir('Docs', 'a');
  const b = t.dir('Docs', 'b');
  t.access.add(a, 'bm-a');
  t.access.add(b, 'bm-b');
  t.access.add(path.join(t.root, 'Docs'), 'bm-docs');
  assert.deepEqual(t.stored().map((g) => g.bookmark), ['bm-docs']);
  assert.deepEqual(t.stopped.sort(), ['bm-a', 'bm-b']);
  assert.equal(t.access.reachable(a), true);
});

test('at launch every stored folder opens; one that does not stays stored', () => {
  const t = setup();
  const books = t.dir('Books');
  const gone = path.join(t.root, 'Moved');
  const stale = t.dir('Stale');
  const again = setup([
    { path: books, bookmark: 'bm-books' },
    { path: gone, bookmark: 'bm-gone' },
    { path: stale, bookmark: 'bad' },
  ]);
  again.access.restore();
  assert.equal(again.access.reachable(books), true);
  assert.equal(again.access.reachable(gone), false, 'the folder is not where it was');
  assert.equal(again.access.reachable(stale), false);
  assert.deepEqual(again.stopped, ['bm-gone'], 'a folder that is not there is closed again');
  assert.equal(again.stored().length, 3, 'a disk that comes back works at the next launch');
});

test('a followed file kept as a bookmark opens at launch as a folder does', () => {
  const t = setup();
  const pdf = path.join(t.dir('Build'), 'main.pdf');
  fs.writeFileSync(pdf, '%PDF-1.4\n%%EOF\n');
  t.access.add(pdf, 'bm-pdf');
  assert.deepEqual(t.stored(), [{ path: pdf, bookmark: 'bm-pdf' }]);
  const again = setup(t.stored());
  again.access.restore();
  assert.equal(again.access.reachable(pdf), true);
  assert.deepEqual(again.stopped, []);
});

test('a server started before a grant has to start again to reach it', () => {
  const t = setup();
  const books = t.dir('Books');
  t.access.add(books, 'bm-books');
  const before = t.access.generation(); // a server started now has Books
  const notes = t.dir('Notes');
  t.access.add(notes, 'bm-notes');
  assert.equal(t.access.grantedSince(path.join(books, 'Physics'), before), false);
  assert.equal(t.access.grantedSince(path.join(notes, 'Physics'), before), true);
  assert.equal(t.access.grantedSince(path.join(notes, 'Physics'), t.access.generation()), false);
  assert.equal(t.access.grantedSince(path.join(t.root, 'container', 'x'), 0), false);
  assert.equal(t.access.grantedSince(t.dir('Elsewhere'), 0), false, 'nothing a restart would give it');
});

test('restored folders reach every server; a pick inside one restarts none', () => {
  const t = setup();
  const books = t.dir('Books');
  const again = setup([{ path: books, bookmark: 'bm-books' }]);
  again.access.restore();
  again.access.add(again.dir('Books', 'Physics'), 'bm-physics');
  assert.equal(again.access.grantedSince(path.join(books, 'Physics'), 0), false);
});

test('a pick without a usable bookmark reaches this run only', () => {
  const t = setup();
  const books = t.dir('Books');
  t.access.add(books, 'bad');
  assert.equal(t.access.reachable(books), true);
  assert.deepEqual(t.stored(), []);
  const plain = t.dir('Plain');
  t.access.add(plain, undefined);
  assert.equal(t.access.reachable(plain), true);
  assert.deepEqual(t.stored(), []);
  t.access.add(books, 'bm-books');
  assert.deepEqual(t.stored(), [{ path: books, bookmark: 'bm-books' }], 'chosen again, it is kept');
});
