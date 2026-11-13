# An agent on this computer

On the desktop app's own server, the one that keeps synced vaults, Gamma
itself can be the connector that [feedback.md](feedback.md) "The seam"
leaves to the owner. The maker of a work folder's vault turns on Codex CLI
or Claude Code for it in the desktop app's sync panel. From then on a Send
of that folder's feedback, made in Gamma by the vault's maker, is taken by
this server: it starts the agent in the vault's directory with the comments
as the prompt, shows what the agent is doing as the request's `running`
detail, writes the agent's answer to each comment as a reply signed
`Codex` or `Claude Code`, resolves the comments it fixed, and sets the
request `done` (with a summary and the source files changed) or `failed`.
A withdraw stops the agent. The maker can also send by naming the agent in
a comment (`@codex fix this only`: "A mention in a comment"), and a
follow-up in a thread the agent answered resumes the agent's session. And
the chat window has a second side where the maker talks to the agent
directly ("The chat's computer side").

The design, the survey of both CLIs and the later steps (`@codex` in a
comment, the chat window's agent side, a remote server seeing the
computer) are the research note [agent-collab.md](../research/agent-collab.md);
this is step 1 as built. Backend: the package `gamma/local_agents/` and
`gamma/routers/local_agents.py`, with small parts in
`gamma/feedback/requests.py`, `gamma/comments.py`, `gamma/folder_links.py`
and the folder-links and feedback routers.

| Module | What |
|---|---|
| `__init__.py` | `enabled()`, whether this server runs agents ("Who turns it on") |
| `catalog.py` | the known agents, where each is on this computer, whether it is ready ("Finding the agents") |
| `procs.py` | running a CLI: its environment, no console window, a process tree that dies with the turn or the server ("Processes") |
| `events.py`, `codex.py`, `claude.py` | the drivers: one turn of an agent as a stream of plain events ("The drivers") |
| `prompt.py` | what the agent is told and the shape of its answer ("The prompt and the answer") |
| `runner.py` | hearing the requests, claiming them and running them ("Claiming and the run") |
| `mention.py` | a comment that names the agent as a Send ("A mention in a comment") |
| `chats.py` | the chat's computer side: a page's conversations with the agent ("The chat's computer side") |
| `store.py` | what this computer keeps, in vaults.db ("What this computer keeps") |
| `info.py` | what a vault says of its agent, for the links and a page's chip ("The routes") |

`__init__.py` imports only `config` and `version`: `folder_links`, the
feedback modules and the routers import the rest inside their functions,
and the runner imports `gamma.feedback.agent` and `folder_links` inside
its own, so no cycle forms at import.

## The words

**An agent** is the UI's word for whatever writes through an integration
token or runs on this computer for Gamma ([i18n.md](i18n.md) glossary).
**An agent's name** is `codex` or `claude`, its **label** `Codex` or
`Claude Code`. **The folder's agent** is the agent a work folder's vault on
this computer answers its Sends with, or none. **A run** is one request
claimed by this server and worked by one turn of one agent. **The maker**
is the account that made the vault (`folder_links.created_by`). The
interface says "Codex on this computer"; the files and every text written
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
part answers as if no folder had an agent: the routes below 404, the
listener does nothing, the documents carry no `agent`.

Only a work folder's vault can have an agent (`folder_links.work_folder_link`),
and only its maker may set it. Turning one **on** (or changing it) also
needs the desktop app's own word when the app started this server with its
secret (`config.shell_secret`; the desktop app always does): `_from_shell`,
the `X-Gamma-Shell` header ([desktop architecture](../../desktop/docs/architecture.md)
"The shell's word"), refused with 403 "Only the desktop app may turn on an
agent for this folder." An agent that edits a directory is the computer's
decision, so on the app's server a page of Gamma, which shares its origin
and session, cannot turn one on by itself. A server run with
`GAMMA_LOCAL_AGENTS=1` and no secret (the tests, the e2e server) has no
shell to ask, so there the maker's own session turns one on, as it names a
path on the routes that take one ([synced_vaults.md](synced_vaults.md#the-routes)).
Turning it **off** needs the maker alone. The agent must be known and
ready, asked afresh at the PATCH.

A vault forgets its agent when it is relinked or removed (consent was given
for one directory), and the run stops when the agent is turned off or
changed, when the vault is paused, relinked or removed, and when its push
is lowered so it is no work folder any more (`folder_links._stop_agent`,
called from `set_agent`, `set_paused`, `relink`, `delete_link` and
`set_push`).

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
feedback.md "Requests and their states") and registers `runner.shutdown`
at exit.

- **`on_request(ws, request_id)`** is cheap (a few reads, no process),
  never blocks on a run and never raises. A request now `failed` whose run
  is active sets that run's stop (reason `withdrawn` when its detail is
  `withdrawn`); the watchdog stops the turn. A `sent` one is queued when
  `requests._local_link(ws, W)` finds the vault (a work folder of this
  computer, not paused, not choosing its files), the vault has an agent
  (`store.agent_of`), the requester is the vault's maker and the Send was
  made in Gamma (`via` `session`, feedback.md "Requests and their states";
  never a token's, nor one from before Sends said how they were made:
  `_ours`). Each request is queued once; one already queued or running is
  left as it is, but one whose run was stopped while it waited for a slot
  (its vault paused and resumed, its agent changed) is queued again for the
  vault as it is now, since that run drops it unclaimed.
- **The queue.** One worker thread a vault takes its queue in order, one
  run at a time, so a folder never has two agents in its directory; at
  most `MAX_RUNS` (2) runs at once on this server (a semaphore). A worker
  forgets itself in the same locked step that finds its queue empty. The
  module lock guards the queues, the workers, the active runs and their
  stop events, and nothing else: no call into `requests`, `store`,
  `catalog` or a driver is made while it is held, since the runner's own
  writes come back to `on_request` on the same thread through `_changed`.
