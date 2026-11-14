// What the desktop app registers with the operating system: the generated
// files match the table (tests/shared/openwith.json), the NSIS uninstaller
// removes exactly what the installer writes, and the build configs take
// their file types from the generator (docs/architecture.md "Opening files
// and folders").
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const openwith = require('../scripts/openwith.cjs');

const DESKTOP = path.resolve(__dirname, '..');
const FIXTURE = JSON.parse(fs.readFileSync(path.resolve(DESKTOP, '..', 'tests', 'shared', 'openwith.json'), 'utf8'));

// One NSIS line as its instruction and string arguments ("…", '…' or `…`).
function parse(line) {
  const t = line.trim();
  const op = t.split(/\s+/, 1)[0];
  const flag = /^\S+\s+(\/\w+)/.exec(t)?.[1] || '';
  const args = [...t.matchAll(/"([^"]*)"|'([^']*)'|`([^`]*)`/g)].map((m) => m[1] ?? m[2] ?? m[3]);
  return { op, flag, args };
}

function macro(text, name) {
  const m = new RegExp(`!macro ${name}\\n([\\s\\S]*?)!macroend`).exec(text);
  assert.ok(m, `no ${name}`);
  return m[1].split('\n').map(parse).filter((l) => /^(WriteReg|DeleteReg)/.test(l.op));
}

const under = (key, root) => key === root || key.startsWith(root + '\\');

test('the committed files are what the generator writes', () => {
  for (const [rel, text] of Object.entries(openwith.generated())) {
    assert.strictEqual(fs.readFileSync(path.join(DESKTOP, rel), 'utf8'), text, `${rel}: run npm run openwith`);
  }
});

test('the table is the fixture; ProgIDs start with Gamma.; the manifest names no ContentType', () => {
  assert.deepStrictEqual(openwith.table(), FIXTURE.kinds);
  for (const k of openwith.table()) assert.match(k.progid, /^Gamma\./);
  assert.doesNotMatch(openwith.appx(), /ContentType=/);
  const kinds = [...openwith.appx().matchAll(/FileTypeAssociation Name="([^"]+)"/g)].map((m) => m[1]);
  assert.deepStrictEqual(kinds, FIXTURE.kinds.map((k) => k.kind));
  const exts = [...openwith.appx().matchAll(/<uap:FileType>([^<]+)</g)].map((m) => m[1]);
  assert.deepStrictEqual(exts, FIXTURE.kinds.flatMap((k) => k.exts));
  assert.deepStrictEqual(openwith.dropped, []);
});

test('NSIS: per kind a ProgID, per extension Open with, the folder verbs, and their quoting', () => {
  const text = openwith.nsis();
  const install = macro(text, 'customInstall');
  for (const k of FIXTURE.kinds) {
    assert.ok(text.includes(`WriteRegStr SHELL_CONTEXT "Software\\Classes\\${k.progid}\\shell\\open\\command" "" '"$appExe" "%1"'`));
    for (const ext of k.exts) {
      assert.ok(install.some((l) => l.op === 'WriteRegNone' && l.args[0] === `Software\\Classes\\${ext}\\OpenWithProgids` && l.args[1] === k.progid));
      assert.ok(install.some((l) => l.op === 'WriteRegStr' && l.args[0] === 'Software\\${APP_ID}\\Capabilities\\FileAssociations' && l.args[1] === ext && l.args[2] === k.progid));
    }
  }
  // Never an extension's own default value.
  assert.ok(!install.some((l) => /^Software\\Classes\\\.[a-z]+$/.test(l.args[0])));
  for (const key of ['Directory\\shell\\Gamma', 'Directory\\Background\\shell\\Gamma']) {
    assert.ok(text.includes(`WriteRegStr SHELL_CONTEXT "Software\\Classes\\${key}" "" "${FIXTURE.folder_verb}"`));
    assert.ok(text.includes(`WriteRegStr SHELL_CONTEXT "Software\\Classes\\${key}\\command" "" '"$appExe" "%V"'`));
  }
  assert.ok(!/Drive\\shell|Folder\\shell|\*\\shell/.test(text));
  assert.ok(text.includes('WriteRegStr SHELL_CONTEXT "Software\\RegisteredApplications" "${PRODUCT_NAME}" "Software\\${APP_ID}\\Capabilities"'));
  // The uninstall runs only when not an update; SHChangeNotify ends both.
  assert.match(text, /!macro customUnInstall\n {2}\$\{ifNot\} \$\{isUpdated\}\n[\s\S]*SHChangeNotify[^\n]*\n {2}\$\{endIf\}\n!macroend/);
  assert.match(text, /SHChangeNotify[^\n]*\n!macroend\n\n!macro customUnInstall/);
});

