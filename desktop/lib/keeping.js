// What this computer keeps of Gamma, read as one picture for the bar's
// sync button and its panel (docs/architecture.md "The sync panel"): the
// clones (a local server's mirrors of remote workspaces),
// the Zotero links (a local server's workspaces kept in step with a Zotero
// library) and the synced vaults (a local server's folder links: a folder
// with a directory, or a page with one file). Pure: main.js reads the rows
// off each running local server and hands them here, and the bar only
// renders what comes back. The sentences the shell says about a vault (the
// dialog's first round, the questions before a force or a removal, a
// round's outcome) are made here too, in the vocabulary of
// docs/research/vaults.md "The words": Gamma's server is the remote, the
// directory or the file is local, pull is Gamma to disk, push is disk to
// Gamma, sync is both.
//
// A clone and a Zotero link read as Gamma's own sync pill reads them
// (mirrorState in frontend/src/collaboration/MirrorPopover.jsx, a link's
// errors and waiting files in collaboration/syncLinks.js): the same states,
// checked in the same order, so the bar and the page never disagree.
// Change both.

// How much a state asks of the user, least first; the button shows the
// worst. A detached clone or link or a paused vault is the user's own
// choice and never raises it; PDFs that wait on Zotero's side (a full
// storage, files Zotero does not serve), a link's changes queued for a
// later round and a vault whose directory is not there only say so. A vault
// held by its guard waits for the user's word.
const RANK = { ok: 0, new: 1, waiting: 1, queued: 1, pending: 2, busy: 3, conflicts: 4, held: 4, error: 5 };
const QUIET = ['detached', 'paused'];

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

