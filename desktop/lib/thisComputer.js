// A vault of another server shows this computer there: the vault's maker
// sees on that server, in any window, that the vault is kept here, whether
// this computer is online, and which assistants are here, never a path
// (docs/dev/computers.md). The host keeps one computer link per server,
// workspace and account, with a token of its own minted there, of scope
// `computer`, which opens only the channel the host announces itself over:
// never the vault's token. The shell mints it with the window's session,
// signed into that server, as it mints a vault's, and hands it to the host
// with its word (POST /api/computer-links), which checks it with one
// announce. The token the host's answer says it replaced, and the one a
// link held when it goes, are revoked on that server by the shell.
//
// Beside the links: the agents of this computer (setAgent and the others)
// and the servers whose turns they answer (addLinkedServer and the
// others); a vault removed (removeVault, forgetDetached), its server's last
// one taking this computer off it; what a page asks of its file on this
// computer (revealOnDisk, relocateFile, copyFileFromPage) and of its
// agent's terminal (terminalFromPage); and the focus hint (hintServers).

const { session } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const registry = require('./registry');
const sidecar = require('./sidecar');
const keeping = require('./keeping');
const terminal = require('./terminal');

// The shell's shared names, set by init (main.js "the sections").
let ctx = null;

// This computer's name on those servers: its host name, as the host's
// channel announces it.
const COMPUTER_NAME = String(os.hostname() || '').trim().slice(0, 64) || 'Gamma desktop';

// The host's computer links for the account `host.api` is signed in as.
async function computerLinks(host) {
  return (await host.api(host.origin, '/api/computer-links')).links || [];
}

// The host's computer link of server `remoteOrigin` and workspace `wsId`
// for the account `host.api` is signed in as, or null.
async function computerLinkOf(host, remoteOrigin, wsId) {
  return (await computerLinks(host)).find((c) => registry.originOf(c.remote_url) === remoteOrigin && c.workspace_id === wsId) || null;
}

// A server as the bar names it: the launcher's name, else its host name.
function serverNameOf(url) {
  return (ctx.remoteAt(url) || {}).name || ctx.hostOnly(url);
}

// Show this computer on `remoteOrigin` (`remoteName`, as the bar names it)
// for workspace `wsId`: a computer token minted there through `remote`
// (Gamma's API in a session signed into that server) and handed to the host
// (`host`: `{ origin, api }`, signed into it). Unless `always` (the panel's
// Show this computer there), a host that already shows this computer there
// for the account signed in there keeps its link, but one that server
// refused is handed a new token. Never throws, so a vault just made stays
// made: → null when shown, else the failure as the bar says it, a token
// minted for a link the host did not make revoked.
async function showComputer({ remote, remoteOrigin, remoteName, wsId, host, always = false }) {
  let token = null;
  try {
    const had = always ? null : await computerLinkOf(host, remoteOrigin, wsId);
    if (had) {
      // GET /api/session names the account signed in there (`user`, its username).
      const who = await remote(remoteOrigin, '/api/session').catch(() => null);
      const signedIn = who && typeof who.user === 'string' ? who.user : '';
      if (had.username && signedIn && signedIn !== had.username) return keeping.computerOtherAccount(remoteName, COMPUTER_NAME, had.username);
      if ((had.status || {}).state !== 'refused') return null;
    }
    await ctx.beforeMint(host.api, host.origin, remoteOrigin, { workspace: wsId });
    token = await remote(remoteOrigin, '/api/integrations/tokens', {
      method: 'POST',
      headers: { ...ctx.JSON_TYPE, 'X-Gamma-Workspace': wsId },
      body: JSON.stringify({ name: `Gamma desktop: ${COMPUTER_NAME}`, scope: 'computer', expires_in_days: 365 }),
    }).catch((e) => { throw Object.assign(e, { minting: true }); });
    const made = await host.api(host.origin, '/api/computer-links', {
      method: 'POST',
      headers: { ...ctx.JSON_TYPE, ...ctx.shellWord(host.origin) },
      body: JSON.stringify({ remote_url: remoteOrigin, workspace: wsId, token: token.token, token_id: token.id }),
    });
    const minted = token.id;
    token = null; // the host holds it now
    const replaced = made && made.replaced && made.replaced.token_id;
    if (replaced && replaced !== minted) await ctx.revokeToken(remote, remoteOrigin, wsId, replaced);
    return null;
  } catch (e) {
    if (token) await ctx.revokeToken(remote, remoteOrigin, wsId, token.id); // minted for a link that was never made
    return keeping.computerFailed(remoteName, e);
  }
}

