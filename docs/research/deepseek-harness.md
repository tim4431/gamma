# Connecting DeepSeek Harness to Gamma

November 2026, DeepSeek Harness (dsh) 0.2.2-rc.1, the Windows desktop app and
the npm CLI. The question: why a user who ran the Integrations pane's two
commands had Gamma's tools in the session those commands started and nowhere
else, and what a connection that lasts needs. Read from the installed app,
the npm packages' sources and READMEs, and a scratch `DSH_HOME`; the
mechanics that came of it are in [dev/mcp.md](../dev/mcp.md#deepseek-harness).

## What went wrong with the two commands

The pane's install command ran `npx @deepseek-ai/dsh plugin --profile web add`
and its start command set `GAMMA_URL` and `GAMMA_TOKEN` in the shell, then ran
`npx @deepseek-ai/dsh web`. Two things kept that from lasting:

- **The wrong profile.** A dsh profile is a directory,
  `$DSH_HOME/profiles/<name>`, with its own `package.json` (the bundles it
  loads, `dsh.profile.bundles`), `cordis.patch.yml` (the user's layer) and
  `node_modules`. `dsh web` boots `web`; the desktop app boots its own
  reserved `desktop` profile, which the CLI's profile lookup does not expose.
  The plugin went into `web` only, so the app never loaded it.
- **The environment.** The bundle's MCP row read the address and token from
  `process.env` when dsh started and was disabled without them. The app,
  started from the Start menu, has neither, and a terminal's `$env:` lasts as
  long as that terminal.

## What dsh offers

- **The MCP client** (`@deepseek-ai/dsh-mcp-client`): stdio or Streamable
  HTTP, `url` and `headers` as plain strings. No OAuth, and its headers are a
  plain dictionary, not a credential reference, so it cannot read dsh's own
  secret store. A failing initial connection logs and boots on; a broken row
  config stops the boot.
- **The secret store** (`$DSH_HOME/.credentials.yaml`, `refs` and `records`):
  the LLM providers' `apiKeyEnv` resolves through it (the field has the
  `credential-ref` role), the MCP client's fields do not.
- **Patch layers**: each bundle's patch, then the profile's
  `cordis.patch.yml`, then a home-level `$DSH_HOME/cordis.patch.yml` that
  outranks every profile, with `!!js` expressions. A home patch could carry
  the MCP row to every profile, but an empty, comments-only or malformed file
  stops every profile from booting, and editing a user's YAML from a setup
  script is fragile.
- **A plugin module**: a patch row can name a module relative to its bundle.
  The module's `apply(ctx)` can mount dsh's own plugins with any config
  (`ctx.plugin(McpClient, {...})`), resolved by the running dsh as peer
  dependencies, the way the skill was already mounted.
- **The CLIs**: the desktop app ships its own
  (`%LOCALAPPDATA%\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd`,
  Electron run as Node, with its own pnpm 11), and can put it on `PATH`. Only it
  manages the `desktop` profile, and only after the app has created it and
  while the app is quit: otherwise "Open DeepSeek Harness Desktop once to
  initialize its profile, then fully quit it". The check is the profile's, not
  the process's: with the app open, the same CLI added a plugin to a scratch
  home's `desktop` profile. `dsh plugin --profile <p> add <spec>` is pnpm in
  the profile directory; a remote tarball cannot be added twice (pnpm 10's
  missing integrity), a local file can.
- **The app's Plugins page** installs bundles by registry name, git address or
  tarball while it runs; dsh-gamma is not on npm, so there is nothing to
  name there yet.

## What was chosen

The bundle's one row is a module, `dsh-gamma.js`, that reads the connection
from `$DSH_HOME/gamma.json` when dsh starts (the environment variables still
win) and mounts the MCP client with it, or only the skill without one. A file
of its own is as private as the store dsh keeps its keys in, and needs no YAML
edited. The setup script (from the release, like Codex's) adds the plugin to
`desktop` with the app's CLI and to `web` where it exists, and saves the file.
The token reaches the script through a one-time setup code the browser asks
for, so the command can be pasted whole and only a spent code stays in shell
history. Reconnecting finds the plugin in place and rewrites the file, so only
the first setup needs the app quit.

Verified with the app's CLI on a scratch home: the plugin added to a desktop
profile, the code claimed, the file written without a byte-order mark
(PowerShell 5.1's `-Encoding utf8` writes one, and dsh's JSON reader refuses
it), and a booted `web` profile reading the file, initializing with Gamma and
listing its tools.

## Still open

- macOS and Linux paths of the desktop app's CLI are inferred from the
  Windows layout and Electron's defaults; no Mac or Linux install was read.
- Publishing dsh-gamma to npm would let the app's Plugins page install it by
  name while running; the connection would still need the setup command.
- dsh's MCP client might take OAuth later (its SDK has it); its own sign-in
  would then replace the setup command's.

## Later: the setup command signs in

The setup code made the tab differ from Codex's for no reason of dsh's own: a
code had to be asked for with a button, since it was a credential on screen,
and the workspaces were ticked in the tab before it. The script now signs in
for dsh the way a CLI or a TV does (OAuth's device flow, RFC 8628): it asks
Gamma for a sign-in, prints a short code and opens the consent page on it,
where the user checks the code, ticks the workspaces and allows it, and
collects the token. PowerShell could have run Codex's loopback redirect, but a
POSIX `sh` has no listener of its own (only `nc`, `python` or Node, none of
them sure to be there), and asking every two seconds needs only `curl`. The
command now holds nothing secret, so the tab shows it at once, as Codex's
does ([dev/mcp.md](../dev/mcp.md#a-setup-commands-sign-in)).
