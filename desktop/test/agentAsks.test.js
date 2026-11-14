// An agent's ask on this computer (lib/agentAsks.js): the queue the shell
// keeps of the host's list, the card's words, and what the bar, the
// notification and the tray say. `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const agentAsks = require('../lib/agentAsks');

const at = (min) => `2026-11-13T10:${String(min).padStart(2, '0')}:00Z`;
const time = (iso) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

// An ask as the host lists it.
const askOf = (id, more = {}) => ({
  id, turn_id: 't1', link_id: 'v1', agent: 'codex', label: 'Codex', kind: 'command',
  command: 'curl -L https://example.org/refs.bib -o refs.bib', dir: 'D:\\Papers\\cavity', folder: 'D:\\Papers\\cavity', file: 'main.pdf',
  host: 'example.org', reason: 'Fetch the bibliography the paper cites.',
  server: { origin: 'https://nas.lab:5001', account: 'alice' }, at: at(2), expires_at: at(12), ...more,
});
const servers = [{ id: 'n1', type: 'remote', name: 'NAS', url: 'https://nas.lab:5001' }, { id: 'l1', type: 'local', name: 'Local' }];

test('the queue keeps the shown ask in its place and puts newcomers behind it, an older one too', () => {
  const tombs = new Set();
  let q = agentAsks.take([], [askOf('b', { at: at(5) }), askOf('a', { at: at(3) })], tombs);
  assert.deepEqual(q.map((a) => a.id), ['a', 'b'], 'in the order they were asked');
  q = agentAsks.take(q, [askOf('c', { at: at(1) }), askOf('a', { at: at(3) }), askOf('b', { at: at(5) })], tombs);
  assert.deepEqual(q.map((a) => a.id), ['a', 'b', 'c'], 'an older newcomer goes behind those queued');
  q = agentAsks.take(q, [askOf('c', { at: at(1) })], tombs);
  assert.deepEqual(q.map((a) => a.id), ['c'], 'those the list no longer holds leave');
  assert.deepEqual(agentAsks.take(q, null, tombs), []);
});

test('an ask answered here stays out while a list read before the answer still holds it, and its tombstone goes once one does not', () => {
  const tombs = new Set(['a']);
  let q = agentAsks.take([askOf('a')], [askOf('a'), askOf('b', { at: at(4) })], tombs);
  assert.deepEqual(q.map((x) => x.id), ['b']);
  assert.ok(tombs.has('a'));
  q = agentAsks.take(q, [askOf('b', { at: at(4) })], tombs);
  assert.equal(tombs.size, 0, 'the list lacks it: forgotten');
  assert.deepEqual(q.map((x) => x.id), ['b']);
});

test('the queue keeps which host each ask came from', () => {
  const q = agentAsks.take([{ ...askOf('a'), hostId: 'l1' }], [askOf('a', { reason: 'Again.' })], new Set());
  assert.deepEqual([q[0].hostId, q[0].reason], ['l1', 'Again.'], 'the newer words, the same host');
});

test('a command\'s card: what it runs, where, from whom, the agent\'s reason marked as its own, the risk and the time it is declined', () => {
  const card = agentAsks.cardOf([{ ...askOf('a'), hostId: 'l1' }], servers);
  assert.deepEqual([card.key, card.kind, card.label, card.hostId, card.link, card.waiting, card.count], ['a', 'command', 'Codex', 'l1', 'v1', 1, '']);
  assert.equal(card.caption, 'Approval needed on this computer');
  assert.equal(card.title, 'Codex wants to run a command');
  assert.deepEqual([card.box, card.through, card.lines], ['curl -L https://example.org/refs.bib -o refs.bib', '', '']);
  assert.deepEqual(card.rows, [['In', 'D:\\Papers\\cavity', true], ['Network', 'example.org', false],
    ['From', 'the chat of main.pdf on NAS (https://nas.lab:5001)', false], ['As', 'alice', false]]);
  assert.equal(card.says, 'Codex says: “Fetch the bibliography the paper cites.”');
  assert.deepEqual(card.notes, ['It runs as you, with your files and the network. A build also runs what this folder\'s files say, and NAS can change them.',
    `Declined by itself at ${time(at(12))} unless you answer.`]);
  assert.equal(card.stop, 'Stop Codex asking from NAS');
  assert.deepEqual(card.from, { name: 'NAS', origin: 'https://nas.lab:5001', account: 'alice' });
  assert.equal(card.folder, 'cavity');
});

test('the inner command of a shell wrapper is shown large, the wrapper under it; lines are counted', () => {
  const wrapped = 'powershell.exe -Command \'latexmk -pdf main.tex\n bibtex main\n latexmk -pdf main.tex\'';
  const card = agentAsks.cardOf([askOf('a', { command: wrapped, inner: 'latexmk -pdf main.tex\n bibtex main\n latexmk -pdf main.tex', shell: 'PowerShell', lines: 3, host: '' })], servers);
  assert.equal(card.box, 'latexmk -pdf main.tex\n bibtex main\n latexmk -pdf main.tex');
  assert.equal(card.through, `Through PowerShell: ${wrapped}`);
  assert.equal(card.lines, '3 lines');
  assert.deepEqual(card.rows.map((r) => r[0]), ['In', 'From', 'As'], 'no network row without a host');
  assert.equal(agentAsks.cardOf([askOf('a', { lines: 1 })], servers).lines, '', 'one line says nothing');
});

