// A synced vault (docs/research/vaults.md): a folder of a workspace and a
// directory on this computer kept in step, or a page and one PDF file, by a
// local server (docs/dev/synced_vaults.md "The desktop app").
// Gamma's server is the remote, the directory or the file is local; a vault
// pulls (Gamma to disk), pushes (disk to Gamma: nothing, its PDFs, or
// everything), or both. On a LOCAL server the vault is that server's own and
// reads the workspace in-process. A REMOTE server's folder is synced by the
// host local server (ensureHost) with a token minted on the remote, read
// over HTTP: no clone, only the folder's files come down. The shell asks for
// the directory or the file (the native picker; GAMMA_SHELL_PICK_DIR and
// GAMMA_SHELL_PICK_FILE in tests), shows what the server found there and
// what the first round would do (the vault dialog, a view of the sync panel:
// askVault), mints the token, and makes the vault through the servers'
// public API. Only the shell names a path: the routes that take one want its
// word (shellWord, X-Gamma-Shell). The ways in: a folder's "Sync with this
// computer…" in Gamma's page and the panel's chooser (addFolderVault), "Add a
// synced vault…" in the launcher and the panel (addDirectoryVault), a folder
// opened with Gamma from the file manager (lib/systemFiles.js
// newWorkFolder), a page's "Push from a file on this computer…"
// (addFileVault). A directory that is the Desktop, Downloads… is asked about
// first (broadOk). A work folder (a vault
// of a local server's folder that pushes everything, docs/dev/work_folders.md:
// a directory added, or an empty folder synced with one) asks nothing here:
// the shell makes it paused and choosing, then opens the server's window on
// its folder with its tracking dialog (`?track=`, makeWorkFolder), where the
// person chooses what it tracks; the page answers through the server, never
// through the shell. An empty folder of a REMOTE server becomes a work folder
// there the same way (makeRemoteWorkFolder), kept by the host with a write
// token; its dialog shows in the host's own window, which the page's
// `tracked` ask takes back to that server (trackedFromPage), so the
// directory's names stay on this computer. The rounds are the server's; the
// sync panel (lib/syncPanel.js) shows each vault and acts on it. In the Mac App Store build the
// server that is to read or write there first starts again when it started
// before the pick (reachFolder).

const { app, session } = require('electron');
const path = require('path');
const registry = require('./registry');
const keeping = require('./keeping');
const openFiles = require('./openFiles');

// The shell's shared names, set by init (main.js "the sections").
let ctx = null;

// The vaults of the open server's workspace, of folders and of files: a
// local server's own, or the host's that sync this remote's workspace.
// `server` names the local server holding each. No local server yet: none.
async function listLinks(api, wsId) {
  const origin = ctx.openOrigin();
  if (ctx.current.type === 'local') {
    const all = (await api(origin, '/api/folder-links')).links || [];
    return all.filter((l) => !l.remote_url && l.workspace_id === wsId).map((l) => ({ ...l, server: ctx.current.id }));
  }
  if (!registry.load().servers.some((s) => s.type === 'local')) return [];
  const host = await ctx.ensureHost(api);
  const all = (await api(host.origin, '/api/folder-links')).links || [];
  return all.filter((l) => l.remote_url === origin && l.workspace_id === wsId).map((l) => ({ ...l, server: host.local.id }));
}

// The open workspace's folders, and its folders' vaults (not its files').
async function listFolders(wsId) {
  const api = ctx.serverApi(ctx.content.webContents.session);
  const [tree, links] = await Promise.all([
    api(ctx.openOrigin(), '/api/sync/folders', { headers: { 'X-Gamma-Workspace': wsId } }),
    listLinks(api, wsId),
  ]);
  return { folders: tree.folders || [], links: links.filter((l) => l.kind !== 'file') };
}

// The vault dialog, a view of the sync panel (ui/bar.html), which answers
// through shell:answer-vault: the directory, what the server found there
// (`preview`, POST /api/folder-links/preview), the switches Pull and Push,
// and what the first round would do, a sentence for each setting of them
// (keeping.firstRound; one that is not `ok` shuts Sync). `view`: `{ path,
// server, workspace, folder, newFolder, remote, preview, pull, push }`, the
// last two the switches as they start. One at a time: a newer one cancels
// the one shown. Resolves to `{ pull, push }`, or null when cancelled. While
// it is up the bar stays expanded on it (setBarExpanded), the page's asks
// wait (fromPage), and the window going to the launcher or to another server
// cancels it (cancelVaultAsk): it was asked about what the window showed.
let vaultAsk = null; // the dialog shown, with its resolve
let vaultAsks = 0;
function askVault(view) {
  if (vaultAsk) vaultAsk.resolve(null);
  return new Promise((resolve) => {
    const sentences = {};
    for (const pull of [true, false]) {
      for (const push of keeping.PUSH) sentences[`${pull}:${push}`] = keeping.firstRound({ ...view, pull, push });
    }
    vaultAsk = { ...view, id: ++vaultAsks, sentences, resolve };
    ctx.setBarExpanded(true);
    ctx.pushState();
  });
}

// The dialog answered: true when `id` is the one shown and the answer one
// its sentences allow (or null, a cancel).
function answerVault(id, answer) {
  if (!vaultAsk || vaultAsk.id !== id) return false;
  const choice = answer ? { pull: Boolean(answer.pull), push: keeping.pushWord(answer.push) } : null;
  if (choice && !(vaultAsk.sentences[`${choice.pull}:${choice.push}`] || {}).ok) return false;
  const { resolve } = vaultAsk;
  vaultAsk = null;
  ctx.setBarExpanded(false);
  ctx.pushState();
  resolve(choice);
  return true;
}

function cancelVaultAsk() {
  if (vaultAsk) answerVault(vaultAsk.id, null);
}

// The dialog as the bar renders it.
function vaultAskState() {
  if (!vaultAsk) return null;
  const { resolve: _resolve, ...view } = vaultAsk;
  return view;
}

// What making a vault of `dir` would find and write, writing nothing, on the
// local server at `origin` (`headers`: the workspace it would be made in).
function previewVault(api, origin, headers, dir, folder) {
  return api(origin, '/api/folder-links/preview', {
    method: 'POST', headers: { ...headers, ...ctx.JSON_TYPE, ...ctx.shellWord(origin) }, body: JSON.stringify({ path: dir, folder: folder || '', kind: 'folder' }),
  });
}

