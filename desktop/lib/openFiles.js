// PDFs the operating system hands the app — "Open with → Gamma", Gamma as
// the default PDF app, a file dropped on its icon (docs/architecture.md
// "Opening PDF files"): which files a command line names, and which of the
// open server's workspaces a file goes into. No Electron here; unit tested
// by test/openFiles.test.js (`npm test`).

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

module.exports = { pdfPaths, audience, placeFor };
