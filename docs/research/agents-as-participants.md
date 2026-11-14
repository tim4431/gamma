# Agents as participants: synced folders as an explorer, agents in Settings, no feedback surface

Asked 2026-11-13, decided 2026-11-14. The owner read what
[file-explorer.md](file-explorer.md) and [agent-collab.md](agent-collab.md)
built and corrected four things: a synced folder should look and act like a
file explorer and be detached, not deleted; a local agent belongs in
Settings and is one more agent of the chat, not a property of a synced
folder; the agent on the PC should be driven from the online interface,
with its sessions shared both ways; and there should be no per-folder
feedback surface, because an agent that reads the person's highlights and
comments and answers by commenting is a user of the workspace.

Status: **a plan; nothing is built.** The mechanics as they stand are in
[dev/work_folders.md](../dev/work_folders.md),
[dev/local_agents.md](../dev/local_agents.md),
[dev/computers.md](../dev/computers.md) and
[dev/feedback.md](../dev/feedback.md). This note records where the built
design differs from what was asked, the decisions, the design and its
steps. Three of the corrections reverse decisions the two earlier notes
record ("Reversed" below). What to build, unit by unit with its files,
tests and order, is [agents-as-participants-plan.md](agents-as-participants-plan.md);
where the two differ, the plan is the later word.

## The answer in short

Today everything hangs on the synced folder: the agent is switched on for
one folder, the chat's agent side is drawn only on that folder's files, a
computer is announced to a server when a folder of that server is made,
and feedback is the comment threads of that folder sent as a request. The
correction hangs it on the agent instead:

- **An agent is a named actor that belongs to a person.** Gamma AI, Codex
  on a computer and Claude Code on a computer are three of them. One
  picker in the chat, one transcript, one set of Gamma tools.
- **A synced folder is only where files are.** It gives an agent a
  directory to work in when the page is one of its files, and nothing
  else.
- **A person's marks are the agent's reading.** Highlights and comments on
  any file, read through one reader, and written to the directory of a
  synced folder so an agent started in a terminal reads them too.
- **The agent answers as itself**: a comment, a resolve, a highlight, an
  edit of the files, each signed with its name.

This removes more than it adds: the request layer and its dialog, the
second chat's own transcript and composer, and one of the runner's two
flows go. What is new is the agent's identity with its permissions, a
short-lived Gamma connection handed to an agent for a turn, and the
explorer's look.

## What exists, and where it differs

| Asked | Built | Difference |
|---|---|---|
| Are a Gamma folder and a synced folder one logic? | No. A Gamma folder is a row of a tree, and a page lists the folders it is in, several if one likes. A work folder (a vault that pushes everything) follows the disk: each tracked file is a page in exactly one directory, and a person may not rename, move or delete it (`work_folders/filing.py`, mirrored by `explorer/workFolders.js`). | The data is already an explorer's. The look is not: the same folder card and row as any folder, no mark that tells them apart, a tree only in the Files window, seven icons by format class (`FileGlyph`). |
| Detach on a right click | A work folder's menu has no Delete. *Let go of this work folder…* shows only where no vault of this server keeps it; *Remove the vault…* is in the desktop app's sync panel alone and sends every file not added to the library to Recently deleted. | No one verb, no way to keep the files as pages, nothing in a browser while the computer keeps the folder. |
| The agent in Settings, a peer of the chat | Turned on per work folder in the sync panel (vaults.db `agent_folders`, `agent_policies`); no Settings pane names an agent. The chat draws a second side, *Gamma AI* or *Codex*, only on a page that is a live file of that folder (`chats.vault_of`). Its own routes (`/api/local-agents/*`), its own private table (`computer_chats`), its own components (`chat/computer/`). The agent gets the file's path and the selected passages; Gamma's tools and the page's notes and highlights are withheld, and every MCP server is turned off for the run. | Not a peer: another place, another store, less context. With no provider the chat is closed everywhere except those pages. |
| Driving the PC from the online interface | The channel exists: the computer dials out (an announce and a held poll), and any window of the maker starts, follows and stops a run, a phone included. | Only on files of a work folder made from an empty folder of that server; the computer is announced when such a folder is made; no choice of computer, agent or directory; a far turn never asks (answering an ask is refused with 409). |
| A session of Gamma's found in the CLI | Yes. The CLI's session id is kept (`agent_conversations`, `agent_threads`), sessions are made in the CLI's own store, and *Continue in a terminal* runs `codex resume <id>` or `claude --resume <id>`. | Not checked: whether such a session shows in the CLIs' own resume lists, or only by its id. |
| A session of the CLI continued in Gamma | No. Nothing lists or reads a session started outside Gamma. | |
| No feedback button; the agent reads marks and comments back | Feedback is the comment threads of a work folder's files (`comments.threads_in_work_folder`), with a request layer over them: Send, `feedback_requests`, the states, the *Sent* chip, the Feedback dialog and its three ways in, `.gamma/feedback/requests/`. A highlight without a thread is not feedback. A reply is the person's own comment stamped with the agent's name (`agent`, `agent_id`); a block has no author. | The unit is the request, the scope is the folder, and the agent has no identity. |