- **`waiting(ws, link)`**: the Sends of the vault's work folder that wait
  (`sent`) and are its agent's to take, the maker's made in Gamma, oldest
  first. **`queue_folder(ws, link_id)`** hands them to the agent through
  the same check → how many it took. It runs when an agent is turned on
  (the panel's question counts them first, `GET /api/local-agents/waiting`),
  after a resume, after the files are chosen, and at startup.

A run (`runner._Run.go`) never raises and always ends the run it recorded:

1. **The claim.** A run stopped while it waited for a slot claims nothing:
   the Send stays `sent` for the run queued after it. The request is read
   again: still `sent`, its vault still `_local_link`'s and the queued one,
   still with an agent Gamma knows, the Send still the agent's (`_ours`),
   and the maker still at least an editor of the
   workspace. Then `requests.claim(ws, conn, id, by_name=<label>,
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
   falls back to a new thread itself). A watchdog thread, one a turn,
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
   now: "Stopped: the agent was turned off for this folder.", "Stopped: the
   work folder was paused.", "Stopped: the work folder is no longer kept on
   this computer.", "Stopped: the work folder changed on this computer." or
   "Stopped: Gamma is closing."
8. **The answers.** The turn ended well and `prompt.answers` keeps some:
   each is written by `gamma.feedback.agent.reply` with a connection of
   the requester (`integrations.Connection(user_id=<maker>,
   workspaces=[ws], scope="feedback", token_id="local:<name>",
   name=<label>, username=<maker's username>)`), so the reply is signed
   with the label and its `agent_id` is `local:codex`, and the thread's row
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
feedback.md's.

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
   [".gamma/feedback/img/<key>.jpg"], comments: [{by, is_owner, agent, at,
   text, deleted?}]}` (`json.dumps(..., ensure_ascii=False, indent=1)`;
   `was` only on an entry that has one, a range of a text file placed
   roughly ([feedback.md](feedback.md) "An entry"); `pictures` the
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

The vault's maker can also send by writing the agent's name in a comment:
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
  page a live file of a work folder whose agent here
  (`runner.folder_agent`) is that one, and the writer that vault's maker.
  The route puts the name on the event (`agent_named`), so the Inbox
  reads the same answer: a member named codex is told nothing of it, nor
  of the agent's later replies in a thread where the maker named it
  (`inbox._agent_of`, by the agent switch as it is now).
- **The Send** (`send(ws, event, name, by_name=)`, after `written`; never
  raises): refused, with a code the client words and an English `why`,
  when the thread is resolved (`closed`: it is reopened first), when an
  edit is not of the thread's newest comment (`not_newest`, since the
  agent would find nothing new in it), when the maker is no owner or
  editor of the workspace now (`viewer`), when the server or the
  workspace takes no writes (`writes`), and for a file-wide mention with
  more than `MAX_THREADS` open threads (`too_many`) or none (`nothing`);
  a FeedbackError of the create is `other`. Else `requests.create` with
  the file's scope (`entries.resolve_scope`), `threads=[the thread]` or
  none for the file's, `note` the body with the agent's mentions taken
  out of its prose (`mentions.without`) and cut to `requests.MAX_NOTE`,
  `via` `session`, and the comment's id as the request's (a retried POST
  writes no comment and so no second Send; an edit's Send gets a new
  id). The runner takes it as any Send of the maker's (`_ours`). With the
  vault paused or the agent not ready, it waits or fails as a Send does.
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
  `seen_at` on (`feedback.agent.reply` and `resolve` take `seen=`, never
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
  reply sets them ("Answered" in feedback.md), and it answers nothing the
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

## The chat's computer side

On a page that is a file of a work folder whose vault the account made,
with an agent on, the chat window has a second side: the header's switch
reads **Gamma AI | Codex**, and on the Codex side the person talks to the
agent on this computer, in the vault's directory. Only the maker ever sees
it; for anyone else the chat is as it is. Where the maker lets it, the
agent may ask on a card to go beyond its confinement ("Asking in the
chat"); else Codex keeps its sandbox and Claude Code its file tools. Its
model, the reply as it is typed, a terminal, the passages' source lines
and Background tasks are below ("Its model, its typing, a terminal, the
sources, Background tasks"); there is no diff or Revert yet
(docs/research/agent-collab.md "In steps").

- **The conversation is not a chat.** Gamma AI's chats are the workspace's
  (every member and a share read them, they travel in backups and exports,
  `read_chats` reads them); the computer's are the maker's alone, in their
  own table of the page's server's pages.db, `computer_chats`
  (`db.COMPUTER_CHATS_SCHEMA`, an additive table: no migration step), read
  and written by `gamma/local_agents/chats.py` only. One row a
  conversation: `id`, `page_id`, `user_id` (the vault's maker, its one
  reader), `agent` (the agent it began with), `title` (its first message's
  first line, 80 characters), `messages` (JSON), `version`, `created_at`,
  `updated_at`, `archived_at` (null for the page's active conversation of
  that account; set: History). It is in `ws_backup.PRIVATE_TABLES` (a
  backup carries none, a restore keeps the live rows), goes with its page
  deleted for good (`ops._purge`), and an account's rows go when its
  membership ends or the account is deleted (`db.delete_workspace_prefs`);
  nothing else reads it. Its paths are relative to the folder.
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
  folder or `"outside"`, `program` a command's program alone (as `_said`),
  `changes` the changed sources (at most 200; `changed` how many),
  `error` an English sentence (`_redact`ed), `fresh` true when a follow-up
  could not resume the agent's session. Every write takes the workspace's
  write lock and sets `version` above every version of the page's
  conversations of that account and never below the clock in
  microseconds (`chats._next_version`: a deleted conversation can take the
  highest away), so a version never repeats and a client polling with the
  one it holds learns of any change, an opened conversation's included.
- **A turn** (`chats.begin`): the person's message and the agent's
  `queued` one written in the page's active conversation (one is started
  when there is none), the turn recorded on this computer
  (`store.add_turn`, vaults.db `agent_turns`), then queued
  (`runner.queue_chat`) in the vault's queue under the key `(ws, "chat",
  turn id)`, beside the Sends' `(ws, "send", request id)`: a turn waits
  behind a Send and shares the `MAX_RUNS` slots, so one directory never has
  two agents. A turn belongs to the computer: no connection holds it, so
  closing the tab or the window stops nothing.
- **`_ChatTurn`** (sharing `_Turning`, the turn plumbing `_Run` uses: the
  snapshot, `events.start`, the watchdog, following the events, the
  changed files):
  1. Checks again what its route checked: the vault still a work folder
     here, not paused or choosing its files, still the conversation's
     account's, its agent still on and the conversation's ("Codex is no
     longer this folder's agent."), the maker still an editor, the agent
     ready (`catalog.probe(fresh=True)`), the server and workspace taking
     writes (`_cannot_write`); else the turn fails with that sentence.
  2. The message goes `running`.
  3. The prompt (`prompt.build_chat`): the message, the page's path in the
     folder and the work folder's name, the selections as `{page, quote,
     source}` (`source` from `feedback.sources.source_for` with the
     selection's box as one rect, null without one), all inside one JSON
     block (the selections are the document's text: data); how to work as
     for a Send (change the sources here; Codex may rebuild in its
     sandbox, Claude Code runs no commands; never `.gamma/`; plain prose,
     `file:line`; ask what is the owner's to decide). No answer schema.
  4. The session: the conversation's in vaults.db `agent_conversations`
     (`conversation_id`, `link_id`, `agent`, `session`, `cwd`, `at`) when
     it is of the same agent and was run in the vault's directory as it is
     now. A new session in a conversation with earlier turns takes their
     last six messages along ("Earlier in this conversation (you may not
     remember it)") and the message reads `fresh`; so does a resume Codex
     answered with another thread, and a Claude Code resume that fails
     before any step, which starts once more afresh.
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
  A pause, relink, removal, the agent turned off or changed, Gamma closing
  and the deadline stop it too, as they stop a Send (it then fails with
  why). `runner.chat_state(ws, turn_id)` → `{turn_id, state: queued |
  running, ahead}` for the strip (`ahead`: another run of the vault, or the
  slots, go first).
- **The vault working.** `runner.at_work(link_ids)` names the vaults whose
  agent works now, a Send or a chat turn, so the vault chip, the links
  and the sync panel see the vault busy: `agent.running` is `{kind:
  "send", request_id, detail}` or `{kind: "chat", request_id: null,
  detail}` (the turn's step in a member's words, `_said`).
- **History.** New chat archives the active conversation; History lists
  the archived ones (newest first, at most 200); opening one makes it the
  active one (the active one archived); deleting one forgets its session.
  New chat and opening are refused (409) while a turn of the active one is
  queued or running.

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
  composer's chip on this side is the agent's (`ModelChip`, the Gamma AI
  chip's look): its models, and its efforts as a section; the choice is
  the conversation's.
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
  conversation has a session of the folder's agent, in its directory as it
  is now, and no turn is to end) asks the shell `continue-in-terminal`
  with the page's id alone; the shell asks its server `GET
  /api/local-agents/chat/terminal?page=` (`{cwd, argv, agent, label}`:
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
  /api/local-agents/chat/sources` `{page, selections}` → `{sources: [{file,
  line, end?} | null]}` (`chats.sources_of`, the lookup the turn's prompt
  uses: `feedback.sources.source_for` over the selection's box; at most 20,
  no process), asked 400 ms after the selections change, kept a minute
  and only until a turn of the conversation ends (the agent may have moved
  the lines); the chip reads `sections/method.tex:3`, or the page number
  without one.
- **Background tasks** lists, for the account, one read-only row per chat
  turn queued or running here (`routers/jobs.py`: `{id: "computer-<turn>",
  kind: "computer-turn", title, params: {page, turn, agent, label, title
  (the page's)}, state: queued | running, progress: {phase: "asking"} while
  it asks, stoppable, stopping, readonly}`); the tray words it "Codex on
  this computer: <page>", "Waiting for you" while it asks, opens the page's
  chat on the computer side on a click (a turn of another workspace opens
  only its page there: the app loads that workspace anew), and its Stop
  calls the turn's stop route (in the turn's workspace) instead of
  `cancelJob`.

