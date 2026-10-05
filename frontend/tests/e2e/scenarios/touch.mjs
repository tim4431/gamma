// One behaviour for mouse and finger (docs/dev/ui-design.md): a held
// finger is a right-click and a double tap a double-click (shared/ui/press.js),
// every row's menu has a visible "⋯" (MenuButton), a drag's filing has menu
// rows too, and a control is the same size whatever points at it. Each
// behaviour runs once with a mouse on a desktop and once with a finger on a
// tablet. Chromium emulates the tablet: `isMobile` gives `(hover: none)` and
// `(pointer: coarse)`, and CDP's Input.dispatchTouchEvent the finger, with
// the pointer events a real touch screen sends. That is emulation, not an
// iPad (docs/dev/ipad.md has the device checks).
import { fakeAiModels, wanted } from "../harness.mjs";
import { waitForPdf } from "./pdf.mjs";
import { PNG_1PX } from "./notes.mjs";

// The tablet held in landscape (the docks) and upright (the compact shell).
const TABLET = { hasTouch: true, isMobile: true, deviceScaleFactor: 2, viewport: { width: 1194, height: 834 } };
const UPRIGHT = { ...TABLET, viewport: { width: 834, height: 1194 } };

export async function touchScenarios({ server, browser, alice, makePdf, step, until, sleep, assert, assertEq, assertNoProblems, openPage }) {
  if (!wanted("touch")) return;

  // A paper with notes, for the docks, the menus and the editor.
  const up = await alice.upload("/api/uploads", makePdf([["One behaviour for mouse and finger", "A second line"], ["Page two"]]),
    "touch.pdf", "application/pdf");
  const paper = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Touch paper", source_url: up.source_url } });
  for (const text of ["First touch note", "Second touch note", "Third touch note"]) {
    await alice.api("/api/blocks", { method: "POST", body: { parent_id: paper.id, content: text } });
  }
  const paperUrl = `${server.base}/?ws=${alice.ws}&page=${paper.id}`;
  // A folder of its own for the library steps (other groups' pages share the
  // account's root): three pages and two subfolders.
  const libFolder = await alice.folder("Touch library");
  const alpha = await alice.folder("Touch library/Alpha");
  const beta = await alice.folder("Touch library/Beta");
  const libPages = {};
  for (const title of ["Touch page one", "Touch page two", "Touch page three"]) {
    libPages[title] = (await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: title } })).id;
    await alice.file(libPages[title], { folders: ["Touch library"] });
  }
  const libUrl = `${server.base}/?ws=${alice.ws}&folder=${libFolder}`;
  // A notebook of five blank A4 sheets, in the page's order.
  const SHEET = { width: 595.28, height: 841.89, color: "#ffffff", pattern: "blank", spacing: 24, line: "#c8d1dc" };
  const book = await alice.api("/api/pages", { method: "POST", body: { title: "Touch notebook" } });
  for (let i = 0; i < 5; i++) {
    await alice.api("/api/blocks", { method: "POST", body: { parent_id: book.id, content: "", properties: { sheet: SHEET, collapsed: true } } });
  }
  const bookUrl = `${server.base}/?ws=${alice.ws}&page=${book.id}`;
  // A page of its own for the chat, with two earlier conversations in its history.
  const chatPage = await alice.api("/api/pages", { method: "POST", body: { title: "Touch chat page" } });
  for (const title of ["Earlier talk one", "Earlier talk two"]) {
    await alice.api("/api/chat-history/archive", { method: "POST", body: { bucket: chatPage.id, title,
      messages: [{ role: "user", text: `${title}?` }, { role: "ai", text: "An earlier answer." }] } });
  }
  const chatUrl = `${server.base}/?ws=${alice.ws}&page=${chatPage.id}`;
  // The folder tree as the server holds it: a folder's children by name.
  const folderNode = async (id) => {
    const find = (node) => node.id === id ? node : (node.children || []).map(find).find(Boolean);
    return find((await alice.api("/api/blocks/folders/subtree")).block);
  };
  // Tabs other groups left open would crowd the strip.
  await alice.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [] } });

  // --- the finger, through CDP -------------------------------------------------

  // A finger at (x, y) held for `ms`, then lifted: press.js's long press when
  // `ms` passes HOLD_MS (500).
  const hold = async (cdp, x, y, ms = 650) => {
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
    await sleep(ms); // the gesture IS the wait: a finger held still
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  };
  // A finger dragged from `from` to `to` in `n` moves; `midway(i)` runs
  // after each move, while the finger is still down.
  const drag = async (cdp, from, to, { n = 16, midway } = {}) => {
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [from] });
    for (let i = 1; i <= n; i++) {
      await cdp.send("Input.dispatchTouchEvent", { type: "touchMove",
        touchPoints: [{ x: from.x + (to.x - from.x) * i / n, y: from.y + (to.y - from.y) * i / n }] });
      await sleep(16);
      if (midway) await midway(i);
    }
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  };
  const centre = async (locator) => {
    const b = await locator.boundingBox();
    return { x: Math.round(b.x + b.width / 2), y: Math.round(b.y + b.height / 2) };
  };

  // --- 1. dock windows ---------------------------------------------------------

  const chatGrip = (page) => page.locator('[data-guide="chat.grip"]');
  const chatWindow = (page) => page.locator(".dockWindow", { has: chatGrip(page) });
  const chatFolded = async (page) => (await chatWindow(page).getAttribute("class")).includes("collapsed");

  await step("touch: two taps on the Chat grip fold it, one tap on the folded header opens it", async () => {
    const ctx = await alice.context(browser, TABLET);
    try {
      const page = await openPage(ctx, paperUrl);
      await waitForPdf(page);
      await chatGrip(page).waitFor();
      assert(!(await chatFolded(page)), "Chat starts open");
      // A double tap: two releases on the grip within 400 ms (press.js
      // counts them; a touch screen's own dblclick is not dependable).
      const at = await centre(chatGrip(page));
      await page.touchscreen.tap(at.x, at.y);
      await page.touchscreen.tap(at.x, at.y);
      await until(() => chatFolded(page), { what: "two taps fold Chat" });
      // Folded, ONE tap on the header opens it: no second tap to wait for.
      await sleep(450); // past the double tap's window, so this tap is a first one
      const folded = await centre(chatGrip(page));
      await page.touchscreen.tap(folded.x, folded.y);
      await until(async () => !(await chatFolded(page)), { what: "one tap on the folded header opens Chat" });
      await sleep(900); // the tap that opened it must not count as half of a fold
      assert(!(await chatFolded(page)), "Chat stays open after the opening tap");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("touch: a double-click on the Chat grip folds it, a click on the folded header opens it", async () => {
    const ctx = await alice.context(browser);
    try {
      const page = await openPage(ctx, paperUrl);
      await waitForPdf(page);
      await chatGrip(page).dblclick();
      await until(() => chatFolded(page), { what: "a double-click folds Chat" });
      await sleep(450);
      await chatGrip(page).click();
      await until(async () => !(await chatFolded(page)), { what: "a click on the folded header opens Chat" });
      await sleep(900);
      assert(!(await chatFolded(page)), "Chat stays open after the opening click");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("touch: a folded Chat closed and shown again from the View menu comes back open", async () => {
    const ctx = await alice.context(browser);
    try {
      const page = await openPage(ctx, paperUrl);
      await waitForPdf(page);
      await chatGrip(page).dblclick();
      await until(() => chatFolded(page), { what: "Chat folded" });
      await page.getByRole("button", { name: "Close Chat", exact: true }).click();
      await until(async () => (await chatGrip(page).count()) === 0, { what: "Chat closed" });
      await page.locator('[data-guide="header.view"]').click();
      await page.locator(".menuPopover .ctxMenuItem", { hasText: "AI Chat" }).click();
      await chatGrip(page).waitFor();
      assert(!(await chatFolded(page)), "a closed window forgets its fold");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // The Notes/Chat splitter sits right above Chat's header and takes a press
  // 6 px past itself: a finger on the grip's upper part must still move the
  // window, not the split.
  if (browser.browserType().name() === "chromium") await step("touch: a finger drag from the top of the Chat grip docks Chat on the left and leaves the split alone", async () => {
    const ctx = await alice.context(browser, TABLET);
    try {
      const page = await openPage(ctx, paperUrl);
      await waitForPdf(page);
      await chatGrip(page).waitFor();
      const cdp = await ctx.newCDPSession(page);
      const notes = page.locator('[data-panel-id="notes"]');
      const notesHeight = async () => Math.round((await notes.boundingBox()).height);
      const before = await notesHeight();
      const g = await chatGrip(page).boundingBox();
      // A quarter of the way down the grip: its upper part, clear of the
      // splitter's own 5 px and the 6 px it takes past them (SASH_MARGINS).
      const from = { x: Math.round(g.x + g.width / 2), y: Math.round(g.y + g.height / 4) };
      const sash = await page.locator('[data-panel-id="slot-right"] .sash-vertical').boundingBox();
      const geometry = `grip ${Math.round(g.y)}..${Math.round(g.y + g.height)}, splitter ${Math.round(sash.y)}..${Math.round(sash.y + sash.height)}, finger at ${from.y}`;
      // Up and to the left: a split taken by mistake would follow the finger up.
      const heights = [];
      await drag(cdp, from, { x: 200, y: Math.max(120, from.y - 200) },
        { midway: async (i) => { if (i <= 6) heights.push(await notesHeight()); } });
      assert(heights.every((h) => Math.abs(h - before) <= 1), `the Notes/Chat split moved under the finger (${geometry}): Notes ${before} -> ${heights.join(",")}`);
      await until(async () => (await page.locator('[data-panel-id="slot-left"] [data-guide="chat.grip"]').count()) === 1,
        { what: "Chat docked in the left slot" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("touch: the compact shell's panels have no grip to drag", async () => {
    const ctx = await alice.context(browser, UPRIGHT);
    try {
      const page = await openPage(ctx, paperUrl);
      await page.waitForSelector(".app.phoneUI .phoneBottomBar");
      await page.getByRole("button", { name: "Notes", exact: true }).tap();
      const grip = page.locator(".phonePanel:not(.phonePanelHidden) .dockGrip");
      await grip.waitFor();
      assertEq((await grip.innerText()).trim(), "Notes", "a plain label");
      assert(!(await grip.innerText()).includes("⠿"), "no drag dots");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // --- 2. a held finger is a right-click ------------------------------------------

  const menu = (page) => page.locator(".ctxMenu").first();
  const menuRows = async (page) => (await page.locator(".ctxMenu > .ctxMenuItem .ctxMenuText, .ctxMenu > .ctxSubWrap .ctxMenuText")
    .allInnerTexts()).map((s) => s.trim());
  const closeMenu = async (page) => {
    await page.keyboard.press("Escape");
    await until(async () => (await page.locator(".ctxMenu").count()) === 0, { what: "the menu closes" });
  };
  const fileRow = (page, title) => page.locator(".fileRow", { hasText: title }).first();
  const folderRow = (page, name) => page.locator(".folderRow:not(.folderNewBtn)", { hasText: name }).first();
  const notOpened = (page) => {
    const u = new URL(page.url());
    return !u.searchParams.get("block") && !u.searchParams.get("page");
  };

  if (browser.browserType().name() === "chromium") await step("touch: a held finger opens a page's, a folder's and a tab's menu, and opens nothing; a short tap still opens the page", async () => {
    const ctx = await alice.context(browser, TABLET);
    try {
      const page = await openPage(ctx, libUrl);
      const cdp = await ctx.newCDPSession(page);
      await fileRow(page, "Touch page one").waitFor();
      let at = await centre(fileRow(page, "Touch page one"));
      await hold(cdp, at.x, at.y);
      await menu(page).waitFor();
      const pageMenu = await menuRows(page);
      for (const row of ["Open", "Rename", "Duplicate"]) assert(pageMenu.includes(row), `the page menu has ${row}: ${pageMenu.join(" | ")}`);
      await sleep(300); // the lifted finger's click, had it gone through, would be opening the page now
      assert(notOpened(page), `the hold opened nothing: ${page.url()}`);
      await closeMenu(page);

      at = await centre(folderRow(page, "Alpha"));
      await hold(cdp, at.x, at.y);
      await menu(page).waitFor();
      const folderMenu = await menuRows(page);
      for (const row of ["Rename", "Delete", "Move to"]) assert(folderMenu.includes(row), `the folder menu has ${row}: ${folderMenu.join(" | ")}`);
      await sleep(300);
      assert(!page.url().includes(`folder=${alpha}`), `the hold did not open the folder: ${page.url()}`);
      await closeMenu(page);

      // A short tap opens the page, as on a phone's file manager.
      await fileRow(page, "Touch page one").tap();
      await until(() => page.url().includes(`block=${libPages["Touch page one"]}`), { what: "a tap opens the page" });
      const tab = page.locator(".tabStrip .tab", { hasText: "Touch page one" });
      await tab.waitFor();
      at = await centre(tab);
      await hold(cdp, at.x, at.y);
      await menu(page).waitFor();
      const tabMenu = await menuRows(page);
      for (const row of ["Pin tab", "Close tab"]) assert(tabMenu.includes(row), `the tab menu has ${row}: ${tabMenu.join(" | ")}`);
      await closeMenu(page);
      assertEq(await tab.count(), 1, "the tab is still there");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // --- 3. the row's "⋯" -------------------------------------------------------------

  const opacity = (locator) => locator.evaluate((el) => getComputedStyle(el).opacity);

  await step("touch: a row's ⋯ opens the page menu: shown on hover with a mouse, always with a finger; a card's corner ⋯ too", async () => {
    const desk = await alice.context(browser);
    try {
      const page = await openPage(desk, libUrl);
      const row = fileRow(page, "Touch page two");
      await row.waitFor();
      const dots = row.locator(".rowMenuBtn");
      await page.mouse.move(2, 600);
      await until(async () => (await opacity(dots)) === "0", { what: "the ⋯ hidden while the row is not hovered" });
      await row.hover();
      await until(async () => (await opacity(dots)) === "1", { what: "the ⋯ shows on hover" });
      await dots.click();
      await menu(page).waitFor();
      const rows = await menuRows(page);
      for (const r of ["Open", "Rename", "Duplicate"]) assert(rows.includes(r), `the ⋯ opens the page menu: ${rows.join(" | ")}`);
      assert(notOpened(page), "the ⋯ opened nothing");
      await closeMenu(page);
      // The grid: the card's corner.
      await page.getByRole("button", { name: "Grid view", exact: true }).click();
      const card = page.locator(".pageCard", { hasText: "Touch page two" }).first();
      await card.hover();
      await card.locator(".rowMenuBtn").click();
      await menu(page).waitFor();
      assert((await menuRows(page)).includes("Duplicate"), "the card's ⋯ opens the page menu");
      await closeMenu(page);
      assertNoProblems(page);
    } finally { await desk.close(); }

    const tablet = await alice.context(browser, TABLET);
    try {
      const page = await openPage(tablet, libUrl);
      const dots = fileRow(page, "Touch page two").locator(".rowMenuBtn");
      await dots.waitFor();
      await until(async () => (await opacity(dots)) === "1", { what: "the ⋯ shows at rest where nothing hovers" });
      await dots.tap();
      await menu(page).waitFor();
      assert((await menuRows(page)).includes("Rename"), "a tap on the ⋯ opens the page menu");
      await sleep(300);
      assert(notOpened(page), "the tap on the ⋯ opened nothing");
      await closeMenu(page);
      assertNoProblems(page);
    } finally { await tablet.close(); }
  });

  // --- 4. what a drag files, the folder menu does too -------------------------------

  await step("touch: the folder menu's Move down swaps it with the next folder in Custom order; Move to nests it", async () => {
    const ctx = await alice.context(browser);
    try {
      const page = await openPage(ctx, libUrl);
      await folderRow(page, "Alpha").waitFor();
      const childNames = async () => ((await folderNode(libFolder)).children || []).map((c) => c.content);
      assertEq((await childNames()).join(), "Alpha,Beta", "the two folders in order");
      // Custom order: the listing shows the tree's order, and the menu steps through it.
      await page.locator(".homeListBar .uiSelectBtn").first().click();
      await page.locator(".uiSelectMenu .ctxMenuItem", { hasText: "Custom order" }).click();
      await folderRow(page, "Alpha").click({ button: "right" });
      await menu(page).waitFor();
      assert(!(await page.locator(".ctxMenu .ctxMenuItem", { hasText: "Move up" }).isEnabled()), "the first folder cannot go up");
      await page.locator(".ctxMenu .ctxMenuItem", { hasText: "Move down" }).click();
      await until(async () => (await childNames()).join() === "Beta,Alpha", { what: "Move down swaps Alpha with Beta" });
      await until(async () => (await page.locator(".folderRow .folderName").allInnerTexts()).filter((n) => ["Alpha", "Beta"].includes(n)).join() === "Beta,Alpha",
        { what: "the listing shows the new order" });
      // Move to → Beta: Alpha goes inside it.
      await folderRow(page, "Alpha").click({ button: "right" });
      await page.locator(".ctxMenu .ctxSubTrigger", { hasText: "Move to" }).click();
      await page.locator(".ctxSubMenu .ctxMenuItem", { hasText: "Beta" }).click();
      await until(async () => ((await folderNode(beta)).children || []).some((c) => c.id === alpha), { what: "Alpha nested in Beta" });
      await until(async () => (await folderRow(page, "Alpha").count()) === 0, { what: "Alpha leaves the listing" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // --- 5. Select mode ---------------------------------------------------------------

  for (const finger of [false, true]) {
    await step(`touch: Select mode picks two pages by ${finger ? "tap" : "click"} and its ⋯ acts on both; Done leaves`, async () => {
      const ctx = await alice.context(browser, finger ? TABLET : {});
      try {
        const page = await openPage(ctx, libUrl);
        const press = (locator) => (finger ? locator.tap() : locator.click());
        const bar = page.locator(".homeListBar");
        await fileRow(page, "Touch page one").waitFor();
        await press(bar.getByRole("button", { name: "Select", exact: true }));
        await press(fileRow(page, "Touch page one"));
        await press(fileRow(page, "Touch page three"));
        await bar.locator(".homeSelectCount", { hasText: "2 selected" }).waitFor();
        await sleep(300);
        assert(notOpened(page), `a ${finger ? "tap" : "click"} in Select mode ticks, it does not open: ${page.url()}`);
        await press(bar.locator(".rowMenuBtn"));
        await menu(page).waitFor();
        const rows = await menuRows(page);
        assert(rows.includes("Duplicate 2 pages") && rows.includes("Delete 2 pages"), `the menu acts on both: ${rows.join(" | ")}`);
        await closeMenu(page);
        await press(bar.getByRole("button", { name: "Done", exact: true }));
        await bar.getByRole("button", { name: "Select", exact: true }).waitFor();
        assertEq(await page.locator(".fileRow.selected").count(), 0, "Done clears the selection");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });
  }

  // --- 6. the View menu: the keyboard's commands without a keyboard ----------------

  const viewMenu = (page) => page.locator(".menuPopover");
  const viewRow = (page, label) => viewMenu(page).locator(".ctxMenuItem", { has: page.locator(".ctxMenuText", { hasText: new RegExp(`^${label}$`) }) });

  await step("touch: the View menu has Undo, Redo, Go to page and Command palette with their keys; Command palette opens it", async () => {
    const ctx = await alice.context(browser);
    try {
      const page = await openPage(ctx, paperUrl);
      await waitForPdf(page);
      await page.locator('[data-guide="header.view"]').click();
      await viewMenu(page).waitFor();
      for (const label of ["Undo", "Redo", "Go to page", "Command palette"]) {
        const row = viewRow(page, label);
        assertEq(await row.count(), 1, `the View menu has ${label}`);
        assert((await row.locator(".ctxMenuKey").innerText()).trim(), `${label} shows its key`);
      }
      await viewRow(page, "Command palette").click();
      await page.getByRole("dialog", { name: "Command palette" }).waitFor();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("touch: a share view's View menu has no Undo", async () => {
    const { token } = await alice.api(`/api/share/${paper.id}`, { method: "POST" });
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    try {
      const page = await openPage(ctx, `${server.base}/?share=${token}`);
      await page.waitForSelector(".readOnlyTitle");
      await page.locator('[data-guide="header.view"]').click();
      await viewMenu(page).waitFor();
      assertEq(await viewRow(page, "Undo").count(), 0, "nothing to undo on a read-only page");
      assertEq(await viewRow(page, "Redo").count(), 0, "nor to redo");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api(`/api/share-settings/${paper.id}`, { method: "DELETE" });
    }
  });

  // --- 7. the pill's Undo -------------------------------------------------------------

  await step("touch: a note deleted from its handle menu leaves a pill whose Undo brings it back", async () => {
    const doomed = await alice.api("/api/blocks", { method: "POST", body: { parent_id: paper.id, content: "A note to delete and bring back" } });
    const onServer = async () => ((await alice.api(`/api/blocks/${paper.id}/subtree`)).block.children || []).some((b) => b.content === "A note to delete and bring back");
    const ctx = await alice.context(browser, TABLET);
    try {
      const page = await openPage(ctx, paperUrl);
      const wrap = page.locator(".sortableBlockWrap", { hasText: "A note to delete and bring back" }).first();
      await wrap.waitFor();
      // A finger has no hover: the row's ⋮⋮ is there at rest.
      await wrap.locator(".dragHandle").first().tap();
      await page.locator(".ctxMenu .ctxMenuItem", { hasText: /^Delete$/ }).tap();
      await until(async () => !(await onServer()), { what: "the note deleted on the server" });
      const pill = page.locator(".statusPill", { hasText: "Block deleted." });
      await pill.waitFor();
      await pill.getByRole("button", { name: "Undo", exact: true }).tap();
      await until(onServer, { what: "Undo brings the note back on the server" });
      await page.locator(".blockRow", { hasText: "A note to delete and bring back" }).waitFor();
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api(`/api/blocks/${doomed.id}`, { method: "DELETE" }).catch(() => {});
    }
  });

  // --- 8. a notebook's two views keep the reader's place ------------------------------

  const NB = ".nbViewer .nbSheet";    // the notebook view's sheets
  const NOTES = ".noteSheet .nbSheet"; // the notes view's
  // The sheet (1 = the first) at the top of the view that scrolls `scope`'s
  // sheets, and a scroll that puts sheet `n` there, a little way down it.
  const sheetAtTop = (page, scope) => page.evaluate((scope) => {
    const sheets = [...document.querySelectorAll(scope)];
    let el = sheets[0]?.parentElement;
    while (el && !(/(auto|scroll)/.test(getComputedStyle(el).overflowY) && el.scrollHeight > el.clientHeight)) el = el.parentElement;
    if (!el) return 0;
    const top = el.getBoundingClientRect().top;
    return sheets.findIndex((s) => s.getBoundingClientRect().bottom > top + 40) + 1;
  }, scope);
  const scrollToSheet = (page, scope, n) => page.evaluate(([scope, n]) => {
    const sheet = document.querySelectorAll(scope)[n - 1];
    let el = sheet.parentElement;
    while (el && !(/(auto|scroll)/.test(getComputedStyle(el).overflowY) && el.scrollHeight > el.clientHeight)) el = el.parentElement;
    el.scrollTop += sheet.getBoundingClientRect().top - el.getBoundingClientRect().top + 60;
  }, [scope, n]);
  const atSheet = (page, scope, n, what) => until(async () => (await page.locator(scope).count()) === 5 && (await sheetAtTop(page, scope)) === n,
    { what, timeout: 10000 });
  const titleNotebookBtn = (page) => page.locator(".pageActionCol button", { hasText: "Notebook" });
  const notesViewBtn = (page) => page.locator(".pdfZoomOverlay button[aria-label='Notes view']");

  await step("touch: a notebook's views open at the sheet in view: the title row's Notebook, Notes view, a sheet's own Notebook view", async () => {
    const ctx = await alice.context(browser);
    try {
      const page = await openPage(ctx, bookUrl);
      await atSheet(page, NOTES, 1, "the notes view draws the five sheets");
      await scrollToSheet(page, NOTES, 3);
      await atSheet(page, NOTES, 3, "the notes scrolled to sheet 3");
      await titleNotebookBtn(page).click();
      await atSheet(page, NB, 3, "the title row's Notebook opens the notebook view at sheet 3, the one in view");
      await scrollToSheet(page, NB, 4);
      await atSheet(page, NB, 4, "the notebook view scrolled to sheet 4");
      await notesViewBtn(page).click();
      await until(async () => (await page.locator(NB).count()) === 0, { what: "the notebook view gives way" });
      await atSheet(page, NOTES, 4, "Notes view shows sheet 4 at the top, not sheet 1");
      // A sheet's own switch opens the notebook view at that sheet.
      await page.locator(".noteSheet").nth(1).locator(".noteSheetBar button[aria-label='Notebook view']").click();
      await atSheet(page, NB, 2, "sheet 2's Notebook view opens at sheet 2");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("touch: with the Notes window closed, Notes view still shows the sheets", async () => {
    const ctx = await alice.context(browser);
    try {
      const page = await openPage(ctx, bookUrl);
      await atSheet(page, NOTES, 1, "the notes view");
      await titleNotebookBtn(page).click();
      await atSheet(page, NB, 1, "the notebook view");
      await page.getByRole("button", { name: "Close Notes", exact: true }).click();
      await until(async () => (await page.locator('[data-guide="notes.grip"]').count()) === 0, { what: "the Notes window closed" });
      await notesViewBtn(page).click();
      await atSheet(page, NOTES, 1, "the notes view draws the sheets in the centre");
      const box = await page.locator(NOTES).first().boundingBox();
      assert(box && box.width > 300 && box.height > 300, `a sheet on screen: ${JSON.stringify(box)}`);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("touch: a notebook left at sheet 3 for a PDF tab is at sheet 3 on coming back", async () => {
    const ctx = await alice.context(browser);
    try {
      const page = await openPage(ctx, paperUrl);
      await waitForPdf(page);
      await page.goto(bookUrl);
      await atSheet(page, NOTES, 1, "the notes view");
      await titleNotebookBtn(page).click();
      await atSheet(page, NB, 1, "the notebook view");
      await scrollToSheet(page, NB, 3);
      await atSheet(page, NB, 3, "the notebook at sheet 3");
      await page.locator(".tabStrip .tab", { hasText: "Touch paper" }).click();
      await waitForPdf(page);
      await page.locator(".tabStrip .tab", { hasText: "Touch notebook" }).click();
      await atSheet(page, NB, 3, "back on the notebook, at sheet 3");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("touch: a notebook made while a PDF is zoomed in opens no wider than its viewer", async () => {
    const ctx = await alice.context(browser);
    try {
      const page = await openPage(ctx, paperUrl);
      await waitForPdf(page);
      const width = async () => (await page.locator('[data-page="1"]').boundingBox()).width;
      for (let i = 0; i < 4; i++) {
        const was = await width();
        await page.getByRole("button", { name: "Zoom in", exact: true }).click();
        await until(async () => Math.abs(await width() - was) > 1, { what: "Zoom in resizes the page" });
      }
      await page.click("button[aria-label='Add']");
      await page.getByRole("menuitem", { name: "New notebook" }).or(page.locator("button", { hasText: "New notebook" })).first().click();
      await page.waitForSelector(NB, { timeout: 15000 });
      await page.keyboard.type("Zoomed notebook");
      await page.keyboard.press("Enter");
      const fit = await until(() => page.locator(".nbViewer").evaluate((v) => {
        const s = v.querySelector(".nbSheet").getBoundingClientRect();
        return s.width > 0 ? { sheet: Math.round(s.width), view: v.clientWidth } : null;
      }), { what: "the new notebook's sheet" });
      assert(fit.sheet <= fit.view, `the sheet (${fit.sheet} px) fits its viewer (${fit.view} px)`);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("touch: in the notes view a sheet's tools sit above the paper at the right, its + under it at the right", async () => {
    const ctx = await alice.context(browser);
    try {
      const page = await openPage(ctx, bookUrl);
      await atSheet(page, NOTES, 1, "the notes view");
      const sheet = page.locator(".noteSheet").first();
      const [block, bar, paperBox, plus] = await Promise.all([
        sheet.locator("xpath=..").boundingBox(), sheet.locator(".noteSheetBar").boundingBox(),
        sheet.locator(".nbSheet").boundingBox(), sheet.locator(".noteSheetFoot button").boundingBox(),
      ]);
      const r = (b) => `${Math.round(b.x)},${Math.round(b.y)} ${Math.round(b.width)}x${Math.round(b.height)}`;
      const geometry = `block ${r(block)}, bar ${r(bar)}, paper ${r(paperBox)}, + ${r(plus)}`;
      assert(bar.y + bar.height <= paperBox.y + 1, `the tools sit above the paper's top edge: ${geometry}`);
      assert(Math.abs(bar.x + bar.width - (block.x + block.width)) <= 2, `the tools sit at the block's right: ${geometry}`);
      assert(bar.y <= block.y + 4, `the tools sit at the block's top: ${geometry}`);
      assert(plus.y >= paperBox.y + paperBox.height - 1, `the + sits under the paper: ${geometry}`);
      assert(Math.abs(plus.x + plus.width - (block.x + block.width)) <= 2, `the + sits at the right: ${geometry}`);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("touch: upright, the notebook's bar reads Library, Notebook, Notes, Chat in both views, and its two tabs switch them", async () => {
    const ctx = await alice.context(browser, UPRIGHT);
    try {
      const page = await openPage(ctx, bookUrl);
      await page.waitForSelector(".app.phoneUI .phoneBottomBar");
      const tabs = async () => (await page.locator(".phoneTabBar .phoneTab").allInnerTexts()).map((s) => s.trim()).join(" ");
      const tab = (name) => page.locator(".phoneTabBar .phoneTab", { hasText: new RegExp(`^${name}$`) });
      await atSheet(page, NOTES, 1, "the notes view");
      assertEq(await tabs(), "Library Notebook Notes Chat", "the notes view's bar");
      await tab("Notebook").tap();
      await atSheet(page, NB, 1, "the Notebook tab shows the notebook view");
      assertEq(await tabs(), "Library Notebook Notes Chat", "the notebook view's bar");
      await tab("Notes").tap();
      await atSheet(page, NOTES, 1, "the Notes tab shows the notes view");
      assertEq(await page.locator(NB).count(), 0, "the notebook view gave way");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // --- 9. the viewer ------------------------------------------------------------------

  await step("touch: the text / rectangle toggle is in a touch screen's viewer and not in a desktop's", async () => {
    for (const [opts, want] of [[TABLET, 1], [{}, 0]]) {
      const ctx = await alice.context(browser, opts);
      // The machine running the suite may have a touch screen of its own;
      // the desktop here is one without.
      if (!want) await ctx.addInitScript(() => Object.defineProperty(Navigator.prototype, "maxTouchPoints", { get: () => 0 }));
      try {
        const page = await openPage(ctx, paperUrl);
        await waitForPdf(page);
        await page.locator('[data-guide="viewer.zoom"]').waitFor();
        assertEq(await page.locator('[data-guide="viewer.selectMode"]').count(), want, `the toggle ${want ? "on a touch screen" : "with a mouse"}`);
        assertNoProblems(page);
      } finally { await ctx.close(); }
    }
  });

  // Pen only, a pen tool armed: a finger scrolls instead of drawing, which
  // the pill explains once a session, offering finger drawing.
  if (browser.browserType().name() === "chromium") await step("touch: a finger scrolling under an armed pen in pen-only mode explains itself once; Draw with finger draws", async () => {
    const ctx = await alice.context(browser, TABLET);
    await ctx.addInitScript(() => { if (!sessionStorage.getItem("e2e-pen-only")) { localStorage.setItem("gamma-ink-pen-only", "1"); sessionStorage.setItem("e2e-pen-only", "1"); } });
    try {
      const page = await openPage(ctx, paperUrl);
      await waitForPdf(page);
      const cdp = await ctx.newCDPSession(page);
      const pill = page.locator(".statusPill", { hasText: "Fingers scroll while a pen draws" });
      const box = await page.locator(".pdfViewer").boundingBox();
      const swipe = () => drag(cdp, { x: Math.round(box.x + box.width / 2), y: Math.round(box.y + box.height * 0.7) },
        { x: Math.round(box.x + box.width / 2 + 10), y: Math.round(box.y + box.height * 0.4) });
      await page.getByRole("button", { name: "Handwriting tools", exact: true }).tap();
      await page.locator(".pdfInkBar").waitFor();
      await swipe();
      await pill.waitFor();
      await pill.getByRole("button", { name: "Draw with finger", exact: true }).waitFor();
      // Once a session: gone (it stays 8 s for its button), a second swipe brings no new one.
      await pill.waitFor({ state: "detached", timeout: 12000 });
      await swipe();
      await sleep(1500); // a negative check: nothing to wait for
      assertEq(await pill.count(), 0, "the hint shows once a session");
      // A new session: the hint again, and its button turns finger drawing on.
      await page.reload();
      await waitForPdf(page);
      await page.getByRole("button", { name: "Handwriting tools", exact: true }).tap();
      await page.locator(".pdfInkBar").waitFor();
      await swipe();
      await pill.waitFor();
      // (a click: after a CDP touch scroll, Chromium's emulated taps make no click, docs/dev/debugging.md)
      await pill.getByRole("button", { name: "Draw with finger", exact: true }).click();
      await until(() => page.evaluate(() => localStorage.getItem("gamma-ink-pen-only") === "0"), { what: "pen only turned off" });
      const strokes = () => page.locator(".pdfViewer .inkLayer path").count();
      const before = await strokes();
      await page.locator(".pdfViewer").evaluate((el) => el.scrollTo(0, 0));
      await swipe();
      await until(async () => (await strokes()) > before, { what: "a finger now draws a stroke" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // --- 10. the notes editor under a finger ----------------------------------------------

  const image = await alice.upload("/api/upload-image", PNG_1PX, "touch-dot.png", "image/png");
  const notePage = await alice.api("/api/pages", { method: "POST", body: { title: "Touch editor page" } });
  const noteIds = {};
  for (const text of ["Code me", "Strike me", "Math $\\frac{a}{b}$", `![|120](${image.url})`]) {
    noteIds[text] = (await alice.api("/api/blocks", { method: "POST", body: { parent_id: notePage.id, content: text } })).id;
  }
  const noteContent = async (id) => ((await alice.api(`/api/blocks/${notePage.id}/subtree`)).block.children || []).find((b) => b.id === id)?.content;
  const noteUrl = `${server.base}/?ws=${alice.ws}&page=${notePage.id}`;
  // The open editor's CodeMirror view (what EditorView.findFromDOM reads off
  // the content element): its selection, set and read. A math source shows
  // as a widget where the caret is not, so arrow keys cannot walk into it.
  const caret = (page, anchor, head = anchor) => page.evaluate(([anchor, head]) => {
    const view = document.querySelector(".blockEditorCm .cm-content").cmTile.root.view;
    view.dispatch({ selection: { anchor, head } });
    view.focus();
  }, [anchor, head]);
  const selection = (page) => page.evaluate(() => {
    const { from, to } = document.querySelector(".blockEditorCm .cm-content").cmTile.root.view.state.selection.main;
    return [from, to];
  });

  await step("touch: under a finger every row's handle shows at 40 %, and the focused row keeps its ×", async () => {
    const ctx = await alice.context(browser, TABLET);
    try {
      const page = await openPage(ctx, noteUrl);
      await page.locator(".blockRow", { hasText: "Code me" }).waitFor();
      const handles = await page.locator(".rowHandles > .dragHandle").evaluateAll((els) => els.map((el) => getComputedStyle(el).opacity));
      assert(handles.length >= 4 && handles.every((o) => o === "0.4"), `every ⋮⋮ at 40 % at rest: ${handles.join(",")}`);
      await page.locator(".blockRow", { hasText: "Strike me" }).locator(".blockBody").tap();
      await page.waitForSelector(".blockEditorCm .cm-content");
      const del = page.locator(".blockRow.focused .blockDeleteBtn");
      assertEq(await del.count(), 1, "the focused row has its ×");
      await until(async () => (await opacity(del)) === "1", { what: "the × shows on the focused row" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("touch: the editing bar's inline code and strikethrough wrap a selection; prose gets autocorrect", async () => {
    const ctx = await alice.context(browser, TABLET);
    try {
      const page = await openPage(ctx, noteUrl);
      const bar = page.locator(".editBar");
      for (const [text, command, want] of [["Code me", "block.code", "`Code me`"], ["Strike me", "block.strike", "~~Strike me~~"]]) {
        await page.locator(".blockRow", { hasText: text }).first().locator(".blockBody").tap();
        await page.waitForSelector(".blockEditorCm .cm-content");
        await bar.waitFor();
        if (command === "block.code") {
          assertEq(await page.locator(".blockEditorCm .cm-content").getAttribute("autocorrect"), "on", "prose gets the system's autocorrect");
        }
        await caret(page, 0, text.length);
        await bar.locator(`[data-command="${command}"]`).tap();
        await until(async () => (await noteContent(noteIds[text])) === want, { what: `${command} wraps the selection: ${want}` });
        await bar.getByRole("button", { name: "Done", exact: true }).tap();
        await page.waitForSelector(".blockEditorCm", { state: "detached", timeout: 5000 });
      }
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("touch: the editing bar's next-argument key shows only inside a math argument and hops to the next", async () => {
    const ctx = await alice.context(browser, TABLET);
    try {
      const page = await openPage(ctx, noteUrl);
      const text = "Math $\\frac{a}{b}$";
      const next = page.locator('.editBar [data-command="block.nextSlot"]');
      await page.locator(".blockRow", { hasText: "Math" }).first().locator(".blockBody").tap();
      await page.waitForSelector(".blockEditorCm .cm-content");
      await page.locator(".editBar").waitFor();
      await caret(page, 2);
      await sleep(300); // the bar re-renders on the selection; a negative check
      assertEq(await next.count(), 0, "not in prose");
      const a = text.indexOf("{a}") + 1;
      await caret(page, a);
      await next.waitFor();
      assertEq(await page.locator(".blockEditorCm .cm-content").getAttribute("autocorrect"), "off", "no autocorrect inside math (CodeMirror's own default)");
      await next.tap();
      const b = text.indexOf("{b}") + 1;
      await until(async () => (await selection(page)).join() === `${b},${b + 1}`, { what: "the hop selects the next argument" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("touch: a tapped picture is selected and its toolbar's ⋯ opens the object menu", async () => {
    const ctx = await alice.context(browser, TABLET);
    try {
      const page = await openPage(ctx, noteUrl);
      const frame = page.locator(`.blockRowWrap[data-block-id="${noteIds[`![|120](${image.url})`]}"] .mdObject-image`);
      await frame.locator("img.mdImg").waitFor();
      await frame.locator("img.mdImg").tap();
      await page.locator(".mdObjectSelected").waitFor();
      const tools = frame.locator(".mdImgTools");
      await until(async () => (await opacity(tools)) === "1", { what: "the selected picture shows its toolbar" });
      await tools.locator(".rowMenuBtn").tap();
      await page.locator(".ctxMenu .ctxMenuItem", { hasText: "Edit markdown source" }).waitFor();
      assert((await menuRows(page)).includes("Delete image"), "the object menu");
      assertEq(await page.locator(".blockEditorCm").count(), 0, "no editor opened");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // --- 11. the chat's composer and history ------------------------------------------

  // A chat context with a model to talk to and every /api/ai/chat answered
  // at once; `sent` collects the prompts.
  const chatContext = async (opts) => {
    const ctx = await alice.context(browser, opts);
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    const sent = [];
    await ctx.route("**/api/ai/chat", (route) => {
      sent.push(route.request().postDataJSON().prompt);
      return route.fulfill({ contentType: "application/x-ndjson", body: '{"delta":"A fake answer."}\n' });
    });
    return { ctx, sent };
  };
  const composer = (page) => page.getByRole("combobox", { name: "Message AI" });

  await step("touch: with a keyboard Enter sends from the composer", async () => {
    const { ctx, sent } = await chatContext({});
    try {
      const page = await openPage(ctx, chatUrl);
      await composer(page).waitFor();
      await page.waitForLoadState("networkidle");
      await composer(page).fill("Asked with a keyboard");
      await composer(page).press("Enter");
      await until(() => sent.length === 1, { what: "Enter sends" });
      assertEq(sent[0], "Asked with a keyboard");
      await page.locator(".chatBubble.ai", { hasText: "A fake answer." }).waitFor();
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api(`/api/chats/${chatPage.id}`, { method: "PUT", body: { messages: [] } });
    }
  });

  await step("touch: on a touch screen Enter is a line break and Send sends; a draft survives turning the tablet upright", async () => {
    const { ctx, sent } = await chatContext(TABLET);
    try {
      const page = await openPage(ctx, chatUrl);
      await composer(page).waitFor();
      await page.waitForLoadState("networkidle");
      await composer(page).tap();
      await page.keyboard.type("First line");
      await page.keyboard.press("Enter");
      await page.keyboard.type("second line");
      assertEq(await composer(page).inputValue(), "First line\nsecond line", "Enter added a line break");
      await sleep(300); // nothing to wait for: Enter must not have sent
      assertEq(sent.length, 0, "Enter did not send");
      await page.locator(".chatComposerBar").getByRole("button", { name: "Send", exact: true }).tap();
      await until(() => sent.length === 1, { what: "Send sends" });
      assertEq(sent[0], "First line\nsecond line");
      await page.locator(".chatBubble.ai", { hasText: "A fake answer." }).waitFor();
      // A draft, then the tablet turned upright: the chat moves from its dock
      // to the compact shell's Chat panel, and the draft goes with it.
      await composer(page).fill("A draft typed in landscape");
      await page.setViewportSize({ width: 834, height: 1194 });
      await page.waitForSelector(".app.phoneUI .phoneBottomBar");
      await page.locator(".phoneTabBar .phoneTab", { hasText: "Chat" }).tap();
      await composer(page).waitFor();
      assertEq(await composer(page).inputValue(), "A draft typed in landscape", "the draft survived the rotation");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api(`/api/chats/${chatPage.id}`, { method: "PUT", body: { messages: [] } });
    }
  });

  await step("touch: a history row's ⋯ renames, selects and deletes, the current one only renames; a rename kept by clicking away", async () => {
    const { ctx } = await chatContext({});
    try {
      const page = await openPage(ctx, chatUrl);
      await composer(page).waitFor();
      await page.waitForLoadState("networkidle");
      await page.getByRole("button", { name: "Chat history", exact: true }).click();
      const pop = page.locator(".chatHistoryPop");
      const archived = pop.locator(".chatHistRow:not(.active)", { hasText: "Earlier talk one" });
      await archived.waitFor();
      await archived.hover();
      await archived.locator(".rowMenuBtn").click();
      await menu(page).waitFor();
      assertEq((await menuRows(page)).join(" | "), "Rename | Select | Delete", "an earlier conversation's menu");
      await closeMenu(page);
      const current = pop.locator(".chatHistRow.active");
      await current.hover();
      await current.locator(".rowMenuBtn").click();
      await menu(page).waitFor();
      assertEq((await menuRows(page)).join(" | "), "Rename", "the current conversation's menu");
      await closeMenu(page);
      // Rename, then a click elsewhere: the name is kept.
      await archived.hover();
      await archived.locator(".rowMenuBtn").click();
      await page.locator(".ctxMenu .ctxMenuItem", { hasText: "Rename" }).click();
      const field = pop.locator(".chatHistRow.renaming input");
      await field.waitFor();
      await field.fill("A renamed talk");
      await pop.locator(".searchInput").click();
      const titles = async () => (await alice.api(`/api/chat-history?bucket=${encodeURIComponent(chatPage.id)}`)).sessions.map((s) => s.title);
      await until(async () => (await titles()).includes("A renamed talk"), { what: "the rename saved" });
      await pop.locator(".chatHistRow", { hasText: "A renamed talk" }).waitFor();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // --- 12. one size set ---------------------------------------------------------------

  await step("touch: a topbar button, a viewer button and a window's close are the same size for a mouse and a finger", async () => {
    const sizes = {};
    for (const [name, opts] of [["mouse", {}], ["finger", TABLET]]) {
      const ctx = await alice.context(browser, opts);
      try {
        const page = await openPage(ctx, paperUrl);
        await waitForPdf(page);
        const size = async (sel) => {
          const b = await page.locator(sel).first().boundingBox();
          return `${Math.round(b.width)}x${Math.round(b.height)}`;
        };
        sizes[name] = {
          topbar: await size(".topbar .iconBtn"),
          viewer: await size(".pdfZoomOverlay button"),
          close: await size(".dockWindowHeader .uiClose"),
        };
        assertNoProblems(page);
      } finally { await ctx.close(); }
    }
    const want = { topbar: "35x35", viewer: "29x29", close: "26x26" };
    for (const name of ["mouse", "finger"]) {
      for (const [k, v] of Object.entries(want)) assertEq(sizes[name][k], v, `${name}: ${k}`);
    }
    return JSON.stringify(sizes.finger);
  });
}
