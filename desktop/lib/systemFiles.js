// What the operating system hands the app ("Open with → Gamma", Gamma as the
// default app of a type, a folder's "Open with Gamma", a file or a folder
// dropped on the app's icon) and what Server → Open a PDF… picks
// (docs/architecture.md "Opening files and folders"). One queue takes every
// hand-over, a path at a time in the order they came (openWithGamma). Each
// local server is asked what the path is (whereIs: POST
// /api/folder-links/where, never a remote server), and the window goes by
// the ids it answers. A folder opens its vault's folder, or becomes a work
// folder of this computer's server, its tracking dialog open
// (openFolderWithGamma); a file opens its vault's page, or that tracking
// dialog with the file ticked when its work folder does not track it yet,
// or is read where it is under Opened files (readFile), a Markdown file
// outside every vault asking first whether to make its project a work folder
// (editOrView). The account's preference to add opened PDFs to the library,
// and the Mac App Store build, take the older way for a PDF (addPdf): the
// file is added to the library the window shows and opened there, as an
// upload in Gamma adds it: stored, the page carrying it found or made (`POST
// /api/blocks/by-doc/<hash>`, so a file already in the library opens its
// page), the annotations another reader saved in it imported. A workspace
// others see asks first and offers the account's own; one the account can
// only read hands the file to its own (lib/openFiles.js). The shell's words
// are English: it has no catalog.

const path = require('path');
const fs = require('fs');
const registry = require('./registry');
const sidecar = require('./sidecar');
const openFiles = require('./openFiles');
const { zipNames } = require('./zipNames');

// The shell's shared names, set by init (main.js "the sections").
let ctx = null;

// A proxy in front of a server caps a request's body, so past this a file
// goes in parts, as Gamma's own upload sends it (frontend/src/shared/lib/uploadParts.js).
const PART_BYTES = 32 * 1024 * 1024;
let booted = false; // until then what is handed over waits in `early` (macOS hands it over before `ready`)
const early = [];
let openQueue = Promise.resolve();
const MARKDOWN = /\.(md|markdown)$/i;

// Paths handed over (`cwd`, the start's working directory, for one named
// relative to it), handled one by one after the hand-overs before them: a
// directory by openFolderWithGamma, a file by openFileWithGamma, each with
// the window brought forward first. A failure is said in an error box and
// the next path goes on. → an entry per path: `{ path, workspace, page }` or
// `{ path, workspace, folder }` once it is open (with `track` and `tick` for
// a tracking dialog), `{ path, error }` when it could not be, `{ path }`
// when a question was cancelled or a notice says why nothing opened.
function openWithGamma(paths, cwd) {
  if (!booted) {
    early.push(...openFiles.openedPaths(paths, cwd).map((i) => i.path));
    return Promise.resolve([]);
  }
  const run = openQueue.then(async () => {
    const done = [];
    for (const item of openFiles.openedPaths(paths, cwd)) {
      try {
        await ctx.surfaceWindow();
        if (ctx.opening) await ctx.opening.catch(() => {});
        done.push({ path: item.path, ...(await (item.dir ? openFolderWithGamma(item.path) : openFileWithGamma(item.path))) });
      } catch (e) {
        const detail = String((e && e.message) || e);
        done.push({ path: item.path, error: detail });
        console.error(`[shell] could not open ${item.path}: ${detail}`);
        ctx.errorBox({ type: 'error', title: 'Gamma', message: `Could not open ${path.basename(item.path) || item.path} in Gamma.`, detail, buttons: ['OK'] });
      }
    }
    return done;
  });
  openQueue = run.catch(() => {});
  return run;
}

// The app is up (`on`; main.test.js may say not yet): → what was handed
// over before, to open now.
function boot(on = true) {
  booted = on;
  return early.splice(0);
}

// What path `p` is to this computer's servers (POST /api/folder-links/where,
// gamma/folder_links.py where_is): the first answer that is not
// `none`, as `{ ...answer, srv, at, as, signedOut }`, `srv` the server that
// answered, `at` the `{ origin, api }` it was asked through, `as` 'window'
// or 'shell' (askWhere). Each local server of the registry that runs is
// asked, the window's first, then each that does not, started first: a path
// is taken as nobody's only once every one was asked (a server that did not
// start is named in `unchecked`, for the notice that follows). No remote
// server is ever asked, and only ids come back to the window. Nobody's: the
// host's `none` answer, its `refusal` with it (another's when the host gave
// none, else one made here with no refusal).
const WHERE_MS = 10_000;
async function whereIs(p) {
  const locals = () => registry.load().servers.filter((s) => s.type === 'local');
  if (!locals().some((s) => sidecar.status(s.id))) await ctx.startHost();
  const all = locals();
  const host = ctx.hostOf();
  const answered = [];
  const up = all.filter((s) => sidecar.status(s.id));
  for (const srv of [...up.filter((s) => ctx.current && s.id === ctx.current.id), ...up.filter((s) => !ctx.current || s.id !== ctx.current.id)]) {
    const a = await askWhere(srv, p);
    if (a && a.is !== 'none') return a;
    if (a) answered.push(a);
  }
  const unchecked = [];
  for (const srv of all.filter((s) => !up.includes(s))) {
    ctx.busy = `Starting ${srv.name}…`;
    ctx.pushState();
    try {
      ctx.allowedOrigins.add(new URL((await ctx.startLocal(srv)).url).origin);
    } catch (e) {
      console.error(`[shell] ${srv.name} did not start to say what ${p} is: ${(e && e.message) || e}`);
      unchecked.push(srv.name);
      continue;
    } finally {
      ctx.busy = null;
      ctx.pushState();
    }
    const a = await askWhere(srv, p);
    if (a && a.is !== 'none') return a;
    if (a) answered.push(a);
  }
  const none = answered.find((a) => a.srv.id === host.id) || answered[0];
  if (none) return { ...none, unchecked };
  let dir = false;
  try {
    dir = fs.statSync(p).isDirectory();
  } catch {}
  return { is: 'none', dir, name: path.basename(p) || p, refusal: '', srv: host, at: null, as: 'shell', signedOut: false, unchecked };
}

