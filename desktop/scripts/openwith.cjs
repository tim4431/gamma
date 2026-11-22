// What the desktop app registers with the operating system, written from
// the one table, tests/shared/openwith.json (docs/architecture.md "Opening
// files and folders"). Read by electron-builder.cjs (macOS's file types and
// folder type, the .deb's folder entries) and by test/openWith.test.js; run
// as a script (`npm run openwith`) it writes the six generated files:
//
//   assets/openwith.nsh              the NSIS installer's file types and folder verbs, and its page that offers them
//                                    (macros assets/installer.nsh, electron-builder's include, puts in its hooks)
//   lib/openwith.json                the same entries for the launcher's switches (lib/openWith.js)
//   assets/appx-extensions.xml       the Store package's file types and folder verb
//   native/open-with/verb.h          what the Store package's folder verb shares with it
//   assets/linux/gamma-open.desktop  Dolphin's service menu (the .deb)
//   assets/linux/gamma.nemo_action   Nemo's action (the .deb)
//
// Build time only: scripts/ is not in the app's files (lib/openwith.json is).

const fs = require('fs');
const path = require('path');

const DESKTOP = path.resolve(__dirname, '..');
const FIXTURE = path.resolve(DESKTOP, '..', 'tests', 'shared', 'openwith.json');

const HEADER = 'Written by scripts/openwith.cjs from tests/shared/openwith.json: edit those, then `npm run openwith`.';

// Extensions an earlier version registered and the table no longer holds,
// with the ProgID they were listed under: { ext: '.xyz', progid: 'Gamma.xyz' }.
// The install of the version that drops one deletes its entries, so an
// update leaves nothing behind. Empty: nothing has been dropped yet.
const dropped = [];

