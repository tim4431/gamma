# A computer and the servers its vaults name

The desktop app's own server keeps synced vaults, some of them of another
Gamma server (a NAS, a hosted one: [synced_vaults.md](synced_vaults.md)).
That other server used to see only the files a vault pushed. Now the
computer announces itself to it over a channel of its own, and the server
shows the vault's maker, in any window (a browser, an iPad, the desktop
window on that server), that the folder or file is kept on *TIM-PC*,
whether that computer is online or since when it is not, and which
assistants are there (Codex, Claude Code). Nothing runs on the computer
from that server yet.

This is step 4a of [agent-collab.md](../research/agent-collab.md) ("How
the server sees a vault"). The later steps build on the channel: 4b work
folders of another server, 4c their Sends and mentions run on the
computer, 4d the chat's computer side relayed. The words below call the
desktop app's own server *the host* (as [synced_vaults.md](synced_vaults.md)
does) and the vault's server *the server*.

| Side | Code |
|---|---|
| the server (every Gamma server) | `gamma/computers/registry.py`, `gamma/routers/computers.py`, the `computer` scope in `gamma/integrations.py` and `gamma/auth.py` |
| the host (the desktop app's own server) | `gamma/computers/channel.py`, `gamma/routers/computer_links.py`, a check in `folder_links.tick` |
| the desktop shell | `desktop/main.js` (`showComputer`, the panel's actions, `releaseRemote`), `desktop/lib/keeping.js` (the panel's words) |
| the client | `frontend/src/vault/usePageComputer.js`, `vaultState.js` (`readComputers`, `computerChip`), `VaultChip.jsx`, the `{t: "computer"}` case in `collaboration/collabSession.js`, Settings' connections |

## The credential: a token of the `computer` scope

The computer speaks to the server with a token of its own, never the
vault's sync token. A bearer is identity: a sync token copied into a
script or a mirror's config must not be able to pose as the computer (and,
from 4c on, receive the maker's Sends and chats), and a computer's token
must not be able to read or write the library.

- **The scope.** `integrations.SCOPES` has a fourth, `computer`. Such a
  token is for exactly one workspace, as a write token is (`grant`:
  "A computer's token is for one workspace."), and its workspaces cannot
  be changed later. It is minted like any token, only with a session
  (`POST /api/integrations/tokens {name, scope: "computer",
  expires_in_days}`, the workspace open; a viewer may mint one, since it
  only shows where the account's vaults are kept).
- **What it opens: its channel and nothing else.** `auth.session_middleware`
  answers a `computer` token 403 "This token is a computer's channel; it
  opens nothing else." on every path but those below `auth.CHANNEL_PREFIX`
  (`/api/computers/`: the announce, the poll, and the Sends of 4c), reads
  included;
  `integrations.resolve_connection` gives it no MCP connection. The
  channel's routes in turn refuse a session and every other token (403
  "Only a computer's token opens its channel.").
- **Revoked** from Settings like any connection (it reads *Computer:
  TIM-PC*, "Shows its synced vaults of <workspace>", with no workspace
  choice), by the sync panel's *Stop showing*, and when the last vault of
  that server and workspace is removed. A revoked or expired token's
  computer is forgotten: its row and presence go (`registry.forget`, from
  the revoke route, from a held poll whose token no longer resolves, and
  when the page route meets the row). A deleted workspace or account
  takes its rows and its presence with it (`integrations.forget_workspace`,
  `workspaces.delete_account_workspaces`, `registry.drop`). The revoke
  route answers `{ok, revoked}`, `revoked` whether the account had such a
  token, so the shell can say when one was left.

A server older than the scope refuses to mint it (an unknown scope is
422 there); the shell says the server is older than the app.

## The server

### What it keeps

- **One users.db `computers` row per token** (additive, no migration
  step): `token_id` (the key), `user_id`, `workspace_id`, `computer` (the
  computer's id for this server), `name`, `vaults` (JSON:
  `[{kind: "folder", folder_id, paused} | {kind: "file", page_id,
  paused}]`, only those this workspace has), `assistants` (JSON, below),
  `app` (the app's version), `seen_at`, `created_at`. It is what the
  computer last announced, and when it was last seen, so *offline since
  14:02* survives a restart. Never a path.
- **Whether it is online, in memory** (`registry._live`, one process like
  the page rooms): online while a poll is held, or for `GRACE_S` (45 s,
  `GAMMA_COMPUTER_GRACE_S`) after the last poll ended or the last
  announce. A poll's end arms a timer; a new poll disarms it; when it
  fires the computer is offline, `seen_at` is written and the account is
  told. While online, `seen_at` is written at most every 10 minutes.
- **The nudge** is `collab.publish_account(user_id, {t: "computer"})`:
  content-free, to that account's page sockets in any room, when the
  computer comes online, goes offline, announces something a window shows
  differently (its name, its vaults, its assistants) or is forgotten. A
  co-author never hears it.

### The routes

| Method | Path | |
|---|---|---|
| POST | `/api/computers/announce` | A `computer` token only. `{computer: {id, name}, app, vaults: [{kind: "folder", folder_id, paused} \| {kind: "file", page_id, paused}], assistants: [{name: "codex" \| "claude", label, found, ready, version, code}]}`: the id 1-64 of `[A-Za-z0-9_-]`, the name 1-64 printable characters, at most 500 vaults (deduplicated; one this workspace does not have is dropped), an unknown assistant dropped, an unknown `code` read as `not_ready`, every string capped at 64. At most 128 KiB (413). 200 `{account: {id, username}, computer: {id, name}, workspace: {id}, version, wait}`: `version` is what the computer's polls name, `wait` the longest hold. 400 for a body of another shape. A read-only hosted server lets it through (`hosted.READ_ONLY_ALLOWED`): it holds no data of the library. |
| GET | `/api/computers/poll?v=<version>&wait=<s>` | A `computer` token only, `async def`: a hold uses no worker thread, and every SQLite read is off the event loop. `{announce: true}` at once when this server does not know the computer (a restart) or `v` is not its last announce's version. Else held up to `min(wait, 25)` s (0 answers at once): `{}` when the hold ends, `{announce: true}` when woken for it, `{wake: [...]}` for later steps' work. After the hold the token is resolved again: revoked meanwhile, or its account gone from the workspace, it is 401 and the computer is forgotten. Not written as a slow request (`auth._finish_request_log`), quiet in uvicorn's access log (`logbuf._QuietPolls`). |
| GET | `/api/pages/{page_id}/computer` | The signed-in account's computers that keep the page: a session only (`require_personal_user_id`: a token or the guest 403), a share's reader 404. `{computers: [{id, name, online, since, vault: {kind, name, paused}, assistants}]}`: a `file` vault of that page, or a `folder` vault the page is filed in or below (`blocks_store.page_in_folder`; the library's root covers every page), the one that keeps it when several do (a vault of that file, else the deepest folder: the computer's own rule); `since` the time it went offline, null while online; `vault.name` the folder's name or the page's title as this server has it; online ones first, then the most recently seen. `[]` for none and for any other account. |

`assistants` are `[{name, label, found, ready, version, code}]`: `ready`
null while the computer is still checking, `code` one of `""` (ready),
`missing`, `signed_out`, `not_ready`, `checking`, worded by the client in
its language; never the computer's English problem.

### The Sends (step 4c)

A computer that announces a work folder with an agent (the vault's
`agent`) takes its maker's Sends there ([local_agents.md](local_agents.md)
"A work folder of another server"). The routes answer only the token's
own account's Sends, in the work folders that token announced with an
agent:

| Method | Path | |
|---|---|---|
| GET | `/api/computers/sends` | `{sends: [{id, folder_id, page_id?, at}], withdrawn: [{id, run}]}`: the Sends waiting (`sent`, `via: session`, by the token's account, in its agent folders but paused ones, oldest first, at most 50; none while the account cannot edit), and the runs of this computer whose request is no longer running (each listed once). |
| POST | `/api/computers/sends/{id}/claim` | `{run, agent}` (`run` 1-64 of `[A-Za-z0-9_-]`) → `{request, entries, seen, threads}`: `sent → running` in one locked write, signed with the agent's label; the same run again gets it back; 409 another run holds it, it is no longer sent, another agent is named, or the folder is paused; 404 not this computer's; 403 the account cannot edit. Each entry on a PDF has `place: {page, box, rects, doc_id}`; `seen` is this server's clock; `threads` `[{id, by_agent, at, comment_id, people: [{id, at}]}]` for the resume rule. |
| POST | `/api/computers/sends/{id}/progress` | `{run, detail}` → `{ok}`: the running detail, one line, 300 characters; 409 from another run or once it is no longer running. |
| GET | `/api/computers/threads/{id}/picture?at=now\|before` | the region picture of a thread of a request this computer's run holds; 404 otherwise. |
| POST | `/api/computers/sends/{id}/answers` | `{run, replies: [{id, thread_id, text, resolve}], state: done\|failed, summary, changed, error?}` → `{ok, written}`: the replies written by this server signed `local:<agent>` (a reply id already there is skipped; the same answers again write nothing), a thread resolved only when no person wrote in it after the run read it, then the end (`done`: the summary and the changed files named; `failed`: the error). 400 a thread the request does not name, or a changed path that is absolute or climbs; 409 another run or a withdrawn request. |

The claim's key, `seen` and the comments the run read are a pages.db
`feedback_request_runs` row (additive).

An agent's binding is kept only
for a folder that carries a work folder's mark itself and an account that
may edit there, and read per account: a member's computer serves only
that member's Sends. An agent that takes a request that is not running
ends the open runs of it (their next write refused), and a computer whose
token goes fails the Sends its runs hold. An older server's 401/403 on
`/sends` is asked again only after 180 s. The page route's `vault` gains
`agent: {name, label} | null`; the Feedback document's `agent`, for the
maker alone, comes from the binding (`{name, label, computer, online,
ready, problem, takes, why}`); a token's Send to such a folder is refused
(403, a token of the account whose computer keeps it), and while a
computer's run holds a request a token's state change is refused (409
"Codex on TIM-PC is answering this request."), the maker's withdraw
passing; the document's `problem` says why an online computer's agent is
not ready (*Codex is not signed in on TIM-PC.*).

The chat's computer side on such a page (step 4d,
[local_agents.md](local_agents.md) "The chat on another server's page")
has its own routes, the token's account's turns in its agent folders
alone:

| Method | Path | |
|---|---|---|
| GET | `/api/computers/chats` | `{turns: [{id, conversation_id, page_id, folder_id, at}], stops: [{id, run}]}`: the turns queued for its own agent of each folder (oldest first, at most 20, the folder not paused) and the runs of its turns asked to stop. |
| POST | `/api/computers/chats/{turn}/claim` | `{run, agent}` → `{turn: {id, conversation_id, page_id, text, selections, model, effort}, history, page: {path, title}, seen}`: queued → running in one locked write keyed by the run (the same run again: the same answer; another: 409; 404 not this computer's; 409 a paused folder, or a turn for another agent, left queued). |
| POST | `/api/computers/chats/{turn}/write` | `{run, seq, fields}` → `{ok}` or `{ok, stale: true}` when `seq` is not above the last written: the agent message's `text` (through `prompt.inert`), `steps` (a path not relative stored as `outside`), `changes` (a path not relative left out), `changed`, `built`, `error`, `fresh`, `state`, `model`, `effort`, its `ended_at` this server's clock; 409 another run, a turn no longer running, or a gone conversation. |

The announce's assistants carry their `models` for the chat's model
chip (at most 32, 8 efforts each); an announce is at most 128 KiB.

## The host

### Where the credential is kept

A users.db `computer_links` row per server, workspace there and local
account (additive): `id`, `remote_url` (as a vault of it keeps it, no
trailing slash), `workspace_id`, `created_by` (the local account), `token`
(sealed with `publisher_sessions.seal`, as a vault's), `token_id` (its id
there, for whoever revokes it), `account` and `username` (the account
there, as the announce named it), `created_at` and `status` `{state,
error, at}`:

| State | |
|---|---|
| `connecting` | made, not yet announced |
| `connected` | the announce succeeded, and the polls since |
| `refused` | 401 or 403: the token was revoked or expired there; tried again after 10 minutes |
| `older` | 404 or 405 on the channel's routes: a Gamma from before them; asked again after an hour |
| `unreachable` | anything else; tried again after 5 s, doubling to 5 minutes |

The status is written only when it changes.

### The routes (`config.folder_links_enabled()` only, else 404, as `/api/folder-links`)

| Method | Path | |
|---|---|---|
| GET | `/api/computer-links` | The account's channels (a session): `{links: [{id, remote_url, workspace_id, account, username, status, created_at}]}`, never the token. |
| POST | `/api/computer-links` | The shell's word and a session (`_from_shell`, as the folder-links **P** routes): `{remote_url, workspace, token, token_id}`, checked with one announce. 201 `{link, replaced: {token_id} \| null}`: one already kept for that server, workspace and account takes the new token, and `replaced` names the old one, the shell's to revoke there. 400 "That server refused this computer's token." or a token of another workspace, 409 "That server is older than this app: it cannot show this computer.", 502 unreachable. The channel starts at once. |
| DELETE | `/api/computer-links/{id}` | The shell's word, the link's maker (404 otherwise): the channel stops and is forgotten, `{ok: true, token_id}` for the shell to revoke there. |

### The channel

One daemon thread per row (`channel._Channel`), started when the row is
made, checked by `folder_links.tick` every 30 s (the missing ones start,
the ones whose row went stop; the lifespan has no room for another
timer) and stopped at shutdown. Its HTTP client is `gamma_sync.Server`
(bearer, urllib; `default_open` is the tests' seam), timeout the hold
plus 15 s. Each turn:

1. **Builds the state** (`state_of`): the computer's id for that server
   (the HMAC-SHA256 of the install's id, `cloud_auth.device()`, and the
   server's origin, 22 characters: two servers cannot match one computer
   by it), its name (the host name), the app's version, every vault of
   that server, workspace and local account (`folder_links.all_links()`:
   its kind, its folder or page id, whether it is paused), and the
   assistants as the catalog last found them (`catalog.known`: one probe
   when none was ever made, never one to refresh it, so a channel keeps no
   agent probed; `[]` where agents do not run here,
   `local_agents.enabled()`).
2. **Announces** when that differs from what it last announced, or the
   server asked, keeping the answer's `version` and account.
3. **Polls**, held up to 25 s; a poll answered at once (with nothing, or
   asking to announce again, as a server that always asks so would) waits
   1 s before the next. The status turns `connected` as the announce
   succeeds.

A vault added, paused, resumed or removed nudges the threads
(`channel.nudge`, from `folder_links`): they see it when the poll they
hold ends, at most 25 s later.

## The desktop shell

- **A vault of another server made** (`addFolderVault`, and a file
  vault's remote branch): right after it, `showComputer` mints a
  `computer` token there with the window's session and the workspace
  header (`{name: "Gamma desktop: <host name>", scope: "computer",
  expires_in_days: 365}`) and hands it to the host's `POST
  /api/computer-links` with the shell's word; a replaced token is revoked
  there. It is skipped when the host already has a link for that server
  and workspace whose account is the one signed in there, unless that
  link was refused. When the window is signed in there as another account
  than the link's, nothing is minted and the notice says so: a link is
  never handed to another account, and the host refuses that too
  (`channel.make` checks the token's account with an announce that names
  no vault). A failure revokes the minted token and is added to the
  vault's notice; the vault stays made.
- **The sync panel**, in the view of a vault of another server the
  account made, one line under its state: *Shown on NAS as TIM-PC ·
  connected* (or *connecting*, *refused this computer's token*, *older
  than this app*, *not reachable*, the error in the tooltip, the first two
  in red), with *Stop showing*; without a link, *Not shown on
  NAS* with *Show this computer there*, which mints and hands over as
  above. *Stop showing* deletes the host's link and revokes its token,
  and turns off the agent of each work folder of that server it
  announced (turned on again only with the shell's question);
  when that server did not revoke it (not reached, signed out, another
  account signed in there: the revoke route answers `revoked: false`),
  the notice says so and names the connection to remove in that server's
  Settings.
- **The last vault** of that server and workspace removed
  (`releaseRemote`): the host's link goes and its token is revoked there,
  the removal's notice saying so when it could not be.

## The client

- **`usePageComputer(pageId, enabled)`** reads `GET
  /api/pages/{id}/computer` for the open page in every window, signed in,
  not a guest, not in a share: never gated on `IS_DESKTOP`, and a 404 (a
  server older than the route) or an error is "none" for that read only,
  never for the window (unlike `useVault`'s `elsewhere`). It reads again
  on the socket's `{t: "computer"}` (kept for another page, as `inbox`
  is), on each (re)join and on the poll fallback's refetch, where
  `onVault` is called (`onComputer`), and when the chip's card opens.
- **The chip.** Where the host's own `vaultChip` has nothing to say (any
  server but the desktop app's own), `VaultChip` draws the computer's
  (`computerChip`): *Synced vault on TIM-PC* with a dot, filled while
  online and hollow while not (`data-computer="online" | "offline"`), and
  a card: *Connected* or *Offline since 2:02 PM* (*Nov 3* another day,
  with the year another year), *Folder: cavity* or *File: main.pdf*,
  *Paused on TIM-PC*, and *Assistants there: Codex, Claude Code (not
  signed in)* (*(checking)*, *(not ready)*; a missing one left out, no
  line when none). No action yet: *Show in folder* needs the window to
  know it is on that computer (later, with 4d).

## Trust

- **Only the maker.** The page route, the nudge and the chip are the
  token's account's alone; a co-author, a share, a token and the guest
  learn nothing of the computer.
- **The computer's token opens only its channel**, and the channel only
  the computer's token: no read of the library, no write, no MCP.
- **What the server learns** is the computer's id for it, its host name,
  the app's version, which of the workspace's folders and pages it keeps
  and whether each is paused, and which assistants are there with their
  versions. Never a path, never a problem's words.
- **What it widens: nothing.** The server can still only answer the
  host's own requests; nothing on the computer acts on what the channel
  says yet (an `{announce: true}` makes it announce again). 4c and 4d,
  which let the server start work there, decide their own rules.
- **Made and taken back by the shell's word.** A Gamma page never makes a
  channel: the shell mints the token with the window's session and hands
  it over with its word, as it does a vault's.

## Tests

`backend/tests/test_computers.py`: the scope (minted with a session only,
one workspace, a computer token refused on the library's routes and at
`/mcp`, a session and a write token refused on the channel), announce
(normalised, a foreign folder or page dropped, the answer's account, a
malformed body 400, 413), poll (unknown and another version asked to
announce, a hold of 0, a wake, offline after the grace with the nudge to
the account alone, a revoked token 401 and its row gone), the page route
(file and folder vaults, a subfolder's page, another member `[]`, a token
403, the guest refused, a share 404, offline since surviving a reset),
the host (the routes with and without the shell's word; the channel
against a second server through `gamma_sync.default_open`, announcing the
vaults with their assistants; refused, older and unreachable; stopping
when the link goes). `desktop/test/keeping.test.js` (the panel line per
state, its button) and `main.test.js` (mint and hand-over when a vault of
another server is made, a failure revoking, *Show this computer there*,
*Stop showing*, the last vault removed). `frontend/tests/vaultState.test.mjs`
(`readComputers`, `sinceText`, `computerChip`), `collabSession.test.mjs`
and `collabPoll.test.mjs` (the nudge for another page, on hello, rejoin
and poll). The browser group `computers` (`scenarios/computers.mjs`): two
servers, a NAS and a computer with the stand-in Codex; the computer's
vault of a NAS folder and its channel; the chip on the NAS for the maker,
none for another member; the computer stopped, the chip offline.
