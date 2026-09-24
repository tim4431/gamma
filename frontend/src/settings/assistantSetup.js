const releaseBase = "https://github.com/tim4431/Gamma/releases/latest/download";

function shellQuote(value, platform) {
  // These are shell literals, not JavaScript/JSON strings. Server URLs may be
  // supplied by a self-hosted deployment and must never become shell syntax.
  return platform === "windows"
    ? "'" + value.replaceAll("'", "''") + "'"
    : "'" + value.replaceAll("'", "'\"'\"'") + "'";
}

export function codexSetupCommand(serverUrl, platform) {
  const quote = (value) => shellQuote(value, platform);
  if (platform === "windows") {
    return `& ([scriptblock]::Create((Invoke-RestMethod ${quote(releaseBase + "/install-gamma-codex.ps1")}))) -ServerUrl ${quote(serverUrl)}`;
  }
  // Download completely before executing, and leave stdin attached to the
  // terminal so Codex can open its interactive browser authorization flow.
  // The subshell keeps the cleanup trap local to setup, including failed downloads.
  return `(gamma_setup=$(mktemp) && trap 'rm -f "$gamma_setup"' 0 && curl -fsSL ${quote(releaseBase + "/install-gamma-codex.sh")} -o "$gamma_setup" && sh "$gamma_setup" ${quote(serverUrl)})`;
}

export function claudeConnectCommand(serverUrl, platform, { replace = false } = {}) {
  const add = `claude mcp add --transport http --scope user gamma ${shellQuote(serverUrl, platform)}`;
  return replace ? `claude mcp remove gamma --scope user\n${add}` : add;
}

// Run from the parent of the extracted gamma-marketplace directory on either OS.
export const claudePluginInstallCommands = "claude plugin marketplace add ./gamma-marketplace\nclaude plugin install gamma@gamma-local --scope user";

// DeepSeek Harness installs Gamma as a dsh bundle (plugins/gamma/package.json +
// cordis.patch.yml). pnpm cannot reuse a remote tarball it has already resolved
// (a second profile or an update fails for want of an integrity hash), so the
// command downloads the release tarball to a fixed file in the dsh home and adds
// that file; running it again updates in place.
export function dshInstallCommand(platform) {
  const url = shellQuote(releaseBase + "/dsh-gamma.tgz", platform);
  if (platform === "windows") {
    return "$dshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $HOME '.dsh' }\n"
      + "New-Item -ItemType Directory -Force $dshHome | Out-Null\n"
      + "$bundle = Join-Path $dshHome 'dsh-gamma.tgz'\n"
      + `Invoke-WebRequest -UseBasicParsing ${url} -OutFile $bundle\n`
      + "npx @deepseek-ai/dsh plugin --profile web add $bundle";
  }
  return `(dsh_home="\${DSH_HOME:-$HOME/.dsh}" && mkdir -p "$dsh_home" && curl -fsSL -o "$dsh_home/dsh-gamma.tgz" ${url}`
    + ` && npx @deepseek-ai/dsh plugin --profile web add "$dsh_home/dsh-gamma.tgz")`;
}

// dsh's MCP client has no OAuth: the bundle reads the address and a manual
// token from the environment when dsh starts. The token is typed at a prompt,
// never into the command, so it stays out of shell history.
export function dshStartCommand(serverUrl, platform) {
  const url = shellQuote(serverUrl, platform);
  if (platform === "windows") {
    return `$env:GAMMA_URL = ${url}\n$env:GAMMA_TOKEN = Read-Host 'Gamma token'\nnpx @deepseek-ai/dsh web`;
  }
  return `export GAMMA_URL=${url}\nprintf 'Gamma token: '; read -r GAMMA_TOKEN; export GAMMA_TOKEN\nnpx @deepseek-ai/dsh web`;
}
