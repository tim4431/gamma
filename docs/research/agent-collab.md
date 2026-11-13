# Coding agents on the author's computer: `@codex fix this`

Surveyed 2026-11-10, revised 2026-11-13. The question: the author reads a
manuscript's compiled PDF in Gamma, marks what is wrong, and says
`fix these` to Codex in the chat or writes `@codex fix this only` in one
thread. How does that reach Codex or Claude Code running on the author's
own computer, in the LaTeX project's directory, and how does the answer
come back? Status: **step 1 is built** ([dev/local_agents.md](../dev/local_agents.md),
"As built (step 1)" below); steps 2 to 4 are plans. Part 1 is what exists
(in Gamma, in the two CLIs, in the neutral protocols, in other products),
part 2 what was run on the development machine, part 3 the design and its
steps, with what is still undecided at the end.

Between the first survey and the revision, steps 0 to 3 of
[file-explorer.md](file-explorer.md) were built: a synced vault that
pushes everything is a *work folder*, every open comment thread on its
files is *feedback* with its place, quote, SyncTeX source line and
picture, and *Send* records a request with states an agent sets
([dev/feedback.md](../dev/feedback.md)). That work left one seam on
purpose: "Gamma starts no process". This design fills it, so most of
what the first version proposed to build (anchors, source lines, the
prompt's material, a run's record in the workspace) already existed.

## The answer in short

The shape is VS Code Remote's. A work folder already says which computer
has a page's files: the desktop app's own Gamma server keeps it, on the
computer where the directory is. That server is the computer's side. It
starts Codex or Claude Code in the directory when the person sends
feedback (step 1), when a comment names the agent (step 2), or from the
chat window's second side (step 3). Later it tells a remote server it is
there, so any window of the vault's maker can reach it (step 4):

```text
any Gamma window ── Gamma server ══ Gamma on the PC ── Codex / Claude Code
  feedback, threads,   knows which    keeps the work      edit the sources
  the chat's PC side   computer is on folder, runs them   and rebuild
        ▲                                   │
        └───── new PDF ◄── the vault's push ◄┘
```

- **The server to the PC** is one channel the PC opens, as a VS Code
  tunnel's machine dials out. The server never reaches into a computer,
  and a window talks to its own server only.
- **The PC to the agent** is a driver with four verbs (open or resume a
  session in a directory, prompt, read events, stop). Codex and Claude
  Code each get a native driver over the CLI already installed (`codex
  app-server`, `claude -p`); one more driver speaks the Agent Client
  Protocol for every agent that has it natively.
- **The unit of work is the feedback request.** `fix these` is a Send of
  a file's or a folder's open threads with an instruction; `@codex fix
  this only` is a Send of one thread. The agent's answers are replies
  signed with its name, its progress is the request's state, and the
  person accepts by reading the rebuilt PDF or reopens.
- **The chat window is the second surface.** It gains a second side, the
  computer's, drawn with the steps, approval cards and Stop it already
  has. A selected passage and "fix this" is a turn there.
- **The way back** is already built: the agent rebuilds, the vault pushes
  the new PDF, and the highlights and comments are found again by their
  text.

"Connect to a local session and tell it to do things" turns out to be the
wrong default. Neither CLI has a portable, supported way to put a prompt
into the session open in a terminal, and where one exists it is a preview
or undocumented (below). The clean direction is the other one: Gamma's
conversation is a resumable agent session, and that session is the
author's too — `codex resume <id>` or `claude --resume <id>` opens the
very conversation in a terminal. Delivery into an open session stays an
opt-in extra per vendor.

