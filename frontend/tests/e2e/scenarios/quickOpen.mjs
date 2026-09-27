import { Account, wanted } from "../harness.mjs";

// Ctrl+P: the quick-open page palette (library/QuickOpen.jsx).
export async function quickOpenScenarios(env) {
  const { server, browser, step, openPage, assert, assertEq, assertNoProblems, until, flags } = env;
  if (!wanted("quickopen")) return;
  server.manage("create-user", "quickopen-user", "quickopen-pw");
  const user = await new Account(server, "quickopen-user", "quickopen-pw").login();
  const titles = ["Cavity readout", "Cavity sensors", "Quantum correction", "Atomic clocks"];
  const papers = {};
  for (const title of titles) {
    const properties = title === "Atomic clocks" ? { category: "horlogerie" } : undefined;
    papers[title] = await user.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: title, properties } });
  }

  await step("quickopen: Ctrl+P lists pages, filters by title and opens the pick", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${papers["Atomic clocks"].id}&ws=${user.ws}`);
    try {
      await page.locator(".blockList").first().waitFor();
      await page.keyboard.press("Control+p");
      const dialog = page.getByRole("dialog", { name: "Open a page" });
      await dialog.waitFor();
      const input = dialog.getByRole("textbox", { name: "Search pages by title or label" });
      await until(() => input.evaluate((el) => el === document.activeElement), { what: "the palette's input takes focus" });
      await until(async () => (await dialog.getByRole("option").count()) === titles.length, { what: "every page listed before typing" });
      assert(await dialog.getByRole("option", { name: /Atomic clocks/ }).locator(".quickOpenTag").textContent() === "Current",
        "the open page is tagged Current");

      // A label matches like a title, typos included ("horlogerie" is only a label).
      await input.fill("horlogeire");
      await until(async () => (await dialog.getByRole("option").count()) === 1, { what: "the label's page alone" });
      assert(/Atomic clocks/.test(await dialog.getByRole("option").textContent()), "the labelled page matches");
      assertEq(await dialog.getByRole("option").locator(".labelTagBadge").textContent(), "horlogerie", "the row shows its label");

      await input.fill("cavity");
      await until(async () => (await dialog.getByRole("option").count()) === 2);
      await page.keyboard.press("ArrowDown");
      const picked = await dialog.locator('[role="option"][aria-selected="true"] strong').textContent();
      await page.keyboard.press("Enter");
      await until(() => new URL(page.url()).searchParams.get("block") === papers[picked].id);
      assertEq(await page.getByRole("dialog", { name: "Open a page" }).count(), 0, "opening closes the palette");

      await page.keyboard.press("Control+p");
      await dialog.waitFor();
      await page.keyboard.press("Escape");
      assertEq(await page.getByRole("dialog", { name: "Open a page" }).count(), 0, "Escape closes the palette");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("quickopen: Ctrl+Shift+P is the command palette; a pick runs", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${papers["Atomic clocks"].id}&ws=${user.ws}`);
    try {
      await page.locator(".blockList").first().waitFor();
      await page.keyboard.press("Control+Shift+p");
      const dialog = page.getByRole("dialog", { name: "Command palette" });
      await dialog.waitFor();
      const input = dialog.getByRole("textbox", { name: "Type a command" });
      assertEq(await input.inputValue(), ">", "the command prefix is typed");
      await until(async () => (await dialog.getByRole("option").count()) > 3, { what: "commands listed" });
      assert((await dialog.getByRole("option", { name: /Rename page/ }).textContent()).includes("F2"), "a command shows its keys");
      await input.fill(">rename");
      await until(async () => /Rename page/.test(await dialog.locator('[role="option"][aria-selected="true"]').textContent()), { what: "the match is selected" });
      await page.keyboard.press("Enter");
      await page.locator(".titleEdit").waitFor();
      assertEq(await page.getByRole("dialog", { name: "Command palette" }).count(), 0, "running closes the palette");
      await page.keyboard.press("Escape");
      // ">" typed into Ctrl+P is the same palette.
      await page.keyboard.press("Control+p");
      await page.getByRole("dialog", { name: "Open a page" }).waitFor();
      await page.keyboard.type(">");
      await page.getByRole("dialog", { name: "Command palette" }).waitFor();
      await page.keyboard.press("Escape");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("quickopen: a link to a page that isn't here says so, and Search the library opens the palette", async () => {
    const gone = await user.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Soon deleted" } });
    await user.api(`/api/blocks/${gone.id}`, { method: "DELETE" });
    const holder = await user.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Links to a gone page" } });
    await user.api("/api/blocks", { method: "POST", body: { parent_id: holder.id, content: `see [the old page](/?page=${gone.id})` } });
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${gone.id}&ws=${user.ws}`);
    try {
      // a dead deep link: the library, the notice above it, the id out of the address bar
      const notice = page.locator(".missingPageNotice");
      await notice.waitFor({ timeout: 15000 });
      assert((await notice.textContent()).includes("That page isn't here."), "the notice");
      await page.locator(".homeListBar").waitFor();
      assert(!new URL(page.url()).searchParams.has("page"), "the dead id leaves the address bar");
      await notice.getByRole("button", { name: "Search the library", exact: true }).click();
      await page.getByRole("dialog", { name: "Open a page" }).waitFor();
      assertEq(await notice.count(), 0, "the notice goes when the palette opens");
      await page.keyboard.press("Escape");
      // a dead link clicked on a page: the notice in place, the page stays open
      await page.goto(`${server.base}/?page=${holder.id}&ws=${user.ws}`);
      await page.locator(".gammaLinkCard", { hasText: "the old page" }).click();
      await notice.waitFor();
      assertEq(new URL(page.url()).searchParams.get("block"), holder.id, "still on the page");
      await notice.getByRole("button", { name: "Dismiss", exact: true }).click();
      await notice.waitFor({ state: "detached" });
      assertNoProblems(page, [/find-page\/[^ ]+ -> 404/, /\/subtree -> 404/]);
    } finally { await ctx.close(); }
  });
}
