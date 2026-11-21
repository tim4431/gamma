# Skills

A **skill** is a named set of instructions for one kind of request
("summarize a paper", "write a referee report"). A chat message that
begins with `/name` runs it: the instructions go to the model ahead of the
message, and what follows the name is what the skill works on. Gamma AI's
library agent may also load a skill itself, from a list of names and
descriptions, through the `load_skill` tool. A computer's agent (Codex or
Claude Code, [local_agents.md](local_agents.md)) runs a skill from "/" as
well. The format is the Agent Skills `SKILL.md`; why Gamma took it, and what
it took from Claude Code and Codex, is in
[the research note](../research/chat-skills.md).

`gamma/skills.py` owns the format, the catalog and every text the model
reads of a skill; `routers/skills.py` serves Settings; `ai_tools/skills.py`
is the tool. In the web app, `chat/skills.js` holds the pure pieces,
`chat/useSkills.js` the catalog store, `chat/ComposerPicker.jsx` the picker
shell, and `settings/SettingsSkills.jsx` the Settings sections.

## Sources

The catalog a chat sees has two sources:

- **Built-in skills**, `backend/gamma/builtin_skills/<name>/SKILL.md`
  beside the module (`BUILTIN_DIR`), read once per process (`builtins()`, cached). Three ship:
  `summarize` (a structured summary of the paper or pages in context),
  `review` (a referee-style critique: claims, evidence, weaknesses,
  questions for the authors) and `explain` (a passage, an equation or a
  concept at the level asked for). Each takes its arguments through
  `$ARGUMENTS` and says what to do when they are empty, so a body loaded
  by `load_skill`, which has none, still reads well. A file that does not
  parse, or whose `name` is not its folder's name, is skipped with a
  warning in the log. The desktop build carries the folder with one
  `--add-data` line (`desktop/build_backend.py`); Docker, the iPad and
  Android carry `backend/gamma` whole.
- **The account's own skills**, written or imported in Settings → AI →
  Chat, Your skills ("Storage" below).

An account skill with a built-in's name replaces that built-in in the
catalog. An account may also turn a built-in off. Skills an admin shares
with the server and skills a workspace shares are further sources the
catalog could take ("Left out").

## The format and its parser

A skill is a `SKILL.md`: front matter between `---` lines, then a Markdown
body. Four keys are read:

| Key | Field | Rule |
|---|---|---|
| `name` | `name` | required; lowercase letters, digits and single hyphens (`NAME_RE`), at most 64 characters; the `/` command |
| `description` | `description` | at most 1,024 characters; says what the skill is for, and is what the model matches a request against. Left out, the body's first paragraph is it, as in Claude Code |
| `argument-hint` | `argument_hint` | optional, at most 80 characters; shown in the picker after the name |
| `disable-model-invocation` | `model` (its negation) | optional boolean; `true` keeps the skill to "/" alone, so `load_skill` never loads it |

The body is at most 20,000 characters (`MAX_BODY`), a whole file at most
64 KB (`MAX_IMPORT`). `parse_skill_md` normalizes CRLF and a byte-order
mark, then reads the front matter with the notes' reader,
`markdown_import.parse_frontmatter`, in its `blocks=True` reading: besides
plain, quoted and list values, that takes a block scalar (`|` kept lines,
`>` folded, with their chomping and indentation marks, the text trimmed)
and drops a `#` comment outside a leading quoted value. A note's import
does not pass `blocks`, so its reading is unchanged. `parse_skill_md`
then keeps an unquoted value such as `[number]`, which the reader takes
for a flow list, as its text (`_text`), reads booleans as
`true/false/yes/no/on/off/1/0`, and ignores the keys it does not know
(`allowed-tools`, `license`, `metadata`, …), as Claude Code does. The
reader is lenient: a quote never closed is text, a line that is no
`key:` is skipped and an indented continuation of a plain value is
ignored. A file with no front matter, or none that closes, is refused
with "A SKILL.md starts with front matter between --- lines that names
the skill." A refusal is a `SkillError` whose message is shown to the
person as it is. `clean_fields` applies the same checks to the editor's
fields. The listing hands the client these limits (`LIMITS`, "Routes"),
so Settings states and enforces the same numbers.

## The catalog and ids

