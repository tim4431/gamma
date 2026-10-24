import { generateNKeysBetween } from "fractional-indexing";
import { Account, arrived, assertNear, live, wanted } from "../harness.mjs";

// Motion (docs/dev/ui-design.md, "Motion"): a menu grows in from the point
// it opens from and leaves through a still copy of itself, inert and out of
// the accessibility tree, that goes once its way out has played; a closed
// dialog's copy never counts as an open dialog, so a shortcut works while
// it fades; under reduced motion nothing lingers; a segmented control's
// frame slides to the option chosen; in the notes, a block added mid-list
// fades in while the rows below glide to make room, but nothing moves on a
// page opening or for another client's block, and a fold closes before
// its rows go.
export async function motionScenarios(env) {
  const { server, browser, step, openPage, assert, assertEq, assertNoProblems, until } = env;
  if (!wanted("motion")) return;
  await server.manage("create-user", "motion-user", "motion-pw");
  const user = await new Account(server, "motion-user", "motion-pw").login();
  await user.folder("Lab");
  const home = `${server.base}/?ws=${user.ws}`;
  const folderRow = (page) => page.locator(".fileList .folderRow", { hasText: "Lab" }).first();
  // Right-clicks a folder row 40 px in; the point, in the window.
  async function menuAt(page) {
    const row = folderRow(page);
    await row.waitFor();
    const box = await row.boundingBox();
    const at = { x: 40, y: Math.round(box.height / 2) };
    await row.click({ button: "right", position: at });
    return { x: box.x + at.x, y: box.y + at.y };
  }

  await step("motion: a menu grows from the cursor and leaves through an inert copy that goes; the next menu takes its place", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, home);
    try {
      const point = await menuAt(page);
      const menu = page.locator(live(".ctxMenu"));
      await menu.waitFor();
      await arrived(menu);
      const { box, origin } = await menu.evaluate((el) => ({ box: el.getBoundingClientRect().toJSON(), origin: getComputedStyle(el).transformOrigin }));
      const [ox, oy] = origin.split(" ").map(parseFloat);
      assertNear(box.x + ox, point.x, 1.5, "it grows from the cursor (x)");
      assertNear(box.y + oy, point.y, 1.5, "it grows from the cursor (y)");

      await page.keyboard.press("Escape");
      const copy = page.locator('.ctxMenu[data-leaving="surface"]');
      await copy.waitFor({ state: "attached" });
      assertEq(await copy.getAttribute("aria-hidden"), "true", "the copy is out of the accessibility tree");
      assert(await copy.evaluate((el) => el.hasAttribute("inert") && getComputedStyle(el).pointerEvents === "none"), "the copy takes no press");
      assertEq(await page.getByRole("menu").count(), 0, "no menu is open as the copy goes");
      await copy.waitFor({ state: "detached", timeout: 2000 });

      await menuAt(page);
      await menu.waitFor();
      await page.keyboard.press("Escape");
      await menuAt(page);
      await menu.waitFor();
      assertEq(await page.locator(".ctxMenu").count(), 1, "a menu that opens takes the place of one still leaving");
      await page.keyboard.press("Escape");
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("motion: a closed dialog fading out is not open: Ctrl+P works at once, and the palette leaves the same way", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, home);
    try {
      await folderRow(page).waitFor();
      await page.keyboard.press("Control+Comma");
      const settings = page.getByRole("dialog", { name: "Settings" });
      await settings.waitFor();
      await page.keyboard.press("Escape");
      await settings.waitFor({ state: "detached" });
      // the copy of the closed dialog may still be fading: the palette opens anyway
      await page.keyboard.press("Control+p");
      const palette = page.getByRole("dialog", { name: "Open a page" });
      await palette.waitFor({ timeout: 3000 });
      await page.keyboard.press("Escape");
      await palette.waitFor({ state: "detached" });
      await until(async () => (await page.locator("[data-leaving]").count()) === 0, { timeout: 2000, what: "every copy goes" });
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("motion: under reduced motion a closed menu leaves no copy", async () => {
    const ctx = await user.context(browser, { reducedMotion: "reduce" });
    const page = await openPage(ctx, home);
    try {
      await menuAt(page);
      await page.locator(live(".ctxMenu")).waitFor();
      await page.keyboard.press("Escape");
      const left = await page.evaluate(() => new Promise((done) =>
        requestAnimationFrame(() => done(document.querySelectorAll("[data-leaving]").length))));
      assertEq(left, 0, "nothing lingers");
      assertEq(await page.locator(".ctxMenu").count(), 0, "the menu is gone at once");
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // A page of four rows, Row 0 to Row 3 (mv0 … mv3); Row 2 holds two children.
  const pg = await user.api("/api/pages", { method: "POST", body: { title: "Moves" } });
  const keys = generateNKeysBetween(null, null, 4), kids = generateNKeysBetween(null, null, 2);
  await user.api(`/api/pages/${pg.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
    ...keys.map((position, i) => ({ op: "insert", id: `mv${i}`, parent: pg.id, position, content: `Row ${i}` })),
    ...kids.map((position, i) => ({ op: "insert", id: `mv2c${i}`, parent: "mv2", position, content: `Child ${i}` })),
  ] } });
  const notes = `${server.base}/?ws=${user.ws}&page=${pg.id}`;
  // Every glide and fade the page starts, from before it loads: "moving mv2".
  const watch = (ctx) => ctx.addInitScript(() => {
    window.__glides = [];
    new MutationObserver((records) => {
      for (const r of records) {
        if (r.target.hasAttribute(r.attributeName)) window.__glides.push(`${r.attributeName.slice(5)} ${r.target.dataset.motionKey}`);
      }
    }).observe(document, { subtree: true, attributes: true, attributeFilter: ["data-moving", "data-entering"] });
  });
  const glides = (page) => page.evaluate(() => window.__glides.splice(0));
  const rowOf = (page, id) => page.locator(`.sortableBlockWrap[data-block-id="${id}"]`);
  // Opens an editor at the end of `id`'s text and adds a block under it.
  async function addBelow(page, id) {
    await rowOf(page, id).locator(".blockRow").click();
    await page.locator(".blockEditorCm").first().waitFor();
    await page.keyboard.press("End");
    await page.keyboard.press("Shift+Enter");
  }

  await step("motion: a block added mid-outline fades in where it rests while the rows below glide down; a page opening moves nothing", async () => {
    const ctx = await user.context(browser);
    await watch(ctx);
    const page = await openPage(ctx, notes);
    try {
      await rowOf(page, "mv3").waitFor();
      await rowOf(page, "mv2c1").waitFor();
      assertEq((await glides(page)).length, 0, "a page opening moves nothing");
      await addBelow(page, "mv1");
      await until(async () => (await page.locator(".sortableBlockWrap[data-block-id]").count()) === 7, { what: "the new row" });
      const seen = await glides(page);
      for (const key of ["moving mv2", "moving under:mv2", "moving mv3", "moving tail"]) assert(seen.includes(key), `${key} glides: ${seen}`);
      assert(!seen.some((g) => /^moving (mv0|mv1|mv2c\d)$/.test(g)), `the rows above stay, and the children travel with theirs: ${seen}`);
      assertEq(seen.filter((g) => g.startsWith("entering ") && !/ (mv|under:)/.test(g)).length, 1, `the new row fades in: ${seen}`);
      await arrived(rowOf(page, "mv3"));
      assertEq(await page.locator("[data-moving], [data-entering]").count(), 0, "every glide has landed");
      // Tab: the row glides into its new place; its new parent's body does not fade it
      await page.keyboard.press("Tab");
      await until(async () => (await page.locator('.blockChildren[data-motion-key="under:mv1"]').count()) === 1, { what: "the indent" });
      const indent = await glides(page);
      assertEq(indent.filter((g) => g.startsWith("moving ")).length, 1, `only the indented row moves: ${indent}`);
      assert(!indent.includes("entering under:mv1"), `the body it went into does not fade: ${indent}`);
      await page.keyboard.press("Escape");
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("motion: another client's block arrives without moving the rows; a fold the person makes closes before its rows go", async () => {
    const ctxA = await user.context(browser), ctxB = await user.context(browser);
    await watch(ctxA);
    const A = await openPage(ctxA, notes), B = await openPage(ctxB, notes);
    try {
      await rowOf(A, "mv3").waitFor();
      await rowOf(B, "mv3").waitFor();
      const rows = await A.locator(".sortableBlockWrap[data-block-id]").count();
      await glides(A);
      await addBelow(B, "mv0");
      await B.keyboard.type("from B");
      await until(async () => (await A.locator(".sortableBlockWrap[data-block-id]").count()) === rows + 1, { timeout: 15000, what: "B's row reaches A" });
      await A.waitForTimeout(400);
      assertEq((await glides(A)).length, 0, "nothing moves for another client's change");
      await B.keyboard.press("Escape");

      const fold = A.locator('.blockFold:has(> .collapseBody > .blockChildren[data-motion-key="under:mv2"])');
      await rowOf(A, "mv2").hover();
      await rowOf(A, "mv2").locator(".collapseBtn").click();
      assertEq(await fold.getAttribute("data-phase"), "closing", "the fold closes");
      assert(await fold.evaluate((el) => el.hasAttribute("inert")), "what it folds takes no press while it goes");
      await fold.waitFor({ state: "detached", timeout: 2000 });
      assertEq(await rowOf(A, "mv2c0").count(), 0, "then its rows are gone");
      await rowOf(A, "mv2").locator(".collapseBtn").click();
      await rowOf(A, "mv2c0").waitFor();
      await arrived(rowOf(A, "mv2c0"));
      await assertNoProblems(A);
    } finally { await ctxA.close(); await ctxB.close(); }
  });

  await step("motion: under reduced motion a block added mid-outline moves nothing", async () => {
    const ctx = await user.context(browser, { reducedMotion: "reduce" });
    await watch(ctx);
    const page = await openPage(ctx, notes);
    try {
      await rowOf(page, "mv3").waitFor();
      const rows = await page.locator(".sortableBlockWrap[data-block-id]").count();
      await addBelow(page, "mv0");
      await until(async () => (await page.locator(".sortableBlockWrap[data-block-id]").count()) === rows + 1, { what: "the new row" });
      await page.waitForTimeout(100);
      assertEq((await glides(page)).length, 0, "no glide, no fade");
      await page.keyboard.press("Escape");
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("motion: a segmented control's frame stands on the chosen option and slides to the next one chosen", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, home);
    try {
      const toggle = page.locator(".homeViewToggle").first();
      await toggle.waitFor();
      const frameOn = async () => toggle.evaluate((group) => {
        const pill = group.querySelector(":scope > .slidePill").getBoundingClientRect();
        const on = group.querySelector(':scope > [aria-pressed="true"]').getBoundingClientRect();
        return { dx: Math.abs(pill.x - on.x), dw: Math.abs(pill.width - on.width), dy: Math.abs(pill.y - on.y) };
      });
      const before = await frameOn();
      assert(before.dx < 1 && before.dw < 1 && before.dy < 1, `the frame stands on the chosen option: ${JSON.stringify(before)}`);
      await toggle.locator("button").nth(2).click();
      const sliding = await toggle.evaluate((group) => group.querySelector(":scope > .slidePill").getAnimations().length);
      assert(sliding > 0, "it slides");
      await arrived(toggle.locator(".slidePill"));
      const after = await frameOn();
      assert(after.dx < 1 && after.dw < 1 && after.dy < 1, `it lands on the one chosen: ${JSON.stringify(after)}`);
      await toggle.locator("button").first().click();
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // Three pages to open as tabs; a tab's motion key is its page's id.
  const tabTitles = ["Tab one", "Tab two", "Tab three"];
  const tabPages = [];
  for (const title of tabTitles) tabPages.push({ id: (await user.api("/api/pages", { method: "POST", body: { title } })).id, title });
  const liveTab = (page, title) => page.locator(live(".tabStrip .tab"), { hasText: title });
  async function openTab(page, title) {
    await page.locator('[data-guide="header.home"]').click();
    await page.locator(".fileRow", { hasText: title }).dblclick();
    await liveTab(page, title).waitFor();
  }
  const tabMarks = (seen) => seen.filter((g) => tabPages.some((p) => g.endsWith(` ${p.id}`)));

  await step("motion: a tab opened rises in and the topbar keeps its height; one closed sinks out through an inert copy as the next glides over; tabs read back come still", async () => {
    await user.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [] } });
    const ctx = await user.context(browser);
    await watch(ctx);
    const page = await openPage(ctx, home);
    try {
      await folderRow(page).waitFor();
      const topbar = () => page.locator(".topbar").evaluate((el) => el.getBoundingClientRect().height);
      const bare = await topbar();
      await openTab(page, "Tab one");
      assertEq(await topbar(), bare, "the topbar keeps its height as the first tab opens");
      assert((await glides(page)).includes(`entering ${tabPages[0].id}`), "the tab rises in");
      await openTab(page, "Tab two");
      await openTab(page, "Tab three");
      await arrived(liveTab(page, "Tab three"));
      await glides(page);
      // the middle one, in the background
      await liveTab(page, "Tab two").getByRole("button", { name: "Close Tab two", exact: true }).click();
      const copy = page.locator('.tabStrip .tab[data-leaving="tab"]', { hasText: "Tab two" });
      await copy.waitFor({ state: "attached" });
      assert(await copy.evaluate((el) => el.getAttribute("aria-hidden") === "true" && el.hasAttribute("inert")
        && !el.hasAttribute("role") && !el.hasAttribute("aria-selected")), "the copy is inert, and no tab");
      const seen = tabMarks(await glides(page));
      assert(seen.includes(`moving ${tabPages[2].id}`), `the tab after it glides over its place: ${seen}`);
      assert(!seen.includes(`moving ${tabPages[0].id}`), `the tab before it stays: ${seen}`);
      await copy.waitFor({ state: "detached", timeout: 2000 });
      await arrived(liveTab(page, "Tab three"));
      assertEq(await page.locator(".tabStrip .tab").count(), 2, "two tabs are left");
      await page.reload();
      await liveTab(page, "Tab three").waitFor();
      await page.waitForTimeout(300);
      const back = tabMarks(await glides(page));
      assertEq(back.length, 0, `tabs read back from the cache and the server move nothing: ${back}`);
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("motion: under reduced motion a tab closed goes at once and the others jump to their places", async () => {
    await user.api("/api/prefs/open-tabs", { method: "PUT", body: { value: tabPages } });
    const ctx = await user.context(browser, { reducedMotion: "reduce" });
    await watch(ctx);
    const page = await openPage(ctx, `${server.base}/?ws=${user.ws}&page=${tabPages[2].id}`);
    try {
      for (const title of tabTitles) await liveTab(page, title).waitFor();
      await glides(page);
      await liveTab(page, "Tab two").getByRole("button", { name: "Close Tab two", exact: true }).click();
      const left = await page.evaluate(() => new Promise((done) =>
        requestAnimationFrame(() => done(document.querySelectorAll("[data-leaving]").length))));
      assertEq(left, 0, "no copy");
      assertEq(await page.locator(".tabStrip .tab").count(), 2, "the tab is gone at once");
      assertEq(tabMarks(await glides(page)).length, 0, "nothing glides");
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // The library's listing: a row's motion key is its item's (a page's id,
  // folder:<id>).
  const listRow = (page, text) => page.locator(live(".fileList > .listMotion > *"), { hasText: text });
  async function deleteRow(page, text) {
    await listRow(page, text).click({ button: "right" });
    await page.locator(".ctxMenuItem", { hasText: "Delete" }).click();
    const confirm = page.locator(".confirmModal", { hasText: "Move this page to Recently deleted?" });
    await confirm.getByRole("button", { name: "Delete", exact: true }).click();
  }

  await step("motion: in the library a new folder fades in as the rows below glide down, a page deleted leaves through a copy as they close up, and another listing comes into focus without a glide", async () => {
    for (const title of ["Shelved page", "Doomed page"]) await user.api("/api/pages", { method: "POST", body: { title } });
    const ctx = await user.context(browser);
    await watch(ctx);
    const page = await openPage(ctx, home);
    try {
      await listRow(page, "Doomed page").waitFor();
      assertEq((await glides(page)).length, 0, "the listing a load brings moves nothing");
      await page.locator(".folderNewBtn", { hasText: "New folder" }).click();
      await page.locator(".folderNewInput").fill("Shelf");
      await page.keyboard.press("Enter");
      await listRow(page, "Shelf").waitFor();
      const added = await glides(page);
      assert(added.some((g) => g.startsWith("entering folder:")), `the folder fades in: ${added}`);
      assert(added.includes(`moving ${tabPages[0].id}`), `the rows below glide down: ${added}`);
      await arrived(listRow(page, "Tab one"));

      await deleteRow(page, "Doomed page");
      const copy = page.locator('.fileList [data-leaving="row"]', { hasText: "Doomed page" });
      await copy.waitFor({ state: "attached" });
      assert(await copy.evaluate((el) => el.getAttribute("aria-hidden") === "true" && el.hasAttribute("inert")
        && !el.hasAttribute("data-motion-key") && getComputedStyle(el.parentElement.closest(".fileList")).position === "relative"),
      "the copy is inert, keyless, and stands in the list");
      const gone = await glides(page);
      assert(gone.includes(`moving ${tabPages[0].id}`), `the rows below close up: ${gone}`);
      await copy.waitFor({ state: "detached", timeout: 2000 });
      await arrived(listRow(page, "Tab one"));

      // a typed filter and a sort are other listings: nothing glides
      await page.locator(".homeFindInput").fill("Tab");
      await page.locator(".homeFindInput").fill("");
      await page.waitForTimeout(100);
      assertEq((await glides(page)).length, 0, "a typed filter moves nothing");
      // another kind filter: the listing comes into focus where it stands
      await page.evaluate(() => {
        window.__focusIn = new Promise((done) => {
          document.addEventListener("animationstart", (e) => { if (e.target.classList?.contains("listingIn")) done(e.animationName); });
          setTimeout(() => done(null), 3000);
        });
      });
      await page.locator(".homeViewToggle").first().locator("button").nth(2).click();
      assertEq(await page.evaluate(() => window.__focusIn), "sharpen-in", "the listing comes into focus");
      assertEq((await glides(page)).length, 0, "and nothing in it glides");
      await page.locator(".homeViewToggle").first().locator("button").nth(0).click();
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("motion: under reduced motion a page deleted from the library goes at once and the rows jump up", async () => {
    await user.api("/api/pages", { method: "POST", body: { title: "Quietly doomed" } });
    const ctx = await user.context(browser, { reducedMotion: "reduce" });
    await watch(ctx);
    const page = await openPage(ctx, home);
    try {
      await listRow(page, "Quietly doomed").waitFor();
      await glides(page);
      await deleteRow(page, "Quietly doomed");
      await page.locator(".fileRow", { hasText: "Quietly doomed" }).waitFor({ state: "detached" });
      const left = await page.evaluate(() => new Promise((done) =>
        requestAnimationFrame(() => done(document.querySelectorAll(".fileList [data-leaving]").length))));
      assertEq(left, 0, "no copy");
      assertEq((await glides(page)).length, 0, "nothing glides");
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("motion: the rows Load more brings arrive at once", async () => {
    for (let i = 1; i <= 30; i++) await user.api("/api/pages", { method: "POST", body: { title: `Filler ${i}` } });
    const ctx = await user.context(browser);
    await watch(ctx);
    const page = await openPage(ctx, home);
    try {
      await page.locator(".loadMoreBtn").waitFor();
      const shown = await page.locator(".fileList .fileRow").count();
      await glides(page);
      await page.locator(".loadMoreBtn").click();
      await until(async () => (await page.locator(".fileList .fileRow").count()) > shown, { what: "more rows" });
      await page.waitForTimeout(100);
      assertEq((await glides(page)).length, 0, "no row fades in, none glides");
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("motion: the last tab closed in a strip scrolled to its end sinks out where it was seen while the tabs before it glide over its place", async () => {
    const strip = [...tabPages];
    for (const name of "abcdefghi") {
      const title = `Strip ${name}`;
      strip.push({ id: (await user.api("/api/pages", { method: "POST", body: { title } })).id, title });
    }
    await user.api("/api/prefs/open-tabs", { method: "PUT", body: { value: strip } });
    const last = strip[strip.length - 1], before = strip[strip.length - 2];
    const ctx = await user.context(browser);
    await watch(ctx);
    const page = await openPage(ctx, `${server.base}/?ws=${user.ws}&page=${last.id}`);
    try {
      await liveTab(page, last.title).waitFor();
      await liveTab(page, last.title).click(); // the person has acted (the active tab: nothing opens)
      const atEnd = await page.locator(".tabStrip").evaluate((el) => el.scrollWidth > el.clientWidth && el.scrollLeft + el.clientWidth >= el.scrollWidth - 1);
      assert(atEnd, "the strip overflows and stands at its end");
      const leftOf = (locator) => locator.evaluate((el) => el.getBoundingClientRect().left);
      const seenAt = await leftOf(liveTab(page, last.title));
      const beforeAt = await leftOf(liveTab(page, before.title));
      await glides(page);
      await liveTab(page, last.title).hover();
      await liveTab(page, last.title).getByRole("button", { name: `Close ${last.title}`, exact: true }).click();
      const copy = page.locator('.tabStrip .tab[data-leaving="tab"]', { hasText: last.title });
      await copy.waitFor({ state: "attached" });
      assertNear(await leftOf(copy), seenAt, 1, "the copy sinks out where the tab was seen");
      const seen = await glides(page);
      assert(seen.includes(`moving ${before.id}`), `the tab before it glides over its place: ${seen}`);
      await copy.waitFor({ state: "detached", timeout: 2000 });
      await arrived(liveTab(page, before.title));
      assert(await leftOf(liveTab(page, before.title)) > beforeAt + 1, "it came in from the left, as the strip scrolled back");
      await assertNoProblems(page);
    } finally { await ctx.close(); }
  });
}
