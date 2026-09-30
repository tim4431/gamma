// Sheets of paper (docs/dev/notebooks.md): a notebook is a page with
// sheets, shown in the notebook view or among its notes. A new notebook
// opens in the notebook view; it is written on with the same ink tools, a
// click on a stroke selects it, a page is added by writing low on the last
// one and by the button, the paper menu sets a page's paper and the page
// added after it takes that paper; the notebook view's side bar switches
// to the notes view, a sheet's own switch back, remembered over a reload;
// the export is a PDF of the sheets, and on a touch screen two fingers
// pinch-zoom and pan the pages. Then pages in a note: "/page",
// written on in place, the replay of the page and of its group's card (on
// the page too), and "Add page below". The
// rules behind it are backend/tests/test_notebooks.py,
// frontend/tests/notebook.test.mjs and ink.test.mjs (the replay's timing).
import { closeEditor, newPageViaUi } from "./notes.mjs";

async function drawLine(page, from, to) {
  await page.mouse.move(from[0], from[1]);
  await page.mouse.down();
  await page.mouse.move(to[0], to[1], { steps: 12 });
  await page.mouse.up();
}

// A stroke written over about `ms`, so its replay lasts long enough to watch.
async function slowLine(page, from, to, ms, sleep) {
  await page.mouse.move(from[0], from[1]);
  await page.mouse.down();
  for (let i = 1; i <= 20; i++) {
    await page.mouse.move(from[0] + (to[0] - from[0]) * i / 20, from[1] + (to[1] - from[1]) * i / 20);
    await sleep(ms / 20);
  }
  await page.mouse.up();
}