// Stop showing this computer on `remoteOrigin` for workspace `wsId`: the
// host forgets its computer link there, which stops its channel, and the
// token the link held is revoked on that server through `remote`. → null
// when the host had none, else `{ revoked }`: false when that server did not
// revoke it (unreachable, signed out, another account signed in there), the
// token then left for the person to remove in that server's Settings.
async function hideComputer(host, remote, remoteOrigin, wsId) {
  const had = await computerLinkOf(host, remoteOrigin, wsId);
  return had ? dropComputerLink(host, remote, had) : null;
}

// The host's computer link `link` forgotten (its channel stops, and the turns
// its vaults run for that server), and its token revoked there through
// `remote`, which fails there what the token's runs held. → `{ revoked }`, as
// hideComputer's.
async function dropComputerLink(host, remote, link) {
  const gone = await host.api(host.origin, `/api/computer-links/${encodeURIComponent(link.id)}`, {
    method: 'DELETE', headers: ctx.shellWord(host.origin),
  });
  return { revoked: !(gone && gone.token_id) || await ctx.revokedToken(remote, registry.originOf(link.remote_url), link.workspace_id, gone.token_id) };
}

// The panel's Show this computer there, on the server and workspace of the
// vault of another server `linkId` of the host `serverId`: minted with the
// window's session, signed into that server, as changePush mints.
async function showComputerOf(serverId, linkId) {
  const { origin, api, link, item } = await ctx.vaultOn(serverId, linkId);
  if (!link.remote_url) throw new Error('Only a vault of another server shows this computer there.');
  ctx.busy = `Showing this computer on ${item.remote}…`;
  ctx.pushState();
  try {
    const failed = await showComputer({ remote: ctx.serverApi(session.defaultSession), remoteOrigin: ctx.remoteOf(registry.originOf(link.remote_url)),
      remoteName: item.remote, wsId: link.workspace_id, host: { origin, api }, always: true });
    ctx.showNotice(failed || keeping.computerSaid(item.remote, COMPUTER_NAME, true));
    return !failed;
  } finally {
    ctx.busy = null;
    ctx.pushState();
    ctx.refreshKeeping().catch(() => {});
  }
}

// The panel's Stop showing, on the server and workspace of the vault of
// another server `linkId` of the host `serverId`; its vaults stay as they are.
async function hideComputerOf(serverId, linkId) {
  const { origin, api, link, item } = await ctx.vaultOn(serverId, linkId);
  if (!link.remote_url) throw new Error('Only a vault of another server shows this computer there.');
  const had = await hideComputer({ origin, api }, ctx.serverApi(session.defaultSession), registry.originOf(link.remote_url), link.workspace_id);
  if (had) ctx.showNotice(had.revoked ? keeping.computerSaid(item.remote, COMPUTER_NAME, false) : keeping.computerStranded(item.remote, COMPUTER_NAME));
  ctx.refreshKeeping().catch(() => {});
  return !!had;
}

// The agents of this computer (docs/dev/local_agents.md "Who turns it on"),
// on local server `serverId`: Settings → AI asks through the page
// (setAgentFromPage and the others below). The shell's own session acts, so
// its account owns an agent it turns on. The server's listing first
// (`GET /api/local-agents`): `agents`, `[{name, enabled, runs, …}]`, and
// `dirs`, the directories offered.
async function agentsOf(serverId) {
  const at = await ctx.localServer(serverId);
  const listed = await at.api(at.origin, '/api/local-agents');
  return { ...at, agents: listed.agents || [], dirs: listed.dirs || [] };
}

// The agent `name` of that listing, null for one the server did not list.
async function agentOn(serverId, name) {
  if (!Object.prototype.hasOwnProperty.call(keeping.AGENT_LABELS, name)) throw new Error(`An agent is codex or claude, not ${name}`);
  const at = await agentsOf(serverId);
  return { ...at, found: at.agents.find((a) => a.name === name) || null };
}

