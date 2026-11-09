// PDFs the operating system hands the app — "Open with → Gamma", Gamma as
// the default PDF app, a file dropped on its icon (docs/architecture.md
// "Opening PDF files"): which files a command line names, which of a
// server's workspaces a file goes into, added to the library or read where
// it is, and the id Gamma stores its bytes under. No Electron here; unit
// tested by test/openFiles.test.js (`npm test`).

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { fileURLToPath } = require('url');

// The PDFs among command-line arguments (the executable left out), as
// absolute paths of files that exist: switches are skipped (Chromium adds
// its own to a second start's), and a file:// URL — what a Linux file
// manager may pass — is read as its path.
function pdfPaths(args, cwd) {
  const out = [];
  for (const arg of args || []) {
    if (typeof arg !== 'string' || !arg || arg.startsWith('-')) continue;
    let p = arg;
    if (/^file:/i.test(p)) {
      try {
        p = fileURLToPath(p);
      } catch {
        continue;
      }
    }
    if (!/\.pdf$/i.test(p)) continue;
    p = path.resolve(cwd || process.cwd(), p);
    try {
      if (!fs.statSync(p).isFile()) continue;
    } catch {
      continue;
    }
    if (!out.includes(p)) out.push(p);
  }
  return out;
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

// Where a PDF read where it is lands on a local server (docs/research/vaults.md
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

module.exports = { pdfPaths, placeFor, personalOf, readerPlace, docIdOf, carryPlaces };