// "a", "a and b", "a, b and c".
const listed = (parts) => (parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}` : parts.join(''));

const baseName = (p) => String(p || '').split(/[\\/]/).pop();

// A stamp as the bar shows it: the time today, the day before that.
function clock(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return '';
  return d.toDateString() === new Date().toDateString()
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

// A server's name for an address: the registered remote's, else its host.
function nameOf(url, servers) {
  let origin = '';
  try { origin = new URL(url).origin; } catch { return url || ''; }
  const known = (servers || []).find((s) => s.type === 'remote' && s.url === origin);
  return known ? known.name : new URL(origin).host;
}

// A clone of local server `server`: one row of its `GET /api/mirrors`.
// `paused`: detached, which the panel's pause and resume do.
function clone(server, info, servers) {
  const s = info.status || {};
  const p = s.progress || {};
  const item = {
    kind: 'clone', key: `clone:${server.id}:${info.workspace_id}`, server: server.id,
    id: info.workspace_id, name: info.name || info.remote_name || 'Clone',
    where: `Clone of ${info.remote_name || 'a workspace'} on ${nameOf(info.remote_url, servers)}`,
    paused: Boolean(info.detached || info.mode === 'off'),
  };
  if (item.paused) {
    return { ...item, state: 'detached', text: 'Detached', title: 'Nothing is pulled or pushed until you resume it; what both sides did meanwhile merges then.' };
  }
  if (s.running) {
    return { ...item, state: 'busy', text: `${p.first ? 'Cloning' : 'Syncing'}${p.total ? ` ${p.done} / ${p.total}` : '…'}`, title: 'A round is running.' };
  }
  if (info.conflicts_open) {
    return { ...item, state: 'conflicts', text: plural(info.conflicts_open, 'conflict'), title: 'Blocks both sides changed wait for a decision: open the clone to settle them.' };
  }
  if (s.last_error) {
    const unreachable = /cannot reach|timed out|refused|unreachable/i.test(s.last_error);
    const when = clock(s.last_attempt || s.last_sync);
    return { ...item, state: 'error', text: `${unreachable ? 'Remote unreachable' : 'Sync problem'}${when ? ` · ${when}` : ''}`,
      title: `${s.last_error}${unreachable ? ' Your edits stay here and are pushed once the remote is reachable again.' : ''}` };
  }
  if (!s.last_sync) {
    return { ...item, state: 'new', text: s.interrupted ? 'Interrupted · resuming' : 'Not cloned yet',
      title: s.interrupted ? 'The clone was interrupted; it continues in a moment.' : 'The clone starts in a moment.' };
  }
  if (info.pending_local) {
    return info.on_change
      ? { ...item, state: 'busy', text: 'Syncing your edits…', title: 'A round follows every edit; your changes are on their way.' }
      : { ...item, state: 'pending', text: 'Local edits not synced yet', title: 'Sync sends them, or the next automatic round does.' };
  }
  return { ...item, state: 'ok', text: `Up to date · ${clock(s.last_sync)}`, title: `Up to date since ${clock(s.last_sync)}.` };
}

// A Zotero library's name: My Library for the personal one, a group by its own.
const libraryOf = (info) => (String(info.library || '').startsWith('users/') ? 'My Library' : info.library_name || 'a Zotero library');

// A count as the pill writes it ("2,000").
const count = (v) => Number(v || 0).toLocaleString();

// The annotations' reasons for waiting that are a link's ordinary course
// (the engine's sentences: a PDF not downloaded yet, one being placed in
// its new file); the others ask for a look.
const IN_COURSE = ['the PDF is not here yet', 'being placed in the new PDF'];

// What waits on something outside Gamma (`status.waiting`) as the pill
// words it, or null: a full storage at Zotero, then here, a key without
// file access, the PDFs Zotero does not serve, failed transfers, then
// annotations held for a look.
function waitingOf(s) {
  const w = s.waiting || {};
  const f = w.files || {};
  const num = (v) => (Number.isFinite(v) ? v : 0);
  const pdfs = (n) => `${count(n)} ${n === 1 ? 'PDF waits' : 'PDFs wait'}`;
  const given = w.annotations && w.annotations.reasons;
  const reasons = given && typeof given === 'object' && !Array.isArray(given)
    ? Object.entries(given).filter(([why, n]) => !IN_COURSE.includes(why) && num(n) > 0) : [];
  const asking = reasons.reduce((sum, [, n]) => sum + n, 0);
  const missing = num(f.linked) + num(f.unavailable);
  if (num(f.quota)) {
    return { text: `Zotero storage is full · ${pdfs(num(f.quota))}`, title: 'Zotero\'s storage is full, so the PDFs it lacks are not sent; Gamma tries again later.' };
  }
  if (num(f.storage)) {
    return { text: `Storage here is full · ${pdfs(num(f.storage))}`, title: 'This server\'s storage is full, so PDFs from Zotero wait; they come once space frees.' };
  }
  if (num(f.no_access)) {
    return { text: `${pdfs(num(f.no_access))} for file access`, title: 'Your Zotero key cannot read this library\'s files: connect again in Gamma (Settings › Account & sync) with a key that has file access.' };
  }
  if (missing) {
    return { text: `${count(missing)} ${missing === 1 ? 'PDF' : 'PDFs'} not in Zotero Storage`, title: 'Linked files and files not in Zotero Storage (WebDAV) cannot be downloaded; they and their annotations stay in Zotero.' };
  }
  if (num(f.failed)) {
    return { text: `${count(num(f.failed))} ${num(f.failed) === 1 ? 'PDF transfer' : 'PDF transfers'} failed`, title: 'Gamma tries a few times, then waits until the file changes in Zotero.' };
  }
  if (asking) {
    return { text: `${count(asking)} ${asking === 1 ? 'annotation waits' : 'annotations wait'}`,
      title: `Held for a look in Gamma: ${reasons.map(([why, n]) => `${why} (${count(n)})`).join(', ')}.` };
  }
  return null;
}

// What a link's round at work is, in the pill's words: a force, the first
// fill, or a sync; and whether the next round follows at once (the last
// stopped at its bounds with work due, `status.fill`; not once a restart
// cut it short, `status.interrupted`).
const runLabel = (s) => (s.forced === 'pull' ? 'Force pull' : s.forced === 'push' ? 'Force push'
  : s.progress && s.progress.phase === 'first' ? 'First sync' : 'Syncing');
const following = (s) => Boolean(s.fill) && Number(s.backlog) > 0 && !s.running && !s.last_error && !s.interrupted;
// A failed round only connecting again mends: it reads before the conflicts.
const HALTING = ['auth', 'not_connected', 'library_gone'];

// A Zotero link of local server `server`: one row of its `GET
// /api/zotero/links`, its error read by `status.error_kind`, never by the
// sentence. `paused`: detached, which the panel's pause and resume do.
function zotero(server, info) {
  const s = info.status || {};
  const p = s.progress || {};
  const library = libraryOf(info);
  const item = {
    kind: 'zotero', key: `zotero:${server.id}:${info.workspace_id}`, server: server.id,
    id: info.workspace_id, name: info.name || library, where: `Linked to Zotero · ${library}`,
    paused: Boolean(info.detached || info.mode === 'off'),
  };
  if (item.paused) {
    return { ...item, state: 'detached', text: 'Detached', title: 'Nothing goes to or comes from Zotero until you resume it; what both sides did meanwhile merges then.' };
  }
  if (s.running) {
    return { ...item, state: 'busy', text: `${runLabel(s)}${p.total ? ` ${count(p.done)} / ${count(p.total)}` : '…'}`, title: 'A round is running.' };
  }
  if (following(s)) {
    return { ...item, state: 'busy', text: `${runLabel(s)} · ${count(s.backlog)} left`, title: 'A round has just ended; the next one follows at once.' };
  }
  if (info.conflicts_open && !(s.last_error && HALTING.includes(s.error_kind))) {
    return { ...item, state: 'conflicts', text: plural(info.conflicts_open, 'conflict'), title: 'Changes both sides made wait for a decision: open the workspace to settle them.' };
  }
  if (s.last_error) {
    const when = clock(s.last_attempt || s.last_sync);
    const text = {
      offline: 'Zotero unreachable · your edits wait here',
      auth: 'Reconnect Zotero',
      not_connected: 'Reconnect Zotero',
      library_gone: `This key no longer reaches ${library}`,
      busy: 'Zotero is busy · retrying',
    }[s.error_kind] || `Sync problem${when ? ` · ${when}` : ''}`;
    const more = {
      offline: ' Your edits stay here and go to Zotero once it answers again.',
      auth: ' Connect your Zotero account again in Gamma: Settings › Account & sync.',
      not_connected: ' Connect your Zotero account in Gamma: Settings › Account & sync.',
      busy: ' Gamma tries again by itself.',
    }[s.error_kind] || '';
    return { ...item, state: 'error', text, title: `${s.last_error}${more}` };
  }
  if (!s.last_sync) {
    return { ...item, state: 'new', text: s.interrupted ? 'Interrupted · resuming' : 'Not synced yet',
      title: s.interrupted ? 'The first sync was interrupted; it continues in a moment.' : 'The first sync starts in a moment.' };
  }
  const backlog = Number(s.backlog) || 0;
  // (pending_local counts a page queued whatever it waits for: with a backlog, those wait with the queue)
  if (info.pending_local && !backlog) {
    return info.on_change
      ? { ...item, state: 'busy', text: 'Syncing your edits…', title: 'A round follows every edit; your changes are on their way.' }
      : { ...item, state: 'pending', text: 'Local edits not synced yet', title: 'Sync sends them, or the next automatic round does.' };
  }
  const since = backlog > 0 ? `Last round ${clock(s.last_sync)}.` : `Up to date since ${clock(s.last_sync)}.`;
  const waiting = waitingOf(s);
  if (waiting) return { ...item, state: 'waiting', text: waiting.text, title: `${since} ${waiting.title}` };
  if (backlog > 0) {
    return { ...item, state: 'queued', text: `${count(backlog)} ${backlog === 1 ? 'change' : 'changes'} queued · ${clock(s.last_sync)}`,
      title: `${since} ${backlog === 1 ? 'A queued change waits' : `${count(backlog)} queued changes wait`} for a later round: a retry, a PDF on its way, or a page still being edited.` };
  }
  return { ...item, state: 'ok', text: `Up to date · ${clock(s.last_sync)}`, title: `Up to date since ${clock(s.last_sync)}.` };
}

