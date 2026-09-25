// Triggered tours and hints (docs/dev/onboarding.md): an offer comes right
// after the thing happened, beside it, without dimming the app; "Show me"
// runs the tour, a hint is one card; each is offered once per version and
// one per page load, never with "Suggest tours" off, and the card never
// takes the caret or closes the popover it points into. A fresh account, so
// no earlier scenario has seen these offers.
import { Account } from "../harness.mjs";
import { editRow } from "./notes.mjs";

export async function triggeredGuideScenarios(env) {
  const { server, browser, step, until, assert, assertEq, assertNoProblems, openPage, makePdf, flags } = env;
  if (flags.only && !"triggered guide".includes(flags.only)) return;
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
      assertEq(await page.locator(".guideCard .guideStep").textContent(), "Quick tour · 3 steps");
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
      await until(async () => await page.locator(".guideCard").count() === 0);
      assertEq(await progress(page, "tables"), "done");
      await page.reload();
      await page.waitForSelector('[data-guide="notes.table"]');
      await page.waitForTimeout(1500);
      assertEq(await page.locator("[data-guide-offer]").count(), 0, "offered once per version");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("triggered guide: a new share link offers the sharing tour inside the popover", async () => {
    const pg = await user.api("/api/pages", { method: "POST", body: { title: "Shared page" } });
    const { ctx, page } = await open(`&page=${pg.id}`);
    try {
      await page.click('[data-guide="header.share"]');
      await page.getByRole("button", { name: "Create link" }).click();
      await page.waitForSelector('[data-guide-offer="sharing"] .guideCard');
      await page.getByRole("button", { name: "Show me" }).click();
      for (const id of ["share-access", "share-people", "share-link"]) {
        await page.waitForSelector(`[data-guide-overlay="${id}"] .guideCard`);
        assertEq(await page.locator(".sharePopover").count(), 1, `the share popover stays open at ${id}`);
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

  await step("triggered guide: someone else on the page offers the presence tour", async () => {
    const pg = await user.api("/api/pages", { method: "POST", body: { title: "Busy page" } });
    await user.api("/api/blocks", { method: "POST", body: { parent_id: pg.id, content: "together" } });
    const a = await open(`&page=${pg.id}`);
    const b = await open(`&page=${pg.id}`, { suggestTours: false });
    try {
      await a.page.waitForSelector('[data-guide-offer="presence"] .guideCard');
      // The same account in the other tab sees A arrive, and its synced
      // profile says to suggest tours: it gets the offer too. Esc passes it up.
      await b.page.waitForSelector('[data-guide-offer="presence"] .guideCard');
      await b.page.keyboard.press("Escape");
      await editRow(b.page, "together");
      await a.page.getByRole("button", { name: "Show me" }).click();
      await a.page.waitForSelector('[data-guide-overlay="presence-who"] .guideCard');
      await primary(a.page).click();
      await a.page.waitForSelector('[data-guide-overlay="presence-where"] .guideCard');
      // The other person leaves: the optional "the block they are on" step
      // has nothing left to point at and passes by without a warning.
      const warnings = [];
      a.page.on("console", (m) => { if (m.type() === "warning" && m.text().startsWith("guide:")) warnings.push(m.text()); });
      await b.ctx.close();
      await a.page.waitForSelector('[data-guide-overlay="presence-undo"] .guideCard');
      assertEq(warnings.length, 0, `no anchor warning: ${warnings.join("; ")}`);
      await primary(a.page).click();
      await until(async () => await a.page.locator(".guideCard").count() === 0);
      assertNoProblems(a.page);
    } finally { await a.ctx.close(); await b.ctx.close(); }
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