## Decided (2026-11-14)

1. **A synced folder is drawn as a file explorer**, with an icon per file
   type, and is told apart from a Gamma folder at a glance.
2. **Detach replaces Delete, Remove the vault and Let go** as the one way
   a synced folder leaves Gamma. The disk is never touched. The dialog
   asks each time whether the files stay in Gamma as ordinary pages or
   leave it.
3. **Agents are listed in Settings**, per computer, and are chosen in the
   chat as Gamma AI is. A person with no provider and a local agent has a
   working chat.
4. **An agent is a named actor that belongs to a person**: its own name
   and icon on what it writes, a name others can mention, the person's
   permissions at most. It is no member: no row in a member list, no
   sign-in, no seat.
5. **The owner may let a co-author use their agent.** A person named on
   the computer can mention the agent and chat with it; nobody else can.
6. **There is no feedback surface.** The person asks in the chat or by
   naming the agent in a comment; the agent reads every mark and answers
   in comments.
7. **Asks answered from another device** are a switch per server, set on
   the computer, off to begin with.
8. **An agent is turned on at its computer, never from a page.** Kept
   from the built design; the reason is below.
9. **A thread is where a person and an agent talk.** The owner's
   reference is the way Claude's documents do it: the agent's question
   is a comment on the passage it is about, and the reply field carries
   a *Send to* tick, so a reply is the next instruction and "comment to
   me anywhere" needs nothing gathered first. The rules are the plan's
   "The interaction".

## The design

### 1. The explorer and Detach

- **The look.** A synced folder's root carries a mark and its computer's
  name; its contents are a tree with indent guides and one row per file,
  in the Files window and in the home listing's folder view alike
  (`FolderList` already lists directories first with name, format, size
  and date). A Gamma folder keeps its card and row.
- **Icons.** One function from a file's name to an icon, used by the tree,
  the list, quick open, tabs and search hits, with `FileGlyph` as what it
  falls back to. The candidate set is Material Icon Theme (the one VS
  Code users know); its licence, its size and whether its SVGs load one
  at a time are to be checked before it is taken, against the bundle's
  rules ([bundle.md](bundle.md)).
- **Detach.** One item in the folder's menu, for the vault's maker, in
  every window. It stops the sync and leaves the directory as it is,
  `.gamma/` aside. The dialog says how many files and how many bytes and
  asks: *Keep the files in Gamma* (each becomes an ordinary page of an
  ordinary folder, its bytes stored) or *Remove them from Gamma* (to
  Recently deleted, as now). Where the computer is offline the server
  lets go of its mark, as *Let go* does, and the computer drops its vault
  at its next round.

### 2. Agents

- **The list.** Settings → AI gains *Agents* above *Connections*: Gamma
  AI, which needs a connection, then each computer with the agents found
  on it (`catalog.probe`), whether each is signed in, a switch, what it
  may run (*Never*, *Ask*), who else may use it, and the directories it
  offers beyond the synced folders. The settings move from the vault's
  rows to the computer (vaults.db: per agent, no longer per link).
- **Turned on at the computer.** The switch, what it may run, the people
  named and the directories are set by the desktop app's own word (the
  shell's secret, as a vault's path is: `_from_shell`), in its Settings
  window. Another window lists the same rows and says where to change
  them. Turning one off is open to any window of its owner, as now.
- **The picker.** The chat's header loses the side switch. The model chip
  lists agents with their models: *Gamma AI* with the connection's
  models, *Codex on TIM-PC* and *Claude Code on TIM-PC* with theirs. It
  is drawn on every page, folder and the library, not only on a synced
  folder's files.
- **One chat, two tables.** A conversation has one agent, chosen when it
  starts. Gamma AI's stay in the chat's buckets
  ([dev/chat_history.md](../dev/chat_history.md)): the workspace's,
  saved by the client. A computer agent's stay in what `computer_chats`
  becomes: their owner's, written by the server as the turn goes, for
  any page, folder or the library. The two differ in who writes and who
  reads, which is why they are not merged; one transcript, one composer,
  one History and one export read both, and `chat/computer/`'s own
  transcript goes. The CLI's session id stays on the computer, keyed by
  the conversation. A turn of a computer's agent keeps what it has that
  a reply of Gamma AI lacks: it outlives its tab and is read by its
  numbered log.
