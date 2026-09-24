import { Account } from "../harness.mjs";

// Settings → Appearance → Language (shared/i18n, docs/dev/i18n.md): the
// interface follows the pick at once, survives a reload, and English is
// back on "System" in an English browser.
export async function i18nScenarios(env) {
  const { server, browser, step, openPage, assertEq, assertNoProblems, until, flags } = env;
  if (flags.only && !"i18n language".includes(flags.only)) return;
  server.manage("create-user", "i18n-user", "i18n-pw");
  const user = await new Account(server, "i18n-user", "i18n-pw").login();

  await step("i18n: switching to 中文 translates the settings dialog, persists, and System restores English", async () => {
    const ctx = await user.context(browser, { locale: "en-US" });
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    const page = await openPage(ctx, server.base);
    try {
      await page.waitForSelector(".folderNewBtn");
      await page.getByRole("button", { name: "Account & settings", exact: true }).click();
      await page.getByRole("button", { name: "Settings…", exact: true }).click();
      const dialog = () => page.getByRole("dialog", { name: /Settings|设置/ });
      await dialog().waitFor();
      assertEq(await page.evaluate(() => document.documentElement.lang), "en", "an English browser starts in English");

      await page.locator('.settingsPane [data-setting="Language"] .uiSelectBtn').click();
      await page.locator(".uiSelectMenu").getByRole("button", { name: "中文", exact: true }).click();
      // The app remounts under the new locale: the dialog closes, reopen it.
      await until(() => page.evaluate(() => document.documentElement.lang === "zh-CN"), { what: "the document language follows" });
      await page.getByRole("button", { name: "账户与设置", exact: true }).click();
      await page.getByRole("button", { name: "设置…", exact: true }).click();
      await page.getByRole("dialog", { name: "设置", exact: true }).waitFor();
      await page.getByRole("navigation", { name: "设置分类" }).getByRole("button", { name: "外观", exact: true }).waitFor();
      assertEq(await page.locator('.settingsPane [data-setting="语言"] .settingLabel').textContent(), "语言", "the row itself is translated");
      assertEq(await page.evaluate(() => localStorage.getItem("gamma-language")), "zh", "the pick is stored");
      assertNoProblems(page);

      await page.reload();
      await page.waitForSelector(".folderNewBtn");
      assertEq(await page.evaluate(() => document.documentElement.lang), "zh-CN", "a reload paints Chinese from the stored pick");
      await page.getByRole("button", { name: "账户与设置", exact: true }).click();
      await page.getByRole("button", { name: "设置…", exact: true }).click();
      await page.getByRole("dialog", { name: "设置", exact: true }).waitFor();
      await page.locator('.settingsPane [data-setting="语言"] .uiSelectBtn').click();
      await page.locator(".uiSelectMenu").getByRole("button", { name: "系统", exact: true }).click();
      await until(() => page.evaluate(() => document.documentElement.lang === "en"), { what: "System follows the English browser" });
      await page.getByRole("button", { name: "Account & settings", exact: true }).waitFor();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });
}
