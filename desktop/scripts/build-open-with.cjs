// Builds the Store package's folder verb (native/open-with/, an
// IExplorerCommand DLL; docs/architecture.md "Opening files and folders")
// into native/build/GammaOpenWith.dll, and with `test` its harness into
// native/build/test/: a copy of the DLL, invoke.exe, which drives it as
// Explorer does, and record.cpp built as the Gamma.exe it starts (read by
// test/openWithDll.test.js). x64, with MSVC when Visual Studio's C++ tools
// are installed (the release workflow's Windows runner), else MinGW-w64's g++
// on PATH (MSYS2's ucrt64). scripts/after-pack.cjs calls it for the Store
// package; by hand: `node scripts/build-open-with.cjs [--test]`.
//
// Build time only: scripts/ and native/ are not in the app's files.

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const { OPEN_WITH, DESKTOP } = require('./openwith.cjs');

const SRC = path.join(DESKTOP, 'native', 'open-with');
const OUT = path.join(DESKTOP, 'native', 'build');

function vcvars() {
  const base = process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)';
  const vswhere = path.join(base, 'Microsoft Visual Studio', 'Installer', 'vswhere.exe');
  if (!fs.existsSync(vswhere)) return null;
  const root = execFileSync(vswhere, [
    '-latest', '-products', '*', '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
    '-property', 'installationPath',
  ], { encoding: 'utf8' }).trim().split(/\r?\n/)[0];
  const bat = root && path.join(root, 'VC', 'Auxiliary', 'Build', 'vcvars64.bat');
  return bat && fs.existsSync(bat) ? bat : null;
}

function hasGxx() {
  try {
    execFileSync('g++', ['--version'], { stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
}

/** The compiler at hand: { kind: 'msvc', vcvars } or { kind: 'mingw' }; null without one or off Windows. */
function toolchain() {
  if (process.platform !== 'win32') return null;
  const bat = vcvars();
  if (bat) return { kind: 'msvc', vcvars: bat };
  return hasGxx() ? { kind: 'mingw' } : null;
}

const quote = (s) => `"${s}"`;

// One program or DLL from one source file. `dll` takes the .def's exports;
// `gui` makes a program with no console (record.exe, started as the app).
function compile(tools, { src, out, libs, dll = false, gui = false }) {
  fs.mkdirSync(path.dirname(out), { recursive: true });
  if (tools.kind === 'msvc') {
    // A file, not a directory: "…\" would escape its closing quote.
    const obj = path.join(OUT, 'obj', `${path.parse(src).name}.obj`);
    fs.mkdirSync(path.dirname(obj), { recursive: true });
    const args = [
      'cl', '/nologo', '/O2', '/MT', '/EHsc', '/W4', '/std:c++17', '/DUNICODE', '/D_UNICODE',
      ...(dll ? ['/LD'] : []), `/Fo${quote(obj)}`, quote(src),
      '/link', `/OUT:${quote(out)}`,
      ...(dll ? [`/DEF:${quote(path.join(SRC, 'open_with.def'))}`] : []),
      ...(gui ? ['/SUBSYSTEM:WINDOWS'] : []),
      ...libs.map((l) => `${l}.lib`),
    ];
    // cmd /s /c "…": the outer quotes go, the inner ones stay.
    const line = `"call ${quote(tools.vcvars)} >nul && ${args.join(' ')}"`;
    execFileSync('cmd.exe', ['/d', '/s', '/c', line], { stdio: 'inherit', windowsVerbatimArguments: true });
  } else {
    execFileSync('g++', [
      '-O2', '-std=c++17', '-Wall', '-DUNICODE', '-D_UNICODE', '-static', '-static-libgcc', '-static-libstdc++', '-s',
      ...(dll ? ['-shared'] : []), ...(gui ? ['-mwindows'] : []),
      '-o', out, src, ...(dll ? [path.join(SRC, 'open_with.def')] : []),
      ...libs.map((l) => `-l${l}`),
    ], { stdio: 'inherit' });
  }
  if (!fs.existsSync(out)) throw new Error(`The build wrote no ${out}`);
}

/**
 * Builds the DLL (and with `test` the harness); returns { dll, test? }, the
 * DLL's path and the harness's directory. Throws without a compiler.
 */
function buildOpenWith({ test = false } = {}) {
  const tools = toolchain();
  if (!tools) {
    throw new Error('The Store package\'s "Open with Gamma" is a DLL built here on Windows, and no C++ compiler was found: '
      + 'install Visual Studio\'s C++ build tools, or MSYS2 with mingw-w64-ucrt-x86_64-gcc on PATH.');
  }
  const dll = path.join(OUT, OPEN_WITH.dll);
  compile(tools, { src: path.join(SRC, 'open_with.cpp'), out: dll, libs: ['ole32', 'user32'], dll: true });
  if (!test) return { dll };
  const dir = path.join(OUT, 'test');
  fs.mkdirSync(dir, { recursive: true });
  fs.copyFileSync(dll, path.join(dir, OPEN_WITH.dll));
  compile(tools, { src: path.join(SRC, 'test', 'invoke.cpp'), out: path.join(dir, 'invoke.exe'), libs: ['ole32', 'shell32'] });
  compile(tools, { src: path.join(SRC, 'test', 'record.cpp'), out: path.join(dir, OPEN_WITH.exe), libs: ['shell32'], gui: true });
  return { dll, test: dir };
}

module.exports = { buildOpenWith, toolchain };

if (require.main === module) {
  const built = buildOpenWith({ test: process.argv.includes('--test') });
  console.log(`built ${path.relative(DESKTOP, built.dll)} (${toolchain().kind})`);
  if (built.test) console.log(`harness in ${path.relative(DESKTOP, built.test)}`);
}
