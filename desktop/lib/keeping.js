// What this computer keeps of Gamma, read as one picture for the bar's
// sync button and its panel (docs/architecture.md "Kept on this
// computer"): the clones (a local server's mirrors of remote workspaces),
// the folders on disk and the files followed (a local server's folder
// links, of kind folder and file). Pure: main.js reads the rows off each
// running local server and hands them here, and the bar only renders what
// comes back.
//
// A clone reads as Gamma's own sync pill reads it (mirrorState in
// frontend/src/collaboration/MirrorPopover.jsx): the same states, checked
// in the same order, so the bar and the page never disagree. Change both.

// How much a state asks of the user, least first; the button shows the
// worst. A detached clone or a paused folder or file is the user's own
// choice and never raises it.
const RANK = { ok: 0, new: 1, pending: 2, busy: 3, conflicts: 4, error: 5 };
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

// A folder kept on disk by local server `server`: one row of its `GET
// /api/folder-links`. `push`: the PDFs changed in it go back to Gamma.
// Of the files the last round kept, `changed` counts the ones a forced
// Sync replaces with Gamma's (their entry's `force`), `held` the rest: a
// PDF a send could not deliver, a file in the way.
function folder(server, link, servers) {
  const s = link.status || {};
  const names = s.folder_path || [];
  const kept = s.kept || [];
  const changed = kept.filter((k) => k.force).length;
  const held = kept.filter((k) => !k.force);
  const sent = (s.counts || {}).sent || 0;
  const item = {
    kind: 'folder', key: `folder:${server.id}:${link.id}`, server: server.id, id: link.id,
    name: link.folder_id === 'root' ? 'Whole library' : names.length ? names[names.length - 1] : baseName(link.dest),
    where: link.dest, dest: link.dest, from: link.remote_url ? nameOf(link.remote_url, servers) : server.name,
    changed, held: held.length, push: Boolean(link.push), paused: Boolean(s.paused_at),
  };
  if (s.running) return { ...item, state: 'busy', text: 'Syncing…', title: 'A round is running.' };
  if (item.paused) {
    return { ...item, state: 'paused', text: `Paused · ${clock(s.paused_at)}`, title: 'Nothing here is written until you resume it; Sync runs one round now.' };
  }
  if (s.last_error) return { ...item, state: 'error', text: 'Sync problem', title: s.last_error };
  if (!s.last_sync) return { ...item, state: 'new', text: 'Not written yet', title: 'The first round runs in a moment.' };
  const text = [`Up to date · ${clock(s.last_sync)}`, item.push && 'sends back', sent && `${sent} sent`, changed && `${changed} changed here`,
    held.length && `${held.length} not synced`];
  return { ...item, state: 'ok', text: text.filter(Boolean).join(' · '),
    title: `Up to date since ${clock(s.last_sync)}.` +
      (item.push ? ` PDFs you change here are sent back to Gamma${sent ? `; the last round sent ${sent}` : ''}.` : '') +
      (changed ? ` ${plural(changed, 'file')} you changed on this computer ${changed === 1 ? 'is' : 'are'} left alone; Sync can replace ${changed === 1 ? 'it' : 'them'}.` : '') +
      (held.length ? ` Not synced: ${held.slice(0, 3).map((k) => `${k.path} (${k.why})`).join('; ')}${held.length > 3 ? `; ${held.length - 3} more` : ''}.` : '') };
}

// A file followed by local server `server`: one row of its `GET
// /api/folder-links` of kind file, a page whose PDF each new version of
// the file on disk becomes. Named after the page.
function file(server, link, servers) {
  const s = link.status || {};
  const item = {
    kind: 'file', key: `file:${server.id}:${link.id}`, server: server.id, id: link.id,
    name: link.title || s.title || baseName(link.dest),
    where: link.dest, dest: link.dest, from: link.remote_url ? nameOf(link.remote_url, servers) : server.name,
    paused: Boolean(s.paused_at),
  };
  if (s.running) return { ...item, state: 'busy', text: 'Sending…', title: 'A round is running.' };
  if (item.paused) {
    return { ...item, state: 'paused', text: `Paused · ${clock(s.paused_at)}`, title: 'Nothing is sent until you resume it; Sync sends the file now.' };
  }
  if (s.last_error) return { ...item, state: 'error', text: 'Problem', title: s.last_error };
  if (s.waiting) return { ...item, state: 'pending', text: 'Waiting for the file to finish', title: s.waiting };
  if (!s.last_sync) return { ...item, state: 'new', text: 'Not sent yet', title: 'The first round runs in a moment.' };
  const moved = s.relocation_error ? ` The highlights were not moved: ${s.relocation_error}` : '';
  return s.last_sent
    ? { ...item, state: 'ok', text: `Sent · ${clock(s.last_sent)}`, title: `Sent to Gamma at ${clock(s.last_sent)}; each new version is sent.${moved}` }
    : { ...item, state: 'ok', text: `Up to date · ${clock(s.last_sync)}`, title: `Up to date since ${clock(s.last_sync)}: Gamma shows this version of the file.${moved}` };
}

// Everything one local server keeps: its answers to the two reads.
function itemsOf(server, { mirrors = [], links = [] } = {}, servers = []) {
  return [...mirrors.map((m) => clone(server, m, servers)), ...links.map((l) => (l.kind === 'file' ? file : folder)(server, l, servers))];
}

// The sync button's reading of all of it: the worst state, a word or two,
// and a tooltip with the counts.
function summarize(items) {
  const count = (kind) => items.filter((i) => i.kind === kind).length;
  const [clones, folders, files] = [count('clone'), count('folder'), count('file')];
  const what = listed([clones && plural(clones, 'clone'), folders && `${plural(folders, 'folder')} on disk`,
    files && `${plural(files, 'file')} followed`].filter(Boolean));
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

module.exports = { clone, folder, file, itemsOf, summarize };
