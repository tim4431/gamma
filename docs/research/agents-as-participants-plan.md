# Agents as participants: the build plan

Status: **a plan, built**: steps 1-5 are all built, step 5 over the
channel too, each step's "As built" saying where the build differs.
Written 2026-11-14
against `dev` @ b4870d47, after a survey of the code behind work folders, local agents,
computers and feedback. The file and line references were read then, so
check them again before building. This file says what to build and in
what order; the note beside it,
[agents-as-participants.md](agents-as-participants.md), records what was
asked, what exists and why each decision went the way it did.

What it builds, in one paragraph: a synced folder is drawn as a file
explorer and detached, not deleted (step 1); Codex and Claude Code are
agents listed in Settings and picked in the chat as Gamma AI is, on any
page, with Gamma's own tools (step 2); the Feedback dialog and its
requests go, an agent reads every highlight and comment and answers in
comment threads under its own name, and a thread is where a person and an
agent talk (step 3); a computer is linked to a server from Settings and
its agents are used from any device (step 4); a session started in a
terminal is continued in Gamma (step 5).

## Decisions in one list

The first eight are the note's, in its order.

1. A synced folder is drawn as a file explorer with an icon per file type.
2. Detach is the one way a synced folder leaves Gamma. The disk is never
   touched; the dialog asks whether the files stay as pages.
3. Agents are listed in Settings per computer and chosen in the chat.
4. An agent is a named actor owned by a person; it is no member.
5. The owner may let a co-author use their agent.
6. There is no feedback surface.
7. Asks answered from another device: a switch per server, set on the
   computer, off to begin with.
8. An agent is turned on at its computer, never from a page.

What this plan adds to them:

9. **A thread is where a person and an agent talk** ("The interaction"
   below). The agent's question is a comment on the passage it is about;
   the field of every comment has a *Send to* tick.
10. **A conversation has one agent**, chosen when it starts. Picking
    another agent in a conversation that has messages starts a new one,
    as each side of the chat has its own conversation today. The agent's
    own session is that conversation's, so a resume is never ambiguous.
11. **One chat, two tables.** Gamma AI's conversations stay in `chats` and
    `chat_history`: the workspace's, saved by the client. A computer
    agent's stay in the table `computer_chats` becomes: their owner's,
    written by the server as the turn goes. The two differ in who writes
    and who reads, so they are not merged; one transcript, one composer,
    one History and one export read both. This narrows the note's "One
    conversation store".
12. **A computer agent's conversation is private** to the person who
    started it and to the agent's owner. It answers the note's first open
    question the cautious way.
13. **A co-author is granted the agent once.** The owner names the
    person on the computer; from then on they ask the agent with `@` or
    the tick, and it answers them. Their turn runs only in the directory
    of the synced folder its page is a file of, and what it asks to run
    is granted one time at most: *Allow once* or *Decline*, never *Allow
    in this chat*.
14. **On a page of no synced folder** a computer's agent works in a
    directory the person picks from those the computer offers, or in an
    empty one of its own, where it has Gamma's tools and no files.

## The interaction

The owner's reference is the way Claude's documents do it: the agent
leaves a question as a comment on the passage it concerns, and the reply
field carries a tick that sends the reply to the agent.

