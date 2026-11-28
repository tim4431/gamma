import { Account, FAKE_AI_MODELS, PYTHON, ROOT, Server, aiModelsAnswer, arrived, fakeAiModels, keyLabel, keyParts, makePdf, wanted } from "../harness.mjs";
import { execFileSync, spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { TOPBAR } from "../../../src/app/topbarItems.js";
import { VIEWER_TOOLS } from "../../../src/app/viewerToolItems.js";
import { certificate } from "./https.mjs";
import { closeEditor, editRow, newPageViaUi, same, tree } from "./notes.mjs";

export async function settingsScenarios(env) {
  const { server, browser, step, openPage, assert, assertEq, assertNoProblems, until, sleep, flags } = env;
  if (!wanted("settings")) return;
  await server.manage("create-user", "settings-user", "settings-pw");
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
    // The account button — on a phone, the bottom bar's More.
    await page.locator('[data-guide="header.account"]').click();
    await page.getByRole("menuitem", { name: "Settings…", exact: true }).click();
    await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
  }
  const nav = (page, name) => page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name, exact: true });
  const row = (page, name) => page.locator(`.settingsPane [data-setting="${name}"]`).first();
  async function search(page, query, label) {
    await page.getByRole("searchbox", { name: "Search settings" }).fill(query);
    await page.locator(".settingsSearchResult").filter({ has: page.getByText(label, { exact: true }) }).click();
    await row(page, label).waitFor({ state: "visible" });
  }

  // Settings → AI → Usage's chart (UsageChart.jsx, usageChartData.js): a bar
  // per day stacked by what its calls were billed as, Cost stacking the
  // pay-per-token calls alone. The days are faked over the real answer.
  await step("settings: the Usage chart stacks each day by billing, switches to calls, cost and monthly bars, and follows the theme", async () => {
    // The last nine of 365 days hold calls, half (rounded up) paid per token
    // at half a cent each, the rest out of a subscription.
    const daily = Array.from({ length: 365 }, (_, index) => {
      const calls = index < 356 ? 0 : index % 7 + 1;
      const input = index < 356 ? 0 : (index % 13 + 1) * 1000, output = index < 356 ? 0 : 200;
      const api = Math.ceil(calls / 2), apiTokens = calls ? Math.round((input + output) * api / calls) : 0;
      // A billing's share of the day, its tokens all counted as input.
      const part = (n, tokens, cost) => ({ calls: n, input: tokens, output: 0, cache_read: 0, cache_write: 0, cost });
      return {
        date: new Date(Date.UTC(2025, 8, 29 + index)).toISOString().slice(0, 10), calls, input, output,
        cache_read: index < 356 ? 0 : 300, cache_write: 0, cost: api * 0.005, priced_calls: api, unpriced_calls: 0,
        billing: !calls ? {} : { api: part(api, apiTokens, api * 0.005),
          ...(calls > api ? { subscription: part(calls - api, input + output - apiTokens, 0) } : {}) },
      };
    });
    let empty = false;
    const { ctx, page } = await setup(undefined, async (context) => {
      await context.route((url) => url.pathname === "/api/ai/usage", async (route) => {
        const response = await route.fetch();
        const data = await response.json();
        await route.fulfill({ json: { ...data, first_at: "2025-09-01T00:00:00",
          daily: empty ? daily.map((day) => ({ ...day, calls: 0, input: 0, output: 0, cache_read: 0, cost: 0, priced_calls: 0, billing: {} })) : daily } });
      });
    });
    try {
      await openSettings(page);
      await nav(page, "Usage").click();
      const graph = page.locator(".usageChart");
      await graph.waitFor();
      await graph.scrollIntoViewIfNeeded();
      assertEq(await graph.locator("button.usageChartBar").count(), 30);
      const today = graph.locator('[data-date="2026-09-28"]');
      await today.click();
      assert((await graph.locator(".usageChartDetail").innerText()).includes("1 call"));
      assertEq(await today.locator(".usageChartSeg").evaluateAll((els) => els.map((el) => el.dataset.kind).join()), "api", "a day of one paid call");
      await today.press("ArrowLeft");
      const before = graph.locator('[data-date="2026-09-27"]');
      assertEq(await graph.locator('button[aria-pressed="true"].usageChartBar').getAttribute("data-date"), "2026-09-27");
      // Seven calls: four paid per token, three out of the subscription,
      // stacked in that order, each named in the detail.
      assertEq(await before.locator(".usageChartSeg").evaluateAll((els) => els.map((el) => el.dataset.kind).join()), "api,subscription",
        "the day stacked by billing");
      const split = await graph.locator(".usageChartDetail .usageChartLegend").innerText();
      assert(split.includes("Pay per token") && split.includes("Subscription"), `the detail splits the day: ${split}`);
      await graph.getByRole("button", { name: "Calls", exact: true }).click();
      assertEq(await today.getAttribute("data-value"), "1");
      assertEq(await before.getAttribute("data-value"), "7");
      await graph.getByRole("button", { name: "Cost", exact: true }).click();
      assertEq(Number(await before.getAttribute("data-value")), 0.02, "the cost of the paid calls alone");
      assertEq(await before.locator(".usageChartSeg").evaluateAll((els) => els.map((el) => el.dataset.kind).join()), "api",
        "no subscription in the cost");
      await graph.getByRole("button", { name: "Tokens", exact: true }).click();
      assertEq(await today.getAttribute("data-value"), "1200");
      if (flags.keep) await graph.screenshot({ path: `${server.dir}/usage-chart-light.png` });
      await graph.getByRole("button", { name: "Chart interval", exact: true }).click();
      await page.getByRole("menuitemradio", { name: "Monthly · last 12 months", exact: true }).click();
      assertEq(await graph.locator("button.usageChartBar").count(), 12);
      const september = graph.locator('[data-date="2026-09-01"]');
      assertEq(Number(await september.getAttribute("data-value")), daily.reduce((sum, day) => sum + day.input + day.output, 0));
      if (flags.keep) await graph.screenshot({ path: `${server.dir}/usage-chart-monthly.png` });
      await graph.getByRole("button", { name: "Chart interval", exact: true }).click();
      await page.getByRole("menuitemradio", { name: "Daily · last 30 days", exact: true }).click();
      await today.click();
      const paid = today.locator('.usageChartSeg[data-kind="api"]');
      const color = await paid.evaluate((el) => getComputedStyle(el).backgroundColor);
      await page.evaluate(() => { document.documentElement.dataset.theme = "sepia"; document.documentElement.dataset.scheme = "light"; });
      assert((await paid.evaluate((el) => getComputedStyle(el).backgroundColor)) !== color, "bars follow the theme accent");
      if (flags.keep) await graph.screenshot({ path: `${server.dir}/usage-chart-sepia.png` });
      await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; document.documentElement.dataset.scheme = "dark"; });
      if (flags.keep) await graph.screenshot({ path: `${server.dir}/usage-chart-dark.png` });
      await page.setViewportSize({ width: 390, height: 844 });
      await graph.scrollIntoViewIfNeeded();
      assert(await graph.evaluate((el) => el.scrollWidth <= el.clientWidth && el.getBoundingClientRect().right <= innerWidth));
      if (flags.keep) await graph.screenshot({ path: `${server.dir}/usage-chart-mobile.png` });
      empty = true;
      await page.getByRole("button", { name: "Refresh", exact: true }).click();
      await until(() => graph.locator(".usageChartDetail").innerText().then((text) => text.includes("No AI calls in this period")));
      assertEq(await graph.locator('button.usageChartBar:not([data-value="0"])').count(), 0);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: a translation service is set up in the Translation pane and picked as the translator", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Language & translation").click();
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
      // The saved service joins the translation task's model menu, after the
      // connections' models; the pick is the account's (Settings → AI →
      // Models' Translation row is the same task).
      const translateWith = row(page, "Translate with").getByRole("button", { name: "Translate with", exact: true });
      await translateWith.click();
      const services = page.getByRole("listbox", { name: "Translate with", exact: true }).getByRole("group", { name: "Translation services" });
      assertEq((await services.getByRole("option").allInnerTexts()).join(), "Microsoft (free),Google Cloud Translation");
      await services.getByRole("option", { name: "Google Cloud Translation" }).click();
      await until(async () => (await translateWith.innerText()) === "Google Cloud Translation", { what: "the button names the service" });
      await until(async () => JSON.stringify((await user.api("/api/ai/models")).roles.translate)
        === JSON.stringify({ pick: "engine:google", model: "engine:google", state: "ok" }), { what: "the account's translation task on the service" });
      // Reasoning effort means nothing to a translation service; the Speed
      // section keeps only the parallel requests.
      await row(page, "Parallel requests").waitFor();
      assertEq(await row(page, "Translation effort").count(), 0);
      assertEq(await row(page, "Translation speed").count(), 0);
      // Its key removed, the pick stays and says it is gone: the chat model
      // translates meanwhile, and the button asks for another.
      await google.getByRole("button", { name: "Remove key", exact: true }).click();
      await until(() => google.innerText().then((text) => text.includes("Not set up")));
      assertEq((await user.api("/api/ai/models")).translate_engines.map((e) => e.id).join(), "engine:microsoft");
      await until(async () => (await translateWith.innerText()) === "Choose…", { what: "the button asks for another" });
      assert((await row(page, "Translate with").getAttribute("class")).includes("aiTaskMissing"), "the row warns in red");
      const instead = await row(page, "Translate with").locator(".settingDesc").innerText();
      assert(instead.includes("test-model-a") && instead.includes("Test connection"), `the row says what translates instead: ${instead}`);
      assertEq((await user.api("/api/ai/models")).roles.translate.state, "missing");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await user.api("/api/translate/engines/google", { method: "DELETE" });
      await user.api("/api/ai/models/roles", { method: "PUT", body: { translate: "" } });
    }
  });

  await step("settings: online search services are set up in the Tool usage pane, and the engine choice survives a reload", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Tool usage").click();
      const tools = row(page, "Search online");
      assert((await tools.innerText()).includes("Search the web and Crossref, arXiv and OpenAlex, and follow papers' citations"));
      const engine = row(page, "Search the web with").getByRole("button", { name: "Search the web with", exact: true });
      assert((await engine.innerText()).includes("Automatic"));
      const brave = row(page, "Brave Search");
      const tavily = row(page, "Tavily");
      const searxng = row(page, "SearXNG");
      const openalex = row(page, "OpenAlex");
      assert((await brave.innerText()).includes("Not set up"));
      assert((await tavily.innerText()).includes("Not set up"));
      assert((await openalex.innerText()).includes("Optional — works without a key"));

      await brave.getByRole("button", { name: "Set up", exact: true }).click();
      let dialog = page.getByRole("dialog", { name: "Brave Search", exact: true });
      await dialog.locator('input[autocomplete="new-password"]').fill("BSA-test-key-123456");
      await dialog.getByRole("button", { name: "Save", exact: true }).click();
      await until(() => brave.innerText().then((text) => text.includes("Key …3456")));
      // Test answers in the row; the real service is not reached from here.
      await page.route("**/api/ai/search-services/brave/test", (route) => route.fulfill({
        json: { ok: false, error: "Brave Search answered HTTP 401 — check the key" } }));
      await brave.getByRole("button", { name: "Test", exact: true }).click();
      await until(() => brave.innerText().then((text) => text.includes("HTTP 401 — check the key")));
      // Saving with the key left empty keeps the stored one.
      await brave.getByRole("button", { name: "Edit", exact: true }).click();
      dialog = page.getByRole("dialog", { name: "Brave Search", exact: true });
      assertEq(await dialog.locator('input[autocomplete="new-password"]').inputValue(), "");
      await dialog.getByRole("button", { name: "Save", exact: true }).click();
      await until(() => brave.innerText().then((text) => text.includes("Key …3456")));

      await searxng.getByRole("button", { name: "Set up", exact: true }).click();
      dialog = page.getByRole("dialog", { name: "SearXNG", exact: true });
      await dialog.locator("input.aiKeyInput").fill("https://search.example.org/");
      await dialog.getByRole("button", { name: "Save", exact: true }).click();
      await until(() => searxng.innerText().then((text) => text.includes("https://search.example.org")));
      if (flags.keep) {
        await row(page, "Online search").scrollIntoViewIfNeeded();
        await page.screenshot({ path: `${server.dir}/settings-online-search.png`, animations: "disabled" });
      }
      await brave.getByRole("button", { name: "Remove", exact: true }).click();
      await until(() => brave.innerText().then((text) => text.includes("Not set up")));

      await engine.click();
      await page.locator(".uiSelectMenu").getByRole("menuitemradio", { name: "Off", exact: true }).click();
      await until(() => engine.innerText().then((text) => text.includes("Off")));
      await page.reload();
      await page.waitForSelector(".folderNewBtn");
      await openSettings(page);
      // Settings search reaches the services by name.
      await search(page, "SearXNG", "Online search services");
      assert((await row(page, "Search the web with").innerText()).includes("Off"));
      assert((await row(page, "SearXNG").innerText()).includes("https://search.example.org"));
      assertNoProblems(page);
    } finally {
      await ctx.close();
      for (const service of ["brave", "searxng"]) {
        await user.api(`/api/ai/search-services/${service}`, { method: "DELETE" });
      }
      await user.api("/api/ai/search-services/engine", { method: "PUT", body: { engine: "auto" } });
    }
  });

  await step("settings: the account menu's card opens Account & sync", async () => {
    const { ctx, page } = await setup();
    try {
      await page.locator('[data-guide="header.account"]').click();
      const card = page.locator('[data-guide="account.card"]');
      await card.hover();
      if (flags.keep) await page.locator('[data-guide="account.menu"]').screenshot({ path: `${server.dir}/account-card-hover.png`, animations: "disabled" });      await card.click();
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
      await page.locator('.setNavAccount[aria-current="page"]').waitFor();
      assertEq(await page.locator('[data-guide="account.menu"]').count(), 0, "the menu closes");
      await row(page, "Gamma Cloud").waitFor();
      assertNoProblems(page);
    } finally {
      await ctx.close();
    }
  });

  await step("settings: a ?settings= link (Gamma Cloud's Export) opens Settings on its pane and leaves the address", async () => {
    const ctx = await user.context(browser);
    try {
      await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
      const page = await openPage(ctx, `${server.base}/?settings=backups`);
      const dialog = page.getByRole("dialog", { name: "Settings", exact: true });
      await dialog.waitFor();
      await until(async () => (await nav(page, "Backups").getAttribute("aria-current")) === "page", { what: "the Backups pane" });
      await until(() => !new URL(page.url()).searchParams.has("settings"), { what: "the parameter to leave the address" });
      await page.reload();
      await page.waitForSelector(".folderNewBtn");
      assertEq(await dialog.count(), 0, "a reload does not open it again");
      assertNoProblems(page);
    } finally {
      await ctx.close();
    }
  });

  await step("settings: Ctrl+, opens it; Ctrl+F goes to the settings search, Enter and the arrows pick a match", async () => {
    const { ctx, page } = await setup();
    try {
      await page.keyboard.press("ControlOrMeta+Comma");
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
      assertEq(await nav(page, "Account & sync").getAttribute("aria-current"), "page", "Settings opens on Account & sync");
      // Over the home library, whose own find box used to take the key.
      await page.keyboard.press("ControlOrMeta+f");
      const box = page.getByRole("searchbox", { name: "Search settings" });
      await until(() => box.evaluate((el) => el === document.activeElement));
      assertEq(await page.locator(".homeFindInput").evaluate((el) => el === document.activeElement), false);
      await page.keyboard.type("translation concurrency");
      await page.keyboard.press("Enter");
      await row(page, "Parallel requests").waitFor({ state: "visible" });
      // ↓ from the box walks the result buttons, ↑ from the first returns.
      await page.keyboard.press("ControlOrMeta+f");
      await page.keyboard.type("status bar");
      await page.keyboard.press("ArrowDown");
      assertEq(await page.evaluate(() => document.activeElement?.classList.contains("settingsSearchResult")), true);
      await page.keyboard.press("ArrowUp");
      await until(() => box.evaluate((el) => el === document.activeElement));
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("Enter");
      await row(page, "Status bar").waitFor({ state: "visible" });
      // A pane with its own filter: Ctrl+F takes that first, again the settings search, again the filter.
      await nav(page, "Keyboard").click();
      const filter = page.getByRole("searchbox", { name: "Filter shortcuts" });
      await filter.waitFor();
      await page.keyboard.press("ControlOrMeta+f");
      await until(() => filter.evaluate((el) => el === document.activeElement));
      await page.keyboard.press("ControlOrMeta+f");
      await until(() => box.evaluate((el) => el === document.activeElement));
      await page.keyboard.press("ControlOrMeta+f");
      await until(() => filter.evaluate((el) => el === document.activeElement));
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
      assertEq(await caps(del), keyParts("Mod-Shift-k").join(" "), "the default chord");
      await del.getByRole("button", { name: "Change the shortcut for Delete line" }).click();
      await del.getByText("Press keys…").waitFor();
      await page.keyboard.press("ControlOrMeta+Alt+d");
      await until(async () => (await caps(del)) === keyParts("Mod-Alt-d").join(" "), { what: "rebound" });
      await del.getByRole("button", { name: "Reset Delete line to its default shortcut" }).waitFor();
      await until(async () => (await user.api("/api/prefs/profile")).value?.keybindings?.["block.deleteLine"] === "Mod-Alt-d", { what: "the binding reaches the profile" });
      // Two commands on one chord are flagged on both rows.
      const ren = row(page, "Rename page");
      await ren.getByRole("button", { name: "Change the shortcut for Rename page" }).click();
      await page.keyboard.press("ControlOrMeta+Alt+d");
      await ren.getByText("Also used by Delete line").waitFor();
      await del.getByText("Also used by Rename page").waitFor();
      // A bare letter is refused.
      await ren.getByRole("button", { name: "Change the shortcut for Rename page" }).click();
      await page.keyboard.press("x");
      await ren.getByText("Add a modifier…").waitFor();
      await page.keyboard.press("Escape");
      await del.waitFor();
      await page.getByRole("button", { name: "Reset all", exact: true }).click();
      await until(async () => (await caps(del)) === keyParts("Mod-Shift-k").join(" "), { what: "reset" });
      assertEq(await caps(ren), "F2", "the clash is gone with the reset");
      await page.getByRole("searchbox", { name: "Filter shortcuts" }).fill("duplicate");
      // Duplicate block above / below, the handwriting group's Duplicate handwriting, and the
      // built-in row of the selected notes' keys (copy, cut, duplicate).
      await until(async () => (await page.locator(".keyboardPane .setRow").count()) === 4, { what: "filtered to the four duplicate rows" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: Keyboard rebinds the PDF's held keys, and the area drag follows", async () => {
    const up = await user.upload("/api/uploads", makePdf([["Held keys paper", "A line to drag across"]]), "held-keys.pdf", "application/pdf");
    const created = await user.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Held keys", source_url: up.source_url } });
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Keyboard").click();
      const caps = async (r) => (await r.locator(".keyCap").allTextContents()).join(" ");
      const area = row(page, "Hold to box a region of the PDF");
      const peek = row(page, "Hold to see the original of a translated page");
      await area.waitFor();
      assertEq(await caps(area), keyLabel("Mod"), "Mod is Ctrl off a Mac, ⌘ on one");
      assertEq(await caps(peek), keyLabel("Alt"));
      await area.getByRole("button", { name: "Change the shortcut for Hold to box a region of the PDF" }).click();
      await area.getByText("Press a modifier key…").waitFor();
      // A key that is no modifier, and Backspace, leave it recording: a held key is never unbound.
      await page.keyboard.press("x");
      await page.keyboard.press("Backspace");
      await area.getByText("Press a modifier key…").waitFor();
      await page.keyboard.down("Alt");
      await page.keyboard.up("Alt");
      await until(async () => (await caps(area)) === keyLabel("Alt"), { what: "rebound to Alt" });
      await until(async () => (await user.api("/api/prefs/profile")).value?.keybindings?.["pdf.areaDrag"] === "Alt", { what: "the held key reaches the profile" });
      // The peek holds Alt too: both rows say so.
      await area.getByText("Also used by Hold to see the original of a translated page").waitFor();
      await peek.getByText("Also used by Hold to box a region of the PDF").waitFor();

      await page.goto(`${server.base}/?page=${created.id}&ws=${user.ws}`);
      await page.waitForSelector('[data-page="1"] .textLayer span', { timeout: 30000 });
      const viewer = page.locator('[data-guide="pdf.viewer"]');
      const crosshair = () => viewer.evaluate((el) => el.classList.contains("areaCursor"));
      const pageBox = await page.locator('[data-page="1"]').boundingBox();
      const x = pageBox.x + 60, y = pageBox.y + 50;
      // Drags with `key` held; how many area rectangles showed mid-drag.
      const drag = async (key) => {
        await page.keyboard.down(key);
        await page.mouse.move(x, y);
        await page.mouse.down();
        await page.mouse.move(x + 160, y + 90, { steps: 10 });
        const boxes = await page.locator(".pdfAreaMarquee").count();
        await page.mouse.up();
        await page.keyboard.up(key);
        return boxes;
      };
      await page.keyboard.down("ControlOrMeta");
      await sleep(200);
      assertEq(await crosshair(), false, "Ctrl is no longer the area key");
      await page.keyboard.up("ControlOrMeta");
      await page.keyboard.down("Alt");
      await until(crosshair, { what: "Alt shows the crosshair" });
      await page.keyboard.up("Alt");
      await until(async () => !(await crosshair()), { what: "the crosshair goes with the key" });
      assertEq(await drag("ControlOrMeta"), 0, "a Ctrl+drag draws no rectangle");
      await page.keyboard.press("Escape");
      assert(await drag("Alt") > 0, "an Alt+drag boxes a region");
      await page.locator(".plainTip .colorBtn").first().waitFor();
      await page.keyboard.press("Escape");
      assertNoProblems(page);

      await openSettings(page);
      await nav(page, "Keyboard").click();
      await page.getByRole("button", { name: "Reset all", exact: true }).click();
      await until(async () => (await caps(row(page, "Hold to box a region of the PDF"))) === keyLabel("Mod"), { what: "reset" });
      await until(async () => !(await user.api("/api/prefs/profile")).value?.keybindings?.["pdf.areaDrag"], { what: "the reset reaches the profile" });
    } finally { await ctx.close(); }
  });

  await step("settings: model discovery updates automatically, the long checklist scrolls and searches, and a name the list lacks is added by name", async () => {
    const { ctx, page } = await setup({ width: 800, height: 650 });
    try {
      const calls = [];
      const models = Array.from({ length: 100 }, (_, i) => `gpt-test-${String(i).padStart(3, "0")}`);
      await page.route("**/api/ai/model-catalog", async (route) => {
        const body = route.request().postDataJSON();
        calls.push(body);
        if (body.api_key === "old-key") await new Promise((resolve) => setTimeout(resolve, 1500));
        await route.fulfill({ json: { models: body.api_key === "old-key" ? ["stale-model"] : models } });
      });
      // The older key's answer, which lands last.
      const oldAnswer = page.waitForResponse((response) => response.url().endsWith("/api/ai/model-catalog")
        && response.request().postDataJSON()?.api_key === "old-key");
      await openSettings(page);
      await nav(page, "Models").click();
      await page.getByRole("button", { name: "Add provider", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Connect an AI service", exact: true });
      await dialog.getByRole("group", { name: "API key", exact: true }).getByRole("button", { name: "OpenAI API", exact: true }).click();
      const key = dialog.locator('input[autocomplete="new-password"]');
      assertEq(await key.getAttribute("placeholder"), "sk-proj-…", "the provider's own key shape");
      assertEq(await dialog.getByRole("link", { name: /^Get a key at platform\.openai\.com/ }).getAttribute("href"), "https://platform.openai.com/api-keys");
      await key.fill("old-key");
      await until(() => calls.length === 1);
      await key.fill("new-key");
      await until(() => calls.length === 2);
      await dialog.getByRole("button", { name: "100 usable" }).waitFor();
      // The live list doubles as the key check, and its first model is picked.
      await dialog.getByText("Key works · 100 models available", { exact: true }).waitFor();
      await dialog.getByRole("button", { name: "Remove gpt-test-000", exact: true }).waitFor();
      const list = dialog.getByRole("group", { name: "Available models", exact: true });
      const box = (name) => list.getByRole("checkbox", { name, exact: true });
      assertEq(await list.getByRole("checkbox").count(), 100, "every model listed");
      assertEq(await box("gpt-test-000").isChecked(), true, "the first one ticked");
      // The list scrolls inside the dialog, which fits this short window.
      await list.scrollIntoViewIfNeeded();
      const bounds = await list.boundingBox();
      assert(bounds.y >= 0 && bounds.y + bounds.height <= 650, "model list fits the viewport");
      assert(await list.evaluate((el) => el.scrollHeight > el.clientHeight), "long list is scrollable");
      await list.hover();
      await page.mouse.wheel(0, 1600);
      await until(() => list.evaluate((el) => el.scrollTop > 0));
      // The search narrows it; a tick adds the model, an untick takes it off.
      const search = dialog.getByRole("searchbox", { name: "Search models", exact: true });
      await search.fill("099");
      assertEq((await list.getByRole("checkbox").count()), 1, "the search narrows the list");
      await box("gpt-test-099").check();
      await dialog.getByRole("button", { name: "Remove gpt-test-099", exact: true }).waitFor();
      await search.fill("");
      await box("gpt-test-000").uncheck();
      await until(async () => (await dialog.getByRole("button", { name: "Remove gpt-test-000", exact: true }).count()) === 0,
        { what: "the unticked model off the picks" });
      // A name the list doesn't show is added by name, and stays listed, tagged.
      const byName = dialog.getByRole("textbox", { name: "Model name", exact: true });
      await byName.fill("my-custom-model");
      await byName.press("Enter");
      await dialog.getByRole("button", { name: "Remove my-custom-model", exact: true }).waitFor();
      const custom = list.locator(".docPickerItem").filter({ hasText: "my-custom-model" });
      assertEq(await custom.getByRole("checkbox").isChecked(), true);
      await custom.getByText("not listed", { exact: true }).waitFor();
      assertEq(await list.locator(".docPickerItem").first().innerText().then((text) => text.startsWith("my-custom-model")), true,
        "the name the list lacks first");
      assertEq(await byName.inputValue(), "", "the box empties for the next name");
      // Let the older response land (and the page take it in, a frame on);
      // it must not replace the new catalog.
      await (await oldAnswer).finished();
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assertEq(await dialog.getByRole("button", { name: "100 usable" }).isVisible(), true, "the new catalog stays");
      assertEq(await box("stale-model").count(), 0, "nothing of the stale one");
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
      await nav(page, "Models").click();
      await page.getByRole("button", { name: "Add provider", exact: true }).click();
      // The connection is tested once saved; the test is answered here, offline.
      await page.route("**/api/ai/providers/*/test", (route) => route.fulfill({ json: { ok: true, model: "deepseek-flash", latency_ms: 300 } }));
      const dialog = page.getByRole("dialog", { name: "Connect an AI service", exact: true });
      // The named services are cards among the API keys', the search finding one.
      await dialog.getByRole("searchbox", { name: "Search services", exact: true }).fill("deeps");
      const card = dialog.getByRole("group", { name: "API key", exact: true }).getByRole("button", { name: "DeepSeek", exact: true });
      assertEq(await dialog.locator(".setPictureChoice").count(), 1, "the search leaves DeepSeek's card alone");
      await card.click();
      assertEq(await card.getAttribute("aria-pressed"), "true");
      assertEq(await dialog.getByRole("group", { name: "Plan", exact: true }).count(), 0, "one plan: no Plan to choose");
      // A preset's endpoint is fixed: no Base URL field to fill.
      assertEq(await dialog.getByRole("textbox", { name: /Base URL/ }).count(), 0);
      await dialog.locator('input[autocomplete="new-password"]').fill("sk-deepseek-e2e");
      await dialog.getByRole("button", { name: "2 usable" }).waitFor();
      assertEq(calls.at(-1).protocol, "openai");
      assertEq(calls.at(-1).base_url, "https://api.deepseek.com");
      await dialog.getByRole("button", { name: "Remove deepseek-flash", exact: true }).waitFor();
      await dialog.getByRole("button", { name: "Connect", exact: true }).click();
      await until(() => dialog.count().then((n) => n === 0));
      const saved = page.locator(".aiProvRow").filter({ hasText: "sk-deepseek-e2e".slice(-4) });
      await saved.locator(".aiProvName").filter({ hasText: "DeepSeek" }).waitFor();
      await saved.getByText(/working · deepseek-flash/).waitFor(); // tested right after saving
      assertNoProblems(page);
    } finally {
      await ctx.close();
      const info = await user.api("/api/ai/settings");
      for (const p of info.providers.filter((p) => p.base_url === "https://api.deepseek.com")) {
        await user.api(`/api/ai/providers/${p.id}`, { method: "DELETE" });
      }
    }
  });

    await step("settings: a vendor with several plans offers them side by side under its card, and a subscription connects at its own endpoint", async () => {
    const { ctx, page } = await setup();
    const kimiCode = "https://api.kimi.com/coding/v1";
    try {
      const calls = [];
      await page.route("**/api/ai/model-catalog", async (route) => {
        calls.push(route.request().postDataJSON());
        await route.fulfill({ json: { models: ["kimi-for-coding"] } });
      });
      await page.route("**/api/ai/providers/*/test", (route) => route.fulfill({ json: { ok: true, model: "kimi-for-coding", latency_ms: 300 } }));
      await openSettings(page);
      await nav(page, "Models").click();
      await page.getByRole("button", { name: "Add provider", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Connect an AI service", exact: true });
      const keys = dialog.getByRole("group", { name: "API key", exact: true });
      const plans = dialog.getByRole("group", { name: "Sign in or subscription", exact: true });
      const plan = dialog.getByRole("group", { name: "Plan", exact: true });
      const pressed = async () => (await plan.getByRole("button", { pressed: true }).allInnerTexts()).join();
      // Qwen's API key card: the international and China endpoints side by side, the first chosen.
      await keys.getByRole("button", { name: "Qwen", exact: true }).click();
      assertEq((await plan.getByRole("button").allInnerTexts()).join(), "API key,API key (China)");
      assertEq(await pressed(), "API key", "a vendor opens on its first plan");
      await plan.getByRole("button", { name: "API key (China)", exact: true }).click();
      assertEq(await pressed(), "API key (China)");
      // Its coding plan is a card of the subscriptions, with its terms note.
      await plans.getByRole("button", { name: "Qwen Coding Plan", exact: true }).click();
      assertEq(await pressed(), "Coding plan subscription");
      await dialog.getByText(/only inside coding tools such as Qwen Code/).waitFor();
      // Kimi's subscription: one plan, its own endpoint and key page, no Base URL to fill.
      await plans.getByRole("button", { name: "Kimi Code", exact: true }).click();
      assertEq(await plan.count(), 0, "one plan: nothing to choose");
      assertEq(await dialog.getByText(/only inside coding tools/).count(), 0);
      assertEq(await dialog.getByRole("textbox", { name: /Base URL/ }).count(), 0);
      await dialog.getByRole("link", { name: "Get a key at www.kimi.com/code/console" }).waitFor();
      await dialog.locator('input[autocomplete="new-password"]').fill("sk-kimi-code-e2e");
      await dialog.getByRole("button", { name: "1 usable" }).waitFor();
      assertEq(calls.at(-1).protocol, "openai");
      assertEq(calls.at(-1).base_url, kimiCode);
      await dialog.getByRole("button", { name: "Connect", exact: true }).click();
      await until(() => dialog.count().then((n) => n === 0));
      const saved = page.locator(".aiProvRow").filter({ hasText: "sk-kimi-code-e2e".slice(-4) });
      await saved.locator(".aiProvName").filter({ hasText: "Kimi Code" }).waitFor();
      assert((await saved.locator(".aiProvName").innerText()).includes("Subscription"), "its card says how it is paid for");
      // Editing it opens on the same card.
      await saved.getByRole("button", { name: "Manage", exact: true }).click();
      const edit = page.getByRole("dialog", { name: "Edit key", exact: true });
      await edit.waitFor();
      assertEq(await edit.getByRole("group", { name: "Sign in or subscription", exact: true }).getByRole("button", { name: "Kimi Code", exact: true })
        .getAttribute("aria-pressed"), "true", "the vendor's plan");
      await edit.getByRole("button", { name: "Cancel", exact: true }).click();
      await until(() => edit.count().then((n) => n === 0));
      assertNoProblems(page);
    } finally {
      await ctx.close();
      const info = await user.api("/api/ai/settings");
      for (const p of info.providers.filter((p) => p.base_url === kimiCode)) {
        await user.api(`/api/ai/providers/${p.id}`, { method: "DELETE" });
      }
    }
  });

  await step("settings: a failed Test says what failed in the chat error card's words, and its fix opens the connection", async () => {
    const { ctx, page } = await setup();
    try {
      // The probe's answers are faked; their kinds are the backend's (test_ai_failures.py).
      let probe = { ok: false, auth: true, kind: "auth", model: "test-model-a", error: "upstream 401: Incorrect API key provided" };
      await page.route("**/api/ai/providers/*/test", (route) => route.fulfill({ json: probe }));
      await openSettings(page);
      await nav(page, "Models").click();
      const conn = page.locator(".settingsPane .aiProvRow").filter({ hasText: "Test connection" });
      await conn.getByRole("button", { name: "Test", exact: true }).click();
      const result = conn.locator(`[title="${probe.error}"]`);
      await result.waitFor();
      assertEq(await result.innerText(), "✗ Test connection rejected the API key — Update key");
      await result.getByRole("button", { name: "Update key", exact: true }).click();
      const edit = page.getByRole("dialog", { name: "Edit key", exact: true });
      await edit.waitFor();
      await edit.getByRole("button", { name: "Cancel", exact: true }).click();
      await until(() => edit.count().then((n) => n === 0));
      probe = { ok: false, auth: false, kind: "no_model", model: "", error: "no model picked — edit the connection and choose one" };
      await conn.getByRole("button", { name: "Test", exact: true }).click();
      const noModel = conn.locator(`[title="${probe.error}"]`);
      await noModel.waitFor();
      assertEq(await noModel.innerText(), "✗ No model picked for Test connection — Edit connection");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: a model tag says whether the model calls tools, and a click checks it again", async () => {
    const { ctx, page } = await setup();
    try {
      // The verdicts and the check's answer are faked; the backend's are
      // test_ai_tool_check.py's.
      const reason = "upstream 404: No endpoints found that support tool use";
      let listReads = 0;
      await page.route("**/api/ai/settings", async (route) => {
        listReads++;
        const response = await route.fetch();
        const json = await response.json();
        const own = json.providers.find((p) => p.name === "Test connection");
        own.tool_checks = { "test-model-a": { ok: false, reason, at: "2026-11-01T09:30:00.000000Z" },
          "test-model-b": { ok: true, reason: "", at: "2026-11-01T09:30:00.000000Z" } };
        await route.fulfill({ response, json });
      });
      let posted = null;
      await page.route("**/api/ai/providers/*/test", async (route) => {
        posted = route.request().postDataJSON();
        await route.fulfill({ json: { ok: true, model: "test-model-a", latency_ms: 900,
          tools: { ok: false, reason: "answered in text instead of calling the check tool: “ok”", at: "2026-11-06T09:30:00.000000Z" } } });
      });
      await openSettings(page);
      await nav(page, "Models").click();
      const conn = page.locator(".settingsPane .aiProvRow").filter({ hasText: "Test connection" });
      const tag = conn.getByRole("button", { name: "test-model-a", exact: true });
      await tag.waitFor();
      const title = await tag.getAttribute("title");
      assert(title.startsWith(`No tools: ${reason} · checked `) && title.endsWith("Click to check again."), title);
      assert((await conn.getByRole("button", { name: "test-model-b", exact: true }).getAttribute("title")).startsWith("Uses tools · checked "));
      const reads = listReads;
      await tag.click();
      const result = conn.locator(".aiProvDesc").filter({ hasText: "can't use tools" });
      await result.waitFor();
      assertEq(await result.innerText(), "✗ test-model-a can't use tools");
      assert((await result.getAttribute("title")).startsWith("answered in text"));
      assertEq(posted.tools_model, "test-model-a", "the tag checks its own model");
      await until(() => listReads > reads, { what: "the list read again" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: a pasted ChatGPT sign-in address connects at once, a device code is offered", async () => {
    const { ctx, page } = await setup();
    try {
      await page.evaluate(() => {
        window.open = (url) => { window.testSignInUrl = url; return null; };
      });
      // OpenAI's side is faked: the start (a remote browser, so a device code
      // and no listener), the waits (nothing caught yet) and the exchange.
      let started = null;
      await page.route("**/api/ai/oauth/chatgpt/start", async (route) => {
        started = route.request().postDataJSON();
        await route.fulfill({ json: { auth_url: "https://auth.openai.com/oauth/authorize?state=e2e-state", state: "e2e-state",
          local: false, device: { user_code: "ABCD-1234", verification_url: "https://auth.openai.com/codex/device" } } });
      });
      let waits = 0;
      await page.route("**/api/ai/oauth/chatgpt/status", async (route) => {
        waits++;
        await route.fulfill({ json: { ready: false, error: "" } });
      });
      let completed = null;
      await page.route("**/api/ai/oauth/chatgpt/complete", async (route) => {
        completed = route.request().postDataJSON();
        const info = await user.api("/api/ai/settings");
        info.providers.push({ id: "oauth-test", protocol: "chatgpt", name: "Test sign-in", models: "gpt-test", oauth_connected: true, quota: true });
        await route.fulfill({ json: info });
      });
      let catalogCalls = 0;
      await page.route("**/api/ai/model-catalog", async (route) => {
        catalogCalls++;
        await route.fulfill({ json: { models: ["gpt-test", "gpt-new-model"] } });
      });
      await openSettings(page);
      await nav(page, "Models").click();
      await page.getByRole("button", { name: "Add provider", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Connect an AI service", exact: true });
      await dialog.getByRole("group", { name: "Sign in or subscription", exact: true }).getByRole("button", { name: "ChatGPT", exact: true }).click();
      await dialog.getByRole("button", { name: "Open ChatGPT sign-in", exact: true }).click();
      await until(() => page.evaluate(() => !!window.testSignInUrl));
      assertEq(started?.device, true, "a device code is asked for");
      assertEq(await dialog.getByRole("textbox", { name: "One-time code", exact: true }).inputValue(), "ABCD-1234");
      await until(() => waits > 0, { what: "the form waiting for the device code" });
      const callback = dialog.getByRole("textbox", { name: /Callback URL/ });
      assertEq(await callback.inputValue(), "", "callback remains a manual input");
      // Pasting the address connects: no Connect click.
      const address = "http://localhost:1455/auth/callback?code=test&state=e2e-state";
      await callback.evaluate((input, text) => {
        input.focus();
        const data = new DataTransfer();
        data.setData("text/plain", text);
        input.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
      }, address);
      const edit = page.getByRole("dialog", { name: "Edit key", exact: true });
      await edit.getByRole("button", { name: "2 usable" }).waitFor();
      assertEq(completed?.callback, address, "the pasted address is redeemed");
      assertEq(catalogCalls, 1);
      const listed = edit.getByRole("group", { name: "Available models", exact: true });
      assertEq(await listed.getByRole("checkbox", { name: "gpt-test", exact: true }).isChecked(), true, "the entry's model ticked");
      assertEq(await listed.getByRole("checkbox", { name: "gpt-new-model", exact: true }).isChecked(), false, "the account's other model offered");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // The Gemini sign-in (docs/dev/ai.md "The Gemini sign-in"): its card says
  // Google serves it only to organizations' licenses now and that its terms
  // forbid it; the license's Google Cloud project is typed first, and from a
  // browser on another machine Google ends on a page showing a code, which
  // connects once pasted, with the project. Google's side is faked as
  // ChatGPT's is above.
  await step("settings: the Gemini sign-in warns of Google's terms, and the code Google shows connects once pasted, with the organization's project", async () => {
    const { ctx, page } = await setup();
    try {
      await page.evaluate(() => {
        window.open = (url) => { window.testSignInUrl = url; return null; };
      });
      let waits = 0;
      await page.route("**/api/ai/oauth/gemini-signin/start", (route) => route.fulfill({ json: {
        auth_url: "https://accounts.google.com/o/oauth2/v2/auth?state=g-state", state: "g-state", local: false, device: null } }));
      await page.route("**/api/ai/oauth/gemini-signin/status", (route) => { waits++; return route.fulfill({ json: { ready: false, error: "" } }); });
      // The server's side: refused as an individual account without the
      // project (gemini_oauth.RETIRED's words), connected with it.
      const completes = [];
      await page.route("**/api/ai/oauth/gemini-signin/complete", async (route) => {
        const body = route.request().postDataJSON();
        completes.push(body);
        if (!body.project) {
          await route.fulfill({ status: 400, json: { detail: "Gemini setup failed: Google says this account can't use Gemini here: "
            + "This client is no longer supported for Gemini Code Assist for individuals. Google retired this sign-in for "
            + "individual accounts (free, Google AI Pro and Ultra) in June 2026." } });
          return;
        }
        const info = await user.api("/api/ai/settings");
        info.providers.push({ id: "gemini-test", protocol: "gemini-signin", name: "Gemini", models: "gemini-test-pro",
          oauth_connected: true, quota: true, service: "gemini-signin", billing: "subscription" });
        await route.fulfill({ json: info });
      });
      await page.route("**/api/ai/model-catalog", (route) => route.fulfill({ json: { models: ["gemini-test-pro", "gemini-test-flash"] } }));
      await openSettings(page);
      await nav(page, "Models").click();
      await page.getByRole("button", { name: "Add provider", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Connect an AI service", exact: true });
      await dialog.getByRole("group", { name: "Sign in or subscription", exact: true }).getByRole("button", { name: "Gemini", exact: true }).click();
      await dialog.getByText(/^Since June 2026 Google serves this sign-in only to Gemini Code Assist Standard and Enterprise.*Google's terms forbid/).waitFor();
      await dialog.getByText("Sign in with Google", { exact: true }).waitFor();
      await dialog.getByRole("button", { name: "Open Google sign-in", exact: true }).click();
      await until(() => page.evaluate(() => !!window.testSignInUrl));
      const code = dialog.getByRole("textbox", { name: /Authorization code/ });
      await code.waitFor();
      // Pasting Google's code connects: no Connect click, no waiting on the server.
      await code.evaluate((input, text) => {
        input.focus();
        const data = new DataTransfer();
        data.setData("text/plain", text);
        input.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
      }, "4/0AfakeGoogleCode12345");
      // Refused without the project: Google's words and the way left; the
      // project typed then connects the same sign-in with Connect.
      await dialog.getByText(/Google retired this sign-in for individual accounts/).waitFor();
      await dialog.getByRole("textbox", { name: /Google Cloud project/ }).fill("acme-gemini-01");
      await dialog.getByRole("button", { name: "Connect", exact: true }).click();
      const edit = page.getByRole("dialog", { name: "Edit key", exact: true });
      await edit.getByRole("button", { name: "2 usable" }).waitFor();
      assertEq(completes.map((c) => [c.callback, c.project || ""]).join(" | "),
        "4/0AfakeGoogleCode12345, | 4/0AfakeGoogleCode12345,acme-gemini-01", "the same sign-in tried again with the project");
      assertEq(waits, 0, "nothing to wait for: the code comes from the person");
      assertNoProblems(page, [/^POST \/api\/ai\/oauth\/gemini-signin\/complete -> 400$/]);
    } finally { await ctx.close(); }
  });

  await step("settings: connecting an assistant, and the workspaces each connection reads", async () => {
    const { ctx, page } = await setup();
    const second = await user.api("/api/workspaces", { method: "POST", body: { name: "Other assistant workspace" } });
    try {
      await openSettings(page);
      await nav(page, "Integrations").click();
      // Claude Code is the first tab: one command, then sign-in from /mcp
      const claudeCommand = page.getByRole("textbox", { name: "Claude Code connection command", exact: true });
      await claudeCommand.waitFor();
      // the commands themselves are pinned by assistantSetup.test.mjs;
      // here: each field carries this server's URL and follows the platform switch
      assert((await claudeCommand.inputValue()).includes(`'${server.base}/mcp'`), "the connection command names this server");
      await page.getByRole("button", { name: "Copy connection command", exact: true }).click();
      await page.getByText("Copied. You can paste it now.", { exact: true }).waitFor();
      await page.getByText(/run \/mcp, select gamma and choose Authenticate/).waitFor();
      assert(!await page.getByRole("textbox", { name: "Codex setup command", exact: true }).isVisible(), "only the selected assistant is shown");
      await page.getByText("Changed the server address?", { exact: true }).click();
      assert((await page.getByRole("textbox", { name: "Claude Code change server commands", exact: true }).inputValue())
        .includes(`'${server.base}/mcp'`), "the change-server commands name this server");
      await page.getByText("Optional: the /gamma:gamma workflow", { exact: true }).click();
      await page.getByRole("textbox", { name: "Claude Code plugin install commands", exact: true }).waitFor();
      await page.getByText("Remove Gamma from Claude Code", { exact: true }).click();
      assert((await page.getByRole("textbox", { name: "Claude Code remove commands", exact: true }).inputValue())
        .startsWith("claude mcp remove gamma"), "the removal undoes the connection");
      await page.getByRole("button", { name: "Codex", exact: true }).click();
      await page.getByRole("button", { name: "Windows PowerShell", exact: true }).click();
      const commandField = page.getByRole("textbox", { name: "Codex setup command", exact: true });
      const windowsCommand = await commandField.inputValue();
      assert(windowsCommand.includes(`'${server.base}/mcp'`), "the setup command names this server");
      await page.getByRole("button", { name: "macOS / Linux", exact: true }).click();
      await until(async () => (await commandField.inputValue()) !== windowsCommand, { what: "the command follows the platform" });
      await page.getByRole("button", { name: "Copy setup command", exact: true }).click();
      await page.getByText("Copied. You can paste it now.", { exact: true }).waitFor();
      await page.setViewportSize({ width: 390, height: 844 });
      await commandField.scrollIntoViewIfNeeded();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "setup command fits a narrow viewport");
      if (process.env.GAMMA_MCP_SCREENSHOTS) {
        fs.mkdirSync(process.env.GAMMA_MCP_SCREENSHOTS, { recursive: true });
        await page.screenshot({ path: path.join(process.env.GAMMA_MCP_SCREENSHOTS, "codex-setup-mobile.png"), fullPage: true });
      }
      await page.setViewportSize({ width: 1280, height: 860 });
      await page.getByRole("button", { name: "Other", exact: true }).click();
      const serverUrl = page.getByRole("textbox", { name: "Gamma MCP server URL" });
      assertEq(await serverUrl.inputValue(), `${server.base}/mcp`);
      await page.getByRole("button", { name: "Copy server URL", exact: true }).click();
      await page.getByText("Copied. You can paste it now.", { exact: true }).waitFor();
      // A token is the advanced way: collapsed, and it reads the workspaces ticked
      const nameField = page.getByRole("textbox", { name: "Connection name" });
      assert(!await nameField.isVisible(), "tokens start collapsed");
      await page.getByText("Tokens (advanced)", { exact: true }).click();
      await nameField.fill("Codex test");
      const tokenSpaces = page.getByRole("group", { name: "Workspaces the token may read" });
      const otherSpace = tokenSpaces.getByRole("checkbox", { name: "Other assistant workspace", exact: true });
      assert(!await otherSpace.isChecked(), "a token starts with the open workspace only");
      await otherSpace.check();
      await page.getByRole("button", { name: "Create token", exact: true }).click();
      const secret = page.getByRole("textbox", { name: "New integration token" });
      await secret.waitFor();
      assert((await secret.inputValue()).startsWith("gamma_"));
      const codexToken = await secret.inputValue();
      let connections = await user.api("/api/integrations/tokens");
      assertEq(connections.tokens.length, 1);
      assertEq(JSON.stringify(connections.tokens[0].workspaces), JSON.stringify([user.ws, second.id]));
      assert(!JSON.stringify(connections).includes(await secret.inputValue()), "token is never returned in listings");
      await page.getByText(/Reads .*Other assistant workspace/).waitFor();
      if (process.env.GAMMA_MCP_SCREENSHOTS) {
        await page.screenshot({ path: path.join(process.env.GAMMA_MCP_SCREENSHOTS, "settings-desktop.png"), fullPage: true });
      }
      await page.getByRole("button", { name: "Done", exact: true }).click();
      await secret.waitFor({ state: "detached" });
      // A token at the Comment level: a read token whose workspace says Comment, between View and Edit
      await otherSpace.uncheck();
      const levels = tokenSpaces.getByRole("button", { name: "Comment", exact: true });
      assertEq(await levels.count(), 1, "a level for the one workspace ticked");
      assertEq(JSON.stringify(await tokenSpaces.getByRole("button", { pressed: true }).allTextContents()),
        JSON.stringify(["View"]), "a token starts at View");
      await levels.click();
      assertEq(await levels.getAttribute("aria-pressed"), "true", "the level taken");
      await nameField.fill("Comment script");
      await page.getByRole("button", { name: "Create token", exact: true }).click();
      await secret.waitFor();
      const commentToken = (await user.api("/api/integrations/tokens")).tokens.find((item) => item.name === "Comment script");
      assertEq(commentToken?.scope, "read", "a read token");
      assertEq(JSON.stringify(commentToken?.comment_workspaces), JSON.stringify([user.ws]), "that comments in its workspace");
      await page.getByText(/Comment script/).waitFor();
      await page.getByText(/comments in /).waitFor();
      assertEq(await page.getByRole("button", { name: "Choose workspaces", exact: true }).count(), 2, "its workspaces can be changed, as a read token's");
      await page.getByRole("button", { name: "Done", exact: true }).click();
      await secret.waitFor({ state: "detached" });
      await user.api(`/api/integrations/tokens/${commentToken.id}`, { method: "DELETE" });
      await page.getByRole("button", { name: "Refresh connections", exact: true }).click();
      await until(() => page.getByRole("button", { name: "Disconnect", exact: true }).count().then((n) => n === 1));
      // Change what the connection reads, without connecting again
      await page.getByRole("button", { name: "Choose workspaces", exact: true }).click();
      const editor = page.getByRole("group", { name: "Workspaces Codex test may read" });
      await editor.getByRole("checkbox", { name: "Other assistant workspace", exact: true }).uncheck();
      // and let it edit the notes of the one it keeps, as a share's person is given Edit
      assertEq(await editor.getByRole("button", { name: "Edit", exact: true }).count(), 1, "a role for each workspace ticked");
      await editor.getByRole("button", { name: "Edit", exact: true }).click();
      // it asks before each change unless told not to
      const asking = page.getByRole("checkbox", { name: /Ask me before each change it makes/ });
      assert(await asking.isChecked(), "a connection that edits asks first");
      await asking.uncheck();
      await page.getByRole("button", { name: "Save", exact: true }).click();
      await editor.waitFor({ state: "detached" });
      connections = await user.api("/api/integrations/tokens");
      assertEq(JSON.stringify(connections.tokens[0].workspaces), JSON.stringify([user.ws]));
      assertEq(JSON.stringify(connections.tokens[0].edit_workspaces), JSON.stringify([user.ws]));
      assertEq(connections.tokens[0].asks, false);
      await page.getByText(/edits notes in .* · changes without asking/).waitFor();
      // Deleting notes is its own permission, off until ticked
      await page.getByRole("button", { name: "Choose workspaces", exact: true }).click();
      const deleting = page.getByRole("checkbox", { name: /May delete notes/ });
      assert(!await deleting.isChecked(), "a connection that edits deletes nothing yet");
      await deleting.check();
      await page.getByRole("button", { name: "Save", exact: true }).click();
      await deleting.waitFor({ state: "detached" });
      assertEq((await user.api("/api/integrations/tokens")).tokens[0].deletes, true);
      await page.getByText(/edits notes in .* · may delete notes/).waitFor();
      // A change it makes through MCP is listed under its row, and undone there: made while the account reviews,
      // a suggestion, which Accept there keeps
      const target = await user.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Settings undo page" } });
      await user.api("/api/prefs/ai-review", { method: "PUT", body: { value: true } });
      const pasted = await fetch(`${server.base}/mcp`, { method: "POST", headers: { Authorization: `Bearer ${codexToken}`,
        "Content-Type": "application/json", Accept: "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "create_block",
        arguments: { parent_id: target.id, content: "Pasted one\n\nPasted two" } } }) }).then((r) => r.json());
      assert(!pasted.result.isError && pasted.result.structuredContent.change_id, JSON.stringify(pasted.result));
      await page.getByRole("button", { name: "Changes", exact: true }).click();
      const change = page.getByText("Added 2 notes in “Settings undo page”", { exact: true });
      await change.waitFor();
      const suggestion = page.locator(".settingsPane .uiTag", { hasText: "Suggestion" });
      await suggestion.waitFor();
      await page.getByRole("button", { name: "Accept", exact: true }).click();
      await suggestion.waitFor({ state: "detached" });
      assertEq(await page.getByRole("button", { name: "Accept", exact: true }).count(), 0, "an accepted change has no Accept");
      assertEq((await user.api(`/api/blocks/${target.id}/children`)).children.length, 2, "accepted, the paste stays");
      await page.getByRole("button", { name: "Undo", exact: true }).click();
      await page.getByText("Undone", { exact: true }).waitFor();
      assertEq((await user.api(`/api/blocks/${target.id}/children`)).children.length, 0, "the paste is gone");
      await page.getByRole("button", { name: "Changes", exact: true }).click();
      await change.waitFor({ state: "detached" });
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
      // A connection made in another workspace is the account's too: listed here.
      const elsewhere = await user.api(`/api/integrations/tokens?ws=${second.id}`, { method: "POST", body: { name: "Codex elsewhere" } });
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await page.getByText("Codex elsewhere", { exact: true }).waitFor();
      await revoked.waitFor({ state: "detached" });
      await page.getByText("Reads Other assistant workspace", { exact: false }).waitFor();
      await user.api(`/api/integrations/tokens/${elsewhere.id}`, { method: "DELETE" });
      await page.getByRole("button", { name: "Refresh connections", exact: true }).click();
      await until(() => page.getByRole("button", { name: "Disconnect", exact: true }).count().then((n) => n === 1));
      await page.getByRole("button", { name: "Disconnect", exact: true }).click();
      await page.getByText("No assistant is connected yet.", { exact: true }).waitFor();
      assertEq((await user.api("/api/integrations/tokens")).tokens.length, 0);
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await user.api("/api/prefs/ai-review", { method: "PUT", body: { value: false } });
      await user.api(`/api/workspaces/${second.id}`, { method: "DELETE" });
    }
  });

  await step("settings: DeepSeek Harness's setup command signs in through the browser", async () => {
    const { ctx, page } = await setup();
    const second = await user.api("/api/workspaces", { method: "POST", body: { name: "Harness workspace" } });
    try {
      await openSettings(page);
      await nav(page, "Integrations").click();
      await page.getByRole("button", { name: "DeepSeek Harness", exact: true }).click();
      await page.getByRole("button", { name: "macOS / Linux", exact: true }).click();
      // shown at once: it names this server and carries nothing secret
      const command = page.getByRole("textbox", { name: "DeepSeek Harness setup command", exact: true });
      const unix = await command.inputValue();
      // the commands themselves are pinned by assistantSetup.test.mjs
      assert(unix.endsWith(`'${server.base}/mcp')`), "the setup command names this server alone");
      // What the script does once the plugin is in: ask for a sign-in and open it.
      const post = (path, body) => fetch(`${server.base}/api/integrations/${path}`, { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const started = await post("device", { name: "DeepSeek Harness" });
      assertEq(started.status, 201);
      const { device_code: deviceCode, user_code: userCode } = await started.json();
      assertEq((await post("device/token", { device_code: deviceCode })).status, 202, "waiting for the browser");
      const signIn = await ctx.newPage();
      await signIn.goto(`${server.base}/?gamma_device=${userCode}`);
      await signIn.getByText(userCode, { exact: true }).waitFor();
      await signIn.getByRole("group", { name: "Workspaces it may read" })
        .getByRole("checkbox", { name: "All workspaces, and those added later", exact: true }).check();
      await signIn.getByRole("button", { name: "Allow read-only access" }).click();
      await signIn.getByText(/DeepSeek Harness is connected\. Go back to your terminal/).waitFor();
      const claimed = await post("device/token", { device_code: deviceCode });
      assertEq(claimed.status, 200);
      const { token } = await claimed.json();
      assertEq((await post("device/token", { device_code: deviceCode })).status, 400, "the token is collected once");
      await signIn.close();
      // Back in settings, the connection is listed.
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await until(() => page.getByRole("button", { name: "Disconnect", exact: true }).count().then((n) => n === 1));
      await page.getByText(/Reads all workspaces/).waitFor();
      const [made] = (await user.api("/api/integrations/tokens")).tokens;
      assertEq(made.name, "DeepSeek Harness");
      assert(made.all_workspaces && made.scope === "read", "a read-only connection to every workspace");
      // What dsh sends on startup: an initialize with the bearer header.
      const init = await fetch(`${server.base}/mcp`, { method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize",
          params: { protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "dsh-e2e", version: "0" } } }) });
      assertEq(init.status, 200);
      await page.getByRole("button", { name: "Windows PowerShell", exact: true }).click();
      assert((await command.inputValue()).startsWith("& ([scriptblock]::Create("), "the command follows the platform");
      await page.getByText("Remove Gamma from DeepSeek Harness", { exact: true }).click();
      const removeCommand = page.getByRole("textbox", { name: "DeepSeek Harness remove command", exact: true });
      assert((await removeCommand.inputValue()).endsWith(" -Remove"), "the removal runs the same script");
      await page.getByRole("button", { name: "macOS / Linux", exact: true }).click();
      await until(async () => (await removeCommand.inputValue()).endsWith(" --remove)"), { what: "the removal follows the platform" });
      await page.setViewportSize({ width: 390, height: 844 });
      await command.scrollIntoViewIfNeeded();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "four assistant tabs fit a narrow viewport");
      if (process.env.GAMMA_MCP_SCREENSHOTS) {
        await page.screenshot({ path: path.join(process.env.GAMMA_MCP_SCREENSHOTS, "dsh-setup-mobile.png"), fullPage: true });
      }
      await page.setViewportSize({ width: 1280, height: 860 });
      await user.api(`/api/integrations/tokens/${made.id}`, { method: "DELETE" });
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await user.api(`/api/workspaces/${second.id}`, { method: "DELETE" });
    }
  });

  await step("settings: section tags say where settings live and whether each section's settings are saved", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Appearance").click();
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
      // one change spins only the section holding it: the Enter key sits
      // in Keyboard › Built in, next to the keys it swaps
      await nav(page, "Keyboard").click();
      await until(() => sync("Built in").then((v) => v === "saved"));
      await row(page, "Enter makes").getByRole("button", { name: "New line", exact: true }).click();
      await until(() => sync("Built in").then((v) => v === "syncing"));
      assertEq(await sync("Shortcuts"), "saved");
      await until(() => sync("Built in").then((v) => v === "saved"));
      assertEq((await user.api("/api/prefs/profile")).value?.enterNewBlock, false);
      await nav(page, "Appearance").click();
      assertEq(await sync("Theme"), "saved");
      // the top bar's buttons are an account preference shown under Toolbars
      await nav(page, "Toolbars").click();
      await until(() => sync("Viewer tools").then((v) => v === "saved"));
      await row(page, "Sync pill").getByRole("button", { name: "Every page", exact: true }).click();
      await until(async () => (await user.api("/api/prefs/profile")).value?.topbar?.modes?.sync === "always");
      assertEq(await sync("Viewer tools"), "saved", "the change spins only Top bar");
      await row(page, "Sync pill").getByRole("button", { name: "Synced pages", exact: true }).click();
      await until(async () => (await user.api("/api/prefs/profile")).value?.topbar?.modes?.sync === "auto");
      await nav(page, "Keyboard").click();
      await row(page, "Enter makes").getByRole("button", { name: "New note", exact: true }).click();
      await until(async () => (await user.api("/api/prefs/profile")).value?.enterNewBlock === true);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: Enter moved every account to a new block once: what the old preference holds is not read, and a choice made since stays and syncs", async () => {
    await server.manage("create-user", "enter-mover", "enter-mover-pw");
    const mover = await new Account(server, "enter-mover", "enter-mover-pw").login();
    const profile = () => mover.api("/api/prefs/profile");
    const stored = (page, key) => page.evaluate((key) => localStorage.getItem(key), key);
    const enterMakes = (page, name) => row(page, "Enter makes").getByRole("button", { name, exact: true });
    const keyboard = async (page) => {
      await openSettings(page);
      await nav(page, "Keyboard").click();
    };
    let id;
    const treeIs = (lines, what) => until(async () => same(await tree(mover, id), lines.map((content) => ({ content, children: [] }))), { what });
    // A browser from before: the old key's off, nothing under the new one.
    const contexts = [];
    const oldBrowser = async (account) => {
      const ctx = await (account ? account.context(browser) : browser.newContext());
      contexts.push(ctx);
      await ctx.addInitScript(() => { try { localStorage.setItem("gamma-enter-new-note", "0"); } catch {} });
      const pushed = [];
      ctx.on("request", (r) => { if (r.method() === "PATCH" && r.url().endsWith("/api/prefs/profile")) pushed.push(r.postDataJSON().set); });
      return { ctx, pushed };
    };
    try {
      // An account from before: its first load stored the whole profile, and off is the old entry's.
      const first = await mover.context(browser);
      contexts.push(first);
      await openPage(first, `${server.base}/?ws=${mover.ws}`);
      await until(async () => (await profile()).value?.enterNewBlock === true, { what: "the first load's profile" });
      const { enterNewBlock: _, ...kept } = (await profile()).value;
      await mover.api("/api/prefs/profile", { method: "PUT", body: { value: { ...kept, enterNewNote: false } } });

      // Both old copies of off go unread: Enter makes a block, and the first load adds the new entry once.
      const here = await oldBrowser(mover);
      const pageA = await openPage(here.ctx, `${server.base}/?ws=${mover.ws}`);
      id = await newPageViaUi(pageA, "Enter moved");
      await pageA.keyboard.type("one");
      await pageA.keyboard.press("Enter");
      await pageA.keyboard.type("two");
      await pageA.keyboard.press("Enter");
      await pageA.keyboard.type("three");
      await closeEditor(pageA);
      await treeIs(["one", "two", "three"], "Enter made blocks");
      await until(async () => (await profile()).value?.enterNewBlock === true, { what: "the new entry pushed" });
      assertEq((await profile()).value.enterNewNote, false, "the old entry stays in the profile, unread");
      assertEq(JSON.stringify(here.pushed.filter((set) => "enterNewBlock" in set).map((set) => set.enterNewBlock)), "[true]", "the first load pushed the new entry once");
      await keyboard(pageA);
      assertEq(await enterMakes(pageA, "New note").getAttribute("aria-pressed"), "true", "Settings shows the move");

      // New line chosen here survives a reload.
      await enterMakes(pageA, "New line").click();
      await until(async () => (await profile()).value?.enterNewBlock === false, { what: "the choice saved" });
      await pageA.reload();
      await editRow(pageA, "one");
      await pageA.keyboard.press("Enter");
      await pageA.keyboard.type("line");
      await closeEditor(pageA);
      await treeIs(["one\nline", "two", "three"], "Enter broke the line after a reload");
      assertEq((await profile()).value.enterNewBlock, false);
      await keyboard(pageA);
      assertEq(await enterMakes(pageA, "New line").getAttribute("aria-pressed"), "true", "the choice shows after a reload");

      // A second old browser takes the choice from the server and writes nothing back.
      const { updated_at: chosenAt } = await profile();
      const there = await oldBrowser(mover);
      const pageB = await openPage(there.ctx, pageA.url());
      await until(async () => (await stored(pageB, "gamma-enter-new-block")) === "0", { what: "the server's choice replaced the default" });
      await editRow(pageB, "two");
      await pageB.keyboard.press("Enter");
      await pageB.keyboard.type("line");
      await closeEditor(pageB);
      await treeIs(["one\nline", "two\nline", "three"], "Enter broke the line in the other browser");
      await sleep(1500); // past a push's settling delay
      const after = await profile();
      assertEq(after.value.enterNewBlock, false, "nothing wrote the default back");
      assertEq(after.updated_at, chosenAt, "the profile was not written");
      assertEq(there.pushed.length, 0, "the other browser pushed nothing");

      // New note chosen again reaches it on a pull (a wake pulls at most every 15 s).
      await enterMakes(pageA, "New note").click();
      await until(async () => (await profile()).value?.enterNewBlock === true, { what: "the second choice saved" });
      await until(async () => {
        await pageB.evaluate(() => window.dispatchEvent(new Event("focus")));
        return (await stored(pageB, "gamma-enter-new-block")) === "1";
      }, { timeout: 20000, every: 500, what: "the other browser's pull" });
      await editRow(pageB, "three");
      await pageB.keyboard.press("Enter");
      await pageB.keyboard.type("four");
      await closeEditor(pageB);
      await treeIs(["one\nline", "two\nline", "three", "four"], "Enter made a block in the other browser");
      assertEq(await stored(pageB, "gamma-enter-new-note"), "0", "the old key stays where it was");
      assertNoProblems(pageA);
      assertNoProblems(pageB);

      // A guest's preferences live in its browser alone: the new key decides there too.
      const guest = await oldBrowser();
      const pageG = await openPage(guest.ctx, `${server.base}/`);
      await pageG.click(".loginGuestBtn");
      await newPageViaUi(pageG, "Guest page");
      await pageG.keyboard.type("first");
      await pageG.keyboard.press("Enter");
      await pageG.keyboard.type("second");
      await closeEditor(pageG);
      await pageG.locator(".blockRow", { hasText: "second" }).waitFor();
      assertEq(await pageG.locator(".blockRow", { hasText: "first" }).filter({ hasText: "second" }).count(), 0, "a guest's Enter makes a block");
      await keyboard(pageG);
      assertEq(await enterMakes(pageG, "New note").getAttribute("aria-pressed"), "true", "a guest's Settings shows New note");
      assertEq(guest.pushed.length, 0, "a guest pushes no profile");
      assertNoProblems(pageG);
    } finally {
      for (const ctx of contexts) await ctx.close();
    }
  });

  await step("settings: the top bar's buttons hide into the account menu, open their panels from it, and come back with Reset", async () => {
    const { ctx, page } = await setup();
    try {
      const topbar = () => user.api("/api/prefs/profile").then((r) => r.value?.topbar?.modes);
      const onBar = (id) => page.locator(`.topbar [data-guide="${id}"]`).count();
      const menu = page.locator('[data-guide="account.menu"]');
      const openMenu = () => page.locator('[data-guide="header.account"]').click();
      for (const id of ["header.add", "header.tasks", "header.search", "header.inbox", "header.view"]) assertEq(await onBar(id), 1, `${id} starts on the bar`);
      await openSettings(page);
      await nav(page, "Toolbars").click();
      for (const label of ["Background tasks", "Search", "View menu"]) {
        await row(page, label).getByRole("button", { name: "Hidden", exact: true }).click();
      }
      // nothing unread: an Inbox that shows only then is off the bar too
      await row(page, "Inbox").getByRole("button", { name: "When unread", exact: true }).click();
      await until(async () => {
        const value = await topbar();
        return value?.tasks === "hidden" && value.search === "hidden" && value.view === "hidden" && value.inbox === "auto";
      });
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      for (const id of ["header.tasks", "header.search", "header.inbox", "header.view"]) assertEq(await onBar(id), 0, `${id} is off the bar`);
      assertEq(await onBar("header.add"), 1, "Add stays");
      // each is a row of the account menu that opens its own panel
      await openMenu();
      await menu.getByRole("menuitem", { name: "Background tasks", exact: true }).click();
      await page.locator('[data-guide="tasks.panel"]').waitFor();
      await openMenu();
      await menu.locator('[data-command="app.search"]').click();
      await page.locator(".searchPopover .searchInput").waitFor();
      await openMenu();
      await menu.getByRole("menuitem", { name: "Inbox", exact: true }).click();
      await page.locator(".inboxPopover").waitFor();
      assertEq(await onBar("header.inbox"), 1, "an open panel keeps its button while it shows");
      // the View menu's rows join the account menu
      await openMenu();
      await menu.getByRole("menuitem", { name: "Import…", exact: true }).waitFor();
      // Reset: every button back
      await menu.getByRole("menuitem", { name: "Settings…", exact: true }).click();
      await nav(page, "Toolbars").click();
      await page.locator('.settingsPane .setSection[data-setting="Top bar"]').getByRole("button", { name: "Reset", exact: true }).click();
      await until(async () => (await topbar())?.tasks === "always");
      assertEq(JSON.stringify(await topbar()), JSON.stringify(TOPBAR.DEFAULT.modes));
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      for (const id of ["header.tasks", "header.search", "header.inbox", "header.view"]) assertEq(await onBar(id), 1, `${id} is back on the bar`);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // A bar's rows in Toolbars: the list after its section's header, a row's
  // grip, and the ids in the order the rows show.
  const barList = (page, title) => page.locator(`.settingsPane .setSection[data-setting="${title}"] + .barLayoutList`);
  const grip = (list, id) => list.locator(`.barLayoutRow[data-order-id="${id}"] .barLayoutGrip`);
  const listed = (list) => list.locator(".barLayoutRow").evaluateAll((els) => els.map((el) => el.dataset.orderId));

  await step("settings: a top bar button's grip drags it into place, the bar follows through a reload, an arrow key moves a row, and Reset puts the order back", async () => {
    const { ctx, page } = await setup();
    try {
      const topbar = () => user.api("/api/prefs/profile").then((r) => r.value?.topbar);
      // the configurable buttons in the header's own order, Account last
      const header = () => page.locator('.topbar [data-guide^="header."]').evaluateAll((els) => els.map((el) => el.dataset.guide.slice(7))
        .filter((id) => ["add", "tasks", "search", "view", "account"].includes(id)).join());
      assertEq(await header(), "add,tasks,search,view,account");
      await openSettings(page);
      await nav(page, "Toolbars").click();
      const list = barList(page, "Top bar");
      // a real drag: Search's grip pressed and moved over the top half of Add's row
      await grip(list, "search").scrollIntoViewIfNeeded();
      const from = await grip(list, "search").boundingBox();
      const add = await list.locator('.barLayoutRow[data-order-id="add"]').boundingBox();
      await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
      await page.mouse.down();
      await page.mouse.move(from.x + from.width / 2, add.y + add.height / 4, { steps: 12 });
      await page.locator(".dragGhost.barLayoutGhost").waitFor();
      const bar = await list.locator(".dropBar").boundingBox();
      assert(Math.abs(bar.y + 1 - add.y) <= 2, `the drop bar marks Add's top edge: bar ${JSON.stringify(bar)}, row ${JSON.stringify(add)}`);
      assertEq(await list.locator('.barLayoutRow[data-order-id="search"].lifted').count(), 1, "the dragged row is lifted");
      await page.mouse.up();
      await until(async () => (await topbar())?.order?.[0] === "search", { what: "Search first in the stored order" });
      assertEq((await listed(list)).slice(0, 3).join(), "search,add,tasks");
      assertEq(await page.locator(".dragGhost").count(), 0, "the ghost is gone after the drop");
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await until(async () => (await header()) === "search,add,tasks,view,account", { what: "the header in the new order" });
      await page.reload();
      await page.waitForSelector(".folderNewBtn");
      await until(async () => (await header()) === "search,add,tasks,view,account", { what: "the order kept through a reload" });
      // the keyboard: ArrowDown on Add's grip moves it one place, says where, and keeps the focus
      await openSettings(page);
      await nav(page, "Toolbars").click();
      await grip(list, "add").focus();
      await page.keyboard.press("ArrowDown");
      await until(async () => (await listed(list)).slice(0, 3).join() === "search,tasks,add", { what: "Add one row down" });
      const said = await list.locator("xpath=following-sibling::*[contains(@class, 'srOnly')][1]").innerText();
      assert(/^Add: 3 of \d+$/.test(said), `the live region says the new place: ${said}`);
      assertEq(await grip(list, "add").evaluate((el) => el === document.activeElement), true, "the focus stays on the moved row's grip");
      await until(async () => (await topbar())?.order?.slice(0, 3).join() === "search,tasks,add", { what: "the key's move stored" });
      // a drag that ends left of the grip and just below the last row still
      // lands, last; off the pane the drop bar goes
      const rows = await listed(list);
      await list.locator(`.barLayoutRow[data-order-id="${rows.at(-1)}"]`).scrollIntoViewIfNeeded();
      const lastRow = await list.locator(`.barLayoutRow[data-order-id="${rows.at(-1)}"]`).boundingBox();
      const start = await grip(list, "search").boundingBox();
      const pane = await page.locator(".settingsPane").boundingBox();
      await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
      await page.mouse.down();
      await page.mouse.move(pane.x - 20, lastRow.y + lastRow.height / 2, { steps: 12 });
      await page.locator(".dragGhost.barLayoutGhost").waitFor();
      assertEq(await list.locator(".dropBar").count(), 0, "no drop bar where a release would not land");
      await page.mouse.move(start.x - 12, lastRow.y + lastRow.height + 8, { steps: 6 });
      const endBar = await list.locator(".dropBar").boundingBox();
      assert(endBar && Math.abs(endBar.y + 1 - (lastRow.y + lastRow.height)) <= 2, `the drop bar marks the last row's bottom: ${JSON.stringify(endBar)}`);
      await page.mouse.up();
      await until(async () => (await listed(list)).at(-1) === "search", { what: "Search last in the list" });
      await until(async () => {
        const order = (await topbar())?.order || [];
        return order.indexOf("search") > order.indexOf(rows.at(-1));
      }, { what: "Search after the last row in the stored order" });
      // Reset: the default order and modes, and nothing left to reset
      const reset = page.locator('.settingsPane .setSection[data-setting="Top bar"]').getByRole("button", { name: "Reset", exact: true });
      await reset.click();
      await until(async () => JSON.stringify(await topbar()) === JSON.stringify(TOPBAR.DEFAULT), { what: "the default layout stored" });
      assertEq(await reset.isDisabled(), true, "Reset is off at the default");
      assertEq((await listed(list)).slice(0, 3).join(), "add,tasks,search");
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await until(async () => (await header()) === "add,tasks,search,view,account", { what: "the header back in the default order" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: a right-click on the top bar's empty space or one of its items offers Customize top bar…, which opens its section", async () => {
    const { ctx, page } = await setup();
    try {
      // a point of the bar that is none of its buttons, tabs or fields
      const spot = await page.evaluate(() => {
        const bar = document.querySelector(".topbar").getBoundingClientRect();
        const y = bar.top + bar.height / 2;
        for (let x = bar.left + bar.width / 2; x < bar.right; x += 4) {
          const el = document.elementFromPoint(x, y);
          if (el?.closest(".topbar") && !el.closest("button, a, input, [role='tab'], [data-popover]")) return { x, y };
        }
        return null;
      });
      assert(spot, "the top bar has empty space");
      await page.mouse.click(spot.x, spot.y, { button: "right" });
      await page.getByRole("menuitem", { name: "Customize top bar…", exact: true }).click();
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
      await until(() => page.evaluate(() => document.activeElement?.dataset.setting === "Top bar"), { what: "the Top bar section focused" });
      assertEq(await nav(page, "Toolbars").getAttribute("aria-current"), "page");
      // a right-click on a button that is not one of the bar's items is that button's, not the bar's
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await page.locator('[data-guide="header.home"]').click({ button: "right" });
      await sleep(300);
      assertEq(await page.getByRole("menuitem", { name: "Customize top bar…", exact: true }).count(), 0, "no Customize… on Home");
      // on one of its items, the bar's menu, first with where the item stands while pages are side by side
      await page.locator('[data-guide="header.add"]').click({ button: "right" });
      await page.getByRole("menuitem", { name: "Customize top bar…", exact: true }).waitFor();
      assertEq(await page.getByRole("menuitemradio", { name: "Show on the top bar" }).getAttribute("aria-checked"), "true", "Add stands on top by default");
      assertEq(await page.getByRole("menuitemradio", { name: "Show in each pane" }).getAttribute("aria-checked"), "false");
      await page.keyboard.press("Escape");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: the page header's Export button, before the trash can, exports the open page, and Toolbars › Page header turns it off and on", async () => {
    const made = await user.api("/api/pages", { method: "POST", body: { title: "Header export" } });
    await user.api("/api/blocks", { method: "POST", body: { parent_id: made.id, content: "a note to export" } });
    const ctx = await user.context(browser);
    try {
      const page = await openPage(ctx, `${server.base}/?page=${made.id}&ws=${user.ws}`);
      const button = page.locator(".pageActionCol .pageExportBtn");
      await button.waitFor();
      assert(await button.evaluate((el) => el.nextElementSibling?.classList.contains("pageDeleteBtn")), "Export stands right before the trash can");
      await button.click();
      // the page's dialog, not a folder's ("Export “folder”")
      const dialog = page.getByRole("dialog", { name: "Export", exact: true });
      await dialog.getByRole("button", { name: "Markdown", exact: true }).click();
      await dialog.getByRole("button", { name: "Next", exact: true }).click();
      const started = page.waitForRequest((r) => r.url().includes("/api/jobs/export") && r.method() === "POST");
      const download = page.waitForEvent("download", { timeout: 15000 });
      await dialog.getByRole("button", { name: "Export", exact: true }).click();
      assert((await started).postData().includes(made.id), "the job exports the open page");
      await download;
      await dialog.getByRole("button", { name: "Done", exact: true }).click();
      await dialog.waitFor({ state: "detached" });
      const shown = () => user.api("/api/prefs/profile").then((r) => r.value?.pageExportButton);
      const toggle = (on) => row(page, "Export button").getByRole("checkbox")[on ? "check" : "uncheck"]();
      await openSettings(page);
      await nav(page, "Toolbars").click();
      await toggle(false);
      await until(async () => (await shown()) === false, { what: "off, stored with the account" });
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await button.waitFor({ state: "detached" });
      await page.locator(".pageActionCol .pageDeleteBtn").waitFor();
      await openSettings(page);
      await nav(page, "Toolbars").click();
      await toggle(true);
      await until(async () => (await shown()) === true, { what: "on again, stored" });
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await button.waitFor();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: a viewer tool hidden in Settings is a row of the strip's ⋯ menu, whose Customize… opens Viewer tools; the strip follows the order in the PDF and the notebook viewer", async () => {
    const up = await user.upload("/api/uploads", makePdf([["Viewer tools", "a page to read"]]), "tools.pdf", "application/pdf");
    const doc = await user.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Viewer tools", source_url: up.source_url } });
    const notebook = await user.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Tools notebook" } });
    const paper = { width: 595.28, height: 841.89, color: "#ffffff", pattern: "blank", spacing: 24, line: "#c8d1dc" };
    await user.api("/api/blocks", { method: "POST", body: { parent_id: notebook.id, content: "", properties: { sheet: paper, collapsed: true } } });
    const ctx = await user.context(browser);
    try {
      const tools = () => user.api("/api/prefs/profile").then((r) => r.value?.viewerTools);
      // the strip's children in order: each tool by its anchor, the ⋯ by its name
      const strip = (page) => page.locator('[data-guide="viewer.tools"] > *').evaluateAll((els) => els
        .map((el) => el.dataset.guide || el.getAttribute("aria-label")).filter(Boolean).join());
      const page = await openPage(ctx, `${server.base}/?page=${doc.id}&ws=${user.ws}`);
      await page.waitForSelector('[data-page="1"] .textLayer span', { timeout: 30000 });
      assertEq(await strip(page), "viewer.zoom,pdf.inkButton,viewer.record,viewer.highlighter,viewer.translate", "the default strip, no ⋯");
      await openSettings(page);
      await nav(page, "Toolbars").click();
      const list = barList(page, "Viewer tools");
      await list.locator('.barLayoutRow[data-order-id="record"]').getByRole("button", { name: "Hidden", exact: true }).click();
      // Home on Translate's grip: first
      await grip(list, "translate").focus();
      await page.keyboard.press("Home");
      await until(async () => {
        const value = await tools();
        return value?.modes?.record === "hidden" && value.order[0] === "translate";
      }, { what: "Record hidden and Translate first, stored" });
      assertEq(await list.locator('.barLayoutRow[data-order-id="record"].off').count(), 1, "a hidden tool's row reads muted");
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await until(async () => (await strip(page)) === "viewer.translate,viewer.zoom,pdf.inkButton,viewer.highlighter,More tools", { what: "the strip in the new order, Record in ⋯" });
      await page.getByRole("button", { name: "More tools", exact: true }).click();
      const menu = page.getByRole("menu", { name: "More tools" });
      await menu.waitFor();
      const rows = await menu.getByRole("menuitem").evaluateAll((els) => els.map((el) => el.textContent.trim()));
      assertEq(rows.join(" | "), "Record audio | Customize…", "the ⋯ menu: the hidden tool, then the way to its setting");
      // the ⋯'s own press is not the strip's: a right-click on it offers no Customize viewer tools…
      await page.getByRole("button", { name: "More tools", exact: true }).click({ button: "right" });
      await sleep(300);
      assertEq(await page.getByRole("menuitem", { name: "Customize viewer tools…", exact: true }).count(), 0, "no strip menu on the ⋯");
      await menu.getByRole("menuitem", { name: "Customize…", exact: true }).click();
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
      await until(() => page.evaluate(() => document.activeElement?.dataset.setting === "Viewer tools"), { what: "the Viewer tools section focused" });
      assertEq(await nav(page, "Toolbars").getAttribute("aria-current"), "page");
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      // the notebook viewer: the same order, less what only a PDF has
      await page.goto(`${server.base}/?page=${notebook.id}&ws=${user.ws}`);
      await page.locator(".noteSheetBar button[aria-label='Notebook view']").first().click();
      await page.waitForSelector(".nbViewer .nbSheet", { timeout: 15000 });
      await until(async () => (await strip(page)) === "viewer.zoom,pdf.inkButton,viewer.paper,viewer.notesView,More tools", { what: "the notebook's strip" });
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await user.api("/api/prefs/profile", { method: "PATCH", body: { set: { viewerTools: VIEWER_TOOLS.DEFAULT } } });
    }
  });

  await step("settings: navigation, search, scoped management, and preferences survive reload", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      assertEq(await nav(page, "Account & sync").getAttribute("aria-current"), "page");
      await nav(page, "Appearance").click();
      for (const [label, theme, scheme] of [["Gamma Light", "gamma-light", "light"], ["Gamma Dark", "gamma-dark", "dark"]]) {
        await page.getByRole("button", { name: label, exact: true }).click();
        await until(() => page.locator("html").getAttribute("data-theme").then((v) => v === theme));
        assertEq(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme), scheme);
        await until(async () => (await user.api("/api/prefs/profile")).value?.theme === theme);
        await page.reload();
        await page.waitForSelector(".folderNewBtn");
        assertEq(await page.locator("html").getAttribute("data-theme"), theme);
        await openSettings(page);
        await nav(page, "Appearance").click();
        assertEq(await page.getByRole("button", { name: label, exact: true }).getAttribute("aria-pressed"), "true");
        if (flags.keep) await page.screenshot({ path: `${server.dir}/settings-${theme}.png`, animations: "disabled" });
      }
      await page.getByRole("button", { name: "Sepia", exact: true }).click();
      await until(() => page.locator("html").getAttribute("data-theme").then((v) => v === "sepia"));
      const sepiaText = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--text-primary").trim());
      await page.getByRole("button", { name: "Solarized Light", exact: true }).click();
      await until(() => page.locator("html").getAttribute("data-theme").then((v) => v === "solarized"));
      const solarizedText = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--text-primary").trim());
      assert(sepiaText && solarizedText && sepiaText !== solarizedText, `each theme sets its own text colour (${sepiaText}, ${solarizedText})`);
      await until(async () => (await user.api("/api/prefs/profile")).value?.theme === "solarized");
      await page.reload();
      await page.waitForSelector(".folderNewBtn");
      await until(() => page.locator("html").getAttribute("data-theme").then((v) => v === "solarized"));
      await openSettings(page);
      await nav(page, "Appearance").click();
      const themes = page.getByRole("group", { name: "Theme", exact: true });
      assertEq(await themes.locator('[aria-pressed="true"]').count(), 1);
      await page.getByRole("checkbox", { name: "Dark PDF pages", exact: true }).check();
      await until(() => user.api("/api/prefs/profile").then((v) => v.value?.pdfDarkPage === true));
      assert(await page.locator(".appearancePdfPreview.isDark").isVisible());
      await page.getByRole("checkbox", { name: "Dark PDF pages", exact: true }).uncheck();
      await until(() => user.api("/api/prefs/profile").then((v) => v.value?.pdfDarkPage === false));
      // Colors: groups of swatches; a swatch opens an editor (colour, opacity, Remove); groups are added and
      // deleted; each picker chooses groups; Reset puts the defaults back
      const group = (name) => page.locator(`.paletteGroup[data-setting="${name}"]`);
      const swatches = group("Highlight").locator(".paletteSwatch:not(.paletteAdd)");
      assertEq(await swatches.count(), 4);
      assertEq(await group("Ink highlight").locator(".paletteSwatch:not(.paletteAdd)").count(), 8);
      assertEq(await group("Pen").locator(".paletteSwatch:not(.paletteAdd)").count(), 14);
      const palette = async () => (await user.api("/api/prefs/profile")).value?.palette;
      const reset = page.getByRole("button", { name: "Reset colors", exact: true });
      assert(await reset.isDisabled(), "nothing to reset at the defaults");
      await swatches.first().click();
      const editor = group("Highlight").locator(".paletteEditor");
      // the picker: a hex field (#rrggbbaa sets the opacity too), an opacity field, the hue bar
      const hexField = editor.getByRole("textbox", { name: "Hex color", exact: true });
      await hexField.fill("#ff8800");
      await until(async () => (await palette())?.groups?.[0]?.colors?.[0]?.hex === "#ff8800");
      assertEq(await swatches.first().getAttribute("aria-label"), "Amber, 65% opaque", "named after the nearest colour, at its alpha");
      await editor.getByRole("spinbutton", { name: "Opacity", exact: true }).fill("40");
      await until(async () => (await palette())?.groups?.[0]?.colors?.[0]?.alpha === 0.4);
      assertEq(await swatches.first().evaluate((el) => getComputedStyle(el).backgroundColor), "rgba(255, 136, 0, 0.4)");
      await hexField.fill("#00ff0080");
      await until(async () => { const c = (await palette())?.groups?.[0]?.colors?.[0]; return c?.hex === "#00ff00" && c?.alpha === 0.5; });
      const hue = editor.getByRole("slider", { name: "Hue", exact: true });
      const box = await hue.boundingBox();
      await page.mouse.click(box.x + 1, box.y + box.height / 2); // the left edge of the hue bar is red (a pixel in: nearly)
      await until(async () => /^#ff0[0-9a-f]00$/.test((await palette())?.groups?.[0]?.colors?.[0]?.hex || ""), { what: "the hue bar's left edge, red" });
      assertEq((await palette()).groups[0].colors[0].alpha, 0.5, "a hue change keeps the opacity");
      assertEq((await palette()).groups[0].colors[1].hex, "#aaebaa", "the other swatches stay");
      await group("Highlight").getByRole("button", { name: "Add color to Highlight", exact: true }).click();
      await until(async () => (await palette())?.groups?.[0]?.colors?.length === 5);
      assertEq(await swatches.count(), 5);
      await editor.getByRole("button", { name: "Remove", exact: true }).click();
      await until(async () => (await palette())?.groups?.[0]?.colors?.length === 4);
      await page.getByRole("button", { name: "Add group", exact: true }).click();
      await until(async () => (await palette())?.groups?.length === 4);
      // a name is typed as typed, its space too, and saved as the field is left
      const name = group("New group").getByRole("textbox", { name: "Group name", exact: true });
      await name.fill("");
      await name.pressSequentially("Deep work");
      assertEq(await name.inputValue(), "Deep work", "the space before the next word stays");
      await name.press("Enter");
      await until(async () => (await palette())?.groups?.[3]?.name === "Deep work", { what: "the name saved on Enter" });
      // each picker row is a dropdown of the groups; the new one becomes the text highlights' group
      const pickerRow = row(page, "Text highlights");
      assertEq(await pickerRow.getByRole("button", { name: "Text highlights", exact: true }).innerText(), "Highlight");
      await pickerRow.getByRole("button", { name: "Text highlights", exact: true }).click();
      await page.locator(".uiSelectMenu").getByRole("menuitemradio", { name: "Deep work", exact: true }).click();
      await until(async () => { const p = await palette(); return p?.pickers?.highlight === p?.groups?.[3]?.id; });
      assertEq(await pickerRow.getByRole("button", { name: "Text highlights", exact: true }).innerText(), "Deep work");
      // deleting the chosen group sends the picker back to its default group
      await group("Deep work").getByRole("button", { name: "Delete group Deep work", exact: true }).click();
      await until(async () => (await palette())?.groups?.length === 3 && (await palette())?.pickers?.highlight === "highlight");
      await reset.click();
      await until(async () => (await palette())?.groups?.[0]?.colors?.[0]?.hex === "#ffe28f");
      assert(await reset.isDisabled());
      await row(page, "Interface size").getByRole("button", { name: "Larger", exact: true }).click();
      assert((await row(page, "Interface size").innerText()).includes("110%"));
      await row(page, "Interface size").getByRole("button", { name: "Reset", exact: true }).click();
      if (flags.keep) await page.screenshot({ path: `${server.dir}/settings-appearance.png`, animations: "disabled" });
      await nav(page, "Help & diagnostics").click();
      await nav(page, "Appearance").click();
      if (flags.keep) await page.screenshot({ path: `${server.dir}/settings-library.png`, animations: "disabled" });
      await page.getByRole("checkbox", { name: "Labels", exact: true }).uncheck();
      await page.getByRole("checkbox", { name: "Thumbnails", exact: true }).uncheck();
      assertEq(await page.locator(".libraryDisplayCard img").count(), 0);
      assertEq(await page.locator(".libraryDisplayCard .labelTagBadge").count(), 0);
      assertEq(await page.locator(".libraryDisplayCard .folderTagBadge").count(), 1);
      await search(page, "translation concurrency", "Parallel requests");
      // 7 is past the cap (TRANSLATE_PARALLEL_MAX): it is stored as 4
      await row(page, "Parallel requests").locator("input").fill("7");
      await row(page, "Parallel requests").locator("input").press("Tab");
      await until(() => page.evaluate(() => localStorage.getItem("gamma-translate-parallel")).then((v) => v === "4"));
      // An account preference reaches the account's profile; a device one never does.
      await until(() => user.api("/api/prefs/profile").then((v) => v.value?.translateParallel === 4));
      assert(!("uiScale" in (await user.api("/api/prefs/profile")).value), "interface size stays with the browser");
      // A fresh browser signed in to the same account picks the profile up.
      const other = await user.context(browser);
      try {
        const fresh = await openPage(other, server.base);
        await until(() => fresh.evaluate(() => localStorage.getItem("gamma-translate-parallel")).then((v) => v === "4"));
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
      await nav(page, "Appearance").click();
      assertEq(await page.getByRole("button", { name: "Solarized Light", exact: true }).getAttribute("aria-pressed"), "true");
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
      // Text selected with a drag let go past the dialog's edge is no click
      // on the backdrop (press.js backdropPress); a real click there asks.
      // (Its edge is read once it has grown in.)
      await arrived(dialog);
      const keep = await dialog.getByRole("spinbutton", { name: "Keep for", exact: true }).boundingBox();
      const edge = await dialog.boundingBox();
      await page.mouse.move(keep.x + keep.width / 2, keep.y + keep.height / 2);
      await page.mouse.down();
      await page.mouse.move(edge.x - 20, keep.y + keep.height / 2, { steps: 6 });
      await page.mouse.up();
      const unsaved = dialog.getByText("Discard your unsaved edits?", { exact: true });
      await sleep(150);
      assertEq(await unsaved.count(), 0, "a drag out of the dialog does not close it");
      await page.mouse.click(edge.x - 20, edge.y + 40);
      await unsaved.waitFor();
      await dialog.getByRole("button", { name: "Keep editing", exact: true }).click();
      await dialog.getByRole("button", { name: "Frequency", exact: true }).click();
      await page.getByRole("menuitemradio", { name: "Daily", exact: true }).click();
      await dialog.locator("ol li").first().waitFor(); // the schedule preview answered
      await dialog.getByRole("button", { name: "Create task", exact: true }).click();
      await dialog.waitFor({ state: "detached" });
      const rowOf = page.getByRole("region", { name: "Periodic backup tasks" }).locator("tr", { hasText: "Nightly" });
      await rowOf.waitFor();
      assert((await rowOf.innerText()).includes("Daily · 03:00"), "the row shows the daily schedule");
      await rowOf.getByRole("button", { name: "Actions for Nightly", exact: true }).click();
      await page.getByRole("menuitem", { name: "Run now", exact: true }).click();
      // Run now wakes the scheduler at once; the table polls every 5 s
      await until(() => rowOf.innerText().then((t) => /finished|failed/i.test(t)), { timeout: 20000, what: "the queued run to finish" });
      assert(/finished/i.test(await rowOf.innerText()), "the run finished");
      // The Last run cell: the state, when it ran, what it made.
      await until(() => rowOf.innerText().then((t) => /Finished\s+\w{3} \d+, \d+:\d+.*\s+\d+ snapshots? · [\d.]+ [KMG]?B/.test(t)),
        { what: "the Last run cell's time and result" });
      await rowOf.getByRole("button", { name: "Actions for Nightly", exact: true }).click();
      await page.getByRole("menuitem", { name: "Delete task", exact: true }).click();
      await page.locator(".confirmHead").waitFor();
      await page.locator(".reportModalBtns button", { hasText: "Delete task" }).click();
      await rowOf.waitFor({ state: "detached" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // An account keeps at most five tasks (backup_schedule.MAX_TASKS): at five,
  // Add task and Duplicate are disabled and a line under the table says why.
  // On a short window the pane scrolls, and the table keeps its full height
  // rather than shrinking to its border.
  await step("settings: at five backup tasks, Add task is disabled and says why", async () => {
    const made = [];
    for (let i = 1; i <= 5; i++) {
      made.push(await user.api("/api/backup-tasks", { method: "POST", body: { name: `Task ${i}`, scope: "all_owned", cron: "0 3 * * *" } }));
    }
    const { ctx, page } = await setup({ width: 1280, height: 600 });
    try {
      await openSettings(page);
      await nav(page, "Backups").click();
      const add = page.getByRole("button", { name: "Add task", exact: true });
      const table = page.getByRole("region", { name: "Periodic backup tasks" });
      await table.locator("tr", { hasText: "Task 5" }).waitFor();
      const fit = await table.evaluate((wrap) => {
        const pane = wrap.closest(".settingsPane");
        return { overflows: pane.scrollHeight > pane.clientHeight, clipped: wrap.scrollHeight - wrap.clientHeight };
      });
      assert(fit.clipped <= 1, `the task table is not clipped (${fit.clipped}px hidden)`);
      assert(fit.overflows, "the Backups pane scrolls on a short window"); // (else the check above proves nothing)
      assert(await add.isDisabled(), "Add task is disabled at the limit");
      await page.getByText("You can keep up to 5 backup tasks. Delete one to add another.").waitFor();
      const actions = page.getByRole("button", { name: "Actions for Task 1", exact: true });
      await actions.click();
      const duplicate = page.getByRole("menuitem", { name: "Duplicate task", exact: true });
      assert(await duplicate.isDisabled(), "Duplicate too");
      await actions.click(); // (Escape would close Settings)
      await duplicate.waitFor({ state: "detached" });
      await user.api(`/api/backup-tasks/${made[0].id}`, { method: "DELETE" });
      await until(async () => !(await add.isDisabled()), { what: "a deleted task frees a place", timeout: 15000 });
      assertNoProblems(page);
    } finally {
      await ctx.close();
      for (const task of made.slice(1)) await user.api(`/api/backup-tasks/${task.id}`, { method: "DELETE" });
    }
  });

  // A replace restore first keeps the workspace as it is (ws_backup's
  // automatic pre-restore snapshot); restoring a workspace other than the
  // open one reloads its list in place, where that snapshot shows its tag.
  await step("settings: a replace restore lists the state before it as a \"Before restore\" snapshot", async () => {
    const lab = await user.api("/api/workspaces", { method: "POST", body: { name: "Restore lab" } });
    await user.api(`/api/workspaces/${lab.id}/backups`, { method: "POST", body: { label: "e2e-restore", uploads: false } });
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Backups").click();
      const snapshot = page.locator(".aiProvRow", { hasText: "e2e-restore" });
      await snapshot.waitFor();
      const beforeRestore = page.locator(".aiProvRow").filter({ has: page.locator(".uiTag", { hasText: "Before restore" }) });
      assertEq(await beforeRestore.count(), 0, "no pre-restore snapshot yet");
      await snapshot.getByRole("button", { name: "Restore", exact: true }).click();
      await page.getByRole("menuitem", { name: "Replace…", exact: true }).click();
      await page.locator(".confirmModal").getByRole("button", { name: "Replace", exact: true }).click();
      await beforeRestore.waitFor({ timeout: 15000 });
      assertEq(await beforeRestore.count(), 1, "one pre-restore snapshot");
      const kept = (await user.api(`/api/workspaces/${lab.id}/backups`)).backups.filter((b) => b.auto);
      assertEq(kept.length, 1, "the server keeps it as an automatic snapshot");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // Settings → AI → Local agents is offered only with a section to show
  // (chat/agents.js showsLocalAgents): this server runs no agents and no
  // computer of the account's is shown to it, so the rail has none. A link
  // to the pane opens Models instead, and a search for Codex finds
  // Integrations, where Codex reads the library from its terminal.
  await step("settings: with no computer's agents to list, the rail has no Local agents: a link to it opens Models, and a search for Codex finds Integrations", async () => {
    const ctx = await user.context(browser);
    try {
      await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
      const page = await openPage(ctx, `${server.base}/?settings=agents`);
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
      await until(async () => (await nav(page, "Models").getAttribute("aria-current")) === "page", { what: "the link lands on Models" });
      await row(page, "Models for each task").waitFor();
      assertEq(await nav(page, "Local agents").count(), 0, "no Local agents in the rail");
      await page.getByRole("searchbox", { name: "Search settings" }).fill("codex");
      const found = page.locator(".settingsSearchResult");
      await found.first().waitFor();
      assertEq(await found.first().locator(".settingLabel").innerText(), "Integrations", "Codex leads to Integrations");
      assertEq(await found.filter({ hasText: "Local agents" }).count(), 0, "and to no hidden pane");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // With a computer of the account's shown to the server (GET
  // /api/computers faked: no computer announces itself here), Local agents
  // leads the AI group (SettingsDialog.jsx AI_NAV) and lists it: an admin's
  // twenty-one panes fit a full-height dialog (a 900 px window: the dialog's
  // 800 px cap), and a shorter window scrolls the groups alone
  // (.settingsNavList), the account card and Help in view.
  const labPc = { id: "lab-pc", name: "Lab PC", online: true,
    assistants: [{ name: "codex", label: "Codex", found: true, ready: true, enabled: true, version: "1.0.0" }] };
  await step("settings: with a computer shown to the server, an admin's rail leads the AI group with Local agents, fits a full-height dialog, and on a short window scrolls its groups alone", async () => {
    await server.manage("set-admin", "settings-user", "on");
    try {
      for (const height of [900, 640]) {
        const { ctx, page } = await setup({ width: 1280, height }, (context) => context.route((url) => url.pathname === "/api/computers",
          (route) => route.fulfill({ json: { computers: [labPc] } })));
        try {
          await openSettings(page);
          const rail = page.getByRole("navigation", { name: "Settings categories" });
          await nav(page, "Local agents").waitFor();
          const panes = await rail.locator(".settingsNavBtn").allInnerTexts();
          assertEq(panes.slice(panes.indexOf("Local agents"), panes.indexOf("Local agents") + 3).join(" › "), "Local agents › Models › Usage",
            "Local agents first in the AI group");
          const fit = await rail.evaluate((nav) => {
            const list = nav.querySelector(".settingsNavList");
            const help = nav.querySelector(".settingsNavBottom").getBoundingClientRect();
            return { over: list.scrollHeight - list.clientHeight, railOver: nav.scrollHeight - nav.clientHeight,
              helpIn: help.bottom <= nav.getBoundingClientRect().bottom + 1 };
          });
          assert(fit.railOver <= 1 && fit.helpIn, `the rail itself never scrolls and Help stays in view (${JSON.stringify(fit)})`);
          if (height === 900) assert(fit.over <= 1, `twenty-one panes fit the full-height rail (${fit.over}px over)`);
          else assert(fit.over > 1, "a short window scrolls the groups");
          await nav(page, "Local agents").click();
          await row(page, "Lab PC").waitFor();
          assertEq((await row(page, "Codex").locator(".uiTag").innerText()).trim(), "On", "the computer's agent as it announced itself");
          assertEq(await page.locator(".settingsPane .settingsPaneTitle").innerText(), "Local agents", "its own pane");
          assertNoProblems(page);
        } finally { await ctx.close(); }
      }
    } finally { await server.manage("set-admin", "settings-user", "off"); }
  });

  // Settings → Backups › Backup destinations (BackupDestinations.jsx,
  // backupStores.js, docs/dev/settings.md): an admin's section in a pane
  // every account has, one row per place backup tasks may copy their
  // snapshots to (Google Drive, OneDrive, a bucket), each with its own
  // Connect… or Set up….
  const destinations = (page) => page.locator('.settingsPane .setSection[data-setting="Backup destinations"]');
  // The rows under the section's rule, up to the next section's.
  const destinationRows = (page) => destinations(page).evaluate((head) => {
    const out = [];
    for (let el = head.nextElementSibling; el && !el.classList.contains("setSection"); el = el.nextElementSibling) {
      if (el.dataset.setting) out.push(el.dataset.setting);
      for (const sub of el.querySelectorAll?.("[data-setting]") || []) out.push(sub.dataset.setting);
    }
    return out;
  });
  const focused = (locator) => locator.evaluate((el) => el === document.activeElement || el.contains(document.activeElement));
  await step("settings: Backup destinations are in Backups and the settings search for an admin only, between the tasks and the snapshots", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Backups").click();
      await row(page, "Saved snapshots").waitFor();
      assertEq(await destinations(page).count(), 0, "a member has no Backup destinations");
      const box = page.getByRole("searchbox", { name: "Search settings" });
      await box.fill("bucket");
      await page.getByText('No settings found. Try "model", "PDF", or "storage".', { exact: true }).waitFor();
      assertEq(await page.locator(".settingsSearchResult").count(), 0, "a member's search finds no admin section");
      await box.fill("");
      await server.manage("set-admin", "settings-user", "on");
      await page.reload();
      await page.waitForSelector(".folderNewBtn");
      await openSettings(page);
      await nav(page, "Backups").click();
      await row(page, "Bucket").waitFor();
      const sections = await page.locator(".settingsPane .setSection").evaluateAll((els) => els.map((el) => el.dataset.setting));
      const at = sections.indexOf("Backup destinations");
      assertEq(sections.slice(at - 1, at + 2).join(" › "), "Periodic backup tasks › Backup destinations › Saved snapshots",
        "where the section sits (Cloud copies joins it once a destination is set up)");
      assertEq((await destinationRows(page)).join(" › "), "Google Drive › OneDrive › Bucket", "one row per destination");
      assertEq(await row(page, "Bucket").getByRole("button").innerText(), "Set up…", "no bucket yet: Set up…");
      assertEq(await destinations(page).getByRole("button").count(), 0, "no Save on the rule");
      // From another pane, the search finds a destination and lands on it.
      await nav(page, "Appearance").click();
      await box.fill("bucket");
      const results = page.locator(".settingsSearchResult");
      await results.first().waitFor();
      // the destination first, then Server backups' Off-site copies, which points at the section
      assertEq(JSON.stringify(await results.locator(".settingLabel").allInnerTexts()), JSON.stringify(["Bucket", "Cloud copies", "Off-site copies"]),
        "an admin's matches");
      assertEq(await results.first().locator("small").innerText(), "Backups › Backup destinations", "the match names its pane and section");
      await results.first().click();
      await row(page, "Bucket").waitFor();
      assertEq(await nav(page, "Backups").getAttribute("aria-current"), "page", "the match opens Backups");
      // The row renders once the pane has its reads; the jump waits for it,
      // then focuses it and brings it into view.
      await until(() => focused(row(page, "Bucket")), { what: "the jump to focus the row" });
      assert(await row(page, "Bucket").evaluate((el) => { const r = el.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; }),
        "the row is in view");
      await box.fill("");
      // The section carries the admin tag the rail's Administration caption does.
      assertEq(await destinations(page).locator(".uiTag.admin").innerText(), "admin", "Backup destinations are tagged an admin's");
      // Server › Server backups points at it: Off-site copies' Open Backup destinations lands there.
      await nav(page, "Server").click();
      await row(page, "Off-site copies").getByRole("button", { name: "Open Backup destinations", exact: true }).click();
      await until(async () => (await nav(page, "Backups").getAttribute("aria-current")) === "page", { what: "the jump opens Backups" });
      await until(() => focused(destinations(page)), { what: "and focuses Backup destinations" });
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await server.manage("set-admin", "settings-user", "off");
    }
  });

  // A stand-in S3 bucket on loopback, addressed by path as boto3 addresses a
  // bucket behind its own endpoint: ListObjectsV2 (split at a delimiter, as
  // the folder browser lists), HEAD, GET, PUT and DELETE of whole objects,
  // kept in memory (`objects`: key → {body, at}), no signature checked.
  async function standInBucket(name) {
    const objects = new Map();
    const xml = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const missing = (res, code) => {
      res.writeHead(404, { "Content-Type": "application/xml" });
      res.end(`<?xml version="1.0" encoding="UTF-8"?><Error><Code>${code}</Code><Message>${code}</Message></Error>`);
    };
    const stand = http.createServer((req, res) => {
      const chunks = [];
      req.on("data", (chunk) => chunks.push(chunk));
      req.on("end", () => {
        const url = new URL(req.url, "http://bucket");
        const [bucket, ...rest] = url.pathname.slice(1).split("/").map(decodeURIComponent);
        const key = rest.join("/");
        if (bucket !== name) { missing(res, "NoSuchBucket"); return; }
        if (!key && req.method === "GET") {
          const prefix = url.searchParams.get("prefix") || "";
          const delimiter = url.searchParams.get("delimiter") || "";
          const keys = [];
          const prefixes = new Set();
          for (const k of [...objects.keys()].filter((k) => k.startsWith(prefix)).sort()) {
            const at = delimiter ? k.indexOf(delimiter, prefix.length) : -1;
            if (at < 0) keys.push(k); else prefixes.add(k.slice(0, at + delimiter.length));
          }
          res.writeHead(200, { "Content-Type": "application/xml" });
          res.end(`<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">`
            + `<Name>${name}</Name><Prefix>${xml(prefix)}</Prefix><KeyCount>${keys.length + prefixes.size}</KeyCount><MaxKeys>1000</MaxKeys>`
            + `<IsTruncated>false</IsTruncated>${keys.map((k) => `<Contents><Key>${xml(k)}</Key><LastModified>${objects.get(k).at}</LastModified>`
              + `<ETag>"e2e"</ETag><Size>${objects.get(k).body.length}</Size><StorageClass>STANDARD</StorageClass></Contents>`).join("")}`
            + [...prefixes].map((p) => `<CommonPrefixes><Prefix>${xml(p)}</Prefix></CommonPrefixes>`).join("")
            + "</ListBucketResult>");
          return;
        }
        if (req.method === "PUT") {
          objects.set(key, { body: Buffer.concat(chunks), at: new Date().toISOString() });
          res.writeHead(200, { ETag: '"e2e"' });
          res.end();
          return;
        }
        if (req.method === "DELETE") { objects.delete(key); res.writeHead(204); res.end(); return; }
        const found = objects.get(key);
        if (!found) {
          if (req.method === "HEAD") { res.writeHead(404); res.end(); } else missing(res, "NoSuchKey");
          return;
        }
        res.writeHead(200, { "Content-Length": found.body.length, "Content-Type": "application/octet-stream", ETag: '"e2e"',
          "Last-Modified": new Date(found.at).toUTCString() });
        res.end(req.method === "HEAD" ? undefined : found.body);
      });
    });
    stand.unref();
    await new Promise((resolve) => stand.listen(0, "127.0.0.1", resolve));
    return {
      objects, port: stand.address().port,
      close: () => { stand.closeAllConnections(); return new Promise((resolve) => stand.close(resolve)); },
    };
  }

  await step("settings: the Bucket is set up in a dialog with a write-only secret and tested there; a backup task copies its snapshots to it, which come back from Saved snapshots, and Cloud copies brings back the workspaces only the bucket has", async () => {
    await server.manage("set-admin", "settings-user", "on");
    const before = (await user.api("/api/admin/offsite")).settings;
    const stand = await standInBucket("gamma-e2e");
    const where = `127.0.0.1:${stand.port}/gamma-e2e`;
    const gone = `e2eGone${Date.now().toString(36)}`;
    const gone2 = `e2eGoneTwo${Date.now().toString(36)}`;
    let task = null;
    // The workspace restored from the bucket: not the open one, whose restore reloads the tab.
    const side = await user.api("/api/workspaces", { method: "POST", body: { name: "Bucket e2e" } });
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Backups").click();
      const setUp = page.getByRole("dialog", { name: "Set up a bucket", exact: true });
      const edit = page.getByRole("dialog", { name: "Edit the bucket", exact: true });
      const field = (dialog, label) => dialog.getByLabel(label, { exact: true });
      const editButton = () => row(page, "Bucket").getByRole("button", { name: "Edit…", exact: true });
      await row(page, "Bucket").getByRole("button", { name: "Set up…", exact: true }).click();
      await setUp.waitFor();
      assertEq(await setUp.getByRole("button", { name: "How often" }).count(), 0, "the bucket has no schedule of its own");
      assertEq(await field(setUp, "Copies kept").count(), 0, "nor a count of copies: each task keeps its own");
      await field(setUp, "Bucket").fill("gamma-e2e");
      // pasted as R2's dashboard shows its S3 API, the bucket's name on the end: the server takes it off
      await field(setUp, "Endpoint").fill(`http://127.0.0.1:${stand.port}/gamma-e2e`);
      await field(setUp, "Region").fill("auto");
      await field(setUp, "Access key ID").fill("AKIAE2EBUCKET");
      await field(setUp, "Secret key").fill("e2e-bucket-secret");
      // Test tries the dialog's values once and answers beside its button.
      await setUp.getByRole("button", { name: "Test connection", exact: true }).click();
      const passed = setUp.locator(".aiKeyCheck").filter({ hasText: "gamma-e2e: 0 objects in the bucket" });
      await passed.waitFor({ timeout: 20000 });
      await setUp.getByRole("button", { name: "Save", exact: true }).click();
      await setUp.waitFor({ state: "detached" });
      const stored = (await user.api("/api/admin/offsite")).settings;
      for (const [key, value] of Object.entries({ bucket: "gamma-e2e", endpoint: `http://127.0.0.1:${stand.port}`, region: "auto",
        access_key: "AKIAE2EBUCKET", prefix: "", secret_set: true })) {
        assertEq(stored[key], value, `the server stored ${key}`);
      }
      assertEq(await row(page, "Bucket").locator(".settingDesc").innerText(), where, "the row says where the bucket is");
      // Reopened, Edit… reads it back; the secret stays on the server.
      await editButton().click();
      await edit.waitFor();
      assertEq(await field(edit, "Endpoint").inputValue(), `http://127.0.0.1:${stand.port}`);
      assertEq(await field(edit, "Secret key").inputValue(), "", "the secret never comes back");
      assertEq(await field(edit, "Secret key").getAttribute("placeholder"), "secret set — type to replace");
      // Browse… beside the prefix lists the bucket's folders from its top, with the dialog's settings: a
      // press on a folder chooses and opens it; the keys are the trees' (the arrows open and close, Space
      // chooses); its files show with their size, and a new folder is drawn until a copy is saved under
      // it. Choose fills the field.
      stand.objects.set("team/old/one.zip", { body: Buffer.from("12345"), at: new Date().toISOString() });
      await edit.getByRole("button", { name: "Browse…", exact: true }).click();
      const browse = page.getByRole("dialog", { name: "Choose the prefix", exact: true });
      const item = (name) => browse.getByRole("treeitem", { name, exact: true });
      const crumbs = async () => (await browse.locator(".folderBrowserPath .crumbBtn").allInnerTexts()).join("/");
      await item("team").click();
      await item("old").waitFor();
      assertEq(await crumbs(), "gamma-e2e/team", "a press chooses the folder and opens it");
      await item("team").press("ArrowLeft");
      await item("old").waitFor({ state: "detached" });
      assertEq(await crumbs(), "gamma-e2e/team", "← closes the folder, which stays chosen");
      await item("gamma-e2e").press("Space");
      assertEq(await crumbs(), "gamma-e2e", "Space chooses");
      await item("team").press("ArrowRight");
      await item("old").waitFor();
      assertEq(await crumbs(), "gamma-e2e", "→ opens a folder without choosing it");
      await item("old").click();
      const file = browse.getByRole("treeitem", { name: /^one.zip/ });
      await file.waitFor();
      assert((await file.innerText()).includes("5 B"), "a file shows its size");
      assertEq(await crumbs(), "gamma-e2e/team/old", "the crumbs name the folder chosen");
      await browse.locator(".folderBrowserPath").getByRole("button", { name: "team", exact: true }).click();
      await browse.getByRole("button", { name: "New folder", exact: true }).click();
      const name = browse.getByRole("textbox", { name: "New folder's name", exact: true });
      await name.fill("e2e");
      await name.press("Enter");
      const newFolder = browse.getByRole("treeitem", { name: /^e2e/ });
      await until(async () => (await newFolder.getAttribute("aria-selected")) === "true", { what: "the new folder chosen" });
      assertEq(await newFolder.locator(".uiTag").innerText(), "new", "drawn as not made yet");
      await browse.getByRole("button", { name: "Choose", exact: true }).click();
      await browse.waitFor({ state: "detached" });
      assertEq(await edit.getByRole("textbox", { name: "Prefix", exact: true }).inputValue(), "team/e2e", "Choose fills the prefix");
      assert(!stand.objects.has("team/e2e/"), "a bucket's new folder is no object");
      stand.objects.delete("team/old/one.zip");
      // Cancel with an unsaved edit asks first; discarded, nothing is sent.
      await edit.getByRole("button", { name: "Cancel", exact: true }).click();
      await edit.getByRole("button", { name: "Discard changes", exact: true }).click();
      await edit.waitFor({ state: "detached" });
      assertEq((await user.api("/api/admin/offsite")).settings.prefix, "", "a discarded edit is not saved");

      // A new task copies to the bucket by default, its chip naming where.
      await page.getByRole("button", { name: "Add task", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Add backup task", exact: true });
      await dialog.getByPlaceholder("e.g. Nightly research backup").fill("To the bucket");
      const copy = dialog.locator('[data-setting="Copy off this server"]').getByRole("button", { name: "Bucket", exact: true });
      assertEq(await copy.getAttribute("aria-pressed"), "true", "a new task copies to the bucket set up");
      assertEq(await copy.getAttribute("title"), where, "its chip names the bucket");
      await dialog.getByRole("checkbox", { name: "Include uploaded files", exact: true }).uncheck();
      await dialog.locator("ol li").first().waitFor(); // the schedule preview answered
      await dialog.getByRole("button", { name: "Create task", exact: true }).click();
      await dialog.waitFor({ state: "detached" });
      task = (await user.api("/api/backup-tasks")).tasks.find((t) => t.name === "To the bucket");
      assert(task?.bucket === true, "the task was saved with bucket on");
      const rowOf = page.getByRole("region", { name: "Periodic backup tasks" }).locator("tr", { hasText: "To the bucket" });
      assert((await rowOf.innerText()).includes("copies to the bucket"), "the row says it copies to the bucket");
      await rowOf.getByRole("button", { name: "Actions for To the bucket", exact: true }).click();
      await page.getByRole("menuitem", { name: "Run now", exact: true }).click();
      await until(() => rowOf.innerText().then((t) => /finished|failed/i.test(t)), { timeout: 30000, what: "the run to finish" });
      assert(/finished/i.test(await rowOf.innerText()), `the run finished: ${await rowOf.innerText()}`);
      await until(() => rowOf.innerText().then((t) => t.includes("in the bucket")), { what: "the Last run cell to say the copies went" });
      const made = (await user.api(`/api/workspaces/${side.id}/backups`)).backups.find((b) => b.task_id === task.id);
      const object = `snapshots/${side.id}/${made.name}.zip`;
      assert(stand.objects.has(object), `the bucket holds ${object}`);
      assert(stand.objects.has(`snapshots/${side.id}/workspace.json`), "and the workspace's name beside it");
      const tagged = page.locator(".aiProvRow").filter({ has: page.locator(".uiTag", { hasText: "in the bucket" }) });
      await until(async () => (await tagged.count()) >= 1, { what: "the snapshots to say they are in the bucket" });

      // The snapshot gone from the server, its copy is a row of its own, restored from there.
      await user.api(`/api/workspaces/${side.id}/backups/${made.name}`, { method: "DELETE" });
      // Copies of two workspaces this server does not have (a lost disk).
      for (const [id, name] of [[gone, "Gone e2e"], [gone2, "Gone two"]]) {
        stand.objects.set(`snapshots/${id}/${made.name}.zip`, stand.objects.get(object));
        stand.objects.set(`snapshots/${id}/workspace.json`, { body: Buffer.from(JSON.stringify({ name })), at: new Date().toISOString() });
      }
      // Changed behind the pane's back: the dialog opened again reads it all afresh.
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor({ state: "detached" });
      await openSettings(page);
      await nav(page, "Backups").click();
      const only = page.locator(".aiProvRow", { hasText: "only in the bucket" });
      await until(async () => (await only.count()) === 1, { what: "the copy only the bucket holds, under its workspace" });
      const autoBefore = (await user.api(`/api/workspaces/${side.id}/backups`)).backups.filter((b) => b.auto).length;
      await only.filter({ has: page.getByRole("button", { name: "Restore", exact: true }) }).getByRole("button", { name: "Restore", exact: true }).click();
      await page.getByRole("menuitem", { name: "Replace…", exact: true }).click();
      await page.locator(".confirmModal").getByRole("button", { name: "Replace", exact: true }).click();
      await until(async () => (await user.api(`/api/workspaces/${side.id}/backups`)).backups.filter((b) => b.auto).length > autoBefore,
        { timeout: 30000, what: "the restore from the bucket to keep a Before restore snapshot" });
      // Cloud copies: what the bucket holds, and the two workspaces only it has, named as they were.
      const cloud = page.locator('.settingsPane .setSection[data-setting="Cloud copies"]');
      await cloud.waitFor();
      // the bucket's row there, after its row under Backup destinations
      const summary = await page.locator('.settingsPane .settingRow[data-setting="Bucket"]').last().locator(".settingDesc").innerText();
      assert(/· \d+ copies · \d+ workspaces · /.test(summary), `the bucket's summary: ${summary}`);
      await row(page, "2 workspaces are only in the cloud").waitFor();
      for (const name of ["Gone e2e", "Gone two"]) await row(page, name).waitFor();
      // Browse…: every copy by workspace, found by name.
      await cloud.getByRole("button", { name: "Browse…", exact: true }).click();
      const browser = page.getByRole("dialog", { name: "Cloud copies", exact: true });
      await until(async () => {
        const names = await browser.locator(".cloudCopiesHead .settingLabel").allInnerTexts();
        return ["Bucket e2e", "Gone e2e", "Gone two"].every((name) => names.includes(name));
      }, { what: "the workspaces with copies, here and not" });
      await browser.getByRole("searchbox", { name: "Find a workspace", exact: true }).fill("two");
      await until(async () => (await browser.locator(".cloudCopiesGroup").count()) === 1, { what: "the one workspace found by name" });
      assert((await browser.locator(".cloudCopiesGroup").innerText()).includes("not on this server"), "it says the server lacks it");
      await browser.getByRole("button", { name: "Close", exact: true }).click();
      await browser.waitFor({ state: "detached" });
      // Restore all…: both come back, each as a workspace of its own id, in one job.
      await row(page, "2 workspaces are only in the cloud").getByRole("button", { name: "Restore all…", exact: true }).click();
      const pick = page.getByRole("dialog", { name: "Restore workspaces from the cloud", exact: true });
      assertEq(await pick.getByRole("checkbox").count(), 2, "both offered");
      await pick.getByRole("button", { name: "Restore 2 workspaces", exact: true }).click();
      await pick.waitFor({ state: "detached" });
      await until(async () => {
        const ids = (await user.api("/api/workspaces/mine")).workspaces.filter((w) => w.role === "owner").map((w) => w.id);
        return ids.includes(gone) && ids.includes(gone2);
      }, { timeout: 60000, what: "both copies to come back as workspaces of their own ids" });
      // The job ends, and keeps its result, only after its last workspace is in place.
      const listed = (await user.api("/api/jobs")).jobs.find((j) => j.kind === "copies-restore");
      let restored;
      await until(async () => {
        restored = await user.api(`/api/jobs/${listed.id}`);
        return !["queued", "running"].includes(restored.state);
      }, { timeout: 30000, what: "the copies' restore job to end" });
      assertEq(restored.state, "done", "the restore job ended done");
      assertEq(restored.result?.restored?.length, 2, "one job brought both back");
      await row(page, "2 workspaces are only in the cloud").waitFor({ state: "detached" });
      // Deleted from the bucket, a copy is gone from there and from the list.
      await until(async () => (await only.count()) === 3, { what: "the three copies, each under its workspace now" });
      const zips = () => [...stand.objects.keys()].filter((k) => k.endsWith(".zip")).length;
      const held = zips();
      // its button names what it deletes, red as every irreversible delete is
      const deleteCopy = only.first().getByRole("button", { name: /^Delete the copy in the bucket from / });
      assert(await deleteCopy.evaluate((el) => el.classList.contains("danger")), "the delete is a danger button");
      await deleteCopy.click();
      await page.locator(".confirmModal").getByRole("button", { name: "Delete", exact: true }).click();
      await until(async () => (await only.count()) === 2, { what: "the deleted copy to leave the list" });
      assertEq(zips(), held - 1, "and the bucket");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      try {
        if (task) await user.api(`/api/backup-tasks/${task.id}`, { method: "DELETE" });
        for (const id of [gone, side.id]) {
          if ((await user.api("/api/workspaces/mine")).workspaces.some((w) => w.id === id)) {
            await user.api(`/api/workspaces/${id}`, { method: "DELETE" });
          }
        }
        const { secret_set: _secret, ...fields } = before;
        await user.api("/api/admin/offsite", { method: "PUT", body: fields });
      } finally {
        await server.manage("set-admin", "settings-user", "off");
        await stand.close();
      }
    }
  });

  // GAMMA_S3_BUCKET hands every field to the server's environment: a
  // second server of its own (about 2 s to start). GAMMA_OFFSITE=off keeps
  // it from running a round at startup, the unreachable endpoint from ever
  // reaching AWS.
  await step("settings: with GAMMA_S3_BUCKET in the server's environment, the Bucket shows a read-only summary", async () => {
    const managed = new Server({ env: { GAMMA_S3_BUCKET: "gamma-env", GAMMA_S3_ENDPOINT: "http://127.0.0.1:1", GAMMA_S3_REGION: "auto",
      GAMMA_S3_ACCESS_KEY: "AKIAE2EENV", GAMMA_S3_SECRET_KEY: "e2e-env-secret", GAMMA_OFFSITE: "off",
      GAMMA_OFFSITE_INTERVAL: "900", GAMMA_OFFSITE_KEEP: "5" } });
    let ctx = null;
    try {
      await managed.start();
      await managed.manage("create-user", "env-admin", "env-admin-pw");
      await managed.manage("set-admin", "env-admin", "on");
      const admin = await new Account(managed, "env-admin", "env-admin-pw").login();
      ctx = await admin.context(browser);
      await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
      const page = await openPage(ctx, managed.base);
      await page.waitForSelector(".folderNewBtn");
      await openSettings(page);
      await nav(page, "Backups").click();
      // The summary names the environment's bucket, where it is and the
      // variable; the sentence is its hover title. Nothing opens an editor.
      const summary = row(page, "Bucket");
      await summary.locator(".settingDesc").filter({ hasText: "gamma-env" }).waitFor();
      assertEq(await summary.locator(".settingDesc").innerText(), "127.0.0.1:1/gamma-env · set by GAMMA_S3_BUCKET",
        "the summary's hint says where the bucket is and the variable that sets it");
      assertEq(await summary.getAttribute("title"), "Set by GAMMA_S3_BUCKET in the server's environment", "the sentence on hover");
      assert(!(await destinationRows(page)).includes("Off-site rounds"), "no rounds' status while the environment's rounds are off");
      assertEq(await summary.getByRole("button", { name: /^(Edit|Set up)…$/ }).count(), 0, "no Edit… or Set up…");
      assertEq(await destinations(page).getByRole("button").count(), 0, "no Save on the rule");
      // Test tries the environment's bucket and answers beside its button
      // (TestResult, as the dialog's Test connection does).
      await summary.getByRole("button", { name: "Test", exact: true }).click();
      const failure = summary.locator(".aiKeyCheck.aiKeysError");
      await failure.waitFor({ timeout: 20000 });
      assert(/cannot reach s3:\/\/gamma-env\/ at http:\/\/127\.0\.0\.1:1\b/.test(await failure.innerText()),
        `the failure names the environment's endpoint: ${await failure.innerText()}`);
      const refused = await admin.api("/api/admin/offsite", { method: "PUT", body: { bucket: "gamma-other" } }).catch((e) => e);
      assertEq(refused.status, 409, "the server refuses a save");
      assertNoProblems(page);
    } finally {
      await ctx?.close();
      await managed.stop();
    }
  });

  // Settings → Backups › Backup destinations › Google Drive (BackupCloud.jsx,
  // gamma/gdrive.py, gamma/cloud_account.py, docs/dev/backups.md "Google
  // Drive"): a second server whose Google is the fake one
  // (backend/tests/gdrive_fake.py over HTTP, its device codes asking for a
  // 2 s interval), so nothing reaches Google, the app the fake knows given
  // to the server as Gamma's own would be (manage.py cloud-client drive).
  // It is served over HTTPS, as a
  // NAS opened from another computer is, so Connect… opens on a code,
  // cancelled; another entered, which
  // the fake approves, closing the dialog; Test; a backup task copying its
  // snapshot into the folder, which Saved snapshots tags and, the snapshot
  // deleted, lists alone and deletes from Drive; the folder renamed; and
  // Disconnect, which revokes the token.
  // A fake cloud service over HTTP (gdrive_fake.py prints GOOGLE <base>,
  // onedrive_fake.py MICROSOFT <base>), with its scenario's side.
  async function startFake(script, tag) {
    const proc = spawn(PYTHON, ["-I", script, "--port", "0", "--interval", "2"], {
      cwd: ROOT, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    });
    let out = "";
    const base = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`the fake ${tag} did not start:\n${out}`)), 20000);
      const read = (chunk) => {
        out += chunk;
        const found = new RegExp(`${tag} (\\S+)`).exec(out);
        if (found) { clearTimeout(timer); resolve(found[1]); }
      };
      proc.stdout.on("data", read);
      proc.stderr.on("data", read);
      proc.on("exit", (code) => { clearTimeout(timer); reject(new Error(`the fake ${tag} exited (${code}):\n${out}`)); });
    });
    const stop = () => {
      if (proc.exitCode != null) return;
      if (process.platform === "win32") { try { execFileSync("taskkill", ["/pid", String(proc.pid), "/t", "/f"], { stdio: "ignore" }); } catch {} }
      else proc.kill("SIGTERM");
    };
    return { base, stop, state: async () => (await fetch(`${base}/_fake/state`)).json(),
      approve: () => fetch(`${base}/_fake/approve`, { method: "POST" }) };
  }

  await step("settings: Google Drive is connected by signing in with a code, tested, copied to by a backup task, read back in Saved snapshots and disconnected", async () => {
    const google = await startFake("backend/tests/gdrive_fake.py", "GOOGLE");
    const drive = new Server({ env: { GAMMA_GOOGLE_OAUTH_BASE: google.base, GAMMA_GOOGLE_API_BASE: google.base } });
    let ctx = null;
    try {
      drive.tls = (await certificate(drive.dir, "tls")).tls;
      await drive.start();
      await drive.manage("create-user", "drive-admin", "drive-admin-pw");
      await drive.manage("set-admin", "drive-admin", "on");
      await drive.manage("cloud-client", "drive", "fake-client.apps.googleusercontent.com", "GOCSPX-fake");
      const admin = await new Account(drive, "drive-admin", "drive-admin-pw").login();
      ctx = await admin.context(browser, { ignoreHTTPSErrors: true });
      await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
      const page = await openPage(ctx, drive.base);
      await page.waitForSelector(".folderNewBtn");
      await openSettings(page);
      await nav(page, "Backups").click();
      const gdrive = row(page, "Google Drive");
      await gdrive.getByRole("button", { name: "Connect…", exact: true }).waitFor();
      assertEq((await destinationRows(page))[0], "Google Drive", "the first of the destinations");

      // Connect…: Google's code at once, one button to copy it and open Google's page, while the
      // dialog waits; Cancel stops waiting. Nothing to set up first.
      assertEq(await page.locator('.settingsPane [data-setting="OAuth client"]').count(), 0, "no app to set up first");
      const connect = page.getByRole("dialog", { name: "Connect Google Drive", exact: true });
      const code = connect.getByRole("textbox", { name: "Your code", exact: true });
      await gdrive.getByRole("button", { name: "Connect…", exact: true }).click();
      await code.waitFor();
      assertEq(await code.inputValue(), "ABCD-0001", "the code Google gave");
      const openGoogle = connect.getByRole("link", { name: "Copy the code and open Google", exact: true });
      assertEq(await openGoogle.getAttribute("href"), "https://www.google.com/device", "the button opens Google's page");
      await connect.getByText("Waiting for Google…", { exact: true }).waitFor();
      await connect.getByRole("button", { name: "Cancel", exact: true }).click();
      await connect.waitFor({ state: "detached" });
      assertEq((await admin.api("/api/admin/clouds/drive")).pending, null, "Cancel stops waiting on the server too");
      await gdrive.getByRole("button", { name: "Connect…", exact: true }).click();
      await code.waitFor();
      assertEq(await code.inputValue(), "ABCD-0002", "a new code");
      await google.approve();
      await connect.waitFor({ state: "detached", timeout: 20000 });
      const connected = gdrive;
      await connected.getByRole("button", { name: "Test", exact: true }).waitFor();
      assertEq(await connected.locator(".settingDesc").innerText(), "someone@gmail.com › Gamma backups", "the account and the folder");
      const view = await admin.api("/api/admin/clouds/drive");
      assert(view.connected && !JSON.stringify(view).includes("rt-"), "connected, and the token never reaches the browser");
      await connected.getByRole("button", { name: "Test", exact: true }).click();
      await connected.locator(".aiKeyCheck").filter({ hasText: "someone@gmail.com: 0 items in Gamma backups" }).waitFor({ timeout: 20000 });

      // A new task copies there by default, its chip naming where.
      await page.getByRole("button", { name: "Add task", exact: true }).click();
      const editor = page.getByRole("dialog", { name: "Add backup task", exact: true });
      await editor.getByPlaceholder("e.g. Nightly research backup").fill("To Drive");
      const chip = editor.locator('[data-setting="Copy off this server"]').getByRole("button", { name: "Google Drive", exact: true });
      assertEq(await chip.getAttribute("aria-pressed"), "true", "a new task copies to Drive");
      assertEq(await chip.getAttribute("title"), "someone@gmail.com › Gamma backups", "the chip names the account and the folder");
      assertEq(await editor.getByRole("button", { name: "Bucket", exact: true }).count(), 0, "no bucket, no bucket chip");
      await editor.getByRole("checkbox", { name: "Include uploaded files", exact: true }).uncheck();
      await editor.locator("ol li").first().waitFor(); // the schedule preview answered
      await editor.getByRole("button", { name: "Create task", exact: true }).click();
      await editor.waitFor({ state: "detached" });
      const task = (await admin.api("/api/backup-tasks")).tasks.find((t) => t.name === "To Drive");
      assert(task?.drive === true && !task.bucket, "the task was saved with Drive on");
      const rowOf = page.getByRole("region", { name: "Periodic backup tasks" }).locator("tr", { hasText: "To Drive" });
      assert((await rowOf.innerText()).includes("copies to Google Drive"), "the row says it copies to Google Drive");
      await rowOf.getByRole("button", { name: "Actions for To Drive", exact: true }).click();
      await page.getByRole("menuitem", { name: "Run now", exact: true }).click();
      await until(() => rowOf.innerText().then((t) => /finished|failed/i.test(t)), { timeout: 30000, what: "the run to finish" });
      assert(/finished/i.test(await rowOf.innerText()), `the run finished: ${await rowOf.innerText()}`);
      await until(() => rowOf.innerText().then((t) => t.includes("in Google Drive")), { what: "the Last run cell to say the copies went" });
      const ws = (await admin.api("/api/workspaces/mine")).workspaces.find((w) => w.role === "owner").id;
      const made = (await admin.api(`/api/workspaces/${ws}/backups`)).backups.find((b) => b.task_id === task.id);
      const file = `Gamma backups/snapshots/${ws}/${made.name}.zip`;
      const files = (await google.state()).files;
      assertEq(files[file], made.size_bytes, `Drive holds ${file}, whole`);
      assert(`Gamma backups/snapshots/${ws}/workspace.json` in files, "and the workspace's name beside it");
      const tagged = page.locator(".aiProvRow").filter({ has: page.locator(".uiTag", { hasText: "in Google Drive" }) });
      await until(async () => (await tagged.count()) >= 1, { what: "the snapshot to say it is in Google Drive" });

      // The snapshot gone from the server, its copy is a row of its own, deleted from Drive there.
      await admin.api(`/api/workspaces/${ws}/backups/${made.name}`, { method: "DELETE" });
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor({ state: "detached" });
      await openSettings(page);
      await nav(page, "Backups").click();
      const only = page.locator(".aiProvRow", { hasText: "only in Google Drive" });
      await until(async () => (await only.count()) === 1, { what: "the copy only Drive holds" });
      await only.getByRole("button", { name: /^Delete the copy in Google Drive from / }).click();
      const question = page.locator(".confirmModal");
      assert((await question.innerText()).includes("in Google Drive from"), "the question names Drive");
      await question.getByRole("button", { name: "Delete", exact: true }).click();
      await until(async () => (await only.count()) === 0, { what: "the deleted copy to leave the list" });
      assert(!(file in (await google.state()).files), "and Drive");

      // Edit… chooses the folder new copies go to in a tree of My Drive, which shows only what Gamma
      // made, the folder in use chosen and open; My Drive itself cannot be the folder. New folder makes
      // one at once in the folder chosen, Drive's refusal of a name said under the tree; a Save pressed
      // while the name is typed waits for the folder. It holds Disconnect…, which revokes the access.
      const edit = page.getByRole("dialog", { name: "Edit Google Drive", exact: true });
      await gdrive.getByRole("button", { name: "Edit…", exact: true }).click();
      const tree = edit.getByRole("tree", { name: "Folders in My Drive", exact: true });
      const item = (name) => tree.getByRole("treeitem", { name, exact: true });
      await item("snapshots").waitFor();
      assertEq(await item("Gamma backups").getAttribute("aria-selected"), "true", "the folder in use is chosen");
      const save = edit.getByRole("button", { name: "Save", exact: true });
      await item("My Drive").click();
      assert(await save.isDisabled(), "My Drive itself cannot be the folder");
      await item("Gamma backups").click();
      await edit.getByRole("button", { name: "New folder", exact: true }).click();
      const name = edit.getByRole("textbox", { name: "New folder's name", exact: true });
      await name.fill("..");
      await name.press("Enter");
      await edit.locator(".folderBrowser [role=alert]").filter({ hasText: "not . or .." }).waitFor();
      assertEq(await name.inputValue(), "..", "a refused name stays in the field");
      await name.fill("Server A");
      await save.click(); // the field's blur makes the folder; Save waits for it, so this press saves nothing
      await until(async () => (await item("Server A").getAttribute("aria-selected")) === "true", { what: "the new folder chosen" });
      assert(await edit.isVisible(), "a Save pressed while the folder was made did not save the folder before it");
      const held = await admin.api(`/api/admin/clouds/drive/browse?${new URLSearchParams({ path: "Gamma backups" })}`);
      assert(held.folders.some((f) => f.name === "Server A"), "made in Drive at once");
      await save.click();
      await edit.waitFor({ state: "detached" });
      assertEq(await gdrive.locator(".settingDesc").innerText(), "someone@gmail.com › Gamma backups › Server A");
      await gdrive.getByRole("button", { name: "Edit…", exact: true }).click();
      await edit.getByRole("button", { name: "Disconnect…", exact: true }).click();
      await page.locator(".confirmModal").getByRole("button", { name: "Disconnect", exact: true }).click();
      await gdrive.getByRole("button", { name: "Connect…", exact: true }).waitFor();
      assertEq((await google.state()).revoked, 1, "the token was revoked at Google");
      assertNoProblems(page, ["POST /api/admin/clouds/drive/folders -> 400"]); // the refused ".."
    } finally {
      await ctx?.close();
      await drive.stop();
      google.stop();
    }
  });

  // Where Gamma runs on the computer it is opened on (the desktop app; the
  // suite's servers are at 127.0.0.1), Connect… opens Google's page, no code:
  // here the stand-in's, which allows at once and sends the browser back to
  // the server's callback, which makes the connection; the dialog, waiting,
  // sees it and closes.
  await step("settings: where Gamma runs on this computer, Google Drive connects on Google's page, no code", async () => {
    const google = await startFake("backend/tests/gdrive_fake.py", "GOOGLE");
    const drive = new Server({ env: { GAMMA_GOOGLE_OAUTH_BASE: google.base, GAMMA_GOOGLE_API_BASE: google.base,
      GAMMA_GOOGLE_ACCOUNTS_BASE: google.base } });
    let ctx = null;
    try {
      await drive.start();
      await drive.manage("create-user", "page-admin", "page-admin-pw");
      await drive.manage("set-admin", "page-admin", "on");
      await drive.manage("cloud-client", "drive", "--desktop", "fake-desktop.apps.googleusercontent.com", "GOCSPX-desktop");
      const admin = await new Account(drive, "page-admin", "page-admin-pw").login();
      ctx = await admin.context(browser);
      await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
      const page = await openPage(ctx, drive.base);
      await page.waitForSelector(".folderNewBtn");
      await openSettings(page);
      await nav(page, "Backups").click();
      await row(page, "Google Drive").getByRole("button", { name: "Connect…", exact: true }).click();
      const connect = page.getByRole("dialog", { name: "Connect Google Drive", exact: true });
      const go = connect.getByRole("link", { name: "Continue with Google", exact: true });
      await go.waitFor();
      assertEq(await connect.getByRole("textbox", { name: "Your code", exact: true }).count(), 0, "no code to enter");
      const [tab] = await Promise.all([ctx.waitForEvent("page"), go.click()]);
      await tab.getByText("Google Drive is connected", { exact: true }).waitFor({ timeout: 20000 });
      assert(tab.url().startsWith(`${drive.base}/api/admin/clouds/drive/callback?`), `back at the server: ${tab.url()}`);
      await tab.close();
      await connect.waitFor({ state: "detached", timeout: 20000 });
      await row(page, "Google Drive").locator(".settingDesc").filter({ hasText: "someone@gmail.com" }).waitFor();
      assertNoProblems(page);
    } finally {
      await ctx?.close();
      await drive.stop();
      google.stop();
    }
  });

  // OneDrive (BackupCloud.jsx, gamma/onedrive.py) on a server whose
  // Microsoft is the fake one (backend/tests/onedrive_fake.py over HTTP),
  // the app the fake knows given to the server as Gamma's own would be
  // (manage.py cloud-client onedrive). The server runs at 127.0.0.1, as the
  // desktop app's does, so Connect… opens Microsoft's page, which sends the
  // browser back to localhost, the address Microsoft's app registers (the
  // port is not matched); the dialog sees the connection. Then a task
  // copying there, and Cloud copies listing the copy.
  await step("settings: OneDrive connects on Microsoft's page, a task copies to it, and Cloud copies lists the copy", async () => {
    const microsoft = await startFake("backend/tests/onedrive_fake.py", "MICROSOFT");
    const onServer = new Server({ env: { GAMMA_MICROSOFT_LOGIN_BASE: microsoft.base, GAMMA_MICROSOFT_GRAPH_BASE: microsoft.base } });
    let ctx = null;
    try {
      await onServer.start();
      await onServer.manage("create-user", "od-admin", "od-admin-pw");
      await onServer.manage("set-admin", "od-admin", "on");
      await onServer.manage("cloud-client", "onedrive", "11111111-2222-4333-8444-555555555555");
      const admin = await new Account(onServer, "od-admin", "od-admin-pw").login();
      ctx = await admin.context(browser);
      await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
      const page = await openPage(ctx, onServer.base);
      await page.waitForSelector(".folderNewBtn");
      await openSettings(page);
      await nav(page, "Backups").click();
      const onedrive = row(page, "OneDrive");
      await onedrive.getByRole("button", { name: "Connect…", exact: true }).click();
      const connect = page.getByRole("dialog", { name: "Connect OneDrive", exact: true });
      const go = connect.getByRole("link", { name: "Continue with Microsoft", exact: true });
      await go.waitFor();
      assertEq(await connect.getByRole("textbox", { name: "Your code", exact: true }).count(), 0, "no code to enter");
      const [tab] = await Promise.all([ctx.waitForEvent("page"), go.click()]);
      await tab.getByText("OneDrive is connected", { exact: true }).waitFor({ timeout: 20000 });
      assert(tab.url().startsWith(`http://localhost:${onServer.port}/api/admin/clouds/onedrive/callback?`), `back at localhost: ${tab.url()}`);
      await tab.close();
      await connect.waitFor({ state: "detached", timeout: 20000 });
      await onedrive.locator(".settingDesc").filter({ hasText: "someone@outlook.com › Gamma backups" }).waitFor();
      const view = await admin.api("/api/admin/clouds/onedrive");
      assert(view.connected && view.revokes === false && !JSON.stringify(view).includes("mrt-"),
        "connected, Microsoft revokes nothing on Disconnect, and the token never reaches the browser");

      // A new task copies there: its chip on, naming where on hover.
      await page.getByRole("button", { name: "Add task", exact: true }).click();
      const editor = page.getByRole("dialog", { name: "Add backup task", exact: true });
      await editor.getByPlaceholder("e.g. Nightly research backup").fill("To OneDrive");
      const chips = editor.locator('[data-setting="Copy off this server"]').getByRole("button");
      assertEq((await chips.allInnerTexts()).join(), "OneDrive", "a chip per destination set up, none for the others");
      assertEq(await chips.first().getAttribute("aria-pressed"), "true", "on for a new task");
      assertEq(await chips.first().getAttribute("title"), "someone@outlook.com › Gamma backups", "the chip names where its copies go");
      await editor.getByRole("checkbox", { name: "Include uploaded files", exact: true }).uncheck();
      await editor.locator("ol li").first().waitFor(); // the schedule preview answered
      await editor.getByRole("button", { name: "Create task", exact: true }).click();
      await editor.waitFor({ state: "detached" });
      const task = (await admin.api("/api/backup-tasks")).tasks.find((t) => t.name === "To OneDrive");
      assert(task?.onedrive === true && !task.drive && !task.bucket, "saved with OneDrive on");
      const rowOf = page.getByRole("region", { name: "Periodic backup tasks" }).locator("tr", { hasText: "To OneDrive" });
      assert((await rowOf.innerText()).includes("copies to OneDrive"), "the row names OneDrive");
      await rowOf.getByRole("button", { name: "Actions for To OneDrive", exact: true }).click();
      await page.getByRole("menuitem", { name: "Run now", exact: true }).click();
      await until(() => rowOf.innerText().then((t) => /finished|failed/i.test(t)), { timeout: 30000, what: "the run to finish" });
      assert(/finished/i.test(await rowOf.innerText()), `the run finished: ${await rowOf.innerText()}`);
      await until(() => rowOf.innerText().then((t) => t.includes("in OneDrive")), { what: "the Last run cell to say where the copies went" });
      const ws = (await admin.api("/api/workspaces/mine")).workspaces.find((w) => w.role === "owner").id;
      const made = (await admin.api(`/api/workspaces/${ws}/backups`)).backups.find((b) => b.task_id === task.id);
      assertEq((await microsoft.state()).files[`Gamma backups/snapshots/${ws}/${made.name}.zip`], made.size_bytes, "OneDrive holds the copy, whole");
      const tagged = page.locator(".aiProvRow").filter({ has: page.locator(".uiTag", { hasText: "in OneDrive" }) });
      await until(async () => (await tagged.count()) === 1, { what: "the snapshot to say it is in OneDrive" });

      // Cloud copies: OneDrive's row, and Browse… showing the copy.
      const cloud = page.locator('.settingsPane .setSection[data-setting="Cloud copies"]');
      await cloud.waitFor();
      await cloud.getByRole("button", { name: "Browse…", exact: true }).click();
      const copies = page.getByRole("dialog", { name: "Cloud copies", exact: true });
      await until(async () => (await copies.locator(".aiProvRow").count()) === 1, { what: "the copy in OneDrive" });
      assert((await copies.locator(".aiProvRow").innerText()).includes("in OneDrive"), "tagged with its store");
      await copies.getByRole("button", { name: "Close", exact: true }).click();
      assertNoProblems(page);
    } finally {
      await ctx?.close();
      await onServer.stop();
      microsoft.stop();
    }
  });

  // Settings → Network proxy (SettingsProxy.jsx, gamma/proxy_settings.py,
  // docs/dev/network_proxy.md): an admin's pane under Administration. The
  // proxy is the unreachable 127.0.0.1:1, so the Test's sites all fail
  // there and nothing leaves this machine; the address typed beside Test is
  // a stand-in site on loopback, which is always reached directly.
  await step("settings: Network proxy is an admin's pane; Manual is tested against an unreachable proxy, saved with a write-only password, read back after a reload, and set back to the system's", async () => {
    const site = http.createServer((req, res) => { res.writeHead(204); res.end(); });
    site.unref();
    await new Promise((resolve) => site.listen(0, "127.0.0.1", resolve));
    const { ctx, page } = await setup();
    let admin = false;
    try {
      await openSettings(page);
      assertEq(await nav(page, "Network proxy").count(), 0, "a member has no Network proxy pane");
      const box = page.getByRole("searchbox", { name: "Search settings" });
      await box.fill("socks5");
      await page.getByText('No settings found. Try "model", "PDF", or "storage".', { exact: true }).waitFor();
      await box.fill("");
      await server.manage("set-admin", "settings-user", "on");
      admin = true;
      const before = await user.api("/api/admin/proxy");
      await page.reload();
      await page.waitForSelector(".folderNewBtn");
      await openSettings(page);
      // The search finds the pane's rows; one shown only under Manual lands
      // on the Proxy row, which offers Manual.
      await box.fill("socks5");
      const results = page.locator(".settingsSearchResult");
      await results.first().waitFor();
      assertEq(await results.first().locator(".settingLabel").innerText(), "Network proxy", "the pane leads an admin's matches");
      await results.filter({ has: page.getByText("Proxy server", { exact: true }) }).click();
      const proxyRow = row(page, "Proxy");
      await proxyRow.waitFor();
      assertEq(await nav(page, "Network proxy").getAttribute("aria-current"), "page", "the match opens Network proxy");
      await until(async () => proxyRow.evaluate((el) => el === document.activeElement), { what: "the jump to focus the Proxy row" });

      const mode = (name) => proxyRow.getByRole("button", { name, exact: true });
      const pressed = async (name) => (await mode(name).getAttribute("aria-pressed")) === "true";
      const hint = () => proxyRow.locator(".settingDesc").innerText();
      assert(await pressed("Use system settings"), "the system's settings by default");
      const { system } = before;
      const found = system.proxy ? `Uses ${system.proxy}, from ${system.from === "environment" ? "the server's environment" : "this computer's network settings"}`
        : system.http ? `Uses ${system.http} for http:// addresses only; https connects directly`
        : system.unsupported ?`${system.unsupported} can't be used, so this server connects directly` : "No system proxy found: connects directly";
      assertEq(await hint(), found, "the hint says what the system's settings give");
      const connection = page.locator('.settingsPane .setSection[data-setting="Connection"]');
      assertEq(await connection.getByRole("button").count(), 0, "no Save on the rule");

      // Manual: the address, the sign-in, the list and the name lookups.
      await mode("Manual").click();
      assertEq(await hint(), "Uses the proxy below");
      const address = row(page, "Proxy server").getByLabel("Proxy server", { exact: true });
      const username = row(page, "Sign-in").getByLabel("Username", { exact: true });
      const password = row(page, "Sign-in").getByLabel("Password", { exact: true });
      const bypassRow = row(page, "Don't use the proxy for");
      const bypass = bypassRow.getByLabel("Don't use the proxy for", { exact: true });
      const reset = bypassRow.getByRole("button", { name: "Reset to defaults", exact: true });
      await address.waitFor();
      assertEq(await bypass.inputValue(), before.default_bypass, "the list starts as the defaults");
      assertEq(await reset.count(), 0, "nothing to reset");
      assert(!await page.getByRole("checkbox", { name: "Let the proxy look up names", exact: true }).isChecked(), "the proxy looks up no names by default");
      const save = connection.getByRole("button", { name: "Save", exact: true });
      // Saved once the rule has no button left: while the PUT is out, Save
      // reads "Saving…", which `save` no longer matches.
      const saveAndWait = async () => {
        await save.click();
        await connection.getByRole("button").waitFor({ state: "detached" });
      };
      // An https:// proxy is refused in the server's words, and nothing is saved.
      await address.fill("https://127.0.0.1:1");
      await save.click();
      await page.getByRole("alert").filter({ hasText: "An https:// proxy isn't supported yet" }).waitFor();
      assertEq((await user.api("/api/admin/proxy")).mode, before.mode, "a refused save changes nothing");
      // The refusal was about the draft before an edit, so the edit drops it.
      await address.fill("http://127.0.0.1:1");
      await page.getByRole("alert").filter({ hasText: "An https:// proxy isn't supported yet" }).waitFor({ state: "detached" });
      await username.fill("e2e-proxy");
      await password.fill("e2e-proxy-secret");

      // Test tries the draft: each site through the proxy, which is not
      // there, and the typed address directly. The tag says which way.
      const port = site.address().port;
      const testRow = row(page, "Test the connection");
      await testRow.getByLabel("Address to test", { exact: true }).fill(`http://127.0.0.1:${port}/`);
      await testRow.getByRole("button", { name: "Test", exact: true }).click();
      const lines = page.locator(".settingsPane .aiKeyCheck");
      await until(async () => (await lines.count()) >= 5, { what: "a line per site the Test tried", timeout: 30000 });
      const tried = await lines.evaluateAll((els) => els.map((el) => {
        const tag = el.querySelector(".uiTag");
        return { text: el.textContent, ok: el.classList.contains("aiTestOk"), tag: tag?.textContent, title: tag?.title };
      }));
      for (const name of ["OpenAI", "Anthropic", "Zotero", "arXiv"]) {
        const line = tried.find((l) => l.text.includes(name));
        assert(line && !line.ok && line.tag === "Proxy", `${name} went through the proxy and failed: ${JSON.stringify(line)}`);
      }
      const openai = tried.find((l) => l.text.includes("OpenAI")).text;
      assert(openai.includes("Couldn't reach the proxy at 127.0.0.1:1"), `the line says why: ${openai}`);
      const own = tried.filter((l) => l.tag === "Direct");
      assertEq(own.length, 1, "only the typed address went directly");
      assert(own[0].ok && own[0].title === "loopback" && own[0].text.includes(`127.0.0.1:${port}`) && /\b204 · \d+ ms$/.test(own[0].text),
        `the typed address, reached on loopback: ${JSON.stringify(own[0])}`);
      // The lines are about what was tried: an edit clears them.
      await username.fill("e2e-proxy-user");
      await until(async () => (await lines.count()) === 0, { what: "an edit to clear the Test's lines" });

      await saveAndWait();
      const stored = await user.api("/api/admin/proxy");
      for (const [key, value] of Object.entries({ mode: "manual", server: "http://127.0.0.1:1", username: "e2e-proxy-user", password_set: true, proxy_dns: false })) {
        assertEq(stored[key], value, `the server stored ${key}`);
      }
      assert(!(await (await user.api("/api/admin/proxy", { raw: true })).text()).includes("e2e-proxy-secret"), "the password never comes back");

      // Reloaded, the pane reads them back; the password stays on the server.
      await page.reload();
      await page.waitForSelector(".folderNewBtn");
      await openSettings(page);
      await nav(page, "Network proxy").click();
      await address.waitFor();
      assert(await pressed("Manual"), "Manual, as saved");
      assertEq(await address.inputValue(), "http://127.0.0.1:1");
      assertEq(await username.inputValue(), "e2e-proxy-user");
      assertEq(await password.inputValue(), "", "the password is never shown");
      assertEq(await password.getAttribute("placeholder"), "password set — type to replace");
      // The stored password goes with this user name only: without one, the box asks for a password.
      await username.fill("");
      await until(async () => (await password.getAttribute("placeholder")) === "Password", { what: "an emptied user name to drop the kept password's placeholder" });
      await username.fill("e2e-proxy-user");
      // An edited list offers its defaults back; put back, nothing is left to save.
      await bypass.fill(`${before.default_bypass}, example.org`);
      await save.waitFor();
      await reset.click();
      assertEq(await bypass.inputValue(), before.default_bypass, "Reset to defaults puts the list back");
      await reset.waitFor({ state: "detached" });
      await save.waitFor({ state: "detached" });

      // Back to the system's settings: the manual rows go, and the proxy
      // typed under Manual stays stored for the next time.
      await mode("Use system settings").click();
      await address.waitFor({ state: "detached" });
      await saveAndWait();
      const back = await user.api("/api/admin/proxy");
      assertEq(back.mode, "system", "the system's settings again");
      assertEq(back.server, "http://127.0.0.1:1", "the manual proxy is kept");
      assertNoProblems(page, [/PUT \/api\/admin\/proxy -> 400/]);
    } finally {
      await ctx.close();
      site.closeAllConnections();
      await new Promise((resolve) => site.close(resolve));
      if (admin) {
        try {
          await user.api("/api/admin/proxy", { method: "PUT", body: { mode: "system", server: "", username: "", proxy_dns: false } });
        } finally { await server.manage("set-admin", "settings-user", "off"); }
      }
    }
  });

  // Settings → Workspaces → Data: the export and the merge are background
  // jobs (docs/dev/tasks.md) — the export's file downloads by itself once
  // it is ready, a merge into the open workspace reloads the library.
  const openRow = (page) => page.locator(".settingsPane .aiProvRow").filter({ has: page.locator(".uiTag", { hasText: "open" }) }).first();
  await step("settings: a workspace export runs in Background tasks and downloads itself", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Workspaces").click();
      await openRow(page).getByRole("button", { name: "Data", exact: true }).click();
      const download = page.waitForEvent("download", { timeout: 30000 });
      await page.locator(".ctxMenuItem", { hasText: "Export databases only (.zip)" }).click();
      const file = await download;
      assert(/^gamma-export-db-.*\.zip$/.test(file.suggestedFilename()), `export name: ${file.suggestedFilename()}`);
      assertEq(fs.readFileSync(await file.path()).subarray(0, 2).toString(), "PK");
      await page.click("button[aria-label='Background tasks']");
      const row = page.locator(".taskRow", { hasText: "Database export of" }).first();
      await row.locator(".taskStatus.done").waitFor();
      assert((await row.innerText()).includes("gamma-export-db-"), "the finished row names its file");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // Settings → Workspaces → Manage → Delete…: a background job
  // (workspace-delete) that keeps a final copy first. The dialog goes back
  // to the list once the start is answered; the row leaves once the rows go.
  await step("settings: deleting a workspace runs in Background tasks, and its row leaves the list", async () => {
    const doomed = await user.api("/api/workspaces", { method: "POST", body: { name: "Doomed lab" } });
    await user.api(`/api/blocks?ws=${doomed.id}`, { method: "POST", body: { parent_id: "root", content: "In the final copy" } });
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Workspaces").click();
      const doomedRow = page.locator(".settingsPane .aiProvRow").filter({ hasText: "Doomed lab" });
      await doomedRow.getByRole("button", { name: "Manage" }).click();
      await page.getByRole("button", { name: "Delete…", exact: true }).click();
      await page.locator(".confirmModal").getByRole("button", { name: "Delete", exact: true }).click();
      // the row goes as the rows do, a moment before the job ends (its files go last)
      await doomedRow.waitFor({ state: "detached", timeout: 30000 });
      await until(() => user.api("/api/jobs").then(({ jobs }) => jobs.some((j) => j.kind === "workspace-delete"
        && j.params.ws === doomed.id && j.state === "done")), { what: "the delete job done" });
      assertEq((await user.api(`/api/workspaces/${doomed.id}`, { raw: true })).status, 404);
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await page.click("button[aria-label='Background tasks']");
      await page.locator(".taskRow", { hasText: "Delete “Doomed lab”" }).locator(".taskStatus.done").waitFor();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // A workspace being deleted takes no more changes (docs/dev/workspaces.md
  // "Deleting a workspace"): the server refuses an edit with a 423 that names
  // it, and the tab turns read-only and says so on a pill that stays. The
  // refusal is routed in, in the server's shape: a real delete's final copy
  // of a small workspace is over before a step could type into it.
  await step("settings: an edit refused because the workspace is being deleted turns the tab read-only", async () => {
    const ws = (await user.api("/api/session")).default_workspace;
    const doc = await user.api("/api/pages", { method: "POST", body: { title: "Frozen notes" } });
    await user.api("/api/blocks", { method: "POST", body: { parent_id: doc.id, content: "Written before the delete" } });
    const { ctx, page } = await setup(undefined, (context) => context.route("**/api/pages/*/ops", (route) => (
      route.request().method() !== "POST" ? route.fallback() : route.fulfill({
        status: 423, headers: { "X-Gamma-Workspace-Deleting": ws },
        json: { detail: "“Home” is being deleted and takes no more changes", error: "workspace_deleting", workspace: ws },
      }))));
    try {
      await page.goto(`${server.base}/?block=${doc.id}&ws=${ws}`);
      await editRow(page, "Written before the delete");
      await page.keyboard.type(" and after");
      await closeEditor(page);
      await page.locator(".statusPill", { hasText: "This workspace is being deleted, so changes are no longer saved." })
        .getByRole("button", { name: "Reload", exact: true }).waitFor();
      // read-only now: the refused edit went from the screen, and a note opens no editor
      await page.locator(".blockRow", { hasText: "Written before the delete and after" }).waitFor({ state: "detached" });
      await page.locator(".blockRow", { hasText: "Written before the delete" }).first().locator(".blockBody").click();
      await sleep(300);
      assertEq(await page.locator(".blockEditorCm").count(), 0, "no editor opens in a workspace being deleted");
      assert(same(await tree(user, doc.id), [{ content: "Written before the delete", children: [] }]), "nothing was saved");
      assertNoProblems(page, [/\/ops -> 423$/, /status of 423/]);
    } finally { await ctx.close(); }
  });

  // Settings → Maintenance: the batch metadata lookup is a background job
  // (kind metadata) — closing Settings right after starting it does not stop
  // it. Note pages that carry a record but no PDF text: their lookup finds
  // nothing without asking the network, so both count as failed.
  await step("settings: Maintenance's metadata lookup runs in Background tasks after Settings closes", async () => {
    const titles = ["Batch lookup paper A", "Batch lookup paper B"];
    for (const [i, title] of titles.entries()) {
      const block = await user.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: title } });
      // A was looked up at a known time; B predates the stamp
      const meta = { title, source: "ai", ...(i === 0 ? { at: "2025-03-04T09:30:00Z" } : {}) };
      await user.api(`/api/blocks/${block.id}`, { method: "PUT", body: { properties: { meta } } });
    }
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Maintenance").click();
      // the Fetched column: the day (with the year, not this one), the time on hover
      const fetched = page.locator(".metaStatRow", { hasText: titles[0] }).locator(".metaCell").nth(1);
      assert(/2025/.test(await fetched.innerText()), `fetched cell: ${await fetched.innerText()}`);
      assert((await fetched.getAttribute("title")).startsWith("Fetched "), `hover: ${await fetched.getAttribute("title")}`);
      assertEq((await page.locator(".metaStatRow", { hasText: titles[1] }).locator(".metaCell").nth(1).innerText()).trim(), "—");
      for (const title of titles) {
        await page.locator(".metaStatRow", { hasText: title }).locator("input[type=checkbox]").check();
      }
      await page.getByRole("button", { name: "Fetch selected", exact: true }).click();
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await page.click("button[aria-label='Background tasks']");
      const row = page.locator(".taskRow", { hasText: "Metadata lookup for 2 papers" }).first();
      await row.locator(".taskStatus.done").waitFor({ timeout: 30000 });
      const job = (await user.api("/api/jobs")).jobs.find((j) => j.kind === "metadata");
      assertEq(JSON.stringify((await user.api(`/api/jobs/${job.id}`)).result), JSON.stringify({ papers: 2, fetched: 0, failed: 2 }));
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: a backup merged in from Workspaces runs as a job and the library reloads with it", async () => {
    const donor = await user.api("/api/workspaces", { method: "POST", body: { name: "Merge donor" } });
    await user.api(`/api/blocks?ws=${donor.id}`, { method: "POST", body: { parent_id: "root", content: "Merged in by a job" } });
    const zip = Buffer.from(await (await user.api(`/api/export?ws=${donor.id}&uploads=0`, { raw: true })).arrayBuffer());
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Workspaces").click();
      await openRow(page).getByRole("button", { name: "Data", exact: true }).click();
      const chooser = page.waitForEvent("filechooser");
      await page.locator(".ctxMenuItem", { hasText: "Merge a backup into it…" }).click();
      await (await chooser).setFiles({ name: "donor.zip", mimeType: "application/zip", buffer: zip });
      const reloaded = page.waitForEvent("load", { timeout: 30000 });
      await page.locator(".confirmModal").getByRole("button", { name: "Merge", exact: true }).click();
      await reloaded;
      await page.waitForSelector(".folderNewBtn");
      await page.getByText("Merged in by a job", { exact: true }).first().waitFor();
      const merged = (await user.api("/api/jobs")).jobs.find((job) => job.kind === "restore" && job.params.filename === "donor.zip");
      assertEq(merged?.state, "done");
      assertEq(merged.params.mode, "merge");
    } finally { await ctx.close(); }
  });

  // Manage → Duplicate… (docs/dev/workspaces.md "Copies between workspaces"):
  // a new personal workspace with a copy of every page, made by a job; the
  // dialog offers the copy's name, and the pill says it is done and opens it.
  // The row's Data menu no longer has it.
  await step("settings: Duplicate… copies every page into a new personal workspace, which the pill opens", async () => {
    const source = await user.api("/api/workspaces", { method: "POST", body: { name: "Dup source" } });
    await user.api(`/api/blocks?ws=${source.id}`, { method: "POST", body: { parent_id: "root", content: "Duplicated page" } });
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Workspaces").click();
      const sourceRow = page.locator(".settingsPane .aiProvRow").filter({ hasText: "Dup source" });
      await sourceRow.getByRole("button", { name: "Data", exact: true }).click();
      await page.locator(".ctxMenuItem", { hasText: "Export everything (.zip)" }).waitFor();
      assertEq(await page.locator(".ctxMenuItem", { hasText: "Duplicate…" }).count(), 0, "the Data menu has no Duplicate…");
      await page.keyboard.press("Escape");
      await sourceRow.getByRole("button", { name: "Manage", exact: true }).click();
      await page.getByRole("button", { name: "Duplicate…", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Duplicate “Dup source”", exact: true });
      await dialog.waitFor();
      assertEq(await dialog.getByRole("textbox").inputValue(), "Dup source (copy)");
      await dialog.getByRole("button", { name: "Duplicate", exact: true }).click();
      const pill = page.locator(".statusPill", { hasText: "Duplicated “Dup source” as “Dup source (copy)”." });
      await pill.waitFor({ timeout: 30000 });
      const job = (await user.api("/api/jobs")).jobs.find((j) => j.kind === "workspace-copy" && j.params.ws === source.id);
      assertEq(job?.state, "done");
      const made = (await user.api(`/api/jobs/${job.id}`)).result.workspace;
      assertEq((await user.api(`/api/workspaces/${made}`)).name, "Dup source (copy)");
      const reloaded = page.waitForEvent("load", { timeout: 30000 });
      await pill.getByRole("button", { name: "Open", exact: true }).click();
      await reloaded;
      await page.waitForSelector(".folderNewBtn");
      assertEq(new URL(page.url()).searchParams.get("ws"), made);
      await page.getByText("Duplicated page", { exact: true }).first().waitFor();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: a backup past one request's size goes up in parts and merges as a job", async () => {
    // A proxy in front of the server caps a request's body (Cloudflare at
    // 100 MB): past 32 MiB the zip goes in parts (/jobs/restore/parts).
    const donor = await user.api("/api/workspaces", { method: "POST", body: { name: "Big merge donor" } });
    await user.api(`/api/blocks?ws=${donor.id}`, { method: "POST", body: { parent_id: "root", content: "Merged in parts" } });
    const file = path.join(server.dir, "big-donor.zip");
    fs.writeFileSync(file, Buffer.from(await (await user.api(`/api/export?ws=${donor.id}&uploads=0`, { raw: true })).arrayBuffer()));
    // 40 MB the restore passes over: it reads pages.db, data.db and uploads/
    execFileSync(PYTHON, ["-c", "import os, sys, zipfile; zipfile.ZipFile(sys.argv[1], 'a').writestr('padding.bin', os.urandom(40 << 20))", file]);
    const { ctx, page } = await setup();
    try {
      const posts = [];
      page.on("request", (request) => { if (request.method() === "POST") posts.push(new URL(request.url()).pathname); });
      await openSettings(page);
      await nav(page, "Workspaces").click();
      await openRow(page).getByRole("button", { name: "Data", exact: true }).click();
      const chooser = page.waitForEvent("filechooser");
      await page.locator(".ctxMenuItem", { hasText: "Merge a backup into it…" }).click();
      await (await chooser).setFiles(file);
      const reloaded = page.waitForEvent("load", { timeout: 60000 });
      await page.locator(".confirmModal").getByRole("button", { name: "Merge", exact: true }).click();
      await reloaded;
      assertEq(posts.filter((p) => /^\/api\/jobs\/restore\/parts\/[0-9a-f]+$/.test(p)).length, 2, "40 MB goes up as two parts");
      assert(!posts.includes("/api/jobs/restore"), "never as one request");
      await page.waitForSelector(".folderNewBtn");
      await page.getByText("Merged in parts", { exact: true }).first().waitFor();
      const merged = (await user.api("/api/jobs")).jobs.find((job) => job.kind === "restore" && job.params.filename === "big-donor.zip");
      assertEq(merged?.state, "done");
    } finally { await ctx.close(); }
  });

  await step("settings: prompt and connection drafts have save, cancel, and dismissal protection", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await search(page, "custom prompts", "Prompts");
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
      await search(page, "custom prompts", "Prompts");
      await page.getByRole("button", { name: /Chat system prompt/ }).click();
      assertEq(await page.locator(".promptTextarea").first().inputValue(), "Saved test prompt");
      await nav(page, "Models").click();
      await page.getByRole("button", { name: "Add provider", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Connect an AI service", exact: true });
      await dialog.getByRole("group", { name: "On your computer or network", exact: true })
        .getByRole("button", { name: "Custom endpoint", exact: true }).click();
      await dialog.getByRole("button", { name: "API protocol", exact: true }).waitFor();
      assertEq(await dialog.getByRole("link", { name: /^Get a key at/ }).count(), 0, "a custom endpoint has no key link");
      await dialog.getByRole("textbox", { name: /Base URL/ }).fill("https://example.invalid");
      await dialog.getByRole("group", { name: "API key", exact: true }).getByRole("button", { name: "OpenAI API", exact: true }).click();
      assertEq(await dialog.getByRole("textbox", { name: /Base URL/ }).count(), 0);
      await dialog.getByText("More options — name, test model, billing", { exact: true }).click();
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

  // Recording a call is test_ai_usage.py (the suite makes no AI calls): here the empty state.
  await step("settings: the Usage pane shows an account with no AI calls: no spend, empty bars, and Reset is off", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await search(page, "spend", "Usage");
      const chart = page.locator(".settingsPane .usageChart");
      await chart.waitFor();
      const tile = (label) => page.locator(".settingsPane .setStat").filter({ has: page.locator(".setStatLabel", { hasText: new RegExp(`^${label}$`) }) });
      assertEq(await tile("Est. API spend").locator(".setStatNum").innerText(), "—", "nothing priced is no spend, never $0");
      assertEq(await tile("Tokens").locator(".setStatNum").innerText(), "0");
      assert((await tile("Tokens").innerText()).includes("0 calls"));
      assertEq(await chart.getByRole("button", { name: "Reset", exact: true }).isDisabled(), true);
      const usage = await user.api("/api/ai/usage");
      assertEq(usage.windows.all.calls, 0);
      assertEq((await user.api("/api/ai/usage", { method: "DELETE" })).deleted, 0);
      assertEq(await chart.locator('.usageChartBar[data-value="0"]').count(), 30, "every daily bar is empty");
      assertEq(await chart.locator(".usageChartDetail span").innerText(), "No AI calls in this period");
      assertEq(await page.getByRole("table", { name: "Usage by model" }).count(), 0, "no table of nothing");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // What the calls cost (gamma/ai_usage.py cost_of, summary; its arithmetic
  // is test_ai_usage.py's): only calls paid per token are in dollars, a
  // subscription's say so, and a pay-per-token call with no public price is
  // counted, never $0. The answer is faked: no call here has a price.
  await step("settings: Usage estimates the spend of priced pay-per-token calls, says Subscription for a sign-in's, and counts a call without a price", async () => {
    const counts = (extra = {}) => ({ calls: 0, input: 0, output: 0, cache_read: 0, cache_write: 0, cost: 0, priced_calls: 0, unpriced_calls: 0, ...extra });
    const priced = counts({ calls: 2, input: 30000, output: 2000, cost: 0.123, priced_calls: 2 });
    const unpriced = counts({ calls: 1, input: 4000, output: 500, unpriced_calls: 1 });
    const signIn = counts({ calls: 3, input: 50000, output: 4000 });
    const split = (...parts) => parts.reduce((sum, part) => Object.fromEntries(Object.keys(sum).map((k) => [k, sum[k] + part[k]])));
    const api = split(priced, unpriced);
    const chat = { ...split(api, signIn), billing: {
      api: { calls: 3, input: 34000, output: 2500, cache_read: 0, cache_write: 0, cost: 0.123 },
      subscription: { calls: 3, input: 50000, output: 4000, cache_read: 0, cache_write: 0, cost: 0 } } };
    const { ctx, page } = await setup(undefined, async (context) => {
      await context.route((url) => url.pathname === "/api/ai/usage", async (route) => {
        const data = await (await route.fetch()).json();
        await route.fulfill({ json: { ...data, totals: chat, kinds: { chat }, models: [
          { provider_id: "signinAA", provider_name: "ChatGPT", model: "gpt-e2e", ...signIn, billing: "subscription" },
          { provider_id: "workkeyA", provider_name: "Work key", model: "claude-e2e", ...priced, billing: "api" },
          { provider_id: "workkeyA", provider_name: "Work key", model: "mystery-e2e", ...unpriced, billing: "api" },
        ] } });
      });
    });
    try {
      await openSettings(page);
      await nav(page, "Usage").click();
      const tile = (label) => page.locator(".settingsPane .setStat").filter({ has: page.locator(".setStatLabel", { hasText: new RegExp(`^${label}$`) }) });
      await tile("Est. API spend").waitFor();
      assertEq(await tile("Est. API spend").locator(".setStatNum").innerText(), "$0.123", "the priced calls' estimate");
      assert((await tile("Est. API spend").innerText()).includes("1 call without a price"), "the unpriced call counted");
      const plan = await tile("Subscriptions").innerText();
      assert(plan.includes("54k tokens") && plan.includes("3 calls") && !plan.includes("$"), `the sign-in's tokens, no dollars: ${plan}`);
      const table = page.getByRole("table", { name: "Usage by model" });
      const costOf = async (model) => (await table.getByRole("row").filter({ hasText: model }).getByRole("cell").last().innerText()).trim();
      assertEq(await costOf("claude-e2e"), "$0.123");
      assertEq(await costOf("gpt-e2e"), "Subscription", "a sign-in's calls are its plan's, never dollars");
      assertEq(await costOf("mystery-e2e"), "—", "no public price: unknown, never $0");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: chat shortcuts update global model, context, and tool preferences", async () => {
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Models").click();
      // The chat's model is the account's: Models › Chat and the composer's
      // chip set the same one.
      const chatModel = () => row(page, "Chat").getByRole("button", { name: "Chat", exact: true });
      await chatModel().click();
      await page.getByRole("listbox", { name: "Chat", exact: true }).getByRole("option", { name: /^test-model-b/ }).click();
      await until(async () => (await chatModel().innerText()) === "test-model-b", { what: "the row on the pick" });
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      // The model (with its reasoning effort and speed) is the composer's chip:
      // the model in use and the chat models, then every connection's under All models….
      const modelChip = page.getByRole("button", { name: "Model, reasoning effort and speed", exact: true });
      assert((await modelChip.innerText()).includes("test-model-b"));
      await modelChip.click();
      await page.locator(".modelPanelPick").click();
      await page.locator(".modelPanel").getByRole("button", { name: "All models…", exact: true }).click();
      await page.getByRole("listbox", { name: "All models", exact: true }).getByRole("option", { name: /^test-model-a/ }).click();
      await until(async () => (await modelChip.innerText()).includes("test-model-a"), { what: "the chip on the pick" });
      await until(async () => (await user.api("/api/ai/models")).roles.chat.pick.endsWith(":test-model-a"), { what: "the account's chat model" });
      await page.locator('[title^="Chat settings"]').click();
      const popover = page.locator(".chatSettingsPop");
      await popover.locator('input[type="number"]').fill("42000");
      await popover.locator('input[type="number"]').press("Tab");
      await popover.getByRole("checkbox", { name: "Tools", exact: true }).uncheck();
      await page.locator('[title^="Chat settings"]').click();
      await openSettings(page);
      await nav(page, "Models").click();
      assertEq(await chatModel().innerText(), "test-model-a", "the chip's pick is the row's");
      await nav(page, "Tool usage").click();
      assertEq(await page.getByRole("checkbox", { name: "Assistant tools" }).isChecked(), false);
      await nav(page, "Chat").click();
      // The chat's two model knobs have their defaults here (the chip edits
      // the same preferences — docs/dev/settings.md "Chat settings are global").
      await row(page, "Default reasoning effort").waitFor();
      assertEq(await page.getByRole("checkbox", { name: "Fast", exact: true }).isChecked(), false);
      assertEq(await row(page, "Single paper").locator('input[type="number"]').inputValue(), "42000");
      await nav(page, "Tool usage").click();
      await page.getByRole("checkbox", { name: "Assistant tools" }).check();
      // The matrix and chat popover edit the same per-kind permissions.
      const pick = async (scope, name, state) => {
        await scope.getByRole("button", { name, exact: true }).click();
        await page.locator(".uiSelectMenu").getByRole("menuitemradio", { name: state, exact: true }).click();
      };
      await pick(page, "Rename pages — Folder chat", "Off");
      await pick(page, "Use journal sign-ins — Folder chat", "Off");
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await page.locator('[title^="Chat settings"]').click();
      const state = (name) => popover.getByRole("button", { name, exact: true }).innerText();
      assertEq(await popover.getByRole("checkbox", { name: "Tools", exact: true }).isChecked(), true);
      assertEq(await state("Rename pages"), "Off");
      assertEq(await state("Read pages"), "Allow");
      assertEq(await state("Move pages"), "Ask", "changes ask unless set otherwise");
      assertEq(await state("Use journal sign-ins"), "Off");
      // And back: the popover's choice is the one Settings shows.
      await pick(popover, "Rename pages", "Allow");
      await page.locator('[title^="Chat settings"]').click();
      await openSettings(page);
      await nav(page, "Tool usage").click();
      assertEq(await page.getByRole("button", { name: "Rename pages — Folder chat", exact: true }).innerText(), "Allow");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // The chat models (docs/dev/ai.md "The model menu"): Models › Chat models
  // lists the models the composer's chip offers beside the one in use (the
  // account's favorites), Add model… adds one and its × takes it off, so the
  // chip switches between them in one click.
  await step("settings: Chat models are what the chat's model chip switches between: Add model… adds one, its × removes it", async () => {
    const ownId = (await user.api("/api/ai/settings")).providers.find((p) => p.name === "Test connection").id;
    const [a, b] = [`${ownId}:test-model-a`, `${ownId}:test-model-b`];
    const favorites = async () => (await user.api("/api/ai/models")).favorites;
    await user.api("/api/ai/models/roles", { method: "PUT", body: { chat: a, favorites_remove: await favorites() } });
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Models").click();
      const list = row(page, "Chat models");
      await list.getByText("Add the models you switch between", { exact: false }).waitFor();
      await list.getByRole("button", { name: "Add a chat model", exact: true }).click();
      await page.getByRole("listbox", { name: "Add a chat model", exact: true }).getByRole("option", { name: /^test-model-b/ }).click();
      await list.getByRole("listitem").filter({ hasText: "test-model-b" }).waitFor();      await until(async () => JSON.stringify(await favorites()) === JSON.stringify([b]), { what: "the account's chat models" });
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      // The chip's panel offers the model in use and the chat models, a click apart.
      const modelChip = page.getByRole("button", { name: "Model, reasoning effort and speed", exact: true });
      await modelChip.click();
      await page.locator(".modelPanelPick").click();
      const offered = page.locator(".modelPanel").getByRole("group", { name: "Select model", exact: true }).locator(".modelMenuName");
      assertEq((await offered.allInnerTexts()).join(), "test-model-a,test-model-b", "the panel lists the one in use, then the chat models");
      await offered.filter({ hasText: "test-model-b" }).click();
      await page.locator(".modelPanelPick", { hasText: "test-model-b" }).waitFor(); // back where its effort is set
      await page.keyboard.press("Escape");
      await until(async () => (await modelChip.innerText()).includes("test-model-b"), { what: "the chip on the pick" });
      await until(async () => (await user.api("/api/ai/models")).roles.chat.pick === b, { what: "the account's chat model" });
      await openSettings(page);
      await nav(page, "Models").click();
      await row(page, "Chat models").getByRole("button", { name: "Remove test-model-b from the chat models", exact: true }).click();
      await until(async () => (await favorites()).length === 0, { what: "the chat model removed" });
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await user.api("/api/ai/models/roles", { method: "PUT", body: { chat: a, favorites_remove: await favorites() } });
    }
  });

  // A task's model on a connection that goes (docs/dev/ai.md "Models for
  // each task"): the confirm says what the task will use instead. The pick
  // stays and the task runs on the chat model meanwhile: its row is red
  // with Choose…, and a notice (gamma/notices.py, ai-model-metadata) dots
  // the account button and leads to Models. A model chosen in the row ends it.
  await step("settings: a task's model on a removed connection: the confirm names the task, its row warns with Choose…, a notice leads there, and a choice clears it", async () => {
    const own = async () => (await user.api("/api/ai/settings")).providers;
    const ownId = (await own()).find((p) => p.name === "Test connection").id;
    await user.api("/api/ai/models/roles", { method: "PUT", body: { chat: `${ownId}:test-model-a`, metadata: "" } });
    await user.api("/api/ai/providers", { method: "POST", body: {
      protocol: "openai", name: "Second connection", api_key: "test-second-only", base_url: server.base, models: "second-model" } });
    const secondId = (await own()).find((p) => p.name === "Second connection").id;
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Models").click();
      const metadata = row(page, "Metadata & citations");
      const menu = () => metadata.getByRole("button", { name: "Metadata & citations", exact: true });
      const choices = () => page.getByRole("listbox", { name: "Metadata & citations", exact: true });
      const hint = () => metadata.locator(".settingDesc").innerText();
      await menu().click();
      await choices().getByRole("group", { name: /^Second connection/ }).getByRole("option", { name: /^second-model/ }).click();
      await until(async () => (await user.api("/api/ai/models")).roles.metadata.pick === `${secondId}:second-model`,
        { what: "the task on the second connection" });
      await until(async () => (await hint()) === "Second connection · Pay per token", { what: "the row names the connection" });
      // Removing the connection says what the task will use instead.
      const card = page.locator(".settingsPane .aiProvRow").filter({ hasText: "Second connection" });
      await card.getByRole("button", { name: "Remove connection", exact: true }).click();
      const confirm = page.locator(".confirmModal");
      assertEq(await confirm.locator(".confirmMessage").innerText(),
        'Remove the "Second connection" connection? This cannot be undone. Metadata & citations will use test-model-a (Test connection) instead.');
      await confirm.getByRole("button", { name: "Remove", exact: true }).click();
      await until(async () => (await card.count()) === 0, { what: "the connection gone" });
      const warned = async () => {
        await until(async () => (await menu().innerText()) === "Choose…", { what: "the row's button asks for another model" });
        assertEq(await hint(), "second-model (a removed connection) is no longer available. Using test-model-a (Test connection) instead.");
        const [color, danger] = await metadata.locator(".settingDesc").evaluate((el) => {
          const probe = document.createElement("span");
          probe.style.color = "var(--danger)";
          el.append(probe);
          const colors = [getComputedStyle(el).color, getComputedStyle(probe).color];
          probe.remove();
          return colors;
        });
        assertEq(color, danger, "the warning in red");
      };
      await warned();
      assertEq((await user.api("/api/ai/models")).roles.metadata.state, "missing", "the pick kept, no longer offered");
      // The notice: the account button's dot, the menu's row, and Settings… on Models.
      assertEq((await user.api("/api/notices")).notices.map((n) => n.id).join(), "ai-model-metadata", "the metadata model's notice");
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor({ state: "detached" });
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      const account = page.getByRole("button", { name: "Account & settings", exact: true });
      await account.locator(".noticeDot").waitFor();
      assertEq(await account.locator(".noticeDot").getAttribute("data-tone"), "warn");
      await account.click();
      const notices = page.getByTestId("account-notices");
      await notices.waitFor();
      assert((await notices.textContent()).includes("The metadata model second-model is no longer available; the chat model is used instead."),
        await notices.textContent());
      await notices.getByRole("menuitem", { name: /Choose model/ }).waitFor();
      await page.getByRole("menuitem", { name: "Settings…", exact: true }).click();
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
      assertEq(await nav(page, "Models").getAttribute("aria-current"), "page", "Settings… opens Models");
      await warned();
      // A model chosen in the row ends the warning.
      await menu().click();
      await choices().getByRole("option", { name: /^test-model-b/ }).click();
      await until(async () => (await menu().innerText()) === "test-model-b", { what: "the row on the new model" });
      assertEq((await metadata.getAttribute("class")).includes("aiTaskMissing"), false, "no longer red");
      assertEq(await hint(), "Test connection · Pay per token");
      // The row shows a pick before its PUT lands (useAiModels writes it at once).
      await until(async () => JSON.stringify((await user.api("/api/ai/models")).roles.metadata)
        === JSON.stringify({ pick: `${ownId}:test-model-b`, model: `${ownId}:test-model-b`, state: "ok" }),
        { what: "the task on test-model-b, saved" });
      assertEq((await user.api("/api/notices")).notices.length, 0, "nothing left to tell");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await user.api("/api/ai/models/roles", { method: "PUT", body: { chat: "", metadata: "" } });
      for (const p of (await own()).filter((p) => p.name === "Second connection")) {
        await user.api(`/api/ai/providers/${p.id}`, { method: "DELETE" });
      }
    }
  });

  await step("settings: tool permissions compare chat types, preserve choices, and fit narrow screens", async () => {
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
      const { ctx, page } = await setup(viewport);
      try {
        await openSettings(page);
        if (viewport.width < 600) await page.getByRole("button", { name: "Back", exact: true }).click();
        await nav(page, "Tool usage").click();
        const master = page.getByRole("checkbox", { name: "Assistant tools", exact: true });
        const matrix = page.getByRole("group", { name: "Tool permissions by chat type" });
        // Each cell is a state menu (Allow / Ask / Off) named "<tool> — <chat kind>".
        const permission = (tool, kind = "Folder chat") => matrix.getByRole("button", { name: `${tool} — ${kind}`, exact: true });
        const stateOf = (tool, kind) => permission(tool, kind).innerText();
        const menu = page.locator(".uiSelectMenu");
        const choose = async (tool, kind, state) => {
          await permission(tool, kind).click();
          await menu.getByRole("menuitemradio", { name: state, exact: true }).click();
        };
        const preset = (kind) => matrix.getByRole("button", { name: `${kind} permissions`, exact: true });
        const choosePreset = async (kind, label) => {
          await preset(kind).click();
          await menu.getByRole("menuitemradio", { name: label, exact: true }).click();
        };
        await master.check();
        for (const kind of ["Folder chat", "PDF chat", "Notes chat"]) await choosePreset(kind, "Allow all");
        assertEq(await stateOf("Rename pages"), "Allow");
        assertEq(await permission("Rename pages", "PDF chat").count(), 0);
        // The deletions take every state, Allow all allows them, and they are a folder chat's alone.
        assertEq(await stateOf("Organize folders and labels"), "Allow");
        assertEq(await stateOf("Delete folders and labels"), "Allow", "Allow all allows deleting too");
        assertEq(await stateOf("Delete pages"), "Allow");
        assertEq(await preset("Folder chat").innerText(), "Allow all");
        for (const tool of ["Delete folders and labels", "Delete pages"]) {
          assertEq(await permission(tool, "PDF chat").count(), 0, `${tool} is a folder chat's`);
          await permission(tool).click();
          assertEq(JSON.stringify(await menu.locator(".ctxMenuItem").allInnerTexts()), JSON.stringify(["Allow", "Ask", "Off"]));
          await page.keyboard.press("Escape");
          await menu.waitFor({ state: "detached" });
        }
        assertEq(await permission("List pages", "Notes chat").count(), 0);
        await choosePreset("Folder chat", "Ask before changes");
        for (const tool of ["Rename pages", "Move pages", "Edit note blocks"]) {
          assertEq(await stateOf(tool), "Ask", `${tool} asks before changes`);
        }
        assertEq(await stateOf("Fetch documents"), "Allow");

        await choosePreset("Folder chat", "Read library");
        for (const tool of ["Search online", "Fetch documents", "Use journal sign-ins", "Edit note blocks"]) {
          assertEq(await stateOf(tool), "Off", `${tool} is off in read-library mode`);
        }
        assertEq(await stateOf("Read pages"), "Allow");
        assertEq(await stateOf("Fetch documents", "PDF chat"), "Allow");
        await choose("Fetch documents", "Folder chat", "Allow");
        assertEq(await permission("Use journal sign-ins").isDisabled(), false);
        // Journal sign-ins are part of fetching: on or off, nothing to ask.
        await permission("Use journal sign-ins").click();
        assertEq(JSON.stringify(await menu.locator(".ctxMenuItem").allInnerTexts()), JSON.stringify(["Allow", "Off"]));
        await menu.getByRole("menuitemradio", { name: "Allow", exact: true }).click();
        await choose("Fetch documents", "Folder chat", "Off");
        assertEq(await permission("Use journal sign-ins").isDisabled(), true);
        assertEq(await stateOf("Use journal sign-ins"), "Allow", "dependent choices are remembered");
        await choose("Fetch documents", "Folder chat", "Allow");
        await choose("Use journal sign-ins", "Folder chat", "Off");
        assertEq(await preset("Folder chat").innerText(), "Custom");
        await choosePreset("PDF chat", "Read & search");
        assertEq(await stateOf("Edit note blocks", "PDF chat"), "Off");
        assertEq(await stateOf("Search online", "PDF chat"), "Allow");

        await master.uncheck();
        assertEq(await preset("Folder chat").isDisabled(), true);
        assertEq(await permission("Read pages").isDisabled(), true);
        await master.check();
        assertEq(await stateOf("Use journal sign-ins"), "Off");
        assertEq(await stateOf("Use journal sign-ins", "PDF chat"), "Allow");
        const fits = await matrix.evaluate((el) => ({ width: el.clientWidth, scroll: el.scrollWidth }));
        assert(fits.scroll <= fits.width + 1, `tool permissions fit ${viewport.width}px: ${JSON.stringify(fits)}`);
        // Every menu has a full accessible name and opens from the keyboard.
        await permission("Rename pages").focus();
        await page.keyboard.press("Enter");
        await menu.waitFor();
        await page.keyboard.press("Escape");
        await menu.waitFor({ state: "detached" });
        if (flags.keep) {
          await row(page, "Tools").scrollIntoViewIfNeeded();
          await page.screenshot({ path: `${server.dir}/tool-permissions-${viewport.width}.png`, animations: "disabled" });
          await permission("Use journal sign-ins").scrollIntoViewIfNeeded();
          await page.screenshot({ path: `${server.dir}/tool-permissions-web-${viewport.width}.png`, animations: "disabled" });
        }
        assertNoProblems(page);
      } finally { await ctx.close(); }
    }
  });

  await step("settings: Unlimited rounds greys out the round limit and keeps the number for when it is off", async () => {
    const { ctx, page } = await setup();
    const rounds = row(page, "Tool rounds").locator('input[type="number"]');
    const unlimited = page.getByRole("checkbox", { name: "Unlimited rounds", exact: true });
    const saved = (want) => until(async () => {
      const { value } = await user.api("/api/prefs/profile");
      return Object.entries(want).every(([name, v]) => value?.[name] === v);
    }, { what: `the profile holds ${JSON.stringify(want)}` });
    try {
      await openSettings(page);
      await nav(page, "Tool usage").click();
      await rounds.fill("12");
      await rounds.press("Tab");
      await saved({ toolRounds: 12 });
      await unlimited.check();
      assertEq(await rounds.isDisabled(), true, "the number waits while Unlimited is on");
      await saved({ toolRoundsUnlimited: true });
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await openSettings(page);
      await nav(page, "Tool usage").click();
      assertEq(await unlimited.isChecked(), true, "Unlimited stays on");
      await unlimited.uncheck();
      assertEq(await rounds.isDisabled(), false);
      assertEq(await rounds.inputValue(), "12", "the number comes back");
      await rounds.fill("32");
      await rounds.press("Tab");
      await saved({ toolRounds: 32, toolRoundsUnlimited: false });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: the composer's mic shows only when a connection can transcribe; the PDF switch only with a PDF in context", async () => {
    for (const transcribe of [false, true]) {
      const { ctx, page } = await setup(undefined, (c) => fakeAiModels(c, { ...FAKE_AI_MODELS, transcribe }));
      try {
        const chip = page.getByRole("button", { name: "Model, reasoning effort and speed", exact: true });
        await chip.waitFor();
        assertEq(await page.getByRole("button", { name: "Start dictation" }).count(), transcribe ? 1 : 0, `mic with transcribe=${transcribe}`);
        assertEq(await page.getByRole("button", { name: "PDF", exact: true }).count(), 0, "no PDF in the library's context");
        await chip.click();
        const effort = page.locator(".modelPanel").getByRole("slider", { name: "Reasoning effort", exact: true });
        await effort.press("End");
        await chip.locator(".chatModelEffort", { hasText: "High" }).waitFor();
        await page.locator(".modelPanel").getByRole("button", { name: "Reset to default", exact: true }).click();
        assertNoProblems(page);
      } finally { await ctx.close(); }
    }
  });

  await step("settings: with no AI connected the chat is a setup card; connecting from it comes back to the chat, ready", async () => {
    // The model list the browser sees flips once the connection is saved
    // (the real server stores it; its model listing and test are faked).
    // The chat's first read of the key list is held, to see the card wait
    // for its tiles.
    let connected = false, modelsAnswered = false, releaseKeys;
    const keysHeld = new Promise((resolve) => { releaseKeys = resolve; });
    let keysAsked = 0;
    const { ctx, page } = await setup(undefined, async (c) => {
      await c.route("**/api/ai/models*", async (route) => {
        await route.fulfill({ json: connected
          ? aiModelsAnswer([{ id: "x:claude-e2e", provider: "x", provider_name: "Anthropic", model: "claude-e2e", billing: "api" }])
          : { enabled: false, models: [], default: "", efforts: ["low", "medium", "high"] } });
        modelsAnswered = true;
      });
      await c.route("**/api/ai/settings", async (route) => {
        if (++keysAsked === 1) await keysHeld;
        await route.continue();
      });
      await c.route("**/api/ai/model-catalog", (route) => route.fulfill({ json: { models: ["claude-e2e", "claude-e2e-mini"] } }));
      await c.route("**/api/ai/providers/*/test", (route) => route.fulfill({ json: { ok: true, model: "claude-e2e", latency_ms: 200 } }));
      await c.route("**/api/ai/providers", (route) => { connected = true; return route.continue(); });
    });
    try {
      // Known to be off, the chat waits for the tiles: no card, and nothing
      // in its place, until it can show the card whole.
      await until(() => modelsAnswered && keysAsked === 1, { what: "the models answered, the key list asked for" });
      await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
      assertEq(await page.locator(".chatSetup").count(), 0, "the card waits for its tiles");
      assertEq(await page.locator(".chatEmpty").count(), 0, "and no empty-state text stands in for it");
      const tiles = page.locator(".chatSetup").getByRole("group", { name: "Connect an AI service" });
      releaseKeys();
      await page.locator(".chatSetup").waitFor();
      assertEq(await tiles.getByRole("button").count(), 4, "ChatGPT, Anthropic, OpenAI API, Other service");
      const input = page.getByRole("combobox", { name: "Message AI" });
      assert(await input.isDisabled(), "a send could only fail");
      assertEq(await input.getAttribute("placeholder"), "Connect an AI service to start chatting");
      assertEq(await page.getByRole("button", { name: "Find in this conversation" }).count(), 0, "no header tools without AI");
      assertEq(await page.getByRole("button", { name: "Start dictation" }).count(), 0, "no mic without AI");
      const anthropic = tiles.getByRole("button", { name: /^Anthropic/ });
      const settings = page.getByRole("dialog", { name: "Settings", exact: true });
      const dialog = page.getByRole("dialog", { name: "Connect an AI service", exact: true });
      const hasFocus = (locator) => locator.evaluate((el) => el.contains(document.activeElement));
      // The tile opens Settings with the connect dialog, which takes the
      // focus; one Escape closes the dialog alone and Settings has it back,
      // a second closes Settings and the tile has it.
      await anthropic.click();
      await dialog.waitFor();
      await until(() => hasFocus(dialog), { what: "the connect dialog has the focus" });
      await page.keyboard.press("Escape");
      await dialog.waitFor({ state: "detached" });
      assert(await settings.isVisible() && await hasFocus(settings), "Settings stays, with the focus");
      await page.keyboard.press("Escape");
      await settings.waitFor({ state: "detached" });
      assert(await anthropic.evaluate((el) => el === document.activeElement), "the tile has the focus back");
      // Settings closed while the dialog is up (an Escape Settings itself
      // takes) leaves no form behind: Connections opens on its list alone.
      await anthropic.click();
      await dialog.waitFor();
      await page.locator(".settingsModal").evaluate((el) => el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
      await settings.waitFor({ state: "detached" });
      await openSettings(page);
      await page.evaluate(() => {
        window.__subDialogs = 0;
        new MutationObserver((records) => {
          for (const r of records) for (const n of r.addedNodes) if (n.nodeType === 1 && n.matches(".subDialog")) window.__subDialogs++;
        }).observe(document.querySelector(".settingsModal"), { childList: true, subtree: true });
      });
      await nav(page, "Models").click();
      await page.locator(".settingsPane").getByText("Test connection").first().waitFor();
      assertEq(await page.evaluate(() => window.__subDialogs), 0, "no connect dialog was drawn on the way");
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      await settings.waitFor({ state: "detached" });
      await anthropic.click();
      await dialog.waitFor();
      assertEq(await dialog.getByRole("group", { name: "API key", exact: true }).getByRole("button", { name: "Anthropic", exact: true }).getAttribute("aria-pressed"), "true", "the dialog is on the tile's service");
      await dialog.locator('input[autocomplete="new-password"]').fill("sk-ant-e2e-key-4242");
      await dialog.getByText("Key works · 2 models available", { exact: true }).waitFor();
      await dialog.getByRole("button", { name: "Connect", exact: true }).click();
      // Settings closes and the chat takes the question.
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor({ state: "detached" });
      await page.getByText("Connected — claude-e2e ready", { exact: true }).waitFor();
      await until(() => input.evaluate((el) => el === document.activeElement), { what: "the message box has the focus" });
      assert(!await input.isDisabled(), "the composer is enabled");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      for (const p of (await user.api("/api/ai/settings")).providers.filter((p) => p.key_hint === "…4242")) {
        await user.api(`/api/ai/providers/${p.id}`, { method: "DELETE" });
      }
    }
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
    const { ctx, page } = await setup(undefined, (c) => c.route("**/api/ai/model-info?**", (route) => route.fulfill({
      json: { model: "test-model-a", context_window: 128000, source: "provider", efforts: null, efforts_source: "" } })));
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
      await page.getByRole("button", { name: "Back", exact: true }).click();
      await nav(page, "Appearance").click();
      await page.getByRole("button", { name: "Gray", exact: true }).click();
      assertEq(await page.getByRole("button", { name: "Gray", exact: true }).getAttribute("aria-pressed"), "true");
      assert(!(await page.locator(".settingsPane").evaluate((el) => el.scrollWidth > el.clientWidth + 1)), "appearance fits the phone without horizontal scrolling");
      if (flags.keep) await page.screenshot({ path: `${server.dir}/settings-appearance-mobile.png`, animations: "disabled" });
      await page.setViewportSize({ width: 320, height: 844 });
      for (let i = 0; i < 6; i++) await row(page, "Interface size").getByRole("button", { name: "Larger", exact: true }).click();
      assert(!(await page.locator(".settingsPane").evaluate((el) => el.scrollWidth > el.clientWidth + 1)), "appearance fits a small phone at maximum interface size");
      await row(page, "Interface size").getByRole("button", { name: "Reset", exact: true }).click();
      await page.setViewportSize({ width: 390, height: 844 });
      // the bars' rows, each with its modes and places, fit the phone too
      await page.getByRole("button", { name: "Back", exact: true }).click();
      await nav(page, "Toolbars").click();
      await row(page, "Sync pill").waitFor();
      assert(!(await page.locator(".settingsPane").evaluate((el) => el.scrollWidth > el.clientWidth + 1)), "toolbars fit the phone without horizontal scrolling");
      if (flags.keep) await page.screenshot({ path: `${server.dir}/settings-toolbars-mobile.png`, animations: "disabled" });
      await page.getByRole("button", { name: "Back", exact: true }).click();
      await nav(page, "Keyboard").click();
      await row(page, "Enter makes").waitFor();
      assert((await row(page, "Enter makes").innerText()).includes("Shift+Enter inserts a new line"), "a note by default");
      await row(page, "Enter makes").getByRole("button", { name: "New line", exact: true }).click();
      assert((await row(page, "Enter makes").innerText()).includes("Shift+Enter creates a new note"));
      await row(page, "Enter makes").getByRole("button", { name: "New note", exact: true }).click();
      assert((await row(page, "Enter makes").innerText()).includes("Shift+Enter inserts a new line"));
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
      await page.getByRole("menuitem", { name: "Report a problem…", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Report a problem", exact: true });
      await dialog.waitFor();
      const open = dialog.getByRole("button", { name: "Open GitHub issue", exact: true });
      assert(await open.isDisabled(), "nothing to report yet");
      await dialog.getByLabel("What happened").fill("A blue line stays on the notes\nafter a drag");
      await dialog.getByLabel("How to reproduce").fill("drag a block, drop it outside");
      await dialog.getByText("Preview the report", { exact: true }).click();
      const preview = await dialog.locator("pre").textContent();
      // the lines' format and scrubbing are problemReport.test.mjs; here: this app's facts reach the preview
      assert(preview.includes("**Build:**") && preview.includes("workspace: personal, owner"), `diagnostics in ${preview}`);
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
      // the form's fields are problemReport.test.mjs; here: what was typed and recorded reaches it
      assertEq(url.searchParams.get("title"), "A blue line stays on the notes");
      assert(/gamma-recording-\d{8}-\d{4}/.test(url.searchParams.get("steps")), "the steps name the recording");
      assert(url.searchParams.get("diagnostics").includes("**Build:**"), "diagnostics ride along");
      // The Diagnostics pane's Help row opens the same dialog; an admin's
      // report adds the server dashboard and log.
      await server.manage("set-admin", "settings-user", "on");
      await page.reload();
      await page.waitForSelector(".folderNewBtn");
      await openSettings(page);
      await nav(page, "Help & diagnostics").click();
      const privacy = row(page, "Privacy policy").getByRole("link", { name: "Open", exact: true });
      assertEq(await privacy.getAttribute("href"), "https://gammapdf.com/privacy/", "Help links the privacy policy");
      assertEq(await privacy.getAttribute("target"), "_blank");
      await row(page, "Report a problem").getByRole("button", { name: "Report…", exact: true }).click();
      await dialog.waitFor();
      await dialog.getByText("Preview the report", { exact: true }).click();
      await until(async () => (await dialog.locator("pre").textContent()).includes("**Server (seen as admin):** Gamma"), { what: "the admin's server section" });
      await dialog.getByRole("button", { name: "Close Report a problem", exact: true }).click();
      await dialog.waitFor({ state: "detached" });
      assertNoProblems(page);
    } finally {
      await server.manage("set-admin", "settings-user", "off");
      await ctx.close();
    }
  });

  await step("settings: administrators see their own account separately from all users", async () => {
    await server.manage("set-admin", "settings-user", "on");
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Account & sync").click();
      await page.locator(".settingsPane .aiProvRow").waitFor();
      assertEq(await page.locator(".settingsPane .aiProvRow").count(), 1);
      // the storage defaults sit under the accounts, in Users
      await nav(page, "Users").click();
      const quota = row(page, "Default quota").locator("input");
      await quota.waitFor();
      assertEq(await page.locator('.settingsPane [data-setting="Default max upload"]').count(), 0); // no per-file cap
      const originalQuota = await quota.inputValue();
      // the quota saves on commit, like every other setting
      await quota.fill("1200");
      await quota.press("Enter");
      await until(() => user.api("/api/admin/settings").then((s) => s.quota_mb === 1200), { what: "quota saved on Enter" });
      await quota.fill(originalQuota);
      await quota.press("Enter");
      await until(() => user.api("/api/admin/settings").then((s) => String(s.quota_mb) === originalQuota), { what: "quota restored" });
      await quota.fill("1200");
      await quota.press("Enter");
      await until(() => user.api("/api/admin/settings").then((v) => v.quota_mb === 1200));
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
      // Databases: the on-demand integrity check of every database file
      await row(page, "Check databases").getByRole("button", { name: "Check now", exact: true }).click();
      await until(() => row(page, "Check databases").innerText().then((text) => /database files? passed/.test(text)),
        { what: "the database check's result" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: an admin's own new username and password saved together both take", async () => {
    // The new password ends the account's sessions, the editor's own too,
    // so the rename has to go first or it is sent signed out.
    await server.manage("create-user", "rename-self", "rename-self-pw");
    await server.manage("set-admin", "rename-self", "on");
    const self = await new Account(server, "rename-self", "rename-self-pw").login();
    const ctx = await self.context(browser);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    try {
      const page = await openPage(ctx, server.base);
      await page.waitForSelector(".folderNewBtn");
      await openSettings(page);
      await nav(page, "Users").click();
      const own = page.locator(".settingsPane .aiProvRow").filter({ has: page.locator(".aiProvName", { hasText: "rename-self" }) });
      await own.getByRole("button", { name: "Edit", exact: true }).click();
      const edit = page.getByRole("dialog", { name: "Edit rename-self", exact: true });
      await edit.getByRole("textbox").first().fill("renamed-self");
      await edit.locator('input[autocomplete="new-password"]').fill("renamed-self-pw");
      const saved = page.waitForResponse((r) => r.request().method() === "PUT" && new URL(r.url()).pathname === "/api/admin/users/renamed-self");
      await edit.getByRole("button", { name: "Save", exact: true }).click();
      // the tab's identity follows the rename, so the password change after it is not refused as another account's
      assertEq((await saved).status(), 200, "the password set under the new name");
      const signIn = (username, password) => fetch(`${server.base}/api/login`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }),
      }).then((r) => r.status);
      assertEq(await signIn("renamed-self", "renamed-self-pw"), 200, "the new name with the new password");
      assert(await signIn("rename-self", "rename-self-pw") !== 200, "the old name is gone");
    } finally { await ctx.close(); }
  });

  await step("settings: Users → Guests turns guest sign-in off, and saves the lifetime and demo mode", async () => {
    await server.manage("set-admin", "settings-user", "on");
    const before = await user.api("/api/admin/settings");
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Users").click();
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
      await nav(page, "Server").click();
      await nav(page, "Users").click();
      await until(async () => (await row(page, "Guest workspaces last").locator("input").inputValue()) === "36");
      assert(await row(page, "Demo mode").locator("input").isChecked(), "demo mode reads back on");
      await page.getByRole("checkbox", { name: "Demo mode", exact: true }).uncheck();
      await until(() => user.api("/api/admin/settings").then((v) => v.demo_mode === false), { what: "demo mode off again" });
      // Guest sign-in off: the login page loses its guest button, and the two
      // rows that only describe guests go idle.
      const guestSwitch = page.getByRole("checkbox", { name: "Guest sign-in", exact: true });
      assert(await guestSwitch.isChecked(), "guest sign-in is on by default");
      await guestSwitch.uncheck();
      await until(() => user.api("/api/admin/settings").then((v) => v.guest_logins === false), { what: "guest sign-in saved off" });
      assertEq((await (await fetch(`${server.base}/api/server-config`)).json()).guest, false, "no guest button on the login page");
      await until(() => row(page, "Guest workspaces last").locator("input").isDisabled(), { what: "the lifetime row goes idle" });
      await guestSwitch.check();
      await until(() => user.api("/api/admin/settings").then((v) => v.guest_logins === true), { what: "guest sign-in back on" });
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await user.api("/api/admin/settings", {
        method: "PUT", body: { guest_ttl_hours: before.guest_ttl_hours, demo_mode: false, guest_logins: true },
      });
    }
  });

  await step("settings: a pane's reads go out together and once, as the pointer reaches its button; the pane before stays until they are in, but not for long; a second visit shows at once and reads again in place", async () => {
    await server.manage("set-admin", "settings-user", "on");
    const { ctx, page } = await setup();
    try {
      const asked = [];
      page.on("request", (r) => {
        const url = new URL(r.url());
        if (url.pathname.startsWith("/api/") && r.method() === "GET") asked.push(url.pathname + url.search);
      });
      const count = (path) => asked.filter((u) => u === path).length;
      const title = () => page.locator(".settingsPane .settingsPaneTitle").first().textContent();
      // the pane's title as the next one shows, and whether what it read is there in that frame
      const nextPane = (name, text) => page.evaluate(([name, text]) => new Promise((resolve) => {
        const tick = () => {
          const pane = document.querySelector(".settingsPane");
          if (pane?.querySelector(".settingsPaneTitle")?.textContent === name) resolve(pane.textContent.includes(text));
          else requestAnimationFrame(tick);
        };
        tick();
      }), [name, text]);
      // The Server pane (where Settings opens while a notice points there, else
      // a click away): its sections over the server's settings make one read,
      // and no path is read twice. Settings opens on Keyboard, which
      // reads nothing (Account & sync, where Settings… opens, reads Users'
      // accounts and Server's settings ahead).
      await page.keyboard.press("ControlOrMeta+Shift+p");
      const palette = page.getByRole("dialog", { name: "Command palette" });
      await palette.getByRole("textbox", { name: "Type a command" }).fill(">Keyboard shortcuts");
      await until(async () => /Keyboard shortcuts/.test(await palette.locator('[role="option"][aria-selected="true"]').textContent()), { what: "the command selected" });
      await page.keyboard.press("Enter");
      await until(async () => (await title()) === "Keyboard shortcuts", { what: "the Keyboard pane" });
      if ((await title()) !== "Server") await nav(page, "Server").click();
      await until(async () => (await title()) === "Server", { what: "the Server pane" });
      await page.getByRole("textbox", { name: "Public server URL" }).waitFor();
      await page.waitForTimeout(500);
      assertEq(count("/api/admin/settings"), 1, `the sections over the server's settings, one read: ${asked}`);
      // (the pane's own reads; the storage line and the sync state are the menu's and the dialog's)
      const panes = asked.filter((u) => /^\/api\/(admin\/|accounts$)/.test(u));
      assertEq(panes.length, new Set(panes).size, `no path read twice: ${asked}`);
      // Pointing at Users reads its pane ahead; the click then finds it in.
      await nav(page, "Appearance").click();
      await until(async () => (await title()) === "Appearance", { what: "Appearance" });
      const read = page.waitForResponse((r) => r.url().endsWith("/api/admin/users"));
      await nav(page, "Users").hover();
      await read;
      await nav(page, "Users").click();
      assertEq(await title(), "Users", "a pane whose reads are in shows in the click's commit");
      assert((await page.locator(".settingsPane").textContent()).includes("settings-user"), "with its accounts");
      // A later visit shows what the first read at once, and reads it again in place.
      await page.waitForTimeout(3200);
      await nav(page, "Server").click();
      assertEq(await title(), "Server", "a second visit shows at once");
      assert((await page.getByRole("textbox", { name: "Public server URL" }).inputValue()).startsWith("http"),
        "the public URL is in its box at once");
      await until(() => count("/api/admin/settings") === 2, { what: "the settings read again in place" });
      // The pane before stays while the next one's reads come in, its button marked at once.
      await page.route("**/api/auth/cloud/status", async (route) => { await sleep(200); await route.continue(); });
      const account = nextPane("Account & sync", "Gamma Cloud");
      await page.getByRole("button", { name: "Account & sync", exact: true }).click();
      assertEq(await title(), "Server", "Server stays while the account pane reads");
      assertEq(await page.getByRole("button", { name: "Account & sync", exact: true }).getAttribute("aria-current"), "page",
        "the account card is marked at once");
      assert(await account, "the account pane comes with its Gamma Cloud row");
      // But not for long: a slow read lets the pane show without it after a moment.
      await page.route("**/api/backup-tasks", async (route) => { await sleep(2000); await route.continue(); });
      const shownAt = Date.now();
      await nav(page, "Backups").click();
      await until(async () => (await title()) === "Backups", { timeout: 1000, what: "the Backups pane, before its slow read" });
      assert(Date.now() - shownAt < 1000, "it waits a moment, not for the read");
      await page.getByText("Loading tasks…", { exact: true }).waitFor({ state: "attached" });
      await page.getByText("Loading tasks…", { exact: true }).waitFor({ state: "detached", timeout: 5000 });
      assertNoProblems(page);
    } finally { await ctx.close(); } // settings-user stays an admin for the steps below
  });

  // Settings → Server › Zotero sign-in (SettingsZoteroApp.jsx,
  // gamma/zotero_oauth.py `app`): this server's own Zotero app, saved with
  // its secret write-only beside the callback URL to register it with, then
  // removed. Saving asks nothing of zotero.org.
  await step("settings: Server › Zotero sign-in saves this server's Zotero app, its secret write-only, and removes it", async () => {
    await server.manage("set-admin", "settings-user", "on");
    const { ctx, page } = await setup();
    try {
      await openSettings(page);
      await nav(page, "Server").click();
      // (a section's data-setting is its heading's; its rows follow it, and only Zotero's says "Not set up" here)
      await page.locator('.settingsPane .setSection[data-setting="Zotero sign-in"]').waitFor();
      await row(page, "Not set up").getByRole("button", { name: "Set up…", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Set up a Zotero app", exact: true });
      assertEq(await dialog.getByRole("link", { name: "Open", exact: true }).getAttribute("href"), "https://www.zotero.org/oauth/apps",
        "the link to register an app");
      const callback = await dialog.getByRole("textbox", { name: "Callback URL", exact: true }).inputValue();
      assert(callback.startsWith("http") && callback.endsWith("/api/zotero/oauth/callback"), `the callback URL to register: ${callback}`);
      await dialog.getByRole("textbox", { name: "Client key", exact: true }).fill("gamma-e2e-key");
      await dialog.getByLabel("Client secret", { exact: true }).fill("gamma-e2e-secret");
      await dialog.getByRole("button", { name: "Save", exact: true }).click();
      await dialog.waitFor({ state: "detached" });
      const saved = row(page, "This server's Zotero app");
      assertEq(await saved.locator(".settingDesc").innerText(), "gamma-e2e-key · people connect Zotero by signing in at zotero.org",
        "the row names the saved app");
      const read = await user.api("/api/admin/zotero-app");
      assert(read.client_key === "gamma-e2e-key" && !JSON.stringify(read).includes("gamma-e2e-secret"),
        "the secret never reaches the browser");
      await saved.getByRole("button", { name: "Edit…", exact: true }).click();
      const editor = page.getByRole("dialog", { name: "Edit the Zotero app", exact: true });
      assertEq(await editor.getByLabel("Client secret", { exact: true }).getAttribute("placeholder"), "secret set — type to replace");
      await editor.getByRole("button", { name: "Remove…", exact: true }).click();
      await page.locator(".confirmModal").getByRole("button", { name: "Remove", exact: true }).click();
      await row(page, "Not set up").waitFor();
      assertEq((await user.api("/api/admin/zotero-app")).client_key, "", "removed on the server");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // The server options of Settings (SettingsServerOptions.jsx,
  // gamma/server_options.py), once environment variables: Server › Online
  // services' switches, and Tool usage › Online search for every account,
  // whose OpenAlex key is write-only. The suite's servers start with paper
  // lookups off (harness.mjs OFFLINE), which this step leaves as it found.
  await step("settings: Server › Online services and Tool usage › Online search for every account are server options an admin sets", async () => {
    await server.manage("set-admin", "settings-user", "on");
    const { ctx, page } = await setup();
    const options = async () => (await user.api("/api/admin/settings")).options;
    try {
      await openSettings(page);
      await nav(page, "Server").click();
      const lookups = page.getByRole("checkbox", { name: "Paper lookups", exact: true });
      assert(!(await lookups.isChecked()), "the suite's servers start with paper lookups off");
      await lookups.click();
      await until(async () => (await options()).metadata_lookup === true, { what: "paper lookups on" });
      await lookups.click();
      await until(async () => (await options()).metadata_lookup === false, { what: "paper lookups off again" });
      assert(!(await page.getByRole("checkbox", { name: "AI model catalog", exact: true }).isChecked()), "the catalog off too");

      await nav(page, "Tool usage").click();
      const section = page.locator('.settingsPane .setSection[data-setting="Online search for every account"]');
      await section.waitFor();
      await row(page, "SearXNG for every account").getByRole("button", { name: "Set up…", exact: true }).click();
      const searx = page.getByRole("dialog", { name: "SearXNG for every account", exact: true });
      await searx.getByRole("textbox", { name: "Instance URL", exact: true }).fill("http://searx.lan:8080/");
      await searx.getByRole("button", { name: "Save", exact: true }).click();
      await searx.waitFor({ state: "detached" });
      assertEq(await row(page, "SearXNG for every account").locator(".settingDesc").innerText(), "http://searx.lan:8080");
      await row(page, "OpenAlex key for every account").getByRole("button", { name: "Set up…", exact: true }).click();
      const openalex = page.getByRole("dialog", { name: "OpenAlex key for every account", exact: true });
      await openalex.getByLabel("API key", { exact: true }).fill("oa-e2e-key");
      await openalex.getByRole("button", { name: "Save", exact: true }).click();
      await openalex.waitFor({ state: "detached" });
      const saved = await options();
      assert(saved.searxng_url === "http://searx.lan:8080" && saved.openalex_key_set === true, `saved: ${JSON.stringify(saved)}`);
      assert(!JSON.stringify(await user.api("/api/admin/settings")).includes("oa-e2e-key"), "the key never reaches the browser");
      for (const name of ["SearXNG for every account", "OpenAlex key for every account"]) {
        await row(page, name).getByRole("button", { name: "Remove", exact: true }).click();
        await row(page, name).getByRole("button", { name: "Set up…", exact: true }).waitFor();
      }
      const cleared = await options();
      assert(!cleared.searxng_url && !cleared.openalex_key_set, "both removed");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("settings: a shared AI provider from Server is a read-only connection for every account", async () => {
    // settings-user is an admin since the step above.
    await server.manage("create-user", "settings-member", "settings-member-pw");
    const member = await new Account(server, "settings-member", "settings-member-pw").login();
    const { ctx, page } = await setup();
    try {
      await page.route("**/api/ai/model-catalog", (route) => route.fulfill({ json: { models: ["lab-model", "lab-big"] } }));
      await openSettings(page);
      await nav(page, "Server").click();
      await row(page, "Shared AI provider").getByRole("button", { name: "Add provider", exact: true }).click();
      // The connection is tested once saved; the test is answered here, offline.
      await page.route("**/api/ai/providers/*/test", (route) => route.fulfill({ json: { ok: true, model: "lab-model", latency_ms: 300 } }));
      const dialog = page.getByRole("dialog", { name: "Connect a shared AI service", exact: true });
      // A key or a ChatGPT sign-in (the next step connects one).
      await dialog.getByRole("group", { name: "Sign in or subscription", exact: true }).getByRole("button", { name: "ChatGPT", exact: true }).waitFor();
      await dialog.getByRole("group", { name: "API key", exact: true }).getByRole("button", { name: "OpenAI API", exact: true }).click();
      await dialog.locator('input[autocomplete="new-password"]').fill("sk-shared-e2e-key-7777");
      await dialog.getByRole("button", { name: "2 usable" }).waitFor();
      await dialog.getByRole("button", { name: "Remove lab-model", exact: true }).waitFor();
      await dialog.getByRole("button", { name: "Connect", exact: true }).click();
      await until(() => dialog.count().then((n) => n === 0));
      const sharedRow = page.locator(".settingsPane .aiProvRow").filter({ hasText: "…7777" });
      await sharedRow.getByText(/working · lab-model/).waitFor();
      // Who may use it: every account, no guests, unless the admin says otherwise.
      await until(async () => (await sharedRow.innerText()).includes("every account"), { what: "the row says who may use it" });
      // The shared allowance: millions of tokens per account / guest per
      // day, stored in tokens; 0 = unlimited.
      const perAccount = row(page, "Allowance per account").locator("input");
      assertEq(await perAccount.inputValue(), "0", "unlimited by default");
      await perAccount.fill("0.05");
      await perAccount.press("Enter");
      await row(page, "Allowance per guest").locator("input").fill("0.002");
      await row(page, "Allowance per guest").locator("input").press("Enter");
      await until(() => user.api("/api/admin/ai-providers").then((v) => v.allowance?.accounts === 50000 && v.allowance?.guests === 2000),
        { what: "the allowance saved" });
      assertEq(await perAccount.inputValue(), "0.05", "the saved allowance reads back in millions");
      const metered = (await member.api("/api/ai/usage")).allowance;
      assertEq(JSON.stringify(metered), JSON.stringify({ limit: 50000, used: 0, exhausted: false }), "the member's allowance");

      // Models: a read-only connection with the tag, and its models in every
      // task's menu, under the connection.
      await nav(page, "Models").click();
      const shared = page.locator(".settingsPane .aiProvRow").filter({ hasText: "Shared by this server" });
      await shared.waitFor();
      assertEq(await shared.getByRole("button").count(), 0, "no edit or delete on a shared row");
      const chatModel = row(page, "Chat").getByRole("button", { name: "Chat", exact: true });
      await chatModel.click();
      const group = page.getByRole("listbox", { name: "Chat", exact: true }).getByRole("group").filter({ hasText: "Shared by this server" });
      assertEq((await group.locator(".modelMenuName").allInnerTexts()).join(), "lab-model", "the shared connection's model, the one picked");
      await group.getByRole("option", { name: /^lab-model/ }).click();
      await until(async () => (await chatModel.innerText()) === "lab-model", { what: "the chat on the shared model" });
      await until(async () => (await user.api("/api/ai/models")).roles.chat.pick.startsWith("server:"),
        { what: "the chat on the shared entry's model, saved" });
      // Usage says what is left of the allowance.
      await nav(page, "Usage").click();
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
      await user.api("/api/ai/models/roles", { method: "PUT", body: { chat: "" } });
    }
  });

  await step("settings: who may use a shared connection — every account, guests too, or the accounts named", async () => {
    // settings-user is an admin and settings-member exists since the steps above.
    await server.manage("create-user", "settings-outsider", "settings-outsider-pw");
    const member = await new Account(server, "settings-member", "settings-member-pw").login();
    const outsider = await new Account(server, "settings-outsider", "settings-outsider-pw").login();
    const shared = (await user.api("/api/admin/ai-providers", { method: "POST",
      body: { protocol: "openai", name: "Lab key", api_key: "sk-shared-access-e2e-5555", models: "lab-model" } })).providers.at(-1);
    const { ctx, page } = await setup();
    try {
      // The stored keys are made up: the model list and the probe are answered here.
      await page.route("**/api/ai/model-catalog", (route) => route.fulfill({ json: { models: ["lab-model"] } }));
      await page.route("**/api/ai/providers/*/test", (route) => route.fulfill({ json: { ok: true, model: "lab-model", latency_ms: 300 } }));
      await openSettings(page);
      await nav(page, "Server").click();
      const labRow = page.locator(".settingsPane .aiProvRow").filter({ hasText: "Lab key" });
      await until(async () => (await labRow.innerText()).includes("every account"), { what: "every account by default" });
      assert((await outsider.api("/api/ai/models")).enabled, "every account has it to begin with");

      // Only the accounts named: the picker names the member.
      await labRow.getByRole("button", { name: "Manage", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Edit shared key", exact: true });
      await dialog.getByRole("group", { name: "Access" }).getByRole("button", { name: "Selected accounts", exact: true }).click();
      await dialog.getByText(/No account named yet/).waitFor();
      await dialog.getByPlaceholder("Add an account…").fill("settings-mem");
      await dialog.getByRole("option", { name: /settings-member/ }).click();
      await dialog.getByRole("button", { name: "Remove settings-member", exact: true }).waitFor();
      await dialog.getByRole("button", { name: "Save changes", exact: true }).click();
      await until(() => dialog.count().then((n) => n === 0));
      const accessOf = async () => (await user.api("/api/admin/ai-providers")).providers.find((p) => p.id === shared.id)?.access;
      await until(async () => JSON.stringify(await accessOf()) === JSON.stringify({ who: "selected", users: ["settings-member"], guests: false }),
        { what: "the access saved" });
      await until(async () => (await labRow.innerText()).includes("settings-member"), { what: "the row names the account" });
      assert((await member.api("/api/ai/models")).models.some((m) => m.provider === shared.id), "the named account has it");
      assert(!(await outsider.api("/api/ai/models")).enabled, "an account not named has nothing");

      // Accounts and guests.
      await labRow.getByRole("button", { name: "Manage", exact: true }).click();
      await dialog.getByRole("group", { name: "Access" }).getByRole("button", { name: "Accounts and guests", exact: true }).click();
      await dialog.getByRole("button", { name: "Save changes", exact: true }).click();
      await until(async () => (await accessOf())?.guests === true, { what: "guests admitted" });
      await until(async () => (await labRow.innerText()).includes("every account and guests"), { what: "the row says guests too" });
      assert((await outsider.api("/api/ai/models")).enabled, "every account again");

      assertNoProblems(page);
    } finally {
      await ctx.close();
      for (const p of (await user.api("/api/admin/ai-providers")).providers) {
        await user.api(`/api/admin/ai-providers/${encodeURIComponent(p.id)}`, { method: "DELETE" });
      }
    }
  });

  await step("settings: a ChatGPT subscription can be the server's shared connection", async () => {
    // The admin signs in from Settings → Server on the server's own machine,
    // so the server catches the redirect and the form connects by itself.
    // OpenAI's side is faked: the start (no port bound), the wait and the
    // exchange (the backend's side is tests/test_shared_chatgpt.py and
    // tests/test_chatgpt_oauth.py).
    const { ctx, page } = await setup();
    try {
      await page.evaluate(() => {
        window.open = (url) => { window.testSignInUrl = url; return null; };
      });
      await page.route("**/api/admin/ai-providers/chatgpt/start", (route) => route.fulfill({ json: {
        auth_url: "https://auth.openai.com/oauth/authorize?state=e2e-shared", state: "e2e-shared", local: true, device: null } }));
      await page.route("**/api/admin/ai-providers/chatgpt/status", (route) => route.fulfill({ json: { ready: true, error: "" } }));
      let completed = null;
      await page.route("**/api/admin/ai-providers/chatgpt/complete", async (route) => {
        completed = route.request().postDataJSON();
        const info = await user.api("/api/admin/ai-providers");
        info.providers.push({ id: "server:oauth-e2e", protocol: "chatgpt", name: "Lab ChatGPT", label: "Lab ChatGPT",
          models: "gpt-lab", oauth_connected: true, account: "lab@example.com", shared: true, quota: true });
        await route.fulfill({ json: info });
      });
      await page.route("**/api/ai/model-catalog", (route) => route.fulfill({ json: { models: ["gpt-lab", "gpt-lab-mini"] } }));
      await openSettings(page);
      await nav(page, "Server").click();
      await row(page, "Shared AI provider").getByRole("button", { name: "Add provider", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Connect a shared AI service", exact: true });
      await dialog.getByRole("group", { name: "Sign in or subscription", exact: true }).getByRole("button", { name: "ChatGPT", exact: true }).click();
      await dialog.getByRole("button", { name: "Open ChatGPT sign-in", exact: true }).click();
      await until(() => page.evaluate(() => !!window.testSignInUrl), { what: "the sign-in page opened" });
      await dialog.getByText("Did not connect? Paste the address instead", { exact: true }).waitFor();
      // Connected: the form stays open on the entry with the account's live list.
      const edit = page.getByRole("dialog", { name: "Edit shared key", exact: true });
      await edit.getByRole("button", { name: "2 usable" }).waitFor();
      assertEq(completed?.state, "e2e-shared", "the shared flow's own state is redeemed");
      assertEq(completed?.callback, "", "nothing was pasted");
      assertEq(completed?.provider_id, "", "a new entry, not a reconnect");
      await edit.getByRole("button", { name: "Cancel", exact: true }).click();
      const shared = page.locator(".settingsPane .aiProvRow").filter({ hasText: "signed in as lab@example.com" });
      await shared.waitFor();
      await shared.getByRole("button", { name: "Usage", exact: true }).waitFor();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // The red dot (app/notices.js): the feed is faked so no real error or
  // release is needed; the acks go to the real server.
  await step("settings: a notice dots the account button, the menu says what it is, and Settings… lands on its pane", async () => {
    await server.manage("set-admin", "settings-user", "on");
    const ctx = await user.context(browser);
    try {
      await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
      const seen = [];
      let notices = [
        { id: "update", fingerprint: "9.9.9", tone: "warn", pane: "server", title: "Gamma v9.9.9 is available",
          message: "Gamma v{version} is available — this server runs v{current}", params: { version: "9.9.9", current: "1.0.0" } },
        // an older server sends no message: the title shows as it is
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
      assertEq(await account.getAttribute("title"), "Account & settings — 2 notices", "the hover title counts them");
      await account.click();
      // The menu names each notice, with a link to the pane that resolves it.
      const rows = page.getByTestId("account-notices");
      await rows.waitFor();
      assert((await rows.textContent()).includes("Gamma v9.9.9 is available — this server runs v1.0.0"), "the message with its values");
      assert((await rows.textContent()).includes("A backup task failed"), "a title-only notice");
      await rows.getByRole("menuitem", { name: /See the update/ }).waitFor();
      // The link is an item of the menu itself, nothing between them, and
      // the notice's sentence describes it.
      const [around, described] = await rows.getByRole("menuitem", { name: /See the update/ }).evaluate((el) =>
        [el.parentElement.closest("[role]")?.getAttribute("role"), document.getElementById(el.getAttribute("aria-describedby"))?.textContent]);
      assertEq(around, "menu", "a notice's link sits in the menu, no status region between");
      assert((described || "").includes("Gamma v9.9.9 is available"), `the link is described by its sentence: ${described}`);
      const item = page.getByRole("menuitem", { name: "Settings…", exact: true });
      await item.locator(".noticeDot").waitFor();
      await item.click();
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
      // Lands on the strongest notice's pane; the visit resolves it and the
      // other pane keeps its dot.
      assertEq(await nav(page, "Backups").getAttribute("aria-current"), "page", "Settings… opens the dotted pane");
      await until(() => seen.length === 1, { what: "the ack of the visited pane" });
      assertEq(seen[0], "backup-failed:t1");
      await nav(page, "Server").locator(".noticeDot").waitFor();
      // the ack goes out in the tick that drops the dot, whose render can
      // land after it on a loaded machine
      await until(async () => await nav(page, "Backups").locator(".noticeDot").count() === 0, { what: "the visited pane's dot to go" });
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
      // A new notice arrives (the feed is polled on focus); its row's link
      // opens its pane, as Settings… would.
      await page.keyboard.press("Escape");
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor({ state: "detached" });
      notices = [{ id: "storage", fingerprint: "90", tone: "warn", pane: "account", title: "Your storage is nearly full (95 of 100 MB used)",
        message: "Your storage is nearly full ({used} of {quota} MB used)", params: { used: 95, quota: 100 } }];
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await account.locator(".noticeDot").waitFor();
      await account.click();
      await page.getByTestId("account-notices").getByRole("menuitem", { name: /Review storage/ }).click();
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
      assertEq(await nav(page, "Account & sync").getAttribute("aria-current"), "page", "the row's link opens its pane");
      await until(() => seen.includes("storage:90"), { what: "visiting the pane resolves it" });
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await user.api("/api/prefs/notices-seen", { method: "PUT", body: { value: {} } });
    }
  });
}
