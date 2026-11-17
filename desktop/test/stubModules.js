// Modules replaced for the code a test requires next (`stubs`, id →
// exports): Electron, and electron-updater, under plain Node.
const Module = require('module');

module.exports = function stubModules(stubs) {
  const resolve = Module._resolveFilename;
  Module._resolveFilename = function (request, ...rest) {
    return Object.hasOwn(stubs, request) ? request : resolve.call(this, request, ...rest);
  };
  for (const [id, exports] of Object.entries(stubs)) require.cache[id] = { id, filename: id, loaded: true, exports };
};
