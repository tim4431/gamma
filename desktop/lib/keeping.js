// What this computer keeps of Gamma, read as one picture for the bar's
// sync button and its panel (docs/architecture.md "Kept on this
// computer"): the clones (a local server's mirrors of remote workspaces)
// and the folders on disk (a local server's folder links). Pure: main.js
// reads the rows off each running local server and hands them here, and
// the bar only renders what comes back.
//
// A clone reads as Gamma's own sync pill reads it (mirrorState in
// frontend/src/collaboration/MirrorPopover.jsx): the same states, checked
// in the same order, so the bar and the page never disagree. Change both.

// How much a state asks of the user, least first; the button shows the
// worst. A detached clone or a paused folder is the user's own choice and
// never raises it.
const RANK = { ok: 0, new: 1, pending: 2, busy: 3, conflicts: 4, error: 5 };
const QUIET = ['detached', 'paused'];

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

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

// A folder kept on disk by local server `server`: one row of its `GET /api/folder-links`.
function folder(server, link, servers) {
  const s = link.status || {};
  const names = s.folder_path || [];
  const changed = (s.kept || []).length;
  const item = {
    kind: 'folder', key: `folder:${server.id}:${link.id}`, server: server.id, id: link.id,
    name: link.folder_id === 'root' ? 'Whole library' : names.length ? names[names.length - 1] : String(link.dest || '').split(/[\\/]/).pop(),
    where: link.dest, dest: link.dest, from: link.remote_url ? nameOf(link.remote_url, servers) : server.name,
    changed, paused: Boolean(s.paused_at),
  };
  if (s.running) return { ...item, state: 'busy', text: 'Syncing…', title: 'A round is running.' };
  if (item.paused) {
    return { ...item, state: 'paused', text: `Paused · ${clock(s.paused_at)}`, title: 'Nothing here is written until you resume it; Sync runs one round now.' };
  }
  if (s.last_error) return { ...item, state: 'error', text: 'Sync problem', title: s.last_error };
  if (!s.last_sync) return { ...item, state: 'new', text: 'Not written yet', title: 'The first round runs in a moment.' };
  return { ...item, state: 'ok', text: `Up to date · ${clock(s.last_sync)}${changed ? ` · ${changed} changed here` : ''}`,
    title: `Up to date since ${clock(s.last_sync)}.${changed ? ` ${plural(changed, 'file')} you changed on this computer ${changed === 1 ? 'is' : 'are'} left alone; Sync can replace ${changed === 1 ? 'it' : 'them'}.` : ''}` };
}

// Everything one local server keeps: its answers to the two reads.
function itemsOf(server, { mirrors = [], links = [] } = {}, servers = []) {
  return [...mirrors.map((m) => clone(server, m, servers)), ...links.map((l) => folder(server, l, servers))];
}

// The sync button's reading of all of it: the worst state, a word or two,
// and a tooltip with the counts.
function summarize(items) {
  const clones = items.filter((i) => i.kind === 'clone').length;
  const folders = items.length - clones;
  const what = [clones && plural(clones, 'clone'), folders && plural(folders, 'folder on disk')].filter(Boolean).join(' and ');
  if (!items.length) return { state: 'none', text: '', title: 'Nothing is kept on this computer yet' };
  const live = items.filter((i) => !QUIET.includes(i.state));
  if (!live.length) return { state: 'paused', text: 'Paused', title: `${what} on this computer: paused` };
  const state = live.reduce((worst, i) => (RANK[i.state] > RANK[worst] ? i.state : worst), 'ok');
  const n = live.filter((i) => i.state === state).length;
  const text = {
    error: plural(n, 'problem'),
    conflicts: 'Conflicts',
    busy: 'Syncing…',
    pending: 'Not synced yet',
    new: 'Starting…',
    ok: 'Synced',
  }[state];
  return { state, text, title: `${what} on this computer: ${text.toLowerCase()}` };
}

module.exports = { clone, folder, itemsOf, summarize };
