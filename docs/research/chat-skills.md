# Skills in the chat: "/" commands after Claude Code and Codex

How Claude Code, Codex and the Agent Skills standard describe a skill,
where they keep it, how the user and the model invoke one and what enters
the conversation when they do; and why Gamma's skills took the shape they
have. Sources read on 2026-11-19 (listed under each section). The
mechanics as built are [dev/skills.md](../dev/skills.md).

## Claude Code and Agent Skills (Anthropic)

Sources: <https://code.claude.com/docs/en/skills>,
<https://code.claude.com/docs/en/commands>,
<https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview>.

- **Format.** A skill is a directory holding `SKILL.md`: YAML front matter
  between `---` lines, then a Markdown body. It may carry supporting files
  (`reference.md`, `scripts/…`) that the body links to by relative path.
  The docs advise keeping `SKILL.md` under 500 lines.
- **Front matter.** Every field is optional in Claude Code; `description`
  is recommended.
  - `name` defaults to the directory's name and is the `/` command.
  - `description` is what the model matches a request against, the key
    use first. `when_to_use` is appended to it.
  - `argument-hint` is the autocomplete hint (`[issue-number]`);
    `arguments` names positional arguments (`$issue`).
  - `disable-model-invocation: true` keeps the skill to the user, and its
    description out of the context. `user-invocable: false` hides it from
    the menu, for the model alone.
  - `allowed-tools` pre-approves tools for that turn (it grants, never
    restricts); also `disallowed-tools`, `model`, `effort`,
    `context: fork`, `agent`, `background`, `hooks`, `paths`, `shell`.
  - `license`, `compatibility` (at most 500 characters) and `metadata` are
    accepted and ignored. Unknown keys are ignored. Booleans take
    true/false, yes/no, on/off and 1/0.