// `name` ('codex' or 'claude') turned on (`on`) or off for this computer,
// where it works for the account in its chats here (in a synced folder's
// directory, one offered to it or one of its own), and for a server this
// computer is shown on. Turning one on is the
// computer's decision: asked first (`confirmed` answers in tests, where the
// question takes Cancel), and the PATCH carries the shell's word, which a
// page of Gamma cannot. The server checks the agent meanwhile ("Checking
// Codex…") and refuses one that is not ready with why. Turning it off asks
// nothing and carries the word too, so the person here turns off an agent
// whose owner is signed out of the app or gone. → false when it was so
// already.
async function setAgent(serverId, name, on, confirmed) {
  const { origin, api, found } = await agentOn(serverId, name);
  if (found && Boolean(found.enabled) === Boolean(on)) return false;
  const patch = (extra) => api(origin, `/api/local-agents/${name}`, {
    method: 'PATCH', headers: { ...ctx.JSON_TYPE, ...extra }, body: JSON.stringify({ enabled: Boolean(on) }),
  });
  if (!on) {
    await patch(ctx.shellWord(origin));
    ctx.showNotice(keeping.agentSaid(name, false));
    ctx.refreshKeeping().catch(() => {});
    return true;
  }
  if (!confirmed) {
    const q = keeping.agentAsk(name);
    if (await ctx.ask({ type: 'question', message: q.message, detail: q.detail, buttons: q.buttons, defaultId: 0, cancelId: 1 }, 1) !== 0) return null;
  }
  ctx.busy = `Checking ${keeping.AGENT_LABELS[name]}…`;
  ctx.pushState();
  try {
    await patch(ctx.shellWord(origin));
    ctx.showNotice(keeping.agentSaid(name, true));
    return true;
  } finally {
    ctx.busy = null;
    ctx.pushState();
    ctx.refreshKeeping().catch(() => {});
  }
}

// What an agent of this computer may run beyond its confinement: 'ask', it
// may ask (Codex to go past its sandbox, Claude Code to run a command): in
// the account's chats of the host, wherever the agent works, and, for a turn
// started on another server this computer is shown on, in the bar of this
// window alone (lib/agentAskBar.js); or 'never'.
// Letting it ask is the computer's decision, as turning it on is: asked
// first, with who writes what it is asked on another server, Cancel the
// default (`confirmed` answers in tests), and the PATCH carries the shell's
// word. Back to 'never' asks nothing. → false when it was so already.
async function setAgentRuns(serverId, name, runs, confirmed) {
  if (!['never', 'ask'].includes(runs)) throw new Error(`What an agent may run is never or ask, not ${runs}`);
  const { origin, api, found } = await agentOn(serverId, name);
  if (!found || !found.enabled) throw new Error(`Turn ${keeping.AGENT_LABELS[name]} on first.`);
  if (runs === found.runs) return false;
  if (runs === 'ask' && !confirmed) {
    const q = keeping.runsAsk(name);
    if (await ctx.ask({ type: 'warning', message: q.message, detail: q.detail, buttons: q.buttons, defaultId: 1, cancelId: 1 }, 1) !== 0) return null;
  }
  await api(origin, `/api/local-agents/${name}`, {
    method: 'PATCH', headers: { ...ctx.JSON_TYPE, ...(runs === 'ask' ? ctx.shellWord(origin) : {}) }, body: JSON.stringify({ runs }),
  });
  ctx.showNotice(keeping.runsSaid(name, runs));
  ctx.refreshKeeping().catch(() => {});
  return true;
}

// A directory offered to the agents beyond the synced folders, picked with
// the native dialog: a new one (`dirId` 'new'), or the one `dirId` names
// moved to the directory picked. Only the shell names a path
// (`PUT /api/local-agents/dirs` with its word); the page leaves one out or
// renames it itself. → the directories offered, null when nothing was
// picked.
async function agentDirs(serverId, dirId) {
  const { origin, api, dirs } = await agentsOf(serverId);
  const was = dirs.find((d) => d.id === dirId);
  if (dirId !== 'new' && !was) throw new Error('That directory is no longer offered to the agents.');
  const picked = await ctx.pickDirectory(was ? `Move “${was.name}”` : 'Offer a directory to the agents',
    'Choose a directory on this computer. Codex and Claude Code can then work in it from a chat whose page is in no synced folder.',
    was ? path.dirname(was.path) : undefined);
  if (!picked) return null;
  const entries = was ? dirs.map((d) => (d.id === dirId ? { id: d.id, path: picked, name: d.name } : d))
    : [...dirs, { path: picked, name: '' }];
  const after = await api(origin, '/api/local-agents/dirs', {
    method: 'PUT', headers: { ...ctx.JSON_TYPE, ...ctx.shellWord(origin) },
    body: JSON.stringify(entries.map(({ id, path: at, name }) => ({ ...(id ? { id } : {}), path: at, name }))),
  });
  ctx.showNotice(keeping.dirSaid(picked, Boolean(was)));
  return after.dirs;
}