test('characters that do not show are marked and counted, those the host marked and any it missed', () => {
  const card = agentAsks.cardOf([askOf('a', { command: 'echo ⟨U+202E⟩gnp.exe', marked: 1, reason: 'Look\u200b here', inner: 'x', shell: '' })], servers);
  assert.equal(card.box, 'echo ⟨U+202E⟩gnp.exe', 'no inner command without its shell');
  assert.equal(card.says, 'Codex says: “Look⟨U+200B⟩ here”');
  assert.equal(card.notes[0], 'This holds 2 characters that do not show: Gamma marks them ⟨U+…⟩.');
  assert.equal(card.marked, 2, 'the bar draws the marks as marks');
  assert.equal(agentAsks.cardOf([askOf('a', { command: 'echo ⟨U+202E⟩ typed', reason: '' })], servers).marked, 0,
    'letters the agent typed are no mark');
  assert.deepEqual(agentAsks.shown('a\tb\nc\u0000\u2028\ufeff'), ['a\tb\nc⟨U+0000⟩⟨U+2028⟩⟨U+FEFF⟩', 3], 'the tab and the newline kept');
  assert.equal(agentAsks.cardOf([askOf('a', { marked: 1, command: 'ls ⟨U+2066⟩' })], servers).notes[0],
    'This holds 1 character that does not show: Gamma marks it ⟨U+…⟩.');
});

test('a file change\'s card: its paths and its diff, no network, its own risk; background and a replaced card say so', () => {
  const files = askOf('f', { kind: 'files', command: '', paths: ['D:\\Papers\\refs.bib', 'D:\\Shared\\style.sty'], diff: '--- a\n+++ b\n-x\n+y', host: '', reason: '' });
  const card = agentAsks.cardOf([files, askOf('g', { at: at(9) })], servers);
  assert.equal(card.title, 'Codex wants to change files outside its sandbox');
  assert.deepEqual(card.rows[0], ['Files', 'D:\\Papers\\refs.bib\nD:\\Shared\\style.sty', true]);
  assert.equal(card.diff, '--- a\n+++ b\n-x\n+y');
  assert.deepEqual([card.box, card.through, card.says, card.count], ['', '', '', '1 of 2 waiting']);
  assert.equal(card.notes[0], 'It changes files outside the work folder.');
  const bg = agentAsks.cardOf([askOf('b', { background: true, label: 'Claude Code', agent: 'claude' })], servers, 'b');
  assert.ok(bg.notes.includes('Asked to run in the background: Gamma runs it in the foreground, so Stop ends it.'));
  assert.ok(bg.notes.includes('The question before this one went away: this is another one.'));
  assert.equal(agentAsks.cardOf([askOf('b')], servers, 'other').notes.some((n) => /went away/.test(n)), false, 'another key is not this one');
  assert.equal(agentAsks.cardOf([], servers), null);
});

test('a server the registry does not hold is named by its host; the account is cut to 64, its own row', () => {
  const card = agentAsks.cardOf([askOf('a', { file: '', server: { origin: 'https://far.example:8443', account: 'x'.repeat(90) } })], servers);
  assert.deepEqual(card.rows.slice(-2), [['From', 'the chat on https://far.example:8443', false], ['As', 'x'.repeat(64), false]]);
  assert.equal(card.stop, 'Stop Codex asking from far.example:8443');
  assert.match(card.notes[0], /and far\.example:8443 can change them\.$/);
});

test('what the bar, the notification and the tray say', () => {
  const card = agentAsks.cardOf([askOf('a'), askOf('b', { at: at(4) })], servers);
  assert.equal(agentAsks.notice(card, 'once'), 'Allowed once: Codex goes on.');
  assert.equal(agentAsks.notice(card, 'deny'), 'Declined: Codex goes on without it.');
  assert.equal(agentAsks.notice(card, 'stop'), 'Declined, and Codex no longer asks on this computer for NAS.');
  assert.equal(agentAsks.stale(card), 'Codex no longer waits for that: nothing was allowed.');
  assert.equal(agentAsks.unsure(card), 'Gamma did not hear whether Codex got your answer: see the chat on NAS.');
  assert.equal(agentAsks.couldNot(card, 'That server is not running'),
    `Could not answer Codex: That server is not running. Without an answer it is declined at ${time(at(12))}.`);
  assert.equal(agentAsks.answerFirst(card), 'Answer Codex\'s question first.');
  assert.deepEqual(agentAsks.notification(card), { title: 'Codex asks to run a command', body: 'From NAS, in cavity. Answer it in Gamma\'s window.' });
  const files = agentAsks.cardOf([askOf('f', { kind: 'files', folder: '/home/tim/Papers/cavity/' })], servers);
  assert.deepEqual(agentAsks.notification(files), { title: 'Codex asks to change files', body: 'From NAS, in cavity. Answer it in Gamma\'s window.' });
  assert.equal(agentAsks.trayLabel([askOf('a')]), 'Answer Codex…');
  assert.equal(agentAsks.trayLabel([askOf('a'), askOf('b')]), 'Answer Codex (2 waiting)…');
  assert.equal(agentAsks.trayLabel([]), '');
  assert.equal(agentAsks.trayTip([askOf('a')]), 'Gamma: Codex waits for your answer');
  assert.equal(agentAsks.trayTip([]), 'Gamma');
});
