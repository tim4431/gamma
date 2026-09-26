// Share links: creating one from the dialog, the anonymous share view (title,
// PDF, highlight overlay, an image served through the share token, no
// editing), an edit share letting another account type into the page, and a
// folder share (the folder view's link button; the listing a visitor browses).
import { tree, same, editRow, closeEditor, PNG_1PX } from "./notes.mjs";
import { waitForPdf } from "./pdf.mjs";

export async function shareScenarios({ server, browser, alice, bob, step, until, sleep, assert, assertEq, assertNoProblems, openPage }, { alice2, pdfPageId }) {
  await step("share chat: saved conversation is read-only on desktop and phone", async () => {
    const shared = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Shared chat notes" } });
    const saved = { messages: [{ role: "user", text: "Explain this shared page" }, { role: "assistant", text: "A **saved answer** for visitors." }] };
    await alice.api(`/api/chats/${shared.id}`, { method: "PUT", body: saved });
    const { token: chatToken } = await alice.api(`/api/share/${shared.id}`, { method: "POST" });
    for (const phone of [false, true]) {
      const ctx = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1280, height: 860 } });
      const page = await openPage(ctx, `${server.base}/?share=${chatToken}`);
      const writes = [];
      page.on("request", (r) => { if (/\/api\/(chats|chat-history|ai)\b/.test(r.url()) && r.method() !== "GET") writes.push(r.url()); });
      await page.waitForSelector(".readOnlyTitle");
      if (phone) await page.getByRole("button", { name: "AI chat", exact: true }).click();
      await page.locator(".chatMessages strong", { hasText: "saved answer" }).waitFor();
      assertEq(await page.locator(".chatInputRow, .chatMsgActionBtn[title^='Edit']").count(), 0, "no chat editing controls");
      assertEq(await page.getByRole("button", { name: "New chat", exact: true }).count(), 0, "no new chat action");
      await page.getByRole("button", { name: "Find in this conversation" }).click();
      await page.getByPlaceholder("Find in chat…").fill("saved answer");
      await until(async () => (await page.textContent(".chatFindCount")) === "1/1", { what: "searching saved chat" });
      await page.getByRole("button", { name: "Close Chat", exact: true }).click();
      if (phone) {
        await page.getByRole("button", { name: "AI chat", exact: true }).click();
      } else {
        await page.getByRole("button", { name: "View", exact: true }).click();
        await page.locator(".popoverItem", { hasText: "AI Chat" }).click();
      }
      await page.locator(".chatMessages strong", { hasText: "saved answer" }).waitFor();
      await sleep(650); // wait beyond the autosave debounce
      assertEq(writes.length, 0, "shared chat never writes");
      assertNoProblems(page);
      await ctx.close();
    }
    await alice.api(`/api/share-settings/${shared.id}`, { method: "PUT", body: { audience: "users", role: "edit" } });
    const ctx = await bob.context(browser);
    const page = await openPage(ctx, `${server.base}/?share=${chatToken}`);
    await page.locator(".chatMessages strong", { hasText: "saved answer" }).waitFor();
    assertEq(await page.locator(".chatInputRow").count(), 0, "page editors also get read-only chat");
    assertNoProblems(page);
    await ctx.close();
    assertEq(JSON.stringify((await alice.api(`/api/chats/${shared.id}`)).messages), JSON.stringify(saved.messages), "owner's conversation is unchanged");
  });

  await step("folder share: the folder view's link button shares every page filed there; visitors browse the listing", async () => {
    const paperA = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Folder share paper A" } });
    await alice.api(`/api/blocks/${paperA.id}`, { method: "PUT", body: { properties: { folder: "sharedlab" } } });
    const paperB = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Folder share paper B" } });
    await alice.api(`/api/blocks/${paperB.id}`, { method: "PUT", body: { properties: { folder: "sharedlab" } } });
    const deeper = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Folder share paper in a subfolder" } });
    await alice.api(`/api/blocks/${deeper.id}`, { method: "PUT", body: { properties: { folder: "sharedlab/sub" } } });
    await alice.api("/api/blocks", { method: "POST", body: { parent_id: paperA.id, content: "a note inside the shared folder" } });
    const outside = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Not in the shared folder" } });

    // the owner: open the folder, share it from the browse bar's link button
    const ctx = await alice.context(browser);
    const page = await openPage(ctx, `${server.base}/?folder=sharedlab&ws=${alice.ws}`);
    await page.click("button[aria-label='Share this folder']");
    await page.waitForSelector(".sharePopover");
    assert((await page.textContent(".sharePopover")).includes("Share this folder"), "the popover is about the folder");
    await page.locator(".sharePopover button", { hasText: "Create link" }).click();
    const copyBtn = page.locator(".sharePopover button", { hasText: /Copy link|Copied/ }).first();
    await copyBtn.waitFor({ timeout: 10000 });
    const folderToken = new URL(await copyBtn.getAttribute("title")).searchParams.get("share");
    assert(folderToken, "folder share token");
    await until(async () => (await page.textContent(".sharePopover")).includes("every page in this folder"), { what: "folder wording" });
    await page.keyboard.press("Escape");
    await page.locator(".sharePopover").waitFor({ state: "detached" });
    assertNoProblems(page);
    await ctx.close();
    assertEq((await alice.api("/api/share-settings/folder?name=sharedlab")).token, folderToken, "the folder's share");

    // an anonymous visitor: the folder view itself — the library's own rows,
    // confined to the folder and stripped of everything that would change it
    const vctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const v = await openPage(vctx, `${server.base}/?share=${folderToken}`);
    await v.locator(".fileRow", { hasText: "Folder share paper A" }).waitFor({ timeout: 15000 });
    assertEq(await v.locator(".fileList .fileRow").count(), 2, "the folder's own pages");
    assertEq(await v.locator(".fileList .folderRow").count(), 1, "its subfolder");
    assertEq(await v.locator(".folderNewBtn").count(), 0, "no New page / New folder for a visitor");
    assertEq(await v.locator(".fileRowPin").count(), 0, "no pins for a visitor");
    assertEq(await v.locator(".folderBackRow").count(), 0, "nothing above the shared folder");
    assert((await v.textContent(".folderCurrent")).includes("sharedlab"), "the folder crumb");
    assert((await v.textContent(".readOnlyTitle")).includes("sharedlab"), "the folder name in the topbar");
    // into the subfolder and back up — never above the root
    await v.locator(".fileList .folderRow", { hasText: "sub" }).dblclick();
    await v.locator(".fileRow", { hasText: "in a subfolder" }).waitFor();
    await v.locator(".folderBackRow").click();
    await v.locator(".fileRow", { hasText: "Folder share paper B" }).waitFor();
    assertEq(await v.locator(".folderBackRow").count(), 0, "back at the root, no way further up");
    // a page opens in the same share view; the home button returns; history replays both
    await v.locator(".fileRow", { hasText: "Folder share paper A" }).dblclick();
    await v.locator(".blockRow", { hasText: "a note inside the shared folder" }).waitFor({ timeout: 15000 });
    assert(v.url().includes(`page=${paperA.id}`), "the open page rides in the URL");
    await v.click("button[aria-label='Back to the shared folder']");
    await v.locator(".fileRow", { hasText: "Folder share paper B" }).waitFor();
    await v.goBack();
    await v.locator(".blockRow", { hasText: "a note inside the shared folder" }).waitFor({ timeout: 15000 });
    assertNoProblems(v);
    await vctx.close();

    // a deep link into the folder opens the page; the token never reaches other pages
    const dctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const d = await openPage(dctx, `${server.base}/?share=${folderToken}&page=${paperB.id}`);
    await until(async () => (await d.textContent(".readOnlyTitle")).includes("Folder share paper B"), { what: "deep-linked page" });
    assertNoProblems(d);
    await dctx.close();
    const refused = await fetch(`${server.base}/api/blocks/${outside.id}?share=${folderToken}`);
    assertEq(refused.status, 403, "a page outside the folder is refused");
    await alice.api("/api/share-settings/folder?name=sharedlab", { method: "DELETE" });
  });

  const account = alice2;
  let token;
  if (!pdfPageId) { console.log("  skip  share: needs the pdf steps (drop --only)"); return; }

  // An image block on the paper's page, so the share view has an upload to fetch.
  const up = await account.upload("/api/upload-image", PNG_1PX, "dot.png", "image/png");
  await account.api("/api/blocks", { method: "POST", body: { parent_id: pdfPageId, content: `figure ![](${up.url})` } });

  await step("share: the popover creates a link and shows it on the copy button", async () => {
    const ctx = await account.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${pdfPageId}&ws=${account.ws}`);
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Share']");
    await page.waitForSelector(".sharePopover");
    await page.locator(".sharePopover button", { hasText: "Create link" }).click();
    const copyBtn = page.locator(".sharePopover button", { hasText: /Copy link|Copied/ }).first();
    await copyBtn.waitFor({ timeout: 10000 });
    token = new URL(await copyBtn.getAttribute("title")).searchParams.get("share");
    await page.keyboard.press("Escape");
    await page.locator(".sharePopover").waitFor({ state: "detached" });
    if (!token) token = (await account.api(`/api/share-settings/${pdfPageId}`)).token;
    assert(token, "share token");
    assertNoProblems(page);
    await ctx.close();
    return `?share=${token.slice(0, 8)}…`;
  });

  await step("share: an anonymous visitor sees the paper, the highlight and the image, read-only", async () => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const page = await openPage(ctx, `${server.base}/?share=${token}`);
    await page.waitForSelector(".readOnlyTitle", { timeout: 15000 });
    assert((await page.textContent(".readOnlyTitle")).includes("Rydberg paper"), "title in the share view");
    await waitForPdf(page, 1);
    await page.waitForSelector('[data-page="1"] [data-hl-id]', { timeout: 10000 });
    await until(async () => {
      const imgs = await page.$$eval("img.mdImg", (els) => els.map((e) => [e.getAttribute("src"), e.naturalWidth]));
      return imgs.length === 1 && imgs[0][0].includes("share=") && imgs[0][1] === 1;
    }, { what: "image served through the share token" });
    await page.locator(".blockRow", { hasText: "figure" }).locator(".blockBody").click();
    await sleep(400); // a negative check: nothing to wait for, so give an editor time to (not) appear
    assert((await page.$(".blockEditorCm")) == null, "no editor opens on a view-only share");
    assertNoProblems(page);
    await ctx.close();
  });

  await step("share: an edit share lets bob type into alice's page", async () => {
    await account.api(`/api/share-settings/${pdfPageId}`, { method: "PUT", body: { audience: "users", role: "edit" } });
    const ctx = await bob.context(browser);
    const page = await openPage(ctx, `${server.base}/?share=${token}`);
    await page.waitForSelector(".readOnlyTitle", { timeout: 15000 });
    await until(async () => (await page.textContent("body")).includes("Can edit"), { what: "edit badge" });
    await editRow(page, "figure");
    await page.keyboard.type(" edited by bob");
    await closeEditor(page);
    await until(async () => JSON.stringify(await tree(account, pdfPageId)).includes("edited by bob"), { what: "bob's edit saved to alice's page" });
    assertNoProblems(page);
    await ctx.close();
  });

  await step("share: an anyone-with-the-link edit share lets a stranger type under a display name", async () => {
    await account.api(`/api/share-settings/${pdfPageId}`, { method: "PUT", body: { audience: "anyone", role: "edit" } });
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } }); // no session at all
    const page = await openPage(ctx, `${server.base}/?share=${token}`);
    await page.waitForSelector(".readOnlyTitle", { timeout: 15000 });
    await until(async () => (await page.textContent("body")).includes("Can edit"), { what: "edit badge" });
    // a generated name, changeable from the tag
    assert(/^as \S+ \S+$/.test((await page.locator(".linkNameTag").textContent()).trim()), "a generated two-word name");
    await page.locator(".linkNameTag").click();
    await page.locator(".linkNameInput").fill("Otter");
    await page.keyboard.press("Enter");
    await until(async () => (await page.locator(".linkNameTag").textContent()).trim() === "as Otter", { what: "renamed" });
    await editRow(page, "figure");
    await page.keyboard.type(" edited by a stranger");
    await closeEditor(page);
    await until(async () => JSON.stringify(await tree(account, pdfPageId)).includes("edited by a stranger"), { what: "stranger's edit saved" });
    const { batches } = await account.api(`/api/pages/${pdfPageId}/ops`);
    assertEq(batches[batches.length - 1].actor, "link:Otter", "the edit is attributed to the display name");
    assertNoProblems(page);
    await ctx.close();
  });
}
