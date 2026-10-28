// electron-builder `afterPack` hook of the Mac App Store build (wired in
// electron-builder.mas.cjs): every file in the app readable by everyone,
// directories and executables searchable, nothing writable but by the
// owner.
//
// Why: the store installs the .pkg's files with the modes they were packed
// with, owned by root. A build machine with a private umask (077) packs
// 0600 files and 0700 directories, the frozen server's among them, and the
// installed app cannot read its own code. Signing runs after this hook; the
// npm scripts set umask 022 so the signatures' files come out the same.

const { execFileSync } = require('child_process');
const path = require('path');

module.exports = async function masModes(context) {
  if (context.electronPlatformName !== 'mas') return;
  const app = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`);
  execFileSync('chmod', ['-R', 'u+rwX,go+rX,go-w', app], { stdio: 'inherit' });
};
