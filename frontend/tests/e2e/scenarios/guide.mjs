// The first-run guide (docs/dev/onboarding.md): the account menu starts a tour, every
// registered anchor for the home view is in the DOM, the demo step adds a
// paper by itself (click Add, type the link, Enter — against an uploaded PDF
// so no network is needed), the user's highlight checks the next step off,
// finishing records "done", the account menu restarts it, and Esc leaves a
// replay with its temporary selection cleared.
import { ANCHORS, anchorsForView } from "../../../src/guide/anchors.js";
import { selectPdfText, waitForPdf } from "./pdf.mjs";
import { ABSTRACT_PASSAGE } from "../../../src/guide/previewHighlight.js";
import { readFileSync } from "node:fs";
import { Account } from "../harness.mjs";

export async function guideScenarios(env) {
  const { server, browser, alice, step, until, assert, assertEq, assertNoProblems, openPage, makePdf, flags } = env;
  await step("guide: first-run tour — demo adds a paper, the user highlights, menu restarts", async () => {
    const pdf = process.env.GAMMA_GUIDE_PDF ? readFileSync(process.env.GAMMA_GUIDE_PDF) : makePdf([[
      "Attention is all you need, said the transformer.",
      "We propose a new simple network architecture,",
      "the Transformer, based solely on attention",
      "mechanisms, dispensing with recurrence",
      "and convolutions entirely.",
    ], ["Scaled dot-product attention", "Attention(Q, K, V) = softmax(QK / d) V"]]);
    const up = await alice.upload("/api/uploads", pdf, "attention.pdf", "application/pdf");
    const ctx = await alice.context(browser);
    await ctx.addInitScript((url) => localStorage.setItem("gamma-guide-vars", JSON.stringify({ demoUrl: url })), `/api/uploads/${up.doc_id}.pdf`);
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    try {
      await page.click('[data-guide="header.account"]');
      await page.click('[data-guide="account.tour"]');
      await page.click('[data-tour="first-run"]');
      await page.waitForSelector('[data-guide-overlay="welcome"] .guideCard.guideCardWelcome');
      assertEq(await page.locator(".guideCard .guideOutline li").count(), 3, "the welcome card outlines the tour");
      for (const id of anchorsForView("home").filter((id) => !ANCHORS[id].open)) {
        assertEq(await page.locator(`[data-guide="${id}"]`).count(), 1, `anchor ${id} (${ANCHORS[id].description}) present once`);
      }
      await page.click(".guideCard .uiBtn.primary");
      // The demo: Add opens, the link is typed, Enter opens the paper.
      await page.waitForSelector('[data-guide-overlay="add-demo"][data-guide-busy]');
      assertEq(await page.locator(".guideCard .guideCount").textContent(), "Step 1 of 5", "the welcome card is not counted as a step");
      assertEq(await page.locator('.guideCard .guideBtns button').count(), 0, "automatic step has no navigation buttons, including during its initial pause");
      assertEq(await page.locator('.guideCard .guideChip.watch').count(), 1, "a demo says to watch");
      assertEq(await page.locator('.guideCard .guideLink').count(), 0, "the add demo cannot be skipped: every later step needs its paper");
      await page.keyboard.press("ArrowRight");
      assertEq(await page.locator('[data-guide-overlay="add-demo"]').count(), 1, "keyboard cannot skip the automatic step");
      await page.waitForSelector(".guideCursor");
      // The pointer waits below the field, faded, so the typed link stays
      // readable; Enter shows as a key cap beside it.
      await page.waitForSelector(".guideCursor.faded");
      await until(async () => page.evaluate(() => new DOMMatrix(getComputedStyle(document.querySelector(".guideCursor")).transform).f
        >= document.querySelector('[data-guide="add.urlInput"]').getBoundingClientRect().bottom), { what: "the typing pointer is off the text" });
      await until(async () => (await page.inputValue('[data-guide="add.urlInput"]')).endsWith(".pdf"), { what: "the demo typed the link" });
      await page.locator(".guideModifier", { hasText: "Enter" }).waitFor();
      // The highlight step shows the drag first, then hands over.
      await page.waitForSelector('[data-guide-overlay="highlight"][data-guide-busy] .guideCursor.dragging', { timeout: 30000 });
      assertEq(await page.locator('.guideCard .guideLink').textContent(), "Skip this demo", "other demos can be skipped one by one");
      const firstPointer = await page.locator(".guideCursor").getAttribute("style");
      await until(async () => (await page.locator(".guideCursor").getAttribute("style")) !== firstPointer, { what: "the example pointer moves across the text" });
      await page.waitForSelector('[data-guide="pdf.highlightColor"]');
      assertEq((await page.evaluate(() => window.getSelection().toString())).replace(/\s/g, ""), ABSTRACT_PASSAGE.replace(/\s/g, ""), "demo selects the requested abstract across lines");
      if (flags.keep) await page.screenshot({ path: `${server.dir}/highlight-demo.png` });
      await page.waitForSelector('[data-guide-overlay="highlight"]:not([data-guide-busy]) .guideCard', { timeout: 30000 });
      assertEq(await page.evaluate(() => window.getSelection().toString()), "", "example selection clears before handing over");
      // A light step: the user may try it, and Next is there whether or not they do.
      assertEq(await page.locator('.guideCard .guideChip.turn').count(), 0, "a light step does not wait for the user");
      assertEq(await page.locator('.guideCard .uiBtn.primary').textContent(), "Next", "Next stays on a step that invites a try");
      assertEq(await page.locator('.guideCard .guideLink').count(), 0, "nothing to skip on a light step");
      assertEq(await page.locator("[data-hl-id]").count(), 0, "the example creates no saved highlight");
      assert(/[?&]block=/.test(page.url()), "the paper opened");
      await waitForPdf(page, 1);
      // User actions show Done, then advance without a Next click.
      await selectPdfText(page, 1, "We propose");
      await page.locator(".plainTip .colorBtn").first().click();
      await page.locator(".guideCard .guideDone").waitFor();
      assertEq(await page.locator('.guideCard .uiBtn.primary').count(), 0, "Done needs no Next button");
      await page.waitForSelector('[data-guide-overlay="area"][data-guide-busy] .guideCursor.dragging');
      await page.waitForSelector('.pdfAreaMarquee');
      await until(async () => page.evaluate(() => {
        const marquee = document.querySelector('.pdfAreaMarquee');
        const formula = [...(marquee?.parentElement.querySelectorAll('[data-guide="pdf.textLayer"] span') || [])]
          .filter((s) => s.textContent.includes("softmax"))
          .sort((a, b) => a.textContent.length - b.textContent.length)[0];
        if (!formula || !marquee) return false;
        const f = formula.getBoundingClientRect(), m = marquee.getBoundingClientRect();
        return m.left < f.left && m.top < f.top && m.right > f.right && m.bottom > f.bottom;
      }), { what: "rectangle encircles the attention formula" });
      let lastWidth = 0, stableSince = Date.now();
      await until(async () => {
        const box = await page.locator('.pdfAreaMarquee').boundingBox();
        if (!box) return false;
        if (Math.abs(box.width - lastWidth) > 0.5) { lastWidth = box.width; stableSince = Date.now(); }
        return Date.now() - stableSince > 150;
      }, { what: "the formula rectangle finishes growing" });
      assertEq(await page.locator('.guideModifier').textContent(), "Ctrl", "box demonstration shows the modifier");
      if (flags.keep) await page.screenshot({ path: `${server.dir}/area-demo.png` });
      await page.waitForSelector('[data-guide-overlay="area"]:not([data-guide-busy]) .guideCard');
      assertEq(await page.locator('.pdfAreaMarquee').count(), 0, "example rectangle is cleaned up");
      const pdfBox = await page.locator('[data-guide="pdf.page"]').first().boundingBox();
      const viewerBox = await page.locator('[data-guide="pdf.viewer"]').boundingBox();
      const x = Math.max(pdfBox.x, viewerBox.x) + 70;
      const y = Math.max(pdfBox.y, viewerBox.y) + 90;
      await page.keyboard.down("Control");
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(x + 180, y + 95, { steps: 16 });
      await page.mouse.up();
      await page.keyboard.up("Control");
      await page.locator('.plainTip .colorBtn').first().click();
      await page.locator('.guideCard .guideDone').waitFor();
      await page.waitForSelector('[data-guide-overlay="note-label"] .guideCard');
      assertEq(await page.locator('[data-guide="dock.notes"]').count(), 1, "the notes window is anchored");
      await page.waitForSelector('[data-guide="notes.editor"]');
      await until(async () => (await page.locator('[data-guide="notes.editor"]').allTextContents()).some((t) => t.includes("Attention compares queries")), { what: "notes demo types real text" });
      const paperId = new URL(page.url()).searchParams.get("block");
      await page.waitForSelector('[data-guide-overlay="home"] .guideCard', { timeout: 20000 });
      // The label field made the llm label (a block of the label tree) and
      // filed the page under it, once.
      const saved = await until(async () => {
        const { block } = await alice.api(`/api/blocks/${paperId}/subtree`);
        const llm = (await alice.api("/api/blocks/labels/subtree")).block.children.filter((l) => l.content === "llm");
        return llm.length && block.properties.labels?.includes(llm[0].id) && block.children.some((b) => b.content.includes("Scaling keeps the scores stable.")) ? { block, llm } : null;
      }, { what: "demo note and llm label persist" });
      assertEq(saved.llm.length, 1, "llm is made once");
      assertEq(saved.block.properties.labels.filter((id) => id === saved.llm[0].id).length, 1, "llm is added once");
      await page.click('[data-guide="header.home"]');
      await page.locator('.guideCard .guideDone').waitFor();
      assert(!new URL(page.url()).searchParams.has("block"), "final step returns to the homepage");
      // The finish card: what the tour made, and what to try next.
      await page.waitForSelector('[data-guide-finish="first-run"] .guideCardFinish');
      assertEq(await page.evaluate(() => JSON.parse(localStorage.getItem("gamma-guide:first-run")).state), "done", "done is recorded when the finish card shows");
      const made = await page.locator(".guideMade li").allTextContents();
      assertEq(made.length, 4, `the paper, the highlights, the note and the label: ${made.join(" | ")}`);
      assert(made[1].includes("2 highlights"), `both highlights are counted: ${made[1]}`);
      if (flags.keep) await page.screenshot({ path: `${server.dir}/finish.png` });
      await page.locator('.guideNext [data-finish="tours"]').click();
      await page.waitForSelector('.userPopover [data-tour="ai-chat"]');
      assertEq(await page.locator(".guideCard").count(), 0, "a tile closes the finish card");
      assertEq(await page.evaluate(() => JSON.parse(localStorage.getItem("gamma-guide:first-run")).state), "done", "closing it keeps done");
      await page.goto(`${server.base}/?ws=${alice.ws}&block=${paperId}`);
      await page.waitForSelector('[data-guide="pdf.textLayer"] span:visible');
      // Manual re-entry through the Tours submenu.
      await page.click('[data-guide="header.account"]');
      await page.click('[data-guide="account.tour"]');
      await page.click('[data-tour="first-run"]');
      await page.waitForSelector('[data-guide-overlay="welcome"] .guideCard');
      await until(async () => await page.locator(".userPopover").count() === 0);
      // Replaying with the same PDF already open must still complete.
      await page.click(".guideCard .uiBtn.primary");
      await page.waitForSelector('[data-guide-overlay="highlight"][data-guide-busy] .guideCursor.dragging', { timeout: 30000 });
      assertEq(new URL(page.url()).searchParams.get("block"), paperId, "replay reuses the open paper");
      await page.keyboard.press("Escape");
      await until(async () => await page.locator(".guideCard").count() === 0);
      assertEq(await page.evaluate(() => window.getSelection().toString()), "", "dismissing a drag clears its temporary selection");
      await until(async () => await page.locator(".plainTip").count() === 0);
      // Without an override the demo paper is the seeded Welcome page's own
      // PDF (a new account starts with it): no resolver, no network.
      const welcome = (await alice.api("/api/blocks/root/children")).children.find((b) => b.properties?.seeded === "welcome");
      assert(welcome?.properties?.doc_id, "a new account starts with the Welcome page and its PDF");
      await page.goto(`${server.base}/?ws=${alice.ws}`);
      await page.click('[data-guide="header.account"]');
      await page.click('[data-guide="account.tour"]');
      await page.click('[data-tour="first-run"]');
      await page.waitForSelector('[data-guide-overlay="welcome"] .guideCard');
      await page.evaluate(() => localStorage.removeItem("gamma-guide-vars"));
      const resolves = [];
      const trackResolve = (request) => { if (request.url().includes("/api/resolve")) resolves.push(request.url()); };
      page.on("request", trackResolve);
      await page.click(".guideCard .uiBtn.primary");
      await page.waitForSelector('[data-guide-overlay="highlight"] .guideCard', { timeout: 30000 });
      assertEq(new URL(page.url()).searchParams.get("block"), welcome.id, "the demo opens the Welcome page's PDF");
      assertEq(resolves.length, 0, "the Welcome PDF needs no resolver");
      page.off("request", trackResolve);
      await page.keyboard.press("Escape");
      await until(async () => await page.locator(".guideCard").count() === 0);
      // Once the Welcome page is gone the demo paper is arXiv's again: a
      // known arXiv paper opens its local copy without contacting the
      // resolver or creating a second page, even from the library.
      await page.goto(`${server.base}/?ws=${alice.ws}`); // off the page before it goes
      await alice.api(`/api/blocks/${welcome.id}`, { method: "DELETE" });
      const { block } = await alice.api(`/api/blocks/${paperId}/subtree`);
      await alice.api(`/api/blocks/${paperId}`, { method: "PUT", body: {
        properties: { ...block.properties, meta: { ...block.properties.meta, arxiv_id: "1706.03762" } },
      } });
      await page.goto(`${server.base}/?ws=${alice.ws}`);
      await page.click('[data-guide="header.account"]');
      await page.click('[data-guide="account.tour"]');
      await page.click('[data-tour="first-run"]');
      await page.waitForSelector('[data-guide-overlay="welcome"] .guideCard');
      await page.evaluate(() => localStorage.removeItem("gamma-guide-vars"));
      const unexpected = [];
      const track = (request) => {
        if (request.url().includes("/api/resolve") || (request.method() === "POST" && /\/api\/(pages(?:\?|$)|blocks\/by-doc\/)/.test(request.url()))) unexpected.push(request.url());
      };
      page.on("request", track);
      await page.click(".guideCard .uiBtn.primary");
      await page.waitForSelector('[data-guide-overlay="highlight"][data-guide-busy] .guideCursor.dragging', { timeout: 30000 });
      await page.locator(".guideCard .guideLink", { hasText: "Skip this demo" }).click();
      await page.waitForSelector('[data-guide-overlay="highlight"]:not([data-guide-busy]) .guideCard');
      assertEq(await page.evaluate(() => window.getSelection().toString()), "", "skipping a demo clears its temporary selection");
      assertEq(new URL(page.url()).searchParams.get("block"), paperId, "arXiv demo opens the saved library copy");
      assertEq(unexpected.length, 0, "known paper needs no resolver or page creation");
      await page.waitForSelector('[data-guide="pdf.textLayer"] span:visible');
      page.off("request", track);
      await page.keyboard.press("Escape");
      await page.click('[data-guide="header.account"]');
      await page.click('[data-guide="account.tour"]');
      await page.click('[data-tour="first-run"]');
      await page.click(".guideCard .uiBtn.primary");
      await page.waitForSelector('[data-guide="add.urlInput"]:focus');
      await page.keyboard.press("Escape");
      await until(async () => await page.locator(".guideCard").count() === 0);
      // the Welcome page's tab outlived the page deleted behind the app's back
      assertNoProblems(page, [new RegExp(`/api/blocks/${welcome.id}/subtree -> 404`)]);
    } finally { await ctx.close(); }
  });

  await step("guide: a new account's empty library is offered the first tour as its welcome card", async () => {
    server.manage("create-user", "newcomer", "newcomer-pw");
    const newcomer = await new Account(server, "newcomer", "newcomer-pw").login();
    const ctx = await newcomer.context(browser, { suggestTours: true });
    const page = await openPage(ctx, `${server.base}/?ws=${newcomer.ws}`);
    try {
      await page.waitForSelector('[data-guide-offer="first-run"] .guideCard.guideCardWelcome', { timeout: 15000 });
      assertEq(await page.locator(".guideDim").count(), 0, "the arrival offer dims nothing");
      assertEq(await page.locator(".guideCard .guideTitle").textContent(), "Welcome to Gamma");
      await page.getByRole("button", { name: "Explore on my own", exact: true }).click();
      await until(async () => await page.locator(".guideCard").count() === 0);
      assertEq(await page.evaluate(() => JSON.parse(localStorage.getItem("gamma-guide:first-run")).state), "dismissed");
      await page.reload();
      await page.waitForSelector('[data-guide="header.home"]');
      await page.waitForTimeout(4000);
      assertEq(await page.locator("[data-guide-offer]").count(), 0, "offered once per version");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });
}
