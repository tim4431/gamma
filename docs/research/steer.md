# Steer: a message sent into a reply while it runs

How Codex and Claude Code take a message the user sends while the agent is
working, what Gamma AI's chat took from them, and the plan for steering an
agent on a computer (Codex CLI or Claude Code) the same way. Read on
2026-11-16: Codex's source at `openai/codex` `56e4366` (codex-cli 0.169.0
on the development machine), Claude Code's Agent SDK documentation (2.1.235
on the development machine). The mechanics as built are
[dev/ai.md](../dev/ai.md#queue-and-steer).

## Codex

Codex separates two things the user can do while a turn runs. **Queue**
holds the message for the next turn. **Steer** puts it into the turn under
way. The desktop app queues by default, draws the queue above the composer
(edit, reorder, Steer, delete) and sends the next one when a turn completes
on its own; Stop, a failure or a disconnection pauses it.

The core, `codex-rs/core/src/session/`:

- **The request.** The app-server's `turn/steer` takes `threadId`,
  `expectedTurnId`, `input` and an optional `clientUserMessageId`
  (`app-server/src/request_processors/turn_processor.rs`). It is refused
  when no turn runs or another one does ("no active turn to steer",
  "expected active turn id … but found …"), when the turn is a review or a
  compaction, and when the input is empty. A refused steer is the client's
  to send as a new turn.
- **The queue.** An accepted message joins the active turn's pending input
  (`input_queue.rs`). At the top of every iteration the turn loop drains it
  into the conversation, before it builds the next model request
  (`turn.rs`, "pending_input would be something like a message the user
  submitted through the UI while the model was running"), and records it as
  a user message item (`PersistContext::SteeredUserInput`), which is when
  the app-server reports it.
- **The turn goes on.** After a sampling request, `needs_follow_up =
  model_needs_follow_up || has_pending_input`: a message that arrived while
  the model wrote its final answer opens another request in the same turn.
- **Instant interrupt** (`Feature::InstantInterrupt`, `instant_interrupt`,
  stage under development, on by default). Each sampling request watches
  the queue (`watch_user_input`); a message cancels its `preempt` token,
  which stops reading the model's stream, asks the server to stop (or, once
  the response is settling, waits for its accounting to come in), and
  samples again at once with the message. Calls of a response that settles
  while a message waits are not dispatched (`stream_events_utils.rs`,
  `aborted_before_dispatch`); calls already running finish. What the model
  was halfway through writing is not kept ("TODO: Reconcile any response
  item already being presented to the client").
- **Stop** clears the pending input (`clear_pending`); the terminal client
  puts the steers it had not seen taken back into its composer.

Nothing of it is a model feature: the steered message is one more user
item in the next request of a stateless conversation.

## Claude Code

Claude Code takes messages on stdin while it runs when it reads
`--input-format stream-json` (the Agent SDK's streaming input). A message
sent during tool calls "reaches Claude after those tool calls finish";
Claude answers it and the earlier one in one response, and if the turn ends
first, the message starts the next turn
([Send messages to a running agent](https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode)).
`SDKUserMessage.priority`
([TypeScript reference](https://code.claude.com/docs/en/agent-sdk/typescript))
chooses when: `"next"` (the default) as above, `"later"` holds it for a new
turn, `"now"` interrupts the turn, and from v2.1.286, with `origin: {kind:
"human"}`, moves background-able work (commands, subagents, MCP calls)
aside instead and reads the message in the same turn. A result's
`user_message_uuids` names every message the turn answered, since messages
sent close together may be merged into one turn. `--replay-user-messages`
echoes each stdin message on stdout as it is taken.

## What Gamma AI took

The queue and Steer of Codex's desktop app, the boundary rule of its core
and its instant interrupt, for every provider, since a steered message is
a user turn on every wire (Anthropic's adapter folds it into the turn of
tool results). Differences, on purpose:

- What the model wrote before the cut stays as its turn, because the
  transcript the user read shows it and is what later messages replay.
- A cut turn reports no tokens, as a stopped one does; Codex waits for the
  provider's accounting, which the Responses API's interrupt gives it.
- The last round a limit allows is never cut, so a reply never ends
  half-written with nothing after it.
- A provider silent while it thinks is cut at its next event, as Stop is;
  closing a blocked socket from another thread was not worth it.
- The queue is the tab's, per conversation, and lost on reload, as Codex's
  is.

## Plan: Steer for a computer's agent

Status: **built**, on `ai-chat-steer` @ 2026-11-16; "As built" says where
the build differs from the plan. The mechanics:
[dev/local_agents.md](../dev/local_agents.md) ("The drivers", "The chat's
computer side", "The routes").

### What the user sees

- A queued message's **Steer** while Codex or Claude Code works, on this
  computer or on another one the chat reaches, as Gamma AI's row has it:
  "Steering…" until the agent takes it in, then the message drawn inside
  the turn's row at the place it was taken, the agent's work going on
  under it.
- A steer the agent never took (its turn ended first, the agent refused
  it, the computer went offline) is queued again at the front and goes as
  the next turn, as Gamma AI's does.
- An agent that cannot be steered (a Claude Code too old for it, a
  computer whose Gamma predates it) offers no Steer, as today.

### Checks before building

1. **Codex 0.169** (`codex app-server`): `turn/steer` while a command runs,
   while the model writes, and after the turn's last item; whether the
   `userMessage` item it emits carries `clientUserMessageId`; whether a
   steer refused because the turn just ended answers a JSON-RPC error the
   driver can tell from others.
2. **Claude Code 2.1.235** with `-p --input-format stream-json
   --replay-user-messages` and no ask flags: the echo of a message written
   mid-turn and when it comes; a message written after the last tool call
   (a second `result` in the same process, or one turn); `user_message_uuids`
   on the result; what closing stdin does to a message not yet read.
3. Whether `--restricted` and `--permission-prompts none` still hold with
   `--input-format stream-json` (today only the ask turns use it).

### The pieces

- **Driver** (`local_agents/events.py`): `Turn.steer(text, client_id) ->
  bool`, callable from any thread like `answer`, False when the agent
  cannot take it now; a `steered` event `{id}` when the agent took it in.
  - Codex (`codex.py`): `turn/steer {threadId, expectedTurnId, input:
    [{type: "text", text}], clientUserMessageId}` through `_send`, as
    `_reply` already sends one for a decline's note; the `userMessage`
    item's start is the `steered` event; a refusal is False.
  - Claude Code (`claude.py`): every turn reads stream-json input with
    stdin open until its `result` (today only `ask` turns), so the plain
    flags gain `--input-format stream-json --replay-user-messages`; a steer
    is a `user` line with `uuid` = the client id and `client_composed:
    true`; its echo is the `steered` event. How a turn ends with a steer
    written but not echoed waits for check 2.
- **Runner** (`runner.py`): `steer_chat(ws, turn_id, item)` beside
  `answer_chat` (`_active[(ws, turn_id)]["chat"].steer`), and
  `steer_relayed(link_id, turn_id, run, item)` beside `answer_relayed`.
  `_ChatTurn` keeps the steers it handed on and, at the `steered` event,
  where the reply stands (`{paragraphs, steps}`: the counts of
  `self.paragraphs` and `self.steps`), written with the next `_show`.
- **Stored shape**: the agent's message gains `steers: [{id, text,
  selections, at, taken: {paragraphs, steps}}]` (at most 20, the text at
  `TEXT_MAX`), one message a turn as today, so a turn's state, Stop, Retry,
  History and the CLI's sessions stay as they are. No migration:
  `computer_chats.messages` is JSON and the key is optional; a client from
  before it draws the turn without the steered messages. Export
  (`chat_export.step_text` and the conversation export) writes each where
  it was taken. `computer_chats` is in no backup.
- **Routes** (`routers/local_agents.py`): `POST
  /api/local-agents/chat/turns/{turn_id}/steer {id, text, selections}`,
  authorized as `answer_ask` is (the conversation's account; a co-author's
  turn by that person or the agent's owner). A turn here goes to
  `runner.steer_chat`; one running on another computer is kept with its run
  (`chats` beside `answer_run`) and the computer woken, and the channel's
  `GET /api/computers/chats` hands it over as `steers` beside `answers`
  (`computers/channel.py`). 409 for a turn that ended, which the client
  takes as "send it next".
- **Client**: the side's `steer(turnId, item)` (`useComputerChat.js`); the
  queue's Steer offered while the side's turn says it takes steers (a
  `steers: true` the server puts on `computer.turn` when the driver and the
  CLI's version can); `shownMessages` (`computerChat.js`) splits an agent
  message at its `taken` places into the agent's part, the user's steered
  message, the agent's part, so ChatTranscript draws them as Gamma AI's;
  a queue row "Steering…" leaves the queue once the message shows its id,
  and goes back to the front when the turn ends without it.

### Tests

The fake CLIs (`backend/tests/fake_agents/fake_codex.py`,
`fake_claude.py`) learn a steer: the driver tests check the request each
writes and the `steered` event; a runner test checks `taken` against the
paragraphs and steps around it and a steer refused after the end; a route
test checks the authorization and the 409; `computerChat.test.mjs` checks
the split; the e2e `local-agents` and `remote-chat` groups get a step each
(a steer here, a steer relayed to another computer).

### Left out

Claude Code's `"now"` (an interrupt, or from v2.1.286 the work moved
aside); a steer while an ask waits, which waits behind the ask as Codex's
own client has it; steering a comment thread's turn, whose messages are the
thread's comments.

### As built

- **The checks.** Codex 0.169.0: `turn/steer` answers `{turnId}`, the
  steered message comes back at once as a `userMessage` item with
  `clientId` (a command still running), and after the turn's end the
  answer is -32600 "no active turn to steer". Claude Code 2.1.235: a
  message written while a tool runs is echoed (`isReplay`, its `uuid`)
  after the running calls and answered in the same response; one written
  after the `result` starts a second turn in the same process; the result
  carries no `user_message_uuids` yet. Check 3 was not run: the
  development machine's 2.1.235 has neither `--restricted` nor
  `--permission-prompts`, so the flags are as before plus the two input
  ones.
- **No `steers: true` flag** on `computer.turn`: Steer is offered for any
  turn whose agent works (`steeringTurn`). One the agent never takes (a
  computer whose Gamma is older, a CLI that refuses it) goes as the next
  turn, the fallback every refusal has.
- **The far steers wait in the server's memory** (`chats._far_steers`), not
  in `computer_chat_runs`, as Gamma AI's inbox does: no migration, and a
  restart's loss sends the message as the next turn.
- **Claude Code's turn goes on past a `result`** while a steer it was
  written is not echoed: the driver keeps reading until the result of the
  turn Claude Code starts for it, so the steer is answered in Gamma's turn
  and never lost to the stdin closing.
- **Retry** of a failed turn finds the turn by its row's `turnId`, since a
  steered turn draws more rows than the conversation has messages.
