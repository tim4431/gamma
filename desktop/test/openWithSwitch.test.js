// The launcher's switches over Gamma's entries in File Explorer
// (lib/openWith.js): what they read from the installer's record, that a
// switch writes or removes its part as the installer does and records it,
// that another app's entries stay, that an install for all users and the
// Store's file types are shown and never changed, and the Store's folder
// verb by its file. Against a stand-in for reg.exe; on Windows also against
// reg.exe itself, under a throwaway key Explorer never reads.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const { create } = require('../lib/openWith');
const TABLE = require('../lib/openwith.json');

const EXE = 'C:\\Users\\Some One\\AppData\\Local\\Programs\\Gamma\\Gamma.exe';
const RECORD = TABLE.record;
const opts = (extra) => ({ platform: 'win32', installed: true, exe: EXE, ...extra });

// reg.exe's query, add and delete over a map of keys, as it answers them:
// exit code 1 for what is not there; a query lists the key's values and
// then its subkeys, and prints nothing more for an empty key.
function fakeReg() {
  const keys = new Map(); // lowercased path → { path, values: Map(lowercased name → { name, type, data }) }
  const norm = (k) => k.toLowerCase();
  const ensure = (k) => {
    const parts = k.split('\\');
    for (let i = 1; i <= parts.length; i++) {
      const p = parts.slice(0, i).join('\\');
      if (!keys.has(norm(p))) keys.set(norm(p), { path: p, values: new Map() });
    }
    return keys.get(norm(k));
  };
  const subkeys = (k) => [...keys.values()].filter((e) => norm(e.path).startsWith(`${norm(k)}\\`) && !e.path.slice(k.length + 1).includes('\\'));
  const flag = (args, f) => { const i = args.indexOf(f); return i >= 0 ? args[i + 1] : undefined; };
  const calls = [];
  const run = async (args) => {
    calls.push(args);
    const [op, k] = args;
    const named = args.includes('/ve') ? '' : flag(args, '/v');
    if (op === 'query') {
      const e = keys.get(norm(k));
      if (!e) return { code: 1, out: '' };
      const values = [...e.values.values()].map((v) => `    ${v.name || '(Default)'}    ${v.type}    ${v.data}`);
      const subs = subkeys(k).map((s) => s.path);
      return { code: 0, out: `\r\n${values.length ? `${e.path}\r\n${values.join('\r\n')}\r\n\r\n` : ''}${subs.join('\r\n')}${subs.length ? '\r\n' : ''}` };
    }
    if (op === 'add') {
      const type = flag(args, '/t') || 'REG_SZ';
      const data = type === 'REG_DWORD' ? `0x${Number(flag(args, '/d')).toString(16)}` : flag(args, '/d') ?? '';
      ensure(k).values.set(norm(named ?? ''), { name: named ?? '', type, data });
      return { code: 0, out: '' };
    }
    if (op === 'delete') {
      const e = keys.get(norm(k));
      if (!e) return { code: 1, out: '' };
      if (named !== undefined) return { code: e.values.delete(norm(named)) ? 0 : 1, out: '' };
      for (const key of [...keys.keys()]) if (key === norm(k) || key.startsWith(`${norm(k)}\\`)) keys.delete(key);
      return { code: 0, out: '' };
    }
    throw new Error(`reg ${op}?`);
  };
  const value = (k, name = '') => keys.get(norm(k))?.values.get(norm(name));
  return { run, keys, calls, ensure, value, has: (k) => keys.has(norm(k)) };
}

// An install as the installer leaves it with both parts on: each write of
// both parts, in `hive`, and its record (`values` false: from before the page).
function installed(reg, hive = 'HKCU', values = true) {
  for (const p of Object.values(TABLE.parts)) {
    for (const w of p.write) {
      reg.ensure(`${hive}\\${w.key}`).values.set(w.name.toLowerCase(), { name: w.name, type: w.type === 'none' ? 'REG_NONE' : 'REG_SZ', data: (w.data || '').replace(/\$appExe/g, EXE) });
    }
    if (values) reg.ensure(`${hive}\\${RECORD}`).values.set(p.value.toLowerCase(), { name: p.value, type: 'REG_DWORD', data: '0x1' });
  }
}

test('nothing to switch off Windows, in a dev run or the test scripts, or without an install record', async () => {
  const reg = fakeReg();
  assert.strictEqual(await create(opts({ platform: 'darwin', run: reg.run })).read(), null);
  assert.strictEqual(await create(opts({ installed: false, run: reg.run })).read(), null);
  assert.deepStrictEqual(reg.calls, [], 'nothing asked of the registry');
  assert.strictEqual(await create(opts({ run: reg.run })).read(), null, 'an unpacked build: no record');
  await assert.rejects(create(opts({ run: reg.run })).set('files', false), /no such entry/);
});