// A person who may use agent `name` of this computer from now on (a
// co-author: decision 13 of docs/research/agents-as-participants-plan.md),
// the account `account` of the workspace `placeId` (of this server, or of
// one this computer is shown on). The shell reads whom the owner may name
// itself (`GET /api/local-agents/people`), so the question names the person
// and the workspace as the server says them, never as the page does; it
// asks, Cancel the default (`confirmed` answers in tests), with the one
// sentence of consent (keeping.personAsk: the owner answers for the
// account's terms), and the POST carries the shell's word. Taking a person
// off asks nothing: the page does it with its session. → false when they
// were named already, null when cancelled.
async function agentPerson(serverId, name, placeId, account, confirmed) {
  const { origin, api, found } = await agentOn(serverId, name);
  if (!found || !found.enabled || !found.mine) throw new Error(`Turn ${keeping.AGENT_LABELS[name]} on first.`);
  const listed = await api(origin, '/api/local-agents/people');
  const place = (listed.places || []).find((p) => p.workspace_id === placeId && (p.people || []).some((x) => x.id === account));
  if (!place) throw new Error('That person does not edit a workspace you share with them.');
  const person = place.people.find((x) => x.id === account);
  if ((found.people || []).some((p) => p.server === place.server && p.workspace_id === placeId && p.account === account)) return false;
  if (!confirmed) {
    const q = keeping.personAsk(name, person.username, keeping.placeName(place));
    if (await ctx.ask({ type: 'question', message: q.message, detail: q.detail, buttons: q.buttons, defaultId: 1, cancelId: 1 }, 1) !== 0) return null;
  }
  await api(origin, `/api/local-agents/${name}/people`, {
    method: 'POST', headers: { ...ctx.JSON_TYPE, ...ctx.shellWord(origin) },
    body: JSON.stringify({ server: place.server, workspace_id: placeId, account }),
  });
  ctx.showNotice(keeping.personSaid(name, person.username));
  return true;
}

// Settings → AI's asks about the agents of this computer (preload.js
// PAGE_ASKS `set-agent`, `set-agent-runs`, `agent-dirs`, `agent-person`, and
// below, `add-server`, `remove-server`, `link-asks`):
// only on a page of a server on this computer, the shell then asking its
// own question.
function pageAgents() {
  if (ctx.current.type !== 'local') throw new Error('The agents of this computer are set from a server on this computer.');
  return ctx.current.id;
}
const setAgentFromPage = async (wsId, name, on) => setAgent(pageAgents(), name, on);
const agentRunsFromPage = async (wsId, name, asks) => setAgentRuns(pageAgents(), name, asks ? 'ask' : 'never');
const agentDirsFromPage = async (wsId, dirId) => agentDirs(pageAgents(), dirId);
const agentPersonFromPage = async (wsId, name, _flag, placeId, account) => agentPerson(pageAgents(), name, placeId, account);

// Settings → AI → Agents' Servers this computer answers, on local server
// `serverId` (step 4.1 of docs/research/agents-as-participants-plan.md): a
// link to a server needs no vault of it. The shell's own session reads and
// changes the host's links, as agentsOf does; the window's session, signed
// into that server, mints and revokes its token there.