// A byte count as the frontend's fmtBytes writes it ("4.2 MB").
function size(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1073741824) return `${(n / 1048576).toFixed(1)} MB`;
  return `${(n / 1073741824).toFixed(1)} GB`;
}

// A directory vault's round as it runs (`status.progress`,
// gamma/vault/sync.py Round._report: the files push takes and then the
// pages pull looks at, the bytes of the PDFs push takes, the file at hand):
// "Syncing 30 / 120", or "Syncing…" before it knows, or when its round says
// nothing of itself.
function folderRun(p) {
  if (!p || !(Number(p.total) > 0)) return { text: 'Syncing…', title: 'A round is running.' };
  const b = p.bytes || {};
  const pdfs = Number(b.total) > 0 ? ` (PDFs ${size(Math.min(Number(b.done) || 0, Number(b.total)))} of ${size(Number(b.total))} pushed)` : '';
  return { text: `Syncing ${count(p.done)} / ${count(p.total)}`,
    title: `A round is running: ${count(p.done)} of ${count(p.total)} pushed or pulled${pdfs}${p.path ? `, now ${p.path}` : ''}.` };
}

// What a vault pushes, as the API words it: nothing, its PDFs, or
// everything (notes, PDFs, new files, renames and deletions). A word, never
// a flag: `Boolean('off')` is true.
const PUSH = ['off', 'pdfs', 'everything'];
const pushWord = (word) => (PUSH.includes(word) ? word : 'off');

const capital = (text) => text.charAt(0).toUpperCase() + text.slice(1);
const isAre = (n) => (n === 1 ? 'is' : 'are');

// A size as a question says it.
const bytesText = (n) => (n >= 1e6 ? `${Math.round(n / 1e5) / 10} MB` : `${Math.max(1, Math.round((n || 0) / 1e3))} KB`);

// A synced vault of local server `server`: one row of its `GET
// /api/folder-links`, a folder with a directory (`scope` folder) or a page
// with one file (`scope` file, which only pushes, its PDF). `where` is in the
// row's tooltip, `path ⇄ remote · folder`: the local side, the server
// the vault belongs to by its name, and the folder (or the page). Of the
// files the last round kept, `changed` counts those a force pull replaces
// (their entry's `force`: changed here, never pushed); `left` counts the
// rest (a PDF a push could not deliver, a file in the way) and the names
// its scan refused (`status.refused`: two names that differ only by case,
// a name Windows refuses, a link), which never become pages. `whole`: a
// vault of the whole library. `work`: a work folder (a folder of the
// server's own that pushes everything, docs/dev/work_folders.md), with its
// library switch (`library`) and whether its files wait to be chosen in
// Gamma (`choosing`).
//
// `roles` is the workspaces the server's account may open, as its `GET
// /api/session` lists them (`[{id, role}]`; null: not read). The server
// lists a vault to the account that made it even in a workspace it is no
// longer in (`away`): its rounds are refused there, but its maker may still
// pause it, locate it and remove it. A vault with no `dest` is another
// account's, listed to a viewer of its workspace (`seen`): where its files
// lie is its maker's reading, and the server refuses the viewer every
// action on it. `mine`: the account made it (the list's own word), the one
// who may turn on an agent for a work folder.
//
// A work folder's agent (docs/dev/local_agents.md): the agent on this
// computer that answers its Sends (`agent`, a name, "" for none, with its
// `agentLabel`), whether it is ready (`agentReady`: null while the server
// still checks) and why not (`agentProblem`), and what it does now
// (`agentRunning`: the detail of its run while one works, else "";
// `agentChat` when that run is a turn of a page's chat, not a Send), and
// what it may run beyond its confinement (`agentRuns`: "ask", it may ask in
// the chat, or "never"), as the link's `agent` says them. `agents` is what
// the server finds on this computer (`GET /api/local-agents`' `agents`;
// null: a server that runs no agents, which then offers none).
//
// A vault of another server also says whether this computer is shown there
// (`computer`, computerLine): `computers` is `{ links, name }`, the server's
// computer links (`GET /api/computer-links`) and this computer's name; null:
// not read, and nothing is said.
function vault(server, link, servers, roles = null, agents = null, computers = null) {
  const s = link.status || {};
  const scope = link.kind === 'file' ? 'file' : 'folder';
  const names = s.folder_path || [];
  const remote = link.remote_url ? nameOf(link.remote_url, servers) : server.name;
  const title = link.title || s.title || '';
  const folder = scope === 'file' ? title : link.folder_id === 'root' ? 'Whole library' : names.join(' / ');
  const kept = s.kept || [];
  const changed = kept.filter((k) => k.force).length;
  const left = [...kept.filter((k) => !k.force), ...(s.refused || [])];
  const item = {
    kind: 'vault', scope, key: `vault:${server.id}:${link.id}`, server: server.id, id: link.id,
    name: (scope === 'file' ? title || baseName(link.dest)
      : link.folder_id === 'root' ? 'Whole library' : names.length ? names[names.length - 1] : baseName(link.dest)) || 'Synced vault',
    dest: link.dest || '', remote, remoteUrl: link.remote_url || '', folder,
    where: `${link.dest ? `${link.dest} ⇄ ` : ''}${remote}${folder ? ` · ${folder}` : ''}`,
    pull: scope === 'folder' && Boolean(link.pull), push: scope === 'file' ? 'pdfs' : pushWord(link.push),
    paused: Boolean(s.paused_at), stopped: s.stopped || '', offline: Boolean(s.offline), held: s.held || null,
    changed, left: left.length, untracked: s.untracked || 0,
    away: Boolean(roles && !link.remote_url && !roles.some((w) => w.id === link.workspace_id)), seen: !link.dest,
    whole: scope === 'folder' && link.folder_id === 'root',
    work: scope === 'folder' && !link.remote_url && link.folder_id !== 'root' && pushWord(link.push) === 'everything',
    library: Boolean(link.library), choosing: Boolean(link.choosing), mine: Boolean(link.mine),
  };
  const agent = link.agent || null;
  const run = agent && agent.running;
  Object.assign(item, {
    agent: (agent && agent.name) || '', agentLabel: agent ? agent.label || AGENT_LABELS[agent.name] || agent.name : '',
    agentReady: agent ? (agent.ready === undefined ? null : agent.ready) : null, agentProblem: (agent && agent.problem) || '',
    agentRunning: run ? String(run.detail || 'Working') : '', agentChat: Boolean(run && run.kind === 'chat'),
    agentRuns: agent && agent.runs === 'ask' ? 'ask' : 'never',
    agents: item.work && Array.isArray(agents) ? agentChoices(agents) : null,
  });
  item.computer = link.remote_url && !item.seen && computers && Array.isArray(computers.links)
    ? computerLine(item, link, computers) : null;
  const stated = scope === 'file' ? fileState(item, s) : folderState(item, s, left);
  return item.seen ? { ...stated, title: `${stated.title} ${SEEN}` } : stated;
}

