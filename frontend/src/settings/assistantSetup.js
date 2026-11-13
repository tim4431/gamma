const releaseBase = "https://github.com/tim4431/Gamma/releases/latest/download";

function shellQuote(value, platform) {
  // These are shell literals, not JavaScript/JSON strings. Server URLs may be
  // supplied by a self-hosted deployment and must never become shell syntax.
  return platform === "windows"
    ? "'" + value.replaceAll("'", "''") + "'"
    : "'" + value.replaceAll("'", "'\"'\"'") + "'";
}

// Run one of the release's setup scripts (`<name>.ps1` / `<name>.sh`) with
// `args`, [[PowerShell parameter, value]] in the shell script's order; a
// parameter without a value is a switch (`-Remove`, `--remove` for sh).
function releaseScript(name, args, platform) {
  const quote = (value) => shellQuote(value, platform);
  if (platform === "windows") {
    return `& ([scriptblock]::Create((Invoke-RestMethod ${quote(`${releaseBase}/${name}.ps1`)}))) `
      + args.map(([flag, value]) => value === undefined ? `-${flag}` : `-${flag} ${quote(value)}`).join(" ");
  }
  // Download completely before executing, and leave stdin attached to the
  // terminal so Codex can open its interactive browser authorization flow.
  // The subshell keeps the cleanup trap local to setup, including failed downloads.
  return `(gamma_setup=$(mktemp) && trap 'rm -f "$gamma_setup"' 0 && curl -fsSL ${quote(`${releaseBase}/${name}.sh`)} -o "$gamma_setup" && sh "$gamma_setup" `
    + args.map(([flag, value]) => value === undefined ? `--${flag.toLowerCase()}` : quote(value)).join(" ") + ")";
}

export function codexSetupCommand(serverUrl, platform) {
  return releaseScript("install-gamma-codex", [["ServerUrl", serverUrl]], platform);
}

export function claudeConnectCommand(serverUrl, platform, { replace = false } = {}) {
  const add = `claude mcp add --transport http --scope user gamma ${shellQuote(serverUrl, platform)}`;
  return replace ? `claude mcp remove gamma --scope user\n${add}` : add;
}

// Run from the parent of the extracted gamma-marketplace directory on either OS.
export const claudePluginInstallCommands = "claude plugin marketplace add ./gamma-marketplace\nclaude plugin install gamma@gamma-local --scope user";

// DeepSeek Harness has no browser sign-in of its own: its setup script
// (tools/install-gamma-dsh.*) adds the Gamma plugin to the dsh profiles on that
// computer, signs in for it (it opens Gamma's sign-in in the browser and waits
// for the user to allow it) and saves the token where the plugin reads it when
// dsh starts. The token never appears in the command or on screen.
export function dshSetupCommand(serverUrl, platform) {
  return releaseScript("install-gamma-dsh", [["ServerUrl", serverUrl]], platform);
}

// Taking Gamma out of each assistant: its MCP connection and, where setup
// installed one, the plugin and its local marketplace. The same on every
// platform but DeepSeek Harness's, whose script removes the plugin from the
// profiles that have it and deletes the saved connection. Disconnecting in
// Gamma ends the access itself.
export const claudeRemoveCommands = "claude mcp remove gamma --scope user\n"
  + "claude plugin uninstall gamma@gamma-local --scope user\nclaude plugin marketplace remove gamma-local";
export const codexRemoveCommands = "codex mcp remove gamma\ncodex plugin remove gamma@gamma-local\n"
  + "codex plugin marketplace remove gamma-local";
export function dshRemoveCommand(platform) {
  return releaseScript("install-gamma-dsh", [["Remove"]], platform);
}
