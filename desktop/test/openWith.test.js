// What the desktop app registers with the operating system: the generated
// files match the table (tests/shared/openwith.json), the NSIS installer
// writes each of its two parts only when chosen and removes it otherwise,
// its uninstaller removes exactly what it writes and never another app's
// entries, and the build configs take their file types from the generator
// (docs/architecture.md "Opening files and folders").
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const openwith = require('../scripts/openwith.cjs');

const DESKTOP = path.resolve(__dirname, '..');
const FIXTURE = JSON.parse(fs.readFileSync(path.resolve(DESKTOP, '..', 'tests', 'shared', 'openwith.json'), 'utf8'));

// One NSIS line as its instruction and string arguments ("…", '…' or `…`);
// the generated macro that deletes a key once empty reads as `IfEmpty`.
function parse(line) {
  const t = line.trim().replace(/^!insertmacro gammaDeleteKeyIfEmpty\b/, 'IfEmpty');
  const op = t.split(/\s+/, 1)[0];
  const flag = /^\S+\s+(\/\w+)/.exec(t)?.[1] || '';
  const args = [...t.matchAll(/"([^"]*)"|'([^']*)'|`([^`]*)`/g)].map((m) => m[1] ?? m[2] ?? m[3]);
  return { op, flag, args };
}

const REGISTRY_OP = /^(WriteReg|DeleteReg|IfEmpty$)/;

function macroText(text, name) {
  const m = new RegExp(`!macro ${name}\\n([\\s\\S]*?)!macroend`).exec(text);
  assert.ok(m, `no ${name}`);
  return m[1];
}

function macro(text, name) {
  return macroText(text, name).split('\n').map(parse).filter((l) => REGISTRY_OP.test(l.op));
}

// A part of parts() as the NSIS lines it becomes.
const asLines = (entries) => entries.map((e) => (e.type
  ? { op: e.type === 'none' ? 'WriteRegNone' : 'WriteRegStr', flag: '', args: e.type === 'none' ? [e.key, e.name] : [e.key, e.name, e.data] }
  : { op: { value: 'DeleteRegValue', key: 'DeleteRegKey', ifEmpty: 'IfEmpty' }[e.op], flag: '', args: e.op === 'value' ? [e.key, e.name] : [e.key] }));

