// Triggered tours and hints (docs/dev/onboarding.md): an offer comes right
// after the thing happened, beside it, without dimming the app; "Show me"
// runs the tour, a hint is one card; each is offered once per version and
// one per page load, never with "Suggest tours" off, and the card never
// takes the caret or closes the popover it points into. A fresh account, so
// no earlier scenario has seen these offers.
import { Account, wanted } from "../harness.mjs";
import { editRow } from "./notes.mjs";
import { waitForPdf } from "./pdf.mjs";

export async function triggeredGuideScenarios(env) {
  const { server, browser, step, until, assert, assertEq, assertNoProblems, openPage, makePdf } = env;
  if (!wanted("triggered guide")) return;
  server.manage("create-user", "tourist", "tourist-pw");
  const user = await new Account(server, "tourist", "tourist-pw").login();
  const open = async (query, { setup, ...opts } = {}) => {
    const ctx = await user.context(browser, { suggestTours: true, ...opts });
    await setup?.(ctx);
    const page = await openPage(ctx, `${server.base}/?ws=${user.ws}${query}`);
    return { ctx, page };
  };
  const progress = (page, id) => page.evaluate((id) => JSON.parse(localStorage.getItem(`gamma-guide:tourist:${id}`) || "null")?.state, id);
  const primary = (page) => page.locator(".guideCard .uiBtn.primary");

  await step("triggered guide: a new table offers the table tour; hover tools show while pointed at", async () => {
    const pg = await user.api("/api/pages", { method: "POST", body: { title: "Table page" } });
    await user.api("/api/blocks", { method: "POST", body: { parent_id: pg.id, content: "| a | b |\n|---|---|\n| 1 | 2 |" } });
    const { ctx, page } = await open(`&page=${pg.id}`);
    try {
      await page.waitForSelector('[data-guide-offer="tables"] .guideCard');
      assertEq(await page.locator(".guideDim").count(), 0, "an offer does not dim the app");
      assertEq(await page.locator(".guideCard .guideStep").textContent(), "Quick tour · 4 steps", "the add-a-table step is dropped: there is one");
      await page.getByRole("button", { name: "Show me" }).click();
      await page.waitForSelector('[data-guide-overlay="table-add"] .guideCard');
      const add = page.locator('[data-guide="notes.tableAdd"]');
      await until(async () => (await add.getAttribute("data-guide-active")) !== null, { what: "the add strip is marked active" });
      await until(async () => (await add.evaluate((el) => getComputedStyle(el).opacity)) === "1", { what: "the hover-only strip shows" });
      await primary(page).click();
      await page.waitForSelector('[data-guide-overlay="table-move"]');
      assertEq(await add.getAttribute("data-guide-active"), null, "the previous anchor is unmarked");
      await primary(page).click();
      await page.waitForSelector('[data-guide-overlay="table-cell"]');
      await primary(page).click();
      await page.waitForSelector('[data-guide-overlay="table-whole"]');
      await primary(page).click();
      await until(async () => await page.locator(".guideCard").count() === 0);
      assertEq(await progress(page, "tables"), "done");
      await page.reload();
      await page.waitForSelector('[data-guide="notes.table"]');
      await page.waitForTimeout(1500);
      assertEq(await page.locator("[data-guide-offer]").count(), 0, "offered once per version");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("triggered guide: the table's corner handle selects the whole table; the menu or Delete removes it", async () => {
    const pg = await user.api("/api/pages", { method: "POST", body: { title: "Table removal" } });
    const table = "| a | b |\n|---|---|\n| 1 | 2 |";
    const byMenu = await user.api("/api/blocks", { method: "POST", body: { parent_id: pg.id, content: `before\n\n${table}\n\nafter` } });
    const byKey = await user.api("/api/blocks", { method: "POST", body: { parent_id: pg.id, content: table } });
    const content = async (id) => (await user.api(`/api/blocks/${pg.id}/subtree`)).block.children.find((b) => b.id === id)?.content;
    const { ctx, page } = await open(`&page=${pg.id}`, { suggestTours: false });
    try {
      const corner = page.locator('[data-guide="notes.tableCorner"]');
      await corner.first().waitFor({ state: "attached" });
      assertEq(await corner.count(), 2, "every editable table has a corner handle");
      // The tables hint the first table offered sits under the corner; the
      // object menu it opens is taller than the hint card's clearance.
      if (await page.locator(".guideCard").count()) {
        await page.locator(".guideCard .guideClose").click();
        await until(async () => await page.locator(".guideCard").count() === 0);
      }
      await page.locator('[data-guide="notes.table"]').first().hover();
      await corner.first().click();
      await page.waitForSelector(".mdObjectSelected");
      await page.getByRole("button", { name: "Delete table" }).click();
      await until(async () => await content(byMenu.id) === "before\n\nafter", { what: "the table left its block, the text around it stayed" });
      await page.locator('[data-guide="notes.table"]').first().hover();
      await corner.first().click();
      await page.waitForSelector(".mdObjectSelected");
      await page.keyboard.press("Delete");
      await until(async () => (await content(byKey.id)) === "", { what: "Delete removed the selected table" });
      assertEq(await page.locator('[data-guide="notes.table"]').count(), 0);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("triggered guide: a new share link offers the sharing tour inside the popover", async () => {
    const pg = await user.api("/api/pages", { method: "POST", body: { title: "Shared page" } });
    const { ctx, page } = await open(`&page=${pg.id}`);
    try {
      await page.click('[data-guide="header.share"]');
      await page.locator(".sharePopover").getByRole("button", { name: "Anyone", exact: true }).click();
      await page.waitForSelector('[data-guide-offer="sharing"] .guideCard');
      await page.getByRole("button", { name: "Show me" }).click();
      // the popover top to bottom; an anyone-with-the-link share gets the access step that says so
      for (const id of ["share-link", "share-people", "share-access-anyone", "share-stop"]) {
        await page.waitForSelector(`[data-guide-overlay="${id}"] .guideCard`);
        assertEq(await page.locator(".sharePopover").count(), 1, `the share popover stays open at ${id}`);
        if (id === "share-access-anyone") assert((await page.textContent(".guideCard")).includes("no sign-in needed"), "the anyone wording");
        await primary(page).click();
      }
      await until(async () => await page.locator(".guideCard").count() === 0);
      assertEq(await progress(page, "sharing"), "done");
      // The Tours menu lists it where it can start: a shared page.
      await page.click('[data-guide="header.account"]');
      await page.click('[data-guide="account.tour"]');
      assertEq(await page.locator('[data-tour="sharing"]').count(), 1, "the menu offers the sharing tour on a shared page");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("triggered guide: from the menu, the sharing tour has the user pick who can open the page only when there is no link", async () => {
    const pg = await user.api("/api/pages", { method: "POST", body: { title: "Menu shared page" } });
    const { ctx, page } = await open(`&page=${pg.id}`);
    const startSharing = async () => {
      await page.click('[data-guide="header.account"]');
      await page.click('[data-guide="account.tour"]');
      await page.click('[data-tour="sharing"]');
    };
    try {
      await startSharing();
      await page.waitForSelector('[data-guide-overlay="share-create"] .guideCard');
      await page.locator(".sharePopover").getByRole("button", { name: "Signed in", exact: true }).click();
      await page.waitForSelector('[data-guide-overlay="share-link"] .guideCard');
      assertEq((await user.api(`/api/share-settings/${pg.id}`)).audience, "users", "the pick is the new share's audience");
      await page.keyboard.press("Escape");
      await until(async () => await page.locator(".guideCard").count() === 0);
      // Shared now: the link shows up only once the popover has loaded, and
      // the create step passes by without a card or a warning.
      await page.reload();
      await page.waitForSelector('[data-guide="header.share"]');
      const warnings = [];
      page.on("console", (m) => { if (m.type() === "warning" && m.text().startsWith("guide:")) warnings.push(m.text()); });
      await startSharing();
      await page.waitForSelector('[data-guide-overlay="share-link"] .guideCard');
      assertEq(warnings.length, 0, `no anchor warning: ${warnings.join("; ")}`);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("triggered guide: a cited AI reply offers the citation tour; the demo opens the passage", async () => {
    const quote = "Rydberg atoms interact strongly";
    const upload = await user.upload("/api/uploads", makePdf([["Introduction", `${quote} over long distances.`]]), "cited.pdf", "application/pdf");
    const paper = await user.api(`/api/blocks/by-doc/${upload.doc_id}`, { method: "POST", body: { default_title: "Cited paper", source_url: upload.source_url } });
    const models = { enabled: true, models: [{ id: "demo:model", provider: "demo", provider_name: "Demo", model: "model" }], default: "demo:model" };
    const reply = `As the paper puts it [p. 1](/?page=${paper.id}&pdf_page=1&quote=${encodeURIComponent(quote)}).`;
    const { ctx, page } = await open(`&page=${paper.id}`, { setup: async (ctx) => {
      await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
      await ctx.route("**/api/ai/models*", (route) => route.fulfill({ json: models }));
      await ctx.route("**/api/ai/chat", (route) => route.fulfill({ contentType: "application/x-ndjson", body: JSON.stringify({ delta: reply }) + "\n" }));
    } });
    try {
      await page.waitForSelector('[data-guide="pdf.textLayer"] span');
      const input = page.getByRole("combobox", { name: "Message AI" });
      await input.fill("What does it say about interactions?");
      await input.press("Enter");
      await page.waitForSelector('[data-guide-offer="citations"] .guideCard');
      await page.getByRole("button", { name: "Show me" }).click();
      await page.waitForSelector('[data-guide-overlay="citation-mark"] .guideCard', { timeout: 20000 });
      assertEq(await page.locator('[data-guide="pdf.citation"]').count(), 1, "the passage is marked");
      await primary(page).click();
      await until(async () => await page.locator(".guideCard").count() === 0);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("triggered guide: the math hint keeps the caret in the editor", async () => {
    const pg = await user.api("/api/pages", { method: "POST", body: { title: "Math page" } });
    await user.api("/api/blocks", { method: "POST", body: { parent_id: pg.id, content: "Energy" } });
    const { ctx, page } = await open(`&page=${pg.id}`);
    try {
      await editRow(page, "Energy");
      await page.keyboard.type(" $E=mc");
      await page.waitForSelector('[data-guide-offer="math-keys"] .guideCard');
      assertEq(await page.locator(".guideCard .guideStep").textContent(), "Tip");
      await page.getByRole("button", { name: "Got it" }).click();
      await until(async () => await page.locator(".guideCard").count() === 0);
      assert(await page.evaluate(() => !!document.activeElement?.closest(".cm-content")), "the editor keeps focus");
      await page.keyboard.type("^2");
      assert((await page.locator(".blockEditorCm .cm-content").textContent()).includes("E=mc^2"), "typing goes on in place");
      assertEq(await progress(page, "math-keys"), "done");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("triggered guide: the fourth trip home offers the Ctrl+P hint", async () => {
    await user.api("/api/pages", { method: "POST", body: { title: "Trip page" } });
    const { ctx, page } = await open("");
    try {
      for (let trip = 1; trip <= 4; trip++) {
        await page.locator(".pageCard, .fileRow", { hasText: "Trip page" }).first().dblclick();
        await until(async () => new URL(page.url()).searchParams.has("block") || new URL(page.url()).searchParams.has("page"));
        assertEq(await page.locator("[data-guide-offer]").count(), 0, `no hint before trip ${trip} ends`);
        await page.click('[data-guide="header.home"]');
      }
      await page.waitForSelector('[data-guide-offer="quick-open"] .guideCard');
      await page.getByRole("button", { name: "Got it" }).click();
      assertEq(await progress(page, "quick-open"), "done");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("triggered guide: the first link jump offers the Back hint; Back returns and retires it", async () => {
    const target = await user.api("/api/pages", { method: "POST", body: { title: "Jump target" } });
    const source = await user.api("/api/pages", { method: "POST", body: { title: "Jump source" } });
    await user.api("/api/blocks", { method: "POST", body: { parent_id: source.id, content: `See [Jump target](/?page=${target.id})` } });
    const { ctx, page } = await open(`&page=${source.id}`);
    try {
      assertEq(await page.locator('[data-guide="header.back"]').count(), 0, "no Back before a jump");
      await page.locator("a.gammaLinkCard", { hasText: "Jump target" }).click();
      await until(() => new URL(page.url()).searchParams.get("page") === target.id || new URL(page.url()).searchParams.get("block") === target.id,
        { what: "the link opened its page" });
      await page.waitForSelector('[data-guide-offer="back"] .guideCard');
      assertEq(await page.locator(".guideCard .guideStep").textContent(), "Tip");
      assertEq(await page.locator(".guideDim").count(), 0, "a hint does not dim the app");
      // Going back on their own is what the hint teaches: it retires it.
      await page.getByRole("button", { name: "Back", exact: true }).click();
      await until(() => [new URL(page.url()).searchParams.get("page"), new URL(page.url()).searchParams.get("block")].includes(source.id),
        { what: "Back returned to the source page" });
      await until(async () => await page.locator(".guideCard").count() === 0, { what: "the hint went with the first Back" });
      assertEq(await progress(page, "back"), "done");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("triggered guide: joining a shared workspace offers its tour, which opens the account menu", async () => {
    server.manage("create-workspace", "Tour lab", "tourist", "shared");
    const { ctx, page } = await open("");
    try {
      await page.waitForSelector('[data-guide-offer="workspaces"] .guideCard', { timeout: 10000 });
      await page.getByRole("button", { name: "Show me" }).click();
      await page.waitForSelector('[data-guide-overlay="ws-switch"] .guideCard');
      await page.waitForSelector('[data-guide="account.workspaces"] .wsItem');
      await primary(page).click();
      await page.waitForSelector('[data-guide-overlay="ws-role"] .guideCard');
      assertEq(await page.locator(".userPopover").count(), 1, "the menu stays open between its steps");
      await primary(page).click();
      await until(async () => await page.locator(".guideCard").count() === 0);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("triggered guide: another person on the page offers the presence tour; your own second tab does not", async () => {
    // A shared workspace the tourist owns and a second account edits.
    server.manage("create-user", "visitor", "visitor-pw");
    const out = server.manage("create-workspace", "Presence lab", "tourist", "shared");
    const labId = (out.match(/workspace (\S+)/) || [])[1];
    assert(labId, `shared workspace id from: ${out}`);
    server.manage("set-member", labId, "visitor", "editor");
    const visitor = await new Account(server, "visitor", "visitor-pw").login();
    visitor.ws = labId;
    const inLab = Object.assign(Object.create(Object.getPrototypeOf(user)), user, { ws: labId });
    const pg = await inLab.api("/api/pages", { method: "POST", body: { title: "Busy page" } });
    await inLab.api("/api/blocks", { method: "POST", body: { parent_id: pg.id, content: "together" } });
    const url = `${server.base}/?ws=${labId}&page=${pg.id}`;
    // Being in shared workspaces offers their tour once the page settles;
    // keep it out of the way so only presence can be offered.
    const tab = async (account, opts = {}) => {
      const ctx = await account.context(browser, opts);
      await ctx.addInitScript(() => localStorage.setItem("gamma-guide:tourist:workspaces", JSON.stringify({ state: "done", version: 999 })));
      return { ctx, page: await openPage(ctx, url) };
    };
    const a = await tab(user, { suggestTours: true });
    const own = await tab(user, { suggestTours: true });
    let v = null;
    try {
      await a.page.locator(".blockRendered", { hasText: "together" }).first().waitFor();
      // The same account in another tab shows in the stack, but is nobody else.
      await until(async () => (await a.page.$$(".presenceBar .peerAvatar")).length >= 1, { what: "the own second tab shows in the stack" });
      await a.page.waitForTimeout(1500);
      assertEq(await a.page.locator("[data-guide-offer]").count(), 0, "your own second tab offers nothing");
      assertEq(await own.page.locator("[data-guide-offer]").count(), 0, "nor does the first tab, seen from the second");
      await own.ctx.close();
      v = await tab(visitor);
      await a.page.waitForSelector('[data-guide-offer="presence"] .guideCard');
      await editRow(v.page, "together");
      await a.page.getByRole("button", { name: "Show me" }).click();
      await a.page.waitForSelector('[data-guide-overlay="presence-who"] .guideCard');
      await primary(a.page).click();
      await a.page.waitForSelector('[data-guide-overlay="presence-where"] .guideCard');
      // The other person leaves: the optional "the block they are on" step
      // has nothing left to point at and passes by without a warning.
      const warnings = [];
      a.page.on("console", (m) => { if (m.type() === "warning" && m.text().startsWith("guide:")) warnings.push(m.text()); });
      await v.ctx.close();
      await a.page.waitForSelector('[data-guide-overlay="presence-undo"] .guideCard');
      assertEq(warnings.length, 0, `no anchor warning: ${warnings.join("; ")}`);
      await primary(a.page).click();
      await until(async () => await a.page.locator(".guideCard").count() === 0);
      assertNoProblems(a.page);
    } finally { await a.ctx.close(); await own.ctx.close(); await v?.ctx.close(); }
  });

  await step("triggered guide: the handwriting tour has the user draw first, leads with the note, and ends with the pen armed", async () => {
    const upload = await user.upload("/api/uploads", makePdf([["A page to write on"]]), "ink-tour.pdf", "application/pdf");
    const paper = await user.api(`/api/blocks/by-doc/${upload.doc_id}`, { method: "POST", body: { default_title: "Ink tour paper", source_url: upload.source_url } });
    const { ctx, page } = await open(`&page=${paper.id}`);
    const line = async (from, to) => {
      await page.mouse.move(...from);
      await page.mouse.down();
      await page.mouse.move(...to, { steps: 12 });
      await page.mouse.up();
    };
    try {
      await waitForPdf(page, 1);
      await page.click('[data-guide="header.account"]');
      await page.click('[data-guide="account.tour"]');
      await page.click('[data-tour="handwriting"]');
      // Nothing drawn yet: the tour opens the tools and asks for a drawing.
      await page.waitForSelector('[data-guide-overlay="ink-draw"] .guideCard');
      await page.waitForSelector(".pdfInkBar");
      const box = await page.locator('[data-page="1"]').boundingBox();
      await line([box.x + 100, box.y + 150], [box.x + 260, box.y + 170]);
      // Why it is worth it first: the drawing is a note block, caption included.
      await page.waitForSelector('[data-guide-overlay="ink-note"] .guideCard');
      assertEq(await page.locator('[data-guide="notes.ink"][data-guide-active]').count(), 1, "the ink block is pointed at");
      await primary(page).click();
      await page.waitForSelector('[data-guide-overlay="ink-style"] .guideCard');
      await page.click(".pdfInkBar .inkToolBtn.modeActive");
      await page.waitForSelector('[data-guide-overlay="ink-erase"] .guideCard');
      await page.click('[data-guide="ink.eraser"]');
      await line([box.x + 180, box.y + 120], [box.x + 180, box.y + 200]);
      await page.waitForSelector('[data-guide-overlay="ink-lasso"] .guideCard');
      assertEq(await page.locator(".guideSegments i").count(), 5, "five steps from the menu");
      // The erase card's promise: Ctrl+Z brings the drawing back.
      await page.keyboard.press("Control+z");
      await page.waitForSelector('[data-guide="notes.ink"] svg.blockInkCard');
      await page.click('[data-guide="ink.lasso"]');
      await primary(page).click();
      await until(async () => await page.locator(".guideCard").count() === 0);
      assertEq(await progress(page, "handwriting"), "done");
      // Finishing leaves the pen armed, not the lasso or the eraser: the next drag writes.
      await until(async () => await page.locator('[data-guide="ink.lasso"].modeActive, [data-guide="ink.eraser"].modeActive').count() === 0,
        { what: "no lasso or eraser armed" });
      assertEq(await page.locator('.pdfInkBar .inkToolBtn.modeActive:not([aria-label="Hand"])').count(), 1, "the pen is armed again");
      // With a drawing on the page, a replay starts at the drawing's note instead.
      await page.click('[data-guide="header.account"]');
      await page.click('[data-guide="account.tour"]');
      await page.click('[data-tour="handwriting"]');
      await page.waitForSelector('[data-guide-overlay="ink-note"] .guideCard');
      await page.keyboard.press("Escape");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("triggered guide: Suggest tours off in Settings stops offers on every device", async () => {
    const pg = await user.api("/api/pages", { method: "POST", body: { title: "Quiet table" } });
    await user.api("/api/blocks", { method: "POST", body: { parent_id: pg.id, content: "| x |\n|---|\n| 1 |" } });
    const home = await open("");
    try {
      await home.page.click('[data-guide="header.account"]');
      await home.page.getByRole("button", { name: "Settings…" }).click();
      await home.page.locator(".settingsNavBtn", { hasText: "Appearance" }).click();
      const toggle = home.page.getByRole("checkbox", { name: "Suggest tours" });
      assert(await toggle.isChecked(), "on by default");
      await toggle.click({ force: true });
      await until(async () => (await user.api("/api/prefs/profile")).value?.suggestTours === false, { what: "the switch reaches the account" });
      assertNoProblems(home.page);
    } finally { await home.ctx.close(); }
    // Another browser still holds "on" locally until the account's profile
    // loads; nothing may be offered in between.
    const { ctx, page } = await open(`&page=${pg.id}`, { setup: (ctx) => ctx.addInitScript(() => localStorage.removeItem("gamma-guide:tourist:tables")) });
    try {
      await page.waitForSelector('[data-guide="notes.table"]');
      await page.waitForTimeout(1500);
      assertEq(await page.locator("[data-guide-offer]").count(), 0, "no offer while suggestions are off");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });
}