const FILES = {
  nsis: 'assets/openwith.nsh',
  runtime: 'lib/openwith.json',
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
// The launcher's switch hides the verb (lib/openWith.js): while the file
// `off` is in the app's data directory, %APPDATA%\<data>, the DLL's
// GetState says hidden. Electron names that directory after package.json's
// `name` (electron-builder adds no productName to the app's package.json).
const OPEN_WITH = {
  clsid: 'a5ac2231-021b-477c-846c-b5a2a3ef67c2',
  dll: 'GammaOpenWith.dll',
  verbId: 'OpenWithGamma',
  app: 'Gamma',
  exe: 'Gamma.exe',
  data: require('../package.json').name,
  off: 'folder-verb-off',
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

/** The table's kinds in words: "PDF, Markdown, LaTeX, BibTeX and Typst". */
function typesPhrase() {
  const words = table().map((k) => k.name.split(' ')[0]);
  return words.length > 1 ? `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}` : words.join('');
}

// ---------------------------------------------------------------- Windows registry

// The two parts of the installer's registration a person may leave out, at
// install on its page or later with the launcher's switches: `files`,
// Gamma in Open with and Default apps for each type of the table, and
// `folders`, "Open with Gamma" on a folder and on an open folder's
// background (on Windows 11 under "Show more options"; not on a drive, a
// drive root is refused, and not on Folder, which also matches Control
// Panel, Libraries and zip folders). Each is its writes and the removals
// that undo them, keys under SHELL_CONTEXT (HKCU for a per-user install,
// HKLM for one for all users), and the value under APP_KEY that records
// whether this install has it. In keys, names and data ${APP_ID} and
// ${PRODUCT_NAME} are electron-builder's NSIS defines (the config's appId
// and productName) and $appExe the installed Gamma.exe; lib/openWith.js
// puts in the same. A write is { key, name, type: 'sz' | 'none', data },
// name '' a key's default value; a removal { op, key, name }, op 'value',
// 'key' (the whole tree) or 'ifEmpty' (the key once it holds no value, its
// default included, and no subkey: an extension's key may hold another
// app's entries, and NSIS's DeleteRegKey /ifempty looks at subkeys alone).
// Never an extension's default value: the user chooses Gamma.

const CLASSES = 'Software\\Classes';
const APP_KEY = 'Software\\${APP_ID}';
const CAPABILITIES = `${APP_KEY}\\Capabilities`;
const VERB_KEYS = [`${CLASSES}\\Directory\\shell\\Gamma`, `${CLASSES}\\Directory\\Background\\shell\\Gamma`];
const EXE = '$appExe';
const DESCRIPTION = 'Annotate PDFs and keep notes beside them.';

const sz = (key, name, data) => ({ key, name, type: 'sz', data });

// The value, then both keys once empty: the installer made the extension's
// key where it had none (.typ, .bib, .markdown under HKCU), and another
// reader's entries keep .pdf's.
function forgetExtension(ext, progid) {
  return [
    { op: 'value', key: `${CLASSES}\\${ext}\\OpenWithProgids`, name: progid },
    { op: 'ifEmpty', key: `${CLASSES}\\${ext}\\OpenWithProgids` },
    { op: 'ifEmpty', key: `${CLASSES}\\${ext}` },
  ];
}

/** The two parts: { files, folders }, each { value, write, remove }. */
function parts() {
  const kinds = table();
  const exts = kinds.flatMap((k) => k.exts.map((ext) => ({ ext, progid: k.progid })));
  return {
    files: {
      value: 'OpenWithFiles',
      write: [
        // A ProgID per kind: its name, its icon, how it opens a file.
        ...kinds.flatMap((k) => [
          sz(`${CLASSES}\\${k.progid}`, '', k.name),
          sz(`${CLASSES}\\${k.progid}\\DefaultIcon`, '', `${EXE},0`),
          sz(`${CLASSES}\\${k.progid}\\shell\\open\\command`, '', `"${EXE}" "%1"`),
        ]),
        // Open with and Default apps per extension.
        sz(CAPABILITIES, 'ApplicationName', '${PRODUCT_NAME}'),
        sz(CAPABILITIES, 'ApplicationDescription', DESCRIPTION),
        ...exts.flatMap(({ ext, progid }) => [
          { key: `${CLASSES}\\${ext}\\OpenWithProgids`, name: progid, type: 'none' },
          sz(`${CAPABILITIES}\\FileAssociations`, ext, progid),
        ]),
        sz('Software\\RegisteredApplications', '${PRODUCT_NAME}', CAPABILITIES),
      ],
      remove: [
        ...exts.flatMap(({ ext, progid }) => forgetExtension(ext, progid)),
        ...kinds.map((k) => ({ op: 'key', key: `${CLASSES}\\${k.progid}` })),
        { op: 'value', key: 'Software\\RegisteredApplications', name: '${PRODUCT_NAME}' },
        { op: 'key', key: CAPABILITIES },
      ],
    },
    folders: {
      value: 'OpenWithFolders',
      write: VERB_KEYS.flatMap((key) => [
        sz(key, '', folderVerb()),
        sz(key, 'Icon', `${EXE},0`),
        sz(`${key}\\command`, '', `"${EXE}" "%V"`),
      ]),
      remove: VERB_KEYS.map((key) => ({ op: 'key', key })),
    },
  };
}

// The installer's page and the launcher's rows, in the same words.
function words() {
  const verb = folderVerb().replace(/&/g, '');
  return {
    title: 'File Explorer',
    subtitle: 'Choose where Gamma appears in File Explorer.',
    files: `List Gamma under Open with for ${typesPhrase()} files`,
    folders: `Add ${verb} to the right-click menu of folders`,
    note: 'Gamma never makes itself a file type\'s default app. Both can be changed later in Gamma\'s launcher, under Settings.',
  };
}

// ---------------------------------------------------------------- NSIS

// One NSIS string argument. NSIS takes ", ' or ` as the delimiter, so a
// value holding double quotes ('"$appExe" "%1"') is written in single ones.
// `$` starts a variable, which every value here means where it has one.
function q(s) {
  for (const d of ['"', "'", '`']) if (!s.includes(d)) return d + s + d;
  throw new Error(`No NSIS quote fits ${s}`);
}

const NOTIFY = 'System::Call "shell32::SHChangeNotify(i,i,i,i) (0x08000000, 0x1000, 0, 0)"';
const reg = (op, key, ...rest) => `${op} SHELL_CONTEXT ${[key, ...rest].map(q).join(' ')}`;

/** One write or removal of parts() as an NSIS line. */
function nsisLine(e) {
  if (e.type === 'sz') return reg('WriteRegStr', e.key, e.name, e.data);
  if (e.type === 'none') return reg('WriteRegNone', e.key, e.name);
  if (e.op === 'value') return reg('DeleteRegValue', e.key, e.name);
  if (e.op === 'key') return `DeleteRegKey SHELL_CONTEXT ${q(e.key)}`;
  if (e.op === 'ifEmpty') return `!insertmacro gammaDeleteKeyIfEmpty ${q(e.key)}`;
  throw new Error(`Not a registry entry: ${JSON.stringify(e)}`);
}

const ind = (lines, n = 2) => lines.map((l) => (l ? ' '.repeat(n) + l : l));

/** The NSIS script's text (assets/openwith.nsh); `gone` stands in for `dropped` in tests. */
function nsis(gone = dropped) {
  const { files, folders } = parts();
  const say = words();
  // A part: written when its variable is 1, else removed.
  const part = (comment, v, p) => [
    `; ${comment}`,
    `\${if} ${v} = 1`,
    ...ind(p.write.map(nsisLine)),
    '${else}',
    ...ind(p.remove.map(nsisLine)),
    '${endIf}',
  ];
  const record = (v, p) => `WriteRegDWORD SHELL_CONTEXT ${q(APP_KEY)} ${q(p.value)} ${v}`;
  const read = (v, p) => [
    'ClearErrors',
    `ReadRegDWORD ${v} SHELL_CONTEXT ${q(APP_KEY)} ${q(p.value)}`,
    '${if} ${Errors}',
    `  StrCpy ${v} 1`,
    '${endIf}',
  ];

  const page = [
    '!include nsDialogs.nsh',
    'Var gammaChosen',
    'Var gammaFiles',
    'Var gammaFolders',
    'Var gammaFilesBox',
    'Var gammaFoldersBox',
    '',
    'Page custom gammaOpenWithShow gammaOpenWithLeave',
    '',
    '; The parts as this install\'s last one recorded them, else on.',
    'Function gammaReadChoice',
    ...ind([...read('$gammaFiles', files), ...read('$gammaFolders', folders), 'ClearErrors']),
    'FunctionEnd',
    '',
    'Function gammaOpenWithShow',
    ...ind([
      '${if} ${isUpdated}',
      '  Abort',
      '${endIf}',
      'Call gammaReadChoice',
      `!insertmacro MUI_HEADER_TEXT ${q(say.title)} ${q(say.subtitle)}`,
      'nsDialogs::Create 1018',
      'Pop $0',
      `\${NSD_CreateCheckbox} 0 0 100% 14u ${q(say.files)}`,
      'Pop $gammaFilesBox',
      '${if} $gammaFiles = 1',
      '  ${NSD_Check} $gammaFilesBox',
      '${endIf}',
      `\${NSD_CreateCheckbox} 0 20u 100% 14u ${q(say.folders)}`,
      'Pop $gammaFoldersBox',
      '${if} $gammaFolders = 1',
      '  ${NSD_Check} $gammaFoldersBox',
      '${endIf}',
      `\${NSD_CreateLabel} 0 48u 100% 24u ${q(say.note)}`,
      'Pop $0',
      'nsDialogs::Show',
    ]),
    'FunctionEnd',
    '',
    'Function gammaOpenWithLeave',
    ...ind([
      '${NSD_GetState} $gammaFilesBox $gammaFiles',
      '${NSD_GetState} $gammaFoldersBox $gammaFolders',
      'StrCpy $gammaChosen 1',
    ]),
    'FunctionEnd',
  ];

  const install = [
    '; The parts: as ticked on the page, else (an update, a silent install) as recorded, else on.',
    '${if} $gammaChosen != 1',
    '  Call gammaReadChoice',
    '${endIf}',
    record('$gammaFiles', files),
    record('$gammaFolders', folders),
    ...part('Gamma in Open with and Default apps: a ProgID per kind, each extension\'s Open with, the capabilities.', '$gammaFiles', files),
    ...part('Open with Gamma on a folder and on the background of an open one.', '$gammaFolders', folders),
    ...(gone.length
      ? ['; Extensions an earlier version registered: gone with this update.',
        ...gone.flatMap(({ ext, progid }) => [
          ...forgetExtension(ext, progid),
          { op: 'value', key: `${CAPABILITIES}\\FileAssociations`, name: ext },
        ]).map(nsisLine)]
      : []),
    '; SHCNE_ASSOCCHANGED, SHCNF_FLUSH: Explorer rereads the associations',
    NOTIFY,
  ];

  const uninstall = [
    ...[...files.remove, ...folders.remove].map(nsisLine),
    `DeleteRegKey SHELL_CONTEXT ${q(APP_KEY)}`,
    NOTIFY,
  ];

  return [
    `; ${HEADER}`,
    ';',
    '; The NSIS installer\'s registration, macros that assets/installer.nsh',
    '; (electron-builder.cjs, nsis.include) puts in electron-builder\'s hooks,',
    '; in the two parts of scripts/openwith.cjs parts(): Gamma in Explorer\'s',
    '; "Open with" and Settings -> Default apps for each file type of the',
    '; table, never a type\'s handler by itself - the user chooses it - and',
    '; "Open with Gamma" on a folder and on the background of an open one. The',
    '; page (gammaOpenWithPage, after the directory page) offers both, ticked',
    '; as this install\'s last one recorded them (Software\\${APP_ID}:',
    '; OpenWithFiles, OpenWithFolders), both on for a first install.',
    '; gammaOpenWithInstall writes a part that is on and removes one that is',
    '; off, then records them, so an update (silent, no page) and the',
    '; launcher\'s switches (lib/openWith.js, the same entries) keep what the',
    '; person chose. SHELL_CONTEXT is HKCU for a per-user install, HKLM for one',
    '; for all users. An update rewrites the keys of a part that is on, so',
    '; Gamma stays the default where the user made it so;',
    '; gammaOpenWithUnInstall removes exactly what the install writes. An',
    '; extension dropped from the table goes in `dropped` of the generator: the',
    '; install of the version that drops it deletes its value and then both',
    '; keys once empty, so an update leaves nothing behind.',
    '',
    '; A key deleted only once it holds no value, its default included, and no',
    '; subkey: DeleteRegKey /ifempty looks at subkeys alone, and an extension\'s',
    '; key or its OpenWithProgids may hold another app\'s entries.',
    '!macro gammaDeleteKeyIfEmpty KEY',
    ...ind([
      'Push $R8',
      'Push $R9',
      'ClearErrors',
      'EnumRegValue $R8 SHELL_CONTEXT "${KEY}" 0',
      '${if} ${Errors}',
      '  EnumRegKey $R9 SHELL_CONTEXT "${KEY}" 0',
      '  ${if} $R9 == ""',
      '    DeleteRegKey SHELL_CONTEXT "${KEY}"',
      '  ${endIf}',
      '${endIf}',
      'ClearErrors',
      'Pop $R9',
      'Pop $R8',
    ]),
    '!macroend',
    '',
    '; The page, put in after the directory page, where MUI2, LogicLib and',
    '; electron-builder\'s own macros are defined. nsis.oneClick stays false: a',
    '; one-click installer has no pages, and gammaOpenWithInstall reads these',
    '; variables. An update (--updated, run silently) skips it.',
    '!macro gammaOpenWithPage',
    ...ind(page),
    '!macroend',
    '',
    '!macro gammaOpenWithInstall',
    ...ind(install),
    '!macroend',
    '',
    '; Not on an update: the hook runs it only for an uninstall.',
    '!macro gammaOpenWithUnInstall',
    ...ind(uninstall),
    '!macroend',
    '',
  ].join('\n');
}

// ---------------------------------------------------------------- the launcher's switches

// electron-builder's NSIS defines, as the installer has them: the config's
// appId and productName. Read when called: electron-builder.cjs requires
// this file.
function installerDefines() {
  const config = require('../electron-builder.cjs');
  return { '${APP_ID}': config.appId, '${PRODUCT_NAME}': config.productName };
}

/**
 * lib/openwith.json: parts() and the words, for lib/openWith.js, with the
 * installer's defines put in (the app's own name is package.json's, not
 * productName); $appExe stays, the app's path. An entry a line.
 */
function runtime() {
  const defines = installerDefines();
  const fill = (s) => (typeof s === 'string' ? s.replace(/\$\{(APP_ID|PRODUCT_NAME)\}/g, (d) => defines[d]) : s);
  const filled = (entries) => entries.map((e) => Object.fromEntries(Object.entries(e).map(([k, v]) => [k, fill(v)])));
  const p = parts();
  const text = JSON.stringify({
    about: `${HEADER} The installer's registration in its two parts (scripts/openwith.cjs parts()), which the launcher's switches write and remove as the installer does (lib/openWith.js), and the file that hides the Store package's folder verb.`,
    types: typesPhrase(),
    verb: folderVerb().replace(/&/g, ''),
    record: fill(APP_KEY),
    parts: Object.fromEntries(Object.entries(p).map(([name, { value, write, remove }]) => [name, { value, write: filled(write), remove: filled(remove) }])),
    storeOff: OPEN_WITH.off,
  }, null, 2);
  return `${text.replace(/\{\n\s+([^{}[\]]*?)\n\s+\}/g, (_, body) => `{ ${body.replace(/,\n\s+/g, ', ')} }`)}\n`;
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
    '// The launcher\'s switch (lib/openWith.js): while this file is in the app\'s',
    '// data directory, %APPDATA%\\<GAMMA_DATA_DIR> (the package\'s copy of it), the',
    '// verb is hidden.',
    `#define GAMMA_DATA_DIR ${cString(OPEN_WITH.data)}`,
    `#define GAMMA_FOLDER_VERB_OFF ${cString(OPEN_WITH.off)}`,
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

/** The six generated files' text, by their path under desktop/. */
function generated() {
  return {
    [FILES.nsis]: nsis(),
    [FILES.runtime]: runtime(),
    [FILES.appx]: appx(),
    [FILES.verb]: verbHeader(),
    [FILES.kde]: kdeMenu(),
    [FILES.nemo]: nemoAction(),
  };
}

module.exports = {
  table, parts, words, typesPhrase, installerDefines, nsis, runtime, appx, verbHeader, kdeMenu, nemoAction, debFpm, macFileAssociations,
  MAC_FOLDER_TYPE, dropped, macEntry, generated, guidInit, FILES, DESKTOP, OPEN_WITH,
};

if (require.main === module) {
  for (const [rel, text] of Object.entries(generated())) {
    const file = path.join(DESKTOP, rel);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, text);
    console.log(`wrote ${rel}`);
  }
}