// A directory holding the marker of a vault no link holds any more takes it up again.
const adopts = (preview) => Boolean(preview.marker && !preview.marker.link_id);

// How a folder's vault starts: pulling, and pushing nothing while the folder
// has pages; an empty folder and a directory with notes or PDFs push
// everything, a work folder, of another server's folder too (whose emptiness
// that server's own answer settles: makeRemoteWorkFolder).
function firstPush(preview) {
  return !preview.pages && (preview.notes || preview.pdfs) ? 'everything' : 'off';
}

// The pages a folder of a remote server holds, for the dialog's sentence (the
// host's preview cannot count them). Best effort: 0; a work folder made of a
// folder that holds pages after all is refused by the host, which asks that
// server again once the folder is marked.
async function remotePages(api, origin, headers, folderId) {
  try {
    return ((await api(origin, `/api/sync/folders/${encodeURIComponent(folderId)}`, { headers })).pages || []).length;
  } catch {
    return 0;
  }
}

// Why workspace `wsId` of the remote at `origin` (`remoteName`, as the bar
// names it) takes no work folder from this computer, or '' when it does:
// GET /api/sync/whoami, asked with the window's session `api`, says whether
// that server keeps work folders (`work_folders`) and the account's role
// there, which writes as an owner's or an editor's.
async function remoteWorkRefusal(api, origin, wsId, remoteName) {
  const older = `${remoteName} is older than this app and keeps no synced folders`;
  let who;
  try {
    who = await api(origin, '/api/sync/whoami', { headers: { 'X-Gamma-Workspace': wsId } });
  } catch (e) {
    if (!(e.status >= 400 && e.status < 500)) throw e;
    return e.status === 404 ? older : e.message;
  }
  if (!who.work_folders) return older;
  if (who.role !== 'owner' && who.role !== 'editor') return 'your account may only view this workspace there';
  return '';
}

// A work folder made from the directory `picked` on the local server `srv`
// (at `origin`, through `api`) in its workspace `ws` (`headers`), into a new
// folder named `name` or the folder `body.folder`: what `preview` refuses (a
// marker another vault holds among it) is said in the bar and nothing is
// made; else the vault is made paused and choosing, and the server's window,
// opened when another shows, goes to its folder with its tracking dialog,
// which saves what it tracks and runs its first round; `tick`, a file's path
// in the directory, is ticked there (a Markdown file opened with Gamma,
// *Edit in Gamma*). → the link, or null.
async function makeWorkFolder({ srv, origin, api, ws, headers, name, picked, preview, body, tick = '' }) {
  if (preview.refusal) {
    ctx.showNotice(preview.refusal);
    return null;
  }
  ctx.busy = `Adding the synced folder ${name}…`;
  ctx.pushState();
  let link;
  try {
    link = await api(origin, '/api/folder-links', {
      method: 'POST',
      headers: { ...ctx.JSON_TYPE, ...ctx.shellWord(origin), ...headers },
      body: JSON.stringify({ path: picked, ...body, pull: true, push: 'everything', choose: true, adopt: adopts(preview) }),
    });
  } finally {
    ctx.busy = null;
    ctx.pushState();
  }
  if (!ctx.current || ctx.current.id !== srv.id) {
    await ctx.openServer(srv.id);
    ctx.buildMenu();
  }
  await ctx.openGammaWorkspace(ws.id, { folder: link.folder_id, track: link.id, tick });
  ctx.refreshKeeping().catch(() => {});
  ctx.offerBackground(`The synced folder “${name}”`).catch(() => {});
  return link;
}

// The work folders of another server whose tracking dialog the host's window
// shows, by the host's link id: `{ host, nas, remoteOrigin, remoteName, wsId,
// folderId, tokenId, name }`, the host's server id, that server's in the
// registry (null when it is not there), the folder's workspace and id there,
// and the token the vault holds there. The page's `tracked` ask
// (trackedFromPage) is taken only for one of these.
const tracking = new Map();

// The host's own workspace for its window, where a work folder of another
// server's tracking dialog shows: the personal one of the account `api` is
// signed into there, else its default. Never that server's ids.
async function hostWorkspace(api, origin) {
  const info = await api(origin, '/api/session');
  return (openFiles.personalOf(info) || {}).id || info.default_workspace || '';
}

// A work folder of another server (docs/dev/work_folders.md): the empty
// folder `folderId` of workspace `wsId` of the remote at `origin` (`shown`,
// as the registry has it), through the window's session `api`, kept by the
// host (`host`: `{ local, origin }`) from the directory `picked`. What
// `preview` refuses is said in the bar and nothing is made. Else a write
// token minted there, as a pushing vault's, and the vault made on the host
// paused and choosing (the host marks the folder there and refuses one that
// holds pages, or that its server would not mark), this computer shown there
// (showComputer), then the host's own window, its tracking dialog open
// (`?track=`) over the host's own workspace: the directory's names never go
// to that server, only the files the person ticks. The token is remembered
// (tracking) so that a Cancel there revokes it; a vault not made revokes it
// at once, and one that server or the host refuses (a 4xx: the token not
// minted, a clone's or a Zotero-linked workspace, a nested folder) throws
// marked `refused`, for the vault dialog to be asked instead. → the link, or
// null.
async function makeRemoteWorkFolder({ host, api, origin, shown, wsId, folderId, name, picked, preview }) {
  if (preview.refusal) {
    ctx.showNotice(preview.refusal);
    return null;
  }
  const wsHeaders = { 'X-Gamma-Workspace': wsId };
  ctx.busy = `Adding the synced folder ${name}…`;
  ctx.pushState();
  let token = null;
  let link;
  let unshown = null; // why this computer is not shown on the remote, the vault made all the same
  try {
    await ctx.beforeMint(api, host.origin, origin);
    token = await api(origin, '/api/integrations/tokens', {
      method: 'POST',
      headers: { ...ctx.JSON_TYPE, ...wsHeaders },
      body: JSON.stringify({ name: `Gamma desktop: ${name} synced folder`.slice(0, 80), scope: 'write', expires_in_days: 365 }),
    });
    link = await api(host.origin, '/api/folder-links', {
      method: 'POST',
      headers: { ...ctx.JSON_TYPE, ...ctx.shellWord(host.origin) },
      body: JSON.stringify({ folder: folderId, path: picked, notes: true, pull: true, push: 'everything', choose: true,
        remote_url: origin, workspace: wsId, token: token.token, token_id: token.id }),
    });
    tracking.set(link.id, { host: host.local.id, nas: shown.id, remoteOrigin: origin, remoteName: shown.name, wsId, folderId, tokenId: token.id, name });
    token = null; // the host holds it now
    registry.setSettings({ folderHost: host.local.id }); // runs at launch with the clones' hosts
    unshown = await ctx.showComputer({ remote: api, remoteOrigin: origin, remoteName: shown.name, wsId, host: { origin: host.origin, api } });
  } catch (e) {
    if (token) await ctx.revokeToken(api, origin, wsId, token.id); // minted for a vault that was never made
    if (!link && e.status >= 400 && e.status < 500) e.refused = true;
    throw e;
  } finally {
    ctx.busy = null;
    ctx.pushState();
  }
  const hostWs = await hostWorkspace(api, host.origin);
  if (!ctx.current || ctx.current.id !== host.local.id) {
    await ctx.openServer(host.local.id);
    ctx.buildMenu();
  }
  await ctx.openGammaWorkspace(hostWs, { track: link.id });
  if (unshown) ctx.showNotice(unshown);
  ctx.refreshKeeping().catch(() => {});
  ctx.offerBackground(`The synced folder “${name}”`).catch(() => {});
  return link;
}