- **The same context and tools.** A computer's agent is handed what Gamma
  AI is: the page's context, and Gamma's tools over MCP through a
  connection made for the turn (below). Its engine stays its own loop;
  it is never a model behind Gamma's tool loop, which is what
  [textfold.md](textfold.md) and the vendors' terms turn down.
- **Where it works.** In the directory of the synced folder the page is a
  file of, when that computer keeps it. Elsewhere, in a directory the
  person picks from those the computer offers, or in none: an empty
  directory of its own, where it has Gamma's tools and no files.
- **Without a provider.** The chat's setup card offers the agents found
  beside *Connect an AI service*. Metadata, translation, handwriting and
  dictation go on needing a connection.

### 3. From another server, and sessions

- **A computer is linked in Settings.** *Connect this computer to a
  server* makes the `computer` token and the channel that exist
  ([dev/computers.md](../dev/computers.md)), with no vault needed. A
  server's Settings list the computers of the account and their agents.
- **Any chat there picks an agent of an online computer**, and a
  directory as above. The one-run-per-folder rule becomes one run per
  directory.
- **Asks are relayed** when the computer allows that server: the card is
  the chat's, the answer travels to the computer by a wake, as a turn
  does. Off, a far turn declines what would ask, as now.
- **Sessions from Gamma to the terminal**: as built, by id. To check:
  that both CLIs list them.
- **Sessions from the terminal to Gamma**: History, for the agent and the
  directory at hand, lists the CLI's own sessions (Codex `thread/list`
  with its `cwd` filter; Claude Code's session files of the project).
  Picking one reads its turns into a conversation and continues it by
  resume. A session open in a terminal is not shared: Codex refuses a
  second writer, and Claude Code has no lock to ask, so the row says to
  close it there first.

### 4. Marks, and the agent's hand

- **The reader.** `feedback/entries.py` becomes the reader of a scope's
  marks, any page or folder: every open thread and every highlight, with
  its file, its place, its quote, the source line SyncTeX finds, a
  picture of its region, its note and its comments. The work-folder gate
  goes (`resolve_scope`, `require_commenter`, `agent._thread`). What
  stays as it is: anchors, source lines, pictures, the version kept
  while a thread is open, the Markdown form.
- **On disk.** A synced folder's directory holds the marks of its files
  as `.gamma/annotations/` (`open.md`, `open.json`, `img/`), kept by the
  vault's rounds, for a folder of another server too (today a remote
  vault writes none). An agent started in a terminal reads them with no
  connection.
- **The tools.** In the one registry the chat and MCP share
  (`ai_tools.py`): `list_annotations` and `read_annotation` in place of
  `list_feedback` and `read_feedback`; `comment` (a new thread on a
  passage, an area, a range of lines or a page, or a reply), `resolve`,
  and `highlight`. Gamma AI gets them by being in the registry, so it
  comments and highlights too. An assistant's connection gains a level
  between View and Edit, *Comment*, which the `feedback` scope becomes.
- **The connection of a turn.** A computer's agent is handed Gamma's MCP
  endpoint with a token made for the turn: its workspace, the *Comment*
  level, gone when the turn ends. Edits of notes go through the approval
  card as Gamma AI's do. What it writes is stamped with the agent and is
  one group the changes card can take back.
- **Identity.** A row per agent, owned by an account: a kind (Gamma AI,
  a computer's agent, an assistant's connection), a name, an icon, its
  computer. A comment's `agent_id` names it, and a block an agent makes
  carries it (`properties.agent`), so a highlight says who made it. The
  author stays the owner's account, which keeps every role check and
  member list as it is; the interface shows the agent's name with *for
  tim* beside it.
- **Asking it.** In the chat, or in any comment whose *Send to* tick is
  on (`@name` ticks it). Both start one thing, a turn of a conversation.
  A comment's conversation belongs to its thread, and the thread shows
  the reply with *Open in chat*. The agent asks the same way: its
  question is a thread on the passage, and the person's reply resumes
  its session.
  `feedback_requests`, Send, the states, the *Sent* chip, the Feedback
  dialog and its three ways in, `.gamma/feedback/requests/`, the claim
  and the answers of the channel, and the runner's Send flow go. A
  turn's progress is the Background tasks row a chat turn already has.
- **A co-author.** The owner names accounts of a server on the computer.
  A named person sees the agent in the @ menu and the picker, and their
  words are the instruction of their turn; everyone else's stay data.
  Such a turn edits the files of the folder and comments; what it asks
  to run is granted one time at most (*Allow once*, never *Allow in this
  chat*). Its answers read
  *Codex on TIM-PC, for alice*, and its use is the owner's subscription.