const SEEN = 'Another account made this vault: it, or an editor of the workspace, acts on it.';

const originOf = (url) => {
  try { return new URL(url).origin; } catch { return url || ''; }
};

// What a computer link's state reads as after "Shown on NAS as TIM-PC ·",
// and its tooltip (`remote`, the server's name; `name`, this computer's;
// `who`, the account there that sees it).
const COMPUTER_STATES = {
  connecting: ['connecting', (remote) => `This computer is announcing itself to ${remote}.`],
  connected: ['connected', (remote, name, who) => `Only ${who} sees on ${remote}, in any window, that this vault is kept on ${name}, whether it is online, and which assistants are here. No path is sent.`],
  refused: ['refused this computer\'s token', (remote) => `${remote} refused the token this computer shows itself with: it was revoked there, or it expired. Stop showing, then Show this computer there, to hand it a new one.`],
  older: ['older than this app', (remote) => `${remote} is older than this app, so it cannot show this computer: update it.`],
  unreachable: ['not reachable', (remote) => `${remote} does not answer now; this computer tries again by itself.`],
};

// Whether this computer is shown on the server a vault of another server
// syncs with, as the vault's view says it: the host's computer link of that
// server and workspace (`computers.links`, `{id, remote_url, workspace_id,
// account, username, status: {state, error, at}}`), whose channel tells that
// server, for the vault's maker alone, that the vault is kept on this
// computer (`computers.name`), whether it is online and which assistants are
// here, never a path. `{ state, text, title, action, button }`: the line, its
// tooltip, and the view's action, `hide-computer` (Stop showing) with a link,
// `show-computer` (Show this computer there) with none; `state` is the
// link's, or `none`.
function computerLine(item, link, computers) {
  const name = computers.name || 'this computer';
  const shown = computers.links.find((c) => c && originOf(c.remote_url) === originOf(link.remote_url) && c.workspace_id === link.workspace_id);
  if (!shown) {
    return { state: 'none', text: `Not shown on ${item.remote}`, action: 'show-computer', button: 'Show this computer there',
      title: `Show this computer there, and you see on ${item.remote}, in any window, that this vault is kept on ${name}, whether it is online, and which assistants are here. No path is sent.` };
  }
  const s = shown.status || {};
  const state = Object.prototype.hasOwnProperty.call(COMPUTER_STATES, s.state) ? s.state : 'connecting';
  const [words, why] = COMPUTER_STATES[state];
  const who = shown.username || 'your account';
  return { state, text: `Shown on ${item.remote} as ${name} · ${words}`, action: 'hide-computer', button: 'Stop showing',
    title: `${why(item.remote, name, who)}${s.error && state !== 'connected' ? ` ${said(String(s.error))}` : ''}` };
}

// What showing this computer on server `remote` came to when it failed, for
// the bar: a server older than this app refuses a computer's token as it is
// minted (`minting`: its scopes do not know it, 422) or at the host's check
// (409); otherwise the server's reason, or the error's.
function computerFailed(remote, err) {
  const e = err || {};
  const detail = e.body && typeof e.body.detail === 'string' ? e.body.detail : '';
  if (e.status === 409 || (e.minting && e.status === 422)) {
    return `${remote} is older than this app: it cannot show this computer.`;
  }
  return `${remote} cannot show this computer: ${detail || String(e.message || e)}`;
}

// What showing this computer (`name`) on server `remote` came to, or
// stopping (`shown` false), for the bar.
function computerSaid(remote, name, shown) {
  return shown ? `${name} is shown on ${remote}: only your account there sees it, with the vaults it keeps and the assistants here.`
    : `${name} is no longer shown on ${remote}; its token there is revoked.`;
}

// What the bar says when this computer (`name`) stopped showing itself on
// server `remote` but its token there could not be revoked (the server not
// reached, the window signed out of it or into another account): the token
// is left for the person to remove there.
function computerStranded(remote, name) {
  return `${name} is no longer shown on ${remote}, but its token there could not be revoked: remove “Gamma desktop: ${name}” in ${remote}'s Settings → Integrations, signed in as the account that made it.`;
}

// What the bar says when a vault of server `remote` is made while the window
// is signed in there as another account than the one this computer is shown
// to (`who`): the link stays theirs, never handed to another account.
function computerOtherAccount(remote, name, who) {
  return `${name} is shown on ${remote} to ${who}, not to the account signed in there now: sign in there as ${who}, or Stop showing first.`;
}

