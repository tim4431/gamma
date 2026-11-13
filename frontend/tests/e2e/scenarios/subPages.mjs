import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { Account, PYTHON, live, pressMouseBack, wanted } from "../harness.mjs";
import { editRow, row } from "./notes.mjs";
import { openDialog } from "./transfers.mjs";

// Sub-pages (docs/dev/home_library.md "Sub-pages"): a page inside another
// carries `parent_page` and no folders of its own. /page and "New page
// inside" make one; the library lists only the pages outside any, while
// the parent's "N pages inside" panel and the child's crumbs show where it
// is; Quick open finds it with its path; "Move to library" and "Move into
// page…" take it out and put one in, keeping to the topmost pages of a
// selection, Move to folder files a selection's library pages and says what
// it left out, Move to page… offers a block the open page's pages inside
// first, and the move pickers match and show a path; deleting the parent
// takes the pages inside along and Restore brings the tree back, from
// Recently deleted, a
// link's missing-page notice (which then opens the linked page) or the
// open page's own notice; a page share of the parent reaches the pages
// inside, one of the child reaches nothing above. A label on a page inside
// counts in the label's tile and view, the search's folder chip keeps its
// notes, and its Properties name the page it is inside. A page's row and
// card count the pages inside it, a pinned page inside shows its path and
// its preview, and Quick open, the [[ picker and the @ menu match it on its
// top's folder and its label's name. A Notion export's
// pages inside pages come in as sub-pages, reviewed under their page, and
// a page's export takes the pages inside it with "Include pages inside".
// The panel follows a page made, moved in or taken out elsewhere when the
// window comes back; its rows have the page menu and drag, and a page
// dropped on a panel row, a crumb or a library row or card goes inside
// that page (never into one inside it), one dropped on Home to the library.
export async function subPageScenarios(env) {
  const { server, browser, step, openPage, assert, assertEq, assertNoProblems, until } = env;
  if (!wanted("sub-pages")) return;
  await server.manage("create-user", "subpages-user", "subpages-pw");
  const user = await new Account(server, "subpages-user", "subpages-pw").login();
  const shelf = await user.folder("Shelf");
  const top = await user.api("/api/pages", { method: "POST", body: { title: "Family top", folders: [shelf] } });
  await user.api(`/api/pages/${top.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
    { op: "insert", id: "subintro", parent: top.id, position: "a0", content: "Intro" }] } });
  const loose = await user.api("/api/pages", { method: "POST", body: { title: "Loose page" } });
  const props = async (id) => (await user.api(`/api/blocks/${id}/subtree`)).block.properties || {};
  const listing = async () => (await user.api("/api/blocks/root/children")).children;
  const family = async (id) => user.api(`/api/pages/${id}/family`);
  const titles = (pages) => pages.map((p) => p.title).join("|");
  const home = `${server.base}/?ws=${user.ws}`;
  const at = (id) => `${server.base}/?page=${id}&ws=${user.ws}`;
  // the library from an open page, through the tab strip's Home (a load
  // of the bare address would put the open tab back)
  const goHome = async (page) => {
    await page.getByRole("button", { name: "Home", exact: true }).click();
    await page.locator(".fileList").waitFor();
  };
  const blockOf = (page) => new URL(page.url()).searchParams.get("block");
  // a library row by its whole title (a row's preview may name another page)
  const fileRow = (page, title) => page.locator(".fileRow").filter({ has: page.locator(".fileRowName", { hasText: new RegExp(`^${title}$`) }) });
  const panel = (page) => page.locator(".subPagesPanel");
  const panelRows = (page) => panel(page).locator(".subPageRow:not(.subPageNew)");
  const crumbs = (page) => page.locator(".pageCrumbs .crumbBtn");
  // a page picker's rows (library/PagePicker.jsx) as "title path": the
  // page's title, then the pages a page inside is in
  const optionTexts = (dialog) => dialog.getByRole("option").evaluateAll((els) => els.map((el) =>
    [el.querySelector("strong"), el.querySelector("small")].filter(Boolean).map((n) => n.innerText.trim()).join(" ")));
  let childOne = "", childTwo = "";

  await step("sub-pages: /page in a filed page makes a page inside it, kept out of the library; the panel lists it and its crumbs lead back", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, at(top.id));
    try {
      await editRow(page, "Intro");
      await page.keyboard.type(" /page");
      await page.waitForSelector(".slashMenu");
      assertEq(await page.locator(".slashMenu .slashMenuItem.selected .slashMenuLabel").innerText(), "New page", "/page is New page");
      await page.keyboard.press("Enter");
      await page.waitForSelector(".titleEdit", { timeout: 5000 });
      childOne = await until(() => { const id = blockOf(page); return id !== top.id && id; }, { what: "the new page opens" });
      await page.keyboard.type("Child one");
      await page.keyboard.press("Enter");
      await until(async () => (await user.api(`/api/blocks/${childOne}/subtree`)).block.content === "Child one", { what: "the title saved" });
      const stored = await props(childOne);
      assertEq(stored.parent_page, top.id, "the new page is inside the page /page was typed in");
      assertEq(stored.folders, undefined, "and carries no folders of its own");
      const summary = (await listing()).find((p) => p.id === childOne);
      assertEq(summary?.parent, top.id, "the listing names its parent");

      // its crumbs name the parent, and a crumb opens it
      await until(async () => (await crumbs(page).allInnerTexts()).join("|") === "Family top", { what: "the crumbs name the parent" });
      await crumbs(page).first().click();
      await until(() => blockOf(page) === top.id, { what: "the crumb opens the parent" });
      await until(async () => (await row(page, "Intro").locator(".blockRefChip").innerText()) === "Child one", { what: "the chip where /page was typed" });
      await until(async () => (await panelRows(page).allInnerTexts()).join("|") === "Child one", { what: "the panel lists the page inside" });
      assert((await panel(page).locator(".backlinksHead").innerText()).includes("1 page inside"), "the panel counts it");
      assertEq(await crumbs(page).count(), 0, "a library page has no crumbs");

      // the library and the folder list the parent only, and count one page
      await goHome(page);
      await fileRow(page, "Family top").waitFor({ timeout: 15000 });
      assertEq(await fileRow(page, "Child one").count(), 0, "the home library leaves the sub-page out");
      assert((await page.locator(".fileList .folderRow", { hasText: "Shelf" }).innerText()).includes("1 page"), "the folder counts its one library page");
      await page.locator(".fileList .folderRow", { hasText: "Shelf" }).dblclick();
      await until(() => new URL(page.url()).searchParams.get("folder") === shelf, { what: "the folder opens" });
      await fileRow(page, "Family top").waitFor({ timeout: 15000 });
      assertEq(await fileRow(page, "Child one").count(), 0, "the folder's listing leaves it out too");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("sub-pages: Quick open finds a page inside another and shows its path", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, at(loose.id));
    try {
      await page.locator(".blockList").first().waitFor();
      await page.keyboard.press("ControlOrMeta+p");
      const dialog = page.getByRole("dialog", { name: "Open a page" });
      await dialog.waitFor();
      await dialog.getByRole("textbox", { name: "Search pages, files, folders and labels" }).fill("Child one");
      const option = dialog.locator('[role="option"][data-kind="page"]').filter({ hasText: "Child one" }).first();
      await option.waitFor();
      assert((await option.innerText()).includes("Family top"), `the row names the page it is in: ${await option.innerText()}`);
      await option.click();
      await until(() => blockOf(page) === childOne, { what: "the pick opens" });
      await until(async () => (await crumbs(page).allInnerTexts()).join("|") === "Family top", { what: "its crumbs" });
      // filed where its top is: its header shows no folder of its own
      await page.locator('[data-guide="page.labels"]').waitFor();
      assertEq(await page.locator('[data-guide="page.labels"] .folderChip').count(), 0, "no folder chip on a page inside another");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("sub-pages: New page inside makes another page inside and opens it with its title in edit", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, at(top.id));
    try {
      await panelRows(page).first().waitFor({ timeout: 15000 });
      await panel(page).locator(".subPageNew", { hasText: "New page inside" }).click();
      await page.waitForSelector(".titleEdit", { timeout: 5000 });
      childTwo = await until(() => { const id = blockOf(page); return id !== top.id && id; }, { what: "the new page opens" });
      await until(() => page.locator(".titleEdit").evaluate((el) => el === document.activeElement || el.contains(document.activeElement)),
        { what: "the title takes the typing" });
      await page.keyboard.type("Child two");
      await page.keyboard.press("Enter");
      await until(async () => (await user.api(`/api/blocks/${childTwo}/subtree`)).block.content === "Child two", { what: "the title saved" });
      assertEq((await props(childTwo)).parent_page, top.id, "inside the page whose panel made it");
      await page.getByRole("button", { name: "Back", exact: true }).click();
      await until(() => blockOf(page) === top.id, { what: "Back returns to the parent" });
      await until(async () => (await panelRows(page).allInnerTexts()).join("|") === "Child one|Child two", { what: "the panel lists both" });
      assert((await panel(page).locator(".backlinksHead").innerText()).includes("2 pages inside"), "the panel counts two");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("sub-pages: the tab menu's Move to library files a page in its top's folder; the page menu's Move into page… puts one inside", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, at(childTwo));
    try {
      await until(async () => (await crumbs(page).allInnerTexts()).join("|") === "Family top", { what: "the crumbs of the page inside" });
      await page.locator(".tabStrip .tab", { hasText: "Child two" }).click({ button: "right" });
      await page.locator(".ctxMenuItem", { hasText: "Move to library" }).click();
      await until(async () => !(await props(childTwo)).parent_page, { what: "out of the page" });
      assertEq(JSON.stringify((await props(childTwo)).folders), JSON.stringify([shelf]), "filed in its former top's folder");
      await until(async () => (await crumbs(page).count()) === 0, { what: "the crumbs go" });
      assertEq(titles((await family(top.id)).sub_pages), "Child one", "the parent keeps one page inside");

      await goHome(page);
      await fileRow(page, "Loose page").waitFor({ timeout: 15000 });
      await fileRow(page, "Child two").waitFor();
      await fileRow(page, "Loose page").click({ button: "right" });
      await page.locator(".ctxMenuItem", { hasText: "Move into page…" }).click();
      const dialog = page.getByRole("dialog", { name: "Move into page" });
      await dialog.waitFor();
      await dialog.getByRole("textbox").fill("Family");
      // the page by its title, then the page inside it by its path
      await until(async () => (await optionTexts(dialog)).join("|") === "Family top|Child one Family top",
        { what: "the picker matches the title and the path" });
      await dialog.getByRole("option").filter({ has: page.locator("strong", { hasText: /^Family top$/ }) }).click();
      await fileRow(page, "Loose page").waitFor({ state: "detached" });
      const stored = await props(loose.id);
      assertEq(stored.parent_page, top.id, "inside the picked page");
      assertEq(stored.folders, undefined, "with no folders of its own");
      assertEq(titles((await family(top.id)).sub_pages), "Loose page|Child one", "the target lists it");
      await page.goto(at(top.id));
      await until(async () => (await panelRows(page).allInnerTexts()).join("|") === "Loose page|Child one", { what: "the target's panel lists it" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("sub-pages: Move to page… offers the open page's pages inside first, each naming where it is, and a block moves into one", async () => {
    await user.api(`/api/pages/${top.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "submove1", parent: top.id, position: "a8", content: "Goes inside" }] } });
    const ctx = await user.context(browser);
    const page = await openPage(ctx, at(top.id));
    try {
      const wrap = page.locator(".sortableBlockWrap", { hasText: "Goes inside" }).first();
      await wrap.hover();
      await wrap.locator(".dragHandle").click();
      await page.locator(".ctxMenuItem", { hasText: "Move to page…" }).click();
      const dialog = page.getByRole("dialog", { name: "Move block to page" });
      await dialog.waitFor();
      const names = () => dialog.locator('[role="option"] strong').allInnerTexts();
      await until(async () => (await names()).slice(0, 2).sort().join("|") === "Child one|Loose page", { what: "the open page's pages inside lead" });
      assertEq(await dialog.locator(".quickOpenSection").first().innerText(), "Inside this page");
      assert(!(await names()).includes("Family top"), "the open page is no target");
      await dialog.getByRole("textbox").fill("child one");
      const option = dialog.getByRole("option").filter({ has: page.locator("strong", { hasText: /^Child one$/ }) });
      await until(async () => (await dialog.getByRole("option").count()) === 1, { what: "the query narrows to the page" });
      assertEq(await option.locator("small").innerText(), "Family top", "a page inside names the page it is in");
      await page.keyboard.press("Enter");
      await until(async () => (await user.api(`/api/blocks/${childOne}/subtree`)).block.children.some((b) => b.content === "Goes inside"),
        { what: "the block moved into the page inside" });
      await until(async () => (await page.locator(".sortableBlockWrap", { hasText: "Goes inside" }).count()) === 0, { what: "and left the open page" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("sub-pages: deleting a page says the pages inside go with it; Recently deleted lists one entry and Restore brings the tree back", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, home);
    try {
      await fileRow(page, "Family top").click({ button: "right" });
      await page.locator(".ctxMenuItem", { hasText: "Delete" }).click();
      const confirm = page.locator(".confirmModal", { hasText: "Move \"Family top\" and the 2 pages inside it to Recently deleted?" });
      await confirm.waitFor();
      await confirm.getByRole("button", { name: "Delete", exact: true }).click();
      await fileRow(page, "Family top").waitFor({ state: "detached" });
      const trash = (await user.api("/api/trash")).pages;
      assertEq(trash.map((p) => p.id).join("|"), top.id, "one entry, the page deleted");
      assertEq(trash[0].sub_pages, 2, "with its two pages inside");
      assert(!(await listing()).some((p) => [top.id, childOne, loose.id].includes(p.id)), "the tree left the library");

      await page.getByRole("button", { name: "Recently deleted", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Recently deleted" });
      const entry = dialog.locator(`.aiProvRow[data-page="${top.id}"]`);
      await entry.waitFor();
      assert((await entry.innerText()).includes("with 2 pages inside"), `the entry counts the pages inside: ${await entry.innerText()}`);
      assertEq(await dialog.locator(`.aiProvRow[data-page="${childOne}"], .aiProvRow[data-page="${loose.id}"]`).count(), 0,
        "the pages inside have no entries of their own");
      await entry.getByRole("button", { name: "Restore", exact: true }).click();
      await entry.waitFor({ state: "detached" });
      await dialog.getByRole("button", { name: "Close", exact: true }).click();
      await fileRow(page, "Family top").waitFor();
      assertEq((await props(childOne)).parent_page, top.id, "the page inside is inside again");
      assertEq(titles((await family(top.id)).sub_pages), "Loose page|Child one", "the tree came back whole");

      await page.goto(at(top.id));
      await until(async () => (await panelRows(page).allInnerTexts()).join("|") === "Loose page|Child one", { what: "the panel lists them again" });
      await panelRows(page).filter({ hasText: "Child one" }).click();
      await until(() => blockOf(page) === childOne, { what: "the panel row opens the page" });
      await until(async () => (await crumbs(page).allInnerTexts()).join("|") === "Family top", { what: "its crumbs" });
      await crumbs(page).first().click();
      await until(() => blockOf(page) === top.id, { what: "the crumb leads back" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("sub-pages: a page share reaches the pages inside through the panel, the chip and the crumbs; a share of the page inside reveals nothing above; a folder share reaches them under their top", async () => {
    const { token } = await user.api(`/api/share/${top.id}`, { method: "POST" });
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const page = await openPage(ctx, `${server.base}/?share=${token}`);
    try {
      await page.waitForSelector(".readOnlyTitle", { timeout: 15000 });
      assert((await page.textContent(".readOnlyTitle")).includes("Family top"), "the shared page");
      await until(async () => (await panelRows(page).allInnerTexts()).join("|") === "Loose page|Child one", { what: "the visitor's panel" });
      assertEq(await panel(page).locator(".subPageNew").count(), 0, "a visitor makes no page");
      await panelRows(page).filter({ hasText: "Child one" }).click();
      await until(() => new URL(page.url()).searchParams.get("page") === childOne, { what: "the panel opens the page inside" });
      await until(async () => (await page.textContent(".readOnlyTitle")).includes("Child one"), { what: "its title" });
      await until(async () => (await crumbs(page).allInnerTexts()).join("|") === "Family top", { what: "its crumbs in the share view" });
      await crumbs(page).first().click();
      await until(() => !new URL(page.url()).searchParams.get("page"), { what: "the crumb returns to the shared page" });
      await until(async () => (await page.textContent(".readOnlyTitle")).includes("Family top"), { what: "the shared page's title" });
      const chip = row(page, "Intro").locator(".blockRefChip");
      await until(async () => (await chip.innerText()) === "Child one", { what: "the chip names the page inside" });
      await chip.click();
      await until(() => new URL(page.url()).searchParams.get("page") === childOne, { what: "the chip opens the page inside" });
      await until(async () => (await page.textContent(".readOnlyTitle")).includes("Child one"), { what: "its title, from the chip" });
      await page.goBack();
      await until(async () => (await page.textContent(".readOnlyTitle")).includes("Family top"), { what: "Back replays the shared page" });
      // The mouse's back button keeps the window's history here, which is the share view's navigation.
      await chip.click();
      await until(() => new URL(page.url()).searchParams.get("page") === childOne, { what: "the chip again" });
      await pressMouseBack(page);
      await until(async () => (await page.textContent(".readOnlyTitle")).includes("Family top"), { what: "the mouse's back button replays it too" });
      assertNoProblems(page);
    } finally { await ctx.close(); }

    const { token: childToken } = await user.api(`/api/share/${childOne}`, { method: "POST" });
    const cctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const child = await openPage(cctx, `${server.base}/?share=${childToken}`);
    try {
      await child.waitForSelector(".readOnlyTitle", { timeout: 15000 });
      await until(async () => (await child.textContent(".readOnlyTitle")).includes("Child one"), { what: "the shared page inside" });
      await child.waitForTimeout(500); // the family read lands after the page
      assertEq(await child.locator(".pageCrumbs").count(), 0, "no crumbs to a page the share does not reach");
      const seen = await (await fetch(`${server.base}/api/pages/${childOne}/family?share=${childToken}`)).json();
      assertEq(JSON.stringify(seen.ancestors), "[]", "the family call names no page above");
      assertEq((await fetch(`${server.base}/api/pages/${top.id}/family?share=${childToken}`)).status, 403, "nor reads the parent's");
      assertNoProblems(child);
    } finally { await cctx.close(); }

    // a folder share reaches the pages inside its pages: listed under
    // their top, not in the folder's listing
    const { token: folderToken } = await user.api(`/api/share/folder/${shelf}`, { method: "POST" });
    const fctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const folder = await openPage(fctx, `${server.base}/?share=${folderToken}`);
    try {
      await fileRow(folder, "Family top").waitFor({ timeout: 15000 });
      await fileRow(folder, "Child two").waitFor();
      assertEq(await fileRow(folder, "Child one").count() + await fileRow(folder, "Loose page").count(), 0,
        "the shared folder lists its library pages only");
      await fileRow(folder, "Family top").dblclick();
      await until(async () => (await panelRows(folder).allInnerTexts()).join("|") === "Loose page|Child one", { what: "the panel in the folder share" });
      await panelRows(folder).filter({ hasText: "Child one" }).click();
      await until(() => new URL(folder.url()).searchParams.get("page") === childOne, { what: "the page inside opens" });
      await until(async () => (await crumbs(folder).allInnerTexts()).join("|") === "Family top", { what: "its crumbs in the folder share" });
      assertNoProblems(folder);
    } finally { await fctx.close(); }
  });

  // A page's Recently deleted entry, from its 404 (blocks_store.trash_entry:
  // for a page that went with another, that one's), or null while it is live.
  const trashEntry = async (id) => {
    try { await user.api(`/api/blocks/${id}`); return null; } catch (e) {
      if (e.status !== 404) throw e;
      return e.data?.trashed || {};
    }
  };
  // Each page's parent as the listing has it ("" for a library page), or
  // "gone" for one the library does not list.
  const parentsOf = async (ids) => {
    const live = new Map((await listing()).map((p) => [p.id, p.parent]));
    return ids.map((id) => (live.has(id) ? live.get(id) : "gone")).join("|");
  };

  await step("sub-pages: a link to a page that went with its parent offers Restore, which brings the tree back and opens the linked page, not its parent", async () => {
    const parent = await user.api("/api/pages", { method: "POST", body: { title: "Cascade top" } });
    const inside = await user.api("/api/pages", { method: "POST", body: { title: "Cascade inside", parent_page: parent.id } });
    await user.api(`/api/pages/${inside.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "cascnote", parent: inside.id, position: "a0", content: "a note inside the cascade" }] } });
    const linker = await user.api("/api/pages", { method: "POST", body: { title: "Cascade linker" } });
    await user.api(`/api/pages/${linker.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "casclink", parent: linker.id, position: "a0", content: `see [[${inside.id}]] here` }] } });
    await user.api(`/api/blocks/${parent.id}`, { method: "DELETE" });
    assertEq((await trashEntry(inside.id))?.id, parent.id, "the page inside went with its parent");
    assertEq(await parentsOf([parent.id, inside.id]), "gone|gone", "both left the library");
    const ctx = await user.context(browser);
    const page = await openPage(ctx, at(linker.id));
    try {
      const chip = row(page, "see").locator(".blockRefChip.trashedRef");
      await chip.waitFor({ timeout: 15000 });
      await chip.click();
      const notice = page.locator(".missingPageNotice");
      await notice.waitFor();
      assert((await notice.textContent()).includes("is in Recently deleted."), `the notice says where it went: ${await notice.textContent()}`);
      await notice.getByRole("button", { name: "Restore", exact: true }).click();
      await until(() => blockOf(page) === inside.id, { what: "the linked page opens, not its parent" });
      await page.locator(".blockRow", { hasText: "a note inside the cascade" }).waitFor();
      await until(async () => (await crumbs(page).allInnerTexts()).join("|") === "Cascade top", { what: "inside its parent again" });
      assertEq(await notice.count(), 0, "the notice goes");
      assertEq(await trashEntry(inside.id), null, "the page is live");
      assertEq(await parentsOf([parent.id, inside.id]), `|${parent.id}`, "the parent is back in the library, the page inside it");
      assert(!(await user.api("/api/trash")).pages.some((p) => p.id === parent.id), "nothing left in Recently deleted");
      assertNoProblems(page, [new RegExp(`/blocks/${inside.id}(/subtree)? -> 404`)]);
    } finally { await ctx.close(); }
  });

  await step("sub-pages: Restore on the open page inside, its parent deleted elsewhere, brings the whole tree back", async () => {
    const parent = await user.api("/api/pages", { method: "POST", body: { title: "Gone top" } });
    const inside = await user.api("/api/pages", { method: "POST", body: { title: "Gone middle", parent_page: parent.id } });
    const below = await user.api("/api/pages", { method: "POST", body: { title: "Gone bottom", parent_page: inside.id } });
    const ctx = await user.context(browser);
    const page = await openPage(ctx, at(inside.id));
    try {
      await until(async () => (await crumbs(page).allInnerTexts()).join("|") === "Gone top", { what: "the crumbs of the page inside" });
      await until(async () => (await panelRows(page).allInnerTexts()).join("|") === "Gone bottom", { what: "its own page inside" });
      await user.api(`/api/blocks/${parent.id}`, { method: "DELETE" }); // from another tab
      const notice = page.locator(".pageTrashedNotice");
      await notice.waitFor({ timeout: 15000 });
      assert((await notice.textContent()).includes("This page was moved to Recently deleted."), "the open page says it went");
      assertEq(await parentsOf([parent.id, inside.id, below.id]), "gone|gone|gone", "the whole tree left the library");
      await notice.getByRole("button", { name: "Restore", exact: true }).click();
      await notice.waitFor({ state: "detached" });
      await until(async () => (await parentsOf([parent.id, inside.id, below.id])) === `|${parent.id}|${inside.id}`,
        { what: "the parent, the page and the one below it back, in place" });
      assert(!(await user.api("/api/trash")).pages.some((p) => [parent.id, inside.id, below.id].includes(p.id)), "nothing left in Recently deleted");
      assertEq(blockOf(page), inside.id, "the page stays open");
      await until(async () => (await crumbs(page).allInnerTexts()).join("|") === "Gone top", { what: "its crumbs again" });
      await until(async () => (await panelRows(page).allInnerTexts()).join("|") === "Gone bottom", { what: "its panel again" });
      assertNoProblems(page, [new RegExp(`/blocks/${inside.id}(/subtree)? -> 404`)]);
    } finally { await ctx.close(); }
  });

  await step("sub-pages: a label on a page inside counts in the label's tile and lists in its view, in the library and in its top's folder", async () => {
    const tag = await user.label("Inside label");
    await user.api(`/api/pages/${childOne}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "set", id: childOne, props: { labels: [tag] } }] } });
    const stored = await props(childOne);
    assertEq(JSON.stringify(stored.labels), JSON.stringify([tag]), "the page inside carries the label");
    assertEq(stored.parent_page, top.id, "and is still inside its parent");
    const ctx = await user.context(browser);
    const page = await openPage(ctx, home);
    const tile = (p) => p.locator(".fileList .labelRow", { hasText: "Inside label" });
    const labelsView = (p) => p.locator('[data-guide="home.labelsView"]').click();
    try {
      await page.locator(".fileList").waitFor({ timeout: 15000 });
      await labelsView(page);
      await tile(page).waitFor({ timeout: 15000 });
      await until(async () => (await tile(page).innerText()).includes("1 page"), { what: "the tile counts the page inside" });
      await tile(page).dblclick();
      await until(() => new URL(page.url()).searchParams.get("label") === tag, { what: "the label's view opens" });
      await fileRow(page, "Child one").waitFor({ timeout: 15000 });
      assertEq(await fileRow(page, "Family top").count(), 0, "the view lists the labelled page only");

      // the folder its top is filed in (the library's own view stays on labels)
      await page.goto(`${home}&folder=${shelf}`);
      await fileRow(page, "Family top").waitFor({ timeout: 15000 });
      await labelsView(page);
      await tile(page).waitFor({ timeout: 15000 });
      await until(async () => (await tile(page).innerText()).includes("1 page"), { what: "the folder's tile counts the page inside its page" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("sub-pages: the library's search box finds a page inside by its label or title, with its path, in the library and its top's folder only", async () => {
    const other = await user.folder("Search elsewhere");
    await user.api("/api/pages", { method: "POST", body: { title: "Elsewhere page", folders: [other] } });
    const ctx = await user.context(browser);
    const page = await openPage(ctx, home);
    const box = page.locator(".homeFindInput");
    const found = (p) => fileRow(p, "Child one");
    try {
      await page.locator(".fileList").waitFor({ timeout: 15000 });
      assertEq(await found(page).count(), 0, "with no query a page inside is not listed");
      await box.fill("Inside label");
      await found(page).waitFor({ timeout: 15000 });
      assertEq(await found(page).locator(".insideTagBadge").innerText(), "Family top", "its row names the page it is in");
      assert(!(await found(page).getAttribute("class")).includes("homeDim"), "it is a match, not a dimmed row");
      await box.fill("Child one");
      await found(page).waitFor({ timeout: 15000 });
      await box.fill("");
      await until(async () => (await found(page).count()) === 0, { what: "cleared, the page inside leaves the listing" });
      await box.fill("Inside label");
      await found(page).dblclick();
      await until(() => blockOf(page) === childOne, { what: "the found page opens" });

      // the folder its top is filed in finds it; another folder does not
      await page.goto(`${home}&folder=${shelf}`);
      await fileRow(page, "Family top").waitFor({ timeout: 15000 });
      await page.locator(".homeFindInput").fill("Inside label");
      await found(page).waitFor({ timeout: 15000 });
      await page.goto(`${home}&folder=${other}`);
      await fileRow(page, "Elsewhere page").waitFor({ timeout: 15000 });
      await page.locator(".homeFindInput").fill("Inside label");
      await page.locator(".homeSearchBanner").waitFor({ timeout: 15000 });
      assertEq(await found(page).count(), 0, "a folder its top is not in does not find it");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // Hearth top, filed in Quartz drawer, holds Ledger inside, which is
  // pinned, labelled Zircon tag, has a note and carries a folder of its own
  // (Basalt stray, as an import or a merge can leave): the library says the
  // top holds a page, the pinned card shows the page inside by its path and
  // its first note, and Quick open, the [[ picker and the @ menu find it by
  // its top's folder and its label's name, never by the folder it carries.
  await step("sub-pages: a page with pages inside says so on its row and card; a pinned page inside shows its path and preview; the pickers match its top's folder and its label by name", async () => {
    const quartz = await user.folder("Quartz drawer");
    const stray = await user.folder("Basalt stray");
    const zircon = await user.label("Zircon tag");
    const hearth = await user.api("/api/pages", { method: "POST", body: { title: "Hearth top", folders: [quartz] } });
    const ledger = await user.api("/api/pages", { method: "POST", body: { title: "Ledger inside", parent_page: hearth.id } });
    // a later set keeps the folder: only setting parent_page drops them
    await user.api(`/api/pages/${ledger.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "ledgernote", parent: ledger.id, position: "a0", content: "ledger first note" },
      { op: "set", id: ledger.id, props: { labels: [zircon], folders: [stray], pinned: new Date().toISOString() } }] } });
    assertEq(JSON.stringify((await props(ledger.id)).folders), JSON.stringify([stray]), "the page inside carries a folder of its own");
    const src = await user.api("/api/pages", { method: "POST", body: { title: "Picker source page" } });
    await user.api(`/api/pages/${src.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "ledgersrc", parent: src.id, position: "a0", content: "pick from here" }] } });
    const ctx = await user.context(browser);
    const page = await openPage(ctx, home);
    try {
      const holds = fileRow(page, "Hearth top").locator(".pagesTagBadge");
      await holds.waitFor({ timeout: 15000 });
      assertEq(await holds.innerText(), "1", "the row counts the page inside");
      assertEq(await holds.getAttribute("title"), "1 page inside");
      const pinned = page.locator(".pinnedStrip .pageCard", { hasText: "Ledger inside" });
      await pinned.waitFor({ timeout: 15000 });
      assertEq(await pinned.locator(".insideTagBadge").innerText(), "Hearth top", "the pinned card names the page it is in");
      assertEq(await pinned.locator(".folderTagBadge").count(), 0, "and shows no folder chip");
      assert((await pinned.locator(".pageCardPreview").innerText()).includes("ledger first note"), "its cover previews its first note");
      await page.getByRole("button", { name: "Grid view", exact: true }).click();
      const card = page.locator(".fileGrid .pageCard", { has: page.locator(".pageCardTitle", { hasText: /^Hearth top$/ }) });
      await card.waitFor({ timeout: 15000 });
      assertEq(await card.locator(".pagesTagBadge").innerText(), "1", "the card counts it too");
      await page.getByRole("button", { name: "List view", exact: true }).click();

      // Quick open: the top's folder and the title; the folder it carries
      // neither shows on its row nor finds it
      await page.keyboard.press("ControlOrMeta+p");
      const dialog = page.getByRole("dialog", { name: "Open a page" });
      await dialog.waitFor();
      const query = dialog.getByRole("textbox", { name: "Search pages, files, folders and labels" });
      await query.fill("quartz ledger");
      const option = dialog.locator('[role="option"][data-kind="page"]').filter({ hasText: "Ledger inside" });
      await option.waitFor();
      assertEq(await dialog.locator('[role="option"][data-kind="page"]').count(), 1, "only the page inside answers");
      await option.locator(".labelTagBadge", { hasText: "Zircon tag" }).waitFor();
      assertEq(await option.locator(".folderTagBadge").count(), 0, "its row shows its label but not the folder it carries");
      await query.fill("basalt stray");
      await dialog.locator('[role="option"][data-kind="folder"]', { hasText: "Basalt stray" }).waitFor();
      assertEq(await option.count(), 0, "the folder it carries does not find it");
      await page.keyboard.press("Escape");

      // the [[ picker and the @ menu in a note of another page
      await page.goto(at(src.id));
      await editRow(page, "pick from here");
      // the page's row, not a block row under its path
      const pageRow = (popup) => popup.locator(".refPopupItem").filter({ has: page.locator(".refPopupText", { hasText: /^Ledger inside$/ }) });
      const picker = page.locator('.refPopup[data-guide="editor.refSearch"]');
      // a query typed, its row checked, the query taken out again
      const typed = async (text, check) => {
        await page.keyboard.type(text);
        await check();
        await page.keyboard.press("Escape");
        for (let i = 0; i < text.length; i++) await page.keyboard.press("Backspace");
      };
      await typed(" [[quartz ledger", async () => {
        await pageRow(picker).waitFor();
        assertEq(await pageRow(picker).locator(".refPopupMeta").innerText(), "Page · Hearth top", "the [[ row names where it is");
      });
      await typed(" [[zircon", () => pageRow(picker).waitFor());
      await page.keyboard.type(" @zircon");
      const menu = page.locator(".mentionMenu");
      await pageRow(menu).waitFor();
      assert((await pageRow(menu).innerText()).includes("Hearth top"), "the @ row names where it is");
      await page.keyboard.press("Escape");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // A selection holding a library page and a page inside it (the filter
  // box lists both): the moves keep to the topmost page, Move to folder
  // files the library page and says it left the other out.
  await step("sub-pages: a page selected with a page inside it moves as one; Move to folder files the library page and says what it left out", async () => {
    const selBin = await user.folder("Sel bin");
    const outer = await user.api("/api/pages", { method: "POST", body: { title: "Sel outer" } });
    const inner = await user.api("/api/pages", { method: "POST", body: { title: "Sel inner", parent_page: outer.id } });
    const target = await user.api("/api/pages", { method: "POST", body: { title: "Sel target" } });
    const ctx = await user.context(browser);
    const page = await openPage(ctx, home);
    const status = () => page.locator(".statusPill .pillText").allTextContents();
    // both pages selected through the filter box, the menu opened on the page inside
    const pickBoth = async () => {
      await page.locator(".homeFindInput").fill("Sel");
      await fileRow(page, "Sel inner").waitFor({ timeout: 15000 });
      await fileRow(page, "Sel outer").click();
      await fileRow(page, "Sel inner").click({ modifiers: ["ControlOrMeta"] });
      await fileRow(page, "Sel inner").click({ button: "right" });
    };
    try {
      await page.locator(".fileList").waitFor({ timeout: 15000 });
      await pickBoth();
      const flyoutRow = page.locator(".ctxMenu .ctxSubTrigger", { hasText: "Move to folder" });
      await flyoutRow.click();
      const flyout = page.getByRole("menu", { name: "Move to folder", exact: true });
      await flyout.waitFor();
      await flyout.locator(".ctxMenuItem", { hasText: "1 page inside another page is left out: it is filed with its top." }).waitFor();
      await flyout.locator(".ctxMenuItem", { hasText: "Sel bin" }).click();
      await until(async () => JSON.stringify((await props(outer.id)).folders) === JSON.stringify([selBin]), { what: "the library page filed" });
      assertEq((await props(inner.id)).folders, undefined, "the page inside gets no folders of its own");
      await until(async () => (await status()).some((text) => text.includes("Added 1 page to “Sel bin”. Left out 1 page inside another page, filed with its top.")),
        { what: "the status says what it left out" });

      // Move into page…: the page inside goes along inside its page
      await pickBoth();
      await page.locator(".ctxMenuItem", { hasText: "Move into page…" }).click();
      const dialog = page.getByRole("dialog", { name: "Move into page" });
      await dialog.waitFor();
      assert((await dialog.innerText()).includes("The page goes inside the one you pick"), `one page moves: ${await dialog.innerText()}`);
      await dialog.getByRole("textbox").fill("Sel target");
      await dialog.getByRole("option").filter({ has: page.locator("strong", { hasText: /^Sel target$/ }) }).click();
      await until(async () => (await props(outer.id)).parent_page === target.id, { what: "the page inside the target" });
      assertEq((await props(inner.id)).parent_page, outer.id, "the page inside it stays inside it");
      assertEq(await parentsOf([outer.id, inner.id]), `${target.id}|${outer.id}`, "the listing reads the tree");

      // Move to library: the topmost page comes out, the one inside it stays
      await pickBoth();
      await page.locator(".ctxMenuItem", { hasText: "Move to library" }).click();
      await until(async () => !(await props(outer.id)).parent_page, { what: "the page back in the library" });
      assertEq((await props(inner.id)).parent_page, outer.id, "the page inside it still inside it");
      await until(async () => (await status()).some((text) => text.includes("Moved 1 page to the library.")), { what: "one page moved" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // Two pages inside two pages, both called "Twin": the move pickers name
  // each by its path and match on it, as Ctrl+P does.
  await step("sub-pages: Move block to page and Move into page… name a page inside by its path and match on it", async () => {
    const alpha = await user.api("/api/pages", { method: "POST", body: { title: "Twin home Alpha" } });
    const beta = await user.api("/api/pages", { method: "POST", body: { title: "Twin home Beta" } });
    await user.api("/api/pages", { method: "POST", body: { title: "Twin", parent_page: alpha.id } });
    const twinB = await user.api("/api/pages", { method: "POST", body: { title: "Twin", parent_page: beta.id } });
    const src = await user.api("/api/pages", { method: "POST", body: { title: "Twin source" } });
    await user.api(`/api/pages/${src.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "twinmove", parent: src.id, position: "a0", content: "a block for a twin" }] } });
    const ctx = await user.context(browser);
    const page = await openPage(ctx, at(src.id));
    try {
      const wrap = page.locator(".sortableBlockWrap", { hasText: "a block for a twin" }).first();
      await wrap.waitFor({ timeout: 15000 });
      await wrap.hover();
      await wrap.locator(".dragHandle").click();
      await page.locator(".ctxMenuItem", { hasText: "Move to page…" }).click();
      const dialog = page.getByRole("dialog", { name: "Move block to page" });
      await dialog.waitFor();
      await dialog.getByRole("textbox").fill("Twin");
      const twins = async () => (await optionTexts(dialog)).filter((text) => /^Twin Twin home/.test(text)).sort();
      await until(async () => (await twins()).length === 2, { what: "both twins listed" });
      assertEq((await twins()).join("|"), "Twin Twin home Alpha|Twin Twin home Beta", "each twin names the page it is in");
      await dialog.getByRole("textbox").fill("twin beta");
      await until(async () => (await optionTexts(dialog)).join("|") === "Twin home Beta|Twin Twin home Beta",
        { what: "the parent by its title, then the twin inside it by its path" });
      await dialog.getByRole("option").nth(1).click();
      await until(async () => (await user.api(`/api/blocks/${twinB.id}/subtree`)).block.children.some((b) => b.content === "a block for a twin"),
        { what: "the block moved into the twin picked" });

      // Move into page… matches the path the same way
      await page.locator(".tabStrip .tab", { hasText: "Twin source" }).first().click({ button: "right" });
      await page.locator(".ctxMenuItem", { hasText: "Move into page…" }).click();
      const into = page.getByRole("dialog", { name: "Move into page" });
      await into.waitFor();
      await into.getByRole("textbox").fill("twin alpha");
      await until(async () => (await optionTexts(into)).join("|") === "Twin home Alpha|Twin Twin home Alpha",
        { what: "the twin inside Alpha by its path" });
      await page.keyboard.press("Escape");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("sub-pages: the search panel's folder chip keeps a hit inside a page inside a page filed there", async () => {
    const note = await user.api("/api/blocks", { method: "POST", body: { parent_id: childOne, content: "quokkascope inside note" } });
    const elsewhere = await user.api("/api/pages", { method: "POST", body: { title: "Unfiled quokka page" } });
    await user.api("/api/blocks", { method: "POST", body: { parent_id: elsewhere.id, content: "quokkascope loose note" } });
    const scoped = await user.api(`/api/block-search?q=quokkascope&limit=20&scope=${shelf}`);
    assertEq((scoped.blocks || []).map((b) => b.id).join("|"), note.id, "the server's scope keeps the note inside the page inside");
    assertEq((await props(childOne)).parent_page, top.id, "a page inside the filed page");
    const ctx = await user.context(browser);
    const page = await openPage(ctx, home);
    const hits = page.locator(".searchPopover .searchResult", { hasText: "quokkascope" });
    try {
      await page.locator(".fileList").waitFor();
      await page.click("button[aria-label='Search']");
      const search = page.locator(".searchPopover .searchInput");
      await search.fill("quokkascope");
      await until(async () => (await hits.count()) === 2, { what: "both notes, unscoped" });
      await search.fill("shelf");
      await page.locator(".searchLabelSuggest .categorySuggestionItem", { hasText: "Shelf" }).waitFor();
      await search.press("Tab");
      await page.locator(".searchPopover .searchChip", { hasText: "Shelf" }).waitFor();
      await search.fill("quokkascope");
      await page.locator(".searchPopover .searchResult", { hasText: "quokkascope inside note" }).waitFor();
      await until(async () => (await hits.count()) === 1, { what: "the folder chip keeps the note inside, and only it" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("sub-pages: a page inside's Properties name the page it is inside, which opens from there", async () => {
    assertEq((await props(childOne)).parent_page, top.id, "stored inside the top page");
    const ctx = await user.context(browser);
    const page = await openPage(ctx, at(childOne));
    const pop = page.locator(live(".propsPopover"));
    const inside = pop.locator('.propsRow[data-key="parent_page"]');
    try {
      await until(async () => (await crumbs(page).allInnerTexts()).join("|") === "Family top", { what: "the crumbs" });
      await page.locator(".tabStrip .tab", { hasText: "Child one" }).first().click({ button: "right" });
      await page.getByRole("menuitem", { name: "Page properties", exact: true }).click();
      await pop.waitFor();
      await inside.waitFor();
      assertEq(await inside.locator(".metaKey").innerText(), "Inside page", "the row's label");
      assertEq(await pop.locator('.propsSection[data-section="filing"] .propsRow[data-key="parent_page"]').count(), 1, "under Filing");
      await until(async () => (await inside.locator(".metaVal").innerText()).includes("Family top"), { what: "the row names the parent" });
      await inside.getByRole("button", { name: "Open page: Inside page", exact: true }).click();
      await until(() => blockOf(page) === top.id, { what: "the row opens the parent" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  const choice = (dialog, name) => dialog.getByRole("button", { name, exact: true });
  const toggle = (dialog, name) => dialog.getByRole("checkbox", { name, exact: true });

  // Notion's "Markdown & CSV" export (gamma/markdown_zip_import.py): a page
  // `Title <id>.md` beside `Title <id>/`, which holds the pages inside it,
  // and a database `Name <id>.csv` beside `Name <id>/`, its rows.
  await step("sub-pages: a Notion export's pages inside pages are reviewed under their page and come in as sub-pages, with no folder for their directories", async () => {
    const dest = await user.folder("Notion dest");
    const zip = path.join(server.dir, "Notion export.zip");
    execFileSync(PYTHON, ["-c", `
import struct, sys, zipfile, zlib
def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
png = b'\\x89PNG\\r\\n\\x1a\\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) \\
    + chunk(b'IDAT', zlib.compress(b'\\x00\\xff\\x00\\x00')) + chunk(b'IEND', b'')
home, middle, bottom = 'Notion home ' + '1' * 32, 'Notion middle ' + '2' * 32, 'Notion bottom ' + '3' * 32
tasks, one, two = 'Notion tasks ' + '4' * 32, 'Row one ' + '5' * 32, 'Row two ' + '6' * 32
with zipfile.ZipFile(sys.argv[1], 'w') as z:
    z.writestr(home + '.md', '# Notion home\\n\\nThe top of the tree.\\n\\n![diagram](' + home.replace(' ', '%20') + '/diagram.png)\\n')
    z.writestr(home + '/diagram.png', png)
    z.writestr(home + '/' + middle + '.md', '# Notion middle\\n\\nInside the home page.\\n')
    z.writestr(home + '/' + middle + '/' + bottom + '.md', '# Notion bottom\\n\\nInside the middle page.\\n')
    z.writestr(tasks + '.csv', 'Name,Status\\nRow one,Done\\nRow two,Todo\\n')
    z.writestr(tasks + '/' + one + '.md', '# Row one\\n\\nStatus: Done\\n')
    z.writestr(tasks + '/' + two + '.md', '# Row two\\n\\nStatus: Todo\\n')
`, zip]);
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${home}&folder=${dest}`);
    try {
      await page.locator(".fileList").waitFor({ timeout: 15000 });
      const dialog = await openDialog(page, "Import");
      await choice(dialog, "Notion pages").click();
      const chooser = page.waitForEvent("filechooser");
      await choice(dialog, "Choose .zip…").click();
      await (await chooser).setFiles(zip);
      const review = page.getByRole("dialog", { name: "Review Notion import", exact: true });
      const target = review.getByRole("region", { name: "Library after import", exact: true });
      await target.getByText("Notion bottom", { exact: true }).waitFor({ timeout: 15000 });
      assert((await review.innerText()).includes("Library / Notion dest"), "the import goes to the open folder");
      // each page row of the library column and the row or folder it is under
      const under = await target.locator(".importTreeFile").evaluateAll((rows) => rows.map((el) => {
        const name = (row) => row.querySelector(".importFileText > :first-child").textContent;
        const up = el.closest("li").parentElement.closest("li");
        const parent = up?.querySelector(":scope > .importTreeFile");
        return `${name(el)} < ${parent ? name(parent) : up?.querySelector(":scope > details > summary > span:last-child")?.textContent || ""}`;
      }).sort());
      assertEq(under.join("|"), ["Notion bottom < Notion middle", "Notion home < Notion dest", "Notion middle < Notion home",
        "Notion tasks < Notion dest", "Row one < Notion tasks", "Row two < Notion tasks"].join("|"),
      "the pages inside are listed under their page, the top pages in the destination");
      await choice(review, "Import to library").click();
      const report = page.getByRole("dialog", { name: "Import complete", exact: true });
      await report.waitFor({ timeout: 15000 });
      assert((await report.innerText()).includes("6 new pages"), `the report counts every page: ${await report.innerText()}`);
      await choice(report, "Done").click();

      const pages = await listing();
      const byTitle = (title) => pages.find((p) => p.content === title);
      assertEq(pages.filter((p) => p.properties?.folders?.includes(dest)).map((p) => p.content).sort().join("|"),
        "Notion home|Notion tasks", "the destination files the top pages only");
      assertEq(["Notion home", "Notion middle", "Notion bottom", "Notion tasks", "Row one", "Row two"]
        .map((title) => byTitle(title)?.parent ?? "missing").join("|"),
      ["", byTitle("Notion home").id, byTitle("Notion middle").id, "", byTitle("Notion tasks").id, byTitle("Notion tasks").id].join("|"),
      "each page inside names the page it is in");
      const folderNames = [];
      const walk = (node) => node.children.forEach((child) => { folderNames.push(child.content); walk(child); });
      walk((await user.api("/api/blocks/folders/subtree")).block);
      assertEq(folderNames.filter((name) => /Notion|Row/.test(name)).join("|"), "Notion dest", "no folder for the pages' directories");

      // the folder lists the top pages; the panels and crumbs lead through the tree
      await fileRow(page, "Notion home").waitFor({ timeout: 15000 });
      await fileRow(page, "Notion tasks").waitFor();
      assertEq(await page.locator(".fileList .fileRow").count(), 2, "the folder's listing holds the two top pages");
      await fileRow(page, "Notion home").dblclick();
      await until(() => blockOf(page) === byTitle("Notion home").id, { what: "the top page opens" });
      await until(async () => (await panelRows(page).allInnerTexts()).join("|") === "Notion middle", { what: "its panel lists the page inside" });
      const picture = page.locator(".blockList img[src*='/api/uploads/']");
      await picture.waitFor();
      await until(() => picture.evaluate((img) => img.complete && img.naturalWidth > 0), { what: "the picture in the page's directory loaded" });
      await panelRows(page).first().click();
      await until(() => blockOf(page) === byTitle("Notion middle").id, { what: "the page inside opens" });
      await until(async () => (await crumbs(page).allInnerTexts()).join("|") === "Notion home", { what: "its crumbs" });
      await until(async () => (await panelRows(page).allInnerTexts()).join("|") === "Notion bottom", { what: "the page inside it" });
      await panelRows(page).first().click();
      await until(() => blockOf(page) === byTitle("Notion bottom").id, { what: "the page at the bottom opens" });
      await until(async () => (await crumbs(page).allInnerTexts()).join("|") === "Notion home|Notion middle", { what: "the crumbs name both above" });
      await crumbs(page).first().click();
      await until(() => blockOf(page) === byTitle("Notion home").id, { what: "the top crumb opens the top page" });
      await page.goto(at(byTitle("Notion tasks").id));
      await until(async () => (await panelRows(page).allInnerTexts()).sort().join("|") === "Row one|Row two", { what: "the database's rows inside its page" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // Left out of the selection, a parent names nothing: a page inside it
  // that is still ticked is filed by its directory, as a library page,
  // and the review draws it there (the entry's folders_alone).
  await step("sub-pages: a page inside ticked without its parent is reviewed, and filed, in its directory's folder", async () => {
    const dest = await user.folder("Alone dest");
    const zip = path.join(server.dir, "Notion alone.zip");
    execFileSync(PYTHON, ["-c", `
import sys, zipfile
home, inside = 'Alone home ' + '7' * 32, 'Alone inside ' + '8' * 32
with zipfile.ZipFile(sys.argv[1], 'w') as z:
    z.writestr(home + '.md', '# Alone home\\n\\nThe page left out.\\n')
    z.writestr(home + '/' + inside + '.md', '# Alone inside\\n\\nTicked on its own.\\n')
`, zip]);
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${home}&folder=${dest}`);
    try {
      await page.locator(".fileList").waitFor({ timeout: 15000 });
      const dialog = await openDialog(page, "Import");
      await choice(dialog, "Markdown notes").click();
      const chooser = page.waitForEvent("filechooser");
      await choice(dialog, "Choose file…").click();
      await (await chooser).setFiles(zip);
      const review = page.getByRole("dialog", { name: "Review Markdown import", exact: true });
      const target = review.getByRole("region", { name: "Library after import", exact: true });
      await target.getByText("Alone inside", { exact: true }).waitFor({ timeout: 15000 });
      // what each row of the library column is under: a page row, or a folder
      const under = () => target.locator(".importTreeFile").evaluateAll((rows) => rows.map((el) => {
        const name = (row) => row.querySelector(".importFileText > :first-child").textContent;
        const up = el.closest("li").parentElement.closest("li");
        const parent = up?.querySelector(":scope > .importTreeFile");
        return `${name(el)} < ${parent ? `page ${name(parent)}` : `folder ${up?.querySelector(":scope > details > summary > span:last-child")?.textContent || ""}`}`;
      }).sort().join("|"));
      assertEq(await under(), "Alone home < folder Alone dest|Alone inside < page Alone home", "ticked together, the page inside is under its page");
      await target.getByRole("checkbox", { name: "Import Alone home", exact: true }).click();
      assert(!(await target.getByRole("checkbox", { name: "Import Alone inside", exact: true }).isChecked()),
        "unticking the page unticks the page inside it");
      assertEq(await under(), "Alone home < folder Alone dest|Alone inside < page Alone home", "unticked, it stays under its page");
      await target.getByRole("checkbox", { name: "Import Alone inside", exact: true }).click();
      await until(async () => (await under()) === "Alone home < folder Alone dest|Alone inside < folder Alone home",
        { what: "ticked alone, the page inside moves to its directory's folder" });
      await choice(review, "Import to library").click();
      const report = page.getByRole("dialog", { name: "Import complete", exact: true });
      await report.waitFor({ timeout: 15000 });
      assert((await report.innerText()).includes("1 new page"), `the report counts the one page: ${await report.innerText()}`);
      await choice(report, "Done").click();

      const pages = await listing();
      const inside = pages.find((p) => p.content === "Alone inside");
      assert(inside && !pages.some((p) => p.content === "Alone home"), "only the page ticked came in");
      assertEq(inside.parent, "", "without its parent it is a library page");
      const folders = (await user.api("/api/blocks/folders/subtree")).block;
      const destNode = folders.children.find((f) => f.id === dest);
      const homeFolder = destNode?.children?.find((f) => f.content === "Alone home");
      assert(homeFolder, "a folder for its directory, inside the destination");
      assertEq(JSON.stringify(inside.properties.folders), JSON.stringify([homeFolder.id]), "filed where the review drew it");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("sub-pages: a page's export offers Include pages inside, off by default; on, the zip nests the pages inside; a page with none has no switch", async () => {
    const tree = await user.api("/api/pages", { method: "POST", body: { title: "Export top" } });
    const inner = await user.api("/api/pages", { method: "POST", body: { title: "Export inner", parent_page: tree.id } });
    await user.api("/api/pages", { method: "POST", body: { title: "Export leaf", parent_page: inner.id } });
    const alone = await user.api("/api/pages", { method: "POST", body: { title: "Export alone" } });
    const ctx = await user.context(browser);
    const page = await openPage(ctx, at(tree.id));
    // Export from the review step: the job's body, the dialog's download and the job's file
    const exportIt = async (dialog) => {
      const started = page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/jobs/export");
      const download = page.waitForEvent("download");
      await choice(dialog, "Export").click();
      const response = await started;
      const job = await response.json();
      const file = await download;
      await dialog.getByRole("heading", { name: "Export ready", exact: true }).waitFor();
      return { body: response.request().postDataJSON(), name: file.suggestedFilename(), job };
    };
    const markdownReview = async () => {
      const dialog = await openDialog(page, "Export");
      await choice(dialog, "Markdown").dblclick();
      await dialog.getByRole("heading", { name: "Markdown", exact: true }).waitFor();
      return dialog;
    };
    try {
      await until(async () => (await panelRows(page).allInnerTexts()).join("|") === "Export inner", { what: "the page inside" });
      let dialog = await openDialog(page, "Export");
      await choice(dialog, "Gamma").click();
      assertEq(await choice(dialog, "Next").count(), 1, "a format with no switches of its own gets the review step for the switch");
      await choice(dialog, "Close Export").click();
      await dialog.waitFor({ state: "detached" });

      dialog = await markdownReview();
      const include = toggle(dialog, "Include pages inside");
      assertEq(await include.isChecked(), false, "off by default");
      assert((await dialog.innerText()).includes("Adds the 2 pages inside it, at any depth"), "its hint counts the pages it adds");
      const off = await exportIt(dialog);
      assertEq(off.body.mode, "readable");
      assertEq(off.body.sub_pages, false, "the job is asked for the page alone");
      assert(off.name.endsWith(".md"), `the page alone is its own file: ${off.name}`);
      const stem = off.name.slice(0, -".md".length);
      await dialog.getByText("1 page exported.", { exact: true }).waitFor();
      await choice(dialog, "Done").click();
      await dialog.waitFor({ state: "detached" });

      dialog = await markdownReview();
      assertEq(await toggle(dialog, "Include pages inside").isChecked(), false, "still off: the choice remembered");
      await toggle(dialog, "Include pages inside").check();
      assert((await dialog.innerText()).includes("The 2 pages inside it are exported with it."), "the summary says they come along");
      const on = await exportIt(dialog);
      assertEq(on.body.sub_pages, true, "the job is asked for the pages inside");
      assertEq(on.name, `${stem}.zip`, "several files are a zip named for the page");
      await dialog.getByText("3 pages exported.", { exact: true }).waitFor();
      const saved = path.join(server.dir, "sub-pages-export.zip");
      fs.writeFileSync(saved, Buffer.from(await (await user.api(`/api/jobs/${on.job.id}/download`, { raw: true })).arrayBuffer()));
      const files = JSON.parse(execFileSync(PYTHON, ["-I", "-c",
        "import json, sys, zipfile; z = zipfile.ZipFile(sys.argv[1]); print(json.dumps({n: z.read(n).decode() for n in z.namelist()}))", saved]).toString());
      const names = Object.keys(files).sort();
      assertEq(names.length, 3, `the page and the two inside it: ${names.join(", ")}`);
      const [topFile, innerFile, leafFile] = names;
      assertEq(topFile, `${stem}.md`, "the page's file at the top");
      const innerStem = innerFile.slice(`${stem}/`.length, -".md".length);
      assert(innerFile.startsWith(`${stem}/`) && !innerStem.includes("/"), `the page inside in the page's directory: ${innerFile}`);
      assert(leafFile.startsWith(`${stem}/${innerStem}/`), `the one below in its parent's: ${leafFile}`);
      assert(files[topFile].includes("Export top") && !files[topFile].includes("parent:"), "the top names no parent");
      assert(files[innerFile].includes("Export inner") && files[innerFile].includes(`parent: ../${encodeURIComponent(stem)}.md`),
        `the page inside names its parent: ${files[innerFile]}`);
      assert(files[leafFile].includes("Export leaf") && files[leafFile].includes(`parent: ../${encodeURIComponent(innerStem)}.md`),
        `the one below names its own: ${files[leafFile]}`);
      await choice(dialog, "Done").click();
      await dialog.waitFor({ state: "detached" });

      // a page with no pages inside: no switch, and Gamma straight from the first step
      await page.goto(at(alone.id));
      await page.locator(".blockList").first().waitFor();
      dialog = await markdownReview();
      assertEq(await toggle(dialog, "Include pages inside").count(), 0, "no switch without pages inside");
      await choice(dialog, "1. Choose a format").click();
      await choice(dialog, "Gamma").click();
      assertEq(await choice(dialog, "Next").count(), 0, "Gamma exports straight from the first step");
      await choice(dialog, "Close Export").click();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // One HTML5 drag, the events a browser sends: dragstart on `source`,
  // dragover then drop on the middle of `target`; `over` runs between the
  // two, while the drag is over the target.
  const drag = async (page, source, target, over) => {
    const dt = await page.evaluateHandle(() => new DataTransfer());
    await source.dispatchEvent("dragstart", { dataTransfer: dt });
    const box = await target.boundingBox();
    const at = { dataTransfer: dt, clientX: box.x + box.width / 2, clientY: box.y + box.height / 2 };
    await target.dispatchEvent("dragover", at);
    if (over) await over();
    await target.dispatchEvent("drop", at);
  };
  const dragOver = (target) => until(() => target.evaluate((el) => el.classList.contains("dragOver")), { what: "the target shows the drop" });
  const homeButton = (page) => page.getByRole("button", { name: "Home", exact: true });
  const statusOf = (page) => page.locator(".statusPill .pillText").allTextContents();
  const said = (page, text) => until(async () => (await statusOf(page)).some((line) => line.includes(text)), { what: `the status says "${text}"` });

  // A page made inside the open page, or moved into it or out of it,
  // elsewhere writes nothing to its room: the panel follows when the window
  // comes back, with the listing (at most every 15 s, so the page's clock
  // is put past that first).
  await step("sub-pages: the panel follows a page another client makes inside the open page, moves in or takes out, when the window comes back", async () => {
    const nest = await user.api("/api/pages", { method: "POST", body: { title: "Wake nest" } });
    const first = await user.api("/api/pages", { method: "POST", body: { title: "Wake first", parent_page: nest.id } });
    const mover = await user.api("/api/pages", { method: "POST", body: { title: "Wake mover" } });
    const ctx = await user.context(browser);
    const page = await openPage(ctx, at(nest.id));
    const wake = () => page.evaluate(() => {
      if (window.__e2eSkew === undefined) {
        const real = Date.now.bind(Date);
        window.__e2eSkew = 0;
        Date.now = () => real() + window.__e2eSkew;
      }
      window.__e2eSkew += 20000;
      window.dispatchEvent(new Event("focus"));
    });
    try {
      await until(async () => (await panelRows(page).allInnerTexts()).join("|") === "Wake first", { what: "the panel lists the page inside" });
      // another client makes one inside and moves one in
      await user.api("/api/pages", { method: "POST", body: { title: "Wake made", parent_page: nest.id } });
      await user.api(`/api/blocks/${mover.id}`, { method: "PUT", body: { properties: { parent_page: nest.id } } });
      await page.waitForTimeout(400);
      assertEq(await panelRows(page).allInnerTexts().then((texts) => texts.join("|")), "Wake first", "nothing reaches the open page's room");
      await wake();
      await until(async () => (await panelRows(page).allInnerTexts()).join("|") === "Wake first|Wake mover|Wake made",
        { what: "the panel lists them, in the library's order" });
      assert((await panel(page).locator(".backlinksHead").innerText()).includes("3 pages inside"), "and counts them");
      // and takes one out
      await user.api(`/api/blocks/${first.id}`, { method: "PUT", body: { properties: { parent_page: null } } });
      await wake();
      await until(async () => (await panelRows(page).allInnerTexts()).join("|") === "Wake mover|Wake made", { what: "the page taken out leaves the panel" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // A Pages inside row has the library's page menu, less Rename, and drags
  // as a library row does: onto another row or a crumb it goes inside that
  // page, onto the topbar's Home to the library, filed where its top is.
  await step("sub-pages: a Pages inside row has the page menu; dragged onto another row, a crumb or Home it moves inside that page or to the library", async () => {
    const dshelf = await user.folder("Drag shelf");
    const dtop = await user.api("/api/pages", { method: "POST", body: { title: "Drag top", folders: [dshelf] } });
    const alpha = await user.api("/api/pages", { method: "POST", body: { title: "Drag alpha", parent_page: dtop.id } });
    const beta = await user.api("/api/pages", { method: "POST", body: { title: "Drag beta", parent_page: dtop.id } });
    const ctx = await user.context(browser);
    const page = await openPage(ctx, at(dtop.id));
    const rowOf = (title) => panelRows(page).filter({ has: page.locator(".backlinkPageTitle", { hasText: new RegExp(`^${title}$`) }) });
    const menuItem = (name) => page.locator(".ctxMenu .ctxMenuItem", { hasText: name });
    const rows = () => panelRows(page).allInnerTexts().then((texts) => texts.join("|"));
    try {
      await until(async () => (await rows()) === "Drag alpha|Drag beta", { what: "the panel lists both" });
      // the row's menu: a right-click, or its "⋯"
      await rowOf("Drag beta").click({ button: "right" });
      for (const name of ["Open", "New page inside", "Pin", "Add label", "Move into page…", "Move to library", "Duplicate", "Copy link", "Share…", "Export…", "Properties", "Delete"]) {
        assertEq(await menuItem(name).count() > 0, true, `the menu has ${name}`);
      }
      assertEq(await menuItem("Rename").count(), 0, "no Rename, which is the listing's");
      assertEq(await page.locator(".ctxMenu .ctxSubTrigger", { hasText: "Move to folder" }).count(), 0, "no Move to folder for a page inside another");
      await page.keyboard.press("Escape");
      await page.locator(".ctxMenu").waitFor({ state: "detached" });
      await rowOf("Drag alpha").hover();
      await rowOf("Drag alpha").getByRole("button", { name: "Actions", exact: true }).click();
      await menuItem("Duplicate").click();
      await until(async () => (await rows()) === "Drag alpha|Drag beta|Drag alpha (copy)", { what: "the copy is beside it, in the panel" });
      await rowOf("Drag alpha \\(copy\\)").click({ button: "right" });
      await menuItem("Delete").click();
      await page.locator(".confirmModal").getByRole("button", { name: "Delete", exact: true }).click();
      await until(async () => (await rows()) === "Drag alpha|Drag beta", { what: "the deleted copy leaves the panel" });

      // onto another row: inside it
      await drag(page, rowOf("Drag beta"), rowOf("Drag alpha"), () => dragOver(rowOf("Drag alpha")));
      await until(async () => (await props(beta.id)).parent_page === alpha.id, { what: "inside the row it was dropped on" });
      await until(async () => (await rows()) === "Drag alpha", { what: "it leaves the panel" });
      await said(page, "Moved 1 page into “Drag alpha”.");
      // onto itself: nothing
      await drag(page, rowOf("Drag alpha"), rowOf("Drag alpha"));
      assertEq((await props(alpha.id)).parent_page, dtop.id, "a drag let go where it began moves nothing");

      // onto a crumb: inside that page, a level up
      await rowOf("Drag alpha").click();
      await until(() => blockOf(page) === alpha.id, { what: "the row opens its page" });
      await until(async () => (await rows()) === "Drag beta", { what: "its panel" });
      const topCrumb = crumbs(page).filter({ hasText: "Drag top" });
      await drag(page, rowOf("Drag beta"), topCrumb, () => dragOver(topCrumb));
      await until(async () => (await props(beta.id)).parent_page === dtop.id, { what: "inside the crumb's page" });
      await panel(page).waitFor({ state: "detached" });

      // onto Home: to the library, filed in its top's folder
      await topCrumb.click();
      await until(async () => (await rows()) === "Drag alpha|Drag beta", { what: "both inside the top again" });
      await drag(page, rowOf("Drag beta"), homeButton(page), () => dragOver(homeButton(page)));
      await until(async () => !(await props(beta.id)).parent_page, { what: "out of the page" });
      assertEq(JSON.stringify((await props(beta.id)).folders), JSON.stringify([dshelf]), "filed in its former top's folder");
      await until(async () => (await rows()) === "Drag alpha", { what: "it leaves the panel" });
      await said(page, "Moved 1 page to the library.");
      assert(blockOf(page) === dtop.id, "the page stays open");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // A library row or card takes a dropped page inside, never a page into
  // itself or a page inside it; a page inside another dropped on Home goes
  // to the library, and a folder drag is no page drag.
  await step("sub-pages: a page dropped on a library row or card goes inside it, never into a page inside it; one inside another dropped on Home goes to the library", async () => {
    const dropTop = await user.api("/api/pages", { method: "POST", body: { title: "Drop top" } });
    const dropInner = await user.api("/api/pages", { method: "POST", body: { title: "Drop inner", parent_page: dropTop.id } });
    const dropLoose = await user.api("/api/pages", { method: "POST", body: { title: "Drop loose" } });
    const dropCard = await user.api("/api/pages", { method: "POST", body: { title: "Drop card" } });
    const dropBin = await user.folder("Drop bin");
    const ctx = await user.context(browser);
    const page = await openPage(ctx, home);
    try {
      // a list row
      await fileRow(page, "Drop loose").waitFor({ timeout: 15000 });
      await drag(page, fileRow(page, "Drop loose"), fileRow(page, "Drop top"), () => dragOver(fileRow(page, "Drop top")));
      await until(async () => (await props(dropLoose.id)).parent_page === dropTop.id, { what: "inside the row's page" });
      await fileRow(page, "Drop loose").waitFor({ state: "detached" });
      await said(page, "Moved 1 page into “Drop top”.");
      assertEq((await props(dropLoose.id)).folders, undefined, "with no folders of its own");

      // a grid card
      await page.getByRole("button", { name: "Grid view", exact: true }).click();
      const card = (title) => page.locator(".fileGrid .pageCard", { has: page.locator(".pageCardTitle", { hasText: new RegExp(`^${title}$`) }) });
      await card("Drop card").waitFor({ timeout: 15000 });
      await drag(page, card("Drop card"), card("Drop top"), () => dragOver(card("Drop top")));
      await until(async () => (await props(dropCard.id)).parent_page === dropTop.id, { what: "inside the card's page" });
      await card("Drop card").waitFor({ state: "detached" });
      await page.getByRole("button", { name: "List view", exact: true }).click();

      // a folder dragged onto a page row is no page drag: nothing moves
      await drag(page, page.locator(".fileList .folderRow", { hasText: "Drop bin" }), fileRow(page, "Drop top"));
      assertEq((await user.api("/api/blocks/folders/subtree")).block.children.find((f) => f.id === dropBin)?.id, dropBin, "the folder stays at the top level");

      // the filter box lists the pages inside: one dropped on Home goes to
      // the library; a page dropped on a page inside it is refused
      await page.locator(".homeFindInput").fill("Drop");
      await fileRow(page, "Drop inner").waitFor({ timeout: 15000 });
      await drag(page, fileRow(page, "Drop top"), fileRow(page, "Drop inner"));
      await said(page, "A page can’t go inside a page that is inside it.");
      assertEq((await props(dropTop.id)).parent_page, undefined, "the page stays in the library");
      assertEq((await props(dropInner.id)).parent_page, dropTop.id, "and the page inside it inside it");
      await drag(page, fileRow(page, "Drop card"), homeButton(page), () => dragOver(homeButton(page)));
      await until(async () => !(await props(dropCard.id)).parent_page, { what: "the page inside back in the library" });
      await said(page, "Moved 1 page to the library.");
      await drag(page, fileRow(page, "Drop top"), homeButton(page));
      await said(page, "Already in the library.");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });
}
