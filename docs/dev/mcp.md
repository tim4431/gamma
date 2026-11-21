# Gamma MCP, Codex, Claude Code, and DeepSeek Harness

Gamma exposes a Streamable HTTP MCP endpoint at `/mcp`, on the same server as
the app. It reads; it writes what the user let the connection write: comments,
replies, resolves and highlights under its own name in the workspaces at the
Comment level ("The Comment level" below), and notes in those at Edit
("Editing notes"). Gamma's chat and MCP
adapter share `gamma/ai_tools/`: tool definitions, executors, page/folder
scope checks, and dispatch permissions. The chat calls these functions
directly. Gamma is not an MCP client for external servers, and connecting
Codex does not invoke Gamma's AI provider.

| File | Owns |
|---|---|
| `gamma/mcp_server.py`, `gamma/mcp_lazy.py` | the `/mcp` transport (official Python MCP SDK): the chat registry's read tools (`READ_TOOLS`), link reading, page export, the writing tools at the Comment level (`WRITING_TOOLS`), the note tools at Edit (`EDIT_TOOLS`); `mcp_lazy.py` authenticates the request and loads the SDK adapter on first use |
| `gamma/annotations/hand.py`, `gamma/annotations/places.py` | what the writing tools write as the connection's agent, and the places they find ([annotations.md](annotations.md) "The writing tools") |
| `gamma/mcp_oauth.py`, `gamma/mcp_oauth_provider.py` | discovery, dynamic registration, PKCE authorization and token exchange, the consent API (`/api/integrations/oauth/*`), `public_base`; the provider issues the 90-day token through `integrations.create_token` |
| `gamma/mcp_links.py` | local page/block/share link resolution within the connected workspaces; which of them holds an id (`holders`: a block, a comment thread) |
| `gamma/mcp_export.py` | `export_page`: a page as Markdown text or an embedded PDF, over `routers/export.py` |
| `gamma/integrations.py` | integration tokens (`integration_tokens` table, hashes only), the workspaces each may read, comment in and edit (`grant`, `comment_grant`, `edit_grant`, `resolve_connection`) and their resolution |
| `gamma/server_settings.py` | the admin-confirmed public URL and the MCP host allowlist |
| `gamma/routers/integrations.py` | the session-only connection management API, account-wide; a setup command's sign-in for a client without its own |
| `users.db` tables `integration_tokens`, `mcp_oauth` | part of the baseline schema (steps 0–19 are folded into it); `integration_tokens.workspaces` is step 51, `edits` 56, `asks` 57, `comments` 61 ([migrations.md](migrations.md) "Versions") |
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
to retry. Sign in with your Gamma account, tick the workspaces, mark any it
may comment in **Comment** ("The Comment level") and any whose notes it may
also edit **Edit** ("Editing notes"), and approve.
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
the same pane, name the token, pick its access (read, or read and write),
tick the workspaces it may read, each at View, Comment or Edit (a read and
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
| `backlinks` | The notes that link to a page or a note, newest first, each with its block id and page |
| `read_page` | The pages it is inside and the pages inside it, notes, highlights, open comment threads, properties, and the document's text in windows: a PDF's by page, a text file's numbered lines by `from_line` / `to_line`; a work folder's file named by its `path` too |
| `read_block` | One block/subtree or a page's nested note outline |
| `read_chats` | The AI chat kept with a page or folder: the current conversation as a transcript, earlier ones by `chat_id` |
| `list_annotations` | The highlights and comment threads of a page, a folder or the library (a page's or a folder's id, a folder's path, a work folder by name, `home`; the library without one), each with its file, place, source line, note and a thread's comments, as Markdown, in batches (`limit`, `after`) |
| `read_annotation` | One annotation, a thread in any state or a bare highlight (`id`), as Markdown and as JSON, with the pictures of its region now and as it was commented on |
| `view_pdf_page` | One PDF page as an image (a scan, a figure, a table's layout) |
| `view_ink` | The user's handwriting as an image: a group's strokes on their PDF page or sheet of paper, or a whole page with all its handwriting |
| `view_image` | The pictures a note block embeds (`![alt](/api/uploads/…)` in its markdown), all or one by index |
| `cite` | The citation records kept with pages: the paper metadata, its BibTeX, the slide citation |
| `list_workspaces` | The workspaces the connection reads, the default first: each one's id, name and the account's role |
| `read_gamma_link` | Resolve and read a page, block, folder, or share URL, including PDF page and line context (`line=N` reads the file from that line, `line=N-M` those lines); a folder or folder-share URL lists the folder's pages |
| `export_page` | One page as Markdown text, or as a PDF file: the annotated paper or the notes typeset; with `sub_pages`, the pages inside it too |
| `comment` | At the Comment level: a reply in a thread (`thread_id`), or a new thread on a place of a page (a PDF quote with its page, a text file's lines, a note, the page itself), signed with the connection's name |
| `resolve` | At the Comment level: resolve a thread once it is done (never a reopen) |
| `highlight` | At the Comment level: highlight a PDF quote or a text file's lines, with a note, a colour and a style, stamped as the connection's |
| `edit_block` | At the Edit level: replace, append to, prepend to or patch a note's text |
| `create_block` | At the Edit level: paste Markdown as new note blocks (a heading holding what follows it, list items nested), after a sibling, first or last |
| `move_block` | At the Edit level: move a note with its children under another parent, after a sibling, first or last |
| `clip_region` | At the Edit level: store a picture of a PDF region or of handwriting for a note to embed |
| `draw`, `add_text_box`, `place_picture`, `add_sheet` | At the Edit level: shapes, a text box, a picture or a page of paper on a page, one block a call |
| `upload_file` | At the Edit level: store a picture or file for a note, fetched from a public URL or sent from the assistant's computer with the one-time command it returns |
| `create_page` | At the Edit level: make a page to write into, in a folder, inside a page or at the top of the library |
| `list_changes` | At the Edit level: this connection's own changes, newest first, each with its change id, time, summary, page and whether it was undone |
| `undo_change` | At the Edit level: take back one of this connection's changes by its change id, once; `force` past a conflict |
| `delete_block` | At the Edit level, only where the person also allowed deleting notes: delete a note with its children |

The first thirteen are the Gamma chat's own tools (`gamma/ai_tools/`, described
in [ai_tools.md](ai_tools.md)), run through the same dispatcher with a
workspace-wide, non-writable scope. `READ_TOOLS` in `mcp_server.py` comes
from `ai_tools.mcp_tools`: every reading tool that stays inside the library.
A new reading tool is offered without touching the adapter; `test_mcp.py`
pins the names. The chat's other tools stay off: the web tools (`search_papers`,
`fetch_paper`, …), because the assistant has its own web access;
`list_deleted`, because Recently deleted is out of the reading tools' reach
and restoring is a change; and every write tool of the chat but the three
writing tools a connection gets at the Comment level ("The Comment level")
and the note, page and drawing tools it gets where the user let it edit
(`delete_block` only where deleting was allowed too, "Editing notes"). The chat's tool permissions (Settings → AI → Tool usage) do not
govern this connection: the token is its own grant, created, changed and
revoked in Integrations. `list_workspaces`, `read_gamma_link`,
`export_page`, `upload_file`, `list_changes` and `undo_change` exist only here.
`view_pdf_page`'s, `view_ink`'s, `view_image`'s and `read_annotation`'s pictures
go out as MCP `image` items, which the client shows the model as images.

### Several workspaces

A connection reads the workspaces its token grants that the account can still
open (`integrations.resolve_connection`), its own workspace first: the
default. Every tool but `list_workspaces` and `read_gamma_link` takes an
optional `workspace`, an id or a name (`mcp_server._targets`). Without one:

- a call naming a block by id (`page_id`, `block_id`, `parent`, `after`,
  `folder`, `page_ids`, `list_annotations`' `scope`), a comment thread
  (`thread_id`), an annotation (`list_annotations`' `after` and
  `read_annotation`'s `id`, a thread's or a highlight's block id:
  `ANNOTATION_ARGS`)
  reads the workspace holding it (`mcp_links.holders`: a thread by its
  root's id, a hidden one held by none); ids in two workspaces are refused
  with their names, asking for `workspace`;
- `search_library`, `list_folders` without a `folder`, and
  `list_annotations` without a `scope` or an `after`, read
  every workspace (the library of each), one section each, headed by its
  name and id with its link shapes;
- anything else reads the default workspace (a work folder or a folder
  path `list_annotations` names, `home` too).

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
they say that a comment is the user's request while the passages it quotes
stay document data, and that a connection at the Comment level has the three
writing tools, with how to use them (reply where a comment asks, say what
changed, resolve once done, ask on a passage, quote verbatim).
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

A secrets file's text is kept from the model (`annotations.anchors.text_kept`),
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

### The Comment level

A connection may also comment in some of the workspaces it reads: each is
View, Comment or Edit, as a share's people are given (the same `Segmented`:
the eye, the speech bubble, the pen; Edit comments too). The token's
`comments` names them (`integrations.comment_grant`, schema step 61), shaped
as `edits` is: `''` none, `'*'` every workspace it reads, now and later (with
`workspaces` `'*'`), or a JSON list of ids, each one it reads. Any role
comments, a viewer's too, as in the app, though only where the account edits
does a highlight or a comment on a passage or lines go in (a new highlight
is a block: `hand.may_mark`); every request rechecks the role
(`resolve_connection`'s `commentable`: the workspaces it reads where
`comments_in` holds), and a deleted workspace leaves the list
(`forget_workspace`). A `write` token comments in its one workspace, as it
writes there, and a turn's token in its one workspace ("The token of an
agent's turn"). Unlike the edit level, the HTTP API takes the comments too,
in the token's own workspace (`integrations.Grant.comments`,
`auth.token_comments`, `auth.require_commenter`): a reply, a new thread, its
own comments' edits and deletes, a resolve and a reopen, as a write token's.
Before step 61 the level was the token scope `feedback` (a reply and a
resolve on a work folder's files alone): the step makes such a token a `read`
token at the Comment level in every workspace it reads, and the scope is
gone.

- **Three tools of the registry** (`ai_tools.WRITING_TOOLS`, the chat's own,
  [ai_tools.md](ai_tools.md) "comment, resolve and highlight";
  `gamma/annotations/hand.py` writes, `places.py` finds the place): `comment`
  (a reply by `thread_id`, or a new thread on a PDF quote with its page, a
  text file's lines, a note or the page itself), `resolve` and `highlight`.
  `list_tools` adds them, from the registry, only for a connection that
  comments somewhere, with `readOnlyHint: False` (`idempotentHint` true for
  the resolve) and a `workspace` argument naming where it may write
  (`COMMENT_WORKSPACE_ARG`). The SDK keeps one tool cache for every
  connection, so a read connection may reach `call_tool` with a writing
  tool's name: the dispatch checks the level before it reads any argument
  (`NOT_COMMENTING`, the steps to give it), and `_call` refuses one aimed at a
  workspace it only reads, naming those it comments in (`_writes`, which the
  note tools share). They never ask first: a comment changes no note, is
  signed and can be deleted by the person.
- **What a call checks and writes.** The scope `_writes` gives the call is
  the workspace's whole library (`type: folder`), `can_comment` on,
  `can_write` off, and the connection's stamp (`agents.stamp`). `_writes`
  refuses every comment and note change while a hosted server's plan keeps
  it read-only (`hosted.read_only`; the hosted gate covers `/api/*` only,
  never `/mcp`), and the hand checks that again, then that the workspace is
  not being deleted and the account may comment there; a thread must be a live root
  (`comments.thread_of`) on a page of the library, of any kind: a library
  page's as a work folder's file's. It writes through `comments.create` /
  `comments.resolve` and, for a passage, `ops.apply_ops` (client `ai`), as
  the app does, the author the connection's account; `comments.written` and
  `ops.after_commit` tell the page's room, the Inbox and the annotation
  files. The answer names the file and the place (`anchors.place_words`),
  the ids it made and the link `list_annotations` gives the entry
  (`entries.link_of`: `?ws=&page=&block=&thread=`), and carries `{page_id,
  block_id, thread_id, comment_id, link}` as `structuredContent`, with
  `workspace_id` on a connection of several workspaces. A call made again
  moments later (`hand.REPEAT_S`) writes nothing twice and is refused with
  what the first wrote.
- **The stamp.** A comment written through a token stores its agent's
  name and id (`agents.of_connection`: the token's display name and id, a
  turn's token its computer agent's; `comments.agent`, `agent_id`;
  `resolved_agent` and `resolved_agent_id` on a resolve), so the thread shows "Claude
  Code" with the person's account ([comments.md](comments.md) "Who wrote a
  comment"), and
  the token edits only what it wrote ([comments.md](comments.md) "Who may
  comment"). A token's comment makes no one follow the page
  (`ops.UNFOLLOWED`'s rule: it is nobody's keystrokes), and the Inbox tells
  the person of it as of anyone else's write: the thread's authors, the
  page's followers and the people it names hear of an agent's reply, the
  thread's author of its resolve, each item naming the agent.
- **Which workspace.** The thread id finds its workspace
  (`mcp_links.holders(threads=)`, "Several workspaces"), a page or block id
  its own; one held by two connected workspaces is refused naming both until
  `workspace` chooses.
- **Where it is set.** The consent page shows the eye, the speech bubble and
  the pen on each ticked workspace and on "All workspaces"; a client that
  asked for `gamma:feedback` (the level's name, "Browser sign-in protocol")
  finds the open workspace at Comment to begin with, and the page's summary
  becomes **Read and comment**, its button **Allow reading and commenting**.
  The Tokens pane's checklist takes the same levels. Afterwards a
  connection's **Choose workspaces** changes them, and its row says where it
  comments ("comments in Lab", "comments everywhere").

The plugin skill's "Highlights and comments" section (`plugins/gamma/skills/gamma/SKILL.md`)
tells the agent the loop: find the items (`list_annotations`, or a
repository's `.gamma/annotations/open.md`), read each with its source line,
change what it asks for (the sources, then the project's build), reply with
what changed and resolve what is done, ask about a passage on the passage
with `comment`, highlight only on request, act on the owner's comments and
ask about anyone else's, and without the writing tools say so in the
terminal.

### Editing notes

A connection may also change the notes of some of the workspaces it reads:
each is View, Comment or Edit (`WorkspacePicker`'s `Segmented`: the eye,
the speech bubble and the pen), and Edit comments too. The token's `edits`
names the Edit ones (`integrations.edit_grant`, schema step 56): `''` none,
`'*'` every workspace it reads, now and later, or a JSON list of ids, each
one it reads. Edit is the account's own to give: a workspace the account
only views offers View and Comment alone, Edit there is refused (403), and
every request rechecks the role, so an account made a viewer since edits
there no more (`resolve_connection`'s `editable`) and its picker shows that
workspace at Comment. A deleted workspace leaves the list
(`forget_workspace`). The note edits are MCP's alone: the HTTP API answers
such a token's block writes 403, as any read token's, and takes its
comments in its own workspace, as at the Comment level ("The Comment
level").

- **The tools.** Where it may edit, the connection gets four of the chat's
  note tools (`mcp_server.EDIT_TOOLS`): `edit_block`, `create_block` (the
  paste, below), `move_block` and `clip_region` (a picture of a PDF region
  or of handwriting, for a note to embed), the chat's `create_page` (a new
  page in a folder, inside a page or at the top of the library), MCP's own
  `upload_file` (below), `list_changes` and `undo_change` ("Undoing a
  change"), and the chat's drawing tools
  (`DRAWING_TOOLS`: `draw`, `add_text_box`, `place_picture`, `add_sheet`,
  each one new block on a PDF page or a page of paper, [ai_tools.md](ai_tools.md)
  "Drawing on pages"). `delete_block` (`DELETE_TOOLS`) only where deleting
  was allowed too (below). Nothing of existing pages' titles or places,
  folders, labels or settings. A drawing's answer carries its
  picture (image content, as `view_ink`'s) and, since there is no reply to
  revert it from, says `undo_change` takes a misplaced one back
  (`ai_tools.drawing._placed_after`). They run through
  `ai_tools.registry.run_agent_tool` with `can_write` on, as the chat's agent's do:
  written as the account with client `ai`, a block `create_block` or a
  drawing tool makes stamped with the connection (`properties.agent`, `agents.stamp`:
  `{id, name, for}`, [block_centric.md](block_centric.md) "Made by an
  agent"), planned and checked as in the chat (the edit's three-way merge with what the user typed meanwhile, the
  picture checks, the write lock), refused in a workspace being deleted and,
  like the comments, on a hosted server its plan made read-only (`_writes`,
  before anyone is asked).
  `list_tools` adds them only for a connection that edits somewhere, with
  `readOnlyHint: False` (`destructiveHint` true for `edit_block`, whose
  replace overwrites a note's text, for `delete_block` and for
  `undo_change`; `openWorldHint` true for `upload_file`,
  which may fetch from the web); `call_tool` refuses them to one that
  edits nothing before reading any argument, as the writing tools are
  refused, and `_call` refuses one aimed at a workspace it only reads,
  naming those it edits. `parent_id` and `after_id` find the call's
  workspace, as the reading tools' ids do ("Several workspaces");
  `list_workspaces` says where it edits (`editable` in its structured
  content).
- **Deleting notes.** `delete_block` is its own permission: the token's
  `deletes` (`users.db` `integration_tokens.deletes`, schema step 70, 0 on
  every token made before it), which holds only where the connection edits
  (`Connection.deletes` is false on a connection that edits nothing).
  `list_tools` offers it only with `deletes` on, and `call_tool` refuses it
  without, before reading any argument (`NOT_DELETING`, the steps to allow
  it and that `undo_change` takes back its own changes meanwhile). No
  client asks for it and the consent page never offers it: the person turns
  it on afterwards, on the connection's row (below). A deletion is
  journaled like any other change, so `undo_change` brings it back.
- **The paste.** `create_block`'s `content` may be a whole Markdown
  passage, split as the editor's Paste as → Blocks splits it
  (`markdown_import.md_to_blocks`, [ai_tools.md](ai_tools.md) "edit_block /
  create_block / move_block"): a heading holds what follows it, list items
  nest, and a paragraph, table, fence or `$$` math stays one block;
  `split: false` keeps it in one block. The whole paste is one op batch
  under the write lock, so it is one question to the person ("Asking
  first"), at most `ops.MAX_OPS` (500) blocks and 100,000 characters a
  call. `after_id` names the sibling it lands after, or the parent itself
  to land first. Every block carries the connection's `properties.agent`,
  the answer lists the new blocks by id (up to 50 lines), and the
  structured content carries `block_id` (the first) and `block_ids` (all, in
  order). Links this server handed out (`gamma_link`'s
  `https://host/?ws=<id>&…`), here and in what `edit_block` writes, are
  stored host-free, `/?…`
  (`ai_tools.base.host_free`, through the scope's `link_base`), as the
  editor stores a pasted link of its own library; another workspace's
  links and web links stay as written. The request body takes up to 1 MiB
  (`mcp_server.MAX_BODY`), room for 100,000 characters in any JSON
  escaping.
- **Pictures and files.** A note shows a stored file by a line, `![](url)`
  for a picture or `[name](url)` for a file chip, as the editor writes a
  dropped one; `missing_uploads` refuses an `/api/uploads/` name the
  workspace does not hold, and over MCP its refusal names `upload_file`. So
  a file is stored first, and the line it answers is pasted: `clip_region`
  for a region of the library's own PDFs or handwriting, `upload_file`
  (`gamma/mcp_upload.py`, SDK-free like `mcp_export.py`) for anything else.
  `upload_file` is MCP's own, outside the registry (a ticket belongs to a
  connection, and the chat has the person's own drop), and takes one of two
  routes:
  - `url`: Gamma fetches a public file (`web_fetch.read_bounded`: the SSRF
    guard, every redirect checked again, 40 MB for a PDF and 5 MB for
    anything else, a longer body refused, never cut short) with no
    publisher sign-ins, and refuses a web page. The extension is
    `formats.upload_extension(name, sniffed picture type)`, `/upload-file`'s
    rule, the name `name` or the URL's own; a `.ink` is refused. It is
    stored with `storage.store_file_stream` (the quota, the content checks)
    and answers `{upload, name, size, markdown, already_existed}`.
  - `name` alone (a path or name on the assistant's computer): a one-time
    ticket, answered as the command that sends the file, `curl -sS
    --fail-with-body -T <path> -H 'X-Gamma-Upload: <secret>'
    <base>/api/agent-uploads` (`curl.exe` on Windows), and as structured
    content `{upload_url, header, ticket, expires_in, max_bytes}`. An
    executable's or a `.ink`'s name is refused before a ticket is made
    (`upload_parts.file_extension`). The PUT ([api.md](api.md) "PDFs &
    uploads") answers `{url, name, size, already_existed, markdown}`.

  `markdown` is the line to paste (`mcp_upload.markdown_line`: `![](url)`
  for a picture extension, else `note_markup.markdown_link(name, url)`).
  The tickets live in this process's memory, as the uploads in parts do, so
  a restart forgets them: only the secret's SHA-256 is kept, a ticket lives
  `mcp_upload.TICKET_TTL_S` (900 s), the first PUT the route takes spends it
  whatever its outcome, and a connection holds at most 8 (`MAX_TICKETS`; minting another
  drops its oldest). The ticket is the only authority the PUT carries; it
  goes in a header so no access log keeps it, and the connection's token
  still writes nothing on `/api`. A stored file shows in no note until a
  note write names it; one that is never pasted is an unreferenced upload,
  which the upload GC collects as any other.
- **What each client can do.**

  | Client | A Markdown paste | A library figure (`clip_region`) | A public file (`upload_file(url)`) | A file on the person's computer | Asking first |
  |---|---|---|---|---|---|
  | Claude Code | yes | yes | yes | the ticket's `curl` from its shell (outside the sandbox for a Gamma on localhost on Linux, where a sandboxed command's localhost is its own) | elicitation |
  | Codex CLI | yes | yes | yes | the ticket's `curl` with `network_access` on, else the person drops it in | elicitation (form) |
  | Cursor, VS Code | yes | yes | yes | the ticket's `curl` from the terminal | not checked |
  | claude.ai, Claude Desktop | yes, with asking off (no elicitation); Gamma must be reachable from the internet | yes | yes | the code sandbox's `curl` when its egress allows Gamma's host, else the person drops it in | none |
  | ChatGPT, DeepSeek Harness | not checked | | | | |

  Without a shell, the assistant asks the person to drop the file into the
  note in Gamma, then reads its line with `read_block`. Why tickets and a
  server fetch rather than base64 or a hand-off page:
  [research/mcp-file-input.md](../research/mcp-file-input.md).
- **What it read.** An `edit_block` replace must start from a full read of
  the note (`ai_tools.notes_seen`, the merge base), which the chat keeps for
  one turn. MCP has no turn, every call its own request, so a connection's
  reads in a workspace it edits are kept in memory while the server runs
  (`mcp_server._seen`: the latest 2,000 notes per connection and workspace,
  for 64 connections): `read_block` then `edit_block` works across calls,
  and a replace of a note it never read is refused as in the chat. Two
  sessions of one client share the token, and so share what it read.
- **Where it is set.** The consent page (an MCP client's sign-in and a setup
  command's) shows the levels on each ticked workspace and on "All
  workspaces"; at Edit its summary becomes **Read and edit notes** and its button
  **Allow reading and editing**, and its Edit line reads "Make pages, and
  create, edit and move notes with their pictures and files, in the
  workspaces marked Edit. Deletes nothing unless you allow it later in
  Settings, and changes no folders or settings." Afterwards, a connection's **Choose
  workspaces** under Connected assistants changes both, and its row says
  where it edits ("edits notes in Lab", "edits notes wherever you edit").
  There, once a workspace is Edit, the `Toggle` **May delete notes** sets
  `deletes` (`PATCH /api/integrations/tokens/{id}` with `deletes`, off by
  default). Not a scope: no client asks for either, the person gives them.

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
  characters), and "Open in Gamma:" with the page's URL. A paste of
  several blocks says how many and where ("Claude Code wants to add 7 notes
  to “Paper”, under “Methods”, after “We measured…”. It adds 23 lines, 1
  picture and 1 file.", or "at the top"), from the preview's `count`,
  `after` or `first`, `picture_count` and `file_count`, and its lines are
  the blocks' texts indented two spaces a level. In every line a stored
  upload is named, `[picture: caption]` or `[file: name]` (`_UPLOAD_LINK`,
  `_stored`), not shown as its URL; other links on an added or removed
  line stay as written. Its one field,
  `remember`, is "Don't ask again for this connection" ("You can turn
  asking back on in Gamma: Settings → AI → Integrations"). Accept runs the call with the preview's digest
  (`run_agent_tool(approved=)`), as the chat's gate does; accept with
  `remember` also turns asking off for the connection (`set_asks`). Decline
  or cancel, or no answer within the chat card's ten minutes
  (`ai_permissions.APPROVAL_TIMEOUT`), changes nothing, and the model hears
  `DECLINED` or `UNANSWERED`, worded as the chat's.
- **Storing asks nobody.** A tool that only stores a file for a note to
  show, `clip_region` (the registry's `"stores": True`, gathered in
  `ai_tools.registry.STORING_TOOLS`) and `upload_file`, runs without a
  question: it changes no note, and the note write that pastes its line
  asks with the picture or file named in it. A figure from a PDF is
  therefore one question, not two. The chat's `ApprovalGate.asks`
  (`ai_agent.py`) follows the same rule.
- **Undoing asks; listing does not.** `list_changes` changes nothing and
  runs without a question. `undo_change` asks as a change does, its
  question from the journal rather than a preview (`_undo_question`): "Codex
  wants to undo its change “Added 7 notes in “Paper””, made 2026-11-20 10:02 UTC, in
  “Paper”.", with "Someone changed it since: undoing it anyway loses that."
  when it is forced, and the page's link. A change undone already, or not
  this connection's, answers without asking. `create_page` asks "<who>
  wants to make the page “T” in <folder> / inside “P” / at the top of the
  library.", and `delete_block` "<who> wants to delete a note in “T” with
  the N notes under it. undo_change can bring it back."
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
rows with View / Comment / Edit), the Permissions (the `Toggle` of asking
first once a workspace is Edit, else a line saying what Comment and Edit
let it do) and what it may do (one icon line each, the section named
Read-only access, Read and comment or Read and edit notes); the footnotes;
then the primary Allow and Cancel.

#### Undoing a change

Gamma's chat keeps each change's revert record with its reply, where the
person takes it back. MCP has no reply, so the adapter journals the changes
instead (`gamma/mcp_changes.py`, SDK-free like `mcp_export.py`).

- **The journal.** After a change tool succeeds, `mcp_server._call` hands
  its action to `mcp_changes.record`, which keeps it when the action carries
  a revert record of a kind `ai_revert.revert_change` takes back
  (`mcp_changes.KINDS`): `edit_block`, `create_block` (a paste is one
  change), `move_block`, `delete_block`, the drawing tools, `add_sheet` and
  `create_page` (kind `made`). `clip_region` and `upload_file` store a file
  and make no change. The row goes in the workspace's `pages.db` table
  `agent_changes` (`db.PAGES_SCHEMA`, additive, so no migration step): id,
  `token_id` (the connection), `agent` (its stamp then), `at`, `tool`,
  `kind`, `block_id`, `page_id`, `summary`, `revert` (the tool's record, as
  JSON) and `undone_at` (`''` while it stands). The answer ends "Change
  <id>: undo_change(change_id="<id>") takes it back.", and its structured
  content carries `change_id`.
- **What is kept.** Each write prunes the table: the newest
  `KEEP_PER_CONNECTION` (500) per connection, none older than `KEEP_DAYS`
  (30). It is each account's own and of this server only: the table is in
  `ws_backup.PRIVATE_TABLES`, so a backup carries none and a restore keeps
  the live rows, and no mirror or Gamma export carries it. A revert record
  names blocks of this one database, so it means nothing elsewhere.
- **`list_changes(limit?)`.** This connection's own changes, newest first
  (20 by default, at most 100), across the workspaces it edits or the one
  `workspace` names: each line its change id, time (UTC), summary,
  "(undone)" once taken back, the workspace on a connection of several, and
  the page's link; the structured content lists `{change_id, at, summary,
  undone, page_id, workspace_id}`.
- **`undo_change(change_id, force?)`.** Takes one back with
  `ai_revert.revert_change`, written as the account (`mcp_changes.undo`,
  which marks it undone): the chat's revert, a merge into the notes as they
  are now. Only this connection's own (`_change_of` looks in the workspaces
  it still edits), and each once; a redo is the change made again. A page
  made goes to Recently deleted while it is empty (`ai_revert._plan_made`),
  so its notes are undone first; the latest change first when several touch
  one note. When someone's work since would be lost (409 `filled`,
  `changed` or `moved`) it refuses, saying what forcing would take out, and
  `force: true` takes it back anyway, which the tool's description says to
  send only once the user agrees. A hosted server its plan made read-only
  refuses it, as it does every change, and a workspace being deleted too.
- **The person's side.** Each editing connection's row in Settings → AI →
  Integrations has a **Changes** button (`SettingsIntegrations.jsx`
  `ConnectionChanges`): what it changed, the summary, time, workspace and
  page, each with **Undo**, or "Undone". A conflict is worded as the chat's
  revert words it (`chat/AgentChanges.jsx` `refusalCopy`), with the button
  that undoes anyway. The routes ([api.md](api.md) "Integrations and MCP"):
  `GET /api/integrations/tokens/{token_id}/changes` (the latest 100 across
  the workspaces it reads, newest first) and `POST
  /api/integrations/tokens/{token_id}/changes/{change_id}/undo` with
  `{workspace_id, force}`, as the person: 409 `{detail, conflict, preview}`
  as `/api/ai/revert` answers, 404 for another connection's change or one
  gone, 403 where the account no longer edits.

## Self-hosted and remote connections

Open Gamma at its public HTTPS address, or on this network at its plain
HTTP one, then sign in as an administrator and
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

Plain HTTP is taken on this computer and on this network, where a NAS has
no certificate: `http://192.168.1.10:8000`, `http://nas.local:8000`, a
Tailscale address. A host counts as this network's by its form alone,
once normalized (`host_scope.literal` "lan": a private address, a
single-label name, a `.local`, `.lan`, `.home.arpa` or `.internal` name; a
host written as one number, `3405803781`, is the address it stands for).
A name of that form is looked up when it is confirmed in Settings
(`server_settings.confirmable_public_url`): one that resolves to an
address on the internet is refused, since assistants send their tokens to
it in the clear, and so is one whose lookup does not answer in time
([https.md](https.md#plain-http)). `GAMMA_PUBLIC_URL` is taken as written.
Any other host needs HTTPS. Browser sign-in over plain HTTP needs that
confirmed address: a name `GAMMA_MCP_ALLOWED_HOSTS` allows takes manual
tokens alone (`mcp_oauth.public_base`). Gamma Cloud
uses only an https or loopback address (`cloud_auth.cloud_public_url`):
with a plain-HTTP one of this network, cloud sign-in and the server list
go as with no public URL ([cloud_accounts.md](cloud_accounts.md)).

`GAMMA_PUBLIC_URL` overrides the saved address and makes the field read-only. Its hostname is
automatically allowed too. `GAMMA_MCP_ALLOWED_HOSTS` adds other allowed host
authorities, for example a second name of the server for manual tokens.
Localhost and loopback remain allowed by default. A path-prefixed
deployment has no browser sign-in and can use manual tokens.

OAuth handles sign-in, not network reachability. A remote assistant still needs
to reach Gamma's server. A public listing cannot access a user's localhost by itself.

Browser-origin requests to `/mcp` are rejected. This endpoint targets native
MCP clients; it does not enable cross-origin browser access. The official Python
MCP SDK handles protocol negotiation, request validation, and the stateless JSON
transport. Request bodies are limited to 1 MiB (`MAX_BODY`). The backend lifespan owns the
SDK manager; it must run when embedding Gamma's ASGI app.

## Browser sign-in protocol

Gamma provides OAuth authorization-server discovery, protected-resource discovery,
dynamic public-client registration, and authorization-code exchange with S256
PKCE. The SDK validates client identity, redirect matching, and PKCE. Gamma adds:

- resource binding and an explicit approval screen, where the user ticks the
  workspaces the assistant may read (or all of them, those added later too)
  and marks those it may comment in ("The Comment level") and those whose
  notes it may also edit ("Editing notes");
  the code carries the grant, rechecked against the account at the exchange;
- session-bound consent, rate and body limits;
- persistent expiring records and atomic single-use codes.

Two scopes exist, `gamma:read` and `gamma:feedback` (`mcp_oauth.SCOPES`;
`COMMENT_SCOPE`, the Comment level's name, kept from when it was a token
scope so a client that asks for it goes on working): a client may register
with and ask for either or both, every client is stored with both, and the
401 challenge still names `gamma:read` alone, so a client that reads it asks
to read. What the connection may do is decided on the consent page, not by
the request: a client that asked for `gamma:feedback` finds the open
workspace at Comment to begin with (`comments_asked`), and the person sets
each workspace's level either way. The token is a `read` one; when it
comments anywhere the code carries both scopes and the token response says
`gamma:read gamma:feedback`, else `gamma:read`. `gamma:write` is refused at registration
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

- An integration token belongs to one workspace, its `workspace_id`: `read` (the default; the MCP endpoint and the HTTP API's reads; a viewer may make one) or `write` (a mirror's push credential, [mirror.md](mirror.md); refused to viewers); a third, `computer`, is a computer's channel to the server ([computers.md](computers.md)), which opens nothing else and no MCP connection; a fourth, `turn`, is the token of one turn of an agent on this computer ("The token of an agent's turn" below), which opens MCP alone. A read token's MCP connection may read more, its `workspaces` (`integrations.grant`): `''` for its own workspace alone (every token from before step 51), `'*'` for every workspace the account can open, now and later, or a JSON list of ids, its own first; and of those, the ones it may also comment in, its `comments` (`integrations.comment_grant`, "The Comment level"), and the ones whose notes it may also edit, its `edits` (`integrations.edit_grant`, "Editing notes"), each `''`, `'*'` or a list, edits only where the account edits. A write token is always its workspace alone. On the HTTP API a manual token is a bearer credential — the account behind it, confined to its own workspace whatever it grants MCP, never an admin, and refused (403) by every account-level endpoint — AI provider entries, preferences, AI usage, the workspace list, backups, tokens — whatever its scope ([api.md](api.md) "Integrations"). A `read` token writes nothing there: every write endpoint answers 403 "this token is read-only" (chats and cover snapshots included), but for its comments in its own workspace when it comments there (`auth.token_comments`), whatever else its MCP connection reads. `POST /api/ai/chat` through it arms the reading tools only (`auth.can_write`), and the writing tools where it comments (`auth.can_comment`), so its agent cannot rename, move or edit. On MCP, a tool's `workspace` argument reaches only the connection's workspaces; on the HTTP API,
  `?ws=` and `X-Gamma-Workspace` cannot select another workspace.
- Only token SHA-256 hashes are stored in `users.db`. Tokens contain 256 random bits.
- HTTP share URLs also stay within the integration token's workspace and
  write scope: neither `?share=` nor `/api/share/{token}` can borrow the
  account's access to another workspace, and a share cannot turn a read
  token into a writer. `auth.request_share_access` applies this boundary
  to the link resolver and the shared data endpoints.
- Tokens expire after 90 days by default (API range: 1–365 days); accounts may
  have at most 20 unexpired tokens (a turn's not counted). Revoke a connection in the same settings panel.
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
  for the three writing tools in the workspaces a connection may comment in
  ("The Comment level") and the note, page and drawing tools in those it
  may edit, `delete_block` only where deleting was allowed too ("Editing
  notes"). The allowlist is enforced on every dispatch, independent of
  which tools the client was offered. Deprecated chat aliases are not accepted
  by the MCP transport.
- Token management requires a normal Gamma session and never accepts an integration
  token. A browser session alone cannot authenticate to `/mcp`.

Management API (session authenticated; account-wide, the current workspace is
the default grant and a write token's workspace):

```text
GET    /api/integrations/tokens                 the account's connections, its workspaces
POST   /api/integrations/tokens       {"name":"Codex","expires_in_days":90,"scope":"read","workspaces":[…],
                                       "comment_workspaces":[…],"edit_workspaces":[…]}
                                      scope: read (the default), write or computer (the last two: the current workspace alone)
PATCH  /api/integrations/tokens/{id}  {"workspaces":[…]} or {"all_workspaces":true}   a read token,
                                      with "comment_workspaces":[…] or "comment_all":true (of those): the ones it may
                                      comment in, "edit_workspaces":[…] or "edit_all":true: the ones it may edit,
                                      and "asks": whether each change asks first (left out: as it was)
DELETE /api/integrations/tokens/{id}
```

None of them lists, changes or revokes a turn's token: the list leaves it
out, the PATCH answers 404 and the DELETE `revoked: false`.

### The token of an agent's turn

Codex or Claude Code turned on for this computer
([local_agents.md](local_agents.md) "Gamma's tools") reads Gamma through
this endpoint during a turn of this server, with a token of the turn's
own (`integrations.mint_turn`, scope `turn`); so does the agent of another
computer during a turn of this server's it takes over its channel, with a
token this server minted at the claim ([computers.md](computers.md) "The
agents' turns there"):

- the turn owner's, of the turn's workspace alone (`workspaces` `''`,
  `edits` `''`), named with the agent's id (`computer:<this
  computer>:<name>`, or the other computer's id here, so what it writes is
  that agent's, on the computer as it is called here: `agents.of_token`),
  so `list_workspaces` names
  that one workspace and the read tools (`READ_TOOLS`, with
  `list_workspaces`, `read_gamma_link` and `export_page`) are offered and
  dispatched as for a read token, and the writing tools as at the Comment
  level there (`integrations.comments_in`): its comments, resolves and
  highlights are signed and stamped as that computer's agent, for the turn's
  owner; the note tools are refused;
- living the run's timeout and a minute, renewed at each start of the
  agent and while an ask holds the turn (a far turn's by its writes),
  deleted when the turn ends however it ends (`integrations.drop`) and,
  for what a desktop app's server that stopped left, at the next start
  (`integrations.drop_turns`);
- refused by every route of the HTTP API (403 `auth.TURN_ONLY`), its reads
  included, as a `computer` token is outside `auth.CHANNEL_PREFIX`: a read
  token opens the API's reads as well as MCP, and this one should not;
- no connection: never listed in Settings, none of the twenty, never in
  Background tasks or a log, and the agent gets it in an environment
  variable of its own process alone, never on a command line or in a file.

The address the agent is given is this server's `/mcp` as a process on
this computer reaches it, `http://127.0.0.1:<port>/mcp` on the desktop
app's server (`local_agents.tools.mcp_url`), which the transport's
allowed hosts take at any port, under the server name `gamma_turn`
(`events.MCP_NAME`), never the user's own `gamma` connection.

### A setup command's sign-in

A client without browser sign-in of its own (DeepSeek Harness) is signed in
by its setup command, the way a TV or a CLI signs in: OAuth's device flow
(RFC 8628) in Gamma's own shape, on its own endpoints, not advertised in the
OAuth metadata. The command holds nothing secret, so the tab shows it at once.

```text
POST /api/integrations/device          {"name":"DeepSeek Harness"}   no session; rate-limited per address
     → {device_code, user_code: "BCDF-GHJK", expires_in: 600, interval: 2}
GET  /api/integrations/device/request?user_code=…   session: who asks, the code, the workspaces, a csrf
POST /api/integrations/device/consent  {user_code, csrf, workspaces | all_workspaces, comment_workspaces | comment_all,
                                        edit_workspaces | edit_all, asks, approve}
POST /api/integrations/device/token    {device_code}   no session: 202 {"status":"pending"} while it waits,
     then {token, name, expires_at} once; 403 cancelled; 400 expired, used or signed out
```

The command opens `/?gamma_device=<user_code>`, which App routes to the
consent page (`McpConsent.jsx`) as it does `?gamma_oauth=`: sign in if need
be, the same workspace checklist with its levels, and the code to check
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

### An older plugin

An installed plugin does not update itself: setup registers it from a local
folder (`gamma-local`), so neither client has an upstream copy to refresh
from. What updates is the server, whose instructions every client reads again
at each session's `initialize`. A release build (`version.VERSION` an
`X.Y.Z`; a `-dev` build or a checkout says nothing) ends them with the plugin
version of its own release and how each assistant updates to it
(`mcp_server.plugin_hint`): Codex and DeepSeek Harness by running the setup
command again, Claude Code by extracting the release's ZIP over its
`gamma-marketplace` folder and running `claude plugin marketplace update
gamma-local` and `claude plugin update gamma@gamma-local`. The skill names
its own plugin version (`SKILL.md`'s first line, which
`package_plugins.load_manifests` holds to the manifests and the release
stamps, `stamp_skill`) and tells the user once a conversation when the
server's is newer. The comparison is the model's, so it is a hint; a skill
older than the hint itself has no such line and says nothing.

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
token and gone once deleted); the Comment level (the writing tools listed for
a connection that comments alone, refused to a read connection even right
after a commenting one listed them, a reply with its stamp, the room and the
Inbox told, the same words again written once, a library page's thread
answered, a resolve repeated idempotently and never a reopen, a read-only
server refused; the level granted per workspace, a viewer's included, only of
those it reads, the edit level commenting too, every workspace, a deleted one
leaving the grant), and a thread id finding its workspace on a connection of
two, or refused when both hold it, and a bare highlight's id, a library page
and a library folder read through `/mcp`.
`test_annotations_hand.py` covers the writing tools' places and refusals
(a PDF quote, a text file's lines with and without their quote, a note, the
page; a quote not there with the closest passage), a reply, a resolve, a
highlight, a call made again, Gamma AI's permission and a viewer's chat, and
the level of a read, a commenting, a write and a turn token over MCP and the
HTTP API.
`test_annotations_tools.py` covers `list_annotations` and `read_annotation`
in the chat's scopes (the library, a work folder, a directory, a file, a
name, a library folder by id or path, a library page and its bare
highlight, batches, a page chat, the reach refused, the
item's JSON and pictures, the old names replayed); `test_mcp_links.py`
`holders` by thread. `test_token_api.py` covers the Tokens
pane's Comment level (a viewer's, several workspaces, all of them, its PATCH,
the scope `feedback` refused) and the token's HTTP reads and comments kept to
its first workspace. `test_mcp_oauth.py` covers discovery, approval (several
workspaces, or all; the Comment level given or not to a client that asked
for `gamma:feedback`, and to every workspace; `gamma:write` refused),
PKCE, resource/client/redirect binding, expiration, replay prevention,
revocation, and streamed body limits.
It also covers HTTPS proxy consent with saved and environment-configured public
URLs, including approval, cancellation, and rejection of foreign origins.
`test_integration_lifecycle.py` covers manual and OAuth tokens across account
rename, password changes through the admin API and CLI, workspace deletion,
and account deletion through the admin API and `manage.py delete-user`.
`test_migrations.py` covers
schema upgrades, step 61's `feedback` tokens made `read` ones at the Comment
level among them. Browser scenarios cover sign-in, ticking a second workspace,
approval, cancellation, and MCP reads, each assistant's tab, a token's
workspaces and changing them, the account-wide list and revocation, and the
DeepSeek Harness setup command, shown at once, and its sign-in allowed in the
browser and collected once; a workspace marked Edit, and asking turned off,
in a connection's editor and on the consent page; a client asking for
`gamma:feedback` starting at Comment, and its connection commenting under its
name; a token made at Comment in the Tokens pane; an assistant's comment on a
PDF quote highlighting it on the page (`comments`); and Gamma's question about
a change streamed to a client over a real session and answered: allowed,
declined, allowed with "don't ask again", then not asked.