// The agents a work folder may have, by name, in the order the panel
// offers them (the server's catalog, gamma/local_agents/catalog.py).
const AGENT_LABELS = { codex: 'Codex', claude: 'Claude Code' };

// What the panel offers of the agents the server found: each known one,
// `{name, label, found, ready, problem}`, one the server did not list read as
// not on this computer.
function agentChoices(agents) {
  return Object.entries(AGENT_LABELS).map(([name, label]) => {
    const a = agents.find((x) => x && x.name === name);
    if (!a) return { name, label, found: false, ready: false, problem: `${label} is not on this computer.` };
    return { name, label: a.label || label, found: a.found === undefined ? null : a.found,
      ready: a.ready === undefined ? null : a.ready, problem: a.problem || '' };
  });
}

// A vault of a workspace its maker is no longer in, as its row says it.
function awayState(item) {
  const free = item.scope === 'file' ? 'the file; it stays as it is' : 'its directory; its files stay as they are';
  return { ...item, state: 'error', text: 'No access to its workspace',
    title: `Your account is no longer a member of the workspace this vault syncs with, so nothing is pulled or pushed. Remove the vault to free ${free}.` };
}

// Which way a vault goes, said after its state.
function directions(item) {
  if (item.pull && item.push === 'everything') return ' Notes, PDFs, new files, renames and deletions are pulled and pushed.';
  if (item.pull && item.push === 'pdfs') return ' Gamma\'s changes are pulled; PDFs you change here are pushed.';
  if (item.pull) return ' Gamma\'s changes are pulled; nothing here is pushed.';
  if (item.push === 'everything') return ' What changes here is pushed; nothing is pulled.';
  return ' Its PDFs are pushed; nothing is pulled.';
}

// A sentence the server wrote, as the tooltip ends one.
const said = (text) => `${capital(text)}${/[.!?]$/.test(text) ? '' : '.'}`;

// A directory's vault, worst first: a work folder whose files are not chosen
// yet (Choose files… opens its dialog in Gamma), a round running, its agent
// answering a Send or working in a page's chat (busy: the files may change
// meanwhile), paused,
// stopped (its marker gone or another's: Locate… says where it is), held by
// its guard (Go on), its directory not there, a problem, never synced, up to
// date.
// Up to date, it also says what its last round left (`left`: not synced),
// why its push was turned off when it was taken onto synced vaults
// (`push_off`), the pages filed in it that an older vault beside it keeps
// (`shared`), and when a folder kept before became a vault (`upgraded_at`).
function folderState(item, s, left) {
  const counts = s.counts || {};
  if (item.choosing) {
    return { ...item, state: 'pending', text: 'Choosing files',
      title: 'Nothing is tracked yet: Choose files… opens its tracking dialog in Gamma, and the first round runs once you save it.' };
  }
  if (s.running) return { ...item, state: 'busy', ...folderRun(s.progress) };
  if (item.paused) {
    return { ...item, state: 'paused', text: `Paused · ${clock(s.paused_at)}`, title: 'Nothing is pulled or pushed until you resume it; Sync now runs one round.' };
  }
  if (item.away) return awayState(item);
  // A vault's own problem keeps its row while its agent works: the agent's run is said in the title.
  const meanwhile = item.agentRunning
    ? ` ${item.agentLabel} is working ${item.agentChat ? 'in the chat' : 'on a Send'} meanwhile.` : '';
  if (item.stopped) return { ...item, state: 'error', text: 'Stopped', title: `${item.stopped} Locate… tells Gamma where the directory is now.${meanwhile}` };
  if (item.held) {
    const { removals = 0, total = 0 } = item.held;
    return { ...item, state: 'held', text: `Held · ${removals} of ${total} files gone`,
      title: `${plural(removals, 'file')} of ${total} went from this computer since the last sync, so nothing was pulled or pushed. Go on pushes the removals (their pages move to Recently deleted); or put the files back.${meanwhile}` };
  }
  if (item.offline) {
    return { ...item, state: 'waiting', text: 'Directory not there',
      title: `${s.last_error || `${item.dest} is not there.`} The vault syncs again once it is back; Locate… if it moved.${meanwhile}` };
  }
  if (s.last_error) return { ...item, state: 'error', text: 'Sync problem', title: `${s.last_error}${meanwhile}` };
  if (item.agentRunning) {
    return { ...item, state: 'busy', agentBusy: true, text: `${item.agentLabel} is working · ${item.agentRunning}`,
      title: `${item.agentLabel} is ${item.agentChat ? 'working in the chat of a file of this folder' : 'answering a Send of this folder'} on this computer: the files here may change meanwhile.` };
  }
  if (!s.last_sync) return { ...item, state: 'new', text: 'Not synced yet', title: 'The first round runs in a moment.' };
  const { changed } = item;
  const sent = counts.sent || 0;
  const clashes = counts.conflicts || 0;
  const shared = s.shared || [];
  const text = [`Up to date · ${clock(s.last_sync)}`, sent && `${sent} pushed`, clashes && plural(clashes, 'conflict'),
    changed && `${changed} changed here`, left.length && `${left.length} not synced`,
    shared.length && `${shared.length} in another vault`, s.push_off && 'push turned off'];
  return { ...item, state: 'ok', text: text.filter(Boolean).join(' · '),
    title: `Up to date since ${clock(s.last_sync)}.${directions(item)}` +
      (clashes ? ` ${plural(clashes, 'conflict')} in the last round: the file won, and Gamma's side is kept on the block's merge chip.` : '') +
      (changed ? ` ${plural(changed, 'file')} you changed on this computer ${isAre(changed)} kept as you left ${changed === 1 ? 'it' : 'them'}; Force pull… replaces ${changed === 1 ? 'it' : 'them'} with Gamma's.` : '') +
      (left.length ? ` Not synced: ${left.slice(0, 3).map((k) => `${k.path} (${k.why})`).join('; ')}${left.length > 3 ? `; ${left.length - 3} more` : ''}.` : '') +
      (s.push_off ? ` ${said(s.push_off)}` : '') +
      (shared.length ? ` Kept by an older synced vault beside this one, so not written here: ${shared.slice(0, 3).map((p) => `“${p.title || 'Untitled'}”`).join(', ')}${shared.length > 3 ? `; ${shared.length - 3} more` : ''}.` : '') +
      (s.upgraded_at ? ` Kept on this computer before; a synced vault since ${clock(s.upgraded_at)}, with .gamma/ in place of .gamma-sync.json.` : '') +
      (item.untracked ? ` Untracked: ${plural(item.untracked, 'file')} Gamma does not read.` : '') };
}

