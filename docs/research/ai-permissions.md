# Asking before an agent acts

How coding agents ask the user before a tool call, surveyed in September
2026, and what Gamma's chat took from them. The mechanics Gamma ended up
with are in [ai.md](../dev/ai.md#asking-before-a-call-approvals).

## What the agents do

**Claude Code** keeps permission rules in its settings as allow, ask and
deny lists. A rule names a tool, optionally with a pattern (`Bash(npm run
*)`), and the lists are checked deny first, then ask, then allow. A call no
rule allows stops at a prompt in the terminal with three answers:

- Yes.
- Yes, and don't ask again: this writes an allow rule for that command. It
  is offered only when the prompt can show everything the rule would allow.
- No, with a way to tell Claude what to do differently.

File edits have a session mode, accept edits, that stops asking for them
until the session ends. `/permissions` lists every rule with the settings
file it came from. An edit prompt shows the change as a diff.

**Codex** separates two controls. The sandbox says what the agent can touch
at all: read-only, the workspace, or full access. The approval policy says
when it pauses: before most actions (untrusted), when it judges something
risky (on request), or never. A prompt offers approving once, approving for
the session, or declining with an instruction. `/permissions` switches
between named profiles.

Both keep the same shape:

- a fixed boundary the agent cannot cross, and an approval layer inside it;
- answers that reach this call, this session, or always;
- a decline that can redirect the agent rather than only stop it.

## What Gamma took

- **The boundary exists already.** A chat's tools reach only its page or
  folder, and the changing ones only where the workspace role lets the user
  write. So Gamma needs no sandbox layer: the permission states are the
  approval layer inside that boundary.
- **Three states per capability** (Allow, Ask, Off) rather than pattern
  rules. Gamma's tools are few and named, so a rule per tool pattern would
  add syntax without adding control.
- **Reading allowed, changes asked** by default, matching an untrusted
  default for anything that changes the user's data.
- **The three reaches of an answer**: once, for this conversation, and
  always. A chat is Gamma's session, and always writes the setting like
  Claude Code's don't ask again.
- **A decline that redirects**: Don't allow takes an optional line saying
  what to do instead, and the model is told to follow it.
- **The change as a diff** on the card, word by word, because notes are
  prose and a one-word edit in a paragraph should read as one word.

Not taken: an "on request" policy where the model decides when to ask,
since asking is the user's setting, not the model's call. Per-argument rules
are left for when a tool needs them.

## Sources

- [Claude Code: configure permissions](https://code.claude.com/docs/en/permissions)
- [Codex: agent approvals and security](https://developers.openai.com/codex/agent-approvals-security)
- [Codex: sandboxing](https://developers.openai.com/codex/concepts/sandboxing)
- [Codex: configuration reference](https://developers.openai.com/codex/config-reference)
