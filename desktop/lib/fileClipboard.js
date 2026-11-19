// A file on the system clipboard as a file manager's Copy leaves it, so a
// paste in Finder, Explorer, a file manager or a chat app takes the file
// itself: a Gamma page's Copy PDF (lib/thisComputer.js copyFileFromPage). Electron's
// clipboard writes one raw format a call, and not Windows' own file list:
//
// - macOS: the file's URL as `public.file-url`, which Finder's paste and
//   the apps that take a dropped file read;
// - Windows: CF_HDROP, the list Explorer's Copy makes, through Windows
//   PowerShell's `Set-Clipboard -LiteralPath` (the 5.1 every Windows has;
//   PowerShell 7 dropped the parameter). The path goes in the environment,
//   never on the command line, so no name is read as script;
// - Linux: `text/uri-list`, the file list of the freedesktop file managers
//   and toolkits.
//
// `deps` stand in for Electron's clipboard and child_process in the tests.

const { execFile } = require('child_process');
const { pathToFileURL } = require('url');

const WINDOWS_SET = 'Set-Clipboard -LiteralPath $env:GAMMA_CLIPBOARD_FILE';

function putFile(file, { clipboard, platform = process.platform, run = execFile } = {}) {
  const url = pathToFileURL(file).href;
  if (platform === 'darwin') {
    clipboard.writeBuffer('public.file-url', Buffer.from(url, 'utf8'));
    return Promise.resolve();
  }
  if (platform === 'win32') {
    return new Promise((resolve, reject) => {
      run('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', WINDOWS_SET],
        { env: { ...process.env, GAMMA_CLIPBOARD_FILE: file }, windowsHide: true, timeout: 20_000 },
        (err, _stdout, stderr) => (err ? reject(new Error(String(stderr || err.message).trim() || 'PowerShell could not set the clipboard.')) : resolve()));
    });
  }
  clipboard.writeBuffer('text/uri-list', Buffer.from(`${url}\r\n`, 'utf8'));
  return Promise.resolve();
}

module.exports = { putFile, WINDOWS_SET };