// A file's vault: a page whose PDF each new version of the file becomes.
// Worst first: pushing, paused, a problem, the file still being written
// (pending, never an error), never pushed, then pushed or up to date.
function fileState(item, s) {
  if (s.running) return { ...item, state: 'busy', text: 'Pushing…', title: 'A round is running.' };
  if (item.paused) {
    return { ...item, state: 'paused', text: `Paused · ${clock(s.paused_at)}`, title: 'Nothing is pushed until you resume it; Push now pushes the file.' };
  }
  if (item.away) return awayState(item);
  if (s.last_error) return { ...item, state: 'error', text: 'Problem', title: s.last_error };
  if (s.waiting) return { ...item, state: 'pending', text: 'Waiting for the file to finish', title: s.waiting };
  if (!s.last_sync) return { ...item, state: 'new', text: 'Not pushed yet', title: 'The first round runs in a moment.' };
  const moved = s.relocation_error ? ` The highlights were not moved: ${s.relocation_error}` : '';
  return s.last_sent
    ? { ...item, state: 'ok', text: `Pushed · ${clock(s.last_sent)}`, title: `Pushed to Gamma at ${clock(s.last_sent)}; each new version is pushed.${moved}` }
    : { ...item, state: 'ok', text: `Up to date · ${clock(s.last_sync)}`, title: `Up to date since ${clock(s.last_sync)}: Gamma shows this version of the file.${moved}` };
}

// Everything one local server keeps: its answers to the three reads
// (`zotero`, its Zotero links: none from a server that has no such list),
// `roles`, the workspaces its account may open, `agents`, the agents it
// finds on this computer, and `computers`, its computer links with this
// computer's name (vault).
function itemsOf(server, { mirrors = [], links = [], zotero: zoteroLinks = [], roles = null, agents = null, computers = null } = {}, servers = []) {
  return [...mirrors.map((m) => clone(server, m, servers)), ...(zoteroLinks || []).map((z) => zotero(server, z)),
    ...links.map((l) => vault(server, l, servers, roles, agents, computers))];
}

// The sync button's reading of all of it: the worst state, a word or two,
// and a tooltip with the counts.
function summarize(items) {
  const kinds = (kind) => items.filter((i) => i.kind === kind).length;
  const [clones, links, vaults] = [kinds('clone'), kinds('zotero'), kinds('vault')];
  const what = listed([clones && plural(clones, 'clone'), links && plural(links, 'Zotero link'),
    vaults && plural(vaults, 'synced vault')].filter(Boolean));
  if (!items.length) return { state: 'none', text: '', title: 'Nothing syncs with this computer yet' };
  const live = items.filter((i) => !QUIET.includes(i.state));
  if (!live.length) return { state: 'paused', text: 'Paused', title: `${what} on this computer: paused` };
  const state = live.reduce((worst, i) => (RANK[i.state] > RANK[worst] ? i.state : worst), 'ok');
  const n = live.filter((i) => i.state === state).length;
  // busy only because agents work (no round runs): say so, not "Syncing…"
  const agentsOnly = state === 'busy' && live.filter((i) => i.state === 'busy').every((i) => i.agentBusy);
  const text = agentsOnly ? 'Agent working' : {
    error: plural(n, 'problem'),
    conflicts: 'Conflicts',
    held: 'Held',
    busy: 'Syncing…',
    pending: 'Not synced yet',
    new: 'Starting…',
    waiting: 'Waiting',
    queued: 'Changes queued',
    ok: 'Synced',
  }[state];
  // the first word in lower case, an acronym (PDFs) as it is
  return { state, text, title: `${what} on this computer: ${text.replace(/^[A-Z][a-z]/, (s) => s.toLowerCase())}` };
}

// --- what the shell says about a vault ---------------------------------------------

// What a vault of another server may be, for now: it pulls, and pushes PDFs
// at most (docs/research/vaults.md "Not built"). The dialog, the vault's
// view and the shell's switches (main.js setPull, changePush) keep to it.
const REMOTE_RULE = 'A vault of another server pulls, and pushes PDFs at most, for now.';

// What a vault of the whole library may not be: a work folder, which needs
// a folder of its own (the server's words, gamma/folder_links.py). One that
// pushed everything before goes on as it is.
const WHOLE_RULE = 'A vault of the whole library cannot push everything: make one of a folder.';

