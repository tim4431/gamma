import test from "node:test";
import assert from "node:assert/strict";
import { claudeConnectCommand, claudePluginInstallCommands, dshInstallCommand, dshStartCommand } from "../src/settings/assistantSetup.js";

test("Claude connection uses the current server URL and user scope on either platform", () => {
  for (const platform of ["windows", "unix"]) {
    assert.equal(claudeConnectCommand("http://localhost:9001/mcp", platform),
      "claude mcp add --transport http --scope user gamma 'http://localhost:9001/mcp'");
    assert.equal(claudeConnectCommand("https://new.example/mcp", platform, { replace: true }),
      "claude mcp remove gamma --scope user\nclaude mcp add --transport http --scope user gamma 'https://new.example/mcp'");
  }
});

test("Claude server URLs remain shell literals", () => {
  const url = "https://example/mcp'$(whoami)`&\"";
  assert(claudeConnectCommand(url, "windows").endsWith("'https://example/mcp''$(whoami)`&\"'"));
  assert(claudeConnectCommand(url, "unix").endsWith("'https://example/mcp'\"'\"'$(whoami)`&\"'"));
});

test("plugin installation is independent of a user's server address and credentials", () => {
  assert.equal(claudePluginInstallCommands,
    "claude plugin marketplace add ./gamma-marketplace\nclaude plugin install gamma@gamma-local --scope user");
});

test("DeepSeek Harness installs the release bundle from a fixed file in the dsh home", () => {
  assert.equal(dshInstallCommand("unix"),
    `(dsh_home="\${DSH_HOME:-$HOME/.dsh}" && mkdir -p "$dsh_home" && curl -fsSL -o "$dsh_home/dsh-gamma.tgz" 'https://github.com/tim4431/Gamma/releases/latest/download/dsh-gamma.tgz' && npx @deepseek-ai/dsh plugin --profile web add "$dsh_home/dsh-gamma.tgz")`);
  const windows = dshInstallCommand("windows").split("\n");
  assert.equal(windows[3], "Invoke-WebRequest -UseBasicParsing 'https://github.com/tim4431/Gamma/releases/latest/download/dsh-gamma.tgz' -OutFile $bundle");
  assert.equal(windows[4], "npx @deepseek-ai/dsh plugin --profile web add $bundle");
});

test("DeepSeek Harness start prompts for the token and keeps the address a shell literal", () => {
  assert.equal(dshStartCommand("http://localhost:9001/mcp", "unix"),
    "export GAMMA_URL='http://localhost:9001/mcp'\nprintf 'Gamma token: '; read -r GAMMA_TOKEN; export GAMMA_TOKEN\nnpx @deepseek-ai/dsh web");
  assert.equal(dshStartCommand("http://localhost:9001/mcp", "windows"),
    "$env:GAMMA_URL = 'http://localhost:9001/mcp'\n$env:GAMMA_TOKEN = Read-Host 'Gamma token'\nnpx @deepseek-ai/dsh web");
  const url = "https://example/mcp'$(whoami)";
  assert(dshStartCommand(url, "unix").startsWith("export GAMMA_URL='https://example/mcp'\"'\"'$(whoami)'\n"));
  assert(dshStartCommand(url, "windows").startsWith("$env:GAMMA_URL = 'https://example/mcp''$(whoami)'\n"));
});
