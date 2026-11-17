// lib/fileClipboard.js: the file a page's Copy PDF puts on the clipboard,
// in each system's own form.
const test = require('node:test');
const assert = require('node:assert');
const { putFile, WINDOWS_SET } = require('../lib/fileClipboard');

function recorder() {
  const written = [];
  return { written, clipboard: { writeBuffer: (format, buf) => written.push([format, buf.toString('utf8')]) } };
}

test('macOS gets the file URL Finder pastes, a name with spaces and accents encoded', async () => {
  const { written, clipboard } = recorder();
  await putFile('/tmp/gamma-copy-x/Rydberg atoms — été.pdf', { clipboard, platform: 'darwin' });
  assert.deepEqual(written, [['public.file-url', 'file:///tmp/gamma-copy-x/Rydberg%20atoms%20%E2%80%94%20%C3%A9t%C3%A9.pdf']]);
});

test('Linux gets a text/uri-list of the one file', async () => {
  const { written, clipboard } = recorder();
  await putFile('/tmp/gamma-copy-x/a b.pdf', { clipboard, platform: 'linux' });
  assert.deepEqual(written, [['text/uri-list', 'file:///tmp/gamma-copy-x/a%20b.pdf\r\n']]);
});

test('Windows sets the file list through PowerShell, the path in the environment and never in the script', async () => {
  const runs = [];
  const name = "C:\\Temp\\gamma-copy-x\\it's $(Remove-Item C:\\x).pdf";
  const run = (cmd, args, opts, done) => { runs.push({ cmd, args, file: opts.env.GAMMA_CLIPBOARD_FILE, hide: opts.windowsHide }); done(null, '', ''); };
  await putFile(name, { clipboard: null, platform: 'win32', run });
  assert.equal(runs.length, 1);
  assert.equal(runs[0].cmd, 'powershell.exe');
  assert.equal(runs[0].args[runs[0].args.length - 1], WINDOWS_SET);
  assert(!runs[0].args.some((a) => a.includes('Remove-Item')), 'the name is no part of the command');
  assert.equal(runs[0].file, name);
  assert.equal(runs[0].hide, true);
  const failing = (cmd, args, opts, done) => done(new Error('exit 1'), '', 'Set-Clipboard: access denied');
  await assert.rejects(putFile(name, { platform: 'win32', run: failing }), /access denied/);
});