// One local server's answer about `p`, or null when it gives none (an older
// server without the route, 404 or 405; a refusal; no answer within
// WHERE_MS; a server that stopped meanwhile): logged, never thrown. It is
// asked as the account that would navigate: through the window's session
// while the window shows that server signed in, else as the shell's account
// there (localServer), the one openServer signs the window in with. The
// window's own server asked that way, the window being signed out there, is
// marked `signedOut`, and landOn stops an answer that would take the window
// there. A 401 through the window (signed out since its session was read) is
// asked once more as the shell.
async function askWhere(srv, p) {
  let timer;
  const late = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`no answer in ${WHERE_MS / 1000} s`)), WHERE_MS);
  });
  try {
    return await Promise.race([whereOn(srv, p), late]);
  } catch (e) {
    console.error(`[shell] ${srv.name} did not say what ${p} is: ${(e && e.message) || e}`);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function whereOn(srv, p) {
  const origin = ctx.localOrigin(srv);
  const where = (api) => api(origin, '/api/folder-links/where', {
    method: 'POST', headers: { ...ctx.JSON_TYPE, ...ctx.shellWord(origin) }, body: JSON.stringify({ path: p }),
  });
  let signedOut = false;
  if (ctx.current && ctx.current.id === srv.id && ctx.content) {
    const api = ctx.serverApi(ctx.content.webContents.session);
    const info = await api(origin, '/api/session').catch(() => null);
    if (info && info.user) {
      try {
        return { ...(await where(api)), srv, at: { origin, api }, as: 'window', signedOut: false };
      } catch (e) {
        if (e.status !== 401) throw e;
      }
    }
    signedOut = true;
  }
  const own = await ctx.localServer(srv.id);
  return { ...(await where(own.api)), srv, at: { origin: own.origin, api: own.api }, as: 'shell', signedOut };
}

// An answer that would take the window to the server it came from while the
// window is signed out there (whereIs asked that server as the shell only
// to learn what the path is): stopped, with what to do.
function landOn(r, name) {
  if (r.signedOut) throw new Error(`Sign in to ${r.srv.name}, then open ${name} again.`);
}

// The window on server `srvId` (opened when another shows, signed into when
// it is a local one), then at `at` of its workspace `ws` (openGammaWorkspace).
async function showAt(srvId, ws, at) {
  if (!ctx.current || ctx.current.id !== srvId) {
    await ctx.openServer(srvId);
    ctx.buildMenu();
  }
  await ctx.openGammaWorkspace(ws, at);
}

// The registry's remote server at the origin of `url`, or null.
function remoteAt(url) {
  const origin = registry.originOf(url);
  return registry.load().servers.find((s) => s.type === 'remote' && registry.originOf(s.url) === origin) || null;
}

function hostName(url) {
  try {
    return new URL(url).host;
  } catch {
    return String(url || '');
  }
}

// Where a vault's folder `folder` opens: that folder, or the library for a
// vault of the whole library (`root`).
const folderAt = (folder) => (folder && folder !== 'root' ? { folder } : {});

// What a notice adds for the local servers whereIs could not ask (`names`).
const uncheckedSaid = (names) => (names || []).map((n) => ` Gamma could not check ${n}'s synced folders: it did not start.`).join('');

// The work folders *Open with Gamma* made in this run of the app, `{ srv, ws,
// linkId, name }`. While one still waits for its files to be chosen, the
// verb makes no other (stillChoosing): a dozen folders selected by mistake
// write one marker, not a dozen, and never reach a workspace's cap.
const waiting = [];

// The first of `waiting` that still chooses its files (GET
// /api/folder-links/<id> as the shell's account there), or null. Those
// saved, cancelled or gone, and those that cannot be asked any more, are
// dropped.
async function stillChoosing() {
  for (const w of [...waiting]) {
    let on = false;
    try {
      const at = await ctx.localServer(w.srv);
      on = Boolean((await at.api(at.origin, `/api/folder-links/${encodeURIComponent(w.linkId)}`, { headers: { 'X-Gamma-Workspace': w.ws } })).choosing);
    } catch {}
    if (on) return w;
    waiting.splice(waiting.indexOf(w), 1);
  }
  return null;
}

// A work folder of the directory `dir`, made as *Add a synced folder…* makes
// one (workFolderOf: the broad-place question, vaultPlace, the preview), its
// tracking dialog open with `tick` ticked; remembered in `waiting`, and the
// bar says where it went (`unchecked`: the servers whereIs could not ask).
// → the link, or null when asked off or refused by the preview.
async function newWorkFolder(dir, tick = '', unchecked = []) {
  const made = await ctx.workFolderOf(dir, '', tick);
  if (!made) return null;
  waiting.push({ srv: made.srv.id, ws: made.ws.id, linkId: made.link.id, name: made.name });
  ctx.showNotice(`“${made.name}” is a synced folder of ${made.ws.name} on ${made.srv.name} now: choose the files it tracks.${uncheckedSaid(unchecked)}`);
  return made.link;
}

// A directory opened with Gamma: its vault's folder (openVaultFolder), the
// one vault it holds (several: the bar names them), else a new work folder
// of this computer's server (newWorkFolder), one at a time; a place no
// vault may have is refused with the server's words. The Mac App Store
// build asks for it in the picker first, opened at it: its servers reach a
// directory only once the picker granted it.
async function openFolderWithGamma(dir) {
  if (process.mas) {
    const picked = await ctx.pickDirectory('Open with Gamma',
      'Choose this folder to let Gamma read it: the App Store version reaches a folder only once you choose it here.', dir);
    if (!picked) return {};
    const link = await ctx.addDirectoryVault(picked);
    return link ? { workspace: link.workspace_id, folder: link.folder_id, track: link.id } : {};
  }
  const r = await whereIs(dir);
  if (r.is === 'hidden') throw new Error(`${dir} is kept by another account on this computer.`);
  if (r.is === 'vault') {
    const opened = await openVaultFolder(r, r.link, dir, r);
    if (opened.said) ctx.showNotice(opened.said);
    return opened.at;
  }
  const vaults = r.is === 'around' ? r.vaults || [] : [];
  if (vaults.length > 1) {
    ctx.showNotice(`${dir} holds ${vaults.length} synced folders (${vaults.map((v) => `“${v.name}”`).join(', ')}): open one of their folders with Gamma, or pick it in the sync panel.`);
    return {};
  }
  if (vaults.length === 1) {
    const [v] = vaults;
    const opened = await openVaultFolder(r, { ...v, kind: 'folder' }, dir, { folder: v.folder });
    ctx.showNotice(`${dir} holds the synced folder “${v.name}”, which opened: a synced folder is never inside or around another.`);
    return opened.at;
  }
  if (r.refusal) throw new Error(r.refusal);
  const w = await stillChoosing();
  if (w) {
    ctx.showNotice(`Choose the files of “${w.name}” first, then open ${dir} with Gamma again.`);
    return {};
  }
  const link = await newWorkFolder(dir, '', r.unchecked);
  return link ? { workspace: link.workspace_id, folder: link.folder_id, track: link.id } : {};
}

// The folder of a vault a directory belongs to (`link`, as where names it;
// `folder`, the directory's folder there, with `rel`, `exact` and `never`
// for a directory inside it): a work folder still choosing opens its
// tracking dialog, its maker's alone; else the window on the vault's server
// (for a vault of another server, that server, which the registry must
// hold) at that folder, a vault of the whole library at the library. A
// paused vault opens as it is; a kept folder is never raised to a work
// folder from here. → `{ at, said }`: the entry openWithGamma answers, and
// the notice ('' for none).
async function openVaultFolder(r, link, dir, { folder, rel = '', exact = true, never = '' } = {}) {
  const name = path.basename(dir) || dir;
  const W = link.name;
  if (link.work && link.choosing) {
    if (!link.mine) throw new Error(`Only the account that made the synced folder “${W}” chooses what it tracks.`);
    landOn(r, name);
    await ctx.chooseFiles(r.srv.id, link.id);
    return { at: { workspace: link.workspace, folder: link.folder, track: link.id }, said: `“${W}” waits for its files to be chosen.` };
  }
  if (link.remote_url) {
    const nas = remoteAt(link.remote_url);
    if (!nas) throw new Error(`${dir} is synced with ${hostName(link.remote_url)}: add that server to Gamma to open it here.`);
    await showAt(nas.id, link.workspace, folderAt(folder));
    return { at: { workspace: link.workspace, folder }, said: '' };
  }
  landOn(r, name);
  await showAt(r.srv.id, link.workspace, folderAt(folder));
  let said = '';
  if (never) said = `Gamma never tracks ${rel}: ${never}. “${W}” opened.`;
  else if (!exact) {
    said = `Nothing in ${rel} is tracked yet: “${W}” opened at ${await folderName(r, link, folder)}.${link.work ? ' Choose files… picks what it tracks.' : ''}`;
  }
  return { at: { workspace: link.workspace, folder }, said };
}

// Folder `folder` of a vault's workspace as a notice names it: “<the
// vault>” for its own, else as that server's folder list does.
async function folderName(r, link, folder) {
  if (!folder || folder === link.folder) return `“${link.name}”`;
  try {
    const tree = await r.at.api(r.at.origin, '/api/sync/folders', { headers: { 'X-Gamma-Workspace': link.workspace } });
    const f = (tree.folders || []).find((x) => x.id === folder);
    if (f && f.path && f.path.length) return `“${f.path[f.path.length - 1]}”`;
  } catch {}
  return 'its nearest folder';
}

// A file opened with Gamma: inside a vault, as openVaultFile says; a vault of
// one file, its page; else it is read where it is under Opened files
// (readFile), a Markdown file outside every vault asking first (editOrView).
// The Mac App Store build opens PDFs only, added to the library (addPdf).
async function openFileWithGamma(file) {
  const name = path.basename(file);
  if (process.mas) {
    if (/\.pdf$/i.test(name)) return addPdf(file);
    throw new Error('This build of Gamma opens PDFs only.');
  }
  const r = await whereIs(file);
  if (r.is === 'vault') return openVaultFile(r, file);
  if (r.is === 'file_vault') {
    landOn(r, name);
    await showAt(r.srv.id, r.workspace, r.page);
    return { workspace: r.workspace, page: r.page };
  }
  if (r.is === 'none' && MARKDOWN.test(name)) return editOrView(file, r);
  return readFile(file, { more: uncheckedSaid(r.unchecked) });
}

// A file inside a vault's directory (`r`, where's answer): one its scan
// never tracks is refused, and so is one whose page is in Recently deleted;
// one with a page opens it, its viewer following its format (no new page,
// nothing copied); a work folder's file it does not track yet opens the
// tracking dialog with it ticked, its maker's alone; one it tracks with no
// page yet is opened by the server (POST /api/folder-links/open, whose
// round reads it), unless the vault is paused; a file the vault does not
// read (a kept folder's that is not its own) is read as a lone file, having
// one reader. A vault of another server: its page there, the host's dialog
// for a work folder's file it does not track yet, never a page here for one
// it tracks.
async function openVaultFile(r, file) {
  const name = path.basename(file);
  const { link } = r;
  const W = link.name;
  if (r.never) throw new Error(`Gamma never tracks ${name}: ${r.never}.`);
  if (r.trashed) throw new Error(`${name} is a file of “${W}”, whose page is in Recently deleted: restore it there.`);
  if (r.page) {
    if (link.remote_url) {
      const nas = remoteAt(link.remote_url);
      if (!nas) throw new Error(`${name} is synced with ${hostName(link.remote_url)}: add that server to Gamma to open it here.`);
      await showAt(nas.id, link.workspace, r.page);
    } else {
      landOn(r, name);
      await showAt(r.srv.id, link.workspace, r.page);
    }
    return { workspace: link.workspace, page: r.page };
  }
  if (link.work && (link.choosing || !r.tracked)) {
    if (!link.mine) throw new Error(`Only the account that made the synced folder “${W}” chooses what it tracks: ask it to track ${name}.`);
    landOn(r, name);
    await ctx.chooseFiles(r.srv.id, link.id, r.rel);
    ctx.showNotice(`${name} is not tracked by “${W}” yet: tick it and save to open it.`);
    return { workspace: link.workspace, folder: link.folder, track: link.id, tick: r.rel };
  }
  if (!link.work) return readFile(file);
  if (link.remote_url) throw new Error(`${name} has not reached ${hostName(link.remote_url)} yet: it opens there after the synced folder's next round.`);
  if (link.paused) throw new Error(`“${W}” is paused: resume it in the sync panel, then open ${name} again.`);
  landOn(r, name);
  ctx.busy = `Opening ${name}…`;
  ctx.pushState();
  let opened;
  try {
    opened = await r.at.api(r.at.origin, '/api/folder-links/open', {
      method: 'POST',
      headers: { 'X-Gamma-Workspace': link.workspace, ...ctx.JSON_TYPE, ...ctx.shellWord(r.at.origin) },
      body: JSON.stringify({ path: file }),
    });
  } finally {
    ctx.busy = null;
    ctx.pushState();
  }
  await showAt(r.srv.id, opened.workspace, opened.page);
  return { workspace: opened.workspace, page: opened.page };
}

// The answers of the question about a Markdown file outside every vault.
const EDIT = 0;
const VIEW = 1;

// A Markdown file outside every vault. A note is edited only through a work
// folder, whose pull writes Gamma's edits back to the file and settles a
// change made in another editor meanwhile, and Gamma never writes a lone
// file: so the person is asked, each time, whether to make the file's
// project (openFiles.projectDir: its repository, else its own directory) a
// work folder with the file ticked (Edit in Gamma), or to read the file as
// it is (Just view it: readFile, read-only in the code view). Not asked
// when neither the project nor the file's directory can be a work folder (a
// refused place, a directory holding a vault): the file is read and the
// notice says why; nor while a work folder this run made still chooses its
// files: nothing opens. Edit in Gamma goes on as a folder opened with Gamma
// (newWorkFolder: the broad-place question, the preview, its tracking
// dialog), and whatever stops it on the way opens nothing: the person asked
// to edit, not to read. Under the test harness the question takes Just view
// it.
async function editOrView(file, r) {
  const name = path.basename(file);
  const own = path.dirname(file);
  const project = openFiles.projectDir(file, { places: ctx.knownPlaces() });
  let dir = '';
  let reason = '';
  let unchecked = r.unchecked || [];
  for (const d of project === own ? [own] : [project, own]) {
    const a = await whereIs(d);
    if (a.unchecked) unchecked = a.unchecked;
    if (a.is === 'none' && !a.refusal) {
      dir = d;
      break;
    }
    reason = a.is === 'none' ? a.refusal : `${d} holds a synced folder, so it cannot be one itself.`;
  }
  if (!dir) return readFile(file, { more: ` ${reason}${uncheckedSaid(unchecked)}` });
  const w = await stillChoosing();
  if (w) {
    ctx.showNotice(`Choose the files of “${w.name}” first, then open ${name} again.`);
    return {};
  }
  const repo = fs.existsSync(path.join(dir, '.git'));
  const choice = await ctx.ask({
    type: 'question', buttons: ['Edit in Gamma', 'Just view it'], defaultId: EDIT, cancelId: VIEW,
    message: `Edit ${name} in Gamma?`,
    detail: `${name} is in no synced folder. Edit in Gamma makes ${dir} a synced folder: Gamma writes a hidden .gamma folder there, asks which of its files to track, and saves your edits back to ${name}. Just view it opens the file read-only.${repo ? ` ${dir} is the repository ${name} is in.` : ''}`,
  }, VIEW);
  if (choice !== EDIT) return readFile(file, { more: uncheckedSaid(unchecked), hint: true });
  const tick = path.relative(dir, file).split(path.sep).join('/');
  const link = await newWorkFolder(dir, tick, unchecked);
  return link ? { workspace: link.workspace_id, folder: link.folder_id, track: link.id, tick } : {};
}

// A file read where it is, of any format Gamma can show: `POST
// /api/folder-links/open` on a local server, never a remote one, so a paper
// opened on a train is not sent to the NAS. It lands in the workspace the
// window shows when that is a local server's, the account writes there and
// it is no clone, else in the host's personal workspace
// (openFiles.readerPlace), as a page inside *Opened files* in the viewer of
// its format, and the bar says where. Before the host takes a PDF, a page
// of the workspace the window shows that carries those bytes already opens
// instead (openCarried): a paper annotated on the NAS, in a clone or in a
// workspace the account only reads opens its page there. A workspace others
// see asks first, its members being able to read the file through the
// page, and offers the account's own. The server opens a file inside a
// vault as that vault's page, bytes a page of the workspace stores as that
// page, and a file opened before as its page; a Markdown file outside every
// vault is a text document there, read-only. The annotations another reader
// saved in a new page's PDF become Gamma's (importAnnotations).
// `openAddsToLibrary` in the account's profile on the open server, and the
// Mac App Store build, whose servers reach only the folders the app held
// when they started, add a PDF instead (addPdf); that build opens no other
// file. `more`: words the notice adds; `hint`: a lone Markdown file whose
// question was answered Just view it, and the notice says how to edit it.
async function readFile(file, { more = '', hint = false } = {}) {
  const pdf = /\.pdf$/i.test(file);
  if (process.mas) {
    if (pdf) return addPdf(file);
    throw new Error('This build of Gamma opens PDFs only.');
  }
  await ctx.surfaceWindow();
  if (ctx.opening) await ctx.opening.catch(() => {});
  if (!ctx.current) {
    await ctx.openServer(ctx.hostOf().id);
    ctx.buildMenu();
  }
  const api = ctx.serverApi(ctx.content.webContents.session);
  if (pdf && await addsToLibrary(api, ctx.openOrigin())) return addPdf(file);
  const name = path.basename(file);
  // (a remote's session is not asked: a NAS out of reach still lets the file be read here)
  let info = ctx.current.type === 'local' ? await api(ctx.openOrigin(), '/api/session') : null;
  let place = info && openFiles.readerPlace(info, ctx.currentWsId());
  if (!place) {
    // Only a PDF is looked for by its hash: another server holds no page of a file read here.
    const carried = pdf ? await openCarried(api, file) : null;
    if (carried) return carried;
    const host = ctx.hostOf();
    if (ctx.current.id !== host.id) {
      await ctx.openServer(host.id);
      ctx.buildMenu();
    }
    info = await api(ctx.openOrigin(), '/api/session');
    const mine = openFiles.personalOf(info);
    if (!mine) throw new Error(`Sign in to ${ctx.current.name}, then open the file again.`);
    place = { ws: mine, audience: '', mine: null };
  }
  if (ctx.current.type !== 'local') throw new Error('A file is read where it is by a server on this computer only.');
  const at = ctx.current; // the server it is read on, whatever the window opens meanwhile
  let ws = place.ws;
  // A page of a workspace others see reads the file for each of them: asked
  // first, as addPdf asks, with the account's own offered instead.
  if (place.audience) {
    const buttons = [...(place.mine ? [`Open in ${place.mine.name}`] : []), `Open in ${ws.name}`, 'Cancel'];
    const r = await ctx.ask({
      type: 'question', buttons, defaultId: 0, cancelId: buttons.length - 1,
      message: `Open “${name}” in ${ws.name}?`,
      detail: `${ws.name} is shared: everyone in it can see the page and read the file from this computer while Gamma runs.`,
    }, 0);
    if (r === buttons.length - 1) return {};
    if (r === 0 && place.mine) ws = place.mine;
  }
  const origin = ctx.localOrigin(registry.get(at.id));
  ctx.busy = `Opening ${name}…`;
  ctx.pushState();
  try {
    const opened = await api(origin, '/api/folder-links/open', {
      method: 'POST',
      headers: { 'X-Gamma-Workspace': ws.id, ...ctx.JSON_TYPE, ...ctx.shellWord(origin) },
      body: JSON.stringify({ path: file }),
    });
    const headers = { 'X-Gamma-Workspace': opened.workspace };
    // (a server older than `format` opened PDFs only)
    if (opened.created && (opened.format ? opened.format === 'pdf' : pdf)) {
      const page = await api(origin, `/api/blocks/${encodeURIComponent(opened.page)}`, { headers });
      const doc = (page.properties || {}).doc;
      if (doc && doc.format === 'pdf' && doc.hash) await importAnnotations(api, origin, headers, opened.page, doc.hash);
    }
    // Said always, a page that was there already too: where the file is read.
    const where = ((info.workspaces || []).find((w) => w.id === opened.workspace) || ws).name;
    const edit = hint && opened.format === 'text' && MARKDOWN.test(name) ? ' To edit it as a note, open it with Gamma again and pick Edit in Gamma.' : '';
    ctx.showNotice(`${opened.created ? `${opened.title || name} is read from this computer, under Opened files in ${where} on ${at.name}.`
      : `${opened.title || name} is read from this computer, in ${where} on ${at.name}.`}${edit}${more}`);
    if (!ctx.current || ctx.current.id !== at.id) await ctx.openServer(at.id);
    await ctx.openGammaWorkspace(opened.workspace, opened.page);
    return { workspace: opened.workspace, page: opened.page };
  } finally {
    ctx.busy = null;
    ctx.pushState();
  }
}

// The page of the workspace the window shows that carries the file's bytes
// already, opened there as an added file's page opens (`GET
// /api/blocks/by-doc/<hash>`, the hash made here, so nothing of the file
// is sent): `{ workspace, page }`. null when no page there holds them or
// the server does not answer: the file is then read on this computer.
async function openCarried(api, file) {
  const shown = ctx.currentWsId();
  if (!shown) return null;
  const at = ctx.current; // the server asked, whatever the window opens meanwhile
  const origin = ctx.openOrigin();
  const name = path.basename(file);
  ctx.busy = `Opening ${name}…`;
  ctx.pushState();
  let page;
  try {
    page = await openFiles.docIdOf(file)
      .then((docId) => api(origin, `/api/blocks/by-doc/${docId}`, {
        headers: { 'X-Gamma-Workspace': shown }, signal: AbortSignal.timeout(5000),
      }))
      .catch(() => null);
  } finally {
    ctx.busy = null;
    ctx.pushState();
  }
  if (!page || !page.id) return null;
  ctx.stillOn(at);
  const where = (((ctx.gamma && ctx.gamma.list) || []).find((w) => w.id === shown) || { name: 'this workspace' }).name;
  ctx.showNotice(`${page.content || name} is in ${where} on ${at.name} already.`);
  await ctx.openGammaWorkspace(shown, page.id);
  return { workspace: shown, page: page.id };
}

// Whether the account wants an opened PDF added to its library, as before
// (Settings: `openAddsToLibrary` in its profile on the server at `origin`).
// Not said, or not read: no.
async function addsToLibrary(api, origin) {
  try {
    const profile = await api(origin, '/api/prefs/profile', { signal: AbortSignal.timeout(5000) });
    return (profile.value || {}).openAddsToLibrary === true;
  } catch {
    return false;
  }
}

// An opened PDF's "Add to library…" for another server (docs/research/vaults.md
// "Reading a PDF where it is"): the page `pageId` of the open local server's
// workspace `wsId`, which reads its file where it is, carried to a library of
// another server the user picks (carryPage), then opened there. Another
// workspace of this server is the page's own "Add to library…" (POST
// /api/folder-links/opened/<id>/add).
async function carryToServer(wsId, pageId) {
  const done = await carryPage(wsId, pageId);
  if (!done) return null;
  ctx.showNotice(`“${done.title}” is in ${done.workspaceName} on ${done.serverName} now, with its notes, highlights and chat.`);
  await ctx.openServer(done.server);
  ctx.buildMenu();
  await ctx.openGammaWorkspace(done.workspace, done.page);
  return done;
}

// What carryToServer carries: the server asked (the other servers of the
// registry, before any is started or reached), then the workspace when it
// has several (a remote's workspaces as the window's session has them, a
// local one's as its own session does), the page and the pages inside
// it exported from here as a Gamma export (GET /api/pages/<id>/export?mode=gamma,
// which takes the PDF's bytes from the file and leaves its link to the file
// behind), merged in there (POST /api/import-data?mode=merge), taken out of
// *Opened files*, whose page stays here, and only then the page here moved
// to Recently deleted with the pages inside it, as a carry to another
// workspace here moves it (POST /api/folder-links/opened/<id>/carried with
// the digest its GET gave before the export), so the file opened again is a
// new page rather than this one back. A page written on here since that
// digest stays here (409): the copy there lacks what was written, so it is
// in both places. Each check comes before anything is written there (none
// of the pages may be a page there already, live or in Recently deleted,
// nor any of their PDFs), and nothing here goes unless the page and every
// page inside it are there, with the PDF; once they are, the page here is
// let go whatever the window opened meanwhile. Only the account
// that opened the file carries it (the page state's `reader.mine`), as only
// it takes the page to another workspace here: the file is on its computer,
// and another editor of the workspace would take that account's page, its
// notes and chat with it, away. → `{ server, serverName, workspace,
// workspaceName, page, title }`, or null when a question was cancelled.
async function carryPage(wsId, pageId) {
  if (!ctx.current || ctx.current.type !== 'local' || !ctx.content) throw new Error('Only a page of a server on this computer reads a file here.');
  const shown = ctx.current; // the server it is carried from, whatever the window opens while a question is up
  const ses = ctx.content.webContents.session;
  const api = ctx.serverApi(ses);
  const origin = ctx.localOrigin(registry.get(shown.id));
  const headers = { 'X-Gamma-Workspace': wsId };
  const state = await api(origin, `/api/folder-links/page/${encodeURIComponent(pageId)}`, { headers });
  if (!state.reader) throw new Error('This page does not read a file on this computer.');
  if (state.reader.mine !== true) {
    throw new Error('Only the account that opened this file may take it to another server: the file is on that account\'s computer.');
  }
  if (state.reader.missing) throw new Error('The file this page reads is not there: locate it first.');
  // Before anything is asked: another server stores only a PDF from here (its carried routes refuse the rest).
  if (state.reader.format && state.reader.format !== 'pdf') throw new Error('Only a PDF is carried to another server.');
  const page = await api(origin, `/api/blocks/${encodeURIComponent(pageId)}`, { headers });
  const title = page.content || 'Untitled';
  // The page's own PDF (its `doc`: a duplicated page's `copy` owns none).
  const pageDoc = (page.properties || {}).doc;
  if (!pageDoc || pageDoc.format !== 'pdf' || !pageDoc.hash || pageDoc.copy) throw new Error('This page has no PDF.');
  const docId = pageDoc.hash;

  const others = registry.load().servers.filter((s) => s.id !== shown.id);
  if (!others.length) {
    await ctx.ask({
      type: 'info', buttons: ['OK'], defaultId: 0,
      message: `This app knows no other Gamma server to add “${title}” to.`,
      detail: 'Add one under Server → All Servers… (New local server or Add remote server…), then add the page to its library from here again.',
    }, 0);
    return null;
  }
  // Asked before the server is reached: a page's ask starts no server and
  // sends nothing anywhere on its own.
  const pick = await ctx.ask({
    type: 'question', buttons: [...others.map((s) => `To ${s.name}`), 'Cancel'], defaultId: 0, cancelId: others.length,
    message: others.length > 1 ? `Add “${title}” to the library of which server?` : `Add “${title}” to the library of ${others[0].name}?`,
    detail: 'Its notes, highlights, comments and chat go with it, and its PDF is stored there. Once it is there, the page here is moved to Recently deleted here; the file on this computer stays as it is.',
  }, others.length);
  if (!(pick >= 0 && pick < others.length)) return null;
  const srv = others[pick];
  ctx.stillOn(shown);
  let at;
  if (srv.type === 'local') {
    if (!sidecar.status(srv.id)) await ctx.startLocal(srv);
    at = await ctx.localServer(srv.id);
  } else {
    at = { srv, origin: registry.originOf(srv.url), api };
  }
  const info = await at.api(at.origin, '/api/session');
  if (!info.user) throw new Error(`Sign in to ${srv.name} first, then add the page to its library again.`);
  const places = openFiles.carryPlaces(info);
  if (!places.length) throw new Error(`Your account on ${srv.name} has no workspace it can add pages to.`);
  let ws = places[0];
  if (places.length > 1) {
    const r = await ctx.ask({
      type: 'question', buttons: [...places.map((w) => `To ${w.name}`), 'Cancel'], defaultId: 0, cancelId: places.length,
      message: `Add “${title}” to the library of which workspace of ${srv.name}?`,
      detail: 'Once it is there, the page here is moved to Recently deleted here; the file on this computer stays as it is.',
    }, places.length);
    if (!(r >= 0 && r < places.length)) return null;
    ws = places[r];
  }
  ctx.stillOn(shown);

  const there = { 'X-Gamma-Workspace': ws.id };
  // What the workspace there holds of `apiPath`: 'live', 'trashed' for a
  // page in its Recently deleted (a 404 that names its entry there), or ''
  // for nothing. The merge skips a live page, and puts the copy sent in the
  // place of a trashed one, which it deletes for good with all that was
  // written on it there (ws_backup._merge): either is refused before
  // anything is sent, as a carry to another workspace here refuses both.
  const holds = (apiPath) => at.api(at.origin, apiPath, { headers: there }).then(() => 'live', (e) => {
    if (e.status !== 404) throw e;
    return e.body && e.body.trashed ? 'trashed' : '';
  });
  const held = await holds(`/api/blocks/${encodeURIComponent(pageId)}`);
  if (held === 'trashed') throw new Error(`${ws.name} on ${srv.name} has this page in Recently deleted: restore it there and open it.`);
  if (held) throw new Error(`${ws.name} on ${srv.name} has this page already: open it there.`);
  if (await holds(`/api/blocks/by-doc/${encodeURIComponent(docId)}`)) throw new Error(`${ws.name} on ${srv.name} has a page of this PDF already: open it there.`);
  // The pages go in this server's shape (its workspace data format, named
  // by the change feed): a release older than it would keep them in a shape
  // it cannot read, so it is refused before anything is sent.
  const formatOf = (feed) => (Number.isInteger(feed.format) ? feed.format : 0);
  const [here, thereFeed] = await Promise.all([
    api(origin, '/api/sync/changes?limit=1', { headers }),
    at.api(at.origin, '/api/sync/changes?limit=1', { headers: there }),
  ]);
  if (formatOf(thereFeed) < formatOf(here)) throw new Error(`${srv.name} runs an older Gamma than this app: update it, then add the page to its library again.`);

  ctx.busy = `Adding ${title} to the library of ${srv.name}…`;
  ctx.pushState();
  try {
    // What the export takes, read before it: anything written here since
    // keeps the page here (the POST below). Each page inside it is looked
    // for there as the page was.
    const carried = `/api/folder-links/opened/${encodeURIComponent(pageId)}/carried`;
    const { digest, pages } = await api(origin, carried, { headers });
    for (const id of pages) {
      const inside = id !== pageId && await holds(`/api/blocks/${encodeURIComponent(id)}`);
      if (inside) {
        throw new Error(`A page inside “${title}” is a page of ${ws.name} on ${srv.name} already${inside === 'trashed' ? ', in Recently deleted' : ''}: take it out of “${title}” first.`);
      }
    }
    const zip = await apiBytes(ses, origin, `/api/pages/${encodeURIComponent(pageId)}/export?mode=gamma&sub_pages=1`, { headers });
    const names = zipNames(zip);
    if (!names.includes(`uploads/${docId}${pageDoc.ext}`)) {
      throw new Error('The PDF did not come with the page: its file is gone or has just changed. Locate it, or try again in a moment.');
    }
    // A page inside it whose PDF is a page there already would not go (the
    // merge skips it), and would go to Recently deleted here with its page.
    for (const name of names) {
      const doc = /^uploads\/([\w-]+)\.pdf$/.exec(name);
      if (doc && doc[1] !== docId && await holds(`/api/blocks/by-doc/${encodeURIComponent(doc[1])}`)) {
        throw new Error(`A page inside “${title}” has a PDF that ${ws.name} on ${srv.name} has a page of already: take it out of “${title}” first.`);
      }
    }
    const form = new FormData();
    form.append('file', new Blob([zip], { type: 'application/zip' }), `${pageId}-gamma.zip`);
    const merged = await at.api(at.origin, `/api/import-data?mode=merge&ws=${encodeURIComponent(ws.id)}`, { method: 'POST', headers: there, body: form });
    if (!merged.pages_added) throw new Error(`${ws.name} on ${srv.name} has this page already: open it there.`);
    try {
      await at.api(at.origin, `/api/blocks/${encodeURIComponent(pageId)}`, {
        method: 'PUT', headers: { ...there, ...ctx.JSON_TYPE }, body: JSON.stringify({ properties: { parent_page: null } }),
      });
    } catch (e) {
      throw new Error(`“${title}” went to ${ws.name} on ${srv.name}, but stays inside a page it does not have there (${e.message}). The page here is kept.`);
    }
    // Every page sent arrived, or nothing here goes: the ones skipped would
    // go to Recently deleted here with their page, with no copy there.
    if (merged.pages_skipped) {
      throw new Error(`“${title}” went to ${ws.name} on ${srv.name}, but ${merged.pages_skipped === 1 ? 'a page' : `${merged.pages_skipped} pages`} inside it did not: it has them already. The page here is kept.`);
    }
    // The page here let go on the server it came from, whatever the window
    // shows now: through the window's session, else, once the window has
    // signed into another server on this computer (a cookie does not tell
    // ports apart, so that one's is all it holds: 401), through the
    // server's own (localServer). Either way the server lets it go only for
    // the account that opened the file.
    const letGo = { method: 'POST', headers: { ...headers, ...ctx.JSON_TYPE }, body: JSON.stringify({ digest }) };
    try {
      await api(ctx.localOrigin(registry.get(shown.id)), carried, letGo).catch(async (e) => {
        if (e.status !== 401) throw e;
        const own = await ctx.localServer(shown.id);
        return own.api(own.origin, carried, letGo);
      });
    } catch (e) {
      if (e.status === 409) {
        throw new Error(`“${title}” is in ${ws.name} on ${srv.name} now, and stays here too: it changed here while it was carried, so nothing here was deleted. It is in both places now.`);
      }
      throw new Error(`“${title}” is in ${ws.name} on ${srv.name} now, but the page here could not be moved to Recently deleted: ${e.message.replace(/\.$/, '')}. It is in both places now: delete the page here to keep only the copy there.`);
    }
    return { server: srv.id, serverName: srv.name, workspace: ws.id, workspaceName: ws.name, page: pageId, title };
  } finally {
    ctx.busy = null;
    ctx.pushState();
  }
}

// A route's answer as bytes (an export's zip), through session `ses`.
async function apiBytes(ses, origin, apiPath, init) {
  const r = await ses.fetch(origin + apiPath, { credentials: 'include', cache: 'no-store', ...init });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `${apiPath}: HTTP ${r.status}`);
  }
  return Buffer.from(await r.arrayBuffer());
}