// The install's block for one part: what `${if} <v> = 1` writes and what its `${else}` removes.
function branches(text, v) {
  const m = new RegExp(`\\$\\{if\\} \\${v} = 1\\n([\\s\\S]*?)\\n\\s+\\$\\{else\\}\\n([\\s\\S]*?)\\n\\s+\\$\\{endIf\\}`).exec(macroText(text, 'gammaOpenWithInstall'));
  assert.ok(m, `no block for ${v}`);
  const lines = (s) => s.split('\n').map(parse).filter((l) => REGISTRY_OP.test(l.op));
  return { on: lines(m[1]), off: lines(m[2]) };
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

test('Store: the folder verb is the class of native/open-with/, on a folder and an open folder\'s background', () => {
  const x = openwith.appx();
  const { clsid, dll, verbId } = openwith.OPEN_WITH;
  assert.ok(x.includes(`<com:Class Id="${clsid}" Path="app\\${dll}" ThreadingModel="STA" />`));
  const verbs = [...x.matchAll(/<desktop5:ItemType Type="([^"]+)">\s*<desktop5:Verb Id="([^"]+)" Clsid="([^"]+)" \/>/g)].map((m) => m.slice(1));
  assert.deepStrictEqual(verbs, [['Directory', verbId, clsid], ['Directory\\Background', verbId, clsid]]);
  // The header gives the DLL the same class, the table's words and the app it starts.
  assert.strictEqual(openwith.guidInit('a5ac2231-021b-477c-846c-b5a2a3ef67c2'),
    '{0xa5ac2231, 0x021b, 0x477c, {0x84, 0x6c, 0xb5, 0xa2, 0xa3, 0xef, 0x67, 0xc2}}');
  const h = openwith.verbHeader();
  assert.ok(h.includes(`CLSID_OpenWithGamma = ${openwith.guidInit(clsid)};`));
  assert.ok(h.includes(`#define GAMMA_VERB_TITLE L"${FIXTURE.folder_verb}"`));
  const config = require('../electron-builder.cjs');
  assert.ok(h.includes(`#define GAMMA_APP_ID L"${config.appx.applicationId}"`));
  assert.ok(h.includes(`#define GAMMA_EXE L"${config.productName}.exe"`));
  // The launcher's switch hides it by a file in the app's data directory,
  // which Electron names after package.json's name (the packaged one has no
  // productName); the app writes it to app.getPath('userData').
  assert.ok(h.includes(`#define GAMMA_DATA_DIR L"${require('../package.json').name}"`));
  assert.ok(!('productName' in require('../package.json')), 'a productName would rename the data directory');
  assert.ok(h.includes(`#define GAMMA_FOLDER_VERB_OFF L"${openwith.OPEN_WITH.off}"`));
  assert.strictEqual(require('../lib/openwith.json').storeOff, openwith.OPEN_WITH.off);
  // The DLL goes in by the afterPack hook; native/ itself never ships.
  assert.strictEqual(config.afterPack, 'scripts/after-pack.cjs');
  assert.ok(!config.files.some((f) => f.startsWith('native')));
});

// The Store manifest template (assets/appxmanifest.xml) and its <Package …> start tag.
function storeManifest() {
  const config = require('../electron-builder.cjs');
  const text = fs.readFileSync(path.join(DESKTOP, config.directories.buildResources, config.appx.customManifestPath), 'utf8');
  return { text, start: text.slice(text.indexOf('<Package'), text.indexOf('>', text.indexOf('<Package'))) };
}

test('Store: the manifest template declares the verb\'s namespaces', () => {
  const { start } = storeManifest();
  // Every prefix the extensions use is declared; the folder verb's newer ones are ignorable.
  for (const prefix of new Set([...openwith.appx().matchAll(/<(\w+):/g)].map((m) => m[1]))) {
    assert.match(start, new RegExp(`xmlns:${prefix}="http://schemas\\.microsoft\\.com/appx/manifest/`), prefix);
  }
  assert.match(start, /IgnorableNamespaces="desktop4 desktop5"/);
});

// electron-builder's own template, where desktop/ is installed (npm ci); the
// PR's check runs this suite with Node's built-ins alone, no install.
const UPSTREAM_MANIFEST = path.join(DESKTOP, 'node_modules', 'app-builder-lib', 'templates', 'appx', 'appxmanifest.xml');

test('Store: the manifest template is electron-builder\'s', {
  skip: !fs.existsSync(UPSTREAM_MANIFEST) && 'electron-builder is not installed (npm ci in desktop/)',
}, () => {
  const { text: ours, start } = storeManifest();
  const theirs = fs.readFileSync(UPSTREAM_MANIFEST, 'utf8');
  // Everything after the <Package …> start tag is upstream's, macros included.
  const body = (t) => t.slice(t.indexOf('>', t.indexOf('<Package')) + 1);
  assert.strictEqual(body(ours), body(theirs), 'electron-builder\'s template changed: carry the change into assets/appxmanifest.xml');
  for (const decl of theirs.slice(theirs.indexOf('<Package'), theirs.indexOf('>', theirs.indexOf('<Package'))).match(/xmlns(:\w+)?="[^"]+"/g)) {
    assert.ok(start.includes(decl), decl);
  }
});

test('NSIS: per kind a ProgID, per extension Open with, the folder verbs, and their quoting', () => {
  const text = openwith.nsis();
  const install = macro(text, 'gammaOpenWithInstall').filter((l) => l.op.startsWith('WriteReg'));
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
  // SHChangeNotify ends the install and the uninstall (installer.nsh runs the
  // uninstall only when not an update: test/installer.test.js).
  assert.match(macroText(text, 'gammaOpenWithInstall'), /SHChangeNotify[^\n]*\n$/);
  assert.match(macroText(text, 'gammaOpenWithUnInstall'), /SHChangeNotify[^\n]*\n$/);
});

// Whether `removals` undo `writes`: each write's key goes whole, or its value
// does; each removal is of something written (a key once empty: a written
// key or one above one); a whole key is never an extension's, whose may
// hold another app's entries.
function assertUndoes(removals, writes, what) {
  const whole = removals.filter((l) => l.op === 'DeleteRegKey').map((l) => l.args[0]);
  const values = removals.filter((l) => l.op === 'DeleteRegValue').map((l) => l.args.slice(0, 2).join('|'));
  for (const w of writes) {
    const [key, name] = w.args;
    const gone = whole.some((root) => under(key, root)) || (name && values.includes(`${key}|${name}`));
    assert.ok(gone, `nothing in ${what} removes ${key} ${name}`);
  }
  for (const root of whole) {
    assert.ok(writes.some((w) => under(w.args[0], root)), `${what} deletes ${root}, never written`);
    assert.doesNotMatch(root, /^Software\\Classes\\\.[a-z0-9]+(\\|$)/);
  }
  for (const v of values) assert.ok(writes.some((w) => w.args.slice(0, 2).join('|') === v), `${what} deletes the value ${v}, never written`);
  for (const l of removals.filter((x) => x.op === 'IfEmpty')) {
    assert.ok(writes.some((w) => under(w.args[0], l.args[0])), `${what} deletes ${l.args[0]} once empty, never written there`);
  }
}

test('NSIS: each part is written when on and removed when off, its choice recorded first', () => {
  const text = openwith.nsis();
  const { files, folders } = openwith.parts();
  for (const [v, p] of [['$gammaFiles', files], ['$gammaFolders', folders]]) {
    const { on, off } = branches(text, v);
    assert.deepStrictEqual(on, asLines(p.write));
    assert.deepStrictEqual(off, asLines(p.remove));
    assertUndoes(off, on, `${v} off`);
  }
  const install = macroText(text, 'gammaOpenWithInstall');
  // Read back when the page did not run (an update, a silent install), then recorded, then applied.
  const at = (s) => { const i = install.indexOf(s); assert.ok(i >= 0, s); return i; };
  assert.ok(at('${if} $gammaChosen != 1\n    Call gammaReadChoice') < at('WriteRegDWORD SHELL_CONTEXT "Software\\${APP_ID}" "OpenWithFiles" $gammaFiles'));
  assert.ok(at('"OpenWithFolders" $gammaFolders') < at('${if} $gammaFiles = 1'));
  assert.ok(at('${if} $gammaFiles = 1') < at('${if} $gammaFolders = 1'));
  // The folder part is the verbs alone, the files part everything else.
  assert.ok(folders.write.every((w) => /\\Directory\\(Background\\)?shell\\Gamma(\\command)?$/.test(w.key)));
  assert.ok(!files.write.some((w) => /\\Directory\\/.test(w.key)));
});

test('NSIS: the page offers both parts, ticked as recorded (on when not), and an update skips it', () => {
  const page = macroText(openwith.nsis(), 'gammaOpenWithPage');
  const say = openwith.words();
  assert.match(page, /^\s+Page custom gammaOpenWithShow gammaOpenWithLeave$/m);
  assert.match(page, /Function gammaOpenWithShow\n\s+\$\{if\} \$\{isUpdated\}\n\s+Abort\n\s+\$\{endIf\}\n\s+Call gammaReadChoice/);
  for (const [v, value] of [['$gammaFiles', 'OpenWithFiles'], ['$gammaFolders', 'OpenWithFolders']]) {
    assert.ok(page.includes(`ReadRegDWORD ${v} SHELL_CONTEXT "Software\\\${APP_ID}" "${value}"\n    \${if} \${Errors}\n      StrCpy ${v} 1`), v);
  }
  assert.ok(page.includes(`\${NSD_CreateCheckbox} 0 0 100% 14u "${say.files}"`));
  assert.ok(page.includes(`\${NSD_CreateCheckbox} 0 20u 100% 14u "${say.folders}"`));
  assert.ok(page.includes(`"${say.note}"`));
  assert.match(page, /\$\{NSD_GetState\} \$gammaFilesBox \$gammaFiles\n\s+\$\{NSD_GetState\} \$gammaFoldersBox \$gammaFolders\n\s+StrCpy \$gammaChosen 1/);
  // The types in words are the table's kinds, in order.
  assert.strictEqual(openwith.typesPhrase().replace(' and ', ', '), FIXTURE.kinds.map((k) => k.name.split(' ')[0]).join(', '));
  assert.ok(say.files.includes(openwith.typesPhrase()));
  assert.ok(say.folders.includes(FIXTURE.folder_verb.replace('&', '')));
  // The page and the install's variables exist in an assisted installer alone.
  const config = require('../electron-builder.cjs');
  assert.strictEqual(config.nsis.oneClick, false);
  assert.strictEqual(config.nsis.include, 'installer.nsh');
});

test('NSIS: the uninstaller removes exactly what the installer writes', () => {
  const install = macro(openwith.nsis(), 'gammaOpenWithInstall');
  const uninstall = macro(openwith.nsis(), 'gammaOpenWithUnInstall');
  assertUndoes(uninstall, install.filter((l) => l.op.startsWith('WriteReg')), 'the uninstall');
  const { files, folders } = openwith.parts();
  assert.deepStrictEqual(uninstall, [
    ...asLines([...files.remove, ...folders.remove]),
    { op: 'DeleteRegKey', flag: '', args: ['Software\\${APP_ID}'] },
  ]);
});

test('NSIS: an extension\'s keys go only once empty, after Gamma\'s value; never by /ifempty, which keeps no value', () => {
  const text = openwith.nsis();
  for (const removals of [branches(text, '$gammaFiles').off, macro(text, 'gammaOpenWithUnInstall')]) {
    for (const k of FIXTURE.kinds) {
      for (const ext of k.exts) {
        const i = removals.findIndex((l) => l.op === 'DeleteRegValue' && l.args[0] === `Software\\Classes\\${ext}\\OpenWithProgids` && l.args[1] === k.progid);
        assert.ok(i >= 0, `no DeleteRegValue for ${ext}`);
        assert.deepStrictEqual(removals.slice(i + 1, i + 3), [
          { op: 'IfEmpty', flag: '', args: [`Software\\Classes\\${ext}\\OpenWithProgids`] },
          { op: 'IfEmpty', flag: '', args: [`Software\\Classes\\${ext}`] },
        ]);
      }
    }
  }
  assert.doesNotMatch(text.replace(/^\s*;.*$/gm, ''), /\/ifempty/);
  // The macro deletes a key with no value (EnumRegValue's error, the default
  // value included) and no subkey, and leaves the caller's registers.
  const m = macroText(text, 'gammaDeleteKeyIfEmpty KEY');
  assert.match(m, /^\s+Push \$R8\n\s+Push \$R9\n\s+ClearErrors\n\s+EnumRegValue \$R8 SHELL_CONTEXT "\$\{KEY\}" 0\n\s+\$\{if\} \$\{Errors\}\n\s+EnumRegKey \$R9 SHELL_CONTEXT "\$\{KEY\}" 0\n\s+\$\{if\} \$R9 == ""\n\s+DeleteRegKey SHELL_CONTEXT "\$\{KEY\}"\n/);
  assert.match(m, /Pop \$R9\n\s+Pop \$R8\n$/);
});

test('NSIS: an extension dropped from the table is deleted by the install after both parts, the same three in order', () => {
  const after = (text) => macro(text, 'gammaOpenWithInstall').slice(macro(text, 'gammaOpenWithInstall').findLastIndex((l) => l.args[0]?.includes('\\Directory\\Background\\shell\\Gamma')) + 1);
  assert.deepStrictEqual(after(openwith.nsis()), []);
  assert.deepStrictEqual(after(openwith.nsis([{ ext: '.ltx', progid: 'Gamma.latex' }])), [
    { op: 'DeleteRegValue', flag: '', args: ['Software\\Classes\\.ltx\\OpenWithProgids', 'Gamma.latex'] },
    { op: 'IfEmpty', flag: '', args: ['Software\\Classes\\.ltx\\OpenWithProgids'] },
    { op: 'IfEmpty', flag: '', args: ['Software\\Classes\\.ltx'] },
    { op: 'DeleteRegValue', flag: '', args: ['Software\\${APP_ID}\\Capabilities\\FileAssociations', '.ltx'] },
  ]);
});

test('the launcher\'s switches take the installer\'s parts and record, its defines put in, and its words', () => {
  const rt = require('../lib/openwith.json');
  const config = require('../electron-builder.cjs');
  // The installer's ${APP_ID} and ${PRODUCT_NAME} are the config's appId and
  // productName (not the app's own name, which is package.json's); the app
  // puts in only $appExe, its path.
  const fill = (v) => (typeof v === 'string' ? v.replace(/\$\{APP_ID\}/g, config.appId).replace(/\$\{PRODUCT_NAME\}/g, config.productName) : v);
  const filled = (entries) => entries.map((e) => Object.fromEntries(Object.entries(e).map(([k, v]) => [k, fill(v)])));
  const parts = openwith.parts();
  for (const name of Object.keys(parts)) {
    assert.deepStrictEqual(rt.parts[name], { value: parts[name].value, write: filled(parts[name].write), remove: filled(parts[name].remove) });
  }
  assert.deepStrictEqual(Object.keys(rt.parts), Object.keys(parts));
  assert.strictEqual(rt.record, `Software\\${config.appId}`);
  assert.ok(rt.parts.files.write.some((w) => w.key === 'Software\\RegisteredApplications' && w.name === 'Gamma'));
  assert.doesNotMatch(JSON.stringify(rt), /\$\{/);
  assert.strictEqual(rt.types, openwith.typesPhrase());
  assert.strictEqual(rt.verb, FIXTURE.folder_verb.replace('&', ''));
  assert.ok(config.files.includes('lib/**'));
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
