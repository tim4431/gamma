import { Account, FIELD_END, wanted } from "../harness.mjs";

// Folders and labels are blocks (docs/dev/home_library.md "Folders and
// labels"): New folder makes an empty folder that stays and lists first, a
// rename is one op on the folder tree that touches no page, folders keep an
// order of their own, deleting one unfiles its pages, the page header's
// label field makes a label or picks a folder, and dragging a page files it
// by id.
export async function folderScenarios(env) {
  const { server, browser, step, openPage, assert, assertEq, assertNoProblems, until } = env;
  if (!wanted("folders")) return;
  await server.manage("create-user", "folders-user", "folders-pw");
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
      const field = page.locator(".folderNewInput");
      await field.fill("Empty one");
      // The Enter that confirms an input method's word is the input
      // method's (composing, shared/lib/hotkeys.js): the field stays open
      // with its name and no folder is made.
      await field.dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true });
      await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
      assertEq(await field.inputValue(), "Empty one", "a composing Enter leaves the field as it was");
      assertEq((await tree("folders")).filter((f) => f.content === "Empty one").length, 0, "a composing Enter makes no folder");
      await page.keyboard.press("Enter");
      await row(page, "Empty one").waitFor();
      assertEq((await tree("folders")).filter((f) => f.content === "Empty one").length, 1, "one folder block, empty");
      await page.reload();
      await row(page, "Empty one").waitFor();
      // Recently modified (the default sort) dates an empty folder from its
      // making: the newest thing here, it leads Lab and both papers.
      const first = page.locator(".fileList .folderRow:not(.folderNewBtn), .fileList .fileRow").first();
      await until(async () => (await first.textContent()).includes("Empty one"),
        { what: "the new, empty folder leads the Recently modified listing" });

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
      await row(page, "Alpha").click({ modifiers: ["ControlOrMeta"] });
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

  await step("folders: the kind filter is per folder — a subfolder without a choice of its own shows folders and files", async () => {
    const alpha = await user.folder("Alpha");
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=${user.ws}`);
    const kind = (name) => page.locator(".homeListBar .homeViewBtn[title='" + name + "']");
    try {
      await row(page, "Alpha").waitFor();
      await kind("Files only").click();
      await row(page, "Alpha").waitFor({ state: "detached" });
      await page.goto(`${server.base}/?ws=${user.ws}&folder=${alpha}`);
      await row(page, "Gamma").waitFor();
      assertEq(await kind("Folders & files").getAttribute("aria-pressed"), "true", "the subfolder shows folders and files");
      await kind("Folders only").click();
      await page.goto(`${server.base}/?ws=${user.ws}`);
      await page.locator(".fileRow").first().waitFor();
      assertEq(await kind("Files only").getAttribute("aria-pressed"), "true", "the library keeps its own choice");
      await page.goto(`${server.base}/?ws=${user.ws}&folder=${alpha}`);
      await row(page, "Gamma").waitFor();
      assertEq(await kind("Folders only").getAttribute("aria-pressed"), "true", "and the subfolder its own");
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

      // Tab completes a folder path, as a shell or VS Code does: a partial
      // name is written out in full, one holding subfolders keeps the
      // separator so the next Tab walks into it, and Tab with nothing left
      // to write files the page there.
      await field.click();
      const input = page.locator('[data-guide="page.labelInput"]');
      await input.fill("alph");
      await page.locator(".categorySuggestionItem", { hasText: /^Alpha$/ }).waitFor();
      await page.keyboard.press("Tab");
      assertEq(await input.inputValue(), "Alpha / ", "written out in full, open for the subfolder");
      await page.locator(".categorySuggestionItem", { hasText: "Alpha / Gamma" }).waitFor();
      assertEq(await page.locator(".categorySuggestionItem").count(), 1, "and listing only what is inside Alpha");
      await page.keyboard.press("Tab");
      assertEq(await input.inputValue(), "Alpha / Gamma", "the subfolder, with nothing inside it to open");
      await page.keyboard.press("Tab");
      // the field stays open for the next name: its chips are the editing ones
      await page.locator(".categoryTagInputWrap .categoryTag.folderChip", { hasText: "Alpha / Gamma" }).waitFor();
      assertEq(await input.inputValue(), "", "the input clears for the next name");
      const gamma = await user.folder("Alpha/Gamma");
      await until(async () => (await pageOf(paper.id)).properties.folders?.includes(gamma), { what: "Tab filed it in Alpha / Gamma" });

      // A path no folder has makes one under the folder it names, not a top
      // folder called "Beta / Nested one".
      await input.fill("Beta / Nested one");
      await page.locator(".categorySuggestionItem", { hasText: "New folder “Beta / Nested one”" }).click();
      await page.locator(".categoryTagInputWrap .categoryTag.folderChip", { hasText: "Beta / Nested one" }).waitFor();
      assert((await tree("folders")).find((f) => f.content === "Beta").children.some((f) => f.content === "Nested one"),
        "the new folder is a child of Beta");
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

  // The search's folder chip goes to the server as its scope: a note in the
  // folder is found though the newest 20 notes are all elsewhere. Each
  // group pages with "Show more".
  await step("folders: search keeps its notes to a folder chip on the server, and Show more pages them", async () => {
    const deep = await user.folder("Deep");
    const filed = await user.api("/api/pages", { method: "POST", body: { title: "Deep paper", folders: [deep] } });
    await user.api("/api/blocks", { method: "POST", body: { parent_id: filed.id, content: "narwhalscope filed note" } });
    const other = await user.api("/api/pages", { method: "POST", body: { title: "Loose notes" } });
    await user.api(`/api/blocks/${other.id}/children`, { method: "PUT",
      body: { blocks: Array.from({ length: 25 }, (_, i) => ({ content: `narwhalscope loose note ${i}` })) } });
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=${user.ws}`);
    const hits = page.locator(".searchPopover .searchResult", { hasText: "narwhalscope" });
    const more = page.locator(".searchPopover .loadMoreBtn", { hasText: "Show more" });
    try {
      await page.click("button[aria-label='Search']");
      const search = page.locator(".searchPopover .searchInput");
      await search.fill("narwhalscope");
      await until(async () => (await hits.count()) === 20, { what: "the first page of notes" });
      await more.click();
      await until(async () => (await hits.count()) === 26, { what: "the next page appended" });
      assertEq(await more.count(), 0, "nothing more to show");

      await search.fill("");
      await search.fill("deep");
      await page.locator(".searchLabelSuggest .categorySuggestionItem", { hasText: "Deep" }).waitFor();
      await search.press("Tab");
      await page.locator(".searchPopover .searchChip", { hasText: "Deep" }).waitFor();
      await search.fill("narwhalscope");
      await page.locator(".searchPopover .searchResult", { hasText: "narwhalscope filed note" }).waitFor();
      await until(async () => (await hits.count()) === 1, { what: "only the folder's note" });
      assertEq(await more.count(), 0, "the scope holds one note");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // Typed qualifiers (search/searchFilters.js) beside the chips: the
  // listing's pages they keep go to the server by id, so the notes found
  // are the folder's, or all but its, however many outrank them. A key
  // typed is completed from the dropdown; a value Gamma cannot apply is
  // searched as text under an amber line.
  await step("folders: search qualifiers narrow on the server, and the dropdown completes them", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=${user.ws}`);
    const hits = page.locator(".searchPopover .searchResult", { hasText: "narwhalscope" });
    const chips = page.locator(".searchPopover .searchChip");
    const rows = page.locator(".searchPopover .searchLabelSuggest [role='option']");
    // The body of the panel's next notes search that `test` accepts.
    const notesAsked = (test) => page.waitForResponse((r) => new URL(r.url()).pathname === "/api/block-search"
      && r.request().method() === "POST" && test(r.request().postDataJSON() || {}));
    try {
      await page.click("button[aria-label='Search']");
      const search = page.locator(".searchPopover .searchInput");
      await search.waitFor();
      await page.locator(".searchPopover .searchHint", { hasText: "Narrow with label: folder:" }).waitFor();

      // Everything but Deep: the 20 newest loose notes, then the other 5 on
      // a Show more that pages the same set.
      let asked = notesAsked((b) => b.q === "narwhalscope" && Array.isArray(b.pages));
      await search.fill("-folder:Deep narwhalscope");
      const body = (await asked).request().postDataJSON();
      assert(body.pages.length > 0, "the pages outside Deep go along");
      await until(async () => (await hits.count()) === 20, { what: "the first page of loose notes" });
      assertEq(await hits.filter({ hasText: "filed note" }).count(), 0, "none from Deep");
      await page.locator(".searchPopover .searchSection", { hasText: "-folder:Deep" }).waitFor();
      await page.locator(".searchPopover .loadMoreBtn", { hasText: "Show more" }).last().click();
      await until(async () => (await hits.count()) === 25, { what: "the rest of the loose notes" });
      assertEq(await hits.filter({ hasText: "filed note" }).count(), 0, "still none from Deep");

      // Capitalised as an iPad's first word is, and never Tabbed: no chip.
      await search.fill("Folder:deep narwhalscope");
      await page.locator(".searchPopover .searchResult", { hasText: "narwhalscope filed note" }).waitFor();
      await until(async () => (await hits.count()) === 1, { what: "only the folder's note" });
      assertEq(await chips.count(), 0, "a typed qualifier makes no chip");

      // in: keeps the block kinds: no highlight holds the word.
      asked = notesAsked((b) => b.q === "narwhalscope" && b.kinds?.join() === "highlight");
      await search.fill("in:highlights narwhalscope");
      await asked;
      await page.locator(".searchPopover .searchHint", { hasText: "No matches." }).waitFor();
      assertEq(await hits.count(), 0, "no notes group");

      // The dropdown: a key once three letters begin it, then its values;
      // Tab completes the text and adds no chip.
      await search.fill("fol");
      await rows.filter({ hasText: "folder:" }).first().waitFor();
      assertEq(await rows.first().getAttribute("aria-selected"), "true", "the first row is the one Tab takes");
      await search.press("Tab");
      assertEq(await search.inputValue(), "folder:");
      await rows.filter({ hasText: "Alpha" }).first().waitFor(); // the folders, by path
      await page.keyboard.type("de");
      await until(async () => (await rows.count()) === 1, { what: "the one folder the value begins" });
      await search.press("Tab");
      assertEq(await search.inputValue(), "folder:Deep ", "completed, with a space after it");
      assertEq(await chips.count(), 0, "a completed qualifier is text, not a chip");
      await page.locator(".searchPopover .searchResult", { hasText: "Deep paper" }).waitFor();

      // A clause already whole: Tab leaves the text and moves the caret past
      // it at once, so the next keys typed at the end stay there.
      await search.fill("folder:Deep x");
      await search.press(FIELD_END);
      await search.press("ArrowLeft");
      await search.press("ArrowLeft");
      await until(async () => (await rows.count()) === 1, { what: "the folder the caret's value names" });
      await search.press("Tab");
      assertEq(await search.inputValue(), "folder:Deep x", "nothing to complete");
      assertEq(await search.evaluate((el) => el.selectionStart), 12, "the caret after the clause's space");
      await search.press(FIELD_END);
      await page.keyboard.type("yz");
      assertEq(await search.inputValue(), "folder:Deep xyz", "typed where the caret was put");

      // A name no label has is searched as text, and the line says so.
      await search.fill("label:nosuch narwhalscope");
      await page.locator(".searchPopover .searchHint.warn",
        { hasText: "“label:nosuch” is searched as text: no label is named nosuch" }).waitFor();
      await page.locator(".searchPopover .searchHint", { hasText: "No matches." }).waitFor();
      assertEq(await hits.count(), 0, "a label that does not exist finds nothing");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });
}
