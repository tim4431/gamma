// The Store package's "Open with Gamma" DLL (native/open-with/), built and
// driven as Explorer drives it: invoke.exe loads it, invokes the verb on a
// selection of folders, and the record.exe built as Gamma.exe beside it
// writes down what it was started with. Windows only, with a C++ compiler
// (scripts/build-open-with.cjs: MSVC or MinGW-w64); skipped elsewhere. That
// Explorer shows the verb and starts the app as the package is checked by
// hand on an installed package (docs/checklist.md 4.12).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const { buildOpenWith, toolchain } = require('../scripts/build-open-with.cjs');
const { OPEN_WITH } = require('../scripts/openwith.cjs');

const skip = process.platform !== 'win32' ? 'Windows only' : !toolchain() ? 'no C++ compiler' : false;

function lines(text) {
  return Object.fromEntries(text.trim().split(/\r?\n/).map((l) => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]));
}

async function record(file) {
  for (let i = 0; i < 50; i++) {
    if (fs.existsSync(file)) {
      const text = fs.readFileSync(file, 'utf8');
      if (text.endsWith('end\n')) return text.split('\n').slice(0, -2);
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`${file} never written`);
}

test('the verb names itself from the table and starts Gamma.exe with each folder as an argument', { skip }, async () => {
  const { test: dir } = buildOpenWith({ test: true });
  // Long names, as the shell gives them (a temp directory can be 8.3's).
  const tmp = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-open-with-')));
  try {
    const spaced = path.join(tmp, 'a folder');
    const named = path.join(tmp, '论文 é');
    fs.mkdirSync(spaced);
    fs.mkdirSync(named);
    // A drive's root as Explorer names it, D:\: its closing backslash must
    // not escape the quote (the registry verb's "%V" gave D:").
    const root = path.parse(tmp).root;
    const out = path.join(dir, 'record.txt');
    fs.rmSync(out, { force: true });

    const shown = lines(execFileSync(path.join(dir, 'invoke.exe'), [path.join(dir, OPEN_WITH.dll), spaced, named, root], { encoding: 'utf8' }));
    assert.strictEqual(shown.title, 'Open with &Gamma');
    assert.strictEqual(shown.icon, `${path.join(dir, OPEN_WITH.exe)},0`);
    assert.strictEqual(shown.name, 'clsid');
    assert.strictEqual(shown.state, 'enabled');
    assert.strictEqual(shown.invoke, '0x00000000');
    assert.strictEqual(shown.unload, 'yes', 'every object released');

    // Outside a package the app beside the DLL is started, with no identity.
    const got = await record(out);
    assert.strictEqual(got[0], 'package=-');
    const args = got.slice(1).map((l) => l.replace(/^arg=/, ''));
    assert.strictEqual(args.length, 3);
    // The shell names a path its own way (a directory's case as on disk).
    assert.deepStrictEqual(args.map((a) => a.toLowerCase()), [spaced, named, root].map((a) => a.toLowerCase()));
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
