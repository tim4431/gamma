import { Account } from "../harness.mjs";

export async function mentionScenarios(env) {
  const { server, browser, step, openPage, assert, assertEq, assertNoProblems, until, flags, makePdf } = env;
  if (flags.only && !"mentions".includes(flags.only)) return;
  server.manage("create-user", "mentions-user", "mentions-pw");
  const user = await new Account(server, "mentions-user", "mentions-pw").login();
  const papers = [];
  for (const title of ["Cavity readout", "Cavity sensors", "Quantum correction", "Atomic clocks", "Photon counting", "Spin transport", "Thermal noise"]) {
    papers.push(await user.api("/api/blocks", { method: "POST", body: {
      parent_id: "root", content: title, properties: { meta: { authors: ["Ada One"], year: "2024" } },
    } }));
  }
  const setup = async (opts, pageId = papers[2].id) => {
    const ctx = await user.context(browser, opts);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    const page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${user.ws}`);
    const requests = [];
    await page.route("**/api/ai/chat", async (route) => {
      requests.push(route.request().postDataJSON());
      await route.fulfill({ contentType: "application/x-ndjson", body: '{"delta":"References received."}\n' });
    });
    const input = page.getByRole("combobox", { name: "Message AI" });
    await input.waitFor();
    await page.waitForLoadState("networkidle");
    return { ctx, page, input, requests };
  };

  await step("mentions: flat references fit long titles and open their paper", async () => {
    const title = "Real-time quantum error correction beyond break-even";
    const source = await user.api("/api/pages", { method: "POST", body: { title: "Reference style check" } });
    const paper = await user.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: title } });
    const { ctx, page, input, requests } = await setup({}, source.id);
    try {
      await input.fill("@Real-time quantum");
      await page.getByRole("option", { name: new RegExp(title) }).click();
      await input.press("Enter");
      await until(() => requests.length === 1);
      const reference = page.locator(".chatBubble.user .chatMsgPdfs .crumbBtn").last();
      await reference.waitFor();
      for (const theme of ["light", "dark"]) {
        await page.evaluate((theme) => document.documentElement.setAttribute("data-theme", theme), theme);
        for (const selector of [".chatReferenceChip", ".chatReferenceChip .crumbBtn", ".chatBubble.user .chatMsgPdfs .crumbBtn"]) {
          const style = await page.locator(selector).last().evaluate((el) => {
            const css = getComputedStyle(el);
            return { border: css.borderTopWidth, background: css.backgroundColor,
              fits: el.scrollWidth <= el.clientWidth + 1 };
          });
          assertEq(style.border, "0px", `${selector} has no frame`);
          assertEq(style.background, "rgba(0, 0, 0, 0)", `${selector} has no filled badge`);
          assert(style.fits, `${selector} truncates long titles without overflowing`);
        }
        if (process.env.GAMMA_MENTIONS_SCREENSHOT) await page.locator(".chatPanel").screenshot({ path: `${process.env.GAMMA_MENTIONS_SCREENSHOT}-${theme}.png` });
      }
      await page.getByRole("button", { name: `Remove ${title} from context` }).click();
      assertEq(await page.locator(".chatReferenceChip").count(), 0);
      await reference.focus();
      await page.keyboard.press("Enter");
      await until(() => new URL(page.url()).searchParams.get("block") === paper.id);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("mentions: clearing a reference returns the composer to one line", async () => {
    const { ctx, page, input } = await setup();
    try {
      await input.fill("@Cavity readout");
      await page.getByRole("option", { name: /Cavity readout/ }).click();
      await input.fill("A question\nwith several lines\nabout the paper");
      const expanded = (await input.boundingBox()).height;
      await input.fill("");
      await page.getByRole("button", { name: "Remove Cavity readout from context" }).click();
      const size = await input.evaluate((el) => {
        const css = getComputedStyle(el);
        return { height: el.getBoundingClientRect().height,
          oneLine: parseFloat(css.lineHeight) + parseFloat(css.paddingTop) + parseFloat(css.paddingBottom)
            + parseFloat(css.borderTopWidth) + parseFloat(css.borderBottomWidth) };
      });
      assert(expanded > size.oneLine * 1.5, "actual multiline text still expands");
      assert(size.height <= size.oneLine + 1, `empty composer should be one line: ${JSON.stringify(size)}`);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("mentions: keyboard search, context persistence, removal, and replay", async () => {
    const { ctx, page, input, requests } = await setup();
    try {
      assert((await input.getAttribute("placeholder")).includes("@"));
      await input.fill("Compare @cavtiy readout");
      await page.getByRole("option", { name: /Cavity readout/ }).waitFor();
      assert((await page.getByRole("option").first().innerText()).includes("Ada One"));
      await input.press("Enter");
      assertEq(requests.length, 0, "choosing a mention does not send the message");
      assertEq(await input.inputValue(), "Compare @“Cavity readout” ");
      await input.press("Enter");
      await until(() => requests.length === 1);
      assertEq(requests[0].pages.join(","), [papers[2].id, papers[0].id].join(","));
      assertEq(requests[0].attach_pdf, false, "notes pages are not PDF attachments");
      await until(async () => (await user.api(`/api/chats/${papers[2].id}`)).messages?.length === 2);
      await page.reload();
      await page.getByRole("button", { name: "Remove Cavity readout from context" }).waitFor();
      await input.fill("What about the methods?");
      await input.press("Enter");
      await until(() => requests.length === 2);
      assert(requests[1].pages.includes(papers[0].id), "follow-up retains the reference");
      await page.getByRole("button", { name: "Remove Cavity readout from context" }).click();
      await input.fill("Only the open paper now");
      await input.press("Enter");
      await until(() => requests.length === 3);
      assertEq(requests[2].pages.length, 0);
      await page.getByTitle("Edit and re-send (removes later messages)").first().click();
      await page.locator(".chatEditTextarea").fill("Compare the results again");
      await page.locator(".chatEditTextarea").press("Enter");
      await until(() => requests.length === 4);
      assert(requests[3].pages.includes(papers[0].id), "editing reuses that message's references");
      assertEq(requests[3].history.length, 0);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("mentions: dismissal, empty results, duplicate references, and six-page limit", async () => {
    const { ctx, page, input, requests } = await setup();
    try {
      await input.fill("@cavity");
      await page.getByRole("listbox", { name: "Library pages" }).waitFor();
      await input.press("Escape");
      await input.press("End");
      await input.press("Space");
      assertEq(await page.getByRole("listbox").count(), 0, "Escape stays dismissed while continuing prose");
      await input.fill("");
      await input.fill("@no-such-paper-xyz");
      await page.getByText("No matching pages. Try another title.").waitFor();
      await input.press("Enter");
      assertEq(requests.length, 0);
      for (const paper of papers.slice(0, 6)) {
        await input.fill(`@${paper.content}`);
        await page.getByRole("option", { name: new RegExp(paper.content) }).click();
      }
      assertEq(await page.locator(".chatReferenceChip").count(), 6);
      await input.fill("@Thermal noise");
      const disabled = page.getByRole("option", { name: /Thermal noise/ });
      assertEq(await disabled.getAttribute("aria-disabled"), "true");
      await input.press("Enter");
      assertEq(requests.length, 0);
      await input.fill("@Cavity readout");
      await input.press("Tab");
      assertEq(await page.locator(".chatReferenceChip").count(), 6);
      assertEq(await input.inputValue(), "@“Cavity readout” ");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("mentions: touch selection keeps the picker visible until a paper is chosen", async () => {
    const { ctx, page, input } = await setup({ hasTouch: true });
    try {
      await input.fill("@cavity sensors");
      await page.getByRole("option", { name: /Cavity sensors/ }).waitFor();
      await page.getByRole("option", { name: /Cavity sensors/ }).tap();
      assertEq(await input.inputValue(), "@“Cavity sensors” ");
      await page.getByRole("button", { name: "Remove Cavity sensors from context" }).waitFor();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("mentions: PDF receipts include the open paper and persist across reload", async () => {
    const pdfs = [];
    for (const title of ["Current PDF", "Reference PDF"]) {
      const upload = await user.upload("/api/uploads", makePdf([[title]]), `${title}.pdf`, "application/pdf");
      const paper = await user.api(`/api/blocks/by-doc/${upload.doc_id}`, { method: "POST", body: {
        default_title: title, source_url: upload.source_url,
      } });
      pdfs.push({ id: paper.id, doc: upload.doc_id, title });
    }
    const { ctx, page, input, requests } = await setup({}, pdfs[0].id);
    try {
      await input.fill("@Reference PDF");
      await page.getByRole("option", { name: /Reference PDF/ }).click();
      await input.fill("Compare with @Cavity sensors");
      await page.getByRole("option", { name: /Cavity sensors/ }).click();
      await input.press("Enter");
      await until(() => requests.length === 1);
      assertEq(requests[0].attach_pdf, true);
      let saved;
      await until(async () => {
        saved = await user.api(`/api/chats/${pdfs[0].id}`);
        return saved.messages?.length === 2;
      });
      assertEq(saved.messages[0].pdfs.join(","), pdfs.map((p) => p.title).join(","));
      assertEq(saved.messages[0].pdfDocs.join(","), pdfs.map((p) => p.doc).join(","));
      await page.reload();
      await page.getByRole("button", { name: "Remove Reference PDF from context" }).waitFor();
      await input.fill("A follow-up");
      await input.press("Enter");
      await until(() => requests.length === 2);
      assertEq(requests[1].attach_pdf, false, "reload does not silently resend PDF files");
      assert(requests[1].pages.includes(pdfs[1].id));
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("mentions: selected note text rides with the message as its exact source range", async () => {
    // The same sentence twice, the second one bold: the selection must name
    // the SECOND one's range in the markdown, not rendered text.
    const src = "Noise is low. **Noise is low.** End.";
    const notes = await user.api("/api/pages", { method: "POST", body: { title: "Selection check" } });
    const block = await user.api("/api/blocks", { method: "POST", body: { parent_id: notes.id, content: src } });
    const { ctx, page, input, requests } = await setup({}, notes.id);
    try {
      const rendered = page.locator(`[data-block-id="${block.id}"] .blockRendered`);
      await rendered.waitFor();
      // Ctrl+drag across the bold sentence on the rendered view → a Note chip.
      const span = await rendered.locator("strong").evaluate((el) => {
        const r = document.createRange(), t = el.firstChild;
        r.setStart(t, 0); r.setEnd(t, 1);
        const a = r.getBoundingClientRect();
        r.setStart(t, t.data.length - 1); r.setEnd(t, t.data.length);
        const b = r.getBoundingClientRect();
        return { x0: a.left + 1, x1: b.right - 1, y: (a.top + a.bottom) / 2 };
      });
      await page.keyboard.down("Control");
      await page.mouse.move(span.x0, span.y);
      await page.mouse.down();
      await page.mouse.move(span.x1, span.y, { steps: 5 });
      await page.mouse.up();
      await page.keyboard.up("Control");
      const noteChip = page.locator(".chatSelChips .isNote");
      await noteChip.waitFor({ timeout: 5000 });
      assertEq(await page.locator(".blockEditorCm").count(), 0, "a Ctrl+drag never opens the editor");

      // A plain drag opens the editor and keeps selecting in the raw source:
      // from the start of "End." to past the line's end.
      const end = await rendered.evaluate((el) => {
        const t = el.querySelector("p").lastChild;
        const r = document.createRange();
        r.setStart(t, t.data.indexOf("End")); r.setEnd(t, t.data.indexOf("End") + 1);
        const a = r.getBoundingClientRect();
        return { x: a.left + 1, y: (a.top + a.bottom) / 2, right: el.getBoundingClientRect().right - 4 };
      });
      await page.mouse.move(end.x, end.y);
      await page.mouse.down();
      await page.waitForSelector(".blockEditorCm .cm-content", { timeout: 5000 });
      await page.mouse.move(end.right, end.y, { steps: 5 });
      await page.mouse.up();
      // The Cursor chip turns into a Selection chip holding just the selection.
      const selChip = page.locator(".chatSelChips .isCursor", { hasText: "Selection" });
      await selChip.waitFor({ timeout: 5000 });
      const chipText = await selChip.textContent();
      assert(chipText.includes("End.") && !chipText.includes("Noise"), `selection chip: ${chipText}`);

      await input.click(); // closes the editor; the selection chip stays
      await selChip.waitFor({ timeout: 5000 });
      await input.fill("make it louder");
      await input.press("Enter");
      await until(() => requests.length === 1);
      const bold = src.indexOf("Noise", 5);
      assertEq(JSON.stringify(requests[0].note_selections), JSON.stringify([
        { block_id: block.id, from: src.length - 4, to: src.length, text: "End." },
        { block_id: block.id, from: bold, to: bold + 13, text: "Noise is low." },
      ]), "both selections as exact source ranges, the editor's first");
      assertEq(requests[0].focus_block_id, block.id);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });
}