// Add a server… (`linkId` 'new'): the shell asks which of the launcher's
// remote servers this computer answers (keeping.serverAsk), with the account
// the window is signed in as there (signed out, it offers to open that
// server to sign in) and, where the account has several, for which
// workspace; then showComputer mints the computer token there and hands it
// to the host. A link's id in place of 'new' hands that link's server a new
// token (one it refused: revoked or expired there). The host then starts at
// launch, as for a vault of another server. → true when shown, false when it
// failed (said in the bar), null when nothing was asked of that server.
async function addLinkedServer(serverId, linkId) {
  const host = await ctx.localServer(serverId);
  const remote = ctx.serverApi(session.defaultSession);
  let srvName;
  let origin;
  let wsId;
  if (linkId !== 'new') {
    const link = (await computerLinks(host)).find((l) => l.id === linkId);
    if (!link) throw new Error('This computer no longer answers that server.');
    origin = ctx.remoteOf(registry.originOf(link.remote_url));
    srvName = serverNameOf(origin);
    wsId = link.workspace_id;
  } else {
    const servers = registry.load().servers.filter((s) => s.type === 'remote');
    if (!servers.length) throw new Error('Add the other server in the launcher first: this computer answers the servers Gamma knows.');
    const q = keeping.serverAsk(servers, COMPUTER_NAME);
    const r = await ctx.ask({ type: 'question', message: q.message, detail: q.detail, buttons: q.buttons, defaultId: 0, cancelId: servers.length }, 0);
    if (!(r >= 0 && r < servers.length)) return null;
    const srv = servers[r];
    srvName = srv.name;
    origin = ctx.remoteOf(registry.originOf(srv.url));
    const info = await remote(origin, '/api/session').catch(() => null);
    if (!info || !info.user) {
      const s = keeping.serverSignIn(srvName);
      if (await ctx.ask({ type: 'question', message: s.message, detail: s.detail, buttons: s.buttons, defaultId: 0, cancelId: 1 }, 1) === 0) {
        await ctx.openServer(srv.id);
        ctx.buildMenu();
      }
      return null;
    }
    const all = (info.workspaces || []).filter((w) => !w.mirror_of);
    const places = [...all.filter((w) => w.personal), ...all.filter((w) => !w.personal)];
    if (!places.length) throw new Error(`Your account on ${srvName} has no workspace.`);
    let place = places[0];
    if (places.length > 1) {
      const p = keeping.serverPlaceAsk(srvName, places);
      const r2 = await ctx.ask({ type: 'question', message: p.message, detail: p.detail, buttons: p.buttons, defaultId: 0, cancelId: places.length }, 0);
      if (!(r2 >= 0 && r2 < places.length)) return null;
      place = places[r2];
    }
    wsId = place.id;
  }
  ctx.busy = `Showing this computer on ${srvName}…`;
  ctx.pushState();
  try {
    const failed = await showComputer({ remote, remoteOrigin: origin, remoteName: srvName, wsId, host, always: linkId !== 'new' });
    if (!failed) registry.setSettings({ folderHost: serverId }); // its channel runs while the host does: at launch too
    ctx.showNotice(failed || keeping.computerSaid(srvName, COMPUTER_NAME, true));
    return !failed;
  } finally {
    ctx.busy = null;
    ctx.pushState();
    ctx.refreshKeeping().catch(() => {});
  }
}

// Remove: the host forgets the link `linkId` and its token is revoked on
// that server (dropComputerLink); the bar says when that server did not
// revoke it. Asks nothing: it takes nothing more from this computer.
async function removeLinkedServer(serverId, linkId) {
  const host = await ctx.localServer(serverId);
  const link = (await computerLinks(host)).find((l) => l.id === linkId);
  if (!link) return false;
  const name = serverNameOf(link.remote_url);
  const { revoked } = await dropComputerLink(host, ctx.serverApi(session.defaultSession), link);
  await ctx.releaseHost(host.api, host.origin, serverId);
  ctx.showNotice(revoked ? keeping.computerSaid(name, COMPUTER_NAME, false) : keeping.computerStranded(name, COMPUTER_NAME));
  ctx.refreshKeeping().catch(() => {});
  return true;
}

// Whether the server of link `linkId` may answer what this computer's agents
// ask (`on`: decision 7 of docs/research/agents-as-participants-plan.md).
// Letting it is the computer's decision: asked first, naming the server
// (keeping.linkAsksAsk, Cancel the default; `confirmed` answers in tests),
// and the PATCH carries the shell's word. Off asks nothing. → false when it
// was so already, null when cancelled.
async function setLinkAsks(serverId, linkId, on, confirmed) {
  const host = await ctx.localServer(serverId);
  const link = (await computerLinks(host)).find((l) => l.id === linkId);
  if (!link) throw new Error('This computer no longer answers that server.');
  if (Boolean(link.asks) === Boolean(on)) return false;
  const name = serverNameOf(link.remote_url);
  if (on && !confirmed) {
    const q = keeping.linkAsksAsk(name);
    if (await ctx.ask({ type: 'warning', message: q.message, detail: q.detail, buttons: q.buttons, defaultId: 1, cancelId: 1 }, 1) !== 0) return null;
  }
  await host.api(host.origin, `/api/computer-links/${encodeURIComponent(link.id)}`, {
    method: 'PATCH', headers: { ...ctx.JSON_TYPE, ...(on ? ctx.shellWord(host.origin) : {}) }, body: JSON.stringify({ asks: Boolean(on) }),
  });
  ctx.showNotice(keeping.linkAsksSaid(name, Boolean(on)));
  return true;
}

const addServerFromPage = async (wsId, linkId) => addLinkedServer(pageAgents(), linkId);
const removeServerFromPage = async (wsId, linkId) => removeLinkedServer(pageAgents(), linkId);
const linkAsksFromPage = async (wsId, linkId, asks) => setLinkAsks(pageAgents(), linkId, asks);

