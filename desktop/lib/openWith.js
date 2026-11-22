// The launcher's two switches over Gamma's entries in File Explorer
// (docs/architecture.md "Opening files and folders"): `files`, Gamma in
// Open with and Default apps for the paper's file types, and `folders`,
// "Open with Gamma" on a folder and on an open folder's background. The
// entries are lib/openwith.json, written by scripts/openwith.cjs from the
// installer's own (parts()), so a switch writes and removes exactly what
// the installer does.
//
// The NSIS install: what it has is its own record, `Software\<appId>`
// OpenWithFiles and OpenWithFolders (REG_DWORD), in HKCU for a per-user
// install, HKLM for one for all users. The installer's page sets it, an
// update reads it back, and a switch writes the part's entries or removes
// them through reg.exe, then the record, so the next update keeps what the
// person chose. A record without the values is an install from before the
// page, which wrote both. An install for all users is shown, never
// changed: its keys need an administrator, who runs the installer again.
// Explorer reads the verbs and Open with when it shows them; Default apps
// when it opens.
//
// The Store package: its file types are its manifest's, fixed, so only the
// folder verb switches. The verb is the class of native/open-with/, which
// hides itself while the file `storeOff` is in the app's data directory.
//
// Elsewhere (macOS, Linux, a dev run, the test scripts, an unpacked build
// with no install record) there is nothing to switch: read() gives null.

const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');
const TABLE = require('./openwith.json');

const PARTS = Object.keys(TABLE.parts);

const STORE_NOTE = 'While this file is here, Open with Gamma is left out of folders\' menus. Gamma\'s launcher, Settings, puts it back.\n';

// reg.exe with its exit code. Its messages are in the system's language;
// only the codes, the value names and REG_ types are read.
function reg(args) {
  return new Promise((resolve) => {
    execFile('reg', args, { windowsHide: true, encoding: 'latin1' }, (err, stdout) => {
      resolve({ code: err ? (typeof err.code === 'number' ? err.code : 1) : 0, out: String(stdout || '') });
    });
  });
}

// A DWORD of `reg query`'s listing, as on or off; absent, on.
function flag(listing, name) {
  const m = new RegExp(`^\\s+${name}\\s+REG_DWORD\\s+0x([0-9a-f]+)\\s*$`, 'im').exec(listing);
  return m ? parseInt(m[1], 16) !== 0 : true;
}

/**
 * `installed`: a packaged app outside the test scripts; `store`:
 * process.windowsStore; `exe`: the installed Gamma.exe, the table's $appExe
 * (its other names are the installer's, put in by the generator); `dataDir`:
 * the app's data directory (the Store's switch). `root` puts every key under
 * that one and `run` stands in for reg.exe (the tests).
 */
function create({
  platform = process.platform, installed = false, store = false,
  exe = process.execPath, dataDir = '', root = '', run = reg,
} = {}) {
  const fill = (s) => String(s).replace(/\$appExe/g, exe);
  const key = (hive, k) => `${hive}\\${root ? `${root}\\` : ''}${fill(k)}`;
  const storeOff = () => path.join(dataDir, TABLE.storeOff);

  // The install's record: where it is, and what it says.
  async function record() {
    for (const hive of ['HKCU', 'HKLM']) {
      const { code, out } = await run(['query', key(hive, TABLE.record)]);
      if (code === 0) return { hive, files: flag(out, TABLE.parts.files.value), folders: flag(out, TABLE.parts.folders.value) };
    }
    return null;
  }

  /**
   * → null, or { types, verb, store, allUsers, files, folders }, a part
   * { on, editable }, or null where it cannot be switched (the Store's files).
   */
  async function read() {
    if (platform !== 'win32' || !installed) return null;
    const base = { types: TABLE.types, verb: TABLE.verb, store };
    if (store) return { ...base, allUsers: false, files: null, folders: { on: !fs.existsSync(storeOff()), editable: true } };
    const r = await record();
    if (!r) return null;
    const editable = r.hive === 'HKCU';
    return { ...base, allUsers: !editable, files: { on: r.files, editable }, folders: { on: r.folders, editable } };
  }

  async function must(args) {
    const { code } = await run(args);
    if (code !== 0) throw new Error(`Gamma could not change File Explorer's entries (reg ${args[0]} exited with ${code}).`);
  }

  const write = (hive, w) => must(['add', key(hive, w.key), ...(w.name ? ['/v', fill(w.name)] : ['/ve']),
    ...(w.type === 'none' ? ['/t', 'REG_NONE'] : ['/t', 'REG_SZ', '/d', fill(w.data)]), '/f']);

  // What is already gone is no failure. A key "once empty" waits for the
  // values and keys before it (an extension's key after its OpenWithProgids).
  async function remove(hive, entries) {
    await Promise.all(entries.filter((e) => e.op !== 'ifEmpty').map((e) =>
      run(['delete', key(hive, e.key), ...(e.op === 'value' ? ['/v', fill(e.name)] : []), '/f'])));
    for (const e of entries.filter((x) => x.op === 'ifEmpty')) {
      const { code, out } = await run(['query', key(hive, e.key)]);
      if (code === 0 && !out.trim()) await run(['delete', key(hive, e.key), '/f']);
    }
  }

  /** Turns part `files` or `folders` on or off → read()'s state after. */
  async function set(part, on) {
    const now = await read();
    const current = now && PARTS.includes(part) ? now[part] : null;
    if (!current) throw new Error('This copy of Gamma has no such entry to change.');
    if (!current.editable) throw new Error('Gamma is installed for all users of this computer: an administrator changes this by running its installer again.');
    if (store) {
      if (on) fs.rmSync(storeOff(), { force: true });
      else fs.writeFileSync(storeOff(), STORE_NOTE);
      return read();
    }
    const hive = 'HKCU';
    const p = TABLE.parts[part];
    if (on) await Promise.all(p.write.map((w) => write(hive, w)));
    else await remove(hive, p.remove);
    await must(['add', key(hive, TABLE.record), '/v', p.value, '/t', 'REG_DWORD', '/d', on ? '1' : '0', '/f']);
    return read();
  }

  return { read, set };
}

module.exports = { create, flag, PARTS };
