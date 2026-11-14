// What the desktop app registers with the operating system, written from
// the one table, tests/shared/openwith.json (docs/architecture.md "Opening
// files and folders"). Read by electron-builder.cjs (macOS's file types and
// folder type, the .deb's folder entries) and by test/openWith.test.js; run
// as a script (`npm run openwith`) it writes the five generated files:
//
//   assets/installer.nsh             the NSIS installer's file types and folder verbs
//   assets/appx-extensions.xml       the Store package's file types and folder verb
//   native/open-with/verb.h          what the Store package's folder verb shares with it
//   assets/linux/gamma-open.desktop  Dolphin's service menu (the .deb)
//   assets/linux/gamma.nemo_action   Nemo's action (the .deb)
//
// Build time only: scripts/ is not in the app's files.

const fs = require('fs');
const path = require('path');

const DESKTOP = path.resolve(__dirname, '..');
const FIXTURE = path.resolve(DESKTOP, '..', 'tests', 'shared', 'openwith.json');

const HEADER = 'Written by scripts/openwith.cjs from tests/shared/openwith.json: edit those, then `npm run openwith`.';

// Extensions an earlier version registered and the table no longer holds,
// with the ProgID they were listed under: { ext: '.xyz', progid: 'Gamma.xyz' }.
// customInstall of the version that drops one deletes its entries, so an
// update leaves nothing behind. Empty: nothing has been dropped yet.
const dropped = [];

const FILES = {
  nsis: 'assets/installer.nsh',
  appx: 'assets/appx-extensions.xml',
  verb: 'native/open-with/verb.h',
  kde: 'assets/linux/gamma-open.desktop',
  nemo: 'assets/linux/gamma.nemo_action',
};

// The Store package's "Open with Gamma": a packaged app reaches Explorer's
// context menu only through a COM class it declares, so the package carries
// one (native/open-with/, an IExplorerCommand built by
// scripts/build-open-with.cjs) as a surrogate server and names it as the verb
// of a folder and of an open folder's background. The class id is where the
// manifest and the DLL meet; `app` and `exe` are electron-builder.cjs's
// appx.applicationId and productName's executable, which the DLL activates.
const OPEN_WITH = {
  clsid: 'a5ac2231-021b-477c-846c-b5a2a3ef67c2',
  dll: 'GammaOpenWith.dll',
  verbId: 'OpenWithGamma',
  app: 'Gamma',
  exe: 'Gamma.exe',
};

function fixture() {
  return JSON.parse(fs.readFileSync(FIXTURE, 'utf8'));
}

/** The fixture's kinds: { kind, format, languages, exts, left_out, progid, name }. */
function table() {
  return fixture().kinds;
}

function folderVerb() {
  return fixture().folder_verb;
}

// ---------------------------------------------------------------- NSIS

// One NSIS string argument. NSIS takes ", ' or ` as the delimiter, so a
// value holding double quotes ('"$appExe" "%1"') is written in single ones.
// `$` starts a variable, which every value here means where it has one.
function q(s) {
  for (const d of ['"', "'", '`']) if (!s.includes(d)) return d + s + d;
  throw new Error(`No NSIS quote fits ${s}`);
}

const CLASSES = 'Software\\Classes';
const CAPABILITIES = 'Software\\${APP_ID}\\Capabilities';
const VERB_KEYS = [`${CLASSES}\\Directory\\shell\\Gamma`, `${CLASSES}\\Directory\\Background\\shell\\Gamma`];
const NOTIFY = 'System::Call "shell32::SHChangeNotify(i,i,i,i) (0x08000000, 0x1000, 0, 0)"';

const reg = (op, key, ...rest) => `${op} SHELL_CONTEXT ${[key, ...rest].map(q).join(' ')}`;
const regKey = (flag, key) => `DeleteRegKey ${flag ? flag + ' ' : ''}SHELL_CONTEXT ${q(key)}`;

