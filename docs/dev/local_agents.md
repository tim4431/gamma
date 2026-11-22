# An agent on this computer

On the desktop app's own server, the one that keeps synced vaults, Gamma
itself can be the connector that [annotations.md](annotations.md) "The seam"
leaves to the owner. An account turns on Codex CLI or Claude Code for this
computer (Settings → AI → Local agents, through the desktop shell: "Who turns it on"), and
owns it from then on. The owner talks to the agent in two places: the chat
window's second side ("The chat's computer side"), and a comment sent to the
agent (its *Send to Codex* tick, or `@codex fix this only`: "A comment sent
to an agent"), which is a turn of a conversation of its thread, answered in
the thread, the next one sent there resuming the agent's session. A turn
runs the agent in the directory its conversation works in (a synced
folder's, one the computer offers, one of its own), with Gamma's tools, and
shows what the agent is doing as it goes. The owner may name co-authors who
use the agent too, on the files of a synced folder ("A co-author's turn").

The design and the survey of both CLIs are the research notes
[agent-collab.md](../research/agent-collab.md) and
[agents-as-participants-plan.md](../research/agents-as-participants-plan.md).
Backend: the package `gamma/local_agents/` and
`gamma/routers/local_agents.py`, with small parts in `gamma/comments.py`,
`gamma/folder_links.py`, the folder-links router and `gamma/routers/shell.py`
(the shell's word).

| Module | What |
|---|---|
| `__init__.py` | `enabled()`, whether this server runs agents ("Who turns it on") |
| `catalog.py` | the known agents, where each is on this computer, whether it is ready ("Finding the agents") |
| `procs.py` | running a CLI: its environment, no console window, a process tree that dies with the turn or the server ("Processes") |
| `events.py`, `codex.py`, `claude.py` | the drivers: one turn of an agent as a stream of plain events ("The drivers") |
| `tools.py` | Gamma's tools for a turn: this server's `/mcp` as the agent reaches it, the turn's token ("Gamma's tools") |
| `prompt.py` | what the agent is told ("The prompts") |
| `runner.py` | the turns' queues and runs ("The queue and the turn") |
| `threads.py` | a comment sent to an agent: its thread's turn, the thread's prompt's entry, its end written in the thread ("A comment sent to an agent") |
| `mention.py` | a comment's `@name` read as the agent it is sent to ("A comment sent to an agent") |
| `chats.py` | the chat's computer side: a bucket's conversations with the agent, and a thread's ("The chat's computer side") |
| `far_turns.py` | on the server that keeps a conversation, its turns another computer runs: the claim, the writes, the stops, the answers and the turn's token ("The chat on another server's page") |
| `home.py` | where a turn's conversation lives: this server's pages.db, or another server's over the channel ("Another server's chat") |
| `store.py` | what this computer keeps, in vaults.db, the people an agent serves among it ("What this computer keeps", "A co-author's turn") |
| `info.py` | what a vault says of its agent, for the links and a page's chip ("The routes") |

`__init__.py` imports only `config` and `version`: `folder_links`, the
annotation modules and the routers import the rest inside their functions,
and the runner imports `folder_links` inside its own, so no cycle forms at
import. `tools.py`, which the session middleware imports, imports
`integrations` inside `mint`.

## The words

**An agent** is the UI's word for whatever writes through an integration
token or runs on this computer for Gamma ([i18n.md](i18n.md) glossary).
**An agent's name** is `codex` or `claude`, its **label** `Codex` or
`Claude Code`. **The computer's agents** are those turned on here, each
**owned** by the account that turned it on (`set_by`): its turns and the
comments sent to it use it, and those of the people it lets use it
(**co-authors**, "A co-author's turn"), nobody else's. **The agent picked**
is the one the chat's picker shows for a bucket (`chat/agents.js`, "The
client"): a chat turn names it (`agent`) and a conversation keeps the one
it began with. **An account's agent** is the first of its agents in the
catalog's order, Codex then Claude Code (`store.agent_for`): the one a turn
that names none runs with, and on a vault the one the links and a page's
chip show (the vault maker's). **A turn** is one message to the agent and
the agent's work on it, its answer the agent's message. **The maker** is
the account that made the vault (`folder_links.created_by`). The interface
says "Codex on this computer" and calls a work folder a synced folder; the
files and every text written for an agent are English.

## Who turns it on

`local_agents.enabled()` is true when all of these hold:
`config.folder_links_enabled()` (the server keeps folders on disk);
`version.UPDATES != "mas"` (a Mac App Store build may not run code it was
not reviewed with); and either `config.shell_secret()` is set (the desktop
app started this server) or `GAMMA_LOCAL_AGENTS` is on.
`GAMMA_LOCAL_AGENTS` off (`0`, `false`, `no`, `off`) turns it off always
(`config.local_agents_setting()`: True, False, or None for unset). Tests
and the e2e server set `GAMMA_LOCAL_AGENTS=1`. On any other server every
part answers as if no agent were on: the routes below 404 and nothing
queues a turn.

An agent is the computer's, not a vault's: vaults.db `computer_agents`
("What this computer keeps"), turned on, off and let ask through `PATCH
/api/local-agents/{agent}` ("The routes"), which Settings → AI → Local agents
asks the desktop shell to send ("The desktop app"; the section is
[settings.md](settings.md) "AI"): in the desktop app on its own server
each agent found has its switch, what it may run and the directories
offered; in any other window the same rows are read-only and say to change
them in Gamma on that computer, but for its owner's switch, which turns it
off with the account's session. The account that turns one on
owns it (`set_by`); another account can turn it on only once its owner
turned it off (409 while it is on). Turning one **on** needs the desktop
app's own word when the app started this server with its secret
(`config.shell_secret`; the desktop app always does): `routers/shell.py`
`from_shell`, the `X-Gamma-Shell` header ([desktop architecture](../../desktop/docs/architecture.md)
"The shell's word"), refused with 403 "Only the desktop app may turn on an
agent on this computer." An agent that edits directories is the
computer's decision, so on the app's server a page of Gamma, which shares
its origin and session, cannot turn one on by itself. A server run with
`GAMMA_LOCAL_AGENTS=1` and no secret (the tests, the e2e server) has no
shell to ask, so there the account's own session turns one on, as it names
a path on the routes that take one ([synced_vaults.md](synced_vaults.md#the-routes)).
Letting it ask (`runs` `ask`, "Asking in the chat", "Asking on this
computer") takes the shell's word the same way. Turning it **off** needs
its owner or the shell's word, never unchecked (`shell_strict`: a server
with no secret takes its owner alone), so the person at the computer turns
off an agent whose owner is signed out of the app or gone; setting it back
to `never` needs its owner. An account deleted turns its agents off
(`runner.release_account`, from `workspaces.delete_account_workspaces`).
The agent must be known and ready, asked afresh at the PATCH. The
directories the computer offers its agents beyond its vaults (`agent_dirs`,
`PUT /api/local-agents/dirs`) are named by the shell alone too; leaving one
out or renaming one takes an account with an agent on here (else the
shell's word). An offered directory never overlaps a vault: a new path
inside or around a vault, or around another offered directory, is
refused, and so is a vault inside or around an offered directory
(`folder_links.check_vault`), so one directory has one queue.

Where an agent works: in the vault of the work folder the page is in, kept
here (a work folder of this server, or one of another server this computer
is shown on, `folder_links.work_vault`), else in an offered directory or
one of its own ("The chat's computer side"). A chat turn names its agent,
and a comment sent to an agent the agent it is sent to ("A comment sent to
an agent").

Turned off, every turn of an agent stops (`runner.stop_agent`), it may run
`never` again and the commands
allowed in its chats go; set back to `never`, the asks its turns hold are
declined (`runner.runs_never`). Each change tells the open pages of every
work folder here `{t: "vault"}` and the channels announce it
(`folder_links.agents_changed`). A vault's turn stops when the vault is
paused, relinked or removed, and when its push is lowered so it is no work
folder any more (`folder_links._stop_agent`, called from `set_paused`,
`relink`, `delete_link` and `set_push`); a relinked vault forgets the
sessions of its threads and conversations (a session belongs to the
directory it ran in: `store.forget_sessions`), and an agent stays on
through all of these.

## Finding the agents

`catalog.AGENTS` holds the two, in the order the panel offers them:

| Name | Label | Executable | Status command | npm script |
|---|---|---|---|---|
| `codex` | Codex | `codex` | `codex login status` | `@openai/codex/bin/codex.js` |
| `claude` | Claude Code | `claude` | `claude auth status` | `@anthropic-ai/claude-code/cli.js` |

- **`command(name)`**, the argv prefix that starts it, or None. In order:
  `GAMMA_AGENT_CODEX` / `GAMMA_AGENT_CLAUDE` (a JSON list, an argv prefix,
  or a plain path); `shutil.which(exe)`; then `FALLBACKS[name]`, the places
  the installers use, since an app opened from the Dock or the Start menu
  has no login shell's PATH: on Windows
  `%LOCALAPPDATA%\Programs\OpenAI\Codex\bin\codex.exe` and
  `%USERPROFILE%\.local\bin\claude.exe`; elsewhere `~/.local/bin/<exe>`,
  `/opt/homebrew/bin/<exe>`, `/usr/local/bin/<exe>`,
  `~/.npm-global/bin/<exe>`. A Windows `.cmd` or `.bat` (an npm shim) is
  started as `[node, <shim dir>/node_modules/<npm script>]`, `node` the
  shim directory's `node.exe` or the PATH's; never through `cmd /c`, which
  parses the arguments again. A shim whose script is missing gives the
  problem "Codex is installed through npm in a way Gamma cannot start:
  install the standalone Codex."
- **`probe(name, fresh=False)`** → `{name, label, found, version, ready,
  problem, at}`. It runs `<cmd> --version` and the status command through
  `procs.run_quick` (15 s each, `PROBE_TIMEOUT_S`), which use no model.
  `version` is the first line of the output. Codex is ready when the
  status exits 0 and says "logged in" (not "not logged in"); Claude Code
  when the status's JSON says `loggedIn: true` (else exit 0 and "login
  method" in the text). Codex on Windows also needs its sandbox:
  `codex.sandbox_ready(argv)` asks a short-lived app-server
  `windowsSandbox/readiness` (a Codex too old to know the request is let
  through). `problem` is one English sentence, "" when ready: "Codex is not
  on this computer.", "Codex is not signed in on this computer: run `codex
  login` in a terminal.", "Claude Code is not signed in on this computer:
  run `claude` in a terminal and sign in.", "Codex's Windows sandbox is not
  set up on this computer: open Codex once in a terminal and finish its
  setup.", "Codex did not answer `codex --version`." The answer is kept
  `PROBE_TTL_S` (600 s); one lock per agent, so callers at the same time
  share one probe, and one that finished after a call began answers it,
  fresh or not. Never raises. It runs processes: a write and a run use it,
  never a read path.
- **`cached(name)`** never runs a process: the kept probe, and when it is
  missing or stale, or a ready agent's models were listed `PROBE_TTL_S`
  ago or never ("Its model, its typing…"), one background re-probe (a
  daemon thread, one per agent at a time). With nothing kept yet it answers `found: null, ready: null`,
  "still checking". A listing and a page's chip read this.
- **`probes(fresh)`** is both, in order.

## The queue and the turn

`runner.register()`, from `app.create_app`, registers `runner.shutdown` at
exit. Every turn, a chat's ("The chat's computer side") and a comment
thread's ("A comment sent to an agent"), waits in the queue of the
directory its agent works in and runs as a `_ChatTurn`, whose plumbing
this section describes.

- **The queue.** A turn is queued under `(ws, turn id)` (`runner.queue_chat`;
  a turn of another server's page under `(link id, turn id)`,
  `queue_remote_chat`, so it never reads as a workspace's here) in the
  queue of its directory (`runner._place`, the path as the file system
  compares it). One worker thread a directory takes its queue in order, one
  turn at a time, so a directory never has two agents in it; at most
  `MAX_RUNS` (2) turns at once on this server (a semaphore). A worker
  forgets itself in the same locked step that finds its queue empty. The
  module lock guards the queues, the workers, the active turns and their
  stop events, and nothing else: no call into `store`, `catalog`, `chats`
  or a driver is made while it is held.
- **The snapshot.** Before the agent starts, a snapshot of the directory
  (`_snapshot`: `{path: (size, mtime_ns)}`, leaving out `.git`, `.gamma`,
  `node_modules`, `__pycache__`, `.venv`, `venv` and every directory whose
  name starts with a dot; at most 20,000 files and 3 s, and whether the walk
  got through). A stop set meanwhile ends the turn before any process
  starts. Then `events.start(agent, argv=catalog.command(agent), cwd=<the
  directory>, prompt=<the prompt>, images=<the pictures' absolute paths>,
  session=<the one to resume, or "">, ...)`, its pid recorded. A resumed
  turn that ends badly before any step, not stopped and not past its
  deadline, is started once more afresh (Claude Code has no fallback of its
  own; Codex falls back to a new thread itself), unless Gamma's tools did
  not start for it (the done's `gamma_down`, `events.GammaDown`: afresh
  would fail alike, and replace the session; `_ChatTurn._resume_refused`) or
  another process holds the session (the done's `busy`,
  `events.SessionBusy`: afresh would fork it). A session a terminal or an
  editor has open fails the turn with why before any process starts
  (`sessions.held`, "Sessions started elsewhere").
- **The watchdog**, one thread a turn, waits for the turn's stop or the
  deadline, `RUN_TIMEOUT_S` (1800 s, `GAMMA_AGENT_RUN_TIMEOUT`) counted
  from the turn's start, not from the queue's wait, and calls
  `turn.stop()`.
- **The steps in words** (`_said`): `Reading main.tex`, `Editing
  sections/intro.tex`, `Running latexmk` (the program's name alone, never
  its arguments; a command a CLI wraps in a shell, as Codex on Windows runs
  each through `powershell.exe -Command`, names the program inside it),
  `Searching the sources`, `Reading Gamma: read_page`, `Working`. What a
  vault's links show of a turn at work is this line (`runner.at_work`,
  "The chat's computer side"), which names a file only when the vault
  tracks it (`tracked`, members see it already): any other reads "a file in
  the folder", and a path outside the folder "a file outside the folder",
  so a file the person left out of Gamma is never named to the workspace.
  The agent's message itself names every file inside the directory by its
  relative path.
- **What changed**: a second snapshot against the first, added, changed
  and deleted files by path; when either walk was cut short, only the
  files both reached are compared, so a file past the cut never reads as
  changed. The build's outputs (`.pdf .aux .log .synctex.gz .synctex .fls
  .fdb_latexmk .out .toc .bbl .blg .bcf .run.xml .xdv .dvi .nav .snm .lof
  .lot .idx .ind .ilg .bak`) are told apart from the sources: `changes` is
  the sources (at most 200), `built` whether an output changed. The disk
  says what changed, not the events: Codex on Windows edits through shell
  commands and reports no patch.
- **A stop** other than the person's fails the turn with what the vault is
  now, or as the stop said it (`stop_link`'s `why`: a vault being removed,
  one its other server detached): "Stopped: the agent was turned off on
  this computer.", "Stopped: the synced folder was paused.", "Stopped: the
  synced folder is no longer kept on this computer.", "Stopped: the synced
  folder was detached.", "Stopped: the synced folder changed on this
  computer.", "Stopped: the directory is no longer offered on this
  computer.", "Stopped: the agent's owner no longer lets you use it." or
  "Stopped: Gamma is closing." (`runner.STOPPED`).
- **A failed turn.** The deadline → "Codex took longer than 30 minutes and
  was stopped."; else "Codex could not finish: <the driver's error, first
  line>", the directory's paths made relative, the home directory written
  `~` and any other absolute path "a file outside the folder" (`_redact`).
  The whole error and the agent's stderr tail go only to the turn's row
  (`agent_turns.error`, at most `MAX_ERROR`).
- **The end.** The turn is closed (`Turn.close`: its CLI killed even when
  nobody read its events, as after an error between its start and the
  reading) and its row ended, always (a `finally`); an unexpected
  exception fails the turn with "Gamma could not run Codex.". A turn whose
  end could not be written is left open on this computer, and the next
  start ends it ("Recovery").

## The prompts

`prompt.build_chat` writes a chat turn's prompt and `prompt.build_thread`
a thread's, in English ("The chat's computer side", "A comment sent to an
agent" say what each holds). Gamma writes every line itself: the message,
the selections, the thread's comments and their quotes are data, each a
JSON string inside one fenced `json` block, never pasted as Markdown, so
nothing a member wrote reads as a heading or an instruction of Gamma's.
Three backticks in a row in any string get a zero-width space before the
third, so a body cannot close the fence. Both say where the agent works
and how: in a vault, change the sources there, a comment without a
`source` found by its `quote` and a `was` the text a range of a text file
was commented on before the file changed under it; Codex is told to
rebuild with the project's own build (latexmk, make, a script), its
commands running in a sandbox with no network, Claude Code that it cannot
run commands and the owner's own build picks up its changes (with asking
on, that each command is asked of the owner first); never edit anything
under `.gamma/`; open a comment's `pictures` when it is about how
something looks. A thread's entry (`prompt._item`) is `{thread_id, file,
place, page, quote, note, was?, source: {file, line, end?, as: "current" |
"commented"} | null, pictures: [".gamma/annotations/img/<key>.jpg"],
comments: [{by, is_owner, agent, at, text, deleted?}]}`, `was` only on an
entry that has one ([annotations.md](annotations.md) "An entry"),
`pictures` the entry's `picture` and `before` that exist under the
directory (`prompt.picture_paths` gives them to the driver as images), a
tombstone's `text` "". The agent answers in prose: its final message is
the chat's reply, or the thread's when it wrote none there. Every text of
the agent's that Gamma stores to be read rendered is `prompt.inert` first
("Trust").

A chat turn whose message ran one of the owner's skills gets a section
before `## The message`, `## The skill /name`: a line saying the owner
ran it and that its instructions are part of the owner's request, then the
instructions in a fenced `markdown` block, rendered at the claim from the
account's catalog by the skill's id (`chats.skill_of`; a far turn's come
rendered with its claim, `turn.skill`). A skill gone by then fails the
turn: it never runs the arguments without the instructions. The earlier
messages a fresh session is given write such a message as `/name args`
([skills.md](skills.md#a-computers-agent)).

## A comment sent to an agent

A person sends a comment to an agent by ticking *Send to Codex* under the
comment's field, or by typing `@codex` there, which ticks it ("The
interaction" of [the plan](../research/agents-as-participants-plan.md),
step 3.5; the field is [comments.md](comments.md) "Sent to an agent"). The
comment is stored as any comment, and is also the instruction of a turn of
that agent: a turn of the account's **conversation of the thread**, run as
a chat's turn is ("The chat's computer side"), answered in the thread under
the agent's name. A new comment on any passage, ticked, is a turn about
that passage; nothing is gathered first. `gamma/local_agents/threads.py`
holds the turn and its end, `mention.py` the `@name`.

- **The request.** `POST /api/pages/{id}/comments` and `PATCH
  /api/comments/{id}` take `to`, an agent's id ([comments.md](comments.md)
  "Who wrote a comment": `computer:<computer>:<name>`), which the field
  names from the agents the person may use on the page (`chat/agents.js`
  `commentAgents`: this computer's agents the account turned on, else the
  agent of the computer of the account's that keeps the page's work
  folder, as the chat's picker has them; `GET /api/local-agents` names
  each agent's `id`, `agents.here`). A field that shows the tick always
  sends `to`, `""` while it is unticked (a comment among people, whatever
  its text names); a client that sent no `to` (an edit, a field with no
  tick, an older client) has its `@name` read as `to` (`mention.to_of`: the
  names the comment newly
  mentions outside code, for an edit less those of its text before, among
  the catalog's; the agent of that name of the computer that keeps the
  page's work folder where no vault here keeps it, else this computer's
  that the writer turned on), so the server decides once. Only a person's
  own comment in Gamma is sent: a token's `to` is refused, one made
  through a share is never read, and the agents' own comments name none.
  The route (`routers/comments.py` `_written`) puts the agent's name on
  the event when the comment's text names it (`agent_named`), so the Inbox
  tells no member of that name, then `threads.start` makes the turn. A
  `PATCH` that leaves the text as it is, with `to`, sends the stored
  comment to that agent again (the event's kind `again`, wherever the
  comment stands in the thread, under a new message id): the thread's
  *Send again* after a refusal ([comments.md](comments.md) "Sent to an
  agent").
- **The refusals** (`threads.REFUSALS`, each with an English `why`, the
  comment stored all the same): `closed` (a resolved thread: reopen it
  first), `not_newest` (an edit of a comment that is not the thread's
  newest), `viewer` (no owner or editor of the workspace), `writes` (the
  server or the workspace takes no writes), `off` (this computer's agent
  not turned on by the writer), `elsewhere` (an agent that does not work
  on this page: another computer's, or one this server cannot reach),
  `offline` (the computer that runs it is away: the turn would wait
  unseen), `paused` (its synced folder is paused), `not_ready` (its
  problem), `busy` (a turn of the thread's conversation is still to end),
  `folder` (a co-author's on a page of no synced folder) and `other`. A
  far computer's `offline` and `paused` name it (`computer`), so the client
  words every code but `not_ready` and `other` itself (`comments.js`
  `turnRefusal`), the server's `why` only for those two. Every answer of
  `threads.start`, begun or refused, carries `agent_id`, the `to` it
  answers, which the thread's *Send again* sends.
- **The turn** (`threads.start`): the thread's conversation of the
  account (`chats.of_thread`, `computer_chats.thread_id`, step 62 of
  [migrations.md](migrations.md)), its bucket the thread's page, where it
  works chosen as a chat's (`chats.place_of`, for the thread's
  conversation's `dir`, else the page's chat's with that agent: the
  synced folder's directory, an offered directory, or one of its own); a
  conversation of another agent or another directory is archived and a
  new one begun, as in the chat. The person's message is the comment's
  text with the agent's `@name` taken out of its prose (`mentions.without`)
  and names the comment (`comment_id`), a new comment's id its id, so a
  retried POST makes no second turn; the agent's message names the
  agent that answers in the thread (`agent_id`: `agents.here`, or the far
  computer's agent's, `to` itself). It is queued for this computer's runner
  (`runner.queue_chat`), or, for another computer whose agent `to` names
  (`far_turns.remote_of`, refused while it is offline), written to wait for
  it (`chats.begin(remote=True)`, working where the thread's conversation
  or the page's chat with it works, `far_turns.far_dir`) and that computer
  woken: it claims it over its channel as a chat's turn, the claim
  carrying the thread (`far_turns.claim`'s `thread`, its entry with its PDF
  place, whose source line it computes from its own disk). The answer
  is `{agent, conversation_id, turn_id, state}`.
- **The prompt** (`prompt.build_thread`): the comment as the request; what
  the page is (`about`), and the thread as data, its entry as
  `list_annotations` and `read_annotation` give it (`threads.entry_of`:
  its file, place, quote, `source` line, `pictures` where a vault's files
  hold them, the comments with their authors, `is_owner` on the person's)
  inside the JSON block with `message`; where it works, as a chat's; with
  Gamma's tools, to answer in the thread with `comment` and its
  `thread_id` (else its final message is written there), to resolve the
  thread only once nothing is left to decide, and to ask a decision with
  `comment` on the passage it concerns and end its turn. The thread holds
  what was said, so no earlier messages are pasted. A chat turn's prompt
  says the same of asking (`prompt._asking`).
- **The next ticked reply** in the thread is a turn of the same
  conversation, resuming its agent's session as a chat's follow-up does
  (`store.conversation_of`). The first ticked reply in a thread an agent
  opened in a turn of this computer (to ask a decision) begins the
  thread's own conversation with the session of the conversation that
  opened it (`threads.opener_of`, by the agent messages' `threads`;
  `chats.begin`'s `resume` copies its `agent_conversations` row while it
  waits on no turn and has the same directory, agent and owner), so the
  answer reaches the session that asked.
- **The end** (`threads.settle`, from every write that ends a thread's
  turn: `chats.write_reply` of a turn of this computer's, `write_run`,
  `stop_run` and the far turns' other ends on the server that keeps the
  conversation): when the agent wrote no reply in the thread since the
  turn began (a comment of its id, for the account, through the
  `comment` tool and its turn's token), Gamma writes the turn's last
  message there as the agent's reply (`annotations.hand.reply`, signed as
  the agent for the account), under an id of the turn's (`home.reply_id`),
  so it is written once however often the end is; a failed turn writes its
  error (else "Codex could not finish."), a stopped one "Stopped before
  Codex answered.", one done without a word "Codex finished without a
  reply." A thread sent is a thread answered. Those notes keep
  their English for the readers that know no more (an MCP client's
  `read_annotation`, the annotation files); the comment routes add to each
  what became of its turn (`turn_end`, `{state: "failed" | "stopped" |
  "empty", error}`, `threads.ends_of`: read off the turns of the threads'
  conversations, a note's id being its turn's), so the app draws it as the
  turn's state in the reader's words ([comments.md](comments.md) "Sent to
  an agent"). No column holds it: a conversation deleted since takes it
  along, and its note reads as the agent's words again. The note's event
  carries the outcome too (`turn_end`, `hand.reply`'s), and a stop's tells
  the account it was for nothing in the Inbox: it, or its agent's owner,
  pressed Stop.
- **The threads it opened**: a turn of this computer's records on its end
  the threads its agent opened for the account since it began running
  (`threads.opened`, the agent message's `threads: [{id, page_id,
  block_id, text}]`, a chat's turn's too), which its answer card lists.
- **What a reader sees**: each thread the comment routes answer carries
  `agent_chat` (a reply sent to no agent too, so its thread's chip stays
  while a turn works), the session account's conversation of it (`{conversation_id,
  agent, label, turn: {turn_id, state, asking, waiting, computer} | null}`,
  `threads.chats_of`), for nobody else; `GET /api/local-agents/chat?bucket=
  &thread=` reads that conversation (the thread's *Open in chat*), and its
  turns are stopped and answered by the chat's routes. A thread's
  conversation is never its page's active chat nor in History.
- **The Inbox**: the agent's reply reaches the person who sent the thread
  as a reply does (an agent's write is someone else's to its own account),
  and a member named codex is told nothing of a comment sent to the agent,
  nor of the later comments of its sender in that thread
  (`inbox._agents_of`, through `threads.agent_named`).

## A co-author's turn

Step 3.6 of
[agents-as-participants-plan.md](../research/agents-as-participants-plan.md)
(decision 13): the owner of an agent here names, on the computer, the
people who may use it, and a person named talks to it as the owner does,
with the chat's picker, the comment field's *Send to* tick or `@codex`, on
the files of a synced folder alone. Their use is the owner's subscription:
the desktop app's question before a person is named says what that person
can do and ends with the one sentence of consent the vendors' terms ask for
(both forbid making a personal plan's account available to anyone else:
[the research note](../research/agents-as-participants.md) "A co-author's
use and the vendors' terms").

- **Naming them.** vaults.db `agent_people` ("What this computer keeps"):
  per agent, a person of a workspace by the account's id there, on this
  server (`server` "") or on a server one of the owner's channels is
  shown on (`server` that channel's `remote_url`). `GET
  /api/local-agents/people` lists whom the owner may name: the editors and
  owners of each workspace of this server it edits with others, and of
  each channel's workspace there, which that server answers over the
  channel (`GET /api/computers/people`, [computers.md](computers.md));
  `POST /api/local-agents/{agent}/people` names one with the shell's word
  and the agent's owner (the desktop app's `agent-person` ask, which reads
  the person from that listing itself); `DELETE` takes one off with the
  owner's session (else the shell's word), and their turns of it that run
  here stop (`runner.stop_person`, "Stopped: the agent's owner no longer
  lets you use it."). Turning the agent off deletes its people: turned on
  again, by anyone, it serves none of them. Each change tells the open
  pages and the channels (`folder_links.agents_changed`).
- **Where they see it.** On this server, the listing's agent carries `lent:
  {by}` for a person it serves in the session's workspace while its
  owner edits that workspace (`store.lent_to`, `usable`), and the chat's seat (`chats.place_of`)
  gives a co-author the `vault` place alone: a page of no synced folder,
  an offered directory and a directory of its own are never theirs (404,
  and `folder` for a comment's turn). On another server the announce names
  each assistant's `people`, and that server shows a person it names the
  agent of the computer that keeps the work folder ([computers.md](computers.md)
  "A co-author's agent"), so the picker, the tick and the `@` menu offer
  it there.
- **Their turn.** Their own conversation (`computer_chats.user_id`), whose
  `owner` is the agent's owner (pages.db step 63): a conversation never
  changes whose agent it is (`chats.begin` archives one of another
  owner's), a far turn is given only to the owner's computer, and the
  owner reads it too (`chats.readable`: read, stop, answer, export and the
  thread's chip with `for`, never open or delete it): the owner's History
  lists the person's chat conversations with the agent under their name
  ("bob's chat with Codex", `chats.lent_history`, `GET /chat/history`'s
  `lent`), the chat shows one in place of the owner's own (`GET
  /chat?conversation=`, `chats.lent_one`, which names the `person`), and
  the owner's Background tasks list the person's turns ("Codex on this
  computer, for bob: …", `params.person`). The turn is the
  person's: the token is minted for them, the prompt names them as the one
  whose words are the instruction, the editor check is theirs, and the
  computer checks again when the turn is taken that the owner still names
  them, that the owner still edits the workspace and that it works in the
  vault (`home.person`, `lent`, `serves`, `editor`; a far turn's claim
  names the person, `for`). What it asks is allowed once
  at most ("Trust"): each ask is marked `once`, so the card offers *Allow
  once* and *Decline* alone, `chat` is 400 "A co-author's turn is allowed
  once at most.", no grant is looked up or kept, and the ask is answered by
  the person in their chat or by the owner, in the chat's card or on this
  computer (a card of `computer_asks` with `for`). A far co-author's turn
  asks only where the link relays asks to that server, once at most there
  too ("Asking on this computer").
- **What it writes** is the person's, signed as the agent: a comment's
  `author` is the person and the reader names the agent's owner
  (`agent_owner`, gamma/agents.py `Agent.owner`, kept with the comment as
  it was when it was written, pages.db step 64), a block's stamp keeps
  `owner` beside `for`. The byline reads *Codex on TIM-PC, for bob*, its
  title *Written by alice's Codex on TIM-PC, for bob*; the strip reads
  *alice's Codex on this computer*.

Gamma AI and connections are not lent this way: a connection is its
account's alone.

## Another server's chat

Once this computer is shown on another Gamma server (a NAS:
[computers.md](computers.md), a vault of that server or a link made in
Settings), the agents on here for the account the channel belongs to are
that account's on **every bucket of that server's workspace** (a synced
folder's file or not, a library PDF, a note, a folder, the library), in
any window: its chat turns and the comments it sends them run here and
answer there, taken as turns over the channel ("The chat on another
server's page", "A comment sent to an agent"). This is step 4c of the
research note and step 4.2 of
[the plan](../research/agents-as-participants-plan.md).

- **Turning it on** is the computer's (`PATCH /api/local-agents/{agent}`,
  "Who turns it on"); nothing is set per folder. The shell's question
  before an agent is turned on says what a server this computer is shown
  on can then do with it; showing this computer on a server asks nothing
  of its own. The first token minted for an origin over plain `http://`
  is asked about already (the shell's `beforeMint`, once per origin of the
  local network, each time for a public host an existing link keeps; a
  new link to a public host over plain http is refused,
  [https.md](https.md#plain-http)). A turn there asks on this computer
  while its agent may ask, over https alone, and there too where the link
  lets that server answer ("Asking on this computer").
- **What that server learns.** The channel's announce names the
  computer's agents (`assistants`, each with `enabled`, on for the
  channel's account, and `runs`) and the directories offered them by id
  and name alone (`dirs`, `channel.offered`, while the account has an
  agent on here; never a path), never a vault's agent; the NAS keeps them
  with the computer's row (`registry.seats`), so it lists the computer's
  agents while it is away, shown offline, and queues nothing for it then.
  It is **per account**: one member's computer serves only its own
  account's turns (and the co-authors its owner named, "A co-author's
  turn", on a synced folder's files alone).
- **Where a turn works** is named by the NAS by an id, never a path (the
  listing's and the claim's `dir`, `far_turns.dir_of` there) and resolved here
  (`home.far_place`): `vault`, a work folder of that server, the vault of
  it this channel's account made here (`channel.vaults_of`,
  `folder_links.work_vault`: a vault of another channel never answers for
  it); `offered`, a directory of `store.dirs` while the channel's account
  has an agent on here; `own`, an empty directory of its own,
  `<data>/agents/<channel id>/<conversation id>/` (`home.far_dir`, removed
  with the channel, `home.remove_far_dirs`, and at start once the channel
  is gone, `chats.sweep_dirs`: that server never says a conversation
  went). A kind or an id of another shape, a work folder no vault of this
  channel keeps, a directory not offered: the turn is claimed and fails
  with why ("The synced folder is no longer kept on this computer.",
  "The directory this conversation works in is no longer offered on this
  computer.", "That server named no directory this computer offers it:
  Gamma ran nothing."); the runner queues it in a queue of its own
  (`runner.queue_remote_chat`), so it waits behind no directory's turns.
- **Gamma's tools** of that server come with the claim (`tools: {url,
  token}`: a turn token that server minted for the account the turn is
  for, and its MCP address, [computers.md](computers.md) "The agent's
  turns there"); this computer takes them only where the address's origin
  is the one its channel speaks to that server at, its path `/mcp`
  (`HttpChatHome.gamma`), and the token reaches the agent's process alone,
  in `events.TOKEN_ENV`, as a local turn's ("Gamma's tools"). An address
  at another origin is never given the token: the turn runs without the
  tools and its message says why first (`home.FAR_TOOLS`, the turn's
  `preface`). That server deletes the token at the turn's end; this
  computer never keeps it.
- **The conversation's home** (`local_agents/home.py`): a turn of this
  server's reads and writes its conversation through `LocalChatHome`, a
  turn of another server's through `HttpChatHome`, which speaks the
  channel's routes with the link's computer token: the claim (one locked
  write there keyed by the run id minted here, so the same run claiming
  again holds it and another is refused), the turn's message written at
  most every `home.WRITE_EVERY_S`, and its end. A thread's turn's claim
  carries the thread's entry with its PDF place's page, box, rects and
  document; in a vault its **source line** is computed here
  (`annotations.sources.source_in_vault`: the page's one PDF row in the
  vault's files, its hash the place's document and its size and time
  unchanged, and the sources on disk), and its **region pictures** are
  fetched from `GET /api/computers/threads/{id}/picture?at=now|before`
  ([computers.md](computers.md)), at most 16 MiB each, into the directory's
  `.gamma/annotations/img/` (its `.gitignore` written first), which the
  work folder's rounds prune as they prune a local one's, reading that
  server's annotations, but never while a turn works there
  ([annotations.md](annotations.md) "The files"). Outside a vault the
  claim's entry goes to the prompt as it came, its pictures none. The
  agent answers the thread with its token there; a turn that wrote no
  reply is answered there by the NAS at its end (`threads.settle`).
- **After the turn** in a vault a push round starts at once when files
  changed (remote vaults get no fast rounds), so the NAS sees the rebuilt
  PDF; none while the vault is paused, choosing its files or gone (the
  next round after resume pushes).
- **Recovery.** An open remote turn is failed through its home ("Gamma
  stopped before Codex finished.") at start and again whenever that
  channel's announce succeeds, so one left open while the NAS was away is
  failed once it is back; a claim whose answer never came is recorded
  open for that (vaults.db `agent_turns`, its `ws` and `link_id` the
  channel's id). A computer's token that goes (revoked, replaced,
  expired) fails on the NAS the turns its runs held ("The computer's
  channel was taken back before Codex finished."); *Remove* in Settings
  stops the turns of that server running here (`runner.stop_channel`,
  "Stopped: this computer no longer answers that server."); *Stop showing*
  leaves the computer's agents on, and that server, told nothing more of
  this computer, gives it no work.

### The chat on another server's page

On any bucket of such a server the chat's computer side works too, in
any window of the account on the NAS (step 4d, step 4.2 of the plan): the
composer's chip lists *Codex on TIM-PC* beside Gamma AI for each agent on
of each of the account's computers shown there (those keeping the page's
work folder first), and a message is a turn that runs on the computer
picked, its reply, steps and changed files coming back into the
conversation, which the NAS keeps (pages.db `computer_chats`, the
account's alone, its `computer` the computer it talks to), so it reads
while the computer is off.

- **The seat.** The chat routes (`routers/local_agents.py`, registered on
  every server) take the agent a request names (`agent`: this computer's
  by its name, another computer's by its id, `computer:<computer>:<name>`),
  else the active conversation's while its computer still offers it, else
  the host's own vault, else a computer that keeps the bucket's work
  folder, else this server's own agent, else the first far seat
  (`far_turns.remote_of` → `registry.seat_of`); none: 404, a token or the
  guest 403, as locally. A conversation keeps its computer as it keeps
  its agent: another one picked begins a new conversation
  (`chats.begin`).
- **The strip** (`GET /api/local-agents/chat`, `computer.remote: true`)
  names the computer and its presence (`computer`, `online`, `since`),
  offers the models the computer announced with its assistants (`models`,
  at most 32) and the directories it offers (`dirs`, `[{id, name}]`, which
  a conversation that begins picks among: the composer's folder chip, as
  locally),
  says where the agent works (`dir`: the work folder by its name, an
  offered directory by its id and name, null for one of its own) and why
  an online computer's agent is not ready. While the computer is off the
  composer holds a message (*TIM-PC is offline: send this once it
  connects.*), and the NAS refuses one (409): nothing is queued for a
  computer away. With no turn to poll, the side reads the conversation
  again when the page's computers, read again on the account's nudge
  (`vault/usePageComputer.js`), say the computer came back or went away,
  or its agent got ready or not (`useComputerChat`).
- **A turn.** `POST /chat/turns` checks the computer is online, the work
  folder not paused there, the agent ready, the model, effort and speed among
  the announced ones and `dir` among the directories offered
  (`far_turns.far_dir`: the work folder where that computer keeps it, else the
  one named, else the conversation's while that computer still offers it,
  else one of its own, so a conversation whose directory is no longer kept
  or offered is archived and a new one begins; a co-author's turn outside
  a synced folder is refused), then writes the person's
  message and the queued agent message (`chats.begin(remote=True)`) with
  a pages.db `computer_chat_runs` row (the turn, its conversation and its
  bucket, the account, the turn's agent and the computer last known to
  take it, the holder and run once claimed, a `seq`, a `stop` flag, its
  state, the turn's token once claimed, the answers given here to what it
  asks, when queued and started), and
  wakes the computer (`{wake: ["chat"]}`). A computer is offered only the
  turns of its own conversations with it; a claim for an agent off there
  leaves the turn queued.
- **On the computer** the channel thread asks `GET /api/computers/chats`
  (after an announce, on a wake, at least every `CHATS_EVERY_S`, 180 s,
  while the channel's account has an agent on here), queues each turn
  where it works (`home.far_place`) under `(channel id, turn id)`, claims
  it for a run it names (the message, the selections with their places,
  the last 6 messages, the model, effort and speed, what the bucket is, where it
  works, Gamma's tools), computes the selections' source lines from its
  disk in a vault, resumes the conversation's session from its own
  vaults.db as locally, runs the agent confined as a local turn (asking
  only while its agent may ask, on this computer: "Asking on this
  computer"), and writes the agent message back at most every second,
  each write with the next `seq` (an older one changes nothing: `stale`),
  the last always sent, retried while the NAS cannot be reached, a lost
  one by the next flush (`home.missed`; one refused there waits for the
  next change); a 409 stops the turn. The listing's `steers` are the
  messages steered into its turns there (`far_turns.steer_run`, kept in that
  server's memory and handed out once, `far_turns.steers_of`), each given to
  its turn here (`runner.steer_relayed`); the message it writes back
  carries their `steers` (`home.CHAT_FIELDS`, checked there by
  `chats.steered_of`). One a restart there loses, or one the agent never
  takes, goes as the next turn from that server's chat.
- **Stop**: a queued turn ends at once; a running one is flagged and its
  computer woken (it stops it and writes its end, or, for a turn it no
  longer runs, ends it there under its run); a running one whose computer
  is offline, or a second Stop, ends it at once, its run's next write
  refused. Stop is the account's on its own turn's row, with no seat
  needed, so a turn stays stoppable after the agent is turned off, the
  token goes or the page is deleted.
- **What the agent asks** is answered on the page where the computer's
  link relays asks to that server (step 4.3: the message's `asks`, the
  chat's card with *Allow once* and *Decline* alone, as a local turn's,
  "Asking on this computer"); else the page says *Waiting for your answer
  on TIM-PC* while the message's `waiting` is true and draws no card, and
  an answer is refused (409 "Codex no longer waits for that.").
- **Not on another server's page**: *Continue in a terminal* and *Show in
  folder* (the strip's ⋯ menu is hidden; `/chat/terminal` 404), and the
  passage chips' source lines (`/chat/sources` answers nulls; the turn's
  prompt still gets them, computed on the computer in a vault).
- **Background tasks** lists the account's queued and running remote
  turns from `computer_chat_runs`, named with the computer that holds
  them or was picked for them (*Codex on TIM-PC: main*), in the phase
  `asking` while an ask waits (`there` while the message says `waiting`),
  Stop the same route; a row's
  `params.agent_id` picks that computer's agent when it opens the chat.
- **Recovery**: an open remote turn is failed through its home at start
  and whenever its channel's announce succeeds again; a computer's token
  taken back fails the turns its runs hold and the queued ones no other
  token of that computer there takes (a link's new token announces before
  the old one is revoked); an announce that turns the turn's agent off,
  drops a person it served, or no longer keeps its work folder or offers
  its directory fails the turns queued for that computer
  (`registry._refusal`: "Codex was turned off on TIM-PC.", "TIM-PC no
  longer keeps this synced folder.", "TIM-PC no longer offers that
  directory."), and so does the computer when one waiting there finds its
  agent off ("Codex was turned off on this computer."), so none runs later
  unasked. The watcher of a remote turn never waits on a network write (a
  writer thread flushes), so Stop and the deadline stay prompt.

## The chat's computer side

For an account with an agent on here, a chat with the agent picked in the
chat's chip (a turn's `agent`; with none, `store.agent_for`) belongs to any
bucket the chat has: a page, a folder, the library (`home`), as Gamma AI's
chats do. A conversation keeps the agent it began with (decision 10 of the
plan): the read of a bucket for another agent (`agent`) shows no
conversation, and that agent's first turn archives the active one and
starts its own (`chats.begin`). The agent
works in one directory a conversation (`chats.place_of`, decision 14 of
[the plan](../research/agents-as-participants-plan.md)):

- **the vault's**, when the bucket is in a work folder a vault here keeps
  (a file of it, a folder in it, the work folder itself; the vault the
  account made first when two keep it). The vault's maker reads its own
  conversations there with no agent on;
- else **an offered directory** the conversation names (`store.dirs`,
  vaults.db `agent_dirs`, which Settings sets with the shell's word);
- else **an empty directory of its own**, `<data>/agents/<ws>/<conversation
  id>/` (`config.AGENTS_DIR`, `chats.own_dir`), made at the conversation's
  first turn and removed with it (`chats.delete`; at start,
  `chats.sweep_dirs` removes those of conversations gone some other way: a
  page deleted for good, an account no longer a member, a workspace gone).

A conversation's directory is chosen when it starts and kept with it
(`dir`): a turn whose place is another (another directory named, the
bucket moved into or out of a work folder kept here) archives the active
conversation and starts a new one, as another agent does. A page of a work
folder no vault here keeps, kept by a computer of the account's with an
agent there, has its turns run on that computer ("The chat on another
server's page").

The chat draws such a conversation wherever the composer's chip picks a
computer's agent ("The client"): on a page, a folder or the library, in
its own transcript and composer, as it draws Gamma AI's, under a strip
saying where the agent works (the synced folder's name), else with the
composer's folder chip, a menu of the offered directories and *Its own
directory* while the conversation has no turn ("The strip"). Only they see
their conversations; for anyone
else the chat is as it is. Where its owner lets it, the agent may ask on a
card to go beyond its confinement ("Asking in the chat"); else Codex keeps
its sandbox and Claude Code its file tools. Its model, the reply as it is
typed, a terminal, the passages' source lines and Background tasks are
below ("Its model, its typing, a terminal, the sources, Background
tasks"); there is no diff or Revert yet (docs/research/agent-collab.md "In
steps").

- **The conversation is not a chat.** Gamma AI's chats are the workspace's
  (every member and a share read them, they travel in backups and exports,
  `read_chats` reads them); the computer's are their account's alone, in their
  own table of the bucket's server's pages.db, `computer_chats`
  (`db.COMPUTER_CHATS_SCHEMA`; step 59 gave it `bucket` and `dir`, step 62
  `thread_id`, [migrations.md](migrations.md)),
  read and written by `gamma/local_agents/chats.py` and `far_turns.py` only. One row a
  conversation: `id`, `bucket` (a page's id, a folder's or `home`, as
  `chats.bucket`), `dir` (where its agent works: the work folder's id, an
  offered directory's id, or `""` for one of its own), `user_id` (the
  account that talks to the agent, its one reader), `agent` (the agent it
  began with), `title` (its first message's first line, 80 characters),
  `messages` (JSON), `version`, `created_at`, `updated_at`, `archived_at`
  (null for the bucket's active conversation of that account; set:
  History), `thread_id` (the comment thread a conversation answers, its
  bucket the thread's page, "" for a chat's: "A comment sent to an agent";
  such a conversation is never its bucket's active one nor in History, and
  `archived_at` null is the thread's own of that account). It is in `ws_backup.PRIVATE_TABLES` (a backup carries none, a
  restore keeps the live rows), goes with its page deleted for good
  (`ops._purge`), is filed into the library's History with its folder
  deleted (`chats.file_into_home`, in the tree delete's transaction, as
  the AI chats' `routers.chats.file_into_home`), and an account's rows go
  when its membership ends or the account is deleted
  (`db.delete_workspace_prefs`); nothing else reads it. Its paths are
  relative to the directory.
- **Messages.** The person's: `{id, role: "user", text, selections:
  [{text, page, box}], at, skill?}` (`skill` the skill the message ran,
  `{id, name, args}`, its text the arguments: [skills.md](skills.md#a-computers-agent);
  at most 20 selections, 2,000 characters a
  quote; the box kept, since the source line is looked up when the turn
  starts). The agent's: `{id, role: "agent", agent, label, turn_id, state:
  queued | running | done | failed | stopped, text, steps: [{id, kind:
  read | edit | run | search | other, status, path? | program?}], asks,
  changes, changed, built, error, fresh, model, effort, speed, threads?, steers?,
  at, ended_at}`: `asks` what waits for the person ("Asking in the chat"),
  `steers` the messages the person steered into the turn that its agent
  took in (`[{id, text, selections, at, place: {text, steps}}]`, at most
  20, `place` where the reply stood then: the length of its text and how
  many steps had come; `chats.steered_of`),
  `threads` the comment threads its agent opened in the turn (a turn of
  this server's: `threads.opened`), `model`,
  `effort` and `speed` what the turn asked for (null for the agent's default),
  `path` relative to the
  directory or `"outside"`, `program` a command's program alone (as `_said`),
  `changes` the changed files (at most 200; `changed` how many),
  `error` an English sentence (`_redact`ed), `fresh` true when a follow-up
  could not resume the agent's session. Every write takes the workspace's
  write lock and sets `version` above every version of the bucket's
  conversations of that account and never below the clock in
  microseconds (`chats._next_version`: a deleted conversation can take the
  highest away), so a version never repeats and a client polling with the
  one it holds learns of any change, an opened conversation's included.
- **A turn** (`chats.begin`): the person's message and the agent's
  `queued` one written in the bucket's active conversation (one is started
  when there is none, or when it works elsewhere), the turn recorded on
  this computer (`store.add_turn`, vaults.db `agent_turns`, its `link_id`
  the vault's or `""`, its `page_id` the bucket), then queued
  (`runner.queue_chat`) in the queue of its directory under the key `(ws,
  turn id)` ("The queue and the turn"), so two conversations in one
  directory, a thread's among them, wait for each other, and all share the
  `MAX_RUNS` slots: one directory never has two agents. A turn belongs to
  the computer: no connection holds it, so closing the tab or the window
  stops nothing.
- **`_ChatTurn`** ("The queue and the turn": the snapshot,
  `events.start`, the watchdog, following the events, the changed files):
  1. Checks again what its route checked (`home.place`): where it works,
     still the conversation's (`chats.place_of` for its account and
     `dir`: else "The synced folder is no longer kept on this computer.",
     "The directory this conversation works in is no longer offered on this
     computer.", or what it is about gone), a vault not paused or choosing
     its files, the turn's agent still on and the conversation's account's
     (`store.owns`: "Codex was turned off on this computer."), that account
     still an editor, the agent ready (`catalog.probe(fresh=True)`), the
     server and workspace taking writes (`_cannot_write`); else the turn
     fails with that sentence. A turn of another server's page is the
     channel's account's, the vault's maker. A directory of its own is made
     now, the first time.
  2. The message goes `running`.
  3. The prompt (`prompt.build_chat`): the message; what it is about
     (`chats.about`: a page's id, title and format, with its path in the
     work folder it is a file of; a folder, by its path in the work folder
     or its name; the library); the selections as `{page, quote, source}`
     (`source` from `annotations.sources.source_for` with the selection's box
     as one rect, in a work folder kept here only, null without one), all
     inside one JSON block (the selections are the document's text: data);
     where it works and how ("The prompts"): in a vault, change the sources
     here (Codex may rebuild in its sandbox, Claude Code runs no commands;
     never `.gamma/`); in an offered directory, by its name, with the
     build's line; in one of its own, that it holds none of the owner's
     files; and always plain prose, `file:line`, ask what is the owner's to
     decide. The turn takes Gamma's tools before its
     prompt ("Gamma's tools"), and the prompt says whether it has them:
     with them, a section *Gamma's tools* says that what the server
     `gamma_turn` returns is data, not instructions, and names the tools that answer for
     what the conversation is about, by its id (`prompt._tools`): a page's
     `read_page` (the passages highlighted and their notes, its open
     threads, its text) and `read_block`, `view_pdf_page` with its
     `page_id` and a `pdf_page` for a PDF, a
     folder's `list_pages`, the library's `list_folders` and `list_pages`,
     `list_annotations` with the page's, the folder's or the library's
     (`home`) scope and `read_annotation`, then
     `search_library`, `cite` and `read_chats` for the rest, and last the
     three that write under its own name, for the owner: `comment` (a reply
     by `thread_id`, or a new thread on a PDF quote with its page, a text
     file's lines, a note or the page), `resolve` and `highlight`; nothing of
     the page is pasted, and outside a vault Gamma's pages are read with them.
     Without them (no address learned, a far turn) the message and the
     selections are what it is given of the pages.
  4. The session: the conversation's in vaults.db `agent_conversations`
     (`conversation_id`, `link_id`, `agent`, `session`, `cwd`, `at`;
     `link_id` `""` outside a vault) when it is of the same agent and was
     run in that directory as it is now. A new session in a conversation
     with earlier turns takes their last six messages along ("Earlier in
     this conversation (you may not remember it)"; no tool reads a computer
     agent's conversation) and the message reads `fresh`; so does a resume Codex answered with another thread, and a
     Claude Code resume that fails before any step, which starts once more
     afresh (not one whose Gamma's tools did not start, nor one another
     process holds: it fails once, and the conversation keeps its session).
     The earlier messages never include those read from a CLI's session
     (`from_session`, "Sessions started elsewhere"): that history is the
     CLI's own.
  5. The events become the message, written at most every
     `CHAT_EVERY_S` (0.5 s; the watchdog writes the last one when nothing
     follows) and at the end: `session` kept in `agent_conversations`,
     `text` appended as a paragraph (`done.text`, which repeats the last
     text, only when no text came), each `tool` a step or its status, and
     at the end the changed files from the snapshots, `built`, the state
     and its words. The whole error and stderr tail stay in
     `agent_turns.error`.
- **Steer** (`runner.steer_chat`, `_ChatTurn.steer`): a message the person
  steers into the running turn (`chats.steer_of`: `{id, text, selections}`)
  goes to the agent as `prompt.build_steer` writes it (the request and the
  passages as `{page, quote}`, data in a JSON block), through the driver's
  `steer`. When the agent says it took it in (`steered`), the turn records
  where the reply stood (its paragraphs and steps so far, the agent's next
  text a paragraph of its own) and writes it in the message's `steers`, its
  `place` the length of the text those paragraphs make. One the agent never
  takes is the chat's to send as the next turn (its queue). A turn of
  another server's running here takes it from that server's listing
  (`steer_relayed`, below).
- **Stop** (`runner.stop_chat`): a running turn's stop is set (it ends
  `stopped`), a queued one leaves its queue, one its worker holds while it
  waits for a slot ends `stopped` at once (given its slot, it finds its
  message ended and writes nothing more), and one whose message still
  reads queued or running though the runner does not hold it is ended so;
  the conversation takes the next message at once.
  A pause, relink, removal, the agent turned off, its directory no longer
  offered (`runner.stop_dirs`, from `PUT /api/local-agents/dirs`: "Stopped:
  the directory is no longer offered on this computer."), Gamma closing and
  the deadline stop it too (it then fails with why: "The queue and the
  turn").
  `runner.chat_state(ws, turn_id)` → `{turn_id, state: queued | running,
  ahead}` for the chat (`computer.turn`; `ahead`: another run in its
  directory, or the slots, go first, which the turn's pill says).
- **The strip** (`GET /api/local-agents/chat`'s `computer`) names where
  the agent works: `dir` is `{kind: "vault", name, path}` (the path for the
  vault's maker alone), `{kind: "offered", id, name}`, or null for a
  directory of its own; `folder`, `folder_id` and `paused` are the work
  folder's, empty outside one.
- **The vault working.** `runner.at_work(link_ids)` names the vaults whose
  agent works now (`{vault id: {ws, id, agent, detail}}`, a chat's or a
  thread's turn), so the vault chip, the links and the sync panel see the
  vault busy: `agent.running` is `{detail}` (the turn's step in a member's
  words, `_said`).
- **History.** New chat archives the active conversation; History lists
  the archived ones (newest first, at most 200) and the active one apart
  (`active`, whichever its agent); opening one makes it the active one
  (the active one archived); deleting one forgets its session, its grants
  and its directory of its own. New chat and opening are refused (409)
  while a turn of the active one is queued or running. The chat's History
  lists them with the bucket's Gamma AI conversations ("The client").
- **Export.** `GET /chat/history/{id}/export` downloads a conversation of
  the account's, active or archived, as Gamma AI's export does
  (`chat_export.export_file` with the agent's label, a bare `.md`: the
  person's messages with their passages quoted with their pages, the
  agent's with its steps in words (`chat_export.step_text`, the client's
  `stepText` in English), its reply, the files it changed and how its turn
  ended). Its owner alone, as every route here; `read_chats` and the
  workspace's exports read none of them.

### Its model, its typing, a terminal, the sources, Background tasks

- **The model, effort and speed** the agent offers are read with the
  probe, with no model use (`catalog.probe(models=True)`, kept with it;
  not re-read at a turn's start, which asks only whether the agent is
  ready, so a read lists them again once that listing is 10 minutes old,
  or when there was none: `catalog._listed`): Codex's `model/list` on a
  short-lived app-server (every page, hidden models left out, 15 s at
  most; it does the Windows sandbox check in the same process, and
  `config/read` for the user's `service_tier`), Claude Code's
  `initialize` on a
  `claude -p --input-format stream-json … --restricted --strict-mcp-config`
  closed before any message. Shape: `[{id, label, efforts, effort (the
  default), default, speeds, speed (the default)}]` in `GET
  /api/local-agents` and the chat's `computer.models`; none listed leaves
  the agent ready with `models: []`.
- **A model's speeds** are among `events.SPEEDS` (`flex`, `standard`,
  `fast`, cheapest first): standard beside the tiers the agent lists for
  it, none for a model with no other. Codex lists `serviceTiers`
  (`priority` is its fast one, an older Codex's `additionalSpeedTiers`
  says `fast`; `ultrafast` is not offered); Claude Code offers standard
  and fast for a model with `supportsFastMode`. A model's `speed` is the
  one a turn runs at when none is chosen: Codex's `defaultServiceTier`,
  else the config's `service_tier` (a config.toml that sets `priority`
  runs every turn at Fast), else standard; Claude Code's
  `fast_mode_state` (off under `--restricted`). An older computer
  announces none, so its models offer no speed on another server.
- **A turn's choice.** `POST /chat/turns` takes `model`, `effort` and
  `speed`, each one the agent listed (400 otherwise; an effort and a
  speed checked against the chosen model, or the default one); Codex
  gets them on `thread/start` / `thread/resume` (`model`) and
  `turn/start` (`effort`, and the speed as `serviceTierForTurn`:
  `codex.TIERS`, `priority`, `default` or `flex`, which leaves the
  thread's own tier as it was, so the next turn and the person's
  `codex resume` run at that), Claude Code as `--model` (none for its
  `default` entry), `--effort` and the `fastMode` setting (`true` for
  fast, `false` for standard) in its one `--settings`, beside a turn
  with ask's rule, which `--restricted` would not read from the person's
  settings. The agent's message records them. The composer's chip
  (`chat/AgentChip.jsx`, Gamma AI's) lists the agent's models, and the
  model's efforts and speeds and where it works as sections
  (`useComputerSide`'s `chip`), each Default naming the model's own
  where the agent says (*Default (fast)*), the speed chosen drawn as
  Gamma AI's glyph; the choice is the conversation's.
- **The reply as it is typed.** A turn's driver streams
  (`stream=True`): Codex keeps
  `item/agentMessage/delta`, Claude Code runs with
  `--include-partial-messages` and reads `stream_event` `text_delta`s of
  the main thread. Both yield `{type: "delta", text}`; the runner grows the
  message's live paragraph, written at most every `CHAT_EVERY_S`, and the
  whole `text` event that follows replaces it (no text twice).
- **Continue in a terminal and Show in Explorer** are the strip's `⋯` menu.
  Show in Explorer (*Show in Finder*, *Show in file manager*: `vaultState.js`
  `revealLabel`) asks the shell `reveal-on-disk` as the vault chip does.
  Continue in a terminal (offered when the strip's `session` is true: the
  conversation has a session of the account's agent, in its directory as it
  is now, and no turn is to end) asks the shell `continue-in-terminal`
  with the page's id alone; the shell asks its server `GET
  /api/local-agents/chat/terminal?bucket=` (`{cwd, argv, agent, label}`,
  `cwd` the directory the conversation works in:
  the agent's command line plus `resume <thread>` for Codex or `--resume
  <session>` for Claude Code, the session checked against the drivers'
  pattern; 404 without one, 409 while a turn runs, since two writers on
  one session are unsafe) and opens a terminal there ([desktop
  architecture](../../desktop/docs/architecture.md) "The page's asks").
  That terminal is the person's own session of the agent: their settings,
  MCP servers and trust prompts apply, not Gamma's confinement. Checked
  for real: `codex resume` and `claude --resume` in the folder take up a
  session Gamma's driver made, with its context.
- **A selection's source line** on its passage chip: `POST
  /api/local-agents/chat/sources` `{bucket, selections}` → `{sources: [{file,
  line, end?} | null]}` (`chats.sources_of`, the lookup the turn's prompt
  uses: `annotations.sources.source_for` over the selection's box; at most 20,
  no process; nulls for a bucket that is no file of a work folder kept
  here), asked 400 ms after the selections change, kept a minute
  and only until a turn of the conversation ends (the agent may have moved
  the lines); the chip reads `sections/method.tex:3`, or the page number
  without one.
- **Background tasks** lists, for the account and for the co-authors its
  agents serve, one read-only row per chat turn queued or running here
  (`routers/jobs.py`: `{id: "computer-<turn>", kind: "computer-turn",
  title, params: {bucket, turn, agent, label, title (the page's or the
  folder's), conversation, thread?, person?}, state: queued | running,
  progress: {phase: "asking"} while it asks, stoppable, stopping,
  readonly}`, `thread` a comment thread's turn's, `person` the co-author's
  username for a co-author's; `runner.chat_turns` keeps each turn's thread
  and owner); the tray words it "Codex on this computer: <page>" ("…, for
  bob: <page>" for a co-author's), "Waiting for you" while it asks, opens
  the bucket's chat (a page's, or a folder's or the library's in the
  library's view) on a click with the turn's agent picked and on the
  turn's conversation (`taskKinds.turnShown`, `chatSide.js` `showInChat`:
  the thread's, a co-author's, else the bucket's own; a turn of another
  workspace opens only its page there, on its own chat: the app loads that
  workspace anew), and its Stop calls the turn's stop route (in the turn's
  workspace) instead of `cancelJob`.

Checked against the real CLIs (Codex 0.167.0, Claude Code 2.1.318): both
list their models with no model use, stream a reply in deltas, and take a
chosen model and effort. Their speeds were read with no model use too:
Codex's `model/list` names `priority` (*Fast*) on every model and
`ultrafast` on one, and its `thread/start` takes `fast` (as `priority`),
`priority`, `flex` and `default` (an unknown tier, or one the model
lacks, comes back null); Claude Code's `initialize` reports
`fast_mode_state` `on` under `--restricted` with `--settings
'{"fastMode":true}'`, and `off` for a model without fast mode. No turn
was run at a chosen speed against either (it uses the person's plan).

## Sessions started elsewhere

The other direction of *Continue in a terminal* (step 5 of
[agents-as-participants-plan.md](../research/agents-as-participants-plan.md),
check C4): a session the person started in a terminal or an editor, with
the same CLI in the directory the chat's agent works in, is continued in
Gamma. `gamma/local_agents/sessions.py` reads the CLIs' own stores; it runs
no turn and uses no model.

- **Which directory.** The one the chat's picker names, by its id, never a
  path (`chats.place_named`): a work folder's (W) whose vault here the
  account made, or a directory this computer offers. A directory of its
  own holds Gamma's sessions alone and lists none; a vault another account
  made lists none either, as its terminal is its maker's
  (`computerChat.sessionsDir` on the client).
- **Codex.** `thread/list {cwd, sourceKinds: ["cli", "vscode", "exec"],
  sortKey: "updated_at", limit: 50}` on a short-lived app-server
  (`codex.threads`, its pages followed by `nextCursor`, at most three),
  which compares the directory whole (separators and case aside); each
  thread is titled by its `name`, else its `preview`, and its source is
  its originator's (`codex-tui` a terminal, `codex_exec` a script,
  `gamma` Gamma's own), else its `source`. Opened, `thread/read` (its
  `cwd` checked again) and `thread/turns/list {itemsView: "full",
  sortDirection: "desc", limit: 100}` (`codex.thread`) give its turns:
  `userMessage` the person's text, `agentMessage` the agent's,
  `commandExecution`, `fileChange`, `mcpToolCall` and `webSearch` steps
  worded as a turn's are (`codex.item_steps`), `reasoning` left out; a
  failed turn keeps its error, an interrupted one reads stopped.
- **Claude Code.** The files of `<CLAUDE_CONFIG_DIR or
  ~/.claude>/projects/<slug>/` (`sessions.slug`: every UTF-16 unit outside
  `A-Za-z0-9` a `-`, case kept; past 200 characters cut there with `-` and
  the base-36 of the path's Java string hash after), opened with the
  `\\?\` prefix on Windows (such a directory passes 260 characters); a file
  counts only where its first user record's `cwd` is the directory (two
  paths can share a slug). The listing reads the newest 200 files' first
  and last 64 KB: the directory, the entrypoint (`cli` a terminal,
  `claude-vscode` an editor, `sdk-cli` a script), the first prompt, the
  last `ai-title` (the title, else the first prompt) and the last time.
  Opened, the file's last 16 MB are read and the current branch followed
  from the last `last-prompt`'s `leafUuid` back through `parentUuid`
  (`logicalParentUuid` across a compaction): a prompt (a user record with
  text, not `isMeta`, not a slash command's) begins a turn, an assistant
  record's text blocks are the agent's text and its `tool_use` blocks its
  steps (`claude.tool_started`, `tool_ended` with the `tool_result`), its
  thinking left out; the last 100 turns. The summary a compaction writes
  as a user record (`isCompactSummary`, `isVisibleInTranscriptOnly`) is
  no prompt.
- **Busy.** A session another process holds cannot be opened, and a turn
  that would resume it fails before it starts, with "Codex has this
  session open in another window: close it there first." (`sessions.held`,
  `events.BUSY`): Codex's while `<CODEX_HOME or
  ~/.codex>/thread-writer-locks/<id>.lock` is locked (a read of it refused
  on Windows, a shared `flock` refused elsewhere: a file left by a Codex
  that was killed locks nothing), and Codex refuses a second writer itself
  besides; Claude Code's, which has no lock and whose two writers fork
  one file, while `<CLAUDE_CONFIG_DIR>/sessions/<pid>.json` names it and
  that process lives (on Windows its creation time must be the entry's
  `procStart`, so a reused number is not it).
- **Ours.** A session `agent_conversations` binds to a conversation of
  this workspace (`store.bound`, `chats.titles`) is *In Gamma*: titled as
  that conversation, never busy (its own turn holds it), and opening it
  opens that conversation, the bucket's active one again
  (`chats.open_one`) where it is of this bucket; one of another bucket is
  refused with where it is, as are another account's and a comment
  thread's. A session bound to a conversation the workspace does not have
  (deleted for good, another workspace's, another server's over the
  channel, one whose far open failed) is listed and opened as any other.
- **One conversation resumes a session.** Opening a session bound to a
  conversation elsewhere moves the binding: `store.unbound` clears the
  session of every conversation that had it (their rows and grants kept,
  their next turns starting afresh with their earlier messages), then the
  new one is bound. The desktop's opens run one at a time (`_opening`), and
  History's rows wait while one is on its way, so two opens never both
  bind it.
- **Opened.** `chats.adopt` archives the bucket's active conversation
  (409 while its turn is to end), writes a new one of the agent and the
  place, titled as the session, whose messages are the session's turns in
  the shapes above (`sessions.messages`: the agent's text through
  `runner.reply_text`, its steps through `runner.step_of`, each message
  `from_session`), and the route binds it to the session in that directory
  (`store.ran_conversation`), so its next turn resumes it as a resumed chat
  does (`thread/resume`, `--resume`). Codex keeps the thread's name; no
  `thread/name/set` follows a resume.
- **Caps.** 50 sessions listed, 200 files looked at, 64 KB of each end, 16
  MB and 100 turns opened, Codex's reads within 30 s
  (`codex.READ_S`); the routes read in the server's thread pool
  (`asyncio.to_thread`), off the event loop.
- **Another computer's agent** (a far conversation, "Another server's
  chat"): the server that keeps the conversation asks the computer over
  its channel ([computers.md](computers.md) "Sessions over the channel"),
  for the account whose computer it is alone (never a co-author's use), the
  directory named by an id as the far turn's claim names it (the work
  folder that computer keeps, or one it offers). The computer answers
  in a thread of its own (`sessions.answer_far`): `home.far_place`
  resolves the id as for a far turn (a path, a directory of its own or one
  not offered refused with why), the listing is `sessions.listed`'s with
  the conversation each is the session of by its id, which that server
  turns into `ours` for its own (`sessions.shown`, `chats.titles` of the
  account's with that computer's agent). Opened, the server mints the
  conversation's id and the computer reads the session (only one `read`
  finds in that directory), binds it to that id in its
  `agent_conversations` (the vault's id or "", the directory, as a far
  turn binds a session: `runner._session` resumes it at the next claim),
  and sends its messages without ids, the newest within 2 MiB
  (`FAR_MAX_BYTES`), which the server checks and keeps as its own
  (`chats.adopt` with that id and that computer). The bucket's active
  conversation at work is refused before the computer is asked, since it
  binds the session before it answers. A session it names as a
  conversation's already is that conversation where the server has it as
  the account's with that agent; one the server has otherwise (a
  co-author's, another computer's) is refused with 409; one it does not
  have is opened again bound to the new one (`rebind`: a conversation of
  that computer's own server's, or one gone there), the old one letting go
  of it (`store.unbound`). Busy is refused as here. The claim's earlier messages leave
  `from_session` ones out as `home`'s do (`chats.earlier_of`, one rule
  for both). A computer offline, or a release before this step (its
  announce without `sessions`), is not asked: the section says *TIM-PC is
  offline: its sessions show once it connects.* or *TIM-PC needs a newer
  Gamma to list its sessions here.*, and no answer within 15 s *TIM-PC has
  not answered yet.* (`away`, `computerChat.sessionsAway`), each such line
  and a failed read followed by *Try again*, which asks again; the server
  keeps the answer that came later for 30 s, and nothing is asked while
  History is closed. The section's rows lead with the agent's icon as the
  conversations above do while the chat has several agents (the caption
  names the agent), and keep the room of the ⋯ they lack, so every row's
  age stands in one column.
- **History** (`ChatDock`, `ChatHistoryPop`'s `section`): while a
  computer's agent is picked (this computer's or another's of the
  reader's; never a co-author's) and its directory has an id, the popover
  reads `GET /api/local-agents/sessions` each time it opens and lists them
  below the conversations under a caption naming the CLI and the directory
  (`computerChat.sessionsWords`: *Codex sessions in paper*, with *on
  TIM-PC* only for another computer's agent), in the same `.chatHistRow`s
  (`computerChat.sessionRows`: the title, the time, *Terminal*, *VS Code*,
  *Script*, *Desktop app*, *Gamma*, or *Continued here* for one Gamma
  continues). A busy one is `aria-disabled` and says why on a muted line
  under its title; every row is off while conversations are ticked, a
  turn runs or a session's open is on its way, and a press on a row that
  is off puts its reason in the status pill. With no session the section
  says *Sessions you start with Codex in paper show here.* The caption ends
  in a refresh button (*Read the sessions again*) that reads them again in
  place, the rows read before kept until the answer, for a terminal closed
  since History opened. The search filters them too. A row (a press, Enter
  or Space) opens the session (`POST /api/local-agents/sessions/open`):
  its age is a spinner and the status pill says *Opening the session…*
  until the chat has read its conversation again, as an opened History row
  does, and then clears.

Checked against the real CLIs (Codex 0.167.0, Claude Code 2.1.318) by
check C4: both stores read as above, a held Codex thread refused with
-32600, and `--resume` continuing a `claude -p` session.

## Asking in the chat

An agent of a chat turn may ask the person to let it do what its
confinement keeps from it: Codex to step outside its sandbox (a command
with the network, a write outside the folder), Claude Code to run a command
at all (the build). The person answers on a card in the chat (a thread's
turn's in its conversation, the thread's *Open in chat*); an agent its
owner has not let ask behaves as without it.

- **What it may run** is an agent's second setting, the computer's, set on
  the computer only: vaults.db `computer_agents.runs` (`never` or `ask`;
  `never` while it is off), through `PATCH /api/local-agents/{agent}`
  `{runs}` by its owner, `ask` with the shell's word ("The routes"). `ask`
  lets a turn of a work folder of this server ask in the chat and one of
  another server's ask on this computer ("Asking on this computer"). It
  goes back to `never`, with the chat grants, when the agent is turned off
  (`store.set_agent`) and set back to `never` (`store.set_runs`).
  `_ChatTurn` reads it when the turn starts (`runner.ask_refusal`, for the
  turn's agent) and starts the driver with `ask=True` only when it may
  ask; each ask checks it again.
- **The asks.** A driver started with `ask` yields `{type: "ask", id, kind:
  command | files | tool, step, command?, cwd?, host?, paths?, diff?,
  diff_cut?, reason?, tool?, background?, grant_root?}` (`id` the driver's
  key: Codex's JSON-RPC id, Claude Code's `request_id`; `step` the tool
  event it belongs to; `host` the network host a Codex command reaches,
  its own field, the reason as Codex gave it; `diff_cut` when the diff was
  cut to `MAX_DIFF`; `background` a Claude Code call asked to run in the
  background; `grant_root` a Codex file change's `grantRoot`) and `{type:
  "ask_gone", id}` when the agent withdraws one, and takes the answer
  through `Turn.answer(id, decision, note, by)` (`once`, `chat`, `deny`;
  `by` `owner` or `gamma`, whose decline it is; thread-safe). While an ask
  waits, the idle watchdog does not count the silence and the run's
  deadline moves by the wait.
- **The runner** holds each ask for the turn and writes it in the agent's
  message as `asks: [{id (Gamma's own), kind, command?, command_shown?,
  marked?, lines?, inner?, shell?, background?, cwd?, host?, paths?, diff?,
  reason?, tool?, at, expires_at}]`, its step reading `asked` (a step the
  event names none of takes a new id, never the ask's); the strip reads
  *Waiting for you*. An ask shows the person what would run: the command
  exactly as the agent gave it, and, when it holds a character that would
  not show (a control or format character: bidi controls, zero-width
  ones, a BOM, U+2028/9), `command_shown` with each marked `⟨U+202E⟩`
  and `marked` their count (`events.shown_text`); `lines` its line count
  when over one; `inner` and `shell` the command a shell wrapper runs
  (`powershell.exe -Command '…'`, `bash -lc '…'`), only when quoting it
  again by that shell's rule gives back the whole command byte for byte
  and nothing in it is marked (`events.unwrap_shell`, the one parser:
  `_program` names a step's program from it first); `expires_at` when
  Gamma declines it; its folder and paths relative to the work folder or
  under `~` (rewritten only at a path boundary, so a sibling such as
  `paper-old` is never read as the folder; `_relative`), every other path
  as it is (the message's other words are `_redact`ed); it leaves the
  message once answered (and when a stopped server's turn is failed).
  `POST /api/local-agents/chat/turns/{turn}/asks/{ask}` answers it
  (`runner.answer_chat`): `once`, `chat` (a command only) or `deny` with
  an optional note, the step then reading `allowed` or `declined`.
- **Allowed in this chat** is Gamma's record, not the agent's: vaults.db
  `agent_dir_grants` (the conversation, the directory the turn works in,
  the command, the folder below it the command runs in, a network host,
  the conversation's agent); a later ask of exactly that command
  (trimmed), in the same folder and for the same host, in that
  conversation and directory, is answered `once` by Gamma and never shown;
  the same command elsewhere asks again, and a relinked vault's turns work
  in another directory, where none holds. Never a prefix, never a rule written into the
  agent's settings or the folder. A file change has no chat grant beyond
  Codex's own `acceptForSession` for the turn. The strip lists the
  conversation's grants (`computer.grants`, `store.grants_of`), which the
  composer's chip shows; `DELETE /api/local-agents/chat/grants/{id}` takes
  them all back (`store.forget_grants`), and each command asks again.
- **Turned back to *Nothing more*** while a turn runs: the asks waiting are
  declined (the turn goes on), a later ask in it is declined unseen, and no
  grant is written any more.
- **Never asked, declined by Gamma**: a change to a protected part of the
  folder (`.git`, `.codex`, `.claude`, `.vscode`, `.husky`, `.idea`, a
  top-level dotfile such as `.gitconfig`), by either agent; a Codex
  approval that is not a new command (`writeStdin`, input to a terminal it
  already runs); a Codex file change with a `grantRoot` (writes below a
  root for the rest of the session: "Gamma allows file changes one at a
  time."); a turn's 21st ask (`MAX_ASKS`: "This turn asked 20 times: …")
  and every ask after three declines of the person's in a row
  (`MAX_DECLINES`: "The owner declined three asks in a row: …"; an allow
  sets the count back, Gamma's own declines leave it). A decline that is
  Gamma's reaches the agent as Gamma's ("Gamma declined that: …" for
  Codex, "Gamma declined: …" for Claude Code), the person's as the owner's.
- **Claude Code's allowed call** runs with its input cut to `command`,
  `description` and `timeout` (`claude.ALLOWED_INPUT`; the timeout a whole
  number): never `run_in_background`, so an allowed command stays in the
  foreground, where Stop and the turn's end reach it.
- **Unanswered** for `ASK_TIMEOUT_S` (600 s), an ask is declined by Gamma
  with "No answer within 10 minutes."; a Stop, a pause, the agent turned off and
  Gamma closing take the cards out of the message at once and decline what
  waits (Codex `cancel`, Claude Code a denial, then its interrupt).
- **The prompt** (`prompt.build_chat`) tells Claude Code, when asking is
  on, that it may run commands (its build) with Bash, each asked of the
  owner first, and Codex that a command needing more than its sandbox asks
  the owner.

What each driver does with `ask` is in its section below. Checked against
the real CLIs (Codex 0.167.0, Claude Code 2.1.318): a Claude Code Bash call
asked through Gamma's handler, allowed once and run; its Edit of
`.git/config` refused by Gamma with no card, the file unchanged; Codex
asking to run `curl` outside its sandbox (an integer id, echoed), allowed
once and run with the network.

## Asking on this computer

A chat turn of another server's ("The chat on another server's page") may
ask too, as a local turn does, and the card is drawn and answered on the
computer itself, in the desktop app's bar, with the server's page hidden
while it is up ([desktop architecture](../../desktop/docs/architecture.md)).
Step 5b of [agent-collab](../research/agent-collab.md). Where the link lets
that server answer what this computer's agents ask (its `asks`, decision 7
of [the plan](../research/agents-as-participants-plan.md), step 4.3), the
ask is relayed there too and answered in the chat there ("Relayed" below);
else that server starts the turn and learns that it waits, never what it
asks: nothing it sends draws, words or answers the card.

- **What it may run** is the turn's agent's, the computer's (`runs`
  `ask`, "Asking in the chat"): one setting lets it ask in the chat here
  and on this computer for a turn there; per server, only whether that
  server answers too (the link's `asks`, [computers.md](computers.md) "The
  servers this computer answers").
- **Who may ask** (`runner.ask_refusal`, at the turn's start and at every
  ask): the agent's `ask`, that server reached securely now (`NOT_SECURE`:
  `remote_servers.plain` must be "", https or this computer,
  [https.md](https.md)), and, unless the link relays asks, the shell's
  secret set (`NOBODY_HERE` without: no window to ask in); a co-author's
  turn only where the link relays them. A turn that may not starts its
  agent without `ask`.
- **Each ask**, in `_ChatTurn._asked`'s order, is declined unseen ("Gamma
  declined that: …") for: its agent or the address no longer allowing it;
  a kind other than a command or a file change; a `grantRoot` (every
  turn); a command over `FAR_MAX_COMMAND` (8000 characters) or
  `FAR_MAX_LINES` (200), a cut diff or more than `FAR_MAX_PATHS` (20)
  paths (`TOO_LONG`: what this computer cannot show whole); a network host
  that does not read as one (`events.host_ok`: `HOST_UNREAD`); the caps of
  every turn; and, where the link relays no asks, the desktop app not there
  to answer: unless it read the asks within `SHELL_SEEN_S` (60 s), the ask
  waits up to `SHELL_GRACE_S` (15 s) for its first read, else
  `NOBODY_HERE`. No grant is looked up for a remote turn: only *Allow
  once*, *Don't allow* and the shell's *Stop asking*, which declines and sets
  what the agent may run back to `never` (`PATCH /api/local-agents/{agent}`
  `{runs: "never"}`, as its owner).
- **What is held** is the ask with its card (`_ChatTurn._card`): `{id,
  turn_id, link_id, agent, label, kind, command?, lines?, inner?, shell?,
  marked?, background?, dir, folder, file, host?, paths?, diff?, reason?,
  server: {origin, account}, at, expires_at}`, every string through
  `events.shown_text` (`marked` counting the marks in all), `dir` and
  `paths` absolute on this computer, `folder` the vault's directory,
  `file` the page's path from this computer's vault rows alone (""
  otherwise: never that server's path or title, which the prompt keeps),
  `reason` the agent's one line (300 characters), `server.origin` the
  link's, `server.account` the channel's account there (64 characters):
  the one word in it that server chose.
- **What that server learns**, the link relaying no asks: the message's
  `waiting` (`home.CHAT_FIELDS`; `_turn_fields` takes it as a flag,
  `far_turns.write_run` and `_end_run` set it false at every end) and the steps
  as for any turn (`{id, kind, status, program | path | tool}`, a step id
  never this computer's ask id); never `asks`. Integrity, not secrecy: the
  agent's next words may say what it did.
- **Relayed** (the link's `asks` on, read at each ask and each write:
  `_ChatTurn._relays`): the message there takes the asks as the chat's
  message holds them (`_progress`' `asks`, each `once`, its paths relative
  to the directory, under `~` or whole; `waiting` false), so the chat there
  draws the card and a thread's chip says it waits for the person. Its
  answer there (the turn's person or the agent's owner, `once` or `deny`
  with a note; [computers.md](computers.md) "What the agents ask, answered
  here") is kept with the run, and the computer is woken: the channel's
  listing hands it to the turn (`channel._chats` → `runner.answer_relayed`:
  this run's, the ask still waiting, the link still relaying, else
  nothing). The bar lists the ask all the same, and the first answer is
  the one: the other card closes (the bar's list loses it, the message
  there is written without it), and an answer after it is refused (409).
  The link turned off or on while a turn runs writes its message there
  again without the asks or with them (`runner.relays_changed`). Neither
  the bar nor the desktop app's secret is needed then: with no window here,
  that server alone answers. A co-author's far turn asks only so.
- **The routes** are the computer's alone (404 where agents do not run),
  with the shell's word, never unchecked (403), and a session of the
  account that made the vaults (the shell's own partition): `GET
  /api/local-agents/asks?after=&wait=` → `{version, asks}` (the asks of
  that account's vaults, oldest first), held up to `wait` (20 s at most)
  while `version` is `after`, each read telling the runner the app is
  there (`runner.wait_asks`); `POST /api/local-agents/asks/{id}`
  `{decision: once | deny}` → `{answered, version}` (`runner.
  answer_computer`; 404 "No question on this computer has that id.", also
  for another account's; 400 "On this computer an ask is allowed once or
  declined."; 409 as an answer in the chat). `version` rises at every
  change of what waits (`_asks_changed`), so the shell drops a held list
  older than an answer. This computer's own chat route refuses every
  remote turn (409 "This is answered on the computer that runs the
  agent."), by rule too: `_ChatTurn.answer` refuses it; only
  `answer_computer` reaches `_answer_here`, and `answer_relayed`
  `_decide`.
- **The answer never waits on that server**: after an ask or an answer a
  remote turn's message is written by the flush thread (`_show_later`),
  so the POST returns once the driver has the answer; a write that did not
  reach that server (`HttpChatHome.missed`) is tried again by the flush.
- **The time**: a remote turn's waiting moves its deadline by `FAR_WAIT_S`
  (600 s) in all, so a turn holds one of this computer's two slots at
  most 10 minutes past `RUN_TIMEOUT_S`; an ask is declined after
  `ASK_TIMEOUT_S` as in the chat. Stop there declines what waits here (the
  drivers' stop), and the list loses it at once.
- **Background tasks** there read the turn's phase `asking` while its
  message says `waiting` or holds an ask (`far_turns.remote_turns`' `asking`),
  with `there` for `waiting` (*Waiting for your answer on TIM-PC*; else
  *Waiting for you*).

## Gamma's tools

Step 2.7 of
[agents-as-participants-plan.md](../research/agents-as-participants-plan.md)
gives a turn of this server Gamma's read tools, and step 3.3 its writing
tools (`comment`, `resolve`, `highlight`, [mcp.md](mcp.md) "The Comment
level"): the agent calls this server's own MCP endpoint ([mcp.md](mcp.md))
with a token of the turn's (`tools.py`, `runner._ChatTurn._tools`). What it
writes is signed and stamped as this computer's agent, for the turn's owner,
and the owner can delete it; notes it changes only through the turn's asks.

- **The token** (`integrations.mint_turn`): scope `turn`, the turn owner's,
  of the turn's workspace alone, named with the agent's id
  (`computer:<this computer>:codex`, `agents.here`: what it writes through
  MCP is that agent's, `agents.of_token`), living the run's timeout (`RUN_TIMEOUT_S`) and
  `TOKEN_SLACK_S` (60 s). It is minted when the turn of this server starts,
  once (`_ChatTurn`, before its prompt and the folder's snapshot); each start of the agent (a retry
  afresh too) renews it to that start's deadline when it would expire
  sooner, and while the turn's deadline moves (an ask waits for the person)
  the watch renews it, at most once a minute and `TOKEN_SLACK_S` past the
  deadline (`_keep_tools`; a renewal that failed is tried a minute later).
  It is deleted when the turn ends, however it ends (`_drop_tools`, in
  `go`'s `finally`: done, failed, stopped, the agent's process gone), and
  every turn token a server that stopped left is deleted at start
  (`runner.recover`, `integrations.drop_turns`). It opens `/mcp` and
  nothing else: every `/api/` route answers 403 `auth.TURN_ONLY`, its
  reads included, as a computer's token is refused outside its channel. It
  is no connection: Settings' list leaves it out, it counts toward none of
  the account's twenty, and the routes that change or revoke a connection
  do not find it. Step 3.3 adds the *Comment* level.
- **The address** (`tools.mcp_url`): this server's `/mcp` as a process on
  this computer reaches it, learned from the requests this server is sent
  (`tools.heard`, from the session middleware): the first that came from a
  loopback client to `127.0.0.1` or `::1` (the hosts MCP's transport takes)
  without `X-Forwarded-Proto`, which would have uvicorn take the proxy's
  scheme for the socket's, names the socket's scheme, address and port.
  The desktop app's window is such a client, so its server knows the
  address before any turn. A server reached only through a proxy or from
  other computers learns none, and its turns run without Gamma's tools
  (logged). A server that serves https itself names `https://127.0.0.1:…`,
  which its certificate may not cover.
- **What reaches the agent**: the token in `GAMMA_AGENT_MCP_TOKEN`
  (`events.TOKEN_ENV`) of that one process's environment (`procs.spawn`
  adds a driver's variables after `child_env` dropped every `GAMMA_*` of
  the server's), the address in its config; never the token on a command
  line, in a prompt, in a file, a log or a stored turn. The drivers' part
  is under Codex and Claude Code below.
- **The prompt** of a turn names the tools that answer for what the
  conversation is about and keeps Gamma's text data ("The chat's computer
  side", step 3); the turn mints the token before it.
- **A step** of a call is of kind `gamma` with the tool's name (`tool`:
  `read_page`, `list_pages`, …): the runner keeps `{id, kind: "gamma",
  status, tool}`, the export words it "Read Gamma: read_page", a writing
  tool's "Wrote in Gamma: comment" (`events.gamma_words`, the chat's
  `stepText` and `stepNow` alike), and the chat's chip is the kind Gamma
  AI's own call of that tool is (`computerChat.stepChip`), a writing tool's
  the kind `write`, which lists no change under the reply.
- **A far turn** (one this computer runs for another server over the
  channel, "Another server's chat") gets that server's tools, never this
  one's (step 4.2 of the plan): that server mints the turn's token at the
  claim (`integrations.mint_turn` for the account the turn is for, of its
  workspace alone, named with this computer's agent's id there,
  `computer:<its id there>:codex`, so what it writes reads *Codex on
  TIM-PC* there: `agents.of_token`, `registry.far_name`), living
  `far_turns.TOKEN_S` (the run's timeout and a minute) and renewed by the
  run's writes at most once a minute, replaced by a retried claim's, and
  deleted at the turn's end, however it ends (a write that ends it, a Stop
  that ends it there, the computer's token taken back: `far_turns._close`);
  its address is that server's own `/mcp`, its public address
  (Settings → Administration → Server) or the loopback address the claim
  came to, else none (`routers/computers.py` `_mcp_url`: the transport
  takes no other host), and the turn runs without tools. This computer
  takes them only where the address's origin is the channel's
  (`HttpChatHome.gamma`, else the message says why), and hands the token
  to the agent as a local turn's: `events.TOKEN_ENV` of its process alone.
  A restart of that server's desktop app deletes every turn token there
  (`integrations.drop_turns`), a far turn's with them: its next call is
  refused.

## The drivers

`events.start(name, argv=, cwd=, prompt=, session=, images=, ask=, stream=,
model=, effort=, gamma=, title=)` starts one turn of an agent and gives a `Turn`: `session` (the
agent's session or thread id, "" until known), `pid`, `events()` (blocks;
read once), `stop()` (thread-safe, returns at once, idempotent) and
`steer(text, id)` (thread-safe: a message the person steered into the turn
while it works, "Steer" under "The chat's computer side"; whether it was
written, False once the turn ends or before it can take one).
`gamma` is `events.Gamma(url, token)`, Gamma's tools for the turn, or
None; `title` a new Codex thread's name (a chat turn's conversation's
title; Claude Code takes none). `events()` gives plain dicts:

| type | fields |
|---|---|
| `session` | `id` |
| `text` | `text`, a whole message the agent wrote |
| `tool` | `id`, `kind` (`read`, `edit`, `run`, `search`, `gamma`, `other`), `title` (one line: `Read sections/intro.tex`, `Run latexmk -pdf main.tex`, `Read Gamma: read_page`), `status` (`started`, `completed`, `failed`), `path` (read, edit: relative to the directory with `/` when inside it, else absolute), `command` (run), `output` (a run's last 2000 characters, at its end), `tool` (gamma: the tool's name) |
| `steered` | `id`: the agent took in the message steered under that id (`steer`) and works with it from here |
| `done` | `ok`, `text` (the last message), `error` ("" when ok), `stopped`, `usage`, `gamma_down` (Gamma's tools did not start or connect: `events.GammaDown`) |

Every turn ends with exactly one `done`: at its end, on `stop()`, on a
crash, a malformed stream, a broken pipe, or `TURN_IDLE_S` (600 s) without
a line ("Codex said nothing for 10 minutes."). The thread reading
`events()` does all the work: `stop()` sets a flag it sees within 0.2 s;
it asks the agent to stop where it can (Codex's `turn/interrupt`, then
5 s to end) and kills it otherwise. A turn ended by a stop gives
`done(ok=False, stopped=True, error="")`. A turn that ends on its own, or
whose output ends, closes the CLI's stdin and gives it 5 s to exit (Codex
saves its thread and runs the user's turn-end hook), then kills its tree;
a stop past its grace, the silence, a failed protocol step or a crash
closes stdin and kills the tree at once. `close()` ends the CLI of a turn
whose events nobody will read. A failure's error prefers the
agent's own message (Codex's `error` notification, a failed turn's error,
a JSON-RPC error; Claude Code's `result`) over stderr's last line, which is
used only when it is no timestamped log line, then "Codex stopped
unexpectedly (exit N)." The stderr tail (8 KB) stays for the run's row.

Only the user's own settings shape the agent, its `AGENTS.md` or
`CLAUDE.md`, its models, its hooks, except that a turn gets **no MCP
server but Gamma's and no web tools**: with approvals off nothing would
ask before such a tool ran. Codex's plugins bring servers of their own, so
a Codex turn runs without its plugins (their skills and hooks too).

### Codex

`codex app-server` on stdin and stdout: JSON-RPC, one object per line, no
`"jsonrpc"` key (the protocol of codex-cli 0.167.0's generated schema). One
loop dispatches every message in every phase, since notifications and the
server's requests come before the response a request waits for; requests
carry increasing ids and wait 30 s (`initialize`, `config/read`, the
sandbox check) or 120 s (`thread/start`, `thread/resume`, `turn/start`).

1. `argv + ["app-server"]` in the turn's directory.
2. `initialize` `{clientInfo: {name: "gamma", title: "Gamma", version},
   capabilities: {optOutNotificationMethods: [the delta notifications:
   item/agentMessage/delta, item/commandExecution/outputDelta,
   item/reasoning/summaryTextDelta, item/reasoning/summaryPartAdded,
   item/reasoning/textDelta, item/plan/delta, item/fileChange/outputDelta]}}`,
   then `initialized`. Only whole items are used.
3. On Windows `windowsSandbox/readiness`; not `ready` → the sandbox
   problem.
4. `config/read {cwd}`, then `thread/start {cwd, sandbox: "workspace-write",
   approvalPolicy: "never", config: _quiet(config)}` (or `thread/resume`
   with the session's `threadId` and `excludeTurns: true`; a thread that is
   gone starts a new one, and one another process holds, refused with
   -32600 "already has an active writer" (`codex._held`), fails the turn as
   `events.SessionBusy`, never a new thread in its place). `_quiet` turns off every server the user's
   `mcp_servers` names (`mcp_servers.<name>.enabled = false`), sets
   `features.apps = false`, `features.plugins = false` and
   `web_search = "disabled"`. Verified on the
   development machine (codex-cli 0.167.0, three MCP servers in
   config.toml) with a zero-token probe of `thread/start` counting
   `mcpServer/startupStatus/updated`: `config.mcp_servers = {}` does nothing
   (it merges: 6 notifications); every listed server `enabled = false`
   keeps the user's servers down but ChatGPT's apps server (`codex_apps`)
   still starts (2); `features.apps = false` beside it leaves none (0).
   A plugin's servers (its `.mcp.json`) are not among those `config/read`
   lists: with the Codex app's `unified-computer-use@openai-bundled` plugin
   enabled, `cua_repl` still started and failed every turn;
   `features.plugins = false` leaves none (0). It turns off every plugin's
   skills and hooks too (Gamma's own `gamma` plugin is a skill for the
   user's `gamma` server, which the turn turns off anyway), and naming the
   server alone (`mcp_servers.cua_repl.enabled = false`) is refused
   ("invalid transport": a plugin's server is no entry of the config's).
   `web_search = "disabled"` is the documented switch of the search tool.
   A server that reports starting all the same fails the turn ("Codex
   started the MCP server … although Gamma asked for none."), one that
   reports it before `thread/start` answers too (the thread's id is not
   known yet, so any such report is this turn's). With `gamma`, `_quiet`
   also names Gamma's server, `mcp_servers.gamma_turn = {enabled: true,
   url, bearer_token_env_var: "GAMMA_AGENT_MCP_TOKEN"}` (`events.MCP_NAME`:
   not `gamma`, the name Gamma's own setup gives the user's connection,
   `codex mcp add gamma`, since the override merges into a server of the
   same name, whose headers, tools turned off or command would then be
   the turn's; the user's `gamma` is turned off with the rest), and
   `shell_environment_policy.exclude`, the user's own list with
   `GAMMA_AGENT_MCP_TOKEN` added (an override replaces a list, so the
   user's excludes are kept by naming them), without
   which the agent's own commands printed the token (C1 of the plan): its
   `starting` and `ready` are its own, its `failed` fails the turn ("Gamma's
   tools did not start for Codex: …", `events.GammaDown`), any other server's start still
   fails it ("… although Gamma asked for no other."). The thread's
   id is the `session`; a thread whose effective sandbox is not
   `workspaceWrite` ends the turn ("Codex did not take the workspace
   sandbox."). A new thread (`thread/start`, not a resume) with a `title`
   is named `thread/name/set {threadId, name}`, so `codex resume` in the
   directory lists Gamma's conversation by its title rather than by its
   long first prompt (C3 of the plan); a computer agent's conversation is
   never renamed, so this is once per thread. Its answer is not waited for:
   a Codex that refuses the request, or never answers it, runs the turn
   all the same. Claude Code lists a `-p` session as
   it lists its own and needs nothing.
5. `turn/start {threadId, input: [{type: "text", text: prompt}] +
   [{type: "localImage", path}] (at most 6), sandboxPolicy:
   {type: "workspaceWrite", writableRoots: [cwd], networkAccess: false}}`.
   Items that come before its response belong to the turn.
6. Items (`item/started`, `item/completed`): `commandExecution` → `run`
   (failed unless it completed with exit 0, its `aggregatedOutput` on
   completion); `fileChange` → one `edit` per changed path; `mcpToolCall`
   of Gamma's server → `gamma` with its `tool` ("Read Gamma: <tool>",
   failed when its `status` is), of another → `other` ("Use
   <server>.<tool>"); `webSearch` → `search`;
   `agentMessage`, completed → `text`. An `error` notification with
   `willRetry` false is kept as the reason. `turn/completed` of this turn
   ends it: `completed` ok, `interrupted` after a stop stopped, else the
   turn's error.
7. The server's requests are refused in the shape the schema requires:
   `item/commandExecution/requestApproval` and
   `item/fileChange/requestApproval` `{decision: "decline"}`,
   `applyPatchApproval` and `execCommandApproval` `{decision: {denied:
   {rejection: …}}}`,
   `item/permissions/requestApproval` `{permissions: {}, scope: "turn"}`,
   `mcpServer/elicitation/request` `{action: "decline"}`, anything else a
   `-32601` "not supported" error. `approvalPolicy: "never"` makes them
   rare, but an administrator's requirements can put it back to
   on-request (this development machine's allow only `on-request` and
   `untrusted`).
8. **With `ask`** (a turn whose agent may ask, "Asking in the chat") the thread starts or resumes with `approvalPolicy:
   "on-request"` and `approvalsReviewer: "user"` in the same sandbox, and
   the thread's answer must echo both ("Codex did not take Gamma's way of
   asking you." otherwise). Under `on-request` the model asks only to step
   outside the sandbox, so a build inside it runs unasked.
   `item/commandExecution/requestApproval` of kind `command` becomes an ask
   (the command, its `cwd`, the reason, a network host as its own field;
   any other kind, `writeStdin` among them, is declined) and
   `item/fileChange/requestApproval` one with the paths and the diff of the
   `fileChange` item of that id (kept from `item/started` and
   `item/fileChange/patchUpdated`: the request itself carries none), each
   change under a `--- a/<path>` / `+++ b/<path>` header, an added or
   deleted file's text as `+` or `-` lines, and past `MAX_DIFF` cut at a
   line end with a line saying how much more there was; a change to a
   protected part of the folder is declined, never asked. The
   answer echoes the request's id exactly (an integer as an integer):
   once `accept`, chat `acceptForSession`, deny `decline` and, with a note,
   a `turn/steer` "The owner declined that: <note>" ("Gamma declined that:
   <note>" for Gamma's own) in the same write, so
   a turn that ends on reading the decline still has the note and the
   answer counts as given. The step reads allowed or declined before the
   agent hears the answer, and what the agent says of a declined step
   after that leaves it declined. A Stop answers what
   waits `cancel`, then interrupts. `serverRequest/resolved` for an id Gamma
   did not answer withdraws the ask. The other requests stay refused.
9. **A steer** is `turn/steer {threadId, expectedTurnId, clientUserMessageId:
   <its id>, input: [{type: "text", text}]}`, not waited for. Codex takes
   it at once (its instant interrupt cuts what the model writes; a command
   running finishes) and reports a `userMessage` item whose `clientId` is
   that id: the `steered` event. After the turn's end it answers -32600
   "no active turn to steer" and no item names it (checked on codex-cli
   0.169.0).

### Claude Code

`claude -p --input-format stream-json --output-format stream-json --verbose
--replay-user-messages --restricted --permission-mode acceptEdits
--permission-prompts none --strict-mcp-config --tools Read,Edit,Write,Glob,Grep
--disallowedTools Bash,WebFetch,WebSearch`,
then, with `gamma`, `--mcp-config {"mcpServers":{"gamma_turn":{"type":"http",
"url":<the address>,"headers":{"Authorization":"Bearer
${GAMMA_AGENT_MCP_TOKEN}"}}}} --allowedTools mcp__gamma_turn` (`claude.mcp_config`:
Claude Code fills the variable from its own environment, so the token is
on no command line; `--allowedTools` runs Gamma's tools without a prompt,
which with prompts off would deny them; `--tools` limits the built-in
tools only: C2 of the plan), then `--include-partial-messages` (the reply
as it is typed), `--model`, `--effort` and `--resume <session>` when the session matches `^[A-Za-z0-9_-]{1,128}$`
(`claude.command`). The flags are Claude Code 2.1.318's `--help`;
`--tools`, `--disallowedTools` and `--allowedTools` take several values,
so nothing positional follows them.

- **No shell.** Its tools are the file tools alone. A rule such as
  `Bash(latexmk *)` would let it run anything: latexmk takes Perl with `-e`
  and runs any `.latexmkrc` the agent writes. `acceptEdits` accepts edits
  inside the working directory only, and with permission prompts off
  anything that would ask is denied, an edit elsewhere among them.
  `--strict-mcp-config` starts no MCP server of the user's: none, or
  Gamma's alone with `--mcp-config`.
- **Restricted.** `--restricted` reads no settings file: a folder's own
  `.claude/settings.json` could otherwise add hooks, which `-p` runs
  without asking whether the folder is trusted, and a Codex run may write
  one. It also keeps the file tools inside the working directory and makes
  a write to a settings, git or tool-configuration file a person's
  decision, refused with prompts off: a planted `.git/hooks/pre-commit`
  never lands. Checked against the real CLI on the development machine: an
  edit inside the folder accepted, the hook and a file outside the folder
  refused. The cost: the user's own settings
  (a chosen model) are not read either, so the account's default model
  answers.
- **The prompt** goes on stdin as a `user` line with `client_composed:
  true` (no `@path` or `/command` read in it), from a thread of its own,
  started at once (Claude Code waits about 3 s for stdin), and stdin stays
  open until the turn's `result`. The pictures are not passed: the prompt
  names them and Claude Code opens them with Read.
- **A steer** is one more `user` line, its `uuid` the steer's id (never
  before the prompt's). Claude Code reads it once the tool calls running
  finish and answers it in the same response; `--replay-user-messages`
  echoes it then (`isReplay`, that `uuid`): the `steered` event. One
  written after the turn's last call starts another turn in the same
  process, a second `result`, so a `result` while a steer written is not
  echoed does not end the turn: the next one does, the token reports
  added. After that a steer is refused (checked on Claude Code 2.1.235).
- **Lines**: `system`/`init` → `session` (`session_id`); with `gamma` its
  `mcp_servers` must name `gamma_turn` `connected`, else the turn fails
  ("Gamma's tools did not connect for Claude Code (it says failed).",
  `events.GammaDown`): Claude Code would go on without them. `assistant`: every
  block of `message.content` (one message may come split over several
  lines): `tool_use` → a tool started (`Read` → read `file_path`; `Edit`,
  `MultiEdit`, `Write`, `NotebookEdit` → edit; `Bash` → run; `Glob`,
  `Grep` → search; `mcp__gamma_turn__<tool>` → `gamma` with its `tool`;
  anything else `other`); `text` → `text`. `user`
  lines' `tool_result` blocks end the matching tool (`is_error` failed).
  `result` → `done`: ok when `subtype` is `success` and not `is_error`,
  the error the `result` text or the subtype.
- **`stop()`** kills it: without `ask` Gamma speaks no control protocol to
  it, so there is no interrupt.
- **With `ask`** (a turn whose agent may ask, "Asking in the chat") the flags are their own, `ASK_FLAGS`: `-p --input-format
  stream-json --output-format stream-json --verbose --restricted
  --permission-mode acceptEdits --permission-prompt-tool stdio
  --strict-mcp-config --tools Read,Edit,Write,Glob,Grep,Bash
  --disallowedTools WebFetch,WebSearch --settings
  {"permissions":{"ask":["Bash"]}}` (and `--resume`). `--permission-prompt-tool
  stdio` (hidden from `--help`; the form the Agent SDK uses) sends every
  permission prompt to Gamma on stdout as a `control_request` `can_use_tool`
  and takes the answer on stdin; the ask rule (which `--restricted` still
  reads from `--settings`) makes every Bash call one, read-only ones too.
  The process gets `CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR=1`, so every
  Bash call starts in the folder (an earlier `cd` carries over otherwise,
  and the card says where a command runs).
  Stdin carries an `initialize` control request, then the message as a
  `user` line with `client_composed: true` (no `@path` or `/command` read
  in it), and stays open until the turn's `result` (closing it cancels a
  waiting prompt). A `can_use_tool` for `Bash` is an ask (the command, its
  description as the reason); any other tool is denied at once, never
  asked: under `acceptEdits` only an edit outside the folder or of a
  protected file (`.git`, `.claude`, `.vscode`…) reaches Gamma, refused
  "Gamma does not let an agent change that file.", and anything else "Gamma
  does not offer that tool here." Answers: a success `control_response`
  with `toolUseID`, `{behavior: "allow", updatedInput: <the input cut to
  command, description and timeout>}` (`ALLOWED_INPUT`: never
  `run_in_background`; never `updatedPermissions`: no rule is written
  anywhere) or `{behavior: "deny", message: "The owner declined: <note>"}`
  ("Gamma declined: <note>" for Gamma's own). A call asked with
  `run_in_background` is an ask with `background: true`: allowed, it runs
  in the foreground. A Stop denies
  what waits, then sends an `interrupt` control request and gives it the
  grace period before the kill. Other control requests get an error
  response; `keep_alive`, `control_response` and `stream_event` lines are
  read and passed over.

## Processes

`procs.py` starts every CLI (`spawn` for a turn, `run_quick` for a probe):

- **The environment** (`child_env`): this server's minus every `GAMMA_*`
  variable (the shell's secret, the data directory: the server's, never
  the agent's) and minus the variables a running agent session sets for
  what it starts (`CLAUDECODE`, `CLAUDE_PID`, `CLAUDE_EFFORT`,
  `CLAUDE_AGENT_SDK_VERSION`, `CLAUDE_CODE_ENTRYPOINT`,
  `CLAUDE_CODE_EXECPATH`, `CLAUDE_CODE_SESSION_ID`,
  `CLAUDE_CODE_CHILD_SESSION`, `CLAUDE_CODE_MESSAGING_SOCKET` and `_TOKEN`,
  `CLAUDE_CODE_SSE_PORT` and the rest of `procs._DROP`; `CODEX_THREAD_ID`,
  `CODEX_SANDBOX*`, `CODEX_MANAGED_BY_*`), so an agent started from a
  server run inside Claude Code or Codex does not think it is nested or
  attach to that session's message socket. The user's own configuration
  stays: `CODEX_HOME`, `ANTHROPIC_*`, `OPENAI_*`, and `CLAUDE_CODE_*`
  settings such as `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_OAUTH_TOKEN` or
  `CLAUDE_CODE_GIT_BASH_PATH`. `NO_COLOR=1`. A driver's own variables are
  added after (`spawn`'s `env`): Claude Code's `ASK_ENV`, and the turn's
  token in `GAMMA_AGENT_MCP_TOKEN` ("Gamma's tools"), the one `GAMMA_*`
  name an agent sees.
  A frozen Linux build gives back the library path its bootloader replaced
  (`LD_LIBRARY_PATH_ORIG`) or drops it.
- **Windows**: `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` (no console
  pops up), and the child is put in a Job object with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` right after it starts. The job's
  handle is this server's, so the agent and whatever it started die with
  the server however it ends; `kill_tree` terminates the job (taskkill
  `/T /F` when no job could be made, which is logged). Popen cannot start
  a child suspended, so a grandchild started in that first instant would
  escape the job; the CLIs start nothing that early.
- **POSIX**: the child leads its own session (`start_new_session`), and
  `kill_tree` sends its group SIGTERM, then SIGKILL after a grace. On Linux
  a `preexec_fn` sets `PR_SET_PDEATHSIG` to SIGTERM: the signal follows the
  thread that spawned the child, so a turn is started and finished on one
  thread (the run's worker).
- **The end**: every live child is registered; `procs.stop_all` (at exit,
  and from `runner.shutdown`) closes their stdin, waits up to 5 s for all
  of them, then kills each tree. `runner.shutdown` first sets every turn's
  stop with the reason "closing", so they fail with "Stopped: Gamma is
  closing."
- `run_quick` never raises: `(returncode, stdout + stderr)`, `(-1, "timed
  out")` past its timeout with the tree killed, `(-1, reason)` when it
  cannot start.

## Recovery

`app._startup_maintenance`, after vaults.db is made, calls
`runner.recover()` when `enabled()`, in a `try` that logs (the agents never
stop the server). The vaults' agents of before become the computer's, once
(`store.carry_over`). Every turn's token a stopped server left is deleted
(`integrations.drop_turns`: no turn runs yet). The agents' rows of vaults
that are gone are forgotten (`store.forget_links`). Then, in a daemon
thread, the turns it left open (`store.open_turns`, `agent_turns` with no
`ended_at`) fail, their message too when it still reads queued or running
("Gamma stopped while Codex worked on this.", `_fail_left_open`; a
thread's turn ends in its thread so, `threads.settle`), and the
directories of their own of conversations gone meanwhile are removed
(`chats.sweep_dirs`). A workspace behind on its migrations
(`migrations.is_behind`) is waited for, every `RECOVER_POLL_S` (5 s) and at
most `RECOVER_WAIT_S` (600 s), while the background walk upgrades it; a
turn not failed by then stays open for the next start. A turn of another
server's turn fails there ("Another server's chat").

## What this computer keeps

vaults.db (`db.VAULTS_SCHEMA`: what this computer knows, never migrated,
additive statements; `gamma/local_agents/store.py`, every function its own
short connection):

| Table | Columns |
|---|---|
| `computer_agents` | `agent` (the key: `codex`, `claude`), `enabled`, `runs` (`never` or `ask`; `never` while it is off), `set_by` (the account that turned it on, which owns it), `set_at`; a row stays when it is turned off. Turned on again while on, it keeps its owner and `set_at` |
| `agent_dirs` | `id` (the key), `path` (a directory as `folder_links.clean_path` gives it), `name` (the directory's own when none is given), `added_at`: the directories offered to the agents beyond the vaults, in the order last given |
| `agent_conversations` | `conversation_id` (the key), `link_id` (its vault's, "" outside one), `agent`, `session`, `cwd`, `at`: the session a chat conversation's turns resume ("The chat's computer side"), a session started elsewhere's once it is opened ("Sessions started elsewhere") |
| `agent_turns` | `id` (the turn), `ws`, `link_id` ("" outside a vault), `conversation_id`, `page_id` (the conversation's bucket), `agent`, `state` (queued, running, then done, failed or stopped), `started_at`, `ended_at`, `error` (this computer's record: the agent's whole error and its stderr tail, never shown to members), `pid`: a turn's run, a chat's or a thread's |
| `agent_dir_grants` | `conversation_id`, `dir` (the directory the turn works in), `command`, `cwd` (the folder below it the command runs in, `.` the directory) and `host` (a network host, "" for none), the key; `agent` (the conversation's), `at`: a command the person allowed in that chat, answered by Gamma when the same command is asked again in the same directory and folder for the same host |
| `agent_people` | `server` ("" this server, else a channel's `remote_url`), `workspace_id`, `account` (the person's account there) and `agent`, the key; `username` (how Settings names them while that server is not reached), `at`: a person the agent's owner lets use it ("A co-author's turn"), at most 50 an agent, deleted with the agent turned off |
| `agent_runs`, `agent_threads` | the runs of the Feedback dialog's Sends and the threads they answered, before step 3.7 of the plan removed Sends: left unread and unwritten, their rows (sessions, directories, stderr tails) deleted at every start (`store.carry_over`) |
| `agent_folders`, `agent_policies`, `agent_grants` | what each vault's agent was, what it could run and the chat grants keyed by the vault, before agents were the computer's: left unread. `store.carry_over`, at the start (`runner.recover`), once (nothing while `computer_agents` has a row), turns on for the computer every agent on for any vault, owned by the account that turned it on first, and lets it ask where a vault of it was let ask on this computer (`computer`, which covered the chat here and another server's turns, as `ask` now does; a vault's `ask`, its own chat's alone, carries `never`, so the person lets it ask through the desktop's warning); the grants are not carried |

`carry_over`, `agents()` (every row, in the catalog's order),
`enabled_agents()`, `agent_for(user_id)` (the account's first agent on),
`owns(user_id, agent)`, `runs_of(agent)`, `set_agent(agent, enabled, by=)`
and `set_runs(agent, runs, by=)` (each → whether it changed: turned on by
anyone while off, off and narrowed by its owner alone), `dirs()` and
`set_dirs(entries)` (the whole list, an offered id kept), `grant`,
`granted` and `grants_of` (by the directory), `people`, `named`,
`name_person`, `unname_person`, `lent_to(ws, account)` and `usable(user_id,
ws, agent)` (the agents another account lends the account, and whether it
may use one), `conversation_of`, `ran_conversation` (a chat
conversation's session, in place of the one it had), `bound` (the
conversation each of a CLI's sessions is bound to), `forget_conversation`
(a conversation deleted), `add_turn`, `turn_running`, `end_turn`,
`get_turn` and `open_turns` (a turn's row; `claimed_of` the row of a
turn of another server's page this computer claimed, by which this
server's own chat route answers such a turn's asks 409). What goes with a vault:
`forget_sessions(link_id)` (a relink: the conversations' sessions, which
belong to the directory that was);
`vault.store.forget_link` (a vault removed: its `agent_conversations` rows,
their grants and its ended `agent_turns`);
`forget_links(live_ids)` (vaults gone while this server was not looking:
the same). The agents and the grants are no vault's: they stay. A turn still open stays, so the next start
fails it and its message (`recover`). Nothing of this travels:
the workspace's members see the conversation's messages and the replies,
and nothing of the turn's row.

## The routes

| Method | Path | What |
|---|---|---|
| GET | `/api/local-agents?fresh=0\|1` | `{here, computer, agents: [{id, name, label, found, version, ready, problem, code, models, at, enabled, runs, mine, lent, people}], dirs: [{id, path, name, added_at}]}` (`here` whether this server runs agents, an agent's `id` its id as every agent has one, `agents.here`, which a comment sent to it names, `computer` this computer's name as its channels announce it; `code` how the agent stands as a code, `catalog.code`: `checking`, `missing`, `signed_out`, `not_ready` or `""`, which the client words; `models`: "Its model, its typing…"; `enabled` whether it is on for this computer, `runs` what it may run, `never` or `ask`, `mine` whether the session's account turned it on: the one who may turn it off or narrow it; `lent` `{by}` where another account's agent serves the session's account in its workspace, else null; `people` the people it serves, for its owner alone, `store.people`'s rows; `dirs` the directories offered beyond the vaults), Codex then Claude Code, from `catalog.cached`; `fresh=1` probes both anew, at most once every 10 s on this server (sooner, the kept answers). A signed-in account's session (`require_personal_user_id`; no token, no guest); when not `enabled()`, `{here: false, computer: "", agents: [], dirs: []}`, so every window asks it without an error (`gamma/routers/local_agents.py`) |
| GET | `/api/local-agents/people` | `{places: [{server, workspace_id, name, origin, people: [{id, username, role}], error}]}`: whom the session's account may let use an agent it turned on ("A co-author's turn"): the workspaces of this server it edits with others (`server` ""), then each of its channels' workspaces, asked there (`channel.members`, `GET /api/computers/people`; `origin` that server's, `error` why it did not answer); the editors and owners of each, the account left out. 404 where agents do not run |
| POST | `/api/local-agents/{agent}/people` | `{server, workspace_id, account}` → `{people}`: the person may use the agent from now on (`store.name_person`). The shell's word (403 "Only the desktop app may let another person use an agent on this computer."), the agent's owner (403, 404 for an agent Gamma does not know), a person who edits that workspace beside the owner (400; on another server, as that server lists them: 404 for a server no channel of the account names, 502 for one that does not answer), at most 50 (400) |
| DELETE | `/api/local-agents/{agent}/people?server=&workspace_id=&account=` | → `{people}`: the person may no longer use it; their turns of it running here stop (`runner.stop_person`). The agent's owner, else the shell's word never unchecked (`shell_strict`, 403); 404 for an agent that is off |
| PATCH | `/api/local-agents/{agent}` | `{enabled?, runs?}` → the agent as the listing says it. The gate, in order: `enabled()` (404 "Agents run on the desktop app's own server."), a session, an agent Gamma knows (404 "Gamma knows no agent named …"), a known `runs` (400 "What an agent may run is never or ask."); turning it on: the shell's word (`shell.from_shell`, 403 "Only the desktop app may turn on an agent on this computer.") and `catalog.probe(name, fresh=True)` ready (400 with its problem), or, while it is on, its owner (409 "Codex was turned on by another account on this computer."); turning it off: its owner, or the shell's word never unchecked (`shell.shell_strict`, 403 "Only the account that turned on Codex, or the desktop app, may turn it off."), which turns it off whoever owns it; `runs`: the agent on after this PATCH's `enabled` (400 "Turn Codex on first."), its owner (403), and for `ask` the shell's word (403 "Only the desktop app may let an agent ask to run commands on this computer."). Turned on, its account owns it (`store.set_agent`); turned off, its turns stop (`runner.stop_agent`); either tells the work folders' open pages and the channels (`folder_links.agents_changed`); set back to `never`, its turns' asks are declined (`runner.runs_never`) |
| PUT | `/api/local-agents/dirs` | `[{id?, path, name}]` → `{dirs}`: the directories offered to the agents beyond the vaults, replaced whole (`store.set_dirs`: an `id` offered keeps its row and `added_at`, one left out goes, a name left empty is the directory's own). An entry that names a path not offered under its id takes the shell's word (403 "Only the desktop app itself may name a path on this computer."), and must be a directory here (400) that a vault could have (`folder_links.clean_path`, 400 with its refusal); any change takes an account with an agent on here, else the shell's word (403 "Only an account with an agent on this computer, or the desktop app, may change the directories it offers."). 400 for a new path inside or around a vault here (a directory vault, or a file vault's own file: "… is inside or around the synced folder …") or another entry of the list ("… is inside or around …, also offered."), for more than 50, or a path or an id given twice; 404 where agents do not run |
| GET | `/api/folder-links` | each link's `agent`: `{name, label, ready, problem, running: {detail} \| null}` for a work folder, of the agent that works there (`local_agents.info.listing`: the one at work there now, else the vault maker's first agent on), else null (one store query for the whole listing, readiness from `catalog.cached`, nothing run). `PATCH /api/folder-links/{id}` sets no agent, the agents being the computer's; a pause, a relink and a delete stop the vault's turn. A member who cannot act on the vault (`_acts`) reads `running.detail` as "" (it may name files) |
| GET | `/api/folder-links/page/{page_id}` | the page's `vault` gains the same `agent` (`folder_links.page_vault`), on a server that runs agents only, and `mine` (whether this account made the vault) on every server. Turning an agent on or off tells each open page of every work folder here `{t: "vault"}` (`folder_links.agents_changed`, `_tell_open_pages`, from `collab.open_pages()`), so its chip and the comments' @ menu read it again |
| POST, PATCH | `/api/pages/{id}/comments`, `/api/comments/{id}` | a comment sent to an agent (`to`, or its `@name`) is a turn of its thread too, answered as `turn` ("A comment sent to an agent") |
| GET | `/api/local-agents/chat?bucket=&version=&agent=&thread=&conversation=` | the chat's computer side ("The chat's computer side") of a bucket (a page's id, a folder's or `home`), an account with an agent on here, a co-author an agent here serves (a synced folder's file or folder alone), or the vault's maker on its work folder: `{computer, conversation}`, `computer` the strip (`{agent, label, ready, problem, models, folder, paused, turn, session, folder_id, dir, grants, lent}`, `agent` the one picked (`agent`, while the account turned it on or its owner lends it) or else the account's first (`store.agent_for`, else the first lent), null for none, `session` whether a terminal can continue the conversation (never a co-author's), `ready` and `problem` from `catalog.cached`, `turn` `runner.chat_state` or null, `folder`, `folder_id` and `paused` the work folder's ("", null and false outside one), `dir` where the agent works (`{kind: "vault", name, path}`, the path for the vault's maker alone; `{kind: "offered", id, name}`; null for a directory of its own), `grants` the commands allowed in the conversation in that directory, `[{command, cwd, host}]` (none for a co-author's), `lent` `{by}` for an agent another account lends, else null) and `conversation` the bucket's active one (`{id, title, agent, thread_id, messages, version}`, null for none, and for one of another agent than `agent`), or with `thread` the account's conversation of that comment thread on the bucket, else a co-author's there with the account's agent ("A comment sent to an agent", "A co-author's turn"), or with `conversation` a co-author's chat conversation of the bucket with the account's agent (`chats.lent_one`; null for any other), a co-author's carrying `person`, their username; with `version` the stored one, `{computer, same: true}` |
| POST | `/api/local-agents/chat/turns` | `{bucket, text, selections?, id?, agent?, model?, effort?, dir?, skill?}` → 202 `{conversation, turn: {id, state}}`, `agent` the one picked (left out, the account's first, `store.agent_for`; another computer's turn runs that computer's), `dir` where the conversation works outside a work folder kept here (an offered directory's id, or `""` for one of its own; left out, the active conversation's): another `agent` or a `dir` other than the active conversation's starts a new one (`chats.begin`); `skill` the skill the message runs (`{id, name, args}`, [skills.md](skills.md#a-computers-agent)), 400 `{detail: "The skill /x was deleted or turned off.", kind: "skill"}` (`skills.SkillGone`) for one the account's catalog does not hold; 400 for no text and no skill, past 20,000 characters, a malformed `id`, a `dir` no offered directory has, or a model or effort the agent did not list; 409 while a turn of the conversation is queued or running ("Codex is still working on the last message…", named by that turn's agent), while the account has no agent on here ("Turn on Codex or Claude Code in Settings → AI → Local agents first.") or not the one picked ("Turn on Claude Code in Settings → AI first."), for a folder paused or choosing its files, for an agent known not to be ready (its problem), and for a skill turn on another computer that did not announce `skills` ("Update Gamma on TIM-PC to run skills there."); 403 when the account is no editor or writes are refused. A repeated `id` (the person's message's) answers the turn it made |
| POST | `/api/local-agents/chat/turns/{id}/stop` | `{stopped}` (`runner.stop_chat`); the conversation's account's, or its agent's owner's (`chats.readable`) |
| POST | `/api/local-agents/chat/turns/{id}/steer` | `{id, text, selections?}` → `{steered: true}`: a message steered into the running turn (`runner.steer_chat`; a turn on another computer kept for it, `far_turns.steer_run`, and the computer woken). The conversation's own account alone. 400 for no text, past 20,000 characters or a malformed `id`; 404 for a turn of no conversation of the account's; 409 for one that takes none now ("The agent is not taking messages now: …"): queued, ended, or its agent refused it, and for a body with `skill` ("A message that runs a skill goes as a turn of its own.", which the client then queues) |
| POST | `/api/local-agents/chat/new` | `{bucket}` → `{conversation: null}`, the active conversation archived whichever its agent; 409 while a turn runs |
| GET | `/api/local-agents/chat/history?bucket=` | `{conversations: [{id, title, agent, updated_at, turns}], active, lent}`: the archived ones newest first (never a thread's), and the active one as they are listed (`chats.listed`, null for none), whichever its agent, which the chat's History lists while another agent is picked; the account's own on every server, whatever became of its agents (no seat asked); `lent` the chat conversations there, active or archived, of the co-authors the account's agents serve, each with its `person` (`chats.lent_history`), read, stopped, answered and exported, never opened or deleted (404) |
| GET | `/api/local-agents/chat/history/{id}/export` | a conversation of the account's, active or archived, or a co-author's with the account's agent (`chats.readable`), as a file to keep (`chat_export.export_file` with the agent's label: the person's messages with their passages, the agent's steps in words, its reply, what it changed and how its turn ended); 404 for another account's, as the other history routes; like the list and delete, it asks no seat, so a conversation whose agent was turned off is still the account's to keep |
| DELETE | `/api/local-agents/chat/grants/{id}` | the conversation's Allowed in this chat taken back (`store.forget_grants`) → `{forgotten: true}`; 404 for a conversation not the account's |
| POST, DELETE | `/api/local-agents/chat/history/{id}/open`, `/api/local-agents/chat/history/{id}` | open (→ `{conversation}`, 409 while a turn runs, 404 for one not archived) or delete (→ `{deleted: true}`, 409 for the active one, whatever became of its agent) an archived conversation; open takes the bucket's seat, as a turn does |
| POST | `/api/local-agents/chat/turns/{turn}/asks/{ask}` | `{decision: "once" \| "chat" \| "deny", note?}` (a note at most 2,000 characters) → `{answered: true}`: the person's answer to a waiting ask ("Asking in the chat"), or in a co-author's turn that person's or the agent's owner's; 400 for another decision, a longer note or `chat` on anything but a command or in a co-author's turn ("A co-author's turn is allowed once at most."); 404 for no such turn the account reads or ask; 409 when it was answered, withdrawn or declined already, or the turn ended, and for a turn that runs on another computer, on every server ("This is answered on the computer that runs the agent.") |
| GET | `/api/local-agents/asks?after=&wait=` | the desktop app's alone ("Asking on this computer"): `{version, asks}`, the asks waiting on remote turns of the account's vaults and on co-authors' turns of its agents (each card's `for` the person), oldest first, held up to `wait` s (at most 20) while `version` is `after`; 404 where agents do not run, 403 "Only the desktop app reads what waits for an answer on this computer." without the shell's word or with no secret |
| POST | `/api/local-agents/asks/{ask}` | the desktop app's alone: `{decision: "once" \| "deny"}` → `{answered: true, version}`, given to the agent before it returns; 403 "Only the desktop app answers on this computer."; 404 "No question on this computer has that id." (another account's too); 400 "On this computer an ask is allowed once or declined."; 409 "{agent} no longer waits for that." or "This turn has ended." |
| GET | `/api/local-agents/chat/terminal?bucket=` | the desktop shell's alone in practice (Continue in a terminal): `{cwd, argv, agent, label}`, the conversation's session resumed in the directory it works in, with the conversation's agent ("Its model, its typing, a terminal…"); 404 "This conversation has no session to continue.", 409 while a turn runs, when the account no longer has that agent on here or it is not on this computer |
| POST | `/api/local-agents/chat/sources` | `{bucket, selections: [{text, page, box}]}` → `{sources: [{file, line, end?} \| null]}`, in order, nulls outside a work folder kept here; 400 past 20 selections |
| GET | `/api/local-agents/sessions?agent=&dir=` | `{sessions: [{id, title, at, created, source, ours, busy}]}`: the sessions the CLI of `agent` keeps of the directory `dir` names, the most recently used first ("Sessions started elsewhere"; `source` one of `cli`, `vscode`, `exec`, `app`, `gamma`, `other`). The agent's owner alone (404 for a co-author, another account, an agent that is off, a `dir` that is no work folder whose vault the account made here and no offered directory), 403 for a token or the guest, 404 where agents do not run; 409 when Codex is not here or cannot list them. On every server for an agent of another computer of the account's (`agent` its id, `dir` a work folder it keeps or a directory it offers, by id; 404 otherwise, a lent computer's too), asked over its channel, with `away` (`offline`, `older`, `waiting`) and no session while it cannot answer |
| POST | `/api/local-agents/sessions/open` | `{bucket, agent, dir, session}` → `{conversation}`: the session opened as the bucket's active conversation, bound to it (`chats.adopt`), or the conversation of the account's in that bucket that continues it already. The same gate; 400 when the bucket's agent works in another directory; 404 for no such session of that directory; 409 while another process holds it (with why), while a conversation elsewhere continues it (where), while the bucket's active conversation's turn is to end, when Codex cannot read it; 403 when the account is no editor or writes are refused. For another computer's agent (its id) the same, that computer reading and binding the session; 409 too while it is offline, older ("TIM-PC needs a newer Gamma to open its sessions here.") or not answering within 30 s ("TIM-PC did not answer: try again.") |

The chat routes answer 404 on a server that runs no agents, for no such
bucket (a live page, a folder, `home`), and for an account that has no
agent on here and did not make the vault of the bucket's work folder (so
nobody else learns it exists), 403 for a token or the guest
(`require_personal_user_id`).

## The client

- **Comments** (`comments/comments.js`): a computer agent's comment
  (`agent_kind: "computer"`, `agent_on` its computer) reads *Codex* with
  *on TIM-PC, for tim* after it (`agentFor`) beside its bot avatar
  (`AgentAvatar`), and its `authorTitle` "Written by Codex on TIM-PC, for
  tim";
  `turnChip` of a thread's turn reads *Codex is working…* with a spinner,
  *Waiting for Codex* (or *Waiting for TIM-PC*) while it is queued, and
  *Codex is waiting for you* while it asks (comments.md "Sent to an
  agent").
- **The agents' list** (`frontend/src/chat/agents.js`, pure,
  `tests/chatAgents.test.mjs`): one list for the chat's picker, the
  comment fields and Settings, `[{id, kind: "gamma" | "computer", name,
  label, where, models, efforts, ready, problem, owner, …}]`. App reads
  `GET /api/local-agents` once for the window and again when it comes back
  (`chat/useLocalAgents.js`, a store every reader shares: the chat, the
  comments and Settings, which reads it afresh and after each change it
  makes) and the page's computers (`vault/usePageComputer.js`; where no
  page is open, a folder's or the library's chat, the account's computers,
  `GET /api/computers`, without another account's lent ones:
  `vaultState.readAccountComputers`), and builds
  `chatAgents`: Gamma AI (`gammaAgent`, from the AI info and its
  connection's models), then the agents of the page's computers that keep
  its work folder (`farAgents`, by their announce's codes, each agent on
  of each computer, `id` its `agentId`, which the chat's routes take),
  then the agents this computer runs that the account turned on
  (`localAgent`: `enabled` and `mine`, `id` its name), then the other
  computers' agents, an offline one's listed offline. The picker names a
  computer's agent with its computer (*Codex on TIM-PC*), so two
  computers' read apart. Each agent's state is worded from its code (`checking`,
  `missing`, `signed_out`, `not_ready`); this computer's own problem
  sentence is shown as it came, since it names the command that mends it.
- **A comment sent to an agent.** App derives the agents the open page's
  comments can be sent to from that list, `agents.commentAgents(list)`:
  `{id, name, label, ready, problem}` of each computer agent the chat
  lists for the page (`id` its `agentId`, `computer:<computer>:<name>`:
  this computer's from the listing's `id`, another computer's from the
  page's computers' `id`), with `computer` and `online` for another
  computer's and `lentBy` for a co-author's (its owner). None while the
  reader may not write the workspace (a viewer, a share's visitor: App's
  `canWriteWorkspace`, as the server refuses `viewer`), so no tick is
  offered that always refuses. They reach every comment field as
  `links.agents` (the popover's and the margin's) with `links.sendTo` (the
  tick as the person left it last, the account preference
  `commentSendTo`), never `MentionContext`, which reaches the notes. There
  the @ menu lists them under People (`mentions.mentionRows`' `agents`: the
  agent's avatar, the label, "on this computer" or its problem; a member of the
  same name is not listed there), the field and the person's own posted
  comments draw `@codex` as the agent's chip (`mentionChip`'s `agents`),
  and a new comment's and a reply's field has the *Send to {agent}* tick
  (comments.md "Sent to an agent", `comments/sendTo.js`: "Send to Codex on
  TIM-PC", "Send to alice's Codex on TIM-PC"). `useComments`
  posts the tick's agent as `to`, takes the answer's `thread` (with its
  `agent_chat`) and keeps a refusal on its thread until the next send
  there (the thread's `refused`: "Not sent to Codex", `comments.turnRefusal`
  by its code, else the server's why, and *Send again*).
- **The picker.** App passes the list to ChatDock (`agents`, with this
  computer's listing as `localAgents`: the offered directories, and the
  agents found that the setup card offers, each tile led by its agent's
  icon). The composer's chip (`chat/AgentChip.jsx`,
  a `MenuSelect`) lists the agents first, by their icon and where they
  run ("Codex", "Codex on TIM-PC"), a muted "offline" after another
  computer that is away and an amber "not ready" after one with a problem
  (`computerChat.agentHint`, the sentence the row's title; still
  pickable), then the picked one's models and
  efforts and speeds (Gamma AI's and the computer agent's model's); where a computer's agent works outside a
  synced folder is the bar's folder chip (below, "The strip"), in every
  bucket: a page, a folder, the library. The chip reads the agent, then its model and effort ("Codex on
  TIM-PC · GPT-5.5 · high", the model ellipsized first, a speed chosen its
  glyph after them), named "Agent, model and reasoning effort" (with a
  speed, "Agent, model, reasoning effort and speed (fast)"). A viewer has Gamma AI alone. The agent shown is the bucket's
  pick for the tab's life (`chatSide.js` `useChatAgent`, beside
  `showInChat`, which the Background tasks rows and a thread's *Open in
  chat* call, so the first screen does not load the side's state), else
  `agents.pickAgent`: Gamma AI, unless no AI
  service is connected and a computer's agent can answer, which then is
  the chat's own. Picking another agent in a conversation that has
  messages asks *Start a new chat with Codex?* (`newChatQuestion`); on yes
  the agent picked starts a new conversation (Gamma AI's New chat, or
  `POST /chat/new` for a computer's agent) and the one left stays where it
  is, in History or as that agent's current one. An ask from App (a
  handwriting block's Transcribe) picks Gamma AI. A conversation opened
  from History of another agent picks that agent.
- **One transcript.** A conversation with a computer's agent is drawn by
  the chat's own transcript (`chat/ChatTranscript.jsx`, the rows ChatDock
  draws Gamma AI's with) and composer. `chat/computer/` reads it and hands
  it over in the shape the transcript draws: `useComputerChat` reads the
  conversation of the agent picked (`?agent=`; a turn names it too) as the
  chat shows it, on focus, after each action and every 1.5 s while a turn
  is to end, with `version` so an unchanged one answers `same`, and
  numbers its reads so only the newest lands; `useComputerSide` is what
  ChatDock reads of it (the messages, Stop's turn, whether a message can
  go, the chip's models, efforts and speeds, the Allowed in this chat chip, where
  a conversation that begins works, the passages' source lines); and
  `computerChat.shownMessages`, pure, maps each message: the person's to
  `{role: "user", text, passages}` (its passages' pages as the bubble's
  chips), the agent's to `{role: "ai", text, partial, pill, actions,
  asks, files, notice, model, effort, speed}`: `partial` while its turn is queued
  or running (the reply as it is typed, Stop in Send's place), `pill` the
  steps pill's words (`turnLabel`: "Codex is working: Editing
  sections/intro.tex", while queued "Starting Codex…" or "Waiting for
  Codex to finish its other work…" (the server's `turn.ahead`; `queued`:
  a clock, no shimmer), "Codex is waiting for you", and once it ended
  Gamma AI's summary of its chips, `agentSteps.stepsSummary`: "3 steps ·
  read 2 files, edited 1 file, ran latexmk", each file counted once, the
  steps that failed or were not allowed left out), `actions`
  its steps as plain chips of kinds of their own (`stepChip`: a file
  read, a file edited, a command, a search, other work, with the file's
  `path` and the command's `program`; a call of Gamma's
  tools the kind Gamma AI's own call of that tool is, `GAMMA_KINDS`: list,
  view, cite, read…; none a change of
  the library or the notes, so Gamma AI's changes list and revert pass
  them by). A call of Gamma's tools reads as Gamma AI's own call of that
  tool (`agentSteps.toolWords`): runningLabel's words while it runs
  ("Codex is working: Listing the highlights and comments…"), their done
  twin after ("Listed the highlights and comments"), and a tool neither
  names by its name ("Read Gamma: new_tool"). `asks` while it waits on the person and the page draws them,
  `files` what it changed, `notice` the line of a session not resumed,
  `model` the model's name. A stopped turn ends "(stopped)" as Gamma AI's
  stopped reply does; a failed one carries the error card's fields, kind
  `agent` (`chatErrors.failureCopy`: the computer's own sentence is the
  headline), with **Retry** on the newest, which sends the person's
  message again with its passages. What a row offers comes from the
  handlers ChatDock passes: editing a sent message, reporting a reply and
  reverting a change are Gamma AI's alone; Save to library reads either's
  text. The words are the client's (`computerChat.js`, through `t()`); the
  server sends structured steps, which never carry a `page_id` or
  `block_id`, and nothing of such a conversation goes through
  `/api/ai/*`, `chatSession`, `MentionContext` or Gamma's model list.
  `tests/computerChat.test.mjs` maps each kind of message.
- **The strip** (`chat/computer/ComputerStrip.jsx`) is such a
  conversation's health strip, in the provider strip's slot: a plain line
  (not a live region: the turn's pill is the one read out)
  (`computerChat.stripLine`), at rest "Codex on this computer · paper",
  which it keeps while a turn is queued or runs (the pill says how it
  goes); "Checking Codex…" with a spinner until the agent's state is
  known; amber with a shield while it waits for an answer ("Waiting for
  you: Codex wants to run a command"), amber when the agent cannot run
  (its problem), its computer is away or the synced folder is paused;
  drawn from the list's facts (`agents.stripFacts`) until the read lands.
  Where the agent works outside a synced folder (`computerChat.workPlace`)
  is the **folder chip** at the start of the composer's bar
  (`chat/computer/PlaceChip.jsx`, a `MenuSelect` opening upward from its
  left edge, `start`), as a Codex thread's project: the folder glyph and
  the directory's name, titled "Codex works in Notes". Its menu, *Works
  in*, lists *Its own directory* and the offered directories, the ones
  used last first, and in the desktop app on this computer's own server,
  for its own agent (`offerDirs`: `IS_DESKTOP` and the listing's `here`),
  *Add folder…* after a rule (`addPlace`): the shell's folder dialog
  (`askShellWatching("agent-dirs", {dir: "new"})`, the same ask as
  Settings' *Offer a directory…*, its question and its word), then the
  listing read each second until it lists one more, which the
  conversation that begins then works in. A new conversation starts where
  the last one of this browser began (`chat/computer/usedDirs.js`,
  `gamma-agent-dirs-used`: the ids, the newest first, "" for a directory
  of its own, noted at a first turn; one no longer offered passed over),
  as Codex opens a new thread in the project used last; a pick in the
  bucket comes first. The choice is sent with the first turn as `dir`;
  then it is the conversation's: the chip's rows are locked with the title
  "Start a new chat to work elsewhere", the trigger titled "Codex works in
  Notes in this conversation: start a new chat to work elsewhere", and
  *Add folder…* is gone. Its ⋯
  offers Continue in a terminal and, on a page of a work folder kept here
  alone (`revealsFile`), Show in folder.
- **What it asks.** While a live turn has `asks`, each is a card under the
  steps (`ApprovalCard.jsx` `AskCard`, on the approval card's shell
  `chat/ApprovalShell.jsx` but with no *Always allow* and no Gamma
  permission): "Codex wants to run a command" with the command whole in
  its box in monospace, where it runs and its reason, or "Codex wants to
  change files outside its sandbox" with the paths and the diff; **Allow
  once**, **Allow in this chat** (a command only: "Run this exact command
  without asking for the rest of this conversation"; never on an ask the
  server marks `once`, a co-author's turn's) and **Don't allow** (a note,
  then Don't allow). The box shows `askCommand`: the inner
  command of a shell wrapper with "Through PowerShell: …" under it, else
  `command_shown` with its `⟨U+…⟩` marks in the warning colour ("This
  holds 2 characters that do not show: …"), "3 lines" over it, "It reaches
  example.org on the network" for a `host`; the allow buttons stay off
  ("Scroll to the end first: …", said as the first line under them, not
  in a title alone) until the command's box and the diff were scrolled to
  their end (`scrolledToEnd`, the answers' `disabled`). Under them "It runs as you, with your files and the network." or "It changes
  files outside the work folder."; the strip reads "Waiting for you: Codex
  wants to run a command" (amber, `asking`, a shield before it), the turn's pill "Codex is
  waiting for you", and the steps "Asked to run latexmk", "Allowed to…",
  "Not allowed to…". `useComputerChat.answer` posts the answer and leaves
  the rest to the next poll, so the card says "Allowed. Codex goes on…" or
  "Not allowed. Codex goes on without it…" until the read that drops the
  ask; a refusal is said on the card and the conversation read again at
  once. Another computer's turn draws the card of what its computer relays
  here (its message's `asks`, each `once`), the same way; one that waits
  on that computer alone (`waiting`, `waitsThere`) draws none and says
  where it waits.
- **What it changed** is Gamma AI's changes card (`AgentChanges.jsx`
  `FileChanges`, "Changed in the synced folder · 2", "Changed in its
  directory" outside one): a row a file, its path in a synced folder a
  `PathLink` resolved from the work folder's top (the strip's `folder_id`,
  the transcript's `PathLinkContext` while such a conversation shows), and
  outside one plain monospace text, since no page holds it; the first
  three then "and N more" unfolding to the rest, and "The PDF was rebuilt"
  last.
- **The composer** takes the message and the PDF passages selected (App's
  `pdfSelections`, Gamma AI's too, cleared once the server accepts the
  turn), each chip noting its source line; what only Gamma AI takes (the
  notes and the cursor's block, the @ pages, files, pictures, the PDF
  switch, the + menu) is not offered. The field is named "Message Codex"
  ("Message Codex on this computer…" its placeholder, or "on TIM-PC"); the
  **Allowed in this chat** chip shows while the strip lists `grants`
  (Gamma AI's `GrantChip`); Send, and Stop in its place while a turn is
  queued or running and the box is empty. A message sent meanwhile waits
  in the chat's queue with its passages and goes as the next turn once
  this one ends done; a turn stopped or failed pauses it. Its **Steer**
  hands it to the turn while the agent works (`steeringTurn`: running, not
  waiting for its directory), and the turn's row shows it where the agent
  took it in (`computerChat.js` `steerParts`: the part before it, the
  message, the turn going on; [ai.md](ai.md#queue-and-steer)). A first read that fails says so where the
  conversation would be ("Could not load chat: …", **Try again**) and
  Send stays off until a read lands; a poll that fails keeps what was read.
- **The header** has what applies to such a conversation: Export (the
  conversation, `GET /chat/history/{id}/export`), Find, History and New
  chat, which keeps the conversation in History; New chat is off while a
  turn is to end ("Stop Codex or wait for it to finish…"), and while a
  comment thread's or a co-author's conversation is shown, its title
  saying so: the line in the composer's place has *Back to the chat*
  (a ghost button led by the back arrow). No AI service
  connected closes none of it.
- **History** (`ChatHistoryPop`) lists every agent's conversations of the
  bucket together (`computerChat.historyRows`): the one shown first, then
  by time Gamma AI's archived ones (`GET /api/chat-history`), the computer
  agents' (`GET /chat/history`, read beside it whatever agents are on, so
  a conversation with an agent since turned off can still be exported or
  deleted; opening it takes that agent on again), and each agent's current conversation while another
  agent is shown (Gamma AI's, or the computer's `active`), which picking
  that agent shows. Each row leads with its agent's icon while the chat
  has several agents (`AgentChip.agentIcon`). A row opens with its agent
  picked: Gamma AI's swaps with its current one, a computer agent's is
  opened (`POST /chat/history/{id}/open`), and while that agent's turn is
  to end its rows are greyed and titled "Stop Codex or wait for it to
  finish to open another conversation". Its menu exports any row, renames
  Gamma AI's, and selects and deletes the archived ones of either. The
  owner of an agent sees its co-authors' chat conversations there too,
  each named after its title by whose it is ("bob's chat with Codex",
  `.chatHistBy`): one opens in place of the owner's own conversation
  (`showInChat`, `{conversation}`), its composer giving way to a line, "This
  is bob's chat with Codex: you can read it, stop it and answer what it
  asks.", with *Stop* while a turn of it is to end and *Back to the chat*,
  as a thread's does (whose line has its comment's button too, led by the
  speech bubble); its menu exports it, and it is never selected or
  deleted.
- **The vault chip's card** (`vault/VaultChip.jsx`, `vaultState.agentLine`):
  "Agent: Codex on this computer", the problem when it is not ready (amber,
  `.agentProblem`), and during a run the live tag "Codex is working…" with
  its detail on a line under it, the chip's icon a spinner. Opening the card reads the
  page's standing again (App's `vault.refresh`), since a run's start and
  end tell the page's room nothing.
- **The Inbox** names an agent's item by the agent's id
  ([comments.md](comments.md) "The inbox"), worded in the reader's
  language (`chat/agents.js` `agentWords`).

## The desktop app

The agents of this computer are set in Settings → AI → Local agents, a page of Gamma,
which asks the desktop shell for every change that widens what an agent
reaches (the page asks of `desktop/preload.js`, [desktop architecture](../../desktop/docs/architecture.md)
"Pages asking the shell"): `set-agent` (`agent`, `enabled`), `set-agent-runs`
(`agent`, `ask`) and `agent-dirs` (`dir`: "new", or the id of an offered
directory to move), each only from a page of a server on this computer.
The shell's own session acts (`desktop/lib/thisComputer.js` `setAgent`, `setAgentRuns`,
`agentDirs`), so its account owns an agent it turns on:

- **Turning one on** asks first (`keeping.agentAsk`: "Turn on Codex on this
  computer?", what it does in the person's chats on this computer (in a
  synced folder's directory, a directory offered to it or one of its own),
  and that on a server this computer is shown on whoever can sign in there as
  the person, and whoever runs it, can send work to it), then `PATCH
  /api/local-agents/{agent}` `{enabled: true}` with the shell's word while
  the bar reads "Checking Codex…"; a notice says what came of it
  (`keeping.agentSaid`). Turning it off asks
  nothing and carries the word too, so the shell turns off an agent
  another account owns (one signed out of the app, or gone). An agent on already, or off already, is
  left as it is.
- **Letting it ask** confirms first, Cancel the default (`keeping.runsAsk`:
  "Let Codex ask to run more?", what it may then ask for in the person's
  chats on this computer, wherever it works, that a turn started on another server asks in Gamma's
  window here, whose words that server's people write, and that an
  allowed command runs as the person, with their files and the network),
  then `{runs: "ask"}` with the shell's word; `never` asks nothing
  (`keeping.runsSaid`).
- **A directory offered** is picked with the native dialog
  (`pickDirectory`) and put whole (`PUT /api/local-agents/dirs`, with the
  shell's word; `keeping.dirSaid`); the page leaves one out or renames it
  itself.

The sync panel sets no agent: a work folder's view says that Codex and
Claude Code work there once they are turned on in Gamma's Settings → AI →
Agents (`bar.html` `AGENTS_SET`). `lib/keeping.js` `vault()` still gives the item
`agentLabel` and `agentRunning` from the link's `agent`: while a turn works
the row reads busy, *Codex is working · Editing main.tex*, its title
saying it works on a file of the folder, and the title bar's summary
*Agent working* when no round runs anywhere; a vault's own problem
(stopped, held, its directory gone, a sync error) keeps its row, the turn
said in its title.

`refreshKeeping` reads `GET /api/local-agents` from each local server (5 s
timeout; a 404 or no answer is a server that offers no agent): on the host,
while an agent may ask (`runs` `ask`) and the host keeps a work folder of
another server, the shell follows `GET /api/local-agents/asks`, whose cards
are drawn in the bar over the whole window and answered with `POST
…/asks/{id}` ("Asking on this computer"; the shell's side is the
[desktop architecture](../../desktop/docs/architecture.md)'s). The card's
*Stop asking* declines and sets what its agent may run back to `never`.

## Trust

- **Who starts a turn.** Only an agent's owner, the account that turned it
  on for this computer, by a chat message or a comment of their own sent to
  the agent (never through a share, never a token's), while still at least
  an editor of the workspace; on a page of a work folder a vault here
  keeps, the agent works in that vault's directory whoever made the vault.
  A co-author the owner named on the computer (the shell's word and a
  question carrying the consent of the account's terms: "A co-author's
  turn") starts a chat turn or a comment's turn too, on the files of a
  synced folder alone. A comment of anyone else's, a token's or the agent's
  own reply starts nothing, so a mirror or a script holding the owner's
  write token cannot start one (a token's `to` is refused). On a server
  this computer is shown on, the account of the channel (and the
  co-authors named for it) starts turns on any page there, in a directory
  this computer offers or one of its own, or in the vault of a work folder
  it keeps ("Another server's chat"). Turning an agent on, and letting it
  ask, take the desktop app's own word on the app's server, and its
  question names what a server this computer is shown on can then do; a
  page of Gamma cannot ("Who turns it on").
- **What each agent may touch.** Codex: the vault's directory, written in
  its `workspace-write` sandbox, network off, approvals refused, no MCP
  server but Gamma's and no web search; its commands run, the build among
  them. It **reads** whatever the account can read: the sandbox confines
  writes and the network, not reads. Claude Code: the file tools inside the
  directory (`--restricted`), edits accepted there only, no write to a
  settings, git or tool-configuration file, no shell, no MCP server but
  Gamma's, no web tools, no settings file of the folder's. Its environment
  has none of the server's variables; it writes in Gamma through its turn's
  token alone, and Gamma writes a thread's reply from its final message
  when it wrote none. Nothing is started with a bypass flag.
- **The turn's token** ("Gamma's tools"). The one Gamma credential an agent
  holds: Gamma's read tools on MCP, and its comments, resolves and
  highlights, in the turn's workspace alone, as the turn's owner and signed
  as the agent; refused by every route of the HTTP API; gone when the turn
  ends and at the next start, and never more than 60 s past its turn's
  deadline (the run's timeout from the agent's start, moved while an ask
  holds the turn). It reaches
  the agent's process in `GAMMA_AGENT_MCP_TOKEN` alone. Codex's own
  commands do not inherit it (`shell_environment_policy.exclude`, the
  user's own excludes kept beside it). A far turn's token is the other
  server's, minted there at the claim for that turn alone and deleted
  there at its end: it reads and writes that server's workspace as the
  account the turn is for, as a local turn's reads this one's, and this
  computer gives it to the agent only where the address that server named
  is that server's own origin, so a server cannot hand the agent a token
  for somewhere else, nor have one sent there.
  **In an ask turn Claude Code's Bash can read it**: an allowed command
  inherits Claude Code's environment, and no setting of Claude Code's was
  found that keeps one variable from Bash (C2 of the plan). The plan accepts
  that: the token only reads what the person can read in that workspace,
  and only for that turn, and the command was the person's to allow; a
  command that sends it elsewhere sends what the person reads anyway, until
  the turn ends. Any process of the same user can read another's
  environment by the operating system's means, so the token is kept short
  rather than hidden. The agent reads PDFs and other people's comments
  while it holds it (prompt injection, below): what it reads can only be
  read by the person already, and nothing it holds writes.
- **What asking opens** ("Asking in the chat"). Only where its owner let the
  agent ask at the computer, only on a card in its conversation, only what the
  person allows on a card, one exact command at a time: an allowed command
  runs as the person, with their files and the network (Codex outside its
  sandbox, Claude Code with no sandbox at all), which is what the card and
  the panel's question say. No answer is ever a rule: no prefix, no
  *always*, nothing written into the agent's settings or the folder. What
  is left: Claude Code may write a `.latexmkrc` (not a protected file)
  without asking, and a `latexmk` the person then allows runs it, Perl
  included; a grant of `latexmk` in this chat lets the next turn do so
  unasked. The card shows the command exactly as given for that reason,
  and a chat grant holds for one command in one directory only.
- **A co-author's turn** ("A co-author's turn"). It runs in the directory
  of the synced folder its page is a file of and nowhere else, with the
  co-author's token (what they can read, in that workspace), their words
  the instruction, the owner's sign-in of the CLI. What it asks is allowed
  once at most, by that person or the owner, and no grant outlives the ask;
  on another server it asks only where the link relays asks there, once at
  most too. Its conversation is the person's and
  the owner reads it. Taking the person off stops their running turn and
  fails the next when it is taken, here and on the other server.
- **Which servers reach this computer.** A server reaches the agents only
  through a link this computer made to it ([computers.md](computers.md)
  "The servers this computer answers"): with a vault of it, or from
  Settings → AI → Local agents' *Add a server…*, the shell minting the token with
  the window's session and handing it over with its word. Such a server
  gives the agents turns on any of its pages, naming where each works by
  an id alone (a work folder of it a vault here keeps, a directory this
  computer offers, or none: an empty one of its own), which this computer
  resolves itself and refuses otherwise; it learns of the offered
  directories their ids and names, never a path. Removing the link stops
  the turns of that server running here and revokes its token there.
  Whether that server may answer what the agents ask (the link's
  `asks`, decision 7 of the plan) is off for every link until the shell's
  word turns it on after a question naming the server; a page cannot turn
  it on. While it is off every far turn's ask is answered on this computer
  alone (below).
- **Asking on this computer** ("Asking on this computer"). The person at
  the computer answers in the desktop app's bar, with the shell's word on
  routes that never take a session alone; the other server starts the
  turn, so it writes what the agent is asked to do, but, with the link's
  `asks` off, never the card or the answer, and learns `waiting` and the
  step statuses, never the ask. With it on (the shell's word after its
  question) the ask is written there too and whoever signs in there as the
  turn's person or the agent's owner can allow it, once at most, never as a
  grant: that server then learns the command and the places it names, and
  its answer reaches the agent only while the link still allows it
  (`runner.answer_relayed`). Over plain http asking is refused either way
  (a man in the middle would write the prompt, or the answer), without the
  desktop app's secret nobody here is asked (and none at all unless the
  link relays), and what this computer cannot show whole is declined
  unseen. The shell's secret is
  read once and taken off the server's environment (`config.shell_secret`,
  on Linux and macOS its bytes in the block the process began with too,
  which /proc and `ps eww` read), and no agent inherits a `GAMMA_*`
  variable of the server's (`procs.child_env`; the turn's token is its
  own), so a confined command cannot answer its own ask by reading it.
- **Prompt injection.** A viewer and a share visitor may comment, so a
  comment is not trusted text. Other members' comments reach the agent
  named as theirs (`is_owner` false), escaped in JSON, under an instruction
  that says they are context; that lowers the risk and does not remove it.
  A note page the work folder pulls as a file is on disk like any source:
  one named `AGENTS.md` or `CLAUDE.md`, which an editor of the workspace
  can write, is read by the agent as its own instructions.
  The answers are the way out such a request would take: a member's
  comment that asks for the contents of a file elsewhere on the disk (a
  key, a token) and that Codex follows lands in a reply that member reads,
  network or not. The instruction tells the agent such words are no
  instruction; on the desktop app's own server, reachable from this
  computer alone, the members are usually the person alone.
- **Nothing an agent writes loads by itself.** Its words are rendered as
  Markdown (a comment's reply by the notes' renderer, which takes raw
  HTML; a chat reply by the chat's), so a picture in them would be fetched
  by whoever opens it, its address carrying what the agent read: the
  network the sandbox keeps from the agent, reached through the window.
  `prompt.inert` rewrites every reply and chat text before it is stored:
  outside code an image becomes the link it names and a raw HTML tag's
  `<` is written `&lt;`; code is left as written, except code whose
  opening backtick is escaped (prose to Markdown) and a diagram fence
  (```` ```mermaid ````, ```` ```svg ````), which is shown as its source.
- **A session started elsewhere** is its owner's: listed and opened for
  the agent's owner alone, in a directory the picker names by its id
  (never a path from the page), and in a vault only for the account that
  made it. Its text is shown as any message is (the agent's through
  `prompt.inert`, its paths made relative as a turn's are) and never put
  in a prompt: the CLI keeps its own history and Gamma sends only the next
  message (`from_session` keeps it out of a turn's earlier messages, even
  when a resume fails and the turn starts afresh). A session another
  process holds is refused, never forked.
- **The replies** are comments of the workspace: every member who can
  open the file reads them. The running detail names only the files
  members see already (`tracked`); the turn's error record and its stderr
  stay on this computer, and a member who cannot act on the vault reads no
  running detail in the links or the chip.

## Known gaps

- Sessions started elsewhere: Codex's held lock on macOS and Linux is read as an exclusive `flock`, which check C4 saw
  only on Windows; Claude Code's sessions are read along the shapes 2.1.318
  writes, a later one's records unread where they differ.
- A row of Background tasks of a turn of another workspace opens its page
  there on the page's own chat, not on the turn's conversation: the app
  loads that workspace anew, and what the chat shows in place of its own
  lives in the tab (`chatSide.js`).

- The inner command of a shell wrapper (`unwrap_shell`) is checked against
  the shapes Gamma expects, not yet against Codex 0.167's real approvals
  on each platform: a shape it does not round-trip shows the whole
  command, as before.
- Asking on this computer: no *Allow in this chat* and no note on a
  decline (each command its own consent); remote turns may take both of
  the computer's slots while they wait (at most 10 minutes past their
  deadline); the host's own Background tasks list no remote turn (the bar
  and the tray say what waits); the other server learns how soon the
  person answers (`waiting`) and the agent's words after a decline.
  Whether Codex's sandbox reaches 127.0.0.1 and whether several approvals
  come for one item (the zsh exec bridge's `approvalId`) is to be checked
  against the real CLI; a `grantRoot` is declined whatever Codex does
  with it.
- Codex's Windows sandbox must be set up once in a terminal; until then
  Codex is not ready. On Windows, Codex edits through shell commands and
  reports no file changes as items, so the progress shows `Running …`
  where it edits (the changed files come from the disk).
- macOS and Linux: an app opened from the Dock has no login shell's PATH;
  an agent installed elsewhere than `FALLBACKS` (an nvm directory, say) is
  found only through `GAMMA_AGENT_CODEX` / `GAMMA_AGENT_CLAUDE`.
- The Mac App Store build runs no agents.
- macOS: a server killed hard (the shell's SIGKILL after its 10 s, a
  crash) leaves its agent running, since macOS has neither a Job object
  nor `PR_SET_PDEATHSIG`; the next start fails the turn but does not
  look for the process (the turn's row keeps its `pid`). On Linux only the
  agent itself gets the signal, not what it started.
- Gamma's own progress sentences (`Editing …`, `Stopped: …`) are English:
  the running detail and a turn's error are shown as they are.
- Claude Code answers with the account's default model: `--restricted`
  reads none of the user's settings files, a chosen model among them.
- Each reply of an agent is an Inbox item of its own. An Inbox item of
  before step 66 whose agent users.db could not find, and a thread an
  agent resolved before it whose agent no comment there names, keep the
  English label as their words.
- The agent's chip in a person's own comments follows the agents the page
  offers now, not which comments were sent to one.
- Deleting a comment sent to an agent does not stop its turn (the chip's
  Stop does).
- A thread the agent resolved needs Reopen before it is sent to the agent
  again (`closed`).
- Comments on a clone never reach this computer's agent, as they never
  reach its files (annotations.md "Known gaps"). Another server's do
  ("Another server's chat"), its chat too ("The chat on another server's
  page"); their asks are answered on this computer
  ("Asking on this computer"), and the terminal and Show in folder stay on
  this computer's own pages.
- The chat's computer side: Claude Code builds only where the folder lets
  it ask; no diff or Revert of what it changed (Gamma writes no file the
  disk made: a decision first), no *Attach what I see*, no *Open comments
  on this page*, no rename of a conversation in History. A server that
  learned no address of its own from this computer runs its far turns
  without Gamma's tools. The window polls while a turn is to end (no push), so the
  typed reply grows in steps of about a second and a half. *Continue in a
  terminal* is unit-tested on every platform but opened for real on none
  yet; on macOS and Linux the terminal is found by name. When Codex silently answers a resume with a new thread,
  the turn reads `fresh` but carries none of the earlier messages, since
  Codex says so only once the turn is under way. The running step of a
  turn is in `agent.running.detail` for anyone who may act on the
  vault (only tracked files named).

## Tests

- `tests/fake_agents/fake_codex.py`, `fake_claude.py`: standard-library
  stand-ins for the two CLIs (`--version`, the status, `app-server`, `-p`
  stream-json), scripted by `FAKE_AGENT` (edits, the reply, a delay, a failure mode, `resume` among them, the fake Codex's `sessions` for the threads a terminal left, `distinct` for a session id
  of its own each, the fake Codex's `config` for what `config/read`
  answers, a log of every argv, request and stdin). Each reads
  Gamma's server from the config or the flags it was given and, scripted
  with `gamma`, calls a tool over HTTP with the token from its environment
  (`gamma_call.py`); the fake Codex's `turns` scripts one chat message's
  turn apart (`$page` in an argument is the page the prompt is about,
  `{gamma}` in its reply what the call read); `fake_agents.serve_mcp` puts this server's `/mcp` on a
  socket of its own for them (`test_local_agents_drivers.py`, the chat's
  turn in `test_local_agents_chat.py`). Tests
  point `GAMMA_AGENT_CODEX` / `GAMMA_AGENT_CLAUDE` at them, set
  `GAMMA_LOCAL_AGENTS=1`, hide the real CLIs (no PATH, no fallbacks) and
  call `runner.reset()` around each test; `tests/conftest.py` pops the four
  variables. No test leaves a process.
- `tests/test_local_agents_catalog.py`: `enabled`, finding each agent,
  the probe and its problems, the cache.
- `tests/test_local_agents_procs.py`: the environment, the Job object or
  process group taking a grandchild down, `run_quick`'s timeout,
  `stop_all`.
- `tests/test_local_agents_drivers.py`: both drivers' events, text and
  flags, a stop ending with `done(stopped=True)` and no process left, a
  crash, garbage lines, silence, the approval answers' shapes, the sandbox
  not ready, an MCP server that starts anyway (also before `thread/start`
  answers), every step one of `events.KINDS`, a turn closed unread, a new
  Codex thread named with its title (not a resumed one, nor one with no
  title; a Codex that refuses it or never answers), Gamma's server under
  a name of its own beside a user's `gamma` turned off, the user's
  excludes kept with the token's variable added once, a Gamma that did
  not start or connect marked `gamma_down`.
- `tests/test_local_agents_runner.py`: the store (the computer's agents,
  their owners, what they may run, the grants by directory, the offered
  directories), the carry-over once, the words of a step (tracked names,
  wrapped commands, redaction, a walk cut short) and what an agent's words
  load.
- `tests/test_local_agents_threads.py`: a ticked comment a turn of its
  thread (the conversation the thread's, about its page, in the synced
  folder; a retried POST once; nobody else reads it), its prompt (the
  comment as the message, the thread as data), its answer under the
  agent's name, the next ticked reply resuming the session, an agent that
  answers itself answered once and the threads it opened listed, the first
  ticked reply in one of those resuming the session that asked, a failed
  and a stopped turn's notes (a reply among people meanwhile keeping the
  turn's chip; the error's record kept on this computer; the timeout), a
  detach stopping the turn, a turn a stopped server left failed once its
  workspace is upgraded, a library page's thread in a directory of its own, `@codex`
  sent for a client that sent none (and where it is plain text) and not for
  an untick, an edit's new `@name`, a co-author's comment on a synced
  folder's file signed with its `agent_owner` (kept when the agent changes
  hands), refused on a library page and once its owner no longer edits,
  each refusal with the comment stored,
  a member named codex told nothing and the sender told of the reply, the
  thread's prompt and the chat's asking line; `tests/test_remote_chat.py`
  the sessions of another computer's agent asked over its channel (its
  owner alone, a directory it does not offer or a path refused there and
  here, one opened bound there and resumed by the next far turn with
  nothing of it in the prompt, busy, offline, an older computer, an answer
  that came late read at the next opening), a comment sent to the computer's agent there run here and answered in
  its thread there, an offline computer refused, and a co-author's turn
  there (named with the shell's word, run here for them asking nothing,
  their name in the prompt, answered in their thread with its owner named,
  read by the owner, failed once they are taken off), an `@codex` made
  there, the thread's pictures fetched while the computer's run holds the
  turn, the source line through the vault's rows, the reply's one id, a
  push skipped while paused and *Stop showing*; a page there in no synced
  folder run here in a directory of its own and in one offered (announced
  by id and name), the directory a turn names checked here
  (`home.far_place`: a vault of this channel alone, never a path), a token
  of that server per far turn kept by its writes and gone at its end (a
  retried claim's replacing the first, a Stop, the computer's token taken
  back), and the agent here commenting with it as *Codex on* the computer
  only where its address is that server's own origin;
  `tests/test_computer_agents.py` the agents a computer takes per account
  and a mention there a turn of its thread the computer takes;
  `tests/test_computer_chats.py` the server's side: any page's seat, a
  turn for a computer away refused, a turn listed to the computer it is
  for alone, the claim's `about` and `dir`, the queued turns failed by an
  announce that no longer takes them.
- `tests/test_local_agents_extras.py`: the probe listing the models, a
  choice one the agent listed, a turn running the model, effort and
  speed asked for (Codex's thread and turn, the speed the turn's alone;
  Claude Code's flags; a config's tier the default speed), both drivers
  streaming a chat turn's text, the reply growing then replaced by the
  whole message, the terminal route for Codex and Claude Code (and the
  strip's `session` agreeing with it), the sources of the selections, the
  Background tasks rows (running and asking, queued, gone after a Stop);
  sessions started elsewhere: Codex's threads of the folder from the
  stand-in's `sessions` store (by name or first message, newest first, a
  subagent's and another directory's left out, the owner alone, a thread
  held by another Codex's lock busy and refused, a lock nobody holds not),
  one opened with its turns (no thinking, its steps worded), bound,
  opened again as the same conversation, refused from another page, its
  next turn resuming the thread with nothing of it in the prompt, and a
  resume Codex refuses as held failing the turn with no thread started;
  Claude Code's files of a temporary `CLAUDE_CONFIG_DIR` (a project
  directory past 260 characters, the ai-title, the branch the last prompt
  left, a fork and a slash command left out, another directory of the same
  slug left out, a live process's session busy, an ended one's not), its
  next turn `--resume`, refused before any process while a terminal has it,
  and a failed resume starting afresh without the session's text; the slug
  rule with its hash (`tests/fake_agents/hold_lock.py` holds a lock as
  Codex does).
- `tests/test_local_agents_asks.py`: asking in the chat: consent held in
  one directory and forgotten with the agent turned off or set back to
  never and with the conversation, both
  drivers' flags and thread parameters with and without `ask`, Codex
  relaying a command (an integer id echoed, decline with a steer, silence
  not idleness) and a file change with its diff, Codex stopped while
  asking (`cancel`), Claude Code's control protocol (Bash asked, a
  protected path and other tools denied unasked, allow with
  `updatedInput`, decline with the note, a Stop's interrupt), in the chat
  a command allowed once (its wait not the turn's), allowed in this chat
  (the same command answered alone, a near-same one asked), an ask
  expired, a Stop while asking and who may answer, an agent that may not
  ask never asking, Claude Code asking a command and nothing
  else, stopped while asking; an ask's command shown as given and its
  paths made short only at a boundary, a grant per command, folder and
  host, Codex's terminal input and a protected change declined unasked,
  asking turned back to never mid-turn, a diff's headers and its cut,
  Claude Code's ask turn starting each Bash call in the folder; what an
  ask shows (`shown_text`'s marks, `host_ok`, `unwrap_shell` on a round
  trip only, `_program` naming its inner command, a card's `marked`,
  `lines`, `inner`), Claude Code's allowed input cut to command,
  description and timeout, Gamma's declines worded as Gamma's, a
  `grantRoot` declined unseen, the 20 asks and three declines of a turn;
  a co-author's ask (`once`, never `chat`, answered by them or the owner,
  a card on this computer for the owner, no grant, the person taken off
  mid-turn stopped, no seat on a page of no synced folder).
- `tests/test_computers.py` (its co-author part): the people an agent
  serves named with the shell's word and its owner, of a workspace the
  owner edits with them, taken off with the owner's session, gone with the
  agent turned off; the announce naming those of that server and workspace
  alone, asked over the channel; the server offering a person it names the
  computer's agent alone and nobody else, no longer once the announce drops
  them, failing their waiting turn.
- `tests/test_remote_asks.py`: asking on this computer, against
  test_remote_chat.py's NAS with a follower of the shell's own: who may
  ask (the agent's `ask`, https, the secret, another agent's turn), an ask
  listed here with Gamma's reading of it and none
  of it there (`waiting`, the step, no host or path in the conversation),
  the chat's routes and a session refused, allowed once by the shell, a
  decline and an expiry, Stop there, the grace for the app's first read
  and nobody reading, plain http and no secret mid-turn, too long, too
  many lines, a cut diff, an odd host and a mark drawn, the caps, an
  answer while that server hangs, a lost write of `waiting` written again,
  the deadline's ten minutes, the read held until a change, no grant, the
  maker alone, Background tasks there, `waiting` false at every end, the
  card's file from this computer's rows, the secret off the environment
  (in a child started with it on Linux and macOS), a refused write not
  tried again; and relayed, the link letting that server answer: the ask
  in that server's message, allowed once there by the person or the
  owner (another account and *Allow in this chat* refused), kept with the
  run, listed and taken here, the bar's card closing, the first answer the
  one either way, an answer waiting out the channel down with no window
  here, a Stop there or the agent turned off ending the ask and an answer
  after it refused, the link off relaying nothing and a co-author then
  asking nothing, a co-author's ask relayed once at most, answered by them
  or the owner, no grant kept.
- `tests/test_local_agents_chat.py`: the chat's computer side: who may (a
  co-author with no agent on, on a file, a library page and the library,
  no such bucket, another server: 404; a token and the guest: 403; one who
  turns an agent of its own on, in conversations of its own), a turn run and its reply kept
  (text, steps, changes), a follow-up resuming the session, Codex not
  resuming and saying so, Claude Code started afresh, a reply that loads
  nothing by itself, Stop of a running, a queued and a slot-waiting turn
  and what waits for one (New chat, open, a second message), a version
  never another conversation's, a turn behind a thread's turn of its
  vault, a pause, the strip
  and the refusals, the agent picked (another agent's read shows no
  conversation, its first turn archives the one before; one not turned on
  is refused), the agent turned off meanwhile, recovery (a turn whose
  vault was removed too), the vault forgotten, the transcript in no
  backup and gone with its page, the prompt (a page, a folder, the
  library; a vault, an offered directory, one of its own; with Gamma's
  tools, those that answer for each, and without them), a turn's token
  (minted before its prompt, which points to the tools by the page's id,
  its thread named with the conversation's title, gone however the turn
  ends, living as long as its deadline with a failed renewal tried a
  minute later), a follow-up whose Gamma's tools did not start failing
  once with its session kept, for both agents, what a message
  keeps; a library page's turn in a directory of its own (made, resumed
  there, its terminal, removed with its conversation and by the sweep),
  an offered directory and another one (a new conversation, a bad id),
  two conversations in one directory waiting for each other, a directory
  no longer offered stopping its turn and failing the one that waits, a
  folder of the work folder in its vault, a library folder's conversation
  filed into the library's History with its folder deleted, a
  conversation exported for its owner alone, History's `active` of the
  agent not picked;
  `tests/test_workspaces.py` a member who goes taking their conversations.
- `tests/test_local_agents_routes.py`: `PATCH /api/local-agents/{agent}`'s
  gate in order (the shell's word, ready, the owner, another account's,
  `ask` and `never`), the offered directories (the shell's word for a
  path, a rename and a removal without it, the refusals), the links and a
  page's standing carrying `agent` without running a process, a turn's
  detail on the links for who may act alone, `GET /api/local-agents`
  (`enabled`, `runs`, `mine`, `dirs`), a pause, a relink and a removal
  stopping the turn.
- `tests/test_migrations.py` (`test_v62_*`): a conversation's
  `thread_id`; (`test_v65_*`): the Sends' tables dropped.
- `tests/test_comments.py` (`agent_kind`, `agent_on`),
  `tests/test_annotations_entries.py` (a computer agent's
  comment's Markdown), `tests/test_agents.py` (who an agent is).
- Frontend: `tests/comments.test.mjs` (a thread's turn's
  chip, the refusal's words), `tests/commentSendTo.test.mjs` (the tick's
  default, its agent, `@name` ticking it, the field's lines),
  `tests/chatAgents.test.mjs` (the agents' list, the picker's rules, the
  agents a comment can be sent to, `farAgents`, whether Settings offers
  Local agents: `showsLocalAgents`), `tests/vaultState.test.mjs` (`computerChip`),
  `tests/mentions.test.mjs` (the agent's row, a member of its name, its
  text and chip), `tests/computerChat.test.mjs` (the side's requests, the
  words of its steps, pill, changes, endings and strip, when a message may
  go, each kind of message in the transcript's shape, History's rows of
  every agent). Desktop:
  `desktop/test/keeping.test.js` (the running row, a problem kept beside a
  run, *Agent working*, the questions and what they came to) and
  `desktop/test/main.test.js` (the page's asks, the questions first, the
  PATCH and the PUT carrying `X-Gamma-Shell`, off and never without it, a
  remote's page refused).
- The browser group `local-agents` (`frontend/tests/e2e/scenarios/localAgents.mjs`,
  `npm run e2e -- --group local-agents`): the stand-ins through
  `GAMMA_AGENT_CODEX` / `GAMMA_AGENT_CLAUDE` and `FAKE_AGENT`; turning
  Codex on, and Settings → AI → Local agents, offered first in the AI
  group on this server, listing it on in a plain browser under "Change
  these in Gamma on …"; `@codex` picked
  from the @ menu in a reply on the PDF ticking *Send to Codex* (the row,
  the tick, the turn's chip *Codex is working* with Stop, the reply under
  Codex's name, the agent's chip, *Open in chat* showing the thread's
  conversation), the next reply ticked by default in the
  thread Codex answered last (*Reply to Codex…*, its button *Send to
  Codex*) resuming its session,
  an edit of an older comment refused with its reason in the status line,
  a resolved thread refused; a ticked comment on a line of a synced
  folder's `.tex` worked as a turn (its edit shown in the code view, Codex's
  question opened on another line, `open.md` naming both threads) and one
  on a library PDF's passage; Codex in the chat
  (picked in the chip, the strip, a message with a PDF passage worked and
  answered in the chat's transcript, the prompt carrying both), a tab closed mid-turn finding the
  turn again in a new one and the follow-up resuming Codex's session, Stop,
  New chat and History; asking turned on for the folder, a command Codex
  asks to run waiting on a card (the strip, the command whole, its reason)
  until allowed once, declined with a note (Codex's steer), allowed in this
  chat and the same command answered unasked in the next turn; the passage
  chip's source line, the strip's menu asking the shell (the asks
  recorded, no shell in the browser suite), a model, effort and speed
  chosen in the chip reaching Codex (a model with no other tier offering
  no speed, the one chosen the chip's glyph and the reply's), the reply seen part-way as it is typed, a
  running turn's Background tasks row and its Stop; with no AI service, a
  library PDF's chat that is Codex's, the setup card picking Codex again
  after Gamma AI, a directory offered picked in the composer's folder chip
  and edited, the reply, its pill *1 step · edited 1 file* and what it changed in the
  transcript, the file named as text, not linked, outside a synced folder,
  the chip then locked; a new chat starting in that folder, and *Add
  folder…* asking the shell and picking the folder it offered, the one used
  listed first; with no AI service, Codex
  reading a library PDF's highlight through Gamma's tools with its turn's
  token (the prompt pointing to `read_page` by the page's id and pasting
  none of its notes, the call answered, the reply quoting the highlight
  and its note, the pill *1 step · read 1 page* and the step *Read a
  page*, the server log saying the token was deleted); History's *Codex
  sessions in …* (this computer unnamed) listing
  the stand-in's Codex threads of the paper's directory, one held by a
  lock another process holds (`hold_lock.py`) disabled and saying why in
  its title, under its title and, pressed, in the status pill, the
  caption's refresh reading them again, a terminal's opened with its
  turns, the next message resuming its
  thread (no thread started, nothing of it in the prompt) and the row then
  *Continued here*; turning it off.
