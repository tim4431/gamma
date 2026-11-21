// The bar's sync button and its panel (docs/architecture.md "The sync
// panel"): every clone, Zotero link and synced vault the running local
// servers keep, read from each in its own session
// (localServer, signed in on first use), turned into states and one
// summary by lib/keeping.js, and pushed in the shell state as `keeping`.
// Read every KEEPING_MS, every KEEPING_BUSY_MS while something syncs, on
// the panel's opening and after each action. A row names its server, so
// the panel acts whichever server the window shows. Each reading also
// records which servers hold a Zotero link that is not detached
// (registry.setZoteroHost), the ones startBackgroundHosts starts.

const registry = require('./registry');
const sidecar = require('./sidecar');
const keeping = require('./keeping');

// The shell's shared names, set by init (main.js "the sections").
let ctx = null;

const KEEPING_MS = 15_000;
const KEEPING_BUSY_MS = 2_000;
let keepingState = { items: [], summary: keeping.summarize([]) };
let asksWanted = false; // whether the last reading of the host wanted its asks followed (refreshKeeping)
let keepingTimer = null;
let keepingRead = null; // the reading in flight
let keepingNext = null; // the reading asked for while it ran, which follows it

// A timer that comes due while a reading runs leaves it be: that reading
// schedules the next when it ends.
function scheduleKeeping(ms) {
  clearTimeout(keepingTimer);
  keepingTimer = setTimeout(() => keepingRead || refreshKeeping().catch(() => {}), ms);
}

// A call while a reading runs is answered by a reading that starts once
// that one ends. An action calls it right after changing something on a
// server, which the running reading may have read before the change: that
// reading's result is dropped, and the panel and the launch record wait
// for the one that follows.
function refreshKeeping() {
  if (keepingRead) {
    keepingNext ||= keepingRead.catch(() => {}).then(() => {
      keepingNext = null;
      return refreshKeeping();
    });
    return keepingNext;
  }
  keepingRead = (async () => {
    const { servers } = registry.load();
    const items = [];
    const zoteroHosts = new Map(); // server id -> holds a link that is not detached
    const moves = new Map(); // a redirected address -> the same server over https (autoUpgrade)
    const net = ctx.netState();
    const host = servers.find((s) => s.type === 'local');
    let asksHere = null; // whether an agent may ask while the host answers another server (any link), keeps a work folder of one or serves a co-author; null: not read
    const detached = []; // the vaults of work folders their other server detached, forgotten here once read
    for (const srv of servers) {
      if (srv.type !== 'local' || !sidecar.status(srv.id)) continue;
      try {
        const { origin, api } = await ctx.localServer(srv.id);
        // (a server from before the Zotero links has no list of them: none; the session's
        // workspaces say which vaults' workspaces the account is still in, unread when it does not answer;
        // the agents it finds on this computer, none from a server that runs no agents, which answers 404;
        // the servers it shows this computer on, for its vaults of another server, unsaid when it does not answer)
        const [m, f, z, who, found, shown] = await Promise.all([api(origin, '/api/mirrors'), api(origin, '/api/folder-links'),
          api(origin, '/api/zotero/links').catch(() => null), api(origin, '/api/session').catch(() => null),
          api(origin, '/api/local-agents', { signal: AbortSignal.timeout(5000) }).catch(() => null),
          api(origin, '/api/computer-links').catch(() => null)]);
        const roles = who && Array.isArray(who.workspaces) ? who.workspaces : null;
        const agents = found && Array.isArray(found.agents) ? found.agents : null;
        const computers = shown && Array.isArray(shown.links) ? { links: shown.links, name: ctx.COMPUTER_NAME } : null;
        const kept = keeping.itemsOf(srv, { mirrors: m.mirrors, links: f.links, zotero: z ? z.links : [], roles, computers }, servers, net);
        // A clone, a vault or a computer link redirected to the same server over https moves there by itself.
        for (const row of [...(m.mirrors || []), ...(f.links || []), ...((computers && computers.links) || [])]) {
          const s = (row && row.status) || {};
          const to = s.error_kind === 'redirected' ? keeping.upgradeOf(row.remote_url, s.move_to || '') : '';
          if (to) moves.set(registry.originOf(row.remote_url), to);
        }
        // A server with a link that is not detached starts at launch; one
        // whose links all went or were detached no longer does. A list that
        // did not come leaves that as it was.
        if (z) zoteroHosts.set(srv.id, kept.some((i) => i.kind === 'zotero' && !i.paused));
        if (host && srv.id === host.id && agents) {
          // a server this computer answers (any chat there asks here), a work folder of another server, or a
          // co-author of this one (its asks are this computer's too)
          asksHere = agents.some((a) => a.enabled && a.runs === 'ask') && (((computers && computers.links) || []).length > 0
            || kept.some((i) => i.kind === 'vault' && i.remoteUrl && i.work)
            || agents.some((a) => a.enabled && a.runs === 'ask' && (a.people || []).some((p) => !p.server)));
        }
        detached.push(...kept.filter((i) => i.kind === 'vault' && i.letGo && i.mine));
        items.push(...kept);
      } catch {
        // a server that does not answer now is left out of this reading
      }
    }
    if (keepingNext) return;
    for (const [id, on] of zoteroHosts) registry.setZoteroHost(id, on);
    for (const [from, to] of moves) ctx.autoUpgrade(from, to);
    // The asks are followed while the host keeps a folder that may ask here,
    // and stop once a reading of it says none does; a reading that did not
    // come leaves the follower as it is.
    if (asksHere !== null) asksWanted = asksHere;
    if (asksHere === true) ctx.followAsks(host.id);
    else if (asksHere === false) ctx.stopAsks();
    keepingState = { items, summary: keeping.summarize(items) };
    ctx.pushState();
    for (const item of detached) ctx.forgetDetached(item);
  })().finally(() => {
    keepingRead = null;
    scheduleKeeping(keepingState.summary.state === 'busy' ? KEEPING_BUSY_MS : KEEPING_MS);
  });
  return keepingRead;
}

