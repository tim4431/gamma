import { generateNKeysBetween } from "fractional-indexing";
import { Account, live, wanted } from "../harness.mjs";

// Ctrl+P: the quick-open page palette (library/QuickOpen.jsx).
export async function quickOpenScenarios(env) {
  const { server, browser, step, openPage, assert, assertEq, assertNoProblems, until, flags } = env;
  if (!wanted("quickopen")) return;
  await server.manage("create-user", "quickopen-user", "quickopen-pw");
  const user = await new Account(server, "quickopen-user", "quickopen-pw").login();
  const titles = ["Cavity readout", "Cavity sensors", "Quantum correction", "Atomic clocks"];
  const papers = {};
  const cavities = await user.folder("optics/cavities");
  const horlogerie = await user.label("horlogerie");
  for (const title of titles) {
    const properties = title === "Atomic clocks" ? { labels: [horlogerie] }
      : title === "Cavity readout" ? { folders: [cavities] } : undefined;
    papers[title] = await user.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: title, properties } });
  }

  await step("quickopen: Ctrl+P lists pages, filters by title and opens the pick", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${papers["Atomic clocks"].id}&ws=${user.ws}`);
    try {
      await page.locator(".blockList").first().waitFor();
      await page.keyboard.press("Control+p");
      const dialog = page.getByRole("dialog", { name: "Open a page" });
      await dialog.waitFor();
      const input = dialog.getByRole("textbox", { name: "Search pages by title or label" });
      await until(() => input.evaluate((el) => el === document.activeElement), { what: "the palette's input takes focus" });
      // every page, and the Welcome page and the Sketchbook a new account starts with
      await until(async () => (await dialog.getByRole("option").count()) === titles.length + 2, { what: "every page listed before typing" });
      assert(await dialog.getByRole("option", { name: /Atomic clocks/ }).locator(".quickOpenTag").textContent() === "Current",
        "the open page is tagged Current");

      // A label matches like a title, typos included ("horlogerie" is only a label).
      const pageRows = dialog.locator('[role="option"][data-kind="page"]');
      await input.fill("horlogeire");
      await until(async () => (await pageRows.count()) === 1, { what: "the label's page alone" });
      assert(/Atomic clocks/.test(await pageRows.textContent()), "the labelled page matches");
      assertEq(await pageRows.locator(".labelTagBadge").textContent(), "horlogerie", "the row shows its label");
      assert(/horlogerie/.test(await dialog.locator('[role="option"][data-kind="label"]').textContent()), "the label itself is listed");
      assertEq(await dialog.locator('[role="option"][data-action]').count(), 2, "the search and create actions end the list");

      await input.fill("cavity");
      await until(async () => (await pageRows.count()) === 2);
      assertEq(await dialog.locator(".quickOpenSection").first().textContent(), "Pages", "results sit under section headings");
      await page.keyboard.press("ArrowDown");
      const picked = await dialog.locator('[role="option"][aria-selected="true"] strong').textContent();
      await page.keyboard.press("Enter");
      await until(() => new URL(page.url()).searchParams.get("block") === papers[picked].id);
      assertEq(await page.getByRole("dialog", { name: "Open a page" }).count(), 0, "opening closes the palette");

      await page.keyboard.press("Control+p");
      await dialog.waitFor();
      await page.keyboard.press("Escape");
      assertEq(await page.getByRole("dialog", { name: "Open a page" }).count(), 0, "Escape closes the palette");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("quickopen: folders and labels open their view; Ctrl+Enter searches everywhere, Shift+Enter creates the page", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${papers["Atomic clocks"].id}&ws=${user.ws}`);
    try {
      await page.locator(".blockList").first().waitFor();
      const dialog = page.getByRole("dialog", { name: "Open a page" });
      const input = dialog.getByRole("textbox", { name: "Search pages by title or label" });
      // A folder is a row of its own; picking it opens the folder view.
      await page.keyboard.press("Control+p");
      await input.fill("cavities");
      const folderRow = dialog.locator('[role="option"][data-kind="folder"]');
      await until(async () => (await folderRow.count()) === 1, { what: "the folder matches" });
      assert(/in optics/.test(await folderRow.textContent()), "the folder row names its parent");
      await folderRow.click();
      await until(async () => new URL(page.url()).searchParams.get("folder") === cavities, { what: "the folder view opens" });
      await page.locator(".fileRow", { hasText: "Cavity readout" }).waitFor();
      // A label row opens the label's view, over the whole library.
      await page.keyboard.press("Control+p");
      await input.fill("horlogerie");
      const labelRow = dialog.locator('[role="option"][data-kind="label"]');
      await until(async () => (await labelRow.count()) === 1, { what: "the label matches" });
      await labelRow.click();
      await until(async () => new URL(page.url()).searchParams.get("label") === horlogerie, { what: "the label view opens" });
      assert(!new URL(page.url()).searchParams.get("folder"), "over the whole library");
      await page.locator(".fileRow", { hasText: "Atomic clocks" }).waitFor();
      await page.goto(`${server.base}/?folder=${cavities}&ws=${user.ws}`);
      await page.locator(".fileRow", { hasText: "Cavity readout" }).waitFor();

      // Ctrl+Enter hands the query to the workspace search.
      await page.keyboard.press("Control+p");
      await input.fill("quantum");
      await page.keyboard.press("Control+Enter");
      const search = page.locator(".searchPopover .searchInput");
      await search.waitFor();
      await until(async () => (await search.inputValue()) === "quantum", { what: "the search opens with the query" });
      await page.keyboard.press("Escape");
      await page.mouse.click(5, 300);

      // Shift+Enter creates a page with the query as its title, and opens it.
      await page.keyboard.press("Control+p");
      await input.fill("Fresh cavity idea");
      await page.keyboard.press("Shift+Enter");
      await until(async () => (await user.api("/api/blocks/root/children")).children.some((b) => b.content === "Fresh cavity idea"),
        { what: "the page exists" });
      const created = (await user.api("/api/blocks/root/children")).children.find((b) => b.content === "Fresh cavity idea");
      await until(() => new URL(page.url()).searchParams.get("block") === created.id, { what: "the new page opens" });
      assertEq(JSON.stringify(created.properties?.folders), JSON.stringify([cavities]), "it lands in the folder the palette was opened over");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("quickopen: Ctrl+Shift+P is the command palette; a pick runs", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${papers["Atomic clocks"].id}&ws=${user.ws}`);
    try {
      await page.locator(".blockList").first().waitFor();
      await page.keyboard.press("Control+Shift+p");
      const dialog = page.getByRole("dialog", { name: "Command palette" });
      await dialog.waitFor();
      const input = dialog.getByRole("textbox", { name: "Type a command" });
      assertEq(await input.inputValue(), ">", "the command prefix is typed");
      await until(async () => (await dialog.getByRole("option").count()) > 3, { what: "commands listed" });
      assert((await dialog.getByRole("option", { name: /Rename page/ }).textContent()).includes("F2"), "a command shows its keys");
      await input.fill(">rename");
      await until(async () => /Rename page/.test(await dialog.locator('[role="option"][aria-selected="true"]').textContent()), { what: "the match is selected" });
      await page.keyboard.press("Enter");
      await page.locator(".titleEdit").waitFor();
      assertEq(await page.getByRole("dialog", { name: "Command palette" }).count(), 0, "running closes the palette");
      await page.keyboard.press("Escape");
      // ">" typed into Ctrl+P is the same palette.
      await page.keyboard.press("Control+p");
      await page.getByRole("dialog", { name: "Open a page" }).waitFor();
      await page.keyboard.type(">");
      await page.getByRole("dialog", { name: "Command palette" }).waitFor();
      await page.keyboard.press("Escape");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("quickopen: a link to a page that isn't here says so, and Search the library opens the palette", async () => {
    const gone = await user.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Soon deleted" } });
    await user.api(`/api/blocks/${gone.id}`, { method: "DELETE" });
    await user.api(`/api/trash/${gone.id}`, { method: "DELETE" }); // gone for good (trash.mjs covers Recently deleted)
    const holder = await user.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Links to a gone page" } });
    await user.api("/api/blocks", { method: "POST", body: { parent_id: holder.id, content: `see [the old page](/?page=${gone.id})` } });
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${gone.id}&ws=${user.ws}`);
    try {
      // a dead deep link: the library, the notice above it, the id out of the address bar
      const notice = page.locator(".missingPageNotice");
      await notice.waitFor({ timeout: 15000 });
      assert((await notice.textContent()).includes("That page isn't here."), "the notice");
      await page.locator(".homeListBar").waitFor();
      assert(!new URL(page.url()).searchParams.has("page"), "the dead id leaves the address bar");
      await notice.getByRole("button", { name: "Search the library", exact: true }).click();
      await page.getByRole("dialog", { name: "Open a page" }).waitFor();
      assertEq(await notice.count(), 0, "the notice goes when the palette opens");
      await page.keyboard.press("Escape");
      // a dead link clicked on a page: the notice in place, the page stays open
      await page.goto(`${server.base}/?page=${holder.id}&ws=${user.ws}`);
      await page.locator(".gammaLinkCard", { hasText: "the old page" }).click();
      await notice.waitFor();
      assertEq(new URL(page.url()).searchParams.get("block"), holder.id, "still on the page");
      await notice.getByRole("button", { name: "Dismiss", exact: true }).click();
      await notice.waitFor({ state: "detached" });
      assertNoProblems(page, [/find-page\/[^ ]+ -> 404/, /\/subtree -> 404/]);
    } finally { await ctx.close(); }
  });

  await step("library: the page menu adds a label in place, a folder's makes a page in it; F2 renames and Delete asks first", async () => {
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=${user.ws}`);
    try {
      const row = page.locator(".fileRow", { hasText: "Quantum correction" });
      await row.waitFor();
      // Add label ▸ New label… takes a name typed in the flyout.
      await row.click({ button: "right" });
      await page.locator(".ctxMenuItem", { hasText: "Add label" }).click();
      await page.locator(".ctxMenuItem", { hasText: "New label…" }).click();
      await page.keyboard.type("urgent");
      await page.keyboard.press("Enter");
      await row.locator(".labelTagBadge", { hasText: "urgent" }).waitFor();
      // A label every picked page carries is checked; picking it takes it off.
      await row.click({ button: "right" });
      assertEq(await page.locator(".ctxMenuItem", { hasText: "Delete" }).locator(".ctxMenuKey").textContent(), "Del", "Delete shows its key");
      await page.locator(".ctxMenuItem", { hasText: "Add label" }).click();
      const urgent = page.locator(".ctxSubMenu .ctxMenuItem", { hasText: "urgent" });
      await urgent.locator(".ctxMenuCheck").waitFor();
      await urgent.click();
      await row.locator(".labelTagBadge", { hasText: "urgent" }).waitFor({ state: "detached" });

      // F2 renames the selected row in place; Delete asks before it deletes.
      await row.click();
      await page.keyboard.press("F2");
      await page.locator(".fileRowRename").waitFor();
      await page.keyboard.press("Escape");
      await row.click();
      await page.keyboard.press("Delete");
      const confirm = page.locator(".confirmModal", { hasText: "Move this page to Recently deleted?" });
      await confirm.waitFor();
      await confirm.getByRole("button", { name: "Cancel", exact: true }).click();
      await row.waitFor();

      // A folder's menu starts a page filed in it.
      await page.locator(".folderRow", { hasText: "optics" }).first().click({ button: "right" });
      await page.locator(".ctxMenuItem", { hasText: "New page here" }).click();
      await until(() => new URL(page.url()).searchParams.get("block"), { what: "the new page opens" });
      const id = new URL(page.url()).searchParams.get("block");
      const optics = await user.folder("optics");
      await until(async () => (await user.api("/api/blocks/root/children")).children.some((b) => b.id === id && b.properties?.folders?.includes(optics)),
        { what: "the page is filed in the folder" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("tabs: closing follows viewing order through page links and tab switches", async () => {
    const made = [];
    for (const title of ["Return first", "Return second", "Return third"]) {
      made.push(await user.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: title } }));
    }
    for (let i = 0; i < made.length - 1; i++) {
      await user.api("/api/blocks", { method: "POST", body: {
        parent_id: made[i].id, content: `[Next return page](/?page=${made[i + 1].id})`,
      } });
    }
    await user.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [] } });
    const ctx = await user.context(browser);
    const page = await openPage(ctx, `${server.base}/?page=${made[0].id}&ws=${user.ws}`);
    const tab = (i) => page.locator(".tabStrip .tab", { hasText: made[i].content });
    const onPage = async (i) => {
      await until(() => new URL(page.url()).searchParams.get("block") === made[i].id, { what: `${made[i].content} opens` });
      await until(() => tab(i).evaluate((el) => el.classList.contains("active")), { what: "the tab is active" });
    };
    try {
      await onPage(0);
      await page.locator(".gammaLinkCard", { hasText: "Next return page" }).click();
      await onPage(1);
      await page.locator(".gammaLinkCard", { hasText: "Next return page" }).click();
      await onPage(2);
      // The previously viewed tab is third even though first is leftmost.
      await tab(0).click();
      await onPage(0);
      // A middle-click closes nothing (a stray one used to lose the tab).
      await tab(0).click({ button: "middle" });
      assertEq(await tab(0).count(), 1, "a middle-click leaves the tab open");
      await tab(0).getByRole("button", { name: `Close ${made[0].content}`, exact: true }).click();
      await onPage(2);
      await tab(0).waitFor({ state: "detached" });
      // Context-menu close takes the same route; repeated closes skip first.
      await tab(2).click({ button: "right" });
      await page.locator(".ctxMenuItem", { hasText: "Close tab" }).click();
      await onPage(1);
      await tab(2).waitFor({ state: "detached" });
      await tab(1).getByRole("button", { name: `Close ${made[1].content}`, exact: true }).click();
      await page.locator(".homeListBar").waitFor();
      // (a closed tab's copy may still be sinking out)
      assertEq(await page.locator(live(".tabStrip .tab")).count(), 0, "closing the last tab returns home without reopening closed pages");

      // Closing a background tab keeps the active page and removes that
      // background page from subsequent return destinations.
      await page.locator(".fileRow", { hasText: made[0].content }).dblclick();
      await onPage(0);
      await page.locator(".gammaLinkCard", { hasText: "Next return page" }).click();
      await onPage(1);
      await page.locator(".gammaLinkCard", { hasText: "Next return page" }).click();
      await onPage(2);
      await tab(1).getByRole("button", { name: `Close ${made[1].content}`, exact: true }).click();
      await tab(1).waitFor({ state: "detached" });
      assertEq(new URL(page.url()).searchParams.get("block"), made[2].id, "background close leaves the current page open");
      await tab(2).getByRole("button", { name: `Close ${made[2].content}`, exact: true }).click();
      await onPage(0);
      assertEq(await tab(1).count(), 0, "the closed background tab stays closed");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("tabs: closing returns to the folder or label it opened from, even with another tab open", async () => {
    const filed = await user.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Return to library view" } });
    await user.file(filed.id, { folders: ["return/nested"], labels: ["return-label"] });
    const nested = await user.folder("return/nested");
    const returnLabel = await user.label("return-label");
    const other = papers["Atomic clocks"];
    for (const label of ["", returnLabel]) {
      await user.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [{ id: other.id, title: other.content }] } });
      const ctx = await user.context(browser);
      const page = await openPage(ctx, `${server.base}/?folder=${nested}&label=${label}&ws=${user.ws}`);
      try {
        const row = page.locator(".fileRow", { hasText: filed.content });
        await row.dblclick();
        const active = page.locator(".tabStrip .tab.active", { hasText: filed.content });
        await active.waitFor();
        await active.getByRole("button", { name: `Close ${filed.content}`, exact: true }).click();
        await row.waitFor();
        const params = new URL(page.url()).searchParams;
        assertEq(params.get("folder"), nested, "closing restores the nested folder");
        assertEq(params.get("label") || "", label, "closing restores the label filter");
        assertEq(await page.locator(live(".tabStrip .tab"), { hasText: other.content }).count(), 1, "the other tab stays open");
        assertEq(await page.locator(live(".tabStrip .tab.active")).count(), 0, "the source library view takes priority over other tabs");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    }
  });

  await step("tabs: the active tab stays in view, and ⌄ lists every open tab and opens the pick", async () => {
    const made = [];
    for (let i = 1; i <= 14; i++) made.push(await user.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: `Tab page ${i}` } }));
    const tabs = made.map((b, i) => ({ id: b.id, title: b.content, ...(i === 0 ? { pinned: true } : {}) }));
    await user.api("/api/prefs/open-tabs", { method: "PUT", body: { value: tabs } });
    const ctx = await user.context(browser);
    const target = made[made.length - 2];
    const page = await openPage(ctx, `${server.base}/?page=${target.id}&ws=${user.ws}`);
    try {
      await page.locator(".blockList").first().waitFor();
      const active = page.locator(".tabStrip .tab.active");
      await active.waitFor();
      await until(() => page.evaluate(() => {
        const strip = document.querySelector(".tabStrip").getBoundingClientRect();
        const tab = document.querySelector(".tabStrip .tab.active").getBoundingClientRect();
        return tab.left >= strip.left && tab.right <= strip.right;
      }), { what: "the active tab is scrolled into view" });
      assertEq(await page.locator(".tabStrip .tab.pinned .tabTitle").count(), 1, "a pinned tab keeps its title");
      assertEq(await page.locator(".tabStrip .tab.pinned").getByRole("button", { name: "Unpin tab" }).count(), 1, "a pin takes the close button's place");
      // The overflow button lists every tab; a filter and Enter open one.
      await page.getByRole("button", { name: "All open tabs", exact: true }).click();
      const menu = page.getByRole("dialog", { name: "All open tabs" });
      await menu.waitFor();
      assertEq(await menu.getByRole("option").count(), tabs.length, "every open tab is listed");
      await menu.getByRole("textbox", { name: "Find an open tab" }).fill("page 3");
      await until(async () => (await menu.getByRole("option").count()) === 1, { what: "the filter narrows the list" });
      await page.keyboard.press("Enter");
      await until(() => new URL(page.url()).searchParams.get("block") === made[2].id, { what: "the pick opens" });
      assertEq(await menu.count(), 0, "opening closes the list");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await user.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [] } });
    }
  });

  await step("tabs: a tab pressed is selected at once while the page it leaves stays whole until its own lands; a quick switch shows no pill, a slow one says Opening… and takes it down as the page lands, never Ready. Pressing the tab on screen, or closing the one pressed, takes the press back", async () => {
    const made = [];
    for (const title of ["Switch from", "Switch to", "Switch third"]) {
      const b = await user.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: title } });
      await user.api("/api/blocks", { method: "POST", body: { parent_id: b.id, content: `the body of ${title}` } });
      made.push(b);
    }
    await user.api("/api/prefs/open-tabs", { method: "PUT", body: { value: made.map((b) => ({ id: b.id, title: b.content })) } });
    const ctx = await user.context(browser);
    // Each page's tree answers after `delay` ms, or once `hold` resolves;
    // the page `late` names answers 800 ms late.
    const gate = { delay: 0, hold: null, late: "" };
    for (const b of made) {
      await ctx.route(`**/api/blocks/${b.id}/subtree*`, async (route) => {
        if (gate.hold) await gate.hold;
        else if (gate.late === b.id) await new Promise((resolve) => setTimeout(resolve, 800));
        else if (gate.delay) await new Promise((resolve) => setTimeout(resolve, gate.delay));
        await route.continue();
      });
    }
    const page = await openPage(ctx, `${server.base}/?page=${made[0].id}&ws=${user.ws}`);
    // Every painted frame from the press on: the tabs selected, the page's
    // title and first row, the pill.
    const watch = () => page.evaluate(() => {
      const seen = (window.__seen = []);
      let pressed = false;
      window.addEventListener("pointerdown", () => { pressed = true; }, { capture: true, once: true });
      const look = () => {
        if (window.__seen !== seen) return;
        if (pressed) {
          seen.push({
            tabs: [...document.querySelectorAll(".tabStrip .tab.active:not([data-leaving]) .tabTitle")].map((el) => el.textContent.trim()),
            title: document.querySelector(".pageTitleRow .titleText")?.textContent.trim() || "",
            row: document.querySelector(".blockList .blockRow")?.textContent.trim() || "",
            pill: document.querySelector(".statusPill .pillText")?.textContent.trim() || "",
          });
        }
        requestAnimationFrame(look);
      };
      requestAnimationFrame(look);
    });
    const frames = () => page.evaluate(() => { const seen = window.__seen; window.__seen = null; return seen; });
    const onPage = (title) => until(async () => (await page.locator(".pageTitleRow .titleText").innerText()) === title
      && (await page.locator(".blockList .blockRow").first().innerText()).includes(`the body of ${title}`), { what: `${title} open` });
    const tab = (title) => page.locator(live(".tabStrip .tab"), { hasText: title });
    try {
      await onPage("Switch from");
      await tab("Switch to").waitFor();
      // slow: the tree held well past the 300 ms after which an open says so
      let release;
      gate.hold = new Promise((resolve) => { release = resolve; });
      await watch();
      await tab("Switch to").click();
      // held until a painted frame has shown it: a pill found in the DOM may
      // not have been painted yet, and a busy machine can land the released
      // tree before the next frame
      await until(() => page.evaluate(() => window.__seen.some((f) => f.title === "Switch from" && f.pill.startsWith("Opening"))),
        { timeout: 3000, every: 50, what: "the slow open to say Opening… over the page it leaves" });
      gate.hold = null;
      release();
      await onPage("Switch to");
      await page.locator(".statusPill").waitFor({ state: "detached", timeout: 3000 });
      let seen = await frames();
      assert(seen.length > 2, `frames seen: ${seen.length}`);
      const said = (f) => `${f.tabs.join("+")} | ${f.title} | ${f.pill}`;
      assertEq(said(seen[0]), "Switch to | Switch from | ", "the press's own frame: the tab selected, the page it leaves still on screen");
      assert(seen.every((f) => f.tabs.length === 1), `one tab selected in every frame: ${seen.map(said).join(" / ")}`);
      assert(seen.every((f) => f.row.includes(`the body of ${f.title}`)),
        `every frame shows one page whole: ${seen.map((f) => `${f.title}: ${f.row}`).join(" / ")}`);
      assert(!seen.some((f) => /Ready/.test(f.pill)), `no Ready. for a switch: ${seen.map(said).join(" / ")}`);
      // quick: under the threshold, nothing but the switch itself
      gate.delay = 150;
      await watch();
      await tab("Switch from").click();
      await onPage("Switch from");
      await page.waitForTimeout(400);
      seen = await frames();
      assertEq(said(seen[0]), "Switch from | Switch to | ", "the press's own frame, again");
      assert(seen.every((f) => f.tabs.length === 1 && f.tabs[0] === "Switch from"), `the pressed tab stays selected: ${seen.map(said).join(" / ")}`);
      assert(seen.every((f) => !f.pill), `no pill for a quick switch: ${seen.map(said).join(" / ")}`);
      // two presses in quick succession: the later one's page stays, though
      // the earlier one's tree answers after it
      const selected = async () => (await page.locator(".tabStrip .tab.active:not([data-leaving]) .tabTitle").allInnerTexts()).join();
      gate.delay = 0;
      gate.late = made[1].id;
      await tab("Switch to").click();
      await tab("Switch third").click();
      await onPage("Switch third");
      await page.waitForTimeout(1200);
      assertEq(await page.locator(".pageTitleRow .titleText").innerText(), "Switch third", "the late answer does not take over");
      assertEq(await selected(), "Switch third");
      assertEq(await page.locator(".statusPill").count(), 0, "and leaves no pill behind");
      gate.late = "";
      // The tab of the page on screen, pressed while another's page opens,
      // takes that press back: the page on screen is not read again, and no
      // "Opening…" stands over it.
      const reads = [];
      page.on("request", (r) => { const m = /\/api\/blocks\/([^/]+)\/subtree/.exec(r.url()); if (m) reads.push(m[1]); });
      let release2;
      gate.hold = new Promise((resolve) => { release2 = resolve; });
      await tab("Switch to").click();
      await page.locator(".statusPill", { hasText: "Opening" }).waitFor({ timeout: 3000 });
      await tab("Switch third").click();
      assertEq(await selected(), "Switch third", "the page on screen's tab is selected again at once");
      await page.locator(".statusPill").waitFor({ state: "detached", timeout: 3000 });
      gate.hold = null;
      release2();
      await page.waitForTimeout(800);
      assertEq(await page.locator(".pageTitleRow .titleText").innerText(), "Switch third", "the page on screen stays");
      assertEq(await selected(), "Switch third");
      assertEq(reads.filter((id) => id === made[2].id).length, 0, `the page on screen is not read again: ${reads.join(", ")}`);
      // The tab pressed, closed before its page lands: the press is taken
      // back, the page on screen keeps its tab selected, and the closed tab
      // does not come back with its page.
      let release3;
      gate.hold = new Promise((resolve) => { release3 = resolve; });
      await tab("Switch to").click();
      await until(async () => (await selected()) === "Switch to", { what: "the pressed tab selected" });
      await tab("Switch to").getByRole("button", { name: "Close Switch to", exact: true }).click();
      await until(async () => (await selected()) === "Switch third", { what: "the page on screen's tab selected again" });
      gate.hold = null;
      release3();
      await page.waitForTimeout(800);
      assertEq(await page.locator(".pageTitleRow .titleText").innerText(), "Switch third", "the page on screen stays");
      assertEq((await page.locator(".tabStrip .tab:not([data-leaving]) .tabTitle").allInnerTexts()).join(), "Switch from,Switch third",
        "the closed tab stays closed");
      assertEq(await selected(), "Switch third");
      assertEq(await page.locator(".statusPill").count(), 0, "and no pill is left");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await user.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [] } });
    }
  });
  // A jump to a block of another page, or the home menu's Share…, belongs
  // to the open it was asked of: a tab pressed before that page lands
  // drops both, and no page after it lands on the block or opens Share.
  await step("tabs: a [[ref]] jump or a Share… whose page a tab press overtook is dropped with it: the pages after land at their own place, and that page opens later as any other", async () => {
    const made = {};
    for (const name of ["Jump from", "Jump target", "Jump over"]) {
      const pg = await user.api("/api/pages", { method: "POST", body: { title: name } });
      const tag = name.split(" ")[1].toLowerCase();
      const ops = generateNKeysBetween(null, null, 60).map((position, i) => ({ op: "insert", id: `jt${tag}${i}`, parent: pg.id, position,
        content: tag === "from" && i === 30 ? "jump to [[jttarget40]] here" : `${name} row ${i} with words enough to fill a line` }));
      await user.api(`/api/pages/${pg.id}/ops`, { method: "POST", body: { client: "seed", ops } });
      made[tag] = { id: pg.id, title: name };
    }
    await user.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [made.from, made.over] } });
    const ctx = await user.context(browser);
    const gate = { hold: null };
    await ctx.route(`**/api/blocks/${made.target.id}/subtree*`, async (route) => {
      if (gate.hold) await gate.hold;
      await route.continue();
    });
    const page = await openPage(ctx, `${server.base}/?page=${made.from.id}&ws=${user.ws}`);
    const tab = (title) => page.locator(live(".tabStrip .tab"), { hasText: title });
    const title = () => page.locator(".pageTitleRow .titleText").innerText();
    // The notes' scroll and the first row in view.
    const notesAt = () => page.evaluate(() => {
      const list = document.querySelector(".sidebar .blockList");
      const top = list.getBoundingClientRect().top + 4;
      const first = [...list.querySelectorAll(".blockRowWrap[data-block-id]")].find((r) => r.getBoundingClientRect().bottom > top);
      return `${Math.round(list.scrollTop)} ${first?.dataset.blockId}`;
    });
    try {
      await page.locator(".blockList .blockRow", { hasText: "Jump from row 59 with" }).waitFor();
      const chip = page.locator(".blockRefChip").first();
      // once it names its block (the chip renders again as its title lands)
      await until(async () => /Jump target row 40/.test(await chip.innerText().catch(() => "")), { what: "the chip names its block" });
      await page.waitForTimeout(300);
      await chip.scrollIntoViewIfNeeded();
      await until(async () => (await notesAt()).split(" ")[0] !== "0", { what: "the notes scrolled to the chip" });
      let release;
      gate.hold = new Promise((resolve) => { release = resolve; });
      await chip.click();
      await tab("Jump over").click();
      await until(async () => (await title()) === "Jump over", { what: "the tab pressed after the jump opens" });
      gate.hold = null;
      release();
      await page.waitForTimeout(800);
      assertEq(await title(), "Jump over", "the jump's page does not take over");
      assertEq(await notesAt(), "0 jtover0", "the page after lands at its top, not at the scroll the jump left");
      // The home menu's Share… for the target page, overtaken the same way.
      await page.locator('[data-guide="header.home"]').click();
      const row = page.locator(".fileRow, .pageCard", { hasText: "Jump target" }).first();
      await row.waitFor();
      gate.hold = new Promise((resolve) => { release = resolve; });
      await row.click({ button: "right" });
      await page.locator(".ctxMenuItem", { hasText: "Share…" }).click();
      await tab("Jump over").click();
      await until(async () => (await title().catch(() => "")) === "Jump over", { what: "the tab pressed after Share… opens" });
      gate.hold = null;
      release();
      await page.waitForTimeout(800);
      assertEq(await page.locator(".sharePopover").count(), 0, "no Share popover over the page after");
      // That page opened later, from a link to its block: it lands on it,
      // and only on it, with no Share popover.
      await page.keyboard.press("Control+p");
      const dialog = page.getByRole("dialog", { name: "Open a page" });
      await dialog.getByRole("textbox", { name: "Search pages by title or label" }).fill("Jump target");
      await dialog.getByRole("option", { name: /Jump target/ }).first().click();
      await until(async () => (await title()) === "Jump target", { what: "the target page open" });
      await page.waitForTimeout(800);
      assertEq(await page.locator(".sharePopover").count(), 0, "the dropped Share… does not open with its page later");
      assertEq((await notesAt()).split(" ")[0], "0", "nor does the dropped jump land it on the block");
      // The jump with nothing overtaking it lands on its block, focused.
      await tab("Jump from").click();
      await until(async () => (await title()) === "Jump from", { what: "back on the page with the chip" });
      await page.locator(".blockRefChip").first().click();
      await until(async () => (await title()) === "Jump target", { what: "the jump's page open" });
      const target = page.locator('.blockRowWrap[data-block-id="jttarget40"] .blockRow');
      await until(async () => /focused/.test(await target.getAttribute("class")) && await target.isVisible()
        && await target.evaluate((el) => {
          const r = el.getBoundingClientRect(), list = el.closest(".blockList").getBoundingClientRect();
          return r.top >= list.top && r.bottom <= list.bottom;
        }), { what: "the jump lands on its block, focused and in view" });
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await user.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [] } });
    }
  });
}