Checked against the real CLIs (Codex 0.167.0, Claude Code 2.1.318): both
list their models with no model use, stream a reply in deltas, and take a
chosen model and effort.

## Asking in the chat

An agent of a chat turn may ask the person to let it do what its
confinement keeps from it: Codex to step outside its sandbox (a command
with the network, a write outside the folder), Claude Code to run a command
at all (the build). The person answers on a card in the chat. A Send never
asks (nobody watches it) and keeps refusing; a folder whose maker has not
turned asking on behaves as without it.

- **What it may run** is a vault's second setting, set on the computer
  only: vaults.db `agent_policies` (`link_id`, `runs` `never` or `ask`,
  `set_by`, `set_at`; no row is `never`), through `PATCH
  /api/folder-links/{id}` `{agent_runs}` with the agent's gate and, for
  `ask`, the shell's word ("The routes"); the link's `agent` carries
  `runs`. It goes, with the chat grants, when the agent is turned off or
  changed (`store.set_agent`), the vault relinked or removed, and set back
  to `never`. `_ChatTurn` reads it when the turn starts and starts the
  driver with `ask=True` only for `ask`.
- **The asks.** A driver started with `ask` yields `{type: "ask", id, kind:
  command | files | tool, step, command?, cwd?, paths?, diff?, reason?,
  tool?}` (`id` the driver's key: Codex's JSON-RPC id, Claude Code's
  `request_id`; `step` the tool event it belongs to) and `{type:
  "ask_gone", id}` when the agent withdraws one, and takes the answer
  through `Turn.answer(id, decision, note)` (`once`, `chat`, `deny`;
  thread-safe). While an ask waits, the idle watchdog does not count the
  silence and the run's deadline moves by the wait.
