// What this computer keeps of Gamma, read as one picture for the bar's
// sync button and its panel (docs/architecture.md "Kept on this
// computer"): the clones (a local server's mirrors of remote workspaces),
// the Zotero links (a local server's workspaces kept in step with a Zotero
// library), the folders on disk and the files followed (a local server's
// folder links, of kind folder and file). Pure: main.js reads the rows off
// each running local server and hands them here, and the bar only renders
// what comes back.
//
// A clone and a Zotero link read as Gamma's own sync pill reads them
// (mirrorState in frontend/src/collaboration/MirrorPopover.jsx, a link's
// errors and waiting files in collaboration/syncLinks.js): the same states,
// checked in the same order, so the bar and the page never disagree.
// Change both.

// How much a state asks of the user, least first; the button shows the
// worst. A detached clone or link or a paused folder or file is the
// user's own choice and never raises it; PDFs that wait on Zotero's side (a
// full storage, files Zotero does not serve) and a link's changes queued
// for a later round only say so.
const RANK = { ok: 0, new: 1, waiting: 1, queued: 1, pending: 2, busy: 3, conflicts: 4, error: 5 };
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

// Everything one local server keeps: its answers to the three reads
// (`zotero`, its Zotero links: none from a server that has no such list),
// its folder links split by kind.
function itemsOf(server, { mirrors = [], links = [], zotero: zoteroLinks = [] } = {}, servers = []) {
  return [...mirrors.map((m) => clone(server, m, servers)), ...(zoteroLinks || []).map((z) => zotero(server, z)),
    ...links.map((l) => (l.kind === 'file' ? file : folder)(server, l, servers))];
}

// The sync button's reading of all of it: the worst state, a word or two,
// and a tooltip with the counts.
function summarize(items) {
  const kinds = (kind) => items.filter((i) => i.kind === kind).length;
  const [clones, links, folders, files] = [kinds('clone'), kinds('zotero'), kinds('folder'), kinds('file')];
  const what = listed([clones && plural(clones, 'clone'), links && plural(links, 'Zotero link'),
    folders && `${plural(folders, 'folder')} on disk`, files && `${plural(files, 'file')} followed`].filter(Boolean));
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
    waiting: 'Waiting',
    queued: 'Changes queued',
    ok: 'Synced',
  }[state];
  // the first word in lower case, an acronym (PDFs) as it is
  return { state, text, title: `${what} on this computer: ${text.replace(/^[A-Z][a-z]/, (s) => s.toLowerCase())}` };
}

module.exports = { clone, zotero, folder, file, itemsOf, summarize };
