// PDFs the operating system hands the app (lib/openFiles.js): the files a
// command line names, and the workspace a file goes into, added to the
// library or read where it is. `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { pathToFileURL } = require('url');
const openFiles = require('../lib/openFiles');

test('a command line names the PDFs that exist, as absolute paths, once each', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-open-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  fs.writeFileSync(path.join(dir, 'paper.pdf'), '%PDF-1.4\n');
  fs.writeFileSync(path.join(dir, 'Upper.PDF'), '%PDF-1.4\n');
  fs.writeFileSync(path.join(dir, 'notes.txt'), 'x');
  fs.mkdirSync(path.join(dir, 'folder.pdf'));
  const got = openFiles.pdfPaths([
    '.', // the app directory in a dev start (`electron .`)
    '--allow-file-access-from-files', // a switch Chromium adds to a second start
    'paper.pdf', // relative to the start's working directory
    path.join(dir, 'paper.pdf'), // the same file again
    pathToFileURL(path.join(dir, 'Upper.PDF')).href, // a Linux file manager's URL
    'notes.txt',
    'folder.pdf',
    'missing.pdf',
  ], dir);
  assert.deepEqual(got, [path.join(dir, 'paper.pdf'), path.join(dir, 'Upper.PDF')]);
  assert.deepEqual(openFiles.pdfPaths(['--hidden'], dir), []);
});

const ws = (id, extra) => ({ id, name: id, role: 'owner', personal: false, members: 1, access: 'private', mirror_of: '', ...extra });
const mine = ws('mine', { personal: true });
const session = (...list) => ({ user: 'tim', default_workspace: 'mine', workspaces: [mine, ...list] });

test('a file goes into the workspace the window shows, else the default one', () => {
  const lab = ws('lab');
  assert.deepEqual(openFiles.placeFor(session(lab), 'lab'), { ws: lab, audience: '', mine: null, readOnly: null });
  assert.equal(openFiles.placeFor(session(lab), '').ws, mine, 'nothing shown yet: the default');
  assert.equal(openFiles.placeFor(session(lab), 'gone').ws, mine, 'a workspace no longer listed: your own');
});

test('a workspace others see offers your own instead; your own alone offers nothing', () => {
  const shared = ws('lab', { role: 'editor', members: 3 });
  assert.deepEqual(openFiles.placeFor(session(shared), 'lab'), { ws: shared, audience: 'shared', mine, readOnly: null });
  const open = ws('open', { access: 'public' });
  assert.equal(openFiles.placeFor(session(open), 'open').audience, 'shared', 'every account sees a public workspace');
  const clone = ws('clone', { mirror_of: 'Lab' });
  assert.equal(openFiles.placeFor(session(clone), 'clone').audience, 'clone');
  assert.deepEqual(openFiles.placeFor(session(), 'mine'), { ws: mine, audience: '', mine: null, readOnly: null });
});

test('a PDF read where it is lands in the workspace the window shows, unless it is a clone or read-only: then the host\'s own takes it', () => {
  const lab = ws('lab', { role: 'editor', members: 3 });
  // a shared one asks first: each member could read the file from this computer through its page
  assert.deepEqual(openFiles.readerPlace(session(lab), 'lab'), { ws: lab, audience: 'shared', mine });
  const open = ws('open', { access: 'public' });
  assert.equal(openFiles.readerPlace(session(open), 'open').audience, 'shared', 'every account sees a public workspace');
  const together = { ...mine, members: 2 };
  assert.deepEqual(openFiles.readerPlace({ user: 'tim', default_workspace: 'mine', workspaces: [together, lab] }, 'lab'),
    { ws: lab, audience: 'shared', mine: null }, 'an own workspace others see too is not offered instead');
  assert.deepEqual(openFiles.readerPlace(session(ws('solo')), 'solo'), { ws: ws('solo'), audience: '', mine: null });
  assert.deepEqual(openFiles.readerPlace(session(lab), ''), { ws: mine, audience: '', mine: null }, 'nothing shown yet: the default');
  assert.equal(openFiles.readerPlace(session(ws('clone', { mirror_of: 'Lab' })), 'clone'), null, 'a clone\'s pages are its origin\'s');
  assert.equal(openFiles.readerPlace(session(ws('read', { role: 'viewer' })), 'read'), null);
  assert.equal(openFiles.readerPlace(session(lab), 'gone'), null, 'a workspace no longer listed');
  assert.equal(openFiles.readerPlace({ user: null }, ''), null, 'signed out');
  assert.equal(openFiles.personalOf(session(lab)), mine);
  assert.equal(openFiles.personalOf({ user: 'g', workspaces: [ws('p', { personal: true, mirror_of: 'X' })] }), null, 'a clone is nobody\'s own');
  assert.equal(openFiles.personalOf({ user: null }), null);
});

test('a workspace you can only read hands the file to your own; none to write in is null', () => {
  const read = ws('lab', { role: 'viewer', members: 4 });
  assert.deepEqual(openFiles.placeFor(session(read), 'lab'), { ws: mine, audience: '', mine: null, readOnly: read });
  assert.equal(openFiles.placeFor({ user: 'g', default_workspace: 'lab', workspaces: [read] }, 'lab'), null);
  assert.equal(openFiles.placeFor({ user: null }, ''), null, 'signed out');
});

test('a file\'s id is the one Gamma stores its bytes under: the first 24 hex characters of its SHA-256', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-open-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'abc.pdf');
  fs.writeFileSync(file, 'abc');
  assert.equal(await openFiles.docIdOf(file), 'ba7816bf8f01cfea414140de', 'storage.content_digest(b"abc")');
  await assert.rejects(openFiles.docIdOf(path.join(dir, 'gone.pdf')), /ENOENT/);
});

test('an opened PDF is carried to a workspace of the other server the account writes in, its own first, never a clone', () => {
  const lab = ws('lab', { role: 'editor', members: 3 });
  const read = ws('read', { role: 'viewer' });
  const clone = ws('clone', { mirror_of: 'Lab' });
  assert.deepEqual(openFiles.carryPlaces({ user: 'tim', workspaces: [lab, read, clone, mine] }), [mine, lab]);
  assert.deepEqual(openFiles.carryPlaces({ user: 'g', workspaces: [read] }), []);
  assert.deepEqual(openFiles.carryPlaces({ user: null }), [], 'signed out');
});