// The tracking dialog of a work folder of another server answered, in the
// host's window (its page's `tracked` ask: `linkId`, the host's link, and
// whether it was `cancelled`): the window goes back to that server, the
// folder open. Its Cancel removed the vault a fresh dialog made (the page's
// DELETE): the token minted for it is revoked there, and this computer is no
// longer shown there once no other vault of that server and workspace stays
// (releaseRemote). A Cancel the host refused (that server not reached: the
// page says `cancelled` false) leaves the vault choosing, its token kept,
// and is said. Only a dialog the shell opened (tracking), while the window
// shows its host.
async function trackedFromPage(_wsId, linkId, cancelled) {
  const t = tracking.get(linkId);
  if (!t || !ctx.current || ctx.current.id !== t.host) return null;
  tracking.delete(linkId);
  const at = await ctx.localServer(t.host);
  const kept = ((await at.api(at.origin, '/api/folder-links')).links || []).find((l) => l.id === linkId);
  if (!kept && cancelled) {
    const stranded = await releaseRemote(at.origin, at.api, t.host, { remote_url: t.remoteOrigin, workspace_id: t.wsId, token_id: t.tokenId }, t.remoteName);
    ctx.showNotice(`“${t.name}” is not synced with this computer; its folder on ${t.remoteName} stays as it was.${stranded ? ` ${stranded}` : ''}`);
  } else if (kept && kept.choosing && !cancelled) {
    // a fresh dialog's every way out cancels: one that leaves the vault choosing was refused
    ctx.showNotice(`“${t.name}” could not be cancelled, ${t.remoteName} not reached: it waits for its files to be chosen. Choose files… in the sync panel, or stop syncing it once ${t.remoteName} answers.`);
  } else if (kept && kept.choosing) {
    ctx.showNotice(`“${t.name}” waits for its files to be chosen: Choose files… in the sync panel.`);
  } else if (kept && !cancelled) {
    ctx.showNotice(`“${t.name}” pushes the files you chose to ${t.remoteName}, and pulls what changes there.`);
  }
  ctx.refreshKeeping().catch(() => {});
  if (t.nas && registry.get(t.nas)) {
    await ctx.openServer(t.nas);
    ctx.buildMenu();
    await ctx.openGammaWorkspace(t.wsId, { folder: t.folderId });
  }
  return true;
}

