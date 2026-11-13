// The terminal a page's "Continue in a terminal" opens (lib/terminal.js):
// per platform, the program found, the argv passed as arguments, quoted
// where a command line is one string, and what cannot be passed safely
// refused. Nothing is spawned: the spawn is a stand-in. `npm test` in
// desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { onPath, shellQuote, terminalCommand, openTerminal } = require('../lib/terminal');

// `found` for the programs named, at /bin/<name> (C:\bin\<name> on Windows).
const having = (...names) => (name) => (names.includes(name) ? (name.endsWith('.exe') ? `C:\\bin\\${name}` : `/bin/${name}`) : null);
const CODEX = ['C:\\Users\\Tim\\AppData\\Local\\Programs\\OpenAI\\Codex\\bin\\codex.exe', 'resume', '0199a1b2-c3d4'];
const PAPER = 'C:\\Users\\Tim\\My papers\\paper';

test('Windows: Windows Terminal when found, in the folder, the argv after --', () => {
  const run = terminalCommand('win32', PAPER, CODEX, having('wt.exe', 'cmd.exe'));
  assert.equal(run.command, 'C:\\bin\\wt.exe');
  assert.deepEqual(run.args, ['-d', PAPER, '--', ...CODEX]);
  assert.deepEqual(run.options, { cwd: PAPER, detached: true, stdio: 'ignore' });
});

test('Windows: else a new console, cmd.exe /k running the argv with every part in double quotes', () => {
  const run = terminalCommand('win32', PAPER, CODEX, having('cmd.exe'));
  assert.equal(run.command, 'C:\\bin\\cmd.exe');
  assert.deepEqual(run.args, ['/d', '/c', 'start', '"Gamma"', '/d', `"${PAPER}"`, 'cmd.exe', '/d', '/k', 'call', ...CODEX.map((a) => `"${a}"`)]);
  assert.equal(run.options.windowsVerbatimArguments, true);
  assert.equal(run.options.detached, true);
  // a part cmd.exe reads even within quotes is refused; & and parentheses are kept quoted
  for (const bad of ['C:\\100%\\paper', 'C:\\wow!\\paper', 'C:\\a^b']) {
    assert.throws(() => terminalCommand('win32', bad, CODEX, having('cmd.exe')), /cannot be opened in a terminal safely/, bad);
  }
  assert.throws(() => terminalCommand('win32', PAPER, ['C:\\codex.exe', 'C:\\dir\\'], having('cmd.exe')), /backslash/);
  const kept = terminalCommand('win32', 'C:\\R&D (old)\\paper', CODEX, having('cmd.exe'));
  assert.equal(kept.args[5], '"C:\\R&D (old)\\paper"');
  // a part Windows Terminal would read as its separator, wherever its ; is, goes to cmd.exe
  assert.equal(terminalCommand('win32', PAPER, [...CODEX, 'a;'], having('wt.exe', 'cmd.exe')).command, 'C:\\bin\\cmd.exe');
  const split = terminalCommand('win32', 'C:\\Users\\Tim\\draft;calc', CODEX, having('wt.exe', 'cmd.exe'));
  assert.deepEqual([split.command, split.args[5]], ['C:\\bin\\cmd.exe', '"C:\\Users\\Tim\\draft;calc"']);
  // no double quote anywhere
  assert.throws(() => terminalCommand('win32', PAPER, ['C:\\codex.exe', 'say "hi"'], having('wt.exe')), /double quote/);
});

test('macOS: Terminal through osascript, every argument quoted for the shell and the line for AppleScript', () => {
  const cwd = "/Users/tim/Tim's papers/paper";
  const argv = ['/opt/homebrew/bin/claude', '--resume', 'a"b; c $(touch x)'];
  const run = terminalCommand('darwin', cwd, argv, having());
  assert.equal(run.command, '/usr/bin/osascript');
  const line = `cd '/Users/tim/Tim'\\''s papers/paper' && '/opt/homebrew/bin/claude' '--resume' 'a"b; c $(touch x)'`;
  const script = `tell application "Terminal" to do script "${line.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
  assert.deepEqual(run.args, ['-e', script, '-e', 'tell application "Terminal" to activate']);
  assert.equal(run.options.detached, true);
  assert.equal(shellQuote("it's"), `'it'\\''s'`);
  // what another login shell reads within single quotes is refused: fish's backslash, csh's !
  for (const bad of ["/Users/tim/x\\'; open -a Calculator; #", '/Users/tim/wow!/paper']) {
    assert.throws(() => terminalCommand('darwin', bad, argv, having()), /backslash or !/, bad);
  }
});