test('a per-user install: both on as recorded, or from before the page; each switch removes and writes its part and records it', async () => {
  const reg = fakeReg();
  installed(reg, 'HKCU', false);
  const ow = create(opts({ run: reg.run }));
  assert.deepStrictEqual(await ow.read(), {
    types: TABLE.types, verb: TABLE.verb, store: false, allUsers: false,
    files: { on: true, editable: true }, folders: { on: true, editable: true },
  });

  const verb = 'HKCU\\Software\\Classes\\Directory\\shell\\Gamma';
  let s = await ow.set('folders', false);
  assert.deepStrictEqual([s.files.on, s.folders.on], [true, false]);
  assert.ok(!reg.has(verb) && !reg.has('HKCU\\Software\\Classes\\Directory\\Background\\shell\\Gamma'));
  assert.ok(reg.has('HKCU\\Software\\Classes\\Gamma.pdf'), 'the other part stays');
  assert.deepStrictEqual(reg.value(`HKCU\\${RECORD}`, 'OpenWithFolders'), { name: 'OpenWithFolders', type: 'REG_DWORD', data: '0x0' });

  s = await ow.set('folders', true);
  assert.strictEqual(s.folders.on, true);
  assert.strictEqual(reg.value(verb).data, TABLE.verb.replace('Gamma', '&Gamma'));
  assert.strictEqual(reg.value(`${verb}\\command`).data, `"${EXE}" "%V"`);
  assert.strictEqual(reg.value(verb, 'Icon').data, `${EXE},0`);
  assert.strictEqual(reg.value(`HKCU\\${RECORD}`, 'OpenWithFolders').data, '0x1');

  s = await ow.set('files', false);
  assert.strictEqual(s.files.on, false);
  for (const k of ['Gamma.pdf', 'Gamma.markdown', 'Gamma.typst', '.typ', '.bib', '.pdf']) assert.ok(!reg.has(`HKCU\\Software\\Classes\\${k}`), k);
  assert.ok(!reg.has(`HKCU\\${RECORD}\\Capabilities`));
  assert.strictEqual(reg.value('HKCU\\Software\\RegisteredApplications', 'Gamma'), undefined);
  assert.ok(reg.has(`HKCU\\${RECORD}`), 'the record stays, for the next update');
  s = await ow.set('files', true);
  assert.strictEqual(s.files.on, true);
  assert.strictEqual(reg.value('HKCU\\Software\\Classes\\.typ\\OpenWithProgids', 'Gamma.typst').type, 'REG_NONE');
  assert.strictEqual(reg.value('HKCU\\Software\\Classes\\Gamma.pdf\\shell\\open\\command').data, `"${EXE}" "%1"`);
  assert.strictEqual(reg.value(`HKCU\\${RECORD}\\Capabilities\\FileAssociations`, '.md').data, 'Gamma.markdown');
  assert.strictEqual(reg.value('HKCU\\Software\\RegisteredApplications', 'Gamma').data, `${RECORD}\\Capabilities`);
});

test('switching the files off keeps another app\'s entries and the keys that hold them', async () => {
  const reg = fakeReg();
  installed(reg);
  reg.ensure('HKCU\\Software\\Classes\\.md\\OpenWithProgids').values.set('vscode.md', { name: 'VSCode.md', type: 'REG_NONE', data: '' });
  reg.ensure('HKCU\\Software\\Classes\\.pdf').values.set('', { name: '', type: 'REG_SZ', data: 'Acrobat.Document' });
  await create(opts({ run: reg.run })).set('files', false);
  assert.ok(reg.value('HKCU\\Software\\Classes\\.md\\OpenWithProgids', 'VSCode.md'));
  assert.strictEqual(reg.value('HKCU\\Software\\Classes\\.md\\OpenWithProgids', 'Gamma.markdown'), undefined);
  assert.strictEqual(reg.value('HKCU\\Software\\Classes\\.pdf').data, 'Acrobat.Document');
  assert.ok(!reg.has('HKCU\\Software\\Classes\\.pdf\\OpenWithProgids'), 'emptied: gone');
  assert.ok(!reg.has('HKCU\\Software\\Classes\\.markdown'));
});