// What a vault's row and its view in the panel do (VAULT_ACTIONS), `value`
// the switch's new setting for `pull` and `library` (a bool) and `push` (a
// word); `choose` opens a work folder's tracking dialog in Gamma;
// `show-computer` and `hide-computer` show this computer on a vault of
// another server's server, or stop showing it.
const VAULT_ACTIONS = {
  pause: (serverId, id) => ctx.pauseVault(serverId, id, true),
  resume: (serverId, id) => ctx.pauseVault(serverId, id, false),
  sync: (serverId, id) => ctx.syncVault(serverId, id, 'both'),
  'pull-now': (serverId, id) => ctx.syncVault(serverId, id, 'pull'),
  'push-now': (serverId, id) => ctx.syncVault(serverId, id, 'push'),
  pull: (serverId, id, value) => ctx.setPull(serverId, id, Boolean(value)),
  push: (serverId, id, value) => ctx.setPush(serverId, id, value),
  'force-pull': (serverId, id) => ctx.forceVault(serverId, id, 'pull'),
  'force-push': (serverId, id) => ctx.forceVault(serverId, id, 'push'),
  'go-on': (serverId, id) => ctx.goOnVault(serverId, id),
  locate: (serverId, id) => ctx.locateVault(serverId, id),
  remove: (serverId, id) => ctx.removeVault(serverId, id),
  choose: (serverId, id) => ctx.chooseFiles(serverId, id),
  library: (serverId, id, value) => ctx.setLibrary(serverId, id, Boolean(value)),
  'show-computer': (serverId, id) => ctx.showComputerOf(serverId, id),
  'hide-computer': (serverId, id) => ctx.hideComputerOf(serverId, id),
};

// What a panel row does: a clone or a Zotero link opens, pauses (detaches)
// or resumes (reattaches, what both sides did meanwhile merging), or syncs;
// a vault opens its directory or shows its file, or does one of
// VAULT_ACTIONS.
async function keepingAction(kind, serverId, id, action, value) {
  const item = keepingState.items.find((i) => i.kind === kind && i.server === serverId && i.id === id);
  if (!item) throw new Error('That no longer syncs with this computer');
  const link = `/api/zotero/links/${encodeURIComponent(id)}`;
  if ((kind === 'clone' || kind === 'vault') && ['open-server', 'use-https', 'trust-sync'].includes(action)) {
    await ctx.fixRow(item, action);
  } else if ((kind === 'clone' || kind === 'zotero') && action === 'open') {
    await ctx.openServer(serverId);
    await ctx.openGammaWorkspace(id);
    ctx.buildMenu();
  } else if (kind === 'clone' && (action === 'pause' || action === 'resume')) {
    const { origin, api } = await ctx.localServer(serverId);
    await api(origin, `/api/mirrors/${encodeURIComponent(id)}/${action === 'pause' ? 'detach' : 'relink'}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    ctx.showNotice(action === 'pause' ? `${item.name} is paused: nothing is pulled or pushed until you resume it.`
      : `${item.name} follows its origin again; what both sides did meanwhile merges.`);
  } else if (kind === 'clone' && action === 'sync') {
    const { origin, api } = await ctx.localServer(serverId);
    await api(origin, `/api/mirrors/${encodeURIComponent(id)}/sync`, { method: 'POST' });
    ctx.showNotice(`Syncing ${item.name}…`);
  } else if (kind === 'zotero' && (action === 'pause' || action === 'resume')) {
    const { origin, api } = await ctx.localServer(serverId);
    await api(origin, `${link}/${action === 'pause' ? 'detach' : 'relink'}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    // a resumed link starts its server at launch again (a pause is settled by the reading below)
    if (action === 'resume') registry.setZoteroHost(serverId, true);
    ctx.showNotice(action === 'pause' ? `${item.name} is paused: nothing goes to or comes from Zotero until you resume it.`
      : `${item.name} syncs with Zotero again; what both sides did meanwhile merges.`);
  } else if (kind === 'zotero' && action === 'sync') {
    const { origin, api } = await ctx.localServer(serverId);
    await api(origin, `${link}/sync`, { method: 'POST' });
    ctx.showNotice(`Syncing ${item.name} with Zotero…`);
  } else if (kind === 'vault' && action === 'open') {
    if (!item.dest) throw new Error('This vault is another account\'s: where its files are is that account\'s to see.');
    if (item.scope === 'file') ctx.revealFile(item.dest);
    else ctx.openDirectory(item.dest);
  } else if (kind === 'vault' && Object.prototype.hasOwnProperty.call(VAULT_ACTIONS, action)) {
    return VAULT_ACTIONS[action](serverId, id, value);
  } else {
    throw new Error(`Unknown action: ${kind} ${action}`);
  }
  refreshKeeping().catch(() => {});
  return true;
}

function init(shell) {
  ctx = shell;
}

module.exports = {
  init,
  refreshKeeping,
  get keepingState() { return keepingState; },
  get asksWanted() { return asksWanted; },
  scheduleKeeping,
  keepingAction,
};