// Folder `folderId` of the open server's workspace `wsId` synced with a
// directory the user picks, as the dialog says; an empty folder that the
// directory's notes and PDFs would fill (firstPush) becomes a work folder
// instead (makeWorkFolder, or makeRemoteWorkFolder for a remote's), with no
// dialog here. A remote's is one only when that server keeps work folders
// and the account writes there (remoteWorkRefusal), and that server and the
// host take it; else the dialog asks, pushing nothing to start, the reason
// in the bar. The whole
// library is never one (keeping.WHOLE_RULE): its dialog starts pulling
// only. A folder that has a vault already opens its directory instead.
async function addFolderVault(wsId, folderId) {
  const shown = ctx.current; // the server it is asked of, whatever the window opens while the picker is up
  const remote = shown && shown.type === 'remote';
  const origin = remote ? ctx.remoteOf() : ctx.openOrigin();
  const serverName = shown.name;
  const api = ctx.serverApi(ctx.content.webContents.session);
  const g = ctx.gamma;
  const wsName = (g && (g.list.find((w) => w.id === wsId) || {}).name) || 'Library';
  const wsHeaders = { 'X-Gamma-Workspace': wsId };
  const tree = await api(origin, '/api/sync/folders', { headers: wsHeaders });
  const folder = folderId === 'root' ? { path: [] } : (tree.folders || []).find((f) => f.id === folderId);
  if (!folder) throw new Error('Unknown folder');
  const name = folder.path.length ? folder.path[folder.path.length - 1] : wsName;
  const synced = (await listLinks(api, wsId)).find((l) => l.kind !== 'file' && l.folder_id === folderId);
  if (synced) {
    ctx.openDirectory(synced.dest);
    ctx.showNotice(`${name} is already synced with ${synced.dest}`);
    return synced;
  }
  const picked = await ctx.pickDirectory(`Sync “${name}” with this computer`,
    `Choose the directory “${name}” is synced with: an empty one, or one whose notes and PDFs it takes.`);
  if (!picked || !(await broadOk(picked, false))) return null;
  ctx.stillOn(shown);
  // The server that keeps the vault: the open one, or the host for a remote's folder.
  let at = remote ? await ctx.ensureHost(api) : { local: registry.get(shown.id), origin };
  if (await ctx.reachFolder(at.local, picked)) at = remote ? await ctx.ensureHost(api) : { local: at.local, origin: ctx.localOrigin(at.local) };
  const preview = await previewVault(api, at.origin, remote ? {} : wsHeaders, picked, remote ? '' : folderId);
  if (remote) preview.pages = await remotePages(api, origin, wsHeaders, folderId);
  const whole = folderId === 'root';
  let push = whole ? 'off' : firstPush(preview);
  if (push === 'everything') {
    ctx.stillOn(shown);
    if (!remote) {
      return makeWorkFolder({ srv: at.local, origin: at.origin, api, ws: { id: wsId, name: wsName }, headers: wsHeaders, name, picked,
        preview, body: { folder: folderId } });
    }
    let refused = await remoteWorkRefusal(api, origin, wsId, serverName);
    if (!refused) {
      ctx.stillOn(shown);
      try {
        return await makeRemoteWorkFolder({ host: at, api, origin, shown, wsId, folderId, name, picked, preview });
      } catch (e) {
        if (!e.refused) throw e;
        refused = e.message;
      }
    }
    ctx.showNotice(keeping.remoteWorkRefused(serverName, name, refused));
    push = 'off';
  }
  const choice = await askVault({ path: picked, server: serverName, workspace: wsName, folder: name, newFolder: false, remote, whole,
    preview, pull: true, push });
  if (!choice) return null;
  ctx.busy = `Syncing ${name} with this computer…`;
  ctx.pushState();
  let token = null;
  try {
    const body = { folder: folderId, path: picked, notes: true, pull: choice.pull, push: choice.push };
    let link;
    let unshown = null; // why this computer is not shown on the remote, the vault made all the same
    if (remote) {
      // A token on the remote for the host to read the folder with, one that
      // may write when the vault pushes; the vault on the host; then this
      // computer shown there (showComputer).
      await ctx.beforeMint(api, at.origin, origin);
      token = await api(origin, '/api/integrations/tokens', {
        method: 'POST',
        headers: { ...ctx.JSON_TYPE, ...wsHeaders },
        body: JSON.stringify({ name: `Gamma desktop: ${name} synced folder`.slice(0, 80), scope: choice.push === 'off' ? 'read' : 'write', expires_in_days: 365 }),
      });
      link = await api(at.origin, '/api/folder-links', {
        method: 'POST',
        headers: { ...ctx.JSON_TYPE, ...ctx.shellWord(at.origin) },
        body: JSON.stringify({ ...body, remote_url: origin, workspace: wsId, token: token.token, token_id: token.id }),
      });
      token = null; // the host holds it now
      registry.setSettings({ folderHost: at.local.id }); // runs at launch with the clones' hosts
      unshown = await ctx.showComputer({ remote: api, remoteOrigin: origin, remoteName: serverName, wsId, host: { origin: at.origin, api } });
    } else {
      ctx.stillOn(shown);
      link = await api(at.origin, '/api/folder-links', {
        method: 'POST',
        headers: { ...ctx.JSON_TYPE, ...ctx.shellWord(at.origin), ...wsHeaders },
        body: JSON.stringify({ ...body, adopt: adopts(preview) }),
      });
    }
    ctx.showNotice(`${name} is synced with ${link.dest}${unshown ? `. ${unshown}` : ''}`);
    ctx.offerBackground(`The synced folder “${name}”`).catch(() => {});
    ctx.refreshKeeping().catch(() => {});
    return link;
  } catch (e) {
    if (token) await ctx.revokeToken(api, origin, wsId, token.id); // minted for a vault that was never made
    throw e;
  } finally {
    ctx.busy = null;
    ctx.pushState();
  }
}

// The host: the first local server, made when there is none.
function hostOf() {
  return registry.load().servers.find((s) => s.type === 'local') || registry.addLocal('Local');
}

// The host, started, and signed into in its own session (localServer).
async function startHost() {
  const local = hostOf();
  const entry = await ctx.startLocal(local);
  ctx.allowedOrigins.add(new URL(entry.url).origin);
  return ctx.localServer(local.id);
}

// Where a vault made from a directory lands (openFiles.readerPlace): `{ srv,
// origin, api, ws }`, the open local server through the window's session in
// the workspace the window shows when the account writes there, it is no
// clone and no one else sees it (else the account's own there), else the
// host in the account's personal workspace. A shared workspace takes a
// directory through its folder's "Sync with this computer…", which names it.
// `wsId`: the workspace asked in, in place of the one the window shows.
async function vaultPlace(wsId) {
  if (ctx.current && ctx.current.type === 'local' && ctx.content) {
    const origin = ctx.openOrigin();
    const api = ctx.serverApi(ctx.content.webContents.session);
    const place = openFiles.readerPlace(await api(origin, '/api/session'), wsId || ctx.currentWsId());
    const ws = place && (place.audience ? place.mine : place.ws);
    if (ws) return { srv: registry.get(ctx.current.id), origin, api, ws };
  }
  const host = await startHost();
  const ws = openFiles.personalOf(await host.api(host.origin, '/api/session'));
  if (!ws) throw new Error(`Your account on ${host.srv.name} has no workspace of its own.`);
  return { ...host, ws };
}

// "Add a synced folder…" (the launcher, the sync panel): a directory the user
// picks becomes a work folder, a new folder named after it whose files the
// person chooses in its tracking dialog (makeWorkFolder). No server need be
// open (vaultPlace). `dir`, the directory already chosen (oneByOne's "Sync
// the folder…", the Mac App Store build's *Open with Gamma*), asks for none;
// `wsId`, the workspace it was asked in there.
async function addDirectoryVault(dir, wsId) {
  const picked = dir || await ctx.pickDirectory('Add a synced folder',
    'Choose a directory: it becomes a folder of Gamma named after it, and you choose the files it tracks.');
  if (!picked) return null;
  const made = await workFolderOf(picked, wsId);
  return made && made.link;
}

