# An agent on this computer

On the desktop app's own server, the one that keeps synced vaults, Gamma
itself can be the connector that [annotations.md](annotations.md) "The seam"
leaves to the owner. An account turns on Codex CLI or Claude Code for this
computer (Settings → AI, through the desktop shell: "Who turns it on"), and
owns it from then on. A Send of the feedback of a work folder a vault here
keeps, made in Gamma by that account, is taken by this server: it starts
the agent in the vault's directory with the comments as the prompt, shows
what the agent is doing as the request's `running` detail, writes the
agent's answer to each comment as a reply signed `Codex` or `Claude Code`,
resolves the comments it fixed, and sets the request `done` (with a summary
and the source files changed) or `failed`. A withdraw stops the agent. The
owner can also send by naming the agent in a comment (`@codex fix this
only`: "A mention in a comment"), and a follow-up in a thread the agent
answered resumes the agent's session. And the chat window has a second side
where the owner talks to the agent directly ("The chat's computer side").

The design, the survey of both CLIs and the later steps (`@codex` in a
comment, the chat window's agent side, a remote server seeing the
computer) are the research note [agent-collab.md](../research/agent-collab.md);
this is step 1 as built. Backend: the package `gamma/local_agents/` and
`gamma/routers/local_agents.py`, with small parts in
`gamma/annotations/requests.py`, `gamma/comments.py`, `gamma/folder_links.py`,
the folder-links and feedback routers and `gamma/routers/shell.py` (the
shell's word). Step 2.1 of
[agents-as-participants-plan.md](../research/agents-as-participants-plan.md)
made the agents the computer's, where they were each vault's.

| Module | What |
|---|---|
| `__init__.py` | `enabled()`, whether this server runs agents ("Who turns it on") |
| `catalog.py` | the known agents, where each is on this computer, whether it is ready ("Finding the agents") |
| `procs.py` | running a CLI: its environment, no console window, a process tree that dies with the turn or the server ("Processes") |
| `events.py`, `codex.py`, `claude.py` | the drivers: one turn of an agent as a stream of plain events ("The drivers") |
| `tools.py` | Gamma's tools for a turn: this server's `/mcp` as the agent reaches it, the turn's token ("Gamma's tools") |
| `prompt.py` | what the agent is told and the shape of its answer ("The prompt and the answer") |
| `runner.py` | hearing the requests, claiming them and running them ("Claiming and the run") |
| `mention.py` | a comment that names the agent as a Send ("A mention in a comment") |
| `chats.py` | the chat's computer side: a page's conversations with the agent ("The chat's computer side") |
| `store.py` | what this computer keeps, in vaults.db ("What this computer keeps") |
| `info.py` | what a vault says of its agent, for the links and a page's chip ("The routes") |

`__init__.py` imports only `config` and `version`: `folder_links`, the
feedback modules and the routers import the rest inside their functions,
and the runner imports `gamma.annotations.hand` and `folder_links` inside
its own, so no cycle forms at import. `tools.py`, which the session
middleware imports, imports `integrations` inside `mint`.

## The words

**An agent** is the UI's word for whatever writes through an integration
token or runs on this computer for Gamma ([i18n.md](i18n.md) glossary).
**An agent's name** is `codex` or `claude`, its **label** `Codex` or
`Claude Code`. **The computer's agents** are those turned on here, each
**owned** by the account that turned it on (`set_by`): its turns, mentions
and Sends use it, nobody else's. **The agent picked** is the one the chat's
picker shows for a bucket (`chat/agents.js`, "The client"): a chat turn
names it (`agent`) and a conversation keeps the one it began with. **An
account's agent** is the first of its agents in the catalog's order, Codex
then Claude Code (`store.agent_for`): the one a turn that names none runs
with, the one a mention names and its Sends run with, and on a vault the
one the links and a page's chip show (the vault maker's). **A run** is one request claimed by this server and worked by
one turn of one agent. **The maker** is the account that made the vault
(`folder_links.created_by`). The interface says "Codex on this computer"
and calls a work folder a synced folder; the files and every text written
for an agent are English.

## Who turns it on

`local_agents.enabled()` is true when all of these hold:
`config.folder_links_enabled()` (the server keeps folders on disk);
`version.UPDATES != "mas"` (a Mac App Store build may not run code it was
not reviewed with); and either `config.shell_secret()` is set (the desktop
app started this server) or `GAMMA_LOCAL_AGENTS` is on.
`GAMMA_LOCAL_AGENTS` off (`0`, `false`, `no`, `off`) turns it off always
(`config.local_agents_setting()`: True, False, or None for unset). Tests
and the e2e server set `GAMMA_LOCAL_AGENTS=1`. On any other server every
part answers as if no agent were on: the routes below 404, the listener
does nothing, the documents carry no `agent`.

An agent is the computer's, not a vault's: vaults.db `computer_agents`
("What this computer keeps"), turned on, off and let ask through `PATCH
/api/local-agents/{agent}` ("The routes"), which Settings → AI → Agents
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

Where an agent works: in the vault of the work folder the page or the
Send is in, kept here (`folder_links.agent_folder`: a work folder of this
server, or one of another server this computer is shown on), else, for a
chat, in an offered directory or one of its own ("The chat's computer
side"). Until the Feedback dialog goes (step 3.7) a Send, the dialog's or
a mention's, is taken by its requester's agent, the first of its agents
in the catalog's order (`store.agent_for`); a mention names that agent.

An agent turned on hands it the Sends that wait in every work folder kept
here (`runner.queue_waiting`). Turned off, every run and chat turn of it
stops (`runner.stop_agent`), it may run `never` again and the commands
allowed in its chats go; set back to `never`, the asks its turns hold are
declined (`runner.runs_never`). Each change tells the open pages of every
work folder here `{t: "vault"}` and the channels announce it
(`folder_links.agents_changed`). A vault's run stops when the vault is
paused, relinked or removed, and when its push is lowered so it is no work
folder any more (`folder_links._stop_agent`, called from `set_paused`,
`relink`, `delete_link` and `set_push`); a relinked vault forgets the
sessions of its threads and conversations (a session belongs to the
directory it ran in), and an agent stays on through all of these.

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
  "still checking". A listing, a page's chip and the Feedback dialog's poll
  read this.
- **`probes(fresh)`** is both, in order.

## Claiming and the run

`runner.register()`, from `app.create_app` beside
`feedback_listeners.register()`, appends `runner.on_request` to
`requests.change_listeners` (every committed write of a request calls it,
annotations.md "Requests and their states") and registers `runner.shutdown`
at exit.

- **`on_request(ws, request_id)`** is cheap (a few reads, no process),
  never blocks on a run and never raises. A request now `failed` whose run
  is active sets that run's stop (reason `withdrawn` when its detail is
  `withdrawn`); the watchdog stops the turn. A `sent` one is queued when
  `requests._local_link(ws, W)` finds the vault (a work folder of this
  computer, not paused, not choosing its files), the requester has an
  agent on here (`store.agent_for`) and the Send was made in Gamma (`via`
  `session`, annotations.md "Requests and their states"; never a token's, nor
  one from before Sends said how they were made: `_ours`). Each request is
  queued once; one already queued or running is left as it is, but one
  whose run was stopped while it waited for a slot (its vault paused and
  resumed) is queued again for the vault as it is now, since that run drops
  it unclaimed.
- **The queue.** One worker thread a vault takes its queue in order, one
  run at a time, so a folder never has two agents in its directory; at
  most `MAX_RUNS` (2) runs at once on this server (a semaphore). A worker
  forgets itself in the same locked step that finds its queue empty. The
  module lock guards the queues, the workers, the active runs and their
  stop events, and nothing else: no call into `requests`, `store`,
  `catalog` or a driver is made while it is held, since the runner's own
  writes come back to `on_request` on the same thread through `_changed`.
- **`waiting(ws, link)`**: the Sends of the vault's work folder that wait
  (`sent`) and are an agent's to take, made in Gamma by an account with an
  agent on here, oldest first. **`queue_folder(ws, link_id)`** hands them
  over through the same check → how many were taken. It runs for every
  work folder kept here when an agent is turned on (`queue_waiting`), after
  a resume, after the files are chosen, and at startup.

A run (`runner._Run.go`) never raises and always ends the run it recorded:

1. **The claim.** A run stopped while it waited for a slot claims nothing:
   the Send stays `sent` for the run queued after it. The request is read
   again: still `sent`, its vault still `_local_link`'s and the queued one,
   the Send still for an agent here (`_ours`), its requester's agent
   (`store.agent_for`) the run's, and the requester still at least an
   editor of the workspace. Then `requests.claim(ws, conn, id, by_name=<label>,
   detail="Starting Codex")`: `sent` to `running` in one step under the
   write lock, so of two takers, or a taker and a withdraw, one wins.
   None (taken, withdrawn) drops the run silently. Once claimed,
   `runner.running(ws, id)` is true and the run is recorded
   (`store.start_run`).
2. **The agent, asked afresh** (`catalog.probe(agent, fresh=True)`): one not
   ready fails the request with its problem. A read-only hosted server
   (`hosted.read_only`) or a workspace that refuses writes
   (`workspaces.refuse_writes`) fails it with that reason.
3. **What there is to answer**: the run notes when it reads (`seen_at`)
   and, once read, which comments ("A mention while a run works" below):
   `requests.entries_of` with the paths from
   the vault's rows (`entries.paths_of(vault.store.files(link_id))`, as the
   files' writer names them), then `prompt.pending`. None pending → `done`
   with "Nothing new since the last answers."; more than `MAX_THREADS`
   (100) → `failed` "Codex takes at most 100 comments at a time: send a
   file or a directory of this work folder." Else the detail reads
   `Reading 1 comment` / `Reading 3 comments`. The files members may see
   named are noted for the words of the request (`tracked`): those the
   vault tracks, and the source files the entries name (their SyncTeX
   lines, which members read in the entries already).
4. **The turn.** A snapshot of the folder (`_snapshot`: `{path: (size,
   mtime_ns)}`, leaving out `.git`, `.gamma`, `node_modules`, `__pycache__`,
   `.venv`, `venv` and every directory whose name starts with a dot; at
   most 20,000 files and 3 s, and whether the walk got through). A stop set
   meanwhile ends the run before any process starts. Then
   `events.start(agent, argv=catalog.command(agent),
   cwd=<the vault's directory>, prompt=prompt.build(...), schema=
   prompt.ANSWER_SCHEMA, images=<the pictures' absolute paths>,
   session=<the one to resume, or "">)`, its pid recorded. The session is
   the one this agent answered every pending thread in last, in the
   vault's directory as it is now, when they share one (`_session`, from
   `agent_threads`: "Resuming the session" below); a resumed turn that
   ends badly before any step, not stopped and not past its deadline, is
   started once more afresh (Claude Code has no fallback of its own; Codex
   falls back to a new thread itself), unless Gamma's tools did not start
   for it (the done's `gamma_down`, `events.GammaDown`: afresh would fail
   alike, and replace the session; `_Turning._resume_refused`). A watchdog thread, one a turn,
   waits for the run's stop or the deadline,
   `RUN_TIMEOUT_S` (1800 s, `GAMMA_AGENT_RUN_TIMEOUT`) counted from the
   turn's start, not from the queue's wait, and calls `turn.stop()`.
5. **Progress.** Each step the agent starts becomes the request's detail,
   at most every `DETAIL_EVERY_S` (3 s), only when the text changes and the
   run is not stopping: `Reading main.tex`, `Editing sections/intro.tex`,
   `Running latexmk` (the program's name alone, never its arguments; a
   command a CLI wraps in a shell, as Codex on Windows runs each through
   `powershell.exe -Command`, names the program inside it), `Searching the
   sources`, `Working`. A file is named only when it is `tracked`; any
   other reads "a file in the folder", and a path outside the folder "a
   file outside the folder", so a file the person left out of Gamma is
   never named to the workspace. A detail refused (409: the request was
   withdrawn) stops the turn. The run keeps its steps for this computer
   (`{kind, title, status}`, every file inside the folder named, at most
   `MAX_STEPS`, 200).
