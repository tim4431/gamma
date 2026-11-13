# Gamma PDF for Codex, Claude Code, and DeepSeek Harness

This plugin supplies the Gamma workflow and display identity. Configure the
Gamma MCP connection separately: every Gamma installation has its own address
and workspace authorization. The plugin contains no credentials or hardcoded
server address.

## Claude Code

From a Gamma checkout, build a marketplace into a permanent directory:

```text
python tools/package_plugins.py --output <permanent-directory>/gamma-marketplace --archive
claude plugin marketplace add <permanent-directory>/gamma-marketplace
claude plugin install gamma@gamma-local --scope user
claude mcp add --transport http --scope user gamma <your-gamma-address>/mcp
```

Alternatively, download `gamma-claude-code-plugin-X.Y.Z.zip` from a Gamma release,
extract it into a permanent location, and add the extracted `gamma-marketplace`
directory with the same marketplace command. Replace the MCP URL with the one
shown in Gamma's **Settings → AI → Integrations**. Use HTTPS for remote servers;
HTTP localhost is supported.

Start Claude Code, open `/mcp`, select `gamma`, and authenticate. Sign in to Gamma
in the browser and approve read-only access to your chosen workspaces; the consent
page can also let Claude Code answer your comments on a work folder's files
(reply and resolve), and nothing else. Start a new
session and run `/gamma:gamma`, or ask Claude to find your Gamma notes. If tools
are missing, inspect `/mcp` or `claude mcp get gamma` before retrying sign-in.
Copy the Gamma page URL from your browser and paste it with your question.
A share link works too.

To update an extracted marketplace, replace its contents with the newer release.
Then run `claude plugin marketplace update gamma-local`
and `claude plugin update gamma@gamma-local`, then start a new session. For local
development, load this directory directly with `claude --plugin-dir ./plugins/gamma`
from the repository root; the MCP connection still needs separate setup.

To remove Gamma from Claude Code, run `claude mcp remove gamma --scope user`
and, if you installed the plugin, `claude plugin uninstall gamma@gamma-local
--scope user` and `claude plugin marketplace remove gamma-local`. Then
disconnect Claude Code under **Connected assistants** in Gamma.