// A work folder made of the directory `picked` where vaultPlace puts it, a
// new folder named after it, after the broad-place question when it is one
// (broadOk), its tracking dialog open with `tick` ticked when one is given.
// → `{ link, srv, ws, name }`, or null when asked off or refused by the
// preview (said in the bar).
async function workFolderOf(picked, wsId, tick = '') {
  if (!(await broadOk(picked))) return null;
  let place = await vaultPlace(wsId);
  if (await ctx.reachFolder(place.srv, picked)) place = await vaultPlace(wsId);
  const { srv, origin, api, ws } = place;
  const headers = { 'X-Gamma-Workspace': ws.id };
  const name = path.basename(picked) || picked;
  const preview = await previewVault(api, origin, headers, picked, '');
  const link = await makeWorkFolder({ srv, origin, api, ws, headers, name, picked, preview, body: { new_folder: true }, tick });
  return link && { link, srv, ws, name };
}

// The places a work folder does not take without a question (broadOk):
// the system's own folders for documents and media, and a cloud drive's
// root. Each the place itself only: `Documents/paper` is a project.
function knownPlaces() {
  const places = [];
  for (const [key, label] of [['desktop', 'Desktop'], ['documents', 'Documents'], ['downloads', 'Downloads'], ['music', 'Music'],
    ['pictures', 'Pictures'], ['videos', 'Videos']]) {
    try {
      places.push({ label, path: app.getPath(key) });
    } catch {} // a folder this system does not have
  }
  for (const key of ['OneDrive', 'OneDriveConsumer', 'OneDriveCommercial']) {
    if (process.env[key]) places.push({ label: 'OneDrive', path: process.env[key] });
  }
  if (process.platform === 'darwin') {
    places.push({ label: 'iCloud Drive', path: path.join(app.getPath('home'), 'Library', 'Mobile Documents', 'com~apple~CloudDocs') });
  }
  return places;
}

// Whether a vault may take the directory `dir`: asked first when it is one
// of knownPlaces (the Desktop, Downloads…), which holds a person's loose
// files rather than a project; true for any other. The server refuses home
// and the system's places itself (folder_links.place_refusal). Asked by
// every way the shell gives a vault a directory: a work folder made
// (workFolderOf), a folder's *Sync with this computer…* and *Locate…*;
// `work` false for those two, which make or move a synced vault that need
// not be a work folder, so the question does not say one. Under the test
// harness the question takes Cancel.
async function broadOk(dir, work = true) {
  const label = openFiles.broadPlace(dir, knownPlaces());
  if (!label) return true;
  const r = await ctx.ask(work ? {
    type: 'question', buttons: ['Make it a synced folder', 'Cancel'], defaultId: 1, cancelId: 1,
    message: `Make all of ${label} a synced folder?`,
    detail: `${dir} is your ${label} folder. Gamma would write a hidden .gamma folder there, read the folder while it runs, and ask which of its files to track. For one project, open that project's folder instead.`,
  } : {
    type: 'question', buttons: ['Use it', 'Cancel'], defaultId: 1, cancelId: 1,
    message: `Sync all of ${label} with Gamma?`,
    detail: `${dir} is your ${label} folder. Gamma would write a hidden .gamma folder there and read the folder while it runs. For one project, pick that project's folder instead.`,
  }, 1);
  return r === 0;
}

// Page `pageId` of the open server's workspace `wsId` pushed from a PDF the
// user picks: a vault of one file, which only pushes (each new version of
// the file becomes the page's PDF as "Replace the PDF…" makes it, the
// highlights moved to where their text is) and never writes the file. On a
// REMOTE server the vault is the host's, with a token minted there that may
// write. A page that has one already offers to show the file or to remove
// the vault.
async function addFileVault(wsId, pageId) {
  const shown = ctx.current; // the server it is asked of, whatever the window opens while the picker is up
  const remote = shown && shown.type === 'remote';
  const origin = remote ? ctx.remoteOf() : ctx.openOrigin();
  const api = ctx.serverApi(ctx.content.webContents.session);
  const wsHeaders = { 'X-Gamma-Workspace': wsId };
  const page = await api(origin, `/api/blocks/${encodeURIComponent(pageId)}`, { headers: wsHeaders });
  const title = page.content || 'Untitled';
  const synced = (await listLinks(api, wsId)).find((l) => l.kind === 'file' && l.page_id === pageId);
  if (synced) {
    const r = await ctx.ask({
      type: 'question', buttons: ['Show in folder', 'Remove the vault…', 'Cancel'], defaultId: 0, cancelId: 2,
      message: `“${title}” is pushed from ${synced.dest}`,
      detail: 'Each new version of the file becomes its PDF.',
    }, 0);
    if (r === 0) ctx.revealFile(synced.dest);
    if (r === 1) await ctx.removeVault(synced.server, synced.id);
    return synced;
  }
  const picked = await ctx.pickPdf(`Push “${title}” from a file`, `Choose a PDF on this computer. Each time it changes, it becomes the PDF of “${title}”.`, 'Push from it');
  if (!picked) return null;
  if (!remote) {
    const way = await oneByOne(api, ctx.localOrigin(registry.get(shown.id)), wsId, picked);
    if (!way) return null;
    ctx.stillOn(shown);
    if (way === 'folder') return addDirectoryVault(path.dirname(picked), wsId);
  }
  const file = path.basename(picked);
  ctx.busy = `Pushing ${file}…`;
  ctx.pushState();
  let token = null;
  try {
    let link;
    let unshown = null; // why this computer is not shown on the remote, the vault made all the same
    if (remote) {
      // The host, which says whether a token may go there (beforeMint); a write token on the remote, for the
      // host to replace the page's PDF with; the vault on the host; then this computer shown there (showComputer).
      let host = await ctx.ensureHost(api);
      if (await ctx.reachFolder(host.local, picked)) host = await ctx.ensureHost(api);
      await ctx.beforeMint(api, host.origin, origin);
      token = await api(origin, '/api/integrations/tokens', {
        method: 'POST',
        headers: { ...ctx.JSON_TYPE, ...wsHeaders },
        body: JSON.stringify({ name: `Gamma desktop: ${title} from ${file}`.slice(0, 80), scope: 'write', expires_in_days: 365 }),
      });
      link = await api(host.origin, '/api/folder-links', {
        method: 'POST',
        headers: { ...ctx.JSON_TYPE, ...ctx.shellWord(host.origin) },
        body: JSON.stringify({ kind: 'file', page: pageId, path: picked, remote_url: origin, workspace: wsId, token: token.token, token_id: token.id }),
      });
      token = null; // the host holds it now
      registry.setSettings({ folderHost: host.local.id }); // runs at launch with the clones' hosts
      unshown = await ctx.showComputer({ remote: api, remoteOrigin: origin, remoteName: shown.name, wsId, host: { origin: host.origin, api } });
    } else {
      ctx.stillOn(shown);
      const local = registry.get(shown.id);
      await ctx.reachFolder(local, picked);
      ctx.stillOn(shown);
      // its origin now: reachFolder may have restarted the server on another port
      const at = ctx.localOrigin(local);
      link = await api(at, '/api/folder-links', {
        method: 'POST',
        headers: { ...ctx.JSON_TYPE, ...ctx.shellWord(at), ...wsHeaders },
        body: JSON.stringify({ kind: 'file', page: pageId, path: picked }),
      });
    }
    ctx.showNotice(`“${title}” is pushed from ${file}: each new version becomes its PDF.${unshown ? ` ${unshown}` : ''}`);
    ctx.offerBackground(`“${title}”`).catch(() => {});
    ctx.refreshKeeping().catch(() => {});
    return link;
  } catch (e) {
    if (token) await ctx.revokeToken(api, origin, wsId, token.id); // minted for a vault that was never made
    throw e;
  } finally {
    ctx.busy = null;
    ctx.pushState();
  }
}

