// PDF pages: upload + page creation by attachment, the viewer's render and
// text layer, creating a highlight from a text selection (overlay + block
// row + persisted position), the library card, and the search panel hitting
// PDF text on another page.
import { tree, same, partialBlockSearch } from "./notes.mjs";
import { fakeAiModels } from "../harness.mjs";

// Select `needle` inside one text-layer span of `pageNo` and release the
// mouse the way the viewer listens for it (document-level mouseup).
export async function selectPdfText(page, pageNo, needle) {
  const ok = await page.evaluate(([pageNo, needle]) => {
    const spans = Array.from(document.querySelectorAll(`[data-page="${pageNo}"] .textLayer span`));
    const span = spans.find((s) => s.textContent.includes(needle));
    if (!span || !span.firstChild) return false;
    const off = span.textContent.indexOf(needle);
    const range = document.createRange();
    range.setStart(span.firstChild, off);
    range.setEnd(span.firstChild, off + needle.length);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    return true;
  }, [pageNo, needle]);
  if (!ok) throw new Error(`text "${needle}" not found on page ${pageNo}'s text layer`);
}

export async function waitForPdf(page, pageNo = 1) {
  await page.waitForSelector(`[data-page="${pageNo}"] .textLayer span`, { timeout: 30000 });
}

export async function pdfScenarios({ server, browser, alice, makePdf, step, until, sleep, assert, assertEq, assertNoProblems, openPage }, { alice2, second }) {
  let ctx, page, pageId, docId;
  const account = alice2;

  await step("pdf: upload, create the page by attachment, the viewer renders both pages", async () => {
    const pdf = makePdf([["Quantum entanglement in Rydberg arrays", "Second line of page one"], ["Page two says hello world"]]);
    const up = await account.upload("/api/uploads", pdf, "rydberg.pdf", "application/pdf");
    docId = up.doc_id;
    const created = await account.api(`/api/blocks/by-doc/${docId}`, { method: "POST", body: { default_title: "Rydberg paper", source_url: up.source_url } });
    pageId = created.id;
    ctx = await account.context(browser);
    await fakeAiModels(ctx); // the chat steps below send (their /api/ai/chat is faked)
    page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${account.ws}`);
    await waitForPdf(page, 1);
    await until(async () => (await page.$$("[data-page]")).length >= 2, { what: "two page wrappers" });
    const text = await page.textContent(`[data-page="1"] .textLayer`);
    assert(text.includes("Rydberg"), `text layer: ${text.slice(0, 80)}`);
    assertNoProblems(page);
  });

  // An AI record no registry confirmed carries the red "!" until the user
  // verifies it in the metadata popover; the source file is a read-only row.
  await step("pdf: Verify vouches for an unverified metadata record; the source file is a table row", async () => {
    const meta = { title: "Quantum entanglement in Rydberg arrays", authors: ["A. Writer"], year: "2026", source: "ai", unverified: true };
    await account.api(`/api/blocks/${pageId}`, { method: "PUT", body: { properties: { meta, bibtex: "@article{writer2026,\n  title = {Quantum entanglement in Rydberg arrays}\n}" } } });
    await page.reload();
    await waitForPdf(page, 1);
    await page.locator(".metaWarnDot").waitFor();
    await page.click("button[aria-label='Paper metadata']");
    const pop = page.locator(".metaPopover");
    const row = (key) => pop.locator(".metaRow").filter({ has: page.locator(".metaKey").getByText(key, { exact: true }) });
    assertEq(await row("Source").locator(".metaValWarn").innerText(), "AI-extracted — unverified");
    const path = await row("Source file").locator(".metaPath").innerText();
    assert(path.startsWith("/api/uploads/") && path.includes(docId), `source file row: ${path}`);
    assertEq(await pop.locator("input:not(.metaInput)").count(), 0, "the path is not an edit box");
    await row("Source").getByRole("button", { name: "Verify", exact: true }).click();
    await until(async () => (await account.api(`/api/blocks/${pageId}`)).properties.meta.user_verified === true, { what: "the verified mark saved" });
    await until(async () => (await row("Source").innerText()).includes("AI-extracted — verified by hand"), { what: "the Source row says verified" });
    assertEq(await page.locator(".metaWarnDot").count(), 0, "the red ! is gone");
    assertEq(await row("Source").getByRole("button").count(), 0, "nothing left to verify");
    await page.click("button[aria-label='Paper metadata']");
    // The later steps expect a page without metadata.
    await account.api("/api/metadata/update", { method: "POST", body: { block_id: pageId, meta: {} } });
    assertNoProblems(page);
  });

  // The spans must not inherit the interface language: pdf.js measures them
  // in the PDF's language, and a Chinese <html lang> resolves the generic
  // font families to CJK fonts — spans (and every highlight made from a
  // selection) came out wider than the glyphs they cover.
  await step("pdf: the text layer's geometry does not follow the interface language", async () => {
    const line = "Quantum entanglement in Rydberg arrays";
    const measure = (p) => p.evaluate((line) => {
      const span = [...document.querySelectorAll('[data-page="1"] .textLayer span')].find((s) => s.textContent === line);
      return span && span.getBoundingClientRect().width / span.closest("[data-page]").getBoundingClientRect().width;
    }, line);
    const english = await until(() => measure(page), { what: "the English span" });
    const zhCtx = await account.context(browser, { locale: "zh-CN" });
    try {
      const zh = await openPage(zhCtx, `${server.base}/?page=${pageId}&ws=${account.ws}`);
      await waitForPdf(zh, 1);
      assertEq(await zh.evaluate(() => document.documentElement.lang), "zh-CN", "the interface is Chinese");
      const chinese = await until(() => measure(zh), { what: "the Chinese-interface span" });
      assert(Math.abs(chinese / english - 1) < 0.01, `span width ${chinese.toFixed(4)} under zh-CN vs ${english.toFixed(4)} under en`);
      assertNoProblems(zh);
    } finally { await zhCtx.close(); }
  });

  await step("pdf: a text selection makes a highlight (overlay + note row), persisted with its position", async () => {
    await selectPdfText(page, 1, "entanglement in Rydberg");
    await page.waitForSelector(".plainTip .colorBtn", { timeout: 5000 });
    await page.locator(".plainTip .colorBtn").first().click();
    await page.waitForSelector('[data-page="1"] [data-hl-id]', { timeout: 5000 });
    await page.waitForSelector(".blockRow .blockQuote", { timeout: 5000 });
    const quote = await page.textContent(".blockRow .blockQuote");
    assert(quote.includes("entanglement in Rydberg"), `quote: ${quote}`);
    const saved = await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      return (d.block.children || []).find((b) => b.properties?.pdf_position) || null;
    }, { what: "highlight block with pdf_position" });
    // The block is the highlight: no second id, the page and its size once.
    const { pdf_position: pos, ...rest } = saved.properties;
    assert(!("highlight_id" in rest) && !("pdf_page" in rest), `no highlight_id or pdf_page: ${JSON.stringify(rest)}`);
    assertEq(pos.pageNumber, 1, "highlight page");
    assert(pos.width > 0 && pos.height > 0, "the page size the rects are measured in");
    assert(pos.rects.length && pos.rects.every((r) => JSON.stringify(Object.keys(r)) === '["x1","y1","x2","y2"]')
      && JSON.stringify(Object.keys(pos.boundingRect)) === '["x1","y1","x2","y2"]', `rects carry no size or page: ${JSON.stringify(pos)}`);
    assertNoProblems(page);
    await page.reload();
    await waitForPdf(page, 1);
    await page.waitForSelector('[data-page="1"] [data-hl-id]', { timeout: 10000 });
    assertNoProblems(page);
  });

  await step("pdf: clicking the overlay focuses its note row; the row's dot jumps back and the highlight pulses once", async () => {
    await page.click('[data-page="1"] [data-hl-id]');
    await until(async () => (await page.$(".blockRow.focused .blockQuote")) != null, { what: "focused highlight row" });
    await page.click(".blockRow.focused .highlightDotBtn");
    await page.waitForSelector('[data-page="1"] [data-hl-id].pdfHlFlash', { timeout: 3000 });
    await page.waitForSelector('[data-page="1"] [data-hl-id].pdfHlFlash', { state: "detached", timeout: 5000 });
    assertNoProblems(page);
  });

  // With the notes window closed the new highlight's row can't render. The
  // jump to it used to keep "unfolding" the tree as a load, so the highlight
  // was never sent, and the next edit was refused for naming a block the
  // server didn't have.
  await step("pdf: a highlight made with the notes window closed is saved, and so is the next", async () => {
    const toggleNotes = async () => {
      await page.getByRole("button", { name: "View", exact: true }).click();
      await page.locator(".menuPopover").getByRole("button", { name: "Notes" }).click();
      await page.keyboard.press("Escape");
    };
    const highlight = async (needle) => {
      await selectPdfText(page, 1, needle);
      await page.waitForSelector(".plainTip .colorBtn", { timeout: 5000 });
      await page.locator(".plainTip .colorBtn").first().click();
    };
    const saved = (needle) => until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      return (d.block.children || []).some((b) => b.properties?.quote === needle);
    }, { what: `highlight "${needle}" saved` });
    await toggleNotes();
    await highlight("Quantum");
    await saved("Quantum");
    await toggleNotes();
    await page.locator(".blockRow .blockQuote", { hasText: "Quantum" }).waitFor();
    await highlight("Second line");
    await saved("Second line");
    // A collapsed notes window (double-click on its grip) hides the rows the
    // same way; the highlight must still reach the server.
    const grip = page.locator(".dockWindow .dockGrip", { hasText: "Notes" });
    await grip.dblclick();
    await page.locator(".dockWindow.collapsed .dockGrip", { hasText: "Notes" }).waitFor();
    await highlight("arrays");
    await saved("arrays");
    await grip.dblclick();
    await page.locator(".blockRow .blockQuote", { hasText: "arrays" }).waitFor();
    assertNoProblems(page);
  });

  await step("pdf: a highlight with a note shows a badge that opens the note", async () => {
    const data = await account.api(`/api/blocks/${pageId}/subtree`);
    const highlight = data.block.children.find((block) => block.properties?.pdf_position);
    await account.api(`/api/blocks/${highlight.id}`, { method: "PUT", body: { content: "A note on this passage" } });
    await page.reload();
    await waitForPdf(page);
    const badge = page.locator(".pdfNoteBadge").first();
    await badge.waitFor();
    await badge.click();
    await until(async () => (await page.$(".blockRow.focused .blockQuote")) != null, { what: "badge focuses its note" });
    assertNoProblems(page);
  });

  await step("pdf: flipped pages draw palette highlights in a dark-tuned set; other colours keep screening", async () => {
    const mark = page.locator('[data-page="1"] div[data-hl-id]').first();
    const look = () => mark.evaluate((el) => { const s = getComputedStyle(el); return `${s.backgroundColor} ${s.mixBlendMode}`; });
    assertEq(await mark.getAttribute("data-hl-color"), "0", "the first palette colour is tagged by its index");
    assert((await look()).endsWith(" multiply"), "a light page multiplies");
    const flip = (on) => page.locator(".pdfViewer").evaluate((el, on) => el.classList.toggle("pdfDark", on), on);
    await flip(true);
    try {
      assertEq(await look(), "rgba(250, 204, 21, 0.3) normal", "the dark-tuned yellow, not screened");
      assertEq(await page.locator(".pdfNoteBadge").first().evaluate((el) => getComputedStyle(el).backgroundColor), "rgb(42, 42, 42)", "a dark note badge");
      // A colour from another app has no palette index and keeps the screen blend.
      await mark.evaluate((el) => { el.removeAttribute("data-hl-color"); el.style.setProperty("--hl", "rgba(255, 0, 0, 0.4)"); });
      assertEq(await look(), "rgba(255, 0, 0, 0.4) screen");
    } finally { await flip(false); }
    await page.reload();
    await waitForPdf(page);
    assertNoProblems(page);
  });

  await step("pdf: interface scale keeps note badges anchored and Tours consistent", async () => {
    // Sizes are compared with interface size 100% (measured first), not pinned in pixels.
    let base;
    for (const scale of [1, 0.7, 1.6]) {
      await page.evaluate((value) => localStorage.setItem("gamma-ui-scale", String(value)), scale);
      await page.reload();
      await waitForPdf(page);
      const badge = page.getByRole("button", { name: "Show highlight note", exact: true }).first();
      await badge.waitFor();
      const measure = async () => badge.evaluate((el) => {
        const box = el.getBoundingClientRect();
        const marks = [...el.closest("[data-page]").querySelectorAll("div[data-hl-id]")]
          .filter((mark) => mark.dataset.hlId === el.dataset.hlId)
          .map((mark) => mark.getBoundingClientRect());
        const end = marks.sort((a, b) => b.top - a.top || b.right - a.right)[0];
        return { width: box.width, dx: box.left - end.right, dy: box.top - end.top };
      });
      // at the passage's end, just after it; zooming the PDF moves it along, same size and offset
      const before = await until(async () => {
        const m = await measure();
        return m.dx >= 0 && m.dx < 6 * scale && Math.abs(m.dy) < 16 * scale ? m : null;
      }, { what: `badge sits at the passage end with interface scale ${scale}` });
      await page.getByRole("button", { name: "Zoom in", exact: true }).click();
      await until(async () => {
        const m = await measure();
        return Math.abs(m.width - before.width) < 1 && Math.abs(m.dx - before.dx) < 1 && Math.abs(m.dy - before.dy) < 1;
      }, { what: `badge keeps its place and size when the PDF zooms (interface scale ${scale})` });
      await page.getByRole("button", { name: "Account & settings", exact: true }).click();
      const settings = page.getByRole("button", { name: "Settings…", exact: true });
      const tours = page.locator('[data-guide="account.tour"]');
      const settingsBox = await settings.boundingBox();
      const toursBox = await tours.boundingBox();
      assert(Math.abs(settingsBox.height - toursBox.height) < 1, "Tours matches adjacent menu controls");
      await tours.click();
      const tour = page.getByRole("menuitem", { name: "Your first paper", exact: true });
      assert(Math.abs((await tour.boundingBox()).height - toursBox.height) < 1, "submenu scales once");
      await settings.click();
      const font = await page.getByText("Interface size", { exact: true }).evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
      base ||= { font, badge: before.width };
      assert(Math.abs(font / base.font - scale) < 0.02, `ordinary settings text follows interface size (${font} at ${scale})`);
      assert(Math.abs(before.width / base.badge - scale) < 0.08, `the badge follows interface size (${before.width} at ${scale})`);
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      assertNoProblems(page);
    }
    await page.evaluate(() => localStorage.removeItem("gamma-ui-scale"));
    await page.reload();
    await waitForPdf(page);
  });

  await step("pdf: search finds PDF text on page 2, the details list it, clicking marks it", async () => {
    await page.click("button[aria-label='Search']");
    await page.waitForSelector(".searchPopover .searchInput");
    await page.fill(".searchPopover .searchInput", "hello world");
    // The paper view opens the compact find bar: a match count, no rows.
    await until(async () => (await page.textContent(".searchPopover .searchFindCount").catch(() => "")) === "1/1", { what: "find count 1/1" });
    await page.click("button[aria-label='Toggle result details']");
    const hit = page.locator("button.searchResult", { hasText: "p. 2" }).first();
    try { await hit.waitFor({ timeout: 10000 }); }
    catch (e) { throw new Error(`no "p. 2" hit; popover showed: ${JSON.stringify(await page.textContent(".searchPopover"))}`); }
    await hit.click();
    await page.waitForSelector('[data-page="2"] .pdfFindMark', { timeout: 15000 });
    await page.keyboard.press("Escape");
    assertNoProblems(page);
  });

  // /block-search stops scanning at its time budget and answers `partial`:
  // the notes group says the list is incomplete instead of "No matches.".
  await step("pdf: a notes search the server cut short says so in the notes group", async () => {
    const unroute = await partialBlockSearch(page);
    try {
      await page.click("button[aria-label='Search']");
      await page.waitForSelector(".searchPopover .searchInput");
      const details = page.locator("button[aria-label='Toggle result details']");
      if (!/\bon\b/.test(await details.getAttribute("class"))) await details.click();
      const answered = page.waitForResponse((r) => r.url().includes("/api/block-search?q=zebrafish"));
      await page.fill(".searchPopover .searchInput", "zebrafish");
      await answered;
      const notice = page.locator(".searchPopover .searchHint", { hasText: "Stopped early — refine the search to see more." });
      await notice.waitFor();
      await until(async () => (await page.locator(".searchPopover .searchHint", { hasText: "Searching…" }).count()) === 0, { what: "the search settles" });
      assertEq(await page.locator(".searchPopover .searchSection", { hasText: "Other notes" }).count(), 1, "under the notes group");
      assertEq(await page.locator(".searchPopover .searchHint", { hasText: "No matches." }).count(), 0, "an incomplete search claims no \"No matches.\"");
      await page.keyboard.press("Escape");
      assertNoProblems(page);
    } finally { await unroute(); }
  });

  await step("pdf: AI citation aligns with text and dismisses on outside clicks without creating a note", async () => {
    const before = await account.api(`/api/blocks/${pageId}/subtree`);
    const quote = "Page two says hello world";
    const href = `/?page=${pageId}&pdf_page=2&quote=${encodeURIComponent(quote)}`;
    await account.api(`/api/chats/${pageId}`, { method: "PUT", body: {
      messages: [{ role: "user", text: "Where is the greeting?" },
        { role: "ai", text: `The greeting is here [p. 2](${href}).` }],
    } });
    await page.reload();
    const link = page.locator('a.gammaLink-citation', { hasText: "p. 2" });
    await link.waitFor();
    assert((await link.getAttribute("href")).includes("quote="), "saved citation retains its quote");
    await link.click();
    const mark = page.locator('[data-page="2"] .pdfCitationMark').first();
    await mark.waitFor();
    const aligned = () => page.evaluate(() => {
      const mark = document.querySelector('[data-page="2"] .pdfCitationMark').getBoundingClientRect();
      const span = [...document.querySelectorAll('[data-page="2"] .textLayer span')]
        .find(s => s.textContent.includes("Page two says hello world"));
      const range = document.createRange(); range.selectNodeContents(span);
      const text = range.getBoundingClientRect();
      return Math.abs(mark.left - text.left) < 3 && Math.abs(mark.top - text.top) < 3
        && Math.abs(mark.width - text.width) < 3 && mark.top >= 0 && mark.top < innerHeight;
    });
    await until(aligned, { what: "citation rectangles align with the rendered text" });
    const box = await mark.boundingBox();
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    assertEq(await mark.count(), 1, "clicking the highlighted passage keeps it visible");
    const paper = await page.locator('[data-page="2"]').boundingBox();
    await page.mouse.click(paper.x + 8, box.y + box.height / 2);
    await until(async () => await mark.count() === 0, { what: "clicking elsewhere on the PDF dismisses the citation" });
    await link.click();
    await mark.waitFor();
    await page.locator(".chatInputArea").click();
    await until(async () => await mark.count() === 0, { what: "clicking in chat dismisses the citation" });
    await link.click();
    await mark.waitFor();
    await page.getByRole("button", { name: "Zoom in", exact: true }).click();
    await until(async () => await mark.count() === 0, { what: "clicking a viewer control dismisses the citation" });
    await link.click();
    await mark.waitFor();
    await until(aligned, { what: "citation aligns after zoom and reopening" });
    await page.keyboard.press("Escape");
    await until(async () => await page.locator('.pdfCitationMark').count() === 0, { what: "citation dismissed" });
    await link.click();
    await mark.waitFor();
    const after = await account.api(`/api/blocks/${pageId}/subtree`);
    assertEq(JSON.stringify(after.block.children), JSON.stringify(before.block.children), "citation creates no annotations or notes");
    // Direct links (including Ctrl/Cmd-click) restore the passage on load.
    await page.goto(`${server.base}${href}&ws=${account.ws}`);
    await mark.waitFor();
    await until(aligned, { what: "direct citation link resolves on initial load" });
    assertNoProblems(page);
  });

  await step("pdf: a citation pasted into a note is the chat's pill, previews its passage and opens it in place", async () => {
    const quote = "Page two says hello world";
    const note = await account.api("/api/blocks", { method: "POST", body: {
      parent_id: pageId,
      // As pasted from a chat answer (relative), and as copied from a server
      // this library no longer lives on (absolute, another host).
      content: `See [p. 2](/?page=${pageId}&pdf_page=2&quote=${encodeURIComponent(quote)})`
        + ` and [moved](https://old-host.invalid:9001/?page=${pageId}&pdf_page=2&quote=${encodeURIComponent(quote)})`,
    } });
    await page.reload();
    await waitForPdf(page);
    const pill = page.locator(`.blockRow a.gammaLink-citation`, { hasText: "p. 2" });
    await pill.waitFor();
    assertEq(await page.locator(".blockRow a.linkChip").count(), 0, "a Gamma link is never an external link chip");
    assertEq(await page.locator(".blockRow a.citePill").count(), 2, "both citations are pills, the moved one too");
    assertEq(await page.locator(".blockRow a.gammaLinkCard").count(), 0, "a citation is not a card");
    await pill.hover();
    const preview = page.locator(".citePreview");
    await preview.waitFor();
    const title = (await account.api(`/api/blocks/${pageId}/subtree`)).block.content;
    assertEq(await preview.locator(".citePreviewTitle").textContent(), title, "the preview names the cited paper");
    assert((await preview.textContent()).includes(quote), "the preview shows the quote");
    await page.mouse.move(0, 0);
    await preview.waitFor({ state: "detached" });
    const url = page.url();
    await pill.click();
    const mark = page.locator('[data-page="2"] .pdfCitationMark').first();
    await mark.waitFor();
    assertEq(page.url(), url, "the citation opens in place, without navigating");
    await page.keyboard.press("Escape");
    await until(async () => await mark.count() === 0, { what: "citation dismissed" });
    // The same link written against the old server still resolves: the id is
    // what identifies the page, not the host.
    await page.locator(".blockRow a.gammaLink-citation", { hasText: "moved" }).click();
    await mark.waitFor();
    await page.keyboard.press("Escape");
    await account.api(`/api/blocks/${note.id}`, { method: "DELETE" });
    assertNoProblems(page);
  });

  await step("pdf: a citation stays clickable when a stream update arrives during a click", async () => {
    await page.reload();
    await waitForPdf(page);
    // Deliver chunks on demand so the regression cannot depend on timing or an AI provider.
    await page.evaluate(() => {
      const originalFetch = window.fetch;
      window.fetch = (input, init) => {
        if (!String(input).endsWith("/api/ai/chat")) return originalFetch(input, init);
        return Promise.resolve(new Response(new ReadableStream({
          start(controller) {
            window.citationStream = {
              delta(text) { controller.enqueue(new TextEncoder().encode(JSON.stringify({ delta: text }) + "\n")); },
              finish() { controller.close(); window.fetch = originalFetch; },
            };
          },
        }), { headers: { "Content-Type": "application/x-ndjson" } }));
      };
    });
    await page.locator("textarea.chatInputArea").fill("Show a citation while streaming");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await page.waitForFunction(() => !!window.citationStream);
    const href = `/?page=${pageId}&pdf_page=2&quote=${encodeURIComponent("Page two says hello world")}`;
    await page.evaluate(href => window.citationStream.delta(`See [streamed passage](${href}).\n\nContinuing`), href);
    const link = page.locator("a.gammaLink-citation", { hasText: "streamed passage" });
    await link.waitFor();
    await link.scrollIntoViewIfNeeded();
    const box = await link.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.evaluate(() => window.citationStream.delta(" the answer while the mouse is held."));
    await page.getByText("Continuing the answer while the mouse is held.", { exact: true }).waitFor();
    await page.mouse.up();
    await page.locator('[data-page="2"] .pdfCitationMark').first().waitFor({ timeout: 5000 });
    assertEq(await page.getByRole("button", { name: "Stop generating", exact: true }).count(), 1, "citation opens before the stream finishes");
    await page.evaluate(() => window.citationStream.delta(" More text after opening the citation."));
    await page.getByText(/More text after opening the citation\./).waitFor();
    assert(await page.locator('[data-page="2"] .pdfCitationMark').count() > 0, "further chunks keep the passage highlighted");
    await page.evaluate(() => window.citationStream.finish());
    await page.getByRole("button", { name: "Stop generating", exact: true }).waitFor({ state: "detached" });
    assertNoProblems(page);
  });

  await step("pdf: a selection goes to the chat with its page and region, and the reply says where it was placed", async () => {
    await page.reload();
    await waitForPdf(page, 2);
    const requests = [];
    // What the server reports it did with the selection (ai_context.selection_context).
    const coverage = { context: [{ title: "Rydberg paper", doc_id: docId, native: false, native_requested: false,
      partial: true, chars: 900, pages: 0, pages_shown: 0,
      selection: { passages: [{ page: 2, section: "Results", found: false, crop: true, box: [0.1, 0.1, 0.6, 0.2] }] } }] };
    await page.route("**/api/ai/chat", async (route) => {
      requests.push(route.request().postDataJSON());
      await route.fulfill({ contentType: "application/x-ndjson",
        body: `${JSON.stringify(coverage)}\n${JSON.stringify({ delta: "It says hello." })}\n` });
    });
    try {
      // The chat takes the selection from a mouseup inside the viewer.
      await page.evaluate(() => {
        const span = [...document.querySelectorAll('[data-page="2"] .textLayer span')]
          .find((s) => s.textContent.includes("says hello"));
        const off = span.textContent.indexOf("says hello");
        const range = document.createRange();
        range.setStart(span.firstChild, off);
        range.setEnd(span.firstChild, off + "says hello".length);
        window.getSelection().removeAllRanges();
        window.getSelection().addRange(range);
        span.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      });
      await page.locator(".chatSelChips").getByText("PDF passage", { exact: true }).waitFor();
      await page.locator(".chatSelChips").getByText("· p. 2", { exact: true }).waitFor();
      assertEq(await page.getByRole("button", { name: "Full PDF" }).count(), 1, "a PDF in context offers the Full PDF switch");
      await page.locator("textarea.chatInputArea").fill("What does this say?");
      await page.getByRole("button", { name: "Send", exact: true }).click();
      await until(() => requests.length === 1, { what: "the chat request" });
      const [sel] = requests[0].selections;
      assertEq(sel.text, "says hello");
      assertEq(sel.page, 2, "the page the selection starts on");
      assert(Array.isArray(sel.box) && sel.box.length === 4 && sel.box[0] < sel.box[2] && sel.box[1] < sel.box[3]
        && sel.box.every((v) => v >= 0 && v <= 1), `box as page fractions: ${JSON.stringify(sel.box)}`);
      await page.getByText("Model saw text around p. 2 · Results").waitFor();
      // Opening the pill shows the picture, drawn again from the saved box.
      await page.getByRole("button", { name: "Picture of the selection sent" }).click();
      const crop = page.locator("img.chatCoverageCrop");
      await crop.waitFor();
      await until(() => crop.evaluate((img) => img.complete && img.naturalWidth > 0), { what: "the selection picture loads" });
    } finally {
      await page.unroute("**/api/ai/chat");
    }
    assertNoProblems(page);
  });

  await step("pdf: a picked picture is stored and attached by URL, and an area drag attaches the region the server draws", async () => {
    const requests = [];
    await page.route("**/api/ai/chat", async (route) => {
      requests.push(route.request().postDataJSON());
      await route.fulfill({ contentType: "application/x-ndjson", body: `${JSON.stringify({ delta: "A picture." })}\n` });
    });
    try {
      // A picked file goes to POST /api/ai/pictures and comes back stored.
      const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");
      await page.locator('.chatComposer input[type="file"]').setInputFiles({ name: "figure.png", mimeType: "image/png", buffer: png });
      const thumbs = page.locator('[data-guide="chat.imageContext"] img');
      await thumbs.first().waitFor();
      assert((await thumbs.first().getAttribute("src")).includes("/api/uploads/"), "the composer shows the stored picture");
      await until(() => thumbs.first().evaluate((img) => img.complete && img.naturalWidth > 0), { what: "the stored picture loads" });
      // Ctrl+drag a rectangle on the page in view (page 2, from the step
      // before): its region goes along, drawn by the server.
      const pageEl = page.locator('[data-page="2"]');
      await pageEl.scrollIntoViewIfNeeded();
      const box = await pageEl.boundingBox();
      const viewport = page.viewportSize();
      const x0 = box.x + 60, y0 = Math.max(box.y + 80, 120);
      assert(y0 + 140 < viewport.height, `page 2 is in view (${JSON.stringify(box)})`);
      await page.keyboard.down("Control");
      await page.mouse.move(x0, y0);
      await page.mouse.down();
      await page.mouse.move(x0 + 100, y0 + 60, { steps: 8 });
      await page.mouse.move(x0 + 200, y0 + 120, { steps: 8 });
      await page.mouse.up();
      await page.keyboard.up("Control");
      await until(async () => await thumbs.count() === 2, { what: "the region joins the pictures" });
      const region = await thumbs.nth(1).getAttribute("src");
      assert(region.includes("/api/ai/page-image/") && region.includes("page=2") && region.includes("box="), `a region URL: ${region}`);
      await until(() => thumbs.nth(1).evaluate((img) => img.complete && img.naturalWidth > 0), { what: "the region picture loads" });
      await page.keyboard.press("Escape");
      await page.locator("textarea.chatInputArea").fill("What are these?");
      await page.getByRole("button", { name: "Send", exact: true }).click();
      await until(() => requests.length === 1, { what: "the chat request" });
      const [sent] = requests;
      assertEq(sent.images.length, 2, "both pictures go with the message");
      assert(sent.images[0].url.startsWith("/api/uploads/") && sent.images[0].kind === "file", JSON.stringify(sent.images[0]));
      assertEq(sent.images[1].kind, "area");
      assertEq(sent.images[1].page, 2);
      assert(Array.isArray(sent.images[1].box) && sent.images[1].box.length === 4, "the region's box");
      assertEq(sent.max_pictures, 12, "the default picture budget");
      // The sent message shows both, and the composer row is empty again.
      await until(async () => await page.locator(".chatBubble.user img.chatMsgImage").count() === 2, { what: "the bubble shows the pictures" });
      assertEq(await thumbs.count(), 0, "the composer's pictures went with the message");
    } finally {
      await page.unroute("**/api/ai/chat");
    }
    assertNoProblems(page);
  });

  await step("pdf: chat paper recommendations and bare identifiers open clickable source links", async () => {
    const doi = "https://doi.org/10.1103/PhysRevA.69.062320";
    await account.api(`/api/chats/${pageId}`, { method: "PUT", body: { messages: [
      { role: "ai", text: `[Cavity quantum electrodynamics](${doi})\n\nDOI: **10.1103/PhysRevA.69.062320**\n\narXiv:cond-mat/0402216` },
    ] } });
    await page.reload();
    const title = page.locator("a.gammaLinkCard", { hasText: "Cavity quantum electrodynamics" });
    await title.waitFor();
    assertEq(await title.getAttribute("href"), doi, "paper title links to the search result's DOI");
    const identifier = page.locator("strong a.gammaLinkCard", { hasText: "10.1103/PhysRevA.69.062320" });
    assertEq(await identifier.getAttribute("href"), doi, "saved bare DOI is clickable");
    assertEq(await page.locator("a.gammaLinkCard", { hasText: "arXiv:cond-mat/0402216" }).getAttribute("href"),
      "https://arxiv.org/abs/cond-mat/0402216", "legacy arXiv identifier is clickable");
    // Verify actual navigation without contacting the publisher.
    const chatUrl = page.url();
    await ctx.route(doi, route => route.fulfill({ contentType: "text/html", body: "<p>Paper source</p>" }));
    try {
      const [source] = await Promise.all([page.waitForEvent("popup"), identifier.click()]);
      await source.waitForLoadState();
      assertEq(source.url(), doi, "source opens in a new tab");
      assertEq(page.url(), chatUrl, "chat remains on the library page");
      await source.close();
    } finally {
      await ctx.unroute(doi);
    }
    assertNoProblems(page);
  });

  await step("pdf: citations open another document and report missing or ambiguous quotes", async () => {
    const quote = "A distinct source sentence in another document.";
    const uploaded = await account.upload("/api/uploads", makePdf([["Repeated source passage.", quote, "Repeated source passage."]]), "citation.pdf", "application/pdf");
    const target = await account.api(`/api/blocks/by-doc/${uploaded.doc_id}`, { method: "POST", body: { default_title: "Citation source", source_url: uploaded.source_url } });
    const href = `/?page=${target.id}&pdf_page=1&quote=${encodeURIComponent(quote)}`;
    await account.api(`/api/chats/${pageId}`, { method: "PUT", body: { messages: [
      { role: "ai", text: `[other source](${href})` },
    ] } });
    await page.reload();
    await page.locator("a.gammaLink-citation", { hasText: "other source" }).click();
    await page.waitForSelector('[data-page="1"] .pdfCitationMark');
    assert((await page.textContent('[data-page="1"] .textLayer')).includes(quote), "citation resolved against the requested document");
    for (const [text, message] of [["This passage does not exist.", "could not be located"], ["Repeated source passage.", "More than one passage"]]) {
      await page.goto(`${server.base}/?page=${target.id}&pdf_page=1&quote=${encodeURIComponent(text)}&ws=${account.ws}`);
      await page.locator('.pdfCitationNotice', { hasText: message }).waitFor();
      assertEq(await page.locator('.pdfCitationMark').count(), 0, "unresolved reference does not highlight guessed text");
      await page.locator(".chatInputArea").click();
      await until(async () => await page.locator('.pdfCitationNotice').count() === 0, { what: "clicking elsewhere dismisses an unresolved reference" });
    }
    assertNoProblems(page);
    await page.goto(`${server.base}/?page=${pageId}&ws=${account.ws}`);
    await waitForPdf(page);
  });

  await step("pdf: fuzzy citations align with source glyphs and reject changed numbers or competing passages", async () => {
    const text = "We carefully measured 53 atoms under stable experimental conditions";
    const quote = text.replace("carefully", "carefuly");
    const pdf = makePdf([{ lines: [text], box: [900, 792] }, { lines: [text, text], box: [900, 792] }]);
    const up = await account.upload("/api/uploads", pdf, "fuzzy-citation.pdf", "application/pdf");
    const target = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Fuzzy citation source", source_url: up.source_url } });
    const before = await account.api(`/api/blocks/${target.id}/subtree`);
    const href = (number, value) => `${server.base}/?page=${target.id}&pdf_page=${number}&quote=${encodeURIComponent(value)}&ws=${account.ws}`;
    await page.goto(href(1, quote));
    await page.locator('.pdfCitationNotice', { hasText: "approximate text match" }).waitFor();
    const aligned = () => page.evaluate(text => {
      const mark = document.querySelector('[data-page="1"] .pdfCitationMark')?.getBoundingClientRect();
      const span = [...document.querySelectorAll('[data-page="1"] .textLayer span')].find(s => s.textContent === text);
      if (!mark || !span) return false;
      const range = document.createRange(); range.selectNodeContents(span);
      const box = range.getBoundingClientRect();
      return ['left', 'top', 'width', 'height'].every(k => Math.abs(mark[k] - box[k]) < 3);
    }, text);
    await until(aligned, { what: "approximate citation covers the actual source glyphs" });
    await page.keyboard.press("Escape");
    await until(async () => await page.locator('.pdfCitationMark, .pdfCitationNotice').count() === 0, { what: "fuzzy citation and notice dismiss together" });
    for (const [number, value, message] of [[1, quote.replace("53", "54"), "could not be located"], [2, quote, "More than one passage"]]) {
      await page.goto(href(number, value));
      await page.locator('.pdfCitationNotice', { hasText: message }).waitFor();
      assertEq(await page.locator('.pdfCitationMark').count(), 0, "unsafe or ambiguous approximate citation has no highlight");
    }
    const after = await account.api(`/api/blocks/${target.id}/subtree`);
    assertEq(JSON.stringify(after.block.children), JSON.stringify(before.block.children), "fuzzy citation creates no annotations or notes");
    assertNoProblems(page);
    await page.goto(`${server.base}/?page=${pageId}&ws=${account.ws}`);
    await waitForPdf(page);
  });

  // The translate endpoint, answered locally: every text comes back as
  // "译:<text>" in one final NDJSON line (a translation service's shape).
  async function mockTranslate(requests) {
    await page.route("**/api/ai/translate", async (route) => {
      const body = route.request().postDataJSON();
      requests.push(body);
      await route.fulfill({ contentType: "application/x-ndjson",
        body: `${JSON.stringify({ translations: body.texts.map((t) => `译:${t}`), model: "engine:google", cached: false })}\n` });
    });
  }

  await step("pdf: the translate button translates the page being read, and shows/hides only on a translated one", async () => {
    await page.reload();
    await waitForPdf(page, 2);
    await sleep(1000); // the saved reading position is restored after the first paint
    const requests = [];
    await mockTranslate(requests);
    const button = page.getByRole("button", { name: "Translate", exact: true });
    // Scroll the visible viewer (another tab's stays mounted, hidden) so
    // `pn` is the page being read.
    const goTo = async (pn) => {
      await page.evaluate((pn) => {
        const v = [...document.querySelectorAll(".pdfViewer")].find((el) => el.offsetParent !== null);
        const el = v.querySelector(`[data-page="${pn}"]`);
        v.scrollTop = pn === 1 ? 0 : el.getBoundingClientRect().top - v.getBoundingClientRect().top + v.scrollTop;
        v.dispatchEvent(new Event("scroll"));
      }, pn);
      await until(() => page.evaluate(() => [...document.querySelectorAll("input[aria-label='Current page']")]
        .find((el) => el.offsetParent !== null)?.value).then((v) => v === String(pn)), { what: `page ${pn} is the one being read` });
      await sleep(300);
      assertEq(await page.evaluate(() => [...document.querySelectorAll("input[aria-label='Current page']")]
        .find((el) => el.offsetParent !== null)?.value), String(pn), "the viewer stays on the page");
    };
    try {
      await goTo(1);
      await button.click();
      await page.locator('[data-page="1"] .pdfTransPara').first().waitFor();
      await page.getByRole("button", { name: "Hide translation", exact: true }).waitFor();
      // Page 2 isn't translated yet: the button offers to translate it.
      await goTo(2);
      await button.click();
      await page.locator('[data-page="2"] .pdfTransPara').first().waitFor();
      assert(requests.some((r) => r.texts.some((t) => t.includes("says hello"))), "page 2's text was sent");
      // On a translated page the button hides every page's translation.
      await page.getByRole("button", { name: "Hide translation", exact: true }).click();
      await page.getByRole("button", { name: "Show translation", exact: true }).waitFor();
      assertNoProblems(page);
    } finally {
      await page.unroute("**/api/ai/translate");
      await page.reload(); // drop the translated view: later steps select PDF text
      await waitForPdf(page, 2);
    }
  });

  await step("pdf: the selection popup translates the selected text, or on select once that is on", async () => {
    const requests = [];
    await mockTranslate(requests);
    const { value: profile } = await account.api("/api/prefs/profile");
    try {
      await selectPdfText(page, 2, "says hello");
      await page.getByRole("button", { name: "Translate selection", exact: true }).click();
      const body = page.locator(".plainTip .selTransBody");
      await until(async () => (await body.textContent()) === "译:says hello", { what: "the translation under the colors" });
      assertEq(JSON.stringify(requests.at(-1).texts), JSON.stringify(["says hello"]));
      // The header folds the result; clicking into the text keeps the popup.
      await page.locator(".selTransToggle").click();
      assertEq(await body.count(), 0);
      await page.locator(".selTransToggle").click();
      await body.click();
      await sleep(150);
      assertEq(await page.locator(".plainTip").count(), 1, "the popup stays while reading the translation");

      // A refusal says why (a used-up shared AI allowance is a 429 whose
      // detail tells what to do): the popup shows the server's reason.
      await page.unroute("**/api/ai/translate");
      const refusal = "Shared AI allowance used up: add your own key under Settings → AI, or come back later.";
      await page.route("**/api/ai/translate", (route) => route.fulfill({ status: 429, json: { detail: refusal } }));
      await selectPdfText(page, 2, "Page two");
      await page.getByRole("button", { name: "Translate selection", exact: true }).click();
      await until(async () => (await body.textContent()) === `Translation failed: ${refusal}`, { what: "the refusal's reason in the popup" });
      assertNoProblems(page, [/429/]);
      await page.unroute("**/api/ai/translate");
      await mockTranslate(requests);

      // Translate on select: the result opens without a click.
      await account.api("/api/prefs/profile", { method: "PUT", body: { value: { ...(profile || {}), selTranslateAuto: true } } });
      await page.reload();
      await waitForPdf(page, 2);
      await until(() => page.evaluate(() => localStorage.getItem("gamma-sel-translate-auto")).then((v) => v === "1" || v === "true"),
        { what: "the profile's translate-on-select" });
      const before = requests.length;
      await selectPdfText(page, 1, "Second line");
      await until(async () => (await page.locator(".plainTip .selTransBody").textContent().catch(() => "")) === "译:Second line",
        { what: "the translation, opened by itself" });
      assertEq(requests.length, before + 1);
      assertNoProblems(page);
    } finally {
      await page.unroute("**/api/ai/translate");
      await account.api("/api/prefs/profile", { method: "PUT", body: { value: { ...(profile || {}), selTranslateAuto: false } } });
      await page.reload();
      await waitForPdf(page);
    }
  });

  // Area-highlight thumbnails are re-cropped from the loaded document. During
  // a paper switch the viewer still holds the previous paper's document (it
  // stays on screen until the new one commits): a crop taken then showed that
  // paper's page under the new paper's note, and stuck — cached by rect alone.
  await step("pdf: a note's area-highlight thumbnail comes from its own paper after switching papers", async () => {
    const rect = { x1: 60, y1: 40, x2: 480, y2: 110 };
    const area = (parent, content) => account.api("/api/blocks", { method: "POST", body: { parent_id: parent, content,
      properties: { quote: "", color: "rgba(255, 229, 100, 0.55)",
        pdf_position: { pageNumber: 2, width: 612, height: 792, boundingRect: rect, rects: [rect], area: true } } } });
    await area(pageId, "rydberg top of page two");
    const other = await account.upload("/api/uploads", makePdf([["Other paper page one"], ["OTHER PAPER TOP LINE"]]), "other.pdf", "application/pdf");
    const otherId = (await account.api(`/api/blocks/by-doc/${other.doc_id}`, { method: "POST", body: { default_title: "Other paper", source_url: other.source_url } })).id;
    await area(otherId, "other top of page two");
    const thumb = (p) => until(async () => (await p.$$eval("img.blockAreaSnap", (imgs) => imgs.map((i) => i.src)))[0] || null,
      { what: "the area thumbnail", timeout: 15000 });
    const switchTo = async (p, title, note) => {
      await p.keyboard.press("Escape"); // nothing else open, focus back on the page
      await p.keyboard.press("Control+p");
      const dialog = p.getByRole("dialog", { name: "Open a page" });
      await dialog.waitFor();
      const input = dialog.getByRole("textbox", { name: "Search pages by title or label" });
      await until(() => input.evaluate((el) => el === document.activeElement), { what: "the palette's input takes focus" });
      await input.fill(title);
      const rows = dialog.locator('[role="option"][data-kind="page"]');
      await until(async () => (await rows.count()) === 1 && new RegExp(title).test(await rows.textContent()), { what: `the "${title}" row` });
      await p.keyboard.press("Enter");
      await p.getByText(note, { exact: true }).waitFor();
    };
    const c = await account.context(browser);
    try {
      // The reference: each paper's thumbnail on a fresh load.
      let p = await openPage(c, `${server.base}/?page=${otherId}&ws=${account.ws}`);
      const otherFresh = await thumb(p);
      await p.close();
      p = await openPage(c, `${server.base}/?page=${pageId}&ws=${account.ws}`);
      const rydberg = await thumb(p);
      assert(rydberg !== otherFresh, "the two papers' crops differ");
      // Switching inside the app: each paper's note shows its own crop.
      await switchTo(p, "Other paper", "other top of page two");
      await until(async () => (await thumb(p)) === otherFresh, { what: "the other paper's own crop", timeout: 15000 });
      await switchTo(p, "Rydberg paper", "rydberg top of page two");
      await until(async () => (await thumb(p)) === rydberg, { what: "the Rydberg paper's own crop", timeout: 15000 });
      assertNoProblems(p);
      await p.close();
    } finally { await c.close(); }
    await account.api(`/api/blocks/${otherId}`, { method: "DELETE" });
  });

  // A highlight's id is its block's: a link region names the highlight it
  // lands on by `link_block_id` (docs/dev/pdf_citations.md).
  await step("pdf: a link region opens the page it links to and lands on the highlight its link_block_id names", async () => {
    const rect = { x1: 60, y1: 40, x2: 480, y2: 60 };
    const at = (pageNumber) => ({ pageNumber, width: 612, height: 792, boundingRect: rect, rects: [rect] });
    const target = await account.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: "the cited passage",
      properties: { quote: "Page two says hello world", color: "rgba(255, 229, 100, 0.55)", pdf_position: at(2) } } });
    const up = await account.upload("/api/uploads", makePdf([["A citing paper", "see the Rydberg paper"]]), "citing.pdf", "application/pdf");
    const citing = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Citing paper" } });
    const link = await account.api("/api/blocks", { method: "POST", body: { parent_id: citing.id, content: "",
      properties: { quote: "see the Rydberg paper", color: "rgba(140, 180, 255, 0.35)", pdf_position: at(1),
        link_url: "", link_page_id: pageId, link_block_id: target.id } } });
    assertEq(link.kind, "link", "a link region's kind");
    const c = await account.context(browser);
    try {
      const p = await openPage(c, `${server.base}/?page=${citing.id}&ws=${account.ws}`);
      await waitForPdf(p, 1);
      await p.click(`[data-page="1"] [data-hl-id="${link.id}"]`);
      await p.waitForSelector(`[data-page="2"] [data-hl-id="${target.id}"].pdfHlFlash`, { timeout: 15000 });
      assertNoProblems(p);
      await p.close();
    } finally { await c.close(); }
    await account.api(`/api/blocks/${target.id}`, { method: "DELETE" });
    await account.api(`/api/blocks/${citing.id}`, { method: "DELETE" });
  });

  if (ctx) await ctx.close();
  return { pdfPageId: pageId, docId };
}
