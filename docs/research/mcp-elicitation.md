# Asking before an MCP connection changes notes

November 2026, MCP SDK 1.30.0, spec 2025-11-25. The question: once an
assistant's connection may edit notes ([dev/mcp.md](../dev/mcp.md#editing-notes)),
can Gamma ask the person before each change, as the chat's approval card
does ([ai-permissions.md](ai-permissions.md)), rather than leave it to the
assistant's own "allow this tool?" prompt? Read from the SDK's source, the
spec, and the clients' issue trackers and changelogs as of the date above.

## Three ways to ask

- **The client's own prompt.** Claude Code and Codex ask before an MCP tool
  call unless the person allowed the tool. That prompt shows the arguments
  the model chose, and "always allow" turns it off for good. Gamma can't see
  it or require it, and text in a PDF can steer what the model writes there.
  It is the client's guard, not Gamma's.
- **Elicitation** (spec 2025-06-18, URL mode added in 2025-11-25). In the
  middle of a tool call the server sends `elicitation/create` to the client:
  a message and a small form (flat JSON Schema: strings, numbers, booleans,
  enums). The client shows it and returns `accept` with the form's values,
  `decline` or `cancel`. The message is the server's, so Gamma words the
  question from the planned change itself, the same text the chat's card
  shows. This is the one Gamma took.
- **Step-up authorization** (SEP-835, 2025-11-25). The server answers 403
  `insufficient_scope`, and the client signs in again asking for more. It
  fits *getting* a grant, not approving each change. Client support was
  weak: Cursor offered no way to re-consent in a session, and Claude's
  TypeScript SDK had refreshed tokens without the wider scope (since fixed).
  Gamma gives Edit on the consent page instead ([dev/mcp.md](../dev/mcp.md#editing-notes)).

## Which clients can be asked

- **Claude Code**: since about v2.1.76 (March 2026), form and URL modes,
  with `Elicitation` / `ElicitationResult` hooks. A June 2026 bug (2.1.163,
  since closed) reported it not advertising the capability.
- **Codex CLI**: since April 2026 (openai/codex#17043), form mode.
- **Claude.ai and Claude Desktop**: no support, with open feature requests.
- **VS Code's Claude extension**: one report of URL mode declined silently.
- **DeepSeek Harness**: its `dsh-mcp-client` was not checked.

About half of the 24 clients one directory lists document elicitation. A
server has to work without it.

## What the SDK needs

- **A session.** The client answers `elicitation/create` in a new POST, which
  has to reach the call still waiting for it. In stateless mode every request
  gets a fresh transport, so the answer has nowhere to go. It needs a
  stateful session (`Mcp-Session-Id`).
- **SSE.** With `json_response=True`, the transport's POST handler waits for
  the call's result and skips every other message on that request's stream
  ("For notifications and request, keep waiting"). The question would never
  leave, and the call would hang. Asking needs the POST answered over SSE.

Gamma's transport was stateless and JSON for every client. Switching
everyone would change every answer to SSE, and keep sessions in memory for
clients that never need them.

## What was chosen

Two transports behind `/mcp`. Every client keeps the stateless JSON one.
A client gets a stateful SSE session when two things hold: it may edit
somewhere, and its `initialize` says it can show a form question
(`capabilities.elicitation` empty or with `form`). A session answers only
the token that opened it, and is gone after 30 idle minutes.

- **The question.** Asked from the tool's own approval preview, with one
  field, "Don't ask again for this connection". The message is plain text
  (Codex shows it as is, bold, `##` and Markdown links included), so the
  change goes line by line, `− ` and `+ ` as in a terminal, with a summary
  first. A first try showed the word diff inline (`[-…-]{+…+}`), which
  opened a bracket mid-line and closed it paragraphs later. Declined or unanswered after the chat card's 10 minutes,
  nothing changes and the model hears so, as it does in the chat.
- **Who is asked.** A connection asks by default (`integration_tokens.asks`).
  The person can turn that off on the consent page, in Settings, or with the
  question's checkbox.
- **The fallback.** A client that can't be asked is refused, with the steps
  to restart it or to stop asking.

## Still open

- No real Claude Code or Codex session has answered Gamma's question yet. The
  browser suite answers it with a hand-written client over a real server.
- No change made over MCP can be undone from Gamma yet. The chat's revert
  records ride on its reply's tool chips, which an MCP call has nowhere to keep.
- Clients that can't be asked (claude.ai, Claude Desktop) only change notes
  with asking turned off. Gamma could put the question in its own open
  window instead.
