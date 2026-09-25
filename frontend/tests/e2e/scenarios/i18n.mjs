import { Account, wanted } from "../harness.mjs";

// Settings → Appearance → Language (shared/i18n, docs/dev/i18n.md): the
// interface follows the pick at once, the pane comes back after the
// remount, the pick outlives the profile pull of the fresh app and a
// reload, and English is back on "System" in an English browser.
export async function i18nScenarios(env) {
  const { server, browser, step, openPage, assertEq, assertNoProblems, until, sleep, flags } = env;
  if (!wanted("i18n")) return;
  server.manage("create-user", "i18n-user", "i18n-pw");
  const user = await new Account(server, "i18n-user", "i18n-pw").login();
  // An account with a saved profile: the server's copy wins on every load,
  // so a switch that is not pushed before the remount would revert.
  await user.api("/api/prefs/profile", { method: "PUT", body: { value: { language: "system", theme: "light" } } });

  const settings = (page) => page.getByRole("dialog", { name: /^(Settings|设置)$/ });
  async function openSettings(page, account, item) {
    await page.getByRole("button", { name: account, exact: true }).click();
    await page.getByRole("button", { name: item, exact: true }).click();
    await settings(page).waitFor();
  }
  const pick = async (page, row, option) => {
    await page.locator(`.settingsPane [data-setting="${row}"] .uiSelectBtn`).click();
    await page.locator(".uiSelectMenu").getByRole("button", { name: option, exact: true }).click();
  };

  await step("i18n: switching to 中文 translates the settings dialog, persists, and System restores English", async () => {
    const ctx = await user.context(browser, { locale: "en-US" });
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    const page = await openPage(ctx, server.base);
    try {
      await page.waitForSelector(".folderNewBtn");
      await openSettings(page, "Account & settings", "Settings…");
      assertEq(await page.evaluate(() => document.documentElement.lang), "en", "an English browser starts in English");

      await pick(page, "Language", "中文");
      await until(() => page.evaluate(() => document.documentElement.lang === "zh-CN"), { what: "the document language follows" });
      // The app remounted under the new locale, on the same pane.
      await page.getByRole("dialog", { name: "设置", exact: true }).waitFor();
      await page.getByRole("navigation", { name: "设置分类" }).getByRole("button", { name: "外观", exact: true }).waitFor();
      assertEq(await page.locator('.settingsPane [data-setting="语言"] .settingLabel').textContent(), "语言", "the row itself is translated");
      assertEq(await page.evaluate(() => localStorage.getItem("gamma-language")), "zh", "the pick is stored");
      // The fresh app's profile pull must not bring the old value back.
      await sleep(2500);
      assertEq(await page.evaluate(() => document.documentElement.lang), "zh-CN", "the pick survives the profile pull");
      assertEq((await user.api("/api/prefs/profile")).value.language, "zh", "the profile holds the pick");
      assertNoProblems(page);

      await page.reload();
      await page.waitForSelector(".folderNewBtn");
      assertEq(await page.evaluate(() => document.documentElement.lang), "zh-CN", "a reload paints Chinese from the stored pick");
      await openSettings(page, "账户与设置", "设置…");
      await pick(page, "语言", "系统");
      await until(() => page.evaluate(() => document.documentElement.lang === "en"), { what: "System follows the English browser" });
      await settings(page).waitFor();
      await sleep(2500);
      assertEq(await page.evaluate(() => document.documentElement.lang), "en", "System survives the profile pull");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });
}