test('NSIS: the uninstaller removes exactly what the installer writes', () => {
  const install = macro(openwith.nsis(), 'customInstall');
  const uninstall = macro(openwith.nsis(), 'customUnInstall');
  const whole = uninstall.filter((l) => l.op === 'DeleteRegKey' && !l.flag).map((l) => l.args[0]);
  const values = uninstall.filter((l) => l.op === 'DeleteRegValue').map((l) => l.args.slice(0, 2).join('|'));
  const writes = install.filter((l) => l.op.startsWith('WriteReg'));
  for (const w of writes) {
    const [key, name] = w.args;
    const gone = whole.some((root) => under(key, root)) || (name && values.includes(`${key}|${name}`));
    assert.ok(gone, `nothing in customUnInstall removes ${key} ${name}`);
  }
  // Each delete is of something the installer wrote, and a whole key is
  // never an extension's (.pdf's holds another reader's entries).
  for (const root of whole) {
    assert.ok(writes.some((w) => under(w.args[0], root)), `customUnInstall deletes ${root}, never written`);
    assert.doesNotMatch(root, /^Software\\Classes\\\.[a-z0-9]+(\\|$)/);
  }
  for (const v of values) assert.ok(writes.some((w) => w.args.slice(0, 2).join('|') === v), `customUnInstall deletes the value ${v}, never written`);
  // Per extension: the value, then its OpenWithProgids key and its own key, both /ifempty.
  for (const k of FIXTURE.kinds) {
    for (const ext of k.exts) {
      const i = uninstall.findIndex((l) => l.op === 'DeleteRegValue' && l.args[0] === `Software\\Classes\\${ext}\\OpenWithProgids` && l.args[1] === k.progid);
      assert.ok(i >= 0, `no DeleteRegValue for ${ext}`);
      assert.deepStrictEqual(uninstall[i + 1], { op: 'DeleteRegKey', flag: '/ifempty', args: [`Software\\Classes\\${ext}\\OpenWithProgids`] });
      assert.deepStrictEqual(uninstall[i + 2], { op: 'DeleteRegKey', flag: '/ifempty', args: [`Software\\Classes\\${ext}`] });
    }
  }
});

test('NSIS: an extension dropped from the table is deleted by customInstall, the same three in order', () => {
  assert.ok(!macro(openwith.nsis(), 'customInstall').some((l) => l.op.startsWith('Delete')));
  const install = macro(openwith.nsis([{ ext: '.ltx', progid: 'Gamma.latex' }]), 'customInstall');
  const i = install.findIndex((l) => l.op === 'DeleteRegValue' && l.args[0] === 'Software\\Classes\\.ltx\\OpenWithProgids');
  assert.ok(i >= 0);
  assert.deepStrictEqual(install.slice(i, i + 4), [
    { op: 'DeleteRegValue', flag: '', args: ['Software\\Classes\\.ltx\\OpenWithProgids', 'Gamma.latex'] },
    { op: 'DeleteRegKey', flag: '/ifempty', args: ['Software\\Classes\\.ltx\\OpenWithProgids'] },
    { op: 'DeleteRegKey', flag: '/ifempty', args: ['Software\\Classes\\.ltx'] },
    { op: 'DeleteRegValue', flag: '', args: ['Software\\${APP_ID}\\Capabilities\\FileAssociations', '.ltx'] },
  ]);
  // After every write, so a reinstall of the same version is no different.
  assert.ok(install.slice(i).every((l) => l.op.startsWith('Delete')));
});

