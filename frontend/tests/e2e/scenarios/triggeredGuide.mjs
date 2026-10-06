// Triggered tours and hints (docs/dev/onboarding.md): an offer comes right
// after the thing happened, beside it, without dimming the app; "Show me"
// runs the tour, a hint is one card; each is offered once per version and
// one per page load, never with "Suggest tours" off, and the card never
// takes the caret or closes the popover it points into. A fresh account, so
// no earlier scenario has seen these offers.
import { Account, wanted } from "../harness.mjs";
import { closeEditor, editRow } from "./notes.mjs";
import { waitForPdf } from "./pdf.mjs";
import { TOURS } from "../../../src/guide/tours/index.js";

export async function triggeredGuideScenarios(env) {
  const { server, browser, step, until, assert, assertEq, assertNoProblems, openPage, makePdf } = env;
  if (!wanted("triggered guide")) return;
  server.manage("create-user", "tourist", "tourist-pw");
  const user = await new Account(server, "tourist", "tourist-pw").login();
  const open = async (query, { setup, seenWindows = false, seen = [], ...opts } = {}) => {
    const ctx = await user.context(browser, { suggestTours: true, ...opts });
    // A PDF's window-layout or viewer offer otherwise takes this load's only
    // offer slot and can cover the menu used to start a different tour;
    // `seen` settles any other guide this case would compete with for it.
    const settled = [...(seenWindows ? ["windows", "pdf-viewer"] : []), ...seen];
    // Only where nothing is recorded: the script runs on every navigation,
    // and a tour finished in this context stays finished.
    if (settled.length) await ctx.addInitScript((rows) => {
      for (const [id, version] of rows) {
        const key = `gamma-guide:tourist:${id}`;
        if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({ version, state: "dismissed" }));
      }
    }, settled.map((id) => [id, TOURS[id].version]));
    await setup?.(ctx);
    const page = await openPage(ctx, `${server.base}/?ws=${user.ws}${query}`);
    return { ctx, page };
  };
  const progress = (page, id, who = "tourist") => page.evaluate(([id, who]) =>
    JSON.parse(localStorage.getItem(`gamma-guide:${who}:${id}`) || "null")?.state, [id, who]);
  const primary = (page) => page.locator(".guideCard .uiBtn.primary");

  // Type /table into the row's block and close the editor: the table renders.
  const makeTable = async (page, rowText) => {
    await editRow(page, rowText);
    await page.keyboard.press("Enter");
    await page.keyboard.type("/table");
    await page.locator(".slashMenu .slashMenuItem", { hasText: "2×2 table" }).click();
    await until(async () => (await page.locator(".blockEditorCm .cm-content").textContent()).includes("|"), { what: "the table source is in the editor" });
    await closeEditor(page);
    await page.waitForSelector('[data-guide="notes.table"]');
  };

  await step("triggered guide: opening a page with a table offers nothing; making one offers the tour on the new table, which starts in a cell", async () => {
    const existing = await user.api("/api/pages", { method: "POST", body: { title: "Table page" } });
    await user.api("/api/blocks", { method: "POST", body: { parent_id: existing.id, content: "| a | b |\n|---|---|\n| 1 | 2 |" } });
    const pg = await user.api("/api/pages", { method: "POST", body: { title: "Grid page" } });
    // An older table above: the offer and the steps point at the one made.
    await user.api("/api/blocks", { method: "POST", body: { parent_id: pg.id, content: "| old | table |\n|---|---|\n| x | y |" } });
    const grid = await user.api("/api/blocks", { method: "POST", body: { parent_id: pg.id, content: "grid here" } });
    const { ctx, page } = await open(`&page=${existing.id}`);
    try {
      await page.waitForSelector('[data-guide="notes.table"]');
      await page.waitForTimeout(1500);
      assertEq(await page.locator("[data-guide-offer]").count(), 0, "a table that is merely there offers nothing");
      await page.goto(`${server.base}/?ws=${user.ws}&page=${pg.id}`);
      await makeTable(page, "grid here");
      await page.waitForSelector('[data-guide-offer="tables"] .guideCard');
      assertEq(await page.locator(".guideDim").count(), 0, "an offer does not dim the app");
      assertEq(await page.locator(".guideCard .guideStep").textContent(), "Quick tour · 3 steps", "the add-a-table step is dropped: there is one");
      const tables = page.locator('[data-guide="notes.table"]');
      const pointedAt = async (el) => (await el.getAttribute("data-guide-active")) !== null;
      await until(async () => await pointedAt(tables.nth(1)), { what: "the offer points at the table just made" });
      assert(!(await pointedAt(tables.nth(0))), "not at the page's older table");
      await page.getByRole("button", { name: "Show me" }).click();
      // First what the user came for: a cell, typed into in place.
      await page.waitForSelector('[data-guide-overlay="table-cell"] .guideCard');
      await until(async () => await pointedAt(tables.nth(1)), { what: "the step points at the new table" });
      await tables.nth(1).locator("td").first().click();
      await page.locator(".mdTableCellInput").fill("42");
      await page.keyboard.press("Enter");
      await until(async () => (await user.api(`/api/blocks/${pg.id}/subtree`)).block.children.find((b) => b.id === grid.id)?.content.includes("42"),
        { what: "the cell edit is saved" });
      await page.waitForSelector('[data-guide-overlay="table-add"] .guideCard');
      const add = page.locator('[data-guide="notes.tableAdd"]').nth(1);
      await until(async () => (await add.getAttribute("data-guide-active")) !== null, { what: "the add strip is marked active" });
      await until(async () => (await add.evaluate((el) => getComputedStyle(el).opacity)) === "1", { what: "the hover-only strip shows" });
      // With no room under the strip (the table at the bottom of the window)
      // the card goes above the whole table or beside it, never onto it.
      const size = page.viewportSize();
      const box = await tables.nth(1).boundingBox();
      await page.setViewportSize({ width: size.width, height: Math.ceil(box.y + box.height + 40) });
      const card = page.locator(".guideCard");
      await until(async () => !/\bside-bottom\b/.test(await card.getAttribute("class")), { what: "the card leaves the crowded space under the strip" });
      const [cb, tb] = [await card.boundingBox(), await tables.nth(1).boundingBox()];
      assert(cb.x >= tb.x + tb.width || cb.x + cb.width <= tb.x || cb.y >= tb.y + tb.height || cb.y + cb.height <= tb.y,
        `the add card keeps clear of the table (${await card.getAttribute("class")})`);
      await page.setViewportSize(size);
      await primary(page).click();
      await page.waitForSelector('[data-guide-overlay="table-whole"]');
      assertEq(await add.getAttribute("data-guide-active"), null, "the previous anchor is unmarked");
      await primary(page).click();
      await until(async () => await page.locator(".guideCard").count() === 0);
      assertEq(await progress(page, "tables"), "done");
      await makeTable(page, "grid here");
      await page.waitForTimeout(1500);
      assertEq(await page.locator("[data-guide-offer]").count(), 0, "offered once per version");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("triggered guide: a pasted spreadsheet offers the table tour too, as html or as cells chosen as Table", async () => {
    const clips = [
      { kind: "html", data: { "text/html": "<table><tr><td>a</td><td>b</td></tr><tr><td>1</td><td>2</td></tr></table>", "text/plain": "a\tb\n1\t2" } },
      { kind: "cells", data: { "text/plain": "a\tb\n1\t2" }, choose: "Table" },
    ];
    for (const clip of clips) {
      const pg = await user.api("/api/pages", { method: "POST", body: { title: `Pasted ${clip.kind}` } });
      await user.api("/api/blocks", { method: "POST", body: { parent_id: pg.id, content: "paste here" } });
      const { ctx, page } = await open(`&page=${pg.id}`);
      try {
        await editRow(page, "paste here");
        await page.keyboard.press("Enter");
        await page.evaluate((data) => {
          const dt = new DataTransfer();
          for (const [type, value] of Object.entries(data)) dt.setData(type, value);
          document.querySelector(".blockEditorCm .cm-content")
            .dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
        }, clip.data);
        if (clip.choose) await page.locator(".slashMenu .slashMenuItem", { hasText: clip.choose }).first().click();
        // (the caret rests after it, so the editor shows it as a table)
        await until(async () => await page.locator(".blockEditorCm .cmTableWidget").count() === 1, { what: `the ${clip.kind} paste became a table` });
        await closeEditor(page);
        await page.waitForSelector('[data-guide-offer="tables"] .guideCard');
        await page.getByRole("button", { name: "Not now" }).click();
        assertNoProblems(page);
      } finally { await ctx.close(); }
    }
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
      await page.locator(".sharePopover").getByRole("button", { name: "Share", exact: true }).click();
      await page.waitForSelector('[data-guide-offer="sharing"] .guideCard');
      await page.locator(".sharePopover").getByRole("button", { name: "Anyone", exact: true }).click();
      await page.locator(".sharePopover").getByText("Anyone with the link can read this page.").waitFor();
      await page.getByRole("button", { name: "Show me" }).click();
      // the popover top to bottom; an anyone-with-the-link share gets the access step that says so
      for (const id of ["share-link", "share-people", "share-access-anyone"]) {
        await page.waitForSelector(`[data-guide-overlay="${id}"] .guideCard`);
        assertEq(await page.locator(".sharePopover").count(), 1, `the share popover stays open at ${id}`);
        if (id === "share-access-anyone") {
          assert((await page.textContent(".guideCard")).includes("no sign-in needed"), "the anyone wording");
          assert((await page.textContent(".guideCard")).includes("Stop sharing"), "the last card names Stop sharing");
        }
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

  await step("triggered guide: from the menu, the sharing tour has the user press Share only when there is no link", async () => {
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
      await page.locator(".sharePopover").getByRole("button", { name: "Share", exact: true }).click();
      await page.waitForSelector('[data-guide-overlay="share-link"] .guideCard');
      assertEq((await user.api(`/api/share-settings/${pg.id}`)).audience, "users", "Share makes a signed-in link");
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
    const { ctx, page } = await open(`&page=${paper.id}`, { seenWindows: true, setup: async (ctx) => {
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
      await a.page.waitForSelector('[data-guide-overlay="presence-undo"] .guideCard');
      // The other person leaves: the last card points at the notes, which
      // stay, so it stays without a warning.
      const warnings = [];
      a.page.on("console", (m) => { if (m.type() === "warning" && m.text().startsWith("guide:")) warnings.push(m.text()); });
      await v.ctx.close();
      await a.page.waitForTimeout(1000);
      assertEq(await a.page.locator('[data-guide-overlay="presence-undo"] .guideCard').count(), 1);
      assertEq(warnings.length, 0, `no anchor warning: ${warnings.join("; ")}`);
      await primary(a.page).click();
      await until(async () => await a.page.locator(".guideCard").count() === 0);
      assertNoProblems(a.page);
    } finally { await a.ctx.close(); await own.ctx.close(); await v?.ctx.close(); }
  });

  await step("triggered guide: the handwriting tour has the user draw first, leads with the note, and ends with the pen armed", async () => {
    const upload = await user.upload("/api/uploads", makePdf([["A page to write on"]]), "ink-tour.pdf", "application/pdf");
    const paper = await user.api(`/api/blocks/by-doc/${upload.doc_id}`, { method: "POST", body: { default_title: "Ink tour paper", source_url: upload.source_url } });
    const { ctx, page } = await open(`&page=${paper.id}`, { seenWindows: true });
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
      assertEq(await page.locator(".guideSegments i").count(), 4, "four steps from the menu");
      await page.click('[data-guide="ink.eraser"]');
      await line([box.x + 180, box.y + 120], [box.x + 180, box.y + 200]);
      assertEq(await page.locator('[data-guide-overlay="ink-erase"] .guideCard').count(), 1, "erasing does not end the last card");
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

  // The guides that draw: a step's `media` is the registry's drawing inlined
  // into the card, so what this checks in a real browser is that the file is
  // bundled, drawn with media.css's classes and there before the card is
  // placed (docs/dev/onboarding.md, "Illustrations").
  // steps: [step id, the drawing it must carry, or null for a card of words].
  const walk = async (page, steps, { open = null } = {}) => {
    for (const [id, media] of steps) {
      const card = `[data-guide-overlay="${id}"] .guideCard`;
      // One selector for the step and its drawing together: a step that
      // passed over while we looked cannot read as a step that drew nothing.
      await page.waitForSelector(media ? `${card} .guideMedia[data-media="${media}"] svg` : card);
      if (open) assertEq(await page.locator(open).count(), 1, `${open} stays open at ${id}`);
      if (!media) assertEq(await page.locator(`${card} .guideMedia`).count(), 0, `${id}: no drawing asked for`);
      await primary(page).click();
    }
    await until(async () => await page.locator(".guideCard").count() === 0);
  };

  await step("triggered guide: opening Add offers its tour, whose cards draw and keep the popover open", async () => {
    const { ctx, page } = await open("", { seen: ["first-run", "library", "folders", "quick-open", "workspaces"] });
    try {
      // Nothing is suggested until the account's synced profile has loaded,
      // so an Add opened in that first moment is not offered anything —
      // closing and opening it again emits `popover.opened` afresh.
      let offered = false;
      for (let tries = 0; tries < 5 && !offered; tries++) {
        await page.click('[data-guide="header.add"]');
        await page.waitForSelector(".addPopover");
        offered = await page.locator('[data-guide-offer="add-paper"] .guideCard')
          .waitFor({ timeout: 3000 }).then(() => true, () => false);
        if (!offered) await page.click('[data-guide="header.add"]'); // close, then round again
      }
      assert(offered, "the Add tour was never offered");
      assertEq(await page.locator(".guideDim").count(), 0, "an offer never dims the app");
      assertEq(await page.locator(".addPopover").count(), 1, "the offer does not close the popover it points into");
      await page.getByRole("button", { name: "Show me" }).click();
      // The popover is the spotlight's surface: undimmed whole, its rows
      // clickable around the address box the ring marks.
      await page.waitForSelector('[data-guide-overlay="add-url"] .guideCard');
      const row = await page.locator('[data-guide="add.upload"]').boundingBox();
      assertEq(await page.evaluate(([x, y]) => !!document.elementFromPoint(x, y)?.closest('[data-guide="add.upload"]'),
        [row.x + row.width / 2, row.y + row.height / 2]), true, "the popover's other rows are not under the sheet");
      // Its scene types into the field and leaves it as it was.
      await page.waitForSelector('[data-guide-scene="add-url"] .guideSceneCaret', { state: "attached", timeout: 8000 });
      assertEq(await page.inputValue('[data-guide="add.urlInput"]'), "", "the scene types nothing for real");
      await walk(page, [["add-url", null], ["add-upload", null], ["add-notebook", "page-notebook"]], { open: ".addPopover" });
      assertEq(await progress(page, "add-paper"), "done");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("triggered guide: a sheet of paper offers the notebook tour in the notes view", async () => {
    const pg = await user.api("/api/pages", { method: "POST", body: { title: "Notebook page" } });
    await user.api("/api/blocks", { method: "POST", body: { parent_id: pg.id, content: "A note" } });
    const { ctx, page } = await open(`&page=${pg.id}`, { seen: ["add-paper", "tables", "workspaces"] });
    try {
      // "/note" in an empty block of its own: the block becomes a sheet.
      await editRow(page, "A note");
      await page.keyboard.press("Enter");
      await page.keyboard.type("/note");
      await page.locator(".slashMenu .slashMenuItem", { hasText: "Handwritten note" }).click();
      await page.waitForSelector('[data-guide="notes.sheet"]');
      await page.waitForSelector('[data-guide-offer="notebook"] .guideCard');
      await page.getByRole("button", { name: "Show me" }).click();
      // nb-make is dropped from the run: the sheet it would ask for is there.
      await walk(page, [["nb-grow", null], ["nb-pen", null], ["nb-view", "page-notebook"]]);
      assertEq(await progress(page, "notebook"), "done");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("triggered guide: the PDF viewer tour walks the left edge; the notebook view's then leaves out what they share", async () => {
    const upload = await user.upload("/api/uploads", makePdf([["Viewer tools", "A page to zoom."]]), "viewer.pdf", "application/pdf");
    const paper = await user.api(`/api/blocks/by-doc/${upload.doc_id}`, { method: "POST", body: { default_title: "Viewer paper", source_url: upload.source_url } });
    const nb = await user.api("/api/pages", { method: "POST", body: { title: "Viewer notebook" } });
    await user.api("/api/blocks", { method: "POST", body: { parent_id: nb.id, content: "", properties: {
      sheet: { width: 595.28, height: 841.89, color: "#ffffff", pattern: "blank", spacing: 24, line: "#c8d1dc" }, collapsed: true } } });
    const { ctx, page } = await open(`&page=${paper.id}`, { seenWindows: true, seen: ["add-paper", "notebook", "workspaces"] });
    try {
      await waitForPdf(page);
      // Both viewers' columns start with the same run, the pen at its end.
      const column = await page.locator('[data-guide="viewer.tools"] button').evaluateAll((els) => els.map((el) => el.getAttribute("aria-label")));
      assertEq(column.slice(0, 4).join("|"), "Zoom out|Zoom in|Fit to width|Handwriting tools", "the shared run leads the PDF's column");
      await page.click('[data-guide="header.account"]');
      await page.click('[data-guide="account.tour"]');
      const tours = page.getByRole("menu", { name: "Tours" });
      assertEq(await tours.locator('[data-tour="notebook-view"]').count(), 0, "the notebook view's tour is not listed on a PDF");
      // Listed though its first step, the table of contents, has nothing to
      // point at in this PDF: that step passes over.
      await page.click('[data-tour="pdf-viewer"]');
      await walk(page, [["viewer-zoom", null], ["viewer-pen", null], ["pdf-translate", null]]);
      assertEq(await progress(page, "pdf-viewer"), "done");
      await page.goto(`${server.base}/?ws=${user.ws}&page=${nb.id}`);
      await page.locator('[data-guide="sheet.notebookView"]').first().click();
      await page.waitForSelector('[data-guide-offer="notebook-view"] .guideCard', { timeout: 15000 });
      assertEq(await page.locator(".guideCard .guideStep").textContent(), "Quick tour · 2 steps", "zoom and the pen are not taught twice");
      const nbColumn = await page.locator('[data-guide="viewer.tools"] button').evaluateAll((els) => els.map((el) => el.getAttribute("aria-label")));
      assertEq(nbColumn.slice(0, 4).join("|"), column.slice(0, 4).join("|"), "the same buttons in the same places");
      await page.getByRole("button", { name: "Show me" }).click();
      await page.waitForSelector('[data-guide-overlay="nbv-paper"] .guideCard');
      await primary(page).click();
      await page.waitForSelector('[data-guide-overlay="nbv-notes"] .guideCard .guideMedia[data-media="page-notebook"] svg');
      // Doing what the last card points at leaves the notebook view, and
      // with it the tour, which has been shown whole.
      await page.locator('[data-guide="viewer.notesView"]').click();
      await until(async () => await page.locator(".guideCard").count() === 0);
      await until(async () => await progress(page, "notebook-view") === "done", { what: "leaving on the last card finishes the tour" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("triggered guide: a library past 20 pages offers the organizing tour, passing over the strips it has not got", async () => {
    server.manage("create-user", "filer", "filer-pw");
    const big = await new Account(server, "filer", "filer-pw").login();
    const attention = await big.folder("ml/attention");
    for (let i = 0; i < 20; i++) {
      await big.api("/api/pages", { method: "POST", body: { title: `Filed paper ${i}`, ...(i ? {} : { folders: [attention] }) } });
    }
    const ctx = await big.context(browser, { suggestTours: true });
    const page = await openPage(ctx, `${server.base}/?ws=${big.ws}`);
    try {
      await page.waitForSelector('[data-guide-offer="library"] .guideCard', { timeout: 15000 });
      await page.getByRole("button", { name: "Show me" }).click();
      await page.waitForSelector('[data-guide-overlay="lib-menu"] .guideCard');
      // Its scene right-clicks a card: the mouse badge shows the button.
      await page.waitForSelector('[data-guide-scene="lib-menu"] .guideMouse', { state: "attached", timeout: 8000 });
      await primary(page).click();
      // The model step is a centred card with no anchor, so it is the one
      // step of this tour that can never be passed over.
      await page.waitForSelector('[data-guide-overlay="lib-model"] .guideCard.guideCardCentered .guideMedia[data-media="labels-folders"] svg');
      assert((await page.textContent(".guideCard")).includes("never copies"), "what a folder actually is");
      await primary(page).click();
      // Nothing was viewed yet, so the recents step passes over on its own.
      await page.waitForSelector('[data-guide-overlay="lib-trash"] .guideCard', { timeout: 15000 });
      await primary(page).click();
      await until(async () => await progress(page, "library", "filer") === "done", { what: "the tour recorded as done" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("triggered guide: the export hint comes with the menu that holds Export, on a page worth exporting", async () => {
    const upload = await user.upload("/api/uploads", makePdf([["Annotated", "A line to mark."]]), "annotated.pdf", "application/pdf");
    const paper = await user.api(`/api/blocks/by-doc/${upload.doc_id}`, { method: "POST", body: { default_title: "Annotated paper", source_url: upload.source_url } });
    // `annotatedPage` counts what blocksToHighlights returns, which needs a
    // place on the page on each block, not just its page.
    for (let i = 0; i < 5; i++) {
      const rect = { x1: 60, y1: 60 + i * 20, x2: 480, y2: 78 + i * 20 };
      await user.api("/api/blocks", { method: "POST", body: { parent_id: paper.id, content: `Marked ${i}`,
        properties: { quote: "A line to mark.", color: "rgba(255, 229, 100, 0.55)",
          pdf_position: { pageNumber: 1, width: 612, height: 792, boundingRect: rect, rects: [rect] } } } });
    }
    const { ctx, page } = await open(`&page=${paper.id}`, { seenWindows: true, seen: ["add-paper", "notebook", "workspaces"] });
    try {
      await waitForPdf(page);
      await page.click('[data-guide="header.view"]');
      await page.waitForSelector('[data-guide-offer="export-page"] .guideCard');
      assertEq(await page.locator(".guideDim").count(), 0, "a hint never dims the app");
      assertEq(await page.locator(".menuPopover").count(), 1, "the menu it points into stays open");
      assertEq(await page.locator(".guideCard .guideMedia").count(), 0, "a hint is a card of words");
      await page.getByRole("button", { name: "Got it" }).click();
      await until(async () => await page.locator(".guideCard").count() === 0);
      assertEq(await progress(page, "export-page"), "done");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("triggered guide: Suggest tours off in Settings stops offers on every device", async () => {
    const pg = await user.api("/api/pages", { method: "POST", body: { title: "Quiet table" } });
    await user.api("/api/blocks", { method: "POST", body: { parent_id: pg.id, content: "quiet grid" } });
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
    // Another browser still holds "on" locally; the account's "off" wins
    // there too, so making a table offers nothing.
    const { ctx, page } = await open(`&page=${pg.id}`, { setup: (ctx) => ctx.addInitScript(() => localStorage.removeItem("gamma-guide:tourist:tables")) });
    try {
      await makeTable(page, "quiet grid");
      await page.waitForTimeout(1500);
      assertEq(await page.locator("[data-guide-offer]").count(), 0, "no offer while suggestions are off");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });
}
