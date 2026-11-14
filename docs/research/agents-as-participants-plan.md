# Agents as participants: the build plan

Status: **a plan; nothing is built.** Written 2026-11-14 against `dev` @
b4870d47, after a survey of the code behind work folders, local agents,
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

### 2.2 The desktop app

- `preload.js`: asks `set-agent`, `set-agent-runs`, `agent-dirs`.
  `main.js`: `setAgent` and `setAgentRuns` (`:2233`, `:2279`) lose their
  link and keep their native question and the shell's word.
- `ui/bar.html:616`: the vault's agent row goes; its place says where
  agents are set. `lib/keeping.js`: the agent's words move with it.
- Tests: `desktop/test`. Docs: `desktop/docs/architecture.md`.

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

### 2.8 A chat without a provider

- `ChatSetupCard` (`ChatDock.jsx:408`) lists the agents found beside
  *Connect an AI service*; `aiOff` no longer closes a chat whose agent
  is a computer's.
- Docs: [dev/onboarding.md](../dev/onboarding.md), the user guide.

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
- The tools in `ai_tools.py` (`:4389`, `:4416`) become
  `list_annotations` and `read_annotation`; `mcp_server.py`'s `SPREAD`
  and thread arguments follow. `ai_context.py`'s highlights section
  gives a PDF passage its page number, which it lacks today.
- Tests: `test_feedback_entries.py`, `_anchors`, `_sources`, `_kept`,
  `_pictures`, `_images`, `_tools` under their new names, with a library
  page and a bare highlight added.

### 3.2 On disk

- `annotations/files.py`: `.gamma/annotations/open.md`, `open.json` and
  `img/`, written by the vault's rounds as `.gamma/feedback/` is; the old
  directory is removed at the first round (`files.remove`).
- For a vault of another server, the round reads the entries and the
  pictures from that server with the vault's token and writes the same
  files (today `files.py:138` writes none there).
- Tests: `test_feedback_files.py` renamed, `test_remote_work_folders.py`.

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

### 3.7 The request layer goes

Removed, with their tests and docs:

- `feedback/requests.py` and the tables `feedback_requests`,
  `feedback_request_threads`, `feedback_request_runs` (`db.py:1106-1149`);
  the routes `/api/feedback/requests*` (`routers/feedback.py:221-317`);
  `.gamma/feedback/requests/`.
- `runner.py`'s `_Run` (`:1152`) with `on_request`, `_consider`,
  `queue_remote`, `withdraw_remote`, `waiting`, `queue_folder`; vaults.db
  `agent_runs` and `agent_threads` left unread; `home.py`'s Send half.
- The channel's `/api/computers/sends*` and the thread picture route
  (`routers/computers.py:198-332`).
- `frontend/src/feedback/`, the Feedback button (`App.jsx:11630`), the
  vault chip's item (`VaultChip.jsx:120`), the Comments window's button
  (wired at `App.jsx:12609`), `FeedbackHost` (`App.jsx:14477`).
- [dev/feedback.md](../dev/feedback.md) becomes `annotations.md`, its
  request sections gone; the developer guide's row and every source
  comment citing it follow.

Done when: on a library PDF and on a `.tex` of a synced folder, the
person highlights a passage, comments with the tick, and Codex answers in
the thread under its own name, edits the file where there is one, and
asks a question of its own as a thread; no Feedback button is left; an
agent started in a terminal reads `.gamma/annotations/open.md`.

## 4. Another server

### 4.1 A computer linked in Settings

- Settings → AI → Agents, in the desktop app, lists *Servers this
  computer answers* from `GET /api/computer-links`, adds one (the shell
  mints the `computer` token as `showComputer` does, `main.js:2480`) and
  removes one. No vault is needed.
- `computer_links` gains `asks`, the switch of decision 7.

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

## Data, in one table

| Where | Change | How |
|---|---|---|
| vaults.db | `computer_agents`, `agent_dirs`, `agent_people` new; `agent_folders`, `agent_policies`, `agent_runs`, `agent_threads` unread | additive, with one carry-over at start (2.1) |
| users.db | `integration_tokens.comments`; `feedback` tokens become commenting `read` tokens; `computer_links.asks`; `computers.assistants` reshaped | one global step, the next free number after 58 |
| pages.db | `computer_chats` → `agent_chats` with `bucket`, `dir`, `thread_id`; `computer_chat_runs` renamed; the three `feedback_request*` tables dropped; `comments.agent_id` `local:<name>` rewritten to the new id | one workspace step, in `LOCAL_STEPS` as 54 and 58 are: no copy of a workspace syncs these tables |
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
- **The icons** are the whole set, mapped as VS Code maps them with the
  Material Icon Theme: its manifest's file names, extensions and folder
  names, light variants where the theme has them.
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
