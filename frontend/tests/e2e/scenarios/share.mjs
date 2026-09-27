// Share links: creating one from the popover (an audience tile, or the first
// invitation as Invited only; Stop sharing confirmed inline), the anonymous
// share view (title, PDF, highlight overlay, an image served through the
// share token, no editing), an edit share letting another account type into
// the page, and a folder share (the folder view's link button; the listing a
// visitor browses).
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
      assertEq(await page.locator(".chatComposer, .chatMsgActionBtn[title^='Edit']").count(), 0, "no chat editing controls");
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
    assertEq(await page.locator(".chatComposer").count(), 0, "page editors also get read-only chat");
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
    assert((await page.textContent(".sharePopover")).includes("Share folder “sharedlab”"), "the popover is about the folder");
    await page.locator(".sharePopover").getByText("Choosing who can open this folder creates its link.").waitFor();
    await page.locator(".sharePopover").getByRole("button", { name: "Anyone", exact: true }).click();
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
    assert((await v.textContent(".shareCrumbs")).includes("sharedlab"), "the folder path leads the title");
    await v.locator(".shareCrumbs .crumbBtn", { hasText: "sharedlab" }).click();
    await v.locator(".fileRow", { hasText: "Folder share paper B" }).waitFor();
    await v.goBack();
    await v.locator(".blockRow", { hasText: "a note inside the shared folder" }).waitFor({ timeout: 15000 });
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

  await step("share view: the topbar says who shared it and what a visitor may do; Sign in keeps the page", async () => {
    const shown = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Visitor bar page" } });
    await alice.api("/api/blocks", { method: "POST", body: { parent_id: shown.id, content: "a note for visitors" } });
    const { token: barToken } = await alice.api(`/api/share/${shown.id}`, { method: "POST" });
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const page = await openPage(ctx, `${server.base}/?share=${barToken}`);
    await page.locator(".blockRow", { hasText: "a note for visitors" }).waitFor({ timeout: 15000 });
    const pill = page.locator(".shareAccess");
    const text = await pill.textContent();
    assert(text.includes("View only") && text.includes(`shared by`) && text.includes(alice.name), `the role and who shared it (${text})`);
    assertEq(await pill.getAttribute("title"), `You can read this page. Only ${alice.name} can change it.`, "the sentence behind it");
    assertEq(await page.locator("a.shareBrand").getAttribute("href"), "/", "the mark leads to the server's front door");
    // Sign in opens the share's own sign-in, and Back returns to the page
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page.getByText("Sign in to open this shared page", { exact: true }).waitFor();
    await page.getByRole("button", { name: "Back to the shared page", exact: true }).click();
    await page.locator(".blockRow", { hasText: "a note for visitors" }).waitFor();
    // signing in keeps the page and offers to copy it
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page.fill(".loginInput >> nth=0", bob.name);
    await page.fill("input[type=password]", bob.password);
    await page.click("button.loginBtn[type=submit]");
    await page.getByRole("button", { name: "Add to my library", exact: true }).waitFor({ timeout: 15000 });
    assertEq(await page.getByRole("button", { name: "Sign in", exact: true }).count(), 0, "no Sign in once signed in");
    assertNoProblems(page);
    await ctx.close();
    await alice.api(`/api/share-settings/${shown.id}`, { method: "DELETE" });
  });

  await step("share gate: a dead link, a signed-in-only link opened anonymously and an invite-only link each show their page", async () => {
    const gated = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Gated share page" } });
    await alice.api("/api/blocks", { method: "POST", body: { parent_id: gated.id, content: "a note behind the gate" } });
    const { token: gateToken } = await alice.api(`/api/share/${gated.id}`, { method: "POST" });
    const refusals = [/GET \/api\/share\/[^ ]+ -> (401|403|404)/];

    // an unknown token: the explanation and the two ways on; signing in lands in the library
    let ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    let page = await openPage(ctx, `${server.base}/?share=no-such-token`);
    await page.getByText("This link doesn't work", { exact: true }).waitFor({ timeout: 15000 });
    assert((await page.textContent(".loginCard")).includes("Ask the person who sent it for a new link."), "the dead link's explanation");
    await page.getByRole("button", { name: "Go to Gamma", exact: true }).waitFor();
    await page.getByRole("button", { name: "Sign in to your library", exact: true }).click();
    await page.fill(".loginInput >> nth=0", bob.name);
    await page.fill("input[type=password]", bob.password);
    await page.click("button.loginBtn[type=submit]");
    await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
    assert(!page.url().includes("share="), "the library, not the dead link again");
    assertNoProblems(page, refusals);
    await ctx.close();

    // signed-in users only, opened anonymously: the login gate, then the page
    await alice.api(`/api/share-settings/${gated.id}`, { method: "PUT", body: { audience: "users", role: "view" } });
    ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    page = await openPage(ctx, `${server.base}/?share=${gateToken}`);
    await page.getByText("Sign in to open this shared page", { exact: true }).waitFor({ timeout: 15000 });
    await page.fill(".loginInput >> nth=0", bob.name);
    await page.fill("input[type=password]", bob.password);
    await page.click("button.loginBtn[type=submit]");
    await until(async () => (await page.textContent(".readOnlyTitle").catch(() => "")).includes("Gated share page"), { what: "the page after signing in" });
    assertNoProblems(page, refusals);
    await ctx.close();

    // specific people only, opened by bob who isn't one of them
    await alice.api(`/api/share-settings/${gated.id}`, { method: "PUT", body: { audience: "list", role: "view", users: [] } });
    ctx = await bob.context(browser);
    page = await openPage(ctx, `${server.base}/?share=${gateToken}`);
    await page.getByText("Not shared with you", { exact: true }).waitFor({ timeout: 15000 });
    assert((await page.textContent(".loginCard")).includes(`${bob.name} isn't one of them`), "names the refused account");
    await page.getByRole("button", { name: "Sign in as someone else", exact: true }).waitFor();
    assertNoProblems(page, refusals);
    await ctx.close();
    await alice.api(`/api/share-settings/${gated.id}`, { method: "DELETE" });
  });

  await step("share: opening the popover shares nothing; the first invitation makes an invite-only link; Stop sharing asks first", async () => {
    const pg = await alice.api("/api/pages", { method: "POST", body: { title: "Invite first" } });
    const ctx = await alice.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${pg.id}&ws=${alice.ws}`);
    await page.click("button[aria-label='Share']");
    const pop = page.locator(".sharePopover");
    await pop.getByText("Choosing who can open this page creates its link.").waitFor();
    await until(async () => (await pop.textContent()).includes("Share “Invite first”"), { what: "the page's name in the title" });
    assertEq((await alice.api(`/api/share-settings/${pg.id}`)).token, null, "opening the popover shares nothing");
    assertEq(await pop.locator(".setPictureChoice.on").count(), 0, "no audience is picked before there is a link");
    await pop.getByRole("textbox", { name: "Invite people by name…" }).fill("bob");
    await pop.getByRole("option", { name: "bob", exact: true }).click();
    await pop.getByRole("button", { name: "Invite", exact: true }).click();
    await pop.locator(".aiProvRow", { hasText: "bob" }).waitFor();
    const settings = await alice.api(`/api/share-settings/${pg.id}`);
    assertEq(settings.audience, "list", "the first invitation shares Invited only");
    assertEq(JSON.stringify(settings.users), JSON.stringify([{ name: "bob", role: "view" }]), "bob is invited to view");
    await pop.getByText("Only the people above can open this page; the link does nothing for anyone else.").waitFor();
    assert(await pop.locator(".setSection", { hasText: "General access" }).getByRole("button", { name: "Edit", exact: true }).isDisabled(),
      "the View / Edit toggle stays, disabled, under Invited only");
    await pop.getByRole("button", { name: "Copy link" }).waitFor();
    await pop.locator(".shareStop").getByRole("button", { name: "Stop sharing", exact: true }).click();
    await pop.getByText("Stop sharing? The link stops working and the 1 invited person loses access.").waitFor();
    assert((await alice.api(`/api/share-settings/${pg.id}`)).token, "nothing stops before the confirm");
    await pop.locator(".mirrorConfirm").getByRole("button", { name: "Stop sharing", exact: true }).click();
    await pop.getByText("Choosing who can open this page creates its link.").waitFor();
    assertEq((await alice.api(`/api/share-settings/${pg.id}`)).token, null, "the share stopped");
    assertNoProblems(page);
    await ctx.close();
  });

  const account = alice2;
  let token;
  if (!pdfPageId) { console.log("  skip  share: needs the pdf steps (drop --only)"); return; }

  // An image block on the paper's page, so the share view has an upload to fetch.
  const up = await account.upload("/api/upload-image", PNG_1PX, "dot.png", "image/png");
  await account.api("/api/blocks", { method: "POST", body: { parent_id: pdfPageId, content: `figure ![](${up.url})` } });

  await step("share: picking Anyone creates the link and shows it on the copy button", async () => {
    const ctx = await account.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${pdfPageId}&ws=${account.ws}`);
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Share']");
    await page.waitForSelector(".sharePopover");
    await page.locator(".sharePopover").getByRole("button", { name: "Anyone", exact: true }).click();
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
