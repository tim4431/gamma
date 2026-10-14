// PDFs the operating system hands the app (lib/openFiles.js): the files a
// command line names, and the workspace a file goes into. `npm test` in
// desktop/.

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

test('a workspace you can only read hands the file to your own; none to write in is null', () => {
  const read = ws('lab', { role: 'viewer', members: 4 });
  assert.deepEqual(openFiles.placeFor(session(read), 'lab'), { ws: mine, audience: '', mine: null, readOnly: read });
  assert.equal(openFiles.placeFor({ user: 'g', default_workspace: 'lab', workspaces: [read] }, 'lab'), null);
  assert.equal(openFiles.placeFor({ user: null }, ''), null, 'signed out');
});
