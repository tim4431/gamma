// Files and folders the operating system hands the app (lib/openFiles.js):
// the paths a command line names, the broad places, the project a Markdown
// file belongs to, and the workspace a file goes into, added to the library
// or read where it is. `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { pathToFileURL } = require('url');
const openFiles = require('../lib/openFiles');

test('a command line names the files and directories that exist, as absolute paths, once each, whatever their extension', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-open-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  fs.writeFileSync(path.join(dir, 'paper.pdf'), '%PDF-1.4\n');
  fs.writeFileSync(path.join(dir, 'notes.md'), '# Notes\n');
  fs.mkdirSync(path.join(dir, 'Project'));
  const got = openFiles.openedPaths([
    '--allow-file-access-from-files', // a switch Chromium adds to a second start
    'paper.pdf', // relative to the start's working directory
    path.join(dir, 'paper.pdf'), // the same file again
    pathToFileURL(path.join(dir, 'Project')).href, // a Linux file manager's URL, of a directory
    'notes.md',
    'Project', // the same directory again
    'missing.pdf',
    'file://remote-host/share/x.pdf', // a URL that names no file here
    '',
    42,
  ], dir);
  assert.deepEqual(got, [
    { path: path.join(dir, 'paper.pdf'), dir: false },
    { path: path.join(dir, 'Project'), dir: true },
    { path: path.join(dir, 'notes.md'), dir: false },
  ]);
  assert.deepEqual(openFiles.openedPaths(['--hidden'], dir), []);
});

test('on Windows an argument ending in a quote is a path that ended in a backslash (a drive root\'s "%V"); anywhere else it is left as it is', () => {
  assert.equal(openFiles.argPath('D:"', 'win32'), 'D:\\');
  assert.equal(openFiles.argPath('C:\\Users\\tim\\paper', 'win32'), 'C:\\Users\\tim\\paper');
  assert.equal(openFiles.argPath('D:"', 'linux'), 'D:"');
  assert.equal(openFiles.argPath('D:"', 'darwin'), 'D:"');
});

test('the app\'s own directory is not handed over: a packaged start\'s arguments are its own, a dev start drops its first argument that is no switch, wherever Playwright puts its switches', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-open-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const app = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-app-'));
  t.after(() => fs.rmSync(app, { recursive: true, force: true }));
  assert.deepEqual(openFiles.launchArgs(['Gamma.exe', dir], false), [dir], 'packaged');
  assert.deepEqual(openFiles.launchArgs(['Gamma.exe'], false), []);
  assert.deepEqual(openFiles.launchArgs(['electron', '.', dir], true), [dir], 'electron . <dir>');
  const played = ['electron', '--inspect=0', '--remote-debugging-port=0', app, dir];
  assert.deepEqual(openFiles.launchArgs(played, true), ['--inspect=0', '--remote-debugging-port=0', dir]);
  assert.deepEqual(openFiles.openedPaths(openFiles.launchArgs(played, true), os.tmpdir()), [{ path: dir, dir: true }],
    'the app directory, a directory too, is not opened');
  assert.deepEqual(openFiles.launchArgs(['electron', '--hidden'], true), ['--hidden'], 'only switches: nothing dropped');
});

test('a broad place is the place itself, by its label, without case on Windows and macOS and with no trailing separator', () => {
  const places = [{ label: 'Desktop', path: '/home/tim/Desktop' }, { label: 'OneDrive', path: 'C:\\Users\\tim\\OneDrive' }];
  assert.equal(openFiles.broadPlace('/home/tim/Desktop', places, 'linux'), 'Desktop');
  assert.equal(openFiles.broadPlace('/home/tim/Desktop/', places, 'linux'), 'Desktop', 'a trailing separator');
  assert.equal(openFiles.broadPlace('/home/tim/Desktop/paper', places, 'linux'), '', 'inside it: a project');
  assert.equal(openFiles.broadPlace('/home/tim/Documents', places, 'linux'), '', 'another directory');
  assert.equal(openFiles.broadPlace('/home/tim/desktop', places, 'linux'), '', 'Linux tells case apart');
  assert.equal(openFiles.broadPlace('/home/tim/desktop', places, 'darwin'), 'Desktop', 'macOS does not');
  assert.equal(openFiles.broadPlace('c:/users/TIM/onedrive\\', places, 'win32'), 'OneDrive', 'nor does Windows, either separator');
  assert.equal(openFiles.broadPlace('/home/tim/Desktop', [], 'linux'), '');
});

test('a Markdown file\'s project is the repository it is in, else its own directory, never home, a broad place or what is around them', (t) => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-project-')));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const put = (p) => {
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, '# x\n');
    return p;
  };
  const home = path.join(root, 'home');
  const opts = { home, places: [{ label: 'Downloads', path: path.join(home, 'Downloads') }] };
  // a file in a repository's subdirectory: its root
  fs.mkdirSync(path.join(home, 'paper', '.git'), { recursive: true });
  assert.equal(openFiles.projectDir(put(path.join(home, 'paper', 'docs', 'deep', 'notes.md')), opts), path.join(home, 'paper'));
  assert.equal(openFiles.projectDir(put(path.join(home, 'paper', 'README.md')), opts), path.join(home, 'paper'), 'at its root');
  // a worktree: `.git` is a file
  put(path.join(home, 'wt', '.git'));
  assert.equal(openFiles.projectDir(put(path.join(home, 'wt', 'docs', 'a.md')), opts), path.join(home, 'wt'));
  // no repository: the file's own directory
  assert.equal(openFiles.projectDir(put(path.join(home, 'loose', 'sub', 'b.md')), opts), path.join(home, 'loose', 'sub'));
  // home under git (a dotfiles repository): not taken
  fs.mkdirSync(path.join(home, '.git'));
  assert.equal(openFiles.projectDir(put(path.join(home, 'notes', 'c.md')), opts), path.join(home, 'notes'));
  assert.equal(openFiles.projectDir(put(path.join(home, 'd.md')), opts), home, 'a file at home: home, which the server refuses');
  // a broad place: its own directory, even with a repository around it
  fs.mkdirSync(path.join(root, '.git'));
  assert.equal(openFiles.projectDir(put(path.join(home, 'Downloads', 'e.md')), opts), path.join(home, 'Downloads'));
  // outside home: up to the filesystem's root, which is not taken
  const other = path.join(root, 'elsewhere', 'f.md');
  assert.equal(openFiles.projectDir(put(other), { home: path.join(os.tmpdir(), 'nobody-home'), places: [] }), root,
    'a repository around it, outside home');
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