- **The runner** holds each ask for the turn and writes it in the agent's
  message as `asks: [{id (Gamma's own), kind, command?, cwd?, paths?,
  diff?, reason?, tool?, at}]`, its step reading `asked`; the strip reads
  *Waiting for you*. An ask shows the person what would run: the command
  exactly as the agent gave it, its folder and paths relative to the work
  folder or under `~` (rewritten only at a path boundary, so a sibling such
  as `paper-old` is never read as the folder; `_relative`), every other
  path as it is (the message's other words are `_redact`ed); it leaves the
  message once answered (and when a stopped server's turn is failed).
  `POST /api/local-agents/chat/turns/{turn}/asks/{ask}` answers it
  (`runner.answer_chat`): `once`, `chat` (a command only) or `deny` with
  an optional note, the step then reading `allowed` or `declined`.
- **Allowed in this chat** is Gamma's record, not the agent's: vaults.db
  `agent_grants` (the conversation, the command, the folder it runs in, a
  network host, the vault); a later ask of exactly that command (trimmed),
  in the same folder and for the same host, in that conversation and
  vault, is answered `once` by Gamma and never shown; the same command
  elsewhere asks again. Never a prefix, never a rule written into the
  agent's settings or the folder. A file change has no chat grant beyond
  Codex's own `acceptForSession` for the turn.
- **Turned back to *Nothing more*** while a turn runs: the asks waiting are
  declined (the turn goes on), a later ask in it is declined unseen, and no
  grant is written any more.
- **Never asked, declined by Gamma**: a change to a protected part of the
  folder (`.git`, `.codex`, `.claude`, `.vscode`, `.husky`, `.idea`, a
  top-level dotfile such as `.gitconfig`), by either agent; and a Codex
  approval that is not a new command (`writeStdin`, input to a terminal it
  already runs).
- **Unanswered** for `ASK_TIMEOUT_S` (600 s), an ask is declined with "No
  answer within 10 minutes."; a Stop, a pause, the agent turned off and
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

## The drivers

`events.start(name, argv=, cwd=, prompt=, schema=, session=, images=, ask=)`
starts one turn of an agent and gives a `Turn`: `session` (the agent's
session or thread id, "" until known), `pid`, `events()` (blocks; read
once) and `stop()` (thread-safe, returns at once, idempotent). `events()`
gives plain dicts:

| type | fields |
|---|---|
| `session` | `id` |
| `text` | `text`, a whole message the agent wrote |
| `tool` | `id`, `kind` (`read`, `edit`, `run`, `search`, `other`), `title` (one line: `Read sections/intro.tex`, `Run latexmk -pdf main.tex`), `status` (`started`, `completed`, `failed`), `path` (read, edit: relative to the directory with `/` when inside it, else absolute), `command` (run), `output` (a run's last 2000 characters, at its end) |
| `done` | `ok`, `answer` (the parsed answer or None), `text` (the last message), `error` ("" when ok), `stopped`, `usage` |

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
`CLAUDE.md`, its models, its hooks, except that a feedback run gets **no
MCP servers and no web tools**: it needs the folder alone, and with
approvals off nothing would ask before such a tool ran.

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
   known yet, so any such report is this turn's). The thread's
   id is the `session`; a thread whose effective sandbox is not
   `workspaceWrite` ends the turn ("Codex did not take the workspace
   sandbox.").
5. `turn/start {threadId, input: [{type: "text", text: prompt}] +
   [{type: "localImage", path}] (at most 6), outputSchema, sandboxPolicy:
   {type: "workspaceWrite", writableRoots: [cwd], networkAccess: false}}`.
   Items that come before its response belong to the turn.
6. Items (`item/started`, `item/completed`): `commandExecution` → `run`
   (failed unless it completed with exit 0, its `aggregatedOutput` on
   completion); `fileChange` → one `edit` per changed path; `mcpToolCall`
   → `other` ("Use <server>.<tool>"); `webSearch` → `search`;
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
   a `turn/steer` "The owner declined that: <note>". A Stop answers what
   waits `cancel`, then interrupts. `serverRequest/resolved` for an id Gamma
   did not answer withdraws the ask. The other requests stay refused.

### Claude Code

`claude -p --output-format stream-json --verbose --restricted
--permission-mode acceptEdits --permission-prompts none --strict-mcp-config
--tools Read,Edit,Write,Glob,Grep --disallowedTools Bash,WebFetch,WebSearch`,
then `--json-schema <the schema>` and `--resume <session>` when the session
matches `^[A-Za-z0-9_-]{1,128}$` (`claude.command`). The flags are Claude
Code 2.1.318's `--help`; `--tools` and `--disallowedTools` take several
values, so nothing positional follows them.

- **No shell.** Its tools are the file tools alone. A rule such as
  `Bash(latexmk *)` would let it run anything: latexmk takes Perl with `-e`
  and runs any `.latexmkrc` the agent writes. `acceptEdits` accepts edits
  inside the working directory only, and with permission prompts off
  anything that would ask is denied, an edit elsewhere among them.
  `--strict-mcp-config` with no `--mcp-config` starts no MCP server.
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
- **Lines**: `system`/`init` → `session` (`session_id`). `assistant`: every
  block of `message.content` (one message may come split over several
  lines): `tool_use` → a tool started (`Read` → read `file_path`; `Edit`,
  `MultiEdit`, `Write`, `NotebookEdit` → edit; `Bash` → run; `Glob`,
  `Grep` → search; `StructuredOutput`, the tool `--json-schema` answers
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
  with `toolUseID`, `{behavior: "allow", updatedInput: <the input as
  asked>}` (never `updatedPermissions`: no rule is written anywhere) or
  `{behavior: "deny", message: "The owner declined: <note>"}`. A Stop denies
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
  `CLAUDE_CODE_GIT_BASH_PATH`. `NO_COLOR=1`.
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
stop the server). Every run a stopped server left open
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
the server stopped is taken again. A workspace behind on its migrations
(`migrations.is_behind`) is waited for, every `RECOVER_POLL_S` (5 s) and at
most `RECOVER_WAIT_S` (600 s), while the background walk upgrades it; one
not upgraded by then waits for the next Send, pause or start.

## What this computer keeps

vaults.db (`db.VAULTS_SCHEMA`: what this computer knows, never migrated,
additive statements; `gamma/local_agents/store.py`, every function its own
short connection):

| Table | Columns |
|---|---|
| `agent_folders` | `link_id` (the key), `agent` (`codex`, `claude`), `set_by` (the account that turned it on), `set_at`; no row for no agent. The same agent set again keeps its `set_at` |
| `agent_runs` | `id`, `ws`, `link_id`, `request_id`, `agent`, `session` (the agent's session or thread), `state` (`running`, then `done`, `failed` or `stopped`), `started_at`, `ended_at` (null while it runs), `detail` (the request's detail the run ended with: a done or failed run's words, "" for a stopped one), `error` (this computer's record of a failure: the agent's whole error and its stderr tail, never shown to members), `changes` (JSON, the source files changed, at most 200), `built`, `steps` (JSON, at most 200), `pid`; indexed by `(ws, request_id)` and on the open runs |
| `agent_threads` | `link_id` and `thread_id` (the key), `agent`, `session`, `cwd` (the vault's directory the turn ran in), `seen_at` (when the run read the thread), `read_ids` (JSON, the comments it read), `comment_id` (its reply), `request_id`, `at`: the thread an agent of the vault answered last ("Resuming the session") |
| `agent_conversations` | `conversation_id` (the key), `link_id`, `agent`, `session`, `cwd`, `at`: the session a chat conversation's turns resume ("The chat's computer side") |
| `agent_turns` | `id` (the turn), `ws`, `link_id`, `conversation_id`, `page_id`, `agent`, `state` (queued, running, then done, failed or stopped), `started_at`, `ended_at`, `error` (this computer's record), `pid`: a chat turn's run |
| `agent_policies` | `link_id` (the key), `runs` (`never` or `ask`), `set_by`, `set_at`: what the vault's agent may run beyond its confinement; no row is `never` ("Asking in the chat") |
| `agent_grants` | `conversation_id`, `command`, `cwd` (the folder it runs in, `.` the work folder) and `host` (a network host, "" for none), the key; `link_id`, `at`: a command the person allowed in that chat, answered by Gamma when the same command is asked again in the same folder for the same host |

`agent_of`, `agents_of` (one query for a listing), `set_agent(link_id,
agent, by=)` (`""` deletes the row), `start_run`, `update_run` (`session`
and `pid` while it runs), `end_run` (a run that ended stays as it ended),
`open_runs` and `run_of` (the newest run of a request), `answered_thread`
(a thread's row, in place of the one it had), `threads_of(link_id,
thread_ids)` (one query), `conversation_of`, `ran_conversation` (a chat
conversation's session, in place of the one it had), `forget_conversation`
(a conversation deleted), `add_turn`, `turn_running`, `end_turn`,
`get_turn` and `open_turns` (a chat turn's row). What goes with a vault:
`forget_threads(link_id)` (a relink: the threads' and the conversations'
sessions, which belong to the directory that was);
`vault.store.forget_link` (a vault removed: its `agent_folders`,
`agent_threads` and `agent_conversations` rows and its ended
`agent_turns`); `forget_links(live_ids)` (vaults gone while this server
was not looking: the same). A turn still open stays, so the next start
fails it and its message (`recover`). Nothing of this travels:
the workspace's members see the request's state and detail, the replies,
and nothing of the run's row.

## The routes

| Method | Path | What |
|---|---|---|
| PATCH | `/api/folder-links/{id}` | `agent` (`LinkPatch`, at most 32 characters): `"codex"` or `"claude"` turns the work folder's agent on, `""` off; counts toward the PATCH's changes. The gate, in order: the vault's maker (`_made`, 403); `enabled()` (404 "Agents run on the desktop app's own server."); a work folder (400 "Only a work folder can have an agent."); then, turning on or changing only, the shell's word (403 "Only the desktop app may turn on an agent for this folder."), a known name (400 "Gamma knows no agent named …") and `catalog.probe(name, fresh=True)` ready (400 with its problem). `folder_links.set_agent(link_id, agent, by=)` stores it, stops the run of the agent it had when it is turned off or changed (`_stop_agent`), and when one is turned on hands it the Sends that wait now; the answer is the link with `queued`, how many it took. `{paused: false}` and a saved selection (`PUT …/selection`) of a vault with an agent hand it the Sends that wait (`_hand_waiting`); a pause stops its run. A relink and a delete stop the run and turn the agent off (`_forget_agent`). `agent_runs` (`never` or `ask`, "Asking in the chat"): the same gate, then a known value (400 "What an agent may run is never or ask."), an agent on after this PATCH's `agent` (400 "Turn an agent on first."), and for `ask` the shell's word (403 "Only the desktop app may let an agent ask to run commands in this folder."); `never` needs the maker alone. The link's `agent` gains `runs` |
| GET | `/api/folder-links` | each link's `agent`: `{name, label, ready, problem, running: {kind: "send" \| "chat", request_id (null for a chat turn), detail} \| null}` for a work folder with one, else null (`local_agents.info.listing`: one store query for the whole listing, readiness from `catalog.cached`, nothing run). A member who cannot act on the vault (`_acts`) reads `running.detail` as "" (it may name files) |
| GET | `/api/folder-links/page/{page_id}` | the page's `vault` gains the same `agent` (`folder_links.page_vault`), on a server that runs agents only, and `mine` (whether this account made the vault, whose comments may name its agent) on every server. Turning an agent on, off or to another (`set_agent`) tells each open page of the folder `{t: "vault"}` (`_tell_open_pages`, from `collab.open_pages()`), so its chip and the comments' @ menu read it again |
| POST, PATCH | `/api/pages/{id}/comments`, `/api/comments/{id}` | a comment that names the folder's agent is a Send too, answered as `send` ("A mention in a comment") |
| GET | `/api/local-agents/chat?page=&version=` | the chat's computer side ("The chat's computer side"), the vault's maker only: `{computer, conversation}`, `computer` the strip (`{agent, label, ready, problem, models, folder, paused, turn, session}`, `session` whether a terminal can continue the conversation, `ready` and `problem` from `catalog.cached`, `turn` `runner.chat_state` or null) and `conversation` the page's active one (`{id, title, agent, messages, version}`, null for none); with `version` the stored one, `{computer, same: true}` |
| POST | `/api/local-agents/chat/turns` | `{page, text, selections?, id?, model?, effort?}` → 202 `{conversation, turn: {id, state}}`; 400 for no text, past 20,000 characters, a malformed `id`, or a model or effort the agent did not list; 409 while a turn of the conversation is queued or running, for a folder with no agent, paused or choosing its files, and for an agent known not to be ready (its problem); 403 when the maker is no editor or writes are refused. A repeated `id` (the person's message's) answers the turn it made |
| POST | `/api/local-agents/chat/turns/{id}/stop` | `{stopped}` (`runner.stop_chat`) |
| POST | `/api/local-agents/chat/new` | `{page}` → `{conversation: null}`; 409 while a turn runs |
| GET | `/api/local-agents/chat/history?page=` | `{conversations: [{id, title, agent, updated_at, turns}]}`, newest first |
| POST, DELETE | `/api/local-agents/chat/history/{id}/open`, `/api/local-agents/chat/history/{id}` | open (→ `{conversation}`, 409 while a turn runs, 404 for one not archived) or delete (→ `{deleted: true}`, 409 for the active one) an archived conversation |
| POST | `/api/local-agents/chat/turns/{turn}/asks/{ask}` | `{decision: "once" \| "chat" \| "deny", note?}` (a note at most 2,000 characters) → `{answered: true}`: the person's answer to a waiting ask ("Asking in the chat"); 400 for another decision, a longer note or `chat` on anything but a command; 404 for no such turn of the account or ask; 409 when it was answered, withdrawn or declined already, or the turn ended |
| GET | `/api/local-agents/chat/terminal?page=` | the desktop shell's alone in practice (Continue in a terminal): `{cwd, argv, agent, label}`, the conversation's session resumed ("Its model, its typing, a terminal…"); 404 "This conversation has no session to continue.", 409 while a turn runs, when the folder has no agent or it is not on this computer |
| POST | `/api/local-agents/chat/sources` | `{page, selections: [{text, page, box}]}` → `{sources: [{file, line, end?} \| null]}`, in order; 400 past 20 selections |
| GET | `/api/local-agents?fresh=0\|1` | `{agents: [{name, label, found, version, ready, problem, models, at}]}` (`models`: "Its model, its typing…"), Codex then Claude Code, from `catalog.cached`; `fresh=1` probes both anew, at most once every 10 s on this server (sooner, the kept answers). A signed-in account's session (`require_personal_user_id`; no token, no guest); 404 when not `enabled()` (`gamma/routers/local_agents.py`, registered beside the feedback router) |
| GET | `/api/local-agents/waiting?link=` | `{waiting: n}`: how many Sends of the vault's work folder an agent turned on now would take (`runner.waiting`, the rule `queue_folder` takes them by), for the panel's question; the vault's maker only (404 otherwise, and for no such work folder) |
| GET | `/api/feedback?scope=` | on a server that runs agents the JSON gains `agent` and `agents_found` (feedback.md "The routes"); the files' document never does |
| GET | `/api/feedback/requests`, `/api/feedback/requests/{id}` | on a server that runs agents each request record gains `local`: whether a run on this computer holds it now (`runner.running`), so the dialog's Withdraw can say it stops the agent |
| POST | `/api/feedback/requests` | a token's Send to a work folder whose vault here has an agent, paused or not, is 403 "An agent on this computer takes Sends made in Gamma, not with a token." A Send is stamped `via` `session` or `token` either way, and a token's is never the agent's, so one made before the folder had an agent waits for an outside agent too |
| POST | `/api/feedback/requests/{id}/state` | a token's change while a run of the request is active here is 409 "Codex on this computer is answering this request." |

The chat routes answer 404 on a server that runs no agents and for any page
that is not a live file of a work folder whose vault here the account
made (so nobody else learns it exists), 403 for a token or the guest
(`require_personal_user_id`).

`doc.agent` (`routers/feedback._agent_of`): null when the work folder has
no agent here; else `{name, label, ready, problem, takes, why}`, `ready`
and `problem` from `catalog.cached`, `takes` whether a Send of this account
would reach it now, and `why` (English) when not: "Only the person who
keeps this folder on this computer can send to Codex." or "The work folder
is paused on this computer: Codex starts once it syncs again." (paused or
choosing its files). `doc.agents_found`: whether `catalog.cached` finds
either agent.

## The client

- **The Feedback dialog** (`frontend/src/feedback/`) reads `doc.agent` and
  `doc.agents_found` from its 5 s poll. `feedbackView.sendAgent(doc,
  kept)` gives the mode: `ready` and `not_ready` (the folder's agent takes
  this person's Send), `checking` (`ready` null), `other` (an agent that
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
  says how to turn one on in the sync panel. The lines are a live region
  (`role="status"`, kept mounted while empty) that the Send button names
  as its description, so a change a poll brings is heard, and so is why
  the button is off.
- **Requests.** `requestLine` gives a short state, naming the agent by the
  request's `state_by`, whoever set it, a token's agent included (*Codex is
  working*, *Done by Codex*, *Failed*), and apart from it the agent's own
  words (its progress, its summary, the reason it failed), which the row
  shows on a line of their own under the note, at most three lines, whole
  in its title. The Withdraw button's title (`withdrawTitle`) says "Stop
  Codex and withdraw: what it changed already stays in the files." only on
  a running request a run on this computer holds (`local`); nothing here
  stops an outside agent.
- **Comments** (`comments/comments.js`): `authorTitle` of a comment with
  `agent_kind: "local"` reads "Written by Codex on tim's computer";
  `sentChip` of a running Send with `state_by` reads *Codex is working*.
- **The mention.** App derives the agent the open page's comments can
  name, `vaultState.mentionAgent(vault.state)`: `{name, label, ready,
  problem}` for the maker of the page's work-folder vault with an agent
  on, in the desktop app, else null. It reaches every comment field as
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
- **The chat's computer side** (`frontend/src/chat/computer/`): App derives
  `vaultState.computerAgent(vault.state)` (`mentionAgent` with `running`,
  the folder's name and whether it is paused or choosing its files; the
  desktop app only, as `vault.state` is) and passes it to ChatDock as
  `computer`. ChatDock draws the switch (`ChatSides`, `Segmented`: *Gamma
  AI | Codex*) first in its header, outside the Gamma AI gate, so an
  account with no AI provider still gets the computer side; the side is
  remembered per page for the tab's life (`useChatSide`). On the computer
  side ChatDock renders the same window with the side's History and New
  chat (`ComputerHeader`) and its body (`ComputerChat`): the strip in the
  provider health strip's slot, neutral, amber when the agent cannot run
  (`computerChat.stripLine`: "Codex on this computer · paper", "Codex is
  working: Editing sections/intro.tex", "Waiting for Codex to finish its
  other work in this folder", "Starting Codex…", the agent's problem, the
  paused folder), the transcript (the person's messages with their
  passages' pages; the agent's with its steps in one pill that unfolds,
  its reply through `ChatMarkdown`, "Changed a.tex and 1 more · The PDF
  was rebuilt", how a turn ended, the fresh-session line), and a composer
  with the PDF passages selected (App's `pdfSelections`, Gamma AI's too,
  cleared once the server accepts the turn), Send, and Stop in its place
  while a turn is queued or running. `useComputerChat` reads the
  conversation on opening the side, on focus, after each action and every
  1.5 s while a turn is to end, with `version` so an unchanged one answers
  `same`. Every word is the client's (`computerChat.js`, through `t()`);
  the server sends structured steps. The side's steps never carry a
  `page_id` or `block_id` and never go through `/api/ai/*`, `chatSession`,
  `MentionContext` or Gamma's model list. While a live turn has `asks`,
  each is a card under the steps (`ComputerAsk`, the approval card's look
  but not its component: no *Always allow*, no Gamma permission): "Codex
  wants to run a command" with the command whole on a code line, where it
  runs and its reason, or "Codex wants to change files outside its
  sandbox" with the paths and the diff; **Allow once**, **Allow in this
  chat** (a command only: "Run this exact command without asking for the
  rest of this conversation") and **Decline…** (a note, then Decline);
  under them "It runs as you, with your files and the network." or "It
  changes files outside the work folder."; the strip reads "Waiting for
  you: Codex wants to run a command" (amber, `asking`), the turn's pill
  "Codex is waiting for you", and the steps "Asked to run latexmk",
  "Allowed to…", "Not allowed to…". `useComputerChat.answer` posts the
  answer and reads the conversation again.
- **The vault chip's card** (`vault/VaultChip.jsx`, `vaultState.agentLine`):
  "Agent: Codex on this computer", the problem when it is not ready, and
  "Codex is working: <detail>" during a run. Opening the card reads the
  page's standing again (App's `vault.refresh`), since a run's start and
  end tell the page's room nothing.
- **The Inbox** is unchanged.

## The desktop app

The sync panel's vault view carries the agent row
([desktop architecture](../../desktop/docs/architecture.md) "The sync
panel"). `refreshKeeping` also reads `GET /api/local-agents` from each
local server (5 s timeout; a 404 or no answer is a server that offers no
agent), and `lib/keeping.js` `vault()` gives the item `mine`, `agent`,
`agentLabel`, `agentReady`, `agentProblem`, `agentRunning`, `agentChat`
(the run is a chat turn: `running.kind` "chat") and, for a work
folder, `agents` (what can be chosen, `agentChoices`). While a run works the
row reads busy, *Codex is working · Editing main.tex*, its title saying it
answers a Send or works in the chat of a file of the folder, and the title bar's
summary *Agent working* when no round runs anywhere; a vault's own problem
(stopped, held, its directory gone, a sync error) keeps its row, the run
said in its title. Opening a work
folder's view as its maker asks `?fresh=1` (`check-agents`, main.js
`checkAgents`). The row offers Off | Codex | Claude Code, an agent not found
or not ready shut with its problem shown under it. Turning one on asks
first (`keeping.agentAsk`: "Let Codex work in <directory>?", with what it
may do and how many waiting Sends will be handed to it, counted by the
server, `GET /api/local-agents/waiting`), then `PATCH {agent}` with the
shell's word while the bar reads "Checking Codex…", and a notice says what
came of it (`keeping.agentSaid`). Turning it off asks nothing.

With an agent on, the row ends in **What it may run**: *Nothing more* |
*Ask me in the chat* (`vault()`'s `agentRuns`, from the link's
`agent.runs`), and a line saying what each means for that agent ("Codex
runs commands in its sandbox, without network; with Ask, it can ask in the
chat to go further…", "Claude Code edits the files here and runs no
commands; with Ask, it can ask in the chat to run one (your build)."). Ask
confirms first (`keeping.runsAsk`: "Let Codex ask to run more in
<directory>?", what it may then ask for, that nothing runs until allowed in
the chat, and that an allowed command runs as the person, with their files
and the network), then `PATCH {agent_runs: "ask"}` with the shell's word
(main.js `setAgentRuns`, action `agent-runs`); *Nothing more* PATCHes
`never` and asks nothing; a notice says what came of it (`keeping.runsSaid`).

## Trust

- **Who starts a run.** Only the vault's maker, by a Send made in Gamma
  (from the dialog, or a mention in their own comment, never through a
  share) while still at least an editor of the workspace. A mention in
  anyone else's comment, a token's or the agent's own reply starts
  nothing. Another member's Send is
  left for an agent they run; a token's Send to such a folder is refused,
  and a token's Send made before the folder had an agent is never taken
  (`via`), so a mirror or a script holding the maker's write token cannot
  start one, and a token cannot move a request a run holds. Turning an
  agent on takes the desktop app's own word on the app's server; a page of
  Gamma cannot ("Who turns it on").
- **What each agent may touch.** Codex: the vault's directory, written in
  its `workspace-write` sandbox, network off, approvals refused, no MCP
  server and no web search; its commands run, the build among them. It
  **reads** whatever the account can read: the sandbox confines writes and
  the network, not reads. Claude Code: the file tools inside the directory
  (`--restricted`), edits accepted there only, no write to a settings, git
  or tool-configuration file, no shell, no MCP server, no web tools, no
  settings file of the folder's. Neither holds a Gamma credential:
  its environment has none of the server's variables, it is handed the
  comments and answers in its final message, and Gamma writes the replies.
  Nothing is started with a bypass flag.
- **What asking opens** ("Asking in the chat"). Only where the maker turned
  it on at the computer, only in the chat (never a Send), only what the
  person allows on a card, one exact command at a time: an allowed command
  runs as the person, with their files and the network (Codex outside its
  sandbox, Claude Code with no sandbox at all), which is what the card and
  the panel's question say. No answer is ever a rule: no prefix, no
  *always*, nothing written into the agent's settings or the folder. What
  is left: Claude Code may write a `.latexmkrc` (not a protected file)
  without asking, and a `latexmk` the person then allows runs it, Perl
  included; a grant of `latexmk` in this chat lets the next turn do so
  unasked. The card shows the command exactly as given for that reason,
  and a chat grant holds for one command in one folder only.
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
  and does not build, and Codex's requests for approval are refused. The
  plan's *Ask on this computer* (a card on the computer's own screen) is
  not built; neither is taking back a chat grant (New chat starts with
  none). On Windows a Codex command's card shows the shell it runs through
  (`powershell.exe -Command '…'`).
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
- Comments on another server (the NAS, a clone) never reach this computer's
  agent, as they never reach its files (feedback.md "Known gaps"). That
  server now sees the computer ([computers.md](computers.md): its channel
  and presence, step 4a of the research note); its work folders, Sends,
  mentions and chat over that channel are the next steps.
- The chat's computer side: Claude Code builds only where the folder lets
  it ask; no diff or Revert of what it changed (Gamma writes no file the
  disk made: a decision first), no *Attach what I see*, no *Open comments
  on this page*; a page's chat only (no folder's); the desktop app only,
  as the @ row. The window polls while a turn is to end (no push), so the
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
  of its own each, a log of every argv, request and stdin). Tests
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
  answers), every step one of `events.KINDS`, a turn closed unread.
- `tests/test_local_agents_runner.py`: the whole loop on the sample paper
  as a work folder (replies signed Codex with `agent_kind` local, the fixed
  ones resolved, `done` with the summary and the changed files, progress
  with no path outside the folder and no untracked file named), another
  member's Send not taken, a token's refused and one made before the agent
  never taken, a withdraw during the probe, the turn and the answers, a
  token's state change refused while running, not ready, the timeout,
  nothing pending, more than 100, no reply written, a run stopped while it
  waited for a slot taken again, an error before the turn is read, a
  request that cannot be written left for the next start, recovery (what
  waits taken, a workspace behind on its migrations waited for), turning
  on, pause and unpause; the words (tracked names, wrapped commands,
  redaction, a walk cut short), the store and the prompt on their own.
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
  and its open pages hearing the switch.
- `tests/test_local_agents_extras.py`: the probe listing the models, a
  choice one the agent listed, a turn running the model and effort asked
  for (Codex's thread and turn, Claude Code's flags), both drivers
  streaming a chat turn's text, the reply growing then replaced by the
  whole message, the terminal route for Codex and Claude Code (and the
  strip's `session` agreeing with it), the sources of the selections, the
  Background tasks rows (running and asking, queued, gone after a Stop).
- `tests/test_local_agents_asks.py`: asking in the chat: the policy is the
  computer's word (the PATCH's gate, the default, the listing's `runs`),
  consent forgotten with the agent, the directory and the vault, both
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
  Claude Code's ask turn starting each Bash call in the folder.
- `tests/test_local_agents_chat.py`: the chat's computer side: only the
  vault's maker (a co-author, a page outside a work folder, another
  server: 404; a token and the guest: 403), a turn run and its reply kept
  (text, steps, changes), a follow-up resuming the session, Codex not
  resuming and saying so, Claude Code started afresh, a reply that loads
  nothing by itself, Stop of a running, a queued and a slot-waiting turn
  and what waits for one (New chat, open, a second message), a version
  never another conversation's, a turn behind a Send, a pause, the strip
  and the refusals, an agent changed meanwhile, recovery (a turn whose
  vault was removed too), the vault forgotten, the transcript in no
  backup and gone with its page, the prompt, what a message keeps;
  `tests/test_workspaces.py` a member who goes taking their conversations.
- `tests/test_local_agents_routes.py`: the PATCH's gate in order, the links
  and a page's standing carrying `agent` without running a process,
  `GET /api/local-agents`, the waiting count, `doc.agent`, a request's
  `local`, a pause, a relink and a removal.
- `tests/test_migrations.py` (`test_v58_*`): `via`.
- `tests/test_feedback_requests.py` (`claim`, `only_from`,
  `change_listeners`), `tests/test_comments.py` (`agent_kind`, `state_by`),
  `tests/test_feedback_entries.py` (the local comment's Markdown, the
  pinned output unchanged).
- Frontend: `tests/feedbackView.test.mjs`, `tests/feedbackText.test.mjs`,
  `tests/comments.test.mjs` (the hint, the refusal's words),
  `tests/vaultState.test.mjs` (`mentionAgent`, `computerAgent`),
  `tests/mentions.test.mjs` (the agent's row, a member of its name, its
  text and chip), `tests/computerChat.test.mjs` (the side's requests, the
  words of its steps, pill, changes, endings and strip, when a message may
  go). Desktop:
  `desktop/test/keeping.test.js` (the item's fields, the running row, a
  problem kept beside a run, *Agent working*, the question) and
  `desktop/test/main.test.js` (the PATCH carries `X-Gamma-Shell`, the
  count asked of the server).
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
  page's own thread sending the file's open comments; the chat's Codex
  side (the switch, the strip, a message with a PDF passage worked and
  answered, the prompt carrying both), a tab closed mid-turn finding the
  turn again in a new one and the follow-up resuming Codex's session, Stop,
  New chat and History; asking turned on for the folder, a command Codex
  asks to run waiting on a card (the strip, the command whole, its reason)
  until allowed once, declined with a note (Codex's steer), allowed in this
  chat and the same command answered unasked in the next turn; the passage
  chip's source line, the strip's menu asking the shell (the asks
  recorded, no shell in the browser suite), a model and effort chosen in
  the chip reaching Codex, the reply seen part-way as it is typed, a
  running turn's Background tasks row and its Stop; turning it off.
