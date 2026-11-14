// Files and folders the operating system hands the app — "Open with →
// Gamma", Gamma as the default PDF app, a folder's "Open with Gamma", a file
// or folder dropped on its icon (docs/architecture.md "Opening files and
// folders"): which paths a command line names, which of them is a broad
// place a work folder should not take without a question, the project a
// Markdown file belongs to, which of a server's workspaces a file goes
// into, added to the library or read where it is, and the id Gamma stores
// its bytes under. No Electron here; unit tested by test/openFiles.test.js
// (`npm test`).

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { fileURLToPath } = require('url');

// The arguments of a start that are the user's: the executable left out,
// and under `defaultApp` (`electron .`: a dev start, the e2e's dev
// launches) also the first argument that is not a switch, the app's own
// directory, as Electron's default app picks it. Playwright puts its own
// switches before it (`--inspect=0 --remote-debugging-port=0`), so it is not
// always the first argument.
function launchArgs(argv, defaultApp) {
  const args = (argv || []).slice(1);
  if (!defaultApp) return args;
  const at = args.findIndex((a) => typeof a === 'string' && a && !a.startsWith('-'));
  return at < 0 ? args : [...args.slice(0, at), ...args.slice(at + 1)];
}

// One argument as a path, before it is resolved: a file:// URL (what a
// Linux file manager may pass) read as its path, and on Windows an argument
// ending in `"` is a path that ended in a backslash (`"%V"` of `D:\` arrives
// as `D:"`, CommandLineToArgvW's `\"`). null for a URL that names no file.
function argPath(arg, platform = process.platform) {
  if (/^file:/i.test(arg)) {
    try {
      return fileURLToPath(arg);
    } catch {
      return null;
    }
  }
  return platform === 'win32' && arg.endsWith('"') ? `${arg.slice(0, -1)}\\` : arg;
}

// What a command line names, `[{ path, dir }]`: absolute paths of the files
// (`dir` false) and directories (`dir` true) that exist, once each, in their
// order. Switches are skipped (Chromium adds its own to a second start's).
// No extension is filtered: the server says what it can show, and any file
// can arrive by *Choose another app* or a drop.
function openedPaths(args, cwd, platform = process.platform) {
  const out = [];
  for (const arg of args || []) {
    if (typeof arg !== 'string' || !arg || arg.startsWith('-')) continue;
    const named = argPath(arg, platform);
    if (!named) continue;
    const p = path.resolve(cwd || process.cwd(), named);
    let stat;
    try {
      stat = fs.statSync(p);
    } catch {
      continue;
    }
    if (!stat.isFile() && !stat.isDirectory()) continue;
    if (!out.some((o) => o.path === p)) out.push({ path: p, dir: stat.isDirectory() });
  }
  return out;
}

