// The bar's reading of what this computer keeps (lib/keeping.js): a clone's
// states in the order Gamma's sync pill checks them, a Zotero link's, a
// synced vault's of a directory (a work folder's among them) and of one
// file, the one summary the sync
// button shows, and what the shell says about a vault: the dialog's first
// round, the questions before a removal, a force or going on, a round's
// outcome. `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const keeping = require('../lib/keeping');

const local = { id: 'l1', name: 'Local', type: 'local' };
const servers = [local, { id: 'r1', name: 'NAS', type: 'remote', url: 'http://nas.lan:9001' }];
const now = new Date().toISOString();
const clone = (info) => keeping.clone(local, { workspace_id: 'w1', name: 'Lab (clone)', remote_name: 'Lab', remote_url: 'http://nas.lan:9001', ...info }, servers);
const folder = (link) => keeping.vault(local, { id: 'f1', kind: 'folder', folder_id: 'x', dest: 'C:\\Papers\\Quantum', remote_url: '', pull: true, push: 'off', ...link }, servers);
const file = (link) => keeping.vault(local, { id: 'p1', kind: 'file', page_id: 'pg', dest: 'D:\\tex\\build\\main.pdf', remote_url: '', pull: false, push: 'pdfs', title: 'Draft on X', ...link }, servers);

test('a clone reads as the sync pill reads it, in the same order', () => {
  assert.equal(clone({ mode: 'off', status: { running: true } }).state, 'detached');
  assert.equal(clone({ status: { running: true, last_error: 'x' }, conflicts_open: 2 }).state, 'busy');
  assert.equal(clone({ status: { last_error: 'x' }, conflicts_open: 2 }).text, '2 conflicts');
  const down = clone({ status: { last_error: 'cannot reach http://nas.lan:9001', last_sync: now } });
  assert.equal(down.state, 'error');
  assert.match(down.text, /^Remote unreachable/);
  assert.equal(clone({ status: {} }).text, 'Not cloned yet');
  assert.equal(clone({ status: { last_sync: now }, pending_local: true, on_change: true }).state, 'busy');
  assert.equal(clone({ status: { last_sync: now }, pending_local: true }).state, 'pending');
  const ok = clone({ status: { last_sync: now } });
  assert.equal(ok.state, 'ok');
  assert.match(ok.text, /^Up to date · /);
  assert.equal(ok.where, 'Clone of Lab on NAS', 'the remote by its registered name');
  assert.equal(ok.key, 'clone:l1:w1');
});

test('a vault of a directory: its name, its row\'s path ⇄ remote · folder, and its direction as words', () => {
  const v = folder({ status: { last_sync: now, folder_path: ['Papers', 'Quantum'] } });
  assert.deepEqual([v.kind, v.scope, v.key, v.name], ['vault', 'folder', 'vault:l1:f1', 'Quantum']);
  assert.equal(v.where, 'C:\\Papers\\Quantum ⇄ Local · Papers / Quantum');
  assert.deepEqual([v.pull, v.push], [true, 'off']);
  assert.equal(folder({ status: {} }).name, 'Quantum', 'named from its directory before a round');
  assert.equal(folder({ folder_id: 'root', status: {} }).where, 'C:\\Papers\\Quantum ⇄ Local · Whole library');
  assert.equal(folder({ remote_url: 'http://nas.lan:9001', status: { folder_path: ['Lab'] } }).where, 'C:\\Papers\\Quantum ⇄ NAS · Lab', 'the remote by its registered name');
  assert.equal(folder({ remote_url: 'http://other.example:9001', status: {} }).remote, 'other.example:9001');
  // push is a word: the string "off" is no push, whatever Boolean() says of it
  assert.equal(folder({ push: 'off', status: {} }).push, 'off');
  assert.equal(folder({ push: 'everything', status: {} }).push, 'everything');
  assert.equal(folder({ push: true, status: {} }).push, 'off', 'a flag is not a word');
  assert.equal(folder({ pull: false, push: 'everything', status: {} }).pull, false);
});

