// electron-builder `afterPack` hook (wired in electron-builder.cjs): what a
// packed app needs before its targets are made.
//
// macOS: ad-hoc signing without a Developer ID (scripts/adhoc-sign.cjs).
// The Microsoft Store package: "Open with Gamma" on folders is a COM class
// the package declares (assets/appx-extensions.xml), so its DLL is built
// (scripts/build-open-with.cjs) and put beside Gamma.exe, where the manifest
// names it (app\GammaOpenWith.dll). Only when the targets include appx: the
// NSIS installer's verb is a registry command and needs no DLL. The DLL is
// x64, as the package is (the release workflow builds no other).

const fs = require('fs');
const path = require('path');
const { Arch } = require('electron-builder');
const adhocSign = require('./adhoc-sign.cjs');
const { buildOpenWith } = require('./build-open-with.cjs');
const { OPEN_WITH } = require('./openwith.cjs');

module.exports = async function afterPack(context) {
  await adhocSign(context);
  if (context.electronPlatformName !== 'win32') return;
  if (!context.targets.some((target) => target.name === 'appx')) return;
  if (context.arch !== Arch.x64) {
    throw new Error(`The Store package's folder verb is built for x64 only, not ${Arch[context.arch]}`);
  }
  const { dll } = buildOpenWith();
  fs.copyFileSync(dll, path.join(context.appOutDir, OPEN_WITH.dll));
  console.log(`  • folder verb  ${OPEN_WITH.dll} → ${path.basename(context.appOutDir)}`);
};