// The value, then both keys only when empty: the installer made the
// extension's key where it had none (.typ, .bib, .markdown under HKCU),
// and /ifempty leaves one that holds anything else (.pdf's).
function forgetExtension(ext, progid) {
  return [
    reg('DeleteRegValue', `${CLASSES}\\${ext}\\OpenWithProgids`, progid),
    regKey('/ifempty', `${CLASSES}\\${ext}\\OpenWithProgids`),
    regKey('/ifempty', `${CLASSES}\\${ext}`),
  ];
}

/** The NSIS script's text (assets/installer.nsh); `gone` stands in for `dropped` in tests. */
function nsis(gone = dropped) {
  const kinds = table();
  const verb = folderVerb();
  const exts = kinds.flatMap((k) => k.exts.map((ext) => ({ ext, progid: k.progid })));
  const ind = (lines, n = 2) => lines.map((l) => ' '.repeat(n) + l);

  const install = [
    '; A ProgID per kind: its name, its icon, how it opens a file.',
    ...kinds.flatMap((k) => [
      reg('WriteRegStr', `${CLASSES}\\${k.progid}`, '', k.name),
      reg('WriteRegStr', `${CLASSES}\\${k.progid}\\DefaultIcon`, '', '$appExe,0'),
      reg('WriteRegStr', `${CLASSES}\\${k.progid}\\shell\\open\\command`, '', '"$appExe" "%1"'),
    ]),
    '; Open with and Default apps per extension; never the extension\'s own default.',
    reg('WriteRegStr', CAPABILITIES, 'ApplicationName', '${PRODUCT_NAME}'),
    reg('WriteRegStr', CAPABILITIES, 'ApplicationDescription', 'Annotate PDFs and keep notes beside them.'),
    ...exts.flatMap(({ ext, progid }) => [
      reg('WriteRegNone', `${CLASSES}\\${ext}\\OpenWithProgids`, progid),
      reg('WriteRegStr', `${CAPABILITIES}\\FileAssociations`, ext, progid),
    ]),
    reg('WriteRegStr', 'Software\\RegisteredApplications', '${PRODUCT_NAME}', CAPABILITIES),
    '; The folder verb, on a folder and on the background of an open one.',
    ...VERB_KEYS.flatMap((key) => [
      reg('WriteRegStr', key, '', verb),
      reg('WriteRegStr', key, 'Icon', '$appExe,0'),
      reg('WriteRegStr', `${key}\\command`, '', '"$appExe" "%V"'),
    ]),
    ...(gone.length
      ? ['; Extensions an earlier version registered: gone with this update.',
        ...gone.flatMap(({ ext, progid }) => [
          ...forgetExtension(ext, progid),
          reg('DeleteRegValue', `${CAPABILITIES}\\FileAssociations`, ext),
        ])]
      : []),
    '; SHCNE_ASSOCCHANGED, SHCNF_FLUSH: Explorer rereads the associations',
    NOTIFY,
  ];

  const uninstall = [
    ...exts.flatMap(({ ext, progid }) => forgetExtension(ext, progid)),
    ...kinds.map((k) => regKey('', `${CLASSES}\\${k.progid}`)),
    ...VERB_KEYS.map((key) => regKey('', key)),
    reg('DeleteRegValue', 'Software\\RegisteredApplications', '${PRODUCT_NAME}'),
    regKey('', 'Software\\${APP_ID}'),
    NOTIFY,
  ];

  return [
    `; ${HEADER}`,
    ';',
    '; The NSIS installer\'s registration (electron-builder.cjs, nsis.include):',
    '; Gamma in Explorer\'s "Open with" and Settings -> Default apps for each',
    '; file type of the table, never a type\'s handler by itself - the user',
    '; chooses it - and "Open with Gamma" on a folder and on the background of',
    '; an open one (on Windows 11 under "Show more options"; not on a drive, a',
    '; drive root is refused, and not on Folder, which also matches Control',
    '; Panel, Libraries and zip folders). SHELL_CONTEXT is HKCU for a per-user',
    '; install, HKLM for one for all users. An update keeps every key and',
    '; customInstall rewrites them, so Gamma stays the default where the user',
    '; made it so; an uninstall removes exactly what customInstall writes.',
    '; An extension dropped from the table goes in `dropped` of the generator:',
    '; customInstall of the version that drops it deletes its value and then',
    '; both keys /ifempty, so an update leaves nothing behind.',
    '',
    '!macro customInstall',
    ...ind(install),
    '!macroend',
    '',
    '!macro customUnInstall',
    '  ${ifNot} ${isUpdated}',
    ...ind(uninstall, 4),
    '  ${endIf}',
    '!macroend',
    '',
  ].join('\n');
}

