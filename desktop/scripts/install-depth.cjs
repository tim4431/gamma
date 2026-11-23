// Whether the packed app fits beneath the NSIS installer's longest install
// folder (assets/installer.nsh GAMMA_INSTDIR_MAX). Windows reaches no path
// longer than 259 characters while long paths are off, so the folder, a
// separator and the app's deepest file have to fit in that. afterPack runs
// the check before the installer is made, and the build fails when the app
// has outgrown the limit: shorten what is deep, or lower the limit, which
// stays above any folder a person is likely to pick (the default,
// %LOCALAPPDATA%\Programs\Gamma, is about 45 characters).

const fs = require('fs');
const path = require('path');

const PATH_MAX = 259; // MAX_PATH, 260, less the terminating NUL
const NSH = path.join(__dirname, '..', 'assets', 'installer.nsh');

function instdirMax(text = fs.readFileSync(NSH, 'utf8')) {
  const m = /^!define GAMMA_INSTDIR_MAX (\d+)$/m.exec(text);
  if (!m) throw new Error(`no GAMMA_INSTDIR_MAX in ${NSH}`);
  return Number(m[1]);
}

// The longest path of a file under `root`, relative to it.
function deepestFile(root) {
  let deepest = '';
  for (const entry of fs.readdirSync(root, { recursive: true, withFileTypes: true })) {
    if (!entry.isFile()) continue;
    const rel = path.relative(root, path.join(entry.parentPath, entry.name));
    if (rel.length > deepest.length) deepest = rel;
  }
  return deepest;
}

// → the deepest file and the room a `max`-character folder leaves it; throws
// when the file does not fit.
function checkInstallDepth(appOutDir, max = instdirMax()) {
  const deepest = deepestFile(appOutDir);
  const room = PATH_MAX - max - 1;
  if (deepest.length > room) {
    throw new Error(`${deepest} is ${deepest.length} characters deep, past the ${room} that a ${max}-character install folder leaves under Windows' path limit (GAMMA_INSTDIR_MAX in assets/installer.nsh)`);
  }
  return { deepest, room };
}

module.exports = { PATH_MAX, instdirMax, deepestFile, checkInstallDepth };
