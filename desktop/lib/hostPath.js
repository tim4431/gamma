// A Microsoft Store (MSIX) install's AppData is virtualized: what the app
// creates under %APPDATA% or %LOCALAPPDATA% lands in the package's private
// copy, %LOCALAPPDATA%\Packages\<family>\LocalCache\{Roaming,Local}, and is
// merged back into the real path only for processes inside the package.
// Explorer, Notepad and every other program see the real path, where the
// file is not ("Location is not available"). hostPath() turns such a path
// into the one they can open; anywhere else it returns the path unchanged.

const fs = require('fs');
const path = require('path').win32;

// The package family name, `<name>_<publisher id>`, from the install dir:
// C:\Program Files\WindowsApps\<name>_<version>_<arch>_<resource id>_<publisher id>\…
function packageFamily(execPath) {
  const parts = String(execPath || '').split(/[\\/]/);
  const i = parts.findIndex((p) => p.toLowerCase() === 'windowsapps');
  const fields = i >= 0 && parts[i + 1] ? parts[i + 1].split('_') : [];
  return fields.length === 5 && fields[0] && fields[4] ? `${fields[0]}_${fields[4]}` : null;
}

function isUnder(p, dir) {
  const rel = path.relative(dir, p);
  return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
}

function hostPath(p, {
  store = process.windowsStore,
  execPath = process.execPath,
  appData = process.env.APPDATA,
  localAppData = process.env.LOCALAPPDATA,
  exists = fs.existsSync,
} = {}) {
  if (!store || !p || !appData || !localAppData) return p;
  const family = packageFamily(execPath);
  if (!family) return p;
  const packages = path.join(localAppData, 'Packages');
  const cache = path.join(packages, family, 'LocalCache');
  for (const [real, kind] of [[appData, 'Roaming'], [localAppData, 'Local']]) {
    if (!isUnder(p, real) || isUnder(p, packages)) continue;
    // A file that was already there before the package wrote to it stays in
    // the real place, so the private copy is used only where it exists.
    const mapped = path.join(cache, kind, path.relative(real, p));
    return exists(mapped) ? mapped : p;
  }
  return p;
}

module.exports = { hostPath, packageFamily };