// ---------------------------------------------------------------- Store

const xml = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** The Store manifest's extensions (assets/appx-extensions.xml), appended inside <Extensions>. */
function appx() {
  const verb = (type) => [
    `            <desktop5:ItemType Type="${xml(type)}">`,
    `              <desktop5:Verb Id="${OPEN_WITH.verbId}" Clsid="${OPEN_WITH.clsid}" />`,
    '            </desktop5:ItemType>',
  ];
  return [
    `        <!-- ${HEADER}`,
    '             The Store package\'s file types (electron-builder.cjs,',
    '             appx.customExtensionsPath, appended inside <Extensions>): Gamma in',
    '             "Open with" and Default apps, the default only when the user',
    '             chooses it; the file\'s path arrives on the command line. No',
    '             ContentType: Windows types a file by its extension. -->',
    ...table().flatMap((k) => [
      '        <uap:Extension Category="windows.fileTypeAssociation">',
      `          <uap:FileTypeAssociation Name="${xml(k.kind)}">`,
      `            <uap:DisplayName>${xml(k.name)}</uap:DisplayName>`,
      '            <uap:SupportedFileTypes>',
      ...k.exts.map((ext) => `              <uap:FileType>${xml(ext)}</uap:FileType>`),
      '            </uap:SupportedFileTypes>',
      '          </uap:FileTypeAssociation>',
      '        </uap:Extension>',
    ]),
    '        <!-- "Open with Gamma" on a folder and on an open folder\'s background:',
    '             Explorer runs the COM class of native/open-with/ in a surrogate',
    '             process of this package, and the class hands the folders to the',
    '             app as its arguments. Not on a drive: a drive root is refused. The',
    '             com, desktop4 and desktop5 namespaces are declared, desktop4 and',
    '             desktop5 as ignorable, in assets/appxmanifest.xml. -->',
    '        <com:Extension Category="windows.comServer">',
    '          <com:ComServer>',
    '            <com:SurrogateServer DisplayName="Open with Gamma">',
    `              <com:Class Id="${OPEN_WITH.clsid}" Path="app\\${OPEN_WITH.dll}" ThreadingModel="STA" />`,
    '            </com:SurrogateServer>',
    '          </com:ComServer>',
    '        </com:Extension>',
    '        <desktop4:Extension Category="windows.fileExplorerContextMenus">',
    '          <desktop4:FileExplorerContextMenus>',
    ...verb('Directory'),
    ...verb('Directory\\Background'),
    '          </desktop4:FileExplorerContextMenus>',
    '        </desktop4:Extension>',
    '',
  ].join('\n');
}

// A GUID's C initializer: {0xa5ac2231, 0x021b, 0x477c, {0x84, 0x6c, …}}.
function guidInit(id) {
  const hex = id.replace(/-/g, '');
  const h = (from, to) => `0x${hex.slice(from, to)}`;
  const bytes = Array.from({ length: 8 }, (_, i) => h(16 + 2 * i, 18 + 2 * i));
  return `{${h(0, 8)}, ${h(8, 12)}, ${h(12, 16)}, {${bytes.join(', ')}}}`;
}