test('Linux: x-terminal-emulator, else the first of gnome-terminal, konsole and xterm found; none is said', () => {
  const cwd = '/home/tim/paper';
  const argv = ['/home/tim/.local/bin/codex', 'resume', 't1'];
  const all = having('x-terminal-emulator', 'gnome-terminal', 'konsole', 'xterm');
  assert.deepEqual([terminalCommand('linux', cwd, argv, all).command, terminalCommand('linux', cwd, argv, all).args], ['/bin/x-terminal-emulator', ['-e', ...argv]]);
  assert.deepEqual(terminalCommand('linux', cwd, argv, having('gnome-terminal', 'xterm')).args, [`--working-directory=${cwd}`, '--', ...argv]);
  assert.deepEqual(terminalCommand('linux', cwd, argv, having('konsole', 'xterm')).args, ['--workdir', cwd, '-e', ...argv]);
  const xterm = terminalCommand('linux', cwd, argv, having('xterm'));
  assert.deepEqual([xterm.command, xterm.args, xterm.options.cwd], ['/bin/xterm', ['-e', ...argv], cwd]);
  assert.throws(() => terminalCommand('linux', cwd, argv, having()), /No terminal was found/);
});

test('an answer that is no directory and argv, or holds a control character, is refused everywhere', () => {
  for (const platform of ['win32', 'darwin', 'linux']) {
    const cwd = platform === 'win32' ? PAPER : '/home/tim/paper';
    const found = having('wt.exe', 'xterm');
    assert.throws(() => terminalCommand(platform, 'paper', ['codex'], found), /no directory/, platform);
    assert.throws(() => terminalCommand(platform, null, ['codex'], found), /no directory/, platform);
    for (const argv of [[], 'codex resume t1', ['codex', ''], ['codex', 3], null]) {
      assert.throws(() => terminalCommand(platform, cwd, argv, found), /no command/, `${platform} ${JSON.stringify(argv)}`);
    }
    assert.throws(() => terminalCommand(platform, cwd, ['codex', 'a\nrm -rf ~'], found), /control character/, platform);
  }
});

test('opening spawns the command detached as an argv and lets it go', () => {
  const run = terminalCommand('linux', '/home/tim/paper', ['codex', 'resume', 't1'], having('xterm'));
  const spawned = [];
  const child = { handlers: {}, on(ev, fn) { this.handlers[ev] = fn; return this; }, unref() { this.unrefed = true; } };
  openTerminal(run, (command, args, options) => { spawned.push([command, args, options]); return child; });
  assert.deepEqual(spawned, [['/bin/xterm', ['-e', 'codex', 'resume', 't1'], run.options]]);
  assert.equal(child.unrefed, true);
  assert.equal(typeof child.handlers.error, 'function', 'a terminal that fails to start throws nothing in the shell');
});

test('a program on the PATH is found, with PATHEXT on Windows', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gamma-terminal-'));
  try {
    fs.writeFileSync(path.join(dir, 'wt.exe'), '');
    fs.writeFileSync(path.join(dir, 'xterm'), '', { mode: 0o755 });
    const win = { platform: 'win32', env: { PATH: `C:\\nowhere;${dir}`, PATHEXT: '.COM;.EXE' } };
    if (process.platform === 'win32') {
      assert.equal(onPath('wt', win).toLowerCase(), path.join(dir, 'wt.exe').toLowerCase()); // as PATHEXT spells it
      assert.equal(onPath('wt.exe', win), path.join(dir, 'wt.exe'));
    }
    assert.equal(onPath('konsole', { ...win, platform: process.platform, env: { PATH: dir, Path: dir } }), null);
    if (process.platform !== 'win32') assert.equal(onPath('xterm', { platform: 'linux', env: { PATH: `/nowhere:${dir}` } }), path.join(dir, 'xterm'));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