// A path as the file system tells paths apart: by case only on Linux.
const samePath = (p) => (process.platform === 'linux' ? path.resolve(p) : path.resolve(p).toLowerCase());

// The one hint about vaults of one file (docs/research/vaults.md "A vault of
// one file, and .gamma/"): the vault of `file` would be the third or a later
// one of the files of its directory in workspace `wsId` of the local server
// at `origin`, so the question offers the directory's vault instead, which
// takes the files' pages in. → 'file' (push this file only), 'folder' (sync
// the folder), or null (cancelled). Only a local server's own vaults, of a
// workspace a directory's vault would land in (vaultPlace: no clone, no one
// else sees it): a directory's vault of a remote's pages, or of a shared
// workspace's, is a folder of its, which the folder's "Sync with this
// computer…" makes.
async function oneByOne(api, origin, wsId, file) {
  const place = openFiles.readerPlace(await api(origin, '/api/session'), wsId);
  if (!place || place.audience || place.ws.id !== wsId) return 'file';
  const dir = path.dirname(file);
  const beside = ((await api(origin, '/api/folder-links')).links || []).filter((l) => l.kind === 'file' && !l.remote_url &&
    l.workspace_id === wsId && l.dest && samePath(l.dest) !== samePath(file) && samePath(path.dirname(l.dest)) === samePath(dir));
  if (beside.length < 2) return 'file';
  const r = await ctx.ask({
    type: 'question', buttons: ['Sync the folder…', 'Push this file only', 'Cancel'], defaultId: 0, cancelId: 2,
    message: `${beside.length + 1} files in ${dir} are synced one by one. Sync the folder instead?`,
    detail: 'A synced folder takes these files’ pages in, with their notes and highlights, and keeps every PDF and note in it in step, the ones added later too.',
  }, 1);
  return ['folder', 'file', null][r] ?? null;
}

// A vault of local server `serverId`, with what to call it through, and as
// the panel reads it (`item`, lib/keeping.js).
async function vaultOn(serverId, linkId) {
  const at = await ctx.localServer(serverId);
  const link = ((await at.api(at.origin, '/api/folder-links')).links || []).find((l) => l.id === linkId);
  if (!link) throw new Error('That vault is no longer synced with this computer');
  // One of another server says whether this computer is shown there (its agent's condition), unsaid when that list does not answer.
  const shown = link.remote_url ? await at.api(at.origin, '/api/computer-links').catch(() => null) : null;
  const computers = shown && Array.isArray(shown.links) ? { links: shown.links, name: ctx.COMPUTER_NAME } : null;
  // A vault of the server's own workspace is changed in that workspace; one of a remote's is the account's.
  return { ...at, link, item: keeping.vault(at.srv, link, registry.load().servers, null, computers),
    headers: link.remote_url ? {} : { 'X-Gamma-Workspace': link.workspace_id } };
}

// A round of a link asked for and followed to its end: the server answers
// at once with the round's `running` set (the one it started, or one
// already running, after which the round asked for runs, the link reading
// `running` until that one ends), and the link is read again every
// ROUND_POLL_MS until `running` comes down, however long the round takes:
// the server reads `running` from the round's lock, so a mark a server
// stopped mid-round left never reads as a round (gamma/folder_links.py
// _round_held). No request stays open for the round. → the link after it.
const ROUND_POLL_MS = 1_000;
async function followRound(api, origin, link, headers, query) {
  let after = await api(origin, `/api/folder-links/${link.id}/sync?${query}`, { method: 'POST', headers });
  while (after.status && after.status.running) {
    await new Promise((done) => setTimeout(done, ROUND_POLL_MS));
    after = await api(origin, `/api/folder-links/${link.id}`, { headers });
  }
  return after;
}

// A round of a vault now (`query`: its direction, a force, the guard passed),
// followed to its end (followRound), its outcome in the bar.
async function roundOf(at, query, doing) {
  const { origin, api, link, item, headers } = at;
  ctx.busy = doing;
  ctx.pushState();
  try {
    const after = await followRound(api, origin, link, headers, query);
    ctx.showNotice(keeping.roundSaid(item, after.status));
    return after;
  } finally {
    ctx.busy = null;
    ctx.pushState();
    ctx.refreshKeeping().catch(() => {});
  }
}

// Sync now (`both`: push, then pull), Pull now or Push now. A paused vault
// syncs too and stays paused.
async function syncVault(serverId, linkId, direction = 'both') {
  const at = await vaultOn(serverId, linkId);
  const doing = direction === 'pull' ? 'Pulling' : direction === 'push' || at.item.scope === 'file' ? 'Pushing' : 'Syncing';
  return roundOf(at, `direction=${direction}`, `${doing} ${at.item.name}…`);
}

