// Paths a Microsoft Store install hands to Explorer (lib/hostPath.js): the
// package's private copy of AppData, where the files really are. `npm test`
// in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const { hostPath, packageFamily } = require('../lib/hostPath');

const EXE = 'C:\\Program Files\\WindowsApps\\xwtim.GammaPDF_1.4.0.0_x64__abc123xyz\\app\\Gamma.exe';
const CACHE = 'C:\\Users\\u\\AppData\\Local\\Packages\\xwtim.GammaPDF_abc123xyz\\LocalCache';
const env = (present = []) => ({
  store: true,
  execPath: EXE,
  appData: 'C:\\Users\\u\\AppData\\Roaming',
  localAppData: 'C:\\Users\\u\\AppData\\Local',
  exists: (p) => present.includes(p),
});

test('the package family comes from the install directory', () => {
  assert.equal(packageFamily(EXE), 'xwtim.GammaPDF_abc123xyz');
  assert.equal(packageFamily('C:\\Program Files\\Gamma\\Gamma.exe'), null);
  assert.equal(packageFamily(''), null);
});

test('a Store install opens the private copy of what it wrote under AppData', () => {
  const log = 'C:\\Users\\u\\AppData\\Roaming\\gamma-desktop\\logs\\abc.log';
  const privateLog = `${CACHE}\\Roaming\\gamma-desktop\\logs\\abc.log`;
  assert.equal(hostPath(log, env([privateLog])), privateLog);
  const local = 'C:\\Users\\u\\AppData\\Local\\gamma\\x';
  assert.equal(hostPath(local, env([`${CACHE}\\Local\\gamma\\x`])), `${CACHE}\\Local\\gamma\\x`);
});

test('a path with no private copy, outside AppData, or off the Store is left alone', () => {
  const log = 'C:\\Users\\u\\AppData\\Roaming\\gamma-desktop\\logs\\abc.log';
  assert.equal(hostPath(log, env()), log); // was there before the package
  assert.equal(hostPath('D:\\Gamma\\data', env(['D:\\Gamma\\data'])), 'D:\\Gamma\\data');
  assert.equal(hostPath(`${CACHE}\\Roaming\\x`, env([`${CACHE}\\Local\\Packages\\xwtim.GammaPDF_abc123xyz\\LocalCache\\Roaming\\x`])), `${CACHE}\\Roaming\\x`);
  const privateLog = `${CACHE}\\Roaming\\gamma-desktop\\logs\\abc.log`;
  assert.equal(hostPath(log, { ...env([privateLog]), store: false }), log);
  assert.equal(hostPath(log, { ...env([privateLog]), execPath: 'C:\\Program Files\\Gamma\\Gamma.exe' }), log);
});