const cString = (s) => `L"${s.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;

/** native/open-with/verb.h: the class id, the verb's title and the app it activates. */
function verbHeader() {
  return [
    `// ${HEADER}`,
    '// What the Store package\'s folder verb (open_with.cpp) shares with the',
    '// package\'s manifest (assets/appx-extensions.xml) and electron-builder.cjs.',
    '#pragma once',
    '',
    `// {${OPEN_WITH.clsid}}: the class the manifest's surrogate server and verbs name.`,
    `static const GUID CLSID_OpenWithGamma = ${guidInit(OPEN_WITH.clsid)};`,
    '',
    '// The verb\'s words (& marks the accelerator), the package\'s application id',
    '// and its executable, beside this DLL in the package\'s app\\ directory.',
    `#define GAMMA_VERB_TITLE ${cString(folderVerb())}`,
    `#define GAMMA_APP_ID ${cString(OPEN_WITH.app)}`,
    `#define GAMMA_EXE ${cString(OPEN_WITH.exe)}`,
    '',
  ].join('\n');
}

// ---------------------------------------------------------------- Linux

/** Dolphin's service menu (KF5 and KF6): "Open with Gamma" on a folder and an open folder's background. */
function kdeMenu() {
  return [
    `# ${HEADER}`,
    '# Dolphin\'s service menu, installed by the .deb (electron-builder.cjs deb.fpm):',
    '# a plain service, not an application entry, so it is no default for anything.',
    '[Desktop Entry]',
    'Type=Service',
    'X-KDE-ServiceTypes=KonqPopupMenu/Plugin',
    'MimeType=inode/directory;',
    'Actions=openGamma;',
    '',
    '[Desktop Action openGamma]',
    `Name=${folderVerb()}`,
    'Icon=gamma',
    'Exec=gamma %F',
    '',
  ].join('\n');
}

/** Nemo's action: the same verb, without the accelerator mark Nemo would show. */
function nemoAction() {
  return [
    `# ${HEADER}`,
    '# Nemo\'s action, installed by the .deb (electron-builder.cjs deb.fpm): a',
    '# context entry on a folder and an open folder\'s background, no default.',
    '[Nemo Action]',
    `Name=${folderVerb().replace(/&/g, '')}`,
    'Exec=gamma %F',
    'Icon-Name=gamma',
    'Selection=any',
    'Extensions=dir;',
    '',
  ].join('\n');
}

/** fpm's source=destination mappings that put the two Linux entries in the .deb. */
function debFpm() {
  const kde = path.join(DESKTOP, FILES.kde);
  return [
    `${kde}=/usr/share/kio/servicemenus/gamma-open.desktop`, // KF6
    `${kde}=/usr/share/kservices5/ServiceMenus/gamma-open.desktop`, // KF5
    `${path.join(DESKTOP, FILES.nemo)}=/usr/share/nemo/actions/gamma.nemo_action`,
  ];
}

// ---------------------------------------------------------------- macOS

function macEntry(k) {
  const ext = k.exts.map((e) => e.slice(1));
  return { ext: ext.length === 1 ? ext[0] : ext, name: k.name, role: 'Viewer', rank: 'Alternate' };
}

/** mac.fileAssociations: an alternate viewer per kind, listed under Open With, the default only when chosen. */
function macFileAssociations() {
  return table().map(macEntry);
}

/** A folder dropped on the Dock icon, or `open -a Gamma <dir>`, arrives as open-file. */
const MAC_FOLDER_TYPE = {
  CFBundleTypeName: 'Folder',
  CFBundleTypeRole: 'Viewer',
  LSHandlerRank: 'Alternate',
  LSItemContentTypes: ['public.folder'],
};

/** The five generated files' text, by their path under desktop/. */
function generated() {
  return {
    [FILES.nsis]: nsis(),
    [FILES.appx]: appx(),
    [FILES.verb]: verbHeader(),
    [FILES.kde]: kdeMenu(),
    [FILES.nemo]: nemoAction(),
  };
}

module.exports = {
  table, nsis, appx, verbHeader, kdeMenu, nemoAction, debFpm, macFileAssociations, MAC_FOLDER_TYPE, dropped,
  macEntry, generated, guidInit, FILES, DESKTOP, OPEN_WITH,
};

if (require.main === module) {
  for (const [rel, text] of Object.entries(generated())) {
    const file = path.join(DESKTOP, rel);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, text);
    console.log(`wrote ${rel}`);
  }
}
