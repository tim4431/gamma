import { Account } from "../harness.mjs";

// Ctrl+P: the quick-open page palette (library/QuickOpen.jsx).
export async function quickOpenScenarios(env) {
  const { server, browser, step, openPage, assert, assertEq, assertNoProblems, until, flags } = env;
  if (flags.only && !"quickopen".includes(flags.only)) return;
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
}