// Force pull (the files become what Gamma holds) or force push (the pages
// become what their files say), after a count and a confirmation;
// `confirmed` answers it in tests, where the question takes Cancel.
async function forceVault(serverId, linkId, which, confirmed) {
  const at = await vaultOn(serverId, linkId);
  if (!confirmed) {
    const removal = await at.api(at.origin, `/api/folder-links/${at.link.id}/removal`, { headers: at.headers });
    const q = keeping.forceAsk(at.item, which, removal);
    if (await ctx.ask({ type: 'warning', message: q.message, detail: q.detail, buttons: q.buttons, defaultId: 1, cancelId: 1 }, 1) !== 0) return null;
  }
  return roundOf(at, `force=${which}`, `Force ${which === 'pull' ? 'pulling' : 'pushing'} ${at.item.name}…`);
}

// A vault its guard held (many files gone from the directory at once) goes
// on, once the user says so; `confirmed` answers it in tests.
async function goOnVault(serverId, linkId, confirmed) {
  const at = await vaultOn(serverId, linkId);
  if (!confirmed) {
    const q = keeping.goOnAsk(at.item);
    if (await ctx.ask({ type: 'question', message: q.message, detail: q.detail, buttons: q.buttons, defaultId: 0, cancelId: 1 }, 1) !== 0) return null;
  }
  return roundOf(at, 'confirm=1', `Syncing ${at.item.name}…`);
}

// Pause a vault, or resume it (a round runs then). Paused, the server's loop
// leaves it alone; a sync asked for still runs.
async function pauseVault(serverId, linkId, paused) {
  const { origin, api, link, item, headers } = await vaultOn(serverId, linkId);
  await api(origin, `/api/folder-links/${link.id}`, {
    method: 'PATCH', headers: { ...headers, ...ctx.JSON_TYPE }, body: JSON.stringify({ paused }),
  });
  ctx.showNotice(paused ? `${item.name} is paused: nothing is pulled or pushed until you resume or sync it.` : `${item.name} syncs again.`);
  ctx.refreshKeeping().catch(() => {});
  return true;
}

// Whether a vault pulls: Gamma's changes written to its directory. A vault
// of another server keeps pulling (keeping.REMOTE_RULE), a work folder there
// too (keeping.REMOTE_WORK_RULE).
async function setPull(serverId, linkId, pull) {
  const { origin, api, link, item, headers } = await vaultOn(serverId, linkId);
  if (!pull && link.remote_url) throw new Error(item.work ? keeping.REMOTE_WORK_RULE : keeping.REMOTE_RULE);
  await api(origin, `/api/folder-links/${link.id}`, {
    method: 'PATCH', headers: { ...headers, ...ctx.JSON_TYPE }, body: JSON.stringify({ pull }),
  });
  ctx.showNotice(pull ? `${item.name} pulls Gamma's changes again.` : `${item.name} no longer pulls: its files stay as they are.`);
  ctx.refreshKeeping().catch(() => {});
  return true;
}

// A work folder's library switch: whether the PDFs and notes it finds from
// now on are added to the library too (the files it holds keep theirs).
async function setLibrary(serverId, linkId, on) {
  const { origin, api, link, item, headers } = await vaultOn(serverId, linkId);
  await api(origin, `/api/folder-links/${link.id}`, {
    method: 'PATCH', headers: { ...headers, ...ctx.JSON_TYPE }, body: JSON.stringify({ library: on }),
  });
  ctx.showNotice(on ? `The PDFs and notes ${item.name} finds from now on are added to the library too.`
    : `The PDFs and notes ${item.name} finds from now on stay in the synced folder only.`);
  ctx.refreshKeeping().catch(() => {});
  return true;
}

// Choose files…: the server's window on the work folder, its tracking dialog
// open (makeWorkFolder's navigation); for a work folder of another server,
// the host's window over its own workspace, as makeRemoteWorkFolder hands
// over, the dialog's answer taking the window back there (trackedFromPage).
// `tick`, a file's path in the work folder, is ticked in the dialog (a file
// of it opened with Gamma that it does not track yet).
async function chooseFiles(serverId, linkId, tick = '') {
  const { origin, api, link, item } = await vaultOn(serverId, linkId);
  let ws = link.workspace_id;
  let at = { folder: link.folder_id, track: link.id, tick };
  if (link.remote_url) {
    const remoteOrigin = registry.originOf(link.remote_url);
    const nas = registry.load().servers.find((s) => s.type === 'remote' && registry.originOf(s.url) === remoteOrigin);
    tracking.set(link.id, { host: serverId, nas: nas ? nas.id : null, remoteOrigin, remoteName: item.remote, wsId: link.workspace_id,
      folderId: link.folder_id, tokenId: link.token_id || '', name: item.name });
    ws = await hostWorkspace(api, origin);
    at = { track: link.id, tick };
  }
  if (!ctx.current || ctx.current.id !== serverId) {
    await ctx.openServer(serverId);
    ctx.buildMenu();
  }
  await ctx.openGammaWorkspace(ws, at);
  return true;
}

// What a vault pushes: 'off', 'pdfs' or 'everything' (the Push segment of
// the vault's own view); a remote server's folder PDFs at most
// (keeping.REMOTE_RULE), a work folder there everything and nothing less
// (keeping.REMOTE_WORK_RULE), the whole library not everything
// (keeping.WHOLE_RULE). A folder of a remote server needs a write token
// for it, where it was made with a read one: a write token is minted on the
// remote with the window's session (signed into the remote, as for
// revokeToken) and handed to the host in place of the old one, which is
// then revoked; the new one is revoked instead when the host refuses it, or
// when the vault the host answers with holds another token. One change per
// vault at a time: a second click while one runs is ignored, so no two
// write tokens are minted for one vault.
const pushing = new Set();
async function setPush(serverId, linkId, word) {
  const key = `${serverId}:${linkId}`;
  if (pushing.has(key)) return false;
  pushing.add(key);
  try {
    return await changePush(serverId, linkId, word);
  } finally {
    pushing.delete(key);
  }
}