test('a part recorded off reads off; a failed write stops before the record', async () => {
  const reg = fakeReg();
  installed(reg);
  reg.ensure(`HKCU\\${RECORD}`).values.set('openwithfiles', { name: 'OpenWithFiles', type: 'REG_DWORD', data: '0x0' });
  assert.strictEqual((await create(opts({ run: reg.run })).read()).files.on, false);
  const failing = async (args) => (args[0] === 'add' && args[1].includes('Gamma.latex') ? { code: 5, out: '' } : reg.run(args));
  await assert.rejects(create(opts({ run: failing })).set('files', true), /could not change File Explorer's entries \(reg add exited with 5\)/);
  assert.strictEqual(reg.value(`HKCU\\${RECORD}`, 'OpenWithFiles').data, '0x0');
});

test('an install for all users is shown, never changed', async () => {
  const reg = fakeReg();
  installed(reg, 'HKLM');
  const ow = create(opts({ run: reg.run }));
  const s = await ow.read();
  assert.strictEqual(s.allUsers, true);
  assert.deepStrictEqual([s.files, s.folders], [{ on: true, editable: false }, { on: true, editable: false }]);
  const before = reg.calls.length;
  await assert.rejects(ow.set('folders', false), /installed for all users/);
  assert.ok(reg.calls.slice(before).every((a) => a[0] === 'query'), 'nothing written');
  assert.ok(reg.has('HKLM\\Software\\Classes\\Directory\\shell\\Gamma'));
});

test('the Store package: no file types row; its folder verb hidden by the file the DLL looks for', async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-openwith-'));
  try {
    const reg = fakeReg();
    const ow = create(opts({ store: true, dataDir, run: reg.run }));
    assert.deepStrictEqual(await ow.read(), {
      types: TABLE.types, verb: TABLE.verb, store: true, allUsers: false, files: null, folders: { on: true, editable: true },
    });
    await assert.rejects(ow.set('files', false), /no such entry/);
    assert.strictEqual((await ow.set('folders', false)).folders.on, false);
    assert.ok(fs.existsSync(path.join(dataDir, TABLE.storeOff)));
    assert.strictEqual((await ow.set('folders', true)).folders.on, true);
    assert.ok(!fs.existsSync(path.join(dataDir, TABLE.storeOff)));
    assert.deepStrictEqual(reg.calls, [], 'the registry is not the Store\'s');
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

// reg.exe itself, under HKCU\Software\GammaTest-<pid>: its quoting of the
// commands, REG_NONE, and a query of an emptied key.
test('reg.exe: the switches write and remove the entries under a throwaway key', { skip: process.platform !== 'win32' && 'Windows only' }, async () => {
  const root = `Software\\GammaTest-${process.pid}`;
  const query = (k) => { try { return execFileSync('reg', ['query', `HKCU\\${root}\\${k}`], { encoding: 'latin1' }); } catch { return null; } };
  try {
    execFileSync('reg', ['add', `HKCU\\${root}\\${RECORD}`, '/f']); // an install from before the page
    execFileSync('reg', ['add', `HKCU\\${root}\\Software\\Classes\\.md\\OpenWithProgids`, '/v', 'Other.md', '/t', 'REG_NONE', '/f']);
    const ow = create(opts({ root, exe: EXE }));
    assert.deepStrictEqual([(await ow.read()).files.on, (await ow.read()).folders.on], [true, true]);
    await ow.set('files', true);
    await ow.set('folders', true);
    assert.match(query('Software\\Classes\\Gamma.pdf\\shell\\open\\command'), new RegExp(`REG_SZ\\s+"${EXE.replace(/\\/g, '\\\\')}" "%1"`));
    assert.match(query('Software\\Classes\\Directory\\Background\\shell\\Gamma\\command'), /REG_SZ\s+"C:\\Users\\Some One\\[^"]+\\Gamma\.exe" "%V"/);
    assert.match(query('Software\\Classes\\.typ\\OpenWithProgids'), /Gamma\.typst\s+REG_NONE/);
    assert.match(query(`${RECORD}\\Capabilities`), /ApplicationName\s+REG_SZ\s+Gamma/);

    await ow.set('files', false);
    await ow.set('folders', false);
    assert.strictEqual(query('Software\\Classes\\Gamma.pdf'), null);
    assert.strictEqual(query('Software\\Classes\\.typ'), null, 'the key made for Gamma alone is gone');
    assert.match(query('Software\\Classes\\.md\\OpenWithProgids'), /Other\.md/);
    assert.doesNotMatch(query('Software\\Classes\\.md\\OpenWithProgids'), /Gamma\.markdown/);
    assert.strictEqual(query('Software\\Classes\\Directory\\shell\\Gamma'), null);
    assert.match(query(RECORD), /OpenWithFiles\s+REG_DWORD\s+0x0[\s\S]*OpenWithFolders\s+REG_DWORD\s+0x0/);
    assert.deepStrictEqual([(await ow.read()).files.on, (await ow.read()).folders.on], [false, false]);
  } finally {
    try { execFileSync('reg', ['delete', `HKCU\\${root}`, '/f'], { stdio: 'ignore' }); } catch {}
  }
});