// Remove the vault…, a work folder's Detach…: its directory stays as it is
// but for Gamma's own .gamma folder, and the token the shell minted on a
// remote for it is revoked there. Asked first with GET .../removal's counts
// (keeping.removalAsk, the web app's Detach dialog in its words), unless
// `pages` says: a work folder's files `keep` in Gamma as pages, every one's
// bytes stored first, or those not in the library `remove` to Recently
// deleted; any other vault's pages stay as they are (the PDFs it read where
// they are stored in Gamma first). A file's vault never touches its file. A
// work folder of another server is let go of there, as its Detach there
// does; while that server cannot be reached the host refuses (409), and
// asked (keeping.farRemovalAsk; Cancel under the harness unless a test
// queued the answer) the vault is forgotten here all the same (`force=1`),
// the folder staying a synced folder there until it is detached there. GET
// .../removal saying that server could not be counted (`unreachable`) asks
// that at once, never the usual question with counts it could not read.
async function removeVault(serverId, linkId, pages) {
  const { origin, api, link, item, headers } = await ctx.vaultOn(serverId, linkId);
  const far = Boolean(link.remote_url && item.work);
  const unreachable = (e) => far && [409, 502, 503, 504].includes(e && e.status);
  const forced = async (reason) => {
    const q = keeping.farRemovalAsk(item, reason);
    return await ctx.ask({ type: 'warning', message: q.message, detail: q.detail, buttons: q.buttons, defaultId: 1, cancelId: 1 }, 1) === 0;
  };
  let force = false;
  if (!pages) {
    let removal = null;
    try {
      removal = await api(origin, `/api/folder-links/${link.id}/removal`, { headers });
    } catch (e) {
      if (!unreachable(e)) throw e;
      removal = { unreachable: true, reason: e.message };
    }
    if (far && removal.unreachable) {
      if (!await forced(removal.reason)) return false;
      force = true;
      pages = 'keep';
    } else {
      const q = keeping.removalAsk(item, removal, COMPUTER_NAME);
      const r = await ctx.ask({ type: 'question', message: q.message, detail: q.detail, buttons: q.buttons, defaultId: 0, cancelId: q.buttons.length - 1 }, 0);
      pages = q.choices[r];
      if (!pages) return false;
    }
  }
  ctx.busy = item.work ? `Stopping the sync of ${item.name}…` : `Removing the synced folder ${item.name}…`;
  ctx.pushState();
  try {
    const remove = () => api(origin, `/api/folder-links/${link.id}?pages=${pages === 'remove' ? 'remove' : 'keep'}${force ? '&force=1' : ''}`,
      { method: 'DELETE', headers });
    try {
      await remove();
    } catch (e) {
      if (force || !unreachable(e)) throw e;
      if (!await forced(e.message)) return false;
      force = true;
      await remove();
    }
    const stranded = link.remote_url ? await ctx.releaseRemote(origin, api, serverId, link, item.remote) : '';
    ctx.showNotice((item.scope === 'file' ? `${item.name} is no longer pushed from ${path.basename(item.dest)}; the file stays as it is.`
      : force ? `${item.name} is no longer synced with this computer; on ${item.remote} its folder stays a synced folder until it stops syncing there.`
      : item.offline || item.stopped ? `${item.name} is no longer synced; nothing in ${item.dest} was touched.`
      : item.work && pages === 'remove' ? `Stopped syncing ${item.name}: its files not in the library went to Recently deleted; ${item.dest} stays as it is.`
      : item.work ? `Stopped syncing ${item.name}: its files stay in Gamma as pages; ${item.dest} stays as it is.`
      : `${item.dest} is no longer synced; its files stay.`) + (stranded ? ` ${stranded}` : ''));
    return true;
  } finally {
    ctx.busy = null;
    ctx.pushState();
    ctx.refreshKeeping().catch(() => {});
  }
}

// A work folder of another server detached there (its Detach, which woke
// this computer's channel: docs/dev/computers.md): its vault here is
// forgotten as Detach… forgets one, its
// directory as it is but for Gamma's own .gamma folder, the token minted for
// it revoked there, the last vault of that server taking this computer off
// it, and the bar says so. Once per vault while it is being forgotten
// (`forgetting`); one that fails is tried again at the next reading.
const forgetting = new Set(); // vault ids
async function forgetDetached(item) {
  if (forgetting.has(item.id)) return;
  forgetting.add(item.id);
  try {
    const { origin, api, link, headers } = await ctx.vaultOn(item.server, item.id);
    await api(origin, `/api/folder-links/${link.id}?pages=keep`, { method: 'DELETE', headers });
    const stranded = await ctx.releaseRemote(origin, api, item.server, link, item.remote);
    ctx.showNotice(`${item.name} was detached on ${item.remote}: this computer no longer syncs it; ${item.dest} stays as it is.`
      + (stranded ? ` ${stranded}` : ''));
    ctx.refreshKeeping().catch(() => {});
  } catch {
    // the host not answering now: the next reading tries again
  } finally {
    forgetting.delete(item.id);
  }
}