// The dialog's sentence: what the first round of a vault would do, from
// what `POST /api/folder-links/preview` found in the directory `path` and
// the two switches (`pull`, `push`), into the folder named `folder` (a new
// one with `newFolder`). `{ ok, text }`: not ok, the text says why and Sync
// is shut. A vault of another server (`remote`) keeps to REMOTE_RULE, and
// one of the whole library (`whole`) to WHOLE_RULE. The refusals the server
// would answer are said here first where the preview tells them (its
// `refusal`; a folder with pages and a directory with files that would push
// everything into each other). A work folder is not asked about here: its
// tracking dialog in Gamma says what it tracks (main.js makeWorkFolder), and
// this dialog is for the other vaults, a folder with pages, a vault of
// another server and the whole library.
function firstRound({ path: dir, preview = {}, pull, push, folder, newFolder = false, remote = false, whole = false }) {
  const no = (text) => ({ ok: false, text });
  const p = preview || {};
  if (!pull && push === 'off') return no('A vault pulls, pushes, or both: turn one of them on.');
  if (remote && (push === 'everything' || !pull)) return no(REMOTE_RULE);
  if (whole && push === 'everything') return no(WHOLE_RULE);
  if (p.refusal) return no(p.refusal);
  const notes = p.notes || 0;
  const pdfs = p.pdfs || 0;
  const pages = p.pages || 0;
  const files = notes + pdfs;
  if (!newFolder && push === 'everything' && pages && files) {
    return no('Both the folder and the directory hold files: sync an empty folder with this directory, or this folder with an empty or new one.');
  }
  const into = newFolder ? `a new folder “${folder}”` : `“${folder}”`;
  const found = listed([notes && plural(notes, 'note'), pdfs && plural(pdfs, 'PDF')].filter(Boolean));
  const pulled = pages ? plural(pages, 'page') : `what Gamma files under ${into}`;
  let main;
  if (push === 'everything' && pull) {
    const steps = [files && `push ${found} from ${dir} into ${into}`, pages && `pull ${plural(pages, 'page')} into ${dir}`].filter(Boolean);
    main = `${capital(steps.join(', then ') || `sync ${dir} with ${into}`)}; from then on both sides are kept in step.`;
  } else if (push === 'everything') {
    main = `Push ${found || 'what you add'} from ${dir} into ${into}; nothing is ever pulled into ${dir}.`;
  } else if (push === 'pdfs' && !pull) {
    main = `Push the ${pdfs ? plural(pdfs, 'PDF') : 'PDFs'} of ${dir} into ${into}; nothing is ever pulled into ${dir}.`;
  } else {
    main = `Pull ${pulled} into ${dir}; ${push === 'pdfs' ? 'PDFs you change there are pushed back' : 'nothing there is pushed'}.`;
  }
  // Every vault is known by its marker, .gamma/, which is all Gamma writes
  // beside the files (the preview's `will_write`; a folder kept before
  // gives its .gamma-sync.json up for it). One that does not push
  // everything makes no page of a file Gamma did not write.
  const marker = p.marker || null;
  const more = [
    marker && !marker.link_id ? `${dir} was a synced vault before: it is taken up again.`
      : marker ? '' : 'Gamma adds .gamma/ there, to know the directory again, and nothing else of its own.',
    pull && push !== 'everything' && files
      ? `The ${files === 1 ? 'note or PDF' : `${files} notes and PDFs`} already there ${isAre(files)} left as ${files === 1 ? 'it is' : 'they are'}.` : '',
    p.untracked ? `${plural(p.untracked, 'other file')} ${p.untracked === 1 ? 'stays' : 'stay'} untracked.` : '',
  ];
  return { ok: true, text: [main, ...more].filter(Boolean).join(' ') };
}

// The question before removing a vault, with `GET .../removal`'s counts
// (`{pages, in_place, bytes, files, in_library}`): `{ message, detail,
// buttons, choices }`, `choices[i]` the `files=` of button i (null: cancel).
// A file's vault never touches its file; a vault whose directory is not
// there, or is no longer its own (stopped: the marker gone or another
// vault's), is forgotten without touching the disk. A work folder's files
// not in the library go to Recently deleted, and those in it stay: both are
// said.
function removalAsk(item, removal = {}) {
  if (item.scope === 'file') {
    return { message: `Remove the synced vault of “${item.name}”?`,
      detail: `${baseName(item.dest)} is no longer pushed to the page. The file and the page stay as they are.`,
      buttons: ['Remove the vault', 'Cancel'], choices: ['keep', null] };
  }
  const pages = removal.pages || 0;
  const files = removal.files || 0;
  const kept = removal.in_library || 0;
  const inPlace = removal.in_place || 0;
  const pagesSaid = files
    ? `${files === 1 ? 'Its file not in the library goes' : `Its ${files} files not in the library go`} to Recently deleted` +
      (kept ? `; ${kept === 1 ? 'its page in the library stays' : `its ${kept} pages in the library stay`}` : '')
    : pages ? `${pages === 1 ? 'Its page stays' : `Its ${pages} pages stay`} in Gamma` : 'It holds no pages yet';
  const stays = pagesSaid +
    (inPlace ? `; ${plural(inPlace, 'PDF')} read where ${inPlace === 1 ? 'it is' : 'they are'} (${bytesText(removal.bytes)}) ${isAre(inPlace)} stored in Gamma first` : '') + '.';
  const message = `Remove the synced vault “${item.name}”?`;
  if (item.offline) {
    return { message, detail: `${stays} ${item.dest} is not there now, so nothing on disk is touched.`,
      buttons: ['Remove the vault', 'Cancel'], choices: ['keep', null] };
  }
  if (item.stopped) {
    return { message, detail: `${stays} ${item.dest} is no longer this vault's directory, so nothing in it is touched.`,
      buttons: ['Remove the vault', 'Cancel'], choices: ['keep', null] };
  }
  return { message,
    detail: `${stays} Keep the files in ${item.dest} as they are, or move the files the sync wrote to its trash, .gamma/trash; files you added or changed stay.`,
    buttons: ['Remove, keep the files', 'Remove, move them to the trash', 'Cancel'], choices: ['keep', 'trash', null] };
}