## Is it simpler

| Goes | Stays, widened | New |
|---|---|---|
| `feedback/requests.py` and its two tables, `feedback/files.py`'s request files, the routes for requests, `frontend/src/feedback/`, the Feedback buttons, `sentChip`, the mention as a Send, the runner's claim-and-answer flow and its half of the channel | the comments, anchors, source lines, pictures, kept versions and the reader, for any page | an agent's row and its stamp on blocks |
| `chat/computer/`'s transcript and composer, the side switch, the gate that ties a conversation to a work folder's file | the drivers, the processes, the turn that outlives its tab, the asks, the session kept and resumed, `computer_chats` for any page | the picker, the turn's connection, the *Comment* level, the *Send to* tick |
| `agent_folders`, `agent_policies` per link, the agent row of the sync panel | the channel and presence | a computer linked without a vault, the directories it offers, the people it names, asks relayed |

One thing is lost: a Send's answer had a shape (`fixed`, `question` or
`unchanged` per thread) that Gamma wrote out itself, so every thread got
an answer. An agent that calls `comment` and `resolve` may skip one. The
turn's prompt names the open marks it was asked about, and the reply's
card lists those it left without an answer.

## Why an agent is turned on at its computer

Turning an agent on gives a way to run a program that edits files and
runs commands on that computer, under the person's login there. If a page
could flip the switch, anything that can act as the person in a browser
could turn a Gamma session into a program on their PC: a stolen session
cookie, a script injected into a page, a browser left open on another
device, the operator of a server someone else runs. Asked for at the
computer, that step needs the computer itself. VS Code's tunnel is
started on the machine it opens, and a machine's SSH server is enabled on
that machine, for the same reason.

It costs one switch, once. After it, the agent is used from any window.
The people who may use it and the servers whose asks are honoured are set
in the same place, since each of them widens who reaches the computer.
It does not protect against a server that lies about who is asking: the
server a computer is linked to is trusted with the agents turned on for
it, as [agent-collab.md](agent-collab.md) "Whose words run" says.

## Reversed

| Recorded | Where | Now |
|---|---|---|
| "An agent as a workspace member" declined | agent-collab.md, Declined | still no member; an actor with a name of its own, owned by a person (decision 4) |
| "The assistant as a model of Gamma's chat" declined; a switch, "not an entry of the model menu" | agent-collab.md, Declined and "The chat window gets a second side" | an entry of the picker; its loop stays its own |
| "The agent writing its own replies through MCP" declined; "The agent holds no Gamma credential" | agent-collab.md, Declined and "Whose words run" | a token for the turn, at the *Comment* level |
| "The unit of work is the feedback request" | agent-collab.md, "The answer in short" | a turn of a conversation |
| "Gamma is a feedback window"; Send | file-explorer.md, Decided 4 and layer 7 | no surface; the marks are read where they are |
| "Only the vault's maker" | agent-collab.md, "Whose words run" | the owner and the people they name |
| One agent per work folder, in the sync panel | agent-collab.md, "On the computer" | per computer, in Settings |

## In steps

Each is merged by itself. The units of each step, with their files and
tests, are in [the plan](agents-as-participants-plan.md).

| Step | Builds |
|---|---|
| 1. The explorer and Detach | the icon function and its set, the synced folder's mark and tree, Detach with its two outcomes, in every window |
| 2. Agents in Settings and the picker | the settings moved to the computer, the Agents list, the picker on every page, one conversation store, the turn's connection and Gamma's tools, the chat without a provider |
| 3. Marks and identity | the reader for any page with highlights, `.gamma/annotations/`, `comment` / `resolve` / `highlight`, the *Comment* level, the agent's row and stamp, a mention as a turn, a co-author's use; the request layer removed |
| 4. Another server | a computer linked in Settings, the choice of computer and directory, asks relayed |
| 5. Sessions | the CLI's sessions listed in History, read and continued |

## Still to decide

The plan took the cautious answer to three questions this note first
left open: a computer agent's conversation is private to the person who
started it and the agent's owner; on a page of no synced folder the
agent works in an offered directory or an empty one of its own; requests
already made go with their tables, and conversations move. The owner answered the rest on 2026-11-14: the interface says *synced
folder*; the icons are Material Icon Theme's whole set, mapped as VS Code
maps them; the marks of a file linked to disk are written there too; a
co-author is granted the agent once, asks it with `@` or the tick, and
what their turn asks to run is granted one time at most
([the plan](agents-as-participants-plan.md), decision 13). Still to read
before a co-author's use ships: what each vendor's terms say of a second
person driving the owner's signed-in CLI.
