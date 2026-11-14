# Gamma MCP, Codex, Claude Code, and DeepSeek Harness

Gamma exposes a Streamable HTTP MCP endpoint at `/mcp`, on the same server as
the app. It reads, and writes nothing but the answers of a connection the user
allowed to answer feedback: a reply in, or the resolve of, a comment thread on
a work folder's file ("Answering feedback" below). Gamma's chat and MCP
adapter share `gamma/ai_tools.py`: tool definitions, executors, page/folder
scope checks, and dispatch permissions. The chat calls these functions
directly. Gamma is not an MCP client for external servers, and connecting
Codex does not invoke Gamma's AI provider.

| File | Owns |
|---|---|
| `gamma/mcp_server.py`, `gamma/mcp_lazy.py` | the `/mcp` transport (official Python MCP SDK): the chat registry's read tools (`READ_TOOLS`), link reading, page export, a feedback connection's answering tools; `mcp_lazy.py` authenticates the request and loads the SDK adapter on first use |
| `gamma/feedback/agent.py` | `reply_feedback` and `resolve_feedback`: their schemas and executors ([feedback.md](feedback.md)) |
| `gamma/mcp_oauth.py`, `gamma/mcp_oauth_provider.py` | discovery, dynamic registration, PKCE authorization and token exchange, the consent API (`/api/integrations/oauth/*`), `public_base`; the provider issues the 90-day token through `integrations.create_token` |
| `gamma/mcp_links.py` | local page/block/share link resolution within the connected workspaces; which of them holds an id (`holders`: a block, a comment thread, a Send request) |
| `gamma/mcp_export.py` | `export_page`: a page as Markdown text or an embedded PDF, over `routers/export.py` |
| `gamma/integrations.py` | integration tokens (`integration_tokens` table, hashes only), the workspaces each may read (`grant`, `resolve_connection`) and their resolution |
| `gamma/server_settings.py` | the admin-confirmed public URL and the MCP host allowlist |
| `gamma/routers/integrations.py` | the session-only connection management API, account-wide; a setup command's sign-in for a client without its own |
| `users.db` tables `integration_tokens`, `mcp_oauth` | part of the baseline schema (steps 0–19 are folded into it); `integration_tokens.workspaces` is step 51 ([migrations.md](migrations.md) "Versions") |
| `frontend/src/settings/SettingsIntegrations.jsx`, `frontend/src/settings/WorkspacePicker.jsx`, `frontend/src/auth/McpConsent.jsx` | the Integrations pane, the workspace checklist it and the consent screen share, the consent screen |
| `plugins/gamma/`, `tools/package_plugins.py`, `tools/release_plugins.py`, `tools/install-gamma-*.{ps1,sh}`, `.github/workflows/codex-plugin.yml` | the shared Codex / Claude Code plugin, the DeepSeek Harness bundle in the same directory, their setup scripts and packaging |

## Connect

Open Gamma in your browser and go to **Settings → AI → Integrations**. The
pane is the account's, whichever workspace is open: **Connect an assistant**
has a tab per assistant (Claude Code, Codex, DeepSeek Harness, Other) with its
steps, **Connected assistants** lists every connection with the workspaces it
reads, and **Tokens (advanced)** makes a token by hand. One connection may read
several workspaces: they are ticked at sign-in (the consent screen's
checklist, the open workspace ticked to start) or in the DeepSeek Harness tab,
and changed later from the connection's row (**Choose workspaces**) without
connecting again. **All workspaces, and those added later** grants every
workspace the account can open, now and to come. The Codex tab's command:

```text
codex mcp add gamma --url https://gamma.example.com/mcp
```

Codex opens the browser. If sign-in is interrupted, run `codex mcp login gamma`
to retry. Sign in with your Gamma account, tick the workspaces, mark any
whose notes it may also edit **Edit** ("Editing notes"), and approve, or turn
on **Answer my comments on work-folder files** to let it reply to and
resolve them ("Answering feedback").
Start a new Codex chat. There is no token to copy
and no environment variable to set. This works with local browser-based Gamma
and self-hosted servers; the Gamma desktop app is not required. Local installations
may use `http://127.0.0.1:8000/mcp` (substitute your actual port).

A `gamma` entry configured with `bearer_token_env_var` or an Authorization
header must be removed (`codex mcp remove gamma`) and added again with just the
URL before signing in. Installing the optional plugin does not install this
per-user connection. A browser cannot directly edit Codex's configuration on
your computer.

### Claude Code