// Server → Open a PDF…: a PDF from the native picker, opened as one the
// system hands over (openWithGamma).
async function openPdfDialog() {
  const file = await ctx.pickPdf('Open a PDF', 'Choose a PDF on this computer to open in Gamma.', 'Open');
  if (file) await openWithGamma([file]);
}

async function addPdf(file) {
  const name = path.basename(file);
  await ctx.surfaceWindow();
  if (ctx.opening) await ctx.opening.catch(() => {});
  if (!ctx.current) {
    const { servers } = registry.load();
    const srv = registry.getLastOpened() || servers.find((s) => s.type === 'local') || registry.addLocal('Local');
    await ctx.openServer(srv.id);
    ctx.buildMenu();
  }
  const origin = ctx.openOrigin();
  const api = ctx.serverApi(ctx.content.webContents.session);
  const info = await api(origin, '/api/session');
  if (!info.user) throw new Error(`Sign in to ${ctx.current.name}, then open the file again.`);
  const place = openFiles.placeFor(info, ctx.currentWsId());
  if (!place) throw new Error(`Your account cannot add files to ${ctx.current.name}.`);
  let ws = place.ws;
  if (place.audience) {
    const buttons = [`Add to ${ws.name}`, ...(place.mine ? [`Add to ${place.mine.name}`] : []), 'Cancel'];
    const r = await ctx.ask({
      type: 'question', buttons, defaultId: 0, cancelId: buttons.length - 1,
      message: `Add “${name}” to ${ws.name}?`,
      detail: place.audience === 'clone' ? `${ws.name} is a clone: what you add reaches the workspace it follows too.`
        : `${ws.name} is shared: everyone in it can see what you add.`,
    }, 0);
    if (r === buttons.length - 1) return {};
    if (r === 1 && place.mine) ws = place.mine;
  }
  ctx.busy = `Adding ${name}…`;
  ctx.pushState();
  try {
    const headers = { 'X-Gamma-Workspace': ws.id };
    const up = await uploadPdf(api, origin, headers, file, name);
    const page = await api(origin, `/api/blocks/by-doc/${encodeURIComponent(up.doc_id)}`, {
      method: 'POST',
      headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify({ default_title: name, source_url: up.source_url, original_filename: name }),
    });
    await importAnnotations(api, origin, headers, page.id, up.doc_id);
    await ctx.openGammaWorkspace(ws.id, page.id);
    if (place.readOnly) ctx.showNotice(`${name} went into ${ws.name}: you can only read ${place.readOnly.name}.`);
    return { workspace: ws.id, page: page.id };
  } finally {
    ctx.busy = null;
    ctx.pushState();
  }
}