An agent run from the terminal can already act on the same feedback
through the Gamma plugin: [What works without the host](#what-works-without-the-host).

## Part 1: what exists

### In Gamma

| Piece | What it gives this | Gap |
|---|---|---|
| Work folders ([work_folders.md](../dev/work_folders.md)) | the directory the agent works in: a vault of this computer that pushes everything, its picked files pages at their paths, a rebuilt PDF replacing the page's | only the desktop app's own server keeps one; a work folder of another server is "Later" in its plan |
| Feedback ([feedback.md](../dev/feedback.md)) | the agent's whole context: every open thread on a work folder's files as an entry with its file, place, quote, source line (an in-house SyncTeX reader, recorded at the thread's open), region pictures and comments, in JSON and Markdown, also as `.gamma/feedback/` files in the directory; the version a thread was made on kept until it is resolved | Gamma started no process: "The seam" left the connector to the owner |
| Send and its requests ([feedback.md](../dev/feedback.md#requests-and-their-states)) | the person's "go": a request naming the threads and a line of instruction, with the states `sent`, `running`, `done`, `failed` and a detail, shown in the Feedback dialog and on each thread's chip; Withdraw | an agent set a state through a token only |
| Comments written by an agent | a reply and a resolve stamped with the agent's name (`agent`, `agent_id`), reaching the person's Inbox, making no one follow the page; the `feedback` token scope, which answers and does nothing else | written through a token |
| `@name` in a comment, `comments.after_write` → `inbox.from_comment` | the trigger's place for step 2: the event carries the body and, for an edit, the text before | a name is a chip only when a member has it ([mentions.md](../dev/mentions.md)); an agent is no member |
| Who may comment | owners, editors, **viewers and signed-in share visitors** | so a comment is not trusted text: see [Whose words run](#whose-words-run-and-what-a-server-can-do) |
| Highlights after a replacement ([highlight_relocation.md](../dev/highlight_relocation.md)) | after a fix, a highlight moves with its text; one whose text was rewritten is drawn dashed or hollow | a fixed typo reads as a rough match or a lost highlight, which here means "done" |
| The desktop app's local server | a Python process on the PC that keeps the work folder, beside the directory | it started no agent |
| The MCP endpoint ([mcp.md](../dev/mcp.md)) | a connected Codex or Claude Code reads the library, and `list_feedback` / `read_feedback`; on a `feedback` connection `reply_feedback` / `resolve_feedback` | the agent is started by the person, at a terminal |
| The chat window ([ai.md](../dev/ai.md)): the steps pill and its chips, `ApprovalCard`, `PlanCard`, the changes card, Stop, a conversation per page and per folder | everything a turn of an agent needs drawn | a reply stops with its tab, and the stream is Gamma's own tool loop |
| A selected PDF passage in the composer (`pdfSelections`: text, page, box) | the anchor of "fix this", with the position SyncTeX needs | |
| The paper-fetch card ([ai_tools.md](../dev/ai_tools.md)) | the precedent: a reply that waits on something on the user's computer (the Connector) and says what it is doing there | |
| Background tasks | the row for work that outlives a tab | server jobs and one tab's work only |

One earlier decision is close to this and is not this one.
[textfold.md](textfold.md), a study of the TextFold reader, turned down
using `claude -p` and `codex exec` as the model behind Gamma's own chat:
on a server the CLI would run under the operator's login for every
account, and [ai.md](../dev/ai.md) declines routing a chat's requests
through a subscription's credentials. Here the CLI runs on the author's
computer, under the author's login, on the author's files, as an agent
with its own tools; Gamma's server neither runs it nor sees its
credentials.

### How a program hands Codex work

Codex CLI 0.167.0; the docs moved from developers.openai.com/codex to
learn.chatgpt.com/docs. *Run here* means run on the development machine
(part 2); the rest is from the docs.

| Mechanism | Reaches | State | Notes |
|---|---|---|---|
| `codex exec --json -C DIR -s workspace-write "…"`, `codex exec resume ID "…"` | a new or resumed thread | stable | JSONL events on stdout: `thread.started` (the thread id), `turn.*`, `item.*` (`agent_message`, `command_execution`, `file_change`, …). `-o FILE` and `--output-schema FILE` give the last message, shaped. No channel for approvals: the sandbox decides. `--full-auto` is gone; `--approve-for-me` routes approvals to an automatic review. Run here |
| `codex app-server` over stdio | a new or resumed thread, with approvals | labelled experimental; what the VS Code extension and the desktop app use | JSON-RPC, one message a line: `initialize`, `thread/start {cwd, sandbox, approvalPolicy}`, `thread/resume`, `thread/list`, `turn/start`, `turn/steer`, `turn/interrupt`; the server asks back with `item/commandExecution/requestApproval` and `item/fileChange/requestApproval`. `generate-json-schema` writes the protocol of the installed version: 106 client requests, 10 server requests, 86 notifications at 0.167.0. Run here |
| `codex queue --thread ID --message "…" [--resume]` | **an existing thread** | in the CLI's help; not in the docs read | stores the message with the thread; it runs as a turn when the thread is next live. Run here against a thread resumed by a second process, not against an open terminal |
| The shared daemon (`codex app-server daemon`, since 0.157 the terminal UI attaches to it by default) | the thread open in the terminal UI | undocumented for third parties | a client on its control socket sees the same process as the open session, so `turn/start` lands in it (a user's report, issue 32445). The socket speaks WebSocket; `--no-daemon`, a `-c` override or an elevated terminal make the UI embed its own server, and a second writer on a thread is then refused (-32600) |
| `@openai/codex-sdk` (TypeScript), `openai-codex` (Python) | a new or resumed thread | stable | wrappers: the first spawns the CLI and reads its JSONL, the second drives the app-server |
| `codex://threads/ID`, `codex://new?prompt=…&path=…` | the desktop app's composer | supported | fills the prompt in; the person sends it |
| Hooks (`PermissionRequest`, `Stop`, …), `notify` | a run's approvals and its end, from outside | stable | a hook must be trusted by hash first |
| `codex mcp-server` | | **removed** (deprecated 0.149.1) | absent from 0.167.0's commands |
| ACP | | adapter only | `@agentclientprotocol/codex-acp`, built on the app-server |

### How a program hands Claude Code work

Claude Code 2.1.318; docs at code.claude.com/docs.

| Mechanism | Reaches | State | Notes |
|---|---|---|---|
| `claude -p "…" --output-format stream-json --verbose`, `--resume ID` | a new or resumed session, in its own process | GA | NDJSON: `system/init` (the session id), assistant and user messages with each tool call's input, a final `result` with the cost. Permissions: `--permission-mode`, `--allowedTools`, `--permission-prompts none` (deny what would ask) or `--permission-prompt-tool` (an MCP tool of the caller answers). `--json-schema` shapes the answer. Loads the project's CLAUDE.md, skills and hooks with no trust dialog. Run here |
| Claude Agent SDK (`query()`, `canUseTool`) | the same, as a library | GA | bundles the binary; its docs tell third-party products to use API keys (below) |
| Channels | **the session open in a terminal** | research preview | an MCP server the session starts pushes `<channel>` events into it, can take a reply through a tool and relay permission prompts. The session must be started with `--channels` (allowlisted plugins) or `--dangerously-load-development-channels`, confirmed at every launch; ignored under `-p` and in the SDK |
| The cross-session inbox (`SendMessage` between sessions) | any running session | GA between Claude sessions | each session binds a socket, a named pipe on Windows, and says so in `init` as `messaging_socket_path` (seen here). The docs say a script may post to it, and document only the auth line, not a message |
| Background sessions (`claude --bg`, `claude agents --json`, `attach`) | a session moved to the background | research preview | scriptable listing with what each waits for |
| Remote Control, `--cloud`, teleport | | Anthropic's own clients, or cloud sessions | no third-party protocol; a cloud session does not see local files |
| The IDE lock file (`~/.claude/ide/*.lock`) | | | control runs from the CLI to the editor. `vscode://anthropic.claude-code/open?prompt=…` fills a prompt in without sending it |
| Hooks: `PermissionRequest` as `http`, `Stop`, `asyncRewake` | a session's approvals and progress, from outside; a queue drained between turns | GA | the one way to answer the prompts of a session someone else started |
| `claude mcp serve` | | | exposes the tools, no agent loop |
| ACP | | adapter only | `@agentclientprotocol/claude-agent-acp`, built on the Agent SDK |

**Terms.** Anthropic's docs: developers may not "route requests through
Free, Pro, Max, or Ultra plan credentials on behalf of their users", which
does not prevent "an end user from signing in to the unmodified Claude
Code binary with their own Claude subscription"; products built on the
Agent SDK are told to use API keys. A runner that starts the installed,
unmodified `claude` under the login it already has is the closest to
the case the docs permit, and a runner that embeds the SDK the closest
to the one they forbid; they settle neither. OpenAI allows plan usage
through "Sign in with ChatGPT"
for "open-source projects, personal projects that run locally", and its
own `codex-plugin-cc` drives the local install. Neither vendor says
outright that a local program may drive the user's own signed-in CLI:
worth one question to each before Gamma Cloud advertises it.

### Neutral protocols

- **Agent Client Protocol** (Zed and JetBrains; agentclientprotocol.com).
  JSON-RPC over stdio between a client and a coding agent: `initialize`,
  `session/new {cwd, mcpServers}`, `session/resume`, `session/list`,
  `session/prompt` with text, resource links and images, streamed
  `session/update`s (message chunks, tool calls with diffs and file
  locations, a plan), `session/request_permission`, `session/cancel`.
  Native in Gemini CLI (`gemini --acp`), Copilot CLI (`copilot --acp`),
  Cursor (`agent acp`), OpenCode, Goose and a dozen more; Claude Code and
  Codex only through the adapters above, both Node packages. SDKs: Rust
  and TypeScript 1.0 since June 2026, Python 1.0.0 since 2026-11-09. A
  v2 draft (July 2026) reshapes tool calls and plans and removes the
  client's file and terminal methods; the remote transport is still an
  RFD. Oleafly is listed among its clients.
- **A2A** is for services that are agents talking to each other. It has
  no working directory, diff or permission model, and neither CLI
  implements it.
- **MCP** cannot hand an agent work. A server has no way to start a turn
  in its client: Sampling is deprecated in the 2026-07-28 revision and
  Tasks track what the client asked for. What is left is the agent
  polling an inbox tool, which costs tokens and wakes nothing that is
  idle. MCP's place here is the read side an agent already has.

### How other products do it

- **Linear's agent platform** is the model for the interaction. An agent
  is an app user: installed by an admin, listed with people in the
  mention menu, a *delegate* on an issue while a person stays its owner.
  A mention makes an **agent session**; the agent reports typed
  **activities** (thought, action, question, response, error), the
  session's state follows from the last one, a *stop* signal ends it,
  and the agent must show something within ten seconds.
- **GitHub.** `@claude` (claude-code-action) answers in **one comment it
  keeps editing** while it works, only for people with write access,
  never for a bot. `@copilot` reacts with eyes, then links its session.
  The self-hosted Actions runner is the transport to copy: it registers
  with a token, opens outbound HTTPS only, takes the jobs its labels
  match, and a job nobody takes fails visibly after a day.
- **Feedback on a rendered page turned into a source edit.** The VS Code
  extension *PDF Review for Coding Agents* writes one line per comment:
  an id, the page, the area, `file.tex:line` from SyncTeX, the quote,
  the comment; the agent cites the ids in its answer, and a note whose
  quote is gone after the rebuild is taken as addressed. Lattice (a
  local LaTeX app) runs one read-only agent task per annotation and
  writes nothing until the author accepts. A pull request to Orca adds
  the SyncTeX range per annotation and warns when a source changed
  after the PDF was built. The web equivalents (Agentation, react-grab)
  hand over a selector, the component's file and line and a bounding box.
  The common shape: **a stable id, the page, the quote, the source line
  with a staleness check, the comment**.
- **Claude in Slack** warns that the agent may follow instructions found
  in the thread, which is the risk below.

## Part 2: run on the development machine

Windows 11, codex-cli 0.167.0 (its daemon 0.167.1 running), Claude Code
2.1.318, TeX Live 2024. A three-file article (`main.tex` and two
`\input` sections with planted typos) built with `latexmk -pdf
-synctex=1`. Each turn was one small request on the developer's own
logins.

**SyncTeX finds the line.** Ten phrases were found on the page with
PyMuPDF and asked for with `synctex edit -o "page:x:y:main.pdf"` at the
centre of the phrase's first rectangle, in points from the page's top
left:

- all ten came back with the right file and line:
  body text in both sections, the abstract, a table cell, a caption, and
  text a macro printed (`\sys{}` came back as the line that calls it);
- a display equation came back two lines low, at its `\end{equation}`.
  Its text layer reads `F = π R 1 −R,`, which no search of the sources
  finds, so there the position is the only pointer;
- the column is always −1: the answer is a line, never a word;
- 14 ms a lookup (median).

A highlight's stored rects are in a `width` × `height` frame from the
top left ([`highlights.position`](../../backend/gamma/highlights.py)), so
a rect's centre scaled by the page's size in points is the `x:y` to ask
with.

**`claude -p` edits and resumes.** `claude -p "…fix 'usefull' on line
2…" --output-format stream-json --verbose --permission-mode acceptEdits
--allowedTools "Read,Edit" --permission-prompts none --strict-mcp-config
--model haiku`: a Read and an Edit whose input carries the file, the old
string and the new one, then `result` with `total_cost_usd` 0.0045, in
3.6 s of the agent's time. Without `< /dev/null` it waits three seconds
for stdin first. `--resume <session id>` with a follow-up fixed the next
typo and said what the first request had been (4.6 s, 0.0057).

**`codex exec` edits and resumes.** `codex exec --json
--skip-git-repo-check --sandbox workspace-write -C . "…"`: 40 s the
first time, 64,066 input tokens of which 42,240 cached. The run starts
the MCP servers the user's config names; one that needed a sign-in
logged an error and the run went on. `codex exec resume <thread id>`
answered from the same thread in 6.7 s. **The edit was made by two
PowerShell commands, not a patch: the stream held no `file_change`
item.** A caller that wants to show what changed cannot read it from
the events.

**The app-server relays approvals.** A sixty-line standard-library
client: `initialize` answered in 0.4 s, `thread/list` returned the
machine's threads from both the terminal UI and the VS Code extension
with each one's `cwd`, `thread/start {cwd, approvalPolicy: "untrusted",
sandbox: "read-only"}` in 2.5 s. One turn then sent three
`item/commandExecution/requestApproval` requests, each with the command,
its directory and for the write a reason in words ("Allow removing the
single duplicated word in sections/method.tex? The workspace is
read-only."); answered `accept`, the turn completed at 16 s. Again no
`fileChange` item and no `turn/diff/updated`.

**A queued message runs when its thread is next live.** `codex queue
--thread <id> --message "…"` returned in 0.18 s. A second app-server
that then called `thread/resume` on that thread saw
`thread/queue/changed`, `turn/started`, the queued text as the user's
message and the agent's answer, with no `turn/start` sent.

**Not run.** The two ACP adapters (installing npm packages was not
permitted in the session that did this survey); a queued message
against a thread open in a terminal; the daemon's socket (newline JSON
through `codex app-server proxy` got no answer, as WebSocket framing
would explain); Channels; anything on macOS or Linux.

## Part 3: the design

### What the work-folder steps settled

The feedback layer answers most of what the first version of this part
designed, and it was built for this use (file-explorer.md, decision 5:
"the connection to the agent is the owner's to build later; this plan has
to leave the right seam"):

| The first version proposed | Now |
|---|---|
| a project bound to a page and a directory | the work folder: its vault knows the directory, its pages are the files at their paths |
| finding a passage's source line with the `synctex` program | `gamma/feedback/sources.py`'s in-house reader, with gates against a stale build, the line recorded when the thread is opened |
| a "run" row in `users.db` | the feedback request, its threads and its states |
| the agent's context packaged by the server | the feedback entries (`requests.entries_of`), with their region pictures written to `.gamma/feedback/img/` |
| a reply that changes while the agent works | the request's `running` detail, on the dialog and on each thread's chip |
| the agent stamped on its replies | `agent` / `agent_id` on a comment |
| Gamma, or the agent, building the PDF | the agent builds in the local environment (file-explorer.md, decision 4: Gamma builds nothing) |

So the host is a connector at that seam, on the desktop app's own server,
in the same process as the work folder. It claims a request, runs the
agent in the directory, writes the answers through the same functions the
MCP answering tools use, and sets the states. The chat's second side
(step 3) and the remote (step 4) are other ways in to the same host.

### The model: a page's computer

| VS Code Remote | Gamma |
|---|---|
| the machine that has the files | the computer that keeps the vault, by its name (*TIM-PC*) |
| VS Code Server on it | the desktop app's local Gamma server |
| a tunnel: the machine dials out, a window reaches it through a relay | local Gamma dials out to the server its vault names; a window talks to its own server only |
| the remote indicator in the window's corner | the page's vault chip, and a strip at the top of the chat |
| the terminal and the extensions that run there | Codex or Claude Code, started in the vault's folder |
| *Reconnecting…* | the strip's states; a turn goes on at the computer meanwhile |

One word differs. In the vaults' vocabulary Gamma's server is the
*remote* and the directory is *local*, the other way round from VS Code,
so the interface never says "remote": it names the computer.

### How the server sees a vault (step 4)

Today it does not: a vault's row lives in the desktop app's own server,
and the server that holds the page sees a token replacing a PDF. The
missing piece is small because that local server already holds, for each
vault of another server, its address and a token of the vault's maker.

- **Local Gamma announces itself.** While it runs, it keeps one channel
  open to every server its vaults name and says who it is (a computer
  id and name), which vaults it keeps there (the page or folder each is
  for) and which assistants it found (`codex` and `claude` on the path,
  their versions, whether each is signed in).
- **The server keeps that as presence**, in memory, as it keeps the page
  rooms: nothing is stored, and a computer that stops is gone at its
  next missed beat. `GET /api/pages/{id}/computer` answers, for the
  vault's maker, `{computers: [{id, name, online, here, assistants,
  folder}]}`: the page's own vault first, else the folder vault it is
  filed under. The page's room hears `{t: "computer"}` when that
  changes, as it hears `{t: "vault"}`.
- **The channel** is outbound HTTPS from the PC: a long-poll for what
  the server asks (25 s, under a proxy's idle limit) and posts for what
  the PC reports, in batches a few times a second while an agent
  streams. A WebSocket where one passes; the page's own socket already
  falls back the same way ([collab.md](../dev/collab.md)).
- **A workspace on the desktop app's own server** has no hop: the
  server is the computer. The two cases are one interface with two
  implementations, as a vault's `LocalSink` and `RemoteSink` are.
- **`here`** says the window is on that computer (the shell's preload
  gives the page its computer id), so the label reads *This computer*.

### One path for every window

A page asks its own server, the server asks the computer. The desktop
app's window on a NAS's page could reach the local server directly, but
that is a second origin, a bridge that answers the page nothing today
(`askShell`), and a second code path for the same thing. One path means
an iPad, a browser and the desktop window behave alike, and the
approvals, the transcript and Stop have one home.

### The chat window gets a second side (step 3)

The chat keeps its one window. Where a page has a connected computer,
its header carries a switch between two sides, each with its own
conversation and its own History:

```text
┌ Chat ── [ Gamma AI | TIM-PC ● ] ───── find · history · new ┐
│ TIM-PC · D:\Papers\cavity · connected                    … │
│────────────────────────────────────────────────────────────│
│ You                                                        │
│   fix these                                                │
│   p.3  "Optical cavities are usefull for"                  │
│   p.5  "which is to small to measure"                      │
│                                                            │
│ Codex                                                      │
│ ▾ 4 steps                                                  │
│     Read sections/intro.tex                                │
│     Edited sections/intro.tex                 +1 −1   diff │
│     Edited sections/method.tex                +1 −1   diff │
│     Built main.pdf · 9 s                                   │
│   Fixed both: "useful" (intro.tex:2) and "too small"       │
│   (method.tex:3).                                          │
│   ┌ Changed 2 files ───────────────── View diff · Revert ┐ │
│   │ PDF updated on this page · 2 highlights moved        │ │
│   └──────────────────────────────────────────────────────┘ │
│                                                            │
│   ┌──────────────────────────────────────────────────────┐ │
│   │ p.7  "Parameters of the the cavity"                × │ │
│   │ Message Codex on TIM-PC…                             │ │
│   │ [+]                   [ Codex · model ▾ ]  mic  Send │ │
│   └──────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

- **The switch** (*Gamma AI* │ the computer's name with its state dot)
  is drawn only where there is a computer; every other page's chat is
  as it is. It is a switch and not an entry of the model menu because
  the side changes what the conversation can touch, which approvals
  apply and where its history is, where a model changes none of those.
  Two computers for one page make the second segment a menu.
- **The strip** under the header is the remote indicator: the computer,
  the folder (to its maker), the state. It sits where the provider's
  health strip sits (`chatHealthStrip`), and its *⋯* holds *Continue in
  a terminal* and *Show in folder*.
- **The composer** keeps its chips. A selected passage is already kept
  with its position (`pdfSelections`: `{text, page, box}`), which is
  what the computer needs to find the source line, so "select, type
  *fix this*, send" needs nothing new from the PDF viewer. The [+] menu
  gains *Open comments on this page (n)* beside *Attach what I see*; a
  picture of the page matters here, since an overfull box or a figure
  in the wrong place is seen and not quoted. The model chip becomes the
  assistant's: *Codex* or *Claude Code* as found on that computer, with
  that assistant's models and effort as the chip's sections. The PDF
  and Plan switches are not drawn on this side.
- **The transcript** is the chat's own. The steps pill and its chips
  take four new kinds: a file read, a file edited (its diff in the rows
  the approval card draws, `ctx` / `del` / `ins`), a command with its
  output in the chip's detail, the build. A reply that changed files
  ends in one card: the files, *View diff*, *Revert*, and what happened
  to the PDF.
- **Asking** is `ApprovalCard`: *Codex wants to run a command on
  TIM-PC*, the command and its folder, *Allow once*, *Allow in this
  chat*, *Decline…* with a note. The composer's *Allowed in this chat*
  chip shows what was granted and takes it back.

This is cheap to draw because the server speaks the chat's own stream
to the window. What the agent says is translated once, on the server:

| From the agent (ACP's names; each driver maps its own) | The chat's stream | Drawn by |
|---|---|---|
| a message chunk | text | the reply |
| a tool call: read, edit, execute | a step and its action | the steps pill, `chipText.js` |
| an edit's diff | the action's `diff` | the approval card's diff rows |
| a plan | the plan | `PlanCard` |
| a permission request | `{approval}` | `ApprovalCard`; the answer goes to `POST /api/ai/approvals/{id}` as now |
| the turn's end, the files changed, the build | `done` with a summary | a card after the reply, as `AgentChanges` is |

### The states

| State | The switch's dot, the strip | The composer |
|---|---|---|
| connected | green; *TIM-PC · folder · connected* (*This computer* in a window on it) | open |
| working | pulsing; *Codex is working*, Stop | Stop in place of Send |
| waiting for an answer | amber; *Waiting for you* | the card above it |
| reconnecting | grey, turning; *Reconnecting to TIM-PC…* | closed; a running turn goes on there |
| offline | hollow; *TIM-PC is offline since 14:02. Open Gamma there to continue.* | closed; the conversation stays readable |
| assistant missing or signed out | amber; *Codex is not signed in on TIM-PC* | closed, with the words |
| turned off on the computer | no second side at all | |

### On the computer

What a server may ask of a computer is switched on at the computer,
never from a page: a vault alone opens nothing. The switch is in the
vault's view of the sync panel, set through the shell as a vault's path
is ([synced_vaults.md](../dev/synced_vaults.md#the-routes), the **P**
routes). Step 1 built its first form, one agent per work folder:

```text
Synced vault: cavity                         D:\Papers\cavity
  Pull ●   Push: everything
  Agent in this folder      [ Off | Codex | Claude Code ]
    Codex answers this folder's Sends: it edits the files here and
    runs commands in its sandbox, without network.
  Now: Codex is working · Editing sections/intro.tex
```

Later forms add, with step 3's approvals, what the agent may run
(*Never*, *Ask in the chat*, *Ask on this computer*) and Stop on the
*Now* line, so the person at the computer sees what drives it and from
where, as VS Code lists a tunnel's connections. The computer is reachable
while the desktop app runs, in the tray included.

### The page and the thread

The vault chip is the page's indicator. Today it is drawn only in the
desktop app on its own server; with presence it is drawn for the maker
in every window:

```text
┌ Synced vault: main.pdf ──────────────────────┐
│ On TIM-PC · connected                        │
│ D:\Papers\cavity\main.pdf                    │
│ Pushed from this file                        │
│ Assistants there: Codex, Claude Code         │
│──────────────────────────────────────────────│
│ Chat with Codex on TIM-PC                    │
│ Show in folder                               │
└──────────────────────────────────────────────┘
```

A comment is a second way in, for a mark made while reading:

```text
│ tim     @codex fix this only                               │
│ Codex   on TIM-PC, for tim                                 │
│         Fixed: sections/intro.tex:2, "usefull" → "useful". │
│         Open in chat · Revert                              │
```

Step 2's plan for the mention:

- The @ menu of the vault's maker lists the folder's agent under the
  people, in a comment on a work folder's file. In anyone else's comment
  `@codex` is plain text, and a username `codex` keeps its meaning
  elsewhere.
- The mention is a Send: of that thread, with the rest of the comment as
  the instruction, when it is written in a thread on a passage, an area,
  a region or a note; of every open thread of the file when it is written
  in the page's own thread. The request, its states, its chip and its
  answers are step 1's.
- A follow-up in a thread the agent answered ("no, keep the British
  spelling") names it again, and the run resumes the agent session that
  answered there (Codex `thread/resume`, Claude Code `--resume`), so the
  agent remembers what it did.
- With the computer off, the request waits as any Send does; with step 4
  the thread says *TIM-PC is offline; this runs when it connects*.

The server hangs the mention on `comments.after_write`, beside
`inbox.from_comment`, by the rule the Inbox uses (the names after an
edit, less the names before).

### What a run does on the computer

The mechanics are [dev/local_agents.md](../dev/local_agents.md); in short:

1. **Claims the request**, `sent` to `running` in one write, so an outside
   agent watching the same request sees it taken.
2. **Asks for the entries** the feedback reader gives (the place, the
   source line SyncTeX found, the region pictures), leaving out the
   threads already answered and those whose newest comment is an agent's.
3. **Prompts the agent** through its driver, in the folder: the
   instruction, then each comment as an escaped JSON object with its
   author and whether it is the owner's, so no comment text can pass for
   Gamma's own lines, and the shape of the answer: per thread `fixed`,
   `question` or `unchanged`, with a reply.
4. **Reads what changed from the disk**, not from the events: a snapshot
   of the directory before and after. Codex on Windows edits through
   shell commands and reports no patch.
5. **Answers**: one reply per thread signed with the agent's name, the
   fixed ones resolved, the request `done` with the summary and the files
   changed. The vault pushes the rebuilt PDF as it does any build.

One run at a time per folder; the next waits.

### A turn belongs to the computer (step 3)

A reply of Gamma's own chat stops with its tab. A turn here is the
computer's: it keeps a numbered log of what the agent did, a window
reads from where it left off, and closing the tab stops nothing. So a
turn started on the PC is watched from an iPad, a dropped connection
costs nothing, and a turn a comment started needs no window at all. It
shows in Background tasks for the account (*Codex on TIM-PC · A cavity
with two mirrors*, Stop), and the conversation is saved as the chat
saves any ([chat_history.md](../dev/chat_history.md)), with the agent's
session id, so it is read where the computer is off.

### Whose words run, and what a server can do

1. **Only the vault's maker.** A run starts from the maker's own Send
   made in Gamma, never from a token's (a mirror or a script holding the
   maker's write token is refused while the folder has an agent), and the
   computer's side, the chip's card and the mention are the maker's; a
   co-author sees none of them. A viewer and a share visitor may comment,
   so a comment is not trusted text, and nobody reaches another person's
   computer by writing one.
2. **Other people's words are data.** A thread's comments by others
   travel named as theirs, in escaped JSON, under an instruction that
   says so.
3. **The agent holds no Gamma credential.** It is handed its context and
   answers in its final message; Gamma writes the replies. Its
   environment has none of the server's variables (the shell's secret,
   the data directory) and none of a parent agent session's.
4. **Edits inside the folder; no shell for Claude Code; Codex's sandbox.**
   Claude Code runs with `--permission-mode acceptEdits`, the file tools
   and no `Bash`: a rule such as `Bash(latexmk *)` would let it run
   anything, since latexmk takes Perl with `-e` and runs any
   `.latexmkrc` the agent has just written. Codex runs in its
   `workspace-write` sandbox with the network off, its Windows sandbox
   checked ready first. Neither gets the person's MCP servers or web
   tools in a feedback run. Nothing is started with a bypass flag.
   Step 3's approvals let Claude Code ask to run the build.
5. **The server is trusted with the folder.** Whoever runs the server a
   vault names can send a turn to its computer and answer its cards.
   For one's own server that is nothing new; for a server someone else
   runs (step 4), *Ask on this computer* puts every command's card on
   the computer's own screen. The panel's switch says this where it is
   turned on.

### The drivers

One interface on the computer, shaped after ACP's session methods so
that driver is a pass-through:

```text
open(cwd, resume=None) -> session      prompt(session, text) -> events
answer(request, decision)              stop(session)
```

| Driver | Speaks | Why this one |
|---|---|---|
| `codex` | `codex app-server` over stdio | the installed CLI, no dependency, approvals relayed; pinned to a tested version, its schema generated again on an update |
| `claude` | `claude -p` with `stream-json` both ways | the installed, unmodified binary under the login it has |
| `acp` | ACP over stdio with any command line | Gemini CLI, Copilot CLI, Cursor, OpenCode, Goose and whatever comes, with no adapter |

Why not ACP alone: for exactly the two agents asked about it adds a
Node package each, the Claude one embedding the Agent SDK (the side of
the terms that points to API keys), neither was run here, and v2 will
break v1. The seam costs nothing: if the adapters prove out, `codex` and
`claude` become two command lines of the `acp` driver.

The assistant is an engine of a conversation, never an entry of the
provider list: it brings its own loop, tools and approvals, so Gamma's
tool loop, its permissions and its context building stay out of that
side.

### The session is the author's too

The strip's *Continue in a terminal* gives `codex resume <id>` (or
`codex://threads/<id>`) and `claude --resume <id>`: the conversation
opens on the computer with its context, with the agent's own settings,
its AGENTS.md or CLAUDE.md, its models and its MCP servers, as a remote
window uses the remote's settings.

Delivery **into a session already open** is an opt-in per assistant,
later:

- **Codex**: the computer lists the threads whose `cwd` is the folder
  (`thread/list` takes a `cwd` filter), the owner picks one in the
  vault's view, and a turn is sent with `codex queue --thread <id>
  --message … --resume`. The answer is read from the thread afterwards.
- **Claude Code**: a Gamma channel, once Channels leave their preview
  and a plugin can be allowlisted; until then *Send to my session*
  opens `vscode://anthropic.claude-code/open?prompt=…`, which fills the
  prompt in for the person to send.

It is not the default because nothing about it is portable, the answer's
way back is weak in every variant, approvals appear in a terminal the
author is not looking at, and a turn dropped into a conversation about
something else spends that conversation's context.

### What works without the host

An agent the person starts at a terminal already acts on the same
feedback: the plugin's skill ([plugins/gamma](../../plugins/gamma/README.md),
"Feedback on a work folder") reads `.gamma/feedback/open.md` or
`list_feedback`, changes the sources, rebuilds, and answers through
`reply_feedback` and `resolve_feedback` on a connection allowed to
answer feedback ([dev/feedback.md](../dev/feedback.md#the-seam)). Step 1
adds the other direction: Gamma starts the agent when the person sends.
Where a folder has an agent on this computer, an outside agent leaves
the requests the host has taken alone (their `state_by` names the host's
agent), and a token cannot move them.

### In steps

| Step | Builds | State |
|---|---|---|
| 1. An agent on this computer answers a Send | the host on the desktop app's own server (`gamma/local_agents/`: finding the agents, the two drivers, the claim and the run, the answers), the folder's agent switched on in the sync panel, "Send to Codex" in the Feedback dialog, the agent's progress, summary and changed files on the request | built: [dev/local_agents.md](../dev/local_agents.md), "As built (step 1)" below |
| 2. `@codex` in a comment | the mention as a Send of its thread or of the file's open threads, the @ menu's agent row, a follow-up resuming the session that answered, the names settled without reserving them | built: [dev/local_agents.md](../dev/local_agents.md) "A mention in a comment", "As built (step 2)" below |
| 3a. The chat's computer side | the switch and the strip, the agent's turns in the chat (its reply, its steps, what it changed), a selected passage sent with its source line, Stop, a turn that outlives its tab, its own conversations and History | built: [dev/local_agents.md](../dev/local_agents.md) "The chat's computer side", "As built (step 3a)" below |
| 3b. Approvals | approval cards relayed from Codex's app-server and Claude Code's permission prompts (so Claude Code may ask to build), what the agent may run set on the computer | built: [dev/local_agents.md](../dev/local_agents.md) "Asking in the chat", "As built (step 3b)" below |
| 3c. The rest of the side | *Continue in a terminal* and *Show in folder*, the agent's model and effort, the reply as it is typed, the selection's source line as a chip, a Background tasks row | built: [dev/local_agents.md](../dev/local_agents.md) "Its model, its typing, a terminal, the sources, Background tasks", "As built (step 3c)" below; later: View diff and Revert, *Open comments on this page (n)*, *Attach what I see*, a folder's computer chat |
| 4a. The channel and presence | the computer announced to the servers its vaults name over a channel of its own (a `computer` token, an announce and a held poll), presence kept by the server and shown to the vault's maker in any window, the sync panel's line | built: [dev/computers.md](../dev/computers.md), "As built (step 4a)" below |
| 4b. Work folders of another server | a vault of another server that pushes everything into an empty folder it marks there, the tracking dialog on the host, that server's rules for the mark and its owner's *Let go* | built: [dev/work_folders.md](../dev/work_folders.md) "Work folders of another server", "As built (step 4b)" below |
| 4c. Their Sends and mentions | run on the computer over the channel: the server's claim, the entries, the answers written by the server | built: [dev/local_agents.md](../dev/local_agents.md) "A work folder of another server", [dev/computers.md](../dev/computers.md) "The Sends", "As built (step 4c)" below |
| 4d. The chat relayed | the chat's computer side on another server's page, its asks answered on the computer, *Show in folder* and *Continue in a terminal* from the desktop window there | plan |
| Later | the `acp` driver, delivery to an open session, a co-author's use of the maker's computer, one Inbox item per finished request | |

### As built (step 1)

[dev/local_agents.md](../dev/local_agents.md) is the reference; where it
went otherwise than planned above:

- **A Send says how it was made.** "Only the maker's Send starts a run"
  was not enough: a mirror or a script holding the maker's write token
  writes as the maker. A request records `via`, `session` or `token`
  (migration step 58, `request_via`, a local part only), and only a
  session's Send by the vault's maker is the agent's. A token's Send to a
  folder with an agent here is refused; one made before the agent was
  turned on stays for an outside agent.
- **Claude Code reads no settings.** `--restricted` with
  `--permission-mode acceptEdits`, the five file tools, no `Bash`, no
  web tools and `--strict-mcp-config`: a folder's own `.claude/settings.json`
  or hooks never reach the run, and a write under `.git/` is refused (both
  tried against the real CLI). The cost is the account's default model.
- **Codex's MCP servers are switched off by name.** `thread/start` takes
  a config of `mcp_servers.<name>.enabled = false` for each server the
  person's config lists, with apps and web search off, rather than a
  config file of Gamma's own, so the person's sign-in and model stay.
- **What the request says is limited to what members see.** Its detail
  names a changed file only when the work folder tracks it (other changes
  are counted), absolute paths are cut from the agent's words, and the
  run's error, stderr and steps stay in vaults.db on the computer.
- **Turning one on asks how many it will take, counted by the server.**
  The panel's confirmation reads `GET /api/local-agents/waiting`, the
  rule the hand-over itself uses, rather than counting the requests it
  sees.
- **A request knows it is the computer's.** Its record carries `local`,
  so *Withdraw* on a running request of this computer's agent says it
  stops the agent (what it changed stays), which an outside agent's
  never claims; withdrawing stops the run.
- **No session is resumed.** The drivers take one; step 2's follow-ups
  are the first to use it.
- **The Inbox is unchanged.** A local agent's reply reads as the
  connection's, and the finished request has no item of its own.

### As built (step 2)

[dev/local_agents.md](../dev/local_agents.md) "A mention in a comment" is
the reference; where it went otherwise than "The page and the thread"
planned:

- **Decided in the route, not on `after_write`.** The rule runs once per
  write in the comment routes, before `comments.written`, and puts the
  name on the event, so the Inbox reads the same answer; the Send follows
  the write and comes back in the route's answer (`send`), so a refusal
  reaches the field's status line and the thread comes back with its Sent
  chip. A listener on `after_write` could do neither.
- **The names are not reserved.** In the maker's comment on a file of the
  folder, with that agent on, `@codex` is the agent: the Inbox tells no
  member of that name, and the @ menu lists the agent and not the member.
  Anywhere else the name keeps its meaning. Accounts named `codex` may
  exist already, and the `acp` driver would add names.
- **A thread the agent fixed stays resolved.** A follow-up reopens it
  first; a mention in a resolved thread is refused with that advice.
- **A mention while a run works is the next run's.** The plan had one run
  a folder and nothing more; a mention made in a thread a run was working
  on was then answered by that run's reply and never seen. The run now
  says when it read the threads (`seen`) and which comments it read,
  answers only the Sends made by then, leaves a thread open over a word
  it did not read, and the next run takes the thread up again although
  its newest comment is the agent's. Nor does another member's reply
  cancel a waiting mention any more: the runner asks no request's
  `answered` flags, which such a reply sets.
- **Sessions are per thread, kept on the computer.** vaults.db's
  `agent_threads` records the session that answered each thread and the
  directory it ran in; a run resumes one only when all its threads share
  it there. A session the agent no longer has starts afresh once.
- **The @ row is the desktop app's.** The page's vault answer gained
  `mine`, and switching the agent tells the folder's open pages, so the
  row follows the switch; a browser on the same server shows no row,
  though a typed `@codex` sends there too.

### As built (step 3a)

[dev/local_agents.md](../dev/local_agents.md) "The chat's computer side" is
the reference. Step 3 was cut in three (3a, 3b, 3c in the table above)
once the survey of the chat showed how little of Gamma AI's machinery the
side can take; where 3a went otherwise than planned:

- **Not "as the chat saves any".** Gamma AI's chats are the workspace's:
  every member and a share read them, and they travel in backups, exports
  and `read_chats`. The computer's conversations are the maker's alone,
  in their own private table of the page's server's pages.db
  (`computer_chats`, out of backups by `PRIVATE_TABLES`), written by the
  runner; the session id stays on the computer (vaults.db
  `agent_conversations`), where a resume and a terminal need it. pages.db
  and not vaults.db, so that step 4's server already holds the transcript
  when the computer is off.
- **Not "the chat's own stream".** Gamma AI's reply is one streamed POST
  whose disconnect stops it, which is the opposite of a turn that belongs
  to the computer. A turn is started by a POST, queued in the vault's
  queue beside the Sends (so one directory never has two agents), and
  read whole by polling with a version (every 1.5 s while it runs); Stop is
  its own call. A numbered log was not needed: the transcript is small.
- **The transcript is drawn by the side, not by the chat's components.**
  The chat's step kinds (`read`, `edit`) already mean notes read and
  edited, with a Revert of notes; the side's steps are files of the
  folder, structured on the server and worded on the client.
- **Whole messages, not typed text.** The drivers give a message at a
  time; Codex's deltas stay off.
- **Kept out of 3a:** approvals (3b), and every item of 3c. The plan's
  *Revert* also needs a decision first: Gamma writes no file the disk made
  (file-explorer.md, decision 3), so it would be a turn asking the agent to
  undo, or the agent's own checkpoint (Claude Code's `rewind_files`).

### As built (step 3b)

[dev/local_agents.md](../dev/local_agents.md) "Asking in the chat" is the
reference; where it went otherwise than planned:

- **Asking is the chat's, never a Send's.** Nobody watches a Send, so it
  keeps refusing. The panel's *Never | Ask in the chat | Ask on this
  computer* is built as *Nothing more | Ask me in the chat*: the third,
  a card on the computer's own screen, waits.
- **Not `POST /api/ai/approvals/{id}`.** That route takes any token of the
  account, keeps its approvals in a window's stream and offers *Always
  allow*. The asks are the turn's, held by the runner, written in the
  agent's message so any of the maker's windows draws the card from its
  poll, and answered through their own maker-only route; the card is the
  approval card's look, not its component, and has no *Always allow*.
- **Allowed in this chat is Gamma's record.** Codex's `acceptForSession`
  lives in one app-server process and Claude Code's session rules in one
  CLI process, while a turn is one process; and an agent's own rule could
  be a prefix (`Bash(latexmk *)`, a shell). Gamma keeps the exact
  commands allowed per conversation and answers them itself.
- **Claude Code asks through `--permission-prompt-tool stdio`**, the
  control protocol the Agent SDK speaks (hidden from `--help`), with an ask
  rule in `--settings` making every Bash call a prompt; a run that asks is
  stream-json both ways. Every other prompt (an edit of a protected file,
  any other tool) Gamma denies itself.
- **Checked against the real CLIs** (Codex 0.167.0, Claude Code 2.1.318)
  before the merge: a Claude Code Bash call asked through Gamma's handler
  and run once allowed; its Edit of `.git/config` refused by Gamma with no
  card; Codex asking to run `curl` outside its sandbox (its request id an
  integer), allowed and run with the network. Still not run for real: a
  Codex file-change approval, `untrusted`, and Claude Code's interrupt with
  a prompt waiting.

### As built (step 3c)

[dev/local_agents.md](../dev/local_agents.md) "Its model, its typing, a
terminal, the sources, Background tasks" is the reference. Five of the
plan's pieces were built; where they went otherwise:

- **The terminal is the shell's, never the page's.** The page asks the
  shell with the page's id alone; the shell asks its own server for the
  session and the agent's command line and builds the terminal's command
  itself, refusing anything a terminal would read as its own syntax.
  Checked for real before building: `codex resume` and `claude --resume`
  in the folder take up a session Gamma's driver made, with its context.
  The terminal session is the person's own (their settings, MCP servers
  and trust prompts), not Gamma's confinement.
- **The models come from the agents themselves**, with no model use:
  Codex's `model/list` and Claude Code's `initialize`, read with the probe.
  Claude Code needs `--model` and `--effort` on its command line, since
  `--restricted` reads none of the person's settings.
- **Typed text is the poll's**, not a stream to the window: the drivers
  stream deltas into the message, which the window reads every 1.5 s while
  a turn runs.
- **Left for later, each needing more than building:** *View diff* and
  *Revert* (Gamma writes no file the disk made: a turn asking the agent to
  undo, or the agent's own checkpoint, is a decision first); *Open
  comments on this page (n)* (context for the chat, or a Send: undecided);
  *Attach what I see* (pictures for Claude Code must sit under `.gamma/`);
  a folder's computer chat.

### As built (step 4a)

[dev/computers.md](../dev/computers.md) is the reference. Where it went
otherwise than "How the server sees a vault":

- **A credential of its own.** The channel speaks with a token of a new
  `computer` scope, minted on that server by the shell with the window's
  session and handed to the host with the shell's word, never with the
  vault's sync token: a bearer is identity, and a sync token in a script
  or a mirror's config must not pose as the computer. Such a token opens
  the channel's two routes and nothing else, reads included, and no MCP
  connection.
- **The poll is the heartbeat.** One held `GET` (25 s, under a proxy's
  idle limit, `async` so a hold takes no worker thread) both waits for
  what the server asks and keeps the computer online; online lasts 45 s
  past the last poll. The announce is a `POST` only when what the
  computer keeps or finds changes.
- **A small durable row.** Presence is in memory, but what the computer
  last announced and when it was last seen is a users.db row per token,
  so *offline since* survives a restart, and 4c can word a mention while
  the computer is away.
- **What the server learns** is the computer's id for it (an HMAC of the
  install's id and the server's origin, so two servers cannot match one
  computer by it), its host name, which folders and pages it keeps (ids;
  the server names them), whether each is paused, and the assistants
  with a problem's code, never its words and never a path.
- **The nudge goes to the account** (`publish_account`), not the page's
  room, so co-authors never learn when the maker's computer is on.
- **One account there per link.** The host keeps one link per server,
  workspace and local account, and never hands it to another account
  signed in there (the shell says so, the host refuses it): the vaults it
  announces were made with that account's tokens. The vaults themselves
  record no account there yet, which 4c, whose runs answer as the maker,
  needs (the survey's critic, C1).
- **Not yet**: `here` (the desktop window on that server's page knowing
  it is on the computer, for *Show in folder*) waits for 4d; nothing on
  the computer acts on what the channel says, but to announce again.
- **The open questions of trust** are answered as defaults for 4c and 4d:
  a server the app did not start gets *Ask on this computer* as the only
  asking form (a card answered on the computer itself, never relayed from
  that server's sessions), and a window on a phone may start, follow and
  stop the agent's work within its confinement but never widen it.

### As built (step 4b)

[dev/work_folders.md](../dev/work_folders.md) "Work folders of another
server" is the reference. Where it went otherwise than planned:

- **An explicit opt-in.** "A remote vault that pushes everything" would
  have turned the kept folders of another server that already pushed
  everything into work folders; a remote vault is one only once made one
  (`work_vault`: its link's `TAKEN_OVER`).
- **Empty folders only.** A work folder of another server is made of an
  empty folder there, checked after the mark is written; a takeover over
  HTTP (a folder with pages) is later.
- **That server keeps its members safe**: it refuses a mark in a clone, a
  Zotero-linked workspace or around another work folder, says it keeps
  work folders (`whoami.work_folders`, so an older one gets no mark), and
  its owner can *Let go* of a work folder whose computer is gone, so no
  folder stays locked for every member; the computer whose mark is gone
  stops instead of marking it again.
- **The dialog stays on the computer** (the host's own window, the window
  sent back to that server after), saying what is uploaded there and
  which ticked files may hold secrets.

### As built (step 4c)

[dev/local_agents.md](../dev/local_agents.md) "A work folder of another
server" and [dev/computers.md](../dev/computers.md) "The Sends" are the
reference. Where it went otherwise than "How the server sees a vault":

- **Not a relay of the local routes.** The page's server decides whose
  Send it is and keeps the request, its claim and its answers; the
  computer pulls the Sends meant for it on the channel (woken by the
  held poll), claims each for a run it names, and posts its answers. A
  claim or an answer retried writes nothing twice; a withdraw is never
  overwritten.
- **The server writes the agent's replies** (signed `local:<agent>`,
  with its own clock as `seen`): the computer's token writes no comment.
- **The computer computes what needs its disk**: the source lines, from
  the PDF and the sources there; the pictures are fetched from the server.
- **The binding is announced** with the vault, so the server words the
  agent and refuses a token's Send while the computer is away; a Send
  made then waits for it.
- **Asking stays local**: a server the app did not start gets Sends and
  mentions, which never ask; the chat (4d) is next.

### Declined

- **A webhook from the server to the PC.** A computer behind a router
  has no address; Linear's agents need a public endpoint for this.
- **A window talking to the local server directly.** A second path for
  the desktop app alone.
- **The assistant as a model of Gamma's chat.** It is not a model.
- **The agent writing its own replies through MCP.** It would hold a
  write credential while reading untrusted text.
- **A shell rule for Claude Code's build** (`Bash(latexmk *)`). latexmk
  runs Perl from `-e` and from any `.latexmkrc`, so the rule is a shell.
  The build waits for step 3's approvals.
- **Gamma running the build itself.** file-explorer.md's decision 4:
  building happens in the local environment, and Gamma shows the new PDF.
- **The live session as the transport.** Above.
- **Gamma's own chat editing the sources over the same channel.** The
  channel could carry file tools for it one day; that is
  [latex-projects.md](latex-projects.md)'s tool loop with the files
  left on disk, and a second thing to build.
- **An agent as a workspace member.** An account that is no person
  touches every member list, role check and share.

## Still open

- The switch in the header, or the cheaper form: a *Runs on* section in
  the model chip's menu, which hides the choice and swaps the
  conversation from inside a menu.
- Whether the computer's conversation of a page is one per page or one
  per assistant, and whether a page under a folder vault shares the
  folder's (the agent learns the paper once, the context grows; Codex
  already sends 64k input tokens for a one-line fix).
- The server's part in the trust, taken as defaults in "As built (step
  4a)": *Ask on this computer* the only asking form for a server the app
  did not start, and a phone driving the computer within its confinement
  only. Whether an agent may be on at all for a server reached over plain
  http outside this computer (the token travels in clear on the LAN)
  is for 4c.
- Whether "these" on a page also means highlights that carry a note and
  no thread. A note has no resolved state to say it is done; a work
  folder's feedback counts threads only (file-explorer.md, "Still to
  decide" 2).
- A highlight whose text the fix rewrote is drawn as a rough match or
  lost: accept it automatically when its turn answered it fixed?
- Undo: the snapshot makes a *Revert* possible; whether it restores
  files under an editor that has them open.
- What Codex's `workspace-write` confines on Windows depends on the
  sandbox the machine has set up (`mxc`, `elevated`, or the weaker
  `unelevated`); step 1 checks that it is ready, not which one it is. On
  this machine it is `elevated`, whose commands may run under a service
  outside the agent's process tree, so a stop interrupts the turn first.
- The answers of the two vendors on a local program driving the
  signed-in CLI, before Gamma Cloud's pages say it.
- A computer without the desktop app: `gamma_sync.py` could carry the
  same channel for one directory.
- Comments do not travel to clones, publications or the share host, so a
  mention works on the home server only.
- Every item under "Not run".

## Sources

Codex: [non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[app-server](https://learn.chatgpt.com/docs/app-server),
[SDK](https://learn.chatgpt.com/docs/codex-sdk),
[hooks](https://learn.chatgpt.com/docs/hooks),
[mcp-server removed](https://learn.chatgpt.com/docs/mcp-server),
[GitHub integration](https://learn.chatgpt.com/docs/third-party/github),
[Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox),
[release 0.157.0](https://github.com/openai/codex/releases/tag/rust-v0.157.0),
issues [32445](https://github.com/openai/codex/issues/32445) (a turn into
the daemon's thread), [45406](https://github.com/openai/codex/issues/45406)
(writer locks), [48043](https://github.com/openai/codex/issues/48043) (the
daemon on Windows),
[Sign in with ChatGPT](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt),
[codex-plugin-cc](https://github.com/openai/codex-plugin-cc).

Claude Code: [headless](https://code.claude.com/docs/en/headless.md),
[CLI reference](https://code.claude.com/docs/en/cli-reference.md),
[Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview.md),
[user input and approvals](https://code.claude.com/docs/en/agent-sdk/user-input.md),
[channels](https://code.claude.com/docs/en/channels.md) and their
[reference](https://code.claude.com/docs/en/channels-reference.md),
[cross-session messaging](https://code.claude.com/docs/en/cross-session-messaging.md),
[agent view](https://code.claude.com/docs/en/agent-view.md),
[remote control](https://code.claude.com/docs/en/remote-control.md),
[hooks](https://code.claude.com/docs/en/hooks.md),
[VS Code](https://code.claude.com/docs/en/vs-code.md),
[GitHub Actions](https://code.claude.com/docs/en/github-actions.md),
[legal and compliance](https://code.claude.com/docs/en/legal-and-compliance.md).

Protocols: [ACP overview](https://agentclientprotocol.com/protocol/overview),
[session setup](https://agentclientprotocol.com/protocol/session-setup),
[prompt turn](https://agentclientprotocol.com/protocol/prompt-turn),
[tool calls](https://agentclientprotocol.com/protocol/tool-calls),
[updates](https://agentclientprotocol.com/updates),
[v2 draft](https://agentclientprotocol.com/rfds/v2/overview),
[agents](https://agentclientprotocol.com/get-started/agents),
[clients](https://agentclientprotocol.com/get-started/clients),
[claude-agent-acp](https://github.com/agentclientprotocol/claude-agent-acp),
[codex-acp](https://github.com/agentclientprotocol/codex-acp),
[Python SDK](https://pypi.org/project/agent-client-protocol/);
[A2A and MCP](https://a2a-protocol.org/latest/topics/a2a-and-mcp/);
[MCP 2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog).

Products: Linear's [agents](https://linear.app/developers/agents),
[agent interaction](https://linear.app/developers/agent-interaction),
[signals](https://linear.app/developers/agent-signals) and
[guidelines](https://linear.app/developers/aig);
GitHub's [self-hosted runners](https://docs.github.com/en/actions/reference/runners/self-hosted-runners)
and [coding agent](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent);
[PDF Review for Coding Agents](https://marketplace.visualstudio.com/items?itemName=adgiz05.pdf-review-for-agents);
[Lattice's annotation rounds](https://github.com/leo1oel/lattice/pull/300);
[Orca's SyncTeX annotations](https://github.com/stablyai/orca/pull/25232);
[Agentation](https://github.com/benjitaylor/agentation),
[react-grab](https://github.com/aidenybai/react-grab);
[Claude in Slack](https://code.claude.com/docs/en/slack).
