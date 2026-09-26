import { Account, wanted } from "../harness.mjs";
import fs from "node:fs";
import path from "node:path";

export async function settingsScenarios(env) {
  const { server, browser, step, openPage, assert, assertEq, assertNoProblems, until, flags } = env;
  if (!wanted("settings")) return;
  server.manage("create-user", "settings-user", "settings-pw");
  const user = await new Account(server, "settings-user", "settings-pw").login();
  // A dummy connection supplies model choices. These tests never send AI jobs.
  await user.api("/api/ai/providers", { method: "POST", body: {
    protocol: "openai", name: "Test connection", api_key: "test-settings-only",
    base_url: server.base, models: "test-model-a, test-model-b",
  } });
  async function setup(viewport, prepare) {
    const ctx = await user.context(browser, viewport ? { viewport } : {});
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    await prepare?.(ctx);
    const page = await openPage(ctx, server.base);
    await page.waitForSelector(".folderNewBtn");
    return { ctx, page };
  }
  async function openSettings(page) {
    await page.getByRole("button", { name: "Account & settings", exact: true }).click();
    await page.getByRole("button", { name: "Settings…", exact: true }).click();
    await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
  }
  const nav = (page, name) => page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name, exact: true });
  const row = (page, name) => page.locator(`.settingsPane [data-setting="${name}"]`).first();
  async function search(page, query, label) {
    await page.getByRole("searchbox", { name: "Search settings" }).fill(query);
    await page.locator(".settingsSearchResult").filter({ has: page.getByText(label, { exact: true }) }).click();
    await row(page, label).waitFor({ state: "visible" });
  }

  await step("settings: a translation service is set up under Reading and picked as the translator", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Reading & editing").click();
      // Microsoft's free service needs no setup: only a Test button.
      const microsoft = row(page, "Microsoft (free)");
      assert((await microsoft.innerText()).includes("No key needed"));
      assertEq(await microsoft.getByRole("button").count(), 1);
      await page.route("**/api/translate/engines/microsoft/test", (route) => route.fulfill({ json: { ok: true, text: "TEST-OK" } }));
      await microsoft.getByRole("button", { name: "Test", exact: true }).click();
      await until(() => microsoft.innerText().then((text) => text.includes("TEST-OK")));
      const google = row(page, "Google Cloud Translation");
      assert((await google.innerText()).includes("Not set up"));
      await google.getByRole("button", { name: "Set up", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Google Cloud Translation", exact: true });
      await dialog.locator('input[autocomplete="new-password"]').fill("AIza-e2e-key-4321");
      await dialog.getByRole("button", { name: "Save", exact: true }).click();
      await until(() => dialog.count().then((n) => n === 0));
      await until(() => google.innerText().then((text) => text.includes("…4321")));
      if (flags.keep) {
        await row(page, "Translation services").scrollIntoViewIfNeeded();
        await page.screenshot({ path: `${server.dir}/settings-translation.png`, animations: "disabled" });
      }
      // The saved service joins the picker next to the chat models.
      await row(page, "Translate with").getByRole("button", { name: "Translate with", exact: true }).click();
      await page.locator(".uiSelectMenu").getByRole("button", { name: "Google Cloud Translation" }).click();
      await until(() => page.evaluate(() => localStorage.getItem("gamma-translate-model")).then((v) => v === "engine:google"));
      // Reasoning effort means nothing to a translation service; the speed
      // rows sit in the same Translation section.
      await row(page, "Parallel requests").waitFor();
      assertEq(await row(page, "Translation effort").count(), 0);
      await google.getByRole("button", { name: "Remove key", exact: true }).click();
      await until(() => google.innerText().then((text) => text.includes("Not set up")));
      assertEq((await user.api("/api/ai/models")).translate_engines.map((e) => e.id).join(), "engine:microsoft");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await user.api("/api/translate/engines/google", { method: "DELETE" });
    }
  });

  await step("settings: Ctrl+F goes to the settings search, Enter and the arrows pick a match", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      // Over the home library, whose own find box used to take the key.
      await page.keyboard.press("Control+f");
      const box = page.getByRole("searchbox", { name: "Search settings" });
      await until(() => box.evaluate((el) => el === document.activeElement));
      assertEq(await page.locator(".homeFindInput").evaluate((el) => el === document.activeElement), false);
      await page.keyboard.type("translation concurrency");
      await page.keyboard.press("Enter");
      await row(page, "Parallel requests").waitFor({ state: "visible" });
      // ↓ from the box walks the result buttons, ↑ from the first returns.
      await page.keyboard.press("Control+f");
      await page.keyboard.type("status bar");
      await page.keyboard.press("ArrowDown");
      assertEq(await page.evaluate(() => document.activeElement?.classList.contains("settingsSearchResult")), true);
      await page.keyboard.press("ArrowUp");
      await until(() => box.evaluate((el) => el === document.activeElement));
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("Enter");
      await row(page, "Status bar").waitFor({ state: "visible" });
      assertNoProblems(page);
    } finally {
      await ctx.close();
    }
  });

  await step("settings: Keyboard lists the shortcuts, rebinds one, flags a clash and resets", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Keyboard").click();
      const caps = async (r) => (await r.locator(".keyCap").allTextContents()).join(" ");
      const del = row(page, "Delete line");
      await del.waitFor();
      assertEq(await caps(del), "Ctrl Shift K", "the default chord");
      await del.getByRole("button", { name: "Change the shortcut for Delete line" }).click();
      await del.getByText("Press keys…").waitFor();
      await page.keyboard.press("Control+Alt+d");
      await until(async () => (await caps(del)) === "Ctrl Alt D", { what: "rebound" });
      await del.getByRole("button", { name: "Reset Delete line to its default shortcut" }).waitFor();
      await until(async () => (await user.api("/api/prefs/profile")).value?.keybindings?.["block.deleteLine"] === "Mod-Alt-d", { what: "the binding reaches the profile" });
      // Two commands on one chord are flagged on both rows.
      const ren = row(page, "Rename page");
      await ren.getByRole("button", { name: "Change the shortcut for Rename page" }).click();
      await page.keyboard.press("Control+Alt+d");
      await ren.getByText("Also used by Delete line").waitFor();
      await del.getByText("Also used by Rename page").waitFor();
      // A bare letter is refused.
      await ren.getByRole("button", { name: "Change the shortcut for Rename page" }).click();
      await page.keyboard.press("x");
      await ren.getByText("Add a modifier…").waitFor();
      await page.keyboard.press("Escape");
      await del.waitFor();
      await page.getByRole("button", { name: "Reset all", exact: true }).click();
      await until(async () => (await caps(del)) === "Ctrl Shift K", { what: "reset" });
      assertEq(await caps(ren), "F2", "the clash is gone with the reset");
      await page.getByRole("searchbox", { name: "Filter shortcuts" }).fill("duplicate");
      await until(async () => (await page.locator(".keyboardPane .setRow").count()) === 2, { what: "filtered to the two duplicate rows" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: model discovery updates automatically and long lists scroll", async () => {
    const { ctx, page } = await setup({ width: 800, height: 650 });
    try {
      const calls = [];
      let oldResponseSent = false;
      const models = Array.from({ length: 100 }, (_, i) => `gpt-test-${String(i).padStart(3, "0")}`);
      await page.route("**/api/ai/model-catalog", async (route) => {
        const body = route.request().postDataJSON();
        calls.push(body);
        if (body.api_key === "old-key") await new Promise((resolve) => setTimeout(resolve, 1500));
        await route.fulfill({ json: { models: body.api_key === "old-key" ? ["stale-model"] : models } });
        if (body.api_key === "old-key") oldResponseSent = true;
      });
      await openSettings(page);
      await nav(page, "Connections").click();
      await page.getByRole("button", { name: "+ Add provider", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Add key", exact: true });
      await dialog.getByRole("button", { name: "AI service", exact: true }).click();
      await page.getByText("OpenAI API", { exact: true }).click();
      const key = dialog.locator('input[autocomplete="new-password"]');
      await key.fill("old-key");
      await until(() => calls.length === 1);
      await key.fill("new-key");
      await until(() => calls.length === 2);
      await dialog.getByRole("button", { name: "100 usable" }).waitFor();
      const input = dialog.getByRole("combobox", { name: "Add a model" });
      await input.click();
      const list = page.getByRole("listbox", { name: "Available models" });
      assertEq(await list.getByRole("option").count(), 100);
      const bounds = await list.boundingBox();
      assert(bounds.y >= 0 && bounds.y + bounds.height <= 650, "model list fits the viewport");
      assert(await list.evaluate((el) => el.scrollHeight > el.clientHeight), "long list is scrollable");
      await list.hover();
      await page.mouse.wheel(0, 1600);
      await until(() => list.evaluate((el) => el.scrollTop > 0));
      await input.fill("099");
      await list.getByRole("option", { name: "gpt-test-099", exact: true }).click();
      await dialog.getByRole("button", { name: "Remove gpt-test-099", exact: true }).waitFor();
      await input.click();
      await input.press("ArrowUp");
      await input.press("Enter");
      await dialog.getByRole("button", { name: "Remove gpt-test-098", exact: true }).waitFor();
      await input.fill("my-custom-model");
      await input.press("Enter");
      await dialog.getByRole("button", { name: "Remove my-custom-model", exact: true }).waitFor();
      // Let the older response land; it must not replace the new catalog.
      await until(() => oldResponseSent);
      await until(() => page.getByRole("button", { name: "100 usable" }).isVisible());
      await input.click();
      await input.press("Escape");
      assertEq(await list.count(), 0);
      assert(await dialog.isVisible(), "Escape dismisses the list without closing the dialog");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: the DeepSeek preset is the OpenAI protocol at DeepSeek's endpoint", async () => {
    const { ctx, page } = await setup();
    try {
      const calls = [];
      await page.route("**/api/ai/model-catalog", async (route) => {
        calls.push(route.request().postDataJSON());
        await route.fulfill({ json: { models: ["deepseek-flash", "deepseek-v4-pro"] } });
      });
      await openSettings(page);
      await nav(page, "Connections").click();
      await page.getByRole("button", { name: "+ Add provider", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Add key", exact: true });
      await dialog.getByRole("button", { name: "AI service", exact: true }).click();
      await page.getByText("DeepSeek", { exact: true }).click();
      // A preset's endpoint is fixed: no Base URL field to fill.
      assertEq(await dialog.getByRole("textbox", { name: /Base URL/ }).count(), 0);
      await dialog.locator('input[autocomplete="new-password"]').fill("sk-deepseek-e2e");
      await dialog.getByRole("button", { name: "2 usable" }).waitFor();
      assertEq(calls.at(-1).protocol, "openai");
      assertEq(calls.at(-1).base_url, "https://api.deepseek.com");
      const input = dialog.getByRole("combobox", { name: "Add a model" });
      await input.click();
      await page.getByRole("listbox", { name: "Available models" })
        .getByRole("option", { name: "deepseek-flash", exact: true }).click();
      await dialog.getByRole("button", { name: "Add key", exact: true }).click();
      await until(() => dialog.count().then((n) => n === 0));
      const saved = page.locator(".aiProvRow").filter({ hasText: "sk-deepseek-e2e".slice(-4) });
      await saved.locator(".aiProvName").filter({ hasText: "DeepSeek" }).waitFor();
      assertNoProblems(page);
    } finally {
      await ctx.close();
      const info = await user.api("/api/ai/settings");
      for (const p of info.providers.filter((p) => p.base_url === "https://api.deepseek.com")) {
        await user.api(`/api/ai/providers/${p.id}`, { method: "DELETE" });
      }
    }
  });

  await step("settings: manual OAuth connection automatically fetches models", async () => {
    const { ctx, page } = await setup();
    try {
      await page.evaluate(() => {
        window.open = (url) => { window.testSignInUrl = url; return null; };
      });
      await page.route("**/api/ai/oauth/chatgpt/complete", async (route) => {
        const info = await user.api("/api/ai/settings");
        info.providers.push({ id: "oauth-test", protocol: "chatgpt", name: "Test sign-in", models: "gpt-test", oauth_connected: true });
        await route.fulfill({ json: info });
      });
      let catalogCalls = 0;
      await page.route("**/api/ai/model-catalog", async (route) => {
        catalogCalls++;
        await route.fulfill({ json: { models: ["gpt-test", "gpt-new-model"] } });
      });
      await openSettings(page);
      await nav(page, "Connections").click();
      await page.getByRole("button", { name: "+ Add provider", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Add key", exact: true });
      await dialog.getByRole("button", { name: "AI service", exact: true }).click();
      await page.getByRole("button", { name: "ChatGPT subscription", exact: true }).click();
      await dialog.getByRole("button", { name: "Open ChatGPT sign-in", exact: true }).click();
      await until(() => page.evaluate(() => !!window.testSignInUrl));
      const state = await page.evaluate(() => new URL(window.testSignInUrl).searchParams.get("state"));
      const callback = dialog.getByRole("textbox", { name: /Callback URL/ });
      assertEq(await callback.inputValue(), "", "callback remains a manual input");
      await callback.fill(`http://localhost:1455/auth/callback?code=test&state=${state}`);
      await dialog.getByRole("button", { name: "Connect", exact: true }).click();
      const edit = page.getByRole("dialog", { name: "Edit key", exact: true });
      await edit.getByRole("button", { name: "2 usable" }).waitFor();
      assertEq(catalogCalls, 1);
      await edit.getByRole("combobox", { name: "Add a model" }).click();
      await page.getByRole("option", { name: "gpt-new-model", exact: true }).waitFor();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: external assistant token creation, hiding, and revocation", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Integrations").click();
      const serverUrl = page.getByRole("textbox", { name: "Gamma MCP server URL" });
      await serverUrl.waitFor();
      assertEq(await serverUrl.inputValue(), `${server.base}/mcp`);
      assert(!await page.getByRole("textbox", { name: "Codex MCP configuration" }).isVisible(), "manual setup starts collapsed");
      assert(!await page.getByRole("textbox", { name: "Codex setup command", exact: true }).isVisible(), "only the selected method is shown");
      await page.getByRole("button", { name: "Copy server URL", exact: true }).click();
      await page.getByText("Copied. You can paste it now.", { exact: true }).waitFor();
      if (process.env.GAMMA_MCP_SCREENSHOTS) {
        fs.mkdirSync(process.env.GAMMA_MCP_SCREENSHOTS, { recursive: true });
        await page.screenshot({ path: path.join(process.env.GAMMA_MCP_SCREENSHOTS, "settings-desktop.png") });
        await page.setViewportSize({ width: 390, height: 844 });
        await page.screenshot({ path: path.join(process.env.GAMMA_MCP_SCREENSHOTS, "settings-mobile.png"), fullPage: true });
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "no horizontal overflow");
        await page.setViewportSize({ width: 1280, height: 860 });
      }
      await page.getByRole("button", { name: "Codex CLI", exact: true }).click();
      await page.getByRole("button", { name: "Windows PowerShell", exact: true }).click();
      const commandField = page.getByRole("textbox", { name: "Codex setup command", exact: true });
      const commands = await commandField.inputValue();
      assert(commands.includes("install-gamma-codex.ps1"));
      assert(commands.includes(`-ServerUrl '${server.base}/mcp'`));
      assert(!commands.includes("GAMMA_TOKEN"));
      await page.getByRole("button", { name: "macOS / Linux", exact: true }).click();
      assert((await commandField.inputValue()).includes("install-gamma-codex.sh"));
      assert((await commandField.inputValue()).endsWith(`'${server.base}/mcp')`), "Unix setup passes the server URL inside its cleanup subshell");
      await page.getByRole("button", { name: "Copy setup command", exact: true }).click();
      await page.getByText("Copied. You can paste it now.", { exact: true }).waitFor();
      await page.setViewportSize({ width: 390, height: 844 });
      await commandField.scrollIntoViewIfNeeded();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "setup command fits a narrow viewport");
      if (process.env.GAMMA_MCP_SCREENSHOTS) {
        await page.screenshot({ path: path.join(process.env.GAMMA_MCP_SCREENSHOTS, "codex-setup-mobile.png"), fullPage: true });
      }
      await page.setViewportSize({ width: 1280, height: 860 });
      await page.getByRole("button", { name: "Claude Code", exact: true }).click();
      assert(!await commandField.isVisible(), "Codex command is hidden in the Claude Code tab");
      const claudeCommand = page.getByRole("textbox", { name: "Claude Code connection command", exact: true });
      assertEq(await claudeCommand.inputValue(), `claude mcp add --transport http --scope user gamma '${server.base}/mcp'`);
      await page.getByRole("button", { name: "Windows PowerShell", exact: true }).click();
      assertEq(await claudeCommand.inputValue(), `claude mcp add --transport http --scope user gamma '${server.base}/mcp'`);
      await page.getByRole("button", { name: "Copy connection command", exact: true }).click();
      await page.getByText("Copied. You can paste it now.", { exact: true }).waitFor();
      await page.getByText(/Start Claude Code, run \/mcp/).waitFor();
      await page.getByText(/\/gamma:gamma starts the workflow/).waitFor();
      await page.getByText("Install the plugin (once)", { exact: true }).click();
      const pluginCommands = await page.getByRole("textbox", { name: "Claude Code plugin install commands", exact: true }).inputValue();
      assert(pluginCommands.includes("claude plugin marketplace add ./gamma-marketplace"));
      assert(pluginCommands.includes("claude plugin install gamma@gamma-local --scope user"));
      await page.getByText("Changed the server address?", { exact: true }).click();
      const reconnect = await page.getByRole("textbox", { name: "Claude Code change server commands", exact: true }).inputValue();
      assertEq(reconnect, `claude mcp remove gamma --scope user\nclaude mcp add --transport http --scope user gamma '${server.base}/mcp'`);
      if (process.env.GAMMA_MCP_SCREENSHOTS) {
        await page.screenshot({ path: path.join(process.env.GAMMA_MCP_SCREENSHOTS, "claude-setup-desktop.png"), fullPage: true });
      }
      await page.setViewportSize({ width: 390, height: 844 });
      await claudeCommand.scrollIntoViewIfNeeded();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Claude setup fits a narrow viewport");
      if (process.env.GAMMA_MCP_SCREENSHOTS) {
        await page.screenshot({ path: path.join(process.env.GAMMA_MCP_SCREENSHOTS, "claude-setup-mobile.png"), fullPage: true });
      }
      await page.setViewportSize({ width: 1280, height: 860 });
      await page.getByText("Manual setup (advanced)", { exact: true }).click();
      const config = page.getByRole("textbox", { name: "Codex MCP configuration" });
      await config.waitFor();
      assert((await config.inputValue()).includes(`${server.base}/mcp`));
      await page.getByRole("textbox", { name: "Connection name" }).fill("Codex test");
      await page.getByRole("button", { name: "Create token", exact: true }).click();
      const secret = page.getByRole("textbox", { name: "New integration token" });
      await secret.waitFor();
      assert((await secret.inputValue()).startsWith("gamma_"));
      const connections = await user.api("/api/integrations/tokens");
      assertEq(connections.tokens.length, 1);
      assert(!JSON.stringify(connections).includes(await secret.inputValue()), "token is never returned in listings");
      await page.getByRole("button", { name: "Done", exact: true }).click();
      await secret.waitFor({ state: "detached" });
      // Separate authorizations can have the same name. Revoking one must
      // leave the other visible, without claiming the assistant lost access.
      const duplicate = await user.api("/api/integrations/tokens", { method: "POST", body: { name: "Codex test" } });
      await page.getByRole("button", { name: "Refresh connections", exact: true }).click();
      await until(() => page.getByRole("button", { name: "Disconnect", exact: true }).count().then((n) => n === 2));
      let releaseStale, captured;
      const staleReady = new Promise((resolve) => { captured = resolve; });
      const staleGate = new Promise((resolve) => { releaseStale = resolve; });
      let holdNext = true;
      const routePattern = "**/api/integrations/tokens";
      await page.route(routePattern, async (route) => {
        if (!holdNext || route.request().method() !== "GET") return route.continue();
        holdNext = false;
        const response = await route.fetch();
        captured();
        await staleGate;
        await route.fulfill({ response });
      });
      await page.getByRole("button", { name: "Refresh connections", exact: true }).click();
      await staleReady;
      await page.getByRole("button", { name: "Disconnect", exact: true }).first().click();
      const revoked = page.getByText("Access revoked for the selected “Codex test” connection.", { exact: true });
      await revoked.waitFor();
      await until(() => page.getByRole("button", { name: "Disconnect", exact: true }).count().then((n) => n === 1));
      const staleResponse = page.waitForResponse((response) => new URL(response.url()).pathname.endsWith("/api/integrations/tokens") && response.request().method() === "GET");
      releaseStale();
      await (await staleResponse).finished();
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assertEq(await page.getByRole("button", { name: "Disconnect", exact: true }).count(), 1, "late refresh cannot restore a revoked connection");
      await page.unroute(routePattern);
      const remaining = (await user.api("/api/integrations/tokens")).tokens;
      assertEq(remaining.length, 1);
      assert(remaining[0].id !== duplicate.id, "only the selected connection was revoked");
      // Reconnecting in another tab clears the old notice on window focus.
      const reconnected = await user.api("/api/integrations/tokens", { method: "POST", body: { name: "Codex reconnected" } });
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await page.getByText("Codex reconnected", { exact: true }).waitFor();
      await revoked.waitFor({ state: "detached" });
      await user.api(`/api/integrations/tokens/${reconnected.id}`, { method: "DELETE" });
      await page.getByRole("button", { name: "Refresh connections", exact: true }).click();
      await until(() => page.getByRole("button", { name: "Disconnect", exact: true }).count().then((n) => n === 1));
      await page.getByRole("button", { name: "Disconnect", exact: true }).click();
      await page.getByText("No assistants have access to this workspace yet.", { exact: true }).waitFor();
      assertEq((await user.api("/api/integrations/tokens")).tokens.length, 0);
      const second = await user.api("/api/workspaces", { method: "POST", body: { name: "Other assistant workspace" } });
      const elsewhere = await user.api(`/api/integrations/tokens?ws=${second.id}`, { method: "POST", body: { name: "Codex elsewhere" } });
      const refreshed = page.waitForResponse((response) => new URL(response.url()).pathname.endsWith("/api/integrations/tokens") && response.request().method() === "GET");
      await page.getByRole("button", { name: "Refresh connections", exact: true }).click();
      assertEq((await (await refreshed).json()).tokens.length, 0);
      await revoked.waitFor({ state: "detached" });
      assertEq(await page.getByText("Codex elsewhere", { exact: true }).count(), 0, "other workspace connections stay out of this panel");
      assert(await page.getByText("No assistants have access to this workspace yet.", { exact: true }).isVisible());
      assertEq(await page.getByRole("button", { name: "Disconnect", exact: true }).count(), 0, "other workspace connections are not managed as current workspace access");
      await user.api(`/api/integrations/tokens/${elsewhere.id}?ws=${second.id}`, { method: "DELETE" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: DeepSeek Harness connects with a token made in its own tab", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Integrations").click();
      await page.getByRole("button", { name: "DeepSeek Harness", exact: true }).click();
      const start = page.getByRole("textbox", { name: "DeepSeek Harness start command", exact: true });
      await page.getByRole("button", { name: "macOS / Linux", exact: true }).click();
      assert((await start.inputValue()).startsWith(`export GAMMA_URL='${server.base}/mcp'
`));
      const install = page.getByRole("textbox", { name: "DeepSeek Harness plugin install command", exact: true });
      assert((await install.inputValue()).includes("releases/latest/download/dsh-gamma.tgz"));
      await page.getByRole("button", { name: "Create token", exact: true }).click();
      const secret = page.getByRole("textbox", { name: "New integration token" });
      await secret.waitFor();
      const token = await secret.inputValue();
      assert(!(await start.inputValue()).includes(token), "the token is typed at a prompt, never copied into a command");
      await until(() => page.getByRole("button", { name: "Disconnect", exact: true }).count().then((n) => n === 1));
      const [made] = (await user.api("/api/integrations/tokens")).tokens.filter((t) => t.name === "DeepSeek Harness");
      assertEq(made.scope, "read");
      // What dsh sends on startup: an initialize with the bearer header.
      const init = await fetch(`${server.base}/mcp`, { method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize",
          params: { protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "dsh-e2e", version: "0" } } }) });
      assertEq(init.status, 200);
      await page.getByText("Manual setup (advanced)", { exact: true }).click();
      assertEq(await secret.count(), 1, "the one-time token shows only in the tab that made it");
      await page.getByRole("button", { name: "Windows PowerShell", exact: true }).click();
      assert((await install.inputValue()).endsWith("npx @deepseek-ai/dsh plugin --profile web add $bundle"));
      assert((await start.inputValue()).includes("Read-Host 'Gamma token'"));
      await page.setViewportSize({ width: 390, height: 844 });
      await start.scrollIntoViewIfNeeded();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "four assistant tabs fit a narrow viewport");
      if (process.env.GAMMA_MCP_SCREENSHOTS) {
        await page.screenshot({ path: path.join(process.env.GAMMA_MCP_SCREENSHOTS, "dsh-setup-mobile.png"), fullPage: true });
      }
      await page.setViewportSize({ width: 1280, height: 860 });
      await page.getByRole("button", { name: "Done", exact: true }).click();
      await secret.waitFor({ state: "detached" });
      await user.api(`/api/integrations/tokens/${made.id}`, { method: "DELETE" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: section tags say where settings live and whether each section's settings are saved", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      const tag = (title) => page.locator(`.settingsPane .setSection[data-setting="${title}"] .setScope`);
      const sync = (title) => tag(title).getAttribute("data-sync");
      // a password account without Gamma Cloud: saved on this server, a check and one word
      await until(() => sync("Theme").then((v) => v === "saved"));
      assertEq(await tag("Theme").innerText(), "account");
      assertEq(await tag("Theme").locator("svg").count(), 1);
      assert((await tag("Theme").getAttribute("title")).startsWith("Saved on this server."));
      assert((await tag("Theme").getAttribute("aria-label")).startsWith("Account setting. Saved on this server."));
      // a device section stays with the browser: a monitor and one word
      assertEq(await sync("Interface"), "browser");
      assertEq(await tag("Interface").innerText(), "browser");
      assertEq(await tag("Interface").locator("svg").count(), 1);
      // one change spins only the section holding it
      await nav(page, "Reading & editing").click();
      await until(() => sync("Notes").then((v) => v === "saved"));
      await row(page, "Enter key").getByRole("button", { name: "New note", exact: true }).click();
      await until(() => sync("Notes").then((v) => v === "syncing"));
      assertEq(await sync("Search opens as"), "saved");
      assertEq(await sync("PDFs"), "saved");
      await until(() => sync("Notes").then((v) => v === "saved"));
      assertEq((await user.api("/api/prefs/profile")).value?.enterNewNote, true);
      await nav(page, "Appearance").click();
      assertEq(await sync("Theme"), "saved");
      await nav(page, "Reading & editing").click();
      await row(page, "Enter key").getByRole("button", { name: "New line", exact: true }).click();
      await until(async () => (await user.api("/api/prefs/profile")).value?.enterNewNote === false);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: navigation, search, scoped management, and preferences survive reload", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      assertEq(await nav(page, "Appearance").getAttribute("aria-current"), "page");
      for (const [label, theme, scheme] of [["Gamma Light", "gamma-light", "light"], ["Gamma Dark", "gamma-dark", "dark"]]) {
        await page.getByRole("button", { name: label, exact: true }).click();
        await until(() => page.locator("html").getAttribute("data-theme").then((v) => v === theme));
        assertEq(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme), scheme);
        await until(async () => (await user.api("/api/prefs/profile")).value?.theme === theme);
        await page.reload();
        await page.waitForSelector(".folderNewBtn");
        assertEq(await page.locator("html").getAttribute("data-theme"), theme);
        await openSettings(page);
        assertEq(await page.getByRole("button", { name: label, exact: true }).getAttribute("aria-pressed"), "true");
        if (flags.keep) await page.screenshot({ path: `${server.dir}/settings-${theme}.png`, animations: "disabled" });
      }
      await page.getByRole("button", { name: "Sepia", exact: true }).click();
      await until(() => page.locator("html").getAttribute("data-theme").then((v) => v === "sepia"));
      assertEq(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--text-primary").trim()), "#073642");
      await page.getByRole("button", { name: "Solarized Light", exact: true }).click();
      await until(() => page.locator("html").getAttribute("data-theme").then((v) => v === "solarized"));
      assertEq(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--text-primary").trim()), "#657b83");
      await until(async () => (await user.api("/api/prefs/profile")).value?.theme === "solarized");
      await page.reload();
      await page.waitForSelector(".folderNewBtn");
      await until(() => page.locator("html").getAttribute("data-theme").then((v) => v === "solarized"));
      await openSettings(page);
      const themes = page.getByRole("group", { name: "Theme", exact: true });
      assertEq(await themes.getByRole("button").count(), 8);
      assertEq(await themes.locator('[aria-pressed="true"]').count(), 1);
      await page.getByRole("checkbox", { name: "Dark PDF pages", exact: true }).check();
      await until(() => user.api("/api/prefs/profile").then((v) => v.value?.pdfDarkPage === true));
      assert(await page.locator(".appearancePdfPreview.isDark").isVisible());
      await page.getByRole("checkbox", { name: "Dark PDF pages", exact: true }).uncheck();
      await until(() => user.api("/api/prefs/profile").then((v) => v.value?.pdfDarkPage === false));
      await row(page, "Interface size").getByRole("button", { name: "Larger", exact: true }).click();
      assert((await row(page, "Interface size").innerText()).includes("110%"));
      await row(page, "Interface size").getByRole("button", { name: "Reset", exact: true }).click();
      if (flags.keep) await page.screenshot({ path: `${server.dir}/settings-appearance.png`, animations: "disabled" });
      await nav(page, "Diagnostics").click();
      assertEq(await nav(page, "Back to settings").count(), 0, "one sidebar: no second-level navigation");
      await nav(page, "Appearance").click();
      if (flags.keep) await page.screenshot({ path: `${server.dir}/settings-library.png`, animations: "disabled" });
      await page.getByRole("checkbox", { name: "Labels", exact: true }).uncheck();
      await page.getByRole("checkbox", { name: "Thumbnails", exact: true }).uncheck();
      assertEq(await page.locator(".libraryDisplayCard img").count(), 0);
      assertEq(await page.locator(".libraryDisplayCard .labelTagBadge").count(), 0);
      assertEq(await page.locator(".libraryDisplayCard .folderTagBadge").count(), 1);
      await search(page, "translation concurrency", "Parallel requests");
      await row(page, "Parallel requests").locator("input").fill("7");
      await row(page, "Parallel requests").locator("input").press("Tab");
      await until(() => page.evaluate(() => localStorage.getItem("gamma-translate-parallel")).then((v) => v === "7"));
      // An account preference reaches the account's profile; a device one never does.
      await until(() => user.api("/api/prefs/profile").then((v) => v.value?.translateParallel === 7));
      assert(!("uiScale" in (await user.api("/api/prefs/profile")).value), "interface size stays with the browser");
      // A fresh browser signed in to the same account picks the profile up.
      const other = await user.context(browser);
      try {
        const fresh = await openPage(other, server.base);
        await until(() => fresh.evaluate(() => localStorage.getItem("gamma-translate-parallel")).then((v) => v === "7"));
        assertEq(await fresh.evaluate(() => localStorage.getItem("gamma-theme")), "solarized");
        assertNoProblems(fresh);
      } finally { await other.close(); }
      await nav(page, "Workspaces").click();
      await page.getByRole("button", { name: "Manage", exact: true }).first().click();
      await page.getByRole("button", { name: "Back to workspaces", exact: true }).waitFor();
      assertEq(await page.getByRole("dialog").count(), 1, "workspace details stay in the manager instead of stacking a dialog");
      await page.getByRole("button", { name: "Rename", exact: true }).click();
      const rename = page.getByRole("dialog", { name: "Rename workspace", exact: true });
      await rename.getByRole("textbox").fill("Unsaved workspace name");
      await rename.press("Escape");
      await rename.getByRole("button", { name: "Discard changes", exact: true }).click();
      await page.getByRole("button", { name: "Back to workspaces", exact: true }).click();
      await nav(page, "Backups").click();
      await row(page, "Backups").waitFor();
      assertEq(await nav(page, "Server").count(), 0, "non-admin has no server navigation");
      assertEq(await nav(page, "Users").count(), 0, "non-admin has no users navigation");
      await page.getByRole("searchbox", { name: "Search settings" }).fill("administration");
      assertEq(await page.locator(".settingsSearchResult").count(), 0);
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await page.reload();
      await page.getByRole("button", { name: "Account & settings", exact: true }).waitFor();
      await openSettings(page);
      assertEq(await page.getByRole("button", { name: "Solarized Light", exact: true }).getAttribute("aria-pressed"), "true");
      await nav(page, "Appearance").click();
      assertEq(await page.getByRole("checkbox", { name: "Folders", exact: true }).isChecked(), true);
      assertEq(await page.getByRole("checkbox", { name: "Labels", exact: true }).isChecked(), false);
      assertEq(await page.getByRole("checkbox", { name: "Thumbnails", exact: true }).isChecked(), false);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: a backup task is created, run and deleted from the Backups table", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Backups").click();
      await page.getByRole("button", { name: "Add task", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Add backup task", exact: true });
      await dialog.getByPlaceholder("e.g. Nightly research backup").fill("Nightly");
      await dialog.getByRole("button", { name: "Frequency", exact: true }).click();
      await page.getByRole("button", { name: "Daily", exact: true }).click();
      await dialog.locator("ol li").first().waitFor(); // the schedule preview answered
      await dialog.getByRole("button", { name: "Create task", exact: true }).click();
      await dialog.waitFor({ state: "detached" });
      const rowOf = page.getByRole("region", { name: "Periodic backup tasks" }).locator("tr", { hasText: "Nightly" });
      await rowOf.waitFor();
      assert((await rowOf.innerText()).includes("Daily · 03:00"), "the row shows the daily schedule");
      await rowOf.getByRole("button", { name: "Actions for Nightly", exact: true }).click();
      await page.getByRole("button", { name: "Run now", exact: true }).click();
      // Run now wakes the scheduler at once; the table polls every 5 s
      await until(() => rowOf.innerText().then((t) => /finished|failed/i.test(t)), { timeout: 20000, what: "the queued run to finish" });
      assert(/finished/i.test(await rowOf.innerText()), "the run finished");
      await rowOf.getByRole("button", { name: "Actions for Nightly", exact: true }).click();
      await page.getByRole("button", { name: "Delete task", exact: true }).click();
      await page.locator(".confirmHead").waitFor();
      await page.locator(".reportModalBtns button", { hasText: "Delete task" }).click();
      await rowOf.waitFor({ state: "detached" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: prompt and connection drafts have save, cancel, and dismissal protection", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await search(page, "custom prompts", "Custom prompts");
      await page.getByRole("button", { name: /Chat system prompt/ }).click();
      const input = page.locator(".promptTextarea").first();
      const original = await input.inputValue();
      await input.fill("Temporary unsaved prompt");
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await page.getByRole("alertdialog", { name: "Unsaved changes" }).waitFor();
      await page.getByRole("button", { name: "Keep editing", exact: true }).click();
      assertEq(await input.inputValue(), "Temporary unsaved prompt");
      await page.getByRole("button", { name: "Cancel", exact: true }).click();
      assertEq(await input.inputValue(), original);
      await input.fill("Saved test prompt");
      await page.getByRole("button", { name: "Save", exact: true }).click();
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await openSettings(page);
      await search(page, "custom prompts", "Custom prompts");
      await page.getByRole("button", { name: /Chat system prompt/ }).click();
      assertEq(await page.locator(".promptTextarea").first().inputValue(), "Saved test prompt");
      await nav(page, "Connections").click();
      await page.getByRole("button", { name: "+ Add provider", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Add key", exact: true });
      await dialog.getByRole("button", { name: "AI service", exact: true }).click();
      await page.getByText("Custom endpoint", { exact: true }).click();
      await dialog.getByRole("button", { name: "API protocol", exact: true }).waitFor();
      await dialog.getByRole("textbox", { name: /Base URL/ }).fill("https://example.invalid");
      await dialog.getByRole("button", { name: "AI service", exact: true }).click();
      await page.getByText("OpenAI API", { exact: true }).click();
      assertEq(await dialog.getByRole("textbox", { name: /Base URL/ }).count(), 0);
      await dialog.getByRole("textbox", { name: /Name/ }).fill("Unsaved connection");
      await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
      await dialog.getByRole("button", { name: "Keep editing", exact: true }).click();
      await dialog.press("Escape");
      await dialog.getByRole("button", { name: "Discard changes", exact: true }).click();
      assertEq(await dialog.count(), 0);
      assertEq(await page.getByRole("dialog", { name: "Settings", exact: true }).count(), 1);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: token usage section lists the account's AI calls", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await search(page, "token", "Token usage");
      const section = page.locator(".settingsPane");
      await row(page, "All time").waitFor();
      assert((await row(page, "All time").innerText()).includes("No AI calls recorded yet"));
      assertEq(await row(page, "All time").getByRole("button", { name: "Reset" }).isDisabled(), true);
      const usage = await user.api("/api/ai/usage");
      assertEq(usage.windows.all.calls, 0);
      assertEq((await user.api("/api/ai/usage", { method: "DELETE" })).deleted, 0);
      assert((await section.innerText()).includes("no calls"), "the window tiles say no calls");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: chat shortcuts update global model, context, and tool preferences", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Connections").click();
      await row(page, "Default chat model").waitFor();
      await row(page, "Default chat model").getByRole("button").first().click();
      await page.getByText("test-model-b", { exact: true }).last().click();
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await page.locator('[title^="Chat settings"]').click();
      const popover = page.locator(".chatSettingsPop");
      assert((await popover.innerText()).includes("test-model-b"));
      await popover.getByRole("button", { name: "Switch model" }).click();
      await page.getByText("test-model-a", { exact: true }).last().click();
      await popover.locator('input[type="number"]').fill("42000");
      await popover.locator('input[type="number"]').press("Tab");
      await popover.getByRole("checkbox", { name: "Allow tools in all chats" }).uncheck();
      await page.locator('[title^="Chat settings"]').click();
      await openSettings(page);
      await nav(page, "Connections").click();
      assert((await row(page, "Default chat model").innerText()).includes("test-model-a"));
      await nav(page, "Chat").click();
      assertEq(await page.getByRole("checkbox", { name: "Assistant tools" }).isChecked(), false);
      await nav(page, "Advanced").click();
      assertEq(await row(page, "Single paper").locator('input[type="number"]').inputValue(), "42000");
      await nav(page, "Chat").click();
      await page.getByRole("checkbox", { name: "Assistant tools" }).check();
      // the per-chat chips: turning Rename off for folder chats
      await row(page, "Folder chat").getByRole("button", { name: /^Rename/ }).click();
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await page.locator('[title^="Chat settings"]').click();
      assertEq(await popover.getByRole("checkbox", { name: "Allow tools in all chats" }).isChecked(), true);
      assertEq(await popover.getByRole("button", { name: "Rename", exact: true }).getAttribute("aria-pressed"), "false");
      assertEq(await popover.getByRole("button", { name: "Read", exact: true }).getAttribute("aria-pressed"), "true");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: the chat header's context ring shows the last reply's size", async () => {
    // An agent reply: usage sums its rounds, context_tokens is the last round alone.
    await user.api("/api/chats/home", { method: "PUT", body: { messages: [
      { role: "user", text: "hi" },
      { role: "ai", text: "hello", actions: [{ kind: "read" }], context_tokens: 64000,
        usage: { input: 90000, output: 500, cache_read: 0, cache_write: 0 } },
    ] } });
    // The window lookup (provider listing, then models.dev) is pinned by the
    // backend tests; here the server's answer is stubbed to stay offline.
    const { ctx, page } = await setup(undefined, (c) => c.route("**/api/ai/context-window?**", (route) => route.fulfill({
      json: { model: "test-model-a", context_window: 128000, source: "provider" } })));
    try {
      const ring = page.getByRole("button", { name: "Context: 64k of 128k tokens (50%)", exact: true });
      await ring.waitFor();
      await ring.click();
      assert((await page.locator(".chatSettingsPop").innerText()).includes("Context: 64k of 128k tokens (50%)"));
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await user.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
    }
  });

  await step("settings: mobile uses labeled navigation and fits a narrow viewport", async () => {
    const { ctx, page } = await setup({ width: 390, height: 844 });
    try {
      await openSettings(page);
      await page.getByRole("button", { name: "Gray", exact: true }).click();
      assertEq(await page.getByRole("button", { name: "Gray", exact: true }).getAttribute("aria-pressed"), "true");
      assert(!(await page.locator(".settingsPane").evaluate((el) => el.scrollWidth > el.clientWidth + 1)), "appearance fits the phone without horizontal scrolling");
      if (flags.keep) await page.screenshot({ path: `${server.dir}/settings-appearance-mobile.png`, animations: "disabled" });
      await page.setViewportSize({ width: 320, height: 844 });
      for (let i = 0; i < 6; i++) await row(page, "Interface size").getByRole("button", { name: "Larger", exact: true }).click();
      assert(!(await page.locator(".settingsPane").evaluate((el) => el.scrollWidth > el.clientWidth + 1)), "appearance fits a small phone at maximum interface size");
      await row(page, "Interface size").getByRole("button", { name: "Reset", exact: true }).click();
      await page.setViewportSize({ width: 390, height: 844 });
      await page.getByRole("button", { name: "Back", exact: true }).click();
      await nav(page, "Reading & editing").click();
      assertEq(await page.getByRole("checkbox", { name: "Snap vertical scrolling", exact: true }).count(), 0);
      assertEq(await page.getByRole("checkbox", { name: "Note badges on highlights", exact: true }).count(), 0);
      await row(page, "Enter key").waitFor();
      await row(page, "Enter key").getByRole("button", { name: "New note", exact: true }).click();
      assert((await row(page, "Enter key").innerText()).includes("Shift+Enter inserts a new line"));
      await page.getByRole("button", { name: "Back", exact: true }).click();
      await nav(page, "Appearance").click();
      for (const [folders, labels, mode] of [[false, false, "off"], [false, true, "labels"], [true, false, "folders"], [true, true, "both"]]) {
        await page.getByRole("checkbox", { name: "Folders", exact: true }).setChecked(folders);
        await page.getByRole("checkbox", { name: "Labels", exact: true }).setChecked(labels);
        assertEq(await page.locator(".libraryDisplayCard .folderTagBadge").count(), Number(folders));
        assertEq(await page.locator(".libraryDisplayCard .labelTagBadge").count(), Number(labels));
        assertEq(await page.evaluate(() => localStorage.getItem("gamma-home-file-labels")), mode);
      }
      await page.getByRole("checkbox", { name: "Thumbnails", exact: true }).check();
      assertEq(await page.locator(".libraryDisplayCard img").count(), 1);
      assert(!(await page.locator(".settingsPane").evaluate((el) => el.scrollWidth > el.clientWidth + 1)), "library display fits the phone");
      if (flags.keep) await page.screenshot({ path: `${server.dir}/settings-library-mobile.png`, animations: "disabled" });
      await search(page, "translation model", "Translate with");
      assert(await row(page, "Translate with").isVisible());
      const overflow = await page.locator(".settingsPane").evaluate((el) => el.scrollWidth > el.clientWidth + 1);
      assert(!overflow, "settings content fits the phone without horizontal scrolling");
      if (flags.keep) await page.screenshot({ path: `${server.dir}/settings-mobile.png`, animations: "disabled" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: Report a problem gathers diagnostics and opens the prefilled GitHub bug form", async () => {
    const { ctx, page } = await setup();
    try {
      // window.open would leave the test; record the URL instead.
      await page.evaluate(() => { window.__opened = []; window.open = (u) => { window.__opened.push(u); return {}; }; });
      await page.getByRole("button", { name: "Account & settings", exact: true }).click();
      await page.getByRole("button", { name: "Report a problem…", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Report a problem", exact: true });
      await dialog.waitFor();
      const open = dialog.getByRole("button", { name: "Open GitHub issue", exact: true });
      assert(await open.isDisabled(), "nothing to report yet");
      await dialog.getByLabel("What happened").fill("A blue line stays on the notes\nafter a drag");
      await dialog.getByLabel("How to reproduce").fill("drag a block, drop it outside");
      await dialog.getByText("Preview the report", { exact: true }).click();
      const preview = await dialog.locator("pre").textContent();
      assert(/\*\*Build:\*\* Gamma .+ · (server|checkout|desktop app)/.test(preview), `build line in ${preview}`);
      assert(/\*\*Browser:\*\* .+ on .+ · \d+×\d+/.test(preview), "browser line");
      assert(/\*\*View:\*\* home.* · workspace: personal, owner$/m.test(preview), `view line in ${preview}`);
      assert(!preview.includes("Server (seen as admin)"), "a member sees no server section");
      // The toggle folds the diagnostics away — and the preview with them.
      await dialog.getByRole("checkbox", { name: "Include diagnostics" }).uncheck();
      assertEq(await dialog.locator("pre").count(), 0, "no preview without diagnostics");
      await dialog.getByRole("checkbox", { name: "Include diagnostics" }).check();
      // A screen recording: the browser's picker is stubbed with a canvas
      // stream (a real MediaStream, so MediaRecorder runs for real). The
      // dialog folds into the pill meanwhile and comes back with the file.
      await page.evaluate(() => {
        navigator.mediaDevices.getDisplayMedia = async () => {
          const c = document.createElement("canvas");
          c.width = 64; c.height = 64;
          const g = c.getContext("2d");
          setInterval(() => { g.fillStyle = `hsl(${Date.now() % 360} 80% 50%)`; g.fillRect(0, 0, 64, 64); }, 40);
          return c.captureStream(10);
        };
      });
      await dialog.getByRole("button", { name: "Record…", exact: true }).click();
      await dialog.waitFor({ state: "detached" });
      const pill = page.locator(".reportRecordPill");
      await pill.waitFor();
      assert(/Recording \d:\d\d/.test(await pill.textContent()), "the pill shows a clock");
      await page.waitForTimeout(1500);
      await pill.getByRole("button", { name: "Stop", exact: true }).click();
      await dialog.waitFor();
      const recRow = dialog.locator('[data-setting="Screen recording"]');
      await until(async () => /gamma-recording-\d{8}-\d{4}\.(webm|mp4) · 0:0\d · \d+(\.\d+)? [KM]B/.test(await recRow.textContent()), { what: "the recording's row" });
      assertEq(await dialog.getByLabel("What happened").inputValue(), "A blue line stays on the notes\nafter a drag", "the draft survives the recording");
      const download = page.waitForEvent("download");
      await recRow.getByRole("button", { name: "Save", exact: true }).click();
      const file = await download;
      assert(/^gamma-recording-\d{8}-\d{4}\.(webm|mp4)$/.test(file.suggestedFilename()), `saved as ${file.suggestedFilename()}`);
      await until(async () => (await recRow.textContent()).includes("· saved"), { what: "the saved tag" });
      await open.click();
      await dialog.waitFor({ state: "detached" });
      const url = new URL(await until(() => page.evaluate(() => window.__opened[0]), { what: "the GitHub tab" }));
      assertEq(`${url.origin}${url.pathname}`, "https://github.com/tim4431/gamma/issues/new");
      assertEq(url.searchParams.get("template"), "bug_report.yml");
      assertEq(url.searchParams.get("title"), "A blue line stays on the notes");
      assertEq(url.searchParams.get("description"), "A blue line stays on the notes\nafter a drag");
      assert(/^drag a block, drop it outside\n\nScreen recording: `gamma-recording-\d{8}-\d{4}\.(webm|mp4)` \(dropped into this issue by the reporter\)\.$/.test(url.searchParams.get("steps")), `steps name the recording: ${url.searchParams.get("steps")}`);
      assert(url.searchParams.get("diagnostics").includes("**Build:**"), "diagnostics ride along");
      // The Diagnostics pane's Help row opens the same dialog; an admin's
      // report adds the server dashboard and log.
      server.manage("set-admin", "settings-user", "on");
      await page.reload();
      await page.waitForSelector(".folderNewBtn");
      await openSettings(page);
      await nav(page, "Diagnostics").click();
      await row(page, "Report a problem").getByRole("button", { name: "Report…", exact: true }).click();
      await dialog.waitFor();
      await dialog.getByText("Preview the report", { exact: true }).click();
      await until(async () => (await dialog.locator("pre").textContent()).includes("**Server (seen as admin):** Gamma"), { what: "the admin's server section" });
      await dialog.getByRole("button", { name: "Close Report a problem", exact: true }).click();
      await dialog.waitFor({ state: "detached" });
      assertNoProblems(page);
    } finally {
      server.manage("set-admin", "settings-user", "off");
      await ctx.close();
    }
  });

  await step("settings: administrators see their own account separately from all users", async () => {
    server.manage("set-admin", "settings-user", "on");
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Account & sync").click();
      await page.locator(".settingsPane .aiProvRow").waitFor();
      assertEq(await page.locator(".settingsPane .aiProvRow").count(), 1);
      await nav(page, "Server").click();
      const limit = row(page, "Default max upload").locator("input");
      await limit.waitFor();
      const originalLimit = await limit.inputValue();
      // limits save on commit, like every other setting
      await limit.fill("77");
      await limit.press("Enter");
      await until(() => user.api("/api/admin/settings").then((s) => s.max_upload_mb === 77), { what: "limit saved on Enter" });
      await limit.fill(originalLimit);
      await limit.press("Enter");
      await until(() => user.api("/api/admin/settings").then((s) => String(s.max_upload_mb) === originalLimit), { what: "limit restored" });
      await limit.fill("77");
      await limit.press("Enter");
      await row(page, "Default quota").locator("input").fill("1200");
      await row(page, "Default quota").locator("input").press("Enter");
      await until(() => user.api("/api/admin/settings").then((v) => v.max_upload_mb === 77 && v.quota_mb === 1200));
      await nav(page, "Users").click();
      await until(() => page.locator(".settingsPane .aiProvRow").count().then((n) => n > 1));
      // Each account row nests its personal workspaces; Manage opens the
      // workspace dialog in admin mode. The Server pane lists shared ones only.
      await until(() => page.locator(".settingsPane .aiProvSubRow").count().then((n) => n > 1));
      await page.locator(".settingsPane .aiProvSubRow").first().getByRole("button", { name: "Manage" }).click();
      await page.locator(".subDialog").getByRole("button", { name: "Rename", exact: true }).waitFor();
      await page.keyboard.press("Escape");
      await page.locator(".subDialog").waitFor({ state: "detached" });
      await nav(page, "Server").click();
      // the dashboard: three tiles and the update row (the release check is
      // disabled for the isolated backend, so the row says so)
      await until(() => page.locator(".settingsPane .setStatText").count().then((n) => n === 3));
      await page.getByText("could not check", { exact: false }).waitFor();
      await page.locator(".settingsPane .segGroup button", { hasText: "Warnings" }).click();
      await page.getByText("Shared workspaces", { exact: true }).waitFor();
      assertEq(await page.getByText("Personal workspaces", { exact: true }).count(), 0);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: Server → Guests saves how long guest workspaces last and demo mode", async () => {
    server.manage("set-admin", "settings-user", "on");
    const before = await user.api("/api/admin/settings");
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Server").click();
      const ttl = row(page, "Guest workspaces last").locator("input");
      await ttl.waitFor();
      assertEq(await ttl.inputValue(), String(before.guest_ttl_hours));
      assert(!await ttl.isDisabled(), "a saved (not environment) value is editable");
      await ttl.fill("36");
      await ttl.press("Enter");
      await until(() => user.api("/api/admin/settings").then((v) => v.guest_ttl_hours === 36 && v.guest_ttl_source === "saved"),
        { what: "guest lifetime saved on Enter" });
      const demo = row(page, "Demo mode").locator("input");
      assertEq(await demo.isChecked(), false, "demo mode is off by default");
      await page.getByRole("checkbox", { name: "Demo mode", exact: true }).check();
      await until(() => user.api("/api/admin/settings").then((v) => v.demo_mode === true), { what: "demo mode saved" });
      assertEq((await (await fetch(`${server.base}/api/server-config`)).json()).demo, true, "the login page learns of it");
      // Another pane and back: the pane reads the stored values again.
      await nav(page, "Users").click();
      await nav(page, "Server").click();
      await until(async () => (await row(page, "Guest workspaces last").locator("input").inputValue()) === "36");
      assert(await row(page, "Demo mode").locator("input").isChecked(), "demo mode reads back on");
      await page.getByRole("checkbox", { name: "Demo mode", exact: true }).uncheck();
      await until(() => user.api("/api/admin/settings").then((v) => v.demo_mode === false), { what: "demo mode off again" });
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await user.api("/api/admin/settings", { method: "PUT", body: { guest_ttl_hours: before.guest_ttl_hours, demo_mode: false } });
    }
  });

  await step("settings: a shared AI provider from Server is a read-only connection for every account", async () => {
    // settings-user is an admin since the step above.
    server.manage("create-user", "settings-member", "settings-member-pw");
    const member = await new Account(server, "settings-member", "settings-member-pw").login();
    const { ctx, page } = await setup();
    try {
      await page.route("**/api/ai/model-catalog", (route) => route.fulfill({ json: { models: ["lab-model", "lab-big"] } }));
      await openSettings(page);
      await nav(page, "Server").click();
      await row(page, "Shared AI provider").getByRole("button", { name: "+ Add provider", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Add shared key", exact: true });
      // API keys only: the ChatGPT sign-in is not offered for a shared entry.
      await dialog.getByRole("button", { name: "AI service", exact: true }).click();
      assertEq(await page.getByText("ChatGPT subscription", { exact: true }).count(), 0);
      await page.locator(".uiSelectMenu").getByRole("button", { name: "OpenAI API", exact: true }).click();
      await dialog.locator('input[autocomplete="new-password"]').fill("sk-shared-e2e-key-7777");
      await dialog.getByRole("button", { name: "2 usable" }).waitFor();
      await dialog.getByRole("combobox", { name: "Add a model" }).click();
      await page.getByRole("listbox", { name: "Available models" })
        .getByRole("option", { name: "lab-model", exact: true }).click();
      await dialog.getByRole("button", { name: "Add key", exact: true }).click();
      await until(() => dialog.count().then((n) => n === 0));
      await page.locator(".settingsPane .aiProvRow").filter({ hasText: "…7777" }).waitFor();
      assert(!await row(page, "Guests may use it").locator("input").isChecked(), "guests are off by default");
      // The shared allowance: tokens per account / guest per day, 0 = unlimited.
      const perAccount = row(page, "Allowance per account").locator("input");
      assertEq(await perAccount.inputValue(), "0", "unlimited by default");
      await perAccount.fill("50000");
      await perAccount.press("Enter");
      await row(page, "Allowance per guest").locator("input").fill("2000");
      await row(page, "Allowance per guest").locator("input").press("Enter");
      await until(() => user.api("/api/admin/ai-providers").then((v) => v.allowance?.accounts === 50000 && v.allowance?.guests === 2000),
        { what: "the allowance saved" });
      const metered = (await member.api("/api/ai/usage")).allowance;
      assertEq(JSON.stringify(metered), JSON.stringify({ limit: 50000, used: 0, exhausted: false }), "the member's allowance");

      // Connections: a read-only row with the tag, and its model in the pickers.
      await nav(page, "Connections").click();
      const shared = page.locator(".settingsPane .aiProvRow").filter({ hasText: "Shared by this server" });
      await shared.waitFor();
      assertEq(await shared.getByRole("button").count(), 0, "no edit or delete on a shared row");
      await shared.getByRole("radio").check();
      await row(page, "Default chat model").getByRole("button").click();
      await page.locator(".uiSelectMenu").getByRole("button", { name: "lab-model", exact: true }).click();
      // Token usage says what is left of the allowance.
      await row(page, "Shared allowance").getByText("0 of 50k tokens in the last 24 h", { exact: true }).waitFor();

      // Another account gets the models, never the key hint.
      const models = await member.api("/api/ai/models");
      assert(models.models.some((m) => m.model === "lab-model" && m.shared), "member sees the shared model");
      const mine = (await member.api("/api/ai/settings")).providers.find((p) => p.shared);
      assertEq(mine.key_hint, "");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      for (const p of (await user.api("/api/admin/ai-providers")).providers) {
        await user.api(`/api/admin/ai-providers/${encodeURIComponent(p.id)}`, { method: "DELETE" });
      }
      await user.api("/api/admin/ai-providers", { method: "PUT", body: { allowance: { accounts: 0, guests: 0 } } });
    }
  });

  // The red dot (app/notices.js): the feed is faked so no real error or
  // release is needed; the acks go to the real server.
  await step("settings: a notice dots the account button and Settings… lands on its pane", async () => {
    server.manage("set-admin", "settings-user", "on");
    const ctx = await user.context(browser);
    try {
      await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
      const seen = [];
      let notices = [
        { id: "update", fingerprint: "9.9.9", tone: "warn", pane: "server", title: "Gamma v9.9.9 is available" },
        { id: "backup-failed", fingerprint: "t1", tone: "error", pane: "backups", title: "A backup task failed" },
      ];
      await ctx.route("**/api/notices", (route) => route.fulfill({ json: { notices } }));
      await ctx.route("**/api/notices/*/seen", async (route) => {
        const id = route.request().url().match(/notices\/([^/]+)\/seen/)[1];
        seen.push(`${id}:${route.request().postDataJSON().fingerprint}`);
        notices = notices.filter((n) => n.id !== id);
        await route.continue();
      });
      const page = await openPage(ctx, server.base);
      await page.waitForSelector(".folderNewBtn");
      const account = page.getByRole("button", { name: "Account & settings", exact: true });
      await account.locator(".noticeDot").waitFor();
      assertEq(await account.locator(".noticeDot").getAttribute("data-tone"), "error", "the strongest notice colours the dot");
      await account.click();
      const item = page.getByRole("button", { name: "Settings…", exact: true });
      await item.locator(".noticeDot").waitFor();
      await item.click();
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
      // Lands on the strongest notice's pane; the visit resolves it and the
      // other pane keeps its dot.
      assertEq(await nav(page, "Backups").getAttribute("aria-current"), "page", "Settings… opens the dotted pane");
      await until(() => seen.length === 1, { what: "the ack of the visited pane" });
      assertEq(seen[0], "backup-failed:t1");
      await nav(page, "Server").locator(".noticeDot").waitFor();
      assertEq(await nav(page, "Backups").locator(".noticeDot").count(), 0);
      assertEq(await account.locator(".noticeDot").getAttribute("data-tone"), "warn");
      await nav(page, "Server").click();
      await until(() => seen.length === 2, { what: "the second ack" });
      assertEq(seen[1], "update:9.9.9");
      await until(() => nav(page, "Server").locator(".noticeDot").count().then((n) => n === 0));
      assertEq(await account.locator(".noticeDot").count(), 0, "nothing left to see");
      // The ack is stored with the account (posted after the dot has gone),
      // so other browsers agree.
      await until(async () => (await user.api("/api/prefs/notices-seen")).value?.update === "9.9.9",
        { what: "the ack stored with the account" });
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await user.api("/api/prefs/notices-seen", { method: "PUT", body: { value: {} } });
    }
  });
}
