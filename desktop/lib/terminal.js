// A terminal of this computer opened in a directory, running a command line:
// a page's "Continue in a terminal" (main.js terminalFromPage), the agent's
// session of a computer chat resumed where the agent works. The command line
// is the open server's answer, never the page's, and always an argv: each
// terminal gets it as separate arguments, spawned with no shell between.
// Two terminals take a command line as one string, and there each argument
// is quoted: cmd.exe (when Windows Terminal is not there), whose quotes
// carry no `"`, `%`, `!` or `^`, so those are refused; macOS Terminal, whose
// `do script` types a line into the person's login shell, every argument
// quoted for a POSIX shell, and a backslash (fish reads one within single
// quotes) or `!` (csh's history, within them too) refused. Windows Terminal
// reads a `;` anywhere in its arguments as its own command separator, so
// such a command line goes to cmd.exe instead. No Electron in here; unit
// tested by test/terminal.test.js (`npm test`).

const fs = require('fs');
const path = require('path');
const { spawn: spawnChild } = require('child_process');

// Where program `name` is on the PATH, or null. On Windows the PATHEXT
// endings are tried, and a file is one lstat finds: Windows Terminal's
// wt.exe is an app execution alias, a link that stat cannot follow.
function onPath(name, { platform = process.platform, env = process.env } = {}) {
  const p = platform === 'win32' ? path.win32 : path.posix;
  const dirs = String((platform === 'win32' ? env.Path || env.PATH : env.PATH) || '').split(p.delimiter).filter(Boolean);
  const endings = platform === 'win32' && !p.extname(name)
    ? String(env.PATHEXT || '.COM;.EXE;.BAT;.CMD').split(';').filter(Boolean)
    : [''];
  for (const dir of dirs) {
    for (const ending of endings) {
      const file = p.join(dir, name + ending);
      try {
        if (platform === 'win32') {
          fs.lstatSync(file);
          return file;
        }
        if (fs.statSync(file).isFile()) {
          fs.accessSync(file, fs.constants.X_OK);
          return file;
        }
      } catch {}
    }
  }
  return null;
}

const unsafe = (why) => new Error(`This command cannot be opened in a terminal safely: ${why}`);

// A word quoted for a POSIX shell: in single quotes, a quote in it closed,
// escaped and opened again.
const shellQuote = (s) => `'${s.replace(/'/g, `'\\''`)}'`;

// An AppleScript string.
const scriptString = (s) => `"${s.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;

// The terminal that opens `cwd` running `argv` on `platform`: `{ command,
// args, options }` to spawn, detached. `found(name)` says where a program
// is (onPath), null when it is not there. Throws when the answer is not a
// directory and an argv, a part cannot be passed safely, or no terminal is
// found.
function terminalCommand(platform, cwd, argv, found) {
  const p = platform === 'win32' ? path.win32 : path.posix;
  if (typeof cwd !== 'string' || !p.isAbsolute(cwd)) throw new Error('The answer names no directory to open the terminal in.');
  if (!Array.isArray(argv) || !argv.length || !argv.every((a) => typeof a === 'string' && a)) {
    throw new Error('The answer names no command to run in the terminal.');
  }
  const parts = [cwd, ...argv];
  if (parts.some((a) => /[\x00-\x1f\x7f]/.test(a))) throw unsafe('it holds a control character.');
  const options = { cwd, detached: true, stdio: 'ignore' };
  if (platform === 'win32') {
    if (parts.some((a) => a.includes('"'))) throw unsafe('it holds a double quote.');
    const wt = found('wt.exe');
    if (wt && !parts.some((a) => a.includes(';'))) {
      return { command: wt, args: ['-d', cwd, '--', ...argv], options };
    }
    // A new console: cmd.exe starts one (`start`), in which cmd.exe /k runs
    // the argv and stays. Every part is in double quotes at both readings,
    // `call` keeping the second one from taking the quotes off; within them
    // cmd.exe still reads `%` and `!` (and `call` doubles a `^`), and the
    // program reads a backslash before the closing quote as escaping it.
    if (parts.some((a) => /[%!^]/.test(a))) throw unsafe('it holds %, ! or ^.');
    if (argv.some((a) => a.endsWith('\\'))) throw unsafe('an argument ends in a backslash.');
    const quote = (a) => `"${a}"`;
    return {
      command: found('cmd.exe') || 'cmd.exe',
      args: ['/d', '/c', 'start', '"Gamma"', '/d', quote(cwd), 'cmd.exe', '/d', '/k', 'call', ...argv.map(quote)],
      options: { ...options, windowsVerbatimArguments: true },
    };
  }
  if (platform === 'darwin') {
    if (parts.some((a) => /[\\!]/.test(a))) throw unsafe('it holds a backslash or !.');
    const line = `cd ${shellQuote(cwd)} && ${argv.map(shellQuote).join(' ')}`;
    return {
      command: found('osascript') || '/usr/bin/osascript',
      args: ['-e', `tell application "Terminal" to do script ${scriptString(line)}`, '-e', 'tell application "Terminal" to activate'],
      options,
    };
  }
  const linux = [
    ['x-terminal-emulator', ['-e', ...argv]],
    ['gnome-terminal', [`--working-directory=${cwd}`, '--', ...argv]],
    ['konsole', ['--workdir', cwd, '-e', ...argv]],
    ['xterm', ['-e', ...argv]],
  ];
  for (const [name, args] of linux) {
    const command = found(name);
    if (command) return { command, args, options };
  }
  throw new Error('No terminal was found on this computer (x-terminal-emulator, gnome-terminal, konsole or xterm).');
}

// Opens the terminal terminalCommand answered and leaves it to run on its
// own, whatever happens to Gamma.
function openTerminal(run, spawn = spawnChild) {
  const child = spawn(run.command, run.args, run.options);
  child.on('error', () => {});
  child.unref();
}

module.exports = { onPath, shellQuote, terminalCommand, openTerminal };