export async function notebookScenarios({ server, browser, alice, step, until, sleep, assert, assertEq, assertNoProblems, openPage, flags }) {
  const account = alice;
  let ctx, page, pageId;
  const tree = async () => (await account.api(`/api/blocks/${pageId}/subtree`)).block;
  const sheetsOnServer = async () => ((await tree()).children || []).filter((b) => b.properties?.sheet);
  const sheetBox = (i) => page.locator(".nbSheet").nth(i).boundingBox();
  const pageRow = (n) => page.locator(".sortableBlockWrap", { has: page.locator(".blockSheetLabel", { hasText: `Page ${n}` }) }).first();

  await step("notebook: the add menu makes a page with one sheet, in the notebook view; a stroke on it becomes a group under the sheet", async () => {
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?ws=${account.ws}`);
    await page.click("button[aria-label='Add']");
    await page.getByRole("menuitem", { name: "New notebook" }).or(page.locator("button", { hasText: "New notebook" })).first().click();
    await page.waitForSelector(".nbViewer .nbSheet", { timeout: 15000 });
    await page.keyboard.type("Lecture notes");
    await page.keyboard.press("Enter");
    pageId = await until(async () => {
      const pages = (await account.api("/api/blocks/root/children")).children || [];
      return pages.find((b) => b.content === "Lecture notes")?.id;
    }, { what: "the notebook, named, in the library" });
    assert(page.url().includes(pageId), "the address names the open notebook");
    assertEq((await page.$$(".nbSheet")).length, 1, "one page to start");
    const root = await tree();
    assert(!("notebook" in root.properties), "nothing marks the page as a notebook: it has a sheet");
    const [first] = await sheetsOnServer();
    assert(first?.properties.sheet.width > 0 && first.properties.collapsed === true, "the first sheet, with its paper, folded");
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    const b = await sheetBox(0);
    await drawLine(page, [b.x + 80, b.y + 100], [b.x + 260, b.y + 130]);
    await until(async () => (await page.$$(".nbSheet >> nth=0 >> .inkLayer path")).length === 1, { what: "the stroke on the page" });
    const group = await until(async () => {
      const [sheet] = await sheetsOnServer();
      const g = (sheet?.children || []).find((c) => c.properties?.ink_url);
      return g && g.properties.ink_url.endsWith(".ink") && g.properties.ink_strokes === 1 ? g : null;
    }, { what: "an ink group under the first sheet with its upload" });
    assert(!("pdf_page" in group.properties), "a sheet's group names no PDF page");
    const ink = await account.api(group.properties.ink_url);
    assertEq(ink.space.kind, "canvas", "the file is on the sheet's canvas");
    assertEq(Math.round(ink.space.width), 595, "the canvas is the sheet's width");
    assertNoProblems(page);
  });

  await step("notebook: a click on a stroke selects it and opens its edit menu", async () => {
    // The menu once looked for a PDF viewer around the sheet, found none and
    // took the whole app down.
    await page.getByRole("button", { name: "Hand", exact: true }).click();
    const b = await sheetBox(0);
    await page.mouse.click(b.x + 170, b.y + 115);
    await page.waitForSelector(".inkEditMenu", { timeout: 5000 });
    assert(await page.$(".nbSheet >> nth=0 >> .inkSelRect"), "the stroke is selected");
    assert(await page.$(".blockRow"), "the app is still there");
    await page.keyboard.press("Escape");
    await until(async () => !(await page.$(".inkEditMenu")), { what: "Esc drops the selection" });
    await page.getByRole("button", { name: / pen · .* · key 1\b/ }).click();
    assertNoProblems(page);
  });

  await step("notebook: writing low on the last page adds the next; Add page adds one more", async () => {
    // the sheet is taller than the view at fit-width: bring its foot up first
    await page.evaluate(() => {
      const v = document.querySelector(".nbViewer"), s = v.querySelector(".nbSheet");
      v.scrollTop = s.offsetTop + s.offsetHeight - v.clientHeight + 40;
    });
    let b = await sheetBox(0);
    await drawLine(page, [b.x + 80, b.y + b.height * 0.86], [b.x + 240, b.y + b.height * 0.88]);
    await until(async () => (await page.$$(".nbSheet")).length === 2, { what: "a second page after writing low on the first" });
    await until(async () => (await sheetsOnServer()).length === 2, { what: "two sheets stored" });
    await page.click(".nbAddSheet");
    await until(async () => (await page.$$(".nbSheet")).length === 3, { what: "a third page from the button" });
    await until(async () => (await sheetsOnServer()).length === 3, { what: "three sheets stored" });
    // the notes list the sheets as pages, each folded over its handwriting
    await until(async () => (await page.locator(".blockSheetLabel").allTextContents()).join("|") === "Page 1|Page 2|Page 3",
      { what: "Page 1-3 in the notes" });
    assertEq((await page.$$(".blockInkCard")).length, 0, "page 1 folded over its drawing");
    await pageRow(1).locator(".collapseBtn").click();
    await page.waitForSelector(".blockInkCard", { timeout: 5000 });
    b = await sheetBox(0);
    assert(b.width > 300, "the page fits the viewer's width");
    assertNoProblems(page);
  });

  await step("notebook: the paper menu sets a page's pattern; a page added after it takes its paper", async () => {
    await page.keyboard.press("Escape"); // close the ink strip
    await page.evaluate(() => document.querySelector(".nbViewer").scrollTo({ top: 0 }));
    await page.click("button[aria-label='Paper of this page']");
    await page.waitForSelector(".nbPaperMenu");
    await page.locator(".nbPaperMenu").getByRole("button", { name: "Ruled", exact: true }).click();
    await until(async () => (await sheetsOnServer())[0].properties.sheet.pattern === "ruled", { what: "page 1 ruled on the server" });
    await until(async () => !!(await page.$(".nbSheet >> nth=0 >> .nbPaper path")), { what: "page 1 draws its lines" });
    if (flags.keep) await page.screenshot({ path: `${server.dir}/notebook-paper.png` });
    const first = pageRow(1);
    await first.hover();
    await first.locator(".dragHandle").click();
    await page.locator(".ctxMenu").getByText("Add page below", { exact: true }).click();
    await until(async () => {
      const sheets = await sheetsOnServer();
      return sheets.length === 4 && sheets[1].properties.sheet.pattern === "ruled";
    }, { what: "the page after page 1 takes its paper" });
    assertEq((await sheetsOnServer())[2].properties.sheet.pattern, "blank", "the other pages keep theirs");
    await until(async () => (await page.$$(".nbViewer .nbSheet")).length === 4, { what: "four pages in the viewer" });
    assertNoProblems(page);
  });

  await step("notebook: pages and handwriting survive a reload; the export is a PDF of the pages", async () => {
    await page.reload();
    await page.waitForSelector(".nbViewer .nbSheet", { timeout: 15000 });
    await until(async () => (await page.$$(".nbSheet")).length === 4, { what: "four pages after the reload" });
    await until(async () => (await page.$$(".nbSheet >> nth=0 >> .inkLayer path")).length === 2, { what: "page 1's two strokes" });
    const r = await account.api(`/api/pages/${pageId}/export-pdf`, { raw: true });
    assertEq(r.status, 200, "the export answers");
    assertEq(r.headers.get("content-type"), "application/pdf", "a PDF");
    const bytes = new Uint8Array(await r.arrayBuffer());
    const text = new TextDecoder("latin1").decode(bytes);
    assertEq((text.match(/\/Type\s*\/Page\b/g) || []).length, 4, "one PDF page per sheet");
    assertNoProblems(page);
  });

  await step("notebook: the side bar switches to the notes view, a page's own switch back; the choice outlives a reload", async () => {
    await page.click(".pdfZoomOverlay button[aria-label='Notes view']");
    await until(async () => !(await page.$(".nbViewer")) && (await page.$$(".noteSheet .nbSheet")).length === 4,
      { what: "the notes view draws the four pages among the blocks" });
    await until(async () => (await page.$$(".noteSheet .inkLayer path")).length === 2, { what: "page 1's strokes there too" });
    assertEq((await page.$$(".noteSheetBar button[aria-label='Notebook view']")).length, 4, "each page's tools switch back");
    await page.locator(".noteSheetBar button[aria-label='Notebook view']").first().click();
    await page.waitForSelector(".nbViewer .nbSheet", { timeout: 5000 });
    assertEq((await page.$$(".noteSheet")).length, 0, "the notes list the pages as rows again");
    await page.click("button[aria-label='Back to the notes']");
    await until(async () => !(await page.$(".nbViewer")), { what: "the viewer's close button goes back to the notes" });
    await page.reload();
    await until(async () => (await page.$$(".noteSheet .nbSheet")).length === 4, { what: "the notes view, remembered", timeout: 15000 });
    assertNoProblems(page);
    await ctx.close();
  });

  // A tablet's only zoom gesture: the viewport meta turns the browser's own
  // off (docs/dev/ipad.md), so the notebook view has to run the pinch itself.
  // CDP supplies native Chromium touch gestures.
  if (browser.browserType().name() === "chromium") await step("notebook: two fingers pinch-zoom the pages, hold the paper under them, and pan", async () => {
    const touch = await account.context(browser, { hasTouch: true, isMobile: true, deviceScaleFactor: 2, viewport: { width: 1024, height: 768 } });
    try {
      const tab = await openPage(touch, `${server.base}/?page=${pageId}&ws=${account.ws}`);
      await tab.locator(".noteSheetBar button[aria-label='Notebook view']").first().click();
      await tab.waitForSelector(".nbViewer .nbSheet", { timeout: 15000 });
      const cdp = await touch.newCDPSession(tab);
      // Which paper a view point sits on, and where on it: what a zoom must hold.
      const paperAt = (vx, vy) => tab.evaluate(([vx, vy]) => {
        for (const node of document.querySelectorAll(".nbSheet")) {
          const r = node.getBoundingClientRect();
          if (vy >= r.top && vy <= r.bottom) return { id: node.dataset.sheetId, fy: (vy - r.top) / r.height };
        }
        return null;
      }, [vx, vy]);
      const scale = () => tab.evaluate(() => {
        const sheet = document.querySelector(".nbSheet");
        return sheet.getBoundingClientRect().width / sheet.querySelector(".nbPaper").viewBox.baseVal.width;
      });
      const box = await tab.locator(".nbViewer").boundingBox();
      const cx = Math.round(box.x + box.width / 2), cy = Math.round(box.y + box.height / 2);
      // Halfway down the middle sheet, so the hold has paper on both sides of it.
      await tab.locator(".nbSheet").nth(1).evaluate((el) => {
        const viewer = el.closest(".nbViewer");
        viewer.scrollTop += el.getBoundingClientRect().top - viewer.getBoundingClientRect().top - 80;
      });
      await sleep(200);

      // Two fingers from `from` apart to `to` apart, their midpoint moving by
      // (dx, dy): a spread with no travel is a pinch, travel with no spread a pan.
      const twoFingers = async (from, to, dx = 0, dy = 0) => {
        const pts = (gap, f) => [
          { x: cx + dx * f - gap / 2, y: cy + dy * f, id: 1 },
          { x: cx + dx * f + gap / 2, y: cy + dy * f, id: 2 },
        ];
        await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: pts(from, 0) });
        for (let i = 1; i <= 12; i++) {
          await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: pts(from + (to - from) * i / 12, i / 12) });
          await sleep(16);
        }
        await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
        await sleep(300);
      };

      // Out, then back in: each pinch scales the pages by what the fingers did
      // (up to the zoom limit) and leaves the paper under them where it was.
      for (const [from, to] of [[120, 300], [300, 140]]) {
        const was = await scale(), held = await paperAt(cx, cy);
        await twoFingers(from, to);
        const now = await until(async () => {
          const s = await scale();
          return Math.abs(s - was) > 0.01 ? s : false;
        }, { what: `pages resize for a ${(to / from).toFixed(2)}x pinch` });
        const expected = Math.min(4, Math.max(0.2, was * to / from));
        assert(Math.abs(now - expected) < 0.02, `pinch ${from}->${to}: scale ${now.toFixed(3)} is the fingers' ${expected.toFixed(3)}`);
        const after = await paperAt(cx, cy);
        assert(after && after.id === held.id && Math.abs(after.fy - held.fy) < 0.01,
          `the paper under the fingers is held: ${held.fy.toFixed(3)} -> ${after?.fy.toFixed(3)}`);
      }
      assertEq(await tab.evaluate(() => visualViewport.scale), 1, "the browser's own zoom stays out of it");

      // Two fingers held the same distance apart pan, like the PDF viewer's:
      // the gesture takes the move event, so nothing else can scroll it.
      const zoomed = await scale();
      // Against the room the view actually has: at this zoom the column can be
      // barely wider than the view, and a pan that asks for more is clamped.
      const room = () => tab.locator(".nbViewer").evaluate((el) => ({
        left: el.scrollLeft, top: el.scrollTop,
        maxLeft: el.scrollWidth - el.clientWidth, maxTop: el.scrollHeight - el.clientHeight,
      }));
      const from = await room();
      await twoFingers(200, 200, -120, -150);
      const to = await room();
      const want = [Math.min(from.left + 120, from.maxLeft), Math.min(from.top + 150, from.maxTop)];
      assert(Math.abs(to.left - want[0]) < 3 && Math.abs(to.top - want[1]) < 3,
        `a two-finger drag pans by what the fingers travelled: ${[from.left, from.top]} -> ${[to.left, to.top]}, wanted ${want}`);
      assertEq(await scale(), zoomed, "and does not change the zoom");

      // One finger still scrolls: only the two-finger move is taken.
      const before = await tab.locator(".nbViewer").evaluate((el) => el.scrollTop);
      await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: cx, y: cy }] });
      for (let i = 1; i <= 10; i++) {
        await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: cx, y: cy - 20 * i }] });
        await sleep(16);
      }
      await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      await until(async () => (await tab.locator(".nbViewer").evaluate((el) => el.scrollTop)) > before + 100,
        { what: "a one-finger swipe scrolls the notebook" });
      assertNoProblems(tab);
    } finally {
      await touch.close();
    }
  });

  // --- pages in a note ----------------------------------------------------------------
  let noteId;
  const noteTree = async () => (await account.api(`/api/blocks/${noteId}/subtree`)).block;
  const notePage = (i = 0) => page.locator(".noteSheet .nbSheet").nth(i);

  await step("notes page: /page turns an empty block into a page drawn in place; strokes on it are a group under it", async () => {
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?ws=${account.ws}`);
    noteId = await newPageViaUi(page, "Notes with a page");
    await page.keyboard.type("Before the page");
    await closeEditor(page);
    const first = page.locator(".sortableBlockWrap", { hasText: "Before the page" }).first();
    await first.hover();
    await first.locator(".addHandle").click();
    await page.waitForSelector(".blockEditorCm .cm-content", { timeout: 5000 });
    await page.keyboard.type("/page");
    await page.waitForSelector(".slashMenu");
    assertEq(await page.locator(".slashMenu .slashMenuItem.selected .slashMenuLabel").innerText(), "Page to write on",
      "/page picks the page, not a command that merely mentions pages");
    await page.keyboard.press("Enter");
    await page.waitForSelector(".noteSheet .nbSheet", { timeout: 5000 });
    assertEq((await page.$$(".blockEditorCm")).length, 0, "the block is the page now; its editor closed");
    const sheet = await until(async () => (await noteTree()).children.find((c) => c.properties?.sheet), { what: "the page on the server" });
    assertEq(sheet.properties.sheet.width, 595.28, "A4 when no page stands before it");
    assertEq(sheet.properties.collapsed, true, "a new page starts folded: it shows its drawings itself");
    assert(await page.$("button[aria-label='Notebook view']"), "a page with pages can show them in the notebook view");
    await page.click("button[aria-label='Write on this page']");
    await page.waitForSelector(".notesInkDock .pdfInkBar", { timeout: 5000 });
    await notePage().evaluate((n) => n.scrollIntoView({ block: "start" }));
    const b = await notePage().boundingBox();
    assert(b.width > 300, "the page fits the notes' width");
    await slowLine(page, [b.x + 60, b.y + 80], [b.x + 200, b.y + 120], 700, sleep);
    await slowLine(page, [b.x + 60, b.y + 140], [b.x + 220, b.y + 150], 700, sleep);
    await until(async () => (await page.$$(".noteSheet .inkLayer path")).length === 2, { what: "two strokes on the page" });
    const group = await until(async () => {
      const s = (await noteTree()).children.find((c) => c.properties?.sheet);
      const g = (s?.children || []).find((c) => c.properties?.ink_url);
      return g && g.properties.ink_strokes === 2 ? g : null;
    }, { what: "a group under the page with both strokes" });
    const ink = await account.api(group.properties.ink_url);
    assertEq(ink.space.kind, "canvas", "drawn on the page's canvas");
    assert(ink.strokes.every((s) => Number.isFinite(s.t0) && s.ch.includes("t")), "the strokes keep their timing");
    assertEq((await page.$$(".blockInkCard")).length, 0, "the group's row is folded under the page");
    await page.getByRole("button", { name: "Hand", exact: true }).click();
    await page.mouse.click(b.x + 130, b.y + 100);
    await page.waitForSelector(".inkEditMenu", { timeout: 5000 });
    assert(await page.$(".noteSheet .inkSelRect"), "a click with the hand selects the stroke on the page");
    await page.keyboard.press("Escape");
    await page.keyboard.press("Escape");
    await until(async () => !(await page.$(".notesInkDock")), { what: "Esc closes the strip" });
    assertNoProblems(page);
  });

  await step("notes page: the page and its group's card replay the writing; Add page below; both pages survive a reload", async () => {
    const pageReplay = ".noteSheetBar .inkReplayBtn";
    await page.click(pageReplay);
    assertEq(await page.getAttribute(pageReplay, "aria-pressed"), "true", "the page replays its writing");
    assert((await page.$$(".noteSheet svg.inkLayer path")).length < 2, "stroke by stroke");
    await until(async () => (await page.getAttribute(pageReplay, "aria-pressed")) === "false", { what: "the replay ends", timeout: 20000 });
    assertEq((await page.$$(".noteSheet .inkLayer path")).length, 2, "then the drawing is whole again");
    await page.locator(".sortableBlockWrap", { has: page.locator(".noteSheet") }).locator(".collapseBtn").first().click();
    await page.waitForSelector(".blockInkCard", { timeout: 5000 });
    const cardReplay = ".blockInkCardWrap .inkReplayBtn";
    await page.hover(".blockInkCardWrap");
    await page.click(cardReplay);
    assertEq(await page.getAttribute(cardReplay, "aria-pressed"), "true", "the card replays its group");
    assert((await page.$$(".noteSheet .inkLayer path")).length < 2, "on the page too, stroke by stroke");
    await page.click(cardReplay);
    assertEq(await page.getAttribute(cardReplay, "aria-pressed"), "false", "a second press stops it");
    assertEq((await page.$$(".blockInkCard path")).length, 2, "the card shows the whole drawing again");
    const first = page.locator(".sortableBlockWrap", { hasText: "Before the page" }).first();
    await first.hover();
    await first.locator(".dragHandle").click();
    await page.locator(".ctxMenu").getByText("Add page below", { exact: true }).click();
    await until(async () => (await page.$$(".noteSheet")).length === 2, { what: "a second page" });
    await until(async () => (await noteTree()).children.map((c) => !!c.properties?.sheet).join() === "false,true,true",
      { what: "the text, the new page, the first page" });
    await page.reload();
    await until(async () => (await page.$$(".noteSheet .nbSheet")).length === 2, { what: "both pages after the reload", timeout: 15000 });
    await until(async () => (await page.$$(".noteSheet .inkLayer path")).length === 2, { what: "the strokes after the reload", timeout: 15000 });
    assertEq((await page.locator(".blockSheetLabel").allTextContents()).join("|"), "Page 1|Page 2", "numbered in document order");
    assertNoProblems(page);
    await ctx.close();
  });
}
