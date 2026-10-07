import { Account, wanted } from "../harness.mjs";

// Folders and labels are blocks (docs/dev/home_library.md "Folders and
// labels"): New folder makes an empty folder that stays, a rename is one op
// on the folder tree that touches no page, folders keep an order of their
// own, deleting one unfiles its pages, the page header's label field makes
// a label or picks a folder, and dragging a page files it by id.
export async function folderScenarios(env) {
  const { server, browser, step, openPage, assert, assertEq, assertNoProblems, until } = env;
  if (!wanted("folders")) return;
  server.manage("create-user", "folders-user", "folders-pw");
  const user = await new Account(server, "folders-user", "folders-pw").login();
  const tree = async (name) => (await user.api(`/api/blocks/${name}/subtree`)).block.children;
  const pageOf = async (id) => (await user.api("/api/blocks/root/children")).children.find((b) => b.id === id);
  const labId = await user.folder("Lab");
  const paper = await user.api("/api/pages", { method: "POST", body: { title: "Filed paper", folders: [labId] } });
  const loose = await user.api("/api/pages", { method: "POST", body: { title: "Loose paper" } });
  const row = (page, name) => page.locator(".fileList .folderRow", { hasText: name });

  // A drag from `source` to `target`, dropped in its middle or by its top
  // (`before`) or bottom (`after`) edge — the custom order's place beside it.
  async function drag(page, source, target, edge) {
    const dt = await page.evaluateHandle(() => new DataTransfer());
    await source.dispatchEvent("dragstart", { dataTransfer: dt });
    const box = await target.boundingBox();
    const y = edge === "before" ? box.y + 2 : edge === "after" ? box.y + box.height - 2 : box.y + box.height / 2;
    const at = { dataTransfer: dt, clientX: box.x + box.width / 2, clientY: y };
    await target.dispatchEvent("dragover", at);
    await target.dispatchEvent("drop", at);
  }

  await step("folders: New folder makes an empty folder that stays after a reload; a rename touches no page", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=${user.ws}`);
    try {
      await page.locator(".folderNewBtn", { hasText: "New folder" }).click();
      await page.locator(".folderNewInput").fill("Empty one");
      await page.keyboard.press("Enter");
      await row(page, "Empty one").waitFor();
      assertEq((await tree("folders")).filter((f) => f.content === "Empty one").length, 1, "one folder block, empty");
      await page.reload();
      await row(page, "Empty one").waitFor();

      const before = await pageOf(paper.id);
      await row(page, "Lab").click({ button: "right" });
      await page.locator(".ctxMenuItem", { hasText: "Rename" }).click();
      await page.locator(".fileList .folderNewInput").fill("Lab, renamed / 2026");
      await page.keyboard.press("Enter");
      await row(page, "Lab, renamed / 2026").waitFor();
      assertEq((await tree("folders")).find((f) => f.id === labId).content, "Lab, renamed / 2026", "the folder block is renamed");
      const after = await pageOf(paper.id);
      assertEq(after.updated_at, before.updated_at, "the page's modified time did not move");
      assertEq(JSON.stringify(after.properties.folders), JSON.stringify([labId]), "the page is filed by the same id");
      await row(page, "Lab, renamed / 2026").dblclick();
      await page.locator(".fileRow", { hasText: "Filed paper" }).waitFor();
      assertEq(new URL(page.url()).searchParams.get("folder"), labId, "the folder view's URL names the id");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("folders: in the custom order a folder dropped on another's edge takes its place beside it", async () => {
    for (const name of ["Alpha", "Beta", "Gamma"]) await user.folder(name);
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=${user.ws}`);
    const order = async () => (await page.locator(".fileList .folderRow:not(.folderNewBtn) .folderName").allTextContents());
    try {
      await row(page, "Gamma").waitFor();
      await page.locator(".homeListBar .uiSelectBtn").first().click();
      await page.locator(".uiSelectMenu .ctxMenuItem", { hasText: "Custom order" }).click();
      await until(async () => (await order()).join("|") === "Lab, renamed / 2026|Empty one|Alpha|Beta|Gamma",
        { what: "the custom order is the folders' own" });
      await drag(page, row(page, "Gamma"), row(page, "Alpha"), "before");
      await until(async () => (await order()).join("|") === "Lab, renamed / 2026|Empty one|Gamma|Alpha|Beta",
        { what: "Gamma lands before Alpha" });
      await drag(page, row(page, "Lab, renamed / 2026"), row(page, "Beta"), "after");
      await until(async () => (await order()).join("|") === "Empty one|Gamma|Alpha|Beta|Lab, renamed / 2026",
        { what: "the renamed folder lands after Beta" });
      assertEq((await tree("folders")).map((f) => f.content).join("|"), "Empty one|Gamma|Alpha|Beta|Lab, renamed / 2026",
        "the tree keeps the order");
      // dropped in the middle, a folder moves in
      await drag(page, row(page, "Gamma"), row(page, "Alpha"));
      await until(async () => !(await order()).includes("Gamma"), { what: "Gamma moved into Alpha" });
      assertEq((await tree("folders")).find((f) => f.content === "Alpha").children.map((f) => f.content).join("|"), "Gamma");
      await page.reload();
      await until(async () => (await order()).join("|") === "Empty one|Alpha|Beta|Lab, renamed / 2026", { what: "the order stays" });
      // several selected folders dropped on an edge all land there, in their listed order (Beta picked first)
      await row(page, "Beta").click();
      await row(page, "Alpha").click({ modifiers: ["Control"] });
      await drag(page, row(page, "Beta"), row(page, "Empty one"), "before");
      await until(async () => (await order()).join("|") === "Alpha|Beta|Empty one|Lab, renamed / 2026",
        { what: "Alpha and Beta land before Empty one" });
      assertEq((await tree("folders")).map((f) => f.content).join("|"), "Alpha|Beta|Empty one|Lab, renamed / 2026",
        "the tree keeps the order");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("folders: a page dropped on a folder is filed in it; on the back row it leaves; a crumb takes a drop too", async () => {
    const [alpha, beta] = await Promise.all(["Alpha", "Beta"].map((name) => user.folder(name)));
    const gamma = await user.folder("Alpha/Gamma");
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=${user.ws}`);
    try {
      const looseRow = page.locator(".fileRow", { hasText: "Loose paper" });
      await drag(page, looseRow, row(page, "Beta"));
      await until(async () => (await pageOf(loose.id)).properties.folders?.includes(beta), { what: "filed in Beta" });
      await row(page, "Beta").dblclick();
      await looseRow.waitFor();
      await drag(page, looseRow, page.locator(".folderBackRow"));
      await until(async () => !(await pageOf(loose.id)).properties.folders?.length, { what: "out of Beta again" });
      await looseRow.waitFor({ state: "detached" });
      // into a subfolder, then onto the crumb of the folder above: filing
      // into a folder keeps the page's other folders, but one below refines
      // the one above away
      await user.api(`/api/blocks/${loose.id}`, { method: "PUT", body: { properties: { folders: [alpha] } } });
      await page.goto(`${server.base}/?ws=${user.ws}&folder=${alpha}`);
      await drag(page, page.locator(".fileRow", { hasText: "Loose paper" }), row(page, "Gamma"));
      await until(async () => JSON.stringify((await pageOf(loose.id)).properties.folders) === JSON.stringify([gamma]),
        { what: "Gamma refines Alpha away" });
      await page.goto(`${server.base}/?ws=${user.ws}&folder=${gamma}`);
      const crumb = page.locator(".folderCurrent .crumbBtn", { hasText: "Alpha" });
      await drag(page, page.locator(".fileRow", { hasText: "Loose paper" }), crumb);
      await until(async () => JSON.stringify((await pageOf(loose.id)).properties.folders) === JSON.stringify([gamma, alpha]),
        { what: "the crumb files it in Alpha too" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("folders: a pin is stored on the folder block, and the Pinned strip reads it", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=${user.ws}`);
    try {
      await row(page, "Empty one").click({ button: "right" });
      await page.locator(".ctxMenuItem", { hasText: "Pin" }).first().click();
      const card = page.locator(".pinnedStrip .pageCard", { hasText: "Empty one" });
      await card.waitFor();
      assert((await tree("folders")).find((f) => f.content === "Empty one").properties.pinned, "pinned on the block");
      await page.reload();
      await card.waitFor();
      await card.locator(".pinBtn").click();
      await card.waitFor({ state: "detached" });
      assert(!(await tree("folders")).find((f) => f.content === "Empty one").properties.pinned, "unpinned on the block");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("folders: deleting a folder takes it off its pages and keeps them", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=${user.ws}`);
    try {
      await row(page, "Lab, renamed / 2026").click({ button: "right" });
      await page.locator(".ctxMenuItem", { hasText: "Delete" }).click();
      const confirm = page.locator(".confirmModal", { hasText: "It contains 1 page" });
      await confirm.getByRole("button", { name: "Keep pages", exact: true }).click();
      await row(page, "Lab, renamed / 2026").waitFor({ state: "detached" });
      assert(!(await tree("folders")).some((f) => f.id === labId), "the folder block is gone");
      const kept = await pageOf(paper.id);
      assert(kept, "the page stays in the library");
      assert(!kept.properties.folders?.length, "and is filed nowhere");
      await page.locator(".fileRow", { hasText: "Filed paper" }).waitFor();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("folders: the page header's label field makes a new label and picks an existing folder by name", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${paper.id}&ws=${user.ws}`);
    try {
      const field = page.locator('[data-guide="page.labels"]');
      await field.click();
      await page.locator('[data-guide="page.labelInput"]').fill("Fresh, label");
      await page.locator(".categorySuggestionItem", { hasText: "New label “Fresh, label”" }).waitFor();
      await page.keyboard.press("Enter");
      await field.locator(".categoryBadge", { hasText: "Fresh, label" }).waitFor();
      const fresh = (await tree("labels")).find((l) => l.content === "Fresh, label");
      assert(fresh, "a label block, its comma kept");
      await until(async () => (await pageOf(paper.id)).properties.labels?.includes(fresh.id), { what: "the page carries it" });

      await field.click();
      await page.locator('[data-guide="page.labelInput"]').fill("beta");
      // the folder itself, by its whole name: "New label “beta”" holds it too
      await page.locator(".categorySuggestionItem", { hasText: /^Beta$/ }).waitFor();
      await page.keyboard.press("Enter");
      await field.locator(".categoryBadge.folderChip", { hasText: "Beta" }).waitFor();
      const beta = await user.folder("Beta");
      await until(async () => (await pageOf(paper.id)).properties.folders?.includes(beta), { what: "filed in the folder of that name" });
      assertEq((await tree("folders")).filter((f) => f.content.toLowerCase() === "beta").length, 1, "no folder was made");

      // Tab completes a partial name to the first suggestion.
      await field.click();
      await page.locator('[data-guide="page.labelInput"]').fill("alph");
      await page.locator(".categorySuggestionItem", { hasText: /^Alpha$/ }).waitFor();
      await page.keyboard.press("Tab");
      // the field stays open for the next name: its chips are the editing ones
      await page.locator(".categoryTagInputWrap .categoryTag.folderChip", { hasText: /^Alpha$/ }).waitFor();
      assertEq(await page.locator('[data-guide="page.labelInput"]').inputValue(), "", "the input clears for the next name");
      const alpha = await user.folder("Alpha");
      await until(async () => (await pageOf(paper.id)).properties.folders?.includes(alpha), { what: "Tab filed it in Alpha" });
      await page.keyboard.press("Escape");

      // Search: a matching label is offered, but only Tab makes it a filter.
      await page.click("button[aria-label='Search']");
      const search = page.locator(".searchPopover .searchInput");
      await search.fill("fresh");
      await page.locator(".searchLabelSuggest .categorySuggestionItem", { hasText: "Fresh, label" }).waitFor();
      await search.press("Enter");
      assertEq(await page.locator(".searchPopover .searchChip").count(), 0, "Enter leaves the query a query");
      assertEq(await search.inputValue(), "fresh");
      await search.press("Tab");
      await page.locator(".searchPopover .searchChip", { hasText: "Fresh, label" }).waitFor();
      assertEq(await search.inputValue(), "", "Tab turned the query into the label filter");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });
}