const PUSH_SAID = {
  off: (name) => `${name} no longer pushes.`,
  pdfs: (name) => `${name} pushes the PDFs you change there.`,
  everything: (name) => `${name} pushes everything: notes, PDFs, new files, renames and deletions.`,
};

async function changePush(serverId, linkId, word) {
  if (!keeping.PUSH.includes(word)) throw new Error(`Push is off, pdfs or everything, not ${word}`);
  const { origin, api, link, item, headers } = await vaultOn(serverId, linkId);
  if (link.remote_url && item.work && word !== 'everything') throw new Error(keeping.REMOTE_WORK_RULE);
  if (word === 'everything' && link.remote_url && !item.work) throw new Error(keeping.REMOTE_RULE);
  if (word === 'everything' && link.folder_id === 'root') throw new Error(keeping.WHOLE_RULE);
  const patch = (body) => api(origin, `/api/folder-links/${link.id}`, {
    method: 'PATCH', headers: { ...headers, ...ctx.JSON_TYPE }, body: JSON.stringify(body),
  });
  if (word !== 'off' && link.remote_url && keeping.pushWord(link.push) === 'off') {
    const remote = ctx.serverApi(session.defaultSession);
    const remoteOrigin = ctx.remoteOf(new URL(link.remote_url).origin);
    await ctx.beforeMint(api, origin, remoteOrigin, { vault: link.id });
    const token = await remote(remoteOrigin, '/api/integrations/tokens', {
      method: 'POST',
      headers: { ...ctx.JSON_TYPE, 'X-Gamma-Workspace': link.workspace_id },
      body: JSON.stringify({ name: `Gamma desktop: ${item.name} synced folder`.slice(0, 80), scope: 'write', expires_in_days: 365 }),
    });
    let after;
    try {
      after = await patch({ push: word, token: token.token, token_id: token.id });
    } catch (e) {
      await ctx.revokeToken(remote, remoteOrigin, link.workspace_id, token.id);
      throw e;
    }
    if (after && after.token_id && after.token_id !== token.id) {
      await ctx.revokeToken(remote, remoteOrigin, link.workspace_id, token.id); // another change won: ours is no vault's
    } else if (link.token_id && link.token_id !== token.id) {
      await ctx.revokeToken(remote, remoteOrigin, link.workspace_id, link.token_id);
    }
  } else {
    await patch({ push: word });
  }
  ctx.showNotice(PUSH_SAID[word](item.name));
  ctx.refreshKeeping().catch(() => {});
  return true;
}

// Locate…: a vault found where it is now, a directory that moved or a disk
// mounted elsewhere (its marker must be the vault's), or the file of a
// vault of one file that moved; a round follows there.
async function locateVault(serverId, linkId) {
  const { srv, origin, api, link, item, headers } = await vaultOn(serverId, linkId);
  const picked = item.scope === 'file'
    ? await ctx.pickPdf(`Where is ${path.basename(item.dest)} now?`, `Choose the PDF “${item.name}” is pushed from.`)
    : await ctx.pickDirectory(`Where is “${item.name}” now?`, `Choose the directory of the synced folder “${item.name}”, wherever it is now.`);
  if (!picked || (item.scope !== 'file' && !(await broadOk(picked, false)))) return null;
  let at = { origin, api };
  if (await ctx.reachFolder(srv, picked)) at = await ctx.localServer(serverId);
  const after = await at.api(at.origin, `/api/folder-links/${link.id}/relink`, {
    method: 'POST', headers: { ...headers, ...ctx.JSON_TYPE, ...ctx.shellWord(at.origin) }, body: JSON.stringify({ path: picked }),
  });
  ctx.showNotice(`${item.name} is synced with ${after.dest} from now on.`);
  ctx.refreshKeeping().catch(() => {});
  return after;
}

// A vault of a remote server is gone: the token the shell minted for it is
// revoked there, with the window's session, which is signed into the
// remote; the last vault of that server and workspace takes this computer
// off it (hideComputer); and once the host keeps nothing more of remote
// servers it need not start at launch for them. → what the bar adds when
// the computer's token there could not be revoked (`remoteName`, that
// server as the bar names it), else "".
async function releaseRemote(origin, api, serverId, link, remoteName) {
  const remote = ctx.serverApi(session.defaultSession);
  const remoteOrigin = registry.originOf(link.remote_url);
  if (link.token_id) await ctx.revokeToken(remote, remoteOrigin, link.workspace_id, link.token_id);
  const left = ((await api(origin, '/api/folder-links')).links || []).filter((l) => l.remote_url);
  let stranded = '';
  if (!left.some((l) => registry.originOf(l.remote_url) === remoteOrigin && l.workspace_id === link.workspace_id)) {
    const hidden = await ctx.hideComputer({ origin, api }, remote, remoteOrigin, link.workspace_id).catch(() => null);
    if (hidden && !hidden.revoked) stranded = keeping.computerStranded(remoteName || remoteOrigin, ctx.COMPUTER_NAME);
  }
  if (!left.length) await releaseHost(api, origin, serverId);
  return stranded;
}

// The host `serverId` (at `origin`, through `api`) no longer starts at launch
// for remote servers once it keeps no vault of one (the caller knows) and
// no computer link: a link's channel runs only while the host does.
async function releaseHost(api, origin, serverId) {
  if (registry.getSettings().folderHost !== serverId) return;
  const vaults = ((await api(origin, '/api/folder-links')).links || []).filter((l) => l.remote_url);
  const links = await ctx.computerLinks({ origin, api }).catch(() => null); // not read: kept, so a channel is not left down
  if (!vaults.length && links && !links.length) registry.setSettings({ folderHost: '' });
}

function init(shell) {
  ctx = shell;
}

module.exports = {
  init,
  startHost,
  vaultOn,
  locateVault,
  releaseRemote,
  releaseHost,
  hostOf,
  addDirectoryVault,
  workFolderOf,
  knownPlaces,
  chooseFiles,
  syncVault,
  forceVault,
  goOnVault,
  pauseVault,
  setPull,
  setLibrary,
  setPush,
  listFolders,
  get vaultAsk() { return vaultAsk; },
  answerVault,
  cancelVaultAsk,
  vaultAskState,
  trackedFromPage,
  addFolderVault,
  broadOk,
  addFileVault,
};
