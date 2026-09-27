// Tours are manual, compact, and never submit a message or activate the mic.
// The AI chat tour has steps per place (a paper, the library), each ending on
// the user's own Send; with no AI connected it points at the setup card.
import { FAKE_AI_MODELS } from "../harness.mjs";

export async function contextualGuideScenarios(env) {
  const { server, browser, alice, step, until, assert, assertEq, assertNoProblems, openPage, makePdf, flags } = env;
  const openTours = async (page, paperId) => {
    await page.click('[data-guide="header.account"]');
    await page.click('[data-guide="account.tour"]');
    const tours = page.getByRole("menu", { name: "Tours" });
    assertEq(await tours.locator('[data-tour="first-run"], [data-tour="ai-chat"]').count(), 2, "submenu lists both tours");
    assertEq(await tours.locator('[data-tour="sharing"]').count(), paperId ? 1 : 0, "sharing is listed on a page, not in the library");
    assertEq(await tours.locator('[data-tour="math-keys"], [data-tour="quick-open"]').count(), 0, "hints are never listed");
    await page.click('[data-tour="ai-chat"]');
  };
  for (const mode of ["library", "pdf", "hidden-pdf", "phone"]) {
    await step(`guide: manual chat tour ${mode}`, async () => {
      const mobile = mode === "phone";
      let paperId;
      if (mode.includes("pdf")) {
        const up = await alice.upload("/api/uploads", makePdf([["A figure can become chat context."]]), "context.pdf", "application/pdf");
        const paper = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Context paper", source_url: up.source_url } });
        paperId = paper.id;
      }
      const ctx = await alice.context(browser, mobile ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } : {});
      await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
      await ctx.route("**/api/ai/models*", (route) => route.fulfill({ json: FAKE_AI_MODELS }));
      let sends = 0;
      await ctx.route("**/api/ai/chat", (route) => { sends++; return route.fulfill({ body: "" }); });
      const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}${paperId ? `&block=${paperId}` : ""}`);
      try {
        await page.waitForLoadState("networkidle");
        if (mobile) await page.getByRole("button", { name: "AI chat", exact: true }).click();
        const input = page.getByRole("combobox", { name: "Message AI" });
        await input.waitFor();
        await input.click();
        await input.fill("Keep this draft");
        assertEq(await page.locator('[data-guide-overlay], [data-guide-offer]').count(), 0, "chat focus never starts a tour");
        if (paperId) await page.waitForSelector('[data-guide="pdf.textLayer"] span');
        if (mode === "hidden-pdf") await page.getByRole("button", { name: "Close PDF", exact: true }).click();
        // The page's chat mounts after the initial library shell.
        await input.fill("Keep this draft");
        const first = paperId ? "chat-question" : "chat-question-library";
        const example = paperId ? "What is the main result, and where is it shown?"
          : "Which of these papers use attention? File them into ML/attention";
        const send = paperId ? "chat-send-paper" : "chat-send-library";
        await openTours(page, paperId);
        await page.waitForSelector(`[data-guide-overlay="${first}"]`);
        await until(async () => await input.inputValue() === example, { what: "the place's example is typed into the composer" });
        if (flags.keep) await page.screenshot({ path: `${server.dir}/chat-${mode}-input.png` });
        if (mode === "pdf") {
          // A box is dragged on the paper; its snapshot is the next step's subject.
          await page.waitForSelector('[data-guide-overlay="chat-figure"] .guideCursor.dragging');
          await page.waitForSelector('[data-guide-overlay="chat-snapshot"]');
          await page.waitForSelector('[data-guide="chat.imageContext"] img');
          assertEq(await page.locator('[data-hl-id]').count(), 0, "context creates no saved annotation");
          if (flags.keep) await page.screenshot({ path: `${server.dir}/chat-pdf-context.png` });
          await page.locator('.guideCard .primary').click();
        } else if (!paperId) {
          // The library's tools step, when its header button is on screen.
          await page.waitForSelector(`[data-guide-overlay="chat-tools"], [data-guide-overlay="${send}"]`);
          if (await page.locator('[data-guide-overlay="chat-tools"]').count()) await page.locator('.guideCard .primary').click();
        }
        // The last step waits for the user's own Send; its link leaves.
        await page.waitForSelector(`[data-guide-overlay="${send}"]`);
        await until(async () => await input.inputValue() === "Keep this draft", { what: "existing draft is restored" });
        assertEq(await page.getByRole("button", { name: "Cancel recording", exact: true }).count(), 0, "tour never records");
        assertEq(await page.locator('[data-guide-overlay="chat-voice"]').count(), 0, "no voice step");
        assertEq(await page.locator('.guideCard .primary').count(), 0, "the send step's action is the user's Send");
        assertEq((await page.locator('.guideCard .guideLink').textContent()).trim(), "Done");
        await page.locator('.guideCard .guideLink').click();
        await until(async () => await page.locator('.guideCard').count() === 0);
        assertEq(sends, 0, "tour sends no AI request");
        if (mode === "pdf") {
          await until(async () => await page.locator('[data-guide="chat.imageContext"] img').count() === 0,
            { what: "the demo's snapshot goes when the tour ends without a send" });
        }
        await openTours(page, paperId);
        await page.waitForSelector(`[data-guide-overlay="${first}"]`);
        await page.keyboard.press("Escape");
        await until(async () => await page.locator('.guideCard').count() === 0);
        assertEq(await input.inputValue(), "Keep this draft", "cancelling preserves the draft");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });
  }

  await step("guide: manual chat tour with no AI connected points at the setup card", async () => {
    const ctx = await alice.context(browser);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    await ctx.route("**/api/ai/models*", (route) => route.fulfill({ json: { enabled: false, models: [], default: "" } }));
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    try {
      await page.locator(".chatSetup").waitFor();
      await openTours(page, null);
      await page.waitForSelector('[data-guide-overlay="chat-setup"]');
      assertEq(await page.locator('.guideCard .primary').count(), 0, "the step waits for a tile");
      // Picking a service opens Settings → Connections, which ends the tour.
      await page.locator(".chatSetup").getByRole("button", { name: /^Anthropic/ }).click();
      await page.getByRole("dialog", { name: "Connect an AI service", exact: true }).waitFor();
      await until(async () => await page.locator('.guideCard').count() === 0, { what: "the tour finished" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });
}
