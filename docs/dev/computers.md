# A computer and the servers its vaults name

The desktop app's own server keeps synced vaults, some of them of another
Gamma server (a NAS, a hosted one: [synced_vaults.md](synced_vaults.md)).
The computer announces itself to that server over a channel of its own,
and the server shows the vault's maker, in any window (a browser, an iPad, the desktop
window on that server), that the folder or file is kept on *TIM-PC*,
whether that computer is online or since when it is not, and which
assistants are there (Codex, Claude Code). The same channel then carries
work the other way: the turns of that computer's agent asked for from the
server ("The agents' turns there"), and sessions over the channel.

The design is step 4a of [agent-collab.md](../research/agent-collab.md)
("How the server sees a vault"). The channel also carries the work
folders of another server, the turns of the computer's agent in the chat
and those of the comments sent to it. The words below call the
desktop app's own server *the host* (as [synced_vaults.md](synced_vaults.md)
does) and the vault's server *the server*.

| Side | Code |
|---|---|
| the server (every Gamma server) | `gamma/computers/registry.py`, `gamma/routers/computers.py`, `gamma/local_agents/far_turns.py` (the turns a computer takes), the `computer` scope in `gamma/integrations.py` and `gamma/auth.py` |
| the host (the desktop app's own server) | `gamma/computers/channel.py`, `gamma/routers/computer_links.py`, a check in `folder_links.tick` |
| the desktop shell | `desktop/lib/thisComputer.js` (`showComputer`, Settings' `addLinkedServer`, `removeLinkedServer`, `setLinkAsks`), `desktop/lib/syncPanel.js` (the panel's actions), `desktop/lib/vaults.js` (`releaseRemote`), `desktop/lib/keeping.js` (the panel's words and Settings' questions) |
| the client | `frontend/src/vault/usePageComputer.js`, `vaultState.js` (`readComputers`, `computerChip`), `VaultChip.jsx`, the `{t: "computer"}` case in `collaboration/collabSession.js`, Settings' connections, `settings/SettingsAgents.jsx` (`AnsweredServers`), `settings/answeredServers.js` (its rows) |

## The credential: a token of the `computer` scope

The computer speaks to the server with a token of its own, never the
vault's sync token. A bearer is identity: a sync token copied into a
script or a mirror's config must not be able to pose as the computer (and,
from 4c on, receive the maker's turns), and a computer's token
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
  (`/api/computers/`: the announce, the poll, and the turns of 4d), reads
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
  `app` (the app's version), `seen_at`, `created_at`, `asks` (users.db
  step 67: whether the computer lets this server answer what its agents
  ask, its link's switch, "The servers this computer answers" below) and
  `dirs` (step 68: the directories the computer offers its agents there,
  `[{id, name}]`). It is what the computer last announced, and when it was
  last seen, so *offline since 14:02* survives a restart. Never a path.
- **Whether it is online, in memory** (`registry._live`, one process like
  the page rooms): online while a poll is held, or for `GRACE_S` (45 s,
  `GAMMA_COMPUTER_GRACE_S`) after the last poll ended or the last
  announce. A poll's end arms a timer; a new poll disarms it; when it
  fires the computer is offline, `seen_at` is written and the account is
  told. While online, `seen_at` is written at most every 10 minutes.
- **The nudge** is `collab.publish_account(user_id, {t: "computer"})`:
  content-free, to that account's page sockets in any room, when the
  computer comes online, goes offline, announces something a window shows
  differently (its name, its vaults, its assistants) or is forgotten; the
  people its agent serves, before and after such an announce, hear it too
  ("A co-author's agent"). Another member never hears it.

### The routes

| Method | Path | |
|---|---|---|
| POST | `/api/computers/announce` | A `computer` token only. `{computer: {id, name}, app, vaults: [{kind: "folder", folder_id, paused} \| {kind: "file", page_id, paused}], assistants: [{name: "codex" \| "claude", label, found, ready, version, code, models, enabled, runs, people}], asks, dirs: [{id, name}]}`: `asks` a flag (false when left out, 400 for another value), `dirs` the directories the computer offers its agents by id (1-64 of `[A-Za-z0-9_-]`, 400 for another, a path among them) and name (one line, at most 80 characters), at most 50, none kept while no assistant is on, the id 1-64 of `[A-Za-z0-9_-]`, the name 1-64 printable characters, at most 500 vaults (deduplicated; one this workspace does not have is dropped), an unknown assistant dropped, an unknown `code` read as `not_ready`, `enabled` a flag (400 for another value), `runs` anything but `ask` read as `never` (and `never` while it is off), `people` at most 50 account ids of this workspace (400 for another shape; none while it is off), every string capped at 64; this server's own computer id (`agents.here_id`) is refused (400 "The computer's id is this server's own."), so a far turn's token never writes as this computer's agent. A work folder's vault also carries its `agent`, which the app still sends for an older server; this server drops it, and reads it only from an announce whose assistants carry no `enabled` (an older app's), where the first agent a folder vault names is on, `runs` `never`. `sessions` a flag: the computer answers what this server asks of its agents' sessions ("Sessions over the channel"; false when left out, an app before that), kept in memory alone (`registry._live`, `answers`). `skills` a flag the same way: the computer runs a turn's skill with the instructions its claim carries (`registry.runs_skills`; a skill turn for a computer without it is refused at its start, 409 "Update Gamma on TIM-PC to run skills there.", [skills.md](skills.md#a-computers-agent)). At most 128 KiB (413). 200 `{account: {id, username}, computer: {id, name}, workspace: {id, name}, version, wait}`: `name` the workspace's, which the computer's Settings shows, `version` is what the computer's polls name, `wait` the longest hold. 400 for a body of another shape. A read-only hosted server lets it through (`hosted.READ_ONLY_ALLOWED`): it holds no data of the library. |
| GET | `/api/computers/poll?v=<version>&wait=<s>` | A `computer` token only, `async def`: a hold uses no worker thread, and every SQLite read is off the event loop. `{announce: true}` at once when this server does not know the computer (a restart) or `v` is not its last announce's version. Else held up to `min(wait, 25)` s (0 answers at once): `{}` when the hold ends, `{announce: true}` when woken for it, `{wake: [...]}` for later steps' work. After the hold the token is resolved again: revoked meanwhile, or its account gone from the workspace, it is 401 and the computer is forgotten. Not written as a slow request (`auth._finish_request_log`), quiet in uvicorn's access log (`logbuf._QuietPolls`). |
| GET | `/api/folders/computers` | The signed-in account's computers that keep a folder vault of the workspace, by folder (`registry.of_folders`): `{folders: {folder id: {name, online, since, paused}}}`, each folder's first computer (online ones first, then the most recently seen; the whole library's vault left out), which a synced folder's card, row and Files window row name with its state ([work_folders.md](work_folders.md) "File icons"). A session only, a share's reader 404; `{folders: {}}` for an account none of whose computers keeps one. Read with the listing. |
| GET | `/api/pages/{page_id}/computer` | The signed-in account's computers for the page, those that keep it and those with an agent on for it (whose agents it talks to on any page, "The agents' turns there"): a session only (`require_personal_user_id`: a token or the guest 403), a share's reader 404. `{computers: [{id, name, online, since, vault: {kind, name, paused, work} \| null, assistants, asks, dirs, lent_by?}]}` (`asks` as announced, `dirs` the directories it offers, `[{id, name}]`; a lent computer's false and `[]`): `vault` a `file` vault of that page, or a `folder` vault the page is filed in or below (`blocks_store.page_in_folder`; the library's root covers every page), the one that keeps it when several do (a vault of that file, else the deepest folder: the computer's own rule), null for a computer that keeps none of it; `work` whether that vault keeps a work folder of the workspace the account may edit (its agents work in that folder there); `since` the time it went offline, null while online; `vault.name` the folder's name or the page's title as this server has it. In the order its turns are given (`registry._ordered`): a computer that keeps the page's work folder first, the account's own before the lent ones, online ones first, then the most recently seen, a computer that keeps the page before one that keeps none. `[]` for none and for any other account. |
| GET | `/api/computers` | The signed-in account's computers shown to the workspace, for Settings → AI → Local agents (its sections, and whether the rail offers it at all: [settings.md](settings.md) "AI") and a chat with no page open (`registry.of_account`): `{computers: [{id, name, online, since, assistants, asks, dirs, lent_by?}]}`, as the page's route says them, without a vault, then another account's computers whose agent serves this one (`lent_by` the owner's username, "A co-author's agent"); the same session-only gate. `[]` for none. A `computer` token never opens it (`_computer` refuses it). |
| GET | `/api/computers/people` | A `computer` token only: `{people: [{id, username, role}]}`, the editors and owners of the token's workspace but its own account, whom the computer's owner may let use an agent there ("A co-author's agent"). |

`assistants` are `[{name, label, found, ready, version, code, models,
enabled, runs}]`: `ready` null while the computer is still checking,
`code` one of `""` (ready), `missing`, `signed_out`, `not_ready`,
`checking`, worded by the client in its language, never the computer's
English problem; `enabled` whether the agent is on there for the token's
account (on, and that account turned it on: another account's agent is
not this server's to use) and `runs` what it may run then (`never` or
`ask`), and `people` the account ids of this workspace its owner lets use
it. The agents are the computer's ([local_agents.md](local_agents.md)
"Who turns it on"): a vault names none.

### A co-author's agent

The computer's owner may name people of the token's workspace who use its
agent there (step 3.6 of [the plan](../research/agents-as-participants-plan.md),
[local_agents.md](local_agents.md) "A co-author's turn"); the announce
names them per assistant (`people`, kept in `computers.assistants`, no
migration). `registry._lent_rows` reads, for a person, the rows of other
accounts' computers of the workspace whose first agent on (`_agent`)
names them (`_lends`), while both may edit: those answer the person in
`GET /api/computers` and the page's route (`lent_by`, the owner's
username; that agent alone, `enabled`, `runs` `never`, no directory and
naming nobody, so a person never learns whom else it serves; a computer
that keeps the page in no work folder left out), `far_turns.remote_of`
(`registry.seats`, after the person's own computers that keep the work
folder: their chat on a synced folder's file, a comment's turn), and
`agents.of_account`. `registry.names_of` names the computer in a comment its agent
wrote for the person, by a row of the comment's workspace of the owner
kept with the comment (`comments.agent_owner`) while it serves the person
and both edit there, as `_lent_rows` reads it. The page's computers list
the person's own before the lent ones, as `seats` takes them, so the
chat and the *Send to* tick name the computer the turns go to.

A conversation names whose agent it talks to (`computer_chats.owner`,
pages.db step 63), and a far turn is the owner's computer's: `GET
/computers/chats` lists the turns of its account's conversations and of
the people its agent serves (`registry.people_of`, those who may still
edit), the claim checks the conversation's owner and that the agent still
serves the person (`registry.serves`, 409 otherwise) and names them in the
turn (`for: {id, username}`), and `write` takes the owner's computer's run
alone. An announce that drops a person fails the turns of theirs that wait
for it (`_end_orphaned` → `far_turns.fail_queued`, "The owner of Codex on
TIM-PC no longer lets you use it."); the computer
stops the one that runs ([local_agents.md](local_agents.md) "A co-author's
turn"). A co-author's far turn asks only where the computer relays asks
here, allowed once at most ("What the agents ask, answered here"), and is
answered by that person or the owner. On the server that keeps the
page, the owner reads the person's conversations there as on its own:
the chat's History lists them under the person's name (`GET
/api/local-agents/chat/history`'s `lent`), `GET
/api/local-agents/chat?conversation=` shows one, and the owner's
Background tasks list the person's turns that wait or run there
(`far_turns.remote_turns` by the conversation's `owner`, `params.person`),
each opening its conversation.

### The agents' turns there (steps 4c, 4d, 4.2 and 4.3)

The agents a computer announces on for the token's account
(`assistants`' `enabled`) are that account's on every bucket of the
workspace, a page of a synced folder or not, a folder, the library
(`registry.seats`: each agent on of each of the account's computers shown
to the workspace, as announced, read even while the computer is away; step
4.2 of [the plan](../research/agents-as-participants-plan.md),
[local_agents.md](local_agents.md) "Another server's chat"), and per
account: a member's computer serves only that member's turns (and its
co-authors', above, on a work folder's files alone). Where a computer
keeps the work folder the bucket is in (a folder vault of its root, a
work folder of an account that may edit there), its agents work in that
vault (`folder_id`, `paused` as it is), and its seats come first; else in
a directory the computer offers (`dirs`), picked when the conversation
begins, or an empty one of its own. A conversation is with one
computer's agent (`computer_chats.computer`, pages.db step 68), and its
turns go to that computer alone. The chat's strip says why an online
computer's agent is not ready (*Codex is not signed in on TIM-PC.*); a turn
for a computer offline is refused, never queued ("TIM-PC is offline: send
this again once it connects."). A computer whose token goes fails the
turns its runs hold (`registry._fail_held`), and an announce, or a
token's end with no other token of that computer there, fails the turns
queued for it that it would no longer take (`registry._end_orphaned`,
`_refusal`: its agent off, a person no longer served, the work folder no
longer kept, the directory no longer offered, the channel gone).

The chat's computer side there (step 4d, [local_agents.md](local_agents.md)
"The chat on another server's page") has its own routes, the token's
account's turns with that computer alone:

| Method | Path | |
|---|---|---|
| GET | `/api/computers/chats` | `{turns: [{id, conversation_id, bucket, agent, dir: {kind, id}, at}], stops: [{id, run}], answers: [{id, run, ask, decision, note}]}`: the turns queued for this computer (its account's conversations with it, and the people its agent serves; oldest first, at most 20; none in a work folder paused there, none of a bucket gone, each of an agent on there: `registry.taking`), `dir` where its agent works by an id (`far_turns.dir_of`: `vault` a work folder's, `offered` a directory's, `own` "" for one of its own; never a path), the runs of its turns asked to stop, and the answers given here to what its running turns ask (`far_turns.answers_of`, below). |
| POST | `/api/computers/chats/{turn}/claim` | `{run, agent}` → `{turn: {id, conversation_id, bucket, title, text, selections, model, effort, speed, for: {id, username}, skill?, thread_id?, comment_id?}, history, about, dir: {kind, id}, seen, thread?, tools: {url, token} \| null}` (`for` the account the turn is for, the token's own or a co-author's; `skill` `{name, instructions}`, only for a message that ran one, its instructions rendered here from that account's catalog ([skills.md](skills.md#a-computers-agent)), the run failed and 409 when it is gone; `title` the conversation's, which names a new Codex thread; the computer cuts it as a local title, `chats.title_of`; `about` what the bucket is, `chats.about`: its kind, id, title or name, format, and in a work folder its path and the work folder's name; `dir` as listed; a comment thread's turn names its thread and the comment it is, and `thread` is the thread's entry with its PDF place, whose source line the computer finds on its own disk: [local_agents.md](local_agents.md) "A comment sent to an agent"; `tools` Gamma's tools for the turn, below): queued → running in one locked write keyed by the run (the same run again: the same answer, with a new token in place of the one the lost answer carried; another: 409; 404 not this computer's turn; 409 an agent off there or another agent's, left queued, a co-author's the agent no longer serves, a work folder this computer does not keep or paused there, a directory no longer offered, a bucket gone or no longer in the work folder its conversation works in, failed with why). |
| GET | `/api/computers/threads/{id}/picture?at=now\|before` | The JPEG of the region of a thread whose turn a run of this computer holds running (`far_turns.holds_thread`: the run's holder the token, the turn's conversation of that thread, its owner the token's account), as `GET /api/annotations/{id}/picture` draws it, which the computer fetches beside the thread for its prompt; 404 "No thread of a turn on this computer has that id." for any other thread, and when the entry has no such picture; 400 for another `at`. |
| POST | `/api/computers/chats/{turn}/write` | `{run, seq, fields}` → `{ok}` or `{ok, stale: true}` when `seq` is not above the last written (an end of a thread's turn that wrote no reply is answered in its thread here, `threads.settle`): the agent message's `text` (through `prompt.inert`), `steps` (a path not relative stored as `outside`), `changes` (a path not relative left out), `changed`, `built`, `error`, `fresh`, `asks` (what the agent asks that this server may answer, where the computer relays its asks: each `{id, kind: command \| files, once: true, command?, command_shown?, marked?, lines?, inner?, shell?, background?, cwd?, host?, paths?, diff?, reason?, at, expires_at}`, at most 20, a command, a place or a diff longer than the computer shows whole 400, never cut; those answered here left out, below), `waiting` (a flag: an ask of the turn waits for an answer on the computer alone, [local_agents.md](local_agents.md#asking-on-this-computer)), `state`, `model`, `effort`, `speed`, its `ended_at` this server's clock (an end writes `asks` empty and `waiting` false and deletes the turn's token and the answers kept for it; any other write keeps the token alive); 409 another run, a turn no longer running, or a gone conversation. |

**What the agents ask, answered here** (step 4.3 of [the
plan](../research/agents-as-participants-plan.md), decision 7). Where the
computer's link lets this server answer (its `asks`, "The servers this
computer answers") and the agent may ask (`runs` `ask`), a far turn's ask is
written in its message as a chat turn's is (`asks`, each `once`: no grant
is kept for a turn on another computer), so the chat draws its card and a
thread's chip reads *Codex is waiting for you* with *Answer in the chat*.
`POST /api/local-agents/chat/turns/{turn}/asks/{ask}` answers it, by the
turn's person or the agent's owner (`chats.readable`; another account 404),
`once` or `deny` with a note (`chat` 400): the answer is kept with the run
(`computer_chat_runs.answers`, pages.db step 69, `far_turns.answer_run`), the ask
leaves the message at once, and the computer is woken (`chat`), whose
listing hands it the answer (`answers`). The computer's own bar lists the
ask too, and the first answer is the one: one here after the computer's is
refused once the computer wrote the message without it (409 "Codex no
longer waits for that."), one answered here already 409 "That was answered
already.", a turn ended 409 "This turn has ended."; an answer kept here is
listed while the run's writes still name the ask. A computer offline or a
channel down: the answer waits with the run, and the ask on the computer
with its turn (declined there after 10 minutes, as any ask); a Stop, here
or there, ends both. Background tasks read the turn `asking` (`progress:
{phase: "asking"}`), and `there: true` where the ask waits on the computer
alone. With the link's `asks` off nothing is relayed: the message says
`waiting` alone, and a co-author's far turn asks nothing.

**Gamma's tools for a far turn** (step 4.2; [local_agents.md](local_agents.md)
"Gamma's tools"): the claim mints a turn token of this server
(`integrations.mint_turn`: the account the turn is for, the token's
workspace alone, named with the computer's agent's id here,
`computer:<computer>:codex`, so its comments, resolves and highlights read
*Codex on TIM-PC* as the computer is called here: `agents.of_token`,
`registry.far_name`), living `far_turns.TOKEN_S` from the claim and renewed by
the run's writes (at most once a minute), kept in the run's row
(`computer_chat_runs.token`, step 68) and deleted at the turn's end
however it ends (`far_turns._close`: an end written, a Stop that ends it here,
the computer's channel taken back, its message gone) and by a retried
claim, whose new token replaces it. `url` is this server's MCP endpoint
as another computer reaches it (`routers/computers.py` `_mcp_url`): its
public address (Settings → Administration → Server), else the loopback
address the claim came to; `tools` is null without either (the MCP
transport takes no other host), and the turn runs without them. The
computer takes them only at the origin its channel speaks to this server
at.

The announce's assistants carry their `models` for the chat's model
chip (at most 32, 8 efforts each, with their speeds); an announce is at
most 128 KiB.

### Sessions over the channel (step 5)

History's *On TIM-PC* for a far conversation's agent
([local_agents.md](local_agents.md) "Sessions started elsewhere", step 5
of [the plan](../research/agents-as-participants-plan.md)) lists the
sessions its CLI keeps of the directory it works in there, started in a
terminal or an editor, and opens one as a conversation here. This server
cannot reach the computer, so it asks through the channel
(`gamma/computers/sessions.py`): the ask waits in memory, the computer's
held poll is woken (`{wake: ["sessions"]}`), and the computer takes and
answers it. `GET /api/local-agents/sessions` waits on the event loop up
to 15 s for the answer and keeps a listing's 30 s, so History opened
again soon reads it (a listing asked while one of that agent and
directory waits joins it); an opening waits 30 s, and its answer forgets
that computer's kept listings. Nothing is asked while History is closed,
and nothing polls: no answer in time is said (`away: "waiting"`), and the
answer that comes later is kept for the next opening. A computer has 4
asks unanswered at most, and one never answered goes after 60 s.

Only the account's own computer is asked (`registry.own_computer`: never
one another account lends it, so a co-author asks nothing), for an agent
on there, in a directory named by an id: a work folder that computer
keeps (`{kind: "vault", id}`) or one it offers (`{kind: "offered", id}`),
never a path; anything else is 404 before anything is asked. A computer
offline is not asked (`away: "offline"`; an opening 409 "TIM-PC is
offline: send this again once it connects."), nor one whose announce does
not say `sessions` (`away: "older"`; an opening 409 "TIM-PC needs a newer
Gamma to open its sessions here.").

| Method | Path | |
|---|---|---|
| GET | `/api/computers/sessions` | A `computer` token only: `{asks: [{id, kind: list \| open, agent, dir: {kind, id}, session?, conversation_id?, rebind?}]}`, the asks of this token not handed out yet, each handed out once (`sessions.taken`). |
| POST | `/api/computers/sessions/{id}` | A `computer` token only, the token the ask was of (another 404): the answer → `{ok}`, handed to the route that waits (404 for an ask answered already or given up; 413 past 3 MiB), checked and capped as a turn's writes are (`routers/computers.py` `_session_answer`; 400 for another shape): `{error, status}` a refusal there (404 or 409, its words cut to 2000 characters); to a listing `{sessions: [{id, title, at, created, source, busy, conversation}]}`, at most 50, one whose id is of another shape left out, `conversation` the conversation of Gamma's that is its session there ("" for none); to an opening `{busy: true}`, `{conversation}`, or `{title, messages}`, at most 200 messages (the person's `{role: "user", text, at}`, the agent's `{role: "agent", text, steps, state, error, at, ended_at}`), each kept as this server keeps a conversation's (`sessions.user_message`, `agent_message`, `from_session`): its text made inert (`prompt.inert`), its steps as a turn's write keeps them (a path not relative `outside`), its state an end. |

Opening one (`POST /api/local-agents/sessions/open` with the far agent's
id) mints the id of the conversation it will be and asks the computer to
open the session for it; the computer reads it, binds it there to that id
(its vaults.db `agent_conversations`, as a far turn's session is bound)
and answers its turns, and the conversation is made here with them
(`chats.adopt`, `computer_chats.computer` that computer's, `dir` the
directory's id), so its next far turn's claim resumes the session there:
the session a turn resumes is always one the computer bound, never one
this server names. The bucket's active conversation at work is refused
(409) before the computer is asked, since it binds before it answers. A
session the computer names as a conversation's already (`{conversation}`)
is that conversation where it is the account's here with that agent
(opened as History opens one; one of another bucket 409 with where); one
this server has otherwise (a co-author's, another account's, another
computer's or agent's, a comment thread's) is 409, never taken from it;
one this server does not have (one of the computer's own server, or one
gone here) is asked again with it as `rebind`, and the computer moves the
session to the new conversation, the old one letting go of it
(`store.unbound`: its next turn starts afresh). A session another process holds there is
refused with why (409 "Codex has this session open in another window:
close it there first."). The listing's `ours` is this server's: a session
whose `conversation` is one of the account's here with that computer's
agent, titled as it is.

### A work folder detached here

A work folder of this server that a computer keeps (an announced folder
vault of its root) leaves Gamma by its Detach, on this server, from any
window ([work_folders.md](work_folders.md) "Detaching a work folder"):
`registry.keepers` names the computers that announced a vault of it,
whose account may detach it as the workspace's owner may, while it may
edit the workspace (an announce is not checked beyond the folder, and a
viewer's computer token announces what it likes), and the dialog
names the first of the caller's own (an online one first) and whether it
is online. The folder is let go in place, then each of those computers
is woken with `let_go`: a held poll answers `{wake: ["let_go"]}` at once,
and one of a computer offline is answered so at its next poll. The
computer then runs a round of each work folder of this server it keeps,
unless paused ("The channel"), which finds the mark gone; a computer whose
server restarted meanwhile, or whose vault is paused, finds it at its next
round all the same.

## The host

### Where the credential is kept

A users.db `computer_links` row per server, workspace there and local
account (additive): `id`, `remote_url` (as a vault of it keeps it, no
trailing slash), `workspace_id`, `created_by` (the local account), `token`
(sealed with `publisher_sessions.seal`, as a vault's), `token_id` (its id
there, for whoever revokes it), `account` and `username` (the account
there, as the announce named it), `workspace_name` (the workspace's name
there, as the announce's answer names it: users.db step 67, "" until a
server names it), `asks` (step 67: whether that server may answer what
this computer's agents ask, below), `created_at` and `status` `{state,
error, at}`, with what was in the way when that server was not reached as
itself (`error_kind` and its fields, below):

| State | |
|---|---|
| `connecting` | made, not yet announced; moved to another address, not yet announced there |
| `connected` | the announce succeeded, and the polls since |
| `refused` | Gamma's own 401 or 403: the token was revoked or expired there; tried again after 10 minutes |
| `older` | Gamma's own 404 or 405 on the channel's routes: a Gamma from before them; asked again after an hour |
| `unreachable` | anything else; tried again after 5 s, doubling to 5 minutes; a redirect, a certificate not trusted or no HTTPS after 5 minutes, since none goes away by itself (a certificate's rest ends when the shell replaces the pins: `folder_links.certificates_changed`); a handshake cut short is not "no HTTPS" |

*Gamma's own* is an answer whose body is a JSON object with a `detail`
key (`gamma_sync.is_gamma_refusal`, FastAPI's refusal shape). Anything else
in the way is never the token's refusal and never an older server
(`gamma_sync.blocked`, shared with the vaults and `net_proxy`): a sign-in
or proxy page's 401/403/407, Cloudflare's challenge, a gateway's 502/503/504
are `unreachable` with `error_kind: "blocked"`, and an HTML 404 is plain
`unreachable`. The status's `error_kind` (`gamma_sync.status_fields`):

| `error_kind` | Fields | When |
|---|---|---|
| `redirected` | `move_to` (the address the redirect points at: scheme, host and port, at most 300 characters), `upgrade` (the same host name, http:// to https://) | a 3xx that keeps the request's path; never followed |
| `blocked` | `blocked`: `signin`, `proxy`, `cloudflare`, `gateway` | a 3xx to another path (a sign-in host's `/login?rd=…`), or a refusal that is not Gamma's |
| `certificate`, `certificate_changed` | `cert`: `{sha256, subject, issuer, not_before, not_after}` of the certificate served, or null | its certificate not trusted here; a pinned one replaced ([https.md](https.md)) |
| `not_https` | | an https:// address answered without TLS |

The status is written only when its state, its error or its `error_kind`
changes.

**This computer's id** for a server is the HMAC of the install's id and
the origin the server was first shown at: users.db `server_addresses`
(`gamma/remote_servers.py`) keeps, per origin this computer's links moved
from or to, that first origin (`id_origin`) and, for an address the links
left, where they went (`moved_to`). A server never moved has no row and the
origin of its address. A move (below) keeps the id, so the server sees the
same computer, presence row and token at its new address, and a move back
gives it again. An address the links left is never given that id again: a
new channel there (`channel.make`, not one re-tokened) gets a generation of
its own (`remote_servers.fresh_id_origin`: `<origin>#<8 hex>`), so another
server now at that address cannot match this computer to the one that left.

### The routes (`config.folder_links_enabled()` only, else 404, as `/api/folder-links`)

| Method | Path | |
|---|---|---|
| GET | `/api/computer-links` | The account's channels (a session): `{links: [{id, remote_url, plain, workspace_id, workspace_name, account, username, status, asks, created_at}]}`, never the token. `plain` says whether the address is plain http, without DNS (`remote_servers.plain`: `""` for https:// and loopback, `"lan"`, `"name"`). Settings → AI → Local agents reads it in the desktop app. |
| POST | `/api/computer-links` | The shell's word and a session (`routers/shell.py` `from_shell`, as the folder-links **P** routes): `{remote_url, workspace, token, token_id}`, checked with one announce that names no vault and no directory and lets that server answer nothing. 201 `{link, replaced: {token_id} \| null}`: one already kept for that server, workspace and account takes the new token, and `replaced` names the old one, the shell's to revoke there. 400 "That server refused this computer's token.", a token of another workspace, or a new channel over http:// to a host on the internet (refused before any request; one kept there takes its new token: `remote_servers.refuse_plain_far`), 409 "That server is older than this app: it cannot show this computer." or a redirect (the same host over https: "*old* now redirects to *to*: open *to* in Gamma and show this computer from there."; any other address: "That server redirects to another address (*to*). If you moved it, change its address in the launcher.", never "open"), 502 unreachable, a certificate not trusted here, no HTTPS, or a sign-in or proxy in the way (their words). The channel starts at once. |
| PATCH | `/api/computer-links/{id}` | `{asks}` → the link: whether its server may answer what this computer's agents ask (`channel.set_asks`), announced there at the channel's next turn. `true` takes the shell's word, refused too on a server started with no secret (`shell_strict`: "Only the desktop app itself may let another server answer what this computer's agents ask.", 403), `false` the session alone; the link's maker (404 otherwise). |
| DELETE | `/api/computer-links/{id}` | The shell's word, the link's maker (404 otherwise): the channel stops and is forgotten, and the turns of that server its vaults run here stop ("Stopped: this computer no longer answers that server.", `runner.STOPPED["unlinked"]`); `{ok: true, token_id}` for the shell to revoke there, which fails there what the token's runs held (`registry._fail_held`). |

### The servers this computer answers

A link needs no vault: Settings → AI → Local agents, in the desktop app, lists
the account's links as *Servers this computer answers* (step 4.1 of
[the plan](../research/agents-as-participants-plan.md)), each with its
address, its workspace and the account there, how its channel stands
(*Connected*, *Not reachable since 2:02 PM*, *Refused this computer's
token since …*), *Remove*, *Reconnect…* for one that server refused, and
a switch, *NAS may answer what the agents ask*; then *Add a server…*. Each
asks the shell ("The desktop shell" below), so no other window shows the
section.

The switch is `asks` (decision 7): off for every link to begin with
(step 67 made every link so far `0`), turned on only with the shell's
word after its native question naming the server, off with the session.
The channel announces it (`state_of`'s `asks`), the server keeps it in its
`computers` row and answers it with the account's computers. While it is
on, what the agents ask in a turn of that server's is written there too
and answered there ("What the agents ask, answered here" above;
[local_agents.md](local_agents.md) "Asking on this computer"); turned on or
off, the messages there of the turns running here are written again with
their asks or without them (`runner.relays_changed`). A new token for a
link keeps its switch.

### The channel

One daemon thread per row (`channel._Channel`), started when the row is
made, checked by `folder_links.tick` every 30 s (the missing ones start,
the ones whose row went stop; the lifespan has no room for another
timer) and stopped at shutdown. Its HTTP client is `gamma_sync.Server`
(bearer, urllib; `default_open` is the tests' seam, `default_open_url`
the one that sees the address), which follows no redirect and names a TLS
failure ([https.md](https.md)), timeout the hold plus 15 s. Each turn:

1. **Builds the state** (`state_of`): the computer's id for that server
   (the HMAC-SHA256 of the install's id, `cloud_auth.device()`, and the
   origin the server was first shown at, above, 22 characters: two servers
   cannot match one computer by it), its name (the host name), the app's version, every vault of
   that server, workspace and local account (`folder_links.all_links()`:
   its kind, its folder or page id, whether it is paused), and the
   assistants as the catalog last found them (`catalog.known`: one probe
   when none was ever made, never one to refresh it, so a channel keeps no
   agent probed; `[]` where agents do not run here,
   `local_agents.enabled()`), each with whether it is on for the link's
   local account, what it may run and the people of that server's
   workspace its owner lets use it (`channel.assistants`, `people`; whom
   Settings may name, that workspace's editors, the host asks over the
   channel: `channel.members`, `GET /api/computers/people`), and whether
   that server may answer what they ask (the link's `asks`). An agent
   turned on or off, or the switch changed, nudges the threads
   (`folder_links.agents_changed`, `channel.nudge`), so the next turn
   announces it. The announce's answer names the account and the
   workspace there, kept when they change (`_account`).
2. **Announces** when that differs from what it last announced, or the
   server asked, keeping the answer's `version` and account.
3. **Polls**, held up to 25 s; a poll answered at once (with nothing, or
   asking to announce again, as a server that always asks so would) waits
   1 s before the next. The status turns `connected` as the announce
   succeeds.

A vault added, paused, resumed or removed nudges the threads
(`channel.nudge`, from `folder_links`): they see it when the poll they
hold ends, at most 25 s later.

A poll answered `{wake: ["sessions"]}` (History's *On TIM-PC* there:
"Sessions over the channel") takes that server's asks
(`_Channel._sessions`, `GET /api/computers/sessions`) and answers each in
a thread of its own (`channel._answer_session`,
`local_agents/sessions.answer_far`), so a slow listing never holds the
poll: for the channel's account alone (its agent on here, `store.owns`),
in the directory the ask names as a far turn's claim does
(`home.far_place`: the vault of that server's work folder this channel's
account keeps, or a directory offered; a path, a directory of its own or
anything else refused with why); a listing as History's here
(`sessions.listed`, with the conversation each is the session of), an
opening only of a session `read` finds in that directory, bound to the
conversation id that server named unless this computer knows that id
already, its messages without ids, the newest within 2 MiB. The state
says `sessions: true`.

A poll answered `{wake: ["let_go"]}` (a work folder of that server
detached there) gives each work folder of that server kept here a round
at once, in the background (`_Channel._let_go`): it finds the mark gone,
stops, and records it (`folder_links.LET_GO_THERE`, the list's `let_go`),
and the desktop shell forgets the vault (below). Paused ones wait, as the
tick holds them: a paused vault learns of the detach at its next round, a
sync asked for in the sync panel or the tick's once it is resumed.

**An address move** (`POST /api/remote-servers/move`, the shell's word,
`gamma/remote_servers.py`; [https.md](https.md)) re-points the account's
vaults, channels and offline copies of one server to its new address in
one transaction, once the new address answered `/api/health` with no
credential and each row's token one at a time (a channel's first: an
announce that names no vault, with the id the computer keeps), stopping at
the first that fails. A moved channel's status is `connecting` again and
its thread restarts at the new address; one this computer had there
already gives the moved row its place, its token in the answer's `revoke`
for the shell to revoke there.

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
  above. *Stop showing* deletes the host's link and revokes its token
  (the computer's agents stay on: that server, told nothing more of this
  computer, gives it no work); when that server did not revoke it (not reached, signed out, another
  account signed in there: the revoke route answers `revoked: false`),
  the notice says so and names the connection to remove in that server's
  Settings.
- **The last vault** of that server and workspace removed
  (`releaseRemote`): the host's link goes and its token is revoked there,
  the removal's notice saying so when it could not be. The host stops
  starting at launch for remote servers (`folderHost`, `releaseHost`) only
  once it keeps no vault of one and no link.
- **Settings → AI → Local agents** (page asks of the desktop app's own server,
  `preload.js` PAGE_ASKS, acted on with the shell's own session, as the
  agents' are): *Add a server…* (`add-server {link: "new"}`,
  `addLinkedServer`) asks which of the launcher's remote servers this
  computer answers (`keeping.serverAsk`, naming what that server then sees,
  the offered directories' names among it, and that whoever signs in there
  as the account can send work to the agents turned on here), with the window's session there: signed out, it
  offers to open that server to sign in (`keeping.serverSignIn`) and
  mints nothing; with several workspaces there (a clone's left out, the
  personal one first) it asks which (`keeping.serverPlaceAsk`); then
  `showComputer` mints and hands over as for a vault, and the host is
  remembered for launch (`folderHost`). `add-server {link: <id>}`
  (*Reconnect…*) hands that link's server a new token, asking nothing.
  *Remove* (`remove-server`, `removeLinkedServer`) deletes the link and
  revokes its token there as *Stop showing* does (`dropComputerLink`),
  asking nothing. The switch (`link-asks {link, asks}`, `setLinkAsks`)
  asks before it turns on (`keeping.linkAsksAsk`, a warning naming the
  server, what whoever signs in there can then allow, that the question is
  shown there with its command, its paths and its changes, and that a co-author
  there allows what their own turn asks, once; Cancel the default) and
  PATCHes with the shell's word (`shell_strict`); off asks nothing.
- **A work folder detached there** (the host's list says `let_go`): the
  shell's next reading of the host, at most 15 s on, forgets the vault as
  *Detach…* forgets one (`forgetDetached`):
  `DELETE /api/folder-links/{id}`, which only forgets it, its directory as
  it is but for `.gamma/`; the token minted for it revoked there and, for
  the last vault of that server and workspace, this computer taken off it
  (`releaseRemote`); the bar says so. One that fails is tried again at the
  next reading.

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
  server but the desktop app's own), `VaultChip` draws the first computer's
  that keeps the page (`computerChip`; the page's computers also list
  those that keep none of it, for their agents, which the chat and the
  comment fields read: `chat/agents.js` `farAgents`): *Synced vault on TIM-PC* with a dot, filled while
  online and hollow while not (`data-computer="online" | "offline"`), and
  a card: *Connected* or *Offline since 2:02 PM* (*Nov 3* another day,
  with the year another year), *Folder: cavity* or *File: main.pdf*,
  *Paused on TIM-PC*, and *Assistants there: Codex, Claude Code (not
  signed in)* (*(checking)*, *(not ready)*; a missing one left out, no
  line when none). No action yet: *Show in folder* needs the window to
  know it is on that computer (later, with 4d).

## Trust

- **Plain http.** A new channel over http:// to a host on the internet
  (`host_scope.resolved`: its DNS answers, or a dotted name that does not
  resolve) is refused before any request; one on the local network is
  made, and the shell asks the person once per origin before it mints the
  token (`POST /api/remote-servers/check`, with the channel's
  `workspace`). A channel kept there from before keeps its upkeep. [https.md](https.md) has the table.
- **Only the maker, and whom its agent serves.** The page route, the nudge
  and the chip are the token's account's; a person the computer's owner
  named for its agent learns the computer's name, whether it is online,
  that agent and which page of a work folder it keeps ("A co-author's
  agent"), never whom else it serves. Another member, a share, a token and
  the guest learn nothing of the computer.
- **The computer's token opens only its channel**, and the channel only
  the computer's token: no read of the library, no write, no MCP.
- **What the server learns** is the computer's id for it, its host name,
  the app's version, which of the workspace's folders and pages it keeps
  and whether each is paused, which assistants are there with their
  versions, and the directories offered its agents by id and name. Never
  a path, never a problem's words, but in what a turn's agent asks where
  the computer relays its asks: the command as the agent gave it, the
  directory and the files it names relative to where it works, under `~`,
  or whole when outside both (consent needs to see where).
- **What it widens.** The server gives the agents turns of the token's
  account (and of the co-authors named for it), on any of its pages, in a
  work folder's vault, a directory the computer offers or an empty one of
  its own, named by an id the computer resolves itself and refuses
  otherwise ([local_agents.md](local_agents.md) "Another server's chat").
  It hands each turn a token of its own, for the account the turn is for
  and that turn alone; the computer gives it to the agent only at that
  server's own origin.
- **Answering the agents' asks is the computer's to allow**, per server:
  a link's `asks` is off until the shell's word turns it on after its
  native question, which says that whoever can sign in there as the
  account, and whoever runs that server, can then allow what runs here.
  A page cannot turn it on; the server only learns it from the announce,
  and the computer checks it again when an answer comes
  (`runner.answer_relayed`). What that server answers is allowed once at
  most, never kept as a grant, over https alone, within the caps of any
  turn there (what the computer cannot show whole is declined unseen).
- **Sessions read there.** The server asks of the CLI's sessions of a
  directory its agent works in for its own account alone (a co-author's
  use asks none), by the directory's id; the computer answers for the
  channel's account, in a directory it would run that account's far turn
  in, and sends titles, times, what each was started from and, for one
  opened, its turns as messages (the person's text, the agent's text and
  steps, paths relative, no thinking, the newest within 2 MiB). It opens
  only a session it finds in that directory, and binds it there to the
  conversation that server makes, so a far turn resumes only a session
  the computer itself handed out.
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
vaults with their assistants; refused, older and unreachable; a proxy's
HTML 403 blocked and its 404 unreachable, never refused or older; a
redirect kept with `move_to` and `upgrade`, and making a channel there
naming the new address only for the same host over https; stopping when
the link goes; the workspace's name kept, `asks` off until the shell's
word, announced and answered with the account's computers, a new token
keeping it; a link removed stopping the turns of that server and taking
their directories; a computer with an agent on offered on a page it keeps
none of, after those that keep it, with the directories it offers by id
and name, never a path), and a co-author's agent (the people an agent serves named
with the shell's word and taken off, the announce naming those of that
server and workspace, asked over the channel; the server offering a named
person the computer's agent alone and nobody else, no longer once the
announce drops them, their waiting turn failed, none on a page of no work
folder); `backend/tests/test_computer_agents.py` and
`test_computer_chats.py` the seats and the turns' routes;
`backend/tests/test_remote_chat.py` a co-author's turn there run on the
computer, a page in no synced folder run there, the turn's token; the
sessions there (listed for the owner alone, a directory not offered or a
path refused on both sides, the answer kept, one opened and resumed by
the next far turn, one bound to a conversation that server lacks opened
afresh, busy, offline, an older computer, no answer yet, the channel's
routes taking and checking the answers);
`backend/tests/test_remote_asks.py` the asks relayed where the link lets
that server answer (answered there by the person or the owner, another
account refused, once at most, the first answer the one, an answer
waiting out a channel down, a Stop from either side), none with the link
off. `backend/tests/test_remote_servers.py`: the address move
(the id kept and given again on a move back, a new channel at a left
address a new id, the probe with no credential, the channel checked first
and the first failure stopping the rest, a channel already at the new
address merged) and plain http refused before any request. `desktop/test/keeping.test.js` (the panel line per
state, its button, Settings' questions) and `main.test.js` (mint and
hand-over when a vault of another server is made, a failure revoking,
*Show this computer there*, *Stop showing*, the last vault removed, a work
folder detached there forgotten with its token revoked; Settings' *Add a
server…* with no vault, its server and workspace asked, signed out
offering to sign in, *Reconnect…*, the switch asked first with the shell's
word, *Remove*). `backend/tests/test_migrations.py` `test_v67_*`, `test_v68_*`, `test_v69_*`. `backend/tests/test_remote_work_folders.py`:
a work folder a computer keeps detached here, by the workspace's owner or
the computer's account alone, the computer named to its own account, online
or not, and woken (`let_go`); the woken channel's round of each work folder
of that server. `frontend/tests/vaultState.test.mjs`
(`readComputers`, `sinceText`, `computerChip`), `collabSession.test.mjs`
and `collabPoll.test.mjs` (the nudge for another page, on hello, rejoin
and poll). The browser group `computers` (`scenarios/computers.mjs`): two
servers, a NAS and a computer with the stand-in Codex; the computer's
vault of a NAS folder and its channel; the chip on the NAS for the maker,
none for another member; the computer stopped, the chip offline. The
group `https` (`scenarios/https.mjs`): the same over https with a
self-signed certificate, pinned by the shell, moved to another address
with the same computer id, and the channel saying the certificate changed.
The group `remote-asks` (`scenarios/remoteAsks.mjs`): a turn's asks on the
computer's bar alone with the link's `asks` off, then, turned on, a build
Codex asks to run in a turn of a library PDF of the NAS answered on a phone.
