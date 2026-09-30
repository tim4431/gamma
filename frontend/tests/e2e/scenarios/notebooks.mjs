// Notebooks (docs/dev/notebooks.md): a page of sheets instead of a PDF —
// made from the add menu, written on with the same ink tools, a page added
// by writing low on the last one and by the button, the paper menu, the
// sheets in the notes, a reload, and the export as a PDF of the sheets.
// The rules behind it are backend/tests/test_notebooks.py and
// frontend/tests/notebook.test.mjs.

async function drawLine(page, from, to) {
  await page.mouse.move(from[0], from[1]);
  await page.mouse.down();
  await page.mouse.move(to[0], to[1], { steps: 12 });
  await page.mouse.up();
}

export async function notebookScenarios({ server, browser, alice, step, until, assert, assertEq, assertNoProblems, openPage, flags }) {
  const account = alice;
  let ctx, page, pageId;
  const tree = async () => (await account.api(`/api/blocks/${pageId}/subtree`)).block;
  const sheetsOnServer = async () => ((await tree()).children || []).filter((b) => b.properties?.sheet);
  const sheetBox = (i) => page.locator(".nbSheet").nth(i).boundingBox();

  await step("notebook: the add menu makes a notebook with one page; a stroke on it becomes a group under that page", async () => {
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?ws=${account.ws}`);
    await page.click("button[aria-label='Add']");
    await page.getByRole("menuitem", { name: "New notebook" }).or(page.locator("button", { hasText: "New notebook" })).first().click();
    await page.waitForSelector(".nbViewer .nbSheet", { timeout: 15000 });
    await page.keyboard.type("Lecture notes");
    await page.keyboard.press("Enter");
    pageId = await until(async () => {
      const pages = (await account.api("/api/blocks/root/children")).children || [];
      return pages.find((b) => b.properties?.notebook && b.content === "Lecture notes")?.id;
    }, { what: "the notebook, named, in the library" });
    assert(page.url().includes(pageId), "the address names the open notebook");
    assertEq((await page.$$(".nbSheet")).length, 1, "one page to start");
    const root = await tree();
    assert(root.properties.notebook?.sheet?.width > 0, "the root carries the notebook's paper");
    assertEq((await sheetsOnServer()).length, 1, "the first sheet is stored with it");
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
    // the notes list the sheets as pages, the handwriting under the first
    await until(async () => (await page.locator(".blockSheetLabel").allTextContents()).join("|") === "Page 1|Page 2|Page 3",
      { what: "Page 1-3 in the notes" });
    assert(await page.$(".blockInkCard"), "the handwriting's card in the notes");
    b = await sheetBox(0);
    assert(b.width > 300, "the page fits the viewer's width");
    assertNoProblems(page);
  });

  await step("notebook: the paper menu sets a page's pattern and the paper new pages get", async () => {
    await page.keyboard.press("Escape"); // close the ink strip
    await page.evaluate(() => document.querySelector(".nbViewer").scrollTo({ top: 0 }));
    await page.click("button[aria-label='Paper']");
    await page.waitForSelector(".nbPaperMenu");
    await page.locator(".nbPaperMenu").getByRole("button", { name: "Ruled", exact: true }).click();
    await until(async () => (await sheetsOnServer())[0].properties.sheet.pattern === "ruled", { what: "page 1 ruled on the server" });
    await until(async () => !!(await page.$(".nbSheet >> nth=0 >> .nbPaper path")), { what: "page 1 draws its lines" });
    if (flags.keep) await page.screenshot({ path: `${server.dir}/notebook-paper.png` });
    await page.locator(".nbPaperMenu").getByRole("button", { name: "Use for new pages" }).click();
    await until(async () => (await tree()).properties.notebook.sheet.pattern === "ruled", { what: "the notebook's paper is ruled" });
    await page.click(".nbAddSheet");
    await until(async () => {
      const sheets = await sheetsOnServer();
      return sheets.length === 4 && sheets[3].properties.sheet.pattern === "ruled";
    }, { what: "a new page gets the notebook's paper" });
    assertEq((await sheetsOnServer())[1].properties.sheet.pattern, "blank", "the other pages keep theirs");
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
    await ctx.close();
  });
}