`catalog(user_id, personal)` is the effective skills of a request, sorted
by name: the account's own (`source: "account"`) when `personal`, then the
built-ins the account has neither turned off nor replaced. `personal` is a
signed-in session that is not a guest, a share link or an integration
token; everyone else gets the built-ins alone, so a token's request, a
context export or a share's visitor never reads a private skill's
instructions. A computer's agent works for its conversation's account,
whose catalog it uses (`personal=True`).

Every skill has an id. A built-in's is `builtin:<name>`
(`BUILTIN_PREFIX`), the same on every server and for every account. An
account skill's is `secrets.token_urlsafe(9)`, given when it is made, kept
through a rename, an edit and an import that replaces it, and never given
out again. `find(catalog, id)` looks a skill up by id alone, never by name.
That is what keeps a conversation safe to share: a member's message in a
shared workspace's chat names that member's skill by an id no other
member's catalog holds, and an account's override of a built-in, once
deleted, does not fall back to the built-in, since the ids differ.

## Running a skill from "/"

**The picker.** Typing "/" as the first non-blank character of the
composer opens a picker above the field (`skillAt`), in the "@" page
picker's shell (`.chatMentionPicker`, [ai.md](ai.md#mentioning-library-papers)).
A row shows `/name`, the description in two lines at most, the argument
hint (a built-in's both translated, `skillDescription` and `skillHint`
from `BUILTIN_TEXT`, which a unit test holds to the shipped files) and a
*Built-in* tag.
The query is any run of non-blank characters after the "/", so a Chinese
word finds a skill by its description. `rankSkills` orders the matches:
the exact name, a name the query begins, a word of the description as
shown (inside it for a query in Chinese, Japanese or Thai), then a fuzzy
match of the name (`fuzzyIndices`, `editor/slashCommands.js`), the matched
letters marked. ↑↓ choose, Enter, Tab or a tap insert `/name ` with the
caret after it (`insertSkill`), Escape closes. A query with no match says
*No skill matches “/x”* with *Manage skills…*, which opens Settings → AI →
Chat at Your skills (`openSettings("skills")`). The keys are `ComposerPicker.pickerKey`'s, which the "@" picker
uses too: the selection keys are taken only while the picker lists a row
(an "@" row that cannot be added, past the six-page limit, takes Enter
and does nothing), so with none listed Enter sends the text as typed; Ctrl/⌘+Enter keeps its meaning;
on an on-screen keyboard Return keeps its line break and a tap or Tab
picks. The field carries the combobox ARIA of whichever picker is open.

ChatDock offers skills (`offersSkills`) when the chat is not read-only,
the catalog has loaded and is not empty, and either Gamma AI is on or a
computer's agent is picked with no lent conversation shown. The
placeholder then ends with " (/ skill)".

**The invocation.** As a message is sent, ChatDock splits the composer's
raw text once, before quotes or anything else join it (`splitSkill`): text
that begins with `/name` followed by a blank or its end, with `name` in the
loaded catalog, runs that skill, `skill: {id, name, args}`, `args` the rest
trimmed; the message's text is then `args`, with the quotes around it as
usual. An unknown `/word` is text ("/usr/bin/env", "/ 2"). A message is
sendable when it has text **or** a skill, in every gate: the composer, the
queue, the edit bubble, Retry, the computer side's `ready` and the
server's checks.

The invocation is fixed when the message is sent or queued. A queued item
keeps the text as typed (`/name args`, which the queue row shows) and the
invocation in `parts.skill`; what goes is `chatQueue.sentText(item)`, the
arguments. Editing a queued row splits its text again. A message that runs
a skill never steers (`canSteer` is false): it waits as a turn of its own,
and the queue row says so.

**The bubble.** A user message that ran a skill shows a chip `/name`
(`uiTag chatMsgSkill`) before its text, titled *Ran the skill /name*. Copy
message copies `skillText(message)`, `/name args`; the chip carries
`data-mention="/name"`, so the transcript's Markdown copy starts the user
turn with `/name`. Edit keeps the chip with a × that drops the skill, and
Send is allowed with the chip alone. Resend, Retry and an edit carry the
message's `skill`. The live header and the History row read
`skillTitle`, the rule the server's titles follow ("Titles and
exports").

## What the model reads

`skills.turn_text(invocation, text, catalog)` writes the user turn of a
message that ran a skill:

```
The user ran the skill /summarize with this message. Follow its instructions.

<skill name="summarize">
…the body, each $ARGUMENTS replaced by the stored args…
</skill>

…the message as sent: the quotes, the arguments, what it points at…
```

The name in the block is the catalog's, so a renamed skill reads under its
new name. `$ARGUMENTS` is always the stored `args`, never the text as
displayed, so the current turn and its later replays render the same
string; `\$ARGUMENTS` is the literal word. The block is at most 40,000
characters (`MAX_RENDERED`), the arguments cut first. The message's text
follows the block whole, the arguments among it, whether or not the body
took `$ARGUMENTS`: nothing is taken out of it, as a computer agent's
prompt has it too. The rest of the turn is as for any
message ([ai.md](ai.md#chat-endpoint)): the message context and the
pictures' lines around it, and the document context still opening the
oldest turn.

`render(skill, args)` is the body alone, for `load_skill` and a computer's
agent: each `$ARGUMENTS` replaced by `args`, capped the same way; a body
without the placeholder is as written.

**A skill that is gone.** A history item whose `skill.id` the requester's
catalog does not hold reads `[This message used the skill /name, which is
not available here; its instructions are left out.]` ahead of its text
(`missing_note`). That covers a deleted skill, another member's private
skill, a token's request, a built-in turned off and a backup restored on
another server. The current message's skill, gone between the catalog's
read and the send, is refused before any provider call: `_chat_prompt`
raises `skills.SkillGone`, which `/api/ai/chat` and `/api/ai/chat/context`
answer 400 `{detail: "The skill /x was deleted or turned off.", kind:
"skill"}`, as `/api/local-agents/chat/turns` does. The request's
invocation is read once, by `skills.invocation` (`{id, name, args}`,
strings, capped at `MAX_ID`, `MAX_NAME` and `MAX_RENDERED`), on every
route that takes one. The chat reads its catalog again and, while the
composer is still empty and on the same bucket, puts the message back as
typed (`/x args`, with its parts: ChatDock's `giveBack`, the undoing of
`takeComposer`); the error card *That skill is no longer available*
offers *Manage skills…* and no Retry.

## Replay across turns

On every request, `ai_context.build_messages(..., skills=catalog)` renders
each earlier user turn that ran a skill again, from the requester's
catalog, by id. A conversation never stores a skill's body. While the skill
stands the turn is the same bytes, so the provider's prompt cache holds
([ai.md](ai.md#prompt-caching)); an edit costs one cache miss; a rename
keeps the id and still resolves. The client sends `skill` on each history
item that carries it ([ai.md](ai.md#replay-across-turns)).

## load_skill: the agent loads a skill itself

**When it is armed.** In a tool chat of Gamma AI, on a folder or a page,
when the request's catalog holds a skill with `model: true` and the
`skills` permission (*Use skills on its own*, Allow or Off, default Allow)
is not Off. The TOOLS entry's `needs: "skills"` is what
`registry.available` grants for such a catalog; MCP's tools exclude every
tool with a `needs`, so it is never an MCP tool. A plain chat gets no
listing and no tool, and still takes "/".

**The listing.** `prompt.agent_system` then ends with `skills.listing`:

```
Skills: instructions the user keeps for kinds of requests. When a request fits one, call load_skill with its name before answering, then follow it. A message that begins with “The user ran the skill …” already carries its skill.

- explain: Explains the selected passage, …
- review: A referee-style critique …
```

Only skills with `model: true`, sorted by name, so the block reads the same
while the account's skills stand. At most 50 skills in 8,000 characters
(`LISTING_SKILLS`, `LISTING_CHARS`): when it is longer, every description
is cut to the longest length that fits (Codex's rule), and skills are
dropped from the end only when the names alone do not fit.

**The call.** `load_skill {name}` takes the name alone (a leading `/` is
stripped): the conversation is its input. It answers with
`render(skill, "")`, the body with `$ARGUMENTS` empty. A name that is not
listed, or a skill with `model: false`, gets `error: no skill /x to load
…`, so a skill kept to "/" cannot be loaded by guessing its name.

**What is saved.** The action stored with the reply (`kind: "skill"`,
summary *Loaded skill x*, the chip *Loaded skill {name}* in
`agentSteps.js`, running as *Loading the skill {name}…*) keeps
`args: {name}`, the `skill_id` and the result `(the skill's
instructions)`, never the body. An executor may set `result` on its action
to store another text than the model got; `run_agent_tool` takes it
(`saved = action.pop("result", result)`). `STEP_ARGS` includes `name`.

**Replay.** A TOOLS entry may declare `replay(catalog, action) -> str`
(`registry.replayed`). The caller (`routers/ai.py`, the agent loop) hands
`registry.replayed` to `build_messages` and `elide_live_results` as
`replay=`, so `ai_context` does not import the tool registry; without it
nothing is rendered again. `ai_context._rendered_again` renders every
successful replayed `load_skill` call again from the requester's catalog
by `skill_id`, each as it renders now, a skill loaded twice included, so
a later load never changes an earlier turn's bytes. A failed load replays its saved error as any call does, and an id that is
gone reads the missing-skill note. These results carry no
`_REPLAYED_NOTE` (they are instructions to follow, not a snapshot of the
library) and stand outside `TOOL_REPLAY_BUDGET`.

**Within a reply.** `elide_live_results` keeps the results of a replayed
tool, outside the live budget, their round still counted. The agent loop's
emergency retry, when the provider still finds the prompt too long
(`keep_replayed=False`), elides them with the rest, to the entry's
`elided` stub, `(skill instructions elided — call load_skill again if
needed)`.

## A computer's agent

The chat's computer side ([local_agents.md](local_agents.md#the-chats-computer-side))
posts `skill: {id, name, args}` with the turn (`computerChat.turnBody`).
`routers/local_agents._skill_of` checks the id against the session
account's catalog: one it does not hold is 400 `{detail, kind: "skill"}`
(`skills.SkillGone`), as the library chat answers, and the client reads
its catalog again. The stored invocation takes the catalog's current name
and the client's `args`, never the message text. "Write a message first."
is for a body with neither text nor skill. `chats.begin` keeps the
invocation on the person's message, titles a new conversation with
`skills.title` and writes it as `/name args` in the earlier messages a
fresh session is given (`earlier_of`, `skills.display`), the instructions
never repeated.

At the claim the instructions are rendered again from that account's
catalog by id (`chats.skill_of`, `{name, instructions}`, `render` with the
stored `args`): the arguments replace `$ARGUMENTS` where the body has one,
and the prompt's message holds them whole either way. `prompt.build_chat` puts a section before
`## The message`:

````
## The skill /summarize

tim ran their skill /summarize with this message; its instructions below are part of tim's request.

```markdown
…the instructions…
```
````

The instructions pass through `_safe`, so the body's own fences cannot
close the block
([local_agents.md](local_agents.md#the-prompts)). A skill deleted between
the send and the claim fails the turn with "The skill /name is no longer
available, so the agent did not start." (`chats.SKILL_GONE`, `runner`):
it never runs the arguments without the instructions.

**On another computer.** The server that keeps the conversation renders
the instructions and sends them with the claimed turn,
`turn.skill: {name, instructions}`, only for a message that ran a skill
(`far_turns.claim`; a skill gone there fails the run and the claim answers
409). The computer need not have the skill; `HttpChatHome.claim` reads it
through `home._skill`, which checks the name (`skills.safe_name`) and caps the instructions,
and fails the turn for any other shape. A computer says it can run them
with `skills: true` in its announce (`computers/channel.state_of`), kept
beside `sessions` in the live registry (`registry.runs_skills`). A skill
turn for a computer that did not say so is refused at its start with 409
"Update Gamma on {computer} to run skills there."
([computers.md](computers.md)), so an older app never runs the arguments
alone.

The steer route refuses a body with `skill`, 409 "A message that runs a
skill goes as a turn of its own."; the client queues such a message
instead. Retry (`retryOf`, `useComputerChat`'s `ready` and `send`) carries
the skill.

## Titles and exports

`skills.display(invocation, text)` is a message's text as `/name` then its
text, on a line of its own after a blank line when it opens with a quoted
passage; `/name` alone when the text is empty. The earlier messages a
fresh session is given (`earlier_of`) and `chat_export.py`'s `## You`
text (both transcripts, Gamma AI's and a computer agent's) use it, and the
client's `skillText` (Copy message) writes the same text.

`skills.title(invocation, text)` is a conversation's title when the user
never gave one: the first non-blank line that is not a quote, after
`/name` when the message ran a skill, its blanks folded to one space and
cut to 80 characters (`MAX_TITLE`). `routers/chats.derive_title` and
`local_agents/chats.begin` use it, and the client's `skillTitle` (the
live header, the History row) follows it. Both rules read only the text,
never `args`. `tests/shared/skilltitles.json` holds their cases, which
`tests/test_skills.py` and the frontend's `tests/chatSkills.test.mjs` both
read, so the client and the server give the same answers.

## Settings → AI → Chat: the skills sections

**Your skills** and **Built-in skills** are two sections of the Chat pane,
after Prompts and before Context size ([settings.md](settings.md)); there
is no pane of their own. `openSettings("skills")` (*Manage skills…*, the
vanished skill's card) opens the pane at Your skills
(`settingsNavigation.js` `SECTIONS`). They read the chat's own store,
`useSkills()` (`{rows, ready, error, limits}`, "Freshness"), not
`PANE_READS`, so the "/" menu and Settings share one `GET /api/ai/skills`,
without bodies; a skill's instructions are read as its editor or viewer
opens. Every number they show or enforce (lengths, the 50-skill cap, the
import's size) comes from the listing's `limits`; only the name's pattern
(`NAME_RE`) is kept on the client too, for the live hint, the server's
check being the rule.

- **Your skills** lists the account's: `/name`, the description, *Only
  when you run it* for `model: false`, Edit and a delete button
  (`uiBtn sm iconSq danger`, named for the skill, confirmed, sent with
  the `updated_at` it read). The rule's actions are *Add skill* and
  *Import SKILL.md…*, both off at the cap. Import picks a `.md` file
  (refused over `limits.import` before it is read) and posts its text; a
  name the account has already asks Replace or Cancel. `Empty` explains
  skills before the first one; failures are a `FormNotice`.
- **The editor** is a `SubDialog` over `useDraftOf`, with the dismissal
  guard: the name with a fixed `/` before it, checked as it is typed
  (format, length, names taken); the description; the argument hint; the
  instructions, a wide textarea whose hint explains `$ARGUMENTS`; and *The
  assistant may use it on its own*, on by default. A save sends the
  `updated_at` the editor opened with; a 409 *changed* keeps the draft and
  offers *Load the newer version*.
- **Built-in skills** lists the server's, each with its translated
  description, View (a read-only `SubDialog` with the instructions and
  *Copy to your skills*, which opens the editor on a copy under the same
  name, which then replaces it) and an on/off switch. A built-in an
  account skill replaces says *Replaced by your skill* and has no switch.
- **Guests** see the built-ins, with no actions, the switches disabled and
  no Copy; the server answers their writes 403. Integration tokens cannot
  use the routes at all.

Every write, a 409 on delete and Try again call `refreshSkills()` once, so
the "/" menu has the change at once with one read. Settings search finds
the two sections in the Chat pane and the Tool usage row.

## Routes

All under `/api/ai/skills` (`routers/skills.py`), session only: a read
takes `require_user_id`, which refuses an integration token as every
account endpoint does; a write takes `require_personal_user_id` (a guest's
is 403). A refusal is 400 `{detail}`, 404 for no such skill.

| Method | Path | What |
|---|---|---|
| GET | `/api/ai/skills` | `{skills: [{id, name, description, argument_hint, model, source: "account" \| "builtin", effective, off?, replaced?, created_at?, updated_at?}], limits: {name, description, hint, body, import, skills}}`: every account skill (a guest has none) and every built-in, sorted by name, an account skill before the built-in it replaces; `effective` whether it is in the catalog; `limits` the server's caps (`skills.LIMITS`: characters, the import's bytes, the account's count) |
| GET | `/api/ai/skills/{id}` | the row plus `body` |
| POST | `/api/ai/skills` | `{name, description, argument_hint?, body, model?}` → 201 with the row; 409 `{detail, kind: "name", id}` for a name the account has; 400 at 50 skills |
| POST | `/api/ai/skills/import` | `{text, replace?}`, a SKILL.md's text → 201 with the new skill, or 200 when `replace` overwrote the one of its name in place (its id and `created_at` kept); a clash without `replace` is 409 `{detail, kind: "name", id}` |
| PUT | `/api/ai/skills/{id}` | `{name, description, argument_hint, body, model, updated_at}` → the row; 409 `{detail, kind: "changed", skill}` (with its body) when `updated_at` is not the stored one; 400 for a built-in |
| DELETE | `/api/ai/skills/{id}?updated_at=` | → `{ok: true}`; 409 when stale; 400 for a built-in |
| PUT | `/api/ai/skills/builtin:{name}/state` | `{off}` → the built-in's row; 404 for an unknown built-in |

## Storage

The account's skills live in users.db `user_prefs` under the account-wide
key `skills` (`PREF_KEY`, in `db.USER_PREF_KEYS`):

```
{"skills": [{id, name, description, argument_hint, body, model, created_at, updated_at}],
 "off": [names of the built-ins turned off]}
```

`/api/prefs` refuses the key (`routers/prefs._check_key`); the skill routes
are the only way in. Every write is one `db.update_pref`
read-modify-write: the name check and the `updated_at` check run inside
that transaction, and every edit moves `updated_at` forward
(`stamp_after`). The key is not synced to Gamma Cloud and not in the
`profile`. A pref value is opaque JSON, so no migration step was needed,
and deleting an account removes its `user_prefs` rows. At most 50 skills
an account (`MAX_SKILLS`).

A message that ran a skill keeps `skill: {id, name, args}`: a user message
of the pages.db `chats` and `chat_history` tables
([chat_history.md](chat_history.md#stored-conversations)) and a person's
message of `computer_chats`. A stale tab, the only way the web app and its
server differ (the server serves its web app; the desktop, iPad and
Android apps bundle both), replays such a message as its arguments alone
until it reloads.

## Freshness

`chat/useSkills.js` is one store for the window (`useSyncExternalStore`),
shared by the picker and the split. It reads `GET /api/ai/skills` when a
reader mounts, again when the window gains focus (one listener for all
readers) and after any write the Settings sections make (`refreshSkills`).
It keeps the listing's `limits` beside its rows.
Reads are numbered and only the newest lands; what it holds is kept with
the account it was read for, so another account signing in on the tab
starts unknown; on a share link it reads nothing. Until its first read
lands the catalog is unknown (`ready` false): "/" opens no picker and a
`/word` goes as text, never refused.

## Tests

`tests/test_skills.py` (the shipped files, the parser's cases, the routes
with their 409s, 403s and the caps the listing names, the reserved pref
key, `render`, `invocation` and `safe_name`, the shared title cases),
`tests/test_skills_chat.py` (the expanded turn and its replay byte for
byte, the missing note and the 400, the token's catalog, another member
continuing the conversation reading the note, `load_skill`'s arming,
refusal, stub, replay and emergency elision, the listing, titles and
exports), `tests/test_local_agents_skills.py` with steps in
`test_computer_chats.py` and `test_remote_chat.py` (the stored invocation,
the prompt's section, a skill gone before the start or the claim, the
older computer's 409, the steer refusal, `earlier_of`), and in the
frontend `tests/chatSkills.test.mjs` (`skillAt`, `splitSkill`, the ranking
with a non-Latin query, the shared title cases, `BUILTIN_TEXT` against
the shipped files). The browser group `chat-skills`
(`tests/e2e/scenarios/chatSkills.mjs`) drives the menu, a send with its
chip and copy, dismissal and an unmatched `/word` sent as text, *Manage
skills…* opening the Chat pane at Your skills, a skill made in Settings
and a built-in turned off reaching the menu without a reload, the draft
restored after a vanished skill, Retry and an unchanged edit posting the
skill again, and the edit bubble's × dropping it; the local-agents group
picks a skill from the menu and checks the chip and the skill it stores
and posts.

## Left out

- **The agents' own skills.** Codex's skill list and Claude Code's skills
  are not offered: a Codex turn Gamma runs has its plugins off, and Claude
  Code runs under a `--tools` list without `Skill`
  ([local_agents.md](local_agents.md#trust)). They would be another
  catalog source, and mean revisiting that trust section.
- **A computer's agent loading a skill itself** (an MCP `load_skill` on
  the turn's tools).
- **Skills shared by an admin with the server, or by a workspace's
  members**, as further sources.
- **Skills as MCP prompts** for outside assistants.
- **SKILL.md features with no place in a chat without a shell:** supporting
  files, `` !`cmd` ``, `$0` and `$1`, `arguments`, `allowed-tools`,
  `context: fork`.
- **Client commands** such as `/new` or `/clear` in the same menu, chaining
  skills (`/a /b`), and syncing an account's skills through Gamma Cloud.