// Where page `pageId` of the open server's workspace `wsId` is on this
// computer: `{ path, vault, reader, server }`. On a local server as it says
// (GET /api/folder-links/page/<id>: the vault that keeps the page, or the
// PDF it reads where it is); on a remote one, the host's vault of one file
// that pushes to it, read only while the host runs: a page's ask starts no
// server. null for a page with no file here.
async function pageOnDisk(wsId, pageId) {
  if (ctx.current.type === 'local') {
    const api = ctx.serverApi(ctx.content.webContents.session);
    const s = await api(ctx.openOrigin(), `/api/folder-links/page/${encodeURIComponent(pageId)}`, { headers: { 'X-Gamma-Workspace': wsId } });
    if (s.vault) return { path: s.vault.path, vault: s.vault, reader: null, server: ctx.current.id };
    if (s.reader) return { path: s.reader.path, vault: null, reader: s.reader, server: ctx.current.id };
    return null;
  }
  const host = registry.load().servers.find((s) => s.type === 'local');
  if (!host || !sidecar.status(host.id)) return null;
  const at = await ctx.localServer(host.id);
  const origin = ctx.openOrigin();
  const link = ((await at.api(at.origin, '/api/folder-links')).links || []).find((l) =>
    l.kind === 'file' && l.remote_url === origin && l.workspace_id === wsId && l.page_id === pageId);
  return link ? { path: link.dest, vault: { id: link.id, scope: 'file', name: link.title || '' }, reader: null, server: host.id } : null;
}

// A page's "Show in folder": its file in the system's file manager. Only a
// local server's page asks (its chip, vault/VaultChip.jsx): a remote's page
// has no chip, and a remote server opens no file manager on this computer
// (its ask is dropped); its vault of one file shows its file from the sync
// panel and from "Push from a file on this computer…".
async function revealOnDisk(wsId, pageId) {
  if (ctx.current.type !== 'local') return null;
  const found = await pageOnDisk(wsId, pageId);
  if (!found || !found.path) throw new Error('This page has no file on this computer yet.');
  ctx.revealFile(found.path);
  return found;
}

// A page's missing file located: a file read where it is takes the file the
// user picks, of the same extension (POST /api/folder-links/opened/<id>/locate,
// which refuses one of another format), and the page of a vault has its
// vault located (locateVault).
async function relocateFile(wsId, pageId) {
  const shown = ctx.current; // the server it is asked of, whatever the window opens while the picker is up
  const found = await pageOnDisk(wsId, pageId);
  if (!found) throw new Error('This page reads no file on this computer.');
  if (found.vault) return ctx.locateVault(found.server, found.vault.id);
  const ext = path.extname(found.path || '').toLowerCase();
  const picked = await ctx.pickFile(`Where is ${path.basename(found.path) || 'the file'} now?`,
    ext === '.pdf' ? 'Choose the PDF this page reads.' : `Choose the ${ext ? `${ext} ` : ''}file this page reads.`, ext);
  if (!picked) return null;
  ctx.stillOn(shown);
  const local = registry.get(shown.id);
  await ctx.reachFolder(local, picked);
  ctx.stillOn(shown);
  // its origin now: reachFolder may have restarted the server on another port
  const at = ctx.localOrigin(local);
  const page = await ctx.serverApi(ctx.content.webContents.session)(at, `/api/folder-links/opened/${encodeURIComponent(pageId)}/locate`, {
    method: 'POST', headers: { 'X-Gamma-Workspace': wsId, ...ctx.JSON_TYPE, ...ctx.shellWord(at) }, body: JSON.stringify({ path: picked }),
  });
  ctx.showNotice(`“${page.content || 'The page'}” reads ${path.basename(picked)} from now on.`);
  return page;
}