The plugin layout follows the [Claude Code plugin reference](https://code.claude.com/docs/en/plugins-reference);
connection setup follows the [Claude Code MCP guide](https://code.claude.com/docs/en/mcp).

## DeepSeek Harness

This directory is also a DeepSeek Harness plugin: `package.json` declares a dsh
bundle whose `cordis.patch.yml` adds one row, `dsh-gamma.js`, which mounts the
same `skills/gamma` skill and the Gamma MCP connection. In Gamma, open
**Settings → AI → Integrations → DeepSeek Harness**:

1. Quit DeepSeek Harness, then run the setup command the tab shows. It adds
   the plugin to the desktop app's profile (and to `web`, where `dsh web` has
   made one), then signs in for dsh, whose MCP client has no browser sign-in.
2. In the browser page it opens, check that the code matches the terminal's,
   tick the workspaces it may read and allow it. The command saves the
   read-only connection in `~/.dsh/gamma.json`.
3. Open DeepSeek Harness. The plugin reads the saved connection every time
   dsh starts, from the app or the terminal.

`GAMMA_URL` and `GAMMA_TOKEN` in the environment take the file's place. To
remove Gamma, quit DeepSeek Harness and run the command under **Remove Gamma
from DeepSeek Harness** in the same tab: it runs the setup script with
`-Remove` (`--remove` on macOS / Linux), which takes the plugin out of every
profile that has it and deletes `~/.dsh/gamma.json`. Then disconnect DeepSeek
Harness under **Connected assistants** in Gamma. For
local development with dsh 0.1.7 or later, add this directory itself:
`dsh plugin --profile web add ./plugins/gamma`. Details:
[the integration guide](../../docs/dev/mcp.md#deepseek-harness).

## Codex

For the combined installer, open **Settings → AI → Integrations → Codex**,
select your operating system, and copy the setup command. It downloads
the plugin from Gamma's latest release, installs it, and connects this server.
Requires an installed Codex CLI and a release containing the setup scripts.
Tick the workspaces in your browser, then start a new chat. Gamma's desktop app
is not required. To connect manually or install a development package:

1. Update/start Gamma and open **Settings → AI → Integrations**.
2. Copy the server URL into your assistant's MCP settings and sign in with Gamma.
   For Codex CLI, copy the setup commands shown in Gamma, or:

   ```text
   codex mcp add gamma --url <your-gamma-address>/mcp
   ```

   If sign-in is interrupted, retry with `codex mcp login gamma`.
   Tick the workspaces and approve read-only access in your browser; the consent
   page can also let Codex answer your comments on a work folder's files (reply
   and resolve), and nothing else. No manual token
   or environment variable is needed. HTTPS is required for remote servers;
   HTTP localhost is supported. Manual tokens remain available for other clients.

3. Add the marketplace repository or extracted directory with
   `codex plugin marketplace add <owner/repo-or-directory>`.
   Install **Gamma PDF** using the desktop plugin browser or `/plugins` in the
   CLI. Start a new conversation. Select Gamma from the available plugin/skill
   picker, or invoke `$gamma` in the CLI.

The IDE extension can use the direct MCP connection without the plugin.

To remove Gamma from Codex, run `codex mcp remove gamma`,
`codex plugin remove gamma@gamma-local` and
`codex plugin marketplace remove gamma-local`, then disconnect Codex under
**Connected assistants** in Gamma.

Paste a Gamma page or share link with your question. The assistant uses
`read_gamma_link` to resolve and read it. Copy the page URL directly from your
browser's address bar, or use an existing block or share link.
All three assistants use the same workflow. Follow-up questions keep the last supplied
page as context until you send a different reference.

Gamma must be running and reachable from the machine running the MCP client.
The packaged workflow uses the configured tools; installing it alone does not
establish a connection. Public directory publication is a separate review process.

If setup reports successful login but the assistant has no Gamma tools, inspect
`codex mcp get gamma` in your terminal. A server disabled by managed requirements
needs administrator approval in Codex's managed policy; reinstalling or repeating
OAuth cannot enable it. If the server is enabled, restart the Codex app and start
a new task, then inspect MCP startup errors if tools are still missing. An OAuth
entry in Gamma confirms a grant of access, not that Codex loaded its MCP tools.

For remote hosts, HTTPS, troubleshooting, token management, and release
instructions, see [the integration guide](../../docs/dev/mcp.md).

To build a distributable marketplace from a Gamma source checkout:

```text
python tools/package_plugins.py --output tmp/gamma-marketplace --archive
```

The output includes installation instructions, the hidden marketplace catalog,
plugin files for both clients, and a ZIP. Publish the generated directory as the root of your
marketplace repository, including hidden files. Gamma's normal release workflow
also publishes a versioned plugin ZIP and Windows/macOS/Linux setup scripts.
The `Assistant plugin package` workflow tests them and builds preview artifacts.

## Shared implementation

Both manifests live in this directory and point to `skills/`; the dsh
`package.json` serves the same `skills/` through `dsh-gamma.js`. Edit
`skills/gamma/SKILL.md` once to change the workflow for both clients. Codex's
display metadata stays in `.codex-plugin/plugin.json` and `agents/openai.yaml`;
Claude Code reads `.claude-plugin/plugin.json`. The builder checks shared
manifest metadata for drift and copies an explicit allowlist of distributable files.
There are no symlinks or cross-plugin references that could break in a client cache.

`tools/package_plugins.py` creates both marketplace catalogs around this one plugin.
`tools/release_plugins.py` versions both manifests together and produces both
downloads from identical package bytes, preserving existing Codex asset names and
setup scripts. Connection, authentication, and read tools remain in the shared
Gamma MCP backend; per-user addresses and credentials never enter the package.
