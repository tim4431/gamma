// The NSIS installer's hooks (assets/installer.nsh): they put in the
// generated File Explorer macros (assets/openwith.nsh), the app fits
// beneath the longest install folder the directory page accepts
// (scripts/install-depth.cjs), an update undoes
// nothing, and the uninstaller deletes Gamma's data only when the person
// ticks its box, and then only Gamma's own folders (docs/architecture.md
// "Shell state").
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const DESKTOP = path.resolve(__dirname, '..');
const config = require('../electron-builder.cjs');
const openwith = require('../scripts/openwith.cjs');
const TEXT = fs.readFileSync(path.join(DESKTOP, config.directories.buildResources, config.nsis.include), 'utf8');

function macroText(name) {
  const m = new RegExp(`^!macro ${name}\\n([\\s\\S]*?)^!macroend`, 'm').exec(TEXT);
  assert.ok(m, `no ${name}`);
  return m[1];
}
const code = (s) => s.split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith(';'));

test('the hooks put in the generated macros, from the file beside this one', () => {
  assert.strictEqual(path.basename(openwith.FILES.nsis), 'openwith.nsh');
  assert.strictEqual(path.dirname(path.join(DESKTOP, openwith.FILES.nsis)), path.join(DESKTOP, config.directories.buildResources));
  assert.ok(code(TEXT).includes('!include "${__FILEDIR__}\\openwith.nsh"'));
  assert.deepStrictEqual(code(macroText('customPageAfterChangeDir')), ['!insertmacro gammaInstallFolderLimit', '!insertmacro gammaOpenWithPage']);
  assert.deepStrictEqual(code(macroText('customInstall')), ['!insertmacro gammaOpenWithInstall']);
  const generated = openwith.nsis();
  for (const m of ['gammaOpenWithPage', 'gammaOpenWithInstall', 'gammaOpenWithUnInstall']) assert.match(generated, new RegExp(`^!macro ${m}$`, 'm'));
  // Each hook once: electron-builder inserts one macro of each name.
  for (const hook of ['customPageAfterChangeDir', 'customInstall', 'customUnInstall', 'customUnWelcomePage']) {
    assert.strictEqual(TEXT.match(new RegExp(`^!macro ${hook}$`, 'gm')).length, 1, hook);
    assert.doesNotMatch(generated, new RegExp(`^!macro ${hook}\\b`, 'm'));
  }
});

test('the app fits beneath the longest install folder the directory page accepts, or the build fails', () => {
  const { PATH_MAX, instdirMax, checkInstallDepth } = require('../scripts/install-depth.cjs');
  const max = instdirMax();
  const room = PATH_MAX - max - 1;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-depth-'));
  try {
    const deep = (n) => {
      const rel = path.join('resources', 'x'.repeat(n - 'resources'.length - 1));
      fs.mkdirSync(path.join(dir, 'resources'), { recursive: true });
      fs.writeFileSync(path.join(dir, rel), '');
      return rel;
    };
    const fits = deep(room);
    assert.deepStrictEqual(checkInstallDepth(dir, max), { deepest: fits, room });
    const over = deep(room + 1);
    assert.throws(() => checkInstallDepth(dir, max), (e) => e.message.startsWith(`${over} is ${room + 1} characters deep`));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
  // The check counts what electron-builder adds to a folder not naming the app, and lets an update and a silent install be.
  const check = code(macroText('gammaInstallFolderLimit'));
  assert.ok(check.includes('${StrContains} $1 "${APP_FILENAME}" $INSTDIR'));
  assert.ok(check.includes('${If} $0 > ${GAMMA_INSTDIR_MAX}'));
  assert.deepStrictEqual(check.slice(1, 5), ['Function .onVerifyInstDir', '${If} ${Silent}', '${OrIf} ${isUpdated}', 'Return']);
});

test('an update undoes nothing: the uninstall of the entries and of the data are both inside ${ifNot} ${isUpdated}', () => {
  const lines = code(macroText('customUnInstall'));
  assert.strictEqual(lines[0], '${ifNot} ${isUpdated}');
  assert.strictEqual(lines[lines.length - 1], '${endIf}');
  // Nothing but that block, and its own if/endIf pairs inside.
  let depth = 0;
  lines.forEach((l, i) => {
    if (/^\$\{if(Not)?\}/.test(l)) depth++;
    if (l === '${endIf}') depth--;
    if (i < lines.length - 1) assert.ok(depth >= 1, `outside the update guard: ${l}`);
  });
  assert.strictEqual(depth, 0);
  assert.ok(lines.includes('!insertmacro gammaOpenWithUnInstall'));
});

test('the data box is never ticked by itself, and only a tick deletes', () => {
  const page = macroText('customUnWelcomePage');
  assert.match(page, /^\s+!insertmacro MUI_UNPAGE_WELCOME\n/);
  assert.match(page, /UninstPage custom un\.gammaDataShow un\.gammaDataLeave/);
  assert.doesNotMatch(page, /NSD_Check|NSD_SetState/);
  assert.match(page, /\$\{NSD_GetState\} \$gammaDeleteDataBox \$gammaDeleteData/);
  // Every deletion of a folder sits under the tick.
  const lines = code(macroText('customUnInstall'));
  const tick = lines.indexOf('${if} $gammaDeleteData = 1');
  assert.ok(tick > 0);
  const rmdirs = lines.map((l, i) => [l, i]).filter(([l]) => l.startsWith('RMDir'));
  assert.ok(rmdirs.length > 0);
  for (const [, i] of rmdirs) assert.ok(i > tick, 'a folder deleted outside the tick');
  assert.doesNotMatch(TEXT.replace(macroText('customUnInstall'), ''), /RMDir|Delete /, 'no deletion anywhere else');
});

test('the data deleted is Gamma\'s own: Electron\'s userData and the updater\'s cache, of the person uninstalling', () => {
  const lines = code(macroText('customUnInstall'));
  const tick = lines.indexOf('${if} $gammaDeleteData = 1');
  const block = lines.slice(tick + 1, lines.indexOf('${endIf}', tick));
  assert.strictEqual(block[0], 'SetShellVarContext current', 'per user, whatever the install mode');
  assert.deepStrictEqual(block.filter((l) => l.startsWith('RMDir')), [
    'RMDir /r "$APPDATA\\${APP_PACKAGE_NAME}"',
    'RMDir /r "$LOCALAPPDATA\\${APP_PACKAGE_NAME}-updater"',
  ]);
  // The context goes back for an install for all users, as electron-builder's own deletion does.
  assert.ok(lines.slice(tick).join('\n').includes('${if} $installMode == "all"\nSetShellVarContext all'));
  // APP_PACKAGE_NAME is package.json's name, which Electron names userData
  // after and electron-updater its cache (`<name>-updater`); a productName
  // there would move userData away from what this deletes.
  assert.ok(!('productName' in require('../package.json')));
  assert.ok(TEXT.includes('(%APPDATA%\\${APP_PACKAGE_NAME})'), 'the page names the folder it deletes');
});