- **Portability.** claude.ai uploads, the Skills API and `package_skill.py`
  allow only `name`, `description`, `license`, `compatibility`, `metadata`
  and `allowed-tools`, and refuse any other key ("Unexpected key(s) in
  SKILL.md frontmatter: argument-hint…").
- **Names and descriptions** (the platform and the API): a name of at most
  64 characters of lowercase letters, digits and hyphens, no XML tags, not
  `anthropic` or `claude`; a description non-empty, at most 1,024
  characters, no XML tags. The Claude Code page states no length for a
  name; it refuses control, zero-width, private-use and bidi characters,
  `<`, `>` and blanks other than single spaces, and reserves
  `anthropic-skills:*`. It shows `<` and `>` in descriptions escaped, so
  placeholders are better written `[x]` or as the `argument-hint`.
- **Where skills live.** Managed (enterprise) settings, the user's
  `~/.claude/skills/<name>/SKILL.md`, the project's `.claude/skills/` from
  the start directory up to the repository root (nested ones load once the
  model touches files there), `--add-dir` directories, plugins
  (`/plugin:skill`), and skills synced from the claude.ai account.
  Enterprise wins over personal over project; a user skill with a bundled
  skill's name replaces it. `.claude/commands/<name>.md` still works.
  Directories are watched; `/reload-skills` forces a reload.
- **Progressive disclosure.** Names and descriptions go into the system
  prompt (about 100 tokens a skill); the body (under 5k tokens
  recommended) loads when the skill is invoked; files load when read, and a
  script's output alone enters the context. The listing's budget is 1% of
  the context window (`skillListingBudgetFraction`,
  `SLASH_COMMAND_TOOL_CHAR_BUDGET`); over it, descriptions are dropped,
  the least-invoked skills' first, and names always stay. An entry's
  description is capped at 1,536 characters. `skillOverrides` sets a skill
  on, name-only, user-invocable-only or off.
- **Invocation and arguments.** `/name args` is recognised at the start of
  a message only; up to six skills chain (`/a /b text`). `$ARGUMENTS` is
  the whole argument string, `$ARGUMENTS[N]` or `$N` a positional one with
  shell quoting, `$name` one named in `arguments`. When no placeholder
  takes them, `ARGUMENTS: <value>` is appended. `\$` is a literal dollar.
  `${CLAUDE_SKILL_DIR}` and other session variables expand, and
  `` !`cmd` `` runs a command before the model sees the skill.
- **What enters the conversation.** The rendered `SKILL.md` goes in as one
  message and stays across turns; the file is not read again. A model
  re-invoking a skill whose render is identical gets a short "already
  loaded" note instead of a second copy; a different render is appended in
  full. After compaction the newest invocation of each skill is attached
  again (its first 5k tokens, 25k for all together). The model invokes
  skills through the `Skill` tool (`Skill(name)` in permission rules). How
  the invocation looks in the transcript is not documented; observed, the
  typed `/name args` shows as the user's message and the body is a hidden
  message.
- **The menu.** "/" lists built-in commands and skills together, filtered
  as one types. The top suggestion is highlighted only on a prefix or
  word-start match of a name or an alias (`/adddir` → `/add-dir`), and
  Enter runs it; after a typo nothing is highlighted, close matches stay
  for Tab or the arrows, and Enter sends the text as typed ("Unknown
  command"). No match reads `No commands match "/name"`. Synced skills are
  labelled with their source. `/skills` lists them, filterable, with each
  one's visibility. A command sent while a turn runs is queued. MCP
  prompts appear as commands too.

## Codex (OpenAI)

Sources: <https://developers.openai.com/codex/skills> (now
<https://learn.chatgpt.com/docs/build-skills>),
<https://learn.chatgpt.com/docs/cli/slash-commands>,
<https://simonwillison.net/2025/Dec/12/openai-skills/>; the third-party
sources are named where they are used.

- **Format.** The same `SKILL.md`, `name` and `description` required, with
  optional `scripts/`, `references/`, `assets/` and `agents/openai.yaml`
  (the display name, icons and default prompt; `policy.allow_implicit_invocation`,
  default true, false keeping a skill to an explicit mention; the tools it
  depends on). No length limits of its own.
- **Where skills live.** The repository's `.agents/skills` from the working
  directory up to its root, `$HOME/.agents/skills`, `/etc/codex/skills`,
  and the bundled `skill-creator` and `skill-installer`. Skills sharing a
  name are not merged. `~/.codex/skills` is a deprecated location kept
  working (source snippets quoted in
  <https://github.com/openai/skills/issues/420> and
  <https://github.com/vercel-labs/skills/pull/2067>, not checked against
  the source). `[[skills.config]]` in `config.toml` turns one off by path
  or name, `[skills.bundled] enabled = false` the bundled ones; a restart
  is needed.
- **The model's listing.** Each skill's name, description and file path;
  the body loads when the skill is chosen. The list is capped at 2% of the
  context window, or 8,000 characters when the window is unknown,
  **descriptions shortened first**, skills dropped with a warning past
  that (`skills.max_context_tokens`, up to 10k tokens). An entry reads
  `name: description (file: …/SKILL.md)`. The system text tells the model
  to use a skill the user names, or whose description clearly fits the
  task (<https://zenn.dev/takiko/articles/codex-cli-agent-skills-implementation?locale=en>).
  An explicit mention injects the body as a `SkillInstructions` item; an
  implicit match lets the model read the file itself (DeepWiki,
  <https://github.com/openai/codex/issues/18770>).
- **Invocation.** `/skills`, a picker that puts the skill's context into
  the next request, or `$` to mention one (`@` in ChatGPT); the "/" popup
  filters as one types, and Tab during a turn queues the command.
- **Arguments.** None: no `$ARGUMENTS` or `$1`; the arguments are the
  request in plain words. The older custom prompts (`~/.codex/prompts/*.md`,
  with named arguments) are deprecated, reportedly removed in 0.117.0
  (<https://github.com/openai/codex/issues/15941>,
  <https://github.com/openai/codex/issues/15316>,
  <https://developers.openai.com/codex/custom-prompts>); an open request
  asks for explicit-only skills
  (<https://github.com/openai/codex/issues/16172>).

## The open standard (agentskills.io)

Source: <https://agentskills.io/specification>.

`skill-name/SKILL.md` with optional `scripts/`, `references/` and
`assets/`. `name` is 1-64 characters of `[a-z0-9-]`, no leading,
trailing or doubled hyphen, the parent directory's name; `description`
1-1,024 characters saying what the skill does and when to use it.
Optional: `license`, `compatibility` (1-500), `metadata` (a string map),
`allowed-tools` (experimental). The guidance is the same disclosure: about
100 tokens of metadata, a body under 5k tokens, resources on demand. A
validator ships as `skills-ref validate`
(<https://github.com/agentskills/agentskills/tree/main/skills-ref>).

## What Gamma had

No note covered chat commands. The notes editor already had a "/" menu
(`editor/slashCommands.js`, its tiered ranking and fuzzy match, and
[dev/ui-design.md](../dev/ui-design.md)); the "@" page picker
(`.chatMentionPicker`) was the chat's one composer picker; Gamma already
ships an Agent Skills file for outside assistants
(`plugins/gamma/skills/gamma/SKILL.md`). The prompt-cache work
([prompt-length.md](prompt-length.md)) set the constraint that the system
prompt and the earlier turns read the same from one message to the next,
and the steer work ([steer.md](steer.md)) the queue a message waits in. A
Codex turn Gamma runs has its plugins, and with them their skills, off
([dev/local_agents.md](../dev/local_agents.md#trust)).

## What Gamma took, and why

- **The format: `SKILL.md`, four keys.** `name` and `description` as the
  standard has them (its name rule and its 1,024 characters), and Claude
  Code's `argument-hint` and `disable-model-invocation`. A file written for
  either tool imports as it is; the keys a chat with no shell has no use
  for (`allowed-tools`, `context`, …) are ignored, as Claude Code ignores
  unknown ones. The notes' front-matter reader reads it, in an opt-in
  reading (`blocks`) that adds the block scalars and comments hand-written
  SKILL.md files have; a parser of its own was built first and dropped in
  review, as a second reader of the same YAML subset.
- **"/" at the message's start, as both tools do,** with Claude Code's
  `$ARGUMENTS`, because a chat request usually has a subject ("for a
  journal club", "equation 3"). The message follows the instructions
  whole, so where the body has no placeholder the arguments read after
  it, Claude Code's rule, and where it has one they are in both places;
  taking them out of the message was built and dropped in review, as a
  rule over the text that a replay had to repeat exactly. Codex's plain-words
  arguments are what `load_skill` gets: the conversation is its input.
- **The menu after Claude Code's.** Keys taken only while a row can be
  picked, so an unmatched "/usr" and Enter sends the text as typed; "No
  skill matches"; the ranking the notes editor already had, extended to
  the description so a query in Chinese finds a skill.
- **The model's listing after Codex.** A fixed block of `- name:
  description` lines at the end of the agent prompt, capped at 50 skills
  and 8,000 characters with descriptions cut first, as Codex cuts them; no
  share of the context window, since Gamma's windows vary by connection
  and a listing that changed with the model would change the cached
  prompt. Sorted by name, so it reads the same while the skills stand.
- **The body is never stored.** Claude Code puts the rendered skill into
  the conversation once; Gamma's conversations are a workspace's, read by
  every member and a share, so a message keeps `{id, name, args}` and each
  request renders the instructions again from the requester's catalog.
  While a skill is unchanged the turn is the same bytes and the prompt
  cache holds; an edit costs one miss. Every successful `load_skill` is
  rendered again in full: Claude Code's "already loaded" note, tried as
  "only the newest load carries the text", was dropped in review, since a
  later load then changed an earlier turn's bytes and missed the cache.
- **Two sources now,** the built-ins and the account's own, in the account's
  prefs rather than files on disk: the server may be a NAS or a hosted
  service with no directory of the user's. Claude Code's precedence (a
  user skill replaces a bundled one of its name) holds. Admin-shared and
  workspace skills are further sources the catalog can take.

### Decisions of the plan review

The plan went through one round of review (GPT-6 Astra); these changed it:

1. A `load_skill` result stored the body in a shared conversation: the
   saved action keeps a stub and the skill's id, and replay renders it
   again.
2. A token's context export could read private skills: the catalog is the
   built-ins alone unless the request is a personal session, and the
   routes refuse tokens.
3. Shared history resolved another member's skill by name against the
   requester's: messages store the id and resolve by id only. The same ids
   keep a deleted override from falling back to the built-in.
4. An older computer would have run a skill turn without its
   instructions: the announce carries `skills`, and a turn for a computer
   without it is refused.
5. A skill with no arguments failed several "write a message first" gates:
   text or a skill is sendable everywhere. Computer Retry and a fresh
   session's earlier messages keep the skill.
6. A skill deleted before a computer's claim ran without it: the turn
   fails instead.
7. Quotes prepended before the split hid the "/": the raw text is split
   once, before anything joins it, and a queued message keeps the split it
   was sent with.
8. `$ARGUMENTS` differed between the turn and its replay: the stored
   `args` is rendered both times. `load_skill` takes no arguments, so a
   replay never truncates them.
9. A failed load replayed as a success, and exempt results were unbounded:
   only successful loads render, and the emergency retry elides them.
10. A stale catalog: the store refreshes on focus and after writes, and is
    unknown (no picker, nothing refused) before its first read. Two
    editors: a write names the version it read.
11. An unmatched picker swallowed Enter, and Return on an on-screen
    keyboard picked a row: the shared picker's key rules, for "@" too.
12. Titles and Copy dropped the command: both read `/name args`.

Declined: a capability handshake for an older web app talking to a newer
server (the server serves its own web app and every native app bundles the
two, so the skew is a stale tab a reload fixes), and binding a queued turn
to the skill's revision (the window is seconds long, and an edit made in it
is the owner's newer intent; a deletion is handled).

## Left open

The agents' own skills (Codex's skill list, Claude Code's `Skill` tool)
as a catalog source, which means revisiting what a turn Gamma runs may
load; a computer's agent loading a skill itself; skills shared by an admin
or a workspace; skills as MCP prompts; supporting files, `` !`cmd` ``,
positional arguments and `context: fork`; client commands (`/new`) in the
same menu; chaining; syncing skills through Gamma Cloud.
