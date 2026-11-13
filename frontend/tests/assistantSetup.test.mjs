import test from "node:test";
import assert from "node:assert/strict";
import { codexSetupCommand, claudeConnectCommand, claudePluginInstallCommands, dshSetupCommand,
  claudeRemoveCommands, codexRemoveCommands, dshRemoveCommand } from "../src/settings/assistantSetup.js";

test("Codex setup downloads the release installer and passes this server", () => {
  for (const platform of ["windows", "unix"]) {
    const command = codexSetupCommand("https://gamma.example/mcp", platform);
    assert(command.includes("https://github.com/tim4431/Gamma/releases/latest/download/install-gamma-codex."));
    assert(command.endsWith("'https://gamma.example/mcp'" + (platform === "unix" ? ")" : "")));
    assert(!command.includes("GAMMA_TOKEN"));
    assert(!command.includes("| sh"), "OAuth must retain terminal stdin");
  }
});

test("Codex server text is shell quoted rather than interpreted", () => {
  const value = "https://example/mcp'$(whoami)`&\"";
  assert(codexSetupCommand(value, "windows").endsWith("'https://example/mcp''$(whoami)`&\"'"));
  assert(codexSetupCommand(value, "unix").endsWith("'https://example/mcp'\"'\"'$(whoami)`&\"')"));
});

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

test("DeepSeek Harness setup runs the release installer with this server alone", () => {
  assert.equal(dshSetupCommand("http://localhost:9001/mcp", "windows"),
    "& ([scriptblock]::Create((Invoke-RestMethod 'https://github.com/tim4431/Gamma/releases/latest/download/install-gamma-dsh.ps1'))) "
    + "-ServerUrl 'http://localhost:9001/mcp'");
  assert.equal(dshSetupCommand("http://localhost:9001/mcp", "unix"),
    "(gamma_setup=$(mktemp) && trap 'rm -f \"$gamma_setup\"' 0 && curl -fsSL 'https://github.com/tim4431/Gamma/releases/latest/download/install-gamma-dsh.sh' "
    + "-o \"$gamma_setup\" && sh \"$gamma_setup\" 'http://localhost:9001/mcp')");
  for (const platform of ["windows", "unix"]) {
    const command = dshSetupCommand("https://example/mcp'$(whoami)", platform);
    assert(!command.includes("GAMMA_TOKEN") && !command.includes("Read-Host"), "no token is typed or pasted");
    assert(command.includes(platform === "windows" ? "'https://example/mcp''$(whoami)'" : "'https://example/mcp'\"'\"'$(whoami)'"));
  }
});

test("each assistant's removal undoes its setup", () => {
  assert.equal(claudeRemoveCommands, "claude mcp remove gamma --scope user\n"
    + "claude plugin uninstall gamma@gamma-local --scope user\nclaude plugin marketplace remove gamma-local");
  assert.equal(codexRemoveCommands, "codex mcp remove gamma\ncodex plugin remove gamma@gamma-local\ncodex plugin marketplace remove gamma-local");
  assert.equal(dshRemoveCommand("windows"),
    "& ([scriptblock]::Create((Invoke-RestMethod 'https://github.com/tim4431/Gamma/releases/latest/download/install-gamma-dsh.ps1'))) -Remove");
  assert.equal(dshRemoveCommand("unix"),
    "(gamma_setup=$(mktemp) && trap 'rm -f \"$gamma_setup\"' 0 && curl -fsSL 'https://github.com/tim4431/Gamma/releases/latest/download/install-gamma-dsh.sh' "
    + "-o \"$gamma_setup\" && sh \"$gamma_setup\" --remove)");
});