// A page's Copy PDF (Copy file for another format, the library's page
// menu): the file the page shows on the clipboard, for a paste into a folder
// or another app. It is fetched from the open server with the window's
// session, as the page's viewer reads it: the page's `properties.doc`
// (GET /api/blocks/<id>) names it, served at /api/uploads/<hash><ext>,
// a file read where it is included. It goes into a directory of its own
// under the system's temporary one, named after the page; the one copied
// before goes, since the clipboard holds one copy at a time. A page whose
// document has no bytes on the server (a PDF read from its address, a work
// folder's file too large to keep) is told so.
let copyDir = '';
async function copyFileFromPage(wsId, pageId) {
  const shown = ctx.current;
  const origin = ctx.openOrigin();
  const ses = ctx.content.webContents.session;
  const headers = { 'X-Gamma-Workspace': wsId };
  const page = await ctx.serverApi(ses)(origin, `/api/blocks/${encodeURIComponent(pageId)}`, { headers });
  const doc = (page.properties && page.properties.doc) || {};
  if (!/^[A-Za-z0-9_.-]{1,128}$/.test(doc.hash || '') || !/^\.[a-z0-9]{1,12}$/.test(doc.ext || '')) {
    throw new Error('This page has no file to copy.');
  }
  const r = await ses.fetch(`${origin}/api/uploads/${doc.hash}${doc.ext}`, { credentials: 'include', cache: 'no-store', redirect: 'error', headers });
  if (!r.ok) throw new Error(r.status === 404 ? 'The server does not keep this page\'s file.' : `The file could not be read: HTTP ${r.status}.`);
  const bytes = Buffer.from(await r.arrayBuffer());
  ctx.stillOn(shown);
  const stem = String(page.content || '').replace(/[\\/:*?"<>|\x00-\x1f]/g, '_').replace(/[. ]+$/, '').trim().slice(0, 120) || 'Gamma file';
  const dir = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'gamma-copy-'));
  const file = path.join(dir, stem + doc.ext);
  await fs.promises.writeFile(file, bytes);
  if (copyDir) fs.promises.rm(copyDir, { recursive: true, force: true }).catch(() => {});
  copyDir = dir;
  await ctx.copyFile(file);
  ctx.showNotice(`${path.basename(file)} is on the clipboard: paste it into a folder or another app.`);
  return file;
}

// A page's "Continue in a terminal" (its computer chat's strip): the agent's
// session of the conversation the page shows, resumed in a terminal in the
// work folder (lib/terminal.js). The page names only itself: what runs and
// where is the open server's answer (GET /api/local-agents/chat/terminal,
// the vault's maker's alone), asked through the shell's own session, as the
// sync panel asks. The terminal is the person's, with their own settings.
// The notice says what opened, or why not; under the test harness the
// terminal is only recorded (`terminals`).
const terminals = [];
async function terminalFromPage(wsId, pageId) {
  try {
    if (ctx.current.type !== 'local') throw new Error('A conversation goes on in a terminal only from a server on this computer.');
    const at = await ctx.localServer(ctx.current.id);
    const answer = await at.api(at.origin, `/api/local-agents/chat/terminal?bucket=${encodeURIComponent(pageId)}`, {
      headers: { 'X-Gamma-Workspace': wsId },
    });
    const run = terminal.terminalCommand(process.platform, answer.cwd, answer.argv, (name) => terminal.onPath(name));
    let there = false;
    try { there = fs.statSync(answer.cwd).isDirectory(); } catch {}
    if (!there) throw new Error(`${answer.cwd} is not on this computer any more.`);
    terminals.push(run);
    if (!process.env.GAMMA_SHELL_TEST) terminal.openTerminal(run);
    ctx.showNotice(`Opened ${answer.label || 'the agent'}’s conversation in a terminal.`);
    return run;
  } catch (e) {
    ctx.showNotice(String(e && e.message || e));
    return null;
  }
}

// A window got the focus: each running local server's vaults look at their
// disks now (POST /api/folder-links/hint: the server's tick, in the
// background), at most every HINT_MS. Best effort.
const HINT_MS = 10_000;
let hintedAt = 0;
function hintServers() {
  if (Date.now() - hintedAt < HINT_MS) return;
  hintedAt = Date.now();
  for (const srv of registry.load().servers) {
    if (srv.type !== 'local' || !sidecar.status(srv.id)) continue;
    ctx.localServer(srv.id).then(({ origin, api }) => api(origin, '/api/folder-links/hint', { method: 'POST' })).catch(() => {});
  }
}

function init(shell) {
  ctx = shell;
}

module.exports = {
  init,
  COMPUTER_NAME,
  computerLinks,
  showComputer,
  hideComputer,
  removeVault,
  showComputerOf,
  hideComputerOf,
  forgetDetached,
  setAgent,
  setAgentRuns,
  agentDirs,
  agentPerson,
  setAgentFromPage,
  agentRunsFromPage,
  agentDirsFromPage,
  agentPersonFromPage,
  addLinkedServer,
  removeLinkedServer,
  setLinkAsks,
  addServerFromPage,
  removeServerFromPage,
  linkAsksFromPage,
  revealOnDisk,
  relocateFile,
  copyFileFromPage,
  terminals,
  terminalFromPage,
  hintServers,
};