**Settings → AI → Integrations → Claude Code** (the pane's first tab). The
connection command uses this Gamma server's MCP URL, with quoting for the
selected terminal platform:

```text
claude mcp add --transport http --scope user gamma https://gamma.example.com/mcp
```

The same tab's **Optional: the /gamma:gamma workflow** explains how to install
the plugin from an extracted release, and **Changed the server address?** gives
commands that replace the user-scoped MCP connection without reinstalling the
plugin. Confirm a changed remote public URL in **Settings → Server** first, then
reopen the tab and sign in again. Frontend command generation lives in
`frontend/src/settings/assistantSetup.js`; every command shares its
shell-literal quoting, and the Codex and DeepSeek Harness ones its
download-then-run form (`releaseScript`).

Start Claude Code and open `/mcp`. Select `gamma` and authenticate in your browser;
sign in to Gamma and tick the workspaces. Start a new session. Check `/mcp` or
`claude mcp get gamma` if tools are unavailable. Each assistant maintains its own
connection and authorization; an existing Codex login does not connect Claude Code.
See the [Claude Code MCP documentation](https://code.claude.com/docs/en/mcp).

For the optional Gamma workflow, extract `gamma-claude-code-plugin-X.Y.Z.zip`
from a Gamma release into a permanent directory, then run:

```text
claude plugin marketplace add <permanent-directory>/gamma-marketplace
claude plugin install gamma@gamma-local --scope user
```

Start a new session and use `/gamma:gamma`, or ask about your Gamma library.
The plugin uses the separately configured connection. To update after refreshing
your extracted package or published marketplace, run
`claude plugin marketplace update gamma-local` and
`claude plugin update gamma@gamma-local`. Restart the session.

For local development from this checkout, use `claude --plugin-dir ./plugins/gamma`.
This loads the shared workflow without creating a marketplace; connect MCP separately.
See the [plugin README](../../plugins/gamma/README.md) for both clients' setup.

### DeepSeek Harness

[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`) has
no plugin marketplace; its plugins are npm packages that declare a **bundle**
(`"dsh": {"bundle": {"patch": ...}}` in `package.json`), installed into a dsh
profile with `dsh plugin --profile <name> add <package>`, which runs pnpm in the
profile directory. `plugins/gamma` is such a bundle as well as the Codex /
Claude Code plugin:

| File | Role |
|---|---|
| `package.json` | name `dsh-gamma`, the bundle declaration, `files`, the peer dependencies the running dsh supplies (`@deepseek-ai/dsh-mcp-client`, `@deepseek-ai/dsh-skill-filesystem`); version and description kept equal to the plugin manifests (the builder checks) |
| `cordis.patch.yml` | inserts one row, `gamma`, naming `./dsh-gamma.js` |
| `dsh-gamma.js` | mounts dsh's filesystem skill provider on this package's `skills/` and, when this computer is connected, dsh's MCP client on Gamma (Streamable HTTP, `Authorization: Bearer`) |

dsh's MCP client sends fixed headers and has no OAuth, so it connects with a
token, and its config takes only literal values or `!!js` expressions. A patch
row can name only a plugin module relative to the bundle, so `dsh-gamma.js`
reads the connection itself when dsh starts: `$DSH_HOME/gamma.json`
(`{"url": ".../mcp", "token": "gamma_..."}`, default home `~/.dsh`), which the
setup script writes, or `GAMMA_URL` / `GAMMA_TOKEN` in the environment, which
take its place. One file serves every profile and every way of starting dsh,
and the package holds no per-user values. Without a connection the module logs
a warning and mounts the skill alone, so dsh still boots (a failing row would
stop it).

**Setup.** **Settings → AI → Integrations → DeepSeek Harness** has the same
three steps as Codex: run the setup command, choose what it may read in the
browser, and open DeepSeek Harness. The command, `dshSetupCommand`
(`assistantSetup.js`), names only this server's MCP URL, so the tab shows it
at once: it downloads `install-gamma-dsh.ps1` / `.sh` from the latest release
and runs it with that URL. The script:

1. finds the dsh CLIs: the desktop app's own
   (`%LOCALAPPDATA%\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd`,
   `/Applications/DeepSeek Harness.app/…/cli/bin/dsh`, `/opt/DeepSeek Harness/…`),
   a `dsh` on `PATH`, or `npx @deepseek-ai/dsh`;
2. picks the profiles: the desktop app's `desktop` profile where the app is
   installed, `web` where that profile exists, else `web` (`-Profiles` / extra
   arguments name others). The desktop profile belongs to the app: only its own
   CLI manages it, after the app has initialized it once and while the app is
   fully quit (dsh refuses otherwise; the script says so). The script stops at
   once while the app runs (a process named `DeepSeek Harness`; `pgrep -f`
   on macOS and Linux, where its path names it), before it installs, asks or
   removes anything: the app reads the plugin and `gamma.json` only when it
   starts, and one left open went on without Gamma's tools while the skill
   it had loaded before said nothing was wrong;
3. adds the plugin to each profile whose `node_modules/dsh-gamma` is missing or
   another version: the release's pinned `dsh-gamma-X.Y.Z.tgz`, checked against
   its SHA-256 and saved as `$DSH_HOME/dsh-gamma.tgz`, the one file every
   profile names. pnpm cannot add a remote tarball it resolved before (a second
   profile or a re-add fails with `ERR_PNPM_MISSING_TARBALL_INTEGRITY`); a local
   file installs into any number of profiles and updates in place. pnpm warns
   about the unresolved peer dependencies; the running dsh provides them;
4. signs in for dsh, which has no browser sign-in of its own ("A setup
   command's sign-in" below): it asks Gamma for a sign-in, prints its code,
   opens `/?gamma_device=<code>` in the browser (an address it builds itself,
   never one from the reply), where the user checks the code, ticks the
   workspaces and allows it, and asks every two seconds until it may collect
   the connection's token;
5. writes the token to `gamma.json` (no byte-order mark: dsh parses it as
   JSON; mode 600 on macOS / Linux).

A failure in steps 1–3 asks nothing of Gamma, so the same command can run
again; so can one whose sign-in expired or was cancelled. A command from a
Gamma before this sign-in passes a one-time setup code (`-SetupCode`, the sh
script's second argument), which the script still claims
(`POST /api/integrations/claim` on that server). Connecting again (a new
token, other workspaces) finds the plugin in place and only saves the new
connection, so the app need not be quit; it reads the file at its next start.
`-Bundle <tarball>` (`GAMMA_DSH_BUNDLE` for the sh script) installs a local
build instead of the release's, for development; `-Remove` undoes the setup
("Remove Gamma from an assistant" below). The tab works without the
confirmed public address the other assistants' browser sign-in needs: the
sign-in is the account's own session on the page the script opened.

The tools appear as `mcp__gamma__<tool>` and the skill as `gamma`. An expired or
revoked token shows in Gamma's log as a 401 `authentication-required` on `/mcp`;
dsh then starts without the tools.

**Build.** `tools/release_plugins.py` writes `dsh-gamma-X.Y.Z.tgz`, a
byte-identical `dsh-gamma.tgz`, the two setup scripts with that version, its
download URL and digest pinned in (`__GAMMA_DSH_*__`), and
`gamma-dsh-SHA256SUMS.txt`. `write_dsh_bundle` in `tools/package_plugins.py`
builds the tarball in Python (npm layout, `package/` prefix, fixed timestamps,
reproducible), stamping the release version into `package.json`.
`tools/test_release_dsh_plugin.py` runs the real scripts (PowerShell on
Windows, sh elsewhere) with only the download, Gamma's replies (a sign-in
that waits once, then is allowed or cancelled; an older Gamma's claim), the
browser and the dsh CLI stubbed. For local development, `dsh plugin --profile web add ./plugins/gamma`
links the checkout (dsh 0.1.7 or later), or run the setup script from
`tools/` with `-Bundle` and a tarball from `write_dsh_bundle`. Verified against
dsh 0.2.2-rc.1 (the Windows desktop app's CLI and pnpm 11): the plugin added to
a desktop profile, the code claimed, a booted profile reading `gamma.json`,
initializing with Gamma and listing its tools, and `-Remove` taking the plugin
out of a desktop and a web profile; earlier, 0.1.5-rc.3 and
0.1.7-rc.1 with the environment variables.

### Tokens (advanced)

For other clients without OAuth, or a script, expand **Tokens (advanced)** in
the same pane, name the token, pick its access (read, read and answer
feedback, or read and write), tick the workspaces it may read (a read and
write token is for the open workspace alone), and copy it once. Guests cannot
create connections. The client sends it as `Authorization: Bearer <token>`.
Codex, for example, reads one from the environment:

```powershell
$gammaSecret = Read-Host 'Gamma token' -AsSecureString
$env:GAMMA_TOKEN = [System.Net.NetworkCredential]::new('', $gammaSecret).Password
codex mcp add gamma --url http://127.0.0.1:8000/mcp --bearer-token-env-var GAMMA_TOKEN
codex
```

Replace the address/port with the one shown in Gamma. A desktop Codex process
must inherit the variable; restarting an app launched elsewhere may not inherit
a terminal's variables. Never put tokens in prompts, screenshots, source control,
or plugin packages.

### Remove Gamma from an assistant

Each tab ends with **Remove Gamma from …**, folded: the commands that undo its
setup on that computer (`claudeRemoveCommands`, `codexRemoveCommands`,
`dshRemoveCommand` in `assistantSetup.js`). Removing them ends nothing on
Gamma's side, so the tab says to disconnect the connection under **Connected
assistants** too, which revokes its token.

| Assistant | Commands |
|---|---|
| Claude Code | `claude mcp remove gamma --scope user`; for the plugin, `claude plugin uninstall gamma@gamma-local --scope user` and `claude plugin marketplace remove gamma-local` |
| Codex | `codex mcp remove gamma`, `codex plugin remove gamma@gamma-local`, `codex plugin marketplace remove gamma-local`; the plugin source under `%LOCALAPPDATA%/Gamma/codex-plugin` (`~/.local/share/gamma/codex-plugin`) can then be deleted |
| DeepSeek Harness | the setup script with `-Remove` (`--remove` for sh), with the app quit: `dsh plugin --profile <p> remove dsh-gamma` for every profile whose `package.json` names `dsh-gamma` (or those `-Profiles` names), which drops the package and its entry in `dsh.profile.bundles`, then deletes `$DSH_HOME/gamma.json` and `dsh-gamma.tgz`. A profile without it is left alone: pnpm refuses to remove what a profile lacks. A failed removal keeps the connection file, so the command can run again |

Ask Codex to find a page, search a topic, or summarize notes. Tools available:

| Tool | Content |
| --- | --- |
| `list_folders` | The folder tree, each folder with its id, its path ("a / b") and its direct and total counts of library pages (empty folders too); pages in no folder |
| `list_pages` | Page IDs/titles, a work folder's file's path in it (`path=`), folders (paths) or, for a page inside another page, that page's id (`parent=`), labels, the document's format and size; filters by folder (a path or an id), label, title and the page they are inside (`parent`); most recently added first, in batches (`limit`, `after`) |
| `search_library` | Full-text note, PDF and file matches, with source locations: a block, a PDF page, a file's path and line |
| `read_page` | The pages it is inside and the pages inside it, notes, highlights, open comment threads, properties, and the document's text in windows: a PDF's by page, a text file's numbered lines by `from_line` / `to_line`; a work folder's file named by its `path` too |
| `read_block` | One block/subtree or a page's nested note outline |
| `read_chats` | The AI chat kept with a page or folder: the current conversation as a transcript, earlier ones by `chat_id` |
| `list_feedback` | The feedback: the comment threads on a work folder's files (a work folder by name or id, a directory, a file; every work folder without one), each with its file, place, source line and comments, as Markdown, in batches (`limit`, `after`); a Send request's threads (`request`) |
| `read_feedback` | One feedback item in any state, as Markdown and as JSON, with the pictures of its region now and as it was commented on |
| `view_pdf_page` | One PDF page as an image (a scan, a figure, a table's layout) |
| `view_ink` | The user's handwriting as an image: a group's strokes on their PDF page or sheet of paper, or a whole page with all its handwriting |
| `view_image` | The pictures a note block embeds (`![alt](/api/uploads/…)` in its markdown), all or one by index |
| `cite` | The citation records kept with pages: the paper metadata, its BibTeX, the slide citation |
| `list_workspaces` | The workspaces the connection reads, the default first: each one's id, name and the account's role |
| `read_gamma_link` | Resolve and read a page, block, folder, or share URL, including PDF page and line context (`line=N` reads the file from that line, `line=N-M` those lines); a folder or folder-share URL lists the folder's pages |
| `export_page` | One page as Markdown text, or as a PDF file: the annotated paper or the notes typeset; with `sub_pages`, the pages inside it too |
| `reply_feedback` | A feedback connection's only: a reply in a feedback item's thread, signed with the connection's name |
| `resolve_feedback` | A feedback connection's only: resolve a feedback item once it is done (never a reopen) |

The first twelve are the Gamma chat's own tools (`gamma/ai_tools.py`, described
in [ai_tools.md](ai_tools.md)), run through the same dispatcher with a
workspace-wide, non-writable scope. `READ_TOOLS` in `mcp_server.py` comes
from `ai_tools.mcp_tools`: every reading tool that stays inside the library.
A new reading tool is offered without touching the adapter; `test_mcp.py`
pins the names. The chat's other tools stay off: the web tools (`search_papers`,
`fetch_paper`, …), because the assistant has its own web access;
`list_deleted`, because Recently deleted is out of the reading tools' reach
and restoring is a change; and every write tool of the chat but the four note
tools a connection gets where the user let it edit ("Editing notes"; a
feedback connection's answers are its own two tools, below). The chat's tool
permissions (Settings → AI → Tool usage) do not govern this connection: the
token is its own grant, created, changed and revoked in Integrations.
`list_workspaces`, `read_gamma_link`, `export_page` and the two answering
tools exist only here.
`view_pdf_page`'s, `view_ink`'s, `view_image`'s and `read_feedback`'s pictures
go out as MCP `image` items, which the client shows the model as images.

### Several workspaces

A connection reads the workspaces its token grants that the account can still
open (`integrations.resolve_connection`), its own workspace first: the
default. Every tool but `list_workspaces` and `read_gamma_link` takes an
optional `workspace`, an id or a name (`mcp_server._targets`). Without one:

- a call naming a block by id (`page_id`, `block_id`, `parent`, `after`,
  `folder`, `page_ids`, `list_feedback`'s `scope`), a comment thread
  (`thread_id`, `list_feedback`'s `after`) or a Send request
  (`list_feedback`'s `request`) reads the workspace holding it
  (`mcp_links.holders`: a thread by its root's id, a hidden one held by
  none); ids in two workspaces are refused with their names, asking for
  `workspace`;
- `search_library`, `list_folders` without a `folder`, and `list_feedback`
  without a `scope`, a `request` or an `after`, read every workspace, one
  section each, headed by its name and id with its link shapes;
- anything else reads the default workspace (a work folder `list_feedback`
  names by its name, too).

With more than one workspace, a single-workspace result starts with the
workspace's name and id, and its `structuredContent` carries `workspace_id`.
A pasted link reads in the workspace it names (`?ws=`, a share token's
prefix), else in the one holding its page or block; one in a workspace the
connection does not read is refused. A connection of one workspace answers as
before, without the extra line.

### Links in results

Every result starts with one line naming this server and workspace's link
shapes: page, PDF citation, file line (`…&page=<id>&line=<N>[-<M>]&quote=<the
first line's verbatim text>`), note block. Each located hit carries its own
absolute URL:

- a search hit links its PDF page (`…&page=<id>&pdf_page=N`), its file's
  line (`…&page=<id>&line=N`) or its note block (`…&block=<id>`);
- `read_page` ends with a `[Links: …]` line naming the page and the
  citation form of the window's PDF pages or of its first line;
- `read_block` and `cite` link in their head lines;
- `view_pdf_page` links beside its page number.

The executors add these only when the scope carries `link_base`
(`ai_tools.gamma_link`), which the adapter sets to `<public URL>/?ws=<id>`.
The chat's results carry no URLs; its model writes the relative links the
chat renders itself. A `Page URL:` footer follows a result that names a page
without linking it (`read_chats`). The call's location goes out as
`structuredContent` too: `page_id` and `url`, with `block_id`, `pdf_page`,
`pdf_pages`, a text file's `lines` (`[first, last]`) or its `path` when the
tool located one. A line link (`mcp_links.resolve_link`: `line` is `N` or
`N-M`, `1 ≤ N ≤ M ≤ 10,000,000`, never beside `pdf_page`) reads as
`read_page(page_id, from_line=N)` (the window's budget decides where it
ends) or with `to_line=M` for a range; its reference carries `line` and
`end` (null for one line), and its canonical URL keeps the line. Beside a
folder link alone, `line` and `pdf_page` are not read.

The server instructions (`INSTRUCTIONS`, one text for every connection: the
SDK sends them at `initialize`, with no request at hand) say Gamma's tools
search and read pages, notes, highlights, PDF text and the text of files,
that `list_pages` names a work folder's files by path and that a file hit
is followed with `read_page(page_id, from_line=N)` or `read_page(path=…)`;
they carry the chat's citation instruction (`ai_tools.citation_prompt`,
[pdf_citations.md](pdf_citations.md)), the PDF's and the line's, in its
absolute form and ask for absolute Markdown links, never relative ones;
they say what feedback is, that
a connection allowed to answer it has the two answering tools, and that a
feedback comment is the user's request while the passages it quotes stay
document data.
Codex's terminal UI hyperlinks only absolute http(s) destinations (OSC 8,
Codex CLI 0.150 and later; other terminals show the raw URL), and its IDE
extension and desktop app open them in the browser. Such a link in a
signed-in browser lands in the workspace and opens the page; with `pdf_page`
and `quote` it highlights the passage. The desktop app's sidecar picks a
free port per launch (`desktop/lib/sidecar.js`), so links minted against it
outlive neither a restart nor the OAuth token bound to that address; a stable
server address is what makes them durable.

### Export a page

`export_page` (`gamma/mcp_export.py`) gives an assistant the files the Export
dialog makes, without a download link. It renders through the same functions
as the HTTP export routes (`page_markdown`, `annotated_pdf`, `page_notes_pdf`
in `routers/export.py`), with the dialog's two switches, `highlights` and
`notes`, both on by default, and its "Include pages inside", `sub_pages`,
off by default (a page with none inside exports as without it):

- `markdown`: the readable Markdown export as the result's text. The file is
  not bundled: image and file links become absolute URLs on this server with
  `?ws=`, which open in a signed-in browser. Handwriting links name an SVG that
  only the zip export writes. With `sub_pages`, each page's Markdown in
  turn, parents first, between `---` lines.
- `pdf`: the page's PDF with the highlights as annotations and the notes
  painted beside them, the same file `/api/pages/{id}/export-pdf` returns.
  Both switches off gives the stored PDF. A page without a PDF is refused.
  With `sub_pages`, a zip of the PDFs of the page and the pages inside it,
  nested as the pages are (`page_export_file`, the annotated-PDF export's
  builder; one PDF among them is that PDF), whose `uri` is
  `/api/pages/{id}/export-pdf?sub_pages=1`.
- `notes_pdf`: the notes typeset as their own PDF, for any page; with
  `sub_pages`, one PDF of every page, each on a fresh sheet.

A secrets file's text is kept from the model (`feedback.anchors.text_kept`),
so its ranges' quotes are too: `markdown` leaves out the highlights of such a
page and the result's text says so; `notes_pdf` leaves out every highlight
when the export holds one.

A PDF (`application/pdf`) or a zip (`application/zip`) is returned as an
embedded resource (base64 blob) after a text line naming the file. Its `uri` is the HTTP export that gives the
same file to a signed-in browser. Claude Code writes the blob to a file and
hands the model the path (checked in Claude Code 2.1.283, which does this for
blobs up to 100 MB). A client that cannot handle embedded resources gets only
the text line. Files over `EXPORT_MAX_BYTES` (50 MB) are refused with a
pointer to the Export dialog, rather than inflated into one JSON response.
Nothing is stored.

### Send a page to an assistant

Paste a Gamma page, block, folder, or share URL with your question. `read_gamma_link`
validates the server and workspace, resolves the reference locally, and reads the
page through the same dispatcher as the other read tools. A block link also reads
that block; `pdf_page` starts the PDF excerpt at that physical page. A URL's
optional `quote` is returned as selected context, not treated as an instruction.
The response includes stable IDs and a canonical URL for subsequent reads.

Copy the page URL directly from the browser's address bar, or use an existing
block or share link. The assistant keeps that reference as context until another
is supplied; it does not track the user's active tab or PDF scroll position.

A folder link (the library's `?ws=…&folder=<id>` address while a folder is
browsed) and a folder-share link (a `?share=` token naming a folder,
[api.md](api.md) "Shares") resolve to the folder — by its id, the reference
carrying the folder's path as its `title` and a `?ws=…&folder=<id>` URL —
and answer with `list_pages(folder=<id>)` in the workspace scope, so the
call its result names for the next batch lists the same folder: the pages
the library or the share view lists, with the page URL template for citing
them. A folder link without `ws=` reads in the connected workspace holding
the folder; a folder that is gone fails as unavailable. Beside a page
(`&page=`, `&block=`) the link's `folder=` is only where the page was
opened, and the page is read. A folder share's `&folder=` is a folder
browsed in the share view, read when it is the shared folder or below it
and refused otherwise. With `&page=` beside the
token it reads that page instead, refused unless the page is filed in the
folder or below it (a page inside another page by where its top page is,
as the share view reaches it). A page share's link reads its page, or
with `&page=` a page inside it (the address the share view writes for a
sub-page), by the same scope rule; a page outside it, an ancestor
included, is refused as naming different pages.

Links never grant extra MCP access. Share tokens resolve only inside the
workspaces the connection already reads, including restricted shares whose
workspace the user can already read. A token names its workspace (`<workspace id>.<secret>`,
[api.md](api.md) "Auth model"), so one whose prefix is another workspace is
refused before any lookup, and a link minted before schema version 32 (a bare
secret) resolves nothing. Revoked, unknown, mismatched, or cross-workspace references fail
without disclosing the target. URLs are never fetched; foreign origins are
rejected. Localhost, 127.0.0.1 and ::1 are equivalent only at the same scheme and
port. A new server address requires the corresponding connection and link.

The other tools remain available for follow-up reading and for requests naming
a page, folder or topic without a URL.

### Answering feedback

The comment threads on a work folder's files are the user's feedback
([feedback.md](feedback.md)): `list_feedback` and `read_feedback` read them
on any connection, as the chat does. Answering them is the `feedback` token
scope (`integrations.SCOPES`): everything `read` may, plus a reply in and the
resolve of such a thread, and setting a Send request's state over the HTTP
API. A manual token gets the scope in the Tokens pane (**Read and answer
feedback**); a browser sign-in gets it from the consent page's checkbox
("Browser sign-in protocol"). A `write` token answers through the comment
API as before and is read-only on MCP, as it always was. A setup command's
sign-in gets it from the same checkbox.

- **Two tools of the adapter's own**, `reply_feedback` and `resolve_feedback`
  (`gamma/feedback/agent.py`: their schemas, descriptions and executors;
  `mcp_server.ANSWERING_TOOLS`). They are no entries of the chat registry,
  so the chat never answers threads and the MCP scope keeps `can_write:
  False`. `reply_feedback(thread_id, body, id?)` writes one reply (`id`, the
  client's own, makes a retry write once); `resolve_feedback(thread_id)`
  resolves, and never reopens (reopening sends a thread back to the agent and
  is the person's). Their annotations are `readOnlyHint: False`
  (`idempotentHint` true for the resolve), as the note tools' are.
- **Listed per token, checked per call.** `list_tools` appends them only for
  a connection whose scope is `feedback` (read from the request the SDK
  hands the handler). The SDK keeps one tool cache for every connection, so
  a read connection may reach `call_tool` with an answering tool's name, with
  or without the SDK's input validation: the dispatch checks the scope before
  it reads any argument ("This connection may not answer feedback: reconnect
  allowing it, or use a feedback token."), before the `READ_TOOLS` allowlist.
- **What the executor checks**, in order: this server takes writes
  (`hosted.read_only`) and the workspace is not being deleted
  (`workspaces.refuse_writes`; the hosted gate covers `/api/*` only, never
  `/mcp`); the account may comment in the workspace; the id is a thread's
  root that is not hidden (`comments.thread_of`); the thread's page is a live
  file of a work folder (a library page's thread is not feedback). Then it
  writes through `comments.create` / `comments.resolve` as the comment API
  does, author the connection's account, and `comments.written` tells the
  page's room, the Inbox and the feedback files. The answer names the file
  and the place (`anchors.place_words`; a range of a text file by the lines
  it holds now, `anchors.following`), links the thread (`gamma_link(thread_id=)`, the address the
  Inbox opens) and carries `{thread_id, comment_id?, page_id, url}` as
  `structuredContent`, with `workspace_id` on a connection of several
  workspaces.
- **The stamp.** A comment written through a token stores the token's
  display name and id (`comments.agent`, `agent_id`; `resolved_agent` on a
  resolve), so the thread shows "Claude Code" with the person's account, and
  the token edits only what it wrote ([comments.md](comments.md) "Who may
  comment"). A token's comment makes no one follow the page
  (`ops.UNFOLLOWED`'s rule: it is nobody's keystrokes), and the Inbox tells
  the person of it as of anyone else's write: the thread's authors, the
  page's followers and the people it names hear of an agent's reply, the
  thread's author of its resolve, each item naming the agent.
- **Which workspace.** The thread id finds its workspace
  (`mcp_links.holders(threads=)`, "Several workspaces"); one held by two
  connected workspaces is refused naming both until `workspace` chooses.

The plugin skill's "Feedback on a work folder" section (`plugins/gamma/skills/gamma/SKILL.md`)
tells the agent the loop: find the items (the repository's
`.gamma/feedback/open.md`, or `list_feedback`), read each with its source
line, change the sources and rebuild, reply with what changed and resolve
what is done, act on the owner's comments and ask about anyone else's, and
without the answering tools say so in the terminal.

### Editing notes

A connection may also change the notes of some of the workspaces it reads:
each is View or Edit, as a share's people are given (the same `Segmented`,
eye and pen). The token's `edits` names them (`integrations.edit_grant`,
schema step 56): `''` none, `'*'` every workspace it reads, now and later,
or a JSON list of ids, each one it reads. Edit is the account's own to give:
a workspace the account only views is refused (403) and its row says *view
only*, and every request rechecks the role, so an account made a viewer
since edits there no more (`resolve_connection`'s `editable`). A deleted
workspace leaves the list (`forget_workspace`). The grant is MCP's alone:
the HTTP API still answers such a token's writes 403, as any read token's.

- **The tools.** Where it may edit, the connection gets four of the chat's
  note tools (`mcp_server.EDIT_TOOLS`): `edit_block`, `create_block`,
  `move_block` and `clip_region` (a picture of a PDF region or of
  handwriting, for a note to embed). Never `delete_block`, and nothing of
  pages, folders, labels or settings. They run through
  `ai_tools.run_agent_tool` with `can_write` on, as the chat's agent's do:
  written as the account with client `ai`, planned and checked as in the
  chat (the edit's three-way merge with what the user typed meanwhile, the
  picture checks, the write lock), refused in a workspace being deleted.
  `list_tools` adds them only for a connection that edits somewhere, with
  `readOnlyHint: False` (`destructiveHint` true for `edit_block`, whose
  replace overwrites a note's text); `call_tool` refuses them to one that
  edits nothing before reading any argument, as the answering tools are
  refused, and `_call` refuses one aimed at a workspace it only reads,
  naming those it edits. `parent_id` and `after_id` find the call's
  workspace, as the reading tools' ids do ("Several workspaces");
  `list_workspaces` says where it edits (`editable` in its structured
  content).
- **What it read.** An `edit_block` replace must start from a full read of
  the note (`ai_tools.notes_seen`, the merge base), which the chat keeps for
  one turn. MCP has no turn, every call its own request, so a connection's
  reads in a workspace it edits are kept in memory while the server runs
  (`mcp_server._seen`: the latest 2,000 notes per connection and workspace,
  for 64 connections): `read_block` then `edit_block` works across calls,
  and a replace of a note it never read is refused as in the chat. Two
  sessions of one client share the token, and so share what it read.
- **Where it is set.** The consent page (an MCP client's sign-in and a setup
  command's) shows the eye and pen on each ticked workspace and on "All
  workspaces"; its summary becomes **Read and edit notes** and its button
  **Allow reading and editing**. Afterwards, a connection's **Choose
  workspaces** under Connected assistants changes both, and its row says
  where it edits ("edits notes in Lab", "edits notes wherever you edit").
  Not a scope: no client asks for it, the person gives it.

#### Asking first

A connection that edits asks the person before each change, by default
(`integration_tokens.asks`, schema step 57), as the chat's Ask permission
does: through the client, with MCP elicitation, in Gamma's words. Why this
way and not the client's own prompt or a step-up sign-in:
[research/mcp-elicitation.md](../research/mcp-elicitation.md).

- **The question** (`GammaMCP._ask`). The call is planned first with the
  tool's approval preview (`ai_tools.approval_preview`, the chat card's), in
  a worker thread; a call that could change nothing gets its answer and asks
  nobody. Then `elicit_form` sends the client the question (`_question`),
  plain text, since that is what a client shows: who asks to do what where
  and how much ("Codex wants to remove text from a note in “test”. It
  removes 2 lines."), the change line by line as a terminal shows one
  (`_lines`, from the preview's word diff: a removed line after `− `, an
  added one after `+ `, one kept line beside each run of them with its
  links as their text, "…" for the rest, at most 24 lines of 240
  characters), and "Open in Gamma:" with the page's URL. Its one field,
  `remember`, is "Don't ask again for this connection" ("You can turn
  asking back on in Gamma: Settings → AI → Integrations"). Accept runs the call with the preview's digest
  (`run_agent_tool(approved=)`), as the chat's gate does; accept with
  `remember` also turns asking off for the connection (`set_asks`). Decline
  or cancel, or no answer within the chat card's ten minutes
  (`ai_permissions.APPROVAL_TIMEOUT`), changes nothing, and the model hears
  `DECLINED` or `UNANSWERED`, worded as the chat's.
- **Two transports** (`GammaMCP.__call__`). The question needs a stateful
  session answered over SSE: the SDK's JSON mode drops a server request made
  during a call, and a stateless request has no session for the answer to
  come back to. So every client stays on the stateless JSON manager, and a
  second, stateful manager (SSE, 30 minutes idle, at most 1,000 sessions)
  takes the `initialize` of a connection that edits somewhere whose client
  can show a form (`capabilities.elicitation` empty or with `form`:
  `_opens_session`, reading the body once and handing it on). The session's
  id is noted as that token's (`owners`, from the response's
  `Mcp-Session-Id`); a request naming a session answers only the token that
  opened it, else 404, which tells a client to open another.
- **When it cannot ask.** A client on the stateless transport (it cannot
  show a question, or it connected before the connection could edit) gets
  `CANNOT_ASK`: nothing changed, and the steps to restart it or to turn
  asking off. So does one whose `elicit_form` fails.
- **Where it is set.** On the consent page, the switch **Ask me before each
  change it makes** under Permissions once a workspace is Edit (on; its
  summary says which way); in a connection's **Choose workspaces**, the same
  `Toggle`; the connection's row adds "changes without asking" once it is
  off.

**The consent page** (`auth/McpConsent.jsx`, an MCP client's sign-in and a
setup command's) is `AuthShell`'s card, wider (`.loginCard.mcpConsent`),
built from the settings' own widgets: the headline "Connect Codex" over who
is signed in, with **Use another account** beside it; a setup command's code
in a box of its own; `Section`s for the workspaces (the `WorkspacePicker`
rows with View / Edit), the Permissions (`Toggle` rows: asking first, once a
workspace is Edit; answering feedback) and what it may do (one icon line
each, the section named Read-only access, Read and edit notes or Read and
answer feedback); the footnotes; then the primary Allow and Cancel.

## Self-hosted and remote connections

Open Gamma at its public HTTPS address, then sign in as an administrator and
open **Settings → Server → Public server URL**. The field
suggests the browser's origin. Check it and click **Confirm** once.
Gamma stores the address in `users.db` and immediately uses it for OAuth,
MCP links, and the MCP hostname allowlist. No environment variables or restart
are needed. Merely opening the settings page does not trust an address.

Serve Gamma at an origin root, such as `https://gamma.example.com`, and preserve
the external Host header at the reverse proxy. The saved HTTPS address works
even when the proxy connects to Gamma over HTTP. Browser consent checks the
Origin against this validated public address, including its scheme and port,
rather than the proxy's backend connection URL. Changing the address requires
assistants to reconnect; the previous hostname is removed from the allowlist
unless separately allowed. Clearing the field restores request-based discovery.

`GAMMA_PUBLIC_URL` overrides the saved address and makes the field read-only. Its hostname is
automatically allowed too. `GAMMA_MCP_ALLOWED_HOSTS` adds other allowed host
authorities, for example `nas.local:8000` for manual tokens. Localhost and
loopback remain allowed by default. Browser sign-in over HTTP is restricted to
localhost/loopback; HTTP LAN and path-prefixed deployments can use manual tokens.

OAuth handles sign-in, not network reachability. A remote assistant still needs
to reach Gamma's server. A public listing cannot access a user's localhost by itself.

Browser-origin requests to `/mcp` are rejected. This endpoint targets native
MCP clients; it does not enable cross-origin browser access. The official Python
MCP SDK handles protocol negotiation, request validation, and the stateless JSON
transport. Request bodies are limited to 64 KiB. The backend lifespan owns the
SDK manager; it must run when embedding Gamma's ASGI app.

## Browser sign-in protocol

Gamma provides OAuth authorization-server discovery, protected-resource discovery,
dynamic public-client registration, and authorization-code exchange with S256
PKCE. The SDK validates client identity, redirect matching, and PKCE. Gamma adds:

- resource binding and an explicit approval screen, where the user ticks the
  workspaces the assistant may read (or all of them, those added later too)
  and marks those whose notes it may also edit ("Editing notes");
  the code carries the grant, rechecked against the account at the exchange;
- session-bound consent, rate and body limits;
- persistent expiring records and atomic single-use codes.

Two scopes exist, `gamma:read` and `gamma:feedback` (`mcp_oauth.SCOPES`): a
client may register with and ask for either or both, every client is stored
with both, and the 401 challenge still names `gamma:read` alone, so a client
that reads it asks to read. What the connection may do is decided on the
consent page, not by the request: its switch **Answer my comments on
work-folder files** is on when the client asked for `gamma:feedback`, and
the person may turn it on or off either way. Ticked,
the code carries both scopes, the token is made with the `feedback` scope
and the token response says `gamma:read gamma:feedback`; unticked, a read
token as before. `gamma:write` is refused at registration
(`invalid_client_metadata`) and at authorize (`invalid_scope`). Callback URLs
must be HTTPS or HTTP loopback.

```text
GET  /.well-known/oauth-protected-resource/mcp
GET  /.well-known/oauth-authorization-server
POST /oauth/register
GET  /oauth/authorize
POST /oauth/token
```

Both authorization and token requests must include `resource=<exact MCP URL>`.
Clients use `token_endpoint_auth_method=none`. Dynamic client registrations expire
after 90 days; sign-in requests expire after 10 minutes and authorization codes
after two minutes. Access tokens last 90 days. Refresh tokens are not issued;
sign in again after expiration or revocation. OAuth connections appear alongside
manual tokens in **Integrations**, where users can revoke them.

## Permissions and credentials

- An integration token belongs to one workspace, its `workspace_id`: `read` (the default; the MCP endpoint and the HTTP API's reads), `feedback` (everything `read` may, plus replying to and resolving the comment threads on a work folder's files and setting a Send request's state, [feedback.md](feedback.md); a viewer may make one, since viewers comment) or `write` (a mirror's push credential, [mirror.md](mirror.md); refused to viewers); a fourth, `computer`, is a computer's channel to the server ([computers.md](computers.md)), which opens nothing else and no MCP connection. A read or feedback token's MCP connection may read more, its `workspaces` (`integrations.grant`): `''` for its own workspace alone (every token from before step 51), `'*'` for every workspace the account can open, now and later, or a JSON list of ids, its own first; and of those, the ones whose notes it may also edit, its `edits` (`integrations.edit_grant`, "Editing notes": `''`, `'*'` or a list), only where the account edits. A write token is always its workspace alone. On the HTTP API a manual token is a bearer credential — the account behind it, confined to its own workspace whatever it grants MCP, never an admin, and refused (403) by every account-level endpoint — AI provider entries, preferences, AI usage, the workspace list, backups, tokens — whatever its scope ([api.md](api.md) "Integrations"). A `read` token writes nothing there: every write endpoint answers 403 "this token is read-only" (chats and cover snapshots included); a `feedback` token the same, but for its answers (`auth.require_commenter`'s `answer`), which it gives in its own workspace alone, whatever else its MCP connection reads. `POST /api/ai/chat` through either arms the reading tools only (`auth.can_write`), so its agent cannot rename, move or edit either. On MCP, a tool's `workspace` argument reaches only the connection's workspaces; on the HTTP API,
  `?ws=` and `X-Gamma-Workspace` cannot select another workspace.
- Only token SHA-256 hashes are stored in `users.db`. Tokens contain 256 random bits.
- HTTP share URLs also stay within the integration token's workspace and
  write scope: neither `?share=` nor `/api/share/{token}` can borrow the
  account's access to another workspace, and a share cannot turn a read
  token into a writer. `auth.request_share_access` applies this boundary
  to the link resolver and the shared data endpoints.
- Tokens expire after 90 days by default (API range: 1–365 days); accounts may
  have at most 20 unexpired tokens. Revoke a connection in the same settings panel.
- Each request checks the account still exists and can access the workspace:
  an MCP connection reads only those of its workspaces the account can still
  open, and none left denies it. Removing access, expiration, or revocation
  denies subsequent requests. A request already running may finish. Public
  workspace access follows Gamma's existing rules.
- Account deletion removes the account's tokens. Deleting a workspace removes
  the tokens of it alone and takes it out of the others' lists
  (`integrations.forget_workspace`); a list left empty goes with its token.
  Account rename preserves them; password changes through the admin API or
  `manage.py set-password` revoke them along with sessions.
- MCP always disables the chat's external web tools and its writes, but
  for a feedback connection's two answering tools ("Answering feedback")
  and the four note tools in the workspaces a connection may edit
  ("Editing notes"). The allowlist is enforced on every dispatch, independent of
  which tools the client was offered. Deprecated chat aliases are not accepted
  by the MCP transport.
- Token management requires a normal Gamma session and never accepts an integration
  token. A browser session alone cannot authenticate to `/mcp`.

Management API (session authenticated; account-wide, the current workspace is
the default grant and a write token's workspace):

```text
GET    /api/integrations/tokens                 the account's connections, its workspaces
POST   /api/integrations/tokens       {"name":"Codex","expires_in_days":90,"scope":"read","workspaces":[…],"edit_workspaces":[…]}
                                      scope: read (the default), feedback, write or computer (the last two: the current workspace alone)
PATCH  /api/integrations/tokens/{id}  {"workspaces":[…]} or {"all_workspaces":true}   a read or feedback token,
                                      with "edit_workspaces":[…] (of those) or "edit_all":true: the ones it may edit,
                                      and "asks": whether each change asks first (left out: as it was)
DELETE /api/integrations/tokens/{id}
```

### A setup command's sign-in

A client without browser sign-in of its own (DeepSeek Harness) is signed in
by its setup command, the way a TV or a CLI signs in: OAuth's device flow
(RFC 8628) in Gamma's own shape, on its own endpoints, not advertised in the
OAuth metadata. The command holds nothing secret, so the tab shows it at once.

```text
POST /api/integrations/device          {"name":"DeepSeek Harness"}   no session; rate-limited per address
     → {device_code, user_code: "BCDF-GHJK", expires_in: 600, interval: 2}
GET  /api/integrations/device/request?user_code=…   session: who asks, the code, the workspaces, a csrf
POST /api/integrations/device/consent  {user_code, csrf, workspaces | all_workspaces, feedback, approve}
POST /api/integrations/device/token    {device_code}   no session: 202 {"status":"pending"} while it waits,
     then {token, name, expires_at} once; 403 cancelled; 400 expired, used or signed out
```

The command opens `/?gamma_device=<user_code>`, which App routes to the
consent page (`McpConsent.jsx`) as it does `?gamma_oauth=`: sign in if need
be, the same workspace checklist and feedback checkbox, and the code to check
against the terminal's, since a sign-in someone else started would show
another. Allowed, the page says to go back to the terminal; the token is
minted only when the command collects it, so the connection appears only
once something has it, and only while the session that allowed it is still
signed in. The user code is eight letters without vowels, read in any case
and with or without its dash; the device code, the command's credential,
never leaves the terminal. Each step is a record of `mcp_oauth`'s store
(ten minutes) and is spent once.

## Codex plugin and distribution

### Install from a Gamma release

Open **Settings → AI → Integrations → Codex** in browser or self-hosted
Gamma (the same walkthrough ships in `plugins/gamma/README.md`). Select **Windows PowerShell** or **macOS / Linux**, copy the setup command,
and run it on the computer where you use Codex. The Codex CLI must already be
installed. Setup installs **Gamma PDF**, adds this server's MCP URL, and opens
browser sign-in. Tick the workspaces and start a new chat. No Gamma desktop app
is needed.

The command downloads a script from Gamma's latest GitHub release. Each script
pins a versioned ZIP URL and its SHA-256 digest, preventing mixed-version downloads.
Sources stay under `%LOCALAPPDATA%/Gamma/codex-plugin` on Windows or
`${XDG_DATA_HOME:-~/.local/share}/gamma/codex-plugin` on macOS/Linux. Repeating
setup updates the same local marketplace; keep that source directory.
Download or plugin-install failures stop before changing the MCP connection.
`codex mcp add` starts OAuth itself, so setup does not start a second login.
If sign-in is interrupted, resume with `codex mcp login gamma`.

If `gamma-local` is already registered from another directory, keep using it or
remove that marketplace registration in Codex before switching to release setup.
Setup does not remove existing marketplaces or plugins.

### Build and publish

`plugins/gamma` is one skills-based plugin for Codex and Claude Code. Its workflow
uses the separately configured Gamma MCP server; this keeps per-installation addresses and credentials
out of a distributable package. A direct MCP connection also works without the
plugin, including in the Codex IDE extension.

Both native manifests point to the same `skills/gamma/SKILL.md`. Keep client UI
metadata in its own manifest or `agents/openai.yaml`; keep library behavior in
the shared skill and server. The builder rejects mismatched shared manifest
metadata, and releases stamp both versions together. It copies only allowlisted
files and writes each client's catalog around the same plugin directory, without
symlinks or references outside the installed plugin. No second MCP backend is needed.

From a Gamma checkout, build into a directory you will keep. Codex registers
the source path and continues reading its catalog after installation; deleting
it breaks marketplace discovery even when the plugin remains cached. For
example, in Windows PowerShell:

```powershell
python tools/package_plugins.py --output "$env:LOCALAPPDATA/Gamma/codex-plugin/gamma-marketplace" --archive
codex plugin marketplace add "$env:LOCALAPPDATA/Gamma/codex-plugin/gamma-marketplace"
```

On macOS/Linux, use a persistent directory such as
`~/.local/share/gamma/codex-plugin/gamma-marketplace`. Reserve `tmp/` output for
package previews, not a registered marketplace source.

Install Gamma PDF through the desktop plugin browser or CLI `/plugins`, then
start a new conversation. To distribute it, publish the generated directory as
a Git repository or release archive. Users add that directory or Git marketplace
source, install the plugin, and configure their own Gamma connection. Package
updates into a new directory; the builder never overwrites an existing one.

The builder also writes a root README with installation and publication steps,
copies the privacy policy, and optionally creates a ZIP containing the hidden
catalog and plugin manifest. To put your destination marketplace repository in
the generated instructions, pass `--github-repo OWNER/REPO`. This only formats
the instructions; it does not create a repository or push files.

For GitHub distribution, commit the **contents of the generated directory** at
the root of a separate marketplace repository. Include `.agents/`,
`.claude-plugin/`, `plugins/gamma/.codex-plugin/`, and
`plugins/gamma/.claude-plugin/`. After publication, users run the command for their client:

```text
codex plugin marketplace add OWNER/REPO
claude plugin marketplace add OWNER/REPO
```

They then install Gamma PDF from that marketplace in the plugin browser and
connect their own library. The main Gamma repository itself is not a marketplace
root. The `Assistant plugin package` workflow checks both catalogs, shared files,
release versions, checksums, and the Codex installers on
Windows, macOS, and Linux and uploads preview assets.

The existing `desktop.yml` Gamma release workflow also builds the plugin with
the computed release version and publishes these assets on the same `vX.Y.Z` release:

- `gamma-codex-plugin-X.Y.Z.zip`
- `install-gamma-codex.ps1` and `install-gamma-codex.sh`
- `gamma-codex-SHA256SUMS.txt`
- `gamma-claude-code-plugin-X.Y.Z.zip`
- `gamma-claude-code-SHA256SUMS.txt`
- `dsh-gamma-X.Y.Z.tgz`, `dsh-gamma.tgz`, `install-gamma-dsh.ps1` and
  `install-gamma-dsh.sh`, and `gamma-dsh-SHA256SUMS.txt`

Both ZIPs contain identical bytes; separate names make the client downloads easy
to find while keeping one build. The Codex installers retain their published names
and behavior. The DeepSeek Harness tab's command fetches its setup script from
the latest release, so it works once a release carries the scripts.

Build-only runs keep them as CI artifacts. Keeping them on the existing
release preserves the desktop updater's latest-release convention. Preview the
assets locally with:

```text
python tools/release_plugins.py --output tmp/gamma-plugin-release --version 1.2.3
```

The packager sets the version only in the exported manifests; it does not tag
or publish. Nothing here submits a public directory listing, which needs a
stable public HTTPS endpoint and a review.

Official references: [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli),
[plugin packaging](https://developers.openai.com/plugins/build/plugins), and
[public submission](https://developers.openai.com/plugins/deploy/submission).

## Troubleshooting

- **DeepSeek Harness has the gamma skill but no `mcp__gamma__*` tools:** it was
  open when the setup ran, so it still holds the plugin and the connection it
  read when it started. Quit it completely, also from its tray icon, start it
  again and open a new conversation. The setup script now stops while the app
  runs, so this comes only from an older script or a connection replaced by
  hand.
- **Marketplace root does not contain a supported manifest:** run
  `codex plugin marketplace list` and check whether the registered source still
  contains `.agents/plugins/marketplace.json`. If a temporary source was deleted,
  rebuild into a persistent directory, run `codex plugin marketplace remove gamma-local`,
  then `codex plugin marketplace add <persistent-directory>` and
  `codex plugin add gamma@gamma-local`. Restart Codex and use a new chat.
- **MCP disabled by requirements:** `codex mcp list` reports the applicable
  managed policy. Ask the workspace administrator to permit the Gamma MCP
  connection; reinstalling the plugin or signing in again cannot override policy.
- **401:** sign in again if OAuth access expired or was revoked. For a manual token,
  confirm the Codex process inherited the variable. Access to at least one of the
  connection's workspaces must still exist. OAuth tokens are bound to the exact
  server URL used when signing in.
- **DeepSeek Harness has no Gamma tools:** the plugin is in the profile the app
  starts (the desktop app's is `desktop`; `dsh web` is `web`) and
  `$DSH_HOME/gamma.json` exists; a profile added to later needs the setup
  command again. dsh logs `gamma: not connected` when neither the file nor
  `GAMMA_URL` is there. Restart the app after connecting.
- **421:** the request's host is neither loopback nor the confirmed public
  server URL (Settings → Server); confirm the address first.
- **503:** the ASGI lifespan is not running.
- **Connection refused:** Gamma is stopped, the desktop port changed, or the MCP
  client is running on a different machine where localhost means that machine.
- **No tool in the picker:** verify the direct MCP connection first. Plugin/skill
  picker behavior varies by Codex surface; installing the workflow alone does not
  connect a server. Use `/mcp` in the CLI to inspect configured servers.

## Validation

`backend/tests/test_mcp.py` exercises the SDK endpoint, the registry-derived
tool list, real tool reads (the folder tree, a page's chat, a PDF page and
handwriting as images, a citation record), the links and structured content
results carry, the three export formats and their refusals, input validation,
workspace isolation, permissions, expiration, and revocation; one connection
reading several workspaces (ids finding theirs, a search across them, a named
one, a link's own, the ones it may not read refused, the HTTP API kept to the
first), changing a connection's workspaces, all of them following a new one, a
deleted one leaving the grant, and a setup command's sign-in (pending,
allowed, cancelled, spent once, ended by signing out); editing notes (the
grant only of workspaces it reads and the account edits, every one of them,
the four tools listed only where it edits and none of them a deletion, a
note added and appended to as client `ai`, a rewrite refused until a read
in an earlier call, a workspace it only reads refused, a deleted one
leaving the grant; `test_mcp_oauth.py` the consent's grant); asking first
(a stateless client refused until asking is off; a stateful session only for
a client that edits and can be asked, answering over SSE, refused to another
token and gone once deleted); a feedback
connection's answering tools (listed for it alone, refused to a read
connection even right after a feedback one listed them, a reply with its
stamp, the room and the Inbox told, a retried id written once, a resolve
repeated idempotently and never a reopen, a library page's thread and a
read-only server refused), and a thread id finding its workspace on a
connection of two, or refused when both hold it. `test_feedback_tools.py`
covers `list_feedback` and `read_feedback` in the chat's scopes (a section per
work folder, a directory, a file, a name, a Send request, batches, a page
chat, a library page, the item's JSON and pictures); `test_mcp_links.py`
`holders` by thread and request. `test_token_api.py` covers the Tokens
pane's `feedback` scope (a viewer's, several workspaces, all of them, its
PATCH) and the token's HTTP reads kept to its first workspace.
`test_mcp_oauth.py` covers discovery, approval (several workspaces, or all;
the consent's "answer feedback" ticked or not, for a client that asked for
`gamma:feedback` and for one that did not; `gamma:write` refused),
PKCE, resource/client/redirect binding, expiration, replay prevention,
revocation, and streamed body limits.
It also covers HTTPS proxy consent with saved and environment-configured public
URLs, including approval, cancellation, and rejection of foreign origins.
`test_integration_lifecycle.py` covers manual and OAuth tokens across account
rename, password changes through the admin API and CLI, workspace deletion,
and account deletion through the admin API and `manage.py delete-user`.
`test_migrations.py` covers
schema upgrades. Browser scenarios cover sign-in, ticking a second workspace,
approval, cancellation, and MCP reads, each assistant's tab, a token's
workspaces and changing them, the account-wide list and revocation, and the
DeepSeek Harness setup command, shown at once, and its sign-in allowed in the
browser and collected once; a workspace marked Edit, and asking turned off,
in a connection's editor and on the consent page; and Gamma's question about
a change streamed to a client over a real session and answered: allowed,
declined, allowed with "don't ask again", then not asked.