```text
        … The rule gives normal users access to any device …
            ▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔
┌──────────────────────────────────────────────┐
│ ✳ Codex   on TIM-PC, for tim   14:02    ⋯  ✓ │
│ Right now the rule opens the camera to every │
│ account on this PC. Is that fine, or should  │
│ I limit it to the plugdev group?             │
│ ┌──────────────────────────────────────────┐ │
│ │ Ask Codex to edit                        │ │
│ │ ☑ Send to Codex ▾                      ↑ │ │
│ └──────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

The rules, which step 3.5 builds:

1. **The tick.** Every comment field, a new thread's and a reply's, has
   *Send to {agent}* when the person may use an agent on that page.
   Ticked, the comment is stored and is also the instruction of a turn.
   Unticked, it is a comment among people.
2. **Its default.** Ticked in a thread whose newest comment is an
   agent's. Elsewhere as the person left it last (a preference of the
   account).
3. **Which agent.** The one that wrote in the thread last, else the one
   picked in that page's chat. The label is a menu where there are
   several.
4. **`@name`** typed in the comment ticks the box for that agent. It is
   the same request field, so there is one path.
5. **The agent asks in a thread.** When it needs a decision it opens a
   thread on the passage with its question and ends its turn. The
   person's reply, ticked, resumes the same session.
6. **While it works** the thread carries a chip, *Codex is working*, with
   Stop. When the agent asks to run something the chip reads *Codex is
   waiting for you* and opens the chat's card.
7. **The answer** is the agent's reply in the thread, with *Open in chat*
   for its steps and what it changed. A turn that ends without one has
   its last message written there by Gamma, so a thread sent is a thread
   answered.
8. **"Comment to me anywhere"**: a new comment on any passage, ticked, is
   a turn about that passage. Nothing has to be gathered first, and there
   is no Send of a file's or a folder's comments.

Not taken from the reference: emoji reactions on a comment.

## 0. Before building: five checks

Each is run on the development machine with the real CLIs and decides one
thing below. None changes the repository.

| | Check | Decides |
|---|---|---|
| C1 | Codex's app-server takes Gamma's MCP endpoint as the only server of a thread (`thread/start`'s `config`, which `codex.py:516` builds from `_quiet`; a bearer token from an environment variable), and its tool calls arrive as events | 2.7's way in for Codex. If it does not: a stdio bridge the driver starts |
| C2 | Claude Code takes `--mcp-config` with an HTTP server and a header beside `--strict-mcp-config`, with `--allowedTools mcp__gamma__*`, under the flags `claude.py` passes now | 2.7's way in for Claude Code |
| C3 | A session Gamma's driver made shows in `codex resume` and in Claude Code's resume list, not only by its id | the wording of *Continue in a terminal*, and whether 5 needs the other direction more |
| C4 | A session started in a terminal is listed for its directory (Codex `thread/list` with `cwd`; Claude Code's session files of the project), its turns are readable, and a headless resume continues it | all of step 5 |
| C5 | `material-icon-theme` 5.40.0 (MIT, 1.7 MB unpacked, 1,393 files by the registry): its manifest function, how many icons the manifest names, the light variants | 1.1: every icon, or a chosen subset |

### What they found (run 2026-11-14, Codex CLI 0.167.0, Claude Code 2.1.318, Windows)

All five hold, three of them with changes to this plan.

- **C1, Codex: works, with two changes.** A thread's `config` takes
  Gamma's endpoint beside `_quiet`'s rule: `mcp_servers.gamma = {url,
  bearer_token_env_var: "GAMMA_AGENT_MCP_TOKEN"}`, every other server of
  `config/read` `{enabled: false}`, the variable set in the app-server's
  environment only. Exactly that server starts, a call arrives as an
  `mcpToolCall` item (`server`, `tool`, `arguments`, `readOnlyHint`,
  `result`, `status`), `networkAccess: false` does not block it, and a
  revoked token fails the next call with *Authentication required* while
  the turn completes. Changes: `_dispatch` accepts `mcpServer/startupStatus`
  for the one name Gamma added (and fails a turn on `failed`, or on any
  other name), and the thread's config adds
  `shell_environment_policy.exclude: ["GAMMA_AGENT_MCP_TOKEN"]`, without
  which the agent's own shell commands printed the token. No approval was
  asked before an MCP call, read-only or not.
- **C2, Claude Code: works.** `--mcp-config` with `{"mcpServers":
  {"gamma": {"type": "http", "url": …, "headers": {"Authorization":
  "Bearer ${GAMMA_AGENT_MCP_TOKEN}"}}}}` (the variable expanded from the
  process's environment), then `--allowedTools mcp__gamma`, under
  `claude.py`'s flags as they are; `--tools` limits the built-ins only.
  `system/init`'s `mcp_servers` must read `[{name: "gamma", status:
  "connected"}]`. In an ask turn, where Bash is allowed, Bash can read
  the variable: an ask turn's token is the same short-lived one, and the
  plan accepts that (2.7).
- **A token's reach.** A read token opens the REST API's reads as well
  as MCP. The token of a turn is therefore deleted at the turn's end,
  never outlives the run's timeout, and is refused by every route that is
  not `/mcp` (a scope of its own, as `computer` tokens have
  `auth.CHANNEL_PREFIX`).
- **C3: works.** A Gamma-made Codex thread is stored as any other
  (`source: "vscode"`, `originator: "gamma"`) and is listed for its `cwd`
  by `thread/list`'s default, so `codex resume` in the folder lists it.
  It has no name, so the picker shows Gamma's long first prompt:
  `thread/name/set` gives it the conversation's title. A `claude -p`
  session is a file of the project like any other (`entrypoint:
  "sdk-cli"`), and `claude --continue` there took it up. Neither
  interactive picker could be driven here, so their lists are inferred.
- **C4: works.** Codex: `thread/list {cwd, sourceKinds: ["cli", "vscode",
  "exec"]}` lists a directory's threads (an exact directory, separators
  and case normalized), `thread/turns/list {itemsView: "full"}` reads
  their items, and `thread/resume` continues one. A thread another
  process holds is refused with -32600 *already has an active writer*:
  `codex.py` today takes any resume error for a gone thread and starts a
  new one, which would fork it silently, so step 5 tells the two apart
  (`~/.codex/thread-writer-locks/<id>.lock` says it ahead). Claude Code:
  `~/.claude/projects/<slug>/`, the slug the path with every character
  outside `A-Za-z0-9` made `-` (cut at 200 with a hash after); `user` and
  `assistant` records chained by `parentUuid`; `--resume <id>` continues
  it. Claude Code has no lock: two resumes forked one file.
  `~/.claude/sessions/<pid>.json` names each live process's session.
- **C5: works, with one change.** The package's `generateManifest()` runs
  in plain Node and equals its `dist/material-icons.json`: 1,256 icons
  (1.03 MB of SVG), 1,391 extensions, 2,156 file names, 4,674 folder
  names, a light overlay. VS Code's rule, which Gamma implements: case
  ignored; a whole file name, then the longest dotted extension (`d.ts`
  before `ts`), a key `parent/name` only under that parent, then a
  language id, then the default; folders by name, the root folder always
  `folder-root`. Language ids reach nine icons nothing else does, all of
  third-party languages, so thirteen of VS Code's built-in associations
  (`.gitignore` → ignore, `tsconfig.*.json` → jsonc, `Dockerfile.*`, …)
  are the whole table needed. The change: a dev dependency and a Vite
  plugin (`frontend/tools/fileIcons.mjs`) that emits the SVGs into
  `dist/assets/file-icons/<version>/`, which the server already caches as
  immutable, and the map as a lazy chunk; not `public/`, which is served
  `no-cache`. The theme's licence ships beside the icons.

The full results, with the commands and the JSON each one saw, were kept
with the session that ran them; what an implementation needs is above.

## 1. The explorer and Detach

Independent of the other steps.

### 1.1 Icons by file name

- **New** `frontend/src/shared/ui/fileIcons.js`: `iconFor(name, {dir,
  open})` → an icon id, from a generated map (extension, whole file name,
  directory name). **New** `FileIcon.jsx`: an `<img>` of
  `/file-icons/<id>.svg`, falling back to `FileGlyph`
  (`shared/ui/Icons.jsx:528`) for an unknown name or a failed load.
- **New** `frontend/scripts/build-file-icons.mjs`, run by the build:
  reads the package (a dev dependency), writes the map and copies the
  SVGs into `frontend/public/file-icons/`. Nothing enters the JS bundle;
  an icon is fetched when a row first shows it and cached.
- **Used by** a work folder's files and directories only: `FileTree.jsx`
  (line 165 draws the row's icon), `FolderList.jsx`, quick open's Files
  group, a file's tab, search hits of `source: "file"`. A library paper
  keeps its `FileGlyph`: it is a reference, not a file.
- Tests: `frontend/tests/fileIcons.test.mjs` (the map's rules: a whole name beats
  an extension, the longest extension wins, a directory's name, the
  fallback). Docs: [dev/ui-design.md](../dev/ui-design.md),
  [dev/work_folders.md](../dev/work_folders.md).

### 1.2 The look

- **The root.** A work folder's card and row (`App.jsx:9520`,
  `:11890-11925`) and its row in the Files window carry the theme's root
  folder icon, the name of the computer that keeps it and the state of
  its sync, read from the mark the tree already carries
  (`explorer/workFolders.js` `markOf`).
- **The tree.** The folder view of a work folder (`FolderList`, swapped
  in at `App.jsx:11656`) becomes the Files window's tree with columns:
  one row component for both, indent guides, a chevron per directory,
  the columns it has now (format, size, modified, comments). The rows
  come from `fileTree.js` `treeRows`; `useFolderRows.js` goes when
  nothing else reads it.
- Tests: the `work-folders` browser steps, with the tree's rows and one
  icon asserted. Docs: [dev/work_folders.md](../dev/work_folders.md),
  [dev/home_library.md](../dev/home_library.md).

**As built** (1.1, 1.2, the word). *Superseded 2026-11-17 for the icons:
Gamma draws its own, a mark per kind on its file sheet or folder in its
stroke set, and the theme, its plugin and its virtual module are gone
([dev/ui-design.md](../dev/ui-design.md#file-icons) "File icons").* The icons were C5's: `material-icon-theme`
5.40.0 pinned as a dev dependency, `frontend/tools/fileIcons.mjs` emitting
every SVG into `dist/assets/file-icons/<version>/` with the theme's
`LICENSE`, the lowercased map as `virtual:file-icons` (a 27 KB Brotli lazy
chunk; `tools/bundleBudget.mjs` names a virtual module's chunk as Rollup
does), and `iconFor(map, path, {dir, open, root, light})` taking the path
from the synced folder's name down, so `parent/name` keys and the
language patterns work; twelve built-in languages, VS Code's thirteenth
(`*.log.?`) never matching. `FileIcon` draws `FileGlyph`'s classes, so every
place that sizes a kind glyph sizes it, and is drawn also in the `@` and
`[[` pickers and Linked from. The folder view is `FolderTree.jsx` over
`useFolderTree.js` (`useFolderRows.js`, `FolderList.jsx` and `listDirs`
gone); both trees share `TreeRow.jsx`, `useTree.js` (the keys and the
focus, out of `FileTree.jsx`) and `tree.css`, and `fileTree.js` builds
both trees' rows (`folderRows`, `openDirs`). `useDirectoryRows.js` takes a
sort and a `version`: the folder view reads in its own sort and again with
each listing, as the list did, the Files window by name and only on a
change. A directory press opens it in place; the folder view keeps no
`ListMotion`, as the window has none. The root's computer is a new read,
`GET /api/folders/computers` (`registry.of_folders`, the account's own
computers only), with this desktop app's own link first; neither the mark
nor the page's computers name a folder's keeper. The word: every
user-facing "work folder", and "synced vault" where it names a folder's,
is "synced folder" (同步文件夹) in the frontend, the zh catalog, the
server's refusals a person reads (`filing.WORDS`, `folder_links`,
`linked_files`), the desktop panel, launcher and notices, and the user
guide, whose sections are now "Synced folders and files" and "Working in a
synced folder"; a vault of one file keeps "synced vault". The chat's and
the local agents' strings (`chat/`, `local_agents/`, the agents' routes)
were left to step 2, whose rewrite of them says "synced folder" too.

### 1.3 Detach

- **Backend.** `folder_links.delete_link` (`folder_links.py:1906`) gains
  `pages: "remove" | "keep"`. `remove` is today's `_release_work`
  (`:2020`, `takeover.release(trash_files=True)`). `keep` stores the
  bytes of every tracked file first (`_store_in_place`, `:1811`, widened
  from the PDFs read in place to every format), then
  `takeover.release(trash_files=False)`, and drops only the directory
  folders that hold nothing. `removal()` (`:1685`) also answers the bytes
  `keep` would store. The route `DELETE /api/folder-links/{id}`
  (`routers/folder_links.py:903`) takes `pages`.
  `takeover.let_go` (`work_folders/takeover.py:258`) takes the same
  choice, for the server's side.
- **Every window.** The folder menu (`App.jsx:15239-15246`) shows one
  item, *Detach from {computer}…*, in place of *Let go of this work
  folder…*:
  - in the desktop app on its own server, the page calls the route;
  - in the desktop app on another server's page, the page asks the shell
    (a new ask `detach-folder` in `desktop/preload.js`, beside
    `keep-folder-on-disk`), which removes its vault as *Remove the
    vault…* does (`main.js:2571`);
  - in a browser, the server asks the computer over the channel (a new
    wake reason beside `sends` and `chat`, `computers/registry.py:333`),
    and the computer removes its vault; with the computer offline the
    server lets go itself and the computer forgets its vault at its next
    round (`LET_GO_THERE`, as now).
- **The dialog**, one component in `frontend/src/explorer/`: the counts
  and bytes from `removal`, the two choices, the sentence that the
  directory stays as it is. The desktop panel's *Remove the vault…*
  (`desktop/ui/bar.html:866`, `keeping.js:764`) takes the same words and
  the same choice.
- Tests: `test_work_folder_takeover.py` and `test_folder_links.py` (keep
  stores each format and trashes nothing; remove as before; a file
  already in the library either way), `test_remote_work_folders.py` (the
  wake, the offline path), `desktop/test` for the ask. Docs:
  [dev/work_folders.md](../dev/work_folders.md) "Removing a work folder",
  [dev/synced_vaults.md](../dev/synced_vaults.md),
  `desktop/docs/architecture.md`, the user guide.

**As built** (1.3). One path in every window instead of three: a page asks
its own server, `GET` and `POST /api/folders/{id}/detach`
(`gamma/work_folders/detach.py`), which removes this server's vault
(`delete_link` with `pages`) or lets the folder go in place
(`takeover.let_go` with the choice) and wakes the computers that announced
a vault of it (`let_go`, `registry.keepers`); there is no `detach-folder`
ask in the shell. The computer's channel runs a round of its work folders
of that server, which records `LET_GO_THERE`, and the shell's next reading
(at most 15 s) forgets the vault through the same `DELETE` as *Remove the
vault…* (`forgetDetached`), so the token minted for it is revoked and the
computer hidden there with the last vault, which the host alone could not
do. `POST …/let-go`, `files=trash` and its helpers, Let go's client
(`mayLetGo`, `letGoSaid`, `keptFolders`, `useVault`'s `kept`) are gone.
`removal()` now answers `files` as every file (not those leaving), with
`too_large`, and `bytes` as what keep stores; *remove* stores the PDFs read
in place as before, *keep* every format. Every removal also takes `.gamma/`
away once it is left empty. Who may: the link's maker alone where a vault
of this server keeps the folder (its directory is on that account's
computer); elsewhere the owner, or the account of a computer that
announced any folder vault of the root while it may edit the workspace
(the announce does not say which vault is the work folder's, and a
viewer's computer token announces what it likes); the computer is named
to that account alone, so the owner's row reads *Detach…*. The row
shows once the plan read as the menu opens answers; the panel's button
reads *Detach…* for a work folder.

Done when: a synced folder is told from a Gamma folder in the listing
and the Files window, a `.tex`, a `.py` and a `figures/` directory each
show their own icon, and Detach from a browser on another device leaves
the directory untouched with either choice.

## 2. Agents in Settings and the picker

### 2.1 The computer's settings

- **vaults.db** (additive; this database is never migrated): `computer_agents(agent
  PRIMARY KEY, enabled, runs, set_by, set_at)` and `agent_dirs(id
  PRIMARY KEY, path, name, added_at)`. At start, once, an agent on for
  any folder in `agent_folders` is on for the computer, and `runs` is
  `ask` where any row of `agent_policies` says so; the two old tables
  are then left unread. `agent_grants` keys by directory in place of
  `link_id`.
- **`local_agents/store.py`**: `agent_of(link_id)` and `agents_of` give
  way to `enabled_agents()`; `set_agent` and `set_runs` take the agent,
  not a link.
- **Routes.** `GET /api/local-agents` (`routers/local_agents.py:44`)
  adds `enabled`, `runs` and `dirs` to what it lists. **New** `PATCH
  /api/local-agents/{agent}` `{enabled?, runs?}` and `PUT
  /api/local-agents/dirs`, each needing the shell's word to turn on, to
  let ask or to name a path (`_from_shell` moves out of
  `routers/folder_links.py:155` to where both routers read it). `agent`
  and `agent_runs` leave `PATCH /api/folder-links/{id}` with
  `_agent_allowed` and `_runs_allowed` (`:639-693`).
- **The announce** (`computers/channel.py`) says the computer's agents
  once, not one per vault; `computers.vaults` loses its `agent` field
  and `computers.assistants` carries `enabled` and `runs`.
- Tests: `test_local_agents_routes.py`, `test_local_agents_runner.py`,
  `test_computers.py`, a new case for the carry-over.

**As built.** `runs` is `never` or `ask`, one setting per agent: `ask`
lets a turn of this server ask in the chat and one of another server ask
on this computer, so the vault's `computer` is gone, the https check moved
to each ask (`runner.ask_refusal`, as it was), the PATCH's `older` (that
server's whoami) and `remote_servers._unasked` went with it. The carry-over
is `store.carry_over`, run by `runner.recover` at start and doing nothing
once `computer_agents` has a row; it owns each agent by whoever turned it
on first and counts a vault's `computer` as `ask`; a vault's `ask`, which
let only its own chat ask, carries `never`, since `ask` now reaches every
server's turns and the person agrees to that through the desktop's
warning. The grants are a new table, `agent_dir_grants` (the conversation, the directory, the command,
its folder, the host, the agent): vaults.db adds no column, so
`agent_grants` joins `agent_folders` and `agent_policies` unread, its
grants not carried. An agent on is its owner's alone: another account gets
409 until the owner turns it off. Until the picker (2.4) the account's
agent is its first on in the catalog's order (`store.agent_for`): the chat
turns, the Sends and the mentions use it, a mention names it, the chat
strip shows it, and a vault's `agent` in the links and the page shows the
one at work there or the maker's; `runs` left the vault's `agent`.
`chats.vault_of` answers an account with an agent on, or the vault's
maker, who still reads their conversations with none on. `GET
/api/local-agents/waiting` (the panel's per-folder count) is gone: turning
an agent on hands over the Sends of every work folder kept here
(`runner.queue_waiting`). `_from_shell`, `_shell_strict` and `_account`
moved to `gamma/routers/shell.py` (`from_shell`, `shell_strict`,
`account`), which the folder-links, computer-links, remote-servers and
local-agents routers import. *Stop showing* turns no agent off (the
channel's delete did): the agents are the computer's, and showing the
computer again asks nothing of its own, so the question before an agent
is turned on names what a server this computer is shown on can do. The
registry no longer keeps a vault's `agent`: `registry._agent` reads the
first assistant `enabled` wherever a folder's agent was read, the page
route's `vault.agent` among them. The two sides ship apart, so the
announce keeps the old shape both ways: the app still puts its agent on
each work folder's vault for an older server, and `registry.normalise`
reads an announce whose assistants carry no `enabled` (an older app's) by
the agent its folder vaults name. Turning an agent off takes its owner or
the shell's word (`shell_strict`), so the person at the computer turns off
one whose owner is signed out of the app or gone, and an account deleted
turns its agents off (`runner.release_account`). The offered directories
are stored and routed; nothing reads them yet (2.5).

### 2.2 The desktop app

- `preload.js`: asks `set-agent`, `set-agent-runs`, `agent-dirs`.
  `main.js`: `setAgent` and `setAgentRuns` (`:2233`, `:2279`) lose their
  link and keep their native question and the shell's word.
- `ui/bar.html:616`: the vault's agent row goes; its place says where
  agents are set. `lib/keeping.js`: the agent's words move with it.
- Tests: `desktop/test`. Docs: `desktop/docs/architecture.md`.

**As built.** The three asks are page asks (`PAGE_ASKS`, as
`continue-in-terminal`): `set-agent {agent, enabled}`, `set-agent-runs
{agent, ask}` and `agent-dirs {dir}`, `dir` "new" to add a directory
picked with the native dialog or an offered one's id to move it; a page
of a remote server is refused. main.js `setAgent(serverId, name, on)`,
`setAgentRuns(serverId, name, runs)` and `agentDirs(serverId, dirId)` act
with the shell's own session, read `GET /api/local-agents` first (an
agent so already is left as it is) and keep their questions, now the
computer's (`keeping.agentAsk(name)`, `keeping.runsAsk(name)`, Cancel the
default since it covers the asks of another server too). The panel's
`agent`, `agent-runs` and `check-agents` actions went, and with them
`agentChoices`, `farAgentWords`, `runsComputerAsk`, `olderServer`,
`plainRule`, `FAR_RUNS_RULE`, `NEAR_COMPUTER_RULE` and the item's `agent`,
`agentReady`, `agentProblem`, `agentRuns`, `agents`, `agentHere` and
`secure`; the busy row keeps `agentLabel`, `agentRunning` and
`agentChat`. The asks are followed while the host has an agent that may
ask and keeps a work folder of another server; the card's *Stop Codex
asking* sets that agent's `runs` back to `never`. The Feedback dialog's
offer line names Settings → AI in place of the sync panel (with its
`zh`).

### 2.3 Settings → AI → Agents

- **New** `frontend/src/settings/SettingsAgents.jsx`, a section of
  `SettingsAi.jsx` above *Connections* (`:780`): Gamma AI and the
  connection it uses; then each computer, with each agent found, its
  version, whether it is signed in, its switch, what it may run, and the
  directories offered. In the desktop app the controls ask the shell; in
  any other window they are read-only and say *Change this in Gamma on
  {computer}*, but for turning an agent off.
- Docs: [dev/settings.md](../dev/settings.md),
  [dev/local_agents.md](../dev/local_agents.md) "Who turns it on".

**As built.** The section is *Agents*, first in Settings → AI →
Connections: Gamma AI with the connection in use, then *This computer:
TIM-PC* where `GET /api/local-agents` says `here`, then one read-only
section per computer of the account's shown to this server, from a new
`GET /api/computers` (`registry.of_account`, the page route's rows without
a vault; one of the pane's reads). The listing now answers on every server,
`{here: false, …}` where agents do not run, so every window reads it with
no 404, and gains `computer` (this computer's name) and `code`
(`catalog.code`, moved out of the channel), which the client words. What an
agent may run is a `Segmented`, *Only in its sandbox* or *Asks before
more*; a directory is renamed in place (`NameInput`), moved with the
shell's dialog and removed with no shell's word. The shell answers a page
nothing, so after an ask the pane reads the listing every second for a
minute until it changed. Outside the desktop app the owner's switch turns
an agent off with the session; nothing else is editable there.

### 2.4 The picker

- **New** `frontend/src/chat/agents.js`: one list for the chat, the
  comment field and Settings, `[{id, kind, label, where, models, ready,
  problem}]`, from the AI info, `GET /api/local-agents` and the page's
  computers. `chatComputer` and `commentAgent` (`App.jsx:4905-4911`),
  `computerAgent`, `mentionAgent` and `keeperComputer`
  (`vault/vaultState.js:110-135`, `:262`) are read through it.
- The model chip (`ChatDock.jsx:2605`) lists agents, each with its
  models and efforts. `ChatSides` and the `onComputer` branch
  (`ChatDock.jsx:1894-1908`) go; `chat/computer/chatSide.js` becomes the
  bucket's chosen agent.
- Picking another agent in a conversation with messages asks *Start a
  new chat with {agent}?* (decision 10).

**As built.** An agent's `id` is its name (`gamma`, `codex`, `claude`):
the server binds a page that another computer keeps to that computer's
agent, so a page lists either this computer's agents or that one, never
both, and the name is what the routes take. `chat/agents.js` also carries
`owner`, `code`, `version`, `enabled`, `runs` and, for another computer's,
`remote`, `online`, `since`; `chatAgents`, `pickAgent`, `keeperAgent`,
`mentionAgent` and `stripFacts` replace vaultState's `mentionAgent`,
`computerAgent`, `keeperAgent` and `keeperComputer`, and App's
`chatComputer` and `chatComputerMark` (with `computerChat.sideMark` and
`Segmented`'s `trailing`, which only the switch used). The listing is one
store per window, `chat/useLocalAgents.js`, read by the chat, the comments
and Settings. The chip is `chat/AgentChip.jsx`, one `MenuSelect` both
sides draw: the agents first, then the picked one's models and efforts.
The server took the pick: a turn names `agent`, the read takes `?agent=`
and shows no conversation of another agent, `chats.begin` archives one of
another agent as it does one of another directory, and the terminal and
New chat name the conversation's own agent. On yes, *Start a new chat*
starts the picked agent's new conversation (Gamma AI's New chat, or `POST
/chat/new`) and leaves the other where it is. A bucket never picked shows
Gamma AI, or a computer's agent when no AI service is connected (a step
toward 2.8). The strip shows the directory after its line, a menu of the
offered ones and *No folder* until the first turn (`computerChat.workPlace`),
and *Show in folder* only on a page of a work folder kept here. A
Background tasks row picks its turn's agent and opens a folder's or the
library's chat as well as a page's. The @ menu lists the agent a mention
reaches, the account's first here (`store.agent_for`), not every agent it
owns: a Send records no agent until a comment sent to an agent is a turn
(3.5).

### 2.5 An agent on any page

- **`computer_chats`** (`db.py:987`): `page_id` becomes `bucket` (a
  page's id, a folder's, or `home`, as `chats.bucket`), and it gains
  `dir` (a vault's id, an offered directory's id, or none) and
  `thread_id` (step 3.5). The table is renamed `agent_chats`, and
  `computer_chat_runs` `agent_turns_far` (the name `agent_turns` is
  vaults.db's); `ws_backup.PRIVATE_TABLES` follows.
- **`local_agents/chats.py`**: the gate `vault_of` (`:60`) becomes
  `place_of(ws, bucket, user)` → the directory of the vault the page is
  a file of, else the offered directory chosen, else an empty directory
  below the data directory, made for the conversation (decision 14).
- **`runner.py`**: the queue is keyed by directory in place of the link
  (`_work`, `:788`; `queue_chat`, `:288`), so one run at a time per
  directory. `_ChatTurn` (`:1465`) takes its `cwd` from `place_of`.
- The routes keep their paths; `page` becomes `bucket`. A page of
  another server keeps today's binding to its work folder until step 4.
- Tests: `test_local_agents_chat.py`, `test_computer_chats.py`,
  `test_remote_chat.py`.

**As built.** The tables keep their names, `computer_chats` and
`computer_chat_runs`: they still hold a computer agent's conversations and
their far turns, and `thread_id` waits for 3.5. Step 59
(`computer_chat_buckets`, in `LOCAL_STEPS`) renames `page_id` to `bucket`
in both and gives `computer_chats` a `dir`. A conversation in a work
folder names the work folder's id W, not the vault's link id: the step
fills it from pages.db alone (the page's `work_folder`), a far
conversation names the same W, and `chats.place_of` finds the vault that
keeps W from the bucket itself, so `dir` only tells whether the
conversation still works where it began. `place_of(ws, bucket, user, dir,
conversation_id)` → `{kind: vault | offered | own, dir, path, name, link,
about}`, `about` what the bucket is (`chats.about`), and it is the one
gate: an account with an agent on, for any bucket; a vault's maker with
none, for its work folder alone. The own directory is
`<data>/agents/<ws>/<conversation id>/` (`config.AGENTS_DIR`), made by the
turn and removed by `chats.delete`, and at start by `chats.sweep_dirs` for
conversations gone otherwise. A turn whose place is another than the
active conversation's (another `dir` named, the bucket moved into or out
of a work folder kept here, an offered directory withdrawn) archives it
and starts a new one inside `chats.begin`. The queues are keyed by the
directory's path as the file system compares it (`runner._place`), and
`_queued` remembers each entry's vault; `runner.stop_dirs`, from `PUT
/dirs`, stops a turn in a directory no longer offered, and one that waits
there fails when taken. The strip gains `dir` (a vault's name, its path for
the vault's maker; an offered directory's id and name; null for its own);
`folder`, `folder_id` and `paused` stay the work folder's, empty outside
one. The prompt's JSON names `about` in place of `file`, and `.gamma/` and
the build line only where they apply. One directory has one queue, so an
offered directory never overlaps a vault or another offered one: `PUT
/dirs` refuses a new path inside or around either, `check_vault` a vault
inside or around an offered directory, and a turn whose place's path is no
longer its queue's (an offered directory moved while it waited) fails when
it is taken. `PUT /dirs` takes an account with an agent on here, else the
shell's word. A deleted folder's conversations go
to the library's History (`chats.file_into_home`, in the tree delete's
transaction). Background tasks' rows carry `params.bucket`. The channel
still names a far turn's bucket `page_id`, a page's always, so a computer of
an older Gamma keeps working. The client still opens the side on a page of
a work folder only: 2.4 and 2.6 open it elsewhere.

### 2.6 One transcript

- A computer agent's messages are drawn by the chat's own transcript.
  `chat/computer/computerChat.js` keeps the reading of a turn (the poll,
  the numbered writes) and gains the mapping onto the chat's message
  shape: text, steps (`chat/agentSteps.js`, `chipText.js`), asks
  (`ApprovalCard`), changes (`AgentChanges`). `ComputerChat.jsx`,
  `ComputerAsk.jsx` and `computer.css` go; the strip (`ComputerHeader`)
  stays as the health strip of such a conversation.
- History lists both tables by time, each row with its agent's icon.
  The export (`chat_export.py`) and `read_chats` read the person's own
  agent conversations.
- This is the unit most likely to grow: `ChatDock.jsx` is past 2,600
  lines. It is built as an adapter, the agent conversation presented in
  the shape the transcript already draws, not as a second set of
  branches.
- Tests: the chat's unit tests and the `chat navigation` browser steps,
  with a fake agent (`backend/tests/fake_agents`). Docs:
  [dev/ai.md](../dev/ai.md), [dev/chat_history.md](../dev/chat_history.md),
  [dev/local_agents.md](../dev/local_agents.md) "The chat's computer side".

**As built.** The transcript moved first, out of `ChatDock.jsx` into
`chat/ChatTranscript.jsx` (the rows with their pieces: `AgentSteps`,
`ContextCoverage`, `ReplyErrorCard`, `UsageLine`, the tool chips' detail),
and both draw through it; ChatDock is 2,466 lines. The adapter is
`computerChat.shownMessages`: a computer agent's steps become plain chips
of kinds of the chat's own (`stepChip`: `file`, `fileEdit`, `run`,
`search`, `work`, in `ACTION_ICONS`, none a change, so Gamma AI's changes
list and revert pass them by), the pill's words come with the message
(`pill`, `turnLabel`), its asks are `AskCard` (`ApprovalCard.jsx`, on
`ApprovalShell`), what it changed `FileChanges` (`AgentChanges.jsx`,
"Changed in its directory" outside a work folder), a stopped turn ends
"(stopped)" as a stopped Gamma AI reply does, and a failed one is the error
card of a new kind, `agent`, headed by the computer's own sentence
(`errorSaid`). A row that had ended when it was first drawn is still, for
both agents, in place of the old side's per-message state. What a row
offers comes from the handlers ChatDock passes (edit, report and revert
are Gamma AI's; Save to library reads either's text). `useComputerSide`
holds what `ComputerChat.jsx` held, the strip is `ComputerStrip.jsx`, the
composer is ChatDock's with what such an agent takes alone (the message
and the PDF passages: `PaperMentionInput`, now `ChatComposerInput`, gains `mentions` and `label`),
and `computer.css`'s rules are the chat's own in `app.css`;
`ComputerChat.jsx`, `ComputerAsk.jsx` and `computer.css` are gone. History
is one popover (`computerChat.historyRows`): `GET /chat/history` also
answers the bucket's `active` conversation, whichever its agent, so every
agent's current conversation is listed while another agent is shown, and
a row's icon is its agent's (`AgentChip.agentIcon`) while the chat has
several; rename stays Gamma AI's. The export is a route of its own, `GET
/api/local-agents/chat/history/{id}/export` (its owner, active or
archived; `chat_export.transcript` with the agent's label words the steps
in English, `step_text`). History's list, export and delete ask no seat:
a conversation stays its account's to keep or delete with its agent
turned off, and History reads it on every server; opening one takes the
seat, as a turn does. `read_chats` is left as it is: it reads the
workspace's chats, and a computer agent's conversations stay their
account's, read by no tool.

### 2.7 Gamma's tools for the agent

- **The token of a turn.** `integrations.py`: a token minted when a turn
  starts and deleted when it ends, in `integration_tokens`, its name the
  agent's label, its workspace the turn's, its life the turn's timeout.
  In this step it reads (the `READ_TOOLS` of `mcp_server.py:61`); step
  3.3 adds the *Comment* level.
- **The drivers.** Codex: `_quiet` (`codex.py:116`) turns off every MCP
  server but Gamma's, added to the thread's `config` (C1). Claude Code:
  `--mcp-config` beside `--strict-mcp-config`, and Gamma's tools among
  the allowed ones (C2). The token reaches the process in an environment
  variable of that one child (`procs.child_env`), never in its command
  line.
- **The prompt.** `prompt.build_chat` (`prompt.py:207`) names the page
  (its id, title, format, path), says its notes, highlights and comments
  are read with the tools, and no longer pastes earlier context it can
  read. The selected passages stay as they are.
- Tests: `test_local_agents_drivers.py` (the flags, the config), a fake
  agent that calls a tool with the token it was handed and is refused
  after the turn ends. Docs: [dev/mcp.md](../dev/mcp.md),
  [dev/local_agents.md](../dev/local_agents.md) "Trust".

**As built** (2.7, the token and the drivers). The token is a scope of its
own, `turn` (`integrations.mint_turn`, `renew_turn`, `drop`, `drop_turns`):
the turn owner's, of the turn's workspace alone, named with the agent's
label, living `RUN_TIMEOUT_S` and `TOKEN_SLACK_S` (60 s); it is no
connection (Settings' list, the twenty, the PATCH and the DELETE pass it
by) and every `/api/` route answers it 403 `auth.TURN_ONLY`.
`runner._Turning` mints it at the turn's first start (a chat turn's and a
Send's of this server), renews it at every start of the agent to that
start's deadline when it would expire sooner, and while an ask moves the
deadline (at most once a minute, 60 s past the deadline; a renewal that
failed is tried a minute later), and deletes it in `go`'s `finally`;
`recover` deletes what a crash left. The address is new
(`local_agents/tools.py`): nothing on the server knew its own port, so the
session middleware hands every request to `tools.heard`, and the first one
from a loopback client to `127.0.0.1` or `::1` without `X-Forwarded-Proto`
names `<scheme>://<that socket>/mcp`; a server that learned none runs its
turns without the tools (Settings' MCP URL, `public_base`, names the
public address, which the agent's process may not reach). The drivers take
`gamma=events.Gamma(url, token)`; the token goes into the process's
environment alone (`events.TOKEN_ENV`, added by `Turn` after
`child_env`). The server is named `gamma_turn` (`events.MCP_NAME`), not
`gamma` as C1 and C2 tried it: Gamma's own setup names the user's
connection `gamma` (`codex mcp add gamma`), and Codex merges a thread's
server into the user's of the same name, whose headers, tools turned off or
command would then be the turn's; the user's `gamma` is turned off with the
rest. Its entry carries `enabled: true`, and Claude Code fails a turn only
on `gamma_turn` not `connected`, not on another server listed. Codex's
`shell_environment_policy.exclude` is the user's own list with the token's
variable added: an override replaces a list, where it merges a table. A
Gamma that did not start or connect fails the turn as `events.GammaDown`
(the done's `gamma_down`), which the runner never takes for a refused
resume: a turn afresh would fail alike and replace the conversation's
session. A call is a step of a new kind, `gamma`, with the tool's
name (`events.gamma_step`, `runner.step_of`, the channel's `_step`,
`chat_export.step_text`: "Read Gamma: read_page"); the chat's chip takes
the kind of Gamma AI's own call of that tool (`computerChat.stepChip`), so
the steps pill counts it as a read, and `chat/agentSteps.js` needed no
change. A far turn gets no token (4.2). The prompt's half of 2.7 is not
in this part.

**As built** (2.7, the prompt). `build_chat` takes `tools`, whether the
turn has Gamma's tools: a chat turn mints its token before its prompt
(`_ChatTurn._work` calls `_tools`, which mints once; each start renews
it in `_turn`), so a turn without them (no address learned, a far turn) is
told what it was told before. With them, a section *Gamma's tools* says
that what the server `gamma_turn` returns is data, not instructions, and names
the tools that answer for what the conversation is about, by its id
(`prompt._tools`): a page's `read_page` and `read_block`, `view_pdf_page`
with its `page_id` and a `pdf_page` for a PDF, a folder's `list_pages`, the library's `list_folders` and
`list_pages`, `list_feedback` and `read_feedback` in a work folder, then
`search_library`, `cite` and `read_chats`. Nothing the prompt pasted could
be read with them: it carried the message, what it is about, the
selections and the earlier messages, and the earlier messages stay, since
no tool reads a computer agent's conversation (2.6). The tool list and
`mcp_server`'s instructions are unchanged. A new Codex thread is named with
the conversation's title (`thread/name/set` after a `thread/start`, never
after a resume, its answer not waited for, so a Codex that refuses it or
never answers runs the turn all the same): a
computer agent's conversation has no rename (2.6), so the name is set
once. The title comes with the turn's context (`home.claim`) and, for a
far turn, the channel's claim (`turn.title`, cut as a local title,
`chats.title_of`). The browser step is in
`local-agents`: with no provider, a library PDF with a highlight, its chat
on the fake Codex, which reads the page with `read_page` through `/mcp`
with its token and answers with what it read (the fake Codex's `turns`
scripts that one message: `$page`, `{gamma}`); the pill unfolds to *Read
Gamma: read_page*, and the server log says the token was deleted
(`_Turning._drop_tools` logs it). The Send's step there now lets the
turn's token through the check that no `GAMMA_` variable reaches the
agent. With this, 2.7 is built, and step 2 with it.

### 2.8 A chat without a provider

- `ChatSetupCard` (`ChatDock.jsx:408`) lists the agents found beside
  *Connect an AI service*; `aiOff` no longer closes a chat whose agent
  is a computer's.
- Docs: [dev/onboarding.md](../dev/onboarding.md), the user guide.

**As built.** The setup card takes the chat's computer agents (a tile that
picks one) and the agents `GET /api/local-agents` found here but not
turned on (a tile that opens Settings → AI): App hands ChatDock the
listing (`localAgents`) in place of its directories alone. `aiOff` closes
only Gamma AI's side (`closed`): with a computer's agent picked the
composer, the header's Export, Find, History and New chat, and the
transcript stay. The browser step is in `local-agents`: with no provider a
library PDF's chat is Codex's, Gamma AI picked shows the card and its tile
picks Codex again, and the reply lands in a directory offered (the fake
Codex edits a file, which a directory of its own lacks). Reading the
page's highlights through Gamma's tools waits for 2.7.

Done when: with no provider configured, in the desktop app, a library
PDF's chat is opened, Codex is picked, it reads the page's highlights
through Gamma's tools and answers; the same on a file of a synced
folder edits that file; the sync panel no longer names an agent.

## 3. Marks, the agent's hand, and threads

### 3.1 The reader, for any page

- `backend/gamma/feedback/` is renamed `annotations/` (and its tests),
  with the source comments that cite the doc.
- `entries.resolve_scope` (`entries.py:92`) accepts a library page, a
  library folder and `home`; `Scope` gains those kinds, and a library
  page's "path" is its title. `comments.threads_in_work_folder`
  (`comments.py:94`) becomes the threads of a scope.
- An entry is a thread or a **highlight without one**: its file, place,
  quote, source line, picture and note. `state` filters threads only.
- The tools in `ai_tools/` become
  `list_annotations` and `read_annotation`; `mcp_server.py`'s `SPREAD`
  and thread arguments follow. `ai_context.py`'s highlights section
  gives a PDF passage its page number, which it lacks today.
- Tests: `test_feedback_entries.py`, `_anchors`, `_sources`, `_kept`,
  `_pictures`, `_images`, `_tools` under their new names, with a library
  page and a bare highlight added.

**As built** (3.1). The package is `gamma/annotations/`, its tests
`test_annotations_*.py` and its doc [dev/annotations.md](../dev/annotations.md),
whose request sections say they go in 3.7; `FeedbackError` is
`AnnotationError`. The reader has a router of its own,
`routers/annotations.py`: `GET /api/annotations`, `GET
/api/annotations/{id}` (a thread's id or a bare highlight's block id) and
`…/{id}/picture` in place of `/api/feedback`, `/threads/{id}` and its
picture; `routers/feedback.py` keeps the Sends' routes, and its
`dialog_agent` is what the reader's route adds for a work folder's scope
(the Feedback dialog's `agent`, `agents_found`), the dialog listing the
document's threads alone (`feedbackView.threadsOf`). `resolve_scope`
answers six kinds (`page`, `library_folder`, `home` added) and refuses
none; `work_scope` refuses what is no work folder's for the callers that
need one (a Send, the files' writer, the far turn's claim, the chat strip,
a mention's Send). `comments.threads_in_work_folder` is gone: the reader
names a scope's pages (`_page_ids`: the work folder's column,
`pages_in_folder`, `live_pages`) and reads them with `threads_for`. An
entry gains `type` (`thread` or `highlight`); a bare highlight is a
highlight block (the `kind` column) that no thread of any state is placed
on, read as a root without an anchor is, its own text as `where.note`,
`state` and `sent` null, no comments, no `before`; `state` filters threads
alone. The document's format is `gamma-annotations-1`, its `work_folder`
null outside one, `updated_at` a highlight's write too; past
`MAX_ENTRIES` the threads fill the cap first, so a file of many
highlights never pushes another's threads out, and `truncated_threads`
counts the threads cut (the dialog's *n more are not shown*). Outside a
work folder `answer.mcp` and the Markdown name no answering tool, which
reach a work folder's threads alone until 3.3. The Markdown is
headed *Annotations on …* and counts threads and highlights apart. The
tools are `list_annotations` (`scope` a page's or a folder's id, a work
folder by name, a folder's path, or `home`; without one the chat's own
page or folder, so a folder chat reads one document, no longer a section
per work folder) and `read_annotation(id)`; the old names stay callable
through `DEPRECATED_TOOLS`, `thread_id` too. MCP finds an annotation's
workspace by `ANNOTATION_ARGS` (the list's `after`, the read's `id`, as a
block and as a thread). The turn's prompt names `list_annotations` with the
page's, the folder's or `home` for every conversation. data.db's table is
`annotation_pictures`; `DATA_SCHEMA` drops `feedback_pictures`, a cache
drawn again when asked for. The model-facing text keeps "work folder", as
the registry's other tools do; the chat's step labels are the person's
(*Listing the highlights and comments…*, with their `zh`).

### 3.2 On disk

- `annotations/files.py`: `.gamma/annotations/open.md`, `open.json` and
  `img/`, written by the vault's rounds as `.gamma/feedback/` is; the old
  directory is removed at the first round (`files.remove`).
- For a vault of another server, the round reads the entries and the
  pictures from that server with the vault's token and writes the same
  files (today `files.py:138` writes none there).
- Tests: `test_feedback_files.py` renamed, `test_remote_work_folders.py`.

**As built** (3.2). A work folder's files are below `.gamma/annotations/`
(the Sends' `requests/` with them until 3.7), and a vault of one file's,
which the owner asked for after the plan, beside its file in
`.gamma/annotations/<file name>/` (`files.place`), the same shape; both
list the open threads and the bare highlights, and the Markdown's opening
lines say what a highlight is to the reader. `.gamma/feedback/` is
`files.FORMER`, deleted by every write and every removal while it is
there. One test, `folder_links.annotated`, says who writes: a work folder
of this server or another (`work_vault`, no longer `work_folder_link`), or
a vault of one file that no folder vault's directory holds (one
`_take_in` could not take, whose `.gamma/` may be that vault's). A vault
of one file writes at its round (`_file_round`) and in the hot pass, whose
marks now resolve to a work folder or to a followed page
(`files._scopes`, `_annotations_pass`); its files go at its removal, a
relink (beside the file that was) and a take-in, `.gamma/` with them when
left empty. The `.gamma/` beside a file holds no marker, and
`place_refusal` now refuses a vault of a `.gamma` directory or one inside
it (`OWN_REFUSED`). `vault.disk.write_own` takes `beside`, the followed
file standing for the marker, and hides the `.gamma/` it makes. A vault of
another server reads its document there (`GET /api/annotations` of its
folder or page, cut to the document's fields) and fetches the pictures by
the routes it names, with the vault's token: an integration token of that
workspace already reads both as any member, so the token gained nothing.
Such a work folder writes at every whole round and, on a round of push
alone, at most every `REMOTE_POLL_S` (`_annotations_read`), its document
asked for within `ASK_TIMEOUT_S`, so a silent server holds the lock no
longer than a question; a followed file of another server has a round every
`REMOTE_POLL_S` while its file rests (`folder_links._poll`), which sends
nothing and reads its annotations again, since no mark reaches this
computer from there. The status's summary is `annotations`, in place of
`feedback`. A remote run's pictures in `img/` are now pruned by the work
folder's rounds as a local one's are, but not while an agent works there
(`runner.at_work`). A vault of one file writes nothing in a directory no
vault may have (`place_refusal`: a drive's root, home, a system or
temporary place), and nothing where that directory's `.gamma/` holds a
marker (a vault this server does not know); a take-in removes the file
vault's files under its lock. The browser step is in
`work-folders`: a highlight made in `main.pdf` reaches `open.md` on disk.

### 3.3 The writing tools and the Comment level

- **Tools**, in the registry the chat and MCP share: `comment` (`body`,
  and either `thread_id` for a reply or a place: a quote with its PDF
  page, a range of lines, a note's block, or nothing for the page's own
  thread), `resolve` (`thread_id`), `highlight` (a place, an optional
  note and style).
- **Placing.** A PDF quote is found with
  `highlight_relocate.find_passage` (`:818`) and turned into rectangles
  (`rects_for_span`, `:936`); a range of lines with
  `documents/text_anchors.find_range` (`:523`). A quote not found is
  refused with the closest match, never placed by guess.
- **The hand.** `feedback/agent.py` becomes `annotations/hand.py`:
  `_thread` (`:61`) loses its work-folder check, and `reply` and
  `resolve` are the two tools' replies and resolves.
- **The level.** `integration_tokens` gains `comments`, shaped as
  `edits` is (`integrations.py` `edit_grant`); a token of the scope
  `feedback` becomes a `read` token that comments in its workspaces.
  `mcp_server.py:532-566` offers and gates the three tools by it, in
  place of `ANSWERING_TOOLS` and the `feedback` scope.
  `auth.require_commenter` (`auth.py:873`) follows. The OAuth scope
  `gamma:feedback` (`mcp_oauth.py:31`) is kept as a name for the same
  level, so a connection made before goes on working.
- **Gamma AI** gets the three tools under one permission of the chat,
  *Comments and highlights* (Allow, Ask, Off), beside those it has.
- **The interface.** The share-style control of a connection gains the
  level between the eye and the pen (`settings/SettingsIntegrations.jsx:274`,
  `auth/McpConsent.jsx:155`). The plugin's skill (`plugins/gamma`) is
  rewritten around the new tools.
- Tests: a new `test_annotation_tools.py` (each place, a refused quote,
  the level's gate, a repeated id), `test_mcp.py`, `test_mcp_oauth.py`.
  Docs: [dev/mcp.md](../dev/mcp.md), [dev/ai_tools.md](../dev/ai_tools.md),
  [dev/comments.md](../dev/comments.md).

**As built** (3.3). The three tools are the registry's
(`ai_tools.WRITING_TOOLS`, permission `comment`, both scopes), marked
`commenting`: they are armed where the scope may comment (`can_comment`,
`auth.can_comment`, a viewer's chat too), not only where it edits, and
`run_agent_tool` refuses them without it. The hand is `annotations/hand.py`
with a `Writer` (the account, its username, the `Agent`) in place of a
`Connection`, which the Send's `answer_run` and the local home write with
too; the places are a module of their own, `annotations/places.py`, and the
PDF's finder is `highlight_relocate.place_quote` (`find_passage` over the
named page and one either side, `rects_for_span` in the display frame). Only
an exact copy, as the finders fold text, places a quote: a rough match, even
past `ROUGH_APPLY`, is the closest passage and refused, since the agent
copied the text and anything less is a guess; a secrets file's text is never
said back, and a quote on one is refused before any match (its lines are
named whole), so the answer is no oracle for its text. A new highlight is a
block, an edit: the hand makes one only where the account edits
(`hand.may_mark`), so a viewer's agent (its chat, a token at the Comment
level, a turn) comments, replies and resolves, and its `highlight` or
`comment` on a passage or lines is refused, before the card asks. A call
made again (the same agent's same words for the same account on the same
place within `hand.REPEAT_S`, 300 s) is refused naming what the first
wrote, and a place keeps one highlight of each agent per account (the
stamp's `for`: Gamma AI's id is every account's); the tools take no `id` (the old
`reply_feedback`'s), and a Send keeps its minted ids through `comment_id`.
A thread may be on any page of the library, a library page's as a file's.
Each answers with the entry's link as the reader gives it
(`entries.link_of`, `&thread=` for a thread). The level is
`integration_tokens.comments` (users.db global step 61,
`integration_comments`), shaped as `edits` by `comment_grant`; `editing()`
became `named()`, read for both. The edit level comments as well, a `write`
token comments in its workspace and a turn's token in its own
(`integrations.comments_in`). The HTTP API takes a commenting token's
comments in its own workspace (`Grant.comments`, `auth.token_comments`):
every comment write a person may make, a new thread, its own edits and
deletes and a reopen too, so `require_commenter` lost its `answer`; the
Send's state route reads the same. `gamma:feedback` stays as
`mcp_oauth.COMMENT_SCOPE`: a client asking for it finds the open workspace
at Comment on the consent page (`comments_asked`), and the token response
names it when the token comments anywhere, at the Comment or the Edit level.
A chat driven by a token signs as its connection (`agents.of_request`), not
as Gamma AI. MCP's `_change` became `_writes`,
shared by the note tools and the writing tools, which never ask first.
Gamma AI's card words the place (`approvals.placeText`), its steps and
changes have their sentences, a highlight is one of `BLOCK_TOOLS`, and
`ai_agent.STEP_ARGS` gains `thread_id`; a computer agent's call of one is
worded "Wrote in Gamma: comment" (`events.gamma_words`, `computerChat`'s
`stepText`), its chip of the kind `write`, listed under no reply's changes.
The picker's levels are View /
Comment / Edit (the speech bubble between the eye and the pen), a viewer's
workspace offering View and Comment in place of its *view only* tag (an
Edit stored there before the account lost its role is held as Comment,
`settings/workspaceAccess.js` `accessOf`, so a save sends what it shows); the
Tokens pane's scope is Read or Read and write, a read token taking the
levels, and the consent page's feedback switch is gone. The new tests are
`test_annotations_hand.py`, named so beside the reader's
`test_annotations_tools.py`. Browser steps: a token made at Comment
(`settings`), a client asking for `gamma:feedback` starting at Comment and
its comment landing under its name (`mcp`), an assistant's comment on a PDF
quote highlighting the passage live and a quote not there refused with the
closest, Gamma AI's the same from a PDF's chat once its card is allowed (a
stand-in model on the OpenAI wire), its highlight's tooltip and the thread
naming it (`comments`), the fake Codex commenting through its turn's token
as *Codex on …, for* its person (`localAgents`), and the Send script's token
at Comment (`feedback`).

### 3.4 Identity

- **New** `backend/gamma/agents.py`: an agent's id (`gamma`,
  `computer:<computer>:<name>`, a connection's token id), its label and
  its kind. No table: the agents of an account are Gamma AI, the enabled
  agents of its computers and its commenting connections.
- A comment keeps `agent` and `agent_id` (`db.py:1064`). A block an
  agent makes carries `properties.agent = {id, name, for}`. The author
  stays the account, so every role check is as it is.
- The interface: an agent's avatar by its kind, its name, and *for
  {person}* in the byline (`comments.js:230-246`, `Thread.jsx:97`); a
  highlight's popover and its row in Notes say who made it.
- Tests: `test_comments.py`, the comment components' unit tests.

**As built** (3.4). `gamma/agents.py` holds `Agent(id, kind, name, on)`,
`on` a computer agent's computer by its name and `label` "Codex on
TIM-PC". This computer is in no registry of its own server, so its id is
`here_id()`, an HMAC of the install's id as its id for another server is;
a channel's computer is its `computers.computer` (`of_channel`), and a
token's agent is its connection's (`of_token`, `of_request`,
`of_connection`), a turn's this computer's agent (`here`). A turn's token is now named with its agent's id
(`tools.mint` takes the agent, not its label), so what it writes through
MCP once 3.3 lets it is that agent's. `read_all` names a computer's agent
by its computer as called now: this computer by its own name where agents
run here, another by the name it announced for the comment's author
(`registry.names_of` by account and computer; `read_all` is keyed by
account and agent id), since a computer's id is what its channel says and
another account could announce the same one. A comment's `agent` keeps the agent's name
("Codex"), not the label; the reader adds `agent_kind` (`gamma`,
`computer`, `connection`) to every agent's comment, where only a local
one's had it, and `agent_on`, so the client words "on TIM-PC" itself;
`resolved_agent` and the write's event (so the Inbox's item) carry the
label. The writers take an `Agent` (`comments.create`, `edit`, `delete`,
`resolve`; `annotations.agent.reply`, `resolve`, `answer_run`) in place of
the name and id strings, and `comments.LOCAL_AGENT` is gone. Step 60
(`comment_agent_ids`) knows an old comment's computer from pages.db alone:
a `feedback_request_runs` row of a Send naming its thread is another
computer's run (its token's `computers` row), a Send with none was run here
(`here_id`, where agents run on this server), and vaults.db's `agent_runs`
is not read; a Send swept or a token gone leaves `computer::<name>`, which
reads as the agent alone. The account's agents (`of_account`) count a
`write` token beside the `feedback` one (`COMMENTING`: a write token
comments as it writes notes); nothing calls it yet, 3.5's tick will. The
block's stamp is `{id, name, on?, for}`: `on` beyond the plan's three
keys, for the same reason as a comment's `agent_on`, and stamped by Gamma
AI's `create_block` (the chat's scope, `routers/ai.py`) and a connection's
through MCP (`mcp_server._change`); `clip_region` makes no block. The key
is the server's: a client's op batch cannot set it (`ops.client_ops` at
`POST /pages/{id}/ops`), only the mirror's. The op
log, a mirror, a Gamma export and a backup keep it with the block, a
vault's file holds no property, and a Duplicate drops it: the copy is the
copier's. The interface: `AgentAvatar` (`Presence.jsx`, by
`AgentChip.agentKindIcon`) in a thread and the Comments window's rows,
the name followed by *on TIM-PC, for tim* (`comments.agentFor`), the
title "Written by Codex on TIM-PC, for tim" (a connection's keeps "with
tim's connection"), the Inbox's "Codex on TIM-PC, for tim". A highlight has
no popover of its own: its tooltip on the PDF, its row in Notes (under the
passage) and the Properties popover (*Made by*) say who made it
(`blockModel.blockAgent`, `agents.stampLabel`).

### 3.5 A comment sent to an agent

- **The request.** `POST /api/pages/{id}/comments` and the edit take
  `to`, an agent's id. `_written` (`routers/comments.py:87`) starts a
  turn where `mention.send` made a Send. `local_agents/mention.py`
  shrinks to reading `@name` as `to` for a client that sent none.
- **The turn.** A conversation of the thread (`agent_chats.thread_id`),
  its agent's session resumed as `agent_threads` resumed one;
  `prompt.py` gains the thread's prompt: the entry (place, quote, source
  line, picture, the comments with their authors as data) and the
  instruction. At its end, a reply in the thread is checked for and the
  last message written there when there is none.
- **The field.** `CommentComposer.jsx`: the tick, its menu, the
  placeholder (*Ask {agent} to edit* when ticked). `Thread.jsx:155`: the
  chip reads the turn's state in place of `sentChip`; *Open in chat* on
  an agent's reply. `comments.js`: `sentChip`, `mentionHint` and the
  refusal words are rewritten for a turn. The `@` menu's agent rows
  (`editor/mentions.js:122`) come from `chat/agents.js`.
- **The agent's question.** The prompts of a chat turn and a thread turn
  tell the agent to ask by `comment` on the place and then stop. The
  reply's card lists the threads it opened.
- Tests: `test_local_agents_mention.py` rewritten as thread turns, a
  browser scenario: comment on a passage with the tick, see the chip,
  the agent's reply, reply again and see the session resumed.
- Docs: [dev/comments.md](../dev/comments.md),
  [dev/mentions.md](../dev/mentions.md),
  [dev/local_agents.md](../dev/local_agents.md) "A mention in a comment".

**As built** (3.5). The table keeps its name, `computer_chats`, and gains
`thread_id` (pages.db workspace step 62, `computer_chat_threads`, in
`LOCAL_STEPS`, with a partial index); a thread's conversation
(`chats.of_thread`) is never its bucket's active chat nor in History, and
`chats.begin` takes `thread_id` in place of the bucket's active one. The
turn is `local_agents/threads.py` (`start`, `settle`, `entry_of`,
`opened`, `chats_of`, `agent_named`); `mention.py` is `to_of` alone, and
reads a far computer's agent first where no vault here keeps the page,
the chat's own rule. `to` is checked against what the chat's picker offers,
not `agents.of_account`: this computer's agent the writer turned on, else
`chats.remote_of`'s for the page's work folder, by the computer's id; a
token's `to` and a share visitor's comment start nothing. The refusals are
`threads.REFUSALS`, `closed`, `not_newest`, `viewer`, `writes`, `off`,
`elsewhere`, `offline`, `paused`, `not_ready`, `busy`, `other` (the Send's
`too_many` and `nothing` went with the page's own thread sending the file:
a comment there is a turn about the page). The answer's key is `turn` in
place of `send`, and every thread the comment routes answer carries
`agent_chat` for its reader (the conversation and its turn's state), which
the chip reads; the client polls the threads every two seconds while one
has a turn to end. The end-of-turn rule runs where the conversation is
kept, from every write that ends a thread's turn (`chats._answered`: the
local `write_reply`, the far `write_run`, `stop_run` and the far fails),
under an id derived from the turn (`home.reply_id`), so a far turn, which
has no token, is answered by the server that keeps it; the agent's message
names the agent that answers (`agent_id`: `agents.here`, or the channel's
`agents.of_channel`). The far claim carries the thread's entry with its
PDF place and the computer computes its source line on its own disk
(`home._on_disk`, which a Send's entries share); its pictures are a local
vault's only. The prompt is `prompt.build_thread`, its JSON block
`{message, about, thread}`, the thread's comments standing for the earlier
messages; both prompts take the asking line (`prompt._asking`). The answer
card's threads are the agent message's `threads` (`threads.opened`,
written at a local turn's end), drawn by `chat/AgentChanges.jsx`
`ThreadsOpened`, opened by the navigation's new `openThread`. *Open in
chat* reads `GET /api/local-agents/chat?thread=` and shows the
conversation in place of the page's (`chatSide.js` `showThreadChat`,
`useChatThread`), its composer giving way to a line with *Show the thread*
and *Back to the chat*. The comment's agents are `chat/agents.js`
`commentAgents` (agents gain `agentId`: `GET /api/local-agents` names each
agent's `id`, a page's computers theirs), `mentionAgent` is gone, and a
computer agent's comment now carries its `agent_id`; the tick's rules are
`comments/sendTo.js`, its preference the account's `commentSendTo`, and an
edit has no tick (its `editHint` says what saving an `@name` it adds does).
`comments.js` lost `sentChip`, `sendState`, `isWithdrawn`, `mentionHint`
and `sendRefusal`: the Send's three went to `feedback/` for the Feedback
dialog until 3.7, and `turnChip` and `turnRefusal` are a turn's. The chats'
`far_problem` moved from the feedback router to `chats`. Rule 1's untick
is the client's word: a field that shows the tick always sends `to`, `""`
unticked, and only a client that sent no `to` (an edit, a field with no
tick) has its `@name` read. Rule 5 holds for a thread the agent opened too:
its first ticked reply begins the thread's own conversation with the
session of the conversation whose turn opened it (`threads.opener_of`,
`chats.begin`'s `resume` copying its `agent_conversations` row), so one
conversation still has one thread. A new comment's answer carries its
thread's `agent_chat` whether it is sent or not, so a reply among people
keeps the chip of a turn at work; the far `offline` and `paused` refusals
name the `computer`, so the client words every code but `not_ready` and
`other`. Not built: the Inbox item and `resolved_agent` still carry the
agent's English label, not its id (no step for it here).

### 3.6 A co-author's use

- vaults.db: `agent_people(server, workspace_id, account, agent, at)`,
  set in Settings on the computer with the shell's word. The announce
  carries the names; the server answers a named person the agent in the
  picker, the tick and the `@` menu.
- A named person's turn: their conversation, private to them and the
  owner; only in the directory of the folder its page is a file of; an
  ask offers *Allow once* and *Decline* alone, answered by that person
  or the owner, and no grant outlives the ask (decision 13). Its replies
  read *{agent} on {computer}, for {person}*.
- Before this ships: read what each vendor's terms say of a second
  person driving the owner's signed-in CLI. The note's survey did not
  cover it.
- Tests: `test_computers.py`, `test_remote_chat.py`.

**As built** (3.6). The vendors' terms were read first (the note's "A
co-author's use and the vendors' terms"): OpenAI's Terms of Use and
Anthropic's Consumer Terms both forbid making a personal plan's account
available to anyone else, Anthropic's Commercial Terms let an API key serve
the key owner's own users. That is close to a clear prohibition for the
personal plans, so the feature is built behind the owner's consent: the
desktop app's question before a person is named ends with one sentence
naming the vendor's terms and saying the owner answers for them
(`keeping.personAsk`). `agent_people` has a sixth column, `username` (how
Settings names a person while their server is not reached), and `server`
is "" for this server or the channel's `remote_url`; turning the agent off
deletes its people, so an agent turned on again, by anyone, serves none.
The routes are `GET /api/local-agents/people` (whom the owner may name: the
editors of the workspaces of this server it edits with others, and of each
channel's workspace, asked there over the channel's new `GET
/api/computers/people`), `POST /api/local-agents/{agent}/people` (the shell's
word and the owner) and `DELETE` (the owner's session, else the shell's
word, `shell_strict`), which stops the person's running turns
(`runner.stop_person`, `STOPPED["unnamed"]`). The shell's page ask is
`agent-person {agent, place, person}` (page asks carry further ids,
`preload.js`), main.js `agentPerson` reading the people itself, so the
native question names the person as the server says them. The listing
gives the owner each agent's `people` and a person it serves `lent: {by}`;
the announce names `people` per assistant (account ids), kept in
`computers.assistants`, so no users.db step. The server's `_lent_rows`
answers a named person the agent of another account's computer
(`of_account`, `of_page` with `lent_by`, that agent alone, never asking and
naming nobody; `agent_of(…, lent=True)`, never for the Sends), and
`names_of` names the computer in their comments. Whose agent a
conversation talks to is a column: pages.db workspace step 63
(`computer_chat_owners`, in `LOCAL_STEPS`) gives `computer_chats` an
`owner`, which binds a far turn to the owner's computer (`waiting_turns`,
`claim`, `write_run`, `fail_queued` key by it; the claim's turn names
`for`, and the channel's route checks `registry.serves`), and
`chats.readable` lets the owner read, stop, answer and export a
co-author's conversation (and see a thread's chip, `agent_chat.for`, and
open it, `of_thread(owner=True)`), never open or delete it. A local
co-author's turn mints its token for the person, names the person in the
prompt, checks `store.named` and the vault place again when taken
(`home.person`, `lent`, `serves`), marks each ask `once` (the card offers
Allow once and Decline alone; `chat` is 400 `ONCE_ONLY`; no grant is looked
up or kept), and its asks are cards of `computer_asks` for the owner too
(`card.for`, the bar's "From bob's chat of main.pdf on this computer"; the
bar follows the asks while an agent that may ask serves a person of this
server); a far co-author's turn asks nothing (`self.ask` false). The
byline: a comment's reader adds `agent_owner` and a block's stamp `owner`
(`agents.Agent.owner`, `here(name, person)`, `read_all`); the byline keeps
"on TIM-PC, for bob", its title reads "Written by alice's Codex on TIM-PC,
for bob", and a co-author's strip "alice's Codex on this computer". The
refusal code `folder` says a lent agent works on a synced folder's files
alone. A comment keeps whose agent wrote it: pages.db workspace step 64
(`comment_agent_owners`, in `LOCAL_STEPS`) gives `comments` an
`agent_owner`, the owner's username at write time, so an agent that later
changes hands relabels nothing, and `registry.names_of` names a lent
computer only by a row of the comment's workspace of that owner while it
serves the author and both edit there (`_lent_rows`' checks), so no other
account's announce renames it. An owner who no longer edits the workspace
lends nothing there (`store.lent_to`, `usable`, and the runner's check when
a turn is taken). The page's computers list the person's own before the lent
ones, as `agent_of` takes them, so the strip and the tick name the computer
the turns go to. Not built: the owner's History does not list a co-author's chat
conversations (the owner reaches a thread's from its chip); the Inbox's
item still names the agent by its English label.

### 3.7 The request layer goes

Removed, with their tests and docs:

- `feedback/requests.py` and the tables `feedback_requests`,
  `feedback_request_threads`, `feedback_request_runs` (`db.py:1106-1149`);
  the routes `/api/feedback/requests*` (`routers/feedback.py:221-317`);
  `.gamma/annotations/requests/` (moved there in 3.2) and the request files'
  half of `annotations/files.py` (`write_request`, `_requests`,
  `_prune_requests`, `REQUESTS`).
- `runner.py`'s `_Run` (`:1152`) with `on_request`, `_consider`,
  `queue_remote`, `withdraw_remote`, `waiting`, `queue_folder`; vaults.db
  `agent_runs` and `agent_threads` left unread; `home.py`'s Send half.
- The channel's `/api/computers/sends*` and the thread picture route
  (`routers/computers.py:198-332`).
- `frontend/src/feedback/`, the Feedback button (`App.jsx:11630`), the
  vault chip's item (`VaultChip.jsx:120`), the Comments window's button
  (wired at `App.jsx:12609`), `FeedbackHost` (`App.jsx:14477`).
- [dev/annotations.md](../dev/annotations.md) (renamed in 3.1) loses its
  request sections, and `routers/feedback.py` goes with the routes.

Done when: on a library PDF and on a `.tex` of a synced folder, the
person highlights a passage, comments with the tick, and Codex answers in
the thread under its own name, edits the file where there is one, and
asks a question of its own as a thread; no Feedback button is left; an
agent started in a terminal reads `.gamma/annotations/open.md`.

**As built** (3.7). The tables go through a migration rather than by
leaving them: pages.db workspace step 65 (`drop_requests`, in
`LOCAL_STEPS`) drops `feedback_requests`, `feedback_request_threads` and
`feedback_request_runs`, `db.FEEDBACK_SCHEMA` is gone from
`PAGES_SCHEMA`, and a backup made before the step runs it on its copy and
restores without them; vaults.db's `agent_runs` and `agent_threads` stay
in the schema, unread and unwritten (vaults.db is additive).
`annotations/requests.py`, `routers/feedback.py`, the channel's
`/api/computers/sends*` (`channel._sends`, `SENDS_EVERY_S`, the `sends`
wake) and `registry.heard` go; `files.FORMER` is a tuple, `.gamma/feedback`
and `.gamma/annotations/requests`, both deleted at every round and
removal. The thread picture route stays, which differs from the list
above: a far thread's turn needs its region's picture, so
`GET /api/computers/threads/{id}/picture` is authorized by
`chats.holds_thread` (the computer's run holds a turn of that thread,
for the token's account) and `HttpChatHome` fetches the pictures into the
folder's `.gamma/annotations/img/` (its `.gitignore` first), so a far turn
sees them as a local one does. The runner keeps one kind of item, a
`_ChatTurn`, keyed `(ws, turn id)` (a far one `(link id, turn id)`), and
`at_work` and a link's `agent.running` say `{detail}` alone; `_Run`,
`waiting`, `queue_waiting`, `queue_folder`, `folder_agent`, `running`,
`send_runs` and `MAX_THREADS` went with it, and so did `PATCH
/api/local-agents/{agent}`'s `queued`, `folder_links.try_holding`,
`agent_folder` and `_hand_waiting`, the jobs' `feedback-run` rows and the
tasks' `stopBody`. The structured answer goes from the drivers too (the
schema, Codex's `outputSchema`, Claude Code's `--json-schema` and
`StructuredOutput`, `done.answer`), with `prompt.build`, `ANSWER_SCHEMA`,
`pending` and `answers`. The reader loses `sent`, `request_state`, the
`ids` argument and `thread_ids`; `list_annotations` loses `request` and
`mcp_links.holders` its requests; `hand.answer_run` and the `seen` stamp
go; `store.forget_threads` is `forget_sessions` (the conversations'
sessions alone). The client loses `frontend/src/feedback/` with its two
test files and the 49 strings only it used (`tools/i18n.mjs --sync`); the
desktop's `agentChat` goes and `agentSaid` names the chat and the comments.
The tests the Sends had are thread turns now where the behaviour stays: a
timeout, the error's record, a detach, recovery waiting for a workspace's
migrations, a turn behind another in its directory, an agent that may not
ask, a turn's detail on the links, the source line through the vault's
rows, a push while paused, *Stop showing*, an `@codex` made on the other
server, the computer's binding, and the handwriting card's new group in
the browser; withdraws, claims, progress, answer retries, the 100-comment
cap, "nothing new", a token's Send refused and the dialog's document are
the Sends' own and went with them. The browser groups `feedback` and
`remote-sends` go (`select.mjs` maps their files to `work-folders` and
`remote-chat`); `local-agents` checks the Done-when: no Feedback button in
the folder's list bar, the vault chip's menu or the Comments window, a
ticked comment on a line of a synced folder's `.tex` edited by Codex (the
code view shows it after the sync), Codex's own question opened as a
thread on another line, `open.md` naming both threads, and a ticked comment
on a library PDF's passage answered in its thread. That step found the code
view's CodeMirror rules (`viewers.css`) reaching the comment column's own
fields, which stand inside its editor (a field with the code's margin, height
and hidden caret): they name the code's editor by its children now
(`.docTextHost > .cm-editor > …`).

The loose ends of 3.3-3.6 close in the same step. The Inbox item and a
thread's resolving agent keep the agent's name, id and owner (users.db
`inbox_items` gains `agent_id` and `agent_owner`, pages.db `comments`
`resolved_agent_id` and `resolved_agent_owner`, step 66 `agent_ids` with
a global and a workspace part, the latter in `LOCAL_STEPS`), the server
answers them as `Agent.shown`'s `{id, kind, name, on?, owner?}` named
through `agents.read_all`, and the client words them with
`chat/agents.js` `agentWords`; the step rewrites an old label where
users.db (the Inbox) or the thread's comments (a resolve) name its agent
and leaves any other as words, and `channel.computer_name` reads the host
name without the settings so the users.db part can name this computer. A
thread turn's row in Background tasks opens the thread's conversation:
`runner._chats` and `chats.remote_turns` keep the turn's thread and the
conversation's owner, the row's `params` carry `conversation`, `thread`
and, for a co-author's turn listed in its owner's tasks too, `person`, and
the client opens it through `chatSide.js` `showInChat`, which replaced
`showThreadChat` and `pickChatAgent` as the one way to show another
conversation in a page's chat. An undo of an agent's block's deletion
keeps its stamp through a tombstone rather than anything the client
sends: an op's delete writes the stamp to pages.db `agent_stamps` (an
additive table) with the block's text, and an insert of that id with
that text takes it back (`_Batch.keep_stamps`, `stamp_back`). The owner's
History lists a co-author's chat conversations under their name (`GET
/chat/history`'s `lent`, `chats.lent_history`), opened in place of the
owner's own (`GET /chat?conversation=`, `chats.lent_one`, the
`{conversation}` of `showInChat`), exported but never selected, opened as
the owner's or deleted; the strip offers no terminal on one.

## 4. Another server

### 4.1 A computer linked in Settings

- Settings → AI → Agents, in the desktop app, lists *Servers this
  computer answers* from `GET /api/computer-links`, adds one (the shell
  mints the `computer` token as `showComputer` does, `main.js:2480`) and
  removes one. No vault is needed.
- `computer_links` gains `asks`, the switch of decision 7.

**As built** (4.1). `GET /api/computer-links` was there already (the
sync panel's line read it); it gains `asks` and `workspace_name`, and the
section is `SettingsAgents.jsx` `AnsweredServers`, a read of the pane where
`who.desktop`, shown below *This computer* in the desktop app and nowhere
else. A row names the link by its address's host and the workspace there:
the host keeps no server name of the launcher's, so the announce's answer
names the workspace (`workspace: {id, name}`) and the channel keeps it
(`computer_links.workspace_name`), which also names that workspace in the
people an owner may name (`_co_authors_there`, "" before). Users.db step 67
(`computer_asks`) adds `computer_links.asks` and `workspace_name` and the
server's `computers.asks`: the announce carries `asks` (a flag, false from
an app that says nothing), the server keeps it and answers it with the
account's computers and the page's (`registry.of_account`, `of_page`; a
lent computer's false), for 4.3 to read (4.3 relays the asks). `PATCH
/api/computer-links/{id} {asks}` takes the shell's word to turn it on,
even on a server started with no secret (`shell_strict`), and the session
to turn it off; a new token for a link keeps it. The shell's
page asks are `add-server {link}` (`"new"`, or a link's id: *Reconnect…*
on one that server refused, hands it a new token), `remove-server {link}`
and `link-asks {link, asks}`, run by `addLinkedServer`,
`removeLinkedServer` and `setLinkAsks` (`removeServer` is the
launcher's). *Add a server…* picks among the launcher's remote
servers; it signs in nowhere by itself: signed out there, its question
offers to open that server in the window to sign in, and the person adds
it again. With several workspaces there it asks which (a clone's left
out). *Remove* deletes the link with the shell's word, which stops the
turns of that server that run here (`runner.stop_channel(…,
"unlinked")`, 4.2's), and the shell revokes the token there, where the server's
`registry._fail_held` fails what its runs held, as *Stop showing* does
(`dropComputerLink`, shared). The host starts at launch while it keeps a
link (`folderHost`, cleared by `releaseHost` only once neither a vault of
a remote server nor a link is left), since a channel runs only while the
host does. Not changed: the last vault of a server and workspace removed
still takes the link with it (`releaseRemote`), one made in Settings too,
since the host does not tell the two apart.

### 4.2 Any chat there

- `registry.agent_of` (`computers/registry.py:609-646`), bound to a
  folder, gives way to the agents of an account's online computers;
  `GET /api/pages/{id}/computer` to a read of the agents for a bucket.
  The picker names the computer where there are two.
- A far turn's claim carries the directory as an id (a vault's, an
  offered directory's, or none). The server never names a path; the
  computer resolves the id and refuses one it does not offer.
- The token of a turn (2.7) is minted by the server at the claim and
  handed to the computer with its own MCP address.

**As built** (4.2). The far seats are `registry.seats(ws, bucket,
user_id)`, in place of `agent_of`, `agent_folders` and `_kept_with`: each
agent on of each of the account's computers shown to the workspace, on
any bucket (a page of a synced folder or not, a folder, the library), read
even while the computer is away; those keeping the bucket's work folder
first (their agents work in that vault), then the lent ones (3.6's rule
stands: a co-author's agent is the owner's first agent on, on a work
folder's files alone), then the others; online ones first. `GET
/api/pages/{id}/computer` keeps its path and answers every such computer:
`vault` the one that keeps the page or null, its `agent` replaced by
`work` (a computer's every agent on works in the work folder it keeps,
not its first alone, as on this computer), and `dirs`. The client's
`keeperAgent` and vaultState's `vaultAgent` gave way to `farAgents`, a
far agent's `id` is its `agentId` (`computer:<computer>:<name>`), which
the chat's routes take as `agent` (`_named`: this computer's by its name
or its id, another's by its id), and the chip lists the keepers' agents,
then this computer's, then the others'; every far agent is named with its
computer (*Codex on TIM-PC*), not only where there are two. The computer
chip names the first computer that keeps the page, and its *Agent:* line
went. A conversation keeps its computer as it keeps its agent:
`computer_chats.computer` (workspace step 68 `far_chats`, in
`LOCAL_STEPS`, filled from each far conversation's newest run's holder,
else its owner's computer that announced a vault of its work folder), so
a turn is listed to that computer alone (`chats.waiting_turns` by it, no
longer by folder and agent) and the announce's or a token's end fails
the queued turns it would no longer take (`registry._refusal`: its agent
off, a person no longer served, the work folder no longer kept, the
directory no longer offered, the channel gone; the old "Codex is no
longer this folder's agent." went). The server reads the conversation's
`dir` (W, an offered directory's id, "") as `chats.dir_of`'s `{kind, id}`
(a folder of the tree is a `vault`), the listing and the claim carry it,
and the claim's `page: {path, title}` became `about` (`chats.about`). The
offered directories are announced by id and name (`state_of`'s `dirs`,
`channel.offered`, while the channel's account has an agent on; users.db
`computers.dirs`, step 68's global part, none kept while no agent is on),
the strip answers them (`dirs`) and `POST /chat/turns`'s `dir` must be one
(`chats.far_dir`). The computer resolves the id (`home.far_place`): a vault
of that channel's server, workspace and account alone, an offered
directory while the account has an agent on, or `<data>/agents/<channel
id>/<conversation id>/` of its own (`home.far_dir`, removed with the
channel and, once it is gone, at start), refusing anything else with why;
a turn that names nothing here waits in a queue of its own and fails
once taken. The runner keys a far turn by the channel (`(channel id, turn
id)`, `_chats[…]["channel"]`, vaults.db `agent_turns` `ws` and `link_id`
the channel's id) in place of its vault, which `run["link_id"]` still
names for a turn in one; `stop_remote_chat`, `end_stopped_remote`,
`fail_remote_of` take the channel, the channel's `delete` calls the new
`runner.stop_channel`, `ask_refusal` takes the server's address, and
`channel.of_vault` and `runner._vault_place` went. Not asked for but
needed: a turn for a computer offline is refused (409 "TIM-PC is offline:
send this again once it connects."), never queued, so the strip says
*send this once it connects.* and the composer holds the message.
The turn token: the claim mints it (`routers/computers.py` `_tools`,
`integrations.mint_turn` for the turn's `for`, named with the computer's
agent's id here), keeps its id in the run's row (`computer_chat_runs.token`,
step 68), replaces it on a retried claim, renews it at the run's writes
(`renew_turn`'s new `within`, at most once a minute, `chats.TOKEN_S`), and
deletes it wherever the run ends (`chats._close`: a write that ends it, a
Stop, `fail_held`, a message gone); `agents.of_token` names a far token's
agent by `registry.far_name` (the account's computer of that id in the
token's workspace, else a lent one with its owner), and `of_channel` went.
The address is `_mcp_url`: the public URL set in Settings, else the
loopback address the claim came to, else none (the MCP transport refuses
other hosts), the claim's `tools` null and the turn without tools. The
computer takes it only at the channel's origin and path `/mcp`
(`HttpChatHome.gamma`); refused, the turn runs without the tools and its
message begins with why (`home.FAR_TOOLS`, `_ChatTurn.preface`), and
where the server gave none it says nothing (only the log does): a NAS with
no public URL would otherwise say it on every turn. History rows, the
conversation, a thread's `agent_chat` and Background tasks' `params` name
the conversation's agent by its id too (`agent_id`, `chats.agent_id_of`),
which `pickAgent` matches by `agentId`, so *Open in chat* and History
pick the right computer's agent. A desktop app's restart deletes every
turn token it holds (`drop_turns`), a far one minted there with them.
A conversation whose directory is no longer kept or offered there gives
way to a new one at its next message (`far_dir` answers "" for it and
`begin` archives it), a thread's conversation too; a `dir` the request
names that is not offered is still refused. A computer may not announce
this server's own computer id (`registry.normalise`, 400), so a far
turn's token never writes as this computer's agent, and `of_token` reads
that id as this computer's only where agents run here. The turn path, like
the announce, reads an older server's shape: a listed turn with no
`agent` (`{id, conversation_id, page_id, folder_id}`) is queued in the
vault here that keeps its work folder, with the agent that vault names
(`channel._chats`), and a claim's answer with no `dir` keeps the listed
one (`HttpChatHome.claim`). Where no page is open (a folder's or the
library's chat), the chip lists the account's computers' agents from `GET
/api/computers`, which now carries each computer's `dirs`, without
another account's lent ones (`vaultState.readAccountComputers`), after
this computer's.

### 4.3 Asks relayed

- Answering an ask of a far turn is refused today
  (`routers/local_agents.py:403-406`), and a far turn never asks
  (`runner.py:1614`). Where the computer's link allows it and the agent
  may ask, the ask is written with the turn as now, the answer is stored
  with the run and wakes the computer, and the computer's turn takes it.
- *Ask on this computer*, the card on the computer's own screen, stays
  later.
- Tests: `test_local_agents_asks.py`, `test_remote_chat.py`. Docs:
  [dev/computers.md](../dev/computers.md),
  [dev/local_agents.md](../dev/local_agents.md) "Trust".

Done when: from a phone, on a page of a NAS that is in no synced folder,
Claude Code on the PC is picked, asks to run a build, and the card is
answered on the phone.

**As built** (4.3). The relay is the link's switch of 4.1 (`asks`), read
on the computer at the turn's start, at each ask and at each write
(`_ChatTurn._relays`), never the server's record of it. *Ask on this
computer* did not wait for later: it is the first place, and the relayed
card the second, as the note at the end said. A far turn of the owner's
asks on the computer as before whatever the switch (the bar, the shell's
secret, https); with the switch on it is written in that server's message
too (`asks`, the chat's own card, each `once`: no grant is kept for any
turn on another computer, so the owner's far ask offers *Allow once* and
*Decline* as a co-author's does), and the shell's secret and the bar's
presence are not needed then; `waiting` now says only that an ask waits on
the computer alone. A co-author's far turn asks only with the switch on.
The answer is kept with the run in a new column, pages.db
`computer_chat_runs.answers` (workspace step 69 `far_answers`, in
`LOCAL_STEPS`; `SCHEMA_VERSION` 69), the ask taken out of the message at
once (`chats.answer_run`), the computer woken with `chat` and handed it by
the listing's new `answers` (`chats.answers_of` → `channel._chats` →
`runner.answer_relayed`); the server's writes leave an ask answered there
out of the message, and keep its answer while the run's writes still name
the ask. The first answer is the one: the computer's `_decide` takes one
answer an ask, and the server refuses an ask its message no longer holds;
when both come at once the computer's stands and the server's card closes
at the next write. The server's answer route now answers a far turn's ask
(the turn's person or its agent's owner, `chats.readable`) and refuses
`chat`; on the computer, its chat route still refuses its far turns (409).
Not asked for but needed: the desktop app follows the asks while it keeps
any link, not only a remote work folder (4.2 made every chat there ask
here); the bar's notice after an answer that came too late reads "nothing
was allowed here", since the server's answer may have allowed it;
Background tasks say *Waiting for you* for a relayed ask and *Waiting for
your answer on TIM-PC* (`progress.there`) for one that waits on the
computer alone; the switch changed while a turn runs writes its message
there again (`runner.relays_changed`). The stand-in Codex lets a scripted
turn give its own asks (`turns`' `ask`), for the browser scenario's build.

## 5. Sessions

- **New** `local_agents/sessions.py`: the CLI's own sessions of a
  directory (C4), each with its id, title, time and whether Gamma has it
  already. `GET /api/local-agents/sessions?agent=&dir=`.
- History, for a computer's agent, gains *On {computer}*: those sessions.
  Opening one reads its turns into a new conversation (the person's and
  the agent's text; tool steps as a summary) and binds it to the session
  (`agent_conversations`), so the next turn resumes it.
- A session another process holds is refused with why.
- First on the desktop app's own server; over the channel after.
- Tests: `test_local_agents_extras.py` with fake session stores.

**As built** (5.1, sessions on this computer). `local_agents/sessions.py`
reads both stores as C4 found them, `GET /api/local-agents/sessions?agent=&dir=`
lists them and `POST /api/local-agents/sessions/open {bucket, agent, dir,
session}` opens one, a route of its own rather than a `session` field on
the turn's, since opening runs no turn. `dir` is the id the picker names
(`chats.place_named`): a work folder's W whose vault the account made (the
terminal's rule: a vault another account made lists none), or an offered
directory; a directory of its own lists none, as it holds Gamma's sessions
alone, and the page's bucket must work in that same directory (400). Each
session is `{id, title, at, created, source, ours, busy}`, `source` one of
`cli`, `vscode`, `exec`, `app`, `gamma`, `other` (Codex's originator first,
since its TUI's threads say `source: "vscode"`: C3); no turn count, which
Claude Code's files would give only read whole. `busy` is a flag the
client words, not the server's sentence. Codex's lock is read as held, not
as present: on Windows Codex holds a byte-range lock on the file (a read
of it is refused, which a file left by a killed Codex is not), elsewhere a
shared `flock` is tried, so a stale lock never makes a session look busy;
the turn checks it too (`sessions.held`, before any process for either
CLI), and Codex's own -32600 refusal fails the turn as
`events.SessionBusy` (the done's `busy`), which neither the driver nor the
runner restarts afresh. The fallback to `thread/start` stays for any other
refused resume. A live Claude Code process must also have the entry's
`procStart` as its creation time on Windows. The messages read from a
session are marked `from_session`: a turn's earlier messages leave them
out (`chats.earlier_of`), so a resume that fails and starts afresh is told
the conversation's own messages and never the session's. The opened
conversation is written by `chats.adopt` (the bucket's active one archived,
`_insert` shared with `begin`), its agent's text and steps through the
runner's own wording (`reply_text`, `step_of`, made public), the drivers'
tool mappings shared with it (`codex.item_steps`, `claude.tool_started`,
`tool_ended`). A session a conversation of this bucket continues already
opens that conversation (`chats.open_one` when archived); one of another
bucket, another account's or a thread's is refused with where. Nothing is
stored anew (`agent_conversations` binds the session, `store.bound` reads
it back), so no migration. History draws the sessions as a `section` of
`ChatHistoryPop` under a `popoverSection` caption *On {computer}*, read
each time the popover opens, searched with the rest. The browser step
holds a thread's lock from another process (`tests/fake_agents/hold_lock.py`),
and the stand-in Codex keeps a `sessions` store (`thread/list`,
`thread/read`, `thread/turns/list`, the held thread's refusal).

**As built** (5.2, over the channel). The server asks the computer in
memory, never in a table (`gamma/computers/sessions.py`): an ask waits
there, the poll is woken with `sessions`, the computer takes the asks
(`GET /api/computers/sessions`, each handed out once) and answers each by
its id (`POST /api/computers/sessions/{id}`, the token it was asked of
alone, checked and capped by `routers/computers.py` `_session_answer` as a
turn's writes are). The routes of 5.1 keep their paths and became `async`:
`agent` names another computer's agent by its id, as the chat's routes do
(`_named`), and the route waits on the event loop for the answer (a
listing 15 s, an opening 30 s), so a NAS's worker threads are never held.
"The client asks again on opening History": a listing's answer is kept
30 s and one that comes after the wait is kept for the next opening (a
listing asked while one waits joins it), and nothing is asked while
History is closed; the client words why no session shows (`away`:
`offline`, `older`, `waiting`; `computerChat.sessionsAway`). An older
computer is told apart by its announce, not by a silence: the computer
says `sessions: true`, which the server keeps in memory alone
(`registry._live.sessions`, `answers`), so no users.db step. Only the
account's own computer is asked (`registry.own_computer`: never a lent
one), in a work folder it keeps or a directory it offers, by id. The
computer answers in a thread of its own (`channel._answer_session`,
`local_agents/sessions.answer_far`) for the channel's account with its
agent on, in the place `home.far_place` resolves (which now checks the
conversation's id for a directory of its own alone; a far turn's claim
checks its id's shape itself, `FarChatHome.place`). The binding is the
computer's: the server mints the conversation's id, the computer binds the
session it read to that id in `agent_conversations` before it answers, and
`runner._session` resumes it at the next far claim as it resumes any far
conversation's, so the server never names the session a turn resumes. Not
asked for but needed: the computer's listing names the conversation each
session is bound to (`conversation`), and the server makes `ours` of its
own alone (`sessions.listed` and `shown` split, `chats.titles` by account,
computer and agent); a session bound to a conversation the server lacks
(one of the computer's own server, or deleted there, which the computer
never hears) is opened again with `rebind`, since the computer cannot
tell which server a conversation is of; `chats.adopt` takes the id and
the computer and no longer binds (the local route binds after it); the
claim's earlier messages leave `from_session` ones out with the rule
`home` used (`chats.earlier_of`, one function for both), so a far resume
that fails and starts afresh is told none of the session. The browser
step is in `remoteChat.mjs`: the computer's stand-in Codex keeps a
terminal's thread of the paper's directory, opened from alice's History
on the NAS and resumed by her next message there.

**As built** (5.1 and 5.2, after review). One conversation resumes a
session: opening a session bound to a conversation elsewhere moves the
binding (`store.unbound` clears the session of every conversation that
had it, which then starts afresh), on the rebind over the channel and on
the desktop alike. A binding outlives its conversation (a page deleted for
good, a far open that failed after the computer bound it), so the
desktop's History counts a session as *In Gamma* only where the workspace
has its conversation (`chats.titles`), and opens any other as a new one.
Over the channel, a conversation the server has but that is not the
account's with that agent (a co-author's among them) is refused rather
than rebound, and the bucket's turn at work is refused before the computer
is asked. The desktop's opens run one at a time (`_opening`), and
History's session rows wait while conversations are ticked or an open is
on its way. Claude Code's compaction summary (`isCompactSummary`) is no
prompt.

## Data, in one table

| Where | Change | How |
|---|---|---|
| vaults.db | `computer_agents`, `agent_dirs`, `agent_dir_grants` new and `agent_folders`, `agent_policies`, `agent_grants` unread (built, 2.1, 2.2); `agent_people` new (built, 3.6); `agent_runs`, `agent_threads` unread (later) | additive, with one carry-over at start (2.1) |
| users.db | `integration_tokens.comments`; `feedback` tokens become commenting `read` tokens (built, 3.3: global step 61, `integration_comments`); `computer_links.asks` and `workspace_name`, `computers.asks` (built, 4.1: global step 67, `computer_asks`); `computers.dirs` (built, 4.2: step 68 `far_chats`, its global part); `computers.assistants` reshaped | one global step, the next free number after 58 |
| pages.db | `computer_chats` keeps its name, `page_id` becoming `bucket`, with `dir`, and `computer_chat_runs` gains `bucket` (built, 2.5: workspace step 59, `computer_chat_buckets`); `thread_id` (built, 3.5: workspace step 62, `computer_chat_threads`); `owner` (built, 3.6: workspace step 63, `computer_chat_owners`); `comments.agent_owner` (built, 3.6: workspace step 64, `comment_agent_owners`); `computer_chats.computer` and `computer_chat_runs.token` (built, 4.2: workspace step 68, `far_chats`); `computer_chat_runs.answers` (built, 4.3: workspace step 69, `far_answers`); the three `feedback_request*` tables dropped; `comments.agent_id` `local:<name>` rewritten to the new id (built, 3.4: workspace step 60, `comment_agent_ids`) | one workspace step, in `LOCAL_STEPS` as 54 and 58 are: no copy of a workspace syncs these tables |
| data.db | `feedback_pictures` renamed | rebuilt on demand, no step |
| The directory | `.gamma/feedback/` removed, `.gamma/annotations/` written | at the vault's first round |

## Order

Each numbered step is a branch from `dev` in its own worktree, merged
back before the next starts; none is stacked on another.

```text
0 checks ──┬── 1 explorer, Detach            (alone, any time)
           └── 2.1–2.3 settings ── 2.4–2.6 picker, one transcript
                                   └── 2.7–2.8 tools, no provider
                                       └── 3.1–3.2 reader, disk
                                           └── 3.3–3.4 tools, identity
                                               └── 3.5 threads ── 3.6 co-author
                                                   └── 3.7 removal
                                                       └── 4 another server ── 5 sessions
```

3.7 comes after 3.5 so that no day passes with neither way of asking an
agent. Step 2 is merged in its three groups, each leaving the app
working: after the first the sync panel no longer sets an agent and
Settings does; after the second the chat has its picker; after the third
the agent has Gamma's tools.

Per step: the affected modules' tests, one build after the last edit and
the browser scenarios named above
([dev/debugging.md](../dev/debugging.md#local-changes-test-the-affected-modules)).
New words get their `zh` strings ([dev/i18n.md](../dev/i18n.md)).

## Risks

- **`ChatDock.jsx`** (2.6). The adapter keeps the change in one file of
  mapping; if the transcript needs more than the mapping, stop and split
  the transcript out first ([dev/frontend-refactor.md](../dev/frontend-refactor.md)).
- **A credential beside untrusted text** (2.7, 3.3). The agent reads
  PDFs and other people's comments while it holds a token. The token
  comments and highlights and does nothing else, lives for one turn, and
  everything it writes is stamped and can be deleted by the owner. Notes
  are changed only through the approval card.
- **The CLIs move.** The app-server is labelled experimental. The
  drivers stay pinned to tested versions, and C1 to C4 are run again on
  an update.
- **A thread left unanswered** (3.5). Without Send's shaped answer the
  agent may skip one; the turn's end writes its last message there.
- **A server that lies** (4). A linked server is trusted with the agents
  turned on for it; relayed asks widen that, which is why they are off
  until the owner turns them on per server.
- **History that does not come along.** Requests already made are
  dropped with their tables. Conversations in `computer_chats` move.

## Answered by the owner (2026-11-14)

- **The word** in the interface is *synced folder*, in English and in
  every catalog; *work folder* stays the code's and the developer docs'
  name for it.
- **The icons** were the whole set, mapped as VS Code maps them with the
  Material Icon Theme: its manifest's file names, extensions and folder
  names, light variants where the theme has them. Replaced on 2026-11-17
  by Gamma's own kind icons, the theme's filled logos being the one foreign
  set in the app ([dev/ui-design.md](../dev/ui-design.md#file-icons)).
- **Marks on disk**: the marks of a file linked to disk are written
  there too, a synced folder's files and a page that follows one file
  (a vault of kind `file`) alike. A library PDF linked to nothing is
  read through the tools only.
- **A co-author** is granted the agent once and asks it with `@` or the
  tick; what their turn asks to run is a one-time grant (decision 13).

Since the plan was written `dev` gained *Ask on this computer* (step 5b
of [agent-collab.md](agent-collab.md)'s follow-ups: a far turn's asks
drawn and answered in the desktop app's bar on the computer). Step 4.3
builds on it: the relayed card is the second place such an ask is
answered, where the computer allows that server.