// The file into the workspace's uploads → `{ doc_id, source_url }`: one
// request, or in parts past PART_BYTES (open, each part, finish), read from
// disk as it goes.
async function uploadPdf(api, origin, headers, file, name) {
  const blob = await fs.openAsBlob(file, { type: 'application/pdf' });
  if (blob.size <= PART_BYTES) {
    const form = new FormData();
    form.append('file', blob, name);
    return api(origin, '/api/uploads', { method: 'POST', headers, body: form });
  }
  const opened = await api(origin, '/api/uploads/parts', {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ size: blob.size, name }),
  });
  const parts = `/api/uploads/parts/${opened.token}`;
  const step = Math.max(1, Math.min(PART_BYTES, opened.part_bytes || PART_BYTES));
  try {
    for (let offset = 0; offset < blob.size;) {
      const form = new FormData();
      form.append('offset', String(offset));
      form.append('part', blob.slice(offset, Math.min(offset + step, blob.size)), 'part');
      offset = (await api(origin, parts, { method: 'POST', headers, body: form })).received;
      ctx.busy = `Adding ${name}… ${Math.round((offset / blob.size) * 100)}%`;
      ctx.pushState();
    }
    return await api(origin, `${parts}/finish`, { method: 'POST', headers });
  } catch (e) {
    api(origin, parts, { method: 'DELETE', headers }).catch(() => {}); // the server drops what it held
    throw e;
  }
}

// Annotations another reader saved in the file become Gamma highlights, as
// on an upload in Gamma, and leave the stored copy unless the account chose
// to only hide them (Settings → Embedded PDF annotations: `embAnnots` in its
// profile, frontend/src/app/prefDefs.js). Best effort: the page opens either way.
async function importAnnotations(api, origin, headers, pageId, docId) {
  let strip = true;
  try {
    const profile = await api(origin, '/api/prefs/profile', { headers, signal: AbortSignal.timeout(5000) });
    strip = (profile.value || {}).embAnnots !== 'hide';
  } catch {}
  await api(origin, '/api/import/pdf-annotations', {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ block_id: pageId, doc_id: docId, strip }),
  }).catch((e) => console.error(`[shell] annotations in ${docId}: ${e.message || e}`));
}

function init(shell) {
  ctx = shell;
}

module.exports = {
  init,
  remoteAt,
  get booted() { return booted; },
  openWithGamma,
  boot,
  whereIs,
  waiting,
  readFile,
  carryToServer,
  carryPage,
  openPdfDialog,
};