// The question before a force pull (the files become what Gamma holds) or
// a force push (the pages become what their files say), with the vault's
// page count (`GET .../removal`'s `pages`): `{ message, detail, buttons }`,
// button 0 forcing.
function forceAsk(item, which, removal = {}) {
  const pages = plural(removal.pages || 0, 'page');
  if (which === 'pull') {
    return { message: `Force pull “${item.name}”?`,
      detail: `Every file of its ${pages} in ${item.dest} becomes what Gamma holds` +
        (item.changed ? `, the ${plural(item.changed, 'file')} you changed on this computer among them` : '') + '. What they held is replaced.',
      buttons: ['Force pull', 'Cancel'] };
  }
  return { message: `Force push “${item.name}”?`,
    detail: `Each of its ${pages} becomes what its file in ${item.dest} says: what changed in Gamma since the last sync is replaced, and each change replaced is kept as a resolved conflict.`,
    buttons: ['Force push', 'Cancel'] };
}

// The question before a held vault goes on (its guard: many files gone at
// once): `{ message, detail, buttons }`, button 0 going on.
function goOnAsk(item) {
  const { removals = 0, total = 0 } = item.held || {};
  return { message: `${plural(removals, 'file')} of ${total} in “${item.name}” went from this computer since the last sync.`,
    detail: 'Go on, and their pages move to Recently deleted in Gamma, where they can be restored. Or put the files back, and the next sync finds them.',
    buttons: ['Go on', 'Cancel'] };
}

// What each agent may do in a work folder's directory, as the panel and the
// question before turning one on say it: Codex runs commands in its
// sandbox; Claude Code gets no shell (docs/dev/local_agents.md "Trust").
const AGENT_DOES = {
  codex: 'it edits files in this directory and runs commands in its sandbox, without network',
  claude: 'it edits files in this directory and runs no commands',
};

// The question before agent `name` works in a work folder's directory, the
// computer's decision (main.js setAgent), with the Sends of the folder that
// wait now (`waiting`, handed to it once it is on; null: not counted):
// `{ message, detail, buttons }`, button 0 turning it on.
function agentAsk(item, name, waiting = null) {
  const label = AGENT_LABELS[name] || name;
  const handed = waiting ? ` ${waiting === 1 ? '1 waiting Send' : `${waiting} waiting Sends`} will be handed to it.` : '';
  return { message: `Let ${label} work in ${item.dest}?`,
    detail: `${label} will answer the Sends you make on this folder: ${AGENT_DOES[name] || 'it edits files in this directory'}.${handed}`,
    buttons: [`Turn on ${label}`, 'Cancel'] };
}

// What turning a work folder's agent on (`name`, with the `queued` Sends
// handed to it) or off ("": `item.agentLabel` was on) came to, for the bar.
function agentSaid(item, name, queued = 0) {
  if (!name) return `${item.agentLabel || 'The agent'} no longer answers the Sends on ${item.name}: they wait for an agent you run yourself.`;
  const label = AGENT_LABELS[name] || name;
  return `${label} now answers the Sends on ${item.name}${queued ? `; ${queued} waiting ${queued === 1 ? 'was' : 'were'} handed to it` : ''}.`;
}

// What each agent may ask for in the chat when its folder's "What it may
// run" is Ask (docs/dev/local_agents.md): Codex to go past its sandbox,
// Claude Code to run a command at all.
const AGENT_ASKS = {
  codex: 'to run a command with the network or to change files outside this folder',
  claude: 'to run a command, such as your build',
};

// The question before a work folder's agent may ask in the chat to run
// what its confinement keeps from it, the computer's decision (main.js
// setAgentRuns): `{ message, detail, buttons }`, button 0 letting it ask.
function runsAsk(item) {
  const label = item.agentLabel || AGENT_LABELS[item.agent] || 'The agent';
  return { message: `Let ${label} ask to run more in ${item.dest}?`,
    detail: `In the chat of a file of this folder, ${label} can then ask you ${AGENT_ASKS[item.agent] || 'to run a command'}; nothing of it runs until you allow it there. A command you allow runs as you, with your files and the network.`,
    buttons: ['Ask me in the chat', 'Cancel'] };
}

// What setting a work folder's "What it may run" to `runs` came to, for the bar.
function runsSaid(item, runs) {
  const label = item.agentLabel || AGENT_LABELS[item.agent] || 'The agent';
  return runs === 'ask' ? `${label} may now ask you in the chat to run more on ${item.name}.`
    : `${label} no longer asks to run more on ${item.name}: it keeps to its confinement.`;
}

// What a round asked for came to, for the bar: its status as the round
// left it (main.js followRound).
function roundSaid(item, status) {
  const s = status || {};
  if (s.stopped) return `${item.name}: ${s.stopped}`;
  if (s.held) return `${item.name} is held: ${plural(s.held.removals || 0, 'file')} of ${s.held.total || 0} went from this computer. Go on from the sync panel, or put them back.`;
  if (s.last_error) return `${item.name}: ${s.last_error}`;
  const counts = s.counts || {};
  if (item.scope === 'file') {
    const file = baseName(item.dest);
    return counts.sent ? `${file} was pushed to Gamma.` : s.waiting ? `${file} is not pushed yet: ${s.waiting}.` : `${file}: nothing new to push.`;
  }
  const words = { added: 'added', updated: 'updated', renamed: 'renamed', removed: 'removed', sent: 'pushed', read: 'read', waiting: 'waiting' };
  const parts = Object.entries(counts).filter(([k, n]) => n && (words[k] || k === 'conflicts'))
    .map(([k, n]) => (k === 'conflicts' ? plural(n, 'conflict') : `${n} ${words[k]}`));
  return `${item.name} is up to date${parts.length ? `: ${parts.join(', ')}` : ''}.`;
}

module.exports = { PUSH, pushWord, clone, zotero, vault, itemsOf, summarize, REMOTE_RULE, WHOLE_RULE, firstRound, removalAsk, forceAsk,
  goOnAsk, roundSaid, AGENT_LABELS, agentAsk, agentSaid, runsAsk, runsSaid, computerLine, computerFailed, computerSaid,
  computerStranded, computerOtherAccount };
