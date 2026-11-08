// Share links: creating one from the popover (an audience tile, or the first
// invitation as Invited only; Stop sharing confirmed inline), the anonymous
// share view (title, PDF, highlight overlay, an image served through the
// share token, no editing), an edit share letting another account type into
// the page, and a folder share (the folder view's link button; the listing a
// visitor browses; the folder copied into another library by the listing's
// Add to my library and by its link pasted into another Gamma's +, which
// is a second server), and what a visitor's export holds. A page inside a
// shared page names the link that opens it, a move into a shared page asks
// first, and an edit link's visitor makes a page inside the shared page.
import { execFileSync } from "node:child_process";
import path from "node:path";
import { Account, ROOT, Server } from "../harness.mjs";
import { tree, same, editRow, closeEditor, PNG_1PX } from "./notes.mjs";
import { waitForPdf } from "./pdf.mjs";
import { openDialog } from "./transfers.mjs";

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
        await page.locator(".menuPopover .ctxMenuItem", { hasText: "AI Chat" }).click();
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
    await alice.file(paperA.id, { folders: ["sharedlab"] });
    const paperB = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Folder share paper B" } });
    await alice.file(paperB.id, { folders: ["sharedlab"] });
    const deeper = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Folder share paper in a subfolder" } });
    await alice.file(deeper.id, { folders: ["sharedlab/sub"] });
    const sharedlab = await alice.folder("sharedlab");
    await alice.api("/api/blocks", { method: "POST", body: { parent_id: paperA.id, content: "a note inside the shared folder" } });
    const outside = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Not in the shared folder" } });

    // the owner: open the folder, share it from the browse bar's link button
    const ctx = await alice.context(browser);
    const page = await openPage(ctx, `${server.base}/?folder=${sharedlab}&ws=${alice.ws}`);
    await page.click("button[aria-label='Share this folder']");
    await page.waitForSelector(".sharePopover");
    assert((await page.textContent(".sharePopover")).includes("Share folder “sharedlab”"), "the popover is about the folder");
    await page.locator(".sharePopover").getByText("Not shared yet. Share makes a link to every page in this folder.").waitFor();
    await page.locator(".sharePopover").getByRole("button", { name: "Share", exact: true }).click();
    await page.locator(".sharePopover").getByRole("button", { name: "Anyone", exact: true }).click();
    const copyBtn = page.locator(".sharePopover button", { hasText: /Copy link|Copied/ }).first();
    await copyBtn.waitFor({ timeout: 10000 });
    const folderToken = new URL(await copyBtn.getAttribute("title")).searchParams.get("share");
    assert(folderToken, "folder share token");
    await until(async () => (await page.textContent(".sharePopover")).includes("Anyone with the link can read every page in this folder"), { what: "folder wording" });
    await page.keyboard.press("Escape");
    await page.locator(".sharePopover").waitFor({ state: "detached" });
    assertNoProblems(page);
    await ctx.close();
    assertEq((await alice.api(`/api/share-settings/folder/${sharedlab}`)).token, folderToken, "the folder's share");
    // the share names the folder by id: renamed, the link opens it under its new name
    await alice.api("/api/pages/folders/ops", { method: "POST", body: { ops: [{ op: "set", id: sharedlab, content: "sharedlab, renamed" }] } });
    assertEq((await (await fetch(`${server.base}/api/share/${folderToken}`)).json()).folder_name, "sharedlab, renamed", "the link names the folder");

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
    assert((await v.textContent(".folderCurrent")).includes("sharedlab, renamed"), "the folder crumb");
    assert((await v.textContent(".readOnlyTitle")).includes("sharedlab, renamed"), "the folder name in the topbar");
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

    // the owner on their own link: Open in my library leaves the share for the folder itself
    const octx = await alice.context(browser);
    const o = await openPage(octx, `${server.base}/?share=${folderToken}`);
    await o.locator(".fileRow", { hasText: "Folder share paper A" }).waitFor({ timeout: 15000 });
    await o.getByRole("button", { name: "Open in my library" }).click();
    await until(async () => !o.url().includes("share=") && new URL(o.url()).searchParams.get("folder") === sharedlab,
      { what: "the owner's library, at the folder" });
    assertEq(new URL(o.url()).searchParams.get("ws"), alice.ws, "in the share's workspace");
    await o.locator(".fileRow", { hasText: "Folder share paper A" }).waitFor({ timeout: 15000 });
    assert(await o.locator(".folderNewBtn").count() > 0, "the owner's own listing, editable");
    assertNoProblems(o);
    await octx.close();
    await alice.api(`/api/share-settings/folder/${sharedlab}`, { method: "DELETE" });
  });

  await step("folder share: Add to my library, and the link pasted into another Gamma's +, copy the folder to the top level", async () => {
    const paper = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Copied folder paper" } });
    await alice.file(paper.id, { folders: ["privatetop/copylab"] });
    await alice.api("/api/blocks", { method: "POST", body: { parent_id: paper.id, content: "a note that comes along" } });
    const deeper = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Copied subfolder paper" } });
    await alice.file(deeper.id, { folders: ["privatetop/copylab/sub"] });
    const copylab = await alice.folder("privatetop/copylab");
    const { token } = await alice.api(`/api/share/folder/${copylab}`, { method: "POST", body: { audience: "anyone" } });
    const link = `${server.base}/?share=${token}`;
    // the top-level folders of a library, each with its subfolders' names
    const tops = async (account, ws) => (await account.api(`/api/blocks/folders/subtree?ws=${ws}`)).block.children
      .map((f) => `${f.content}/${f.children.map((c) => c.content).join()}`);
    const landed = (page) => !page.url().includes("share=") && new URL(page.url()).searchParams.get("folder");

    // bob, signed in on alice's server: the listing's Add to my library
    // leaves the share view for his own library, which imports the folder
    // and opens it there
    const ctx = await bob.context(browser);
    const page = await openPage(ctx, link);
    await page.locator(".fileRow", { hasText: "Copied folder paper" }).waitFor({ timeout: 15000 });
    await page.getByRole("button", { name: "Add to my library", exact: true }).click();
    await until(async () => landed(page), { what: "bob's library, at the imported folder", timeout: 30000 });
    await page.locator(".fileRow", { hasText: "Copied folder paper" }).waitFor({ timeout: 15000 });
    assert((await page.textContent(".folderCurrent")).includes("copylab"), "the imported folder is open");
    const bobWs = new URL(page.url()).searchParams.get("ws");
    const bobTops = await tops(bob, bobWs);
    assert(bobTops.includes("copylab/sub") && !bobTops.some((f) => f.startsWith("privatetop/")),
      `the shared folder at the top level, nothing above it (${bobTops})`);
    assertNoProblems(page);
    await ctx.close();

    // another Gamma: the link pasted into +, fetched cross-origin with no session
    const other = new Server();
    let octx = null;
    try {
      await other.start();
      await other.manage("create-user", "share-importer", "share-importer-pw");
      const importer = await new Account(other, "share-importer", "share-importer-pw").login();
      octx = await importer.context(browser);
      const o = await openPage(octx, `${other.base}/?ws=${importer.ws}`);
      await o.waitForSelector(".folderNewBtn");
      await o.locator('[data-guide="header.add"]').click();
      await o.locator('[data-guide="add.urlInput"]').fill(`Here it is: ${link}`);
      await o.locator(".addPopover .popoverHint", { hasText: "a page or a whole folder" }).waitFor();
      await o.locator('[data-guide="add.urlInput"]').press("Enter");
      await until(async () => landed(o), { what: "the other library, at the imported folder", timeout: 30000 });
      assertEq(new URL(o.url()).searchParams.get("folder"), copylab, "a fresh library keeps the folder's id");
      await o.locator(".fileRow", { hasText: "Copied folder paper" }).waitFor({ timeout: 15000 });
      assertEq((await tops(importer, importer.ws)).join(), "copylab/sub", "only the shared folder, at the top level");
      assertEq((await importer.api(`/api/blocks/${deeper.id}`)).content, "Copied subfolder paper", "the subfolder's page came too");
      const notes = (await importer.api(`/api/blocks/${paper.id}/children`)).children.map((c) => c.content);
      assertEq(notes.join(), "a note that comes along", "with its notes");
      assertNoProblems(o);
    } finally {
      await octx?.close();
      await other.stop();
      await alice.api(`/api/share-settings/folder/${copylab}`, { method: "DELETE" });
    }
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

  await step("share: opening the popover shares nothing; Share makes a signed-in link and shows the access; Stop sharing asks first", async () => {
    const pg = await alice.api("/api/pages", { method: "POST", body: { title: "Invite first" } });
    const ctx = await alice.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${pg.id}&ws=${alice.ws}`);
    await page.click("button[aria-label='Share']");
    const pop = page.locator(".sharePopover");
    const noLink = "Not shared yet. Share makes a link you can send.";
    await pop.getByText(noLink).waitFor();
    await until(async () => (await pop.textContent()).includes("Share “Invite first”"), { what: "the page's name in the title" });
    assertEq((await alice.api(`/api/share-settings/${pg.id}`)).token, null, "opening the popover shares nothing");
    assertEq(await pop.locator(".setPictureChoice").count(), 0, "no access controls before Share");
    assertEq(await pop.getByRole("textbox", { name: "Invite people by name…" }).count(), 0, "no invite box before Share");
    await pop.getByRole("button", { name: "Share", exact: true }).click();
    await pop.getByRole("button", { name: "Copy link" }).waitFor();
    let settings = await alice.api(`/api/share-settings/${pg.id}`);
    assertEq(`${settings.audience}/${settings.role}`, "users/view", "Share makes a link any account here can read");
    await pop.getByText("Anyone signed in can read this page.").waitFor();
    await pop.getByRole("textbox", { name: "Invite people by name…" }).fill("bob");
    await pop.getByRole("option", { name: "bob", exact: true }).click();
    await pop.getByRole("button", { name: "Invite", exact: true }).click();
    await pop.locator(".aiProvRow", { hasText: "bob" }).waitFor();
    settings = await alice.api(`/api/share-settings/${pg.id}`);
    assertEq(JSON.stringify(settings.users), JSON.stringify([{ name: "bob", role: "view" }]), "bob is invited to view");
    await pop.getByRole("button", { name: "Invited only", exact: true }).click();
    await pop.getByText("Only the people above can open this page; the link does nothing for anyone else.").waitFor();
    assert(await pop.locator(".setSection", { hasText: "General access" }).getByRole("button", { name: "Edit", exact: true }).isDisabled(),
      "the View / Edit toggle stays, disabled, under Invited only");
    // Stop sharing sits beside Copy link
    await pop.locator('.setRow[data-setting="Share link"]').getByRole("button", { name: "Stop sharing", exact: true }).click();
    await pop.getByText("Stop sharing? The link stops working and the 1 invited person loses access.").waitFor();
    assert((await alice.api(`/api/share-settings/${pg.id}`)).token, "nothing stops before the confirm");
    await pop.locator(".mirrorConfirm").getByRole("button", { name: "Stop sharing", exact: true }).click();
    await pop.getByText(noLink).waitFor();
    assertEq((await alice.api(`/api/share-settings/${pg.id}`)).token, null, "the share stopped");
    assertEq(await pop.locator(".setPictureChoice").count(), 0, "the access controls go with the link");
    assertNoProblems(page);
    await ctx.close();
  });

  await step("share: a page inside a shared page names the link that opens it; a move into one asks first; its edit link makes a page inside", async () => {
    const top = await alice.api("/api/pages", { method: "POST", body: { title: "Reach top" } });
    const kid = await alice.api("/api/pages", { method: "POST", body: { title: "Reach kid", parent_page: top.id } });
    const loose = await alice.api("/api/pages", { method: "POST", body: { title: "Reach loose" } });
    const { token } = await alice.api(`/api/share/${top.id}`, { method: "POST", body: { audience: "anyone", role: "edit" } });
    const ctx = await alice.context(browser);
    const vctx = await browser.newContext({ viewport: { width: 1280, height: 860 } }); // no session: the link alone
    try {
      // the page inside: not shared itself, and the popover says the parent's link opens it
      const page = await openPage(ctx, `${server.base}/?page=${kid.id}&ws=${alice.ws}`);
      const pop = page.locator(".sharePopover");
      await page.click("button[aria-label='Share']");
      await pop.locator("[data-share-via]")
        .getByText("The link of “Reach top” opens this page too, as a page inside it: anyone with the link can edit it.").waitFor();
      await pop.getByText("Not shared yet. Share makes a link you can send.").waitFor();
      await page.keyboard.press("Escape");
      await pop.waitFor({ state: "detached" });
      // the shared page's own summary counts the page inside it
      await page.goto(`${server.base}/?page=${top.id}&ws=${alice.ws}`);
      await page.click("button[aria-label='Share']");
      await pop.getByText("Anyone with the link can edit this page and the 1 page inside it.").waitFor();
      assertEq(await pop.locator("[data-share-via]").count(), 0, "no other link opens the shared page");
      await page.keyboard.press("Escape");
      await pop.waitFor({ state: "detached" });

      // Move into page… into the shared page says who will open the page before it moves
      await page.goto(`${server.base}/?page=${loose.id}&ws=${alice.ws}`);
      await page.locator(".tabStrip .tab", { hasText: "Reach loose" }).click({ button: "right" });
      await page.locator(".ctxMenuItem", { hasText: "Move into page…" }).click();
      const dialog = page.getByRole("dialog", { name: "Move into page" });
      await dialog.getByRole("textbox").fill("Reach top");
      await dialog.getByRole("option").filter({ has: page.locator("strong", { hasText: /^Reach top$/ }) }).click();
      const confirm = page.locator(".confirmModal");
      await confirm.getByText("Move into a shared page?", { exact: true }).waitFor();
      await confirm.getByText("The link of “Reach top” opens it: anyone with the link can edit the page you move, too.").waitFor();
      assertEq((await alice.api(`/api/blocks/${loose.id}`)).properties?.parent_page, undefined, "nothing moves before the confirm");
      await confirm.getByRole("button", { name: "Move", exact: true }).click();
      await until(async () => (await alice.api(`/api/blocks/${loose.id}`)).properties?.parent_page === top.id,
        { what: "inside the shared page after the confirm" });
      assertNoProblems(page);

      // the edit link's visitor makes a page inside the shared page from its Pages inside panel
      const v = await openPage(vctx, `${server.base}/?share=${token}`);
      await v.waitForSelector(".readOnlyTitle", { timeout: 15000 });
      await v.locator(".subPagesPanel .subPageNew", { hasText: "New page inside" }).click();
      await until(() => v.url().includes("page="), { what: "the new page opens in the share view" });
      await v.keyboard.type("Made through the link");
      await v.keyboard.press("Enter");
      await until(async () => (await alice.api(`/api/pages/${top.id}/family`)).sub_pages.some((p) => p.title === "Made through the link"),
        { what: "the page inside the shared page, in alice's library" });
      assertNoProblems(v);
    } finally {
      await vctx.close();
      await ctx.close();
    }
  });

  await step("share: the command palette's Share this page… opens the open page's popover, not the last one shown", async () => {
    const shared = await alice.api("/api/pages", { method: "POST", body: { title: "Palette shared" } });
    const plain = await alice.api("/api/pages", { method: "POST", body: { title: "Palette plain" } });
    await alice.api(`/api/share/${shared.id}`, { method: "POST" });
    const ctx = await alice.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${shared.id}&ws=${alice.ws}`);
    await page.locator(".blockList").first().waitFor();
    const pop = page.locator(".sharePopover");
    const shareFromPalette = async () => {
      await page.keyboard.press("Control+Shift+p");
      const dialog = page.getByRole("dialog", { name: "Command palette" });
      await dialog.getByRole("textbox", { name: "Type a command" }).fill(">share this page");
      await until(async () => /Share this page/.test(await dialog.locator('[role="option"][aria-selected="true"]').textContent()), { what: "the Share command is selected" });
      await page.keyboard.press("Enter");
      await pop.waitFor();
    };
    await shareFromPalette();
    await pop.getByRole("button", { name: "Copy link" }).waitFor();
    await page.keyboard.press("Escape");
    await pop.waitFor({ state: "detached" });
    // Another page, opened in place: the popover is that page's, unshared.
    await page.keyboard.press("Control+p");
    await page.getByRole("dialog", { name: "Open a page" }).getByRole("option", { name: /Palette plain/ }).click();
    await until(async () => (await page.locator(".pageTitleRow .titleText").textContent()).includes("Palette plain"), { what: "the second page open" });
    await shareFromPalette();
    await pop.getByText("Not shared yet. Share makes a link you can send.").waitFor();
    assertEq(await pop.getByRole("button", { name: "Copy link" }).count(), 0, "not the first page's link");
    assertEq((await alice.api(`/api/share-settings/${plain.id}`)).token, null, "opening the popover shares nothing");
    assertNoProblems(page);
    await ctx.close();
    await alice.api(`/api/share-settings/${shared.id}`, { method: "DELETE" });
  });

  const account = alice2;
  let token;
  if (!pdfPageId) { console.log("  skip  share: needs the pdf steps (drop --only)"); return; }

  // An image block on the paper's page, so the share view has an upload to fetch.
  const up = await account.upload("/api/upload-image", PNG_1PX, "dot.png", "image/png");
  await account.api("/api/blocks", { method: "POST", body: { parent_id: pdfPageId, content: `figure ![](${up.url})` } });

  await step("share: Share makes the link, shown on the copy button; picking Anyone opens it to everyone", async () => {
    const ctx = await account.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${pdfPageId}&ws=${account.ws}`);
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Share']");
    await page.waitForSelector(".sharePopover");
    await page.locator(".sharePopover").getByRole("button", { name: "Share", exact: true }).click();
    const copyBtn = page.locator(".sharePopover button", { hasText: /Copy link|Copied/ }).first();
    await copyBtn.waitFor({ timeout: 10000 });
    token = new URL(await copyBtn.getAttribute("title")).searchParams.get("share");
    await page.locator(".sharePopover").getByRole("button", { name: "Anyone", exact: true }).click();
    await page.locator(".sharePopover").getByText("Anyone with the link can read this page.").waitFor();
    await page.keyboard.press("Escape");
    await page.locator(".sharePopover").waitFor({ state: "detached" });
    if (!token) token = (await account.api(`/api/share-settings/${pdfPageId}`)).token;
    assert(token, "share token");
    assertNoProblems(page);
    await ctx.close();
    return `?share=${token.slice(0, 8)}…`;
  });

  await step("share: an anonymous visitor sees the paper, the highlight and the image, read-only", async () => {
    // A note linking to the paper and referencing a block on a page the
    // share does not open: the share view looks both up through the token
    // (/block-search), and the outside one stays unresolved.
    const elsewhere = await account.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Not shared" } });
    const secret = await account.api("/api/blocks", { method: "POST", body: { parent_id: elsewhere.id, content: "an unshared secret" } });
    const note = await account.api("/api/blocks", { method: "POST", body: {
      parent_id: pdfPageId, content: `links: [the paper](/?page=${pdfPageId}) and [[${secret.id}]]` } });
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const lookups = [];
    ctx.on("response", (r) => { if (r.url().includes("/api/block-search")) lookups.push(r.status()); });
    const page = await openPage(ctx, `${server.base}/?share=${token}`);
    await page.waitForSelector(".readOnlyTitle", { timeout: 15000 });
    assert((await page.textContent(".readOnlyTitle")).includes("Rydberg paper"), "title in the share view");
    await waitForPdf(page, 1);
    await page.waitForSelector('[data-page="1"] [data-hl-id]', { timeout: 10000 });
    await until(async () => {
      const imgs = await page.$$eval("img.mdImg", (els) => els.map((e) => [e.getAttribute("src"), e.naturalWidth]));
      return imgs.length === 1 && imgs[0][0].includes("share=") && imgs[0][1] === 1;
    }, { what: "image served through the share token" });
    await page.locator(".blockRow", { hasText: "links:" }).waitFor();
    await until(() => lookups.length > 0, { what: "the note's links looked up" });
    assertEq(lookups.filter((s) => s !== 200).length, 0, "lookups go through the share token");
    assert(!(await page.textContent("body")).includes("an unshared secret"), "a block outside the share stays hidden");
    await account.api(`/api/blocks/${note.id}`, { method: "DELETE" });
    await account.api(`/api/blocks/${elsewhere.id}`, { method: "DELETE" });
    await page.locator(".blockRow", { hasText: "figure" }).locator(".blockBody").click();
    await sleep(400); // a negative check: nothing to wait for, so give an editor time to (not) appear
    assert((await page.$(".blockEditorCm")) == null, "no editor opens on a view-only share");
    assertNoProblems(page);
    await ctx.close();
  });

  await step("share: a visitor's export holds the shared page, never a block or a file outside it", async () => {
    const python = process.env.GAMMA_E2E_PYTHON || path.join(ROOT, "backend", "venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
    const picture = await account.upload("/api/upload-image", PNG_1PX, "private.png", "image/png");
    const pictureName = picture.url.split("/").pop().split("?")[0];
    const hidden = await account.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Hiddentitleword" } });
    const secret = await account.api("/api/blocks", { method: "POST", body: { parent_id: hidden.id, content: `TOPSECRETWORD ![](${picture.url})` } });
    const shared = await account.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Page for the visitor" } });
    await account.api("/api/blocks", { method: "POST", body: { parent_id: shared.id, content: `Openword and [[${secret.id}]]` } });
    await account.api("/api/blocks", { method: "POST", body: { parent_id: shared.id, content: `![[${secret.id}]]` } });
    const { token: visitorToken } = await account.api(`/api/share/${shared.id}`, { method: "POST" });
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const page = await openPage(ctx, `${server.base}/?share=${visitorToken}`);
    await page.locator(".blockRow", { hasText: "Openword" }).waitFor();
    for (const format of ["Markdown", "Obsidian", "Notion"]) {
      const dialog = await openDialog(page, "Export");
      await dialog.getByRole("button", { name: format, exact: true }).click();
      await dialog.getByRole("button", { name: "Next", exact: true }).click();
      const download = page.waitForEvent("download");
      await dialog.getByRole("button", { name: "Export", exact: true }).click();
      // A page with no file to bundle comes as one .md, a vault and a Notion export as a zip.
      const held = execFileSync(python, ["-c", `
import json, sys, zipfile
name = sys.argv[1]
if zipfile.is_zipfile(name):
    z = zipfile.ZipFile(name)
    print(json.dumps({n: z.read(n).decode("utf-8", "replace") for n in z.namelist()}))
else:
    print(json.dumps(open(name, encoding="utf-8").read()))
`, await (await download).path()]).toString();
      assert(held.includes("Openword"), `${format}: the shared page is in the export`);
      for (const word of ["TOPSECRETWORD", "Hiddentitleword", pictureName]) assert(!held.includes(word), `${format}: nothing names ${word}`);
    }
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
    // A share's editor makes a page inside the shared page (the step above makes one).
    await page.keyboard.type(" /page");
    await page.waitForSelector(".slashMenu");
    assertEq(await page.locator(".slashMenu .slashMenuItem", { hasText: "New page" }).count(), 1, "/page offers a new page inside in an edit share");
    for (let i = 0; i < 6; i++) await page.keyboard.press("Backspace");
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
