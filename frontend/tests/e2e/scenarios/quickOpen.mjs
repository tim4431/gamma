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
    const properties = title === "Atomic clocks" ? { category: "horlogerie" }
      : title === "Cavity readout" ? { folder: "optics/cavities" } : undefined;
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
      // every page, and the Welcome page a new account starts with
      await until(async () => (await dialog.getByRole("option").count()) === titles.length + 1, { what: "every page listed before typing" });
      assert(await dialog.getByRole("option", { name: /Atomic clocks/ }).locator(".quickOpenTag").textContent() === "Current",
        "the open page is tagged Current");

      // A label matches like a title, typos included ("horlogerie" is only a label).
      const pageRows = dialog.locator('[role="option"][data-kind="page"]');
      await input.fill("horlogeire");
      await until(async () => (await pageRows.count()) === 1, { what: "the label's page alone" });
      assert(/Atomic clocks/.test(await pageRows.textContent()), "the labelled page matches");
      assertEq(await pageRows.locator(".labelTagBadge").textContent(), "horlogerie", "the row shows its label");
      assert(/horlogerie/.test(await dialog.locator('[role="option"][data-kind="label"]').textContent()), "the label itself is listed");
      assertEq(await dialog.locator('[role="option"][data-action]').count(), 2, "the search and create actions end the list");

      await input.fill("cavity");
      await until(async () => (await pageRows.count()) === 2);
      assertEq(await dialog.locator(".quickOpenSection").first().textContent(), "Pages", "results sit under section headings");
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

  await step("quickopen: folders open their view; Ctrl+Enter searches everywhere, Shift+Enter creates the page", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${papers["Atomic clocks"].id}&ws=${user.ws}`);
    try {
      await page.locator(".blockList").first().waitFor();
      const dialog = page.getByRole("dialog", { name: "Open a page" });
      const input = dialog.getByRole("textbox", { name: "Search pages by title or label" });
      // A folder is a row of its own; picking it opens the folder view.
      await page.keyboard.press("Control+p");
      await input.fill("cavities");
      const folderRow = dialog.locator('[role="option"][data-kind="folder"]');
      await until(async () => (await folderRow.count()) === 1, { what: "the folder matches" });
      assert(/in optics/.test(await folderRow.textContent()), "the folder row names its parent");
      await folderRow.click();
      await until(async () => new URL(page.url()).searchParams.get("folder") === "optics/cavities", { what: "the folder view opens" });
      await page.locator(".fileRow", { hasText: "Cavity readout" }).waitFor();

      // Ctrl+Enter hands the query to the workspace search.
      await page.keyboard.press("Control+p");
      await input.fill("quantum");
      await page.keyboard.press("Control+Enter");
      const search = page.locator(".searchPopover .searchInput");
      await search.waitFor();
      await until(async () => (await search.inputValue()) === "quantum", { what: "the search opens with the query" });
      await page.keyboard.press("Escape");
      await page.mouse.click(5, 300);

      // Shift+Enter creates a page with the query as its title, and opens it.
      await page.keyboard.press("Control+p");
      await input.fill("Fresh cavity idea");
      await page.keyboard.press("Shift+Enter");
      await until(async () => (await user.api("/api/blocks/root/children")).children.some((b) => b.content === "Fresh cavity idea"),
        { what: "the page exists" });
      const created = (await user.api("/api/blocks/root/children")).children.find((b) => b.content === "Fresh cavity idea");
      await until(() => new URL(page.url()).searchParams.get("block") === created.id, { what: "the new page opens" });
      assert(String(created.properties?.folder || "").includes("optics/cavities"), "it lands in the folder the palette was opened over");
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
    await user.api(`/api/trash/${gone.id}`, { method: "DELETE" }); // gone for good (trash.mjs covers Recently deleted)
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

  await step("library: the page menu adds a label in place, a folder's makes a page in it; F2 renames and Delete asks first", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=${user.ws}`);
    try {
      const row = page.locator(".fileRow", { hasText: "Quantum correction" });
      await row.waitFor();
      // Add label ▸ New label… takes a name typed in the flyout.
      await row.click({ button: "right" });
      await page.locator(".ctxMenuItem", { hasText: "Add label" }).click();
      await page.locator(".ctxMenuItem", { hasText: "New label…" }).click();
      await page.keyboard.type("urgent");
      await page.keyboard.press("Enter");
      await row.locator(".labelTagBadge", { hasText: "urgent" }).waitFor();
      // A label every picked page carries is checked; picking it takes it off.
      await row.click({ button: "right" });
      assertEq(await page.locator(".ctxMenuItem", { hasText: "Delete" }).locator(".ctxMenuKey").textContent(), "Del", "Delete shows its key");
      await page.locator(".ctxMenuItem", { hasText: "Add label" }).click();
      const urgent = page.locator(".ctxSubMenu .ctxMenuItem", { hasText: "urgent" });
      await urgent.locator(".ctxMenuCheck").waitFor();
      await urgent.click();
      await row.locator(".labelTagBadge", { hasText: "urgent" }).waitFor({ state: "detached" });

      // F2 renames the selected row in place; Delete asks before it deletes.
      await row.click();
      await page.keyboard.press("F2");
      await page.locator(".fileRowRename").waitFor();
      await page.keyboard.press("Escape");
      await row.click();
      await page.keyboard.press("Delete");
      const confirm = page.locator(".confirmModal", { hasText: "Move this page to Recently deleted?" });
      await confirm.waitFor();
      await confirm.getByRole("button", { name: "Cancel", exact: true }).click();
      await row.waitFor();

      // A folder's menu starts a page filed in it.
      await page.locator(".folderRow", { hasText: "optics" }).first().click({ button: "right" });
      await page.locator(".ctxMenuItem", { hasText: "New page here" }).click();
      await until(() => new URL(page.url()).searchParams.get("block"), { what: "the new page opens" });
      const id = new URL(page.url()).searchParams.get("block");
      await until(async () => (await user.api("/api/blocks/root/children")).children.some((b) => b.id === id && b.properties?.folder === "optics"),
        { what: "the page is filed in the folder" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("tabs: closing follows viewing order through page links and tab switches", async () => {
    const made = [];
    for (const title of ["Return first", "Return second", "Return third"]) {
      made.push(await user.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: title } }));
    }
    for (let i = 0; i < made.length - 1; i++) {
      await user.api("/api/blocks", { method: "POST", body: {
        parent_id: made[i].id, content: `[Next return page](/?page=${made[i + 1].id})`,
      } });
    }
    await user.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [] } });
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${made[0].id}&ws=${user.ws}`);
    const tab = (i) => page.locator(".tabStrip .tab", { hasText: made[i].content });
    const onPage = async (i) => {
      await until(() => new URL(page.url()).searchParams.get("block") === made[i].id, { what: `${made[i].content} opens` });
      await until(() => tab(i).evaluate((el) => el.classList.contains("active")), { what: "the tab is active" });
    };
    try {
      await onPage(0);
      await page.locator(".gammaLinkCard", { hasText: "Next return page" }).click();
      await onPage(1);
      await page.locator(".gammaLinkCard", { hasText: "Next return page" }).click();
      await onPage(2);
      // The previously viewed tab is third even though first is leftmost.
      await tab(0).click();
      await onPage(0);
      await tab(0).click({ button: "middle" });
      await onPage(2);
      await tab(0).waitFor({ state: "detached" });
      // Context-menu close takes the same route; repeated closes skip first.
      await tab(2).click({ button: "right" });
      await page.locator(".ctxMenuItem", { hasText: "Close tab" }).click();
      await onPage(1);
      await tab(2).waitFor({ state: "detached" });
      await tab(1).getByRole("button", { name: `Close ${made[1].content}`, exact: true }).click();
      await page.locator(".homeListBar").waitFor();
      assertEq(await page.locator(".tabStrip .tab").count(), 0, "closing the last tab returns home without reopening closed pages");

      // Closing a background tab keeps the active page and removes that
      // background page from subsequent return destinations.
      await page.locator(".fileRow", { hasText: made[0].content }).dblclick();
      await onPage(0);
      await page.locator(".gammaLinkCard", { hasText: "Next return page" }).click();
      await onPage(1);
      await page.locator(".gammaLinkCard", { hasText: "Next return page" }).click();
      await onPage(2);
      await tab(1).getByRole("button", { name: `Close ${made[1].content}`, exact: true }).click();
      await tab(1).waitFor({ state: "detached" });
      assertEq(new URL(page.url()).searchParams.get("block"), made[2].id, "background close leaves the current page open");
      await tab(2).getByRole("button", { name: `Close ${made[2].content}`, exact: true }).click();
      await onPage(0);
      assertEq(await tab(1).count(), 0, "the closed background tab stays closed");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("tabs: closing returns to the folder or label it opened from, even with another tab open", async () => {
    const filed = await user.api("/api/blocks", { method: "POST", body: {
      parent_id: "root", content: "Return to library view", properties: { folder: "return/nested", category: "return-label" },
    } });
    const other = papers["Atomic clocks"];
    for (const category of ["", "return-label"]) {
      await user.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [{ id: other.id, title: other.content }] } });
      const ctx = await user.context(browser);
      const page = await openPage(ctx, `${server.base}/?folder=return/nested&category=${category}&ws=${user.ws}`);
      try {
        const row = page.locator(".fileRow", { hasText: filed.content });
        await row.dblclick();
        const active = page.locator(".tabStrip .tab.active", { hasText: filed.content });
        await active.waitFor();
        await active.getByRole("button", { name: `Close ${filed.content}`, exact: true }).click();
        await row.waitFor();
        const params = new URL(page.url()).searchParams;
        assertEq(params.get("folder"), "return/nested", "closing restores the nested folder");
        assertEq(params.get("category") || "", category, "closing restores the label filter");
        assertEq(await page.locator(".tabStrip .tab", { hasText: other.content }).count(), 1, "the other tab stays open");
        assertEq(await page.locator(".tabStrip .tab.active").count(), 0, "the source library view takes priority over other tabs");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    }
  });

  await step("tabs: the active tab stays in view, and ⌄ lists every open tab and opens the pick", async () => {
    const made = [];
    for (let i = 1; i <= 14; i++) made.push(await user.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: `Tab page ${i}` } }));
    const tabs = made.map((b, i) => ({ id: b.id, title: b.content, ...(i === 0 ? { pinned: true } : {}) }));
    await user.api("/api/prefs/open-tabs", { method: "PUT", body: { value: tabs } });
    const ctx = await user.context(browser);
    const target = made[made.length - 2];
    const page = await openPage(ctx, `${server.base}/?page=${target.id}&ws=${user.ws}`);
    try {
      await page.locator(".blockList").first().waitFor();
      const active = page.locator(".tabStrip .tab.active");
      await active.waitFor();
      await until(() => page.evaluate(() => {
        const strip = document.querySelector(".tabStrip").getBoundingClientRect();
        const tab = document.querySelector(".tabStrip .tab.active").getBoundingClientRect();
        return tab.left >= strip.left && tab.right <= strip.right;
      }), { what: "the active tab is scrolled into view" });
      assertEq(await page.locator(".tabStrip .tab.pinned .tabTitle").count(), 1, "a pinned tab keeps its title");
      assertEq(await page.locator(".tabStrip .tab.pinned").getByRole("button", { name: "Unpin tab" }).count(), 1, "a pin takes the close button's place");
      // The overflow button lists every tab; a filter and Enter open one.
      await page.getByRole("button", { name: "All open tabs", exact: true }).click();
      const menu = page.getByRole("dialog", { name: "All open tabs" });
      await menu.waitFor();
      assertEq(await menu.getByRole("option").count(), tabs.length, "every open tab is listed");
      await menu.getByRole("textbox", { name: "Find an open tab" }).fill("page 3");
      await until(async () => (await menu.getByRole("option").count()) === 1, { what: "the filter narrows the list" });
      await page.keyboard.press("Enter");
      await until(() => new URL(page.url()).searchParams.get("block") === made[2].id, { what: "the pick opens" });
      assertEq(await menu.count(), 0, "opening closes the list");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await user.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [] } });
    }
  });
}