test('Linux: the folder entries carry the verb, and the .deb maps them by absolute path', () => {
  const kde = openwith.kdeMenu();
  for (const line of ['Type=Service', 'X-KDE-ServiceTypes=KonqPopupMenu/Plugin', 'MimeType=inode/directory;', 'Actions=openGamma;', '[Desktop Action openGamma]', `Name=${FIXTURE.folder_verb}`, 'Icon=gamma', 'Exec=gamma %F']) {
    assert.ok(kde.split('\n').includes(line), line);
  }
  const nemo = openwith.nemoAction().split('\n');
  for (const line of ['[Nemo Action]', `Name=${FIXTURE.folder_verb.replace('&', '')}`, 'Exec=gamma %F', 'Icon-Name=gamma', 'Selection=any', 'Extensions=dir;']) {
    assert.ok(nemo.includes(line), line);
  }
  assert.ok(!openwith.nemoAction().includes('&'));
  const fpm = openwith.debFpm();
  assert.deepStrictEqual(fpm.map((m) => m.split('=')[1]), [
    '/usr/share/kio/servicemenus/gamma-open.desktop',
    '/usr/share/kservices5/ServiceMenus/gamma-open.desktop',
    '/usr/share/nemo/actions/gamma.nemo_action',
  ]);
  for (const m of fpm) {
    const src = m.split('=')[0];
    assert.ok(path.isAbsolute(src) && fs.existsSync(src), src);
  }

  const config = require('../electron-builder.cjs');
  assert.deepStrictEqual(config.deb.fpm, fpm);
  assert.deepStrictEqual(config.linux.mimeTypes, ['application/pdf']);
  // scripts/ and the Linux entries are build-time only.
  assert.ok(!config.files.some((f) => f.startsWith('scripts') || f.startsWith('assets/linux')));
});

test('macOS: an alternate viewer per kind and public.folder; the PDF entry as before', () => {
  const config = require('../electron-builder.cjs');
  assert.deepStrictEqual(config.mac.fileAssociations, openwith.macFileAssociations());
  assert.deepStrictEqual(config.mac.fileAssociations[0], { ext: 'pdf', name: 'PDF document', role: 'Viewer', rank: 'Alternate' });
  assert.deepStrictEqual(config.mac.fileAssociations.find((a) => a.name === 'Markdown document').ext, ['md', 'markdown']);
  for (const a of config.mac.fileAssociations) assert.deepStrictEqual([a.role, a.rank], ['Viewer', 'Alternate']);
  assert.deepStrictEqual(config.mac.extendInfo.CFBundleDocumentTypes, [{
    CFBundleTypeName: 'Folder', CFBundleTypeRole: 'Viewer', LSHandlerRank: 'Alternate', LSItemContentTypes: ['public.folder'],
  }]);
  assert.ok(config.mac.extendInfo.NSMicrophoneUsageDescription);
  assert.ok(!('fileAssociations' in config), 'no top-level fileAssociations: NSIS would take the type over');
});

test('the Mac App Store build stays PDF-only, with no folder type', () => {
  const vars = { GAMMA_MAS_VERSION: '1.0', GAMMA_MAS_BUILD: '1', GAMMA_MAS_PROFILE: 'test.provisionprofile' };
  const saved = Object.fromEntries(Object.keys(vars).map((k) => [k, process.env[k]]));
  Object.assign(process.env, vars);
  try {
    const mas = require('../electron-builder.mas.cjs');
    assert.deepStrictEqual(mas.mac.fileAssociations, [{ ext: 'pdf', name: 'PDF document', role: 'Viewer', rank: 'Alternate' }]);
    assert.ok(!('CFBundleDocumentTypes' in mas.mac.extendInfo));
    assert.ok(mas.mac.extendInfo.NSMicrophoneUsageDescription);
    assert.strictEqual(mas.mac.extendInfo.CFBundleShortVersionString, '1.0');
  } finally {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  }
});
