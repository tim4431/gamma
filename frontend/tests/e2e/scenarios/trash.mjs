import { Account, wanted } from "../harness.mjs";

// Recently deleted (docs/dev/home_library.md "Recently deleted"): deleting a
// page from the library moves it to the 30-day trash, the toolbar's trash
// button lists it with Restore and Delete permanently, and a link to a page
// in the trash says where it went and offers Restore.
export async function trashScenarios(env) {
  const { server, browser, step, openPage, assert, assertEq, assertNoProblems, until } = env;
  if (!wanted("trash")) return;
  await server.manage("create-user", "trash-user", "trash-pw");
  const user = await new Account(server, "trash-user", "trash-pw").login();
  const pageApi = (title, folders = []) => user.api("/api/pages", { method: "POST", body: { title, folders } });
  const binTests = await user.folder("bin/tests");
  const doomed = await pageApi("Doomed page", [binTests]);
  await user.api("/api/blocks", { method: "POST", body: { parent_id: doomed.id, content: "a note that survives" } });
  const forever = await pageApi("Gone for good");
  const library = async () => (await user.api("/api/blocks/root/children")).children.map((b) => b.id);
  const trashed = async () => (await user.api("/api/trash")).pages.map((p) => p.id);

  await step("trash: a page deleted from the library lands in Recently deleted, and Restore brings it back", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=${user.ws}`);
    try {
      const row = page.locator(".fileRow", { hasText: "Doomed page" });
      await row.click({ button: "right" });
      await page.locator(".ctxMenuItem", { hasText: "Delete" }).click();
      const confirm = page.locator(".confirmModal", { hasText: "Move this page to Recently deleted?" });
      await confirm.waitFor();
      await confirm.getByRole("button", { name: "Delete", exact: true }).click();
      await row.waitFor({ state: "detached" });
      assert((await trashed()).includes(doomed.id), "the page is in the trash");

      await page.getByRole("button", { name: "Recently deleted", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Recently deleted" });
      const entry = dialog.locator(`.aiProvRow[data-page="${doomed.id}"]`);
      await entry.waitFor();
      assert(/Deleted .* by trash-user/.test(await entry.textContent()), "who deleted it, and when");
      assert((await entry.textContent()).includes("bin / tests"), "the folder it comes back to");
      await entry.getByRole("button", { name: "Restore", exact: true }).click();
      await entry.waitFor({ state: "detached" });
      await dialog.getByRole("button", { name: "Close", exact: true }).click();
      await row.waitFor();
      assert((await library()).includes(doomed.id), "back in the library");
      const { block } = await user.api(`/api/blocks/${doomed.id}/subtree`);
      assertEq(JSON.stringify(block.properties.folders), JSON.stringify([binTests]), "filed where it was");
      assertEq(JSON.stringify(block.children.map((c) => c.content)), JSON.stringify(["a note that survives"]), "its notes came back");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // The page header's Delete closes the tab first, back to the folder the
  // page was opened from, in place: no reload, and the `trashed` its socket
  // gets never puts up the Recently deleted notice in this window.
  await step("trash: the page header's Delete returns to the folder it opened from, with no notice and no reload", async () => {
    const binOpen = await user.folder("bin/open");
    const open = await pageApi("Deleted while open", [binOpen]);
    await pageApi("Stays in the folder", [binOpen]);
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?folder=${binOpen}&ws=${user.ws}`);
    try {
      const row = page.locator(".fileRow", { hasText: "Deleted while open" });
      await row.dblclick();
      await page.locator(".tabStrip .tab.active", { hasText: "Deleted while open" }).waitFor();
      await page.evaluate(() => {
        window.e2eSameDocument = true;
        window.e2eSawNotice = false;
        new MutationObserver(() => { if (document.querySelector(".pageTrashedNotice")) window.e2eSawNotice = true; })
          .observe(document.body, { childList: true, subtree: true });
      });
      await page.locator(".pageDeleteBtn").click();
      const confirm = page.locator(".confirmModal", { hasText: "Move \"Deleted while open\" to Recently deleted?" });
      await confirm.getByRole("button", { name: "Delete", exact: true }).click();
      await until(async () => (await trashed()).includes(open.id), { what: "the page is in the trash" });
      await page.locator(".fileRow", { hasText: "Stays in the folder" }).waitFor();
      await page.waitForTimeout(500); // room for a late `trashed` or reload to show
      assertEq(new URL(page.url()).searchParams.get("folder"), binOpen, "back in the folder it was opened from");
      assertEq(await row.count(), 0, "gone from the folder");
      assertEq(await page.locator(".tabStrip .tab", { hasText: "Deleted while open" }).count(), 0, "its tab closed");
      assert(await page.evaluate(() => window.e2eSameDocument === true), "no reload");
      assert(!(await page.evaluate(() => window.e2eSawNotice)), "the Recently deleted notice never came up");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await user.api(`/api/trash/${open.id}`, { method: "DELETE" }).catch(() => {}); // the next step empties the trash
    }
  });

  await step("trash: a link to a page in the trash offers Restore; Delete permanently asks first", async () => {
    await user.api(`/api/blocks/${doomed.id}`, { method: "DELETE" });
    await user.api(`/api/blocks/${forever.id}`, { method: "DELETE" });
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${doomed.id}&ws=${user.ws}`);
    try {
      const notice = page.locator(".missingPageNotice");
      await notice.waitFor({ timeout: 15000 });
      assert((await notice.textContent()).includes("“Doomed page” is in Recently deleted."), "the notice names where it went");
      await notice.getByRole("button", { name: "Restore", exact: true }).click();
      await until(() => new URL(page.url()).searchParams.get("block") === doomed.id, { what: "the restored page opens" });
      await page.locator(".blockRow", { hasText: "a note that survives" }).waitFor();
      assertEq(await notice.count(), 0, "the notice goes");

      // Ctrl+Shift+P → Recently deleted, then the page deleted for good
      await page.keyboard.press("Control+Shift+p");
      const palette = page.getByRole("dialog", { name: "Command palette" });
      await palette.getByRole("textbox").fill(">recently deleted");
      await page.keyboard.press("Enter");
      const dialog = page.getByRole("dialog", { name: "Recently deleted" });
      const entry = dialog.locator(`.aiProvRow[data-page="${forever.id}"]`);
      await entry.waitFor();
      await entry.getByRole("button", { name: "Delete “Gone for good” permanently" }).click();
      const confirm = page.locator(".confirmModal", { hasText: "Delete “Gone for good” for good?" });
      await confirm.getByRole("button", { name: "Delete permanently", exact: true }).click();
      await entry.waitFor({ state: "detached" });
      assert(!(await trashed()).includes(forever.id), "gone from the trash");
      assert(!(await library()).includes(forever.id), "and not back in the library");
      await dialog.getByText("Nothing was deleted recently.").waitFor();
      assertNoProblems(page, [new RegExp(`/blocks/${doomed.id}/subtree -> 404`)]);
    } finally { await ctx.close(); }
  });

  // A [[ref]] or ![[embed]] naming a trashed page's block: the ref lookup
  // leaves it out, and the block's 404 names the trash entry (App's
  // onFetchRefs), so the chip and the card say where the page went.
  await step("trash: a [[ref]] and an ![[embed]] of a page in the trash say so; the chip offers Restore", async () => {
    const away = await pageApi("Linked away");
    await user.api(`/api/pages/${away.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "awaynote", parent: away.id, position: "a0", content: "the embedded note" }] } });
    const linker = await pageApi("Linker");
    await user.api(`/api/pages/${linker.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "linkref", parent: linker.id, position: "a0", content: `see [[${away.id}]] here` },
      { op: "insert", id: "linkembed", parent: linker.id, position: "a1", content: "![[awaynote]]" },
      { op: "insert", id: "linkghost", parent: linker.id, position: "a2", content: "and [[nosuchblock]] too" }] } });
    await user.api(`/api/blocks/${away.id}`, { method: "DELETE" });
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${linker.id}&ws=${user.ws}`);
    try {
      const chip = page.locator(".blockRow", { hasText: "see" }).locator(".blockRefChip.trashedRef");
      await chip.waitFor({ timeout: 15000 });
      assertEq(await chip.innerText(), "Linked away", "the chip names the page, not its id");
      assertEq(await chip.getAttribute("title"), "“Linked away” is in Recently deleted.");
      const card = page.locator(".blockEmbedCard.gone");
      await card.waitFor();
      assertEq(await card.innerText(), "“Linked away” is in Recently deleted.", "the card says where its source went");
      assertEq(await page.locator(".blockRow", { hasText: "too" }).locator(".unlinkedRef").innerText(), "nosuchblock",
        "an id nothing holds is an unlinked chip");
      await chip.click();
      const notice = page.locator(".missingPageNotice");
      await notice.waitFor();
      await notice.getByRole("button", { name: "Restore", exact: true }).click();
      await page.locator(".blockRow", { hasText: "the embedded note" }).waitFor();
      assert((await library()).includes(away.id), "restored from the link");
      assertNoProblems(page, [
        new RegExp(`/blocks/(${away.id}|awaynote|nosuchblock) -> 404`),
        new RegExp(`/blocks/${away.id}/subtree -> 404`),
      ]);
    } finally { await ctx.close(); }
  });
}