test('a vault of a directory, worst first: syncing, paused, stopped, held, not there, a problem, never synced, up to date', () => {
  assert.equal(folder({ status: { running: true, stopped: 'x' } }).state, 'busy');
  const paused = folder({ status: { paused_at: now, stopped: 'x', last_error: 'x' } });
  assert.equal(paused.state, 'paused');
  assert.match(paused.text, /^Paused · /);
  const stopped = folder({ status: { stopped: 'C:\\Papers\\Quantum holds another vault\'s marker', last_error: 'x', held: { removals: 9, total: 10 } } });
  assert.deepEqual([stopped.state, stopped.text], ['error', 'Stopped']);
  assert.match(stopped.title, /another vault's marker Locate… tells Gamma where the directory is now\.$/);
  const held = folder({ status: { held: { removals: 7, total: 12 }, offline: true, last_error: 'x' } });
  assert.deepEqual([held.state, held.text], ['held', 'Held · 7 of 12 files gone']);
  assert.match(held.title, /Go on pushes the removals/);
  const away = folder({ status: { offline: true, last_error: 'the directory is not there' } });
  assert.deepEqual([away.state, away.text], ['waiting', 'Directory not there']);
  assert.match(away.title, /^the directory is not there The vault syncs again once it is back; Locate… if it moved\.$/);
  assert.deepEqual([folder({ status: { last_error: 'disk full' } }).state, folder({ status: { last_error: 'disk full' } }).title], ['error', 'disk full']);
  assert.deepEqual([folder({ status: {} }).state, folder({ status: {} }).text], ['new', 'Not synced yet']);
});

test('a work folder is a folder of the server\'s own that pushes everything; one choosing its files says so first, paused or stopped as it is', () => {
  const work = folder({ push: 'everything', library: true, status: { last_sync: now } });
  assert.deepEqual([work.work, work.library, work.choosing], [true, true, false]);
  for (const kept of [folder({ status: {} }), folder({ push: 'pdfs', status: {} }), folder({ push: 'everything', folder_id: 'root', status: {} }),
    folder({ push: 'everything', remote_url: 'http://nas.lan:9001', status: {} }), file({ status: {} })]) {
    assert.equal(kept.work, false, kept.where);
  }
  const choosing = folder({ push: 'everything', choosing: true, status: { paused_at: now, stopped: 'choose its files first' } });
  assert.deepEqual([choosing.choosing, choosing.state, choosing.text], [true, 'pending', 'Choosing files']);
  assert.match(choosing.title, /^Nothing is tracked yet: Choose files… opens its tracking dialog in Gamma/);
});

test('a work folder is what the link says: one of another server is made so there, pushes everything and pulls, and has an agent here once this computer is shown there', () => {
  const nas = { remote_url: 'http://nas.lan:9001', push: 'everything', status: { last_sync: now, folder_path: ['Paper'] } };
  const codex = [{ name: 'codex', label: 'Codex', found: true, ready: true }];
  const farOf = (computers) => keeping.vault(local, { id: 'f1', kind: 'folder', folder_id: 'x', workspace_id: 'lab', dest: 'C:\\Paper', pull: true, mine: true, work: true, ...nas },
    servers, null, codex, computers);
  const far = farOf(null);
  assert.deepEqual([far.work, far.agentHere, far.agents, far.remote, far.push, far.pull], [true, false, null, 'NAS', 'everything', true]);
  // not shown there (another workspace's link does not count): no agent; shown, in any state of its link: one is offered
  const link = (more = {}) => ({ id: 'c1', remote_url: 'http://nas.lan:9001', workspace_id: 'lab', username: 'tim', status: { state: 'connected' }, ...more });
  assert.equal(farOf({ links: [link({ workspace_id: 'other' })], name: 'TIM-PC' }).agentHere, false);
  for (const state of ['connected', 'unreachable', 'connecting']) {
    const shown = farOf({ links: [link({ status: { state } })], name: 'TIM-PC' });
    assert.deepEqual([shown.agentHere, shown.agents.map((a) => a.name), shown.computer.who], [true, ['codex', 'claude'], 'tim'], state);
  }
  // a kept folder of another server, shown there, is no work folder and has none
  assert.equal(keeping.vault(local, { id: 'f1', kind: 'folder', folder_id: 'x', workspace_id: 'lab', dest: 'C:\\Paper', pull: true, mine: true, work: false, ...nas },
    servers, null, codex, { links: [link()], name: 'TIM-PC' }).agentHere, false);
  assert.match(far.title, /Notes, PDFs, new files, renames and deletions are pulled and pushed\.$/);
  // a kept folder of another server that pushes everything stays one; the link's word wins over the push
  assert.equal(folder({ ...nas, work: false }).work, false);
  assert.equal(folder({ push: 'everything', work: false, status: {} }).work, false);
  assert.equal(folder({ ...nas, folder_id: 'root', work: true }).work, false, 'never the whole library');
  const here = folder({ push: 'everything', work: true, status: {} });
  assert.deepEqual([here.work, here.agentHere], [true, true]);
  assert.equal(keeping.REMOTE_WORK_RULE, 'A work folder of another server pushes everything and pulls: remove the vault to stop.');
  // an empty folder there that cannot be one: why, while the vault dialog asks instead
  assert.equal(keeping.remoteWorkRefused('NAS', 'Lab', 'NAS is older than this app and keeps no work folders'),
    '“Lab” cannot be a work folder of NAS: NAS is older than this app and keeps no work folders. It can be synced as a vault that pulls instead.');
  assert.equal(keeping.remoteWorkRefused('NAS', 'Lab', 'A clone keeps no work folder.'),
    '“Lab” cannot be a work folder of NAS: A clone keeps no work folder. It can be synced as a vault that pulls instead.');
  assert.equal(keeping.remoteWorkRefused('NAS', 'Lab', ''), '“Lab” cannot be a work folder of NAS: NAS refused it. It can be synced as a vault that pulls instead.');
  // removing it: its files go to Recently deleted there; that server not reached, the vault is forgotten here only if asked
  assert.match(keeping.removalAsk(far, { pages: 4, files: 3, in_library: 1 }).detail,
    /^Its 3 files not in the library go to Recently deleted on NAS; its page in the library stays\. /);
  const q = keeping.farRemovalAsk(far, 'NAS cannot be reached: remove the vault when it can, or let go of the folder there');
  assert.equal(q.message, 'NAS cannot be reached: remove the work folder “Paper” all the same?');
  assert.match(q.detail, /^NAS cannot be reached: remove the vault when it can, or let go of the folder there\. Remove anyway, and this computer forgets it; the folder stays a work folder on NAS, its files locked, until the workspace's owner lets go of it there\./);
  assert.deepEqual(q.buttons, ['Remove anyway', 'Cancel']);
});

test('a work folder\'s agent: who made it, the agent and how it stands, the agents found here, and a run that works reads busy', () => {
  const work = (link, agents) => keeping.vault(local, { id: 'f1', kind: 'folder', folder_id: 'x', dest: 'C:\\Papers\\Quantum', remote_url: '',
    pull: true, push: 'everything', status: { last_sync: now }, ...link }, servers, null, agents);
  const found = [{ name: 'codex', label: 'Codex', found: true, version: 'codex-cli 0.167.0', ready: true, problem: '' },
    { name: 'claude', label: 'Claude Code', found: true, version: '2.1.318', ready: false, problem: 'Claude Code is not signed in on this computer: run `claude` in a terminal and sign in.' }];

  const none = work({ mine: true, agent: null }, found);
  assert.deepEqual([none.mine, none.agent, none.agentLabel, none.agentReady, none.agentProblem, none.agentRunning], [true, '', '', null, '', '']);
  assert.deepEqual(none.agents.map((a) => [a.name, a.label, a.ready]), [['codex', 'Codex', true], ['claude', 'Claude Code', false]]);
  assert.equal(none.state, 'ok');

  const on = work({ mine: true, agent: { name: 'codex', label: 'Codex', ready: false, problem: 'Codex is not signed in on this computer: run `codex login` in a terminal.', running: null } }, found);
  assert.deepEqual([on.agent, on.agentLabel, on.agentReady, on.agentRunning], ['codex', 'Codex', false, '']);
  assert.match(on.agentProblem, /^Codex is not signed in/);
  // what it may run: never unless the link says ask (a server from before 3b says nothing)
  assert.deepEqual([none.agentRuns, on.agentRuns], ['never', 'never']);
  assert.equal(work({ mine: true, agent: { name: 'claude', label: 'Claude Code', ready: true, runs: 'ask' } }, found).agentRuns, 'ask');
  assert.equal(work({ mine: true, agent: { name: 'claude', label: 'Claude Code', ready: true, runs: 'always' } }, found).agentRuns, 'never');

  const checking = work({ agent: { name: 'claude', label: 'Claude Code', ready: null, problem: '', running: null } }, found);
  assert.deepEqual([checking.mine, checking.agentReady], [false, null], 'another account\'s, and the server still checks');

  // a run at work: busy, its detail on the row
  const running = work({ mine: true, agent: { name: 'codex', label: 'Codex', ready: true, problem: '', running: { request_id: 'r1', detail: 'Editing sections/intro.tex' } } }, found);
  assert.deepEqual([running.state, running.text, running.agentRunning], ['busy', 'Codex is working · Editing sections/intro.tex', 'Editing sections/intro.tex']);
  assert.match(running.title, /answering a Send of this folder/);
  const hidden = work({ agent: { name: 'codex', label: 'Codex', ready: true, problem: '', running: { request_id: 'r1', detail: '' } } }, found);
  assert.deepEqual([hidden.state, hidden.text], ['busy', 'Codex is working · Working'], 'a member who cannot act reads no detail');
  assert.deepEqual([keeping.summarize([running]).state, keeping.summarize([running]).text], ['busy', 'Agent working'],
    'no round runs: the title bar says an agent works, not "Syncing…"');
  const round = work({ mine: true, agent: { name: 'codex', running: { request_id: 'r1', detail: 'Reading main.tex' } },
    status: { running: true, progress: { done: 1, total: 4 } } }, found);
  assert.equal(round.text, 'Syncing 1 / 4', 'a round of the vault reads first');
  assert.equal(keeping.summarize([running, round]).text, 'Syncing…', 'a round anywhere still reads as syncing');

  // the vault's own problem keeps its row while the agent works, the run said in its title
  const at = { name: 'codex', label: 'Codex', ready: true, problem: '', running: { request_id: 'r1', detail: 'Editing main.tex' } };
  const failing = work({ mine: true, agent: at, status: { last_sync: now, last_error: 'The disk is full.' } }, found);
  assert.deepEqual([failing.state, failing.text], ['error', 'Sync problem']);
  assert.match(failing.title, /^The disk is full\. Codex is working on a Send meanwhile\.$/);
  const held = work({ mine: true, agent: at, status: { last_sync: now, held: { removals: 3, total: 9 } } }, found);
  assert.equal(held.state, 'held');
  assert.match(held.title, /Codex is working on a Send meanwhile\.$/);
  assert.equal(keeping.summarize([failing]).state, 'error');

  // a turn of a page's chat works the folder too: busy, said as the chat's, not a Send's
  const chatting = { name: 'codex', label: 'Codex', ready: true, problem: '', running: { kind: 'chat', request_id: null, detail: 'Editing sections/intro.tex' } };
  const chat = work({ mine: true, agent: chatting }, found);
  assert.deepEqual([chat.state, chat.text, chat.agentChat], ['busy', 'Codex is working · Editing sections/intro.tex', true]);
  assert.equal(chat.title, 'Codex is working in the chat of a file of this folder on this computer: the files here may change meanwhile.');
  assert.equal(keeping.summarize([chat]).text, 'Agent working');
  assert.match(work({ mine: true, agent: chatting, status: { last_sync: now, last_error: 'The disk is full.' } }, found).title,
    /^The disk is full\. Codex is working in the chat meanwhile\.$/);
  assert.equal(running.agentChat, false, 'a Send\'s run, kind "send" or none, is no chat');

  // a server that runs no agents offers none; one that lists only some reads the others as not here; only a work folder offers them
  assert.equal(work({ mine: true }, null).agents, null);
  assert.deepEqual(work({ mine: true }, [found[0]]).agents[1], { name: 'claude', label: 'Claude Code', found: false, ready: false, problem: 'Claude Code is not on this computer.' });
  assert.equal(work({ mine: true, push: 'pdfs' }, found).agents, null);
  assert.equal(keeping.itemsOf(local, { links: [{ id: 'f1', kind: 'folder', folder_id: 'x', dest: 'C:\\P', push: 'everything', mine: true, status: {} }], agents: found }, servers)[0].agents.length, 2);
});

test('turning on a work folder\'s agent is asked with what it may do and the Sends waiting; the outcome is said', () => {
  const item = { name: 'Quantum', dest: 'C:\\Papers\\Quantum', agentLabel: 'Codex' };
  const codex = keeping.agentAsk(item, 'codex', 2);
  assert.equal(codex.message, 'Let Codex work in C:\\Papers\\Quantum?');
  assert.equal(codex.detail, 'Codex will answer the Sends you make on this folder: it edits files in this directory and runs commands in its sandbox, without network. 2 waiting Sends will be handed to it.');
  assert.deepEqual(codex.buttons, ['Turn on Codex', 'Cancel']);
  assert.equal(keeping.agentAsk(item, 'claude', 0).detail, 'Claude Code will answer the Sends you make on this folder: it edits files in this directory and runs no commands.');
  assert.match(keeping.agentAsk(item, 'claude', 1).detail, / 1 waiting Send will be handed to it\.$/);
  assert.equal(keeping.agentSaid(item, 'codex', 0), 'Codex now answers the Sends on Quantum.');
  assert.equal(keeping.agentSaid(item, 'claude', 3), 'Claude Code now answers the Sends on Quantum; 3 waiting were handed to it.');
  assert.equal(keeping.agentSaid(item, ''), 'Codex no longer answers the Sends on Quantum: they wait for an agent you run yourself.');
});

test('turning on the agent of a work folder of another server says what that server can then do, and the plain http of one outside this computer', () => {
  const far = (remoteUrl, who = 'tim') => ({ name: 'Paper', dest: 'C:\\Paper', remote: 'NAS', remoteUrl, computer: { state: 'connected', who } });
  const said = 'Codex will answer the Sends you make on this folder: it edits files in this directory and runs commands in its sandbox, without network.' +
    ' Whoever can sign in to NAS as tim, and whoever runs NAS, can send work to Codex here. Codex reads your files and its answers are stored on NAS.';
  const plain = ' NAS is reached without encryption: someone on your network could read what is sent and pose as NAS.';
  const https = keeping.agentAsk(far('https://nas.example.org'), 'codex');
  assert.deepEqual([https.message, https.detail, https.buttons], ['Let Codex work in C:\\Paper?', said, ['Turn on Codex', 'Cancel']]);
  assert.equal(keeping.agentAsk(far('http://nas.lan:9001'), 'codex').detail, said + plain);
  for (const url of ['http://127.0.0.1:9100', 'http://localhost:9100', 'http://[::1]:9100']) {
    assert.equal(keeping.agentAsk(far(url), 'codex').detail, said, `${url} is this computer's own`);
  }
  assert.match(keeping.agentAsk(far('https://nas.example.org', ''), 'claude').detail,
    /runs no commands\. Whoever can sign in to NAS as your account, and whoever runs NAS, can send work to Claude Code here\. Claude Code reads your files/);
  assert.equal(keeping.FAR_RUNS_RULE, 'Asking in the chat is for a work folder of this computer\'s server.');
});

test('letting a work folder\'s agent ask in the chat is asked with what it may ask for and the risk; the outcome is said', () => {
  const codex = keeping.runsAsk({ name: 'Quantum', dest: 'C:\\Papers\\Quantum', agent: 'codex', agentLabel: 'Codex' });
  assert.equal(codex.message, 'Let Codex ask to run more in C:\\Papers\\Quantum?');
  assert.equal(codex.detail, 'In the chat of a file of this folder, Codex can then ask you to run a command with the network or to change files outside this folder; nothing of it runs until you allow it there. A command you allow runs as you, with your files and the network.');
  assert.deepEqual(codex.buttons, ['Ask me in the chat', 'Cancel']);
  const claude = { name: 'Quantum', dest: 'C:\\Papers\\Quantum', agent: 'claude', agentLabel: 'Claude Code' };
  assert.match(keeping.runsAsk(claude).detail, /^In the chat of a file of this folder, Claude Code can then ask you to run a command, such as your build; .* runs as you, with your files and the network\.$/);
  assert.equal(keeping.runsSaid(claude, 'ask'), 'Claude Code may now ask you in the chat to run more on Quantum.');
  assert.equal(keeping.runsSaid(claude, 'never'), 'Claude Code no longer asks to run more on Quantum: it keeps to its confinement.');
});

test('a vault of another server says whether this computer is shown there, in its link\'s state, with Stop showing or Show this computer there', () => {
  const remote = (computers, more = {}) => keeping.vault(local, { id: 'f1', kind: 'folder', folder_id: 'x', workspace_id: 'lab', dest: 'C:\\Papers\\Lab',
    remote_url: 'http://nas.lan:9001', pull: true, push: 'off', status: { last_sync: now, folder_path: ['Lab'] }, ...more }, servers, null, null, computers);
  const shown = (state, more = {}) => ({ links: [{ id: 'c1', remote_url: 'http://nas.lan:9001/', workspace_id: 'lab', account: 'u_1', username: 'tim',
    status: { state, error: '', at: now }, created_at: now, ...more }], name: 'TIM-PC' });

  const on = remote(shown('connected')).computer;
  assert.deepEqual([on.state, on.text, on.action, on.button], ['connected', 'Shown on NAS as TIM-PC · connected', 'hide-computer', 'Stop showing']);
  assert.equal(on.title, 'Only tim sees on NAS, in any window, that this vault is kept on TIM-PC, whether it is online, and which assistants are here. No path is sent.');
  const words = Object.fromEntries(['connecting', 'refused', 'older', 'unreachable'].map((s) => [s, remote(shown(s)).computer.text]));
  assert.deepEqual(words, {
    connecting: 'Shown on NAS as TIM-PC · connecting',
    refused: 'Shown on NAS as TIM-PC · refused this computer\'s token',
    older: 'Shown on NAS as TIM-PC · older than this app',
    unreachable: 'Shown on NAS as TIM-PC · not reachable',
  });
  assert.match(remote(shown('refused')).computer.title, /revoked there, or it expired\. Stop showing, then Show this computer there/);
  assert.equal(remote(shown('unreachable', { status: { state: 'unreachable', error: 'timed out' } })).computer.title,
    'NAS does not answer now; this computer tries again by itself. Timed out.');
  assert.equal(remote(shown('')).computer.text, 'Shown on NAS as TIM-PC · connecting', 'a state it does not know reads as connecting');

  // none for this server and workspace: another workspace's or another server's link does not count
  for (const links of [[], shown('connected', { workspace_id: 'other' }).links, shown('connected', { remote_url: 'http://other.lan:9001' }).links]) {
    const none = remote({ links, name: 'TIM-PC' }).computer;
    assert.deepEqual([none.state, none.text, none.action, none.button], ['none', 'Not shown on NAS', 'show-computer', 'Show this computer there']);
    assert.match(none.title, /that this vault is kept on TIM-PC, .* No path is sent\.$/);
  }
  // unread, a vault of this server's own, or another account's seen by a viewer: nothing said
  assert.equal(remote(null).computer, null);
  assert.equal(keeping.vault(local, { id: 'f2', kind: 'folder', folder_id: 'x', dest: 'C:\\Lab', remote_url: '', status: {} }, servers, null, null, shown('connected')).computer, null);
  assert.equal(remote(shown('connected'), { dest: '' }).computer, null);
  // itemsOf hands the links on; a vault's state is its own
  const [item] = keeping.itemsOf(local, { links: [{ id: 'f1', kind: 'folder', folder_id: 'x', workspace_id: 'lab', dest: 'C:\\Lab', remote_url: 'http://nas.lan:9001', status: { last_sync: now } }],
    computers: shown('refused') }, servers);
  assert.deepEqual([item.state, item.computer.state], ['ok', 'refused']);

  // what showing came to, and why not
  assert.equal(keeping.computerSaid('NAS', 'TIM-PC', true), 'TIM-PC is shown on NAS: only your account there sees it, with the vaults it keeps and the assistants here.');
  assert.equal(keeping.computerSaid('NAS', 'TIM-PC', false), 'TIM-PC is no longer shown on NAS; its token there is revoked.');
  const older = 'NAS is older than this app: it cannot show this computer.';
  assert.equal(keeping.computerFailed('NAS', { status: 422, minting: true, body: { detail: [{ msg: 'pattern' }] } }), older, 'its scopes do not know a computer\'s');
  assert.equal(keeping.computerFailed('NAS', { status: 409, body: { detail: 'That server is older than this app: it cannot show this computer.' } }), older);
  assert.equal(keeping.computerFailed('NAS', { status: 400, minting: true, body: { detail: 'At most 20 tokens per account.' } }), 'NAS cannot show this computer: At most 20 tokens per account.');
  assert.equal(keeping.computerFailed('NAS', new Error('fetch failed')), 'NAS cannot show this computer: fetch failed');
});

test('an up-to-date vault says what it pushed, its conflicts, the files changed here and those not synced', () => {
  const v = folder({ push: 'pdfs', status: { last_sync: now, untracked: 4, counts: { sent: 2, conflicts: 1 }, kept: [
    { path: 'a.md', why: 'changed here', force: true },
    { path: 'b.pdf', why: 'attachment belongs to another page', force: false },
  ] } });
  assert.equal(v.state, 'ok');
  assert.match(v.text, /^Up to date · .+ · 2 pushed · 1 conflict · 1 changed here · 1 not synced$/);
  assert.deepEqual([v.changed, v.left, v.untracked], [1, 1, 4]);
  assert.match(v.title, /Gamma's changes are pulled; PDFs you change here are pushed\./);
  assert.match(v.title, /1 conflict in the last round: the file won/);
  assert.match(v.title, /Force pull… replaces it with Gamma's\./);
  assert.match(v.title, /Not synced: b\.pdf \(attachment belongs to another page\)\./);
  assert.match(v.title, /Untracked: 4 files Gamma does not read\.$/);
  const quiet = folder({ status: { last_sync: now, counts: { sent: 0 } } });
  assert.doesNotMatch(quiet.text, /pushed|conflict|changed|not synced/);
  assert.match(quiet.title, /nothing here is pushed\.$/);
  assert.match(folder({ push: 'everything', status: { last_sync: now } }).title, /are pulled and pushed\.$/);
  assert.match(folder({ pull: false, push: 'everything', status: { last_sync: now } }).title, /nothing is pulled\.$/);
});

test('an up-to-date vault lists the names its scan refused among those not synced, says why its push went off, and names the pages another vault keeps', () => {
  const v = folder({ status: { last_sync: now, upgraded_at: now,
    kept: [{ path: 'b.pdf', why: 'attachment belongs to another page', force: false }],
    refused: [{ path: 'notes.md', why: 'differs from another name here only by case' }, { path: 'con.md', why: 'is a name Windows keeps for a device' }],
    push_off: 'push turned off when it was taken onto synced vaults: Its folder holds pages the synced vault D:\\Old pushes into too: only one vault pushes into a page.',
    shared: [{ page: 'p1', title: 'Attention' }, { page: 'p2', title: 'BERT' }] } });
  assert.equal(v.state, 'ok');
  assert.match(v.text, /^Up to date · .+ · 3 not synced · 2 in another vault · push turned off$/);
  assert.equal(v.left, 3);
  assert.match(v.title, /Not synced: b\.pdf \(attachment belongs to another page\); notes\.md \(differs from another name here only by case\); con\.md \(is a name Windows keeps for a device\)\./);
  assert.match(v.title, / Push turned off when it was taken onto synced vaults: Its folder holds pages the synced vault D:\\Old pushes into too: only one vault pushes into a page\./);
  assert.match(v.title, / Kept by an older synced vault beside this one, so not written here: “Attention”, “BERT”\./);
  assert.match(v.title, / Kept on this computer before; a synced vault since .+, with \.gamma\/ in place of \.gamma-sync\.json\./);
  const plain = folder({ status: { last_sync: now } });
  assert.doesNotMatch(`${plain.text} ${plain.title}`, /not synced|another vault|turned off|before/);
});

test('a vault of one file: pushing, paused, a problem, waiting for the file, never pushed, then pushed or up to date', () => {
  assert.equal(file({ status: { running: true, last_error: 'x' } }).text, 'Pushing…');
  const running = file({ status: { running: true, paused_at: now } });
  assert.equal(running.state, 'busy');
  assert.equal(running.paused, true, 'still paused once the round ends');
  const paused = file({ status: { paused_at: now, last_error: 'x', waiting: 'still being written' } });
  assert.equal(paused.state, 'paused');
  const bad = file({ status: { last_error: 'the page is no longer in Gamma', waiting: 'still being written', last_sync: now } });
  assert.deepEqual([bad.state, bad.text, bad.title], ['error', 'Problem', 'the page is no longer in Gamma']);
  const waiting = file({ status: { waiting: 'still being written', last_sync: now } });
  assert.deepEqual([waiting.state, waiting.text, waiting.title], ['pending', 'Waiting for the file to finish', 'still being written']);
  assert.deepEqual([file({ status: {} }).state, file({ status: {} }).text], ['new', 'Not pushed yet']);
  const sent = file({ status: { last_sync: now, last_sent: now } });
  assert.equal(sent.state, 'ok');
  assert.match(sent.text, /^Pushed · /);
  assert.match(file({ status: { last_sync: now } }).text, /^Up to date · /);
  assert.match(file({ status: { last_sync: now, last_sent: now, relocation_error: 'too many background tasks' } }).title,
    /The highlights were not moved: too many background tasks$/);
});

test('a vault of one file is named after its page, only pushes, and its row names the page', () => {
  const f = file({ status: {} });
  assert.deepEqual([f.kind, f.scope, f.key, f.name, f.dest, f.remote, f.paused], ['vault', 'file', 'vault:l1:p1', 'Draft on X', 'D:\\tex\\build\\main.pdf', 'Local', false]);
  assert.equal(f.where, 'D:\\tex\\build\\main.pdf ⇄ Local · Draft on X');
  assert.deepEqual([f.pull, f.push], [false, 'pdfs']);
  assert.equal(file({ title: '', status: { title: 'Draft on Y' } }).name, 'Draft on Y');
  assert.equal(file({ title: '', status: {} }).name, 'main.pdf');
  assert.equal(file({ remote_url: 'http://nas.lan:9001', status: {} }).remote, 'NAS');
  const items = keeping.itemsOf(local, { links: [{ id: 'f1', kind: 'folder', folder_id: 'x', dest: 'C:\\Papers', status: {} }, { id: 'p1', kind: 'file', dest: 'D:\\main.pdf', status: {} }] }, servers);
  assert.deepEqual(items.map((i) => [i.kind, i.scope]), [['vault', 'folder'], ['vault', 'file']], 'one kind, two scopes');
});

test('a directory vault\'s round says how far it is: the files pushed and pages pulled of those it knows, the pushed PDFs\' bytes', () => {
  assert.equal(folder({ status: { running: true, progress: { done: 0, total: 0, path: '' } } }).text, 'Syncing…');
  const going = folder({ status: { running: true, progress: { done: 1200, total: 3400, path: 'Sub/A paper.pdf',
    bytes: { done: 1.2 * 1073741824, total: 8 * 1073741824 } } } });
  assert.equal(going.state, 'busy');
  assert.equal(going.text, 'Syncing 1,200 / 3,400');
  assert.equal(going.title, 'A round is running: 1,200 of 3,400 pushed or pulled (PDFs 1.2 GB of 8.0 GB pushed), now Sub/A paper.pdf.');
  const notes = folder({ status: { running: true, progress: { done: 0, total: 1, path: '', bytes: { done: 0, total: 0 } } } });
  assert.equal(notes.title, 'A round is running: 0 of 1 pushed or pulled.');
  assert.equal(file({ status: { running: true, progress: { done: 1, total: 3 } } }).text, 'Pushing…', 'a file\'s vault pushes one file');
});

test('a vault of a workspace its maker left says so, paused it stays quiet; another account\'s seen by a viewer names no place', () => {
  const roles = [{ id: 'mine', role: 'owner' }, { id: 'lab', role: 'viewer' }];
  const link = (more) => ({ id: 'f1', kind: 'folder', folder_id: 'x', workspace_id: 'gone', dest: 'C:\\Papers', remote_url: '',
    status: { last_sync: now, last_error: 'the account that made this vault has no access to its workspace any more', folder_path: ['Papers'] }, ...more });
  const [away, inLab, remote, unread] = [
    ...keeping.itemsOf(local, { links: [link(), link({ id: 'f2', workspace_id: 'lab' }), link({ id: 'f3', remote_url: 'http://nas.lan:9001' })], roles }, servers),
    ...keeping.itemsOf(local, { links: [link()] }, servers)];
  assert.deepEqual([away.away, away.state, away.text, away.name, away.where], [true, 'error', 'No access to its workspace', 'Papers', 'C:\\Papers ⇄ Local · Papers']);
  assert.match(away.title, /no longer a member.*Remove the vault to free its directory/);
  assert.deepEqual([inLab.away, inLab.text], [false, 'Sync problem'], 'a viewer is still in the workspace');
  assert.equal(remote.away, false, 'a vault of another server names a workspace there');
  assert.equal(unread.away, false, 'no session read: nothing is assumed');
  const awayFile = keeping.itemsOf(local, { links: [{ id: 'p1', kind: 'file', workspace_id: 'gone', dest: 'D:\\main.pdf', status: {} }], roles }, servers)[0];
  assert.match(awayFile.title, /free the file; it stays as it is/);
  assert.equal(keeping.itemsOf(local, { links: [link({ status: { paused_at: now } })], roles }, servers)[0].state, 'paused');
  const seen = folder({ dest: '', path: '', status: { last_sync: now } });
  assert.deepEqual([seen.seen, seen.dest, seen.where, seen.name], [true, '', 'Local', 'Synced vault']);
  assert.match(seen.title, /Another account made this vault/);
  assert.equal(folder({ status: {} }).seen, false);
});

test('the button shows the worst state; a detached clone or a paused vault never raises it; a held vault asks for a look', () => {
  assert.deepEqual(keeping.summarize([]), { state: 'none', text: '', title: 'Nothing syncs with this computer yet' });
  const ok = folder({ status: { last_sync: now } });
  const busy = folder({ id: 'f2', status: { running: true } });
  const bad = clone({ status: { last_error: 'x', last_sync: now } });
  const off = clone({ workspace_id: 'w2', mode: 'off' });
  assert.deepEqual(keeping.summarize([ok, off]), { state: 'ok', text: 'Synced', title: '1 clone and 1 synced vault on this computer: synced' });
  assert.equal(keeping.summarize([ok, busy]).text, 'Syncing…');
  assert.equal(keeping.summarize([ok, busy, bad]).text, '1 problem');
  const held = folder({ id: 'f4', status: { held: { removals: 6, total: 10 } } });
  assert.equal(keeping.summarize([ok, busy, held]).text, 'Held');
  assert.equal(keeping.summarize([held, bad]).state, 'error');
  assert.equal(keeping.summarize([ok, folder({ id: 'f5', status: { offline: true } })]).text, 'Waiting', 'a disk unplugged only says so');
  const paused = folder({ id: 'f3', status: { last_error: 'x', paused_at: now } });
  assert.equal(keeping.summarize([ok, paused]).text, 'Synced');
  assert.deepEqual(keeping.summarize([off, paused]), { state: 'paused', text: 'Paused', title: '1 clone and 1 synced vault on this computer: paused' });
});

test('the paused states of a vault and a clone', () => {
  const paused = folder({ status: { last_sync: now, last_error: 'x', paused_at: now } });
  assert.equal(paused.paused, true);
  const running = folder({ status: { running: true, paused_at: now } });
  assert.equal(running.state, 'busy');
  assert.equal(running.paused, true, 'still paused once the round ends');
  assert.equal(folder({ status: { last_sync: now } }).paused, false);
  assert.equal(clone({ mode: 'off' }).paused, true);
  assert.equal(clone({ status: { last_sync: now } }).paused, false);
});

const link = (info) => keeping.zotero(local, { kind: 'zotero', workspace_id: 'w3', name: 'Papers', library: 'users/1', library_name: 'My Library', mode: 'two-way', ...info });

test('a Zotero link reads as the pill reads it: its error by kind, what waits on Zotero', () => {
  assert.equal(link({ mode: 'off' }).state, 'detached');
  assert.equal(link({ mode: 'off' }).paused, true);
  assert.equal(link({ status: { running: true, progress: { phase: 'first', done: 3, total: 10 } } }).text, 'First sync 3 / 10');
  assert.equal(link({ status: { last_error: 'x' }, conflicts_open: 1 }).text, '1 conflict');
  // a refused key's sentence says "refused": the clone's regex would call it unreachable
  const auth = link({ status: { last_error: 'Zotero refused the key: reconnect your account in Settings', error_kind: 'auth', last_sync: now } });
  assert.equal(auth.state, 'error');
  assert.equal(auth.text, 'Reconnect Zotero');
  assert.equal(link({ status: { last_error: 'getaddrinfo failed', error_kind: 'offline' } }).text, 'Zotero unreachable · your edits wait here');
  assert.equal(link({ library: 'groups/5', library_name: 'Lab', status: { last_error: 'x', error_kind: 'library_gone' } }).text, 'This key no longer reaches Lab');
  assert.equal(link({ status: { last_error: 'x', error_kind: 'busy' } }).text, 'Zotero is busy · retrying');
  assert.match(link({ status: { last_error: 'odd', error_kind: 'other', last_attempt: now } }).text, /^Sync problem · /);
  assert.equal(link({ status: {} }).text, 'Not synced yet');
  assert.equal(link({ status: { last_sync: now }, pending_local: true }).state, 'pending');
  const missing = link({ status: { last_sync: now, waiting: { files: { linked: 2, unavailable: 310 } } } });
  assert.equal(missing.state, 'waiting');
  assert.equal(missing.text, '312 PDFs not in Zotero Storage');
  assert.equal(link({ status: { last_sync: now, waiting: { files: { quota: 4 }, quota_until: now } } }).text, 'Zotero storage is full · 4 PDFs wait');
  assert.equal(link({ status: { last_sync: now, waiting: { files: { quota: 1 } } } }).text, 'Zotero storage is full · 1 PDF waits', 'its wait lifted by Sync now');
  assert.equal(link({ status: { last_sync: now, waiting: { files: { storage: 2 } } } }).text, 'Storage here is full · 2 PDFs wait');
  assert.equal(link({ status: { last_sync: now, waiting: { files: { no_access: 3 } } } }).text, '3 PDFs wait for file access');
  assert.equal(link({ status: { last_sync: now, waiting: { files: { failed: 1 } } } }).text, '1 PDF transfer failed');
  const ok = link({ status: { last_sync: now } });
  assert.equal(ok.state, 'ok');
  assert.equal(ok.where, 'Linked to Zotero · My Library');
  assert.equal(ok.key, 'zotero:l1:w3');
});

test('a Zotero link: annotations held for a look wait, those in the ordinary course do not', () => {
  const held = link({ status: { last_sync: now, waiting: { annotations: { n: 5, reasons: { 'the PDFs differ': 2, 'the PDF is not here yet': 3 } } } } });
  assert.equal(held.state, 'waiting');
  assert.equal(held.text, '2 annotations wait');
  assert.match(held.title, /the PDFs differ \(2\)\.$/);
  const course = link({ status: { last_sync: now, waiting: { annotations: { n: 3, reasons: { 'the PDF is not here yet': 3 } } } } });
  assert.equal(course.state, 'ok');
});

test('a Zotero link syncs on between two bounded rounds of a fill, and changes queued for later are not up to date', () => {
  assert.equal(link({ status: { running: true, forced: 'pull', progress: { phase: 'sync', done: 3, total: 10 } } }).text, 'Force pull 3 / 10');
  const between = link({ status: { last_sync: now, fill: { units: 50, bytes: 0 }, backlog: 7 }, conflicts_open: 1 });
  assert.equal(between.state, 'busy');
  assert.equal(between.text, 'Syncing · 7 left');
  assert.equal(link({ status: { last_sync: now, fill: { units: 50 }, backlog: 7, last_error: 'x', error_kind: 'offline' } }).state, 'error');
  const queued = link({ status: { last_sync: now, backlog: 2 } });
  assert.equal(queued.state, 'queued');
  assert.match(queued.text, /^2 changes queued · /);
  assert.equal(keeping.summarize([queued]).text, 'Changes queued');
  assert.equal(keeping.summarize([queued, link({ workspace_id: 'w4', status: { last_sync: now }, pending_local: true })]).state, 'pending');
});

test('a Zotero link: a dead key shows over the conflicts; edits that wait and an interrupted fill are queued, not syncing', () => {
  // conflicts left open, then the key revoked: every round fails, and only connecting again mends it
  const dead = link({ status: { last_sync: now, last_error: 'Zotero refused the key', error_kind: 'auth' }, conflicts_open: 12 });
  assert.equal(dead.state, 'error');
  assert.equal(dead.text, 'Reconnect Zotero');
  assert.equal(link({ status: { last_error: 'x', error_kind: 'library_gone' }, conflicts_open: 2 }).state, 'error');
  assert.equal(link({ status: { last_error: 'x', error_kind: 'offline' }, conflicts_open: 2 }).state, 'conflicts', 'Gamma retries that by itself');
  // pending_local counts a page whose notes wait for quiet, or a write backing off: queued with the backlog
  const waits = link({ on_change: true, pending_local: true, status: { last_sync: now, backlog: 1 } });
  assert.equal(waits.state, 'queued');
  assert.equal(link({ on_change: true, pending_local: true, status: { last_sync: now, backlog: 0 } }).state, 'busy', 'a write since the last round');
  // a fill a restart cut short asks for no round any more
  const cut = link({ status: { last_sync: now, fill: { units: 50 }, backlog: 7, interrupted: true } });
  assert.equal(cut.state, 'queued');
});

test('a server lists its Zotero links among what it keeps, and the button counts them', () => {
  const items = keeping.itemsOf(local, { mirrors: [], links: [], zotero: [{ workspace_id: 'w3', library: 'groups/5', library_name: 'Lab', status: { last_sync: now } }] }, servers);
  assert.deepEqual(items.map((i) => [i.kind, i.name]), [['zotero', 'Lab']]);
  assert.equal(keeping.itemsOf(local, { mirrors: [], links: [] }, servers).length, 0, 'a server with no list of links');
  const waiting = link({ status: { last_sync: now, waiting: { files: { linked: 1 } } } });
  assert.deepEqual(keeping.summarize([waiting]), { state: 'waiting', text: 'Waiting', title: '1 Zotero link on this computer: waiting' });
  const ok = folder({ status: { last_sync: now } });
  const off = clone({ workspace_id: 'w2', mode: 'off' });
  assert.equal(keeping.summarize([ok, off, link({ status: { last_sync: now } })]).title, '1 clone, 1 Zotero link and 1 synced vault on this computer: synced');
  assert.equal(keeping.summarize([link({ status: { last_error: 'x', error_kind: 'auth' } }), ok]).state, 'error');
});

test('the summary counts vaults of both scopes together; a paused one never raises it', () => {
  const ok = folder({ status: { last_sync: now } });
  const sent = file({ status: { last_sync: now, last_sent: now } });
  const cl = clone({ status: { last_sync: now } });
  const zl = link({ status: { last_sync: now } });
  assert.equal(keeping.summarize([cl, zl, ok, sent]).title, '1 clone, 1 Zotero link and 2 synced vaults on this computer: synced');
  const paused = file({ id: 'p3', status: { last_error: 'x', paused_at: now } });
  assert.equal(keeping.summarize([ok, paused]).text, 'Synced');
  assert.equal(keeping.summarize([paused]).state, 'paused');
  assert.equal(keeping.summarize([ok, file({ id: 'p4', status: { waiting: 'still being written', last_sync: now } })]).text, 'Not synced yet');
});

// The dialog's sentence for `preview` and the switches.
const say = (preview, more = {}) => keeping.firstRound({ path: 'D:\\Papers', folder: 'Papers', preview, pull: true, push: 'off', ...more });

test('the dialog says what the first round does: pull into the directory, push from it, or both', () => {
  const pages = { notes: 0, pdfs: 0, pages: 12, empty: true, marker: null, refusal: '' };
  assert.deepEqual(say(pages), { ok: true,
    text: 'Pull 12 pages into D:\\Papers; nothing there is pushed. Gamma adds .gamma/ there, to know the directory again, and nothing else of its own.' });
  assert.match(say(pages, { push: 'pdfs' }).text, /^Pull 12 pages into D:\\Papers; PDFs you change there are pushed back\. Gamma adds \.gamma\//);
  // every vault made now is the engine's: none keeps a list of its files beside them
  for (const pull of [true, false]) {
    for (const push of keeping.PUSH) assert.doesNotMatch(say(pages, { pull, push }).text, /gamma-sync/, `${pull}:${push}`);
  }
  const files = { notes: 3, pdfs: 2, pages: 0, untracked: 4, marker: null, refusal: '' };
  assert.deepEqual(say(files, { push: 'everything', newFolder: true }), { ok: true,
    text: 'Push 3 notes and 2 PDFs from D:\\Papers into a new folder “Papers”; from then on both sides are kept in step. ' +
      'Gamma adds .gamma/ there, to know the directory again, and nothing else of its own. 4 other files stay untracked.' });
  assert.match(say(files, { push: 'everything', pull: false }).text, /^Push 3 notes and 2 PDFs from D:\\Papers into “Papers”; nothing is ever pulled into D:\\Papers\. Gamma adds \.gamma\//);
  assert.match(say(files, { push: 'pdfs', pull: false }).text, /^Push the 2 PDFs of D:\\Papers into “Papers”; nothing is ever pulled/);
  assert.match(say({ pages: 12 }, { push: 'everything' }).text, /^Pull 12 pages into D:\\Papers; from then on both sides are kept in step\./);
  assert.match(say(files).text, /The 5 notes and PDFs already there are left as they are\./, 'a vault that does not push everything makes no page of them');
  assert.match(say(files, { push: 'pdfs', newFolder: true }).text, /The 5 notes and PDFs already there are left as they are\./, 'made from the directory too');
  assert.doesNotMatch(say(files, { push: 'everything' }).text, /left as they are/);
  assert.match(say({ untracked: 1 }).text, /1 other file stays untracked\.$/);
});

test('the dialog shuts Sync for a vault that would be refused, and says why', () => {
  assert.deepEqual(say({}, { pull: false, push: 'off' }), { ok: false, text: 'A vault pulls, pushes, or both: turn one of them on.' });
  assert.equal(say({}, { remote: true, push: 'everything' }).ok, false);
  assert.deepEqual(say({}, { remote: true, pull: false, push: 'pdfs' }), { ok: false, text: keeping.REMOTE_RULE }, 'the rule the vault\'s switches keep to');
  assert.equal(keeping.REMOTE_RULE, 'A vault of another server pulls, and pushes PDFs at most: a work folder there is made from an empty folder.');
  assert.equal(say({}, { remote: true, push: 'pdfs' }).ok, true, 'a remote\'s folder may push its PDFs');
  assert.deepEqual(say({}, { whole: true, push: 'everything' }), { ok: false, text: keeping.WHOLE_RULE }, 'the server\'s words');
  assert.equal(say({ notes: 2 }, { whole: true, push: 'pdfs' }).ok, true, 'the whole library pulls, and pushes PDFs');
  assert.equal(folder({ folder_id: 'root', status: {} }).whole, true);
  assert.deepEqual(say({ refusal: 'D:\\Papers holds the marker of the synced vault E:\\Papers: was it copied?' }),
    { ok: false, text: 'D:\\Papers holds the marker of the synced vault E:\\Papers: was it copied?' });
  assert.match(say({ pages: 3, notes: 1 }, { push: 'everything' }).text, /^Both the folder and the directory hold files/);
  assert.equal(say({ pages: 3, notes: 1 }, { push: 'everything' }).ok, false);
  assert.equal(say({ pages: 0, notes: 1 }, { push: 'everything', newFolder: true }).ok, true);
});

test('a directory that was a vault no link holds is taken up again; one with its marker writes nothing of its own', () => {
  assert.match(say({ marker: { id: 'v1', link_id: '' } }, { push: 'everything', newFolder: true }).text, /D:\\Papers was a synced vault before: it is taken up again\.$/);
  assert.doesNotMatch(say({ marker: { id: 'v1', link_id: 'l9' } }, { push: 'everything', newFolder: true }).text, /\.gamma\//);
});

test('removing a vault: the pages stay, the PDFs read in place are stored first; the files stay or go to its trash', () => {
  const v = folder({ status: { last_sync: now, folder_path: ['Papers'] } });
  const q = keeping.removalAsk(v, { pages: 12, in_place: 2, bytes: 3_400_000 });
  assert.equal(q.message, 'Remove the synced vault “Papers”?');
  assert.match(q.detail, /^Its 12 pages stay in Gamma; 2 PDFs read where they are \(3\.4 MB\) are stored in Gamma first\. /);
  assert.match(q.detail, /\.gamma\/trash; files you added or changed stay\.$/);
  assert.deepEqual(q.choices, ['keep', 'trash', null]);
  assert.equal(q.buttons.length, q.choices.length);
  assert.match(keeping.removalAsk(v, { pages: 1 }).detail, /^Its page stays in Gamma\. /);
  assert.match(keeping.removalAsk(v, { pages: 0 }).detail, /^It holds no pages yet\. /);
  const away = keeping.removalAsk(folder({ status: { offline: true } }), { pages: 3 });
  assert.deepEqual(away.choices, ['keep', null], 'a directory not there is never touched');
  assert.match(away.detail, /is not there now, so nothing on disk is touched\.$/);
  // a stopped vault (its marker gone or another's): the server touches nothing there, so no trash is offered
  const stopped = keeping.removalAsk(folder({ status: { stopped: 'The marker is gone.' } }), { pages: 3 });
  assert.deepEqual(stopped.choices, ['keep', null]);
  assert.deepEqual(stopped.buttons, ['Remove the vault', 'Cancel']);
  assert.match(stopped.detail, /is no longer this vault's directory, so nothing in it is touched\.$/);
  const f = keeping.removalAsk(file({ status: {} }), { pages: 1 });
  assert.deepEqual(f.choices, ['keep', null]);
  assert.match(f.detail, /^main\.pdf is no longer pushed to the page\. The file and the page stay as they are\.$/);
});

test('removing a work folder says both numbers: its files not in the library go to Recently deleted, those in it stay', () => {
  const w = folder({ push: 'everything', status: { last_sync: now, folder_path: ['Paper'] } });
  assert.match(keeping.removalAsk(w, { pages: 5, files: 3, in_library: 2, in_place: 1, bytes: 2_000 }).detail,
    /^Its 3 files not in the library go to Recently deleted; its 2 pages in the library stay; 1 PDF read where it is \(2 KB\) is stored in Gamma first\. /);
  assert.match(keeping.removalAsk(w, { pages: 1, files: 1, in_library: 0 }).detail, /^Its file not in the library goes to Recently deleted\. /);
  assert.match(keeping.removalAsk(w, { pages: 2, files: 0, in_library: 2 }).detail, /^Its 2 pages stay in Gamma\. /);
});

test('a force pull or push and going on are counted and asked first', () => {
  const v = folder({ status: { last_sync: now, folder_path: ['Papers'], kept: [{ path: 'a.md', why: 'changed', force: true }] } });
  const pull = keeping.forceAsk(v, 'pull', { pages: 12 });
  assert.equal(pull.message, 'Force pull “Papers”?');
  assert.match(pull.detail, /^Every file of its 12 pages in C:\\Papers\\Quantum becomes what Gamma holds, the 1 file you changed on this computer among them\./);
  assert.deepEqual(pull.buttons, ['Force pull', 'Cancel']);
  const push = keeping.forceAsk(v, 'push', { pages: 1 });
  assert.match(push.detail, /^Each of its 1 page becomes what its file in C:\\Papers\\Quantum says/);
  assert.deepEqual(push.buttons, ['Force push', 'Cancel']);
  const on = keeping.goOnAsk(folder({ status: { held: { removals: 7, total: 12 }, folder_path: ['Papers'] } }));
  assert.equal(on.message, '7 files of 12 in “Papers” went from this computer since the last sync.');
  assert.deepEqual(on.buttons, ['Go on', 'Cancel']);
});

test('a round asked for is said in the words: pushed, pulled files, conflicts; stopped, held or a problem', () => {
  const v = folder({ status: { folder_path: ['Papers'] } });
  assert.equal(keeping.roundSaid(v, { counts: { added: 2, unchanged: 9, kept: 1, sent: 3, conflicts: 1 } }), 'Papers is up to date: 2 added, 3 pushed, 1 conflict.');
  assert.equal(keeping.roundSaid(v, { counts: { unchanged: 4 } }), 'Papers is up to date.');
  assert.equal(keeping.roundSaid(v, { stopped: 'its marker is gone', last_error: 'its marker is gone' }), 'Papers: its marker is gone');
  assert.match(keeping.roundSaid(v, { held: { removals: 7, total: 12 } }), /^Papers is held: 7 files of 12 went from this computer\./);
  assert.equal(keeping.roundSaid(v, { last_error: 'disk full' }), 'Papers: disk full');
  const f = file({ status: {} });
  assert.equal(keeping.roundSaid(f, { counts: { sent: 1 } }), 'main.pdf was pushed to Gamma.');
  assert.equal(keeping.roundSaid(f, { counts: {}, waiting: 'still being written' }), 'main.pdf is not pushed yet: still being written.');
  assert.equal(keeping.roundSaid(f, { counts: {} }), 'main.pdf: nothing new to push.');
});