// A path as places are told apart: trailing separators left out, and
// without case on Windows and macOS.
function placeKey(p, platform = process.platform) {
  let s = String(p || '');
  if (platform === 'win32') s = s.replace(/\//g, '\\');
  s = s.replace(platform === 'win32' ? /\\+$/ : /\/+$/, '') || s.slice(0, 1);
  return platform === 'win32' || platform === 'darwin' ? s.toLowerCase() : s;
}

// The label of the broad place `dir` is (`places`: `[{ label, path }]`, the
// Desktop, Documents, a cloud drive's root… as main.js knownPlaces lists
// them), or ''. The place itself only: a directory inside one is a project.
function broadPlace(dir, places, platform = process.platform) {
  const key = placeKey(dir, platform);
  const hit = (places || []).find((p) => p && p.path && placeKey(p.path, platform) === key);
  return hit ? hit.label : '';
}

// The project a file belongs to, the directory a work folder of it is
// made of: the first directory up from the file's own that holds `.git` (a
// directory, or the file a git worktree has), up to the home directory, a
// drive or filesystem root, or a broad place, none of which is taken; else
// the file's own directory. A repository's root, since work folders are
// made for repositories and one at `repo/docs/` would later refuse `repo/`.
function projectDir(file, { home = os.homedir(), places = [], platform = process.platform } = {}) {
  const own = path.dirname(path.resolve(file));
  const homeKey = home ? placeKey(home, platform) : '';
  for (let d = own; ;) {
    const up = path.dirname(d);
    if (up === d || placeKey(d, platform) === homeKey || broadPlace(d, places, platform)) return own;
    if (fs.existsSync(path.join(d, '.git'))) return d;
    d = up;
  }
}

const WRITERS = new Set(['owner', 'editor']);
const writable = (w) => Boolean(w) && WRITERS.has(w.role);

// Who else sees what lands in workspace `w` (an `/api/session` entry):
// 'clone' (it reaches the workspace the clone follows), 'shared' (other
// members, or every account of a public workspace), '' (only you).
function audience(w) {
  if (w.mirror_of) return 'clone';
  return w.access === 'public' || w.members > 1 ? 'shared' : '';
}

// Where a file goes on the open server, from its `/api/session` answer: the
// workspace the window shows (`shown`, else the account's default) when the
// account can write there, else its personal one. `{ ws, audience, mine,
// readOnly }`: `mine` is the personal workspace to offer instead when others
// see `ws`, `readOnly` the shown workspace passed over for being read-only.
// null when there is no workspace to write in.
function placeFor(info, shown) {
  const list = (info && info.workspaces) || [];
  const mine = list.find((w) => w.personal && !w.mirror_of && writable(w)) || null;
  const here = list.find((w) => w.id === (shown || (info && info.default_workspace))) || null;
  const ws = writable(here) ? here : mine;
  if (!ws) return null;
  const who = audience(ws);
  return {
    ws,
    audience: who,
    mine: who && mine && mine.id !== ws.id ? mine : null,
    readOnly: here && ws !== here ? here : null,
  };
}

// The account's own workspace in an `/api/session` answer, the one it may
// always write in; null when it has none.
function personalOf(info) {
  return ((info && info.workspaces) || []).find((w) => w.personal && !w.mirror_of && writable(w)) || null;
}

// Where a file read where it is lands on a local server (docs/research/vaults.md
// "Reading a PDF where it is"), and where a synced vault made from a directory
// does: the workspace the window shows (`shown`, else the account's default)
// when the account writes there and it is no clone, whose pages are its
// origin's; null otherwise, and then the host's personal workspace takes it.
// `{ ws, audience, mine }` as placeFor's: a page of a shared workspace reads
// the file for every member, so `audience` asks first, `mine` the personal
// workspace of this server to offer instead (one only the account sees).
function readerPlace(info, shown) {
  const list = (info && info.workspaces) || [];
  const here = list.find((w) => w.id === (shown || (info && info.default_workspace))) || null;
  if (!writable(here) || here.mirror_of) return null;
  const who = audience(here);
  const mine = personalOf(info);
  return { ws: here, audience: who, mine: who && mine && mine.id !== here.id && !audience(mine) ? mine : null };
}

// The id Gamma stores a file's bytes under (`storage.digest_id`): the first
// 24 hex characters of its SHA-256, read as a stream, so a server can be
// asked whether it holds the file with nothing of the file sent.
function docIdOf(file) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256');
    fs.createReadStream(file)
      .on('error', reject)
      .on('data', (chunk) => hash.update(chunk))
      .on('end', () => resolve(hash.digest('hex').slice(0, 24)));
  });
}

// The workspaces of another server an opened PDF may be carried to (main.js
// carryPage), from that server's `/api/session` answer: those the account
// writes in, the personal one first, and no clone, whose pages are its
// origin's.
function carryPlaces(info) {
  const list = ((info && info.workspaces) || []).filter((w) => writable(w) && !w.mirror_of);
  return [...list.filter((w) => w.personal), ...list.filter((w) => !w.personal)];
}

module.exports = { launchArgs, argPath, openedPaths, broadPlace, projectDir, placeFor, personalOf, readerPlace, docIdOf, carryPlaces };
