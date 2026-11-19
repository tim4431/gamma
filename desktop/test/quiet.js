// What keeps a test run's shell window off the screen and the keyboard, the
// one place the scripts (e2e.js, security.e2e.js, smoke.js) take it from.
// `env` opens the window minimized (main.js TEST_MINIMIZED) unless the run is
// headed (tools/headed.cjs). `printShellLines` passes on the `[shell]` lines
// the shell logs in place of a message box (lib/systemFiles.js errorBox, an uncaught
// error), which the Playwright driver would otherwise keep.
const headed = require('../../tools/headed.cjs');

const env = headed ? {} : { GAMMA_SHELL_MINIMIZED: '1' };

function printShellLines(child) {
  child.stderr.on('data', (d) => { if (String(d).includes('[shell]')) process.stderr.write(d); });
}

module.exports = { headed, env, printShellLines };