6. **What changed**: a second snapshot against the first, added, changed
   and deleted files by path; when either walk was cut short, only the
   files both reached are compared, so a file past the cut never reads as
   changed. The build's outputs (`.pdf .aux .log
   .synctex.gz .synctex .fls .fdb_latexmk .out .toc .bbl .blg .bcf .run.xml
   .xdv .dvi .nav .snm .lof .lot .idx .ind .ilg .bak`) are told apart from
   the sources: `changes` is the sources (at most 200), `built` whether an
   output changed. The disk says what changed, not the events: Codex on
   Windows edits through shell commands and reports no patch.
7. **A stop.** A request withdrawn meanwhile (read again, or the stop set by
   a withdraw) gets nothing more written in Gamma; the run ends `stopped`.
   Stopped for another reason, the request fails with what the vault is
   now, or as the stop said it (`stop_link`'s `why`: a vault being removed,
   one its other server detached): "Stopped: the agent was turned off on
   this computer.", "Stopped: the synced folder was paused.", "Stopped: the
   synced folder is no longer kept on this computer.", "Stopped: the synced
   folder was detached.", "Stopped: the synced folder changed on this
   computer." or "Stopped: Gamma is closing."
8. **The answers.** The turn ended well and `prompt.answers` keeps some:
   each is written by `gamma.annotations.hand.reply` as the requester
   (`hand.Writer(<maker>, <maker's username>, agents.here(name))`),
   signed as this computer's agent: the reply is the
   maker's, its `agent` is the label (Codex) and its `agent_id`
   `computer:<this computer>:codex` ([comments.md](comments.md) "Who wrote
   a comment"), so it reads *Codex on TIM-PC, for tim*, and the thread's row
   in `agent_threads` records the reply, the turn's session and the
   directory; a `fixed` one is resolved too (`agent.resolve`), unless a
   person wrote or edited a comment in it after `seen_at` (a word the agent
   never saw: the thread stays open for the next run). Both writes pass
   `seen=seen_at`, so they answer only the Sends made by then
   (`requests.answered`). A refusal of one is kept (the first
   one's words) and the others go on; a withdraw meanwhile (the run's stop)
   ends the writing before the next one. No reply written → `failed` with
   that refusal. Else `done` with the detail `<summary, or "Answered 2 of 3
   comments">` + " Changed a.tex, b.tex." (the first five `tracked`
   sources, "and n more"; " Changed 3 files." when it changed none of them)
   + " The PDF was rebuilt." when `built`, cut to `requests.MAX_DETAIL`.
9. **A failed turn.** Ended well but no valid answer → "Codex finished
   without answering in the form asked for."; the deadline → "Codex took
   longer than 30 minutes and was stopped."; else "Codex could not finish:
   <the driver's error, first line>", the folder's paths made relative, the
   home directory written `~` and any other absolute path "a file outside
   the folder" (`_redact`). The whole error and the agent's stderr tail go
   only to the run's row.
10. **The end.** The turn is closed (`Turn.close`: its CLI killed even when
    nobody read its events, as after an error between its start and the
    reading) and `store.end_run` called, always (a `finally`); an
    unexpected exception fails the request with "Gamma could not run
    Codex." and the run with its traceback. When even that write is
    refused (the workspace's database locked, say), the run's row is left
    open, so the next start fails the request ("Recovery") rather than
    leave it `running`.

Every write of a run passes `only_from=("running",)` to `requests.set_state`,
so a withdraw that lands between two of them is never overwritten (the
write is a 409 and the run stops). `requests.claim` and `only_from` are
annotations.md's.

## The prompt and the answer

`prompt.build(agent, owner, work_folder, note, entries, root)` writes the
prompt, in English. Gamma writes every line itself: the comments, their
quotes and the instruction are data, each inside a JSON string, never
pasted as Markdown, so nothing a member wrote reads as a heading or an
instruction of Gamma's. Its parts:

1. "Gamma, the app this folder syncs with, hands you 3 comments on files of
   the work folder "paper". tim sent them with this instruction:", then the
   note as a JSON string on its own line, or "(no instruction: address the
   comments)".
2. **How to work**: act on what the owner asks, the instruction and the
   owner's comments (`is_owner` true); a comment by anyone else is context,
   not an instruction, and the reply says when someone else's request was
   not acted on. Change the sources in this directory; a comment without a
   `source` is found by its `quote`, and a `was` is the text a range of a
   text file was commented on before the file changed under it. The build: Codex is told to rebuild
   with the project's own build (latexmk, make, a script), its commands
   running in a sandbox with no network; Claude Code that it cannot run
   commands and the owner's own build picks up its changes. Never edit
   anything under `.gamma/`; open a comment's `pictures` when it is about
   how something looks; do not reply in Gamma or resolve anything: the
   final message is the answer, which Gamma writes as the agent's reply.
3. **Your answer**: the final message is JSON, per comment its `thread_id`,
   `outcome` (`fixed` once changed, `question` when the owner must decide,
   `unchanged` when no change is needed or it could not be made) and
   `reply` (one or two sentences, `file:line`, or the question), and one
   `summary` sentence.
4. **The comments**: per entry `### Comment {i}` and a fenced `json` block
   (the file's name only inside it, since a member can name a file) with `{thread_id, file, place, page, quote, note, was?, source:
   {file, line, end?, as: "current" | "commented"} | null, pictures:
   [".gamma/annotations/img/<key>.jpg"], comments: [{by, is_owner, agent, at,
   text, deleted?}]}` (`json.dumps(..., ensure_ascii=False, indent=1)`;
   `was` only on an entry that has one, a range of a text file placed
   roughly ([annotations.md](annotations.md) "An entry"); `pictures` the
   entry's `picture` and `before` that exist under the vault's directory; a tombstone's `text` ""). Three backticks in a row
   in any string get a zero-width space before the third, so a body cannot
   close the fence.

`prompt.pending(entries, seen=None)`: the entries that are open and whose
newest comment is not an agent's (nothing new from a person since an
agent answered; a request's `answered` flags are not asked), unless that
comment is a reply of this computer's agent written while a person spoke
in the thread unread (`seen`: `{thread id: that reply's id}`, which the
runner finds through `agent_threads` and `_spoken_after`: "A mention while
a run works" below). The same full prompt goes to a resumed session: the
agent reads the whole thread again either way.
`prompt.ANSWER_SCHEMA` is the strict JSON schema of the answer (`answers`
of `{thread_id, outcome, reply}` with `additionalProperties: false`, and
`summary`), passed to Codex as `outputSchema` and to Claude Code as
`--json-schema`. `prompt.answers(answer, thread_ids)` → `(valid,
summary)`: the first item per thread among `thread_ids` with a known
outcome and a non-empty reply (cut to `comments.MAX_BODY`), the summary on
one line cut to 300 characters; anything malformed is `([], "")`.

## A mention in a comment

An agent's owner can also send by writing the agent's name in a comment:
`@codex fix this only` in a thread on a passage, an area, a region, ink
or a note is a Send of that thread, the comment's other words its
instruction; written in the page's own thread (`block_id` the page's), it
is a Send of every open thread of the file. The request, its states, its
chip and its answers are the ones above. `gamma/local_agents/mention.py`
holds the rule; the names are not reserved, so everywhere else `@codex`
is what it was, plain text or a member named codex.

- **The rule** (`agent_named(ws, event, via=)` → `codex`, `claude` or
  `""`), asked once per write by the comment routes, before
  `comments.written`, cheapest test first: `enabled()`; a create, reply
  or edit; a person's own write (the event has neither `agent` nor
  `agent_id`, so a token's comment and the agent's own replies never
  count) not made through a share (`via`); the names it newly mentions
  (`mentions.people_in`, for an edit less those of its text before, the
  Inbox's rule) among the catalog's, exact lowercase (`@Codex` is not
  the agent), and none read from the database before one is; then the
  page a live file of a work folder a vault here keeps, the name the
  writer's agent (`runner.folder_agent(ws, w, user_id)`: the first of its
  agents on, `store.agent_for`, the one a mention can name until a
  comment sent to an agent is a turn of its own, step 3.5 of the plan;
  the field's @ menu lists that one alone), and the writer its owner. The route puts the name on the event
  (`agent_named`), so the Inbox reads the same answer: a member named
  codex is told nothing of it, nor of the agent's later replies in a
  thread where its owner named it (`inbox._agent_of`, by the agents as
  they are now).
- **The Send** (`send(ws, event, name, by_name=)`, after `written`; never
  raises): refused, with a code the client words and an English `why`,
  when the thread is resolved (`closed`: it is reopened first), when an
  edit is not of the thread's newest comment (`not_newest`, since the
  agent would find nothing new in it), when the writer is no owner or
  editor of the workspace now (`viewer`), when the server or the
  workspace takes no writes (`writes`), and for a file-wide mention with
  more than `MAX_THREADS` open threads (`too_many`) or none (`nothing`);
  a AnnotationError of the create is `other`. Else `requests.create` with
  the file's scope (`entries.resolve_scope`), `threads=[the thread]` or
  none for the file's, `note` the body with the agent's mentions taken
  out of its prose (`mentions.without`) and cut to `requests.MAX_NOTE`,
  `via` `session`, and the comment's id as the request's (a retried POST
  writes no comment and so no second Send; an edit's Send gets a new
  id). The runner takes it as any Send of the agent's owner (`_ours`). With
  the vault paused or the agent not ready, it waits or fails as a Send
  does.
- **The answer.** `POST /api/pages/{id}/comments` answers `{comment,
  thread, send?}` and `PATCH /api/comments/{id}` `{comment, thread?,
  send?}`: `send` is `{agent, request_id, scope: "thread" | "file",
  threads}` or `{agent, refused, why}`, absent when no agent was named;
  with it, `thread` is read again after the Send, so its `sent` is the new
  request (the thread the writer built under its lock has none, and the
  client replaces a thread whole). The comment is stored either way.

### A mention while a run works

One run at a time a folder, so a mention made in a thread a run is
working on waits behind it. These rules keep it from being lost:

- The run notes when it reads its entries (`seen_at`) and the ids of the
  comments it read, by thread (`read`). Its replies and resolves pass
  `seen_at` on (`annotations.hand.reply` and `resolve` take `seen=`, never
  from a tool's arguments, and put it on the event): `requests.answered`
  with an event's `seen` answers only the requests made by then. Without
  it (a token's write) a reply answers every waiting request made before
  it, as before.
- A word the run never saw is a person's live comment in the thread (a
  session's: no agent's, no token's) whose id the run did not read, or
  one it read that was edited after `seen_at` (`runner._spoken_after`; by
  the ids, since a comment is stamped before its commit). A thread the
  agent marked `fixed` is resolved only when it has no such word.
- The next run puts the thread to the agent although its newest comment
  is the agent's reply, when that reply is this computer's recorded one
  (`agent_threads.comment_id`) and the thread has such a word for that
  run (`agent_threads.read_ids`, `prompt.pending`'s `seen`).
- The runner does not ask a request's `answered` flags: another member's
  reply sets them ("Answered" in annotations.md), and it answers nothing the
  agent was asked. What is new is the newest comment's rule above.

A mention made while an earlier Send of the same thread still waits is
read by that Send's run, which then answers both: the mention's own
request ends "Nothing new since the last answers."

### Resuming the session

Each thread a run answers gets a row in vaults.db's `agent_threads`: the
agent, its turn's session (Codex's thread, Claude Code's session), the
directory it ran in, `seen_at` and the comments it read, the reply's id
and the request. A later
run whose pending threads all have a row of the same agent, run in the
vault's directory as it is now, sharing one session, resumes it (Codex
`thread/resume`, Claude Code `--resume`), so a follow-up in a thread
(`@codex no, keep the British spelling`) is answered by an agent that
remembers what it did; any other run starts a new session. A session the
agent no longer has is started afresh once (step 4 of the run). The rows
of a vault go when it is removed (`vault.store.forget_link`), relinked
(`store.forget_threads`) or found gone (`store.forget_links`).

## A work folder of another server

A work folder of another Gamma server (a NAS: [work_folders.md](work_folders.md)
"Work folders of another server") kept here has the computer's agent too,
once this computer is shown on that server ([computers.md](computers.md)):
the first agent on of the account the channel belongs to (the vault's
maker, `channel.agent_folders`); from then on that account's Sends and
`@codex` mentions **made on the NAS, in any window**, run here, and the
answers go back there. This is step 4c of the research note; the chat's
computer side stays local (4d).

- **Turning it on** is the computer's (`PATCH /api/local-agents/{agent}`,
  "Who turns it on"); nothing is set per folder. The shell's question
  before an agent is turned on says what a server this computer is shown
  on can then do with it; showing this computer on a server asks nothing
  of its own. The first token minted for an origin over plain `http://`
  is asked about already (the shell's `beforeMint`, once per origin of the
  local network, each time for a public host an existing link keeps; a
  new link to a public host over plain http is refused,
  [https.md](https.md#plain-http)). A turn there asks on this computer
  while its agent may ask, over https alone ("Asking on this computer").
- **The binding.** The channel's announce names the computer's agents
  (`assistants`, each with `enabled`, on for the channel's account, and
  `runs`), never a vault's; the NAS keeps them with the computer's row,
  so it knows the folders it keeps have an agent while the computer is
  away: the first assistant on, for a folder that carries a work folder's
  mark itself and an account that may edit there, and **per account**
  (`registry.agent_of(ws, folder, user_id)`): one member's computer
  announcing another's work folder never changes that member's binding,
  and only ever serves its own account's Sends. Until step 4 of the plan
  it answers only for a folder that computer keeps.
- **Who decides, who runs.** The NAS decides "ours": only a Send made in
  Gamma (`via: session`) by the computer token's account, in a work
  folder that computer announced with an agent, reaches it; a token of
  that account's Send there is refused (403 "Codex on TIM-PC takes Sends
  made in Gamma, not with a token."), and anyone else's mention is plain
  text. The NAS
  wakes the computer's held poll (`{wake: ["sends"]}`) when such a Send
  lands or is withdrawn; a wake while no poll is held answers the next
  poll at once.
- **Hearing them.** The channel thread asks `GET /api/computers/sends`
  after each announce, on a wake, when the agent is turned on, the vault
  resumes or its files are chosen, and at least every 180 s, while a
  vault of that server has an agent; each new Send is queued for its link
  under `(link_id, "send", request_id)`, so a remote run never reads as a
  local workspace's request, and the runs listed `withdrawn` stop.
- **The run, through a seam** (`local_agents/home.py`): `home_for(ws, link_id)`
  gives a local run `LocalHome`, today's calls unchanged, and a remote
  one `HttpHome`, which speaks the channel's routes with the link's
  computer token: the claim (`sent → running`, one locked write on the
  NAS keyed by the run id minted here, so the same run claiming again
  holds it and another is refused), the entries (each with its PDF
  place's page, box, rects and document), the server's `seen`, the
  threads' last speakers for the resume rule, the progress lines, the
  region pictures (fetched, at most 16 MiB each, into the directory's
  `.gamma/annotations/img/` as locally, which the work folder's rounds
  prune as they prune a local one's, reading that server's annotations:
  [annotations.md](annotations.md) "The files"), and the end: the replies (each with
  an id derived from the run and its thread, so a retry writes nothing
  twice), the summary and the changed files the vault tracks (relative),
  in one `answers` call. The NAS writes the replies itself, signed as
  this computer's agent there (`agents.of_channel`: `computer:<this
  computer's id for the NAS>:<agent>`, named with the computer the
  channel announced; `annotations.hand.answer_run`,
  with the `seen` it stamped at the claim), resolves a thread only when no
  person wrote in it after the run read it, and ends the request (only
  from that run while it holds it, so a withdraw is never overwritten).
  The claim and the end are retried for up to 180 s while the NAS cannot
  be reached; a progress line that cannot be delivered is dropped.
- **Source lines** are computed here (`feedback/sources.source_in_vault`):
  the page's one PDF row in the vault's files, its hash the place's
  document and its size and time unchanged, and the sources on disk, as a
  local run's.
- **After the run** a push round starts at once when files changed
  (remote vaults get no fast rounds), so the NAS sees the rebuilt PDF;
  none while the vault is paused, choosing its files or gone (the next
  round after resume pushes).
- **Recovery.** An open remote run is failed through its home ("Gamma
  stopped before Codex finished.") at start and again whenever that
  channel's announce succeeds, so one left open while the NAS was away is
  failed once it is back; a claim whose answer never came is recorded
  open for that. A computer's token that goes (revoked, replaced, expired)
  fails on the NAS the Sends its runs held ("The computer's channel was
  taken back before Codex finished."); *Stop showing* leaves the
  computer's agents on, and that server, told nothing more of this
  computer, gives it no work. A request whose computer never comes back
  stays running there until its maker withdraws it.
- **What the NAS keeps** of a run: a pages.db `feedback_request_runs` row
  per claim (the request, the holding token, the run, the agent, the
  `seen` time, the comment ids the run read, its end), additive, so the
  claim's key and what the run read survive a restart; the sweep removes
  rows whose request is gone.

### The chat on another server's page

On a page of such a work folder the chat's computer side works too, in
any window of the maker on the NAS (step 4d): the composer's chip lists
*Codex on TIM-PC* beside Gamma AI, and a message is a turn that runs on the computer,
its reply, steps and changed files coming back into the conversation,
which the NAS keeps (pages.db `computer_chats`, the maker's alone), so it
reads while the computer is off.

- **The seat.** The chat routes (`routers/local_agents.py`, registered on
  every server) take the host's own vault first, where this server runs
  agents, else the binding of the page's work folder for the session's
  account (`chats.remote_of` → `registry.agent_of`); neither: 404, a token
  or the guest 403, as locally.
- **The strip** (`GET /api/local-agents/chat`, `computer.remote: true`)
  names the computer and its presence (`computer`, `online`, `since`),
  offers the models the computer announced with its assistants (`models`,
  at most 32), and says why an online computer's agent is not ready. The
  composer stays open while the computer is off: a message waits
  (*Waiting for TIM-PC*) and runs when it connects; the window reads the
  conversation every 10 s while it waits.
- **A turn.** `POST /chat/turns` checks the folder is not paused there,
  the agent ready while the computer is online, the model and effort
  among the announced ones, then writes the person's message and the
  queued agent message (`chats.begin(remote=True)`) with a pages.db
  `computer_chat_runs` row (the turn, its conversation and its bucket, a page,
  the account, the turn's agent and the computer last known to take it,
  the holder and run once claimed, a `seq`, a `stop` flag, its state, when
  queued and started), and wakes the computer (`{wake: ["chat"]}`). A
  computer is offered only the turns of its own agent for that folder; a
  claim for another agent leaves the turn queued.
- **On the computer** the channel thread asks `GET /api/computers/chats`
  as it asks for Sends (after an announce, on a wake, at least every
  180 s while a vault there has an agent), queues each turn for its link
  under `(link_id, "chat", turn_id)`, claims it for a run it names (the
  message, the selections with their places, the last 6 messages, the
  model and effort, the page's path), computes the selections' source
  lines from its disk, resumes the conversation's session from its own
  vaults.db as locally, runs the agent confined as a Send (asking only
  while its agent may ask, on this computer: "Asking on this computer"),
  and writes the agent message back at most every second,
  each write with the next `seq` (an older one changes nothing: `stale`),
  the last always sent, retried while the NAS cannot be reached, a lost
  one by the next flush (`home.missed`; one refused there waits for the
  next change); a 409 stops the turn.
- **Stop**: a queued turn ends at once; a running one is flagged and its
  computer woken (it stops it and writes its end, or, for a turn it no
  longer runs, ends it there under its run); a running one whose computer
  is offline, or a second Stop, ends it at once, its run's next write
  refused. Stop is the account's on its own turn's row, with no seat
  needed, so a turn stays stoppable after the agent is turned off, the
  token goes or the page is deleted.
- **Not on another server's page**: answering what the agent asks (409
  "This is answered on the computer that runs the agent.": the page says
  *Waiting for your answer on TIM-PC* while the message's `waiting` is
  true, and draws no card), *Continue in a terminal* and *Show in folder*
  (the strip's ⋯ menu is hidden; `/chat/terminal` 404), and the passage
  chips' source lines (`/chat/sources` answers nulls; the turn's prompt
  still gets them, computed on the computer).
- **Background tasks** lists the account's queued and running remote
  turns from `computer_chat_runs`, named with the computer that holds or
  keeps them (*Codex on TIM-PC: main*), in the phase `asking` while the
  message says `waiting`, Stop the same route.
- **Recovery** as for Sends: an open remote turn is failed through its
  home at start and whenever its channel's announce succeeds again; a
  computer's token taken back fails the turns its runs hold and the
  queued ones no other computer of the account would take; an announce
  that drops or changes a folder's agent fails that folder's queued turns
  ("Codex was turned off on the computer that keeps this folder.", or
  "Codex is no longer this folder's agent." when another is on), and so
  does the computer when one waiting there finds its agent off ("Codex
  was turned off on this computer."), so none runs later unasked. The watcher of a remote turn never waits on a network write
  (a writer thread flushes), so Stop and the deadline stay prompt.

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
server's page"), until step 4 of the plan links computers in Settings.

The chat draws such a conversation wherever the composer's chip picks a
computer's agent ("The client"): on a page, a folder or the library, in
its own transcript and composer, as it draws Gamma AI's, under a strip
saying where the agent works (the synced folder's name, or while the
conversation has no turn a menu of the offered directories and *No
folder*). Only they see their conversations; for anyone
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
  (`db.COMPUTER_CHATS_SCHEMA`; step 59 gave it `bucket` and `dir`, [migrations.md](migrations.md)),
  read and written by `gamma/local_agents/chats.py` only. One row a
  conversation: `id`, `bucket` (a page's id, a folder's or `home`, as
  `chats.bucket`), `dir` (where its agent works: the work folder's id, an
  offered directory's id, or `""` for one of its own), `user_id` (the
  account that talks to the agent, its one reader), `agent` (the agent it
  began with), `title` (its first message's first line, 80 characters),
  `messages` (JSON), `version`, `created_at`, `updated_at`, `archived_at`
  (null for the bucket's active conversation of that account; set:
  History). It is in `ws_backup.PRIVATE_TABLES` (a backup carries none, a
  restore keeps the live rows), goes with its page deleted for good
  (`ops._purge`), is filed into the library's History with its folder
  deleted (`chats.file_into_home`, in the tree delete's transaction, as
  the AI chats' `routers.chats.file_into_home`), and an account's rows go
  when its membership ends or the account is deleted
  (`db.delete_workspace_prefs`); nothing else reads it. Its paths are
  relative to the directory.
- **Messages.** The person's: `{id, role: "user", text, selections:
  [{text, page, box}], at}` (at most 20 selections, 2,000 characters a
  quote; the box kept, since the source line is looked up when the turn
  starts). The agent's: `{id, role: "agent", agent, label, turn_id, state:
  queued | running | done | failed | stopped, text, steps: [{id, kind:
  read | edit | run | search | other, status, path? | program?}], asks,
  changes, changed, built, error, fresh, model, effort, at, ended_at}`:
  `asks` what waits for the person ("Asking in the chat"), `model` and
  `effort` what the turn asked for (null for the agent's default),
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
  "chat", turn id)`, beside the Sends' `(ws, "send", request id)`: the
  queues are kept by directory (`runner._place`, the path as the file
  system compares it), so a turn waits behind a Send of its vault, two
  conversations in one directory wait for each other, and all share the
  `MAX_RUNS` slots: one directory never has two agents. A turn belongs to
  the computer: no connection holds it, so closing the tab or the window
  stops nothing.
- **`_ChatTurn`** (sharing `_Turning`, the turn plumbing `_Run` uses: the
  snapshot, `events.start`, the watchdog, following the events, the
  changed files):
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
     where it works and how: in a vault, as for a Send (change the sources
     here; Codex may rebuild in its sandbox, Claude Code runs no commands;
     never `.gamma/`); in an offered directory, by its name, with the
     build's line; in one of its own, that it holds none of the owner's
     files; and always plain prose, `file:line`, ask what is the owner's to
     decide. No answer schema. The turn takes Gamma's tools before its
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
     afresh (not one whose Gamma's tools did not start: it fails once, and
     the conversation keeps its session).
  5. The events become the message, written at most every
     `CHAT_EVERY_S` (0.5 s; the watchdog writes the last one when nothing
     follows) and at the end: `session` kept in `agent_conversations`,
     `text` appended as a paragraph (`done.text`, which repeats the last
     text, only when no text came), each `tool` a step or its status, and
     at the end the changed files from the snapshots, `built`, the state
     and its words. The whole error and stderr tail stay in
     `agent_turns.error`.
- **Stop** (`runner.stop_chat`): a running turn's stop is set (it ends
  `stopped`), a queued one leaves its queue, one its worker holds while it
  waits for a slot ends `stopped` at once (given its slot, it finds its
  message ended and writes nothing more), and one whose message still
  reads queued or running though the runner does not hold it is ended so;
  the conversation takes the next message at once.
  A pause, relink, removal, the agent turned off, its directory no longer
  offered (`runner.stop_dirs`, from `PUT /api/local-agents/dirs`: "Stopped:
  the directory is no longer offered on this computer."), Gamma closing and
  the deadline stop it too, as they stop a Send (it then fails with why).
  `runner.chat_state(ws, turn_id)` → `{turn_id, state: queued | running,
  ahead}` for the strip (`ahead`: another run in its directory, or the
  slots, go first).
- **The strip** (`GET /api/local-agents/chat`'s `computer`) names where
  the agent works: `dir` is `{kind: "vault", name, path}` (the path for the
  vault's maker alone), `{kind: "offered", id, name}`, or null for a
  directory of its own; `folder`, `folder_id` and `paused` are the work
  folder's, empty outside one.
- **The vault working.** `runner.at_work(link_ids)` names the vaults whose
  agent works now, a Send or a chat turn, so the vault chip, the links
  and the sync panel see the vault busy: `agent.running` is `{kind:
  "send", request_id, detail}` or `{kind: "chat", request_id: null,
  detail}` (the turn's step in a member's words, `_said`).
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

- **The model and effort** the agent offers are read with the probe, with
  no model use (`catalog.probe(models=True)`, kept with it; not re-read at
  a turn's start, which asks only whether the agent is ready, so a read
  lists them again once that listing is 10 minutes old, or when there was
  none: `catalog._listed`): Codex's `model/list` on a short-lived
  app-server (every page, hidden models left out, 15 s at most; it does
  the Windows sandbox check in the same process), Claude Code's
  `initialize` on a
  `claude -p --input-format stream-json … --restricted --strict-mcp-config`
  closed before any message. Shape: `[{id, label, efforts, effort (the
  default), default}]` in `GET /api/local-agents` and the chat's
  `computer.models`; none listed leaves the agent ready with `models: []`.
  `POST /chat/turns` takes `model` and `effort`, each one the agent listed
  (400 otherwise; an effort checked against the chosen model, or the
  default one); Codex gets them on `thread/start` / `thread/resume`
  (`model`) and `turn/start` (`effort`), Claude Code as `--model` (none
  for its `default` entry) and `--effort`, which `--restricted` would not
  read from the person's settings. The agent's message records them. The
  composer's chip (`chat/AgentChip.jsx`, Gamma AI's) lists the agent's
  models, and its efforts as a section (`useComputerSide`'s `chip`); the
  choice is the conversation's.
- **The reply as it is typed.** A chat turn's driver streams
  (`stream=True`; a Send's never does): Codex keeps
  `item/agentMessage/delta` (opted out for Sends), Claude Code runs with
  `--include-partial-messages` and reads `stream_event` `text_delta`s of
  the main thread. Both yield `{type: "delta", text}`; the runner grows the
  message's live paragraph, written at most every `CHAT_EVERY_S`, and the
  whole `text` event that follows replaces it (no text twice).
- **Continue in a terminal and Show in folder** are the strip's `⋯` menu.
  Show in folder asks the shell `reveal-on-disk` as the vault chip does.
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
- **Background tasks** lists, for the account, one read-only row per chat
  turn queued or running here (`routers/jobs.py`: `{id: "computer-<turn>",
  kind: "computer-turn", title, params: {bucket, turn, agent, label, title
  (the page's or the folder's)}, state: queued | running, progress: {phase: "asking"} while
  it asks, stoppable, stopping, readonly}`); the tray words it "Codex on
  this computer: <page>", "Waiting for you" while it asks, opens the
  bucket's chat (a page's, or a folder's or the library's in the library's
  view) on a click with the turn's agent picked (`chatSide.js`
  `pickChatAgent`; a turn of another workspace opens only its page there:
  the app loads that workspace anew), and its Stop
  calls the turn's stop route (in the turn's workspace) instead of
  `cancelJob`. A Send this computer's agent answers is a row too, for the
  work folder's maker (`{id: "feedback-<request>", kind: "feedback-run",
  params: {request, label, name, scope: {kind, id}, comments}, progress:
  {item: <the request's detail>}}`, runner `send_runs`): a click opens the
  Feedback dialog on the Send's file or folder, and Stop, on the maker's
  own Send, withdraws it.

Checked against the real CLIs (Codex 0.167.0, Claude Code 2.1.318): both
list their models with no model use, stream a reply in deltas, and take a
chosen model and effort.

## Asking in the chat

An agent of a chat turn may ask the person to let it do what its
confinement keeps from it: Codex to step outside its sandbox (a command
with the network, a write outside the folder), Claude Code to run a command
at all (the build). The person answers on a card in the chat. A Send never
asks (nobody watches it) and keeps refusing; an agent its owner has not
let ask behaves as without it.

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

A chat turn of a page of another server's work folder ("The chat on
another server's page") may ask too, as a local turn does, but the card is
drawn and answered on the computer itself, in the desktop app's bar, with
the server's page hidden while it is up
([desktop architecture](../../desktop/docs/architecture.md)). That server
starts the turn and learns that it waits, never what it asks: nothing it
sends draws, words or answers the card. Step 5b of
[agent-collab](../research/agent-collab.md).

- **What it may run** is the turn's agent's, the computer's (`runs`
  `ask`, "Asking in the chat"): one setting lets it ask in the chat here
  and on this computer for a turn there; nothing is set per server or per
  folder (a per-server switch is step 4.1 of
  [the plan](../research/agents-as-participants-plan.md)).
- **Who may ask** (`runner.ask_refusal`, at the turn's start and at every
  ask): the agent's `ask`, the shell's secret set (`NOBODY_HERE` without:
  no window to ask in), and that server reached securely now
  (`NOT_SECURE`: `remote_servers.plain` must be "", https or this
  computer, [https.md](https.md)). A turn that may not starts its agent
  without `ask`.
- **Each ask**, in `_ChatTurn._asked`'s order, is declined unseen ("Gamma
  declined that: …") for: its agent or the address no longer allowing it;
  a kind other than a command or a file change; a `grantRoot` (every
  turn); a command over `FAR_MAX_COMMAND` (8000 characters) or
  `FAR_MAX_LINES` (200), a cut diff or more than `FAR_MAX_PATHS` (20)
  paths (`TOO_LONG`: what this computer cannot show whole); a network host
  that does not read as one (`events.host_ok`: `HOST_UNREAD`); the caps of
  every turn; and the desktop app not there to answer: unless it read the
  asks within `SHELL_SEEN_S` (60 s), the ask waits up to `SHELL_GRACE_S`
  (15 s) for its first read, else `NOBODY_HERE`. No grant is looked up for
  a remote turn: only *Allow once*, *Decline* and the shell's *Stop
  asking*, which declines and sets what the agent may run back to `never`
  (`PATCH /api/local-agents/{agent}` `{runs: "never"}`, as its owner).
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
- **What that server learns**: the message's `waiting` (`home.CHAT_FIELDS`;
  `_turn_fields` takes it as a flag, `chats.write_run` and `_end_run` set it
  false at every end) and the steps as for any turn (`{id, kind, status,
  program | path | tool}`, a step id never this computer's ask id); never `asks`.
  Integrity, not secrecy: the agent's next words may say what it did.
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
  older than an answer. The chat's own answer route refuses every remote
  turn (409 "This is answered on the computer that runs the agent."), by
  rule too: `_ChatTurn.answer` refuses it, only `answer_computer` reaches
  `_answer_here`.
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
  message says `waiting` (`chats.remote_turns`' `asking`).

## Gamma's tools

Step 2.7 of
[agents-as-participants-plan.md](../research/agents-as-participants-plan.md)
gives a turn of this server Gamma's read tools, and step 3.3 its writing
tools (`comment`, `resolve`, `highlight`, [mcp.md](mcp.md) "The Comment
level"): the agent calls this server's own MCP endpoint ([mcp.md](mcp.md))
with a token of the turn's (`tools.py`, `runner._Turning._tools`). What it
writes is signed and stamped as this computer's agent, for the turn's owner,
and the owner can delete it; notes it changes only through the turn's asks.

- **The token** (`integrations.mint_turn`): scope `turn`, the turn owner's,
  of the turn's workspace alone, named with the agent's id
  (`computer:<this computer>:codex`, `agents.here`: what it writes through
  MCP is that agent's, `agents.of_token`), living the run's timeout (`RUN_TIMEOUT_S`) and
  `TOKEN_SLACK_S` (60 s). It is minted when the turn starts, once, for a
  chat turn (`_ChatTurn`, before its prompt and the folder's snapshot) and
  a Send's run (`_Run`) of this server; each start of the agent (a retry
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
- **The prompt** of a chat turn names the tools that answer for what the
  conversation is about and keeps Gamma's text data ("The chat's computer
  side", step 3); a chat turn mints the token before it, a Send's run when
  its turn starts. The Send's prompt (`prompt.build`) names no tool: its
  comments are in it.
- **A step** of a call is of kind `gamma` with the tool's name (`tool`:
  `read_page`, `list_pages`, …): the runner keeps `{id, kind: "gamma",
  status, tool}`, the export words it "Read Gamma: read_page", a writing
  tool's "Wrote in Gamma: comment" (`events.gamma_words`, the chat's
  `stepText` and `stepNow` alike), and the chat's chip is the kind Gamma
  AI's own call of that tool is (`computerChat.stepChip`), a writing tool's
  the kind `write`, which lists no change under the reply.
- **A far turn** (one this computer runs for another server's work folder
  over the channel, "A work folder of another server") gets no token here:
  this server's token reads this server, not that one. Step 4.2 has that
  server mint one at the claim and hand it over with its own address.

## The drivers

`events.start(name, argv=, cwd=, prompt=, schema=, session=, images=, ask=,
gamma=, title=)` starts one turn of an agent and gives a `Turn`: `session` (the
agent's session or thread id, "" until known), `pid`, `events()` (blocks;
read once) and `stop()` (thread-safe, returns at once, idempotent).
`gamma` is `events.Gamma(url, token)`, Gamma's tools for the turn, or
None; `title` a new Codex thread's name (a chat turn's conversation's
title; Claude Code takes none). `events()` gives plain dicts:

| type | fields |
|---|---|
| `session` | `id` |
| `text` | `text`, a whole message the agent wrote |
| `tool` | `id`, `kind` (`read`, `edit`, `run`, `search`, `gamma`, `other`), `title` (one line: `Read sections/intro.tex`, `Run latexmk -pdf main.tex`, `Read Gamma: read_page`), `status` (`started`, `completed`, `failed`), `path` (read, edit: relative to the directory with `/` when inside it, else absolute), `command` (run), `output` (a run's last 2000 characters, at its end), `tool` (gamma: the tool's name) |
| `done` | `ok`, `answer` (the parsed answer or None), `text` (the last message), `error` ("" when ok), `stopped`, `usage`, `gamma_down` (Gamma's tools did not start or connect: `events.GammaDown`) |

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
ask before such a tool ran.

### Codex

`codex app-server` on stdin and stdout: JSON-RPC, one object per line, no
`"jsonrpc"` key (the protocol of codex-cli 0.167.0's generated schema). One
loop dispatches every message in every phase, since notifications and the
server's requests come before the response a request waits for; requests
carry increasing ids and wait 30 s (`initialize`, `config/read`, the
sandbox check) or 120 s (`thread/start`, `thread/resume`, `turn/start`).

1. `argv + ["app-server"]` in the vault's directory.
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
   gone starts a new one). `_quiet` turns off every server the user's
   `mcp_servers` names (`mcp_servers.<name>.enabled = false`), sets
   `features.apps = false` and `web_search = "disabled"`. Verified on the
   development machine (codex-cli 0.167.0, three MCP servers in
   config.toml) with a zero-token probe of `thread/start` counting
   `mcpServer/startupStatus/updated`: `config.mcp_servers = {}` does nothing
   (it merges: 6 notifications); every listed server `enabled = false`
   keeps the user's servers down but ChatGPT's apps server (`codex_apps`)
   still starts (2); `features.apps = false` beside it leaves none (0).
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
   [{type: "localImage", path}] (at most 6), outputSchema, sandboxPolicy:
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
8. The answer is the last `agentMessage` parsed as JSON (a `json` code fence
   around it stripped), None when it is not.
9. **With `ask`** (a chat turn of a folder that lets its agent ask, "Asking
   in the chat") the thread starts or resumes with `approvalPolicy:
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

### Claude Code

`claude -p --output-format stream-json --verbose --restricted
--permission-mode acceptEdits --permission-prompts none --strict-mcp-config
--tools Read,Edit,Write,Glob,Grep --disallowedTools Bash,WebFetch,WebSearch`,
then, with `gamma`, `--mcp-config {"mcpServers":{"gamma_turn":{"type":"http",
"url":<the address>,"headers":{"Authorization":"Bearer
${GAMMA_AGENT_MCP_TOKEN}"}}}} --allowedTools mcp__gamma_turn` (`claude.mcp_config`:
Claude Code fills the variable from its own environment, so the token is
on no command line; `--allowedTools` runs Gamma's tools without a prompt,
which with prompts off would deny them; `--tools` limits the built-in
tools only: C2 of the plan), then `--json-schema <the schema>` and
`--resume <session>` when the session matches `^[A-Za-z0-9_-]{1,128}$`
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
  refused, the structured answer kept. The cost: the user's own settings
  (a chosen model) are not read either, so the account's default model
  answers.
- **The prompt** goes on stdin from a thread of its own, started at once
  (Claude Code waits about 3 s for stdin), and stdin is closed after it.
  The pictures are not passed: the prompt names them and Claude Code opens
  them with Read.
- **Lines**: `system`/`init` → `session` (`session_id`); with `gamma` its
  `mcp_servers` must name `gamma_turn` `connected`, else the turn fails
  ("Gamma's tools did not connect for Claude Code (it says failed).",
  `events.GammaDown`): Claude Code would go on without them. `assistant`: every
  block of `message.content` (one message may come split over several
  lines): `tool_use` → a tool started (`Read` → read `file_path`; `Edit`,
  `MultiEdit`, `Write`, `NotebookEdit` → edit; `Bash` → run; `Glob`,
  `Grep` → search; `mcp__gamma_turn__<tool>` → `gamma` with its `tool`;
  `StructuredOutput`, the tool `--json-schema` answers
  through, is no step; anything else `other`); `text` → `text`. `user`
  lines' `tool_result` blocks end the matching tool (`is_error` failed).
  `result` → `done`: ok when `subtype` is `success` and not `is_error`,
  the answer `structured_output` or else `result` parsed as JSON, the
  error the `result` text or the subtype.
- **`stop()`** kills it: in this form (a text prompt) `-p` has no interrupt.
- **With `ask`** (a chat turn of a folder that lets its agent ask, "Asking
  in the chat") the flags are their own, `ASK_FLAGS`: `-p --input-format
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
  of them, then kills each tree. `runner.shutdown` first sets every run's
  stop with the reason "closing", so their requests fail with "Stopped:
  Gamma is closing."
- `run_quick` never raises: `(returncode, stdout + stderr)`, `(-1, "timed
  out")` past its timeout with the tree killed, `(-1, reason)` when it
  cannot start.

## Recovery

`app._startup_maintenance`, after vaults.db is made, calls
`runner.recover()` when `enabled()`, in a `try` that logs (the agents never
stop the server). Every turn's token a stopped server left is deleted
(`integrations.drop_turns`: no turn runs yet). Every run a stopped server left open
(`store.open_runs`) fails its request, `only_from=("running",)` (a
withdrawn one is left as it is): "Gamma stopped while Codex worked on
this. Send again.", and the run ends `failed`; that includes a run left
open because its request could not be written. The agents of vaults that
are gone are forgotten (`store.forget_links`). Then, in a daemon thread,
the chat turns it left open (`store.open_turns`, `agent_turns` with no
`ended_at`) fail, their message too when it still reads queued or running
("Gamma stopped while Codex worked on this.", `_fail_left_open`), and
each vault with an agent takes the Sends that still wait for it
(`queue_folder`, `waiting`), as turning it on does: one handed over before
the server stopped is taken again; and the directories of their own of
conversations gone meanwhile are removed (`chats.sweep_dirs`). A workspace behind on its migrations
(`migrations.is_behind`) is waited for, every `RECOVER_POLL_S` (5 s) and at
most `RECOVER_WAIT_S` (600 s), while the background walk upgrades it; one
not upgraded by then waits for the next Send, pause or start.

## What this computer keeps

vaults.db (`db.VAULTS_SCHEMA`: what this computer knows, never migrated,
additive statements; `gamma/local_agents/store.py`, every function its own
short connection):

| Table | Columns |
|---|---|
| `computer_agents` | `agent` (the key: `codex`, `claude`), `enabled`, `runs` (`never` or `ask`; `never` while it is off), `set_by` (the account that turned it on, which owns it), `set_at`; a row stays when it is turned off. Turned on again while on, it keeps its owner and `set_at` |
| `agent_dirs` | `id` (the key), `path` (a directory as `folder_links.clean_path` gives it), `name` (the directory's own when none is given), `added_at`: the directories offered to the agents beyond the vaults, in the order last given |
| `agent_runs` | `id`, `ws`, `link_id`, `request_id`, `agent`, `session` (the agent's session or thread), `state` (`running`, then `done`, `failed` or `stopped`), `started_at`, `ended_at` (null while it runs), `detail` (the request's detail the run ended with: a done or failed run's words, "" for a stopped one), `error` (this computer's record of a failure: the agent's whole error and its stderr tail, never shown to members), `changes` (JSON, the source files changed, at most 200), `built`, `steps` (JSON, at most 200), `pid`; indexed by `(ws, request_id)` and on the open runs |
| `agent_threads` | `link_id` and `thread_id` (the key), `agent`, `session`, `cwd` (the vault's directory the turn ran in), `seen_at` (when the run read the thread), `read_ids` (JSON, the comments it read), `comment_id` (its reply), `request_id`, `at`: the thread an agent of the vault answered last ("Resuming the session") |
| `agent_conversations` | `conversation_id` (the key), `link_id` (its vault's, "" outside one), `agent`, `session`, `cwd`, `at`: the session a chat conversation's turns resume ("The chat's computer side") |
| `agent_turns` | `id` (the turn), `ws`, `link_id` ("" outside a vault), `conversation_id`, `page_id` (the conversation's bucket), `agent`, `state` (queued, running, then done, failed or stopped), `started_at`, `ended_at`, `error` (this computer's record), `pid`: a chat turn's run |
| `agent_dir_grants` | `conversation_id`, `dir` (the directory the turn works in), `command`, `cwd` (the folder below it the command runs in, `.` the directory) and `host` (a network host, "" for none), the key; `agent` (the conversation's), `at`: a command the person allowed in that chat, answered by Gamma when the same command is asked again in the same directory and folder for the same host |
| `agent_folders`, `agent_policies`, `agent_grants` | what each vault's agent was, what it could run and the chat grants keyed by the vault, before agents were the computer's: left unread. `store.carry_over`, at the start (`runner.recover`), once (nothing while `computer_agents` has a row), turns on for the computer every agent on for any vault, owned by the account that turned it on first, and lets it ask where a vault of it was let ask on this computer (`computer`, which covered the chat here and another server's turns, as `ask` now does; a vault's `ask`, its own chat's alone, carries `never`, so the person lets it ask through the desktop's warning); the grants are not carried |

`carry_over`, `agents()` (every row, in the catalog's order),
`enabled_agents()`, `agent_for(user_id)` (the account's first agent on),
`owns(user_id, agent)`, `runs_of(agent)`, `set_agent(agent, enabled, by=)`
and `set_runs(agent, runs, by=)` (each → whether it changed: turned on by
anyone while off, off and narrowed by its owner alone), `dirs()` and
`set_dirs(entries)` (the whole list, an offered id kept), `grant`,
`granted` and `grants_of` (by the directory), `start_run`, `update_run` (`session`
and `pid` while it runs), `end_run` (a run that ended stays as it ended),
`open_runs` and `run_of` (the newest run of a request), `answered_thread`
(a thread's row, in place of the one it had), `threads_of(link_id,
thread_ids)` (one query), `conversation_of`, `ran_conversation` (a chat
conversation's session, in place of the one it had), `forget_conversation`
(a conversation deleted), `add_turn`, `turn_running`, `end_turn`,
`get_turn` and `open_turns` (a chat turn's row; `claimed_of` the row of a
turn of another server's page this computer claimed, by which this
server's own chat route answers such a turn's asks 409). What goes with a vault:
`forget_threads(link_id)` (a relink: the threads' and the conversations'
sessions, which belong to the directory that was);
`vault.store.forget_link` (a vault removed: its `agent_threads` and
`agent_conversations` rows and its ended `agent_turns`);
`forget_links(live_ids)` (vaults gone while this server was not looking:
the same). The agents and the grants are no vault's: they stay. A turn still open stays, so the next start
fails it and its message (`recover`). Nothing of this travels:
the workspace's members see the request's state and detail, the replies,
and nothing of the run's row.

## The routes

| Method | Path | What |
|---|---|---|
| GET | `/api/local-agents?fresh=0\|1` | `{here, computer, agents: [{name, label, found, version, ready, problem, code, models, at, enabled, runs, mine}], dirs: [{id, path, name, added_at}]}` (`here` whether this server runs agents, `computer` this computer's name as its channels announce it; `code` how the agent stands as a code, `catalog.code`: `checking`, `missing`, `signed_out`, `not_ready` or `""`, which the client words; `models`: "Its model, its typing…"; `enabled` whether it is on for this computer, `runs` what it may run, `never` or `ask`, `mine` whether the session's account turned it on: the one who may turn it off or narrow it; `dirs` the directories offered beyond the vaults), Codex then Claude Code, from `catalog.cached`; `fresh=1` probes both anew, at most once every 10 s on this server (sooner, the kept answers). A signed-in account's session (`require_personal_user_id`; no token, no guest); when not `enabled()`, `{here: false, computer: "", agents: [], dirs: []}`, so every window asks it without an error (`gamma/routers/local_agents.py`, registered beside the feedback router) |
| PATCH | `/api/local-agents/{agent}` | `{enabled?, runs?}` → the agent as the listing says it, with `queued`, how many waiting Sends of this server's work folders it took when turned on (`runner.queue_waiting`; another server's Sends are its own to hand over, which the channels ask for). The gate, in order: `enabled()` (404 "Agents run on the desktop app's own server."), a session, an agent Gamma knows (404 "Gamma knows no agent named …"), a known `runs` (400 "What an agent may run is never or ask."); turning it on: the shell's word (`shell.from_shell`, 403 "Only the desktop app may turn on an agent on this computer.") and `catalog.probe(name, fresh=True)` ready (400 with its problem), or, while it is on, its owner (409 "Codex was turned on by another account on this computer."); turning it off: its owner, or the shell's word never unchecked (`shell.shell_strict`, 403 "Only the account that turned on Codex, or the desktop app, may turn it off."), which turns it off whoever owns it; `runs`: the agent on after this PATCH's `enabled` (400 "Turn Codex on first."), its owner (403), and for `ask` the shell's word (403 "Only the desktop app may let an agent ask to run commands on this computer."). Turned on, its account owns it (`store.set_agent`); turned off, its runs and chat turns stop (`runner.stop_agent`); either tells the work folders' open pages and the channels (`folder_links.agents_changed`); set back to `never`, its turns' asks are declined (`runner.runs_never`) |
| PUT | `/api/local-agents/dirs` | `[{id?, path, name}]` → `{dirs}`: the directories offered to the agents beyond the vaults, replaced whole (`store.set_dirs`: an `id` offered keeps its row and `added_at`, one left out goes, a name left empty is the directory's own). An entry that names a path not offered under its id takes the shell's word (403 "Only the desktop app itself may name a path on this computer."), and must be a directory here (400) that a vault could have (`folder_links.clean_path`, 400 with its refusal); any change takes an account with an agent on here, else the shell's word (403 "Only an account with an agent on this computer, or the desktop app, may change the directories it offers."). 400 for a new path inside or around a vault here (a directory vault, or a file vault's own file: "… is inside or around the synced folder …") or another entry of the list ("… is inside or around …, also offered."), for more than 50, or a path or an id given twice; 404 where agents do not run |
| GET | `/api/folder-links` | each link's `agent`: `{name, label, ready, problem, running: {kind: "send" \| "chat", request_id (null for a chat turn), detail} \| null}` for a work folder, of the agent that works there (`local_agents.info.listing`: the one at work there now, else the vault maker's first agent on), else null (one store query for the whole listing, readiness from `catalog.cached`, nothing run). `PATCH /api/folder-links/{id}` sets no agent, the agents being the computer's; `{paused: false}` and a saved selection (`PUT …/selection`) hand the Sends that wait to the agents on (`_hand_waiting`); a pause, a relink and a delete stop the vault's run. A member who cannot act on the vault (`_acts`) reads `running.detail` as "" (it may name files) |
| GET | `/api/folder-links/page/{page_id}` | the page's `vault` gains the same `agent` (`folder_links.page_vault`), on a server that runs agents only, and `mine` (whether this account made the vault) on every server. Turning an agent on or off tells each open page of every work folder here `{t: "vault"}` (`folder_links.agents_changed`, `_tell_open_pages`, from `collab.open_pages()`), so its chip and the comments' @ menu read it again |
| POST, PATCH | `/api/pages/{id}/comments`, `/api/comments/{id}` | a comment that names the writer's agent on a file of a work folder kept here is a Send too, answered as `send` ("A mention in a comment") |
| GET | `/api/local-agents/chat?bucket=&version=&agent=` | the chat's computer side ("The chat's computer side") of a bucket (a page's id, a folder's or `home`), an account with an agent on here, or the vault's maker on its work folder: `{computer, conversation}`, `computer` the strip (`{agent, label, ready, problem, models, folder, paused, turn, session, folder_id, dir, grants}`, `agent` the one picked (`agent`, while the account turned it on) or else the account's first (`store.agent_for`), null for none, `session` whether a terminal can continue the conversation, `ready` and `problem` from `catalog.cached`, `turn` `runner.chat_state` or null, `folder`, `folder_id` and `paused` the work folder's ("", null and false outside one), `dir` where the agent works (`{kind: "vault", name, path}`, the path for the vault's maker alone; `{kind: "offered", id, name}`; null for a directory of its own), `grants` the commands allowed in the conversation in that directory, `[{command, cwd, host}]`) and `conversation` the bucket's active one (`{id, title, agent, messages, version}`, null for none, and for one of another agent than `agent`); with `version` the stored one, `{computer, same: true}` |
| POST | `/api/local-agents/chat/turns` | `{bucket, text, selections?, id?, agent?, model?, effort?, dir?}` → 202 `{conversation, turn: {id, state}}`, `agent` the one picked (left out, the account's first, `store.agent_for`; another computer's turn runs that computer's), `dir` where the conversation works outside a work folder kept here (an offered directory's id, or `""` for one of its own; left out, the active conversation's): another `agent` or a `dir` other than the active conversation's starts a new one (`chats.begin`); 400 for no text, past 20,000 characters, a malformed `id`, a `dir` no offered directory has, or a model or effort the agent did not list; 409 while a turn of the conversation is queued or running ("Codex is still working on the last message…", named by that turn's agent), while the account has no agent on here ("Turn on Codex or Claude Code in Settings → AI first.") or not the one picked ("Turn on Claude Code in Settings → AI first."), for a folder paused or choosing its files, and for an agent known not to be ready (its problem); 403 when the account is no editor or writes are refused. A repeated `id` (the person's message's) answers the turn it made |
| POST | `/api/local-agents/chat/turns/{id}/stop` | `{stopped}` (`runner.stop_chat`) |
| POST | `/api/local-agents/chat/new` | `{bucket}` → `{conversation: null}`, the active conversation archived whichever its agent; 409 while a turn runs |
| GET | `/api/local-agents/chat/history?bucket=` | `{conversations: [{id, title, agent, updated_at, turns}], active}`: the archived ones newest first, and the active one as they are listed (`chats.listed`, null for none), whichever its agent, which the chat's History lists while another agent is picked; the account's own on every server, whatever became of its agents (no seat asked) |
| GET | `/api/local-agents/chat/history/{id}/export` | a conversation of the account's, active or archived, as a file to keep (`chat_export.export_file` with the agent's label: the person's messages with their passages, the agent's steps in words, its reply, what it changed and how its turn ended); 404 for another account's, as the other history routes; like the list and delete, it asks no seat, so a conversation whose agent was turned off is still the account's to keep |
| DELETE | `/api/local-agents/chat/grants/{id}` | the conversation's Allowed in this chat taken back (`store.forget_grants`) → `{forgotten: true}`; 404 for a conversation not the account's |
| POST, DELETE | `/api/local-agents/chat/history/{id}/open`, `/api/local-agents/chat/history/{id}` | open (→ `{conversation}`, 409 while a turn runs, 404 for one not archived) or delete (→ `{deleted: true}`, 409 for the active one, whatever became of its agent) an archived conversation; open takes the bucket's seat, as a turn does |
| POST | `/api/local-agents/chat/turns/{turn}/asks/{ask}` | `{decision: "once" \| "chat" \| "deny", note?}` (a note at most 2,000 characters) → `{answered: true}`: the person's answer to a waiting ask ("Asking in the chat"); 400 for another decision, a longer note or `chat` on anything but a command; 404 for no such turn of the account or ask; 409 when it was answered, withdrawn or declined already, or the turn ended, and for a turn that runs on another computer, on every server ("This is answered on the computer that runs the agent.") |
| GET | `/api/local-agents/asks?after=&wait=` | the desktop app's alone ("Asking on this computer"): `{version, asks}`, the asks waiting on remote turns of the account's vaults, oldest first, held up to `wait` s (at most 20) while `version` is `after`; 404 where agents do not run, 403 "Only the desktop app reads what waits for an answer on this computer." without the shell's word or with no secret |
| POST | `/api/local-agents/asks/{ask}` | the desktop app's alone: `{decision: "once" \| "deny"}` → `{answered: true, version}`, given to the agent before it returns; 403 "Only the desktop app answers on this computer."; 404 "No question on this computer has that id." (another account's too); 400 "On this computer an ask is allowed once or declined."; 409 "{agent} no longer waits for that." or "This turn has ended." |
| GET | `/api/local-agents/chat/terminal?bucket=` | the desktop shell's alone in practice (Continue in a terminal): `{cwd, argv, agent, label}`, the conversation's session resumed in the directory it works in, with the conversation's agent ("Its model, its typing, a terminal…"); 404 "This conversation has no session to continue.", 409 while a turn runs, when the account no longer has that agent on here or it is not on this computer |
| POST | `/api/local-agents/chat/sources` | `{bucket, selections: [{text, page, box}]}` → `{sources: [{file, line, end?} \| null]}`, in order, nulls outside a work folder kept here; 400 past 20 selections |
| GET | `/api/annotations?scope=` | for a work folder's scope, on a server that runs agents, the JSON gains `agent` and `agents_found` for the Feedback dialog (annotations.md "The routes"); the files' document never does |
| GET | `/api/feedback/requests`, `/api/feedback/requests/{id}` | on a server that runs agents each request record gains `local`: whether a run on this computer holds it now (`runner.running`), so the dialog's Withdraw can say it stops the agent |
| POST | `/api/feedback/requests` | a token's Send to a work folder a vault here keeps while an agent is on, paused or not, is 403 "An agent on this computer takes Sends made in Gamma, not with a token." A Send is stamped `via` `session` or `token` either way, and a token's is never the agent's, so one made before the folder had an agent waits for an outside agent too |
| POST | `/api/feedback/requests/{id}/state` | a token's change while a run of the request is active here is 409 "Codex on this computer is answering this request." |

The chat routes answer 404 on a server that runs no agents, for no such
bucket (a live page, a folder, `home`), and for an account that has no
agent on here and did not make the vault of the bucket's work folder (so
nobody else learns it exists), 403 for a token or the guest
(`require_personal_user_id`).

`doc.agent` (`routers/feedback._agent_of`): null when no vault here keeps
the work folder or no agent is on; else the agent that takes this
account's Sends there (`runner.folder_agent`: its first agent on, else
another account's, which takes none of them) as `{name, label, ready,
problem, takes, why}`, `ready` and `problem` from `catalog.cached`, `takes`
whether a Send of this account would reach it now, and `why` (English)
when not: "Only the person who turned on Codex on this computer can send
to it." or "The work folder is paused on this computer: Codex starts once
it syncs again." (paused or
choosing its files). `doc.agents_found`: whether `catalog.cached` finds
either agent.

## The client

- **The Feedback dialog** (`frontend/src/feedback/`) reads `doc.agent` and
  `doc.agents_found` from its 5 s poll. `feedbackView.sendAgent(doc,
  kept)` gives the mode: `ready` and `not_ready` (the person's agent takes
  their Send), `checking` (`ready` null), `other` (an agent that
  does not take it), `offer` (no agent, one found, and the folder kept in
  this desktop app: `links.get(W)`), or none. `feedbackText.sendWords`
  turns it into the button and the lines under the note: a Send the agent
  takes reads *Send 2 comments to Codex* with "Codex works on this
  computer, in this folder's directory, and answers each comment."; not
  ready, the server's problem and "This Send fails until then."; checking,
  "Checking Codex…"; past `AGENT_MAX` (100) comments the button is shut
  with the limit's sentence; and "1 of these comments is by someone else:
  Codex treats it as context." when entries were opened by someone else
  (`othersCount`, by each entry's first comment). An agent that does not
  take the Send leaves the plain button with the server's `why`; `offer`
  says where to turn one on: "To have Codex or Claude Code answer here,
  turn one on in Settings → AI." The lines are a live region
  (`role="status"`, kept mounted while empty) that the Send button names
  as its description, so a change a poll brings is heard, and so is why
  the button is off.
- **Requests.** `requestLine` gives a short state in the Sent chip's words
  (`comments.sendState`, drawn by the same `StateTag`), naming the agent by
  the request's `state_by`, whoever set it, a token's agent included
  (*Waiting for Codex*, *Codex is working…*, *Done by Codex*, *Failed*),
  and apart from it the agent's own
  words (its progress, its summary, the reason it failed), which the row
  shows on a line of their own under the note, at most three lines, whole
  in its title. The Withdraw button's title (`withdrawTitle`) says "Stop
  Codex and withdraw: what it changed already stays in the files." only on
  a running request a run on this computer holds (`local`); nothing here
  stops an outside agent.
- **Comments** (`comments/comments.js`): a computer agent's comment
  (`agent_kind: "computer"`, `agent_on` its computer) reads *Codex* with
  *on TIM-PC, for tim* after it (`agentFor`) beside its bot avatar
  (`AgentAvatar`), and its `authorTitle` "Written by Codex on TIM-PC, for
  tim";
  `sentChip` of a running Send with `state_by` reads *Codex is working…*
  with a spinner, and one waiting, *Waiting for Codex* (the agent that
  answers the reader's Sends) or *Waiting for TIM-PC*.
- **The agents' list** (`frontend/src/chat/agents.js`, pure,
  `tests/chatAgents.test.mjs`): one list for the chat's picker, the
  comment fields and Settings, `[{id, kind: "gamma" | "computer", name,
  label, where, models, efforts, ready, problem, owner, …}]`. App reads
  `GET /api/local-agents` once for the window and again when it comes back
  (`chat/useLocalAgents.js`, a store every reader shares: the chat, the
  comments and Settings, which reads it afresh and after each change it
  makes) and the page's computers (`vault/usePageComputer.js`), and builds
  `chatAgents`: Gamma AI (`gammaAgent`, from the AI info and its
  connection's models), then on a page of a work folder another computer
  keeps that computer's agent there (`keeperAgent`, by its announce's
  codes; the server binds such a page's chat to it), else the agents this
  computer runs that the account turned on (`localAgent`: `enabled` and
  `mine`). Each agent's state is worded from its code (`checking`,
  `missing`, `signed_out`, `not_ready`); this computer's own problem
  sentence is shown as it came, since it names the command that mends it.
- **The mention.** App derives the agent the open page's comments can
  name from that list, `agents.mentionAgent(list, vault.state)`: `{name,
  label, ready, problem}` of the account's first agent here on a file of a
  work folder a vault here keeps (the one its mentions and Sends run with,
  `store.agent_for`: until step 3.5 a mention names that one alone, so the
  @ menu lists it alone), or with `computer` and `online` the agent of the
  computer that keeps the page's work folder, else null. It reaches every comment field as
  `links.agent` (the popover's and the margin's), never `MentionContext`,
  which reaches the notes. There the @ menu lists it under People
  (`mentions.mentionRows`' `agents`: a bot icon, the label, "on this
  computer" or its problem; a member of the same name is not listed
  there), the field and the maker's own posted comments draw `@codex` as
  the agent's chip (`mentionChip`'s `agents`), and while the text newly
  names it the bar under the field says what sending does
  (`comments.mentionHint`: "Sends this thread to Codex on this
  computer.", in the page's own thread "Sends this file's open comments
  to Codex on this computer.", the agent's problem, or for an edit the
  server will not send, why: in a resolved thread, and of a comment that is
  not the thread's newest, `Thread.jsx`'s `editRefused`). `useComments`
  takes the answer's `thread` (with its Sent chip) and says a refusal in
  the status line (`comments.sendRefusal`, by its code, else "Not sent to
  Codex: <why>").
- **The picker.** App passes the list to ChatDock (`agents`, with this
  computer's listing as `localAgents`: the offered directories, and the
  agents found that the setup card offers). The composer's chip (`chat/AgentChip.jsx`,
  a `MenuSelect`) lists the agents first, by their icon and where they
  run ("Codex", "Codex on TIM-PC"), then the picked one's models and
  efforts (Gamma AI's speed too), in every bucket: a page, a folder, the
  library. A viewer has Gamma AI alone. The agent shown is the bucket's
  pick for the tab's life (`chatSide.js` `useChatAgent`, beside the
  Background tasks row's `pickChatAgent`, so the first screen does not load
  the side's state), else `agents.pickAgent`: Gamma AI, unless no AI
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
  go, the chip's models and efforts, the Allowed in this chat chip, where
  a conversation that begins works, the passages' source lines); and
  `computerChat.shownMessages`, pure, maps each message: the person's to
  `{role: "user", text, passages}` (its passages' pages as the bubble's
  chips), the agent's to `{role: "ai", text, partial, pill, actions,
  asks, files, notice, model, effort}`: `partial` while its turn is queued
  or running (the reply as it is typed, Stop in Send's place), `pill` the
  steps pill's words (`turnLabel`: "Codex is working: Editing
  sections/intro.tex", "Codex is waiting for you", "3 steps"), `actions`
  its steps as plain chips of kinds of their own (`stepChip`: a file
  read, a file edited, a command, a search, other work; a call of Gamma's
  tools the kind Gamma AI's own call of that tool is, `GAMMA_KINDS`: list,
  view, cite, read…; none a change of
  the library or the notes, so Gamma AI's changes list and revert pass
  them by), `asks` while it waits on the person and the page draws them,
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
  (not a live region: the turn's pill is the one read out), neutral, amber
  when the agent cannot run or waits for an answer (`computerChat.stripLine`:
  "Codex on this computer · paper", "Codex is working: Editing
  sections/intro.tex", "Waiting for Codex to finish its other work in this
  folder", "Starting Codex…", the agent's problem, the paused folder),
  drawn from the list's facts (`agents.stripFacts`) until the read lands;
  after it, where the agent works outside a synced folder
  (`computerChat.workPlace`): while the conversation has no turn a menu of
  the offered directories and *No folder*, sent with its first turn as
  `dir`, then the one it works in. Its ⋯ offers Continue in a terminal
  and, on a page of a work folder kept here alone (`revealsFile`), Show in
  folder.
- **What it asks.** While a live turn has `asks`, each is a card under the
  steps (`ApprovalCard.jsx` `AskCard`, on the approval card's shell
  `chat/ApprovalShell.jsx` but with no *Always allow* and no Gamma
  permission): "Codex wants to run a command" with the command whole in
  its box in monospace, where it runs and its reason, or "Codex wants to
  change files outside its sandbox" with the paths and the diff; **Allow
  once**, **Allow in this chat** (a command only: "Run this exact command
  without asking for the rest of this conversation") and **Don't allow**
  (a note, then Don't allow). The box shows `askCommand`: the inner
  command of a shell wrapper with "Through PowerShell: …" under it, else
  `command_shown` with its `⟨U+…⟩` marks in the warning colour ("This
  holds 2 characters that do not show: …"), "3 lines" over it, "It reaches
  example.org on the network" for a `host`; the allow buttons stay off
  ("Scroll to the end first: …") until the command's box and the diff were
  scrolled to their end (`scrolledToEnd`, the answers' `disabled`). Under
  them "It runs as you, with your files and the network." or "It changes
  files outside the work folder."; the strip reads "Waiting for you: Codex
  wants to run a command" (amber, `asking`), the turn's pill "Codex is
  waiting for you", and the steps "Asked to run latexmk", "Allowed to…",
  "Not allowed to…". `useComputerChat.answer` posts the answer and leaves
  the rest to the next poll, so the card says "Allowed. Codex goes on…" or
  "Not allowed. Codex goes on without it…" until the read that drops the
  ask; a refusal is said on the card and the conversation read again at
  once. Another computer's turn draws no card: it waits for the answer on
  that computer.
- **What it changed** is Gamma AI's changes card (`AgentChanges.jsx`
  `FileChanges`, "Changed in the synced folder · 2", "Changed in its
  directory" outside one): a row a file, its path a `PathLink` resolved
  from the work folder's top (the strip's `folder_id`, the transcript's
  `PathLinkContext` while such a conversation shows), the first three then
  "and N more" unfolding to the rest, and "The PDF was rebuilt" last.
- **The composer** takes the message and the PDF passages selected (App's
  `pdfSelections`, Gamma AI's too, cleared once the server accepts the
  turn), each chip noting its source line; what only Gamma AI takes (the
  notes and the cursor's block, the @ pages, files, pictures, the PDF
  switch, the + menu) is not offered. The field is named "Message Codex"
  ("Message Codex on this computer…" its placeholder, or "on TIM-PC"); the
  **Allowed in this chat** chip shows while the strip lists `grants`
  (Gamma AI's `GrantChip`); Send, and Stop in its place while a turn is
  queued or running. A first read that fails says so where the
  conversation would be ("Could not load chat: …", **Try again**) and
  Send stays off until a read lands; a poll that fails keeps what was read.
- **The header** has what applies to such a conversation: Export (the
  conversation, `GET /chat/history/{id}/export`), Find, History and New
  chat, which keeps the conversation in History; New chat is off while a
  turn is to end ("Stop Codex or wait for it to finish…"). No AI service
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
  Gamma AI's, and selects and deletes the archived ones of either.
- **The vault chip's card** (`vault/VaultChip.jsx`, `vaultState.agentLine`):
  "Agent: Codex on this computer", the problem when it is not ready (amber,
  `.agentProblem`), and during a run the live tag "Codex is working…" with
  its detail on a line under it, the chip's icon a spinner. Opening the card reads the
  page's standing again (App's `vault.refresh`), since a run's start and
  end tell the page's room nothing.
- **The Inbox** is unchanged.

## The desktop app

The agents of this computer are set in Settings → AI, a page of Gamma,
which asks the desktop shell for every change that widens what an agent
reaches (the page asks of `desktop/preload.js`, [desktop architecture](../../desktop/docs/architecture.md)
"Pages asking the shell"): `set-agent` (`agent`, `enabled`), `set-agent-runs`
(`agent`, `ask`) and `agent-dirs` (`dir`: "new", or the id of an offered
directory to move), each only from a page of a server on this computer.
The shell's own session acts (main.js `setAgent`, `setAgentRuns`,
`agentDirs`), so its account owns an agent it turns on:

- **Turning one on** asks first (`keeping.agentAsk`: "Turn on Codex on this
  computer?", what it does in the person's chats on this computer (in a
  synced folder's directory, a directory offered to it or one of its own),
  and that on a server this computer is shown on whoever can sign in there as
  the person, and whoever runs it, can send work to it), then `PATCH
  /api/local-agents/{agent}` `{enabled: true}` with the shell's word while
  the bar reads "Checking Codex…"; a notice says what came of it
  (`keeping.agentSaid`, with the Sends handed to it). Turning it off asks
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
Claude Code work there once they are turned on in Gamma's Settings → AI
(`bar.html` `AGENTS_SET`). `lib/keeping.js` `vault()` still gives the item
`agentLabel`, `agentRunning` and `agentChat` (the run is a chat turn:
`running.kind` "chat") from the link's `agent`: while a run works the row
reads busy, *Codex is working · Editing main.tex*, its title saying it
answers a Send or works in the chat of a file of the folder, and the title
bar's summary *Agent working* when no round runs anywhere; a vault's own
problem (stopped, held, its directory gone, a sync error) keeps its row,
the run said in its title.

`refreshKeeping` reads `GET /api/local-agents` from each local server (5 s
timeout; a 404 or no answer is a server that offers no agent): on the host,
while an agent may ask (`runs` `ask`) and the host keeps a work folder of
another server, the shell follows `GET /api/local-agents/asks`, whose cards
are drawn in the bar over the whole window and answered with `POST
…/asks/{id}` ("Asking on this computer"; the shell's side is the
[desktop architecture](../../desktop/docs/architecture.md)'s). The card's
*Stop asking* declines and sets what its agent may run back to `never`.

## Trust

- **Who starts a run.** Only an agent's owner, the account that turned it
  on for this computer, by a Send made in Gamma (from the dialog, or a
  mention in their own comment, never through a share) on a work folder a
  vault here keeps, while still at least an editor of the workspace; the
  agent works in that vault's directory whoever made the vault. A mention
  in anyone else's comment, a token's or the agent's own reply starts
  nothing. The Send of a member with no agent on here is left for an agent
  they run; a token's Send to such a folder is refused, and a token's Send
  made before an agent was on is never taken (`via`), so a mirror or a
  script holding the owner's write token cannot start one, and a token
  cannot move a request a run holds. Turning an agent on, and letting it
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
  has none of the server's variables, it is handed the comments and
  answers in its final message, and Gamma writes the replies. Nothing is
  started with a bypass flag.
- **The turn's token** ("Gamma's tools"). The one Gamma credential an agent
  holds: Gamma's read tools on MCP, and its comments, resolves and
  highlights, in the turn's workspace alone, as the turn's owner and signed
  as the agent; refused by every route of the HTTP API; gone when the turn
  ends and at the next start, and never more than 60 s past its turn's
  deadline (the run's timeout from the agent's start, moved while an ask
  holds the turn). It reaches
  the agent's process in `GAMMA_AGENT_MCP_TOKEN` alone. Codex's own
  commands do not inherit it (`shell_environment_policy.exclude`, the
  user's own excludes kept beside it).
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
  agent ask at the computer, only in the chat (never a Send), only what the
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
- **Asking on this computer** ("Asking on this computer"). The person at
  the computer alone answers, in the desktop app's bar, with the shell's
  word on routes that never take a session alone; the other server starts
  the turn, so it writes what the agent is asked to do, but never the card
  or the answer, and learns `waiting` and the step statuses, never the
  ask. Over plain http it is refused (a man in the middle would write the
  prompt), without the desktop app's secret nobody is asked, and what this
  computer cannot show whole is declined unseen. The shell's secret is
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
- **The replies** are comments of the workspace: every member who can
  open the file reads them and the summary. The request's detail names
  only the files members see already (`tracked`); the run's error, its
  stderr and its steps stay on this computer, and a member who cannot act
  on the vault reads no running detail in the links or the chip.

## Known gaps

- Asking is the chat's alone: a Send never asks, so there Claude Code edits
  and does not build, and Codex's requests for approval are refused.
  The inner command of a shell wrapper (`unwrap_shell`) is checked against
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
  nor `PR_SET_PDEATHSIG`; the next start fails the request but does not
  look for the process (the run's row keeps its `pid`). On Linux only the
  agent itself gets the signal, not what it started.
- Gamma's own progress sentences (`Reading 2 comments`, `Editing …`,
  `Stopped: …`) are English, as an outside agent's detail is: the request's
  detail is the agent's words, which the interface shows as it is.
- Claude Code answers with the account's default model: `--restricted`
  reads none of the user's settings files, a chosen model among them.
- The Inbox says "Codex, with tim's connection" for a local agent's reply:
  its items carry no agent kind. Each reply is an Inbox item of its own, none
  for the finished request.
- The @ menu's agent row and the field's hint are the desktop app's: in a
  browser on the same server they are not drawn, though the server still
  sends the maker's typed `@codex`. The
  agent's chip in the maker's comments follows the switch as it is now, not
  which comments did start a Send.
- Deleting a mention's comment does not withdraw its Send (the Feedback
  dialog's Withdraw does).
- A thread the agent fixed is resolved, so a follow-up needs Reopen first;
  `@codex` in a resolved thread's reply (through the API) is refused.
- A run resumes a session only when every thread it answers was answered
  last in that one session; a Send of a file or a folder whose threads
  came from several starts a new one.
- Comments on a clone never reach this computer's agent, as they never
  reach its files (annotations.md "Known gaps"). A work folder of another
  server does ("A work folder of another server"), its chat too ("The
  chat on another server's page"); its asks are answered on this computer
  ("Asking on this computer"), and the terminal and Show in folder stay on
  this computer's own pages.
- On the NAS, another member's waiting Send in the maker's work folder
  reads *Waiting for TIM-PC* on the maker's page too (a thread's `sent`
  does not say who sent it).
- The chat's computer side: Claude Code builds only where the folder lets
  it ask; no diff or Revert of what it changed (Gamma writes no file the
  disk made: a decision first), no *Attach what I see*, no *Open comments
  on this page*, no rename of a conversation in History. The @ row names the account's first agent
  alone until a comment sent to an agent is a turn (step 3.5). Gamma's
  tools read only (commenting is step 3.3), a far turn has none (step
  4.2), and a server that learned no address of its own from this
  computer runs its turns without them. The window polls while a turn is to end (no push), so the
  typed reply grows in steps of about a second and a half. *Continue in a
  terminal* is unit-tested on every platform but opened for real on none
  yet; on macOS and Linux the terminal is found by name. When Codex silently answers a resume with a new thread,
  the turn reads `fresh` but carries none of the earlier messages, since
  Codex says so only once the turn is under way. The running step of a
  chat turn is in `agent.running.detail` for anyone who may act on the
  vault (only tracked files named), as a Send's progress is.

## Tests

- `tests/fake_agents/fake_codex.py`, `fake_claude.py`: standard-library
  stand-ins for the two CLIs (`--version`, the status, `app-server`, `-p`
  stream-json), scripted by `FAKE_AGENT` (edits, the answer or `each`, a
  delay, a failure mode, `resume` among them, `distinct` for a session id
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
- `tests/test_local_agents_drivers.py`: both drivers' events, answers and
  flags, a stop ending with `done(stopped=True)` and no process left, a
  crash, garbage lines, silence, the approval answers' shapes, the sandbox
  not ready, an MCP server that starts anyway (also before `thread/start`
  answers), every step one of `events.KINDS`, a turn closed unread, a new
  Codex thread named with its title (not a resumed one, nor one with no
  title; a Codex that refuses it or never answers), Gamma's server under
  a name of its own beside a user's `gamma` turned off, the user's
  excludes kept with the token's variable added once, a Gamma that did
  not start or connect marked `gamma_down`.
- `tests/test_local_agents_runner.py`: the whole loop on the sample paper
  as a work folder (replies signed Codex with `agent_kind` computer, the fixed
  ones resolved, `done` with the summary and the changed files, progress
  with no path outside the folder and no untracked file named), another
  member's Send not taken, a token's refused and one made before the agent
  never taken, a withdraw during the probe, the turn and the answers, a
  token's state change refused while running, not ready, the timeout,
  nothing pending, more than 100, no reply written, a run stopped while it
  waited for a slot taken again, an error before the turn is read, a
  request that cannot be written left for the next start, recovery (what
  waits taken, the agent a vault had carried over, a workspace behind on
  its migrations waited for), turning on, turning off stopping that
  agent's run alone, pause and unpause, `folder_agent` for the account and
  for another; the words (tracked names, wrapped commands, redaction, a
  walk cut short), the store (the computer's agents, their owners, what
  they may run, the grants by directory, the offered directories), the
  carry-over once, and the prompt on their own.
- `tests/test_local_agents_mention.py`: a mention in a thread and in the
  page's own thread, where it is plain text (each gate of the rule), an
  edit's new mention of the newest comment, each refusal with the comment
  posted anyway, a retried POST sending once, the instruction without the
  mention, a member named codex told nothing (and told, of the maker's
  comment through a token), a mention made while a run works answered by
  the next run (the thread not resolved over it), another member's reply
  not cancelling a waiting mention, a word unread by its id whatever its
  time, a follow-up resuming the session, two threads of two sessions
  resuming neither, a refused resume started afresh, the vault's `mine`
  and its open pages hearing an agent turned on or off.
- `tests/test_local_agents_extras.py`: the probe listing the models, a
  choice one the agent listed, a turn running the model and effort asked
  for (Codex's thread and turn, Claude Code's flags), both drivers
  streaming a chat turn's text, the reply growing then replaced by the
  whole message, the terminal route for Codex and Claude Code (and the
  strip's `session` agreeing with it), the sources of the selections, the
  Background tasks rows (running and asking, queued, gone after a Stop).
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
  expired, a Stop while asking and who may answer, a folder that does not
  ask and a Send never asking, Claude Code asking a command and nothing
  else, stopped while asking; an ask's command shown as given and its
  paths made short only at a boundary, a grant per command, folder and
  host, Codex's terminal input and a protected change declined unasked,
  asking turned back to never mid-turn, a diff's headers and its cut,
  Claude Code's ask turn starting each Bash call in the folder; what an
  ask shows (`shown_text`'s marks, `host_ok`, `unwrap_shell` on a round
  trip only, `_program` naming its inner command, a card's `marked`,
  `lines`, `inner`), Claude Code's allowed input cut to command,
  description and timeout, Gamma's declines worded as Gamma's, a
  `grantRoot` declined unseen, the 20 asks and three declines of a turn.
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
  tried again.
- `tests/test_local_agents_chat.py`: the chat's computer side: who may (a
  co-author with no agent on, on a file, a library page and the library,
  no such bucket, another server: 404; a token and the guest: 403; one who
  turns an agent of its own on, in conversations of its own), a turn run and its reply kept
  (text, steps, changes), a follow-up resuming the session, Codex not
  resuming and saying so, Claude Code started afresh, a reply that loads
  nothing by itself, Stop of a running, a queued and a slot-waiting turn
  and what waits for one (New chat, open, a second message), a version
  never another conversation's, a turn behind a Send, a pause, the strip
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
  page's standing carrying `agent` without running a process, `GET
  /api/local-agents` (`enabled`, `runs`, `mine`, `dirs`), `doc.agent`, a
  request's `local`, a pause, a relink and a removal stopping the run.
- `tests/test_migrations.py` (`test_v58_*`): `via`.
- `tests/test_annotations_requests.py` (`claim`, `only_from`,
  `change_listeners`), `tests/test_comments.py` (`agent_kind`, `agent_on`,
  `state_by`), `tests/test_annotations_entries.py` (a computer agent's
  comment's Markdown), `tests/test_agents.py` (who an agent is).
- Frontend: `tests/feedbackView.test.mjs`, `tests/feedbackText.test.mjs`,
  `tests/comments.test.mjs` (the hint, the refusal's words),
  `tests/chatAgents.test.mjs` (the agents' list, the picker's rules, the
  agent a comment names), `tests/vaultState.test.mjs` (`vaultAgent`),
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
  `GAMMA_AGENT_CODEX` / `GAMMA_AGENT_CLAUDE` and `FAKE_AGENT`; the dialog's
  offer, turning Codex on, a Send from the dialog read as *Codex is
  working* then *Done by Codex* with the summary and the source file it
  changed, the replies' signature, the threads resolved; `@codex` picked
  from the @ menu in a reply on the PDF (the row, the hint, the Send of
  that thread with the comment's words, the agent's chip), a follow-up
  after Reopen resuming the session that answered, an edit of an older
  comment refused with its reason in the status line, a mention in the
  page's own thread sending the file's open comments; Codex in the chat
  (picked in the chip, the strip, a message with a PDF passage worked and
  answered in the chat's transcript, the prompt carrying both), a tab closed mid-turn finding the
  turn again in a new one and the follow-up resuming Codex's session, Stop,
  New chat and History; asking turned on for the folder, a command Codex
  asks to run waiting on a card (the strip, the command whole, its reason)
  until allowed once, declined with a note (Codex's steer), allowed in this
  chat and the same command answered unasked in the next turn; the passage
  chip's source line, the strip's menu asking the shell (the asks
  recorded, no shell in the browser suite), a model and effort chosen in
  the chip reaching Codex, the reply seen part-way as it is typed, a
  running turn's Background tasks row and its Stop; with no AI service, a
  library PDF's chat that is Codex's, the setup card picking Codex again
  after Gamma AI, a directory offered picked in the strip and edited, the
  reply and what it changed in the transcript; with no AI service, Codex
  reading a library PDF's highlight through Gamma's tools with its turn's
  token (the prompt pointing to `read_page` by the page's id and pasting
  none of its notes, the call answered, the reply quoting the highlight
  and its note, the step *Read Gamma: read_page* in the pill, the server
  log saying the token was deleted); turning it off.
